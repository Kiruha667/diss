"""E5: reliability of the agent's success claim (E5_PROMPT.md).

Sub-population: trajectories whose final message claims success (G0: has_claim == True, 2877 runs). Label: the
benchmark tests — 1 = the claim is NOT reliable (task failed), 0 = reliable. AUROC = detecting unreliable claims.
Two hypotheses, compared:
  H1  H and C of the segment AFTER the last code edit (verification / justification phase) separate the classes;
  H2  structural process features (verification, phase order, repeats, errors) separate them.

Rules fixed before any E5 result was seen:
* Step events (edit / verify) = G0 rules, reused verbatim from src.g0_false_success (classify_segment on the
  executed commands of every assistant step). Step phase: 'edit' if the step has an edit event, else 'verify' if it
  has a verification event, else 'other'.
* Segment = assistant steps strictly AFTER the last step with an edit event; its fragments are the E1 reason_sent
  fragments (same cleaning incl. leak-marker masking) of those steps. No edit at all -> the whole trajectory, flagged.
* H1 window: the FIRST N fragments of the segment (the phase right after the last edit); N, d from the Stage-1 gate on
  the median segment length in sentences; segments shorter than N are excluded and counted by class.
  Series: projections cos_step, cos_task, pca1 (E2; pca1 fitted on training folds only) of MiniLM embeddings (E1).
  H_full / C_full: E3 features (reason_sent, first N fragments of the whole trajectory, same N, d), outputs/features.csv.
* M_len = n_steps (all assistant steps), seg_len_steps, seg_len_sent, n_edits.
* Models: StandardScaler + LogisticRegression(L2, C=1) (src.stats.fit_predict), no tuning; task folds from
  outputs/folds.csv (GroupKFold by task_id, 5 folds); AUROC / PR-AUC fold-averaged (as in E4), Brier on pooled
  out-of-fold probabilities; 95% CIs from 1000 bootstrap resamples of TASKS within folds, paired across models.
* No oracle: the gold patch is never used.
* Shapley values: exact linear SHAP (phi_j = beta_j * (z_j - mean z_j) on standardized inputs, independent features).
Seed 42 everywhere.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from src import g0_false_success as g0
from src.chaos import chaos_features
from src.common import (EMB, OUTPUTS, REPORTS, SEED, get_logger, load_leak_markers, read_jsonl_gz, safe_name,
                        task_emb_path, trajectories_path)
from src.folds import load_folds
from src.project import cos_step, cos_task, fit_pca1, pca1
from src.segment import compile_markers, segment, task_sentences
from src.stats import auroc, fit_predict, fold_avg, oof, pr_auc, usable_columns

log = get_logger("e5")

SOURCE = "swebench_bashonly"
PROJS = ("cos_step", "cos_task", "pca1")
LEN = ["n_steps", "seg_len_steps", "seg_len_sent", "n_edits"]
STRUCT = ["has_any_verification", "n_verifications", "verif_after_last_edit", "n_verif_after_last_edit",
          "verified_before_first_edit", "n_steps_after_last_edit", "share_edit", "share_verify", "share_other",
          "n_transitions", "phase_bigram_entropy", "share_before_first_edit", "repeat_ratio", "distinct_cmd_ratio",
          "nonzero_rc_share", "n_files_edited", "n_file_switches"]
CHAOS_SEG = [f"{k}_seg_{p}" for p in PROJS for k in ("H", "C")]
CHAOS_FULL = [f"{k}_full_{p}" for p in PROJS for k in ("H", "C")]
GATE_OUT = OUTPUTS / "e5_gate.json"
FEATS_OUT = OUTPUTS / "e5_features.csv"
SEG_EMB = EMB.parent / "emb_e5" / "segment"

# ------------------------------------------------------------------------------------------ step events


def _norm_path(p: str) -> str:
    p = p.strip("'\"")
    p = re.sub(r"^(\./|/testbed/)", "", p)
    return p


def _edit_target(seg: str, docs: list[dict]) -> str:
    """Best-effort target file of an edit segment ('?' if unknown)."""
    noq = g0._QUOTED_RE.sub("''", seg)
    plain = g0._PLACEHOLDER_RE.sub(" ", noq)
    red = g0._REDIRECT_RE.search(plain)
    if red and g0._is_code_target(red.group(1)):
        return _norm_path(red.group(1))
    tee = g0._TEE_RE.search(plain)
    if tee and g0._is_code_target(tee.group(1)):
        return _norm_path(tee.group(1))
    w = g0._words(plain)
    if w and w[0] in ("sed", "perl") and len(w) > 1 and not w[-1].startswith("-") and w[-1] != "''":
        return _norm_path(w[-1])
    ph = g0._PLACEHOLDER_RE.search(seg)
    code = docs[int(ph.group(1))]["body"] if ph else " ".join(g0._QUOTED_RE.findall(seg))
    m = re.search(r"open\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"][wa]", code) or \
        re.search(r"Path\(\s*['\"]([^'\"]+)['\"]\s*\)\.write_text", code)
    if m:
        return _norm_path(m.group(1))
    return "?"


def command_events(cmd: str, files: dict[str, str]) -> list[tuple[str, str]]:
    """[(kind, target)] for one executed command — the same splitting and classification as
    g0_false_success.classify_command, plus the edit target."""
    flat, docs = g0._cut_heredocs(cmd)
    quotes: list[str] = []

    def protect(m):
        quotes.append(m.group(0))
        return f"__QUOTE{len(quotes) - 1}__"

    out = []
    for seg in g0._SPLIT_RE.split(g0._QUOTED_RE.sub(protect, flat)):
        seg = re.sub(r"__QUOTE(\d+)__", lambda m: quotes[int(m.group(1))], seg)
        if not seg.strip():
            continue
        kind = g0.classify_segment(seg, docs, files)
        if kind:
            out.append((kind, _edit_target(seg, docs) if kind == "edit" else ""))
    return out


def _norm_cmd(c: str) -> str:
    return re.sub(r"\s+", " ", c).strip()


# ------------------------------------------------------------------------------------------ per trajectory


def process_record(rec: dict, marker_re) -> dict:
    """Stage-1 segment audit + Stage-3 structural features of one trajectory (full trajectory, no gold patch)."""
    asst = [s for s in rec["steps"] if s["role"] == "assistant"]
    n = len(asst)
    files: dict[str, str] = {}
    events = []  # (step_idx, kind, target) in execution order
    for s in asst:
        for c in s.get("tool_calls") or []:
            for kind, target in command_events(c, files):
                events.append((s["step_idx"], kind, target))
    edit_steps = sorted({i for i, k, _ in events if k == "edit"})
    verif_steps = sorted({i for i, k, _ in events if k == "verify"})
    kinds = [k for _, k, _ in events]
    last_edit_ev = max((j for j, k in enumerate(kinds) if k == "edit"), default=None)
    first_edit_ev = min((j for j, k in enumerate(kinds) if k == "edit"), default=None)
    last_edit_step = edit_steps[-1] if edit_steps else -1

    # segment after the last edit step (E1 reason_sent fragments, with E1 cleaning)
    frags = segment(rec, "reason_sent", marker_re)
    seg_frags = [f for f in frags if f.step_idx > last_edit_step]

    phases = []
    for i in range(n):
        ks = {k for j, k, _ in events if j == i}
        phases.append("edit" if "edit" in ks else "verify" if "verify" in ks else "other")
    bigrams = list(zip(phases, phases[1:]))
    if bigrams:
        _, cnt = np.unique(np.array([a + ">" + b for a, b in bigrams]), return_counts=True)
        p = cnt / cnt.sum()
        bigram_h = float(-(p * np.log2(p)).sum() / math.log2(9))
    else:
        bigram_h = 0.0
    cmds = [_norm_cmd(c) for s in asst for c in (s.get("tool_calls") or [])]
    reps = sum(1 for a, b in zip(cmds, cmds[1:]) if a == b)
    tools = [s for s in rec["steps"] if s["role"] == "tool"]
    nonzero = sum(1 for t in tools if t["text"].startswith("returncode: "))
    targets = [t for _, k, t in events if k == "edit" and t != "?"]
    switches = sum(1 for a, b in zip(targets, targets[1:]) if a != b)
    after = kinds[last_edit_ev + 1:] if last_edit_ev is not None else kinds

    seg_steps = n - (last_edit_step + 1)
    return {
        "run_id": rec["run_id"], "task_id": rec["task_id"], "agent_id": rec["agent_id"],
        "model_id": rec["model_id"], "family": rec["model_family"], "label": int(rec["label"]),
        "exit_status": rec["meta"].get("exit_status", ""),
        # length
        "n_steps": n, "seg_len_steps": seg_steps, "seg_len_sent": len(seg_frags), "n_edits": len(edit_steps),
        "no_edit": not edit_steps, "seg_empty": seg_steps == 0,
        # verification
        "has_any_verification": bool(verif_steps), "n_verifications": len(verif_steps),
        "verif_after_last_edit": ("verify" in after) if last_edit_ev is not None else bool(verif_steps),
        "n_verif_after_last_edit": after.count("verify"),
        "verified_before_first_edit": "verify" in (kinds[:first_edit_ev] if first_edit_ev is not None else kinds),
        "n_steps_after_last_edit": seg_steps,
        # phases
        "share_edit": phases.count("edit") / max(1, n), "share_verify": phases.count("verify") / max(1, n),
        "share_other": phases.count("other") / max(1, n),
        "n_transitions": sum(1 for a, b in bigrams if a != b), "phase_bigram_entropy": bigram_h,
        "share_before_first_edit": (edit_steps[0] / max(1, n)) if edit_steps else 1.0,
        # repeats and errors
        "repeat_ratio": reps / (len(cmds) - 1) if len(cmds) > 1 else 0.0,
        "distinct_cmd_ratio": len(set(cmds)) / max(1, n),
        "nonzero_rc_share": nonzero / max(1, len(tools)),
        "n_files_edited": len(set(targets)), "n_file_switches": switches,
        # kept for Stage 2 (not written to the CSV)
        "_seg_texts": [f.text for f in seg_frags],
        "_task_text": rec["task_text"],
    }


def build_rows(claim_ids: set[str]) -> list[dict]:
    mre = compile_markers(load_leak_markers())
    rows = []
    for rec in read_jsonl_gz(trajectories_path(SOURCE)):
        if rec["run_id"] in claim_ids:
            rows.append(process_record(rec, mre))
    return rows


# ------------------------------------------------------------------------------------------ Stage 1: gate


def _q(x) -> str:
    x = np.asarray(list(x), dtype=float)
    return " / ".join(f"{v:.0f}" for v in np.percentile(x, [0, 25, 50, 75, 90])) if len(x) else "—"


def gate_decision(median_sent: float) -> dict:
    if median_sent >= 200:
        return {"tested": True, "N": 200, "d": 4, "biased": False}
    if median_sent >= 100:
        return {"tested": True, "N": 100, "d": 4, "biased": False}
    if median_sent >= 50:
        return {"tested": True, "N": 50, "d": 3, "biased": True}
    return {"tested": False, "N": None, "d": None, "biased": None}


def write_audit(df: pd.DataFrame, path: Path) -> dict:
    med = float(df.seg_len_sent.median())
    gate = {**gate_decision(med), "median_seg_len_sent": med, "n": len(df)}
    L = ["# E5 — этап 1 (шлюз): аудит длины сегмента после последней правки", "",
         f"Подпопуляция: траектории, где финальное сообщение заявляет успех (G0 `has_claim`), n = {len(df)} "
         f"(недостоверных — провал по тестам — {int(df.label.sum())}, достоверных {int((1 - df.label).sum())}).",
         "Сегмент — шаги агента строго после последнего шага с правкой (правила G0); длина в шагах и во фрагментах "
         "reason_sent E1 (предложения прозы + строки кода, очистка E1).", "",
         "## Распределение длины сегмента (мин / 25% / медиана / 75% / 90%)", "",
         "| популяция | n | шагов | предложений |", "|---|---|---|---|",
         f"| все заявившие | {len(df)} | {_q(df.seg_len_steps)} | {_q(df.seg_len_sent)} |",
         f"| достоверные (успех) | {int((df.label == 0).sum())} | {_q(df[df.label == 0].seg_len_steps)} | {_q(df[df.label == 0].seg_len_sent)} |",
         f"| недостоверные (провал) | {int((df.label == 1).sum())} | {_q(df[df.label == 1].seg_len_steps)} | {_q(df[df.label == 1].seg_len_sent)} |",
         "", f"- Сегмент пуст (правка была последним действием): {df.seg_empty.mean():.1%} "
             f"(достоверные {df[df.label == 0].seg_empty.mean():.1%}, недостоверные {df[df.label == 1].seg_empty.mean():.1%}).",
         f"- Правок не было вовсе (сегмент = вся траектория): {int(df.no_edit.sum())}.",
         f"- Доля сегментов ≥ 50 / ≥ 100 / ≥ 200 предложений: {(df.seg_len_sent >= 50).mean():.1%} / "
         f"{(df.seg_len_sent >= 100).mean():.1%} / {(df.seg_len_sent >= 200).mean():.1%}.", "",
         "## Решение по шлюзу", "",
         f"Медиана длины сегмента: **{med:.0f} предложений**. Правило: ≥ 200 → N = 200, d = 4; 100–200 → N = 100, d = 4; "
         "50–100 → N = 50, d = 3 (оценка смещена); < 50 → H1 на сегменте не проверяется.", ""]
    if gate["tested"]:
        L.append(f"**Решение: H1 проверяется при N = {gate['N']}, d = {gate['d']}**" +
                 (" — оценка энтропии смещена (короткое окно, см. AGENTS.md п. 1.1)." if gate["biased"] else "."))
    else:
        L.append("**Решение: H1 на сегменте НЕ проверяется** — это результат: аппарат перестановочной энтропии неприменим "
                 "к объекту такой длины (при n < 50 смещение оценки H велико, см. AGENTS.md п. 1.1 и требование n > 5·d!). "
                 "Переход к этапу 3.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(L) + "\n", encoding="utf-8")
    GATE_OUT.write_text(json.dumps(gate, indent=2), encoding="utf-8")
    return gate


# ------------------------------------------------------------------------------------------ Stage 2: H1


def segment_chaos(rows: list[dict], N: int, d: int, fold_of: dict[str, int], batch_size: int = 128) -> pd.DataFrame:
    """H, C of the first N fragments of the segment for every trajectory with >= N segment fragments."""
    from src.embed import embed_tasks, load_encoder

    elig = [r for r in rows if r["seg_len_sent"] >= N]
    model = None
    SEG_EMB.mkdir(parents=True, exist_ok=True)
    todo = [r for r in elig if not (SEG_EMB / f"{safe_name(r['run_id'])}.npy").exists()]
    if todo:
        model = load_encoder("minilm")
        texts = [t for r in todo for t in r["_seg_texts"][:N]]
        log.info("E5: embedding %d segment fragments of %d trajectories", len(texts), len(todo))
        emb = model.encode(texts, batch_size=batch_size, convert_to_numpy=True, normalize_embeddings=True,
                           show_progress_bar=False).astype(np.float32)
        i = 0
        for r in todo:
            np.save(SEG_EMB / f"{safe_name(r['run_id'])}.npy", emb[i:i + N])
            i += N
    mre = compile_markers(load_leak_markers())
    tasks = {r["task_id"]: task_sentences(r["_task_text"], mre) for r in elig}
    missing = {t: s for t, s in tasks.items() if not task_emb_path("minilm", t).exists()}
    if missing:
        model = model or load_encoder("minilm")
        embed_tasks(missing, "minilm", model=model)
    embs = {r["run_id"]: np.load(SEG_EMB / f"{safe_name(r['run_id'])}.npy") for r in elig}
    folds = sorted(set(fold_of.values()))
    pcas = {k: fit_pca1([embs[r["run_id"]] for r in elig if fold_of[r["task_id"]] != k]) for k in folds}
    out = []
    for r in elig:
        E = embs[r["run_id"]]
        tv = np.load(task_emb_path("minilm", r["task_id"]))
        series = {"cos_step": cos_step(E), "cos_task": cos_task(E, tv), "pca1": pca1(E, pcas[fold_of[r["task_id"]]])}
        row = {"run_id": r["run_id"]}
        for p, s in series.items():
            f = chaos_features(s, d)
            row[f"H_seg_{p}"], row[f"C_seg_{p}"] = f["H"], f["C"]
        out.append(row)
    return pd.DataFrame(out)


def full_chaos(N: int, d: int, run_ids: set[str]) -> pd.DataFrame:
    """E3 features (reason_sent, first N fragments of the whole trajectory) for cos_step / cos_task / pca1."""
    src = OUTPUTS / "features.csv"
    if not src.exists():
        src = OUTPUTS / "features.csv.gz"
    usecols = ["run_id", "seg", "proj", "N", "d", "H", "C"]
    parts = []
    for ch in pd.read_csv(src, usecols=usecols, dtype={"run_id": str}, chunksize=200_000):
        ch = ch[(ch.seg == "reason_sent") & (ch.N == N) & (ch.d == d) & ch.proj.isin(PROJS) & ch.run_id.isin(run_ids)]
        parts.append(ch)
    f = pd.concat(parts)
    wide = f.pivot_table(index="run_id", columns="proj", values=["H", "C"])
    wide.columns = [f"{k}_full_{p}" for k, p in wide.columns]
    return wide.reset_index()


# ------------------------------------------------------------------------------------------ models


def _fold_auroc_boot(y, preds: dict[str, np.ndarray], tasks, fold, n_boot: int, seed: int = SEED) -> dict:
    """Paired bootstrap (tasks resampled within folds): {model: array(n_boot) of fold-averaged AUROC}."""
    per_fold = []
    for k in np.unique(fold):
        m = np.flatnonzero(fold == k)
        uniq, inv = np.unique(tasks[m], return_inverse=True)
        per_fold.append([m[inv == i] for i in range(len(uniq))])
    rng = np.random.default_rng(seed)
    out = {name: np.full(n_boot, np.nan) for name in preds}
    for b in range(n_boot):
        vals = {name: [] for name in preds}
        for rows_of in per_fold:
            idx = np.concatenate([rows_of[i] for i in rng.integers(0, len(rows_of), len(rows_of))])
            yy = y[idx]
            if yy.min() == yy.max():
                continue
            for name, p in preds.items():
                vals[name].append(auroc(yy, p[idx]))
        for name in preds:
            if vals[name]:
                out[name][b] = np.mean(vals[name])
    return out


def evaluate_models(df: pd.DataFrame, models: dict[str, list[str]], n_boot: int, baseline: str = "M_len",
                    extra_pairs: list[tuple[str, str]] = ()) -> tuple[pd.DataFrame, dict, pd.DataFrame]:
    """OOF predictions, fold-averaged AUROC / PR-AUC, pooled Brier, paired task-bootstrap CIs and deltas."""
    need = sorted({c for cols in models.values() for c in cols})
    d = df.dropna(subset=need).reset_index(drop=True)
    y, fold, tasks = d.label.to_numpy(), d.fold.to_numpy(), d.task_id.to_numpy()
    preds = {}
    for name, cols in models.items():
        preds[name] = oof(d, usable_columns(d, cols))
    boots = _fold_auroc_boot(y, preds, tasks, fold, n_boot)
    rows = []
    for name, p in preds.items():
        a = fold_avg(auroc, y, p, fold)
        bd = boots[name] - boots[baseline] if baseline in boots else None
        ci = np.nanpercentile(boots[name], [2.5, 97.5])
        row = {"model_name": name, "n": len(d), "auroc": a, "auroc_ci_low": ci[0], "auroc_ci_high": ci[1],
               "pr_auc": fold_avg(pr_auc, y, p, fold), "brier": float(np.mean((p - y) ** 2)),
               "delta_vs_len": a - fold_avg(auroc, y, preds[baseline], fold) if baseline in preds else math.nan,
               "delta_ci_low": np.nanpercentile(bd, 2.5) if bd is not None else math.nan,
               "delta_ci_high": np.nanpercentile(bd, 97.5) if bd is not None else math.nan}
        rows.append(row)
    pairs = {}
    for a_, b_ in extra_pairs:
        if a_ in preds and b_ in preds:
            dd = boots[a_] - boots[b_]
            pairs[(a_, b_)] = (fold_avg(auroc, y, preds[a_], fold) - fold_avg(auroc, y, preds[b_], fold),
                               np.nanpercentile(dd, 2.5), np.nanpercentile(dd, 97.5))
    return pd.DataFrame(rows), {"preds": preds, "pairs": pairs, "data": d}, d


def within_task(d: pd.DataFrame, preds: dict[str, np.ndarray], n_boot: int, seed: int = SEED) -> pd.DataFrame:
    """Control 1: AUROC inside tasks that have both reliable and unreliable claims, averaged over tasks."""
    groups = [g.index.to_numpy() for _, g in d.groupby("task_id") if g.label.nunique() == 2]
    y = d.label.to_numpy()
    rng = np.random.default_rng(seed)
    out = []
    for name, p in preds.items():
        per = np.array([auroc(y[ix], p[ix]) for ix in groups])
        boots = [np.nanmean(per[rng.integers(0, len(per), len(per))]) for _ in range(n_boot)]
        out.append({"model_name": name, "n_tasks": len(groups), "within_task_auroc": float(np.nanmean(per)),
                    "ci_low": float(np.percentile(boots, 2.5)), "ci_high": float(np.percentile(boots, 97.5))})
    return pd.DataFrame(out)


def transfer(d: pd.DataFrame, cols: list[str], group_col: str, pairs=None) -> pd.DataFrame:
    """Controls 2-3: train on other groups (and other task folds), test on the target group; compare with
    in-distribution out-of-fold AUROC on the same rows."""
    cols = usable_columns(d, cols)
    y, fold = d.label.to_numpy(), d.fold.to_numpy()
    in_dist = oof(d, cols)
    groups = sorted(d[group_col].dropna().unique())
    pairs = pairs or [("все остальные", g) for g in groups]
    out = []
    for src, tgt in pairs:
        is_t = (d[group_col] == tgt).to_numpy()
        is_s = ~is_t if src == "все остальные" else (d[group_col] == src).to_numpy()
        cross = np.full(len(d), np.nan)
        for k in np.unique(fold):
            te, tr = is_t & (fold == k), is_s & (fold != k)
            if te.sum() and tr.sum():
                cross[te] = fit_predict(d[tr], d[te], cols)
        ok = is_t & ~np.isnan(cross)
        a_in, a_cr = fold_avg(auroc, y[ok], in_dist[ok], fold[ok]), fold_avg(auroc, y[ok], cross[ok], fold[ok])
        out.append({"train": src, "test": tgt, "n_test": int(is_t.sum()), "auroc_in": a_in, "auroc_cross": a_cr,
                    "drop": a_in - a_cr})
    return pd.DataFrame(out)


def importance(d: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """Control 5: standardized LR coefficients (fit on all rows) and exact linear SHAP (mean |phi|)."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler

    cols = usable_columns(d, cols)
    X = d[cols].to_numpy(float)
    sc = StandardScaler().fit(X)
    Z = sc.transform(X)
    lr = LogisticRegression(C=1.0, max_iter=5000).fit(Z, d.label.to_numpy())
    phi = (Z - Z.mean(0)) * lr.coef_[0]
    return pd.DataFrame({"feature": cols, "coef_std": lr.coef_[0], "mean_abs_shap": np.abs(phi).mean(0)}) \
        .sort_values("mean_abs_shap", ascending=False).reset_index(drop=True)


