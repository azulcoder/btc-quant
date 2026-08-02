// bench_render.cjs — where the terminal's frame budget ACTUALLY goes.
//
// Written because STRATEGY §ARCHITECTURE (A2/A3) rested on an unmeasured
// hypothesis — "most of the frame budget goes to data prep rather than
// rasterization" — and this repo's own rule is to verify numerically before
// building on a claim. There is no `performance.mark` anywhere in dashboard/,
// so nothing in the terminal could answer the question. This does.
//
// This half runs under Node against the REAL stores (terminal-state.js is
// DOM-free and clock-free by contract, so it loads unmodified — the same trick
// scripts/check_terminal.cjs uses). It answers two questions:
//
//   1. What does the movable half — ingest + normalize + stores — actually
//      cost per wall second at a realistic burst rate? That is the CEILING on
//      what a Web Worker can free from the main thread. You cannot offload
//      more work than exists.
//   2. What does the Worker BOUNDARY cost? Stores hand views live references
//      on purpose (DepthHistoryStore.samples(): "copying ~3600x80 map entries
//      per heatmap redraw would be pure waste"). A thread boundary forces
//      exactly that copy. Measure it before assuming the offload is free.
//
// The rasterization half lives in scripts/bench_render.html (needs a real GPU
// and a real Canvas2D; Node has neither).
//
//   node scripts/bench_render.cjs
'use strict';

const path = require('path');
const S = require(path.join(__dirname, '..', 'dashboard', 'terminal-state.js'));

const now = () => Number(process.hrtime.bigint()) / 1e6;

function bench(label, fn, iters) {
  fn(); fn();                              // warm the JIT
  const t0 = now();
  for (let i = 0; i < iters; i++) fn();
  const ms = (now() - t0) / iters;
  console.log(`  ${(ms * 1000).toFixed(2).padStart(10)} us   ${label}`);
  return ms;
}

// ─── 1. Ingest + store cost — the movable half ──────────────────────────────
console.log('\n== INGEST + STORES (the DOM-free half — the only part a Worker can take) ==');

const MID = 61000;
const ladder = (n, base, dir) => {
  const rows = [];
  for (let i = 0; i < n; i++) rows.push([base + dir * i * 0.1, 0.5 + (i % 20) / 10]);
  return rows;
};

const book = S.BookStore();
book.applyDepth({ ts: 1e12, bids: ladder(400, MID, -1), asks: ladder(400, MID + 0.1, 1), snapshot: true });

let k = 0;
const tDepth = bench('BookStore.applyDepth (20-level delta — the wire shape)', () => {
  k++;
  book.applyDepth({ ts: 1e12 + k, bids: ladder(20, MID - (k % 5) * 0.1, -1), asks: ladder(20, MID + 0.1 + (k % 5) * 0.1, 1) });
}, 20000);
const tGrouped = bench('BookStore.grouped(1, 40) (one per depth sample, 1/s)', () => book.grouped(1, 40), 5000);

const fp = S.FootprintStore({ barMs: 60000, tickSize: 1 });
const tTrade = bench('FootprintStore.onTrade', () => {
  k++;
  fp.onTrade({ ts: 1e12 + k * 30, price: MID + (k % 200) - 100, qty: 0.01, aggressorBuy: (k & 1) === 0 });
}, 200000);

const cvd = S.CvdStore ? S.CvdStore() : null;
const tCvd = cvd ? bench('CvdStore.onTrade', () => {
  k++;
  cvd.onTrade({ ts: 1e12 + k, price: MID, qty: 0.01, aggressorBuy: (k & 1) === 0 });
}, 200000) : 0;

const vpin = S.VpinStore ? S.VpinStore({ bucketVol: 50 }) : null;
const tVpin = vpin ? bench('VpinStore.push', () => {
  k++;
  vpin.push(1e12 + k, 0.01, (k & 1) === 0);
}, 200000) : 0;

// A BURST rate, not an average: BTC perp runs ~0.5-1.5M prints/day (the
// CvdStore decimation note), i.e. ~6-17/s typical. 2000/s is a liquidation
// cascade, and the depth legs sample at ~100/s across venues.
const TRADES_PER_S = 2000, DEPTH_PER_S = 100, GROUPED_PER_S = 1;
const perSec = TRADES_PER_S * (tTrade + tCvd + tVpin) + DEPTH_PER_S * tDepth + GROUPED_PER_S * tGrouped;
console.log(`\n  At a ${TRADES_PER_S} trade/s BURST + ${DEPTH_PER_S} depth/s:`);
console.log(`    stores cost ${perSec.toFixed(2)} ms per WALL SECOND = ${((perSec / 1000) * 100).toFixed(3)} % of one core.`);
console.log('    That is the CEILING on what moving the stores to a Worker can free.');

