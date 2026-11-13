"""E7 (reports/E7_PROTOCOL.md): decomposition of the failure signal by information source. No LLM calls.

  python -m src.e7_sources data  -> data/raw/swebench_verified/tasks.parquet (instance_id, repo, problem_statement only),
                                    data/interim/swebench_bashonly/e7_texts.parquet (run_id, final message, reasoning)
  python -m src.e7_sources run   -> outputs/e7_metrics.csv, outputs/e7_diffs.csv, outputs/e7_oof_risk.csv.gz,
                                    outputs/e7_summary.json

Blocks: T = TF-IDF of the problem statement (fit on the unique tasks of the training fold) + one-hot repo;
M = one-hot model x agent version; S = the 21 M_struct+len features (demo_lib FEATURES); A = TF-IDF of the final
message; R = TF-IDF of all reasoning (both fit on the training fold). Dense blocks are standardised, TF-IDF blocks are
not (L2-normalised rows). LogisticRegression(C=1, max_iter=5000), GroupKFold(5, shuffle, seed 42) by task: the folds
of notebooks/demo.ipynb. Cluster bootstrap over tasks, 1000 resamples common to all models and metrics.
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import sparse
from scipy.stats import rankdata

from src import g0_false_success as g0
from src.common import INTERIM, OUTPUTS, RAW, ROOT, SEED, get_logger, read_jsonl_gz, trajectories_path

sys.path.insert(0, str(ROOT))
from demo_lib.features import FEATURES  # noqa: E402

log = get_logger("e7")
HF_REPO = "princeton-nlp/SWE-bench_Verified"
TASKS_PATH = RAW / "swebench_verified" / "tasks.parquet"
TEXTS_PATH = INTERIM / "swebench_bashonly" / "e7_texts.parquet"
MODELS = {"S": "S", "M": "M", "T": "T", "T+M": "TM", "T+M+S": "TMS", "T+M+S+A": "TMSA", "T+M+S+R": "TMSR",
          "M+S": "MS"}
KS = (0.05, 0.10, 0.20)
METRICS = ["auroc", "within", "brier", "p@5", "p@10", "p@20"]
# (question, model, baseline, metric, primary)
DIFFS = [("б", "M+S", "M", "within", True), ("в", "T+M+S", "T+M", "auroc", True),
         ("г-A", "T+M+S+A", "T+M+S", "auroc", True), ("г-R", "T+M+S+R", "T+M+S", "auroc", True)]
DIFF_PAIRS = [("б", "M+S", "M"), ("в", "T+M+S", "T+M"), ("г-A", "T+M+S+A", "T+M+S"), ("г-R", "T+M+S+R", "T+M+S")]
N_BOOT = 1000
EXPECTED_S = (0.662, 0.640)  # DEMO_REPORT.md: AUROC and within-task AUROC of M_struct+len


# ------------------------------------------------------------------------------------------ data

def fetch_tasks() -> dict:
    """SWE-bench Verified: only instance_id, repo, problem_statement are read (no patch, no tests, no hints)."""
    from huggingface_hub import HfApi, hf_hub_download

    api = HfApi()
    info = api.dataset_info(HF_REPO)
    files = [f for f in api.list_repo_files(HF_REPO, repo_type="dataset") if f.endswith(".parquet") and "test" in f]
    assert len(files) == 1, files
    path = Path(hf_hub_download(HF_REPO, files[0], repo_type="dataset", revision=info.sha,
                                local_dir=RAW / "swebench_verified"))
    df = pd.read_parquet(path, columns=["instance_id", "repo", "problem_statement"])
    TASKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(TASKS_PATH, index=False)
    meta = {"hf_repo": HF_REPO, "revision": info.sha, "file": files[0],
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "rows": len(df),
            "columns_read": ["instance_id", "repo", "problem_statement"]}
    (TASKS_PATH.parent / "tasks_meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    log.info("SWE-bench Verified: %d tasks, revision %s", len(df), info.sha)
    return meta


def extract_texts() -> pd.DataFrame:
    """Full final message (G0 clean_nl + pick_final, no truncation) and all reasoning of every trajectory."""
    rows = []
    for rec in read_jsonl_gz(trajectories_path("swebench_bashonly")):
        asst = [s for s in rec["steps"] if s["role"] == "assistant"]
        texts = [g0.clean_nl(s["text"]) for s in asst]
        _, final = g0.pick_final([{"full": t} for t in texts], "full")
        rows.append({"run_id": rec["run_id"], "final_full": final, "reasoning_full": "\n".join(texts)})
    df = pd.DataFrame(rows)
    TEXTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(TEXTS_PATH, index=False)
    log.info("texts: %d runs, final %.1f MB, reasoning %.1f MB", len(df), df.final_full.str.len().sum() / 1e6,
             df.reasoning_full.str.len().sum() / 1e6)
    return df


# ------------------------------------------------------------------------------------------ population

def load_population(drop_requests: bool) -> pd.DataFrame:
    runs = pd.read_parquet(ROOT / "data" / "demo" / "runs.parquet")
    pop = runs[runs.submitted == 1].sort_values("run_id").reset_index(drop=True)
    if drop_requests:
        pop = pop[~pop.task_id.str.startswith("psf__requests")].reset_index(drop=True)
    tasks = pd.read_parquet(TASKS_PATH).rename(columns={"instance_id": "task_id", "repo": "repo_hf"})
    texts = pd.read_parquet(TEXTS_PATH)
    n = len(pop)
    pop = pop.merge(tasks, on="task_id", how="left").merge(texts, on="run_id", how="left")
    assert len(pop) == n and pop.problem_statement.notna().all(), "task missing in SWE-bench Verified"
    assert pop.final_full.notna().all() and pop.reasoning_full.notna().all(), "texts missing"
    # the extracted final message must be the untruncated version of the demo one
    assert (pop.final_full.str.slice(0, 1000) == pop.final_message.fillna("")).all(), "final message mismatch"
    pop["combo"] = pop.model + " | " + pop.agent_version
    return pop


# ------------------------------------------------------------------------------------------ models

def folds_of(pop: pd.DataFrame, y: np.ndarray) -> np.ndarray:
    from sklearn.model_selection import GroupKFold

    fold = np.full(len(pop), -1)
    for k, (_, te) in enumerate(GroupKFold(n_splits=5, shuffle=True, random_state=SEED)
                                .split(pop, y, pop.task_id.to_numpy())):
        fold[te] = k
    return fold


def tfidf():
    from sklearn.feature_extraction.text import TfidfVectorizer

    return TfidfVectorizer(ngram_range=(1, 2), max_features=5000)


SPEC = {"S": (["S"], []), "M": (["M"], []), "T": (["repo"], ["T"]), "T+M": (["repo", "M"], ["T"]),
        "T+M+S": (["repo", "M", "S"], ["T"]), "T+M+S+A": (["repo", "M", "S"], ["T", "A"]),
        "T+M+S+R": (["repo", "M", "S"], ["T", "R"]), "M+S": (["M", "S"], [])}  # name -> (dense blocks, TF-IDF blocks)


def oof_risks(pop: pd.DataFrame, y: np.ndarray, fold: np.ndarray,
              spec: dict | None = None) -> tuple[dict[str, np.ndarray], dict]:
    """Out-of-fold risks of the models in `spec` (default: the protocol's SPEC; src.paper_population adds M+S+A and
    M+S+R)."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import OneHotEncoder, StandardScaler

    spec = SPEC if spec is None else spec
    risk = {m: np.zeros(len(pop)) for m in spec}
    groups = pop.task_id.to_numpy()
    tasks = np.array(sorted(pop.task_id.unique()))
    task_pos = pd.Series(np.arange(len(tasks)), index=tasks)[pop.task_id].to_numpy()
    statements = pop.drop_duplicates("task_id").set_index("task_id").problem_statement.reindex(tasks).to_numpy()
    n_warn, timing = 0, {}
    for k in range(5):
        t0 = time.time()
        tr, te = fold != k, fold == k
        assert not set(groups[tr]) & set(groups[te])
        # dense blocks: S, one-hot M, one-hot repo (encoders on the training fold), standardised on it
        oh_m = OneHotEncoder(handle_unknown="ignore", sparse_output=False).fit(pop.loc[tr, ["combo"]])
        oh_r = OneHotEncoder(handle_unknown="ignore", sparse_output=False).fit(pop.loc[tr, ["repo_hf"]])
        dense = {"S": pop[FEATURES].to_numpy(float), "M": oh_m.transform(pop[["combo"]]),
                 "repo": oh_r.transform(pop[["repo_hf"]])}
        # TF-IDF blocks fit on the training fold: T on the unique training tasks, A and R on the training runs
        train_tasks = np.isin(tasks, np.unique(groups[tr]))
        vt = tfidf().fit(statements[train_tasks])
        txt = {"T": vt.transform(statements)[task_pos],
               "A": tfidf().fit(pop.final_full[tr]).transform(pop.final_full),
               "R": tfidf().fit(pop.reasoning_full[tr]).transform(pop.reasoning_full)}
        timing[f"fold{k}_tfidf_s"] = round(time.time() - t0, 1)
        for name, (dblocks, tblocks) in spec.items():
            xd = np.hstack([dense[b] for b in dblocks])
            sc = StandardScaler().fit(xd[tr])
            xd = sc.transform(xd)
            x = sparse.hstack([sparse.csr_matrix(xd)] + [txt[b] for b in tblocks]).tocsr() if tblocks else xd
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                m = LogisticRegression(C=1.0, max_iter=5000).fit(x[tr], y[tr])
                n_warn += sum("converge" in str(i.message).lower() for i in w)
            risk[name][te] = m.predict_proba(x[te])[:, 1]
        timing[f"fold{k}_total_s"] = round(time.time() - t0, 1)
        log.info("fold %d done in %.0f s", k, time.time() - t0)
    return risk, {"convergence_warnings": n_warn, **timing,
                  "tfidf_vocab_sizes_last_fold": {b: int(txt[b].shape[1]) for b in txt}}


