"""E10 (reports/E10_PROTOCOL.md): outcomes of the agent's own checks (block O). No LLM calls.

  python -m src.e10_check_outcomes checks        -> data/interim/e10/commands.parquet (every executed command of the
                                                    leaderboard population and of the KTH attempts with a patch;
                                                    checks with their parsed outcome)
  python -m src.e10_check_outcomes parser-check  -> reports/E10_parser_check.md (round 4: 8 checks per changed rule;
                                                    data/interim/e10/commands_r3.parquet, the round-3 build, for the
                                                    comparison)

Sources: leaderboard = data/interim/swebench_bashonly/trajectories.jsonl.gz (E0 adapter, full tool outputs; checked
byte for byte against 5 local .traj.json, data/interim/e10/source_check.json); KTH = data/raw/agentic_evals via
src.adapters.agentic_evals (E8). Non-zero return codes are a "returncode: N" prefix of the tool output in both.
A check = a command with a demo_lib 'verify' event or a test-runner invocation (a self-made script that writes the
repository is not a check); its outcome is parsed from the full output of the command (protocol, sections 3-4 and 11,
with the fixes of rounds 1-4 in reports/E10_parser_check.md). Categories: pass / fail / error_env / error_code / unknown
for project tests, script_ok / script_fail instead of pass / fail for self-made scripts. Round 4 also records, per
command, the first segment that edits the project's code (protocol 11.5).
"""
from __future__ import annotations

import json
import posixpath
import re
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from src.adapters.agentic_evals import SETTINGS, iter_raw, labels_of, run_dirs, to_record
from src.adapters.swebench_bashonly import _text_of, clean_tool_output
from src.common import INTERIM, REPORTS, ROOT, SEED, get_logger, read_jsonl_gz, trajectories_path

sys.path.insert(0, str(ROOT))
from demo_lib.phases import (_PLACEHOLDER_RE, _QUOTED_RE, _SPLIT_RE, _cut_heredocs, _edit_target,  # noqa: E402
                             _is_code_target, _is_python, _norm_path, _words, classify_segment)

log = get_logger("e10")
OUT_DIR = INTERIM / "e10"
LB_RAW = ROOT / "data" / "raw" / "swebench_bashonly"
# outcome categories after the author's review of the seed-44 sample (round 3): errors split into env / code,
# self-made scripts get their own categories
CATS = ("pass", "fail", "error_env", "error_code", "script_ok", "script_fail", "unknown")

# ------------------------------------------------------------------------------------------ test runners

_PROJECT_RUNNERS = {"runtests", "bin/test", "tox", "make test", "django test"}
_PATH_TESTS_RE = re.compile(r"(^|/)(tests|testing)(/|$)")
_LABEL_RE = re.compile(r"^[A-Za-z_][\w]*(\.[\w]+)+$")
_SPLIT_CAP_RE = re.compile(f"({_SPLIT_RE.pattern})")


def _segments_ops(cmd: str) -> tuple[list[tuple[str, str]], list[dict]]:
    """[(segment, operator after it)] split exactly as demo_lib.command_events (heredoc bodies cut out, quoted
    strings kept; empty segments kept, so that operators stay in place), and the heredoc bodies."""
    flat, docs = _cut_heredocs(cmd)
    quotes: list[str] = []

    def protect(m):
        quotes.append(m.group(0))
        return f"__QUOTE{len(quotes) - 1}__"

    parts = _SPLIT_CAP_RE.split(_QUOTED_RE.sub(protect, flat))
    out = []
    for i in range(0, len(parts), 2):
        seg = re.sub(r"__QUOTE(\d+)__", lambda m: quotes[int(m.group(1))], parts[i])
        out.append((seg, parts[i + 1] if i + 1 < len(parts) else ""))
    return out, docs


def _segments(cmd: str) -> list[str]:
    """Non-empty command segments split as in demo_lib (heredoc bodies cut out, quoted strings kept)."""
    return [s for s, _ in _segments_ops(cmd)[0] if s.strip()]


def _rc_informative(segops: list[tuple[str, str]], k: int) -> bool:
    """True if the return code of the whole command is the return code of segment k: every later command is
    joined to it by `&&` (by `|` too under `set -o pipefail`). After `|`, `;`, `||` or a new line the return code
    is that of a later command (round 3, item 5)."""
    pipefail = any("pipefail" in s for s, _ in segops[:k + 1])
    allowed = {"&&", "cont"} | ({"|"} if pipefail else set())
    ops, prev = [], segops[k][0]
    for i in range(k + 1, len(segops)):
        ops.append(segops[i - 1][1])
        seg = segops[i][0].strip()
        if not seg or seg.startswith("#"):
            continue
        real = [o for o in ops if o != "\n"]  # a new line right after an operator continues the command
        eff = real[0] if real else ("cont" if prev.rstrip().endswith("\\") else "\n")
        if eff not in allowed:
            return False
        ops, prev = [], segops[i][0]
    return True


_OPEN_W_RE = re.compile(r"\bopen\(\s*(?P<arg>[^,()]+?)\s*,\s*(?:mode\s*=\s*)?[rbfu]*['\"](?:[wax]|r\+)")
_WRITE_TEXT_RE = re.compile(r"(?:Path\(\s*(?P<lit>[^()]+?)\s*\)|(?P<var>[A-Za-z_]\w*))\.write_(?:text|bytes)\(")
_STR_RE = re.compile(r"^[rbfu]*(['\"])(?P<s>.*)\1$")


def _resolve_path(arg: str, code: str) -> str | None:
    """The literal path of a write target: a string literal, Path(literal) or a name assigned one; else None."""
    arg = arg.strip()
    m = _STR_RE.match(arg)
    if m:
        return m.group("s")
    m = re.match(r"^Path\(\s*(.+?)\s*\)$", arg)
    if m:
        return _resolve_path(m.group(1), code)
    if re.fullmatch(r"[A-Za-z_]\w*", arg):
        m = re.search(rf"^\s*{arg}\s*=\s*(?:Path\(\s*)?[rbfu]*(['\"])(?P<s>[^'\"]*)\1", code, re.M)
        if m:
            return m.group("s")
    return None


def _writes_repo(code: str) -> bool:
    """True if python code writes a file of the repository: open(…, 'w'|'a'|'x'|'r+') or .write_text / .write_bytes
    with a target under /testbed or relative. Targets under /tmp, other absolute paths, and unresolved targets named
    tmp / temp are not the repository; other unresolved targets count as the repository."""
    targets = [m.group("arg") for m in _OPEN_W_RE.finditer(code)]
    targets += [m.group("lit") or m.group("var") for m in _WRITE_TEXT_RE.finditer(code)]
    for t in targets:
        p = _resolve_path(t, code)
        if p is None:
            if re.search(r"tmp|temp", t, re.I):
                continue
            return True
        if re.search(r"\{[^}]*(tmp|temp)[^}]*\}", p, re.I) or p.startswith(("/tmp", "/var/tmp", "/dev/")) or \
                (p.startswith("/") and not p.startswith("/testbed")):
            continue
        return True
    return False


def _script_of(seg: str, docs: list[dict], files: dict[str, str]) -> tuple[str, str]:
    """(code, executed .py path) of a python segment, read the way demo_lib.classify_segment reads it."""
    w = _words(_PLACEHOLDER_RE.sub(" ", _QUOTED_RE.sub("''", seg)))
    if not w or not _is_python(w[0]):
        return "", ""
    rest = w[1:]
    args = [a for a in rest if not a.startswith("-")]
    ph = _PLACEHOLDER_RE.search(seg)
    code = docs[int(ph.group(1))]["body"] if ph else (" ".join(_QUOTED_RE.findall(seg)) if "-c" in rest else "")
    path = args[0] if args and args[0].endswith(".py") else ""
    if path and not code:
        code = files.get(path) or files.get(Path(path).name) or ""
    return code, path


# ---- round 4: paths from the current directory (item C) and files the agent created in the run (item D)
REPO_DIR = "/testbed"  # the repository in both corpora; every command starts there (no shell state between commands)
_UNKNOWN_DIR = "/?"  # `cd -`, `cd $VAR`, `cd ~`: a directory outside the repository


def _abs(path: str, cwd: str) -> str:
    p = path.strip("'\"")
    if not p.startswith("/"):
        if p.startswith(("~", "$")) or cwd == _UNKNOWN_DIR:
            return _UNKNOWN_DIR
        p = posixpath.join(cwd, p)
    return posixpath.normpath(p)


def _rel(path: str) -> str:
    """Repository-relative form of an absolute path; paths outside the repository stay absolute."""
    if path == REPO_DIR:
        return "."
    return path[len(REPO_DIR) + 1:] if path.startswith(REPO_DIR + "/") else path


def _resolve(path: str, cwd: str) -> str:
    return _rel(_abs(path, cwd))


def _cd_target(seg: str) -> str | None:
    w = _words(seg.strip().lstrip("("))
    if w and w[0] in ("cd", "pushd"):
        return w[1].strip("'\"") if len(w) > 1 else "~"
    return None


_CREATE_REDIR_RE = re.compile(r"(?<![0-9&<>])>(?!>)\s*(?!&)([^\s|&;<>]+)")  # `> file`, not `>>`, `2>`, `>&`
_TEE_ARGS_RE = re.compile(r"\btee\s+((?:-\S+\s+)*)([^\s|&;<>-][^\s|&;<>]*)")
_OPEN_CREATE_RE = re.compile(r"\bopen\(\s*(?P<arg>[^,()]+?)\s*,\s*(?:mode\s*=\s*)?[rbfu]*['\"][wx]")


def _py_code(seg: str, docs: list[dict]) -> str:
    w = _words(_PLACEHOLDER_RE.sub(" ", _QUOTED_RE.sub("''", seg)))
    if not w or not _is_python(w[0]):
        return ""
    ph = _PLACEHOLDER_RE.search(seg)
    return docs[int(ph.group(1))]["body"] if ph else (" ".join(_QUOTED_RE.findall(seg)) if "-c" in w[1:] else "")


def _py_reads(arg: str, code: str) -> bool:
    a = re.escape(arg.strip())
    return re.search(rf"open\(\s*{a}\s*(?:\)|,\s*(?:mode\s*=\s*)?[rbfu]*['\"]r(?!\+))|{a}\s*\)?\.read_(?:text|bytes)\(",
                     code) is not None


def created_files(seg: str, docs: list[dict], cwd: str) -> list[str]:
    """Files a segment creates (item D): `> file` of a code file (cat >, echo >, printf >), `tee file` without
    -a, python open(file, 'w'|'x') / .write_text of a literal path the same code does not read first (a file read and
    written back is modified, not created)."""
    plain = _PLACEHOLDER_RE.sub(" ", _QUOTED_RE.sub("''", seg))
    out = [m.group(1) for m in _CREATE_REDIR_RE.finditer(plain) if _is_code_target(m.group(1))]
    m = _TEE_ARGS_RE.search(plain)
    if m and "-a" not in m.group(1).split() and _is_code_target(m.group(2)):
        out.append(m.group(2))
    code = _py_code(seg, docs)
    if code:
        args = [m.group("arg") for m in _OPEN_CREATE_RE.finditer(code)]
        args += [m.group("lit") or m.group("var") for m in _WRITE_TEXT_RE.finditer(code)]
        for a in args:
            p = _resolve_path(a, code)
            if p is not None and not re.search(r"[{}]", p) and not _py_reads(a, code):
                out.append(p)
    return [_resolve(p, cwd) for p in out]


def reverted_files(seg: str, cwd: str) -> list[str]:
    """Files a segment restores from git (`git checkout [--] path`, `git restore path`): no longer the agent's."""
    w = _words(_QUOTED_RE.sub(lambda m: m.group(0).strip("'\""), seg))
    if len(w) < 3 or w[0] != "git" or w[1] not in ("checkout", "restore"):
        return []
    args = w[w.index("--") + 1:] if "--" in w else [a for a in w[2:] if not a.startswith("-") and ("/" in a or "." in a)]
    return [_resolve(a, cwd) for a in args if a not in (".", "HEAD")]


