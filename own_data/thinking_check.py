"""Offline check of the glm-4.6 reasoning mode (no API calls): does the corpus run look like the thinking-disabled
probe or like the thinking-enabled smoke run?

For every model response: completion_tokens and reasoning_tokens from the response's usage, the tokens of the
VISIBLE text (message content without reasoning_content) counted with ONE tokenizer for all sets — tiktoken
cl100k_base as a common proxy (it is not GLM's tokenizer) — and r = completion_tokens / visible tokens.
Sets: (a) the 19 local corpus trajectories of glm-4.6; (b) smoke run 20261027b (protocol 1.0); (c) the probe
probe-thinking-disabled-20261027. Decision rule (fixed by the author before the check): go to protocol 1.1 if the
corpus median r is closer to the probe than to the smoke run AND differs from the smoke run at least 1.5-fold.
Writes own_data/THINKING_CHECK.md and own_data/thinking_check.json.
Run inside own-ubuntu with the agent venv (tiktoken is a litellm dependency there):
  /opt/own/venv-agent/bin/python /mnt/c/Users/1/Desktop/diss/own_data/thinking_check.py
"""
import glob
import importlib.metadata
import json
from pathlib import Path

import numpy as np
import tiktoken

REPO = Path("/mnt/c/Users/1/Desktop/diss")
SETS = {
    "corpus": ("(а) корпус: 19 локальных траекторий glm-4.6",
               "data/raw/swebench_bashonly/20251201_mini-v1.17.1_glm-4.6/trajs/*.traj.json"),
    "smoke": ("(б) smoke-прогон 20261027b (протокол 1.0)",
              "own_data/raw/own-v1.0_mini-v1.17.1_glm-4.6_smoke-20261027b/*/*.traj.json"),
    "probe": ("(в) проба probe-thinking-disabled-20261027",
              "own_data/probes/probe-thinking-disabled-20261027/*/*.traj.json"),
}
enc = tiktoken.get_encoding("cl100k_base")


def responses(pattern: str):
    for p in sorted(glob.glob(str(REPO / pattern))):
        d = json.loads(Path(p).read_text(encoding="utf-8"))
        for m in d.get("messages") or []:
            if m.get("role") != "assistant":
                continue
            resp = ((m.get("extra") or {}).get("response")) or {}
            msg = ((resp.get("choices") or [{}])[0].get("message")) or {}
            usage = resp.get("usage") or {}
            details = usage.get("completion_tokens_details") or {}
            reasoning = msg.get("reasoning_content") or (msg.get("provider_specific_fields") or {}).get(
                "reasoning_content") or ""
            yield {"trajectory": Path(p).name, "completion_tokens": usage.get("completion_tokens"),
                   "reasoning_tokens": details.get("reasoning_tokens") if isinstance(details, dict) else None,
                   "visible_tokens": len(enc.encode(msg.get("content") or "")),
                   "reasoning_text_tokens": len(enc.encode(reasoning)) if reasoning else 0}


def summarize(rows: list[dict]) -> dict:
    with_usage = [r for r in rows if r["completion_tokens"] is not None]
    rs = [r["completion_tokens"] / r["visible_tokens"] for r in with_usage if r["visible_tokens"] > 0]
    q1, med, q3 = np.percentile(rs, [25, 50, 75]) if rs else (np.nan,) * 3
    rt = [r["reasoning_tokens"] for r in rows if r["reasoning_tokens"] is not None]
    return {"responses": len(rows), "with_usage": len(with_usage), "with_r": len(rs),
            "zero_visible_text": sum(1 for r in with_usage if r["visible_tokens"] == 0),
            "r_median": float(med), "r_q1": float(q1), "r_q3": float(q3),
            "completion_tokens_median": float(np.median([r["completion_tokens"] for r in with_usage])) if with_usage
            else None,
            "visible_tokens_median": float(np.median([r["visible_tokens"] for r in rows])) if rows else None,
            "reasoning_tokens_reported": len(rt), "reasoning_tokens_positive": sum(1 for x in rt if x and x > 0),
            "responses_with_reasoning_text": sum(1 for r in rows if r["reasoning_text_tokens"] > 0)}


