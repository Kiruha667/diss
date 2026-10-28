#!/usr/bin/env bash
# Pull the evaluation images of the 8 psf/requests tasks one by one (never during agent runs: 5400 rpm HDD).
for t in psf__requests-1142 psf__requests-1724 psf__requests-1766 psf__requests-1921 psf__requests-2317 \
         psf__requests-2931 psf__requests-5414 psf__requests-6028; do
    bash /mnt/c/Users/1/Desktop/diss/own_data/infra/pull_one.sh "$t"
done
