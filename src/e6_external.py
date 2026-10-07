"""E6 block 4: external validation of the claim-reliability model in other domains (rules: src/e6_refine.py
docstring, "Block 4 rules"; data: src/adapters/exgentic.py).

One generic feature function turns per-step events into the main-set features; SWE-bench step events (bash
commands, G0 rules) and Exgentic step events (tool calls, the block-4 mapping) both go through it, and on SWE-bench
it must reproduce outputs/e6_features.csv exactly before it is used on the new domains.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

from src import e5_claims as e5
from src import e6_refine as e6
from src import g0_false_success as g0
from src.common import CONFIG, INTERIM, OUTPUTS, REPORTS, SEED, get_logger, load_leak_markers, read_jsonl_gz, \
    trajectories_path
from src.folds import make_folds
from src.segment import compile_markers, segment
from src.stats import auroc, fit_predict, fold_avg, oof, usable_columns

log = get_logger("e6x")

CLAIM_MIN = 0.05
SWE_FALSE_SUCCESS = 0.369  # G0 headline: share of has_claim among failed SWE-bench trajectories
DOMAINS = {"appworld": "AppWorld", "tau2": "τ²-bench"}
FEATS_OUT = OUTPUTS / "e6_external_features.csv"

# ------------------------------------------------------------------------------------------ generic features


def struct_features(ev: list[list[tuple[str, str]]], sig: list[str | None], objs: list[set[str]],
                    err: list[list[bool]], n_outputs: int, n_err_outputs: int, frag_steps: list[int]) -> dict:
    """Length + main-set structural features from per-step data (definitions of e5.process_record and
    e6_refine.block1_record): ev[i] = ordered (kind, target) events of assistant step i (kind edit | verify),
    sig[i] = normalized action signature (None = no action), objs[i] = files / objects the step mentions,
    err[i] = error flags of the step's tool outputs, frag_steps = step index of every E1 reason_sent fragment."""
    n = len(ev)
    events = [(i, k, t) for i, e in enumerate(ev) for k, t in e]
    edit_steps = sorted({i for i, k, _ in events if k == "edit"})
    verif_steps = sorted({i for i, k, _ in events if k == "verify"})
    kinds = [k for _, k, _ in events]
    last_edit_ev = max((j for j, k in enumerate(kinds) if k == "edit"), default=None)
    last_edit_step = edit_steps[-1] if edit_steps else -1
    phases = [e6._phase({k for k, _ in e}) for e in ev]
    bigrams = list(zip(phases, phases[1:]))
    if bigrams:
        _, cnt = np.unique(np.array([a + ">" + b for a, b in bigrams]), return_counts=True)
        p = cnt / cnt.sum()
        bigram_h = float(-(p * np.log2(p)).sum() / math.log2(9))
    else:
        bigram_h = 0.0
    targets = [t for _, k, t in events if k == "edit" and t != "?"]
    after = kinds[last_edit_ev + 1:] if last_edit_ev is not None else kinds
    sigs = [s for s in sig if s is not None]
    na = len(sigs)
    run = best = 0
    for i, s in enumerate(sigs):
        run = run + 1 if i and s == sigs[i - 1] else 1
        best = max(best, run)
    seen: set[str] = set()
    revisits = 0
    for o in objs:
        revisits += bool(o & seen)
        seen |= o
    prun = pbest = 0
    for i, ph in enumerate(phases):
        prun = prun + 1 if i and ph == phases[i - 1] else 1
        pbest = max(pbest, prun)
    return {
        "n_steps": n, "seg_len_steps": n - (last_edit_step + 1),
        "seg_len_sent": sum(1 for s in frag_steps if s > last_edit_step), "n_edits": len(edit_steps),
        "has_any_verification": int(bool(verif_steps)), "n_verifications": len(verif_steps),
        "verif_after_last_edit": int(("verify" in after) if last_edit_ev is not None else bool(verif_steps)),
        "n_verif_after_last_edit": after.count("verify"),
        "share_edit": phases.count("edit") / max(1, n), "share_verify": phases.count("verify") / max(1, n),
        "phase_bigram_entropy": bigram_h,
        "share_before_first_edit": (edit_steps[0] / max(1, n)) if edit_steps else 1.0,
        "nonzero_rc_share": n_err_outputs / max(1, n_outputs),
        "n_file_switches": sum(1 for a, b in zip(targets, targets[1:]) if a != b),
        "repeat_in_window_10": sum(sigs[i] in sigs[max(0, i - 10):i] for i in range(na)) / na if na else 0.0,
        "max_run_length": best, "distinct_ratio": len(set(sigs)) / max(1, n), "revisit_ratio": revisits / max(1, n),
        "verify_fail_share": (sum(any(err[i]) for i in verif_steps) / len(verif_steps)) if verif_steps else 0.0,
        "max_phase_run": pbest,
    }


