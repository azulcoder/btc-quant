# DESIGN — Orderflow Terminal (CryExc-inspired) + tick collector

Status: **O-0 + O-1 shipped 2026-07-03 (`8c2781f`); O-2 + verification system + O-3
shipped 2026-07-04** (§4b heatmaps/OKX; §7 three-layer verification incl. deterministic
browser replay; §4c structure views — TPO, kline VP, historical chart + no-peek bar
replay, **BYOD tick replay from the collector store (verified end-to-end against a real
8 h recording)**, cross-venue funding, macro proxies). O-4/O-5 specced and deferred —
same greenlight discipline as DEVELOPMENT.md §6.

Provenance: feature surface adapted from [Cryexc](https://cryexc.josedonato.com/) (José
Donato's free orderflow terminal — footprint, DOM, heatmaps, TPO, whale/options flow;
browser-only, keyless WS to public exchange feeds) and its self-host history store
[cryexc-history](https://github.com/jose-donato/cryexc-history) (Go + DuckDB, trades +
liquidations, BYOD HTTP protocol). We adapt the *capabilities* into btc-quant's stack
(vanilla JS + canvas, no bundler; Python + DuckDB) — we do not port C++/WASM.

## 0. Honesty rails (non-negotiable, inherited + extended)

1. **Everything in the terminal is LIVE-DESCRIPTIVE.** No terminal series is ever merged
   into a backtested series or the OOS harness (same rail as the existing live tape,
   app.js §3.5). The terminal page carries a permanent "research context — descriptive
   only, never a backtest input" banner.
2. **Keyless only.** Public WS/REST, no signing, no accounts. Connectivity of every
   endpoint verified from this machine 2026-07-03 (all REST reachable). **Empirical
   constraint found during frame capture:** Binance Futures WS **topic-filters** this
   network — `depth20@100ms` flows (112 frames/12s) while `aggTrade`/`markPrice`/`ticker`
   on the *same socket, same subscribe* deliver zero frames (sub-ack only); REST
   `fapi/v1/aggTrades|premiumIndex|openInterest` all work. Design therefore uses **Bybit
   v5 as the primary WS feed** (publicTrade/orderbook.50/tickers/allLiquidation all
   verified live), Binance for depth WS + REST polls. We state what the wire actually
   delivers rather than pretending the documented stream list is available.
3. **The collector changes data-family status from *un-ingested* to *time-gated*, not to
   *validated*.** Tick CVD / liquidations / OI / funding accrual start accumulating at
   first collector run. They may enter the OOS harness ONLY after (a) accumulated history
   clears MinBTL for the intended trial count, and (b) a pre-registered hypothesis with a
   kill criterion (net-of-cost OOS DSR > 0.95 AND beats baseline) — DEVELOPMENT.md §6
   greenlight required. Until then they are terminal panels, nothing more.
4. **Model estimates are labeled as estimates.** The liquidation heatmap (O-2) infers
   cascade levels from leverage assumptions — it is a *model*, labeled "estimated", never
   presented as observed data. Same class as the existing max_pain/gamma positioning reads.
5. **Signed dealer GEX stays refused** (dealer sign unknowable keyless — options run-log).
   The options widget extensions (O-4) keep the unsigned Σ|gamma|·OI convention.
6. **Aggressor-side conventions are per-exchange and MUST be normalized explicitly:**
   - Coinbase `market_trades.side` is the **MAKER** side → aggressor = the inverse
     (DEVELOPMENT.md §5 gotcha — the tape coloring inverts if read as aggressor).
   - Binance `aggTrade.m` (isBuyerMaker) `true` → **SELL** aggressor.
   - Bybit v5 `publicTrade` `S` (`Buy`/`Sell`) is already the **taker** side → use as-is.
   Every adapter documents its convention inline and the fixture test asserts it.
7. **No fabricated history.** The terminal renders only what arrived over the wire this
   session (plus what the local collector genuinely recorded). No backfill from mixed
   sources into one series without an explicit per-source label.

## 1. Architecture

