# EDA-microstructure-001 — descriptive exploration of the recorded L2 store

**Status: §0 and §2 complete. §1 and §3–§7 not started.**

**Scope, locked before §2 and not revisable on §3 results:**

| track | scope | role |
|---|---|---|
| **A** tape-only | all UTC hours | **primary** — the only track that can ever be scored |
| **B** book, binancef single-venue | UTC 12–23, ON hours | §2 (cost) and execution-overlay characterisation **only** — never a signal candidate |
| **C** book cross-venue | — | **CLOSED.** bybit reaches ON in at most **10 of 30 days at its best hour** and 0–4 days across UTC 00–11 (§0c). Recorded here so it is not re-proposed. |

Descriptive research under `DESIGN-orderflow-terminal.md:29-32` (§0.1, live-descriptive). **No
finding in this document may become a trading rule** without passing through a PREREG with a
counted `N_trials`. Nothing here is a signal, a recommendation, or a backtest.

---

## Slice declaration — exploration vs LockBox

Declared **before** any number below was produced, per `backtest.py:772-831` (`LockBox`,
AFML §11.6) and `STRATEGY.md:843-846`.

| slice | span | status |
|---|---|---|
| **EXPLORATION** | `2026-07-05` … `2026-08-03` (30 recorded UTC days, the HF mirror `azulcoder/btc-quant-ticks`) | **touched.** Every query in this document reads only this. |
| **LockBox** | ~~`2026-08-05 00:00 UTC`~~ → **`2026-08-05 01:00 UTC` onward**, all venues, all tables. Boundary MOVED FORWARD — see the amendment below. | **NOT touched, NOT read, NOT peeked.** Reserved for evaluate-once scoring of whatever hypothesis this document eventually produces. |
| **quarantine-2** | `2026-08-05 00:00 … 00:59:59 UTC` (the 60 min between the old and new boundary) | excluded from both. Recorded with the `cursor=None` defect active. Never read. |
| **quarantine** | `2026-08-04` (the day in progress) | excluded from both. It is mid-experiment (`reports/incident-2026-08-04-sleep/`) and its coverage is being deliberately manipulated, so it is neither clean exploration nor a fair holdout. |

### AMENDMENT 2026-08-05 — LockBox boundary moved forward, and why that is legitimate

**Old boundary: `2026-08-05 00:00 UTC`. New boundary: `2026-08-05 01:00 UTC`.** The old value is
struck through above rather than deleted; the original declaration stays on the record.

**Reason [DIUKUR].** The LockBox opened at `00:00 UTC` with the `cursor=None` defect still active
in the running collector. That defect is measured, not suspected: on `2026-08-03` it silently
dropped **28,428 aggTrade prints in 4 contiguous blocks** (§10, §10a), verified against Binance's
own published archive. A slice reserved for evaluate-once scoring cannot be recorded by an
instrument known to be losing tape, because the one property that makes it worth anything —
that it is a fair, untouched sample — would be false on arrival.

The collector was restarted at **`2026-08-05 00:05:28 UTC`**, activating `_stamped` and the
cursor fix. The new boundary is the **next full UTC hour after that restart**, chosen as a rule
rather than a judgement so it could not be tuned. The 60 minutes in between are quarantined, not
absorbed.

**Why moving it leaks nothing, stated so it cannot be misread later.** The boundary moved because
of a **documented data-quality defect**, decided **before anyone looked at any of it**.
**Zero bytes of LockBox data have been read, queried, plotted, or summarised** — not before the
move, not during it, not after. No query in this repository has ever touched a timestamp at or
beyond `2026-08-05 00:00 UTC`, and the move was specified in terms of the restart clock alone,
with no reference to anything inside the slice.

**The forbidden version of this action, named explicitly so the difference is on the record:**
peeking at LockBox data, disliking the result, and then moving the boundary to exclude it. That
is a different act with a different consequence, it remains forbidden, and nothing here
authorises it. The distinguishing test is simple and checkable — *was the slice read before the
boundary moved?* Here the answer is no, and it is no because no such query exists.

**One consequence, recorded rather than hidden:** the LockBox now starts 1 hour later, so the
evaluate-once clock is 1 hour shorter than declared. That cost is real and it is the price of
the slice being clean.

A forward LockBox rather than a random hold-out because the binding constraint here is
**calendar time**, not row count — a random 20 % of 30 days removes 6 days of an already
1.8 %-of-MinBTL sample while leaving every regime represented in training. A forward slice
costs nothing today and is the only split that can ever answer "did this survive out of
sample in time".

---

# §0 — The missingness mechanism

The question is not "how much coverage" (baseline answered that) but **whether the missingness
can be ignored statistically**. All three sub-parts below run against the exploration slice.

## 0a — ON / OFF structure over 30 days

Each `(date, hour, venue)` cell classified by unique depth-seconds in that hour:
**ON ≥ 3,240 (90 %)**, **OFF < 360 (10 %)**, else **PARTIAL**. The full 30 × 24 × 3 = 2,160-cell
grid is built by cross join, so **cells with no rows at all are counted as OFF** rather than
silently dropped — that was the failure mode in the first coverage scan.

*Source: `scratchpad/eda/hourly.duckdb` tables `depth_h`, `grid`, built from
`read_parquet('hf://datasets/azulcoder/btc-quant-ticks/data/date=*/depth_snapshots.parquet',
hive_partitioning=1)`.*

**Cell census [DIUKUR]:** OFF **963 (44.6 %)** · ON **752 (34.8 %)** · PARTIAL **445 (20.6 %)**.

### By UTC hour — your hypothesis HOLDS over 30 days [DIUKUR]

`%ON` = share of the 90 cells (30 days × 3 venues) in that hour classified ON.

| UTC | WIB | %ON | session |
|---|---|---:|---|
| 00–08 | 07–15 | **10–21 %** | Asia |
| 09–11 | 16–18 | 19–38 % | London |
| 12–15 | 19–22 | 40–47 % | London + NY |
| 16–22 | 23–05 | **56–61 %** | NY |
| 23 | 06 | 49 % | — |

You derived this from one 24 h window and asked whether it survives 30 days. **It does**, and
the gradient is monotone: worst at 03h UTC (10 %), best at 22h UTC (61 %) — a **6× spread**.

**But the WIB column inverts the interpretation you offered** [DISIMPULKAN]. Coverage is *worst*
during your working day (07:00–15:00 WIB) and *best* overnight (23:00–05:00 WIB). If the
mechanism were "I sit at the terminal when the market moves", coverage would peak in your
waking hours. It does the opposite. The pattern is consistent with **machine undisturbed-ness**
— left running overnight, lid-closed / carried / idling during the day — not with attention.
That mechanism is inferred from the shape, not observed.

The consequence is unchanged and severe: **the recorded book is concentrated in the NY session
and nearly absent in Asia.**

### By day of week [DIUKUR]

Wed 46.9 % · Sun 43.6 % · Mon 41.4 % · Tue 33.7 % · Thu 33.3 % · Sat 24.7 % · **Fri 16.3 %**

Friday is a 2.9× outlier below Wednesday on 4 observed Fridays. **With 4–5 days per weekday
this is not separable from noise** [DISIMPULKAN] and is reported as an observation, not an
effect.

## 0b — Attention endogeneity: the answer differs by granularity

The identification: binancef trades survive when the book does not (REST `aggTrades` with a
`fromId` cursor, `collector.py:_aggtrades_loop`), so tape-derived market state is observable in
book-OFF hours.

**Precondition check first, and it did not pass cleanly [DIUKUR].** Tape coverage is itself
book-state-dependent: **67.5 %** in book-OFF hours vs **78.3 %** in book-ON hours (median trades
per hour 25,938 vs 27,449 — only 5.8 % apart). The tape is not a clean instrument; it is a
*better* one. Mechanism [DISIMPULKAN]: `_aggtrades_loop` initialises `cursor = None` at loop
start, so **a leg restart re-seeds at the live edge and skips the backlog** — and because the
id-gap warning is guarded on `cursor is not None`, that skip is not even logged. Every restart
silently drops its backlog. All metrics below are therefore computed on a **fixed per-minute
grid with a ≥40-of-60-minutes floor**, so a thinner tape cannot mechanically shrink the estimate.

### Day-level: NO endogeneity [DIUKUR]

Within each UTC hour, days when binancef's book was ON vs OFF, compared on tape metrics.
16 of 24 hours had ≥3 days on each side.

| statistic | value |
|---|---|
| RV ratio (ON/OFF), median across hours | **0.957** (min 0.59, max 1.62) |
| trade-count ratio, median | **0.861** |
| hours with RV_ON > RV_OFF | **6 of 16** |

6 of 16 is indistinguishable from a coin flip. **Which hour-days got recorded is not
systematically related to volatility or trade intensity.**

### Minute-level, inside PARTIAL hours: MILD endogeneity [DIUKUR]

The day-level test cannot see your actual concern, which is *within* an hour. So: inside hours
classified PARTIAL, minutes with book coverage (≥30 s) vs minutes without.

| | minutes | RV (bps/min) | median trades | p95 abs-return |
|---|---:|---:|---:|---:|
| book present | 2,548 | **4.978** | 528 | 9.40 bps |
| book absent | 1,942 | **4.368** | 482 | 9.18 bps |
| **ratio** | | **1.140** | **1.095** | 1.024 |

**Book-covered minutes are ~14 % more volatile and carry ~10 % more trades.** The tail (p95
absolute return) is nearly unaffected at 1.024, so this is a shift in the *body* of the
distribution, not in extremes.

**Verdict [DISIMPULKAN]: conditionally ignorable given UTC hour at day granularity;
mildly non-ignorable within the hour.** Any §3 conditional distribution must be reported
**with and without** reweighting, and the uncorrected version should be read as
**over-stating volatility by roughly 10–15 %**.

**A competing mechanism I cannot rule out, and which would change the interpretation
entirely** [DIASUMSIKAN]: macOS DarkWake is triggered by network activity
(`'Maintenance Sleep':TCPKeepAlive`, `pmset -g log`). Inbound WS frame rate scales with market
activity. So the machine may wake more when the market is busier **mechanically**, with no
human attention involved. Day-level and minute-level results are both consistent with that.
Distinguishing it from attention needs the wake-reason string joined to the burst timestamps —
not done here.

## 0c — Analytical power per hour: half the clock is unstudiable

Days (of 30) with all three venues ON in that hour [DIUKUR]:

| UTC hours | days with 3-venue book | verdict |
|---|---:|---|
| **00–11** | **0–4** | **cannot be studied with book data** |
| 12–17, 19–23 | 5–9 | thin — wide CIs, no stratification within |
| 18 | 10 | the only hour with enough for stratified analysis |

Per venue, best hour: binancef 25/30 · okx 22/30 · **bybit ≤ 10/30 at every hour**.

**12 of 24 UTC hours have effectively no multi-venue book history**, and exactly **one hour
(18h UTC = 01:00 WIB)** reaches 10 days. bybit is the binding constraint for anything
three-venue.

---

## What I could not measure in §0, and why

- **Whether the endogeneity is attention or network-triggered DarkWake.** Both fit. Separating
  them needs the `pmset` wake-reason joined per burst; not done.
