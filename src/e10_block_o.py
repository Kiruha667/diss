"""E10 (reports/E10_PROTOCOL.md, sections 6-9 with the changes of section 11): block O features, models, criteria.
No LLM calls, no downloads.

  python -m src.e10_block_o  -> outputs/e10_checks.csv.gz, e10_features.csv.gz, e10_leaderboard.csv, e10_kth.csv,
                                e10_selection.csv, e10_unknown.csv, e10_descriptive.csv, e10_summary.json

Input: data/interim/e10/commands.parquet (python -m src.e10_check_outcomes checks). Block O of a run (protocol 11.4,
11.5): main set O (19 features, separate categories; criteria K1-K4) and merged set Om (the 15 features of section 6
on pass = pass | script_ok, fail = fail | script_fail, error = error_env | error_code; robustness). o_n_fail,
o_share_fail, o_unresolved_fail count only the checks after the first edit of the project's code.
Leaderboard (section 7): E7 population, folds, model and cluster bootstrap over tasks; models O, S, S+O, M+S, M+S+O
and the Om variants. KTH (section 8): models S, O, S+O (and Om) within the setting on the E9 folds; within-unit AUROC
with a bootstrap over units, a within-unit permutation test and Holm over the settings; best-of-k selection with the
E9 subsets and bootstrap.
"""
from __future__ import annotations

import json
import sys
import time
import warnings
from collections import namedtuple

import numpy as np
import pandas as pd

from src import e7_sources as e7
from src import e8_repeats as e8
from src import e9_best_of_k as e9
from src import g0_false_success as g0
from src.adapters.agentic_evals import SETTINGS
from src.common import CONFIG, OUTPUTS, ROOT, SEED, get_logger
from src.e10_check_outcomes import CATS, OUT_DIR, kth_records

sys.path.insert(0, str(ROOT))
from demo_lib.features import FEATURES  # noqa: E402

log = get_logger("e10")
N_BOOT = N_PERM = 1000
PAIR_T = 0.03  # pairs of near-equal strength (E7 addendum)
K_MAIN = 5
PROJ_CATS = ("pass", "fail", "error_env", "error_code", "unknown")
MERGE = {"pass": "pass", "script_ok": "pass", "fail": "fail", "script_fail": "fail", "error_env": "error",
         "error_code": "error", "unknown": "unknown"}
M4 = ("pass", "fail", "error", "unknown")
FAILS = {"fail", "error_env", "error_code"}
REST = ["n_fail", "share_fail", "unresolved_fail", "edit_after_last_pass", "last_fail_count", "contradiction"]
O_MAIN = (["o_ran_project_tests"] + [f"o_last_status_{c}" for c in CATS] +
          [f"o_last_project_status_{c}" for c in PROJ_CATS] + [f"o_{r}" for r in REST])
O_MERGED = (["o_ran_project_tests"] + [f"om_last_status_{c}" for c in M4] +
            [f"om_last_project_status_{c}" for c in M4] + [f"om_{r}" for r in REST])
assert len(O_MAIN) == 19 and len(O_MERGED) == 15
BLOCKS = {"O": O_MAIN, "Om": O_MERGED, "S": FEATURES}
# leaderboard models (blocks in the order of E7, so that S and M+S reproduce it)
LB_MODELS = {"O": ["O"], "S": ["S"], "S+O": ["S", "O"], "M+S": ["M", "S"], "M+S+O": ["M", "S", "O"],
             "Om": ["Om"], "S+Om": ["S", "Om"], "M+S+Om": ["M", "S", "Om"]}
LB_DIFFS = [("S+O", "S"), ("M+S+O", "M+S"), ("S+Om", "S"), ("M+S+Om", "M+S")]
LB_METRICS = ["auroc", "within", "pairs", "p@10", "brier"]
KTH_MODELS = {"S": ["S"], "O": ["O"], "S+O": ["S", "O"], "Om": ["Om"], "S+Om": ["S", "Om"]}
KTH_DIFFS = [("S+O", "S"), ("S+Om", "S")]
EXPECTED = {"S": (0.662, 0.640), "M+S": 0.685, "e9_delta_S_k5": (0.77, -0.61, -1.25)}

