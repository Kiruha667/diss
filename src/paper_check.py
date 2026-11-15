"""Check of the paper (paper/, LaTeX) against the numbers index of the kit and the bibliography.

  python -m src.paper_check           -> paper/CHECK.md; stale index line numbers in \\numsrc are rewritten in place
  python -m src.paper_check --no-fix  -> report only; a stale line number is then an error

What is checked, in paper/sections/*.tex:
1. \\numsrc{key@Lline}{value}: `key` must be a key of the current paper_kit/12_numbers_index.md and `line` the line of
   its row there; a stale or missing line number is reported and (without --no-fix) replaced by the current one.
   The printed `value` is compared with the index value: after LaTeX normalisation ($ removed, \\, and ~ -> space,
   \\% -> %, -- -> en dash, minus signs unified) either the text is the same ("exact"), or the sequence of numbers is
   the same — same numbers, same order, same signs — and only words differ ("numbers"; the index is in Russian, the
   paper in English). Anything else is an error.
2. numbers in the printed text outside \\numsrc: comments, \\numsrc, the arguments of \\cite, \\ref, \\label, \\url,
   \\includegraphics, \\begin/\\end (and tabular column specs), optional arguments, section numbers, years, arXiv ids,
   dates and identifiers with digits (E15, T1, C3, Qwen3-32B, Opus 4.5) are masked; every number left is an error.
3. every \\cite key is in paper/refs.bib; keys that paper_kit/02_related_work.md assigns to the dissertation only are
   warnings.
4. paper/refs.bib and paper/figures/ are copies of paper_kit/references.bib and paper_kit/figures/.
Open [TODO: ...] markers in the printed text are listed (not an error).
"""
from __future__ import annotations

import filecmp
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from src.common import ROOT
from src.paper_kit.build import MASK_WORDS, NUM

NUMSRC = "\\numsrc"
# commands whose arguments are not printed prose: name -> number of brace arguments to drop
DROP = {"cite": 1, "citep": 1, "citet": 1, "nocite": 1, "ref": 1, "eqref": 1, "autoref": 1, "cref": 1, "Cref": 1,
        "label": 1, "url": 1, "href": 1, "includegraphics": 1, "input": 1, "include": 1, "bibliography": 1,
        "bibliographystyle": 1, "setcounter": 2, "addtocounter": 2, "setlength": 2, "vspace": 1, "hspace": 1,
        "begin": 1, "end": 1, "numsrc": 2, "documentclass": 1, "usepackage": 1}
TABULAR = {"tabular", "tabular*", "tabularx", "array"}
CITE = re.compile(r"\\(?:no)?cite[a-zA-Z]*\*?\s*(?:\[[^\]]*\]\s*)*\{([^}]*)\}")
BIBKEY = re.compile(r"^@\w+\s*\{\s*([^,\s]+)\s*,", flags=re.M)
TODO = re.compile(r"\[TODO:[^\]]*\]|\\TODO\{")


@dataclass
class Use:
    file: str
    line: int
    start: int          # offset of "\numsrc" in the file
    end: int            # offset after the second argument
    arg: str            # first argument as written
    value: str          # second argument as written
    key: str = ""
    cited_line: int | None = None


@dataclass
class Report:
    uses: list[Use] = field(default_factory=list)
    stale: list[tuple[Use, int]] = field(default_factory=list)       # (use, current index line)
    unknown: list[Use] = field(default_factory=list)
    compared: list[tuple[Use, str, str]] = field(default_factory=list)  # (use, index value, "exact" | "numbers")
    mismatch: list[tuple[Use, str]] = field(default_factory=list)
    bare: list[tuple[str, int, str, str]] = field(default_factory=list)  # (file, line, number, context)
    cites: dict[str, list[tuple[str, int]]] = field(default_factory=dict)
    missing_cites: list[tuple[str, str, int]] = field(default_factory=list)
    diss_only: list[str] = field(default_factory=list)
    copies: list[str] = field(default_factory=list)
    todos: list[tuple[str, int, str]] = field(default_factory=list)
    fixed: bool = False

    def errors(self, fix: bool) -> int:
        return (len(self.unknown) + len(self.mismatch) + len(self.bare) + len(self.missing_cites) + len(self.copies)
                + (0 if fix else len(self.stale)))