def _outputs_by_step(rec: dict, asst: list[dict], is_err) -> tuple[list[list[bool]], int, int]:
    pos = {s["step_idx"]: i for i, s in enumerate(asst)}
    err = [[] for _ in asst]
    tools = [s for s in rec["steps"] if s["role"] == "tool"]
    for t in tools:
        if t["step_idx"] in pos:
            err[pos[t["step_idx"]]].append(is_err(t))
    return err, len(tools), sum(is_err(t) for t in tools)


def swe_inputs(rec: dict, marker_re) -> dict:
    """Generic inputs from a SWE-bench record (bash commands, G0 / E5 / block-1 rules)."""
    asst = [s for s in rec["steps"] if s["role"] == "assistant"]
    files: dict[str, str] = {}
    ev, sig, objs = [], [], []
    for s in asst:
        cmds = s.get("tool_calls") or []
        ev.append([kt for c in cmds for kt in e5.command_events(c, files)])
        sig.append(" ;; ".join(e6.norm_command(c) for c in cmds) if cmds else None)
        objs.append(set().union(*(e6.command_files(c) for c in cmds)) if cmds else set())
    err, n_out, n_err = _outputs_by_step(rec, asst, lambda t: t["text"].startswith("returncode: "))
    pos = {s["step_idx"]: i for i, s in enumerate(asst)}
    frag_steps = [pos.get(f.step_idx, -1) for f in segment(rec, "reason_sent", marker_re)]
    return {"ev": ev, "sig": sig, "objs": objs, "err": err, "n_outputs": n_out, "n_err_outputs": n_err,
            "frag_steps": frag_steps}


# ------------------------------------------------------------------------------------------ new-domain mapping

OTHER_TOOLS = {"message", "transfer_to_human_agents", "finish", "AskUserQuestion", "TodoWrite", "Task", "TaskOutput",
               "Bash", "Glob", "Grep", "Read", "ListMcpResourcesTool", "calculate", "think"}
_AUTH_RE = re.compile(r"^(?:login|logout|signup)$|password|verification_code")
_COMPLETION_RE = re.compile(r"complete_task|fail_task|task_completed|task_completion")
_READ_RE = re.compile(r"^(?:show_|search_|get_|list_|find_|check_|can_|is_)|^(?:file_exists|directory_exists|"
                      r"run_speed_test)$")
_ENTITY_KEYS = ("order_id", "reservation_id", "user_id", "customer_id", "line_id")
_SKIP_KEY_RE = re.compile(r"token|password|content|message|body", re.I)
_KWARG_RE = re.compile(r"(\w+)\s*=\s*(?:'([^']{3,100})'|\"([^\"]{3,100})\"|(\d{3,}))")


def tool_kind(bench: str, name: str) -> tuple[str | None, str]:
    """('read' | 'edit' | None, app) of one tool call (block-4 mapping)."""
    if name in OTHER_TOOLS:
        return None, ""
    if bench == "appworld":
        if "__" not in name:
            return None, ""
        app, act = name.split("__", 1)
    else:
        app, act = bench, name
    if _AUTH_RE.search(act) or _COMPLETION_RE.search(act):
        return None, app
    return ("read" if _READ_RE.search(act) else "edit"), app


def _args(call: dict) -> dict:
    a = call.get("args")
    if not isinstance(a, dict):
        return {}
    raw = a.get("_code") or a.get("_raw")
    if isinstance(raw, str):  # smolagents code / unparsed arguments: keyword literals
        return {m.group(1): m.group(2) or m.group(3) or m.group(4) for m in _KWARG_RE.finditer(raw)}
    return a


def call_objects(call: dict) -> set[str]:
    out = set()
    for k, v in _args(call).items():
        if _SKIP_KEY_RE.search(str(k)) or isinstance(v, bool) or not isinstance(v, (str, int, float)):
            continue
        s = str(v).strip().lower()
        if 3 <= len(s) <= 100:
            out.add(s)
    return out


def domain_inputs(rec: dict, marker_re) -> dict:
    bench = rec["benchmark"]
    asst = [s for s in rec["steps"] if s["role"] == "assistant"]
    edited_apps: set[str] = set()
    ev, sig, objs = [], [], []
    for s in asst:
        e, o = [], set()
        for c in s.get("tool_calls") or []:
            kind, app = tool_kind(bench, c["name"])
            if kind == "edit":
                a = _args(c)
                if bench == "appworld":
                    target = app
                else:
                    target = next((str(a[k]).lower() for k in _ENTITY_KEYS if a.get(k)), c["name"])
                e.append(("edit", target))
                edited_apps.add(app)
            elif kind == "read" and app in edited_apps:
                e.append(("verify", ""))
            o |= call_objects(c)
        ev.append(e)
        calls = s.get("tool_calls") or []
        sig.append(" ;; ".join(e6.norm_command(c["name"] + json.dumps(c.get("args"), sort_keys=True, default=str))
                               for c in calls) if calls else None)
        objs.append(o)
    err, n_out, n_err = _outputs_by_step(rec, asst, lambda t: bool(t.get("error")))
    pos = {s["step_idx"]: i for i, s in enumerate(asst)}
    frag_steps = [pos.get(f.step_idx, -1) for f in segment(rec, "reason_sent", marker_re)]
    return {"ev": ev, "sig": sig, "objs": objs, "err": err, "n_outputs": n_out, "n_err_outputs": n_err,
            "frag_steps": frag_steps}


