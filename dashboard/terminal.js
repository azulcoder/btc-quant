// terminal.js — orderflow terminal bootstrap (DESIGN-orderflow-terminal.md §4).
//
// RESEARCH / DESCRIPTIVE ONLY (§0.1): everything on this page is LIVE-
// DESCRIPTIVE session state — never merged into a backtested series or the
// OOS harness; the permanent banner in terminal.html states it. Keyless
// public WS/REST only (§0.2), no orders, no signing.
//
// Wiring (§1 architecture): adapters → sink() → stores → rAF loop → views.
//   - Bybit v5 is the PRIMARY leg (trades/book/tickers/liqs — §2: Binance
//     Futures WS topic-filters trades/mark on this network, so Binance
//     contributes depth WS + REST-polled mark/OI only, and Coinbase the spot
//     tape). Footprint / CVD / profile / session range are fed from BYBIT
//     trades ONLY: blending venues into one bar/profile series would fabricate
//     a market that traded nowhere (§0.7 — per-source labels or nothing). The
//     tape is the one deliberate mix, and every row carries its exchange tag.
//   - ONE requestAnimationFrame loop with per-view dirty flags + per-view
//     minimum redraw intervals: a dirty flag says "the store changed", the
//     interval keeps a 100-trade/s burst from re-painting canvases 60×/s.
//     Views are only called when BOTH gates pass (§4 "no per-frame full redraw").
'use strict';