# ---------------------------------------------------------------- the numbers index
def read_index(path: Path) -> dict[str, tuple[int, str]]:
    """key -> (line of its row in the index, value as printed in the index)."""
    out = {}
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        m = re.match(r"^\| `([^`]+)` \| ", line)
        if m:
            cells = line.strip().strip("|").split(" | ")
            out[m.group(1)] = (i, cells[1].strip())
    return out


# ---------------------------------------------------------------- LaTeX helpers
def brace_end(s: str, i: int) -> int:
    """s[i] == '{' -> index after the matching '}' (escaped braces skipped)."""
    assert s[i] == "{", (s[i:i + 20])
    depth, j = 0, i
    while j < len(s):
        c = s[j]
        if c == "\\":
            j += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    raise ValueError(f"unbalanced braces at offset {i}")


def skip_ws_opt(s: str, j: int) -> int:
    """Skip whitespace and [optional] arguments."""
    while j < len(s):
        if s[j] in " \t\n":
            j += 1
        elif s[j] == "[":
            k = s.find("]", j)
            j = len(s) if k < 0 else k + 1
        else:
            break
    return j


def find_numsrc(text: str, file: str) -> list[Use]:
    out, i = [], 0
    while (i := text.find(NUMSRC + "{", i)) >= 0:
        if comment_start(text, i) is not None:
            i += 1
            continue
        a0 = i + len(NUMSRC)
        a1 = brace_end(text, a0)
        b0 = skip_ws_opt(text, a1)
        b1 = brace_end(text, b0)
        out.append(Use(file, text.count("\n", 0, i) + 1, i, b1, text[a0 + 1:a1 - 1], text[b0 + 1:b1 - 1]))
        i = b1
    return out


def comment_start(text: str, i: int) -> int | None:
    """Offset of the % that comments out position i, if any."""
    ls = text.rfind("\n", 0, i) + 1
    m = re.search(r"(?<!\\)%", text[ls:i])
    return ls + m.start() if m else None


def tex_plain(v: str) -> str:
    """LaTeX value -> plain text in the conventions of the index."""
    s = v.replace("$", "")
    s = s.replace("\\,", " ").replace("\\;", " ").replace("\\ ", " ").replace("~", " ").replace("\\thinspace", " ")
    s = s.replace("\\%", "%").replace("---", "—").replace("--", "–")
    for a, b in (("\\pm", "±"), ("\\times", "×"), ("\\approx", "≈"), ("\\le", "≤"), ("\\ge", "≥"),
                 ("\\textminus", "−"), ("\\textendash", "–")):
        s = s.replace(a, b)
    s = re.sub(r"\\[a-zA-Z]+\*?", "", s).replace("{", "").replace("}", "")
    return re.sub(r"\s+", " ", s).strip()


def _cmp_norm(s: str) -> str:
    s = s.replace("−", "-").replace("\u2009", " ").replace("\u202f", " ").replace("\xa0", " ")
    return re.sub(r"\s*([=;:,])\s*", r"\1 ", re.sub(r"\s+", " ", s)).strip()


def numbers_of(s: str) -> list[str]:
    """Numbers of a string in order, with signs; thousands spaces removed."""
    s = s.replace("−", "-").replace("\u2009", " ").replace("\u202f", " ").replace("\xa0", " ")
    return [m.group(0).replace(" ", "") for m in NUM.finditer(s)]


def compare(paper_value: str, index_value: str) -> str | None:
    """'exact' | 'numbers' | None (mismatch)."""
    p = tex_plain(paper_value)
    if _cmp_norm(p) == _cmp_norm(index_value):
        return "exact"
    pn, xn = numbers_of(p), numbers_of(index_value)
    return "numbers" if pn and pn == xn else None


