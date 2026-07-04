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
// O-3 additions (DESIGN §4c "check_terminal.cjs additions", binding list):
//  17. Bybit REST klines  → normalizer REVERSES the NEWEST-FIRST list to
//                           chronological, exact fixture numbers, input
//                           unmutated, retCode error → null, NaN row dropped
//  18. buildTpo           → constructed 30m bars: rows/periods, POC (count tie
//                           broken toward session mid), VAH/VAL (70% expansion),
//                           interior-only singles, IB = first 2 OBSERVED
//                           periods — all EXACT; sessions per UTC day, newest-first
//  19. buildKlineVp       → Σ levels.vol ≡ Σ bars.v (constructed exact + real
//                           fixture bars ≤1e-9), POC tie → lowest, HVN
//                           prominence gate, approx:'bar-range' label ALWAYS on
//  20. rollingCorr        → identical series = +1 / inverted = −1 on every full
//                           window, NaN below window/2 valid pairs, NaN pairs
//                           SKIPPED (never zero-coerced), window < 2 refused
//  21. OKX REST funding/OI → fundingRate exact; nextFundingTs = `fundingTime`
//                           (the UPCOMING settlement — naming gotcha); intervalH
//                           derived = 8 (+ fallback-8 path); OI = oiCcy COIN,
//                           NOT the contracts `oi` field (§4b ctVal unit rail)
//  22. HL mids normalizer → SPX-MEMECOIN GUARD: main-universe 'SPX' (SPX6900,
//                           ~$0.37, NOT the index) can never surface for a
//                           dex-filtered query — dex-prefixed keys only
//  23. BYOD row→event     → exact field rename for ALL 5 collector tables
//                           (trades/depth/liqs/funding/oi), §0.6 values pass
//                           through UNCHANGED (no re-inversion), corrupt rows → null
//
// O-4 additions (DESIGN §4d "check_terminal.cjs additions", binding list):
//  24. Bybit REST tickers → 24h-VWAP proxy === turnover24h/volume24h (≤1e-9,
//                           null on zero volume — never a fabricated 0/0),
//                           fundingIntervalH RESPONSE-provided (fallback 8
//                           only when absent), annualized = rate×(8760/H)×100,
//                           pct24h ×100, the Number('')===0 trap (blank wire
//                           field → row dropped, never a plottable 0)
//  25. Deribit chain + DVOL → name parse ('BTC-28AUG26-105000-C' + the
//                           single-digit-day edge) → strike/cp/expiryTs
//                           verified against hand-computed Date.UTC at the
//                           08:00 UTC convention; iv === mark_iv/100 (the
//                           PERCENT trap, DEVELOPMENT §5); unparseable names
//                           SKIPPED AND COUNTED; DVOL = 38.68 (the PINNED
//                           payload — §0: real capture wins)
//  26. HL leaderboard/positions → topByValue addr + acctVal exact,
//                           `windowPerformances` PAIR-ARRAY parse ('month' =
//                           the 30d window), dust (<$10k) excluded from the
//                           ROI ranking ONLY; positions szi/side/entry/
//                           leverage.value vs fixture, szi<0 → short
//  27. buildScreener       → turnover-USD ranking on the REAL fixture rows,
//                           topN slice + 'all' passthrough, total = universe
//                           size, non-finite turnover sinks (never dropped),
//                           input unmutated
//  28. confluenceReads     → EXACTLY the 9 §4d categories in order, each
//                           driven bullish AND bearish, n/a on missing feeds
//                           (never neutral), response-provided funding
//                           interval honored, tally sums to 9, the mandatory
//                           IC-honesty label VERBATIM
//  29. AlertEngine         → per-kind fire + cooldown for all 9 rule kinds,
//                           cvd-divergence carries label:'heuristic' both
//                           directions, thresholds injected (no threshold →
//                           cannot fire), event-ts driven (NaN ts → [])
//  30. unsigned GEX + PCR  → Black-76 Γ > 0 on a pinned real chain row
//                           (T from the fixture's own creation_timestamp),
//                           call Γ ≡ put Γ, Σ|Γ|·OI over two constructed
//                           rows === the hand sum (quant.js — the view's
//                           math source), PCR-by-OI exact on constructed
//                           rows AND vs hand-summed raw fixture OI
//
// Exit: 0 with one PASS line per group; non-zero with a clear FAIL message
// (plus stack) if any group breaks. Run: node scripts/check_terminal.cjs

const fs = require('fs');
const path = require('path');
const assert = require('assert');

const A = require(path.join(__dirname, '..', 'dashboard', 'terminal-adapters.js'));
const S = require(path.join(__dirname, '..', 'dashboard', 'terminal-state.js'));
const H = require(path.join(__dirname, '..', 'dashboard', 'terminal-hist.js'));
const R = require(path.join(__dirname, '..', 'dashboard', 'terminal-replay.js'));
// quant.js is the O-4 views' options-math source (§4d: Γ via black76Greeks,
// max pain via maxPain — the house rule forbids reimplementing either), so
// group 30 drives it directly with normalized chain rows.
const Q = require(path.join(__dirname, '..', 'dashboard', 'quant.js'));
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

// ─── 17. Bybit REST klines: NEWEST-FIRST reversal + exact fixture numbers ────
group('bybit REST kline normalizer reversal (exact fixture numbers)', () => {
  const rawList = FX.bybit_rest_kline.result.list;
  // Fixture precondition (§4c gotcha this whole group pins): the capture
  // really is NEWEST-FIRST — a re-captured fixture that arrived chronological
  // would let a reversal-dropping regression pass silently.
  assert.ok(Number(rawList[0][0]) > Number(rawList[rawList.length - 1][0]),
    'fixture precondition: bybit kline list must be NEWEST-FIRST');

  const before = JSON.stringify(FX.bybit_rest_kline);
  const bars = H.normalizeBybitKlines(FX.bybit_rest_kline);
  // The normalizer iterates backwards instead of slice().reverse() so replays
  // can reuse the cached fixture — pin that the input really is untouched.
  assert.strictEqual(JSON.stringify(FX.bybit_rest_kline), before, 'normalizer must NOT mutate its input');

  assert.strictEqual(bars.length, 5, 'one bar per fixture row');
  for (let i = 1; i < bars.length; i++) {
    assert.ok(bars[i].ts > bars[i - 1].ts, 'bars must come out CHRONOLOGICAL (oldest → newest)');
  }
  // Exact values, both ends: the fixture's LAST row (oldest) must land FIRST…
  assert.strictEqual(bars[0].ts, 1783112400000, 'oldest wire row must land at index 0');
  assert.strictEqual(bars[0].o, 62748);
  assert.strictEqual(bars[0].h, 62946.1);
  assert.strictEqual(bars[0].v, 1783.151);
  // …and the fixture's FIRST row (newest) must land LAST, fully Number()ed.
  const newest = bars[4];
  assert.strictEqual(newest.ts, 1783119600000, 'newest wire row must land at the end');
  assert.strictEqual(newest.o, 62542.2);
  assert.strictEqual(newest.h, 62578.2);
  assert.strictEqual(newest.l, 62516.5);
  assert.strictEqual(newest.c, 62537.1);
  assert.strictEqual(newest.v, 59.008);
  for (const b of bars) {
    for (const k of ['ts', 'o', 'h', 'l', 'c', 'v']) {
      assert.ok(typeof b[k] === 'number' && Number.isFinite(b[k]), k + ' must be a finite Number (wire sends strings)');
    }
  }

  // Bybit errors keep HTTP 200 — retCode is the real status → null (so the
  // fetch wrapper's silent-null contract holds end-to-end).
  assert.strictEqual(H.normalizeBybitKlines(Object.assign({}, FX.bybit_rest_kline, { retCode: 10001 })), null,
    'retCode !== 0 must yield null');
  assert.strictEqual(H.normalizeBybitKlines(null), null);
  assert.strictEqual(H.normalizeBybitKlines({ retCode: 0, result: {} }), null, 'missing list must yield null');

  // One malformed row is DROPPED (never a NaN bar), the rest of the history survives.
  const mangled = JSON.parse(before);
  mangled.result.list[2][3] = 'not-a-number';
  const survived = H.normalizeBybitKlines(mangled);
  assert.strictEqual(survived.length, 4, 'exactly the NaN row dropped, 4 bars kept');
  assert.ok(!survived.some((b) => b.ts === 1783116000000), 'the mangled row is the one missing');
});

