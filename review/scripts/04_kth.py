"""Reviewer's checks on the ASSERT-KTH repeats (read-only; own metric code).

  python review/scripts/04_kth.py -> review/results/04_kth.json

Input: data/interim/agentic_evals/runs.parquet (features, label, ended_by_final_message), outputs/e10_features.csv.gz
(block O). Unit = setting x task. Everything out-of-fold by task (GroupKFold 5, seed 42).
"""
from __future__ import annotations

import json
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import rankdata
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[2]
S = ["has_any_verification", "n_verifications", "verif_after_last_edit", "n_verif_after_last_edit",
     "verified_before_first_edit", "n_steps_after_last_edit", "share_edit", "share_verify", "share_other",
     "n_transitions", "phase_bigram_entropy", "share_before_first_edit", "repeat_ratio", "distinct_cmd_ratio",
     "nonzero_rc_share", "n_files_edited", "n_file_switches", "n_steps", "seg_len_steps", "seg_len_sent", "n_edits"]
VERIF = ["has_any_verification", "n_verifications", "verif_after_last_edit", "n_verif_after_last_edit",
         "verified_before_first_edit", "share_verify"]
LEN = ["n_steps", "seg_len_steps", "seg_len_sent", "n_edits"]
SEED = 42
SHORT = {"nano-agent-Qwen_Qwen3-32B": "qwen", "nano-agent-agentica-org_DeepSWE-Preview": "deepswe",
         "nano-agent-mistral_devstral-2512": "devstral"}
res: dict = {}


def lr():
    return make_pipeline(StandardScaler(), LogisticRegression(C=1.0, max_iter=5000))


def unit_counts(y, r, units):
    c, n = np.zeros(len(units)), np.zeros(len(units))
    for i, ix in enumerate(units):
        k = y[ix].sum()
        if 0 < k < len(ix):
            rk = rankdata(r[ix])
            c[i], n[i] = rk[y[ix] == 1].sum() - k * (k + 1) / 2, k * (len(ix) - k)
    return c, n


def within(df, score, cluster="unit", n_boot=1000):
    """Within-unit AUROC (pooled pairs) with a cluster bootstrap over `cluster` (unit or task)."""
    d = df.reset_index(drop=True)
    y, r = d.label.to_numpy(), np.asarray(score, float)
    units = pd.Series(np.arange(len(d))).groupby(d.unit.to_numpy()).apply(np.array).tolist()
    c, n = unit_counts(y, r, units)
    if n.sum() == 0:
        return None
    if cluster == "unit":
        cl = [np.array([i]) for i in range(len(units))]
    else:
        task_of_unit = np.array([d.task_id.iloc[u[0]] for u in units])
        cl = pd.Series(np.arange(len(units))).groupby(task_of_unit).apply(np.array).tolist()
    rng = np.random.default_rng(SEED)
    b = []
    for _ in range(n_boot):
        pick = np.concatenate([cl[i] for i in rng.integers(0, len(cl), len(cl))])
        b.append(c[pick].sum() / n[pick].sum())
    lo, hi = np.percentile(b, [2.5, 97.5])
    return {"auroc": round(float(c.sum() / n.sum()), 4), "ci": [round(float(lo), 4), round(float(hi), 4)],
            "pairs": int(n.sum()), "units": int((n > 0).sum())}


def oof(df, cols, by_setting: bool):
    """Out-of-fold risk; pooled over settings (E8 P2) or trained within the setting (E9/E10)."""
    r = np.full(len(df), np.nan)
    parts = [df.index[df.setting == s] for s in df.setting.unique()] if by_setting else [df.index]
    for ix in parts:
        d = df.loc[ix]
        g = d.task_id.to_numpy()
        for tr, te in GroupKFold(n_splits=5, shuffle=True, random_state=SEED).split(d, d.label, g):
            m = lr().fit(d.iloc[tr][cols].to_numpy(float), d.label.to_numpy()[tr])
            r[ix[te]] = m.predict_proba(d.iloc[te][cols].to_numpy(float))[:, 1]
    return r


