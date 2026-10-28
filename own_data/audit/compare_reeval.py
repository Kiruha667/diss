"""Label audit, step 3: leaderboard label vs our re-evaluation of the same patch (eval_requests.sh) for the runs with
resolved = 0 and network failures. For every failed test of our evaluation the reason is read from the error lines
('E ...') of its section of test_output.txt ('____ Class.test ____'). A failed test is an environment failure of our
side if its section shows one of the changes of httpbin.org observed on 2026-10-28:
  - ssl_cert: 'SSL: CERTIFICATE_VERIFY_FAILED' on https to httpbin.org (the certificate of httpbin.org changed on
    2026-10-26 and the CA bundle of the old requests in the task images does not accept it);
  - ssl_handshake_timeout: 'The handshake operation timed out' on https to httpbin.org;
  - redirect_to_403: test_auth_is_stripped_on_redirect_off_host fails with IndexError on r.history[0]: httpbin.org
    /redirect-to with an external url answers 403 instead of 302 (curl, 2026-10-28 08:41 UTC).
'our label without env' = resolved if the patch applied and every other FAIL_TO_PASS / PASS_TO_PASS test passed; it
assumes that the environment-failed tests would pass, which is weaker when such a test is FAIL_TO_PASS (column
our_env_f2p). A pair without our report because the patch is empty gets 0, as the harness counts empty patches.
Inputs: own_data/audit/requests_lb_audit.csv, own_data/audit/our_eval/<run>/<task>/report.json,
data/raw/label_audit/our_eval/<run>/<task>/test_output.txt, data/raw/label_audit/lb_requests/<run>/<task>/patch.diff.
Output: own_data/audit/reeval_compare.csv. Run from the repository root. No LLM calls.
"""
import json
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
A = ROOT / "own_data" / "audit"
RAW = ROOT / "data" / "raw" / "label_audit"
SECTION = re.compile(r"^_{3,} (\S+) _{3,}$", re.M)
SIGNS = {
    "http_5xx": re.compile(r"<title>50[234] [^<]*</title>|<Response \[50[234]\]>|assert 50[234] =="),
    "conn": re.compile(r"^E\s.*(?:ConnectionError|Max retries exceeded|NewConnectionError|Temporary failure in name "
                       r"resolution|Name or service not known|Failed to establish a new connection|Connection refused|"
                       r"Network is unreachable)", re.M),
    "timeout": re.compile(r"^E\s.*(?:ReadTimeout|ConnectTimeout|timed out|TimeoutError)", re.M),
    "ssl_cert": re.compile(r"^E\s.*CERTIFICATE_VERIFY_FAILED", re.M),
}


def sections(text: str) -> dict[str, str]:
    """failure section per test function name (last component of 'Class.test')."""
    marks = list(SECTION.finditer(text))
    out = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        out[m.group(1).split(".")[-1]] = text[m.end():end]
    return out


def env_reason(test: str, sec: dict[str, str]) -> str | None:
    name = re.sub(r"\[.*$", "", test.split("::")[-1])
    err = "\n".join(l for l in sec.get(name, "").splitlines() if l.startswith("E "))
    if "CERTIFICATE_VERIFY_FAILED" in err:
        return "ssl_cert"
    if "handshake operation timed out" in err:
        return "ssl_handshake_timeout"
    if name == "test_auth_is_stripped_on_redirect_off_host" and "IndexError" in err:
        return "redirect_to_403"
    return None


def main() -> None:
    lb = pd.read_csv(A / "requests_lb_audit.csv")
    todo = json.loads((A / "reeval_list.json").read_text(encoding="utf-8"))
    rows = []
    for run, tasks in todo.items():
        for task in tasks:
            rep_p = A / "our_eval" / run / task / "report.json"
            l = lb[(lb.run_id == run) & (lb.task_id == task)].iloc[0]
            row = {"run_id": run, "task_id": task, "model": l.model, "resolved_lb": int(l.resolved_lb_report),
                   "lb_sign_http_5xx": int(l.sign_http_5xx), "lb_failed_network_tests": int(l.failed_network_tests),
                   "lb_failed_other_tests": int(l.failed_other_tests)}
            if not rep_p.exists():
                empty = (RAW / "lb_requests" / run / task / "patch.diff").read_text(encoding="utf-8").strip() == ""
                rows.append({**row, "evaluated": False, "note": "empty patch" if empty else "no report",
                             **({"resolved_ours": 0, "resolved_ours_without_env": 0} if empty else {})})
                continue
            rep = json.loads(rep_p.read_text(encoding="utf-8"))[task]
            ts = rep["tests_status"]
            text = (RAW / "our_eval" / run / task / "test_output.txt").read_text(encoding="utf-8", errors="replace")
            sec = sections(text)
            f2p, p2p = ts["FAIL_TO_PASS"]["failure"], ts["PASS_TO_PASS"]["failure"]
            env = {t: env_reason(t, sec) for t in f2p + p2p}
            other = [t for t, r in env.items() if r is None]
            short = lambda ts_: " ".join(t.split("::")[-1] for t in ts_)
            rows.append({**row, "evaluated": True, "note": "", "resolved_ours": int(bool(rep["resolved"])),
                         "resolved_ours_without_env": int(bool(rep.get("patch_successfully_applied")) and not other),
                         **{f"our_sign_{k}": len(p.findall(text)) for k, p in SIGNS.items()},
                         "our_env_f2p": sum(env[t] is not None for t in f2p),
                         "our_env_p2p": sum(env[t] is not None for t in p2p),
                         "our_failed_env": " ".join(f"{t.split('::')[-1]}:{r}" for t, r in env.items() if r),
                         "our_failed_other": short(other)})
    df = pd.DataFrame(rows)
    df.to_csv(A / "reeval_compare.csv", index=False)
    with pd.option_context("display.width", 250, "display.max_colwidth", 70):
        print(df.drop(columns=["run_id", "our_failed_env"]).assign(run=df.run_id.str[9:30]).to_string())
    print("evaluated:", int(df.evaluated.sum()), "of", len(df), "| notes:", df.note[df.note != ""].tolist())
    lab = df.dropna(subset=["resolved_ours"])
    print("label changed (ours vs lb):", int((lab.resolved_ours != lab.resolved_lb).sum()),
          "| without env:", int((lab.resolved_ours_without_env != lab.resolved_lb).sum()),
          "| of them with env-failed FAIL_TO_PASS:",
          int(((lab.resolved_ours_without_env != lab.resolved_lb) & (lab.our_env_f2p > 0)).sum()))


if __name__ == "__main__":
    main()
