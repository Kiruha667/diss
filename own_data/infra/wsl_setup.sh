#!/usr/bin/env bash
# One-time setup of the own-ubuntu WSL2 distro (Ubuntu Base 22.04.5) for the own corpus (PROTOCOL.md 1.0):
# Docker Engine from the Ubuntu archive + uv. Run as root inside the distro:
#   wsl -d own-ubuntu -- bash /mnt/c/Users/1/Desktop/diss/own_data/infra/wsl_setup.sh
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends ca-certificates curl git iptables docker.io xz-utils procps \
    less patch tzdata
# the WSL2 kernel of wsl_update_x64.msi (5.10) works with the legacy iptables backend
update-alternatives --set iptables /usr/sbin/iptables-legacy
update-alternatives --set ip6tables /usr/sbin/ip6tables-legacy
if ! command -v uv >/dev/null; then
    curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
fi
docker --version
uv --version