```
                    ┌──────────────────────────────────────────────┐
  Binance Futures ─▶│  browser terminal (dashboard/terminal.html)  │
  Bybit v5 linear ─▶│  adapters → stores → canvas views (rAF)      │
  Coinbase spot   ─▶│  keyless WS, session-local state             │
                    └──────────────────────────────────────────────┘
                    ┌──────────────────────────────────────────────┐
  same feeds  ────▶ │  collector daemon (btcquant/collector.py)    │
                    │  asyncio WS → batched DuckDB inserts         │
                    │  + BYOD HTTP API (stdlib) for later replay   │
                    └──────────────────────────────────────────────┘
                         data/ticks.duckdb  (keep-all by default)
```

Browser terminal and collector are independent: the terminal works with zero setup
(CryExc "demo mode" equivalent); the collector is the opt-in history store (CryExc
"self-hosted" equivalent) whose value is *research optionality*, not the UI.

## 2. Data-source matrix (all keyless, verified reachable)

| Source | Transport | Streams (BTC) | Feeds | Verified |
|---|---|---|---|---|
| **Bybit v5 linear** (`stream.bybit.com/v5/public/linear`) — **PRIMARY** | WS | `publicTrade.BTCUSDT`, `orderbook.50.BTCUSDT`, `tickers.BTCUSDT` (mark/index/funding/**OI**), `allLiquidation.BTCUSDT` | tape, footprint, CVD, VP, DOM, book, header stats, **liquidations** | live frames captured ✓ |
| Binance Futures (`fstream.binance.com`) | WS | `btcusdt@depth20@100ms` **only** (trades/mark topic-filtered on this network, §0.2) | second agg-book leg | live frames captured ✓ |
| Binance Futures (`fapi.binance.com`) | REST poll | `/fapi/v1/premiumIndex` (5 s), `/fapi/v1/openInterest` (60 s) | cross-exchange funding/OI columns | responses captured ✓ |
| Coinbase Adv. Trade (`advanced-trade-ws.coinbase.com`) | WS | `market_trades`, `ticker`, `heartbeats` | spot tape (conventions proven in app.js) | live frames captured ✓ |
| OKX (`ws.okx.com:8443/ws/v5/public`) | WS (O-2+) | `trades`, `books` (BTC-USDT-SWAP) | deeper agg book | REST reachable ✓ |
| Deribit / Hyperliquid / Polymarket / Tree-of-Alpha | REST/WS (O-4/O-5) | — | options ext, whale tracking, prediction, news | REST reachable ✓ |

Captured real frames live in `scripts/fixtures_ws.json` (trimmed) — adapters and the
fixture smoke are written against **actual wire shapes**, not remembered docs. Notable
realities encoded there: Bybit `tickers` sends a full `snapshot` then **partial deltas**
(only changed fields — the adapter must merge); Bybit `orderbook.50` sends `snapshot`
then `delta` frames (qty `"0"` deletes a level); Binance `depth20` frames are each a
full 20-level snapshot; Coinbase `market_trades` arrives as `snapshot` then `update`
batches and its `side` is the **maker** side.

## 3. Collector spec (O-0) — `btcquant/collector.py`

- **Deps:** `duckdb>=1.0`, `websockets>=12` via `requirements-collector.txt` (opt-in, like
  MLflow/DVC — guarded import with an actionable install hint; core `make test` never
  needs them beyond the guarded skip).
- **DB:** `data/ticks.duckdb` (gitignored). Single writer process. Batched inserts
  (flush every 500 ms or 500 rows, whichever first). Graceful shutdown flush on SIGINT.
- **Streams (empirically grounded, §2):** Bybit WS `publicTrade` → trades,
  `allLiquidation` → liquidations, `orderbook.50` → depth (1/s downsample), `tickers` →
  funding_mark + open_interest (merge partial deltas against the last snapshot!);
  Binance WS `depth20@100ms` → depth (1/s downsample); Binance REST `premiumIndex` (5 s)
  → funding_mark, `openInterest` (60 s) → open_interest; Coinbase WS `market_trades` →
  trades (spot leg, maker-side inversion). Binance futures *trades* are NOT collected —
  topic-filtered on this network (§0.2); documented, not proxied.
- **Schema** (all timestamps epoch **ms**, UTC; `exchange` short code `binancef|bybit|coinbase`):
  - `trades(exchange VARCHAR, symbol VARCHAR, trade_id VARCHAR, ts_ms BIGINT, price DOUBLE, qty DOUBLE, aggressor_buy BOOLEAN)` — trade_id VARCHAR (Bybit ids are UUIDs).
  - `liquidations(exchange, symbol, ts_ms BIGINT, side VARCHAR, price DOUBLE, qty DOUBLE, notional_usd DOUBLE)` — side = the *liquidated* position (`long|short`), normalized per exchange (Bybit `allLiquidation` side `Buy` = a **short** was liquidated — the printed order is the forced *buy-back*).
  - `depth_snapshots(exchange, symbol, ts_ms BIGINT, bids VARCHAR, asks VARCHAR)` — JSON `[[price,qty]…]`, top-20, downsampled to 1/s (the 100ms firehose is a UI concern, not a storage one).
  - `funding_mark(exchange, symbol, ts_ms BIGINT, mark DOUBLE, index DOUBLE, funding_rate DOUBLE, next_funding_ts BIGINT)` — downsampled to 1/s.
  - `open_interest(exchange, symbol, ts_ms BIGINT, oi DOUBLE)` — 60 s REST poll.
  - Indexes on `(symbol, ts_ms)` per table.
- **Retention: keep-all by default** — the whole point is accumulating research history
  (unlike cryexc-history's 24 h cap). `--retention-days N` optional. Honest sizing note:
  BTC perp trades ≈ 0.5–1.5 M rows/day → order-of ~0.5–1 GB/month in DuckDB; depth\@1s
  adds ~0.1 GB/month. Disk is the user's budget; documented, not hidden.
- **Resilience:** per-stream reconnect with capped exponential backoff + jitter (mirror of
  dashboard `makeSocket` semantics); a stalled-stream watchdog (no frame > 60 s → force
  reconnect); gaps are **left as gaps** (no interpolation — honesty rail; cryexc-history
  has the same known limitation and says so).
- **BYOD HTTP API** (stdlib `http.server`, thread in same process, off by default,
  `--api-port 8788` to enable): `GET /health`, `/v1/info`, `/v1/trades`,
  `/v1/liquidations`, `/v1/funding`, `/v1/oi`, `/v1/depth` with
  `symbol,start_ms,end_ms,limit` params, JSON out. Read via a `threading.Lock`-serialized
  cursor (single-process; no cross-process readers while the daemon owns the file).
- **CLI:** `scripts/run_collector.py --symbol BTCUSDT --exchanges binancef,bybit
  [--api-port 8788] [--db data/ticks.duckdb] [--retention-days N]`.
  Make targets: `make collector`, `make collector-api`.
- **Tests (no network):** pure normalization functions (`normalize_binance_aggtrade`,
  `normalize_binance_forceorder`, `normalize_bybit_trade`, `normalize_bybit_liq`,
  `normalize_bybit_ticker`) against recorded fixture frames; schema create + insert/query
  roundtrip on a temp DB; API handler against a seeded temp DB. Skips cleanly (`pytest
  importorskip`) when collector deps absent.

## 4. Terminal spec (O-1) — files & module contracts

No bundler; plain `<script>` IIFEs exposing ONE global each (matches app.js style).
Load order: `vendor/lightweight-charts.js` → `livewire.js` → `terminal-adapters.js` →
`terminal-state.js` → `terminal-views.js` → `terminal.js`.

- **`dashboard/livewire.js`** — `window.BTCQ_LIVEWIRE = { makeSocket }`: the existing
  `makeSocket(adapter, api)` (reconnect/backoff/watchdog/`markAlive`) **extracted verbatim
  from app.js** so both pages share one implementation. app.js keeps a
  `const makeSocket = window.BTCQ_LIVEWIRE.makeSocket;` shim; `index.html` loads
  livewire.js before app.js. Behavior change: none (verified by `node --check` + manual
  dashboard smoke).
- **`dashboard/terminal-adapters.js`** — `window.BTCQ_TERMINAL_ADAPTERS`:
  `makeBybitAdapter(sym, sink)` (primary: trade/book/tickers/liq; merges `tickers`
  partial deltas), `makeBinanceDepthAdapter(sym, sink)` (depth20 snapshots only),
  `makeCoinbaseAdapter(productId, sink)` (spot tape; maker-side inversion), each
  `makeSocket`-compatible; plus `makeBinanceRestPoller(sym, sink, opts)` →
  `{start(), stop()}` polling `premiumIndex` (5 s) / `openInterest` (60 s) via fetch.
  `sink` receives **normalized events** (the only shapes stores ever see):
  ```js
  { kind:'trade', ex, ts, price, qty, aggressorBuy, id }
  { kind:'depth', ex, ts, bids:[[p,q]…], asks:[[p,q]…], isSnapshot }   // sorted best-first
  { kind:'liq',   ex, ts, side:'long'|'short', price, qty, notionalUsd }
  { kind:'mark',  ex, ts, mark, index, fundingRate, nextFundingTs }
  { kind:'oi',    ex, ts, oi }
  ```
  Aggressor conventions per rail §0.6, documented inline per adapter. Bybit
  `orderbook.50` delta frames are applied against the last snapshot (qty 0 = remove);
  Binance `depth20@100ms` frames are full snapshots (`isSnapshot:true` each frame).
- **`dashboard/terminal-state.js`** — `window.BTCQ_TERMINAL_STATE`: pure state, zero DOM.
  - `TapeStore(max)` — ring buffer; `push(trade)`, `filtered(minNotional)`.
  - `BookStore()` — per-exchange L2: `applyDepth(ev)`, `best()`, `grouped(tickSize, nLevels)`.
  - `AggBookStore(exs)` — merged price-level view across BookStores with per-exchange
    breakdown per level; `grouped(tickSize, nLevels)` → `{price, total, byEx:{…}}[]`.
  - `FootprintStore({barMs, tickSize})` — per-bar map priceLevel → `{buyVol, sellVol}` +
    per-bar `{o,h,l,c, delta, totalVol}`; finished-bar ring (last 120 bars);
    diagonal imbalance flags (`buy[i] ≥ k·sell[i+1]`, default k=3, ≥min volume);
    `onTrade`, `bars()`, `current()`.
  - `CvdStore({bucketsUsd:[1e4,1e5,1e6]})` — cumulative delta overall + per notional
    bucket (CryExc's CVD-by-trade-size); session-anchored; `onTrade`, `series()`.
  - `ProfileStore({tickSize})` — session volume-at-price → POC / VAH / VAL (70% value
    area, standard expansion-from-POC), HVN/LVN candidates; `onTrade`, `profile()`.
  - `LiqStore(max)` — ring of normalized liqs + rolling 1 m/5 m notional sums.
  - All stores unit-testable in Node (no `window` reference) — consumed by the fixture
    smoke `scripts/check_terminal.cjs`.
- **`dashboard/terminal-views.js`** — `window.BTCQ_TERMINAL_VIEWS`: canvas/DOM renderers,
  each `{ mount(el, opts), render(stateSlice) }`, dirty-flag friendly:
  `FootprintView` (canvas: bid×ask cells per price per bar, delta row, volume row,
  imbalance highlights, session-VP gutter, CVD subchart via lightweight-charts),
  `DomLadderView` (table: grouped bid/ask qty + session sold/bought at level + delta),
  `TapeView` (list, min-notional filter, aggressor coloring, whale row emphasis ≥ $250k),
  `AggBookView` (horizontal depth bars w/ per-exchange stacking + cumulative depth curve),
  `HeaderStatsView` (mark, index, basis bp, funding + countdown, OI, 24h realized range),
  `LiqFeedView` (recent liquidation prints w/ side + notional).
  Colors reuse styles.css tokens; CVD-safe palette respected (no new hues outside tokens).
- **`dashboard/terminal.js`** — bootstrap: instantiate adapters (**Bybit primary**;
  Binance depth + REST poller and Coinbase tape as secondary legs), fan events into
  stores, one rAF loop with per-view dirty
  flags (no per-frame full redraw), per-exchange status chips (reuse `conn-status`
  semantics: live/stale/reconnecting), settings row (tick grouping, footprint bar interval
  1m|5m, tape min-notional, CVD buckets), honesty banner, link back to the analytics page.
- **`dashboard/terminal.html` + `terminal.css`** — same shell/status-bar/toolbar/font
  conventions as index.html (links styles.css THEN terminal.css). index.html header gains
  a small `Terminal →` nav link (mirror of the 404 page's pattern on cryexc).
- **Fixture smoke:** `scripts/check_terminal.cjs` (node, follows `_parity_eval.cjs`
  pattern): loads adapters+stores in a `vm` sandbox with stub `window`, replays recorded
  fixture frames (Binance aggTrade/depth/forceOrder/markPrice, Bybit trade/book/liq/ticker,
  Coinbase market_trades), asserts: aggressor normalization per §0.6 (incl. the Coinbase
  maker-inversion), book best-bid/ask after snapshot+delta, footprint bar delta = Σsigned,
  CVD bucket sums = total, VP value-area ∈ [session low, high], agg book merge math.

## 4b. O-2 contracts — heatmaps + OKX leg (binding, same style as §4)

Rails first: spoof/iceberg detection is a **heuristic** (labeled on every emitted event and
on the panel); the liquidation heatmap is a **model estimate** (§0.4 — volume-weighted
entry proxy × standard leverage tiers; the true tier mix is unknowable keyless and the
panel says so); the footprint/session-VP stay **single-venue Bybit** (§0.7 — no mixed-venue
bars), while CVD gains **per-exchange, per-labeled** series (bybit/okx/coinbase).

- **OKX adapter** (`makeOkxAdapter(instId, sink, {ctVal})` in terminal-adapters.js):
  `wss://ws.okx.com:8443/ws/v5/public`, subscribe `books` + `trades` for BTC-USDT-SWAP;
  text `'ping'` keepalive (~25 s). **Sizes are in CONTRACTS: qty = sz × ctVal (0.01 BTC —
  verified via `/api/v5/public/instruments`, pinned in fixtures `_okx_ctval_note`).**
  trades: `side` is the taker → aggressorBuy = side==='buy'. books: `action` snapshot |
  update; update level sz `"0"` deletes; `checksum`/`seqId` ignored (comment why — we
  re-snapshot on reconnect rather than verify checksums). ex code `'okx'`.
- **Bybit book upgrade:** `orderbook.50` → `orderbook.200` (same code path — snapshot/
  delta/tombstones already store-side; real 200-level frames in fixtures). Deeper book =
  usable heatmap range; the DOM ladder just keeps reading `grouped()`.
- **`DepthHistoryStore({tickSize, maxSamples=3600, nLevels=40})`** (terminal-state.js,
  pure): `sample(ts, bookStore)` — caller gates to ≤1/s per exchange; ring of
  `{ts, bids:Map bucket→qty, asks:Map bucket→qty}` (grouped, top-N per side);
  `samples()`, `priceRange()`, `velocity(bucket, windowMs)` = Δqty/Δs at a level.
  Memory-bounded by construction (3600 × 2×40 levels).
- **`SpoofIcebergDetector({wallK=8, wallWindowMs=15000, tradeCoverPct=0.2, icebergM=3,
  icebergWindowMs=60000, minQty})`** (terminal-state.js, pure, every event
  `label:'heuristic'`): consumes `onDepthSample(ts, grouped)` + `onTrade(t)`.
  *Spoof-pull:* a level ≥ wallK × median level size that vanishes within wallWindowMs
  with traded volume at that bucket < tradeCoverPct × wall size → `{kind:'spoof-pull',
  ts, price, size, lifetimeMs, label:'heuristic'}`. *Iceberg-refill:* traded volume at a
  bucket within icebergWindowMs ≥ icebergM × max displayed size there →
  `{kind:'iceberg-refill', …}`. `events()` ring (100). Honest comment: these are
  *patterns consistent with* spoofing/icebergs, not proof — intent is unobservable.
- **`LiqHeatmapModel({tiers=[5,10,25,50,100], mmr=0.005, tickSize})`** (terminal-state.js,
  pure, output `label:'estimated'`): inputs = ProfileStore volume-at-price (entry proxy)
  + current mark + LiqStore prints. For each entry bucket (weight ∝ volume) × tier L:
  long-liq ≈ entry·(1 − 1/L + mmr), short-liq ≈ entry·(1 + 1/L − mmr); tiers weighted
  EQUALLY (stated model assumption — the real tier mix is unknowable keyless).
  `estimate(mark)` → `{bands:[{price, weight, side}], observed:[liq prints], label}`.
  Observed prints are rendered distinctly from estimates — never blended.
- **Views** (terminal-views.js): `BookHeatmapView` (canvas: X=session time, Y=price,
  alpha ∝ resting qty from DepthHistoryStore, last-price polyline overlay, optional
  velocity tint, wall/spoof markers; DPR-aware), `LiqHeatmapView` (canvas: estimated
  long bands below / short bands above mark + observed dots; permanent "ESTIMATED
  (model)" badge in the panel chrome), `DetectionFeedView` (spoof/iceberg event list,
  per-row "heuristic" badge). CVD panel: one line per exchange + overall (labeled).
- **Bootstrap** (terminal.js): OKX adapter → AggBook + per-exchange CvdStore; OKX trades
  do NOT feed FootprintStore/ProfileStore (§0.7). Depth sampler: on rAF, sample each
  exchange's BookStore into its DepthHistoryStore when the latest depth event ts advanced
  ≥1 s (event-ts gated — stores stay Date.now()-free). LiqHeatmapModel re-estimates on a
  5 s dirty flag. New panels join the grid: heatmap (wide, under footprint), liq-heatmap +
  detections (right column).
- **check_terminal.cjs additions:** OKX trade ctVal math (sz 200 → 2.00 BTC), OKX books
  snapshot+update through BookStore (incl. a delete), bybit orderbook200 snapshot sanity,
  DepthHistoryStore ring + velocity sign, SpoofIcebergDetector fires on a constructed
  pull + refill and stays quiet on a benign book, LiqHeatmapModel band math for a known
  (entry, L, mmr) → exact price, and `label` fields present on every heuristic/model output.

## 4c. O-3 contracts — structure views (binding, same style as §4/§4b)

Empirical data map (probed 2026-07-04, responses pinned in fixtures `_o3_notes` +
`bybit_rest_kline` / `okx_rest_funding` / `okx_rest_oi`):
- **Bybit REST klines** work (linear BTCUSDT/ETHUSDT/PAXGUSDT): list is **NEWEST-FIRST**
  `[startMs,o,h,l,c,vol,turnover]` strings — reverse for chronological (gotcha).
- **OKX REST** funding-rate + open-interest work (8 h funding interval).
- **Hyperliquid**: main-universe `SPX` is the **SPX6900 memecoin (~$0.37), NOT the index**
  — never label it macro. HIP-3 dexs carry real index/commodity perps (`km:US500`,
  `km:USTECH`, `km:GOLD`, `km:USOIL`, `xyz:XYZ100`) with **live `allMids` only** —
  `candleSnapshot` for HIP-3 returns empty/500 keyless → **no history**; macro history
  legs therefore use **PAXG** (tokenized gold, Bybit klines ✓) and ETH; HIP-3 legs get
  **session-correlation** built from polled mids (labeled `session · n=…`). `km:GOLD`
  trades ~4% rich vs PAXG/xyz:GOLD — the tracking-error caveat is mandatory panel text.
- **stooq is NOT keyless-scriptable** (JS challenge) — dropped, stated. **No CME feeds.**

Modules:
- **`dashboard/terminal-hist.js`** (new; pure REST fetchers + normalizers, dual-export):
  `fetchBybitKlines(sym, interval, limit)` → chronological `[{ts,o,h,l,c,v}]` (Number()ed,
  reversed); `fetchOkxFunding(instId)` → `{fundingRate, nextFundingTs, intervalH}`;
  `fetchOkxOi(instId)` → `{oi, oiUsd, ts}`; `fetchHlMids(dex)` (POST allMids) →
  `{name→Number(mid)}`. Each has a pure `normalize*` taking the parsed JSON (fixture-
  tested) + a thin fetch wrapper (AbortController 10 s, silent-null on failure — comment).
- **terminal-state.js additions (pure):**
  `buildTpo(bars, {tickSize, periodMs=1.8e6})` → per-UTC-day sessions
  `[{date, rows:[{price, periods:[i…]}], poc, vah, val, singles:[price…], ib:{hi,lo}}]`
  — classical 30-min-bar TPO construction (each bar marks its full H-L range; that IS
  the canonical method, comment it); value area = same 70 % expansion as ProfileStore.
  `buildKlineVp(bars, {tickSize})` → `{levels, poc, vah, val, hvns, lvns}` distributing
  each bar's volume **uniformly across its H-L range** — LABELED `bar-range
  approximation` (tick-accurate VP = footprint gutter / collector); HVN/LVN as local
  extrema vs median with min-prominence. `rollingCorr(retsA, retsB, window)` → aligned
  Pearson series (NaN-safe). `SessionSeriesStore({sampleMs=60000})` — `onSample(ts, key,
  px)` (gated ≥sampleMs per key), `returns(key)`, `corr(keyA, keyB)` → `{r, n}`.
- **terminal-replay.js — BYOD mode:** `?replay=byod[&api=http://127.0.0.1:8788][&from=…
  &to=…&speed=60]` fetches `/v1/trades|depth|liquidations|funding|oi` for the window
  from the collector's BYOD API, merge-sorts by ts, and feeds the **sink directly**
  with normalized events (BYOD rows are already normalized — adapters are bypassed,
  comment why). Chips + banner read `replay (byod)` — your own recorded ticks, clearly
  not live. `drive(name, adapter, api, sink)` gains the optional 4th arg (fixture mode
  ignores it). Honest failure: API unreachable → chips 'error', banner explains
  `make collector-api`.
- **Views** (terminal-views.js) + layout (terminal.html/css — new STRUCTURE section
  between CVD and settings):
  `HistChartView` (wide): Bybit klines candlestick+volume (lightweight-charts),
  interval 5m/30m/1h/4h/1d, SMA 20/50/200 + Heikin-Ashi toggles (math from quant.js —
  never reimplement §house-rule), **bar replay**: play/pause/step/speed/scrub rendering
  ONLY bars ≤ cursor (no peeking), 'REPLAY (historical bars)' flag while scrubbing;
  source label `bybit linear klines`.
  `TpoView`: letter profile per session, POC/VAH/VAL lines, single prints highlighted,
  IB bracket, session selector (last 5 UTC days), label `kline-range TPO · 30 m`.
  `KlineVpView`: composite VP over the chart lookback + HVN/LVN + extension lines of
  untested levels; permanent `bar-range approximation` badge.
  `FundingArbView`: venue table (bybit WS · binancef REST · okx REST 60 s poll):
  mark / funding % / **annualized** (rate × 8760/intervalH) / next-funding countdown /
  OI coin+USD; spread row (max−min annualized bp); note: `descriptive only — carry
  remains off-board (B3)`.
  `MacroView`: live mids strip (km:US500 *(scaled contract — % only)*, km:USTECH,
  km:GOLD, km:USOIL, xyz:XYZ100, PAXG, ETH) with session-% vs BTC; correlation block:
  BTC×ETH×PAXG rolling corr from 1 h klines (7 d window) + session-corr cells for HIP-3
  legs once n≥30, every cell labeled window/n; caveat text: on-chain proxies, tracking
  error, no CME feeds.
- **check_terminal.cjs additions:** kline normalizer reversal (exact fixture numbers),
  buildTpo on constructed bars (POC/VA/singles/IB exact), buildKlineVp distribution sum
  ≡ Σvol + approximation label present, rollingCorr(±identical)=±1, OKX funding/OI
  normalizers vs fixtures, BYOD row→event mapping, HL mids normalizer (memecoin-SPX
  guard: main-universe SPX must NOT appear in macro keys).

## 5. CryExc → btc-quant feature map & phase plan

| # | CryExc view | Phase | Rail notes |
|---|---|---|---|
| 1 | Tape feed (size filter) | **O-1** | aggressor conventions §0.6 |
| 2 | DOM / ladder | **O-1** | |
| 3 | Footprint (multi-ex CVD subplots, size buckets) | **O-1** (Binance leg; multi-ex O-2) | |
| 4 | Multi-source aggregated orderbook | **O-1** (binancef+bybit+coinbase; +OKX O-2) | |
| 5 | Session volume profile (POC/VAH/VAL) | **O-1** (gutter) → full panel O-3 | |
| 6 | Header market stats (mark/funding/OI/basis) | **O-1** | |
| 7 | Liquidation feed | **O-1** (feed) → heatmap O-2 | |
| 8 | Orderbook heatmap (historical DOM) | O-2 | session ring buffer, browser-side |
| 9 | Live heatmap w/ spoof & iceberg detection | O-2 | detection = **heuristic**, labeled |
| 10 | Liquidation heatmap (cascade levels) | O-2 | **estimated — model, labeled** (§0.4) |
| 11 | Market Profile / TPO | O-3 | |
| 12 | Volume profile panel (HVN/LVN, extensions) | O-3 | |
| 13 | Historical charts + bar replay | O-3 | Binance klines REST, per-source labeled |
| 14 | Funding-rate arb (cross-exchange, annualized, OI) | O-3 | descriptive; carry stays off-board |
| 15 | Market correlation (macro) | O-3 | HL HIP-3 pairs; **no CME feeds** (honest) |
| 16 | VWAP screener (bubble) | O-4 | multi-symbol = Binance 24h REST |
| 17 | RSI heatmap | O-4 | |
| 18 | Mechanical analysis (9-category confluence) | O-4 | **descriptive confluence ONLY — never a board signal; every category labeled un-validated** |
| 19 | Whale tracking (Hyperliquid) | O-4 | public info API |
| 20 | Options widget (surface, strike/expiry heatmap, PCR) | O-4 | extends existing Deribit panels; **unsigned GEX only** (§0.5) |
| 21 | Alerts (price/volume/delta/liq/CVD-div/exhaustion) | O-4 | browser Notification API |
| 22 | Trade journal + playbook | O-5 | localStorage |
| 23 | Calendar returns / performance | O-5 | journal-derived |
| 24 | Polymarket + econ calendar + news feed | O-5 | Polymarket REST / ToA WS; econ source TBD keyless |

## 6. What this unlocks for research (time-gated, NOT granted)

| Family (currently refused as un-ingested) | After collector | Earliest honest OOS use |
|---|---|---|
| Tick CVD / aggressor imbalance | accumulating | when history ≥ MinBTL for the trial count (months, 1h bars) + pre-registered hypothesis |
| Liquidation cascades | accumulating | same |
| OI history (1-min) | accumulating | same |
| Funding accrual (dense, vs ~200-interval REST cap) | accumulating | same; may eventually revisit `carry`'s descriptive-only status **through the harness** |
| VPIN | derivable from stored trades | same + methodology pre-registration |

Everything in this table still requires the DEVELOPMENT.md §6 greenlight ritual. Building
the collector buys *optionality*, not conclusions.

## 7. Verification — the three-layer system

Layer 0 (static, every commit): `python -m pytest` (incl. collector tests; network-free);
`node --check` on every dashboard JS file; `node scripts/check_terminal.cjs` (fixture
smoke: adapters + stores + O-3 normalizers/builders replayed over the REAL captured
frames/responses, 23 assertion groups).

- **L1 — deterministic browser harness** (`make verify-browser`,
  `scripts/verify_terminal_browser.py`): serves the repo, opens
  `terminal.html?replay=1` in headless Chromium. Replay mode
  (`dashboard/terminal-replay.js`) drives the UNTOUCHED adapters with the captured
  fixture frames on a rebased deterministic clock — chips read **replay**, the banner
  gains a REPLAY MODE flag (never masquerades as live, §0). Asserts: zero console/page
  errors, 4/4 chips, store counts advancing, every canvas non-blank, honesty flags
  present; writes before/after screenshots to `reports/verify/` (gitignored). This is
  how UI changes are SEEN without waiting for a human browser pass.
- **L2 — live-wire invariants** (`make verify-wire`, `scripts/verify_wire_live.mjs`,
  zero-dep node): drives the PRODUCTION adapter/store modules from the real endpoints
  for ~45 s; checks books never crossed, venue mids coherent (≤80 bp), event-ts sane,
  trades near mid, CVD bucket-sum ≡ overall, tickers merge finite. Catches exchange API
  drift that frozen fixtures cannot. Exit 2 = offline (distinct from a code bug).
- **L3 — tick-store QA** (`make check-ticks`, `scripts/check_ticks.py`): read-only
  report card over `data/ticks.duckdb` — inventory + GB/30d projection, duplicate
  trade_ids (FAIL), ts inversions, gap census (reported, NEVER filled — §0), cadence
  p95 vs expectation, cross-venue mark divergence, liq sanity. The collector is
  accumulating the future research dataset; L3 is the standing gate that keeps that
  dataset honest. Run it periodically (weekly) and before any research pass touches
  the store.
- Existing dashboard unaffected: `node dashboard/app.js --check` still passes.