def reliability(y, p, bins: int = 10) -> pd.DataFrame:
    q = pd.qcut(pd.Series(p), q=bins, duplicates="drop")
    t = pd.DataFrame({"y": y, "p": p, "bin": q}).groupby("bin", observed=True).agg(
        n=("y", "size"), mean_pred=("p", "mean"), observed=("y", "mean")).reset_index(drop=True)
    return t


def plot_reliability(tab: pd.DataFrame, name: str, path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(4.5, 4.5))
    ax.plot([0, 1], [0, 1], color="grey", lw=0.8, ls="--")
    ax.plot(tab.mean_pred, tab.observed, "o-")
    ax.set_xlabel("предсказанная P(заявление недостоверно)")
    ax.set_ylabel("наблюдаемая доля недостоверных")
    ax.set_title(f"Кривая надёжности: {name} (out-of-fold)", fontsize=9)
    lim = max(0.05, float(max(tab.mean_pred.max(), tab.observed.max())) + 0.05)
    ax.set_xlim(0, lim)
    ax.set_ylim(0, lim)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=130)
    plt.close(fig)


# ------------------------------------------------------------------------------------------ driver


def _fmt(x, nd=3) -> str:
    return "—" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.{nd}f}"


def run_e5(stage: str = "all", n_boot: int = 1000) -> dict:
    g = pd.read_csv(OUTPUTS / "g0_final_messages.csv", dtype={"run_id": str, "task_id": str})
    claim = g[g.has_claim]
    rows = build_rows(set(claim.run_id))
    df = pd.DataFrame([{k: v for k, v in r.items() if not k.startswith("_")} for r in rows])
    lab = dict(zip(claim.run_id, claim.label))
    bad = int((df.run_id.map(lab) != df.label).sum())
    if bad or len(df) != len(claim):
        raise ValueError(f"E5 population mismatch: {len(df)} rows vs {len(claim)} claims, {bad} label mismatches")
    gate = write_audit(df, REPORTS / "E5_segment_audit.md")
    log.info("E5 gate: %s", gate)
    if stage == "audit":
        return gate

    fold_of = load_folds(OUTPUTS / "folds.csv")
    df["fold"] = df.task_id.map(fold_of).astype(int)
    v = df.agent_id.str.extract(r"^mini-swe-agent-(\d+)\.")[0]
    df["agent_version"] = "v" + v + ".x"
    for c in ("has_any_verification", "verif_after_last_edit", "verified_before_first_edit"):
        df[c] = df[c].astype(int)
    h1 = {}
    if gate["tested"]:
        N, d = gate["N"], gate["d"]
        short = df[df.seg_len_sent < N]
        h1["excluded_short"] = {"reliable": int((short.label == 0).sum()), "unreliable": int((short.label == 1).sum())}
        df = df.merge(segment_chaos(rows, N, d, fold_of), on="run_id", how="left")
        df = df.merge(full_chaos(N, d, set(df.run_id)), on="run_id", how="left")
        seg_ok = df[CHAOS_SEG].notna().all(axis=1)
        full_missing = df[seg_ok & df[CHAOS_FULL].isna().any(axis=1)]
        h1["excluded_no_full"] = {"reliable": int((full_missing.label == 0).sum()),
                                  "unreliable": int((full_missing.label == 1).sum())}
        ok = df.dropna(subset=CHAOS_SEG)
        h1["spearman"] = {p: float(spearmanr(ok[f"H_seg_{p}"], ok.seg_len_sent).statistic) for p in PROJS}
        h1["spearman_steps"] = {p: float(spearmanr(ok[f"H_seg_{p}"], ok.seg_len_steps).statistic) for p in PROJS}
    if not gate["tested"]:
        # H_full / C_full columns of e5_features.csv: E4 primary combo (reason_sent, cos_step, N=200, d=4),
        # for reference only — no E5 model uses them when H1 is not tested.
        ref = full_chaos(200, 4, set(df.run_id))
        df = df.merge(ref[["run_id", "H_full_cos_step", "C_full_cos_step"]], on="run_id", how="left")
    for k in ("H", "C"):
        df[f"{k}_seg"] = df.get(f"{k}_seg_cos_step", np.nan)
        df[f"{k}_full"] = df.get(f"{k}_full_cos_step", np.nan)
    lead = ["run_id", "task_id", "model_id", "family", "label", "seg_len_steps", "seg_len_sent", "H_seg", "C_seg",
            "H_full", "C_full"]
    rest = [c for c in df.columns if c not in lead and c != "fold"]
    df[lead + rest].to_csv(FEATS_OUT, index=False)

    struct_len = STRUCT + [c for c in LEN if c not in STRUCT]
    res_all, info_all, d_all = evaluate_models(
        df, {"M_len": LEN, "M_struct": STRUCT, "M_struct+len": struct_len}, n_boot,
        extra_pairs=[("M_struct+len", "M_len")])
    res_all.insert(1, "population", "все заявившие")
    summary = [res_all]
    res_h1 = info_h1 = None
    if gate["tested"]:
        models_h1 = {"M_len": LEN, "M_chaosSeg": CHAOS_SEG, "M_chaosFull": CHAOS_FULL,
                     "M_chaosSeg+len": CHAOS_SEG + LEN, "M_struct+len": struct_len,
                     "M_all": STRUCT + CHAOS_SEG + [c for c in LEN if c not in STRUCT]}
        res_h1, info_h1, d_h1 = evaluate_models(df, models_h1, n_boot,
                                                extra_pairs=[("M_chaosSeg+len", "M_len"), ("M_all", "M_struct+len")])
        res_h1.insert(1, "population", f"сегмент ≥ {gate['N']}")
        summary.append(res_h1)
    summ = pd.concat(summary, ignore_index=True)
    summ.to_csv(OUTPUTS / "e5_summary.csv", index=False)

    # ---- Stage 4 controls
    wt = [within_task(info_all["data"], info_all["preds"], n_boot).assign(population="все заявившие")]
    if info_h1:
        wt.append(within_task(info_h1["data"], info_h1["preds"], n_boot).assign(population=f"сегмент ≥ {gate['N']}"))
    wt = pd.concat(wt, ignore_index=True)
    final_name, final_cols, final_info = ("M_all", models_h1["M_all"], info_h1) if info_h1 else \
        ("M_struct+len", struct_len, info_all)
    fd = final_info["data"]
    tr = []
    for name, cols, info in (("M_struct+len", struct_len, info_all), (final_name, final_cols, final_info)):
        dd = info["data"]
        t1 = transfer(dd, cols, "family").assign(axis="семейство моделей", model_name=name)
        vers = sorted(dd.agent_version.dropna().unique())
        t2 = transfer(dd, cols, "agent_version", [(a, b) for a in vers for b in vers if a != b]) \
            .assign(axis="версия агента", model_name=name)
        tr += [t1, t2]
        if name == final_name:
            break
    tr = pd.concat(tr, ignore_index=True).drop_duplicates(subset=["model_name", "axis", "train", "test"])
    tr.to_csv(OUTPUTS / "e5_transfer.csv", index=False)
    p_final = final_info["preds"][final_name]
    rel = reliability(fd.label.to_numpy(), p_final)
    plot_reliability(rel, final_name, REPORTS / "figures" / "e5_reliability.png")
    brier = float(np.mean((p_final - fd.label.to_numpy()) ** 2))
    brier_base = float(np.mean((fd.label.mean() - fd.label.to_numpy()) ** 2))
    imp = importance(fd, final_cols)
    imp.to_csv(OUTPUTS / "e5_importance.csv", index=False)

    out = {"gate": gate, "h1": h1, "summary": summ, "within": wt, "transfer": tr, "reliability": rel,
           "brier": brier, "brier_base": brier_base, "importance": imp, "final": final_name,
           "pairs_all": info_all["pairs"], "pairs_h1": info_h1["pairs"] if info_h1 else {}, "df": df}
    write_results(out, REPORTS / "E5_results.md", n_boot)
    return out


