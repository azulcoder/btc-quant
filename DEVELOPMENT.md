# btc-quant — developer guide

How to extend this terminal **without breaking the thing that makes it credible**: the honesty
rails and the Python↔JS parity. Read this after [README.md](README.md) (what it is + methodology)
and alongside [DESIGN.md](DESIGN.md) (module signatures) and [RESEARCH.md](RESEARCH.md) (the cited
strategy rationale). The two run-logs — [RESEARCH-partB-runlog.md](RESEARCH-partB-runlog.md) and
[RESEARCH-options-runlog.md](RESEARCH-options-runlog.md) — are worked examples of the
pre-registration / rejection discipline.

## 1. Architecture & the one rule

```
btcquant/           Python engine — the SOURCE OF TRUTH (pure, typed, pytest-covered)
  data.py           fetch + cache: get_ohlcv / get_funding / get_option_chain / get_dvol / get_onchain (public, no keys)
  features.py       indicators + option-surface + greeks + regime gate (hurst/variance_ratio/adx) + yang_zhang_vol (pure)
  backtest.py       run (shift-by-1, cost-on-turnover) · walk_forward (+ oos_positions) · cpcv
  risk.py           sharpe…calmar, VaR/CVaR, kelly, probabilistic/deflated Sharpe, min_backtest_length, PBO,
                    trade_ledger + expectancy_report (Tharp R-multiples + SQN/profit-factor/MAE; vol-notional R, no hard stop)
  ic.py             forward Information Coefficient (lead-time validation): does signal_t lead return_{t+k}?
                    rank IC + overlap-corrected significance + IC-IR + regime-conditional IC (eval layer, OOS)
  strategies.py     position builders (df -> Series in [-1,1]); each cites edge + caveat;
                    sizing wrappers vol_target + percent_risk_size (ATR; Python-harness-only);
                    research-only candidates donchian_breakout / vwap_reversion / fixed_r_exit / random_entry
                    (NOT on the board — all deflated, logged in RESEARCH-tharp-runlog.md)
  report.py         matplotlib tearsheet + dashboard JSON
  tracking.py       OPTIONAL MLflow run-logging (guarded import; no-ops without requirements-dev.txt)
scripts/            CLIs: compare.py (OOS leaderboard, --research) · run_backtest.py (--walk, --track) · scan.py · fetch_data.py
  check_parity.py   JS↔Python mirror parity harness (+ _parity_eval.cjs); CI-enforced
tests/              pytest — no-lookahead, vectorized==reference, parity, the honesty-rail teeth
dashboard/          static terminal, no build step:
  quant.js          REQUIREABLE JS MIRROR of the engine's math (Q.*) — parity-checked vs Python
  app.js            data fetch (client-side, public feeds) + DOM render; calls Q.* for all math
  charts.js         dependency-free SVG charts (no logic, just drawing)
  index.html        panel shell + tab regions ;  styles.css  design tokens + components
data/               cached CSV/JSON (gitignored)
```

**The one rule that keeps the project honest:** **every shared formula exists in two places — Python
(`btcquant/`, the source of truth, tested) and JS (`dashboard/quant.js`, the live mirror) — and they
must agree.** The dashboard never computes math in `app.js`; it calls `Q.*` from `quant.js`, which
mirrors a tested Python function. When you add or change a formula, you change it in *both* and prove
they still match (§4). `charts.js` draws — it has no analytics. `app.js` fetches + renders + wires
panels; its only "math" is reading values out of `Q.*` and `bt.stats`.

## 2. Honesty rails (non-negotiable — these ARE the product)

1. **No look-ahead.** Signals are target weights; `backtest.run` shifts them by one bar so a signal at
   `t` trades `t→t+1`. `tests/test_core.py` asserts it (and `backtest._assert_no_lookahead`).
2. **Rank out-of-sample.** The leaderboard ranks by **walk-forward OOS deflated Sharpe**, never the
   in-sample fit. The IS→OOS Sharpe drop is shown as the overfitting tell.
3. **Report selection overfit.** PBO (CSCV), MinBTL, CPCV dispersion accompany any ranking — surfaced,
   not hidden.
4. **One number across the page.** A metric shown in two places must be the *same computed value*
   (the DSR-unification fix: the panel/KPI hero read the leaderboard row, not a parallel recompute).
   Captions are **fully derived** — every number in prose comes from the value that drives its chart;
   the only literals allowed are methodology constants and cited literature figures.
