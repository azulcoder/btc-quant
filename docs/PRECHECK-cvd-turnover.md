# PRECHECK — daily vs hourly CVD turnover

**Declared before running. No returns are scored; 0 predictive trials.**

## What this decides

`PLAN-derivative-001.md` C1 rests on one claim: the daily formulation differs from the hourly
`sign(ΔCVD)` — which scored OOS Sharpe **−3.1 to −10.3** and was labelled *"a statement about
transaction costs"* — **primarily through cost, not signal**. If that cost difference is not
real, C1 drops here, before a single return is examined.

## The quantity, corrected before measuring

The obvious precheck is "is the daily flip rate lower per bar?" **That is the wrong quantity.**
What a strategy pays is turnover **per unit time**, and that is

```
flips_per_day = flip_rate_per_bar × bars_per_day
```

so the hourly formulation carries a **24× bar-count multiplier** the daily one does not. The
mechanism could only fail if the daily sign alternated almost every day *while* the hourly sign
persisted within the day — a specific and unlikely shape, and exactly what this measures.

**Reported quantity: annualised cost drag**, `flips_per_day × 365 × 10.02 bps`, using §2's
**measured** taker/taker round trip rather than `backtest.py`'s assumed 24 bps.

## Declared sample

- **4 contiguous blocks of 30 days**, start dates at fractions **0.00, 0.33, 0.66, 0.97** of the
  **sorted list of available partitions** (2,086 of them), computed rather than chosen.

  **AMENDED before any block's numbers existed.** The rule first said "fractions of the calendar
  span". The archive is not uniformly covered — it is dense 2020-01-01 … 2025-10-07, then a
  **296-day hole [count corrected by audit 2026-08-06]** (2025-10-08 … 2026-07-30, the ENOSPC that killed the ingest), then 3 days at
  the end. Block 4 landed inside the hole and returned nothing. Indexing by available partition
  cannot land in a hole by construction. The run aborted before any ΔCVD was computed, so no
  result influenced this change; all four blocks are recomputed under the new rule so the sample
  stays homogeneous.
- **Contiguous on purpose**: a flip rate needs consecutive bars, and a scattered sample
  fabricates transitions that never happened.
- **Same days at both horizons**, so the comparison is within-sample and no regime difference can
  explain it.

## Positive control — required before any number is reported

The CVD reader is a **new instrument** (blindness class F). Every partition read is checked
against `MANIFEST-<date>.json`'s `normalized.rows`, an independent record written at ingest time
whose source zip was **checksum-verified against the venue**. **Any mismatch aborts the
precheck** rather than reporting a number.

## Declared anchor — what "too expensive" is measured against

An absolute threshold in bps/year would be a number I picked. Instead the daily cost drag is
compared to the **same quantity for `tsmom`**, the board's best-scoring strategy, computed on the
same daily bars. `tsmom` survives its own cost drag, so it is a measured reference rather than an
opinion.

## Declared decision rule

| result | verdict |
|---|---|
| daily cost drag ≤ **2×** `tsmom`'s | **mechanism EXISTS** — the cost difference is real and C1 may proceed to pre-registration |
| daily cost drag ≥ **10×** `tsmom`'s | **mechanism ABSENT** — C1 drops now |
| between 2× and 10× | **AMBIGUOUS** — C1 stays unproposed pending a stated reason, not a re-reading |

**Both outcomes are useful and neither is hoped for.** A drop here costs nothing and saves a
predictive trial; a pass buys one candidate worth pre-registering, not a result.

## What this precheck cannot decide

- **Whether daily CVD has any edge at all.** It measures cost, not signal. Passing means only
  that the hourly failure's stated cause does not automatically apply.
- **Whether the flip rate is stable outside the four blocks.** 120 days of 2,406.
- **Whether a position sized on ΔCVD magnitude rather than its sign would turn over differently.**
  Only the sign formulation is measured, because only that one failed hourly.

---

# RESULT — mechanism ABSENT. C1 drops. [DIUKUR]

**Positive control passed first:** 120 partitions read, every one matched its
`MANIFEST-<date>.json` `normalized.rows`, 0 mismatches. 122,659,658 tape rows.

Blocks (fractions 0.00/0.33/0.66/0.97 of the 2,086 available partitions):
`2019-12-31`, `2021-12-03`, `2023-11-02`, `2025-08-10`, 30 contiguous days each.

| horizon | bars | flips | flips/bar | flips/day | **cost drag** |
|---|---:|---:|---:|---:|---:|
| 1 h | 2,880 | 1,487 | 0.5165 | 12.40 | **45,336 bps/yr** |
| 1 d | 120 | 53 | 0.4454 | 0.45 | **1,629 bps/yr** |

