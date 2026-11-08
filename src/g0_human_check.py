"""Human check of the G0 claim detector (reports/G0_HUMAN_CHECK_PROTOCOL.md). No API calls.

  python -m src.g0_human_check sample   -> outputs/g0_human_sample.csv (to mark), outputs/g0_human_key.csv (key)
  python -m src.g0_human_check analyze  -> outputs/g0_human_results.json (after the author's marks)

Population: the 7864 submitted leaderboard runs; final message = full G0 text (e7_texts final_full); detector =
has_claim / has_admit of outputs/g0_final_messages.csv. Sample: 25 per cell (detector claim x resolved), seed 47,
without the runs of outputs/g0_manual_sample.csv. Estimates: precision, recall and the true claim share per class
with the cell weights; stratified bootstrap (1000, seed 42).
"""
from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd

from src.common import INTERIM, OUTPUTS, ROOT, SEED

SAMPLE = OUTPUTS / "g0_human_sample.csv"
KEY = OUTPUTS / "g0_human_key.csv"
LONG_DIR = OUTPUTS / "g0_human_long"
RESULTS = OUTPUTS / "g0_human_results.json"
SAMPLE_SEED = 47
PER_CELL = 25
CELLS = [(1, 1), (1, 0), (0, 1), (0, 0)]  # (detector claim, resolved)
LONG_LIMIT, LONG_KEEP = 32_000, 30_000
N_BOOT = 1000


def population() -> pd.DataFrame:
    r = pd.read_parquet(ROOT / "data" / "demo" / "runs.parquet")
    r = r[r.submitted == 1][["run_id", "model", "task_id", "resolved"]]
    g = pd.read_csv(OUTPUTS / "g0_final_messages.csv", usecols=["run_id", "has_claim", "has_admit"])
    t = pd.read_parquet(INTERIM / "swebench_bashonly" / "e7_texts.parquet", columns=["run_id", "final_full"])
    pop = r.merge(g, on="run_id").merge(t, on="run_id")
    assert len(pop) == 7864 and (pop.final_full.str.len() > 0).all()
    pop["det_claim"], pop["det_admit"] = pop.has_claim.astype(int), pop.has_admit.astype(int)
    return pop.sort_values("run_id").reset_index(drop=True)


def sample() -> pd.DataFrame:
    if SAMPLE.exists() or KEY.exists():
        raise SystemExit("the sample already exists; it is drawn once")
    pop = population()
    prior = set(pd.read_csv(OUTPUTS / "g0_manual_sample.csv").run_id)
    frame = pop[~pop.run_id.isin(prior)]
    rng = np.random.default_rng(SAMPLE_SEED)
    picks = []
    for c, r in CELLS:
        cand = frame[(frame.det_claim == c) & (frame.resolved == r)].sort_values("run_id")
        picks.append(cand.iloc[sorted(rng.choice(len(cand), PER_CELL, replace=False))].assign(
            cell=f"claim={c}|resolved={r}"))
    s = pd.concat(picks, ignore_index=True)
    s["id"] = rng.permutation(len(s)) + 1
    s = s.sort_values("id").reset_index(drop=True)
    texts = []
    for i, txt in zip(s.id, s.final_full):
        if len(txt) > LONG_LIMIT:
            LONG_DIR.mkdir(parents=True, exist_ok=True)
            (LONG_DIR / f"{i}.txt").write_text(txt, encoding="utf-8")
            txt = txt[:LONG_KEEP] + f"\n\n[УСЕЧЕНО: полный текст в outputs/g0_human_long/{i}.txt]"
        texts.append(txt)
    pd.DataFrame({"id": s.id, "final_message": texts, "claim": "", "admit": "", "unsure": "", "comment": ""}).to_csv(
        SAMPLE, index=False, encoding="utf-8-sig")
    s[["id", "run_id", "cell", "det_claim", "det_admit", "resolved", "model", "task_id"]].to_csv(KEY, index=False)
    return s


# ------------------------------------------------------------------------------------------ after the marks

def _estimates(cells: dict, n: dict, col: str) -> dict:
    """Per class: precision of the detector (det=1 cell), recall and true share with the cell weights."""
    out = {}
    for r in (1, 0):
        p, q = cells[(1, r)][col].mean(), cells[(0, r)][col].mean()
        tp, fn = n[(1, r)] * p, n[(0, r)] * q
        out[r] = {"precision": p, "missed_share_in_det0": q, "recall": tp / (tp + fn) if tp + fn > 0 else np.nan,
                  "true_share": (tp + fn) / (n[(1, r)] + n[(0, r)])}
    out["diff_true_share_resolved_minus_unresolved"] = out[1]["true_share"] - out[0]["true_share"]
    return out


