"""E6: refinement of the claim-reliability model and external validation (E6_PROMPT.md).

Base (not redone): the E5 sub-population (G0 `has_claim`, 2877 runs), label 1 = the success claim is NOT reliable
(task failed by the SWE-bench tests), E5 features from src.e5_claims.process_record (unchanged), task folds
outputs/folds.csv (GroupKFold by task_id, 5 folds), LR with L2 + standardization (src.stats), task bootstrap 1000,
seed 42.

Block 1 rules (fixed before any E6 result was seen):
* Commands = executed commands of each assistant step (E0 adapter `tool_calls`). Normalized command: leading
  `cd DIR &&` / `cd DIR;` removed, /tmp/... paths -> TMP, hex hashes (7-40 chars with a letter and a digit) -> H,
  every digit run -> N (line numbers, sizes, ids), whitespace collapsed; heredoc bodies and quoted code are kept
  (rewriting a file with DIFFERENT content is not a repeat). Step signature = the normalized commands of the step
  joined by ' ;; '; "action steps" = steps with at least one executed command.
    repeat_ratio_norm    share of action steps (from the 2nd) whose signature equals the previous one;
    repeat_in_window_k   share of action steps whose signature occurred among the previous k action steps, k=3,5,10;
    max_run_length       longest run of identical consecutive signatures;
    distinct_ratio       unique signatures / all assistant steps;
    revisit_ratio        share of assistant steps whose commands mention a file already mentioned by an earlier step
                         (file = token with a file extension, outside heredoc bodies; ./ and /testbed/ stripped).
  They replace E5's repeat_ratio (exact consecutive duplicates) and distinct_cmd_ratio.
* Return codes: present in the raw logs (<returncode> in every tool output); the E0 adapter keeps non-zero codes as a
  'returncode: N' prefix of the tool text, so E5's nonzero_rc_share is kept as is. New: verify_fail_share = share of
  steps with a verification event whose tool output has a non-zero return code (0 when there is no verification
  step). The text proxy (Traceback, Error, No such file, command not found, SyntaxError, FAILED, exit status != 0) is
  computed as a diagnostic only (agreement with the return codes); it is not a model feature.
* Phase sequence (E5 phases per assistant step: edit > verify > other):
    phase_trigram_entropy  Shannon entropy of the trigram distribution, normalized by log2(27);
    bg_<a>_<b>             relative frequency of each of the 9 phase bigrams;
    tg_<a>_<b>_<c>         relative frequency of the 10 trigrams most frequent in the pooled population (the choice
                           uses counts only, never labels);
    max_phase_run          longest run of one phase;
    reverify_after_last_edit  some verification event after the last edit event is not the trajectory's first
                           verification event (a repeated check; without edits: >= 2 verification events).
* "Working" rule: a feature fires when it differs from its trivial value (0; 1 for max_run_length, max_phase_run,
  distinct_ratio); firing in < 5% of the trajectories -> not working, excluded from the models. Applied to every
  structural feature, old (E5) and new.
* Single-feature AUROC: whole sub-population, no CV (descriptive), 95% CI from 1000 bootstrap resamples of tasks;
  > 0.5 = higher in unreliable claims; strength = |AUROC - 0.5|.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

from src import e5_claims as e5
from src.adapters.swebench_bashonly import _text_of
from src.common import OUTPUTS, RAW, REPORTS, SEED, get_logger, load_leak_markers, read_jsonl_gz, trajectories_path
from src.folds import load_folds
from src.segment import compile_markers
from src.stats import auroc

log = get_logger("e6")

SOURCE = "swebench_bashonly"
PHASES = ("edit", "verify", "other")
FIRE_MIN = 0.05
WINDOWS = (3, 5, 10)
N_TOP_TRIGRAMS = 10
FEATS_OUT = OUTPUTS / "e6_features.csv"

LOOP = ["repeat_ratio_norm"] + [f"repeat_in_window_{k}" for k in WINDOWS] + \
       ["max_run_length", "distinct_ratio", "revisit_ratio"]
ERRORS = ["verify_fail_share"]
BIGRAMS = [f"bg_{a}_{b}" for a, b in product(PHASES, PHASES)]
SEQ_FIXED = ["phase_trigram_entropy", "max_phase_run", "reverify_after_last_edit"]
DIAG = ["diag_rc_step_err_share", "diag_text_err_share", "diag_text_err_verify_share"]
REPLACED = ["repeat_ratio", "distinct_cmd_ratio"]  # E5 loop features rewritten in block 1
TRIVIAL_ONE = {"max_run_length", "max_phase_run", "distinct_ratio"}

# ------------------------------------------------------------------------------------------ block 1: commands

_CD_RE = re.compile(r"^\s*(?:cd\s+[^\s;&|]+\s*(?:&&|;)\s*)+")
_TMP_RE = re.compile(r"/(?:var/)?tmp/[^\s'\"|;&<>)]*")
_HASH_RE = re.compile(r"\b(?=[0-9a-f]*[a-f])(?=[0-9a-f]*[0-9])[0-9a-f]{7,40}\b")
_DIGITS_RE = re.compile(r"\d+")
_FILE_RE = re.compile(r"(?<![\w/.-])((?:\.{1,2}/|/)?(?:[\w.-]+/)*[\w-][\w.-]*\.(?:py|pyi|pyx|pxd|c|cc|cpp|h|hpp|js|ts|"
                      r"jsx|tsx|java|rs|go|rb|rst|md|txt|cfg|ini|toml|json|yaml|yml|html|css|xml|sh|in|po|tex|log|out|"
                      r"diff|patch))\b")
_TEXT_ERR_RE = re.compile(r"Traceback|Error|No such file|command not found|SyntaxError|FAILED|exit status [1-9]")


def norm_command(cmd: str) -> str:
    c = _CD_RE.sub("", cmd)
    c = _TMP_RE.sub("TMP", c)
    c = _HASH_RE.sub("H", c)
    c = _DIGITS_RE.sub("N", c)
    return re.sub(r"\s+", " ", c).strip()


def command_files(cmd: str) -> set[str]:
    flat, _ = e5.g0._cut_heredocs(cmd)
    return {re.sub(r"^(?:\./|/testbed/)", "", m) for m in _FILE_RE.findall(flat)}


def loop_features(step_cmds: list[list[str]]) -> dict:
    n = len(step_cmds)
    sigs = [" ;; ".join(norm_command(c) for c in cmds) for cmds in step_cmds if cmds]
    na = len(sigs)
    out = {"repeat_ratio_norm": sum(a == b for a, b in zip(sigs, sigs[1:])) / (na - 1) if na > 1 else 0.0}
    for k in WINDOWS:
        out[f"repeat_in_window_{k}"] = sum(sigs[i] in sigs[max(0, i - k):i] for i in range(na)) / na if na else 0.0
    run = best = 0
    for i, s in enumerate(sigs):
        run = run + 1 if i and s == sigs[i - 1] else 1
        best = max(best, run)
    out["max_run_length"] = best
    out["distinct_ratio"] = len(set(sigs)) / max(1, n)
    seen: set[str] = set()
    revisits = 0
    for cmds in step_cmds:
        fs = set().union(*(command_files(c) for c in cmds)) if cmds else set()
        revisits += bool(fs & seen)
        seen |= fs
    out["revisit_ratio"] = revisits / max(1, n)
    return out


# ------------------------------------------------------------------------------------------ block 1: phases


def _phase(kinds) -> str:
    return "edit" if "edit" in kinds else "verify" if "verify" in kinds else "other"


def _entropy(counter: Counter, n_cells: int) -> float:
    tot = sum(counter.values())
    if not tot:
        return 0.0
    p = np.array(list(counter.values()), float) / tot
    return float(-(p * np.log2(p)).sum() / math.log2(n_cells))


def block1_record(rec: dict) -> dict:
    """Block-1 features of one trajectory (phases recomputed with the E5 rules for the sequence features)."""
    asst = [s for s in rec["steps"] if s["role"] == "assistant"]
    n = len(asst)
    files: dict[str, str] = {}
    step_kinds, events = [], []  # events: (assistant position, kind) in execution order
    for i, s in enumerate(asst):
        ks = []
        for c in s.get("tool_calls") or []:
            for kind, _ in e5.command_events(c, files):
                ks.append(kind)
                events.append((i, kind))
        step_kinds.append(ks)
    phases = [_phase(ks) for ks in step_kinds]
    pos_of = {s["step_idx"]: i for i, s in enumerate(asst)}
    outs: dict[int, list[str]] = {}
    for s in rec["steps"]:
        if s["role"] == "tool" and s["step_idx"] in pos_of:
            outs.setdefault(pos_of[s["step_idx"]], []).append(s["text"])

    row = {"_phases": phases}
    row.update(loop_features([s.get("tool_calls") or [] for s in asst]))

    # return codes (and the text proxy, diagnostic only)
    rc_err = {i: any(t.startswith("returncode: ") for t in ts) for i, ts in outs.items()}
    txt_err = {i: any(_TEXT_ERR_RE.search(t) for t in ts) for i, ts in outs.items()}
    vsteps = [i for i, ks in enumerate(step_kinds) if "verify" in ks]
    row["verify_fail_share"] = sum(rc_err.get(i, False) for i in vsteps) / len(vsteps) if vsteps else 0.0
    row["diag_rc_step_err_share"] = sum(rc_err.values()) / len(rc_err) if rc_err else 0.0
    row["diag_text_err_share"] = sum(txt_err.values()) / len(txt_err) if txt_err else 0.0
    row["diag_text_err_verify_share"] = sum(txt_err.get(i, False) for i in vsteps) / len(vsteps) if vsteps else 0.0
    row["_rc_txt"] = [(rc_err[i], txt_err[i]) for i in outs]

    # phase sequence
    bi = Counter(zip(phases, phases[1:]))
    tri = Counter(zip(phases, phases[1:], phases[2:]))
    nb, nt = max(1, sum(bi.values())), sum(tri.values())
    for a, b in product(PHASES, PHASES):
        row[f"bg_{a}_{b}"] = bi[(a, b)] / nb
    row["phase_trigram_entropy"] = _entropy(tri, 27)
    row["_tri"] = tri
    run = best = 0
    for i, p in enumerate(phases):
        run = run + 1 if i and p == phases[i - 1] else 1
        best = max(best, run)
    row["max_phase_run"] = best
    kinds = [k for _, k in events]
    last_edit = max((j for j, k in enumerate(kinds) if k == "edit"), default=-1)
    vpos = [j for j, k in enumerate(kinds) if k == "verify"]
    row["reverify_after_last_edit"] = int(any(j > last_edit for j in vpos[1:]))
    return row


def raw_rc_presence(raw_dir: Path = RAW / SOURCE) -> dict:
    """Do raw tool outputs carry a return code? (local raw trajectories: v1 user messages / v2 tool messages)."""
    n = with_rc = trajs = 0
    for p in sorted(raw_dir.glob("*/trajs/*.traj.json")):
        trajs += 1
        for m in json.loads(p.read_text(encoding="utf-8")).get("messages") or []:
            if not isinstance(m, dict):
                continue
            t = _text_of(m.get("content"))
            if m.get("role") == "tool" or (m.get("role") == "user" and "<returncode>" in t[:200]):
                n += 1
                with_rc += "<returncode>" in t
    return {"trajs": trajs, "outputs": n, "with_rc": with_rc}


def build_block1(claim_ids: set[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Claim rows (E5 features + block 1) and, over ALL trajectories, the non-zero return-code share by agent."""
    mre = compile_markers(load_leak_markers())
    rows, rc_by_agent = [], Counter()
    for rec in read_jsonl_gz(trajectories_path(SOURCE)):
        tools = [s["text"] for s in rec["steps"] if s["role"] == "tool"]
        rc_by_agent[(rec["agent_id"], "outputs")] += len(tools)
        rc_by_agent[(rec["agent_id"], "nonzero")] += sum(t.startswith("returncode: ") for t in tools)
        if rec["run_id"] not in claim_ids:
            continue
        r = {k: v for k, v in e5.process_record(rec, mre).items() if not k.startswith("_")}
        b = block1_record(rec)
        ph = b["_phases"]
        if not math.isclose(r["share_edit"], ph.count("edit") / max(1, len(ph))):
            raise AssertionError(f"phase mismatch with E5 for {rec['run_id']}")
        rows.append({**r, **b})
    agents = sorted({a for a, _ in rc_by_agent})
    rc = pd.DataFrame([{"agent_id": a, "outputs": rc_by_agent[(a, "outputs")],
                        "nonzero_share": rc_by_agent[(a, "nonzero")] / max(1, rc_by_agent[(a, "outputs")])}
                       for a in agents])
    return pd.DataFrame(rows), rc