// ─── 18. buildTpo: constructed 30m bars — POC/VA/singles/IB exact (§4c) ──────
group('buildTpo constructed bars: POC/VA/singles/IB exact + UTC sessions newest-first', () => {
  const P = 1800000;   // 30 m — the classical TPO period
  // Hand-derivable session on UTC day 0 (tick 1), plus one bar on day 1 so
  // the per-UTC-day split and newest-first session order are both exercised.
  //   period 0: 100..104  → rows 100-104 get letter 0
  //   period 1: 102..106  → rows 102-106 get letter 1
  //   period 2: 103..110  → rows 103-110 get letter 2
  // Row counts (ascending 100..110): [1,1,2,3,3,2,2,1,1,1,1], total 18.
  const bars = [
    { ts: 0,        o: 100, h: 104, l: 100, c: 104, v: 10 },
    { ts: P,        o: 104, h: 106, l: 102, c: 106, v: 5 },
    { ts: 2 * P,    o: 106, h: 110, l: 103, c: 110, v: 2 },
    { ts: 86400000, o: 200, h: 201, l: 200, c: 201, v: 1 },   // next UTC day
  ];
  const sessions = S.buildTpo(bars, { tickSize: 1, periodMs: P });

  assert.strictEqual(sessions.length, 2, 'one session per UTC day');
  assert.strictEqual(sessions[0].date, '1970-01-02', 'sessions newest-first ([0] = latest day)');
  assert.strictEqual(sessions[1].date, '1970-01-01');

  const s0 = sessions[1];   // the constructed 3-period day
  assert.deepStrictEqual(s0.rows.map((r) => r.price),
    [100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110], 'rows ascending, one per tick touched');
  assert.deepStrictEqual(s0.rows.map((r) => r.periods.length),
    [1, 1, 2, 3, 3, 2, 2, 1, 1, 1, 1], 'per-row TPO counts (each bar letters its FULL l..h range)');
  assert.deepStrictEqual(s0.rows[3].periods, [0, 1, 2], 'period indices ascending within a row');

  // POC: rows 103 and 104 tie at 3 letters — the classical tiebreak goes to
  // the row closest to the session mid (105), so 104 must win, EXACTLY.
  assert.strictEqual(s0.poc, 104, 'count tie must break toward the session mid');
  // 70% expansion on counts (target 12.6 of 18), hand-traced: absorb 103(3),
  // 105(2), 106(2), 102(2) → covered 12 < 12.6, then the 107-vs-101 tie (1 vs
  // 1) expands UPWARD → 107, covered 13 → VA rows = 102..107. Same algorithm
  // as ProfileStore (shared valueArea70 helper).
  assert.strictEqual(s0.vah, 107, 'VAH exact');
  assert.strictEqual(s0.val, 102, 'VAL exact');

  // Singles: 1-letter rows STRICTLY INSIDE the session range — 101 and
  // 107..109 qualify; the extremes 100/110 are tails BY CONSTRUCTION and must
  // NOT be flagged (§4c interior-only rule).
  assert.deepStrictEqual(s0.singles, [101, 107, 108, 109], 'interior single prints exact (edges excluded)');

  // IB = range of the first 2 OBSERVED periods (0 and 1): hi 106, lo 100 —
  // raw bar extremes, not bucketed rows.
  assert.deepStrictEqual(s0.ib, { hi: 106, lo: 100 }, 'initial balance exact');

  // Hygiene: malformed bars are SKIPPED, never zero-coerced.
  const dirty = S.buildTpo([bars[0], { ts: NaN, l: 1, h: 2 }, { ts: 0, l: 105, h: 104 }], { tickSize: 1, periodMs: P });
  assert.strictEqual(dirty.length, 1, 'NaN-ts and h<l bars dropped');
  assert.strictEqual(dirty[0].rows.length, 5, 'only the valid bar contributed rows');
});

// ─── 19. buildKlineVp: volume conservation + POC tie + 'bar-range' label ─────
group('buildKlineVp volume conservation + bar-range approximation label', () => {
  // Constructed exact case (tick 1): bar 1 spreads v=10 over 5 buckets
  // (2 each), bar 2 puts v=3 entirely on 102 → levels [2,2,5,2,2].
  const bars = [
    { ts: 0,     o: 100, h: 104, l: 100, c: 104, v: 10 },
    { ts: 60000, o: 102, h: 102, l: 102, c: 102, v: 3 },
  ];
  const vp = S.buildKlineVp(bars, { tickSize: 1 });
  // §4c LABEL RAIL: the approximation label rides the RETURN VALUE itself so
  // no view can drop it by accident — and it must be the exact token the
  // KlineVpView badge renders.
  assert.strictEqual(vp.approx, 'bar-range', "return value must carry approx:'bar-range'");
  assert.deepStrictEqual(vp.levels.map((l) => l.price), [100, 101, 102, 103, 104], 'levels ascending');
  assert.ok(approx(vp.levels[2].vol, 5), 'level 102 = 2 (spread) + 3 (point bar)');
  assert.ok(approx(vp.levels.reduce((a, l) => a + l.vol, 0), 13), 'Σ levels.vol == Σ bars.v (13) on the constructed case');
  assert.strictEqual(vp.poc, 102);
  assert.strictEqual(vp.vah, 104); assert.strictEqual(vp.val, 101, '70% expansion exact');
  assert.deepStrictEqual(vp.hvns, [102], 'the 5-vs-2 spike clears the 25%-of-median prominence gate');
  assert.deepStrictEqual(vp.lvns, [], 'flat shoulders must NOT flag as LVNs');

  // CONSERVATION on the REAL fixture bars through the REAL normalizer (messy
  // floats: v like 1783.151 split over multi-bucket ranges): the uniform
  // spread must neither invent nor lose volume — Σ levels ≡ Σ bars ≤ 1e-9.
  const fbars = H.normalizeBybitKlines(FX.bybit_rest_kline);
  const fvp = S.buildKlineVp(fbars, { tickSize: 10 });
  const fsum = fvp.levels.reduce((a, l) => a + l.vol, 0);
  const vsum = fbars.reduce((a, b) => a + b.v, 0);
  assert.ok(Math.abs(fsum - vsum) <= 1e-9,
    'fixture-bar conservation: Σ levels.vol ' + fsum + ' != Σ bars.v ' + vsum);
  assert.strictEqual(fvp.approx, 'bar-range', 'label present on real data too');

  // POC tie → LOWEST price (ProfileStore convention, deterministic).
  const tie = S.buildKlineVp([
    { ts: 0, o: 105, h: 105, l: 105, c: 105, v: 4 },
    { ts: 1, o: 101, h: 101, l: 101, c: 101, v: 4 },
  ], { tickSize: 1 });
  assert.strictEqual(tie.poc, 101, 'POC tie must resolve to the LOWEST price');

  // Empty input: NaN sentinels (no data must never look like price 0) and the
  // label STILL present — a view rendering the empty state keeps its badge.
  const empty = S.buildKlineVp([], { tickSize: 1 });
  assert.ok(empty.levels.length === 0 && Number.isNaN(empty.poc) && Number.isNaN(empty.vah) && Number.isNaN(empty.val));
  assert.strictEqual(empty.approx, 'bar-range');
});

