# Lead-time Information Coefficient — run-log (pre-registered)

**Question (pre-registered):** do btc-quant's board strategies actually *lead* returns
out-of-sample — i.e. is the forward Information Coefficient of the signal statistically
different from zero — or is their backtest edge coming from something other than per-bar
predictive skill?

**Method** (`btcquant/ic.py`, surfaced in `scripts/compare.py`):

- IC = **Spearman rank** correlation of `position_t` (the OOS walk-forward signal) vs the
  **forward** return `t → t+k`, for `k ∈ {1, 3, 5, 10}`. Rank IC for robustness to crypto tails.
- Scored on `backtest.walk_forward`'s `oos_positions` only (held-out, never in-sample).
- Significance: **overlap-corrected** 95% band `|IC| > 1.96·√(k/N)` (the textbook `1.96/√N`
  at `k=1`, widened for `k>1` because k-horizon forward windows overlap → ~`N/k` independent obs).
- **IC-IR** from **non-overlapping** 21-bar blocks (t-stat not inflated by autocorrelation).
- *Implementation is unit-tested* (`tests/test_ic.py`): a perfect predictor scores IC≈1, pure
  noise scores ~0 and is flagged insignificant, a k-ahead signal peaks at the true k, Spearman is
  rank-invariant where Pearson is not, and a regime-only predictor is significant in-regime / null out.

**Result** (2026-06-16 · BTC-USD 1d · 2018-01-01 → 2026-06-16 · 3089 bars · 5 folds):

| strategy | IC k=1 | IC k=3 | IC k=5 | IC k=10 | IC-IR t(k=3) | OOS DSR |
|---|---|---|---|---|---|---|
| tsmom | +0.013 | +0.006 | +0.002 | +0.038 | **−4.01** | 0.93 |
| tsmom_ls | +0.022 | +0.019 | +0.019 | +0.058 | **−2.42** | 0.79 |
| ma_trend_filter | +0.011 | +0.005 | +0.002 | −0.005 | −0.83 | 0.74 |
| pairs_coint | +0.016 | −0.012 | −0.010 | −0.010 | +0.31 | 0.15 |
| buy_and_hold | n/a (constant signal ⇒ IC undefined) | | | | | 0.81 |

**Verdict: NONE of the board strategies show a statistically significant forward IC** at any
horizon (every |IC| sits inside the 95% band; no `*`). Notably the two trend strategies that
*do* clear a respectable OOS Sharpe/DSR (tsmom DSR 0.93) carry a **negative IC-IR t-stat** at
k=3 — their per-block IC is unstable and sign-flipping, not a stable lead.

**Interpretation (the honest answer to "apakah sudah sesuai"):**

1. The IC layer itself is *correct and behaving as designed* (the unit tests pin the math). So
   the near-zero readings are a true property of the strategies, not a measurement artifact.
2. The strategies' (modest, real) OOS edge is **not** bar-to-bar leading skill — it is
   low-frequency trend / vol-scaling capture. A trend follower can have a positive Sharpe with
   ~zero forward IC because it harvests autocorrelation in *realized* trends, not by forecasting
   the next k bars. This matches the weak single-asset daily-predictability literature.
3. Practical consequence: **do not market any of these as "leading" indicators**, and the IC gate
   should remain a precondition before trusting any future IC-weighted combination (cf. the
   companion Pine project, where only an **ADX≥25 regime-conditional** composite showed a small but
   significant IC_3 ≈ +0.055 — a regime split worth testing here next via `ic.regime_conditional_ic`).

**Next:** regime-conditional IC (e.g. `mask = ADX ≥ 25`) per `ic.regime_conditional_ic`, to test
whether a *momentum-regime* subset carries the significant IC the unconditional series does not.
