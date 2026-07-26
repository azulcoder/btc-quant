# `btcquant/orderflow.py` (M1) — run-log

**What was added.** The research-side keystone that closes the flywheel described in
STRATEGY.md Gap 1: a module that reads the recorded tick store (per-UTC-day DuckDB
files and/or the same tables mirrored as `hf://` parquet) and emits **event-time
order-flow bars in the DataFrame contract `features.py` and `backtest.py` already
consume** — a tz-aware UTC `DatetimeIndex` with `open/high/low/close/volume` first,
order-flow and quality columns after. Nothing in the harness changed; that is the whole
point and it is executed rather than argued (see "Harness contract", below).

The module produces **features only**. It never ranks, scores, or implies predictive
power. Scoring stays in `btcquant/risk.py`, behind the STRATEGY.md §6 refusal.

---

## 0. Grounding corrections — the brief was wrong in three places, the data won

These were measured against the real store before a line of production code was
written. They are recorded here because "don't take a spec on faith" is the rail that
caught them.

| Claim in the brief | What the store actually holds (measured 2026-07-26) |
|---|---|
| book is "bybit-primary single-venue" | **Not for the recent window.** `depth_snapshots` per day: `binancef`+`bybit`+`okx` for 2026-07-05..07-22; on 07-23 bybit/okx are nearly dead (1,444 / 2,215 rows); **07-24 and 07-25 are binancef only**. No single venue has trades *and* depth across all 21 days. |
| "~269k trades/day" | That is a **degraded** day. A full day is 4 venues and **4–6.5 M trades**: 07-06 = bybit 2,459,459 + binancef 1,958,674 + okx 1,719,248 + coinbase 797,121. Consequence: aggregation is pushed into DuckDB, never into pandas. |
| "liquidations 0 rows on 2026-07-25 — honestly empty days EXIST" | True but ambiguous, and the ambiguity is load-bearing. The local day file **has** the table (the DDL creates all tables) with 0 rows; the Hub has **no** `liquidations.parquet` partition for 07-24/07-25 (the exporter skips empty tables → 19 partitions, 07-05..07-23). Two representations, one fact. **And** 0 rows on 07-25 is not "no liquidations happened" — the bybit leg was dead that day (0 bybit trades). Zero-vs-unknown therefore cannot be decided by a row count; it is decided by the **liveness of the source leg**. |

Two more measured facts that shaped the design:

* `binancef` stores exactly **20** book levels (`min = max = 20` over 74,575 snapshots on
  07-25) and the whole top-20 spans **~0.37 bp p50 / 0.45 bp p95** from the touch. A
  depth-slope defined over a *fixed bp band* is therefore degenerate on that leg. The
  slope is defined over a **fixed level count** instead — the only identifiable
  definition across legs — and `depth_levels_*` is emitted so the resulting
  cross-venue incomparability stays visible.
* `binancef` **does** have a trades leg in the archive (07-05..07-22, 0.2–2.0 M/day),
  which DESIGN §3 says is not collected. The design doc describes the original wire
  constraint; the store has since moved past it. Worth a `[SUPERSEDED]` note in
  DESIGN-orderflow-terminal.md §3 when someone next touches it.

---

## 1. The gap model, and what it says about the archive

Central rule: **for every source leg (venue × stream), a derived feature is NaN unless
that leg is demonstrably alive in the bar. A zero is only written when the leg is alive
and the event genuinely did not happen.** Nothing is interpolated, forward-filled, or
smoothed. Liveness comes from a *witness* stream that is dense by construction
(`trades ∪ depth_snapshots`), because a stream that prints a few dozen rows a day —
liquidations — cannot witness its own liveness. The hole threshold is
`GAP_MS = 30_000`, imported in spirit from `scripts/check_ticks.py` and pinned against
it by a test so the L3 QA report and research can never disagree about what a gap is.

**Numerical validation (2026-07-25, coinbase leg, 1min bars).** The module's
interval-overlap coverage was checked against an independent 86,400-slot per-second
occupancy array built straight from the raw timestamps:

```
module : 1314 full-coverage bars, 4 partial, 122 empty; total hole 7,453.149 s
census : 2 interior holes (6,511.047 s + 942.102 s) = 7,453.149 s
         122 minutes containing zero prints
```

Exact agreement on all four numbers, by two routes that share no code.