def top_trigrams(tris: pd.Series, k: int = N_TOP_TRIGRAMS) -> list[tuple[str, str, str]]:
    tot = Counter()
    for c in tris:
        tot.update(c)
    return [t for t, _ in sorted(tot.items(), key=lambda x: (-x[1], x[0]))[:k]]


# ------------------------------------------------------------------------------------------ block 1: audit


def fires(df: pd.DataFrame, col: str) -> pd.Series:
    v = df[col].astype(float)
    return v > 1 if col in TRIVIAL_ONE else v != 0


def single_auroc(df: pd.DataFrame, cols: list[str], n_boot: int, seed: int = SEED) -> pd.DataFrame:
    """AUROC of each feature alone (no CV) with a 95% task-bootstrap CI, firing share overall and by class."""
    y = df.label.to_numpy()
    groups = [g.to_numpy() for g in df.groupby("task_id").indices.values()]
    rng = np.random.default_rng(seed)
    boot_idx = [np.concatenate([groups[i] for i in rng.integers(0, len(groups), len(groups))]) for _ in range(n_boot)]
    out = []
    for c in cols:
        x = df[c].astype(float).to_numpy()
        f = fires(df, c)
        bs = np.array([auroc(y[ix], x[ix]) for ix in boot_idx])
        lo, hi = np.nanpercentile(bs, [2.5, 97.5])
        out.append({"feature": c, "fire_share": f.mean(), "fire_reliable": f[df.label == 0].mean(),
                    "fire_unreliable": f[df.label == 1].mean(), "mean_reliable": x[y == 0].mean(),
                    "mean_unreliable": x[y == 1].mean(), "auroc": auroc(y, x), "auroc_ci_low": lo,
                    "auroc_ci_high": hi, "working": bool(f.mean() >= FIRE_MIN)})
    t = pd.DataFrame(out)
    t["strength"] = (t.auroc - 0.5).abs()
    return t


