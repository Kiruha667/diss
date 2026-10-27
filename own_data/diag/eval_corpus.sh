#!/usr/bin/env bash
# Evaluate the 5 corpus glm-4.6 patches (own_data/diag/corpus_glm46/<task>/submission.diff) with OUR harness:
# swebench 4.1.0, the same prebuilt images as the pilot, run_id diag_corpus_glm46. No LLM calls.
# Reports go to own_data/diag/corpus_glm46/<task>/our_eval/.
set -euo pipefail
REPO=/mnt/c/Users/1/Desktop/diss
BASE="$REPO/own_data/diag/corpus_glm46"
RUN_ID=diag_corpus_glm46
EV="/opt/own/eval/$RUN_ID"
bash "$REPO/own_data/infra/dockerd_up.sh"
mkdir -p "$EV"; cd "$EV"
/opt/own/venv-eval/bin/python - "$BASE" <<'EOF'
import json, pathlib, sys
base = pathlib.Path(sys.argv[1])
preds = [{"instance_id": d.name, "model_name_or_path": "glm-4.6-corpus",
          "model_patch": (d / "submission.diff").read_text()} for d in sorted(base.iterdir()) if (d / "submission.diff").exists()]
json.dump(preds, open("preds_list.json", "w"), indent=1)
print(len(preds), "predictions")
EOF
/opt/own/venv-eval/bin/python -m swebench.harness.run_evaluation --dataset_name princeton-nlp/SWE-bench_Verified \
    --split test --predictions_path preds_list.json --max_workers 1 --run_id "$RUN_ID" --namespace swebench \
    --instance_image_tag latest --cache_level instance --clean False --timeout 1800 > eval_console.log 2>&1 || true
for d in logs/run_evaluation/"$RUN_ID"/*/*/; do
    iid=$(basename "$d"); mkdir -p "$BASE/$iid/our_eval"
    for f in report.json test_output.txt run_instance.log patch.diff; do [ -f "$d/$f" ] && cp "$d/$f" "$BASE/$iid/our_eval/"; done
done
cp eval_console.log "$BASE/our_eval_console.log"
grep -h '"resolved"' "$BASE"/*/our_eval/report.json | sed 's/^ *//'
