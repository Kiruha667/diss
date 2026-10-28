#!/usr/bin/env bash
# Every 5 minutes drop the page cache of the own-ubuntu VM so that the memory goes back to Windows (the inbox WSL has
# no autoMemoryReclaim; with Docker images read by the runs, vmmem otherwise stays at the .wslconfig limit and the
# laptop runs out of RAM). Only the clean page cache is dropped (echo 1), nothing of the runs is touched.
# Run as root, detached:  wsl.exe -d own-ubuntu -u root -- bash /mnt/c/Users/1/Desktop/diss/own_data/infra/wsl_cache_trim.sh
while true; do
    sleep 300
    sync
    echo 1 > /proc/sys/vm/drop_caches
done
