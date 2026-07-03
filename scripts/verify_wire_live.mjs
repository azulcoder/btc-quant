#!/usr/bin/env node
// verify_wire_live.mjs — L2 live-wire invariant verifier for the orderflow
// terminal (DESIGN-orderflow-terminal.md §2 endpoints, §4/§4b module contracts).
//
// WHY THIS EXISTS (the gap next to scripts/check_terminal.cjs): the fixture
// smoke replays FROZEN frames captured 2026-07-03 — it proves adapters/stores
// still agree with the wire *as it was on capture day*. Frozen frames can
// never catch (a) exchange API drift — a venue changing field names, units or
// framing after capture — or (b) cross-venue incoherence — e.g. a decimal /
// symbol / contract-multiplier drift that stays self-consistent inside one
// venue but is nonsense next to the others. This script closes that gap: it
// connects to the SAME keyless public endpoints the terminal uses (§0.2, §2),
// pushes every live frame through the PRODUCTION normalization path — the
// real terminal-adapters.js onMessage() feeding the real terminal-state.js
// stores, loaded via createRequire through their quant.js dual-export, NOT a
// reimplementation — and tallies physical invariants only a live wire can
// break. Any FAIL here means "the wire and the code no longer agree", which
// is exactly the class of bug fixtures are structurally blind to.
//
// The socket loop is OURS, not livewire.js's: makeSocket owns reconnect/
// backoff/watchdog for a long-lived page, while this is a bounded probe — a
// leg that dies mid-run just stops counting frames and the per-venue table
// says so. Adapters receive the fake api {markAlive(){}, onStatus(){}}
// (the only two members they ever touch — see check_terminal.cjs nullApi).
// Parse discipline mirrors livewire.js: every incoming message is
// JSON.parse'd and parse failures are dropped silently — that is exactly how
// OKX's plain-text 'pong' is ignored BY CONSTRUCTION in production
// (terminal-adapters.js §4b keepalive note), so we must do the same.
//
// Honesty rails (§0) this file holds itself to:
//   - LIVE-DESCRIPTIVE observation only. NO writes anywhere — no DB, no
//     files, stdout/stderr only. Nothing here feeds a backtest (§0.1).
//   - Keyless public endpoints only, the terminal's own (§0.2). Binance
//     Futures WS trades/mark are topic-filtered on this network — their
//     ABSENCE is expected wire reality, not a failure; Binance depth MUST
//     flow though, and mark/OI come from one REST poll each (§2).
//   - Offline is not a code bug: if nothing connects, the invariants were
//     UNOBSERVABLE, not violated. Distinct exit code 2 (vs 1 = a real
//     invariant break) so callers/CI can tell "wire unreachable from here"
//     apart from "code or exchange drifted". Conflating them would train
//     people to ignore exit 1.
//
// Invariants (named checks; violations tallied, first examples kept):
//   1 book-never-crossed   per-venue BookStore best bid < best ask after
//                          EVERY depth event (a crossed book after the real
//                          snapshot/delta path = drift in depth semantics).
//   2 venue-mids-coherent  pairwise |mid_a − mid_b| / mid ≤ 80 bp. All book
//                          legs here are USDT-linear perps (spread ~0–5 bp of
//                          each other normally); 80 bp is deliberately wide —
//                          spot-vs-perp basis headroom, so it stays valid if a
//                          spot book leg is ever added — and still catches
//                          what it hunts: price-unit / symbol / decimal drift.
//   3 event-ts-sane        |ts − local now| ≤ 60 s (clock-skew + wire-lag
//                          headroom) and per-stream (ex:kind) ts NON-DECREASING
//                          (equal allowed — batch frames share timestamps).
//                          Seed-backlog nuance: Coinbase's market_trades
//                          snapshot legitimately replays recent history (§2
//                          fixture reality), so skew enforcement per stream
//                          ARMS at the first in-window event; a stream that
//                          NEVER gets within the window is one violation at
//                          end of run (a dead-clocked stream must not pass by
//                          hiding in the backlog exemption).
//   4 trade-near-mid       trade price within 1% of that venue's current mid
//                          (arrival coherence). Coinbase is tape-only (no book
//                          subscribed, §2) so its reference is the cross-venue
//                          median perp mid — 1% ≥ 80 bp basis bound + tape lag.
//   5 cvd-bucket-sum       Σ per-bucket CVD == overall after every trade (one
//                          CvdStore per venue). Float-assoc tolerance 1e-6
//                          relative — the buckets and the overall accumulate
//                          in different orders, so bit-exact `===` would be a
//                          dishonest demand of IEEE754; matches the repo
//                          precedent in check_terminal.cjs group 8.
//   6 liq-well-formed      any allLiquidation events that fire must be the §4
//                          normalized shape: side ∈ {long,short} (§3: side =
//                          the LIQUIDATED position), finite price/qty > 0,
//                          notionalUsd == price·qty. Zero events in a quiet
//                          window is normal (PASS with checked=0).
//   7 mark-merge-sane      every 'mark' event (Bybit tickers partial-delta
//                          MERGE — the drift-sensitive path, §2 — and the
//                          Binance REST premiumIndex poll) yields finite
//                          mark/index/fundingRate, with mark and index within
//                          5% of that venue's mid. fundingRate is a
//                          dimensionless per-interval rate, not a price, so
//                          "within 5%" reads as |fundingRate| ≤ 0.05 — a
//                          funding print above 5%/interval means unit drift
//                          (percent-vs-fraction), precisely the drift class
//                          this probe hunts.
//
// Also counted: raw WS frames per venue + normalized events per venue:kind.
// A venue with 0 frames is a WARNING, not a failure (one blocked venue on one
// network is a connectivity fact, not a code bug — §0.2 precedent); binancef
// *trades* absent is EXPECTED (topic-filter reality §0.2) and not even warned,
// but binancef depth going silent gets its own warning.
//
// Exit codes: 0 = all checks pass · 1 = ≥1 invariant violated · 2 = could not
// connect (zero WS frames across all venues — see offline rail above).
// Usage:  node scripts/verify_wire_live.mjs [--seconds N]     (default 45)
// Ctrl-C: stops early, reports what was gathered, exits with the same rules.
//
// Requires node ≥ 22 (native WebSocket + fetch; ZERO deps by design — a
// verifier that needs `npm install` before it can tell you the wire drifted
// is a verifier that never gets run).