// ─── 20. rollingCorr: ±identical = ±1, small-n NaN, NaN pairs skipped ────────
group('rollingCorr identical=+1 / inverted=−1 / short-window NaN', () => {
  const x = [0.01, -0.02, 0.015, 0.005, -0.01, 0.02, -0.005, 0.012];
  const w = 4;

  const same = S.rollingCorr(x, x, w);
  assert.strictEqual(same.length, x.length, 'one output per aligned index');
  assert.strictEqual(same[0].i, 0, 'outputs carry their index');
  // Small-n honesty: index 0 holds ONE valid pair < w/2 → NaN, never a
  // confident-looking number.
  assert.ok(Number.isNaN(same[0].r), 'below window/2 valid pairs r must be NaN');
  for (let i = w - 1; i < x.length; i++) {
    assert.ok(approx(same[i].r, 1, 1e-12), 'identical series must read +1 on every full window (i=' + i + ')');
  }

  const inv = S.rollingCorr(x, x.map((v) => -v), w);
  for (let i = w - 1; i < x.length; i++) {
    assert.ok(approx(inv[i].r, -1, 1e-12), 'inverted series must read −1 on every full window (i=' + i + ')');
  }

  // A NaN pair is SKIPPED — the window correlates the remaining pairs instead
  // of zero-coercing (a fabricated flat return would fake decorrelation).
  const y = x.slice(); y[5] = NaN;
  const sk = S.rollingCorr(x, y, w);
  assert.ok(approx(sk[6].r, 1, 1e-12), 'window spanning the NaN pair must skip it and still read +1');
  assert.ok(approx(sk[7].r, 1, 1e-12));

  // Refusals: a 1-sample "correlation" is undefined; non-arrays have no rows.
  assert.deepStrictEqual(S.rollingCorr(x, x, 1), [], 'window < 2 must be refused');
  assert.deepStrictEqual(S.rollingCorr(null, x, w), [], 'non-array input must yield []');
});

// ─── 21. OKX REST funding + OI normalizers vs fixtures (§4c) ─────────────────
group('okx REST funding (intervalH derivation) + OI (oiCcy unit rail) normalizers', () => {
  // Funding: nextFundingTs must be OKX `fundingTime` — the UPCOMING settlement
  // (naming gotcha: OKX `nextFundingTime` is the one AFTER that) — and
  // intervalH is DERIVED from the fundingTime→nextFundingTime spacing.
  const before = JSON.stringify(FX.okx_rest_funding);
  const f = H.normalizeOkxFunding(FX.okx_rest_funding);
  assert.strictEqual(JSON.stringify(FX.okx_rest_funding), before, 'normalizer must NOT mutate its input');
  assert.strictEqual(f.fundingRate, 0.0000387369202921, 'fundingRate exact (Number of the wire string)');
  assert.strictEqual(f.nextFundingTs, 1783123200000, 'nextFundingTs = fundingTime (upcoming settlement), NOT nextFundingTime');
  assert.strictEqual(f.intervalH, 8, 'intervalH derived from the response spacing = 8h exactly');

  // Fallback path (§4c): degenerate/absent spacing → the stated 8h fallback
  // (never NaN — the annualization column divides by intervalH).
  const clone = JSON.parse(before);
  clone.data[0].nextFundingTime = '';
  assert.strictEqual(H.normalizeOkxFunding(clone).intervalH, 8, 'missing nextFundingTime must fall back to 8');
  assert.strictEqual(H.normalizeOkxFunding({ code: '51000', msg: 'err', data: [] }), null, "code !== '0' must yield null");
  assert.strictEqual(H.normalizeOkxFunding({ code: '0', data: [] }), null, 'missing row must yield null');

  // OI unit rail (§4b ctVal gotcha, REST edition): the raw `oi` field is
  // CONTRACTS; `oiCcy` is COIN. Fixture precondition first, so a re-capture
  // where the two fields agree can never vacuously pass the mixup check.
  const raw = FX.okx_rest_oi.data[0];
  assert.ok(approx(Number(raw.oi) * 0.01, Number(raw.oiCcy), 1e-6),
    'fixture precondition: oi(contracts) × ctVal 0.01 == oiCcy(coin)');
  const o = H.normalizeOkxOi(FX.okx_rest_oi);
  assert.strictEqual(o.oi, 31337.2794000001118, 'oi must be oiCcy (COIN) — exact fixture number');
  assert.notStrictEqual(o.oi, Number(raw.oi), 'returning the CONTRACTS field would overstate OKX OI 100×');
  assert.strictEqual(o.oiUsd, Number(raw.oiUsd), 'oiUsd passed through Number()ed');
  assert.ok(approx(o.oiUsd, 1959817785.0363069919161, 1e-3), 'oiUsd ≈ the captured $1.96B');
  assert.strictEqual(o.ts, 1783120004270, 'ts = wire numeric-string ms as int');
  assert.strictEqual(H.normalizeOkxOi({ code: '1', data: [] }), null, "code !== '0' must yield null");
});

// ─── 22. HL mids normalizer: the SPX-memecoin guard (§4c, honesty-critical) ──
group('hl mids normalizer memecoin guard (main-universe SPX must never surface)', () => {
  // Synthetic allMids-shaped payload — HIP-3 dexs expose LIVE mids only, so no
  // captured fixture exists (fixtures _o3_notes); the shape is the documented
  // flat {name: "mid-string"} object. It deliberately contains the
  // honesty-critical trap: main-universe 'SPX' is the SPX6900 MEMECOIN
  // (~$0.37), NOT the S&P 500 — filtered out BY CONSTRUCTION (only
  // dex-prefixed keys pass), whatever the server includes.
  const wire = {
    SPX: '0.3712',            // the memecoin trap — MUST NOT surface
    BTC: '62000.5',           // main-universe majors don't belong in a dex query either
    'km:US500': '6234.5',
    'km:GOLD': '3412.8',
    'km:USOIL': 'not-a-number',   // NaN mid — dropped, never emitted
    'xyz:XYZ100': '2201.1',
  };
  const km = H.normalizeHlMids(wire, 'km');
  // deepStrictEqual pins the WHOLE object: nothing extra can hide in it.
  assert.deepStrictEqual(km, { 'km:US500': 6234.5, 'km:GOLD': 3412.8 },
    'km query must yield EXACTLY the finite km:-prefixed mids — SPX guarded out, NaN dropped');
  assert.ok(!('SPX' in km), 'the SPX6900 memecoin must NEVER pass a dex-filtered query');

  const xyz = H.normalizeHlMids(wire, 'xyz');
  assert.deepStrictEqual(xyz, { 'xyz:XYZ100': 2201.1 }, 'prefix filter is per-dex');

  // Guard preconditions: no dex → no prefix → the guard cannot hold → null
  // (a permissive fallback would let 'SPX' through the moment dex is '').
  assert.strictEqual(H.normalizeHlMids(wire, ''), null, 'empty dex must yield null');
  assert.strictEqual(H.normalizeHlMids(wire, undefined), null, 'missing dex must yield null');
  assert.strictEqual(H.normalizeHlMids(null, 'km'), null, 'non-object payload must yield null');
  assert.strictEqual(H.normalizeHlMids(['km:US500'], 'km'), null, 'array payload is not an allMids object');
});

