# DESIGN — Orderflow Terminal (CryExc-inspired) + tick collector

Status: **O-0 + O-1 shipped 2026-07-03 (`8c2781f`); O-2 + verification system + O-3
shipped 2026-07-04** (§4b heatmaps/OKX; §7 three-layer verification incl. deterministic
browser replay; §4c structure views — TPO, kline VP, historical chart + no-peek bar
replay, **BYOD tick replay from the collector store (verified end-to-end against a real
8 h recording)**, cross-venue funding, macro proxies). **O-4 shipped 2026-07-05** (§4d
intelligence — VWAP/RSI screeners, Deribit options widget w/ unsigned GEX, HL whale
watchlist, 9-rule alert engine, 9-read descriptive confluence — every read labeled
un-validated). **O-5 shipped 2026-07-05** (§4e portfolio & research — trade journal
(localStorage, Tharp stats on the user's OWN trades, 'NOT a backtest' label), calendar
returns, Polymarket crowd-implied panel, ToA news feed, local-mirror econ calendar
(`make econ` — faireconomy has no CORS), + the elite pass: sticky section nav w/
persisted collapse, hidden-tab/offscreen paint gating (ingestion never pauses), and
`check_terminal.cjs` (77 groups) promoted to a CI build gate). **The terminal feature
plan (§5) is complete** — further work follows the DEVELOPMENT.md §6 greenlight ritual;
the T-1 Trader's Edge pass (§4g) added multi-symbol + the delta/intensity/walls/VPIN/
opening-type/key-levels/basis surfaces on top of it.

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
   **Extension (M7, 2026-08-02) — the public-archive partition, and the four rails that
   let it exist at all.** `scripts/ingest_vision.py` writes Binance's own published
   `aggTrades` archive into `data/vision/`, a tree the recorded store never touches (§3d).
   It is admissible under this rail for one reason only: the archive is the **same venue,
   same stream, same aggTradeId space** the `binancef-aggTrades` collector leg already
   records, so overlap resolves by **exact key match** on `(exchange, symbol, trade_id)`
   and missing history is found by **ID continuity**, not by a timestamp guess. Measured
   2026-08-02 on 2026-08-01: 399,219 archive rows vs the recorded day, set difference
   **0 both ways**, and **0 mismatches** across `ts_ms`/`price`/`qty`/`aggressor_buy` on
   all 399,219 joined rows — the join being against the **DISTINCT** recorded relation,
   stated because the raw recorded table holds 400,190 rows for that day: 971 exact
   full-row duplicates from a RECORDED defect that predates this item (no unique constraint
   on `trades`, in-memory-only `fromId` dedup, so a restart re-seeds a written range;
   RESEARCH-vision-runlog.md §7). A plain join yields 400,190 rows and 0 mismatches either
   way. It is not "another source"; it is the same source arriving by a slower road. The
   rails, non-negotiable:
   a. **Provenance class is per UTC day and never within a bar.** Recorded and archive
      rows are never unioned into one day — precedence is `local > hf > vision`, recorded
      always wins — and a bar therefore carries exactly one `source_code`. This is true
      *by construction*, not by convention: bar boundaries are grid-aligned (refused
      otherwise), and every clock in `BAR_FREQS` divides 86,400,000 ms, so no bar can
      straddle midnight. `orderflow._bar_ms` refuses a clock that does not divide the UTC
      day, precisely so a future addition cannot quietly break the property. The rail rests
      on one more thing, and it is now checked on **both** sides: **a `date=D` partition
      holds day-D rows and nothing else.** Enforcing that only where the tree is WRITTEN
      (G4a) was not enough — the tree is designed to be copied between machines, and a
      partition carrying foreign rows would be unioned into ANOTHER day's bars, a day that
      may have resolved to the recorded store and is labelled as such. So
      `orderflow._open_source` re-checks it per partition before reading (parquet
      row-group `min`/`max` on `ts_ms`: exhaustive, one metadata read, refuses with
      `OrderFlowError`), each partition is additionally fenced to its own day in the SQL,
      and `check_ticks --vision` grades it as a **FAIL** section — the archive-side twin of
      `upload_hf.stage_day`'s "a day file IS its partition".
   b. **Archive rows never count toward `sec_readiness`.** The MinBTL countdown counts
      RECORDED days only (`data/ticks/levels.jsonl`). It is the one honest clock in the
      project; a partition that could inflate it would corrupt every downstream verdict.
      `check_ticks --vision` therefore *refuses to print a readiness number at all*.
   c. **`aggTrades` is TRADES ONLY, so Gap 1 SPLITS rather than closes.** Trade-derived
      families reach 6.587 years (244 % of MinBTL(5)); book-derived families stay at
      1.8 % because the archive publishes no book snapshots. Every book column is NaN on
      every archive bar **by construction**, `provenance_table` says so per column, and a
      frame mixing both families is only as long as its shortest family. `coverage_book_*`
      and `coverage_liq_*` are NaN there too: a coverage column is a WITNESS measure, so
      0.0 asserts "the leg was observed and was silent", and on an archive day the stream
      was never published, never subscribed and never silent — unknown, not zero. Spans are
      counted in resolved days that actually CONTRIBUTED rows, and the `attrs["history"]`
      fraction keys each name their basis (`_requested_window` / `_trade_derived` /
      `_book_derived`; the unqualified `fraction_of_minbtl` is the shortest family). An
      unqualified fraction over the REQUESTED WINDOW is how a two-day frame reports 244 %.
   d. **A day the archive does not publish is ABSENT.** No parquet, no zero-row file, no
      interpolation — a ledger row saying it was asked for and not served.
8. **The terminal is an OBSERVATION surface, not an execution venue.** It holds no keys and
   places no orders — verified zero signing/order/hmac path in `dashboard/terminal*.js`, kept
   as a rail so it stays true, not merely today's state. A GC'd single-thread JS event loop
   painting at rAF over a 100 ms-batched keyless feed (§0.2) is eyes, not hands; the strategies
   it informs execute ELSEWHERE, in a separate native process. HFT and market-making EXECUTION
   are a category boundary for this substrate — sub-µs tick-to-trade, colocation, kernel-bypass,
   C++/Rust/FPGA, a different machine and not a slower version of this one — a design line, not
   a roadmap gap to close by increment. Stated separately and never conflated: surpassing
   Exocharts/aggr on order-flow DISPLAY and research is a real, live goal (STRATEGY §1, §4).

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
  trades (spot leg, maker-side inversion). ~~Binance futures *trades* are NOT collected —
  topic-filtered on this network (§0.2); documented, not proxied.~~ **`[SUPERSEDED]` by
  §3c:** the WS topic filter is real and unchanged, but v2 added a **REST** `aggTrades`
  poll (`binancef-aggTrades`, cursor `fromId` = last `a`+1) which is not topic-filtered,
  so binancef *does* have a trades leg — 0.2–2.0 M rows/day across 2026-07-05..07-22 in
  the recorded store. This sentence describes the original wire constraint, not today's
  store; M7 (§3d) depends on that leg's existence for its exact-dedup argument.
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

### Data lifecycle — archive to GitHub Releases (`scripts/archive_ticks.py`)

The store grows ~2.4 GB/month against a critically limited disk, so closed UTC months
move offsite instead of dying. Binding order (§0 rails applied to storage):

- **Archive-then-prune, never prune-then-hope.** Each closed month is exported per
  table to ZSTD parquet (`<table>_<YYYY-MM>[_pN].parquet`), verified by re-reading
  (row count + ts range vs source), sha256-checksummed into a provenance-stamped
  `MANIFEST-ticks-<YYYY-MM>.json`, and (`--upload`) attached to GitHub Release
  `ticks-YYYY-MM` (repo auto-detected from the git remote). `--prune` refuses to run
  without a byte-verified upload in the same run (usage error otherwise);
  `--force-local-prune` is the deliberately scary offline escape hatch and shouts
  that the only copy is now local parquet.
- **Archives are immutable.** Data assets are never clobbered on a release — only the
  manifest is replaceable; an incomplete month needs `--partial` (cutoff = last full
  hour, captured once per run) and lands beside earlier passes as `_pN`. Ranges whose
  data overlap an existing manifest entry are refused — double-archived rows corrupt
  a later merge.
- **Prune rebuilds the file** (DuckDB files do not shrink in place): DELETE exported
  ranges → CHECKPOINT → rebuild via `collector.open_db` (the canonical schema +
  indexes) → per-table count verification → swap; every failure before the swap
  leaves the live db as-is, and every deleted row already sits in a verified archive.
- **The archive window is an honest maintenance gap.** The script refuses to run
  beside a live collector (`/health` probe + lock detection, exit 2) and the downtime
  is a real hole in `ts_ms` — reported, never filled (§0.7).
- Archives stay queryable in place over HTTP:
  `SELECT count(*) FROM read_parquet('https://github.com/<owner>/<repo>/releases/download/ticks-YYYY-MM/trades_YYYY-MM.parquet')`.
- Make targets: `make archive-dry` (local export only) · `make archive` (export +
  upload + prune) · `make archive-list` (what is offsite). Tests: `tests/test_archive.py`
  (no network, no gh — the CLI seam is monkeypatched).

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

## 3c. Collector v2 — daily rotation + full keyless coverage + Hugging Face lifecycle (binding)

Decisions (user, 2026-07-05): Tier-1..4 completeness · daily-rotation architecture (zero
maintenance gap) · **HF Datasets primary** (GH Release `ticks-2026-07` stays as a frozen
artifact; archive_ticks.py remains functional but is no longer the scheduled path).

**Rotation.** Store becomes per-UTC-day files `data/ticks/YYYY-MM-DD.duckdb` (same schema
per file). The writer routes every row by **UTC day of its `ts_ms`** (event time, not
arrival time — day files are dataset partitions, and partitions must mean event time).
Around midnight both day files stay open during a **5-minute grace window** (late/out-of-
order rows land in yesterday correctly), then yesterday is flushed + closed. A closed day
file is immutable — that is what makes gap-free upload possible. `--migrate-legacy` splits
an existing single `ticks.duckdb` into day files (one-shot). BYOD API serves any range
covered by LOCAL day files (today + yesterday always kept; older days answer 410 with an
'archived to HF' hint). The API contract (paths/params/shapes) is UNCHANGED — terminal
BYOD replay and L1 must stay green.

**New coverage (fixtures `_v2_notes` + reuse of the JS-proven frames):**
- trades: + **OKX** WS (`sz×ctVal` → coin, taker side as-is), + **Coinbase spot** WS
  (maker-side INVERSION — same §0.6 rail as the JS adapter), + **binancef via REST
  `aggTrades` poll** (5 s, cursor `fromId` = last `a`+1 — gapless by aggTradeId; `m`
  true → SELL aggressor; the WS topic-filter (§0.2) does not apply to REST).
- depth: store **top-50** (bybit `orderbook.50` full; **OKX `books`** top-50 ctVal-scaled);
  binancef stays top-20 (that is the whole wire).
- funding_mark + open_interest: + OKX REST 60 s (fundingRate/oiCcy — oiCcy is COIN).
- NEW `crowding(exchange, symbol, ts_ms, metric VARCHAR, value DOUBLE)` — binancef
  `futures/data` endpoints @ 5 m: `taker_buy_sell_ratio`, `top_position_ls_ratio`,
  `global_account_ls_ratio`, `oi_sum_coin`, `oi_sum_usd` (long format: one row per metric).
- NEW `dvol(ts_ms, index_price)` — Deribit 60 s.
- NEW `options_chain(ts_ms, name, expiry_ts, strike, cp, iv, oi, volume, mark_price,
  underlying)` — Deribit book summary snapshot **hourly** (iv stored as DECIMAL — the
  /100 rail; this starts the VRP/skew research clock).

**HF lifecycle** (`scripts/upload_hf.py`, `make hf-sync`, launchd
`com.btcquant.hfsync.plist.example` daily ~00:20 UTC — needs NO collector stop):
dataset repo `azulcoder/btc-quant-ticks` (create_repo exist_ok; token from the user's own
HF login — an account credential, not market-data access); layout
`data/date=YYYY-MM-DD/{table}.parquet` (hive-style) + `manifests/MANIFEST-<date>.json`
(sha256, rows, ranges, provenance) + an auto-generated dataset card (schema, honesty
rails, keyless provenance, 'gaps stay gaps'). Flow per closed local day: export → re-read
verify → sha256 → upload → **verify on HF (size + LFS sha when available)** → only then
delete the local day file (today + yesterday never deleted). Query-back:
`read_parquet('hf://datasets/azulcoder/btc-quant-ticks/data/date=YYYY-MM-DD/trades.parquet')`
and `load_dataset` streaming for ML. `check_ticks.py` learns dir/glob mode (union view
across day files). Same prune-safety creed as the §3 lifecycle: no offsite verification,
no local delete.

## 3d. Public-archive trade ingest (M7) — `scripts/ingest_vision.py` (binding)

Rails first, because the limit has to be in the doc before there is code that could hide
it. **`aggTrades` is TRADES ONLY.** This partition extends the trade-derived families
(CVD, footprint, size-bucketed delta, VPIN) and **nothing else**. OFI, weighted mid,
depth-imbalance slope and walls gain zero rows: the archive publishes no book snapshots
(`bookDepth` is 12 cumulative ±% bands at ~30 s — no levels, no price-per-level, no
queue — and cannot satisfy `depth_snapshots(bids, asks)`; `bookTicker` is L1-only and
exists for 320 days ending 2024-03-30, discontinuous with the recorded window). Gap 1
SPLITS: trade-derived reaches **2,406 d = 6.587 yrs = 244 % of MinBTL(5)**, book-derived
stays at **1.8 %**. §0.7 rails a–d bind every line below.

**Scope allowlist, in code not in a comment.** Only `market="futures/um"` +
`family="aggTrades"` is accepted; anything else raises with the reason. Spot is a
different instrument with an 8-column layout, `True`/`False` capitalized booleans and
microsecond timestamps since 2025-01-01 — a different ID space, so the exact-dedup
argument does not hold for it. `metrics` has no unique key and its timestamp convention
differs per metric inside one file (+300,000 ms for open interest vs 0 ms for the taker
ratio). `liquidationSnapshot` does not exist for USD-M at all.

**Tree.** Provenance readable from the path alone, so no schema migration is ever needed
to know where a row came from:

```
data/vision/<venue>/<symbol>/<family>/date=<YYYY-MM-DD>/trades.parquet
data/vision/<venue>/<symbol>/<family>/manifests/MANIFEST-<YYYY-MM-DD>.json
data/vision/<venue>/<symbol>/<family>/manifests/FAILED-<YYYY-MM-DD>.json
data/vision/_ledger.jsonl                      # append-only, one row per day ATTEMPTED
```

Parquet ZSTD, not `.duckdb`: this is a write-once immutable batch export, the same idiom
`upload_hf.stage_day` already uses — and a `.duckdb` file under `data/vision/` would be
one `ATTACH` away from being unioned into a recorded relation, which the tree exists to
make structurally hard. The output root is `resolve()`d and **refused** if it lies inside
the tick store or the order-flow cache. Schema is `collector._TABLE_COLUMNS["trades"]`
exactly, written `ORDER BY ts_ms`; `first_trade_id`/`last_trade_id` are dropped from the
rows (column identity is load-bearing — the relation must stay `UNION ALL`-compatible
with recorded) and their extents kept in the manifest.

**Column mapping** (measured, not assumed): `agg_trade_id → trade_id` as VARCHAR
(identical to `str(t["a"])` in `collector.normalize_binance_aggtrades`), `price → price`,
`quantity → qty` (BTC), `transact_time → ts_ms` (epoch **ms**), and
`aggressor_buy = NOT is_buyer_maker` (§0.6 — `m` true means the buyer was the maker, so
the aggressor SOLD). `exchange='binancef'` and `symbol='BTCUSDT'` come from the path, not
the file. A fixture test drives the collector normalizer and the vision normalizer over
the same trade and asserts the 7-tuples are identical, so "same stream" is mechanical
rather than prose.

**Seven gates. A day is not trusted until all seven pass, and the canonical parquet is
never written before they do.**

| | Gate | Refusal |
|---|---|---|
| G1 | CHECKSUM: sha256 of the zip matches the companion `.CHECKSUM`, **and** its filename field is the canonical name | abort — parse the hex in-process, never shell out to `shasum -c` (it fails on a renamed file for the wrong reason) |
| G2 | Zip shape: exactly one entry, named `<stem>.csv` | abort |
| G3 | Header sniff: line 1's first field is a base-10 integer ⇒ data, else header; a present header must match the expected 7 names exactly; column count ≠ 7 ⇒ abort | abort (this is what catches the spot layout) |
| G4a | Day containment: every `ts_ms ∈ [day_start, day_end)`, integer epoch bounds | abort |
| G4b | ms-unit guard: `ts_ms < 1e14` (13 digits). 16 digits = µs ⇒ abort | abort — read as ms it lands in year ~58500 |
| G5 | ID continuity in-day: rows = distinct ids (**duplicates abort**); `max−min+1 − count` is recorded as `id_holes` + `id_hole_ranges` | holes **reported, never filled** |
| G6 | Seam: if `date−1` is already ingested, `first_id(D) == last_id(D−1)+1` | mismatch recorded in both manifests, **never patched** |
| G7 | Re-read verify: reopen the written parquet, match `(rows, ts_min, ts_max)` and containment | abort, keep `.bad` for inspection |

Failure keeps the bad artifact for inspection (`trades.parquet.bad`), writes
`FAILED-<date>.json`, skips that day, **continues** the run, and exits non-zero. A day
the archive does not publish (HTTP 404) is `status="absent"` in the ledger and produces
**no file at all** — never a zero-row parquet. Day bucketing is integer
(`ts_ms // 86_400_000`), never a local-timezone date function.

**Granularity.** `auto` uses monthly files for months wholly inside the range and wholly
in the past (79 requests instead of 2,406 for the full history) and daily for the ragged
ends. Monthly is set-identical to the daily files it contains (verified: 71,359 = 71,359
rows for 2020-01-01, `EXCEPT` empty both ways), and every day split out of a monthly file
passes the same G4–G7 — monthly buys request count, never leniency. Three things the
monthly path must not do, all measured:

* **A missing MONTHLY object is not an absent DAY.** Binance publishes the bundle days
  after the month ends: on 2026-08-02 the `2026-08` monthly object was HTTP 404 while
  `2026-08-01` daily was HTTP 200 (5,049,749 B, 399,219 rows). Marking the month's days
  `absent` is a FABRICATED ABSENCE — the inverse of rail d — so a 404 on the bundle falls
  back to the daily objects and lets each day answer for itself.
* **Re-download a month whose days are complete.** Resume is checked BEFORE the wire, as
  the daily path already did (measured: a completed 2020-01 re-run costs 0 B and 1.1 s
  instead of 97.8 MiB and 23 s), and an `already` day reports its true row/byte counts.
* **Escape.** The per-month handler catches `Exception`, not only `VisionError`: a duckdb
  `ConversionException` or a `zipfile.BadZipFile` used to abort the whole backfill with a
  traceback and no ledger row. Each day the month owed now gets `FAILED-<date>.json` + a
  ledger row, and the run continues and exits non-zero. Manifests from the monthly path
  carry the `first_trade_id`/`last_trade_id` extents the daily path records, computed in
  one extra grouped scan.

**Politeness and memory.** Sequential by default (measured 3.7–4.8 MB/s single-stream;
concurrency buys nothing against the CDN edge and is what gets an IP throttled), `--sleep`
between requests, exponential backoff with jitter honouring `Retry-After` on 429/5xx, no
retry on 404/403 (404 is an answer), a descriptive UA, and `--all` prints the measured byte
total and requires `--yes`. The zip **streams to a scratch `.part` file, hashed in the same
pass**, so a ~530 MB monthly object is never buffered whole and G1 verifies exactly the
bytes that landed; no file ever carries the canonical name before G1 passes, because the
download never leaves the scratch directory. A failed attempt truncates and restarts rather
than splicing a byte range — a silently spliced file is a worse failure than a re-fetch.

**L3 QA covers it.** `check_ticks.py --vision <root>` grades the archive partition with
the same code path as the recorded store — duplicate `trade_id` still **FAILs**, gaps
stay gaps — plus two vision-only censuses: **partition containment** (rows outside the day
their path claims: **FAIL**, same grade as a duplicate id) and **ID continuity**. The ID
census is computed PER DAY and summed: in-day holes plus the gaps across calendar-ADJACENT
ingested days are missing ids; ids lying between NON-adjacent days are the days the
operator did not ask for and are reported as such (`INFO`), not as missing data. Measured:
a tree holding only 2026-07-30 and 2026-08-01 used to WARN `1,106,864 missing id(s)` —
exactly the row count of the un-ingested 2026-07-31. Two things it deliberately does *not* do: it prints
`[INFO] not applicable` rather than `[OK]` for the ts-inversion check (the parquet is
ts-sorted, so arrival order is not recoverable and a pass there would be vacuous), and it
**refuses to compute a readiness number** (rail b). Make targets: `make vision-sync`,
`make vision-list`, `make check-vision`.

**Reading it back.** `orderflow.order_flow_bars(..., source=("local","hf","vision"))` and
`orderflow.volume_buckets(..., source=(...))` are the only ways archive rows enter a frame;
the default `"auto"` is recorded-only and stays that way. Every bar — and every VPIN
bucket, which is the trade-derived table the archive extends furthest — carries
`source_code` (always emitted, so its presence never signals anything), an
`attrs["orderflow"]["archive"]` block, and its own `vision_root`/`vision_symbol` so the
tree read is the caller's choice. `provenance_table` gains a `vision_contribution` column
naming the archive per column, and a run touching archive days warns with the exact
counts — **including on a cache hit**, since `cache=True` is the default and a warning the
cache skips is not mandatory, it is incidental.

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

## 4d. O-4 contracts — intelligence views (binding; fixtures `_o4_notes` + captures 2026-07-05)

Rails first: screeners / alerts / confluence are **descriptive reads, never signals** —
the IC run-log found NO board signal with significant forward IC, so every tally/read
carries "un-validated · not a score to trade". Whale positions = **public on-chain
facts**. Options stay **mark-only + unsigned GEX** (§0.5; `mark_iv` is PERCENT → /100,
DEVELOPMENT §5). Empirical: Deribit REST is **CORS-open** to browser origins (verified);
DVOL via `get_index_price?index_name=btcdvol_usdc`; **HL leaderboard = 33 MB / 40 k rows**
→ opt-in one-shot load (button states the size), then light per-address
`clearinghouseState` polls; Bybit `tickers?category=linear` = **720 symbols in ONE call**
with `fundingIntervalHour` response-provided (use it — not the 8 h constant) and 24 h
VWAP proxy = `turnover24h/volume24h` (labeled `24h VWAP`).

- **terminal-hist.js additions** (same fetcher+normalizer pattern):
  `fetchBybitAllTickers()`, `fetchDeribitChain('BTC')` (book_summary by currency,
  kind=option), `fetchDeribitDvol()`, `fetchHlLeaderboard()` (33 MB — callers gate),
  `fetchHlClearinghouse(addr)`; pure `normalize*` for each (fixture-tested):
  tickers → `{sym, last, vwap24h, vwapDevPct, pct24h, turnover24h, fundingRate,
  fundingIntervalH, annualizedFundingPct, oiUsd, mark, index}`; chain rows →
  `{name, expiryTs, strike, cp:'C'|'P', iv (decimal — /100!), oi, volume, markPrice,
  underlying}` (parse `BTC-28AUG26-105000-C`); leaderboard → `{topByValue:[{addr,
  acctVal}], topByRoi30d:[…]}` (parse `windowPerformances`); positions →
  `[{coin, szi, side, entryPx, posValue, uPnl, leverage}]`.
- **terminal-state.js additions (pure):**
  `buildScreener(tickers, {topN})` → rows sorted by turnover (default top 40, 'all'
  passthrough); `confluenceReads(inputs)` → exactly 9 labeled categories (footprint
  Δ-trend, CVD slope, price vs POC/VA, TPO position, funding sign/extreme, OI 1 h
  change, liq-pressure 5 m imbalance, book top-10 imbalance, price vs SMA50 hist trend)
  each `{category, read:'bullish'|'bearish'|'neutral'|'n/a', detail}` + tally — output
  object carries `label:'un-validated descriptive reads — forward IC of board signals
  ≈ 0 (RESEARCH-ic-runlog); NOT a signal'`; `AlertEngine({rules})` — `evaluate(snap)`
  → events, rule kinds: price-cross, whale-print≥$X, liq-1m≥$X, funding-flip,
  cvd-divergence (price HH & CVD lower-high in window — `label:'heuristic'`),
  book-imbalance≥X%, detector-passthrough, oi-jump≥X%/h, basis≥X bp; per-rule cooldown
  (default 60 s, event-ts driven), thresholds injected (no defaults hidden in logic).
- **Views** (+ layout: new INTELLIGENCE section after STRUCTURE):
  `ScreenerView` — canvas bubble scatter (x = 24 h %, y = VWAP-dev %, r ∝ √turnover,
  color = funding sign/degree), hover readout, top-40/all toggle, header
  `bybit linear · 24h VWAP = turnover/volume`;
  `RsiHeatmapView` — RSI-14 (quant.js `rsi`) on 1 h klines for top-N-by-turnover
  bubbles with 30/70 bands; batch fetch on demand + 5 min refresh, progress honest
  (`n/40 loaded`); label `1h RSI-14 · bybit klines`;
  `OptionsView` — Deribit chain: IV smile per selected expiry + term structure,
  strike×expiry IV heatmap, PCR (by OI and by volume), **unsigned GEX profile**
  Σ|Γ|·OI by strike (Γ via quant.js `black76Greeks`, F = underlying, iv decimal),
  max pain (quant.js `maxPain`), DVOL stat; labels `mark-only chain` + `unsigned —
  dealer sign unknowable (§0.5)`;
  `WhaleView` — HL watchlist table (addr short, coin, side, szi, entry, uPnL, lev),
  add-address input + localStorage, `discover top traders (~33 MB, one-shot)` button
  → seeds top-10 by acctVal + top-10 by 30 d ROI, per-address 60 s polls (cap 25);
  label `public on-chain state (Hyperliquid) — facts, not signals`;
  `AlertsView` — rule table with enable + threshold inputs (persisted), live alert
  feed, browser Notification opt-in, banner `descriptive triggers — un-validated`;
  `ConfluenceView` — the 9 reads + tally + the mandatory IC-honesty line.
  Also: HeaderStatsView renders the transport msg for kind `error` when present
  (('byod api unreachable') — closes the O-3 chip-visibility flag).
- **Wiring:** polls — tickers 30 s (one call), RSI batch on demand/5 min, chain 60 s,
  whale 60 s/address; ALL disabled in replay modes (deterministic L1 — same rule as
  O-3; panels show the honest replay-disabled note).
- **check_terminal.cjs additions:** normalizer exactness vs the pinned fixtures
  (BTCUSDT vwap = turnover/volume to 1e-9; deribit name-parse strike/expiry/cp +
  iv/100; dvol 38.68 — the pinned capture's value (§0: real payload wins);
  leaderboard top-N incl. `windowPerformances` parse; whale
  positions szi/side/entry), `confluenceReads` both-directions per category + tally +
  mandatory label, `AlertEngine` fire/cooldown/divergence-label per rule kind,
  unsigned-GEX sanity (Γ > 0, Σ matches a hand-computed strike), PCR math.

## 4e. O-5 contracts — portfolio & research views + elite pass (binding; fixtures `_o5_notes`)

Empirical (2026-07-05): **Polymarket gamma CORS `*`** — route is `/events?tag_slug=bitcoin`
(nested `markets[]`; `outcomePrices` are STRINGS in a JSON-encoded array; `markets?search`
and `markets?tag_slug` both IGNORE their filters — verified, they return FIFA/Rihanna).
**Tree of Alpha REST CORS `*`** (`/api/news?limit=N`). **faireconomy econ JSON has NO CORS**
→ browser cannot fetch it; design = `scripts/fetch_econ.py` (stdlib) writes
`dashboard/econ_calendar.json` (gitignored, same-origin) via `make econ`; the panel shows a
fetch-age stamp and a friendly "run `make econ`" note when the file is absent/stale.

Rails: the journal records **the user's own trades** (manual log; localStorage; export/
import CSV — data portability). Its stats reuse the repo's Tharp conventions (R-multiple,
expectancy, SQN, PF — same definitions as risk.py/quant.js; cite them) on **journaled, not
backtested** trades — panel label `your logged trades — descriptive record, NOT a backtest`.
Polymarket prices are **crowd-implied probabilities** (labeled). News/econ are **context
feeds** (labeled sources).