import { createRequire } from 'node:module';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

// ─── Load the PRODUCTION modules (quant.js dual-export → plain require) ─────
const require = createRequire(import.meta.url);
const HERE = path.dirname(fileURLToPath(import.meta.url));
const A = require(path.join(HERE, '..', 'dashboard', 'terminal-adapters.js'));
const S = require(path.join(HERE, '..', 'dashboard', 'terminal-state.js'));

// Native WebSocket is the whole zero-deps story — fail loudly on old node
// rather than let `new WebSocket` throw a confusing ReferenceError later.
if (typeof WebSocket === 'undefined') {
  console.error('verify_wire_live: node >= 22 required (native WebSocket). Running: ' + process.version);
  process.exit(64); // EX_USAGE — environment problem, NOT exit 2 (that code is reserved for "wire unreachable")
}

// ─── Thresholds (each one justified above; constants named so the table reads) ─
const MID_COHERENCE = 0.008;   // 80 bp — check 2 (spot-vs-perp basis headroom)
const TS_SKEW_MS = 60_000;     // check 3 — clock skew + wire lag headroom
const TRADE_MID_PCT = 0.01;    // 1% — check 4 (≥ 80 bp basis + tape lag)
const MARK_MID_PCT = 0.05;     // 5% — check 7 (mark/index vs venue mid)
const FUNDING_ABS_MAX = 0.05;  // check 7 — |fundingRate| bound (unit-drift tripwire)
const CVD_REL_EPS = 1e-6;      // check 5 — float-assoc tolerance (check_terminal.cjs precedent)
const MAX_EXAMPLES = 3;        // first N violation examples kept per check (diagnosis without spam)

