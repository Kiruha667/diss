"""own_data/STAGE2_REPORT.md — pilot of PROTOCOL.md 1.1 (5 tasks x 3 repeats): criteria of the protocol (section 3),
outcomes per task, score_trajectory risks, spend. Run from the repository root:
  python own_data/infra/stage2_report.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.adapters.swebench_bashonly import load_cfg, parse_trajectory, read_label  # noqa: E402

OWN = ROOT / "own_data"
PLANNED = 15
REF = json.loads((OWN / "stage0_results.json").read_text(encoding="utf-8"))["protocol_reference"]["glm-4.6"]
OK_EXIT = ("Submitted", "LimitsExceeded")


def pilot_runs() -> pd.DataFrame:
    infra = list(load_cfg().get("infra_exit_statuses") or [])
    rows = []
    for d in sorted((OWN / "raw").glob("*_pilot_*")):
        rid = d.name
        k = int(rid.split("_pilot_r")[1].split("_")[0])
        task = rid.split(f"_pilot_r{k}_")[1]
        row = {"run_id": rid, "task_id": task, "repeat": k, "exit_status": "no output", "cost": 0.0, "parsed": False,
               "label": None}
        traj = d / task / f"{task}.traj.json"
        if traj.exists():
            t = json.loads(traj.read_text(encoding="utf-8"))
            info = t.get("info", {})
            row["exit_status"] = info.get("exit_status")
            row["cost"] = float((info.get("model_stats") or {}).get("instance_cost") or 0.0)
            rec, reason = parse_trajectory(t, rid, task, {}, {}, infra)
            row["parsed"] = rec is not None
            row["parse_reason"] = reason
        rep = OWN / "eval" / rid / task / "report.json"
        row["label"] = read_label(rep, task) if rep.exists() else None
        rows.append(row)
    return pd.DataFrame(rows)


def main() -> None:
    p = pilot_runs()
    runs = pd.read_parquet(OWN / "runs_own.parquet")
    runs = runs[runs.run_id.str.contains("_pilot_") & (runs.in_analysis == 1)].copy()
    runs["run_dir"] = runs.run_id.str.split("/").str[0]
    sc = pd.read_csv(OWN / "scores_own.csv")
    risk = dict(zip(sc.run_id.str.split("/").str[0], sc.risk))
    p = p.merge(runs[["run_dir", "n_steps", "submitted", "resolved", "claim_success"]].rename(
        columns={"run_dir": "run_id"}), on="run_id", how="left")
    p["risk"] = p.run_id.map(risk)
    n = len(p)
    tech_ok = int(p.exit_status.isin(OK_EXIT).sum())
    # interrupted attempts (machine reboot etc.) are kept in own_data/raw_failed and count as technical failures
    failed_attempts = sorted(d.name for d in (OWN / "raw_failed").glob("*_pilot_*")) if (OWN / "raw_failed").exists() else []
    attempts = n + len(failed_attempts)
    failures = attempts - tech_ok
    parsed = int(p.parsed.sum())
    labelled = int(p.label.notna().sum())
    med_cost = float(p.cost.median()) if n else float("nan")
    total = float(p.cost.sum())
    sub = p[p.submitted == 1]
    share = float(sub.resolved.mean()) if len(sub) else float("nan")
    med_len = float(sub.n_steps.median()) if len(sub) else float("nan")
    by_task = p[p.resolved.notna()].groupby("task_id").resolved.agg(lambda s: s.nunique() == 2)
    mixed = int(by_task.sum())
    lo, hi = REF["resolved_share_corridor"]
    llo, lhi = REF["median_steps_corridor"]
    crit = [("Прогоны без технических сбоев", "≥ 14 из 15 (не больше 1 сбоя)",
             f"{tech_ok} из {attempts} попыток; сбоев {failures} (прерванных попыток {len(failed_attempts)})",
             failures <= 1 and n == PLANNED),
            ("Траектории, разобранные адаптером без ошибок", "15 из 15", f"{parsed} из {n}", parsed == PLANNED),
            ("Метка получена", "15 из 15", f"{labelled} из {n}", labelled == PLANNED),
            ("Медианная стоимость прогона", "≤ $0.20", f"${med_cost:.4f}", med_cost <= 0.20),
            ("Суммарный расход пилота", "< $5 (жёсткая остановка)", f"${total:.4f}", total < 5.0),
            ("Задачи со смешанным исходом", "≥ 1 из 5", f"{mixed} из {by_task.size}", mixed >= 1),
            ("Доля решённых (отправленные прогоны)", f"[{lo:.3f}; {hi:.3f}]", f"{share:.3f} ({int(sub.resolved.sum())} из {len(sub)})",
             lo <= share <= hi),
            ("Медиана длины (отправленные прогоны)", f"[{llo:.1f}; {lhi:.1f}] шага", f"{med_len:.1f}", llo <= med_len <= lhi)]
    L = ["# Этап 2 — пилот (протокол 1.1)", "",
         f"glm-4.6 (z.ai, thinking disabled), mini-swe-agent 1.17.1, конфиг `config/glm-4.6_v1.1.yaml`; 5 задач × 3 повтора "
         f"строго по очереди. Выполнено прогонов: {n} из {PLANNED}. Метка — swebench 4.1.0 (`resolved`). Журнал — `LOG.md`.",
         "", "## Критерии (PROTOCOL.md, раздел 3)", "", "| критерий | порог | значение | итог |", "|---|---|---|---|"]
    for name, thr, val, ok in crit:
        L.append(f"| {name} | {thr} | {val} | {'**выполнен**' if ok else '**не выполнен**'} |")
    L += ["", f"Итог: выполнено {sum(c[3] for c in crit)} из {len(crit)} критериев."]
    if failed_attempts:
        L += ["", "Прерванные попытки (технические сбои, остатки в `raw_failed/`; прогон повторён под тем же run_id): "
              + ", ".join(f"`{a}`" for a in failed_attempts) + ". Их расход по локальным данным неизвестен (траектория "
              "не сохранилась) и в суммы ниже не входит."]
    L += ["",
          "## Исходы по задачам", "",
          "Ячейка — resolved (1 / 0), число шагов, стоимость; статус выхода, если не `Submitted`. «Корпус» — единственный "
          "прогон glm-4.6 на этой задаче в корпусе лидерборда.", "",
          "| задача | повтор 0 | повтор 1 | повтор 2 | смешанный исход | glm-4.6 в корпусе |", "|---|---|---|---|---|---|"]
    pilot_tasks = json.loads((OWN / "tasks_pilot.json").read_text(encoding="utf-8"))["tasks"]
    for t in pilot_tasks:
        cells = []
        for k in range(3):
            r = p[(p.task_id == t["task_id"]) & (p.repeat == k)]
            if not len(r):
                cells.append("—")
                continue
            r = r.iloc[0]
            res = "—" if pd.isna(r.resolved) else int(r.resolved)
            steps = "—" if pd.isna(r.n_steps) else int(r.n_steps)
            extra = "" if r.exit_status == "Submitted" else f", {r.exit_status}"
            cells.append(f"{res}, {steps} ш., ${r.cost:.3f}{extra}")
        mx = by_task.get(t["task_id"])
        L.append(f"| `{t['task_id']}` | " + " | ".join(cells) + f" | {'да' if mx else 'нет' if mx is not None else '—'} | "
                 f"{t['glm-4.6']} |")
    L += ["", "## Риски score_trajectory", "",
          "Риск — вероятность нерешённой задачи по модели демо-ноутбука (логистическая регрессия на 21 признаке "
          "`M_struct+len`, обучена на корпусе без задачи прогона). При 15 прогонах — только описательно.", "",
          "| прогон | задача | resolved | риск |", "|---|---|---|---|"]
    for _, r in p.sort_values(["task_id", "repeat"]).iterrows():
        L.append(f"| r{int(r['repeat'])} | `{r['task_id']}` | {'—' if pd.isna(r['resolved']) else int(r['resolved'])} | "
                 f"{'—' if pd.isna(r['risk']) else format(r['risk'], '.3f')} |")
    ok = p[p.risk.notna() & p.resolved.notna()]
    if len(ok) and ok.resolved.nunique() == 2:
        m1, m0 = ok[ok.resolved == 0].risk.mean(), ok[ok.resolved == 1].risk.mean()
        pairs = conc = 0
        for _, g in ok.groupby("task_id"):
            u, s = g[g.resolved == 0].risk.to_numpy(), g[g.resolved == 1].risk.to_numpy()
            d = u[:, None] - s[None, :]
            pairs += d.size
            conc += (d > 0).sum() + 0.5 * (d == 0).sum()
        L += ["", f"- Средний риск: нерешённые {m1:.3f}, решённые {m0:.3f}.",
              f"- Пары «нерешённый — решённый» внутри одной задачи: {pairs}; нерешённый получил больший риск в "
              f"{conc:g} из них" + (f" ({conc / pairs:.2f})." if pairs else ".")]
    # descriptive: the corpus glm-4.6 run vs the pilot, per task (offline, data/demo/runs.parquet)
    corpus = pd.read_parquet(ROOT / "data" / "demo" / "runs.parquet")
    cg = corpus[(corpus.model == "glm-4.6") & corpus.task_id.isin(p.task_id.unique())].set_index("task_id")
    L += ["", "## Корпус и пилот по задачам (описательно)", "",
          "Корпус — единственный прогон glm-4.6 на задаче (mini-swe-agent 1.17.1, декабрь 2025); пилот — 3 повтора.",
          "", "| задача | корпус: resolved, шагов, статус | пилот: решено, шаги (медиана) |", "|---|---|---|"]
    for t in pilot_tasks:
        tid = t["task_id"]
        g = p[p.task_id == tid]
        c = cg.loc[tid] if tid in cg.index else None
        cs = f"{int(c.resolved)}, {int(c.n_steps)}, {c.exit_status}" if c is not None else "—"
        L.append(f"| `{tid}` | {cs} | {int(g.resolved.sum())} из {len(g)}, {g.n_steps.median():.0f} |")
    c_share = float(cg.resolved.mean()) if len(cg) else float("nan")
    L += ["", f"- На этих 5 задачах glm-4.6 в корпусе решила {int(cg.resolved.sum())} из {len(cg)} ({c_share:.1f}); "
          f"в пилоте — {int(sub.resolved.sum())} из {len(sub)} ({share:.2f}). Опора критерия (0.314) взята по 36 задачам "
          "пула (TODO-6), но и относительно этих 5 задач (коридор ±0.2 от {:.1f}) доля пилота выше.".format(c_share),
          "- Расхождение сосредоточено в задачах, где корпусный прогон не решил, а пилот решил во всех трёх повторах. "
          "Возможные причины (не проверены): обновление модели у провайдера под тем же именем; отличие инструмента "
          "оценки (swebench 4.1.0 здесь против инструмента лидерборда); случайность единственного корпусного прогона "
          "на задачу. Медиана длины при этом в коридоре (34 шага)."]
    smoke = 0.0127373 + 0.01983208
    L += ["", "## Расход", "", f"- Пилот: ${total:.4f} (медиана прогона ${med_cost:.4f}, максимум ${p.cost.max():.4f}).",
          f"- До пилота: smoke 1.0 $0.0127 и проба $0.0198. Всего с ключа: ${total + smoke:.4f}.", ""]
    (OWN / "STAGE2_REPORT.md").write_text("\n".join(L), encoding="utf-8")
    print(json.dumps({"n": n, "criteria_met": sum(c[3] for c in crit), "total": total}, ensure_ascii=False))


if __name__ == "__main__":
    main()
