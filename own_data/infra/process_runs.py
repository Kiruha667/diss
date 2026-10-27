"""Own runs -> own_data/runs_own.parquet, steps_own.parquet, scores_own.csv (OWN_DATASET_PLAN.md, sections 6 and 7).

Run from the repository root with the project venv:  python own_data/infra/process_runs.py
For every own_data/raw/<run_id>/<instance>/<instance>.traj.json with a report in own_data/eval/<run_id>/<instance>/:
  - parse with the existing adapter (src/adapters/swebench_bashonly.parse_trajectory), label from report.json
    (read_label);
  - phases, features, final message, claim — the demo build code (notebooks/build_demo_data.record_rows, i.e.
    src.e5_claims.process_record and the G0 functions) — same schema as data/demo/runs.parquet / steps.parquet,
    plus the own columns of the plan (source, repeat_idx, temperature, provider, cost_usd, tokens_in, tokens_out,
    wall_time_s, config_sha256, image_digest, protocol_version);
  - risk = the demo notebook's score_trajectory: LR (StandardScaler + L2, C=1) on the 21 M_struct+len features,
    fitted on the corpus population (data/demo/runs.parquet, submitted == 1) without the run's task, applied to
    the features recomputed by demo_lib from the steps.
Checks: the trajectory's info.config equals own_data/config/glm-4.6_v1.0.yaml (except image / selectors), no API
key field in it, demo_lib features equal the original code's.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from demo_lib import features as dfeat  # noqa: E402
from src.adapters.swebench_bashonly import load_cfg, parse_trajectory, read_label  # noqa: E402
from src.common import CONFIG, load_leak_markers  # noqa: E402
from src import g0_false_success as g0  # noqa: E402
from src.segment import compile_markers  # noqa: E402

OWN = ROOT / "own_data"
spec = importlib.util.spec_from_file_location("build_demo_data", ROOT / "notebooks" / "build_demo_data.py")
bdd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bdd)


def tokens(d: dict) -> tuple[int, int]:
    tin = tout = 0
    for m in d.get("messages") or []:
        u = (((m.get("extra") or {}).get("response") or {}).get("usage") or {}) if isinstance(m, dict) else {}
        tin += int(u.get("prompt_tokens") or 0)
        tout += int(u.get("completion_tokens") or 0)
    return tin, tout


def config_check(info_cfg: dict, cfg_yaml: dict) -> list[str]:
    """Differences between the trajectory's saved config and the run config (expected: none)."""
    diffs = []
    if "api_key" in (info_cfg.get("model", {}).get("model_kwargs") or {}):
        diffs.append("API KEY FIELD PRESENT")
    if info_cfg.get("agent") != cfg_yaml["agent"]:
        diffs.append("agent")
    if {k: v for k, v in info_cfg.get("environment", {}).items() if k != "image"} != \
            {k: v for k, v in cfg_yaml["environment"].items() if k != "environment_class"}:
        diffs.append("environment")
    if info_cfg.get("model") != {k: v for k, v in cfg_yaml["model"].items() if k != "model_class"}:
        diffs.append("model")
    return diffs


def demo_model_without(task: str, corpus: pd.DataFrame):
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    train = corpus[corpus.task_id != task]
    return make_pipeline(StandardScaler(), LogisticRegression(C=1.0, max_iter=5000)).fit(
        train[dfeat.FEATURES].to_numpy(float), 1 - train.resolved.to_numpy())


