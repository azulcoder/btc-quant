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
python3 -m pytest -q                      # 38 tests — the honesty-rail teeth (incl. JS↔Python parity)
node --check dashboard/app.js             # JS syntax (also quant.js, charts.js)
node dashboard/app.js --check             # ppy guard: ppy()=365 (1d)/8760 (1h); no literal-365 at an annualization site
python3 scripts/check_parity.py           # JS↔Python mirror parity (35 shared formulas; the one rule)
# CSS brace balance:
awk '{o+=gsub(/{/,"{");c+=gsub(/}/,"}")}END{print (o==c)?"balanced":"UNBALANCED"}' dashboard/styles.css
python3 scripts/compare.py                # public OOS leaderboard (defaults to --start 2018-01-01)
python3 scripts/compare.py --research     # + pre-registered candidate verdicts
make test        # convenience targets: also  make compare / backtest / scan / fetch / dash / install
```

**CI** ([.github/workflows/ci.yml](.github/workflows/ci.yml)) runs the first three on every push/PR:
`pytest`, `node --check` ×3, and `python scripts/check_parity.py`. A diverging mirror fails the build.

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
builds a fixed deterministic fixture, computes 35 shared formulas in Python (`btcquant.*`) and in Node
(`scripts/_parity_eval.cjs` → `require('dashboard/quant.js')`), and diffs them within the §5 tolerances.
It is committed, wrapped by `tests/test_parity_mirror.py` (so `pytest` enforces it; skipped when Node is
absent), and run as its own CI step. To extend the mirror, add the formula to both sides **and** a row to
the harness. (This check earned its keep immediately: it caught a real Sortino divergence — the JS mirror
was dividing the downside variance by the downside count instead of the full sample.)

## 5. Gotchas & numerical tolerances (hard-won — do not relitigate)

- **JS↔Python is NOT bit-for-bit; it agrees to a known tolerance.** PBO is exact (`0.0`); MinBTL & the
  Deflated/Probabilistic Sharpe agree to **~1e-8** (JS `normPpf` is Acklam's rational approx vs scipy
  `norm.ppf`); Black-76 **gamma/vega are exact** (they use `normPdf`/`exp`) while **delta agrees to
  ~7e-8** (JS `erf` approx); a full DSR computed from independently-estimated **skew/kurtosis agrees
  to ~1e-5** (JS moment helpers vs scipy `bias=False`). State the *real* tolerance; don't claim
  bit-for-bit.
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
  The tape coloring inverts if you read it as the aggressor.
- **`max_pain`/gamma-concentration are positioning/structure, never forecasts**; **signed dealer GEX /
  flip levels are rejected** (dealer sign unknowable from keyless data — see options run-log).
- **TradingView embed** writes inline px heights on the iframe + container → must be overridden with
  CSS `!important` on `.tv-embed` (don't assume; read the rendered DOM).
- **Stats grids:** explicit column counts that divide the cell count (auto-fit wraps to a ragged
  half-row of empty cells at wide widths).

## 6. Roadmap / deferred (pre-registered — do NOT start without an explicit greenlight)

- **Part B strategies B1/B2/B3** — already evaluated and **rejected/logged** (B1 tsmom×vol-target = a
  literal duplicate of the board's vol-scaled tsmom, corr 1.00; B2 OU-pairs = "model, not edge"; B3
  carry = OOS-insufficient). Re-judge only through the harness on OOS DSR / PBO.
- **DSR-convention unification** — the older single-strategy Performance code path historically used a
  different Sharpe-variance convention than the leaderboard; the headline is unified, but a full audit
  of every DSR call to one convention is a tidy follow-up.
- **True bit-for-bit parity** — swap JS `normPpf` (Acklam) / `erf` for higher-order approximations to
  close the ~1e-8 / ~7e-8 gaps, if ever wanted.
- ~~Commit the parity probes under `scripts/`/`tests/` for CI~~ — **done**: `scripts/check_parity.py` +
  `tests/test_parity_mirror.py` + `.github/workflows/ci.yml` (see §4).
- **Visual pass** — the institutional redesign is a first pass; type-scale/color/per-panel refinements
  may iterate.

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
| [AUDIT.md](AUDIT.md) / [AUDIT_LOG.md](AUDIT_LOG.md) | Repeatable code/stat audit spec + the change-log of verified fixes (H1 funding P&L fixed; remaining findings tracked) |
| **DEVELOPMENT.md** (this) | Contributors — architecture, the parity rule, extend-recipes, verification, gotchas, roadmap |
| [DISCLAIMER.md](DISCLAIMER.md) | Research-only / not financial advice |
