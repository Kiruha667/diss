"""Reviewer's own recomputation of the kit's key numbers that appeared after the first review (read-only, own code).

  python review/scripts/k4_kit_recompute.py -> review/results/kit_recompute.json
 1. Leaderboard without the 132 empty patches (Table I of the kit): D, M, M+logN, M+LEN, S, M+S.
 2. KTH, two settings: S within unit, pooled training (T1) and within-setting (T2); all pairs and finished pairs;
    the 'attempt not finished' flag.
 3. E16: C3 on all finished pairs, C3 - (S+O), C3 - S (the kit's thesis says "no stronger than trajectory structure").
 4. G0: which population gives 36.9% / 35.6%; the Simpson numbers on the paper's population (no empty patches).
 5. DeepSWE-Preview: labelled attempts with an empty generated_diff, from the raw files.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


lb = _load("01_leaderboard")
k4 = _load("04_kth")
g5 = _load("05_e11_g0")
res: dict = {}
SEED = 42


def r4(x):
    return round(float(x), 4)


def part_leaderboard():
    p = lb.load()
    pf = pd.read_csv(ROOT / "outputs" / "e13_patch_features.csv.gz")
    pf = pf[pf.corpus == "leaderboard"].set_index("run_id")
    p["is_empty"] = pf.loc[p.run_id, "p_empty"].to_numpy().astype(bool)
    fold_all = lb.folds(p.task_id.to_numpy())
    ne = ~p.is_empty.to_numpy()
    q = p[ne].reset_index(drop=True)
    fold = fold_all[ne]  # the folds of the full population, as the author does
    y = q.y.to_numpy()
    g = q.task_id.to_numpy()
    q["base_model"] = q.model.str.replace("@high", "", regex=False)
    # D: share of unresolved among runs of the same task by other base models
    ft, nt = q.groupby("task_id").y.transform("sum"), q.groupby("task_id").y.transform("size")
    fo, no = q.groupby(["task_id", "base_model"]).y.transform("sum"), q.groupby(["task_id", "base_model"]).y.transform("size")
    d = np.where((nt - no) > 0, (ft - fo) / np.maximum(nt - no, 1), y.mean())
    ex = {"LOGN": np.log1p(q[["n_steps"]].to_numpy(float))}
    risk = {"D": np.asarray(d, float), "M": lb.oof_blocks(q, y, fold, ["M"]),
            "M+logN": lb.oof_blocks(q, y, fold, ["M", "LOGN"], extra=ex), "M+LEN": lb.oof_blocks(q, y, fold, ["M", "LEN"]),
            "S": lb.oof_blocks(q, y, fold, ["S"]), "M+S": lb.oof_blocks(q, y, fold, ["M", "S"])}
    out = {"runs": len(q), "unresolved": r4(y.mean())}
    _, da = lb.boot_ci(y, g, risk, roc_auc_score)
    for k, s in risk.items():
        out[k] = {"auroc": r4(roc_auc_score(y, s)), "ci": [r4(x) for x in np.percentile(da[k], [2.5, 97.5])],
                  "within": None if k == "D" else r4(lb.within_pooled(y, s, g)[0]), "p@10_index_order_ties": r4(lb.p_at(y, s, 0.10))}
    for a, b in (("M+S", "M+LEN"), ("M+S", "M+logN")):
        dd = da[a] - da[b]
        out[f"{a} - {b}"] = [r4(out[a]["auroc"] - out[b]["auroc"]), *[r4(x) for x in np.percentile(dd, [2.5, 97.5])]]
    res["1_leaderboard_no_empty"] = out
    print("1.", json.dumps(out))
    return p


def part_kth():
    k = pd.read_parquet(ROOT / "data" / "interim" / "agentic_evals" / "runs.parquet").sort_values("run_id").reset_index(drop=True)
    k = k[~k.setting.str.contains("DeepSWE")].reset_index(drop=True)
    k["unit"] = k.setting + "|" + k.task_id
    k["fin"] = k.ended_by_final_message.astype(int)
    t1, t2 = k4.oof(k, k4.S, False), k4.oof(k, k4.S, True)
    out = {"runs": len(k)}
    for name, m in (("devstral", k.setting.str.contains("devstral")), ("qwen", k.setting.str.contains("Qwen")), ("both", pd.Series(True, index=k.index))):
        d, mm = k[m], m.to_numpy()
        f = (d.fin == 1).to_numpy()
        out[name] = {"S-T1 all": k4.within(d, t1[mm]), "S-T1 finished": k4.within(d[f], t1[mm][f]),
                     "S-T2 all": k4.within(d, t2[mm]), "S-T2 finished": k4.within(d[f], t2[mm][f]),
                     "not finished flag": k4.within(d, 1 - d.fin.to_numpy())}
        print("2.", name, {a: (b["auroc"], b["ci"], b["pairs"]) for a, b in out[name].items()})
    res["2_kth_two_settings"] = out


def part_e16():
    a = pd.read_csv(ROOT / "outputs" / "e16_attempts.csv")
    print("3. e16_attempts columns:", list(a.columns))
    num = "C3_number" if "C3_number" in a.columns else [c for c in a.columns if "C3" in c][0]
    a["c3_risk"] = 100 - a[num]
    units = sorted(a.task_id.unique())
    per = {}
    for name, col in (("C3", "c3_risk"), ("S+O", "S+O"), ("S", "S")):
        c, n = np.zeros(len(units)), np.zeros(len(units))
        for i, t in enumerate(units):
            d = a[a.task_id == t]
            u, v = d[col][d.label == 1].to_numpy(float), d[col][d.label == 0].to_numpy(float)
            if len(u) and len(v):
                dd = u[:, None] - v[None, :]
                tie = np.isnan(dd)  # unparsed answer -> tie (the E11 rule)
                c[i] = np.nansum(dd > 0) + 0.5 * (np.nansum(dd == 0) + tie.sum())
                n[i] = dd.size
        per[name] = (c, n)
    rng = np.random.default_rng(SEED)
    draws = rng.integers(0, len(units), (2000, len(units)))
    b = {k: np.array([c[dr].sum() / n[dr].sum() for dr in draws]) for k, (c, n) in per.items()}
    out = {"units": len(units), "pairs": int(per["C3"][1].sum())}
    for k, (c, n) in per.items():
        out[k] = [r4(c.sum() / n.sum()), *[r4(x) for x in np.percentile(b[k], [2.5, 97.5])]]
    for x, y_ in (("C3", "S+O"), ("C3", "S")):
        out[f"{x} - {y_}"] = [r4(out[x][0] - out[y_][0]), *[r4(v) for v in np.percentile(b[x] - b[y_], [2.5, 97.5])]]
    res["3_e16"] = out
    print("3.", json.dumps(out))


def part_g0(p: pd.DataFrame):
    g = pd.read_csv(ROOT / "outputs" / "g0_final_messages.csv")
    out = {}
    ok = g[g.cls != "NO_FINAL_MESSAGE"]
    out["all 7975 with a final message"] = {"n": len(ok), "claim_failed": r4(ok.has_claim[ok.label == 1].mean()), "claim_succeeded": r4(ok.has_claim[ok.label == 0].mean()),
                                            "FALSE_SUCCESS_share_of_failed": r4((ok.cls[ok.label == 1] == "FALSE_SUCCESS").mean())}
    d = p[["run_id", "task_id", "combo", "resolved", "is_empty"]].merge(g[["run_id", "has_claim"]], on="run_id")
    d["has_claim"] = d.has_claim.astype(int)
    pooled = lambda x: x.has_claim[x.resolved == 1].mean() - x.has_claim[x.resolved == 0].mean()  # noqa: E731
    for name, dd in (("P_all 7864 submitted", d), ("P_ne 7732 without empty patches", d[~d.is_empty])):
        out[name] = {"n": len(dd), "claim_unresolved": r4(dd.has_claim[dd.resolved == 0].mean()), "claim_resolved": r4(dd.has_claim[dd.resolved == 1].mean()),
                     "diff_pooled": [r4(pooled(dd)), *g5.boot_tasks(dd, pooled)],
                     "diff_within_combo": [r4(g5.strat_diff(dd)), *g5.boot_tasks(dd, g5.strat_diff)]}
    out["claim share among the 132 empty patches"] = r4(d.has_claim[d.is_empty].mean())
    res["4_g0"] = out
    print("4.", json.dumps(out))


def part_deepswe():
    from src.adapters.agentic_evals import iter_raw, labels_of, run_dirs, _lit

    out = {}
    for setting in ("nano-agent-agentica-org_DeepSWE-Preview", "nano-agent-mistral_devstral-2512"):
        n_lab = n_empty = n_empty_res = 0
        names = []
        for rd in run_dirs(setting):
            lab = labels_of(rd)
            names += [x.name for x in rd.glob("*.json")]
            for raw in iter_raw(rd):
                y = lab.get(raw["instance_id"])
                if not isinstance(y, int):
                    continue
                n_lab += 1
                dp = raw["detailed_predictions"]
                dp = dp if isinstance(dp, dict) else (_lit(dp) or {})
                diff = dp.get("generated_diff")
                if not (isinstance(diff, str) and diff.strip() and diff != "None"):
                    n_empty += 1
                    n_empty_res += int(y == 0)
        out[setting] = {"labelled_attempts": n_lab, "with_empty_generated_diff": n_empty, "of_them_resolved": n_empty_res,
                        "result_file_names_example": names[:2]}
        print("5.", setting, out[setting])
    res["5_deepswe"] = out


if __name__ == "__main__":
    p = part_leaderboard()
    part_kth()
    part_e16()
    part_g0(p)
    part_deepswe()
    (ROOT / "review" / "results" / "kit_recompute.json").write_text(json.dumps(res, indent=1, ensure_ascii=False, default=float), encoding="utf-8")