def main() -> None:
    cfg_src = load_cfg()["swebench_bashonly"] if "swebench_bashonly" in load_cfg() else load_cfg()
    infra = list(cfg_src.get("infra_exit_statuses") or [])
    mre = compile_markers(load_leak_markers())
    claim = g0.load_patterns(CONFIG / "claim_patterns.txt")
    cfgs = {p.stem.split("_v")[-1]: yaml.safe_load(p.read_text(encoding="utf-8"))
            for p in (OWN / "config").glob("glm-4.6_v*.yaml")}
    corpus = pd.read_parquet(ROOT / "data" / "demo" / "runs.parquet")
    corpus = corpus[corpus.submitted == 1]
    runs, steps, scores, problems = [], [], [], []
    for meta_path in sorted((OWN / "raw").glob("*/run_meta.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        run_id = meta["run_id"]
        version = str(meta.get("protocol_version"))
        cfg_yaml = cfgs[version]
        rep_m = re.search(r"_r(\d+)(?:_|$)", run_id)
        for traj in sorted(meta_path.parent.glob("*/*.traj.json")):
            iid = traj.parent.name
            report = OWN / "eval" / run_id / iid / "report.json"
            label = read_label(report, iid) if report.exists() else None
            d = json.loads(traj.read_text(encoding="utf-8"))
            info = d.get("info", {})
            diffs = config_check(info.get("config", {}), cfg_yaml)
            if diffs:
                problems.append(f"{run_id}/{iid}: config differs: {diffs}")
            rec, reason = parse_trajectory(d, run_id, iid, {"family": "zhipu"}, {"tags": {"model": ["glm-4.6"]}},
                                           infra)
            if reason or label is None:
                problems.append(f"{run_id}/{iid}: not processed ({reason or 'no evaluation report'})")
                continue
            rec["label"] = label
            reasoning = rec["meta"].get("reasoning_sources") or []
            if reasoning and version != "1.0":
                problems.append(f"{run_id}/{iid}: REASONING ENTERS TEXT FEATURES ({reasoning})")
            run, st, chk = bdd.record_rows(rec, mre, claim)
            run["claim_success"] = int(chk["claim_recomputed"])
            tin, tout = tokens(d)
            run.update({"source": "own", "repeat_idx": int(rep_m.group(1)) if rep_m else 0,
                        "temperature": info["config"]["model"]["model_kwargs"].get("temperature"),
                        "provider": meta.get("provider"), "cost_usd": info.get("model_stats", {}).get("instance_cost"),
                        "tokens_in": tin, "tokens_out": tout, "wall_time_s": meta.get("wall_time_s"),
                        "config_sha256": meta.get("config_sha256"),
                        "image_digest": meta.get("image_digests", {}).get(iid),
                        "protocol_version": version, "reasoning_sources": " + ".join(reasoning),
                        "in_analysis": int(version != "1.0" and "_smoke" not in run_id),
                        "note": ("smoke-тест протокола 1.0 (рассуждения glm-4.6 включены), не входит в анализ"
                                 if version == "1.0" else "")})
            # features recomputed by demo_lib from the steps (score_trajectory path) must equal the original ones
            st_df = pd.DataFrame(st)
            f = dfeat.trajectory_features(dfeat.steps_from_frame(st_df))
            worst = max(abs(float(f[c]) - float(run[c])) for c in dfeat.FEATURES)
            if worst > 1e-12:
                problems.append(f"{run_id}/{iid}: demo_lib features differ by {worst}")
            model = demo_model_without(iid, corpus)
            risk = float(model.predict_proba(np.array([[f[c] for c in dfeat.FEATURES]], dtype=float))[0, 1])
            scores.append({"run_id": run["run_id"], "task_id": iid, "risk": risk, "resolved": run["resolved"],
                           "submitted": run["submitted"], "n_steps": run["n_steps"], "cost_usd": run["cost_usd"]})
            runs.append(run)
            steps += st
    if runs:
        rdf = pd.DataFrame(runs)
        rdf.to_parquet(OWN / "runs_own.parquet", index=False)
        sdf = pd.DataFrame(steps)
        sdf["commands"] = sdf["commands"].apply(list)
        sdf.to_parquet(OWN / "steps_own.parquet", index=False)
        pd.DataFrame(scores).to_csv(OWN / "scores_own.csv", index=False)
    status = {"runs": len(runs), "problems": problems, "scores": scores}
    (OWN / "process_status.json").write_text(json.dumps(status, indent=1, ensure_ascii=False, default=str),
                                             encoding="utf-8")
    print(json.dumps(status, indent=1, default=str))


if __name__ == "__main__":
    main()
