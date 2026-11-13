"""Paper kit (paper_kit/): build from templates and the numbers spec, then check every number against its source.

  python -m src.paper_kit.build build  -> paper_kit/*.md from src/paper_kit/templates/*.md, paper_kit/12_numbers_index.md
  python -m src.paper_kit.build check  -> paper_kit/CHECK.md (exit code 1 if anything fails)

Templates: `{{key}}` -> "VALUE [источник: FILE, стр. N; POPULATION]"; `{{key|v}}` -> "VALUE"; `{{src:key}}` ->
"[источник: FILE, стр. N; POPULATION]" (no population part for settings, thresholds and counts of features).
Keys and values live in src/paper_kit/numbers.py: every key has a value string, a meaning, a source file (reports/,
outputs/ or review/), an anchor — a substring that occurs on exactly one line of the source — and a population (a code
of POPULATIONS, or "na" where none applies); the build stops if the anchor is not unique, the value is not on that line
or the population is missing. 12_numbers_index.md lists the keys used in the kit.

The check (CHECK.md):
1. every index entry: the value is on the cited source line;
2. every number in the kit text (WRITER_PROMPT.md, CHECK.md, the index and references.bib aside; identifiers such as
   E15, T1, Qwen3-32B, precision@10%, arXiv ids, dates, file paths and the source tags themselves masked) is one of the
   index values on the same line and is found on a source line cited on that line;
3. every kit line with a number cites a source.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from src.common import ROOT
from src.paper_kit.numbers import NUMBERS, POPULATIONS

KIT = ROOT / "paper_kit"
TEMPLATES = Path(__file__).parent / "templates"
SKIP_CHECK = {"WRITER_PROMPT.md", "CHECK.md", "12_numbers_index.md", "CHANGES.md"}  # CHANGES: log, "was -> is"
PLACEHOLDER = re.compile(r"\{\{(src:)?([A-Za-z0-9_.+\-]+)(\|v)?\}\}")
TAG = re.compile(r"\[источник: ([^\],;]+), стр\. (\d+)(?:; [^\]]*)?\]")
# names with digits that are not numbers of the work (models, metrics, versions, stages)
MASK_WORDS = ["Claude 4.5 Opus", "Opus 4.5", "Opus 4.6", "Claude Opus 4.5", "Claude Opus 4.6", "GPT-5.2", "GPT-5",
              "Llama", "IEEE", "all-MiniLM-L6-v2", "SWE-bench Verified", "SWE-bench Pro", "τ²-bench", "τ²", "v1.x",
              "v2.0.0", "v1", "v2", "Итерация 1", "итерация 1", "итерации 1"]
NUM = re.compile(r"(?<![\w.@/\-])[−+-]?\d+(?:[  ]\d{3})*(?:[.,]\d+)?%?(?![\w])")


def norm(s: str) -> str:
    return s.replace("−", "-").replace(" ", " ").replace(" ", " ").replace(" ", " ")


def num_tokens(s: str) -> list[str]:
    s = norm(s)
    return [m.group(0).lstrip("+") for m in NUM.finditer(s)]


def line_numbers(s: str) -> set[str]:
    """Numeric tokens of a source line, in a normalised form (thousands spaces removed, leading + dropped)."""
    out = set()
    for t in num_tokens(s):
        out.add(t.replace(" ", ""))
        out.add(t.replace(" ", "").rstrip("%"))
    return out


def resolve() -> dict[str, dict]:
    res, errors = {}, []
    cache: dict[str, list[str]] = {}
    for key, n in NUMBERS.items():
        path = ROOT / n["file"]
        if n["file"] not in cache:
            cache[n["file"]] = path.read_text(encoding="utf-8").splitlines()
        lines = cache[n["file"]]
        anchor, nth = n["anchor"], None
        m = re.match(r"^(.*)@(\d+)$", anchor, flags=re.S)
        if m:  # explicit occurrence: the main text and an appendix repeat the same line
            anchor, nth = m.group(1), int(m.group(2))
        hits = [i for i, x in enumerate(lines) if anchor in x]
        if nth is not None and len(hits) >= nth:
            hits = [hits[nth - 1]]
        if len(hits) != 1:
            errors.append(f"{key}: anchor {n['anchor']!r} found on {len(hits)} lines of {n['file']}")
            continue
        i = hits[0]
        if norm(n["value"]) not in norm(lines[i]):
            errors.append(f"{key}: value {n['value']!r} not on {n['file']}:{i + 1}")
        if n.get("pop") not in POPULATIONS:
            errors.append(f"{key}: population {n.get('pop')!r} is not one of POPULATIONS")
        res[key] = {**n, "line": i + 1}
    if errors:
        raise SystemExit("STOP:\n" + "\n".join(errors))
    return res


def build() -> None:
    res = resolve()
    used: list[str] = []

    def sub(m):
        src, key, v = m.groups()
        if key not in res:
            raise SystemExit(f"STOP: unknown key {key}")
        n = res[key]
        if key not in used:
            used.append(key)
        label = POPULATIONS[n["pop"]]
        tag = f"[источник: {n['file']}, стр. {n['line']}" + (f"; {label}]" if label else "]")
        return tag if src else (n["value"] if v else f"{n['value']} {tag}")

    KIT.mkdir(exist_ok=True)
    for t in sorted(TEMPLATES.glob("*")):
        text = t.read_text(encoding="utf-8")
        # placeholders only in markdown: BibTeX uses {{...}} for protected capitals
        (KIT / t.name).write_text(PLACEHOLDER.sub(sub, text) if t.suffix == ".md" else text, encoding="utf-8")
    rows = ["# 12. Индекс чисел", "",
            "Все числа набора. Порядок — по файлу-источнику и строке. Сгенерировано `python -m src.paper_kit.build "
            "build` из `src/paper_kit/numbers.py`; проверка — `CHECK.md`. Популяция — к чему относится число "
            "(обозначения — `03_data.md`, §6); «—» — настройка, порог или счёт признаков, популяции нет.", "",
            "| ключ | число | что означает | популяция | файл-источник | строка |", "|---|---|---|---|---|---|"]
    for key in sorted(used, key=lambda k: (res[k]["file"], res[k]["line"], k)):
        n = res[key]
        rows.append(f"| `{key}` | {n['value']} | {n['meaning']} | {POPULATIONS[n['pop']] or '—'} | `{n['file']}` | "
                    f"{n['line']} |")
    (KIT / "12_numbers_index.md").write_text("\n".join(rows) + "\n", encoding="utf-8")
    print(f"built {len(list(TEMPLATES.glob('*')))} files, {len(used)} numbers in the index")


def mask(line: str) -> str:
    s = TAG.sub(" ", line)
    s = re.sub(r"§\s?\d+(?:\.\d+)*(?:[–-]\d+(?:\.\d+)*)?", " ", s)  # section references
    s = re.sub(r"`[^`]*`", " ", s)  # inline code: file names, identifiers
    s = re.sub(r"\]\([^)]*\)", "] ", s)  # link targets
    s = re.sub(r"https?://\S+", " ", s)
    s = re.sub(r"\b\d{4}\.\d{4,5}(v\d+)?\b", " ", s)  # arXiv ids
    s = re.sub(r"\b\d{1,2}\.\d{1,2}\.\d{4}\b|\b\d{4}-\d{2}-\d{2}\b|\b20\d{2}\b", " ", s)  # dates and years
    for w in MASK_WORDS:
        s = s.replace(w, " ")
    s = re.sub(r"[A-Za-zА-Яа-яЁё_]+[\w]*\d[\w.\-]*", " ", s)  # words with digits: E15, T1, Qwen3-32B, p@10
    s = re.sub(r"@\d+%?", " ", s)
    s = re.sub(r"#\d+", " ", s)  # issue numbers
    s = re.sub(r"^\s*(#+\s*)?(\d+\.)+\s", " ", s)  # numbered headings and list items
    s = re.sub(r"^\s*\|\s*\d+\.\s", "| ", s)
    s = re.sub(r"(?:Рисун\w*|рисун\w*|Рис\.|рис\.|Fig\.?|Figure|Табл\w*|табл\w*|Table)\s*\d+", " ", s)
    # the paper plan (11_paper_outline.md): page estimates ("0.6 с.") and the number of references are a plan, not data
    s = re.sub(r"\d+(?:\.\d+)?\s?с\.", " ", s)
    s = re.sub(r"\d+\s+запис\w*", " ", s)
    return s


def units(lines: list[str]):
    """Logical units of a markdown file: a table row; a paragraph or a list item with its continuation lines.
    Code blocks are skipped. Yields (first line number, text)."""
    cur, start, in_code = [], 0, False
    for i, line in enumerate(lines, 1):
        s = line.strip()
        if s.startswith("```"):
            in_code = not in_code
            if cur:
                yield start, " ".join(cur)
                cur = []
            continue
        if in_code:
            continue
        new_unit = (not s or s.startswith("|") or s.startswith("#") or re.match(r"^([-*]|\d+\.)\s", s)
                    or not cur)
        if new_unit and cur:
            yield start, " ".join(cur)
            cur = []
        if not s:
            continue
        if s.startswith("|") or s.startswith("#"):
            yield i, line
            continue
        if not cur:
            start = i
            cur = [line] if not re.match(r"^\s*([-*])\s", line) else [line]
        else:
            cur.append(s)
    if cur:
        yield start, " ".join(cur)


def check() -> int:
    res = resolve()
    index_text = (KIT / "12_numbers_index.md").read_text(encoding="utf-8")
    index_keys = re.findall(r"^\| `([^`]+)` \|", index_text, flags=re.M)
    index_values = set()
    for k in index_keys:
        index_values |= line_numbers(res[k]["value"])
    src_cache: dict[tuple[str, int], set[str]] = {}

    def src_numbers(f: str, ln: int) -> set[str]:
        if (f, ln) not in src_cache:
            src_cache[(f, ln)] = line_numbers((ROOT / f).read_text(encoding="utf-8").splitlines()[ln - 1])
        return src_cache[(f, ln)]

    out = ["# Проверка чисел набора", "", "Сгенерировано `python -m src.paper_kit.build check`.", ""]
    bad_index = [k for k in index_keys
                 if not line_numbers(res[k]["value"]) <= src_numbers(res[k]["file"], res[k]["line"])]
    files = sorted(p for p in KIT.glob("*.md") if p.name not in SKIP_CHECK)
    n_lines = n_tokens = 0
    not_indexed, not_in_source, no_tag = [], [], []
    for p in files:
        for i, unit in units(p.read_text(encoding="utf-8").splitlines()):
            toks = [t.replace(" ", "") for t in num_tokens(mask(unit))]
            if not toks:
                continue
            n_lines += 1
            n_tokens += len(toks)
            tags = [(f.strip(), int(ln)) for f, ln in TAG.findall(unit)]
            if not tags:
                no_tag.append(f"{p.name}:{i}: {unit.strip()[:140]}")
                continue
            cited = set().union(*(src_numbers(f, ln) for f, ln in tags))
            for t in toks:
                if t not in index_values and t.rstrip("%") not in index_values:
                    not_indexed.append(f"{p.name}:{i}: «{t}» — {unit.strip()[:120]}")
                elif t not in cited and t.rstrip("%") not in cited:
                    not_in_source.append(f"{p.name}:{i}: «{t}» нет на строках {tags}")
    ok = not (bad_index or not_indexed or not_in_source or no_tag)
    out += [f"**Итог: {'всё сходится' if ok else 'есть расхождения'}.**", "",
            f"- Записей индекса: {len(index_keys)}; значение есть на указанной строке источника: "
            f"{len(index_keys) - len(bad_index)} из {len(index_keys)}.",
            "- Источники записей индекса: " + ", ".join(f"`{d}/` — {k}" for d, k in sorted(
                __import__("collections").Counter(res[x]["file"].split("/")[0] for x in index_keys).items())) + ".",
            "- Популяции записей индекса: " + "; ".join(f"{POPULATIONS[p] or '— (популяции нет)'} — {k}" for p, k in sorted(
                __import__("collections").Counter(res[x]["pop"] for x in index_keys).items(),
                key=lambda t: -t[1])) + ".",
            f"- Проверено файлов набора: {len(files)} (кроме {', '.join(sorted(SKIP_CHECK))}); абзацев и строк таблиц с "
            f"числами: {n_lines}; чисел: {n_tokens}.",
            f"- Чисел вне индекса: {len(not_indexed)}; чисел, которых нет на строках источников, указанных в том же "
            f"абзаце или строке таблицы: {len(not_in_source)}; абзацев и строк с числами без ссылки: {len(no_tag)}.", "",
            "Как проверяется: значение записи индекса должно стоять на указанной строке файла-источника (сравнение "
            "текстом, «−» = «-»). В файлах набора каждое число строки (после маскирования идентификаторов вида E15, "
            "T1, Qwen3-32B, precision@10%, номеров arXiv, дат, путей и самих ссылок) должно быть среди значений "
            "индекса и стоять на одной из строк источников, указанных в том же абзаце (пункте списка) или строке таблицы. "
            "Числа чужих работ — из `docs/LITERATURE.md`. `WRITER_PROMPT.md` — "
            "текст автора, не проверяется; `CHANGES.md` — журнал изменений с прежними числами, не проверяется. Не проверяются и плановые величины плана статьи (`11_paper_outline.md`): объём "
            "в страницах (формат «0.6 с.») и число записей литературы — это оценка, а не данные.", ""]
    for title, items in (("Записи индекса, значение которых не найдено на строке источника", bad_index),
                         ("Числа вне индекса", not_indexed), ("Числа, которых нет на строке источника", not_in_source),
                         ("Строки с числами без ссылки", no_tag)):
        if items:
            out += [f"## {title}", ""] + [f"- {x}" for x in items] + [""]
    (KIT / "CHECK.md").write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\n".join(out[:12]))
    return 0 if ok else 1


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    if cmd == "build":
        build()
    else:
        sys.exit(check())