5. **Never fabricate / never silently go stale.** A dead feed degrades the panel to an explicit
   "unavailable" message + a stale/error chip (the feed-watchdog). It never shows old data as live or
   invents values.
6. **No keys, no orders, no authenticated endpoints.** Public data only.
7. **Commits carry NO AI attribution** (no "Co-Authored-By", no "Generated with…"). Repo rule.

## 3. How to extend

### Add a strategy
1. `btcquant/strategies.py` — a `df -> pd.Series` of target positions in `[-1,1]`/`[0,1]`, with a
   docstring stating edge, evidence tag, citation, and honest caveat. Reuse `features.py` primitives.
2. `tests/test_core.py` — assert it stays in the unit band + any invariant (e.g. no-lookahead /
   prefix-stability for stateful signals like `pairs_*`).
3. `scripts/compare.py` — add a builder in `_make_positions_fn` and the name to `SPOT_STRATS`
   (public board) **only if it earns a slot** — judge it first under `--research` (`RESEARCH_STRATS`),
   pre-registering a hypothesis + kill criterion in a run-log. Adding a strategy raises N and lowers
   every DSR + burns MinBTL headroom, so losers stay off the board (see Part B).
4. If it ships to the dashboard: mirror the builder in `quant.js` (a `sig*` function), add it to the
   `STRATEGIES` registry in `app.js`, and **re-run the JS↔Python parity probe** (§4).

### Add a dashboard panel
1. Put the math in `btcquant/features.py` (or `risk.py`) **with pytest** — source of truth.
2. Mirror it in `quant.js`, export it on the `Quant` object, and **parity-check** vs Python (§4).
3. `app.js` — a `render*()` that reads `Q.*`, draws via `C.*` (charts.js), and writes a
   **fully-derived** caption. Wire it into the panel's loader, the tab→panel map, and the
   feed-watchdog registry (so it degrades on feed loss).
4. `index.html` — the panel markup with the right `DESCRIPTIVE`/`SIGNAL` tag + a §-style caveat
   matching the existing options/perp panels. `styles.css` — reuse the tokens; give any stats grid an
   **explicit column count that divides its cell count** (no ragged auto-fit half-rows).

**Live-descriptive exception (no Python mirror).** A few panels read the live WS trade tape and have
**no backtest** because there is no historical tick/TPO store — so they have no Python source-of-truth
and no parity obligation: the **CVD / aggressor-flow** panel (`accumCvd`/`renderCvd`, `panel-cvd`) and
the **developing volume profile — POC / value area** panel (`accumProfile`/`renderProfile`,
`panel-profile`, the live form of Market Profile). Both live in the **Live** tab, carry a
`DESCRIPTIVE` tag with a *NOT a signal · NOT backtestable* caveat, and degrade with the WS feed via the
shared `onStatus` handler. Keep new live-only reads to this same pattern; never let one imply an edge.

### Add / change a shared formula
Change it in **Python (+test)** and **quant.js**, then prove parity (§4). Cite the math + conventions
in the docstring so a quant can audit.

## 4. Verification suite (run before every commit)

```bash
python3 -m pytest -q                      # 190 tests — the honesty-rail teeth (incl. JS↔Python parity + collector normalizers)
node --check dashboard/app.js             # JS syntax (also quant.js, charts.js, livewire.js, terminal-*.js)
node dashboard/app.js --check             # ppy guard: ppy()=365 (1d)/8760 (1h); no literal-365 at an annualization site
python3 scripts/check_parity.py           # JS↔Python mirror parity (68 shared fields; the one rule)
node scripts/check_terminal.cjs           # orderflow terminal smoke: adapters+stores replayed over REAL captured WS frames (fixtures_ws.json)
make verify-browser                       # L1: terminal.html?replay=1 in headless Chromium — render + zero-console-error gate + screenshots (needs playwright)
make verify-wire                          # L2: ~45s live-wire invariants through the PRODUCTION adapters (exit 2 = offline, not a bug)
make check-ticks                          # L3: tick-store QA report card (gaps/dupes/cadence/coherence — reported, never filled)
# CSS brace balance:
awk '{o+=gsub(/{/,"{");c+=gsub(/}/,"}")}END{print (o==c)?"balanced":"UNBALANCED"}' dashboard/styles.css
python3 scripts/compare.py                # public OOS leaderboard (defaults to --start 2018-01-01)
python3 scripts/compare.py --research     # + pre-registered candidate verdicts
make test        # convenience targets: also  make compare / backtest / scan / fetch / dash / collector / install
```

