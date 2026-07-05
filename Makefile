# btc-quant — convenience targets (no long flags, no pasted-comment issues).
# Examples:
#   make backtest                 -> ma_trend_filter, 2018 -> now, deflated Sharpe
#   make backtest STRAT=tsmom     -> pick a strategy
#   make scan                     -> live signal snapshot
#   make test                     -> pytest
#   make dash                     -> serve dashboard at :8787 (Ctrl-C to stop)
#   make collector                -> O-0 tick collector daemon (Ctrl-C flushes + exits)
#   make collector-api            -> same, plus the BYOD replay API at :8788

STRAT  ?= ma_trend_filter
START  ?= 2018-01-01
TRIALS ?= 20
PORT   ?= 8787
SYMBOL   ?= BTCUSDT
API_PORT ?= 8788

.PHONY: help install backtest compare scan test fetch dash collector collector-api verify-browser verify-wire check-ticks econ archive archive-dry archive-list

help:
	@echo "targets: install | backtest [STRAT=.. START=..] | compare | scan | test | fetch | dash [PORT=..] | collector [SYMBOL=..] | collector-api [SYMBOL=.. API_PORT=..]"
	@echo "verify:  verify-browser (L1 fixture-replay in headless Chromium) | verify-wire (L2 live invariants, ~45s) | check-ticks (L3 tick-store QA)"
	@echo "archive: archive-dry (export closed months to local parquet ONLY) | archive (export + upload to GitHub Releases + prune) | archive-list (what is offsite)"
	@echo "strategies: buy_and_hold ma_trend_filter tsmom pairs_coint carry"
	@echo "collector needs opt-in deps: pip install -r requirements-collector.txt"

compare:
	python3 scripts/compare.py --start $(START)

install:
	python3 -m pip install -r requirements.txt

backtest:
	MPLBACKEND=Agg python3 scripts/run_backtest.py --strategy $(STRAT) --start $(START) --n-trials $(TRIALS)

scan:
	python3 scripts/scan.py

test:
	python3 -m pytest -q

fetch:
	python3 scripts/fetch_data.py --symbol BTC-USD --granularity 1d --start $(START)

dash:
	@echo "Dashboard -> http://127.0.0.1:$(PORT)   (Ctrl-C to stop)"
	python3 -m http.server $(PORT) --directory dashboard

# O-0 tick collector (DESIGN-orderflow-terminal.md §3). Keyless public feeds ->
# data/ticks.duckdb (gitignored, keep-all). Opt-in deps: requirements-collector.txt.
collector:
	python3 scripts/run_collector.py --symbol $(SYMBOL) --exchanges binancef,bybit --db data/ticks.duckdb

collector-api:
	@echo "BYOD API -> http://127.0.0.1:$(API_PORT)   (Ctrl-C to stop)"
	python3 scripts/run_collector.py --symbol $(SYMBOL) --exchanges binancef,bybit --db data/ticks.duckdb --api-port $(API_PORT)

# O-5 econ-calendar mirror (DESIGN-orderflow-terminal.md §4e): the faireconomy
# JSON has NO CORS header so the browser cannot fetch it — this writes the
# same-origin dashboard/econ_calendar.json (gitignored) the EconView reads.
# Re-run whenever the panel says the mirror is stale.
econ:
	python3 scripts/fetch_econ.py

# Data lifecycle (DESIGN-orderflow-terminal.md §3 'Data lifecycle'): the store grows
# ~2.4 GB/month on a disk-limited machine, so closed UTC months move to GitHub
# Releases (immutable, checksummed, provenance-stamped parquet) BEFORE any prune.
# Stop the collector first — the script refuses otherwise; the window is an honest
# maintenance gap. archive-dry stages local parquet only (nothing uploaded, nothing
# pruned); archive is the real lifecycle (prune runs only after the upload is
# byte-verified, and asks for confirmation); archive-list shows what is offsite.
# Extra flags via ARGS, e.g.:  make archive ARGS="--month 2026-07 --partial"
archive:
	python3 scripts/archive_ticks.py --db data/ticks.duckdb --out data/archive --upload --prune $(ARGS)

archive-dry:
	python3 scripts/archive_ticks.py --db data/ticks.duckdb --out data/archive $(ARGS)

archive-list:
	python3 scripts/archive_ticks.py --list

# Terminal verification layers (DESIGN-orderflow-terminal.md §7).
# L1: deterministic — replays the captured fixture frames through the real terminal in
#     headless Chromium (?replay=1), asserts render + zero console errors, screenshots
#     to reports/verify/. Needs: pip install playwright && playwright install chromium.
verify-browser:
	python3 scripts/verify_terminal_browser.py

# L2: live wire — drives the PRODUCTION adapters/stores from the real public feeds for
#     ~45s and checks cross-venue invariants (never-crossed books, coherent mids,
#     ts sanity, CVD bucket sums). Exit 2 = offline (not a code bug).
verify-wire:
	node scripts/verify_wire_live.mjs --seconds 45

# L3: tick-store QA report card over data/ticks.duckdb (gaps/dupes/cadence/coherence —
#     reported, never filled). Run while the collector is stopped, or on a copy.
check-ticks:
	python3 scripts/check_ticks.py
