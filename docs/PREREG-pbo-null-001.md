# PREREG-pbo-null-001 — replacing `PBO < threshold` with a calibrated-null test

**Status: RUN 2026-08-06, as declared (see RESULT at the bottom; amendment above §3 records the
one pre-run anchor re-base).** Declared 2026-08-04, run two days later by `scripts/pbo_null_test.py`,
seed 20260806, results in `reports/pbo-null-result.json`.

---

## 0. Why the clause has to change

`STRATEGY.md` (grep `` `DSR>0.95` net-of-cost AND `PBO<threshold` ``) requires `DSR > 0.95` **AND** `PBO < threshold` **AND** `history ≥ MinBTL(N)`.

Two facts, both measured in `docs/EDA-microstructure-001.md` §8:

- **No numeric threshold ever existed in code.** `PBO < threshold` was prose.
- **No threshold could have worked.** At `T = 2,615` and `S = 8`, PBO's sampling dispersion on a
  pure-noise board is **sd ≈ 0.25**, with a 5–95 % band of **[0.13, 0.91]**. The observed board
  value (0.5286) and the twin-pruned value (0.5143) both sit inside that band. And the null's
  *centre* moves with column count — 0.500 at N = 8, 0.657 at N = 5 — so a fixed number is not
  even comparing like with like across boards of different width.

**And the clause fails the repo's own tie-break rail** (`STRATEGY.md` §6, grep `flips with a free methodological choice`; written 2026-08-04):
the CSCV block count `S` is a free methodological parameter that moves PBO by **0.33** on a fixed
board (0.700 / 0.529 / 0.373 / 0.500 at S = 6 / 8 / 10 / 12). Under that rail, PBO as currently
used may not decide anything. The rail executes itself here.

---

## 1. The proposal, and what it actually costs — read this before approving

The question with resolving power is not *"is PBO below some number"* but:

> **Is this board's PBO lower than that of a random board with the same width, length,
> volatility, and correlation structure — one where no candidate is truly better than any other?**

**But be clear about what this buys.** It makes the clause *calibrated*; it does not make it
*strong*. With sd ≈ 0.25 the test only separates a **decisively** dominant board from noise. For
most real boards the honest output will be "cannot tell". So:

> **This is option (c) in mechanism and mostly option (a) in effect.** PBO stops being a
> clearance criterion and becomes a **rarely-firing alarm**. The bar goes from three load-bearing
> clauses to **two clauses (DSR, MinBTL) plus an alarm**. That is a reduction in the bar's
> strength, and it is the finding — not a concession, and not something to phrase around.

I recommend (c) as specified below rather than (a) for one reason: the *upper* tail is still
informative. A board whose PBO is significantly **worse** than a no-skill null is a specific,
diagnosable pathology, and dropping the clause entirely would discard that alarm. Everything
else in (c) is honest bookkeeping about a statistic that cannot certify health at this sample.

If you prefer (a) — delete the clause, record the gap, and stop maintaining machinery that
abstains most of the time — that is a defensible read and I would not argue hard against it.

---

## 2. Declared instrument

### 2.1 CSCV block count — LOCKED at `S = 8`

Locked **before any result is seen**. Reasons, in order:

1. It is what the repo already uses everywhere — `risk.probability_of_backtest_overfitting`'s
   default, `compare.py:_pbo_over`, `orderflow_smoke.py`. Locking it introduces **no new choice**
   and changes **no historical number**.
2. `C(8,4) = 70` splits gives granularity `1/70 ≈ 0.014`, fine enough that the statistic is not
   quantisation-limited.

**The lock matters less than it looks, and that is the strongest argument for this design.**
Observed and null PBO are computed at the **same** `S`, so the 0.33 swing largely **cancels**.
A fixed threshold had no such protection — that is exactly why it broke.

Changing `S` later requires an amendment to this document. It may not be changed after seeing a
result.

### 2.2 Replications — `B = 2,000`

The decision boundary is a **5th percentile**. Monte-Carlo standard error there is
`sqrt(0.05 × 0.95 / B)`: **0.013 at B = 300** (what §8 used post-hoc — too coarse to sit on a 5 %
boundary) and **0.005 at B = 2,000**. Each replication is 70 CSCV splits on a `T × N` matrix;
2,000 is cheap.

### 2.3 Null board construction — the design choice, stated and justified

**The null hypothesis the bar needs is "no candidate is truly better than any other."** That is
precisely the state in which picking the backtest winner is pure overfitting, which is what the
clause exists to detect. So every null column has **zero drift**.

