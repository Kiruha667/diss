"""Reviewer's checks of E11 / E11b (elicited self-assessment) and G0 (claims). Read-only, own code.

  python review/scripts/05_e11_g0.py -> review/results/05_e11_g0.json
"""
from __future__ import annotations

import json
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import binomtest, fisher_exact

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "outputs"
SEED = 42
res: dict = {}


# ------------------------------------------------------------------------------------------ E11 / E11b
def pair_table(att: pd.DataFrame, col: str) -> pd.DataFrame:
    """One row per unit: the risk (100 - number) of the unresolved and of the solved attempt."""
    w = att.pivot(index="task_id", columns="label", values=col)
    return pd.DataFrame({"r1": 100 - w[1], "r0": 100 - w[0]})


def pair_stats(att: pd.DataFrame, col: str, n_boot: int = 2000) -> dict:
    p = pair_table(att, col)
    ok = p.r1.notna() & p.r0.notna()
    d = np.sign((p.r1 - p.r0)[ok].to_numpy())
    conc, disc, tie = int((d > 0).sum()), int((d < 0).sum()), int((d == 0).sum())
    n_all = len(p)
    s_all = np.where(ok, np.where(p.r1 > p.r0, 1.0, np.where(p.r1 < p.r0, 0.0, 0.5)), 0.5)
    rng = np.random.default_rng(SEED)
    b = s_all[rng.integers(0, n_all, (n_boot, n_all))].mean(1)
    out = {"pairs": n_all, "pairs_parsed": int(ok.sum()), "concordant": conc, "discordant": disc, "ties_parsed": tie,
           "tie_share_incl_unparsed": round(1 - (conc + disc) / n_all, 3),
           "auroc_ties_half": round(float(s_all.mean()), 4), "ci": [round(float(x), 4) for x in np.percentile(b, [2.5, 97.5])],
           "auroc_untied_only": round(conc / max(conc + disc, 1), 4),
           "sign_test_p_two_sided": round(float(binomtest(conc, conc + disc, 0.5).pvalue), 4) if conc + disc else None}
    # power: smallest true AUROC (ties as 1/2) detectable with 80% power at this n and tie share, normal approx
    se = float(np.std(b))
    out["boot_se"] = round(se, 4)
    out["mde_auroc_80pct_power"] = round(0.5 + 2.8 * se, 3)
    return out


def e11():
    r = {}
    b = pd.read_csv(OUT / "e11b_attempts.csv")      # 112 finished pairs (E11 + E11b)
    a = pd.read_csv(OUT / "e11_attempts.csv")       # 100 pairs of E11 (all)
    for name, att in (("E11b: 112 finished pairs", b), ("E11: 100 pairs, all", a),
                      ("E11b: 61 new finished pairs", b[b.source == "E11b"]),
                      ("E11: 51 finished pairs", b[b.source == "E11"])):
        r[name] = {c: pair_stats(att, f"{c}_number") for c in ("C3", "C4")}
    # exploratory: mean of the two answers; C3 minus C4 "discount"
    b = b.copy()
    b["C34_number"] = b[["C3_number", "C4_number"]].mean(axis=1, skipna=False)
    r["E11b: 112 finished pairs"]["mean(C3,C4) [exploratory]"] = pair_stats(b, "C34_number")
    # distribution of the answers on finished pairs
    r["answer_distribution_finished_pairs"] = {
        c: {str(lab): b[b.label == lab][f"{c}_number"].round().value_counts().sort_index().to_dict() for lab in (0, 1)}
        for c in ("C3", "C4")}
    # paired comparison C3 vs C4 on the same 112 pairs (bootstrap over units)
    s = {}
    for c in ("C3", "C4"):
        p = pair_table(b, f"{c}_number")
        ok = p.r1.notna() & p.r0.notna()
        s[c] = np.where(ok, np.where(p.r1 > p.r0, 1.0, np.where(p.r1 < p.r0, 0.0, 0.5)), 0.5)
    rng = np.random.default_rng(SEED)
    idx = rng.integers(0, len(s["C3"]), (2000, len(s["C3"])))
    d = s["C3"][idx].mean(1) - s["C4"][idx].mean(1)
    r["C3 - C4 on 112 finished pairs"] = [round(float(s["C3"].mean() - s["C4"].mean()), 4),
                                         *[round(float(x), 4) for x in np.percentile(d, [2.5, 97.5])]]
    # E8-style structure on the same pairs for scale (S+O column is the E10 out-of-fold risk)
    w = b.pivot(index="task_id", columns="label", values="S+O")
    so = np.where(w[1] > w[0], 1.0, np.where(w[1] < w[0], 0.0, 0.5))
    r["S+O on 112 finished pairs"] = round(float(so.mean()), 4)
    res["E11"] = r
    for k, v in r.items():
        print(k, json.dumps(v, default=float)[:1200])


