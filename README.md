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
  - `risk.py` — Sharpe/Sortino/CAGR/Calmar/maxDD/VaR/CVaR (+ **EVT POT-GPD tail VaR/ES**, PWM-fitted, parity-mirrored), Kelly, **probabilistic & deflated Sharpe**, `min_backtest_length` (MinBTL), `probability_of_backtest_overfitting` (PBO via CSCV).
  - `strategies.py` — literature-grounded baselines (buy-and-hold, MA trend filter, golden cross, time-series momentum ± vol-targeting, BTC–ETH cointegration pairs, funding carry), each citing its edge and its honest caveat. Plus `pairs_ou`, a research variant (see Methodology).
  - `report.py` — matplotlib tearsheet + JSON export for the dashboard.
  - `collector.py` — **optional** tick collector daemon (`requirements-collector.txt`): keyless WS/REST → local DuckDB (trades, liquidations, depth, funding/mark, OI; keep-all retention). Starts the clock on order-flow history so those families can *eventually* be OOS-tested — see `DESIGN-orderflow-terminal.md` §6. When the disk fills, `make archive` moves closed months to GitHub Releases as checksummed parquet and only then prunes the store (DESIGN §3 "Data lifecycle"). The **primary offsite home is the Hugging Face dataset** `azulcoder/btc-quant-ticks` (`make hf-sync`, DESIGN §3c): closed UTC day files land as Hub-verified hive partitions, queryable in place — `SELECT count(*) FROM read_parquet('hf://datasets/azulcoder/btc-quant-ticks/data/date=2026-07-04/trades.parquet')`.
- **`scripts/`** — CLIs:
  - `compare.py` — **the centerpiece**: every strategy walk-forward-validated on the same data, ranked by out-of-sample deflated Sharpe, with PBO + MinBTL. `--research` also evaluates the pre-registered candidates and prints their verdicts.
  - `run_backtest.py` — single strategy; `--walk` adds the walk-forward + CPCV multi-path dispersion.
  - `fetch_data.py`, `scan.py` — data cache; current signal snapshot.
  - `run_collector.py` — the tick-collector CLI (`make collector`), with an optional read-only BYOD HTTP API.
- **`dashboard/`** — static web terminal (no build step): the OOS leaderboard, live candles + indicators, equity/drawdown, return distribution, rolling vol/Sharpe, perp funding/basis, option-IV surface. A faithful mirror of the Python engine. Open `dashboard/index.html` or serve the folder.
  - **`terminal.html`** — the **orderflow terminal** (live-descriptive ONLY, never a backtest input): footprint chart + session volume profile (POC/VAH/VAL), DOM ladder, time-and-sales tape, multi-exchange aggregated orderbook, CVD by trade-size bucket **and per exchange**, liquidation feed, mark/funding/OI header; plus the O-2 heatmap layer — session **orderbook heatmap** (time × price × resting liquidity), **spoof-pull / iceberg-refill detections** (labeled *heuristic* — patterns consistent with, not proof), and a **liquidation heatmap** (labeled *ESTIMATED (model)* — volume-weighted entry proxy × standard leverage tiers). Plus the O-3 structure layer — historical chart with **no-peek bar replay**, classical 30-min **TPO / Market Profile**, composite volume profile (labeled *bar-range approximation*), cross-venue **funding/OI table** (descriptive; carry stays off-board), **macro proxies** (HIP-3 index/commodity perps + PAXG — labeled proxies, *no CME feeds*), and **BYOD tick replay**: `terminal.html?replay=byod` re-plays your own collector recordings through the same panels. The O-4 intelligence layer adds a 720-symbol **VWAP-deviation screener** + **RSI heatmap** (bybit, labeled *24h VWAP = turnover/volume*), a **Deribit options widget** (IV smile/term/heatmap, PCR, max pain, **unsigned Σ|Γ|·OI** — dealer sign unknowable), **Hyperliquid whale watchlist** (public on-chain state — *facts, not signals*), a 9-rule **alert engine** and a 9-read **mechanical confluence board** — both labeled *un-validated descriptive, NOT signals* (forward IC of board signals ≈ 0, RESEARCH-ic-runlog). The O-5 portfolio layer completes the map: **trade journal** (your own logged trades — Tharp R-multiple stats, *NOT a backtest*; CSV export/import), **calendar returns**, **Polymarket BTC panel** (*crowd-implied probabilities*), **Tree-of-Alpha news feed**, and a **local-mirror econ calendar** (`make econ` — the origin has no CORS). The I-1 **Institutional Auction Suite** goes beyond the map: **tick-exact volume profiles** from the recorded store (total / buy×sell / **delta** / size-bucket; server-side DuckDB aggregation — a 2.87M-trade day in ~20ms), a **daily levels registry** with serve-time **naked-POC** derivation, session **VWAP ±1σ/±2σ bands** (AMT read: value ≈ vwap ± 1σ), **OFI (Cont–Kukanov–Stoikov) + microprice (Stoikov)** panes, footprint stacked-imbalance zones + absorption flags (*heuristic*), and **in-browser archived-day analytics**: the public terminal can pull any recorded day straight from the HF dataset (vendored hyparquet+fzstd, size-gated). Elite pass: sticky section nav with persisted collapse, hidden-tab/offscreen paint gating (ingestion never pauses), and the terminal fixture smoke (now 55 groups) promoted to a **CI build gate**. The T-1 **Trader's Edge** pass makes the symbol **runtime-switchable** (Bybit linear universe; every WS leg re-derives its venue id, probes listability, and degrades to an honest *no-leg* chip — store-backed panels state their BTCUSDT-only reality), and adds per-bar **Δmin/Δmax/Δ%** footprint rows + **unfinished-auction** markers, a **tape-intensity** gauge (z vs session baseline — *burst ≠ signal*), a resting-order **walls ledger** (*book-history bookkeeping, not intent*), **VPIN** (Easley–López de Prado–O'Hara 2012, computed from real aggressor flags; *toxicity interpretation contested — a series, not a claim*), a Dalton **opening-type** chip (*descriptive session read, not a signal*), a **key-levels strip** (registry levels + live Initial Balance, footprint overlay), a **basis/funding** intraday mini-chart, and a **⌘K command palette** with workspace presets. Keyless WS (Bybit primary + Binance depth + OKX + Coinbase tape). Design + honesty rails: `DESIGN-orderflow-terminal.md`.
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

