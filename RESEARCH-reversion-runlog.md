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

**Result.** *(filled by the Commit-4 sweep — `compare.py --reversion` / run-log update.)*

| timeframe | variant | OOS DSR | OOS Sharpe | OOS MaxDD | IC k=1 | verdict |
|---|---|---|---|---|---|---|
| 1d | mean_reversion ungated | _tbd_ | | | | |
| 1d | mean_reversion gated | _tbd_ | | | | |
| 1h | mean_reversion ungated | _tbd_ | | | | |
| 1h | mean_reversion gated | _tbd_ | | | | |

**Verdict.** *(tbd)* — expected: ungated deflates; gated is the genuine test of whether the
regime gate adds OOS value. Keep only if it clears the pre-registered kill criterion.
