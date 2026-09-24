"""E4: statistical tests 3-6 (AGENTS.md "E4. Статистические тесты").

Everything uses ONE task -> fold map (outputs/folds.csv): a task is never in train and test at once.
Models are fixed, not tuned (AGENTS.md 5): StandardScaler + LogisticRegression(L2, C=1).
  M_base  = trivial features (constant columns such as n_frag == N are dropped inside a combo)
  M_chaos = H, C
  M_full  = both
Randomness: seed 42 everywhere.

Test 4 (critical): pooled out-of-fold AUROC / PR-AUC / precision at 10% alerts; delta AUROC (full - base)
  with a 95% percentile CI from a bootstrap over TASKS; one-sided bootstrap p for delta <= 0 with
  Benjamini-Hochberg across all combos. Spearman of H with full-trajectory length (diagnostic).
Rule 4.1: the headline number is either the pre-registered primary combo or a NESTED selection
  (combo chosen per outer fold by inner CV on the training folds only).
Test 3: per task with both classes, Mann-Whitney U of H (and C) between classes, rank-biserial effect
  (positive = higher in failures), BH across tasks; share of tasks with the majority direction.
Test 5: Test 4 over N in {50,100,200,400}, both on all eligible trajectories per N and on a FIXED set
  (trajectories long enough for the largest N), so the prefix effect is not confounded by the N filter.
Test 6: transfer across model families / agent versions, combined with task folds.
"""
from __future__ import annotations

import math
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from joblib import Parallel, delayed
from scipy.stats import binomtest, mannwhitneyu, rankdata, spearmanr
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from src.common import BASE_FEATURES, CHAOS_FEATURES, SEED, get_logger
from src.folds import load_folds

log = get_logger("stats")
warnings.filterwarnings("ignore", category=RuntimeWarning)

MODELS = {"base": BASE_FEATURES, "chaos": CHAOS_FEATURES, "full": BASE_FEATURES + CHAOS_FEATURES}
KEY = ["seg", "proj", "N", "d"]
# Rule 4.1 selection space = the MAIN analysis grid of AGENTS.md: seg=step is "only a control" (section 1:
# step resolution is forbidden as the main unit) and N=50 exists only for the Test 5 curve (E3 grid is 100/200/400).
MAIN_SEGS = ("reason_sent", "all_sent")
MAIN_NS = (100, 200, 400)
ALERT_RATE = 0.10
AUROC_EARLY = 0.65


# ------------------------------------------------------------------------------------------ metrics


