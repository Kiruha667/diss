#!/usr/bin/env bash
# Pull the SWE-bench evaluation images of the main-collection tasks (own_data/tasks_main.json), one by one, before
# any run (PROTOCOL.md: images are never pulled during agent runs). Images already present are skipped. Every line
# (instance, digest, seconds) is appended to own_data/images_main.log.
set -uo pipefail
REPO=/mnt/c/Users/1/Desktop/diss
LOG="$REPO/own_data/images_main.log"
bash "$REPO/own_data/infra/dockerd_up.sh" >/dev/null
ids=$(/opt/own/venv-eval/bin/python -c "import json,sys; print(' '.join(t['task_id'] for t in json.load(open(sys.argv[1]))['tasks']))" "$REPO/own_data/tasks_main.json")
for id in $ids; do
    img="docker.io/swebench/sweb.eval.x86_64.$(echo "${id//__/_1776_}" | tr '[:upper:]' '[:lower:]'):latest"
    if docker image inspect "$img" >/dev/null 2>&1; then
        echo "$(date -u +%FT%TZ) $id present $(docker image inspect --format '{{index .RepoDigests 0}}' "$img")" >> "$LOG"
        continue
    fi
    echo "$(date -u +%FT%TZ) $(bash "$REPO/own_data/infra/pull_one.sh" "$id")" >> "$LOG"
done
echo "$(date -u +%FT%TZ) done; images: $(docker images -q | wc -l); $(df -h /var/lib/docker | tail -1 | awk '{print "free " $4}')" >> "$LOG"