def load_population() -> tuple[pd.DataFrame, pd.DataFrame]:
    g = pd.read_csv(OUTPUTS / "g0_final_messages.csv", dtype={"run_id": str, "task_id": str})
    claim = g[g.has_claim]
    df, rc_agents = build_block1(set(claim.run_id))
    lab = dict(zip(claim.run_id, claim.label))
    if len(df) != len(claim) or int((df.run_id.map(lab) != df.label).sum()):
        raise ValueError(f"E6 population mismatch: {len(df)} rows vs {len(claim)} claims")
    fold_of = load_folds(OUTPUTS / "folds.csv")
    df["fold"] = df.task_id.map(fold_of).astype(int)
    df["agent_version"] = "v" + df.agent_id.str.extract(r"^mini-swe-agent-(\d+)\.")[0] + ".x"
    for c in ("has_any_verification", "verif_after_last_edit", "verified_before_first_edit"):
        df[c] = df[c].astype(int)
    tris = top_trigrams(df["_tri"])
    for t in tris:
        df["tg_" + "_".join(t)] = [c[t] / max(1, sum(c.values())) for c in df["_tri"]]
    return df, rc_agents


def tg_cols(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if c.startswith("tg_")]


def new_features(df: pd.DataFrame) -> list[str]:
    return LOOP + ERRORS + ["phase_trigram_entropy"] + BIGRAMS + tg_cols(df) + ["max_phase_run",
                                                                                 "reverify_after_last_edit"]


