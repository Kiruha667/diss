"""E14 (reports/E14_PROTOCOL.md): transfer of S / S+P to new models of the same agent format (v2.0.0). No LLM calls.

  python -m src.e14_new_models  -> outputs/e14_runs.csv, e14_parse.csv, e14_metrics.csv, e14_combos.csv,
                                   e14_summary.json

The four Feb 2026 runs (data/raw/swebench_bashonly_new) are parsed with the same code as the corpus: the E0 adapter,
then notebooks/build_demo_data.record_rows (S via e5.process_record, submitted = exit_status "Submitted", label from
report.json). P from logs/<task>/patch.diff (as E13). Train on the 8 v2.0.0 combos of the corpus (tasks outside fold
k), test on the new combos (tasks of fold k), E7 folds; upper bound: trained within the new combos on the same folds.
"""
from __future__ import annotations

import gzip
import json
import sys
import warnings
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from src import e7_sources as e7
from src import e13_generations_patch as e13
from src import g0_false_success as g0
from src.adapters import swebench_bashonly as sb
from src.common import CONFIG, INTERIM, OUTPUTS, RAW, ROOT, SEED, get_logger, load_leak_markers, trajectories_path
from src.e7_sources import FEATURES
from src.segment import compile_markers

sys.path.insert(0, str(ROOT / "notebooks"))
import build_demo_data as bdd  # noqa: E402

log = get_logger("e14")
OUT_DIR = INTERIM / "e14"
NEW_RAW = RAW / "swebench_bashonly_new"
NEW_RUNS = [("20260217_mini-v2.0.0_claude-4-5-opus-high", "anthropic"), ("20260217_mini-v2.0.0_claude-4-6-opus", "anthropic"),
            ("20260217_mini-v2.0.0_gpt-5-2-high", "openai"), ("20260219_mini-v2.0.0_gpt-5-2-codex", "openai")]
N_CHECK = 50
N_BOOT = 1000


# ------------------------------------------------------------------------------------------ parsing

def reproduction_check() -> dict:
    """Protocol 2: record_rows on 50 random corpus records must reproduce runs.parquet (S, submitted, resolved)."""
    runs = pd.read_parquet(ROOT / "data" / "demo" / "runs.parquet").set_index("run_id")
    idx = set(np.random.default_rng(SEED).choice(len(runs), N_CHECK, replace=False).tolist())
    mre, claim = compile_markers(load_leak_markers()), g0.load_patterns(CONFIG / "claim_patterns.txt")
    bad, n = [], 0
    with gzip.open(trajectories_path("swebench_bashonly"), "rt", encoding="utf-8") as fh:
        for i, line in enumerate(fh):
            if i not in idx:
                continue
            row, _, _ = bdd.record_rows(json.loads(line), mre, claim)
            ref = runs.loc[row["run_id"]]
            n += 1
            for c in FEATURES + ["submitted", "resolved"]:
                if abs(float(row[c]) - float(ref[c])) > 1e-9:
                    bad.append((row["run_id"], c, row[c], ref[c]))
    if n != N_CHECK or bad:
        raise SystemExit(f"STOP: record_rows does not reproduce runs.parquet: checked {n}, mismatches {bad[:5]}")
    return {"checked": n, "mismatches": 0}