**The hourly failure needs no signal analysis to explain.** A cost drag of **453 % per year**
accounts for OOS Sharpe −3.1 to −10.3 on its own, and it is **626×** the anchor below. The
run-log's label — *"a statement about transaction costs"* — is now arithmetic rather than
judgement.

**Daily persistence is genuinely higher**, 0.4454 flips/bar against 0.5165, so the drag ratio is
**27.8×** rather than the 24× the bar count alone would give. The mechanism has a real component.
It is not nearly enough.

## Against the declared anchor

| strategy | turnover/yr | cost drag |
|---|---:|---:|
| `tsmom` (the anchor) | 14.46 | **72 bps/yr** |
| `ma_trend_filter` | 7.22 | 36 bps/yr |
| `buy_and_hold` | 0.12 | 1 bps/yr |
| **daily `sign(ΔCVD)`** | **327** | **1,629 bps/yr** |

**Ratio to anchor: 22.49×.** The declared rule was ≤2× exists, ≥10× absent.

> **MECHANISM ABSENT — C1 drops now, before any return was scored.**

The reason is structural, not marginal: a sign-based daily signal flips **~164 times a year**,
while a trend filter flips a handful. Moving from hourly to daily divides the drag by 27.8 and
still leaves it **22× above a strategy that survives its own costs**.

## What is NOT concluded, and one thing I will not propose

- **This does not say daily CVD carries no information.** It says the `sign(ΔCVD)` *formulation*
  cannot pay for itself at 10.02 bps round-trip. Cost, not signal — the same distinction §19 drew
  for options.
- **A smoothed variant would turn over less** — sign of an N-day mean of ΔCVD, or a deadband.
  **I am not proposing it.** It would be a specification chosen *after* seeing this failure, which
  is exactly the spec-fitting `PLAN-derivative-001.md` forbids. If it is ever tested it needs its
  own pre-registration, written before its numbers exist, and it should be judged knowing that its
  parent specification failed.

## What this could not measure

- **Whether the 4 blocks represent the other 1,966 partitions.** 120 days of 2,406; the blocks
  are structurally spaced but no dispersion across blocks is reported.
- **Whether `tsmom` is the right anchor.** It is vol-scaled and continuous; `sign(ΔCVD)` is
  binary, and binary positions turn over more by construction. The anchor was declared in
  advance, so it stands — but a fairer comparison would be against a binary board strategy, and
  none exists.
- **Anything about magnitude-weighted ΔCVD.** Only the sign formulation was measured, because
  only the sign formulation failed hourly.

## Instrument note — the first run was WRONG and printed its own contradiction

The first attempt reported **identical bar counts for 1 h and 1 d** (61,380,169 each), because
`ts_ms / 3600000` in DuckDB is **float** division, so `GROUP BY` grouped per millisecond. The
derived columns still looked plausible — flips/bar 0.5311, a ratio of exactly 24.0× — because the
ratio was forced by my own `bars_per_day` arithmetic rather than measured.

**It was caught instantly because the two bar counts printed side by side**, which is the
class-G placement rail doing its job. The rerun uses explicit integer division `//` and asserts
the bar count against its expected value before reporting anything. This is **class H** again —
the third silent-default instance after `get_ohlcv`'s 300 bars and `CAST` rounding.

---

## CORRECTION 2026-08-06 — the anchor bias was real and material

This document's own limitations said: *"a fairer comparison would be against a binary board
strategy, and none exists."* **One does: `tsmom_dir`**, binary ±1, the highest-DSR binary
strategy on the board (0.87), drag **188 bps/yr**.

| anchor | ratio | verdict |
|---|---:|---|
| `tsmom` (continuous) — as run above | 22.62× | mechanism ABSENT |
| **`tsmom_dir` (binary) — the fair anchor** | **8.66×** | **AMBIGUOUS** |

**The result above is not rewritten.** It was computed against the anchor declared in advance and
it stands as recorded. The correction is admissible because the bias and its **direction** were
named before the number was seen — a continuous vol-scaled anchor understates the fair threshold
for a binary candidate.

**Consequence: C1 moves from DROPPED to AMBIGUOUS**, and stays NOT PROPOSED under the declared
ambiguous-band rule. The claim weakens from "the mechanism is absent" to "the mechanism is real
and not demonstrably sufficient at 8.7× the board's highest turnover". See
`PLAN-derivative-001.md`, standard procedure.
