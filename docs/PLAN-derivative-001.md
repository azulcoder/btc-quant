# PLAN-derivative-001 — open-gate signal research

**Status: PLAN ONLY. Nothing here has been run, and no candidate is pre-registered.** This
document says what would be researched, what data each needs, whether that data exists, and what
it would cost in trials. The decision to spend any of it is not mine.

**Why this path.** Sixteen consecutive items have been instrument integrity — necessary, and no
signal research has run since §4. The open-gate families (hours-to-days horizon) are the only ones
never touched by a single query, they need no order book, they are not blocked by the book's
1.8 %-of-MinBTL problem, and they are not gated on bybit.

**The taxonomy is the equipment, not decoration.** Each candidate below names the blindness
classes that specifically threaten it (`CLAUDE.md`), because a design that does not name its own
failure mode is class F by construction.

---

## The finding that reorders the candidates, before any of them are described

**Three of the four proposed candidates hit the same frozen-sample wall that just killed the
options test (§19), and the fourth does not.** [DIUKUR]

**STEP 0 RESULT [DIUKUR]** — three of four unfrozen, measured rather than assumed:
`fundingRate` reaches **2019-09-10**, perp klines **2019-09-08**, spot klines **2017-08-17**;
`openInterestHist` serves exactly **30 rolling days** and rejects any `startTime` beyond ~30 days
with HTTP 400 (boundary located between 25 and 35 days). So C2 and C3 are unblocked with ~6.9
years each, and **C4 is confirmed frozen permanently** — not by assumption, by probe.

| source | span available | frozen? |
|---|---|---|
| `funding_mark`, `open_interest`, `crowding`, `dvol`, `options_chain` | **30 days** recorded | **YES** — every new day goes to the LockBox, not to exploration (§19) |
| **`aggTrades` via the Vision archive** | **2,086 day-partitions, 2019-12-31 … 2026-08-01 = 6.59 years** | **NO** — already on disk, needs no further collection |
| daily OHLCV (`BTC-USD`, coinbase) | 3,139 bars, 2018-01-01 … 2026-08-05 | no |

So the candidate the request was most sceptical of — **daily CVD** — is the only one with a
sample worth testing today, and the three that sound most mechanically appealing are the ones with
30 observations. That is not an argument that CVD is more likely to work. It is an argument about
what can be *answered* now.

**Step 0 for the three frozen candidates is not analysis, it is availability:** Binance publishes
keyless historical **funding rates** (`/fapi/v1/fundingRate`) and keyless historical **klines** for
both perp and spot, which would unfreeze funding and basis without touching the LockBox. Whether
those reach back far enough, and whether an equivalent exists for open interest, is **NOT CHECKED**
— checking it costs one HTTP call each and must happen before any of them is designed further.

---

---

# STANDARD PROCEDURE — the cost-drag gate, run BEFORE any design

**Every candidate passes this before a line of its design is written.** It costs **0 predictive
trials**, because turnover is a property of the *position series*, never of returns.

```
cost_drag_bps_per_year = (Σ|Δposition| / years) × 5.01 bps      # 10.02 bps round trip, §2 MEASURED
```

**This is the turnover twin of §4's perfect-foresight bound.** §4 closed candidates on HORIZON —
if the move is smaller than the cost, no skill helps. This closes them on TURNOVER — if the
trading is more expensive than a strategy that survives, no skill helps either. Two cheap gates,
both before anything expensive.

## The anchor — FIXED NOW, at 188 bps/yr

| board strategy | type | turnover/yr | cost drag |
|---|---|---:|---:|
| **`tsmom_dir`** | **BINARY** (±1) | 37.60 | **188 bps/yr ← ANCHOR** |
| `tsmom_ls` | continuous | 28.52 | 143 |
| `ma_trend_fixedR` | binary | 19.32 | 97 |
| `pairs_ou` | binary | 17.69 | 89 |
| `tsmom` / `tsmom_voltarget` | continuous | 14.46 | 72 |
| `vwap_reversion_48` | binary | 10.01 | 50 |
| `donchian_55_20` | binary | 8.61 | 43 |
| `random_entry` (control) | binary | 8.03 | 40 |
| `pairs_coint` | binary | 3.96 | 20 |
| `ma_trend_filter` | binary | 1.86 | 9 |
| `buy_and_hold` | binary | 0.12 | 1 |

**Why `tsmom_dir`:** it is the **highest-DSR binary strategy** on the board (0.87), and a binary
anchor is the only fair comparison for a binary candidate — a vol-scaled continuous position
turns over less by construction, so anchoring on `tsmom` understates the fair threshold. That
weakness was flagged when the C1 precheck ran, and correcting it **changed C1's verdict**
(below), so it was material rather than pedantic.

