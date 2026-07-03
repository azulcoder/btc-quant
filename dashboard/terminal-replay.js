// terminal-replay.js — deterministic fixture-replay driver for the orderflow
// terminal (DESIGN-orderflow-terminal.md §4; browser verification harness L1).
//
// PURPOSE: with `?replay=1` in the URL, terminal.js routes each venue's adapter
// through drive() below INSTEAD of livewire's makeSocket — no WebSocket, no
// REST, no network beyond fetching the repo's own captured frames. The REAL
// adapters and REAL stores run unchanged; only the transport is replaced by a
// deterministic setTimeout clock replaying REAL captured wire frames from
// scripts/fixtures_ws.json (DESIGN §2 — actual captures, not synthesized data).
//
// HONESTY RAILS (§0, non-negotiable):
//   - This mode is NEVER presented as live. onStatus is called with
//     ('open', 'replay') — never 'live' — and drive() prepends a visible
//     "REPLAY MODE …" flag to the page's permanent honesty banner (§0.1).
//   - Frames are REAL captures. The only mutations are (a) timestamp REBASING
//     onto a synthetic clock (the frames' relative content is untouched),
//     (b) retargeting the allLiquidation topic symbol to the driven symbol
//     (same trick as scripts/check_terminal.cjs group 6 — the captured liq
//     window happened to print JUPUSDT; the convention under test is
//     symbol-independent), and (c) offsetting Coinbase trade_ids per loop pass
//     so the adapter's monotonic-id dedupe doesn't freeze accumulation on
//     replayed (identical) prints. Each mutation is commented at the site.
//   - §0.7 still holds: the stores accumulate only what "arrived" through the
//     adapters this session — the loop keeps the synthetic clock advancing so
//     session state keeps growing exactly as a live session would.
//
// CLOCK: one module-level synthetic clock, anchored at the FIRST driven
// frame's real exchange timestamp (t0) and shared by every venue — frames are
// dealt at FRAME_MS wall intervals (~4 frames/s) and each frame's exchange
// timestamps are rebased to t0 + i·FRAME_MS with i strictly monotonic per
// venue FOREVER (it never resets when the fixture sequence loops), so
// event-time always moves forward and the event-ts-gated machinery in
// terminal.js (depth sampler, liq-model gate, footprint bar roll) behaves as
// in a live session. Deterministic: same fixtures + same call order (terminal
// .js drives bybit first) ⇒ same synthetic timeline every run.
//
// Contract (§4 house style): plain-script IIFE exposing ONE global,
// `BTCQ_TERMINAL_REPLAY = { active(), drive(name, adapter, api) }`, plus the
// quant.js dual-export so Node tooling can require() it without a DOM.
'use strict';