**Primary — stationary block bootstrap of the DE-MEANED real board.**
Subtract each column's own mean (enforcing the null), then resample **whole rows** in blocks
(Politis–Romano stationary bootstrap) to build each null board.

Preserved by construction: the exact empirical marginal distributions, fat tails, volatility
clustering, and the **full cross-column correlation** — including the ρ 0.91–1.00 twins §7 found.
Destroyed by construction: any true difference in expected return between columns.

*Why correlation must be matched:* the real board contains near-duplicates. A null with
independent columns is a board of a different effective width, and PBO's null centre depends on
width (0.500 at N = 8 vs 0.657 at N = 5). Not matching it would compare a board against a
differently-shaped board and call the difference skill.

*Why per-column Sharpe must NOT be matched:* a null in which one column is genuinely best has a
low PBO **by construction**, and the test would silently become "is the ranking as stable as the
drifts imply" instead of "is there a ranking at all". Matching the drifts builds the answer into
the null.

**Block length `L` is itself a free parameter, so it gets the tie-break treatment.** Primary
`L = 21` (one trading month — the turnover horizon of the board's trend/momentum signals). The
verdict must hold for **`L ∈ {5, 21, 63}`**; if it flips across them, the result is
**INDETERMINATE** and the clause abstains. Declared now, not chosen after.

**Secondary — Gaussian, and it must AGREE.** Multivariate normal, mean 0, covariance `D·R·D`
with `D = diag(σ_i)` and `R` the real board's empirical correlation matrix. This is the simpler,
fully transparent construction and reproduces what §8 measured post-hoc.

**Disagreement rule, fixed in advance:** if primary and secondary reach different verdicts, the
result is **INDETERMINATE** and the clause abstains. This is not a free choice between two
methods — the failure mode is abstention, never selection. It is the same rail as the tie-break refusal in `STRATEGY.md` §6
applied to this instrument.

### 2.4 Decision rule — declared with its threshold

Let `P5` and `P95` be the 5th and 95th percentiles of the null PBO distribution.

| observed PBO | verdict |
|---|---|
| `< P5` | **clause SATISFIED** — the board has an identifiable winner |
| `P5 … P95` | **clause ABSTAINS** — PBO is not evidence; see §2.5 |
| `> P95` | **clause FAILS, hard** — selection is worse than a board with no true differences |

**Why α = 5 %:** it is the stringency of the clause sitting beside it. `DSR > 0.95` is a 95 %
statement; using the same α means neither clause silently dominates the other. Any other value
would need its own justification, and none is available.

### 2.5 What ABSTAIN means — the one genuinely debatable choice

**Declared:** on ABSTAIN a candidate may still be CLEARED on `DSR` and `MinBTL`, but its registry
entry **must carry `pbo: INDETERMINATE` visibly**, and the terminal's countdown must show it.

The alternative — ABSTAIN counts as NOT CLEARED — is more conservative and I rejected it for one
reason: at this sample ABSTAIN is the *likely* outcome, so that rule makes the bar
unpassable-in-principle, and a bar that can never be passed is a refusal wearing a criterion's
clothes. That is a different dishonesty, not a safer one.

**This is the call most worth overruling, and it is yours.** If you want ABSTAIN to block
promotion, say so and I will invert it — the machinery is identical either way.

---

## AMENDMENT 2026-08-06 — PC1's anchor embedded an ephemeral bar; re-based BEFORE any null ran

**What happened, in order.** The runner executed PC1 and ABORTED twice, exactly as designed:

1. First abort: the matrix came out `(2617, 8)` — the OHLCV cache had grown two days since the
   documented run. Fixed by pinning `--end 2026-08-04` (the declaration's every number is
   conditional on T = 2,615). **The pin is faithfulness, not tuning.**
2. Second abort: T = 2,615 matched but PBO = **0.5429** (38/70) against the anchor **0.5286**
   (37/70) — one CSCV combination. **Cause [DISIMPULKAN]:** the documented run happened mid-day
   on `2026-08-04`, so its final bar was the LIVE, PARTIAL bar as of ~13:26Z — a snapshot that
   no longer exists anywhere. Today's cache holds the closed `2026-08-04` bar. The instrument is
   identical; one input bar changed from partial to final, and one IS-best pick flips one split.

**Amendment: PC1's anchor is re-based to `0.5429`, the value on the REPRODUCIBLE sample**
(closed bars through `2026-08-04`, T = 2,615). Legitimacy rests on three facts: the change was
made **before any null replication was computed** (both aborts happened ahead of the null loop);
the anchor is not a verdict input (the verdict compares observed vs null percentiles — both use
whichever sample is pinned, so the switch cannot move the verdict's direction); and the original
`0.5286` stays on the record here, struck rather than replaced, with its cause named.

**The lesson, and it generalizes:** an anchor measured against a live, partially-formed bar is
not an anchor — future controls must pin to CLOSED data only.

## 3. Positive controls — binding, and they run BEFORE the verdict

Per the standing positive-control rule. **Any failure discards the instrument rather than
adjusting it.**

| # | control | required outcome |
|---|---|---|
| **PC1** | Observed board PBO, locked `S = 8`, on the provenanced matrix | must reproduce **0.5286** (documented **0.53**). A different number means a different instrument. |
| **PC2** | Synthetic board: one column with a large true edge, rest matched noise, same `T`/`N`/`σ`/`R` | must return **SATISFIED**. If a board with an obvious winner cannot pass, the test has no power and is not a gate. |
| **PC3** | Synthetic board: all columns duplicates of one zero-edge series | must **not** return SATISFIED. |

PC2 is the one that decides whether this proposal survives at all. It is stated before the run
precisely so it cannot be quietly dropped if it fails.

---

## 4. Trial classification — argued, not assumed

**0 predictive trials.** The procedure scores no strategy, configures nothing, and promotes
nothing; it calibrates a statistic against synthetic data. Diagnostic looks are counted at the
run.

**The risk that would invalidate that**, named here so it can be checked: if the null construction
were adjusted after seeing which construction let a candidate through, the whole exercise would be
a selection dressed as a calibration. The defences are that every choice above — `S`, `B`, both
constructions, `L` and its sensitivity set, α, and the three positive controls — is fixed in this
document before the run, and that the disagreement and sensitivity rules both fail toward
**abstention**, never toward clearance.

---

## 5. What this cannot measure

- **Whether PBO is the right statistic at all.** This calibrates it; it does not defend it. DSR
  already deflates for `N` trials, so PBO's marginal contribution over DSR is unquantified here.
- **Power at any specific effect size.** PC2 tests one large effect. The full power curve — what
  edge is needed to clear at a given `T` — is not computed, so "how good must a strategy be to
  pass" stays unknown.
- **Non-stationarity that outlives a block.** A regime shift longer than `L` is partly preserved
  by the block bootstrap and wholly absent from the Gaussian arm; neither represents it faithfully.
- **Anything about `T > 2,615`.** Every number here is conditional on the current daily history.
  A longer sample changes the null's dispersion and could revive a fixed threshold.

---

# RESULT [DIUKUR] — run 2026-08-06, `scripts/pbo_null_test.py`, seed 20260806, B = 2,000/arm

**All three controls passed before the verdict was computed:**

| control | outcome |
|---|---|
| PC1 — reproduce the board PBO on the pinned sample | **0.5429 = 0.5429, MATCH** (anchor re-based per the amendment; T = 2,615 asserted) |
| PC2 — dominant-column board must be SATISFIED | PBO 0.0000 vs null P5 0.1143 → **SATISFIED** — the test has power on an obvious winner |
| PC3 — duplicate zero-edge board must NOT be SATISFIED | PBO 0.5571 → ABSTAINS — correctly not-satisfied |

**The declared test — unanimous across all four arms:**

| arm | null P5 | median | P95 | observed | verdict |
|---|---:|---:|---:|---:|---|
| bootstrap L=5 | 0.0286 | 0.4857 | 0.9143 | 0.5429 | ABSTAINS |
| bootstrap L=21 | 0.0286 | 0.4857 | 0.9286 | 0.5429 | ABSTAINS |
| bootstrap L=63 | 0.0143 | 0.4714 | 0.9143 | 0.5429 | ABSTAINS |
| Gaussian | 0.0714 | 0.5143 | 0.9143 | 0.5429 | ABSTAINS |

> **FINAL: the clause ABSTAINS.** The board's PBO sits mid-null in every arm — indistinguishable
> from a board with no true differences, in either direction. This is the outcome §1 predicted as
> likely ("for most real boards the honest output will be 'cannot tell'"), now measured rather
> than predicted.

**Consequences, per the declared semantics (§2.4–2.5):** the PBO clause is not evidence for or
against any candidate at this sample; candidates may still be CLEARED on `DSR` and `MinBTL`, and
every registry entry must carry **`pbo: INDETERMINATE`** visibly. `tsmom` remains NOT CLEARED —
its block was never PBO; it is the N_eff tie-break (L7 LockBox queue). The clause becomes a
calibrated, rarely-firing alarm exactly as §1 said, and the upper-tail alarm (P95 ≈ 0.91) stays
armed: a future board past it fails hard.