# ------------------------------------------------------------------------------------------ claims


def final_claim(rec: dict, claim, admit, claim_dom) -> dict:
    asst = [s for s in rec["steps"] if s["role"] == "assistant"]
    texts = [{"full": g0.clean_nl(s.get("nl") or "")} for s in asst]
    idx, text = g0.pick_final(texts, "full")
    if idx is None:
        return {"has_final": False, "has_claim": False, "has_admit": False, "has_claim_dom": False, "final_text": ""}
    c, a, cd = g0.match_any(claim, text), g0.match_any(admit, text), g0.match_any(claim_dom, text)
    return {"has_final": True, "has_claim": bool(c), "has_admit": bool(a), "has_claim_dom": bool(c or cd),
            "claims_hit": " | ".join(c), "claims_dom_hit": " | ".join(cd), "final_text": text[:2000]}


# ------------------------------------------------------------------------------------------ build


def build_external() -> pd.DataFrame:
    mre = compile_markers(load_leak_markers())
    claim = g0.load_patterns(CONFIG / "claim_patterns.txt")
    admit = g0.load_patterns(CONFIG / "admit_patterns.txt")
    claim_dom = g0.load_patterns(CONFIG / "claim_patterns_domain.txt")
    rows = []
    for rec in read_jsonl_gz(INTERIM / "exgentic" / "trajectories.jsonl.gz"):
        f = struct_features(**domain_inputs(rec, mre))
        rows.append({"run_id": rec["run_id"], "task_id": rec["task_id"], "task_id_args": rec["task_id_args"],
                     "benchmark": rec["benchmark"],
                     "domain": "appworld" if rec["benchmark"] == "appworld" else "tau2",
                     "harness": rec["agent_id"], "model_id": rec["model_id"], "family": rec["model_family"],
                     "label": rec["label"], "status": rec["meta"]["status"], **final_claim(rec, claim, admit, claim_dom),
                     **f})
    return pd.DataFrame(rows)


def check_swe_equivalence(cols: list[str]) -> dict:
    """The generic function must reproduce the SWE-bench features of outputs/e6_features.csv."""
    ref = pd.read_csv(e6.FEATS_OUT, dtype={"run_id": str}).set_index("run_id")
    mre = compile_markers(load_leak_markers())
    worst = {c: 0.0 for c in cols}
    n = 0
    for rec in read_jsonl_gz(trajectories_path("swebench_bashonly")):
        if rec["run_id"] not in ref.index:
            continue
        f = struct_features(**swe_inputs(rec, mre))
        r = ref.loc[rec["run_id"]]
        for c in cols:
            worst[c] = max(worst[c], abs(float(f[c]) - float(r[c])))
        n += 1
    return {"n": n, "max_abs_diff": worst}


# ------------------------------------------------------------------------------------------ models


def _boot_auroc(d: pd.DataFrame, preds: dict[str, np.ndarray], n_boot: int, seed: int = SEED) -> dict:
    return e5._fold_auroc_boot(d.label.to_numpy(), preds, d.task_id.to_numpy(), d.fold.to_numpy(), n_boot, seed)


def evaluate_domain(swe: pd.DataFrame, dom: pd.DataFrame, cols: list[str], n_boot: int) -> dict:
    """Transfer (fit on all SWE claims) and in-domain (5-fold GroupKFold) AUROC on the same folds."""
    folds = make_folds(dom.task_id)
    d = dom.merge(folds, on="task_id").reset_index(drop=True)
    y, fold = d.label.to_numpy(), d.fold.to_numpy()
    n1, n0 = int(y.sum()), int((1 - y).sum())
    out = {"n": len(d), "n_unreliable": n1, "n_reliable": n0, "n_groups": d.task_id.nunique(), "rows": []}
    if min(n1, n0) < 20:
        out["skipped"] = "fewer than 20 claims in a class"
        return out
    preds, names = {}, []
    for tag, c in (("M_len", e5.LEN), ("M_main", cols)):
        preds[f"transfer_{tag}"] = fit_predict(swe, d, c)
        names.append(f"transfer_{tag}")
        if min(n1, n0) >= 50:
            dc = d.copy()
            preds[f"indomain_{tag}"] = oof(dc, usable_columns(dc, c))
            names.append(f"indomain_{tag}")
    boots = _boot_auroc(d, preds, n_boot)
    for m in names:
        lo, hi = np.nanpercentile(boots[m], [2.5, 97.5])
        out["rows"].append({"model_name": m, "auroc": fold_avg(auroc, y, preds[m], fold), "auroc_ci_low": lo,
                            "auroc_ci_high": hi, "auroc_pooled": auroc(y, preds[m])})
    pairs = {}
    for a, b in (("transfer_M_main", "transfer_M_len"), ("indomain_M_main", "indomain_M_len"),
                 ("indomain_M_main", "transfer_M_main")):
        if a in boots and b in boots:
            dd = boots[a] - boots[b]
            pairs[(a, b)] = (fold_avg(auroc, y, preds[a], fold) - fold_avg(auroc, y, preds[b], fold),
                             np.nanpercentile(dd, 2.5), np.nanpercentile(dd, 97.5))
    out["pairs"] = pairs
    if min(n1, n0) >= 50:
        out["importance"] = e5.importance(d, usable_columns(d, cols))
        if (dom.task_id_args != dom.task_id).any():  # sensitivity: the first (pre-registered) grouping
            ds = dom.drop(columns=["task_id"]).rename(columns={"task_id_args": "task_id"})
            ds = ds.merge(make_folds(ds.task_id), on="task_id").reset_index(drop=True)
            sp = {f"indomain_{tag}": oof(ds, usable_columns(ds, c)) for tag, c in (("M_len", e5.LEN), ("M_main", cols))}
            sb = _boot_auroc(ds, sp, n_boot)
            out["sens_args"] = {m: (fold_avg(auroc, ds.label.to_numpy(), p, ds.fold.to_numpy()),
                                    *np.nanpercentile(sb[m], [2.5, 97.5])) for m, p in sp.items()}
    out["single"] = {c: auroc(y, d[c].to_numpy(float)) for c in cols}
    if d.benchmark.nunique() > 1:  # descriptive: transfer AUROC per benchmark (no CV needed for transfer)
        out["per_bench"] = [{"benchmark": b, "n": int(m.sum()), "n_unreliable": int(y[m].sum()),
                             **{k: auroc(y[m], preds[k][m]) for k in ("transfer_M_len", "transfer_M_main")}}
                            for b in sorted(d.benchmark.unique()) for m in [(d.benchmark == b).to_numpy()]]
    out["data"] = d
    return out


