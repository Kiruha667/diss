"""KTH repeats: is the within-unit ~0.53-0.58 a ceiling, or a limit of the 21 structure features?

  python review/scripts/09_kth_commands_tfidf.py -> review/results/09_kth_commands_tfidf.json

A richer but still cheap representation of the same trajectories: TF-IDF of the executed commands (1-2 grams,
min_df 5, 20000 features, fit on the training fold), logistic regression, trained within the setting, out-of-fold by
task (GroupKFold 5, seed 42). Within-unit AUROC as in E8 / E10. Input: data/interim/agentic_evals/{runs,steps}.parquet.
apply_patch calls are `sed -i 's/<hash>/<hash>/' <file>` in this table (the author's adapter): the hashes are dropped
by min_df, the file path stays. Read-only.
"""
from __future__ import annotations

import importlib.util
import json
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import sparse
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import GroupKFold
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("k4", HERE / "04_kth.py")
k4 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(k4)
SEED = 42


def main():
    k = pd.read_parquet(ROOT / "data" / "interim" / "agentic_evals" / "runs.parquet").sort_values("run_id").reset_index(drop=True)
    k["unit"] = k.setting + "|" + k.task_id
    k["fin"] = k.ended_by_final_message.astype(int)
    st = pd.read_parquet(ROOT / "data" / "interim" / "agentic_evals" / "steps.parquet", columns=["run_id", "step_idx", "commands"])
    st = st.sort_values(["run_id", "step_idx"])
    st["cmd"] = st.commands.apply(lambda c: "\n".join(str(x) for x in c))
    doc = st.groupby("run_id").cmd.apply(lambda s: "\n".join(s))
    tail = st.groupby("run_id").cmd.apply(lambda s: "\n".join(s.iloc[-10:]))
    k["doc"] = doc.reindex(k.run_id).fillna("").to_numpy()
    k["tail"] = tail.reindex(k.run_id).fillna("").to_numpy()
    res = {}
    for setting, short in k4.SHORT.items():
        d = k[k.setting == setting].reset_index(drop=True)
        y = d.label.to_numpy()
        g = d.task_id.to_numpy()
        risk = {n: np.zeros(len(d)) for n in ("commands tf-idf", "last 10 commands tf-idf", "S + commands tf-idf")}
        for tr, te in GroupKFold(n_splits=5, shuffle=True, random_state=SEED).split(d, y, g):
            xs = {}
            for n, col in (("commands tf-idf", "doc"), ("last 10 commands tf-idf", "tail")):
                v = TfidfVectorizer(ngram_range=(1, 2), max_features=20000, min_df=5, sublinear_tf=True).fit(d[col].iloc[tr])
                xs[n] = v.transform(d[col])
            dense = StandardScaler().fit(d.iloc[tr][k4.S].to_numpy(float)).transform(d[k4.S].to_numpy(float))
            xs["S + commands tf-idf"] = sparse.hstack([sparse.csr_matrix(dense), xs["commands tf-idf"]]).tocsr()
            for n, x in xs.items():
                risk[n][te] = LogisticRegression(C=1.0, max_iter=5000).fit(x[tr], y[tr]).predict_proba(x[te])[:, 1]
        out = {}
        for n, r in risk.items():
            out[n] = {"all attempts": k4.within(d, r), "finished attempts only": k4.within(d[d.fin == 1], r[(d.fin == 1).to_numpy()])}
        res[short] = out
        print(short, json.dumps(out, default=float))
    (ROOT / "review" / "results" / "09_kth_commands_tfidf.json").write_text(json.dumps(res, indent=1, default=float), encoding="utf-8")


if __name__ == "__main__":
    main()