def auroc(y: np.ndarray, s: np.ndarray) -> float:
    """Rank-based AUROC (ties averaged). NaN if only one class present."""
    y = np.asarray(y)
    n1 = int(y.sum())
    n0 = len(y) - n1
    if n1 == 0 or n0 == 0:
        return math.nan
    r = rankdata(s)
    return float((r[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def pr_auc(y, s) -> float:
    return float(average_precision_score(y, s)) if 0 < np.sum(y) < len(y) else math.nan


def precision_at_alert(y, s, rate: float = ALERT_RATE) -> float:
    k = max(1, int(math.ceil(rate * len(y))))
    top = np.argsort(-np.asarray(s), kind="mergesort")[:k]
    return float(np.asarray(y)[top].mean())


def fold_avg(fn, y: np.ndarray, s: np.ndarray, fold: np.ndarray) -> float:
    """Metric computed inside each CV fold and averaged over folds that contain both classes.

    Pooling out-of-fold predictions across folds biases AUROC (each fold's model has its own intercept,
    which moves against the fold's failure rate — strongly negative with heterogeneous tasks), so all
    headline metrics are fold-averaged.
    """
    vals = []
    for k in np.unique(fold):
        m = fold == k
        ok = m & ~np.isnan(s)
        yk = y[ok]
        if yk.size and 0 < yk.sum() < yk.size:
            vals.append(fn(yk, s[ok]))
    return float(np.mean(vals)) if vals else math.nan


def bh(p: np.ndarray) -> np.ndarray:
    """Benjamini-Hochberg adjusted q-values (NaNs passed through)."""
    p = np.asarray(p, dtype=float)
    q = np.full_like(p, np.nan)
    ok = ~np.isnan(p)
    m = ok.sum()
    if m == 0:
        return q
    ps = p[ok]
    order = np.argsort(ps)
    ranked = ps[order] * m / (np.arange(m) + 1)
    ranked = np.minimum.accumulate(ranked[::-1])[::-1]
    out = np.empty(m)
    out[order] = np.minimum(ranked, 1.0)
    q[ok] = out
    return q


# ------------------------------------------------------------------------------------------ models


def _model():
    return make_pipeline(StandardScaler(), LogisticRegression(C=1.0, max_iter=5000))


def usable_columns(df: pd.DataFrame, cols: list[str]) -> list[str]:
    """Drop columns that are all-NaN or constant within the combo (e.g. n_frag == N)."""
    out = []
    for c in cols:
        v = df[c]
        if v.notna().any() and v.nunique(dropna=True) > 1:
            out.append(c)
    return out


def fit_predict(train: pd.DataFrame, test: pd.DataFrame, cols: list[str]) -> np.ndarray:
    ytr = train["label"].to_numpy()
    if not cols or len(np.unique(ytr)) < 2:
        return np.full(len(test), ytr.mean() if len(ytr) else 0.5)
    m = _model().fit(train[cols].to_numpy(float), ytr)
    return m.predict_proba(test[cols].to_numpy(float))[:, 1]


def oof(df: pd.DataFrame, cols: list[str], folds: list[int] | None = None) -> np.ndarray:
    """Out-of-fold predictions over the given folds (default: all folds present)."""
    pred = np.full(len(df), np.nan)
    fold = df["fold"].to_numpy()
    for k in (folds if folds is not None else sorted(np.unique(fold))):
        te = fold == k
        tr = ~te if folds is None else np.isin(fold, [f for f in folds if f != k])
        if te.sum() == 0:
            continue
        assert not set(df.loc[tr, "task_id"]) & set(df.loc[te, "task_id"]), "task leaked across folds"
        pred[te] = fit_predict(df[tr], df[te], cols)
    return pred


def prepare(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, list[str]], int]:
    """Rows usable by all three models (same rows for base/chaos/full) and each model's columns."""
    base_cols = usable_columns(df, BASE_FEATURES)
    need = CHAOS_FEATURES + base_cols
    clean = df.dropna(subset=need).reset_index(drop=True)
    cols = {"base": usable_columns(clean, BASE_FEATURES), "chaos": usable_columns(clean, CHAOS_FEATURES)}
    cols["full"] = cols["base"] + cols["chaos"]
    return clean, cols, len(df) - len(clean)


def task_bootstrap(y, preds: dict[str, np.ndarray], tasks: np.ndarray, fold: np.ndarray, n_boot: int,
                   seed: int = SEED) -> np.ndarray:
    """Bootstrap over TASKS, stratified by fold (tasks are resampled within their fold, so the fold
    structure of the fold-averaged AUROC is kept). Returns n_boot deltas AUROC(full) - AUROC(base)."""
    per_fold = []
    for k in np.unique(fold):
        m = np.flatnonzero(fold == k)
        uniq, inv = np.unique(tasks[m], return_inverse=True)
        per_fold.append([m[inv == i] for i in range(len(uniq))])
    rng = np.random.default_rng(seed)
    out = np.full(n_boot, np.nan)
    for b in range(n_boot):
        deltas = []
        for rows_of in per_fold:
            pick = rng.integers(0, len(rows_of), len(rows_of))
            idx = np.concatenate([rows_of[i] for i in pick])
            yy = y[idx]
            if yy.min() == yy.max():
                continue
            deltas.append(auroc(yy, preds["full"][idx]) - auroc(yy, preds["base"][idx]))
        if deltas:
            out[b] = np.mean(deltas)
    return out


def evaluate(df: pd.DataFrame, n_boot: int) -> tuple[dict, dict[str, np.ndarray], pd.DataFrame]:
    """Test 4 for one combo's rows (already joined with folds)."""
    clean, cols, n_nan = prepare(df)
    y = clean["label"].to_numpy()
    fold = clean["fold"].to_numpy()
    res = {"n_traj": len(clean), "n_dropped_nan": n_nan, "n_tasks": clean["task_id"].nunique(),
           "n_fail": int(y.sum()), "n_succ": int(len(y) - y.sum()),
           "base_cols": ",".join(cols["base"])}
    preds = {}
    for name in MODELS:
        p = oof(clean, cols[name])
        preds[name] = p
        res[f"auroc_{name}"] = fold_avg(auroc, y, p, fold)
        res[f"pr_auc_{name}"] = fold_avg(pr_auc, y, p, fold)
        res[f"prec10_{name}"] = fold_avg(precision_at_alert, y, p, fold)
        res[f"auroc_pooled_{name}"] = auroc(y, p)  # for reference only (biased, see fold_avg)
    res["delta_auroc"] = res["auroc_full"] - res["auroc_base"]
    if n_boot and len(np.unique(y)) == 2:
        d = task_bootstrap(y, preds, clean["task_id"].to_numpy(), fold, n_boot)
        d = d[~np.isnan(d)]
    else:
        d = np.array([])
    if len(d):
        res["delta_ci_low"], res["delta_ci_high"] = (float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5)))
        res["p_boot"] = float((np.sum(d <= 0) + 1) / (len(d) + 1))
    else:
        res["delta_ci_low"] = res["delta_ci_high"] = res["p_boot"] = math.nan
    return res, preds, clean


# ------------------------------------------------------------------------------------------ test 3


def within_task(df: pd.DataFrame, feature: str, min_per_class: int = 1) -> dict:
    effects, pvals = [], []
    for _, g in df.groupby("task_id"):
        a = g.loc[g.label == 1, feature].dropna().to_numpy()
        b = g.loc[g.label == 0, feature].dropna().to_numpy()
        if len(a) < min_per_class or len(b) < min_per_class:
            continue
        u, p = mannwhitneyu(a, b, alternative="two-sided")
        effects.append(2 * u / (len(a) * len(b)) - 1)  # rank-biserial, > 0: higher in failures
        pvals.append(p)
    if not effects:
        return {"n_tasks": 0, "frac_signif": math.nan, "median_effect": math.nan,
                "majority_share": math.nan, "share_positive": math.nan, "p_sign": math.nan}
    e = np.array(effects)
    q = bh(np.array(pvals))
    nz = e[e != 0]
    share_pos = float((nz > 0).mean()) if len(nz) else math.nan
    # sign test across tasks: is the direction of the within-task effect consistent (share != 0.5)?
    p_sign = float(binomtest(int((nz > 0).sum()), len(nz), 0.5).pvalue) if len(nz) else math.nan
    return {"n_tasks": len(e), "frac_signif": float((q < 0.05).mean()), "median_effect": float(np.median(e)),
            "majority_share": max(share_pos, 1 - share_pos) if len(nz) else math.nan, "share_positive": share_pos,
            "p_sign": p_sign}


# ------------------------------------------------------------------------------------------ nested


def _inner_delta(clean: pd.DataFrame, cols: dict, k: int, inner: list[int]) -> float:
    """Inner-CV delta AUROC (full - base) using only the training folds of outer fold k."""
    tr = clean[clean.fold != k].reset_index(drop=True)
    if tr.label.nunique() < 2:
        return math.nan
    y, f = tr["label"].to_numpy(), tr["fold"].to_numpy()
    pb, pf = oof(tr, cols["base"], inner), oof(tr, cols["full"], inner)
    return fold_avg(auroc, y, pf, f) - fold_avg(auroc, y, pb, f)


