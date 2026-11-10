"""Mutual information between the G0 detector's claim and the outcome. No API calls.

  python -m src.g0_mutual_information  -> outputs/g0_mutual_information.json

Main: the 7864 submitted leaderboard runs (data/demo/runs.parquet, submitted == 1), detector = has_claim of
outputs/g0_final_messages.csv, outcome = resolved. For comparison: the G0 population (all 7975 trajectories of
outputs/g0_final_messages.csv, outcome = label). Bits (log2). The detector misses claims (recall 0.56-0.66 strict,
reports/G0_HUMAN_CHECK.md), so this is the information in the detector's claim, not in the true claim.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from src.common import OUTPUTS, ROOT


def entropy(p: np.ndarray) -> float:
    p = p[p > 0]
    return float(-(p * np.log2(p)).sum())


def mutual_information(x: np.ndarray, y: np.ndarray) -> dict:
    joint = pd.crosstab(x, y, normalize=True).to_numpy()
    px, py = joint.sum(axis=1), joint.sum(axis=0)
    mi = entropy(px) + entropy(py) - entropy(joint.ravel())
    hy = entropy(py)
    return {"n": int(len(x)), "mutual_information_bits": mi, "outcome_entropy_bits": hy,
            "normalized_mi": mi / hy if hy > 0 else float("nan"),
            "claim_share_by_outcome": {str(k): float(v) for k, v in pd.Series(x).groupby(np.asarray(y)).mean().items()}}


def run() -> dict:
    g = pd.read_csv(OUTPUTS / "g0_final_messages.csv", usecols=["run_id", "has_claim", "label"])
    runs = pd.read_parquet(ROOT / "data" / "demo" / "runs.parquet", columns=["run_id", "submitted", "resolved"])
    sub = runs[runs.submitted == 1].merge(g, on="run_id")
    assert len(sub) == 7864
    out = {"submitted_7864": {**mutual_information(sub.has_claim.astype(int).to_numpy(), sub.resolved.to_numpy()),
                              "outcome": "resolved (1 = resolved)"},
           "g0_population_7975": {**mutual_information(g.has_claim.astype(int).to_numpy(), g.label.to_numpy()),
                                  "outcome": "label (1 = failed)"}}
    (OUTPUTS / "g0_mutual_information.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


if __name__ == "__main__":
    print(json.dumps(run(), indent=1))
