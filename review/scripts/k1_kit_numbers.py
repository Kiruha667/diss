"""Reviewer's check of paper_kit numbers against their cited sources (read-only; own parser, not src/paper_kit).

  python review/scripts/k1_kit_numbers.py -> review/results/kit_index_lines.txt   (index entry + its source line, for reading)
                                             review/results/kit_tags_check.csv   (every tagged number of the kit files)
                                             review/results/kit_numbers_summary.json
A. Index (12_numbers_index.md): the value string must stand on the cited line of the cited file.
B. Kit files: every numeric token of a paragraph / list item / table row must stand on one of the source lines cited in
   that same unit (own tokenizer; identifiers such as E15, T1, Qwen3-32B, p@10, arXiv ids, dates, paths are masked).
C. Numeric tokens in kit files that sit in a unit without any source tag are listed.
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KIT = ROOT / "paper_kit"
OUT = ROOT / "review" / "results"
TAG = re.compile(r"\[источник: ([^\],]+), стр\. (\d+)\]")
NUM = re.compile(r"(?<![\w.@/\-])[−+\-]?\d+(?:[   ]\d{3})*(?:[.,]\d+)?%?(?!\w)")
_cache: dict[str, list[str]] = {}


def lines_of(f: str) -> list[str]:
    if f not in _cache:
        _cache[f] = (ROOT / f).read_text(encoding="utf-8").splitlines()
    return _cache[f]


def norm(s: str) -> str:
    return s.replace("−", "-").replace(" ", " ").replace(" ", " ").replace(" ", " ")


def toks(s: str) -> list[str]:
    out = []
    for m in NUM.finditer(norm(s)):
        t = m.group(0).lstrip("+").replace(" ", "")
        out.append(t)
    return out


def mask(s: str) -> str:
    s = TAG.sub(" ", s)
    s = re.sub(r"`[^`]*`", " ", s)
    s = re.sub(r"§\s?\d+(?:\.\d+)*(?:[–-]\d+(?:\.\d+)*)?", " ", s)
    s = re.sub(r"https?://\S+", " ", s)
    s = re.sub(r"\b\d{4}\.\d{4,5}(v\d+)?\b", " ", s)
    s = re.sub(r"\b\d{1,2}\.\d{1,2}\.\d{4}\b|\b\d{4}-\d{2}-\d{2}\b|\b20\d{2}\b", " ", s)
    for w in ("Claude 4.5 Opus", "Claude Opus 4.5", "Claude Opus 4.6", "Opus 4.5", "Opus 4.6", "GPT-5.2", "GPT-5",
              "all-MiniLM-L6-v2", "τ²-bench", "τ²", "v1.x", "v2.0.0", "Итерация 1", "итерация 1", "итерации 1"):
        s = s.replace(w, " ")
    s = re.sub(r"[A-Za-zА-Яа-яЁё_]+\w*\d[\w.\-]*", " ", s)
    s = re.sub(r"@\d+%?|#\d+", " ", s)
    s = re.sub(r"^\s*(#+\s*)?(\d+\.)+\s", " ", s)
    s = re.sub(r"(?:Рисун\w*|Рис\.|рис\.|Fig\.?|Figure|Табл\w*|табл\w*|Table)\s*\d+", " ", s)
    return s


def units(text: list[str]):
    cur, start = [], 0
    for i, line in enumerate(text, 1):
        s = line.strip()
        new = (not s) or s.startswith(("|", "#")) or re.match(r"^([-*]|\d+\.)\s", s) or not cur
        if new and cur:
            yield start, " ".join(cur)
            cur = []
        if not s:
            continue
        if s.startswith(("|", "#")):
            yield i, line
            continue
        if not cur:
            start = i
        cur.append(s)
    if cur:
        yield start, " ".join(cur)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    # ---- A. index
    idx = []
    for line in (KIT / "12_numbers_index.md").read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\| `([^`]+)` \| (.*?) \| (.*?) \| `([^`]+)` \| (\d+) \|$", line)
        if m:
            idx.append({"key": m.group(1), "value": m.group(2), "meaning": m.group(3), "file": m.group(4), "line": int(m.group(5))})
    bad, L = [], []
    for e in idx:
        src = lines_of(e["file"])[e["line"] - 1]
        ok = norm(e["value"]) in norm(src)
        if not ok:
            bad.append(e["key"])
        pos = norm(src).find(norm(e["value"]))
        a = max(0, pos - 150) if pos >= 0 else 0
        ctx = src[a:a + 150 + len(e["value"]) + 110].replace("\t", " ")
        L.append(f"{'OK ' if ok else 'BAD'} {e['key']} = {e['value']}  // {e['meaning']}\n      {e['file']}:{e['line']}: …{ctx}…")
    (OUT / "kit_index_lines.txt").write_text("\n".join(L), encoding="utf-8")
    # ---- B, C. kit files
    rows, untagged = [], []
    for p in sorted(KIT.glob("*.md")):
        if p.name in ("12_numbers_index.md", "CHECK.md", "WRITER_PROMPT.md"):
            continue
        for i, u in units(p.read_text(encoding="utf-8").splitlines()):
            ts = toks(mask(u))
            if not ts:
                continue
            tags = [(f.strip(), int(n)) for f, n in TAG.findall(u)]
            if not tags:
                untagged.append({"file": p.name, "line": i, "tokens": " ".join(ts), "text": u.strip()[:160]})
                continue
            cited = set()
            for f, n in tags:
                for t in toks(lines_of(f)[n - 1]):
                    cited.add(t)
                    cited.add(t.rstrip("%"))
            for t in ts:
                rows.append({"file": p.name, "line": i, "token": t, "on_cited_line": t in cited or t.rstrip("%") in cited,
                             "tags": "; ".join(f"{f}:{n}" for f, n in tags)})
    with open(OUT / "kit_tags_check.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["file", "line", "token", "on_cited_line", "tags"])
        w.writeheader()
        w.writerows(rows)
    summ = {"index_entries": len(idx), "index_value_not_on_line": bad,
            "index_by_source_dir": {d: sum(e["file"].startswith(d) for e in idx) for d in ("reports/", "review/", "docs/", "outputs/")},
            "kit_numeric_tokens_with_tag": len(rows), "kit_tokens_not_on_cited_line": [r for r in rows if not r["on_cited_line"]],
            "kit_units_with_numbers_without_tag": untagged}
    (OUT / "kit_numbers_summary.json").write_text(json.dumps(summ, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: (v if not isinstance(v, list) else len(v)) for k, v in summ.items()}, ensure_ascii=False))
    for r in summ["kit_tokens_not_on_cited_line"][:40]:
        print("NOT ON LINE:", r)
    for r in untagged[:40]:
        print("NO TAG:", r)


if __name__ == "__main__":
    main()
