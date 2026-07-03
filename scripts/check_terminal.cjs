'use strict';
// check_terminal.cjs — fixture smoke for the orderflow terminal (DESIGN-orderflow-terminal.md §4).
//
// Follows the scripts/_parity_eval.cjs pattern: plain node, zero deps. Both
// terminal-adapters.js and terminal-state.js carry the quant.js dual-export
// (`module.exports` alongside the ONE window global), so a plain require() is
// enough — no vm sandbox / window stub needed. This file has NO normalization
// logic of its own: every assertion drives the REAL adapter/store code against
// the REAL captured frames in scripts/fixtures_ws.json (DESIGN §2 — actual
// wire shapes, not remembered docs), so any failure here is a true contract
// break between what the exchanges send and what the stores consume.
//
// What this smoke pins (DESIGN §4 "Fixture smoke" + §0.6 aggressor rails):
//   1. Bybit publicTrade  → aggressorBuy === (S === 'Buy')  (taker side, as-is)
//   2. Coinbase trades    → aggressorBuy INVERTED from `side` (maker-side rule,
//                           DEVELOPMENT.md §5 gotcha) + reconnect snapshot NOT re-seeded
//   3. Bybit orderbook (.50-captured; the adapter routes any depth) → snapshot+
//                           delta through BookStore: sane best(), and a delta
//                           qty-"0" level actually DELETES (tombstone rail)
//   4. Binance depth20    → combined {stream,data} unwrap, full-snapshot semantics
//   5. Bybit tickers      → partial-delta merge: mark/funding persist across a
//                           delta that omits them (the fixture's deltas really do)
//   6. Bybit allLiquidation → printed side inverted to the LIQUIDATED position (§3)
//   7. FootprintStore     → bar delta == Σ signed qty; diagonal imbalance fires on
//                           a constructed 3:1 (and only on finished bars)
//   8. CvdStore           → Σ per-bucket series == overall series at EVERY sample
//   9. ProfileStore       → POC = max-volume level; VAH ≥ POC ≥ VAL; value-area
//                           volume ≈ 70% (±5pp)
//  10. AggBookStore       → two-exchange merge: row.total == Σ row.byEx exactly
//
// O-2 additions (DESIGN §4b "check_terminal.cjs additions", binding list):
//  11. OKX adapter        → descriptor contract (plain-text 'ping', books+trades
//                           subscribe) + trade ctVal math: sz "200" → 2.00 BTC
//                           EXACTLY; taker side as-is (§0.6 Bybit family);
//                           ctVal override opt; markAlive per data frame
//  12. OKX books          → snapshot+update through BookStore, ctVal-scaled
//                           levels, incl. a REAL sz-"0" delete from the captured
//                           update frames (store-side tombstone rail)
//  13. Bybit orderbook.200 → subscribe arg upgraded (.200, §4b), snapshot sane at
//                           200 levels/side through BookStore, delta tombstones delete
//  14. DepthHistoryStore  → empty-book guard, ring bound by construction,
//                           velocity sign (+ on a constructed fill, − on a pull)
//  15. SpoofIcebergDetector → fires on a constructed wall-pull AND iceberg-refill,
//                           stays QUIET on a benign book; every event label:'heuristic'
//  16. LiqHeatmapModel    → exact band math (entry 100, L 10, mmr 0.005 →
//                           long 90.5 / short 109.5), sides correct vs mark,
//                           observed prints passed through UNblended, label:'estimated'
//
// Exit: 0 with one PASS line per group; non-zero with a clear FAIL message
// (plus stack) if any group breaks. Run: node scripts/check_terminal.cjs

const fs = require('fs');
const path = require('path');
const assert = require('assert');

const A = require(path.join(__dirname, '..', 'dashboard', 'terminal-adapters.js'));
const S = require(path.join(__dirname, '..', 'dashboard', 'terminal-state.js'));
const FX = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures_ws.json'), 'utf8'));

// makeSocket's liveApi surface, minus the socket: adapters only ever touch
// markAlive/onStatus here. Frames are fed straight into adapter.onMessage —
// exactly what livewire.js does after JSON.parse.
const nullApi = { markAlive() {}, onStatus() {} };

/** Like nullApi but counts markAlive() calls — the O-2 OKX groups pin the
 *  "every books/trades data frame marks alive" contract (§4b), which is that
 *  adapter's ONLY liveness source (its 'pong' is plain text and never survives
 *  makeSocket's JSON.parse). */
function countingApi() {
  const api = { alive: 0, markAlive() { api.alive++; }, onStatus() {} };
  return api;
}

/** Capture everything an adapter's subscribe()/ping() writes to the socket —
 *  frames are JSON.parse'd when possible, kept as raw strings otherwise (the
 *  OKX keepalive is deliberately a NON-JSON plain-text 'ping', §4b). */
function captureWs() {
  const sent = [];
  return { sent, ws: { send(s) { try { sent.push(JSON.parse(s)); } catch (_) { sent.push(s); } } } };
}

/** Drive every frame of a fixture array through an adapter, exactly as
 *  livewire.js would post-JSON.parse. Events land in the collecting sink the
 *  adapter was constructed with — see collectSink(). */
function replay(adapter, frames) {
  for (const f of frames) adapter.onMessage(f, nullApi);
}

function collectSink() {
  const evts = [];
  return { evts, sink: (e) => evts.push(e) };
}

let failures = 0;
function group(name, fn) {
  try {
    fn();
    console.log('PASS ' + name);
  } catch (e) {
    failures++;
    console.error('FAIL ' + name + ' — ' + (e && e.message ? e.message : e));
    if (e && e.stack) console.error(String(e.stack).split('\n').slice(1, 4).join('\n'));
  }
}

const approx = (a, b, eps) => Math.abs(a - b) <= (eps === undefined ? 1e-9 : eps);

// ─── 1. Bybit publicTrade → normalized trade (§0.6: taker side AS-IS) ────────
group('bybit publicTrade normalization', () => {
  const { evts, sink } = collectSink();
  const ad = A.makeBybitAdapter('BTCUSDT', sink);
  replay(ad, FX.bybit_publicTrade);
  assert.ok(evts.length === 3, 'expected 3 trades from 3 fixture frames, got ' + evts.length);
  // Pair each emitted event with its raw wire item (frames carry 1 trade each).
  FX.bybit_publicTrade.forEach((frame, i) => {
    const raw = frame.data[0], ev = evts[i];
    assert.strictEqual(ev.kind, 'trade');
    assert.strictEqual(ev.ex, 'bybit');
    // The §0.6 rail this whole group exists for: S is ALREADY the aggressor.
    assert.strictEqual(ev.aggressorBuy, raw.S === 'Buy', 'aggressorBuy must equal (S===Buy), no inversion');
    assert.ok(Number.isInteger(ev.ts) && ev.ts === raw.T, 'ts must be the wire T as an int (epoch ms)');
    assert.ok(typeof ev.price === 'number' && Number.isFinite(ev.price) && ev.price === Number(raw.p), 'price must be Number(p)');
    assert.ok(typeof ev.qty === 'number' && Number.isFinite(ev.qty) && ev.qty === Number(raw.v), 'qty must be Number(v)');
    assert.strictEqual(ev.id, raw.i, 'id must be the UUID string verbatim');
  });
});

