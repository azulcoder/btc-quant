# btc-quant — Bitcoin quant research terminal

A focused, **honest** quant toolkit for Bitcoin: a Python engine for research & backtesting on
your laptop, plus a dependency-free web dashboard for live charts and signals from public APIs.

> **Research & backtesting only. Not financial advice. Places no orders, holds no API keys.**
> Read [DISCLAIMER.md](DISCLAIMER.md) — a backtest is not a forecast, and edges decay.

The whole point is to see *through* flattering backtests. Every result is net of transaction
costs, ranked **out-of-sample (walk-forward)**, and reported as a **deflated Sharpe ratio** — then
guarded by three selection-overfit diagnostics (**PBO**, **MinBTL**, **CPCV**), with buy-and-hold
always the baseline. The headline is never a single equity curve. It is the applied companion to
the trading knowledge base, implementing the same ideas (expectancy/Kelly, deflated Sharpe, funding
carry, OU mean-reversion) as runnable, tested code.

If you are a practitioner: clone it, run `compare.py`, and check the numbers against the prose. They
should match — that is the design.

## What's inside

- **`btcquant/`** — the engine (pure, typed, tested; the source of truth):
  - `data.py` — fetch + cache OHLCV (Coinbase / Kraken / CoinGecko), perp funding (Bybit), Deribit option chain. No keys.
  - `features.py` — returns, realized vol, ATR, MAs, momentum, z-score, RSI, rolling Sharpe, drawdown, and the mean-reversion primitives `ou_half_life` + `ou_sigma_eq` (AR(1) fit).
  - `backtest.py` — vectorized backtester: position sizing, fees + slippage, shift-by-one (no look-ahead), plus `walk_forward` and `cpcv` (combinatorial purged CV).
  - `risk.py` — Sharpe/Sortino/CAGR/Calmar/maxDD/VaR/CVaR, Kelly, **probabilistic & deflated Sharpe**, `min_backtest_length` (MinBTL), `probability_of_backtest_overfitting` (PBO via CSCV).
  - `strategies.py` — literature-grounded baselines (buy-and-hold, MA trend filter, golden cross, time-series momentum ± vol-targeting, BTC–ETH cointegration pairs, funding carry), each citing its edge and its honest caveat. Plus `pairs_ou`, a research variant (see Methodology).
  - `report.py` — matplotlib tearsheet + JSON export for the dashboard.
- **`scripts/`** — CLIs:
  - `compare.py` — **the centerpiece**: every strategy walk-forward-validated on the same data, ranked by out-of-sample deflated Sharpe, with PBO + MinBTL. `--research` also evaluates the pre-registered candidates and prints their verdicts.
  - `run_backtest.py` — single strategy; `--walk` adds the walk-forward + CPCV multi-path dispersion.
  - `fetch_data.py`, `scan.py` — data cache; current signal snapshot.
- **`dashboard/`** — static web terminal (no build step): the OOS leaderboard, live candles + indicators, equity/drawdown, return distribution, rolling vol/Sharpe, perp funding/basis, option-IV surface. A faithful mirror of the Python engine. Open `dashboard/index.html` or serve the folder.
- **`tests/`** — `pytest` math-sanity, no-look-ahead, and harness checks.
- **`RESEARCH.md`** — the cited design brief. **`RESEARCH-partB-runlog.md`** — the pre-registered candidate run-log (a worked rejection example; see Methodology).

## Quick start

```bash
git clone https://github.com/azulcoder/btc-quant.git
cd btc-quant
python3 -m pip install -r requirements.txt        # numpy/pandas/scipy/statsmodels/matplotlib/requests/pytest

# 1) cache some data (live public API, no keys)
python3 scripts/fetch_data.py --symbol BTC-USD --granularity 1d

# 2) the OOS leaderboard — every strategy, walk-forward, ranked by deflated Sharpe vs buy-and-hold
python3 scripts/compare.py
python3 scripts/compare.py --research          # + the pre-registered candidates and their verdicts

# 3) a single strategy, with walk-forward + CPCV multi-path dispersion
#    (--start pins the 2018→ window so the figures below reproduce; the default is shorter)
python3 scripts/run_backtest.py --strategy tsmom --walk --start 2018-01-01

# 4) current signal snapshot (momentum / vol regime / funding)
python3 scripts/scan.py

# 5) tests
python3 -m pytest -q

# 6) the live web dashboard
python3 -m http.server 8787 --directory dashboard   # then open http://127.0.0.1:8787
```