# ---------------------------------------------------------------- printed text: numbers outside \numsrc
def blank(chars: list[str], i: int, j: int) -> None:
    for k in range(i, j):
        if chars[k] != "\n":
            chars[k] = " "


def printed_text(text: str) -> str:
    """The text with everything that is not printed prose blanked (same length, same lines)."""
    chars = list(text)
    for m in re.finditer(r"(?<!\\)%[^\n]*", text):
        blank(chars, m.start(), m.end())
    t = "".join(chars)
    for m in re.finditer(r"\\([a-zA-Z]+)(\*?)", t):
        name = m.group(1)
        if name not in DROP or chars[m.start()] == " ":
            continue
        j, env = m.end(), None
        for n in range(DROP[name]):
            j = skip_ws_opt(t, j)
            if j < len(t) and t[j] == "{":
                k = brace_end(t, j)
                env = env or t[j + 1:k - 1]
                j = k
        if name == "begin" and env in TABULAR:
            j = skip_ws_opt(t, j)
            if j < len(t) and t[j] == "{":
                j = brace_end(t, j)
        blank(chars, m.start(), j)
    # optional arguments of the remaining commands: \\[2pt], \item[...], \caption[...]
    t = "".join(chars)
    for m in re.finditer(r"\\[a-zA-Z]+\*?\s*(\[[^\]\n]*\])", t):
        blank(chars, m.start(1), m.end(1))
    t = "".join(chars)
    return t.replace("\\,", "  ").replace("~", " ")


def mask_line(s: str) -> str:
    s = re.sub(r"\b\d{4}\.\d{4,5}(v\d+)?\b", " ", s)  # arXiv ids
    s = re.sub(r"\b\d{1,2}\.\d{1,2}\.\d{4}\b|\b\d{4}-\d{2}-\d{2}\b|\b(?:19|20)\d{2}\b", " ", s)  # dates and years
    for w in MASK_WORDS:
        s = s.replace(w, " ")
    s = re.sub(r"[A-Za-z_]+\w*\d[\w.\-]*", " ", s)  # identifiers with digits: E15, T1, C3, Qwen3-32B
    s = re.sub(r"\b(?:Sections?|Sec\.|Fig\.|Figs\.|Figure|Figures|Table|Tables|Eq\.|Appendix)\s*\d+", " ", s)
    s = re.sub(r"\\S\s*\d+(?:\.\d+)*", " ", s)  # \S 3
    s = re.sub(r"@\d+%?", " ", s)
    return s


def bare_numbers(text: str, file: str) -> list[tuple[str, int, str, str]]:
    out = []
    src_lines = text.splitlines()
    for i, line in enumerate(printed_text(text).splitlines(), 1):
        for m in NUM.finditer(mask_line(line).replace("−", "-")):
            out.append((file, i, m.group(0).strip(), src_lines[i - 1].strip()[:120]))
    return out


# ---------------------------------------------------------------- the check
def paper_list(related: Path) -> tuple[set[str], set[str]]:
    """Keys marked for the paper and for the dissertation only in paper_kit/02_related_work.md, section 3."""
    if not related.exists():
        return set(), set()
    t = related.read_text(encoding="utf-8")
    a, b = t.find("**В статье:**"), t.find("**Только диссертация:**")
    if a < 0 or b < 0:
        return set(), set()
    end = t.find("\n\n", b)
    return set(re.findall(r"`([^`]+)`", t[a:b])), set(re.findall(r"`([^`]+)`", t[b:end if end > 0 else None]))


