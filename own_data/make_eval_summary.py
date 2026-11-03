"""own_data/eval_summary.json: one record per SWE-bench report.json in own_data/eval/ (own runs and gold_pre / gold_post),
for notebooks/visual_demo.ipynb. Run from the repository root:  python own_data/make_eval_summary.py
Fields: run_id (the folder under own_data/eval/: run name or gold_pre), task_id, resolved (0/1), patch_applied (0/1),
f2p_pass / f2p_fail (FAIL_TO_PASS tests that pass / fail after the patch), p2p_pass / p2p_fail (same for
PASS_TO_PASS). Runs without report.json (empty patch, evaluation failure) have no record.
"""
from __future__ import annotations

import json
from pathlib import Path

OWN = Path(__file__).resolve().parent


def record(path: Path) -> dict:
    task = path.parent.name
    rep = json.loads(path.read_text(encoding="utf-8"))[task]
    ts = rep.get("tests_status") or {}
    n = lambda kind, res: len((ts.get(kind) or {}).get(res) or [])  # noqa: E731
    return {"run_id": path.parent.parent.name, "task_id": task, "resolved": int(bool(rep.get("resolved"))),
            "patch_applied": int(bool(rep.get("patch_successfully_applied"))),
            "f2p_pass": n("FAIL_TO_PASS", "success"), "f2p_fail": n("FAIL_TO_PASS", "failure"),
            "p2p_pass": n("PASS_TO_PASS", "success"), "p2p_fail": n("PASS_TO_PASS", "failure")}


def main() -> None:
    recs = [record(p) for p in sorted((OWN / "eval").glob("*/*/report.json"))]
    (OWN / "eval_summary.json").write_text(json.dumps(recs, indent=1) + "\n", encoding="utf-8", newline="\n")
    runs = {r["run_id"] for r in recs}
    print(f"{len(recs)} records, {len(runs)} run folders, resolved {sum(r['resolved'] for r in recs)}")


if __name__ == "__main__":
    main()
