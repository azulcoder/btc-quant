// terminal-adapters.js — exchange WS/REST adapters for the orderflow terminal.
//
// DESIGN-orderflow-terminal.md §4 + §4b contract: each make*Adapter(symbolOrProduct,
// sink[, opts]) returns a makeSocket-compatible descriptor { url, pingMs?, subscribe(ws),
// ping?(ws), onMessage(msg, api) } (the shared socket skeleton lives in livewire.js — extracted
// verbatim from app.js; adapters never manage reconnection themselves). `sink(evt)`
// receives ONLY the normalized event shapes below — the single vocabulary the stores
// ever see (DESIGN §4):
//
//   { kind:'trade', ex, ts, price, qty, aggressorBuy, id }
//   { kind:'depth', ex, ts, bids:[[p,q]…], asks:[[p,q]…], isSnapshot }  // sorted best-first
//   { kind:'liq',   ex, ts, side:'long'|'short', price, qty, notionalUsd }
//   { kind:'mark',  ex, ts, mark, index, fundingRate, nextFundingTs }
//   { kind:'oi',    ex, ts, oi }
//
// ts is ALWAYS epoch milliseconds (int); price/qty are ALWAYS Number() — exchanges
// send numeric strings on the wire. `ex` short codes: 'bybit' | 'binancef' |
// 'coinbase' | 'okx' (the first three are what the collector writes to DuckDB,
// DESIGN §3 schema; 'okx' is a browser-terminal-only leg added in O-2, §4b).
//
// Honesty rails (DESIGN §0, inherited from app.js):
//   - LIVE-DESCRIPTIVE only. Nothing normalized here ever feeds a backtest or the
//     OOS harness (§0.1). Keyless public endpoints only — no signing, no accounts (§0.2).
//   - Aggressor-side conventions are PER-EXCHANGE and normalized explicitly (§0.6):
//     Bybit publicTrade `S` is already the TAKER side (use as-is); OKX trades
//     `side` is likewise the TAKER side (use as-is — Bybit family); Coinbase
//     market_trades `side` is the MAKER side (invert it — DEVELOPMENT.md §5 gotcha).
//     Each adapter documents its convention inline; the fixture smoke asserts it.
//   - Liveness: adapters call api.markAlive() on heartbeat-ish frames ONLY — never on
//     trades. A quiet tape is a quiet market, NOT a stalled socket (app.js watchdog
//     rule); marking trades alive would mask a genuinely dead subscription.
//   - Coded against REAL captured frames in scripts/fixtures_ws.json (DESIGN §2), not
//     remembered API docs. Wire realities encoded below: Bybit `tickers` sends one
//     snapshot then PARTIAL deltas (only changed fields — must merge); Bybit
//     `orderbook.200` (§4b upgrade from .50 — deeper heatmap range) sends snapshot
//     then deltas where qty "0" deletes a level; Binance `depth20@100ms` frames are
//     each a FULL 20-level snapshot; Coinbase `market_trades` arrives as snapshot
//     then update batches, newest-first; OKX `books` sends action:'snapshot' then
//     'update' frames where sz "0" deletes — and ALL OKX sizes are in CONTRACTS,
//     not BTC (ctVal 0.01 — fixtures `_okx_ctval_note`).
//
// No DOM access, no globals beyond the ONE export — unit-testable in Node via the
// quant.js dual-export pattern (consumed by scripts/check_terminal.cjs).
'use strict';