The numbers in the next section come from `compare.py` (which defaults to `--start 2018-01-01`) and
`run_backtest.py --strategy tsmom --walk --start 2018-01-01`, on the 2018→ window (BTC-USD daily,
~8.4 years). Run them; you should see the same figures. Markets move, so the exact values drift over
time — the most recent (still-forming) bar nudges the CAGRs by a few hundredths — but the *shape* of
the result is stable.

## Methodology — how the honesty machinery works

### Why rank out-of-sample, not in-sample

A backtest fit and scored on the same history flatters itself. The leaderboard instead ranks by the
**walk-forward out-of-sample** deflated Sharpe: fit on each in-sample block, trade the *next*
held-out block, score on the concatenated OOS returns (Bailey & López de Prado 2014). The drop from
in-sample to out-of-sample Sharpe is the overfitting tell, and it is printed side by side.

The honest result, from `compare.py` on the 2018→ daily history (N = 5 strategies):

```
strategy            OOS CAGR   OOS SR    IS SR   OOS DSR  OOS MaxDD  beats B&H
tsmom                 12.38%     0.99     1.31     0.93     -22.51%        yes
buy_and_hold          33.25%     0.78     0.76     0.81     -77.29%        (baseline)
tsmom_ls              12.46%     0.75     1.09     0.79     -24.48%        no
ma_trend_filter       24.89%     0.70     0.93     0.74     -65.94%        no
pairs_coint           -0.19%    -0.00     0.05     0.12     -14.85%        no
```

Read it straight: **every strategy's Sharpe decays in-sample → out-of-sample** (tsmom 1.31 → 0.99,
ma_trend 0.93 → 0.70, pairs 0.05 → −0.00). On this long window `tsmom` tops the board and does beat
buy-and-hold — **but nothing clears OOS deflated Sharpe 0.95**, the threshold for "distinguishable
from luck after deflating for the number of strategies tried." Even the winner (0.93) is not
significant. The other three trend/reversion strategies do not beat buy-and-hold net of cost
out-of-sample, and the one that wins on return (buy-and-hold, +33% CAGR) does it with a −77%
drawdown. That is the point, not a disappointment: most of what survives crypto OOS is
risk-management, not alpha.

This is **window-dependent**, and the tool is honest about that too: on the dashboard's shorter
default window buy-and-hold tops the board instead (trend-following had fewer clean cycles to catch),
and the overfit probability is higher. Which is why the next section matters.

### The selection-overfit guards: PBO, MinBTL, CPCV

Ranking by OOS Sharpe is necessary but not sufficient — *picking the best of N* is itself a way to
overfit. Three diagnostics guard the selection:

- **PBO — Probability of Backtest Overfitting** (CSCV; Bailey, Borwein, López de Prado & Zhu 2017).
  Over every way to split the history into in-sample / out-of-sample blocks, how often would "keep
  the backtest winner" have picked an out-of-sample *under*-performer? Above ~0.50 the ranking is
  essentially noise. **The number depends on the data window and N — do not quote a single value as
  "the" PBO:**
  - `compare.py` (N = 5, 2018→) prints **PBO ≈ 0.67**.
  - `compare.py --research` (N = 8, the same window + the research candidates) prints **≈ 0.63**;
    inside it, PBO over just the 5 board strategies is again 0.67.
  - the dashboard's shorter default window (N = 7) shows **≈ 0.83**.

  All three say the same thing — the cross-sectional ranking is not robust — but only the value from
  *your* command is the one to cite. Run it and you will see it.

