"""E4 smoke test on Mahmoud-queens/swe-agent-subset-selection-vectors (AGENTS.md 2.2 #4).

Precomputed per-STEP nomic embeddings of 6 SWE-bench Verified runs ("multi_model" archive), labels
SUCCESS/FAIL from the SWE-bench harness (results.json). Used ONLY to exercise the E4 statistics code and as
an explicitly labelled step-level control — no conclusions are drawn from it:
* there is no text: the vectors embed sanitised "Action | Result" strings of tool calls, not reasoning;
* series are short (median ~39 steps), i.e. the regime where AGENTS.md 1.1-1.2 says the method is
  indistinguishable from noise, so the window is N = 20 / 30 steps and d = 3 is the main order;
* text-derived trivial baselines cannot be computed (their features.npz is FULL-trajectory -> leaks the
  future and is not used), so only n_steps = n_frag = N exist and M_base is a constant model.

Archive layout: vectors/timeseries/<run>/<SUCCESS|FAIL>_<repo>_<instance_id>_ts.npy, shape (n_steps, 768).
"""
from __future__ import annotations

import re
import tarfile
from pathlib import Path

import numpy as np
import pandas as pd

from src.chaos import chaos_features
from src.common import FEATURE_COLUMNS, REPORTS, get_logger
from src.folds import make_folds
from src.project import cos_step, fit_pca1, norm, pca1

log = get_logger("mahmoud_vectors")

REPO_ID = "Mahmoud-queens/swe-agent-subset-selection-vectors"
ARCHIVE = "multi_model_vectors.tar.gz"
_NAME_RE = re.compile(r"^(SUCCESS|FAIL)_(.+?)_([^_/]+__[^_/]+-\d+)_ts\.npy$")


def download(raw_dir: Path) -> Path:
    from huggingface_hub import hf_hub_download

    raw_dir = Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    return Path(hf_hub_download(REPO_ID, ARCHIVE, repo_type="dataset", local_dir=raw_dir))


def extract(raw_dir: Path) -> Path:
    raw_dir = Path(raw_dir)
    out = raw_dir / "multi_model"
    marker = out / ".extracted"
    if not marker.exists():
        with tarfile.open(raw_dir / ARCHIVE, "r:gz") as tf:
            members = [m for m in tf.getmembers() if "/timeseries/" in m.name and m.name.endswith("_ts.npy")]
            tf.extractall(out, members=members, filter="data")
        marker.write_text("ok")
    return out


def list_series(root: Path) -> pd.DataFrame:
    rows = []
    for p in sorted(root.rglob("*_ts.npy")):
        m = _NAME_RE.match(p.name)
        if not m:
            log.warning("unrecognised file name: %s", p.name)
            continue
        run = p.parent.name
        rows.append({"run_id": f"{run}/{m.group(3)}", "task_id": m.group(3), "agent_id": run, "model_id": run,
                     "model_family": run, "label": 1 if m.group(1) == "FAIL" else 0, "path": str(p)})
    df = pd.DataFrame(rows).drop_duplicates("run_id")
    df["n_steps_total"] = [np.load(p, mmap_mode="r").shape[0] for p in df["path"]]
    return df


def build_smoke(raw_dir: Path, out_dir: Path, Ns=(20, 30), ds=(3, 4), n_boot: int = 1000) -> pd.DataFrame:
    from src.stats import run_stats

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    idx = list_series(extract(raw_dir))
    log.info("smoke: %d trajectories, %d tasks, failure rate %.3f", len(idx), idx.task_id.nunique(), idx.label.mean())
    folds = make_folds(idx["task_id"])
    folds.to_csv(out_dir / "folds.csv", index=False)
    fold_of = dict(zip(folds.task_id, folds.fold))
    idx["fold"] = idx["task_id"].map(fold_of)

    # pca1: one PCA per fold, fitted on steps of trajectories from the other folds only
    series = {r.run_id: np.load(r.path).astype(np.float32) for r in idx.itertuples()}
    pcas = {k: fit_pca1([series[r] for r in idx.loc[idx.fold != k, "run_id"]]) for k in sorted(idx.fold.unique())}

    rows, excl = [], []
    for r in idx.itertuples():
        E = series[r.run_id]
        for N in Ns:
            if len(E) < N:
                continue
            w = E[:N]
            proj = {"cos_step": cos_step(w), "norm": norm(w), "pca1": pca1(w, pcas[r.fold])}
            for pname, s in proj.items():
                for d in ds:
                    rows.append({"run_id": r.run_id, "task_id": r.task_id, "agent_id": r.agent_id,
                                 "model_id": r.model_id, "label": r.label, "seg": "step", "proj": pname,
                                 "N": N, "d": d, **chaos_features(s, d),
                                 "n_steps": N, "n_frag": N, "total_chars": np.nan, "mean_frag_len": np.nan,
                                 "n_tool_calls": np.nan, "repeat_ratio": np.nan})
    for N in Ns:
        for y in (0, 1):
            g = idx[idx.label == y]
            excl.append({"seg": "step", "N": N, "label": y, "n_total": len(g),
                         "n_excluded_short": int((g.n_steps_total < N).sum())})
    feats = pd.DataFrame(rows, columns=FEATURE_COLUMNS)
    feats.to_csv(out_dir / "features.csv", index=False)
    meta = idx.assign(seg="step", n_frag_total=idx.n_steps_total)[
        ["run_id", "task_id", "agent_id", "model_id", "model_family", "label", "seg", "n_frag_total", "n_steps_total"]]
    meta.to_csv(out_dir / "traj_meta.csv", index=False)
    pd.DataFrame(excl).to_csv(out_dir / "window_exclusions.csv", index=False)
    note = ("**Смоук-тест конвейера E4 и шаговый контроль — НЕ результат работы.** Данные: готовые шаговые "
            "эмбеддинги nomic (Mahmoud-queens, архив multi_model: 6 прогонов SWE-bench Verified), текста нет, "
            "окно N = 20/30 шагов (режим, где по AGENTS.md п. 1 метод неотличим от шума). Тривиальные признаки "
            "по тексту недоступны, поэтому M_base — константная модель (AUROC 0.5), а Δ фактически сравнивает "
            "хаос-признаки с нулём. Тест 6 «семейство» здесь = прогон (агент×модель).")
    return run_stats(out_dir / "features.csv", out_dir / "folds.csv", out_dir / "traj_meta.csv", out_dir,
                     REPORTS / "E4_smoke_mahmoud.md", tag="smoke_mahmoud",
                     window_exclusions_csv=out_dir / "window_exclusions.csv", primary=("step", "cos_step", 30, 3),
                     n_boot=n_boot, title_note=note)
