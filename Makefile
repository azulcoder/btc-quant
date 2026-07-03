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

.PHONY: help install backtest compare scan test fetch dash collector collector-api

help:
	@echo "targets: install | backtest [STRAT=.. START=..] | compare | scan | test | fetch | dash [PORT=..] | collector [SYMBOL=..] | collector-api [SYMBOL=.. API_PORT=..]"
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