def _boot(cells: dict, n: dict, col: str) -> dict:
    rng = np.random.default_rng(SEED)
    keys = ["precision_1", "recall_1", "true_share_1", "precision_0", "recall_0", "true_share_0", "diff"]
    vals = {k: [] for k in keys}
    for _ in range(N_BOOT):
        b = {c: d.iloc[rng.integers(0, len(d), len(d))] for c, d in cells.items()}
        e = _estimates(b, n, col)
        for r in (1, 0):
            for m in ("precision", "recall", "true_share"):
                vals[f"{m}_{r}"].append(e[r][m])
        vals["diff"].append(e["diff_true_share_resolved_minus_unresolved"])
    out = {}
    for k, v in vals.items():
        v = np.array(v, float)
        ok = ~np.isnan(v)
        lo, hi = np.percentile(v[ok], [2.5, 97.5]) if ok.any() else (np.nan, np.nan)
        out[k] = {"ci_lo": float(lo), "ci_hi": float(hi), "undefined_draws": int((~ok).sum())}
    return out


def _admit_detector(d: pd.DataFrame, w: pd.Series) -> dict:
    """Weighted precision / recall of the admit detector (descriptive; only if detector-positive rows are sampled)."""
    pos = d.det_admit == 1
    if not pos.any():
        return {"estimable": False, "detector_positive_in_sample": 0}
    tp = (w * ((d.admit == 1) & pos)).sum()
    return {"estimable": True, "detector_positive_in_sample": int(pos.sum()),
            "precision_weighted": float(tp / (w * pos).sum()),
            "recall_weighted": float(tp / (w * (d.admit == 1)).sum()) if (d.admit == 1).any() else None}


def analyze() -> dict:
    pop = population()
    n = {(c, r): int(((pop.det_claim == c) & (pop.resolved == r)).sum()) for c, r in CELLS}
    marks = pd.read_csv(SAMPLE, encoding="utf-8-sig")
    key = pd.read_csv(KEY)
    d = key.merge(marks[["id", "claim", "admit", "unsure", "comment"]], on="id")
    for c in ("claim", "admit", "unsure"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    res = {"population_cells": {f"claim={c}|resolved={r}": v for (c, r), v in n.items()},
           "marked_claim": int(d.claim.notna().sum()), "unmarked_claim": int(d.claim.isna().sum()),
           "unsure": int((d.unsure == 1).sum())}
    for variant, keep in (("all", d.claim.notna()), ("without_unsure", d.claim.notna() & (d.unsure != 1))):
        dd = d[keep]
        cells = {(c, r): dd[(dd.det_claim == c) & (dd.resolved == r)] for c, r in CELLS}
        est = _estimates(cells, n, "claim")
        ci = _boot(cells, n, "claim")
        res[f"claim_{variant}"] = {"n_rows": len(dd), "point": est, "ci": ci,
                                   "cell_rows": {f"claim={c}|resolved={r}": len(v) for (c, r), v in cells.items()}}
    diff = res["claim_all"]["ci"]["diff"]
    res["conclusion"] = ("вывод остаётся: различие долей заявлений после поправки не показано"
                         if diff["ci_lo"] <= 0 <= diff["ci_hi"] else
                         "не остаётся: у решённых заявлений больше" if diff["ci_lo"] > 0 else
                         "не остаётся: у нерешённых заявлений больше")
    da = d[d.admit.notna()]
    cells_a = {(c, r): da[(da.det_claim == c) & (da.resolved == r)] for c, r in CELLS}
    w = da.apply(lambda x: n[(x.det_claim, x.resolved)] / len(cells_a[(x.det_claim, x.resolved)]), axis=1)
    est_a = _estimates(cells_a, n, "admit")
    res["admit"] = {"n_rows": len(da), "true_share_resolved": est_a[1]["true_share"],
                    "true_share_unresolved": est_a[0]["true_share"],
                    "diff": est_a["diff_true_share_resolved_minus_unresolved"],
                    "ci": {k: v for k, v in _boot(cells_a, n, "admit").items() if k.startswith(("true_share", "diff"))},
                    "detector": _admit_detector(da, w)}
    dc = d[d.claim.notna()]
    wc = dc.apply(lambda x: n[(x.det_claim, x.resolved)] / len(dc[(dc.det_claim == x.det_claim) &
                                                                   (dc.resolved == x.resolved)]), axis=1)
    res["claim_agreement_weighted"] = float((wc * (dc.claim == dc.det_claim)).sum() / wc.sum())
    RESULTS.write_text(json.dumps(res, indent=1, ensure_ascii=False, default=float), encoding="utf-8")
    return res


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "sample"
    if cmd == "sample":
        s = sample()
        print(s.groupby("cell").size(), "long:", int((s.final_full.str.len() > LONG_LIMIT).sum()))
    elif cmd == "analyze":
        print(json.dumps(analyze(), indent=1, ensure_ascii=False, default=float))
