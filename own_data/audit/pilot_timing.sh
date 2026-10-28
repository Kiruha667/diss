#!/usr/bin/env bash
# Pilot timing for the main-collection forecast (PROTOCOL.md 1.2): per pilot run, the agent wall time (run_meta.json)
# and the evaluation time = mtime(eval_console.log) - mtime(preds_list.json) in /opt/own/eval/<run_id>/ (preds_list.json
# is written right before the harness starts, eval_console.log is closed when it ends). Output: TSV on stdout.
REPO=/mnt/c/Users/1/Desktop/diss
echo -e "run_id\tagent_wall_s\teval_s"
for d in "$REPO"/own_data/raw/own-v1.1_*pilot*; do
    id=$(basename "$d")
    wall=$(/opt/own/venv-eval/bin/python -c "import json,sys; print(json.load(open(sys.argv[1])).get('wall_time_s'))" "$d/run_meta.json")
    ev=/opt/own/eval/$id
    if [ -f "$ev/preds_list.json" ] && [ -f "$ev/eval_console.log" ]; then
        e=$(( $(stat -c %Y "$ev/eval_console.log") - $(stat -c %Y "$ev/preds_list.json") ))
    else
        e=NA
    fi
    echo -e "$id\t$wall\t$e"
done
