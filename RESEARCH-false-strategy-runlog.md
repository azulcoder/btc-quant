# False Strategy Theorem — run-log (honesty diagnostics)

**Question:** the leaderboard already deflates each strategy's Sharpe against `sr0`, the
*expected maximum Sharpe of N skill-less trials* (Bailey & López de Prado 2014). But two
things were left implicit: (1) `sr0` was buried inline — you could not read off *the
Sharpe a strategy must clear* to escape the luck-of-N null; and (2) the trial count `N`
was the **literal** number of strategies, even though several are near-duplicates. If the
board's `N=8` is really only ~3 independent bets, the naive count over-states how much
was searched and the hurdle it implies is wrong. This log surfaces both, as diagnostics —
it changes no production number.

## What already existed vs. what is new (honesty rail)

**Already present** (do not re-credit as new): the False Strategy Theorem *core* —
`sr0 = √V · [(1−γ)·Φ⁻¹(1−1/N) + γ·Φ⁻¹(1−1/(N·e))]`, γ = 0.5772156649 — was already
computed, but **inline and duplicated** inside `risk.deflated_sharpe_ratio` *and*
`risk.min_backtest_length`. The Deflated Sharpe the board prints IS a False-Strategy test.

**New in this pass** (`btcquant/risk.py`, mirrored in `dashboard/quant.js`, parity-pinned):
- `expected_max_sharpe_ratio(n_trials, var_trials_sr)` — the `sr0` core factored into one
  named, tested, public function; `deflated_sharpe_ratio` and `min_backtest_length` now
  both call it (the two inline copies removed; results byte-identical, regression-tested).
- `false_strategy_threshold(n_trials, var_trials_sr, n_periods, skew, kurt, prob=0.95)` —
  **the explicit hurdle**: the minimum per-period Sharpe such that
  `PSR(sr; sr0) ≥ prob`. Because the PSR denominator depends on the Sharpe itself, the
  threshold is a fixed point, solved iteratively; re-feeding `sr*` into PSR returns
  exactly `prob` (tested to 1e-6).
- `effective_number_of_trials(returns_matrix)` — **N_eff**, the eigenvalue *participation
  ratio* `(Σλ)² / Σλ²` of the trials' correlation matrix (Bailey-LdP; Harvey, Liu & Zhu
  2016, multiple testing under correlation). `N` identical strategies → ~1; `N`
  independent → ~N.
- `probability_false_strategy(max_sharpe, n_trials, var_trials_sr, n_periods, skew, kurt)`
  — the family-wise `P(the best-of-N is a false positive) = 1 − PSR(SR_max; sr0(N))`.

`scripts/compare.py` prints a **False-strategy diagnostics** block beside PBO / MinBTL.

## Result (`compare.py --research`, BTC-USD 1d, 2018→2026, N=8)

```
PBO (selection overfit, CSCV): 0.61          [>0.50 ⇒ ranking is essentially noise]
MinBTL for N=8: 2.85 yrs vs 8.5 yrs of data  (ok)

False-strategy hurdle (95%, on 'tsmom'):
  annualized OOS Sharpe > 1.13  (naive N=8)
                        > 0.91  (effective N_eff = 2.95)
  top OOS Sharpe        = 0.98
P(top strategy is a false positive) = 10.7%   [= 1 − DSR of the best row; DIAGNOSTIC]
```

**The finding.** The board carries **N = 8 nominal trials but only N_eff ≈ 2.95 independent
ones.** `tsmom`, `tsmom_voltarget`, and `tsmom_dir` are ~corr 1.00 (B1 in Part B is a
literal duplicate); they collapse to ~one bet, so the eight rows are really ~three
searches. This is decision-relevant: under the **naive N=8** the false-strategy hurdle is
Sharpe **1.13**, which the top strategy's **0.98 fails** — you would call it luck-of-eight.
Under the **honest N_eff=2.95** the hurdle is **0.91**, which 0.98 **clears**. The verdict
on whether `tsmom` is a false strategy *flips* on the trial-count convention — exactly the
kind of hidden methodology lever this diagnostic exists to expose.

(The `0.98` / `DSR 0.89` here vs `1.01` / `0.91` on other days is the documented live
current-day-bar drift in `compare.py`, not an FST effect; the FST block is diagnostic-only
and the leaderboard DSR column is byte-unchanged by this pass.)

## The methodology question this pre-registers (NOT decided here)

Should the production leaderboard deflate against **N** or **N_eff**? Arguments both ways,
same shape as the [DSR trial-variance decision](RESEARCH-dsr-convention.md):

- **Keep N (literal).** Every row IS a distinct backtest you ran and could have picked;
  the expected-max over N is the honest luck-of-selection benchmark, and B1's own Part-B
  log already KILLs the corr-1.00 duplicate rather than pretending it is independent.
- **Use N_eff.** The eight rows are ~three independent bets; deflating against eight
  over-states the search and over-deflates the survivors (it is why the naive hurdle 1.13
  exceeds even a genuinely-decent 0.98). PBO/MinBTL already carry the raw-count selection
  story separately.

Left as azul's call, with the numbers now in hand (`scripts/compare.py` prints both; the
switch would be a one-line `N → round(N_eff)` in the leaderboard `sr0`, its `quant.js`
mirror, and a re-pin — reversible, no strategy P&L touched). **Related but distinct from
the A→B2 variance decision (`RESEARCH-dsr-convention.md`): that changed `V`; this would
change `N`.**

## Rails

Pure honesty / risk math with **no edge claim** — needs no MinBTL and no pre-registration
(it measures the selection process, not a strategy). Every function is parity-pinned
(Python ↔ quant.js, 1e-7) and hand-tested (`expected_max(5,1)=1.1926`, `(10,1)=1.5746`;
threshold fixed-point → PSR=0.95; `N_eff` on identical/independent/mixed matrices → 1 / N /
~3). The refactor of the inline `sr0` is regression-tested byte-identical.
