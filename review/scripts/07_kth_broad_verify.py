"""KTH repeats under a BROAD 'verify' (any execution of python code): do Qwen3 / DeepSWE really not check their work?

  python review/scripts/07_kth_broad_verify.py -> review/results/07_kth_broad_verify.json
Input: data/interim/agentic_evals/{runs,steps}.parquet. Read-only; the author's code is not changed.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


p6 = _load("06_phases")
k4 = _load("04_kth")
FEATURES = p6.FEATURES
SHORT = k4.SHORT


def main():
    res = {}
    k = pd.read_parquet(ROOT / "data" / "interim" / "agentic_evals" / "runs.parquet").sort_values("run_id").reset_index(drop=True)
    k["unit"] = k.setting + "|" + k.task_id
    st = pd.read_parquet(ROOT / "data" / "interim" / "agentic_evals" / "steps.parquet")
    st = st[st.run_id.isin(set(k.run_id))].sort_values(["run_id", "step_idx"])
    st["cmd"] = st.commands.apply(lambda c: "\n".join(str(x) for x in c))
    st["py"] = st.cmd.apply(p6.runs_python)
    r = st.groupby("run_id").agg(strict=("phase", lambda s: (s == "verify").any()), py=("py", "any"),
                                 n_py=("py", "sum"), n_steps=("py", "size"))
    r["broad"] = r.strict | r.py
    r = r.join(k.set_index("run_id")[["setting", "label", "ended_by_final_message"]])
    per = r.groupby("setting").agg(runs=("strict", "size"), verif_strict=("strict", "mean"), verif_broad=("broad", "mean"),
                                   python_steps_per_run=("n_py", "mean"), steps_per_run=("n_steps", "mean")).round(3)
    per["unresolved_if_broad_verified"] = r[r.broad].groupby("setting").label.mean().round(3)
    per["unresolved_if_not_broad_verified"] = r[~r.broad].groupby("setting").label.mean().round(3)
    print(per.to_string())
    res["verification_share"] = per.reset_index().to_dict("records")

    # within-unit AUROC of the single flag 'no (broad) verification' and of S recomputed with the broad rule
    by_run = {}
    for rid, d in st.groupby("run_id", sort=False):
        by_run[rid] = [{"step_idx": int(a), "commands": [str(c) for c in b], "n_outputs": int(o), "n_nonzero_rc": int(z),
                        "n_sent": int(s)} for a, b, o, z, s in zip(d.step_idx, d.commands, d.n_outputs, d.n_nonzero_rc, d.n_sent)]
    f_strict = p6.features_of(by_run, False)
    res["strict_features_reproduce_runs_parquet_max_abs_diff"] = float(
        np.abs(f_strict.loc[k.run_id, FEATURES].to_numpy(float) - k[FEATURES].to_numpy(float)).max())
    f_broad = p6.features_of(by_run, True)
    kb = k.drop(columns=FEATURES).join(f_broad[FEATURES], on="run_id")
    out = {}
    for name, frame in (("S strict, within-setting", k), ("S broad verify, within-setting", kb)):
        risk = k4.oof(frame, FEATURES, True)
        out[name] = {SHORT[s]: k4.within(frame[frame.setting == s], risk[(frame.setting == s).to_numpy()]) for s in SHORT}
        out[name]["all"] = k4.within(frame, risk)
        print(name, {a: (b["auroc"], b["ci"]) for a, b in out[name].items()})
    flag = (~r.broad).astype(float).reindex(k.run_id).to_numpy()
    out["flag 'no broad verification' alone"] = {SHORT[s]: k4.within(k[k.setting == s], flag[(k.setting == s).to_numpy()]) for s in SHORT}
    print("flag alone", {a: (b["auroc"], b["ci"]) for a, b in out["flag 'no broad verification' alone"].items()})
    res["within_unit"] = out
    print("strict features vs runs.parquet:", res["strict_features_reproduce_runs_parquet_max_abs_diff"])
    (ROOT / "review" / "results" / "07_kth_broad_verify.json").write_text(json.dumps(res, indent=1, default=float), encoding="utf-8")


if __name__ == "__main__":
    main()
