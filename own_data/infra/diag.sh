#!/usr/bin/env bash
# Diagnostics of own-ubuntu: processes, DNS, docker responsiveness (each step bounded by a timeout).
echo "== resolv.conf"; cat /etc/resolv.conf | grep -v '^#'
echo "== processes"; ps -eo pid,etimes,stat,cmd --sort=-etimes | grep -E 'apt|dpkg|docker|dockerd|containerd|python|sleep' | grep -v grep | cut -c1-150 | head -15
echo "== dns"; timeout 10 getent hosts registry-1.docker.io || echo "DNS lookup failed/timed out"
timeout 10 getent hosts api.z.ai || echo "DNS api.z.ai failed/timed out"
echo "== docker"; timeout 20 docker ps -a --format '{{.Names}} {{.Status}}' || echo "docker ps timed out"
timeout 20 docker info --format 'containers={{.Containers}} running={{.ContainersRunning}}' || echo "docker info timed out"
echo "== load"; cat /proc/loadavg; free -m | head -2