def check(root: Path = ROOT, fix: bool = True) -> Report:
    paper, kit = root / "paper", root / "paper_kit"
    index = read_index(kit / "12_numbers_index.md")
    bib = paper / "refs.bib"
    bib_keys = set(BIBKEY.findall(bib.read_text(encoding="utf-8"))) if bib.exists() else set()
    _, diss_only = paper_list(kit / "02_related_work.md")
    rep = Report()
    for p in sorted((paper / "sections").glob("*.tex")):
        name = p.relative_to(root).as_posix()
        raw = p.read_bytes().decode("utf-8")
        text = raw.replace("\r\n", "\n")
        uses = find_numsrc(text, name)
        edits = []
        for u in uses:
            m = re.match(r"^\s*([^@\s]+)(?:@L(\d+))?\s*$", u.arg)
            u.key = m.group(1) if m else u.arg.strip()
            u.cited_line = int(m.group(2)) if m and m.group(2) else None
            rep.uses.append(u)
            if u.key not in index:
                rep.unknown.append(u)
                continue
            line, ivalue = index[u.key]
            if u.cited_line != line:
                rep.stale.append((u, line))
                a0 = u.start + len(NUMSRC)
                edits.append((a0, brace_end(text, a0), "{" + f"{u.key}@L{line}" + "}"))
            how = compare(u.value, ivalue)
            if how:
                rep.compared.append((u, ivalue, how))
            else:
                rep.mismatch.append((u, ivalue))
        if fix and edits:
            for a, b, new in sorted(edits, reverse=True):
                text = text[:a] + new + text[b:]
            p.write_bytes((text.replace("\n", "\r\n") if "\r\n" in raw else text).encode("utf-8"))
            rep.fixed = True
        rep.bare += bare_numbers(text, name)
        printed = printed_text(text)
        for m in TODO.finditer(printed):
            j = brace_end(printed, m.end() - 1) if m.group(0).endswith("{") else m.end()
            body = printed[m.start():j] if not m.group(0).endswith("{") else f"[TODO: {printed[m.end():j - 1]}]"
            rep.todos.append((name, printed.count("\n", 0, m.start()) + 1, re.sub(r"\s+", " ", body)[:120]))
        for m in CITE.finditer(text):
            if comment_start(text, m.start()) is not None:
                continue
            ln = text.count("\n", 0, m.start()) + 1
            for k in (x.strip() for x in m.group(1).split(",")):
                if k:
                    rep.cites.setdefault(k, []).append((name, ln))
                    if k not in bib_keys:
                        rep.missing_cites.append((k, name, ln))
    rep.diss_only = sorted(k for k in rep.cites if k in diss_only)
    src_bib = kit / "references.bib"
    if not bib.exists() or src_bib.read_bytes().replace(b"\r\n", b"\n") != bib.read_bytes().replace(b"\r\n", b"\n"):
        rep.copies.append("paper/refs.bib is not a copy of paper_kit/references.bib")
    for f in sorted((kit / "figures").glob("*")):
        g = paper / "figures" / f.name
        if not g.exists():
            rep.copies.append(f"paper/figures/{f.name} is missing")
        elif not filecmp.cmp(f, g, shallow=False):
            rep.copies.append(f"paper/figures/{f.name} differs from paper_kit/figures/{f.name}")
    return rep


