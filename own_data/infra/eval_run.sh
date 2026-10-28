#!/usr/bin/env bash
# Evaluate the patches of one own run with the SWE-bench harness (PROTOCOL.md 1.0: label = resolved).
#   usage: eval_run.sh <run_id>
# Uses the prebuilt image swebench/sweb.eval.x86_64.<instance>:latest (the one the agent ran in) and keeps it
# (--cache_level instance) for the next runs. Reports are copied to own_data/eval/<run_id>/<instance>/.
# PROTOCOL.md 1.2: before the evaluation, httpbin probe (infra/httpbin_probe.sh) for every instance of the run; if it
# is not 200, wait 10 min and probe again (up to 6 probes); if it still fails, exit 5 = evaluation postponed (nothing
# evaluated, to be repeated later). Every probe goes to own_data/eval/<run_id>/httpbin_probe.log.
# OWN_SKIP_HTTPBIN_PROBE=1 disables the probe (protocol <= 1.1 behaviour).
set -euo pipefail
RUN_ID="$1"
REPO=/mnt/c/Users/1/Desktop/diss
RAW="$REPO/own_data/raw/$RUN_ID"
DEST="$REPO/own_data/eval/$RUN_ID"
EV="/opt/own/eval/$RUN_ID"
[ -f "$RAW/preds.json" ] || { echo "no $RAW/preds.json"; exit 2; }
bash "$REPO/own_data/infra/dockerd_up.sh"
if [ "${OWN_SKIP_HTTPBIN_PROBE:-0}" != "1" ]; then
    mkdir -p "$DEST"
    for iid in $(/opt/own/venv-eval/bin/python -c "import json,sys; print(' '.join(json.load(open(sys.argv[1]))))" "$RAW/preds.json"); do
        ok=0
        for attempt in 1 2 3 4 5 6; do
            if line=$(bash "$REPO/own_data/infra/httpbin_probe.sh" "$iid"); then ok=1; fi
            echo "$line $iid attempt=$attempt" >> "$DEST/httpbin_probe.log"
            [ $ok -eq 1 ] && break
            [ $attempt -lt 6 ] && sleep 600
        done
        [ $ok -eq 1 ] || { echo "httpbin probe failed 6 times for $iid: evaluation postponed"; exit 5; }
    done
fi
mkdir -p "$EV"; cd "$EV"
# mini-swe-agent writes predictions as {instance_id: prediction}; the harness gets a plain list
/opt/own/venv-eval/bin/python -c "import json,sys; d=json.load(open(sys.argv[1])); json.dump(list(d.values()), open('preds_list.json','w'), indent=1)" "$RAW/preds.json"
/opt/own/venv-eval/bin/python -m swebench.harness.run_evaluation --dataset_name princeton-nlp/SWE-bench_Verified \
    --split test --predictions_path preds_list.json --max_workers 1 --run_id "$RUN_ID" --namespace swebench \
    --instance_image_tag latest --cache_level instance --clean False --timeout 1800 > eval_console.log 2>&1 || true
mkdir -p "$DEST"
cp eval_console.log "$DEST/"
cp ./*."$RUN_ID".json "$DEST/" 2>/dev/null || true
for d in logs/run_evaluation/"$RUN_ID"/*/*/; do
    iid=$(basename "$d")
    mkdir -p "$DEST/$iid"
    for f in report.json test_output.txt run_instance.log patch.diff; do
        [ -f "$d/$f" ] && cp "$d/$f" "$DEST/$iid/"
    done
done
/opt/own/venv-eval/bin/python - "$DEST" <<'EOF'
import importlib.metadata as m, json, pathlib, sys
dest = pathlib.Path(sys.argv[1])
res = {}
for rep in dest.glob("*/report.json"):
    d = json.loads(rep.read_text())
    res[rep.parent.name] = {k: v.get("resolved") for k, v in d.items()}
(dest / "eval_meta.json").write_text(json.dumps({"swebench": m.version("swebench"), "resolved": res}, indent=1))
print(json.dumps({"swebench": m.version("swebench"), "resolved": res}))
EOF
