"""Step labelling of mini-SWE-agent bash commands (edit / verify events) for the demo notebook.

VERBATIM copies of the existing pipeline code, extracted automatically from the source files (no logic changes):
  src/g0_false_success.py  - command splitting and classify_segment (G0 step rules)
  src/e5_claims.py         - _norm_path, _edit_target, command_events, _norm_cmd (E5 events and edit targets)
The e5 functions refer to the G0 helpers as `g0.<name>`; here both live in one module, so `g0` is bound to this
module itself. Equality with the original code is checked by notebooks/build_demo_data.py.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

g0 = sys.modules[__name__]  # e5 code below calls g0._QUOTED_RE etc.

# ---------------------------------------------------------------- from src/g0_false_success.py

_HEREDOC_RE = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1([^\n]*)\n(.*?)\n[ \t]*\2[ \t]*(?=\n|$)", re.S)

_QUOTED_RE = re.compile(r"'[^']*'|\"(?:\\.|[^\"\\])*\"")

_SPLIT_RE = re.compile(r"&&|\|\||;|\n|\|")

_REDIRECT_RE = re.compile(r"(?<![0-9&<>])>{1,2}\s*(?!&)([^\s|&;<>]+)")

_TEE_RE = re.compile(r"\btee\s+(?:-a\s+)?([^\s|&;<>-][^\s|&;<>]*)")

_PY_WRITES_RE = re.compile(r"open\([^)]*['\"][wa][b+]?['\"]|\.write_text\(|\.write\(")

_ASSERT_RE = re.compile(r"\bassert\b")

_PLACEHOLDER_RE = re.compile(r"__HEREDOC(\d+)__")

# Writing command OUTPUT to these files is output capture, not a code edit (e.g. `git diff > patch.txt`, which the
# mini-swe-agent v2 submission protocol requires; `python repro.py > out.log`).
_OUTPUT_EXT = (".txt", ".log", ".out", ".err", ".patch", ".diff")

_TEST_RUNNERS = {"pytest", "py.test", "tox", "nosetests"}

_PREFIXES = {"sudo", "time", "nice", "env", "xvfb-run", "command", "exec"}

def _is_code_target(target: str) -> bool:
    t = target.strip("'\"")
    return bool(t) and not t.startswith("/dev/") and not t.lower().endswith(_OUTPUT_EXT)

def _words(noq: str) -> list[str]:
    """Command words of a segment with env assignments and wrappers (sudo, timeout N, ...) skipped."""
    w = noq.split()
    i = 0
    while i < len(w):
        if "=" in w[i] and not w[i].startswith("-"):
            i += 1
        elif w[i] in _PREFIXES:
            i += 1
        elif w[i] == "timeout":
            i += 2
        else:
            break
    return w[i:]

def _is_python(word: str) -> bool:
    return bool(re.fullmatch(r"(?:.*/)?python[0-9.]*", word))

def _is_runner_script(word: str) -> bool:
    b = word.rsplit("/", 1)[-1]
    return bool(re.fullmatch(r"run_?tests(?:\.py|\.sh)?", b)) or word.endswith("bin/test")

def _cut_heredocs(cmd: str) -> tuple[str, list[dict]]:
    """Replace heredoc bodies by placeholders; return (command, [{head, body}])."""
    docs = []

    def repl(m):
        docs.append({"tail": m.group(3), "body": m.group(4)})
        return f"__HEREDOC{len(docs) - 1}__{m.group(3)}"

    return _HEREDOC_RE.sub(repl, cmd), docs

def classify_segment(seg: str, docs: list[dict], files: dict[str, str]) -> str | None:
    """'verify' | 'edit' | None for one command segment (heredoc bodies already cut out into `docs`)."""
    noq = _QUOTED_RE.sub("''", seg)
    ph = _PLACEHOLDER_RE.search(seg)
    doc = docs[int(ph.group(1))] if ph else None
    plain = _PLACEHOLDER_RE.sub(" ", noq)
    w = _words(plain)
    w0 = w[0] if w else ""
    verify = edit = False

    # --- verification: a test runner in COMMAND position
    if w0.rsplit("/", 1)[-1] in _TEST_RUNNERS or (w0 == "make" and len(w) > 1 and w[1] == "test"):
        verify = True
    elif _is_runner_script(w0) or (w0 in ("bash", "sh") and len(w) > 1 and _is_runner_script(w[1])):
        verify = True
    elif _is_python(w0):
        rest = w[1:]
        if "-m" in rest and rest.index("-m") + 1 < len(rest) and \
                rest[rest.index("-m") + 1] in ("pytest", "py.test", "unittest", "nose"):
            verify = True
        args = [a for a in rest if not a.startswith("-")]
        if args and _is_runner_script(args[0]):
            verify = True
        code = ""
        if doc is not None:
            code = doc["body"]  # python [-] << EOF ... EOF
        elif "-c" in rest:
            code = " ".join(_QUOTED_RE.findall(seg))
        if code:
            verify = verify or bool(_ASSERT_RE.search(code))
            edit = bool(_PY_WRITES_RE.search(code))
        if args and args[0].endswith(".py"):
            content = files.get(args[0]) or files.get(Path(args[0]).name)
            if content and _ASSERT_RE.search(content):
                verify = True

    # --- edits
    if w0 == "edit" or any(t in ("apply_patch", "str_replace", "str_replace_editor") for t in w):
        edit = True
    if w0 == "sed" and any(re.match(r"^-[a-zA-Z]*i", t) or t.startswith("--in-place") for t in w[1:]):
        edit = True
    if w0 == "perl" and any(re.match(r"^-[a-zA-Z]*i", t) for t in w[1:]):
        edit = True
    if (w0 == "git" and len(w) > 1 and w[1] == "apply") or w0 == "patch":
        edit = True
    tee = _TEE_RE.search(plain)
    if tee and _is_code_target(tee.group(1)):
        edit = True
    red = _REDIRECT_RE.search(plain)
    if red and _is_code_target(red.group(1)):
        edit = True
        if doc is not None:  # heredoc written to a file: remember its content (assert detection later)
            files[red.group(1)] = doc["body"]
            files[Path(red.group(1)).name] = doc["body"]
    if verify:
        return "verify"  # verification wins inside one segment (e.g. `pytest > out.log`)
    return "edit" if edit else None


# ---------------------------------------------------------------- from src/e5_claims.py

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
