"""Reviewer's independent recomputation on the leaderboard corpus (read-only; own metric code, no src imports).

  python review/scripts/01_leaderboard.py  -> review/results/01_leaderboard.json (+ printed tables)

Input: data/demo/runs.parquet only. Population: submitted == 1 (7864 runs).
Part A  reproduction of S, M, M+S (overall AUROC, within-task pooled, precision@10%), cluster bootstrap over tasks.
Part B  the Stage-1 predictions P1-P7 and extra robustness checks.
"""
from __future__ import annotations

import json
import math
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "review" / "results"
S = ["has_any_verification", "n_verifications", "verif_after_last_edit", "n_verif_after_last_edit",
     "verified_before_first_edit", "n_steps_after_last_edit", "share_edit", "share_verify", "share_other",
     "n_transitions", "phase_bigram_entropy", "share_before_first_edit", "repeat_ratio", "distinct_cmd_ratio",
     "nonzero_rc_share", "n_files_edited", "n_file_switches", "n_steps", "seg_len_steps", "seg_len_sent", "n_edits"]
LEN = ["n_steps", "seg_len_steps", "seg_len_sent", "n_edits"]
SEED = 42
res: dict = {}


def load():
    r = pd.read_parquet(ROOT / "data" / "demo" / "runs.parquet")
    p = r[r.submitted == 1].sort_values("run_id").reset_index(drop=True)
    p["combo"] = p.model + " | " + p.agent_version
    p["y"] = 1 - p.resolved
    return p


def folds(groups, seed=SEED, n=5):
    f = np.full(len(groups), -1)
    for k, (_, te) in enumerate(GroupKFold(n_splits=n, shuffle=True, random_state=seed).split(groups, groups=groups)):
        f[te] = k
    return f


def lr():
    return make_pipeline(StandardScaler(), LogisticRegression(C=1.0, max_iter=5000))


def oof(X, y, fold, make=lr):
    r = np.zeros(len(y))
    for k in np.unique(fold):
        tr, te = fold != k, fold == k
        r[te] = make().fit(X[tr], y[tr]).predict_proba(X[te])[:, 1]
    return r


def within_pooled(y, s, groups):
    """Share of (unresolved, resolved) pairs of the same group ordered correctly, ties 1/2; also macro mean."""
    c = n = 0.0
    per = []
    df = pd.DataFrame({"y": y, "s": s, "g": groups})
    for _, d in df.groupby("g"):
        u, v = d.s[d.y == 1].to_numpy(), d.s[d.y == 0].to_numpy()
        if len(u) and len(v):
            dd = u[:, None] - v[None, :]
            cc = (dd > 0).sum() + 0.5 * (dd == 0).sum()
            c += cc
            n += dd.size
            per.append(cc / dd.size)
    return c / n, float(np.mean(per)), int(n), len(per)


def p_at(y, s, frac):
    k = math.ceil(frac * len(y))
    return float(y[np.argsort(-s, kind="mergesort")[:k]].mean())


def boot_ci(y, groups, scores: dict, fn, n_boot=1000, seed=SEED):
    """Cluster bootstrap over groups; fn(y, s) -> float. Returns {name: (lo, hi)} and the draws."""
    idx = pd.Series(np.arange(len(y))).groupby(groups).apply(np.array).tolist()
    rng = np.random.default_rng(seed)
    out = {k: np.zeros(n_boot) for k in scores}
    for b in range(n_boot):
        ix = np.concatenate([idx[i] for i in rng.integers(0, len(idx), len(idx))])
        for k, s in scores.items():
            out[k][b] = fn(y[ix], s[ix])
    return {k: tuple(np.percentile(v, [2.5, 97.5]).round(4)) for k, v in out.items()}, out


def onehot(col, tr):
    enc = OneHotEncoder(handle_unknown="ignore", sparse_output=False).fit(col[tr].reshape(-1, 1))
    return enc.transform(col.reshape(-1, 1))


