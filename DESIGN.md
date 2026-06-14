# btc-quant — design contract

A Bitcoin **quant research terminal**: a Python engine (research/backtest on your laptop) + a
static web dashboard (live charts from public APIs). Research & backtest only — **no orders,
no API keys**. See [DISCLAIMER.md](DISCLAIMER.md). The honesty rails (costs, walk-forward,
deflated Sharpe) are not optional decoration — they are the product.

> This file is the **module contract** (signatures + non-negotiables). For architecture, the
> Python↔JS parity rule, extend-recipes, the verification suite, gotchas, and the roadmap, read
> **[DEVELOPMENT.md](DEVELOPMENT.md)**.

## Architecture

```
btcquant/            Python package (the engine)
  data.py            fetch + cache market data (public APIs, no keys)
  features.py        indicators / signals (pure functions on pandas Series/DataFrame)
  backtest.py        vectorized backtester: sizing, costs, slippage, walk-forward
  risk.py            performance & risk stats incl. deflated/probabilistic Sharpe
  strategies.py      strategy library — each returns a target-position Series + cites its edge
  report.py          matplotlib tearsheet + JSON export for the dashboard
scripts/             CLIs: fetch_data.py, run_backtest.py, scan.py
tests/               pytest — math sanity, no-lookahead, vectorized==reference
dashboard/           static web terminal (index.html, app.js, charts.js, quant.js, styles.css)
data/                cached CSV/JSON (gitignored)
notebooks/           starter research notebook
```

## Module contracts (build agents MUST follow these signatures)

### data.py
- `get_ohlcv(symbol="BTC-USD", source="coinbase", granularity="1d", start=None, end=None, cache=True) -> pd.DataFrame`
  - returns columns `[open, high, low, close, volume]`, a **UTC DatetimeIndex**, ascending, de-duplicated.
  - sources: `coinbase` (api.exchange.coinbase.com/products/{sym}/candles, max 300/req → paginate),
    `kraken` (api.kraken.com/0/public/OHLC), `coingecko` (market_chart, daily). Granularities: `1h`,`1d`.
  - caches to `data/{source}_{symbol}_{granularity}.csv`; on network failure, loads cache and warns.
- `get_funding(symbol="BTCUSDT", source="bybit", limit=200) -> pd.DataFrame`  → `[funding_rate]` UTC index
  (Bybit `/v5/market/funding/history`). Funding is perp-only; document that.
- `get_option_chain(currency="BTC") -> pd.DataFrame` — one Deribit `get_book_summary_by_currency`
  call; columns incl. `[instrument_name, expiry, strike, opt_type, iv, mark_iv, open_interest,
  underlying_price, …]`. **No greeks, `mark_iv` only** (see DEVELOPMENT.md gotchas). `get_dvol`,
  `get_onchain` for the DVOL index + on-chain context (descriptive).
- A tiny HTTP helper with timeout + retry + a clear error if all sources fail.
- NEVER require an API key. NEVER call a private/authenticated endpoint.

### features.py  (pure; input a `close`/returns Series unless noted)
- `log_returns(close)`, `simple_returns(close)`
- `realized_vol(returns, window=20, periods_per_year=365)`  (annualized)
- `atr(df, window=14)`  (needs OHLC)
- `sma(s, n)`, `ema(s, n)`
- `momentum(close, lookback=90)`  (total return over lookback; the TSMOM signal)
- `zscore(s, window=30)`
- `ou_half_life(spread)`  (AR(1) fit → ln2/κ; ∞ if not mean-reverting) and `ou_sigma_eq(spread)`
  (OU stationary σ, used by the `pairs_ou` research variant)
- `rsi(close, window=14)`
- `rolling_sharpe(returns, window=90, periods_per_year=365)`
- `drawdown(equity)` → Series of drawdown; `max_drawdown(equity)` → float
- Option surface (consume `get_option_chain`): `year_fraction_to_expiry`, `atm_iv`,
  `iv_term_structure`, `iv_skew_25d` (RR25), `smile`, `black76_greeks` (delta/gamma/vega — MARK,
  validated vs Deribit ticker), `max_pain`, `gamma_concentration` (unsigned Σ|γ|·OI). See the
  options run-log.
- Every function: no look-ahead (use only past data at each point); docstring states the convention.

### backtest.py
- `run(positions, prices, cost_bps=10, slippage_bps=2, periods_per_year=365) -> dict`
  - `positions`: target weight in **[-1, 1]** per bar (or [0,1] for long-only), **shifted internally
    by 1 bar** so today's signal trades tomorrow's open→close (NO look-ahead — assert this).
  - cost charged on **turnover** = |Δposition|; returns net of cost+slippage.
  - returns `{equity, returns, gross_returns, turnover, trades, stats}` where `stats` comes from risk.py.
- `walk_forward(make_positions, prices, n_splits=5, ...)` — fit on each in-sample block, evaluate the
  next out-of-sample block, concatenate OOS; report combined OOS stats vs in-sample (the overfitting tell).
- `cpcv(make_positions, prices, n_blocks=6, k_test=2, embargo=0.01, ...)` — combinatorial purged CV:
  a *distribution* of OOS Sharpe across block subsets (median ± IQR), not a single path.