// ─── CLI ─────────────────────────────────────────────────────────────────────
function usage() {
  console.error('usage: node scripts/verify_wire_live.mjs [--seconds N]   (default 45, min 5)');
}
let seconds = 45;
{
  const argv = process.argv.slice(2);
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--seconds') seconds = Number(argv[++i]);
    else if (a.startsWith('--seconds=')) seconds = Number(a.slice('--seconds='.length));
    else if (a === '-h' || a === '--help') { usage(); process.exit(0); }
    else { console.error('unknown argument: ' + a); usage(); process.exit(64); }
  }
  if (!Number.isFinite(seconds) || seconds < 5) {
    console.error('--seconds must be a number >= 5, got: ' + seconds);
    process.exit(64);
  }
}

// ─── Check registry ──────────────────────────────────────────────────────────
// Every check carries {checked, violations, skipped, examples[]} plus ad-hoc
// sub-tallies (skew/nonmono/backlog on check 3) surfaced in the notes column.
const CHECK_NAMES = [
  'book-never-crossed',
  'venue-mids-coherent',
  'event-ts-sane',
  'trade-near-mid',
  'cvd-bucket-sum',
  'liq-well-formed',
  'mark-merge-sane',
];
const checks = new Map(CHECK_NAMES.map((n) => [n, { checked: 0, violations: 0, skipped: 0, examples: [] }]));
function viol(check, msg) {
  const c = checks.get(check);
  c.violations++;
  if (c.examples.length < MAX_EXAMPLES) c.examples.push(msg);
}

// ─── Verifier state (all in-memory — NO writes anywhere, §0 rail) ───────────
const books = new Map();  // ex → BookStore (production store, real snapshot/delta path)
const cvds = new Map();   // ex → CvdStore (one per venue — check 5 contract)
const mids = new Map();   // ex → latest mid from that venue's own book
// per-stream (`ex:kind`) timestamp state for check 3 — high-water `last` so a
// single late print counts once, not once per subsequent in-order event.
const tsState = new Map(); // key → {last, armed, backlog, count}
const eventCounts = new Map(); // ex → Map(kind → n)

function ensureBook(ex) {
  let b = books.get(ex);
  if (!b) { b = S.BookStore(); books.set(ex, b); }
  return b;
}
function countEvent(ex, kind) {
  let m = eventCounts.get(ex);
  if (!m) { m = new Map(); eventCounts.set(ex, m); }
  m.set(kind, (m.get(kind) || 0) + 1);
}
function median(xs) {
  if (!xs.length) return NaN;
  const s = xs.slice().sort((a, b) => a - b);
  const n = s.length;
  return n % 2 ? s[(n - 1) / 2] : 0.5 * (s[n / 2 - 1] + s[n / 2]);
}

// ─── Check implementations (called from the one sink below) ─────────────────

// Check 3: |ts − now| ≤ 60 s (armed after first in-window event — see header
// seed-backlog note) + per-stream non-decreasing ts (equal allowed).
function checkTs(ev) {
  const c = checks.get('event-ts-sane');
  const key = ev.ex + ':' + ev.kind;
  let st = tsState.get(key);
  if (!st) { st = { last: NaN, armed: false, backlog: 0, count: 0 }; tsState.set(key, st); }
  st.count++;
  c.checked++;
  if (!Number.isFinite(ev.ts)) {
    // A non-finite ts out of a production adapter would be its own drift class.
    c.nonfinite = (c.nonfinite || 0) + 1;
    viol('event-ts-sane', key + ': non-finite ts ' + ev.ts);
    return;
  }
  if (Number.isFinite(st.last) && ev.ts < st.last) {
    c.nonmono = (c.nonmono || 0) + 1;
    viol('event-ts-sane', key + ': ts went backwards ' + st.last + ' -> ' + ev.ts + ' (delta ' + (ev.ts - st.last) + 'ms)');
  }
  if (!Number.isFinite(st.last) || ev.ts > st.last) st.last = ev.ts;
  const skew = Math.abs(ev.ts - Date.now());
  if (skew <= TS_SKEW_MS) st.armed = true;
  else if (st.armed) {
    c.skew = (c.skew || 0) + 1;
    viol('event-ts-sane', key + ': |ts-now| = ' + skew + 'ms > ' + TS_SKEW_MS + 'ms');
  } else {
    st.backlog++; // pre-arm backlog (e.g. Coinbase seed snapshot history) — skipped, tallied
  }
}

