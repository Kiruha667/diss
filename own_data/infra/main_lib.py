"""Helpers of the main-collection orchestrator (own_data/infra/main.ps1), PROTOCOL.md 1.2, sections 11.6-11.8.
Run from the repository root.
  python own_data/infra/main_lib.py next              -> '<run_id> <task> <round>' of the next run, or 'DONE'
  python own_data/infra/main_lib.py state <run_id>    -> new | needs_eval | incomplete | done
  python own_data/infra/main_lib.py attempt <run_id>  -> next free attempt number in raw_failed/ for this run
  python own_data/infra/main_lib.py spent             -> spend of protocol 1.2, USD (raw/ + raw_failed/ attempts)
  python own_data/infra/main_lib.py header            -> append the main-collection section header to LOG.md
  python own_data/infra/main_lib.py evalfail <run_id> -> mark a run whose evaluation gave no report twice
  python own_data/infra/main_lib.py log <run_id> <round>   -> append the run's LOG.md line; prints OK or STOP: <reason>
  python own_data/infra/main_lib.py summary           -> counts for the commit message: '<done main runs>'
  python own_data/infra/main_lib.py archived <run_id> <dest> | stopped <reason> | finished  -> LOG.md lines of main.ps1
    (written here: Windows PowerShell 5.1 reads a .ps1 without BOM as cp1251, Cyrillic text there gets garbled)
Pause: the file .own_main_stop in the repository root makes `log` answer STOP after the current run (main.ps1 then
commits and exits without gold_post); delete the file and start main.ps1 again to continue.
Rounds: 0, 1, 2 over all tasks of the pool (tasks_main.json order, minus gold_pre exclusions), then 3, 4 over the
first 30 tasks with a mixed outcome among rounds 0-2. Rounds 0-2 of the pilot tasks are the pilot runs (protocol 1.1).
"""
from __future__ import annotations

import datetime as dt
import functools
import json
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OWN = ROOT / "own_data"
LOG = OWN / "LOG.md"
STOP_FILE = ROOT / ".own_main_stop"
BUDGET = 35.0
MAX_EXTRA_TASKS = 30
PREFIX = "own-v1.2_mini-v1.17.1_glm-4.6_main"
PILOT_PREFIX = "own-v1.1_mini-v1.17.1_glm-4.6_pilot"
# agent outcomes; any other exit status (config/datasets.yaml infra_exit_statuses, RetryError after the API retries,
# Docker / environment exceptions) is a technical failure: mini-swe-agent then writes the exception text as the patch
AGENT_OUTCOMES = {"Submitted", "LimitsExceeded"}
# the agent's container is gone (it lives `container_timeout: 2h` and is started with --rm): every later command gets
# this Docker error, so the outcome says nothing about the agent — a technical failure whatever the exit status
CONTAINER_LOST = re.compile(r"Error response from daemon: (No such container|Container \S+ is not running)", re.I)
HEADER = "## 2026-10-28 — этап 4: основной сбор (протокол 1.2)"


def pool() -> list[str]:
    tasks = [t["task_id"] for t in json.loads((OWN / "tasks_main.json").read_text(encoding="utf-8"))["tasks"]]
    gv = json.loads((OWN / "gold_validation.json").read_text(encoding="utf-8"))  # required: stage V comes first
    return [t for t in tasks if t not in set(gv["excluded"])]


@functools.lru_cache(maxsize=None)
def pilot_tasks() -> set[str]:
    return {t["task_id"] for t in json.loads((OWN / "tasks_main.json").read_text(encoding="utf-8"))["tasks"]
            if t.get("source", "").startswith("pilot")}


def run_id(task: str, k: int) -> str:
    return f"{PILOT_PREFIX}_r{k}_{task}" if k <= 2 and task in pilot_tasks() else f"{PREFIX}_r{k}_{task}"


def traj_info(rid: str) -> tuple[Path | None, dict]:
    traj = next((OWN / "raw" / rid).glob("*/*.traj.json"), None)
    return traj, (json.loads(traj.read_text(encoding="utf-8")).get("info", {}) if traj else {})


def empty_patch(rid: str, iid: str) -> bool:
    preds = OWN / "raw" / rid / "preds.json"
    if not preds.exists():
        return False
    p = json.loads(preds.read_text(encoding="utf-8")).get(iid)
    return p is not None and not (p.get("model_patch") or "").strip()


def state(rid: str) -> str:
    raw = OWN / "raw" / rid
    if not raw.exists():
        return "new"
    traj, info = traj_info(rid)
    if traj is None or (raw / "FAILED.json").exists() or info.get("exit_status") not in AGENT_OUTCOMES or \
            CONTAINER_LOST.search(traj.read_text(encoding="utf-8")):
        return "incomplete"
    iid = traj.parent.name
    if (OWN / "eval" / rid / iid / "report.json").exists() or empty_patch(rid, iid) or \
            (OWN / "eval" / rid / "EVAL_FAILED.json").exists():
        return "done"
    return "needs_eval"


def label(rid: str) -> int | None:
    """1 = resolved, 0 = not resolved, None = no label."""
    traj, _ = traj_info(rid)
    if traj is None:
        return None
    iid = traj.parent.name
    rep = OWN / "eval" / rid / iid / "report.json"
    if rep.exists():
        return int(bool(json.loads(rep.read_text(encoding="utf-8"))[iid]["resolved"]))
    return 0 if empty_patch(rid, iid) else None


