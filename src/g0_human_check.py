"""Human check of the G0 claim detector (reports/G0_HUMAN_CHECK_PROTOCOL.md). No API calls.

  python -m src.g0_human_check sample   -> outputs/g0_human_sample.csv (to mark), outputs/g0_human_key.csv (key)
  python -m src.g0_human_check analyze  -> outputs/g0_human_results.json (after the author's marks)

Population: the 7864 submitted leaderboard runs; final message = full G0 text (e7_texts final_full); detector =
has_claim / has_admit of outputs/g0_final_messages.csv. Sample: 25 per cell (detector claim x resolved), seed 47,
without the runs of outputs/g0_manual_sample.csv. Estimates: precision, recall and the true claim share per class
with the cell weights; stratified bootstrap (1000, seed 42). Section 8 of the protocol: the author's claim_level
(2 explicit result, 1 approval only, 0 none) gives a strict (= 2) and a broad (>= 1) claim; agreement = p_o, Cohen's
kappa, PABAK.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

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
AI_LABELS = OUTPUTS / "g0_ai_labels.csv"
INDEPENDENT = OUTPUTS / "g0_human_independent.csv"
VARIANTS = {"strict": lambda lv: lv == 2, "broad": lambda lv: lv >= 1}  # protocol section 8.3
MIN_POS_KAPPA = 5  # protocol 8.4: admit kappa is not interpreted below this number of positives


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
    pd.DataFrame({"id": s.id, "final_message": texts, "claim": "", "claim_level": "", "admit": "", "unsure": "",
                  "comment": ""}).to_csv(SAMPLE, index=False, encoding="utf-8-sig")
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


def agreement(a, b, w=None, min_pos: int | None = None) -> dict:
    """Protocol 8.4: share of agreement p_o, Cohen's kappa and PABAK = 2 p_o - 1 of two binary raters (optionally
    weighted); with min_pos, kappa is marked not interpretable when either rater has fewer positives (unweighted)."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    w = np.ones(len(a)) if w is None else np.asarray(w, float)
    w = w / w.sum()
    po = float((w * (a == b)).sum())
    pa, pb = float((w * a).sum()), float((w * b).sum())
    pe = pa * pb + (1 - pa) * (1 - pb)
    out = {"n": len(a), "p_o": po, "kappa": (po - pe) / (1 - pe) if pe < 1 else float("nan"), "pabak": 2 * po - 1,
           "positives": [int(a.sum()), int(b.sum())]}
    if min_pos is not None:
        out["kappa_interpretable"] = bool(min(a.sum(), b.sum()) >= min_pos)
    return out


def _weights(dd: pd.DataFrame, n: dict) -> pd.Series:
    """Cell weights N / n of the sample rows (section 5)."""
    size = dd.groupby(["det_claim", "resolved"]).id.transform("size")
    return pd.Series([n[(c, r)] for c, r in zip(dd.det_claim, dd.resolved)], index=dd.index) / size


def _conclusion(ci: dict) -> str:
    """Protocol 8.3: one sentence from the CIs of the difference in both variants."""
    zero = {v: c["ci_lo"] <= 0 <= c["ci_hi"] for v, c in ci.items()}
    if all(zero.values()):
        return ("вывод устойчив: различие долей заявлений у решённых и нерешённых после поправки не показано "
                "ни в строгом, ни в широком варианте")
    sides = {c["ci_lo"] > 0 for c in ci.values()}
    if not any(zero.values()) and len(sides) == 1:
        return f"вывод не остаётся: у {'решённых' if sides.pop() else 'нерешённых'} заявлений больше (в обоих вариантах)"
    return "вывод зависит от границы заявления (уровни 1 и 2)"


