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
  const SCR_TOPS = ['40', 'all'];          // screener slice: top-40 by turnover (default) | whole universe

  // O-4 (§4d) alert-rule defaults — the SEED the AlertsView inputs edit and
  // the ONLY place thresholds are defaulted (§4d: no defaults hidden in
  // engine logic — everything below is visible in the rules table). One rule
  // per kind, id = kind. price-cross ships disabled with no level: a price
  // level is the user's opinion, not ours to invent.
  const ALERT_KINDS = ['price-cross', 'whale-print', 'liq-1m', 'funding-flip',
    'cvd-divergence', 'book-imbalance', 'detector-pass', 'oi-jump', 'basis-bp'];
  const DEFAULT_RULES = [
    { kind: 'price-cross', enabled: false, threshold: null },
    { kind: 'whale-print', enabled: true, threshold: 250000 },   // mirrors the tape's ◆ whale bar
    { kind: 'liq-1m', enabled: true, threshold: 1000000 },
    { kind: 'funding-flip', enabled: true, threshold: null },
    { kind: 'cvd-divergence', enabled: true, threshold: null },
    { kind: 'book-imbalance', enabled: true, threshold: 0.6 },   // top-10 depth ≥ 80/20 one-sided
    { kind: 'detector-pass', enabled: true, threshold: null },
    { kind: 'oi-jump', enabled: true, threshold: 2 },            // |%/h| — deliberate repositioning, not drift
    { kind: 'basis-bp', enabled: true, threshold: 25 },
  ];

  // O-5 (§4e.1): the four collapsible page sections — the section mini-nav's
  // toggles persist through the settings object like every other control.
  const SECTIONS = ['orderflow', 'structure', 'intelligence', 'portfolio'];

  const DEFAULTS = {
    tick: 10, barMs: 60000, tapeMin: 0, liqRange: 'pct6',
    // O-4 (§4d): screener slice, whale watchlist (+BTC filter), alert rules.
    screenerTop: '40', whaleBtcOnly: true, whaleAddrs: [], alertRules: DEFAULT_RULES,
    // O-5 (§4e.1): per-section collapse state (all expanded by default).
    collapsed: { orderflow: false, structure: false, intelligence: false, portfolio: false },
  };

  function loadSettings() {
    const s = Object.assign({}, DEFAULTS);
    // Deep-copy the array/object defaults — settings.whaleAddrs.push() must
    // never mutate DEFAULTS (a shared reference would corrupt the fallback).
    s.whaleAddrs = [];
    s.alertRules = DEFAULT_RULES.map((r) => Object.assign({ id: r.kind }, r));
    s.collapsed = { orderflow: false, structure: false, intelligence: false, portfolio: false };
    try {
      const j = JSON.parse(localStorage.getItem(LS_KEY) || '{}');
      if (TICKS.indexOf(j.tick) >= 0) s.tick = j.tick;
      if (BARS.indexOf(j.barMs) >= 0) s.barMs = j.barMs;
      if (Number.isFinite(j.tapeMin) && j.tapeMin >= 0) s.tapeMin = j.tapeMin;
      if (LIQ_RANGES.indexOf(j.liqRange) >= 0) s.liqRange = j.liqRange;
      // O-4 whitelists (same rule as above: only recognized values return
      // from storage — a hand-edited blob can't smuggle an unsupported state):
      if (SCR_TOPS.indexOf(j.screenerTop) >= 0) s.screenerTop = j.screenerTop;
      if (typeof j.whaleBtcOnly === 'boolean') s.whaleBtcOnly = j.whaleBtcOnly;
      if (Array.isArray(j.whaleAddrs)) {
        // Addresses must LOOK like EVM addresses (they go straight into POST
        // bodies + innerHTML data-attrs) and honor the §4d cap of 25.
        s.whaleAddrs = j.whaleAddrs
          .filter((a) => typeof a === 'string' && /^0x[0-9a-fA-F]{40}$/.test(a))
          .map((a) => a.toLowerCase())
          .filter((a, i, arr) => arr.indexOf(a) === i)
          .slice(0, 25);
      }
      if (Array.isArray(j.alertRules)) {
        // Stored rules OVERRIDE matching defaults by kind — unknown kinds are
        // dropped (stale storage from a future/older build must not feed the
        // engine rules it can't evaluate), missing kinds keep their default.
        for (const r of j.alertRules) {
          if (!r || ALERT_KINDS.indexOf(r.kind) < 0) continue;
          const dst = s.alertRules.find((d) => d.kind === r.kind);
          if (!dst) continue;
          dst.enabled = r.enabled === true;
          dst.threshold = Number.isFinite(r.threshold) ? r.threshold : null;
        }
      }
      // O-5 (§4e.1): only the four KNOWN section names, strict booleans —
      // same validated-on-load rule as every other stored value above.
      if (j.collapsed && typeof j.collapsed === 'object') {
        for (const sec of SECTIONS) {
          if (typeof j.collapsed[sec] === 'boolean') s.collapsed[sec] = j.collapsed[sec];
        }
      }
    } catch (_) { /* corrupt storage → defaults */ }
    return s;
  }
  const settings = loadSettings();
  function saveSettings() {
    try {
      localStorage.setItem(LS_KEY, JSON.stringify({
        tick: settings.tick, barMs: settings.barMs, tapeMin: settings.tapeMin,
        liqRange: settings.liqRange,
        screenerTop: settings.screenerTop, whaleBtcOnly: settings.whaleBtcOnly,
        whaleAddrs: settings.whaleAddrs,
        alertRules: settings.alertRules.map((r) => ({ kind: r.kind, enabled: r.enabled, threshold: r.threshold })),
        collapsed: settings.collapsed,
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

  // ─── O-4 intelligence feed state (§4d) — consumed by the 5s intel gate ──
  let lastBybitTs = NaN;      // newest bybit trade/mark EVENT ts — the intel gate's clock (no Date.now() in the gate: replay rail)
  const pendingTrades = [];   // trades since the last AlertEngine evaluate (whale-print input), bounded below
  const oiHistBybit = [];     // [{ts, oi}] ring (~2h) — the 'OI 1h change' read needs history the WS event alone doesn't carry

  // ─── Dirty flags — the ONLY signal that a view needs repainting ─────────
  const dirty = {
    fp: true, dom: true, tape: true, agg: true, header: true, liq: true,
    heat: true, liqmap: true, det: true,   // O-2 panels (§4b)
    hist: true, tpo: true, vp: true, farb: true, macro: true,   // O-3 STRUCTURE panels (§4c)
    scr: true, rsi: true, opts: true, whale: true, alerts: true, conf: true,   // O-4 INTELLIGENCE panels (§4d)
    jour: true, cal: true, poly: true, news: true, econ: true,   // O-5 PORTFOLIO panels (§4e)
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
        // O-4 (§4d): whale-print alert input — EVERY venue's prints qualify
        // (the tape mixes venues deliberately and a $2M Coinbase sweep is as
        // notable as a Bybit one; each event names its rule, not its venue).
        // Bounded: between 5s evaluates even a 100-trade/s burst stays ≤500;
        // the cap only bites if the intel gate stalls, and then dropping the
        // OLDEST keeps the newest (largest-recent) prints evaluable.
        pendingTrades.push(ev);
        if (pendingTrades.length > 4000) pendingTrades.splice(0, pendingTrades.length - 4000);
        if (ev.ex === 'bybit' && Number.isFinite(ev.ts) && !(lastBybitTs >= ev.ts)) lastBybitTs = ev.ts;
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
        if (ev.ex === 'bybit' && Number.isFinite(ev.ts) && !(lastBybitTs >= ev.ts)) lastBybitTs = ev.ts;
        break;
      case 'oi':
        ois[ev.ex] = ev;
        dirty.header = true;
        // O-4 (§4d): keep ~2h of bybit OI samples so 'OI 1h change' compares
        // real history instead of extrapolating one print (event-ts pruned).
        if (ev.ex === 'bybit' && Number.isFinite(ev.ts) && Number.isFinite(ev.oi)) {
          oiHistBybit.push({ ts: ev.ts, oi: ev.oi });
          while (oiHistBybit.length && oiHistBybit[0].ts < ev.ts - 7200000) oiHistBybit.shift();
        }
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

  // ─── O-4 INTELLIGENCE section (§4d): screener / RSI / options / whales /
  // alerts / confluence ──────────────────────────────────────────────────
  //
  // Two transport classes, two replay rules (§4d wiring contract):
  //   - REST-fed panels (screener tickers 30s, RSI kline batch, Deribit chain
  //     + DVOL 60s, whale clearinghouse 60s/address) are DISABLED in replay
  //     modes — real network breaks the deterministic L1 harness — and each
  //     panel renders the honest 'disabled in replay' note (same rail as O-3).
  //   - Confluence + alerts read the EXISTING live stores (footprint, CVD,
  //     profile, book, liqs, detector), which replay drives deterministically
  //     — so those two panels run in BOTH modes, gated on EVENT time.
  //
  // Rail restated (§4d): everything in this section is a DESCRIPTIVE read or
  // attention trigger — the IC run-log measured ≈0 forward IC for board
  // signals, and the confluence label / alerts banner say so on the page.
  let screenerView = null, rsiView = null, optionsView = null, whaleView = null;
  // O-4 REST caches (the frame loop only reads them).
  let tickerRows = null;           // normalizeBybitTickers() rows (30s poll)
  let btcTicker = null;            // the BTCUSDT row — response-provided fundingIntervalHour for the confluence read
  let rsiState = { items: [], loaded: 0, total: 0 };   // partial-by-design RSI batch state
  let rsiGen = 0;                  // batch generation — a new refresh abandons stale in-flight completions
  let rsiStarted = false;
  let chainData = null, dvolVal = null;   // Deribit 60s poll results
  const whaleState = new Map();    // addr → {positions, ts, polledAt, pending}
  let whaleDiscovering = false, whaleNote = '';
  let whaleRR = 0;                 // round-robin cursor for the staggered polls

  // Confluence + alert state (both modes — store-fed, event-ts gated).
  let confData = null;             // last confluenceReads() output
  let lastIntelTs = -Infinity;     // event-ts of the last intel evaluation (5s gate)
  const alertFresh = [];           // events fired since the last AlertsView render (Notification candidates)
  const intelWin = { price: [], cvd: [] };   // per-gate samples for cvd-divergence (ring 24 ≈ 2min)
  let detSeenIntel = null;         // newest detector event already forwarded to the engine
  // Engine rules come from the persisted settings; threshold null → undefined
  // so the engine's Number.isFinite() gate reads "no threshold, cannot fire".
  function engineRules() {
    return settings.alertRules.map((r) => ({
      id: r.kind, kind: r.kind, enabled: !!r.enabled,
      threshold: Number.isFinite(r.threshold) ? r.threshold : undefined,
    }));
  }
  const alertEngine = S.AlertEngine({ rules: engineRules(), cooldownMs: 60000 });

  // Confluence + alerts views mount in BOTH modes (store-fed — see section
  // header); their inputs simply carry more 'n/a' in replay because the REST
  // legs (TPO, SMA50, funding interval) are honestly absent.
  const confView = V.ConfluenceView();
  confView.mount($('view-conf'));
  const alertsView = V.AlertsView();
  alertsView.mount($('view-alerts'), {
    rules: settings.alertRules,
    onRules: (rules) => {
      settings.alertRules = rules;
      saveSettings();
      alertEngine.setRules(engineRules());   // surviving ids keep their cooldown/tracker state (engine contract)
      dirty.alerts = true;
    },
  });

  if (REPLAY) {
    // Honest replay note for the REST-fed O-4 panels (§4d wiring rule — same
    // text discipline as the O-3 STRUCTURE notes above).
    const NOTE4 = '<div class="chart-na">awaiting REST data — REST polls are disabled in replay '
      + '(deterministic L1 harness: no network beyond the local fixture file). '
      + 'Nothing is fabricated to fill this panel (§0.7).</div>';
    for (const id of ['view-screener', 'view-rsi', 'view-options', 'view-whale']) {
      $(id).innerHTML = NOTE4;
    }
    for (const id of ['set-scr-top', 'rsi-refresh', 'set-opt-expiry']) {
      $(id).disabled = true;
    }
  } else {
    // ── Views (controls in the panel chrome; views own their behavior). ──
    screenerView = V.ScreenerView();
    const scrTopSel = $('set-scr-top');
    scrTopSel.value = settings.screenerTop;
    screenerView.mount($('view-screener'), {
      topInput: scrTopSel,
      onTop: (v) => {
        if (SCR_TOPS.indexOf(v) < 0) return;   // whitelist, like TICKS/BARS
        settings.screenerTop = v;
        saveSettings();
        dirty.scr = true;
      },
    });
    rsiView = V.RsiHeatmapView();
    rsiView.mount($('view-rsi'), { progressEl: $('rsi-progress') });
    optionsView = V.OptionsView();
    optionsView.mount($('view-options'), { expirySel: $('set-opt-expiry') });
    whaleView = V.WhaleView();
    whaleView.mount($('view-whale'), {
      btcOnly: settings.whaleBtcOnly,
      onBtcOnly: (v) => { settings.whaleBtcOnly = !!v; saveSettings(); },
      onAdd: (addr) => {
        if (settings.whaleAddrs.indexOf(addr) >= 0) { whaleNote = 'already watching ' + addr.slice(0, 6) + '…'; dirty.whale = true; return; }
        if (settings.whaleAddrs.length >= 25) { whaleNote = 'watchlist cap (25) reached — remove one first'; dirty.whale = true; return; }
        settings.whaleAddrs.push(addr);
        saveSettings();
        whaleNote = '';
        dirty.whale = true;
      },
      onRemove: (addr) => {
        const i = settings.whaleAddrs.indexOf(addr);
        if (i < 0) return;
        settings.whaleAddrs.splice(i, 1);
        whaleState.delete(addr);
        saveSettings();
        dirty.whale = true;
      },
      onDiscover: () => {
        // The 33 MB leaderboard is ONE-SHOT and user-consented (the view's
        // confirm() dialog stated the size before this callback ran, §4d).
        if (whaleDiscovering) return;
        whaleDiscovering = true;
        whaleNote = '';
        dirty.whale = true;
        HIST.fetchHlLeaderboard(10).then((lb) => {
          whaleDiscovering = false;
          if (!lb) {
            whaleNote = 'leaderboard fetch failed — nothing seeded (retry the button)';
            dirty.whale = true;
            return;
          }
          // Seed top-10 by account value + top-10 by 30d ROI, deduped, cap 25.
          let added = 0;
          for (const r of lb.topByValue.concat(lb.topByRoi30d)) {
            const a = String(r.addr || '').toLowerCase();
            if (!/^0x[0-9a-f]{40}$/.test(a)) continue;
            if (settings.whaleAddrs.indexOf(a) >= 0) continue;
            if (settings.whaleAddrs.length >= 25) break;
            settings.whaleAddrs.push(a);
            added++;
          }
          saveSettings();
          whaleNote = 'seeded ' + added + ' address' + (added === 1 ? '' : 'es')
            + ' (top-10 by value + top-10 by 30d ROI, deduped)';
          dirty.whale = true;
        });
      },
    });

    // ── Screener universe: ONE tickers call carries all ~720 linear symbols
    // (§4d empirical map) — a single 30s poll, never per-symbol fan-out. ──
    function pollTickers() {
      HIST.fetchBybitAllTickers().then((rows) => {
        if (rows && rows.length) {
          tickerRows = rows;
          btcTicker = null;
          for (const r of rows) if (r.sym === SYM) { btcTicker = r; break; }
          if (!rsiStarted) { rsiStarted = true; refreshRsi(); }   // RSI batch needs the universe first
        }
        dirty.scr = true;   // null result → the view keeps saying 'awaiting tickers'
      });
    }

    // ── RSI batch: 1h klines for the screener's top-40 by turnover, through
    // quant.js rsi(closes, 14) — 40 fetches behind a 4-way concurrency pool
    // (politeness cap: Bybit tolerates bursts, but 40 simultaneous kline hits
    // from one browser is rude and rate-limit bait). Progress is HONEST
    // partial state: the strip renders each symbol as it lands, and the
    // header counts 'n/40 loaded' (§4d). ──
    function refreshRsi() {
      const Q = window.Quant;
      if (!tickerRows || !Q || !Q.rsi) { dirty.rsi = true; return; }
      const top = S.buildScreener(tickerRows, { topN: 40 }).rows;
      if (!top.length) { dirty.rsi = true; return; }
      const gen = ++rsiGen;   // a newer refresh abandons this batch's stragglers
      rsiState = { items: [], loaded: 0, total: top.length };
      dirty.rsi = true;
      let idx = 0;
      const next = () => {
        if (gen !== rsiGen || idx >= top.length) return;
        const row = top[idx++];
        // 50×1h bars: RSI-14 needs 15; the extra history settles Wilder's
        // recursive smoothing instead of reading a cold-start artifact.
        HIST.fetchBybitKlines(row.sym, '60', 50).then((bars) => {
          if (gen !== rsiGen) return;
          rsiState.loaded++;
          if (bars && bars.length >= 15) {
            const series = Q.rsi(bars.map((b) => b.c), 14);
            const last = series[series.length - 1];
            if (Number.isFinite(last)) {
              rsiState.items.push({ sym: row.sym, rsi: last, turnover24h: row.turnover24h });
            }
          }
          // Failed fetches still count as 'loaded' — the progress denominator
          // is attempts, and a missing bubble IS the honest render of a miss.
          dirty.rsi = true;
          next();
        });
      };
      for (let k = 0; k < 4; k++) next();   // the politeness cap: 4 in flight
    }
    $('rsi-refresh').addEventListener('click', refreshRsi);

    // ── Deribit chain + DVOL: 60s poll (CORS-open, fetched straight from the
    // page — §4d empirical). null keeps the last good chain on display; the
    // view's stats say when the chain is absent entirely. ──
    function pollOptions() {
      HIST.fetchDeribitChain('BTC').then((c) => { if (c) chainData = c; dirty.opts = true; });
      HIST.fetchDeribitDvol().then((d) => { if (d !== null) dvolVal = d; dirty.opts = true; });
    }

    // ── Whale polls: one clearinghouseState POST per 2.5s tick, round-robin,
    // each address gated to ≥60s — STAGGERED so 25 watched addresses (the
    // cap) spread over a ~62.5s sweep instead of firing a 25-request burst
    // every minute at the same API. ──
    function whaleTick() {
      const n = settings.whaleAddrs.length;
      if (!n) return;
      const now = Date.now();
      for (let k = 0; k < n; k++) {
        const addr = settings.whaleAddrs[whaleRR % n];
        whaleRR++;
        const st = whaleState.get(addr);
        if (st && (st.pending || (Number.isFinite(st.polledAt) && now - st.polledAt < 60000))) continue;
        whaleState.set(addr, Object.assign({}, st, { pending: true, polledAt: now }));
        HIST.fetchHlClearinghouse(addr).then((pos) => {
          const cur = whaleState.get(addr);
          if (!cur) return;   // removed from the watchlist mid-flight
          whaleState.set(addr, {
            // null (failed) keeps the last real positions; ts marks the last
            // SUCCESS so a stale row is at least honestly datable.
            positions: pos !== null ? pos : cur.positions,
            ts: pos !== null ? Date.now() : cur.ts,
            polledAt: cur.polledAt, pending: false,
          });
          dirty.whale = true;
        });
        break;   // one address per tick — the stagger
      }
    }

    pollTickers();
    setInterval(pollTickers, 30000);
    setInterval(refreshRsi, 300000);   // §4d: 5min auto-refresh on top of the button
    pollOptions();
    setInterval(pollOptions, 60000);
    whaleTick();
    setInterval(whaleTick, 2500);
  }

  // ─── O-5 PORTFOLIO section (§4e): journal / calendar / polymarket / news /
  // econ ────────────────────────────────────────────────────────────────────
  //
  // Two transport classes, same replay rules as O-3/O-4:
  //   - Journal + calendar are localStorage-fed (the user's OWN trades — §4e
  //     rail: a manual descriptive record, NOT a backtest) and run in BOTH
  //     modes.
  //   - Polymarket (60s) / ToA news (30s) / econ local-file read are DISABLED
  //     in replay modes with the honest note — polymarket/news are real
  //     network, and the econ file is machine-dependent local state (its
  //     presence/absence would leak nondeterminism + 404 console noise into
  //     the deterministic L1 harness).

  // ── Journal storage: ONE localStorage key holding the SAME CSV the export
  // button downloads — one format everywhere, and every load runs through
  // validateJournalCsv (terminal-state.js), so a hand-edited or corrupt blob
  // is rejected PER ROW instead of poisoning the stats (§4e: import — and by
  // extension load — never silently coerces). ──
  const LS_JOURNAL = 'btcq-terminal-journal';
  let journalTrades = [];
  let journalNote = '';        // one-line status under the form (load/import outcomes)
  let importErrors = [];       // last import's per-row rejections (rendered honestly)
  try {
    const stored = localStorage.getItem(LS_JOURNAL);
    if (stored) {
      const res = S.validateJournalCsv(stored);   // validated on load — same path as import
      journalTrades = res.trades;
      if (res.errors.length) {
        journalNote = res.errors.length + ' stored row(s) failed validation and were not loaded (storage is re-written clean on the next change)';
      }
    }
  } catch (_) { /* private mode / quota — journal starts empty, nothing guessed */ }
  function saveJournal() {
    try { localStorage.setItem(LS_JOURNAL, S.journalToCsv(journalTrades)); }
    catch (_) { journalNote = 'localStorage unavailable — journal will not survive a reload'; }
  }

  /** Descriptive context snapshot at log time (§4e trade shape `ctx`): plain
   *  facts from the live stores — a record of CONDITIONS, never a judgment.
   *  Missing feeds store null (JSON has no NaN; null renders '—'). */
  function ctxSnapshot() {
    const m = marks.bybit;
    const slope = cvdSlope60s();
    return {
      mark: m && Number.isFinite(m.mark) ? m.mark : null,
      fundingRate: m && Number.isFinite(m.fundingRate) ? m.fundingRate : null,
      oi: ois.bybit && Number.isFinite(ois.bybit.oi) ? ois.bybit.oi : null,
      cvdSlope: Number.isFinite(slope) ? slope : null,
      confluenceTally: confData ? confData.tally : null,
    };
  }

  const journalView = V.JournalView();
  journalView.mount($('view-journal'), {
    onAdd: (fields) => {
      const now = Date.now();   // bootstrap layer — the user logged NOW (pure code stays wall-clock-free)
      journalTrades.push({
        id: 'j' + now.toString(36) + Math.random().toString(36).slice(2, 7),
        tsOpen: now, tsClose: now,
        side: fields.side, entry: fields.entry, exit: fields.exit,
        size: fields.size, riskUsd: fields.riskUsd,
        tag: fields.tag, note: fields.note,
        ctx: ctxSnapshot(),
      });
      saveJournal();
      journalNote = '';
      dirty.jour = true; dirty.cal = true;
    },
    onRemove: (id) => {
      const i = journalTrades.findIndex((t) => t.id === id);
      if (i < 0) return;
      journalTrades.splice(i, 1);
      saveJournal();
      dirty.jour = true; dirty.cal = true;
    },
    onExport: () => {
      // Data portability (§4e): the download IS the storage format.
      const blob = new Blob([S.journalToCsv(journalTrades)], { type: 'text/csv' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = 'btcq-journal.csv';
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 5000);
    },
    onImport: (text) => {
      const res = S.validateJournalCsv(text);
      let added = 0, dupes = 0;
      for (const t of res.trades) {
        // ids are the identity — re-importing your own export is a no-op,
        // never a silent duplication of every trade.
        if (journalTrades.some((x) => x.id === t.id)) { dupes++; continue; }
        journalTrades.push(t);
        added++;
      }
      importErrors = res.errors;   // rendered per-row by the view (§4e: never silently coerced)
      journalNote = 'import: ' + added + ' added'
        + (dupes ? ', ' + dupes + ' duplicate id(s) skipped' : '')
        + (res.errors.length ? ', ' + res.errors.length + ' row(s) rejected below' : '');
      if (added) saveJournal();
      dirty.jour = true; dirty.cal = true;
    },
  });
  const calView = V.CalendarView();
  calView.mount($('view-calendar'));

  // ── Context feeds: REST/local polls (non-replay only — section header). ──
  let polyView = null, newsView = null, econView = null;
  let polyEvents = null;   // normalizePolymarketEvents() result (null → 'awaiting')
  let newsItems = null;    // normalizeToaNews() result
  let econData = null;     // normalizeEconLocal() result (null → the make-econ note)
  if (REPLAY) {
    // Honest replay note (same text discipline as the O-3/O-4 notes): these
    // panels stay empty in replay and SAY WHY (§0.7 — empty-but-honest).
    const NOTE5 = '<div class="chart-na">awaiting REST data — REST polls (and the local econ file read) are '
      + 'disabled in replay (deterministic L1 harness: no network / machine-local state beyond the fixture '
      + 'file). Nothing is fabricated to fill this panel (§0.7).</div>';
    for (const id of ['view-polymarket', 'view-news', 'view-econ']) {
      $(id).innerHTML = NOTE5;
    }
  } else {
    polyView = V.PolymarketView();
    polyView.mount($('view-polymarket'));
    newsView = V.NewsView();
    newsView.mount($('view-news'));
    econView = V.EconView();
    econView.mount($('view-econ'));

    // Polymarket 60s / ToA 30s (§4e cadences). null keeps the last good list
    // on display — a transient REST miss must not blank a rendered panel.
    function pollPolymarket() {
      HIST.fetchPolymarketBtc().then((evs) => { if (evs) polyEvents = evs; dirty.poly = true; });
    }
    function pollNews() {
      HIST.fetchToaNews(30).then((items) => { if (items) newsItems = items; dirty.news = true; });
    }
    // Econ local file: null is MEANINGFUL here (file absent → the view shows
    // the make-econ how-to), so it is assigned through — and re-checked every
    // 5min so running `make econ` in a shell shows up without a page reload.
    function pollEcon() {
      HIST.fetchEconLocal().then((d) => { econData = d; dirty.econ = true; });
    }
    pollPolymarket();
    setInterval(pollPolymarket, 60000);
    pollNews();
    setInterval(pollNews, 30000);
    pollEcon();
    setInterval(pollEcon, 300000);
  }

  // ── O-4 intel gate (§4d): confluence inputs + AlertEngine snapshot, both
  // assembled from EXISTING stores every ≥5s of EVENT time (lastBybitTs — the
  // same event-clock discipline as the liq-model gate: no fresh bybit events
  // → no re-read, and replay evaluates deterministically). ──

  /** CVD slope over the trailing ~60s of bybit samples, USD/s. NaN below 5s
   *  of span — a two-sample "slope" is noise wearing a number. */
  function cvdSlope60s() {
    const s = cvds.bybit.series();
    const n = s.t.length;
    if (n < 2) return NaN;
    const tL = s.t[n - 1];
    let i0 = n - 1;
    while (i0 > 0 && s.t[i0 - 1] >= tL - 60000) i0--;
    if (i0 === n - 1) return NaN;
    const dt = (tL - s.t[i0]) / 1000;
    if (!(dt >= 5)) return NaN;
    return (s.overall[n - 1] - s.overall[i0]) / dt;
  }

  /** Bybit OI %-change normalized to per-hour. NaN below 10min of history —
   *  extrapolating a 1-minute wiggle ×60 would fabricate a rate (§0.7). */
  function oiChangePct1h(nowTs) {
    if (oiHistBybit.length < 2) return NaN;
    const newest = oiHistBybit[oiHistBybit.length - 1];
    let oldest = null;
    for (const smp of oiHistBybit) { if (smp.ts >= nowTs - 3600000) { oldest = smp; break; } }
    if (!oldest || oldest === newest || !(oldest.oi > 0)) return NaN;
    const spanMs = newest.ts - oldest.ts;
    if (spanMs < 600000) return NaN;
    return (newest.oi / oldest.oi - 1) * 100 * (3600000 / spanMs);
  }

  /** Long-vs-short liquidation notional imbalance over the trailing 5m
   *  (event-ts window): +1 = all longs liquidated. NaN when nothing printed
   *  — no liqs is absence, not balance. */
  function liqImb5m(nowTs) {
    let longUsd = 0, shortUsd = 0;
    for (const l of liq.recent()) {
      if (!(l.ts > nowTs - 300000 && l.ts <= nowTs)) continue;
      if (l.side === 'long') longUsd += l.notionalUsd;
      else if (l.side === 'short') shortUsd += l.notionalUsd;
    }
    const tot = longUsd + shortUsd;
    return tot > 0 ? (longUsd - shortUsd) / tot : NaN;
  }

  /** Top-10 grouped bid-vs-ask depth imbalance on the bybit book: +1 = all
   *  bids. NaN on an empty book (pre-snapshot) — absence, not balance. */
  function bookImb10() {
    const g = bybitBook.grouped(settings.tick, 10);
    let b = 0, a = 0;
    for (const r of g.bids) b += r.qty;
    for (const r of g.asks) a += r.qty;
    return b + a > 0 ? (b - a) / (b + a) : NaN;
  }

  /** Last close + SMA50 from the hist chart's CURRENT bars (quant.js sma —
   *  house rule) — NaNs while the REST history is absent (e.g. replay). */
  function sma50FromHist() {
    const Q = window.Quant;
    if (!histBars || histBars.length < 51 || !Q || !Q.sma) return { sma: NaN, close: NaN };
    const closes = histBars.map((b) => b.c);
    const arr = Q.sma(closes, 50);
    return { sma: arr[arr.length - 1], close: closes[closes.length - 1] };
  }

  function maybeIntel() {
    const ts = lastBybitTs;
    if (!Number.isFinite(ts) || ts - lastIntelTs < 5000) return;
    lastIntelTs = ts;

    // ── Confluence inputs — plain values from the existing stores/caches
    // (§4d contract: the builder never touches a store). Missing feeds stay
    // NaN/empty and read 'n/a' — never a fabricated 'neutral'. ──
    const prof = profile.profile();
    const tpo = tpoSessions && tpoSessions.length ? tpoSessions[0] : null;   // newest UTC session (REST — null in replay)
    const mBy = marks.bybit;
    const sm = sma50FromHist();
    const finished = [];
    for (const b of footprint.bars()) if (b.finished) finished.push(b.delta);
    confData = S.confluenceReads({
      fpDeltas: finished.slice(-20),   // last ≤20 finished bars — the recent flow, not the whole ring
      cvdSlope: cvdSlope60s(),
      price: lastPrice,
      poc: prof.poc, vah: prof.vah, val: prof.val,
      tpoPoc: tpo ? tpo.poc : NaN, tpoVah: tpo ? tpo.vah : NaN, tpoVal: tpo ? tpo.val : NaN,
      fundingRate: mBy ? mBy.fundingRate : NaN,
      // Response-provided interval from the tickers poll when known (§4d —
      // beats the 8h constant); the builder falls back to 8 on its own.
      fundingIntervalH: btcTicker ? btcTicker.fundingIntervalH : undefined,
      oiChangePct1h: oiChangePct1h(ts),
      liqImb5m: liqImb5m(ts),
      bookImb: bookImb10(),
      sma50: sm.sma, lastClose: sm.close,
    });
    dirty.conf = true;

    // ── AlertEngine snapshot — same gate, same event clock. ──
    // Divergence window: one {price, CVD} sample per gate, ring 24 ≈ 2min.
    if (Number.isFinite(lastPrice)) {
      const cs = cvds.bybit.series();
      intelWin.price.push(lastPrice);
      intelWin.cvd.push(cs.overall.length ? cs.overall[cs.overall.length - 1] : NaN);
      if (intelWin.price.length > 24) { intelWin.price.shift(); intelWin.cvd.shift(); }
    }
    // Detector events NEW since the last evaluate (identity scan — the ring
    // caps at 100, so a plain count goes blind after it wraps).
    const detEvs = detector.events();
    let newDet = [];
    if (detEvs.length) {
      const i = detSeenIntel ? detEvs.lastIndexOf(detSeenIntel) : -1;
      newDet = i >= 0 ? detEvs.slice(i + 1) : detEvs.slice();
      detSeenIntel = detEvs[detEvs.length - 1];
    }
    const fired = alertEngine.evaluate({
      ts,
      price: lastPrice,
      trades: pendingTrades,
      liq1mUsd: liq.sumWindow(60000, ts),
      fundingRate: mBy ? mBy.fundingRate : NaN,
      window: { price: intelWin.price.slice(), cvd: intelWin.cvd.slice() },
      bookImb: bookImb10(),
      detectorEvents: newDet,
      oiChangePct1h: oiChangePct1h(ts),
      basisBp: (mBy && Number.isFinite(mBy.mark) && Number.isFinite(mBy.index) && mBy.index !== 0)
        ? ((mBy.mark - mBy.index) / mBy.index) * 1e4 : NaN,
    });
    pendingTrades.length = 0;   // consumed — the next snapshot sees only newer prints
    if (fired.length) {
      for (const ev of fired) alertFresh.push(ev);
      dirty.alerts = true;
    }
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
  // O-4 budgets: scr/rsi/opts move at REST-poll cadence (30–60s) — budgets
  // just cap redraw bursts from hover-independent dirty flips; conf/alerts
  // tick with the 5s intel gate; whale at its 60s/address polls.
  const MIN_MS = {
    fp: 250, dom: 120, tape: 180, agg: 220, header: 400, liq: 300, heat: 500, liqmap: 600, det: 250,
    hist: 500, tpo: 800, vp: 800, farb: 500, macro: 800,
    scr: 800, rsi: 500, opts: 1000, whale: 600, alerts: 300, conf: 800,
    // O-5 budgets: jour/cal move on user actions; poly/news/econ at their
    // 30–60s poll cadence — budgets just cap redraw bursts.
    jour: 400, cal: 600, poly: 1000, news: 800, econ: 1000,
  };
  const lastAt = {
    fp: 0, dom: 0, tape: 0, agg: 0, header: 0, liq: 0, heat: 0, liqmap: 0, det: 0,
    hist: 0, tpo: 0, vp: 0, farb: 0, macro: 0,
    scr: 0, rsi: 0, opts: 0, whale: 0, alerts: 0, conf: 0,
    jour: 0, cal: 0, poly: 0, news: 0, econ: 0,
  };

  // ─── O-5 elite pass (§4e.1 + §4e.2): section collapse + visibility-gated
  // painting ────────────────────────────────────────────────────────────────
  //
  // HONESTY (comment mandated by §4e.2): skipping paint ≠ skipping data.
  // Nothing below ever consults these gates for INGESTION — sink(),
  // sampleDepth(), the liq-model gate and the intel gate all run regardless,
  // so stores keep accumulating while a panel is collapsed, offscreen, or the
  // tab is hidden. A skipped view keeps its dirty flag (due() below refuses
  // BEFORE clearing it) and repaints the moment it is visible again.

  // View key → its section (for the collapse gate) + its panel anchor (for
  // the IntersectionObserver). 'header' appears in neither ON PURPOSE: the
  // stats strip carries the connection chips and is exempt from every
  // presentation gate except document.hidden (nobody is looking) — hiding
  // connection health could mask a dead feed (same rule as the pause button).
  const SEC_OF = {
    fp: 'orderflow', dom: 'orderflow', tape: 'orderflow', agg: 'orderflow',
    liq: 'orderflow', heat: 'orderflow', liqmap: 'orderflow', det: 'orderflow',
    hist: 'structure', tpo: 'structure', vp: 'structure', farb: 'structure', macro: 'structure',
    scr: 'intelligence', rsi: 'intelligence', opts: 'intelligence',
    whale: 'intelligence', alerts: 'intelligence', conf: 'intelligence',
    jour: 'portfolio', cal: 'portfolio', poly: 'portfolio', news: 'portfolio', econ: 'portfolio',
  };
  const VIEW_ANCHOR = {
    fp: 'view-footprint', dom: 'view-dom', tape: 'view-tape', agg: 'view-aggbook',
    liq: 'view-liq', heat: 'view-bookheat', liqmap: 'view-liqheat', det: 'view-detect',
    hist: 'view-hist', tpo: 'view-tpo', vp: 'view-klinevp', farb: 'view-farb', macro: 'view-macro',
    scr: 'view-screener', rsi: 'view-rsi', opts: 'view-options',
    whale: 'view-whale', alerts: 'view-alerts', conf: 'view-conf',
    jour: 'view-journal', cal: 'view-calendar', poly: 'view-polymarket', news: 'view-news', econ: 'view-econ',
  };
  // key → last IntersectionObserver verdict. Defaults TRUE (paint until told
  // otherwise) so the page is never blank if IO is unavailable.
  const onScreen = {};
  for (const k in VIEW_ANCHOR) onScreen[k] = true;
  if (typeof IntersectionObserver === 'function') {
    const keyOfEl = new Map();
    const io = new IntersectionObserver((entries) => {
      for (const en of entries) {
        const key = keyOfEl.get(en.target);
        if (!key) continue;
        onScreen[key] = en.isIntersecting;
        // Re-entering the viewport repaints what moved while offscreen.
        if (en.isIntersecting) dirty[key] = true;
      }
      // rootMargin pre-paints just-below-the-fold panels so scrolling never
      // meets a blank flash (and the fp key also paints the CVD subchart —
      // gating fp on the footprint anchor alone covers both).
    }, { rootMargin: '600px 0px' });
    for (const k in VIEW_ANCHOR) {
      const el = $(VIEW_ANCHOR[k]);
      if (el) { keyOfEl.set(el, k); io.observe(el); }
    }
  }

  /** May `key` paint right now? (§4e.1 collapsed-section render skip +
   *  §4e.2 offscreen paint skip.) A never-painted view (lastAt 0) always
   *  gets its FIRST paint — a canvas that was never drawn would read as
   *  broken the instant it scrolls into view faster than the IO callback,
   *  and the L1 harness legitimately judges every canvas non-blank. */
  function paintable(key) {
    const sec = SEC_OF[key];
    if (sec && settings.collapsed[sec]) return false;   // collapsed section: rendering skipped entirely (§4e.1)
    if (onScreen[key] === false && lastAt[key] > 0) return false;   // offscreen: skip REpaints only (§4e.2)
    return true;
  }

  function due(key, now) {
    if (!paintable(key)) return false;   // gate BEFORE clearing dirty — the flag survives the skip
    if (!dirty[key] || now - lastAt[key] < MIN_MS[key]) return false;
    dirty[key] = false;
    lastAt[key] = now;
    return true;
  }

  // ── §4e.1 section mini-nav: collapse toggles (persisted; terminal.html
  // carries the buttons). Collapse hides PRESENTATION only — [data-sec]
  // elements get [hidden] (terminal.css forces display:none over any
  // flex/grid rule) and paintable() skips their renders; stores keep
  // accumulating either way. ──
  function applyCollapse() {
    document.querySelectorAll('[data-sec]').forEach((el) => {
      el.hidden = settings.collapsed[el.getAttribute('data-sec')] === true;
    });
    document.querySelectorAll('.sec-toggle').forEach((btn) => {
      const sec = btn.getAttribute('data-collapse');
      const c = settings.collapsed[sec] === true;
      btn.setAttribute('aria-pressed', String(c));
      btn.textContent = c ? '+' : '–';
    });
  }
  document.querySelectorAll('.sec-toggle').forEach((btn) => {
    btn.addEventListener('click', () => {
      const sec = btn.getAttribute('data-collapse');
      if (SECTIONS.indexOf(sec) < 0) return;
      settings.collapsed[sec] = !settings.collapsed[sec];
      saveSettings();
      applyCollapse();
      if (!settings.collapsed[sec]) {
        // Re-expanded: every view in the section repaints (its canvases were
        // display:none-sized while hidden and its data moved meanwhile).
        for (const k in SEC_OF) if (SEC_OF[k] === sec) dirty[k] = true;
      }
    });
  });
  applyCollapse();   // apply the PERSISTED state before the first frame

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
    // INGESTION-SIDE work runs on every tick unconditionally — §4e.2 honesty:
    // the visibility gates below skip PAINT only, never data. These three keep
    // sampling/evaluating while the tab is hidden (frame() then ticks on the
    // background timer in scheduleFrame instead of rAF).
    sampleDepth();
    maybeEstimateLiq();
    maybeIntel();   // O-4 (§4d): confluence + alert evaluation on the 5s event-ts gate

    // §4e.2: document.hidden pauses ALL painting (nobody is looking; browser
    // notifications from the alert engine cover the hidden-tab case) — the
    // same presentation-only rule as the pause button, and dirtyAll() on
    // visibilitychange repaints everything that moved the moment eyes return.
    if (document.hidden) { scheduleFrame(); return; }

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
      // O-4 INTELLIGENCE panels (§4d). REST-fed views are null in replay
      // (honest notes were rendered instead); conf/alerts run in both modes.
      if (screenerView && due('scr', now)) {
        const topN = settings.screenerTop === 'all' ? 0 : 40;   // buildScreener: topN ≤ 0 → whole universe
        const scr = S.buildScreener(tickerRows || [], { topN });
        screenerView.render({ rows: scr.rows, total: scr.total, topMode: settings.screenerTop });
      }
      if (rsiView && due('rsi', now)) {
        rsiView.render(rsiState);
      }
      if (optionsView && due('opts', now)) {
        // nowTs rides the slice (frame clock) — the view must not read
        // Date.now() itself for T-to-expiry (§4d GEX contract).
        optionsView.render({ chain: chainData, dvol: dvolVal, nowTs: now });
      }
      if (whaleView && due('whale', now)) {
        const entries = settings.whaleAddrs.map((a) => {
          const st = whaleState.get(a);
          return { addr: a, positions: st ? st.positions : undefined, ts: st ? st.ts : NaN };
        });
        whaleView.render({ entries, discovering: whaleDiscovering, note: whaleNote });
      }
      if (due('conf', now)) {
        confView.render({ conf: confData });
      }
      if (due('alerts', now)) {
        alertsView.render({ events: alertEngine.events(), fresh: alertFresh.splice(0) });
      }
      // O-5 PORTFOLIO panels (§4e). Journal + calendar run in both modes
      // (localStorage-fed); poly/news/econ are null in replay (honest notes
      // were rendered instead). Stats/calendar recompute on render — pure
      // functions over ≤ a few hundred journal rows, gated by dirty.jour/cal
      // which only user actions flip.
      if (due('jour', now)) {
        journalView.render({
          trades: journalTrades,
          stats: S.journalStats(journalTrades),
          note: journalNote,
          importErrors,
        });
      }
      if (due('cal', now)) {
        calView.render({ cal: S.calendarReturns(journalTrades), nowMs: now });
      }
      if (polyView && due('poly', now)) {
        polyView.render({ events: polyEvents, nowMs: now });
      }
      if (newsView && due('news', now)) {
        newsView.render({ items: newsItems, nowMs: now });
      }
      if (econView && due('econ', now)) {
        econView.render({ data: econData, nowMs: now });
      }
    }

    scheduleFrame();
  }

  // §4e.2 scheduling seam: rAF while visible (paint-synced), a coarse 500ms
  // timer while document.hidden — browsers throttle/starve rAF in hidden
  // tabs, and the ingestion-side work at the top of frame() (depth sampler /
  // liq model / intel gate) must keep running so stores and event-time gates
  // accumulate the true session. Paint is skipped while hidden either way.
  function scheduleFrame() {
    if (document.hidden) setTimeout(frame, 500);
    else requestAnimationFrame(frame);
  }
  scheduleFrame();
  document.addEventListener('visibilitychange', () => {
    // Eyes back on the page: repaint everything that moved while hidden.
    // (The pending 500ms timer tick reschedules itself onto rAF via
    // scheduleFrame — no double-scheduling.)
    if (!document.hidden) dirtyAll();
  });

  // Time itself moves the funding countdowns and rolls liqs out of the 1m/5m
  // windows even when no event arrives — tick those panels once a second
  // (farb joins in O-3: its 'next in' column is a countdown too, §4c;
  // O-5: polymarket/news/econ carry countdowns and age stamps — their
  // MIN_MS budgets cap this to ~1s repaints, and paintable() still skips
  // them offscreen/collapsed).
  setInterval(() => {
    dirty.header = true; dirty.liq = true; dirty.farb = true;
    dirty.poly = true; dirty.news = true; dirty.econ = true;
  }, 1000);

  // Canvas panels re-measure on their next draw; a resize makes them dirty.
  // (O-3: tpo/vp are canvases too; the hist chart resizes itself in-view.
  // O-4: screener/RSI/options are canvases; whale/alerts/confluence are DOM.)
  window.addEventListener('resize', () => {
    dirty.fp = true; dirty.agg = true; dirty.heat = true; dirty.liqmap = true;
    dirty.tpo = true; dirty.vp = true;
    dirty.scr = true; dirty.rsi = true; dirty.opts = true;
  });
})();