// Checks 1 + 2, driven on every depth event.
function onDepth(ev) {
  const book = ensureBook(ev.ex);
  book.applyDepth(ev); // PRODUCTION snapshot/delta/tombstone path — the thing under test
  const { bid, ask } = book.best();
  if (!bid || !ask) return; // one-sided/empty book mid-seed — nothing to judge yet
  const c1 = checks.get('book-never-crossed');
  c1.checked++;
  if (!(bid[0] < ask[0])) {
    viol('book-never-crossed', ev.ex + ': bid ' + bid[0] + ' >= ask ' + ask[0] + ' after ' + (ev.isSnapshot ? 'snapshot' : 'delta'));
  }
  const mid = 0.5 * (bid[0] + ask[0]);
  mids.set(ev.ex, mid);
  // Check 2: only the pairs involving the venue that just moved — every pair
  // still gets re-judged continuously because every book leg keeps ticking.
  const c2 = checks.get('venue-mids-coherent');
  for (const [other, m] of mids) {
    if (other === ev.ex || !Number.isFinite(m)) continue;
    c2.checked++;
    const ref = 0.5 * (mid + m);
    const dev = Math.abs(mid - m) / ref;
    if (dev > MID_COHERENCE) {
      viol('venue-mids-coherent', ev.ex + ' mid ' + mid.toFixed(1) + ' vs ' + other + ' mid ' + m.toFixed(1)
        + ' -> ' + (dev * 1e4).toFixed(1) + 'bp > ' + (MID_COHERENCE * 1e4) + 'bp');
    }
  }
}

// Checks 4 + 5, driven on every trade event.
function onTrade(ev) {
  const c4 = checks.get('trade-near-mid');
  let mid = mids.get(ev.ex);
  // Coinbase leg is tape-only (§2: market_trades + heartbeats, no book), so
  // its arrival-coherence reference is the cross-venue median perp mid.
  if (!Number.isFinite(mid)) mid = median([...mids.values()].filter(Number.isFinite));
  if (!Number.isFinite(mid)) {
    c4.skipped++; // no book has seeded yet (first instants of the run) — unknowable, not wrong
  } else {
    c4.checked++;
    const dev = Math.abs(ev.price - mid) / mid;
    if (dev > TRADE_MID_PCT) {
      viol('trade-near-mid', ev.ex + ': trade ' + ev.price + ' vs mid ' + mid.toFixed(1)
        + ' -> ' + (dev * 100).toFixed(2) + '% > ' + (TRADE_MID_PCT * 100) + '%');
    }
  }
  // Check 5: production CvdStore per venue; Σ buckets vs overall at the
  // newest sample (stride is 1 at this run length — one sample per trade).
  let cvd = cvds.get(ev.ex);
  if (!cvd) { cvd = S.CvdStore(); cvds.set(ev.ex, cvd); }
  cvd.onTrade(ev);
  const s = cvd.series();
  const i = s.t.length - 1;
  if (i < 0) return; // store's hygiene dropped the trade (would imply adapter emitted junk — caught by ts/finite checks)
  const c5 = checks.get('cvd-bucket-sum');
  c5.checked++;
  let sum = 0;
  for (const k of cvd.buckets) sum += s.byBucket[k][i];
  if (!(Math.abs(sum - s.overall[i]) <= CVD_REL_EPS * Math.max(1, Math.abs(s.overall[i])))) {
    viol('cvd-bucket-sum', ev.ex + ': sum(buckets) ' + sum + ' != overall ' + s.overall[i] + ' at sample ' + i);
  }
}

