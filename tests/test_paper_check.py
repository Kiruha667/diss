"""Tests of the paper check (src/paper_check.py): \\numsrc against the numbers index, numbers without \\numsrc, \\cite."""
from pathlib import Path

from src import paper_check as pc

INDEX = """# 12. Индекс чисел

| ключ | число | что означает | популяция | файл-источник | строка |
|---|---|---|---|---|---|
| `a.runs` | 7 732 | прогонов | популяция 7 732 | `reports/X.md` | 4 |
| `a.diff` | −0.077 [−0.098; −0.057] | разность | популяция 7 732 | `reports/X.md` | 5 |
| `a.boot` | 1000 выборок, seed 42 | бутстрэп | — | `reports/X.md` | 6 |
"""
BIB = "% comment\n@misc{known2026,\n  title = {{T}},\n  year = {2026}\n}\n\n@misc{old2024,\n  year = {2024}\n}\n"
RELATED = "## 3. Что цитировать\n\n**В статье:** `known2026`.\n\n**Только диссертация:** `old2024`.\n\nТекст.\n"
TEX = r"""\section{Data}
% 99 numbers in a comment do not count; nor \numsrc{zzz}{1} in a comment
We use \numsrc{a.runs@L3}{7\,732} runs; the difference is \numsrc{a.diff@L6}{$-0.077$ [$-0.098$;\,$-0.057$]}.
Bootstrap: \numsrc{a.boot}{1000 resamples, seed 42}. Wrong: \numsrc{a.runs@L6}{7\,733}. Unknown: \numsrc{b.x@L9}{5}.
Section~2, Table 3, the year 2026, arXiv 2611.08285, E15, T1, C3 and Qwen3-32B are not numbers of the work, but 42 runs is.
See~\cite{known2026,missing2025} and \cite{old2024}. \TODO{text}
\begin{figure}[t]\includegraphics[width=0.9\columnwidth]{fig2_run_level}\caption{No numbers here.}\end{figure}
"""


def make_tree(tmp_path: Path) -> Path:
    (tmp_path / "paper_kit" / "figures").mkdir(parents=True)
    (tmp_path / "paper" / "sections").mkdir(parents=True)
    (tmp_path / "paper" / "figures").mkdir()
    (tmp_path / "paper_kit" / "12_numbers_index.md").write_text(INDEX, encoding="utf-8")
    (tmp_path / "paper_kit" / "02_related_work.md").write_text(RELATED, encoding="utf-8")
    (tmp_path / "paper_kit" / "references.bib").write_text(BIB, encoding="utf-8")
    (tmp_path / "paper" / "refs.bib").write_text(BIB, encoding="utf-8")
    (tmp_path / "paper_kit" / "figures" / "f.png").write_bytes(b"png")
    (tmp_path / "paper" / "figures" / "f.png").write_bytes(b"png")
    (tmp_path / "paper" / "sections" / "02_data.tex").write_text(TEX, encoding="utf-8")
    return tmp_path


def test_compare_values():
    assert pc.compare(r"7\,732", "7 732") == "exact"
    assert pc.compare(r"$-0.077$ [$-0.098$;\,$-0.057$]", "−0.077 [−0.098; −0.057]") == "exact"
    assert pc.compare(r"95\%", "95%, кластерный бутстрэп по задачам") == "numbers"
    assert pc.compare("1000 resamples, seed 42", "1000 выборок, seed 42") == "numbers"
    assert pc.compare("$k=5$", "k = 5") == "exact"
    assert pc.compare("0.318", "0.319") is None
    assert pc.compare("+0.077", "−0.077") is None          # the sign is part of the number
    assert pc.compare("4247", "4 247") == "numbers"         # thousands separator only


def test_check_reports_and_fixes(tmp_path):
    root = make_tree(tmp_path)
    rep = pc.check(root, fix=True)
    assert sorted(u.key for u, _ in rep.stale) == ["a.boot", "a.runs", "a.runs"]  # a.diff@L6 is current
    text = (root / "paper" / "sections" / "02_data.tex").read_text(encoding="utf-8")
    assert r"\numsrc{a.runs@L5}{7\,732}" in text and r"\numsrc{a.diff@L6}" in text and r"\numsrc{a.boot@L7}" in text
    assert r"\numsrc{a.runs@L5}{7\,733}" in text            # wrong value: line fixed, value kept and reported
    assert [(u.key, v) for u, v in rep.mismatch] == [("a.runs", "7 732")]
    assert [u.key for u in rep.unknown] == ["b.x"]
    assert [t for _, _, t, _ in rep.bare] == ["42"]
    assert rep.missing_cites == [("missing2025", "paper/sections/02_data.tex", 6)]
    assert rep.diss_only == ["old2024"]
    assert [t for _, _, t in rep.todos] == ["[TODO: text]"]
    assert rep.copies == []
    assert rep.errors(fix=True) == 4                         # mismatch, unknown key, bare number, missing cite
    again = pc.check(root, fix=True)                         # idempotent: nothing stale after the fix
    assert again.stale == []


def test_no_fix_leaves_file_and_counts_stale(tmp_path):
    root = make_tree(tmp_path)
    before = (root / "paper" / "sections" / "02_data.tex").read_bytes()
    rep = pc.check(root, fix=False)
    assert (root / "paper" / "sections" / "02_data.tex").read_bytes() == before
    assert rep.errors(fix=False) == 4 + len(rep.stale)


def test_copies_must_match(tmp_path):
    root = make_tree(tmp_path)
    (root / "paper" / "figures" / "f.png").write_bytes(b"other")
    (root / "paper" / "refs.bib").write_text(BIB + "\n@misc{extra,\n}\n", encoding="utf-8")
    rep = pc.check(root, fix=False)
    assert len(rep.copies) == 2
