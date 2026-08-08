#!/bin/bash
# Two-stage boot, in metadata because raw.githubusercontent CDN caching served a stale
# bootstrap through FOUR resets on 2026-08-08 (exit 128 each time). Metadata has no CDN.
git config --global --add safe.directory /opt/btc-quant 2>/dev/null || true
if [ -d /opt/btc-quant/.git ]; then
  git -C /opt/btc-quant pull --ff-only || true
fi
if [ -x /opt/btc-quant/deploy/gcp/bootstrap-depth.sh ]; then
  bash /opt/btc-quant/deploy/gcp/bootstrap-depth.sh
else
  curl -fsSL https://raw.githubusercontent.com/azulcoder/btc-quant/main/deploy/gcp/bootstrap-depth.sh | bash
fi
