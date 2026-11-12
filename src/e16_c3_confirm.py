"""E16 (reports/E16_PROTOCOL.md): confirmatory check of C3 (the plain probability question of E11) within the unit on
all finished attempts of the devstral units that have both a solved and an unresolved finished attempt.

  python -m src.e16_c3_confirm sample   -> outputs/e16_sample.csv (every eligible finished attempt; asked before or not;
                                           request order)
  python -m src.e16_c3_confirm cost     -> outputs/e16_cost.json (prompt tokens by the model's tokenizer; no API calls)
  python -m src.e16_c3_confirm check    -> outputs/e16_check.json (one minimal request: devstral-2512 by Mistral?)
  python -m src.e16_c3_confirm ask      -> outputs/e16_responses.jsonl (resumable; never passes the $10 budget)
  python -m src.e16_c3_confirm analyze  -> outputs/e16_parsed.csv, e16_attempts.csv, e16_metrics.csv, e16_summary.json
  python -m src.e16_c3_confirm extra    -> outputs/e16_extra.csv (descriptive, outside the protocol, after the results)

Requests exactly as in E11 / E11b (src.e11_elicitation.request(dp, "C3"): the full nano-agent conversation + the C3
question; OpenRouter, provider Mistral without fallbacks, temperature 0, tool_choice none, max_tokens 2048). The API key
is read from OPENROUTER_API_KEY and is never written anywhere.
"""
from __future__ import annotations

import argparse
import json
import time

import numpy as np
import pandas as pd

from src import e11_elicitation as e11
from src import e11b_finished_pairs as e11b
from src.adapters.agentic_evals import RAW_DIR
from src.common import OUTPUTS, SEED, get_logger

log = get_logger("e16")
COND = "C3"
SAMPLE = OUTPUTS / "e16_sample.csv"
COST = OUTPUTS / "e16_cost.json"
CHECK = OUTPUTS / "e16_check.json"
RESPONSES = OUTPUTS / "e16_responses.jsonl"
BUDGET_USD = 10.0
TOKEN_MARGIN = 1.10  # tokenizer count -> billed prompt tokens, safety margin for the budget rule
N_BOOT = 1000


# ------------------------------------------------------------------------------------------ sample