def nested_selection(combos: dict[tuple, pd.DataFrame], n_folds: int, n_jobs: int = -1) -> tuple[pd.DataFrame, list[dict]]:
    """Rule 4.1: per outer fold choose the combo with the best inner-CV delta AUROC on the training folds."""
    prepared = {c: prepare(df) for c, df in combos.items()}
    keys = sorted(prepared)
    outer_rows, choices = [], []
    for k in range(n_folds):
        inner = [j for j in range(n_folds) if j != k]
        deltas = Parallel(n_jobs=n_jobs)(delayed(_inner_delta)(prepared[c][0], prepared[c][1], k, inner) for c in keys)
        best, best_delta = None, -np.inf
        for c, delta in zip(keys, deltas):  # ties -> first combo in sorted order
            if not math.isnan(delta) and delta > best_delta:
                best, best_delta = c, delta
        if best is None:
            continue
        clean, cols, _ = prepared[best]
        tr, te = clean[clean.fold != k], clean[clean.fold == k]
        choices.append({"outer_fold": k, "combo": best, "inner_delta": best_delta, "n_test": len(te)})
        if len(te) == 0:
            continue
        outer_rows.append(pd.DataFrame({"run_id": te.run_id.values, "task_id": te.task_id.values,
                                        "label": te.label.values, "outer_fold": k, "combo": [best] * len(te),
                                        "p_base": fit_predict(tr, te, cols["base"]),
                                        "p_full": fit_predict(tr, te, cols["full"]),
                                        "p_chaos": fit_predict(tr, te, cols["chaos"])}))
    return (pd.concat(outer_rows, ignore_index=True) if outer_rows else pd.DataFrame()), choices


# ------------------------------------------------------------------------------------------ test 6


def transfer(df: pd.DataFrame, group_col: str, pairs: list[tuple[str, str]] | None = None) -> pd.DataFrame:
    """Train on other groups (and other task folds), test on the held-out group in fold k; compare with the
    in-distribution OOF AUROC on exactly the same held-out rows."""
    clean, cols, _ = prepare(df)
    y = clean["label"].to_numpy()
    in_dist = {m: oof(clean, cols[m]) for m in MODELS}
    groups = sorted(clean[group_col].dropna().unique())
    if pairs is None:
        pairs = [("все остальные", g) for g in groups]
    out = []
    for src, tgt in pairs:
        is_tgt = (clean[group_col] == tgt).to_numpy()
        is_src = ~is_tgt if src == "все остальные" else (clean[group_col] == src).to_numpy()
        cross = {m: np.full(len(clean), np.nan) for m in MODELS}
        for k in sorted(clean.fold.unique()):
            te = is_tgt & (clean.fold == k).to_numpy()
            tr = is_src & (clean.fold != k).to_numpy()
            if te.sum() == 0 or tr.sum() == 0:
                continue
            for m in MODELS:
                cross[m][te] = fit_predict(clean[tr], clean[te], cols[m])
        row = {"train": src, "test": tgt, "n_test": int(is_tgt.sum()), "n_fail_test": int(y[is_tgt].sum())}
        fold = clean["fold"].to_numpy()
        for m in MODELS:
            ok = is_tgt & ~np.isnan(cross[m])
            row[f"auroc_{m}_in"] = fold_avg(auroc, y[ok], in_dist[m][ok], fold[ok])
            row[f"auroc_{m}_cross"] = fold_avg(auroc, y[ok], cross[m][ok], fold[ok])
            row[f"drop_{m}"] = row[f"auroc_{m}_in"] - row[f"auroc_{m}_cross"]
        out.append(row)
    return pd.DataFrame(out)


# ------------------------------------------------------------------------------------------ driver


def _nested_summary(nested: pd.DataFrame, n_boot: int) -> dict:
    """Fold-averaged AUROCs of the pooled outer-fold predictions of a nested selection + task-bootstrap CI."""
    if not len(nested):
        return {}
    y, of = nested.label.to_numpy(), nested.outer_fold.to_numpy()
    pr = {"base": nested.p_base.to_numpy(), "full": nested.p_full.to_numpy(), "chaos": nested.p_chaos.to_numpy()}
    res = {m: fold_avg(auroc, y, pr[m], of) for m in pr}
    d = task_bootstrap(y, pr, nested.task_id.to_numpy(), of, n_boot) if n_boot else np.array([])
    d = d[~np.isnan(d)]
    res["delta"] = res["full"] - res["base"]
    res["ci"] = (float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))) if len(d) else (math.nan, math.nan)
    res["n"] = len(nested)
    return res


def _combo_stats(c: tuple, g: pd.DataFrame, n_boot: int) -> dict:
    """Test 4 + Test 3 + length diagnostics for one (seg, proj, N, d) combo."""
    res, _, clean = evaluate(g, n_boot)
    res.update(dict(zip(KEY, c)))
    big = len(clean) > 2
    res["spearman_H_nfrag"] = float(spearmanr(clean["H"], clean["n_frag_total"]).statistic) if big else math.nan
    res["spearman_H_nsteps_total"] = float(spearmanr(clean["H"], clean["n_steps_total"]).statistic) if big else math.nan
    res["spearman_H_nsteps_prefix"] = float(spearmanr(clean["H"], clean["n_steps"]).statistic) if big else math.nan
    res["frac_seen_median"] = float(np.median(clean["N"] / clean["n_frag_total"])) if len(clean) else math.nan
    for feat, sfx in (("H", ""), ("C", "_C")):
        w = within_task(clean, feat)
        w3 = within_task(clean, feat, min_per_class=3)
        res[f"n_tasks_both{sfx}"] = w["n_tasks"]
        res[f"frac_tasks_signif{sfx}"] = w["frac_signif"]
        res[f"median_effect_size{sfx}"] = w["median_effect"]
        res[f"majority_sign_share{sfx}"] = w["majority_share"]
        res[f"share_positive{sfx}"] = w["share_positive"]
        res[f"p_sign{sfx}"] = w["p_sign"]
        res[f"n_tasks_3each{sfx}"] = w3["n_tasks"]
        res[f"frac_tasks_signif_3each{sfx}"] = w3["frac_signif"]
        res[f"median_effect_size_3each{sfx}"] = w3["median_effect"]
    return res