def edit_targets(seg: str, docs: list[dict], cwd: str, files: dict[str, str] | None = None) -> list[str]:
    """Files a segment writes, resolved from the current directory (item F: edits of project code): `>` / `>>`,
    tee, sed / perl -i, python writes (code of a heredoc, of `-c`, or of a script file written earlier in the run and
    run now, e.g. `python /tmp/patch.py`); '*' for `git apply` / `patch` (a diff of the repository)."""
    plain = _PLACEHOLDER_RE.sub(" ", _QUOTED_RE.sub("''", seg))
    w = _words(plain)
    out = []
    for m in re.finditer(r"(?<![0-9&<>])>{1,2}\s*(?!&)([^\s|&;<>]+)", plain):
        if _is_code_target(m.group(1)):
            out.append(m.group(1))
    m = _TEE_ARGS_RE.search(plain)
    if m and _is_code_target(m.group(2)):
        out.append(m.group(2))
    if w and w[0] in ("sed", "perl") and any(re.match(r"^-[a-zA-Z]*i", t) or t.startswith("--in-place")
                                             for t in w[1:]) and not w[-1].startswith("-") and w[-1] != "''":
        out.append(w[-1])
    code = _py_code(seg, docs) or (_script_of(seg, docs, files)[0] if files else "")
    if code:
        for t in [m.group("arg") for m in _OPEN_W_RE.finditer(code)] + \
                 [m.group("lit") or m.group("var") for m in _WRITE_TEXT_RE.finditer(code)]:
            p = _resolve_path(t, code)
            if p is not None:
                out.append(p)
    res = [_resolve(p, cwd) for p in out]
    if w and ((w[0] == "git" and w[1:2] == ["apply"]) or w[0] == "patch"):
        res.append("*")
    return res


_TESTFILE_RE = re.compile(r"(^|/)(tests?|testing)/|(^|/)(test_[^/]*|[^/]*_tests?\.py|conftest\.py)$")


def is_code_file(path: str, repo: str) -> bool:
    """A source file of the repository that is not a test file (item F); '*' (a diff applied) counts."""
    return path == "*" or (_is_project_file(path, repo) and not _TESTFILE_RE.search(path))


def _label_files(label: str, base: str) -> list[str]:
    """Candidate files of a dotted test label run from directory `base` (a module, or a module + class / method)."""
    parts = label.split(".")
    out = []
    for i in range(len(parts), 0, -1):
        stem = "/".join(parts[:i])
        out += [_resolve(stem + ".py", base), _resolve(stem + "/__init__.py", base)]
    return out


def runner_of(seg: str) -> tuple[str, list[str]] | None:
    """(runner name, its arguments) if the segment invokes a test runner in command position, else None."""
    w = _words(_QUOTED_RE.sub(lambda m: m.group(0).replace(" ", "_"), seg))
    if not w:
        return None
    w0, base = w[0], w[0].rsplit("/", 1)[-1]
    if base in ("pytest", "py.test", "nosetests"):
        return base, w[1:]
    if base == "tox":
        return "tox", w[1:]
    if w0 == "make" and len(w) > 1 and w[1] == "test":
        return "make test", w[2:]
    if w0.endswith("bin/test"):
        return "bin/test", w[1:]
    if re.fullmatch(r"runtests(\.py)?", base):
        return "runtests", w[1:]
    if base in ("django-admin", "django-admin.py", "manage.py") and len(w) > 1 and w[1] == "test":
        return "django test", w[2:]
    if _is_python(w0) or w0 in ("bash", "sh"):
        rest = w[1:]
        if "-m" in rest and rest.index("-m") + 1 < len(rest):
            mod = rest[rest.index("-m") + 1]
            after = rest[rest.index("-m") + 2:]
            if mod in ("pytest", "py.test"):
                return "pytest", after
            if mod in ("unittest", "nose"):
                return mod, after
            if mod == "django" and after[:1] == ["test"]:
                return "django test", after[1:]
        args = [a for a in rest if not a.startswith("-")]
        if args:
            b = args[0].rsplit("/", 1)[-1]
            if re.fullmatch(r"runtests(\.py)?", b):
                return "runtests", args[1:]
            if args[0].endswith("bin/test"):
                return "bin/test", args[1:]
            if b == "manage.py" and args[1:2] == ["test"]:
                return "django test", args[2:]
    return None


def is_project_run(runner: str, args: list[str], cwd: str = REPO_DIR, base: str | None = None,
                   created: frozenset[str] = frozenset()) -> bool:
    """Project tests (protocol, section 3). Round 4: a path is resolved from the current directory (`cd` earlier in
    the command, item C) when that directory is inside the repository; from outside it (`cd /root`, `cd /tmp`) a
    relative path is read as written, i.e. from the repository root: the agent meant the repository's file and is in
    the wrong directory (such runs end with `file or directory not found`). A label is resolved from `base` (the
    directory of runtests.py, else the current one). A run whose every target is a file the agent created earlier in
    the run is the agent's own script (item D)."""
    targets = [a.strip("'\"") for a in args if not a.startswith("-")]
    inside = not _resolve(".", cwd).startswith("/")
    tg = []  # (in the project's tests, created by the agent)
    for a in targets:
        if "/" in a or a.endswith(".py") or "::" in a:
            path = a.split("::")[0]
            p = _resolve(path, cwd if inside else REPO_DIR)
            tg.append((not p.startswith(("/", "..")) and _PATH_TESTS_RE.search(p) is not None,
                       _resolve(path, cwd) in created))
        elif _LABEL_RE.match(a):
            tg.append((a.startswith("tests.") or ".tests." in a,
                       any(f in created for f in _label_files(a, base or cwd))))
    if tg and all(own for _, own in tg):
        return False
    if runner in _PROJECT_RUNNERS:
        return True
    if any(proj and not own for proj, own in tg):
        return True
    return not tg and inside  # no target: the runner collects the tests of the current directory


# ------------------------------------------------------------------------------------------ outcome parser

_PYTEST_RE = re.compile(r"^[=\s!*-]*(?P<body>(?:\d+ (?:failed|passed|errors?|skipped|xfailed|xpassed|deselected|"
                        r"warnings?|rerun)(?:,\s*)?)+|no tests ran)\s+in\s+[\d.]+\s*(?:s|sec|seconds)\b", re.M)
_RAN_RE = re.compile(r"^Ran (\d+) tests? in ([\d.]+)s", re.M)
_UT_STATUS_RE = re.compile(r"^(OK\b.*|FAILED \((?P<det>[^)]*)\))", re.M)
# round 3: a unittest status line without its "Ran N tests" line (the agent filtered the output, e.g. `| grep FAILED`)
_UT_ALONE_RE = re.compile(r"^(?:OK(?: \((?:skipped|expected failures|unexpected successes)=[^)]*\))?|"
                          r"FAILED \((?P<det>(?:failures|errors|skipped|expected failures|unexpected successes)=[^)]*)\))"
                          r"[ \t]*$", re.M)
_SYMPY_RE = re.compile(r"tests finished:(?P<body>[^\n]*(?:\n[^\n]*)?)in [\d.]+ seconds")  # may wrap once
# fixes after the seed-42 parser check (reports/E10_parser_check.md, section "Исправления"):
# (a) ANSI colour codes are stripped before parsing; (b) IndentationError / TabError are SyntaxError subclasses;
# (c) unittest.loader._FailedTest (a test that could not be loaded) is a collection error; (d) a pytest summary with
# only skipped / xfailed / xpassed has no pass/fail outcome; (e) runner calls with only informational flags
# (--version, --help, --collect-only, ...) are not checks
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_INFO_FLAGS = {"--version", "-V", "--help", "-h", "--collect-only", "--co", "--fixtures", "--markers"}
_IGNORABLE = {"cd", "source", "export", "echo", "head", "tail", "grep", "set"}
_PIPE_RE = re.compile(r"\|\s*(head|tail|grep)\b")
_LONG_RE = re.compile(r"too long|characters elided|output truncated", re.I)
_TIMEOUT_RE = re.compile(r"timed out|TimeoutExpired", re.I)
_RC_RE = re.compile(r"^returncode: (-?\d+)\n")

# ---- round 3: kind of an error outcome — 'env' (the agent could not run the check) or 'code' (project code broken)
# SWE-bench Verified repositories: (import packages, top-level source directories under /testbed)
_PROJECT = {
    "astropy": ({"astropy"}, {"astropy"}),
    "django": ({"django", "tests"}, {"django", "tests"}),
    "flask": ({"flask", "tests"}, {"src", "tests"}),
    "matplotlib": ({"matplotlib", "mpl_toolkits"}, {"lib"}),
    "pylint": ({"pylint", "tests"}, {"pylint", "tests"}),
    "pytest": ({"_pytest", "pytest", "testing"}, {"src", "testing"}),
    "requests": ({"requests", "tests"}, {"requests", "tests"}),
    "scikit-learn": ({"sklearn"}, {"sklearn"}),
    "seaborn": ({"seaborn", "tests"}, {"seaborn", "tests"}),
    "sphinx": ({"sphinx", "tests"}, {"sphinx", "tests"}),
    "sympy": ({"sympy"}, {"sympy"}),
    "xarray": ({"xarray"}, {"xarray"}),
}
# explicit messages of a check that could not be run (searched in this order; the first hit decides)
_ENV_RULES = [
    ("env-runner-missing", re.compile(r"No module named '?(?:pytest|py\.test|nose)'?[ \t]*$|"
                                      r"\b(?:pytest|py\.test|nosetests|tox): command not found", re.M)),
    ("env-usage", re.compile(r"error: (?:unrecognized arguments|argument |the following arguments are required|"
                             r"no such option)|^ERROR: usage:", re.M)),
    ("env-path", re.compile(r"can't open file|^(?:/usr)?(?:/bin/)?(?:ba|da|z)?sh: .*(?:No such file or directory|"
                            r"not found)[ \t]*$|: command not found|ERROR: (?:file or directory not found|"
                            r"file not found|not found|module or package not found)\b", re.M)),
    ("env-path", re.compile(r"Test label path \S+ does not exist")),  # django runtests
    ("env-nothing-ran", re.compile(r"no tests ran|collected 0 items[ \t]*$|^Ran 0 tests", re.M)),
    ("env-settings", re.compile(r"settings are not configured|Apps aren't loaded yet|AppRegistryNotReady|"
                                r"isn't in (?:an application in )?INSTALLED_APPS|doesn't declare an explicit app_label|"
                                r"^(?:E\s+)?django\.core\.exceptions\.ImproperlyConfigured:", re.M)),
    ("env-invocation", re.compile(r"attempted relative import (?:beyond top-level package|with no known parent)")),
    # compiled extensions of the repository are not built for this interpreter
    ("env-build", re.compile(r"undefined symbol: |has not been built correctly|cannot open shared object file|"
                             r"without building the extension modules|cannot import name '_compiler'")),
]
_NOMOD_RE = re.compile(r"No module named '?(?P<m>[\w.]+)'?")
_CANNOT_IMPORT_RE = re.compile(r"cannot import name '?(?P<n>\w+)'?(?: from (?:partially initialized module )?"
                               r"'?(?P<m>[\w.]+)'?)?")
_SYNTAX_RE = re.compile(r"^(?:E\s+)?(?:SyntaxError|IndentationError|TabError)\b", re.M)
_NOATTR_RE = re.compile(r"(?:module|type object) '(?P<m>[\w.]+)' has no attribute '(?P<a>\w+)'")
_FAILEDTEST_RE = re.compile(r"(?P<a>[\w./-]+) \(unittest\.loader\._FailedTest(?:\.[\w./-]+)?\)|"
                            r"Failed to import test module: (?P<b>[\w./-]+)")  # a path given as label: '/tmp/t'
_COLLECT_RE = re.compile(r"errors? during collection|ImportError while importing test module|^_* ?ERROR collecting",
                         re.M)
_FRAME_RE = re.compile(r'File "(?P<a>[^"]+)", line \d+|^(?:E\s+)?(?P<b>[^\s:"]+\.py|<frozen [^>]+>):\d+: in ', re.M)
_FRAME_FN_RE = re.compile(r'File "(?P<a>[^"]+)", line \d+, in (?P<fa>\S+)|^(?:E\s+)?(?P<b>[^\s:"]+\.py|<frozen [^>]+>)'
                          r':\d+: in (?P<fb>\S+)', re.M)
# an ImportError with its own message (not "cannot import name", "No module named" or a loader header)
_IMPORT_MSG_RE = re.compile(r"^(?:E\s+)?ImportError: (?!cannot import name|No module named|Failed to import test module|"
                            r"attempted relative import)(?P<msg>.+)$", re.M)
