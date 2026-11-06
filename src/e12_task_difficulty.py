"""E12 (reports/E12_PROTOCOL.md): difficulty of the task from its problem statement, for selection. No LLM calls.

  python -m src.e12_task_difficulty  -> outputs/e12_metrics.csv, e12_diffs.csv, e12_oof_risk.csv.gz, e12_summary.json

Population, label, folds, model and cluster bootstrap of E7. T2 = all-MiniLM-L6-v2 embedding of the problem
statement (as E1: max_seq_length 256, normalised), PCA to 32 components fit on the unique tasks of the training fold,
+ one-hot repo. Models T2, T2+M, T2+M+S, M+S (reproduces E7); M itself is taken from the E7 out-of-fold risks.
"""
from __future__ import annotations

import json
import time
import warnings

import numpy as np
import pandas as pd

from src import e7_sources as e7
from src.common import INTERIM, OUTPUTS, SEED, get_logger
from src.e7_sources import FEATURES

log = get_logger("e12")
OUT_DIR = INTERIM / "e12"
EMB_PATH = OUT_DIR / "task_emb.npy"
N_PCA = 32
MODELS = ["T2", "T2+M", "T2+M+S", "M+S", "M"]  # M: E7 out-of-fold risk, not refit
DIFFS = [("T2+M+S", "M+S", True), ("T2+M+S", "T2+M", False), ("T2+M", "M", False)]
EXPECTED_MS = (0.685, 0.689)  # E7: overall and within-task AUROC of M+S


def task_embeddings(tasks: np.ndarray, statements: np.ndarray) -> tuple[np.ndarray, str]:
    from src.embed import _encode, encoder_revision, load_encoder

    rev = encoder_revision("minilm")
    if EMB_PATH.exists():
        emb = np.load(EMB_PATH)
    else:
        emb = _encode(load_encoder("minilm", device="cpu"), list(statements), "minilm", batch_size=32)
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        np.save(EMB_PATH, emb)
        pd.Series(tasks).to_csv(OUT_DIR / "task_order.csv", index=False, header=["task_id"])
    assert emb.shape == (len(tasks), 384)
    return emb, rev


def oof_risks(pop: pd.DataFrame, y: np.ndarray, fold: np.ndarray) -> tuple[dict[str, np.ndarray], dict]:
    from sklearn.decomposition import PCA
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import OneHotEncoder, StandardScaler

    groups = pop.task_id.to_numpy()
    tasks = np.array(sorted(pop.task_id.unique()))
    task_pos = pd.Series(np.arange(len(tasks)), index=tasks)[pop.task_id].to_numpy()
    statements = pop.drop_duplicates("task_id").set_index("task_id").problem_statement.reindex(tasks).to_numpy()
    emb, rev = task_embeddings(tasks, statements)
    risk = {m: np.zeros(len(pop)) for m in MODELS if m != "M"}
    info = {"encoder_revision": rev, "pca_explained_variance": {}, "convergence_warnings": 0}
    spec = {"T2": ["pca", "repo"], "T2+M": ["pca", "repo", "M"], "T2+M+S": ["pca", "repo", "M", "S"],
            "M+S": ["M", "S"]}
    for k in range(5):
        tr, te = fold != k, fold == k
        assert not set(groups[tr]) & set(groups[te])
        train_tasks = np.isin(tasks, np.unique(groups[tr]))
        pca = PCA(n_components=N_PCA, random_state=SEED).fit(emb[train_tasks])
        info["pca_explained_variance"][f"fold{k}"] = round(float(pca.explained_variance_ratio_.sum()), 4)
        oh_m = OneHotEncoder(handle_unknown="ignore", sparse_output=False).fit(pop.loc[tr, ["combo"]])
        oh_r = OneHotEncoder(handle_unknown="ignore", sparse_output=False).fit(pop.loc[tr, ["repo_hf"]])
        blocks = {"pca": pca.transform(emb)[task_pos], "repo": oh_r.transform(pop[["repo_hf"]]),
                  "M": oh_m.transform(pop[["combo"]]), "S": pop[FEATURES].to_numpy(float)}
        for name, bl in spec.items():
            x = np.hstack([blocks[b] for b in bl])
            x = StandardScaler().fit(x[tr]).transform(x)
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                m = LogisticRegression(C=1.0, max_iter=5000).fit(x[tr], y[tr])
                info["convergence_warnings"] += sum("converge" in str(i.message).lower() for i in w)
            risk[name][te] = m.predict_proba(x[te])[:, 1]
        log.info("fold %d done", k)
    e7r = pd.read_csv(OUTPUTS / "e7_oof_risk.csv.gz")
    e7r = e7r[e7r.population == "all"].set_index("run_id")
    risk["M"] = e7r.loc[pop.run_id, "M"].to_numpy(float)
    info["M+S_vs_e7_max_abs_diff"] = float(np.abs(risk["M+S"] - e7r.loc[pop.run_id, "M+S"].to_numpy()).max())
    return risk, info