def main() -> None:
    res = {k: summarize(list(responses(pat))) for k, (_, pat) in SETS.items()}
    c, s, p = (res[k]["r_median"] for k in ("corpus", "smoke", "probe"))
    closer_to_probe = abs(c - p) < abs(c - s)
    fold = max(c, s) / min(c, s)
    go = bool(closer_to_probe and fold >= 1.5)
    out = {"tokenizer": "tiktoken cl100k_base (proxy)", "tiktoken": importlib.metadata.version("tiktoken"),
           "sets": res, "decision": {"corpus_minus_probe": abs(c - p), "corpus_minus_smoke": abs(c - s),
                                     "closer_to_probe": bool(closer_to_probe), "fold_vs_smoke": fold,
                                     "go_to_protocol_1_1": go}}
    (REPO / "own_data/thinking_check.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")

    L = ["# Проверка режима рассуждений glm-4.6 (офлайн, без вызовов API)", "",
         "Скрипт: `own_data/thinking_check.py` (результаты — `thinking_check.json`). Для каждого ответа модели: "
         "`completion_tokens` и `reasoning_tokens` из usage ответа; токены **видимого** текста (поле `content` без "
         "`reasoning_content`), посчитанные одним токенизатором для всех наборов — **tiktoken cl100k_base как общий "
         f"прокси** (это не токенизатор GLM; tiktoken {out['tiktoken']}); r = completion_tokens / токены видимого текста. "
         "Ответы с пустым видимым текстом в r не входят (их число указано).", "",
         "| набор | ответов | с usage | с r | пустой текст | r: медиана [IQR] | completion_tokens, медиана | "
         "видимый текст, медиана токенов | reasoning_tokens в usage: есть / > 0 | ответов с текстом рассуждений |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for k, (name, _) in SETS.items():
        x = res[k]
        L.append(f"| {name} | {x['responses']} | {x['with_usage']} | {x['with_r']} | {x['zero_visible_text']} | "
                 f"{x['r_median']:.2f} [{x['r_q1']:.2f}; {x['r_q3']:.2f}] | {x['completion_tokens_median']:.0f} | "
                 f"{x['visible_tokens_median']:.0f} | {x['reasoning_tokens_reported']} / {x['reasoning_tokens_positive']} | "
                 f"{x['responses_with_reasoning_text']} |")
    usage_note = ("usage есть во всех ответах корпуса — оценка через стоимость не понадобилась."
                  if res["corpus"]["with_usage"] == res["corpus"]["responses"] else
                  "В части ответов корпуса usage нет — они в r не входят.")
    L += ["", f"- {usage_note}",
          "", "## Решение (правило задано автором до проверки)", "",
          f"- |медиана r корпуса − проба| = {abs(c - p):.2f}; |медиана r корпуса − smoke| = {abs(c - s):.2f} → корпус "
          + ("**ближе к пробе**" if closer_to_probe else "**ближе к smoke-прогону**") + ".",
          f"- Отношение медиан r корпуса и smoke-прогона: {fold:.2f} (порог 1.5) → "
          + ("**не меньше 1.5**" if fold >= 1.5 else "**меньше 1.5**") + ".",
          "- Итог: " + ("**переход к протоколу 1.1** (`thinking: disabled`)." if go else
                        "**условие не выполнено — протокол не меняется**, пилот не запускается до решения автора."),
          "", "## Ограничения", "",
          "- cl100k_base — прокси: абсолютные значения r зависят от токенизатора, сравниваются только наборы между собой.",
          "- Малые наборы: smoke — 10 ответов, проба — 7 (одна задача, по одному прогону); корпус — 19 задач.",
          "- Если в корпусе модель рассуждала, но провайдер не возвращал ни текст рассуждений, ни `reasoning_tokens`, "
          "это было бы видно по r (скрытые токены входят в completion_tokens): именно это и проверяет r.", ""]
    (REPO / "own_data/THINKING_CHECK.md").write_text("\n".join(L), encoding="utf-8")
    print(json.dumps(out["decision"]), json.dumps({k: (v["r_median"], v["with_usage"]) for k, v in res.items()}))


if __name__ == "__main__":
    main()
