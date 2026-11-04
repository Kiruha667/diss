"""E10 (reports/E10_PROTOCOL.md): outcomes of the agent's own checks (block O). No LLM calls.

  python -m src.e10_check_outcomes checks        -> data/interim/e10/commands.parquet (every executed command of the
                                                    leaderboard population and of the KTH attempts with a patch;
                                                    checks with their parsed outcome)
  python -m src.e10_check_outcomes parser-check  -> reports/E10_parser_check.md (60 checks, 15 per category)

Sources: leaderboard = data/interim/swebench_bashonly/trajectories.jsonl.gz (E0 adapter, full tool outputs);
KTH = data/raw/agentic_evals via src.adapters.agentic_evals (E8). Non-zero return codes are a "returncode: N" prefix
of the tool output in both. A check = a command with a demo_lib 'verify' event or a test-runner invocation; its
outcome is parsed from the full output of the command (protocol, sections 3-4).
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from src.adapters.agentic_evals import SETTINGS, iter_raw, labels_of, run_dirs, to_record
from src.common import INTERIM, REPORTS, ROOT, SEED, get_logger, read_jsonl_gz, trajectories_path

sys.path.insert(0, str(ROOT))
from demo_lib.phases import (_QUOTED_RE, _SPLIT_RE, _cut_heredocs, _is_python, _words,  # noqa: E402
                             command_events)

log = get_logger("e10")
OUT_DIR = INTERIM / "e10"
CATS = ("pass", "fail", "error", "unknown")

# ------------------------------------------------------------------------------------------ test runners

_PROJECT_RUNNERS = {"runtests", "bin/test", "tox", "make test", "django test"}
_PATH_TESTS_RE = re.compile(r"(^|/)(tests|testing)(/|$)")
_LABEL_RE = re.compile(r"^[A-Za-z_][\w]*(\.[\w]+)+$")


def _segments(cmd: str) -> list[str]:
    """Command segments split as in demo_lib (heredoc bodies cut out, quoted strings kept)."""
    flat, _ = _cut_heredocs(cmd)
    quotes: list[str] = []

    def protect(m):
        quotes.append(m.group(0))
        return f"__QUOTE{len(quotes) - 1}__"

    segs = []
    for seg in _SPLIT_RE.split(_QUOTED_RE.sub(protect, flat)):
        seg = re.sub(r"__QUOTE(\d+)__", lambda m: quotes[int(m.group(1))], seg)
        if seg.strip():
            segs.append(seg)
    return segs


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


def is_project_run(runner: str, args: list[str]) -> bool:
    if runner in _PROJECT_RUNNERS:
        return True
    targets = [a.strip("'\"") for a in args if not a.startswith("-")]
    paths = [a for a in targets if "/" in a or a.endswith(".py") or "::" in a]
    labels = [a for a in targets if _LABEL_RE.match(a)]
    if any(_PATH_TESTS_RE.search(p.split("::")[0]) for p in paths):
        return True
    if any(lb.startswith("tests.") or ".tests." in lb for lb in labels):
        return True
    return not paths and not labels  # no target: the runner collects the project's tests


# ------------------------------------------------------------------------------------------ outcome parser

_PYTEST_RE = re.compile(r"^[=\s!*-]*(?P<body>(?:\d+ (?:failed|passed|errors?|skipped|xfailed|xpassed|deselected|"
                        r"warnings?|rerun)(?:,\s*)?)+|no tests ran)\s+in\s+[\d.]+\s*(?:s|sec|seconds)\b", re.M)
_RAN_RE = re.compile(r"^Ran (\d+) tests? in [\d.]+s", re.M)
_UT_STATUS_RE = re.compile(r"^(OK\b.*|FAILED \((?P<det>[^)]*)\))", re.M)
_SYMPY_RE = re.compile(r"tests finished:(?P<body>[^\n]*(?:\n[^\n]*)?)in [\d.]+ seconds")  # may wrap once
# fixes after the seed-42 parser check (reports/E10_parser_check.md, section "Исправления"):
# (a) ANSI colour codes are stripped before parsing; (b) IndentationError / TabError are SyntaxError subclasses;
# (c) unittest.loader._FailedTest (a test that could not be loaded) is a collection error; (d) a pytest summary with
# only skipped / xfailed / xpassed has no pass/fail outcome; (e) runner calls with only informational flags
# (--version, --help, --collect-only, ...) are not checks
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_IMPORT_RE = re.compile(r"\b(ImportError|ModuleNotFoundError|SyntaxError|IndentationError|TabError)\b|"
                        r"Failed to import test module|unittest\.loader\._FailedTest")
_ERROR_RE = re.compile(r"\b(ImportError|ModuleNotFoundError|SyntaxError|IndentationError|TabError)\b|No module named|"
                       r"errors? during collection|collected 0 items|ERROR: file or directory not found|"
                       r"ERROR: not found|can't open file|command not found")
_INFO_FLAGS = {"--version", "-V", "--help", "-h", "--collect-only", "--co", "--fixtures", "--markers"}
_IGNORABLE = {"cd", "source", "export", "echo", "head", "tail", "grep", "set"}
_TRACE_RE = re.compile(r"Traceback \(most recent call last\)|\bAssertionError\b")
_PIPE_RE = re.compile(r"\|\s*(head|tail|grep)\b")
_LONG_RE = re.compile(r"too long|characters elided|output truncated", re.I)
_TIMEOUT_RE = re.compile(r"timed out|TimeoutExpired", re.I)
_RC_RE = re.compile(r"^returncode: (-?\d+)\n")


def _n(body: str, word: str) -> int:
    return sum(int(x) for x in re.findall(rf"(\d+) {word}", body))


def parse_outcome(output: str, command: str, is_runner: bool) -> tuple[str, str, str, int]:
    """(category, rule, matched line, number of failed tests) of one check (protocol, section 4)."""
    cands = []
    for m in _PYTEST_RE.finditer(output):
        body = m.group("body")
        f, e, p = _n(body, "failed"), _n(body, "errors?"), _n(body, "passed")
        only_skips = re.search(r"\d+ (skipped|xfailed|xpassed)", body) is not None
        cat = ("fail" if f else "error" if e else "pass" if p else "unknown" if only_skips else "error")
        rule = "1-pytest-no-outcome" if cat == "unknown" else "1-pytest"
        cands.append((m.start(), cat, rule, m.group(0).strip(), f + e if cat == "fail" else 0))
    for m in _RAN_RE.finditer(output):
        st = _UT_STATUS_RE.search(output, m.end())
        if not st:
            continue
        n_ran = int(m.group(1))
        if st.group(0).startswith("OK"):
            cat, nf = ("pass" if n_ran > 0 else "error"), 0
        else:
            det = st.group("det") or ""
            fl = int((re.search(r"failures=(\d+)", det) or [0, 0])[1])
            er = int((re.search(r"errors=(\d+)", det) or [0, 0])[1])
            cat = "error" if fl == 0 and er > 0 and _IMPORT_RE.search(output) else "fail"
            nf = fl + er if cat == "fail" else 0
        if n_ran == 0:
            cat, nf = "error", 0
        cands.append((m.start(), cat, "1-unittest", f"{m.group(0).strip()} / {st.group(0).strip()}", nf))
    for m in _SYMPY_RE.finditer(output):
        body = m.group("body")
        f, x, p = _n(body, "failed"), _n(body, "exceptions?"), _n(body, "passed")
        cat = "fail" if f or x else "pass" if p else "unknown"
        if cat != "unknown":
            cands.append((m.start(), cat, "1-sympy", m.group(0).strip(), f + x if cat == "fail" else 0))
    if cands:
        _, cat, rule, line, nf = max(cands, key=lambda c: c[0])
        return cat, rule, line, nf
    m = _ERROR_RE.search(output)
    if m:
        return "error", "2-collect/import", m.group(0), 0
    if not is_runner:
        m = _TRACE_RE.search(output)
        if m:
            return "fail", "3-script-traceback", m.group(0), 1
    if _PIPE_RE.search(command):
        return "unknown", "4-agent-pipe", _PIPE_RE.search(command).group(0), 0
    m = _LONG_RE.search(output)
    if m:
        return "unknown", "4-truncated", m.group(0), 0
    m = _TIMEOUT_RE.search(output)
    if m:
        return "unknown", "4-timeout", m.group(0), 0
    if is_runner and "Traceback (most recent call last)" in output:  # untruncated, no summary: crashed before tests
        return "error", "4b-runner-crash", "Traceback (most recent call last)", 0
    rc = _RC_RE.match(output)
    if not is_runner and rc is None:
        return "pass", "5-script-rc0", "", 0
    return "unknown", "6-other", f"rc={rc.group(1)}" if rc else "rc=0", 0


# ------------------------------------------------------------------------------------------ commands of a run

def command_rows(rec: dict, corpus: str, group: str) -> list[dict]:
    """Every executed command of a run in order, with edit / check flags and the parsed outcome of checks."""
    outs: dict[int, list[str]] = {}
    for s in rec["steps"]:
        if s["role"] == "tool":
            outs.setdefault(s["step_idx"], []).append(s["text"])
    files: dict[str, str] = {}
    rows, pos = [], 0
    for s in rec["steps"]:
        if s["role"] != "assistant":
            continue
        cmds = s["tool_calls"]
        o = outs.get(s["step_idx"], [])
        paired = len(o) == len(cmds)
        for i, cmd in enumerate(cmds):
            kinds = {k for k, _ in command_events(cmd, files)}
            segs = _segments(cmd)
            runners = [r for r in (runner_of(seg) for seg in segs) if r]
            info = [r for r in runners if _INFO_FLAGS & set(r[1])]
            runners = [r for r in runners if r not in info]
            main = [s for s in segs if (_words(s) or [""])[0] not in _IGNORABLE]
            info_only = bool(info) and not runners and len(main) == len(info)
            is_check = ("verify" in kinds or bool(runners)) and not info_only
            row = {"corpus": corpus, "group": group, "run_id": rec["run_id"], "step_idx": s["step_idx"], "pos": pos,
                   "is_edit": "edit" in kinds, "is_check": is_check, "is_runner": bool(runners),
                   "runner": runners[0][0] if runners else "", "is_project": False, "category": "",
                   "rule": "", "line": "", "n_failed": 0, "command": cmd, "out_tail": ""}
            if is_check:
                row["is_project"] = any(is_project_run(r, a) for r, a in runners)
                if paired:
                    out = _ANSI_RE.sub("", o[i])
                    cat, rule, line, nf = parse_outcome(out, cmd, bool(runners))
                    row["out_tail"] = "\n".join(out.rstrip("\n").split("\n")[-15:])
                else:
                    cat, rule, line, nf = "unknown", "unpaired_output", f"{len(cmds)} calls / {len(o)} outputs", 0
                row.update({"category": cat, "rule": rule, "line": line[:200], "n_failed": nf})
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


def build_checks() -> pd.DataFrame:
    t0 = time.time()
    rows = []
    n = 0
    for rec, grp in leaderboard_records():
        rows += command_rows(rec, "leaderboard", grp)
        n += 1
    log.info("leaderboard: %d runs, %d commands, %.0f s", n, len(rows), time.time() - t0)
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
    "Первая выборка (seed 42) просмотрена до передачи автору. Найдены систематические ошибки; разбор исправлен, "
    "проверки пересобраны, эта выборка — новая (seed 43), как требует протокол (раздел 5).",
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


def parser_check(seed: int = 44, per_cat: int = 15, per_lb: int = 8) -> Path:
    df = pd.read_parquet(OUT_DIR / "commands.parquet")
    chk = df[df.is_check].reset_index(drop=True)
    rng = np.random.default_rng(seed)
    picks = []
    for cat in CATS:
        lb = chk.index[(chk.category == cat) & (chk.corpus == "leaderboard")].to_numpy()
        kt = chk.index[(chk.category == cat) & (chk.corpus == "kth")].to_numpy()
        n_lb = min(per_lb, len(lb))
        n_kt = min(per_cat - n_lb, len(kt))
        n_lb = min(per_cat - n_kt, len(lb))
        picks += list(rng.choice(lb, n_lb, replace=False)) + list(rng.choice(kt, n_kt, replace=False))
    counts = chk.groupby(["corpus", "category"]).size().unstack(fill_value=0)
    rules = chk.groupby(["category", "rule"]).size()
    L = ["# E10 — проверка разбора исходов проверок агента", "",
         f"Построено `python -m src.e10_check_outcomes parser-check` (seed {seed}): по {per_cat} случайных проверок "
         f"на категорию, из них до {per_lb} с лидерборда, остальные из KTH. Правила разбора — "
         "`reports/E10_PROTOCOL.md`, раздел 4. Для каждой проверки: корпус, связка или настройка, прогон и шаг, "
         "тесты проекта или самодельный скрипт, программа запуска, категория и сработавшее правило со строкой, "
         "команда, последние 15 строк вывода (вывод полный, из источников раздела 2 протокола; ANSI-коды сняты).", "",
         "## Исправления разбора после первой выборки", ""] + FIXES + ["",
         "## Все проверки: число по корпусам и категориям", "",
         "| корпус | " + " | ".join(CATS) + " | всего |", "|---" * (len(CATS) + 2) + "|"]
    for corpus, r in counts.iterrows():
        L.append(f"| {corpus} | " + " | ".join(str(int(r.get(c, 0))) for c in CATS) + f" | {int(r.sum())} |")
    L += ["", "Правила по категориям: " + "; ".join(f"{c} / {r}: {n}" for (c, r), n in rules.items()), ""]
    for i, j in enumerate(picks, 1):
        r = chk.loc[j]
        cmd = r.command if len(r.command) <= 600 else r.command[:600] + " …[команда усечена в отчёте]"
        L += [f"## {i}. {r.category} — {r.corpus}", "",
              f"- группа: {r.group}", f"- прогон: `{r.run_id}`, шаг {r.step_idx}",
              f"- {'тесты проекта' if r.is_project else 'самодельный скрипт'}; программа: {r.runner or '—'}",
              f"- категория **{r.category}**, правило `{r.rule}`, строка: `{r.line or '—'}`; упавших: {r.n_failed}",
              "", "Команда:", ""] + _fence(cmd) + ["", "Последние 15 строк вывода:", ""] + _fence(r.out_tail) + [""]
    path = REPORTS / "E10_parser_check.md"
    path.write_text("\n".join(L) + "\n", encoding="utf-8")
    log.info("parser check: %d checks -> %s", len(picks), path)
    return path


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "checks"
    {"checks": build_checks, "parser-check": parser_check}[cmd]()
