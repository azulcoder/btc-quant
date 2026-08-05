# EDA-execution-001 — descriptive research for an EXECUTION OVERLAY

**Scope.** Descriptive only. Nothing in this document is a signal, and nothing in it may be
promoted, wired to the terminal, or read as a trading recommendation. It measures what execution
costs and what the tape says about passive fills. `descriptive-never-a-signal` applies in full.

**Slice.** Exploration uses recorded `binancef` book and tape. The **LockBox
(~~`2026-08-05 00:00 UTC`~~ → `2026-08-05 01:00 UTC` onward, boundary moved once for a
documented data defect — see the amendment in `docs/EDA-microstructure-001.md`) is not touched,
not read, not peeked.** Every query in this document predates the boundary either way.

---

# §E0 — MAKER VIABILITY GATE

## E0-pre — the correction that changes the target [DIUKUR]

`EDA-microstructure-001.md` §2 built a **cost** table. Its maker/maker row reads:

| execution | fee (2 sides) | spread crossed | round-trip total |
|---|---:|---:|---:|
| maker / maker | 4.0 | **0.0000** | **4.00 bps** |

`spread crossed = 0.0000` is **correct as a cost** — a maker crosses nothing. But §2 never moved
the spread to the other side of the ledger. **For a market maker the spread is not a cost that
vanishes; it is the entire revenue line.** Putting it where it belongs:

```
revenue  (spread captured, round trip)   +0.0157 bps
cost     (maker fee, VIP 0, 2 bps/side)  −4.0000 bps
                                         ───────────
net, GUARANTEED, before adverse selection  −3.9843 bps
```

**Retail-tier market making is dead by arithmetic, not by opinion.** This ranks with the
perfect-foresight bound of §4: it is a limit that no signal, model, or feature can cross,
because it is fixed before any prediction is made.

### Positive control — three independent routes to the same number

Per the standing positive-control rule (`STRATEGY.md`), the spread was re-derived before being
used, including by a route that involves no query at all:

| # | route | value |
|---|---|---:|
| 1 | this document's own query — `depth_snapshots`, binancef, 2026-08-03, UTC 12–23, n = 41,494 | **0.0157 bps** |
| 2 | `EDA-microstructure-001.md` §2a — 859,264 snapshots across 26 days | **0.0156 bps** |
| 3 | **physical anchor** — tick size $0.10 ÷ median mid $63,719.75 | **0.0157 bps** |

All three agree within 0.0002 bps. Route 3 is the important one: it depends on no data pipeline,
only on the exchange's published tick size and the price level, so an error in routes 1 and 2
could not hide inside it.

### The spread cannot widen to rescue this [DIUKUR]

Book width during ON hours (UTC 12–23), 2026-08-03:

| width | snapshots |
|---|---:|
| **1 tick** | **41,449 (99.95 %)** |
| 2 ticks | 12 |
| 3 ticks | 6 |
| 5 ticks | 3 |

The spread is not merely small — it is **pinned at the minimum the exchange permits, 99.95 % of
the time**. A maker cannot quote wider and expect fills, because there is no second price level
to quote at. Whatever a maker earns per round trip is one tick, and one tick is 0.0157 bps.

---

## E0a — net bps per round trip across maker fee tiers [DIUKUR spread · DIASUMSIKAN fees]

`net = spread_captured − fee_round_trip`, with `spread_captured = 0.0157 bps` (full spread, both
fills at the touch, mid unchanged — an **upper bound**, since any mid drift between the two fills
reduces it) and `fee_round_trip = 2 × fee_per_side`.

| maker fee / side | tier | fee round-trip | **net bps / round trip** | sign |
|---:|---|---:|---:|:--:|
| **2.0** | VIP 0 standard | 4.00 | **−3.9843** | ❌ |
| **1.8** | VIP 0 + BNB −10 % | 3.60 | **−3.5843** | ❌ |
| **1.0** | mid VIP | 2.00 | **−1.9843** | ❌ |
| **0.5** | high VIP | 1.00 | **−0.9843** | ❌ |
| **0.0** | VIP 9 / zero-fee maker | 0.00 | **+0.0157** | ✅ barely |
| **−0.5** | MM-program rebate | −1.00 | **+1.0157** | ✅ |

