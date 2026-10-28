"""Runs INSIDE a task container (/testbed, test patch already applied): which tests of FAIL_TO_PASS / PASS_TO_PASS
need the external network. A test is 'network' if its body
  - calls httpbin(...) or uses HTTPBIN, where httpbin is the module-level helper built on HTTPBIN whose default is
    an external host
    (old requests: HTTPBIN = os.environ.get('HTTPBIN_URL', 'http://httpbin.org/')); with the pytest-httpbin fixture
    (a local server) calls of httpbin(...) are not network;
  - or contains a literal URL to a host other than localhost / 127.0.0.1 (reported separately: such a test may
    expect a connection error and then passes without the network).
Helpers called from a test and fixtures are not followed (limitation, see LABEL_AUDIT_REQUESTS.md).
usage: python scan_network_tests.py <instances.json> <instance_id>   -> JSON on stdout
"""
import ast
import json
import re
import sys
from pathlib import Path

URL_RE = re.compile(r"https?://(?!localhost\b|127\.0\.0\.1|0\.0\.0\.0|\[::1\])([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")


def consts(node):
    for n in ast.walk(node):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            yield n.value
        elif type(n).__name__ == "Str":  # Python < 3.8
            yield n.s


def scan_file(path: Path) -> dict:
    src = path.read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(src)
    httpbin_default = None
    helper = False
    for n in tree.body:
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "HTTPBIN" for t in n.targets):
            # first assignment holds the default; later ones (HTTPBIN = HTTPBIN.rstrip('/') + '/') have no literal
            httpbin_default = httpbin_default or next((s for s in consts(n.value) if s.startswith("http")), None)
        if isinstance(n, ast.FunctionDef) and n.name == "httpbin":
            helper = not any("fixture" in ast.dump(d) for d in n.decorator_list)
    external_helper = bool(helper and httpbin_default and URL_RE.search(httpbin_default))
    tests = {}

    def visit(fn, prefix):
        calls = any((isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id == "httpbin") or
                    (isinstance(c, ast.Name) and c.id == "HTTPBIN") for c in ast.walk(fn))
        lits = sorted({m.group(1) for s in consts(fn) for m in URL_RE.finditer(s)})
        tests[prefix + fn.name] = {"external_httpbin": bool(calls and external_helper), "external_literals": lits}

    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and n.name.startswith("test"):
            visit(n, "")
        elif isinstance(n, ast.ClassDef):
            for f in n.body:
                if isinstance(f, ast.FunctionDef) and f.name.startswith("test"):
                    visit(f, n.name + "::")
    return {"httpbin_default": httpbin_default, "httpbin_helper": helper, "external_helper": external_helper,
            "tests": tests}


def main() -> None:
    inst = json.loads(Path(sys.argv[1]).read_text())[sys.argv[2]]
    names = inst["FAIL_TO_PASS"] + inst["PASS_TO_PASS"]
    files = sorted({n.split("::")[0] for n in names})
    scanned = {f: scan_file(Path("/testbed") / f) for f in files}
    out = {}
    for kind in ("FAIL_TO_PASS", "PASS_TO_PASS"):
        for n in inst[kind]:
            f, rest = n.split("::", 1)
            key = re.sub(r"\[.*$", "", rest)  # parametrized ids, also ones cut at a space
            t = scanned[f]["tests"].get(key)
            out[n] = {"kind": kind, "found": t is not None,
                      "external_httpbin": bool(t and t["external_httpbin"]),
                      "external_literals": t["external_literals"] if t else []}
    print(json.dumps({"files": {f: {k: v for k, v in s.items() if k != "tests"} for f, s in scanned.items()},
                      "tests": out}))


if __name__ == "__main__":
    main()
