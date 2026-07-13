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

## 2026-07-12 — M9 / M8 / M7: delta-neutral pairs P&L, options parity closed, annualization guard in CI

Three findings closed in one pass (each shipped with before/after + regression). Suite after:
pytest **178** passed (was 173 at the M1/M2/M4/M5 close; +4 M9 delta-neutral P&L, +1 M8 options
parity harness), `check_parity` **63** fields PASS (was 55; +2 M9 pairs P&L, +6 M8 options; worst
|Δ| = **1.63e-07** unchanged — still the `rsi` field, pre-existing, untouched by this pass),
`check_terminal` **46/46**, `node --check` all + `app.js --check` clean, `make verify-browser` L1
exit-0 with **0 console / 0 page errors** (the pairs cost+P&L change lives on the analytics path;
the terminal L1 surface is unaffected).

### M9 (Medium) — pairs P&L was single-leg directional; the delta-neutral trade earns the SPREAD

**Before:** M2 made the pairs *cost* two-leg (charging the beta-scaled ETH hedge) but left the
booked P&L single-leg directional — `gross_returns = traded_posₜ · BTC_retₜ`. A delta-neutral
long-spread holds a BTC leg AND a `beta_t`-scaled short-ETH hedge, so its true P&L is the **spread
return**, not a bare BTC move: when ETH outruns BTC the hedge loses even as BTC rises. Cost was
two-leg while P&L was one-leg — an incoherent trade that still credited the pairs strategies with
BTC's directional drift.

**After (shared Python + JS formula — THE ONE RULE engaged):** new optional
`backtest.run(..., hedge_return=)` param (mirror `quant.js backtest` `opts.hedgeReturn`) **subtracts**
a per-bar hedge-leg return from the asset return before booking P&L:
`gross = traded_posₜ · (BTC_retₜ − hedge_returnₜ)`. Callers pass
`hedge_returnₜ = beta_{t−1} · ETH_retₜ` — the SAME single-source rolling hedge ratio
(`strategies.pairs_legs`) the cost is charged on, **1-bar-lagged** (`beta.shift(1)`, the same
no-look-ahead shift as the state), so the BTC-leg state earns `BTC_ret − beta·ETH_ret ≈ Δlog-spread`,
a return on the BTC-leg notional (the `[-1,1]` reference the state weight always used). Threaded
per-fold through `walk_forward` (both IS and OOS blocks). `hedge_return` is aligned exactly like
M2's `extra_cost_turnover` (Series → reindex+`fillna(0)`; positional array → length-checked), so a
warm-up / non-overlapping / degenerate-beta bar nets a **zero** hedge, never a NaN into P&L.
**Default `None` ⇒ `gross = traded_pos · BTC_ret` EXACTLY as before ⇒ byte-identical for every
non-pairs strategy** (the subtraction path is never entered). `strategies.pairs_legs` is unchanged
(still the ONE source of `(state, beta_t)`); `compare.py._pairs_hedge_return` and `app.js` do the
wiring. **M2 (two-leg cost) + M9 (two-leg P&L) together make the pairs trade coherently
delta-neutral for the first time.**

**Before → after** (`compare.py --research`, real BTC/ETH, 2018-01-01→2026-07-12, N=8; BEFORE =
single-leg P&L pinned by re-running the pre-M9 tree on the identical current data):

```
strategy       OOS DSR     OOS CAGR       OOS SR      IS SR        OOS MaxDD      #T
pairs_coint    0.04→0.00   -0.12%→-1.43%   0.01→-0.59  0.01→-0.35  -13.84→-10.94  14→14
pairs_ou       0.01→0.00   -7.41%→-4.38%  -0.22→-0.18 -0.03→-0.06  -54.09→-61.65  70→70
```

Both pairs DSRs collapse to **0.00** — still **KILL**, nowhere near the 0.95 bar, still off the
public board. `#T` unchanged (M9 nets the ETH leg's *price* move into P&L; it does not change
positions or trades). The kill verdicts are unchanged: `pairs_ou` (Part B) and every Tier-B/Tharp
KILL hold; no verdict flipped upward.

