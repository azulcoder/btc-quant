# btc-quant — Design Brief: Option Chain, Honest Vol Signals, Live Charts, UI/UX, Build Plan

Research / backtest terminal. **No API keys, no orders, no authenticated endpoints, zero financial risk** (see `DISCLAIMER.md`). Everything below uses **public, keyless** Deribit/exchange endpoints and **official** embeds — no scraping. This brief targets the existing **no-build static dashboard** (`dashboard/index.html` + `app.js` + `charts.js` + `quant.js` + `styles.css`) and the Python `btcquant` engine. It extends, not replaces, the current panels (leaderboard, performance, candles, funding, VRP, on-chain).

Convention used throughout: a finding tagged **SIGNAL** has documented predictive content worth surfacing as a (risk-) signal; **DESCRIPTIVE** means show-and-label-only, never backtested as alpha. Inline citations are URLs/short refs from the cluster surveys.

---

## 1. Option chain — API calls, fields, formulas, pitfalls

### 1.1 Data layer — exactly two public Deribit endpoints

**Base layer (one call, cheap, rate-limit-friendly): `get_book_summary_by_currency`.** Enumerates the entire option universe in a single request.

```
GET https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency=BTC&kind=option
-> result: [ { instrument_name, mark_price, bid_price, ask_price, mid_price,
               mark_iv, underlying_price, underlying_index, interest_rate,
               open_interest, volume, high, low, last, price_change,
               creation_timestamp } , ... ]
```
- Use it to enumerate the chain and seed the surface. It returns **`mark_iv` only** — no per-contract greeks, no `bid_iv`/`ask_iv`. A smile built purely from this is a **MARK smile, not a tradable bid/ask smile**.
- `underlying_index` distinguishes whether a strip is quoted off a listed future or the spot index — the meaning of `underlying_price` differs. **Group expiries by `underlying_index`** when building per-expiry forwards.
- Source: docs.deribit.com `get_book_summary_by_currency`.

**Detail layer (per-instrument, only where needed): `ticker`.** The only source of greeks and bid/ask IV.

```
GET https://www.deribit.com/api/v2/public/ticker?instrument_name=BTC-27JUN25-100000-C
-> result.greeks = { delta, gamma, vega, theta, rho }
   result.{ mark_iv, bid_iv, ask_iv, best_bid_price, best_ask_price,
            best_bid_amount, best_ask_amount, underlying_price, index_price,
            interest_rate, mark_price, open_interest, settlement_price,
            min_price, max_price, estimated_delivery_price }
```
- `greeks.delta` ∈ [0,1] for calls, [−1,0] for puts (Black-Scholes); `vega` is **per 1 vol POINT** (per 1% IV); `theta` is `min(1-day, lifetime)` so near-expiry theta is the lifetime decay, not the 1-day figure.
- **One instrument per call** — pulling greeks for the whole chain is many requests; watch rate limits. Fetch ticker only for the contracts that actually enter the fit (the gated, OTM, liquid subset — see 1.6), not the whole universe.
- Source: docs.deribit.com `ticker`; insights.deribit.com option-greeks intro.

**CORS note:** Deribit REST is reachable from the browser today (the existing `loadVrp()` already calls `get_volatility_index_data` directly). Treat that as best-effort and degrade gracefully (see §5 and the existing stale-banner pattern), exactly as the VRP/on-chain panels do.

### 1.2 The unit trap — `*_iv` is in PERCENT

`mark_iv`, `bid_iv`, `ask_iv` are **percent** (e.g. `80` = 80% annualized). **Divide by 100 before any vol formula:** `iv_decimal = mark_iv / 100.0`. Forgetting this is the single most common silent 100× bug across the whole surface, RR, BF (engineering writeup, medium.com/coinmonks Deribit IV surface). Deribit docs are silent on the unit — **sanity-check once on a live ATM contract** (does `mark_iv/100` reprice `mark_price` under Black-Scholes?) before hard-coding. `vega` per 1 percentage-point is consistent with the percent convention.

### 1.3 Per-expiry forward via put-call parity (the correct ATM anchor)

Do **not** anchor on spot — BTC futures carry a non-trivial, time-varying basis that biases skew. Recover the synthetic forward per expiry:

```
From C − P = e^{-rT}(F − K)  ⇒  F_implied(K) = K + e^{rT}·(C_mid(K) − P_mid(K))
Robust estimate:  K* = argmin_K |C_mid(K) − P_mid(K)|   (strike nearest forward, least parity noise)
                  F  = F_implied(K*)   (average the implied forwards if several K tie)
                  K0 = max{ listed strike ≤ F }   (the ATM strike)
ATMF vol = IV interpolated at strike F
```
This is Deribit's own DVOL synthetic-forward step (insights.deribit.com DVOL; support.deribit.com market-data best practices).

**Shortcut:** Deribit marks options with an effectively **zero discount rate** (`ticker.interest_rate` ≈ 0; r absorbed into the forward — do **not** double-count discounting), and `underlying_price` is already the forward Deribit uses. For a fast first build, take `underlying_price` as `F` directly and skip the parity solve. The parity solve is the more robust path when `underlying_price` is stale.

### 1.4 The five views and their formulas

**(a) IV smile / skew per expiry.** x-axis = log-moneyness `k = ln(K/F)` (best for arbitrage diagnostics) OR BS delta (best for cross-expiry comparison — normalizes for differing strike ladders). Plot `mark_iv` (or `mid_iv = 0.5·(bid_iv + ask_iv)`), keep **OTM side only** (OTM puts for `K<F`, OTM calls for `K>F` — tighter, more liquid; ITM IV is parity-redundant). Interpolate with **shape-preserving PCHIP or SVI**, never a raw cubic spline (cubic splines are too wiggly for sparse BTC strikes and manufacture butterfly arbitrage).