// ─── 23. BYOD row→event mapping exactness — all 5 collector tables (§4c) ─────
group('byod row→event mapping exactness (all 5 tables)', () => {
  const T0 = 1783076400123;

  // trades: collector snake_case → §4 camelCase, values UNCHANGED — the §0.6
  // aggressor conventions were applied at RECORD time (normalize_bybit_trade
  // etc.), so re-deriving anything here would double-apply them.
  assert.deepStrictEqual(
    R.byodRowToEvent('trades', {
      exchange: 'bybit', symbol: 'BTCUSDT', trade_id: '6c84…-uuid',
      ts_ms: T0, price: 62000.5, qty: 0.25, aggressor_buy: true,
    }),
    { kind: 'trade', ex: 'bybit', ts: T0, price: 62000.5, qty: 0.25, aggressorBuy: true, id: '6c84…-uuid' },
    'trades row must rename EXACTLY onto the §4 trade event');
  assert.strictEqual(
    R.byodRowToEvent('trades', { exchange: 'coinbase', trade_id: '1', ts_ms: T0, price: 1, qty: 1, aggressor_buy: false }).aggressorBuy,
    false, 'stored false must stay false — NO re-inversion of already-normalized rows');

  // depth_snapshots: JSON-string sides parsed, isSnapshot ALWAYS true (every
  // stored row is a full merged top-20 book — BookStore must replace, not merge).
  assert.deepStrictEqual(
    R.byodRowToEvent('depth_snapshots', {
      exchange: 'binancef', symbol: 'BTCUSDT', ts_ms: T0 + 1,
      bids: '[[62000.1,1.5],[62000,2.25]]', asks: '[[62000.2,0.75]]',
    }),
    { kind: 'depth', ex: 'binancef', ts: T0 + 1, bids: [[62000.1, 1.5], [62000, 2.25]], asks: [[62000.2, 0.75]], isSnapshot: true },
    'depth row must parse both sides and always set isSnapshot:true');
  assert.strictEqual(
    R.byodRowToEvent('depth_snapshots', { exchange: 'binancef', ts_ms: T0, bids: 'not json', asks: '[]' }),
    null, 'corrupt depth JSON must be dropped, never guessed');

  // liquidations: side is ALREADY the liquidated position ('long'|'short') —
  // the §0.6 print-side inversion happened in normalize_bybit_liq; pass through.
  assert.deepStrictEqual(
    R.byodRowToEvent('liquidations', {
      exchange: 'bybit', symbol: 'BTCUSDT', ts_ms: T0 + 2,
      side: 'short', price: 61500, qty: 0.4, notional_usd: 24600,
    }),
    { kind: 'liq', ex: 'bybit', ts: T0 + 2, side: 'short', price: 61500, qty: 0.4, notionalUsd: 24600 },
    'liq row must pass the stored (already-inverted) side through unchanged');

  // funding_mark → §4 'mark' event vocabulary.
  assert.deepStrictEqual(
    R.byodRowToEvent('funding_mark', {
      exchange: 'bybit', symbol: 'BTCUSDT', ts_ms: T0 + 3,
      mark: 62001.1, index: 61998.7, funding_rate: 0.0001, next_funding_ts: 1783094400000,
    }),
    { kind: 'mark', ex: 'bybit', ts: T0 + 3, mark: 62001.1, index: 61998.7, fundingRate: 0.0001, nextFundingTs: 1783094400000 },
    'funding_mark row must rename EXACTLY onto the §4 mark event');

  // open_interest → §4 'oi' event.
  assert.deepStrictEqual(
    R.byodRowToEvent('open_interest', { exchange: 'binancef', symbol: 'BTCUSDT', ts_ms: T0 + 4, oi: 83456.123 }),
    { kind: 'oi', ex: 'binancef', ts: T0 + 4, oi: 83456.123 },
    'open_interest row must rename EXACTLY onto the §4 oi event');

  // Hygiene: a row with no time has no home in any store; unknown tables are
  // seam drift with BYOD_ENDPOINTS — both dropped, never guessed.
  assert.strictEqual(R.byodRowToEvent('trades', { exchange: 'bybit', price: 1, qty: 1 }), null, 'missing ts_ms → null');
  assert.strictEqual(R.byodRowToEvent('not_a_table', { ts_ms: T0 }), null, 'unknown table → null');
  assert.strictEqual(R.byodRowToEvent('trades', null), null, 'null row → null');

  // End-to-end sanity: the mapped events ARE what the real stores consume —
  // run one mapped depth row through BookStore (replace semantics hold).
  const book = S.BookStore();
  book.applyDepth(R.byodRowToEvent('depth_snapshots', {
    exchange: 'binancef', ts_ms: T0, bids: '[[62000.1,1.5]]', asks: '[[62000.2,0.75]]',
  }));
  const b = book.best();
  assert.strictEqual(b.bid[0], 62000.1);
  assert.strictEqual(b.ask[0], 62000.2);
});

// ─── 24. Bybit REST tickers: VWAP proxy + response fundingIntervalHour (§4d) ─
group('bybit REST tickers normalizer (24h-VWAP proxy + response funding interval)', () => {
  const before = JSON.stringify(FX.bybit_rest_tickers);
  const rows = H.normalizeBybitTickers(FX.bybit_rest_tickers);
  assert.strictEqual(JSON.stringify(FX.bybit_rest_tickers), before, 'normalizer must NOT mutate its input');
  assert.strictEqual(rows.length, 6, 'all 6 fixture symbols survive');

  // §4d headline: vwap24h = turnover24h / volume24h to 1e-9, EXACT fixture
  // numbers (the '24h VWAP' proxy the ScreenerView labels).
  const btc = rows.find((r) => r.sym === 'BTCUSDT');
  assert.ok(btc, 'BTCUSDT present');
  const expVwap = 2384597440.4426 / 38072.45;
  assert.ok(approx(btc.vwap24h, expVwap), 'vwap24h must equal turnover24h/volume24h (Δ ≤ 1e-9), got ' + btc.vwap24h);
  assert.strictEqual(btc.last, 63100.30, 'last = Number(lastPrice)');
  assert.ok(approx(btc.vwapDevPct, ((63100.30 - expVwap) / expVwap) * 100, 1e-12), 'vwapDevPct = (last−vwap)/vwap ×100');
  // Wire price24hPcnt is a FRACTION (0.01536) → ×100 to the % the scatter plots.
  assert.ok(approx(btc.pct24h, 1.536, 1e-12), 'pct24h = price24hPcnt × 100');
  // §4d: fundingIntervalHour is RESPONSE-PROVIDED — use it, not the 8h constant.
  assert.strictEqual(btc.fundingIntervalH, 8, 'fundingIntervalH = Number(fundingIntervalHour) from the response');
  assert.strictEqual(btc.fundingRate, 0.00003549, 'fundingRate exact');
  assert.ok(approx(btc.annualizedFundingPct, 0.00003549 * (8760 / 8) * 100, 1e-12),
    'annualized = rate × (8760/intervalH) × 100');
  assert.strictEqual(btc.oiUsd, 3637380173.00, 'oiUsd = openInterestValue');
  assert.strictEqual(btc.mark, 63100.20);
  assert.strictEqual(btc.index, 63118.24);
  // Sign preserved through annualization (XRPUSDT funds negative in the capture).
  const xrp = rows.find((r) => r.sym === 'XRPUSDT');
  assert.strictEqual(xrp.fundingRate, -0.00000458, 'fixture precondition: a negative-funding symbol exists');
  assert.ok(xrp.annualizedFundingPct < 0, 'negative funding must stay negative annualized');

  // Response interval ≠ 8 must be USED (some alts fund 4h/1h — a blanket 8
  // would mis-annualize 2–8×); absent/degenerate interval falls back to 8.
  const alt = JSON.parse(before);
  alt.result.list[1].fundingIntervalHour = '4';
  const btc4 = H.normalizeBybitTickers(alt).find((r) => r.sym === 'BTCUSDT');
  assert.strictEqual(btc4.fundingIntervalH, 4, 'response interval 4 must win over the 8h constant');
  assert.ok(approx(btc4.annualizedFundingPct, 0.00003549 * (8760 / 4) * 100, 1e-12), 'annualization uses the response interval');
  const noFih = JSON.parse(before);
  noFih.result.list[1].fundingIntervalHour = '';
  assert.strictEqual(H.normalizeBybitTickers(noFih).find((r) => r.sym === 'BTCUSDT').fundingIntervalH, 8,
    'absent interval falls back to 8 (stated fallback, not a hidden constant)');

  // Zero volume → vwap/dev NULL (a new/dead listing has no VWAP), row kept.
  const zv = JSON.parse(before);
  zv.result.list[1].volume24h = '0.0000';
  const rz = H.normalizeBybitTickers(zv).find((r) => r.sym === 'BTCUSDT');
  assert.strictEqual(rz.vwap24h, null, 'volume 0 → vwap24h null, never a fabricated 0/0');
  assert.strictEqual(rz.vwapDevPct, null, 'no VWAP → no deviation (null, not 0 — flat lies)');

  // The Number('')===0 trap: Bybit spells "absent" as '' (the fixture's own
  // basisRate/preOpenPrice fields show it) — a blank lastPrice must DROP the
  // row, not plot a price-0 symbol at the origin.
  const blank = JSON.parse(before);
  blank.result.list[0].lastPrice = '';
  const survived = H.normalizeBybitTickers(blank);
  assert.strictEqual(survived.length, 5, 'blank-field row dropped, the other 5 survive');
  assert.ok(!survived.some((r) => r.sym === '1000PEPEUSDT'), 'the blanked symbol is the one missing');
  assert.ok(!survived.some((r) => r.last === 0), 'no row may surface a fake price 0');

  assert.strictEqual(H.normalizeBybitTickers(Object.assign({}, FX.bybit_rest_tickers, { retCode: 10001 })), null,
    'retCode !== 0 must yield null');
  assert.strictEqual(H.normalizeBybitTickers(null), null);
});