// ─── 2. Coinbase market_trades → maker-side INVERSION + no snapshot re-seed ──
group('coinbase maker-side inversion + reconnect snapshot skip', () => {
  const { evts, sink } = collectSink();
  const ad = A.makeCoinbaseAdapter('BTC-USD', sink);

  // First snapshot seeds the tape.
  replay(ad, FX.coinbase_market_trades_snapshot);
  const nSeed = evts.length;
  const rawSnapTrades = FX.coinbase_market_trades_snapshot[0].events[0].trades;
  assert.ok(nSeed === rawSnapTrades.length, 'seed snapshot should emit every trade once (' + rawSnapTrades.length + '), got ' + nSeed);

  // §0.6 / DEVELOPMENT.md §5: `side` is the MAKER — aggressor is the INVERSE.
  // Cross-check every emitted event against its raw wire trade by id.
  const rawById = new Map(rawSnapTrades.map((t) => [String(t.trade_id), t]));
  for (const ev of evts) {
    const raw = rawById.get(ev.id);
    assert.ok(raw, 'emitted trade id ' + ev.id + ' not in the fixture');
    assert.strictEqual(ev.aggressorBuy, raw.side === 'SELL',
      'maker side=' + raw.side + ' must invert to aggressorBuy=' + (raw.side === 'SELL'));
    assert.ok(Number.isInteger(ev.ts) && ev.ts === Date.parse(raw.time), 'ts must be int epoch ms of the wire time');
    assert.ok(Number.isFinite(ev.price) && Number.isFinite(ev.qty), 'price/qty must be finite Numbers');
  }
  // Batches must come out oldest→newest (trade_id ascending) so CVD/footprint
  // accumulate in time order — the wire delivers them NEWEST-first.
  for (let i = 1; i < evts.length; i++) {
    assert.ok(Number(evts[i].id) > Number(evts[i - 1].id), 'seed batch must be re-sorted oldest→newest');
  }

  // A SECOND snapshot (Coinbase re-fires the full snapshot on every reconnect)
  // must be skipped entirely — re-seeding would double-count the whole batch
  // into CVD/footprint (fabricated flow, §0.7).
  replay(ad, FX.coinbase_market_trades_snapshot);
  assert.strictEqual(evts.length, nSeed, 'reconnect snapshot must emit ZERO new trades');

  // Updates still flow after the skipped snapshot, inverted + deduped.
  replay(ad, FX.coinbase_market_trades_update);
  const updates = evts.slice(nSeed);
  const nRawUpd = FX.coinbase_market_trades_update
    .reduce((n, f) => n + f.events[0].trades.length, 0);
  assert.strictEqual(updates.length, nRawUpd, 'every update trade emits exactly once');
  for (const ev of updates) {
    assert.ok(Number(ev.id) > 1049465696, 'update ids must be newer than the snapshot max');
  }
  // Spot-check one known update against the rail: trade 1049465700 side=SELL → aggressorBuy true.
  const u700 = updates.find((e) => e.id === '1049465700');
  assert.ok(u700 && u700.aggressorBuy === true, 'SELL maker print must normalize to an aggressive BUY');
});

// ─── 3. Bybit orderbook snapshot+delta → BookStore (qty-0 tombstone rail) ────
// (Frames were captured from orderbook.50 in O-1; the adapter now subscribes
// .200 with a depth-AGNOSTIC 'orderbook.' route — §4b — so these .50 frames
// still exercise the exact same code path. Group 13 covers the .200 frames.)
group('bybit book snapshot+delta through BookStore', () => {
  const { evts, sink } = collectSink();
  const ad = A.makeBybitAdapter('BTCUSDT', sink);
  const book = S.BookStore();

  // Snapshot first.
  replay(ad, FX.bybit_orderbook_snapshot);
  assert.strictEqual(evts.length, 1);
  assert.strictEqual(evts[0].kind, 'depth');
  assert.strictEqual(evts[0].isSnapshot, true, 'type:snapshot must map to isSnapshot:true');
  book.applyDepth(evts[0]);
  let b = book.best();
  assert.ok(b.bid && b.ask && Number.isFinite(b.bid[0]) && Number.isFinite(b.ask[0]), 'best() finite after snapshot');
  assert.ok(b.bid[0] < b.ask[0], 'bid < ask after snapshot');
  assert.strictEqual(b.bid[0], 61855.0, 'fixture best bid');
  assert.strictEqual(b.ask[0], 61855.1, 'fixture best ask');
  assert.ok(book.bids.has(61844.8), 'level 61844.80 present pre-delta');

  // Delta 1 carries ["61844.80","0"] — the qty-0 tombstone MUST survive the
  // adapter (kept, not filtered) and the store must DELETE the level.
  const d1 = FX.bybit_orderbook_delta[0];
  evts.length = 0;
  replay(ad, [d1]);
  assert.strictEqual(evts[0].isSnapshot, false, 'type:delta must map to isSnapshot:false');
  assert.ok(evts[0].bids.some((l) => l[0] === 61844.8 && l[1] === 0), 'adapter must keep the qty-0 tombstone for the store');
  book.applyDepth(evts[0]);
  assert.ok(!book.bids.has(61844.8), 'delta qty "0" must DELETE the level from the book');
  assert.strictEqual(book.bids.get(61854.4), 0.002, 'delta must upsert the new bid level');

  // Remaining deltas keep the book sane (61863.60 deleted then re-added at 1.407).
  evts.length = 0;
  replay(ad, FX.bybit_orderbook_delta.slice(1));
  for (const ev of evts) book.applyDepth(ev);
  assert.ok(!book.asks.has(61857.1), 'third delta deletes ask 61857.10');
  assert.ok(!book.asks.has(61863.5), 'ask 61863.50 added then deleted across deltas');
  assert.strictEqual(book.asks.get(61863.6), 1.407, 'ask 61863.60 deleted then re-added at 1.407');
  b = book.best();
  assert.ok(Number.isFinite(b.bid[0]) && Number.isFinite(b.ask[0]) && b.bid[0] < b.ask[0], 'best() sane after all deltas');
});