**Non-pairs P&L is byte-identical**; only the shared-V leaderboard DSR shifts — the documented M6
cross-sectional coupling. When the pairs OOS SRs dropped, the empirical cross-trial variance rose
`V 0.000602→0.000991`, so every OTHER strategy's leaderboard DSR moved **down** (tsmom 0.82→0.64,
tsmom_dir 0.75→0.56, buy_and_hold 0.61→0.41, tsmom_ls 0.58→0.37, ma_trend 0.53→0.34) with **no
upward flip and no rank change**. Their OOS SR/MaxDD are unchanged (tsmom 1.01 / -22.28%, tsmom_dir
0.93 / -58.68%, b&h 0.79 / -76.85%). (A pre-existing ~0.01% CAGR flutter on a couple of rows —
e.g. tsmom_dir 35.68↔35.69 — reproduces across two runs of the *identical* M9 tree, so it is
float/data-refresh nondeterminism in the CAGR year-count, not an M9 effect.) The IC block is
**unaffected** (M9 does not touch `ic.py`).

**Tests:** `test_core.py` — `test_m9_delta_neutral_identity_zero_spread_when_legs_move_together`
(ETH≡BTC, beta≡1 ⇒ spread return 0 every bar ⇒ zero gross, flat equity),
`test_m9_hedge_return_none_leaves_every_nonpairs_result_byte_identical` (default AND explicit
`None` leave `run` + `walk_forward` byte-for-byte unchanged for buy_and_hold/ma_trend/tsmom),
`test_m9_eth_outrunning_btc_makes_long_spread_pnl_negative` (BTC +2%/bar, ETH +5%/bar, beta≡1: a
long-spread that is positive single-leg turns negative once the faster ETH hedge nets in — every
active bar 0.02−0.05<0, terminal equity <1),
`test_m9_beta_shift_is_t_minus_1_no_lookahead` (a lone beta spike at bar t touches P&L only at
t+1, every other bar identical). Parity: 2 new pinned fields (`pairs_dnGrossSum`,
`pairs_dnNetEquity`) ≤1e-7.

**Adversarial battery (all HELD):** (a) full non-pairs leaderboard rows byte-identical to the
pre-M9 tree on identical data (verified by stash-and-rerun); (b) a degenerate/NaN hedge (NaN
warm-up + a lone mid-series NaN, as a zero-variance beta would produce) nets **zero** on those
bars — the gross NaN mask is identical to the no-hedge run, equity stays finite, no NaN reaches
P&L; (c) the constructed ETH-outruns-BTC long spread loses (above); (d) no look-ahead (above);
(e) Python↔JS pairs **net-P&L** parity on **3 random (btc,eth) fixtures** (seeds 101/202/303) —
worst |Δ| = **8.57e-14** on net equity + gross-return sum, no NaN in either engine's gross.

### M8 (Medium) — CLOSED: options parity now covers max_pain + gamma_concentration past the greeks

**Before (residual after the Black-76 greeks pin):** `max_pain` and `gamma_concentration` had a live
`quant.js` mirror (`Q.maxPain`, `Q.gammaConcentration`) and a Python source (`features.max_pain`,
`features.gamma_concentration`) but **crossed the mirror UNPINNED** — only the Black-76 greeks
(`b76_delta/gamma/vega`) were checked, so the two options analytics that reach the dashboard could
have drifted silently.

**After:** a FIXED synthetic single-expiry chain (6 strikes × {call,put}, each with
`open_interest` + `iv`, one 65 000 underlying; `optNow` exactly 30 calendar days before the
08:00-UTC `optExpiry` so `T = 30/365` matches the existing b76 probe) drives **6 new pinned parity
fields**, additively (the M9 pairs P&L probes were untouched): `mp_maxPain` (argmin over strikes,
tol 0), `mp_pcOiRatio`, `mp_forward`, `gc_sum` (total |gamma|·OI density), `gc_dot` (strike-weighted
gamma profile), `gc_peakStrike` (densest-gamma strike). All 6 agree **bit-exact (|Δ| = 0)**. Two
anchors are pre-registered in `PINS` so the engines cannot drift *together* and still pass:
`mp_maxPain = 64000`, `gc_sum = 0.10701664008807263`.

**Tests:** `test_parity_mirror.py::test_parity_options_fields_present_and_agree` (harness runs, the
6 M8 fields present and agree) + the probe-presence + anchor-constant assertions in
`test_parity_harness_covers_unsaturated_and_walkforward_probes`.

**Adversarial battery (all HELD):** (a) **tie handling** — a symmetric chain whose pain is flat
across inner strikes: Python `np.argmin` and JS strict-`<` both pick the SAME (lowest-strike)
minimum, `62000` both sides; (b) **empty chain** — both engines return `max_pain = NaN`,
`gc_sum = 0`, 0 strikes, no crash; (c) **one-strike chain** — both `65000`, `gc_sum` exact, 1
strike; (d) **a second random chain** — `max_pain`/`pc_oi_ratio`/`gc_sum` all agree |Δ| = 0.

