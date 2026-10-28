#!/usr/bin/env bash
# Stage V (PROTOCOL.md 11.7): evaluate the SWE-bench Verified reference (gold) patches of the tasks of
# own_data/tasks_main.json with our evaluation (swebench 4.1.0, same flags and images as eval_run.sh, no LLM calls).
#   usage: gold_eval.sh <run_id>        (gold_pre before the main collection, gold_post after it)
# Resumable: the harness skips instances that already have a report under this run_id. Reports, test output and
# the instance log go to own_data/eval/<run_id>/<instance>/.
set -euo pipefail
RUN_ID="$1"
REPO=/mnt/c/Users/1/Desktop/diss
DEST="$REPO/own_data/eval/$RUN_ID"
EV="/opt/own/eval/$RUN_ID"
bash "$REPO/own_data/infra/dockerd_up.sh"
mkdir -p "$EV" "$DEST"; cd "$EV"
ids=$(/opt/own/venv-eval/bin/python -c "import json,sys; print(' '.join(t['task_id'] for t in json.load(open(sys.argv[1]))['tasks']))" "$REPO/own_data/tasks_main.json")
date -u +%FT%TZ > "$DEST/started_utc.txt"
/opt/own/venv-eval/bin/python -m swebench.harness.run_evaluation --dataset_name princeton-nlp/SWE-bench_Verified \
    --split test --predictions_path gold --instance_ids $ids --max_workers 1 --run_id "$RUN_ID" --namespace swebench \
    --instance_image_tag latest --cache_level instance --clean False --timeout 1800 >> eval_console.log 2>&1 || true
date -u +%FT%TZ > "$DEST/finished_utc.txt"
cp eval_console.log "$DEST/"
cp ./*."$RUN_ID".json "$DEST/" 2>/dev/null || true
for d in logs/run_evaluation/"$RUN_ID"/gold/*/; do
    iid=$(basename "$d")
    mkdir -p "$DEST/$iid"
    for f in report.json test_output.txt run_instance.log; do
        [ -f "$d/$f" ] && cp "$d/$f" "$DEST/$iid/"
    done
done
/opt/own/venv-eval/bin/python - "$DEST" <<'EOF'
import importlib.metadata as m, json, pathlib, sys
dest = pathlib.Path(sys.argv[1])
res = {rep.parent.name: json.loads(rep.read_text())[rep.parent.name]["resolved"] for rep in dest.glob("*/report.json")}
meta = {"swebench": m.version("swebench"), "n_reports": len(res), "n_resolved": sum(res.values()),
        "not_resolved": sorted(k for k, v in res.items() if not v)}
(dest / "eval_meta.json").write_text(json.dumps({**meta, "resolved": res}, indent=1))
print(json.dumps(meta))
EOF