// ─── 25. Deribit chain name-parse + iv PERCENT/100 + DVOL (§4d) ──────────────
group('deribit chain normalizer (name parse + iv/100) + dvol exact', () => {
  const before = JSON.stringify(FX.deribit_rest_book_summary);
  const ch = H.normalizeDeribitChain(FX.deribit_rest_book_summary);
  assert.strictEqual(JSON.stringify(FX.deribit_rest_book_summary), before, 'normalizer must NOT mutate its input');
  // {rows, skipped} shape: skipped is COUNTED, never silently hidden (§0).
  assert.strictEqual(ch.rows.length, 10, 'all 10 captured instruments parse');
  assert.strictEqual(ch.skipped, 0, 'nothing skipped on the real capture');

  // Headline row, every field vs the wire; expiry against a HAND-COMPUTED
  // Date.UTC at the Deribit 08:00 UTC convention, plus the literal epoch
  // (python-datetime cross-checked) so a Date.UTC misuse cannot self-confirm.
  const r0 = ch.rows.find((r) => r.name === 'BTC-28AUG26-105000-C');
  assert.ok(r0, 'BTC-28AUG26-105000-C present');
  assert.strictEqual(r0.strike, 105000, 'strike from the name');
  assert.strictEqual(r0.cp, 'C', 'call/put flag from the name');
  assert.strictEqual(r0.expiryTs, Date.UTC(2026, 7, 28, 8, 0, 0), 'expiry = 08:00 UTC on the contract date');
  assert.strictEqual(r0.expiryTs, 1787904000000, 'expiry epoch ms exact (2026-08-28T08:00Z)');
  // THE §4d TRAP: mark_iv arrives in PERCENT (48.58) — /100 or every vol
  // formula downstream is silently 100× off (DEVELOPMENT §5).
  assert.strictEqual(r0.iv, 48.58 / 100, 'iv === mark_iv/100 exactly');
  assert.strictEqual(r0.iv, 0.4858, 'iv decimal exact');
  assert.strictEqual(r0.oi, 161.3, 'oi = open_interest (BTC contracts)');
  assert.strictEqual(r0.volume, 0, 'volume passthrough');
  assert.strictEqual(r0.markPrice, 0.00026511, 'markPrice (coin-quoted)');
  assert.strictEqual(r0.underlying, 63358.41, 'underlying = per-expiry synthetic future (Black-76 F)');

  // Single-digit day edge, straight from the real capture ('BTC-6JUL26-…').
  const r1 = ch.rows.find((r) => r.name === 'BTC-6JUL26-54000-P');
  assert.ok(r1, 'single-digit-day instrument present in the capture');
  assert.strictEqual(r1.strike, 54000);
  assert.strictEqual(r1.cp, 'P');
  assert.strictEqual(r1.expiryTs, Date.UTC(2026, 6, 6, 8, 0, 0), 'D MMM YY (no leading zero) must parse');
  assert.strictEqual(r1.expiryTs, 1783324800000, 'expiry epoch ms exact (2026-07-06T08:00Z)');
  assert.strictEqual(r1.iv, 57.71 / 100);

  // Unparseable names (futures, perps) are skipped AND counted — the
  // OptionsView surfaces the count instead of silently shrinking the chain.
  const dirty = JSON.parse(before);
  dirty.result.push({ instrument_name: 'BTC-25SEP26', mark_iv: 50 });          // a future — 3 tokens
  dirty.result.push({ instrument_name: 'BTC_USDC-PERPETUAL', mark_iv: 50 });   // a perp — no date/strike/cp
  const ch2 = H.normalizeDeribitChain(dirty);
  assert.strictEqual(ch2.rows.length, 10, 'options still parse alongside the junk');
  assert.strictEqual(ch2.skipped, 2, 'both non-option names counted, not hidden');
  assert.strictEqual(H.normalizeDeribitChain({ error: { code: 1 } }), null, 'JSON-RPC error payload → null');
  assert.strictEqual(H.normalizeDeribitChain(null), null);
  // Non-finite mark_iv → iv NaN but the row is KEPT: PCR/max-pain need oi,
  // not iv — dropping the row would silently bias both.
  const noIv = JSON.parse(before);
  noIv.result[0].mark_iv = null;
  const chN = H.normalizeDeribitChain(noIv);
  assert.strictEqual(chN.rows.length, 10, 'iv-less row kept for the OI consumers');
  assert.ok(Number.isNaN(chN.rows.find((r) => r.name === 'BTC-28AUG26-105000-C').iv), 'its iv reads NaN, never 0');

  // DVOL: the PINNED capture says 38.68 (§0 — the real payload wins; the §4d
  // list's "38.67" was a transcription slip, corrected in DESIGN 2026-07-05).
  assert.strictEqual(H.normalizeDeribitDvol(FX.deribit_rest_dvol), 38.68, 'DVOL = result.index_price exact');
  assert.strictEqual(H.normalizeDeribitDvol({ result: {} }), null, 'missing index_price → null');
  assert.strictEqual(H.normalizeDeribitDvol(null), null);
});

// ─── 26. HL leaderboard (pair-array parse + dust rule) + whale positions ─────
group('hl leaderboard windowPerformances parse + dust exclusion + positions', () => {
  const before = JSON.stringify(FX.hl_leaderboard_sample);
  const lb = H.normalizeHlLeaderboard(FX.hl_leaderboard_sample);
  assert.strictEqual(JSON.stringify(FX.hl_leaderboard_sample), before, 'normalizer must NOT mutate its input');

  // Top by account value: addr + Number()ed acctVal exact.
  assert.strictEqual(lb.topByValue.length, 3, 'all 3 fixture rows rank (n=10 default caps)');
  assert.strictEqual(lb.topByValue[0].addr, '0xa822a9ceb6d6cb5b565bd10098abcfa9cf18d748', 'largest book first');
  assert.strictEqual(lb.topByValue[0].acctVal, Number('13295008398.5851707458'), 'acctVal Number()ed exact');
  assert.strictEqual(lb.topByValue[1].addr, '0x1c498a93b145e7a73d69691e9023f6f308e1cc3f');
  assert.strictEqual(lb.topByValue[2].addr, '0x24de6b77e8bc31c40aa452926daa6bbab7a71b0f');

  // ROI window: windowPerformances is an ARRAY OF PAIRS [[window, {…}], …]
  // and 'month' is the 30d window (there is no literal '30d' key — wire
  // reality). Only one fixture row has a nonzero month ROI — it must rank #1.
  assert.strictEqual(lb.topByRoi30d[0].addr, '0x24de6b77e8bc31c40aa452926daa6bbab7a71b0f', 'pair-array month window drives the ROI rank');
  assert.strictEqual(lb.topByRoi30d[0].roi, 0.0070708003, 'month roi exact');
  assert.strictEqual(lb.topByRoi30d[0].pnl, Number('14464050.7791530006'), 'month pnl exact');

  // Dust rule (§4d): acctVal < $10k is excluded from the ROI ranking ONLY —
  // a $52 account that lucked into 40× must not outrank every real book —
  // while the VALUE ranking keeps everyone (size is size).
  const dusty = JSON.parse(before);
  dusty.leaderboardRows.push({
    ethAddress: '0xdust', accountValue: '52.10',
    windowPerformances: [['month', { pnl: '2000.0', roi: '40.0', vlm: '0' }]],
  });
  const lb2 = H.normalizeHlLeaderboard(dusty);
  assert.ok(!lb2.topByRoi30d.some((r) => r.addr === '0xdust'), 'dust excluded from the ROI ranking');
  assert.strictEqual(lb2.topByValue.length, 4, 'dust still counts in the VALUE ranking');
  const lbN = H.normalizeHlLeaderboard(FX.hl_leaderboard_sample, 2);
  assert.strictEqual(lbN.topByValue.length, 2, 'n caps the value list');
  assert.strictEqual(lbN.topByRoi30d.length, 2, 'n caps the ROI list');
  assert.strictEqual(H.normalizeHlLeaderboard({}), null, 'missing leaderboardRows → null');

  // Whale positions: assetPositions[].position → the WhaleView row shape.
  const pBefore = JSON.stringify(FX.hl_clearinghouse_state);
  const pos = H.normalizeHlPositions(FX.hl_clearinghouse_state);
  assert.strictEqual(JSON.stringify(FX.hl_clearinghouse_state), pBefore, 'normalizer must NOT mutate its input');
  assert.strictEqual(pos.length, 5, 'all 5 fixture positions survive');
  const sol = pos.find((p) => p.coin === 'SOL');
  assert.strictEqual(sol.szi, 169806.92, 'szi Number()ed, sign intact');
  assert.strictEqual(sol.side, 'long', 'szi > 0 → long (the sign IS the direction)');
  assert.strictEqual(sol.entryPx, 74.6913, 'entryPx exact');
  assert.strictEqual(sol.posValue, Number('14000920.1678400002'), 'posValue = positionValue');
  assert.strictEqual(sol.uPnl, Number('1317808.6891600001'), 'uPnl = unrealizedPnl');
  assert.strictEqual(sol.leverage, 17, 'leverage = leverage.value (the OBJECT wire shape)');
  assert.strictEqual(pos.find((p) => p.coin === 'HYPE').leverage, 2, 'per-position leverage varies');

  // The fixture holds longs only — construct the short so BOTH sides are
  // exercised (same why as the liq-side precondition in group 6).
  assert.ok(pos.every((p) => p.side === 'long'), 'fixture precondition: capture is all-long');
  const shorted = JSON.parse(pBefore);
  shorted.assetPositions[0].position.szi = '-100.5';
  const ps = H.normalizeHlPositions(shorted)[0];
  assert.strictEqual(ps.side, 'short', 'negative szi → short');
  assert.strictEqual(ps.szi, -100.5, 'sign preserved on the row');
  // Zero size = no position = no direction → dropped; bare-number leverage
  // tolerated; missing payload → null.
  const edge = JSON.parse(pBefore);
  edge.assetPositions[1].position.leverage = 5;
  edge.assetPositions[2].position.szi = '0';
  const pe = H.normalizeHlPositions(edge);
  assert.strictEqual(pe.find((p) => p.coin === 'AAVE').leverage, 5, 'bare-number leverage shape tolerated');
  assert.ok(!pe.some((p) => p.coin === 'WLD'), 'zero-szi row dropped');
  assert.strictEqual(H.normalizeHlPositions({}), null, 'missing assetPositions → null');
});

