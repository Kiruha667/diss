"""Ordinal-pattern (Bandt-Pompe) features via ordpy: H, C and Fisher information.

Kept separate from features.py so the E4 smoke test (precomputed vectors, no text) can reuse
exactly the same estimator code as the main pipeline.
"""
from __future__ import annotations

import math

import numpy as np
import ordpy


def chaos_features(series: np.ndarray, d: int) -> dict[str, float]:
    """Normalised permutation entropy H, statistical complexity C (the Jensen-Shannon complexity of
    the ordpy complexity-entropy plane, Rosso et al. 2007) and Fisher information of the
    Fisher-Shannon plane, for embedding dimension ``d`` (delay 1).

    Returns NaNs when the series is too short for a single pattern.
    """
    x = np.asarray(series, dtype=np.float64)
    if len(x) < d:
        return {"H": math.nan, "C": math.nan, "fisher": math.nan}
    H, C = ordpy.complexity_entropy(x, dx=d)
    _, F = ordpy.fisher_shannon(x, dx=d)
    return {"H": float(H), "C": float(C), "fisher": float(F)}
