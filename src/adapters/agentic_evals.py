"""Adapter: ASSERT-KTH/agentic-evals-artifacts (Bjarnason, Silva, Monperrus, "On Randomness in Agentic Evals",
arXiv 2602.07150; CC BY 4.0), nano-agent runs -> unified trajectory records (E0 schema).

Source layout (Hugging Face dataset, pinned revision REVISION):
    <setting>/run_<k>/detailed_predictions.jsonl   one record per SWE-bench Verified instance
    <setting>/run_<k>/<name>.json                  SWE-bench harness results: resolved_ids, unresolved_ids,
                                                   empty_patch_ids, error_ids, ...
Label = 1 if the instance is in unresolved_ids, 0 if in resolved_ids. Instances with an empty patch or an
evaluation error have no label here (reports/E8_PROTOCOL.md, section 2).

nano-agent format (detailed_predictions.completion): assistant messages carry reasoning_content, content and at
most one tool call (shell {cmd} | apply_patch {file, search, replace, ...}); role=tool messages are the outputs;
role=user messages are the scaffold's nudges ("Use shell to explore or apply_patch to make changes. ...").
Mapping (protocol, section 3):
* step = assistant message; text = reasoning_content + content + the step's commands in ```bash blocks
  (the rule of swebench_bashonly._assistant_step);
* shell(cmd) -> cmd; apply_patch(file, search, replace) -> `sed -i 's/<h(search)>/<h(replace)>/' <file>`
  (h = first 12 hex digits of SHA-1): demo_lib then sees an in-place edit of that file, with the identity of the
  patch for repeat / distinct-command counts; a call of any other tool was not executed by nano-agent
  ("unknown tool: ...") -> no command;
* a shell output "command failed with exit code N" (N != 0) gets the prefix "returncode: N" (as the leaderboard
  adapter); apply_patch has no return code;
* user messages are kept as role=user, they are not steps.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Iterator

from src.common import OUTPUTS, RAW

SOURCE = "agentic_evals"
REPO = "ASSERT-KTH/agentic-evals-artifacts"
REVISION = "5db0c4b69382d160a313d7ceaded915398c63e13"
RAW_DIR = RAW / "agentic_evals"
# the three settings of reports/E8_PROTOCOL.md: nano-agent, temperature 0.6, one per model
SETTINGS = {"nano-agent-Qwen_Qwen3-32B": "Qwen/Qwen3-32B",
            "nano-agent-agentica-org_DeepSWE-Preview": "agentica-org/DeepSWE-Preview",
            "nano-agent-mistral_devstral-2512": "mistral/devstral-2512"}
# Settings of the repeat analyses (E8, E9, E10, E15). Since 2026-11-12 nano-agent x DeepSWE-Preview is excluded: its
# harness labels belong to another system (reports/KTH_DEEPSWE_LABELS.md; deviation in E8_PROTOCOL.md and
# E15_PROTOCOL.md). KTH_WITH_DEEPSWE=1 reproduces the earlier three-setting numbers (report appendices); their outputs
# go to outputs/kth_with_deepswe/. Data loading (E8 build, E10 parsing) still covers all three settings.
EXCLUDED_SETTINGS = ("nano-agent-agentica-org_DeepSWE-Preview",)
WITH_DEEPSWE = os.environ.get("KTH_WITH_DEEPSWE") == "1"
ANALYSIS_SETTINGS = tuple(s for s in SETTINGS if WITH_DEEPSWE or s not in EXCLUDED_SETTINGS)
KTH_OUTPUTS = OUTPUTS / "kth_with_deepswe" if WITH_DEEPSWE else OUTPUTS
TOOLS = ("shell", "apply_patch")
_FAILED_RE = re.compile(r"^command failed with exit code (-?\d+)")
_SAFE_PATH_RE = re.compile(r"^[A-Za-z0-9_./+@-]+$")


def _lit(x):
    """Fields may come as Python-literal strings ("None", "[{...}]") or as parsed objects."""
    if isinstance(x, str):
        if x in ("None", ""):
            return None
        try:
            return ast.literal_eval(x)
        except (ValueError, SyntaxError):
            return x
    return x


def _text(x) -> str:
    return x if isinstance(x, str) and x != "None" else ""


def _h(s) -> str:
    return hashlib.sha1(str(s).encode("utf-8")).hexdigest()[:12]


def patch_command(args: dict) -> str:
    """apply_patch(file, search, replace) as the equivalent in-place bash edit (see the module docstring)."""
    f = str(args.get("file") or "").strip()
    target = f if _SAFE_PATH_RE.match(f) else json.dumps(f)  # unusual path: quoted (target then unknown)
    return f"sed -i 's/{_h(args.get('search', ''))}/{_h(args.get('replace', ''))}/' {target}".rstrip()


def tool_calls_of(m: dict) -> list[tuple[str, str, dict | None]]:
    """[(call id, tool name, parsed arguments or None)] of one assistant message."""
    out = []
    for tc in _lit(m.get("tool_calls")) or []:
        fn = (tc or {}).get("function") or {}
        args = fn.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = None
        out.append((str(tc.get("id") or ""), str(fn.get("name") or ""), args if isinstance(args, dict) else None))
    return out


def command_of(name: str, args: dict | None) -> str | None:
    if args is None:
        return None
    if name == "shell":
        cmd = args.get("cmd")
        return cmd.strip() if isinstance(cmd, str) and cmd.strip() else None
    if name == "apply_patch":
        return patch_command(args)
    return None  # unknown tool: not executed by nano-agent


def clean_output(text: str, tool: str) -> str:
    m = _FAILED_RE.match(text) if tool == "shell" else None
    if m and m.group(1) != "0":
        return f"returncode: {m.group(1)}\n{text}"
    return text


def to_record(setting: str, run: str, raw: dict, label: int | None) -> dict:
    """One detailed_predictions record -> unified trajectory record."""
    dp = raw["detailed_predictions"]
    if isinstance(dp, str):
        dp = _lit(dp)
    task_id = raw["instance_id"]
    prompt = _lit(dp.get("prompt")) or []
    task_text = next((_text(m.get("content")) for m in prompt if m.get("role") == "user"), "")
    msgs = _lit(dp.get("completion")) or []
    steps, step_idx, call_tool = [], -1, {}
    n_unknown = n_bad_args = 0
    for m in msgs:
        role = m.get("role")
        if role == "assistant":
            step_idx += 1
            cmds, names = [], []
            for cid, name, args in tool_calls_of(m):
                call_tool[cid] = name
                names.append(name)
                cmd = command_of(name, args)
                if cmd is not None:
                    cmds.append(cmd)
                elif args is None:
                    n_bad_args += 1
                elif name not in TOOLS:
                    n_unknown += 1
            reasoning = _text(m.get("reasoning_content")).strip()
            content = _text(m.get("content")).strip()
            blocks = "\n\n".join(f"```bash\n{c}\n```" for c in cmds)
            text = "\n\n".join(x for x in (reasoning, content, blocks) if x)
            steps.append({"role": "assistant", "text": text, "step_idx": step_idx, "tool_calls": cmds,
                          "tool_names": names})
        elif role == "tool":
            tool = call_tool.get(str(m.get("tool_call_id") or ""), "")
            steps.append({"role": "tool", "text": clean_output(_text(m.get("content")), tool),
                          "step_idx": max(step_idx, 0), "tool_calls": [], "tool": tool})
        elif role == "user":
            steps.append({"role": "user", "text": _text(m.get("content")).strip(), "step_idx": max(step_idx, 0),
                          "tool_calls": []})
    last = msgs[-1] if msgs else {}
    return {
        "run_id": f"{setting}/{run}/{task_id}", "task_id": task_id, "agent_id": "nano-agent",
        "model_id": SETTINGS.get(setting, setting), "model_family": SETTINGS.get(setting, setting).split("/")[0],
        "source": SOURCE, "label": label, "task_text": task_text, "steps": steps,
        "meta": {"setting": setting, "run": run, "n_messages": len(msgs), "n_assistant": step_idx + 1,
                 "last_role": last.get("role", ""),
                 "ended_by_final_message": last.get("role") == "assistant" and not tool_calls_of(last),
                 "n_unknown_tool_calls": n_unknown, "n_unparsable_tool_calls": n_bad_args},
    }


def run_dirs(setting: str) -> list[Path]:
    return sorted((RAW_DIR / setting).glob("run_*"), key=lambda p: int(p.name.split("_")[1]))


def labels_of(run_dir: Path) -> dict[str, int | str]:
    """task -> 1 (unresolved) | 0 (resolved) | 'empty_patch' | 'eval_error' | 'not_evaluated'."""
    files = [p for p in run_dir.glob("*.json")]
    assert len(files) == 1, files
    e = json.loads(files[0].read_text(encoding="utf-8"))
    assert not set(e["resolved_ids"]) & set(e["unresolved_ids"])
    out: dict[str, int | str] = {t: "not_evaluated" for t in e["submitted_ids"]}
    for t in e["empty_patch_ids"]:
        out[t] = "empty_patch"
    for t in e["error_ids"]:
        out[t] = "eval_error"
    for t in e["unresolved_ids"]:
        out[t] = 1
    for t in e["resolved_ids"]:
        out[t] = 0
    return out


def iter_raw(run_dir: Path) -> Iterator[dict]:
    with open(run_dir / "detailed_predictions.jsonl", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)
