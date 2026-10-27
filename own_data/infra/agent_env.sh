#!/usr/bin/env bash
# Python environments inside own-ubuntu (PROTOCOL.md 1.0):
#   /opt/own/venv-agent - mini-swe-agent 1.17.1, dependencies resolved as of its release (2025-12-03);
#   /opt/own/venv-eval  - SWE-bench evaluation harness (swebench package).
# Frozen package lists are written next to the venvs and copied to own_data/infra/.
set -euo pipefail
OWN=/opt/own
REPO=/mnt/c/Users/1/Desktop/diss
mkdir -p "$OWN"
cd "$OWN"
[ -x venv-agent/bin/python ] || uv venv -q --python 3.11 venv-agent
# datasets is needed by mini-extra swebench (task list from Hugging Face) but is not a core dependency
uv pip install -q --python venv-agent/bin/python --exclude-newer 2025-12-03 mini-swe-agent==1.17.1 datasets
# evaluation harness as of the same date (the corpus labels come from the harness of that time; swebench 5.x changed
# how images are obtained)
[ -x venv-eval/bin/python ] || uv venv -q --python 3.11 venv-eval
uv pip install -q --python venv-eval/bin/python --exclude-newer 2025-12-03 swebench
uv pip freeze --python venv-agent/bin/python > "$REPO/own_data/infra/agent_freeze.txt"
uv pip freeze --python venv-eval/bin/python > "$REPO/own_data/infra/eval_freeze.txt"
venv-agent/bin/python - <<'EOF'
import importlib.metadata as m
print("agent:", {p: m.version(p) for p in ("mini-swe-agent", "litellm", "openai", "datasets")})
EOF
venv-eval/bin/python - <<'EOF'
import importlib.metadata as m
print("eval:", {p: m.version(p) for p in ("swebench", "docker")})
EOF