Check = namedtuple("Check", "pos cat proj nf after")


# ------------------------------------------------------------------------------------------ block O of a run

def run_features(checks: list[Check], edit_pos: list[int], claim: bool) -> dict:
    """Main set (o_…, 19) and merged set (om_…, the 15 of section 6; o_ran_project_tests shared)."""
    f = {}
    projc = [c for c in checks if c.proj]
    last, lastp = (checks[-1] if checks else None), (projc[-1] if projc else None)
    f["o_ran_project_tests"] = int(bool(projc))
    for c in CATS:
        f[f"o_last_status_{c}"] = int(last is not None and last.cat == c)
    for c in PROJ_CATS:
        f[f"o_last_project_status_{c}"] = int(lastp is not None and lastp.cat == c)
    for pre, seq, is_bad, is_pass, lst in (
            ("o", projc, lambda c: c.cat in FAILS, lambda c: c.cat == "pass", lastp),
            ("om", checks, lambda c: MERGE[c.cat] in ("fail", "error"), lambda c: MERGE[c.cat] == "pass", last)):
        after = [c for c in seq if c.after]
        bad = [i for i, c in enumerate(seq) if c.after and is_bad(c)]
        last_pass = max((i for i, c in enumerate(seq) if is_pass(c)), default=-1)
        f[f"{pre}_n_fail"] = len(bad)
        f[f"{pre}_share_fail"] = len(bad) / len(after) if after else 0.0
        f[f"{pre}_unresolved_fail"] = int(any(i > last_pass for i in bad))
        f[f"{pre}_edit_after_last_pass"] = int(last_pass >= 0 and any(p > seq[last_pass].pos for p in edit_pos))
        is_fail = lst is not None and (lst.cat == "fail" if pre == "o" else MERGE[lst.cat] == "fail")
        f[f"{pre}_last_fail_count"] = int(lst.nf) if is_fail else 0
        f[f"{pre}_contradiction"] = int(claim and lst is not None and is_bad(lst))
    for c in M4:
        f[f"om_last_status_{c}"] = int(last is not None and MERGE[last.cat] == c)
        f[f"om_last_project_status_{c}"] = int(lastp is not None and MERGE[lastp.cat] == c)
    return f


def features(cmds: pd.DataFrame, claims: dict[tuple[str, str], bool]) -> pd.DataFrame:
    """Block O of every run of the commands table (runs without commands are added by the callers as zeros)."""
    rows = []
    cols = ["pos", "is_edit", "is_check", "is_project", "category", "n_failed", "code_edit_seg", "check_seg"]
    for (corpus, run_id), g in cmds.groupby(["corpus", "run_id"], sort=False):
        a = g[cols].to_numpy()
        ed = [(int(r[0]), int(r[6])) for r in a if r[6] >= 0]
        p0, s0 = ed[0] if ed else (None, None)
        checks = [Check(int(r[0]), r[4], bool(r[3]), int(r[5]),
                        p0 is not None and (r[0] > p0 or (r[0] == p0 and r[7] > s0))) for r in a if r[2]]
        f = run_features(checks, [int(r[0]) for r in a if r[1]], claims.get((corpus, run_id), False))
        rows.append({"corpus": corpus, "run_id": run_id, "group": g.group.iloc[0], "has_code_edit": int(p0 is not None),
                     "n_checks": len(checks), **f})
    return pd.DataFrame(rows)


def kth_claims() -> dict[str, bool]:
    """has_claim of the final message of every KTH attempt with a patch: G0 detector, final message chosen as on the
    leaderboard (E7: g0.pick_final over g0.clean_nl of the assistant step texts)."""
    claim = g0.load_patterns(CONFIG / "claim_patterns.txt")
    out = {}
    for rec, _ in kth_records():
        _, final = g0.pick_final([{"full": g0.clean_nl(s["text"])} for s in rec["steps"] if s["role"] == "assistant"],
                                 "full")
        out[rec["run_id"]] = bool(g0.match_any(claim, final))
    return out