### M7 (Medium) — CLOSED: the annualization guard `node dashboard/app.js --check` now runs in CI

**Before:** `app.js`'s self-check (ppy() mirrors Python `_periods_per_year` = 365/8760, and no
literal 365 survives at an annualization site — the exact sqrt(24) 1h mis-annualization bug that
forced the 1h selector's removal) existed but was **not wired into CI**, so a regression could ship.

**After:** a new step **"annualization guard (ppy() self-check)"** in `.github/workflows/ci.yml`,
placed right after the `node --check` syntax block and before the JS↔Python parity step, running
`node dashboard/app.js --check`. GitHub Actions fails the build on the guard's non-zero exit.

**Adversarial (guard bites — HELD):** planting a literal `365` at a real annualization site
(`Q.realizedVol(rets, 30, 365)` in place of `…, ppy())`) makes the guard print
`SELF-CHECK FAIL (1): literal 365 at annualization site, app.js:794` and **exit 1** (CI would
fail); reverting restores `SELF-CHECK PASS` / exit 0. `ci.yml` parses (pyyaml) and the guard runs
clean locally (exit 0).

**Status (audit roll-up):** M9 / M8 / M7 fixed + tested. **All lettered audit findings
(H1, M1-carry, M1-pairs, M2, M3, M4, M5, M6, M7, M8, M9) are now CLOSED.** Remaining: Low/Info
items only. The pairs trade is, for the first time, coherently delta-neutral in BOTH cost (M2) and
P&L (M9); options parity is complete (greeks + max_pain + gamma_concentration); and the
annualization guard is enforced in CI.

---

## 2026-07-13 — M6-AMENDMENT: leaderboard trial-variance A → B2 (own-Sharpe, decoupled)

**Decision (azul, 2026-07-13; `RESEARCH-dsr-convention.md`):** the `compare.py` leaderboard
`OOS DSR` trial variance `V` switches from **convention A** — the empirical ddof=1 variance of
the ranked strategies' OOS per-period Sharpes, one **shared cross-strategy scalar** (M6 clause
**C3**) — to **convention B2**: each strategy's **own** finite-sample Sharpe-estimator variance
`V_i = (1 − skew_i·SR_i + (kurt_i−1)/4·SR_i²)/(n_i − 1)` (Lo 2002 / Mertens 2002), i.e. *exactly
the quantity already inside the PSR denominator*. New single source of truth
`risk.sharpe_estimator_variance`; `compare.py` wires it per strategy, `dsr_ab.py` column **B2**
delegates to it. **`N` (selection-count deflation) is unchanged at the number of ranked
strategies.** This is a **partial supersede of C2/C3 for the leaderboard surface only**;
`run`/`run_funding` and the PSR/DSR formulae are untouched.

**WHY.** Under A a single peer's realized Sharpe reshapes the shared `V`, so **every other
strategy's DSR moves** — a leaderboard DSR was not a property of that strategy alone. This
coupling was surfaced concretely by **M9** (2026-07-12): when the delta-neutral pairs P&L dropped
the pairs OOS SRs, the empirical cross-trial variance *rose* and shifted every non-pairs DSR
(AUDIT_LOG 2026-07-12, "only the shared-V leaderboard DSR shifts"). B2 makes each strategy's DSR
depend **only on its own returns** (decoupled — verified peer-invariant to Δ=0 below). Selection
overfit is still guarded — the expected-max-of-`N` benchmark `sr0(V,N)` retains the `N` term, and
**PBO (0.60) + MinBTL (2.85 yrs) are unchanged** and carry the cross-strategy selection honesty.

**BEFORE (A, shared cross-strategy V) → AFTER (B2, per-strategy own-Sharpe V):**

```
RESEARCH leaderboard (compare.py --research, N=8, V_A=0.000994 shared → per-strategy V_B2≈0.0002–0.0004)
strategy            OOS SR   DSR_A[before]   DSR_B2[after]
tsmom                 1.01      0.644           0.907
tsmom_voltarget       1.01      0.644           0.907
tsmom_dir             0.93      0.555           0.852
buy_and_hold          0.80      0.414           0.737
tsmom_ls              0.76      0.372           0.726
ma_trend_filter       0.71      0.338           0.662
pairs_ou             -0.18      0.002           0.026
pairs_coint          -0.59      0.000           0.001

PUBLIC leaderboard (compare.py, N=5, V_A=0.001127 shared → per-strategy V_B2)
strategy            OOS SR   DSR_A[before]   DSR_B2[after]
tsmom                 1.01      0.754           0.945
buy_and_hold          0.80      0.536           0.819
tsmom_ls              0.76      0.492           0.807
ma_trend_filter       0.71      0.446           0.750
pairs_coint          -0.59      0.000           0.000

Rank order IDENTICAL A vs B2; only the confidence LEVEL rescales. NO strategy crosses 0.95
(public tsmom 0.9451 ≤ 0.95 ⇒ still no `*`). PBO 0.60 · MinBTL 2.85 yrs — unchanged.
```

