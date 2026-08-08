#!/usr/bin/env bash
# bootstrap-depth.sh — one-shot install of the depth-diff recorder on a fresh Debian 12 VM.
# Idempotent. Run as root (startup-script runs as root). No tokens, no secrets, no service
# account: the recorder is keyless and the repo is public.
set -euo pipefail

id -u btcq >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin btcq

export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -qy git python3-venv

if [ ! -d /opt/btc-quant/.git ]; then
  git clone --depth 1 https://github.com/azulcoder/btc-quant /opt/btc-quant
else
  git -C /opt/btc-quant pull --ff-only
fi

[ -d /opt/btcq-venv ] || python3 -m venv /opt/btcq-venv
/opt/btcq-venv/bin/pip install -q --upgrade pip
/opt/btcq-venv/bin/pip install -q "websockets>=12"

mkdir -p /opt/btc-quant/data/depth_diffs
chown -R btcq:btcq /opt/btc-quant

cp /opt/btc-quant/deploy/gcp/depth-recorder.service \
   /etc/systemd/system/btcquant-depth-recorder.service
systemctl daemon-reload
systemctl enable --now btcquant-depth-recorder
systemctl --no-pager status btcquant-depth-recorder || true