```
SVI total variance:  w(k) = a + b·{ ρ·(k − m) + sqrt((k − m)^2 + σ^2) } ;   σ_BS(k) = sqrt(w(k)/T)
```
Source: medium.com/coinmonks; Gatheral-Jacquier SVI (arxiv 1204.0646); Baruch VW3 notes.

**(b) ATM term structure.** Per expiry, read ATMF IV (interpolate the fitted smile at `K=F`, equivalently 50d). Convert each expiry to year-fraction `T` to **08:00 UTC**, ACT/365 (see 1.5). Interpolate across tenors **in TOTAL VARIANCE, never in IV** — this is the rookie error that creates calendar arbitrage:

```
w_i = IV_ATMF(T_i)^2 · T_i
w(T) = w1 + (w2 − w1)·(T − T1)/(T2 − T1)
IV_term(T) = sqrt( w(T) / T )
```
Same near/far variance interpolation DVOL uses to hit a constant 30-day tenor (insights.deribit.com DVOL; Baruch VW3). Exclude the very front (< ~6h–1 day to expiry) — `T→0` makes ATM IV unstable.

**(c) Clean vol surface (advanced — flag as research-grade).** Industry pipeline: (1) per-expiry raw SVI fit on total variance; (2) enforce no-arb: **butterfly** (risk-neutral density `g(K) = e^{rT}·d²C/dK² ≥ 0`) and **calendar** (`w` non-decreasing in `T` at fixed `k`); (3) for a globally arbitrage-free surface use **SSVI/eSSVI**, which parametrizes the whole surface by the ATM total-variance curve `θ_t`, a correlation `ρ`, and curvature `φ(θ)`:

```
SSVI:  w(k,t) = (θ_t/2)·{ 1 + ρ·φ(θ_t)·k + sqrt( (φ(θ_t)·k + ρ)^2 + (1 − ρ^2) ) }
```
Source: Gatheral-Jacquier (arxiv 1204.0646, 2204.00312), Baruch VW3.
**Caveat:** raw per-slice SVI has **no closed-form parameter conditions guaranteeing no-arbitrage** (Gatheral-Jacquier say so explicitly) — either check density numerically per slice or use SSVI/eSSVI for global guarantees. SVI calibration is non-convex; use the **SVI-JW** reparametrization plus good initial guesses for day-to-day parameter stability.

**(d) 25-delta risk reversal & butterfly.** Read the fitted smile at +25d (call) / −25d (put):

```
RR25 = IV(25d call) − IV(25d put)                      (skew / asymmetry)
BF25 = 0.5·(IV(25d call) + IV(25d put)) − IV(ATM)      (convexity / smile curvature)
Reconstruction:  IV(25d call) ≈ ATM + BF25 + 0.5·RR25
                 IV(25d put)  ≈ ATM + BF25 − 0.5·RR25
```
Locate the 25d strikes by solving BS `delta(K)=±0.25` on the fitted smile (delta depends on IV — iterate, or fit IV as a function of `greeks.delta` directly). Three numbers (ATM, RR25, BF25) compactly summarize each expiry. Source: volquant FX-smile primer; vanna-volga (Wikipedia).
**Caveat:** Deribit `greeks.delta` is **plain spot/BS delta on the inverse payoff**, not the FX premium-adjusted / forward delta — a 25d strike from Deribit delta is **not** identical to an FX-desk 25d. Pick one delta convention, label it, apply it consistently across all expiries.

**(e) Put/call skew (documented sign convention).** Headline number = RR25. Also useful:

```
normalized skew(T) = (IV_25Dp(T) − IV_25Dc(T)) / IV_ATM(T)   ( = −RR25(T)/IV_ATM(T) )
```
Term-structure of skew = `RR25(T)` vs `T` (front-end is noisier/steeper).
**Pin one sign convention and label every chart** — vendors disagree (call-minus-put vs put-minus-put). Positive put-richness is the typical BTC downside-protection read, but BTC skew **changes sign** with regime (see §2).

### 1.5 Expiry / settlement / annualization conventions

