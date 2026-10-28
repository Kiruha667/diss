"""Stage V (PROTOCOL.md 11.7): result of the gold-patch evaluation (infra/gold_eval.sh) of the tasks of tasks_main.json.
  python own_data/gold_validation.py pre    -> own_data/gold_validation.json (excluded = gold not resolved under
                                               gold_pre), own_data/gold_validation_pre.csv
  python own_data/gold_validation.py post   -> own_data/gold_validation_post.csv: gold_pre vs gold_post per task
For every task without resolved = true: failed FAIL_TO_PASS / PASS_TO_PASS tests and the error lines ('E ...',
'Error', 'Traceback' tails) of their sections in test_output.txt, and a heuristic hint of an external dependency:
network, certificate, package (import / missing module / version), image (no report: the harness log is read).
The hint is a starting point for the manual reading written in GOLD_VALIDATION.md, not a decision.
No LLM calls. Run from the repository root.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OWN = ROOT / "own_data"
HINTS = {
    "certificate": re.compile(r"CERTIFICATE_VERIFY_FAILED|SSLError|SSLCertVerificationError|certificate verify"),
    "network": re.compile(r"ConnectionError|Max retries exceeded|NewConnectionError|name resolution|Name or service not "
                          r"known|Failed to establish|Connection refused|Network is unreachable|urlopen error|"
                          r"ReadTimeout|ConnectTimeout|timed out|HTTP Error 5\d\d|<Response \[5\d\d\]>|503 Service"),
    "package": re.compile(r"ModuleNotFoundError|No module named|ImportError|DistributionNotFound|VersionConflict|"
                          r"pkg_resources|DeprecationWarning: .* is deprecated|AttributeError: module '"),
}
SECTION = re.compile(r"^_{3,} (\S+) _{3,}$", re.M)


def sections(text: str) -> dict[str, str]:
    marks = list(SECTION.finditer(text))
    return {m.group(1).split(".")[-1]: text[m.end():(marks[i + 1].start() if i + 1 < len(marks) else len(text))]
            for i, m in enumerate(marks)}


def task_row(run: str, iid: str) -> dict:
    d = OWN / "eval" / run / iid
    rep_p = d / "report.json"
    if not rep_p.exists():
        log = (d / "run_instance.log").read_text(encoding="utf-8", errors="replace") if (d / "run_instance.log").exists() else ""
        tail = " | ".join(l.strip() for l in log.splitlines()[-5:])
        return {"task_id": iid, f"resolved_{run}": None, "report": False, "patch_applied": None, "failed_f2p": "",
                "failed_p2p": "", "hint": "image/harness (no report)", "errors": tail[:600]}
    rep = json.loads(rep_p.read_text(encoding="utf-8"))[iid]
    ts = rep.get("tests_status") or {}
    f2p = (ts.get("FAIL_TO_PASS") or {}).get("failure") or []
    p2p = (ts.get("PASS_TO_PASS") or {}).get("failure") or []
    text = (d / "test_output.txt").read_text(encoding="utf-8", errors="replace") if (d / "test_output.txt").exists() else ""
    sec = sections(text)
    errs = []
    for t in f2p + p2p:
        body = sec.get(re.sub(r"\[.*$", "", t.split("::")[-1]), "") if "::" in t else ""
        errs += [l.strip() for l in body.splitlines() if l.startswith("E ")][-2:]
    if not errs and (f2p or p2p):  # django / sympy runners: error lines of the whole output
        errs = [l.strip() for l in text.splitlines() if re.match(r"^(E\s|\w*Error:|ERROR:|FAIL:|AssertionError)", l)][-6:]
    blob = "\n".join(errs) + "\n" + (text if not errs else "")
    hint = [k for k, p in HINTS.items() if p.search(blob)]
    return {"task_id": iid, f"resolved_{run}": int(bool(rep.get("resolved"))), "report": True,
            "patch_applied": rep.get("patch_successfully_applied"), "failed_f2p": " ".join(f2p),
            "failed_p2p": " ".join(p2p), "hint": " + ".join(hint) or "none found",
            "errors": " | ".join(dict.fromkeys(errs))[:800]}


def tasks() -> list[str]:
    return [t["task_id"] for t in json.loads((OWN / "tasks_main.json").read_text(encoding="utf-8"))["tasks"]]


def main() -> None:
    mode = sys.argv[1]
    if mode == "pre":
        df = pd.DataFrame([task_row("gold_pre", t) for t in tasks()])
        df.to_csv(OWN / "gold_validation_pre.csv", index=False)
        bad = df[df.resolved_gold_pre != 1]
        (OWN / "gold_validation.json").write_text(json.dumps({
            "run_id": "gold_pre", "n_tasks": len(df), "n_resolved": int((df.resolved_gold_pre == 1).sum()),
            "excluded": bad.task_id.tolist(), "stop_rule": "more than 5 excluded -> stop (PROTOCOL.md 11.7)",
            "stop": bool(len(bad) > 5)}, indent=1), encoding="utf-8")
        print(df.resolved_gold_pre.value_counts(dropna=False).to_string())
        print(bad.drop(columns=["errors"]).to_string())
    elif mode == "post":
        pre = pd.read_csv(OWN / "gold_validation_pre.csv")[["task_id", "resolved_gold_pre"]]
        excluded = set(json.loads((OWN / "gold_validation.json").read_text(encoding="utf-8"))["excluded"])
        post = pd.DataFrame([task_row("gold_post", t) for t in tasks() if t not in excluded])
        df = post.merge(pre, on="task_id", how="left")
        df["differs"] = df.resolved_gold_pre != df.resolved_gold_post
        df.to_csv(OWN / "gold_validation_post.csv", index=False)
        print(df.resolved_gold_post.value_counts(dropna=False).to_string())
        print(df[df.differs].drop(columns=["errors"]).to_string())


if __name__ == "__main__":
    main()
