"""Raw .traj.json (355 local) -> E0 adapter -> compare with the records of trajectories.jsonl.gz. Read-only."""
import gzip, json, sys
from pathlib import Path
ROOT = Path(".").resolve(); sys.path.insert(0, str(ROOT))
from src.adapters import swebench_bashonly as sb
from src.common import RAW, trajectories_path
cfg = sb.load_cfg()
local = {}
excl = 0
for rec, ex in sb.iter_records(cfg, RAW / "swebench_bashonly"):
    if ex is not None:
        excl += 1
    else:
        local[rec["run_id"]] = rec
same = diff = 0; bad = []
with gzip.open(trajectories_path("swebench_bashonly"), "rt", encoding="utf-8") as fh:
    for line in fh:
        rid = line[:200].split('"run_id": "')[1].split('"')[0] if '"run_id": "' in line[:200] else None
        if rid is None or rid not in local:
            if rid is None:
                r = json.loads(line); rid = r["run_id"]
                if rid not in local: continue
            else:
                continue
        r = json.loads(line)
        a = local[rid]
        ok = all(a[k] == r[k] for k in ("task_id", "agent_id", "model_id", "label", "task_text", "steps")) and a["meta"]["exit_status"] == r["meta"]["exit_status"]
        same += ok; diff += (not ok)
        if not ok: bad.append(rid)
out = {"local_raw_parsed": len(local), "local_raw_excluded": excl, "identical_to_corpus_record": same, "different": diff, "examples": bad[:5]}
print(out)
json.dump(out, open(ROOT / "review/results/03_verify_raw_to_corpus.json", "w"), indent=1)
