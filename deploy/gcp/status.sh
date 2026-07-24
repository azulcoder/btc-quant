#!/usr/bin/env bash
# btc-quant collector — one-glance health check (remotable over SSH).
# The cloud analogue of the weekly `make check-ticks` ritual: is it alive, is it
# writing, did the last sync run, and is disk OK.
#
#   bash deploy/gcp/status.sh          # or: sudo -u btcquant bash .../status.sh
set -uo pipefail
APP_DIR="${APP_DIR:-/opt/btcquant}"
TICKS="$APP_DIR/data/ticks"

hr() { printf -- '---- %s ----\n' "$*"; }

hr "services"
systemctl is-active btcquant-collector.service | sed 's/^/collector: /'
systemctl list-timers btcquant-hfsync.timer --no-pager 2>/dev/null | sed -n '1,2p'

hr "last HF sync result"
# oneshot exit status + when it last ran
systemctl show btcquant-hfsync.service -p ExecMainStatus -p ExecMainExitTimestamp 2>/dev/null

hr "collector — last 8 log lines"
journalctl -u btcquant-collector.service -n 8 --no-pager 2>/dev/null || echo "(need root/journal access)"

hr "rotation store"
if [ -d "$TICKS" ]; then
  ls -lh --time-style=+%H:%M "$TICKS"/*.duckdb 2>/dev/null | awk '{print $5, $6, $7}'
  newest=$(ls -t "$TICKS"/*.duckdb 2>/dev/null | head -1)
  if [ -n "${newest:-}" ]; then
    now=$(date +%s); mtime=$(date -r "$newest" +%s 2>/dev/null || stat -c %Y "$newest")
    age=$(( (now - mtime) / 60 ))
    printf 'freshness: newest day file touched %s min ago' "$age"
    [ "$age" -gt 10 ] && printf '  <-- STALE (>10 min): collector may be stuck or the market data paused' || printf '  (ok)'
    printf '\n'
  fi
else
  echo "(no $TICKS yet)"
fi

hr "disk"
df -h "$APP_DIR" | awk 'NR==1 || NR==2'
