#!/usr/bin/env bash
# Time container starts from a local SWE-bench image the way mini-swe-agent's DockerEnvironment starts it
# (docker run -d ... sleep), twice (cold, then warm page cache), plus one command inside (git status in /testbed).
set -uo pipefail
bash /mnt/c/Users/1/Desktop/diss/own_data/infra/dockerd_up.sh
img="${1:-docker.io/swebench/sweb.eval.x86_64.psf_1776_requests-1766:latest}"
for attempt in cold warm; do
    t0=$(date +%s%N)
    cid=$(timeout 300 docker run -d -w /testbed --rm "$img" sleep 120)
    t1=$(date +%s%N)
    timeout 300 docker exec "$cid" bash -lc 'cd /testbed && git status --short | wc -l' >/dev/null
    t2=$(date +%s%N)
    docker rm -f "$cid" >/dev/null 2>&1
    echo "$attempt: docker run $(( (t1 - t0) / 1000000 )) ms, git status $(( (t2 - t1) / 1000000 )) ms"
done
free -m | awk 'NR==2 {print "memory used/total MB: " $3 "/" $2}'
