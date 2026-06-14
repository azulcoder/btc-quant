# Tharp eval/risk layer — run-log (pre-registered)

Research only. Not financial advice. Implements the **risk/evaluation** layer from Van Tharp,
*Trade Your Way to Financial Freedom* — the part the trading-books synthesis found genuinely
implementable in btc-quant. **No new "edge"/entry signal is claimed.** Everything below is judged
on **out-of-sample** results (in-sample expectancy is curve-fit); PBO/MinBTL remain the gate.

## Disclosure: how "R" is defined here (honest deviation from the book)

Tharp's R-multiple needs an **initial risk R = |entry − initial stop|**. btc-quant's strategies are
**continuous target-weight** signals with **no hard stop**. So R cannot be stop-based. We define a
**vol-notional R**: at each trade's entry, `R = k · σ` with `k = 2` and `σ` = trailing close-to-close
`realized_vol` (the same estimator in `features.realized_vol` and `quant.js realizedVol` → clean
parity). The R-multiple of a trade is `trade_return / (|entry_weight| · R)`. This is faithful to
Tharp's *intent* (reward measured in units of volatility-scaled initial risk) but is explicitly **a
notional 2σ risk, NOT a stop-based R** — stated wherever the metric is shown.

**Trade segmentation:** a "trade" runs from flat → nonzero position until it returns to flat or flips
sign. **Always-in strategies (buy & hold) collapse to one degenerate trade** → reported but flagged
low-N; the ledger is meaningful for long/flat + flipping strategies. All computed on the **walk-forward
OOS** path only.

## P1 — Expectancy / R-multiple report

*What it adds:* per-strategy OOS `{n_trades, expectancy_R (mean R-multiple), win_rate, avg_win_R,
avg_loss_R, payoff_ratio, max_loss_streak}` alongside the existing deflated-Sharpe/PBO leaderboard.
*Claim:* an **evaluation layer**, not a signal. *Honest expectation:* expectancy ranking should broadly
track the OOS deflated-Sharpe ranking; where it diverges (e.g. high win-rate but negative expectancy
from fat left tails) is the useful read. Low-N folds flagged unreliable — btc-quant's PBO/MinBTL gates
are stricter than Tharp's vague "large sample" and remain authoritative.

**Results** (`compare.py --research`, 2018→, N=8). OOS ExpR / Win% / #T now print on the leaderboard:
- `tsmom` **ExpR 0.43R over 140 trades** (robust N) — the edge is small-but-real per-bet.
- `tsmom_dir` 0.55R/140 (higher per-bet, but worse OOS DSR + maxDD −59%).
- `pairs_coint` **57% win-rate but −0.03 expectancy** — the exact Tharp lesson (win-rate ≠ expectancy:
  many small wins, rare large losses). The expectancy column surfaces this where Sharpe alone hides it.
- `ma_trend_filter` 5.74R but **only 8 trades** (low-N — informative but unreliable).
- `buy_and_hold` = 1 degenerate trade → ExpR/Win% **suppressed (`—`)** below 5 trades so the readout
  never misleads; `#T` always shown.
Verdict: a useful, honest evaluation column that broadly tracks the OOS-DSR ranking and exposes the
win-rate-vs-expectancy divergence. PBO/MinBTL remain the gate.

## P2 — Percent-risk (ATR) position sizing

*What it adds:* `percent_risk_size(positions, df, risk_pct, atr_window, k_stop)` — weight scaled so a
`k·ATR` adverse move ≈ `risk_pct` of equity (ATR/range-based vol budget).
*Honest framing:* **percent-volatility sizing is already `vol_target`** (weight ∝ targetVol/σ) — NOT
re-implemented. percent-risk differs only in the **vol estimator (ATR/range vs close-to-close σ)**, so
it is likely a **near-duplicate of `vol_target`** (cf. the Part-B B1 finding). *Hypothesis:* sizing
reshapes the **equity path / max-drawdown**, not the per-bet OOS deflated Sharpe.
*Kill/verdict rule:* if percent-risk's OOS positions correlate > 0.95 with `vol_target` (same base
signal), label it a duplicate vol estimator — don't promote a second board entry; keep it as a
selectable sizing option only. Report **max-DD prominently** (Tharp's own tables show 60–100% DD at
high risk%).

*Sweep:* risk% ∈ {0.25, 0.5, 1, 2.5}%, atr_window ∈ {10, 20} on `ma_trend` + `tsmom` base signals,
through walk-forward + deflated Sharpe vs buy-and-hold + PBO.

**Results** (sizing sweep on `ma_trend`, walk-forward OOS):

| sizing | OOS DSR | OOS SR | OOS MaxDD | corr vs vol_target |
|---|---|---|---|---|
| ma_trend (raw) | 0.80 | 0.77 | −64.21% | 0.84 |
| + vol_target 15% | 0.73 | 0.68 | **−19.34%** | 1.00 |
| + percent_risk 0.5% ATR20 | 0.89 | 0.90 | **−3.78%** | 0.95 |
| + percent_risk 2.5% ATR20 | 0.89 | 0.90 | −17.91% | 0.95 |

**Verdict — confirmed as predicted.** Sizing **reshapes max-DD dramatically** (−64% → −4…−19%) while
the per-bet OOS Sharpe/DSR moves little — Tharp's core point (sizing/risk, not entry, governs the
equity path). And **percent-risk corr 0.95 with vol_target** ⇒ essentially a duplicate vol estimator
(ATR/range vs close-to-close σ), the same B1 pattern: **kept as a selectable sizing option, NOT added
as a new board strategy** (no new N burned). Max-DD is reported prominently, never just terminal wealth.

## P3 — Live CVD / order-flow panel (descriptive only)

Not in this run-log's harness scope — it is a **live descriptive** panel (cumulative delta + rolling
imbalance + large-print), never backtested, never a signal (no historical tick store). Documented in
DEVELOPMENT.md; honesty caveat shown on the panel.

## JS↔Python parity

`tradeLedger`/`expectancyReport` mirrored in `quant.js`; parity probe on a fixed fixture (260 bars,
two-sided position): **PASS, max\|Δ\| = 6.7e-16** (machine epsilon — pure arithmetic, no inverse-normal,
so far tighter than the ~1e-8 DSR path). `percent_risk_size` is **Python-harness-only** — it has no
dashboard consumer (no sizing UI), so it is deliberately **not mirrored in JS** (mirroring it would be
untested dead code; same discipline as not adding a B1-style duplicate). The dashboard #1 readout
("Trade quality (OOS)") reads from `quant.js expectancyReport` on `walkForward.oosPositions`.