# ------------------------------------------------------------------------------------------ metrics

def auroc(yy: np.ndarray, ss: np.ndarray) -> float:
    r = rankdata(ss)
    n1 = yy.sum()
    n0 = len(yy) - n1
    return float((r[yy == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def prec_at(yy: np.ndarray, ss: np.ndarray, frac: float) -> float:
    k = math.ceil(frac * len(yy))
    return float(yy[np.argsort(-ss, kind="mergesort")[:k]].mean())


def pair_counts(y: np.ndarray, r: np.ndarray, task_rows: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    """Concordant (unresolved, resolved) pairs of every task (ties 1/2) and the number of pairs."""
    c, n = np.zeros(len(task_rows)), np.zeros(len(task_rows))
    for i, ix in enumerate(task_rows):
        u, s = r[ix][y[ix] == 1], r[ix][y[ix] == 0]
        if len(u) and len(s):
            d = u[:, None] - s[None, :]
            c[i], n[i] = (d > 0).sum() + 0.5 * (d == 0).sum(), d.size
    return c, n


def metrics_of(yy: np.ndarray, ss: np.ndarray, c: np.ndarray, n: np.ndarray) -> dict[str, float]:
    out = {"auroc": auroc(yy, ss), "within": float(c.sum() / n.sum()), "brier": float(np.mean((ss - yy) ** 2))}
    for frac in KS:
        out[f"p@{round(frac * 100)}"] = prec_at(yy, ss, frac)
    return out


def evaluate(pop: pd.DataFrame, y: np.ndarray, risk: dict[str, np.ndarray]) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    groups = pop.task_id.to_numpy()
    task_rows = list(pd.Series(np.arange(len(pop))).groupby(groups).apply(np.array))
    counts = {m: pair_counts(y, risk[m], task_rows) for m in MODELS}
    point = {m: metrics_of(y, risk[m], *counts[m]) for m in MODELS}
    # common cluster-bootstrap resamples (same draws as notebooks/demo.ipynb for the overall AUROC)
    rng = np.random.default_rng(SEED)
    draws = [rng.integers(0, len(task_rows), len(task_rows)) for _ in range(N_BOOT)]
    boot = {m: {k: np.zeros(N_BOOT) for k in METRICS} for m in MODELS}
    for b, dr in enumerate(draws):
        ix = np.concatenate([task_rows[i] for i in dr])
        for m in MODELS:
            c, n = counts[m]
            vals = metrics_of(y[ix], risk[m][ix], c[dr], n[dr])
            for k in METRICS:
                boot[m][k][b] = vals[k]
    ci = lambda v, a=0.05: np.percentile(v, [100 * a / 2, 100 * (1 - a / 2)])  # noqa: E731
    mrows = []
    for m in MODELS:
        for k in METRICS:
            lo, hi = ci(boot[m][k])
            mrows.append({"model": m, "metric": k, "value": point[m][k], "ci_lo": lo, "ci_hi": hi})
    primary = {(q, a, b, k) for q, a, b, k, _ in DIFFS}
    drows = []
    for q, a, b in DIFF_PAIRS:
        for k in METRICS:
            d = boot[a][k] - boot[b][k]
            lo, hi = ci(d)
            is_primary = (q, a, b, k) in primary
            blo, bhi = ci(d, 0.05 / len(DIFFS)) if is_primary else (np.nan, np.nan)
            sign = -1 if k == "brier" else 1  # a lower Brier score is better
            verdict = ("gain" if sign * lo > 0 and sign * hi > 0 else "loss" if sign * lo < 0 and sign * hi < 0
                       else "not shown")
            drows.append({"question": q, "model": a, "baseline": b, "metric": k,
                          "diff": point[a][k] - point[b][k], "ci_lo": lo, "ci_hi": hi, "primary": is_primary,
                          "bonf_lo": blo, "bonf_hi": bhi, "verdict": verdict})
    # (a): share of the within-task gain of S over 0.5 reached by M alone
    ratio = (boot["M"]["within"] - 0.5) / (boot["S"]["within"] - 0.5)
    n1, n0 = y.sum(), len(y) - y.sum()
    same_unit = float(sum(len(ix) and (y[ix].sum() * (len(ix) - y[ix].sum())) for ix in task_rows) / (n1 * n0))
    extra = {"a_ratio": (point["M"]["within"] - 0.5) / (point["S"]["within"] - 0.5),
             "a_ratio_ci": list(ci(ratio)), "same_unit_weight": same_unit,
             "tasks_with_pairs": int(sum(1 for c_, n_ in zip(*counts["S"]) if n_ > 0)),
             "within_pairs": int(counts["S"][1].sum()), "n_runs": len(pop), "n_tasks": len(task_rows),
             "unresolved_share": float(y.mean())}
    return pd.DataFrame(mrows), pd.DataFrame(drows), extra


def run_population(name: str, drop_requests: bool) -> tuple[pd.DataFrame, pd.DataFrame, dict, pd.DataFrame]:
    t0 = time.time()
    pop = load_population(drop_requests)
    y = (1 - pop.resolved).to_numpy()
    fold = folds_of(pop, y)
    risk, info = oof_risks(pop, y, fold)
    if not drop_requests:  # check of the protocol: S must reproduce DEMO_REPORT.md
        got = (round(auroc(y, risk["S"]), 3),)
        groups = pop.task_id.to_numpy()
        task_rows = list(pd.Series(np.arange(len(pop))).groupby(groups).apply(np.array))
        c, n = pair_counts(y, risk["S"], task_rows)
        got += (round(float(c.sum() / n.sum()), 3),)
        if got != EXPECTED_S:
            raise SystemExit(f"STOP: S gives AUROC / within {got}, DEMO_REPORT.md {EXPECTED_S}")
        log.info("S reproduces DEMO_REPORT.md: %s", got)
    met, dif, extra = evaluate(pop, y, risk)
    for df in (met, dif):
        df.insert(0, "population", name)
    extra.update(info)
    extra["empty_final"] = int((pop.final_full == "").sum())
    extra["empty_reasoning"] = int((pop.reasoning_full == "").sum())
    extra["seconds"] = round(time.time() - t0, 1)
    oof = pd.DataFrame({"population": name, "run_id": pop.run_id, "task_id": pop.task_id, "y_unresolved": y,
                        "fold": fold, **{m: risk[m] for m in MODELS}})
    return met, dif, extra, oof


def run() -> None:
    results = [run_population("all", False), run_population("no_psf_requests", True)]
    met = pd.concat([r[0] for r in results], ignore_index=True)
    dif = pd.concat([r[1] for r in results], ignore_index=True)
    oof = pd.concat([r[3] for r in results], ignore_index=True)
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    met.round(6).to_csv(OUTPUTS / "e7_metrics.csv", index=False)
    dif.round(6).to_csv(OUTPUTS / "e7_diffs.csv", index=False)
    oof.round(8).to_csv(OUTPUTS / "e7_oof_risk.csv.gz", index=False)
    meta_p = TASKS_PATH.parent / "tasks_meta.json"
    summary = {"populations": {name: r[2] for name, r in zip(("all", "no_psf_requests"), results)},
               "swebench_verified": json.loads(meta_p.read_text(encoding="utf-8")) if meta_p.exists() else None}
    (OUTPUTS / "e7_summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False, default=float),
                                             encoding="utf-8")
    log.info("E7 outputs written")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "data":
        fetch_tasks()
        extract_texts()
    elif cmd == "run":
        run()