// ─── 27. buildScreener: turnover-USD ranking + slice honesty (§4d) ───────────
group('buildScreener turnover ranking + topN slice + honest total', () => {
  // Rank the REAL normalized fixture rows: turnover24h (USD — the only
  // cross-symbol-comparable key; volume24h is base-coin apples vs oranges).
  const rows = H.normalizeBybitTickers(FX.bybit_rest_tickers);
  const symsBefore = rows.map((r) => r.sym).join(',');
  const s3 = S.buildScreener(rows, { topN: 3 });
  assert.strictEqual(rows.map((r) => r.sym).join(','), symsBefore, 'input array must NOT be mutated (slice-before-sort)');
  assert.deepStrictEqual(s3.rows.map((r) => r.sym), ['BTCUSDT', 'ETHUSDT', 'SOLUSDT'],
    'fixture turnover order: BTC $2.38B > ETH $1.32B > SOL $546M');
  assert.strictEqual(s3.total, 6, "total states the TRUE universe size (the 'top 3 of 6' header)");
  // Default topN = 40 > universe → everything; topN ≤ 0 = the 'all' passthrough.
  assert.strictEqual(S.buildScreener(rows).rows.length, 6, 'default top-40 on 6 rows keeps all 6');
  const all = S.buildScreener(rows, { topN: 0 });
  assert.strictEqual(all.rows.length, 6, "topN 0 = 'all' passthrough");
  for (let i = 1; i < all.rows.length; i++) {
    assert.ok(all.rows[i].turnover24h <= all.rows[i - 1].turnover24h, 'rows descending by turnover');
  }
  // Non-finite turnover SINKS to the end but is never dropped — total must
  // keep stating the real universe size.
  const mixed = S.buildScreener([{ sym: 'NANO', turnover24h: NaN }, { sym: 'REAL', turnover24h: 5 }], { topN: 0 });
  assert.deepStrictEqual(mixed.rows.map((r) => r.sym), ['REAL', 'NANO'], 'NaN turnover sinks, never dropped');
  assert.strictEqual(mixed.total, 2);
  assert.deepStrictEqual(S.buildScreener(null), { rows: [], total: 0 }, 'no tickers → empty, total 0');
});

// ─── 28. confluenceReads: 9 categories, both directions, n/a rail (§4d) ──────
group('confluenceReads all-9 both directions + n/a-on-missing + tally + IC label', () => {
  const CATS = ['footprint Δ-trend', 'CVD slope', 'price vs POC/VA', 'TPO position',
    'funding sign/extreme', 'OI 1h change', 'liq-pressure 5m', 'book top-10 imbalance', 'price vs SMA50'];
  const LABEL = 'un-validated descriptive reads — forward IC of board signals ≈ 0 (RESEARCH-ic-runlog); NOT a signal';
  const readOf = (out, cat) => out.reads.find((r) => r.category === cat).read;

  // All-bullish drive: price above both value areas, net buy flow, positive
  // CVD slope, CROWDED SHORTS (negative funding extreme → contrarian
  // bullish), OI building, short-side liq pressure, bid-heavy book, close
  // above SMA50.
  const bull = S.confluenceReads({
    fpDeltas: [2, 3, 1], cvdSlope: 5,
    price: 110, poc: 100, vah: 105, val: 95,
    tpoPoc: 100, tpoVah: 105, tpoVal: 95,
    fundingRate: -0.0005,               // ×(8760/8)×100 = −54.8%/yr — crowded shorts
    oiChangePct1h: 1.0, liqImb5m: -0.8, bookImb: 0.5,
    sma50: 100, lastClose: 101,
  });
  assert.deepStrictEqual(bull.reads.map((r) => r.category), CATS, 'EXACTLY the 9 §4d categories, in order');
  for (const c of CATS) assert.strictEqual(readOf(bull, c), 'bullish', c + ' must read bullish on the bullish drive');
  assert.deepStrictEqual(bull.tally, { bullish: 9, bearish: 0, neutral: 0, na: 0 }, 'tally counts every read');
  assert.strictEqual(bull.label, LABEL, 'the mandatory IC-honesty sentence, VERBATIM');

  // All-bearish mirror (crowded LONGS: positive funding extreme → bearish).
  const bear = S.confluenceReads({
    fpDeltas: [-2, -3, -1], cvdSlope: -5,
    price: 90, poc: 100, vah: 105, val: 95,
    tpoPoc: 100, tpoVah: 105, tpoVal: 95,
    fundingRate: 0.0005,
    oiChangePct1h: -1.0, liqImb5m: 0.8, bookImb: -0.5,
    sma50: 100, lastClose: 99,
  });
  for (const c of CATS) assert.strictEqual(readOf(bear, c), 'bearish', c + ' must read bearish on the bearish drive');
  assert.deepStrictEqual(bear.tally, { bullish: 0, bearish: 9, neutral: 0, na: 0 });
  assert.strictEqual(bear.label, LABEL);

  // §4d: the RESPONSE-PROVIDED funding interval must drive the annualization
  // — the same rate reads neutral at 8h (5.5%/yr) but crowded at 1h (43.8%/yr).
  const in8 = { fundingRate: 0.00005 };
  assert.strictEqual(readOf(S.confluenceReads(in8), 'funding sign/extreme'), 'neutral', '0.005%/8h ≈ 5.5%/yr — baseline carry');
  assert.strictEqual(readOf(S.confluenceReads(Object.assign({ fundingIntervalH: 1 }, in8)), 'funding sign/extreme'),
    'bearish', 'same rate every 1h ≈ 43.8%/yr — crowded longs');

  // n/a rail (§0.7): a missing feed must NEVER default to neutral — 'neutral'
  // claims "I looked and it is balanced", a fabricated read when nothing
  // arrived. Empty inputs → all 9 n/a; a single present feed leaves 8 n/a.
  const na = S.confluenceReads({});
  assert.deepStrictEqual(na.tally, { bullish: 0, bearish: 0, neutral: 0, na: 9 }, 'no feeds → 9 × n/a, ZERO neutral');
  assert.ok(na.reads.every((r) => r.read === 'n/a'), "every read is 'n/a', none invented");
  assert.strictEqual(na.label, LABEL, 'label present even on an all-n/a board');
  const one = S.confluenceReads({ cvdSlope: 0 });
  assert.strictEqual(readOf(one, 'CVD slope'), 'neutral', 'a PRESENT flat feed is a genuine neutral');
  assert.deepStrictEqual(one.tally, { bullish: 0, bearish: 0, neutral: 1, na: 8 }, 'tally always sums to 9');

  // Dead-bands read neutral, not directional: balanced flow, inside-value
  // price, mild OI/liq/book/SMA moves.
  const mid = S.confluenceReads({
    fpDeltas: [1, -1], cvdSlope: 0,
    price: 100, poc: 100, vah: 105, val: 95,
    tpoPoc: 100, tpoVah: 105, tpoVal: 95,
    fundingRate: 0.00001, oiChangePct1h: 0.2, liqImb5m: 0.1, bookImb: 0.1,
    sma50: 100, lastClose: 100.05,
  });
  assert.deepStrictEqual(mid.tally, { bullish: 0, bearish: 0, neutral: 9, na: 0 }, 'dead-band drive → 9 × neutral');
});