**And the archive is genuinely holed.** For the bybit leg on 2026-07-06 — a *good* day
with 2.46 M trades — the witness stream contains **201 holes longer than 30 s totalling
16,264 s (4.5 h, 18.8 % of the day)**, the largest 1,104 s. Inter-arrival p50 is 12 ms.
This is exactly STRATEGY.md Gap 3 (fragile single-laptop collector) showing up as a
number instead of a worry. It is reported, never filled.

A DuckDB semantics note that cost real debugging time and is now pinned by a test:
`least(x, NULL)` returns `x` in DuckDB (NULL-ignoring, unlike standard SQL). The
obvious per-bar overlap expression written over a `LEFT JOIN` therefore evaluates to a
full `bar_ms` for every bar with **no** matching hole — i.e. it reports a fully empty
day. `_gap_ms_per_bar` is numpy precisely so that bug is inexpressible.

---

## 2. Features, citations, and the approximation each one carries

Every column also carries this in machine-readable form: `PROVENANCE[template]` →
`FeatureNote(column, family, formula, citation, approximation, units, source_leg,
aggregation, contested)`. `FeatureNote.__post_init__` **refuses an empty
approximation**, so "no caveat stated" is a construction error, not an oversight. A
test asserts every emitted column resolves to a note.

| Feature | Reference | Approximation, stated |
|---|---|---|
| CVD / signed delta | Chordia, Roll & Subrahmanyam (2002), *JFE* 65(1), 111–130 | **None on the sign** — the aggressor side is the real wire flag normalized per venue by `collector.py`; no Lee-Ready tick rule, no bulk-volume classification. `cvd_*` is session-anchored and **reset at each coverage segment**; carrying the level across an outage would imply flow nobody observed. |
| Size-bucketed delta | taxonomy reused verbatim from the repo's own `CvdStore` (`[1e4, 1e5, 1e6]`) | Bucketed **per print, not per parent order** — an iceberg reads as many retail prints. Smallest threshold ≥ notional wins, so exactly $10,000.00 is retail. |
| OFI | Cont, Kukanov & Stoikov (2014), *J. Financial Econometrics* 12(1), 47–88 | **"1s snapshot approximation stated".** The paper sums per-EVENT contributions over every L1 update; the store holds ~1 Hz snapshots, so this is the net inter-snapshot contribution. Bias direction is known: quote oscillation inside the sampling interval makes the snapshot-sampled `|OFI|` **understate** the event-level value, and intra-second churn is invisible. Pairs separated by more than `GAP_MS` are excluded and counted in `ofi_gap_pairs_*`. |
| Microprice | Stoikov (2018), *Quantitative Finance* 18(12), 1959–1966 | **"weighted mid — Stoikov first-order form, not the fitted micro-price".** The full estimator needs a fitted Markov correction over (imbalance, spread); that is a model, not an observable, and it is not fitted here. Queue imbalance itself: Gould & Bonart (2016), *MML* 2(2). |
| VPIN | Easley, López de Prado & O'Hara (2012), *RFS* 25(5), 1457–1493 | **"contested: Andersen & Bondarenko (2014) attribute VPIN's content largely to volatility/intensity"** (*JFM* 17(1), 1–46). Deviations, both labelled: classification uses the **real aggressor flag** rather than the paper's bulk-volume classification (better input, same statistic); and `V` is set **causally** from the median daily volume of strictly prior days in the range, because the paper's "average daily volume / 50" peeks at the day it measures. First day of any range is warm-up NaN. Clock re-armed at UTC midnight, so each day's trailing partial bucket is dropped. At 50 buckets/day the series updates ~50×/day, so most 1min bars carry a **stale** value — reported in `vpin_age_s_*`, not hidden. |
| Liquidation intensity | descriptive counting only; Brunnermeier & Pedersen (2009), *RFS* 22(6) cited as mechanism **motivation, not evidence** | No cascade model, no leverage assumption. `side` is the *liquidated position*, per the collector's normalization (bybit `allLiquidation` side `Buy` means a **short** was liquidated). Only `LIQUIDATION_VENUES = ("bybit",)` gets columns at all — other venues get **no column**, not a misleading zero. |
| Depth-imbalance slope | Næs & Skjeltorp (2006), *JFM* 9(4), 408–432; Cao, Hansch & Wang (2009), *JFM* 29(1), 16–41 | Fixed **level count**, not a fixed bp band (see §0). Snapshots shallower than `depth_levels` give NaN, never a zero-padded book. Intercept pinned at the origin because cumulative depth at zero distance is zero by construction. **Not comparable across venues** (20 vs 50 stored levels). |

