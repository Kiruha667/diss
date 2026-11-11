"""Reviewer's remaining checks on the leaderboard corpus (read-only).

  python review/scripts/10_misc.py -> review/results/10_misc.json
 a) how much of M+S is 'model identity + number of steps' (paired bootstrap of the differences);
 b) empty submitted patches: a trivially detectable failure inside the population - their share in the top 10%;
 c) 'model strength vs predictability' (E13 part 2) under three definitions of the per-combo AUROC of S.
"""
from __future__ import annotations

import importlib.util
import json
import math
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.metrics import roc_auc_score

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("lb", HERE / "01_leaderboard.py")
lb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lb)
res: dict = {}


def main():
    p = lb.load()
    y = p.y.to_numpy()
    g = p.task_id.to_numpy()
    fold = lb.folds(g)
    risk = {"M+S": lb.oof_blocks(p, y, fold, ["M", "S"]), "M+LEN": lb.oof_blocks(p, y, fold, ["M", "LEN"]),
            "M+N gbm": lb.oof_blocks(p, y, fold, ["M", "N"], make=lb.gbm), "M": lb.oof_blocks(p, y, fold, ["M"]),
            "S": lb.oof_blocks(p, y, fold, ["S"])}
    # ---- a) paired differences
    _, da = lb.boot_ci(y, g, risk, roc_auc_score)
    _, dp = lb.boot_ci(y, g, risk, lambda a, b: lb.p_at(a, b, 0.10))
    A = {}
    for k, s in risk.items():
        A[k] = {"auroc": round(roc_auc_score(y, s), 4), "p@10": round(lb.p_at(y, s, 0.10), 4),
                "p@10_ci": [round(float(x), 4) for x in np.percentile(dp[k], [2.5, 97.5])]}
    for a, b in (("M+S", "M+N gbm"), ("M+S", "M+LEN")):
        A[f"{a} - {b}"] = {"auroc": [round(A[a]["auroc"] - A[b]["auroc"], 4), *[round(float(x), 4) for x in np.percentile(da[a] - da[b], [2.5, 97.5])]],
                           "p@10": [round(A[a]["p@10"] - A[b]["p@10"], 4), *[round(float(x), 4) for x in np.percentile(dp[a] - dp[b], [2.5, 97.5])]]}
    res["a_model_plus_length"] = A
    print(json.dumps(A, indent=0))

    # ---- b) empty submitted patches
    pf = pd.read_csv(ROOT / "outputs" / "e13_patch_features.csv.gz")
    pf = pf[pf.corpus == "leaderboard"].set_index("run_id")
    empty = pf.loc[p.run_id, "p_empty"].to_numpy().astype(bool)
    k = math.ceil(0.10 * len(y))
    B = {"empty_submitted_patches": int(empty.sum()), "unresolved_among_them": int(y[empty].sum()),
         "share_of_all_unresolved": round(float(empty.sum() / y.sum()), 4),
         "by_generation": p[empty].agent_version.eq("2.0.0").map({True: "v2", False: "v1"}).value_counts().to_dict(),
         "by_combo": p[empty].combo.value_counts().to_dict()}
    for name in ("M+S", "S", "M+N gbm"):
        s = risk[name]
        top = np.argsort(-s, kind="mergesort")[:k]
        ne = ~empty
        B[name] = {"empty_in_top10pct": int(empty[top].sum()), "p@10_all": round(float(y[top].mean()), 4),
                   "auroc_all": round(roc_auc_score(y, s), 4),
                   "auroc_without_empty": round(roc_auc_score(y[ne], s[ne]), 4),
                   "p@10_without_empty": round(lb.p_at(y[ne], s[ne], 0.10), 4),
                   "base_rate_without_empty": round(float(y[ne].mean()), 4),
                   "auroc_of_score_for_empty_vs_rest": round(roc_auc_score(empty.astype(int), s), 4)}
    # the rule "empty patch -> review first, then M+S"
    s2 = np.where(empty, 2.0, risk["M+S"])
    B["rule: empty first, then M+S"] = {"p@10": round(lb.p_at(y, s2, 0.10), 4), "auroc": round(roc_auc_score(y, s2), 4)}
    res["b_empty_patches"] = B
    print(json.dumps(B, indent=0))

    # ---- c) strength vs predictability
    d = json.loads((ROOT / "review" / "results" / "01_leaderboard.json").read_text(encoding="utf-8"))["P6_lomo"]["table"]
    t = pd.DataFrame(d)
    t["gen"] = np.where(t.combo.str.endswith("2.0.0"), "v2", "v1")
    C = {}
    for col in ("auroc_S_pooled_oof", "auroc_S_lomo", "auroc_S_within_combo_training", "auroc_n_steps_raw", "auroc_loco_difficulty"):
        r = spearmanr(t.resolved, t[col])
        rng = np.random.default_rng(42)
        bs = [spearmanr(t.resolved.to_numpy()[ix], t[col].to_numpy()[ix]).statistic for ix in (rng.integers(0, len(t), len(t)) for _ in range(2000))]
        C[col] = {"rho": round(float(r.statistic), 3), "p": round(float(r.pvalue), 4),
                  "boot_ci": [round(float(x), 3) for x in np.nanpercentile(bs, [2.5, 97.5])],
                  "rho_v1": round(float(spearmanr(t.resolved[t.gen == "v1"], t[col][t.gen == "v1"]).statistic), 3),
                  "rho_v2": round(float(spearmanr(t.resolved[t.gen == "v2"], t[col][t.gen == "v2"]).statistic), 3)}
    res["c_strength_vs_predictability"] = C
    print(json.dumps(C, indent=0))
    (ROOT / "review" / "results" / "10_misc.json").write_text(json.dumps(res, indent=1, default=float), encoding="utf-8")


if __name__ == "__main__":
    main()