// ─── 29. AlertEngine: per-kind fire + cooldown + heuristic label (§4d) ───────
group('alert engine per-kind fire/cooldown + cvd-divergence heuristic label', () => {
  const T0 = 1783186000000;
  const CD = 60000;   // default cooldownMs
  const eng = (rule) => S.AlertEngine({ rules: [rule] });

  // price-cross: fires on a CROSS in either direction; the first evaluate
  // only seeds prev; prev keeps tracking THROUGH the cooldown so the rule
  // re-arms against reality, not a frozen snapshot.
  {
    const e = eng({ id: 'pc', kind: 'price-cross', threshold: 100 });
    assert.strictEqual(e.evaluate({ ts: T0, price: 99 }).length, 0, 'first sight only seeds prev');
    const up = e.evaluate({ ts: T0 + 1000, price: 100.5 });
    assert.strictEqual(up.length, 1, 'upward cross fires');
    assert.strictEqual(up[0].kind, 'price-cross');
    assert.strictEqual(up[0].ts, T0 + 1000, 'event ts = snap ts (event-time driven)');
    assert.strictEqual(e.evaluate({ ts: T0 + 2000, price: 99 }).length, 0, 'cross inside cooldown suppressed');
    // prev tracked through the cooldown: 99 is on record, so the next
    // post-cooldown tick at 101 is a genuine re-cross and must fire…
    assert.strictEqual(e.evaluate({ ts: T0 + 1000 + CD, price: 101 }).length, 1, 'post-cooldown re-cross fires (prev tracked)');
    // …and the DOWNWARD direction fires symmetrically.
    assert.strictEqual(e.evaluate({ ts: T0 + 1000 + 2 * CD, price: 98 }).length, 1, 'downward cross fires');
    assert.strictEqual(e.evaluate({ ts: T0 + 1000 + 3 * CD, price: 98.5 }).length, 0, 'no cross (same side) → quiet');
  }

  // whale-print: ONE event per evaluate — the largest qualifying notional.
  {
    const e = eng({ id: 'wp', kind: 'whale-print', threshold: 1e6 });
    const mk = (qty, buy) => ({ ts: T0, price: 60000, qty, aggressorBuy: buy, id: String(qty), kind: 'trade', ex: 'bybit' });
    const ev = e.evaluate({ ts: T0, trades: [mk(20, true), mk(50, false), mk(0.1, true)] });
    assert.strictEqual(ev.length, 1, 'one event per evaluate — the largest print, not per-print spam');
    assert.ok(ev[0].msg.indexOf('3000000') >= 0 && ev[0].msg.indexOf('sell') >= 0, 'largest = $3M sell, got: ' + ev[0].msg);
    assert.strictEqual(e.evaluate({ ts: T0 + 1000, trades: [mk(50, false)] }).length, 0, 'cooldown suppresses');
    assert.strictEqual(e.evaluate({ ts: T0 + CD, trades: [mk(50, false)] }).length, 1, 'post-cooldown fires again');
    // Thresholds are INJECTED (§4d): a threshold-less rule cannot fire.
    const bare = eng({ id: 'wp2', kind: 'whale-print' });
    assert.strictEqual(bare.evaluate({ ts: T0, trades: [mk(1000, true)] }).length, 0, 'no threshold → cannot fire, ever');
  }

  // liq-1m: caller-summed LiqStore-style notional ≥ threshold.
  {
    const e = eng({ id: 'lq', kind: 'liq-1m', threshold: 5e6 });
    assert.strictEqual(e.evaluate({ ts: T0, liq1mUsd: 6e6 }).length, 1, 'fires at $6M ≥ $5M');
    assert.strictEqual(e.evaluate({ ts: T0 + 1000, liq1mUsd: 9e6 }).length, 0, 'still-breached inside cooldown stays quiet');
    assert.strictEqual(e.evaluate({ ts: T0 + CD, liq1mUsd: 6e6 }).length, 1, 're-fires after cooldown');
  }

  // funding-flip: last-NONZERO-sign tracking — + → 0 → − is ONE flip (zero is
  // "nobody pays", not a side).
  {
    const e = eng({ id: 'ff', kind: 'funding-flip' });
    assert.strictEqual(e.evaluate({ ts: T0, fundingRate: 0.0001 }).length, 0, 'first sign only seeds');
    assert.strictEqual(e.evaluate({ ts: T0 + 1000, fundingRate: 0 }).length, 0, 'zero is not a flip');
    const flip = e.evaluate({ ts: T0 + 2000, fundingRate: -0.0001 });
    assert.strictEqual(flip.length, 1, '+ → 0 → − reads as exactly ONE flip');
    assert.ok(flip[0].msg.indexOf('positive → negative') >= 0, 'direction stated: ' + flip[0].msg);
    const back = e.evaluate({ ts: T0 + 2000 + CD, fundingRate: 0.0001 });
    assert.strictEqual(back.length, 1, 'flip back fires after cooldown');
    assert.ok(back[0].msg.indexOf('negative → positive') >= 0);
  }

  // cvd-divergence: §4d headline — price HH on a CVD lower-high (bearish) and
  // the LL/HL mirror (bullish); EVERY event carries label:'heuristic' (a
  // descriptive pattern, not a validated one). n ≥ 4 floor.
  {
    const e = eng({ id: 'dv', kind: 'cvd-divergence' });
    const bearish = e.evaluate({ ts: T0, window: { price: [100, 101, 102, 103], cvd: [50, 60, 55, 58] } });
    assert.strictEqual(bearish.length, 1, 'HH price + LH CVD fires');
    assert.strictEqual(bearish[0].label, 'heuristic', "divergence event MUST carry label:'heuristic'");
    assert.ok(bearish[0].msg.indexOf('bearish') >= 0, 'direction in the message');
    const bullish = e.evaluate({ ts: T0 + CD, window: { price: [103, 102, 101, 100], cvd: [55, 50, 58, 60] } });
    assert.strictEqual(bullish.length, 1, 'LL price + HL CVD fires (mirror)');
    assert.strictEqual(bullish[0].label, 'heuristic');
    assert.ok(bullish[0].msg.indexOf('bullish') >= 0);
    const tiny = eng({ id: 'dv2', kind: 'cvd-divergence' });
    assert.strictEqual(tiny.evaluate({ ts: T0, window: { price: [100, 101, 103], cvd: [60, 55, 50] } }).length, 0,
      'n < 4 cannot compare extrema — quiet');
  }

  // book-imbalance: |x| ≥ threshold, either sign.
  {
    const e = eng({ id: 'bi', kind: 'book-imbalance', threshold: 0.4 });
    assert.strictEqual(e.evaluate({ ts: T0, bookImb: 0.3 }).length, 0, 'below threshold quiet');
    const bid = e.evaluate({ ts: T0 + 1000, bookImb: 0.5 });
    assert.strictEqual(bid.length, 1, 'bid-heavy fires');
    assert.ok(bid[0].msg.indexOf('bid-heavy') >= 0);
    const ask = e.evaluate({ ts: T0 + 1000 + CD, bookImb: -0.5 });
    assert.strictEqual(ask.length, 1, 'ask-heavy fires (|x|)');
    assert.ok(ask[0].msg.indexOf('ask-heavy') >= 0);
  }

  // detector-pass: forwards each §4b detector event, PRESERVING the
  // 'heuristic' badge (re-defaulted if a caller stripped it — no layer may
  // drop it); the cooldown then gates subsequent passes.
  {
    const e = eng({ id: 'dp', kind: 'detector-pass' });
    const evs = e.evaluate({
      ts: T0,
      detectorEvents: [
        { kind: 'spoof-pull', price: 95, label: 'heuristic' },
        { kind: 'iceberg-refill', price: 100 },   // stripped label — must be re-defaulted
      ],
    });
    assert.strictEqual(evs.length, 2, 'each detector event forwards individually');
    assert.ok(evs.every((x) => x.label === 'heuristic'), 'heuristic badge preserved AND re-defaulted');
    assert.ok(evs[0].msg.indexOf('spoof-pull') >= 0 && evs[1].msg.indexOf('iceberg-refill') >= 0);
    assert.strictEqual(e.evaluate({ ts: T0 + 1000, detectorEvents: [{ kind: 'spoof-pull', price: 95 }] }).length, 0,
      'cooldown gates subsequent passes');
  }

  // oi-jump + basis-bp: |x| ≥ injected threshold.
  {
    const e = eng({ id: 'oj', kind: 'oi-jump', threshold: 2 });
    assert.strictEqual(e.evaluate({ ts: T0, oiChangePct1h: 1 }).length, 0);
    assert.strictEqual(e.evaluate({ ts: T0 + 1000, oiChangePct1h: 2.5 }).length, 1, 'OI jump fires');
    assert.strictEqual(e.evaluate({ ts: T0 + 1000 + CD, oiChangePct1h: -2.5 }).length, 1, 'OI drop fires (|x|)');
    const b = eng({ id: 'bb', kind: 'basis-bp', threshold: 20 });
    assert.strictEqual(b.evaluate({ ts: T0, basisBp: 10 }).length, 0);
    assert.strictEqual(b.evaluate({ ts: T0 + 1000, basisBp: 25 }).length, 1, 'rich basis fires');
    assert.strictEqual(b.evaluate({ ts: T0 + 1000 + CD, basisBp: -25 }).length, 1, 'discount basis fires (|x|)');
  }

  // Hygiene: event-ts is the ONLY clock — no ts, no events (nothing may be
  // timestamped by guessing); disabled rules are invisible; the ring retains.
  {
    const e = eng({ id: 'lq', kind: 'liq-1m', threshold: 1 });
    assert.strictEqual(e.evaluate(null).length, 0, 'null snap → []');
    assert.strictEqual(e.evaluate({ liq1mUsd: 9e9 }).length, 0, 'missing ts → [] (event-time honesty)');
    assert.strictEqual(e.evaluate({ ts: NaN, liq1mUsd: 9e9 }).length, 0, 'NaN ts → []');
    const off = eng({ id: 'x', kind: 'liq-1m', threshold: 1, enabled: false });
    assert.strictEqual(off.evaluate({ ts: T0, liq1mUsd: 9e9 }).length, 0, 'disabled rule never fires');
    e.evaluate({ ts: T0, liq1mUsd: 5 });
    assert.strictEqual(e.events().length, 1, 'events() replays the retained ring');
    assert.strictEqual(e.events()[0].ruleId, 'lq');
  }
});