### risk.py  (pure; input a returns Series)
- `sharpe`, `sortino`, `cagr`, `volatility`, `calmar`, `max_drawdown`, `hit_rate`, `turnover_to_cost`
- `var(returns, alpha=0.05)`, `cvar(...)` (historical)
- `kelly_fraction(mean, var)` and a binary-bet `kelly(p, b)` (from the Lattice expectancy module)
- `probabilistic_sharpe_ratio(sr, n, skew, kurt, sr_benchmark=0)` — Bailey & López de Prado (2012)
- `deflated_sharpe_ratio(sr, n, skew, kurt, n_trials, var_trials_sr)` — Bailey & López de Prado (2014):
  benchmark the observed SR against the **expected max SR of N skill-less trials**. This is the headline
  honesty metric — surface it on every backtest.
- `min_backtest_length(n_trials)` (MinBTL, Bailey 2014) and
  `probability_of_backtest_overfitting(returns_matrix, n_blocks=8)` (PBO via CSCV) — the
  selection-overfit guards reported alongside the leaderboard ranking.
- `summary(returns, equity=None) -> dict` bundling the above.

### strategies.py  (each: `df -> pd.Series` of target positions in [-1,1] or [0,1]; rich docstring)
Implement exactly the **ranked first-cut set from `RESEARCH.md` §5** (that section is authoritative for
formulas, params, evidence tags, citations, and caveats — read it):
- `buy_and_hold(df)` — the BASELINE every strategy is scored against (long/flat = always 1).
- `ma_trend_filter(df, n=200)` and the dual-cross `50/200` variant — long-above-MA / else flat. Risk management, [Practitioner].
- `vol_target(positions, df, target_vol=0.15)` — a **sizing layer** wrapping any signal (scales by target_vol/σ_t). [Mixed; tail control].
- `tsmom(df, lookback=20, vol_scaled=True)` — short-lookback (days–4wk) time-series momentum, vol-scaled. [Mixed], cost-fragile (~3–10 bps breakeven — surface it).
- `carry(funding_df, ...)` — long-spot/short-perp funding harvest; show the 2021→2025 decay and negative-funding inversion. [Established, decaying].
- `pairs_coint(btc, eth, window=60, entry=2.0, exit=0.5, stop=4.0, max_half_life=60)` — BTC–ETH z-score spread reversion with an OU half-life cointegration-breakdown guard. [Mixed]. `pairs_ou` is a research variant (OU stationary-σ normalizer instead of empirical z) — rejected as "model, not edge" (see Part B run-log).
- `short_vol(...)` — OPTIONAL / last; needs Deribit option data → ship as a documented stub that raises a clear NotImplementedError with guidance (do NOT fake option data).
Supporting components (used BY the above, not standalone): `features.ou_half_life`, `risk.kelly_fraction`, an optional simple `vol_forecast`.
Each docstring states: the edge, the `[Established]/[Practitioner]/[Mixed]/[Weak]` tag, the primary citation, and the honest
caveat (when it works, when it inverts, how it decays). **Buy-and-hold is always shown as the baseline; the headline
metric is the net-of-cost, out-of-sample, deflated Sharpe — never a single equity curve.**

### dashboard/ (static, no build — same ethos as Lattice)
- `index.html` shell + panels; `styles.css` dark terminal theme.
- `app.js`: fetch live public data (Coinbase OHLCV, Bybit funding, CoinGecko context) **client-side**;
  handle CORS/rate-limits/geo gracefully with a source fallback + a clear banner if data is stale.
- `quant.js`: the **requireable JS mirror** of the engine — features, the full OOS harness
  (`walkForward`/`pbo`/`minBacktestLength`/`cpcv`), risk (`deflatedSharpe`/`probabilisticSharpe`),
  options (`black76Greeks`/`maxPain`/`gammaConcentration`), and the strategy signals. It mirrors the
  Python conventions (shift-by-1, turnover cost) and is **parity-checked** vs `btcquant/` (DEVELOPMENT.md §4).
  `app.js` calls `Q.*` for ALL math — it never computes analytics itself.
- `charts.js`: dependency-free SVG/Canvas — candlesticks + MA overlay, equity curve, drawdown,
  returns histogram, rolling vol/Sharpe, funding bar. Reuse Lattice's pure-SVG approach.
- A persistent **"NOT FINANCIAL ADVICE · backtest ≠ forecast"** banner.

## Non-negotiables
- No look-ahead anywhere (signals shift by 1 bar before they trade). Tests must assert it.
- Costs + slippage on by default; never show a gross-only equity curve without the net one beside it.
- Every strategy compared to buy-and-hold after costs; **ranked by walk-forward OOS deflated Sharpe**,
  never the in-sample fit; selection overfit (PBO/MinBTL/CPCV) reported alongside.
- **Python is the source of truth; `quant.js` mirrors it and must agree** (parity-checked). Shared
  formulas live in both; change both together. `app.js` renders, `charts.js` draws — neither holds analytics.
- **Captions fully derived** — a number in prose is the same computed value that drives its chart
  (only methodology constants + cited figures are literals). A dead feed **degrades visibly**, never
  fabricates or silently goes stale.
- No keys, no orders, no authenticated endpoints. Pure research.
- **Commits carry NO AI attribution.**
- Code style: type hints, docstrings, small pure functions, pytest-able.
