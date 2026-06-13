# btc-quant — Bitcoin quant research terminal

A focused, **honest** quant toolkit for Bitcoin: a Python engine for research & backtesting on
your laptop, plus a static web dashboard for live charts and signals from public APIs.

> **Research & backtesting only. Not financial advice. Places no orders, holds no API keys.**
> Read [DISCLAIMER.md](DISCLAIMER.md) — a backtest is not a forecast, and edges decay.

The whole point is to see *through* flattering backtests: every result carries transaction
costs, out-of-sample / walk-forward evaluation, and a **deflated Sharpe ratio**, with
buy-and-hold always shown as the baseline. This is the applied companion to the trading
knowledge base — it implements the same ideas (expectancy/Kelly, deflated Sharpe, funding
carry, CVD, OU mean-reversion) as runnable, testable code.

## What's inside

- **`btcquant/`** — the engine (pure, typed, tested):
  - `data.py` — fetch + cache OHLCV (Coinbase / Kraken / CoinGecko) and perp funding (Bybit). No keys.
  - `features.py` — indicators/signals: returns, realized vol, ATR, MAs, momentum, z-score, OU half-life, RSI, rolling Sharpe, drawdown.
  - `backtest.py` — vectorized backtester with position sizing, fees + slippage, no look-ahead, and walk-forward.
  - `risk.py` — Sharpe/Sortino/CAGR/Calmar/maxDD/VaR/CVaR, Kelly, **probabilistic & deflated Sharpe**.
  - `strategies.py` — literature-grounded baselines (time-series momentum, MA-cross, vol-targeted hold, mean-reversion, funding carry), each citing its edge and its honest caveat.
  - `report.py` — matplotlib tearsheet + JSON export for the dashboard.
- **`scripts/`** — CLIs: `fetch_data.py`, `run_backtest.py`, `scan.py` (current signal snapshot).
- **`dashboard/`** — static web terminal (no build step): live candles + indicators, equity/drawdown, return distribution, rolling vol/Sharpe, funding. Open `dashboard/index.html` or serve the folder.
- **`tests/`** — `pytest` math-sanity + no-look-ahead checks.
- **`RESEARCH.md`** — the cited design brief behind the strategy choices (what actually survives OOS).

## Quick start

```bash
cd ~/Code/btc-quant
python3 -m pip install -r requirements.txt        # numpy/pandas/scipy/statsmodels/matplotlib/requests/pytest

# 1) cache some data (live public API, no keys)
python3 scripts/fetch_data.py --symbol BTC-USD --granularity 1d

# 2) run an honest backtest (costs + walk-forward + deflated Sharpe), with buy-and-hold baseline
python3 scripts/run_backtest.py --strategy tsmom --granularity 1d

# 3) current signal snapshot (momentum / vol regime / funding)
python3 scripts/scan.py

# 4) tests
python3 -m pytest -q

# 5) the live web dashboard
python3 -m http.server 8787 --directory dashboard   # then open http://127.0.0.1:8787
```

## Honesty rails (non-negotiable)

- No look-ahead: signals trade the **next** bar; tests assert it.
- Costs + slippage are on by default; gross is never shown without net beside it.
- Every backtest reports **deflated Sharpe** and **out-of-sample** stats; buy-and-hold is the benchmark.
- No keys, no orders, no authenticated endpoints — pure research.

See [DESIGN.md](DESIGN.md) for module contracts and [RESEARCH.md](RESEARCH.md) for the cited rationale.