def all_features() -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """(leaderboard population with S, M, O; KTH attempts with a patch with O; info)."""
    info = {}
    cmds = pd.read_parquet(OUT_DIR / "commands.parquet")
    pop = e7.load_population(False)
    claim = g0.load_patterns(CONFIG / "claim_patterns.txt")
    pop["has_claim"] = [bool(g0.match_any(claim, t)) for t in pop.final_full]
    g0c = pd.read_csv(OUTPUTS / "g0_final_messages.csv", usecols=["run_id", "has_claim"])
    cmp_ = pop[["run_id", "has_claim"]].merge(g0c, on="run_id", suffixes=("", "_g0"))
    info["lb_claim_vs_g0"] = {"runs": len(cmp_), "agree": int((cmp_.has_claim == cmp_.has_claim_g0).sum()),
                              "claim_share": float(pop.has_claim.mean())}
    kc = kth_claims()
    claims = {("leaderboard", r): c for r, c in zip(pop.run_id, pop.has_claim)}
    claims.update({("kth", r): c for r, c in kc.items()})
    feats = features(cmds, claims)
    o_cols = list(dict.fromkeys(O_MAIN + O_MERGED + ["has_code_edit", "n_checks"]))
    lb = pop.merge(feats[feats.corpus == "leaderboard"][["run_id"] + o_cols], on="run_id", how="left")
    info["lb_runs_without_commands"] = int(lb[o_cols[0]].isna().sum())
    lb[o_cols] = lb[o_cols].fillna(0)
    kth = pd.DataFrame({"run_id": list(kc), "has_claim": list(kc.values())}).merge(
        feats[feats.corpus == "kth"][["run_id"] + o_cols], on="run_id", how="left")
    info["kth_runs_without_commands"] = int(kth[o_cols[0]].isna().sum())
    kth[o_cols] = kth[o_cols].fillna(0)
    return lb, kth, info


# ------------------------------------------------------------------------------------------ leaderboard (section 7)

def lb_risks(pop: pd.DataFrame, y: np.ndarray, fold: np.ndarray) -> tuple[dict[str, np.ndarray], int]:
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import OneHotEncoder, StandardScaler

    risk = {m: np.zeros(len(pop)) for m in LB_MODELS}
    n_warn = 0
    for k in range(5):
        tr, te = fold != k, fold == k
        oh_m = OneHotEncoder(handle_unknown="ignore", sparse_output=False).fit(pop.loc[tr, ["combo"]])
        dense = {"M": oh_m.transform(pop[["combo"]]), **{b: pop[c].to_numpy(float) for b, c in BLOCKS.items()}}
        for name, blocks in LB_MODELS.items():
            x = np.hstack([dense[b] for b in blocks])
            x = StandardScaler().fit(x[tr]).transform(x)
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                m = LogisticRegression(C=1.0, max_iter=5000).fit(x[tr], y[tr])
                n_warn += sum("converge" in str(i.message).lower() for i in w)
            risk[name][te] = m.predict_proba(x[te])[:, 1]
    return risk, n_warn


def pair_counts_similar(y: np.ndarray, strength: np.ndarray, risk: np.ndarray, task_rows) -> tuple[np.ndarray,
                                                                                                 np.ndarray]:
    """Concordant pairs and pairs per task among (unresolved, resolved) pairs of near-equal strength."""
    c, n = np.zeros(len(task_rows)), np.zeros(len(task_rows))
    for i, ix in enumerate(task_rows):
        u, ok = ix[y[ix] == 1], ix[y[ix] == 0]
        if len(u) and len(ok):
            mask = np.abs(strength[u][:, None] - strength[ok][None, :]) < PAIR_T
            d = risk[u][:, None] - risk[ok][None, :]
            c[i], n[i] = ((d > 0) + 0.5 * (d == 0))[mask].sum(), mask.sum()
    return c, n


def lb_metrics(yy, ss, c, n, cp, np_) -> dict[str, float]:
    m = e7.metrics_of(yy, ss, c, n)
    return {"auroc": m["auroc"], "within": m["within"], "pairs": float(cp.sum() / np_.sum()), "p@10": m["p@10"],
            "brier": m["brier"]}