// ─── 4. Binance depth20 (combined {stream,data} wrap) → depth applied ────────
group('binance depth20 combined-stream unwrap', () => {
  const { evts, sink } = collectSink();
  const ad = A.makeBinanceDepthAdapter('BTCUSDT', sink);
  const book = S.BookStore();
  replay(ad, FX.binancef_depth20);
  assert.strictEqual(evts.length, FX.binancef_depth20.length, 'one depth event per wrapped frame');
  for (const ev of evts) {
    assert.strictEqual(ev.kind, 'depth');
    assert.strictEqual(ev.ex, 'binancef');
    assert.strictEqual(ev.isSnapshot, true, 'every depth20 frame is a FULL snapshot (wire reality)');
    assert.strictEqual(ev.bids.length, 20);
    assert.strictEqual(ev.asks.length, 20);
    // Sorted best-first per the §4 contract.
    for (let i = 1; i < 20; i++) {
      assert.ok(ev.bids[i][0] < ev.bids[i - 1][0], 'bids descending (best first)');
      assert.ok(ev.asks[i][0] > ev.asks[i - 1][0], 'asks ascending (best first)');
    }
    book.applyDepth(ev);
  }
  const b = book.best();
  assert.ok(b.bid && b.ask && Number.isFinite(b.bid[0]) && Number.isFinite(b.ask[0]), 'best() finite');
  assert.ok(b.bid[0] < b.ask[0], 'bid < ask');
  assert.strictEqual(b.bid[0], 61883.6, 'fixture best bid (second snapshot replaced the first)');
  assert.strictEqual(b.ask[0], 61883.7, 'fixture best ask');
  // Full-snapshot semantics: level 61887.50 exists only in frame 1 — frame 2
  // must have wiped it (a merge instead of a replace would leak stale levels).
  assert.ok(!book.asks.has(61887.5), 'snapshot replace must drop levels absent from the newest frame');
});

// ─── 5. Bybit tickers partial-delta merge → mark/funding persist ─────────────
group('bybit tickers snapshot+delta merge', () => {
  const { evts, sink } = collectSink();
  const ad = A.makeBybitAdapter('BTCUSDT', sink);
  const snap = FX.bybit_tickers_snapshot[0].data;

  replay(ad, FX.bybit_tickers_snapshot);
  const marks0 = evts.filter((e) => e.kind === 'mark');
  const ois0 = evts.filter((e) => e.kind === 'oi');
  assert.strictEqual(marks0.length, 1, 'snapshot emits one mark');
  assert.strictEqual(ois0.length, 1, 'snapshot emits one oi');
  assert.strictEqual(marks0[0].mark, Number(snap.markPrice));
  assert.strictEqual(ois0[0].oi, Number(snap.openInterest));

  // The fixture deltas genuinely OMIT markPrice/fundingRate/openInterest
  // (delta 1 is bid/ask-only; delta 2 moves indexPrice; delta 3 bid-only) —
  // exactly the wire reality the merge exists for. Assert the precondition so
  // this test can never silently pass against a re-captured fixture that
  // stopped exercising the merge.
  for (const f of FX.bybit_tickers_delta) {
    assert.ok(!('markPrice' in f.data) && !('fundingRate' in f.data) && !('openInterest' in f.data),
      'fixture precondition: deltas must omit mark/funding/OI to exercise the merge');
  }

  evts.length = 0;
  replay(ad, FX.bybit_tickers_delta);
  const marks = evts.filter((e) => e.kind === 'mark');
  const ois = evts.filter((e) => e.kind === 'oi');
  assert.strictEqual(marks.length, 3, 'one merged mark per delta');
  assert.strictEqual(ois.length, 3, 'one merged oi per delta');
  for (const m of marks) {
    assert.strictEqual(m.mark, Number(snap.markPrice), 'mark must PERSIST from the snapshot across omitting deltas');
    assert.strictEqual(m.fundingRate, Number(snap.fundingRate), 'fundingRate must persist');
    assert.strictEqual(m.nextFundingTs, Number(snap.nextFundingTime), 'nextFundingTs must persist');
  }
  for (const o of ois) assert.strictEqual(o.oi, Number(snap.openInterest), 'oi must persist');
  // Delta 2 DOES update indexPrice — the merge must take the new value…
  assert.strictEqual(marks[0].index, Number(snap.indexPrice), 'delta 1 (no index change) keeps the snapshot index');
  assert.strictEqual(marks[1].index, 61876.89, 'delta 2 updates the merged index');
  // …and delta 3 (bid-only) must keep delta 2's index, not regress to the snapshot.
  assert.strictEqual(marks[2].index, 61876.89, 'delta 3 keeps the last merged index');
});

// ─── 6. Bybit allLiquidation → side = the LIQUIDATED position (§3) ──────────
group('bybit liquidation side inversion', () => {
  const { evts, sink } = collectSink();
  const ad = A.makeBybitAdapter('BTCUSDT', sink);
  // Real captured frames (JUP/BEAT/1000PEPE — no BTC liq fired in the capture
  // window; the convention is symbol-independent). Retarget topics so the
  // adapter's own topic routing is exercised too.
  const frames = FX.bybit_allLiquidation.map((f) => Object.assign({}, f, { topic: 'allLiquidation.BTCUSDT' }));
  replay(ad, frames);
  assert.strictEqual(evts.length, frames.length, 'one liq per fixture frame');
  frames.forEach((f, i) => {
    const raw = f.data[0], ev = evts[i];
    assert.strictEqual(ev.kind, 'liq');
    // Printed 'Buy' is the forced BUY-BACK of a liquidated SHORT (and vice
    // versa) — reading the print as the position would flip every label.
    assert.strictEqual(ev.side, raw.S === 'Buy' ? 'short' : 'long', 'printed side must invert to the liquidated position');
    assert.ok(approx(ev.notionalUsd, Number(raw.p) * Number(raw.v)), 'notional = price × qty');
    assert.ok(Number.isInteger(ev.ts) && ev.ts === raw.T, 'ts = wire T');
  });
  // The fixture contains both prints, so both output sides are exercised.
  assert.ok(evts.some((e) => e.side === 'short') && evts.some((e) => e.side === 'long'),
    'fixture precondition: both Buy and Sell prints present');
});

