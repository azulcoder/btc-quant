#!/usr/bin/env bash
# The whole local terminal in one command (`make local`).
#
# Why this exists: the complete surface needs TWO processes, and which of them is
# already running depends on whether the launchd agent is loaded. Getting that
# wrong is not harmless — starting a second collector while the agent owns the
# port dies with "Address already in use" AFTER the agent has been disturbed, and
# a collector that is not recording is a hole in the archive that no later run
# can fill. So this script never assumes: it probes, reports, and only starts
# what is genuinely absent.
#
# It also states the DEGRADED surface out loud. The terminal renders fine with a
# dead collector API — four panels just quietly say they are offline — and a
# quiet degradation is the failure mode this repo exists to refuse.
#
#   PORT      dashboard http port          (default 8787)
#   API_PORT  collector BYOD API port      (default 8788)
#   NO_API=1  skip the collector entirely (charts-only; the four API panels stay off)
set -u -o pipefail

PORT="${PORT:-8787}"
API_PORT="${API_PORT:-8788}"
API="http://127.0.0.1:${API_PORT}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

b() { printf '\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m●\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m●\033[0m %s\n' "$*"; }
bad()  { printf '  \033[31m●\033[0m %s\n' "$*"; }
dim()  { printf '    \033[2m%s\033[0m\n' "$*"; }

listening() { lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }

STARTED_COLLECTOR=0
DASH_PID=""

cleanup() {
  # Only ever stop what THIS script started. A launchd-owned collector is not
  # ours to kill, and killing it would create a gap in the tape for no reason.
  [ -n "$DASH_PID" ] && kill "$DASH_PID" 2>/dev/null
  if [ "$STARTED_COLLECTOR" = "1" ]; then
    echo ""
    echo "stopping the collector this script started (SIGTERM -> final flush)…"
    pkill -TERM -f "run_collector.py --symbol ${SYMBOL:-BTCUSDT} --api-port ${API_PORT}" 2>/dev/null
  fi
  echo ""
  echo "local stack stopped."
}
trap cleanup EXIT INT TERM

b "btc-quant — local stack"
echo ""

# ── 1. preflight ────────────────────────────────────────────────────────────
b "preflight"
command -v python3 >/dev/null || { bad "python3 not on PATH"; exit 1; }
ok "python3 $(python3 -V 2>&1 | cut -d' ' -f2)"
if python3 -c "import duckdb, websockets" 2>/dev/null; then
  ok "collector deps present (duckdb, websockets)"
else
  warn "collector deps missing — pip install -r requirements-collector.txt"
  dim "the terminal still runs; the four collector-API panels stay offline"
fi

# ── 2. collector / BYOD API ─────────────────────────────────────────────────
echo ""
b "collector API (${API})"
if [ "${NO_API:-0}" = "1" ]; then
  warn "skipped (NO_API=1) — auction profile, daily levels, key levels, VWAP stay offline"
elif curl -sf --max-time 4 "${API}/health" >/dev/null 2>&1; then
  OWNER="launchd agent"
  launchctl list 2>/dev/null | grep -q com.btcquant.collector || OWNER="an existing process"
  ok "already up, owned by ${OWNER} — not touching it"
elif listening "$API_PORT"; then
  bad "port ${API_PORT} is bound but /health does not answer"
  dim "something else holds the port; free it or run with API_PORT=<other>"
else
  echo "  starting the collector…"
  python3 -u scripts/run_collector.py --symbol "${SYMBOL:-BTCUSDT}" --api-port "$API_PORT" \
    >> data/collector.log 2>&1 &
  STARTED_COLLECTOR=1
  for _ in $(seq 1 30); do
    curl -sf --max-time 2 "${API}/health" >/dev/null 2>&1 && break
    sleep 1
  done
  if curl -sf --max-time 2 "${API}/health" >/dev/null 2>&1; then
    ok "started (log: data/collector.log)"
  else
    bad "did not come up in 30s — see data/collector.log"
  fi
fi

# Per-leg truth, straight from /health. `ok:false` here is the design working,
# not a script failure: the collector reports degraded rather than pretending.
if curl -sf --max-time 4 "${API}/health" >/dev/null 2>&1; then
  # Locals, not nested quotes: an f-string expression carrying escaped quotes is
  # a 3.12+-only construct and this script has no business demanding that.
  curl -s --max-time 4 "${API}/health" | python3 -c '
import json, sys
h = json.load(sys.stdin)
G, Y, R, Z = "\033[32m", "\033[33m", "\033[31m", "\033[0m"
mark = (G if h.get("ok") else Y) + "●" + Z
st = h.get("status")
up = (h.get("uptime_s") or 0) / 60.0
lok, ltot = h.get("legs_ok"), h.get("legs_total")
print(f"  {mark} status={st} uptime={up:.0f}m legs {lok}/{ltot}")
bad = h.get("unhealthy") or []
if bad:
    print("    " + R + "unhealthy" + Z + ": " + ", ".join(bad))
    for n in bad:
        L = (h.get("legs") or {}).get(n, {})
        state, rows_n = L.get("state"), L.get("rows")
        rst, rp = L.get("restarts"), L.get("reprobes", 0)
        tail = "  (re-probed every 15 min)" if rp else ""
        print(f"      {n}: {state} rows={rows_n} restarts={rst} reprobes={rp}{tail}")
    print("    these legs are NOT recording. The gap is real and is never backfilled.")
w = h.get("writer") or {}
rows = {k: v for k, v in (w.get("rows_written") or {}).items() if v}
if rows:
    print("    written this session: " + ", ".join(f"{k}={v:,}" for k, v in rows.items()))
'
fi

# ── 3. dashboard ────────────────────────────────────────────────────────────
echo ""
b "dashboard (http://127.0.0.1:${PORT})"
if listening "$PORT"; then
  ok "already served on ${PORT} — reusing it"
else
  # Bound to loopback ON PURPOSE. The terminal talks to the API at
  # 127.0.0.1:${API_PORT}, so a LAN client could load the page but never reach
  # the API — it would get the degraded surface and no warning about why.
  # Serving only to this machine keeps "it loaded" and "it works" the same thing.
  python3 -m http.server "$PORT" --bind 127.0.0.1 --directory dashboard >/dev/null 2>&1 &
  DASH_PID=$!
  sleep 1
  if curl -sf --max-time 3 "http://127.0.0.1:${PORT}/terminal.html" >/dev/null 2>&1; then
    ok "started (loopback only)"
  else
    bad "did not come up on ${PORT}"
    exit 1
  fi
fi

# ── 4. what you actually get ────────────────────────────────────────────────
echo ""
b "open"
echo "    terminal    http://127.0.0.1:${PORT}/terminal.html"
echo "    dashboard   http://127.0.0.1:${PORT}/index.html"
echo ""
b "live only here, never on the public Pages build"
dim "an https page cannot fetch http://127.0.0.1 (mixed content), so these four"
dim "panels are structurally offline on the deployed site:"
echo "      auction profile · daily levels · key-levels registry · session VWAP"
if [ -f dashboard/econ_calendar.json ]; then
  ok "econ calendar mirror present"
else
  dim "econ calendar: run \`make econ\` to mirror it (faireconomy sends no CORS header)"
fi
echo ""
dim "Ctrl-C stops the dashboard. A launchd-owned collector keeps recording."
echo ""

# Foreground wait: the script IS the stack, so Ctrl-C is the honest way out.
while true; do sleep 3600; done