- **terminal-hist.js additions:** `fetchPolymarketBtc()` → normalized
  `[{title, endTs, vol24h, markets:[{question, yesPct (Number(outcomePrices[0])·100), vol24h}]}]`;
  `fetchToaNews(limit)` → `[{ts, title, source, url, symbols}]`; `fetchEconLocal()` →
  same-origin `./econ_calendar.json` `{fetchedTs, events:[{ts, title, country, impact,
  forecast, previous}]}` | null. Pure normalizers each, fixture-tested
  (`polymarket_events` / `toa_news` / `ff_econ_sample`).
- **`scripts/fetch_econ.py`** (stdlib only): pulls faireconomy `thisweek` + `nextweek`,
  merges/sorts/annotates `{fetchedTs}`, writes `dashboard/econ_calendar.json`;
  `make econ` target; idempotent; exits nonzero on network failure with a clear message.
- **terminal-state.js additions (pure):** `journalStats(trades)` → Tharp block
  `{n, winRate, expectancyR, sqn, profitFactor, avgWinR, avgLossR, maxDrawR, byTag:{tag→{n,
  expectancyR}}}` — R = pnl/riskUsd per trade (user-declared 1R; same convention family as
  the repo's vol-notional R, comment the difference honestly); `calendarReturns(trades)` →
  `{daily:{'YYYY-MM-DD'→R}, weekly, monthly, hourly:{0..23→R}}` (close-ts bucketing, UTC);
  `validateJournalCsv(text)` → `{trades, errors}` (import NEVER silently coerces — bad rows
  land in errors). Trade shape: `{id, tsOpen, tsClose, side, entry, exit, size, riskUsd,
  tag, note, ctx?}` — `ctx` = auto-captured `{mark, fundingRate, oi, cvdSlope,
  confluenceTally}` at log time (descriptive record of conditions).
- **Views** (+ layout: new PORTFOLIO section after INTELLIGENCE):
  `JournalView` — log form (side/entry/exit/size/1R/tag/note; auto-ctx snapshot from live
  stores), trades table (R colored, tag chips, ctx popover), Tharp stats strip (n, win%,
  Exp R, SQN, PF, by-tag), CSV export/import (import shows per-row errors honestly);
  label per rail. `CalendarView` — month grid heatmap of daily R + weekly/monthly bars +
  hour-of-day histogram; empty state honest. `PolymarketView` — BTC events list (title,
  countdown, per-market yes% bars, 24 h vol), 60 s poll; label `crowd-implied
  probabilities (Polymarket) — not a forecast endorsement`. `NewsView` — ToA feed 30 s
  poll (source chip, symbols, age, link), whale-emphasis on BTC-symbol items; label
  `Tree of Alpha — context feed`. `EconView` — reads the local JSON: upcoming events
  (country flag text, impact badge High/Medium, countdown, forecast/prev), fetch-age
  stamp + `make econ` hint; label `ForexFactory mirror · fetched locally (no CORS)`.
- **Elite pass (this phase, binding):**
  1. **Section nav**: sticky mini-nav in the terminal header — ORDERFLOW · STRUCTURE ·
     INTELLIGENCE · PORTFOLIO anchors + per-section collapse toggles (persisted in the
     settings object; collapsed sections skip rendering entirely — dirty flags intact).
  2. **Visibility performance**: rAF loop pauses on `document.hidden` (ingestion
     continues — pause is presentation-only, same honesty rule as the pause button);
     each canvas view render-gated by an IntersectionObserver (offscreen = skip paint,
     state keeps accumulating; comment the honesty: skipping paint ≠ skipping data).
  3. **CI enforcement**: `.github/workflows/ci.yml` gains `node scripts/check_terminal.cjs`
     (30+ groups, network-free) — the terminal smoke becomes a build gate like parity.
- **check_terminal.cjs additions:** polymarket normalizer vs fixture (STRING
  outcomePrices → yesPct number; nested events route), ToA normalizer, econ local-file
  normalizer (synthetic file object), journalStats hand-computed exactness (3-trade set:
  known expectancy/PF/SQN/winRate), calendarReturns bucketing (UTC day + hour), CSV
  round-trip export→import identity + a bad-row lands-in-errors case.

## 4f. I-1 contracts — Institutional Auction Suite (binding; probes 2026-07-10)

Beyond the CryExc map: tick-EXACT auction analytics on the recorded store. Empirical
basis: DuckDB aggregates a full 2.87 M-trade day into a 174-level profile in **19 ms**
(server-side endpoints are effectively free); **HF CORS echoes the Pages origin** on
parquet resolve links (browser can read ARCHIVED days directly). Rails: every derived
read stays descriptive; heuristics labeled; OFI/microprice carry their paper citations
(Cont–Kukanov–Stoikov OFI; Stoikov microprice); dataviz discipline — delta profile =
diverging two-hue + NEUTRAL midpoint (never a hue at zero), OFI in its OWN pane (never
dual-axis), palette hexes VALIDATED via the dataviz skill validator against the dark
terminal surface, text in text tokens.

**Backend — collector.py read-side API additions (same lock/contract discipline):**
- `GET /v1/profile?symbol&exchange=bybit&start_ms&end_ms&tick=10[&buckets_usd=1e4,1e5]`
  → `{levels:[{lvl, buy_vol, sell_vol, prints[, b0,b1,…]}], poc, vah, val, total_vol,
  vwap, sigma}` — SQL aggregation across LOCAL day files covering the range (union),
  70 % value-area expansion server-side (one convention: mirror ProfileStore's).
- `GET /v1/vwap?symbol&exchange&anchor_ms[&end_ms]` → `{vwap, sigma, n}` (anchored).
- `GET /v1/levels` → the **levels registry**: one row per RECORDED UTC day
  `{date, o,h,l,c, poc, vah, val, vol}` (bybit leg), served from
  `data/ticks/levels.jsonl`; `naked` (POC never revisited by any LATER day's range)
  is DERIVED at serve time, never stored. Registry maintenance: (a) rotation hook —
  on day-close the manager computes the day summary from the closing file and appends
  one line; (b) `scripts/backfill_levels.py` — one-shot over the HF dataset via
  `hf://` for already-archived days (idempotent: skips dates present). Makefile:
  `backfill-levels`.
- Endpoint tests: aggregation vs hand-SQL on synthetic day files; VA expansion parity
  with the JS ProfileStore fixture case; levels rotation-hook append; naked derivation
  (revisited vs untouched); range-spanning union across two day files.

**Builders (terminal-state.js, pure; check_terminal groups mandatory):**
- `buildDeltaProfile(levels)` → per-level `{lvl, delta, intensity∈[0,1]}` (p95-normal-
  ized) for the diverging render; sums must satisfy Σdelta ≡ Σbuy−Σsell exactly.
- `SessionClock()` → UTC session tags + boxes: Asia 00–08, London 07–16, NY 12–21
  (overlaps real, labeled UTC — comment: classic FX-desk convention, not an oracle).
- `AnchoredVwap()` — streaming (Welford-style) vwap ± σ bands from trades:
  `onTrade(t)`, `bands()` → `{vwap, s1, s2}`; deterministic vs batch formula 1e-9.
- `OfiStore({levels=5})` — Cont–Kukanov–Stoikov order-flow imbalance from successive
  top-N book snapshots (event-ts cadence; comment: 1 s snapshot approximation stated);
  `onDepthSample(ts, grouped)`, `series()` (rolling sum) + `zscore(window)`.
- `microprice(book)` — Stoikov imbalance-weighted mid; plus `mid()` delta series.
- `stackedImbalances(bars, {k=3, minRun=3})` → zones `{top, bottom, side, barIdx}`
  (≥3 consecutive same-side diagonal imbalances); zone dies when traded through.
- `AbsorptionDetector({volK=3, progressTicks<=1})` — label:'heuristic' (volume spike
  at a level, no follow-through next bar — pattern-consistent-with, not proof).
- Footprint per-bar cum-delta series accessor (for the mini-pane).

**Views/UI (terminal-views.js/js/html/css — INSTITUTIONAL section after STRUCTURE):**
- `AuctionProfileView` (centerpiece, canvas): day/session/range selector (local days
  from /v1/levels + 'today live'); modes total | buy×sell | **delta (diverging,
  validated ramp)** | size-bucket; POC/VAH/VAL lines; **naked-POC registry overlay**
  (dashed levels, age labels); composite multi-day merge; hover readout; replay mode
  → honest disabled note (endpoints are live-local only).
- HistChartView gains: session VWAP + ±1σ/±2σ band series (day|week|custom anchor
  select — the AMT read: value ≈ vwap ± 1σ), session boxes shading on the book
  heatmap canvas, levels-overlay toggle (prior-day POC/VA + naked POCs from registry).
- `MicrostructureView`: OFI rolling-sum pane + microprice−mid series pane (separate
  scales/panes), readout strip (book imbalance, microprice, OFI z); citations label.
- FootprintView upgrades: stacked-imbalance zone shading, absorption ◉ flags
  (labeled), per-bar POC dot, cum-delta mini-pane.
- `LevelsView`: recorded-day table (date, O/H/L/C, POC, VA, vol, naked badge) +
  draw-on-charts toggle; DOM ladder rows at naked-POC levels get a subtle marker.
- Dataviz gates: diverging ramp built from the existing up/down tokens + neutral,
  hexes run through the skill validator (dark surface) and the report pasted into
  the PR-style commit body; every new panel ships hover + legend per skill rules.

**Wave 2 (stretch, honesty-gated):** `dashboard/terminal-hfdata.js` + vendored
single-file parquet reader (`dashboard/vendor/hyparquet.js`, MIT) → AuctionProfileView
can load ANY ARCHIVED day straight from the HF dataset in-browser (CORS proven),
labeled `archived day · hf dataset`; if vendoring proves unsound the agent reports
and the feature defers — no half-measures on the public page.

## 4g. T-1 contracts — Trader's Edge (binding)

Greenlit 2026-07-22. Everything live-descriptive (§0.1); heuristics labeled; citations
on-panel; the collector still records BTCUSDT only, so store-backed surfaces state that
honestly when another symbol is selected.

**Multi-symbol (the headline).** `SYM` becomes a runtime setting (persisted; default
BTCUSDT). Symbol picker fed by the EXISTING `fetchBybitAllTickers` universe (top-N by
turnover + search). On switch: all WS legs close and re-subscribe with venue ids derived
by the SAME mapping the collector uses (base = strip USDT: bybit/binancef `<B>USDT`,
okx `<B>-USDT-SWAP`, coinbase `<B>-USD`); a leg whose mapping is unknown/unreachable for
that symbol degrades honestly (chip 'no <venue> leg for <sym>'). Session stores REBUILD
on switch (a new symbol is a new session — same honest-restart rule as tick-size
changes, stated in the settings hint). Store-backed panels (auction profile, levels,
BYOD replay) render their compact honest note when sym ≠ BTCUSDT ('collector records
BTCUSDT only'). binance leg keeps its REST-only reality (§0.2).

**Delta pro (FootprintStore additions, pure).** Track running intra-bar delta →
`deltaMin`/`deltaMax` per bar; `deltaPct = delta/totalVol`; **unfinished auction**
flags: a FINISHED bar whose extreme price level (high for up-extreme, low for
down-extreme) printed BOTH buy and sell volume — the auction did not finish there
(classic orderflow marker; Dalton-adjacent, comment the rule). New footer row toggle in
FootprintView (Δmin/Δmax + Δ%) and ⌟ markers at unfinished extremes.

**TapeIntensityStore (pure).** Rolling windows (10 s / 60 s, event-ts): trades/sec,
notional/sec, and a z-score vs the session baseline (Welford over per-10s samples).
Gauge + 60-sample sparkline in the tape panel header. Descriptive; burst ≠ signal.

**Key-levels strip + live IB.** From `/v1/levels` (registry): prior-day H/L/C, POC,
VAH/VAL, naked POCs (age), weekly open (derived from registry rows — the Monday row's
open, comment the convention). Live session adds the Initial Balance (first 2×30 min
UTC range) once elapsed. Rendered as (a) a compact strip panel and (b) optional
horizontal markers on the footprint canvas (toggle, default on). BTC-only via registry —
honest note otherwise.

**WallsLedger (pure).** From DepthHistory samples: a level whose resting qty ≥ K×p95
(K=4 default) sustained ≥ M samples (M=5) enters the ledger {price, side, firstTs,
maxQty, lastTs, status}; status flips to 'pulled' when it vanishes while price is > 1
tick away (ties to the spoof detector's rule family — cross-reference, do not merge) or
'filled' when price trades through it. Ring 50. Panel table, newest-first, status
badges. Label: descriptive book-history bookkeeping, not intent.

**VpinStore (pure, cited).** Volume-synchronized buckets (bucket volume V = configurable,
default session-volume/50 re-estimated hourly): per bucket |buyVol − sellVol|/V using
the REAL aggressor flags (state on-panel that this is *better-informed* than the
original BVC approximation — Easley, López de Prado & O'Hara 2012 — and cite the
Andersen–Bondarenko critique: VPIN's toxicity interpretation is CONTESTED; we show the
series, not a claim). VPIN = mean over last 50 buckets; sparkline + current value.

**OpeningTypeClassifier (pure, rule-based, AMT).** After the first 60 min UTC: classify
open-drive (one-directional, < 20% retrace of open range), open-test-drive (probe one
side ≤ 30 min then drive opposite beyond open), open-rejection-reverse (drive then full
reverse through open), else open-auction (rotational). Cite Dalton, *Mind over Markets*;
label 'descriptive session read — not a signal'. Shown in the session strip.

**BasisSeries.** Ring of (ts, basis_bp, funding_rate) from the EXISTING 1 s mark events →
two-pane mini chart (basis bp; funding) in STRUCTURE. No new feeds.

**UX.** ⌘K command palette on the terminal (mirror index.html §5.1 idiom): jump to
section/panel, switch symbol (fuzzy over the universe), toggle sections, switch
workspace. Three named workspace presets = persisted collapsed-set combos
(ORDERFLOW-focus / AUCTION-focus / ALL) + the user's custom state as 'last'.

**check_terminal groups (mandatory adds):** footprint deltaMin/Max + unfinished-auction
rule on constructed bars; TapeIntensity window math + z; WallsLedger enter/pull/fill
transitions; VPIN bucket math on constructed trades (hand-computed); OpeningType all
four classes on constructed opens; venue-id derivation (BTCUSDT/ETHUSDT/1000PEPEUSDT —
the last has NO coinbase mapping → leg skipped honestly); BasisSeries ring.

## 4h. T-2 contracts — Venue Matrix (binding)

Greenlit 2026-07-23. Empirical basis MEASURED on this network 2026-07-23 (25 s
concurrent probes; scratch probe, results recorded here as the §2 extension):

| leg            | trades                          | depth (keyless, measured)                          |
|----------------|---------------------------------|----------------------------------------------------|
| bybit linear   | publicTrade WS (current)        | orderbook.200 (orderbook.500 → handler-not-found)  |
| bybit spot     | publicTrade WS — flows          | orderbook.200 — flows                              |
| binance fut    | WS topic-filtered (§2) → REST   | diff `@depth@100ms` FLOWS → full local book sync   |
| binance spot   | aggTrade WS — FLOWS (unlike fut)| diff + depth20 flow → full local book sync         |
| okx swap       | trades WS (current)             | `books` 400-level + CRC32 checksum — flows         |
| okx spot       | trades WS — flows               | `books` 400-level + CRC32 checksum — flows         |
| coinbase spot  | matches WS (current)            | `level2_batch` keyless: FULL snapshot (~44k levels measured) + batched l2update |

**Leg registry.** Seven venue×market legs, each individually enable/disable-able
(persisted setting + a chip per leg; disabling closes the socket and freezes that
leg's panels honestly — no interpolation). Symbol mapping extends T-1
`deriveVenueIds` to the matrix (spot ids: binance spot = `<B>USDT`, bybit spot =
`<B>USDT`, okx spot = `<B>-USDT`, coinbase = `<B>-USD`); T-1 listability probes
reused per leg; unknown/unreachable → honest no-leg chip (§4g rule).

**Full-book sync engines** (new file `dashboard/terminal-books.js`, pure, each with
constructed-sequence check groups):
- `BinanceBookSync` — REST snapshot (`depth?limit=1000`) + buffered diff events,
  spot continuity `U ≤ lastUpdateId+1 ≤ u`, futures continuity via `pu` chaining;
  any gap → counted honest resync (chip note `resync ×N`), never silent patching.
- `OkxBookSync` — `books` snapshot+update; integrity via the **seqId/prevSeqId
  chain** (each update's `prevSeqId` must equal the last applied `seqId`; gap →
  counted resync + leg restart). **[SUPERSEDED 2026-07-24]** the original plan was
  CRC32 checksum-verify, but MEASURED on the real wire (179/179 `books` frames
  BTC-USDT-SWAP 2026-07-24, and 300+ frames across SWAP/spot/ETH the day before)
  the public `books` channel carries `checksum: 0` on every frame — the venue only
  populates the CRC on the login/VIP-gated `books-l2-tbt` tick-by-tick channels.
  So checksum-verify is degenerate keyless; the seqId chain IS the venue's ordering
  guarantee and is the correct rail. CRC32 helper retained (dependency-free + pinned
  zlib vectors) for the tbt channel should we ever authenticate — documented dormant.
- `CoinbaseBookSync` — full snapshot + `l2update` absolute-qty application
  (qty 0 removes level); no venue sequence number exists (stated), so the rail is
  reconnect-on-gap-in-time + fresh snapshot.
- Perf contract (stated): O(1) map updates per event; sorted materialization only
  for the visible render window at paint cadence, never per message.

**Surfaces.** Agg-book panel becomes leg-aware (per-leg include toggles — the same
registry); CVD-per-exchange gains the new legs; NEW **spot vs perp CVD** strip
(sum of enabled spot legs vs perp legs — a descriptive lead/lag read, labeled, no
signal claim). Book heatmap + grid-bound history stay bybit-linear primary
(different grids/venues would fabricate a merged history — stated on panel).
Collector recording scope is UNCHANGED this phase (BTCUSDT bybit-primary store);
the matrix is a display/ingest surface — stated in the settings hint.

**check_terminal groups (mandatory adds):** Binance continuity incl. gap→resync
and event-straddling-snapshot; futures `pu` chaining vs spot rule difference;
OKX CRC32 pinned vector + mismatch→resync; Coinbase apply/remove-zero/replace;
leg-registry enable/disable + persistence shape; spot-vs-perp CVD store math;
matrix `deriveVenueIds` extension (spot ids + digit-prefix + non-USDT degrades).

## 4i. T-3 contracts — Tape & Ladder Pro (binding)

Greenlit 2026-07-24, prompted by an aggr.trade workspace (RifeBTC-full). aggr's
value is its aggregated TAPE: many venues merged into one time-ordered tape with
size-tiered emphasis on big prints; it has NO order book. So T-3 = port that
tape-reading MODEL onto the T-2 venue matrix, and (our own add) turn the DOM ladder
into a full-depth reading surface on the T-2 local books. Everything live-descriptive
(§0.1); thresholds are labeled display conventions; COVERAGE IS HONEST — the tape
aggregates the venues we reach keyless (the enabled T-2 trade legs, up to 6), NOT
aggr's ~24. No claim beyond what the wire gives.

### Tape (TapeView rework + pure stores)

- **TapeAggregator (pure).** Merge a run of same-venue, same-side trades at the same
  price within `aggWindowMs` (default 100 ms, event-ts) into one row: summed qty,
  summed notional, volume-weighted avg price, and `count` (shown `×N`). Mirrors aggr
  `aggregationLength`. A price change, side flip, venue change, or window expiry
  closes the row. Never merges across venues (that would fake a single print).
- **Size tiers by USD notional** (configurable; BTC-scaled defaults, each a stated
  convention): `sig` >= $100k, `large` >= $250k, `huge` >= $1M, `whale` >= $5M;
  below `sig` = baseline (dim, optionally collapsed). Each tier => colour intensity
  + row weight + a histogram bar scaled by log-notional. This is the "read the tape"
  core (aggr threshold/significant/huge/rare).
- **Merged multi-venue tape.** All ENABLED T-2 trade legs, time-ordered into one tape
  with a per-row venue dot/tag; spot vs perp visually distinguished + a spot-only /
  perp-only / both filter. The existing per-panel single-venue view stays.
- **Big-print rail.** A compact pinned strip of the last N `huge`/`whale` prints
  (venue, side, $, px, age) above the tape — the don't-miss-the-block surface.
- **Liquidation tier.** Liquidations (already flowing) get their own notional tiers
  and emphasis; keep the ESTIMATED/model label where a venue sends only notional.
- **Optional audio (default OFF).** A toggle plays a short Web-Audio ping (no assets)
  on `huge`/`whale` prints and a distinct one on large liquidations; volume control.
  Labeled a UX aid, NOT a signal. Muted by default; state persisted.
- Display toggles: cumulative $, time-ago, avg price. Honesty line names which legs
  feed the merged tape and that tiers/audio are conventions, not signals.

### DOM ladder (DomView rework on the T-2 full local books)

- **Depth size bars.** Per price level, a bar proportional to resting qty (bid/ask),
  read from the FULL local book (BinanceBookSync / OkxBookSync / CoinbaseBookSync /
  bybit), not just top-of-book. Book maintained full, display windowed (stated).
- **Cumulative depth.** Optional running sum from mid outward (column or curve) —
  where liquidity stacks.
- **Wall highlight.** Levels flagged by WallsLedger (>= K x p95 sustained) get a
  marker — this is where tape-reading meets resting liquidity (cross-reference, do
  not merge the stores).
- **Trade imprint at price.** Rolling-window executed buy vs sell volume bucketed by
  price level, painted on the ladder (a mini bid/ask volume-at-price) — the tape-
  meets-DOM reading surface. Window configurable; descriptive.
- **Spread / mid / imbalance.** Mid marker, spread in ticks + bps, top-of-book
  imbalance %, and a depth-imbalance readout (sum bid vs ask within N ticks).
- **Source select.** Default single-venue (bybit linear). An AGGREGATED ladder option
  merges enabled SAME-QUOTE (USDT) legs onto the primary tick grid — display-only,
  loudly caveated (cross-venue/cross-quote depth is an approximation, never a merged
  truth); different-quote legs are excluded from the sum, not silently rescaled.
- Tick-group selector (existing) drives row granularity.

### check_terminal groups (mandatory adds)
TapeAggregator merge math (same-venue run merges; price/side/venue/window boundary
closes; VWAP avg px); size-tier classification at boundary notionals; merged
multi-venue time-ordering + spot/perp tag + enabled-leg filter; big-print rail last-N
selection; liquidation tier; DOM depth-bar log scaling; cumulative-depth running sum;
trade-imprint-at-price bucketing (buy/sell split, window prune); depth-imbalance
within-N-ticks math; aggregated-ladder same-quote-only grid merge (a non-USDT leg is
excluded, not rescaled).

## 4j. T-4 Wave 1 contracts — Truth (binding; probes 2026-07-26)

Wave 1 of T-4 is robustness + signal-to-noise: make the instrument stop accusing
itself of being broken when it is healthy, make what it drops observable, and stop
the two panels that drown their own signal. Everything below is live-descriptive
(§0.1); the tape floor and the news relevance mode are labeled DISPLAY projections,
never filters at ingest.

### Control frames are not dropped frames (R2)

- **`adapter.isControlFrame(rawText) -> boolean` — OPTIONAL, pure, total.** Given the
  RAW frame text a `JSON.parse` just rejected: is this the venue's own non-JSON
  keepalive REPLY rather than a malformed frame? Consulted **inside** livewire's parse
  catch, never before it — a pre-parse predicate would run on every frame across 7
  legs of 100 ms book updates, while inside the catch the happy path is byte-identical.
- **Exact equality only.** The measured wire is the byte string `pong`. A fuzzy match
  (`trim`/`includes`/case-fold) would swallow a genuinely corrupt frame that happens to
  contain "pong" — destroying the signal the drop counter exists to raise.
- **Who may declare it is MEASURED, not guessed.** 2026-07-26, 180 s live: OKX sent 7
  non-JSON frames per leg; bybit, bybit-spot, binance-fut, binance-spot and coinbase
  sent **0 of 11,648**. Only `makeOkxAdapter` and `makeOkxBooksAdapter` declare the
  predicate; check group 74 pins the ABSENCE on the others so a speculative add fails CI.
- **A control frame stamps liveness, is never counted, and never reaches `onMessage`.**
  It is not data — so it also never retracts the amber `stale` state and never resets
  the dead-man timer. See the split below.
- **A keepalive reply that happens to be VALID JSON takes the same rule.** Bybit v5
  answers our `{op:'ping'}` with `{success, ret_msg:'pong', op:'ping'}` (real capture:
  fixture `t4_bybit_pong` — note `op` echoes back `'ping'`, so `ret_msg` is the
  identifying field). It parses, so it can never reach `isControlFrame`; it lands in
  the adapter's `onMessage`, which routes it to **`api.markControlAlive()`**, never
  `api.markAlive()`. Same fact, two transports, one rule — and this is the one that
  matters most, because Bybit is the PRIMARY venue (§2) and the measured-flakiest leg.
- **Split liveness in livewire — required, not optional, and only ONE clock is a
  verdict.** `lastDataAt` (data frames only) drives **both** `stale` and the `DEAD_MS`
  force-reconnect. `lastAliveAt` (data frames AND control frames) is **diagnostic
  only**: it appends "(socket still answering)" to the amber message and never
  retracts it. A single stamp would let a leg whose subscription died pong every
  15–25 s and keep BOTH the amber chip and the 40 s watchdog from ever firing — the
  silent-stale hole Module D exists to close, on exactly the two venues that answer
  our ping. Letting a keepalive clear `stale` is the same error one level up: the chip
  would go green with "live feed recovered" over a feed that had delivered nothing,
  a positive claim about DATA that a pong cannot support. **Only a data frame retracts
  amber.** Adapters with no control frames stamp both clocks together through
  `markAlive()`, so their behavior is unchanged.
- **`scripts/verify_wire_live.mjs` holds the identical discipline** — same predicate,
  same place, same two liveness stamps, with `ctrl` / `pDrop` / `alive` / `cAlive`
  columns per venue (`alive` = data stamps, `cAlive` = keepalive stamps; a venue with
  `alive=0, cAlive>0` is precisely the dead subscription the split exists to catch).
  It must move in the same commit or the L2 probe and production diverge.

### Socket-close telemetry (R1)

- **`api.onClosed({code, reason, clean, by})` — OPTIONAL, same guarded/try-wrapped
  shape as `onDropped`.** `ws.onclose` used to discard the CloseEvent, so the venue's
  own close code was unobservable and "the leg dropped" could not be told apart from
  "the feed stalled".
- **`by` names the closer: `'us'` | `'watchdog'` | `'venue'`.** `handle.close()` (a
  symbol switch) and the watchdog's own forced reconnect travel through the SAME
  `ws.onclose`; only `'venue'` is a venue drop, and the leg wiring filters on it.
- **Surfaces are the EXISTING ones.** `makeHealthCounter` kind `'socketClose'`, subKey
  `<leg>/<code>`; the per-leg conn-chip **tooltip**; `__BTCQ_TERMINAL_DEBUG.health()`.
  Never the health chip text: a reconnect that already recovered is history, not a
  current defect, and a permanently-on chip is the exact noise R2 removes.
- **`document.visibilityState` is captured in `terminal.js`, not livewire** — livewire
  must stay constructible in Node (the check groups drive it with a WS stub).
- **Bybit v5 `pingMs` 20000 → 15000** on both legs. Bybit is the only venue here whose
  client ping IS the server's contract (≲20 s); at 20 s the connection was only as
  punctual as the event loop. It is a **jitter margin, and the theory it came from is
  REFUTED**: at 15 s both legs still went stale, and the captured close reads
  `1006 / reason "" / wasClean false / visibilityState "visible"` — an abnormal
  TRANSPORT close, not a gateway ping timeout (which arrives as 1000/1001 *with* a
  reason), with background-tab throttling excluded. One episode produced no close at
  all. 15 s stays because the margin is correct; lowering it further is **not
  indicated**. See AUDIT_LOG 2026-07-26/27.

### Tape floor (signal, not dust)

- **`DEFAULTS.tapeMin` 0 → 10000** — this repo's OWN taxonomy cut (`CvdStore`: "≤$10k
  retail, ≤$100k mid, ≤$1M large, >$1M whale"), not an invented number.
  Measured 2026-07-27, 180 s live, all six trade legs into the production
  composition: 1,139 blocks (6.3/s), median block $100, p90 $4.4 k, p99 $41 k; floor
  off, the 60 newest blocks span 16.5 s. **4.7 %** of blocks clear $10 k, so the same
  60 rows cost ~1,290 blocks of memory. $100 k (`SIZE_TIER_DEFAULTS.sig`) was
  rejected on the same measurement: 0.18 % clear it, ~1.5 h to fill the panel.
- **The aggregator ring is the binding constraint, not the DOM budget — `size` 400 →
  1500.** `filterTapeRows`/`tapeFloorSummary` SELECT from `tapeAgg.list()` at render
  time; the floor can never reach further back than the ring holds. A 400-block ring
  is ~80 s of tape at the measured rate and could offer only ~15 rows to the 60-row
  box. Inert with the floor OFF (the panel still renders the 60 NEWEST blocks), and
  the per-trade path now reads `TapeAggregator.lastClosed()` — O(1) via
  `makeRing.newest()` — instead of copying the whole ring on every print.
  End-to-end on the live page, 180 s, ring at capacity: **60 of 60 rows rendered**,
  tiers `{baseline 55, sig 1, large 4}` — 5 rows at/above `sig` against 0 before —
  with the caption reading "below $10.0k · 1,372 blocks (1,792 prints) · $1.41M = 17 %
  of tape $ · 15 % buy — filtered from view, not discarded".
  **[SUPERSEDED 2026-07-27]** the first draft of this section claimed "the 60-row
  window widens 5.4 s → 151 s and carries 5 rows at/above `sig` instead of 0". That
  was unreachable with a 400-block ring at any floor value; retracted, not rewritten.
- **`tapeFloorSummary(rows, opts)` (pure).** The sub-floor residue of the SAME
  market/venue/minN projection `filterTapeRows` applies. The floor FILTERS, it never
  DISCARDS — the hidden volume is stated in one caption line. Boundary is
  `filterTapeRows`' verbatim `notional < minN`; `null` when the floor is off or
  nothing fell below (a "0 hidden" would imply a floor that is not doing work).
  Invariant: `kept + hidden === rows passing market/venue`.
- **`TIER_ALPHA.baseline` 0.0 → 0.06** — presentation only. At 0 the log-notional bar
  computed for every row was never drawn, so the whole sub-`sig` band rendered as
  untinted filler.
- **The default is PERSISTED, so the change is migrated and STATED.** `settingsVer: 4`
  is written into the settings blob; a pre-T4 profile (no `settingsVer`) whose stored
  floor is `0` adopts the new default once and the tape panel says so until the user
  touches the input. A non-zero stored floor is an explicit choice and is never touched.

### News relevance (visible projection, never a hidden filter)

- **`normalizeToaNews` filters NOTHING.** It carries the evidence: `coins` (content-
  derived `suggestions[].coin`) and `accountCoins` (`isAccountMapped:true`, kept
  separate — that maps the POSTER, not the text). A filter at ingest could not be
  switched off; that is the definition of a hidden filter.
- **`newsRelevance(item) -> {crypto, btc, why}`** — evidence ladder, ordered by measured
  strength (n=200 live rows, 2026-07-26): `coins` (158/200 present, BTC on 18/200) →
  `symbols` (18/200 present, BTC on **0/200** — the old view's BTC test was dead code)
  → `press` (a crypto-press publisher stamped into the title itself) → `keyword` (a
  stated in-file lexicon) → `venue` (Upbit/Bithumb notices: every row IS a market event,
  and their Korean titles no English lexicon can reach). The transport `source` is NOT
  evidence — 'Blogs' carries CDC and WHITEHOUSE alongside COINDESK.
- **`filterNewsRows(items, {mode})` returns the kept rows AND the counts the caption
  states**; `kept + filtered === total` always, and `mode:'all'` gives every row back.
- **Toggle is a visible panel control** (default `crypto`, persisted). The caption
  states the RENDERED count, not the kept count — the list caps at 18 rows.
- **Known, measured false positives are stated, not patched:** the feed's own mapper
  matches bare tickers in ordinary prose ("SAVE AMERICA **ACT**" → `ACT`), 3/200 ≈ 1.5%
  across two independent pulls, against 6/200 genuine crypto posts only that rung
  catches. Each row carries its evidence rung in a tooltip.

### Local-only strip

- **Fold rule is "this panel has no datum it could render", NOT `apiUp === false`.**
  Folding on the API alone would hide working features: the auction panel still works
  from an HF archived day, and key levels has a LIVE half (this session's own IB) that
  needs no API. `apiUp === null` (probing) and REPLAY fold nothing.
- **Two strips, two causes.** The collector-API strip and the econ strip are separate
  because econ is a missing LOCAL FILE (`make econ`, no CORS on faireconomy) — one
  strip with one explanation would misstate it.
- **`API_OFFLINE_NOTE` stays ONE constant and is REUSED**, not copied; the four
  in-place render sites remain as the fallback for an unfolded panel.
- **A folded panel's own §0.7 gap prose must survive the fold.** The key-levels panel
  carries live-IB reasons the collector API does not cause ("IB withheld — the page
  did not witness the 00:00 UTC open", "IB forming", "no prints yet this session").
  The strip restates that reason verbatim and calls the folded feature `key levels`,
  not "key levels (registry half)" — the whole panel is gone, both halves.
- **The strip claims NO grid area; it is PLACED at paint time in the cell of a panel
  it actually folded.** A static `area-klev` was wrong: the strip turns on when ANY of
  auct/lvls/klev has nothing to render, while only klev's own panel folds on
  `st.klev`, and a live IB (no API needed) makes `st.klev` false while the API is
  down. The two then occupied one cell, stacked, with the strip painting behind the
  key-levels panel — the offline explanation invisible, the inversion of its purpose.
  With no `area-*` class the CSS fallback is grid auto-placement, which cannot
  overlap. Check group 77 pins "no two elements share an `area-*` class".
- **Folding must never hide a navigation anchor.** `.folded-local` is `display:none`
  and a hidden element has no layout box, so the AUCTION section anchor moved off the
  auction-profile panel onto a zero-size `.sec-anchor` span in the section eyebrow
  (the idiom `#sec-orderflow` already used). Group 77 pins that no foldable panel
  carries a `sec-*` id.
- Visibility is class-driven (`.local-only` / `.local-on` / `.folded-local`), never
  `[hidden]`: `applyCollapse()` owns that property on every `[data-sec]` node.
- **Both halves are TRI-state.** `apiUp === null` (probing) folds nothing; `econ` gets
  the same treatment via an `econProbed` flag, because `econData` is `null` both
  before and after the local read and the strip was otherwise asserting "no local econ
  file" for ~1 s of every page load (§0.7: not yet asked ≠ absent).

### check_terminal groups (mandatory adds)
74 `isControlFrame` contract + livewire's control-frame branch (exact `pong`, OKX-only
by measurement, a control frame is neither dropped nor delivered, a malformed frame
still counts, an absent predicate is byte-identical to today, a throwing predicate
counts) **plus the liveness half, driven through the real watchdog on a stubbed clock**
— a keepalive stamps the answering clock, emits NO status, never retracts amber, and
never saves a pong-only socket from the `DEAD_MS` force-reconnect; only a data frame
recovers, once; and Bybit's JSON pong (real captured frames, `t4_bybit_pong`) routes
through `markControlAlive` while a `tickers` frame still stamps the data clock;
75 `tapeFloorSummary` (blocks vs prints, buy/sell split, share, the verbatim `<`
boundary, `null` when off or empty, and the kept+hidden invariant); 76
`newsRelevance` / `filterNewsRows` over REAL captured rows (`t4_toa_news`); 77 layout
invariants read out of the shipped markup — no two elements share an `area-*` class,
the local-only strips claim none, every `area-*` resolves to a `grid-area` rule, and
no panel the strip folds carries a section-nav anchor id. Plus extensions:
`makeHealthCounter` counts `'socketClose'` separately from `'droppedFrame'`; the
`startLeg` wiring group gains the `onClosed` composition and its `by !== 'venue'`
filter. **CI blind spot named in the group comments:** the browser harness's clean N5
pass runs under `?replay=1`, where livewire is not in the transport at all — which is
exactly why the OKX-pong regression reached production unseen — and `renderLocalOnly`
returns early in replay, so group 77 pins its structure because nothing else can.

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

**Continuity is the budget** (first real-store L3 finding: one slept-Mac night = 23 gaps
>30 s, ~6 h lost — reported, not filled). For 24/7 accumulation: `caffeinate -s make
collector` while on AC, or install the launchd agent
(`scripts/com.btcquant.collector.plist.example` — KeepAlive, clean-flush on unload) and
prevent AC sleep. `make check-ticks` weekly is the standing quality ritual.

## 7. Verification — the three-layer system

Layer 0 (static, every commit): `python -m pytest` (incl. collector tests; network-free);
`node --check` on every dashboard JS file; `node scripts/check_terminal.cjs` (fixture
smoke: adapters + stores + O-3/O-4 normalizers/builders replayed over the REAL captured
frames/responses, 77 assertion groups incl. the T-1 §4g + T-2 §4h + T-3 §4i + N1/N5 adds — a CI build gate since
O-5, §4e.3).

- **L1 — deterministic browser harness** (`make verify-browser`,
  `scripts/verify_terminal_browser.py`): serves the repo, opens
  `terminal.html?replay=1` in headless Chromium. Replay mode
  (`dashboard/terminal-replay.js`) drives the UNTOUCHED adapters with the captured
  fixture frames on a rebased deterministic clock — chips read **replay**, the banner
  gains a REPLAY MODE flag (never masquerades as live, §0). Asserts: zero console/page
  errors, 4/4 chips, store counts advancing, every canvas non-blank, honesty flags
  present; writes before/after screenshots to `reports/verify/` (gitignored). This is
  how UI changes are SEEN without waiting for a human browser pass. Stated scope
  limit: L1 loads and screenshots, it drives no interactions — the ⌘K palette,
  workspace presets, and the symbol-picker popover (disabled in replay by design, so
  live-only) have no automated witness; the short fixture also cannot produce a
  finished-bar ⌟ marker, a walls entry, or a VPIN bucket (honestly empty there;
  their logic is covered at L0). An interaction pass is future work.
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