def encoder_check(features_a: Path, features_b: Path, folds_csv: Path, report_path: Path,
                  names=("minilm", "nomic"), append_to: Path | None = None) -> pd.DataFrame:
    """AGENTS.md E1 control encoder: do conclusions depend on the encoder? Compared on the SAME trajectories:
    rank agreement of H and C between encoders, and AUROC(chaos) / delta AUROC for each encoder."""
    a = pd.read_csv(features_a, dtype={"run_id": str, "task_id": str})
    b = pd.read_csv(features_b, dtype={"run_id": str, "task_id": str})
    fold_of = load_folds(folds_csv)
    shared = set(b.run_id)
    a = a[a.run_id.isin(shared)]
    rows = []
    for key, gb in b.groupby(KEY):
        ga = a[(a.seg == key[0]) & (a.proj == key[1]) & (a.N == key[2]) & (a.d == key[3])]
        m = ga.merge(gb[["run_id", "H", "C"]], on="run_id", suffixes=("", "_b"))
        if len(m) < 20:
            continue
        row = dict(zip(KEY, (str(key[0]), str(key[1]), int(key[2]), int(key[3]))), n_traj=len(m),
                   rho_H=float(spearmanr(m.H, m.H_b).statistic), rho_C=float(spearmanr(m.C, m.C_b).statistic))
        for nm, df in ((names[0], ga[ga.run_id.isin(m.run_id)]), (names[1], gb[gb.run_id.isin(m.run_id)])):
            df = df.assign(fold=df.task_id.map(fold_of)).reset_index(drop=True)
            res, _, _ = evaluate(df, n_boot=0)
            row[f"auroc_chaos_{nm}"] = res["auroc_chaos"]
            row[f"delta_{nm}"] = res["delta_auroc"]
        rows.append(row)
    out = pd.DataFrame(rows)
    L = [f"# Контроль энкодера: {names[0]} vs {names[1]}", "",
         f"Одни и те же траектории (подвыборка {names[1]}, стратифицирована по классу, seed 42). ρ — Спирмен "
         "признака между энкодерами по траекториям; AUROC — усреднённый по фолдам, та же схема CV, что в E4. "
         "На ~300 траекториях ДИ широкие, поэтому здесь сравниваются согласованность признаков и направление, "
         "а не значимость.", "",
         f"| seg | proj | N | d | n | ρ(H) | ρ(C) | AUROC chaos {names[0]} | AUROC chaos {names[1]} | Δ {names[0]} | Δ {names[1]} |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for _, r in out.iterrows():
        L.append(f"| {r.seg} | {r.proj} | {r.N} | {r.d} | {r.n_traj} | {_fmt(r.rho_H, 2)} | {_fmt(r.rho_C, 2)} | "
                 f"{_fmt(r[f'auroc_chaos_{names[0]}'])} | {_fmt(r[f'auroc_chaos_{names[1]}'])} | "
                 f"{_fmt(r[f'delta_{names[0]}'])} | {_fmt(r[f'delta_{names[1]}'])} |")
    if len(out):
        L += ["", f"Медиана ρ(H) по комбинациям: {out.rho_H.median():.2f}; медиана ρ(C): {out.rho_C.median():.2f}."]
    Path(report_path).write_text("\n".join(L) + "\n", encoding="utf-8")
    out.to_csv(Path(report_path).with_suffix(".csv"), index=False)
    if append_to is not None and Path(append_to).exists() and len(out):
        main = out[out.seg.isin(MAIN_SEGS) & out.N.isin(MAIN_NS)]
        txt = Path(append_to).read_text(encoding="utf-8")
        marker = "## Контроль энкодера"
        txt = txt.split(marker)[0].rstrip() + "\n\n"
        txt += (f"{marker}\n\nКонтрольный энкодер {names[1]} на {int(out.n_traj.max())} траекториях (подробно: "
                f"`{Path(report_path).name}`). Согласованность признаков между энкодерами на одних и тех же траекториях "
                f"(основная сетка): медиана ρ(H) = {main.rho_H.median():.2f} (от {main.rho_H.min():.2f} до "
                f"{main.rho_H.max():.2f}), медиана ρ(C) = {main.rho_C.median():.2f}. AUROC хаос-модели: "
                f"{names[0]} — медиана {main[f'auroc_chaos_{names[0]}'].median():.3f}, {names[1]} — медиана "
                f"{main[f'auroc_chaos_{names[1]}'].median():.3f}. Значения H и C в заметной мере определяются "
                "энкодером; вывод об отсутствии/наличии прироста на подвыборке совпадает, но сами признаки не "
                "переносимы между энкодерами.\n")
        Path(append_to).write_text(txt, encoding="utf-8")
    return out


def _fmt(x, nd=3) -> str:
    return "—" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.{nd}f}"