_EXC_LINE_RE = re.compile(r"^(?:E\s+)?(?P<exc>(?:[A-Za-z_]\w*\.)*[A-Z]\w*(?:Error|Exception|Exit|NotReady|"
                          r"Configured|DoesNotExist))\b(?::|$)", re.M)


def repo_of(task_id: str) -> str:
    return task_id.split("__", 1)[1].rsplit("-", 1)[0] if "__" in task_id else ""


def _is_project_file(path: str | None, repo: str, own: frozenset[str] = frozenset()) -> bool:
    """A file of the repository under test: /testbed/<source dir>/… or the same path relative to /testbed; not a file
    the agent created earlier in the run (`own`, round 4, item D)."""
    if not path:
        return False
    p = path.strip()
    if p.startswith("/testbed/"):
        p = p[len("/testbed/"):]
    elif p.startswith(("/", "<")):
        return False
    p = re.sub(r"^(\./)+", "", p)
    return "/" in p and p.split("/", 1)[0] in _PROJECT.get(repo, (set(), set()))[1] and p not in own


def _frame_before(output: str, pos: int) -> str | None:
    last = None
    for m in _FRAME_RE.finditer(output, 0, pos):
        last = m.group("a") or m.group("b")
    return last


def _label_hit(name: str, labels: list[str]) -> bool:
    return any(lb == name or lb.startswith(name + ".") for lb in labels)


def labels_of_runs(runners: list[tuple[str, list[str]]]) -> list[str]:
    """Test labels given to the runners: dotted labels as given, paths also in dotted module form."""
    out = []
    for _, args in runners:
        for a in args:
            a = a.strip("'\"")
            if not a or a.startswith("-"):
                continue
            out.append(a)
            p = re.sub(r"^(\./|/testbed/)+", "", a.split("::")[0])
            if p.endswith(".py"):
                out.append(p[:-3].replace("/", "."))
    return out


def error_kind(output: str, labels: list[str], repo: str, crash: bool = True,
               own: frozenset[str] = frozenset()) -> tuple[str, str] | None:
    """(kind, matched text) of an error outcome, None if the output has no such evidence.

    1. an explicit message that the check could not be run (_ENV_RULES) -> env-…;
    2. else the last missing-module / cannot-import / syntax error / missing test attribute in the output:
       - the missing module or attribute is a test label given to the runner -> env-label;
       - the module is outside the repository (a third-party package, the agent's own module) -> env-module-outside;
       - the module is the repository's top-level package itself -> env-project-not-importable;
       - raised in a file of the repository -> code-import / code-syntax;
       - raised elsewhere (the agent's own script or test file, `python -c`) -> env-script-import / -syntax;
       an ImportError with its own message names no repository package (e.g. `numpy.core.multiarray failed to
       import`) -> env-module-outside;
    3. else a test that could not be loaded (unittest _FailedTest) without these details -> ?-failedtest;
    4. else (crash=True) the last exception line, by its traceback:
       - raised while a module of the repository was being loaded (from the innermost `<module>` frame down to the
         exception, every frame is a repository file) -> code-traceback;
       - raised in a repository file at run time -> ?-runtime (no evidence that the code is broken);
       - raised elsewhere -> env-traceback.
    A file the agent created in the run (`own`) is not a repository file (round 4, item D)."""
    def proj(p):
        return _is_project_file(p, repo, own)

    for why, rx in _ENV_RULES:
        m = rx.search(output)
        if m:
            return why, m.group(0).strip()
    pkgs = _PROJECT.get(repo, (set(), set()))[0]
    hits = []
    for m in _NOMOD_RE.finditer(output):
        name = m.group("m")
        if _label_hit(name, labels):
            kind = "env-label"
        elif name.split(".")[0] not in pkgs:
            kind = "env-module-outside"
        elif "." not in name:  # the repository's own top-level package: the interpreter does not see the project
            kind = "env-project-not-importable"
        else:
            kind = "code-import" if proj(_frame_before(output, m.start())) else "env-script-import"
        hits.append((m.start(), kind, m.group(0)))
    for m in _CANNOT_IMPORT_RE.finditer(output):
        mod = m.group("m")
        if mod and mod.split(".")[0] not in pkgs:
            kind = "env-module-outside"
        else:
            kind = "code-import" if proj(_frame_before(output, m.start())) else "env-script-import"
        hits.append((m.start(), kind, m.group(0)))
    for m in _SYNTAX_RE.finditer(output):
        kind = "code-syntax" if proj(_frame_before(output, m.start())) else "env-script-syntax"
        hits.append((m.start(), kind, m.group(0).strip()))
    for m in _NOATTR_RE.finditer(output):
        name = f"{m.group('m')}.{m.group('a')}"
        if _label_hit(name, labels) or any(lb.endswith("." + name) for lb in labels):
            hits.append((m.start(), "env-label", m.group(0)))
    for m in _IMPORT_MSG_RE.finditer(output):
        if not any(re.search(rf"(?<![\w.]){re.escape(p)}(?![\w])", m.group("msg")) for p in pkgs):
            hits.append((m.start(), "env-module-outside", m.group(0).strip()))
    if hits:
        _, kind, line = max(hits)
        return kind, line
    m = _FAILEDTEST_RE.search(output)
    if m:
        return "?-failedtest", m.group(0)
    if crash:
        exc = None
        for exc in _EXC_LINE_RE.finditer(output):
            pass
        if exc is not None:
            if not proj(_frame_before(output, exc.start())):
                return "env-traceback", exc.group(0).strip()
            start = max(output.rfind("Traceback", 0, exc.start()), 0)
            frames = [(m.group("a") or m.group("b"), m.group("fa") or m.group("fb"))
                      for m in _FRAME_FN_RE.finditer(output, start, exc.start())]
            mods = [i for i, (_, fn) in enumerate(frames) if fn == "<module>"]
            if mods and all(proj(p) for p, _ in frames[mods[-1]:]):
                return "code-traceback", exc.group(0).strip()
            return "?-runtime", exc.group(0).strip()
    return None


def _n(body: str, word: str) -> int:
    return sum(int(x) for x in re.findall(rf"(\d+) {word}", body))


_REAL_ERROR_RE = re.compile(r"^ERROR: \S+ \((?![^)\n]*_FailedTest)", re.M)  # an error inside a test that ran


def _ut_outcome(n_ran: int | None, det: str | None, output: str, labels: list[str], repo: str,
                own: frozenset[str] = frozenset(), secs: float | None = None) -> tuple[str, str | None, int]:
    """(category, error kind if fixed by the summary, n_failed) of a unittest status line. `FAILED` with errors only
    is `error` when the errors are tests that could not be loaded: as many `_FailedTest` as errors, or every test
    that ran is an error and the output shows why it could not run (error_kind without the crash rule, e.g.
    `No module named '<label>'` when `| tail` cut off the `_FailedTest` line); otherwise `fail` (round 3, item 2).
    Round 4, item E: every test that ran is an error, in 0.000 s, and no error of a test that ran is visible (the
    agent's grep or tail removed the lines) -> `error` too, kind not determined (?-not-loaded)."""
    if n_ran == 0:
        return "error", "env-nothing-ran", 0
    if det is None:  # OK
        return "pass", None, 0
    fl = int((re.search(r"failures=(\d+)", det) or [0, 0])[1])
    er = int((re.search(r"errors=(\d+)", det) or [0, 0])[1])
    names = {m.group("a") or m.group("b") for m in _FAILEDTEST_RE.finditer(output)}
    if fl == 0 and er > 0:
        if len(names) >= er or (er == n_ran and error_kind(output, labels, repo, crash=False, own=own) is not None):
            return "error", None, 0
        if er == n_ran and secs == 0 and not _REAL_ERROR_RE.search(output):
            return "error", "?-not-loaded", 0
    return "fail", None, fl + er


# ---- round 4, item A: failed tests named in an output without a summary line (filtered or truncated)
_FAILED_TEST_RES = [re.compile(r"^FAILED (?P<t>\S+::\S+)", re.M),                 # pytest short test summary
                    re.compile(r"^(?P<t>\S+::\S+) FAILED\b", re.M),               # pytest -v
                    re.compile(r"^FAIL: (?P<t>test\w* \([^)\n]*\))", re.M),       # unittest / django
                    re.compile(r"^ERROR: (?P<t>test\w* \((?![^)\n]*_FailedTest)[^)\n]*\))", re.M)]


def _failed_tests(output: str) -> list[tuple[str, str, bool]]:
    """[(test id, its line, is an error)] of the failed tests named in the output, in order, each test once; 'is an
    error' — named only by unittest `ERROR: …` (an exception inside the test), not by FAILED / FAIL."""
    seen = {}
    for i, rx in enumerate(_FAILED_TEST_RES):
        for m in rx.finditer(output):
            t = m.group("t")
            if t not in seen:
                end = output.find("\n", m.start())
                seen[t] = (m.start(), output[m.start(): end if end >= 0 else None].strip(), i == 3)
    return [(t, ln, err) for t, (_, ln, err) in sorted(seen.items(), key=lambda x: x[1][0])]


# ---- round 4, item B: outcome of a self-made script that crashed or reported a failure
_TB_HEAD = "Traceback (most recent call last)"
# the exception line closing a traceback: a (dotted) exception class name, then ": message" or nothing. stdout may be
# interleaved with the traceback (mini-SWE-agent merges the streams), so other unindented lines are skipped
_EXC_TAIL_RE = re.compile(r"^(?P<exc>(?:[A-Za-z_]\w*\.)*(?:[A-Z]\w*)?(?:Error|Exception|Exit|Interrupt|Warning|"
                          r"Iteration|Failure|Failed|Fail|DoesNotExist|ObjectsReturned|Configured|NotReady|Skipped|"
                          r"Timeout))(?::[ \t]?(?P<msg>.*))?$")
_ASSERT_LINE_RE = re.compile(r"^\s*(?:E\s+)?AssertionError\b", re.M)
_MARK_RE = re.compile(r"\bFAIL(?:ED|URE|S)?\b|[✗✘❌]|(?<!\b0 )\b[Ff]ailed\b")
_NANO_RC_LINE_RE = re.compile(r"^command failed with exit code -?\d+\. Error output:[ \t]*$")  # KTH wrapper
_WARNING_LINE_RE = re.compile(r":\d+: \w*Warning: ")


def _drop_ignored(output: str) -> str:
    """The output without the tracebacks Python prints for exceptions it ignores (`Exception ignored in: …`, e.g. in
    __del__ at interpreter shutdown): they do not end the script."""
    if "Exception ignored in" not in output:
        return output
    out, skip = [], 0  # skip: 0 no, 1 header expected, 2 inside the traceback
    for ln in output.split("\n"):
        if ln.startswith("Exception ignored in"):
            skip = 1
            continue
        if skip == 1 and ln.startswith(_TB_HEAD):
            skip = 2
            continue
        if skip == 2:
            if not ln.strip() or ln[0] in " \t":
                continue
            skip = 0
            if _EXC_TAIL_RE.match(ln.rstrip()):
                continue
        skip = 0
        out.append(ln)
    return "\n".join(out)


def _mark_text(output: str) -> str:
    """Lines where failure markers count: not the KTH wrapper line, not Python warnings (and their source line), not
    the frames and code lines of a traceback (its exception line stays)."""
    out, in_tb, after_warn = [], False, False
    for ln in output.split("\n"):
        indented = bool(ln) and ln[0] in " \t"
        if after_warn and indented:
            after_warn = False
            continue
        after_warn = False
        if _NANO_RC_LINE_RE.match(ln) or ln.startswith(_TB_HEAD):
            in_tb = ln.startswith(_TB_HEAD)
            continue
        if _WARNING_LINE_RE.search(ln):
            after_warn = True
            continue
        if in_tb and (indented or not ln.strip()):
            continue
        if in_tb and _EXC_TAIL_RE.match(ln.rstrip()):
            in_tb = False
        out.append(ln)
    return "\n".join(out)