- **Per-trade size quantiles.** Only per-minute aggregates were materialised; true trade-size
  distribution needs a second full pass over 1.4 GB of trades. Mean size is reported, quantiles
  are not.
- **A price-based spread proxy in §0.** Deferred to §2, where the estimator family (Roll,
  Corwin-Schultz, Abdi-Ranaldo, EDGE) is the subject rather than a by-product.
- **Anything about 2026-08-04.** Quarantined by declaration above.
- **Whether HF parquet matches the deleted local day files byte-for-byte.** Manifests carry
  sha256; payloads were not re-downloaded and re-hashed.
- **Day-of-week as an effect.** 4–5 observations per weekday; reported, not tested.

---

---

# §2 — Measured cost

Done before §1 because measuring a cost distribution examines **zero forward returns**, so it
contributes **zero to `N_trials`** and MinBTL does not apply. It is also the one part of this
programme where data is not the binding constraint: 25 of 30 days have ON hours in UTC 12–23.

**The headline, stated first because it reframes the section:** the microstructure component of
cost is **negligible** for this instrument at retail size, and the component that matters —
fees — **cannot be measured from data at all**, only cited. §2 therefore ends up refuting its
own premise, and that refutation is the deliverable.

## 2a — Spread and slippage from the book

### Spread is a constant, not a distribution [DIUKUR]

*Source: `data/ticks/2026-08-03.duckdb` (read-only), `depth_snapshots`, best bid/ask parsed with
`json_extract(bids,'$[0][0]')`; 151,715 snapshots across three venues.*

| venue | n | p10 | p50 | p90 | p99 |
|---|---:|---:|---:|---:|---:|
| bybit | 46,853 | 0.0157 | **0.0157** | 0.0160 | 0.0160 |
| binancef | 50,573 | 0.0157 | **0.0157** | 0.0160 | 0.0160 |
| okx | 54,289 | 0.0157 | **0.0157** | 0.0160 | 0.0160 |

All in **bps**. The three venues are **identical to four decimal places**, and p10→p99 spans
0.0003 bps. Per-UTC-hour medians on binancef range **0.0156 → 0.0160 bps — a total spread of
0.0004 bps across the whole day.**

[DISIMPULKAN] This is the **tick floor**: $0.10 on ~$63,500 = 0.0157 bps, and the book is one
tick wide essentially always. There is no per-hour or per-venue structure to model because there
is no variation to model.

### Confirmed over 26 days, not one [DIUKUR]

A day-by-day HF scan of every binancef ON hour in UTC 12–23 completed after the single-day
figures above were written: **859,264 snapshots across 26 partitions** (1 partition,
`2026-07-13`, failed to read — `InvalidInputException`, the same ZSTD decompression failure seen
elsewhere on this dataset; it is excluded and counted, not silently dropped).

| UTC | n | p10 | p50 | p90 | p99 |
|---|---:|---:|---:|---:|---:|
| 12 | 58,746 | 0.0153 | 0.0156 | 0.0160 | 0.0162 |
| 13 | 65,725 | 0.0154 | 0.0156 | 0.0161 | 0.0163 |
| 14–21 | 65,913–79,080 each | 0.0154 | 0.0156 | 0.0158–0.0160 | 0.0161–0.0162 |
| 22 | 82,015 | 0.0153 | 0.0156 | 0.0158 | 0.0161 |
| 23 | 68,397 | 0.0153 | 0.0156 | 0.0159 | 0.0161 |

**The median is 0.0156 bps in every single hour — range across hours: 0.0000 bps.** p99 ranges
0.0161–0.0163, a spread of 0.0002 bps. Aggregate over 26 days: **p50 0.0156, p99 0.0162**.

**The per-hour spread table this section was commissioned to produce is degenerate** — every
cell holds the same number, now on 859k observations rather than one day's 50k.

One caveat the aggregate reveals and the quantiles hide: **max observed spread is 5.7643 bps**,
about 370× the median. So a tail exists beyond p99. **Even that extreme sits below the 10 bps
round-trip fee**, so it does not change any conclusion below — but a fill model that assumes the
tick floor unconditionally would be wrong in the tail, and this is where it would be wrong.

### Slippage for a retail clip is half the spread and nothing more [DIUKUR]

Depth-walk over the ask ladder, 862 snapshots sampled at 1/minute, binancef 2026-08-03. VWAP of
the walk versus mid.

| clip | n | p50 | p90 | p99 | max |
|---|---:|---:|---:|---:|---:|
| $1,000 | 862 | 0.0079 | 0.0080 | 0.0080 | 0.0156 |
| $3,000 | 862 | 0.0079 | 0.0080 | 0.0080 | 0.0857 |
| $5,000 | 862 | 0.0079 | 0.0080 | 0.0080 | 0.1301 |

**In 99.7 % of snapshots the level-1 ask alone is ≥ $5,000**, so a $1–5k clip never leaves the
touch. p50 slippage of **0.0079 bps is exactly half the 0.0157 bps spread** — the walk is
crossing the spread and stopping.

**`backtest.py:84` assumes `slippage_bps=2.0`. Measured is 0.0079. That is a 250× overstatement**
for this instrument at this size [DIUKUR vs DIASUMSIKAN].

## 2b — Price-only estimators FAIL at this scale

Applied to binancef OHLC bars built from `trades`, 2026-08-03, against the measured book spread
of **0.0157 bps** in the same data.

| bar | n | Roll (1984) | Corwin-Schultz (2012) | Abdi-Ranaldo (2017) |
|---|---:|---:|---:|---:|
| 1 min | 1,375 | **1.6182** | **0.8884** | 0.0000 |
| 5 min | 278 | 4.1164 | 2.6424 | 0.0000 |
| 1 h | 24 | n/a (cov > 0) | 11.4666 | 0.0000 |

**Roll overstates by 103×, Corwin-Schultz by 57×, and Abdi-Ranaldo collapses to its zero floor**
(its moment `E[(c−η_t)(c−η_{t+1})]` is ≤ 0 in every window, so the estimator clamps). Error grows
with bar length.

[DISIMPULKAN] The cause is a signal-to-noise failure, not a coding error. These estimators
extract the bid-ask **bounce** from price changes. Here the spread is 0.0157 bps while
minute-scale realised volatility is **4.4–5.0 bps** (§0b) — a ratio of about **1 : 300**. There is
no bounce left to find; the estimators are measuring volatility. They were designed for daily
equity bars where the spread is a meaningful fraction of the daily range, and that condition is
violated here by two orders of magnitude.

**EDGE (Ardia, Guidotti & Kroencke, JFE 161 (2024) 103916) is NOT implemented.** I cannot
reproduce its estimator from memory to the precision this repo requires, and a spread estimator
that is subtly wrong would silently corrupt the cost table everything else rests on. Given Roll
and CS — which I *can* state exactly — fail by 57–103×, an unverified fourth estimator would add
no information. Marked **[NOT IMPLEMENTED]**, not [UNVERIFIED]: nothing was computed.

## 2c — Calibration: not stable, and not needed

**Result: calibration FAILS** [DIUKUR]. Correction factors would be 57× (CS) to 103× (Roll) and
they move with bar frequency — 2.5× between the 1 min and 5 min bars for Roll alone. A factor
that unstable is not a calibration, it is a fit.

**So cost in the book-less hours (UTC 00–11) and over the 6.587-year archive stays
[DIASUMSIKAN], not [DIUKUR].**

**But this costs less than it appears** [DISIMPULKAN]: since the measured spread is a constant
at the tick floor and is 625× smaller than the fee, the quantity the calibration was meant to
extrapolate does not vary. Assuming 0.0157 bps for hours with no book is a safe assumption, not
a guess — it is the tick.

**A separate blocker, stated plainly:** the 6.587-year application was impossible regardless.
`data/vision/binancef/BTCUSDT/aggTrades/` holds **3 day-partitions on disk** (2026-07-30, 07-31,
08-01) [DIUKUR]. The 2,406-day span is *available*, not *ingested* — ~41 GiB and ~2.7 h of
download, which is a write operation and out of scope for a read-only task.

## 2d — Fees, published rates, not measured

**[DIASUMSIKAN — published tariff, never measured from data.]** Source `RESEARCH.md:216`:
"USDT-M perp ~0.02 % maker / 0.05 % taker standard".

| | bps per side |
|---|---:|
| USDT-M perp maker | **2.0** |
| USDT-M perp taker | **5.0** |

These are list rates. Actual tier depends on 30-day volume and BNB/token discounts, which are
account facts this repo has no access to. **No venue fee table exists anywhere in the codebase**
— grep for `FEE|fee_rate|taker_fee|maker_fee` across `btcquant/`, `scripts/`, `dashboard/`
returns zero hits [DIUKUR].

## Deliverable — cost table ready to replace `cost_bps=10`

Round-trip, BTCUSDT perp, clip $1–5k. Fees [DIASUMSIKAN]; spread and slippage [DIUKUR].

| execution | fee (2 sides) | spread crossed | **round-trip total** |
|---|---:|---:|---:|
| taker / taker | 10.0 | 0.0157 | **10.02 bps** |
| maker in / taker out | 7.0 | 0.0079 | **7.01 bps** |
| maker / maker | 4.0 | 0.0000 | **4.00 bps** ᵃ |

ᵃ maker/maker pays no spread but bears queue risk and adverse selection, **neither of which is
modelled here**. Treat 4.00 as a floor that a real maker will not achieve.

**Versus what the repo assumes today:** `backtest.py:84` charges `cost_bps=10 + slippage_bps=2`
= 12 bps per side = **24 bps round-trip**. The measured taker/taker figure is **10.02 bps**, so
**the standing assumption is 2.4× conservative** [DIUKUR vs DIASUMSIKAN]. Conservative is the
right direction, but every EV conclusion in this repo inherits that factor and should be read
knowing it.

**One number carries the section:** spread + slippage round-trip is **0.016 bps** against fees of
**10 bps** — fees are **625×** the entire microstructure cost. The cost model for this instrument
at this size is a **fee model**. Measuring the book refined a term that does not matter.

**`backtest.py:106-111` is now partly stale** [DIUKUR]: it argues a data-derived spread would be
look-ahead *and* that "btc-quant stores no historical quote/tick series to bound it". The second
clause has been false since M1 — `depth_snapshots` exists and `spread_bps_{b}` is computed at
`orderflow.py:791`. The look-ahead argument still stands and is respected here: every figure
above is a **distribution quantile over a historical window**, never a contemporaneous fill
spread.

## What I could not measure in §2, and why

- **`2026-07-13`.** One of 27 partitions failed to read (`InvalidInputException`, ZSTD
  decompression). Excluded from the 26-day figures and counted here rather than dropped.
- **The shape of the spread tail.** p99 is 0.0162 bps but the observed max is 5.7643 bps, so
  there is a tail this section characterises only by its maximum. What drives it — thin book,
  venue outage edge, a bad snapshot — is not established.
- **Per-hour spread on okx and bybit.** Only binancef was scanned across 26 days; the
  three-venue comparison rests on 2026-08-03 alone.
- **EDGE.** Not implemented — see 2b.
- **Anything over the 6.587-year archive.** 3 of 2,406 days are on disk.
- **Real fee tier.** An account fact, not a data fact.
- **Adverse selection and queue position for the maker cases.** L2 snapshots carry neither; the
  4.00 bps maker/maker row is a floor, not an estimate.