**The anchor is fixed at 188 bps/yr as an absolute reference from here on**, not recomputed from
whatever the board contains at the time. A moving anchor drawn from the set under test is
circular — see the retroactive result.

**Positive control on every run:** `tsmom` must come out at **72 bps/yr**. It did, to the digit,
against the value computed independently in the C1 precheck.

## Decision rule

| drag vs anchor | verdict |
|---|---|
| ≤ 2× | passes; the candidate may be designed |
| 2–10× | **AMBIGUOUS** — not proposed, pending a stated reason rather than a re-reading |
| ≥ 10× | dropped before any return is scored |

## Retroactive result on the 14 board candidates — and why it is nearly vacuous [DIUKUR]

**Zero of 14 would have been dropped**, and that is **largely by construction**: the anchor is
drawn from the same set being tested, so nothing in it can be 10× its own maximum. The
non-circular statement is the useful one:

> Daily `sign(ΔCVD)` at **1,629 bps/yr is 8.7× the HIGHEST drag on the entire board**.

The board spans **1 to 188 bps/yr — a 188× range** — and every member sits inside it. The gate's
discriminating power is therefore against candidates from **outside** the board's turnover
regime, which is exactly where a new family comes from. Fixing the anchor at 188 removes the
circularity for every future candidate.

## C1's verdict, CORRECTED — and this reverses part of the last result

| anchor | drag ratio | verdict |
|---|---:|---|
| `tsmom` (continuous, DSR 0.93) — used in the precheck | **22.62×** | mechanism ABSENT |
| **`tsmom_dir` (binary, DSR 0.87) — the fair anchor** | **8.66×** | **AMBIGUOUS** |

**Both are recorded; the original is not rewritten.** The correction is legitimate because the
bias was named *before* the result was seen — "a fairer comparison would be against a binary board
strategy, and none exists" was written into the precheck's own limitations. One did exist, and
finding it moved C1 from **DROPPED** to **AMBIGUOUS**.

**C1 remains NOT PROPOSED**, because the declared rule for the ambiguous band is "not proposed
pending a stated reason, not a re-reading". What changed is the *reason*: not "the mechanism is
absent" but "the mechanism is real, and at 8.7× the board's maximum turnover it is not
demonstrably enough". That is a weaker claim than the one made last turn, and the weaker one is
the correct one.


---

## C1 — Daily CVD

**Mechanism, and it must be stated honestly because the hourly version already died.**
`sign(ΔCVD)` at the hourly horizon scored OOS Sharpe **−3.1 to −10.3** and the run-log labelled it
*"a statement about transaction costs"*. The distinction at daily horizon is **primarily cost, not
signal**:

1. **Turnover.** If the daily CVD sign persists longer than the hourly one, position flips fall by
   roughly the ratio of flip rates, and the cost drag falls proportionally. §2 established that
   fees are **625×** the entire microstructure cost, so cost drag is the binding term in the
   hourly failure. This is arithmetic, not hope.
2. **Economic content.** Hourly CVD is dominated by intraday inventory cycles that mean-revert
   within the hour; daily CVD aggregates across them and may retain directional accumulation.
   **This second mechanism is weakly supported and I will not lean on it.**

**So the honest hypothesis is "the same weak signal survives at lower turnover", not "a different
signal exists".** That lowers the prior and it should. If the daily flip rate is not materially
below the hourly one, mechanism 1 evaporates and the candidate should be dropped **before** any
return is scored — that check is free and comes first.

**Data:** `trades` from the Vision archive — 6.59 years, already on disk. Needs the aggressor side,
which `aggTrades` carries and §10 verified byte-exact against the venue on the overlap day.
**Effective N: ~2,400 daily observations.**

**Trials:** 1 for the flip-rate precheck (descriptive, no returns), then **1 predictive trial** for
the single pre-registered daily variant. **No parameter sweep** — a sweep here would repeat the
§4 problem, and the whole point is that the specification is fixed by the hourly failure.

**Blindness classes that threaten it:** **H** (silent default — `get_ohlcv` already returned 300
bars once; every loader call gets its defaults inspected), **F** (the CVD builder is a new
instrument — its first number must reproduce a known value, e.g. total daily volume against an
independent source), **B** (days the archive lacks must be counted, not dropped — 321 of 2,406
calendar days have no partition).

**Verdict: DROPPED.** The precheck ran (`docs/PRECHECK-cvd-turnover.md`) and the mechanism is
absent: daily `sign(ΔCVD)` carries a cost drag of **1,629 bps/yr, 22.49× the `tsmom` anchor**,
against a declared abort at 10×. The hourly drag is 45,336 bps/yr — 626× the anchor — which
explains the original OOS Sharpe of −3.1 to −10.3 without any signal analysis. Dropped before a
single return was scored, which is what the precheck was for.