def parse_new() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Run rows of the kept trajectories and the exclusion table (E0 adapter + record_rows)."""
    cfg = dict(sb.load_cfg())
    cfg["runs"] = [{"sub": s, "family": f} for s, f in NEW_RUNS]
    mre, claim = compile_markers(load_leak_markers()), g0.load_patterns(CONFIG / "claim_patterns.txt")
    rows, excl = [], []
    for rec, ex in sb.iter_records(cfg, NEW_RAW):
        if ex is not None:
            excl.append(ex)
            continue
        row, _, _ = bdd.record_rows(rec, mre, claim)
        rows.append(row)
    runs = pd.DataFrame(rows)
    runs["combo"] = runs.model + " | " + runs.agent_version
    return runs, pd.DataFrame(excl, columns=["run_id", "task_id", "label", "stage", "reason"])


def describe_runs() -> pd.DataFrame:
    """Every trajectory of the four runs, without parsing the steps: exit status, label, empty diff (protocol 5)."""
    rows = []
    for sub, _ in NEW_RUNS:
        for p in sorted((NEW_RAW / sub / "trajs").glob("*.traj.json")):
            task = p.name[: -len(".traj.json")]
            info = json.loads(p.read_text(encoding="utf-8")).get("info") or {}
            lab = sb.read_label(NEW_RAW / sub / "reports" / f"{task}.report.json", task)
            rows.append({"sub": sub, "task_id": task, "exit_status": str(info.get("exit_status") or ""),
                         "label_unresolved": lab, "submission": info.get("submission") or ""})
    return pd.DataFrame(rows)


def new_patches(desc: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """P of every new trajectory from logs/<task>/patch.diff (as E13), checked against info.submission."""
    cfg, s = sb.load_cfg(), sb._session(16)

    def one(r):
        dest = e13.PATCH_RAW / r.sub / f"{r.task_id}.patch.diff"
        ok, _ = sb._download_file(s, f"{cfg['s3_base']}/{cfg['s3_prefix']}/{r.sub}/logs/{r.task_id}/patch.diff", dest)
        return r.sub, r.task_id, dest.read_text(encoding="utf-8", errors="replace") if ok else None

    with ThreadPoolExecutor(16) as ex:
        got = {(a, b): d for a, b, d in ex.map(one, desc.itertuples())}
    rows, same, missing = [], 0, 0
    for r in desc.itertuples():
        d = got[(r.sub, r.task_id)]
        if d is None:
            missing += 1
            d = r.submission
        else:
            same += int(d.rstrip() == r.submission.rstrip())
        rows.append({"run_id": f"{r.sub}/{r.task_id}", **e13.diff_features(d)})
    return pd.DataFrame(rows), {"patch_files": len(desc) - missing, "missing_used_submission": missing,
                                "identical_to_submission": same}


# ------------------------------------------------------------------------------------------ models

def risks(corpus: pd.DataFrame, new: pd.DataFrame, fold_c: np.ndarray, fold_n: np.ndarray) -> dict[str, np.ndarray]:
    """Out-of-fold risks of the new runs: transfer (train v2 corpus) and upper bound (train new runs)."""
    xs = {"S": lambda d: d[FEATURES].to_numpy(float),
          "S+P": lambda d: np.hstack([d[FEATURES].to_numpy(float), e13.p_block(d)])}
    out = {}
    for name, f in xs.items():
        xc, xn = f(corpus), f(new)
        yc, yn = (1 - corpus.resolved).to_numpy(), (1 - new.resolved).to_numpy()
        tr_risk, ub_risk = np.full(len(new), np.nan), np.full(len(new), np.nan)
        for k in range(5):
            te = fold_n == k
            x = np.vstack([xc, xn])
            y = np.concatenate([yc, yn])
            tr = np.concatenate([fold_c != k, np.zeros(len(new), bool)])
            r, _ = e13.fit_predict(x, y, tr, np.concatenate([np.zeros(len(corpus), bool), te]))
            tr_risk[te] = r
            r, _ = e13.fit_predict(xn, yn, fold_n != k, te)
            ub_risk[te] = r
        out[f"{name} transfer"], out[f"{name} upper"] = tr_risk, ub_risk
    return out


def run() -> dict:
    summ = {"reproduction": reproduction_check()}
    log.info("reproduction check passed")
    runs, excl = parse_new()
    desc = describe_runs()
    pf, summ["patch_source"] = new_patches(desc)
    runs = runs.merge(pf, on="run_id", how="left")
    assert runs[e13.P_FEATURES].notna().all().all()
    parse = desc.assign(run_id=desc["sub"] + "/" + desc.task_id).drop(columns="submission").merge(
        excl[["run_id", "reason"]], on="run_id", how="left").merge(
        pf[["run_id", "p_empty"]], on="run_id", how="left")
    parse["reason"] = parse.reason.fillna("")
    parse.to_csv(OUTPUTS / "e14_parse.csv", index=False)
    per_run = []
    for sub, g in parse.groupby("sub", sort=False):
        sb_ = g[(g.exit_status == "Submitted") & g.label_unresolved.notna()]
        per_run.append({"sub": sub, "trajectories": len(g), "excluded": int((g.reason != "").sum()),
                        "reasons": g.reason[g.reason != ""].value_counts().to_dict(),
                        "submitted": len(sb_), "resolved_share_submitted": float(1 - sb_.label_unresolved.mean()),
                        "empty_diff_share_submitted": float(sb_.p_empty.mean()),
                        "parsed_submitted": int(((runs.run_id.str.startswith(sub + "/")) & (runs.submitted == 1)).sum())})
    summ["new_runs"] = per_run
    new = runs[runs.submitted == 1].sort_values("run_id").reset_index(drop=True)
    new.drop(columns=["final_message"]).to_csv(OUTPUTS / "e14_runs.csv", index=False)

    pop = e7.load_population(False)
    y = (1 - pop.resolved).to_numpy()
    fold_of = pd.Series(e7.folds_of(pop, y), index=pop.task_id).groupby(level=0).first()
    lbp = pd.read_parquet(e13.OUT_DIR / "lb_patches.parquet")
    corpus = pop[pop.agent_version == "2.0.0"].merge(lbp, on="run_id", how="left").reset_index(drop=True)
    assert corpus[e13.P_FEATURES].notna().all().all() and len(corpus) == 3956
    fold_c, fold_n = fold_of.loc[corpus.task_id].to_numpy(), fold_of.loc[new.task_id].to_numpy()
    rk = risks(corpus, new, fold_c, fold_n)
    yn = (1 - new.resolved).to_numpy()
    rows = []
    diffs = [("S upper", "S transfer"), ("S+P transfer", "S transfer"), ("S+P upper", "S+P transfer")]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # within-task is undefined for one combo (0 / 0)
        for scope, keep in [("new combos together", np.ones(len(new), bool))] + [
                (c, (new.combo == c).to_numpy()) for c in sorted(new.combo.unique())]:
            m, d = e13.boot_eval(new.task_id.to_numpy()[keep], yn[keep], {k: v[keep] for k, v in rk.items()}, diffs,
                                 scope)
            rows += [{**x, "kind": "model"} for x in m] + [{**x, "kind": "diff"} for x in d]
    met = pd.DataFrame(rows)
    met.round(6).to_csv(OUTPUTS / "e14_metrics.csv", index=False)
    c = met[(met.kind == "model") & (met.scope == "new combos together") & (met.model == "S transfer") &
            (met.metric == "auroc")].iloc[0]
    summ["criterion"] = {"auroc_S_transfer": float(c.value), "ci_lo": float(c.ci_lo), "ci_hi": float(c.ci_hi),
                         "met": bool(c.ci_lo > 0.6)}
    summ["test"] = {"runs": len(new), "tasks": int(new.task_id.nunique()), "combos": sorted(new.combo.unique()),
                    "unresolved_share": float(yn.mean()),
                    "within_task_pairs": int(e7.pair_counts(yn, rk["S transfer"], list(
                        pd.Series(np.arange(len(new))).groupby(new.task_id.to_numpy()).apply(np.array)))[1].sum())}
    # protocol 5: Spearman over the 16 corpus combos (E7 OOF, as E13) + the parsed new combos
    cb = pd.read_csv(OUTPUTS / "e13_combos.csv")[["combo", "generation", "runs", "resolved_share", "auroc_S"]]
    newc = []
    for comb, g in new.groupby("combo"):
        ix = (new.combo == comb).to_numpy()
        newc.append({"combo": comb, "generation": "new", "runs": int(ix.sum()), "resolved_share": float(g.resolved.mean()),
                     "auroc_S": e7.auroc(yn[ix], rk["S transfer"][ix]),
                     "auroc_S_upper": e7.auroc(yn[ix], rk["S upper"][ix])})
    cb = pd.concat([cb, pd.DataFrame(newc)], ignore_index=True)
    cb.round(6).to_csv(OUTPUTS / "e14_combos.csv", index=False)
    summ["spearman"] = {}
    for name, col in (("transfer", "auroc_S"), ("upper", "auroc_S_upper")):
        v = cb[col].where(cb.generation == "new", cb.auroc_S).to_numpy(float)
        rs = cb.resolved_share.to_numpy(float)
        rho = float(spearmanr(rs, v).statistic)
        rng = np.random.default_rng(SEED)
        boot = [spearmanr(rs[ix], v[ix]).statistic for ix in (rng.integers(0, len(cb), len(cb)) for _ in range(N_BOOT))]
        lo, hi = np.nanpercentile(boot, [2.5, 97.5])
        summ["spearman"][name] = {"rho": rho, "ci_lo": float(lo), "ci_hi": float(hi), "n_combos": len(cb)}
    (OUTPUTS / "e14_summary.json").write_text(json.dumps(summ, indent=1, ensure_ascii=False, default=float),
                                              encoding="utf-8")
    return summ


if __name__ == "__main__":
    print(json.dumps(run(), indent=1, ensure_ascii=False, default=float))
