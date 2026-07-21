# EVT tail risk (POT-GPD) — run-log (risk measurement, no edge claim)

**What was added.** `risk.evt_pot_tail` (mirrored as `Quant.evtPotTail`, parity-pinned):
Peaks-Over-Threshold Generalized-Pareto tail VaR/ES on daily losses — threshold `u` at
the 95th loss percentile, GPD `(ξ, β)` fit to exceedances by **probability-weighted
moments** (Hosking & Wallis 1987 — closed-form and deterministic, hence bit-identical
Python ↔ JS; scipy MLE serves only as a test-side cross-check), tail quantities via the
standard POT formulas (McNeil–Frey–Embrechts 2005 ch. 7; Pickands–Balkema–de Haan).
Displayed beside the historical VaR/CVaR rows (`run_backtest` table + dashboard
Performance panel, tagged `POT-GPD · PWM`). Guards: fewer than 30 exceedances → NaN
with the count reported (no tail is fitted from nothing); `ξ ≥ 1` → ES undefined, NaN.

**A spec bug caught by fundamentals.** The task brief specified the PWM weight
`b₁ = Σ (i/(n−1))·y₍ᵢ₎ / n` (ascending) — that is the estimator of `E[Y·F]`, which makes
`b₀ − 2b₁ < 0` for *every* sample (the closed form then returns `ξ̂ = 4 − ξ`). The
correct Hosking–Wallis convention for this closed form is `α₁ = E[Y(1−F)]` (ascending
sort, weights `(n−1−i)/(n−1)`). Verified numerically: recovers `ξ 0.30 → 0.2994`,
`β 0.02 → 0.0202` at n=5000, agreeing with scipy `genpareto` MLE to `Δξ = 0.005`. The
implementation follows the correct convention and documents it.

**Result — BTC-USD daily, 2018-01-01 → 2026-07, n = 3,123, exceedances = 157:**

| quantity | EVT (POT-GPD) | historical quantile |
|---|---|---|
| tail shape | **ξ = 0.172** (fat tail, heavier than exponential) | — |
| VaR 99% | −9.36 %/day | −9.74 %/day |
| ES 99% | −12.91 % | −12.75 % |
| VaR 99.9% | **−17.7 %** | −14.9 % |
| ES 99.9% | **−22.9 %** | −21.1 % |

**The honest headline.** At 99% EVT and the empirical quantile *agree* — with ~31
observations behind VaR₉₉ they should, and that agreement is a live cross-validation of
the fit, not a disappointment. EVT earns its keep **further out**: at 99.9% the
empirical quantile rests on 3 observations while the GPD extrapolates the fitted tail —
a ~19% fatter loss estimate (−17.7% vs −14.9%). That is precisely the region where
"historical VaR" silently under-states risk, and precisely the claim EVT exists to make.

**Verification.** Hand-computed POT formulas on the reported `(ξ, β, u, n, n_u)`
reproduce the implementation to 4 decimals (orchestrator-independent check); synthetic
GPD recovery (ξ ±0.08 at n=5000); exponential-tail `ξ→0` limit branch hand-pinned;
uniform-tail `ξ = −1` exact hand case; ES ≥ VaR invariant; sign convention matches
`risk.var`/`risk.cvar` (signed returns). pytest 198→**207**; parity 74→**79** fields
(EVT fields Python↔JS `|Δ| = 0.00e+00`); smoke 46; rails untouched.

**Deferred (stated, not hidden):** threshold-sensitivity diagnostics (ξ stability across
`threshold_q` 0.90–0.97, mean-excess plot) — the standard next EVT hygiene step; and a
conditional (GARCH-filtered) variant is roadmap #6.
