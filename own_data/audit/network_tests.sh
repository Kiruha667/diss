#!/usr/bin/env bash
# Network-dependent tests of the 8 psf/requests tasks: in a container of each task image the test patch of the
# SWE-bench Verified row is applied (as the harness does) and scan_network_tests.py parses the test files.
# Output: own_data/audit/requests_network_tests.json. No LLM calls.
set -euo pipefail
REPO=/mnt/c/Users/1/Desktop/diss
A="$REPO/own_data/audit"
bash "$REPO/own_data/infra/dockerd_up.sh" >/dev/null
PY=/opt/own/venv-eval/bin/python
TASKS=$($PY -c "import json; print(' '.join(sorted(json.load(open('$A/requests_instances.json')))))")
{
echo "{"
first=1
for t in $TASKS; do
    img="docker.io/swebench/sweb.eval.x86_64.$(echo "${t//__/_1776_}" | tr '[:upper:]' '[:lower:]'):latest"
    res=$(docker run --rm --network none -v "$A:/audit:ro" "$img" bash -lc "
        cd /testbed
        /opt/miniconda3/bin/python -c \"import json; open('/tmp/tp.diff','w').write(json.load(open('/audit/requests_instances.json'))['$t']['test_patch'])\"
        git apply /tmp/tp.diff 2>/dev/null
        /opt/miniconda3/bin/python -c 'import sys; print(sys.version.split()[0])' >&2
        /opt/miniconda3/bin/python /audit/scan_network_tests.py /audit/requests_instances.json $t")
    [ $first -eq 1 ] || echo ","
    first=0
    echo "\"$t\": $res"
done
echo "}"
} > "$A/requests_network_tests.json"
$PY - "$A/requests_network_tests.json" <<'EOF'
import json, sys
d = json.load(open(sys.argv[1]))
for t, r in d.items():
    tests = r["tests"].values()
    print(t, "| files:", {f: v["httpbin_default"] for f, v in r["files"].items()},
          "| not found:", sum(not x["found"] for x in tests),
          "| external httpbin F2P/P2P:", sum(x["external_httpbin"] for x in tests if x["kind"] == "FAIL_TO_PASS"), "/",
          sum(x["external_httpbin"] for x in tests if x["kind"] == "PASS_TO_PASS"),
          "| literal external URL:", sum(bool(x["external_literals"]) for x in tests))
EOF