// ─── 7. FootprintStore: delta = Σ signed qty; 3:1 diagonal imbalance ─────────
group('footprint bar delta + diagonal imbalance', () => {
  const fp = S.FootprintStore({ barMs: 60000, tickSize: 1 }); // imbalanceK=3, minVol=1.0 defaults
  const T0 = 1783076400000; // bar-aligned epoch ms
  const mk = (ts, price, qty, buy) => ({ kind: 'trade', ex: 'bybit', ts, price, qty, aggressorBuy: buy, id: String(ts) });

  // Bar 1 — constructed EXACT 3:1 diagonal: 3.0 BTC bought at 101 vs 1.0 BTC
  // sold one tick below at 100 → buyImb at 101 (3.0 ≥ 3×1.0 AND ≥ minVol 1.0;
  // the rule is `buy[p] ≥ k·sell[p−tick]`, §4). The stray 0.5 sell goes to 99
  // — deliberately BELOW minVol so it must not flag anything itself, and one
  // extra tick down so it cannot pollute the 101↔100 diagonal under test.
  fp.onTrade(mk(T0 + 1000, 100, 1.0, false));   // sell 1.0 @ 100
  fp.onTrade(mk(T0 + 2000, 101, 2.0, true));    // buy  2.0 @ 101
  fp.onTrade(mk(T0 + 3000, 101, 1.0, true));    // buy  1.0 @ 101 (same level accumulates)
  fp.onTrade(mk(T0 + 4000, 99.4, 0.5, false));  // sell 0.5 @ 99.4 → snaps DOWN to level 99

  // While the bar is OPEN: delta already sums, but imbalance flags must be all
  // false (flags are finished-bar-only — half-formed flags flicker).
  const open = fp.current();
  assert.ok(open && !open.finished, 'current() is the open bar');
  assert.ok(open.levels.every((l) => !l.buyImb && !l.sellImb), 'no imbalance flags on the open bar');

  // Signed sum: +2 +1 (buys) −1 −0.5 (sells) = 1.5; total 4.5.
  fp.onTrade(mk(T0 + 61000, 100.5, 0.25, true)); // first trade of the NEXT bar closes bar 1
  const bars = fp.bars();
  assert.strictEqual(bars.length, 2, 'one finished + one open bar');
  const bar1 = bars[0];
  assert.ok(bar1.finished, 'bar 1 finished on event time');
  assert.ok(approx(bar1.delta, 1.5), 'delta must equal Σ signed qty (got ' + bar1.delta + ')');
  assert.ok(approx(bar1.totalVol, 4.5), 'totalVol = buy + sell volume');
  assert.ok(approx(bar1.buyVol, 3.0) && approx(bar1.sellVol, 1.5), 'per-side volumes');
  assert.strictEqual(bar1.o, 100, 'OHLC: open = first print');
  assert.strictEqual(bar1.h, 101);
  assert.strictEqual(bar1.l, 99.4);
  assert.strictEqual(bar1.c, 99.4, 'close = last print');

  // Levels come out DESCENDING price (ladder order): [101, 100, 99].
  assert.deepStrictEqual(bar1.levels.map((l) => l.price), [101, 100, 99], 'levels descending');
  const l101 = bar1.levels[0], l100 = bar1.levels[1], l99 = bar1.levels[2];
  assert.ok(approx(l101.buy, 3.0) && approx(l101.sell, 0), 'level 101 volumes');
  assert.ok(approx(l100.buy, 0) && approx(l100.sell, 1.0), 'level 100 volumes');
  assert.ok(approx(l99.sell, 0.5), 'level 99 volumes (99.4 snapped down)');
  assert.strictEqual(l101.buyImb, true, '3:1 diagonal must flag buyImb at 101 (buy 3.0 ≥ 3×sell(100)=3.0)');
  assert.strictEqual(l100.sellImb, false, 'sell 100 must NOT flag (1.0 < 3×buy(101)=9)');
  assert.strictEqual(l100.buyImb, false, 'zero buy volume can never flag buyImb');
  assert.strictEqual(l99.sellImb, false, '0.5 BTC is under the 1.0 minVol floor — dust must not flag');
});

// ─── 8. CvdStore: Σ per-bucket == overall at every sample ────────────────────
group('cvd bucket sums equal overall', () => {
  const cvd = S.CvdStore({ bucketsUsd: [1e4, 1e5, 1e6] });
  const T0 = 1783076400000;
  // Mixed notionals hitting every bucket incl. whale, both signs. price=50k →
  // qty 0.1 = $5k (≤10k), qty 1 = $50k (≤100k), qty 10 = $500k (≤1M), qty 30 = $1.5M (whale).
  const flows = [
    [0.1, true], [1, false], [10, true], [30, false],
    [0.1, false], [1, true], [10, false], [30, true],
    [0.2, true], [2, true],
  ];
  flows.forEach(([qty, buy], i) => cvd.onTrade({ ts: T0 + i * 100, price: 50000, qty, aggressorBuy: buy }));
  const s = cvd.series();
  assert.strictEqual(s.t.length, flows.length, 'stride-1 sampling: one sample per trade');
  assert.ok(cvd.buckets.includes('whale'), "bucket keys include 'whale'");
  for (let i = 0; i < s.t.length; i++) {
    let sum = 0;
    for (const k of cvd.buckets) sum += s.byBucket[k][i];
    // Every signed dollar lands in exactly ONE bucket — Σ buckets must equal
    // overall at every sample, not just the last (float-assoc tolerance only).
    assert.ok(approx(sum, s.overall[i], 1e-6 * Math.max(1, Math.abs(s.overall[i]))),
      'sample ' + i + ': Σ buckets ' + sum + ' != overall ' + s.overall[i]);
  }
  // Sanity on the final value: hand-summed signed notional of the flows above.
  const expected = flows.reduce((a, [q, b]) => a + (b ? 1 : -1) * q * 50000, 0);
  assert.ok(approx(s.overall[s.overall.length - 1], expected, 1e-6), 'final overall = hand-computed signed notional');
});

// ─── 9. ProfileStore: POC / VAH ≥ POC ≥ VAL / value area ≈ 70% ───────────────
group('profile POC + 70% value area', () => {
  const prof = S.ProfileStore({ tickSize: 1 });
  const T0 = 1783076400000;
  // 101 one-unit levels at prices 100..200 plus 1 extra unit at 150 → POC=150
  // (vol 2), total 102. Expansion absorbs one 1-unit level per step, so the
  // covered volume overshoots the 70% target by < one level (~1%) — well
  // inside the ±5pp assertion band.
  for (let p = 100; p <= 200; p++) prof.onTrade({ ts: T0 + p, price: p, qty: 1, aggressorBuy: true });
  prof.onTrade({ ts: T0 + 999, price: 150, qty: 1, aggressorBuy: false });
  const pr = prof.profile();

  assert.strictEqual(pr.poc, 150, 'POC must be the max-volume level');
  assert.ok(Number.isFinite(pr.vah) && Number.isFinite(pr.val), 'VAH/VAL finite');
  assert.ok(pr.vah >= pr.poc && pr.poc >= pr.val, 'VAH ≥ POC ≥ VAL');
  assert.strictEqual(pr.totalVol, 102, 'total session volume');
  assert.strictEqual(pr.levels.length, 101, 'one level per integer price');
  for (let i = 1; i < pr.levels.length; i++) assert.ok(pr.levels[i].price > pr.levels[i - 1].price, 'levels ascending');

  // Value-area volume: sum of levels inside [VAL, VAH] vs total → ≈70% ±5pp.
  const vaVol = pr.levels.reduce((a, l) => a + (l.price >= pr.val && l.price <= pr.vah ? l.vol : 0), 0);
  const frac = vaVol / pr.totalVol;
  assert.ok(frac >= 0.65 && frac <= 0.75, 'value-area volume ' + (100 * frac).toFixed(1) + '% outside 70%±5pp');
  // POC is a strict local max above the median → must appear as an HVN candidate.
  assert.ok(pr.hvn.includes(150), 'POC level qualifies as an HVN candidate');
});