def _last_exception(output: str) -> tuple[str | None, str, list[tuple[str, str]]] | None:
    """(exception, its line, frames [(file, function)]) of the last traceback in the output; None if there is no
    traceback; exception None and line '' if its exception line is not in the output (cut off)."""
    i = output.rfind(_TB_HEAD)
    if i < 0:
        return None
    pos = output.find("\n", i)
    while 0 <= pos < len(output):
        nxt = output.find("\n", pos + 1)
        ln = output[pos + 1: nxt if nxt >= 0 else len(output)]
        if ln.strip() and ln[0] not in " \t":
            m = _EXC_TAIL_RE.match(ln.rstrip())
            if m:
                frames = [(f.group("a") or f.group("b"), f.group("fa") or f.group("fb"))
                          for f in _FRAME_FN_RE.finditer(output, i, pos + 1)]
                return m.group("exc"), ln.strip(), frames
        if nxt < 0:
            break
        pos = nxt
    return None, "", [(f.group("a") or f.group("b"), f.group("fa") or f.group("fb"))
                      for f in _FRAME_FN_RE.finditer(output, i)]


def _script_outcome(output: str, repo: str, own: frozenset[str], rc_fail: bool):
    """Round 4, item B. A self-made script failed (`fail`, later script_fail) only on evidence about the project: an
    AssertionError, an exception with a frame in a repository file, or explicit failure markers (FAIL, ✗, failed)
    with a non-zero return code of the check. A crash for any other reason is the check's own error (error_env):
    printing a non-ASCII character to an ASCII terminal (env-script-encoding), an exception raised in an installed
    library or the standard library with no repository frame (env-script-library), an exception of the script's own
    code (env-script-exception). SyntaxError / ImportError of the script are rule 2 (env-script-syntax / -import).
    A traceback whose exception line is cut off counts by its frames: a repository frame -> fail, else markers, else
    unknown. Tracebacks of exceptions Python ignores (`Exception ignored in`) are dropped first. Markers are searched
    outside traceback frames, Python warnings and the KTH wrapper line.
    Returns (category, rule, kind, line, n_failed) or None."""
    output = _drop_ignored(output)
    mk = _MARK_RE.search(_mark_text(output)) if rc_fail else None
    le = _last_exception(output)
    if le is not None:
        exc, line, frames = le
        name = (exc or "").rsplit(".", 1)[-1]
        in_proj = any(_is_project_file(p, repo, own) for p, _ in frames)
        if name in ("UnicodeEncodeError", "UnicodeDecodeError") and not (frames and
                                                                         _is_project_file(frames[-1][0], repo, own)):
            return "error", "3-script-error", "env-script-encoding", line, 0
        if name == "AssertionError":
            return "fail", "3-script-assert", "", line, 1
        if in_proj:
            return "fail", "3-script-project-frame", "", line or _TB_HEAD, 1
        if mk:
            return "fail", "3-script-marker", "", mk.group(0), 1
        if exc is None:  # exception line cut off, no repository frame among the visible ones
            return "unknown", "3-script-traceback-cut", "", _TB_HEAD, 0
        lib = bool(frames) and re.search(r"site-packages|/lib/python\d|^<frozen", frames[-1][0] or "") is not None
        return "error", "3-script-error", "env-script-library" if lib else "env-script-exception", line, 0
    m = _ASSERT_LINE_RE.search(output)
    if m:
        return "fail", "3-script-assert", "", m.group(0).strip(), 1
    if mk:
        return "fail", "3-script-marker", "", mk.group(0), 1
    return None


def parse_outcome(output: str, command: str, is_runner: bool, labels: list[str] | None = None, repo: str = "",
                  rc_ok: bool = True, own: frozenset[str] = frozenset()) -> tuple[str, str, str, str, int]:
    """(category, rule, error kind, matched line, number of failed tests) of one check — protocol, section 4, with
    the fixes of rounds 1–4 (reports/E10_parser_check.md). Category here is pass / fail / error / unknown; the split
    into error_env / error_code and script_ok / script_fail is final_category(). `own` — files the agent created
    earlier in the run (round 4, item D)."""
    labels = labels or []
    cands = []
    for m in _PYTEST_RE.finditer(output):
        body = m.group("body")
        f, e, p = _n(body, "failed"), _n(body, "errors?"), _n(body, "passed")
        only_skips = re.search(r"\d+ (skipped|xfailed|xpassed)", body) is not None
        cat = ("fail" if f else "error" if e else "pass" if p else "unknown" if only_skips else "error")
        kind = "env-nothing-ran" if cat == "error" and not e else None  # no tests ran / only deselected
        rule = "1-pytest-no-outcome" if cat == "unknown" else "1-pytest"
        cands.append((m.start(), cat, rule, kind, m.group(0).strip(), f + e if cat == "fail" else 0))
    ran = False
    for m in _RAN_RE.finditer(output):
        st = _UT_STATUS_RE.search(output, m.end())
        if not st:
            continue
        ran = True
        det = None if st.group(0).startswith("OK") else (st.group("det") or "")
        cat, kind, nf = _ut_outcome(int(m.group(1)), det, output, labels, repo, own, float(m.group(2).rstrip(".")))
        cands.append((m.start(), cat, "1-unittest", kind, f"{m.group(0).strip()} / {st.group(0).strip()}", nf))
    if is_runner and not ran:
        for st in _UT_ALONE_RE.finditer(output):
            det = st.group("det") if st.group(0).startswith("FAILED") else None
            cat, kind, nf = _ut_outcome(None, det, output, labels, repo, own)
            cands.append((st.start(), cat, "1-unittest-status", kind, st.group(0).strip(), nf))
    for m in _SYMPY_RE.finditer(output):
        body = m.group("body")
        f, x, p = _n(body, "failed"), _n(body, "exceptions?"), _n(body, "passed")
        cat = "fail" if f or x else "pass" if p else "unknown"
        if cat != "unknown":
            cands.append((m.start(), cat, "1-sympy", None, m.group(0).strip(), f + x if cat == "fail" else 0))
    if cands:  # rule 1: the last summary line decides, whatever else the output contains
        _, cat, rule, kind, line, nf = max(cands, key=lambda c: c[0])
        if cat == "error" and kind is None:
            kind = (error_kind(output, labels, repo, own=own) or ("?-summary", ""))[0]
        return cat, rule, kind or "", line, nf
    # rule 1b (round 4, item A): no summary line, but the output names failed tests -> fail; their absence is no pass.
    # FAILED / FAIL (an assertion failed) before rule 2; unittest `ERROR: test_…` (an exception inside the test)
    # after it: with the cause shown (`No module named '<label>'`, settings) every test errors for one reason, which
    # the summary rule (round 3, item 2) also calls an error
    failed = _failed_tests(output) if is_runner else []
    if any(not err for _, _, err in failed):
        return "fail", "1b-failed-tests", "", next(ln for _, ln, err in failed if not err), len(failed)
    # rule 2: explicit error of the check, before the truncation rules (round 3, item 1)
    ek = error_kind(output, labels, repo, crash=False, own=own)
    if ek is None and is_runner and _COLLECT_RE.search(output):
        ek = error_kind(output, labels, repo, own=own) or ("?-collect", _COLLECT_RE.search(output).group(0))
    if ek is not None and (is_runner or not ek[0].startswith("?")):
        return "error", "2-error", ek[0], ek[1], 0
    if failed:
        return "fail", "1b-failed-tests", "", failed[0][1], len(failed)
    rc = _RC_RE.match(output)
    if not is_runner:  # rule 3 (round 4, item B)
        r = _script_outcome(output, repo, own, rc is not None and rc_ok)
        if r is not None:
            return r
    if _PIPE_RE.search(command):
        return "unknown", "4-agent-pipe", "", _PIPE_RE.search(command).group(0), 0
    m = _LONG_RE.search(output)
    if m:
        return "unknown", "4-truncated", "", m.group(0), 0
    m = _TIMEOUT_RE.search(output)
    if m:
        return "unknown", "4-timeout", "", m.group(0), 0
    if is_runner and "Traceback (most recent call last)" in output:  # untruncated, no summary: crashed before tests
        kind, line = error_kind(output, labels, repo, own=own) or ("?-crash", "Traceback (most recent call last)")
        return "error", "4b-runner-crash", kind, line, 0
    if not rc_ok:  # round 3, item 5: the return code is not the check's -> rules 5 and 6 do not use it
        return "unknown", "6-rc-not-informative", "", "", 0
    if not is_runner and rc is None:
        return "pass", "5-script-rc0", "", "", 0
    return "unknown", "6-other", "", f"rc={rc.group(1)}" if rc else "rc=0", 0


# error kinds '?-…': no SyntaxError / ImportError / traceback from repository files in the output. error_code needs
# that evidence (the author's definition), so these are error_env (round 3, item 3)
UNDETERMINED_TO = "env"


def final_category(cat: str, kind: str, is_project: bool) -> str:
    if cat == "error":
        k = kind if not kind.startswith("?") else UNDETERMINED_TO
        return "error_env" if k.startswith("env") else "error_code"
    if not is_project and cat in ("pass", "fail"):
        return "script_ok" if cat == "pass" else "script_fail"
    return cat


# ------------------------------------------------------------------------------------------ commands of a run

def _runner_base(seg: str, cwd: str) -> str:
    """Directory test labels are resolved from: that of runtests.py (it puts itself on sys.path), else the current."""
    for w in _words(_QUOTED_RE.sub("''", seg)):
        if re.fullmatch(r"runtests(\.py)?", w.rsplit("/", 1)[-1]):
            return posixpath.dirname(_abs(w, cwd))
    return cwd


def command_rows(rec: dict, corpus: str, group: str) -> list[dict]:
    """Every executed command of a run in order, with edit / check flags and the parsed outcome of checks.
    Round 4: the current directory is followed through `cd` inside a command (item C); files the agent creates are
    remembered for the rest of the run (item D); `code_edit_seg` — first segment that edits a source file of the
    repository (item F), `check_seg` — last segment of the check. Flags of the round-4 spot check: `c_flag` — the
    current directory changed project / script; `d_flag` — files created by the agent changed project / script or the
    error kind."""
    outs: dict[int, list[str]] = {}
    for s in rec["steps"]:
        if s["role"] == "tool":
            outs.setdefault(s["step_idx"], []).append(s["text"])
    repo = repo_of(rec.get("task_id", ""))
    files: dict[str, str] = {}
    created: set[str] = set()
    rows, pos = [], 0
    for s in rec["steps"]:
        if s["role"] != "assistant":
            continue
        cmds = s["tool_calls"]
        o = outs.get(s["step_idx"], [])
        paired = len(o) == len(cmds)
        for i, cmd in enumerate(cmds):
            segops, docs = _segments_ops(cmd)
            kinds, runners, scripts, edit_targets_ = set(), [], [], []
            cwd, code_edit_seg = REPO_DIR, -1
            for j, (seg, _) in enumerate(segops):
                if not seg.strip():
                    continue
                cd = _cd_target(seg)
                if cd is not None:
                    cwd = _abs(cd, cwd) if not cd.startswith(("~", "$", "-")) else _UNKNOWN_DIR
                kind = classify_segment(seg, docs, files)  # = demo_lib.command_events: same split, same order
                kinds.add(kind)
                if kind == "edit":
                    edit_targets_.append(_norm_path(_edit_target(seg, docs)))
                elif kind == "verify":
                    scripts.append((j,) + _script_of(seg, docs, files))
                if code_edit_seg < 0 and any(is_code_file(t, repo) for t in edit_targets(seg, docs, cwd, files)):
                    code_edit_seg = j
                r = runner_of(seg)
                if r:
                    runners.append((j, r, cwd, _runner_base(seg, cwd), frozenset(created)))
                # a source file of the project rewritten by `cat >` is an edit of the project's code, not the agent's
                # own file; a file restored from git is the project's again
                created.update(f for f in created_files(seg, docs, cwd) if not is_code_file(f, repo))
                created.difference_update(reverted_files(seg, cwd))
            info = [x for x in runners if _INFO_FLAGS & set(x[1][1])]
            runners = [x for x in runners if x not in info]
            main = [seg for seg, _ in segops if seg.strip() and (_words(seg) or [""])[0] not in _IGNORABLE]
            info_only = bool(info) and not runners and len(main) == len(info)
            is_check = ("verify" in kinds or bool(runners)) and not info_only
            excl, created_here = "", False
            if is_check and not runners:  # round 3, item 4: a self-made script that writes the repository is not a check
                own = {_norm_path(p) for _, _, p in scripts if p}
                if any(_writes_repo(code) for _, code, _ in scripts):
                    excl = "script-writes"
                elif any(t not in own and not t.startswith("/") for t in edit_targets_):  # '?' = unknown target
                    excl = "script-edits"
                is_check = not excl
                created_here = is_check and any(t in own for t in edit_targets_)  # the script is written, then run
            row = {"corpus": corpus, "group": group, "run_id": rec["run_id"], "step_idx": s["step_idx"], "pos": pos,
                   "is_edit": "edit" in kinds, "is_check": is_check, "excl": excl, "created_here": created_here,
                   "is_runner": bool(runners), "runner": runners[0][1][0] if runners else "", "is_project": False,
                   "rc_ok": True, "base": "", "category": "", "rule": "", "kind": "", "line": "", "n_failed": 0,
                   "c_flag": False, "d_flag": False, "d_proj": False, "code_edit_seg": code_edit_seg, "check_seg": -1,
                   "command": cmd, "out_tail": ""}
            if is_check:
                row["is_project"] = any(is_project_run(r, a, c, b, cr) for _, (r, a), c, b, cr in runners)
                no_own = any(is_project_run(r, a, c) for _, (r, a), c, _, _ in runners)
                row["c_flag"] = no_own != any(is_project_run(r, a) for _, (r, a), _, _, _ in runners)
                row["d_flag"] = row["d_proj"] = row["is_project"] != no_own
                k = max([x[0] for x in runners] + [j for j, _, _ in scripts])
                row["check_seg"] = k
                row["rc_ok"] = _rc_informative(segops, k)
                if paired:
                    out = _ANSI_RE.sub("", o[i])
                    own = frozenset(created)
                    args = (out, cmd, bool(runners), labels_of_runs([x[1] for x in runners]), repo, row["rc_ok"])
                    cat, rule, kind, line, nf = parse_outcome(*args, own=own)
                    if own and not row["d_flag"] and any(p in out for p in own if not p.startswith("/")):
                        row["d_flag"] = parse_outcome(*args)[:3] != (cat, rule, kind)
                    row["out_tail"] = "\n".join(out.rstrip("\n").split("\n")[-15:])
                else:
                    cat, rule, kind, line, nf = ("unknown", "unpaired_output", "",
                                                 f"{len(cmds)} calls / {len(o)} outputs", 0)
                row.update({"base": cat, "category": final_category(cat, kind, row["is_project"]), "rule": rule,
                            "kind": kind, "line": line[:200], "n_failed": nf})
            rows.append(row)
            pos += 1
    return rows