**A-numbers in prior logs stay as the as-of-then record.** The M6 (2026-07-11) public "AFTER"
DSRs (tsmom 0.946, b&h 0.83, …) and the M9 (2026-07-12) coupling note are the state *at those
dates*; the DSR_A column above is the current post-M9 production A, immediately pre-switch. The
`dsr_ab.py` **column A** continues to reproduce it via `compare._empirical_var_sr` (kept, no
longer wired into the leaderboard).

**Two DSR surfaces were ALWAYS separate and are UNCHANGED by this switch:**
- **`walk_forward` folds-DSR** (`OOS DSR (folds)`, N = n_splits, V = empirical var of per-fold
  SRs — M6 clause C3) — the research **kill-gate**. Re-verified `compare.py --research`
  2026-07-13: every published verdict still **KILL** — Tier-B donchian/vwap_reversion/
  ma_trend+fixedR/random_entry all ≤0.95; B1 tsmom_voltarget KILL (corr 1.00 dup clause);
  B2 pairs_ou KILL (Δ<+0.05); Tharp sizing sweep unchanged. `backtest.py` was not touched.
- **Dashboard fold-V surface** (`quant.js walkForward`, the 63 parity-pinned fields) — a
  **different, already-decoupled** per-fold V. **No JS changed**; `check_parity` **63/63**,
  worst |Δ| = **1.63e-07** (byte-identical to pre-switch). No parity re-pin.

**Adversarial battery (all HELD; in-process, real data 2018-01-01→2026-07-13, 3116 bars):**
- **(a) production == `dsr_ab` B2 to 1e-9** — computed both leaderboards in-process (not the 2dp
  print): worst `|compare.OOS_DSR − dsr_ab.DSR_B2|` = **0.0e+00** across all 8 strategies (exact,
  same `risk.sharpe_estimator_variance` V, same N, same `risk.deflated_sharpe_ratio`). `dsr_ab`
  hand-formula self-check worst = 0.0e+00.
- **(b) decoupling now in production** — perturbed `pairs_coint`'s OOS Sharpe (returns-level
  additive drift; n/σ/skew/kurt held fixed) by ×1.5, ×0.5, ×3.0 and recomputed the `compare.py`
  leaderboard: **every peer's DSR moved by exactly 0.0e+00** (bit-invariant). Contrast under the
  retired convention A, same perturbation: worst peer moved **0.130** (coupled). This invariance
  is the whole point of the switch.
- **(c) `n<2` / single-strategy fallback** — `sharpe_estimator_variance(sr, n=1, …) = nan` ⇒ the
  leaderboard loop falls back to `1/n_periods` with `var_fallback=True` and prints the
  `null-variance fallback` caveat; DSR = nan (deflated `n<2`), **no crash**. `n=0` edge: no
  `ZeroDivisionError`.
- **(d)** parity **63/63**, worst |Δ| 1.63e-07 (unchanged); **(e)** folds-DSR kill-gate all KILL
  (above).

**Tests (+2 intentional, no regression):** `test_core.py` +
`test_sharpe_estimator_variance_hand_pin_and_psr_denominator_identity` (exact pin
0.0020378256513026052 + PSR-denominator round-trip + `n<2` nan) and
`test_compare_leaderboard_uses_per_strategy_b2_variance_and_is_decoupled` (drives compare's exact
loop; perturbing one strategy leaves peers bit-identical). `test_dsr_ab.py` cross-check rewritten
to assert **production == B2** (was == A). Also fixed a stale defect: `dsr_ab.py` still printed
"production stays A" in two places — re-pointed to B2.

**Status:** pytest **185** (was 183; +2 intentional), `check_parity` **63/63** (worst |Δ|
1.63e-07), `check_terminal` **46/46**, `make verify-browser` L1 exit-0. Python-only, as scoped —
no JS mirror change, no parity re-pin. **M6 clauses C2/C3 amended for the leaderboard surface;
all other M6 semantics stand.**
