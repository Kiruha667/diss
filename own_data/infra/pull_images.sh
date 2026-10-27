#!/usr/bin/env bash
# Pull the SWE-bench evaluation images of the pilot tasks (own_data/tasks_pilot.json), smoke-test task first,
# and print each image's digest (PROTOCOL.md 1.0: the digest is logged for every run).
set -uo pipefail
bash /mnt/c/Users/1/Desktop/diss/own_data/infra/dockerd_up.sh
for id in psf__requests-1766 pytest-dev__pytest-7324 pylint-dev__pylint-6386 sympy__sympy-23950 django__django-14376; do
    img="docker.io/swebench/sweb.eval.x86_64.$(echo "${id//__/_1776_}" | tr '[:upper:]' '[:lower:]'):latest"
    start=$(date +%s)
    if docker pull -q "$img" >/dev/null; then
        echo "$id $(docker image inspect --format '{{index .RepoDigests 0}}' "$img") $(( $(date +%s) - start ))s"
    else
        echo "$id PULL FAILED"
    fi
done
