"""Check the interim layer against its sources with the author's code (read-only, nothing written to data/ or outputs/).

  python review/scripts/03_verify_interim.py lb      trajectories.jsonl.gz -> record_rows -> compare with runs.parquet
  python review/scripts/03_verify_interim.py labels  local raw report.json / traj.json -> compare with runs.parquet
  python review/scripts/03_verify_interim.py kth <scratch_interim_dir>   compare a rebuilt KTH runs.parquet with the repo one
"""
from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "notebooks"))
RES = ROOT / "review" / "results"


def lb():
    import build_demo_data as bdd
    from demo_lib.features import FEATURES
    from src import g0_false_success as g0
    from src.common import CONFIG, load_leak_markers, trajectories_path
    from src.segment import compile_markers

    runs = pd.read_parquet(ROOT / "data" / "demo" / "runs.parquet").set_index("run_id")
    mre, claim = compile_markers(load_leak_markers()), g0.load_patterns(CONFIG / "claim_patterns.txt")
    cols = FEATURES + ["submitted", "resolved", "n_steps"]
    n, bad, final_bad, seen = 0, [], 0, set()
    with gzip.open(trajectories_path("swebench_bashonly"), "rt", encoding="utf-8") as fh:
        for line in fh:
            rec = json.loads(line)
            row, _, _ = bdd.record_rows(rec, mre, claim)
            ref = runs.loc[row["run_id"]]
            seen.add(row["run_id"])
            n += 1
            for c in cols:
                if abs(float(row[c]) - float(ref[c])) > 1e-9:
                    bad.append((row["run_id"], c, float(row[c]), float(ref[c])))
            final_bad += int((row["final_message"] or "") != (ref["final_message"] or ""))
            for c in ("task_id", "model", "agent_version", "exit_status", "phase_seq"):
                if str(row[c]) != str(ref[c]):
                    bad.append((row["run_id"], c, row[c], ref[c]))
            if n % 1000 == 0:
                print(n, "records,", len(bad), "mismatches", flush=True)
    out = {"records": n, "runs_parquet_rows": len(runs), "all_rows_seen": seen == set(runs.index),
           "feature_or_field_mismatches": len(bad), "final_message_mismatches": final_bad, "examples": bad[:10]}
    print(out)
    (RES / "03_verify_runs_parquet.json").write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")


def labels():
    from src.adapters import swebench_bashonly as sb

    runs = pd.read_parquet(ROOT / "data" / "demo" / "runs.parquet").set_index("run_id")
    raw = ROOT / "data" / "raw" / "swebench_bashonly"
    n = miss = bad = sub_bad = 0
    for rp in sorted(raw.glob("*/reports/*.report.json")):
        sub, task = rp.parent.parent.name, rp.name[: -len(".report.json")]
        rid = f"{sub}/{task}"
        if rid not in runs.index:
            miss += 1
            continue
        lab = sb.read_label(rp, task)
        n += 1
        bad += int(lab != 1 - int(runs.loc[rid, "resolved"]))
        tp = raw / sub / "trajs" / f"{task}.traj.json"
        if tp.exists():
            es = str((json.loads(tp.read_text(encoding="utf-8")).get("info") or {}).get("exit_status") or "")
            sub_bad += int((es == "Submitted") != bool(runs.loc[rid, "submitted"]))
    out = {"local_reports_checked": n, "not_in_runs_parquet": miss, "label_mismatches": bad,
           "submitted_flag_mismatches": sub_bad}
    # the inventory of report.json of 9 candidate runs (E13) and the new runs (E14)
    new = ROOT / "data" / "raw" / "swebench_bashonly_new"
    e14 = pd.read_csv(ROOT / "outputs" / "e14_runs.csv").set_index("run_id")
    n2 = bad2 = 0
    for rp in sorted(new.glob("*/reports/*.report.json")):
        sub, task = rp.parent.parent.name, rp.name[: -len(".report.json")]
        rid = f"{sub}/{task}"
        if rid in e14.index:
            n2 += 1
            bad2 += int(sb.read_label(rp, task) != 1 - int(e14.loc[rid, "resolved"]))
    out.update({"e14_reports_checked": n2, "e14_label_mismatches": bad2})
    print(out)
    (RES / "03_verify_labels.json").write_text(json.dumps(out, indent=1), encoding="utf-8")


def kth(scratch: str):
    a = pd.read_parquet(Path(scratch) / "agentic_evals" / "runs.parquet").sort_values("run_id").reset_index(drop=True)
    b = pd.read_parquet(ROOT / "data" / "interim" / "agentic_evals" / "runs.parquet").sort_values("run_id").reset_index(drop=True)
    out = {"rows": [len(a), len(b)], "same_columns": list(a.columns) == list(b.columns)}
    if len(a) == len(b) and out["same_columns"]:
        num = [c for c in a.columns if pd.api.types.is_numeric_dtype(a[c])]
        out["max_abs_numeric_diff"] = float(np.nanmax(np.abs(a[num].to_numpy(float) - b[num].to_numpy(float))))
        out["non_numeric_mismatch"] = int(sum((a[c].astype(str) != b[c].astype(str)).sum() for c in a.columns if c not in num))
    print(out)
    (RES / "03_verify_kth_build.json").write_text(json.dumps(out, indent=1), encoding="utf-8")


if __name__ == "__main__":
    {"lb": lb, "labels": labels}.get(sys.argv[1], lambda: kth(sys.argv[2]))()