- **Sell-side slippage.** Only the ask ladder was walked. The book is symmetric at the tick
  floor so the bid side is expected to match, but it was not measured.
- **Whether one day is representative.** 2026-08-03 only, for the venue comparison and the
  depth walk.

---

# §4 — The EV gate

Done before §1 because it needs no new data — §2's cost and §0b's volatility are enough — and
because it decides whether §1 is worth doing at all, and for which horizons.

## 4a — Cost versus movement, measured directly

*Source: `scratchpad/eda/sec.duckdb`, **1,936,206 second-bars** of binancef last-trade price over
all 30 recorded days (30/30 partitions read). Pairs `(t, t+h)` where both seconds carry a bar;
~1.85 M pairs at short horizons, 1.55 M at 1 d.*

`|log return|` quantiles, **bps**, measured at each horizon — **not sqrt-t scaled** [DIUKUR]:

| horizon | n pairs | p50 | p75 | p90 | p99 |
|---|---:|---:|---:|---:|---:|
| 1 s | 1,850,001 | 0.02 | 0.02 | 0.70 | 2.26 |
| 5 s | 1,849,398 | 0.18 | 0.99 | 1.96 | 4.84 |
| 30 s | 1,848,307 | 1.41 | 2.92 | 5.05 | 11.65 |
| 1 m | 1,847,764 | 2.11 | 4.19 | 7.13 | 16.47 |
| 5 m | 1,842,811 | 4.97 | 9.59 | 15.90 | 36.72 |
| 30 m | 1,828,105 | **12.30** | 23.23 | 38.27 | 87.07 |
| 1 h | 1,816,135 | 17.14 | 32.41 | 54.39 | 131.23 |
| 4 h | 1,753,949 | 37.79 | 70.42 | 107.01 | 237.90 |
| 1 d | 1,554,255 | 102.09 | 183.67 | 287.46 | 397.06 |

**The median move first exceeds the 10.02 bps taker round-trip at 30 minutes.** At 30 s it is
1.41 bps — **one seventh of the toll**. Even the p99 at 30 s (11.65) barely clears it.

### You were right to forbid sqrt-t, but the error is at the short end, not the tail [DIUKUR]

Scaling the measured 1 m mean from `E|r|` by `sqrt(h/60)`:

| horizon | sqrt-t predicts | measured | ratio |
|---|---:|---:|---:|
| 1 s | 0.41 | **0.19** | **2.13×** |
| 5 s | 0.91 | 0.68 | 1.33× |
| 30 s | 2.22 | 2.16 | 1.03× |
| 1 h | 24.33 | 24.72 | 0.98× |
| 1 d | 119.18 | 126.45 | 0.94× |

sqrt-t is accurate to within 6 % from 30 s outward — the fat-tail underestimate at 1 d is only
6 %. But at **1 second it overstates the move by 2.13×**, because at that scale price mostly does
not move at all (median 0.02 bps ≈ the tick). Had this been scaled rather than measured, the
scalping case would have looked **twice as good as it is**.

## 4c — The geometry, with no literature assumption at all

The strongest available form of the argument needs no edge-decay curve, so none is assumed.

**Perfect-foresight bound.** If a signal predicted direction with 100 % accuracy, it would earn
`E|r| − C` per round trip. EV > 0 therefore requires **`E|r| > C`** — an absolute ceiling on any
strategy whatsoever, no matter how good.

| horizon | E\|r\| bps | vs taker 10.02 | vs maker/taker 7.01 | vs maker/maker 4.00 |
|---|---:|---|---|---|
| 1 s | 0.19 | fails 0.02× | fails 0.03× | fails 0.05× |
| 5 s | 0.68 | fails 0.07× | fails 0.10× | fails 0.17× |
| **30 s** | **2.16** | **fails 0.22×** | fails 0.31× | fails 0.54× |
| 1 m | 3.14 | fails 0.31× | fails 0.45× | fails 0.79× |
| 5 m | 7.20 | fails 0.72× | **passes** | **passes** |
| 30 m | 17.43 | **passes** | passes | passes |
| 1 h – 1 d | 24.72 – 126.45 | passes | passes | passes |

**At the 1–30 s horizon, a perfect predictor loses money on taker execution.** At 30 s it would
capture 2.16 bps and pay 10.02 — **−7.86 bps per round trip while being right every single
time.** Cost is a fixed toll; the move at that horizon is a fraction of it. The curves do not
cross in the wrong place — at scalping horizons they never approach each other.

This is why no candidate needed to be tested to answer the scalping question.

## 4b — Required hit rate, `p* = (S + C) / (S(R+1))`

`S` anchored to the **measured p75 `|r|`** at each horizon, not to a round number. `p* ≥ 1` is
marked **IMPOSSIBLE** — a different class of answer from "hard".

**taker / taker, C = 10.02 bps**

| horizon | S (p75) | R=0.5 | R=1 | R=1.5 | R=2 | R=3 |
|---|---:|---|---|---|---|---|
| 1 s | 0.02 | IMPOSSIBLE | IMPOSSIBLE | IMPOSSIBLE | IMPOSSIBLE | IMPOSSIBLE |
| 5 s | 0.99 | IMPOSSIBLE | IMPOSSIBLE | IMPOSSIBLE | IMPOSSIBLE | IMPOSSIBLE |
| 30 s | 2.92 | IMPOSSIBLE | IMPOSSIBLE | IMPOSSIBLE | IMPOSSIBLE | IMPOSSIBLE |
| 1 m | 4.19 | IMPOSSIBLE | IMPOSSIBLE | IMPOSSIBLE | IMPOSSIBLE | 84.8 % |
| 5 m | 9.59 | IMPOSSIBLE | IMPOSSIBLE | 81.8 % | 68.2 % | 51.1 % |
| 30 m | 23.23 | 95.4 % | **71.6 %** | 57.3 % | 47.7 % | 35.8 % |
| 1 h | 32.41 | 87.3 % | 65.5 % | 52.4 % | 43.6 % | 32.7 % |
| 4 h | 70.42 | 76.2 % | 57.1 % | 45.7 % | 38.1 % | 28.6 % |
| 1 d | 183.67 | 70.3 % | 52.7 % | 42.2 % | 35.2 % | 26.4 % |

**The original request — RR 1:1 at a 1–30 s horizon, taker execution — is IMPOSSIBLE at every
cell.** Not difficult: arithmetically impossible, because `S + C > S(R+1)` when `C > S·R`, and at
these horizons the cost exceeds the entire stop distance.

Loosening execution moves the boundary but does not reach the scalping horizon: **maker/taker**
first becomes possible at 30 s only at R=3 (85.0 %); **maker/maker** at 30 s R=3 needs 59.2 %.

## 4d — Feasibility map

Cells with `p* < 60 %` (the loosest defensible reading of what microstructure literature reports
as attainable directional accuracy — labelled **[DIASUMSIKAN]**, it is a screening threshold, not
a measurement):

| execution | earliest feasible horizon | at |
|---|---|---|
| taker / taker | **5 m** | R=3 only (51.1 %) |
| maker / taker | **5 m** | R=2 (57.7 %), R=3 (43.3 %) |
| maker / maker | **30 s** | R=3 only (59.2 %) — and see the caveat |

**Below 1 minute the map is empty for every execution model except one cell**: maker/maker at
30 s with R=3, needing 59.2 %. That cell should not be trusted — **maker/maker is market making,
not scalping**, its 4.00 bps figure excludes queue risk and adverse selection entirely (§2), and
adverse selection is precisely the cost that dominates a passive strategy at a 30-second horizon.

**Written explicitly, as requested: at horizons under 1 minute there is no EV-positive
(R, S, execution) cell that survives an honest cost.** That is the answer to the scalping
question, and it was reached without testing a single signal candidate.

## What I could not measure in §4

- **Path, therefore true barrier-touch probability.** `p*` here is a static EV identity on the
  marginal `|r|` distribution. A real triple-barrier hit rate depends on the path between `t` and
  `t+h` and would be **lower** than the marginal figure suggests (the stop can be touched and
  recovered). So every `p*` above is optimistic; the true bar is higher.
- **Whether pairs span a coverage gap.** Both endpoints are required to exist, but a hole between
  them is not excluded. For the marginal distribution this is harmless (the price really did
  move that much); for path-dependent work it is not.
- **Overlapping pairs.** All `(t, t+h)` pairs are used, so the effective independent sample is far
  smaller than the ~1.85 M row count — especially at 1 d, where 1.55 M pairs come from ~29
  independent days. The quantiles are descriptive; **no confidence interval is claimed**.
- **Any venue but binancef.** okx and bybit tape coverage is 36–52 %, so their return
  distributions were not measured.
- **The 60 % screening threshold in 4d.** [DIASUMSIKAN] — a reading of literature, not a
  measurement from this data.

---

# §4-corr — The selectivity gap (correction to §4)

§4's perfect-foresight bound proved that a predictor trading **every** window loses at short
horizons. A perfect predictor would be **selective**, and `E|r| = 2.16 bps` at 30 s is an
**unconditional** mean. §4's "the map is empty below 1 minute" is therefore a claim about
**non-selective** strategies only. This section closes that gap.

## Qualifying windows and conditional capture [DIUKUR]

*Same source as §4a: 1,936,206 second-bars, ~1.85 M overlapping pairs per horizon.*

| horizon | execution | P(\|r\| > C) | E[\|r\| \| >C] | capture |
|---|---|---:|---:|---:|
| 1 s | taker | **0.008 %** | 14.36 | 4.34 |
| 1 s | maker/maker | 0.166 % | 5.79 | 1.79 |
| 5 s | taker | 0.082 % | 14.87 | 4.85 |
| 5 s | maker/maker | 1.780 % | 5.80 | 1.80 |
| **30 s** | **taker** | **1.636 %** | 14.36 | 4.34 |
| 30 s | maker/taker | 4.645 % | 10.38 | 3.37 |
| **30 s** | **maker/maker** | **15.477 %** | 6.74 | 2.74 |
| 1 m | taker | 4.439 % | 14.80 | 4.78 |
| 5 m | taker | 23.510 % | 17.75 | 7.73 |
| 30 m | taker | 57.212 % | 26.95 | 16.93 |

**Your prediction was near-exact for maker and too pessimistic for taker** [DIUKUR vs
DIASUMSIKAN]. You guessed taker ~0.1–0.5 % and maker ~14 %. Measured at 30 s: maker/maker
**15.5 %** (essentially your number); taker **1.636 %**, which is **3–16× more open** than you
expected.

## Frequency implication — the door is not arithmetically shut

Non-overlapping windows per year × P(qualifying) × capture:

| horizon | execution | trades/yr | annual return, perfect selective foresight |
|---|---|---:|---:|
| 1 s | taker | 2,506 | **109 %** |
| 30 s | taker | 17,201 | **746 %** |
| 30 s | maker/maker | 162,696 | 4,456 % |
| 1 m | taker | 23,332 | 1,115 % |
| 5 m | taker | 24,714 | 1,911 % |

