"""E13 (reports/E13_PROTOCOL.md): transfer between agent generations, predictability vs model strength, patch block P.
No LLM calls.

  python -m src.e13_generations_patch patches  -> data/interim/e13/{lb,kth}_patches.parquet (final diffs -> P)
  python -m src.e13_generations_patch run      -> outputs/e13_*.csv, e13_patch_features.csv.gz, e13_summary.json

Leaderboard: the E7 population, folds, model (StandardScaler + LR C=1) and cluster bootstrap over tasks (1000,
seed 42). Transfer: train on one generation (v1 = agent 1.x, v2 = 2.0.0) and the tasks outside fold k, test on the
other generation and the tasks of fold k. P = 6 features of the run's final diff (leaderboard: logs/<task>/patch.diff
of the bucket, protocol 3a; KTH: generated_diff); the reference patch and the hidden tests are never read. KTH: S and S+P within the devstral setting
on the E9 folds, within-unit AUROC (E8 code).
"""
from __future__ import annotations

import json
import re
import sys
import warnings
from itertools import combinations

import numpy as np
import pandas as pd
from scipy.stats import ks_2samp, spearmanr

from src import e7_sources as e7
from src import e8_repeats as e8
from src.adapters.agentic_evals import RAW_DIR as KTH_RAW
from src.common import INTERIM, OUTPUTS, RAW, SEED, get_logger
from src.e7_sources import FEATURES
from src.e10_check_outcomes import _TESTFILE_RE

log = get_logger("e13")
OUT_DIR = INTERIM / "e13"
LB_RAW = RAW / "swebench_bashonly"
PATCH_RAW = RAW / "swebench_bashonly_patches"  # logs/<task>/patch.diff only (protocol 3a)
KTH_SETTING = "nano-agent-mistral_devstral-2512"
P_COUNTS = ["p_n_files", "p_lines_added", "p_lines_removed", "p_new_files"]  # log1p
P_FLAGS = ["p_edits_tests", "p_outside_package"]
P_FEATURES = P_COUNTS + P_FLAGS
FORMAT_FEATURES = ["n_steps", "share_verify", "has_any_verification", "verif_after_last_edit", "phase_bigram_entropy"]
SAME_MODEL = {"claude-sonnet-4-5": ("claude-sonnet-4-5-20250929", "claude-sonnet-4-5-20250929@high"),
              "gemini-3-pro-preview": ("gemini-3-pro-preview", "gemini-3-pro-preview@high")}
EXPECTED_MS = (0.685, 0.689)  # E7
EXPECTED_KTH_S = 0.561  # E10: S within units, devstral
LITERATURE_RHO = -0.662  # arXiv 2610.05572, as given by the author
N_BOOT = 1000

# ------------------------------------------------------------------------------------------ block P

_FILE_RE = re.compile(r"^diff --git a/(.*?) b/(.*)$", re.M)
_CONFIG_NAMES = {"setup.py", "setup.cfg", "pyproject.toml", "tox.ini", "MANIFEST.in", "Makefile"}
_CONFIG_EXT = (".cfg", ".ini", ".toml", ".yml", ".yaml")


def outside_package(path: str) -> bool:
    """Protocol 3.2: a root-level file, a build / configuration file anywhere, or .github/."""
    base = path.rsplit("/", 1)[-1]
    return ("/" not in path or base in _CONFIG_NAMES or base.endswith(_CONFIG_EXT) or path.startswith(".github/")
            or bool(re.match(r"requirements.*\.txt$", base)))


def diff_features(diff: str | None) -> dict:
    """The 6 raw features of protocol 3.2 (counts before log1p) of one git diff."""
    d = diff or ""
    files = [b for _, b in _FILE_RE.findall(d)]
    lines = d.splitlines()
    return {"p_n_files": len(files),
            "p_lines_added": sum(x.startswith("+") and not x.startswith("+++") for x in lines),
            "p_lines_removed": sum(x.startswith("-") and not x.startswith("---") for x in lines),
            "p_new_files": sum(x.startswith("new file mode") for x in lines),
            "p_edits_tests": int(any(_TESTFILE_RE.search(f) for f in files)),
            "p_outside_package": int(any(outside_package(f) for f in files)),
            "p_empty": int(not d.strip())}