# ------------------------------------------------------------------------------------------ driver


def run_block4(n_boot: int = 1000) -> dict:
    sel = json.loads(e6.SELECTION_OUT.read_text(encoding="utf-8"))
    cols = sel["main_cols"]
    eq = check_swe_equivalence(e5.LEN + [c for c in cols if c not in e5.LEN])
    bad = {c: v for c, v in eq["max_abs_diff"].items() if v > 1e-9}
    log.info("SWE equivalence: %d trajectories, mismatches %s", eq["n"], bad)
    if bad:
        raise AssertionError(f"generic features differ from SWE-bench values: {bad}")
    ext = build_external()
    ext.drop(columns=["final_text"]).to_csv(FEATS_OUT, index=False)
    swe = e6.load_features()

    appl = applicability(ext)
    res = {}
    for dom in ("appworld", "tau2"):
        a = appl[appl.domain == dom].iloc[0]
        col = "has_claim" if a.share_claim_failed >= CLAIM_MIN else "has_claim_dom"
        pop = ext[(ext.domain == dom) & ext[col]].copy()
        r = evaluate_domain(swe, pop, cols, n_boot)
        r["claim_rule"] = "G0" if col == "has_claim" else "G0 + доменная адаптация"
        res[dom] = r
        if dom == "tau2":  # post-hoc: telecom tasks cannot be grouped (one customer) -> airline + retail only
            ar = pop[pop.benchmark.isin(["tau2_airline", "tau2_retail"])]
            res["tau2_ar"] = evaluate_domain(swe, ar, cols, n_boot)
    swe_imp = e5.importance(swe, cols)
    out = {"eq": eq, "ext": ext, "appl": appl, "res": res, "swe_imp": swe_imp, "cols": cols, "n_boot": n_boot,
           "phase": phase_profile(ext, swe), "swe": swe}
    summ = pd.read_csv(OUTPUTS / "e6_summary.csv")
    summ = summ[summ.block != 4]
    add = [{"block": 4, "model_name": f"{dom}:{r_['model_name']}", "n": res[dom]["n"], "auroc": r_["auroc"],
            "auroc_ci_low": r_["auroc_ci_low"], "auroc_ci_high": r_["auroc_ci_high"]}
           for dom in res for r_ in res[dom]["rows"]]
    pd.concat([summ, pd.DataFrame(add)], ignore_index=True).to_csv(OUTPUTS / "e6_summary.csv", index=False)
    write_external(out, REPORTS / "E6_external.md")
    return out


def applicability(ext: pd.DataFrame) -> pd.DataFrame:
    """Claim shares by domain / benchmark; the first row is SWE-bench from the G0 output for comparison.
    share_claim_* = has_claim (the population rule); share_fs_failed = claim and no admission (the G0 headline)."""
    g0df = pd.read_csv(OUTPUTS / "g0_final_messages.csv")
    g0df["has_final"] = g0df.cls != "NO_FINAL_MESSAGE"
    g0df["has_claim_dom"] = np.nan
    rows = []
    for key, g in [("swebench (G0)", g0df)] + list(ext.groupby("domain")) + list(ext.groupby("benchmark")):
        fin = g[g.has_final]
        fail, ok = fin[fin.label == 1], fin[fin.label == 0]
        rows.append({"domain": key, "n": len(g), "n_final": len(fin), "fail_rate": g.label.mean(),
                     "share_claim_failed": fail.has_claim.mean(), "share_claim_success": ok.has_claim.mean(),
                     "share_fs_failed": (fail.has_claim & ~fail.has_admit.astype(bool)).mean(),
                     "share_admit_failed": fail.has_admit.mean(),
                     "share_claim_dom_failed": fail.has_claim_dom.astype(float).mean(),
                     "share_claim_dom_success": ok.has_claim_dom.astype(float).mean(),
                     "n_claims": int(fin.has_claim.sum()),
                     "n_claims_dom": int(fin.has_claim_dom.fillna(False).astype(bool).sum())})
    return pd.DataFrame(rows).drop_duplicates("domain")