// Check 6: §4 normalized liq shape (side already normalized to the LIQUIDATED
// position by the adapter — §3 convention the fixture smoke also pins).
function onLiq(ev) {
  const c = checks.get('liq-well-formed');
  c.checked++;
  const notional = ev.price * ev.qty;
  const ok = Number.isFinite(ev.ts)
    && (ev.side === 'long' || ev.side === 'short')
    && Number.isFinite(ev.price) && ev.price > 0
    && Number.isFinite(ev.qty) && ev.qty > 0
    && Number.isFinite(ev.notionalUsd)
    && Math.abs(ev.notionalUsd - notional) <= 1e-9 * Math.max(1, notional);
  if (!ok) viol('liq-well-formed', ev.ex + ': malformed liq ' + JSON.stringify(ev));
}

// Check 7: mark/index/fundingRate sanity (Bybit tickers MERGE path + Binance
// REST premiumIndex — both production normalizers).
function onMark(ev) {
  const c = checks.get('mark-merge-sane');
  if (!(Number.isFinite(ev.mark) && Number.isFinite(ev.index) && Number.isFinite(ev.fundingRate))) {
    c.checked++;
    viol('mark-merge-sane', ev.ex + ': non-finite field(s) mark=' + ev.mark + ' index=' + ev.index + ' fundingRate=' + ev.fundingRate);
    return;
  }
  const mid = mids.get(ev.ex);
  if (!Number.isFinite(mid)) { c.skipped++; return; } // mark before the venue's first depth — no reference yet
  c.checked++;
  const dMark = Math.abs(ev.mark - mid) / mid;
  const dIndex = Math.abs(ev.index - mid) / mid;
  if (dMark > MARK_MID_PCT) viol('mark-merge-sane', ev.ex + ': mark ' + ev.mark + ' vs mid ' + mid.toFixed(1) + ' -> ' + (dMark * 100).toFixed(2) + '%');
  if (dIndex > MARK_MID_PCT) viol('mark-merge-sane', ev.ex + ': index ' + ev.index + ' vs mid ' + mid.toFixed(1) + ' -> ' + (dIndex * 100).toFixed(2) + '%');
  if (Math.abs(ev.fundingRate) > FUNDING_ABS_MAX) viol('mark-merge-sane', ev.ex + ': |fundingRate| ' + ev.fundingRate + ' > ' + FUNDING_ABS_MAX + ' (unit drift?)');
}

// ─── The single sink — every adapter feeds normalized §4 events here ────────
function sink(ev) {
  if (!ev || !ev.ex || !ev.kind) return;
  countEvent(ev.ex, ev.kind);
  checkTs(ev);                       // check 3 applies to EVERY event kind (incl. 'oi')
  if (ev.kind === 'depth') onDepth(ev);
  else if (ev.kind === 'trade') onTrade(ev);
  else if (ev.kind === 'liq') onLiq(ev);
  else if (ev.kind === 'mark') onMark(ev);
  // 'oi': no price invariant to hold it to — event counting + ts sanity only.
}

// ─── Venue wiring — the terminal's exact legs (terminal.js SYM/SPOT/OKX_INST) ─
// The fake api per the L2 contract: adapters only ever touch markAlive/onStatus
// (check_terminal.cjs nullApi precedent); this probe has no watchdog to feed.
const fakeApi = { markAlive() {}, onStatus() {} };

const VENUES = [
  // Bybit v5 linear — PRIMARY (§2): publicTrade/orderbook.200/tickers/allLiquidation.
  { ex: 'bybit', adapter: A.makeBybitAdapter('BTCUSDT', sink) },
  // Binance fstream combined endpoint — depth20@100ms ONLY (§0.2 topic-filter reality).
  { ex: 'binancef', adapter: A.makeBinanceDepthAdapter('BTCUSDT', sink) },
  // Coinbase Advanced Trade — market_trades + heartbeats (spot tape leg).
  { ex: 'coinbase', adapter: A.makeCoinbaseAdapter('BTC-USD', sink) },
  // OKX v5 public — books + trades, BTC-USDT-SWAP (§4b; ctVal scaling inside the adapter).
  { ex: 'okx', adapter: A.makeOkxAdapter('BTC-USDT-SWAP', sink) },
];
for (const v of VENUES) {
  v.frames = 0;        // raw WS messages received (parse failures included — liveness measure)
  v.opened = false;
  v.status = 'connecting';
}

