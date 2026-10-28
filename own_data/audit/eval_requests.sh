#!/usr/bin/env bash
# Label audit, step 3: re-evaluate the leaderboard patches (logs/<task>/patch.diff of the leaderboard evaluation) of
# the runs listed in own_data/audit/reeval_list.json (resolved = 0 with network failures) with OUR harness:
# swebench 4.1.0, the same flags and images as the pilot, harness run_id label_audit, model_name_or_path = corpus run.
# Before EACH evaluation: httpbin probe (own_data/infra/httpbin_probe.sh); if it is not 200 the evaluation is
# postponed (10 min wait, up to 6 probes), then skipped and logged. Resumable: evaluated pairs are skipped.
# Reports -> own_data/audit/our_eval/<run>/<task>/report.json; test_output.txt -> data/raw/label_audit/our_eval/.
# No LLM calls.
set -uo pipefail
REPO=/mnt/c/Users/1/Desktop/diss
A="$REPO/own_data/audit"
RAWLB="$REPO/data/raw/label_audit/lb_requests"
PY=/opt/own/venv-eval/bin/python
EV=/opt/own/eval/label_audit
PROBES="$A/httpbin_probes.log"
bash "$REPO/own_data/infra/dockerd_up.sh" >/dev/null
mkdir -p "$EV"; cd "$EV"
PAIRS=$($PY -c "import json; d=json.load(open('$A/reeval_list.json')); print(' '.join(f'{r}:{t}' for r in d for t in d[r]))")
for pair in $PAIRS; do
    run="${pair%%:*}"; task="${pair##*:}"
    out="$A/our_eval/$run/$task"
    [ -f "$out/report.json" ] && { echo "$run $task: done earlier"; continue; }
    ok=0
    for attempt in 1 2 3 4 5 6; do
        line=$(bash "$REPO/own_data/infra/httpbin_probe.sh" "$task"); rc=$?
        echo "$line $run $task attempt=$attempt" >> "$PROBES"
        [ $rc -eq 0 ] && { ok=1; break; }
        echo "$run $task: httpbin probe not 200 ($line), evaluation postponed"; sleep 600
    done
    [ $ok -eq 1 ] || { echo "$run $task: SKIPPED, httpbin unavailable"; continue; }
    $PY - "$RAWLB/$run/$task/patch.diff" "$run" "$task" <<'EOF'
import json, sys
patch, run, task = sys.argv[1:4]
json.dump([{"instance_id": task, "model_name_or_path": run, "model_patch": open(patch).read()}],
          open("preds_one.json", "w"), indent=1)
EOF
    start=$(date +%s)
    $PY -m swebench.harness.run_evaluation --dataset_name princeton-nlp/SWE-bench_Verified \
        --split test --predictions_path preds_one.json --max_workers 1 --run_id label_audit --namespace swebench \
        --instance_image_tag latest --cache_level instance --clean False --timeout 1800 >> eval_console.log 2>&1
    d="logs/run_evaluation/label_audit/$run/$task"
    if [ -f "$d/report.json" ]; then
        mkdir -p "$out" "$REPO/data/raw/label_audit/our_eval/$run/$task"
        cp "$d/report.json" "$out/"
        for f in test_output.txt run_instance.log patch.diff; do
            [ -f "$d/$f" ] && cp "$d/$f" "$REPO/data/raw/label_audit/our_eval/$run/$task/"
        done
        echo "{\"eval_seconds\": $(( $(date +%s) - start )), \"probe\": \"$line\"}" > "$out/eval_meta.json"
        echo "$run $task: resolved=$($PY -c "import json; print(json.load(open('$out/report.json'))['$task']['resolved'])") ($(( $(date +%s) - start ))s)"
    else
        echo "$run $task: NO REPORT (see $EV/eval_console.log)"
    fi
done
cp eval_console.log "$REPO/data/raw/label_audit/our_eval_console.log"