def evaluate(pop: pd.DataFrame, y: np.ndarray, risk: dict[str, np.ndarray]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """E7 metrics and cluster bootstrap over tasks (1000 draws, seed 42, common to all models and metrics)."""
    task_rows = list(pd.Series(np.arange(len(pop))).groupby(pop.task_id.to_numpy()).apply(np.array))
    counts = {m: e7.pair_counts(y, risk[m], task_rows) for m in MODELS}
    point = {m: e7.metrics_of(y, risk[m], *counts[m]) for m in MODELS}
    rng = np.random.default_rng(SEED)
    draws = [rng.integers(0, len(task_rows), len(task_rows)) for _ in range(e7.N_BOOT)]
    boot = {m: {k: np.zeros(e7.N_BOOT) for k in e7.METRICS} for m in MODELS}
    for b, dr in enumerate(draws):
        ix = np.concatenate([task_rows[i] for i in dr])
        for m in MODELS:
            c, n = counts[m]
            for k, v in e7.metrics_of(y[ix], risk[m][ix], c[dr], n[dr]).items():
                boot[m][k][b] = v
    mrows = [{"model": m, "metric": k, "value": point[m][k], **dict(zip(("ci_lo", "ci_hi"),
                                                                        np.percentile(boot[m][k], [2.5, 97.5])))}
             for m in MODELS for k in e7.METRICS]
    drows = []
    for a, b, primary in DIFFS:
        for k in e7.METRICS:
            lo, hi = np.percentile(boot[a][k] - boot[b][k], [2.5, 97.5])
            drows.append({"model": a, "baseline": b, "metric": k, "diff": point[a][k] - point[b][k],
                          "ci_lo": lo, "ci_hi": hi, "primary": primary and k == "auroc"})
    return pd.DataFrame(mrows), pd.DataFrame(drows)


def run() -> dict:
    t0 = time.time()
    pop = e7.load_population(False)
    y = (1 - pop.resolved).to_numpy()
    fold = e7.folds_of(pop, y)
    risk, info = oof_risks(pop, y, fold)
    task_rows = list(pd.Series(np.arange(len(pop))).groupby(pop.task_id.to_numpy()).apply(np.array))
    c, n = e7.pair_counts(y, risk["M+S"], task_rows)
    got = (round(e7.auroc(y, risk["M+S"]), 3), round(float(c.sum() / n.sum()), 3))
    info["M+S_reproduction"] = got
    if got != EXPECTED_MS:
        raise SystemExit(f"STOP: M+S gives {got}, E7 {EXPECTED_MS}")
    met, dif = evaluate(pop, y, risk)
    met.round(6).to_csv(OUTPUTS / "e12_metrics.csv", index=False)
    dif.round(6).to_csv(OUTPUTS / "e12_diffs.csv", index=False)
    pd.DataFrame({"run_id": pop.run_id, "task_id": pop.task_id, "y_unresolved": y, "fold": fold,
                  **{m: risk[m] for m in MODELS}}).round(8).to_csv(OUTPUTS / "e12_oof_risk.csv.gz", index=False)
    crit = dif[dif.primary].iloc[0]
    info.update({"n_runs": len(pop), "n_tasks": len(task_rows), "seconds": round(time.time() - t0, 1),
                 "criterion": {"diff": float(crit["diff"]), "ci_lo": float(crit.ci_lo), "ci_hi": float(crit.ci_hi),
                               "met": bool(crit.ci_lo > 0)}})
    (OUTPUTS / "e12_summary.json").write_text(json.dumps(info, indent=1, ensure_ascii=False, default=float),
                                              encoding="utf-8")
    return info


if __name__ == "__main__":
    print(json.dumps(run(), indent=1, ensure_ascii=False, default=float))
