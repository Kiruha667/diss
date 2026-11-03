"""E9 (reports/E9_PROTOCOL.md): best-of-k selection by model risk on the E8 data. No LLM calls, no downloads.

  python -m src.e9_best_of_k  -> outputs/e9_results.csv, e9_per_task.csv.gz, e9_risks.csv.gz, e9_link_e8.csv,
                                 e9_summary.json; reports/figures/e9_best_of_k.png

Pool of a task = the 10 attempts (run_0..run_9) of a setting. submitted = non-empty patch; solved = in resolved_ids
(empty patch, unresolved and evaluation errors are not solved). Risks (out-of-fold by task, only for submitted
attempts): P1 = S trained on the leaderboard corpus; P2 = S and P3 = LEN trained within the setting,
GroupKFold(5, shuffle, seed 42) on the E8 population of the setting; evaluation-error attempts get the risk of the fold
model of their task (their features: E8 code on the downloaded trajectories). Rules: random, submitted-random, P1/P2/P3
(lowest risk among submitted; exact expectation over ties), oracle (pass@k); exact expectations, 200 subsets of k of
10 per task (default_rng(42), shared by all rules and settings) for k < 10. 95% CIs: cluster bootstrap over tasks
(1000, default_rng(42) per scope), shared by all rules and k.
"""
from __future__ import annotations

import json
import sys
import time

import numpy as np
import pandas as pd

from src.adapters.agentic_evals import SETTINGS, iter_raw, labels_of, run_dirs, to_record
from src.common import OUTPUTS, REPORTS, ROOT, SEED, get_logger, load_leak_markers
from src.e8_repeats import OUT_DIR, SHORT, new_model, step_dicts, unit_counts
from src.segment import compile_markers

sys.path.insert(0, str(ROOT))
from demo_lib.features import FEATURES, LEN, trajectory_features  # noqa: E402

log = get_logger("e9")
KS = (2, 3, 5, 10)
N_SUB = 200
N_BOOT = 1000
N_RUNS = 10
RISKS = ("P1", "P2", "P3")
RULES = ("random", "submitted-random") + RISKS + ("oracle",)
K_MAIN = 5


# ------------------------------------------------------------------------------------------ attempts and risks

def attempts() -> pd.DataFrame:
    """One row per attempt (setting x task x run): submitted, solved, eval_error."""
    rows = []
    for setting in SETTINGS:
        for rd in run_dirs(setting):
            j = int(rd.name.split("_")[1])
            for t, y in labels_of(rd).items():
                rows.append({"setting": setting, "task_id": t, "j": j, "run": rd.name,
                             "run_id": f"{setting}/{rd.name}/{t}", "submitted": y != "empty_patch",
                             "solved": y == 0, "eval_error": y == "eval_error", "label": y if isinstance(y, int) else -1})
    df = pd.DataFrame(rows)
    assert not (df.label == -1).sum() - (df.eval_error | ~df.submitted).sum(), "unexpected status"
    n = df.groupby(["setting", "task_id"]).size()
    assert (n == N_RUNS).all() and len(n) == 3 * 500, "every task must have 10 attempts in every setting"
    return df.sort_values(["setting", "task_id", "j"]).reset_index(drop=True)


def eval_error_features(att: pd.DataFrame) -> pd.DataFrame:
    """S features of the evaluation-error attempts, by the E8 code from the downloaded trajectories."""
    mre = compile_markers(load_leak_markers())
    err = att[att.eval_error]
    out = []
    for (setting, run), g in err.groupby(["setting", "run"]):
        tasks = set(g.task_id)
        rd = next(r for r in run_dirs(setting) if r.name == run)
        for raw in iter_raw(rd):
            if raw["instance_id"] in tasks:
                rec = to_record(setting, run, raw, None)
                out.append({"run_id": rec["run_id"], **trajectory_features(step_dicts(rec, mre))})
    feats = pd.DataFrame(out)
    assert len(feats) == len(err), (len(feats), len(err))
    return feats