def leaderboard_records():
    runs = pd.read_parquet(ROOT / "data" / "demo" / "runs.parquet", columns=["run_id", "submitted", "model",
                                                                            "agent_version"])
    pop = runs[runs.submitted == 1].set_index("run_id")
    for rec in read_jsonl_gz(trajectories_path("swebench_bashonly")):
        if rec["run_id"] in pop.index:
            r = pop.loc[rec["run_id"]]
            yield rec, f"{r.model} | {r.agent_version}"


def kth_records():
    for setting in SETTINGS:
        for rd in run_dirs(setting):
            lab = labels_of(rd)
            for raw in iter_raw(rd):
                y = lab.get(raw["instance_id"])
                if isinstance(y, int) or y == "eval_error":  # attempts with a patch (E9 pool of risks)
                    yield to_record(setting, rd.name, raw, y if isinstance(y, int) else None), setting


def _raw_tool_texts(d: dict) -> list[str]:
    """Tool outputs of a .traj.json, raw, in the order and selection of the E0 adapter
    (src/adapters/swebench_bashonly.parse_trajectory)."""
    msgs = d["messages"]
    first_user = next(i for i, m in enumerate(msgs) if m.get("role") == "user")
    body = [m for m in msgs[first_user + 1:] if m.get("role") != "exit"]
    if str((d.get("info") or {}).get("exit_status") or "") == "Submitted":
        last_a = max((i for i, m in enumerate(body) if m.get("role") == "assistant"), default=None)
        if last_a is not None:
            body = body[: last_a + 1]
    out = []
    for m in body:
        t = _text_of(m.get("content"))
        if m.get("role") == "tool" or (m.get("role") == "user" and t.lstrip().startswith("<returncode>")):
            out.append(t)
    return out


def source_pick(pop_ids: set[str], seed: int = 45, n: int = 5) -> list[str]:
    """n leaderboard runs of the population whose .traj.json is on disk (round 3, item 6)."""
    local = sorted(f"{p.parent.parent.name}/{p.name[: -len('.traj.json')]}" for p in LB_RAW.glob("*/trajs/*.traj.json"))
    elig = [r for r in local if r in pop_ids]
    idx = np.random.default_rng(seed).choice(len(elig), n, replace=False)
    return sorted(elig[i] for i in idx)


def source_compare(rec: dict) -> dict:
    """Command outputs of one run: trajectories.jsonl.gz vs its local .traj.json, byte for byte."""
    sub, task = rec["run_id"].split("/")
    raw = _raw_tool_texts(json.loads((LB_RAW / sub / "trajs" / f"{task}.traj.json").read_text(encoding="utf-8")))
    jl = [s["text"] for s in rec["steps"] if s["role"] == "tool"]
    same_clean = sum(a.encode() == clean_tool_output(b).encode() for a, b in zip(jl, raw))
    same_raw = sum(a.encode() == b.encode() for a, b in zip(jl, raw))
    return {"run_id": rec["run_id"], "n_raw": len(raw), "n_jsonl": len(jl), "same_as_is": same_raw,
            "same_after_adapter": same_clean, "bytes_jsonl": sum(len(a.encode()) for a in jl),
            "bytes_raw": sum(len(b.encode()) for b in raw),
            "raw_elided": sum(("<elided_chars>" in b) or ("<output_head>" in b) for b in raw)}


def build_checks() -> pd.DataFrame:
    t0 = time.time()
    rows = []
    n = 0
    pop_ids = set(pd.read_parquet(ROOT / "data" / "demo" / "runs.parquet", columns=["run_id", "submitted"])
                  .query("submitted == 1").run_id)
    picks = set(source_pick(pop_ids))
    src = []
    for rec, grp in leaderboard_records():
        rows += command_rows(rec, "leaderboard", grp)
        n += 1
        if rec["run_id"] in picks:
            src.append(source_compare(rec))
    log.info("leaderboard: %d runs, %d commands, %.0f s", n, len(rows), time.time() - t0)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "source_check.json").write_text(json.dumps(sorted(src, key=lambda x: x["run_id"]), indent=1),
                                              encoding="utf-8")
    log.info("source check: %s", src)
    n0 = len(rows)
    n = 0
    for rec, grp in kth_records():
        rows += command_rows(rec, "kth", grp)
        n += 1
    log.info("kth: %d runs, %d commands, %.0f s", n, len(rows) - n0, time.time() - t0)
    df = pd.DataFrame(rows)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT_DIR / "commands.parquet", index=False)
    chk = df[df.is_check]
    log.info("checks: %d; by corpus x category:\n%s", len(chk),
             chk.groupby(["corpus", "category"]).size().unstack(fill_value=0).to_string())
    return df


# ------------------------------------------------------------------------------------------ parser check

def _fence(text: str) -> list[str]:
    body = text.replace("```", "ʼʼʼ")
    return ["```text", body, "```"]


FIXES = [
    "### Раунды 1–2: моя проверка до передачи автору", "",
    "Первая выборка (seed 42) просмотрена до передачи автору. Найдены систематические ошибки; разбор исправлен, "
    "проверки пересобраны, выборка построена заново (seed 43), как требует протокол (раздел 5).",
    "- **ANSI-коды цвета** в выводе (`\\x1b[31m1 failed\\x1b[0m`) ломали шаблон итоговой строки: цветной вывод "
    "pytest попадал в `unknown` (seed 42: №46 — `1 failed` с rc = 1; №57 и №60 — `204 deselected`). Теперь коды "
    "удаляются до разбора.",
    "- **`IndentationError`, `TabError`** — подклассы `SyntaxError`; были `fail` по traceback (№21), теперь `error`.",
    "- **`unittest.loader._FailedTest`** — тест не загрузился (`module has no attribute …`); был `fail` (№25), "
    "теперь `error`, как ошибка сбора.",
    "- **pytest без исхода**: итог только из skipped / xfailed / xpassed (`1 skipped in 0.01s`) был `error` (№41); "
    "тесты запускались, но ни прохода, ни падения нет — теперь `unknown` (правило `1-pytest-no-outcome`). Итог только "
    "из deselected / warnings по-прежнему `error` (тесты не запускались).",
    "- **Справочные вызовы** тестовой программы (`pytest --version`, `manage.py test --help`, `--collect-only` и т. п.) "
    "считались проверками (№52, №33); теперь не считаются.",
    "",
    "Вторая выборка (seed 43) тоже просмотрена до передачи автору; найдены ещё две систематические ошибки, после "
    "исправления выборка построена заново (seed 44):",
    "- **Итог sympy `bin/test` с переносом строки** (`tests finished: 1801 passed, …,` / `1 expected to fail but "
    "passed, in 20.10 seconds`) не распознавался (seed 43: №52, `unknown`); шаблон допускает один перенос.",
    "- **Падение тестовой программы до запуска тестов** (traceback, итоговой строки нет, вывод не обрезан: модель не в "
    "INSTALLED_APPS, `ImproperlyConfigured`) было `unknown` (seed 43: №48, №57); теперь `error` (правило "
    "`4b-runner-crash`, после правил обрезки: в обрезанном выводе traceback может быть от упавшего теста).",
]


