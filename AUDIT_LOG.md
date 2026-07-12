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

## 2026-07-12 — M1-pairs / M2 / M4 / M5: leakage, two-leg cost, purge-embargo machinery, IC HAC

Four findings closed in one pass (each shipped with before/after + regression). Suite after:
pytest **173** passed (was 157 at M6 close; +5 test_ic HAC, +10 test_core M1/M2/M4, +1 the M2
adversarial-alignment guard), `check_parity` **55** fields PASS (was 41; +5 M2 pairs, +9 M4 CPCV,
worst |Δ| = **1.63e-07** unchanged — same `rsi` field as M6), `check_terminal` **46/46**,
`node --check` all clean, `make verify-browser` L1 exit-0 with **0 console / 0 page errors**.

### M1-pairs (Medium) — `bfill()` on the ETH alignment back-stamped a fabricated pre-listing price

**Before:** `compare.py` aligned ETH to BTC's index with `eth_close.reindex(px.index).ffill().bfill()`.
For any pair whose second leg started trading *after* BTC (the normal case — ETH listed later), the
trailing `.bfill()` copied ETH's **first observed price backward** into the leading region where ETH
did not yet trade, fabricating a spread there — a look-back leak identical in spirit to the M1-carry
bug. Adversarial reproduction (ETH starts at bar 150 of a 400-bar BTC index): `bfill` back-stamped
`1838.01` into bars 0–149, and `pairs_coint` emitted a non-NaN position in that fabricated region.

**After:** dropped the trailing `.bfill()` at both pairs sites (`pairs_coint`, `pairs_ou`) — **ffill
only** ⇒ the leading pre-ETH bars stay `NaN`, the index-intersection inside `_hedge_beta` drops them,
and no spread/position is fabricated. JS is unaffected (it aligns by `slice(-m)`, never bfills).

**Test:** `test_core.py::test_pairs_no_bfill_leak_leading_pre_eth_bars_stay_nan` (ffill leaves the
leading region NaN, bfill would back-stamp — asserted both directions, and the downstream position
is NaN there). Adversarial re-run: **HELD** (leading region all-NaN; the pre-M1 `1838.01` value is
absent from the shipped ffill path).

### M2 (Medium) — pairs cost charged only the BTC leg; the beta-scaled ETH hedge was free

**Before:** the pairs backtest charged `cost_rate · |Δ BTC-position|` and **ignored the ETH hedge
leg entirely** — the trade holds a BTC leg AND a `beta_t`-scaled ETH hedge that rebalances every bar,
so the true round-trip pays ≈`(1+|beta|)` (~2×) the charged amount. Undercharging flattered every
pairs Sharpe / DSR.

**After (shared Python + JS formula — THE ONE RULE engaged):** new single-source
`strategies._hedge_beta` (the OLS hedge ratio lives in exactly ONE place; `pairs_coint`/`pairs_ou`
both route through it) surfaced via `strategies.pairs_legs(btc, eth, model) → (state, beta_t)`. The
ETH-leg turnover is the total variation of the hedge notional `|Δ(beta_t·state_t)|` (captures
entries/exits AND continuous rolling-beta rebalancing), fed to a new optional
`backtest.run(..., extra_cost_turnover=)` param (default `None` ⇒ byte-identical single-leg cost for
every non-pairs strategy), threaded per-fold through `walk_forward`. JS mirror: `quant.js sigPairs`
now returns `beta`; `backtest`/`walkForward` accept `extraCostTurnover`; `app.js` computes and passes
the ETH-leg turnover. **SCOPE RAIL:** `gross_returns` is untouched — P&L stays single-leg directional;
netting the ETH leg's *price* move into the P&L is the separate **M9** finding (see open list).

**Before → after** (`compare.py --research`, real BTC/ETH, 2018-01-01→2026-07-12, N=8):

```
strategy       OOS DSR     OOS CAGR       OOS SR    OOS MaxDD    #T
pairs_coint    0.06→0.04   0.19%→-0.12%   0.06→0.01 -12.54→-13.84  14→14
pairs_ou       0.02→0.01  -5.92%→-7.41%  -0.15→-0.22 -50.09→-54.09  70→70
```

Both pairs rows moved **DOWN** (never up toward the 0.95 bar); #T unchanged (trades count the BTC
leg; the ETH hedge rebalances every bar by construction). Non-pairs P&L is **byte-identical** (OOS
SR/CAGR/MaxDD unchanged, e.g. tsmom OOS SR 1.01 / CAGR 12.57% / MaxDD -22.28%); only their shared-V
leaderboard DSR shifts (tsmom 0.84→0.82) because the cross-trial variance V rose 0.000531→0.000600
when the pairs SRs dropped — the documented M6 methodology coupling, moves down/sideways, no upward
flip.