def oof_unit_fe(df, cols):
    """Trained FOR within-unit discrimination: features centred within the unit (unit fixed effects removed), on
    the units with both outcomes of the training tasks; the test runs are scored with the same centring."""
    r = np.full(len(df), np.nan)
    for s in df.setting.unique():
        ix = df.index[df.setting == s]
        d = df.loc[ix]
        X = d[cols].to_numpy(float)
        Xc = X - d.groupby("unit")[cols].transform("mean").to_numpy(float)
        mixed = d.groupby("unit").label.transform(lambda v: 0 < v.mean() < 1).to_numpy(bool)
        g = d.task_id.to_numpy()
        y = d.label.to_numpy()
        for tr, te in GroupKFold(n_splits=5, shuffle=True, random_state=SEED).split(d, y, g):
            trm = tr[mixed[tr]]
            sc = StandardScaler().fit(Xc[trm])
            m = LogisticRegression(C=1.0, max_iter=5000).fit(sc.transform(Xc[trm]), y[trm])
            r[ix[te]] = m.decision_function(sc.transform(Xc[te]))
    return r


def main():
    k = pd.read_parquet(ROOT / "data" / "interim" / "agentic_evals" / "runs.parquet").sort_values("run_id").reset_index(drop=True)
    k["unit"] = k.setting + "|" + k.task_id
    k["fin"] = k.ended_by_final_message.astype(int)
    y = k.label.to_numpy()
    scopes = {**{v: k.setting == s for s, v in SHORT.items()}, "all": pd.Series(True, index=k.index)}

    # ---------------------------------------------------------------- A. reproduction of E8 (pooled) and E10 (within)
    risk = {"S pooled (E8 P2)": oof(k, S, False), "LEN pooled (E8 P3)": oof(k, LEN, False),
            "S within-setting (E10 S)": oof(k, S, True), "S unit-FE trained": oof_unit_fe(k, S),
            "S w/o verification feats, within-setting": oof(k, [c for c in S if c not in VERIF], True),
            "S+fin within-setting": oof(k, S + ["fin"], True)}
    A = {}
    for name, r in risk.items():
        A[name] = {sc: within(k[m], r[m.to_numpy()]) for sc, m in scopes.items()}
        print(name, {sc: (v["auroc"], v["ci"]) for sc, v in A[name].items()})
    A["S pooled, bootstrap over TASKS, all"] = within(k, risk["S pooled (E8 P2)"], cluster="task")
    A["S within-setting, bootstrap over TASKS, all"] = within(k, risk["S within-setting (E10 S)"], cluster="task")
    print("task-cluster bootstrap, all:", A["S pooled, bootstrap over TASKS, all"], A["S within-setting, bootstrap over TASKS, all"])
    res["A_within_unit"] = A

    # ---------------------------------------------------------------- B. is the signal "the attempt was cut off"?
    B = {}
    for sc, m in scopes.items():
        d = k[m]
        B[sc] = {"runs": int(len(d)), "share_finished": round(float(d.fin.mean()), 4),
                 "unresolved_if_finished": round(float(d.label[d.fin == 1].mean()), 4),
                 "unresolved_if_not_finished": round(float(d.label[d.fin == 0].mean()), 4) if (d.fin == 0).any() else None,
                 "not_finished_alone_within": within(d, 1 - d.fin.to_numpy()),
                 "n_steps_alone_within": within(d, d.n_steps.to_numpy())}
        fin_d = d[d.fin == 1]
        for name in ("S pooled (E8 P2)", "S within-setting (E10 S)", "S unit-FE trained"):
            B[sc][f"{name} | finished attempts only"] = within(fin_d, risk[name][fin_d.index.to_numpy()])
        print(sc, json.dumps(B[sc], default=float)[:900])
    # retrain on finished attempts only (within-setting) and evaluate on finished pairs
    kf = k[k.fin == 1].reset_index(drop=True)
    r_f = oof(kf, S, True)
    r_ffe = oof_unit_fe(kf, S)
    B["retrained on finished only"] = {SHORT.get(s, s): {"S within-setting": within(kf[kf.setting == s], r_f[(kf.setting == s).to_numpy()]),
                                                          "S unit-FE": within(kf[kf.setting == s], r_ffe[(kf.setting == s).to_numpy()])}
                                       for s in kf.setting.unique()}
    print("retrained on finished only:", json.dumps(B["retrained on finished only"], default=float))
    res["B_finished"] = B

    # ---------------------------------------------------------------- C. devstral: what carries the within-unit signal
    d = k[k.setting == "nano-agent-mistral_devstral-2512"]
    single = {}
    for f in S + ["fin"]:
        w = within(d, d[f].to_numpy(float), n_boot=200)
        single[f] = round(max(w["auroc"], 1 - w["auroc"]), 4) if w else None
    res["C_devstral_single_feature_within"] = dict(sorted(single.items(), key=lambda kv: -(kv[1] or 0)))
    print("devstral single-feature within-unit |AUROC-0.5|+0.5:", res["C_devstral_single_feature_within"])
    dv = d[d.has_any_verification == 1]
    res["C_devstral_only_runs_with_verification"] = within(dv, risk["S within-setting (E10 S)"][dv.index.to_numpy()])
    res["C_devstral_no_verification_flag_alone"] = within(d, 1 - d.has_any_verification.to_numpy(float))
    print("devstral, runs with verification only:", res["C_devstral_only_runs_with_verification"],
          "| 'no verification' flag alone:", res["C_devstral_no_verification_flag_alone"])

    # ---------------------------------------------------------------- D. how much the unit decides (LOO difficulty)
    n_u = k.groupby("unit").label.transform("size").to_numpy()
    f_u = k.groupby("unit").label.transform("sum").to_numpy()
    loo = np.where(n_u > 1, (f_u - y) / np.maximum(n_u - 1, 1), np.nan)
    D = {}
    for sc, m in scopes.items():
        mm = m.to_numpy() & ~np.isnan(loo)
        um = k[m].groupby("unit").label.mean()
        D[sc] = {"auroc_loo_unit_difficulty": round(roc_auc_score(y[mm], loo[mm]), 4),
                 "auroc_S_within_setting_overall": round(roc_auc_score(y[m.to_numpy()], risk["S within-setting (E10 S)"][m.to_numpy()]), 4),
                 "units": int(len(um)), "units_all_solved": int((um == 0).sum()), "units_none_solved": int((um == 1).sum()),
                 "units_mixed": int(((um > 0) & (um < 1)).sum())}
    print("unit-level:", D)
    res["D_unit_level"] = D

    # ---------------------------------------------------------------- E. block O within devstral, finished only
    fe = pd.read_csv(ROOT / "outputs" / "e10_features.csv.gz")
    fe = fe[fe.corpus == "kth"]
    ocols = [c for c in fe.columns if c.startswith("o_")]
    kk = k.merge(fe[["run_id"] + ocols], on="run_id", how="left")
    kk[ocols] = kk[ocols].fillna(0)
    r_so = oof(kk, S + ocols, True)
    r_s = risk["S within-setting (E10 S)"]
    E = {}
    for sc in ("devstral",):
        m = (kk.setting == "nano-agent-mistral_devstral-2512")
        E["S+O all attempts"] = within(kk[m], r_so[m.to_numpy()])
        mf = m & (kk.fin == 1)
        E["S+O finished only"] = within(kk[mf], r_so[mf.to_numpy()])
        E["S finished only"] = within(kk[mf], r_s[mf.to_numpy()])
    print("devstral block O:", E)
    res["E_block_O_devstral"] = E

    (ROOT / "review" / "results" / "04_kth.json").write_text(json.dumps(res, indent=1, ensure_ascii=False, default=float), encoding="utf-8")


if __name__ == "__main__":
    main()