Deribit options are **European, cash-settled, expire 08:00 UTC**. Delivery = **30-min TWAP of the Deribit index 07:30–08:00 UTC, 4s samples (~450)**, anti-manipulation. Therefore:
- `T = (expiry_08:00UTC − now) / (365 days)`, **ACT/365** to match the annualized IV. Get the expiry from `get_instruments.expiration_timestamp`.
- Near expiry `mark_iv` is unstable as `T→0` and intrinsic dominates — exclude/down-weight the last hours.
- `settlement_price` / `estimated_delivery_price` reflect the TWAP, **not** instantaneous `index_price` — don't mix them.
- Using calendar days, a 360-day year, or counting to midnight mis-annualizes IV and distorts the front of the term structure most.
- Source: support.deribit.com Settlement; insights.deribit.com DVOL. (The Settlement page 403'd automated fetch; the TWAP/4s/450-sample figures came from snippets — **re-verify on the live page before hard-coding**.)

### 1.6 DVOL as benchmark, not as a smile

We already pull DVOL in `app.js` (`loadVrp`). Keep it as the **30-day model-free benchmark** to validate our own ATM term structure and as a feature — not a smile.

```
GET .../public/get_volatility_index_data?currency=BTC&start_timestamp=..&end_timestamp=..&resolution=..
σ²_T = (2/T)·Σ_i (dK_i / K_i²)·e^{rT}·Q(K_i) − (1/T)·(F/K0 − 1)²    [Q = OTM option price]
DVOL = 100·sqrt( var interpolated to 30d ) ;  published index is an EMA (last 240 points)
```
**DVOL sits ABOVE pure ATM IV** by roughly the convexity (BF) premium — do **not** expect DVOL to equal your interpolated 30d ATMF IV; the gap *is* the smile's contribution. The EMA also lags an instantaneous re-price of your surface. Source: insights.deribit.com DVOL; gundersen VIX derivation; LSE simple-variance-swaps.

### 1.7 Pitfalls — gate every contract before it enters the fit

```
keep contract if:
  bid_price != null AND ask_price != null
  AND (ask − bid)/mid < tol           (e.g. drop if spread > ~2 vol-pts in IV)
  AND open_interest >= oi_min          (and/or 24h volume floor)
  AND |greeks.delta| >= 0.05           (deep wings are noise; same as DVOL's 5% cut)
  AND on the OTM side (puts below F, calls above F)
  AND creation_timestamp is fresh
```
- **`mark_iv` exists even for contracts with no live quotes** (Deribit interpolates from its surface), so a mark-IV smile looks complete but silently includes non-tradable points. **`bid_iv`/`ask_iv` reveal the true gaps** — surface that uncertainty in the UI.
- Over-aggressive filtering (dropping all low-OI wings) **flattens measured skew/BF**. There is a real cleanliness-vs-genuine-wing-demand tradeoff — make the filter thresholds visible/adjustable, don't hide them.
- Interpolation choice drives whether the surface is arbitrage-free: PCHIP/SVI across strikes, total-variance-linear across expiries, then run the butterfly + calendar checks; fall back to SSVI/eSSVI if either fails. Convert strike↔delta using the **same** `F` and `r` used to build the smile.
- Source: support.deribit.com best-practices + Mark-Prices; medium.com/coinmonks; Gatheral (1204.0646, 1804.04924).

---

## 2. Honest option signals — SIGNAL vs DESCRIPTIVE

Ranked by what survives. **Only the VRP / short-vol carry is robust enough to surface as a real (risk-)signal — and even that is a risk exposure, not alpha.** Everything else is descriptive or a vol-forecasting input. This is the consensus of the reviewed literature; the BTC options history is short (~2019+, few independent regimes), several results are single-paper, and edges are documented to decay with institutionalization.

### 2.1 Variance risk premium / short-vol carry — **SIGNAL (risk-premium exposure, direction-agnostic)**
- VRP = `E^Q[var] − E^P[var]`, operationalized as model-free implied variance (BKM/VIX strip, or DVOL²) minus realized variance. It is **large and persistently positive** (IV > RV ~70% of the time per Deribit's desk). Almeida-Grith-Miftachov-Wang (2025, arXiv:2410.15195): BTC RN variance ≈ 0.72 vs RV ≈ 0.58 ⇒ BVRP ≈ 0.14, ~7× the equity VRP. The economic read (Carr-Wu 2009, RFS): the negative variance-swap excess return is the price paid for protection against vol spikes that coincide with crashes.
- **It is NOT a market-timing signal.** Almeida et al. find the BTC BVRP→future-return relation is **negative** (opposite of Bollerslev-Tauchen-Zhou's equity result) — porting the equity VRP-timing regression to BTC **inverts the sign**. Treat positive VRP as a **harvestable premium with crash beta**, never as a "buy BTC" signal.
- **Payoff is sharply negatively skewed.** Carr-Wu: short-variance IR > 3 but they explicitly warn Sharpe is misleading on a nonlinear payoff; CAPM explains little, Fama-French none. Deribit's cost-inclusive weekend-strangle backtest (Jan 2020–Apr 2025, fees + 5% slippage): +154.8% / 29.7% APR / 81.8% win rate **but 23.8% max DD** with "occasional large losses"; daily pushed DD toward ~45%. **Report short-vol with tail/CVaR, never headline Sharpe.**
- **UI:** keep the existing VRP panel (implied DVOL vs realized) but upgrade the label from descriptive to "risk-premium exposure" with a CVaR/max-DD readout and an explicit "tail-lethal, not a sell button" caveat. Our `RESEARCH.md §2.8` already frames this.

### 2.2 IV term-structure & smile slopes — **DESCRIPTIVE for returns; SIGNAL for realized-vol forecasting / regime**
- Caporin et al. (2024, *Operations Research Letters*, doi:10.1016/j.orl.2024.107135): smile left/right slopes **do NOT predict returns** but **do forecast weekly realized volatility**. Alexander-Imeraj (2021, *J. Alt. Inv.*): VRP/term structure spikes **before** large moves of **either** sign (a |move| signal, direction-agnostic).
- **Verdict:** use term slope (e.g. `IV_30d − IV_180d`, or DVOL contango/backwardation) as a **regime classifier and vol forecaster for sizing**, label it **NOT a return signal**. The ORL result is in-sample/weekly; OOS over 2021–2024 not established; GARCH can beat option-IV for RV forecasting, so slope adds regime info more than a clean edge.

### 2.3 25d risk-reversal / skew — **DESCRIPTIVE (sentiment / positioning)**
- Deribit's own 4-year backtest (Apr 2019–Dec 2022, delta-hedged): **always-short risk-reversal beat always-long and beat a skew-z-score timing rule**. The profit came from a **structural skew/upside-demand carry premium**, not from the z-score's timing — the z-filter *underperformed* the naive carry. BTC skew **changes sign** with sentiment (Liu-Packham-Sepp 2025, arXiv:2510.21297; Kim et al. 2025, *JFM* doi:10.1002/fut.70004: RN skew more negative in bearish regimes).
- **Verdict: descriptive sentiment + a structural carry component, NOT a validated return-timing signal.** Deribit's backtest used mark prices with no slippage (their own caveat) and warns the edge decays as institutions enter. Show RR25(T) and BKM RN-skew as a **sentiment gauge**, clearly labeled.

### 2.4 BKM model-free moments / jump-risk premia — **DESCRIPTIVE (BKM) / RESEARCH-FLAG (jump premia)**
- BKM (2003, RFS) RN variance/skew/kurtosis from the OTM strip — directly portable to Deribit, gives a clean RN-skew time series (descriptive sentiment). Needs a **dense, clean OTM strip**; Deribit's discrete strikes + wide OTM spreads inject truncation/discretization bias.
- Liu-Packham-Sepp (2025): bivariate Hawkes jump-risk premia show predictive power for BTC futures carry and delta-hedged option P&L — **promising but recent, single-paper, in-sample-leaning, Hawkes calibration is fragile**. Flag as backtest-only research; do not present as a live signal. (A separate ML-OOS-after-costs claim could not be traced to a primary source — treat as unconfirmed.)

### 2.5 Implementation discipline (applies to every option signal)
- Net **every** signal of a realistic cost model (see 2.6) before any statistic.
- Evaluate short-vol with **CVaR₅% / max-DD, not Sharpe**.
- For slope/skew, run return-predictability regressions **expecting insignificant coefficients** (negative control) and RV-predictability regressions expecting significance.
- Walk-forward OOS with **regime-block splits** (2020 crash / 2021 bull / 2022 FTX) to expose decay. This mirrors the engine's existing DSR/OOS rails.

### 2.6 Transaction-cost reality (make-or-break, must be modeled)
The ~0.12% headline spread and 0.04% maker/taker fee apply to **liquid near-ATM, near-dated** only. Effective spreads widen sharply for OTM and in stress: Atanasova et al. (2025, *Finance Research Letters* v85): a 1-SD drop in aggregate gamma inventory widens effective spreads ~0.12% (OTM calls) / ~0.19% (OTM puts), and **illiquidity is the first priced factor** in BTC option returns. The **12.5%-of-premium fee cap is brutal for cheap deep-OTM wings**. Variance-swap replication trades the **full OTM strip** → accumulates the widest spreads across many strikes.

```
per-option cost ≈ max(0.0004·underlying_BTC, taker_floor) capped at 0.125·option_price
                  + half-spread(moneyness, tenor, regime: ~0.1–0.2%+ of underlying for OTM, wider in stress)
                  + slippage ~5% of premium for marketable size
```
Do **not** apply the 0.12% ATM average to the OTM wings these strategies actually trade; spreads blow out exactly when you need to exit. Source: support.deribit.com Fees; Atanasova et al.; Deribit weekend-vol backtest.

---

## 3. Live charts — TradingView embed + WebSocket plan

### 3.1 TradingView Advanced Chart widget (free, no key, no license fee; attribution mandatory)

Drop-in for a polished live-ticking price panel. **No account, no API key, no datafeed** — TradingView serves the data. JSON config goes **inside** the script tag body. Needs a fixed-height parent for `autosize`.

```html
<div class="tradingview-widget-container" style="height:520px;width:100%">
  <div class="tradingview-widget-container__widget" style="height:calc(100% - 32px);width:100%"></div>
  <div class="tradingview-widget-copyright">
    <a href="https://www.tradingview.com/" rel="noopener nofollow" target="_blank">
      <span class="blue-text">Track all markets on TradingView</span></a>
  </div>
  <script type="text/javascript"
    src="https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js" async>
  {
    "autosize": true,
    "symbol": "COINBASE:BTCUSD",
    "interval": "60",
    "timezone": "Etc/UTC",
    "theme": "dark",
    "style": "1",
    "locale": "en",
    "allow_symbol_change": true,
    "calendar": false,
    "studies": ["RSI@tv-basicstudies"],
    "support_host": "https://www.tradingview.com"
  }
  </script>
</div>
```
Key params: `symbol`, `interval` (`"1"`,`"5"`,`"60"`,`"D"`,`"W"`), `theme`, `style` (1=candles), `autosize`, `hide_side_toolbar`, `studies[]`. Source: tradingview.com/widget-docs advanced-chart + widget-integration tutorial; IDouble HTML-Crypto snippets.

**Symbols (verify each resolves before relying on it):**
`COINBASE:BTCUSD` (spot) · `BYBIT:BTCUSDT.P` (USDT perp; `.P` = perpetual) · `BINANCE:BTCUSDT` · `BITSTAMP:BTCUSD` · `DERIBIT:BTCUSD` (index) · `CRYPTOCAP:BTC`.

**Constraints:**
- **Attribution is contractually mandatory** on the free widget — keep the copyright link as designed. Removing branding requires a paid agreement.
- The widget is a **sealed cross-origin iframe**: you cannot read its price series or draw your own primitives into it. There is **no supported postMessage API** to inject drawings, and attempting to script across the origin boundary is browser-blocked and violates the no-modification terms.
- **Theme it `dark` to match our chrome**; set `timezone: "Etc/UTC"` so it lines up with our UTC-epoch data.

### 3.2 Theme-matched alternative + on-chart overlays: TradingView Lightweight Charts (Apache-2.0)

For true co-rendering of **our backtest visuals** (entry/exit markers, equity, drawdown bands, stop/target lines) on **one shared time/price scale**, the widget can't do it — use **Lightweight Charts** (Apache-2.0, ~35KB canvas, free for any use including private). This is the **license-safe** choice for a research tool that may be private/behind auth (see 3.6).

```js
const chart = createChart(el, { layout: { background:{color:'#0b0f18'}, textColor:'#d6e0f5' } });
const candles = chart.addCandlestickSeries();
candles.setData(bars);                 // historical OHLC (UTC epoch SECONDS)
candles.update(liveTick);              // live tick from our WS
candles.setMarkers([{ time, position:'belowBar', color:'#36d399', shape:'arrowUp', text:'BUY' }]);
candles.createPriceLine({ price: stop, color:'#f87272', title:'stop' });
chart.addLineSeries();                 // equity / indicator overlay on same scale
```
Reconcile timestamps in **UTC epoch seconds everywhere** to avoid off-by-timezone marker placement. (Our existing `charts.js` SVG primitives remain the dependency-free fallback and stay for the backtest panels; Lightweight Charts is the upgrade where on-chart overlays + live ticks matter.)

### 3.3 WebSocket subscribe/reconnect — shared pattern

WebSockets are **not subject to CORS preflight** (SOP/CORS gate HTTP fetch/XHR, not the WS data channel); public market-data feeds on all three venues accept browser connections with **no Origin allowlist and no keys**. Stay **strictly on public channels** — any authenticated channel needs signing, which can't be done safely in a browser. Reconnect with **capped exponential backoff + jitter** (1s, 2s, 4s … max ~30s), **re-subscribe on every reopen**, and gate the ping/heartbeat timer to the socket lifecycle.

```js
const ws = new WebSocket('wss://advanced-trade-ws.coinbase.com');
ws.onopen = () => ws.send(JSON.stringify({ type:'subscribe', channel:'ticker', product_ids:['BTC-USD'] }));
// identical skeleton across venues: swap URL + subscribe payload + ping rule.
```
Source: securityevaluators WS-not-CORS; dev.to WS-bypass-SOP; oneuptime WS-CORS.

**Per-exchange specifics:**

| Exchange | Endpoint (public, no auth) | Subscribe | Keepalive | Watch out |
|---|---|---|---|---|
| **Coinbase Advanced Trade** | `wss://advanced-trade-ws.coinbase.com` | `{"type":"subscribe","product_ids":["BTC-USD"],"channel":"ticker"}` (product id is `BTC-USD`, dash) | co-subscribe `{"type":"subscribe","channel":"heartbeats"}` (1/s, `heartbeat_counter` detects gaps) | Must subscribe **within 5s** of connect or you're dropped; channels go stale ~60–90s without updates → always run heartbeats. `ticker` is match-driven (cadence varies). Distinct from legacy `ws-feed.exchange.coinbase.com` — don't mix formats. |
| **Bybit v5** | `wss://stream.bybit.com/v5/public/linear` (BTCUSDT **perp** lives here, not `/spot`); also `/spot`, `/inverse` | `{"req_id":"1","op":"subscribe","args":["publicTrade.BTCUSDT","tickers.BTCUSDT"]}` (topic = `TYPE.SYMBOL`) | send `{"op":"ping"}` **every 20s** → `pong` | `tickers` on linear is **snapshot then delta — merge them** (naive full-snapshot consumer shows wrong values). Spot: max 10 args/request; args ≤ 21,000 chars; ≤ 500 new conns / 5 min / domain. **Geoblocked in some jurisdictions** (a US browser may simply fail) → graceful fallback. |
| **Deribit v2** | `wss://www.deribit.com/ws/api/v2` (JSON-RPC 2.0) | `{"jsonrpc":"2.0","id":1,"method":"public/subscribe","params":{"channels":["trades.BTC-PERPETUAL.100ms","ticker.BTC-PERPETUAL.100ms"]}}` | `public/set_heartbeat` (interval ≥ ~10s) **then answer each `test_request` with `public/test`**, or send `public/ping` | Channel = `CHANNEL.INSTRUMENT.INTERVAL` (`raw`/`100ms`/`agg2`); **prefer `100ms`/`agg2` over `raw`** (raw = dozens msg/s). Derivatives only (no spot BTCUSD trades; index via `deribit_price_index.btc_usd`). Ignore old v1 gitbooks paths. |

Source: docs.cdp.coinbase.com WS overview/channels; bybit-exchange.github.io v5 connect/ticker/trade; docs.deribit.com + market-data best-practices.

**Reachability caveats (all in the survey):** no-preflight ≠ always-reachable — Bybit (sometimes Deribit) geoblock some IPs; corporate proxies may block the WSS upgrade; **REST history endpoints DO enforce CORS** and many reject browser fetches (that path, unlike the WS stream, may need a proxy — but we avoid REST history by using the WS tape for the live tail and our cached/paginated OHLCV for history).

### 3.4 Combining widget + our SVG/backtest overlays

The free widget is a sealed iframe → two real options: **(A) parallel/external** — keep the widget as a live price reference and render our backtest visuals in a separate native SVG/canvas layer beside/beneath it, syncing only symbol + time range via config (no pixel-perfect overlap on top of the iframe). **(B) co-rendered** — drop the widget, feed Lightweight Charts the same WS ticks AND our backtest series for true on-chart markers/price-lines on one scale. **Recommendation:** offer the TV widget as a polished live panel for users who want it, and build the actual backtest visualization on Lightweight Charts (or our existing SVG) where we own the coordinate system.

### 3.5 Live vs research framing (do not fake real-time)
The terminal is research/backtest. Live WS ticks are a **context panel** (current price/tape), clearly separated from the **historical, discrete** backtest bars. Never blend a live tick into a backtested series or flash-animate static history (misleading). Keep the persistent "research only / a backtest is not a forecast" disclaimer.

### 3.6 License / key flags
- **Free widget:** fine for public deployment **with attribution**; no key. (Our GH-Pages publish is public — OK.)
- **TradingView Charting Library** (self-hosted, custom datafeed): **public-project-only license**; likely does **not** cover a private/auth'd research tool, and redistributing real-time exchange prices needs a market-data agreement. **Avoid** — not needed.
- **Lightweight Charts:** Apache-2.0, no restriction — **the safe pick** for overlays. No key.
- **All WS feeds above:** public, **no keys**. Anything authenticated is out of scope (no key in a browser).

---

## 4. UI/UX overhaul — concrete, no-build, applies to current `styles.css`

Our current tokens (`--bg`, `--bg-panel`, `--border`, `--fg`, `--muted`, `--accent`, `--up #36d399`, `--down #f87272`, `--mono`) and the `.panel` / `.panel.wide` 2-col grid + `.stats-grid` already follow most of these principles. The changes below tighten accessibility and density without a framework. `charts.js` already reads CSS vars via `cssVar()`, so updating `:root` re-themes the SVG for free.

### 4.1 Semantic up/down — colorblind-safe (lightness + redundant glyph)
~8% of men have red-green CVD. Two stacked fixes: (1) up/down differ in **lightness** (survive grayscale); (2) shift up toward teal-green, down toward vermillion/orange-red; (3) **always** add a redundant non-color cue (▲/▼ + `+`/`−` sign) — WCAG 1.4.1.

```css
:root{
  --up:#26A69A;      /* teal-green, lighter */
  --down:#EF5350;    /* deeper red — differs in LIGHTNESS from up */
  /* strict CVD-safe alt palette (user toggle): --up:#009E73; --down:#D55E00; (Okabe-Ito) */
}
.delta.up::before{ content:'▲ '; }       /* redundant glyph — never color alone */
.delta.down::before{ content:'▼ '; }
.delta.up{ color:var(--up); }  .delta.down{ color:var(--down); }
```
Validate with a CVD simulator (Color Oracle / Chrome DevTools → Rendering → Emulate vision deficiencies). Offer a **strict blue/orange (Okabe-Ito) toggle**. Source: datawrapper colorblindness pt2; Okabe-Ito (easystats/figcanvas); Carbon a11y; WCAG 2.1.

### 4.2 WCAG contrast targets (bake into tokens)
Body text **≥ 4.5:1**; large text (≥24px reg / ≥19px bold) and **all non-text UI** (borders, chart series, focus rings, icons) **≥ 3:1** (WCAG 1.4.3 + 1.4.11). **In a quant terminal almost every number is data → treat numbers as body text (≥4.5:1), not decoration.** Reserve sub-4.5:1 greys strictly for non-data chrome. Charts: each series **≥3:1 vs background**, neighboring categorical colors average **>2:1** vs each other (Carbon). Our current `--muted #7b8db0` carries data labels — **verify it clears 4.5:1 on `--bg-panel`** in WebAIM; darken if not. Source: WCAG 2.1; WebAIM; Carbon.

### 4.3 Tabular numerals (kill jitter, enable column scanning)
Every changing number must use tabular figures so columns align and values don't reflow on update.

```css
.num, .stat .v, table.leaderboard td.num, .price {
  font-variant-numeric: tabular-nums slashed-zero;
  font-feature-settings:'tnum' 1,'zero' 1;
}
```
Our base font is already the `--mono` stack (inherently tabular) — good; this guarantees it for any non-mono labels and adds slashed-zero to disambiguate 0/O. Budget slightly more column width. Source: loke.dev tabular-nums; Material M3 type.

### 4.4 Color system as tokens (≈9 greys + semantic aliases, hand-tuned)
You need more greys than instinct suggests (8–10) and ~9 shades/accent; **define by hand in HSL**, not via runtime `lighten()/darken()`. Map to `:root` custom properties (already our approach) with **semantic alias tokens** layered on raw scale so components reference intent. Don't over-engineer — 1 primary + up/down + warn/info is enough.

```css
:root{
  --g-900:#0b0f18; --g-800:#111726; --g-700:#0e1420; --g-600:#1d2840; --g-500:#23314a;
  --g-400:#4a5a78; --g-300:#7b8db0; --g-200:#b1bac4; --g-100:#c9d1d9; --g-050:#d6e0f5;
  /* semantic aliases (keep current names so existing CSS/JS keeps working): */
  --bg:var(--g-900); --bg-panel:var(--g-800); --bg-panel-2:var(--g-700);
  --border:var(--g-600); --grid:var(--g-500); --muted:var(--g-300); --fg:var(--g-050);
}
```
(The grey ladder is illustrative — tune each step by eye on real panels and re-check contrast.) Source: Refactoring UI color-palette; Carbon; designsystems.com.

### 4.5 Layered dark surfaces (elevation by lightness, not shadow)
Base canvas darkest → panel one step lighter → nested/hover lighter still; **avoid pure `#000`** (halation; no room to go darker; our `#0b0f18` is correct). Separate panels with a **1px ≥3:1 border or a one-step-lighter fill, not drop shadows** (weak in dark mode). **Desaturate large fills**, reserve saturation for small accents/data points. Keep to ~3 surface levels (our `--bg` / `--bg-panel` / `--bg-panel-2` already is this). Source: Carbon dark-theme layering; Refactoring UI; Dark-mode (Wikipedia).

### 4.6 8pt spacing grid + separate type scale
Spacing on multiples of 8 with 4px half-steps; type on its own ~1.2–1.25 modular scale (do not force type onto the 8pt grid).

```css
:root{
  --sp-1:4px; --sp-2:8px; --sp-3:12px; --sp-4:16px; --sp-5:24px; --sp-6:32px; --sp-8:48px;
  --fs-xs:11px; --fs-sm:12px; --fs-base:13px; --fs-lg:16px; --fs-xl:20px; --fs-2xl:28px;
}
```
Dense data tables: base 12–13px, tight padding `var(--sp-1) var(--sp-2)` (our leaderboard `6px 10px` is close). Power-user density is fine, but offer a **comfortable/compact density toggle** rather than hard-coding the densest setting. Source: 8pt-grid (uxplanet/freecodecamp); designsystems.com.

### 4.7 Chart color & encoding
Cap categorical series at ~12–14 (readability degrades well before that); beyond ~7–8, color alone fails → small multiples / direct labeling / series toggling. Plot on the **darkest** surface (`var(--bg)`), not mid-grey panels. Separate touching fills with a 1px `var(--bg)` divider; add **shape markers (circle/square/triangle) as a 2nd channel**; provide the underlying **data table as the accessible fallback**. CVD-aware default order: `#0072B2, #E69F00, #009E73, #D55E00, #56B4E9, #CC79A7, #F0E442`. For equity-curve / multi-asset overlays prefer small multiples or toggling over 10+ colors on one axis. Source: Carbon data-viz; Okabe-Ito.

### 4.8 Panel states — loading / empty / stale / error (per panel)
Each panel is a small state machine: `loading | empty | ready | stale | error`.
- **Loading:** skeleton placeholders matching final shape (`aria-busy="true"`, reduced-motion-safe shimmer); spinners only for <~1s actions; cap skeleton ~5s then show fallback/error.
- **Empty:** explain **why** + the next action (e.g. "No backtest run yet — pick a strategy and Reload").
- **Stale/refresh:** keep the already-rendered numbers visible; **dim to ~0.6 + amber 'stale' chip + 'Updated 3m ago'** — do **not** wipe to a skeleton on background refresh. (We already have the `#stale-banner` + the `loadOHLCV`/derived-OHLC degrade path — generalize it per-panel; the option-chain, VRP, and on-chain panels already degrade to a `.chart-na` message.)
- **Error:** localized, recoverable, with retry.
- Always surface a **"last updated HH:MM:SS"** per data panel and a persistent **calm "research / delayed data"** label (not a blinking alarm — our cadence is slow).
- `aria-live="polite"` to announce refreshes.

```css
.panel.is-stale{ opacity:.6; }
.panel.is-stale .stale-chip{ display:inline-block; background:var(--warn-bg); color:var(--warn-fg);
  font-size:var(--fs-xs); padding:1px 6px; border-radius:3px; }
@media (prefers-reduced-motion: reduce){ *{ animation:none!important; transition:none!important; } }
```
Source: Carbon loading pattern; OpenReplay; Onething; LogRocket; Eleken.

### 4.9 Micro-interactions (genuine value changes only)
Brief flash-on-change for updated cells (green flash if new>prev, red if <, neutral/dim if unchanged), quick background fade ~150–400ms back to neutral, paired with the same ▲/▼ + sign convention (CVD-safe). **Gate all motion behind `prefers-reduced-motion`** and ideally a user toggle. In this research/backtest context flashing applies to **user-triggered recompute** (strategy change, cost knob, filter), **not** a faked live tick on static history. Source: lollypop trading-UI; Webull tick coloring; WCAG.

```css
.tick-up{ animation:flashUp .4s ease-out; } .tick-down{ animation:flashDown .4s ease-out; }
@keyframes flashUp{ from{ background:rgba(38,166,154,.35);} to{ background:transparent;} }
@keyframes flashDown{ from{ background:rgba(239,83,80,.35);} to{ background:transparent;} }
```

### 4.10 Progressive disclosure + keyboard-first power-user affordances
Conceal complexity by default (Bloomberg principle): show essential metrics, reveal advanced params/columns via native `<details>/<summary>`, tabs, or popovers. Dense tables: **sticky `thead` + pinned first column** so labels stay visible while scrolling. Add a small **vanilla-JS command palette** (Cmd/Ctrl+K) for jump-to-panel/strategy and a `?` help overlay listing shortcuts — no framework. Source: Bloomberg conceal-complexity; NN/g + Microsoft progressive disclosure; VS Code command-palette pattern.

```css
table.leaderboard thead th{ position:sticky; top:0; background:var(--bg-panel); z-index:2; }
table.leaderboard tbody td:first-child{ position:sticky; left:0; background:var(--bg-panel); }
```

---

## 5. Build plan — ordered, low-risk

Principle: each step ships a working dashboard, degrades gracefully (stale-banner / `.chart-na`, never fabricate), and adds **no dependency, no key, no build step**. Lightweight Charts is the only new vendored file (Apache-2.0, vendored locally — no CDN, consistent with `vendor/`). Keep the Python engine as the source of truth for annualization-sensitive work.

**Phase 0 — Foundations / UI tokens (no data risk).**
0.1 Refactor `styles.css` `:root` to the §4.4 grey scale + semantic aliases (keep existing variable names so nothing breaks). Add `--sp-*` and `--fs-*` scales.
0.2 Apply §4.1 CVD-safe up/down + ▲/▼ glyph classes; add the strict Okabe-Ito toggle. Add §4.3 `tabular-nums slashed-zero` to all numeric classes.
0.3 Run WebAIM/DevTools contrast audit on every token pair (§4.2); darken `--muted` if data labels fail 4.5:1.
0.4 Add `prefers-reduced-motion` guard + the per-panel `.is-stale` / stale-chip / "updated HH:MM:SS" treatment (§4.8); generalize the existing `#stale-banner`.
*Risk: none (pure CSS/markup). Ship first.*

**Phase 1 — Hourly timeframe, properly wired.**
The Python engine already does hourly correctly (`run_backtest.py --granularity 1h`, `_periods_per_year` = `24*365`, paginated Coinbase 1h via `_COINBASE_GRAN_SECONDS["1h"]=3600`). The dashboard hourly selector was **removed** to avoid annualization-threading bugs.
1.1 In `app.js`, thread a single `periodsPerYear` constant from a timeframe selector (`1d`→365, `1h`→8760) into **every** `Q.backtest(...)` call (currently hard-coded `periodsPerYear: 365` in both `runStrategy` and `renderLeaderboard`) **and** into `Q.realizedVol`/`Q.rollingSharpe` annualization. Re-paginate `coinbaseCandles` at `gran=3600` for 1h (raise the 16-window guard — 1h needs many more windows for multi-month history).
1.2 Add a guard: if any annualization site still uses a literal 365 while timeframe=1h, fail loud in `--check`. Add a JS self-test mirroring the Python `_periods_per_year`.
1.3 Keep the "use the Python engine for serious hourly" hint, but the dashboard 1h selector now works for recent windows.
*Risk: medium (annualization threading is the exact bug that caused the prior removal). Gate behind tests; do it before option work so the timeframe plumbing is solid.*

**Phase 2 — Deribit option-chain data layer (read-only, public).**
2.1 `app.js`: `loadOptionChain()` → one `get_book_summary_by_currency?currency=BTC&kind=option` call; group by `underlying_index`; parse expiries from `instrument_name`. Mirror in Python `data.get_option_chain()` for offline/`scan.py`.
2.2 Per expiry: synthetic forward via parity (§1.3) with `underlying_price` fast-path; **divide all `*_iv` by 100** with the live ATM repricing sanity-check (§1.2); ACT/365 `T` to 08:00 UTC (§1.5).
2.3 Apply the §1.7 quality gate (null bid/ask, spread, OI, |delta|≥0.05, OTM-only) — make thresholds visible/adjustable; show the bid/ask-IV gap so users see mark-IV's hidden interpolation.
2.4 Lazy `ticker` fetch **only** for gated contracts that need greeks/bid-ask-IV (rate-limit aware, throttle like the existing 120ms pacing); never fetch the whole universe per call.
*Risk: low-medium (Deribit REST CORS is best-effort — degrade to `.chart-na` exactly like the current VRP panel).*

**Phase 3 — Option-chain panels + honest signals.**
3.1 Smile/skew panel per selected expiry (PCHIP fit; OTM-only; delta or log-moneyness toggle) — `charts.js` `lineChart` suffices initially.
3.2 ATM term-structure panel (total-variance interpolation, §1.4b) + overlay our interpolated 30d ATMF vs the existing DVOL line (expect DVOL above by the BF premium — annotate it, §1.6).
3.3 RR25 / BF25 readouts + RR25(T) term-of-skew, with the **documented sign convention** (§1.4d-e) and the Deribit-delta caveat.
3.4 Upgrade the existing VRP panel label to "risk-premium exposure," add **CVaR/max-DD** readout, keep the "tail-lethal, not a sell button" caveat (§2.1). Tag every option panel **SIGNAL vs DESCRIPTIVE** per §2 (RR/skew = descriptive sentiment; term/slope = vol-forecast/regime; VRP = risk-premium signal; BKM/jump = research-flag).
3.5 (Optional, research-flag) SVI/SSVI surface + butterfly/calendar no-arb checks (§1.4c, §1.7) — Python-side first (scipy), surfaced as a static image/JSON like the existing tearsheet; explicitly labeled research-grade.
*Risk: low (additive panels; reuse existing degrade pattern).*

**Phase 4 — Live charts.**
4.1 Add the TradingView Advanced Chart widget panel (§3.1), `theme:dark`, `timezone:Etc/UTC`, **keep mandatory attribution**, `allow_symbol_change` across the verified symbol list. No key/license fee.
4.2 Vendor **Lightweight Charts** (Apache-2.0) locally into `vendor/`; build a "live BTC + backtest overlay" panel (§3.2) reusing our CSS vars — entry/exit markers, equity overlay, stop/target lines on one UTC-seconds scale. This is the license-safe overlay path.
4.3 Add a `WS` module: shared reconnect/backoff+jitter + re-subscribe skeleton (§3.3), one adapter each for Coinbase / Bybit-linear / Deribit (subscribe payload + ping rule from the §3.3 table). Default to Coinbase (no geoblock); Bybit/Deribit selectable with graceful "geo/proxy unreachable" fallback. **Public channels only — no keys, no signing.**
4.4 Keep live ticks visually separated from backtest bars; never blend or fake real-time on history (§3.5).
*Risk: low-medium (WS reachability/geo — already have the degrade UX). No keys anywhere.*

**Phase 5 — Power-user UX + verification.**
5.1 Command palette (Cmd/Ctrl+K), sticky table headers/pinned first column, `<details>` for advanced option-chain filters, `?` shortcut overlay (§4.10).
5.2 Density toggle (comfortable/compact, §4.6).
5.3 Extend `--check`/tests: annualization-threading guard (Phase 1), `*_iv`/100 repricing check, parity-forward sanity, no-arb checks for any SVI surface, and a "no panel fabricates data on source failure" assertion. Mirror the existing 14-pytest discipline.

### License / key flags (summary — and how we avoid them)
- **No API keys anywhere.** All Deribit/Coinbase/Bybit endpoints used are **public/unauthenticated**; all WS channels are **public**. No order, no signing, no authenticated endpoint — consistent with `DISCLAIMER.md`.
- **TradingView widget:** free, no key; **attribution link is mandatory — keep it.**
- **TradingView Charting Library:** **avoided** — public-project-only license + market-data agreement; not needed.
- **Lightweight Charts:** Apache-2.0, no restriction, **vendored locally** (no CDN) — the safe overlay choice.
- **REST history CORS:** avoided by using the WS tape for the live tail + our existing cached/paginated OHLCV for history; if a venue's REST blocks the browser, degrade rather than proxy.

### Honesty rails carried into every new panel
Net-of-cost (use the §2.6 OTM-aware cost model for options, not the 0.12% ATM average), OOS + regime-block walk-forward, deflated Sharpe, buy-and-hold baseline, CVaR/max-DD (not Sharpe) for short-vol. Label each option signal SIGNAL vs DESCRIPTIVE. A backtested Sharpe > 2 in crypto is a red flag; > 3 is almost certainly an artifact. The genuine, multi-source-confirmed edge here is **risk management + the VRP risk-premium exposure**, not directional alpha — present the tool to demonstrate that skepticism, as it already does.
```
