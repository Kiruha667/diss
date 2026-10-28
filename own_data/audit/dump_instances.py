"""SWE-bench Verified rows of the psf/requests tasks, as the harness (swebench 4.1.0) loads them: test_patch,
FAIL_TO_PASS, PASS_TO_PASS. Writes own_data/audit/requests_instances.json. Run with /opt/own/venv-eval/bin/python
inside WSL. No LLM calls."""
import json
import sys
from pathlib import Path

from swebench.harness.utils import load_swebench_dataset

OUT = Path(__file__).resolve().parent / "requests_instances.json"
rows = load_swebench_dataset("princeton-nlp/SWE-bench_Verified", "test")
res = {}
for r in rows:
    if r["repo"] == "psf/requests":
        f2p, p2p = r["FAIL_TO_PASS"], r["PASS_TO_PASS"]
        res[r["instance_id"]] = {
            "base_commit": r["base_commit"], "version": r["version"], "test_patch": r["test_patch"],
            "FAIL_TO_PASS": json.loads(f2p) if isinstance(f2p, str) else f2p,
            "PASS_TO_PASS": json.loads(p2p) if isinstance(p2p, str) else p2p,
        }
OUT.write_text(json.dumps(res, indent=1), encoding="utf-8")
print(len(res), "instances:", ", ".join(f"{k} (F2P {len(v['FAIL_TO_PASS'])}, P2P {len(v['PASS_TO_PASS'])})"
                                         for k, v in sorted(res.items())), file=sys.stdout)