def plan() -> list[tuple[str, str, int]]:
    """All runs of the protocol in execution order, as far as it is known now."""
    tasks = pool()
    out = [(run_id(t, k), t, k) for k in (0, 1, 2) for t in tasks]
    if all(state(r) == "done" for r, _, _ in out):
        mixed = [t for t in tasks if len({label(run_id(t, k)) for k in (0, 1, 2)} - {None}) == 2]
        out += [(run_id(t, k), t, k) for k in (3, 4) for t in mixed[:MAX_EXTRA_TASKS]]
    return out


def next_run() -> str:
    for rid, t, k in plan():  # plan() lists rounds 3-4 only when rounds 0-2 are complete
        if state(rid) != "done":
            return f"{rid} {t} {k}"
    return "DONE"


def attempt(rid: str) -> int:
    n = 1
    while (OWN / "raw_failed" / f"{rid}-attempt{n}").exists():
        n += 1
    return n


def spent() -> float:
    total = 0.0
    paths = list((OWN / "raw").glob(f"{PREFIX}_*/*/*.traj.json")) + \
        list((OWN / "raw_failed").glob(f"{PREFIX}_*-attempt*/**/*.traj.json"))
    for p in paths:
        total += float((json.loads(p.read_text(encoding="utf-8")).get("info", {}).get("model_stats") or {})
                       .get("instance_cost") or 0.0)
    return total


def append(text: str) -> None:
    with open(LOG, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(text + "\n")


def header() -> None:
    if HEADER not in LOG.read_text(encoding="utf-8"):
        append(f"\n{HEADER}\n\nПо кругам (повтор 0 всех задач, затем 1, 2; затем 3, 4 смешанным задачам, ≤ 30), строго по "
               "одному прогону; стоп при расходе $35 без пилота. После каждого прогона: оценка swebench 4.1.0, разбор "
               "адаптером, проверка конфига, рассуждений и ключа; коммит каждые 10 прогонов.\n")


def evalfail(rid: str) -> None:
    d = OWN / "eval" / rid
    d.mkdir(parents=True, exist_ok=True)
    (d / "EVAL_FAILED.json").write_text(json.dumps({"run_id": rid, "reason": "no report after 2 evaluations"},
                                                   indent=1), encoding="utf-8")


def log(rid: str, rnd: str) -> str:
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    status = json.loads((OWN / "process_status.json").read_text(encoding="utf-8"))
    probs = [p for p in status["problems"] if p.startswith(rid + "/")]
    meta_p = OWN / "raw" / rid / "run_meta.json"
    meta = json.loads(meta_p.read_text(encoding="utf-8")) if meta_p.exists() else {}
    runs = pd.read_parquet(OWN / "runs_own.parquet")
    row = runs[runs.run_id.str.startswith(rid + "/")]
    total = spent()
    if len(row):
        r = row.iloc[0]
        lab = "пустой патч → 0" if r.get("label_source") == "empty_patch" else f"resolved={r.resolved}"
        line = (f"- {now} круг {rnd} `{rid}`: {r.exit_status}, {r.n_steps} шагов, ${r.cost_usd:.4f}, {lab}, "
                f"{meta.get('wall_time_s')} с")
    else:
        _, info = traj_info(rid)
        line = (f"- {now} круг {rnd} `{rid}`: **не обработан** — exit {info.get('exit_status')}, "
                f"${(info.get('model_stats') or {}).get('instance_cost', 0):.4f}; " + "; ".join(probs))
    if (OWN / "eval" / rid / "EVAL_FAILED.json").exists():
        line += "; **оценка не дала report.json дважды — без метки**"
    line += f"; ключ в файлах: нет; расход сбора ${total:.4f}"
    if probs and len(row):
        line += "; замечания: " + "; ".join(probs)
    append(line)
    stop = [p for p in probs if "REASONING" in p or "API KEY" in p or "config differs" in p]
    if stop:
        return "STOP: " + "; ".join(stop)
    if total >= BUDGET:
        return f"STOP: budget ${total:.4f} >= ${BUDGET}"
    if STOP_FILE.exists():
        return f"STOP: pause requested ({STOP_FILE.name}: {STOP_FILE.read_text(encoding='utf-8').strip()})"
    return "OK"


def archived(rid: str, dest: str) -> None:
    append(f"- технический сбой: {rid} перенесён в {dest.replace(chr(92), '/')}, повтор под тем же run_id")


def stopped(reason: str) -> None:
    append(f"- **ОСНОВНОЙ СБОР ОСТАНОВЛЕН:** {reason}")


def finished() -> None:
    append("- **Основной сбор завершён**: все круги выполнены.")


def summary() -> str:
    return str(sum(1 for d in (OWN / "raw").glob(f"{PREFIX}_*") if state(d.name) == "done"))


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "next":
        print(next_run())
    elif cmd == "state":
        print(state(sys.argv[2]))
    elif cmd == "attempt":
        print(attempt(sys.argv[2]))
    elif cmd == "spent":
        print(f"{spent():.6f}")
    elif cmd == "header":
        header()
    elif cmd == "evalfail":
        evalfail(sys.argv[2])
    elif cmd == "log":
        print(log(sys.argv[2], sys.argv[3]))
    elif cmd == "summary":
        print(summary())
    elif cmd == "archived":
        archived(sys.argv[2], sys.argv[3])
    elif cmd == "stopped":
        stopped(sys.argv[2])
    elif cmd == "finished":
        finished()
