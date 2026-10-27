#!/usr/bin/env bash
# Start dockerd inside own-ubuntu if it is not running (the distro has no systemd) and wait until it answers.
set -euo pipefail
if ! docker info >/dev/null 2>&1; then
    nohup dockerd >/var/log/dockerd.log 2>&1 &
    for _ in $(seq 1 60); do
        docker info >/dev/null 2>&1 && break
        sleep 1
    done
fi
docker info --format 'dockerd {{.ServerVersion}}, storage {{.Driver}}, cgroup {{.CgroupVersion}}'