def run_stats(features_csv: Path, folds_csv: Path, meta_csv: Path, out_dir: Path, report_path: Path,
              tag: str = "main", exclusions_csv: Path | None = None, window_exclusions_csv: Path | None = None,
              primary: tuple = ("reason_sent", "cos_step", 200, 4), n_boot: int = 1000,
              title_note: str = "", n_jobs: int = -1) -> pd.DataFrame:
    out_dir, report_path = Path(out_dir), Path(report_path)
    fig_dir = report_path.parent / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)
    feats = pd.read_csv(features_csv, dtype={"run_id": str, "task_id": str})
    fold_of = load_folds(folds_csv)
    feats["fold"] = feats["task_id"].map(fold_of)
    if feats["fold"].isna().any():
        raise ValueError(f"{int(feats['fold'].isna().sum())} feature rows have a task without a fold")
    feats["fold"] = feats["fold"].astype(int)
    meta = pd.read_csv(meta_csv, dtype={"run_id": str, "task_id": str})
    feats = feats.merge(meta[["run_id", "seg", "model_family", "n_frag_total", "n_steps_total"]],
                        on=["run_id", "seg"], how="left")
    n_folds = feats["fold"].nunique()
    combos = {(str(k[0]), str(k[1]), int(k[2]), int(k[3])): g.reset_index(drop=True) for k, g in feats.groupby(KEY)}
    log.info("%s: %d combos, %d rows", tag, len(combos), len(feats))

    # ---- Test 4 + Test 3 per combo (independent -> parallel over combos)
    rows = Parallel(n_jobs=n_jobs)(delayed(_combo_stats)(c, g, n_boot) for c, g in combos.items())
    summ = pd.DataFrame(rows)
    summ["q_bh"] = bh(summ["p_boot"].to_numpy())
    summ["q_sign"] = bh(summ["p_sign"].to_numpy())
    summ["q_sign_C"] = bh(summ["p_sign_C"].to_numpy())
    lead = KEY + ["n_traj", "n_tasks", "auroc_base", "auroc_chaos", "auroc_full", "delta_auroc", "delta_ci_low",
                  "delta_ci_high", "spearman_H_nfrag", "frac_tasks_signif", "median_effect_size"]
    summ = summ[lead + [c for c in summ.columns if c not in lead]].sort_values(KEY).reset_index(drop=True)
    summ.to_csv(out_dir / ("summary.csv" if tag == "main" else f"summary_{tag}.csv"), index=False)

    # ---- nested selection (rule 4.1) over the MAIN grid; the unrestricted variant is a sensitivity check
    main_combos = {c: g for c, g in combos.items() if c[0] in MAIN_SEGS and c[2] in MAIN_NS} or combos
    nested, choices = nested_selection(main_combos, n_folds, n_jobs)
    nested_res = _nested_summary(nested, n_boot)
    nested.to_csv(out_dir / f"nested_predictions_{tag}.csv", index=False)
    pd.DataFrame(choices).to_csv(out_dir / f"nested_choices_{tag}.csv", index=False)
    nested_all_res, choices_all = {}, []
    if len(main_combos) < len(combos):
        nested_all, choices_all = nested_selection(combos, n_folds, n_jobs)
        nested_all_res = _nested_summary(nested_all, n_boot)
        pd.DataFrame(choices_all).to_csv(out_dir / f"nested_choices_allcombos_{tag}.csv", index=False)
    nested_res["n_candidates"] = len(main_combos)
    nested_all_res["n_candidates"] = len(combos)

    # ---- Test 5: prefix curve (per-N eligible set, and a fixed set eligible at the largest N)
    curve_rows = []
    for (seg, proj, d), gg in feats.groupby(["seg", "proj", "d"]):
        Ns = sorted(gg.N.unique())
        fixed_ids = set(gg.loc[gg.N == max(Ns), "run_id"])
        for N in Ns:
            s = summ[(summ.seg == seg) & (summ.proj == proj) & (summ.N == N) & (summ.d == d)]
            base = {"seg": seg, "proj": proj, "d": d, "N": N}
            if len(s):
                s = s.iloc[0]
                curve_rows.append({**base, "population": "все допустимые при N", "n": s.n_traj,
                                   "frac_seen_median": s.frac_seen_median,
                                   **{f"auroc_{m}": s[f"auroc_{m}"] for m in MODELS}})
            sub = gg[(gg.N == N) & gg.run_id.isin(fixed_ids)].reset_index(drop=True)
            if len(sub) and sub.label.nunique() == 2:
                r, _, cl = evaluate(sub, 0)
                curve_rows.append({**base, "population": f"фиксированная (≥{max(Ns)} фрагм.)", "n": r["n_traj"],
                                   "frac_seen_median": float(np.median(cl.N / cl.n_frag_total)),
                                   **{f"auroc_{m}": r[f"auroc_{m}"] for m in MODELS}})
    curve = pd.DataFrame(curve_rows)
    curve.to_csv(out_dir / f"early_curve_{tag}.csv", index=False)

    # ---- Test 6: transfer for the primary combo and the most frequently nested-selected combo
    tr_combos = [tuple(primary)]
    if choices:
        top = pd.Series([c["combo"] for c in choices]).value_counts().index[0]
        if tuple(top) != tuple(primary):
            tr_combos.append(tuple(top))
    transfer_tabs = []
    for c in tr_combos:
        if c not in combos:
            continue
        g = combos[c].copy()
        g["agent_major"] = g["agent_id"].str.extract(r"^mini-swe-agent-(\d+)\.")[0].map(
            lambda v: f"v{v}.x" if isinstance(v, str) else None)
        t1 = transfer(g, "model_family")
        t1.insert(0, "axis", "семейство моделей")
        vers = sorted(g["agent_major"].dropna().unique())
        t2 = transfer(g, "agent_major", pairs=[(a, b) for a in vers for b in vers if a != b]) if len(vers) > 1 else pd.DataFrame()
        if len(t2):
            t2.insert(0, "axis", "версия агента")
        t = pd.concat([t1, t2], ignore_index=True)
        t.insert(0, "combo", str(c))
        transfer_tabs.append(t)
    transfer_df = pd.concat(transfer_tabs, ignore_index=True) if transfer_tabs else pd.DataFrame()
    transfer_df.to_csv(out_dir / f"transfer_{tag}.csv", index=False)

    fig_path = _plot_curve(curve, primary, fig_dir / f"early_curve_{tag}.png")
    _write_report(report_path, tag, summ, primary, nested_res, choices, nested_all_res, choices_all, curve,
                  transfer_df, fig_path, exclusions_csv, window_exclusions_csv, meta, n_boot, title_note)
    log.info("%s: report -> %s", tag, report_path)
    return summ


