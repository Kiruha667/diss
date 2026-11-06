"""E11b (reports/E11_PROTOCOL.md, section 13): C3 / C4 on pairs of finished attempts only (both with a final message).

  python -m src.e11b_finished_pairs sample   -> outputs/e11b_sample.csv (new units, not in E11; seed 42)
  python -m src.e11b_finished_pairs cost     -> outputs/e11b_cost.json (no API calls)
  python -m src.e11b_finished_pairs ask      -> outputs/e11b_responses.jsonl (resumable; $6 budget, identity check)
  python -m src.e11b_finished_pairs analyze  -> outputs/e11b_parsed.csv, e11b_metrics.csv, e11b_summary.json

Requests as in E11 (src/e11_elicitation.request: the full nano-agent conversation + the question; OpenRouter, provider
Mistral without fallbacks, temperature 0, tool_choice none, max_tokens 2048). Metrics on the 51 finished pairs of E11
plus the new pairs: within-unit AUROC of C3, C4, S+O, G0 and C4 - C3, bootstrap over units (1000, seed 42).
"""
from __future__ import annotations

import argparse
import json
import time

import numpy as np
import pandas as pd

from src import e11_elicitation as e11
from src.common import OUTPUTS, SEED, get_logger

log = get_logger("e11b")
CONDS = ("C3", "C4")
TARGET_PAIRS = 150
SAMPLE = OUTPUTS / "e11b_sample.csv"
RESPONSES = OUTPUTS / "e11b_responses.jsonl"
BUDGET_USD = 6.0
CHECK_EVERY = 20
PREDICTORS = ["C3", "C4", "S+O", "G0"]


# ------------------------------------------------------------------------------------------ sample

def draw_sample() -> tuple[pd.DataFrame, dict]:
    """Units of the devstral setting with a solved and an unresolved submitted attempt, not in E11, where both a
    solved and an unresolved attempt have a final message; all of them if fewer than needed, else rng.choice; in
    each unit one solved and one unresolved attempt with a final message (rng.integers over the attempts sorted by
    run number), seed 42."""
    att = pd.read_csv(OUTPUTS / "e10_kth_risks.csv.gz")
    att = att[(att.setting == e11.SETTING) & att.submitted & ~att.eval_error].copy()
    att["label"] = (~att.solved).astype(int)
    att["run_id"] = e11.SETTING + "/" + att.run + "/" + att.task_id
    e11_units = set(pd.read_csv(e11.SAMPLE).task_id)
    both = att.groupby("task_id").label.agg(["min", "max"])
    mixed = sorted(set(both.index[(both["min"] == 0) & (both["max"] == 1)]) - e11_units)
    cand = att[att.task_id.isin(mixed)].copy()
    recs = e11.raw_records(cand.run_id.tolist())
    cand["has_final_message"] = [e11.has_final_message(recs[r]) for r in cand.run_id]
    fin = cand[cand.has_final_message == 1]
    ok = fin.groupby("task_id").label.agg(["min", "max"])
    avail = sorted(ok.index[(ok["min"] == 0) & (ok["max"] == 1)])
    e11_fin = pd.read_csv(OUTPUTS / "e11_attempts.csv").groupby("task_id").has_final_message.min()
    n_e11 = int((e11_fin == 1).sum())
    need = TARGET_PAIRS - n_e11
    rng = np.random.default_rng(SEED)
    units = avail if len(avail) <= need else sorted(rng.choice(avail, need, replace=False))
    rows = []
    for t in units:
        a = fin[fin.task_id == t]
        for lab in (0, 1):
            runs = sorted(a.run[a.label == lab], key=lambda r: int(r.split("_")[1]))
            rows.append(a[a.run == runs[int(rng.integers(len(runs)))]].iloc[0])
    out = pd.DataFrame(rows)[["run_id", "task_id", "run", "label", "S", "O", "S+O", "has_final_message"]]
    info = {"new_mixed_units": len(mixed), "available_units": len(avail), "e11_finished_pairs": n_e11,
            "needed": need, "taken": len(units), "total_pairs": n_e11 + len(units)}
    return out.reset_index(drop=True), info