---

## C2 — Perp-spot basis as mean reversion

**Mechanism:** the perp trades at a premium or discount to spot set by leveraged demand; funding
pulls it back. Deviation from a rolling mean is a mean-reversion target with an explicit
restoring force — a real mechanism, not a pattern.

**Data:** perp mark or close, and spot close. `funding_mark` carries `mark` and `index` but only
for **30 recorded days**. Keyless historical klines for both legs would unfreeze it. **Existence
and depth NOT CHECKED.**

**Effective N:** 30 today; potentially years if step 0 succeeds.

**Trials:** 1 predictive, after a pre-registered window and z-threshold. Note this is structurally
the same trade as `pairs_coint`, which §4c showed holds a position **2.2 % of bars** — so the
breadth problem is a live risk here and the design must report breadth *before* Sharpe.

**Classes:** **B** (a basis of zero and a missing leg look identical), **D** (mark vs index vs
spot-close are three different prices; comparing across them without saying which is class D).

**Verdict: blocked on step 0.** Cheap to unblock.

---

## C3 — Extreme funding as crowding

**Mechanism:** persistently positive funding means longs are paying to stay long, i.e. crowded
positioning that is vulnerable to a squeeze. The literature is explicit that this is a **sentiment
indicator needing confirmation, not a standalone signal**, and that funding can stay extreme far
longer than a trader expects during a strong trend — so the design must be conditional, and a
standalone version should not be proposed.

**Data:** `funding_mark.funding_rate`, 30 recorded days. Binance's historical funding endpoint is
keyless. **NOT CHECKED.**

**Effective N:** 30 days = ~90 funding intervals today (8 h cadence); years if step 0 succeeds.

**Trials:** 1, and **percentile-based by construction** — an absolute threshold would be a fitted
parameter and would spend trials it cannot afford. The percentile window itself is a free
parameter and falls under the tie-break rail: if two defensible windows straddle the bar, NOT
CLEARED.

**Classes:** **E** (funding is strongly autocorrelated; any test assuming independent intervals is
misspecified — the §14 error), **D** (funding is per-interval; comparing an 8 h rate to a daily
return without annualising is class D).

**Verdict: blocked on step 0**, and even then it is a **conditioning variable**, not a standalone
candidate. It should enter as a filter on C1 or C2, not on its own.

---

## C4 — OI × price direction, four quadrants

**Mechanism:** the classic reading — price up + OI up = new longs; price up + OI down = short
covering; price down + OI up = new shorts; price down + OI down = long liquidation. Structural and
well-defined, not pattern-mining.

**Data:** `open_interest`, **30 recorded days**, and this is the **hardest to unfreeze**:
`futures/data/openInterestHist` is a short-lookback endpoint, so unlike funding and klines there
may be **no long keyless history at all**. **NOT CHECKED.**

**Effective N:** 30 days. Four quadrants over 30 observations is **~7 per cell** before any
conditioning.

**Trials:** 1 if it ever becomes testable.

**Classes:** **D** (OI is quoted in base asset on one venue and contracts on another — §11 marks
the unit as [DIASUMSIKAN] and never cross-checked; a cross-venue OI series without unit
verification is class D), **B** (a flat OI hour and a dead poll are identical in the store).

**Verdict: NOT PROPOSED for now.** Seven observations per quadrant cannot support a four-way
split, and the unfreeze route is the least likely to exist. Revisit only if step 0 finds real
history.

---

## What this plan does NOT do

- **It proposes no parameter sweep anywhere.** Every candidate is one pre-registered
  specification. The §4 look counter stands at 81 predictive trials, and MinBTL grows with `ln N`
  — the cost of a sweep is not the sweep, it is that it deflates everything already scored.
- **It does not touch the LockBox**, and none of the routes above requires touching it. That is
  deliberate: §19 showed how quickly a frozen sample turns into pressure to spend the one shot.
- **It commits to no ordering beyond step 0.** Which candidate is worth a trial depends on what
  step 0 finds, and step 0 is four HTTP calls.

## What I could not measure

- **Whether any keyless historical source exists** for funding, klines-based basis, or open
  interest. Four candidates, three blocked on this, and it was not checked — checking it is the
  first action if this plan is approved.
- **The daily CVD flip rate** versus hourly, which is the precheck that decides whether C1's
  mechanism exists at all. Not run; it is descriptive and costs no predictive trial.
- **Whether 321 missing Vision partitions bias the CVD series.** Counted (§16-adjacent), not
  characterised.