def finished_flags() -> pd.DataFrame:
    """has_final_message (E11 protocol 12.2 / 13) of every attempt of the devstral setting, streamed per run file."""
    rows = []
    for rd in sorted((RAW_DIR / e11.SETTING).glob("run_*"), key=lambda p: int(p.name.split("_")[1])):
        with open(rd / "detailed_predictions.jsonl", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    rec = json.loads(line)
                    rows.append({"run_id": f"{e11.SETTING}/{rd.name}/{rec['instance_id']}",
                                 "has_final_message": e11.has_final_message(rec["detailed_predictions"])})
    return pd.DataFrame(rows)


def asked_before() -> dict[str, str]:
    """run_id -> 'E11' | 'E11b' for every attempt with an answer to C3 (HTTP 200) in E11 or E11b."""
    out = {}
    for src, path in (("E11", e11.RESPONSES), ("E11b", e11b.RESPONSES)):
        with open(path, encoding="utf-8") as fh:
            for x in fh:
                r = json.loads(x)
                if r["cond"] == COND and r["status"] == 200:
                    out.setdefault(r["run_id"], src)
    return out


def draw_sample() -> tuple[pd.DataFrame, dict]:
    att = pd.read_csv(OUTPUTS / "e10_kth_risks.csv.gz")
    att = att[(att.setting == e11.SETTING) & att.submitted & ~att.eval_error].copy()
    att["label"] = (~att.solved).astype(int)  # 1 = unresolved
    att["run_id"] = e11.SETTING + "/" + att.run + "/" + att.task_id
    att = att.merge(finished_flags(), on="run_id", how="left")
    assert att.has_final_message.notna().all()
    fin = att[att.has_final_message == 1]
    both = fin.groupby("task_id").label.agg(["min", "max"])
    units = sorted(both.index[(both["min"] == 0) & (both["max"] == 1)])
    smp = fin[fin.task_id.isin(units)].copy()
    prev = asked_before()
    smp["asked_before"] = smp.run_id.map(prev).fillna("")
    # E11b analysis set: the 51 finished pairs of E11 + the new pairs of E11b (112 pairs)
    e11b_att = pd.read_csv(OUTPUTS / "e11b_attempts.csv")
    smp["in_e11b_pairs"] = smp.run_id.isin(set(e11b_att.run_id)).astype(int)
    # request order: units in a random order (seed 42), the attempts of a unit by run number
    order = {t: i for i, t in enumerate(np.random.default_rng(SEED).permutation(units))}
    smp["unit_order"] = smp.task_id.map(order)
    smp["j"] = smp.run.str.split("_").str[1].astype(int)
    smp = smp.sort_values(["unit_order", "j"]).reset_index(drop=True)
    recs = e11.raw_records(smp.run_id.tolist())
    smp["chat_ok"] = [int(chat_ok(recs[r])) for r in smp.run_id]
    keep = ["run_id", "task_id", "run", "label", "S", "O", "S+O", "has_final_message", "asked_before",
            "in_e11b_pairs", "unit_order", "chat_ok"]
    smp = smp[keep]
    lab = smp.groupby("task_id").label
    pairs = int((lab.sum() * (lab.count() - lab.sum())).sum())
    info = {"devstral_submitted_evaluated": len(att), "finished": int((att.has_final_message == 1).sum()),
            "units": len(units), "attempts": len(smp), "asked_before": int((smp.asked_before != "").sum()),
            "asked_before_by_source": smp.asked_before[smp.asked_before != ""].value_counts().to_dict(),
            "new_attempts": int((smp.asked_before == "").sum()), "within_unit_pairs": pairs,
            "attempts_in_e11b_pairs": int(smp.in_e11b_pairs.sum()),
            "e11b_attempts_not_in_sample": int(len(set(e11b_att.run_id) - set(smp.run_id))),
            "new_not_representable": smp.run_id[(smp.asked_before == "") & (smp.chat_ok == 0)].tolist()}
    return smp, info


def chat_ok(dp: dict) -> bool:
    """The conversation can be sent in the chat-completions format of E11: every assistant message has text content
    or tool calls (one attempt of the sample has an assistant message with neither; protocol E16, section 2)."""
    return all(m["role"] != "assistant" or m.get("tool_calls") or isinstance(m.get("content"), str)
               for m in e11.conversation(dp))


def sample() -> pd.DataFrame:
    if not SAMPLE.exists():
        df, info = draw_sample()
        df.round(8).to_csv(SAMPLE, index=False)
        (OUTPUTS / "e16_sample_info.json").write_text(json.dumps(info, indent=1), encoding="utf-8")
        log.info("sample: %s", info)
    return pd.read_csv(SAMPLE, keep_default_na=False)


def new_attempts() -> pd.DataFrame:
    smp = sample()
    return smp[(smp.asked_before == "") & (smp.chat_ok == 1)].reset_index(drop=True)


# ------------------------------------------------------------------------------------------ cost

def current_price() -> dict:
    """USD per 1M tokens of the Mistral endpoint of mistralai/devstral-2512 in the public OpenRouter catalogue."""
    import httpx

    r = httpx.get("https://openrouter.ai/api/v1/models/mistralai/devstral-2512/endpoints", timeout=60)
    r.raise_for_status()
    eps = r.json()["data"]["endpoints"]
    ep = next(e for e in eps if e.get("provider_name") == "Mistral")
    p = ep["pricing"]
    return {"input": float(p["prompt"]) * 1e6, "output": float(p["completion"]) * 1e6,
            "cache_read": float(p.get("input_cache_read") or 0) * 1e6, "endpoints": [e.get("provider_name") for e in eps],
            "status": ep.get("status"), "read": time.strftime("%Y-%m-%dT%H:%M:%S")}


def cost() -> dict:
    new = new_attempts()
    recs = e11.raw_records(new.run_id.tolist())
    tok = np.array([e11.n_tokens(e11.request(recs[r], COND)["messages"], recs[r]["tools"]) for r in new.run_id])
    pd.DataFrame({"run_id": new.run_id, "prompt_tokens": tok}).to_csv(OUTPUTS / "e16_tokens.csv", index=False)
    # expected answer length: C3 answers of E11 / E11b on attempts with a final message
    p11 = pd.read_csv(e11.PARSED)
    fin11 = set(pd.read_csv(e11.SAMPLE).query("has_final_message == 1").run_id)
    p11b = pd.read_csv(OUTPUTS / "e11b_parsed.csv")
    out_tok = pd.concat([p11[(p11.cond == COND) & p11.run_id.isin(fin11)].completion_tokens,
                         p11b[p11b.cond == COND].completion_tokens])
    price = current_price()
    n = len(new)
    usd_in = tok.sum() * price["input"] / 1e6
    res = {"requests": n, "prompt_tokens": int(tok.sum()), "prompt_tokens_mean": float(tok.mean()),
           "prompt_tokens_max": int(tok.max()), "expected_output_tokens_mean": float(out_tok.mean()),
           "price_usd_per_1M": price, "usd_input": usd_in,
           "usd_expected": usd_in + n * float(out_tok.mean()) * price["output"] / 1e6,
           "usd_upper_bound": usd_in + n * e11.MAX_TOKENS * price["output"] / 1e6,
           "budget_usd": BUDGET_USD,
           "e11b_actual_usd_per_C3_request": e11b_c3_cost()}
    COST.write_text(json.dumps(res, indent=1), encoding="utf-8")
    return res


def e11b_c3_cost() -> float:
    with open(e11b.RESPONSES, encoding="utf-8") as fh:
        c = [json.loads(x).get("cost_usd") or 0.0 for x in fh if json.loads(x)["cond"] == COND]
    return float(np.mean(c))


# ------------------------------------------------------------------------------------------ API

def check() -> dict:
    status, js, err = e11._post("openrouter", {"messages": [{"role": "user", "content": "Reply with the single word OK."}],
                                               "temperature": 0, "max_tokens": 5})
    out = {"provider": "openrouter", "status": status, "error": err, **e11._summary(js),
           "identity_ok": e11.identity_ok(js), "time": time.strftime("%Y-%m-%dT%H:%M:%S")}
    CHECK.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    return out


def _responses() -> list[dict]:
    if not RESPONSES.exists():
        return []
    with open(RESPONSES, encoding="utf-8") as fh:
        return [json.loads(x) for x in fh if x.strip()]


def spent() -> float:
    chk = json.loads(CHECK.read_text(encoding="utf-8")).get("cost_usd", 0.0) if CHECK.exists() else 0.0
    return sum(r.get("cost_usd") or 0.0 for r in _responses()) + chk


def ask() -> None:
    """C3 for every new attempt in the order of the sample (resumable). Before every request: STOP if the spend so far
    plus the upper bound of this request (prompt tokens x margin at the input price + max_tokens at the output price)
    would pass the budget. Stops on an HTTP error and on an answer not from devstral-2512 / Mistral."""
    chk = json.loads(CHECK.read_text(encoding="utf-8")) if CHECK.exists() else {}
    if not chk.get("identity_ok"):
        raise SystemExit("STOP: no passed availability check (outputs/e16_check.json)")
    price = json.loads(COST.read_text(encoding="utf-8"))["price_usd_per_1M"]
    tok = pd.read_csv(OUTPUTS / "e16_tokens.csv").set_index("run_id").prompt_tokens
    new = new_attempts()
    recs = e11.raw_records(new.run_id.tolist())
    done = {r["run_id"] for r in _responses() if r["status"] == 200}
    total = spent()
    for r in new.itertuples():
        if r.run_id in done:
            continue
        bound = (tok[r.run_id] * TOKEN_MARGIN * price["input"] + e11.MAX_TOKENS * price["output"]) / 1e6
        if total + bound > BUDGET_USD:
            raise SystemExit(f"STOP: spend ${total:.4f} + upper bound ${bound:.4f} > ${BUDGET_USD}")
        t0 = time.time()
        status, js, err = e11._post("openrouter", e11.request(recs[r.run_id], COND))
        row = {"run_id": r.run_id, "task_id": r.task_id, "run": r.run, "cond": COND, "question": e11.QUESTIONS[COND],
               "provider": "openrouter", "status": status, "error": err, **e11._summary(js),
               "identity_ok": e11.identity_ok(js), "seconds": round(time.time() - t0, 1),
               "time": time.strftime("%Y-%m-%dT%H:%M:%S")}
        with open(RESPONSES, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        total += row["cost_usd"] or 0.0
        log.info("%s %s status %s %ss $%.4f (total $%.4f)", r.run_id, row["finish_reason"], status, row["seconds"],
                 row["cost_usd"], total)
        if status != 200:
            raise SystemExit(f"STOP: {r.run_id}: HTTP {status}: {err[:300]}")
        if not row["identity_ok"]:
            raise SystemExit(f"STOP: {r.run_id}: model {row['model']!r}, provider {row['served_by']!r}")
    log.info("all requests done, spent $%.4f", total)


# ------------------------------------------------------------------------------------------ analysis

def answers() -> pd.DataFrame:
    """One row per attempt of the sample: the C3 answer (E11, E11b or E16) and its parse."""
    smp = sample()
    src = {}
    for name, rows in (("E11", e11._responses()), ("E11b", e11b._responses()), ("E16", _responses())):
        for r in rows:
            if r["cond"] == COND and r["status"] == 200:
                src.setdefault(r["run_id"], (name, r))
    out = []
    for a in smp.itertuples():
        name, r = src.get(a.run_id, ("", None))
        text = (r or {}).get("response") or ""
        v, how = (e11.parse_number(text) if text.strip() and not (r or {}).get("tool_calls") else (None, "unparsed"))
        out.append({"run_id": a.run_id, "answer_source": name, "answered": r is not None,
                    "finish_reason": (r or {}).get("finish_reason"), "chars": len(text),
                    "completion_tokens": ((r or {}).get("usage") or {}).get("completion_tokens"),
                    "C3_number": np.nan if v is None else v, "parse": how})
    return smp.merge(pd.DataFrame(out), on="run_id")


def pooled_within(att: pd.DataFrame, risk: str, pair_mask=None, exclude_missing: bool = False) -> dict:
    """Within-unit AUROC on all (unresolved, solved) pairs of a unit, pooled; missing risk -> tie (or the pair is
    dropped); bootstrap over units. pair_mask(ui, si) -> bool array selects pairs (e.g. both attempts new)."""
    units, c, n, cu, nu = [], [], [], [], []
    for t, g in att.groupby("task_id"):
        f, s = g[g.label == 1], g[g.label == 0]
        if not len(f) or not len(s):
            continue
        rf, rs = f[risk].to_numpy(float)[:, None], s[risk].to_numpy(float)[None, :]
        miss = np.isnan(rf) | np.isnan(rs)
        conc = np.where(miss, 0.5, (rf > rs) + 0.5 * (rf == rs))
        mk = np.ones_like(conc, bool) if pair_mask is None else pair_mask(f, s)
        if exclude_missing:
            mk = mk & ~miss
        untied = mk & ~miss & (rf != rs)
        units.append(t)
        c.append(conc[mk].sum()), n.append(mk.sum())
        cu.append(conc[untied].sum()), nu.append(untied.sum())
    c, n, cu, nu = (np.array(x, float) for x in (c, n, cu, nu))
    ok = n > 0
    rng = np.random.default_rng(SEED)
    draws = rng.integers(0, len(units), (N_BOOT, len(units)))
    boot = (c[draws].sum(1) / np.maximum(n[draws].sum(1), 1))
    lo, hi = np.percentile(boot, [2.5, 97.5])
    return {"auroc": float(c.sum() / n.sum()), "ci_lo": float(lo), "ci_hi": float(hi), "pairs": int(n.sum()),
            "units": int(ok.sum()), "macro": float(np.mean(c[ok] / n[ok])),
            "tie_share": float(1 - nu.sum() / n.sum()), "auroc_untied": float(cu.sum() / nu.sum()) if nu.sum() else None,
            "concordant_untied": float(cu.sum()), "untied_pairs": int(nu.sum()), "boot": boot}


def analyze() -> dict:
    att = answers()
    parsed = att[["run_id", "task_id", "label", "answer_source", "answered", "finish_reason", "chars",
                  "completion_tokens", "C3_number", "parse"]]
    parsed.to_csv(OUTPUTS / "e16_parsed.csv", index=False)
    fe = pd.read_csv(OUTPUTS / "e10_features.csv.gz")
    claim = fe[fe.corpus == "kth"].set_index("run_id").has_claim
    att["G0"] = 1 - claim.loc[att.run_id].astype(int).to_numpy()
    att["C3"] = 100 - att.C3_number
    att["is_new"] = (att.asked_before == "").astype(int)
    a = att[att.answered].copy()  # attempts without any answer (budget stop) are left out of every pair
    both_new = lambda f, s: (f.is_new.to_numpy()[:, None] == 1) & (s.is_new.to_numpy()[None, :] == 1)  # noqa: E731
    not_e11b = lambda f, s: ~((f.in_e11b_pairs.to_numpy()[:, None] == 1)  # noqa: E731
                              & (s.in_e11b_pairs.to_numpy()[None, :] == 1)
                              & (f.task_id.to_numpy()[:, None] == s.task_id.to_numpy()[None, :]))
    scopes = {"all_pairs": None, "new_pairs": both_new, "pairs_outside_e11b_112": not_e11b}
    rows, res = [], {}
    for scope, mk in scopes.items():
        for risk in ("C3", "S+O", "S", "G0"):
            for excl in (False, True):
                w = pooled_within(a, risk, mk, excl)
                key = f"{scope}|{risk}|{'excluded' if excl else 'ties'}"
                res[key] = w
                rows.append({"scope": scope, "predictor": risk, "missing": "excluded" if excl else "ties",
                             **{k: v for k, v in w.items() if k != "boot"}})
        for b in ("S+O", "G0"):
            d = res[f"{scope}|C3|ties"]["boot"] - res[f"{scope}|{b}|ties"]["boot"]
            lo, hi = np.percentile(d, [2.5, 97.5])
            rows.append({"scope": scope, "predictor": f"C3 - {b}", "missing": "ties",
                         "auroc": res[f"{scope}|C3|ties"]["auroc"] - res[f"{scope}|{b}|ties"]["auroc"],
                         "ci_lo": lo, "ci_hi": hi})
    met = pd.DataFrame(rows)
    met.to_csv(OUTPUTS / "e16_metrics.csv", index=False)
    att.round(6).to_csv(OUTPUTS / "e16_attempts.csv", index=False)
    from scipy.stats import binomtest

    def sign(key):
        w = res[key]
        return float(binomtest(int(round(w["concordant_untied"])), w["untied_pairs"], 0.5).pvalue) if w["untied_pairs"] \
            else None
    resp = _responses()
    ok = [r for r in resp if r["status"] == 200]
    prim, sec = res["all_pairs|C3|ties"], res["new_pairs|C3|ties"]
    summ = {"criteria": {"primary_all_pairs": {"auroc": prim["auroc"], "ci_lo": prim["ci_lo"], "ci_hi": prim["ci_hi"],
                                               "pairs": prim["pairs"], "units": prim["units"],
                                               "met": bool(prim["ci_lo"] > 0.5)},
                         "secondary_new_pairs": {"auroc": sec["auroc"], "ci_lo": sec["ci_lo"], "ci_hi": sec["ci_hi"],
                                                 "pairs": sec["pairs"], "units": sec["units"],
                                                 "met": bool(sec["ci_lo"] > 0.5)}},
            "sign_test_untied_pairs_descriptive": {k: sign(f"{k}|C3|ties") for k in scopes},
            "attempts": len(att), "answered": int(att.answered.sum()),
            "answers_by_source": att.answer_source.value_counts().to_dict(),
            "requests_e16": len(resp), "answers_ok_e16": len(ok), "spent_usd_e16": spent(),
            "identity_all_ok": all(r.get("identity_ok") for r in ok),
            "models": sorted({str(r.get("model")) for r in ok}), "served_by": sorted({str(r.get("served_by")) for r in ok}),
            "parse": parsed.groupby(["answer_source", "label", "parse"]).size().rename("n").reset_index()
            .to_dict("records"),
            "finish_reason": parsed.groupby(["answer_source", "finish_reason"]).size().rename("n").reset_index()
            .to_dict("records"),
            "C3_by_class": {int(lab): {"mean": float(g.C3_number.mean()), "median": float(g.C3_number.median()),
                                       "share_95": float((g.C3_number == 95).mean()), "n": int(g.C3_number.notna().sum())}
                            for lab, g in a.groupby("label")}}
    (OUTPUTS / "e16_summary.json").write_text(json.dumps(summ, indent=1, ensure_ascii=False, default=float),
                                              encoding="utf-8")
    return summ


def extra() -> pd.DataFrame:
    """Descriptive tables outside the protocol (added after the results; criteria untouched): within-unit AUROC by pair
    type (both answers from E11/E11b = the 112 E11b pairs; one old, one new; both new) and the C3 answer distribution by
    class and answer source (denominator: parsed answers). Reads outputs/e16_attempts.csv written by analyze."""
    att = pd.read_csv(OUTPUTS / "e16_attempts.csv", keep_default_na=False, na_values=[""])
    a = att[att.answered.astype(str) == "True"].copy()
    new = lambda g: g.is_new.to_numpy(int)  # noqa: E731
    types = {"old-old": lambda f, s: (new(f)[:, None] == 0) & (new(s)[None, :] == 0),
             "mixed": lambda f, s: (new(f)[:, None] + new(s)[None, :]) == 1,
             "new-new": lambda f, s: (new(f)[:, None] == 1) & (new(s)[None, :] == 1)}
    rows = []
    for name, mk in types.items():
        for risk in ("C3", "S+O", "G0"):
            w = pooled_within(a, risk, mk)
            rows.append({"table": "pair_type", "group": name, "predictor": risk,
                         **{k: v for k, v in w.items() if k != "boot"}})
    src = a.answer_source.map({"E16": "E16", "E11": "E11+E11b", "E11b": "E11+E11b"})
    for name, g0 in (("all", a), ("E16", a[src == "E16"]), ("E11+E11b", a[src == "E11+E11b"])):
        for lab, g in g0.groupby("label"):
            v = g.C3_number.dropna()
            rows.append({"table": "c3_distribution", "group": name, "predictor": f"label={lab}", "n": len(v),
                         "mean": v.mean(), "median": v.median(), "share_100": (v == 100).mean(),
                         "share_95": (v == 95).mean(), "share_ge90": (v >= 90).mean(), "n_lt90": int((v < 90).sum()),
                         "n_le50": int((v <= 50).sum())})
    out = pd.DataFrame(rows)
    out.to_csv(OUTPUTS / "e16_extra.csv", index=False)
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["sample", "cost", "check", "ask", "analyze", "extra"])
    a = p.parse_args()
    if a.cmd == "sample":
        smp = sample()
        print(json.loads((OUTPUTS / "e16_sample_info.json").read_text(encoding="utf-8")))
        print(smp.groupby(["label", "asked_before"]).size())
    elif a.cmd == "cost":
        print(json.dumps(cost(), indent=1))
    elif a.cmd == "check":
        print(json.dumps(check(), indent=1, ensure_ascii=False))
    elif a.cmd == "ask":
        ask()
    elif a.cmd == "analyze":
        print(json.dumps(analyze(), indent=1, ensure_ascii=False, default=float))
    elif a.cmd == "extra":
        print(extra().round(4).to_string())


if __name__ == "__main__":
    main()
