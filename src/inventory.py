"""E0: inventory of the unified trajectories, leak-marker scan, task folds, acceptance check.

Writes:
  reports/E0_inventory.md      tables required by AGENTS.md E0 + acceptance verdict
  config/leak_markers.txt      candidate markers that actually occur (masked in E1)
  outputs/leak_markers_freq.csv
  outputs/traj_index.csv       run_id, task_id, agent_id, model_id, model_family, label, n_steps, exit_status
  outputs/exclusions.csv       every excluded trajectory with reason and class
  outputs/folds.csv            the single task -> fold map used by E3 (pca1) and E4
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

from src.common import CONFIG, OUTPUTS, REPORTS, get_logger, interim_dir, load_leak_markers, read_jsonl_gz, trajectories_path
from src.folds import make_folds
from src.segment import compile_markers, segment

log = get_logger("inventory")

MIN_TASKS_BOTH = 200
MIN_TRAJ = 2000


def _q(x) -> str:
    x = np.asarray(list(x), dtype=float)
    if len(x) == 0:
        return "—"
    return " / ".join(f"{v:.0f}" for v in np.percentile(x, [0, 25, 50, 75, 100]))


def _marker_patterns(markers: list[str]) -> dict[str, re.Pattern]:
    return {m: compile_markers([m]) for m in markers}


def run_inventory(source: str) -> dict:
    cand = load_leak_markers(CONFIG / "leak_marker_candidates.txt")
    pats = _marker_patterns(cand)
    rows, marker_hits = [], []
    for rec in read_jsonl_gz(trajectories_path(source)):
        a_steps = [s for s in rec["steps"] if s["role"] == "assistant"]
        n_reason = len(segment(rec, "reason_sent", None))
        rows.append({
            "run_id": rec["run_id"], "task_id": rec["task_id"], "agent_id": rec["agent_id"],
            "model_id": rec["model_id"], "model_family": rec["model_family"], "label": int(rec["label"]),
            "n_steps": len(a_steps), "n_reason_sent": n_reason,
            "exit_status": rec["meta"].get("exit_status", ""),
        })
        text_a = "\n".join(s["text"] for s in a_steps)
        text_t = "\n".join(s["text"] for s in rec["steps"] if s["role"] != "assistant")
        for m, p in pats.items():
            ca, ct = len(p.findall(text_a)), len(p.findall(text_t))
            if ca or ct:
                marker_hits.append({"marker": m, "run_id": rec["run_id"], "label": int(rec["label"]),
                                    "n_assistant": ca, "n_other": ct})
    df = pd.DataFrame(rows)
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    df.drop(columns=["n_reason_sent"]).to_csv(OUTPUTS / "traj_index.csv", index=False)

    excl_path = interim_dir(source) / "excluded.csv"
    excl = pd.read_csv(excl_path, dtype=str).fillna("") if excl_path.exists() else pd.DataFrame(
        columns=["run_id", "task_id", "label", "stage", "reason"])
    excl.to_csv(OUTPUTS / "exclusions.csv", index=False)

    folds = make_folds(df["task_id"])
    folds.to_csv(OUTPUTS / "folds.csv", index=False)

    # leak markers: frequency by class; every marker that occurs goes to leak_markers.txt
    hits = pd.DataFrame(marker_hits, columns=["marker", "run_id", "label", "n_assistant", "n_other"])
    n_by_class = df["label"].value_counts().to_dict()
    freq = []
    for m in cand:
        h = hits[hits["marker"] == m]
        freq.append({
            "marker": m,
            "share_fail_runs": len(h[h.label == 1]) / max(1, n_by_class.get(1, 0)),
            "share_success_runs": len(h[h.label == 0]) / max(1, n_by_class.get(0, 0)),
            "occ_assistant": int(h["n_assistant"].sum()), "occ_tool_user": int(h["n_other"].sum()),
        })
    freq = pd.DataFrame(freq)
    freq.to_csv(OUTPUTS / "leak_markers_freq.csv", index=False)
    found = [r["marker"] for _, r in freq.iterrows() if r["occ_assistant"] + r["occ_tool_user"] > 0]
    (CONFIG / "leak_markers.txt").write_text(
        "# Written by E0 (src/inventory.py): candidate outcome markers found in the texts.\n"
        "# E1 replaces them with [MASKED] (case-insensitive, word-bounded).\n" + "\n".join(found) + "\n",
        encoding="utf-8")

    # tasks with both classes
    per_task = df.groupby("task_id")["label"].agg(n="size", n_fail="sum")
    per_task["n_succ"] = per_task["n"] - per_task["n_fail"]
    both = per_task[(per_task.n_fail > 0) & (per_task.n_succ > 0)]
    n_both = len(both)
    ok = n_both >= MIN_TASKS_BOTH and len(df) >= MIN_TRAJ

    L = ["# E0 — инвентаризация", "",
         f"Источник: `{source}` (SWE-bench Verified, mini-SWE-agent bash-only; подмножество D2 — 16 прогонов "
         "моделей с видимыми рассуждениями, отобраны по наличию текста до анализа, не по меткам).", "",
         "## Итог", "",
         f"- траекторий: **{len(df)}**, доля провалов: **{df.label.mean():.3f}** "
         f"({int(df.label.sum())} провалов / {int((1 - df.label).sum())} успехов)",
         f"- задач всего: {df.task_id.nunique()}; задач с прогонами обоих классов: **{n_both}**",
         f"- исключено на этапе E0: {len(excl)} (см. ниже)",
         f"- **критерий приёмки** (≥{MIN_TASKS_BOTH} задач с обоими классами и ≥{MIN_TRAJ} траекторий): "
         + ("**выполнен**" if ok else "**НЕ выполнен — остановка, нужен источник №2/№3**"), "",
         "## Распределения (мин / 25% / медиана / 75% / макс)", "",
         "| величина | все | успех | провал |", "|---|---|---|---|",
         f"| шагов агента | {_q(df.n_steps)} | {_q(df[df.label == 0].n_steps)} | {_q(df[df.label == 1].n_steps)} |",
         f"| предложений (seg=reason_sent, без маскировки) | {_q(df.n_reason_sent)} | "
         f"{_q(df[df.label == 0].n_reason_sent)} | {_q(df[df.label == 1].n_reason_sent)} |", "",
         f"Доля траекторий с ≥200 предложений reason_sent: {(df.n_reason_sent >= 200).mean():.3f} "
         f"(успех {(df[df.label == 0].n_reason_sent >= 200).mean():.3f}, провал {(df[df.label == 1].n_reason_sent >= 200).mean():.3f}).", "",
         "## Задачи с обоими классами", "",
         f"- задач: {n_both}; прогонов в них: {int(both.n.sum())} (провалов {int(both.n_fail.sum())}, успехов {int(both.n_succ.sum())})",
         f"- прогонов на задачу: {_q(both.n)}; провалов на задачу: {_q(both.n_fail)}; успехов на задачу: {_q(both.n_succ)}",
         f"- задач с ≥3 прогонами каждого класса: {int(((both.n_fail >= 3) & (both.n_succ >= 3)).sum())}",
         f"- задач, решённых всеми прогонами: {int((per_task.n_fail == 0).sum())}; не решённых ни одним: {int((per_task.n_succ == 0).sum())}", "",
         "## По агентам и моделям", "",
         "| agent_id | model_id | семейство | траекторий | доля провалов | медиана шагов | медиана предложений |",
         "|---|---|---|---|---|---|---|"]
    for (ag, mo, fa), g in df.groupby(["agent_id", "model_id", "model_family"]):
        L.append(f"| {ag} | {mo} | {fa} | {len(g)} | {g.label.mean():.3f} | {g.n_steps.median():.0f} | {g.n_reason_sent.median():.0f} |")
    L += ["", "## Исключённые траектории (по причинам и классам)", ""]
    if len(excl):
        t = excl.assign(label=excl["label"].replace({"": "?", "0": "успех", "1": "провал"})) \
            .groupby(["reason", "label"]).size().unstack(fill_value=0)
        L += ["| причина | " + " | ".join(map(str, t.columns)) + " |", "|---" * (len(t.columns) + 1) + "|"]
        L += [f"| {r} | " + " | ".join(str(v) for v in t.loc[r]) + " |" for r in t.index]
    else:
        L.append("Нет.")
    L += ["", "## Статусы завершения (только учёт, в признаках не используются)", "",
          "| exit_status | успех | провал |", "|---|---|---|"]
    es = df.groupby(["exit_status", "label"]).size().unstack(fill_value=0)
    for s in es.index:
        L.append(f"| {s or '—'} | {es.loc[s].get(0, 0)} | {es.loc[s].get(1, 0)} |")
    L += ["", "## Маркеры исхода (проверка на утечку метки)", "",
          "Доля траекторий класса, где маркер встречается хотя бы раз; вхождения — в тексте агента и в выводах "
          "инструментов/среды. Найденные маркеры записаны в `config/leak_markers.txt` и маскируются в E1.", "",
          "| маркер | доля (провал) | доля (успех) | вхождений: агент | вхождений: инструменты/среда |",
          "|---|---|---|---|---|"]
    for _, r in freq.iterrows():
        L.append(f"| `{r.marker}` | {r.share_fail_runs:.3f} | {r.share_success_runs:.3f} | {r.occ_assistant} | {r.occ_tool_user} |")
    L += ["", f"Разбиение на {folds.fold.nunique()} фолдов по task_id сохранено в `outputs/folds.csv` "
              "(GroupKFold, shuffle, seed 42) и используется во всех следующих этапах."]
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "E0_inventory.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    log.info("E0: %d trajectories, %d both-class tasks, acceptance=%s", len(df), n_both, ok)
    return {"n_traj": len(df), "n_tasks_both": n_both, "accepted": ok, "leak_markers": found}


def segmentation_report(source: str, stats: pd.DataFrame, path: Path | None = None, clauses: bool = False) -> bool:
    """E1 acceptance: median fragments per trajectory for seg=reason_sent >= 200."""
    path = path or REPORTS / "E1_segmentation.md"
    L = ["# E1 — сегментация", "", f"Режим: {'клаузы' if clauses else 'предложения'} (blingfire), код — по строкам, "
         "блоки > 10 строк усечены до 10; фрагменты < 15 символов отброшены; маркеры исхода замаскированы.", "",
         "| seg | траекторий | мин / 25% / медиана / 75% / макс фрагментов | доля ≥100 | доля ≥200 | доля ≥400 |",
         "|---|---|---|---|---|---|"]
    for seg, g in stats.groupby("seg"):
        n = g["n_frag_total"]
        L.append(f"| {seg} | {len(g)} | {_q(n)} | {(n >= 100).mean():.3f} | {(n >= 200).mean():.3f} | {(n >= 400).mean():.3f} |")
    med = stats.loc[stats.seg == "reason_sent", "n_frag_total"].median() if (stats.seg == "reason_sent").any() else float("nan")
    ok = bool(med >= 200)
    L += ["", f"Критерий приёмки E1 (медиана reason_sent ≥ 200): медиана = {med:.0f} → "
          + ("**выполнен**" if ok else "**не выполнен** — дробить на клаузы (`--clauses`), затем N=100")]
    if not stats.empty:
        L += ["", "По классам (медиана фрагментов):", "", "| seg | успех | провал |", "|---|---|---|"]
        for seg, g in stats.groupby("seg"):
            L.append(f"| {seg} | {g[g.label == 0].n_frag_total.median():.0f} | {g[g.label == 1].n_frag_total.median():.0f} |")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(L) + "\n", encoding="utf-8")
    return ok