**CI** ([.github/workflows/ci.yml](.github/workflows/ci.yml)) runs the build gates on every push/PR:
`pytest`, `node --check` ×N, **`node dashboard/app.js --check`** (the annualization guard — closes
audit M7; a non-zero exit fails the build), `python scripts/check_parity.py`, and
`node scripts/check_terminal.cjs` (network-free terminal smoke). A diverging mirror or a resurfaced
literal-365 fails the build.

### Reproducibility tooling (OPTIONAL — `requirements-dev.txt`, not the core)

Off by default; the engine never hard-depends on it. `pip install -r requirements-dev.txt` to enable.

- **MLflow run-tracking.** `run_backtest.py --track` logs the run's params (strategy, costs, folds, and
  **`n_trials`** — the figure that deflates the Sharpe) + (OOS) metrics + the JSON/PNG artifacts via
  [btcquant/tracking.py](btcquant/tracking.py). Store defaults to local `sqlite:///mlflow.db` (MLflow 3.x
  retired the `file:./mlruns` backend; SQLite works on 2.x too); override with `MLFLOW_TRACKING_URI`.
  Browse with `mlflow ui --backend-store-uri sqlite:///mlflow.db`. Without MLflow installed, `--track`
  prints a hint and no-ops — it never fabricates or fails the run. Only finite scalars are logged.
- **DVC pipeline.** [dvc.yaml](dvc.yaml) defines a `backtest` stage (deps = `btcquant/` + the script,
  out = the dashboard JSON); `dvc repro` re-runs only on change and records output hashes in `dvc.lock`;
  `dvc dag` shows the graph. **Honest limit:** the stage fetches live OHLCV (not in `deps`), so it pins the
  *pipeline + code*, not yet the market data — `dvc add` a point-in-time OHLCV snapshot is the next step.
- Local stores (`mlflow.db`, `mlartifacts/`, `reports/*.json`) are git-ignored. **Prefect orchestration is
  deferred** — on-demand `dvc repro` + `make` suffice for a solo researcher until a schedule is actually needed.

**Headless self-validation** (Playwright, `python3 -m playwright install chromium`): serve
`dashboard/` and drive it — assert panels render or honestly degrade across all tabs, screenshot for
review. This caught real bugs (inverted tape coloring, the clipped TradingView embed, the null-OOS
fallback). Pattern: serve on a port → `page.goto` → wait for `#leaderboard-body tr` / `#smile-expiry
option` → click each `button[data-tab="…"]` → assert + screenshot.

**JS↔Python parity check** (the discipline behind "the mirror agrees"): [scripts/check_parity.py](scripts/check_parity.py)
builds a fixed deterministic fixture, computes 41 shared fields in Python (`btcquant.*`) and in Node
(`scripts/_parity_eval.cjs` → `require('dashboard/quant.js')`), and diffs them within the §5 tolerances.
It is committed, wrapped by `tests/test_parity_mirror.py` (so `pytest` enforces it; skipped when Node is
absent), and run as its own CI step. To extend the mirror, add the formula to both sides **and** a row to
the harness. (This check earned its keep immediately: it caught a real Sortino divergence — the JS mirror
was dividing the downside variance by the downside count instead of the full sample.)

## 5. Gotchas & numerical tolerances (hard-won — do not relitigate)

- **JS↔Python is NOT bit-for-bit; it agrees to a known tolerance.** PBO is exact (`0.0`); MinBTL & the
  Deflated/Probabilistic Sharpe agree to **~1e-7** (JS `normPpf` is Acklam's rational approx vs scipy
  `norm.ppf`; the M6 unsaturated pins `dsr_n1`/`dsr_mid` and the walk-forward probes
  `wf_oosSharpe`/`wf_varTrialsSr`/`wf_deflatedSharpe`/`wf_varFallback` sit at ~6e-8 worst); Black-76
  **gamma/vega are exact** (they use `normPdf`/`exp`) while **delta agrees to ~7e-8** (JS `erf`
  approx); a full DSR computed from independently-estimated **skew/kurtosis agrees to ~1e-5** (JS
  moment helpers vs scipy `bias=False`). Overall parity worst today: **~1.6e-7** (on `rsi`, 63
  fields — +5 M2 two-leg pairs cost, +9 M4 CPCV dispersion, +2 M9 delta-neutral pairs P&L, +6 M8
  options analytics [max_pain + gamma_concentration], the last 6 bit-exact |Δ|=0). State the *real*
  tolerance; don't claim bit-for-bit.
