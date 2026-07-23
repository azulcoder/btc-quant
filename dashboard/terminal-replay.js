// terminal-replay.js — replay drivers for the orderflow terminal
// (DESIGN-orderflow-terminal.md §4 fixture mode / §4c BYOD mode).
//
// TWO MODES, one seam (terminal.js's startLeg calls drive() instead of
// livewire's makeSocket):
//
//   ?replay=1     FIXTURE mode — no WebSocket, no REST, no network beyond
//                 fetching the repo's own captured frames. The REAL adapters
//                 and REAL stores run unchanged; only the transport is
//                 replaced by a deterministic setTimeout clock replaying REAL
//                 captured wire frames from scripts/fixtures_ws.json
//                 (DESIGN §2 — actual captures, not synthesized data).
//
//   ?replay=byod  BYOD mode (§4c) — replays YOUR OWN recorded ticks from the
//                 collector's BYOD HTTP API (btcquant/collector.py §3,
//                 `make collector-api`) straight into the sink the stores
//                 consume. Adapters are BYPASSED here BY DESIGN: collector
//                 rows are ALREADY normalized (§0.6 conventions were applied
//                 when the collector wrote them). See the BYOD block below.
//
// HONESTY RAILS (§0, non-negotiable):
//   - NEITHER mode is ever presented as live. onStatus is called with
//     ('open', 'replay') / ('open', 'replay (byod)') — never 'live' — and
//     drive() prepends a visible replay flag to the page's permanent honesty
//     banner (§0.1) naming exactly what is being replayed.
//   - FIXTURE mode: frames are REAL captures. The only mutations are (a) timestamp REBASING
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
//   - BYOD mode: ts values pass through UNCHANGED — the rows are your own
//     recorded history, so the stored timestamps ARE the honest values (the
//     rebasing above exists only because fixture frames are canned captures
//     being re-dealt "now"). The replay ENDS when the recording does: chips
//     flip to 'stale' and nothing is fabricated past the last real row (§0.7).
//
// CLOCK (fixture mode): one module-level synthetic clock, anchored at the FIRST driven
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
// `BTCQ_TERMINAL_REPLAY = { active(), mode(), drive(name, adapter, api[, sink]),
// byodRowToEvent(table, row) }`, plus the quant.js dual-export so Node
// tooling can require() it without a DOM. drive()'s 4th arg is OPTIONAL and
// only consulted in BYOD mode — older 3-arg callers keep fixture behavior
// bit-for-bit (§4c backward-compat clause).
'use strict';