// ─── 10. AggBookStore: two-exchange merge — total == Σ byEx ──────────────────
group('agg book two-exchange merge math', () => {
  const agg = S.AggBookStore(['bybit', 'binancef']);
  // Feed REAL normalized depth from both venues through their adapters.
  {
    const { evts, sink } = collectSink();
    const ad = A.makeBybitAdapter('BTCUSDT', sink);
    replay(ad, FX.bybit_orderbook_snapshot);
    replay(ad, FX.bybit_orderbook_delta);
    for (const ev of evts) if (ev.kind === 'depth') agg.applyDepth(ev);
  }
  {
    const { evts, sink } = collectSink();
    const ad = A.makeBinanceDepthAdapter('BTCUSDT', sink);
    replay(ad, FX.binancef_depth20);
    for (const ev of evts) agg.applyDepth(ev);
  }

  // Tick 50 makes the venues' books share $-buckets (bybit ~61855, binance
  // ~61883 both land in the 61850 bid bucket) so byEx really has 2 keys.
  const g = agg.grouped(50, 10);
  assert.ok(g.bids.length > 0 && g.asks.length > 0, 'merged ladder non-empty');
  let sawMultiEx = false;
  for (const rows of [g.bids, g.asks]) {
    for (const row of rows) {
      const sum = Object.values(row.byEx).reduce((a, q) => a + q, 0);
      assert.ok(approx(row.total, sum, 1e-9), 'row @' + row.price + ': total ' + row.total + ' != Σ byEx ' + sum);
      if (Object.keys(row.byEx).length >= 2) sawMultiEx = true;
    }
  }
  assert.ok(sawMultiEx, 'at least one merged bucket must carry BOTH exchanges (else the merge was never exercised)');
  for (let i = 1; i < g.bids.length; i++) assert.ok(g.bids[i].price < g.bids[i - 1].price, 'merged bids best-first');
  for (let i = 1; i < g.asks.length; i++) assert.ok(g.asks[i].price > g.asks[i - 1].price, 'merged asks best-first');

  // Per-venue totals must be conserved through the merge (nothing invented,
  // nothing dropped): Σ byEx.bybit over all bid rows == bybit book's own
  // grouped bid total at the same tick/level count.
  const bybitOwn = agg.books.get('bybit').grouped(50, 10).bids.reduce((a, r) => a + r.qty, 0);
  const bybitMerged = g.bids.reduce((a, r) => a + (r.byEx.bybit || 0), 0);
  assert.ok(approx(bybitOwn, bybitMerged, 1e-9), 'bybit bid quantity conserved through the merge');
});

// ─── 11. OKX adapter: descriptor contract + trade ctVal math (§4b) ──────────
group('okx descriptor + trade ctVal math (CONTRACTS → BTC)', () => {
  const { evts, sink } = collectSink();
  const ad = A.makeOkxAdapter('BTC-USDT-SWAP', sink);
  const api = countingApi();

  // Descriptor contract (§4b): OKX prescribes a PLAIN TEXT 'ping' ≲30s — a
  // JSON op frame (Bybit-style) would be ignored server-side and the socket
  // would die at the idle timeout.
  assert.strictEqual(ad.url, 'wss://ws.okx.com:8443/ws/v5/public');
  assert.strictEqual(ad.pingMs, 25000, 'keepalive must beat the ~30s idle drop');
  {
    const { sent, ws } = captureWs();
    ad.ping(ws);
    assert.strictEqual(sent[0], 'ping', "keepalive must be the literal text 'ping', NOT JSON");
    ad.subscribe(ws);
    const sub = sent[1];
    assert.strictEqual(sub.op, 'subscribe');
    const chans = sub.args.map((a) => a.channel).sort();
    assert.deepStrictEqual(chans, ['books', 'trades'], 'subscribe books + trades (§4b)');
    for (const a of sub.args) assert.strictEqual(a.instId, 'BTC-USDT-SWAP');
  }

  // Sub-ack (fixture okx_sub_ack: {event:'subscribe',…}) carries no data →
  // swallowed, and it must NOT count as liveness (an ack is not a data frame).
  for (const f of FX.okx_sub_ack) ad.onMessage(f, api);
  assert.strictEqual(evts.length, 0, 'sub ack must emit nothing');
  assert.strictEqual(api.alive, 0, 'sub ack must not markAlive');

  // Fixture precondition for the §4b headline assertion: the capture really
  // does carry a sz "200" print (re-captured fixtures must keep exercising it).
  assert.strictEqual(FX.okx_trades[0].data[0].sz, '200', 'fixture precondition: first trade sz must be "200"');

  for (const f of FX.okx_trades) ad.onMessage(f, api);
  assert.strictEqual(evts.length, 3, 'one trade per fixture frame');
  // §4b UNIT RAIL: sizes are CONTRACTS; qty = sz × ctVal (0.01 BTC pinned in
  // fixtures _okx_ctval_note). sz "200" → 2 BTC EXACTLY (200 × 0.01 === 2 in
  // doubles); skipping the multiply would overstate OKX flow 100×.
  assert.strictEqual(evts[0].qty, 2, 'sz "200" × ctVal 0.01 must be EXACTLY 2.00 BTC, got ' + evts[0].qty);
  FX.okx_trades.forEach((f, i) => {
    const raw = f.data[0], ev = evts[i];
    assert.strictEqual(ev.kind, 'trade');
    assert.strictEqual(ev.ex, 'okx');
    // §0.6: OKX `side` is the TAKER (aggressor) — as-is, Bybit family, NO
    // Coinbase-style inversion.
    assert.strictEqual(ev.aggressorBuy, raw.side === 'buy', 'aggressorBuy must equal (side===buy), no inversion');
    assert.ok(Number.isInteger(ev.ts) && ev.ts === Number(raw.ts), 'ts must be the numeric-string wire ts as int ms');
    assert.strictEqual(ev.price, Number(raw.px), 'price must be Number(px)');
    assert.ok(approx(ev.qty, Number(raw.sz) * 0.01, 1e-12), 'qty must be Number(sz) × 0.01');
    assert.strictEqual(ev.id, String(raw.tradeId), 'id must be the tradeId as a string');
  });
  // §4b liveness: EVERY books/trades data frame marks alive — data frames are
  // this socket's only liveness source (the text 'pong' dies in JSON.parse).
  assert.strictEqual(api.alive, 3, 'each trade data frame must markAlive');

  // ctVal is an OPT (another instId = another multiplier): override must win.
  const { evts: e2, sink: s2 } = collectSink();
  const ad2 = A.makeOkxAdapter('BTC-USDT-SWAP', s2, { ctVal: 1 });
  ad2.onMessage(FX.okx_trades[0], nullApi);
  assert.strictEqual(e2[0].qty, 200, 'ctVal override 1 must yield raw contract count');
});

