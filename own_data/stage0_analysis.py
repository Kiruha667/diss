"""Stage 0 of OWN_DATASET_PLAN.md: configs of the leaderboard runs, cost of a run, task pool and pilot tasks.
No API calls. Run from the repository root:  python own_data/stage0_analysis.py
Inputs (read only): data/raw/swebench_bashonly/<run>/{trajs/*.traj.json, metadata.yaml}, data/demo/runs.parquet.
Outputs (own_data/): stage0_results.json, tasks_pool.json, tasks_pilot.json, leaderboard_configs/<run>.json
(configs with secrets redacted and the per-task docker image replaced by a placeholder).
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "swebench_bashonly"
OUT = ROOT / "own_data"
SEED = 42
CANDIDATES = {"glm-4.6": "20251201_mini-v1.17.1_glm-4.6",
              "devstral-small-2512": "20251209_mini-v1.17.2_devstral-small-2512",
              "minimax-m2": "20251124_mini-v1.17.0_minimax-m2"}
POOL = (0.4, 0.6)
MAX_MEDIAN_STEPS = 60
N_PILOT = 5
_SECRET_RE = re.compile(r"api_key|token|secret|password", re.I)
_IMAGE_RE = re.compile(r"sweb\.eval\.x86_64\.[^:]+")


def redact(o):
    """Copy of a config with secret-looking values replaced and the task image made generic."""
    if isinstance(o, dict):
        return {k: ("<REDACTED>" if _SECRET_RE.search(k) and v not in (None, "") else redact(v)) for k, v in o.items()}
    if isinstance(o, list):
        return [redact(v) for v in o]
    if isinstance(o, str):
        return _IMAGE_RE.sub("sweb.eval.x86_64.<instance>", o)
    return o


def flat(o, pre=""):
    out = {}
    if isinstance(o, dict):
        for k, v in o.items():
            out.update(flat(v, f"{pre}{k}."))
    else:
        out[pre[:-1]] = json.dumps(o, sort_keys=True)
    return out


def get(d: dict, path: str):
    for k in path.split("."):
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return d


def pct(v, q):
    v = [x for x in v if x is not None]
    return float(np.percentile(v, q)) if v else None


def runs_summary() -> tuple[list[dict], dict]:
    rows, configs = [], {}
    (OUT / "leaderboard_configs").mkdir(parents=True, exist_ok=True)
    for run_dir in sorted(p for p in RAW.iterdir() if p.is_dir()):
        files = sorted((run_dir / "trajs").glob("*.traj.json"))
        meta = yaml.safe_load((run_dir / "metadata.yaml").read_text(encoding="utf-8")) if \
            (run_dir / "metadata.yaml").exists() else {}
        costs, calls, versions, exits, varying, cfg0 = [], [], Counter(), Counter(), defaultdict(set), None
        has_stats = 0
        for p in files:
            info = json.loads(p.read_text(encoding="utf-8")).get("info", {})
            ms = info.get("model_stats") or {}
            if "instance_cost" in ms or "api_calls" in ms:
                has_stats += 1
            costs.append(ms.get("instance_cost"))
            calls.append(ms.get("api_calls"))
            versions[str(info.get("mini_version"))] += 1
            exits[str(info.get("exit_status"))] += 1
            cfg = redact(info.get("config") or {})
            cfg0 = cfg0 or cfg
            for k, v in flat(cfg).items():
                varying[k].add(v)
        varies = sorted(k for k, v in varying.items() if len(v) > 1)
        cfg_hash = hashlib.sha256(json.dumps(cfg0, sort_keys=True).encode()).hexdigest() if cfg0 else None
        (OUT / "leaderboard_configs" / f"{run_dir.name}.json").write_text(
            json.dumps({"run": run_dir.name, "n_trajectories": len(files), "fields_varying_between_tasks": varies,
                        "config_sha256_redacted": cfg_hash, "config": cfg0}, indent=1, ensure_ascii=False),
            encoding="utf-8")
        mi = meta.get("info") or {}
        rows.append({
            "run": run_dir.name, "n_trajectories": len(files), "with_model_stats": has_stats,
            "mini_version_in_trajectories": dict(versions), "metadata_version": mi.get("mini-swe-agent_version"),
            "exit_status": dict(exits),
            "cost_median": pct(costs, 50), "cost_p90": pct(costs, 90),
            "api_calls_median": pct(calls, 50), "api_calls_p90": pct(calls, 90),
            "meta_resolved_pct": mi.get("resolved"), "meta_total_cost": mi.get("cost"),
            "meta_instance_cost_mean": mi.get("instance_cost"), "meta_instance_calls_mean": mi.get("instance_calls"),
            "model_name": get(cfg0, "model.model_name"), "model_type": get(cfg0, "model_type"),
            "model_kwargs": get(cfg0, "model.model_kwargs"), "temperature": get(cfg0, "model.model_kwargs.temperature"),
            "step_limit": get(cfg0, "agent.step_limit"), "cost_limit": get(cfg0, "agent.cost_limit"),
            "agent_type": get(cfg0, "agent_type"), "action_regex": get(cfg0, "agent.action_regex"),
            "env_timeout": get(cfg0, "environment.timeout"), "config_varies_between_tasks": varies,
            "config_sha256_redacted": cfg_hash})
        configs[run_dir.name] = cfg0
    return rows, configs


def template_groups(configs: dict) -> dict:
    """Which runs share identical prompt templates (system / instance)."""
    groups = defaultdict(list)
    for run, cfg in configs.items():
        key = hashlib.sha256(json.dumps([get(cfg, "agent.system_template"), get(cfg, "agent.instance_template")],
                                        sort_keys=True).encode()).hexdigest()[:12]
        groups[key].append(run)
    return dict(groups)


def task_pool() -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    runs = pd.read_parquet(ROOT / "data" / "demo" / "runs.parquet")
    sub = runs[runs.submitted == 1]
    t = sub.groupby("task_id").agg(repo=("repo", "first"), n_submitted=("run_id", "size"),
                                   n_resolved=("resolved", "sum"), median_steps=("n_steps", "median"))
    t["share_resolved"] = t.n_resolved / t.n_submitted
    # for reconciliation with the plan's counts: share over all 16 runs, not submitted = unresolved
    t_all = runs.groupby("task_id").agg(n_runs=("run_id", "size"), n_res=("resolved", "sum"))
    t["share_resolved_all_runs"] = (t_all.n_res / t_all.n_runs).reindex(t.index)
    pool = t[(t.share_resolved >= POOL[0]) & (t.share_resolved <= POOL[1])].copy()
    counts = {"pool_0.4_0.6_submitted": len(pool),
              "pool_0.25_0.75_submitted": int(((t.share_resolved >= 0.25) & (t.share_resolved <= 0.75)).sum()),
              "pool_0.4_0.6_all_runs": int(((t.share_resolved_all_runs >= 0.4) &
                                            (t.share_resolved_all_runs <= 0.6)).sum()),
              "pool_0.25_0.75_all_runs": int(((t.share_resolved_all_runs >= 0.25) &
                                              (t.share_resolved_all_runs <= 0.75)).sum()),
              "tasks_total": len(t), "tasks_with_16_submitted": int((t.n_submitted == 16).sum())}
    # candidate outcomes per task (from all runs, incl. not submitted)
    for name in CANDIDATES:
        r = runs[runs.model == name].set_index("task_id")
        pool[f"{name}"] = [("not_submitted:" + r.loc[x, "exit_status"]) if x in r.index and r.loc[x, "submitted"] == 0
                           else (int(r.loc[x, "resolved"]) if x in r.index else "no_run") for x in pool.index]
    return t, pool, counts


def pick_pilot(pool: pd.DataFrame) -> pd.DataFrame:
    ok = pool[pool.median_steps <= MAX_MEDIAN_STEPS]
    rng = np.random.default_rng(SEED)
    order = rng.permutation(np.sort(ok.index.to_numpy()))
    chosen, repos = [], set()
    for task in order:
        if ok.loc[task, "repo"] not in repos:
            chosen.append(task)
            repos.add(ok.loc[task, "repo"])
        if len(chosen) == N_PILOT:
            break
    return ok.loc[chosen], len(ok)


def candidate_reference(pilot: pd.DataFrame) -> list[dict]:
    runs = pd.read_parquet(ROOT / "data" / "demo" / "runs.parquet")
    out = []
    for name in CANDIDATES:
        r = runs[runs.model == name]
        rp = r[r.task_id.isin(pilot.index)]
        rs = r[r.submitted == 1]
        out.append({"model": name, "agent_version": r.agent_version.iloc[0] if len(r) else None, "runs": len(r),
                    "resolved_share_all": float(r.resolved.mean()), "submitted_share": float(r.submitted.mean()),
                    "median_steps_all": float(r.n_steps.median()),
                    "resolved_share_submitted": float(rs.resolved.mean()),
                    "median_steps_submitted": float(rs.n_steps.median()),
                    "pilot_resolved": int(rp.resolved.sum()), "pilot_runs": len(rp),
                    "pilot_resolved_share": float(rp.resolved.mean()) if len(rp) else None,
                    "pilot_median_steps": float(rp.n_steps.median()) if len(rp) else None})
    return out


MAIN_MODEL = "glm-4.6"            # PROTOCOL.md v1.0, TODO-2
FALLBACK_MODEL = "devstral-small-2512"  # PROTOCOL.md v1.0, fallback (needs protocol v1.1)


def protocol_reference(pool: pd.DataFrame) -> dict:
    """Reference values of the pilot reproducibility criteria (PROTOCOL.md v1.0, TODO-6): the model's corpus runs
    on the pool tasks with submitted == 1 -> share resolved +-0.2 and median length +-30%."""
    runs = pd.read_parquet(ROOT / "data" / "demo" / "runs.parquet")
    out = {}
    for name in (MAIN_MODEL, FALLBACK_MODEL):
        r = runs[(runs.model == name) & runs.task_id.isin(pool.index)]
        s = r[r.submitted == 1]
        share, med = float(s.resolved.mean()), float(s.n_steps.median())
        out[name] = {"pool_tasks": len(pool), "runs": len(r), "submitted": len(s), "resolved": int(s.resolved.sum()),
                     "not_submitted": r[r.submitted == 0][["task_id", "exit_status"]].to_dict("records"),
                     "resolved_share": share, "resolved_share_corridor": [max(0.0, share - 0.2), min(1.0, share + 0.2)],
                     "median_steps": med, "median_steps_corridor": [0.7 * med, 1.3 * med]}
    return out


def main() -> None:
    rows, configs = runs_summary()
    groups = template_groups(configs)
    t, pool, counts = task_pool()
    pilot, n_eligible = pick_pilot(pool)
    ref = candidate_reference(pilot)
    proto_ref = protocol_reference(pool)
    cols = ["repo", "n_submitted", "n_resolved", "share_resolved", "share_resolved_all_runs", "median_steps"] + \
        list(CANDIDATES)
    pool_records = pool[cols].reset_index().to_dict("records")
    pilot_records = pilot[cols].reset_index().to_dict("records")
    (OUT / "tasks_pool.json").write_text(json.dumps(
        {"rule": "share of resolved among submitted runs of the 16 leaderboard runs in [0.4, 0.6] "
                 "(data/demo/runs.parquet, submitted == 1)", "n_tasks": len(pool), "tasks": pool_records},
        indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    (OUT / "tasks_pilot.json").write_text(json.dumps(
        {"rule": f"{N_PILOT} tasks of tasks_pool.json with corpus median length <= {MAX_MEDIAN_STEPS} steps, "
                 f"different repositories; order = numpy default_rng({SEED}).permutation(sorted eligible task ids), "
                 "first task of each new repository", "n_eligible": n_eligible, "tasks": pilot_records},
        indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    (OUT / "stage0_results.json").write_text(json.dumps(
        {"runs": rows, "template_groups": groups, "pool_counts": counts, "pilot_eligible": n_eligible,
         "candidate_reference": ref, "protocol_reference": proto_ref}, indent=1, ensure_ascii=False, default=str),
        encoding="utf-8")
    print(json.dumps(proto_ref, indent=1))
    print(json.dumps({"pool_counts": counts, "pilot_eligible": n_eligible, "template_groups": groups}, indent=1))
    print(pd.DataFrame(rows)[["run", "n_trajectories", "with_model_stats", "mini_version_in_trajectories",
                              "metadata_version", "temperature", "step_limit", "cost_limit", "cost_median",
                              "cost_p90", "api_calls_median", "api_calls_p90", "meta_instance_cost_mean",
                              "meta_instance_calls_mean", "meta_resolved_pct"]].to_string())
    print(pilot[cols].to_string())
    print(pd.DataFrame(ref).to_string())


if __name__ == "__main__":
    main()
