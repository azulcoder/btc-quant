#!/bin/bash
# Boot script, in metadata (no CDN in the path). Root cause of five failed boots on
# 2026-08-08: `git config --global` under the script runner has no HOME, so the
# safe.directory never landed and every pull died 128 on dubious ownership — with the
# error swallowed by our own 2>/dev/null. Three HOME-independent guards now:
export HOME=/root
git config --system --add safe.directory /opt/btc-quant || true
export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0=/opt/btc-quant
if [ -d /opt/btc-quant/.git ]; then
  git -C /opt/btc-quant pull --ff-only || true
fi
if [ -x /opt/btc-quant/deploy/gcp/bootstrap-depth.sh ]; then
  bash /opt/btc-quant/deploy/gcp/bootstrap-depth.sh
else
  curl -fsSL https://raw.githubusercontent.com/azulcoder/btc-quant/main/deploy/gcp/bootstrap-depth.sh | bash
fi
# Measurement to the serial console at every boot: disk truth and a tape QC census.
# This is the ONLY no-SSH read path for current disk usage, so it prints unconditionally.
echo "=== TAPE CENSUS $(date -u +%FT%TZ) ==="
df -h / | tail -1
du -sh /opt/btc-quant/data/depth_diffs 2>/dev/null || true
du -sh /opt/btc-quant/data/depth_diffs/binancef/BTCUSDT/date=* 2>/dev/null || true
/opt/btcq-venv/bin/python3 /opt/btc-quant/deploy/gcp/tape_qc.py --upload 2>&1 || true
echo "=== TAPE CENSUS END ==="