- **DSR trial-variance V by surface — there are THREE and they are NOT interchangeable (M6 +
  M6-AMENDMENT 2026-07-13, binding — do not relitigate):**
  1. **`compare.py` cross-strategy leaderboard** — since 2026-07-13 V is **per-strategy own-Sharpe
     variance** `risk.sharpe_estimator_variance(sr, n, skew, kurt) = (1 − skew·SR + (kurt−1)/4·SR²)
     /(n−1)` (Lo 2002 / Mertens 2002, convention **B2**). Each strategy's DSR depends only on its
     own returns — **decoupled**. The old shared cross-strategy `V = _empirical_var_sr(SRs)`
     (convention A, which coupled the whole board) was **retired here on 2026-07-13**; the function
     is kept ONLY because `scripts/dsr_ab.py` column A (a historical reference) delegates to it. Do
     NOT re-wire `_empirical_var_sr` into the leaderboard.
  2. **`walk_forward` folds-DSR** (`OOS DSR (folds)`, the research kill-gate) — V = EMPIRICAL ddof=1
     variance of the per-FOLD OOS SRs, N = n_splits. **Unchanged**; this is a different surface from
     the leaderboard and always was.
  3. **Dashboard headline DSR** (`quant.js walkForward`) — the per-fold V mirror, the 63
     parity-pinned fields. **Unchanged; no JS moved in the B2 switch, no parity re-pin.**
  `dsr_ab.py` is the A/B/B2 side-by-side comparison tool. N = 1 ≡ PSR everywhere and is LABELED
  `'PSR (single trial — no deflation)'` (`dsr_is_psr`/`dsrIsPsr`). Never reintroduce a silent
  `1/n`-only variance (nor a `max(V, 1/n)` floor); the `1/n` null is a *flagged fallback*
  (`var_fallback`) with a printed caveat, legal only when the trial variance genuinely can't be
  computed (e.g. B2 with `n<2`). Cautionary tale: the JS mirror once fed N=1 through
  `normPpf(1 − 1/1) = −Inf` and **returned DSR = 1.0 identically for ~8 months** — any strategy,
  even a losing one, scored 100% significant at N=1 — and the then-saturated parity pins (both
  tails ≈ 0) couldn't see it. Keep the unsaturated pins; full post-mortem in AUDIT_LOG.md (M6 +
  M6-AMENDMENT).
