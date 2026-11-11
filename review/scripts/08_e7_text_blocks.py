"""E7, question (g): do the agent's texts (A = final message, R = reasoning) add to M+S, the best model WITHOUT T?

  python review/scripts/08_e7_text_blocks.py -> review/results/08_e7_text_blocks.json

The protocol compares T+M+S+A / T+M+S+R with T+M+S, but T+M+S (0.626) is worse than its own sub-model M+S (0.685),
so the baseline of (g) is handicapped. Here the same construction as src/e7_sources.py (TF-IDF 1-2 grams, 5000
features, fit on the training fold; dense blocks standardised; LR C=1; GroupKFold(5, shuffle, seed 42) by task;
cluster bootstrap over tasks, 1000) is applied without T. Read-only.
"""
from __future__ import annotations

import json
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import sparse
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import GroupKFold
from sklearn.preprocessing import OneHotEncoder, StandardScaler

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from demo_lib.features import FEATURES  # noqa: E402

SEED = 42


def main():
    t0 = time.time()
    runs = pd.read_parquet(ROOT / "data" / "demo" / "runs.parquet")
    pop = runs[runs.submitted == 1].sort_values("run_id").reset_index(drop=True)
    texts = pd.read_parquet(ROOT / "data" / "interim" / "swebench_bashonly" / "e7_texts.parquet")
    pop = pop.merge(texts, on="run_id", how="left")
    pop["combo"] = pop.model + " | " + pop.agent_version
    y = (1 - pop.resolved).to_numpy()
    g = pop.task_id.to_numpy()
    names = ["M+S", "M+S+A", "M+S+R", "A", "R"]
    risk = {n: np.zeros(len(pop)) for n in names}
    for k, (tr, te) in enumerate(GroupKFold(n_splits=5, shuffle=True, random_state=SEED).split(pop, y, g)):
        oh = OneHotEncoder(handle_unknown="ignore", sparse_output=False).fit(pop.iloc[tr][["combo"]])
        dense = np.hstack([oh.transform(pop[["combo"]]), pop[FEATURES].to_numpy(float)])
        dense = StandardScaler().fit(dense[tr]).transform(dense)
        txt = {b: TfidfVectorizer(ngram_range=(1, 2), max_features=5000).fit(pop[col].iloc[tr]).transform(pop[col])
               for b, col in (("A", "final_full"), ("R", "reasoning_full"))}
        xs = {"M+S": dense, "M+S+A": sparse.hstack([sparse.csr_matrix(dense), txt["A"]]).tocsr(),
              "M+S+R": sparse.hstack([sparse.csr_matrix(dense), txt["R"]]).tocsr(), "A": txt["A"], "R": txt["R"]}
        for n, x in xs.items():
            m = LogisticRegression(C=1.0, max_iter=5000).fit(x[tr], y[tr])
            risk[n][te] = m.predict_proba(x[te])[:, 1]
        print(f"fold {k} done, {time.time() - t0:.0f} s", flush=True)
    idx = pd.Series(np.arange(len(y))).groupby(g).apply(np.array).tolist()

    def counts(s):
        c, n = np.zeros(len(idx)), np.zeros(len(idx))
        for i, ix in enumerate(idx):
            u, v = s[ix][y[ix] == 1], s[ix][y[ix] == 0]
            if len(u) and len(v):
                d = u[:, None] - v[None, :]
                c[i], n[i] = (d > 0).sum() + 0.5 * (d == 0).sum(), d.size
        return c, n

    cn = {n: counts(s) for n, s in risk.items()}
    rng = np.random.default_rng(SEED)
    draws = [rng.integers(0, len(idx), len(idx)) for _ in range(1000)]
    ba = {n: np.zeros(1000) for n in names}
    bw = {n: np.zeros(1000) for n in names}
    for b, dr in enumerate(draws):
        ix = np.concatenate([idx[i] for i in dr])
        for n in names:
            ba[n][b] = roc_auc_score(y[ix], risk[n][ix])
            bw[n][b] = cn[n][0][dr].sum() / cn[n][1][dr].sum()
    res = {"models": {}, "diffs": {}}
    for n in names:
        res["models"][n] = {"auroc": round(roc_auc_score(y, risk[n]), 4), "auroc_ci": [round(float(x), 4) for x in np.percentile(ba[n], [2.5, 97.5])],
                            "within": round(float(cn[n][0].sum() / cn[n][1].sum()), 4)}
    for a, b in (("M+S+A", "M+S"), ("M+S+R", "M+S")):
        res["diffs"][f"{a} - {b}"] = {
            "auroc": [round(res["models"][a]["auroc"] - res["models"][b]["auroc"], 4), *[round(float(x), 4) for x in np.percentile(ba[a] - ba[b], [2.5, 97.5])]],
            "within": [round(res["models"][a]["within"] - res["models"][b]["within"], 4), *[round(float(x), 4) for x in np.percentile(bw[a] - bw[b], [2.5, 97.5])]]}
    res["seconds"] = round(time.time() - t0, 1)
    print(json.dumps(res, indent=1))
    (ROOT / "review" / "results" / "08_e7_text_blocks.json").write_text(json.dumps(res, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
