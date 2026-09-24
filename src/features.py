"""E2 + E3: projections, Ljung-Box white-noise check and features on fixed windows.

For every trajectory x seg x proj x N x d (AGENTS.md E3):
* chaos features H, C, fisher (src.chaos, ordpy) on the series of the FIRST N fragments;
* trivial baselines computed on the same first N fragments only:
  - n_steps       agent steps started within the window (index of the last step seen + 1);
  - n_frag        == N (constant inside a combo; kept for the fixed schema);
  - total_chars / mean_frag_len  over the N fragments;
  - n_tool_calls  commands executed in steps COMPLETED before the last step seen (the last step may be
                  cut by the window: its command may lie beyond fragment N, so it is not counted);
  - repeat_ratio  share of consecutive identical commands among those calls (loop detector).
Trajectories with fewer than N fragments are excluded for that N and counted by class
(outputs/window_exclusions.csv).

pca1 is out-of-fold: one PCA per task fold, fitted only on fragments of trajectories from the other
folds (src.folds map shared with E4), applied to the held-out fold.

Side outputs: outputs/traj_meta.csv (full-trajectory totals, DIAGNOSTIC ONLY), outputs/e2_ljungbox.csv
and reports/E2_projections.md (share of series indistinguishable from white noise, Ljung-Box lag 1).
"""
from __future__ import annotations

import math
import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from src.chaos import chaos_features
from src.common import (D_GRID, FEATURE_COLUMNS, MAX_N, N_GRID, OUTPUTS, PROJS, REPORTS, SEED, emb_path,
                        get_logger, read_jsonl_gz, segments_path, task_emb_path)
from src.folds import load_folds
from src.project import fit_pca1, ljung_box_p, project

log = get_logger("features")

LB_N = 200          # window used for the E2 white-noise check
LB_ALPHA = 0.05
PCA_ROWS = 200_000  # fragments sampled from training folds to fit each pca1


def load_segments(source: str, seg: str) -> list[dict]:
    """Segment records without the texts (only what the features need)."""
    out = []
    for r in read_jsonl_gz(segments_path(source, seg)):
        out.append({
            "run_id": r["run_id"],
            "task_id": r["task_id"],
            "n_frag_total": r["n_frag_total"],
            "n_steps_total": r["n_steps_total"],
            "lens": np.array([len(f["t"]) for f in r["frags"]], dtype=np.int64),
            "steps": np.array([f["s"] for f in r["frags"]], dtype=np.int64),
            "calls": r["calls"],
        })
    return out


def baseline_features(lens: np.ndarray, steps: np.ndarray, calls: list[list[str]]) -> dict:
    n = len(lens)
    s_last = int(steps[-1])
    seq = [h for s in range(min(s_last, len(calls))) for h in calls[s]]
    reps = sum(1 for a, b in zip(seq, seq[1:]) if a == b)
    return {
        "n_steps": s_last + 1,
        "n_frag": n,
        "total_chars": int(lens.sum()),
        "mean_frag_len": float(lens.mean()),
        "n_tool_calls": len(seq),
        "repeat_ratio": reps / (len(seq) - 1) if len(seq) > 1 else 0.0,
    }


def fit_fold_pcas(items: list[dict], encoder: str, seg: str, fold_of: dict[str, int],
                  n_folds: int) -> dict[int, object]:
    """One PCA per fold k, fitted on a fixed-seed row sample of trajectories NOT in fold k."""
    rng = np.random.default_rng(SEED)
    per_run = max(5, math.ceil(PCA_ROWS * n_folds / max(1, (n_folds - 1) * len(items))))
    samples: dict[int, list[np.ndarray]] = {k: [] for k in range(n_folds)}
    for it in items:
        e = np.load(emb_path(encoder, seg, it["run_id"]))
        idx = rng.choice(len(e), size=min(per_run, len(e)), replace=False)
        samples[fold_of[it["task_id"]]].append(e[np.sort(idx)])
    pcas = {}
    for k in range(n_folds):
        train = [a for j in range(n_folds) if j != k for a in samples[j]]
        pcas[k] = fit_pca1(train, max_rows=PCA_ROWS) if train else None  # no training data -> no pca1
    return pcas


