"""Label audit, step 2: network failures in the leaderboard evaluation of the psf/requests tasks (16 runs x 8 tasks).
Inputs: data/raw/label_audit/lb_requests/<run>/<task>/{report.json, test_output.txt} (fetch_requests_logs.py),
own_data/audit/requests_network_tests.json (network_tests.sh), data/demo/runs.parquet (corpus labels).
Network-failure signs are counted only where they are errors, not names of tests or code in the patch:
  - http_5xx: an HTML error page or a response of httpbin with 502/503/504 ('<title>503 Service Temporarily
    Unavailable</title>', '<Response [503]>', 'assert 503 ==');
  - conn: pytest error lines ('E   ...') with ConnectionError / Max retries exceeded / NewConnectionError /
    Temporary failure in name resolution / Name or service not known / Failed to establish a new connection /
    Connection refused / Network is unreachable;
  - timeout: pytest error lines with ReadTimeout / ConnectTimeout / timed out / TimeoutError.
Writes own_data/audit/requests_lb_audit.csv and own_data/audit/reeval_list.json (runs with resolved = 0 and network
failures: they are re-evaluated by eval_requests.sh). Run from the repository root. No LLM calls.
"""
import json
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw" / "label_audit" / "lb_requests"
OUT = ROOT / "own_data" / "audit"
SIGNS = {
    "http_5xx": re.compile(r"<title>50[234] [^<]*</title>|<Response \[50[234]\]>|assert 50[234] =="),
    "conn": re.compile(r"^E\s.*(?:ConnectionError|Max retries exceeded|NewConnectionError|Temporary failure in name "
                       r"resolution|Name or service not known|Failed to establish a new connection|Connection refused|"
                       r"Network is unreachable)", re.M),
    "timeout": re.compile(r"^E\s.*(?:ReadTimeout|ConnectTimeout|timed out|TimeoutError)", re.M),
}
SECTION = re.compile(r"^_{3,} (\S+) _{3,}$", re.M)
NET_IN_SECTION = re.compile(r"\b50[234]\b|ConnectionError|Max retries exceeded|NewConnectionError|name resolution|"
                            r"Name or service not known|Failed to establish|Connection refused|Network is unreachable|"
                            r"ReadTimeout|ConnectTimeout|timed out|TimeoutError")


def tests(rep: dict, kind: str, outcome: str) -> list[str]:
    return ((rep.get("tests_status") or {}).get(kind) or {}).get(outcome) or []


def sections(text: str) -> dict[str, str]:
    """pytest failure section per test function name (last component of 'Class.test')."""
    marks = list(SECTION.finditer(text))
    return {m.group(1).split(".")[-1]: text[m.end():(marks[i + 1].start() if i + 1 < len(marks) else len(text))]
            for i, m in enumerate(marks)}


def failed_with_net_sign(failed: list[str], sec: dict[str, str]) -> list[str]:
    """failed tests whose own failure section contains a network / 5xx / timeout error ('E' and '>' lines)."""
    out = []
    for t in failed:
        body = sec.get(re.sub(r"\[.*$", "", t.split("::")[-1]), "")
        err = "\n".join(l for l in body.splitlines() if l.startswith(("E ", ">")))
        if NET_IN_SECTION.search(err):
            out.append(t)
    return out


def main() -> None:
    corpus = pd.read_parquet(ROOT / "data" / "demo" / "runs.parquet", columns=["run_id", "task_id", "model", "resolved"])
    corpus = corpus.set_index("run_id")  # "<run>/<task>"
    net = json.loads((OUT / "requests_network_tests.json").read_text(encoding="utf-8"))
    rows = []
    for d in sorted(RAW.glob("*/*/")):
        run, task = d.parent.name, d.name
        rep = json.loads((d / "report.json").read_text(encoding="utf-8"))
        rep = rep.get(task, rep)
        text = (d / "test_output.txt").read_text(encoding="utf-8", errors="replace")
        if text.count("\\n") > text.count("\n"):  # stored with escaped newlines
            text = text.replace("\\n", "\n")
        signs = {k: len(p.findall(text)) for k, p in SIGNS.items()}
        failed = tests(rep, "FAIL_TO_PASS", "failure") + tests(rep, "PASS_TO_PASS", "failure")
        nt = net[task]["tests"]
        failed_net = [t for t in failed if nt.get(t, {}).get("external_httpbin")]
        failed_lit = [t for t in failed if not nt.get(t, {}).get("external_httpbin") and nt.get(t, {}).get("external_literals")]
        failed_sign = failed_with_net_sign(failed, sections(text))
        c = corpus.loc[f"{run}/{task}"]
        rows.append({
            "run_id": run, "task_id": task, "model": c["model"],
            "resolved_corpus": int(c["resolved"]), "resolved_lb_report": int(bool(rep.get("resolved"))),
            "patch_applied": rep.get("patch_successfully_applied"),
            **{f"sign_{k}": v for k, v in signs.items()},
            "network_failure": any(signs.values()),
            "f2p_failed": len(tests(rep, "FAIL_TO_PASS", "failure")), "p2p_failed": len(tests(rep, "PASS_TO_PASS", "failure")),
            "failed_network_tests": len(failed_net), "failed_external_literal_tests": len(failed_lit),
            "failed_other_tests": len(failed) - len(failed_net) - len(failed_lit),
            "failed_with_network_error": len(failed_sign),
            "failed_tests": " ".join(t.split("::")[-1] for t in failed),
        })
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "requests_lb_audit.csv", index=False)
    assert (df.resolved_corpus == df.resolved_lb_report).all(), "corpus label differs from the downloaded report"
    re_list = df[(df.resolved_lb_report == 0) & df.network_failure]
    (OUT / "reeval_list.json").write_text(json.dumps(
        {r: sorted(g.task_id) for r, g in re_list.groupby("run_id")}, indent=1), encoding="utf-8")
    print(df.groupby("task_id").agg(runs=("run_id", "size"), resolved=("resolved_lb_report", "sum"),
                                    with_network_failure=("network_failure", "sum")).to_string())
    print(pd.crosstab(df.network_failure, df.resolved_lb_report).to_string())
    print(df[df.network_failure][["run_id", "task_id", "resolved_lb_report", "sign_http_5xx", "sign_conn", "sign_timeout",
                                  "f2p_failed", "p2p_failed", "failed_network_tests", "failed_external_literal_tests",
                                  "failed_other_tests", "failed_with_network_error"]].to_string())
    print("re-evaluation list:", len(re_list), "runs")


if __name__ == "__main__":
    main()