def phase_profile(ext: pd.DataFrame, swe: pd.DataFrame) -> pd.DataFrame:
    cols = ["n_steps", "n_edits", "share_edit", "share_verify", "has_any_verification", "phase_bigram_entropy",
            "nonzero_rc_share", "seg_len_sent"]
    parts = [swe[cols].mean().rename("SWE-bench (заявления)")]
    for dom, name in DOMAINS.items():
        parts.append(ext[ext.domain == dom][cols].mean().rename(f"{name} (все)"))
    return pd.concat(parts, axis=1)


def _f(x, nd=3):
    return "—" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.{nd}f}"


MODE_NAMES = {"transfer_M_len": "перенос, только длина", "transfer_M_main": "перенос, основная модель E6",
              "indomain_M_len": "обучение на домене, только длина", "indomain_M_main": "обучение на домене, основной набор"}


def write_external(o: dict, path: Path) -> None:
    ext, appl, res = o["ext"], o["appl"].set_index("domain"), o["res"]
    excl = pd.read_csv(INTERIM / "exgentic" / "excluded.csv")
    L = ["# E6 — блок 4: внешняя валидация", "",
         "Правила зафиксированы до расчёта (docstring `src/e6_refine.py`, «Block 4 rules»; адаптер "
         "`src/adapters/exgentic.py`; коммит 80f2cfb).", "",
         "## Источник", "",
         "**Exgentic/agent-llm-traces-v2** (Hugging Face, ревизия 4b8ad4a): трассы 5 каркасов агентов × 5 моделей "
         "(DeepSeek-V3.2, Kimi-K2.5, claude-opus-4-5, gemini-3-pro-preview, gpt-5.2). Использованы домены:",
         "- **AppWorld** (персональный ассистент через API приложений; метка — юнит-тесты AppWorld по итоговому "
         "состоянию базы);",
         "- **τ²-bench** airline / retail / telecom (поддержка клиентов с симулированным пользователем; метка — "
         "проверки среды τ²).", "",
         "Требования: метка от среды — да (поле `success`); идентификатор задачи — **восстановлен**: в AppWorld по "
         "тексту поручения (точно), в τ²-bench задача агенту не видна, поэтому группа — клиент (сессии связаны "
         "общими идентификаторами клиента в аргументах вызовов; группа грубее задачи); текст шагов — да; модель — "
         "да. Не использованы: swebench (домен обучения), browsecompplus (метка от LLM-судьи). Полный корпус "
         "Exgentic/traces-v2 с идентификаторами задач закрыт (HTTP 404).", "",
         "Исключено (по классам):", "", "| бенчмарк | причина | успех | провал |", "|---|---|---|---|"]
    for (b, r), g in excl.groupby(["benchmark", "reason"]):
        L.append(f"| {b} | {r} | {int((g.label == 0).sum())} | {int((g.label == 1).sum())} |")
    L += ["", "| домен | траекторий | доля провалов | групп задач | из них без идентификатора (1 сессия) | "
          "крупнейшая группа | первая группировка: групп / без идентификатора |", "|---|---|---|---|---|---|---|"]
    for b, g in ext.groupby("benchmark"):
        L.append(f"| {b} | {len(g)} | {g.label.mean():.3f} | {g.task_id.nunique()} | "
                 f"{g.task_id.str.contains('/noid/').sum()} | {g.task_id.value_counts().max()} | "
                 f"{g.task_id_args.nunique()} / {g.task_id_args.str.contains('/noid/').sum()} |")
    L += ["", "Группы τ²-bench исправлены после первого прогона блока 4: в нём 2171 из 4647 сессий не получили группы "
          "(все сессии smolagents_code — их аргументы записаны в коде — и сессии, оборвавшиеся на первой реплике). "
          "Теперь идентификаторы клиента берутся также из аргументов в коде и из реплик клиента и выводов инструментов. "
          "Исправление делает группы грубее (строже к утечке); результат с первой группировкой приведён ниже для "
          "сравнения."]
    eq = o["eq"]
    L += ["", "## Проверка общей функции признаков на SWE-bench", "",
          f"Признаки нового домена считаются той же функцией, что и SWE-признаки, по событиям шагов. На {eq['n']} "
          "траекториях SWE-bench она воспроизводит `outputs/e6_features.csv`: максимальное расхождение по всем "
          f"{len(eq['max_abs_diff'])} признакам — {max(eq['max_abs_diff'].values()):.1e}.", "",
          "## Шаг 1: применимость правил заявлений", "",
          "Правила G0 без изменений (финальное сообщение на естественном языке, те же паттерны). Порог применимости, "
          f"зафиксированный заранее: доля заявлений среди провальных ≥ {CLAIM_MIN:.0%}.", "",
          "| домен | траекторий | с финальным сообщением | доля провалов | заявление среди провальных | заявление без "
          "признания среди провальных (метрика G0) | заявление среди успешных | признание среди провальных | с доменной "
          "адаптацией: провальные / успешные |", "|---|---|---|---|---|---|---|---|---|"]
    for k in ["swebench (G0)", "appworld", "tau2", "tau2_airline", "tau2_retail", "tau2_telecom"]:
        if k in appl.index:
            a = appl.loc[k]
            dom_cell = "—" if math.isnan(a.share_claim_dom_failed) else \
                f"{a.share_claim_dom_failed:.3f} / {a.share_claim_dom_success:.3f}"
            L.append(f"| {DOMAINS.get(k, k)} | {int(a.n)} | {int(a.n_final)} | {a.fail_rate:.3f} | "
                     f"{a.share_claim_failed:.3f} | {a.share_fs_failed:.3f} | {a.share_claim_success:.3f} | "
                     f"{a.share_admit_failed:.3f} | {dom_cell} |")
    L += ["", f"Для SWE-bench заголовочная цифра G0 — {SWE_FALSE_SUCCESS:.1%} (заявление без признания среди провальных).",
          ""]
    for dom in ("appworld", "tau2"):
        a = appl.loc[dom]
        L.append(f"- {DOMAINS[dom]}: " + (
            f"правила G0 переносятся (доля {a.share_claim_failed:.3f} ≥ {CLAIM_MIN:.2f}); популяция — заявления по G0."
            if a.share_claim_failed >= CLAIM_MIN else
            f"правила G0 **не переносятся** (доля {a.share_claim_failed:.3f} < {CLAIM_MIN:.2f}); популяция — заявления "
            "по G0 + заранее зафиксированной доменной адаптации (`config/claim_patterns_domain.txt`)."))
    # phase profile
    pp = o["phase"]
    L += ["", "## Шаг 2: фазовая разметка в новом домене", "",
          "Те же три фазы, что в SWE-bench (правка > проверка > прочее). Правка — вызов, меняющий состояние среды; "
          "проверка — чтение состояния приложения, которое уже правилось (повторное чтение изменённого); прочее — "
          "исследование до правок, авторизация, сообщения пользователю, служебные инструменты каркаса. Задание "
          "упоминает «четыре фазы»; в пайплайне их три (edit / verify / other), отображение сохраняет именно их.", "",
          "| признак (среднее) | " + " | ".join(pp.columns) + " |", "|---|" + "---|" * len(pp.columns)]
    for f, r in pp.iterrows():
        L.append(f"| `{f}` | " + " | ".join(_f(v, 3) for v in r) + " |")
    # models
    L += ["", "## Шаг 3: качество на новом домене", "",
          "Популяция — траектории с заявлением об успехе; метка 1 — заявление недостоверно (среда: задача провалена). "
          "Перенос — логистическая регрессия на 20 признаках основного набора E6, обученная на всех 2877 заявлениях "
          "SWE-bench, без дообучения. Обучение на домене — та же модель с нуля, 5 фолдов GroupKFold по группам задач. "
          f"AUROC усреднён по фолдам (для переноса — по тем же фолдам); 95% ДИ — бутстрэп групп задач, {o['n_boot']} "
          "повторов. Для сравнения на SWE-bench: основная модель 0.687, только длина 0.640.", ""]
    for dom in ("appworld", "tau2"):
        r = res[dom]
        L += [f"### {DOMAINS[dom]}", "",
              f"Заявлений: {r['n']} (недостоверных {r['n_unreliable']}, достоверных {r['n_reliable']}), групп задач "
              f"{r['n_groups']}; правило заявлений — {r['claim_rule']}.", ""]
        if r.get("skipped"):
            L += [f"Модели не оценивались: {r['skipped']} (порог зафиксирован заранее).", ""]
            continue
        L += ["| режим | AUROC [95% ДИ] | AUROC без усреднения по фолдам |", "|---|---|---|"]
        for row in r["rows"]:
            L.append(f"| {MODE_NAMES[row['model_name']]} | {_f(row['auroc'])} [{_f(row['auroc_ci_low'])}; "
                     f"{_f(row['auroc_ci_high'])}] | {_f(row['auroc_pooled'])} |")
        L.append("")
        for (a_, b_), (dl, lo, hi) in r["pairs"].items():
            L.append(f"- Δ({MODE_NAMES[a_]} − {MODE_NAMES[b_]}) = {dl:+.3f} [{lo:+.3f}; {hi:+.3f}].")
        if "sens_args" in r:
            L.append("- С первой группировкой (только аргументы вызовов, 2171 сессия без группы — возможна утечка "
                     "задач между фолдами): " + "; ".join(
                         f"{MODE_NAMES[m]} {_f(v[0])} [{_f(v[1])}; {_f(v[2])}]" for m, v in r["sens_args"].items()) + ".")
        if "per_bench" in r:
            L += ["", "Перенос по доменам τ²-bench (описательно, AUROC без CV):", "",
                  "| домен | заявлений | недостоверных | перенос, только длина | перенос, основная модель E6 |",
                  "|---|---|---|---|---|"]
            for pb in r["per_bench"]:
                L.append(f"| {pb['benchmark']} | {pb['n']} | {pb['n_unreliable']} | {_f(pb['transfer_M_len'])} | "
                         f"{_f(pb['transfer_M_main'])} |")
        L.append("")
    ar = res.get("tau2_ar")
    if ar and not ar.get("skipped"):
        L += ["### τ²-bench без telecom (добавлено после расчёта)", "",
              "В telecom все сессии, где агент нашёл клиента, сходятся в одну группу (по-видимому, у задач telecom один "
              "клиент), поэтому задачи telecom не разделяются по фолдам. Проверка на airline + retail, где группа по "
              f"клиенту осмысленна: заявлений {ar['n']} (недостоверных {ar['n_unreliable']}), групп {ar['n_groups']}.",
              "", "| режим | AUROC [95% ДИ] |", "|---|---|"]
        for row in ar["rows"]:
            L.append(f"| {MODE_NAMES[row['model_name']]} | {_f(row['auroc'])} [{_f(row['auroc_ci_low'])}; "
                     f"{_f(row['auroc_ci_high'])}] |")
        L.append("")
        for (a_, b_), (dl, lo, hi) in ar["pairs"].items():
            L.append(f"- Δ({MODE_NAMES[a_]} − {MODE_NAMES[b_]}) = {dl:+.3f} [{lo:+.3f}; {hi:+.3f}].")
        L.append("")
    # importance
    L += ["## Шаг 4: важность признаков по доменам", "",
          "Среднее |SHAP| логистической регрессии, обученной на всех заявлениях домена (для SWE-bench — основная модель "
          "E6); ранг в скобках. «В числе ведущих» — топ-5 (зафиксировано заранее).", ""]
    imps = {"SWE-bench": o["swe_imp"]}
    for dom in ("appworld", "tau2"):
        if "importance" in res[dom]:
            imps[DOMAINS[dom]] = res[dom]["importance"]
    tabs = {k: v.set_index("feature") for k, v in imps.items()}
    ranks = {k: {f: i + 1 for i, f in enumerate(v.index)} for k, v in tabs.items()}
    single = {"SWE-bench": {c: auroc(o["swe"].label.to_numpy(), o["swe"][c].to_numpy(float)) for c in o["cols"]}}
    single.update({DOMAINS[d_]: res[d_]["single"] for d_ in ("appworld", "tau2") if "single" in res[d_]})
    L += ["| признак | " + " | ".join(f"{k}: коэф. / \\|SHAP\\| (ранг)" for k in tabs) + " | " +
          " | ".join(f"AUROC в одиночку: {k}" for k in single) + " |",
          "|---|" + "---|" * (len(tabs) + len(single))]
    for f in o["swe_imp"].feature:
        cells = []
        for k, t in tabs.items():
            cells.append(f"{t.coef_std[f]:+.3f} / {t.mean_abs_shap[f]:.3f} ({ranks[k][f]})" if f in t.index else
                         "константа")
        cells += [_f(single[k][f]) for k in single]
        L.append(f"| `{f}` | " + " | ".join(cells) + " |")
    L += ["", "AUROC в одиночку — по заявлениям домена без CV (описательно; > 0.5 — выше у недостоверных).", ""]
    for k in tabs:
        if k == "SWE-bench":
            continue
        rk = ranks[k].get(e6.ANCHOR)
        L.append(f"- {k}: `{e6.ANCHOR}` — " + (f"ранг {rk} из {len(tabs[k])}, коэффициент "
                                               f"{tabs[k].coef_std[e6.ANCHOR]:+.3f}" if rk else "константа") +
                 (" → **в числе ведущих**" if rk and rk <= 5 else " → не в числе ведущих") +
                 f" (в SWE-bench: ранг {ranks['SWE-bench'][e6.ANCHOR]}, коэффициент "
                 f"{tabs['SWE-bench'].coef_std[e6.ANCHOR]:+.3f}; одиночный AUROC в домене "
                 f"{_f(single[k][e6.ANCHOR])} против {_f(single['SWE-bench'][e6.ANCHOR])} в SWE-bench).")
    L += [""] + conclusions(o, single, tabs, ranks)
    path.write_text("\n".join(L) + "\n", encoding="utf-8")


