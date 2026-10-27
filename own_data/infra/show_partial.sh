#!/usr/bin/env bash
# Show what an interrupted run left inside own-ubuntu (/opt/own/runs/<run_id>[.console.log]).
#   usage: show_partial.sh <run_id>
RUN_ID="$1"
ls -la "/opt/own/runs/$RUN_ID" "/opt/own/runs/$RUN_ID.console.log" 2>&1 | head -20
echo "== minisweagent.log (tail)"; tail -5 "/opt/own/runs/$RUN_ID/minisweagent.log" 2>/dev/null
echo "== console (last progress lines)"
tr '\r' '\n' < "/opt/own/runs/$RUN_ID.console.log" 2>/dev/null | grep -aoE 'Step +[0-9]+ \(\$[0-9.]+\)' | tail -3
docker ps -a --format '{{.Names}} {{.Status}}' 2>/dev/null | head
