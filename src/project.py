"""E2: projection of a trajectory (T, D) into a 1-D series.

All four projections from AGENTS.md E2 are implemented; none is chosen in advance.
Difference-based projections (cos_step, norm) turn T fragments into T-1 points; point-wise ones
(cos_task, pca1) keep T points. Callers always pass exactly the first N fragments, so every series
of a given (proj, N) has the same length.

pca1 is fitted on training folds only (see ``fit_pca1``); the value used for a trajectory is the
out-of-fold projection, i.e. from a PCA that never saw that trajectory's task.
"""
from __future__ import annotations

import numpy as np
from sklearn.decomposition import PCA

from src.common import SEED

PROJS_NEED_TASK = {"cos_task"}
PROJS_NEED_FIT = {"pca1"}


def _unit(x: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(x, axis=-1, keepdims=True)
    return x / np.maximum(n, 1e-12)


def cos_step(emb: np.ndarray) -> np.ndarray:
    """Cosine distance between consecutive fragments (speed of semantic motion). (T,D) -> (T-1,)."""
    u = _unit(emb.astype(np.float64))
    return 1.0 - np.sum(u[1:] * u[:-1], axis=1)


def norm(emb: np.ndarray) -> np.ndarray:
    """Euclidean norm of consecutive differences (crude control). (T,D) -> (T-1,)."""
    e = emb.astype(np.float64)
    return np.linalg.norm(e[1:] - e[:-1], axis=1)


def cos_task(emb: np.ndarray, task_vec: np.ndarray) -> np.ndarray:
    """Cosine distance of each fragment to the task-statement embedding. (T,D) -> (T,)."""
    u = _unit(emb.astype(np.float64))
    t = _unit(task_vec.astype(np.float64).reshape(1, -1))[0]
    return 1.0 - u @ t


def fit_pca1(train_embs: list[np.ndarray], max_rows: int = 200_000, seed: int = SEED) -> PCA:
    """Fit the first principal component on fragments of TRAINING trajectories only.

    Fragments are subsampled (without replacement, fixed seed) to at most ``max_rows`` rows.
    """
    X = np.concatenate(train_embs, axis=0).astype(np.float64)
    if len(X) > max_rows:
        rng = np.random.default_rng(seed)
        X = X[rng.choice(len(X), size=max_rows, replace=False)]
    return PCA(n_components=1, random_state=seed).fit(X)


def pca1(emb: np.ndarray, pca: PCA) -> np.ndarray:
    """Projection on the first principal component. (T,D) -> (T,).

    The component's sign is arbitrary; ordinal-pattern features (H, C, Fisher) are invariant to
    reversing the series' sign, so this does not matter downstream.
    """
    return pca.transform(emb.astype(np.float64))[:, 0]


def project(emb: np.ndarray, proj: str, task_vec: np.ndarray | None = None, pca: PCA | None = None) -> np.ndarray:
    if proj == "cos_step":
        return cos_step(emb)
    if proj == "norm":
        return norm(emb)
    if proj == "cos_task":
        if task_vec is None:
            raise ValueError("cos_task needs the task embedding")
        return cos_task(emb, task_vec)
    if proj == "pca1":
        if pca is None:
            raise ValueError("pca1 needs a PCA fitted on the training folds")
        return pca1(emb, pca)
    raise ValueError(f"unknown projection {proj!r}")


def ljung_box_p(series: np.ndarray, lag: int = 1) -> float:
    """Ljung-Box p-value for autocorrelation up to ``lag`` (E2 white-noise check)."""
    from statsmodels.stats.diagnostic import acorr_ljungbox

    x = np.asarray(series, dtype=np.float64)
    if len(x) <= lag + 1 or np.allclose(x, x[0]):
        return float("nan")
    res = acorr_ljungbox(x, lags=[lag], return_df=True)
    return float(res["lb_pvalue"].iloc[0])