def p_block(df: pd.DataFrame) -> np.ndarray:
    return np.column_stack([np.log1p(df[P_COUNTS].to_numpy(float)), df[P_FLAGS].to_numpy(float)])


def download_lb_patches(pop: pd.DataFrame) -> dict:
    """Protocol 3a: only logs/<task>/patch.diff of every run (the model's own submitted patch) from the bucket."""
    from concurrent.futures import ThreadPoolExecutor

    from src.adapters import swebench_bashonly as sb

    cfg, s = sb.load_cfg(), sb._session(16)

    def one(rid):
        sub, task = rid.split("/")
        return rid, sb._download_file(s, f"{cfg['s3_base']}/{cfg['s3_prefix']}/{sub}/logs/{task}/patch.diff",
                                      PATCH_RAW / sub / f"{task}.patch.diff")[0]

    with ThreadPoolExecutor(16) as ex:
        ok = dict(ex.map(one, pop.run_id))
    return {"requested": len(ok), "downloaded": int(sum(ok.values()))}


def lb_patches(pop: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """P of every leaderboard run from patch.diff (protocol 3a); check against info.submission of the local
    trajectories; fallback to the local submission, else empty."""
    path, info_path = OUT_DIR / "lb_patches.parquet", OUT_DIR / "lb_patches_info.json"
    if path.exists():
        return pd.read_parquet(path), json.loads(info_path.read_text(encoding="utf-8"))
    info = download_lb_patches(pop)
    rows, same, compared, fallback, empty = [], 0, 0, 0, 0
    for rid in pop.run_id:
        sub, task = rid.split("/")
        pf, tf = PATCH_RAW / sub / f"{task}.patch.diff", LB_RAW / sub / "trajs" / f"{task}.traj.json"
        diff = pf.read_text(encoding="utf-8", errors="replace") if pf.exists() else None
        local = None
        if tf.exists():
            local = (json.loads(tf.read_text(encoding="utf-8")).get("info") or {}).get("submission") or ""
            if diff is not None:
                compared += 1
                same += int(diff.rstrip() == local.rstrip())
        if diff is None:
            if local is not None:
                diff, fallback = local, fallback + 1
            else:
                diff, empty = "", empty + 1
        rows.append({"run_id": rid, **diff_features(diff)})
    info.update({"compared_with_local_submission": compared, "identical": same, "fallback_local": fallback,
                 "missing_empty": empty})
    df = pd.DataFrame(rows)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    info_path.write_text(json.dumps(info, indent=1), encoding="utf-8")
    return df, info


def kth_patches() -> pd.DataFrame:
    path = OUT_DIR / "kth_patches.parquet"
    if path.exists():
        return pd.read_parquet(path)
    rows = []
    for rd in sorted((KTH_RAW / KTH_SETTING).glob("run_*")):
        with open(rd / "detailed_predictions.jsonl", encoding="utf-8") as fh:
            for line in fh:
                rec = json.loads(line)
                rows.append({"run_id": f"{KTH_SETTING}/{rd.name}/{rec['instance_id']}",
                             **diff_features(rec["detailed_predictions"].get("generated_diff"))})
    df = pd.DataFrame(rows)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    return df


# ------------------------------------------------------------------------------------------ common

def fit_predict(x: np.ndarray, y: np.ndarray, tr: np.ndarray, te: np.ndarray) -> tuple[np.ndarray, int]:
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler

    sc = StandardScaler().fit(x[tr])
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        m = LogisticRegression(C=1.0, max_iter=5000).fit(sc.transform(x[tr]), y[tr])
        n_warn = sum("converge" in str(i.message).lower() for i in w)
    return m.predict_proba(sc.transform(x[te]))[:, 1], n_warn


def boot_eval(tasks: np.ndarray, y: np.ndarray, risk: dict[str, np.ndarray], diffs: list[tuple[str, str]],
              scope: str) -> tuple[list[dict], list[dict]]:
    """E7 metrics with a cluster bootstrap over tasks (1000 draws, seed 42, common to all models and metrics)."""
    task_rows = list(pd.Series(np.arange(len(y))).groupby(tasks).apply(np.array))
    counts = {m: e7.pair_counts(y, r, task_rows) for m, r in risk.items()}
    point = {m: e7.metrics_of(y, risk[m], *counts[m]) for m in risk}
    rng = np.random.default_rng(SEED)
    draws = [rng.integers(0, len(task_rows), len(task_rows)) for _ in range(N_BOOT)]
    boot = {m: {k: np.zeros(N_BOOT) for k in e7.METRICS} for m in risk}
    for b, dr in enumerate(draws):
        ix = np.concatenate([task_rows[i] for i in dr])
        for m in risk:
            c, n = counts[m]
            for k, v in e7.metrics_of(y[ix], risk[m][ix], c[dr], n[dr]).items():
                boot[m][k][b] = v
    mrows = [{"scope": scope, "model": m, "metric": k, "value": point[m][k],
              **dict(zip(("ci_lo", "ci_hi"), np.percentile(boot[m][k], [2.5, 97.5])))}
             for m in risk for k in e7.METRICS]
    drows = [{"scope": scope, "model": a, "baseline": b, "metric": k, "diff": point[a][k] - point[b][k],
              **dict(zip(("ci_lo", "ci_hi"), np.percentile(boot[a][k] - boot[b][k], [2.5, 97.5])))}
             for a, b in diffs for k in e7.METRICS]
    return mrows, drows


def blocks_of(pop: pd.DataFrame, tr: np.ndarray, p: np.ndarray | None = None) -> dict[str, np.ndarray]:
    from sklearn.preprocessing import OneHotEncoder

    oh = OneHotEncoder(handle_unknown="ignore", sparse_output=False).fit(pop.loc[tr, ["combo"]])
    out = {"S": pop[FEATURES].to_numpy(float), "M": oh.transform(pop[["combo"]])}
    if p is not None:
        out["P"] = p
    return out


# ------------------------------------------------------------------------------------------ part 1: transfer

def transfer(pop: pd.DataFrame, y: np.ndarray, fold: np.ndarray) -> tuple[pd.DataFrame, dict]:
    gen = np.where(pop.agent_version == "2.0.0", "v2", "v1")
    rows, info = [], {"convergence_warnings": 0}
    for b in ("v2", "v1"):
        a = "v1" if b == "v2" else "v2"
        test = gen == b
        risk = {}
        for src in (a, b):
            for name, bl in (("S", ["S"]), ("M+S", ["M", "S"])):
                r = np.full(len(pop), np.nan)
                for k in range(5):
                    tr, te = (gen == src) & (fold != k), test & (fold == k)
                    blk = blocks_of(pop, tr)
                    x = np.hstack([blk[q] for q in bl])
                    r[te], w = fit_predict(x, y, tr, te)
                    info["convergence_warnings"] += w
                risk[f"{name} {src}->{b}"] = r[test]
        m, d = boot_eval(pop.task_id.to_numpy()[test], y[test], risk,
                         [(f"S {b}->{b}", f"S {a}->{b}"), (f"M+S {b}->{b}", f"M+S {a}->{b}"),
                          (f"M+S {a}->{b}", f"S {a}->{b}")], f"test {b}")
        rows += [{**x, "kind": "model"} for x in m] + [{**x, "kind": "diff"} for x in d]
        info[f"test_{b}"] = {"runs": int(test.sum()), "tasks": int(pop.task_id[test].nunique()),
                             "unresolved_share": float(y[test].mean())}
    return pd.DataFrame(rows), info


def format_check(pop: pd.DataFrame) -> pd.DataFrame:
    """Protocol 1.6: the same model in both generations (tasks submitted in both) and, for scale, the median |SMD|
    between different combos within one generation."""
    def smd(a, b):
        sd = np.sqrt((a.var(ddof=1) + b.var(ddof=1)) / 2)
        return float((b.mean() - a.mean()) / sd) if sd > 0 else 0.0

    rows = []
    by = {m: g.set_index("task_id") for m, g in pop.groupby("model")}
    for name, (m1, m2) in SAME_MODEL.items():
        common = by[m1].index.intersection(by[m2].index)
        for f in FORMAT_FEATURES:
            a, b = by[m1].loc[common, f].astype(float), by[m2].loc[common, f].astype(float)
            s = smd(a, b)
            rows.append({"what": "same model", "model": name, "feature": f, "n_tasks": len(common),
                         "mean_v1": a.mean(), "mean_v2": b.mean(), "median_v1": a.median(), "median_v2": b.median(),
                         "smd": s, "ks_d": float(ks_2samp(a, b).statistic),
                         "verdict": "noticeable" if abs(s) >= 0.5 else "moderate" if abs(s) >= 0.2 else "small"})
    gen = pop.drop_duplicates("model").set_index("model").agent_version.eq("2.0.0").map({True: "v2", False: "v1"})
    for g in ("v1", "v2"):
        models = sorted(gen.index[gen == g])
        for f in FORMAT_FEATURES:
            vals = []
            for m1, m2 in combinations(models, 2):
                common = by[m1].index.intersection(by[m2].index)
                vals.append(abs(smd(by[m1].loc[common, f].astype(float), by[m2].loc[common, f].astype(float))))
            rows.append({"what": "between combos (median |SMD|)", "model": g, "feature": f, "n_tasks": np.nan,
                         "smd": float(np.median(vals)), "ks_d": np.nan, "verdict": f"{len(vals)} pairs"})
    return pd.DataFrame(rows)


# ------------------------------------------------------------------------------------------ part 2: combos

def combos(pop: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    oof = pd.read_csv(OUTPUTS / "e7_oof_risk.csv.gz")
    oof = oof[oof.population == "all"].set_index("run_id")
    d = pop[["run_id", "combo", "agent_version"]].assign(y=(1 - pop.resolved).to_numpy(),
                                                          S=oof.loc[pop.run_id, "S"].to_numpy())
    rows = [{"combo": c, "generation": "v2" if g.agent_version.iloc[0] == "2.0.0" else "v1", "runs": len(g),
             "resolved_share": float(1 - g.y.mean()), "auroc_S": e7.auroc(g.y.to_numpy(), g.S.to_numpy())}
            for c, g in d.groupby("combo")]
    df = pd.DataFrame(rows)
    rho = float(spearmanr(df.resolved_share, df.auroc_S).statistic)
    rng = np.random.default_rng(SEED)
    boot = []
    for _ in range(N_BOOT):
        ix = rng.integers(0, len(df), len(df))
        boot.append(spearmanr(df.resolved_share.to_numpy()[ix], df.auroc_S.to_numpy()[ix]).statistic)
    lo, hi = np.nanpercentile(boot, [2.5, 97.5])
    return df, {"spearman_rho": rho, "ci_lo": float(lo), "ci_hi": float(hi), "n_combos": len(df),
                "nan_draws": int(np.isnan(boot).sum()), "literature_rho": LITERATURE_RHO}


# ------------------------------------------------------------------------------------------ part 3: block P

def patch_models(pop: pd.DataFrame, y: np.ndarray, fold: np.ndarray, pf: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    p = p_block(pf.set_index("run_id").loc[pop.run_id])
    spec = {"P": ["P"], "S": ["S"], "S+P": ["S", "P"], "M+S": ["M", "S"], "M+S+P": ["M", "S", "P"]}
    risk = {m: np.zeros(len(pop)) for m in spec}
    n_warn = 0
    for k in range(5):
        tr, te = fold != k, fold == k
        blk = blocks_of(pop, tr, p)
        for name, bl in spec.items():
            risk[name][te], w = fit_predict(np.hstack([blk[q] for q in bl]), y, tr, te)
            n_warn += w
    task_rows = list(pd.Series(np.arange(len(pop))).groupby(pop.task_id.to_numpy()).apply(np.array))
    c, n = e7.pair_counts(y, risk["M+S"], task_rows)
    got = (round(e7.auroc(y, risk["M+S"]), 3), round(float(c.sum() / n.sum()), 3))
    if got != EXPECTED_MS:
        raise SystemExit(f"STOP: M+S gives {got}, E7 {EXPECTED_MS}")
    m, d = boot_eval(pop.task_id.to_numpy(), y, risk, [("M+S+P", "M+S"), ("S+P", "S")], "leaderboard")
    out = pd.DataFrame([{**x, "kind": "model"} for x in m] + [{**x, "kind": "diff"} for x in d])
    return out, {"M+S_reproduction": got, "convergence_warnings": n_warn,
                 "risk": pd.DataFrame({"run_id": pop.run_id, **risk})}


def kth_patch(pf: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """S and S+P within the devstral setting on the E9 folds; within-unit AUROC with a bootstrap over units."""
    from sklearn.model_selection import GroupKFold

    ps = pd.read_parquet(e8.OUT_DIR / "runs.parquet")
    ps = ps[ps.setting == KTH_SETTING].sort_values("run_id").reset_index(drop=True)
    ps = ps.merge(pf, on="run_id", how="left")
    assert ps[P_FEATURES].notna().all().all()
    y = ps.label.to_numpy()
    fold = np.full(len(ps), -1)
    for k, (_, te) in enumerate(GroupKFold(n_splits=5, shuffle=True, random_state=SEED)
                                .split(ps, y, ps.task_id.to_numpy())):
        fold[te] = k
    x = {"S": ps[FEATURES].to_numpy(float), "S+P": np.hstack([ps[FEATURES].to_numpy(float), p_block(ps)])}
    risk = {m: np.zeros(len(ps)) for m in x}
    for k in range(5):
        for m in x:
            risk[m][fold == k], _ = fit_predict(x[m], y, fold != k, fold == k)
    unit_rows = list(pd.Series(np.arange(len(ps))).groupby(ps.task_id.to_numpy()).apply(np.array))
    counts = {m: e8.unit_counts(y, risk[m], unit_rows) for m in risk}
    rng = np.random.default_rng(SEED)
    draws = [rng.integers(0, len(unit_rows), len(unit_rows)) for _ in range(N_BOOT)]
    boot = {m: np.array([c[d].sum() / n[d].sum() for d in draws]) for m, (c, n) in counts.items()}
    rows = []
    for m, (c, n) in counts.items():
        lo, hi = np.percentile(boot[m], [2.5, 97.5])
        rows.append({"model": m, "metric": "within_unit_auroc", "value": c.sum() / n.sum(), "ci_lo": lo, "ci_hi": hi,
                     "overall_auroc": e8.auroc(y, risk[m])})
    lo, hi = np.percentile(boot["S+P"] - boot["S"], [2.5, 97.5])
    rows.append({"model": "S+P - S", "metric": "within_unit_auroc_diff",
                 "value": rows[1]["value"] - rows[0]["value"], "ci_lo": lo, "ci_hi": hi})
    got = round(float(rows[0]["value"]), 3)
    if got != EXPECTED_KTH_S:
        raise SystemExit(f"STOP: KTH S within units {got}, E10 {EXPECTED_KTH_S}")
    return pd.DataFrame(rows), {"runs": len(ps), "units_with_both": int((counts["S"][1] > 0).sum()),
                                "pairs": int(counts["S"][1].sum()), "S_reproduction": got}


def posthoc_patch(pop: pd.DataFrame, y: np.ndarray, fold: np.ndarray, pf: pd.DataFrame) -> dict:
    """NOT in the protocol (reported as post hoc): (M+S+P) - (M+S) without the runs with an empty diff, and within
    each generation; the same E7 folds (from the full population), model and bootstrap."""
    pf = pf.set_index("run_id").loc[pop.run_id].reset_index()
    gen = np.where(pop.agent_version == "2.0.0", "v2", "v1")
    out = {}
    for name, keep in [("non-empty diffs", pf.p_empty.to_numpy() == 0), ("v1 only", gen == "v1"),
                       ("v2 only", gen == "v2")]:
        p_, y_, f_ = pop[keep].reset_index(drop=True), y[keep], fold[keep]
        p = p_block(pf[keep].reset_index(drop=True))
        risk = {m: np.zeros(len(p_)) for m in ("M+S", "M+S+P")}
        for k in range(5):
            tr, te = f_ != k, f_ == k
            blk = blocks_of(p_, tr, p)
            for m, bl in (("M+S", ["M", "S"]), ("M+S+P", ["M", "S", "P"])):
                risk[m][te], _ = fit_predict(np.hstack([blk[q] for q in bl]), y_, tr, te)
        _, d = boot_eval(p_.task_id.to_numpy(), y_, risk, [("M+S+P", "M+S")], name)
        d = pd.DataFrame(d).set_index("metric")
        out[name] = {"runs": int(keep.sum()), **{k: {"diff": float(d.loc[k, "diff"]), "ci_lo": float(d.loc[k, "ci_lo"]),
                                                     "ci_hi": float(d.loc[k, "ci_hi"])} for k in ("auroc", "within")}}
    return out


# ------------------------------------------------------------------------------------------ run

def run() -> dict:
    pop = e7.load_population(False)
    y = (1 - pop.resolved).to_numpy()
    fold = e7.folds_of(pop, y)
    summ = {}
    tr, summ["transfer"] = transfer(pop, y, fold)
    tr.round(6).to_csv(OUTPUTS / "e13_transfer.csv", index=False)
    s12 = tr[(tr.kind == "model") & (tr.model == "S v1->v2") & (tr.metric == "auroc")].iloc[0]
    summ["criterion_transfer"] = {"auroc_S_v1_to_v2": float(s12.value), "ci_lo": float(s12.ci_lo),
                                  "ci_hi": float(s12.ci_hi), "met": bool(s12.ci_lo > 0.6)}
    format_check(pop).round(6).to_csv(OUTPUTS / "e13_format.csv", index=False)
    cb, summ["combos"] = combos(pop)
    cb.round(6).to_csv(OUTPUTS / "e13_combos.csv", index=False)
    (lbp, summ["patch_source"]), kp = lb_patches(pop), kth_patches()
    pm, info = patch_models(pop, y, fold, lbp)
    pm[pm.kind == "model"].drop(columns=["kind", "baseline", "diff"], errors="ignore").round(6).to_csv(
        OUTPUTS / "e13_patch_metrics.csv", index=False)
    pm[pm.kind == "diff"].drop(columns=["kind", "value"], errors="ignore").round(6).to_csv(
        OUTPUTS / "e13_patch_diffs.csv", index=False)
    crit = pm[(pm.kind == "diff") & (pm.model == "M+S+P") & (pm.metric == "auroc")].iloc[0]
    summ["criterion_patch"] = {"diff": float(crit["diff"]), "ci_lo": float(crit.ci_lo), "ci_hi": float(crit.ci_hi),
                               "met": bool(crit.ci_lo > 0)}
    summ["patch"] = {k: v for k, v in info.items() if k != "risk"}
    summ["patch"]["empty_diffs_lb"] = int(lbp.set_index("run_id").loc[pop.run_id, "p_empty"].sum())
    summ["posthoc_patch"] = posthoc_patch(pop, y, fold, lbp)
    kt, summ["kth"] = kth_patch(kp)
    kt.round(6).to_csv(OUTPUTS / "e13_kth_patch.csv", index=False)
    feats = pd.concat([lbp.assign(corpus="leaderboard"), kp.assign(corpus="kth")], ignore_index=True)
    feats.to_csv(OUTPUTS / "e13_patch_features.csv.gz", index=False)
    summ["kth"]["empty_diffs_kth_setting"] = int(kp.p_empty.sum())
    (OUTPUTS / "e13_summary.json").write_text(json.dumps(summ, indent=1, ensure_ascii=False, default=float),
                                              encoding="utf-8")
    return summ


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "patches":
        lbp_, info_ = lb_patches(e7.load_population(False))
        print(info_, lbp_.describe().T, kth_patches().describe().T, sep="\n")
    else:
        print(json.dumps(run(), indent=1, ensure_ascii=False, default=float))