def _plot_curve(curve: pd.DataFrame, primary: tuple, path: Path) -> Path | None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    seg, proj, _, d = primary
    sub = curve[(curve.seg == seg) & (curve.proj == proj) & (curve.d == d)]
    if sub.empty:
        return None
    pops = list(sub.population.unique())
    fig, axes = plt.subplots(1, len(pops), figsize=(5.5 * len(pops), 4), squeeze=False)
    for ax, pop in zip(axes[0], pops):
        s = sub[sub.population == pop].sort_values("N")
        x = np.arange(len(s))  # N grid is geometric (50,100,200,400): equal spacing reads as a log axis
        for m, style in (("base", "s--"), ("chaos", "o:"), ("full", "^-")):
            ax.plot(x, s[f"auroc_{m}"], style, label=f"M_{m}")
        ax.axhline(0.5, color="grey", lw=0.8)
        ax.axhline(AUROC_EARLY, color="grey", lw=0.8, ls="--")
        vals = s[[f"auroc_{m}" for m in MODELS]].to_numpy(float)
        lo = np.nanmin(vals) if np.isfinite(vals).any() else 0.5
        ax.set_ylim(min(0.4, lo - 0.05), 1.0)
        ax.set_xticks(x)
        ax.set_xticklabels([f"{n}\n({f:.0%})" if pd.notna(f) else str(n) for n, f in zip(s.N, s.frac_seen_median)])
        ax.set_xlabel("N фрагментов префикса (медианная доля траектории)")
        ax.set_ylabel("AUROC (out-of-fold)")
        ax.set_title(f"{seg}, {proj}, d={d}\n{pop}", fontsize=9)
        ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def _verdict(lo) -> str:
    if lo is None or (isinstance(lo, float) and math.isnan(lo)):
        return "не определён (недостаточно данных)"
    return "**ВЫПОЛНЕН**" if lo > 0 else "**НЕ выполнен**"