def leaderboard(lb: pd.DataFrame) -> tuple[pd.DataFrame, dict, pd.DataFrame]:
    pop = lb
    y = (1 - pop.resolved).to_numpy()
    fold = e7.folds_of(pop, y)
    risk, n_warn = lb_risks(pop, y, fold)
    groups = pop.task_id.to_numpy()
    task_rows = list(pd.Series(np.arange(len(pop))).groupby(groups).apply(np.array))
    strength = pop.groupby("combo").resolved.transform("mean").to_numpy()  # share of resolved runs of the combo
    cnt = {m: e7.pair_counts(y, risk[m], task_rows) for m in LB_MODELS}
    cnp = {m: pair_counts_similar(y, strength, risk[m], task_rows) for m in LB_MODELS}
    point = {m: lb_metrics(y, risk[m], *cnt[m], *cnp[m]) for m in LB_MODELS}
    got = (round(point["S"]["auroc"], 3), round(point["S"]["within"], 3))
    if got != EXPECTED["S"] or round(point["M+S"]["auroc"], 3) != EXPECTED["M+S"]:
        raise SystemExit(f"STOP: S {got}, M+S {point['M+S']['auroc']:.3f}; E7: {EXPECTED}")
    rng = np.random.default_rng(SEED)
    draws = [rng.integers(0, len(task_rows), len(task_rows)) for _ in range(N_BOOT)]
    boot = {m: {k: np.zeros(N_BOOT) for k in LB_METRICS} for m in LB_MODELS}
    for b, dr in enumerate(draws):
        ix = np.concatenate([task_rows[i] for i in dr])
        for m in LB_MODELS:
            (c, n), (cp, np_) = cnt[m], cnp[m]
            vals = lb_metrics(y[ix], risk[m][ix], c[dr], n[dr], cp[dr], np_[dr])
            for k in LB_METRICS:
                boot[m][k][b] = vals[k]
    rows = []
    for m in LB_MODELS:
        for k in LB_METRICS:
            lo, hi = np.percentile(boot[m][k], [2.5, 97.5])
            rows.append({"kind": "model", "model": m, "baseline": "", "metric": k, "value": point[m][k],
                         "ci_lo": lo, "ci_hi": hi})
    for a, b in LB_DIFFS:
        for k in LB_METRICS:
            lo, hi = np.percentile(boot[a][k] - boot[b][k], [2.5, 97.5])
            rows.append({"kind": "diff", "model": a, "baseline": b, "metric": k,
                         "value": point[a][k] - point[b][k], "ci_lo": lo, "ci_hi": hi})
    res = pd.DataFrame(rows)
    d = res.set_index(["kind", "model", "baseline", "metric"])
    k3 = {"auroc_diff_ci_lo": float(d.loc[("diff", "S+O", "S", "auroc"), "ci_lo"]),
          "pairs_diff": float(d.loc[("diff", "S+O", "S", "pairs"), "value"])}
    k3["met"] = bool(k3["auroc_diff_ci_lo"] > 0 and k3["pairs_diff"] > 0)
    info = {"convergence_warnings": n_warn, "reproduced": {"S": got, "M+S": round(point["M+S"]["auroc"], 3)},
            "pairs_similar": int(cnp["S"][1].sum()), "pairs_tasks": int((cnp["S"][1] > 0).sum()),
            "within_pairs": int(cnt["S"][1].sum()), "K3": k3}
    oof = pd.DataFrame({"run_id": pop.run_id, "task_id": pop.task_id, "y_unresolved": y, "fold": fold,
                        **{m: risk[m] for m in LB_MODELS}})
    return res, info, oof


# ------------------------------------------------------------------------------------------ KTH (section 8)