**§4's conclusion is corrected: short horizons are closed for a NON-SELECTIVE strategy, not
closed in principle.** Even at 1 second, a predictor that could pick the 0.008 % of windows that
move more than the toll would earn 109 %/yr.

**What this does and does not mean.** The bound now requires foreknowledge of **two** things —
which windows will move enough, *and* the direction. That is a strictly harder problem than
direction alone, and the per-trade capture is small in absolute terms (4.34 bps at 30 s taker),
so a real signal capturing even a tenth of the bound needs to be right about volatility timing
and direction simultaneously, thousands of times a year. **The binding constraint has moved from
arithmetic to predictive skill** — which is exactly the question §1 would have to answer, and it
is no longer answerable from the price process alone.

## The door this identifies, recorded and NOT worked on

maker/maker at 30 s admits 15.5 % of windows [DIUKUR] — by far the most open cell. **But its
4.00 bps cost excludes queue risk and adverse selection entirely** (§2), and adverse selection is
precisely the dominant cost of a passive strategy at a 30-second horizon. So the binding
constraint there is **adverse selection, not spread**.

[DISIMPULKAN] That is the constraint **queue imbalance and microprice (Stoikov) are the natural
predictors of**, and this instrument is **large-tick** (§2: spread pinned at 1 tick 99.90–99.96 %
of the time), which is the regime where mid-price is a poor state variable and those two
variables are the right ones. **Recorded as an identified door. Not opened, not measured, not
proposed.**

---

# §4-corr-B — The 45-trial dispute, recorded undecided

Written **before** any result depends on it. That is what makes it usable later.

**PRO 45.** The counting rule was fixed in advance, §4 examined forward returns, and the
conservative direction is the one that cannot be argued into after the fact.

**CONTRA.** The Bailey–López de Prado construction that `deflated_sharpe_ratio`
(`risk.py:592`) implements counts a trial as a **selection among strategy candidates**. §4
selected nothing — it computed a bound and reported the **entire map, dead cells included**.
Reporting a full map is the opposite of selection; it is what prevents selection.

**THE STAKE — and your figures are wrong here, in the direction that weakens your own case for
worrying about it** [DIUKUR, `risk.min_backtest_length`]:

| N | MinBTL |
|---:|---:|
| 2 | 974 d |
| 3 | 940 d |
| 5 | **985 d** |
| 7 | 1,024 d |
| 20 | 1,151 d |
| 45 | **1,243 d** |
| 47 | 1,248 d |
| 100 | 1,328 d |

You cited N=45 → 2,330 d and a 3.8-year stake. The measured value is **1,243 d**, so the real
stake between N=5 and N=45 is **258 days ≈ 0.7 years**, not 3.8. Marginal costs are also smaller
than you stated: **N=5→7 is +39 days** (you said +206) and **N=45→47 is +5 days** (you said +26).

**Your conclusion survives the correction and is strengthened**: a marginal trial at N=45 costs
5 days. Note also that MinBTL is **non-monotonic at small N** — N=3 (940 d) is *below* N=2
(974 d), a property of the Gumbel approximation in `expected_max_sharpe_ratio`.

**Left UNDECIDED and flagged for separate adjudication.** The count stands at 45 in the running
tally until adjudicated. Recorded here, before any dependent result, so that whichever way it is
settled the record shows it was not settled to suit an outcome.

---

# §4bis — Auditing old verdicts against the corrected cost

`backtest.py:84` charges 24 bps round-trip; §2 measures 10.02. A verdict reached with the wrong
cost is as untrustworthy as an acceptance reached with the wrong cost. **This is not an attempt
to revive anything** — it is a check for verdicts standing on a wrong number.

**First, a correction to the premise** [DIUKUR]: not every rejection used 24 bps. The
order-flow runlog scored `sign(ΔCVD)` at **12 bps round-turn**
(`RESEARCH-orderflow-runlog.md:235-236, :386`), which is only **1.20×** the measured 10.02 — not
2.4×. The 2.4× factor applies to the daily strategies that went through the standard harness.

## Method — an upper bound, so no predictive trial is spent

Instead of re-scoring anything, bound the best case. Cost drag on annualised Sharpe is
`turnover_per_year × C / σ_annual`, so a cost cut of `δ` can improve Sharpe by at most

```
ΔSharpe_max = turnover_max × δ / σ
```

with `turnover_max` set to its **arithmetic ceiling**: a position that flips every 2 bars, i.e.
182 round-trips/year on daily bars and 4,380 on hourly. No strategy can exceed that. σ = **0.3407**
annualised, derived from the measured 4.7 bps/minute RV in §0b — not assumed.

If a verdict survives even at that ceiling, it is closed without examining a single return.

## Results [DIUKUR arithmetic on DIUKUR inputs]

| candidate | bar | cost old→new | ΔSharpe max | SR → best case | verdict |
|---|---|---|---:|---|---|
| `vwap_reversion` 48, k=2 | daily | 24 → 10.02 | +0.749 | −0.69 → **+0.06** | **CLOSED — stands** |
| `sign(ΔCVD)` 3/6/12/24 h | hourly | 12 → 10.02 | +2.545 | −3.1 → **−0.55** | **CLOSED — stands** |
| `donchian_breakout` 55/20 | daily | 24 → 10.02 | +0.749 | 0.45 → **1.20** | **NOT CLOSED** |
| `ma_trend` + `fixed_r_exit` 2:3 | daily | 24 → 10.02 | +0.749 | 0.64 → **1.39** | **NOT CLOSED** |

**Both candidates you flagged survive, and two you did not flag do not.** That inversion is the
finding.

`vwap_reversion` at its theoretical ceiling reaches +0.06 — an order of magnitude below the 0.95
bar, and reaching even that would require a 48-day VWAP band to flip position every other day,
which it cannot do by construction. `sign(ΔCVD)` stays negative even at 4,380 round-trips/year,
and if it *did* turn over that fast the residual 10.02 bps cost would itself impose 12.9 Sharpe
points of drag.

`donchian_breakout` and `ma_trend + fixed_r_exit` are **not excluded by this bound**. Their
actual turnover is certainly far below the ceiling — `ma_trend_filter` is reported at **8 trades**
over the whole window (`RESEARCH-tharp-runlog.md:37`), which would make ΔSharpe ≈ 0.004 —
but that figure is for the base signal, not for these two overlays, and **no stored artifact
carries their trade counts** (`reports/`, `data/*.json` checked: only `ma_trend_filter` and
`tsmom` are stored) [DIUKUR].

**Open item, with its price stated.** Closing these two requires their OOS trade counts, and the
only way to produce them is to re-run the harness — which **examines forward returns and
therefore costs 2 predictive trials**. That is a decision, not a detail: 2 trials raise
`N_trials` for every hypothesis in this programme and lower every DSR. I have not spent them.
My judgement is that both will survive comfortably, but **judgement is not the standard here**,
so they are recorded as NOT CLOSED rather than waved through.

## What I could not measure in §4bis

- **Actual turnover for `donchian_breakout` and `ma_trend + fixed_r_exit`.** No stored artifact;
  producing it costs 2 predictive trials. Left unspent.
- **Whether σ = 0.3407 is the right denominator for each strategy.** It is BTC's own annualised
  volatility; a strategy that is flat part of the time has lower return volatility, which would
  make ΔSharpe_max *larger*. So the bound is not conservative in that direction, and the two
  NOT CLOSED rows could be worse than shown, never better.
- **`tsmom` at DSR 0.9451**, the board's top entry. It sits 0.005 below the bar and was not in
  scope here, but by the same arithmetic it is the most cost-sensitive verdict in the repo. Not
  examined.

---

# §4bis-B — Full re-score under the corrected cost

**Declared before running: 16 candidates.** Every one is reported below, including the ones that
fail. That is what makes this a bug fix rather than a search — the distinction is not intent, it
is whether the failures appear.

**Trial classification: ZERO new predictive trials.** These candidates were already scored; only
the cost constant changed, and the *entire* pre-existing set was re-run. Had a subset been run,
or had failures been omitted, it would be a fresh search and would have to be counted. It is
recorded this way so the claim can be checked against the table.

Cost: old `10.0 + 2.0` bps/side (24.0 round-trip) → corrected `5.0 + 0.008` bps/side
(**10.02 round-trip**, from §2). Data: BTC-USD 1d, 2018-01-01 → 2026-08-04, 3,138 bars,
5 walk-forward folds.

## Board (N=8), OOS DSR [DIUKUR]

| strategy | DSR @24 | DSR @10.02 | Δ | beats B&H | #T |
|---|---:|---:|---:|---|---:|
| tsmom | 0.89 | **0.93** | +0.04 | yes | 141 |
| tsmom_voltarget | 0.89 | **0.93** | +0.04 | yes | 141 |
| tsmom_dir | 0.83 | 0.87 | +0.04 | yes | 141 |
| tsmom_ls | 0.69 | 0.80 | **+0.11** | no → **yes** | 283 |
| buy_and_hold | 0.72 | 0.72 | 0.00 | — | 1 |
| ma_trend_filter | 0.64 | 0.65 | +0.01 | no | 8 |
| pairs_ou | 0.03 | 0.06 | +0.03 | no | 69 |
| pairs_coint | 0.00 | 0.00 | 0.00 | no | 14 |

**Nothing crosses 0.95.** Top is `tsmom` at 0.93. `PBO` improves 0.61 → **0.53**, still above the
0.50 noise line. One status change: **`tsmom_ls` now beats buy-and-hold** where it did not before
— real, but at DSR 0.80 it is not promotable.

## Tharp P5 sweep, OOS DSR (folds) [DIUKUR]

| candidate | DSR @24 | DSR @10.02 | SR @10.02 | #T | verdict |
|---|---:|---:|---:|---:|---|
| donchian 55/20 | 0.20 | 0.21 | 0.46 | 34 | **KILL — stands** |
| vwap_reversion 48 | 0.00 | 0.00 | −0.68 | 38 | **KILL — stands** |
| ma_trend + fixedR 2:3 | 0.55 | 0.59 | 0.68 | 79 | **KILL — stands** |
| random_entry (control) | 0.10 | 0.11 | 0.33 | 30 | **KILL — stands** |

**This resolves the two items §4bis left NOT CLOSED.** `donchian` and `ma_trend + fixedR` both
stand comfortably. The analytic bound was loose because it assumed the turnover ceiling: actual
turnover is **34 and 79 trades over 8.6 years** (≈4 and 9 per year), roughly 20–45× below the
182/year ceiling the bound had to assume.

## mean_reversion sweep, OOS DSR [DIUKUR]

| granularity | variant | DSR @24 | DSR @10.02 | SR @24 | SR @10.02 | verdict |
|---|---|---:|---:|---:|---:|---|
| 1d | ungated | 0.00 | 0.01 | −0.32 | −0.27 | **KILL — stands** |
| 1d | gated | 0.00 | 0.00 | −0.64 | −0.59 | **KILL — stands** |
| 1h | ungated | 0.00 | 0.00 | −2.43 | **−1.03** | **KILL — stands** |
| 1h | gated | 0.00 | 0.01 | −2.33 | **−1.14** | **KILL — stands** |

The hourly Sharpes improve markedly (−2.43 → −1.03) because hourly turnover is high and the cost
correction bites hardest there — but DSR stays at 0.00–0.01. The verdict was never close.

