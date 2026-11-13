"""Second link of the chain: do the numbers of the kit's index (taken from reports/) stand in outputs/?

  python review/scripts/k3_reports_vs_outputs.py -> review/results/kit_reports_vs_outputs.csv

For every index entry whose source is reports/<stage>…: every numeric token of the value (point estimate and interval
bounds) is searched among the numeric cells of the stage's SUMMARY files in outputs/ (CSV, JSON under 60 KB; per-run
tables such as *_attempts, *_runs, *_risks are skipped, otherwise any 3-digit number would match), compared at the
precision the report prints: |x − v| < 0.5·10^(−decimals); percentages also against fractions (50.0% ~ 0.500),
percentage points also against fractions. Entries with tokens not found are listed for reading by eye. A match means
"this value exists in the stage's outputs", not "it is the right cell" - the right cell is checked by the rerun of
the author's code and by the reviewer's own recomputation.
"""
from __future__ import annotations

import csv
import gzip
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "outputs"
STAGE_FILES = {"E15": ["e15_"], "E16": ["e16_"], "E8_": ["e8_"], "E9_": ["e9_"], "E10": ["e10_"], "E11b": ["e11b_"],
               "E11_": ["e11_"], "E12": ["e12_"], "E13": ["e13_"], "E14": ["e14_"], "E7_": ["e7_"],
               "G0_HUMAN": ["g0_human_results"], "G0_false": ["g0_final_messages"], "KTH_DEEPSWE": ["kth_deepswe", "e15_noise", "e15_label"],
               "E4_": ["summary", "nested_"]}
NUM = re.compile(r"[−+\-]?\d+(?:[   ]\d{3})*(?:\.\d+)?")


def numbers_of(obj, acc):
    if isinstance(obj, dict):
        for v in obj.values():
            numbers_of(v, acc)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            numbers_of(v, acc)
    elif isinstance(obj, bool):
        return
    elif isinstance(obj, (int, float)):
        if obj == obj:
            acc.append(float(obj))
    elif isinstance(obj, str):
        try:
            acc.append(float(obj))
        except ValueError:
            pass


def load_numbers(prefixes: list[str]) -> np.ndarray:
    acc: list[float] = []
    for p in sorted(OUT.iterdir()):
        if not p.is_file() or not any(p.name.startswith(x) for x in prefixes):
            continue
        if p.stat().st_size > 60_000 or p.name.endswith((".jsonl", ".xlsx")) or re.search(r"attempts|runs|risk|oof|parsed|sample|tokens|features|checks|per_task|parse|final_messages", p.name):
            continue
        try:
            if p.suffix == ".json":
                numbers_of(json.loads(p.read_text(encoding="utf-8")), acc)
            elif p.name.endswith((".csv", ".csv.gz")):
                df = pd.read_csv(p)
                for c in df.columns:
                    if pd.api.types.is_numeric_dtype(df[c]):
                        acc += df[c].dropna().astype(float).tolist()
                acc.append(float(len(df)))
        except Exception as e:  # noqa: BLE001
            print("skip", p.name, type(e).__name__)
    return np.unique(np.array(acc, float))


def main():
    idx = []
    for line in (ROOT / "paper_kit" / "12_numbers_index.md").read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\| `([^`]+)` \| (.*?) \| (.*?) \| `([^`]+)` \| (\d+) \|$", line)
        if m and m.group(4).startswith("reports/"):
            idx.append((m.group(1), m.group(2), m.group(3), m.group(4), int(m.group(5))))
    cache: dict[str, np.ndarray] = {}
    rows = []
    for key, value, meaning, f, ln in idx:
        stage = next((s for s in STAGE_FILES if Path(f).name.startswith(s.rstrip("_")) and (s != "E11_" or "E11b" not in f)), None)
        if "E11b" in f:
            stage = "E11b"
        if stage is None:
            rows.append({"key": key, "value": value, "source": f"{f}:{ln}", "stage_files": "", "not_found": "no outputs mapped"})
            continue
        if stage not in cache:
            cache[stage] = load_numbers(STAGE_FILES[stage])
        pool = cache[stage]
        v = value.replace("−", "-").replace(" ", " ").replace(" ", " ")
        pct = "%" in v or "п.п." in v
        missing = []
        for t in NUM.findall(v):
            t2 = t.replace(" ", "")
            x = float(t2)
            dec = len(t2.split(".")[1]) if "." in t2 else 0
            tol = 0.5 * 10 ** (-dec) + 1e-9
            cands = [x, -x] + ([x / 100, -x / 100] if (pct or True) else [])
            tols = [tol, tol, tol / 100, tol / 100]
            if not any(np.any(np.abs(pool - c) <= tl) for c, tl in zip(cands, tols)):
                missing.append(t2)
        rows.append({"key": key, "value": value, "source": f"{f}:{ln}", "stage_files": ",".join(STAGE_FILES[stage]),
                     "not_found": " ".join(missing)})
    with open(ROOT / "review" / "results" / "kit_reports_vs_outputs.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["key", "value", "source", "stage_files", "not_found"])
        w.writeheader()
        w.writerows(rows)
    bad = [r for r in rows if r["not_found"]]
    print(f"entries from reports/: {len(rows)}; all tokens found in the stage's outputs: {len(rows) - len(bad)}; to read by eye: {len(bad)}")
    for r in bad:
        print(f"  {r['key']} = {r['value']} [{r['source']}] not found: {r['not_found']}")


if __name__ == "__main__":
    main()

