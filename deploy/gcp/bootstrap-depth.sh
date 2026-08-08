#!/usr/bin/env bash
# bootstrap-depth.sh — one-shot install of the depth-diff recorder on a fresh Debian 12 VM.
# Idempotent. Run as root (startup-script runs as root). No tokens, no secrets, no service
# account: the recorder is keyless and the repo is public.
set -euo pipefail

id -u btcq >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin btcq

export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -qy git python3-venv

# Re-runs happen as root over a checkout chowned to btcq, and git refuses that as
# "dubious ownership" (exit 128) — measured on the first VM reset, where the pull
# failed and the service silently kept running the OLD code. Declare the directory
# safe for root before touching it.
# --system, not --global: the metadata script runner has no HOME, so a --global write
# lands nowhere and its error is easy to swallow. /etc/gitconfig is HOME-independent.
export HOME="${HOME:-/root}"
git config --system --add safe.directory /opt/btc-quant ||   git config --global --add safe.directory /opt/btc-quant
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
systemctl enable btcquant-depth-recorder
# enable --now does NOT restart an already-running service, so a re-run would leave
# old code live. Restart is idempotent and costs one bounded, recorded gap.
systemctl restart btcquant-depth-recorder
systemctl --no-pager status btcquant-depth-recorder || true