// ─── 12. OKX books snapshot+update → BookStore (ctVal levels + real delete) ──
group('okx books snapshot+update through BookStore (ctVal + tombstone delete)', () => {
  const { evts, sink } = collectSink();
  const ad = A.makeOkxAdapter('BTC-USDT-SWAP', sink);
  const api = countingApi();
  const book = S.BookStore();
  const CT = 0.01;   // pinned BTC-USDT-SWAP ctVal (fixtures _okx_ctval_note)

  // Fixture preconditions: the snapshot carries checksum/seqId (the adapter
  // IGNORES them by design — re-snapshot-on-reconnect instead, §4b — so the
  // fields must exist for that to be a decision rather than a vacuous no-op),
  // and update[1] carries a REAL sz-"0" tombstone for a level the snapshot
  // holds (bid 62009.2) — no synthesized delete needed, the wire provided one.
  const rawSnap = FX.okx_books_snapshot[0].data[0];
  assert.ok('checksum' in rawSnap && 'seqId' in rawSnap, 'fixture precondition: snapshot carries checksum/seqId');
  assert.ok(rawSnap.bids.some((l) => l[0] === '62009.2'),
    'fixture precondition: snapshot holds bid 62009.2');
  assert.ok(FX.okx_books_update[1].data[0].bids.some((l) => l[0] === '62009.2' && l[1] === '0'),
    'fixture precondition: update[1] deletes bid 62009.2 with sz "0"');

  // Snapshot: action:'snapshot' → isSnapshot:true; 25 levels/side, sorted
  // best-first, EVERY level qty ctVal-scaled (books sz is CONTRACTS too, §4b).
  for (const f of FX.okx_books_snapshot) ad.onMessage(f, api);
  assert.strictEqual(evts.length, 1);
  const snap = evts[0];
  assert.strictEqual(snap.kind, 'depth');
  assert.strictEqual(snap.ex, 'okx');
  assert.strictEqual(snap.isSnapshot, true, "action:'snapshot' must map to isSnapshot:true");
  assert.ok(Number.isInteger(snap.ts) && snap.ts === Number(rawSnap.ts), 'ts from the books row');
  assert.strictEqual(snap.bids.length, 25);
  assert.strictEqual(snap.asks.length, 25);
  for (let i = 1; i < 25; i++) {
    assert.ok(snap.bids[i][0] < snap.bids[i - 1][0], 'bids descending (best first)');
    assert.ok(snap.asks[i][0] > snap.asks[i - 1][0], 'asks ascending (best first)');
  }
  // Cross-check EVERY emitted level against its raw 4-tuple: [px, sz, deprecated,
  // nOrders] → [Number(px), Number(sz)×ctVal] (tuple tail ignored).
  const rawBidBySz = new Map(rawSnap.bids.map((l) => [Number(l[0]), Number(l[1])]));
  for (const [p, q] of snap.bids) {
    assert.ok(rawBidBySz.has(p), 'emitted bid ' + p + ' not in the raw snapshot');
    assert.ok(approx(q, rawBidBySz.get(p) * CT, 1e-12), 'bid @' + p + ' qty must be sz × ctVal');
  }

  book.applyDepth(snap);
  let b = book.best();
  assert.strictEqual(b.bid[0], 62009.9, 'fixture best bid');
  assert.strictEqual(b.ask[0], 62010, 'fixture best ask');
  assert.ok(approx(b.bid[1], 883.58 * CT, 1e-12), 'best-bid qty ctVal-scaled (8.8358 BTC, not 883.58)');
  assert.ok(book.bids.has(62009.2), 'level 62009.2 present pre-update');

  // Update 0: action:'update' → isSnapshot:false (no clear); upserts merge.
  evts.length = 0;
  ad.onMessage(FX.okx_books_update[0], api);
  assert.strictEqual(evts[0].isSnapshot, false, "action:'update' must map to isSnapshot:false");
  book.applyDepth(evts[0]);
  assert.ok(approx(book.asks.get(62010), 250.44 * CT, 1e-12), 'update must upsert ask 62010 (ctVal-scaled)');
  assert.ok(book.bids.has(62008.7), 'update 0 adds bid 62008.7');

  // Update 1 carries the REAL ["62009.2","0",…] tombstone — the adapter must
  // KEEP it (qty 0, 0×ctVal is still 0) and the store must DELETE the level.
  evts.length = 0;
  ad.onMessage(FX.okx_books_update[1], api);
  assert.ok(evts[0].bids.some((l) => l[0] === 62009.2 && l[1] === 0),
    'adapter must keep the sz-"0" tombstone (qty 0) for the store');
  book.applyDepth(evts[0]);
  assert.ok(!book.bids.has(62009.2), 'sz "0" must DELETE bid 62009.2 store-side');
  assert.ok(!book.bids.has(62008.7), 'bid 62008.7 added by update 0 then deleted by update 1');

  // Update 2 keeps the book sane; ask 62011 (in the snapshot) is deleted here.
  evts.length = 0;
  ad.onMessage(FX.okx_books_update[2], api);
  book.applyDepth(evts[0]);
  assert.ok(!book.asks.has(62011), 'update 2 deletes ask 62011');
  b = book.best();
  assert.ok(Number.isFinite(b.bid[0]) && Number.isFinite(b.ask[0]) && b.bid[0] < b.ask[0], 'best() sane after all updates');
  assert.ok(approx(book.bids.get(62009.9), 614.58 * CT, 1e-12), 'best bid qty tracks the last update');

  // §4b liveness: every books data frame (snapshot + 3 updates) marks alive.
  assert.strictEqual(api.alive, 4, 'each books data frame must markAlive');
});