def kth_risks(kfe: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Risks of KTH_MODELS for every submitted attempt, within the setting on the E9 folds (= e9.risks for P2)."""
    from sklearn.model_selection import GroupKFold

    att = e9.attempts()
    pop = pd.read_parquet(e8.OUT_DIR / "runs.parquet").sort_values("run_id").reset_index(drop=True)
    s = pd.concat([pop[["run_id"] + FEATURES], e9.eval_error_features(att)], ignore_index=True)
    feats = s.merge(kfe, on="run_id", how="left")
    info = {"attempts_without_O": int(feats[O_MAIN[0]].isna().sum())}
    sub = att[att.submitted].merge(feats, on="run_id", how="left")
    assert sub[FEATURES + O_MAIN + O_MERGED].notna().all().all() and len(sub) == int(att.submitted.sum())
    pop = pop[["run_id", "setting", "task_id", "label"]].merge(feats, on="run_id", how="left")
    for m in KTH_MODELS:
        sub[m] = np.nan
    sub["fold"] = -1
    for setting in SETTINGS:
        ps = pop[pop.setting == setting].reset_index(drop=True)
        y = ps.label.to_numpy()
        fold_of = {}
        for k, (_, te) in enumerate(GroupKFold(n_splits=5, shuffle=True, random_state=SEED)
                                    .split(ps, y, ps.task_id.to_numpy())):
            fold_of.update({t: k for t in ps.task_id.iloc[te]})
        in_s = (sub.setting == setting).to_numpy()
        sub.loc[in_s, "fold"] = sub.loc[in_s, "task_id"].map(fold_of).fillna(-1).astype(int)
        for name, blocks in KTH_MODELS.items():
            cols = [c for b in blocks for c in BLOCKS[b]]
            for k in range(5):
                tr = ps.task_id.map(fold_of).to_numpy() != k
                m = e8.new_model().fit(ps.loc[tr, cols].to_numpy(float), y[tr])
                te = in_s & (sub.fold == k).to_numpy()
                sub.loc[te, name] = m.predict_proba(sub.loc[te, cols].to_numpy(float))[:, 1]
            orphan = in_s & (sub.fold == -1).to_numpy()
            if orphan.any():
                m = e8.new_model().fit(ps[cols].to_numpy(float), y)
                sub.loc[orphan, name] = m.predict_proba(sub.loc[orphan, cols].to_numpy(float))[:, 1]
    e9r = pd.read_csv(OUTPUTS / "e9_risks.csv.gz")
    e9r["run_id"] = e9r.setting + "/" + e9r.run + "/" + e9r.task_id
    chk = sub[["run_id", "S"]].merge(e9r[["run_id", "P2"]], on="run_id")
    info["S_vs_e9_P2_max_abs_diff"] = float((chk.S - chk.P2).abs().max())
    info["S_vs_e9_P2_n"] = len(chk)
    if len(chk) != len(sub) or info["S_vs_e9_P2_max_abs_diff"] > 1e-6:
        raise SystemExit(f"STOP: S differs from E9 P2: {info}")
    out = att.merge(sub[["run_id", "fold"] + list(KTH_MODELS)], on="run_id", how="left")
    return out, info


def kth_within(att: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Within-unit AUROC per setting and for the three together (E8 code: bootstrap over units, permutations)."""
    pop = att[att.label >= 0].sort_values("run_id").reset_index(drop=True)
    pop["unit"] = pop.setting + "|" + pop.task_id
    y = pop.label.to_numpy()
    risk = {m: pop[m].to_numpy(float) for m in KTH_MODELS}
    rows, desc = [], {}
    for scope in list(SETTINGS) + ["all"]:
        sub = pop if scope == "all" else pop[pop.setting == scope]
        idx = sub.index.to_numpy()
        unit_rows = [idx[g] for g in pd.Series(np.arange(len(sub))).groupby(sub.unit.to_numpy()).apply(np.array)]
        counts = {m: e8.unit_counts(y, risk[m], unit_rows) for m in KTH_MODELS}
        rng = np.random.default_rng(SEED)
        draws = [rng.integers(0, len(unit_rows), len(unit_rows)) for _ in range(N_BOOT)]
        boot = {m: np.array([c[d].sum() / n[d].sum() for d in draws]) for m, (c, n) in counts.items()}
        rng_p = np.random.default_rng(SEED)
        perms = [rng_p.permuted(np.tile(y[ix], (N_PERM, 1)), axis=1) for ix in unit_rows]
        for m, (c, n) in counts.items():
            w = c.sum() / n.sum()
            null = e8.perm_stats(y, risk[m], unit_rows, perms)
            lo, hi = np.percentile(boot[m], [2.5, 97.5])
            rows.append({"scope": scope, "model": m, "baseline": "", "metric": "within_unit_auroc", "value": w,
                         "ci_lo": lo, "ci_hi": hi, "perm_p": (1 + int((null >= w - 1e-12).sum())) / (N_PERM + 1)})
            rows.append({"scope": scope, "model": m, "baseline": "", "metric": "overall_auroc",
                         "value": e8.auroc(y[idx], risk[m][idx]), "ci_lo": np.nan, "ci_hi": np.nan,
                         "perm_p": np.nan})
        for a, b in KTH_DIFFS:
            lo, hi = np.percentile(boot[a] - boot[b], [2.5, 97.5])
            wa, wb = (counts[x][0].sum() / counts[x][1].sum() for x in (a, b))
            rows.append({"scope": scope, "model": a, "baseline": b, "metric": "within_unit_auroc_diff",
                         "value": wa - wb, "ci_lo": lo, "ci_hi": hi, "perm_p": np.nan})
        desc[scope] = {"runs": len(sub), "units": len(unit_rows),
                       "units_with_both": int((counts["S"][1] > 0).sum()), "within_pairs": int(counts["S"][1].sum())}
    res = pd.DataFrame(rows)
    res["perm_p_holm"] = np.nan
    for m in KTH_MODELS:
        sel = (res.metric == "within_unit_auroc") & (res.model == m) & (res.scope != "all")
        adj = e8.holm(dict(zip(res.loc[sel, "scope"], res.loc[sel, "perm_p"])))
        res.loc[sel, "perm_p_holm"] = res.loc[sel, "scope"].map(adj)
    return res, desc


def rule_values(solved, submitted, risk: dict[str, np.ndarray], idx) -> dict:
    """e9.rule_values for the risks of KTH_MODELS (lowest risk among submitted; exact expectation over ties)."""
    t = np.arange(solved.shape[0])[:, None, None]
    sv, sb = solved[t, idx].astype(float), submitted[t, idx]
    nsb = sb.sum(-1)
    anyb = nsb > 0
    out = {"random": sv.mean(-1),
           "submitted-random": np.where(anyb, (sv * sb).sum(-1) / np.maximum(nsb, 1), sv.mean(-1))}
    for p, rk in risk.items():
        r = np.where(sb, rk[t, idx], np.inf)
        tie = sb & (r == r.min(-1, keepdims=True))
        out[p] = np.where(anyb, (sv * tie).sum(-1) / np.maximum(tie.sum(-1), 1), sv.mean(-1))
    out["oracle"] = sv.max(-1)
    return {k: v.mean(-1) for k, v in out.items()}


def selection(att: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Δ of the share solved to submitted-random (E9 subsets and bootstrap), and Δ(S+O) − Δ(S)."""
    subs = e9.subsets()
    rows = []
    for setting in SETTINGS:
        a = att[att.setting == setting].sort_values(["task_id", "j"])
        tasks = a.task_id.unique()
        shape = (500, e9.N_RUNS)
        solved, submitted = a.solved.to_numpy().reshape(shape), a.submitted.to_numpy().reshape(shape)
        risk = {m: a[m].to_numpy(float).reshape(shape) for m in KTH_MODELS}
        for k in e9.KS:
            for rule, v in rule_values(solved, submitted, risk, subs[k]).items():
                rows.append(pd.DataFrame({"setting": setting, "task_id": tasks, "k": k, "rule": rule, "value": v}))
    pt = pd.concat(rows, ignore_index=True)
    out = []
    for scope in list(SETTINGS) + ["all"]:
        d = pt if scope == "all" else pt[pt.setting == scope]
        wide = d.pivot_table(index=["setting", "task_id"], columns=["k", "rule"], values="value")
        utask, tix = np.unique(wide.index.get_level_values("task_id").to_numpy(), return_inverse=True)
        cnt = np.bincount(tix).astype(float)
        sums = {c: np.bincount(tix, weights=wide[c].to_numpy()) for c in wide.columns}
        rng = np.random.default_rng(SEED)
        W = np.stack([np.bincount(rng.integers(0, len(utask), len(utask)), minlength=len(utask))
                      for _ in range(e9.N_BOOT)])
        wc = W @ cnt
        boot = {c: (W @ s) / wc for c, s in sums.items()}
        point = {c: s.sum() / cnt.sum() for c, s in sums.items()}
        for k in e9.KS:
            sr = (k, "submitted-random")
            for rule in ["random", "submitted-random", *KTH_MODELS, "oracle"]:
                dl = (boot[(k, rule)] - boot[sr]) * 100
                out.append({"scope": scope, "k": k, "rule": rule, "baseline": "submitted-random",
                            "solved": point[(k, rule)], "delta_pp": (point[(k, rule)] - point[sr]) * 100,
                            "ci_lo": np.percentile(dl, 2.5), "ci_hi": np.percentile(dl, 97.5)})
            for a, b in KTH_DIFFS:
                dd = (boot[(k, a)] - boot[(k, b)]) * 100
                out.append({"scope": scope, "k": k, "rule": f"{a} - {b}", "baseline": b, "solved": np.nan,
                            "delta_pp": (point[(k, a)] - point[(k, b)]) * 100, "ci_lo": np.percentile(dd, 2.5),
                            "ci_hi": np.percentile(dd, 97.5)})
    res = pd.DataFrame(out)
    got = tuple(round(float(res[(res.scope == s) & (res.k == K_MAIN) & (res.rule == "S")].delta_pp.iloc[0]), 2)
                for s in SETTINGS)
    if got != EXPECTED["e9_delta_S_k5"]:
        raise SystemExit(f"STOP: Δ(S) at k = 5 {got}, E9 {EXPECTED['e9_delta_S_k5']}")
    return res, {"reproduced_delta_S_k5": got}


# ------------------------------------------------------------------------------------------ descriptives

def unknown_shares(cmds: pd.DataFrame) -> pd.DataFrame:
    chk = cmds[cmds.is_check]
    last = chk.groupby(["corpus", "run_id"]).tail(1)
    rows = []
    for (corpus, group), g in chk.groupby(["corpus", "group"]):
        lg = last[(last.corpus == corpus) & (last.group == group)]
        rows.append({"corpus": corpus, "group": group, "checks": len(g), "unknown_share": (g.category == "unknown")
                     .mean(), "runs_with_checks": len(lg), "unknown_share_last": (lg.category == "unknown").mean()})
    out = pd.DataFrame(rows)
    out["over_half"] = (out.unknown_share > 0.5) | (out.unknown_share_last > 0.5)
    return out


def descriptive(lb: pd.DataFrame, kth: pd.DataFrame) -> pd.DataFrame:
    """Section 9 (descriptive, no criterion) and the share of runs without a detected code edit (11.5)."""
    rows = []
    kl = kth[kth.label >= 0]
    scopes = [("leaderboard", lb, lb.resolved.astype(float))] + \
             [(s, kl[kl.setting == s], (kl[kl.setting == s].label == 0).astype(float)) for s in SETTINGS] + \
             [("kth_all", kl, (kl.label == 0).astype(float))]
    for scope, d, solved in scopes:
        base = {"scope": scope, "runs": len(d)}
        rows.append({**base, "what": "solved_share", "n": len(d), "value": solved.mean()})
        rows.append({**base, "what": "no_code_edit_found", "n": int((d.has_code_edit == 0).sum()),
                     "value": float((d.has_code_edit == 0).mean())})
        rows.append({**base, "what": "no_checks", "n": int((d.n_checks == 0).sum()), "value": float((d.n_checks == 0)
                                                                                                    .mean())})
        for pre in ("o", "om"):
            c = d[f"{pre}_contradiction"] == 1
            rows.append({**base, "what": f"{pre}_contradiction_share", "n": int(c.sum()), "value": float(c.mean())})
            rows.append({**base, "what": f"solved_if_{pre}_contradiction", "n": int(c.sum()),
                         "value": float(solved[c].mean()) if c.any() else np.nan})
        for c in CATS:
            s = d[f"o_last_status_{c}"] == 1
            rows.append({**base, "what": f"solved_if_last_{c}", "n": int(s.sum()),
                         "value": float(solved[s].mean()) if s.any() else np.nan})
        for c in M4:
            s = d[f"om_last_status_{c}"] == 1
            rows.append({**base, "what": f"solved_if_last_merged_{c}", "n": int(s.sum()),
                         "value": float(solved[s].mean()) if s.any() else np.nan})
    return pd.DataFrame(rows)


# ------------------------------------------------------------------------------------------ run

def run() -> dict:
    t0 = time.time()
    cmds = pd.read_parquet(OUT_DIR / "commands.parquet")
    lb, kfe, info = all_features()
    log.info("features: %s (%.0f s)", info, time.time() - t0)
    att, kinfo = kth_risks(kfe)
    info.update(kinfo)
    lres, linfo, loof = leaderboard(lb)
    log.info("leaderboard: %s (%.0f s)", linfo, time.time() - t0)
    kres, kdesc = kth_within(att)
    sres, sinfo = selection(att)
    log.info("kth done (%.0f s)", time.time() - t0)
    # criteria (section 9), main set
    w = kres.set_index(["scope", "model", "baseline", "metric"])
    k1_lo = float(w.loc[("all", "S+O", "S", "within_unit_auroc_diff"), "ci_lo"])
    k2 = {s: float(w.loc[(s, "S+O", "", "within_unit_auroc"), "value"]) for s in SETTINGS}
    k4 = {s: float(sres[(sres.scope == s) & (sres.k == K_MAIN) & (sres.rule == "S+O - S")].ci_lo.iloc[0])
          for s in SETTINGS}
    crit = {"K1": {"ci_lo": k1_lo, "met": k1_lo > 0},
            "K2": {"within_auroc_S+O": k2, "met": any(v >= 0.60 for v in k2.values())},
            "K3": linfo["K3"],
            "K4": {"delta_diff_ci_lo": k4, "met": any(v > 0 for v in k4.values())}}
    unk = unknown_shares(cmds)
    kth = att[att.submitted].merge(kfe, on="run_id", how="left")
    desc = descriptive(lb, kth)
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    chk = cmds[cmds.is_check]
    chk.assign(command=chk.command.str.slice(0, 200))[
        ["corpus", "group", "run_id", "step_idx", "pos", "command", "is_runner", "is_project", "category", "rule",
         "kind", "n_failed"]].to_csv(OUTPUTS / "e10_checks.csv.gz", index=False)
    o_cols = list(dict.fromkeys(O_MAIN + O_MERGED))
    fe = pd.concat([lb.assign(corpus="leaderboard", group=lb.combo, y_unresolved=1 - lb.resolved)[
                        ["corpus", "group", "run_id", "task_id", "y_unresolved", "has_claim", "has_code_edit",
                         "n_checks"] + o_cols],
                    kth.assign(corpus="kth", group=kth.setting, y_unresolved=kth.label)[
                        ["corpus", "group", "run_id", "task_id", "y_unresolved", "has_claim", "has_code_edit",
                         "n_checks"] + o_cols]], ignore_index=True)
    fe.round(6).to_csv(OUTPUTS / "e10_features.csv.gz", index=False)
    lres.round(6).to_csv(OUTPUTS / "e10_leaderboard.csv", index=False)
    kres.round(6).to_csv(OUTPUTS / "e10_kth.csv", index=False)
    sres.round(6).to_csv(OUTPUTS / "e10_selection.csv", index=False)
    unk.round(6).to_csv(OUTPUTS / "e10_unknown.csv", index=False)
    desc.round(6).to_csv(OUTPUTS / "e10_descriptive.csv", index=False)
    loof.round(8).to_csv(OUTPUTS / "e10_oof_risk.csv.gz", index=False)
    att[["setting", "task_id", "run", "submitted", "solved", "eval_error", "fold"] + list(KTH_MODELS)].round(8).to_csv(
        OUTPUTS / "e10_kth_risks.csv.gz", index=False)
    summary = {"criteria": crit, "checks": {**info, **linfo, **sinfo}, "kth_desc": kdesc,
               "o_main": O_MAIN, "o_merged": O_MERGED, "n_boot": N_BOOT, "seconds": round(time.time() - t0, 1)}
    (OUTPUTS / "e10_summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False, default=float),
                                              encoding="utf-8")
    log.info("E10 done in %.0f s; criteria %s", time.time() - t0, crit)
    return summary


if __name__ == "__main__":
    run()