- **MinBTL — Minimum Backtest Length** (Bailey et al. 2014). Given N configurations tried, how many
  years of history do you need before an in-sample Sharpe of ~1 is expected even from pure noise?
  `compare.py` prints **2.70 yr for N = 5** (2.85 yr for N = 8 under `--research`) against 8.4 yr of
  data — so here the history is long enough. On shorter windows it correctly flags the backtest as
  under-powered. Every added strategy raises the required MinBTL and lowers everyone's deflated
  Sharpe — which is the explicit cost of putting another strategy on the board.

- **CPCV — Combinatorial Purged Cross-Validation.** Instead of one walk-forward path, score the
  strategy over many purged block combinations and report the *distribution* of OOS Sharpe.
  `run_backtest.py --strategy tsmom --walk --start 2018-01-01` prints **median 0.97 [p25 0.68,
  p75 1.50] over 15 paths** — wide and sign-flipping (on a recent default window it is even negative).
  A single equity curve hides that; the dispersion says the result is regime-dependent, not a stable
  edge.

### A worked example: the Part B rejection log

The harness is only credible if it actually rejects things. Three candidates were pre-registered
(hypothesis + falsifiable kill criterion *before* running) and judged purely on OOS DSR / PBO. None
were promoted — the full log is [RESEARCH-partB-runlog.md](RESEARCH-partB-runlog.md):

- **B1, tsmom × vol-target** — *killed as a literal duplicate.* A vol-target overlay on directional
  momentum came back **correlation 1.00** with the board's already-vol-scaled `tsmom` (byte-identical
  OOS rows, DSR 0.89). It improved on the *raw directional* baseline (0.82 → 0.89) but that strategy
  already exists; a second copy only burns MinBTL headroom.
- **B2, OU-reversion pairs** — *killed as "a model, not an edge."* `pairs_ou` changes exactly one
  thing versus the fixed-z `pairs_coint`: it normalizes the spread by the OU-model stationary σ
  (`features.ou_sigma_eq`) instead of the empirical rolling std. OOS DSR **0.04 vs 0.07** (worse),
  max-DD −52% vs −15%. The fitted OU parameters are non-stationary in crypto, so the parametric model
  adds nothing — the simpler empirical z-score wins.
- **B3, funding carry** — *descriptive only, by construction.* Carry is a funding-stream sleeve, not a
  price-position strategy, and the keyless funding history (~200 8h intervals ≈ 0.18 yr) is far below
  MinBTL. It is reported with its realized APR and never given a deflated Sharpe or a leaderboard slot.

Rejecting a duplicate and a non-stationary model on *real* candidates is the most honest thing the
tool does.

### One number across the page (DSR unification)

The dashboard's headline deflated Sharpe is, by construction, the selected strategy's walk-forward
OOS leaderboard row — one number, read from a single source, not a parallel recompute. When
walk-forward cannot run (too little history, e.g. a thin pair), the panel degrades to "insufficient
history for OOS" rather than silently falling back to the flattering in-sample figure. The dashboard
mirrors the Python engine's formulas and agrees with it to ~1e-8 (the inverse-normal approximation,
not bit-identical); the engine is the source of truth.

## Honesty rails (non-negotiable)

- No look-ahead: signals trade the **next** bar; tests assert it.
- Costs + slippage on by default; gross is never shown without net beside it.
- Strategies are ranked **walk-forward out-of-sample**, by **deflated Sharpe**, with buy-and-hold as
  the benchmark — never by an in-sample fit.
- Selection overfit is reported, not hidden: **PBO**, **MinBTL**, **CPCV** alongside the ranking.
- Candidates are pre-registered with a kill criterion and rejected when they fail; rejections are
  documented as findings ([RESEARCH-partB-runlog.md](RESEARCH-partB-runlog.md)).
- No fabricated data: a feed that is unreachable degrades the panel; it is never filled in.
- No keys, no orders, no authenticated endpoints — pure research.

See [DESIGN.md](DESIGN.md) for module contracts and [RESEARCH.md](RESEARCH.md) for the cited rationale.
