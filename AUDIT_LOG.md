# AUDIT_LOG.md — changes made under AUDIT.md (severity-ranked findings → verified fixes)

Each entry: finding id, what changed, before/after, and the test that proves it. The full
findings report (no Critical; 1 High; ~8 Medium; ~12 Low; 4 Info) was produced read-only
before any edit, per the spec.

## 2026-06-16 — H1/M3/M1-carry: perp carry P&L was spot returns, not funding accrual

**Findings fixed:** H1 (High, verified) carry P&L computed from spot price returns instead
of funding; M3 (Medium) carry annualized at the spot ppy (365) not the 8h funding cadence
(~1095); M1-carry (Medium) `bfill()` on the funding-clock spot series leaked future prices
backward.

**Before:** `compare.py` and `run_backtest.py` built a spot-close series on the funding
index (`close.reindex(funding.index).ffill().bfill()`) and ran the carry position through
`backtest.run`, whose P&L is `position × spot_pct_change`. For a delta-neutral long-spot/
short-perp trade that books the *directional spot exposure of an on/off signal* — the wrong
quantity — and contradicts the repo's own mandatory rail (RESEARCH.md §Funding). The JS
mirror (`quant.js carryBacktest`) was already correct; the Python source-of-truth had
regressed.

**After:**
- New `btcquant.backtest.run_funding(positions, funding_rate, …)`: P&L = `−traded_posₜ ·
  funding_rateₜ` (short perp receives funding when funding > 0), with the same no-look-ahead
  1-bar shift and turnover cost as `run`, annualized on the funding cadence. Mirrors
  `quant.js carryBacktest`. No spot price is used — the trade is delta-neutral by construction.
- `compare.py` carry line now calls `run_funding` on `funding["funding_rate"]` with the
  cadence inferred from stamp spacing (`_funding_periods_per_year`, ≈1095 for 8h); no `bfill`.
  Relabeled "perp FUNDING accrual, delta-neutral" + % time-in-trade.
- `run_backtest.py --strategy carry` routes through a dedicated `_run_carry` (funding engine;
  no spot buy-and-hold baseline / walk-forward / price tearsheet, since none apply).

**Test:** `tests/test_core.py::test_run_funding_books_funding_accrual_not_spot_price` — a short
perp (-1) under constant positive funding earns (equity ↑), a long (+1) pays (equity ↓), flat
earns ~0, and the per-interval net equals +funding (no-look-ahead shift). Independent of price.

**Status:** fixed + tested. Remaining audit findings (M1-pairs bfill, M2 two-leg pairs cost,
M4 lockbox, M5 IC HAC SE, M6 trial-count, M7 app.js `--check` in CI, M8 options parity, Low/Info)
remain open pending the regime-gate research pass.

## 2026-07-11 — M6: DSR trial-count/variance convention unified (Bailey–LdP 2014 as written)

**Findings fixed:** M6 (Medium) — every DSR call site used its own `(N, V)` convention: the
leaderboard deflated for N strategies but with the skill-less null `V = 1/n_oos` even though all N
trial SRs were sitting in its own rows; `walk_forward` hardcoded `V = 1/n_oos` despite having every
per-fold SR; `run_funding` never stored `var_trials_sr` (asymmetric with `run`); N=1 runs printed
"Deflated Sharpe" when nothing was deflated. Plus one **real JS bug** found under the same audit:
`quant.js deflatedSharpe(..., nTrials=1, ...)` fed N=1 through the Gumbel expected-max formula,
hit `normPpf(1 − 1/1) = normPpf(0) = −Infinity`, so `sr0 = −Inf` and the mirror returned
**1.0 identically for ANY inputs** at N=1 — a fabricated 100% significance for exactly the
"no search happened" case. Empirical evidence (old `HEAD` mirror vs fixed, N=1, V arbitrary):

```
sr=0.08  old: 1  → new: 0.8933575631   (Python: 0.8933576314, |Δ|=6.8e-8)
sr=-0.50 old: 1  → new: 0.0001757560   (a LOSING strategy scored 100% significant)
sr=0.00  old: 1  → new: 0.5000000005
```

The existing parity pins never caught it because they were saturated (`dsr = 6.8e-120 vs 0` —
both tails, |Δ| ≈ 0 detects nothing). A second masked divergence fell out of the new unsaturated
probes: JS `computeStats` fed the DSR a **biased** kurtosis while `risk.summary` uses scipy
`bias=False` — fixed in `Q.kurtosis(xs, excess, unbiased)` (C4).

**THE CONVENTION (M6, binding — condensed; verbatim block in `btcquant/risk.py` docstring):**
- **C1** `DSR := PSR(sr0(N,V))`, `sr0 = √V·((1−γ)·Φ⁻¹(1−1/N) + γ·Φ⁻¹(1−1/(N·e)))`,
  γ = 0.5772156649015329. `N=1 ⟹ sr0=0 ⟹ DSR ≡ PSR`, **labeled**
  `'PSR (single trial — no deflation)'` wherever displayed; stats carry `dsr_is_psr: true`
  (numeric key `deflated_sharpe` unchanged).