def sample() -> pd.DataFrame:
    if not SAMPLE.exists():
        df, info = draw_sample()
        df.round(8).to_csv(SAMPLE, index=False)
        (OUTPUTS / "e11b_sample_info.json").write_text(json.dumps(info, indent=1), encoding="utf-8")
        log.info("sample: %s", info)
    return pd.read_csv(SAMPLE)


# ------------------------------------------------------------------------------------------ cost

def cost() -> dict:
    smp = sample()
    recs = e11.raw_records(smp.run_id.tolist())
    tok = {c: np.array([e11.n_tokens(e11.request(recs[r], c)["messages"], recs[r]["tools"]) for r in smp.run_id])
           for c in CONDS}
    # expected output: E11 answers of C3 / C4 on attempts with a final message (mean completion tokens)
    p = pd.read_csv(e11.PARSED)
    fin = set(pd.read_csv(e11.SAMPLE).query("has_final_message == 1").run_id)
    out_mean = {c: float(p[(p.cond == c) & p.run_id.isin(fin)].completion_tokens.mean()) for c in CONDS}
    pr = e11.PRICE
    n = len(smp)
    inp = int(sum(t.sum() for t in tok.values()))
    out_exp = sum(n * out_mean[c] for c in CONDS)
    res = {"attempts": n, "requests": n * len(CONDS), "prompt_tokens": inp,
           "prompt_tokens_per_request_max": int(max(t.max() for t in tok.values())),
           "expected_output_tokens_per_answer_E11": out_mean, "price_usd_per_1M": pr,
           "usd_no_cache": (inp * pr["input"] + out_exp * pr["output"]) / 1e6,
           "usd_with_cache": (tok["C3"].sum() * pr["input"] + tok["C4"].sum() * pr["cache_read"]
                              + out_exp * pr["output"]) / 1e6,
           "usd_upper_bound": (inp * pr["input"] + n * len(CONDS) * e11.MAX_TOKENS * pr["output"]) / 1e6,
           "budget_usd": BUDGET_USD}
    (OUTPUTS / "e11b_cost.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    return res


# ------------------------------------------------------------------------------------------ API

def _responses() -> list[dict]:
    if not RESPONSES.exists():
        return []
    with open(RESPONSES, encoding="utf-8") as fh:
        return [json.loads(x) for x in fh if x.strip()]


def ask() -> None:
    """C3 then C4 for every attempt of the sample (resumable). Stops on an HTTP error, on an answer not from
    devstral-2512 / Mistral, and when spend + 20 x the largest request could pass the $6 budget."""
    smp = sample()
    recs = e11.raw_records(smp.run_id.tolist())
    done = {(r["run_id"], r["cond"]) for r in _responses() if r["status"] == 200}
    n = 0
    for r in smp.itertuples():
        for cond in CONDS:
            if (r.run_id, cond) in done:
                continue
            if n % CHECK_EVERY == 0:
                costs = [x.get("cost_usd") or 0.0 for x in _responses()]
                total, top = sum(costs), max(costs, default=0.0)
                log.info("spent so far $%.4f (largest request $%.4f)", total, top)
                if total + CHECK_EVERY * top > BUDGET_USD:
                    raise SystemExit(f"STOP: spend ${total:.4f} + {CHECK_EVERY} x ${top:.4f} > ${BUDGET_USD}")
            t0 = time.time()
            status, js, err = e11._post("openrouter", e11.request(recs[r.run_id], cond))
            row = {"run_id": r.run_id, "task_id": r.task_id, "run": r.run, "cond": cond,
                   "question": e11.QUESTIONS[cond], "provider": "openrouter", "status": status, "error": err,
                   **e11._summary(js), "identity_ok": e11.identity_ok(js), "seconds": round(time.time() - t0, 1),
                   "time": time.strftime("%Y-%m-%dT%H:%M:%S")}
            with open(RESPONSES, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            n += 1
            log.info("%s %s %s status %s %ss $%.4f", r.run_id, cond, row["finish_reason"], status, row["seconds"],
                     row["cost_usd"])
            if status != 200:
                raise SystemExit(f"STOP: {r.run_id} {cond}: HTTP {status}: {err[:300]}")
            if not row["identity_ok"]:
                raise SystemExit(f"STOP: {r.run_id} {cond}: model {row['model']!r}, provider {row['served_by']!r}")
    log.info("all requests done, spent $%.4f", sum(x.get("cost_usd") or 0.0 for x in _responses()))


# ------------------------------------------------------------------------------------------ analysis

def pairs_table() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Attempts of all finished pairs: the 51 of E11 (answers of E11) and the new ones (answers of E11b)."""
    fe = pd.read_csv(OUTPUTS / "e10_features.csv.gz")
    claim = fe[fe.corpus == "kth"].set_index("run_id").has_claim
    old = pd.read_csv(OUTPUTS / "e11_attempts.csv")
    fin = old.groupby("task_id").has_final_message.min()
    old = old[old.task_id.isin(fin.index[fin == 1])][["run_id", "task_id", "label", "S+O", "C3_number",
                                                      "C4_number"]].assign(source="E11")
    smp = sample()
    last = {(r["run_id"], r["cond"]): r for r in _responses() if r["status"] == 200}
    new = smp[["run_id", "task_id", "label", "S+O"]].assign(source="E11b")
    parsed = []
    for r in smp.itertuples():
        for c in CONDS:
            x = last.get((r.run_id, c))
            text = (x or {}).get("response") or ""
            v, how = (e11.parse_number(text) if text.strip() and not (x or {}).get("tool_calls")
                      else (None, "unparsed"))
            parsed.append({"run_id": r.run_id, "task_id": r.task_id, "label": r.label, "cond": c,
                           "answered": x is not None, "finish_reason": (x or {}).get("finish_reason"),
                           "chars": len(text), "completion_tokens": ((x or {}).get("usage") or {})
                           .get("completion_tokens"), "number": np.nan if v is None else v, "parse": how})
    parsed = pd.DataFrame(parsed)
    parsed.to_csv(OUTPUTS / "e11b_parsed.csv", index=False)
    for c in CONDS:
        new[f"{c}_number"] = parsed[parsed.cond == c].set_index("run_id").loc[new.run_id, "number"].to_numpy()
    att = pd.concat([old, new], ignore_index=True)
    att["G0"] = 1 - claim.loc[att.run_id].astype(int).to_numpy()
    for c in CONDS:
        att[c] = 100 - att[f"{c}_number"]
    return att, parsed


def within(att: pd.DataFrame, scope: str) -> tuple[list[dict], dict]:
    """Within-unit AUROC (unparsed -> tie; check: such pairs excluded) and C4 - C3, bootstrap over units."""
    sc = {p: e11.pair_scores(att, p) for p in PREDICTORS}
    n = len(sc["C4"][0])
    draws = np.random.default_rng(SEED).integers(0, n, (e11.N_BOOT, n))
    rows, crit = [], {}
    for mode in ("ties", "excluded"):
        b = {}
        for p in PREDICTORS:
            s, ok = sc[p]
            if mode == "ties":
                b[p] = (s.mean(), s[draws].mean(axis=1))
            else:
                w = ok[draws]
                b[p] = (s[ok].mean(), (s[draws] * w).sum(axis=1) / np.maximum(w.sum(axis=1), 1))
            lo, hi = np.percentile(b[p][1], [2.5, 97.5])
            rows.append({"scope": scope, "missing": mode, "metric": "within_auroc", "name": p, "value": b[p][0],
                         "ci_lo": lo, "ci_hi": hi, "n_pairs": n, "n_pairs_complete": int(ok.sum())})
        for k, (v, bb) in {"K2': C4 - 0.5": (b["C4"][0] - 0.5, b["C4"][1] - 0.5),
                           "K1': C4 - C3": (b["C4"][0] - b["C3"][0], b["C4"][1] - b["C3"][1]),
                           "C4 - (S+O)": (b["C4"][0] - b["S+O"][0], b["C4"][1] - b["S+O"][1]),
                           "C4 - G0": (b["C4"][0] - b["G0"][0], b["C4"][1] - b["G0"][1])}.items():
            lo, hi = np.percentile(bb, [2.5, 97.5])
            rows.append({"scope": scope, "missing": mode, "metric": "within_auroc_diff", "name": k, "value": v,
                         "ci_lo": lo, "ci_hi": hi, "n_pairs": n, "n_pairs_complete": np.nan})
            if k.startswith("K"):
                crit[f"{mode}|{k}"] = {"value": float(v), "ci_lo": float(lo), "ci_hi": float(hi), "met": bool(lo > 0)}
    return rows, crit


def analyze() -> dict:
    att, parsed = pairs_table()
    assert parsed.answered.all(), f"answers missing: {int((~parsed.answered).sum())}"
    rows, crit = within(att, "all_finished")
    rows_new, _ = within(att[att.source == "E11b"], "new_only")
    rows_old, _ = within(att[att.source == "E11"], "e11_only")
    cls = []
    for lab in (0, 1):
        a = att[att.label == lab]
        for c in CONDS:
            x = a[f"{c}_number"]
            cls.append({"scope": "all_finished", "missing": "excluded", "metric": "by_class", "name": f"mean {c}",
                        "label": lab, "value": x.mean(), "n": int(x.notna().sum())})
            cls.append({"scope": "all_finished", "missing": "excluded", "metric": "by_class", "name": f"median {c}",
                        "label": lab, "value": x.median(), "n": int(x.notna().sum())})
    pd.DataFrame(rows + rows_new + rows_old + cls).to_csv(OUTPUTS / "e11b_metrics.csv", index=False)
    att.round(6).to_csv(OUTPUTS / "e11b_attempts.csv", index=False)
    resp = _responses()
    ok = [r for r in resp if r["status"] == 200]
    summ = {"criteria_main": {k.split("|")[1]: v for k, v in crit.items() if k.startswith("ties|")},
            "criteria_excluded_missing": {k.split("|")[1]: v for k, v in crit.items() if k.startswith("excluded|")},
            "pairs": {"E11": int((att.source == "E11").sum() // 2), "E11b": int((att.source == "E11b").sum() // 2)},
            "requests": len(resp), "answers_ok": len(ok), "spent_usd": sum(r.get("cost_usd") or 0.0 for r in resp),
            "identity_all_ok": all(r.get("identity_ok") for r in ok),
            "models": sorted({r.get("model") for r in ok}), "served_by": sorted({r.get("served_by") for r in ok}),
            "finish_reason": parsed.groupby(["cond", "finish_reason"]).size().rename("n").reset_index()
            .to_dict("records"),
            "parse": parsed.groupby(["cond", "label", "parse"]).size().rename("n").reset_index().to_dict("records")}
    (OUTPUTS / "e11b_summary.json").write_text(json.dumps(summ, indent=1, ensure_ascii=False, default=float),
                                               encoding="utf-8")
    return summ


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["sample", "cost", "ask", "analyze"])
    a = p.parse_args()
    if a.cmd == "sample":
        print(sample().groupby("label").size())
    elif a.cmd == "cost":
        print(json.dumps(cost(), indent=1))
    elif a.cmd == "ask":
        ask()
    elif a.cmd == "analyze":
        print(json.dumps(analyze(), indent=1, ensure_ascii=False, default=float))


if __name__ == "__main__":
    main()
