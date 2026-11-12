"""Reviewer's check of the phase rules (demo_lib/phases.py): how narrow is 'verify', and does it matter?

  python review/scripts/06_phases.py -> review/results/06_phases.json, review/results/06_phase_samples.txt

Input: data/demo/steps.parquet (commands of every step), data/demo/runs.parquet. Read-only. The author's code is not
changed: a wrapper adds a BROAD 'verify' (any execution of python code that the author's rules leave unlabelled:
`python file.py`, `python -c`, python heredoc, `python -m <module>`), and the 21 features are recomputed with it.
"""
from __future__ import annotations

import json
import re
import sys
import warnings
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import demo_lib.features as dfeat  # noqa: E402
from demo_lib import phases as ph  # noqa: E402

SEED = 42
FEATURES = dfeat.FEATURES
_orig_events = ph.command_events
res: dict = {}


def segments(cmd: str) -> list[str]:
    flat, _ = ph._cut_heredocs(cmd)
    quotes: list[str] = []

    def protect(m):
        quotes.append(m.group(0))
        return f"__QUOTE{len(quotes) - 1}__"

    out = []
    for seg in ph._SPLIT_RE.split(ph._QUOTED_RE.sub(protect, flat)):
        seg = re.sub(r"__QUOTE(\d+)__", lambda m: quotes[int(m.group(1))], seg)
        if seg.strip():
            out.append(seg)
    return out


def runs_python(cmd: str) -> bool:
    """True if some segment of the command executes python code (python file.py / -c / heredoc / -m module)."""
    for seg in segments(cmd):
        w = ph._words(ph._PLACEHOLDER_RE.sub(" ", ph._QUOTED_RE.sub("''", seg)))
        if w and ph._is_python(w[0]):
            rest = w[1:]
            if any(a in ("--version", "-V") for a in rest):
                continue
            return True
    return False


def first_word(cmd: str) -> str:
    for seg in segments(cmd):
        w = ph._words(ph._PLACEHOLDER_RE.sub(" ", ph._QUOTED_RE.sub("''", seg)))
        if w and w[0] not in ("cd", "export", "source", "set"):
            return w[0].rsplit("/", 1)[-1][:20]
    return "?"


def broad_events(cmd: str, files: dict) -> list[tuple[str, str]]:
    ev = _orig_events(cmd, files)
    if not any(k == "verify" for k, _ in ev) and runs_python(cmd) and not any(k == "edit" for k, _ in ev):
        ev = ev + [("verify", "")]
    return ev


def features_of(steps_by_run: dict, broad: bool) -> pd.DataFrame:
    dfeat.command_events = broad_events if broad else _orig_events
    rows = []
    for rid, st in steps_by_run.items():
        f = dfeat.trajectory_features(st)
        f["run_id"] = rid
        rows.append(f)
    dfeat.command_events = _orig_events
    return pd.DataFrame(rows).set_index("run_id")


def oof_auc(pop: pd.DataFrame, X: pd.DataFrame, with_m: bool) -> tuple[float, float]:
    y = (1 - pop.resolved).to_numpy()
    g = pop.task_id.to_numpy()
    r = np.zeros(len(pop))
    for tr, te in GroupKFold(n_splits=5, shuffle=True, random_state=SEED).split(pop, y, g):
        xs = [X.loc[pop.run_id, FEATURES].to_numpy(float)]
        if with_m:
            enc = OneHotEncoder(handle_unknown="ignore", sparse_output=False).fit(pop.iloc[tr][["combo"]])
            xs.append(enc.transform(pop[["combo"]]))
        x = np.hstack(xs)
        m = make_pipeline(StandardScaler(), LogisticRegression(C=1.0, max_iter=5000)).fit(x[tr], y[tr])
        r[te] = m.predict_proba(x[te])[:, 1]
    c = n = 0.0
    for _, d in pd.DataFrame({"y": y, "s": r, "g": g}).groupby("g"):
        u, v = d.s[d.y == 1].to_numpy(), d.s[d.y == 0].to_numpy()
        if len(u) and len(v):
            dd = u[:, None] - v[None, :]
            c += (dd > 0).sum() + 0.5 * (dd == 0).sum()
            n += dd.size
    return round(roc_auc_score(y, r), 4), round(c / n, 4)


