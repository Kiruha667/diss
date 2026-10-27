"""Corpus glm-4.6 run vs our harness and our pilot (own_data/DIAG_RESOLVE_RATE.md, steps 3-4). No LLM calls.
  - per task: resolved in the leaderboard report vs resolved of the SAME corpus patch in our evaluation
    (swebench 4.1.0, run_id diag_corpus_glm46) and the tests whose outcome differs;
  - per task: files / functions touched and diff size of the corpus patch and of the 3 pilot patches.
Writes own_data/diag/compare.json. Run from the repository root.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "own_data" / "diag" / "corpus_glm46"
OWN = ROOT / "own_data"


def outcomes(report: dict) -> dict[str, str]:
    out = {}
    for kind in ("FAIL_TO_PASS", "PASS_TO_PASS"):
        for res in ("success", "failure"):
            for t in ((report.get("tests_status") or {}).get(kind) or {}).get(res) or []:
                out[t] = f"{kind}:{res}"
    return out


def load_report(p: Path, task: str) -> dict:
    r = json.loads(p.read_text(encoding="utf-8"))
    return r.get(task, r)


def patch_summary(diff: str) -> dict:
    files, funcs, plus, minus = {}, set(), 0, 0
    cur = None
    for line in diff.splitlines():
        if line.startswith("diff --git"):
            cur = line.split(" b/", 1)[-1]
            files[cur] = [0, 0]
        elif line.startswith("@@"):
            m = re.search(r"@@[^@]*@@\s*(.*)", line)
            if m and m.group(1).strip():
                funcs.add(f"{cur}: {m.group(1).strip()[:70]}")
        elif line.startswith("+") and not line.startswith("+++") and cur:
            files[cur][0] += 1
            plus += 1
        elif line.startswith("-") and not line.startswith("---") and cur:
            files[cur][1] += 1
            minus += 1
    return {"files": {f: f"+{a}/-{b}" for f, (a, b) in files.items()}, "functions": sorted(funcs),
            "added": plus, "removed": minus}


def main() -> None:
    res = {}
    for d in sorted(p for p in BASE.iterdir() if p.is_dir()):
        task = d.name
        lb = load_report(d / "logs" / "report.json", task)
        ours = load_report(d / "our_eval" / "report.json", task)
        o_lb, o_us = outcomes(lb), outcomes(ours)
        diff_tests = {t: [o_lb.get(t), o_us.get(t)] for t in sorted(set(o_lb) | set(o_us)) if o_lb.get(t) != o_us.get(t)}
        pilot = []
        for k in range(3):
            rid = f"own-v1.1_mini-v1.17.1_glm-4.6_pilot_r{k}_{task}"
            preds = OWN / "raw" / rid / "preds.json"
            rep = OWN / "eval" / rid / task / "report.json"
            if preds.exists():
                patch = json.loads(preds.read_text(encoding="utf-8"))[task]["model_patch"] or ""
                r = load_report(rep, task) if rep.exists() else {}
                pilot.append({"repeat": k, "resolved": r.get("resolved"), **patch_summary(patch)})
        res[task] = {"corpus_resolved_leaderboard": lb.get("resolved"), "corpus_resolved_ours": ours.get("resolved"),
                     "tests_differing": diff_tests,
                     "corpus_patch": patch_summary((d / "submission.diff").read_text(encoding="utf-8")),
                     "pilot_patches": pilot}
        print(task, "| leaderboard:", lb.get("resolved"), "| ours:", ours.get("resolved"), "| differing tests:",
              len(diff_tests))
    (ROOT / "own_data" / "diag" / "compare.json").write_text(json.dumps(res, indent=1, ensure_ascii=False),
                                                              encoding="utf-8")


if __name__ == "__main__":
    main()
