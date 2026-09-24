"""One fixed task -> fold assignment shared by every stage (E3 pca1 and all E4 tests).

AGENTS.md rule 2: every split is grouped by task_id. The assignment is computed once, over all
kept trajectories, with GroupKFold(shuffle, seed=42) so folds are balanced in size and not ordered by
repository name. Using the same map everywhere keeps out-of-fold pca1 features consistent with the
cross-validation that evaluates them.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
from sklearn.model_selection import GroupKFold

from src.common import N_FOLDS, SEED


def make_folds(task_ids: list[str] | pd.Series, n_splits: int = N_FOLDS, seed: int = SEED) -> pd.DataFrame:
    """Return a DataFrame(task_id, fold) from one row per trajectory's task_id."""
    groups = pd.Series(list(task_ids), name="task_id").reset_index(drop=True)
    gkf = GroupKFold(n_splits=n_splits, shuffle=True, random_state=seed)
    fold_of = {}
    for k, (_, test_idx) in enumerate(gkf.split(groups, groups=groups)):
        for t in groups.iloc[test_idx].unique():
            fold_of[t] = k
    out = pd.DataFrame({"task_id": list(fold_of), "fold": list(fold_of.values())})
    return out.sort_values("task_id").reset_index(drop=True)


def load_folds(path: Path) -> dict[str, int]:
    df = pd.read_csv(path)
    return dict(zip(df["task_id"].astype(str), df["fold"].astype(int)))
