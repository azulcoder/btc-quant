# btc-quant — convenience targets (no long flags, no pasted-comment issues).
# Examples:
#   make backtest                 -> ma_trend_filter, 2018 -> now, deflated Sharpe
#   make backtest STRAT=tsmom     -> pick a strategy
#   make scan                     -> live signal snapshot
#   make test                     -> pytest
#   make dash                     -> serve dashboard at :8787 (Ctrl-C to stop)

STRAT  ?= ma_trend_filter
START  ?= 2018-01-01
TRIALS ?= 20
PORT   ?= 8787

.PHONY: help install backtest compare scan test fetch dash

help:
	@echo "targets: install | backtest [STRAT=.. START=..] | compare | scan | test | fetch | dash [PORT=..]"
	@echo "strategies: buy_and_hold ma_trend_filter tsmom pairs_coint carry"

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