(function (global) {
  var FRAME_MS = 250;   // ~4 frames/s — fast enough to fill panels, slow enough to watch

  // Venue → ordered fixture keys. Order is LOAD-BEARING: book/tickers/trades
  // snapshots must precede their deltas/updates (the adapters' merge state —
  // e.g. Bybit's tickerView, Coinbase's seed guard — expects snapshot-first,
  // exactly as the wire delivers on a fresh subscribe).
  var VENUE_KEYS = {
    bybit: ['bybit_orderbook200_snapshot', 'bybit_orderbook200_delta', 'bybit_publicTrade',
            'bybit_tickers_snapshot', 'bybit_tickers_delta', 'bybit_allLiquidation'],
    binancef: ['binancef_depth20'],
    coinbase: ['coinbase_market_trades_snapshot', 'coinbase_market_trades_update', 'coinbase_heartbeats'],
    okx: ['okx_books_snapshot', 'okx_books_update', 'okx_trades'],
  };

  // The terminal drives BTCUSDT (terminal.js SYM) — the captured allLiquidation
  // window printed JUPUSDT, so retarget the topic like check_terminal.cjs does.
  var LIQ_TOPIC = 'allLiquidation.BTCUSDT';

  /** true iff the page was opened with ?replay=1 (guard typeof location — this
   *  file also loads under Node via the dual-export, where there is no URL). */
  function active() {
    if (typeof location === 'undefined' || typeof location.search !== 'string') return false;
    return /[?&]replay=1(?:&|$)/.test(location.search);
  }

  // ─── Fixture fetch — ONCE, cached as a promise shared by all venues ───────
  // The page is served from dashboard/, the captures live in scripts/, hence
  // the ../ path (the verify harness serves the REPO ROOT so this resolves).
  var fixturesPromise = null;
  function loadFixtures() {
    if (!fixturesPromise) {
      fixturesPromise = fetch('../scripts/fixtures_ws.json').then(function (res) {
        if (!res.ok) throw new Error('fixtures_ws.json HTTP ' + res.status);
        return res.json();
      });
    }
    return fixturesPromise;
  }

  // ─── Per-family timestamp rebasing ────────────────────────────────────────
  // Each exchange hides its clock in different fields (wire shapes verified
  // against the captures — see terminal-adapters.js header). One small rebase
  // function per family; each receives a CLONE (never the cached fixture) and
  // stamps the synthetic ts into every field the adapter actually reads.

  /** Bybit envelope `.ts` (ms int) + per-item `.data[].T` (publicTrade and
   *  allLiquidation item timestamps — the adapters emit T, not the envelope). */
  function rebaseBybit(f, ts) {
    f.ts = ts;
    if (Array.isArray(f.data)) {
      for (var i = 0; i < f.data.length; i++) {
        if (f.data[i] && f.data[i].T !== undefined) f.data[i].T = ts;
      }
    }
    // Liq retargeting (header note (b)): real frames, retargeted symbol only.
    if (typeof f.topic === 'string' && f.topic.indexOf('allLiquidation.') === 0) f.topic = LIQ_TOPIC;
    return f;
  }

  /** Binance combined-stream wrap: the adapter reads `.data.E` (event time);
   *  `.data.T` (transaction time) is rebased too so the frame stays coherent. */
  function rebaseBinance(f, ts) {
    if (f.data) { f.data.E = ts; f.data.T = ts; }
    return f;
  }

  /** Coinbase speaks ISO-8601 STRINGS: envelope `.timestamp` and every
   *  `events[].trades[].time` (the adapter Date.parse()s trade time). Also
   *  offsets trade_id by 1e6 per loop pass (header note (c)) — the adapter
   *  dedupes on monotonic trade_id, so an unmodified second pass would be
   *  swallowed whole and the tape/CVD would stop accumulating. */
  function rebaseCoinbase(f, ts, pass) {
    var iso = new Date(ts).toISOString();
    f.timestamp = iso;
    if (Array.isArray(f.events)) {
      for (var i = 0; i < f.events.length; i++) {
        var trades = f.events[i] && f.events[i].trades;
        if (!Array.isArray(trades)) continue;
        for (var j = 0; j < trades.length; j++) {
          trades[j].time = iso;
          trades[j].trade_id = String(Number(trades[j].trade_id) + pass * 1000000);
        }
      }
    }
    return f;
  }

  /** OKX: per-row `.data[].ts` (numeric-string ms — books rows AND trade items
   *  carry their own ts; the books fixture has one row, `.data[0].ts`). Kept a
   *  string because that is what the wire sends and the adapter Number()s. */
  function rebaseOkx(f, ts) {
    if (Array.isArray(f.data)) {
      for (var i = 0; i < f.data.length; i++) {
        if (f.data[i] && f.data[i].ts !== undefined) f.data[i].ts = String(ts);
      }
    }
    return f;
  }

  var REBASE = { bybit: rebaseBybit, binancef: rebaseBinance, coinbase: rebaseCoinbase, okx: rebaseOkx };

  /** A frame's own primary exchange timestamp (ms) — only used once, to anchor
   *  the shared synthetic t0 at the first driven frame's REAL capture time
   *  (keeps rebased times consistent with untouched fields like Bybit's
   *  nextFundingTime instead of teleporting the tape decades away). */
  function primaryTs(venue, f) {
    if (venue === 'bybit') return Number(f.ts);
    if (venue === 'binancef') return Number(f.data && f.data.E);
    if (venue === 'coinbase') return Date.parse(f.timestamp);
    if (venue === 'okx') return Number(f.data && f.data[0] && f.data[0].ts);
    return NaN;
  }

  // Shared synthetic anchor — set by whichever venue drives first (terminal.js
  // always drives bybit first, so this is deterministic run-to-run).
  var t0 = null;

  // ─── Honesty banner flag — once per page, dumb DOM (§0.1) ─────────────────
  var flagged = false;
  function flagBanner() {
    if (flagged || typeof document === 'undefined') return;
    flagged = true;
    var banner = document.querySelector('.term-banner');
    if (!banner) return;   // banner is static HTML; missing means a bigger problem
    var el = document.createElement('span');
    el.className = 'replay-flag';   // distinct style hook for the harness/CSS
    el.style.fontWeight = '700';    // inline on purpose — this file owns no stylesheet
    el.textContent = 'REPLAY MODE — recorded fixture frames (real captures, rebased clock), NOT live · ';
    banner.insertBefore(el, banner.firstChild);
  }

  /**
   * Drive one venue's REAL adapter from captured frames on the synthetic clock.
   * Same call surface terminal.js would hand to makeSocket: `adapter` is the
   * untouched descriptor (only onMessage is used — no socket exists to
   * subscribe/ping), `api` carries the venue's onStatus chip callback.
   */
  function drive(name, adapter, api) {
    var keys = VENUE_KEYS[name];
    if (!keys) {
      // Unknown venue = seam drift between terminal.js and this map — say so
      // loudly on the chip rather than silently showing a dead-but-green leg.
      if (api && api.onStatus) api.onStatus('error', 'replay: no fixtures mapped for venue "' + name + '"');
      return;
    }

    // makeSocket normally wraps api with markAlive() for its watchdog; there is
    // no watchdog here (no socket can stall — the setTimeout chain IS the feed)
    // so provide the same surface as an inert no-op, keeping the adapters'
    // markAlive calls (tickers/heartbeats/depth frames) contract-identical.
    var replayApi = Object.assign({}, api, { markAlive: function () {} });

    loadFixtures().then(function (fx) {
      // Flatten this venue's fixture arrays in key order (snapshots first).
      var seq = [];
      for (var k = 0; k < keys.length; k++) {
        var frames = fx[keys[k]];
        if (Array.isArray(frames)) seq = seq.concat(frames);
      }
      if (!seq.length) { api.onStatus('error', 'replay: fixture keys empty for "' + name + '"'); return; }

      if (t0 === null) {
        var anchor = primaryTs(name, seq[0]);
        t0 = Number.isFinite(anchor) ? anchor : 0;
      }

      flagBanner();
      // §0 rail: 'replay', NEVER 'live' — the chip must say what this is.
      api.onStatus('open', 'replay');

      var i = 0;   // monotonic frame counter — never resets, so ts only advances
      var rebase = REBASE[name];
      function tick() {
        // Clone from the cached fixture EVERY pass — rebasing must never
        // mutate the shared fixture object other venues/loops read.
        var frame = JSON.parse(JSON.stringify(seq[i % seq.length]));
        var pass = Math.floor(i / seq.length);   // loop count → coinbase id offset
        rebase(frame, t0 + i * FRAME_MS, pass);
        try { adapter.onMessage(frame, replayApi); }
        catch (e) {
          // A throwing frame is a REAL adapter/fixture contract break — surface
          // it (the harness fails on console errors) but keep replaying: one
          // bad frame must not silently freeze the whole venue.
          console.error('replay(' + name + '): adapter.onMessage threw', e);
        }
        i++;
        setTimeout(tick, FRAME_MS);   // LOOP forever — session stores keep accumulating
      }
      tick();
    }).catch(function (e) {
      api.onStatus('error', 'replay: fixtures failed to load (' + e.message + ')');
    });
  }

  var BTCQ_TERMINAL_REPLAY = { active: active, drive: drive };

  // Dual export (quant.js pattern): window global for the browser page,
  // module.exports so Node tooling can require() it without a DOM.
  if (typeof module !== 'undefined' && module.exports) module.exports = BTCQ_TERMINAL_REPLAY;
  if (typeof global !== 'undefined') global.BTCQ_TERMINAL_REPLAY = BTCQ_TERMINAL_REPLAY;
})(typeof globalThis !== 'undefined' ? globalThis : this);
