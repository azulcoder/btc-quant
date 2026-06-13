# btc-quant — research design brief

*Research / backtest only. Not investment advice. Every number below is a backtest or paper estimate, not a promise; treat any live deployment as unvalidated.*

---

## 1. Executive honest take

**What actually survives out-of-sample in crypto is thin, and most of what survives is risk management, not alpha.** After stripping the in-sample, microcap-inflated, and cost-naive results out of the surveyed literature, the durable conclusions are:

- **Buy-and-hold is the benchmark to beat, and it is a hard benchmark.** BTC's secular uptrend means most "outperforming" backtests are conditioning on that uptrend. Any strategy must be reported *against* buy-and-hold, net of cost, on a deflated-Sharpe basis — not on a standalone equity curve.
- **The most robust effect is volatility/drawdown management, not return prediction.** A trend filter (price vs 200-day MA) and volatility targeting reliably cut max drawdown and vol-of-vol versus buy-and-hold (Harvey et al. 2018; Grayscale 2024). They do *not* reliably raise Sharpe in crypto, because BTC's return-vol relationship is unstable and often *inverted* relative to equities. So the realistic edge is "lose less in crashes," paid for with bull-market lag.
- **Short-horizon time-series momentum (days to ~4 weeks) is real but cost-fragile.** Shen-Urquhart-Wang (2022) find Bitcoin intraday TSMOM works, but break-even transaction costs are ~3-10 bps — below realistic retail round-trip fees. It needs leverage, maker rebates, or low turnover to survive. The classic 12-month equity/futures momentum horizon is *insignificant* in crypto; beyond ~1 month, momentum flips to reversal.
- **Cross-sectional momentum / reversal headline numbers (2.5-4%/week) are largely an illusion for a retail researcher.** They live in illiquid microcaps you cannot trade or short, are statistically insignificant once non-normality is handled (Grobys & Sapkota 2019; Grobys & Shahzad), and crash on single-coin jumps. The tradeable residual in large caps is small.
- **Carry (long spot / short perp) is the best-supported *premium*, but it is a risk premium for blow-up risk, and it is decaying.** It was high-Sharpe in 2020-2023, structurally compressed by the 2024 spot-ETF (Schmeling-Schrimpf-Todorov), and went negative by 2025. It is fundamentally a market-maker/fee-tier edge (He et al. 2024: BTC Sharpe ~1.8 retail-cost vs ~3.5 zero-fee).
- **Variance risk premium (short vol) is real and significant but tail-lethal.** You are short a fat left tail that spikes around large moves in *either* direction. Never naked, always small.
- **On-chain and sentiment signals (NVT, MVRV, SOPR, exchange flows, Fear & Greed) are mostly descriptive, not predictive,** and are dominated by a *look-ahead/revision* trap larger than trading cost (Glassnode's own PIT study). Fear & Greed does not Granger-cause returns; returns cause it.

**Realistic edge for a retail researcher:** a disciplined, low-turnover regime/risk-management overlay on a buy-and-hold core, with carry as an optional delta-neutral sleeve when funding is richly positive. Expect modest Sharpe improvement at best, with the honest win being drawdown reduction. Treat any backtested Sharpe > ~2 in crypto as a red flag for overfitting; > ~3 as almost certainly an artifact.

---

## 2. Strategy library

Each entry gives the edge, the concrete signal for `strategies.py`, typical params, data, an evidence tag, citations, and an honest caveat.

### 2.1 Time-series (absolute) momentum on BTC — `tsmom`

- **Edge:** An asset's own recent return predicts its near-term return. Foundational in futures (Moskowitz-Ooi-Pedersen 2012); in crypto it works mainly at *short* horizons (days to ~4 weeks).
- **Signal / formula:**
  `position_t = sign( cum_return(t-L, t) )` → +1 if trailing return > 0, else 0 (long/flat) or -1 (long/short).
  Vol-scaled variant: `w_t = (target_vol / sigma_t) * sign(cum_return(t-L, t))`, with `sigma_t` an ex-ante realized vol over a 20-60d window.
- **Typical params:** `L = 1 day … 4 weeks` (crypto sweet spot); vol target 10-15% annualized; daily→weekly rebalance. **Do NOT use L = 12 months** (insignificant in crypto).
- **Data:** BTC OHLCV daily/hourly; risk-free proxy for excess returns; realized-vol estimate.
- **Evidence:** **[Mixed]**
- **Citations:** Moskowitz, Ooi & Pedersen (2012), *JFE* 104(2):228-250 (futures only); Shen, Urquhart & Wang (2022), *Financial Review* 57(2):319-344, doi:10.1111/fire.12290 (intraday BTC TSMOM ~16-17%/yr, strong in downturns); Li & Zhang (2023), Springer.
- **Caveat:** Break-even transaction costs are only ~3-10 bps (Shen-Urquhart-Wang) — profits do **not** survive realistic 10-50 bps round-trip spot fees without leverage/maker rebates. Whipsaws in ranging markets are the dominant loss source. Heavy parameter sensitivity = data-mining risk. **Inverts** beyond ~1 month (becomes reversal).

### 2.2 Moving-average / trend filter on BTC — `ma_trend_filter`

- **Edge:** Hold BTC only above a long MA, else go to cash. A volatility/drawdown manager, not a pure alpha source. Trims crashes at the cost of bull-market lag.
- **Signal / formula:**
  `position_t = 1 if Price_t > SMA(Price, n) else 0` (long/flat).
  Dual-cross variant: long when `SMA_fast > SMA_slow`. Optionally vol-target the long leg.
- **Typical params:** `n = 200 days` (or 10 months); dual cross `50/200d` ("golden cross"); shorter `10-30d` MAs maximize Sharpe but trade more. Daily or weekly evaluation.
- **Data:** BTC daily close; long history (≥ 2013) to span multiple regimes.
- **Evidence:** **[Practitioner]**
- **Citations:** Grayscale Research, "The Trend Is Your Friend"; Glucksmann (2019), MSc thesis, ETH Zurich; ResearchGate 389395534 MA backtests.
- **Caveat:** Reduces realized vol and max drawdown vs buy-and-hold, but: (1) whipsaws in sideways markets; (2) lags reversals — gives back gains at tops, re-enters late; (3) very few independent signals on 200d (~8 golden-cross trades 2018-2023) → low statistical power, high overfit risk; (4) parameter/period sensitive. Treat as **risk management with modest switching cost**, not standalone alpha.

### 2.3 Multi-horizon trend factor (ML-aggregated MAs) — `trend_factor`

- **Edge:** Aggregate many MA/trend/volume signals across lookbacks into one predictive score (CTREND); prices the cross-section better than simple momentum and is more crash-robust.
- **Signal / formula:**
  Forecast `r_hat_{i,t+1} = alpha_t + Σ_j beta_{j,t} * z_{i,j,t}`, where `z` are normalized MA/trend/volume signals across horizons, coefficients smoothed over time; sort coins on `r_hat` into quintiles, value-weight long-short.
- **Typical params:** multiple MA horizons + price/volume indicators; weekly rebalance; quintile VW long-short.
- **Data:** broad coin panel — weekly returns, prices, volume, market cap (survivorship-controlled, winsorized/truncated extremes).
- **Evidence:** **[Established]** (single-author-group; thin independent replication)
- **Citations:** Fieberg, Liedtke, Poddig, Walker & Zaremba (2025), *JFQA* 60(7):3116-3153, doi:10.1017/S0022109024000747.
- **Caveat:** Headline ~3.87%/week is a **long-short quintile spread** — shorting many small coins is impractical. ML multi-signal aggregation invites overfitting; independent replication is thin; published 2025, so true forward OOS is short. Authors claim it survives 30-40 bps costs and persists in large coins — promising but unproven out of their sample.

### 2.4 Cross-sectional momentum (winner-minus-loser) — `xs_momentum`

- **Edge:** Rank coins by trailing return; long winners, short losers (value-weighted). One of Liu-Tsyvinski-Wu's three crypto factors.
- **Signal / formula:**
  Each week, sort coins (mcap > $1M) into quintiles by trailing `k`-week return; `WML = VW(top quintile) − VW(bottom quintile)`, held 1 week, rebalanced weekly.
- **Typical params:** formation `k = 1-4 weeks` (peak ~3 weeks); quintile/decile; weekly rebalance.
- **Data:** survivorship-bias-free panel of coin-level weekly returns + market caps + dollar volume (dead coins included).
- **Evidence:** **[Mixed]** — the most contested edge in the corpus.
- **Citations:** Liu, Tsyvinski & Wu (2022), *Journal of Finance* 77(2):1133-1177 (2.5-4.1% excess weekly, 2014-2018); **contra:** Grobys & Sapkota (2019), *Economics Letters* 180:6-10 (insignificant); Dobrynskaya (SSRN 3480348); Grobys et al. (2025), *FMPM* 39(4):443-476 (severe crashes); Grobys & Shahzad, *IJFE* doi:10.1002/ijfe.70036 ("illusion").
- **Caveat:** Headline returns inflated by illiquid microcaps (un-tradeable, un-shortable). Statistically insignificant once non-normality handled; crash-prone on single-coin jumps. Decays sharply with mcap cutoffs and post-2018 OOS. **Fragile, crash-prone, costly to short.** Lower priority for a retail researcher.

### 2.5 Cross-sectional short-term reversal — `xs_reversal`

- **Edge:** Buy prior-day/week cross-sectional losers, short winners.
- **Signal / formula:**
  `signal_i = -1 * (prior 1-day or 1-week return)`, ranked cross-sectionally; long bottom decile / short top decile; rebalance daily/weekly. (Lehmann 1990 contrarian: `w_i ∝ -(r_i − r_market)`.)
- **Typical params:** formation 1 day-4 weeks; holding 1 day-1 week. Sign flips to momentum below ~1 month *in liquid names*.
- **Data:** daily/intraday OHLCV cross-section; dollar volume + bid-ask or Amihud illiquidity for conditioning.
- **Evidence:** **[Established]** as a documented effect — but mostly untradeable.
- **Citations:** Zaremba, Bilgin et al. (2021), *Finance Research Letters*; Lehmann (1990), *QJE*; Liu-Tsyvinski-Wu (2022); Dobrynskaya (2021).
- **Caveat:** Driven almost entirely by **illiquid small coins**; the largest/most-tradeable coins show daily *momentum*, so the signal **inverts by liquidity bucket**. Much of the gross edge is bid-ask bounce (Lehmann critique) and decays after realistic spreads. Lives where you can least trade.

### 2.6 Cash-and-carry / funding harvest (long spot, short perp) — `carry`

- **Edge:** When perp funding is persistently positive, long spot + short perp is delta-neutral and harvests the funding longs pay shorts.
- **Signal / formula:**
  Position: long 1 BTC spot, short 1 BTC perp (delta ≈ 0).
  `PnL_per_period = funding_received − financing − fees ± basis_convergence`.
  `funding ≈ EMA( (perp_mark − spot_index) / index )`, clamped by the exchange band (e.g. Binance fixes at 0.01%/8h inside the clamp), settled every 8h (hourly on some venues).
- **Typical params:** enter when annualized funding/basis > T-bill + execution + counterparty buffer (practitioner re-engage threshold ~10% APR / ~5.5% excess); short-leg leverage low (≤ 2-3×; 10× ⇒ likely liquidation per He et al.); rebalance to stay delta-neutral.
- **Data:** perp mark + 8h funding history, spot index, fixed-maturity futures for basis, borrow/financing rates, taker/maker fee tiers, open interest & liquidations (crowding).
- **Evidence:** **[Established]** (but decaying)
- **Citations:** Schmeling, Schrimpf & Todorov (2023, rev. 2025), BIS WP No. 1087 / SSRN 4268371; He, Manela, Ross & von Wachter (2024), arXiv:2212.06888 (BTC SR ~1.8 retail / ~3.5 MM).
- **Caveat:** **Inverts when funding goes negative** (FTX Nov 2022 — short leg then *pays*). Decays: He et al. Sharpe 2.39 (2021) → 0.70 (2022) → 1.32 (2023); 2024 spot-ETF compressed basis (~3pp DiD). Amberdata: only ~8% of 2025 days offered >10% APR; Q2/Q4-2025 negative. Liquidation risk on the short leg without cross-margin. **Ignore the He et al. "2024 SR 11.52" — it's an N=1,682 partial-year artifact.** Net excess over T-bills is small once financing is netted.

### 2.7 The crypto carry / basis factor — `basis_factor`

- **Edge:** Futures-spot basis is large (sometimes >40% p.a.) and time-varying; a priced inconvenience yield from segmentation and limits to arbitrage.
- **Signal / formula:** `Carry_t = (F_{t,T} − S_t) / S_t` annualized (fixed-maturity), or funding-implied basis (perp). Capture via cash-and-carry when carry > transaction + financing costs.
- **Typical params:** sample Mar 2019-Jul 2024 (SST); carry ranged < −50% (FTX Nov 2022) to > +45% (pre-ETF Jan 2024).
- **Data:** fixed-maturity futures + spot across exchanges (incl. CME), perp funding, margin/liquidation, ETF-era flows.
- **Evidence:** **[Established]**
- **Citations:** Schmeling, Schrimpf & Todorov (2023, rev. 2025), BIS WP 1087; He et al. (2024), arXiv:2212.06888; Du, Tepper & Verdelhan (2018), *JF* (CIP-deviation benchmark).
- **Caveat:** Not a free lunch — compensation for limits-to-arbitrage/funding risk (Brunnermeier-Pedersen). Severe drawdowns; force-liquidation before convergence at high leverage. **Structurally decays** as the asset class institutionalizes; 2024 ETF causally compressed it. Backtests weighted to 2019-2021 massively overstate forward Sharpe.

### 2.8 Variance risk premium (selling vol) — `short_vol`

- **Edge:** BTC option-implied variance systematically exceeds subsequent realized variance, so short-vol earns a premium most of the time.
- **Signal / formula:**
  `VRP = E[RV] − IV^2` over the option horizon. Tradable proxy = P&L of a **delta-hedged short ATM straddle** or short variance swap. Seller is paid.
- **Typical params:** horizons 1-30d; ATM/near-ATM; delta-hedge band rebalancing (hedge when `|delta|` breaches threshold); weekly tenor common on Deribit.
- **Data:** Deribit option chains + IV surface, option mid + bid/ask, perp/spot for delta hedging, 5-min returns for RV.
- **Evidence:** **[Established]**
- **Citations:** Alexander & Imeraj (2021), *J. Alt. Investments* 23(4), SSRN 3383734; Almeida, Grith, Miftachov & Wang (2024/25), arXiv:2410.15195; Atanasova et al. (2024), AUT (Deribit 2020-2024).
- **Caveat:** **Short a fat left tail** — VRP spikes (and short vol loses badly) around large moves in *either* direction (unlike equities). Short backtests systematically understate left-tail losses; daily selling hit ~45% drawdowns. Regime-dependent and can flip sign once vol-of-vol is modeled (Du et al. 2025). Costs (Deribit ~0.0003 BTC/contract capped at 12.5% of premium, ATM bid/ask ~3%, ~5% slippage, ongoing delta-hedge slippage) eat the thin edge. **Size small, never naked.**

### 2.9 Pairs / cointegration (z-score spread reversion) — `pairs_coint`

- **Edge:** A cointegrated pair has a stationary spread; trade z-score deviations, exit on reversion.
- **Signal / formula:**
  Hedge ratio `beta` via Engle-Granger/Johansen: `spread = log(P_a) − beta*log(P_b)`.
  `z = (spread − rolling_mean) / rolling_std`. Enter `|z| > z_entry`, exit near `z≈0`, stop `|z| > z_stop`.
- **Typical params:** `z_entry` 1.5-2.5; `z_exit` 0-0.5; `z_stop` 3-4; rolling window days-weeks; ADF/Johansen on rolling window. **BTC-ETH is the canonical robust pair.**
- **Data:** synchronized OHLCV for both legs from the *same* venue/quote currency; funding if perps; borrow/short availability.
- **Evidence:** **[Mixed]**
- **Citations:** Tadi & Witzany / copula-cointegration (2024), *Financial Innovation*; Erasmus thesis (thesis.eur.nl/pub/67552); Leung & Li (2015), *Optimal Mean Reversion Trading*; Krauss (2017), *J. Economic Surveys* (documents OOS decay).
- **Caveat:** Published Sharpes (~2.45, "12%/month", "100% win rate") are in-sample and fragile; cointegration breaks in regime shifts. Severe pair-selection multiple-testing. **Negative skew** (many small wins, rare large losses on de-cointegration/depeg/delisting). ~7 bps fees + ~20 bps slippage × 2 legs round-trip erodes most of it; altcoin shorting is the binding constraint. Restrict to a few persistently cointegrated high-cap pairs.

### 2.10 Ornstein-Uhlenbeck reversion with half-life thresholds — `ou_reversion`

- **Edge:** If a spread fits an OU process, derive entry/exit thresholds and holding times from the mean-reversion speed.
- **Signal / formula:** `dX = theta*(mu − X)dt + sigma dW`; `half_life = ln(2)/theta`; trade standardized deviation; optimal stop/take from Leung-Li given cost `c`.
- **Typical params:** fit `theta, mu, sigma` by AR(1)/MLE; use only short, stable half-lives (hours-few days); reject if half-life unstable or > a few hundred bars.
- **Data:** the residual/spread series feeding the pairs signal.
- **Evidence:** **[Practitioner]**
- **Citations:** Leung & Li (2015/16); Hudson & Thames "Optimal Trading Thresholds for the O-U Process."
- **Caveat:** A **model, not an edge** — inherits all pairs fragility. Parameter non-stationarity is the killer: a series mean-reverting in-sample can become a random walk/trend OOS, turning "fade the deviation" into "add to a loser into a trend." Use only for sizing/threshold derivation on a series you've *independently* established as stationary.

### 2.11 Realized-measure GARCH (HAR / Realized-GARCH) for vol forecasting — `vol_forecast`

- **Edge:** Models ingesting high-frequency realized measures forecast BTC vol better OOS than plain GARCH(1,1). A *forecasting* tool, not a signal.
- **Signal / formula:**
  Realized-GARCH: `h_t = omega + beta*h_{t-1} + gamma*x_{t-1}`, measurement `x_t = xi + phi*h_t + tau(z_t) + u_t`, `x` = jump-robust realized variance.
  HAR: `RV_t = c + b_d*RV_d + b_w*RV_w + b_m*RV_m`.
- **Typical params:** GARCH(1,1) baseline; EGARCH/TGARCH for asymmetry; Student-t/skewed-t innovations; RV from 5-min bars; bipower/tri-power jump-robust measures.
- **Data:** high-frequency BTC OHLCV (5-min) for RV; daily returns for GARCH.
- **Evidence:** **[Established]** (as forecasting accuracy)
- **Citations:** Katsiampa (2017), *Economics Letters*; Shen, Urquhart & Wang (2020), *NAJEF*; *Risks* 11(12):211 (2023).
- **Caveat:** **Forecast accuracy ≠ trading edge** — better vol forecasts help sizing/option pricing, not alpha. Watch the **leverage-effect sign**: BTC often shows *inverse/positive* asymmetry (positive shocks raise vol), unstable over time — an equity-style EGARCH asymmetry can be wrong. Select by OOS loss, not in-sample fit.

### 2.12 Volatility targeting / vol scaling — `vol_target`

- **Edge:** Scale position inversely to forecast vol (e.g. target 20% annualized) to stabilize risk and cut tail risk / max drawdown / vol-of-vol.
- **Signal / formula:** `w_t = target_vol / sigma_hat_t` (cap leverage); `sigma_hat` from EWMA/GARCH/realized.
- **Typical params:** target vol 15-50% for BTC; estimator lookback 20-60d (or EWMA λ=0.94); leverage cap ~1-3×; daily/weekly rebalance.
- **Data:** daily/intraday returns for `sigma`; the return series being scaled.
- **Evidence:** **[Mixed]**
- **Citations:** Harvey, Hoyle, Korgaonkar, Rattray, Sargaison & Van Hemert (2018), *JPM*; Hocquard, Ng & Papageorgiou (2013), *JPM*; Grayscale (2024).
- **Caveat:** **Robust:** almost always reduces tail risk / max drawdown / vol-of-vol (Harvey 60+ assets) — independent of the leverage effect. **Fragile:** the Sharpe gain only appears for assets with negative return-vol correlation; **BTC's is unstable and often inverted**, so the equity-style Sharpe lift is *not* guaranteed and can hurt. Practitioner crypto lifts usually pair scaling *with* a trend signal — credit the signal. Forces buying calm / selling stress → turnover when liquidity thins.

### 2.13 Kelly / fractional Kelly position sizing — `kelly_sizing`

- **Edge:** Sizing at a fraction of full-Kelly maximizes long-run geometric growth; half-Kelly or less keeps most growth with far lower drawdown.
- **Signal / formula:** discrete `f* = (b*p − q)/b`; continuous `f* = (mu − r)/sigma^2` (Merton); fractional `c*f*`, `c ∈ [0.25, 0.5]`.
- **Typical params:** `c = 0.25-0.5` (never full); re-estimate `mu, sigma` on rolling windows; cap exposure.
- **Data:** estimated return distribution / win-prob and payoff ratio; rolling `mu, sigma`.
- **Evidence:** **[Mixed]**
- **Citations:** Kelly (1956), *Bell System Tech. J.*; MacLean, Thorp & Ziemba (2011), World Scientific.
- **Caveat:** Math is sound but **extremely sensitive to inputs**, which are estimated with huge error in crypto — overestimating edge → over-betting → ruin. `f*` assumes Gaussian/known distribution; BTC's fat tails mean the safe fraction is *lower* than naive estimates. Headline "allocate ~33% to BTC" numbers are artifacts of assumed inputs. **Bet small fractions, combine with vol targeting and hard caps.**

### 2.14 Order-flow / order-book imbalance (OBI, CVD) — `order_flow` (execution overlay only)

- **Edge:** Net signed order flow (CVD) and L2 bid-ask volume imbalance predict very-near-term mid-price direction.
- **Signal / formula:** `OBI = (V_bid − V_ask)/(V_bid + V_ask)` over top-N levels; `CVD = Σ(buy − sell market volume)`. Positive OBI / rising CVD → short-term up pressure.
- **Typical params:** horizon ~1-30 seconds; depth L=1..5; CVD windows seconds-minutes.
- **Data:** full L2 order book + tick-level signed trades (storage/latency-intensive).
- **Evidence:** **[Mixed]**
- **Citations:** Silantyev (Towards Data Science, ETHUSD 10s); Charles University thesis (dspace.cuni.cz); Cont-Kukanov-Stoikov OFI lineage.
- **Caveat:** Predictability is **real but tiny and short-lived** (<10 bps over 10s vs ~10 bps/trade fees) — **not a standalone strategy**, only an execution/order-placement overlay for latency-competitive makers. CVD "divergence" is discretionary with no rigorous edge. For anyone paying taker fees or non-colocated, costs dominate entirely. *Out of scope for a research/backtest terminal except as a fill-model component.*

### 2.15 Funding/positioning as contrarian crowding & liquidation-cascade fades — `funding_contrarian`, `cascade_fade`

- **Edge:** Persistently extreme funding flags one-sided leveraged crowding; extremes can precede reversals over longer (30-365d) horizons. Forced-liquidation cascades overshoot and snap back.
- **Signal / formula:** risk-off / contrarian tilt when funding z-score *and* OI are extreme; fade after a liquidation spike that exhausts (price down + OI down). Combine with OI and liquidation data, never funding alone.
- **Typical params:** multi-sigma funding extremes (~>0.05%/8h sustained); horizons days-to-year; cascade thresholds are *ad hoc and unvalidated*.
- **Data:** funding history, open interest, liquidation feeds, spot price (venue-specific, throttled, noisy).
- **Evidence:** **[Practitioner]** / **[Weak]** as standalone predictors.
- **Citations:** Schmeling-Schrimpf-Todorov (2023) (carry predicts liquidations); BraveNewCoin funding-predictivity test (R² ~0.001-0.017 at next-bar → essentially noise); QuantJourney / CryptoQuant / Yellow.com (practitioner).
- **Caveat:** Funding as a **next-bar directional predictor is noise**. As a contrarian, funding can stay extreme through an entire trend — naively fading bleeds. Cascade fades are catastrophic if the cascade is the *start* of a regime change, not exhaustion; short squeezes are unbounded. **Use as a risk/positioning overlay, not standalone alpha.** The defensible use of funding is harvesting it via delta-neutral carry (2.6), not directional fading.

### 2.16 Cross-exchange / cross-region price-deviation arbitrage — `xexch_arb`

- **Edge:** Same asset, different prices across venues/regions; the spread mean-reverts ("kimchi premium").
- **Signal / formula:** `deviation = (P_A − P_B)/P_B`; trade when it exceeds round-trip cost incl. fees, transfer latency, FX/capital-control friction.
- **Data:** synchronized cross-venue order books, withdrawal/deposit times, fee schedules, on/off-ramp rates.
- **Evidence:** **[Established]** historically — **largely decayed**.
- **Citations:** Makarov & Schoar (2020), *JFE* 135(2):293-319; Crépellière, Pelster & Zeisberger (2022).
- **Caveat:** Cleanest documented mean-reverting deviation, but cross-exchange spreads are near-zero since ~2018; surviving cross-region spreads reflect *real* frictions (capital controls, KYC, transfer latency, exchange-insolvency tail). Requires pre-positioned inventory on both sides. **Not a retail edge today.**

### 2.17 On-chain & sentiment factors — `nvt`, `mvrv`, `sopr`, `exch_flows`, `fear_greed` (descriptive overlays only)

- **Edge (claimed):** NVT (crypto P/E), MVRV / MVRV-Z (cost-basis over/undervaluation), SOPR (realized profit/loss), exchange netflows (sell/accumulate intent), Fear & Greed (contrarian).
- **Signal / formula:**
  `NVT = MarketCap / on-chain tx value` (NVT-Signal uses 90d-MA denominator); `MVRV = MarketCap / RealizedCap`; `MVRV-Z = (MarketCap − RealizedCap)/std(MarketCap)`; `SOPR = value sold / value paid`; `Netflow = inflow − outflow`; FGI contrarian rule long < 20 / flat > 80.
- **Typical (historical) bands:** MVRV-Z top zone formerly >7, now ~3.5-4; bottom MVRV < 1. **These bands decay every cycle.**
- **Data:** **point-in-time (PIT)** on-chain series (Glassnode PIT metrics, ~July 2025+) — *mandatory*; market cap; FGI series.
- **Evidence:** NVT **[Weak]**, MVRV **[Mixed]**, SOPR **[Weak]**, exchange flows **[Weak]**, Fear & Greed **[Weak]**.
- **Citations:** Woo (2017); Kalichkin (2018); Mahmudov & Puell (2018) / Coin Metrics Realized Cap; Glassnode "Your Backtest Is Lying" (PIT); *Finance Research Letters* (2026) S305070062600006X (returns Granger-cause FGI, no OOS gain); Liu-Tsyvinski-Wu, "Accounting for Cryptocurrency Value" (Cowles/SSRN 3951514).
- **Caveat:** **Look-ahead/revision is the dominant trap, larger than cost.** Entity-clustering and last-moved valuation are retroactively revised — a value pulled today for a past date is *not* what was published then; Glassnode's own MA-crossover backtest looks profitable on revised data and degrades on PIT. NVT denominator is structurally non-comparable across eras; MVRV/SOPR rest on ~3-4 cycles (overfit) with thresholds that fall every cycle; exchange "flows" are often internal reshuffles; FGI is reactive and partly built *from* price (tautological). **Use only as descriptive risk-zone gauges with strict PIT data, never as standalone OOS signals.**

---

## 3. Costs & pitfalls (model ALL of these before believing any backtest)

**Fees (retail baseline).** Binance spot 0.10% maker/taker (~0.075% with BNB); USDT-M perp ~0.02% maker / 0.05% taker standard; Coinbase materially higher. Honest baseline: **0.04-0.10%/side spot taker, 0.05%/side perp taker.** Treat anything below ~0.1% round-trip spot taker as optimistic.

**Slippage / market impact.** Top-10 coins ~0.05-0.10%; outside top-100 ~0.5-2%; microcaps 5-10% or untradeable. Retail fills average ~0.4% worse than institutional. A 20% gross strategy realistically becomes ~8% after 0.5% slippage + 0.1% fees per trade; high-turnover dies fastest.

**Funding (perpetuals).** Exchanged ~every 8h, clamped ~±0.03%/interval (spikes higher in trends). For multi-day holds, funding *dominates* fees and can flip a backtest's sign. **Backtesting perps without applying realized historical funding on the actual position (correct 8h timestamps) is one of the largest error sources.** Funding can go negative (FTX 2022), flipping carry from yield to cost.

**Borrow / shorting.** Spot shorts need borrow (financing cost + inventory limits); many small-caps are uneconomical/impossible to borrow. This breaks every long-short headline (xs-momentum, reversal, trend-factor, pairs). On perps the "short cost" is the funding sign.

**Survivorship bias.** Universes of only-still-trading coins drop dead/delisted tokens — can inflate crypto backtests by an estimated **200-400%**. Fix: PIT universe including delisted assets.

**Look-ahead bias.** Revised/forward-filled funding or on-chain series; candle close used at candle open; future-dated labels. 24/7 tape + extreme intrabar moves make this easy. Fix: strict event-time bars, decision-at-bar-close; for ML use López de Prado purging + embargo.

**Timestamp alignment & venue price.** No single "BTC price" — OHLCV differs across venues; misaligned trade/funding/candle clocks create phantom edges. Normalize to one clock; price fills/funding on the *venue you would actually trade*.

**Deflated Sharpe Ratio (DSR).** Before believing any Sharpe, deflate for number of trials `N`, sample length `T`, and non-normality:
`PSR(SR0) = Φ( (SR_hat − SR0) * sqrt(T−1) / sqrt(1 − skew*SR_hat + ((kurt−1)/4)*SR_hat²) )`,
with DSR benchmark `SR0 ≈ sqrt(Var[SR across trials]) * ( (1−γ)*Φ⁻¹(1 − 1/N) + γ*Φ⁻¹(1 − 1/(N·e)) )`, `γ ≈ 0.5772`. Significant when **DSR > 0.95**. Negative skew + fat tails (carry, short-vol) sharply lower DSR. *Bailey & López de Prado (2014), JPM 40(5); SSRN 2460551.*

**Probability of Backtest Overfitting (PBO) via CSCV.** Split the `T × N` strategy-return matrix into S blocks; over all `C(S, S/2)` IS/OOS splits, pick the IS-best and record its OOS rank; **PBO = fraction of splits where the IS-best lands below OOS median.** PBO > ~0.5 ⇒ overfit selection. Requires retaining *every* trial's returns. *Bailey, Borwein, López de Prado & Zhu (2017), J. Computational Finance 20(4); SSRN 2326253.*

**Minimum Backtest Length (MinBTL).** `MinBTL (yrs) < 2·ln(N)/E[max_N]`. Worked example: with only 5 years of data, trying >~45 independent configs almost guarantees IS Sharpe 1 / OOS Sharpe 0. Crypto's short, regime-dominated history makes this brutal — correlated parameter sweeps inflate effective `N` fast. *Bailey et al. (2014), Notices AMS 61(5):458-471; SSRN 2308659.*

**Multiple-testing hurdle.** A single-test t = 2.0 is not evidence in a field that tested hundreds of signals on the same data; the corrected hurdle rises to **t > ~3.0** (Bonferroni/Holm/BHY). Principle transfers to crypto even if the exact number doesn't. *Harvey, Liu & Zhu (2016), RFS 29(1):5-68.*

**Sample-size & CV discipline.** <30 trades meaningless; 30-100 directional; want 100-300+. Use **Purged K-Fold + embargo** or **Combinatorial Purged CV (CPCV)** — standard k-fold leaks future info in time series. Report walk-forward + CPCV multi-path *dispersion* as the headline, not the single best equity curve.

**Negative skew & regime inversion.** Carry, short-vol, z-score/funding/cascade fades all win small and often, lose huge on regime breaks — Sharpe overstates quality. Mean-reversion inverts in trends; trend/momentum whipsaws in ranges. **Every reversion strategy needs an explicit trend/volatility regime filter.**

**Counterparty/exchange risk** (FTX) is a real cost absent from OHLCV backtests.

---

## 4. Open-source resources

**Backtesting engines**
- **NautilusTrader** — Rust core, nanosecond event-driven, backtest-live parity, multi-venue; strongest for realistic crypto fills/funding/L2/perp.
- **vectorbt / vectorbtPRO** — fast vectorized Numba parameter sweeps + metrics; easy to overfit — pair with DSR/PBO.
- **Backtesting.py** — simple, mature, single-asset.
- **Backtrader** — flexible event-driven, large community.
- **Zipline-reloaded** — equities-oriented; usable for cross-sectional factor work.
- **Freqtrade** — open-source bot with backtesting/hyperopt over CCXT (Binance/Bybit/Kraken/OKX/Kucoin).

**Data & exchange access**
- **CCXT** — unified API to 100+ exchanges for OHLCV / trades / funding (`fetchFundingRateHistory`); research-only.
- **Tardis.dev** — tick-level normalized L2 order book, trades, OI, funding, liquidations across derivatives venues; best granularity for honest fill/funding simulation (free historical via Deribit partnership).
- **Kaiko** — institutional consolidated market data (pricier); **CoinAPI** — historical funding-rate API + survivorship-bias guidance; **Binance Vision** raw dumps (you must reconstruct PIT delisted universes).
- **Coinglass / Amberdata / Coinalyze / CryptoQuant** — funding, OI, liquidation datasets (note revision risk on entity-labeled on-chain series).
- **Glassnode** (Studio/Docs/Research) — on-chain metrics incl. **Point-in-Time (PIT) variants** (essential); **Coin Metrics** Community data + Realized Cap methodology.
- **Deribit DVOL** + Deribit Insights — option/vol-regime data and backtests; free historical options via Deribit API.
- **Alternative.me** — free Crypto Fear & Greed historical API.

**Statistical-rigor libraries**
- **arch** (Kevin Sheppard) — GARCH/EGARCH/Realized-GARCH estimation; **statsmodels** — Engle-Granger `coint()`, Johansen, ADF, Fama-MacBeth, VAR/Granger.
- **mlfinlab / Hudson & Thames** — Purged K-Fold, CPCV, DSR/PSR, PBO (licensing now commercial — check terms); **skfolio** — scikit-learn-compatible `CombinatorialPurgedCV`; R **`pbo`** (mrbcuda/pbo) for CSCV-based PBO.
- **QuantLib** — option pricing/Greeks and delta-hedge simulation; **pyfolio / quantstats** — tearsheets (reporting only, not significance tests).
- BTC-premia replication code: `github.com/wang-zjin/BTC-premia`.

**Books**
- **Marcos López de Prado, *Advances in Financial Machine Learning*** (2018) — purging/embargo, CPCV, DSR/PSR, meta-labeling; the canonical honesty reference. Also *Machine Learning for Asset Managers* (2020).
- **Ernest Chan, *Algorithmic Trading*** (2013) & *Machine Trading* (2017) — cost-aware backtesting, mean-reversion/momentum diagnostics.
- **MacLean, Thorp & Ziemba, *The Kelly Capital Growth Investment Criterion*** (2011) — sizing theory.
- **Tim Leung & Xin Li, *Optimal Mean Reversion Trading*** — OU thresholds.
- **David Aronson, *Evidence-Based Technical Analysis*** — data-mining bias, White's Reality Check / bootstrap; **Rishi Narang, *Inside the Black Box*** — luck vs skill.

**Key free papers**
- Bailey-Borwein-LdP-Zhu, "Pseudo-Mathematics and Financial Charlatanism" (Notices AMS 2014); "The Probability of Backtest Overfitting" (davidhbailey.com preprint); Bailey-LdP "Deflated Sharpe Ratio" (SSRN 2460551); Harvey-Liu-Zhu "…and the Cross-Section of Expected Returns" (NBER w20592).
- Schmeling-Schrimpf-Todorov "Crypto Carry" (BIS WP 1087, free PDF); He et al. "Fundamentals of Perpetual Futures" (arXiv:2212.06888); Liu-Tsyvinski-Wu "Common Risk Factors in Cryptocurrency" (J. Finance 2022).

---

## 5. Ranked recommendation — implement these FIRST

Ranked by robustness, documentation quality, and low overfit risk — favouring risk management over fragile alpha. **Buy-and-hold is the baseline every strategy is scored against.**

**0. `buy_and_hold` (BASELINE — implement first).** The benchmark. Most "edges" are just leveraged exposure to BTC's uptrend; you cannot judge anything without this reference, net of cost, with the same DSR treatment.

**1. `ma_trend_filter` (200d / 50-200 dual cross) — [Practitioner].** Simplest, most documented, fewest parameters, lowest turnover. Its honest value (drawdown/crash reduction) is well-established and it's trivial to validate against buy-and-hold. Few independent signals = run it precisely *because* it resists over-tuning. Start here for the risk-management layer.

**2. `vol_target` — [Mixed, but robust on the part that matters].** The drawdown/vol-of-vol reduction is robust across 60+ assets independent of the leverage effect (Harvey 2018). Be explicit that in crypto the durable benefit is tail control, not Sharpe. Pairs naturally with #1 and #3. Implement as a sizing layer, not a standalone.

**3. `tsmom` (short-lookback, L = days-to-4-weeks, vol-scaled) — [Mixed].** The best-documented single-asset directional effect, with a concrete formula and clear failure modes. Implement with realistic costs front-and-center (it has ~3-10 bps break-even) so the terminal *shows* when it dies. Low coin-universe complexity (BTC only). Treat as the alpha candidate most likely to teach honest lessons.

**4. `carry` (long spot / short perp, BTC) — [Established, decaying].** The best-supported *premium* and the most instructive to model correctly: it forces you to implement realized funding, financing-cost netting, and delta-rebalancing — exactly the machinery that exposes naive backtests. Show its decay (2021→2025) and negative-funding inversion explicitly. Single-asset, delta-neutral, no microcap shorting needed.

**5. `pairs_coint` (BTC-ETH only) — [Mixed].** A clean, well-documented mean-reversion archetype on the one pair with the most persistent cointegration, sidestepping the microcap-shorting and pair-mining traps that sink the general case. Implement with hard stops and a cointegration-breakdown guard so the terminal demonstrates de-cointegration risk.

**6. `short_vol` (delta-hedged short ATM straddle) — [Established premium, tail-lethal] — implement last / optional.** Include for completeness of the "real premia" set, but only with mandatory left-tail stress tests and small sizing. It's the clearest teaching case that a high in-sample Sharpe on a negatively-skewed payoff is a trap (drives DSR/skew/kurtosis home). Requires Deribit option data — higher data overhead than #1-5, hence last.

**Explicitly deprioritized for first cut:** `xs_momentum`, `xs_reversal`, `trend_factor` (microcap-inflated, un-shortable, statistically contested, need survivorship-free panels); `order_flow` (execution overlay only, sub-fee horizon); `funding_contrarian` / `cascade_fade` (practitioner-grade, no validated thresholds); `xexch_arb` (decayed, not retail); all on-chain/sentiment signals (`nvt`/`mvrv`/`sopr`/`exch_flows`/`fear_greed` — descriptive only, PIT-data-gated, reactive). Use `vol_forecast`, `ou_reversion`, and `kelly_sizing` as *supporting components* (sizing/threshold/forecasting) of the strategies above, never as standalone signals.

**Cross-cutting build requirement:** every strategy in `strategies.py` must run through the same harness that applies realistic fees + slippage + (for perps) realized funding, builds a PIT survivorship-free universe, and reports **net-of-cost return, trial count N, Deflated Sharpe, and CPCV multi-path dispersion** alongside the buy-and-hold baseline. The headline metric is the *deflated, net, multi-path* result — never a single equity curve.

---

Note: I wrote this brief directly as my response rather than to a file. The repo at `/Users/azul/Code/lattice` is the Lattice learning platform — there is no existing `btc-quant` project, `strategies.py`, or related directory, so there was no target file to edit. If you want this persisted, the natural path would be something like `/Users/azul/Code/lattice/btc-quant/DESIGN_BRIEF.md`, but I did not create files per the no-unsolicited-docs instruction.