- **C2** `V` = the **empirical ddof=1 variance of the per-period SRs across the N trials**
  whenever the trial SRs are in hand — honest in BOTH directions, no invented `max(V, 1/n)`
  floor. The `1/n_periods` null fallback fires ONLY when trial SRs are genuinely unavailable,
  flagged `var_fallback: true` and printed as
  `'null-variance fallback — deflation may be under- or over-stated'`.
- **C3** N by surface: `compare.py` leaderboard N = strategies ranked, V = empirical var of
  their OOS per-period SRs; `walk_forward` keeps N = n_splits (folds-as-trials) with V =
  empirical var of the per-fold OOS SRs; `run`/`run_funding` keep the caller's `n_trials` and
  both store `var_trials_sr`.
- **C4** PSR internals unchanged (per-period SR, `bias=False` moments, non-excess kurtosis,
  `sqrt(1 − skew·SR + (kurt−1)/4·SR²)` denom, `√(n−1)`); **C5** the JS mirror is
  semantics-identical, incl. the N=1 case, with unsaturated parity pins.

**Before → after (public leaderboard, `compare.py`, 2018-01-01→2026-07-10, 3113 bars, N=5,
V: 1/n_oos → empirical 0.000345):**

```
                    BEFORE (V = 1/n_oos)      AFTER (V = empirical, ddof=1)
strategy            OOS DSR                   OOS DSR
tsmom                 0.94                      0.95   (precise 0.946458 — see note)
buy_and_hold          0.81                      0.83
tsmom_ls              0.80                      0.82
ma_trend_filter       0.73                      0.75
pairs_coint           0.16                      0.17
PBO 0.64 (unchanged) · MinBTL 2.70 yrs (unchanged) · rank order unchanged
```

**Display note:** tsmom's AFTER prints as **0.95 at 2dp but is precisely 0.946458 ≤ 0.95**, so it
correctly carries **no `*` significance marker** — "0.95 without a star" is rounding, not a bug.

**Kill-verdict continuity (pre-registered gate, verified on `compare.py --research` 2026-07-11):**
the Tier-B/Tharp kill bar stays **0.95 on the walk-forward folds-DSR** (now labeled
`OOS DSR (folds)`). Empirical cross-fold SR variance ≫ 1/n_oos here, so deflation *strengthened*
and every published KILL verdict moved **down** — none flipped upward:

```
candidate                folds-DSR published → M6    verdict
donchian 55/20                 0.50 → 0.12            KILL (unchanged)
vwap_reversion 48              0.00 → 0.00            KILL (unchanged)
ma_trend + fixedR 2:3          0.69 → 0.34            KILL (unchanged)
random_entry (control)         0.36 → 0.17            KILL (unchanged)
B1 tsmom_voltarget   corr 1.00 > 0.95 clause          KILL (unchanged; Δ now +0.07)
B2 pairs_ou          Δ −0.05 < +0.05 clause           KILL (unchanged)
```

**Adversarial battery (all HELD):** single-fold walk_forward (V falls back to 1/n_oos,
`var_fallback=False` since N=1 deflates nothing, `dsr_is_psr=True`, DSR finite ≡ PSR); two
identical folds (empirical V→0 ⟹ sr0→0 ⟹ DSR→PSR, finite, NOT flagged — V=0 is *measured*, not a
fallback); N=2 tiny (matches hand-computed sr0); V both ≫ and ≪ 1/n flow through unclamped and
monotonically (DSR(V↑) < DSR(V↓)); JS vs Python N=1 across 5 randomized input sets + V=0/V≫1/n/
V≪1/n edges, worst |Δ| = 6.8e-8 < 1e-7; degenerate guards (N=0, V<0, V=NaN → NaN both sides);
grep sweep confirms the only remaining `1/n` sites are the flagged fallbacks + the Gumbel
`Φ⁻¹(1−1/N)` terms themselves.

**Tests:** `tests/test_core.py` — hand-pinned `sr0(N=5, V=0.001) = 0.03771313367059893` (DSR=0.5
exactly at sr=sr0), N=1 flag wiring, `run_funding` var storage, walk_forward fold-V with
hand-computed SRs/variance, `compare._empirical_var_sr`; `scripts/check_parity.py` — unsaturated
pins `dsr_n1 = 0.8933576314257702` (≡ PSR, any V) and `dsr_mid = 0.6072585304659127` (N=5,
V=0.001) anchored to pre-registered constants, + 4-field walk-forward probe
(`wf_oosSharpe/wf_varTrialsSr/wf_deflatedSharpe/wf_varFallback`). Suite: pytest **157** passed
(was 143 pre-M6), parity **41** fields PASS (was 37; worst |Δ| = 1.63e-7, on `rsi`),
`check_terminal` 46/46, `node --check` + `app.js --check` clean.

**Status:** fixed + tested (M6 closed). Known residual: `compare.py`'s carry line calls
`run_funding` with `n_trials=N` and no trial SRs, so `var_fallback` is set in its stats — but the
carry line never displays a DSR (descriptive-only by design), so no caveat prints; the flag is in
the stats if a future surface shows one. Remaining audit findings (M1-pairs bfill, M2 two-leg
pairs cost, M4 lockbox, M5 IC HAC SE, M7 app.js `--check` in CI, M8 options parity, Low/Info)
remain open.