def main():
    runs = pd.read_parquet(ROOT / "data" / "demo" / "runs.parquet")
    pop = runs[runs.submitted == 1].sort_values("run_id").reset_index(drop=True)
    pop["combo"] = pop.model + " | " + pop.agent_version
    steps = pd.read_parquet(ROOT / "data" / "demo" / "steps.parquet",
                            columns=["run_id", "step_idx", "phase", "commands", "n_outputs", "n_nonzero_rc", "n_sent"])
    steps = steps[steps.run_id.isin(set(pop.run_id))].sort_values(["run_id", "step_idx"])
    steps["cmd"] = steps.commands.apply(lambda c: "\n".join(str(x) for x in c))
    steps["py"] = steps.cmd.apply(runs_python)
    print("steps", len(steps), "runs", steps.run_id.nunique())

    # ---------------------------------------------------------------- descriptive: what is inside 'other'
    last_edit = steps[steps.phase == "edit"].groupby("run_id").step_idx.max().rename("last_edit")
    first_edit = steps[steps.phase == "edit"].groupby("run_id").step_idx.min().rename("first_edit")
    steps = steps.join(last_edit, on="run_id").join(first_edit, on="run_id")
    after = steps[(steps.step_idx > steps.last_edit.fillna(-1))]
    oth_after = after[after.phase == "other"]
    oth = steps[steps.phase == "other"]
    res["phase_shares_of_steps"] = steps.phase.value_counts(normalize=True).round(4).to_dict()
    res["other_steps_that_execute_python"] = {
        "all_other_steps": round(float(oth.py.mean()), 4),
        "other_steps_after_last_edit": round(float(oth_after.py.mean()), 4),
        "n_other_after_last_edit": int(len(oth_after)),
        "other_steps_after_first_edit": round(float(steps[(steps.phase == "other") & (steps.step_idx > steps.first_edit.fillna(1e9))].py.mean()), 4)}
    res["first_word_of_other_after_last_edit_top15"] = dict(Counter(oth_after.cmd.apply(first_word)).most_common(15))
    # runs: verification under the author's rule vs the broad rule
    r = steps.groupby("run_id").agg(strict=("phase", lambda s: (s == "verify").any()),
                                    broad=("py", "any"))
    r["broad"] = r.strict | r.broad
    r = r.join(pop.set_index("run_id")[["combo", "resolved"]])
    per = r.groupby("combo").agg(runs=("strict", "size"), verif_strict=("strict", "mean"), verif_broad=("broad", "mean"),
                                 resolved=("resolved", "mean")).round(3).sort_values("verif_strict")
    print(per.to_string())
    res["verification_share_by_combo"] = per.reset_index().to_dict("records")
    res["verification_share_all"] = {"strict": round(float(r.strict.mean()), 4), "broad": round(float(r.broad.mean()), 4)}
    for name, col in (("strict", "strict"), ("broad", "broad")):
        res[f"resolved_if_verified_{name}"] = round(float(r.resolved[r[col]].mean()), 4)
        res[f"resolved_if_not_verified_{name}"] = round(float(r.resolved[~r[col]].mean()), 4) if (~r[col]).any() else None
        res[f"n_not_verified_{name}"] = int((~r[col]).sum())
    print({k: v for k, v in res.items() if k.startswith(("resolved_if", "n_not", "other_steps", "phase_shares", "verification_share_all"))})
    print(res["first_word_of_other_after_last_edit_top15"])

    # ---------------------------------------------------------------- features with the broad 'verify'
    by_run = {}
    for rid, d in steps.groupby("run_id", sort=False):
        by_run[rid] = [{"step_idx": int(a), "commands": [str(c) for c in b], "n_outputs": int(o), "n_nonzero_rc": int(z),
                        "n_sent": int(s)} for a, b, o, z, s in zip(d.step_idx, d.commands, d.n_outputs, d.n_nonzero_rc, d.n_sent)]
    f_strict = features_of(by_run, False)
    ref = pop.set_index("run_id")[FEATURES]
    res["strict_features_reproduce_runs_parquet_max_abs_diff"] = float(
        np.abs(f_strict.loc[ref.index, FEATURES].to_numpy(float) - ref.to_numpy(float)).max())
    f_broad = features_of(by_run, True)
    res["leaderboard_auroc"] = {"S strict (author)": oof_auc(pop, f_strict, False), "S broad verify": oof_auc(pop, f_broad, False),
                                "M+S strict (author)": oof_auc(pop, f_strict, True), "M+S broad verify": oof_auc(pop, f_broad, True)}
    print("strict features vs runs.parquet max abs diff:", res["strict_features_reproduce_runs_parquet_max_abs_diff"])
    print("leaderboard (overall AUROC, within-task):", res["leaderboard_auroc"])
    res["share_verify_mean"] = {"strict": round(float(f_strict.share_verify.mean()), 4), "broad": round(float(f_broad.share_verify.mean()), 4)}

    # ---------------------------------------------------------------- samples for reading
    rng = np.random.default_rng(SEED)
    L = []
    for title, d, n in (("OTHER after the last edit", oth_after, 40), ("OTHER that executes python (after the last edit)", oth_after[oth_after.py], 20),
                        ("VERIFY", steps[steps.phase == "verify"], 20), ("EDIT", steps[steps.phase == "edit"], 20)):
        L.append(f"===== {title}: {len(d)} steps, random {n}")
        for i in rng.choice(len(d), n, replace=False):
            row = d.iloc[int(i)]
            L.append(f"[{row.run_id.split('/')[0][9:40]} step {row.step_idx}] " + " ⏎ ".join(row.cmd.split("\n"))[:230])
        L.append("")
    (ROOT / "review" / "results" / "06_phase_samples.txt").write_text("\n".join(L), encoding="utf-8")
    (ROOT / "review" / "results" / "06_phases.json").write_text(json.dumps(res, indent=1, ensure_ascii=False, default=float), encoding="utf-8")


if __name__ == "__main__":
    main()