---

## 3. Independent verification — every feature, two routes

The standing repo rule (`risk.py` closed-form-vs-optimiser pattern) applied to every
feature: nothing ships whose only witness is the implementation that produced it.
Fixtures are hand-computed; the real-data checks below run against the recorded
archive.

### 3.1 On real recorded data

| Feature | Independent route | Sample | Result |
|---|---|---|---|
| OFI | per-pair Python loop, CKS eq. written out term by term | 74,575 binancef snapshots, 2026-07-25 | max &#124;Δ&#124; = **5.684e-13** |
| Microprice | second algebraic form `I·Pa + (1−I)·Pb` computed from raw JSON | last snapshot of each of 96 bars | max &#124;Δ&#124; = **0.000e+00**; 0 bracket violations; **0 crossed books** in 74,575 |
| Depth slope | `np.linalg.lstsq` on the same (x, Q) points | both sides, 96 bars, K = 10 | max &#124;Δ&#124; = **2.274e-13** |
| Signed delta | naive Python loop over the raw tape | 268,922 coinbase prints | max &#124;Δ&#124; = **3.368e-12** (BTC), **2.654e-08** on ~1e7 USD ⇒ ~1e-15 relative |
| Size buckets | per-bucket naive loop; plus the partition identity | same tape | max &#124;Δ&#124; = **6.985e-09**; `Σ buckets − delta_usd` max **1.397e-08** |
| VPIN | pure-Python bucket-splitting loop | 184 buckets, V = 10 BTC, window 10 | buy/sell max &#124;Δ&#124; = **1.7e-11 / 4.4e-11**; VPIN max &#124;Δ&#124; = **1.171e-12**; bucket close timestamps **identical**; every complete bucket holds V to **4.6e-11** |
| Coverage | 86,400-slot per-second occupancy array | 2026-07-25 | **exact** on all four counts (§1) |

The VPIN check also demonstrates *why* exact boundary splitting matters: the naive
`floor(cumvol / V)` assignment the module deliberately does **not** use produces first
buckets of `9.99304 / 9.98298 / 9.79983` against a target of `10.0`.

### 3.2 In the test suite (`tests/test_orderflow.py`, 52 tests, network-free)

