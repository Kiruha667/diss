"""Flag of the corpus labels of the 128 psf/requests runs (PROTOCOL.md 11.9) from the label audit
(LABEL_AUDIT_REQUESTS.md, audit/reeval_compare.csv). Existing labels are not changed.
  likely_flipped — leaderboard 0, our evaluation without today's httpbin.org environment failures 1 and no
                   environment-failed FAIL_TO_PASS test;
  undetermined   — leaderboard 0, everything else passes but an environment-failed test is FAIL_TO_PASS (2317);
  ok             — all other runs (not re-evaluated, or re-evaluation keeps 0).
Output: own_data/label_audit_corpus.csv (run_id in the corpus format '<run>/<task>'). Run from the repository root.
"""
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
A = ROOT / "own_data" / "audit"


def main() -> None:
    lb = pd.read_csv(A / "requests_lb_audit.csv")
    cmp_ = pd.read_csv(A / "reeval_compare.csv")
    flipped = cmp_.resolved_ours_without_env == 1
    flag = pd.Series("ok", index=cmp_.index)
    flag[flipped & (cmp_.our_env_f2p.fillna(0) == 0)] = "likely_flipped"
    flag[flipped & (cmp_.our_env_f2p.fillna(0) > 0)] = "undetermined"
    flags = dict(zip(cmp_.run_id + "/" + cmp_.task_id, flag))
    out = pd.DataFrame({"run_id": lb.run_id + "/" + lb.task_id})
    out["label_audit"] = out.run_id.map(flags).fillna("ok")
    corpus = pd.read_parquet(ROOT / "data" / "demo" / "runs.parquet", columns=["run_id"])
    assert out.run_id.isin(corpus.run_id).all() and len(out) == 128
    out.sort_values("run_id").to_csv(ROOT / "own_data" / "label_audit_corpus.csv", index=False)
    print(out.label_audit.value_counts().to_string())


if __name__ == "__main__":
    main()