## The one verdict that WAS standing on the wrong number

**`pairs_ou` (Part B, B2): KILL → SURVIVES** [DIUKUR].

| | old (24 bps) | corrected (10.02 bps) |
|---|---|---|
| DSR vs fixed-z pairs | 0.03 vs 0.00, **Δ 0.02** | 0.06 vs 0.00, **Δ 0.05** |
| PBO board → +pairs_ou | 0.67 → 0.54 | 0.56 → 0.47 |
| kill criterion `Δ<+0.05 OR PBO worsens` | **KILL** | **SURVIVES** |

**Read this precisely, because it is easy to misread as an edge.** "SURVIVES" here means it
clears its own **relative** criterion — *is the OU model better than a fixed z-score* — and
nothing more. `pairs_ou` sits at **DSR 0.06**, which is **0.89 below the promotion bar**. It is
not tradeable, not promotable, and not a finding about profitability.

It also lands **exactly on the Δ = 0.05 threshold**, so it is marginal in the extreme: a hair
either way flips it back.

What it *does* establish is the thing the audit was for: **one recorded verdict was standing on a
cost that was 2.4× too high.** The original run-log conclusion — "a model, not an edge; OU params
non-stationary" — is no longer supported by the number that produced it, and
`RESEARCH-partB-runlog.md` should carry an amendment noting the re-score.

## What I could not measure in §4bis-B

- **`sign(ΔCVD)` was not re-scored.** Its harness (`scripts/orderflow_smoke.py`) refuses to score
  by design at 1.8 % of MinBTL, and §4bis closed it analytically at its own 12 bps round-turn
  assumption. Excluded with reason, not omitted.
- **Whether `pairs_ou`'s flip survives its own PBO threshold.** `PBO < threshold` is unbound
  prose in `STRATEGY.md:803` — no numeric threshold exists in code, so "PBO worsens" was the only
  testable form.
- **Any candidate not already carrying a recorded verdict.** By construction: adding one would be
  a new trial, and the declared set was fixed at 16 before the first run.

---

# §5 — Swing, framed (not yet analysed)

§4 put swing on the critical path: the EV gate is wide open at daily horizon, and §4bis-B found
one daily verdict that had been standing on the wrong cost.

## The gate is not the constraint here [DIUKUR, §4b]

At 1-day horizon with S = p75 = 183.67 bps, required hit rates are **52.7 % at R=1** and
**35.2 % at R=2** for taker execution. Compare 30 s, where R=1 is arithmetically impossible.
**Cost is a rounding error at this horizon**: 10.02 bps against a median daily move of 102 bps
is under 10 %.

Carry the §4 caveat: these `p*` values **ignore path** and are therefore **optimistic**. The true
barrier-touch bar is higher, and the daily figures inherit that exactly as the intraday ones do.

## Data is not the constraint either [DIUKUR]

| span | MinBTL(5) | MinBTL(8) | MinBTL(20) | MinBTL(100) |
|---|---:|---:|---:|---:|
| daily OHLCV, 8.6 yr | **319 %** | **302 %** | **273 %** | **236 %** |
| Vision aggTrades, 6.59 yr | 244 % | — | 209 % | 181 % |

**The daily board already clears MinBTL at every trial count up to 100.** No new data is needed
to score a daily hypothesis. The Vision archive is *not* what unblocks swing — it was never the
blocker.

## So what IS the constraint

**The edge.** 8.6 years of data, a wide-open EV gate, MinBTL cleared at 302 %, corrected costs —
and the best candidate is `tsmom` at **DSR 0.93**, still short of 0.95, with **PBO 0.53** saying
the ranking itself is barely distinguishable from noise [DIUKUR, §4bis-B].

[DISIMPULKAN] That is not a data problem or a cost problem. Every feature the board uses is
**OHLCV-derived** — `features.py` contains no microstructure at all (§ survey: no spread, no
imbalance, no order-flow anything). The board has been searching one feature family for 8.6 years
of data and has not cleared the bar.

## What would actually be new — and what it costs

**Trade-derived features over the Vision span.** `orderflow.py` already emits CVD, signed delta,
size-bucketed delta (≤10k / ≤100k / ≤1M / whale) and VPIN from **trades only**, so they are
computable over the full 2,406 days — **209 % of MinBTL(20)** [DIUKUR]. Those are features the
board has never had, on a span that clears the bar, at a horizon where the EV gate is open.

That is the one combination in this whole document where all three constraints are simultaneously
satisfied.

**Cost to unlock: ~41 GiB / ~2.7 h of download** (`DEVELOPMENT.md:166-171`), landing as ~13 GB of
parquet. Currently **3 of 2,406 days are on disk** [DIUKUR].

**BLOCKED, by dependency, not by choice.** The ingest saturates the machine, and a busy machine
does not sleep — which would make the host stay awake for the wrong reason and **void control P5**
of the running sleep experiment (`reports/incident-2026-08-04-sleep/prediction.md`). Scheduled
**after** the T+6 measurement, or after the experiment is formally deferred.

## Candidate shape, stated but NOT pre-registered

Any daily trade-derived candidate must confront `RESEARCH-ic-runlog.md` first: **no board
strategy shows significant forward IC at any horizon**, and `tsmom` (the best) carries a
**negative** IC-IR t-stat of −4.01 at k=3. The board's returns come from low-frequency trend and
vol capture, not from bar-to-bar predictive skill. A trade-derived candidate has to beat that
finding, not just the price-only strategies.

**No hypothesis is registered here.** PREREG comes after the re-score results are reviewed, per
the standing sequence.

## What I could not measure in §5

- **Anything about actual trade-derived daily performance.** Vision is not ingested; 3 days on
  disk cannot score anything.
- **Whether daily trade-derived features carry information the price-only board lacks.** That is
  the hypothesis, and testing it is exactly what a PREREG would have to pre-commit to.
- **The path-corrected `p*` at daily horizon.** Same limitation as §4: marginal distribution
  only, so the reported hit-rate bars are optimistic.
- **Whether `pairs_ou`'s flip changes the board's PBO materially.** Measured 0.56 → 0.47 with it
  added, but no numeric PBO threshold exists to judge that against.

---

# §6 — PBO diagnosis, and the trial-clustering method DECLARED IN ADVANCE

## 6a — There is no sweep. The premise of "sweep width explains PBO" is refuted [DIUKUR]

The working hypothesis was that `tsmom`'s PBO 0.53 might be explained by sweeping hundreds of
configurations. **It cannot be, because nothing is swept.**

- Board strategies take their parameters **straight from CLI defaults** — `--ma-n 200`,
  `--ma-fast 50`, `--lookback 20`, `--target-vol 0.15` (`scripts/compare.py:149-155`, used
  verbatim at `:99-119`). One configuration per strategy, per run.
- **No strategy performs internal parameter search.** Every loop in `strategies.py` is a bar
  loop, not a configuration loop (grep for `argmax|best_|optimi[sz]`: zero hits).
- `probability_of_backtest_overfitting` is fed the **OOS-returns matrix across strategies**
  (`compare.py:280`), and the code says so in its own comment: *"PBO across the OOS-returns
  matrix (**cross-strategy selection overfit**)"*. `n_blocks=8` → **C(8,4) = 70 CSCV splits**,
  matching the printed "CSCV 70 splits".

**So PBO 0.53 measures selection among 8 fixed strategies, not parameter overfitting.** The
question "how does PBO move with sweep width across the 16 candidates" (2b) **has no variable to
correlate against** and is not answerable as posed.

**What it means instead, and it is worse** [DISIMPULKAN]: picking the in-sample-best of these 8
strategies lands you below the OOS median **more than half the time**. With no parameter search
to blame, the remaining explanation is that the 8 strategies are **not distinguishable from each
other** — which is exactly what §7 below tests.

**One uncounted trial source, stated because it is real** [DISIMPULKAN]: the defaults themselves
(200/50/20/0.15) were chosen at some point, and that selection history is **not recorded
anywhere**. Those are trials that never entered any `N`. This is not measurable from the repo as
it stands — see "what I could not measure".

## 6b — Parameters that could be locked from theory rather than searched

Listed only, **not implemented**, with the basis for each.

| parameter | current | lockable basis |
|---|---|---|
| `--target-vol 0.15` | 0.15 | A risk *choice*, not an estimate — set by mandate, never fitted. Already effectively locked; it should be documented as a constant, not a parameter. |
| `--ma-n 200` / `--ma-fast 50` | 200/50 | The 200/50 pair is the industry convention, not a fit. Locking it *by convention* is defensible; searching around it is not. |
| `--lookback 20` | 20 | The weakest case. Momentum horizon is the one genuinely free parameter here, and the literature spans 1–12 months without a canonical value. **Cannot be locked from theory — this is the one that should carry a declared trial count if it is ever varied.** |
| CSCV `n_blocks` | 8 | Methodology, not strategy. Fixed by the harness; varying it to move PBO would be the purest form of the thing PBO exists to detect. |

---

# §7 — Effective trial count: method declared BEFORE any result

**This declaration is written and saved before the correlation matrix is computed.** That
ordering is the only thing that makes the exercise meaningful: if the clustering method could be
chosen after seeing which one lifts `tsmom`, the whole thing is theatre.

## Declared method, fixed now

**7a — correlation.** Pearson correlation of the **OOS return series** across the candidate set,
recomputed through `walk_forward` at the corrected cost (5.0 + 0.008 bps/side).

**7b — two independent estimates of effective N, both reported:**

1. **Hierarchical.** Correlation distance `d_ij = sqrt(0.5 · (1 − ρ_ij))` (López de Prado, AFML
   ch.4), **average linkage**, cut with `criterion='distance'` at **t = 0.5**, which is exactly
   the distance corresponding to **ρ = 0.5**. `N_eff` = number of clusters. The threshold is a
   fixed, pre-stated value — **not tuned**, and not chosen by inspecting a dendrogram.
2. **Spectral.** The eigenvalue participation ratio `(Σλ)² / Σλ²`, via the repo's own
   `risk.effective_number_of_trials` (`risk.py:759`) — already implemented and parity-pinned, so
   no new estimator is being introduced.

**If the two disagree by more than 2×, that disagreement is itself reported as the finding**, and
neither is preferred.

**7c — recompute.** `deflated_sharpe_ratio` at each `N_eff`, for **every** candidate, reporting
those that fall as well as those that rise.

## Is §7 a predictive trial? My argument: NO — with a caveat I am naming myself

**It is a denominator correction.** No new return series is examined, no new strategy is
configured, no candidate is added. It changes `N` in `deflated_sharpe_ratio(sr, n, skew, kurt,
n_trials, var)` and nothing else — the same class of operation as the §4bis-B cost re-score.

**The caveat, which is the real risk:** choosing *between* the hierarchical and spectral estimates
**would** be a selection, and picking whichever lifts `tsmom` would be indefensible. That is
precisely why the method is fixed above, both estimates are reported, and the full candidate set
is reported. **The classification of "0 trials" is valid only if all three of those hold** — and
they are checkable against the tables that follow.

## 7a — OOS return correlation, 14 daily candidates, 2,615 bars [DIUKUR]

Selected pairs (full matrix in `scratchpad/eda`):