**Tests:** `test_core.py` — `pairs_legs` single-source (state matches `pairs_coint`/`pairs_ou`
byte-for-byte, beta finite), exact-2× cost at beta=1, rolling-beta-drift cost with unchanged state,
`extra_cost_turnover=None`/all-zero byte-identical guard, **and the new adversarial-alignment guard**
`test_extra_cost_turnover_alignment_series_vs_positional_array` (see hardening below). Parity: 5 new
pinned fields (`pairs_beta_last`, `pairs_ethTurnover`, `pairs_btcTurnover`, `pairs_totalTurnover`,
`pairs_netEquity`) ≤1.5e-7; independently re-verified on **3 random-ish (btc,eth) fixtures**
(seeds 101/202/303) — Python↔JS worst |Δ| = **3.18e-11**.

**Adversarial hardening (found + fixed this pass):** a raw positional `ndarray`/list passed as
`extra_cost_turnover` was **silently dropped to zero cost** — `pd.Series(array)` gets a RangeIndex
that `.reindex()` cannot align onto a DatetimeIndex, so even a *correct-length* array charged
nothing (a silent under-charge). `backtest.run` now: aligns a `pd.Series` by index (unchanged path,
all real callers), aligns a 1-D array **by position** requiring exact length, and **raises
`ValueError` loudly** on a length mismatch. NaNs in an aligned Series still never reach the charge
(`fillna(0)`), so the degenerate zero-variance-beta case (`beta = cov/0 = NaN/inf`) stays finite —
verified: final equity 1.0, total_turnover 0.0, no NaN into cost. Regression:
`test_extra_cost_turnover_alignment_series_vs_positional_array`.

**Status:** M2 fixed + tested. **OPEN — M9 (needs greenlight):** the pairs backtest P&L is still
**single-leg directional** (`traded_pos · BTC_ret`); a genuinely delta-neutral pairs P&L should net
the ETH leg's price move (`−beta_t·state_t · ETH_ret`), which would change the pairs *return* series
(not just cost). Deliberately NOT done here (M2 is cost-only per the scope rail); documented in code
(`strategies.pairs_legs`, `backtest.run`) as awaiting its own before/after + sign-off.

### M4 (Medium) — no purge/embargo/lockbox; correct-by-construction machinery, default-OFF, numbers identical

**Before:** `walk_forward` had no purge or embargo; `cpcv` had only a legacy fractional
`embargo=0.01` leading trim; there was no evaluate-once ledger. For the strategies shipped **today**
(all 1-bar causal labels, positions generated once on the full series then sliced) no train label
reaches into a test block, so purge/embargo are **not needed for today's numbers** — but the harness
was not correct-by-construction for the k-step-label order-flow signals arriving when MinBTL clears.

**After:** `walk_forward(purge, embargo)` and `cpcv(purge, embargo)` add AFML ch.7 purge+embargo as
**index masks on the per-fold IN-SAMPLE return series only** (never the prices, so `run`'s
`pct_change` is never taken across a gap; **OOS is invariant**); `cpcv`'s legacy float knob was
renamed `embargo_pct` (no caller passed it positionally by that name) with int `purge`/`embargo`
edge-trims added. New `backtest.LockBox` records `(start,end)` OOS slices and `assert_scored_once`
detects a double-score (and a never-scored slice). **Default `purge=embargo=0` reproduces the pre-M4
engine bit-for-bit** — pinned to golden constants.

**Before → after (byte-identical, the point):** on fixed synthetic data, the no-arg call ==
explicit `purge=0/embargo=0` == golden constants: `walk_forward` OOS Sharpe
`-0.6577099382791535`, IS Sharpe `-0.5255985161946755`; `cpcv` median `-0.26458498017492493`,
iqr `0.6414388218458281`, n_paths `15`. OOS invariant under `purge=7/embargo=5`. Live effect on
`compare.py`: **none** — no displayed leaderboard number changed from M4 (compare.py does not pass
purge/embargo; walk_forward defaults 0).

**Tests:** `test_core.py` — `_zero_is_byte_identical_golden` (no-arg == 0/0 == golden constants),
`_purge_removes_exactly_k_train_bars`, `_embargo_inserts_exactly_e_bar_gaps`,
`_purge_changes_is_stat_for_kstep_label_signal` (the machinery is *live*, not inert, for a synthetic
k=5 label — IS stat moves, OOS put), `_lockbox_flags_double_scored_slice`. Parity: 9 new CPCV fields
(`cpcv_nPaths/median/p25/p75/iqr/min/max` + `cpcv_pe_median/nPaths` exercising the int params) ≤1e-9.
Adversarial re-run: **HELD** — huge `purge/embargo=9999` leaves OOS unchanged and every fold `n_is=0`
with no crash (`is_` degrades to `{}`); negative `purge`/`embargo` raise `ValueError` on both
`walk_forward` and `cpcv`; LockBox double-score caught; CPCV parity 1e-7.