Hand-computed fixtures with the arithmetic written out in the test: OFI across all four
indicator branches (bid up/down/flat × ask up/down/flat), VPIN over six 0.5-BTC prints
at V = 1.0, the `$10,000.00` bucket boundary, `q_bid = q_ask ⇒ weighted mid = mid`
exactly. Plus the structural rails: truncation invariance (`bars(start, T)` equals
`bars(start, T′)` truncated at T to machine precision — **not** byte-identically:
DuckDB parallel float aggregation is not order-stable, ~4e-08 absolute on one real
day's `sum(qty)` between `threads=1` and `threads=8`), a future trade cannot change a past
bar, a VPIN bucket closing 1 ms after the bar end is invisible, editing the last day
cannot move an earlier day's `V`, gap bars are NaN and never 0.0 and never
forward-filled, the three liquidation honesty states, a locked day file is skipped and
recorded, today's file is never read, and an AST scan asserting the module imports
nothing from `dashboard/` and no network client.

---

## 4. Harness contract — executed, not argued

`backtest.walk_forward` (`backtest.py:394`) touches only `prices`: coerce → dedupe →
`sort_index()` → `dropna()` → `np.linspace` over integer positions. It never reads the
index frequency. `backtest.run` → `_align` → `pct_change()` → `pos.shift(1)` →
`_assert_no_lookahead` is entirely index-based. `features.atr` needs only
`high`/`low`/`close`. `backtest.cpcv` shares the leading `(make_positions, prices)`
contract — its signature is NOT identical (`n_blocks`/`k_test`/`embargo_pct` vs
`n_splits`/`min_train`), so it is **run** in the smoke rather than inherited by
argument. [SUPERSEDED] an earlier version of this line claimed an identical signature.

Tests prove it on synthetic bars; `scripts/orderflow_smoke.py` proves it on the real
archive with the `scripts/compare.py:533` call idiom reproduced verbatim.

---

## 5. End-to-end smoke — the deflation stack refuses, and that is the result

`make orderflow-smoke` (= `scripts/orderflow_smoke.py`). Window **2026-07-05 →
2026-07-23**, `price_venue = book_venue = bybit`, `bar = 1h` — the only stretch of this
archive where one coherent instrument (BTCUSDT perp) has both a trade leg and a book
leg. No tuning, no window shopping, no dropped days.

Re-run after the review pass of 2026-07-26 (§7). The numbers below are that run,
not the pre-review one: per-leg coverage witnesses and the stricter `ret_spans_gap`
moved the coverage census, which is the point of them.

```
BARS                     : 432 x 52, built in 416 s from hf:// (18/18 days)
index                    : 2026-07-05 00:00 .. 2026-07-22 23:00 UTC, left-labelled, 1h

COVERAGE (qualifies every number below)
  fully covered          :  39 / 432
  partially covered      : 304 / 432
  fully empty (NaN)      :  89 / 432
  total feed hole        : 224.93 h of 432 h  (52.1 %)
  returns spanning gaps  : 405 / 432
  clean segments         :  12   (segment ids in total: 405)
  unresolved days        :   0   range final=True

FEATURE POPULATION (non-NaN / 432)
  close 343 · delta_bybit 343 · cvd_bybit 343 · delta_usd_whale_bybit 343
  ofi_bybit 342 · microprice_bybit 342 · depth_slope_imb_bybit 342
  vpin_bybit 315 (day-1 warm-up NaN by the causal V rule)
  vpin_window_gap_s_bybit 315 · liq_notional_usd_bybit 343 · coverage_liq_bybit 432

HARNESS CONTRACT (unchanged code, executed)
  features.atr(bars, 14)            -> 289.2012      OK
  features.realized_vol(r, 20, ppy) -> 0.2746        OK
  backtest.walk_forward(lambda px, p=pos: p.reindex(px.index), bars["close"],
      n_splits=5, cost_bps=10, slippage_bps=2, periods_per_year=8760)   OK
  backtest.cpcv(same bars, n_blocks=6, k_test=2) -> 15 paths,
      OOS SR p25 -15.2244 / p75 -5.3419 (min -23.5591, max 4.1338)      OK
  OOS keys returned: cagr calmar cvar_5pct deflated_sharpe dsr_is_psr fold_srs
      hit_rate kurtosis max_drawdown n_periods n_trials psr sharpe
      sharpe_per_period skew sortino terminal_equity var_5pct var_fallback
      var_trials_sr volatility

WALK-FORWARD (trivial CVD-slope candidates; net of 10+2 bps)
  candidate         OOS SR    OOS DSR   OOS n    maxDD
  cvd_slope  3h    -8.7950     0.0000     286   -1.74%
  cvd_slope  6h    -3.0748     0.0005     286   -1.00%
  cvd_slope 12h   -10.3116     0.0000     286   -2.38%
  cvd_slope 24h    -8.4981     0.0000     286   -1.93%

DEFLATION
  trials (N) 4 · best-of-N cvd_slope 6h · DSR 0.1315 · PBO 0.2857 (CSCV, 8 blocks, 70 splits)

MinBTL
  recorded span              : 18.00 calendar days = 0.0493 yrs
  usable full-coverage bars  : 27 of 432 = 0.0031 yrs of clean 1h bars
  MinBTL(N=5)   2.699 yrs (  985 d)  -> have 1.8 %
  MinBTL(N=20)  3.152 yrs (1,151 d)  -> have 1.6 %
  MinBTL(N=100) 3.640 yrs (1,328 d)  -> have 1.4 %
  module attrs["history"]    : 18 of 18 days resolved, 0.0493 yrs = 1.8 % of MinBTL(5)

VERDICT: INSUFFICIENT HISTORY — no candidate may be scored, and none is.
```

The smoke exits 0 **because** the verdict is INSUFFICIENT; it fails if the span ever
claims to clear MinBTL(5) on this archive, or if any part of the pipeline breaks.

---

## 6. Honest verdict

**The machinery works. The data does not yet exist.** Three numbers carry that:

1. **1.8 % of MinBTL(N=5).** Even at the most charitable trial count, this archive is
   ~2 % of the length needed before a best-of-N selection means anything. There is no
   candidate to clear and none was cleared.
2. **52.1 % of the smoke window is a feed hole,** and only **27 of 432** bars are both
   fully covered and carry a non-gap-spanning return — 0.0031 years of clean 1h bars.
   The binding constraint on M1's usefulness is not the code, it is STRATEGY.md **N4**
   (collector uptime). Building the rail was right; it now needs the clock to run.
3. **The trivial candidates are all deeply negative net of cost** (OOS Sharpe −3.1 to
   −10.3 at 12 bps round-turn on an hourly clock; CPCV path SRs −23.6 to +4.1 over 15
   paths, i.e. sign-flipping dispersion on top of a negative centre). That is a statement about
   transaction costs and a 3-week sample, **not** evidence about order flow. It is
   reported because a negative result is a result, and deleting it would be the
   dishonest move.

Deliberate limits worth restating: the depth slope is a single-venue time series and is
**not comparable across venues**; VPIN on a 1min grid is mostly stale by construction
(`vpin_age_s_*` reports it); `cvd_*` levels are only comparable inside one `segment`;
and OFI is a snapshot-sampled approximation whose bias direction is **unknown** and is
stated as unknown rather than guessed at.



---

## 7. Review pass, 2026-07-26 — what the first cut got wrong

A full adversarial re-read of the shipped module, run against the real archive rather
than against the fixtures. Eight defects were reproduced *before* being fixed; each fix
carries a test that recomputes the corrected behaviour by an independent route. Recorded
here because a run-log that only lists what worked is a marketing document.

| # | Defect | How it was reproduced | Fix |
|---|---|---|---|
| 1 | **Fabricated zeros.** The trade family was witnessed by `trades ∪ depth_snapshots`, so a venue whose trade leg died under a live book scored `coverage = 1.0` and its missing rows became `0.0`, not NaN | `binancef` on 2026-07-25 has **0** trades and **74,575** depth rows. `price_venue="binancef"`, 1h: **20 of 24** bars read `coverage=1.0, volume=0.0, delta=0.0` while all 24 closes were NaN | Witness per leg: trades-only gates the trade family, depth-only the book family, `trades ∪ depth` only the (sparse) liquidation family — now emitted as `coverage_liq_*` instead of being reused silently. Same call now: `coverage` uniformly `0.0`, every trade column NaN, `mid_binancef` still populated on 23/24 bars |
| 2 | **`ret_spans_gap` never tested its own bar's close**, so the first outage bar was certified clean by `drop_gap_bars`, and `walk_forward`'s `px.dropna()` then compressed the whole across-the-hole move into one tradeable bar | synthetic 100 → 150 across a 30-min trade-leg outage: `drop_gap_bars` kept a NaN-close bar and the +50 % move was earned in one bar | `\| ~isfinite(close[t])` added; a bar with an unknown close can never be a clean return |
| 3 | **VPIN volume clock armed at the window start, not at UTC midnight** — contradicting its own label, and making the same bar window-dependent | real coinbase 2026-07-25, `[00:00,06:00)` vs `[03:00,06:00)`, 180 shared 1-min bars: `vpin` differed on **180/180**, max \|Δ\| **8.74e-02** | trades read from the whole UTC days the request touches (`of_trades_day`); re-measured **0/180**, max \|Δ\| **0.0** — and `_causal_bucket_volumes` no longer sees a partial first day |
| 4 | **OFI lost the snapshot pair straddling the window start** (`lag` over a window-filtered relation), so bar 0 was window-dependent | real binancef 2026-07-25 bar 01:00Z: `[00:00,02:00)` → **175.039 (n=58)**, `[01:00,02:00)` → **191.017 (n=57)** | pairing anchored to the UTC day, aggregation still restricted to `[t0,t1)`; re-measured **175.039 (n=58)** from both windows, 0/60 bars differ |
| 5 | **`segment` only broke on a wholly empty bar**, so `cvd_*` accumulated through any hole shorter than one bar — up to 59 minutes on a 1h grid — while its own note promised a reset | synthetic 20-min hole inside a 1h bar: `coverage=0.667, is_gap=True`, `segment` stayed 0 for all six bars | segment = maximal run of **fully covered** bars; every non-full bar takes an id of its own. `coverage_summary` now reports clean runs and raw ids separately |
| 6 | **Cache poisoning.** A range containing a not-yet-closed (or locked, or not-yet-uploaded) day was written to the cache as an all-gap frame and served forever; the spec hash cannot see which days resolved | seeded today's day file, requested `[today, tomorrow)`, then advanced the clock past midnight — the cache still returned coverage 0.0 while `cache=False` returned real bars | `_open_source` records `resolved`/`final`; a non-final range **warns** and is never written. The docstring's "a cached range cannot go stale" argument is marked `[SUPERSEDED]` |
| 7 | **local and `hf://` disagreed on an honestly-empty liquidations day** (`0.0` vs NaN), because `upload_hf.py` skips empty tables and the gate used table/partition *presence* | 2026-07-25 holds a `liquidations` table with 0 rows locally; the same day has no partition on the Hub | zero-vs-unknown decided by **leg liveness** only, with a backend-symmetric `liquidations_recorded` (structural table presence locally, day-present-on-Hub for `hf`). The one residual asymmetry — a pre-schema day file — is stated in the note rather than hidden |
| 8 | **No `symbol` filter anywhere.** Two symbols on one venue were pooled into one tape, and the book de-dup `QUALIFY row_number() OVER (PARTITION BY ts_ms …)` kept whichever instrument's L1 sorted first | synthetic bybit BTCUSDT + ETHUSDT at 1 Hz: `low` became 2.0, `volume` 183,600, `mid` 2.05 | `symbol=` (string or `{venue: id}`) plus a hard error naming both symbols when a venue carries more than one. Rail 5 now covers symbols, not just venues |

Two label defects, which on this module are defects of the same weight:

* **The OFI bias direction was asserted and is false.** The note claimed the sampled
  `|OFI|` *understates* the event-level value "when the best quote oscillates inside the
  sampling interval" — the counterexample is exactly that case. Asks frozen at 102@7,
  bids 100@10 → 101@5 → 100@1: event-level OFI is `+5 − 5 = 0`, but with the middle
  state missed the single pair gives `1{100≥100}·1 − 1{100≤100}·10 = −9`. It
  **overstates**. Cause: the Cont-Kukanov-Stoikov indicator convention fires *both*
  indicators when `P_n = P_(n−1)`, so a price round-trip reads as a queue depletion of
  the full size difference. The formula itself was re-verified against a per-pair Python
  loop over all 74,575 real binancef snapshots (\|Δ\| = **1.48e-12** on a mean \|OFI\| of
  **823.79**, `ofi_n` and `ofi_gap_pairs` exact) — the arithmetic was never the problem,
  the sentence was. Marked `[SUPERSEDED]`, replaced with the counterexample, pinned by a
  test that runs both stores through `order_flow_bars`.
* **Rail 6 ("no look-ahead") was literally false for the quality family.** A feed hole is
  only measurable once the feed *resumes*, so `coverage`/`gap_ms`/`is_gap`/
  `ret_spans_gap`/`segment` at bar *t* use data-availability knowledge from after *t*
  closes (reproduced: the same bar reads `coverage 0.5667 / gap_ms 26,000 / is_gap True`
  in a 10-minute window and `1.0 / 0.0 / False` in a 1-minute one). The leak is bounded
  by the hole length and carries no price information, and `drop_gap_bars` already
  carried the label — rail 6 and all five quality notes now carry it too.

Also corrected: "byte-identical" was over-strong (DuckDB parallel float aggregation is
not order-stable — one real day's `sum(qty)` differs by **4.28e-08** between `threads=1`
and `threads=8`), so the two invariance tests now assert machine precision instead of
exactness and say why; the hard-coded "21 recorded days" in rail 4 was replaced by a
measured `attrs["orderflow"]["history"]` block, because a numeral in a frozen sentence is
a claim a test can only check for presence, never for truth; `volume_bars` was renamed
`volume_buckets` and dropped from the package top level (it has no OHLCV, so it never
shared the bar contract its name implied); and a spot `price_venue` paired with a perp
`book_venue` — unavoidable on 2026-07-25 — now warns and records
`attrs["orderflow"]["cross_instrument"]`, since `mid − close` there is a ~40 USD funding
basis, not book pressure.

Re-verified after all of it, on the real archive by independent recomputation: OFI vs a
per-pair loop **1.48e-12**; OHLC exact, `volume` **2.40e-12**, `delta` **3.37e-12**,
`dollar_volume` **4.28e-08** vs a naive loop over 268,922 coinbase prints; VPIN vs a
pure-Python splitting loop — 36/36 buckets, `vpin` **1.95e-12**, and the two new
`window_span_s` / `window_gap_s` columns **exact** (0.0) against the same loop.
`SCHEMA_VERSION` bumped to `2`, so no pre-review cached bar can be read back.