# 7) orderflow terminal (same server → /terminal.html) + optional tick collector
python3 -m pip install -r requirements-collector.txt   # duckdb + websockets (opt-in)
make collector                                          # records trades/liqs/depth/funding/OI → data/ticks.duckdb
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

The honest result, from `compare.py` on the 2018→ daily history (N = 5 strategies). Since
2026-07-13 the deflation uses each strategy's **own-Sharpe variance** — the finite-sample
Lo (2002) / Mertens (2002) estimator `V_i = (1 − skew·SR + (kurt−1)/4·SR²)/(n−1)`, the exact
quantity already inside the PSR denominator (convention **B2**, `RESEARCH-dsr-convention.md`).
Each strategy's DSR now depends only on its *own* returns — a peer's Sharpe cannot move it — while
The **hierarchical-Bayes shrinkage** block (Efron–Morris empirical Bayes, correlation-aware DerSimonian–Laird) then gives the Bayesian view of the same winner's curse: each strategy's posterior (*shrunk*) Sharpe and `P(skill>0)` — the frequentist and Bayesian machines agree ([RESEARCH-hierarchical-bayes-runlog.md](RESEARCH-hierarchical-bayes-runlog.md)). Beside them the leaderboard prints a **False-strategy diagnostics** block (Bailey–López de
Prado 2014): the explicit **Sharpe hurdle** a strategy must clear to escape the luck-of-N
null, the **effective number of independent trials** `N_eff` (the board's eight rows are
only ~3 independent bets — the `tsmom` cluster is corr ≈ 1.00), and `P(top strategy is a
false positive)`. It is diagnostic-only; see [RESEARCH-false-strategy-runlog.md](RESEARCH-false-strategy-runlog.md).

**PBO and MinBTL carry the cross-strategy selection honesty**. (The older shared empirical
cross-trial variance is retired for the leaderboard; `scripts/dsr_ab.py` still shows it as
column A.)

```
strategy            OOS CAGR   OOS SR    IS SR   OOS DSR  OOS MaxDD  beats B&H
tsmom                 12.64%     1.01     1.29     0.95     -21.39%        yes
buy_and_hold          34.99%     0.80     0.75     0.82     -76.04%        (baseline)
tsmom_ls              12.59%     0.76     1.08     0.81     -24.68%        no
ma_trend_filter       25.72%     0.71     0.91     0.75     -65.94%        no
pairs_coint           -1.43%    -0.59    -0.35     0.00     -10.94%        no
```

Read it straight: **every strategy's Sharpe decays in-sample → out-of-sample** (tsmom 1.29 → 1.01,
ma_trend 0.91 → 0.71; pairs is now the true delta-neutral spread and loses outright, IS −0.35 →
OOS −0.59). On this long window `tsmom` tops the board and does beat
buy-and-hold — **but nothing clears OOS deflated Sharpe 0.95**, the threshold for "distinguishable
from luck after deflating for the number of strategies tried." Even the winner is not significant:
its DSR prints **0.95 but is precisely 0.9451 ≤ 0.95** — no `*`. The other three trend/reversion strategies do not beat
buy-and-hold net of cost out-of-sample, and the one that wins on return (buy-and-hold, +34% CAGR)
does it with a −77% drawdown. That is the point, not a disappointment: most of what survives crypto
OOS is risk-management, not alpha.