(function (global) {
  var FRAME_MS = 250;   // ~4 frames/s — fast enough to fill panels, slow enough to watch

  // Venue → ordered fixture keys. Order is LOAD-BEARING: book/tickers/trades
  // snapshots must precede their deltas/updates (the adapters' merge state —
  // e.g. Bybit's tickerView, the OKX seq chain, Coinbase's dedupe — expects
  // snapshot-first, exactly as the wire delivers on a fresh subscribe).
  //
  // T-2 (§4h): the matrix legs replay their own 2026-07-23 captures. The okx
  // and coinbase venues moved to the T-2 fixtures because their LIVE legs now
  // run the T-2 adapters (seq-chained okx books / exchange-feed l2+matches) —
  // replaying the old advanced-trade / checksum-less frames into them would
  // drive dead channels. binancef stays on depth20 ON PURPOSE: the live leg's
  // diff engine needs a REST snapshot to ever sync and replay forbids network,
  // so replay keeps the proven top-20 fixture leg (terminal.js seam comment).
  var VENUE_KEYS = {
    bybit: ['bybit_orderbook200_snapshot', 'bybit_orderbook200_delta', 'bybit_publicTrade',
            'bybit_tickers_snapshot', 'bybit_tickers_delta', 'bybit_allLiquidation'],
    binancef: ['binancef_depth20'],
    coinbase: ['t2_coinbase_l2_snapshot', 't2_coinbase_l2_update', 't2_coinbase_matches',
               't2_coinbase_heartbeat'],
    okx: ['t2_okxswap_books_snapshot', 't2_okxswap_books_update', 'okx_trades'],
    bybit_spot: ['t2_bybitspot_orderbook200_snapshot', 't2_bybitspot_orderbook200_delta',
                 't2_bybitspot_publicTrade'],
    binance_spot: ['t2_binancespot_aggtrade', 't2_binancespot_depthdiff'],
    okx_spot: ['t2_okxspot_books_snapshot', 't2_okxspot_books_update', 't2_okxspot_trades'],
  };

  // The terminal drives BTCUSDT (terminal.js SYM) — the captured allLiquidation
  // window printed JUPUSDT, so retarget the topic like check_terminal.cjs does.
  var LIQ_TOPIC = 'allLiquidation.BTCUSDT';

  /** 'fixture' (?replay=1) | 'byod' (?replay=byod) | null — which replay
   *  transport the URL asked for (guard typeof location — this file also
   *  loads under Node via the dual-export, where there is no URL). */
  function mode() {
    if (typeof location === 'undefined' || typeof location.search !== 'string') return null;
    if (/[?&]replay=1(?:&|$)/.test(location.search)) return 'fixture';
    if (/[?&]replay=byod(?:&|$)/.test(location.search)) return 'byod';
    return null;
  }

  /** true iff the page was opened in ANY replay mode — terminal.js's startLeg
   *  seam only needs the boolean (drive() itself dispatches on mode()). */
  function active() {
    return mode() !== null;
  }

  /** URL query param value (null when absent / not in a browser). */
  function qp(name) {
    if (typeof location === 'undefined' || typeof location.search !== 'string') return null;
    var m = new RegExp('[?&]' + name + '=([^&]*)').exec(location.search);
    return m ? decodeURIComponent(m[1]) : null;
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

  /** Coinbase (T-2: the EXCHANGE feed — snapshot/l2update/match/heartbeat all
   *  carry one ISO-8601 `.time`). Match trade_ids offset by 1e6 per loop pass
   *  (header note (c)) — the adapter dedupes on monotonic trade_id, so an
   *  unmodified second pass would be swallowed whole and the tape/CVD would
   *  stop accumulating. The id stays an INT like the wire sends it. */
  function rebaseCoinbase(f, ts, pass) {
    var iso = new Date(ts).toISOString();
    if (typeof f.time === 'string') f.time = iso;
    if (f.trade_id !== undefined) f.trade_id = Number(f.trade_id) + pass * 1000000;
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

  // T-2 (§4h): the matrix legs reuse their family's rebase — same clock
  // fields per venue family, only the endpoint/market differs (fixtures).
  var REBASE = {
    bybit: rebaseBybit, binancef: rebaseBinance, coinbase: rebaseCoinbase, okx: rebaseOkx,
    bybit_spot: rebaseBybit, binance_spot: rebaseBinance, okx_spot: rebaseOkx,
  };

  /** A frame's own primary exchange timestamp (ms) — only used once, to anchor
   *  the shared synthetic t0 at the first driven frame's REAL capture time
   *  (keeps rebased times consistent with untouched fields like Bybit's
   *  nextFundingTime instead of teleporting the tape decades away). */
  function primaryTs(venue, f) {
    if (venue === 'bybit' || venue === 'bybit_spot') return Number(f.ts);
    if (venue === 'binancef' || venue === 'binance_spot') return Number(f.data && f.data.E);
    if (venue === 'coinbase') return Date.parse(f.time);
    if (venue === 'okx' || venue === 'okx_spot') return Number(f.data && f.data[0] && f.data[0].ts);
    return NaN;
  }

  // Shared synthetic anchor — set by whichever venue drives first (terminal.js
  // always drives bybit first, so this is deterministic run-to-run).
  var t0 = null;

  // ─── Honesty banner flag — one span per page, dumb DOM (§0.1) ─────────────
  // Text is per-mode (fixture vs BYOD) and MAY BE UPDATED after insertion —
  // the BYOD failure path rewrites it to say the API is down and how to start
  // it. Same element either way: the harness keys on `.replay-flag` count==1.
  var flagEl = null;
  function flagBanner(text) {
    if (typeof document === 'undefined') return;
    if (!flagEl) {
      var banner = document.querySelector('.term-banner');
      if (!banner) return;   // banner is static HTML; missing means a bigger problem
      flagEl = document.createElement('span');
      flagEl.className = 'replay-flag';   // distinct style hook for the harness/CSS
      flagEl.style.fontWeight = '700';    // inline on purpose — this file owns no stylesheet
      banner.insertBefore(flagEl, banner.firstChild);
    }
    flagEl.textContent = text;
  }

  /**
   * Drive one venue. FIXTURE mode: the venue's REAL adapter is fed captured
   * frames on the synthetic clock — same call surface terminal.js would hand
   * to makeSocket: `adapter` is the untouched descriptor (only onMessage is
   * used — no socket exists to subscribe/ping), `api` carries the venue's
   * onStatus chip callback. BYOD mode (§4c): recorded rows drive `sink`
   * DIRECTLY and `adapter` is bypassed (see the BYOD block below for why).
   *
   * The 4th arg is OPTIONAL and only consulted under ?replay=byod: a 3-arg
   * caller keeps fixture behavior bit-for-bit even on a byod URL (§4c
   * backward-compat clause — without a sink there is nothing BYOD could feed).
   */
  function drive(name, adapter, api, sink) {
    if (mode() === 'byod' && typeof sink === 'function') {
      byodDrive(name, api, sink);
      return;
    }
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

      flagBanner('REPLAY MODE — recorded fixture frames (real captures, rebased clock), NOT live · ');
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

  // ═══ BYOD mode (§4c) — replay YOUR OWN recorded ticks from the collector ═══
  //
  // Rows come from the collector's BYOD HTTP API (btcquant/collector.py §3,
  // started with `make collector-api`). They were written through the SAME
  // §0.6 normalization conventions the live adapters apply — Bybit taker side
  // used as-is, liq side inverted to the liquidated position, depth stored as
  // merged best-first top-20 JSON — so the adapters are BYPASSED BY DESIGN:
  // byodRowToEvent is a pure field rename onto the §4 sink vocabulary and
  // nothing is re-derived (re-running an adapter over already-normalized rows
  // would double-apply conventions like the liq-side inversion).
  //
  // CLOCK CONTRAST with fixture mode (honesty-relevant): fixture frames are
  // canned captures being re-dealt "now", so their timestamps are REBASED
  // onto a synthetic session clock. BYOD rows are your own recorded history —
  // the stored ts_ms values ARE the honest values, so they pass through
  // UNCHANGED (no rebasing, §0.7 no fabrication); replay just walks a virtual
  // cursor across them at `speed`× on the same setTimeout-chain pattern
  // fixture mode uses (no Date.now() pacing). And a recording is FINITE:
  // unlike fixture mode's forever-loop, this replay ENDS when the data does.
  var BYOD_DEFAULT_API = 'http://127.0.0.1:8788';   // collector.py --api-port default
  var BYOD_DEFAULT_SPEED = 60;      // 60× — 10 recorded minutes ≈ 10 wall seconds
  var BYOD_WINDOW_MS = 600000;      // default window: last 10 min of available data
  var BYOD_PAGE_LIMIT = 5000;       // rows per request (server hard-caps at 10000)
  var BYOD_TICK_MS = 100;           // wall cadence of the virtual replay cursor
  var BYOD_PROBE_SPAN_MS = 631152000000;   // ~20y bisection ceiling for the newest-ts probe
  var BYOD_FLAG_TEXT = 'BYOD REPLAY — your recorded ticks (collector store), NOT live · ';

  // Endpoint ↔ table pairs. `table` matches collector.py _TABLE_COLUMNS and
  // byodRowToEvent's switch; array order doubles as the window-probe
  // preference (trades first — densest table, tightest end-of-data bound).
  var BYOD_ENDPOINTS = [
    { path: '/v1/trades', table: 'trades' },
    { path: '/v1/depth', table: 'depth_snapshots' },
    { path: '/v1/liquidations', table: 'liquidations' },
    { path: '/v1/funding', table: 'funding_mark' },
    { path: '/v1/oi', table: 'open_interest' },
  ];

  /**
   * One BYOD row → one normalized sink event (§4 vocabulary), or null for a
   * malformed row (dropped, never guessed — adapter rail). PURE and exported:
   * scripts/check_terminal.cjs replays synthetic rows through it in Node.
   *
   * Field spellings on the wire are the collector's schema columns (§3):
   * snake_case, ts in `ts_ms`, aggressor as `aggressor_buy`, depth sides as
   * JSON STRINGS. The stores speak the §4 camelCase event shapes. This
   * function is exactly that rename — the values themselves are already
   * normalized (see the block comment above).
   */
  function byodRowToEvent(table, row) {
    if (!row || typeof row !== 'object') return null;
    var ts = Number(row.ts_ms);
    if (!Number.isFinite(ts)) return null;   // an event with no time has no home in any store
    var ex = row.exchange;
    switch (table) {
      case 'trades':
        return {
          kind: 'trade', ex: ex, ts: ts,
          price: Number(row.price), qty: Number(row.qty),
          aggressorBuy: !!row.aggressor_buy,   // stored BOOLEAN — §0.6 inversions happened at record time
          id: row.trade_id,                    // VARCHAR (Bybit ids are UUIDs) — kept as-is
        };
      case 'depth_snapshots': {
        // bids/asks are stored as JSON strings '[[price,qty],…]' best-first
        // (§3 schema). Parse here; a corrupt row is dropped, never guessed.
        var bids, asks;
        try { bids = JSON.parse(row.bids); asks = JSON.parse(row.asks); }
        catch (_) { return null; }
        if (!Array.isArray(bids) || !Array.isArray(asks)) return null;
        // isSnapshot:true ALWAYS — every stored depth row is a full merged
        // top-20 book (the collector already applied Bybit deltas before
        // writing), so BookStore must replace wholesale, never merge.
        return { kind: 'depth', ex: ex, ts: ts, bids: bids, asks: asks, isSnapshot: true };
      }
      case 'liquidations':
        // `side` is already the LIQUIDATED position ('long'|'short') — the
        // print-side inversion (§0.6) was applied by normalize_bybit_liq.
        return {
          kind: 'liq', ex: ex, ts: ts, side: row.side,
          price: Number(row.price), qty: Number(row.qty),
          notionalUsd: Number(row.notional_usd),
        };
      case 'funding_mark':
        return {
          kind: 'mark', ex: ex, ts: ts,
          mark: Number(row.mark), index: Number(row.index),
          fundingRate: Number(row.funding_rate), nextFundingTs: Number(row.next_funding_ts),
        };
      case 'open_interest':
        return { kind: 'oi', ex: ex, ts: ts, oi: Number(row.oi) };
      default:
        return null;   // unknown table = seam drift with BYOD_ENDPOINTS — drop, don't guess
    }
  }

  /** from/to params accept epoch-ms ints or ISO-8601 — null if absent/unparseable. */
  function parseWhen(s) {
    if (s === null || s === '') return null;
    if (/^\d+$/.test(s)) return Number(s);   // all-digits → epoch ms as-is
    var t = Date.parse(s);                   // otherwise ISO 8601
    return Number.isFinite(t) ? t : null;
  }

  /** GET json with a 10s abort (terminal-hist.js getJSON idiom) — a hung
   *  localhost fetch must fail into the honest-error path, not spin forever. */
  function byodGet(url) {
    var ctrl = typeof AbortController !== 'undefined' ? new AbortController() : null;
    var timer = ctrl ? setTimeout(function () { ctrl.abort(); }, 10000) : null;
    function done() { if (timer !== null) clearTimeout(timer); }
    return fetch(url, ctrl ? { signal: ctrl.signal } : undefined)
      .then(function (res) {
        if (!res.ok) throw new Error('HTTP ' + res.status);
        return res.json();
      })
      .then(function (j) { done(); return j; }, function (e) { done(); throw e; });
  }

  /** One bounded page from a BYOD endpoint. The server returns rows ORDER BY
   *  ts_ms ASC with INCLUSIVE start_ms/end_ms bounds (collector.py _query_table). */
  function byodPage(base, path, sym, startMs, endMs, limit) {
    var url = base + path + '?limit=' + limit;
    if (sym) url += '&symbol=' + encodeURIComponent(sym);
    if (startMs !== null && startMs !== undefined) url += '&start_ms=' + Math.floor(startMs);
    if (endMs !== null && endMs !== undefined) url += '&end_ms=' + Math.floor(endMs);
    return byodGet(url).then(function (j) {
      return (j && Array.isArray(j.rows)) ? j.rows : [];
    });
  }

  /**
   * Fetch EVERY row of one table in [fromMs, toMs]: page BYOD_PAGE_LIMIT at a
   * time until a short page (§4c). The API surface has no offset/cursor and
   * only orders ts_ms ASC, so each next page restarts AT the last seen ts_ms
   * (inclusive) — starting at lastTs+1 would silently drop same-ms rows that
   * fell past the page cut, a fabricated gap (§0.7). The tie multiset cancels
   * the boundary-ms rows we already hold instead.
   */
  function byodFetchAll(base, path, sym, fromMs, toMs) {
    var out = [];
    function pageFrom(cursor, tie) {
      return byodPage(base, path, sym, cursor, toMs, BYOD_PAGE_LIMIT).then(function (rows) {
        var added = 0;
        for (var i = 0; i < rows.length; i++) {
          var row = rows[i];
          if (tie && Number(row.ts_ms) === cursor) {
            // Boundary-ms row: cancel one held copy per incoming copy
            // (multiset, not set — two IDENTICAL rows in one ms must survive).
            var key = JSON.stringify(row);
            var held = tie.get(key) || 0;
            if (held > 0) { tie.set(key, held - 1); continue; }
          }
          out.push(row);
          added++;
        }
        if (rows.length < BYOD_PAGE_LIMIT) return out;   // short page → table exhausted
        if (added === 0) {
          // > PAGE_LIMIT rows share one millisecond — this API surface cannot
          // page inside a ms losslessly. Step past it and SAY so rather than
          // loop forever (no real capture prints 5000 rows in one ms).
          console.warn('byod replay: >' + BYOD_PAGE_LIMIT + ' rows at ts_ms=' + cursor
            + ' (' + path + ') — stepping past; rows beyond the page cap in that ms are skipped');
          return pageFrom(cursor + 1, null);
        }
        var lastTs = Number(out[out.length - 1].ts_ms);
        var nextTie = new Map();
        for (var k = out.length - 1; k >= 0 && Number(out[k].ts_ms) === lastTs; k--) {
          var kk = JSON.stringify(out[k]);
          nextTie.set(kk, (nextTie.get(kk) || 0) + 1);
        }
        return pageFrom(lastTs, nextTie);
      });
    }
    return pageFrom(fromMs, null);
  }

  /**
   * Newest ts_ms in a table (upper bound, ≤1s slack). The API's only ordering
   * is ASC — limit=1 returns the OLDEST row — so the newest is found by
   * bisecting start_ms over "does any row exist at/after M": ~30 limit=1
   * probes against localhost, each O(index) server-side. Resolves null when
   * the table has no rows for this symbol (caller falls back to a wide window
   * — cheap, because every page on an empty table comes back short).
   */
  function byodProbeNewest(base, path, sym) {
    return byodPage(base, path, sym, null, null, 1).then(function (rows) {
      if (!rows.length) return null;
      var lo = Number(rows[0].ts_ms);        // oldest row = bisection floor (known data)
      var hi = lo + BYOD_PROBE_SPAN_MS;      // no recording spans 20 years
      var guard = 64;                        // hard cap ≫ log2(span/1s) ≈ 30 — never loops
      function step() {
        if (hi - lo <= 1000 || guard-- <= 0) return Promise.resolve(hi);
        var mid = Math.floor((lo + hi) / 2);
        return byodPage(base, path, sym, mid, null, 1).then(function (r) {
          if (r.length) lo = Number(r[0].ts_ms);   // a real row ≥ mid — newest is at/after it
          else hi = mid - 1;                       // nothing ≥ mid — newest is before it
          return step();
        });
      }
      return step();
    });
  }

  /** Resolve the replay window: explicit from/to params win; a missing `to`
   *  is probed from the data itself so the default is the last 10 minutes of
   *  AVAILABLE data (§4c) — not of wall time, which would usually be empty. */
  function byodWindow(base, sym, info, fromP, toP) {
    if (toP !== null) {
      return Promise.resolve({ from: fromP !== null ? fromP : toP - BYOD_WINDOW_MS, to: toP });
    }
    var counts = (info && info.row_counts) || {};
    var probe = null;
    for (var i = 0; i < BYOD_ENDPOINTS.length; i++) {
      if (Number(counts[BYOD_ENDPOINTS[i].table]) > 0) { probe = BYOD_ENDPOINTS[i]; break; }
    }
    if (!probe) {
      // Store is empty: any window works (every page comes back short and
      // empty) and the replay honestly ends with 0 rows — nothing is invented.
      return Promise.resolve({ from: fromP, to: null });
    }
    return byodProbeNewest(base, probe.path, sym).then(function (newest) {
      if (newest === null) return { from: fromP, to: null };   // probe table empty for this symbol
      return { from: fromP !== null ? fromP : newest - BYOD_WINDOW_MS, to: newest };
    });
  }

  // ONE shared session: BYOD rows are venue-tagged already (`exchange` column
  // → ev.ex), so one fetch serves every venue. The FIRST drive() call starts
  // the session; later calls only register their api so their chip mirrors
  // the shared status (§4c "drive called per venue" contract).
  var byod = { started: false, apis: [], status: null };

  function byodStatus(kind, msg) {
    byod.status = { kind: kind, msg: msg };
    for (var i = 0; i < byod.apis.length; i++) {
      var api = byod.apis[i];
      if (api && api.onStatus) api.onStatus(kind, msg);
    }
  }

  function byodDrive(name, api, sink) {
    if (api) {
      byod.apis.push(api);
      // A late registrant catches up to the session's current status — chip
      // state must not depend on the order terminal.js starts its legs.
      if (byod.status && api.onStatus) api.onStatus(byod.status.kind, byod.status.msg);
    }
    if (byod.started) return;   // session already running — this call only added its chip
    byod.started = true;

    var base = (qp('api') || BYOD_DEFAULT_API).replace(/\/+$/, '');
    var speed = Number(qp('speed'));
    if (!(speed > 0)) speed = BYOD_DEFAULT_SPEED;
    var fromP = parseWhen(qp('from'));
    var toP = parseWhen(qp('to'));

    flagBanner(BYOD_FLAG_TEXT);
    // Honest interim state: fetching/paging can take seconds — amber, not
    // silent, and definitely not 'open' before a single row has arrived.
    byodStatus('stale', 'byod: loading recorded window…');

    byodGet(base + '/v1/info').then(function (info) {
      // /v1/info doubles as the reachability probe and the shape probe:
      // `symbol` is what the store recorded (collector runs are
      // single-symbol, §3) and `row_counts` steers the window prober.
      var sym = (info && info.symbol) || 'BTCUSDT';
      return byodWindow(base, sym, info, fromP, toP).then(function (win) {
        var fetches = [];
        for (var i = 0; i < BYOD_ENDPOINTS.length; i++) {
          fetches.push((function (ep) {
            return byodFetchAll(base, ep.path, sym, win.from, win.to).then(function (rows) {
              var evs = [];
              for (var r = 0; r < rows.length; r++) {
                var ev = byodRowToEvent(ep.table, rows[r]);
                if (ev !== null) evs.push(ev);   // malformed rows dropped, never guessed
              }
              return evs;
            });
          })(BYOD_ENDPOINTS[i]));
        }
        return Promise.all(fetches);
      });
    }).then(function (perTable) {
      var events = [];
      for (var i = 0; i < perTable.length; i++) events = events.concat(perTable[i]);
      // Merge the five tables into ONE ts-ordered timeline. Array.prototype
      // .sort is stable, so same-ms events keep BYOD_ENDPOINTS order (trades,
      // depth, liqs, funding, oi) — deterministic run-to-run.
      events.sort(function (a, b) { return a.ts - b.ts; });
      byodPlay(events, sink, speed);
    }).catch(function (e) {
      // Honest failure (§4c): by far the most common cause is that the
      // collector simply isn't serving — chips go red and the banner says
      // exactly how to start it. No demo data is substituted (§0.7).
      console.error('byod replay: ' + (e && e.message ? e.message : e));
      byodStatus('error', 'byod api unreachable');
      flagBanner('BYOD REPLAY — collector API unreachable at ' + base
        + ' — start it with `make collector-api` · ');
    });
  }

  /** Walk the merged timeline at speed× on a setTimeout chain (fixture-mode
   *  pattern — no Date.now() pacing): each wall tick advances the virtual
   *  cursor BYOD_TICK_MS·speed recorded ms and emits every event it passed.
   *  Event ts values reach the sink UNCHANGED (see the block comment above). */
  function byodPlay(events, sink, speed) {
    if (!events.length) {
      // A window with nothing in it is a real (if disappointing) answer —
      // report it as an ended replay, don't widen the window behind the
      // user's back.
      byodStatus('stale', 'byod replay ended (0 rows in window)');
      return;
    }
    // §0 rail: 'replay (byod)', NEVER 'live' — every chip says what this is.
    byodStatus('open', 'replay (byod)');
    var i = 0;
    var cursor = events[0].ts;   // anchored at the window's own first ts — no rebasing
    function tick() {
      while (i < events.length && events[i].ts <= cursor) {
        try { sink(events[i]); }
        catch (e) {
          // One throwing event must not freeze the whole replay (same rule as
          // fixture mode's onMessage guard) — surface it and keep walking.
          console.error('byod replay: sink threw', e);
        }
        i++;
      }
      if (i >= events.length) {
        // A recording is FINITE: ending is the honest terminal state — freeze
        // at the last real row, flip chips to stale, fabricate nothing past
        // the end (§0.7). Contrast: fixture mode loops forever by design.
        byodStatus('stale', 'byod replay ended');
        return;
      }
      cursor += BYOD_TICK_MS * speed;
      setTimeout(tick, BYOD_TICK_MS);
    }
    tick();
  }

  var BTCQ_TERMINAL_REPLAY = { active: active, mode: mode, drive: drive, byodRowToEvent: byodRowToEvent };

  // Dual export (quant.js pattern): window global for the browser page,
  // module.exports so Node tooling can require() it without a DOM.
  if (typeof module !== 'undefined' && module.exports) module.exports = BTCQ_TERMINAL_REPLAY;
  if (typeof global !== 'undefined') global.BTCQ_TERMINAL_REPLAY = BTCQ_TERMINAL_REPLAY;
})(typeof globalThis !== 'undefined' ? globalThis : this);
