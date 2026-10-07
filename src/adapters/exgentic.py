"""Exgentic/agent-llm-traces-v2 adapter (E6 block 4: external validation in other domains).

Source: Hugging Face dataset Exgentic/agent-llm-traces-v2 (revision pinned below), OpenTelemetry chat spans of the
agent under test, 5 harnesses x 5 models. Used domains: AppWorld (personal-assistant APIs; label from the AppWorld
unit tests on the final database state) and tau2-bench airline / retail / telecom (customer service; label from the
tau2 environment checks). Not used: swebench (the training domain itself) and browsecompplus (label from an LLM
judge, excluded by the source requirements). Exgentic/traces-v2 (the full corpus with task ids) is not public, so
task ids are rebuilt from text (below).

Unified record (the E0 schema plus fields this domain needs):
  run_id (= session_id), task_id, agent_id (= harness), model_id, model_family, benchmark, label (1 = failure),
  meta {status, score, harness, run}, task_text,
  steps: assistant {role, text, step_idx, tool_calls: [{name, args}], nl}
         tool      {role, text, step_idx, error}
Reconstruction: one assistant step per chat span (its output message), in span order; the observation of step i =
the messages of span i+1's input after its last assistant message (tool_call_response results or user text), so
cumulative and incremental histories are handled alike. Tool calls: structured tool_call parts; DeepSeek DSML
markup written as text (<｜DSML｜invoke name="...">); smolagents_code actions = calls of known tool names inside the
JSON "code" field. The `mcp__environment__` prefix of claude_code is stripped.
Step natural language (`nl`, for the final-message claim rules of G0): text parts (smolagents: the "thought" field)
plus the content of `message(...)` calls (the agent's words to the tau2 user).
Step text (`text`, for E1 segmentation): nl + every tool call rendered as a fenced block "name(args)", like the
fenced bash commands of SWE-bench steps.
Tool-output error flag: the result text starts with Error/Exception or contains "Error executing tool",
"Traceback (most recent call last)", "Code execution failed" or "InterpreterError".
Task ids: AppWorld — sha1 of the instruction after "Task from supervisor:" (whitespace-normalized); tau2 — the
task is not visible to the agent (a simulated customer plays it), so the group is the customer: sessions are linked
(union-find) whenever they share a customer identifier used in tool-call arguments (user_id, customer_id,
phone_number, email, first+last name+zip, reservation_id, order_id). This group is coarser than the task (several
tasks of one customer fall together), so GroupKFold stays leak-free wherever the agent identified the customer;
sessions without any identifier form their own group (a possible leak, reported).
Correction made after the first block-4 run showed 2171 of 4647 tau2 sessions without a group (all smolagents_code
sessions, whose arguments live in code, and sessions that ended at the first turn): identifiers are also taken from
keyword literals in smolagents code and, by regular expressions (user ids, e-mails, phone numbers, #W order ids),
from the customer's messages and tool outputs (never from the policy prompt). The first grouping (dict arguments
only) is kept as `task_id_args` and reported as a sensitivity.
Exclusions (logged by class): status == "error" (harness / infrastructure error); harness
tool_calling_with_shortlisting (its trace interleaves the scaffold's tool-shortlisting chats with the agent's);
no assistant step.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

import pandas as pd

from src.common import INTERIM, RAW, get_logger

log = get_logger("exgentic")

REPO = "Exgentic/agent-llm-traces-v2"
REVISION = "4b8ad4ab198438e5a170f9171c19c6a2cf7c1814"
SOURCE = "exgentic"
BENCHMARKS = ("appworld", "tau2_airline", "tau2_retail", "tau2_telecom")
FAMILY = {"DeepSeek": "deepseek", "Kimi": "moonshot", "claude": "anthropic", "gemini": "google", "gpt": "openai"}

_DSML_RE = re.compile(r"<｜DSML｜invoke name=\"([^\"]+)\">(.*?)</｜DSML｜invoke>", re.S)
_DSML_PARAM_RE = re.compile(r"<｜DSML｜parameter name=\"([^\"]+)\"[^>]*>(.*?)</｜DSML｜parameter>", re.S)
_DSML_ANY_RE = re.compile(r"<｜DSML｜function_calls>.*?(?:</｜DSML｜function_calls>|$)", re.S)
_ERR_RE = re.compile(r"^\s*(?:Error|Exception)\b|Error executing tool|Traceback \(most recent call last\)|"
                     r"Code execution failed|InterpreterError")
_ID_KEYS = ("user_id", "customer_id", "phone_number", "email")
# tool_calling_with_shortlisting interleaves the agent's chat spans with the scaffold's tool-shortlisting chats
# (outputs like {"tools": [...]}) that cannot be told apart reliably -> excluded (AppWorld only, 300 sessions).
EXCLUDED_HARNESSES = {"tool_calling_with_shortlisting"}


def download(dest: Path | None = None) -> Path:
    from huggingface_hub import snapshot_download

    dest = Path(dest or RAW / SOURCE)
    snapshot_download(REPO, repo_type="dataset", revision=REVISION, local_dir=dest,
                      allow_patterns=["data/train/*.parquet", "README.md"])
    return dest


def family_of(model: str) -> str:
    return next((v for k, v in FAMILY.items() if model.startswith(k)), "other")


def _result_text(res) -> str:
    """Tool result -> text (results are often a JSON list of {type: text, text})."""
    if isinstance(res, str):
        try:
            res = json.loads(res)
        except (json.JSONDecodeError, ValueError):
            return res
    if isinstance(res, list):
        return "\n".join(str(x.get("text", "")) if isinstance(x, dict) else str(x) for x in res)
    if isinstance(res, dict):
        return str(res.get("text", json.dumps(res, ensure_ascii=False)))
    return "" if res is None else str(res)


def _msg_text(m: dict) -> str:
    out = []
    for p in m.get("parts") or []:
        if p.get("type") == "text":
            out.append(p.get("content") or "")
        elif p.get("type") == "tool_call_response":
            out.append(_result_text(p.get("result")))
    return "\n".join(x for x in out if x)


def _strip_prefix(name: str) -> str:
    return re.sub(r"^mcp__environment__", "", name or "")


def _smol_fields(text: str) -> tuple[str, str]:
    """(thought, code) of a smolagents step (JSON object, or 'Thought: ... Code: ```py ...```')."""
    t = text.strip()
    try:
        d = json.loads(t)
        if isinstance(d, dict):
            return str(d.get("thought") or ""), str(d.get("code") or "")
    except (json.JSONDecodeError, ValueError):
        pass
    m = re.search(r"```(?:py|python)?\s*\n(.*?)```", t, re.S)
    code = m.group(1) if m else ""
    return (t[:m.start()] if m else t), code


def _code_calls(code: str, known: set[str]) -> list[dict]:
    calls = []
    for m in re.finditer(r"(?<![\w.])([A-Za-z_][A-Za-z0-9_]*)\s*\(", code):
        if m.group(1) in known:
            calls.append({"name": m.group(1), "args": {"_code": _call_args(code, m.end())}})
    return calls


def _call_args(code: str, start: int) -> str:
    depth, i = 1, start
    while i < len(code) and depth:
        depth += {"(": 1, ")": -1}.get(code[i], 0)
        i += 1
    return code[start:i - 1][:500]


def _message_literals(code: str) -> list[str]:
    out = []
    for m in re.finditer(r"(?<![\w.])message\s*\(", code):
        arg = _call_args(code, m.end())
        lit = re.findall(r"'''(.*?)'''|\"\"\"(.*?)\"\"\"|'((?:\\.|[^'\\])*)'|\"((?:\\.|[^\"\\])*)\"", arg, re.S)
        out += ["".join(x) for x in lit]
    return out


def known_tools(rows) -> dict[str, set[str]]:
    """Tool names per benchmark seen as structured tool calls (used to find calls in smolagents code)."""
    out: dict[str, Counter] = {}
    for r in rows:
        c = out.setdefault(r["benchmark"], Counter())
        for s in r["spans"]:
            for m in json.loads(s["attributes"]["gen_ai.output.messages"] or "[]"):
                for p in m.get("parts") or []:
                    if p.get("type") == "tool_call":
                        c[_strip_prefix(p.get("name"))] += 1
    return {b: {n for n, k in c.items() if k >= 3 and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", n)}
            for b, c in out.items()}


def parse_step(out_msgs: list[dict], harness: str, known: set[str]) -> dict:
    texts, calls, nl = [], [], []
    for m in out_msgs:
        for p in m.get("parts") or []:
            if p.get("type") == "tool_call":
                args = p.get("arguments")
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except (json.JSONDecodeError, ValueError):
                        args = {"_raw": args}
                calls.append({"name": _strip_prefix(p.get("name")), "args": args if isinstance(args, dict) else
                              {"_raw": args}})
            elif p.get("type") == "text":
                t = p.get("content") or ""
                if harness == "smolagents_code":
                    thought, code = _smol_fields(t)
                    calls += _code_calls(code, known)
                    nl += [thought] + _message_literals(code)
                    texts.append(thought)
                    continue
                for inv in _DSML_RE.finditer(t):
                    calls.append({"name": _strip_prefix(inv.group(1)),
                                  "args": dict(_DSML_PARAM_RE.findall(inv.group(2)))})
                t = _DSML_ANY_RE.sub(" ", t).strip()
                if t in ("[empty]", "(no content)"):
                    t = ""
                texts.append(t)
                nl.append(t)
    for c in calls:
        if c["name"] == "message" and isinstance(c["args"], dict) and isinstance(c["args"].get("content"), str):
            nl.append(c["args"]["content"])
    nl_text = "\n\n".join(x.strip() for x in nl if x and x.strip())
    blocks = "\n\n".join("```\n" + c["name"] + "(" + json.dumps(c["args"], ensure_ascii=False, default=str)[:2000]
                         + ")\n```" for c in calls)
    return {"text": "\n\n".join(x for x in ("\n\n".join(t for t in texts if t.strip()), blocks) if x),
            "tool_calls": calls, "nl": nl_text}


def appworld_instruction(first_text: str, sys_text: str) -> str:
    for src in (first_text, sys_text):
        m = re.search(r"Task from supervisor:\s*\n(.*?)(?:\n\s*\n|\n<policy>|\n\s*Context:|$)", src, re.S)
        if m and m.group(1).strip():
            return re.sub(r"\s+", " ", m.group(1)).strip()
    return ""


_KWARG_RE = re.compile(r"(\w+)\s*=\s*(?:'([^']*)'|\"([^\"]*)\")")
# customer identifiers in the customer's messages / tool outputs (tau2 formats: user ids like anya_garcia_5901,
# e-mails, phone numbers 555-123-2002, retail order ids #W1234567)
_TEXT_ID_RES = (("user_id", re.compile(r"\b[a-z]+_[a-z]+_\d{3,5}\b")),
                ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[a-z]{2,}\b")),
                ("phone_number", re.compile(r"\b\d{3}-\d{3}-\d{4}\b")),
                ("order_id", re.compile(r"#w\d{7}\b")))


def _call_kwargs(c: dict) -> dict:
    a = c["args"] if isinstance(c.get("args"), dict) else {}
    raw = a.get("_code") or a.get("_raw")
    if isinstance(raw, str):  # smolagents code: keyword string literals
        return {m.group(1): m.group(2) if m.group(2) is not None else m.group(3) for m in _KWARG_RE.finditer(raw)}
    return a


def customer_ids(bench: str, steps: list[dict], texts: bool = True) -> list[str]:
    """tau2: identifiers of the customer / their objects (linked across sessions): keyword arguments of tool calls
    (incl. smolagents code) and, with texts=True, identifiers in the customer's messages and tool outputs.
    texts=False with plain dict arguments only = the first (pre-registered) grouping, kept as a sensitivity."""
    out = []
    for s in steps:
        for c in s.get("tool_calls") or []:
            a = _call_kwargs(c) if texts else (c["args"] if isinstance(c.get("args"), dict) else {})
            for k in _ID_KEYS + ("reservation_id", "order_id"):
                if isinstance(a.get(k), str) and a[k].strip():
                    out.append(f"{bench}/{k}={a[k].strip().lower()}")
            if {"first_name", "last_name", "zip"} <= set(a):
                out.append(f"{bench}/name_zip={a['first_name']}_{a['last_name']}_{a['zip']}".lower())
        if texts and s["role"] in ("user", "tool"):
            low = (s.get("text") or "").lower()
            for k, rx in _TEXT_ID_RES:
                out += [f"{bench}/{k}={v}" for v in rx.findall(low)]
    return list(dict.fromkeys(out))


def link_groups(ids_of: dict[str, list[str]]) -> dict[str, str]:
    """Union-find over sessions sharing an identifier -> {session: group id}; sessions without one stay alone."""
    parent: dict[str, str] = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for sess, ids in ids_of.items():
        for i in ids:
            parent[find("S:" + sess)] = find(i)
    comp: dict[str, list[str]] = {}
    for sess in ids_of:
        comp.setdefault(find("S:" + sess), []).append(sess)
    out = {}
    for root, sessions in comp.items():
        if root.startswith("S:"):  # no identifier at all
            for s in sessions:
                out[s] = f"noid/{s}"
        else:
            out_id = hashlib.sha1(root.encode()).hexdigest()[:12]
            for s in sessions:
                out[s] = f"cust/{out_id}"
    return out


_DECODER = json.JSONDecoder()


def _tail_after_last_assistant(s: str) -> list[dict]:
    """Messages after the last assistant message of a JSON message list, without parsing the (long, cumulative)
    history before it. Inside JSON strings quotes are escaped, so the marker can only match a message object."""
    i = s.rfind('{"role": "assistant"')
    if i < 0:
        return json.loads(s or "[]")
    _, end = _DECODER.raw_decode(s, i)
    rest = s[end:].lstrip().lstrip(",").strip()
    return json.loads("[" + rest) if rest.startswith("{") else []


def parse_session(row: dict, known: set[str]) -> dict:
    spans = sorted(row["spans"], key=lambda s: s.get("start_time") or "")
    first = json.loads(spans[0]["attributes"]["gen_ai.input.messages"] or "[]") if spans else []
    ins = [first] + [_tail_after_last_assistant(s["attributes"]["gen_ai.input.messages"] or "[]") for s in spans[1:]]
    outs = [json.loads(s["attributes"]["gen_ai.output.messages"] or "[]") for s in spans]
    first_text = _msg_text(first[0]) if first else ""
    sys_text = spans[0]["attributes"].get("gen_ai.system_instructions") or "" if spans else ""
    steps = []
    # the user / customer turns before the first agent output (tau2: first customer message)
    for m in first[1:] if first else []:
        if m.get("role") in ("user", "tool"):
            steps.append({"role": "user", "text": _msg_text(m), "step_idx": 0, "tool_calls": []})
    for i, out in enumerate(outs):
        st = parse_step(out, row["harness"], known)
        steps.append({"role": "assistant", "step_idx": i, **st})
        if i + 1 < len(ins):
            nxt = ins[i + 1]
            last_a = max((j for j, m in enumerate(nxt) if m.get("role") == "assistant"), default=-1)
            for m in nxt[last_a + 1:]:
                t = _msg_text(m)
                is_tool = m.get("role") == "tool" or any(p.get("type") == "tool_call_response"
                                                         for p in m.get("parts") or [])
                if is_tool or (row["harness"] == "smolagents_code" and m.get("role") == "user"):
                    steps.append({"role": "tool", "text": t, "step_idx": i, "tool_calls": [],
                                  "error": bool(_ERR_RE.search(t))})
                else:
                    steps.append({"role": "user", "text": t, "step_idx": i, "tool_calls": []})
    model = (row.get("models") or ["?"])[0]
    instr = appworld_instruction(first_text, sys_text) if row["benchmark"] == "appworld" else ""
    if row["benchmark"] == "appworld":
        task_id = "appworld/" + (hashlib.sha1(instr.encode()).hexdigest()[:12] if instr else "unknown")
    else:
        task_id = "pending"  # set by build(): customer groups linked across sessions
    return {"run_id": row["session_id"], "task_id": task_id, "agent_id": row["harness"], "model_id": model,
            "model_family": family_of(model), "benchmark": row["benchmark"], "label": int(not row["success"]),
            "meta": {"status": row["status"], "score": row["score"], "harness": row["harness"], "run": row["run_id"],
                     "exit_status": row["status"]},
            "task_text": instr or first_text[:4000], "steps": steps}


def iter_rows(raw_dir: Path | None = None, columns=None):
    import pyarrow.parquet as pq

    raw_dir = Path(raw_dir or RAW / SOURCE)
    cols = columns or ["session_id", "run_id", "harness", "benchmark", "models", "score", "success", "status", "spans"]
    for f in sorted((raw_dir / "data" / "train").glob("*.parquet")):
        pf = pq.ParquetFile(f)
        for g in range(pf.num_row_groups):
            for r in pf.read_row_group(g, columns=cols).to_pylist():
                if r["benchmark"] in BENCHMARKS:
                    yield r


def build(raw_dir: Path | None = None, out_dir: Path | None = None) -> pd.DataFrame:
    """Parse every session of the used benchmarks -> data/interim/exgentic/trajectories.jsonl.gz + excluded.csv."""
    out_dir = Path(out_dir or INTERIM / SOURCE)
    out_dir.mkdir(parents=True, exist_ok=True)
    known = known_tools(r for r in iter_rows(raw_dir) if r["harness"] not in EXCLUDED_HARNESSES)
    excl, ids_of, ids_args_of = [], {}, {}
    tmp = out_dir / "trajectories.tmp.jsonl.gz"
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        for r in iter_rows(raw_dir):
            reason = "infra_error" if r["status"] == "error" else None
            if r["harness"] in EXCLUDED_HARNESSES:
                reason = "harness_mixes_scaffold_calls"
            rec = None if reason else parse_session(r, known.get(r["benchmark"], set()))
            if rec is not None and not any(s["role"] == "assistant" for s in rec["steps"]):
                reason = "no_agent_steps"
            if reason:
                excl.append({"run_id": r["session_id"], "benchmark": r["benchmark"], "harness": r["harness"],
                             "label": int(not r["success"]), "reason": reason})
                continue
            if rec["benchmark"] != "appworld":
                ids_of[rec["run_id"]] = customer_ids(rec["benchmark"], rec["steps"])
                ids_args_of[rec["run_id"]] = customer_ids(rec["benchmark"], rec["steps"], texts=False)
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    groups, groups_args = link_groups(ids_of), link_groups(ids_args_of)
    n = 0
    with gzip.open(tmp, "rt", encoding="utf-8") as fi, \
            gzip.open(out_dir / "trajectories.jsonl.gz", "wt", encoding="utf-8") as fo:
        for line in fi:
            rec = json.loads(line)
            rec["task_id_args"] = rec["task_id"]
            if rec["benchmark"] != "appworld":
                rec["task_id"] = f"{rec['benchmark']}/{groups[rec['run_id']]}"
                rec["task_id_args"] = f"{rec['benchmark']}/{groups_args[rec['run_id']]}"
            fo.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n += 1
    tmp.unlink()
    log.info("exgentic: %d trajectories written, %d excluded", n, len(excl))
    ex = pd.DataFrame(excl, columns=["run_id", "benchmark", "harness", "label", "reason"])
    ex.to_csv(out_dir / "excluded.csv", index=False)
    return ex