**`pairs_coint` is now a genuine delta-neutral spread — charged AND booked on both legs (audit M2 +
M9).** A pairs trade holds a BTC leg *and* a `beta`-scaled ETH hedge that rebalances every bar. M2
made the **cost** two-leg: the base is the total-variation turnover of **both** legs
(`|Δ position|` + `|Δ(beta·state)|`), roughly `(1+|beta|)` ≈ 2× the old single-leg charge. M9 makes
the **P&L** two-leg: the BTC-leg state earns the *spread* return `state·(BTC_ret − beta·ETH_ret)`
(the hedge ratio 1-bar-lagged, same no-look-ahead shift as the state), not a bare directional BTC
move — so when ETH outruns BTC the hedge loses even as BTC rises. With cost and P&L both two-leg the
trade is delta-neutral for the first time, and it loses on its own merits: OOS CAGR `−0.12% →
−1.43%`, OOS SR `0.01 → −0.59`, OOS DSR `0.12 → 0.00` (research window `0.04 → 0.00`) — it never
moved *up* toward the 0.95 bar. Under the current **B2** deflation the pairs collapse leaves every
other row's DSR **untouched** — each strategy's DSR is a function of its own returns alone (this
decoupling is exactly why B2 was adopted, 2026-07-13; before it, the shared cross-trial `V` coupled
the whole board and a pairs move re-scaled every DSR). Rank order is unchanged either way.

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
  - `compare.py` (N = 5, 2018→) prints **PBO ≈ 0.64**.
  - `compare.py --research` (N = 8, the same window + the research candidates) prints **≈ 0.60**;
    inside it, PBO over just the 5 board strategies is again 0.64.
  - the dashboard's shorter default window (N = 7) shows **≈ 0.83**.

  All three say the same thing — the cross-sectional ranking is not robust — but only the value from
  *your* command is the one to cite. Run it and you will see it.

- **MinBTL — Minimum Backtest Length** (Bailey et al. 2014). Given N configurations tried, how many
  years of history do you need before an in-sample Sharpe of ~1 is expected even from pure noise?
  `compare.py` prints **2.70 yr for N = 5** (2.85 yr for N = 8 under `--research`) against 8.5 yr of
  data — so here the history is long enough. On shorter windows it correctly flags the backtest as
  under-powered. Every added strategy raises the required MinBTL and lowers everyone's deflated
  Sharpe — which is the explicit cost of putting another strategy on the board.

- **CPCV — Combinatorial Purged Cross-Validation.** Instead of one walk-forward path, score the
  strategy over many purged block combinations and report the *distribution* of OOS Sharpe.
  `run_backtest.py --strategy tsmom --walk --start 2018-01-01` prints **median 1.14 [p25 0.75,
  p75 1.51] over 15 paths** — wide and sign-flipping (on a recent default window it is even negative).
  A single equity curve hides that; the dispersion says the result is regime-dependent, not a stable
  edge.

### A worked example: the Part B rejection log

The harness is only credible if it actually rejects things. Three candidates were pre-registered
(hypothesis + falsifiable kill criterion *before* running) and judged purely on OOS DSR / PBO. None
were promoted — the full log is [RESEARCH-partB-runlog.md](RESEARCH-partB-runlog.md):

- **B1, tsmom × vol-target** — *killed as a literal duplicate.* A vol-target overlay on directional
  momentum came back **correlation 1.00** with the board's already-vol-scaled `tsmom` (byte-identical
  OOS rows, DSR 0.91). It improved on the *raw directional* baseline (0.85 → 0.91) but that strategy
  already exists; a second copy only burns MinBTL headroom.
- **B2, OU-reversion pairs** — *killed as "a model, not an edge."* `pairs_ou` changes exactly one
  thing versus the fixed-z `pairs_coint`: it normalizes the spread by the OU-model stationary σ
  (`features.ou_sigma_eq`) instead of the empirical rolling std. OOS DSR **0.03 vs 0.00** — a Δ of
  only +0.03, under the pre-registered +0.05 promotion bar (and PBO does not improve),
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
mirrors the Python engine's formulas and agrees with it to ~1e-7 (the inverse-normal approximation,
not bit-identical; `scripts/check_parity.py` pins 63 shared fields, including unsaturated deflated-
Sharpe probes, the two-leg pairs cost base + delta-neutral spread P&L, the options analytics
(Black-76 greeks, max-pain, gamma concentration), and the CPCV multi-path dispersion); the engine is the
source of truth. The dashboard's headline is a **walk-forward folds-DSR** (V = empirical variance
of the per-fold OOS Sharpes) and the JS mirror matches it field-for-field — that surface is
unchanged and stays parity-pinned. Note this is a **different** V from `compare.py`'s
cross-strategy leaderboard, which since 2026-07-13 uses each strategy's own-Sharpe variance
(convention B2); the two surfaces were always decoupled. PSR internals are one binding convention
across both engines (per-period SR, `bias=False` non-excess kurtosis; N = 1 is labeled
`PSR (single trial — no deflation)`) — see the M6 entry and the 2026-07-13 M6-AMENDMENT in
[AUDIT_LOG.md](AUDIT_LOG.md).

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