def analyze(sample_path: Path | None = None, results_path: Path | None = None) -> dict:
    """Protocol sections 5, 7 and 8: claim_level -> strict (= 2) and broad (>= 1) claims; detector, model and human
    agreement; the claim column of the sample is not used."""
    pop = population()
    n = {(c, r): int(((pop.det_claim == c) & (pop.resolved == r)).sum()) for c, r in CELLS}
    sample_path = Path(sample_path or SAMPLE)
    marks = pd.read_csv(sample_path, encoding="utf-8-sig")
    if "claim_level" not in marks:
        raise SystemExit(f"{sample_path} has no claim_level column (protocol section 8)")
    key = pd.read_csv(KEY)
    d = key.merge(marks[["id", "claim_level", "admit", "unsure", "comment"]], on="id")
    for c in ("claim_level", "admit", "unsure"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    has = d.claim_level.notna()
    for c in ("admit", "unsure"):  # a blank in a marked row is 0 (protocol 8.2)
        d.loc[has & d[c].isna(), c] = 0
    for v, f in VARIANTS.items():
        d[v] = np.where(has, f(d.claim_level.fillna(-1)).astype(float), np.nan)
    res = {"population_cells": {f"claim={c}|resolved={r}": v for (c, r), v in n.items()},
           "rows_with_claim_level": int(has.sum()), "rows_without_claim_level": int((~has).sum()),
           "claim_level_counts": {int(k): int(v) for k, v in d.claim_level.value_counts().items()},
           "unsure": int((d.unsure == 1).sum())}
    for v in VARIANTS:
        for subset, keep in (("all", has), ("without_unsure", has & (d.unsure != 1))):
            dd = d[keep]
            cells = {(c, r): dd[(dd.det_claim == c) & (dd.resolved == r)] for c, r in CELLS}
            res[f"claim_{v}_{subset}"] = {"n_rows": len(dd), "point": _estimates(cells, n, v),
                                          "ci": _boot(cells, n, v),
                                          "cell_rows": {f"claim={c}|resolved={r}": len(x) for (c, r), x in cells.items()}}
        dd = d[has]
        res[f"detector_agreement_{v}"] = agreement(dd[v], dd.det_claim, _weights(dd, n))
    res["conclusion_by_variant"] = {v: res[f"claim_{v}_all"]["ci"]["diff"] for v in VARIANTS}
    res["conclusion"] = _conclusion(res["conclusion_by_variant"])
    da = d[d.admit.notna()]
    cells_a = {(c, r): da[(da.det_claim == c) & (da.resolved == r)] for c, r in CELLS}
    est_a = _estimates(cells_a, n, "admit")
    res["admit"] = {"n_rows": len(da), "true_share_resolved": est_a[1]["true_share"],
                    "true_share_unresolved": est_a[0]["true_share"],
                    "diff": est_a["diff_true_share_resolved_minus_unresolved"],
                    "ci": {k: v for k, v in _boot(cells_a, n, "admit").items() if k.startswith(("true_share", "diff"))},
                    "detector": _admit_detector(da, _weights(da, n)),
                    "detector_agreement": agreement(da.admit, da.det_admit, _weights(da, n), MIN_POS_KAPPA)}
    if AI_LABELS.exists():  # model pre-labels against the author's final and independent labels (protocol 7, 8.4)
        ai = pd.read_csv(AI_LABELS, encoding="utf-8-sig")
        m = d.merge(ai, on="id")
        mm, ma = m[m.claim_level.notna()], m[m.admit.notna()]
        res["model_vs_final"] = {v: agreement(VARIANTS[v](mm.ai_claim_level).astype(int), mm[v]) for v in VARIANTS}
        res["model_vs_final"]["admit"] = agreement(ma.ai_admit, ma.admit, None, MIN_POS_KAPPA)
        if INDEPENDENT.exists():
            ind = pd.read_csv(INDEPENDENT, encoding="utf-8-sig")
            for c in ("claim", "admit"):
                ind[c] = pd.to_numeric(ind[c], errors="coerce").fillna(0)
            mi = ind.merge(ai, on="id")
            res["model_vs_independent"] = {v: agreement(VARIANTS[v](mi.ai_claim_level).astype(int), mi.claim)
                                           for v in VARIANTS}
            res["model_vs_independent"]["ai_claim_binary"] = agreement(mi.ai_claim, mi.claim)
            res["model_vs_independent"]["admit"] = agreement(mi.ai_admit, mi.admit, None, MIN_POS_KAPPA)
    res["labels_file"] = sample_path.name
    Path(results_path or RESULTS).write_text(json.dumps(res, indent=1, ensure_ascii=False, default=float),
                                             encoding="utf-8")
    return res


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "sample"
    if cmd == "sample":
        s = sample()
        print(s.groupby("cell").size(), "long:", int((s.final_full.str.len() > LONG_LIMIT).sum()))
    elif cmd == "analyze":  # optional: <labels csv> <results json> (protocol 9: the check with the author's labels)
        args = [OUTPUTS / a if "/" not in a and "\\" not in a else Path(a) for a in sys.argv[2:4]]
        print(json.dumps(analyze(*args), indent=1, ensure_ascii=False, default=float))