**Zero crossing: `fee_per_side = 0.0157 / 2 = 0.00785 bps`, i.e. 0.0000785 %.**

Read that number carefully. The break-even maker fee is **eight ten-thousandths of a basis
point** per side. The lowest published Binance USDⓈ-M maker rate is VIP 9 at exactly 0.0000 %.
So the crossing does not sit between two real tiers — it sits between **zero and anything at
all**. A fee of 0.001 % (0.1 bps/side, far below VIP 0) already puts the round trip at
**−0.184 bps**.

**Gross maker/maker is non-negative only at exactly-zero fee or a rebate. There is no third case.**

---

## E0b — the remaining margin against adverse selection

**[DIASUMSIKAN, OTHER MARKETS — not measured here, not ours.]** Reported maker markout in
equity and FX venues runs roughly **−0.5 to −0.8 bps** per fill. That order of magnitude is
carried in as a *scale*, not as a value for BTCUSDT perp. Measuring our own markout curve is
exactly what §E1 exists to do, and until it runs this row stays assumed.

| maker fee / side | margin after fee | adverse selection | **net after AS** | verdict |
|---:|---:|---:|---:|---|
| 2.0 → 0.5 | −3.98 … −0.98 | −0.5 … −0.8 | −4.78 … −1.48 | dead before AS is even considered |
| **0.0** | +0.0157 | −0.5 … −0.8 | **−0.48 … −0.78** | **FAILS** |
| **−0.5** | +1.0157 | −0.5 … −0.8 | **+0.22 … +0.52** | survives, thinly |

**The ratio is the finding, and it is brutal** [DISIMPULKAN]: adverse selection at −0.5 to
−0.8 bps is **32× to 51× the entire spread revenue of 0.0157 bps**. At a *zero* fee — the best
tier that exists without a negotiated agreement — the business still loses roughly 30–50 times
more to being picked off than it earns from the spread it captures.

**Minimum rebate required to break even after adverse selection:**
`rebate_per_side ≥ (AS − 0.0157) / 2` = **0.242 bps** (at AS = 0.5) to **0.392 bps** (at AS = 0.8).

### And a structural obstacle the fee table does not show [DIUKUR]

Notional resting **at the touch**, binancef, 2026-08-03, UTC 12–23, n = 41,494:

| side | p10 | p50 | p90 |
|---|---:|---:|---:|
| bid | $128,155 | **$427,057** | $1,420,067 |
| ask | $145,711 | **$462,178** | $1,435,677 |

Median queue ≈ **$444,617**. Since the book is one tick wide 99.95 % of the time, a new maker
**cannot price-improve** — they can only join the back of that queue. For a $1–5k clip that
means **89× to 445× the clip's own size must trade through first**. And the fills that do arrive
preferentially arrive when the queue is being swept, which is the adverse-selection case by
definition.

**This is not a fee problem and no fee tier fixes it.** It is stated here because a viability
gate that only counted basis points would have declared a zero-fee account viable.

---

## E0c — VERDICT

**Standalone market making is NOT worth pursuing, and no signal can change that.** [DISIMPULKAN
from measured spread + assumed fees]

The account conditions under which it *could* be reconsidered — **all three required, none of
them retail:**

1. **Maker fee ≤ 0 per side.** Anything above zero loses on gross, before any risk is taken.
2. **A rebate of ≥ 0.24–0.39 bps/side** to cover adverse selection of the assumed magnitude.
3. **Queue priority at the touch** — infrastructure that reaches the front of a ~$445k queue on
   a one-tick book. Colocation and cancel/replace latency, not modelling.

Conditions 1 and 2 together mean a **negotiated market-maker agreement with volume commitments**.
Condition 3 means **latency-competitive infrastructure**. This repo's founding constraint is
keyless, retail, non-colocated. **The answer is no, and it is not close: at VIP 0 the gap is
−3.98 bps per round trip, which is 254× the entire spread revenue.**

