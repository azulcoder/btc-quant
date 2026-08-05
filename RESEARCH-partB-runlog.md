# Part B run-log — pre-registered OOS strategy pass

Research only. Not financial advice. A backtest is not a forecast.

Every candidate below is **pre-registered**: the hypothesis and the falsifiable kill criterion
are written *before* the harness is run. Each is judged **only** on walk-forward out-of-sample
Deflated Sharpe (and PBO), through the existing engine (`backtest.walk_forward`, `risk.*`), never
on in-sample curve shape. A candidate earns a permanent slot on the public board **only** if it
clears its kill criterion; otherwise the rejection is recorded here as a finding. The honest
expected outcome is that none are promoted — the pass succeeds by *correctly rejecting*.

Reproduce: `python3 scripts/compare.py --research` (BTC-USD daily, 5 walk-forward folds,
cost 10+2 bps/side). Public board N = len(SPOT_STRATS); research N includes the candidates.

---

## B1 — tsmom × vol-target

**Hypothesis.** Layering volatility-targeting on time-series momentum improves Calmar / max-DD
versus the raw directional momentum signal, but does **not** improve OOS Deflated Sharpe — BTC's
return–volatility correlation is unstable, so the equity-style Sharpe lift does not transfer.

**Method.** Baseline = raw directional `tsmom(vol_scaled=False, long_short=False)` (±1/0 sign,
no sizing). Candidate `tsmom_voltarget` = `vol_target(that, target_vol=0.15, max_leverage=2)`.
The existing vol-scaled `tsmom` (already on the board) is printed for context. All net of cost,
walk-forward OOS.

**Kill criterion (no permanent slot if EITHER holds).**
1. `OOS_DSR(tsmom_voltarget) − OOS_DSR(tsmom_dir) < +0.05`, **or**
2. positions are a near-duplicate of the board's vol-scaled `tsmom` (`|corr| > 0.95`).

On kill, label **tail-control-only** (size, not edge). *Pre-registered expectation: KILL* — and
likely via (2), since the board's `tsmom` is already vol-scaled.

**Results** (`compare.py --research --start 2018-01-01`, BTC-USD 1d, 3087 bars, 5 folds, N=8):

| strat | OOS DSR | OOS SR | OOS MaxDD |
|---|---|---|---|
| `tsmom_voltarget` (B1) | 0.89 | 0.99 | −22.51% |
| board `tsmom` (vol-scaled) | 0.89 | 0.99 | −22.51% |
| `tsmom_dir` (raw directional) | 0.82 | 0.90 | −59.41% |

- Δ vs raw directional = **+0.06**. **corr(tsmom_voltarget, board tsmom) = 1.00.**
- Rows are byte-identical to the board's `tsmom` — `vol_target(tsmom(vol_scaled=False), cap 2)`
  reduces, after the [-1,1] clip, to the same series as `tsmom(vol_scaled=True)`.

**Verdict — KILL (not promoted), via criterion 2 (|corr| = 1.00 > 0.95).** Honest nuance: the
DSR-lift half of the hypothesis was *weakly off* — vol-targeting did raise OOS DSR vs the raw
directional baseline (0.82 → 0.89) and slashed max-DD (−59% → −23%). But that vol-targeted
strategy **already exists on the board as `tsmom`** (correlation 1.00). B1 is therefore a literal
duplicate; adding it would only inflate N and burn MinBTL headroom for zero new information. The
finding is "already represented," exactly as anticipated in the plan's honest read.

---

## B2 — OU-reversion thresholds on the BTC–ETH spread

**Hypothesis.** OU-model-derived thresholds do **not** beat a simple empirical z-score
out-of-sample — the fitted OU parameters are non-stationary in crypto. A teaching case for
"a model, not an edge."

