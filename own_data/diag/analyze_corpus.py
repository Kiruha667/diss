"""Leaderboard evaluation artifacts of the corpus run 20251201_mini-v1.17.1_glm-4.6 on the 5 pilot tasks
(own_data/diag/corpus_glm46/<task>/logs/): resolved, failed FAIL_TO_PASS / PASS_TO_PASS tests, environment problems
(network, timeout, dependency install, patch not applied) found in test_output.txt / job-output.json.
Writes own_data/diag/corpus_glm46/leaderboard_eval.json. Run from the repository root.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "own_data" / "diag" / "corpus_glm46"
ENV_PATTERNS = {
    "network": r"(?:ConnectionError|Max retries exceeded|NewConnectionError|Temporary failure in name resolution|"
               r"Name or service not known|Failed to establish a new connection|Connection refused|"
               r"Network is unreachable)[^\n]{0,100}",
    "http_503_external_service": r"(?:assert 503|503 Service Unavailable|Service Temporarily Unavailable)[^\n]{0,60}",
    "timeout": r"(?:TimeoutError|timed out|ReadTimeout|ConnectTimeout)[^\n]{0,80}",
    "install": r"(?:Could not find a version|No matching distribution|ERROR: Failed building|ModuleNotFoundError|"
               r"ImportError)[^\n]{0,100}",
    "patch": r"(?:Patch Apply Failed|error: patch failed|patch does not apply|Hunk #\d+ FAILED|"
             r"can't find file to patch)[^\n]{0,80}",
}


def tests(report: dict, kind: str, outcome: str) -> list[str]:
    return ((report.get("tests_status") or {}).get(kind) or {}).get(outcome) or []


def main() -> None:
    out = {}
    for d in sorted(p for p in BASE.iterdir() if p.is_dir()):
        task = d.name
        logs = d / "logs"
        rep = json.loads((logs / "report.json").read_text(encoding="utf-8"))
        rep = rep.get(task, rep) if task in rep else rep
        text = (logs / "test_output.txt").read_text(encoding="utf-8", errors="replace") if \
            (logs / "test_output.txt").exists() else ""
        if text.count("\\n") > text.count("\n"):  # stored with escaped newlines
            text = text.replace("\\n", "\n")
        job = json.loads((logs / "job-output.json").read_text(encoding="utf-8")) if \
            (logs / "job-output.json").exists() else {}
        env = {k: sorted(set(m.group(0) for m in re.finditer(p, text)))[:6] for k, p in ENV_PATTERNS.items()}
        patch = (logs / "patch.diff").read_text(encoding="utf-8") if (logs / "patch.diff").exists() else None
        sub = (d / "submission.diff").read_text(encoding="utf-8") if (d / "submission.diff").exists() else None
        out[task] = {
            "resolved": rep.get("resolved"), "patch_successfully_applied": rep.get("patch_successfully_applied"),
            "f2p_success": tests(rep, "FAIL_TO_PASS", "success"), "f2p_failure": tests(rep, "FAIL_TO_PASS", "failure"),
            "p2p_failure": tests(rep, "PASS_TO_PASS", "failure"), "p2p_success_n": len(tests(rep, "PASS_TO_PASS", "success")),
            "job_timed_out": job.get("timed_out"), "job_exit_code": job.get("exit_code"),
            "job_diff_changed": job.get("diff_changed"), "env_matches": env,
            "patch_diff_equals_submission": (patch is not None and sub is not None and patch.strip() == sub.strip()),
        }
        o = out[task]
        print(f"{task}: resolved={o['resolved']} applied={o['patch_successfully_applied']} "
              f"F2P ok/fail={len(o['f2p_success'])}/{len(o['f2p_failure'])} P2P fail={len(o['p2p_failure'])} "
              f"timed_out={o['job_timed_out']} env={ {k: [s[:90] for s in v[:3]] for k, v in env.items() if v} } "
              f"patch==submission={o['patch_diff_equals_submission']}")
    (BASE / "leaderboard_eval.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