// ─── 13. Bybit orderbook.200 (§4b upgrade): subscribe arg + 200-level sanity ─
group('bybit orderbook.200 subscribe + snapshot/delta through BookStore', () => {
  const { evts, sink } = collectSink();
  const ad = A.makeBybitAdapter('BTCUSDT', sink);

  // The §4b upgrade is in the SUBSCRIBE (deeper heatmap range); snapshot/delta
  // semantics are identical at any depth and share one 'orderbook.' route.
  {
    const { sent, ws } = captureWs();
    ad.subscribe(ws);
    const args = sent[0].args;
    assert.ok(args.indexOf('orderbook.200.BTCUSDT') >= 0, 'must subscribe orderbook.200 (§4b)');
    assert.ok(!args.some((a) => a.indexOf('orderbook.50.') === 0), 'the .50 subscription must be GONE');
  }

  // Real captured .200 frames: full 200 levels/side, then sparse deltas.
  const book = S.BookStore();
  replay(ad, FX.bybit_orderbook200_snapshot);
  assert.strictEqual(evts.length, 1, 'one depth event from the snapshot');
  const snap = evts[0];
  assert.strictEqual(snap.isSnapshot, true);
  assert.strictEqual(snap.bids.length, 200, '200 bid levels');
  assert.strictEqual(snap.asks.length, 200, '200 ask levels');
  for (let i = 1; i < 200; i++) {
    assert.ok(snap.bids[i][0] < snap.bids[i - 1][0], 'bids descending (best first)');
    assert.ok(snap.asks[i][0] > snap.asks[i - 1][0], 'asks ascending (best first)');
  }
  book.applyDepth(snap);
  assert.strictEqual(book.bids.size, 200, 'store holds all 200 bid levels');
  assert.strictEqual(book.asks.size, 200, 'store holds all 200 ask levels');
  let b = book.best();
  assert.strictEqual(b.bid[0], 62011.8, 'fixture best bid');
  assert.strictEqual(b.ask[0], 62011.9, 'fixture best ask');
  assert.ok(book.bids.has(62011.3) && book.asks.has(62037.5), 'delta-targeted levels present pre-delta');

  // Deltas: tombstones deep in a 200-level book must still delete store-side.
  evts.length = 0;
  replay(ad, FX.bybit_orderbook200_delta);
  for (const ev of evts) {
    assert.strictEqual(ev.isSnapshot, false, 'type:delta must map to isSnapshot:false');
    book.applyDepth(ev);
  }
  assert.ok(!book.bids.has(62011.3), 'delta ["62011.30","0"] must delete the bid');
  assert.ok(!book.asks.has(62037.5), 'delta must delete ask 62037.50 (the former 200th level)');
  b = book.best();
  assert.ok(Number.isFinite(b.bid[0]) && Number.isFinite(b.ask[0]) && b.bid[0] < b.ask[0], 'best() sane after deltas');
});

// ─── 14. DepthHistoryStore: guard, ring bound, velocity sign (§4b) ──────────
group('depth history ring bound + velocity sign', () => {
  const T0 = 1783076400000;
  const book = S.BookStore();
  const hist = S.DepthHistoryStore({ tickSize: 1, maxSamples: 5, nLevels: 40 });

  // Empty-book guard (§4b): sampling before any snapshot records NOTHING —
  // an all-zero column would render as a fake "liquidity vanished" band.
  hist.sample(T0, book);
  assert.strictEqual(hist.samples().length, 0, 'empty book must not produce a sample');
  assert.ok(Number.isNaN(hist.priceRange().min) && Number.isNaN(hist.priceRange().max), 'empty range is NaN/NaN, never 0');

  // Constructed FILL at bid 100: qty ramps 1→8 over 8 one-second samples.
  // maxSamples=5 → the ring must hold ONLY the newest 5 (bound by construction).
  for (let i = 0; i < 8; i++) {
    book.applyDepth({
      kind: 'depth', ex: 'bybit', ts: T0 + i * 1000, isSnapshot: true,
      bids: [[100, 1 + i]], asks: [[101, 2]],
    });
    hist.sample(T0 + i * 1000, book);
  }
  const smp = hist.samples();
  assert.strictEqual(smp.length, 5, 'ring bound: 8 samples in, 5 held');
  assert.strictEqual(smp[0].ts, T0 + 3000, 'oldest 3 evicted (oldest→newest order)');
  assert.strictEqual(smp[4].ts, T0 + 7000, 'newest sample kept');
  assert.strictEqual(smp[4].bids.get(100), 8, 'grouped bid qty recorded');
  const r = hist.priceRange();
  assert.strictEqual(r.min, 100); assert.strictEqual(r.max, 101);

  // Velocity over the full held window: (8 − 4) qty / 4 s = +1/s — POSITIVE
  // on a fill (liquidity building).
  assert.ok(approx(hist.velocity(100, 10000), 1), 'fill must read as positive velocity, got ' + hist.velocity(100, 10000));
  // <2 samples in a tiny window → 0 ("unknown renders flat, never NaN").
  assert.strictEqual(hist.velocity(100, 1), 0, 'sub-sample window must return 0');

  // Constructed PULL: qty collapses 8 → 0.5 → NEGATIVE velocity. Also checks
  // the absent-bucket-is-zero rule via ask 101 disappearing entirely.
  book.applyDepth({
    kind: 'depth', ex: 'bybit', ts: T0 + 8000, isSnapshot: true,
    bids: [[100, 0.5]], asks: [[102, 1]],
  });
  hist.sample(T0 + 8000, book);
  assert.ok(hist.velocity(100, 4000) < 0, 'pull must read as negative velocity');
  assert.ok(hist.velocity(101, 4000) < 0, 'a bucket that vanished counts as qty 0 (its deletion IS the signal)');
});