function connect(venue) {
  let ws;
  try {
    ws = new WebSocket(venue.adapter.url);
  } catch (e) {
    venue.status = 'connect-error: ' + (e && e.message ? e.message : e);
    return;
  }
  venue.ws = ws;
  ws.addEventListener('open', () => {
    venue.opened = true;
    venue.status = 'open';
    console.log('  [' + venue.ex + '] connected ' + venue.adapter.url);
    try { venue.adapter.subscribe(ws); } catch (e) { venue.status = 'subscribe-error: ' + e.message; }
    // Honor each adapter's keepalive contract (Bybit JSON op ping ≤20s, OKX
    // plain-text 'ping' ~25s, Coinbase heartbeat re-subscribe; Binance none —
    // protocol-level pings are answered by the WS implementation itself).
    if (venue.adapter.pingMs && venue.adapter.ping) {
      venue.pingTimer = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) {
          try { venue.adapter.ping(ws); } catch (_) { /* dying socket — close event reports it */ }
        }
      }, venue.adapter.pingMs);
    }
  });
  ws.addEventListener('message', (m) => {
    venue.frames++;
    let frame;
    try { frame = JSON.parse(m.data); } catch (_) { return; } // livewire.js discipline — OKX text 'pong' lands here
    try {
      venue.adapter.onMessage(frame, fakeApi);
    } catch (e) {
      // An adapter THROWING on a live frame is itself API drift (production
      // code choking on production wire) — count it against check 3's home
      // venue-agnostic tally would be wrong, so give it its own loud line.
      venue.adapterErrors = (venue.adapterErrors || 0) + 1;
      if (venue.adapterErrors <= MAX_EXAMPLES) {
        console.error('  [' + venue.ex + '] adapter threw: ' + e.message);
      }
    }
  });
  ws.addEventListener('error', (e) => {
    venue.status = 'error: ' + ((e && (e.message || (e.error && e.error.message))) || 'socket error');
  });
  ws.addEventListener('close', (e) => {
    if (venue.status === 'open') venue.status = 'closed (code ' + (e && e.code) + ')';
  });
}

// ─── Run ─────────────────────────────────────────────────────────────────────
const startedAt = new Date();
console.log('verify_wire_live — L2 live-wire invariants · BTCUSDT/BTC-USD/BTC-USDT-SWAP · '
  + seconds + 's · ' + startedAt.toISOString());
console.log('(production terminal-adapters.js + terminal-state.js against the live wire — no writes)');

for (const v of VENUES) connect(v);

// Binance REST premiumIndex + openInterest ONCE (§2 columns): reuse the
// production poller — start() fires an immediate sample of each endpoint,
// stop() immediately clears the recurring timers, so exactly one poll per
// endpoint flows through the real normalization into the same sink.
const restPoller = A.makeBinanceRestPoller('BTCUSDT', sink);
restPoller.start();
restPoller.stop();

let finished = false;
const mainTimer = setTimeout(() => finish('window elapsed'), seconds * 1000);

process.once('SIGINT', () => {
  console.log('\n^C — stopping early; reporting what was gathered so far');
  finish('interrupted (Ctrl-C)');
});

function finish(reason) {
  if (finished) return;
  finished = true;
  clearTimeout(mainTimer);
  for (const v of VENUES) {
    if (v.pingTimer) clearInterval(v.pingTimer);
    try { if (v.ws && v.ws.readyState <= WebSocket.OPEN) v.ws.close(); } catch (_) { /* already dead */ }
  }
  // End-of-run sweep for check 3: a stream that produced events but NEVER got
  // within the skew window cannot hide behind the seed-backlog exemption.
  for (const [key, st] of tsState) {
    if (st.count > 0 && !st.armed) {
      viol('event-ts-sane', key + ': no event ever within ' + TS_SKEW_MS + 'ms of local clock ('
        + st.count + ' events — dead clock or fully-stale stream)');
    }
  }
  report(reason);
  process.exit(exitCode());
}