**Method.** `pairs_ou` is `pairs_coint` with exactly **one** variable changed: the deviation is
normalized by the **OU-fit stationary σ** (`features.ou_sigma_eq`, from the same AR(1) fit as
`ou_half_life`) instead of the empirical rolling standard deviation (the z-score). Hedge ratio β,
the half-life stationarity gate, and the entry/exit/stop multiples are identical to the fixed-z
baseline. Isolating the normalizer makes the comparison clean: if OU loses, the parametric model
adds nothing.

**Kill criterion (no permanent slot if EITHER holds).**
1. `OOS_DSR(pairs_ou) − OOS_DSR(pairs_coint) < +0.05`, **or**
2. `PBO(board + pairs_ou) > PBO(board)` (adding it makes the selection *more* overfit).

On kill, document as **"model, not edge."** *Pre-registered expectation: KILL.*

**Results** (same run):

| strat | OOS DSR | OOS SR | OOS MaxDD |
|---|---|---|---|
| `pairs_ou` (B2, OU-σ_eq) | 0.04 | −0.12 | −51.59% |
| `pairs_coint` (fixed-z) | 0.07 | −0.00 | −14.85% |

- Δ = **−0.03** (the OU normalizer made it *worse*, not better) — and max-DD blew out 3.5× (−15%
  → −52%). PBO(board) 0.67 → PBO(board + pairs_ou) 0.61 (lower only because a clearly-bad column
  is never the IS-best — not a point in its favor).

**Verdict — KILL (not promoted), via criterion 1 (Δ = −0.03 < +0.05).** The OU-model-implied
stationary σ replaced the empirical rolling std as the only changed variable, and it strictly
*degraded* OOS performance. Exactly the pre-registered conclusion: **a model, not an edge** — the
fitted OU parameters are non-stationary in crypto, so the parametric normalizer adds nothing and
in this sample subtracts. The simple empirical z-score is the better choice; `pairs_coint` stays
the board's pair strategy.

### AMENDMENT 2026-08-05 — the verdict above no longer follows from its own numbers

**The verdict as written is preserved.** This is an amendment, not a revision: what was concluded
in 2026-07 stands as the record of what was concluded then, and is wrong now for the reasons below.

**Trigger.** `docs/EDA-microstructure-001.md` §2 measured transaction cost from the recorded book
instead of assuming it. `backtest.py:84` charges `cost_bps=10 + slippage_bps=2` = 24 bps
round-trip; measured for BTCUSDT perp at a $1-5k clip is **10.02 bps round-trip** (fee 10.0
published + spread 0.0157 measured + slippage 0.0079 measured). The standing assumption is
**2.4x conservative**, so every verdict scored under it was scored against the wrong toll.

**The full candidate set was re-scored, not a subset.** 16 candidates, declared before running,
all reported including the 15 that did not move (`EDA-microstructure-001.md` §4bis-B). Running
only the interesting ones would have been a search; the distinction is not intent, it is whether
the failures appear.

**What changed for B2:**

| state | `pairs_ou` DSR | baseline DSR | delta | verdict |
|---|---:|---:|---:|---|
| original run-log (24 bps, 8.4 yr data) | 0.04 | 0.07 | **-0.03** | KILL |
| re-score (24 bps, 8.6 yr data) | 0.03 | 0.00 | **+0.02** | KILL |
| re-score (**10.02 bps**, 8.6 yr data) | 0.06 | 0.00 | **+0.05** | **SURVIVES** |

**The flip is NOT attributable to the cost correction alone, and the data is the larger driver**
[DIUKUR]. Holding cost fixed at 24 bps, extending the window from 8.4 to 8.6 years moved delta by
**+0.05** — mostly because the baseline `pairs_coint` fell from 0.07 to 0.00. The cost correction
then moved delta a further **+0.03**. Attributing the whole flip to the audit would overstate
what the audit found.

**Read "SURVIVES" precisely.** The B2 criterion is **relative** — does the OU normaliser beat a
fixed z-score — and it now passes at delta = 0.05, **exactly on the threshold**. `pairs_ou` sits
at **DSR 0.06**, which is **0.89 below the 0.95 promotion bar**. Not tradeable, not promotable,
and not a finding about profitability. A hair either way flips it back.