def rc_agreement(df: pd.DataFrame) -> dict:
    pairs = [p for ps in df["_rc_txt"] for p in ps]
    rc = np.array([a for a, _ in pairs], bool)
    tx = np.array([b for _, b in pairs], bool)
    return {"n_steps": len(pairs), "rc_err": rc.mean(), "txt_err": tx.mean(), "agree": (rc == tx).mean(),
            "rc_and_txt": (rc & tx).mean(), "rc_only": (rc & ~tx).mean(), "txt_only": (~rc & tx).mean()}


def _fmt(x, nd=3) -> str:
    return "—" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.{nd}f}"


DESCR = {
    "repeat_ratio_norm": "доля шагов, повторяющих предыдущую нормализованную команду",
    "repeat_in_window_3": "доля шагов, чья команда была среди 3 предыдущих",
    "repeat_in_window_5": "то же, окно 5",
    "repeat_in_window_10": "то же, окно 10",
    "max_run_length": "самая длинная серия одинаковых команд подряд",
    "distinct_ratio": "уникальные нормализованные команды / шаги",
    "revisit_ratio": "доля шагов, возвращающихся к уже упомянутому файлу",
    "verify_fail_share": "доля шагов верификации с ненулевым кодом возврата",
    "phase_trigram_entropy": "нормированная энтропия триграмм фаз",
    "max_phase_run": "самая длинная серия одной фазы подряд",
    "reverify_after_last_edit": "после последней правки была повторная (не первая) верификация",
}


