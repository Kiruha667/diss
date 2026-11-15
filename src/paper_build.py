"""Build of the paper (paper/main.tex) with latexmk and a report of the build -> paper/BUILD.md.

  python -m src.paper_build   -> latexmk -gg -pdf in paper/ (clean full build), then BUILD.md: pages, overfull and
                                 underfull boxes, undefined references and citations, BibTeX warnings, LaTeX errors

latexmk is taken from PATH, else from the directory in the environment variable TEXLIVE_BIN. The PDF and the
intermediate files are not committed (.gitignore). Exit code 1 if the build failed, a reference or a citation is
undefined, or a box is overfull.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

from src.common import ROOT

PAPER = ROOT / "paper"
PLAN_PAGES = "6.0"  # paper_kit/11_paper_outline.md: the page budget including title, abstract and references


def latexmk() -> str:
    exe = shutil.which("latexmk")
    if not exe and os.environ.get("TEXLIVE_BIN"):
        exe = shutil.which("latexmk", path=os.environ["TEXLIVE_BIN"])
    if not exe:
        raise SystemExit("latexmk not found: put TeX Live's bin directory on PATH or into TEXLIVE_BIN")
    return exe


def run(exe: str) -> tuple[int, str]:
    env = {**os.environ, "max_print_line": "10000"}  # unwrapped log lines
    env["PATH"] = str(Path(exe).parent) + os.pathsep + env.get("PATH", "")
    p = subprocess.run([exe, "-gg", "-pdf", "-interaction=nonstopmode", "-halt-on-error", "main.tex"], cwd=PAPER,
                       env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, p.stdout + p.stderr


def version(exe: str, prog: str) -> str:
    try:
        out = subprocess.run([str(Path(exe).parent / prog), "--version"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace").stdout
        # on Windows latexmk first prints the console code pages; take the line with the program name
        lines = [x.strip() for x in out.splitlines() if re.search(r"pdfTeX|Latexmk|Version", x)]
        return lines[0] if lines else "?"
    except OSError:
        return "?"


def where(log: str, pos: int) -> str:
    """The input file at log offset pos: the last section file (or main.bbl, the bibliography) opened before it."""
    files = list(re.finditer(r"\((\./[^\s()]+\.(?:tex|bbl))", log[:pos]))
    return files[-1].group(1)[2:] if files else "main.tex"


def parse(log: str, blg: str) -> dict:
    boxes = {"Overfull": [], "Underfull": []}
    for m in re.finditer(r"^(Overfull|Underfull) \\([hv]box) \(([^)]*)\) (.*)$", log, flags=re.M):
        boxes[m.group(1)].append((where(log, m.start()), m.group(2), m.group(3), m.group(4).strip()))
    pages = re.search(r"Output written on \S+ \((\d+) pages?, (\d+) bytes\)", log)
    return {
        "pages": int(pages.group(1)) if pages else None,
        "bytes": int(pages.group(2)) if pages else None,
        "boxes": boxes,
        "refs": re.findall(r"LaTeX Warning: Reference `([^']+)' on page (\d+) undefined", log),
        "cites": re.findall(r"LaTeX Warning: Citation `([^']+)' on page (\d+) undefined", log),
        "rerun": bool(re.search(r"Label\(s\) may have changed|Rerun to get", log)),
        "errors": re.findall(r"^! (.*)$", log, flags=re.M),
        "bib_warn": re.findall(r"^Warning--(.*)$", blg, flags=re.M),
        "bib_err": int(m.group(1)) if (m := re.search(r"\(There (?:were|was) (\d+) error message", blg)) else 0,
        "other_warn": [w for w in re.findall(r"^((?:LaTeX|Package \w+) Warning: .*)$", log, flags=re.M)
                       if "undefined" not in w and "Label(s) may have changed" not in w],
    }


def report(code: int, r: dict, tools: list[str]) -> list[str]:
    ok = (code == 0 and r["pages"] and not r["errors"] and not r["refs"] and not r["cites"] and not r["bib_err"]
          and not r["boxes"]["Overfull"])
    out = ["# Сборка статьи", "",
           f"Сгенерировано `python -m src.paper_build` {date.today().isoformat()}: `latexmk -gg -pdf` в `paper/` "
           f"(полная сборка с нуля). Инструменты: {'; '.join(tools)}. PDF (`paper/main.pdf`) в git не добавляется.", "",
           f"**Итог: {'собрано, замечаний нет' if ok else 'есть замечания'}.**", "",
           f"- Код выхода latexmk: {code}.",
           f"- Страниц: {r['pages'] if r['pages'] is not None else '— (PDF не создан)'}"
           + (f" ({r['bytes'] / 1024:.0f} КБ)" if r["bytes"] else "")
           + f"; объём по плану — {PLAN_PAGES} с. вместе с заголовком, аннотацией и литературой "
             "(`paper_kit/11_paper_outline.md`).",
           f"- Переполнения боксов (Overfull): {len(r['boxes']['Overfull'])}; недозаполнения (Underfull): "
           f"{len(r['boxes']['Underfull'])}.",
           f"- Неразрешённые ссылки (\\ref): {len(r['refs'])}; цитаты (\\cite): {len(r['cites'])}; нужен повторный "
           f"проход: {'да' if r['rerun'] else 'нет'}.",
           f"- BibTeX: ошибок {r['bib_err']}, предупреждений {len(r['bib_warn'])}.",
           f"- Ошибки LaTeX: {len(r['errors'])}; прочие предупреждения LaTeX и пакетов: {len(r['other_warn'])}.", ""]
    for title, items in (
            ("Ошибки LaTeX", r["errors"]),
            ("Overfull", [f"`{f}`: \\{b} ({amount}) {what}" for f, b, amount, what in r["boxes"]["Overfull"]]),
            ("Underfull", [f"`{f}`: \\{b} ({amount}) {what}" for f, b, amount, what in r["boxes"]["Underfull"]]),
            ("Неразрешённые ссылки", [f"`{k}` (стр. {p})" for k, p in r["refs"]]),
            ("Неразрешённые цитаты", [f"`{k}` (стр. {p})" for k, p in r["cites"]]),
            ("BibTeX", r["bib_warn"]),
            ("Прочие предупреждения", r["other_warn"])):
        if items:
            out += [f"## {title}", ""] + [f"- {x}" for x in items] + [""]
    return out


def main() -> int:
    exe = latexmk()
    code, _ = run(exe)
    log = (PAPER / "main.log").read_text(encoding="utf-8", errors="replace") if (PAPER / "main.log").exists() else ""
    blg = (PAPER / "main.blg").read_text(encoding="utf-8", errors="replace") if (PAPER / "main.blg").exists() else ""
    r = parse(log, blg)
    tools = [version(exe, "pdflatex"), version(exe, "latexmk").replace("Latexmk, ", "latexmk ")]
    tools = [t for t in tools if t != "?"]
    out = report(code, r, tools)
    (PAPER / "BUILD.md").write_text("\n".join(out) + "\n", encoding="utf-8", newline="\n")
    print("\n".join(out[4:11]))
    ok = out[4].startswith("**Итог: собрано")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
