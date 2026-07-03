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
//   3. Bybit orderbook.50 → snapshot+delta through BookStore: sane best(), and a
//                           delta qty-"0" level actually DELETES (tombstone rail)
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

// ─── 3. Bybit orderbook.50 snapshot+delta → BookStore (qty-0 tombstone rail) ─
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

// ─── Verdict ─────────────────────────────────────────────────────────────────
if (failures) {
  console.error('\ncheck_terminal: ' + failures + ' group(s) FAILED');
  process.exit(1);
}
console.log('\ncheck_terminal: all groups passed');
