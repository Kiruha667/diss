#!/usr/bin/env bash
# One mini-swe-agent run (PROTOCOL.md 1.0) on SWE-bench Verified instances matching a regex.
#   usage: run_agent.sh <run_id> <instance_regex>
# The API key comes only from ZAI_API_KEY (environment) and is handed to litellm as OPENAI_API_KEY in this process:
# never via model_kwargs or MSWEA_MODEL_API_KEY (mini-swe-agent writes the model config into every trajectory) and
# never via mini-swe-agent's global .env. Every output file is scanned for the key before it is kept.
set -euo pipefail
RUN_ID="$1"; FILTER="$2"
: "${ZAI_API_KEY:?ZAI_API_KEY is not set}"
REPO=/mnt/c/Users/1/Desktop/diss
# OWN_CFG / OWN_DEST_ROOT override the config and the destination only for probes outside the corpus (own_data/probes)
CFG="${OWN_CFG:-$REPO/own_data/config/glm-4.6_v1.0.yaml}"
REG_SRC="$REPO/own_data/config/litellm_model_registry.json"
REG=/home/klieret/litellm_model_registry.json   # path written in the verbatim corpus config
OUT="/opt/own/runs/$RUN_ID"
DEST="${OWN_DEST_ROOT:-$REPO/own_data/raw}/$RUN_ID"
[ -e "$DEST" ] && { echo "refusing to overwrite $DEST (raw runs are immutable)"; exit 2; }

bash "$REPO/own_data/infra/dockerd_up.sh"
mkdir -p "$(dirname "$REG")" /opt/own/mswea_global /opt/own/runs
install -m 644 "$REG_SRC" "$REG"
cmp -s "$REG_SRC" "$REG"

unset MSWEA_MODEL_API_KEY MSWEA_COST_TRACKING LITELLM_MODEL_REGISTRY_PATH
export OPENAI_API_KEY="$ZAI_API_KEY"
export MSWEA_GLOBAL_CONFIG_DIR=/opt/own/mswea_global MSWEA_SILENT_STARTUP=1 HF_HUB_DISABLE_TELEMETRY=1
rm -f /opt/own/mswea_global/.env

start=$(date -u +%Y-%m-%dT%H:%M:%SZ); t0=$(date +%s)
set +e
/opt/own/venv-agent/bin/python -m minisweagent.run.extra.swebench --subset verified --split test \
    --filter "^(${FILTER})\$" -c "$CFG" -o "$OUT" -w 1 > "/opt/own/runs/$RUN_ID.console.log" 2>&1
rc=$?
set -e
wall=$(( $(date +%s) - t0 ))
if [ ! -d "$OUT" ]; then
    echo "runner produced no output (exit code $rc):"; tail -20 "/opt/own/runs/$RUN_ID.console.log"; exit 4
fi
mv "/opt/own/runs/$RUN_ID.console.log" "$OUT/console.log" 2>/dev/null || true

if grep -rqF -- "$ZAI_API_KEY" "$OUT"; then
    echo "API KEY FOUND IN OUTPUT - output deleted"; rm -rf "$OUT"; exit 3
fi

# run metadata: versions, hashes, image digests (no secrets)
/opt/own/venv-agent/bin/python - "$OUT" "$RUN_ID" "$start" "$wall" "$rc" "$CFG" "$REG" <<'EOF'
import hashlib, importlib.metadata as m, json, pathlib, subprocess, sys
out, run_id, start, wall, rc, cfg, reg = sys.argv[1:]
out = pathlib.Path(out)
digests = {}
for traj in out.glob("*/*.traj.json"):
    img = json.loads(traj.read_text())["info"].get("config", {}).get("environment", {}).get("image")
    if img:
        r = subprocess.run(["docker", "image", "inspect", "--format", "{{index .RepoDigests 0}}", img],
                           capture_output=True, text=True)
        digests[traj.parent.name] = r.stdout.strip()
meta = {"run_id": run_id, "protocol_version": "1.0", "started_utc": start, "wall_time_s": int(wall),
        "runner_exit_code": int(rc),
        "versions": {p: m.version(p) for p in ("mini-swe-agent", "litellm", "openai", "datasets")},
        "config_sha256": hashlib.sha256(pathlib.Path(cfg).read_bytes()).hexdigest(),
        "registry_sha256": hashlib.sha256(pathlib.Path(reg).read_bytes()).hexdigest(),
        "provider": "z.ai (OpenAI-compatible endpoint, litellm provider openai)", "image_digests": digests}
(out / "run_meta.json").write_text(json.dumps(meta, indent=1))
print(json.dumps(meta))
EOF
mkdir -p "$DEST"
cp -r "$OUT"/. "$DEST"/
echo "saved to $DEST (runner exit code $rc, ${wall}s)"
