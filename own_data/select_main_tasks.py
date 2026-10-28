"""Protocol 1.2 (own_data/PROTOCOL.md, section 11): task pool and selection for the main collection, and the forecast
of its time and cost from the pilot. No LLM calls. Run from the repository root:  python own_data/select_main_tasks.py
Inputs (read only): data/demo/runs.parquet, own_data/tasks_pilot.json, own_data/runs_own.parquet,
own_data/audit/pilot_timing.tsv (own_data/audit/pilot_timing.sh: agent wall time and evaluation time per pilot run).
Outputs: own_data/tasks_main.json, own_data/forecast_main.json.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "own_data"
SEED = 42
N_MAIN = 70
POOL = (0.2, 0.6)
POOL_EXTENDED_LOW = 0.15
MIN_POOL = 60
EXCLUDED_REPOS = ("psf/requests",)
MODEL = "glm-4.6"
REPEATS, EXTRA_REPEATS = 3, 2


def pool_table() -> pd.DataFrame:
    runs = pd.read_parquet(ROOT / "data" / "demo" / "runs.parquet")
    sub = runs[runs.submitted == 1]
    t = sub.groupby("task_id").agg(repo=("repo", "first"), n_submitted=("run_id", "size"),
                                   n_resolved=("resolved", "sum"), median_steps=("n_steps", "median"))
    t["share_resolved"] = t.n_resolved / t.n_submitted
    g = runs[runs.model == MODEL].set_index("task_id")
    t[f"{MODEL}_corpus"] = ["no_run" if x not in g.index else
                            ("not_submitted:" + g.loc[x, "exit_status"]) if g.loc[x, "submitted"] == 0
                            else int(g.loc[x, "resolved"]) for x in t.index]
    t[f"{MODEL}_corpus_steps"] = g.n_steps.reindex(t.index)
    return t


def main() -> None:
    t = pool_table()
    pilot = [x["task_id"] for x in json.loads((OUT / "tasks_pilot.json").read_text(encoding="utf-8"))["tasks"]]
    eligible = t[~t.repo.isin(EXCLUDED_REPOS)]
    low = POOL[0]
    pool = eligible[(eligible.share_resolved >= low) & (eligible.share_resolved <= POOL[1])]
    if len(pool) < MIN_POOL:
        low = POOL_EXTENDED_LOW
        pool = eligible[(eligible.share_resolved >= low) & (eligible.share_resolved <= POOL[1])]
    mandatory = [x for x in pilot if t.loc[x, "repo"] not in EXCLUDED_REPOS]
    assert all(x in pool.index for x in mandatory), "a pilot task is outside the pool"
    rest = np.sort(pool.index.difference(mandatory).to_numpy())
    order = np.random.default_rng(SEED).permutation(rest)
    # the rule asks for 70 tasks, the pool may be smaller (without extension when it has >= 60): then all are taken
    n_select = min(N_MAIN, len(pool))
    chosen = mandatory + list(order[:n_select - len(mandatory)])
    sel = pool.loc[chosen].copy()
    sel["source"] = ["pilot (mandatory)" if x in mandatory else "sampled" for x in chosen]
    cols = ["repo", "n_submitted", "n_resolved", "share_resolved", "median_steps", f"{MODEL}_corpus",
            f"{MODEL}_corpus_steps", "source"]
    (OUT / "tasks_main.json").write_text(json.dumps({
        "protocol_version": "1.2",
        "rule": (f"share of resolved among submitted runs (submitted == 1) of the 16 leaderboard runs in "
                 f"[{POOL[0]}, {POOL[1]}] (data/demo/runs.parquet), repositories {list(EXCLUDED_REPOS)} excluded; "
                 f"if fewer than {MIN_POOL} tasks, lower bound {POOL_EXTENDED_LOW}. The {len(mandatory)} pilot tasks "
                 f"outside psf/requests are included as mandatory, their 12 pilot runs are part of the data; the other "
                 f"tasks = first {N_MAIN} - {len(mandatory)} of numpy default_rng({SEED}).permutation(sorted pool task "
                 f"ids without the mandatory ones), or all of them if the pool has fewer than {N_MAIN} tasks"),
        "pool_lower_bound_used": low,
        "pool_size": len(pool),
        "requested_n": N_MAIN,
        "selection_note": (None if len(pool) >= N_MAIN else
                           f"pool has {len(pool)} < {N_MAIN} tasks and >= {MIN_POOL}, so by the rule the lower bound is "
                           f"not extended and all {len(pool)} tasks are taken; the requested {N_MAIN} cannot be reached "
                           f"(with the lower bound {POOL_EXTENDED_LOW} the pool would have "
                           f"{int(((eligible.share_resolved >= POOL_EXTENDED_LOW) & (eligible.share_resolved <= POOL[1])).sum())}"
                           f" tasks) - decision of the author, see PROTOCOL.md 1.2"),
        "pool_size_by_bound": {f"[{lo}, {POOL[1]}]": int(((eligible.share_resolved >= lo) &
                                                          (eligible.share_resolved <= POOL[1])).sum())
                               for lo in (POOL[0], POOL_EXTENDED_LOW, 0.1)},
        "pool_tasks_with_16_submitted": int((pool.n_submitted == 16).sum()),
        "n_tasks": len(sel),
        "selected_summary": {
            "repos": sel.repo.value_counts().to_dict(),
            "share_resolved_median": float(sel.share_resolved.median()),
            f"{MODEL}_corpus_resolved": int((sel[f"{MODEL}_corpus"] == 1).sum()),
            f"{MODEL}_corpus_not_resolved": int((sel[f"{MODEL}_corpus"] == 0).sum()),
            f"{MODEL}_corpus_not_submitted": int(sel[f"{MODEL}_corpus"].astype(str).str.startswith("not_").sum()),
            f"{MODEL}_corpus_runs_of_at_most_4_steps": int((sel[f"{MODEL}_corpus_steps"] <= 4).sum()),
        },
        "tasks": sel[cols].reset_index().to_dict("records"),
    }, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"pool [{low}, {POOL[1]}] without {EXCLUDED_REPOS}: {len(pool)} tasks; selected {len(sel)}")
    print(sel[cols].to_string())

    # forecast from the pilot
    own = pd.read_parquet(OUT / "runs_own.parquet")
    own["run"] = own.run_id.str.split("/").str[0]
    tim = pd.read_csv(OUT / "audit" / "pilot_timing.tsv", sep="\t").rename(columns={"run_id": "run"})
    p_all = own[own.run.str.contains("_pilot_")].merge(tim, on="run")
    p_all["total_s"] = p_all.agent_wall_s + p_all.eval_s
    # the forecast uses the 12 pilot runs outside psf/requests (requests is excluded from the main pool)
    p = p_all[~p_all.repo.isin(EXCLUDED_REPOS)]
    # the pilot tasks were chosen with corpus median length <= 60 steps; the main tasks are longer -> two scalings of
    # the agent time and the cost by the ratio of corpus lengths (main tasks / 4 pilot tasks): median over the 16
    # runs and the glm-4.6 corpus run; the evaluation time is not scaled; cost is scaled linearly (an underestimate:
    # the context, and the cost of a step, grow with the step number)
    ratios = {"median_steps_16_runs": float(sel.median_steps.median() / t.loc[mandatory, "median_steps"].median()),
              f"{MODEL}_corpus_steps": float(sel[f"{MODEL}_corpus_steps"].median() /
                                             t.loc[mandatory, f"{MODEL}_corpus_steps"].median())}
    n_tasks = len(sel)
    n_new = n_tasks * REPEATS - len(mandatory) * REPEATS
    fc = {"pilot_runs_used": len(p), "all_15_pilot_runs": {
              "run_total_s_median": float(p_all.total_s.median()), "run_total_s_p90": float(p_all.total_s.quantile(0.9)),
              "cost_usd_median": float(p_all.cost_usd.median())},
          "agent_wall_s": {"median": float(p.agent_wall_s.median()), "p90": float(p.agent_wall_s.quantile(0.9)),
                           "mean": float(p.agent_wall_s.mean())},
          "eval_s": {"median": float(p.eval_s.median()), "p90": float(p.eval_s.quantile(0.9)),
                     "mean": float(p.eval_s.mean())},
          "run_total_s": {"median": float(p.total_s.median()), "p90": float(p.total_s.quantile(0.9)),
                          "mean": float(p.total_s.mean())},
          "cost_usd": {"median": float(p.cost_usd.median()), "p90": float(p.cost_usd.quantile(0.9)),
                       "mean": float(p.cost_usd.mean())},
          "length_ratio_main_to_pilot": ratios,
          "n_tasks": n_tasks, "new_runs_base": n_new, "scenarios": {}}
    for name, share in (("mixed_share_0.4_pilot", 0.4), ("mixed_share_0.63_plan_p0.3", 0.63),
                        ("mixed_share_0.75_plan_p0.5", 0.75)):
        extra = int(round(share * n_tasks)) * EXTRA_REPEATS
        n = n_new + extra
        sc = {"runs": n, "extra_runs": extra}
        for adj_name, k in (("as_pilot", 1.0), *((f"scaled_{a}", r) for a, r in ratios.items())):
            sc[adj_name] = {"hours": round(n * (fc["agent_wall_s"]["mean"] * k + fc["eval_s"]["mean"]) / 3600, 1),
                            "cost_usd": round(n * fc["cost_usd"]["mean"] * k, 2)}
        fc["scenarios"][name] = sc
    (OUT / "forecast_main.json").write_text(json.dumps(fc, indent=1), encoding="utf-8")
    print(json.dumps(fc, indent=1))


if __name__ == "__main__":
    main()
