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
//     contributes depth WS + REST-polled mark/OI only, Coinbase the spot
//     tape, and OKX (O-2, §4b) a deeper agg-book leg + its own CVD line).
//     Footprint / profile / session range are fed from BYBIT trades ONLY:
//     blending venues into one bar/profile series would fabricate a market
//     that traded nowhere (§0.7 — per-source labels or nothing). CVD is
//     per-exchange BY CONSTRUCTION (§4b): one store per venue, one labeled
//     line each, plus an exact Σ — never an unlabeled blend. The tape is the
//     one deliberate mix, and every row carries its exchange tag.
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
  if (!LW || !A || !S || !V || !window.BTCQ_TERMINAL_HIST) {
    // Script-order contract broken (§4/§4c load order) — say so, render nothing.
    console.error('terminal.js: missing globals (load order must be livewire → adapters → state → hist → views → terminal)');
    return;
  }

  const $ = (id) => document.getElementById(id);
  const SYM = 'BTCUSDT';       // Bybit + Binance Futures linear perp
  const SPOT = 'BTC-USD';      // Coinbase Advanced Trade product
  const OKX_INST = 'BTC-USDT-SWAP';   // OKX linear swap (O-2, §4b — sizes in CONTRACTS, adapter ctVal-scales)

  // ─── Settings (persisted to localStorage 'btcq-terminal', §4) ───────────
  //
  // Only values from the whitelists below are accepted back from storage — a
  // hand-edited localStorage must not put the stores into an unsupported state.
  const LS_KEY = 'btcq-terminal';
  const TICKS = [1, 5, 10, 25, 50];        // $ tick grouping (default 10 — §4 task spec)
  const BARS = [60000, 300000];            // footprint bar interval: 1m | 5m
  const LIQ_RANGES = ['pct6', 'all'];      // liq-heatmap window: mark ± 6% (default) | full tier extent
  const DEFAULTS = { tick: 10, barMs: 60000, tapeMin: 0, liqRange: 'pct6' };

  function loadSettings() {
    const s = Object.assign({}, DEFAULTS);
    try {
      const j = JSON.parse(localStorage.getItem(LS_KEY) || '{}');
      if (TICKS.indexOf(j.tick) >= 0) s.tick = j.tick;
      if (BARS.indexOf(j.barMs) >= 0) s.barMs = j.barMs;
      if (Number.isFinite(j.tapeMin) && j.tapeMin >= 0) s.tapeMin = j.tapeMin;
      if (LIQ_RANGES.indexOf(j.liqRange) >= 0) s.liqRange = j.liqRange;
    } catch (_) { /* corrupt storage → defaults */ }
    return s;
  }
  const settings = loadSettings();
  function saveSettings() {
    try {
      localStorage.setItem(LS_KEY, JSON.stringify({
        tick: settings.tick, barMs: settings.barMs, tapeMin: settings.tapeMin,
        liqRange: settings.liqRange,
      }));
    } catch (_) { /* private mode / quota — settings just don't persist */ }
  }

  // Pause is deliberately NOT persisted: a page that loads pre-paused looks
  // exactly like a dead feed — an honesty-rail footgun. Pause also only
  // freezes RENDERING; the stores keep ingesting, so resuming shows the true
  // session (a paused-ingest design would leave a gap we'd be tempted to
  // paper over — §0.7 gaps stay gaps, so we never create one).
  let paused = false;

  // ─── Stores (terminal-state.js §4 + §4b) ─────────────────────────────────
  const tape = S.TapeStore(3000);
  const liq = S.LiqStore(500);
  // Per-exchange CVD (§4b): each venue gets its OWN session-anchored store —
  // the view labels every line per venue and computes the exact Σ itself.
  // Buckets only matter on the bybit store (the by-trade-size lines stay
  // single-venue, same §0.7 reasoning as the footprint).
  const CVD_EXS = ['bybit', 'okx', 'coinbase'];
  const cvds = {};
  for (const ex of CVD_EXS) cvds[ex] = S.CvdStore({ bucketsUsd: [1e4, 1e5, 1e6] });   // §4 defaults
  const aggBook = S.AggBookStore(['bybit', 'binancef', 'okx']);   // §4b: okx joins the merged book
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

  // ─── O-2 stores (§4b): depth history + detector + liq model ─────────────
  //
  // All three are constructed AGAINST settings.tick (the detector's grid MUST
  // match the grouped() ladders it is fed — terminal-state.js contract), so a
  // tick change rebuilds them and restarts their session accumulation — the
  // same honest restart rule as footprint/profile above, stated in the
  // settings hint (re-bucketing recorded ladders onto a new grid would
  // fabricate ladders that never stood, §0.7).
  const HIST_EXS = ['bybit', 'binancef', 'okx'];   // every leg that carries a book
  let depthHist;        // ex → DepthHistoryStore (ring of real 1/s ladder samples)
  let detector;         // SpoofIcebergDetector — BYBIT-only feed (see sampler note)
  let liqModel;         // LiqHeatmapModel — ESTIMATED bands (§0.4)
  let liqEst = null;    // last estimate() result (recomputed on the 5s event-ts gate)
  let lastEstTs = -Infinity;
  // Per-venue last-price trails for the heatmap polyline overlay: one {ts,
  // price} point per depth SAMPLE, from that venue's OWN trades (§0.7 — a
  // bybit price line over an okx book would be a silent venue blend). binancef
  // has no trades on this network (§0.2) → no trail, honestly absent.
  let priceTrail;       // ex → [{ts, price}]
  const lastPriceByEx = {};   // ex → latest trade price (trail source)
  let lastSampleTs;     // ex → event-ts of the last taken depth sample (the ≥1s gate)
  function rebuildHeatmapStores() {
    depthHist = {}; priceTrail = {}; lastSampleTs = {};
    for (const ex of HIST_EXS) {
      depthHist[ex] = S.DepthHistoryStore({ tickSize: settings.tick, maxSamples: 3600, nLevels: 40 });
      priceTrail[ex] = [];
      lastSampleTs[ex] = -Infinity;
    }
    detector = S.SpoofIcebergDetector({ tickSize: settings.tick });   // §4b defaults, grid matched
    liqModel = S.LiqHeatmapModel({ tickSize: settings.tick });        // §4b defaults (tiers/mmr)
    liqEst = null;
    lastEstTs = -Infinity;
  }
  rebuildHeatmapStores();

  // ─── Header-stat state (latest-value caches + session extremes) ─────────
  const marks = {};      // ex → latest normalized mark event
  const ois = {};        // ex → latest normalized oi event
  const statuses = {};   // ex → { kind, msg } from each socket's onStatus
  const lastDepthTs = {};   // ex → newest depth EVENT ts — drives the 1/s sampler gate
  let sessionHigh = NaN, sessionLow = NaN;   // Bybit perp prints since page open
  let lastPrice = NaN;

  // ─── Dirty flags — the ONLY signal that a view needs repainting ─────────
  const dirty = {
    fp: true, dom: true, tape: true, agg: true, header: true, liq: true,
    heat: true, liqmap: true, det: true,   // O-2 panels (§4b)
    hist: true, tpo: true, vp: true, farb: true, macro: true,   // O-3 STRUCTURE panels (§4c)
  };
  function dirtyAll() { for (const k in dirty) dirty[k] = true; }

  // ─── The sink: every normalized adapter event funnels through here (§4) ──
  function sink(ev) {
    switch (ev.kind) {
      case 'trade':
        tape.push(ev);
        dirty.tape = true;
        // Per-exchange CVD (§4b): each venue's trades feed ONLY its own
        // labeled store — the panel legend names every line per venue.
        if (cvds[ev.ex]) { cvds[ev.ex].onTrade(ev); dirty.fp = true; }
        lastPriceByEx[ev.ex] = ev.price;   // heatmap polyline trail source
        if (ev.ex === 'bybit') {
          // Primary-leg flow stores only (see header note on venue blending).
          // §0.7 RAIL: OKX (and coinbase) trades deliberately NEVER reach
          // footprint/profile — blending venues into one bar/VP series would
          // fabricate a market that traded nowhere. OKX flow appears ONLY in
          // per-source-labeled places: the tape (tagged), its own CVD line,
          // and the agg book's stacked okx segments.
          footprint.onTrade(ev);
          profile.onTrade(ev);
          // Detector trades come from BYBIT only — it must see the SAME venue
          // as the depth samples it correlates (traded-volume-vs-wall math is
          // per-book; §4b feeds the detector from the primary venue). New
          // events are picked up by the frame loop's identity check, so no
          // dirty flag here — a trade that fires no rule repaints nothing.
          detector.onTrade(ev);
          if (!(sessionHigh >= ev.price)) sessionHigh = ev.price;   // NaN-safe first print
          if (!(sessionLow <= ev.price)) sessionLow = ev.price;
          lastPrice = ev.price;
          dirty.fp = true;
          dirty.dom = true;      // ladder session sold/bought columns move with trades
          dirty.header = true;   // session high/low + topbar price
        }
        break;
      case 'depth':
        aggBook.applyDepth(ev);   // routes by ev.ex (bybit/okx delta merge / binance snapshots)
        // Event-ts bookkeeping for the 1/s depth sampler (frame loop below):
        // the GATE lives here as data, the sampling happens on rAF — stores
        // stay Date.now()-free (§4b replay rail).
        if (Number.isFinite(ev.ts) && !(lastDepthTs[ev.ex] >= ev.ts)) lastDepthTs[ev.ex] = ev.ts;
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
  fpView.mount($('view-footprint'), {
    cvdEl: $('view-cvd'),
    buckets: cvds.bybit.buckets,   // by-size lines read the bybit store only
    cvdExs: CVD_EXS,               // §4b: one labeled line per venue + exact Σ
  });

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

  // ── O-2 views (§4b) ──
  const bookHeatView = V.BookHeatmapView();
  bookHeatView.mount($('view-bookheat'), { velocityInput: $('set-heat-vel') });
  // Heatmap venue selector: one DepthHistoryStore per book leg exists; the
  // panel shows one at a time (bybit default — deepest book, 200 levels).
  const heatVenueSel = $('set-heat-venue');
  let heatVenue = 'bybit';
  heatVenueSel.value = heatVenue;
  heatVenueSel.addEventListener('change', () => {
    if (HIST_EXS.indexOf(heatVenueSel.value) < 0) return;
    heatVenue = heatVenueSel.value;
    dirty.heat = true;
  });

  const liqHeatView = V.LiqHeatmapView();
  // Range toggle (visual-defect fix): the select lives in the panel chrome
  // (terminal.html); the view owns its behavior, persistence lives here —
  // the same split as the tape filter and the heatmap velocity toggle.
  const liqRangeSel = $('set-liq-range');
  liqRangeSel.value = settings.liqRange;
  liqHeatView.mount($('view-liqheat'), {
    rangeInput: liqRangeSel,
    onRange: (v) => {
      if (LIQ_RANGES.indexOf(v) < 0) return;
      settings.liqRange = v;
      saveSettings();
      dirty.liqmap = true;   // repaint from stores too (view already redrew its cache)
    },
  });

  const detView = V.DetectionFeedView();
  detView.mount($('view-detect'));

  // ─── Live legs: three sockets + one REST poller (§2 data matrix) ────────
  //
  // Each leg gets its own onStatus → chip; legs are independent — any subset
  // alive keeps its own panels moving and the rest degrade honestly (chips go
  // amber/red, panels freeze at their last real data; nothing is interpolated).
  function chipStatus(ex) {
    // Normalize the transport's open-state prose to a short chip token: livewire's
    // makeSocket says 'live feed connected' / 'live feed recovered' — both ARE a live
    // socket, so the chip reads 'live'; anything else (the replay driver's 'replay')
    // passes through VERBATIM so fixture replay is never dressed up as live (§0 —
    // verify_terminal_browser.py asserts no chip says 'live' under ?replay=1).
    return (kind, msg) => {
      const short = (kind === 'open' && /^live feed/.test(msg || '')) ? 'live' : msg;
      statuses[ex] = { kind, msg: short };
      dirty.header = true;
    };
  }
  // Replay seam (L1 verification, DESIGN §0 honesty rails): with ?replay=1 the
  // REAL adapters are driven from captured fixture frames on a deterministic
  // synthetic clock (terminal-replay.js) instead of live sockets — chips say
  // 'replay', never 'live'. The startLeg indirection is the WHOLE seam: same
  // adapter, same api, only the transport differs.
  const REPLAY = window.BTCQ_TERMINAL_REPLAY && window.BTCQ_TERMINAL_REPLAY.active();
  function startLeg(name, adapter, api) {
    // O-3 BYOD seam (§4c): sink rides along as the optional 4th arg — under
    // ?replay=byod the driver feeds collector rows to the sink DIRECTLY
    // (rows are already normalized; adapters bypassed); under ?replay=1 the
    // 4th arg is ignored and fixture replay is bit-for-bit unchanged.
    if (REPLAY) window.BTCQ_TERMINAL_REPLAY.drive(name, adapter, api, sink);
    else LW.makeSocket(adapter, api);
  }
  startLeg('bybit', A.makeBybitAdapter(SYM, sink), { onStatus: chipStatus('bybit') });
  startLeg('binancef', A.makeBinanceDepthAdapter(SYM, sink), { onStatus: chipStatus('binancef') });
  startLeg('coinbase', A.makeCoinbaseAdapter(SPOT, sink), { onStatus: chipStatus('coinbase') });
  // O-2 (§4b): OKX leg — deeper agg book + its own labeled CVD line. The
  // adapter ctVal-scales CONTRACT sizes to BTC; default 0.01 is the pinned
  // BTC-USDT-SWAP value (fixtures _okx_ctval_note). Chip semantics identical.
  startLeg('okx', A.makeOkxAdapter(OKX_INST, sink), { onStatus: chipStatus('okx') });
  if (!REPLAY) {
    // REST poller skipped in replay: it is real network (fapi.binance.com) and
    // wall-clock-timed — both break the deterministic no-network replay rail.
    const poller = A.makeBinanceRestPoller(SYM, sink);   // mark 5s / OI 60s → 'binancef' columns
    poller.start();
  }

  // ─── O-3 STRUCTURE section (§4c): REST-fed panels + their polls ──────────
  //
  // All O-3 data is REST history / REST polls (terminal-hist.js). In replay
  // modes EVERY new fetch/poll is skipped — they are real network and would
  // break the deterministic L1 harness (same rail as the Binance REST poller
  // above) — and each panel renders an honest 'disabled in replay' note
  // instead of an empty-looking widget (empty-but-honest, §0.7: we say WHY
  // there is nothing rather than fabricate something).
  const HIST = window.BTCQ_TERMINAL_HIST;
  let histView = null, tpoView = null, vpView = null, farbView = null, macroView = null;
  // O-3 state caches (REST results; the frame loop only reads them).
  let histBars = null;             // current-interval klines (chart + composite VP)
  let histInterval = '60';         // bybit interval code — html select default (1h)
  let tpoSessions = null, tpoTick = 10;
  let vpData = null, vpTick = 10;
  let okxFund = null, okxOiEv = null;   // OKX REST poll results (null → '—' cells)
  const hlMids = {};               // latest HIP-3 mids by prefixed name (km:…/xyz:…)
  const macroLasts = { PAXG: NaN, ETH: NaN };   // hourly kline last closes
  let corr7d = null;               // {btcEth, btcPaxg, ethPaxg} — last rollingCorr values
  const sessStore = S.SessionSeriesStore({ sampleMs: 60000 });   // §4c session-corr accumulator

  /** Chart-friendly tick: smallest of 1/2/2.5/5×10^k covering `raw` — the
   *  §4c 'niceRound' for adaptive TPO/VP grids (a raw range/60 would produce
   *  ticks like $37.42 and unreadable axis labels). */
  function niceRound(raw) {
    if (!Number.isFinite(raw) || raw <= 0) return 1;
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    for (const m of [1, 2, 2.5, 5, 10]) if (m * mag >= raw) return m * mag;
    return 10 * mag;
  }

  if (REPLAY) {
    // Honest replay note per §4c panel (see section header). The bar-replay
    // control row is hidden too — inert controls would look broken, and the
    // note already says why the panel is empty.
    const NOTE = '<div class="chart-na">awaiting REST history — REST fetching is disabled in replay '
      + '(deterministic L1 harness: no network beyond the local fixture file). '
      + 'Nothing is fabricated to fill this panel (§0.7).</div>';
    for (const id of ['view-hist', 'view-tpo', 'view-klinevp', 'view-farb', 'view-macro']) {
      $(id).innerHTML = NOTE;
    }
    $('hist-replay-controls').hidden = true;
    for (const id of ['set-hist-interval', 'set-hist-sma20', 'set-hist-sma50', 'set-hist-sma200', 'set-hist-ha', 'set-tpo-session']) {
      $(id).disabled = true;
    }
  } else {
    // ── Views (controls live in the panel chrome; views own their behavior —
    // the TapeView filter-input ownership split). ──
    histView = V.HistChartView();
    histView.mount($('view-hist'), {
      intervalSel: $('set-hist-interval'),
      smaInputs: { 20: $('set-hist-sma20'), 50: $('set-hist-sma50'), 200: $('set-hist-sma200') },
      haInput: $('set-hist-ha'),
      playBtn: $('hist-play'), stepBtn: $('hist-step'), speedSel: $('hist-speed'),
      scrub: $('hist-scrub'), liveBtn: $('hist-live'), flagEl: $('hist-replay-flag'),
      onInterval: (v) => {
        if (['5', '30', '60', '240', 'D'].indexOf(v) < 0) return;   // whitelist, like TICKS/BARS
        histInterval = v;
        refreshHist();   // refetch on interval change — the ONLY refresh (§0.7 no live merge)
      },
    });
    tpoView = V.TpoView();
    tpoView.mount($('view-tpo'), { sessionSel: $('set-tpo-session') });
    vpView = V.KlineVpView();
    vpView.mount($('view-klinevp'));
    farbView = V.FundingArbView();
    farbView.mount($('view-farb'));
    macroView = V.MacroView();
    macroView.mount($('view-macro'));

    // ── Historical chart + composite VP: one fetch feeds both (§4c — the VP
    // is built over the chart's CURRENT lookback+interval by construction). ──
    function refreshHist() {
      HIST.fetchBybitKlines(SYM, histInterval, 1000).then((bars) => {
        histBars = bars;   // null on failure → the view says so, no retry storm
        if (bars && bars.length) {
          let lo = Infinity, hi = -Infinity;
          for (const b of bars) { if (b.l < lo) lo = b.l; if (b.h > hi) hi = b.h; }
          vpTick = niceRound((hi - lo) / 120);   // ~120 VP levels over the lookback
          vpData = S.buildKlineVp(bars, { tickSize: vpTick });
        } else {
          vpData = null;
        }
        dirty.hist = true; dirty.vp = true;
      });
    }
    refreshHist();

    // ── TPO: 30m bars, last 5 UTC days (1000×30m ≈ 20.8 d covers it, §4c). ──
    function refreshTpo() {
      HIST.fetchBybitKlines(SYM, '30', 1000).then((bars) => {
        if (!bars || !bars.length) { tpoSessions = null; dirty.tpo = true; return; }
        const DAY = 86400000;
        const lastDay = Math.floor(bars[bars.length - 1].ts / DAY);
        const kept = bars.filter((b) => Math.floor(b.ts / DAY) > lastDay - 5);
        // Adaptive tick = niceRound(sessionRange/60) (§4c) on the WIDEST of
        // the 5 sessions: one shared grid keeps day-to-day POC/VA rows
        // comparable, and sizing to the max range caps every session at
        // ~60 letter rows (a median-sized tick would smear the widest day
        // into sub-pixel rows).
        let maxRange = 0;
        const dayRange = new Map();
        for (const b of kept) {
          const d = Math.floor(b.ts / DAY);
          const r = dayRange.get(d) || { lo: Infinity, hi: -Infinity };
          if (b.l < r.lo) r.lo = b.l;
          if (b.h > r.hi) r.hi = b.h;
          dayRange.set(d, r);
        }
        for (const r of dayRange.values()) if (r.hi - r.lo > maxRange) maxRange = r.hi - r.lo;
        tpoTick = niceRound(maxRange / 60);
        tpoSessions = S.buildTpo(kept, { tickSize: tpoTick });
        dirty.tpo = true;
      });
    }
    refreshTpo();

    // ── OKX funding/OI: NEW 60s REST poll (§4c FundingArbView leg). A null
    // result simply keeps the previous value's staleness visible via the
    // countdown / leaves '—' — silent-null tolerated by contract. ──
    function pollOkx() {
      HIST.fetchOkxFunding(OKX_INST).then((f) => { if (f) okxFund = f; dirty.farb = true; });
      HIST.fetchOkxOi(OKX_INST).then((o) => { if (o) okxOiEv = o; dirty.farb = true; });
    }
    pollOkx();
    setInterval(pollOkx, 60000);

    // ── Hyperliquid HIP-3 mids: 10s poll of both dexs (§4c MacroView strip).
    // normalizeHlMids filters to dex-prefixed names — the main-universe
    // SPX6900 memecoin can never leak in. Samples also feed the
    // SessionSeriesStore (its 60s gate downsamples the 10s poll) — the ONLY
    // honest correlation input for history-less HIP-3 legs. ──
    const HIP3_KEYS = ['km:US500', 'km:USTECH', 'km:GOLD', 'km:USOIL', 'xyz:XYZ100'];
    function pollMids() {
      Promise.all([HIST.fetchHlMids('km'), HIST.fetchHlMids('xyz')]).then(([km, xyz]) => {
        // Date.now() here is the POLL time — the caller-supplied clock the
        // store contract asks for (the store itself stays wall-clock-free).
        const now = Date.now();
        const merged = Object.assign({}, km || {}, xyz || {});
        for (const k of HIP3_KEYS) {
          if (Number.isFinite(merged[k])) {
            hlMids[k] = merged[k];
            sessStore.onSample(now, k, merged[k]);
          }
        }
        // BTC leg sampled on the SAME cadence from the live bybit mark, so
        // session-corr pairs align by sample index (§4c corr contract).
        if (marks.bybit && Number.isFinite(marks.bybit.mark)) sessStore.onSample(now, 'BTC', marks.bybit.mark);
        dirty.macro = true;
      });
    }
    pollMids();
    setInterval(pollMids, 10000);

    // ── Macro history legs: hourly 1h-kline fetch for BTC/ETH/PAXG → last
    // closes + the 7d rolling correlation (168 × 1h bars, computed once per
    // hour — klines only grow hourly, re-fetching faster buys nothing). ──
    function lastCorr7d(a, b) {
      if (!a || !b || a.length < 2 || b.length < 2) return NaN;
      // Align by bar timestamp FIRST (venues can differ in leading coverage /
      // a missing bar), then log-returns over the common bars — index-aligned
      // returns of misaligned bars would correlate different hours.
      const mb = new Map();
      for (const x of b) mb.set(x.ts, x.c);
      const ca = [], cb = [];
      for (const x of a) { const y = mb.get(x.ts); if (y != null) { ca.push(x.c); cb.push(y); } }
      const ra = [], rb = [];
      for (let i = 1; i < ca.length; i++) { ra.push(Math.log(ca[i] / ca[i - 1])); rb.push(Math.log(cb[i] / cb[i - 1])); }
      const series = S.rollingCorr(ra, rb, 168);   // 7 d of 1 h bars (§4c label '7d · 1h bars')
      return series.length ? series[series.length - 1].r : NaN;
    }
    function refreshMacroHistory() {
      Promise.all([
        HIST.fetchBybitKlines('BTCUSDT', '60', 400),   // 400 h ≈ 16.7 d ≥ the 168-bar window
        HIST.fetchBybitKlines('ETHUSDT', '60', 400),
        HIST.fetchBybitKlines('PAXGUSDT', '60', 400),  // PAXG = tokenized-gold proxy (§4c — no CME)
      ]).then(([btc, eth, paxg]) => {
        const now = Date.now();
        // ETH/PAXG strip prices come from their own kline closes and are
        // sampled into the session store only when genuinely refreshed —
        // re-sampling a stale hourly close every minute would fabricate a
        // flat series and drag any correlation toward 0 (§0.7).
        if (eth && eth.length) { macroLasts.ETH = eth[eth.length - 1].c; sessStore.onSample(now, 'ETH', macroLasts.ETH); }
        if (paxg && paxg.length) { macroLasts.PAXG = paxg[paxg.length - 1].c; sessStore.onSample(now, 'PAXG', macroLasts.PAXG); }
        corr7d = {
          btcEth: lastCorr7d(btc, eth),
          btcPaxg: lastCorr7d(btc, paxg),
          ethPaxg: lastCorr7d(eth, paxg),
        };
        dirty.macro = true;
      });
    }
    refreshMacroHistory();
    setInterval(refreshMacroHistory, 3600000);
  }

  // ── O-3 render-slice composers (read caches + stores; mutate nothing) ──

  /** FundingArbView slice: per-venue mark/funding/OI, per-source (§4c).
   *  intervalH: bybit + binancef BTC perps fund every 8 h (their
   *  nextFundingTs spacing — stated venue constant); OKX's comes from its
   *  funding response (normalizeOkxFunding derives it, fallback 8). */
  function farbSlice(now) {
    const by = marks.bybit, bn = marks.binancef;
    const oiBy = ois.bybit, oiBn = ois.binancef;
    return {
      nowMs: now,
      venues: {
        bybit: {
          mark: by ? by.mark : NaN, fundingRate: by ? by.fundingRate : NaN,
          nextFundingTs: by ? by.nextFundingTs : NaN, intervalH: 8,
          oi: oiBy ? oiBy.oi : NaN,
          oiUsd: (oiBy && by && Number.isFinite(by.mark)) ? oiBy.oi * by.mark : NaN,
        },
        binancef: {
          mark: bn ? bn.mark : NaN, fundingRate: bn ? bn.fundingRate : NaN,
          nextFundingTs: bn ? bn.nextFundingTs : NaN, intervalH: 8,
          oi: oiBn ? oiBn.oi : NaN,
          oiUsd: (oiBn && bn && Number.isFinite(bn.mark)) ? oiBn.oi * bn.mark : NaN,
        },
        okx: {
          mark: NaN,                          // no keyless OKX mark feed here —
          last: lastPriceByEx.okx,            // the view shows last trade, labeled '(last)'
          fundingRate: okxFund ? okxFund.fundingRate : NaN,
          nextFundingTs: okxFund ? okxFund.nextFundingTs : NaN,
          intervalH: okxFund ? okxFund.intervalH : 8,
          oi: okxOiEv ? okxOiEv.oi : NaN,     // COIN (normalizeOkxOi returns oiCcy, §4c unit rail)
          oiUsd: okxOiEv ? okxOiEv.oiUsd : NaN,
        },
      },
    };
  }

  /** MacroView slice: mids strip + correlation block (§4c). */
  function macroSlice() {
    const items = [
      { key: 'km:US500', label: 'US500', pctOnly: true, src: 'HL km' },   // scaled contract — % only
      { key: 'km:USTECH', label: 'USTECH', src: 'HL km' },
      { key: 'km:GOLD', label: 'GOLD', src: 'HL km' },
      { key: 'km:USOIL', label: 'USOIL', src: 'HL km' },
      { key: 'xyz:XYZ100', label: 'XYZ100', src: 'HL xyz' },
      { key: 'PAXG', label: 'PAXG', src: 'bybit 1h' },
      { key: 'ETH', label: 'ETH', src: 'bybit 1h' },
      { key: 'BTC', label: 'BTC', src: 'bybit mark' },
    ];
    const strip = [];
    for (const it of items) {
      let px = NaN;
      if (it.key.indexOf(':') >= 0) px = Number.isFinite(hlMids[it.key]) ? hlMids[it.key] : NaN;
      else if (it.key === 'PAXG') px = macroLasts.PAXG;
      else if (it.key === 'ETH') px = macroLasts.ETH;
      else px = marks.bybit ? marks.bybit.mark : NaN;
      const ser = sessStore.series(it.key);
      const sessPct = ser.length >= 2 ? (ser[ser.length - 1].px / ser[0].px - 1) * 100 : NaN;
      strip.push({ label: it.label, px, pctOnly: !!it.pctOnly, sessPct, src: it.src });
    }
    const sessCorr = [];
    for (const it of items) {
      if (it.key.indexOf(':') < 0) continue;   // session-corr cells are for the history-less HIP-3 legs
      const c = sessStore.corr(it.key, 'BTC');
      sessCorr.push({ label: it.label + ' × BTC', r: c.r, n: c.n });
    }
    // km:GOLD vs PAXG divergence — its own cell (§4c): the ~4% premium IS the
    // tracking-error story; showing it beats hiding it inside either price.
    const goldPrem = (Number.isFinite(hlMids['km:GOLD']) && Number.isFinite(macroLasts.PAXG) && macroLasts.PAXG > 0)
      ? (hlMids['km:GOLD'] / macroLasts.PAXG - 1) * 100 : NaN;
    return { strip, corr7d, sessCorr, goldPrem };
  }

  // ─── Read-only debug hook FOR THE BROWSER HARNESS ────────────────────────
  //
  // scripts/verify_terminal_browser.py polls this to decide "the page is
  // genuinely rendering data" (chips + store counts) without scraping pixels
  // for state. READ-ONLY by construction: every method derives from existing
  // stores/state and mutates nothing. Not a public API — the harness is the
  // only intended consumer.
  window.__BTCQ_TERMINAL_DEBUG = {
    chips() {
      const out = {};
      for (const ex in statuses) out[ex] = statuses[ex].kind;
      return out;
    },
    counts() {
      const lad = bybitBook.grouped(settings.tick, 12);
      const agg = aggBook.grouped(settings.tick, 14);
      return {
        tapeRows: tape.length,
        ladderRows: lad.bids.length + lad.asks.length,
        cvdPoints: cvds.bybit.series().t.length,
        heatSamples: depthHist.bybit.samples().length,   // reads the CURRENT (rebuildable) store
        aggLevels: agg.bids.length + agg.asks.length,
        footprintBars: footprint.bars().length,
      };
    },
  };

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
    // O-2: the heatmap history / detector / liq model are grid-bound too
    // (§4b — the detector MUST share the grouped() tick) and restart with it.
    rebuildFootprint();
    rebuildProfile();
    rebuildHeatmapStores();
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
  // heat/liqmap update at ~1s/5s data cadence anyway — budgets just cap bursts.
  // O-3 budgets: hist/tpo/vp repaint only on (re)fetch or control changes;
  // farb ticks with its 1s countdown; macro moves at poll cadence (≥10s).
  const MIN_MS = {
    fp: 250, dom: 120, tape: 180, agg: 220, header: 400, liq: 300, heat: 500, liqmap: 600, det: 250,
    hist: 500, tpo: 800, vp: 800, farb: 500, macro: 800,
  };
  const lastAt = {
    fp: 0, dom: 0, tape: 0, agg: 0, header: 0, liq: 0, heat: 0, liqmap: 0, det: 0,
    hist: 0, tpo: 0, vp: 0, farb: 0, macro: 0,
  };

  function due(key, now) {
    if (!dirty[key] || now - lastAt[key] < MIN_MS[key]) return false;
    dirty[key] = false;
    lastAt[key] = now;
    return true;
  }

  const priceEl = $('last-price');
  let detLastEvt = null;   // newest detector event (identity) — repaint signal

  // ── O-2 depth sampler (§4b) — runs every frame, BEFORE the render gates ──
  //
  // Samples each leg's BookStore into its DepthHistoryStore when that leg's
  // newest depth EVENT ts advanced ≥ 1000 ms since the last sample. The gate
  // compares event-ts to event-ts — never Date.now() — so the stores stay
  // wall-clock-free (replay rail): a stalled feed simply stops producing
  // samples (the heatmap freezes at its last real column) instead of
  // recording a fake flat book. Runs even while paused: pause freezes PAINT,
  // never ingestion (the pause note in terminal.html promises exactly that).
  function sampleDepth() {
    for (const ex of HIST_EXS) {
      const ts = lastDepthTs[ex];
      if (!Number.isFinite(ts) || ts - lastSampleTs[ex] < 1000) continue;
      lastSampleTs[ex] = ts;
      const book = aggBook.books.get(ex);
      depthHist[ex].sample(ts, book);
      // Price trail point per sample, from the venue's OWN trades (§0.7).
      const px = lastPriceByEx[ex];
      if (Number.isFinite(px)) {
        const trail = priceTrail[ex];
        trail.push({ ts, price: px });
        if (trail.length > 3600) trail.shift();   // same horizon as the sample ring
      }
      if (ex === 'bybit') {
        // Detector rides the SAME 1/s bybit cadence and the SAME tick grid
        // as the history store (§4b: primary venue — deepest verified book,
        // and the only leg whose trades we correlate against its depth).
        detector.onDepthSample(ts, book.grouped(settings.tick, 40));
      }
      if (ex === heatVenue) dirty.heat = true;
    }
    // Detector repaint signal: identity of the newest event (the ring caps at
    // 100, so length alone would go blind once full).
    const evs = detector.events();
    const newest = evs.length ? evs[evs.length - 1] : null;
    if (newest !== detLastEvt) {
      detLastEvt = newest;
      dirty.det = true;
      dirty.heat = true;   // heatmap overlays ▽/◈ markers at event coords
    }
  }

  // ── O-2 liq-model gate (§4b): re-estimate every ≥5s of EVENT time ──
  //
  // Gate on the bybit mark event's ts (the model's own liveness input): no
  // fresh mark → no re-estimate, and the panel honestly keeps its last
  // labeled estimate rather than restyling stale inputs as new output.
  function maybeEstimateLiq() {
    const m = marks.bybit;
    if (!m || !Number.isFinite(m.ts) || m.ts - lastEstTs < 5000) return;
    lastEstTs = m.ts;
    liqEst = liqModel.estimate(profile.profile().levels, m.mark, liq.recent(100));
    dirty.liqmap = true;
  }

  function frame() {
    const now = Date.now();
    sampleDepth();
    maybeEstimateLiq();

    // The header (with its conn chips) renders even while paused: pause
    // freezes the MARKET panels, never connection health — a paused page that
    // also froze its chips could hide a dead feed behind the pause button.
    if (due('header', now)) {
      // §0 honesty rail note: no chip relabeling happens here anymore —
      // HeaderStatsView now renders the status MESSAGE the transport itself
      // supplied ('replay' from terminal-replay.js, 'live feed connected'
      // from livewire.js), so replay is labeled at the source instead of
      // being patched over after the fact. verify_terminal_browser.py still
      // asserts no chip ever says 'live' in replay.
      headerView.render({ marks, ois, statuses, sessionHigh, sessionLow, nowMs: now });
      priceEl.textContent = Number.isFinite(lastPrice)
        ? '$' + lastPrice.toLocaleString('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 1 })
        : '—';
    }

    if (!paused) {
      if (due('fp', now)) {
        // cvd: per-exchange series map (§4b) — the view draws one labeled
        // line per venue, the exact Σ, and the bybit by-size bucket lines.
        const cvdExs = {};
        for (const ex of CVD_EXS) cvdExs[ex] = cvds[ex].series();
        fpView.render({
          bars: footprint.bars(),
          profile: profile.profile(),
          cvd: { exs: cvdExs },
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
      if (due('heat', now)) {
        const dh = depthHist[heatVenue];
        bookHeatView.render({
          samples: dh.samples(),
          range: dh.priceRange(),
          tickSize: settings.tick,
          trail: priceTrail[heatVenue],   // empty for binancef (no trades leg, §0.2) — honestly absent
          // Detector markers belong to the venue they were computed on: shown
          // on the BYBIT heatmap only (drawing bybit flags over another
          // venue's book would misattribute them, §0.7 per-source rail).
          events: heatVenue === 'bybit' ? detector.events() : [],
          ex: heatVenue,
        });
      }
      if (due('liqmap', now)) {
        liqHeatView.render({
          est: liqEst,
          mark: marks.bybit ? marks.bybit.mark : NaN,
          tickSize: settings.tick,
        });
      }
      if (due('det', now)) {
        detView.render({ events: detector.events() });
      }
      // O-3 STRUCTURE panels (§4c) — null views in replay (honest notes were
      // rendered instead; the dirty flags simply expire unread).
      if (histView && due('hist', now)) {
        histView.render({ bars: histBars });
      }
      if (tpoView && due('tpo', now)) {
        tpoView.render({ sessions: tpoSessions, tickSize: tpoTick });
      }
      if (vpView && due('vp', now)) {
        vpView.render({ vp: vpData, lastPrice, interval: histInterval });
      }
      if (farbView && due('farb', now)) {
        farbView.render(farbSlice(now));
      }
      if (macroView && due('macro', now)) {
        macroView.render(macroSlice());
      }
    }

    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);

  // Time itself moves the funding countdowns and rolls liqs out of the 1m/5m
  // windows even when no event arrives — tick those panels once a second
  // (farb joins in O-3: its 'next in' column is a countdown too, §4c).
  setInterval(() => { dirty.header = true; dirty.liq = true; dirty.farb = true; }, 1000);

  // Canvas panels re-measure on their next draw; a resize makes them dirty.
  // (O-3: tpo/vp are canvases too; the hist chart resizes itself in-view.)
  window.addEventListener('resize', () => {
    dirty.fp = true; dirty.agg = true; dirty.heat = true; dirty.liqmap = true;
    dirty.tpo = true; dirty.vp = true;
  });
})();