| pair | ρ |
|---|---:|
| tsmom ↔ tsmom_voltarget | **1.00** |
| tsmom ↔ tsmom_dir | **0.91** |
| buy_and_hold ↔ ma_trend_filter | 0.81 |
| donchian ↔ vwap_reversion | **−0.79** |
| tsmom ↔ tsmom_ls | 0.70 |
| pairs_coint ↔ everything | ≤ 0.20 |

The 1h `mean_reversion` variants are **excluded by construction**, not omitted: they live on a
different bar index and cannot be correlated against daily series. 14 daily candidates is the
correct set.

## 7b — Effective N, both declared methods [DIUKUR]

| | value |
|---|---:|
| nominal N | **14** |
| **hierarchical** (d=√(0.5(1−ρ)), average linkage, cut t=0.5) | **7** |
| **spectral** (participation ratio, `risk.effective_number_of_trials`) | **4.37** |
| ratio | **1.60×** → agree by the pre-declared < 2× test |

Hierarchical clusters, and they are interpretable:

```
1  vwap_reversion_48, meanrev_ungated          (mean-reversion family)
2  meanrev_gated
3  donchian_55_20, random_entry                (breakout ≈ noise)
4  buy_and_hold, ma_trend_filter, ma_trend_fixedR   (long-bias family)
5  tsmom, tsmom_ls, tsmom_dir, tsmom_voltarget      (4 collapse to 1)
6  pairs_coint
7  pairs_ou
```

**`donchian_breakout` clustering with `random_entry` at ρ = 0.53 is its own quiet verdict**
[DISIMPULKAN]: the channel breakout is statistically hard to separate from the coin-flip control.

## 7c — DSR at each N. All 14, those that fall and those that rise [DIUKUR]

| candidate | SR ann | N=14 | N=8 (production) | N=7 hier | N=4.37 spec |
|---|---:|---:|---:|---:|---:|
| **tsmom** | 1.06 | 0.882 | 0.929 | **0.938** | **0.965** ✱ |
| tsmom_voltarget | 1.06 | 0.882 | 0.929 | 0.938 | 0.965 ✱ |
| tsmom_dir | 0.96 | 0.807 | 0.874 | 0.888 | 0.933 |
| tsmom_ls | 0.84 | 0.707 | 0.795 | 0.815 | 0.880 |
| buy_and_hold | 0.78 | 0.624 | 0.724 | 0.748 | 0.828 |
| ma_trend_filter | 0.70 | 0.539 | 0.647 | 0.673 | 0.766 |
| ma_trend_fixedR | 0.68 | 0.530 | 0.638 | 0.665 | 0.759 |
| donchian_55_20 | 0.46 | 0.306 | 0.410 | 0.438 | 0.548 |
| random_entry | 0.33 | 0.195 | 0.280 | 0.305 | 0.408 |
| pairs_coint | 0.11 | 0.073 | 0.120 | 0.135 | 0.204 |
| pairs_ou | −0.08 | 0.025 | 0.047 | 0.055 | 0.093 |
| meanrev_ungated | −0.27 | 0.007 | 0.014 | 0.017 | 0.033 |
| vwap_reversion_48 | −0.68 | 0.000 | 0.001 | 0.001 | 0.002 |
| meanrev_gated | −0.59 | 0.000 | 0.000 | 0.001 | 0.002 |

✱ = above 0.95.

## The verdict: INDETERMINATE, and that is the honest reading

**The two pre-declared methods straddle the bar.** `tsmom` reaches **0.965 under spectral** and
**0.938 under hierarchical** — pass and fail from the same data, the same returns, and two
methods both fixed in writing before the numbers existed.

The declaration said **neither is preferred**. So:

> **`tsmom` does not clear 0.95.** A result that flips on a choice the declaration explicitly
> refused to make is not a pass — it is a measurement whose resolution is below the size of the
> effect being measured.

Reporting it as a pass would require picking spectral *after* seeing that spectral is the one
that passes. That is precisely the move the advance declaration existed to prevent, and the fact
that the temptation is real is the reason it was written down first.

**Two things this does establish** [DIUKUR]:

1. **Nominal N=8 materially over-deflates.** The momentum family is 4 strategies that are 1
   strategy (ρ = 0.91–1.00). Whatever the right N is, **8 is too many**, and 14 would be far
   worse. `tsmom` moves +0.037 from N=8 to N=4.37.
2. **PBO is untouched and remains the harder problem.** `N_eff` corrects the *deflation*
   denominator; it does nothing to **PBO 0.53**, which says selecting the in-sample best of this
   set lands below the OOS median more than half the time. §6a showed that cannot be blamed on
   sweep width. A `tsmom` at DSR 0.965 sitting on PBO 0.53 would be a strategy that clears the
   bar inside a ranking that is itself noise — which is not a state anyone should promote from.

## What I could not measure in §6–§7

- **Whether the parameter defaults (200/50/20/0.15) were themselves searched.** No record exists.
  Those are trials that never entered any `N`, and they are unrecoverable from the repo.
- **Which `N_eff` is correct.** Both estimators are defensible; the data does not adjudicate
  between them, and this exercise deliberately did not.
- **`N_eff` for the 1h `mean_reversion` pair.** Different bar index; not correlatable here.
- **Whether PBO would improve at `N_eff`.** PBO is a rank-stability statistic over the returns
  matrix and does not take `N` as an input — the question is not well-posed as asked.
- **A confidence interval on `N_eff` itself.** Both estimates are point values from one 2,615-bar
  sample; neither carries a standard error here.

---

# §8 — PBO twin hypothesis: method DECLARED BEFORE RUNNING

**Hypothesis.** §6a established that nothing is swept, so PBO 0.53 measures **cross-strategy
selection**. §7 established that 4 of the 8 board strategies are one strategy (ρ 0.91–1.00).
So PBO 0.53 may be largely **four twins swapping rank between CSCV blocks**, not instability of
`tsmom` against genuinely different strategies.

**This is a DIAGNOSIS, not a treatment.** Pruning duplicates to see what PBO does is a
measurement of what the statistic is reacting to. It is not a fix, and a lower number here does
not make the board better — it would only relocate the problem's description.

## Declared before running — representative selection

The tsmom cluster is `{tsmom, tsmom_ls, tsmom_dir, tsmom_voltarget}`. One member represents it.

**Criterion: the STRUCTURALLY SIMPLEST variant — fewest transformations applied to the raw
signal.** By the code's own descriptions (`compare.py:_make_positions_fn`):

| variant | transformations |
|---|---|
| **`tsmom_dir`** | **raw directional momentum, NO sizing** — the code calls it "B1 baseline" |
| `tsmom` | + vol scaling, + target-vol |
| `tsmom_ls` | + vol scaling, + target-vol, + long/short |
| `tsmom_voltarget` | + separate vol_target overlay with max_leverage |

**Representative: `tsmom_dir`.**

Chosen over "closest to cluster centre" deliberately: centre-of-cluster is computed from the
correlation matrix, which is derived from returns and therefore one step closer to the outcome.
Structural simplicity is readable from the **source alone**, verifiable by anyone, and cannot be
influenced by any score. **No DSR was consulted in making this choice**, and the choice is
recorded here before the pruned PBO is computed.

## Declared before running — thresholds

| result | verdict |
|---|---|
| PBO **< 0.35** | **"drops sharply"** — a ≥0.18 absolute fall and clearly below the 0.50 noise line |
| PBO **≥ 0.45** | **"holds"** — still at or near the noise line |
| 0.35 ≤ PBO < 0.45 | **indeterminate** — neither claim is made |

## Declared before running — interpretation

- **Drops sharply** → PBO 0.53 was largely an artefact of duplication. The board's rank
  instability was mostly four copies of one strategy trading places, and cross-strategy
  selection among *genuinely different* strategies is more stable than 0.53 suggested.
- **Holds ≈ 0.5** → the daily board contains **no candidate distinguishable from noise**, and
  the entire swing path needs review before any PREREG is written on it.
- **Indeterminate** → no claim; the question needs a longer sample, not a re-reading.

## Trial classification: 0 predictive trials — argued, not assumed

Recomputing PBO on a **subset of already-scored return series** examines no new returns,
configures no new strategy, and promotes nothing. It is a diagnostic *of the statistic*, in the
same class as the §7 denominator correction.

**The risk that would invalidate that classification**, named here so it can be checked: if the
pruned PBO were used to argue the board is now acceptable, the pruning choice would have become
a lever on a verdict. It is not, and cannot be — **pruning is not applied to the board, no
candidate's status changes, and `tsmom` remains NOT CLEARED** under §7's rule regardless of what
this number turns out to be.

## Result [DIUKUR]

### Positive control — passed, but only on the second instrument

A cached OOS-returns matrix from earlier in the session gave board PBO **0.5571**, which does
not reproduce the documented **0.53**. That cache was discarded: its provenance (cost setting,
strategy set) was never recorded, and one of its numbers — 0.5571 — happens to equal the *real*
5-column PBO, which is exactly the kind of coincidence that passes a careless check.

The instrument actually used runs `scripts/compare.py --research --cost-bps 5.01
--slippage-bps 0` **unmodified**, with `backtest.walk_forward` and
`risk.probability_of_backtest_overfitting` wrapped at import time to capture what compare.py
itself computes. All three documented PBO numbers reproduce to the digit:

| call | columns | PBO | document |
|---|---:|---:|---|
| leaderboard board | 8 | 0.5286 | **0.53** ✓ |
| B2 `SPOT_STRATS` | 5 | 0.5571 | **0.56** ✓ |
| B2 `+ pairs_ou` | 6 | 0.4714 | **0.47** ✓ |

### The declared test

| board | PBO |
|---|---:|
| full, 8 strategies | 0.5286 |
| pruned — 4 twins → `tsmom_dir` | **0.5143** |
| Δ | **−0.0143** |

**Against the declared thresholds: PBO ≥ 0.45 ⇒ HOLDS.** The twin hypothesis is **not**
supported. Removing three duplicate columns moved PBO by 1.4 points, one CSCV combination out
of 70.

### My declared thresholds had a defect — stated, not quietly dropped [POST-HOC]

The declaration assumed PBO's null is 0.50 **regardless of column count**. It is not. Under 300
replications of a pure-noise board at the same `T = 2615` and σ:

| board width | null median | sd | 5–95 % band |
|---:|---:|---:|---|
| 8 columns | 0.500 | 0.244 | [0.129, 0.914] |
| 5 columns | **0.657** | 0.274 | [0.114, 0.957] |

So a full-vs-pruned comparison spans **two different nulls**: narrowing 8 → 5 columns should
*raise* PBO by ~0.16 under pure noise. Measured against its own null the pruned board is 0.14
**below** expectation while the full board is 0.03 above — but sd is 0.25, so that is half a
standard deviation and means nothing. **The verdict does not change**: HOLDS, by the thresholds
as written.

### The finding that matters more than the declared one [POST-HOC, DESCRIPTIVE]

**PBO has no resolving power at this sample.** Three independent views:

- **Noise null:** the 90 % band for a pure-random board is [0.13, 0.91]. **Both 0.53 and 0.51
  sit inside it.** Neither is distinguishable from a board of random columns.
