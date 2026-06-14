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
| B3 carry | — | — | **descriptive** | ~0.18 yr ≪ MinBTL |

**Net change to the public board: none.** The pass succeeded by correctly rejecting all three. The
harness did its job — it refused to let a duplicate or a non-stationary model onto the board.
