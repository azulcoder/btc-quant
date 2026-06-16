# Regime-gated mean reversion — run-log (pre-registered)

Derived from a mean-reversion blueprint for BTC perps. Most of that blueprint needs data
btc-quant does not ingest (perp OI history, true tick CVD, VPIN, liquidation clusters, CME
CoT, multi-venue basis) — those are **not built** and **not faked** (honesty rail). What IS
testable on our keyless OHLCV history is the blueprint's central, falsifiable claim:

> **H (pre-registered): a ranging-regime gate (Hurst < 0.45 AND ADX < 22) turns an
> otherwise negative-expectancy price-reversion fade into a positive-expectancy one.**

**Prior.** The ungated band-fade is already rejected: `vwap_reversion` KILLed in the Tier-B
sweep (OOS DSR 0.00, −95% DD, RESEARCH-tharp-runlog.md). The IC pass found NO board strategy
has significant forward IC. So the expectation is that the *ungated* `mean_reversion` also
deflates; the open question is whether *gating* rescues it.

**Method.**
- Signal: `strategies.mean_reversion` — z = (close − SMA(lookback)) / rolling-σ; fade
  `−sign(z)` when `|z| > entry_z`, exit `|z| < exit_z` (stateful hysteresis); causal.
- Gate: `strategies.regime_gate` — `features.hurst(window) < 0.45 AND features.adx < 22`;
  trailing/causal. `gated=False` vs `gated=True` is the only difference → a clean A/B.
- Scoring: walk-forward OOS Deflated Sharpe (folds-as-trials) + PBO + the forward-IC profile,
  on **1d AND 1h** (multi-timeframe). Net of cost (10+2 bps on turnover).
- **Kill criterion (pre-registered):** `mean_reversion` joins the board ONLY if the **gated**
  variant clears **OOS DSR > 0.95** on at least one timeframe AND beats its **ungated** twin.
  Otherwise: KILL (documented), stays off the board.

**Data-limited families (honest deferral, NOT in this OOS pass).** VRP-extreme reversion
(needs deep DVOL history) and funding-extreme reversion (keyless funding ≈ a few hundred 8h
intervals) cannot be OOS-validated over multi-year history; they remain descriptive-only,
like `carry`. Building them as board signals would be N-inflation on unvalidatable data.

**Result** (`scripts/reversion_sweep.py`, 2026-06-17 · BTC-USD · 5 folds · net 10+2 bps):

| timeframe | variant | OOS DSR | OOS Sharpe | OOS MaxDD | IC k=1 | % in-trade | verdict |
|---|---|---|---|---|---|---|---|
| 1d (3089 bars, 2018→) | ungated | 0.019 | −0.336 | −89.0% | +0.002 | 45% | **KILL** |
| 1d | gated | 0.000 | −0.643 | **−23.0%** | −0.016 | 2% | **KILL** |
| 1h (26 677 bars, 2023-06→) | ungated | 0.000 | −2.644 | −92.7% | +0.021* | 43% | **KILL** |
| 1h | gated | 0.000 | −3.232 | **−54.5%** | −0.001 | 4% | **KILL** |

**Verdict: HYPOTHESIS FALSIFIED — `mean_reversion` is NOT promoted (board unchanged).** The
gate does exactly what it should as *risk management* — it slashes drawdown (−89%→−23% on 1d,
−93%→−54% on 1h) and time-in-trade (45%→2%, 43%→4%) by refusing to fade trends — but it does
**not** create alpha: every variant deflates (OOS DSR ≪ 0.95), the gated Sharpe is in fact
*more* negative (it just risks far less), and forward IC is ~0 (the lone 1h ungated +0.021*
does not survive cost/structure into a positive Sharpe). Gated does not beat ungated on OOS DSR
on either timeframe, so the pre-registered kill criterion is not met.

This is consistent and triangulated: `vwap_reversion` was already KILLed ungated; the IC pass
found no board strategy with significant forward IC; and the blueprint's own caveat holds — a
single gated oscillator, *without* the multi-family confluence (OI / CVD / funding / VRP
positioning signals that need data btc-quant does not ingest), is "pennies in front of a
steamroller." The gate is retained as a **reusable risk filter / research primitive**
(`features.hurst`/`variance_ratio`/`adx`, `strategies.regime_gate`), not as a standalone edge.
The documented rejection IS the deliverable.