FIXES3 = [  # round 3: the author's review of the seed-44 sample (numbers: build of 2026-11-04, round 2 -> round 3)
    "### Раунд 3: проверка автора (выборка seed 44)", "",
    "Автор проверил выборку seed 44 и не принял разбор: шесть замечаний. Ниже по каждому — что сделано. Где замечание "
    "оставляло выбор, решение помечено **решение**: его нужно подтвердить. После исправлений я просмотрел по 8 "
    "случайных проверок каждого вида ошибки и каждого перехода категории между раундами 2 и 3. Найденное там тоже "
    "исправлено (п. 7). Выборка ниже — новая: seed 45, по 15 на каждую из 7 категорий.", "",
    "**Названные автором случаи seed 44 — категория в раунде 3:**", "",
    "| № | раунд 2 | раунд 3 | правило | вид ошибки |", "|---|---|---|---|---|",
    "| 6 | pass | не проверка (`script-writes`) | — | — |",
    "| 31 | error | error_env | `2-error` | `env-module-outside` |",
    "| 33, 35, 36, 37, 44, 45 | error | error_env | `2-error` | `env-runner-missing` |",
    "| 34 | error | error_env | `2-error` | `env-label` |",
    "| 40 | error | error_env | `2-error` | `env-settings` |",
    "| 41 | error | error_env | `1-pytest` | `env-nothing-ran` |",
    "| 43 | error | error_env | `2-error` | `env-path` |",
    "| 52 | unknown | error_env | `2-error` | `env-usage` |",
    "| 55 | unknown | error_env | `2-error` | `env-path` |",
    "| 57 | unknown | error_env | `1-unittest-status` | `?-failedtest` |", "",
    "**1. Порядок правил.** Итоговая строка (правило 1) и явные ошибки проверки (правило 2) применяются до правил "
    "обрезки (правило 4: `| head`, `| tail`, `| grep`, «output truncated», тайм-аут). Правило 4 даёт `unknown`, "
    "только если нет ни итоговой строки, ни признаков ошибки.",
    "- Итог unittest без строки `Ran N tests` теперь распознаётся у тестовых программ: `FAILED (…)`, `OK`, "
    "`OK (skipped=…)`. Так бывает, когда агент отфильтровал вывод: `| grep -E \"^(OK|FAILED|ERROR)\"`, `| tail -5`. "
    "Правило `1-unittest-status`, 970 проверок: 782 `pass`, 140 `fail`, 48 `error_env`. Голое `OK` ни разу не "
    "пришло от `echo` в той же команде.",
    "- Добавлены явные ошибки запуска: ошибки разбора аргументов (`error: unrecognized arguments`, `ERROR: usage:`) и "
    "ответы оболочки `…: No such file or directory`, `…: not found`. №52 и №55 теперь такие же, как №43.",
    "- Признаки ошибки ищутся как строка исключения, а не как подстрока. Строка должна начинаться с имени исключения "
    "(у pytest — после `E   `) или быть сообщением `No module named …` / `cannot import name …`.", "",
    "**2. Итоговая строка важнее подстроки.** Правило 1 и раньше применялось первым.",
    "- **№34.** В полном выводе (8 026 символов) итоговой строки нет: ни `Ran N tests`, ни `OK` / `FAILED`, ни "
    "строки pytest.",
    "  - Метки `pickling` нет (`ModuleNotFoundError: No module named 'pickling'`).",
    "  - Параллельный раннер django упал, пересылая этот traceback между процессами: "
    "`TypeError: cannot pickle 'traceback' object`. Итог прогона так и не был напечатан.",
    "  - `ResourceWarning` в хвосте — от упавшего пула процессов.",
    "  - Итог: ошибка остаётся, по п. 3 это `error_env` (`env-label`).",
    "- **Изменено правило unittest для `FAILED` только с errors.**",
    "  - Раньше это была ошибка, если в выводе где-нибудь встречалась подстрока `ImportError`, "
    "`ModuleNotFoundError` или `SyntaxError`.",
    "  - Теперь ошибка, только если errors — это тесты, которые не загрузились. Условие: столько же `_FailedTest`, "
    "сколько errors, или все запущенные тесты дали error и вывод показывает причину по правилам п. 3. Второе условие "
    "нужно, когда `| tail` срезал строку `_FailedTest`, а `No module named '<метка>'` остался.",
    "  - Иначе `fail`: исключение внутри теста unittest считает как error, это упавший тест.",
    "  - Переход error → fail после этого правила: 36 проверок.", "",
    "**3. `error_env` и `error_code`.** Вид ошибки определяется по выводу. Правила применяются по порядку, первое "
    "сработавшее решает.",
    "1. Явное сообщение, что проверку запустить не удалось → env:",
    "   - нет тестовой программы (`No module named pytest`) — `env-runner-missing`;",
    "   - ошибка аргументов — `env-usage`;",
    "   - неверный путь (`can't open file`, `No such file or directory` оболочки, `ERROR: not found`, `Test label "
    "path … does not exist`) — `env-path`;",
    "   - ничего не собрано (`no tests ran`, `collected 0 items`, `Ran 0 tests`, только deselected) — "
    "`env-nothing-ran`;",
    "   - django не настроен (`settings are not configured`, `ImproperlyConfigured`, `AppRegistryNotReady`, «not in "
    "INSTALLED_APPS») — `env-settings`;",
    "   - относительный импорт вне пакета — `env-invocation`;",
    "   - C-расширения проекта не собраны — `env-build`.",
    "2. Иначе — последняя в выводе ошибка импорта или синтаксиса:",
    "   - нет модуля или атрибута, переданного как метка теста (`No module named 'pickling'` при `runtests.py … "
    "pickling …`) → env, `env-label`;",
    "   - модуль вне репозитория: сторонний пакет, модуль настроек, модуль агента; или ImportError со своим "
    "сообщением, в котором нет пакета репозитория (`numpy.core.multiarray failed to import`) → env, "
    "`env-module-outside`;",
    "   - нет самого пакета репозитория (`No module named 'django'` из `tests/runtests.py`: интерпретатор не видит "
    "проект) → env, `env-project-not-importable`;",
    "   - ошибка возникла в файле репозитория → code (`code-import`, `code-syntax`);",
    "   - ошибка возникла в скрипте агента, в `python -c` или в heredoc → env (`env-script-import`, "
    "`env-script-syntax`). **Решение:** сломана сама проверка, а не код проекта.",
    "3. Тест не загрузился (`_FailedTest`), но причина обрезана → вид не определён (`?-failedtest`).",
    "4. Падение с traceback:",
    "   - исключение возникло при загрузке модуля репозитория → code (`code-traceback`). Условие: от последнего кадра "
    "`<module>` до исключения все кадры — файлы репозитория;",
    "   - в файле репозитория, но во время работы → не определено (`?-runtime`);",
    "   - вне репозитория → env (`env-traceback`).",
    "",
    "Файл репозитория — файл в каталоге исходников проекта под `/testbed`:",
    "- `django/`, `tests/` (django; так же sphinx, pylint, seaborn, requests);",
    "- `lib/` (matplotlib); `src/`, `testing/` (pytest); `sklearn/`, `sympy/`, `astropy/`, `xarray/`;",
    "- `src/`, `tests/` (flask).",
    "",
    "Пакет репозитория — импортируемое имя проекта (`django`, `sklearn`, `_pytest`, …).",
    "",
    "- **Не определено (`?-…`) → `error_env`.** В выводе нет SyntaxError, ImportError или traceback из файлов "
    "репозитория, а по определению автора `error_code` требует именно этого признака. Таких 866 из 23 074 ошибок "
    "(3,8%). Из них 434 — `?-summary`: итог pytest `N error`, а причина не видна или это ошибка фикстуры либо "
    "плагина. 188 — `?-runtime`; это почти всё старый pytest под Python 3.11 (`required field \"lineno\" missing "
    "from alias`), то есть несовместимость интерпретатора.",
    "- **Решение: №38 и №42.** Там нет модуля, переданного как метка (`No module named 'user_commands'`, "
    "`'aggregates'`). В списке автора этих случаев нет. Они отнесены к `error_env` как «неверный путь».",
    "- **`error_code` — 418 проверок (1,8% ошибок):** 297 `code-syntax`, 69 `code-import`, 52 `code-traceback`.",
    "- **Известное ограничение.** Несовместимость кода проекта с интерпретатором видна как ошибка в файлах "
    "репозитория и попадает в `error_code`. Пример: старый astropy под Python 3.11, `collections.Mapping`.", "",
    "**4. Самодельные скрипты.**",
    "- Проверкой скрипта считается только команда фазы verify (demo_lib), как и раньше.",
    "- **Не проверка**, если скрипт пишет в файлы репозитория или команда правит файл репозитория, кроме самого "
    "запускаемого скрипта.",
    "  - Запись скриптом: `open(…, 'w'|'a')`, `.write_text(`. Путь берётся из литерала или из присваивания имени. "
    "Путь под `/testbed` или относительный — файл репозитория.",
    "  - Правка командой: `sed -i`, `cat >`, `tee`, перенаправление в файл кода — по правилам demo_lib.",
    "  - №6 исключён.",
    "  - Исключено: скрипт пишет — лидерборд 435, KTH 221; правка другого файла — 59 и 2.",
    "- **Решение: запись во временные файлы** (`/tmp`, `tempfile`) проверку не исключает. В первой версии исключала "
    "(п. 7).",
    "- **Решение: создание и запуск скрипта в одной команде** — проверка. Пример: `cat > /testbed/repro.py << EOF … "
    "EOF && python /testbed/repro.py`; команда пишет только сам запускаемый скрипт.",
    "  - Таких проверок: лидерборд 3 423, KTH 4 668.",
    "  - Буквальное «cat > в /testbed» исключило бы из них те, где скрипт лежит в репозитории: 2 499 и 303.",
    "- Исход самодельного скрипта — `script_ok` / `script_fail`, тестов проекта — `pass` / `fail`. Самодельный "
    "скрипт — как в протоколе (раздел 3): всё, кроме тестов проекта, в том числе pytest по файлу вне `tests/`. "
    "Ошибки скриптов получают `error_env` / `error_code` по п. 3.",
    "- **Признаки:**",
    "  - `o_last_project_status_*` — только тесты проекта, 5 значений: pass, fail, error_env, error_code, unknown;",
    "  - `o_last_status_*` — все проверки, 7 значений;",
    "  - **решение (нужно подтверждение):** остальные признаки блока O (`o_n_fail`, `o_share_fail`, "
    "`o_unresolved_fail`, `o_edit_after_last_pass`, `o_last_fail_count`, `o_contradiction`) считаются, как в "
    "протоколе, по объединённым категориям: pass = pass ∪ script_ok, fail = fail ∪ script_fail, error = "
    "error_env ∪ error_code. Блок O — 19 признаков вместо 15.", "",
    "**5. Код возврата.**",
    "- Код возврата считается кодом проверки, только если все последующие команды присоединены к ней через `&&`; при "
    "`set -o pipefail` — ещё и через `|`.",
    "- Иначе правила 5 и 6 его не используют: правило `6-rc-not-informative` даёт `unknown`.",
    "- **Решение:** то же для `;`, `||` и новой строки после проверки. Там код возврата тоже от последней команды: "
    "`… || echo \"…\"`, `python … > out.txt << EOF … EOF` и затем `cat out.txt`.",
    "- Проверок с кодом возврата не от проверки: самодельных скриптов 180 (лидерборд) и 50 (KTH). Правило "
    "`6-rc-not-informative` сработало 47 раз; остальные решены по выводу (traceback, обрезка).", "",
    "**6. Сверка источника.**",
    "- **Исправление протокола.** В разделе 2 сказано, что `.traj.json` на диске нет. Это неверно: локально лежат 355 "
    "файлов (16 связок, примерно по 22 задачи).",
    "- **Результат** (таблица ниже): все 204 вывода команд пяти прогонов совпадают байт в байт с текстом в "
    "`.traj.json`. Для сравнения снимаются только обёртка mini-SWE-agent (элемент `<returncode>N</returncode>`, теги "
    "`<output>`, `<warning>`, `<output_head>`, `<elided_chars>`, `<output_tail>`, переводы строк по краям) и "
    "префикс `returncode: N` адаптера E0.",
    "- Как есть не совпадает ни один вывод: в `.traj.json` каждый вывод обёрнут.",
    "- Текст вывода адаптер не обрезает. Остаёмся на `trajectories.jsonl.gz`.", "",
    "**7. Найдено при моём просмотре после исправлений** и исправлено:",
    "- `No module named 'django'` из `tests/runtests.py` было `code-import`, стало `env-project-not-importable`;",
    "- шаблон «isn't in an application in INSTALLED_APPS» (полный текст django) не совпадал; дополнен;",
    "- `ImproperlyConfigured: … are mutually exclusive` (`--settings=django.conf.global_settings`) было "
    "`code-traceback`, стало `env-settings`;",
    "- старый pytest под Python 3.11 было `code-traceback`, стало `?-runtime`: `code-traceback` теперь только для "
    "падения при загрузке модуля;",
    "- несобранные C-расширения (`It seems that scikit-learn has not been built correctly`, `undefined symbol`, "
    "`_compiler`) были `code-import` / `code-traceback`, стали `env-build`;",
    "- ImportError со своим сообщением о стороннем пакете было `script_fail`, стало `env-module-outside`;",
    "- кадры `<frozen importlib…>` в трассировках pytest не учитывались, из-за этого `No module named "
    "'tests.settings'` было `code-import`;",
    "- правило «скрипт пишет файлы» исключало и запись во временные файлы;",
    "- первая версия правила unittest (п. 2) требовала строку `_FailedTest`. Из-за этого 113 ошибок загрузки, у "
    "которых `| tail` срезал эту строку, ушли в `fail`;",
    "- неопределённые ошибки сначала относились к `error_code`.", "",
    "**Изменение категорий по сравнению с раундом 2** (обе выборки корпусов, все команды, которые были проверкой хотя "
    "бы в одном раунде):", "",
    "| раунд 2 \\ раунд 3 | не проверка | error_code | error_env | fail | pass | script_fail | script_ok | unknown | "
    "всего |", "|---|---|---|---|---|---|---|---|---|---|",
    "| error | 89 | 418 | 21 138 | 36 | 0 | 6 | 3 | 106 | 21 796 |",
    "| fail | 113 | 0 | 261 | 5 520 | 0 | 5 242 | 0 | 0 | 11 136 |",
    "| pass | 475 | 0 | 9 | 0 | 23 371 | 0 | 13 963 | 5 | 37 823 |",
    "| unknown | 40 | 0 | 1 248 | 140 | 782 | 0 | 0 | 11 544 | 13 754 |",
    "| всего | 717 | 418 | 22 656 | 5 696 | 24 153 | 5 248 | 13 966 | 11 655 | 84 509 |", "",
    "- error → unknown (106): прежние совпадения с подстрокой `ImportError` / `SyntaxError` в тексте, а не в строке "
    "исключения. Например, `SyntaxError: invalid syntax` в ожидаемом выводе теста pytest или «Raise an ImportError» "
    "в документации; вывод обрезан.",
    "- unknown → error_env (1 248): явные ошибки при `| head` / `| tail` / `| grep` (п. 1).",
    "- fail → error_env (261): падение скрипта из-за ненастроенного django (п. 3).",
]


