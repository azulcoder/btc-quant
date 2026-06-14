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

## P4 — Eval extensions (SQN / profit factor / MAE)

`expectancy_report` (+ `quant.js expectancyReport`) extended with **SQN** = mean(R)/std(R)·√n,
**profit factor** = Σ win-R / |Σ loss-R|, and **avg MAE** (per-trade max adverse excursion in R,
from the per-bar cumulative path already inside `trade_ledger`). Pure honesty additions — no edge
claim, no behavior change to ranking. Surfaced on the compare.py `#T`/`SQN` columns and the dashboard
"Trade quality (OOS)" line. Re-verified JS↔Python parity (below). No `Date.now`/RNG → deterministic.

## P5 — Tier-B strategy candidates (research-only sweep)

**Pre-registered before the run** — these are price-pattern hypotheses on one asset (BTC daily); the
prior (Part B) was that single-asset chart patterns deflate through walk-forward + cost. They are run
in `compare.py --research` ONLY and do **not** join `SPOT_STRATS`/the dashboard board.

*Kill rule (pre-registered):* a candidate is KILLed unless its **net-of-cost OOS Deflated Sharpe > 0.95**
(the same bar the public board clears). No N-inflation of the strategy count (protects MinBTL).

| candidate | hypothesis | OOS DSR | OOS SR | OOS MaxDD | verdict |
|---|---|---|---|---|---|
| `donchian_breakout` 55/20 | Turtle channel breakout still trends in BTC | 0.50 | 0.45 | −60.2% | **KILL** |
| `vwap_reversion` 48, k=2 | price-only VWAP-band fade (no order-flow confirm) | 0.00 | −0.69 | −95.4% | **KILL** (worst) |
| `ma_trend` + `fixed_r_exit` 2:3 | a fixed-R stop + 3:1 target rescues a trend entry | 0.69 | 0.64 | −65.8% | **KILL** |
| `random_entry` (control) | coin-flip entry + trailing stop ≈ managed-risk floor | 0.36 | 0.31 | −88.7% | **KILL** (control) |

**Result = as pre-registered: all four deflate.** Readings: (a) the fixed-R exit overlay (0.69) does
lift the per-bet quality of the trend entry above the random control (0.36) but **not past the 0.95
bar** — asymmetric R:R is real risk management, not a standalone edge, exactly the book's own claim;
(b) the price-only VWAP fade is actively harmful without the order-flow confirmation the book pairs it
with (which needs a tick store we don't have) — a clean, honest kill, not a tuning failure. The value
here is the **documented rejection**.

*Rejected without a run (data limits, not opinions):*
- **Gamma-regime proxy** — no historical option-chain/greek timeseries ⇒ cannot enter the OOS harness.
- **ORB / Monday–Wednesday & other intraday-seasonality edges** — need intraday bars we don't store.

*Descriptive (not a backtested edge):* day-of-week mean daily return is printed in the sweep as a regime
check; spread is ~noise at this N (Mon/Wed mildly +, Thu −) — surfaced, not traded.

## JS↔Python parity

`tradeLedger`/`expectancyReport` mirrored in `quant.js`; parity probe on a fixed fixture (260 bars,
two-sided position): **PASS, max\|Δ\| = 6.7e-16** (machine epsilon — pure arithmetic, no inverse-normal,
so far tighter than the ~1e-8 DSR path). `percent_risk_size` is **Python-harness-only** — it has no
dashboard consumer (no sizing UI), so it is deliberately **not mirrored in JS** (mirroring it would be
untested dead code; same discipline as not adding a B1-style duplicate). The dashboard #1 readout
("Trade quality (OOS)") reads from `quant.js expectancyReport` on `walkForward.oosPositions`.
