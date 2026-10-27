#!/usr/bin/env bash
# Quick status of own-ubuntu: docker images, bytes received, disk use.
docker images --format '{{.Repository}}:{{.Tag}} {{.Size}}' 2>/dev/null
awk '/eth0/ {printf "network received: %.2f GB\n", $2/1e9}' /proc/net/dev
df -h / | tail -1 | awk '{print "distro disk used: " $3 " of " $2}'