def oof_blocks(p, y, fold, blocks, make=lr, extra=None):
    """Out-of-fold risk for a list of blocks among 'S', 'M', 'LEN', 'N' (n_steps), 'REPO' or an extra array."""
    r = np.zeros(len(y))
    for k in np.unique(fold):
        tr, te = fold != k, fold == k
        xs = []
        for b in blocks:
            if b == "S":
                xs.append(p[S].to_numpy(float))
            elif b == "LEN":
                xs.append(p[LEN].to_numpy(float))
            elif b == "N":
                xs.append(p[["n_steps"]].to_numpy(float))
            elif b == "M":
                xs.append(onehot(p.combo.to_numpy(), tr))
            elif b == "REPO":
                xs.append(onehot(p.repo.to_numpy(), tr))
            else:
                xs.append(extra[b])
        X = np.hstack(xs)
        r[te] = make().fit(X[tr], y[tr]).predict_proba(X[te])[:, 1]
    return r


def gbm():
    return HistGradientBoostingClassifier(max_iter=200, learning_rate=0.05, max_leaf_nodes=15, random_state=SEED)


def main():
    p = load()
    y = p.y.to_numpy()
    g = p.task_id.to_numpy()
    fold = folds(g)
    print(f"runs {len(p)}, tasks {p.task_id.nunique()}, combos {p.combo.nunique()}, unresolved {y.mean():.4f}")

    # ---------------------------------------------------------------- A. reproduction
    risk = {"S": oof_blocks(p, y, fold, ["S"]), "M": oof_blocks(p, y, fold, ["M"]),
            "M+S": oof_blocks(p, y, fold, ["M", "S"]), "LEN": oof_blocks(p, y, fold, ["LEN"]),
            "N": oof_blocks(p, y, fold, ["N"])}
    A = {}
    ci_auc, _ = boot_ci(y, g, risk, roc_auc_score)
    ci_p10, _ = boot_ci(y, g, risk, lambda a, b: p_at(a, b, 0.10))
    for k, s in risk.items():
        wp, wm, npairs, ntasks = within_pooled(y, s, g)
        A[k] = {"auroc": round(roc_auc_score(y, s), 4), "auroc_ci": ci_auc[k], "within_pooled": round(wp, 4),
                "within_macro": round(wm, 4), "pairs": npairs, "tasks_with_pairs": ntasks,
                "p@10": round(p_at(y, s, 0.10), 4), "p@10_ci": ci_p10[k],
                "recall@10": round(float(y[np.argsort(-s, kind='mergesort')[:math.ceil(0.1 * len(y))]].sum() / y.sum()), 4),
                "brier": round(float(np.mean((s - y) ** 2)), 4)}
        print(k, A[k])
    res["A_reproduction"] = A

    # ---------------------------------------------------------------- B. fold-seed stability
    stab = {}
    for name, bl in (("S", ["S"]), ("M+S", ["M", "S"]), ("M", ["M"])):
        v = []
        for sd in range(10):
            f = folds(g, seed=sd)
            s = oof_blocks(p, y, f, bl)
            v.append((roc_auc_score(y, s), within_pooled(y, s, g)[0], p_at(y, s, 0.10)))
        v = np.array(v)
        stab[name] = {"auroc_min_max": [round(v[:, 0].min(), 4), round(v[:, 0].max(), 4)],
                      "within_min_max": [round(v[:, 1].min(), 4), round(v[:, 1].max(), 4)],
                      "p10_min_max": [round(v[:, 2].min(), 4), round(v[:, 2].max(), 4)]}
    print("fold-seed stability (10 seeds):", stab)
    res["B_fold_seed_stability"] = stab

    # ---------------------------------------------------------------- P1/P2: task difficulty from the other combos
    # LOCO difficulty: share of unresolved among the OTHER submitted runs of the same task.
    n_t = p.groupby("task_id").y.transform("size").to_numpy()
    f_t = p.groupby("task_id").y.transform("sum").to_numpy()
    loco = np.where(n_t > 1, (f_t - y) / np.maximum(n_t - 1, 1), y.mean())
    res["P1_loco_difficulty_auroc"] = round(roc_auc_score(y, loco), 4)
    print("P1: LOCO task difficulty alone, overall AUROC", res["P1_loco_difficulty_auroc"])
    # split-half difficulty (independent of the run's own combo AND constant within task for the other half)
    rng = np.random.default_rng(SEED)
    combos = np.array(sorted(p.combo.unique()))
    half_auc = []
    for _ in range(20):
        a = set(rng.permutation(combos)[:8])
        in_a = p.combo.isin(a).to_numpy()
        d_a = p[in_a].groupby("task_id").y.mean()
        d_b = p[~in_a].groupby("task_id").y.mean()
        est = np.where(in_a, p.task_id.map(d_b).to_numpy(), p.task_id.map(d_a).to_numpy())
        ok = ~np.isnan(est)
        half_auc.append(roc_auc_score(y[ok], est[ok]))
    res["P1_split_half_difficulty_auroc_mean"] = round(float(np.mean(half_auc)), 4)
    print("P1: split-half difficulty (8 other combos), overall AUROC mean of 20 splits", res["P1_split_half_difficulty_auroc_mean"])
    # P2: does S add to (difficulty + M)? logit of clipped LOCO as a feature
    lg = np.log(np.clip(loco, 0.02, 0.98) / (1 - np.clip(loco, 0.02, 0.98)))[:, None]
    ex = {"D": lg}
    rD = {"D+M": oof_blocks(p, y, fold, ["D", "M"], extra=ex), "D+M+S": oof_blocks(p, y, fold, ["D", "M", "S"], extra=ex),
          "D+M+LEN": oof_blocks(p, y, fold, ["D", "M", "LEN"], extra=ex),
          "D+M+S gbm": oof_blocks(p, y, fold, ["D", "M", "S"], make=gbm, extra=ex),
          "D+M gbm": oof_blocks(p, y, fold, ["D", "M"], make=gbm, extra=ex)}
    _, dr = boot_ci(y, g, rD, roc_auc_score)
    P2 = {k: round(roc_auc_score(y, s), 4) for k, s in rD.items()}
    d = dr["D+M+S"] - dr["D+M"]
    P2["(D+M+S)-(D+M)"] = [round(P2["D+M+S"] - P2["D+M"], 4), *np.percentile(d, [2.5, 97.5]).round(4)]
    d = dr["D+M+S gbm"] - dr["D+M gbm"]
    P2["gbm (D+M+S)-(D+M)"] = [round(P2["D+M+S gbm"] - P2["D+M gbm"], 4), *np.percentile(d, [2.5, 97.5]).round(4)]
    print("P2:", P2)
    res["P2_residual_over_difficulty"] = P2

    # ---------------------------------------------------------------- P3/P4: flexible length baseline, GBM
    rG = {"N gbm": oof_blocks(p, y, fold, ["N"], make=gbm), "LEN gbm": oof_blocks(p, y, fold, ["LEN"], make=gbm),
          "S gbm": oof_blocks(p, y, fold, ["S"], make=gbm), "M+S gbm": oof_blocks(p, y, fold, ["M", "S"], make=gbm),
          "M+N gbm": oof_blocks(p, y, fold, ["M", "N"], make=gbm), "M+LEN": oof_blocks(p, y, fold, ["M", "LEN"]),
          "M+N": oof_blocks(p, y, fold, ["M", "N"])}
    # log-length logistic baseline
    ex2 = {"LOGN": np.log1p(p[["n_steps"]].to_numpy(float))}
    rG["logN lr"] = oof_blocks(p, y, fold, ["LOGN"], extra=ex2)
    rG["M+logN lr"] = oof_blocks(p, y, fold, ["M", "LOGN"], extra=ex2)
    allr = {**risk, **rG}
    _, dr = boot_ci(y, g, allr, roc_auc_score)
    P34 = {}
    for k, s in rG.items():
        wp, wm, _, _ = within_pooled(y, s, g)
        P34[k] = {"auroc": round(roc_auc_score(y, s), 4), "within_pooled": round(wp, 4), "p@10": round(p_at(y, s, 0.1), 4)}
    for a, b in (("S", "N gbm"), ("S", "LEN gbm"), ("S gbm", "LEN gbm"), ("S gbm", "S"), ("M+S gbm", "M+S"),
                 ("M+S", "M+LEN"), ("M+S", "M+N"), ("M+S gbm", "M+N gbm"), ("S", "LEN"), ("S", "logN lr")):
        dd = dr[a] - dr[b]
        P34[f"{a} - {b}"] = [round(roc_auc_score(y, allr[a]) - roc_auc_score(y, allr[b]), 4),
                             *np.percentile(dd, [2.5, 97.5]).round(4)]
    for k, v in P34.items():
        print("P3/P4", k, v)
    res["P3_P4_length_and_gbm"] = P34

    # ---------------------------------------------------------------- P5: S as a fingerprint of the combo
    cls = p.combo.astype("category").cat.codes.to_numpy()
    pred = np.zeros(len(p), int)
    predg = np.zeros(len(p), int)
    for k in range(5):
        tr, te = fold != k, fold == k
        m = make_pipeline(StandardScaler(), LogisticRegression(C=1.0, max_iter=3000)).fit(p.loc[tr, S].to_numpy(float), cls[tr])
        pred[te] = m.predict(p.loc[te, S].to_numpy(float))
        mg = HistGradientBoostingClassifier(max_iter=150, learning_rate=0.1, max_leaf_nodes=15, random_state=SEED).fit(
            p.loc[tr, S].to_numpy(float), cls[tr])
        predg[te] = mg.predict(p.loc[te, S].to_numpy(float))
    gen = (p.agent_version == "2.0.0").to_numpy().astype(int)
    rgen = oof(p[S].to_numpy(float), gen, fold)
    res["P5_combo_from_S"] = {"acc_lr_16class": round(float((pred == cls).mean()), 4),
                              "acc_gbm_16class": round(float((predg == cls).mean()), 4), "chance": round(1 / 16, 4),
                              "generation_v2_auroc_from_S": round(roc_auc_score(gen, rgen), 4)}
    print("P5:", res["P5_combo_from_S"])

    # ---------------------------------------------------------------- P6: leave-one-model-out (+ task-disjoint)
    rows = []
    for c in combos:
        te_c = (p.combo == c).to_numpy()
        r_lomo, r_in = np.zeros(len(p)), np.zeros(len(p))
        for k in range(5):
            tr = (~te_c) & (fold != k)
            te = te_c & (fold == k)
            r_lomo[te] = lr().fit(p.loc[tr, S].to_numpy(float), y[tr]).predict_proba(p.loc[te, S].to_numpy(float))[:, 1]
            tr2 = te_c & (fold != k)
            r_in[te] = lr().fit(p.loc[tr2, S].to_numpy(float), y[tr2]).predict_proba(p.loc[te, S].to_numpy(float))[:, 1]
        yy = y[te_c]
        rows.append({"combo": c, "n": int(te_c.sum()), "resolved": round(1 - yy.mean(), 3),
                     "verif_share": round(float(p.has_any_verification[te_c].mean()), 3),
                     "median_steps": float(p.n_steps[te_c].median()),
                     "auroc_S_pooled_oof": round(roc_auc_score(yy, risk["S"][te_c]), 4),
                     "auroc_S_lomo": round(roc_auc_score(yy, r_lomo[te_c]), 4),
                     "auroc_S_within_combo_training": round(roc_auc_score(yy, r_in[te_c]), 4),
                     "auroc_n_steps_raw": round(roc_auc_score(yy, p.n_steps[te_c].to_numpy()), 4),
                     "auroc_loco_difficulty": round(roc_auc_score(yy, loco[te_c]), 4)})
    lomo = pd.DataFrame(rows).sort_values("resolved")
    print(lomo.to_string(index=False))
    from scipy.stats import spearmanr
    res["P6_lomo"] = {"table": lomo.to_dict("records"),
                      "mean_lomo": round(float(lomo.auroc_S_lomo.mean()), 4),
                      "mean_pooled_oof_per_combo": round(float(lomo.auroc_S_pooled_oof.mean()), 4),
                      "mean_within_combo_training": round(float(lomo.auroc_S_within_combo_training.mean()), 4),
                      "spearman_verif_share_vs_within_combo_auroc": [round(float(x), 3) for x in spearmanr(
                          lomo.verif_share, lomo.auroc_S_within_combo_training)],
                      "spearman_resolved_vs_within_combo_auroc": [round(float(x), 3) for x in spearmanr(
                          lomo.resolved, lomo.auroc_S_within_combo_training)],
                      "spearman_median_steps_vs_within_combo_auroc": [round(float(x), 3) for x in spearmanr(
                          lomo.median_steps, lomo.auroc_S_within_combo_training)]}
    print({k: v for k, v in res["P6_lomo"].items() if k != "table"})

    # ---------------------------------------------------------------- P7: leave-one-repo-out
    repo = p.repo.to_numpy()
    r_s, r_ms = np.zeros(len(p)), np.zeros(len(p))
    for rp in np.unique(repo):
        te = repo == rp
        r_s[te] = lr().fit(p.loc[~te, S].to_numpy(float), y[~te]).predict_proba(p.loc[te, S].to_numpy(float))[:, 1]
        Xm = np.hstack([onehot(p.combo.to_numpy(), ~te), p[S].to_numpy(float)])
        r_ms[te] = lr().fit(Xm[~te], y[~te]).predict_proba(Xm[te])[:, 1]
    per_repo = {rp: {"n": int((repo == rp).sum()), "unresolved": round(float(y[repo == rp].mean()), 3),
                     "auroc_S_loro": round(roc_auc_score(y[repo == rp], r_s[repo == rp]), 4),
                     "auroc_S_taskfold": round(roc_auc_score(y[repo == rp], risk["S"][repo == rp]), 4)}
                for rp in np.unique(repo) if 0 < y[repo == rp].mean() < 1 and (repo == rp).sum() >= 100}
    res["P7_loro"] = {"S_pooled_auroc": round(roc_auc_score(y, r_s), 4), "M+S_pooled_auroc": round(roc_auc_score(y, r_ms), 4),
                      "S_taskfold": A["S"]["auroc"], "M+S_taskfold": A["M+S"]["auroc"], "per_repo": per_repo,
                      "repo_only_auroc": round(roc_auc_score(y, oof_blocks(p, y, fold, ["REPO"])), 4),
                      "repo+M+S_auroc": round(roc_auc_score(y, oof_blocks(p, y, fold, ["REPO", "M", "S"])), 4)}
    print("P7:", json.dumps(res["P7_loro"], indent=0)[:1500])

    # ---------------------------------------------------------------- within-task question (b), trained for it
    # Task fixed effects (one-hot task, weak penalty) + M (+ S); score test runs with the M and S coefficients only.
    def fe_scores(use_s: bool):
        out = np.zeros(len(p))
        for k in range(5):
            tr, te = fold != k, fold == k
            Xm = onehot(p.combo.to_numpy(), tr)
            Xs = StandardScaler().fit(p.loc[tr, S].to_numpy(float)).transform(p[S].to_numpy(float))
            Xt = onehot(p.task_id.to_numpy(), tr)
            parts = [Xm] + ([Xs] if use_s else [])
            X = np.hstack(parts + [Xt])
            m = LogisticRegression(C=1.0, max_iter=5000).fit(X[tr], y[tr])
            w = m.coef_[0][: sum(a.shape[1] for a in parts)]
            out[te] = np.hstack(parts)[te] @ w
        return out
    fe = {"M (task FE)": fe_scores(False), "M+S (task FE)": fe_scores(True)}
    W = {k: round(within_pooled(y, s, g)[0], 4) for k, s in fe.items()}
    idx = pd.Series(np.arange(len(y))).groupby(g).apply(np.array).tolist()
    rng = np.random.default_rng(SEED)

    def wp_fast(ix, s):
        return within_pooled(y[ix], s[ix], np.repeat(np.arange(len(ix)), 1))[0]
    # bootstrap of the within-task difference over tasks (pair counts per task)
    def per_task_counts(s):
        c, n = np.zeros(len(idx)), np.zeros(len(idx))
        for i, ix in enumerate(idx):
            u, v = s[ix][y[ix] == 1], s[ix][y[ix] == 0]
            if len(u) and len(v):
                dd = u[:, None] - v[None, :]
                c[i], n[i] = (dd > 0).sum() + 0.5 * (dd == 0).sum(), dd.size
        return c, n
    cnt = {k: per_task_counts(s) for k, s in {**fe, "M": risk["M"], "M+S": risk["M+S"], "S": risk["S"]}.items()}
    draws = [rng.integers(0, len(idx), len(idx)) for _ in range(1000)]
    bt = {k: np.array([c[d].sum() / n[d].sum() for d in draws]) for k, (c, n) in cnt.items()}
    for a, b in (("M+S (task FE)", "M (task FE)"), ("M+S (task FE)", "M"), ("M+S", "M")):
        dd = bt[a] - bt[b]
        W[f"{a} - {b}"] = [round(float(cnt[a][0].sum() / cnt[a][1].sum() - cnt[b][0].sum() / cnt[b][1].sum()), 4),
                           *np.percentile(dd, [2.5, 97.5]).round(4)]
    print("within-task, models trained with task fixed effects:", W)
    res["within_task_fixed_effects"] = W

    # ---------------------------------------------------------------- M within task: what 0.742 measures
    strength = p.groupby("combo").y.transform("mean").to_numpy()  # in-sample failure rate of the combo
    res["M_within_task"] = {"in_sample_strength_within_pooled": round(within_pooled(y, strength, g)[0], 4),
                            "oof_M_within_pooled": A["M"]["within_pooled"], "oof_M_within_macro": A["M"]["within_macro"]}
    # expected within-task AUROC of strength under independence given task difficulty: simulate labels from a
    # Rasch-type model fitted by logistic regression with task and combo fixed effects (in-sample), 200 simulations
    Xc = pd.get_dummies(p.combo).to_numpy(float)
    Xt = pd.get_dummies(p.task_id).to_numpy(float)
    m = LogisticRegression(C=10.0, max_iter=5000).fit(np.hstack([Xc, Xt]), y)
    pr = m.predict_proba(np.hstack([Xc, Xt]))[:, 1]
    sims = []
    for _ in range(100):
        ys = (rng.random(len(pr)) < pr).astype(int)
        sims.append(within_pooled(ys, strength, g)[0])
    res["M_within_task"]["rasch_simulated_mean_sd"] = [round(float(np.mean(sims)), 4), round(float(np.std(sims)), 4)]
    res["M_within_task"]["rasch_fit_overall_auroc_in_sample"] = round(roc_auc_score(y, pr), 4)
    print("M within task:", res["M_within_task"])

    # ---------------------------------------------------------------- variance decomposition (descriptive)
    tm = p.groupby("task_id").y.mean()
    res["task_level"] = {"tasks_all_resolved": int((tm == 0).sum()), "tasks_none_resolved": int((tm == 1).sum()),
                         "tasks_mixed": int(((tm > 0) & (tm < 1)).sum()),
                         "share_runs_in_mixed_tasks": round(float(p.task_id.isin(tm.index[(tm > 0) & (tm < 1)]).mean()), 4),
                         "icc_task_anova": None}
    # one-way ANOVA ICC of the binary outcome by task
    k0 = p.groupby("task_id").size().mean()
    msb = (p.groupby("task_id").y.mean().sub(y.mean()) ** 2 * p.groupby("task_id").size()).sum() / (p.task_id.nunique() - 1)
    msw = ((p.y - p.groupby("task_id").y.transform("mean")) ** 2).sum() / (len(p) - p.task_id.nunique())
    res["task_level"]["icc_task_anova"] = round(float((msb - msw) / (msb + (k0 - 1) * msw)), 4)
    print("task level:", res["task_level"])

    # ---------------------------------------------------------------- calibration of M+S (10 bins)
    s = risk["M+S"]
    bins = pd.qcut(s, 10, labels=False, duplicates="drop")
    cal = pd.DataFrame({"b": bins, "s": s, "y": y}).groupby("b").agg(n=("y", "size"), pred=("s", "mean"), obs=("y", "mean"))
    res["calibration_M+S"] = {"ece_10bins": round(float((cal.n / cal.n.sum() * (cal.pred - cal.obs).abs()).sum()), 4),
                              "table": cal.round(3).reset_index().to_dict("records")}
    print("calibration M+S ECE", res["calibration_M+S"]["ece_10bins"])
    print(cal.round(3).to_string())

    # ---------------------------------------------------------------- features: duplicates and single-feature AUROC
    res["features"] = {"seg_len_steps_equals_n_steps_after_last_edit": bool((p.seg_len_steps == p.n_steps_after_last_edit).all()),
                       "shares_sum_to_1": bool(np.allclose(p.share_edit + p.share_verify + p.share_other, 1)),
                       "single_feature_auroc": {f: round(max(roc_auc_score(y, p[f]), 1 - roc_auc_score(y, p[f])), 4) for f in S},
                       "rank": int(np.linalg.matrix_rank(StandardScaler().fit_transform(p[S].to_numpy(float))))}
    print("features:", res["features"])

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "01_leaderboard.json").write_text(json.dumps(res, indent=1, ensure_ascii=False, default=float), encoding="utf-8")


if __name__ == "__main__":
    main()
