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
  distinct_ratio, i.e. run > 1 and distinct ratio < 1); firing in < 5% of the trajectories -> not working, excluded
  from the models. Applied to every structural feature, old (E5) and new.
* Single-feature AUROC: whole sub-population, no CV (descriptive), 95% CI from 1000 bootstrap resamples of tasks;
  > 0.5 = higher in unreliable claims; strength = |AUROC - 0.5|.

Block 2 rules (fixed before any block-2 result was seen):
* Full E6 set = E5 M_len + E5 structural features without the two replaced loop features, without the features that
  fail the 5% firing rule and without n_steps_after_last_edit (identical to seg_len_steps; the latter is kept, it is
  part of M_len) + all block-1 features that work.
* Correlation: Pearson |r| on the trajectories of the sub-population. VIF_j = 1 / (1 - R^2_j), R^2_j from the
  least-squares regression of standardized feature j on all other features of the set (inf when R^2 = 1).
* Cleaning (greedy, "one per group"): features are visited in order of strength |single AUROC - 0.5| (descending,
  ties by name); a feature is kept unless its |r| with an already kept feature exceeds 0.8 — then it is dropped and
  that kept feature (the most correlated one) is its group representative. Exact compositions still complete after
  that (share_edit + share_verify + share_other = 1; the 9 bg_* frequencies sum to 1) lose their weakest component.
* AMENDMENT to E6_PROMPT.md (requested by the author before block 2 was run): phase_bigram_entropy is the anchor
  feature — the feature that links the work to the supervisor's methodology. It is visited FIRST, so it is never
  dropped and represents whatever group it falls into, regardless of single AUROC. The unamended rule (no anchor) is
  reported as a sensitivity analysis, with the AUROC cost of the amendment.
* The main selection uses single AUROC on all rows, as the prompt prescribes; because this uses labels, the same
  amended rule is also re-run inside every training fold (nested) and the resulting out-of-fold AUROC is reported.
* Models: StandardScaler + LR(L2, C=1), task folds of outputs/folds.csv, fold-averaged AUROC / PR-AUC, pooled Brier,
  paired task bootstrap (1000, within folds). Decision: the cleaned set becomes the main one if its AUROC is lower
  than the full E6 set's by no more than 0.01. Coefficients are published for the cleaned set only; for the full set
  only mean |SHAP| (exact linear SHAP on the LR fitted to all rows).
* AMENDMENT 2 (chosen by the author AFTER the block-2 results above were seen — reported as such): variant B drops
  the phase n-gram frequencies (bg_*, tg_*) from the candidates, because phase_bigram_entropy summarizes the
  phase-transition distribution and keeping the summary together with its components is redundant by construction
  (in the spec variant A they rebuilt the entropy: VIF 22.9, rank 18/30 by |SHAP|, sign flipped).
  phase_trigram_entropy is not a frequency and stays a candidate. Cleaning re-run with the same rules (anchor, greedy
  |r| > 0.8, compositions, nested check). Main set: B if AUROC(full 47) - AUROC(B) <= 0.01, else A if it passes the
  same test, else the full set. Variant A stays in the report.

Block 3 rules (fixed before any block-3 result was seen):
* Models: the main set of block 2; for context E5 M_len and E5 M_struct+len. Out-of-fold LR scores as in block 2.
* Budget k in {5, 10, 20}% of the trajectories, applied inside each test fold: the top ceil(k * n_fold) by score
  (stable order) are "checked"; counts are summed over folds. precision@k = found / checked, recall@k = found / all
  unreliable claims, lift@k = precision@k / share of unreliable claims, wasted = checked - found. 95% CIs: 1000
  bootstrap resamples of tasks within folds, seed 42.
* Isotonic calibration on training folds only: for each outer fold, inner out-of-fold LR scores on its 4 training
  folds (leave-one-training-fold-out), IsotonicRegression(out_of_bounds='clip') fitted on them, applied to the outer
  test predictions of the LR fitted on all 4 training folds. Brier on pooled out-of-fold probabilities before/after,
  reliability curves with 10 equal-count bins; fold-averaged AUROC after calibration reported (isotonic ties).
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
    """Value differs from the trivial one: run lengths > 1, distinct ratios < 1 (they are <= 1), others != 0."""
    v = df[col].astype(float)
    if col in ("max_run_length", "max_phase_run"):
        return v > 1
    if col in ("distinct_ratio", "distinct_cmd_ratio"):
        return v < 1
    return v != 0