(function () {
  // Browser bootstrap only — bail harmlessly if loaded in a Node/vm sandbox
  // (the fixture smoke loads adapters/stores/views, not this bootstrap).
  if (typeof document === 'undefined') return;

  const LW = window.BTCQ_LIVEWIRE;
  const A = window.BTCQ_TERMINAL_ADAPTERS;
  const S = window.BTCQ_TERMINAL_STATE;
  const V = window.BTCQ_TERMINAL_VIEWS;
  if (!LW || !A || !S || !V) {
    // Script-order contract broken (§4 load order) — say so, render nothing.
    console.error('terminal.js: missing globals (load order must be livewire → adapters → state → views → terminal)');
    return;
  }

  const $ = (id) => document.getElementById(id);
  const SYM = 'BTCUSDT';       // Bybit + Binance Futures linear perp
  const SPOT = 'BTC-USD';      // Coinbase Advanced Trade product

  // ─── Settings (persisted to localStorage 'btcq-terminal', §4) ───────────
  //
  // Only values from the whitelists below are accepted back from storage — a
  // hand-edited localStorage must not put the stores into an unsupported state.
  const LS_KEY = 'btcq-terminal';
  const TICKS = [1, 5, 10, 25, 50];        // $ tick grouping (default 10 — §4 task spec)
  const BARS = [60000, 300000];            // footprint bar interval: 1m | 5m
  const DEFAULTS = { tick: 10, barMs: 60000, tapeMin: 0 };

  function loadSettings() {
    const s = Object.assign({}, DEFAULTS);
    try {
      const j = JSON.parse(localStorage.getItem(LS_KEY) || '{}');
      if (TICKS.indexOf(j.tick) >= 0) s.tick = j.tick;
      if (BARS.indexOf(j.barMs) >= 0) s.barMs = j.barMs;
      if (Number.isFinite(j.tapeMin) && j.tapeMin >= 0) s.tapeMin = j.tapeMin;
    } catch (_) { /* corrupt storage → defaults */ }
    return s;
  }
  const settings = loadSettings();
  function saveSettings() {
    try {
      localStorage.setItem(LS_KEY, JSON.stringify({
        tick: settings.tick, barMs: settings.barMs, tapeMin: settings.tapeMin,
      }));
    } catch (_) { /* private mode / quota — settings just don't persist */ }
  }

  // Pause is deliberately NOT persisted: a page that loads pre-paused looks
  // exactly like a dead feed — an honesty-rail footgun. Pause also only
  // freezes RENDERING; the stores keep ingesting, so resuming shows the true
  // session (a paused-ingest design would leave a gap we'd be tempted to
  // paper over — §0.7 gaps stay gaps, so we never create one).
  let paused = false;

  // ─── Stores (terminal-state.js §4) ───────────────────────────────────────
  const tape = S.TapeStore(3000);
  const liq = S.LiqStore(500);
  const cvd = S.CvdStore({ bucketsUsd: [1e4, 1e5, 1e6] });   // §4: CVD-by-trade-size defaults
  const aggBook = S.AggBookStore(['bybit', 'binancef']);
  const bybitBook = aggBook.books.get('bybit');   // DOM ladder = the primary venue's book

  // Footprint + profile are constructed AGAINST a tick/bar size, so changing
  // either setting rebuilds the store and restarts its session aggregation.
  // Honest limitation, stated in the settings row: we keep no raw tick store
  // in the browser to re-bucket from (that's the collector's job, §3), and
  // synthesizing the old bars onto a new grid would be fabrication (§0.7).
  let footprint, profile;
  function rebuildFootprint() { footprint = S.FootprintStore({ barMs: settings.barMs, tickSize: settings.tick }); }
  function rebuildProfile() { profile = S.ProfileStore({ tickSize: settings.tick }); }
  rebuildFootprint();
  rebuildProfile();

  // ─── Header-stat state (latest-value caches + session extremes) ─────────
  const marks = {};      // ex → latest normalized mark event
  const ois = {};        // ex → latest normalized oi event
  const statuses = {};   // ex → { kind, msg } from each socket's onStatus
  let sessionHigh = NaN, sessionLow = NaN;   // Bybit perp prints since page open
  let lastPrice = NaN;

  // ─── Dirty flags — the ONLY signal that a view needs repainting ─────────
  const dirty = { fp: true, dom: true, tape: true, agg: true, header: true, liq: true };
  function dirtyAll() { for (const k in dirty) dirty[k] = true; }

  // ─── The sink: every normalized adapter event funnels through here (§4) ──
  function sink(ev) {
    switch (ev.kind) {
      case 'trade':
        tape.push(ev);
        dirty.tape = true;
        if (ev.ex === 'bybit') {
          // Primary-leg flow stores only (see header note on venue blending).
          footprint.onTrade(ev);
          profile.onTrade(ev);
          cvd.onTrade(ev);
          if (!(sessionHigh >= ev.price)) sessionHigh = ev.price;   // NaN-safe first print
          if (!(sessionLow <= ev.price)) sessionLow = ev.price;
          lastPrice = ev.price;
          dirty.fp = true;
          dirty.dom = true;      // ladder session sold/bought columns move with trades
          dirty.header = true;   // session high/low + topbar price
        }
        break;
      case 'depth':
        aggBook.applyDepth(ev);   // routes by ev.ex (bybit delta merge / binance snapshots)
        dirty.agg = true;
        if (ev.ex === 'bybit') dirty.dom = true;
        break;
      case 'liq':
        liq.push(ev);
        dirty.liq = true;
        break;
      case 'mark':
        marks[ev.ex] = ev;
        dirty.header = true;
        break;
      case 'oi':
        ois[ev.ex] = ev;
        dirty.header = true;
        break;
      default:
        // Unknown kind = adapter/store contract drift — drop loudly in dev,
        // never guess a home for the data.
        break;
    }
  }

  // ─── Views (terminal-views.js §4) — mount into terminal.html anchors ─────
  const headerView = V.HeaderStatsView();
  headerView.mount($('view-header'));

  const fpView = V.FootprintView();
  fpView.mount($('view-footprint'), { cvdEl: $('view-cvd'), buckets: cvd.buckets });

  const domView = V.DomLadderView();
  domView.mount($('view-dom'), { levels: 12 });

  const aggView = V.AggBookView();
  aggView.mount($('view-aggbook'), { levels: 14 });

  const tapeView = V.TapeView();
  const tapeMinInput = $('set-tape-min');
  tapeMinInput.value = String(settings.tapeMin);
  tapeView.mount($('view-tape'), {
    filterInput: tapeMinInput,   // the input lives in the settings row; the view owns its behavior
    onFilter: (v) => { settings.tapeMin = v; saveSettings(); dirty.tape = true; },
    whaleUsd: 250000,            // §4 whale emphasis threshold
  });

  const liqView = V.LiqFeedView();
  liqView.mount($('view-liq'));

  // ─── Live legs: three sockets + one REST poller (§2 data matrix) ────────
  //
  // Each leg gets its own onStatus → chip; legs are independent — any subset
  // alive keeps its own panels moving and the rest degrade honestly (chips go
  // amber/red, panels freeze at their last real data; nothing is interpolated).
  function chipStatus(ex) {
    return (kind, msg) => { statuses[ex] = { kind, msg }; dirty.header = true; };
  }
  LW.makeSocket(A.makeBybitAdapter(SYM, sink), { onStatus: chipStatus('bybit') });
  LW.makeSocket(A.makeBinanceDepthAdapter(SYM, sink), { onStatus: chipStatus('binancef') });
  LW.makeSocket(A.makeCoinbaseAdapter(SPOT, sink), { onStatus: chipStatus('coinbase') });
  const poller = A.makeBinanceRestPoller(SYM, sink);   // mark 5s / OI 60s → 'binancef' columns
  poller.start();

  // ─── Settings row wiring ────────────────────────────────────────────────
  const tickSel = $('set-tick');
  tickSel.value = String(settings.tick);
  tickSel.addEventListener('change', () => {
    const v = Number(tickSel.value);
    if (TICKS.indexOf(v) < 0) return;
    settings.tick = v;
    saveSettings();
    // New grid → footprint AND profile restart (see rebuild note above); the
    // book ladders regroup instantly because grouping is a render-time param.
    rebuildFootprint();
    rebuildProfile();
    dirtyAll();
  });

  const barSel = $('set-bar');
  barSel.value = String(settings.barMs);
  barSel.addEventListener('change', () => {
    const v = Number(barSel.value);
    if (BARS.indexOf(v) < 0) return;
    settings.barMs = v;
    saveSettings();
    rebuildFootprint();   // bar interval only affects the footprint store
    dirty.fp = true;
    dirty.dom = true;     // ladder session columns read footprint bars
  });

  const pauseBtn = $('set-pause');
  pauseBtn.addEventListener('click', () => {
    paused = !paused;
    pauseBtn.textContent = paused ? 'resume' : 'pause';
    pauseBtn.setAttribute('aria-pressed', String(paused));
    if (!paused) dirtyAll();   // repaint everything that moved while frozen
  });

  // ─── Render loop: one rAF, per-view dirty flag AND min-interval gate ────
  //
  // Intervals are per-view redraw budgets (ms), tuned to each panel's cost:
  // the footprint repaints hundreds of cells (250ms), the DOM ladder is a
  // fixed text-pool update (120ms), the CVD chart throttles itself further
  // inside FootprintView. Event ingestion is NEVER throttled — only paint.
  const MIN_MS = { fp: 250, dom: 120, tape: 180, agg: 220, header: 400, liq: 300 };
  const lastAt = { fp: 0, dom: 0, tape: 0, agg: 0, header: 0, liq: 0 };

  function due(key, now) {
    if (!dirty[key] || now - lastAt[key] < MIN_MS[key]) return false;
    dirty[key] = false;
    lastAt[key] = now;
    return true;
  }

  const priceEl = $('last-price');

  function frame() {
    const now = Date.now();

    // The header (with its conn chips) renders even while paused: pause
    // freezes the MARKET panels, never connection health — a paused page that
    // also froze its chips could hide a dead feed behind the pause button.
    if (due('header', now)) {
      headerView.render({ marks, ois, statuses, sessionHigh, sessionLow, nowMs: now });
      priceEl.textContent = Number.isFinite(lastPrice)
        ? '$' + lastPrice.toLocaleString('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 1 })
        : '—';
    }

    if (!paused) {
      if (due('fp', now)) {
        fpView.render({
          bars: footprint.bars(),
          profile: profile.profile(),
          cvd: cvd.series(),
          tickSize: settings.tick,
          nowMs: now,
        });
      }
      if (due('dom', now)) {
        domView.render({
          grouped: bybitBook.grouped(settings.tick, 12),
          best: bybitBook.best(),
          bars: footprint.bars(),
          tickSize: settings.tick,
        });
      }
      if (due('agg', now)) {
        aggView.render({ grouped: aggBook.grouped(settings.tick, 14) });
      }
      if (due('tape', now)) {
        tapeView.render({ trades: tape.filtered(settings.tapeMin) });
      }
      if (due('liq', now)) {
        // Wall-clock nowTs so the rolling 1m/5m sums DECAY during quiet spells
        // (the store's default anchor is the last event — replay-honest but a
        // live view wants live windows; LiqStore doc invites exactly this).
        liqView.render({
          recent: liq.recent(40),
          sum1m: liq.sumWindow(60000, now),
          sum5m: liq.sumWindow(300000, now),
        });
      }
    }

    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);

  // Time itself moves the funding countdown and rolls liqs out of the 1m/5m
  // windows even when no event arrives — tick those panels once a second.
  setInterval(() => { dirty.header = true; dirty.liq = true; }, 1000);

  // Canvas panels re-measure on their next draw; a resize makes them dirty.
  window.addEventListener('resize', () => { dirty.fp = true; dirty.agg = true; });
})();