// ─── 2. Worker-boundary cost — what a thread split ADDS ─────────────────────
console.log('\n== WORKER BOUNDARY (what the offload COSTS, per slice crossing) ==');

const N_SAMPLES = 3600, N_LEVELS = 40;   // DepthHistoryStore documented capacity
const fakeBook = (i) => ({
  grouped() {
    const bids = [], asks = [];
    for (let l = 0; l < N_LEVELS; l++) {
      bids.push({ price: MID - l - (i % 7), qty: 1 + (l * 13 + i) % 50 });
      asks.push({ price: MID + l + (i % 7), qty: 1 + (l * 17 + i) % 50 });
    }
    return { bids, asks };
  },
});
const depthHist = S.DepthHistoryStore({ tickSize: 1, maxSamples: N_SAMPLES, nLevels: N_LEVELS });
for (let i = 0; i < N_SAMPLES; i++) depthHist.sample(1e12 + i * 1000, fakeBook(i));
const samples = depthHist.samples();
let entries = 0;
for (const s of samples) entries += s.bids.size + s.asks.size;
console.log(`  heatmap slice: ${samples.length} samples x ${N_LEVELS} levels x 2 sides = ${entries} Map entries`);

bench('samples() as shipped — live references, ZERO copy', () => depthHist.samples(), 200);
bench('priceRange() full scan (real per-redraw data prep)', () => depthHist.priceRange(), 20);
bench('structuredClone(samples())  <- the Worker boundary', () => structuredClone(samples), 5);
const plain = samples.map((s) => ({ ts: s.ts, bids: Array.from(s.bids), asks: Array.from(s.asks) }));
bench('  ... build plain form (Maps -> arrays) first', () => samples.map((s) => ({ ts: s.ts, bids: Array.from(s.bids), asks: Array.from(s.asks) })), 5);
bench('  ... then structuredClone(plain form)', () => structuredClone(plain), 5);
const packF64 = (ss) => {
  const buf = new Float64Array(entries * 2 + ss.length * 2);
  let o = 0;
  for (const s of ss) {
    buf[o++] = s.ts; buf[o++] = s.bids.size + s.asks.size;
    for (const [p, q] of s.bids) { buf[o++] = p; buf[o++] = q; }
    for (const [p, q] of s.asks) { buf[o++] = p; buf[o++] = q; }
  }
  return buf;
};
bench('pack to Float64Array (transferable — the honest zero-copy send)', () => packF64(samples), 10);
console.log(`    packed payload: ${(packF64(samples).byteLength / 1048576).toFixed(2)} MiB`);
console.log('    NOTE: transferables need no COOP/COEP, but SharedArrayBuffer DOES —');
console.log('    and neither GitHub Pages nor `python3 -m http.server` sends those headers.');

// Footprint slice at realistic density: ~120 finished 1m bars, ~120 price
// levels each at a $1 tick (a normal BTC 1m range).
const fp2 = S.FootprintStore({ barMs: 60000, tickSize: 1 });
for (let bar = 0; bar < 130; bar++) {
  const base = MID + Math.round(Math.sin(bar / 8) * 300);
  for (let i = 0; i < 1200; i++) {
    fp2.onTrade({ ts: 1e12 + bar * 60000 + i * 50, price: base + ((i * 37) % 120) - 60, qty: 0.01 + (i % 50) / 1000, aggressorBuy: (i & 3) < 2 });
  }
}
const bars = fp2.bars();
let levelRows = 0;
for (const b of bars) levelRows += (b.levels && b.levels.length) || 0;
console.log(`\n  footprint slice: ${bars.length} bars, ${levelRows} price-level rows`);
bench('fp.bars() as shipped — live references, ZERO copy', () => fp2.bars(), 200);
bench('structuredClone(fp.bars())  <- the Worker boundary', () => structuredClone(bars), 20);

// ─── 3. The verdict, stated in the units that matter ────────────────────────
console.log('\n== READ THIS BEFORE SEQUENCING ANY RENDER WORK ==');
console.log('  Compare the two numbers above: what the stores COST per second,');
console.log('  against what ONE slice crossing costs. A Worker that keeps rendering');
console.log('  on the main thread has to send slices, so it pays the second number');
console.log('  at each panel budget (heat 2/s, fp 4/s) to save the first.');
console.log('  A Worker that renders via OffscreenCanvas sends nothing — but then');
console.log('  it needs getComputedStyle, getBoundingClientRect and devicePixelRatio,');
console.log('  none of which exist off the main thread (see bench_render.html).');
console.log('');