FIXES4 = [  # round 4: the author's review of the seed-45 sample (numbers: build of 2026-11-05)
    "### Раунд 4: проверка автора (выборка seed 45)", "",
    "Автор принял решения 1–5 раунда 3. Решение 5 — с уточнением: объединённые категории (pass ∪ script_ok и т. д.) — "
    "дополнительный набор признаков для проверки устойчивости, критерии К1–К4 проверяются по отдельным категориям "
    "(записано в протокол, раздел 11.4). Исправления A–E меняют разбор, F — признаки (протокол, раздел 11.5). "
    "Проверка по указанию автора точечная: по 8 случаев на каждое изменённое правило, seed 46 (ниже), и пересчёт "
    "таблиц. Перед выборкой я просмотрел по 10 случайных проверок каждого правила и нашёл ещё семь систематических "
    "ошибок; они исправлены (п. G).", "",
    "**Названные автором случаи seed 45 — категория в раунде 4:**", "",
    "| № | раунд 3 | раунд 4 | правило | вид ошибки |", "|---|---|---|---|---|",
    "| 26 | fail | error_env | `1-unittest` | `?-not-loaded` (E) |",
    "| 33 | error_env | error_env | `1-unittest` | `env-label` |",
    "| 52 | error_code | error_env | `1-unittest` | `env-script-import` (D) |",
    "| 69 | script_ok | pass | `1-pytest` | — (C) |",
    "| 76 | script_fail | script_fail | `3-script-project-frame` | — (см. B) |",
    "| 78, 89, 90 | script_fail | error_env | `3-script-error` | `env-script-encoding` (B) |",
    "| 81, 83, 84, 86 | script_fail | script_fail | `1-pytest` | — (F — признаки, не разбор) |",
    "| 91, 98 | unknown | fail | `1b-failed-tests` | — (A) |", "",
    "**A. Упавшие тесты в выводе без итоговой строки.** Новое правило `1b-failed-tests`: итоговой строки нет, но вывод "
    "тестовой программы называет упавший тест → `fail`. Отсутствие таких строк по-прежнему не означает `pass`.",
    "- Строки: `FAILED путь::тест` (сводка pytest), `путь::тест FAILED` (pytest -v), `FAIL: test_… (…)` (unittest), "
    "`ERROR: test_… (…)` без `_FailedTest` в скобках. **Решение:** строки pytest -v в списке автора нет; это то же "
    "свидетельство (1 071 из 1 765 срабатываний).",
    "- Порядок: `FAILED` / `FAIL:` — до явных ошибок (правило 2), `ERROR: test_…` — после. `ERROR:` у unittest — "
    "исключение внутри теста. Если вывод показывает причину (`No module named 'messages_tests'` при неверной метке, "
    "django не настроен), все тесты падают по одной причине, и правило итоговой строки (раунд 3, п. 2) тоже дало бы "
    "ошибку.",
    "- Число упавших — число разных названных тестов (нижняя граница: вывод отфильтрован).",
    "- Сработало: лидерборд 334, KTH 1 431. Было `unknown` — 1 747, `error_env` — 18 (строки pytest `FAILED` при "
    "случайном упоминании `No module named …` в выводе упавшего теста).", "",
    "**B. Падение самодельного скрипта.** Правило 3 для самодельных скриптов переписано. Берётся последняя "
    "трассировка вывода и её строка исключения; по порядку:",
    "1. `UnicodeEncodeError` / `UnicodeDecodeError` не в файле репозитория → `error_env` (`env-script-encoding`): "
    "лидерборд 290, KTH 681;",
    "2. `AssertionError` → `script_fail`: 852 и 1 052;",
    "3. кадр в файле репозитория → `script_fail`: 554 и 568;",
    "4. явные маркеры провала при коде возврата ≠ 0, который принадлежит проверке → `script_fail` "
    "(`3-script-marker`; то же без трассировки): 71 и 60. Маркеры — `FAIL`, `FAILED`, `FAILURE`, `✗`, `✘`, `❌`, "
    "`failed` / `Failed`, кроме `0 failed`; ищутся вне кадров трассировки, предупреждений Python и служебной строки "
    "KTH `command failed with exit code N`;",
    "5. последний кадр — установленная или стандартная библиотека, кадров репозитория нет → `error_env` "
    "(`env-script-library`): 23 и 21;",
    "6. иначе исключение в собственном коде скрипта → `error_env` (`env-script-exception`): 196 и 315. **Решение** "
    "по пункту 4 автора («применить последовательно»): `NameError`, `AttributeError`, `TypeError` в коде самого "
    "скрипта — ошибка скрипта, а не проекта. Если скрипт при этом печатает маркер провала и код возврата ≠ 0, "
    "срабатывает п. 4 (так в правиле автора).",
    "- SyntaxError / ImportError самого скрипта — по-прежнему правило 2 (`env-script-syntax`, `env-script-import`).",
    "- Строка исключения не видна (вывод обрезан): кадр репозитория среди видимых → `script_fail`, иначе маркеры, "
    "иначе `unknown` (`3-script-traceback-cut`, 23).",
    "- `AssertionError` без трассировки считается только в начале строки. Раньше считалась подстрока: «✓ PASS: "
    "correctly raised AssertionError» давало `script_fail` (44 проверки, теперь `script_ok`).",
    "- Итог для `script_fail` раунда 3 (5 248): остались `script_fail` 3 643, стали `error_env` 1 528, `script_ok` "
    "44, `unknown` 26, `fail` 7 (C).",
    "- **№76 остаётся `script_fail`.** В его трассировке есть кадр файла репозитория "
    "`/testbed/xarray/backends/zarr.py`: код xarray обращается к `zarr_array.compressor`, установленный zarr 3 "
    "бросает `TypeError`. По правилу автора `script_fail` — «исключение с кадром в /testbed», а условие `error_env` — "
    "«нет кадров из /testbed». Чтобы №76 стал `error_env`, правило должно смотреть только на последний кадр. Тогда в "
    "`error_env` ушли бы и случаи, где код проекта неверно вызывает библиотеку (а это может быть ошибка исправления). "
    "Я этого не делал — **нужно решение автора**.",
    "- **Ограничение.** Маркеры считаются только при коде возврата ≠ 0. `script_ok` с маркером провала в последних "
    "15 строках вывода: 203 из 14 390 (скрипт напечатал «✗ FAIL», но завершился с кодом 0).", "",
    "**C. Пути от текущего каталога.** Путь в аргументах тестовой программы разрешается от каталога, в который "
    "команда перешла через `cd`, если этот каталог внутри репозитория: `cd ./sklearn/ensemble/tests && pytest "
    "test_iforest.py` — тест проекта (№69).",
    "- Из каталога вне репозитория (`cd /root`, `cd /tmp`) относительный путь читается как написан, от корня "
    "репозитория: агент имел в виду файл репозитория, но стоит не в том каталоге (п. G).",
    "- pytest без целей вне репозитория (`cd /tmp/test_repro && pytest`) собирает файлы агента — скрипт. Абсолютный "
    "путь вне `/testbed` (копия тестов в site-packages) — скрипт.",
    "- Изменилось: скрипт → тесты проекта — лидерборд 30, KTH 195; тесты проекта → скрипт — 43 и 70 (все — `cd` в "
    "каталог агента вне репозитория).", "",
    "**D. Файлы, созданные агентом.** Отслеживание по ходу прогона:",
    "- создание — `> файл` (не `>>`), `tee` без `-a`, python `open(…, 'w'|'x')` / `.write_text` с литеральным "
    "путём, если тот же код этот файл не читает (прочитать и записать обратно — правка, а не создание);",
    "- не отслеживаются исходники проекта: `cat > django/…/client.py` — так агенты правят код (п. G);",
    "- файл, восстановленный `git checkout -- …` / `git restore …`, снова считается файлом проекта.",
    "",
    "Последствия:",
    "- тестовая программа, у которой **все** цели (пути; метки, сведённые к файлам от каталога runtests.py или "
    "текущего) — файлы агента, — самодельный скрипт;",
    "- кадр трассировки в файле агента — не кадр репозитория (№52: ImportError в `test_key_in2.py` → "
    "`env-script-import`).",
    "",
    "Числа:",
    "- тесты проекта → скрипт: лидерборд 751, KTH 260. Это 2,4% и 0,9% запусков тестовых программ по тестам проекта. "
    "Из них `pass` → `script_ok` 422, `fail` → `script_fail` 266, `error_code` → `error_env` 17 (ошибка в файле "
    "агента);",
    "- изменился только вид ошибки: 5;",
    "- **надёжность:** перезапись существующего файла тестов проекта через `cat >` тоже делает его файлом агента — "
    "так в определении автора; отменяется только явным `git checkout`. Создание другими способами (`cp`, `mv`, "
    "путь, собранный в python из переменных) не видно. Проверка — 8 случаев D ниже.", "",
    "**E. unittest: `Ran N tests in 0.000s` и errors = N.** Если ни одна строка `ERROR:` теста, который запускался, "
    "не видна (вывод отфильтрован `grep` / `tail`), это ошибка загрузки → `error`, вид не определён "
    "(`?-not-loaded` → `error_env`). Сработало: лидерборд 23, KTH 29 (№26).", "",
    "**F. Признаки.** Записано в протокол (раздел 11.5) с причиной. `o_n_fail`, `o_share_fail`, `o_unresolved_fail` — "
    "только по проверкам после первой правки кода проекта.",
    "- Правка кода проекта — запись в файл исходников репозитория, не тестов.",
    "- Правкой считается и запуск скрипта-«патчера», записанного раньше: `cat > /tmp/patch.py` с `open('django/…', "
    "'w')`, затем `python /tmp/patch.py` (п. G).",
    "- Правка найдена в 98,0% прогонов лидерборда и 91,8% прогонов KTH. Остальные правили только документацию или "
    "тесты либо путь не виден.",
    "- №84 и №86 стоят до первой правки кода и в эти признаки не входят. №81 и №83 — после неё. В №83 агент поправил "
    "`src/_pytest/python.py` второй командой, а затем запустил тест, который падает по замыслу (`assert 0`: "
    "проверялся заголовок отчёта pytest). **Ограничение:** такой ожидаемый провал после правки по выводу не отличить "
    "от неудачного исправления.", "",
    "**G. Найдено при моём просмотре перед выборкой** (по 10 случайных проверок каждого правила) и исправлено:",
    "- C: 2 435 проверок (KTH 2 387) с `cd /root` / `cd /tmp` и путём `tests/…` ушли из тестов проекта в скрипты; "
    "2 338 из них — `error_env` (файл не найден). Это попытки запустить тесты проекта не из того каталога; "
    "разрешение путей теперь только внутри репозитория;",
    "- B: stdout вклинивался внутрь трассировки (mini-SWE-agent сливает потоки), и строка исключения не находилась: "
    "163 проверки `3-script-traceback-cut`, стало 23. Теперь строка исключения ищется по имени класса исключения, "
    "посторонние строки пропускаются;",
    "- B: трассировки `Exception ignored in: …` (исключение в `__del__` при завершении интерпретатора) считались "
    "падением скрипта, напечатавшего «ALL TESTS PASSED»; теперь не учитываются;",
    "- B: «failed» из предупреждения numpy (`FutureWarning: elementwise comparison failed`) срабатывал как маркер; "
    "предупреждения и кадры трассировки из поиска маркеров исключены;",
    "- D: исходник проекта, переписанный целиком через `cat >`, становился «файлом агента», и SyntaxError в нём "
    "дал бы `env-script-syntax` вместо `code-syntax`; исходники больше не отслеживаются, `git checkout` отменяет "
    "отслеживание;",
    "- F: правка через скрипт-«патчер» не находилась — правка кода была найдена только в 73% прогонов лидерборда, "
    "теперь 98%;",
    "- A: `ERROR: test_…` при показанной причине (неверная метка) давало `fail`; теперь — после правила 2.",
    "",
    "При разборе первой версии точечной выборки нашлась ещё одна неточность. Имя `_FailedTest` с путём "
    "(`/tmp/test_check (unittest.loader._FailedTest./tmp/test_check)`, агент передал путь как метку unittest) не "
    "распознавалось, и срабатывало правило E. Категория была верной (`error_env`), неверной была только метка вида "
    "ошибки: `?-not-loaded` вместо `?-failedtest`, 6 проверок. Шаблон исправлен, выборка построена заново. Изменились "
    "только случаи E; их я просмотрел ещё раз.", "",
    "**Итог раунда 4.**",
    "- `error_code` — 397 проверок: `code-syntax` 292, `code-import` 57, `code-traceback` 48.",
    "- Вид ошибки не определён у 924 из 24 640 ошибок (3,7%), они относятся к `error_env`.",
    "- Доля `unknown`: лидерборд 6,8% (в раунде 3 — 7,7%), KTH 16,5% (19,9%).", "",
]


