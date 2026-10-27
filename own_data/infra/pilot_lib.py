"""Helpers of the pilot orchestrator (own_data/infra/pilot.ps1), PROTOCOL.md 1.1. Run from the repository root.
  python own_data/infra/pilot_lib.py spent            -> cumulative pilot spend, USD (all pilot trajectories)
  python own_data/infra/pilot_lib.py header           -> append the pilot section header to LOG.md
  python own_data/infra/pilot_lib.py fail <run_id> <exit_code>   -> record a run that produced no output
  python own_data/infra/pilot_lib.py log <run_id>     -> append the run's LOG.md line; prints OK or STOP: <reason>
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OWN = ROOT / "own_data"
LOG = OWN / "LOG.md"
BUDGET = 5.0


def spent() -> float:
    total = 0.0
    for p in (OWN / "raw").glob("*_pilot_*/*/*.traj.json"):
        total += float((json.loads(p.read_text(encoding="utf-8")).get("info", {}).get("model_stats") or {})
                       .get("instance_cost") or 0.0)
    return total


def append(text: str) -> None:
    with open(LOG, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(text + "\n")


def header() -> None:
    if "## 2026-10-27 — этап 2: пилот (протокол 1.1)" not in LOG.read_text(encoding="utf-8"):
        append("\n## 2026-10-27 — этап 2: пилот (протокол 1.1)\n\n5 задач × 3 повтора строго по очереди, без загрузки "
               "образов во время прогонов; стоп при суммарном расходе пилота $5. После каждого прогона: оценка swebench "
               "4.1.0, разбор адаптером, проверка конфига, рассуждений и ключа.\n")


def fail(run_id: str, code: str) -> None:
    d = OWN / "raw" / run_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "FAILED.json").write_text(json.dumps({"run_id": run_id, "runner_exit_code": int(code),
                                               "reason": "no output produced"}, indent=1), encoding="utf-8")


def log(run_id: str) -> str:
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    status = json.loads((OWN / "process_status.json").read_text(encoding="utf-8"))
    probs = [p for p in status["problems"] if p.startswith(run_id + "/")]
    raw = OWN / "raw" / run_id
    meta = json.loads((raw / "run_meta.json").read_text(encoding="utf-8")) if (raw / "run_meta.json").exists() else {}
    runs = pd.read_parquet(OWN / "runs_own.parquet")
    row = runs[runs.run_id.str.startswith(run_id + "/")]
    scores = pd.read_csv(OWN / "scores_own.csv")
    sc = scores[scores.run_id.str.startswith(run_id + "/")]
    total = spent()
    if (raw / "FAILED.json").exists():
        line = f"- {now} `{run_id}`: **технический сбой — нет вывода** (код {json.loads((raw / 'FAILED.json').read_text())['runner_exit_code']}); $0"
    elif len(row):
        r = row.iloc[0]
        risk = f"{sc.iloc[0].risk:.3f}" if len(sc) else "—"
        line = (f"- {now} `{run_id}`: {r.exit_status}, {r.n_steps} шагов, ${r.cost_usd:.4f}, resolved={r.resolved}, "
                f"риск {risk}, {meta.get('wall_time_s')} с")
    else:
        traj = next(raw.glob("*/*.traj.json"), None)
        info = json.loads(traj.read_text(encoding="utf-8"))["info"] if traj else {}
        line = (f"- {now} `{run_id}`: **не обработан** — exit {info.get('exit_status')}, "
                f"${(info.get('model_stats') or {}).get('instance_cost', 0):.4f}; " + "; ".join(probs))
    line += f"; ключ в файлах: нет; расход пилота ${total:.4f}"
    if probs and len(row):
        line += "; замечания: " + "; ".join(probs)
    append(line)
    stop = [p for p in probs if "REASONING" in p or "API KEY" in p or "config differs" in p]
    if stop:
        return "STOP: " + "; ".join(stop)
    if total >= BUDGET:
        return f"STOP: budget ${total:.4f} >= ${BUDGET}"
    return "OK"


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "spent":
        print(f"{spent():.6f}")
    elif cmd == "header":
        header()
    elif cmd == "fail":
        fail(sys.argv[2], sys.argv[3])
    elif cmd == "log":
        print(log(sys.argv[2]))