def _row(summ, pop, name):
    r = summ[(summ.population == pop) & (summ.model_name == name)]
    return r.iloc[0] if len(r) else None


def write_results(o: dict, path: Path, n_boot: int) -> None:
    s, gate = o["summary"], o["gate"]
    pop_all = "все заявившие"
    pop_h1 = f"сегмент ≥ {gate['N']}" if gate["tested"] else None
    ml, ms = _row(s, pop_all, "M_len"), _row(s, pop_all, "M_struct+len")
    mc = _row(s, pop_h1, "M_chaosSeg+len") if pop_h1 else None
    ci = lambda r: f"[{_fmt(r.auroc_ci_low)}; {_fmt(r.auroc_ci_high)}]"  # noqa: E731
    L = ["# E5 — достоверность утверждений агента", "",
         f"- **AUROC `M_len`** = {_fmt(ml.auroc)} {ci(ml)} (все заявившие, n = {ml.n})",
         (f"- **AUROC `M_chaosSeg+len`** = {_fmt(mc.auroc)} {ci(mc)} ({pop_h1}, n = {mc.n})" if mc is not None else
          "- **AUROC `M_chaosSeg+len`** — не считался: шлюз этапа 1 не пройден (см. `E5_segment_audit.md`)"),
         f"- **AUROC `M_struct+len`** = {_fmt(ms.auroc)} {ci(ms)} (все заявившие, n = {ms.n})", "",
         "Метка: 1 — заявление недостоверно (задача провалена по тестам SWE-bench); AUROC — способность найти "
         "недостоверные заявления. Логистическая регрессия (L2, C=1, стандартизация), 5 фолдов по task_id "
         "(`outputs/folds.csv`), AUROC и PR-AUC усреднены по фолдам, Бриер — по объединённым out-of-fold "
         f"вероятностям; 95% ДИ — бутстрэп по задачам внутри фолдов, {n_boot} повторов, парный между моделями, seed 42. "
         "Эталонный патч не используется.", ""]
    # criteria
    L += ["## Критерии", ""]
    if gate["tested"]:
        dlt, lo, hi = o["pairs_h1"][("M_chaosSeg+len", "M_len")]
        L.append(f"- **H1** (нижняя граница 95% ДИ AUROC(`M_chaosSeg+len`) − AUROC(`M_len`) > 0), популяция «{pop_h1}»: "
                 f"Δ = {_fmt(dlt)} [{_fmt(lo)}; {_fmt(hi)}] → " + ("**выполнен**" if lo > 0 else "**не выполнен**") + ".")
    else:
        L.append("- **H1** — не проверялась: медиана длины сегмента после последней правки "
                 f"{gate['median_seg_len_sent']:.0f} предложений < 50; аппарат неприменим к объекту такой длины (этап 1).")
    dlt, lo, hi = o["pairs_all"][("M_struct+len", "M_len")]
    h2 = ms.auroc >= 0.65 and ms.auroc_ci_low > ml.auroc
    L.append(f"- **H2** (AUROC(`M_struct+len`) ≥ 0.65 и нижняя граница его ДИ выше AUROC(`M_len`)): AUROC = "
             f"{_fmt(ms.auroc)}, нижняя граница {_fmt(ms.auroc_ci_low)}, AUROC(`M_len`) = {_fmt(ml.auroc)} → "
             + ("**выполнен**" if h2 else "**не выполнен**") + f". Для справки: Δ(`M_struct+len` − `M_len`) = "
             f"{_fmt(dlt)} [{_fmt(lo)}; {_fmt(hi)}].")
    if gate["tested"]:
        dlt, lo, hi = o["pairs_h1"][("M_all", "M_struct+len")]
        L.append(f"- **Вклад хаотичности** (нижняя граница ДИ AUROC(`M_all`) − AUROC(`M_struct+len`) > 0), «{pop_h1}»: "
                 f"Δ = {_fmt(dlt)} [{_fmt(lo)}; {_fmt(hi)}] → " + ("**выполнен**" if lo > 0 else "**не выполнен**") + ".")
    else:
        L.append("- **Вклад хаотичности** — не оценивался (H1 не проверялась).")
    L += ["- Уже измерено ранее (по постановке, не пересчитывалось): H и C по всей траектории на этой подпопуляции — "
          "AUROC 0.528; `verif_after_last_edit` поодиночке — 0.555; `n_verifications` — 0.558.", ""]
    # models table
    L += ["## Модели", "", "| популяция | модель | n | AUROC [95% ДИ] | PR-AUC | Бриер | Δ к `M_len` [95% ДИ] |",
          "|---|---|---|---|---|---|---|"]
    for _, r in s.iterrows():
        L.append(f"| {r.population} | `{r.model_name}` | {r.n} | {_fmt(r.auroc)} [{_fmt(r.auroc_ci_low)}; "
                 f"{_fmt(r.auroc_ci_high)}] | {_fmt(r.pr_auc)} | {_fmt(r.brier)} | {_fmt(r.delta_vs_len)} "
                 f"[{_fmt(r.delta_ci_low)}; {_fmt(r.delta_ci_high)}] |")
    df = o["df"]
    L += ["", f"Доля недостоверных заявлений (база PR-AUC): {df.label.mean():.3f}.", "",
          "Состав моделей: `M_len` = " + ", ".join(LEN) + "; `M_struct` = " + ", ".join(STRUCT) +
          "; `M_chaosSeg` = H, C сегмента по проекциям cos_step, cos_task, pca1; `M_chaosFull` = H, C E3 (первые N "
          "фрагментов всей траектории) по тем же проекциям; `M_all` = структурные + H, C сегмента + длина.", ""]
    if gate["tested"]:
        h1 = o["h1"]
        L += ["## H1: детали", "",
              f"- Окно: первые N = {gate['N']} фрагментов сегмента, d = {gate['d']}" +
              (" (**оценка H смещена**: короткое окно)." if gate["biased"] else "."),
              f"- Исключены (сегмент короче окна): достоверных {h1['excluded_short']['reliable']}, недостоверных "
              f"{h1['excluded_short']['unreliable']}; без признаков всей траектории (траектория короче N): достоверных "
              f"{h1['excluded_no_full']['reliable']}, недостоверных {h1['excluded_no_full']['unreliable']}.",
              "- Спирмен H сегмента с длиной сегмента (предложения / шаги): " +
              "; ".join(f"{p}: {h1['spearman'][p]:.2f} / {h1['spearman_steps'][p]:.2f}" for p in PROJS) + ".", ""]
    # structural descriptive
    L += ["## Структурные признаки: средние по классам (все заявившие)", "",
          "| признак | достоверные | недостоверные | AUROC признака в одиночку |", "|---|---|---|---|"]
    for c in LEN + STRUCT:
        if c in df:
            a = auroc(df.label.to_numpy(), df[c].astype(float).to_numpy())
            L.append(f"| {c} | {df[df.label == 0][c].mean():.3f} | {df[df.label == 1][c].mean():.3f} | {_fmt(a)} |")
    L += ["", "AUROC признака в одиночку — по всей подпопуляции без CV (описательно; > 0.5 — выше у недостоверных).", ""]
    # controls
    wt = o["within"]
    L += ["## Контроль 1: внутри задач", "",
          f"Задач, где есть и достоверные, и недостоверные заявления: {int(wt.n_tasks.iloc[0])} (все заявившие). "
          "AUROC out-of-fold предсказаний считается внутри каждой такой задачи и усредняется по задачам "
          "(сложность задачи при этом общая для сравниваемых прогонов).", "",
          "| популяция | модель | задач | AUROC внутри задач [95% ДИ] |", "|---|---|---|---|"]
    for _, r in wt.iterrows():
        L.append(f"| {r.population} | `{r.model_name}` | {r.n_tasks} | {_fmt(r.within_task_auroc)} [{_fmt(r.ci_low)}; {_fmt(r.ci_high)}] |")
    tr = o["transfer"]
    L += ["", "## Контроли 2–3: перенос между семействами моделей и версиями агента", "",
          "Обучение на других группах и других фолдах задач, тест на целевой группе; сравнение с AUROC внутри "
          "распределения на тех же строках. Падение = in − cross.", "",
          "| модель | ось | обучение | тест | n | AUROC in | AUROC cross | падение |", "|---|---|---|---|---|---|---|---|"]
    for _, r in tr.iterrows():
        L.append(f"| `{r.model_name}` | {r.axis} | {r.train} | {r.test} | {r.n_test} | {_fmt(r.auroc_in)} | "
                 f"{_fmt(r.auroc_cross)} | {_fmt(r['drop'])} |")
    L += ["", f"Медианное падение: {_fmt(tr['drop'].median())}.", "",
          f"## Контроль 4: калибровка итоговой модели (`{o['final']}`)", "",
          f"- Бриер: {o['brier']:.4f} (константная модель с долей недостоверных: {o['brier_base']:.4f}).", "",
          "![кривая надёжности](figures/e5_reliability.png)", "",
          "| бин | n | средняя предсказанная | наблюдаемая доля |", "|---|---|---|---|"]
    for i, r in o["reliability"].iterrows():
        L.append(f"| {i + 1} | {int(r.n)} | {r.mean_pred:.3f} | {r.observed:.3f} |")
    L += ["", f"## Контроль 5: важность признаков (`{o['final']}`)", ""]
    if not gate["tested"]:
        L += ["H1 не проверялась, поэтому `M_all` (структурные + H, C сегмента + длина) совпадает с `M_struct+len`: "
              "важность и калибровка посчитаны для неё. Столбцы `H_full`/`C_full` в `outputs/e5_features.csv` — "
              "основная комбинация E4 (reason_sent, cos_step, N = 200, d = 4), только справочно; `H_seg`/`C_seg` пусты.", ""]
    L += [
          "Коэффициенты логистической регрессии на стандартизованных признаках (обучение на всех строках) и точные "
          "значения Шепли для линейной модели (среднее |φ|; > 0 коэффициента — выше риск недостоверного заявления).", "",
          "| признак | коэффициент | среднее \\|SHAP\\| |", "|---|---|---|"]
    for _, r in o["importance"].iterrows():
        L.append(f"| {r.feature} | {r.coef_std:+.3f} | {r.mean_abs_shap:.3f} |")
    L += ["", "## Ограничения", "",
          "- Подпопуляция задана паттернами G0 (явные формулировки успеха); по ручной проверке G0 часть заявлений "
          "сформулирована иначе и сюда не попала.",
          "- Разметка шагов (правка/верификация) — регулярные выражения над командами bash (правила G0); "
          "«верификация» означает запуск проверки, а не её прохождение.",
          "- Признаки считаются по всей траектории: это классификация заявления в момент сдачи, а не ранний прогноз.",
          "- Один бенчмарк, один каркас агента, 16 моделей с видимыми рассуждениями (отбор E0).", ""]
    path.write_text("\n".join(L) + "\n", encoding="utf-8")