// ─── 30. Unsigned GEX sanity + PCR-by-OI math (§4d / §0.5) ───────────────────
group('unsigned GEX (black76 Γ>0, Σ|Γ|·OI hand sum) + PCR by OI', () => {
  const YEAR_MS = 31536000000;   // 365d — the OptionsView constant (quant.js periodsPerYear=365 convention)

  // A PINNED real chain row through the REAL normalizer, T from the
  // fixture's own creation_timestamp (the view's nowTs = slice ts rule —
  // never Date.now(), so this stays deterministic forever).
  const ch = H.normalizeDeribitChain(FX.deribit_rest_book_summary);
  const nowTs = FX.deribit_rest_book_summary.result[0].creation_timestamp;
  assert.ok(Number.isFinite(nowTs), 'fixture precondition: capture carries creation_timestamp');
  const r = ch.rows.find((x) => x.name === 'BTC-25DEC26-80000-C');
  assert.ok(r, 'pinned row BTC-25DEC26-80000-C present');
  const T = (r.expiryTs - nowTs) / YEAR_MS;
  assert.ok(T > 0, 'pinned expiry is live relative to its own capture ts');
  // iv is the normalizer's /100 decimal — feeding black76Greeks the raw
  // PERCENT value is exactly the silent 100× bug this chain of asserts pins.
  const g = Q.black76Greeks(r.underlying, r.strike, r.iv, T, r.cp).gamma;
  assert.ok(Number.isFinite(g) && g > 0, 'Black-76 Γ must be finite and > 0 on a live near-money row, got ' + g);
  // Γ is call/put-identical in Black-76 — the unsigned Σ|Γ|·OI therefore
  // cannot depend on the cp mix at a strike (structural sanity).
  assert.strictEqual(Q.black76Greeks(r.underlying, r.strike, r.iv, T, 'P').gamma, g, 'call Γ ≡ put Γ');
  // And the PERCENT-trap tripwire: raw mark_iv (48-ish "decimal" = 4858% vol)
  // would produce a wildly smaller gamma — assert the decimal iv differs.
  const gWrong = Q.black76Greeks(r.underlying, r.strike, r.iv * 100, T, r.cp).gamma;
  assert.ok(!(Math.abs(gWrong - g) <= 1e-12), 'iv fed as PERCENT must NOT reproduce the decimal-iv gamma');

  // Σ|Γ|·OI over two constructed rows === the hand-computed sum, through
  // quant.js gammaConcentration (the same |Γ|·OI-by-strike accumulation the
  // GEX profile draws — §4d: unsigned Σ|gamma|·OI convention, §0.5).
  const fwd = 63000, t = 0.25;
  const rows2 = [
    { strike: 60000, type: 'C', oi: 100, iv: 0.5 },
    { strike: 60000, type: 'P', oi: 50, iv: 0.45 },
  ];
  const g1 = Q.black76Greeks(fwd, 60000, 0.5, t, 'C').gamma;
  const g2 = Q.black76Greeks(fwd, 60000, 0.45, t, 'P').gamma;
  const gc = Q.gammaConcentration(rows2, fwd, t);
  assert.deepStrictEqual(gc.strikes, [60000], 'both rows accumulate onto the one strike');
  assert.strictEqual(gc.gammaOi[0], Math.abs(g1) * 100 + Math.abs(g2) * 50,
    'Σ|Γ|·OI must equal the hand sum EXACTLY (same op order)');
  assert.ok(gc.gammaOi[0] > 0, 'gamma mass positive');

  // PCR by OI, constructed: puts 15 / calls 30 = 0.5 exactly (quant.js
  // maxPain.pcRatio — the same put/call-OI ratio arithmetic as the view tile).
  const mp = Q.maxPain([
    { strike: 100, type: 'C', oi: 30, underlying: 100 },
    { strike: 100, type: 'P', oi: 15, underlying: 100 },
  ]);
  assert.strictEqual(mp.pcRatio, 0.5, 'PCR by OI = ΣputOI/ΣcallOI exact');
  assert.strictEqual(mp.maxPain, 100, 'single-strike slice pins max pain there');

  // PCR on the REAL chain vs a hand sum straight off the RAW wire fields —
  // pins the whole path (name-parse cp + oi passthrough + ratio) at once.
  let cOi = 0, pOi = 0;
  for (const raw of FX.deribit_rest_book_summary.result) {
    const cp = raw.instrument_name.slice(-1);
    if (cp === 'C') cOi += raw.open_interest; else pOi += raw.open_interest;
  }
  assert.ok(cOi > 0 && pOi > 0, 'fixture precondition: both calls and puts carry OI');
  const mpAll = Q.maxPain(ch.rows.map((x) => ({ strike: x.strike, type: x.cp, oi: x.oi, underlying: x.underlying })));
  assert.ok(approx(mpAll.pcRatio, pOi / cOi, 1e-12), 'chain PCR by OI ' + mpAll.pcRatio + ' != raw-wire hand sum ' + (pOi / cOi));
});

// ─── Verdict ─────────────────────────────────────────────────────────────────
if (failures) {
  console.error('\ncheck_terminal: ' + failures + ' group(s) FAILED');
  process.exit(1);
}
console.log('\ncheck_terminal: all groups passed');