function exitCode() {
  // Could-not-connect (exit 2): zero WS frames across ALL venues. The wire was
  // unreachable, so no invariant was observable — reporting exit 1 here would
  // cry "code bug" at an unplugged cable (§0 honesty: offline ≠ drift). A
  // REST-only success does not rescue this: the WS product surface — the
  // thing the terminal actually lives on — went unverified.
  const totalFrames = VENUES.reduce((a, v) => a + v.frames, 0);
  if (totalFrames === 0) return 2;
  for (const c of checks.values()) if (c.violations > 0) return 1;
  return 0;
}

// ─── Report ──────────────────────────────────────────────────────────────────
function pad(s, n) { s = String(s); return s.length >= n ? s : s + ' '.repeat(n - s.length); }
function padL(s, n) { s = String(s); return s.length >= n ? s : ' '.repeat(n - s.length) + s; }

function report(reason) {
  // Offline honesty: with zero frames a check was UNOBSERVED, not passed —
  // print 'n/a' instead of a hollow PASS (the exit-2 verdict says why).
  const offline = VENUES.reduce((a, v) => a + v.frames, 0) === 0;
  console.log('\n== invariant checks (' + reason + ') ==');
  console.log(pad('check', 22) + pad('result', 8) + padL('checked', 9) + padL('violations', 12) + '  notes');
  for (const name of CHECK_NAMES) {
    const c = checks.get(name);
    const result = c.violations > 0 ? 'FAIL' : (offline ? 'n/a' : 'PASS');
    const notes = [];
    if (c.skipped) notes.push('skipped=' + c.skipped + (name === 'trade-near-mid' || name === 'mark-merge-sane' ? ' (no mid yet)' : ''));
    if (name === 'event-ts-sane') {
      notes.push('skew=' + (c.skew || 0), 'nonmono=' + (c.nonmono || 0));
      const backlog = [...tsState.values()].reduce((a, st) => a + st.backlog, 0);
      if (backlog) notes.push('seed-backlog-skipped=' + backlog);
    }
    if (name === 'liq-well-formed' && c.checked === 0) notes.push('no liqs fired — quiet window is normal');
    console.log(pad(name, 22) + pad(result, 8) + padL(c.checked, 9) + padL(c.violations, 12) + '  ' + notes.join(' '));
    for (const ex of c.examples) console.log('    e.g. ' + ex);
  }

  console.log('\n== per-venue wire activity ==');
  const kinds = ['trade', 'depth', 'mark', 'oi', 'liq'];
  console.log(pad('venue', 10) + padL('frames', 8) + kinds.map((k) => padL(k, 8)).join('') + '  status');
  for (const v of VENUES) {
    const m = eventCounts.get(v.ex) || new Map();
    console.log(pad(v.ex, 10) + padL(v.frames, 8)
      + kinds.map((k) => padL(m.get(k) || 0, 8)).join('')
      + '  ' + v.status + (v.adapterErrors ? ' · adapter-errors=' + v.adapterErrors : ''));
  }
  console.log('(binancef mark/oi come from ONE REST poll each — premiumIndex + openInterest, §2;'
    + ' binancef trades are absent by topic-filter reality §0.2, expected)');

  // Warnings — connectivity facts, not invariant failures (see header).
  const warns = [];
  for (const v of VENUES) {
    if (v.frames === 0) warns.push(v.ex + ': 0 frames (' + v.status + ') — leg unverified this run');
  }
  const binDepth = (eventCounts.get('binancef') || new Map()).get('depth') || 0;
  const bin = VENUES.find((v) => v.ex === 'binancef');
  if (bin.frames > 0 && binDepth === 0) {
    warns.push('binancef: frames flowed but ZERO depth events — depth20 is the one topic that MUST flow (§0.2)');
  }
  for (const w of warns) console.log('WARN ' + w);

  const code = exitCode();
  const verdict = code === 0 ? 'PASS — live wire and production normalization agree'
    : code === 1 ? 'FAIL — invariant violation(s) above'
      : 'COULD NOT CONNECT — zero frames from every venue; invariants unobservable (offline is not a code bug)';
  console.log('\nRESULT: ' + verdict + ' (exit ' + code + ')');
}