def _md_table(head: list[str], rows: list[list]) -> list[str]:
    return ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)] + \
           ["| " + " | ".join(str(x) for x in r) + " |" for r in rows]


# round 4: the spot check — 8 checks for every changed rule (A, B, C, D, E of the author's review of seed 45)
SPOT = [("A", "упавшие тесты названы в выводе без итоговой строки → fail (правило `1b-failed-tests`)",
         lambda c: c.rule == "1b-failed-tests"),
        ("B1", "падение самодельного скрипта не из-за проекта → error_env (правило `3-script-error`)",
         lambda c: c.rule == "3-script-error"),
        ("B2", "маркеры провала при коде возврата ≠ 0 → script_fail (правило `3-script-marker`)",
         lambda c: c.rule == "3-script-marker"),
        ("C", "путь от текущего каталога изменил «тесты проекта / скрипт»", lambda c: c.c_flag),
        ("D", "файл, созданный агентом, изменил «тесты проекта / скрипт» или вид ошибки", lambda c: c.d_flag),
        ("E", "unittest: `Ran N tests in 0.000s`, errors = N, причина не видна → error_env (`?-not-loaded`)",
         lambda c: c.kind == "?-not-loaded")]
R3_PATH = OUT_DIR / "commands_r3.parquet"  # the round-3 build (commit f712e4e), for the comparison


def parser_check(seed: int = 46, per_rule: int = 8, per_lb: int = 4) -> Path:
    df = pd.read_parquet(OUT_DIR / "commands.parquet")
    key = ["corpus", "run_id", "pos"]
    r3 = pd.read_parquet(R3_PATH, columns=key + ["is_check", "category", "is_project"]) if R3_PATH.exists() else None
    if r3 is not None:
        df = df.merge(r3.rename(columns={"is_check": "is_check_r3", "category": "category_r3",
                                         "is_project": "is_project_r3"}), on=key, how="left")
    chk = df[df.is_check].reset_index(drop=True)
    rng = np.random.default_rng(seed)
    picks = []
    for code, _, sel in SPOT:
        m = sel(chk)
        lb = chk.index[m & (chk.corpus == "leaderboard")].to_numpy()
        kt = chk.index[m & (chk.corpus == "kth")].to_numpy()
        n_lb = min(per_lb, len(lb))
        n_kt = min(per_rule - n_lb, len(kt))
        n_lb = min(per_rule - n_kt, len(lb))
        picks += [(code, j) for j in list(rng.choice(lb, n_lb, replace=False)) + list(rng.choice(kt, n_kt,
                                                                                               replace=False))]
    corpora = ["leaderboard", "kth"]
    counts = chk.groupby(["corpus", "category"]).size().unstack(fill_value=0)
    rules = chk.groupby(["category", "rule", "kind", "corpus"]).size().unstack(fill_value=0)
    L = ["# E10 — проверка разбора исходов проверок агента", "",
         f"Построено `python -m src.e10_check_outcomes parser-check` (seed {seed}). Раунд 4 — точечная проверка: по "
         f"{per_rule} случайных проверок на каждое изменённое правило (A, B, C, D, E замечаний автора к выборке "
         f"seed 45; B — два правила, B1 и B2), до {per_lb} с лидерборда, остальные из KTH. Выборки прошлых раундов — "
         "в истории git (seed 45 — коммит f712e4e). Правила разбора — `reports/E10_PROTOCOL.md`, раздел 4, с "
         "исправлениями ниже. Для каждой проверки: корпус, связка или настройка, прогон и шаг, тесты проекта или "
         "самодельный скрипт, категория в раундах 3 и 4, сработавшее правило, вид ошибки и строка, команда, последние "
         "15 строк вывода (вывод полный, из источников раздела 2 протокола; ANSI-коды сняты).", "",
         "## Исправления разбора", ""]
    L += FIXES4 + [""]
    if r3 is not None:
        both = df[df.is_check | df.is_check_r3.fillna(False)]
        old = np.where(both.is_check_r3.fillna(False), both.category_r3, "не проверка")
        new = np.where(both.is_check, both.category, "не проверка")
        ct = pd.crosstab(pd.Series(old, name="r3"), pd.Series(new, name="r4"))
        cols = list(ct.columns)
        L += ["**Изменение категорий по сравнению с раундом 3** (все команды, которые были проверкой хотя бы в одном "
              "раунде; строки — раунд 3, столбцы — раунд 4):", ""]
        L += _md_table(["раунд 3 \\ раунд 4"] + cols + ["всего"],
                       [[i] + [int(ct.loc[i, c]) for c in cols] + [int(ct.loc[i].sum())] for i in ct.index] +
                       [["всего"] + [int(ct[c].sum()) for c in cols] + [int(ct.values.sum())]])
        L += [""]
    L += FIXES3 + [""] + FIXES + [""]
    L += ["## Все проверки: число по корпусам и категориям", ""]
    L += _md_table(["корпус"] + list(CATS) + ["всего"],
                   [[c] + [int(counts.loc[c].get(k, 0)) for k in CATS] + [int(counts.loc[c].sum())] for c in corpora])
    L += ["", "## Разбивка по правилам", "",
          "`kind` — вид ошибки (раунд 3, п. 3); `?-…` — вид не определён по выводу, такие ошибки относятся к "
          "`error_env` (раунд 3, п. 3).", ""]
    L += _md_table(["категория", "правило", "kind", "лидерборд", "KTH"],
                   [[c, f"`{r}`", f"`{k}`" if k else "—", int(v.get("leaderboard", 0)), int(v.get("kth", 0))]
                    for (c, r, k), v in rules.iterrows()])
    sc = df[(df.excl != "") | df.created_here]
    L += ["", "## Самодельные скрипты: исключённые и созданные в той же команде", ""]
    L += _md_table(["", "лидерборд", "KTH"],
                   [["скрипт пишет в файлы репозитория (`open(…, 'w')`, `.write_text(`) — не проверка"] +
                    [int(((sc.excl == "script-writes") & (sc.corpus == c)).sum()) for c in corpora],
                    ["команда правит другой файл репозитория (`sed -i`, `cat >`, …) — не проверка"] +
                    [int(((sc.excl == "script-edits") & (sc.corpus == c)).sum()) for c in corpora],
                    ["создаёт сам запускаемый скрипт и запускает его — проверка"] +
                    [int((sc.created_here & (sc.corpus == c)).sum()) for c in corpora]])
    L += ["", "## Код возврата не от проверки (раунд 3, п. 5)", ""]
    nr = chk[~chk.rc_ok]
    L += _md_table(["", "лидерборд", "KTH"],
                   [["все проверки (у тестовых программ код возврата правилами не используется)"] +
                    [int((nr.corpus == c).sum()) for c in corpora],
                    ["из них самодельных скриптов"] + [int(((nr.corpus == c) & ~nr.is_runner).sum()) for c in corpora],
                    ["правило `6-rc-not-informative` (unknown)"] +
                    [int(((nr.corpus == c) & (nr.rule == "6-rc-not-informative")).sum()) for c in corpora]])
    src = json.loads((OUT_DIR / "source_check.json").read_text(encoding="utf-8"))
    L += ["", "## Сверка источника лидерборда (раунд 3, п. 6)", "",
          "Пять прогонов популяции, для которых на диске есть `.traj.json` (выбор `default_rng(45)` из "
          f"{len(list(LB_RAW.glob('*/trajs/*.traj.json')))} локальных файлов). Сравниваются все выводы команд прогона "
          "в порядке адаптера E0: текст из `trajectories.jsonl.gz` и текст сообщения в `.traj.json`, байт в байт.", ""]
    L += _md_table(["прогон", "выводов (.traj.json / jsonl)", "совпадают как есть", "совпадают после адаптера E0",
                    "байт (.traj.json / jsonl)", "выводов с обрезкой mini-SWE-agent"],
                   [[f"`{s['run_id']}`", f"{s['n_raw']} / {s['n_jsonl']}", s["same_as_is"], s["same_after_adapter"],
                     f"{s['bytes_raw']} / {s['bytes_jsonl']}", s["raw_elided"]] for s in src])
    L += ["", "## Правила раунда 4: сколько проверок затронуто", ""]
    L += _md_table(["", "правило", "лидерборд", "KTH"],
                   [[code, what] + [int((sel(chk) & (chk.corpus == c)).sum()) for c in corpora]
                    for code, what, sel in SPOT])
    d = chk[chk.d_flag]
    runs = df.groupby(["corpus", "run_id"]).code_edit_seg.max().ge(0).groupby(level=0)
    L += ["", "D: доля проверок, у которых все цели — файлы агента (тестовая программа запускала только их), среди "
          "тестовых программ, запускавших файлы или метки `tests/`; F: доля прогонов с найденной правкой кода проекта "
          "(протокол, 11.5) и доля проверок до неё.", ""]
    first = df[df.code_edit_seg >= 0].groupby(["corpus", "run_id"])[["pos", "code_edit_seg"]].first()
    cf = chk.merge(first.rename(columns={"pos": "p0", "code_edit_seg": "s0"}).reset_index(),
                   on=["corpus", "run_id"], how="left")
    before = cf.p0.isna() | (cf.pos < cf.p0) | ((cf.pos == cf.p0) & (cf.check_seg <= cf.s0))
    L += _md_table(["", "лидерборд", "KTH"],
                   [["D: проверок «тесты проекта → скрипт агента»"] +
                    [int(((d.corpus == c) & d.d_proj).sum()) for c in corpora],
                    ["D: проверок, где файл агента изменил только вид ошибки"] +
                    [int(((d.corpus == c) & ~d.d_proj).sum()) for c in corpora],
                    ["тестовых программ всего"] + [int(((chk.corpus == c) & chk.is_runner).sum()) for c in corpora],
                    ["F: прогонов с найденной правкой кода проекта"] +
                    [f"{runs.sum()[c]} из {runs.size()[c]} ({runs.mean()[c]:.1%})" for c in corpora],
                    ["F: проверок до первой правки кода (или без правки)"] +
                    [f"{int((before & (cf.corpus == c)).sum())} из {int((cf.corpus == c).sum())}" for c in corpora]])
    L += [""]
    for i, (code, j) in enumerate(picks, 1):
        r = chk.loc[j]
        cmd = r.command if len(r.command) <= 600 else r.command[:600] + " …[команда усечена в отчёте]"
        what = "тесты проекта" if r.is_project else "самодельный скрипт"
        if r.created_here:
            what += " (создан и запущен в этой команде)"
        was = ""
        if r3 is not None:
            was = (f"; в раунде 3: {r.category_r3}, " +
                   ("тесты проекта" if r.is_project_r3 else "самодельный скрипт"))
        L += [f"## {i}. {code}: {r.category} — {r.corpus}", "",
              f"- группа: {r.group}", f"- прогон: `{r.run_id}`, шаг {r.step_idx}",
              f"- {what}; программа: {r.runner or '—'}" +
              ("" if r.is_runner else f"; код возврата {'от проверки' if r.rc_ok else 'не от проверки'}"),
              f"- категория **{r.category}**, правило `{r.rule}`" + (f", вид ошибки `{r.kind}`" if r.kind else "") +
              f", строка: `{r.line or '—'}`; упавших: {r.n_failed}{was}",
              "", "Команда:", ""] + _fence(cmd) + ["", "Последние 15 строк вывода:", ""] + _fence(r.out_tail) + [""]
    path = REPORTS / "E10_parser_check.md"
    path.write_text("\n".join(L) + "\n", encoding="utf-8")
    log.info("parser check: %d checks -> %s", len(picks), path)
    return path


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "checks"
    {"checks": build_checks, "parser-check": parser_check}[cmd]()
