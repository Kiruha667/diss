"""E1: cleaning and segmentation of unified trajectories into fragments.

Three segmentations (AGENTS.md E1):
* ``reason_sent`` (main): role=assistant text only, prose split into sentences (blingfire), code split
  by lines with every fenced block truncated to its first 10 lines;
* ``all_sent``: the same, plus role=tool texts (a tool output is treated as a code/log block: by lines,
  first 10 lines);
* ``step``: one fragment per agent step (the assistant message) — control only.

Cleaning, applied before splitting: ANSI escapes removed; tracebacks longer than 20 lines cut to their
first 5 lines; leak markers (config/leak_markers.txt) replaced by [MASKED]. Per fragment: whitespace
normalised (no lowercasing); fragments shorter than 15 characters dropped.

The leading "THOUGHT:" label that mini-swe-agent v1 requires is format boilerplate and is removed, so
v1 and v2 trajectories are segmented alike.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

import blingfire

from src.common import MAX_N

MIN_FRAG_CHARS = 15
CODE_BLOCK_MAX_LINES = 10
TB_MAX_LINES, TB_KEEP = 20, 5

_ANSI_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_FENCE_SPLIT_RE = re.compile(r"(```[^\n]*\n.*?(?:```|$))", re.S)
_THOUGHT_RE = re.compile(r"^\s*THOUGHT\s*:?\s*", re.I)
_WS_RE = re.compile(r"\s+")
# clause boundaries for the E1 fallback (only used if sentence medians are < 200)
_CLAUSE_RE = re.compile(
    r"(?<=[;:])\s+|\s+[—–]\s+|\s+-\s+|,\s+(?=(?:and|but|or|so|because|which|while|although|then|since|however|"
    r"whereas|unless|until|if|when)\b)"
)


@dataclass
class Fragment:
    text: str
    step_idx: int
    role: str


def compile_markers(markers: list[str]) -> re.Pattern | None:
    """Case-insensitive literal markers; word boundaries where the marker starts/ends with a word char
    (so "resolved" does not hit "resolved_path")."""
    pats = []
    for m in sorted({m for m in markers if m}, key=len, reverse=True):
        p = re.escape(m)
        if re.match(r"\w", m[0]):
            p = r"\b" + p
        if re.match(r"\w", m[-1]):
            p = p + r"\b"
        pats.append(p)
    return re.compile("|".join(pats), re.I) if pats else None


def truncate_tracebacks(text: str) -> str:
    lines = text.split("\n")
    out, i = [], 0
    while i < len(lines):
        if "Traceback (most recent call last)" in lines[i]:
            j = i + 1
            while j < len(lines) and (lines[j].startswith((" ", "\t")) or not lines[j].strip()):
                j += 1
            if j < len(lines):  # the exception line closing the traceback
                j += 1
            block = lines[i:j]
            out.extend(block[:TB_KEEP] if len(block) > TB_MAX_LINES else block)
            i = j
        else:
            out.append(lines[i])
            i += 1
    return "\n".join(out)


def clean(text: str, marker_re: re.Pattern | None) -> str:
    text = _ANSI_RE.sub("", text or "")
    text = truncate_tracebacks(text)
    if marker_re is not None:
        text = marker_re.sub("[MASKED]", text)
    return text


def _norm(s: str) -> str:
    return _WS_RE.sub(" ", s).strip()


def _code_lines(block: str) -> list[str]:
    lines = [ln for ln in block.split("\n") if ln.strip()]
    return lines[:CODE_BLOCK_MAX_LINES]


def _fence_body(part: str) -> str:
    """"```lang\\n<body>```" -> "<body>" (an unterminated fence runs to the end of the text)."""
    body = part.split("\n", 1)[1] if "\n" in part else ""
    body = body.rstrip()
    return body[:-3] if body.endswith("```") else body


def _prose_sentences(prose: str, clauses: bool) -> list[str]:
    out = []
    for line in prose.split("\n"):
        line = _THOUGHT_RE.sub("", line) if line.lstrip().upper().startswith("THOUGHT") else line
        if not line.strip():
            continue
        for sent in blingfire.text_to_sentences(line.strip()).split("\n"):
            if clauses:
                out.extend(p for p in _CLAUSE_RE.split(sent) if p)
            else:
                out.append(sent)
    return out


def split_assistant(text: str, clauses: bool = False) -> list[str]:
    """Assistant message -> raw fragments (prose sentences + code lines, fenced blocks <= 10 lines)."""
    pieces = []
    for part in _FENCE_SPLIT_RE.split(text):
        if not part:
            continue
        if part.startswith("```"):
            pieces.extend(_code_lines(_fence_body(part)))
        else:
            pieces.extend(_prose_sentences(part, clauses))
    return pieces


def split_tool(text: str) -> list[str]:
    """Tool output -> first 10 non-empty lines (treated as a code/log block)."""
    return _code_lines(text)


def _keep(frags: list[str]) -> list[str]:
    out = []
    for f in frags:
        f = _norm(f)
        if len(f) >= MIN_FRAG_CHARS:
            out.append(f)
    return out


def _step_fragment(text: str) -> str:
    """Whole assistant step as one fragment, with fenced blocks cut to 10 lines."""
    parts = []
    for part in _FENCE_SPLIT_RE.split(text):
        if part.startswith("```"):
            parts.append("\n".join(_code_lines(_fence_body(part))))
        else:
            parts.append("\n".join(_THOUGHT_RE.sub("", ln) if ln.lstrip().upper().startswith("THOUGHT") else ln
                                   for ln in part.split("\n")))
    return _norm("\n".join(parts))


def segment(rec: dict, seg: str, marker_re: re.Pattern | None, clauses: bool = False) -> list[Fragment]:
    """All fragments of one trajectory for the given segmentation (full length, not truncated)."""
    frags: list[Fragment] = []
    for st in rec["steps"]:
        role = st["role"]
        if seg == "step":
            if role != "assistant":
                continue
            f = _step_fragment(clean(st["text"], marker_re))
            if len(f) >= MIN_FRAG_CHARS:
                frags.append(Fragment(f, st["step_idx"], role))
            continue
        if role == "assistant":
            raw = split_assistant(clean(st["text"], marker_re), clauses)
        elif role == "tool" and seg == "all_sent":
            raw = split_tool(clean(st["text"], marker_re))
        else:
            continue
        frags.extend(Fragment(t, st["step_idx"], role) for t in _keep(raw))
    return frags


def task_sentences(task_text: str, marker_re: re.Pattern | None) -> list[str]:
    """Task statement -> fragments for the task embedding (same cleaning and splitting rules)."""
    return _keep(split_assistant(clean(task_text, marker_re)))


def _call_hash(cmd: str) -> str:
    return hashlib.sha1(_norm(cmd).encode("utf-8")).hexdigest()[:12]


def segment_record(rec: dict, seg: str, marker_re: re.Pattern | None, clauses: bool = False,
                   max_n: int = MAX_N) -> dict:
    """Compact per-trajectory segmentation stored in segments_<seg>.jsonl.gz.

    Keeps only the first ``max_n`` fragments (nothing beyond max(N) is ever embedded or featurised),
    the total count (diagnostic only), and per-step hashes of executed commands (for the n_tool_calls
    and repeat_ratio baselines computed on the prefix).
    """
    frags = segment(rec, seg, marker_re, clauses)
    n_steps_total = 1 + max((s["step_idx"] for s in rec["steps"]), default=-1)
    calls: list[list[str]] = [[] for _ in range(n_steps_total)]
    for s in rec["steps"]:
        if s["role"] == "assistant" and s.get("tool_calls"):
            calls[s["step_idx"]] = [_call_hash(c) for c in s["tool_calls"]]
    return {
        "run_id": rec["run_id"],
        "task_id": rec["task_id"],
        "seg": seg,
        "n_frag_total": len(frags),
        "n_steps_total": n_steps_total,
        "frags": [{"t": f.text, "s": f.step_idx, "r": f.role} for f in frags[:max_n]],
        "calls": calls,
    }
