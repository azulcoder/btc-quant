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