(function (global) {
  // ─── Shared normalization helpers ────────────────────────────────────────

  /**
   * Normalize one raw [price, qty, …] string-tuple array into sorted Number pairs.
   * bids sort DESC (best = highest bid first), asks ASC (best = lowest ask first)
   * — "sorted best-first" per the DESIGN §4 depth contract. qty 0 entries are
   * KEPT (Bybit/OKX delta semantics: qty "0" means DELETE this level — the STORE
   * applies deltas against its book; the adapter only normalizes + sorts).
   * Tuple tails beyond [0]/[1] (e.g. OKX's deprecated field + nOrders) are ignored.
   * `scale` multiplies qty: 1 (default) for venues quoting base units
   * (Bybit/Binance BTC), ctVal for OKX where `sz` is in CONTRACTS (§4b) — note
   * a "0" tombstone survives scaling (0 × ctVal = 0), so deletes still work.
   */
  function normLevels(raw, desc, scale) {
    const k = scale === undefined ? 1 : scale;
    const out = [];
    for (const lvl of raw || []) {
      const p = Number(lvl[0]), q = Number(lvl[1]) * k;
      if (!Number.isFinite(p) || !Number.isFinite(q)) continue;   // never emit NaN levels
      out.push([p, q]);
    }
    out.sort(desc ? (a, b) => b[0] - a[0] : (a, b) => a[0] - b[0]);
    return out;
  }

  // ─── Bybit v5 linear — PRIMARY feed (DESIGN §2: all four topics verified live) ──
  //
  // One socket carries the whole perp picture: publicTrade (tape/footprint/CVD),
  // orderbook.200 (DOM/agg book + the O-2 book heatmap — §4b upgraded from .50:
  // 200 levels/side give the heatmap a usable price range; snapshot/delta
  // semantics are IDENTICAL, proven by fixtures bybit_orderbook200_*), tickers
  // (mark/index/funding/OI header stats),
  // allLiquidation (liq feed). Bybit is primary because Binance Futures WS
  // topic-filters trades/mark on this network (§0.2) — we state what the wire
  // actually delivers rather than pretending the documented stream list works.
  function makeBybitAdapter(sym, sink) {
    // Bybit `tickers` wire reality (fixtures: bybit_tickers_snapshot/_delta): the
    // first frame is a full snapshot, every later frame a PARTIAL delta carrying
    // only the fields that changed (many deltas are bid1/ask1-only). We keep the
    // merged view here and emit mark/oi from it — emitting a delta's raw fields
    // alone would produce mark events with holes.
    let tickerView = null;

    return {
      url: 'wss://stream.bybit.com/v5/public/linear',
      pingMs: 20000,   // Bybit v5 requires a client ping ≲ 20s or the server drops the conn
      subscribe(ws) {
        ws.send(JSON.stringify({
          op: 'subscribe',
          args: [
            'publicTrade.' + sym,
            'orderbook.200.' + sym,   // §4b: deeper book for the heatmap (was .50 in O-1)
            'tickers.' + sym,
            'allLiquidation.' + sym,
          ],
        }));
      },
      // v5 public heartbeat is an op frame, not a WS protocol ping.
      ping(ws) { ws.send(JSON.stringify({ op: 'ping' })); },
      onMessage(msg, api) {
        if (!msg) return;
        // Pong reply → liveness. (Wire shape is {success,ret_msg:'pong',op:'ping'};
        // some gateways answer op:'pong' — accept both.) Subscribe acks (fixture
        // bybit_sub_ack: {success,op:'subscribe'}) carry no data → swallow.
        if (msg.op === 'pong' || msg.ret_msg === 'pong') { if (api.markAlive) api.markAlive(); return; }
        if (msg.op) return;
        if (!msg.topic || msg.data === undefined) return;

        if (msg.topic.indexOf('publicTrade.') === 0) {
          // Fixture reality: every publicTrade frame arrives type:'snapshot' even in
          // steady streaming — the type field is meaningless here; normalize all items.
          // §0.6: Bybit `S` ('Buy'/'Sell') is already the TAKER (aggressor) side —
          // use as-is, NO inversion (unlike Coinbase below). Trades do NOT markAlive.
          for (const t of msg.data || []) {
            const price = Number(t.p), qty = Number(t.v);
            if (!Number.isFinite(price) || !Number.isFinite(qty)) continue;
            sink({
              kind: 'trade', ex: 'bybit',
              ts: Number(t.T),                 // per-trade ms timestamp from the wire
              price, qty,
              aggressorBuy: t.S === 'Buy',     // taker side, verbatim (§0.6)
              id: String(t.i),                 // Bybit trade ids are UUID strings
            });
          }
        } else if (msg.topic.indexOf('orderbook.') === 0) {
          // Depth-AGNOSTIC prefix on purpose: we subscribe orderbook.200 (§4b) but
          // the normalization is identical at any depth — the fixture-proven .50
          // frames and live .200 frames (fixtures bybit_orderbook200_*: same
          // snapshot/delta/tombstone shape, just 200 levels/side) share this path.
          // snapshot = full replace; delta = sparse changes where qty "0" DELETES a
          // level (fixture bybit_orderbook_delta shows ["61844.80","0"]; the .200
          // delta fixture shows ["62011.30","0"]). The BookStore
          // applies deltas against its last snapshot — here we only normalize + sort,
          // keeping the qty-0 tombstones intact so the store can remove those levels.
          sink({
            kind: 'depth', ex: 'bybit',
            ts: Number(msg.ts),
            bids: normLevels(msg.data.b, true),    // best bid (highest) first
            asks: normLevels(msg.data.a, false),   // best ask (lowest) first
            isSnapshot: msg.type === 'snapshot',
          });
        } else if (msg.topic.indexOf('tickers.') === 0) {
          // tickers ticks ~10/s regardless of trade activity → a real liveness signal.
          if (api.markAlive) api.markAlive();
          if (msg.type === 'snapshot') tickerView = Object.assign({}, msg.data);
          else if (tickerView) Object.assign(tickerView, msg.data);   // merge partial delta
          else return;   // delta before any snapshot (shouldn't happen) — nothing to merge into
          // Emit from the MERGED view whenever it has the fields (DESIGN §4). Most
          // deltas only move bid1/ask1 so mark/oi values repeat — the store just
          // overwrites its latest; duplicate emits are cheap and keep this stateless
          // for consumers.
          const mark = Number(tickerView.markPrice);
          if (Number.isFinite(mark)) {
            sink({
              kind: 'mark', ex: 'bybit', ts: Number(msg.ts),
              mark,
              index: Number(tickerView.indexPrice),
              fundingRate: Number(tickerView.fundingRate),
              nextFundingTs: Number(tickerView.nextFundingTime),
            });
          }
          const oi = Number(tickerView.openInterest);
          if (Number.isFinite(oi)) {
            sink({ kind: 'oi', ex: 'bybit', ts: Number(msg.ts), oi });
          }
        } else if (msg.topic.indexOf('allLiquidation.') === 0) {
          // §3 schema convention: side = the LIQUIDATED position. Bybit prints the
          // forced order: a printed 'Buy' is the forced BUY-BACK closing a SHORT →
          // side:'short'; printed 'Sell' is a forced sell-out of a LONG → 'long'.
          // Reading the print as the position side would flip every liq label.
          for (const it of msg.data || []) {
            const price = Number(it.p), qty = Number(it.v);
            if (!Number.isFinite(price) || !Number.isFinite(qty)) continue;
            sink({
              kind: 'liq', ex: 'bybit',
              ts: Number(it.T),
              side: it.S === 'Buy' ? 'short' : 'long',
              price, qty,
              notionalUsd: price * qty,   // linear contract: qty is in BTC, price in USDT
            });
          }
        }
      },
    };
  }

  // ─── Binance Futures depth — second agg-book leg (DESIGN §2) ────────────────
  //
  // HONEST SCOPE (§0.2 empirical constraint): on this network Binance Futures WS
  // TOPIC-FILTERS — depth20@100ms flows (112 frames/12s captured) while aggTrade/
  // markPrice/ticker on the same socket, same subscribe, deliver ZERO frames
  // (sub-ack only). So this adapter carries depth ONLY, intentionally: Binance
  // trades are absent by reality, not by omission, and mark/OI come from the REST
  // poller below. We do not pretend the documented stream list is available.
  function makeBinanceDepthAdapter(sym, sink) {
    const stream = String(sym).toLowerCase() + '@depth20@100ms';
    return {
      // Combined-stream endpoint: the subscription IS the URL — frames arrive
      // wrapped {stream, data} (fixture binancef_depth20).
      url: 'wss://fstream.binance.com/stream?streams=' + stream,
      // Nothing to send: the URL already subscribed us. makeSocket calls
      // subscribe() on every (re)open, so this must exist even as a no-op.
      subscribe() { /* URL-subscribed — no subscribe frame needed */ },
      // No client ping: Binance sends protocol-level pings and the browser
      // answers pongs automatically; an app-level ping frame would be rejected.
      onMessage(msg, api) {
        if (!msg || !msg.data || msg.data.e !== 'depthUpdate') return;
        const d = msg.data;
        // Every depth frame is a liveness proof: this stream ticks 10/s whether or
        // not anyone trades, so frame-flow == socket health (and it's the ONLY
        // topic this socket carries, §0.2 — there is no quieter channel to prefer).
        if (api.markAlive) api.markAlive();
        // Wire reality (fixture): each frame is a FULL top-20 snapshot, not a diff —
        // isSnapshot:true every time; the store replaces its Binance book wholesale.
        sink({
          kind: 'depth', ex: 'binancef',
          ts: Number(d.E),                       // event time (ms)
          bids: normLevels(d.b, true),           // best bid (highest) first
          asks: normLevels(d.a, false),          // best ask (lowest) first
          isSnapshot: true,
        });
      },
    };
  }

  // ─── Coinbase Advanced Trade — spot tape leg (conventions proven in app.js) ──
  //
  // Mirrors app.js's coinbaseAdapter: market_trades for the tape, heartbeats
  // (~1/s) for liveness. MUST subscribe within 5s of connect; channels go stale
  // ~60–90s without updates. The ticker channel is NOT subscribed here — the
  // terminal's spot leg only needs real prints with size + side.
  function makeCoinbaseAdapter(productId, sink) {
    // Snapshot-seeding state (same rule as app.js onLiveTrades): Coinbase re-fires
    // the full market_trades snapshot on every re-subscribe/reconnect. Seed from
    // the FIRST snapshot only; ignore later ones so a reconnect never re-dumps the
    // whole batch into the tape/CVD. Updates are deduped by monotonic trade_id.
    let seeded = false, lastTradeId = -1;

    // Batches arrive NEWEST-first on the wire (fixture: trade_id descending).
    // Emit oldest→newest so downstream accumulators (CVD, footprint) see time order.
    function emitTrades(trades) {
      const norm = [];
      for (const tr of trades || []) {
        const price = Number(tr.price), qty = Number(tr.size);
        const idNum = Number(tr.trade_id), ts = Date.parse(tr.time);
        if (!Number.isFinite(price) || !Number.isFinite(qty) || !Number.isFinite(idNum)) continue;
        norm.push({ price, qty, idNum, ts, id: String(tr.trade_id), side: tr.side });
      }
      norm.sort((a, b) => a.idNum - b.idNum);   // oldest → newest (trade_id is monotonic)
      let maxId = -1;
      for (const t of norm) {
        if (t.idNum <= lastTradeId) continue;   // dedupe across overlapping batches
        if (t.idNum > maxId) maxId = t.idNum;
        sink({
          kind: 'trade', ex: 'coinbase',
          ts: t.ts, price: t.price, qty: t.qty,
          // §0.6 / DEVELOPMENT.md §5 gotcha: Coinbase `side` is the MAKER's side,
          // not the aggressor (verified live: side=BUY prints tick DOWN, side=SELL
          // tick UP). Aggressor is the INVERSE: side=SELL ⇒ a resting ask was
          // lifted by an aggressive BUYER → aggressorBuy:true. Reading `side` as
          // the aggressor flips the tape coloring and negates the CVD.
          aggressorBuy: t.side === 'SELL',
          id: t.id,
        });
      }
      if (maxId > lastTradeId) lastTradeId = maxId;
    }

    return {
      url: 'wss://advanced-trade-ws.coinbase.com',
      pingMs: 20000,
      subscribe(ws) {
        ws.send(JSON.stringify({ type: 'subscribe', product_ids: [productId], channel: 'market_trades' }));
        ws.send(JSON.stringify({ type: 'subscribe', product_ids: [productId], channel: 'heartbeats' }));
      },
      // Coinbase has no client ping frame for this feed; the heartbeats channel is
      // the keepalive. Re-assert the subscription as a liveness nudge (app.js rule).
      ping(ws) { ws.send(JSON.stringify({ type: 'subscribe', product_ids: [productId], channel: 'heartbeats' })); },
      onMessage(msg, api) {
        // heartbeats (~1/s) are the steady liveness signal → feed the watchdog, stop.
        // Trades deliberately do NOT markAlive — a quiet spot tape is normal (§0 rail).
        if (msg.channel === 'heartbeats') { if (api.markAlive) api.markAlive(); return; }
        if (msg.channel !== 'market_trades' || !Array.isArray(msg.events)) return;
        for (const ev of msg.events) {
          if (ev.type === 'snapshot') {
            if (seeded) continue;         // reconnect snapshot — already seeded, skip
            seeded = true;
            emitTrades(ev.trades);
          } else {
            if (!seeded) continue;        // wait for the seed snapshot first (app.js rule)
            emitTrades(ev.trades);
          }
        }
      },
    };
  }

  // ─── OKX v5 public — deeper agg-book leg + labeled per-exchange CVD (O-2, §4b) ──
  //
  // Subscribes `books` (full snapshot then sparse updates) + `trades` for the
  // instId (BTC-USDT-SWAP). Per §4b bootstrap rules, OKX feeds the AggBook and a
  // per-exchange, per-LABELED CvdStore only — OKX trades never enter
  // FootprintStore/ProfileStore (§0.7: no mixed-venue bars).
  //
  // UNIT RAIL (§4b, honesty-critical): OKX SWAP sizes — trades `sz` AND books
  // level `sz` — are in CONTRACTS, not BTC. BTC-USDT-SWAP ctVal = 0.01 BTC
  // (verified via /api/v5/public/instruments 2026-07-03, pinned in fixtures
  // `_okx_ctval_note`). qty = Number(sz) × ctVal EVERYWHERE below; skipping the
  // multiply would overstate OKX flow 100× against the BTC-denominated
  // Bybit/Binance/Coinbase legs and poison the merged book + CVD.
  function makeOkxAdapter(instId, sink, opts) {
    const o = opts || {};
    // ctVal is an OPT (a different instId means a different multiplier) but the
    // 0.01 default is the pinned BTC-USDT-SWAP value, not a guess.
    const ctVal = o.ctVal === undefined ? 0.01 : Number(o.ctVal);

    return {
      url: 'wss://ws.okx.com:8443/ws/v5/public',
      // OKX drops sockets idle ~30s and prescribes a PLAIN TEXT 'ping' — NOT a
      // JSON op frame like Bybit's. ~25s keeps us safely inside the window.
      pingMs: 25000,
      subscribe(ws) {
        ws.send(JSON.stringify({
          op: 'subscribe',
          args: [
            { channel: 'books', instId: instId },
            { channel: 'trades', instId: instId },
          ],
        }));
      },
      // Keepalive quirk, documented so nobody "fixes" it: OKX answers plain-text
      // 'pong'. makeSocket (livewire.js) JSON.parses EVERY incoming message and
      // silently drops parse failures, so the 'pong' never reaches onMessage and
      // needs no branch below — safely ignored BY CONSTRUCTION, not by accident.
      // Liveness therefore comes from books/trades data frames (markAlive below);
      // a genuinely stalled feed still trips the watchdog because nothing else
      // marks this socket alive.
      ping(ws) { ws.send('ping'); },
      onMessage(msg, api) {
        if (!msg) return;
        // Event frames carry no data: sub acks (fixture okx_sub_ack:
        // {event:'subscribe', arg, connId}) and {event:'error'} → swallow.
        if (msg.event) return;
        if (!msg.arg || !Array.isArray(msg.data)) return;
        const ch = msg.arg.channel;
        if (ch !== 'books' && ch !== 'trades') return;

        // Liveness: EVERY books/trades data frame marks alive (§4b contract).
        // books ticks near-continuously for BTC and is the primary signal; a
        // trade frame is equally hard proof the SAME socket is delivering, so
        // marking it too costs nothing. This does not soften the §0 rail (never
        // RELY on trades alone — a quiet tape is a quiet market): if the feed
        // stalls, books goes quiet with it and the watchdog still fires.
        if (api.markAlive) api.markAlive();

        if (ch === 'trades') {
          for (const t of msg.data) {
            const price = Number(t.px), qty = Number(t.sz) * ctVal;   // CONTRACTS → BTC (§4b)
            if (!Number.isFinite(price) || !Number.isFinite(qty)) continue;
            sink({
              kind: 'trade', ex: 'okx',
              ts: Number(t.ts),                // wire ts is a numeric-string ms epoch
              price, qty,
              // §0.6 family: OKX trades `side` ('buy'/'sell') is the TAKER
              // (aggressor) side — use as-is, NO inversion (Bybit convention,
              // NOT the Coinbase maker-side gotcha).
              aggressorBuy: t.side === 'buy',
              id: String(t.tradeId),
            });
          }
        } else {
          // books: action 'snapshot' = full replace; 'update' = sparse changes
          // where sz "0" DELETES a level (fixture okx_books_update carries e.g.
          // ["62009.2","0","0","0"]). The delete happens STORE-side, exactly like
          // the Bybit delta path: we KEEP the tombstone and emit the level with
          // qty 0 (0 × ctVal is still 0) so the BookStore can remove it.
          //
          // Level tuples are [px, sz, deprecated, nOrders] — only px/sz are
          // consumed (normLevels reads [0]/[1] and ignores the tail).
          //
          // `checksum`/`seqId`/`prevSeqId` are deliberately IGNORED: the only
          // remedy for a checksum miss is a resubscribe, and makeSocket already
          // re-subscribes on every reconnect — after which OKX resends a full
          // snapshot. So we RE-SNAPSHOT on reconnect instead of checksum-
          // verifying every frame; the residual failure mode (a silently gapped
          // book between reconnects) is bounded by the stale/dead watchdog.
          const isSnap = msg.action === 'snapshot';
          for (const row of msg.data) {
            sink({
              kind: 'depth', ex: 'okx',
              ts: Number(row.ts),
              bids: normLevels(row.bids, true, ctVal),    // best bid (highest) first; CONTRACTS→BTC
              asks: normLevels(row.asks, false, ctVal),   // best ask (lowest) first; CONTRACTS→BTC
              isSnapshot: isSnap,
            });
          }
        }
      },
    };
  }

  // ─── Binance Futures REST poller — mark/funding (5s) + OI (60s) ─────────────
  //
  // WS markPrice/ticker are topic-filtered on this network (§0.2), so the
  // cross-exchange funding/OI columns come from plain REST polls (DESIGN §2:
  // /fapi/v1/premiumIndex and /fapi/v1/openInterest, responses captured in
  // fixtures). Not makeSocket-compatible on purpose — no socket to babysit;
  // returns { start(), stop() } and the caller owns the lifecycle.
  function makeBinanceRestPoller(sym, sink, opts) {
    const o = opts || {};
    const premiumMs = o.premiumMs || 5000;
    const oiMs = o.oiMs || 60000;
    const base = 'https://fapi.binance.com';
    let premiumTimer = null, oiTimer = null;

    // 8s abort: a poll that outlives its own interval is worthless — kill it and
    // let the next tick try again rather than queueing stale responses.
    async function getJSON(url) {
      const ctrl = new AbortController();
      const t = setTimeout(() => ctrl.abort(), 8000);
      try {
        const res = await fetch(url, { signal: ctrl.signal, headers: { Accept: 'application/json' } });
        if (!res.ok) throw new Error('HTTP ' + res.status);
        return await res.json();
      } finally {
        clearTimeout(t);
      }
    }

    // Silent-skip on failure: a transient REST failure is NOT a terminal state —
    // the next interval retries, and the store's last value simply ages (the UI's
    // staleness display is the store/view's job, not the poller's). No fabricated
    // values, no retry storms.
    async function pollPremium() {
      try {
        // fixture binancef_rest_premiumIndex: {markPrice, indexPrice,
        // lastFundingRate, nextFundingTime, time} — numeric strings + ms ints.
        const j = await getJSON(base + '/fapi/v1/premiumIndex?symbol=' + sym);
        const mark = Number(j.markPrice);
        if (!Number.isFinite(mark)) return;
        sink({
          kind: 'mark', ex: 'binancef',
          ts: Number(j.time),
          mark,
          index: Number(j.indexPrice),
          fundingRate: Number(j.lastFundingRate),
          nextFundingTs: Number(j.nextFundingTime),
        });
      } catch (_) { /* transient REST failure ≠ terminal state — next poll retries */ }
    }
    async function pollOi() {
      try {
        // fixture binancef_rest_openInterest: {openInterest:"107936.535", time}.
        const j = await getJSON(base + '/fapi/v1/openInterest?symbol=' + sym);
        const oi = Number(j.openInterest);
        if (!Number.isFinite(oi)) return;
        sink({ kind: 'oi', ex: 'binancef', ts: Number(j.time), oi });
      } catch (_) { /* transient REST failure ≠ terminal state — next poll retries */ }
    }

    return {
      start() {
        if (premiumTimer || oiTimer) return;   // idempotent — never double the timers
        pollPremium(); pollOi();               // immediate first sample, then steady cadence
        premiumTimer = setInterval(pollPremium, premiumMs);
        oiTimer = setInterval(pollOi, oiMs);
      },
      stop() {
        if (premiumTimer) { clearInterval(premiumTimer); premiumTimer = null; }
        if (oiTimer) { clearInterval(oiTimer); oiTimer = null; }
        // In-flight fetches resolve or abort on their own 8s timer; their sink
        // emits are harmless latest-value overwrites, so no extra cancel plumbing.
      },
    };
  }

  // ─── Export (ONE global + Node dual-export, quant.js pattern) ───────────────
  const ADAPTERS = { makeBybitAdapter, makeBinanceDepthAdapter, makeCoinbaseAdapter, makeOkxAdapter, makeBinanceRestPoller };

  if (typeof module !== 'undefined' && module.exports) module.exports = ADAPTERS;
  if (typeof global !== 'undefined') global.BTCQ_TERMINAL_ADAPTERS = ADAPTERS;
})(typeof globalThis !== 'undefined' ? globalThis : this);
