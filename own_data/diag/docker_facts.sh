#!/usr/bin/env bash
# (1) Which tests of psf__requests-1766 use the network (httpbin) — parsed from /testbed/test_requests.py inside the
#     task image; whether a container started like the harness starts it (default network) reaches httpbin.org now.
# (2) Digest and creation date of the 5 pilot task images.
# Output: own_data/diag/docker_facts.json. No LLM calls.
set -euo pipefail
REPO=/mnt/c/Users/1/Desktop/diss
bash "$REPO/own_data/infra/dockerd_up.sh" >/dev/null
IMG=docker.io/swebench/sweb.eval.x86_64.psf_1776_requests-1766:latest
NET=$(docker run --rm "$IMG" bash -lc 'source /opt/miniconda3/bin/activate testbed >/dev/null 2>&1; python - <<"EOF"
import ast, json, urllib.request
src = open("/testbed/test_requests.py").read()
tree = ast.parse(src)
uses = []
for cls in [n for n in tree.body if isinstance(n, ast.ClassDef)]:
    for f in cls.body:
        if isinstance(f, ast.FunctionDef) and f.name.startswith("test"):
            seg = ast.get_source_segment(src, f) or ""
            if "httpbin(" in seg or "HTTPBIN" in seg:
                uses.append(f"test_requests.py::{cls.name}::{f.name}")
default = [l for l in src.splitlines() if "HTTPBIN" in l and "=" in l][:2]
try:
    status = urllib.request.urlopen("http://httpbin.org/get", timeout=15).status
except Exception as e:
    status = f"{type(e).__name__}: {e}"
print(json.dumps({"tests_using_httpbin": uses, "httpbin_default": default, "httpbin_probe_now": str(status)}))
EOF')
IMGS=$(for id in pytest-dev__pytest-7324 psf__requests-1766 pylint-dev__pylint-6386 sympy__sympy-23950 django__django-14376; do
    img="docker.io/swebench/sweb.eval.x86_64.$(echo "${id//__/_1776_}" | tr '[:upper:]' '[:lower:]'):latest"
    docker image inspect --format "{\"task\": \"$id\", \"digest\": \"{{index .RepoDigests 0}}\", \"created\": \"{{.Created}}\"}" "$img"
done | paste -sd, -)
echo "{\"requests_network\": $NET, \"images\": [$IMGS], \"checked_utc\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}" > "$REPO/own_data/diag/docker_facts.json"
cat "$REPO/own_data/diag/docker_facts.json" | head -c 1500