- **Representative choice:** swapping which twin represents the cluster moves PBO across
  `tsmom` 0.4286 · `tsmom_voltarget` 0.4286 · `tsmom_dir` 0.5143 · `tsmom_ls` 0.7286 — a
  **0.30 range** driven by a choice that is supposed to be immaterial.
- **Block count:** holding the board fixed and varying `S` gives 0.700 / 0.529 / 0.373 / 0.500
  for S = 6 / 8 / 10 / 12 — a **0.33 range** from a free parameter.

And the direct null control: across all `C(8,5) = 56` five-column subsets, median PBO is 0.5500
(min 0.2857, max 0.8286). The deliberately-deduplicated subset sits at the **38th percentile** —
statistically ordinary. Deduplication bought nothing that dropping three arbitrary columns
would not also have bought.

### Consequence for the promotion bar

`STRATEGY.md:804` requires `DSR > 0.95` **AND** `PBO < threshold` **AND** `history ≥ MinBTL(N)`.
§4bis already recorded that no numeric PBO threshold exists in code. This section adds the
harder fact: **with sd ≈ 0.25, no threshold on PBO could resolve anything at this sample.** The
clause is currently unmeasurable, not merely unbound. Any future bar must either raise `T`
substantially, report a PBO **confidence interval** rather than a point estimate, or drop the
clause and say so.

### This does not rescue anything

PBO is removed as evidence **in both directions**. `tsmom` remains **NOT CLEARED**: its DSR 0.93
is below 0.95, and §7's verdict rested on the trial-count denominator, not on PBO. Nothing here
touches either. The pruning was never applied to the board, and no candidate's status changed.

---

---

# §9 — where the default parameters came from [DIUKUR]

The concern: `--ma-n 200`, `--ma-fast 50`, `--lookback 20`, `--target-vol 0.15`
(`scripts/compare.py:152-155`) have no recorded selection history, so they could be **uncounted
trials** — and if they were chosen by looking at results, the whole board would rest on selection
that was never deflated. That is the right worry. It does not survive the archaeology.

## What the history shows

`git log -S` on each literal, across `scripts/compare.py` and `btcquant/strategies.py`, traces
all four to the **root commit `8c9bdb6` (2026-06-13)**. That commit's message contains no
parameter justification — but `RESEARCH.md` is **in the same commit**, and already contained the
convention text:

| default | cited convention, present at the root commit | where it sits |
|---|---|---|
| `ma_n = 200` | `RESEARCH.md:45` — "`n = 200 days` (or 10 months)" | **exactly** the stated value |
| `ma_fast = 50` | `RESEARCH.md:45` — "dual cross `50/200d` (\"golden cross\")" | **exactly** the stated value |
| `lookback = 20` | `RESEARCH.md:33` — "`L = 1 day … 4 weeks` (crypto sweet spot)" | 20 trading days ≈ 4 weeks, top of the band |
| `target_vol = 0.15` | `RESEARCH.md:33` "vol target 10-15%" ∩ `:156` "target vol 15-50% for BTC" | the **only value in both** cited ranges |

`RESEARCH.md` cites external work — Harvey et al. (2018), Shen-Urquhart-Wang (2022), Grayscale
(2024) — that is independent of this repo's data and predates it.

## Verdict, and the residual that stays open

**These are convention-sourced, not result-selected, and they add 0 to the trial count.** The
count that matters for DSR/MinBTL is *how many configurations were scored on THIS data with the
best kept*. Parameters lifted from published convention never entered that competition.
Two details make the reading harder to argue with, and neither was chosen by me:

- `target_vol = 0.15` is the **unique intersection** of two independently cited ranges. A
  result-fitted value would have no reason to land there.
- `lookback = 20` and `target_vol = 0.15` sit at the **least flattering** end of their bands —
  the shortest formation window (most turnover, most cost-punished) and the lowest vol target
  (least leverage). Fitting to results does not produce that pattern.

**The residual, stated plainly:** the code and the convention text landed in the **same commit**,
so the doc cannot be *proven* to predate the code, and no pre-repository history exists to rule
out an offline sweep before 2026-06-13. **If those four numbers were in fact chosen by looking at
results, every board verdict rests on a selection that was never deflated** — that sentence stays
on the record. What the archaeology establishes is that the four values match published
convention exactly, which is not the signature a sweep leaves.

A second-order exposure remains and is **not quantified here**: published conventions are
themselves the product of selection in the literature. That is a real inheritance, it is not
measurable from this repo, and it is a far weaker exposure than in-sample fitting.

## What I could not measure

- **Whether any parameter search happened before the repository existed.** No artefact survives.
- **The literature's own selection bias**, inherited through any convention-sourced default.

---

# §10 — tape loss measured against GROUND TRUTH [DIUKUR]