// ─── 15. SpoofIcebergDetector: fires on pull + refill, quiet on benign (§4b) ─
group('spoof/iceberg detector fires on constructed pull+refill, quiet on benign', () => {
  const T0 = 1783076400000;
  // Grouped-ladder builders (BookStore.grouped() shape: [{price, qty}]).
  const rows = (pairs) => pairs.map(([price, qty]) => ({ price, qty }));
  const baseBids = () => rows([[100, 1], [99, 1.1], [98, 0.9], [97, 1], [96, 1.05],
    [94, 1], [93, 0.95], [92, 1], [91, 1.1], [90, 1]]);
  const baseAsks = () => rows([[101, 1], [102, 1.2], [103, 0.9], [104, 1]]);

  // (a) SPOOF-PULL: a 20 BTC wall at bid 95 (≥ wallK=8 × median≈1) appears at
  // T0, then VANISHES 5s later with ZERO traded volume there — §4b verbatim:
  // pulled within wallWindowMs (15s) and traded < tradeCoverPct (0.2) × wall.
  // 95 stays inside the still-visible bid range (90..100), so the top-N
  // visibility guard cannot mistake the pull for a scroll-out.
  const det = S.SpoofIcebergDetector({ tickSize: 1 });
  det.onDepthSample(T0, { bids: baseBids().concat(rows([[95, 20]])), asks: baseAsks() });
  det.onDepthSample(T0 + 5000, { bids: baseBids(), asks: baseAsks() });
  const spoofs = det.events().filter((e) => e.kind === 'spoof-pull');
  assert.strictEqual(spoofs.length, 1, 'exactly one spoof-pull, got ' + spoofs.length);
  assert.strictEqual(spoofs[0].price, 95, 'pull at the wall bucket');
  assert.strictEqual(spoofs[0].size, 20, 'size = max displayed wall size');
  assert.strictEqual(spoofs[0].lifetimeMs, 5000, 'lifetime = event-ts span on display');

  // (b) NOT a spoof when the wall was EATEN: same wall, but 5 BTC (≥ 0.2×20)
  // trades at the bucket before it vanishes — real liquidity got filled.
  const det2 = S.SpoofIcebergDetector({ tickSize: 1 });
  det2.onDepthSample(T0, { bids: baseBids().concat(rows([[95, 20]])), asks: baseAsks() });
  det2.onTrade({ kind: 'trade', ex: 'bybit', ts: T0 + 2000, price: 95, qty: 5, aggressorBuy: false, id: 'x1' });
  det2.onDepthSample(T0 + 5000, { bids: baseBids(), asks: baseAsks() });
  assert.strictEqual(det2.events().filter((e) => e.kind === 'spoof-pull').length, 0,
    'a wall consumed by real trades must NOT flag as a spoof');

  // (c) ICEBERG-REFILL: bucket 100 displays max 2 BTC but 7.5 BTC trades there
  // inside icebergWindowMs — traded ≥ icebergM (3) × maxDisplayed (2). The
  // event fires on the crossing trade and re-arms only after a full window
  // (a 4th trade must NOT re-emit).
  const det3 = S.SpoofIcebergDetector({ tickSize: 1 });
  det3.onDepthSample(T0, { bids: rows([[100, 2], [99, 1], [98, 1]]), asks: rows([[101, 1], [102, 1]]) });
  const mkT = (ts, qty) => ({ kind: 'trade', ex: 'bybit', ts, price: 100, qty, aggressorBuy: false, id: String(ts) });
  det3.onTrade(mkT(T0 + 1000, 2.5));   // 2.5 < 6 — quiet
  det3.onTrade(mkT(T0 + 2000, 2.5));   // 5.0 < 6 — quiet
  assert.strictEqual(det3.events().length, 0, 'no iceberg before the 3× threshold');
  det3.onTrade(mkT(T0 + 3000, 2.5));   // 7.5 ≥ 3×2 — fires
  det3.onTrade(mkT(T0 + 4000, 3));     // within the window — must NOT re-fire
  const bergs = det3.events().filter((e) => e.kind === 'iceberg-refill');
  assert.strictEqual(bergs.length, 1, 'exactly one iceberg-refill per bucket per window, got ' + bergs.length);
  assert.strictEqual(bergs[0].price, 100);
  assert.ok(approx(bergs[0].tradedQty, 7.5), 'tradedQty = window sum at the crossing trade');
  assert.strictEqual(bergs[0].maxDisplayed, 2, 'maxDisplayed = max shown size in window');

  // (d) BENIGN book: ordinary jittering ladder + dust trades → ZERO events.
  const det4 = S.SpoofIcebergDetector({ tickSize: 1 });
  for (let i = 0; i < 10; i++) {
    const jit = 0.05 * (i % 3);
    det4.onDepthSample(T0 + i * 1000, {
      bids: baseBids().map((r2) => ({ price: r2.price, qty: r2.qty + jit })),
      asks: baseAsks(),
    });
    det4.onTrade({ kind: 'trade', ex: 'bybit', ts: T0 + i * 1000 + 500, price: 100, qty: 0.2, aggressorBuy: true, id: 'b' + i });
  }
  assert.strictEqual(det4.events().length, 0, 'benign book must stay silent, got ' + det4.events().length + ' event(s)');

  // §4b label rail: EVERY emitted event carries label:'heuristic' — the label
  // rides the event itself so no view can drop it by accident.
  for (const d of [det, det3]) {
    for (const e of d.events()) assert.strictEqual(e.label, 'heuristic', "every detector event must carry label:'heuristic'");
  }
});

// ─── 16. LiqHeatmapModel: exact band math + sides + 'estimated' label (§4b) ──
group('liq heatmap model exact band math + estimated label', () => {
  // §4b known-value case: entry 100, L 10, mmr 0.005 →
  //   long-liq  = 100·(1 − 1/10 + 0.005) =  90.5  (BELOW entry/mark)
  //   short-liq = 100·(1 + 1/10 − 0.005) = 109.5  (ABOVE entry/mark)
  // tick 0.5 keeps both on-grid so the snap must not move them.
  const model = S.LiqHeatmapModel({ tiers: [10], mmr: 0.005, tickSize: 0.5 });
  const obs = [{ ts: 1783076400000, price: 99, side: 'long', qty: 1, notionalUsd: 99 }];
  const est = model.estimate([{ price: 100, vol: 5 }], 100, obs);

  assert.strictEqual(est.label, 'estimated', "model output must carry label:'estimated' (§0.4)");
  assert.strictEqual(est.bands.length, 2, 'one long + one short band');
  const [lo, hi] = est.bands;   // ascending by price
  assert.strictEqual(lo.price, 90.5, 'long-liq band EXACTLY at 90.5, got ' + lo.price);
  assert.strictEqual(lo.side, 'long');
  assert.strictEqual(hi.price, 109.5, 'short-liq band EXACTLY at 109.5, got ' + hi.price);
  assert.strictEqual(hi.side, 'short');
  assert.strictEqual(lo.weight, 1, 'single entry level → both bands at max weight 1');
  assert.strictEqual(hi.weight, 1);

  // Observed prints pass through BY REFERENCE — never blended into bands, and
  // never mutated (§4b: estimates and observations must not be confusable).
  assert.strictEqual(est.observed.length, 1);
  assert.strictEqual(est.observed[0], obs[0], 'observed prints must be the SAME objects, unblended');

  // Liveness filter across the full default tier set + several entry levels:
  // EVERY surviving long band sits strictly BELOW mark, every short strictly
  // ABOVE (a long whose liq price is at/above mark has already fired — §4b).
  const model2 = S.LiqHeatmapModel({ tickSize: 1 });   // tiers [5,10,25,50,100], mmr 0.005
  const levels = [{ price: 61000, vol: 3 }, { price: 62000, vol: 5 }, { price: 63000, vol: 2 }];
  const mark = 62000;
  const est2 = model2.estimate(levels, mark, []);
  assert.ok(est2.bands.length > 0, 'real profile must produce bands');
  let wmax = 0;
  for (const bd of est2.bands) {
    if (bd.side === 'long') assert.ok(bd.price < mark, 'long band ' + bd.price + ' must be strictly below mark');
    else assert.ok(bd.price > mark, 'short band ' + bd.price + ' must be strictly above mark');
    assert.ok(bd.weight > 0 && bd.weight <= 1, 'weights normalized to (0, 1]');
    if (bd.weight > wmax) wmax = bd.weight;
  }
  assert.strictEqual(wmax, 1, 'max band weight must normalize to exactly 1');
  for (let i = 1; i < est2.bands.length; i++) {
    assert.ok(est2.bands[i].price > est2.bands[i - 1].price, 'bands ascending by price');
  }
  assert.strictEqual(est2.label, 'estimated');

  // No mark → NO bands (never NaN bands), observed still passes through.
  const estNaN = model2.estimate(levels, NaN, obs);
  assert.strictEqual(estNaN.bands.length, 0, 'non-finite mark must yield empty bands');
  assert.strictEqual(estNaN.observed.length, 1);
  assert.strictEqual(estNaN.label, 'estimated');
});

// ─── Verdict ─────────────────────────────────────────────────────────────────
if (failures) {
  console.error('\ncheck_terminal: ' + failures + ' group(s) FAILED');
  process.exit(1);
}
console.log('\ncheck_terminal: all groups passed');