def single_auroc(df: pd.DataFrame, cols: list[str], n_boot: int, seed: int = SEED) -> pd.DataFrame:
    """AUROC of each feature alone (no CV) with a 95% task-bootstrap CI, firing share overall and by class."""
    y = df.label.to_numpy()
    groups = list(df.reset_index(drop=True).groupby("task_id").indices.values())
    rng = np.random.default_rng(seed)
    boot_idx = [np.concatenate([groups[i] for i in rng.integers(0, len(groups), len(groups))]) for _ in range(n_boot)]
    out = []
    for c in cols:
        x = df[c].astype(float).to_numpy()
        f = fires(df, c)
        bs = np.array([auroc(y[ix], x[ix]) for ix in boot_idx])
        lo, hi = np.nanpercentile(bs, [2.5, 97.5])
        out.append({"feature": c, "nonzero_share": float((x != 0).mean()), "fire_share": f.mean(),
                    "fire_reliable": f[df.label == 0].mean(),
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
         "тривиального (≠ 0; для серий `max_run_length`, `max_phase_run` — > 1; для долей уникальных "
         "`distinct_ratio`, `distinct_cmd_ratio`, которые ≤ 1, — < 1); признак, срабатывающий "
         f"менее чем в {FIRE_MIN:.0%} траекторий, считается неработающим и исключается из моделей. Столбец «≠ 0» — "
         "буквальная доля ненулевых значений. Правила зафиксированы до результатов (коммит d0343fb, docstring "
         "`src/e6_refine.py`); единственная правка после первого прогона — ошибка реализации: для `distinct_ratio` "
         "срабатывание проверялось как > 1 (всегда ложно), исправлено на «отличается от 1», как в правиле.", ""]

    def table(t: pd.DataFrame, descr: bool) -> list[str]:
        out = ["| признак | ≠ 0 | срабатывает: всего / дост. / недост. | среднее: дост. / недост. | AUROC [95% ДИ] "
               "| статус |", "|---|---|---|---|---|---|"]
        for _, r in t.iterrows():
            name = f"`{r.feature}`" + (f" — {DESCR[r.feature]}" if descr and r.feature in DESCR else "")
            out.append(f"| {name} | {r.nonzero_share:.1%} | {r.fire_share:.1%} / {r.fire_reliable:.1%} / "
                       f"{r.fire_unreliable:.1%} | "
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


# ------------------------------------------------------------------------------------------ block 2: collinearity

ANCHOR = "phase_bigram_entropy"
R_MAX = 0.8
DUPLICATE = ("n_steps_after_last_edit", "seg_len_steps")  # (dropped, kept)
COMPOSITIONS = [["share_edit", "share_verify", "share_other"], BIGRAMS]
SELECTION_OUT = OUTPUTS / "e6_selection.json"


def load_features() -> pd.DataFrame:
    return pd.read_csv(FEATS_OUT, dtype={"run_id": str, "task_id": str})


def full_set(df: pd.DataFrame) -> tuple[list[str], list[str]]:
    """(full E6 feature set, structural features dropped by the 5% firing rule)."""
    old = [c for c in e5.STRUCT if c not in REPLACED and c != DUPLICATE[0]]
    cand = old + [c for c in new_features(df) if c not in old]
    dead = [c for c in cand if fires(df, c).mean() < FIRE_MIN]
    return e5.LEN + [c for c in cand if c not in dead], dead


def strengths(d: pd.DataFrame, cols: list[str]) -> dict[str, float]:
    y = d.label.to_numpy()
    return {c: abs(auroc(y, d[c].to_numpy(float)) - 0.5) for c in cols}


def select(d: pd.DataFrame, cols: list[str], anchor: str | None = ANCHOR,
           literal: bool = False) -> tuple[list[str], dict[str, str]]:
    """Greedy one-per-group cleaning (block-2 rules). Returns (kept, {dropped: representative | 'composition'}).
    literal=True ranks by the AUROC value itself instead of |AUROC - 0.5| (sensitivity only)."""
    s = strengths(d, cols)
    if literal:
        y = d.label.to_numpy()
        s = {c: auroc(y, d[c].to_numpy(float)) for c in cols}
    R = d[cols].corr().abs()
    rest = sorted((c for c in cols if c != anchor), key=lambda c: (-s[c], c))
    order = ([anchor] if anchor in cols else []) + rest
    kept, dropped = [], {}
    for c in order:
        hit = [k for k in kept if R.loc[c, k] > R_MAX]
        if hit:
            dropped[c] = max(hit, key=lambda k: R.loc[c, k])
        else:
            kept.append(c)
    for comp in COMPOSITIONS:
        if all(c in kept for c in comp):
            weakest = min((c for c in comp if c != anchor), key=lambda c: (s[c], c))
            kept.remove(weakest)
            dropped[weakest] = "composition"
    return [c for c in cols if c in kept], dropped


def vif(d: pd.DataFrame, cols: list[str]) -> pd.Series:
    X = d[cols].to_numpy(float)
    Z = (X - X.mean(0)) / X.std(0)
    out = {}
    for j, c in enumerate(cols):
        A = np.column_stack([np.ones(len(Z)), np.delete(Z, j, axis=1)])
        beta, *_ = np.linalg.lstsq(A, Z[:, j], rcond=None)
        r2 = 1 - (Z[:, j] - A @ beta).var() / Z[:, j].var()
        out[c] = math.inf if 1 - r2 < 1e-10 else 1 / (1 - r2)
    return pd.Series(out)


def nested_clean_oof(d: pd.DataFrame, cols: list[str], anchor: str | None = ANCHOR) -> tuple[np.ndarray, dict]:
    """Out-of-fold predictions with the cleaning re-run on the training folds only; kept sets per fold."""
    from src.stats import fit_predict

    pred = np.full(len(d), np.nan)
    kept_by_fold = {}
    for k in sorted(d.fold.unique()):
        te = (d.fold == k).to_numpy()
        tr = d[~te]
        kept, _ = select(tr, cols, anchor)
        kept_by_fold[int(k)] = kept
        pred[te] = fit_predict(tr, d[te], kept)
    return pred, kept_by_fold


def evaluate(d: pd.DataFrame, preds: dict[str, np.ndarray], n_boot: int, ref: str,
             pairs: list[tuple[str, str]] = ()) -> tuple[pd.DataFrame, dict]:
    """Fold-averaged AUROC / PR-AUC, pooled Brier, paired task-bootstrap CIs; deltas vs `ref` and extra pairs."""
    from src.stats import fold_avg, pr_auc

    y, fold, tasks = d.label.to_numpy(), d.fold.to_numpy(), d.task_id.to_numpy()
    boots = e5._fold_auroc_boot(y, preds, tasks, fold, n_boot)
    point = {m: fold_avg(auroc, y, p, fold) for m, p in preds.items()}
    rows = []
    for m, p in preds.items():
        lo, hi = np.nanpercentile(boots[m], [2.5, 97.5])
        dd = boots[m] - boots[ref]
        rows.append({"model_name": m, "n": len(d), "auroc": point[m], "auroc_ci_low": lo, "auroc_ci_high": hi,
                     "pr_auc": fold_avg(pr_auc, y, p, fold), "brier": float(np.mean((p - y) ** 2)),
                     "delta_vs_ref": point[m] - point[ref], "delta_ci_low": np.nanpercentile(dd, 2.5),
                     "delta_ci_high": np.nanpercentile(dd, 97.5)})
    extra = {}
    for a, b in pairs:
        dd = boots[a] - boots[b]
        extra[(a, b)] = (point[a] - point[b], np.nanpercentile(dd, 2.5), np.nanpercentile(dd, 97.5))
    return pd.DataFrame(rows), extra


def plot_corr(R: pd.DataFrame, path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(11, 10))
    im = ax.imshow(R.to_numpy(), cmap="RdBu_r", vmin=-1, vmax=1)
    ax.set_xticks(range(len(R)), R.columns, rotation=90, fontsize=6)
    ax.set_yticks(range(len(R)), R.index, fontsize=6)
    fig.colorbar(im, ax=ax, shrink=0.7, label="r Пирсона")
    ax.set_title("E6: корреляции признаков полного набора", fontsize=10)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=130)
    plt.close(fig)


def is_ngram(c: str) -> bool:
    return c.startswith(("bg_", "tg_"))


def run_block2(n_boot: int = 1000) -> dict:
    from src.stats import oof

    df = load_features()
    y = df.label.to_numpy()
    dup_diff = float((df[DUPLICATE[0]] - df[DUPLICATE[1]]).abs().max())
    cols, dead = full_set(df)
    cols_b = [c for c in cols if not is_ngram(c)]
    R = df[cols].corr()
    R.to_csv(OUTPUTS / "e6_corr.csv")
    plot_corr(R, REPORTS / "figures" / "e6_corr.png")
    var = {}
    for v, cand in (("A", cols), ("B", cols_b)):
        kept, dropped = select(df, cand, ANCHOR)
        kept_pure, dropped_pure = select(df, cand, None)
        var[v] = {"cand": cand, "kept": kept, "dropped": dropped, "kept_pure": kept_pure,
                  "dropped_pure": dropped_pure, "vif": vif(df, kept), "imp": e5.importance(df, kept)}
    var["A"]["kept_lit"], var["A"]["dropped_lit"] = select(df, cols, None, literal=True)
    v_full = vif(df, cols)
    pd.DataFrame({"vif_full": v_full, "vif_A": var["A"]["vif"], "vif_B": var["B"]["vif"]}) \
        .rename_axis("feature").to_csv(OUTPUTS / "e6_vif.csv")

    e5_cols = e5.STRUCT + [c for c in e5.LEN if c not in e5.STRUCT]
    preds = {"M_E5": oof(df, e5_cols), "M_E6_full": oof(df, cols)}
    nfeat = {"M_E5": len(e5_cols), "M_E6_full": len(cols)}
    for v in ("A", "B"):
        preds[f"M_E6_{v}"] = oof(df, var[v]["kept"])
        nfeat[f"M_E6_{v}"] = len(var[v]["kept"])
        if var[v]["kept_pure"] != var[v]["kept"]:
            preds[f"M_E6_{v}_pure"] = oof(df, var[v]["kept_pure"])
            nfeat[f"M_E6_{v}_pure"] = len(var[v]["kept_pure"])
        if v == "A":
            preds["M_E6_A_literal"] = oof(df, var["A"]["kept_lit"])
            nfeat["M_E6_A_literal"] = len(var["A"]["kept_lit"])
        preds[f"M_E6_{v}_nested"], var[v]["kept_by_fold"] = nested_clean_oof(df, var[v]["cand"], ANCHOR)
        nfeat[f"M_E6_{v}_nested"] = float(np.mean([len(k) for k in var[v]["kept_by_fold"].values()]))
    pairs = [("M_E6_full", "M_E5"), ("M_E6_A", "M_E5"), ("M_E6_B", "M_E5"), ("M_E6_B", "M_E6_A"),
             ("M_E6_A", "M_E6_A_literal")] + [(f"M_E6_{v}_pure", f"M_E6_{v}") for v in "AB" if f"M_E6_{v}_pure" in preds]
    summ, extra = evaluate(df, preds, n_boot, ref="M_E6_full", pairs=pairs)
    summ.insert(1, "n_features", summ.model_name.map(nfeat))
    summ.insert(0, "block", 2)
    summ.to_csv(OUTPUTS / "e6_summary.csv", index=False)
    a = summ.set_index("model_name").auroc
    main = next((v for v in ("B", "A") if a["M_E6_full"] - a[f"M_E6_{v}"] <= 0.01), "full")
    main_cols = cols if main == "full" else var[main]["kept"]
    SELECTION_OUT.write_text(json.dumps(
        {"main": main, "main_cols": main_cols, "full": cols, "dead": dead,
         **{f"{v}_{k}": var[v][k] for v in "AB" for k in ("kept", "dropped", "kept_pure", "kept_by_fold")},
         "A_kept_literal": var["A"]["kept_lit"]}, indent=1), encoding="utf-8")
    out = {"df": df, "cols": cols, "dead": dead, "dup_diff": dup_diff, "R": R, "var": var, "v_full": v_full,
           "summary": summ, "extra": extra, "main": main, "imp_full": e5.importance(df, cols),
           "single": {c: auroc(y, df[c].to_numpy(float)) for c in cols}, "strength": strengths(df, cols)}
    write_block2(out, REPORTS / "E6_collinearity.md", n_boot)
    return out


def _vif_fmt(v: float) -> str:
    return "∞" if math.isinf(v) else (f"{v:.0f}" if v >= 100 else f"{v:.1f}")


def _variant_lines(o: dict, v: str) -> list[str]:
    """Cleaning decisions, kept set, stability, VIF > 10, sign flips and the anchor's rank of one variant."""
    V, R, single, strength = o["var"][v], o["R"], o["single"], o["strength"]
    kept = V["kept"]
    L = ["| удалён | AUROC | представитель группы | r | AUROC представителя | причина |", "|---|---|---|---|---|---|"]
    for c, rep in V["dropped"].items():
        if rep == "composition":
            L.append(f"| `{c}` | {single[c]:.3f} | — | — | — | точная композиция: самая слабая компонента |")
        else:
            why = "якорь (поправка)" if rep == ANCHOR and strength[c] > strength[ANCHOR] else \
                "представитель сильнее по |AUROC − 0.5|"
            L.append(f"| `{c}` | {single[c]:.3f} | `{rep}` | {R.loc[c, rep]:+.2f} | {single[rep]:.3f} | {why} |")
    L += ["", f"Очищенный набор {v}: {len(kept)} признаков — " + ", ".join(f"`{c}`" for c in kept) + ".", ""]
    if V["kept_pure"] != kept:
        only_a = [c for c in kept if c not in V["kept_pure"]]
        only_p = [c for c in V["kept_pure"] if c not in kept]
        L += [f"Без якоря правило оставило бы {len(V['kept_pure'])} признаков: с якорем оставлены "
              + (", ".join(f"`{c}`" for c in only_a) or "—") + ", без якоря вместо них — "
              + (", ".join(f"`{c}`" for c in only_p) or "—") + ".", ""]
    else:
        grp = [c for c, r in V["dropped"].items() if r == ANCHOR]
        L += ["Без якоря (сила = |AUROC − 0.5|) набор тот же: энтропия биграмм сама сильнее признаков своей группы" +
              (" (" + ", ".join(f"`{c}` {single[c]:.3f}" for c in grp) + f" против {single[ANCHOR]:.3f})" if grp
               else "") + ".", ""]
    stab = Counter(c for k in V["kept_by_fold"].values() for c in k)
    diff = sorted(c for c in set(stab) | set(kept) if stab.get(c, 0) != (5 if c in kept else 0))
    L += ["Устойчивость (та же процедура внутри каждого из 5 обучающих фолдов): " +
          ("тот же набор во всех фолдах." if not diff else "расхождения с основным выбором — " +
           ", ".join(f"`{c}` (оставлен в {stab.get(c, 0)} из 5)" for c in diff) + "."), ""]
    hi = [c for c in kept if V["vif"][c] > 10]
    imp = V["imp"].set_index("feature")
    flip = [c for c in imp.index if np.sign(imp.coef_std[c]) != np.sign(single[c] - 0.5) and abs(single[c] - 0.5) > 0.02]
    rank = {c: i + 1 for i, c in enumerate(imp.index)}
    L += [f"- VIF > 10: " + (", ".join(f"`{c}` ({_vif_fmt(V['vif'][c])})" for c in hi) if hi else "нет") +
          f"; максимальный VIF {_vif_fmt(V['vif'].max())}.",
          "- Знак коэффициента противоречит направлению одиночного AUROC (|AUROC − 0.5| > 0.02): " +
          (", ".join(f"`{c}` (коэф. {imp.coef_std[c]:+.3f}, AUROC {single[c]:.3f}, VIF {_vif_fmt(V['vif'][c])})"
                     for c in flip) if flip else "нет") + ".",
          f"- `{ANCHOR}`: {rank[ANCHOR]}-й из {len(imp)} по среднему |SHAP| ({imp.mean_abs_shap[ANCHOR]:.3f}), "
          f"коэффициент {imp.coef_std[ANCHOR]:+.3f}, VIF {_vif_fmt(V['vif'][ANCHOR])} (в E5: 1-й, |SHAP| 0.315, "
          "коэффициент −0.413).", ""]
    return L


def _coef_table(imp: pd.DataFrame, single: dict, vifs: pd.Series) -> list[str]:
    L = ["| признак | коэффициент | среднее \\|SHAP\\| | AUROC в одиночку | VIF |", "|---|---|---|---|---|"]
    for _, r in imp.iterrows():
        L.append(f"| `{r.feature}` | {r.coef_std:+.3f} | {r.mean_abs_shap:.3f} | {single[r.feature]:.3f} | "
                 f"{_vif_fmt(vifs[r.feature])} |")
    return L


MODEL_NAMES = {"M_E5": "E5 `M_struct+len` (пересчёт)", "M_E6_full": "E6 полный",
               "M_E6_A": "A: очищенный по спецификации (с якорем)",
               "M_E6_A_pure": "A без якоря (чувствительность)",
               "M_E6_A_literal": "A, буквальное прочтение без якоря (чувствительность)",
               "M_E6_A_nested": "A, отбор внутри обучающих фолдов",
               "M_E6_B": "B: без частот n-грамм (поправка 2)", "M_E6_B_pure": "B без якоря (чувствительность)",
               "M_E6_B_nested": "B, отбор внутри обучающих фолдов"}


def write_block2(o: dict, path: Path, n_boot: int) -> None:
    df, cols, R, s, var = o["df"], o["cols"], o["R"], o["summary"].set_index("model_name"), o["var"]
    single = o["single"]
    main_name = {"A": "A (по спецификации)", "B": "B (поправка 2)", "full": "полный"}[o["main"]]
    L = ["# E6 — блок 2: устранение коллинеарности", "",
         f"Подпопуляция E5 (n = {len(df)}, недостоверных {int(df.label.sum())}). Правила зафиксированы в docstring "
         "`src/e6_refine.py` (раздел «Block 2 rules»): основные — до расчёта (коммит e656aed), поправка 2 — после "
         "первого расчёта, до расчёта варианта B.", "",
         "## Поправки к спецификации", "",
         f"1. **До расчёта.** Правило п. 3 E6_PROMPT.md («из группы с |r| > {R_MAX} оставить признак с наибольшим "
         f"одиночным AUROC») дополнено: **`{ANCHOR}` — якорный признак**. Через энтропию биграмм фаз работа связана "
         "с методикой научного руководителя, поэтому признак не удаляется: в любой группе, куда он попадает, "
         "остаётся именно он. Поправка внесена по требованию автора до получения результатов блока 2; правило без "
         "якоря посчитано как анализ чувствительности.",
         "2. **После первого расчёта (выбор автора, сделан, когда результаты варианта A были известны).** В варианте A "
         "якорь формально сохранился, но потерял смысл: частоты биграмм и триграмм фаз, из которых энтропия "
         "вычисляется, восстанавливали её (VIF 22.9), и в модели она оказалась 18-й из 30 по |SHAP| с перевёрнутым "
         "знаком. Вариант B исключает частоты n-грамм фаз (`bg_*`, `tg_*`) из кандидатов — энтропия и есть их "
         "сводка — и повторяет очистку по тем же правилам. Решение принято после просмотра результатов, поэтому оно "
         "проверено отбором внутри обучающих фолдов, а вариант A оставлен в отчёте.", "",
         "## Исходный набор", "",
         f"- Дубль: `{DUPLICATE[0]}` и `{DUPLICATE[1]}` совпадают (максимальное расхождение {o['dup_diff']:.0f}); "
         f"оставлен `{DUPLICATE[1]}` (входит в `M_len`).",
         "- Не входят (блок 1): заменённые `" + "`, `".join(REPLACED) + "`; не срабатывающие (< 5%): " +
         (", ".join(f"`{c}`" for c in o["dead"]) or "нет") + ".",
         f"- Полный набор E6: {len(cols)} признаков (длина E5, структурные E5, новые признаки блока 1); кандидаты "
         f"варианта B — {len(var['B']['cand'])} (без {len(cols) - len(var['B']['cand'])} частот n-грамм).", "",
         "## Матрица корреляций", "",
         "![корреляции](figures/e6_corr.png)", "",
         f"Полная матрица — `outputs/e6_corr.csv`. Пары с |r| > {R_MAX}:", "",
         "| признак 1 | признак 2 | r | AUROC 1 | AUROC 2 |", "|---|---|---|---|---|"]
    for i, a in enumerate(cols):
        for b in cols[i + 1:]:
            if abs(R.loc[a, b]) > R_MAX:
                L.append(f"| `{a}` | `{b}` | {R.loc[a, b]:+.2f} | {single[a]:.3f} | {single[b]:.3f} |")
    L += ["", "Порядок обхода: якорь, затем по убыванию |AUROC − 0.5|; признак удаляется, если |r| > 0.8 с уже "
          "оставленным (представитель группы — самый коррелированный из оставленных); у полностью оставшихся точных "
          "композиций (доли фаз в сумме 1, частоты 9 биграмм в сумме 1) удаляется самая слабая компонента.", "",
          "## Вариант B (поправка 2): решения очистки", ""] + _variant_lines(o, "B")
    L += ["## Вариант A (по спецификации): решения очистки", ""] + _variant_lines(o, "A")
    lit = var["A"]["kept_lit"]
    L += ["*Описательно, добавлено после первого расчёта.* При **буквальном** прочтении правила — «выше по одиночному "
          "AUROC» как по числу, без учёта направления — " +
          (f"`{ANCHOR}` **был бы удалён** (представитель — `{var['A']['dropped_lit'][ANCHOR]}`, AUROC "
           f"{single[var['A']['dropped_lit'][ANCHOR]]:.3f})" if ANCHOR not in lit else f"`{ANCHOR}` сохранился бы") +
          f"; такой набор — {len(lit)} признаков. Якорь защищает признак именно от этого прочтения.", ""]
    # VIF
    L += ["## VIF", "", "| признак | полный набор | A | B |", "|---|---|---|---|"]
    vf = o["v_full"]
    for c in sorted(cols, key=lambda c: (-1e18 if math.isinf(vf[c]) else -vf[c], c)):
        L.append(f"| `{c}` | {_vif_fmt(vf[c])} | " + " | ".join(
            _vif_fmt(var[v]["vif"][c]) if c in var[v]["kept"] else "—" for v in "AB") + " |")
    # models
    L += ["", "## Сравнение моделей", "",
          "Логистическая регрессия (L2, C=1, стандартизация), 5 фолдов по task_id; AUROC/PR-AUC усреднены по фолдам; "
          f"95% ДИ — парный бутстрэп задач внутри фолдов, {n_boot} повторов. Δ — к полному набору E6.", "",
          "| модель | признаков | AUROC [95% ДИ] | PR-AUC | Бриер | Δ к E6 полному [95% ДИ] |", "|---|---|---|---|---|---|"]
    for m, r in s.iterrows():
        nf = f"{r.n_features:.1f}" if m.endswith("_nested") else f"{int(r.n_features)}"
        L.append(f"| {MODEL_NAMES[m]} | {nf} | {_fmt(r.auroc)} [{_fmt(r.auroc_ci_low)}; {_fmt(r.auroc_ci_high)}] | "
                 f"{_fmt(r.pr_auc)} | {_fmt(r.brier)} | {r.delta_vs_ref:+.3f} [{r.delta_ci_low:+.3f}; "
                 f"{r.delta_ci_high:+.3f}] |")
    L.append("")
    for (a_, b_), (dl, lo, hi_) in o["extra"].items():
        L.append(f"- Δ({MODEL_NAMES[a_]} − {MODEL_NAMES[b_]}) = {dl:+.3f} [{lo:+.3f}; {hi_:+.3f}].")
    loss = {v: s.loc["M_E6_full", "auroc"] - s.loc[f"M_E6_{v}", "auroc"] for v in "AB"}
    L += ["", f"**Решение** (правило: потеря AUROC относительно полного набора ≤ 0.01; сначала проверяется B, затем A): "
          f"B теряет {loss['B']:+.3f}, A — {loss['A']:+.3f} → основной набор — **{main_name}**.", ""]
    # coefficients / SHAP
    main_v = o["main"] if o["main"] in var else None
    L += ["## Коэффициенты", "",
          "Логистическая регрессия на стандартизованных признаках, обучение на всех строках; > 0 — выше риск "
          "недостоверного заявления; среднее |SHAP| — точные значения Шепли для линейной модели.", ""]
    for v in ([main_v] if main_v else []) + [w for w in "BA" if w != main_v]:
        title = {"A": "Вариант A (по спецификации)", "B": "Вариант B (поправка 2)"}[v]
        L += [f"### {title}" + (" — основной" if v == main_v else ""), ""] + \
            _coef_table(var[v]["imp"], single, var[v]["vif"]) + [""]
    L += ["## Полный набор: только SHAP", "",
          "Коэффициенты полного набора не публикуются: при коллинеарности они не интерпретируемы.", "",
          "| признак | среднее \\|SHAP\\| |", "|---|---|"]
    for _, r in o["imp_full"].iterrows():
        L.append(f"| `{r.feature}` | {r.mean_abs_shap:.3f} |")
    path.write_text("\n".join(L) + "\n", encoding="utf-8")


def run_e6(block: str = "1", n_boot: int = 1000) -> dict:
    if block == "1":
        return run_block1(n_boot)
    if block == "2":
        return run_block2(n_boot)
    raise NotImplementedError(f"E6 block {block}: run blocks in order (E6_PROMPT.md)")
