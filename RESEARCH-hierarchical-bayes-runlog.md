# Hierarchical-Bayes Sharpe shrinkage — run-log (honesty diagnostics)

**Question:** the board's raw Sharpes are order statistics — the top one is optimistic by
construction (winner's curse). The frequentist machinery (DSR, the False-Strategy hurdle)
already deflates for that. What does the *Bayesian* view of the same problem say, and do
the two agree? This pass adds the classic empirical-Bayes answer as a **complementary
diagnostic** — the production Deflated Sharpe (B2 convention) is unchanged.

## The model (`risk.hierarchical_bayes_sharpe`, mirrored in `quant.js`, parity-pinned)

Normal-normal hierarchical model, solved closed-form by empirical Bayes (James & Stein
1961; Efron & Morris 1975; DerSimonian & Laird 1986 for `τ²`; Gelman et al., *BDA*):

- Each observed per-period Sharpe `SR_i` is a noisy draw of a true skill `θ_i`:
  `SR_i ~ N(θ_i, σ_i²)` with `σ_i²` = **that strategy's own-Sharpe variance** — literally
  `risk.sharpe_estimator_variance` (the B2 quantity).
- The true skills come from a family: `θ_i ~ N(μ, τ²)`, `τ²` estimated by
  DerSimonian–Laird — with **`df = N_eff − 1`** (the correlation-aware variant): the
  board's near-duplicate cluster must not count as independent evidence about the
  family's spread.
- Posterior mean (the *shrunk* Sharpe): `θ̂_i = B_i·μ* + (1−B_i)·SR_i`,
  `B_i = σ_i²/(σ_i² + τ²)`; posterior sd → a credible interval and `P(θ_i > 0)`.

**Why this unifies the two open methodology debates:** the A-vs-B2 variance question and
the N-vs-N_eff trial-count question were both ad-hoc levers on one frequentist statistic.
Here both quantities appear *in their natural places at once* — `σ_i²` (B2, the
within-strategy noise) is the likelihood precision, the cross-strategy dispersion enters
as the estimated `τ²` (the principled version of what convention A groped at), and `N_eff`
sets the degrees of freedom of that estimate. One coherent model instead of three levers.

## Result (`compare.py --research`, BTC-USD 1d, 2018→2026, k=8, df=N_eff−1=1.95)

```
strategy           raw ann.SR  shrunk ann.SR  shrink%  P(skill>0)
tsmom / voltarget       0.96          0.85      24.6        1.00
tsmom_dir               0.89          0.79      25.3        0.99
buy_and_hold            0.77          0.70      26.1        0.98
tsmom_ls                0.71          0.66      25.0        0.98
ma_trend_filter         0.69          0.64      26.3        0.98
pairs_ou               -0.18         -0.00      25.4        0.49
pairs_coint            -0.59         -0.42      15.1        0.04

WINNER'S CURSE: top raw 0.96 ('tsmom') → shrunk 0.85 (Bayesian haircut 0.11).
Population: mu = 0.51, tau = 0.64 (annualized).
```

**Reading it.** The family's average true Sharpe is ~0.5 with a plausible skill spread of
~0.64 — so a raw 0.96 at the top is partly real, partly luck, and the posterior says its
best estimate is **0.85**. `pairs_ou` shrinks to ~zero with `P(skill>0) = 0.49` — the
model calls it *pure noise*, which is exactly what its KILL verdict said. `pairs_coint`
gets `P(skill>0) = 0.04`: near-certainly negative even after pooling.

## The honesty cross-validation (Bayesian vs frequentist)

Two independent machines, same verdict:

| Question | Frequentist (DSR/FST) | Bayesian (this pass) |
|---|---|---|
| Is the raw top Sharpe optimistic? | Yes — must clear an expected-max hurdle (0.91 under N_eff) | Yes — posterior haircut 0.96 → 0.85 |
| Is tsmom probably real skill? | Clears the N_eff hurdle (0.98 > 0.91) | `P(skill>0) = 1.00` |
| Are the pairs strategies dead? | DSR ≈ 0, KILLed | `P(skill>0)` 0.49 / 0.04 |

Agreement between a deflation argument and a shrinkage argument is not circular — they
model luck differently (extreme-value of N trials vs. partial pooling toward a family
mean) and still land on the same story. That is what an honest board should look like.

## Rails & scope

Pure honesty math, **no edge claim** → no MinBTL, no pre-registration. Diagnostic-only:
the leaderboard DSR column is byte-unchanged. Empirical Bayes is the closed-form
approximation (hyperparameters at point estimates); a full correlated-posterior MCMC is
the further frontier step and is *not* pretended here. Verified: hand-computed
DerSimonian–Laird case matches to 1e-9; identical-SR → full pooling; k=1 → no pooling;
`effective_n < k` widens `τ²` (direction asserted); Python ↔ JS parity-pinned
(74 fields). Option pre-registered: elevating the shrunk Sharpe to a headline column is
a one-line display change if ever chosen — a methodology decision, not a bug fix.