§0 could only estimate tape missingness from the collector's own store — a process cannot
witness its own absence. The Vision ingest changes that: Binance publishes its own complete
`aggTrades` record, so the two can be compared as sets. `tests/test_vision_overlap.py` does
exactly this and it now runs (it skips when the archive day is absent; the 2026-08-03 partition
arrived in today's ingest).

**Overlap day 2026-08-03, binancef aggTrades:**

| quantity | value |
|---|---:|
| archive rows / distinct ids | 1,027,925 / 1,027,925 |
| archive id span | 1,027,925 — **contiguous, no holes** |
| recorded rows / distinct ids | 999,497 / 999,497 |
| **archive \\ recorded (prints the collector never saw)** | **28,428 — 2.77 %** |
| **recorded \\ archive (prints the collector invented)** | **0** |
| on the 999,497 joined: ts mismatch / max\|Δprice\| / max\|Δqty\| / side mismatch | **0 / 0.0 / 0.0 / 0** |

**Read both halves.** Every field the collector recorded is **byte-exact** against the venue's
own archive across a million rows — fidelity is not in question. What is in question is
**completeness**: 2.77 % of one day's tape was never captured, and nothing alarmed.

**This is the positive control for the `cursor=None` fix, and it arrived independently.** The
loss signature matches the defect exactly: a leg restart reset the aggTrades cursor to `None`,
the loop resumed at the live edge, and the backlog between the last poll and the restart was
dropped — while the `cursor is not None` guard suppressed the id-gap alarm that would have
announced it. The 28,428 missing ids are contiguous in the archive, which is what a
jump-to-live-edge produces and what a network error does not.

**Attribution is decisive on timing.** These rows were written by the **old** code — the
collector has not been restarted, so the fix has never run. The failing test is not a regression
introduced by the fix; it is the measurement that justifies it.

**No backfill.** The 28,428 prints exist in `data/vision/` under their own hive partition with
their own source label. They are **not** merged into `trades`. Gaps stay gaps
(`scripts/check_ticks.py:12-16`); the archive is a second labelled source, not a repair.

## §10a — WHERE the loss falls, and it decides the consequence [DIUKUR]

The 28,428 missing prints resolve into **exactly 4 contiguous id blocks**:

| id range | prints | UTC | duration |
|---|---:|---|---:|
| 3400365711–3400377172 | 11,462 | 00:39:13–01:03:33Z | 24.3 min |
| 3400395947–3400402841 | 6,895 | 01:31:26–01:51:43Z | 20.3 min |
| 3400507843–3400513008 | 5,166 | 05:38:04–05:50:52Z | 12.8 min |
| 3400514462–3400519366 | 4,905 | 05:55:02–06:08:15Z | 13.2 min |

- **UTC 16–22 (NY session, the best-coverage window): 0 of 28,428 — 0.0 %.**
- **UTC 00–07 (off hours): 28,428 of 28,428 — 100 %.**
- 70.6 minutes of tape = **4.91 % of the day**, in 4 outages, not a chronic drip.

**Consequence: small, and §0/§3 do not change.** The loss overlaps the already-documented
off-hours blackout pattern rather than damaging the best-covered hours. Had it fallen in
16–22 UTC the reverse would hold and the coverage claims in §0/§3 would need revision.

**The fix is sized correctly, verified rather than assumed.** The largest hole is 11,462 prints
= **22.9 %** of the declared `_AGGTRADES_SEEK_BACK_MAX = 50,000` ceiling. All four blocks are
under it, so the cursor fix would have **recovered every one with no loss** — the ceiling was
derived from Binance rate limits before this measurement existed and it clears the real failure
mode with 4× headroom.

**Method note, because it nearly went wrong three ways.** The UTC hour profile was computed
three times and the first two disagreed: DuckDB's `strftime` rendered in the session zone
(`Asia/Jakarta`, +7), and a second attempt using `CAST(ts_ms/3600000 AS BIGINT)` **rounded**
instead of truncating, shifting 00:39 into hour 01. Only explicit floor division in Python is
recorded. The disagreement was visible only because the same quantity was derived by an
independent route; a single method would have published a wrong answer with no symptom.

**What this does not establish:** whether 2.77 % is typical. It is one day, chosen because it is
the one overlap day currently ingested. The same test on further archive days is the measurement
that would answer it, and it costs nothing once the partitions land.

---

# §11 — INVENTORY of the never-queried tables [DIUKUR]

Pure inventory. **No forward returns examined, 0 predictive trials.**

## The premise needs two corrections before the inventory starts

**The store holds EIGHT tables, not six** [DIUKUR, `duckdb_tables()` on `2026-08-03.duckdb`]:
`trades` · `depth_snapshots` · `funding_mark` · `open_interest` · `crowding` · `options_chain` ·
`dvol` · `liquidations`. So **six** have never been queried, not four — `dvol` and `liquidations`
were also missing from the list.

**`liquidations` IS already being collected** [DIUKUR: 388 rows on 2026-08-03; `/health` reports
697 rows written this process]. But **not from Binance `forceOrder`** — from **Bybit
`allLiquidation.<SYM>`** (`collector.py:571`), riding the `bybit-ws` leg. The collector already
documents its sparsity honestly at `collector.py:344-347`: *"bybit prints 3-2000/day and
2026-07-25 had ZERO all day"*. This materially changes the premise of the liquidations proposal
and is flagged here rather than absorbed.

## Schema, units, and source endpoint [DIUKUR schema · DIASUMSIKAN units]

| table | columns | source endpoint | units |
|---|---|---|---|
| `funding_mark` | `exchange, symbol, ts_ms, mark, index, funding_rate, next_funding_ts` | `fapi/v1/premiumIndex` · `api/v5/public/funding-rate` · bybit-ws `tickers` | price USDT; `funding_rate` a fraction per interval [DIASUMSIKAN] |
| `open_interest` | `exchange, symbol, ts_ms, oi` | `fapi/v1/openInterest` · `api/v5/public/open-interest` · bybit-ws | base asset (BTC) [DIASUMSIKAN — Binance docs, not cross-checked] |
| `crowding` | `exchange, symbol, ts_ms, metric, value` — **long format, one row per metric** | `futures/data/{globalLongShortAccountRatio, topLongShortPositionRatio, takerlongshortRatio, openInterestHist}` | ratios, dimensionless |
| `options_chain` | `ts_ms, name, expiry_ts, strike, cp, iv, oi, volume, mark_price, underlying` | Deribit `public/get_book_summary_by_currency` | `iv` in % [DIASUMSIKAN]; `strike` USD; `cp` call/put flag |
| `dvol` | `ts_ms, index_price` | Deribit DVOL index | annualised vol points |
| `liquidations` | `exchange, symbol, ts_ms, side, price, qty, notional_usd` | bybit-ws `allLiquidation` | USD notional |

**`options_chain` carries `iv`, `oi`, `strike`, `cp` and `expiry_ts`** — so IV skew and open
interest per strike are already on disk, not hypothetical.

## Cadence: intended vs actual — the pollers hit their marks [DIUKUR]

30 days (`2026-07-05 … 2026-08-03`), HF mirror, median inter-arrival:

| table | source | rows/30 d | actual | intended | ratio |
|---|---|---:|---:|---:|---:|
| `funding_mark` | binancef | 243,904 | 5.0 s | 5.0 s (`_PREMIUM_POLL_S`) | **1.00×** |
| `funding_mark` | okx | 25,230 | 61.0 s | 60.0 s (`_OKX_REST_POLL_S`) | 1.02× |
| `funding_mark` | bybit | 997,584 | 1.0 s | ws push | — |
| `open_interest` | binancef | 25,229 | 60.2 s | 60.0 s (`_OI_POLL_S`) | **1.00×** |
| `open_interest` | okx | 21,321 | 60.2 s | 60.0 s | **1.00×** |
| `open_interest` | bybit | 997,573 | 1.0 s | ws push | — |
| `crowding` | binancef | 26,282 | — ᵃ | 300 s (`_CROWDING_POLL_S`) | — |
| `dvol` | deribit | 21,268 | 60.2 s | 60.0 s (`_DVOL_POLL_S`) | **1.00×** |
| `options_chain` | deribit | 375,975 | — ᵃ | 3600 s (`_CHAIN_POLL_S`) | — |

ᵃ multiple rows share one `ts_ms` (4 metrics; ~hundreds of instruments), so a row-level
inter-arrival median is undefined. Not estimated.

**When a poller runs, it runs on time.** Every measurable ratio is 1.00–1.02×. The coverage
problem below is therefore **entirely about the poller not running**, never about it drifting.

## Coverage — §0a method, cells with no rows counted OFF [DIUKUR]

`(date, hour, source)` cells over the same 30 days, classified on **distinct `ts_ms` per hour**
against the intended cadence: **ON ≥ 90 %**, **OFF < 10 %**, else PARTIAL. Built by cross join.

| table | source | ON | PART | OFF | %ON | %ON by UTC block (00-08 / 09-11 / 12-15 / 16-22 / 23) |
|---|---|---:|---:|---:|---:|---|
| `funding_mark` | binancef | 298 | 91 | 331 | 41.4 % | 18 / 34 / 49 / **68** / 57 |
| `funding_mark` | okx | 232 | 227 | 261 | 32.2 % | 17 / 27 / 42 / 46 / 50 |
| `funding_mark` | bybit | 89 | 274 | 357 | 12.4 % | 1 / 7 / 20 / 24 / 13 |
| `open_interest` | binancef | 376 | 85 | 259 | 52.2 % | 26 / 42 / 67 / **78** / 77 |
| `open_interest` | okx | 317 | 73 | 330 | 44.0 % | 19 / 34 / 54 / 71 / 70 |
| `open_interest` | bybit | 89 | 274 | 357 | 12.4 % | 1 / 7 / 20 / 24 / 13 |
| `crowding` | binancef | 399 | 134 | 187 | 55.4 % | 27 / 54 / 69 / **80** / 83 |
| `dvol` | deribit | 316 | 75 | 329 | 43.9 % | 19 / 34 / 54 / 71 / 67 |
| `options_chain` | deribit | 425 | 0 | 295 | 59.0 % | 33 / 53 / 73 / **83** / 83 |
| *`depth_snapshots` (§0a reference)* | *3 venues* | *752* | *445* | *963* | *34.8 %* | *10-21 / 19-38 / 40-47 / 56-61 / 49* |

## Does host sleep hit these the same way? YES on coverage, NO on damage [DIUKUR]

**The answer splits into two claims that must not be merged.**

**Claim 1 — coverage: the signature is IDENTICAL, in all nine source/table pairs.** Every row
above shows the same monotone gradient: worst at UTC 00-08, best at 16-22. That is the host-sleep
shape §0a established for the book, and it is present everywhere. **Your hypothesis that these
tables suffer less is not supported on coverage** — the same host slept, and every leg stopped.

**And the higher %ON figures are largely an ARTEFACT, not better coverage** [DISIMPULKAN]. The
ON/PARTIAL/OFF thresholds are applied against each table's own cadence, so they are **not
comparable across cadences**. `options_chain` needs 1 sample per hour to score ON, so a 15-minute
outage usually misses it entirely and the cell still reads ON. `funding_mark` binancef needs 648
of 720, so the same outage drops it straight to PARTIAL. Reading `options_chain` 59.0 % against
`depth_snapshots` 34.8 % as "better covered" would be a threshold artefact.

**Claim 2 — damage: your hypothesis is CORRECT, and by a large margin** [DIUKUR]. What a
15-minute hole actually costs, measured by linearly interpolating across it and comparing to the
observed values (7 days, `2026-07-28 … 08-03`):

| series | n | median daily range | RMSE of 15-min interpolation | **% of daily range** |
|---|---:|---:|---:|---:|
| `open_interest.oi` (binancef) | 6,467 | 2,742 | 93.35 | **3.40 %** |
| `funding_mark.mark` (binancef) | 73,443 | 1,451 | 72.90 | **5.03 %** |
| `funding_mark.funding_rate` | 73,443 | 7.745e-05 | 1.088e-06 | **1.41 %** |
| `dvol.index_price` | 6,475 | 1.30 | 0.0767 | **5.90 %** |
| **`depth_snapshots`** | — | — | — | **100 % — unrecoverable** |

A 15-minute hole costs **1.4–5.9 %** of a day's variation in these slow variables, against
**100 %** for the book, where the state at those seconds exists nowhere and has no historical
endpoint. **Same disease, roughly 17–70× less damage.** That is the correct form of your
hypothesis: not that coverage is better, but that the loss is repairable.

**One caveat on Claim 2, stated rather than buried:** interpolation error is measured on holes
the data *has*, i.e. on hours the host was mostly awake. If sleep is correlated with market
quiet — which §0a's WIB inversion makes plausible — the true error across real outages could be
smaller than this, not larger. The direction of that bias favours the hypothesis; it is still an
untested assumption.

## Backfill or pure snapshot? [DIUKUR]

**`fromId` appears in exactly one place in `collector.py`** — the `fapi/v1/aggTrades` path
(`:44`, `:768`, `:779`). Every other leg listed here is a **pure state snapshot**: it fetches the
current value and has no historical cursor.

**Consequence:** a missed poll on these tables is a permanently missing *sample* — no equivalent
of aggTrades' seek-back exists, and no `cursor=None` class of bug is possible here either,
because there is no cursor. What makes them survivable is Claim 2, not recoverability.

## What I could not measure in §11

- **Units, definitively.** Every unit above is [DIASUMSIKAN] from venue documentation; none was
  cross-checked against a second source or a known reference value.
- **`liquidations` coverage over 30 days.** Its HF partition is absent for the tested date
  (HTTP error), consistent with days having zero rows. Not classified; the 388 rows on
  2026-08-03 are local only.
- **Whether Bybit `allLiquidation` is itself throttled.** Not verified against Bybit's own
  documentation or against an independent record of the same liquidations.
- **`crowding` and `options_chain` true cadence**, since multiple rows share one timestamp.
  Their coverage cells are classified on distinct `ts_ms`, which is correct, but no
  inter-arrival distribution is reported.
- **Whether the bybit-ws rows are genuinely 1/s or an artefact of push batching.** 997,584 rows
  in 30 days is 0.38/s, so the 1.0 s median coexists with long silences — the low %ON (12.4 %)
  says the silences dominate, but their cause is not established.

## Look counter

Per the standing rule, every look is counted and cannot be reduced later.

| section | diagnostic looks | predictive trials |
|---|---:|---:|
| §0 (0a hour 24, weekday 7, 0b day-level 16×3=48, 0b minute-level 3, 0c 24) | 106 | 0 |
| §2 (2a spread per venue 3, per hour 24, slippage 3 clips, 2b 3 estimators × 3 frequencies = 9) | 39 | 0 |
| §4bis (4 candidates × 1 analytic bound each — no returns examined) | 4 | 0 |
| **§4** (4a 9 horizons × 4 quantiles = 36, 4c 9 means) | 0 | **45** |
| **§4-corr** (6 horizons × 3 execution × {P, E[·\|·]} = 36) | 0 | **36** |
| §4bis-B re-score (16 candidates × 2 cost settings = 32) — classified **bug fix, 0 trials**, valid only because the full set is reported | 32 | 0 |
| §5 (framing only, no returns examined) | 0 | 0 |
| §6 PBO diagnosis (code reading + 4 parameter assessments) | 4 | 0 |
| §7 clustering (14×14 correlation, 2 N_eff estimates, 14×4 DSR recomputes = 56) — classified **denominator correction, 0 trials**, valid only because both methods and all 14 candidates are reported | 59 | 0 |
| §8 PBO twin test (2 declared PBO + 4 representative + 8 block-count + 56 subset null + 600 noise replications counted as 2 designs = 72) — classified **diagnostic of the statistic, 0 trials**, valid only because the representative was chosen structurally before any score and no candidate's status changed | 72 | 0 |
| §9 (git archaeology, 0 looks at returns) | 0 | 0 |
| §4b addendum (donchian + 15 random seeds + buy_and_hold + ma_trend = 18 walk-forwards) — audit of already-KILLed candidates, no configuration selected | 18 | 0 |
| §4c addendum (8 per-year decompositions of one existing series) | 8 | 0 |
| §10 (one archive-vs-recorded set comparison, ground truth) | 1 | 0 |
| §10a (1 set difference, 3 independent hour attributions, 1 block decomposition) | 5 | 0 |
| §E0 maker viability gate (in `docs/EDA-execution-001.md`) | 5 | 0 |
| §11 inventory (9 cadence measurements, 9 coverage censuses, 4 interpolation tests) — pure inventory, no forward returns | 22 | 0 |
| **running total** | **418** | **81** |

**§4 is the first non-zero entry, and it is counted conservatively at 45.**

An argument exists that it should be 0: every §4 look is an **unconditional marginal**
distribution of `|return|`. No signal is involved, nothing is conditioned on, and no strategy
configuration is tested — which is what a "trial" means in the Bailey–López de Prado
construction that `deflated_sharpe_ratio(risk.py:592)` implements. Measuring how much BTC moves
in 30 seconds is a property of the asset, not a search over strategies.

**It is carried at 45 anyway.** The rule was fixed in advance, forward returns were examined, and
the conservative direction is the one that cannot be argued into later. If a future PREREG wants
to claim 0 here, that claim must be made explicitly and defended, not assumed by silence.

§0, §2 and §4bis examined **no forward returns at all** — §2 and §4bis were deliberately
constructed that way (§4bis uses an analytic upper bound precisely to avoid re-scoring). The 188
diagnostic looks are recorded because they *were* looks: they decided which strata §1 may use,
which cost figure everything is judged against, and which verdicts stand.