def risks(att: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """P1, P2, P3 for every submitted attempt (NaN otherwise) and the fold of its task within the setting."""
    from sklearn.model_selection import GroupKFold

    pop = pd.read_parquet(OUT_DIR / "runs.parquet").sort_values("run_id").reset_index(drop=True)
    feats = pd.concat([pop[["run_id"] + FEATURES], eval_error_features(att)], ignore_index=True)
    sub = att[att.submitted].merge(feats, on="run_id", how="left")
    assert sub[FEATURES].notna().all().all() and len(sub) == int(att.submitted.sum())
    info = {}
    # P1: S on the leaderboard corpus, no refit (= P1 of E8)
    lb = pd.read_parquet(ROOT / "data" / "demo" / "runs.parquet")
    lb = lb[lb.submitted == 1].sort_values("run_id").reset_index(drop=True)
    p1 = new_model().fit(lb[FEATURES].to_numpy(float), (1 - lb.resolved).to_numpy())
    sub["P1"] = p1.predict_proba(sub[FEATURES].to_numpy(float))[:, 1]
    e8 = pd.read_csv(OUTPUTS / "e8_runs.csv.gz", usecols=["run_id", "risk_P1"]).merge(sub[["run_id", "P1"]], on="run_id")
    info["p1_max_abs_diff_vs_e8"] = float((e8.risk_P1 - e8.P1).abs().max())
    assert len(e8) == len(pop) and info["p1_max_abs_diff_vs_e8"] < 1e-7, "P1 differs from E8"
    # P2, P3: within the setting, folds by task on the E8 population of the setting
    sub["P2"] = sub["P3"] = np.nan
    sub["fold"] = -1
    info["eval_error_without_fold"] = 0
    for setting in SETTINGS:
        ps = pop[pop.setting == setting].reset_index(drop=True)
        y = ps.label.to_numpy()
        fold_of = {}
        for k, (_, te) in enumerate(GroupKFold(n_splits=5, shuffle=True, random_state=SEED)
                                    .split(ps, y, ps.task_id.to_numpy())):
            fold_of.update({t: k for t in ps.task_id.iloc[te]})
        in_s = (sub.setting == setting).to_numpy()
        sub.loc[in_s, "fold"] = sub.loc[in_s, "task_id"].map(fold_of).fillna(-1).astype(int)
        for name, cols in (("P2", FEATURES), ("P3", LEN)):
            for k in range(5):
                tr = ps.task_id.map(fold_of).to_numpy() != k
                m = new_model().fit(ps.loc[tr, cols].to_numpy(float), y[tr])
                te = in_s & (sub.fold == k).to_numpy()
                sub.loc[te, name] = m.predict_proba(sub.loc[te, cols].to_numpy(float))[:, 1]
            orphan = in_s & (sub.fold == -1).to_numpy()  # eval-error attempt of a task without labelled runs
            if orphan.any():
                m = new_model().fit(ps[cols].to_numpy(float), y)
                sub.loc[orphan, name] = m.predict_proba(sub.loc[orphan, cols].to_numpy(float))[:, 1]
                info["eval_error_without_fold"] += int(orphan.sum()) if name == "P2" else 0
    assert sub[list(RISKS)].notna().all().all()
    out = att.merge(sub[["run_id", "fold"] + list(RISKS)], on="run_id", how="left")
    return out, info


# ------------------------------------------------------------------------------------------ selection

def subsets() -> dict[int, np.ndarray]:
    """Attempt indices (500, N_SUB, k): first k positions of random permutations; the same for all settings."""
    rng = np.random.default_rng(SEED)
    out = {k: rng.random((500, N_SUB, N_RUNS)).argsort(-1)[..., :k] for k in KS if k < N_RUNS}
    out[N_RUNS] = np.broadcast_to(np.arange(N_RUNS), (500, 1, N_RUNS))
    return out


def rule_values(solved: np.ndarray, submitted: np.ndarray, risk: dict[str, np.ndarray], idx: np.ndarray) -> dict:
    """Exact expected 'solved' of every rule for every task, averaged over its subsets: {rule: (n_tasks,)}."""
    t = np.arange(solved.shape[0])[:, None, None]
    sv, sb = solved[t, idx].astype(float), submitted[t, idx]
    nsb = sb.sum(-1)
    anyb = nsb > 0
    out = {"random": sv.mean(-1),
           "submitted-random": np.where(anyb, (sv * sb).sum(-1) / np.maximum(nsb, 1), sv.mean(-1))}
    for p in RISKS:
        r = np.where(sb, risk[p][t, idx], np.inf)
        tie = sb & (r == r.min(-1, keepdims=True))
        out[p] = np.where(anyb, (sv * tie).sum(-1) / np.maximum(tie.sum(-1), 1), sv.mean(-1))
    out["oracle"] = sv.max(-1)
    return {k: v.mean(-1) for k, v in out.items()}


def per_task(att: pd.DataFrame) -> pd.DataFrame:
    subs = subsets()
    rows = []
    for setting in SETTINGS:
        a = att[att.setting == setting].sort_values(["task_id", "j"])
        tasks = a.task_id.unique()
        assert len(tasks) == 500
        shape = (500, N_RUNS)
        solved = a.solved.to_numpy().reshape(shape)
        submitted = a.submitted.to_numpy().reshape(shape)
        risk = {p: a[p].to_numpy(float).reshape(shape) for p in RISKS}
        mixed = (solved.sum(1) > 0) & (solved.sum(1) < N_RUNS)
        for k in KS:
            vals = rule_values(solved, submitted, risk, subs[k])
            for rule, v in vals.items():
                rows.append(pd.DataFrame({"setting": setting, "task_id": tasks, "mixed": mixed, "k": k, "rule": rule,
                                          "value": v}))
    return pd.concat(rows, ignore_index=True)


# ------------------------------------------------------------------------------------------ metrics

def holm(p: dict[str, float]) -> dict[str, float]:
    keys = sorted(p, key=p.get)
    out, run = {}, 0.0
    for i, key in enumerate(keys):
        run = max(run, min(1.0, (len(keys) - i) * p[key]))
        out[key] = run
    return out


def scope_rows(pt: pd.DataFrame, scope: str, mixed_only: bool) -> list[dict]:
    d = pt if scope == "all" else pt[pt.setting == scope]
    if mixed_only:
        d = d[d.mixed]
    wide = d.pivot_table(index=["setting", "task_id"], columns=["k", "rule"], values="value")
    tasks = wide.index.get_level_values("task_id").to_numpy()
    utask, tix = np.unique(tasks, return_inverse=True)
    cnt = np.bincount(tix).astype(float)
    sums = {c: np.bincount(tix, weights=wide[c].to_numpy()) for c in wide.columns}
    rng = np.random.default_rng(SEED)
    W = np.stack([np.bincount(rng.integers(0, len(utask), len(utask)), minlength=len(utask)) for _ in range(N_BOOT)])
    wc = W @ cnt
    boot = {c: (W @ s) / wc for c, s in sums.items()}
    point = {c: s.sum() / cnt.sum() for c, s in sums.items()}
    ci = lambda v: np.percentile(v, [2.5, 97.5])  # noqa: E731
    name = scope + ("|mixed" if mixed_only else "")
    rows = []
    for k in KS:
        sr, orc = (k, "submitted-random"), (k, "oracle")
        for rule in RULES:
            c = (k, rule)
            dlt = (boot[c] - boot[sr]) * 100
            gap = (boot[c] - boot[sr]) / (boot[orc] - boot[sr])
            row = {"scope": name, "n_units": len(wide), "n_tasks": len(utask), "k": k, "rule": rule,
                   "solved": point[c], "solved_lo": ci(boot[c])[0], "solved_hi": ci(boot[c])[1],
                   "delta_pp": (point[c] - point[sr]) * 100, "delta_lo": ci(dlt)[0], "delta_hi": ci(dlt)[1],
                   "gap_closed": (point[c] - point[sr]) / (point[orc] - point[sr]),
                   "gap_lo": ci(gap)[0], "gap_hi": ci(gap)[1], "p_one_sided": np.nan}
            if rule in RISKS:
                row["p_one_sided"] = (1 + int((dlt <= 0).sum())) / (N_BOOT + 1)
            rows.append(row)
        dd = (boot[(k, "P2")] - boot[(k, "P3")]) * 100
        rows.append({"scope": name, "n_units": len(wide), "n_tasks": len(utask), "k": k, "rule": "P2-P3",
                     "delta_pp": (point[(k, "P2")] - point[(k, "P3")]) * 100, "delta_lo": ci(dd)[0],
                     "delta_hi": ci(dd)[1]})
    return rows


def link_e8(att: pd.DataFrame, res: pd.DataFrame) -> pd.DataFrame:
    """Δ(P2) at k = 5 vs the within-unit AUROC of E8 (pooled P2) and of P2 / P3 trained within the setting."""
    e8 = pd.read_csv(OUTPUTS / "e8_metrics.csv")
    e8 = e8[(e8.predictor == "P2") & (e8.metric == "within_unit_auroc")].set_index("scope")
    rows = []
    for setting in SETTINGS:
        a = att[(att.setting == setting) & (att.label >= 0)].sort_values("run_id").reset_index(drop=True)
        y = a.label.to_numpy()
        units = list(pd.Series(np.arange(len(a))).groupby(a.task_id.to_numpy()).apply(np.array))
        auc = {}
        for p in ("P2", "P3"):
            c, n = unit_counts(y, a[p].to_numpy(float), units)
            auc[p] = c.sum() / n.sum()
        r = res[(res.scope == setting) & (res.k == K_MAIN) & (res.rule == "P2")].iloc[0]
        rows.append({"setting": setting, "delta_P2_k5_pp": r.delta_pp, "delta_lo": r.delta_lo, "delta_hi": r.delta_hi,
                     "e8_within_auroc_P2_pooled": e8.loc[setting, "value"],
                     "within_auroc_P2_in_setting": auc["P2"], "within_auroc_P3_in_setting": auc["P3"]})
    return pd.DataFrame(rows)


def figure(res: pd.DataFrame, att: pd.DataFrame) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    style = {"random": ("#9e9e9e", "-", "случайная попытка"),
             "submitted-random": ("#555555", "--", "случайная среди отправивших"),
             "P2": ("#1f4e9c", "-", "наименьший риск P2 (длина + структура)"),
             "oracle": ("#2ca02c", ":", "oracle: решена хотя бы одна (pass@k)")}
    scopes = list(SETTINGS) + ["all"]
    fig, axes = plt.subplots(2, 2, figsize=(11, 8), sharex=True)
    for ax, scope in zip(axes.flat, scopes):
        a = att if scope == "all" else att[att.setting == scope]
        p1 = a.solved.mean()  # k = 1: every rule = one random attempt
        for rule, (col, ls, lab) in style.items():
            r = res[(res.scope == scope) & (res.rule == rule)].sort_values("k")
            xs, ys = [1] + list(r.k), [p1] + list(r.solved)
            ax.plot(xs, np.array(ys) * 100, color=col, ls=ls, lw=2, marker="o", ms=4, label=lab)
        ax.set_title("три настройки вместе" if scope == "all" else f"nano-agent × {SHORT[scope]}")
        ax.set_xscale("log")
        ax.set_xticks(list((1,) + KS), [str(k) for k in (1,) + KS])
        ax.minorticks_off()
        ax.set_ylabel("решено задач, %")
        ax.grid(alpha=0.3)
        ax.spines[["top", "right"]].set_visible(False)
    for ax in axes[1]:
        ax.set_xlabel("число попыток k")
    axes[0, 0].legend(loc="upper left", fontsize=8, frameon=False)
    fig.suptitle("Выбор одной попытки из k: доля решённых задач (интервалы разностей — в таблицах E9_REPORT)",
                 fontsize=12)
    fig.tight_layout()
    path = REPORTS / "figures" / "e9_best_of_k.png"
    fig.savefig(path, dpi=130)
    log.info("figure -> %s", path)


def run() -> dict:
    t0 = time.time()
    att = attempts()
    att, info = risks(att)
    pt = per_task(att)
    rows = []
    for scope in list(SETTINGS) + ["all"]:
        for mixed_only in (False, True):
            rows += scope_rows(pt, scope, mixed_only)
    res = pd.DataFrame(rows)
    # Holm over the three settings (full scopes) for every risk rule and k
    res["p_holm"] = np.nan
    for k in KS:
        for p in RISKS:
            sel = (res.k == k) & (res.rule == p) & res.scope.isin(list(SETTINGS))
            adj = holm(dict(zip(res.loc[sel, "scope"], res.loc[sel, "p_one_sided"])))
            res.loc[sel, "p_holm"] = res.loc[sel, "scope"].map(adj)
    crit = {}
    for scope in list(SETTINGS) + ["all"]:
        r = res[(res.scope == scope) & (res.k == K_MAIN)].set_index("rule")
        p_col = "p_one_sided" if scope == "all" else "p_holm"
        crit[scope] = {"1_gain_P2": bool(r.loc["P2", p_col] < 0.025),
                       "2_practical_P2": bool(r.loc["P2", "delta_pp"] >= 2.0),
                       "3_struct_over_length": bool(r.loc["P2-P3", "delta_lo"] > 0),
                       "4_transfer_P1": bool(r.loc["P1", p_col] < 0.025)}
    link = link_e8(att, res)
    desc = {}
    for scope in list(SETTINGS) + ["all"]:
        a = att if scope == "all" else att[att.setting == scope]
        g = a.groupby(["setting", "task_id"]).solved.sum()
        desc[scope] = {"attempts": len(a), "submitted_share": float(a.submitted.mean()),
                       "eval_errors": int(a.eval_error.sum()), "pass_at_1": float(a.solved.mean()),
                       "units": len(g), "mixed_units": int(((g > 0) & (g < N_RUNS)).sum()),
                       "units_never_solved": int((g == 0).sum()), "units_always_solved": int((g == N_RUNS).sum())}
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    res.round(6).to_csv(OUTPUTS / "e9_results.csv", index=False)
    pt.round(6).to_csv(OUTPUTS / "e9_per_task.csv.gz", index=False)
    att[["setting", "task_id", "run", "submitted", "solved", "eval_error", "fold"] + list(RISKS)].round(8).to_csv(
        OUTPUTS / "e9_risks.csv.gz", index=False)
    link.round(6).to_csv(OUTPUTS / "e9_link_e8.csv", index=False)
    summary = {"descriptives": desc, "criteria": crit, "checks": info, "k_main": K_MAIN, "n_subsets": N_SUB,
               "n_boot": N_BOOT, "seconds": round(time.time() - t0, 1)}
    (OUTPUTS / "e9_summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False, default=float),
                                             encoding="utf-8")
    figure(res, att)
    log.info("E9 done in %.0f s; criteria %s", time.time() - t0, crit)
    return summary


if __name__ == "__main__":
    run()
