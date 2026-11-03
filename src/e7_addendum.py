"""E7 addendum — exploratory analysis, NOT in reports/E7_PROTOCOL.md: the within-task signal of structure (S) when the
strength of the two models of a pair is controlled.

  python -m src.e7_addendum  -> outputs/e7_addendum.csv

Input: outputs/e7_oof_risk.csv.gz (population "all", out-of-fold risks of S and M from src.e7_sources).
Strength of a combo (model x agent version) = its share of resolved runs over the whole population: descriptive and
in-sample, used only to group the pairs, never as a feature. Every (unresolved, resolved) pair of one task gets
d = strength of the unresolved run's combo - strength of the resolved run's combo (never 0: one run per task x combo,
no two combos of equal strength). Groups:
  weaker        (i)   d < 0: the failure is at the weaker model (by sign; overlaps with (iii)),
  stronger      (ii)  d > 0: the failure is at the stronger model (by sign; overlaps with (iii)),
  weaker_far    (i)   d <= -t: the same, without the pairs of (iii),
  stronger_far  (ii)  d >= +t: the same, without the pairs of (iii),
  similar       (iii) |d| < t.
t = 0.03; for (iii) 0.02 and 0.05 are robustness checks.
AUROC of a group = share of its pairs ordered correctly by the risk (ties 1/2), pooled over tasks. 95% CI: cluster
bootstrap over tasks, the 1000 resamples of src.e7_sources.evaluate (default_rng(42)). For (iii) also the difference
S - M (score "S-M", column auroc holds the difference) with its CI on the same resamples.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.common import OUTPUTS, ROOT, SEED, get_logger
from src.e7_sources import N_BOOT

log = get_logger("e7_addendum")
MAIN_T = 0.03
EXTRA_T = (0.02, 0.05)
SCORES = ["S", "M"]
# (group, threshold); threshold NaN = split by the sign of d
CELLS = [("weaker", np.nan), ("stronger", np.nan), ("weaker_far", MAIN_T), ("stronger_far", MAIN_T),
         ("similar", MAIN_T)] + [("similar", t) for t in EXTRA_T]


def in_group(d: np.ndarray, group: str, t: float) -> np.ndarray:
    return {"weaker": lambda: d < 0, "stronger": lambda: d > 0, "weaker_far": lambda: d <= -t,
            "stronger_far": lambda: d >= t, "similar": lambda: np.abs(d) < t}[group]()


def load() -> pd.DataFrame:
    oof = pd.read_csv(OUTPUTS / "e7_oof_risk.csv.gz")
    oof = oof[oof.population == "all"].reset_index(drop=True)
    runs = pd.read_parquet(ROOT / "data" / "demo" / "runs.parquet", columns=["run_id", "model", "agent_version"])
    n = len(oof)
    pop = oof.merge(runs, on="run_id", how="left")
    assert len(pop) == n == 7864 and pop.model.notna().all()
    assert pop.run_id.is_monotonic_increasing  # the order of src.e7_sources.load_population
    pop["combo"] = pop.model + " | " + pop.agent_version
    pop["strength"] = 1 - pop.groupby("combo").y_unresolved.transform("mean")
    return pop


def group_counts(pop: pd.DataFrame, task_rows: list[np.ndarray]) -> dict:
    """Per task: concordant pairs and number of pairs of every (group, threshold, score)."""
    y, st = pop.y_unresolved.to_numpy(), pop.strength.to_numpy()
    out = {(g, t, s): (np.zeros(len(task_rows)), np.zeros(len(task_rows))) for g, t in CELLS for s in SCORES}
    for i, ix in enumerate(task_rows):
        u, ok = ix[y[ix] == 1], ix[y[ix] == 0]
        if not (len(u) and len(ok)):
            continue
        d = st[u][:, None] - st[ok][None, :]
        assert (d != 0).all()
        conc = {}
        for s in SCORES:
            r = pop[s].to_numpy()
            dr = r[u][:, None] - r[ok][None, :]
            conc[s] = (dr > 0) + 0.5 * (dr == 0)
        for g, t in CELLS:
            mask = in_group(d, g, t)
            for s in SCORES:
                c, n = out[(g, t, s)]
                c[i], n[i] = conc[s][mask].sum(), mask.sum()
    return out


def run() -> pd.DataFrame:
    pop = load()
    groups = pop.task_id.to_numpy()
    task_rows = list(pd.Series(np.arange(len(pop))).groupby(groups).apply(np.array))
    counts = group_counts(pop, task_rows)
    rng = np.random.default_rng(SEED)
    draws = [rng.integers(0, len(task_rows), len(task_rows)) for _ in range(N_BOOT)]
    rows, boot = [], {}
    for (g, t, s), (c, n) in counts.items():
        boot[(g, t, s)] = np.array([c[dr].sum() / n[dr].sum() for dr in draws])
        lo, hi = np.percentile(boot[(g, t, s)], [2.5, 97.5])
        rows.append({"group": g, "threshold": t, "score": s, "n_pairs": int(n.sum()), "n_tasks": int((n > 0).sum()),
                     "auroc": c.sum() / n.sum(), "ci_lo": lo, "ci_hi": hi})
    # (iii): S over the residual strength ordering that M still captures, on the same resamples
    for g, t in CELLS:
        if g == "similar":
            (cs, n), (cm, _) = counts[(g, t, "S")], counts[(g, t, "M")]
            lo, hi = np.percentile(boot[(g, t, "S")] - boot[(g, t, "M")], [2.5, 97.5])
            rows.append({"group": g, "threshold": t, "score": "S-M", "n_pairs": int(n.sum()),
                         "n_tasks": int((n > 0).sum()), "auroc": (cs.sum() - cm.sum()) / n.sum(), "ci_lo": lo,
                         "ci_hi": hi})
    res = pd.DataFrame(rows)
    # all within-task pairs of E7 (reports/E7_REPORT.md, section 4), in both splits
    n_pairs = {(g, t): int(counts[(g, t, "S")][1].sum()) for g, t in CELLS}
    sign = [c for c in CELLS if c[0] in ("weaker", "stronger")]
    part = [c for c in CELLS if c[0] in ("weaker_far", "stronger_far") or c == ("similar", MAIN_T)]
    assert sum(n_pairs[c] for c in sign) == sum(n_pairs[c] for c in part) == 10852, n_pairs
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    res.round(6).to_csv(OUTPUTS / "e7_addendum.csv", index=False)
    st = pop.drop_duplicates("combo").set_index("combo").strength.sort_values()
    log.info("strength of the 16 combos: %s", ", ".join(f"{v:.3f}" for v in st))
    for r in res.itertuples():
        log.info("%-12s t=%.2f %-3s  pairs %5d tasks %3d  AUROC %+.3f [%+.3f; %+.3f]", r.group, r.threshold, r.score,
                 r.n_pairs, r.n_tasks, r.auroc, r.ci_lo, r.ci_hi)
    return res


if __name__ == "__main__":
    run()