**Net change to the public board: still none.** `pairs_ou` is not promoted.

**What the re-score establishes, and it is the more useful half:** *fifteen of sixteen verdicts
did not move.* "We re-checked every recorded verdict under a corrected cost and one relative
criterion shifted" is a far stronger statement about this run-log's reliability than "one
shifted" would be alone. The two candidates an analytic bound could not close —
`donchian_breakout` and `ma_trend + fixed_r_exit` — were resolved by the re-score at DSR 0.21 and
0.59 and stand comfortably.

---

## ADDENDUM 2026-08-04 — two findings the DSR column did not carry

Both measured on the same instrument that reproduces this run-log's numbers: `compare.py`'s own
data (`BTC-USD`, coinbase, `1d`, `start=2018-01-01`, 3,138 bars) and `backtest.walk_forward`
(5 folds, 10.02 bps round-trip). Positive control: `donchian_breakout` re-measures at OOS DSR
**0.2108**, reproducing the **0.21** recorded above.

### `donchian_breakout` is correlated with the RANDOM CONTROL, and that is the stronger verdict [DIUKUR]

| pair (OOS returns, n = 2,615) | ρ |
|---|---:|
| **`donchian_breakout` ~ `random_entry`** | **0.5255** |
| `donchian_breakout` ~ `buy_and_hold` | 0.0762 |
| `ma_trend_filter` ~ `random_entry` | 0.3085 |
| `ma_trend_filter` ~ `buy_and_hold` | 0.7257 |
| **`buy_and_hold` ~ `random_entry`** | **−0.0460** |

**The obvious confound is refuted by its own control.** "Both are long a market that rose" would
predict a high `buy_and_hold ~ random_entry` correlation. It is **−0.05**. And `donchian` tracks
the coin-flip control **7× more closely than it tracks buy-and-hold** (0.53 vs 0.08). Shared
market exposure does not explain this; `ma_trend_filter` is the strategy that *is* mostly long
BTC (ρ 0.73 with buy-and-hold), and it is a different animal.

**Not a seed artefact.** Over 15 seeds of `random_entry`, ρ with `donchian` has median **0.5255**
(range 0.2895–0.6824), while ρ of the same 15 controls with `buy_and_hold` has median **−0.1137**
(range −0.2102–0.3048). The separation holds across every seed.

**Why this is stronger than the DSR.** DSR 0.21 says *not distinguishable from luck*. The
correlation says something sharper and harder to argue with: **not distinguishable from the
random control itself.** Whatever the 55/20 channel logic contributes, it is mostly the act of
trading sporadically in both directions — which is precisely what `random_entry` does by
construction. A KILL on DSR can be re-litigated by finding a better cost model or a longer
sample. This cannot: the candidate would first have to stop resembling a coin flip.

### `pairs_coint` falling 0.07 → 0.00 is BREADTH, not regime and not a bug [DIUKUR]

`pairs_coint` holds a position on **58 of 2,615 OOS bars — 2.2 %**. Per calendar year the active
bar count is 7 / 3 / 4 / 7 / 9 / 10 / 3 / 15 (2019→2026). Yearly Sharpe on 3–15 active bars is
not an estimate of anything.

- **Not a bug.** `pairs_coint(entry=2.0, exit=0.5, stop=4.0, window=60, max_half_life=60)`
  (`strategies.py:525-532`) enters only at `|z| > 2` on a 60-day rolling window, then gates
  again on a cointegration half-life guard. Under normality `|z| > 2` is ~4.6 % of bars; ~2.2 %
  after the guard is what the design asks for.
- **Not regime.** The per-year sign alternates (−0.70, −1.56, −1.13, +0.43, −0.68, +1.20, −1.09,
  −1.00) with no trend. Nothing decayed; there was never a stable level to decay from.