def _row(r: dict, name: str) -> dict | None:
    return next((x for x in r.get("rows", []) if x["model_name"] == name), None)


def _ci(x: dict | None) -> str:
    return "—" if x is None else f"{_f(x['auroc'])} [{_f(x['auroc_ci_low'])}; {_f(x['auroc_ci_high'])}]"


def conclusions(o: dict, single: dict, tabs: dict, ranks: dict) -> list[str]:
    res, appl = o["res"], o["appl"].set_index("domain")
    aw, t2, ar = res["appworld"], res["tau2"], res.get("tau2_ar", {})
    tm_aw, tm_t2 = _row(aw, "transfer_M_main"), _row(t2, "transfer_M_main")
    im_t2, il_t2 = _row(t2, "indomain_M_main"), _row(t2, "indomain_M_len")
    sig_lo = lambda x: x is not None and x["auroc_ci_low"] > 0.5  # noqa: E731
    sig_hi = lambda x: x is not None and x["auroc_ci_high"] < 0.5  # noqa: E731
    d_in = t2["pairs"].get(("indomain_M_main", "indomain_M_len"))
    L = ["## Выводы", "",
         f"1. **Правила заявлений.** В AppWorld правила G0 переносятся: заявление есть у "
         f"{appl.loc['appworld', 'share_claim_failed']:.1%} провальных траекторий против 36.9% в SWE-bench, причём среди "
         f"провальных заявления чаще, чем среди успешных ({appl.loc['appworld', 'share_claim_success']:.1%}). В τ²-bench "
         f"не переносятся ({appl.loc['tau2', 'share_claim_failed']:.1%} провальных): агент говорит с клиентом другими "
         "словами («I've cancelled your reservation»); использована заранее зафиксированная доменная адаптация.",
         f"2. **Перенос без дообучения не работает.** AppWorld: AUROC {_ci(tm_aw)}"
         + (" — ДИ включает 0.5" if not sig_lo(tm_aw) else "") +
         f" (всего {aw['n']} заявлений, из них достоверных {aw['n_reliable']} — оценка грубая). τ²-bench: "
         f"{_ci(tm_t2)}" + (" — **ниже случайного**: модель SWE-bench ранжирует заявления в обратную сторону"
                            if sig_hi(tm_t2) else (" — на уровне случайного" if not sig_lo(tm_t2) else "")) + ".",
         "3. **Обучение на новом домене: сигнал слабый" +
         (" и не превышает модель одной длины.** " if d_in and d_in[1] <= 0 else ".** ") + f"τ²-bench: AUROC {_ci(im_t2)} против "
         f"{_ci(il_t2)} у модели одной длины"
         + (f"; прирост структуры {d_in[0]:+.3f} [{d_in[1]:+.3f}; {d_in[2]:+.3f}] — "
            + ("значим" if d_in[1] > 0 else "незначим") if d_in else "") + "."
         + (f" Без telecom (группы осмысленны): {_ci(_row(ar, 'indomain_M_main'))} против "
            f"{_ci(_row(ar, 'indomain_M_len'))}." if ar and not ar.get("skipped") else "")
         + " Первая группировка (2171 сессия без группы) давала 0.618; разница, вероятно, — утечка задач между "
           "фолдами, поэтому основной считается строгая группировка. "
         f"AppWorld: обучение на домене не оценивалось (достоверных заявлений {aw['n_reliable']} < 50).",
         f"4. **Энтропия биграмм фаз.** В τ²-bench формально в числе ведущих (ранг {ranks['τ²-bench'][e6.ANCHOR]} "
         f"по |SHAP|), но с **противоположным знаком** коэффициента ({tabs['τ²-bench'].coef_std[e6.ANCHOR]:+.3f} "
         f"против {tabs['SWE-bench'].coef_std[e6.ANCHOR]:+.3f}) и одиночным AUROC {_f(single['τ²-bench'][e6.ANCHOR])}, "
         "то есть сама по себе классы не различает; её вклад — поправка при других признаках. Связь «однообразное "
         "чередование фаз → недостоверное заявление» в τ²-bench не воспроизводится. В AppWorld одиночный AUROC "
         f"{_f(single['AppWorld'][e6.ANCHOR])} — то же направление, что в SWE-bench, но на 175 заявлениях без "
         "модели.",
         "5. **Итог.** Модель достоверности заявлений, построенная на SWE-bench, **не переносится** на другие домены "
         "без дообучения; структурные признаки при обучении на новом домене несут слабый, частично другой по "
         "направлению сигнал. Утверждать доменно-независимую модель по этим данным нельзя.", "",
         "## Ограничения", "",
         "- Идентификатор задачи восстановлен: в AppWorld точно (текст поручения), в τ²-bench — группа по клиенту; "
         "в telecom задачи не разделяются (один клиент), сессии без идентификатора (оборвавшиеся на первой реплике) "
         "образуют отдельные группы.",
         "- Фазовая разметка в новом домене — аналогия: «проверка» — повторное чтение изменённого приложения, а не "
         "запуск тестов; доли фаз заметно ниже, чем в SWE-bench (таблица шага 2).",
         "- Каркас tool_calling_with_shortlisting исключён (300 сессий AppWorld); в AppWorld мало достоверных "
         "заявлений.",
         "- Распределения признаков сильно отличаются (в τ²-bench траектории в 4 раза короче), а модель переноса "
         "использует масштабирование SWE-bench как есть — это и есть условие «без дообучения».", ""]
    return L