**Readiness tie-in:** purge/embargo are the correct-by-construction guard for the k-step-label
signals that enter the OOS harness once the MinBTL readiness meter clears; they ship dormant so the
machinery is *proven* before the labels that need it arrive.

### M5 (Medium) — forward-IC significance was a crude fixed band; now Newey-West / HAC at lag k-1

**Before:** IC significance was a bare threshold `|IC| > 1.96·√(k/n)` — no SE, no t-stat, no
p-value; a strong-signal pass could not distinguish a real overlap-corrected result from luck.

**After:** `ic.ic_significance(signal, fwd, k)` rank-transforms the aligned series and fits
`fwd_rank ~ const + signal_rank` by OLS with `cov_type="HAC"`, `maxlags=max(k-1,0)` — exactly the
overlap-induced MA(k-1) order (Newey & West 1987; Lopez de Prado AFML §4-5, cited in the docstring).
Because the OLS slope of common-sample ranks **equals** the Spearman coefficient, the returned `ic`
IS the Spearman IC and its HAC t-stat tests exactly `H0: IC=0`. Returns
`{ic, n, k, t_stat, p_value, se, significant(p<0.05), method="newey-west-k-1"}`; `ic_ir`
(non-overlapping block t-stat) kept as the complementary column. **`compare.py` IC table wired**
(this pass): the crude band and its `*` gloss are gone; the table now prints `NW t(k=3)` / `NW p(k=3)`
(the overlap-corrected statistic) alongside the retained `IC-IR t(k=3)`, with the per-cell `*` driven
by HAC `p<0.05`.

**Direction — significance is strictly HARDER, so a NONE-significant verdict can only strengthen.**
On a strongly serially-dependent overlapping k=5 sample the HAC SE is **larger** than the naive
homoskedastic OLS SE (2.08× on the audit fixture: 0.0401→0.0833) and the HAC |t| **smaller**
(5.38→2.59). White-noise adversarial battery (300 seeds): HAC p is ≈uniform (mean 0.493,
frac(p<0.05)=0.050) — correctly calibrated. At k=1 there is no overlap, maxlags=0, and the HAC t
equals the OLS-HC0 t on ranks (verified `14.223085` both, rtol 1e-6) with `ic` == Spearman.

**Run-log verdict (real data, `compare.py --research`) — BEFORE vs AFTER the wiring:**

```
BEFORE: IC k=1..10 shown, '*' = |IC|>1.96·√(k/N)  → NO strategy starred (none significant)
AFTER:  same IC magnitudes + NW t(k=3)/p(k=3)     → NO strategy starred; the HAC p's make it explicit
strategy         IC k=3     NW t(k=3)   NW p(k=3)   IC-IR t(k=3)
tsmom            +0.008        0.30       0.767        -5.40
tsmom_ls         +0.020        0.84       0.401        -3.25
tsmom_dir        +0.017        0.58       0.564        -7.33
ma_trend_filter  +0.009        0.29       0.774        -2.39
pairs_coint      -0.063       -0.46       0.649        -0.60
pairs_ou         +0.006        0.14       0.889        -1.26
```

The NONE-significant OOS-lead verdict **holds and is stronger**: every overlap-corrected HAC p is
≫0.05 (0.40–0.89), so the "no strategy leads returns OOS" conclusion is now backed by a real
t-stat/p rather than a bare band. (The large negative IC-IR t is the complementary non-overlapping
block reading of the same near-zero mean IC — expected, and unchanged.)

**Tests:** `test_ic.py` cases (a)-(e) — k=1 reduces to OLS-HC0-t on ranks + within 10% of the
classical Spearman-t; HAC widens SE vs naive on an overlapping AR(1) fixture; zero-information signal
p≈1 not significant; perfect-monotone rank-IC=1, p≈0, no crash on the degenerate residual; n=2
returns NaN stats + `significant=False` without raising. `ic` == Spearman asserted to <1e-9 in every
case. No quant.js change: IC is an evaluation layer with no mirror in the 55 parity fields — THE ONE
RULE is not engaged.

**Status:** M1-pairs / M2 / M4 / M5 fixed + tested. Remaining audit findings: **M9** (delta-neutral
pairs P&L — new, needs greenlight, above), M7 (app.js `--check` in CI), M8 (options parity),
Low/Info.
