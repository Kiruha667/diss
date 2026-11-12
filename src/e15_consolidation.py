"""E15 (reports/E15_PROTOCOL.md): consolidation analyses requested by the review. No LLM calls, no downloads.

  python -m src.e15_consolidation lb        -> outputs/e15_leaderboard.csv, e15_leaderboard_diffs.csv
  python -m src.e15_consolidation kth       -> outputs/e15_kth_within.csv, e15_kth_selection.csv
  python -m src.e15_consolidation variance  -> outputs/e15_variance.csv, e15_noise.csv
  python -m src.e15_consolidation all       -> all three
Each part adds its section to outputs/e15_summary.json.
Parts 2 and 3 cover ANALYSIS_SETTINGS (since 2026-11-12 without DeepSWE-Preview, reports/KTH_DEEPSWE_LABELS.md);
KTH_WITH_DEEPSWE=1 -> the earlier three-setting run, outputs of parts 2 and 3 in outputs/kth_with_deepswe/.

Ported from the reviewer's scripts (review/scripts/01_leaderboard.py: difficulty from the other models, length
baselines, boosting settings, D+M; 04_kth.py: the finished / cut-off split; 10_misc.py: empty patches). Metrics, folds
and the selection machinery are the project's (src.e7_sources, src.e8_repeats, src.e9_best_of_k, src.e10_block_o).

Part 1 (leaderboard): populations P_all (E7, 7864) and P_ne (without the 132 empty submitted patches); models D, D_c,
M, M+N, M+N gbm, M+LEN, S, M+S, D+M, D+M+S; overall AUROC, within-task AUROC (pooled; macro), precision@5/10/20% and
recall@10% with ties at the cut-off counted by expectation; cluster bootstrap over tasks (1000, default_rng(42)).
Part 2 (KTH): within-unit AUROC on all pairs and on pairs of finished attempts (+ mixed, both cut off), training over
the settings pooled (T1, E8) and within the setting (T2, E9/E10), T3 = T2 fitted on finished attempts; best-of-k with the
base "random among finished attempts".
Part 3 (KTH): mixed logistic model logit P(fail) = mu_setting + a_task + c_task x setting, ML by nested Gauss-Hermite
quadrature; variance shares on the latent and on the observed scale; evaluation noise from identical patches within a
unit and the within-unit AUROC ceiling it implies.
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
import time
import warnings

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import expit, logsumexp

from src import e7_sources as e7
from src import e8_repeats as e8
from src import e9_best_of_k as e9
from src.adapters.agentic_evals import (ANALYSIS_SETTINGS, KTH_OUTPUTS, SETTINGS, WITH_DEEPSWE, iter_raw, run_dirs,
                                       to_record)
from src.common import INTERIM, OUTPUTS, ROOT, SEED, get_logger, load_leak_markers
from src.e10_block_o import O_MAIN
from src.segment import compile_markers

sys.path.insert(0, str(ROOT))
from demo_lib.features import FEATURES, LEN, trajectory_features  # noqa: E402

log = get_logger("e15")
N_BOOT = 1000
N_BOOT_GLMM = 200
Q_POINT, Q_CHECK, Q_BOOT = 40, 60, 20
N_SIM = 200
FRACS = (0.05, 0.10, 0.20)
EPS_GRID = (0.0, 0.01, 0.02, 0.05, 0.10)
SHORT = {"nano-agent-Qwen_Qwen3-32B": "Qwen3-32B", "nano-agent-agentica-org_DeepSWE-Preview": "DeepSWE-Preview",
         "nano-agent-mistral_devstral-2512": "devstral-2512"}
CACHE = INTERIM / "e15"
SUMMARY = OUTPUTS / "e15_summary.json"


def _update_summary(section: str, value: dict, out_dir=OUTPUTS) -> None:
    path = out_dir / SUMMARY.name
    s = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    s[section] = value
    out_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(s, indent=1, ensure_ascii=False, default=float), encoding="utf-8")


def _ci(v: np.ndarray) -> tuple[float, float]:
    lo, hi = np.nanpercentile(v, [2.5, 97.5])
    return float(lo), float(hi)


# ================================================================================ shared metrics

def top_k_hits(y: np.ndarray, s: np.ndarray, frac: float) -> float:
    """Expected number of positives among the top ceil(frac*n) by s, ties at the cut-off in random order."""
    k = math.ceil(frac * len(y))
    v = np.sort(s)[::-1][k - 1]
    above, tied = s > v, s == v
    need = k - int(above.sum())
    return float(y[above].sum() + need * y[tied].mean())


def prec_at(y: np.ndarray, s: np.ndarray, frac: float) -> float:
    return top_k_hits(y, s, frac) / math.ceil(frac * len(y))


def recall_at(y: np.ndarray, s: np.ndarray, frac: float) -> float:
    return top_k_hits(y, s, frac) / y.sum()


# ================================================================================ part 1: leaderboard

LB_MODELS = ("D", "D_c", "M", "M+N", "M+N gbm", "M+LEN", "S", "M+S", "D+M", "D+M+S")
LB_DIFFS = (("M+S", "M+N"), ("M+S", "M+LEN"), ("M+S", "M"), ("D+M+S", "D+M"))
LB_METRICS = ("auroc", "within", "within_macro", "p@5", "p@10", "p@20", "recall@10")
NO_WITHIN = ("D", "D_c")  # constant within the task by design (protocol 1.3)
EXPECTED_LB = {"S": (0.662, 0.640), "M+S": (0.685, 0.656), "M_within": 0.742}


def lb_population() -> pd.DataFrame:
    runs = pd.read_parquet(ROOT / "data" / "demo" / "runs.parquet")
    pop = runs[runs.submitted == 1].sort_values("run_id").reset_index(drop=True)  # = e7.load_population order
    pop["combo"] = pop.model + " | " + pop.agent_version
    pop["base_model"] = pop.model.str.replace("@high", "", regex=False)
    pf = pd.read_csv(OUTPUTS / "e13_patch_features.csv.gz")
    pf = pf[pf.corpus == "leaderboard"][["run_id", "p_empty"]]
    n = len(pop)
    pop = pop.merge(pf, on="run_id", how="left")
    assert len(pop) == n and pop.p_empty.notna().all(), "patch features missing"
    pop["y"] = 1 - pop.resolved
    return pop


def difficulty(pop: pd.DataFrame, by: str) -> tuple[np.ndarray, int]:
    """Share of unresolved among the runs of the same task by other `by` (base model or combo) in `pop`."""
    y = pop.y.to_numpy(float)
    ft = pop.groupby("task_id").y.transform("sum").to_numpy(float)
    nt = pop.groupby("task_id").y.transform("size").to_numpy(float)
    fo = pop.groupby(["task_id", by]).y.transform("sum").to_numpy(float)
    no = pop.groupby(["task_id", by]).y.transform("size").to_numpy(float)
    rest = nt - no
    d = np.where(rest > 0, (ft - fo) / np.maximum(rest, 1), y.mean())
    return d, int((rest == 0).sum())


def gbm():
    from sklearn.ensemble import HistGradientBoostingClassifier

    return HistGradientBoostingClassifier(max_iter=200, learning_rate=0.05, max_leaf_nodes=15, random_state=SEED)


def lb_risks(pop: pd.DataFrame, fold: np.ndarray) -> tuple[dict[str, np.ndarray], dict]:
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import OneHotEncoder, StandardScaler

    y = pop.y.to_numpy()
    d, n_fb = difficulty(pop, "base_model")
    dc, n_fbc = difficulty(pop, "combo")
    dl = np.log(np.clip(d, 0.02, 0.98) / (1 - np.clip(d, 0.02, 0.98)))[:, None]
    risk = {"D": d, "D_c": dc}
    spec = {"M": ["M"], "M+N": ["M", "LOGN"], "M+LEN": ["M", "LEN"], "S": ["S"], "M+S": ["M", "S"],
            "D+M": ["D", "M"], "D+M+S": ["D", "M", "S"]}
    for m in list(spec) + ["M+N gbm"]:
        risk[m] = np.zeros(len(pop))
    n_warn = 0
    for k in range(5):
        tr, te = fold != k, fold == k
        oh = OneHotEncoder(handle_unknown="ignore", sparse_output=False).fit(pop.loc[tr, ["combo"]])
        blocks = {"M": oh.transform(pop[["combo"]]), "S": pop[FEATURES].to_numpy(float),
                  "LEN": pop[LEN].to_numpy(float), "LOGN": np.log1p(pop[["n_steps"]].to_numpy(float)), "D": dl}
        for name, bl in spec.items():
            x = np.hstack([blocks[b] for b in bl])
            x = StandardScaler().fit(x[tr]).transform(x)
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                mdl = LogisticRegression(C=1.0, max_iter=5000).fit(x[tr], y[tr])
                n_warn += sum("converge" in str(i.message).lower() for i in w)
            risk[name][te] = mdl.predict_proba(x[te])[:, 1]
        xg = np.hstack([blocks["M"], pop[["n_steps"]].to_numpy(float)])
        risk["M+N gbm"][te] = gbm().fit(xg[tr], y[tr]).predict_proba(xg[te])[:, 1]
    return risk, {"convergence_warnings": n_warn, "D_fallback_runs": n_fb, "D_c_fallback_runs": n_fbc}


def lb_metrics(y: np.ndarray, s: np.ndarray, c: np.ndarray | None, n: np.ndarray | None) -> dict[str, float]:
    out = {"auroc": e7.auroc(y, s)}
    if c is None:
        out["within"] = out["within_macro"] = np.nan
    else:
        out["within"] = float(c.sum() / n.sum())
        ok = n > 0
        out["within_macro"] = float(np.mean(c[ok] / n[ok]))
    for f in FRACS:
        out[f"p@{round(f * 100)}"] = prec_at(y, s, f)
    out["recall@10"] = recall_at(y, s, 0.10)
    return out


def lb_evaluate(name: str, pop: pd.DataFrame, risk: dict[str, np.ndarray]) -> tuple[list, list, dict]:
    y = pop.y.to_numpy()
    task_rows = list(pd.Series(np.arange(len(pop))).groupby(pop.task_id.to_numpy()).apply(np.array))
    cnt = {m: (None, None) if m in NO_WITHIN else e7.pair_counts(y, risk[m], task_rows) for m in risk}
    point = {m: lb_metrics(y, risk[m], *cnt[m]) for m in risk}
    rng = np.random.default_rng(SEED)
    draws = [rng.integers(0, len(task_rows), len(task_rows)) for _ in range(N_BOOT)]
    boot = {m: {k: np.zeros(N_BOOT) for k in LB_METRICS} for m in risk}
    for b, dr in enumerate(draws):
        ix = np.concatenate([task_rows[i] for i in dr])
        for m in risk:
            c, n = cnt[m]
            vals = lb_metrics(y[ix], risk[m][ix], None if c is None else c[dr], None if n is None else n[dr])
            for k in LB_METRICS:
                boot[m][k][b] = vals[k]
    rows = []
    for m in risk:
        for k in LB_METRICS:
            lo, hi = _ci(boot[m][k]) if not np.isnan(point[m][k]) else (np.nan, np.nan)
            rows.append({"population": name, "model": m, "metric": k, "value": point[m][k], "ci_lo": lo, "ci_hi": hi})
    drows = []
    for a, b in LB_DIFFS:
        for k in ("auroc", "within", "p@10"):
            lo, hi = _ci(boot[a][k] - boot[b][k])
            drows.append({"population": name, "model": a, "baseline": b, "metric": k,
                          "diff": point[a][k] - point[b][k], "ci_lo": lo, "ci_hi": hi})
    info = {"runs": len(pop), "tasks": len(task_rows), "unresolved_share": float(y.mean()),
            "within_pairs": int(cnt["S"][1].sum()), "tasks_with_pairs": int((cnt["S"][1] > 0).sum())}
    return rows, drows, info


def part_leaderboard() -> dict:
    t0 = time.time()
    p_all = lb_population()
    fold_all = e7.folds_of(p_all, p_all.y.to_numpy())
    empty = p_all.p_empty.to_numpy().astype(bool)
    if int(empty.sum()) != 132:
        raise SystemExit(f"STOP: {int(empty.sum())} empty submitted patches, protocol expects 132")
    info = {"empty_patches": int(empty.sum()), "empty_unresolved": int(p_all.y[empty].sum()),
            "empty_by_generation": p_all[empty].agent_version.eq("2.0.0").map({True: "v2", False: "v1"})
            .value_counts().to_dict(), "empty_by_combo": p_all[empty].combo.value_counts().to_dict()}
    rows, drows = [], []
    for name, pop, fold in (("P_all", p_all, fold_all), ("P_ne", p_all[~empty].reset_index(drop=True),
                                                         fold_all[~empty])):
        risk, rinfo = lb_risks(pop, fold)
        if name == "P_all":
            y = pop.y.to_numpy()
            task_rows = list(pd.Series(np.arange(len(pop))).groupby(pop.task_id.to_numpy()).apply(np.array))
            w = {m: e7.pair_counts(y, risk[m], task_rows) for m in ("S", "M")}
            got = {"S": (round(e7.auroc(y, risk["S"]), 3), round(float(w["S"][0].sum() / w["S"][1].sum()), 3)),
                   "M+S": (round(e7.auroc(y, risk["M+S"]), 3), round(prec_at(y, risk["M+S"], 0.10), 3)),
                   "M_within": round(float(w["M"][0].sum() / w["M"][1].sum()), 3)}
            if got != EXPECTED_LB:
                raise SystemExit(f"STOP: reproduction {got}, expected {EXPECTED_LB}")
            info["reproduced"] = got
            # descriptive rule: empty patch first, then M+S (protocol 1.2)
            risk["empty first, then M+S"] = np.where(empty, 1.0 + risk["M+S"], risk["M+S"])
            k10 = math.ceil(0.10 * len(y))
            info["M+S_empty_in_top10pct"] = int(empty[np.argsort(-risk["M+S"], kind="mergesort")[:k10]].sum())
        r, d, ev = lb_evaluate(name, pop, risk)
        rows += r
        drows += d
        info[name] = {**ev, **rinfo}
        log.info("%s done (%.0f s)", name, time.time() - t0)
    met, dif = pd.DataFrame(rows), pd.DataFrame(drows)
    met.round(6).to_csv(OUTPUTS / "e15_leaderboard.csv", index=False)
    dif.round(6).to_csv(OUTPUTS / "e15_leaderboard_diffs.csv", index=False)
    info["seconds"] = round(time.time() - t0, 1)
    _update_summary("leaderboard", info)
    return info


# ================================================================================ KTH raw pass (parts 2 and 3)

def kth_raw() -> pd.DataFrame:
    """One row per attempt of all three settings: hash of the stripped generated_diff; for the evaluation-error
    attempts also `fin` and the S features (E9 code path: to_record + trajectory_features). Cached."""
    path = CACHE / "kth_raw.parquet"
    if path.exists():
        return pd.read_parquet(path)
    att = e9.attempts(settings=SETTINGS)
    err = set(att.run_id[att.eval_error])
    mre = compile_markers(load_leak_markers())
    rows = []
    for setting in SETTINGS:
        for rd in run_dirs(setting):
            for raw in iter_raw(rd):
                rid = f"{setting}/{rd.name}/{raw['instance_id']}"
                dp = raw["detailed_predictions"]
                diff = dp.get("generated_diff") if isinstance(dp, dict) else None
                if not isinstance(dp, dict):
                    from src.adapters.agentic_evals import _lit
                    diff = (_lit(dp) or {}).get("generated_diff")
                diff = diff.strip() if isinstance(diff, str) and diff != "None" else ""
                row = {"run_id": rid, "diff_len": len(diff),
                       "diff_hash": hashlib.sha1(diff.encode("utf-8")).hexdigest() if diff else ""}
                if rid in err:
                    rec = to_record(setting, rd.name, raw, None)
                    row["fin_raw"] = bool(rec["meta"]["ended_by_final_message"])
                    row.update(trajectory_features(e8.step_dicts(rec, mre)))
                rows.append(row)
            log.info("raw %s %s: %d attempts", setting, rd.name, len(rows))
    df = pd.DataFrame(rows)
    assert df.run_id.is_unique
    CACHE.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    return df


# ================================================================================ part 2: KTH by finished attempts

KTH_BLOCKS = {"S": FEATURES, "LEN": LEN, "O": O_MAIN, "S+O": FEATURES + O_MAIN}
KTH_RISKS = [f"{b}-{t}" for b in ("S", "LEN", "O", "S+O") for t in ("T1", "T2")] + ["S-T3", "S+O-T3"]
PAIR_SETS = ("all", "FF", "FU", "UU")
EXPECTED_KTH = {("S-T1", "nano-agent-mistral_devstral-2512"): 0.578, ("S-T1", "all"): 0.533,
                ("LEN-T1", "all"): 0.496, ("S-T2", "nano-agent-mistral_devstral-2512"): 0.561,
                ("S-T2", "all"): 0.508, ("S+O-T2", "nano-agent-mistral_devstral-2512"): 0.585}
EXPECTED_SEL = {("S-T2", "nano-agent-Qwen_Qwen3-32B"): -0.61, ("S-T2", "nano-agent-agentica-org_DeepSWE-Preview"): -1.25,
                ("S-T2", "nano-agent-mistral_devstral-2512"): 0.77, ("S+O-T2", "nano-agent-mistral_devstral-2512"): 1.58}


def _expected(d: dict) -> dict:
    """Reproduction checks: all with DeepSWE-Preview; without it only those that do not depend on the other settings
    (training within the setting, T2, of a kept setting)."""
    if WITH_DEEPSWE:
        return d
    return {k: v for k, v in d.items() if k[1] in ANALYSIS_SETTINGS and k[0].endswith("-T2")}


def kth_tables() -> tuple[pd.DataFrame, pd.DataFrame]:
    """(E8 population with S, O, fin; submitted attempts of E9 with S, O, fin, solved)."""
    pop = pd.read_parquet(e8.OUT_DIR / "runs.parquet")
    pop = pop[pop.setting.isin(ANALYSIS_SETTINGS)].sort_values("run_id").reset_index(drop=True)
    pop["fin"] = pop.ended_by_final_message.astype(int)
    fe = pd.read_csv(KTH_OUTPUTS / "e10_features.csv.gz")
    fe = fe[fe.corpus == "kth"][["run_id"] + O_MAIN]
    pop = pop.merge(fe, on="run_id", how="left")
    pop[O_MAIN] = pop[O_MAIN].fillna(0)
    att = e9.attempts()
    raw = kth_raw()
    err = raw[raw.fin_raw.notna()][["run_id", "fin_raw"] + FEATURES].rename(columns={"fin_raw": "fin"})
    feats = pd.concat([pop[["run_id", "fin"] + FEATURES], err.assign(fin=err.fin.astype(int))], ignore_index=True)
    sub = att[att.submitted].merge(feats, on="run_id", how="left").merge(fe, on="run_id", how="left")
    sub[O_MAIN] = sub[O_MAIN].fillna(0)
    assert sub[FEATURES + ["fin"]].notna().all().all() and len(sub) == int(att.submitted.sum())
    return pop, sub


def kth_risks(pop: pd.DataFrame, sub: pd.DataFrame) -> pd.DataFrame:
    """Out-of-fold risks for every submitted attempt: T1 over the settings pooled (E8 folds), T2 within the setting
    (E9 folds), T3 = T2 fitted on finished attempts only; evaluation-error attempts by the fold model of their task."""
    from sklearn.model_selection import GroupKFold

    out = sub.copy()
    for r in KTH_RISKS:
        out[r] = np.nan
    groups = {"T1": [(pop.index, out.index)]}
    groups["T2"] = [(pop.index[pop.setting == s], out.index[out.setting == s]) for s in ANALYSIS_SETTINGS]
    for scheme in ("T1", "T2", "T3"):
        for pidx, sidx in groups["T2" if scheme == "T3" else scheme]:
            ps = pop.loc[pidx].reset_index(drop=True)
            y = ps.label.to_numpy()
            fold_of = {}
            for k, (_, te) in enumerate(GroupKFold(n_splits=5, shuffle=True, random_state=SEED)
                                        .split(ps, y, ps.task_id.to_numpy())):
                fold_of.update({t: k for t in ps.task_id.iloc[te]})
            pf = ps.task_id.map(fold_of).to_numpy()
            sf = out.loc[sidx, "task_id"].map(fold_of).fillna(-1).astype(int).to_numpy()
            keep = (ps.fin == 1).to_numpy() if scheme == "T3" else np.ones(len(ps), bool)
            blocks = ("S", "S+O") if scheme == "T3" else tuple(KTH_BLOCKS)
            for b in blocks:
                cols, name = KTH_BLOCKS[b], f"{b}-{scheme}"
                for k in list(range(5)) + [-1]:
                    te = sidx[sf == k]
                    if not len(te):
                        continue
                    tr = (keep & (pf != k)) if k >= 0 else keep  # -1: task without labelled runs -> all
                    m = e8.new_model().fit(ps.loc[tr, cols].to_numpy(float), y[tr])
                    out.loc[te, name] = m.predict_proba(out.loc[te, cols].to_numpy(float))[:, 1]
    assert out[KTH_RISKS].notna().all().all()
    return out


def unit_pair_counts(y: np.ndarray, r: np.ndarray, fin: np.ndarray, units: list[np.ndarray]) -> dict:
    """Per unit: concordant (unresolved, resolved) pairs (ties 1/2) and pair counts for each pair set."""
    out = {p: (np.zeros(len(units)), np.zeros(len(units))) for p in PAIR_SETS}
    for i, ix in enumerate(units):
        f, s = ix[y[ix] == 1], ix[y[ix] == 0]
        if not len(f) or not len(s):
            continue
        d = r[f][:, None] - r[s][None, :]
        conc = (d > 0) + 0.5 * (d == 0)
        ff, fs = fin[f][:, None], fin[s][None, :]
        masks = {"all": np.ones_like(conc, bool), "FF": (ff == 1) & (fs == 1), "UU": (ff == 0) & (fs == 0)}
        masks["FU"] = ~masks["FF"] & ~masks["UU"]
        for p, mk in masks.items():
            out[p][0][i], out[p][1][i] = conc[mk].sum(), mk.sum()
    return out


def kth_within(risk_tab: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    lab = risk_tab[risk_tab.label >= 0].sort_values("run_id").reset_index(drop=True)
    lab["unit"] = lab.setting + "|" + lab.task_id
    lab["not finished"] = 1 - lab.fin
    preds = KTH_RISKS + ["not finished", "n_steps"]
    y, fin = lab.label.to_numpy(), lab.fin.to_numpy()
    rows, drows, desc = [], [], {}
    diffs = [("S-T1", "not finished", "all"), ("S-T2", "not finished", "all"), ("S+O-T2", "S-T2", "FF"),
             ("S+O-T1", "S-T1", "FF"), ("S-T1", "S-T2", "all"), ("S-T1", "S-T2", "FF")]
    for scope in list(ANALYSIS_SETTINGS) + ["all"]:
        sub = lab if scope == "all" else lab[lab.setting == scope]
        idx = sub.index.to_numpy()
        units = [idx[g] for g in pd.Series(np.arange(len(sub))).groupby(sub.unit.to_numpy()).apply(np.array)]
        cnt = {p: unit_pair_counts(y, lab[p].to_numpy(float), fin, units) for p in preds}
        rng = np.random.default_rng(SEED)
        draws = [rng.integers(0, len(units), len(units)) for _ in range(N_BOOT)]
        boot = {(p, ps): np.array([c[d].sum() / n[d].sum() if n[d].sum() else np.nan for d in draws])
                for p in preds for ps, (c, n) in cnt[p].items()}
        point = {(p, ps): (c.sum() / n.sum() if n.sum() else np.nan) for p in preds for ps, (c, n) in cnt[p].items()}
        for p in preds:
            for ps in PAIR_SETS:
                lo, hi = _ci(boot[(p, ps)])
                rows.append({"scope": scope, "predictor": p, "pairs": ps, "value": point[(p, ps)], "ci_lo": lo,
                             "ci_hi": hi, "n_pairs": int(cnt[p][ps][1].sum()),
                             "n_units": int((cnt[p][ps][1] > 0).sum())})
        for a, b, ps in diffs:
            lo, hi = _ci(boot[(a, ps)] - boot[(b, ps)])
            drows.append({"scope": scope, "predictor": a, "baseline": b, "pairs": ps,
                          "diff": point[(a, ps)] - point[(b, ps)], "ci_lo": lo, "ci_hi": hi})
        d = sub
        desc[scope] = {"attempts": len(d), "share_finished": float(d.fin.mean()),
                       "unresolved_if_finished": float(d.label[d.fin == 1].mean()),
                       "unresolved_if_not_finished": float(d.label[d.fin == 0].mean()) if (d.fin == 0).any() else None,
                       "median_steps_finished": float(d.n_steps[d.fin == 1].median()),
                       "median_steps_not_finished": float(d.n_steps[d.fin == 0].median()) if (d.fin == 0).any()
                       else None, "units": len(units),
                       **{f"pairs_{ps}": int(cnt["S-T1"][ps][1].sum()) for ps in PAIR_SETS}}
    res = pd.DataFrame(rows)
    want = _expected(EXPECTED_KTH)
    got = {key: round(float(res[(res.predictor == key[0]) & (res.scope == key[1]) & (res.pairs == "all")]
                            .value.iloc[0]), 3) for key in want}
    if got != want:
        raise SystemExit(f"STOP: KTH within-unit reproduction {got}, expected {want}")
    return res, pd.DataFrame(drows), {"descriptives": desc, "reproduced": {f"{k[0]}|{k[1]}": v for k, v in got.items()}}


SEL_RISK = ["S-T1", "S-T2", "S+O-T1", "S+O-T2"]
SEL_FIN = ["S-T1", "S-T2", "S-T3", "S+O-T1", "S+O-T2"]


def rule_values(solved, submitted, fin, risk: dict, idx) -> dict:
    """e9.rule_values plus the base 'random among finished' and the risk rules restricted to finished attempts."""
    t = np.arange(solved.shape[0])[:, None, None]
    sv, sb = solved[t, idx].astype(float), submitted[t, idx]
    fn = fin[t, idx] & sb
    nsb, nfn = sb.sum(-1), fn.sum(-1)
    anyb, anyf = nsb > 0, nfn > 0
    out = {"random": sv.mean(-1),
           "submitted-random": np.where(anyb, (sv * sb).sum(-1) / np.maximum(nsb, 1), sv.mean(-1))}
    out["finished-random"] = np.where(anyf, (sv * fn).sum(-1) / np.maximum(nfn, 1), out["submitted-random"])
    for p in SEL_RISK:
        r = np.where(sb, risk[p][t, idx], np.inf)
        tie = sb & (r == r.min(-1, keepdims=True))
        out[p] = np.where(anyb, (sv * tie).sum(-1) / np.maximum(tie.sum(-1), 1), sv.mean(-1))
    cand = np.where(anyf[..., None], fn, sb)
    for p in SEL_FIN:
        r = np.where(cand, risk[p][t, idx], np.inf)
        tie = cand & (r == r.min(-1, keepdims=True))
        out[f"{p} | finished"] = np.where(anyb, (sv * tie).sum(-1) / np.maximum(tie.sum(-1), 1), sv.mean(-1))
    out["oracle"] = sv.max(-1)
    return {k: v.mean(-1) for k, v in out.items()}


def kth_selection(risk_tab: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    att = e9.attempts().merge(risk_tab[["run_id", "fin"] + KTH_RISKS], on="run_id", how="left")
    att["fin"] = att.fin.fillna(0).astype(int)
    subs = e9.subsets()
    rows = []
    for setting in ANALYSIS_SETTINGS:
        a = att[att.setting == setting].sort_values(["task_id", "j"])
        tasks = a.task_id.unique()
        shape = (500, e9.N_RUNS)
        solved, submitted = a.solved.to_numpy().reshape(shape), a.submitted.to_numpy().reshape(shape)
        fin = a.fin.to_numpy().astype(bool).reshape(shape)
        risk = {p: a[p].to_numpy(float).reshape(shape) for p in KTH_RISKS}
        for k in e9.KS:
            for rule, v in rule_values(solved, submitted, fin, risk, subs[k]).items():
                rows.append(pd.DataFrame({"setting": setting, "task_id": tasks, "k": k, "rule": rule, "value": v}))
    pt = pd.concat(rows, ignore_index=True)
    base_of = {**{r: "submitted-random" for r in ["random", "finished-random", *SEL_RISK, "oracle"]},
               **{f"{p} | finished": "finished-random" for p in SEL_FIN}}
    out = []
    for scope in list(ANALYSIS_SETTINGS) + ["all"]:
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
            for rule in ["submitted-random", *base_of]:
                b = base_of.get(rule, "submitted-random")
                dl = (boot[(k, rule)] - boot[(k, b)]) * 100
                lo, hi = _ci(dl)
                out.append({"scope": scope, "k": k, "rule": rule, "baseline": b, "solved": point[(k, rule)],
                            "solved_lo": _ci(boot[(k, rule)])[0], "solved_hi": _ci(boot[(k, rule)])[1],
                            "delta_pp": (point[(k, rule)] - point[(k, b)]) * 100, "ci_lo": lo, "ci_hi": hi})
    res = pd.DataFrame(out)
    want = _expected(EXPECTED_SEL)
    got = {key: round(float(res[(res.scope == key[1]) & (res.k == 5) & (res.rule == key[0])].delta_pp.iloc[0]), 2)
           for key in want}
    if got != want:
        raise SystemExit(f"STOP: selection reproduction {got}, expected {want}")
    return res, {"reproduced": {f"{k[0]}|{k[1]}": v for k, v in got.items()},
                 "attempts_finished_share_submitted": float(att.fin[att.submitted].mean())}


def part_kth() -> dict:
    t0 = time.time()
    pop, sub = kth_tables()
    risk_tab = kth_risks(pop, sub)
    # label comes from e9.attempts (0/1; -1 for evaluation errors); n_steps is in FEATURES
    assert (risk_tab.label >= 0).sum() == len(pop)
    log.info("KTH risks (%.0f s)", time.time() - t0)
    win, wdiff, winfo = kth_within(risk_tab)
    log.info("KTH within (%.0f s)", time.time() - t0)
    sel, sinfo = kth_selection(risk_tab)
    log.info("KTH selection (%.0f s)", time.time() - t0)
    KTH_OUTPUTS.mkdir(parents=True, exist_ok=True)
    win.round(6).to_csv(KTH_OUTPUTS / "e15_kth_within.csv", index=False)
    wdiff.round(6).to_csv(KTH_OUTPUTS / "e15_kth_within_diffs.csv", index=False)
    sel.round(6).to_csv(KTH_OUTPUTS / "e15_kth_selection.csv", index=False)
    info = {**winfo, "selection": sinfo, "settings": list(ANALYSIS_SETTINGS), "seconds": round(time.time() - t0, 1)}
    _update_summary("kth", info, KTH_OUTPUTS)
    return info


# ================================================================================ part 3: variance and noise

def unit_counts_matrix(df: pd.DataFrame, ycol: str) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """(failures, attempts) as (tasks, settings) matrices; settings in ANALYSIS_SETTINGS order."""
    sets = list(ANALYSIS_SETTINGS)
    g = df.groupby(["task_id", "setting"])[ycol].agg(["sum", "size"]).reset_index()
    tasks = sorted(df.task_id.unique())
    ti = {t: i for i, t in enumerate(tasks)}
    s, n = np.zeros((len(tasks), len(sets))), np.zeros((len(tasks), len(sets)))
    for r in g.itertuples(index=False):
        s[ti[r.task_id], sets.index(r.setting)] = r.sum
        n[ti[r.task_id], sets.index(r.setting)] = r.size
    return s, n, tasks


def _gh(q: int) -> tuple[np.ndarray, np.ndarray]:
    x, w = np.polynomial.hermite_e.hermegauss(q)
    return x, np.log(w / w.sum())


def glmm_nll(theta: np.ndarray, s: np.ndarray, n: np.ndarray, q: int) -> float:
    """-log L of logit P = mu_s + a_t + c_ts by nested Gauss-Hermite (inner over c_ts, outer over a_t)."""
    ns = s.shape[1]
    mu, sa, sc = theta[:ns], np.exp(theta[ns]), np.exp(theta[ns + 1])
    x, lw = _gh(q)
    eta = mu[None, :, None, None] + sa * x[None, None, :, None] + sc * x[None, None, None, :]
    ll = s[:, :, None, None] * -np.logaddexp(0, -eta) + (n - s)[:, :, None, None] * -np.logaddexp(0, eta)
    inner = logsumexp(ll + lw[None, None, None, :], axis=3)
    inner = np.where(n[:, :, None] > 0, inner, 0.0)
    return float(-logsumexp(inner.sum(1) + lw[None, :], axis=1).sum())


def glmm_fit(s: np.ndarray, n: np.ndarray, q: int, x0: np.ndarray | None = None) -> np.ndarray:
    ns = s.shape[1]
    if x0 is None:
        p = np.clip(s.sum(0) / np.maximum(n.sum(0), 1), 0.01, 0.99)
        x0 = np.r_[np.log(p / (1 - p)), 0.0, 0.0]
    r = minimize(glmm_nll, x0, args=(s, n, q), method="L-BFGS-B")
    if not r.success:
        log.warning("GLMM fit: %s", r.message)
    return r.x


def glmm1_fit(s: np.ndarray, n: np.ndarray, q: int) -> np.ndarray:
    """One setting: logit P = mu + u_t (s, n: vectors over tasks with n > 0)."""
    x, lw = _gh(q)

    def nll(th):
        eta = th[0] + np.exp(th[1]) * x[None, :]
        ll = s[:, None] * -np.logaddexp(0, -eta) + (n - s)[:, None] * -np.logaddexp(0, eta)
        return float(-logsumexp(ll + lw[None, :], axis=1).sum())

    p = np.clip(s.sum() / n.sum(), 0.01, 0.99)
    return minimize(nll, np.r_[np.log(p / (1 - p)), 0.0], method="L-BFGS-B").x


def latent_shares(theta: np.ndarray, n: np.ndarray) -> dict[str, float]:
    ns = n.shape[1]
    mu = theta[:ns]
    w = n.sum(0) / n.sum()
    v = {"model": float((w * (mu - (w * mu).sum()) ** 2).sum()), "task": float(np.exp(theta[ns]) ** 2),
         "task_x_model": float(np.exp(theta[ns + 1]) ** 2), "attempt": float(np.pi ** 2 / 3)}
    tot = sum(v.values())
    return {**{f"var_{k}": x for k, x in v.items()}, **{f"share_{k}": x / tot for k, x in v.items()}}


def observed_shares(s: np.ndarray, n: np.ndarray) -> dict[str, float]:
    """Protocol 3.4: within-unit variance (unbiased) and a two-way ANOVA of unit means on tasks with all units n >= 2."""
    N = n.sum()
    pbar = s.sum() / N
    ok_u = n >= 2
    p = np.where(n > 0, s / np.maximum(n, 1), 0.0)
    within = float((n * p * (1 - p) * np.where(ok_u, n / np.maximum(n - 1, 1), 0.0)).sum() / n[ok_u].sum())
    full = ok_u.all(1)
    m, v = p[full], (p * (1 - p) / np.maximum(n - 1, 1))[full]
    T, S = m.shape
    mt, ms, mg = m.mean(1), m.mean(0), m.mean()
    res = m - mt[:, None] - ms[None, :] + mg
    ms_res = float((res ** 2).sum() / ((T - 1) * (S - 1)))
    v_model = float(((ms - mg) ** 2).mean())
    v_tx = ms_res - float(v.mean())
    v_task = float(mt.var(ddof=1) - ms_res / S)
    vv = {"model": v_model, "task": v_task, "task_x_model": v_tx, "attempt": within}
    tot = sum(vv.values())
    return {"var_y": float(pbar * (1 - pbar)), "tasks_in_anova": int(T), **{f"var_{k}": x for k, x in vv.items()},
            **{f"share_{k}": x / tot for k, x in vv.items()}}


def variance_population(name: str, s: np.ndarray, n: np.ndarray) -> tuple[list[dict], dict]:
    t0 = time.time()
    th = glmm_fit(s, n, Q_POINT)
    th60 = glmm_fit(s, n, Q_CHECK, x0=th)
    ns = s.shape[1]
    info = {"theta_q40": th.tolist(), "theta_q60": th60.tolist(),
            "q60_max_abs_diff": float(np.abs(np.r_[th[:ns], np.exp(th[ns:])] - np.r_[th60[:ns], np.exp(th60[ns:])])
                                      .max()),
            "sigma_task": float(np.exp(th[ns])), "sigma_task_x_model": float(np.exp(th[ns + 1])),
            "mu": dict(zip([SHORT[k] for k in ANALYSIS_SETTINGS], th[:ns].tolist())),
            "fit_seconds": round(time.time() - t0, 1)}
    # recovery: one sample simulated from the estimates with the same attempt counts
    rng = np.random.default_rng(SEED)
    a = rng.normal(0, np.exp(th[ns]), s.shape[0])
    c = rng.normal(0, np.exp(th[ns + 1]), s.shape)
    s_sim = rng.binomial(n.astype(int), expit(th[None, :ns] + a[:, None] + c))
    info["recovery_theta"] = glmm_fit(s_sim.astype(float), n, Q_POINT).tolist()
    lat, obs = latent_shares(th, n), observed_shares(s, n)
    # per-setting attempt share (model mu + u_t)
    per = {}
    for j, st in enumerate(ANALYSIS_SETTINGS):
        ok = n[:, j] > 0
        t1 = glmm1_fit(s[ok, j], n[ok, j], Q_POINT)
        su2 = float(np.exp(t1[1]) ** 2)
        per[SHORT[st]] = {"mu": float(t1[0]), "sigma_unit": float(np.exp(t1[1])),
                          "share_attempt": float(np.pi ** 2 / 3 / (su2 + np.pi ** 2 / 3))}
    # bootstrap over tasks
    rng = np.random.default_rng(SEED)
    draws = [rng.integers(0, s.shape[0], s.shape[0]) for _ in range(N_BOOT)]
    bl = []
    for d in draws[:N_BOOT_GLMM]:
        bl.append(latent_shares(glmm_fit(s[d], n[d], Q_BOOT, x0=th), n[d]))
    bo = [observed_shares(s[d], n[d]) for d in draws]
    rows = []
    for scale, point, boots in (("latent", lat, bl), ("observed", obs, bo)):
        for k, v in point.items():
            if not (k.startswith("share_") or k.startswith("var_")):
                continue
            lo, hi = _ci(np.array([b[k] for b in boots]))
            rows.append({"population": name, "scale": scale, "component": k, "value": v, "ci_lo": lo, "ci_hi": hi,
                         "n_boot": len(boots)})
    for st, v in per.items():
        rows.append({"population": name, "scale": "latent_per_setting", "component": f"share_attempt|{st}",
                     "value": v["share_attempt"], "ci_lo": np.nan, "ci_hi": np.nan, "n_boot": 0})
    info.update({"observed": obs, "per_setting": per, "seconds": round(time.time() - t0, 1)})
    log.info("variance %s done (%.0f s): %s", name, time.time() - t0, lat)
    return rows, info


def identical_patch_noise(lab: pd.DataFrame, settings=None) -> tuple[pd.DataFrame, dict]:
    """Protocol 3.6 (1)-(3): label disagreement on byte-identical patches within a unit; share s of within-unit
    (unresolved, resolved) pairs with identical patches; eps of the symmetric noise model."""
    rows, per_scope = [], {}
    for scope in list(ANALYSIS_SETTINGS if settings is None else settings) + ["all"]:
        d = lab if scope == "all" else lab[lab.setting == scope]
        units = list(d.groupby("unit"))
        pairs_same = np.zeros(len(units))
        disc_same = np.zeros(len(units))
        fs_pairs = np.zeros(len(units))
        for i, (_, g) in enumerate(units):
            f = int(g.label.sum())
            fs_pairs[i] = f * (len(g) - f)
            h = g[g.diff_hash != ""]
            for _, gg in h.groupby("diff_hash"):
                m, ff = len(gg), int(gg.label.sum())
                pairs_same[i] += m * (m - 1) / 2
                disc_same[i] += ff * (m - ff)
        rng = np.random.default_rng(SEED)
        draws = [rng.integers(0, len(units), len(units)) for _ in range(N_BOOT)]
        dd = disc_same.sum() / pairs_same.sum()
        sh = disc_same.sum() / fs_pairs.sum()
        bd = np.array([disc_same[x].sum() / pairs_same[x].sum() for x in draws])
        bs = np.array([disc_same[x].sum() / fs_pairs[x].sum() for x in draws])
        eps = lambda v: (1 - np.sqrt(np.clip(1 - 2 * v, 0, None))) / 2  # noqa: E731
        res = {"identical_pairs": int(pairs_same.sum()), "units_with_identical_pairs": int((pairs_same > 0).sum()),
               "discordant_identical_pairs": int(disc_same.sum()), "d": float(dd), "d_ci": list(_ci(bd)),
               "eps": float(eps(dd)), "eps_ci": [float(eps(x)) for x in _ci(bd)],
               "within_pairs": int(fs_pairs.sum()), "s": float(sh), "s_ci": list(_ci(bs)),
               "ceiling_direct": float(1 - sh / 2), "ceiling_direct_ci": [float(1 - x / 2) for x in _ci(bs)[::-1]],
               "share_attempts_with_empty_diff": float((d.diff_hash == "").mean())}
        per_scope[scope] = res
        rows.append({"scope": scope, **{k: v for k, v in res.items() if not isinstance(v, list)},
                     "d_lo": res["d_ci"][0], "d_hi": res["d_ci"][1], "eps_lo": res["eps_ci"][0],
                     "eps_hi": res["eps_ci"][1], "s_lo": res["s_ci"][0], "s_hi": res["s_ci"][1]})
    return pd.DataFrame(rows), per_scope


def simulate_ceiling(theta: np.ndarray, settings: list[int], eps: float, n_sim: int = N_SIM, n_tasks: int = 500,
                     n_att: int = 10, seed: int = SEED, sigma_unit: dict | None = None) -> dict[str, float]:
    """Protocol 3.6 (4)-(5): within-unit AUROC of a predictor that knows the true outcome t, against labels flipped
    with probability eps; and overall AUROC of a predictor that knows only the true unit probability."""
    rng = np.random.default_rng(seed)
    w_auc, o_auc = [], []
    for _ in range(n_sim):
        a = rng.normal(0, np.exp(theta[-2]), n_tasks)
        ys, ts, ps = [], [], []
        for j in settings:
            if sigma_unit is None:
                eta = theta[j] + a + rng.normal(0, np.exp(theta[-1]), n_tasks)
            else:
                mu_j, su = sigma_unit[j]
                eta = mu_j + rng.normal(0, su, n_tasks)
            p = expit(eta)  # probability of failure of the unit
            t = rng.random((n_tasks, n_att)) < p[:, None]  # true failure
            ys.append(t ^ (rng.random((n_tasks, n_att)) < eps))  # observed label
            ts.append(t)
            ps.append(np.repeat(p[:, None], n_att, 1))
        y, t, p = (np.concatenate(z) for z in (ys, ts, ps))  # (units, n_att)
        # risk = t: pairs (observed failure, observed success) of a unit; a = y1 t1, b = y1 t0, c = y0 t1, d = y0 t0
        a_, b_ = (y & t).sum(1), (y & ~t).sum(1)
        c_, d_ = (~y & t).sum(1), (~y & ~t).sum(1)
        conc, ties, tot = a_ * d_, a_ * c_ + b_ * d_, (a_ + b_) * (c_ + d_)
        w_auc.append(float((conc + 0.5 * ties).sum() / tot.sum()))
        o_auc.append(e8.auroc(y.ravel().astype(int), p.ravel()))
    return {"within_ceiling": float(np.mean(w_auc)), "within_mc_lo": float(np.percentile(w_auc, 2.5)),
            "within_mc_hi": float(np.percentile(w_auc, 97.5)), "unit_oracle_overall": float(np.mean(o_auc))}


def part_variance() -> dict:
    t0 = time.time()
    pop = pd.read_parquet(e8.OUT_DIR / "runs.parquet")
    pop = pop[pop.setting.isin(ANALYSIS_SETTINGS)].sort_values("run_id").reset_index(drop=True)
    att = e9.attempts()
    att["fail"] = (~att.solved).astype(int)
    rows, info = [], {}
    for name, df, ycol in (("E8_labelled", pop, "label"), ("all_attempts", att, "fail")):
        s, n, _ = unit_counts_matrix(df, ycol)
        r, inf = variance_population(name, s, n)
        rows += r
        info[name] = inf
    KTH_OUTPUTS.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).round(6).to_csv(KTH_OUTPUTS / "e15_variance.csv", index=False)
    # noise and ceiling on the E8 population
    raw = kth_raw()
    lab = pop.merge(raw[["run_id", "diff_hash", "diff_len"]], on="run_id", how="left")
    assert lab.diff_hash.notna().all()
    lab["unit"] = lab.setting + "|" + lab.task_id
    noise_tab, noise = identical_patch_noise(lab)
    th = np.array(info["E8_labelled"]["theta_q40"])
    per = info["E8_labelled"]["per_setting"]
    sig1 = {j: (per[SHORT[st]]["mu"], per[SHORT[st]]["sigma_unit"]) for j, st in enumerate(ANALYSIS_SETTINGS)}
    sims = []
    scopes = [(st, [j]) for j, st in enumerate(ANALYSIS_SETTINGS)] + [("all", list(range(len(ANALYSIS_SETTINGS))))]
    for scope, js in scopes:
        nz = noise[scope]
        eps_list = [("eps_hat", nz["eps"]), ("eps_lo", nz["eps_ci"][0]), ("eps_hi", nz["eps_ci"][1])] + \
                   [(f"grid_{e}", e) for e in EPS_GRID]
        for label, e in eps_list:
            sims.append({"scope": scope, "model": "joint (3.2)", "eps_kind": label, "eps": e,
                         **simulate_ceiling(th, js, e)})
        # outside the protocol (sensitivity): per-setting models mu + u_t
        sims.append({"scope": scope, "model": "per-setting (sensitivity)", "eps_kind": "eps_hat", "eps": nz["eps"],
                     **simulate_ceiling(th, js, nz["eps"], sigma_unit=sig1)})
        log.info("ceiling %s done (%.0f s)", scope, time.time() - t0)
    sim = pd.DataFrame(sims)
    noise_tab.round(6).to_csv(KTH_OUTPUTS / "e15_noise.csv", index=False)
    sim.round(6).to_csv(KTH_OUTPUTS / "e15_ceiling.csv", index=False)
    info["noise"] = noise
    info["settings"] = list(ANALYSIS_SETTINGS)
    info["seconds"] = round(time.time() - t0, 1)
    _update_summary("variance", info, KTH_OUTPUTS)
    return info


def label_check() -> dict:
    """Exploratory, outside the protocol (found while reading part 3): do the harness result files of a setting belong to
    its nano-agent trajectories? Per setting: the name of the result file, labelled attempts with an empty
    generated_diff (an empty patch would be in empty_patch_ids, not labelled) and label disagreement on identical
    patches (identical_patch_noise over all three settings) -> outputs/e15_label_check.json. Fuller evidence:
    src.kth_deepswe_labels, reports/KTH_DEEPSWE_LABELS.md."""
    from src.adapters.agentic_evals import RAW_DIR

    pop = pd.read_parquet(e8.OUT_DIR / "runs.parquet")
    raw = kth_raw()
    lab = pop.merge(raw[["run_id", "diff_hash"]], on="run_id")
    lab["unit"] = lab.setting + "|" + lab.task_id
    noise = identical_patch_noise(lab, list(SETTINGS))[0].set_index("scope")
    out = {}
    for s in SETTINGS:
        x = lab[lab.setting == s]
        files = sorted(p.name for r in run_dirs(s) for p in r.glob("*.json"))
        e = x[x.diff_hash == ""]
        out[SHORT[s]] = {"result_files_first": files[0], "result_files_named_nano_agent": int(sum("nano-agent" in f
                                                                                                    for f in files)),
                         "labelled_attempts": len(x), "labelled_with_empty_generated_diff": len(e),
                         "unresolved_if_empty_generated_diff": float(e.label.mean()) if len(e) else None,
                         "identical_patch_pairs": int(noise.loc[s, "identical_pairs"]),
                         "identical_patch_label_disagreement": round(float(noise.loc[s, "d"]), 6)}
    tmp = RAW_DIR / "nano-agent-agentica-org_DeepSWE-Preview__temp0"
    out["DeepSWE-Preview__temp0_result_file_first"] = sorted(p.name for p in tmp.glob("run_*/*.json"))[0]
    (OUTPUTS / "e15_label_check.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    parts = {"lb": part_leaderboard, "kth": part_kth, "variance": part_variance}
    if cmd == "labels":
        print(json.dumps(label_check(), indent=1))
    else:
        for p in (parts if cmd == "all" else [cmd]):
            parts[p]()