def write_block1(df: pd.DataFrame, t_new: pd.DataFrame, t_old: pd.DataFrame, rc: dict, rc_agents: pd.DataFrame,
                 raw: dict, path: Path, n_boot: int) -> None:
    y = df.label
    L = ["# E6 — блок 1: починка признаков", "",
         f"Подпопуляция E5: траектории с заявлением об успехе (G0 `has_claim`), n = {len(df)} (недостоверных "
         f"{int(y.sum())}, достоверных {int((1 - y).sum())}, доля недостоверных {y.mean():.3f}). AUROC признака "
         f"в одиночку — по всей подпопуляции без CV (описательно), > 0.5 — признак выше у недостоверных заявлений; "
         f"95% ДИ — бутстрэп по задачам, {n_boot} повторов, seed 42. «Срабатывает» — значение отличается от "
         "тривиального (0; для `max_run_length`, `max_phase_run`, `distinct_ratio` — 1); признак, срабатывающий "
         f"менее чем в {FIRE_MIN:.0%} траекторий, считается неработающим и исключается из моделей. Правила "
         "зафиксированы до результатов (docstring `src/e6_refine.py`).", ""]

    def table(t: pd.DataFrame, descr: bool) -> list[str]:
        out = ["| признак | срабатывает: всего / дост. / недост. | среднее: дост. / недост. | AUROC [95% ДИ] | статус |",
               "|---|---|---|---|---|"]
        for _, r in t.iterrows():
            name = f"`{r.feature}`" + (f" — {DESCR[r.feature]}" if descr and r.feature in DESCR else "")
            out.append(f"| {name} | {r.fire_share:.1%} / {r.fire_reliable:.1%} / {r.fire_unreliable:.1%} | "
                       f"{r.mean_reliable:.3f} / {r.mean_unreliable:.3f} | {_fmt(r.auroc)} [{_fmt(r.auroc_ci_low)}; "
                       f"{_fmt(r.auroc_ci_high)}] | {'работает' if r.working else '**исключён** (< 5%)'} |")
        return out

    groups = [("1.1. Детектор зацикливания", LOOP), ("1.2. Неуспешные команды", ERRORS),
              ("1.3. Последовательность фаз", ["phase_trigram_entropy"] + BIGRAMS + tg_cols(df) +
               ["max_phase_run", "reverify_after_last_edit"])]
    L += ["## Новые признаки: одиночные AUROC", ""]
    for title, cols in groups:
        L += [f"### {title}", ""] + table(t_new[t_new.feature.isin(cols)].set_index("feature").loc[cols].reset_index(),
                                           True) + [""]
    L += ["Нормализация команды: убраны ведущие `cd DIR &&`, пути `/tmp/...` → TMP, хеши → H, все числа → N "
          "(номера строк, размеры), пробелы схлопнуты; тела heredoc и код в кавычках сохраняются (перезапись файла "
          "другим содержимым — не повтор). Сигнатура шага — нормализованные команды шага; «шаги-действия» — шаги "
          "с хотя бы одной выполненной командой. `bg_*` — относительные частоты всех 9 биграмм фаз; `tg_*` — "
          f"частоты {N_TOP_TRIGRAMS} самых частых триграмм в объединённой подпопуляции (отбор по частоте, без меток).",
          ""]
    # 1.2 diagnostics
    v1 = rc_agents[rc_agents.agent_id.str.startswith("mini-swe-agent-1.")].nonzero_share
    v2 = rc_agents[rc_agents.agent_id.str.startswith("mini-swe-agent-2.")].nonzero_share
    L += ["## 1.2. Коды возврата: диагностика", "",
          f"- В сыром логе коды возврата **есть**: тег `<returncode>` содержат {raw['with_rc']} из {raw['outputs']} "
          f"выводов инструментов в локально доступных сырых траекториях ({raw['trajs']} шт.; v1 — сообщения "
          "role=user, v2 — role=tool). Адаптер E0 сохраняет ненулевой код префиксом `returncode: N`; доля выводов с "
          f"ненулевым кодом по всем {int(rc_agents.outputs.sum())} выводам 7975 траекторий: v1.x "
          f"{v1.min():.1%}–{v1.max():.1%} по версиям агента, v2.x {v2.min():.1%}.",
          "- Значит, `nonzero_rc_share` извлекался правильно. Его коэффициент в E5 (+0.0004) близок к нулю потому, "
          "что признак не различает классы: среднее "
          f"{t_old.set_index('feature').loc['nonzero_rc_share', 'mean_reliable']:.3f} у достоверных против "
          f"{t_old.set_index('feature').loc['nonzero_rc_share', 'mean_unreliable']:.3f} у недостоверных, AUROC в "
          f"одиночку {_fmt(t_old.set_index('feature').loc['nonzero_rc_share', 'auroc'])}.",
          "- **Использован вариант с кодами возврата** (коды есть, прокси не нужен): `nonzero_rc_share` (E5, без "
          "изменений) и новый `verify_fail_share` — доля шагов верификации с ненулевым кодом.",
          f"- Текстовый прокси (Traceback, Error, No such file, command not found, SyntaxError, FAILED, ненулевой "
          f"exit status) посчитан только для сверки, в модели не входит. По {rc['n_steps']} шагам с выводом: "
          f"ненулевой код у {rc['rc_err']:.1%}, текстовые признаки ошибки у {rc['txt_err']:.1%}; совпадают на "
          f"{rc['agree']:.1%} шагов (оба — {rc['rc_and_txt']:.1%}, только код — {rc['rc_only']:.1%}, только "
          f"текст — {rc['txt_only']:.1%}). Расхождение ожидаемо: код возврата конвейера — код последней команды "
          "(`pytest ... | tail` даёт 0), а текст ловит и напечатанные, но обработанные ошибки.", "",
          "| диагностика | среднее: дост. / недост. | AUROC |", "|---|---|---|"]
    for c in DIAG:
        a = auroc(y.to_numpy(), df[c].to_numpy(float))
        L.append(f"| `{c}` | {df[y == 0][c].mean():.3f} / {df[y == 1][c].mean():.3f} | {_fmt(a)} |")
    # old features
    L += ["", "## Признаки E5: то же правило срабатывания", "",
          f"`{'`, `'.join(REPLACED)}` заменены детектором 1.1 и в модели E6 не входят; остальные проверены тем же "
          "правилом < 5%.", ""] + table(t_old, False)
    excl = list(t_new[~t_new.working].feature) + [f for f in t_old[~t_old.working].feature if f not in REPLACED]
    L += ["", "## Итог блока 1", "",
          "- Исключены как неработающие (срабатывают < 5%): " + (", ".join(f"`{c}`" for c in excl) or "нет") + ".",
          "- Заменены: " + ", ".join(f"`{c}`" for c in REPLACED) + ".",
          "- Сильнейшие новые признаки по |AUROC − 0.5|: " + ", ".join(
              f"`{r.feature}` {_fmt(r.auroc)}" for _, r in t_new[t_new.working].nlargest(5, "strength").iterrows()) + ".",
          "- Для сравнения, сильнейший признак E5 — `phase_bigram_entropy`, AUROC "
          f"{_fmt(t_old.set_index('feature').loc['phase_bigram_entropy', 'auroc'])}.", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(L) + "\n", encoding="utf-8")


def run_block1(n_boot: int = 1000) -> dict:
    df, rc_agents = load_population()
    new = new_features(df)
    old = [c for c in e5.STRUCT]
    t_new = single_auroc(df, new, n_boot)
    t_old = single_auroc(df, old, n_boot)
    rc = rc_agreement(df)
    write_block1(df, t_new, t_old, rc, rc_agents, raw_rc_presence(), REPORTS / "E6_features.md", n_boot)
    lead = ["run_id", "task_id", "agent_id", "model_id", "family", "agent_version", "exit_status", "fold", "label"]
    cols = lead + e5.LEN + [c for c in e5.STRUCT if c not in e5.LEN] + new + DIAG
    df[cols].to_csv(FEATS_OUT, index=False)
    pd.concat([t_new.assign(group="new"), t_old.assign(group="e5")]).to_csv(OUTPUTS / "e6_single_auroc.csv",
                                                                             index=False)
    return {"n": len(df), "new": t_new, "old": t_old, "rc": rc}


def run_e6(block: str = "1", n_boot: int = 1000) -> dict:
    if block == "1":
        return run_block1(n_boot)
    raise NotImplementedError(f"E6 block {block}: run blocks in order (E6_PROMPT.md)")