This also confirms `RESEARCH.md:180`, which the repo already recorded before any of this was
measured: *predictability at this horizon is "only an execution/order-placement overlay for
latency-competitive makers… For anyone paying taker fees or non-colocated, costs dominate
entirely."* §E0 supplies the arithmetic that statement was asserting.

### What this verdict does NOT kill — and the target it re-aims at

**The overlay survives, and it survives for a different reason entirely.** An execution overlay
does not earn the spread; it saves the **taker/maker fee differential**. Using §2d's published
rates — taker 5.0, maker 2.0 bps/side — that differential is **3.0 bps per side = 6.0 bps per
round trip**, which is **382× the spread revenue of 0.0157 bps**.

So the two businesses have unrelated economics:

| | revenue source | magnitude |
|---|---|---:|
| standalone market making | spread capture | 0.0157 bps/round trip |
| execution overlay | fee differential avoided | 6.0 bps/round trip |

**Killing the first strengthens the case for studying the second**, because it establishes that
the overlay's value cannot come from spread capture and must be measured as cost reduction. That
is the framing §E4 requires.

**One arithmetic correction carried forward to §E4:** the differential is 3.0 bps **per side** /
6.0 bps **per round trip**, not 6.0 bps per side. Getting this wrong would double every overlay
value in §E4, so it is fixed here before that section is written.

---

## What I could not measure in §E0

- **Our own adverse selection.** Every AS number above is [DIASUMSIKAN, OTHER MARKETS]. The
  BTCUSDT-perp markout curve is unmeasured until §E1 runs, and the E0b verdict at the zero-fee
  and rebate tiers would move if our true markout differs from −0.5…−0.8 bps. The tiers at
  ≥ 0.5 bps/side fail on gross arithmetic alone and no AS value can rescue them.
- **Actual account fee tier.** No venue fee table exists in this codebase (`EDA-microstructure-001.md`
  §2d, grep-confirmed zero hits). All fees are published list rates, never account facts.
- **Whether the spread widens in stress.** Measured max is 5.128 bps on 2026-08-03 (≈327× the
  median), so a tail exists; whether a maker could *earn* it, or would only be run over by it,
  is not measurable from snapshots alone.
- **Actual queue position or fill probability.** The touch-notional figures bound the problem;
  they do not solve it. That is §E3, and §E3 brackets it rather than choosing.
- **Any hour outside UTC 12–23**, and any day other than 2026-08-03 for the touch-queue and
  book-width figures. The 26-day confirmation covers the spread only.

## Look counter — §E0 contribution

Continues the running total in `docs/EDA-microstructure-001.md`. **That document stands at 391
diagnostic / 81 predictive**, not 386 — the last request quoted 386, which was the total before
§10a (the UTC attribution of the 28,428 missing prints) added 5. Recording the difference rather
than silently adopting either number.

| section | diagnostic looks | predictive trials |
|---|---:|---:|
| §E0 (spread quantiles ×2 windows, tick-width distribution, tick anchor, touch-queue quantiles) | 5 | **0** |

The master running total lives in `docs/EDA-microstructure-001.md` and stands at **418 / 81**
with §E0 and §11 included.

**0 predictive trials, argued rather than assumed.** §E0 examines **no forward returns**. It
compares a measured contemporaneous spread against published fee schedules — arithmetic on
prices and tariffs, with no signal, no conditioning, and no configuration selected. Nothing here
could be overfit because nothing here is fitted.

**The risk that would invalidate that classification**, named so it can be checked: if the fee
tiers examined had been chosen *after* seeing which one produced a favourable answer, the table
would be a selection. They were not — the tier list `{2.0, 1.8, 1.0, 0.5, 0, −0.5}` was
specified in the request before any of it was computed, and **all six are reported**.

§E1 and §E2 examine forward returns and **will** add predictive trials. They are counted there,
not here.
