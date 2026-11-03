"""E8 (reports/E8_PROTOCOL.md): run-level signal on repeated runs of one setting. No LLM calls.

  python -m src.e8_repeats check  -> reports/E8_adapter_check.md: step listing of 20 trajectories (seed 42)
  python -m src.e8_repeats build  -> data/interim/agentic_evals/runs.parquet, steps.parquet (features, phases)
  python -m src.e8_repeats run    -> outputs/e8_runs.csv.gz, outputs/e8_metrics.csv, outputs/e8_summary.json

Data: src.adapters.agentic_evals (three nano-agent settings). Unit = setting x task. Features: demo_lib (S = the 21
FEATURES, length = LEN), n_sent from src.segment as in notebooks/build_demo_data.py.
Predictors (StandardScaler + LogisticRegression(C=1, max_iter=5000)): P1 = S trained on the leaderboard corpus
(data/demo/runs.parquet, submitted == 1), no refit; P2 = S and P3 = LEN, out-of-fold over the three settings pooled,
GroupKFold(5, shuffle, seed 42) by task. Metrics per setting and overall: within-unit AUROC (pooled pairs) with a
cluster bootstrap over units (1000, default_rng(42)), within-unit permutation test (1000, default_rng(42),
one-sided), overall AUROC, share of within-unit pairs.
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import rankdata

from src.adapters.agentic_evals import (REPO, REVISION, SETTINGS, _lit, iter_raw, labels_of, run_dirs, to_record,
                                       tool_calls_of)
from src.common import INTERIM, OUTPUTS, REPORTS, ROOT, SEED, get_logger, load_leak_markers
from src.segment import compile_markers, segment

sys.path.insert(0, str(ROOT))
from demo_lib.features import FEATURES, LEN, step_phases, trajectory_features  # noqa: E402

log = get_logger("e8")
OUT_DIR = INTERIM / "agentic_evals"
N_BOOT = N_PERM = 1000
SHORT = {"nano-agent-Qwen_Qwen3-32B": "Qwen3-32B", "nano-agent-agentica-org_DeepSWE-Preview": "DeepSWE-Preview",
         "nano-agent-mistral_devstral-2512": "devstral-2512"}
PREDICTORS = {"P1": "S, обучена на лидерборде", "P2": "S, обучена на новых данных", "P3": "только длина (LEN)"}


# ------------------------------------------------------------------------------------------ records -> steps

def step_dicts(rec: dict, mre) -> list[dict]:
    """Assistant steps in the form of demo_lib (commands, n_outputs, n_nonzero_rc, n_sent)."""
    outs: dict[int, list[str]] = {}
    for s in rec["steps"]:
        if s["role"] == "tool":
            outs.setdefault(s["step_idx"], []).append(s["text"])
    n_sent = Counter(f.step_idx for f in segment(rec, "reason_sent", mre))
    return [{"step_idx": s["step_idx"], "commands": list(s["tool_calls"]),
             "n_outputs": len(outs.get(s["step_idx"], [])),
             "n_nonzero_rc": sum(1 for t in outs.get(s["step_idx"], []) if t.startswith("returncode: ")),
             "n_sent": n_sent.get(s["step_idx"], 0)}
            for s in rec["steps"] if s["role"] == "assistant"]


def labelled(setting: str) -> list[tuple[Path, str, int]]:
    """(run dir, task, label) of every run of the population of one setting."""
    out = []
    for rd in run_dirs(setting):
        out += [(rd, t, y) for t, y in sorted(labels_of(rd).items()) if isinstance(y, int)]
    return out


# ------------------------------------------------------------------------------------------ check

def _cell(s, n: int) -> str:
    s = " ".join(str(s).split())
    s = s if len(s) <= n else s[: n - 1] + "…"
    return s.replace("|", "\\|") or "—"


def check(n_per: tuple[int, ...] = (7, 7, 6)) -> Path:
    """Step listing of 20 trajectories (seed 42) for the manual check of commands and phases."""
    mre = compile_markers(load_leak_markers())
    rng = np.random.default_rng(SEED)
    L = ["# E8 — ручная проверка адаптера: 20 траекторий", "",
         "Листинг построен `python -m src.e8_repeats check` (выбор: `default_rng(42)`, 7 + 7 + 6 по настройкам "
         "из прогонов популяции). Для каждого шага: исходный вызов инструмента, команда, которую получает "
         "`demo_lib`, фаза (`demo_lib.step_phases`), число фрагментов `n_sent`, первая строка вывода и признак "
         "ненулевого кода возврата.", "", "<!-- VERDICT -->", ""]
    k = 0
    for setting, n in zip(SETTINGS, n_per):
        cand = labelled(setting)
        pick = sorted(rng.choice(len(cand), size=n, replace=False))
        by_run: dict[Path, dict[str, int]] = {}
        for i in pick:
            rd, t, y = cand[i]
            by_run.setdefault(rd, {})[t] = y
        for rd, tasks in by_run.items():
            for raw in iter_raw(rd):
                if raw["instance_id"] not in tasks:
                    continue
                k += 1
                rec = to_record(setting, rd.name, raw, tasks[raw["instance_id"]])
                st = step_dicts(rec, mre)
                ph = step_phases(st)
                asst = [s for s in rec["steps"] if s["role"] == "assistant"]
                tools = {s["step_idx"]: s for s in rec["steps"] if s["role"] == "tool"}
                calls = {}
                dp = _lit(raw["detailed_predictions"])
                a_msgs = [m for m in _lit(dp["completion"]) if m.get("role") == "assistant"]
                for i, m in enumerate(a_msgs):
                    calls[i] = tool_calls_of(m)
                L += [f"## {k}. {SHORT[setting]} / {rd.name} / {raw['instance_id']}", "",
                      f"Исход: **{'не решено' if rec['label'] else 'решено'}**; шагов {len(asst)}; закончилась "
                      f"финальным сообщением агента: {rec['meta']['ended_by_final_message']}.", "",
                      "| шаг | вызов (инструмент: аргументы) | команда для demo_lib | фаза | n_sent | вывод | rc≠0 |",
                      "|---|---|---|---|---|---|---|"]
                for s, d, p in zip(asst, st, ph):
                    cs = calls.get(s["step_idx"], [])
                    if cs:
                        _, name, args = cs[0]
                        if name == "apply_patch" and args:
                            call = (f"apply_patch: file={args.get('file')}; search={args.get('search', '')!r}; "
                                    f"replace={args.get('replace', '')!r}")
                        else:
                            call = f"{name}: {(args or {}).get('cmd', args)}"
                    else:
                        call = "(без вызова)"
                    out = tools.get(s["step_idx"], {}).get("text", "")
                    first = out.split("\n")[1 if out.startswith("returncode: ") else 0] if out else "—"
                    L.append(f"| {s['step_idx']} | {_cell(call, 110)} | {_cell(' ; '.join(d['commands']), 70)} | "
                             f"{p} | {d['n_sent']} | {_cell(first, 60)} | {d['n_nonzero_rc'] or ''} |")
                L.append("")
    path = REPORTS / "E8_adapter_check.md"
    path.write_text("\n".join(L) + "\n", encoding="utf-8")
    log.info("check listing: %d trajectories -> %s", k, path)
    return path


# ------------------------------------------------------------------------------------------ build

def build() -> dict:
    mre = compile_markers(load_leak_markers())
    rows, srows, excl = [], [], Counter()
    t0 = time.time()
    for setting in SETTINGS:
        for rd in run_dirs(setting):
            lab = labels_of(rd)
            seen = set()
            for raw in iter_raw(rd):
                t = raw["instance_id"]
                seen.add(t)
                y = lab.get(t, "not_submitted")
                if not isinstance(y, int):
                    excl[f"{setting}|{y}"] += 1
                    continue
                try:
                    rec = to_record(setting, rd.name, raw, y)
                except Exception as e:  # noqa: BLE001 - logged and counted by class
                    log.warning("parse error %s/%s/%s: %s", setting, rd.name, t, e)
                    excl[f"{setting}|parse_error|y={y}"] += 1
                    continue
                if rec["meta"]["n_assistant"] == 0:
                    excl[f"{setting}|no_agent_steps|y={y}"] += 1
                    continue
                st = step_dicts(rec, mre)
                ph = step_phases(st)
                f = trajectory_features(st)
                meta = rec["meta"]
                rows.append({"run_id": rec["run_id"], "setting": setting, "run": rd.name, "task_id": t,
                             "label": y, "ended_by_final_message": meta["ended_by_final_message"],
                             "n_unknown_tool_calls": meta["n_unknown_tool_calls"],
                             "n_unparsable_tool_calls": meta["n_unparsable_tool_calls"],
                             "n_apply_patch": sum(n == "apply_patch" for s in rec["steps"] if s["role"] == "assistant"
                                                  for n in s["tool_names"]),
                             "n_shell": sum(n == "shell" for s in rec["steps"] if s["role"] == "assistant"
                                            for n in s["tool_names"]),
                             "phase_seq": "".join({"edit": "E", "verify": "V", "other": "O"}[p] for p in ph), **f})
                srows += [{"run_id": rec["run_id"], "step_idx": d["step_idx"], "phase": p, "commands": d["commands"],
                           "n_outputs": d["n_outputs"], "n_nonzero_rc": d["n_nonzero_rc"], "n_sent": d["n_sent"]}
                          for d, p in zip(st, ph)]
            for t, y in lab.items():
                if isinstance(y, int) and t not in seen:
                    excl[f"{setting}|no_trajectory|y={y}"] += 1
            log.info("%s %s: %d runs so far, %.0f s", setting, rd.name, len(rows), time.time() - t0)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    runs = pd.DataFrame(rows).sort_values("run_id").reset_index(drop=True)
    runs.to_parquet(OUT_DIR / "runs.parquet", index=False)
    pd.DataFrame(srows).to_parquet(OUT_DIR / "steps.parquet", index=False)
    info = {"runs": len(runs), "exclusions": dict(sorted(excl.items())), "seconds": round(time.time() - t0, 1)}
    (OUT_DIR / "build_info.json").write_text(json.dumps(info, indent=1), encoding="utf-8")
    log.info("build done: %s", info)
    return info


# ------------------------------------------------------------------------------------------ predictors

def new_model():
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    return make_pipeline(StandardScaler(), LogisticRegression(C=1.0, max_iter=5000))


def predictors(pop: pd.DataFrame) -> tuple[dict[str, np.ndarray], np.ndarray, dict]:
    from sklearn.model_selection import GroupKFold

    y = pop.label.to_numpy()
    lb = pd.read_parquet(ROOT / "data" / "demo" / "runs.parquet")
    lb = lb[lb.submitted == 1].sort_values("run_id").reset_index(drop=True)
    p1 = new_model().fit(lb[FEATURES].to_numpy(float), (1 - lb.resolved).to_numpy())
    risk = {"P1": p1.predict_proba(pop[FEATURES].to_numpy(float))[:, 1]}
    groups = pop.task_id.to_numpy()
    fold = np.full(len(pop), -1)
    for k, (_, te) in enumerate(GroupKFold(n_splits=5, shuffle=True, random_state=SEED).split(pop, y, groups)):
        fold[te] = k
    for name, cols in (("P2", FEATURES), ("P3", LEN)):
        r = np.zeros(len(pop))
        for k in range(5):
            tr, te = fold != k, fold == k
            assert not set(groups[tr]) & set(groups[te])
            m = new_model().fit(pop.loc[tr, cols].to_numpy(float), y[tr])
            r[te] = m.predict_proba(pop.loc[te, cols].to_numpy(float))[:, 1]
        risk[name] = r
    return risk, fold, {"leaderboard_runs": len(lb)}


# ------------------------------------------------------------------------------------------ metrics

def auroc(yy: np.ndarray, ss: np.ndarray) -> float:
    r = rankdata(ss)
    n1 = yy.sum()
    n0 = len(yy) - n1
    return float((r[yy == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def unit_counts(y: np.ndarray, r: np.ndarray, unit_rows: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    """Concordant (unresolved, resolved) pairs of every unit (ties 1/2) and the number of pairs."""
    c, n = np.zeros(len(unit_rows)), np.zeros(len(unit_rows))
    for i, ix in enumerate(unit_rows):
        k = y[ix].sum()
        if 0 < k < len(ix):
            rk = rankdata(r[ix])  # midranks: concordant pairs = Mann-Whitney U of the unresolved runs
            c[i], n[i] = rk[y[ix] == 1].sum() - k * (k + 1) / 2, k * (len(ix) - k)
    return c, n


def perm_stats(y: np.ndarray, r: np.ndarray, unit_rows: list[np.ndarray], perms: list[np.ndarray]) -> np.ndarray:
    """Within-unit AUROC under N_PERM permutations of the labels inside every unit (perms: per unit (N_PERM, m))."""
    num, den = np.zeros(N_PERM), 0.0
    for ix, lab in zip(unit_rows, perms):
        k = y[ix].sum()
        if not 0 < k < len(ix):
            continue
        rk = rankdata(r[ix])
        num += lab @ rk - k * (k + 1) / 2
        den += k * (len(ix) - k)
    return num / den


def holm(p: dict[str, float]) -> dict[str, float]:
    keys = sorted(p, key=p.get)
    out, run = {}, 0.0
    for i, key in enumerate(keys):
        run = max(run, min(1.0, (len(keys) - i) * p[key]))
        out[key] = run
    return out


def scope_metrics(pop: pd.DataFrame, risk: dict[str, np.ndarray], scope: str) -> tuple[list[dict], dict]:
    sub = pop if scope == "all" else pop[pop.setting == scope]
    idx = sub.index.to_numpy()
    y = pop.label.to_numpy()
    unit_rows = [idx[g] for g in pd.Series(np.arange(len(sub))).groupby(sub.unit.to_numpy()).apply(np.array)]
    counts = {p: unit_counts(y, risk[p], unit_rows) for p in risk}
    rng = np.random.default_rng(SEED)
    draws = [rng.integers(0, len(unit_rows), len(unit_rows)) for _ in range(N_BOOT)]
    boot = {p: np.array([c[d].sum() / n[d].sum() for d in draws]) for p, (c, n) in counts.items()}
    rng_p = np.random.default_rng(SEED)
    perms = [rng_p.permuted(np.tile(y[ix], (N_PERM, 1)), axis=1) for ix in unit_rows]
    rows = []
    for p, (c, n) in counts.items():
        w = c.sum() / n.sum()
        null = perm_stats(y, risk[p], unit_rows, perms)
        pval = (1 + int((null >= w - 1e-12).sum())) / (N_PERM + 1)
        lo, hi = np.percentile(boot[p], [2.5, 97.5])
        rows.append({"scope": scope, "predictor": p, "metric": "within_unit_auroc", "value": w, "ci_lo": lo,
                     "ci_hi": hi, "perm_p": pval})
        rows.append({"scope": scope, "predictor": p, "metric": "overall_auroc", "value": auroc(y[idx], risk[p][idx]),
                     "ci_lo": np.nan, "ci_hi": np.nan, "perm_p": np.nan})
    d = boot["P2"] - boot["P3"]
    lo, hi = np.percentile(d, [2.5, 97.5])
    w2, w3 = (counts[p][0].sum() / counts[p][1].sum() for p in ("P2", "P3"))
    rows.append({"scope": scope, "predictor": "P2-P3", "metric": "within_unit_auroc_diff", "value": w2 - w3,
                 "ci_lo": lo, "ci_hi": hi, "perm_p": np.nan})
    yy = y[idx]
    n1, n0 = yy.sum(), len(yy) - yy.sum()
    n_pairs = counts["P2"][1]
    desc = {"runs": len(sub), "tasks": int(sub.task_id.nunique()), "units": len(unit_rows),
            "units_with_both": int((n_pairs > 0).sum()), "within_pairs": int(n_pairs.sum()),
            "all_pairs": int(n1 * n0), "same_unit_share": float(n_pairs.sum() / (n1 * n0)),
            "unresolved_share": float(yy.mean()),
            "ended_by_final_message": float(sub.ended_by_final_message.mean()),
            "median_n_steps": float(sub.n_steps.median()),
            "has_any_verification": float(sub.has_any_verification.mean()),
            "share_edit_mean": float(sub.share_edit.mean()), "share_verify_mean": float(sub.share_verify.mean()),
            "nonzero_rc_share_mean": float(sub.nonzero_rc_share.mean()),
            "runs_per_unit_median": float(np.median([len(u) for u in unit_rows]))}
    return rows, desc


def run() -> dict:
    t0 = time.time()
    pop = pd.read_parquet(OUT_DIR / "runs.parquet").sort_values("run_id").reset_index(drop=True)
    pop["unit"] = pop.setting + "|" + pop.task_id
    risk, fold, info = predictors(pop)
    rows, desc = [], {}
    for scope in list(SETTINGS) + ["all"]:
        r, d = scope_metrics(pop, risk, scope)
        rows += r
        desc[scope] = d
        log.info("%s: %s", scope, d)
    met = pd.DataFrame(rows)
    # Holm over the three settings, for every predictor
    met["perm_p_holm"] = np.nan
    for p in risk:
        sel = (met.metric == "within_unit_auroc") & (met.predictor == p) & (met.scope != "all")
        adj = holm(dict(zip(met.loc[sel, "scope"], met.loc[sel, "perm_p"])))
        met.loc[sel, "perm_p_holm"] = met.loc[sel, "scope"].map(adj)
    # criteria (protocol, section 6), on P2
    crit = {}
    for scope in list(SETTINGS) + ["all"]:
        w = met[(met.scope == scope) & (met.predictor == "P2") & (met.metric == "within_unit_auroc")].iloc[0]
        dd = met[(met.scope == scope) & (met.metric == "within_unit_auroc_diff")].iloc[0]
        p_used = w.perm_p if scope == "all" else w.perm_p_holm
        crit[scope] = {"signal": bool(w.ci_lo > 0.5 and p_used < 0.05), "practical": bool(w.value >= 0.60),
                       "struct_over_length": bool(dd.ci_lo > 0)}
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    met.round(6).to_csv(OUTPUTS / "e8_metrics.csv", index=False)
    keep = ["run_id", "setting", "run", "task_id", "label", "ended_by_final_message"] + FEATURES
    out = pop[keep].copy()
    out["fold"] = fold
    for p, r in risk.items():
        out[f"risk_{p}"] = r
    out.round(8).to_csv(OUTPUTS / "e8_runs.csv.gz", index=False)
    build_info = json.loads((OUT_DIR / "build_info.json").read_text(encoding="utf-8"))
    summary = {"dataset": {"repo": REPO, "revision": REVISION, "settings": list(SETTINGS)}, "build": build_info,
               "descriptives": desc, "criteria": crit, **info, "seconds": round(time.time() - t0, 1)}
    (OUTPUTS / "e8_summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False, default=float),
                                             encoding="utf-8")
    log.info("E8 done in %.0f s; criteria %s", time.time() - t0, crit)
    return summary


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    {"check": check, "build": build, "run": run}[cmd]()
