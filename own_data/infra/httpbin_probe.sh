#!/usr/bin/env bash
# Network check before an evaluation (PROTOCOL.md 1.2): GET http://httpbin.org/get from a container started like the
# harness starts evaluation containers (default Docker network), using the image of the task being evaluated.
#   usage: httpbin_probe.sh <instance_id>     prints "<UTC time> <status>"; exit 0 only if the status is 200
set -uo pipefail
bash /mnt/c/Users/1/Desktop/diss/own_data/infra/dockerd_up.sh >/dev/null
id="$1"
img="docker.io/swebench/sweb.eval.x86_64.$(echo "${id//__/_1776_}" | tr '[:upper:]' '[:lower:]'):latest"
# conda base python 3 of the image: the testbed env of old tasks is older than 3.6
status=$(docker run --rm "$img" bash -lc '/opt/miniconda3/bin/python - <<"EOF"
import urllib.request
try:
    print(urllib.request.urlopen("http://httpbin.org/get", timeout=15).status)
except Exception as e:
    print(f"{type(e).__name__}: {str(e)[:120]}".replace(" ", "_"))
EOF' 2>/dev/null | tail -1)
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) ${status:-no_output}"
[ "$status" = "200" ]