def _featurize(job: dict) -> tuple[list[dict], list[dict]]:
    """Worker: all feature rows (and Ljung-Box rows) of one trajectory for one seg."""
    it, meta = job["item"], job["meta"]
    emb = np.load(job["emb_file"])
    task_vec = np.load(job["task_file"]) if job["task_file"] else None
    rows, lb_rows = [], []
    for N in job["Ns"]:
        if it["n_frag_total"] < N or len(emb) < N:
            continue
        E = emb[:N]
        base = baseline_features(it["lens"][:N], it["steps"][:N], it["calls"])
        series = {}
        for proj in job["projs"]:
            if (proj == "cos_task" and task_vec is None) or (proj == "pca1" and job["pca"] is None):
                continue
            s = project(E, proj, task_vec=task_vec, pca=job["pca"])
            series[proj] = s
            for d in job["ds"]:
                rows.append({**meta, "seg": job["seg"], "proj": proj, "N": N, "d": d,
                             **chaos_features(s, d), **base})
        if N == job["lb_N"]:
            for proj, s in series.items():
                lb = {"run_id": meta["run_id"], "label": meta["label"], "seg": job["seg"], "proj": proj,
                      "N": N, "lb_p": ljung_box_p(s, lag=1), "rho_with_cos_step": math.nan}
                if proj != "cos_step" and "cos_step" in series:
                    a, b = series["cos_step"], s
                    m = min(len(a), len(b))
                    lb["rho_with_cos_step"] = float(spearmanr(a[-m:], b[-m:]).statistic)
                lb_rows.append(lb)
    return rows, lb_rows


def run_features(source: str, segs=None, projs=PROJS, Ns=N_GRID, ds=D_GRID, encoder: str = "minilm",
                 folds_csv: Path | None = None, index_csv: Path | None = None, out_csv: Path | None = None,
                 workers: int | None = None, run_ids: set[str] | None = None) -> pd.DataFrame:
    from src.common import SEGS, N_FOLDS

    segs = segs or SEGS
    folds_csv = folds_csv or OUTPUTS / "folds.csv"
    index_csv = index_csv or OUTPUTS / "traj_index.csv"
    suffix = "" if encoder == "minilm" else f"_{encoder}"
    out_csv = out_csv or OUTPUTS / f"features{suffix}.csv"
    fold_of = load_folds(folds_csv)
    index = pd.read_csv(index_csv, dtype={"run_id": str, "task_id": str}).set_index("run_id")
    workers = workers or os.cpu_count() or 1
    lb_N = LB_N if LB_N in Ns else max(Ns)

    all_rows, all_lb, meta_rows, excl_rows = [], [], [], []
    for seg in segs:
        items = load_segments(source, seg)
        if run_ids is not None:
            items = [it for it in items if it["run_id"] in run_ids]
        missing = [it for it in items if not emb_path(encoder, seg, it["run_id"]).exists()]
        items = [it for it in items if emb_path(encoder, seg, it["run_id"]).exists()]
        if missing:
            log.warning("%s: %d trajectories have no %s embeddings and are skipped", seg, len(missing), encoder)
        if not items:
            continue
        for it in items:
            m = index.loc[it["run_id"]]
            meta_rows.append({"run_id": it["run_id"], "task_id": it["task_id"], "agent_id": m["agent_id"],
                              "model_id": m["model_id"], "model_family": m["model_family"],
                              "label": int(m["label"]), "seg": seg, "n_frag_total": it["n_frag_total"],
                              "n_steps_total": it["n_steps_total"]})
        for N in Ns:
            for y in (0, 1):
                grp = [it for it in items if int(index.loc[it["run_id"], "label"]) == y]
                excl_rows.append({"seg": seg, "N": N, "label": y, "n_total": len(grp),
                                  "n_excluded_short": sum(it["n_frag_total"] < N for it in grp)})
        pcas = fit_fold_pcas(items, encoder, seg, fold_of, N_FOLDS) if "pca1" in projs else {}
        jobs = []
        for it in items:
            m = index.loc[it["run_id"]]
            tf = task_emb_path(encoder, it["task_id"])
            jobs.append({
                "item": it, "seg": seg, "Ns": Ns, "ds": ds, "projs": projs, "lb_N": lb_N,
                "emb_file": str(emb_path(encoder, seg, it["run_id"])),
                "task_file": str(tf) if tf.exists() else None,
                "pca": pcas.get(fold_of[it["task_id"]]),
                "meta": {"run_id": it["run_id"], "task_id": it["task_id"], "agent_id": m["agent_id"],
                         "model_id": m["model_id"], "label": int(m["label"])},
            })
        log.info("%s: featurising %d trajectories with %d workers", seg, len(jobs), workers)
        if workers > 1:
            with ProcessPoolExecutor(max_workers=workers) as ex:
                results = list(ex.map(_featurize, jobs, chunksize=16))
        else:
            results = [_featurize(j) for j in jobs]
        for rows, lb in results:
            all_rows.extend(rows)
            all_lb.extend(lb)

    feats = pd.DataFrame(all_rows, columns=FEATURE_COLUMNS)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    feats.to_csv(out_csv, index=False)
    pd.DataFrame(meta_rows).to_csv(OUTPUTS / f"traj_meta{suffix}.csv", index=False)
    pd.DataFrame(excl_rows).to_csv(OUTPUTS / f"window_exclusions{suffix}.csv", index=False)
    lb = pd.DataFrame(all_lb)
    lb.to_csv(OUTPUTS / f"e2_ljungbox{suffix}.csv", index=False)
    write_e2_report(lb, REPORTS / f"E2_projections{suffix}.md", encoder)
    log.info("wrote %d feature rows -> %s", len(feats), out_csv)
    return feats