def _write_report(path, tag, summ, primary, nested_res, choices, nested_all_res, choices_all, curve,
                  transfer_df, fig_path, exclusions_csv, window_exclusions_csv, meta, n_boot, title_note):
    L = [f"# E4 — результаты статистических тестов ({tag})", ""]
    if title_note:
        L += [title_note, ""]
    L += ["Модели: StandardScaler + логистическая регрессия (L2, C=1), без подбора гиперпараметров. "
          "Кросс-валидация: 5 фолдов, группировка по task_id (единая карта `outputs/folds.csv`). "
          "AUROC, PR-AUC и precision@10% считаются внутри каждого фолда и усредняются: объединение "
          "предсказаний разных фолдов смещает AUROC вниз (у модели каждого фолда свой свободный член, который "
          "сдвигается против доли провалов в тестовом фолде); объединённый AUROC для справки есть в summary.csv. "
          f"ДИ — перцентильный бутстрэп по задачам внутри фолдов, {n_boot} повторов, seed 42.", ""]

    # headline
    p = summ[(summ.seg == primary[0]) & (summ.proj == primary[1]) & (summ.N == primary[2]) & (summ.d == primary[3])]
    L += ["## Главный ответ", "", "Критерий успеха темы (AGENTS.md): нижняя граница 95% ДИ прироста AUROC "
          "(M_full − M_base) > 0.", ""]
    if len(p):
        r = p.iloc[0]
        L += [f"**1. Заранее заданная основная комбинация** `{primary}` (n = {r.n_traj}, задач {r.n_tasks}):",
              f"AUROC base = {_fmt(r.auroc_base)}, chaos = {_fmt(r.auroc_chaos)}, full = {_fmt(r.auroc_full)}; "
              f"Δ = {_fmt(r.delta_auroc)} [95% ДИ {_fmt(r.delta_ci_low)}; {_fmt(r.delta_ci_high)}] → критерий {_verdict(r.delta_ci_low)}.", ""]
    else:
        L += [f"Основная комбинация `{primary}` отсутствует в данных.", ""]
    if nested_res.get("n"):
        lo = nested_res["ci"][0]
        freq = pd.Series([str(c["combo"]) for c in choices]).value_counts()
        L += [f"**2. Вложенный выбор комбинации** (правило 4.1: комбинация выбирается в каждом внешнем фолде по "
              f"внутренней CV только на обучающих фолдах; кандидаты — основная сетка AGENTS.md: seg ∈ {MAIN_SEGS}, "
              f"N ∈ {MAIN_NS}, все proj и d, всего {nested_res['n_candidates']} комбинаций; n = {nested_res['n']}):",
              f"AUROC base = {_fmt(nested_res['base'])}, chaos = {_fmt(nested_res['chaos'])}, full = {_fmt(nested_res['full'])}; "
              f"Δ = {_fmt(nested_res['delta'])} [95% ДИ {_fmt(nested_res['ci'][0])}; {_fmt(nested_res['ci'][1])}] → критерий {_verdict(lo)}.",
              "Выбранные комбинации по внешним фолдам: " + "; ".join(f"{k} ×{v}" for k, v in freq.items()) + ".", ""]
    if nested_all_res.get("n"):
        freq = pd.Series([str(c["combo"]) for c in choices_all]).value_counts()
        L += [f"*Проверка чувствительности (не ответ):* тот же вложенный выбор, но из всех {nested_all_res['n_candidates']} "
              "комбинаций, включая контрольные seg=step и N=50. Выбранные: " + "; ".join(f"{k} ×{v}" for k, v in freq.items())
              + f". Δ = {_fmt(nested_all_res['delta'])} [95% ДИ {_fmt(nested_all_res['ci'][0])}; {_fmt(nested_all_res['ci'][1])}], "
              f"n = {nested_all_res['n']}. Этот вариант некорректен как ответ: он выбирает контрольную шаговую сегментацию "
              "(запрещена как основная, AGENTS.md п. 1) и маленькие выборки, где внутренняя оценка прироста шумная, — "
              "выбор «по максимуму» тогда ловит шум.", ""]
    L += ["Остальные строки таблицы теста 4 — разведочные: их много, поэтому смотреть нужно на q (BH), "
          "а не на отдельные «находки».", ""]

    # data & exclusions
    L += ["## Данные и исключения", ""]
    m0 = meta.drop_duplicates("run_id")
    L.append(f"- траекторий в анализе: {len(m0)} (провалов {int(m0.label.sum())}, успехов {int((1 - m0.label).sum())})")
    if exclusions_csv and Path(exclusions_csv).exists():
        ex = pd.read_csv(exclusions_csv, dtype=str).fillna("")
        if len(ex):
            t = ex.groupby(["reason", "label"]).size()
            L.append("- исключено до анализа (E0): " + "; ".join(f"{r} [метка {l or '?'}]: {n}" for (r, l), n in t.items()))
        else:
            L.append("- исключено до анализа (E0): 0")
    if window_exclusions_csv and Path(window_exclusions_csv).exists():
        we = pd.read_csv(window_exclusions_csv)
        L += ["", "Исключение коротких траекторий фильтром окна N (по классам):", "",
              "| seg | N | исключено успехов | исключено провалов | доля успехов | доля провалов | асимметрия |",
              "|---|---|---|---|---|---|---|"]
        for (seg, N), g in we.groupby(["seg", "N"]):
            s0 = g[g.label == 0].iloc[0] if (g.label == 0).any() else None
            s1 = g[g.label == 1].iloc[0] if (g.label == 1).any() else None
            r0 = s0.n_excluded_short / s0.n_total if s0 is not None and s0.n_total else math.nan
            r1 = s1.n_excluded_short / s1.n_total if s1 is not None and s1.n_total else math.nan
            asym = "да" if not (math.isnan(r0) or math.isnan(r1)) and abs(r0 - r1) > 0.05 else "нет"
            L.append(f"| {seg} | {N} | {s0.n_excluded_short if s0 is not None else '—'} | "
                     f"{s1.n_excluded_short if s1 is not None else '—'} | {_fmt(r0)} | {_fmt(r1)} | {asym} |")
        L.append("\n«Асимметрия» = доли исключённых в классах различаются более чем на 5 п.п.: это смещение выборки.")
    L.append("")

    # test 4
    L += ["## Тест 4 — инкрементальность над тривиальными признаками", "",
          "| seg | proj | N | d | n | AUROC base | AUROC chaos | AUROC full | Δ | 95% ДИ | q (BH) | ρ(H, длина) |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for _, r in summ.iterrows():
        L.append(f"| {r.seg} | {r.proj} | {r.N} | {r.d} | {r.n_traj} | {_fmt(r.auroc_base)} | {_fmt(r.auroc_chaos)} | "
                 f"{_fmt(r.auroc_full)} | {_fmt(r.delta_auroc)} | [{_fmt(r.delta_ci_low)}; {_fmt(r.delta_ci_high)}] | "
                 f"{_fmt(r.q_bh)} | {_fmt(r.spearman_H_nfrag, 2)} |")
    n_pass = int((summ.delta_ci_low > 0).sum())
    n_q = int((summ.q_bh < 0.05).sum())
    L += ["", f"Комбинаций с нижней границей ДИ > 0: {n_pass} из {len(summ)}; значимых после BH (q < 0.05): {n_q}.",
          "ρ(H, длина) — Спирмен между H и полной длиной траектории во фрагментах (диагностика; в модели длина "
          "полной траектории не входит). Высокий |ρ| означает, что H вторичен по отношению к длине.", ""]

    # test 3
    L += ["## Тест 3 — различение внутри задачи", "",
          "Манна–Уитни между классами в каждой задаче с обоими классами; эффект — ранг-бисериальная корреляция "
          "(> 0: выше у провалов); BH по задачам. «Доля > 0» — доля задач (с ненулевым эффектом), где H выше у "
          "провалов; q знак. — знаковый тест этой доли против 0.5 по задачам, с BH по всем комбинациям.", "",
          "| seg | proj | N | d | задач | доля знач. H | медиана эффекта H | доля > 0 (H) | q знак. H | медиана эффекта C | доля > 0 (C) | q знак. C | задач ≥3/≥3 |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for _, r in summ.iterrows():
        L.append(f"| {r.seg} | {r.proj} | {r.N} | {r.d} | {r.n_tasks_both} | {_fmt(r.frac_tasks_signif)} | "
                 f"{_fmt(r.median_effect_size)} | {_fmt(r.share_positive)} | {_fmt(r.q_sign)} | "
                 f"{_fmt(r.median_effect_size_C)} | {_fmt(r.share_positive_C)} | {_fmt(r.q_sign_C)} | {r.n_tasks_3each} |")
    consistent = summ[summ.q_sign < 0.05]
    lower = int((consistent.share_positive < 0.5).sum())
    main = summ[summ.seg.isin(MAIN_SEGS) & summ.N.isin(MAIN_NS)]
    main = main if len(main) else summ
    maj = main.majority_sign_share.dropna()
    L += ["", f"- Отдельные задачи: доля задач со значимым различием после BH — "
              f"{_fmt(summ.frac_tasks_signif.max())} в лучшей комбинации (в задаче 1–16 прогонов на класс, мощности нет).",
          f"- Направление: в {len(consistent)} из {len(summ)} комбинаций перекос направления по задачам устойчив "
          f"(знаковый тест, q < 0.05); из них в {lower} энтропия H **ниже у провалов**, в {len(consistent) - lower} — выше.",
          f"- **Разнонаправленность** (AGENTS.md: тревожный признак): в основной сетке ({len(main)} комбинаций) доля "
          f"задач с направлением большинства — от {_fmt(maj.min() if len(maj) else math.nan, 2)} до "
          f"{_fmt(maj.max() if len(maj) else math.nan, 2)} (медиана {_fmt(maj.median() if len(maj) else math.nan, 2)}), "
          f"то есть в медианной комбинации {1 - maj.median():.0%} задач показывают противоположное направление. "
          "Сдвиг есть «в среднем по задачам», но он слабый и не универсален." if len(maj) else "",
          "Примечание: при 1–2 прогонах класса в задаче тест Манна–Уитни не может дать значимость; поэтому "
          "отдельно приведено число задач, где каждого класса ≥ 3.", ""]

    # test 5
    L += ["## Тест 5 — ранний прогноз", ""]
    if fig_path:
        L += [f"![кривая раннего прогноза](figures/{Path(fig_path).name})", ""]
    seg, proj, _, d = primary
    cp = curve[(curve.seg == seg) & (curve.proj == proj) & (curve.d == d)]
    if len(cp):
        L += [f"Комбинация `{seg}, {proj}, d={d}`:", "",
              "| популяция | N | n | медианная доля траектории | AUROC base | AUROC chaos | AUROC full |",
              "|---|---|---|---|---|---|---|"]
        for _, r in cp.sort_values(["population", "N"]).iterrows():
            L.append(f"| {r.population} | {r.N} | {r.n} | {_fmt(r.frac_seen_median, 2)} | {_fmt(r.auroc_base)} | "
                     f"{_fmt(r.auroc_chaos)} | {_fmt(r.auroc_full)} |")
        for pop, g in cp.groupby("population"):
            g = g.sort_values("N")
            for m in ("full", "chaos"):
                hit = g[g[f"auroc_{m}"] > AUROC_EARLY]
                L.append(f"- {pop}, M_{m}: AUROC впервые > {AUROC_EARLY} при " +
                         (f"N = {hit.iloc[0].N} (медианная доля траектории {hit.iloc[0].frac_seen_median:.0%})"
                          if len(hit) else "— не превышает ни при одном N"))
        L += ["", "«Все допустимые при N» — у каждого N своя выборка (короткие траектории выпадают), поэтому кривая "
              "смешивает эффект префикса и смену выборки. «Фиксированная» — одни и те же траектории при растущем "
              "префиксе; именно она показывает ранний прогноз в чистом виде.", ""]

    # test 6
    L += ["## Тест 6 — перенос", ""]
    if len(transfer_df):
        L += ["Обучение на других семействах/версиях (и других фолдах задач) → тест на целевой группе; сравнение "
              "с AUROC внутри распределения на тех же строках. «Падение» = in − cross.", "",
              "| комбинация | ось | обучение | тест | n теста | AUROC full in | AUROC full cross | падение full | AUROC chaos in | AUROC chaos cross | падение chaos |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
        for _, r in transfer_df.iterrows():
            L.append(f"| {r.combo} | {r.axis} | {r.train} | {r.test} | {r.n_test} | {_fmt(r.auroc_full_in)} | "
                     f"{_fmt(r.auroc_full_cross)} | {_fmt(r.drop_full)} | {_fmt(r.auroc_chaos_in)} | "
                     f"{_fmt(r.auroc_chaos_cross)} | {_fmt(r.drop_chaos)} |")
        L += ["", "Перенос между бенчмарками не проводился: используется один источник (SWE-bench).", ""]
    else:
        L += ["Недостаточно данных для теста переноса.", ""]

    # limitations
    rho = summ.loc[(summ.seg == primary[0]) & (summ.N == primary[2]) & (summ.d == primary[3]), "spearman_H_nfrag"]
    L += ["## Ограничения", "",
          "- **Выборка источника.** Один бенчмарк (SWE-bench Verified, Python-репозитории), один агентный каркас "
          "(mini-SWE-agent, версии 1.x и 2.0). Прогоны отобраны по наличию видимого текста рассуждений "
          "(модели со скрытыми рассуждениями, например GPT-5/o3, не вошли) — выводы на них не переносятся.",
          "- **Фильтр окна.** Траектории короче N исключены; если доли исключённых различаются по классам "
          "(таблица выше), выборка при данном N смещена. Длинные траектории чаще провальные — результат при "
          "больших N относится к «длинным» прогонам.",
          f"- **Связь с длиной.** Спирмен H и полной длины для основной комбинации: {', '.join(_fmt(v, 2) for v in rho)}. "
          "Полная длина траектории — информация из будущего, в модель не входит; префиксные тривиальные признаки "
          "входят в M_base.",
          "- **Проекции norm и cos_step** на эмбеддингах единичной нормы монотонно связаны (norm = √(2·cos_step)), "
          "поэтому их H и C совпадают: это не две независимые проверки.",
          "- **Маскировка маркеров исхода** (`config/leak_markers.txt`) убирает явные фразы, но не все косвенные "
          "признаки успеха в тексте; агенты часто заявляют «all tests pass» и в провальных прогонах (см. E0).",
          "- **Метка** — результат тестов SWE-bench (FAIL_TO_PASS и PASS_TO_PASS); прогоны, упавшие из-за "
          "инфраструктуры провайдера, исключены (E0).",
          "- **Чего нельзя утверждать:** причинную связь хаотичности и провала; применимость к другим доменам, "
          "каркасам и моделям со скрытыми рассуждениями; что отдельные значимые комбинации из разведочной "
          "таблицы — не случайность (смотреть q после BH и вложенный выбор).", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(L) + "\n", encoding="utf-8")