def write_report(rep: Report, fix: bool, path: Path) -> None:
    ok = rep.errors(fix) == 0
    n_exact = sum(1 for _, _, h in rep.compared if h == "exact")
    out = ["# Проверка статьи", "",
           "Сгенерировано `python -m src.paper_check`" + ("" if fix else " --no-fix") + ". Индекс —"
           " `paper_kit/12_numbers_index.md`; текст — `paper/sections/*.tex`.", "",
           f"**Итог: {'всё сходится' if ok else 'есть расхождения'}.**", "",
           f"- `\\numsrc`: {len(rep.uses)}; ключ есть в индексе: {len(rep.uses) - len(rep.unknown)}; значение совпало "
           f"с записью: {len(rep.compared)} (текст точно — {n_exact}, числа точно, слова переведены — "
           f"{len(rep.compared) - n_exact}); не совпало: {len(rep.mismatch)}; неизвестных ключей: {len(rep.unknown)}.",
           f"- Устаревших номеров строк индекса: {len(rep.stale)}"
           + ("; заменены на актуальные." if rep.stale and fix else "." if fix else " (не заменены: --no-fix)."),
           f"- Чисел в тексте без `\\numsrc`: {len(rep.bare)}.",
           f"- Ключей `\\cite`: {len(rep.cites)}; нет в `paper/refs.bib`: {len(rep.missing_cites)}; по "
           f"`02_related_work.md` — только для диссертации: {len(rep.diss_only)}"
           + (f" ({', '.join(rep.diss_only)})" if rep.diss_only else "") + ".",
           f"- Копии `refs.bib` и `figures/`: {'совпадают с paper_kit/' if not rep.copies else 'расходятся'}.",
           f"- Открытых пометок [TODO]: {len(rep.todos)}.", "",
           "Как сравнивается значение: LaTeX приводится к тексту (`$` убираются, `\\,` и `~` — пробел, `\\%` — %, "
           "`--` — «–», минусы одинаковы). «Текст точно» — строки совпали; «числа точно» — совпала "
           "последовательность чисел (те же числа, в том же порядке, с теми же знаками), а слова различаются: индекс "
           "на русском, статья на английском. Числа вне `\\numsrc` ищутся в печатном тексте; не считаются номера "
           "разделов, рисунков и таблиц, годы, даты, идентификаторы arXiv и имена с цифрами (E15, T1, C3, Qwen3-32B, "
           "Opus 4.5).", ""]
    if rep.stale:
        out += ["## Номера строк индекса" + (" (заменены)" if fix else " (устарели)"), "",
                "| файл:строка | ключ | было | стало |", "|---|---|---|---|"]
        out += [f"| `{u.file}:{u.line}` | `{u.key}` | {('L' + str(u.cited_line)) if u.cited_line else '—'} | L{n} |"
                for u, n in rep.stale] + [""]
    sections = (("Неизвестные ключи", [f"`{u.file}:{u.line}`: `{u.arg}` — «{u.value}»" for u in rep.unknown]),
                ("Значение не совпадает с записью индекса",
                 [f"`{u.file}:{u.line}`: `{u.key}` — в статье «{u.value}», в индексе «{v}»" for u, v in rep.mismatch]),
                ("Числа без \\numsrc", [f"`{f}:{ln}`: «{t}» — {c}" for f, ln, t, c in rep.bare]),
                ("Ключи \\cite, которых нет в refs.bib", [f"`{f}:{ln}`: `{k}`" for k, f, ln in rep.missing_cites]),
                ("Ключи \\cite только для диссертации (предупреждение)",
                 [f"`{k}`: " + ", ".join(f"`{f}:{ln}`" for f, ln in rep.cites[k]) for k in rep.diss_only]),
                ("Копии", rep.copies),
                ("Открытые пометки [TODO]", [f"`{f}:{ln}`: {c}" for f, ln, c in rep.todos]))
    for title, items in sections:
        if items:
            out += [f"## {title}", ""] + [f"- {x}" for x in items] + [""]
    out += ["## Все `\\numsrc`", "", "| файл:строка | ключ | в статье | в индексе | совпадение |", "|---|---|---|---|---|"]
    how = {id(u): (v, h) for u, v, h in rep.compared} | {id(u): (v, "НЕТ") for u, v in rep.mismatch}
    for u in rep.uses:
        v, h = how.get(id(u), ("—", "ключа нет"))
        out.append(f"| `{u.file}:{u.line}` | `{u.key}` | {u.value.replace('|', '/')} | {v} | "
                   f"{dict(exact='текст точно', numbers='числа точно').get(h, h)} |")
    path.write_text("\n".join(out) + "\n", encoding="utf-8", newline="\n")


def main(argv: list[str]) -> int:
    fix = "--no-fix" not in argv
    rep = check(ROOT, fix)
    write_report(rep, fix, ROOT / "paper" / "CHECK.md")
    print(f"numsrc {len(rep.uses)}: matched {len(rep.compared)}, mismatched {len(rep.mismatch)}, unknown "
          f"{len(rep.unknown)}; stale lines {len(rep.stale)}{' (fixed)' if fix and rep.stale else ''}; bare numbers "
          f"{len(rep.bare)}; missing cites {len(rep.missing_cites)}; copies {len(rep.copies)}; TODO {len(rep.todos)}")
    return 0 if rep.errors(fix) == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