- **Deribit ticker endpoint is `public/ticker`, NOT `get_ticker`** (the latter returns "Method not
  found"). `get_book_summary_by_currency` has **no greeks** and **`mark_iv` only** (no bid/ask IV) —
  hence client-side Black-76 (validated against `public/ticker` greeks).
- **`mark_iv` is in percent** — divide by 100 before any vol formula (`data.get_option_chain` already
  stores the decimal `iv`; the dashboard does `markIv/100`). Forgetting this is a silent 100× bug.
- **Annualization (`ppy`) must thread through every Sharpe/vol/CAGR** — 365 (1d) / 8760 (1h). A literal
  `365` at an annualization site is a bug; `node dashboard/app.js --check` guards it.
- **Walk-forward: the dashboard SLICES precomputed positions per fold; `compare.py` REFITS per fold.**
  Same strategy, slightly different OOS because refit re-warms-up each block. They are not expected to
  match to 1e-8 end-to-end (this is a methodology choice, not a bug; the dashboard points to the Python
  engine for the rigorous run).
- **Coinbase `market_trades.side` is the MAKER side, not the aggressor** — `SELL` prints on an up-tick.
  The tape coloring inverts if you read it as the aggressor. (Bybit `publicTrade.S` is the TAKER side —
  use as-is; the two conventions are asserted against real captured frames in `scripts/check_terminal.cjs`.)
- **Binance Futures WS is topic-filtered on this network** (verified 2026-07-03): `depth20@100ms`
  flows while `aggTrade`/`markPrice`/`ticker` on the *same socket, same subscribe* deliver zero
  frames (sub-ack only); REST `fapi/v1/*` works fully. Hence Bybit-primary in the terminal/collector
  and Binance-for-depth+REST — do not "fix" adapters by re-adding Binance trade streams without
  re-testing the wire first (capture script pattern: DESIGN-orderflow-terminal.md §2).
- **OKX SWAP sizes are in CONTRACTS, not coin** — BTC-USDT-SWAP `ctVal` = **0.01 BTC** (verified via
  `/api/v5/public/instruments`, pinned in `fixtures_ws.json`). A trades `sz` of `"200"` is 2 BTC, a
  book level of `"883.58"` is ~8.84 BTC. Forgetting the ×ctVal is a silent 100× bug — same trap
  family as `mark_iv` percent. (Terminal book feeds: Bybit `orderbook.200`, OKX `books`; the
  adapters scale at the wire, stores only ever see coin units.)
- **Bybit v5 `tickers` sends a snapshot then PARTIAL deltas** — delta frames omit unchanged fields
  (a delta with no `markPrice` does not mean mark went away). Merge against the last snapshot or
  funding/OI silently vanish. Same stream carries OI — there is no separate Bybit OI poll.
- **Bybit `allLiquidation` prints the forced order's side, not the position's**: printed `Buy` = a
  SHORT was liquidated (forced buy-back), `Sell` = a LONG was. Normalizers flip it to the position
  side; fixtures carry real frames of both directions.
- **`max_pain`/gamma-concentration are positioning/structure, never forecasts**; **signed dealer GEX /
  flip levels are rejected** (dealer sign unknowable from keyless data — see options run-log).
- **TradingView embed** writes inline px heights on the iframe + container → must be overridden with
  CSS `!important` on `.tv-embed` (don't assume; read the rendered DOM).
- **Stats grids:** explicit column counts that divide the cell count (auto-fit wraps to a ragged
  half-row of empty cells at wide widths).
- **`bfill()` on a later-listed leg is a LOOK-BACK LEAK — ffill only (audit M1).** ETH listed after
  BTC; the old pairs alignment `eth.reindex(btc.index).ffill().bfill()` back-stamped ETH's first
  observed price into the pre-listing region, fabricating a spread there. Align with **ffill only** so
  the leading pre-ETH bars stay `NaN` and the index-intersection drops them. Same trap the M1-carry
  fix closed on the funding clock. Never add a `.bfill()` to a price/return series that feeds a signal.
- **Pairs cost is TWO legs (audit M2).** A pairs trade holds a BTC leg AND a `beta`-scaled ETH hedge
  that rebalances every bar. Charge the total-variation turnover of BOTH: `|Δ position|` (BTC) +
  `|Δ(beta·state)|` (ETH), via `backtest.run(..., extra_cost_turnover=)` (default `None` ⇒
  byte-identical single-leg cost for every non-pairs strategy). `strategies._hedge_beta` is the ONE
  source of `beta` (no duplicated OLS); `pairs_legs → (state, beta)` exposes it. `extra_cost_turnover`
  aligns a **`pd.Series` by index**, a **raw array by position (exact length)**, and **raises loudly**
  on a length mismatch — never pass a RangeIndex array against a DatetimeIndex (it would silently zero
  the leg).
- **Pairs P&L is DELTA-NEUTRAL — the BTC-leg state earns the SPREAD, not a bare BTC move (audit M9,
  completes M2).** Book `gross = traded_posₜ · (BTC_retₜ − hedge_returnₜ)` where
  `hedge_returnₜ = beta_{t−1} · ETH_retₜ` — the SAME single-source `beta` the cost is charged on,
  **1-bar-lagged** (`beta.shift(1)`, the identical no-look-ahead shift as the state), so the state
  earns `BTC_ret − beta·ETH_ret ≈ Δlog-spread`. Threaded via `backtest.run(..., hedge_return=)` /
  `walk_forward(..., hedge_return=)` and the `quant.js backtest`/`walkForward` `hedgeReturn` mirror.
  **Default `None` ⇒ `gross = traded_pos · BTC_ret` EXACTLY as before ⇒ byte-identical for every
  non-pairs strategy** (the subtraction path is never entered). `hedge_return` aligns exactly like
  `extra_cost_turnover` (Series → reindex+`fillna(0)`; array → length-checked), so a warm-up /
  degenerate-beta NaN bar nets a **zero** hedge, never a NaN into P&L. M2 (cost) + M9 (P&L) together
  make the pairs trade coherently delta-neutral; both pairs DSRs went to 0.00 (still KILL, off the
  board). Wiring lives in `compare.py._pairs_hedge_return` + `app.js`; `pairs_legs` is unchanged (the
  ONE source of `(state, beta)`).
- **Purge/embargo are default-OFF machinery for k-step labels (audit M4).** `walk_forward`/`cpcv`
  take `purge`/`embargo` as index masks on the per-fold **IN-SAMPLE** return series ONLY — **OOS is
  invariant**, and `purge=embargo=0` reproduces the pre-M4 engine **bit-for-bit** (pinned to golden
  constants). Today's strategies are all 1-bar causal labels generated once then sliced, so no train
  label reaches a test block and purge/embargo are not needed for today's numbers; they ship dormant so
  the harness is correct-by-construction for the k-step-label order-flow signals arriving when MinBTL
  clears. `cpcv`'s legacy float trim is now `embargo_pct` (don't confuse with the int `embargo`).
  `backtest.LockBox` is the evaluate-once ledger (`assert_scored_once` catches a double-peek).
- **Forward-IC significance is Newey-West / HAC at lag k-1 (audit M5), NOT a fixed band.** A k-bar
  forward IC is scored on overlapping windows (consecutive pairs share k-1 forward bars ⇒ MA(k-1)
  autocorrelation), so `ic.ic_significance` fits `fwd_rank ~ const + signal_rank` by OLS with
  `cov_type="HAC"`, `maxlags=max(k-1,0)`. The rank-slope IS the Spearman IC; its HAC t/p test
  `H0: IC=0` exactly. The old crude `|IC| > 1.96·√(k/n)` band is gone. HAC makes significance strictly
  HARDER than a naive OLS SE (wider SE, smaller |t|), so a NONE-significant run-log verdict can only
  strengthen. `compare.py` prints `NW t(k=3)`/`NW p(k=3)` (overlap-corrected) beside the retained
  `IC-IR t(k=3)` (non-overlapping block). IC is an eval layer — no quant.js mirror, not in parity.

## 6. Roadmap / deferred (pre-registered — do NOT start without an explicit greenlight)

- **Part B strategies B1/B2/B3** — already evaluated and **rejected/logged** (B1 tsmom×vol-target = a
  literal duplicate of the board's vol-scaled tsmom, corr 1.00; B2 OU-pairs = "model, not edge"; B3
  carry = OOS-insufficient). Re-judge only through the harness on OOS DSR / PBO.
- ~~DSR-convention unification~~ — **done (M6, 2026-07-11)**: one binding convention (C1–C5) across
  every Python and JS DSR call site — N by surface (leaderboard = strategies, walk-forward = folds),
  N=1 labeled as PSR, flagged 1/n fallback only — with unsaturated parity pins and hand-pinned tests.
  **Amended 2026-07-13 (M6-AMENDMENT):** azul switched the `compare.py` cross-strategy leaderboard V
  from the shared empirical cross-trial variance (A) to the **per-strategy own-Sharpe variance**
  (B2, `risk.sharpe_estimator_variance`) — decoupling each DSR from its peers' returns. Python-only;
  the walk-forward folds-DSR and the dashboard parity surface were untouched. See AUDIT_LOG.md
  (M6 + M6-AMENDMENT) and the §5 gotcha.
- ~~M1-pairs bfill leak · M2 two-leg pairs cost · M4 purge/embargo/lockbox · M5 IC HAC~~ — **done
  (2026-07-12)**: M1-pairs dropped the `.bfill()` (ffill-only, no back-stamped pre-listing price);
  M2 charges the ETH hedge leg via `extra_cost_turnover` (single-source `_hedge_beta`/`pairs_legs`,
  +5 parity fields, pairs DSR moved down, cost-only — P&L completed in M9); M4 added default-OFF,
  byte-identical purge/embargo + `LockBox` (+9 CPCV parity fields); M5 replaced the crude IC band with
  Newey-West HAC at lag k-1 (`compare.py` prints NW t/p — board strategies still show NO significant
  OOS lead, now stronger). See AUDIT_LOG.md (2026-07-12) and the four §5 gotchas.
- ~~M9 delta-neutral pairs P&L · M8 options parity · M7 annualization guard in CI~~ — **done
  (2026-07-12)**: M9 books the pairs SPREAD return `traded_pos · (BTC_ret − beta_{t−1}·ETH_ret)` via
  `backtest.run(..., hedge_return=)` (default `None` ⇒ non-pairs byte-identical), +2 parity fields —
  both pairs DSRs collapsed to 0.00 (still KILL, off the board), non-pairs P&L byte-identical (only
  the shared-V DSR shifted then — the M6 coupling, since retired by the 2026-07-13 B2 switch); M8
  pinned `max_pain` + `gamma_concentration` across the
  mirror (+6 parity fields, bit-exact) so options parity now covers the greeks AND the two chain
  analytics; M7 wired `node dashboard/app.js --check` into CI (the annualization guard bites on any
  resurfaced literal-365). **All lettered audit findings (H1, M1–M9) are now CLOSED** — only Low/Info
  remain. See AUDIT_LOG.md (2026-07-12) and the §5 gotchas.
- **True bit-for-bit parity** — swap JS `normPpf` (Acklam) / `erf` for higher-order approximations to
  close the ~1e-8 / ~7e-8 gaps, if ever wanted.
- ~~Commit the parity probes under `scripts/`/`tests/` for CI~~ — **done**: `scripts/check_parity.py` +
  `tests/test_parity_mirror.py` + `.github/workflows/ci.yml` (see §4).
- **Visual pass** — the institutional redesign is a first pass; type-scale/color/per-panel refinements
  may iterate.
- **Orderflow terminal phases O-2…O-5** (DESIGN-orderflow-terminal.md §5) — orderbook/liquidation
  heatmaps (estimates LABELED), TPO/market profile, bar replay, funding-arb table, screeners, whale
  tracking, options-widget extensions (unsigned GEX only), alerts, journal. All still live-descriptive.
- **Order-flow research families (tick CVD / liquidations / OI / funding accrual)** — the collector
  makes these **time-gated, not granted** (DESIGN §6): OOS candidacy requires accumulated history ≥
  MinBTL *and* a pre-registered hypothesis + kill criterion through the standard harness. No exceptions
  because "we now have the data".

## 7. Where things are documented

| Doc | Audience / contents |
|---|---|
| [README.md](README.md) | Users — what it is, quick start, **Methodology** (OOS, PBO/MinBTL/CPCV, rejection log) |
| [DESIGN.md](DESIGN.md) | Module signatures / contracts + non-negotiables |
| [RESEARCH.md](RESEARCH.md) | The cited strategy-library design brief (per-strategy edge/caveat) |
| [RESEARCH-partB-runlog.md](RESEARCH-partB-runlog.md) | Worked strategy-rejection log (B1/B2/B3) |
| [RESEARCH-options-runlog.md](RESEARCH-options-runlog.md) | Options panels: pre-registration + Deribit greeks validation + signed-GEX rejection |
| [RESEARCH-tharp-runlog.md](RESEARCH-tharp-runlog.md) | Trading-books eval/risk layer: expectancy/R-multiple (vol-notional R) + SQN/PF/MAE, percent-risk sizing sweep, Tier-B candidate sweep (donchian/vwap-reversion/fixed-R — all KILL), live CVD + volume-profile notes |
| [RESEARCH-ic-runlog.md](RESEARCH-ic-runlog.md) | Lead-time Information Coefficient: forward IC of OOS signals (rank, overlap-corrected) — board strategies show NO significant forward IC; their edge is trend/vol-capture, not bar-to-bar lead |
| [RESEARCH-reversion-runlog.md](RESEARCH-reversion-runlog.md) | Regime-gated mean reversion (Hurst/VR/ADX gate + `mean_reversion`): pre-registered gated-vs-ungated A/B on 1d+1h — gate cuts drawdown but adds no OOS alpha; hypothesis FALSIFIED, board unchanged |
| [DESIGN-orderflow-terminal.md](DESIGN-orderflow-terminal.md) | Orderflow terminal + tick collector: CryExc-inspired feature map, honesty rails (live-descriptive only, labeled estimates, unsigned GEX), empirical data-source matrix (real captured frames), module contracts, phase plan O-0…O-5, research time-gating |
| [AUDIT.md](AUDIT.md) / [AUDIT_LOG.md](AUDIT_LOG.md) | Repeatable code/stat audit spec + the change-log of verified fixes (H1 funding P&L fixed; remaining findings tracked) |
| **DEVELOPMENT.md** (this) | Contributors — architecture, the parity rule, extend-recipes, verification, gotchas, roadmap |
| [DISCLAIMER.md](DISCLAIMER.md) | Research-only / not financial advice |
