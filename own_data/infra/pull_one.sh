#!/usr/bin/env bash
# Pull one SWE-bench evaluation image with retries (DNS in WSL is sometimes flaky) and print its digest.
#   usage: pull_one.sh <instance_id>
set -uo pipefail
bash /mnt/c/Users/1/Desktop/diss/own_data/infra/dockerd_up.sh >/dev/null
id="$1"
img="docker.io/swebench/sweb.eval.x86_64.$(echo "${id//__/_1776_}" | tr '[:upper:]' '[:lower:]'):latest"
for attempt in 1 2 3; do
    start=$(date +%s)
    if docker pull -q "$img" >/dev/null; then
        echo "$id $(docker image inspect --format '{{index .RepoDigests 0}}' "$img") $(( $(date +%s) - start ))s (attempt $attempt)"
        exit 0
    fi
    sleep 15
done
echo "$id PULL FAILED after 3 attempts"; exit 1