- **Sample, specifically near-zero breadth.** Grinold–Kahn: `IR ≈ IC·√breadth`. At 58 bets over
  8.6 years the breadth term is so small that the whole-period Sharpe (−0.40) is a handful of
  trades. **A move of 0.07 → 0.00 is inside this series' own noise**, and extending the window by
  0.2 years is more than enough to produce it.

**Consequence, recorded but NOT adjudicated.** The B2 verdict compares `pairs_ou` against
`pairs_coint` **as the baseline**, and lands on Δ = 0.05 exactly at the threshold. That baseline
is flat 97.8 % of the time — closer to cash than to a strategy. This does not change the B2
verdict, which stands as recorded; it records that the verdict's denominator is thinner than the
comparison implies. Any future re-reading of B2 must start here.


---

## B3 — carry as a validated entry

**Honest flag, up front.** Carry is a **funding-stream** sleeve (long spot / short perp), not a
price-position strategy, so it cannot enter the spot walk-forward leaderboard by construction.
Keyless funding history is ~hundreds of 8h intervals — far below the MinBTL needed to distinguish
skill from luck for the board's trial count.

**Method.** Report carry's realized APR descriptively and state the insufficiency explicitly:
funding-intervals-available (≈ years) vs MinBTL-required-years. Never assign it an OOS Deflated
Sharpe or a leaderboard rank.

**Kill criterion.** Definitional — carry is **never** ranked and never gets a DSR. The deliverable
is the honest descriptive label (`compare.py` already prints one; the dashboard gets a matching
OOS-insufficient line on the Perpetual panel). Always **descriptive**.

**Results** (same run): carry over the keyless Bybit funding feed = **200 8h-intervals ≈ 0.18 yr**
(long spot / short perp when funding > 0). `compare.py` prints it descriptively — "OOS n/a … <
MinBTL — not OOS-rankable on keyless history." The dashboard Perpetual panel now carries a matching
OOS-insufficient line next to the carry APR.

**Verdict — Descriptive only, by construction (pre-registered).** 0.18 yr of funding history is two
orders of magnitude below the MinBTL needed to distinguish skill from luck for the board's trial
count; carry is never ranked and never gets a Deflated Sharpe. Honest by design.

---

## MinBTL headroom cost

| | N | MinBTL required | data | verdict |
|---|---|---|---|---|
| public board | 5 | 2.70 yr | 8.4 yr | ok |
| research (+ B1/B2 candidates) | 8 | 2.85 yr | 8.4 yr | ok |

With the full 2018→ daily history (8.4 yr) MinBTL is satisfied at both N, so it is **not** binding
here — the headroom squeeze bites on shorter windows (e.g. a 2022→ run). The point stands directionally:
each added strategy raises the required MinBTL (2.70 → 2.85 yr) and lowers every strategy's deflated
Sharpe. Since **neither candidate survived**, the public board is unchanged (still N = 5); nothing was
promoted, which is the intended result.

## Summary

| candidate | OOS DSR | baseline | verdict | reason |
|---|---|---|---|---|
| B1 tsmom_voltarget | 0.89 | 0.89 (board tsmom) | **KILL** | corr 1.00 — literal duplicate |
| B2 pairs_ou | 0.04 | 0.07 (fixed-z) | **KILL** | Δ −0.03 — model, not edge |
| B2 pairs_ou *(amended 2026-08-05)* | 0.06 | 0.00 (fixed-z) | **SURVIVES** its relative criterion | delta +0.05, exactly at threshold — but DSR 0.06 is 0.89 below the promotion bar; **not promoted**. Driven more by the extended data window than by the cost correction. See the B2 amendment. |
| B3 carry | — | — | **descriptive** | ~0.18 yr ≪ MinBTL |

**Net change to the public board: none.** The pass succeeded by correctly rejecting all three. The
harness did its job — it refused to let a duplicate or a non-stationary model onto the board.