def write_e2_report(lb: pd.DataFrame, path: Path, encoder: str) -> None:
    lines = [f"# E2 — проекции: проверка на белый шум (энкодер {encoder})", "",
             f"Ljung–Box, лаг 1, на первых {LB_N} фрагментах (ряд длины {LB_N - 1} для cos_step/norm, "
             f"{LB_N} для cos_task/pca1). «Неотличим от белого шума» = p > {LB_ALPHA}.",
             "Критерий остановки (AGENTS.md E2): если для ВСЕХ проекций доля > 90% — сигнала в динамике нет.", ""]
    if lb.empty:
        lines.append("Нет рядов нужной длины.")
    else:
        g = lb.dropna(subset=["lb_p"]).groupby(["seg", "proj"])
        tab = g.agg(n=("lb_p", "size"), share_white=("lb_p", lambda p: float((p > LB_ALPHA).mean())),
                    median_p=("lb_p", "median")).reset_index()
        by_class = lb.dropna(subset=["lb_p"]).groupby(["seg", "proj", "label"])["lb_p"].apply(
            lambda p: float((p > LB_ALPHA).mean())).unstack("label")
        lines += ["| seg | proj | n рядов | доля «белый шум» | медиана p | доля (успех) | доля (провал) |",
                  "|---|---|---|---|---|---|---|"]
        for _, r in tab.iterrows():
            bc = by_class.loc[(r["seg"], r["proj"])] if (r["seg"], r["proj"]) in by_class.index else {}
            lines.append(f"| {r['seg']} | {r['proj']} | {r['n']} | {r['share_white']:.3f} | {r['median_p']:.3g} | "
                         f"{bc.get(0, float('nan')):.3f} | {bc.get(1, float('nan')):.3f} |")
        lines.append("")
        for seg, t in tab.groupby("seg"):
            stop = bool((t["share_white"] > 0.9).all())
            lines.append(f"- **{seg}**: " + ("все проекции > 90% белого шума — критерий остановки СРАБОТАЛ."
                                             if stop else "критерий остановки не сработал (есть проекция с долей ≤ 90%)."))
        rho = lb.loc[lb["proj"] == "norm", "rho_with_cos_step"].dropna()
        if len(rho):
            lines += ["", f"Медианная ранговая корреляция рядов norm и cos_step: {rho.median():.4f} "
                          "(эмбеддинги единичной нормы ⇒ norm = sqrt(2·cos_step), монотонное преобразование; "
                          "порядковые признаки H, C, Fisher у них совпадают, это не независимая проверка)."]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