# ------------------------------------------------------------------------------------------ G0
def boot_tasks(df: pd.DataFrame, fn, n_boot: int = 1000) -> list[float]:
    groups = [g for _, g in df.groupby("task_id", sort=False)]
    rng = np.random.default_rng(SEED)
    vals = []
    for _ in range(n_boot):
        pick = rng.integers(0, len(groups), len(groups))
        vals.append(fn(pd.concat([groups[i] for i in pick], ignore_index=True)))
    return [round(float(x), 4) for x in np.nanpercentile(vals, [2.5, 97.5])]


def strat_diff(d: pd.DataFrame, col: str = "has_claim") -> float:
    """Claim share of resolved minus unresolved, within combo, weighted by the combo's number of runs."""
    num = den = 0.0
    for _, g in d.groupby("combo"):
        a, b = g[col][g.resolved == 1], g[col][g.resolved == 0]
        if len(a) and len(b):
            num += len(g) * (a.mean() - b.mean())
            den += len(g)
    return num / den


def g0():
    r = {}
    runs = pd.read_parquet(ROOT / "data" / "demo" / "runs.parquet")
    runs = runs[runs.submitted == 1][["run_id", "task_id", "model", "agent_version", "resolved"]]
    g = pd.read_csv(OUT / "g0_final_messages.csv", usecols=["run_id", "has_claim", "has_admit"])
    d = runs.merge(g, on="run_id")
    d["combo"] = d.model + " | " + d.agent_version
    d["has_claim"] = d.has_claim.astype(int)
    d["gen"] = np.where(d.agent_version == "2.0.0", "v2", "v1")
    pooled = lambda x: x.has_claim[x.resolved == 1].mean() - x.has_claim[x.resolved == 0].mean()  # noqa: E731
    r["detector_pooled"] = {"claim_resolved": round(float(d.has_claim[d.resolved == 1].mean()), 4),
                            "claim_unresolved": round(float(d.has_claim[d.resolved == 0].mean()), 4),
                            "diff_resolved_minus_unresolved": round(float(pooled(d)), 4), "ci": boot_tasks(d, pooled)}
    r["detector_within_combo_weighted"] = {"diff": round(float(strat_diff(d)), 4), "ci": boot_tasks(d, strat_diff)}
    r["detector_by_generation"] = {
        gen: {"claim_resolved": round(float(x.has_claim[x.resolved == 1].mean()), 4),
              "claim_unresolved": round(float(x.has_claim[x.resolved == 0].mean()), 4),
              "diff": round(float(pooled(x)), 4), "ci": boot_tasks(x, pooled),
              "within_combo_diff": round(float(strat_diff(x)), 4), "within_combo_ci": boot_tasks(x, strat_diff),
              "unresolved_share": round(float(1 - x.resolved.mean()), 4)}
        for gen, x in d.groupby("gen")}
    per = []
    for c, x in d.groupby("combo"):
        a, b = x.has_claim[x.resolved == 1], x.has_claim[x.resolved == 0]
        tab = [[int(a.sum()), int(len(a) - a.sum())], [int(b.sum()), int(len(b) - b.sum())]]
        per.append({"combo": c, "resolved_share": round(float(x.resolved.mean()), 3), "claim_resolved": round(float(a.mean()), 3),
                    "claim_unresolved": round(float(b.mean()), 3), "diff": round(float(a.mean() - b.mean()), 3),
                    "fisher_p": round(float(fisher_exact(tab)[1]), 4)})
    per = pd.DataFrame(per).sort_values("diff")
    print(per.to_string(index=False))
    r["detector_per_combo"] = per.to_dict("records")
    r["detector_per_combo_summary"] = {"n_positive_diff": int((per["diff"] > 0).sum()), "n_negative_diff": int((per["diff"] < 0).sum()),
                                       "n_fisher_p<0.05": int((per.fisher_p < 0.05).sum()),
                                       "min_diff": float(per["diff"].min()), "max_diff": float(per["diff"].max())}
    # within task: does 'no claim' order (unresolved, resolved) pairs of one task?
    c = n = 0.0
    for _, x in d.groupby("task_id"):
        u, v = 1 - x.has_claim[x.resolved == 0].to_numpy(), 1 - x.has_claim[x.resolved == 1].to_numpy()
        if len(u) and len(v):
            dd = u[:, None] - v[None, :]
            c += (dd > 0).sum() + 0.5 * (dd == 0).sum()
            n += dd.size
    r["detector_no_claim_within_task_auroc"] = round(c / n, 4)

    # ---- the check with 100 labelled messages
    key = pd.read_csv(OUT / "g0_human_key.csv")
    lab = pd.read_csv(OUT / "g0_human_sample.csv", encoding="utf-8-sig")
    m = key.merge(lab[["id", "claim_level", "admit", "unsure"]], on="id").merge(
        runs[["run_id", "agent_version"]], on="run_id", how="left")
    m["gen"] = np.where(m.agent_version == "2.0.0", "v2", "v1")
    N = {(c_, r_): int(((d.has_claim == c_) & (d.resolved == r_)).sum()) for c_ in (0, 1) for r_ in (0, 1)}
    r["human_check_cells_population"] = {f"det={k[0]}|resolved={k[1]}": v for k, v in N.items()}

    def shares(mm, thr):
        out = {}
        for r_ in (1, 0):
            p1 = (mm[(mm.det_claim == 1) & (mm.resolved == r_)].claim_level >= thr).mean()
            p0 = (mm[(mm.det_claim == 0) & (mm.resolved == r_)].claim_level >= thr).mean()
            out[r_] = (N[(1, r_)] * p1 + N[(0, r_)] * p0) / (N[(1, r_)] + N[(0, r_)])
        return out

    rng = np.random.default_rng(SEED)
    hc = {}
    for name, thr in (("strict", 2), ("broad", 1)):
        s = shares(m, thr)
        bs = []
        cells = {k: x for k, x in m.groupby(["det_claim", "resolved"])}
        for _ in range(2000):
            mb = pd.concat([x.iloc[rng.integers(0, len(x), len(x))] for x in cells.values()])
            sb = shares(mb, thr)
            bs.append(sb[1] - sb[0])
        se = float(np.std(bs))
        hc[name] = {"resolved": round(float(s[1]), 3), "unresolved": round(float(s[0]), 3), "diff": round(float(s[1] - s[0]), 3),
                    "ci": [round(float(x), 3) for x in np.percentile(bs, [2.5, 97.5])], "boot_se": round(se, 3),
                    "share_boot_diff_below_0": round(float(np.mean(np.array(bs) < 0)), 3),
                    "mde_abs_diff_80pct_power": round(2.8 * se, 3),
                    "missed_in_det0": {f"resolved={r_}": [int((m[(m.det_claim == 0) & (m.resolved == r_)].claim_level >= thr).sum()), 25]
                                       for r_ in (1, 0)}}
    r["human_check"] = hc
    r["human_check_levels_by_cell"] = m.groupby(["det_claim", "resolved"]).claim_level.value_counts().unstack(fill_value=0).to_dict("index")
    r["human_check_levels_by_cell"] = {str(k): v for k, v in r["human_check_levels_by_cell"].items()}
    r["human_check_generation_by_cell"] = {str(k): v for k, v in m.groupby(["det_claim", "resolved"]).gen.value_counts().unstack(fill_value=0).to_dict("index").items()}
    r["human_check_strict_share_by_generation_unweighted"] = {
        gen: {"n": int(len(x)), "strict_share": round(float((x.claim_level >= 2).mean()), 3)} for gen, x in m.groupby("gen")}
    r["human_check_unsure"] = int((m.unsure == 1).sum())
    # population composition: generation by resolved (the weights of a possible Simpson effect)
    r["population_generation_by_resolved"] = d.groupby("resolved").gen.value_counts(normalize=True).round(3).unstack().to_dict("index")
    res["G0"] = r
    print(json.dumps({k: v for k, v in r.items() if k != "detector_per_combo"}, indent=1, default=float)[:5000])


if __name__ == "__main__":
    e11()
    g0()
    (ROOT / "review" / "results" / "05_e11_g0.json").write_text(json.dumps(res, indent=1, ensure_ascii=False, default=float), encoding="utf-8")
