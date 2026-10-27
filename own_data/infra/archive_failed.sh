#!/usr/bin/env bash
# Move what an interrupted run left in own-ubuntu to own_data/raw_failed/<run_id>-attempt<N>/ (kept as evidence of a
# technical failure), so the run can be repeated under the same run_id.
#   usage: archive_failed.sh <run_id> <attempt_no>
set -euo pipefail
RUN_ID="$1"; N="$2"
DEST="/mnt/c/Users/1/Desktop/diss/own_data/raw_failed/${RUN_ID}-attempt${N}"
[ -e "$DEST" ] && { echo "exists: $DEST"; exit 2; }
mkdir -p "$DEST"
[ -d "/opt/own/runs/$RUN_ID" ] && cp -r "/opt/own/runs/$RUN_ID"/. "$DEST"/ && rm -rf "/opt/own/runs/$RUN_ID"
[ -f "/opt/own/runs/$RUN_ID.console.log" ] && mv "/opt/own/runs/$RUN_ID.console.log" "$DEST/console.log"
docker ps -aq --filter name=minisweagent- | xargs -r docker rm -f >/dev/null
ls -la "$DEST"
