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
  const B = window.BTCQ_TERMINAL_BOOKS;   // T-2 (§4h): book-sync engines + spot/perp CVD
  if (!LW || !A || !S || !B || !V || !window.BTCQ_TERMINAL_HIST) {
    // Script-order contract broken (§4/§4c load order) — say so, render nothing.
    console.error('terminal.js: missing globals (load order must be livewire → adapters → state → books → hist → views → terminal)');
    return;
  }

  const $ = (id) => document.getElementById(id);
  // T-1 (§4g): SYM is a RUNTIME setting (persisted; default BTCUSDT) — per-
  // venue ids derive from it via deriveVenueIds (the collector's mapping;
  // null = no derivable market → that leg degrades to an honest chip). The
  // lets are (re)assigned by switchSymbol; everything downstream reads them.
  let SYM = 'BTCUSDT';         // Bybit + Binance Futures linear perp
  let SPOT = 'BTC-USD';        // Coinbase Advanced Trade product (null = no leg)
  let OKX_INST = 'BTC-USDT-SWAP';   // OKX linear swap (O-2, §4b — sizes in CONTRACTS, adapter ctVal-scales; null = no leg)
  let BASE = 'BTC';            // base-asset code for unit labels (null = unknown quote → views omit the unit rather than mislabel it)

  /** Base asset of a USDT-quoted symbol (deriveVenueIds' strip-USDT
   *  convention); null when the quote is unknown — a '1000PEPE' OI labeled
   *  'BTC' would be a §0 mislabel, so unit strings ride this, never a
   *  hardcoded 'BTC'. */
  function baseAsset(sym) {
    return typeof sym === 'string' && sym.length > 4 && sym.endsWith('USDT') ? sym.slice(0, -4) : null;
  }

  // ─── Settings (persisted to localStorage 'btcq-terminal', §4) ───────────
  //
  // Only values from the whitelists below are accepted back from storage — a
  // hand-edited localStorage must not put the stores into an unsupported state.
  const LS_KEY = 'btcq-terminal';
  // T-1 (§4g): the tick whitelist is SYMBOL-DEPENDENT — [1,5,10,25,50] is the
  // pinned §4 BTC set; other symbols derive theirs from the ticker price via
  // tickOptionsFor (the "$10 at $100k" ≈1bp convention, settings hint). It is
  // a `let` rebuilt on symbol switch; the select is regenerated with it.
  let TICKS = [1, 5, 10, 25, 50];          // $ tick grouping (default 10 — §4 task spec)
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

  // O-5 (§4e.1): the collapsible page sections — the section mini-nav's
  // toggles persist through the settings object like every other control.
  // I-1 (§4f) adds AUCTION between structure and intelligence.
  const SECTIONS = ['orderflow', 'structure', 'auction', 'intelligence', 'portfolio'];
  // I-1 (§4f): VWAP anchor whitelist (day = current UTC day, week = current
  // UTC ISO week, custom = the datetime input — parsed as UTC, stated).
  const VWAP_ANCHORS = ['day', 'week', 'custom'];

  // T-1 (§4g): workspace presets = named collapsed-set combos; 'last' is the
  // user's own custom state (kept in lastCollapsed whenever a section toggle
  // is used manually).
  const WORKSPACES = {
    all: { orderflow: false, structure: false, auction: false, intelligence: false, portfolio: false },
    'orderflow-focus': { orderflow: false, structure: true, auction: true, intelligence: true, portfolio: true },
    'auction-focus': { orderflow: true, structure: true, auction: false, intelligence: true, portfolio: true },
  };
  const WORKSPACE_NAMES = ['all', 'orderflow-focus', 'auction-focus', 'last'];

  // T-3 (§4i): tape controls — market filter, single-venue filter, and the
  // metric-column toggle. Venue codes are the frozen event ex codes (defined
  // as EX_LEG below); listed here as a plain whitelist so loadSettings (which
  // runs before EX_LEG) can validate a stored value without forward-referencing.
  const TAPE_MARKETS = ['both', 'spot', 'perp'];
  const TAPE_METRICS = ['ntl', 'avg', 'cum', 'ago'];
  const TAPE_VENUES = ['all', 'bybit', 'bybit_spot', 'binancef', 'binance_spot', 'okx', 'okx_spot', 'coinbase'];
  // DOM ladder source (§4i): a single venue book, or the aggregated same-quote
  // (USDT) merge — display-only, loudly caveated (mergeSameQuoteBooks).
  const DOM_SOURCES = TAPE_VENUES.slice(1).concat(['__agg']);
  const DOM_IMB_TICKS = 20;   // §4i depth-imbalance band — a stated convention
  // Liquidation notional tiers are S.liqTier / S.LIQ_TIER_DEFAULTS (§4i, own
  // thresholds separate from the tape's) — ONE classifier for the feed's ◆/◇
  // emphasis and the audio-ping trigger, never a re-derived inline threshold.

  const DEFAULTS = {
    tick: 10, barMs: 60000, tapeMin: 0, liqRange: 'pct6',
    // O-4 (§4d): screener slice, whale watchlist (+BTC filter), alert rules.
    screenerTop: '40', whaleBtcOnly: true, whaleAddrs: [], alertRules: DEFAULT_RULES,
    // O-5 (§4e.1): per-section collapse state (all expanded by default).
    collapsed: { orderflow: false, structure: false, auction: false, intelligence: false, portfolio: false },
    // I-1 (§4f): levels 'draw on charts' toggle + hist VWAP-band controls.
    levelsDraw: false, vwapOn: false, vwapAnchor: 'day',
    // T-1 (§4g): runtime symbol, footprint Δ-rows toggle, key-level footprint
    // markers (default on per contract), workspace preset + the custom state.
    sym: 'BTCUSDT', fpDeltaRows: true, klevDraw: true, workspace: 'all',
    lastCollapsed: { orderflow: false, structure: false, auction: false, intelligence: false, portfolio: false },
    // T-2 (§4h): the 7-leg enabled-set — ALL enabled by default (contract);
    // populated per-key in loadSettings so DEFAULTS never shares the object.
    legs: null,
    // T-3 (§4i): tape size tiers (labeled DISPLAY conventions, not signals —
    // BTC-scaled sizeTier defaults, user-overridable), the optional audio UX
    // aid (default OFF + muted, persisted — never a signal), the tape
    // market/venue filters + metric-column toggle, the DOM ladder source, and
    // the cumulative-depth toggle. Object values are deep-copied in
    // loadSettings so DEFAULTS is never shared (the legs/collapsed rule).
    tapeTiers: null, tapeAudio: null,
    tapeMarket: 'both', tapeVenue: 'all', tapeMetric: 'ntl',
    domSource: 'bybit', domCum: false,
  };

  // T-2 (§4h): the matrix leg keys — ONE source (deriveLegIds' shape), so the
  // settings whitelist, the registry and the lifecycle can never disagree.
  const LEG_KEYS = Object.keys(S.deriveLegIds(''));

  function loadSettings() {
    const s = Object.assign({}, DEFAULTS);
    // Deep-copy the array/object defaults — settings.whaleAddrs.push() must
    // never mutate DEFAULTS (a shared reference would corrupt the fallback).
    s.whaleAddrs = [];
    s.alertRules = DEFAULT_RULES.map((r) => Object.assign({ id: r.kind }, r));
    s.collapsed = { orderflow: false, structure: false, auction: false, intelligence: false, portfolio: false };
    s.lastCollapsed = { orderflow: false, structure: false, auction: false, intelligence: false, portfolio: false };
    // T-2 (§4h): all legs enabled unless storage holds a strict boolean for a
    // KNOWN key (same validated-on-load rule as every other stored value).
    s.legs = {};
    for (const k of LEG_KEYS) s.legs[k] = true;
    // T-3 (§4i): deep-copy the object defaults so DEFAULTS is never shared.
    s.tapeTiers = Object.assign({}, S.SIZE_TIER_DEFAULTS);
    s.tapeAudio = { on: false, vol: 0.4 };
    try {
      const j = JSON.parse(localStorage.getItem(LS_KEY) || '{}');
      // T-1 (§4g): tick is validated for SANITY (finite, positive, bounded)
      // rather than against the fixed list — the legal set is symbol-
      // dependent and rebuilt from the ticker price once the universe
      // arrives; the select whitelists from then on.
      if (Number.isFinite(j.tick) && j.tick > 0 && j.tick <= 10000) s.tick = j.tick;
      if (BARS.indexOf(j.barMs) >= 0) s.barMs = j.barMs;
      // T-1 (§4g): symbol — exchange-style code only (it goes into WS topic
      // strings and REST urls; anything else must not return from storage).
      if (typeof j.sym === 'string' && /^[A-Z0-9]{5,20}$/.test(j.sym)) s.sym = j.sym;
      if (typeof j.fpDeltaRows === 'boolean') s.fpDeltaRows = j.fpDeltaRows;
      if (typeof j.klevDraw === 'boolean') s.klevDraw = j.klevDraw;
      if (WORKSPACE_NAMES.indexOf(j.workspace) >= 0) s.workspace = j.workspace;
      if (j.lastCollapsed && typeof j.lastCollapsed === 'object') {
        for (const sec of SECTIONS) {
          if (typeof j.lastCollapsed[sec] === 'boolean') s.lastCollapsed[sec] = j.lastCollapsed[sec];
        }
      }
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
      // O-5 (§4e.1): only KNOWN section names, strict booleans — same
      // validated-on-load rule as every other stored value above.
      if (j.collapsed && typeof j.collapsed === 'object') {
        for (const sec of SECTIONS) {
          if (typeof j.collapsed[sec] === 'boolean') s.collapsed[sec] = j.collapsed[sec];
        }
      }
      // I-1 (§4f): levels-overlay toggle + VWAP band controls (whitelisted).
      if (typeof j.levelsDraw === 'boolean') s.levelsDraw = j.levelsDraw;
      if (typeof j.vwapOn === 'boolean') s.vwapOn = j.vwapOn;
      if (VWAP_ANCHORS.indexOf(j.vwapAnchor) >= 0) s.vwapAnchor = j.vwapAnchor;
      // T-2 (§4h): leg enabled-set — known keys, strict booleans only.
      if (j.legs && typeof j.legs === 'object') {
        for (const k of LEG_KEYS) {
          if (typeof j.legs[k] === 'boolean') s.legs[k] = j.legs[k];
        }
      }
      // T-3 (§4i): tape tiers — each cut a finite positive USD notional; a
      // hand-edited blob cannot smuggle a NaN/negative/out-of-order tier past
      // the sanity gate (a bad tier would mis-classify every block). Only a
      // strictly-increasing sig<large<huge<whale set is adopted whole; a
      // partial/garbled object keeps the labeled defaults.
      if (j.tapeTiers && typeof j.tapeTiers === 'object') {
        const t = j.tapeTiers, k = ['sig', 'large', 'huge', 'whale'];
        const ok = k.every((x) => Number.isFinite(t[x]) && t[x] > 0)
          && t.sig < t.large && t.large < t.huge && t.huge < t.whale;
        if (ok) s.tapeTiers = { sig: t.sig, large: t.large, huge: t.huge, whale: t.whale };
      }
      if (j.tapeAudio && typeof j.tapeAudio === 'object') {
        if (typeof j.tapeAudio.on === 'boolean') s.tapeAudio.on = j.tapeAudio.on;
        if (Number.isFinite(j.tapeAudio.vol)) s.tapeAudio.vol = Math.max(0, Math.min(1, j.tapeAudio.vol));
      }
      if (TAPE_MARKETS.indexOf(j.tapeMarket) >= 0) s.tapeMarket = j.tapeMarket;
      if (TAPE_VENUES.indexOf(j.tapeVenue) >= 0) s.tapeVenue = j.tapeVenue;
      if (TAPE_METRICS.indexOf(j.tapeMetric) >= 0) s.tapeMetric = j.tapeMetric;
      if (DOM_SOURCES.indexOf(j.domSource) >= 0) s.domSource = j.domSource;
      if (typeof j.domCum === 'boolean') s.domCum = j.domCum;
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
        levelsDraw: settings.levelsDraw, vwapOn: settings.vwapOn, vwapAnchor: settings.vwapAnchor,
        sym: settings.sym, fpDeltaRows: settings.fpDeltaRows, klevDraw: settings.klevDraw,
        workspace: settings.workspace, lastCollapsed: settings.lastCollapsed,
        legs: settings.legs,
        // T-3 (§4i): tape tiers/audio/filters + ladder source.
        tapeTiers: settings.tapeTiers, tapeAudio: settings.tapeAudio,
        tapeMarket: settings.tapeMarket, tapeVenue: settings.tapeVenue, tapeMetric: settings.tapeMetric,
        domSource: settings.domSource, domCum: settings.domCum,
      }));
    } catch (_) { /* private mode / quota — settings just don't persist */ }
  }

  // T-1 (§4g): adopt the persisted symbol + derive the venue legs. In replay
  // the symbol is FORCED to BTCUSDT — the fixtures are recorded BTCUSDT
  // frames and driving them under another label would mislabel every panel.
  if (window.BTCQ_TERMINAL_REPLAY && window.BTCQ_TERMINAL_REPLAY.active()) settings.sym = 'BTCUSDT';
  SYM = settings.sym;
  BASE = baseAsset(SYM);
  {
    const ids = S.deriveVenueIds(SYM);
    SPOT = ids.coinbase;
    OKX_INST = ids.okx;
  }

  // ─── T-2 (§4h): leg registry + the event-tag ↔ leg-key mapping ──────────
  //
  // The registry holds the 7-leg matrix (enabled-set seeded from the
  // persisted settings; startAllLegs consults it BEFORE any socket opens).
  // Event `ex` codes: the four T-1 legs keep their FROZEN short codes —
  // every existing store/panel keys on 'bybit'/'binancef'/'okx'/'coinbase'
  // and relabeling them would orphan that state — while each NEW leg's ex
  // code IS its leg key. LEG_EX is the one place that mapping lives.
  const legReg = S.LegRegistry({ enabled: settings.legs });
  const LEG_EX = {
    bybit_linear: 'bybit', binancef: 'binancef', okx_swap: 'okx', coinbase: 'coinbase',
    bybit_spot: 'bybit_spot', binance_spot: 'binance_spot', okx_spot: 'okx_spot',
  };
  const EX_LEG = {};
  for (const k in LEG_EX) EX_LEG[LEG_EX[k]] = k;
  // Display labels (§4h chip taxonomy: venue·market) — presentation only;
  // keys/ex codes stay frozen. Mirrored in terminal-views.js EX_LABEL (the
  // two files stay standalone — shared-in-spirit helpers are mirrored, the
  // terminal-books.js header rule).
  const LEG_LABEL = {
    bybit_linear: 'bybit·lin', bybit_spot: 'bybit·spot',
    binancef: 'binance·fut', binance_spot: 'binance·spot',
    okx_swap: 'okx·swap', okx_spot: 'okx·spot', coinbase: 'coinbase',
  };

  /** §4g tick-default convention (stated in the settings hint): ≈1bp of the
   *  symbol's price, snapped by niceRound to the 1/2/2.5/5×10^k grid, is the
   *  DEFAULT tick; the selectable set spans base×{0.1, 0.5, 1, 2.5, 5} —
   *  exactly the pinned §4 BTC set [1,5,10,25,50] at base $10. BTCUSDT keeps
   *  that pinned set verbatim (it predates the derivation and the fixtures
   *  assume it); other symbols derive from their ticker last price. */
  function tickOptionsFor(sym, price) {
    if (sym === 'BTCUSDT' || !Number.isFinite(price) || price <= 0) return [1, 5, 10, 25, 50];
    const base = niceRound(price * 1e-4);
    // toPrecision strips float artifacts (0.1 × 0.5 → 0.05000…4) so option
    // values round-trip through the select's string values exactly.
    return [0.1, 0.5, 1, 2.5, 5].map((m) => Number((m * base).toPrecision(3)));
  }

  /** Rebuild the tick <select> for the CURRENT symbol. On a SWITCH the tick
   *  resets to the derived default (index 2 = the ≈1bp base). With `keep`
   *  (boot / first universe answer) a persisted tick outside the derived set
   *  is kept and inserted — the user chose it; only a switch re-defaults. */
  function applyTickOptions(price, keep) {
    TICKS = tickOptionsFor(SYM, price);
    if (TICKS.indexOf(settings.tick) < 0) {
      if (keep && Number.isFinite(settings.tick) && settings.tick > 0) {
        TICKS = TICKS.concat([settings.tick]).sort((a, b) => a - b);
      } else {
        settings.tick = TICKS[2];
      }
    }
    const sel = $('set-tick');
    sel.innerHTML = TICKS.map((t) => '<option value="' + t + '">' + t + '</option>').join('');
    sel.value = String(settings.tick);
  }

  // Pause is deliberately NOT persisted: a page that loads pre-paused looks
  // exactly like a dead feed — an honesty-rail footgun. Pause also only
  // freezes RENDERING; the stores keep ingesting, so resuming shows the true
  // session (a paused-ingest design would leave a gap we'd be tempted to
  // paper over — §0.7 gaps stay gaps, so we never create one).
  let paused = false;

  // ─── Stores (terminal-state.js §4 + §4b) ─────────────────────────────────
  //
  // T-1 (§4g): the flow stores are `let`s behind rebuildFlowStores so a
  // symbol switch can restart them — a new symbol is a new session, the same
  // honest-restart rule as the tick regroup (nothing re-bucketed, nothing
  // synthesized). Per-exchange CVD (§4b): each venue gets its OWN session-
  // anchored store — the view labels every line per venue and computes the
  // exact Σ itself. Buckets only matter on the bybit store (the by-trade-size
  // lines stay single-venue, same §0.7 reasoning as the footprint).
  // T-2 (§4h): every trade-carrying leg gets its OWN CvdStore keyed by its
  // event ex code (= leg key for the new legs, LEG_EX note above), and the
  // CVD panel renders one labeled line per leg (spot legs dashed in the venue
  // hue — the view's EX_TOKEN market cue). binancef is absent by wire reality
  // (§0.2: no futures trades on this network) — the legend note says so
  // rather than drawing a fabricated flat line.
  const CVD_LEG_EXS = ['bybit', 'okx', 'coinbase', 'bybit_spot', 'binance_spot', 'okx_spot'];
  let tape, liq, aggBook, bybitBook, spotPerp;
  // T-3 (§4i): the aggregated read-the-tape surface. ONE TapeAggregator fed by
  // ALL enabled trade legs (§0.7-safe: it merges only same-venue runs, so a
  // venue change closes the run — the merged multi-venue tape is exactly the
  // time-ordered interleave of per-venue blocks). BigPrintRail is fed the rows
  // as they FLUSH (railFed dedup below) and keeps the last N huge/whale blocks.
  let tapeAgg, bigPrints, railFedSig;
  const cvds = {};   // stable object identity; per-venue stores swap inside
  function rebuildFlowStores() {
    tape = S.TapeStore(3000);
    liq = S.LiqStore(500);
    for (const ex of CVD_LEG_EXS) cvds[ex] = S.CvdStore({ bucketsUsd: [1e4, 1e5, 1e6] });   // §4 defaults
    aggBook = S.AggBookStore(['bybit', 'binancef', 'okx']);   // §4b merged book (lazily admits the T-2 legs)
    bybitBook = aggBook.books.get('bybit');   // DOM ladder = the primary venue's book
    // T-2 (§4h): spot-vs-perp CVD strip store — Σ signed USD flow of the
    // enabled perp legs vs the enabled spot legs (descriptive lead/lag read;
    // the strip itself lands with the UI wave, the session accumulates NOW).
    spotPerp = B.SpotPerpCvdStore({});
    tapeAgg = S.TapeAggregator({ aggWindowMs: 100, size: 400 });   // §4i aggr default window
    bigPrints = S.BigPrintRail({ max: 12, thresholds: settings.tapeTiers });
    railFedSig = null;
  }
  rebuildFlowStores();

  /** Feed BigPrintRail the aggregator's newly-FLUSHED rows (§4i). Once any
   *  trade is seen the aggregator's open row is list()[0] forever, so
   *  list()[1] is ALWAYS the newest CLOSED block — push it once (signature
   *  dedup) each time a fresh block flushes. Returns the flushed block's tier
   *  (the audio-ping trigger reads it), or null when nothing new flushed. */
  function feedRailFromTape() {
    const rs = tapeAgg.list();
    if (rs.length < 2) return null;
    const c = rs[1];
    const sig = c.ex + '|' + c.isBuy + '|' + c.price + '|' + c.ts + '|' + c.count;
    if (sig === railFedSig) return null;
    railFedSig = sig;
    bigPrints.push(c);           // kept iff huge/whale (rail's own tier gate)
    return S.sizeTier(c.notional, settings.tapeTiers);
  }

  /** Rebuild the rail with the CURRENT tiers (a tier edit re-classifies what a
   *  "block" is) and re-seed it from the blocks the aggregator still holds —
   *  deterministic and immediate, no waiting for fresh flow (§0 honest restart:
   *  nothing synthesized, only re-read). */
  function rebuildBigPrints() {
    bigPrints = S.BigPrintRail({ max: 12, thresholds: settings.tapeTiers });
    const rs = tapeAgg.list();
    for (let i = rs.length - 1; i >= 1; i--) bigPrints.push(rs[i]);   // oldest→newest, skip the open row
    railFedSig = rs.length >= 2 ? (rs[1].ex + '|' + rs[1].isBuy + '|' + rs[1].price + '|' + rs[1].ts + '|' + rs[1].count) : null;
  }

  // Footprint + profile are constructed AGAINST a tick/bar size, so changing
  // either setting rebuilds the store and restarts its session aggregation.
  // Honest limitation, stated in the settings row: we keep no raw tick store
  // in the browser to re-bucket from (that's the collector's job, §3), and
  // synthesizing the old bars onto a new grid would be fabrication (§0.7).
  let footprint, profile;
  // I-1 (§4f): the absorption detector consumes finished footprint bars and
  // shares their tick grid, so it rebuilds (and honestly restarts) with the
  // footprint — same rule as the O-2 grid-bound stores. absFedT tracks the
  // newest bar already fed (onBar wants each finished bar exactly once).
  let absDet, absFedT;
  function rebuildFootprint() {
    footprint = S.FootprintStore({ barMs: settings.barMs, tickSize: settings.tick });
    absDet = S.AbsorptionDetector({ volK: 3, progressTicks: 1, tickSize: settings.tick });   // §4f defaults
    absFedT = -Infinity;
  }
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
  // I-1 (§4f): OFI store + microprice−mid ring ride the SAME 1/s bybit book
  // sampler (and the same grouped() tick grid for OFI's per-level qtys), so
  // they rebuild with the heatmap stores on a tick change — re-bucketing old
  // ladders onto a new grid would fabricate flow that never printed (§0.7).
  let ofi;              // OfiStore (Cont–Kukanov–Stoikov, top-5 levels)
  let mpHist;           // [{ts, d}] microprice − mid ring (same 3600-sample horizon)
  // T-1 (§4g): the walls ledger is grid-bound too (its levels ARE grouped
  // ladder prices) so it rebuilds with this family — the settings hint names
  // it. prevLadder remembers the last sample's levels per side: a tracked
  // level MISSING from the next sample is how the ledger observes a
  // disappearance (caller contract, terminal-state.js).
  let walls, prevLadder;
  // T-3 (§4i): trade-imprint-at-price, ONE per trade-carrying leg (the DOM
  // ladder paints the imprint of whichever single-venue source it shows). The
  // imprint is grid-bound (its buckets ARE the grouping-tick grid), so it
  // rebuilds with this family on a tick change — re-bucketing a rolling window
  // onto a new grid would fabricate volume-at-price that never printed (§0.7).
  let imprints;
  function rebuildHeatmapStores() {
    depthHist = {}; priceTrail = {}; lastSampleTs = {};
    imprints = {};
    for (const ex of CVD_LEG_EXS) imprints[ex] = S.TradeImprint({ windowMs: 60000 });   // §4i rolling window
    for (const ex of HIST_EXS) {
      depthHist[ex] = S.DepthHistoryStore({ tickSize: settings.tick, maxSamples: 3600, nLevels: 40 });
      priceTrail[ex] = [];
      lastSampleTs[ex] = -Infinity;
    }
    detector = S.SpoofIcebergDetector({ tickSize: settings.tick });   // §4b defaults, grid matched
    liqModel = S.LiqHeatmapModel({ tickSize: settings.tick });        // §4b defaults (tiers/mmr)
    liqEst = null;
    lastEstTs = -Infinity;
    ofi = S.OfiStore({ levels: 5 });   // §4f default top-N
    mpHist = [];
    walls = S.WallsLedger({});        // K=4×p95, M=5 — §4g conventions
    prevLadder = { bid: new Map(), ask: new Map() };
  }
  rebuildHeatmapStores();

  // ─── T-1 (§4g): Trader's Edge session stores — symbol-bound, NOT grid-
  // bound (a tick regroup keeps them; a symbol switch rebuilds them) ────────
  //
  // VPIN arming (§4g convention, stated on-panel): the store is constructed
  // only after 5 min of session flow with V = sessionVol/50, then V is
  // re-estimated hourly via setBucketVol (future buckets only — the store
  // never restates a completed bucket). Trades before arming size V; they do
  // not enter buckets (nothing is backfilled).
  //
  // The opening classifier + IB are anchored on the UTC day of the FIRST
  // bybit print (event time — deterministic in replay) and roll over with it;
  // openState.firstTs lets the witnessed-open rule refuse to classify a
  // session whose opening auction this page never saw.
  let tapeInt, basisSeries, vpin, sessVol, sessVolT0, lastVpinArmTs, openState;
  function rebuildEdgeStores() {
    tapeInt = S.TapeIntensityStore();
    basisSeries = S.BasisSeries({});
    vpin = null;
    sessVol = 0; sessVolT0 = NaN; lastVpinArmTs = -Infinity;
    openState = null;   // {openTs, cls, firstTs, ibHigh, ibLow} — set on the first print
  }
  rebuildEdgeStores();

  /** Witnessed-open convention (§4g honesty rail): the first print this page
   *  ingested for the session's UTC day landed within 60 s of 00:00 UTC —
   *  i.e. the page was actually listening when the auction opened. Without
   *  it, opening-type and IB stay honestly withheld (classifying a partial
   *  window would present fabricated evidence). */
  function openWitnessed() {
    return !!openState && openState.firstTs - openState.openTs <= 60000;
  }

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

  // ─── I-1 (§4f): session clock + anchored VWAP, fed off the bybit tape ────
  //
  // AnchoredVwap accumulates ONLY trades this page witnessed (weighted
  // Welford, terminal-state.js): with a day/week anchor earlier than page
  // open the running value covers page-open→now — stated in the hist legend,
  // and the one-shot /v1/vwap fetch (recorded store, tick-exact) is the
  // labeled cross-check for the full anchor range. Re-anchoring mid-session
  // restarts accumulation from that moment (the browser keeps no raw tick
  // store to replay — the same honest-restart rule as the tick regroup).
  const sessionClock = S.SessionClock();
  const anchoredVwap = S.AnchoredVwap();
  let vwapHist = [];          // genuinely-sampled {ts, vwap, s1, s2} points (5s intel gate)
  let storeVwapTxt = null;    // /v1/vwap parity text (one fetch per anchor change, never merged)

  // ─── Dirty flags — the ONLY signal that a view needs repainting ─────────
  const dirty = {
    fp: true, dom: true, tape: true, agg: true, header: true, liq: true,
    heat: true, liqmap: true, det: true,   // O-2 panels (§4b)
    hist: true, tpo: true, vp: true, farb: true, macro: true,   // O-3 STRUCTURE panels (§4c)
    auct: true, lvls: true, micro: true,   // I-1 AUCTION panels (§4f)
    scr: true, rsi: true, opts: true, whale: true, alerts: true, conf: true,   // O-4 INTELLIGENCE panels (§4d)
    jour: true, cal: true, poly: true, news: true, econ: true,   // O-5 PORTFOLIO panels (§4e)
    tapeint: true, walls: true, vpin: true, klev: true, basis: true,   // T-1 Trader's Edge panels (§4g)
    spcvd: true,   // T-2 (§4h): spot-vs-perp CVD strip
  };
  function dirtyAll() { for (const k in dirty) dirty[k] = true; }

  // ─── N1: paint-loop quarantine — per-panel render circuit breakers ───────
  //
  // Gap 2 (STRATEGY §3): frame() paints ~34 panels and scheduleFrame() was its
  // LAST statement, so ONE throw anywhere before it froze every panel forever.
  // Ingest is hardened (livewire.js try/catch); paint was not. The fix is two
  // orthogonal layers: (1) a top-level try/finally in frame() so the loop always
  // re-arms, and (2) this per-panel error boundary so a faulting panel is
  // isolated while its SIBLINGS keep painting.
  //
  // One breaker per view key, keyed off dirty{} (the existing 34-key source of
  // truth — no new parallel table, Gap 4 not worsened). The isolation UNIT is
  // the whole per-panel block (slice-build + render), not just render(): a throw
  // can originate in S.stackedImbalances(), keyLevelMarks(), cvds[ex].series(),
  // a map over bars — all inside the block but outside view.render() — so the
  // wrapper must wrap the whole block (safePanel below does exactly that).
  const guards = {};
  for (const k in dirty) guards[k] = S.makePanelGuard({ threshold: 3 });
  // N1 (findings 1/6): the per-frame INGESTION prologue (flushBookLegs /
  // sampleDepth / maybeEstimateLiq / maybeIntel) runs BEFORE any panel and is
  // shared code with a real throw surface (maybeIntel → S.confluenceReads etc.,
  // no inner try/catch). A throw there is re-armed by the frame() try/finally,
  // but a finally does NOT suppress it — un-guarded it re-raised out of frame()
  // EVERY frame (unbounded uncaught-pageerror spam) and unwound before any panel
  // painted. Its own breaker isolates it the same way safePanel isolates a
  // panel: caught, logged ONCE, surfaced on the header. Not in dirty{} (it is
  // not a paintable view), so it is added by hand.
  guards.ingest = S.makePanelGuard({ threshold: 3 });
  // N1: loop heartbeat — incremented once per frame() invocation. Read-only via
  // the debug hook (frames()); the L1 fault-injection proof samples it twice to
  // show the rAF loop is STILL running after a panel was quarantined.
  let frameCount = 0;

  // ─── Dead-panel DOM surface: mark EVERY unit a key paints, at the right grain ─
  //
  // A render key is NOT 1:1 with one <section.panel> — two realities the naive
  // closest('.panel') got wrong, both §0 honesty bugs (findings 3/4):
  //   • 'fp' paints TWO panels: fpView.render draws the footprint AND the CVD
  //     subchart (cvdEl: view-cvd, a SEPARATE .panel). Quarantining fp must
  //     chip+dim BOTH, or a FROZEN CVD curve sits beside the chipped footprint
  //     wearing no 'stalled' label — the §0.7 silent-stale-surface N1 exists to
  //     prevent. (view-cvd is the one cross-panel paint — mapped explicitly.)
  //   • view-tape + view-tapeint SHARE one .panel (the tape + its speed strip).
  //     Marking that shared .panel would dim+chip the still-LIVE sibling — a
  //     false 'stalled' on live data, §0 honesty inverted (crying wolf). So a
  //     unit that shares its panel dims only ITS OWN sub-container (.unit--dead),
  //     never the section; the chip in the shared <h2> names which sub-unit died.
  //
  // Shared-ness is DETECTED at mark time (another render unit lives in the same
  // .panel), not hard-coded — the unit set is VIEW_ANCHOR's values plus view-cvd.
  // All read at call time (VIEW_ANCHOR + the DOM exist long before a quarantine).
  function renderUnitIds() { return Object.values(VIEW_ANCHOR).concat(['view-cvd']); }
  // Every DOM element a key paints. Default: its one VIEW_ANCHOR element (header
  // and the 'ingest' pseudo-key have none → the header panel). fp is the lone
  // cross-panel exception.
  function panelUnitsOf(key) {
    const ids = key === 'fp' ? ['view-footprint', 'view-cvd']
              : [VIEW_ANCHOR[key] || 'view-header'];
    return ids.map((id) => $(id)).filter(Boolean);
  }
  // Human names for the sub-unit / pseudo keys whose raw key reads badly — used
  // in the shared-panel chip label and in every tooltip.
  const UNIT_LABEL = { tape: 'tape', tapeint: 'tape speed', ingest: 'data intake', header: 'header' };

  // §0 honesty (§0.1/§0.7, cf. terminal-views.js 'never carry a silently-broken
  // chart'): a quarantined unit keeps its LAST GOOD frame on screen — that is
  // stale, so it MUST announce it, never pass stale pixels off as live. The
  // 'stalled' chip rides the .signal-tag taxonomy (st-dead = error palette).
  // Idempotent per key: safePanel flags once (fail()'s death transition), and a
  // re-mark for the same key stays a no-op.
  function markPanelDead(key, err) {
    const last = err && err.message ? err.message : String(err);
    const N = guards[key] ? guards[key].stats().threshold : 3;
    const label = UNIT_LABEL[key] || key;
    for (const el of panelUnitsOf(key)) {
      const panel = el.closest('.panel');
      if (!panel) continue;
      // Shared .panel (another render unit sits in it) → dim only THIS
      // sub-container so a faulting sibling never dims the live one; sole owner
      // → dim the whole panel body.
      const shared = renderUnitIds().some((id) => {
        const u = $(id);
        return u && u !== el && panel.contains(u);
      });
      (shared ? el : panel).classList.add(shared ? 'unit--dead' : 'panel--dead');
      // Chip host: the panel <h2> (NEVER the dimmed sub-container — opacity on
      // the parent would fade the chip too). The header panel has no <h2> → its
      // chip rides .term-chips, right where connection health lives, so a dead
      // header/ingest is loud, not silent. Last resort: the panel itself.
      const host = panel.querySelector('h2') || panel.querySelector('.term-chips') || panel;
      if (host.querySelector('.st-dead[data-key="' + key + '"]')) continue;  // already flagged for this key
      const chip = document.createElement('span');
      chip.className = 'signal-tag st-dead';
      chip.setAttribute('data-key', key);
      // Shared chips name the sub-unit (two can share one <h2>); a sole owner
      // just says 'stalled' — its whole panel is dimmed, so there is no ambiguity.
      chip.textContent = shared ? label + ' stalled' : 'stalled';
      // .title is a property, not innerHTML — no escaping needed for err.message.
      chip.title = 'render fault — ' + label + ' was quarantined after ' + N
        + ' consecutive paint errors so the rest of the terminal keeps painting. '
        + 'Showing the LAST GOOD frame, not live data (§0). Switch symbol or reload '
        + 'to reset. Last error: ' + last;
      host.appendChild(chip);
    }
  }
  function clearPanelDead(key) {
    for (const el of panelUnitsOf(key)) {
      const panel = el.closest('.panel');
      if (!panel) continue;
      el.classList.remove('unit--dead');
      panel.classList.remove('panel--dead');
      const chip = panel.querySelector('.st-dead[data-key="' + key + '"]');
      if (chip) chip.remove();
    }
  }
  // The per-panel error boundary. Runs the whole block (slice-build + render)
  // inside the panel's breaker: a throw ANYWHERE in it is caught, counted, and
  // after N consecutive throws the breaker latches — we log ONCE (the death
  // transition, not per frame — §N1 anti-spin-loop rail), flag the panel, and
  // stop calling it (no retry; the chip carries the state). Below threshold, or
  // once dead, siblings are untouched. The arrow is allocated only when due()
  // already fired, so a skipped panel costs nothing extra.
  function safePanel(key, fn) {
    const g = guards[key];
    if (g.isDead()) return;                 // quarantined: last good frame stays, no retry
    try {
      // N1 verification hook (inert in production — see FAULT_KEY): forces this
      // ONE panel to throw BEFORE fn(), mimicking a slice-build throw so the
      // proof exercises the whole-block boundary, not just render().
      if (key === FAULT_KEY) throw new Error('fault-injection: forced throw (?fault=' + key + ', verification only §N1)');
      fn();
      g.ok();
    } catch (err) {
      if (g.fail(err)) {                     // true ONLY on the death transition
        markPanelDead(key, err);
        console.error('panel "' + key + '" quarantined after ' + g.stats().threshold
          + ' consecutive render faults: ' + (err && err.message ? err.message : err));
      }
    }
  }

  // ─── T-3 (§4i): optional audio UX aid — Web-Audio ping, NO assets ────────
  //
  // Default OFF + muted, persisted (settings.tapeAudio). A short synthesized
  // blip on a huge/whale BLOCK, a distinct lower two-tone on a large LIQ.
  // LABELED A UX AID, NOT A SIGNAL (§4i). The AudioContext is created lazily on
  // first use (browsers block autoplay until a user gesture — the toggle is
  // one), and pings are rate-limited so a swept level cannot machine-gun.
  let audioCtx = null;
  let lastPingAt = 0;
  function ensureAudioCtx() {
    if (audioCtx) return audioCtx;
    try {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (AC) audioCtx = new AC();
    } catch (_) { audioCtx = null; }
    return audioCtx;
  }
  function audioPing(kind) {
    if (!settings.tapeAudio || !settings.tapeAudio.on) return;
    const ctx = ensureAudioCtx();
    if (!ctx) return;
    const now = Date.now();
    if (now - lastPingAt < 140) return;   // rate-limit — a swept level is ONE cue
    lastPingAt = now;
    if (ctx.state === 'suspended') { try { ctx.resume(); } catch (_) { /* gesture pending */ } }
    const vol = Math.max(0, Math.min(1, settings.tapeAudio.vol)) * 0.28;   // conservative ceiling
    if (vol <= 0) return;
    const t0 = ctx.currentTime;
    const beep = (freq, start, dur) => {
      const osc = ctx.createOscillator();
      const g = ctx.createGain();
      osc.type = 'sine';
      osc.frequency.value = freq;
      g.gain.setValueAtTime(0.0001, t0 + start);
      g.gain.linearRampToValueAtTime(vol, t0 + start + 0.008);
      g.gain.exponentialRampToValueAtTime(0.0001, t0 + start + dur);
      osc.connect(g); g.connect(ctx.destination);
      osc.start(t0 + start); osc.stop(t0 + start + dur + 0.02);
    };
    if (kind === 'liq') { beep(320, 0, 0.12); beep(220, 0.10, 0.16); }   // lower two-tone
    else beep(880, 0, 0.10);                                             // bright blip
  }

  // ─── The sink: every normalized adapter event funnels through here (§4) ──
  function sink(ev) {
    switch (ev.kind) {
      case 'trade':
        tape.push(ev);
        dirty.tape = true;
        // T-3 (§4i): feed the ONE aggregated tape with EVERY trade leg — the
        // aggregator merges only same-venue/side/price runs, so the output is
        // the time-ordered interleave of per-venue blocks (never a cross-venue
        // fusion, §0.7). Then push any freshly-flushed block to the big-print
        // rail; a huge/whale flush optionally pings (UX aid, gated OFF).
        tapeAgg.push({ ts: ev.ts, ex: ev.ex, isBuy: ev.aggressorBuy, price: ev.price, qty: ev.qty, notional: ev.price * ev.qty });
        {
          const flushedTier = feedRailFromTape();
          if (flushedTier === 'huge' || flushedTier === 'whale') audioPing('block');
        }
        // T-3 (§4i): rolling volume-at-price per trade leg (the ladder paints
        // the imprint of its current single-venue source). Grouping-tick grid.
        if (imprints[ev.ex]) imprints[ev.ex].push(ev.ts, ev.price, ev.qty, ev.aggressorBuy, settings.tick);
        // T-1 (§4g): tape-intensity gauge — MIXED venues by design, like the
        // tape it sits on (the strip's title says so).
        tapeInt.push(ev.ts, ev.price * ev.qty);
        dirty.tapeint = true;
        // Per-exchange CVD (§4b): each venue's trades feed ONLY its own
        // labeled store — the panel legend names every line per venue.
        if (cvds[ev.ex]) { cvds[ev.ex].onTrade(ev); dirty.fp = true; }
        // T-2 (§4h): spot-vs-perp CVD — every trade leg pushes its signed
        // USD notional, split by MARKET (the registry's perp/spot defs). A
        // disabled leg's socket never opens, so only enabled flow ever
        // reaches this sum (the store's "caller pushes only enabled legs"
        // contract).
        {
          const legKey = EX_LEG[ev.ex];
          if (legKey && Number.isFinite(ev.ts) && Number.isFinite(ev.price) && ev.qty > 0) {
            spotPerp.push(ev.ts, legKey, legReg.isPerp(legKey), (ev.aggressorBuy ? 1 : -1) * ev.price * ev.qty);
          }
        }
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
          anchoredVwap.onTrade(ev);   // I-1 (§4f): session VWAP ± σ bands — primary leg only
          // Detector trades come from BYBIT only — it must see the SAME venue
          // as the depth samples it correlates (traded-volume-vs-wall math is
          // per-book; §4b feeds the detector from the primary venue). New
          // events are picked up by the frame loop's identity check, so no
          // dirty flag here — a trade that fires no rule repaints nothing.
          detector.onTrade(ev);
          if (!(sessionHigh >= ev.price)) sessionHigh = ev.price;   // NaN-safe first print
          if (!(sessionLow <= ev.price)) sessionLow = ev.price;
          lastPrice = ev.price;
          // T-1 (§4g): primary-leg flow feeds — same single-venue rule as
          // footprint/profile above (mixing venues would fabricate a session
          // that traded nowhere).
          if (Number.isFinite(ev.ts) && ev.qty > 0) {
            if (!Number.isFinite(sessVolT0)) sessVolT0 = ev.ts;
            sessVol += ev.qty;                              // sizes the VPIN bucket V (intel gate arms it)
            if (vpin) { vpin.push(ev.ts, ev.qty, ev.aggressorBuy); dirty.vpin = true; }
            walls.markTrade(ev.ts, ev.price);               // a print through a standing wall = 'filled'
            // Opening classifier + IB, anchored on the print's UTC day (event
            // time — rolls over deterministically at 00:00 UTC).
            const day0 = Math.floor(ev.ts / 86400000) * 86400000;
            if (!openState || day0 > openState.openTs) {
              openState = { openTs: day0, cls: S.OpeningTypeClassifier(day0), firstTs: ev.ts, ibHigh: NaN, ibLow: NaN };
              dirty.klev = true;
            }
            openState.cls.feed(ev.ts, ev.price);
            if (ev.ts < openState.openTs + 3600000) {       // IB = first 2×30min UTC range
              if (!(openState.ibHigh >= ev.price)) openState.ibHigh = ev.price;
              if (!(openState.ibLow <= ev.price)) openState.ibLow = ev.price;
              dirty.klev = true;
            }
          }
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
        // T-3 (§4i): a distinct ping for a large liquidation (own tier, UX aid
        // — gated OFF by default, never a signal). Same classifier the feed
        // paints with, so ping and ◆ emphasis never disagree.
        if (S.liqTier(ev.notionalUsd) === 'huge') audioPing('liq');
        break;
      case 'mark':
        marks[ev.ex] = ev;
        dirty.header = true;
        if (ev.ex === 'bybit' && Number.isFinite(ev.ts) && !(lastBybitTs >= ev.ts)) lastBybitTs = ev.ts;
        // T-1 (§4g): basis/funding ring rides the EXISTING ~1s bybit mark
        // events — no new feeds. A mark without a finite index yields no
        // basis point (never a fabricated one); absent funding stays NaN in
        // the store (BasisSeries contract).
        if (ev.ex === 'bybit' && Number.isFinite(ev.mark) && Number.isFinite(ev.index) && ev.index !== 0) {
          basisSeries.push(ev.ts, ((ev.mark - ev.index) / ev.index) * 1e4, ev.fundingRate);
          dirty.basis = true;
        }
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
  const fpDrowsInput = $('set-fp-drows');
  fpDrowsInput.checked = settings.fpDeltaRows;
  fpView.mount($('view-footprint'), {
    cvdEl: $('view-cvd'),
    buckets: cvds.bybit.buckets,   // by-size lines read the bybit store only
    cvdExs: CVD_LEG_EXS,           // §4b/§4h: one labeled line per trade leg + exact Σ
    // T-1 (§4g): Δmin/Δmax + Δ% footer rows — the view owns the display
    // choice, persistence lives here (the TapeView filter-input split).
    deltaRowsInput: fpDrowsInput,
    onDeltaRows: (on) => { settings.fpDeltaRows = on; saveSettings(); dirty.fp = true; },
  });

  const domView = V.DomLadderView();
  domView.mount($('view-dom'), { levels: 14 });
  // T-2/T-3 (§4h/§4i): ladder source select — one venue book at a time, OR the
  // aggregated same-quote (USDT) merge (loudly caveated). bybit linear stays
  // the default. The select lives in the panel chrome (terminal.html); the
  // whitelist + persistence live here.
  const domSourceSel = $('set-dom-source');
  const domCumBox = $('set-dom-cum');
  const domNote = $('dom-note');
  domSourceSel.value = settings.domSource;
  domCumBox.checked = settings.domCum;
  domSourceSel.addEventListener('change', () => {
    if (DOM_SOURCES.indexOf(domSourceSel.value) < 0) return;   // whitelist: known sources only
    settings.domSource = domSourceSel.value;
    saveSettings();
    dirty.dom = true;
  });
  domCumBox.addEventListener('change', () => {
    settings.domCum = domCumBox.checked;
    saveSettings();
    dirty.dom = true;
  });

  const aggView = V.AggBookView();
  aggView.mount($('view-aggbook'), { levels: 14, qualityEl: $('agg-quality') });
  // T-2 (§4h): per-leg include checkboxes — a DISPLAY-side merge filter
  // (AggBookStore.grouped includeExs): unchecking hides a leg from the merge,
  // its book keeps ingesting untouched. Default all checked.
  const aggInclude = {};
  document.querySelectorAll('#agg-controls input[data-agg-ex]').forEach((box) => {
    const ex = box.getAttribute('data-agg-ex');
    aggInclude[ex] = box.checked;
    box.addEventListener('change', () => { aggInclude[ex] = box.checked; dirty.agg = true; });
  });
  // The merge feed is checkbox AND registry: a leg-disable (topbar) closes the
  // socket and freezes that leg's book at its last frame, so it must ALSO drop
  // out of the shared live-descriptive merge — a frozen ladder painted as live
  // depth would be exactly the stale-as-live fabrication §0 forbids. The book
  // itself is left frozen (not cleared, never interpolated); this gate keeps it
  // off-canvas until the leg is re-enabled and re-snapshots.
  function aggIncludeExs() {
    const out = [];
    for (const ex in aggInclude) {
      if (aggInclude[ex] && legReg.isEnabled(EX_LEG[ex])) out.push(ex);
    }
    return out;
  }

  // T-2 (§4h): spot vs perp CVD strip — store-fed, mounts in BOTH modes
  // (replay drives the trade legs deterministically).
  const spotPerpView = V.SpotPerpCvdView();
  spotPerpView.mount($('view-spotperp'));

  // ─── T-2 (§4h): render-side helpers for the matrix surfaces ─────────────
  //
  // Composed HERE (terminal.js owns registry + engine + book access; the views
  // only paint). Every read is LIVE state — a leg's registry flag, its socket
  // status, its book presence in the merge, and each sync engine's OWN honest
  // counters — and nothing is interpolated: a leg with no proven book reads
  // 'syncing'/'offline', never a fabricated ladder.

  /** One leg's depth-quality cell {cls, text, title} for the agg-book strip.
   *  cls ∈ q-ok|q-wait|q-bad|q-off (terminal.css dot colors). Resync counts are
   *  the engines' OWN tallies — visible by contract (§4h), never smoothed. */
  function legDepthQuality(key) {
    const ex = LEG_EX[key];
    if (!legReg.isEnabled(key)) return { cls: 'q-off', text: 'disabled', title: 'disabled (settings) — socket closed, book frozen' };
    const st = statuses[ex] || {};
    if (st.kind === 'error') return { cls: 'q-bad', text: 'offline', title: st.msg || 'socket offline' };
    const book = aggBook.books.get(ex);
    const hasDepth = !!book && (book.bids.size > 0 || book.asks.size > 0);
    // Book-sync engine legs (binance spot/fut live, coinbase l2): the engine's
    // OWN state is the truth — a diff book that cannot prove continuity is
    // CLEARED, never shown (terminal-books.js header rule).
    const bl = bookLegs[key];
    if (bl && bl.eng) {
      const eng = bl.eng;
      if (typeof eng.state === 'string') {   // BinanceBookSync (spot U/u bracket | futures pu chain)
        const rs = eng.resyncCount ? ' · resync ×' + eng.resyncCount : '';
        if (eng.state === 'synced') return { cls: 'q-ok', text: 'synced' + rs, title: 'diff-synced full local book' + rs };
        if (eng.state === 'desync') return { cls: 'q-bad', text: 'desync — resyncing ×' + eng.resyncCount, title: 'continuity gap; book cleared, awaiting a fresh REST snapshot' };
        return { cls: 'q-wait', text: REPLAY ? 'no book (replay: no REST)' : 'syncing…', title: 'buffering diffs until the REST snapshot brackets them (replay forbids the fetch)' };
      }
      // CoinbaseBookSync — full snapshot + absolute l2; no venue sequence
      // number exists, so the rail is staleness-gated reconnect (no desync
      // count), and 'synced' means the full l2 book is populated.
      return hasDepth
        ? { cls: 'q-ok', text: 'synced (full l2)', title: 'full snapshot + absolute l2updates — no venue seq no.; staleness-gated reconnect' }
        : { cls: 'q-wait', text: 'syncing…', title: 'awaiting the full l2 snapshot' };
    }
    // OKX legs — seq-chain guarded in the adapter; a gap halts emission and
    // requests a resubscribe (the wire checksum is 0 on this network, §4h).
    const ol = okxLegs[key];
    if (ol && ol.adapter) {
      const rs = ol.adapter.bookResyncs ? ' · resync ×' + ol.adapter.bookResyncs : '';
      if (ol.adapter.bookGapped()) return { cls: 'q-wait', text: 'resync — seq gap' + rs, title: 'seqId/prevSeqId gap; resubscribing for a fresh snapshot' };
      return hasDepth
        ? { cls: 'q-ok', text: 'synced' + rs, title: 'seqId/prevSeqId chain intact (wire checksum is 0 keyless, §4h)' + rs }
        : { cls: 'q-wait', text: 'syncing…', title: 'awaiting the books snapshot' };
    }
    // Bybit WS books (linear/spot) + binance·fut-in-replay depth20 — snapshot +
    // deltas straight into the merge, no local sync engine.
    if (hasDepth) return { cls: 'q-ok', text: 'synced', title: 'WS book snapshot + deltas' };
    if (st.kind === 'stale') return { cls: 'q-wait', text: st.msg || 'stale', title: st.msg || 'feed stalled' };
    return { cls: 'q-wait', text: 'syncing…', title: 'awaiting the book snapshot' };
  }

  /** The 7 depth-quality rows in matrix order — AggBookView.renderQuality. */
  function legQualityRows() {
    return LEG_KEYS.map((key) => Object.assign({ ex: LEG_EX[key] }, legDepthQuality(key)));
  }

  /** Live composition of each spot-vs-perp sum: the ENABLED trade legs on each
   *  market side (registry split). binance·fut is excluded — no futures trades
   *  flow on this network (§0.2), so it contributes nothing to the perp Σ and
   *  listing it would overstate the composition. */
  function spotPerpComp() {
    const perp = [], spot = [];
    for (const key of LEG_KEYS) {
      if (key === 'binancef' || !legReg.isEnabled(key)) continue;
      (legReg.isPerp(key) ? perp : spot).push(LEG_LABEL[key]);
    }
    return { perp, spot };
  }

  // ─── T-3 (§4i): DOM ladder render helpers ───────────────────────────────
  const DOM_RAW_LEVELS = 60;   // raw grouped levels fed to ladderRows/depthImbalance (covers the display window + the N-tick band)

  /** A BookStore's grouped ladder as best-first [px,qty] PAIRS on the current
   *  grouping tick — the shape ladderRows/mergeSameQuoteBooks consume (§4i). */
  function bookPairs(bk, n) {
    const g = bk.grouped(settings.tick, n);
    return { bids: g.bids.map((r) => [r.price, r.qty]), asks: g.asks.map((r) => [r.price, r.qty]) };
  }
  /** A leg's quote asset (§4i same-quote merge): coinbase is BTC-USD, every
   *  other matrix leg is USDT — the excluded/summed split keys on this. */
  function legQuote(key) { return key === 'coinbase' ? 'USD' : 'USDT'; }

  /** Spread + imbalance readouts from a best-first book snapshot (§4i). */
  function ladderReads(bookObj, best, mid) {
    const tick = settings.tick;
    let spread = {}, tobImb = NaN;
    if (best.bid && best.ask) {
      const w = best.ask[0] - best.bid[0];
      // ticks against the DISPLAY grouping tick (the ladder's own grid) — a
      // sub-group spread reads 0t, so the dollar width rides alongside it (the
      // native venue tick is not carried here; bps + $ are grid-independent).
      spread = { ticks: Math.round(w / tick), usd: w, bps: Number.isFinite(mid) && mid > 0 ? (w / mid) * 1e4 : NaN };
      const bq = best.bid[1], aq = best.ask[1], t = bq + aq;
      tobImb = t > 0 ? (bq - aq) / t : NaN;
    }
    const di = S.depthImbalance(bookObj, mid, DOM_IMB_TICKS, tick);
    return { spread, tobImb, depthImb: { pct: di.pct, nTicks: DOM_IMB_TICKS } };
  }

  /** Source-selectable DOM ladder (§4h/§4i): full-book depth bars (ladderRows +
   *  log scaling) + rolling trade imprint + wall markers + spread/mid/imbalance
   *  readouts. Single-venue (default bybit·lin) OR the aggregated same-quote
   *  merge (mergeSameQuoteBooks — a DISPLAY approximation, loudly caveated).
   *  Imprint + walls are bybit-grid data and ride the SINGLE-venue source that
   *  owns them (another venue's book under them would blend venues, §0.7). */
  function renderDom() {
    const src = settings.domSource;
    const tick = settings.tick;
    const emptySlice = () => domView.render({ lad: { bids: [], asks: [] }, best: {}, mid: NaN, tickSize: tick, cumOn: settings.domCum, imprint: null, walls: [], nakedPocs: [] });

    if (src === '__agg') {
      // Aggregated same-quote (USDT) ladder — DISPLAY approximation (§4i).
      const booksByLeg = {}, legMeta = {};
      for (const key of LEG_KEYS) {
        if (!legReg.isEnabled(key)) continue;
        const b = aggBook.books.get(LEG_EX[key]);
        if (!b || (b.bids.size === 0 && b.asks.size === 0)) continue;   // no proven depth → not summed
        booksByLeg[key] = bookPairs(b, DOM_RAW_LEVELS);
        legMeta[key] = { quote: legQuote(key), tickSize: tick, primary: key === 'bybit_linear' };
      }
      const merged = S.mergeSameQuoteBooks(booksByLeg, legMeta);
      const mb = merged.book;
      if (!mb.bids.length && !mb.asks.length) {
        domNote.hidden = false;
        domNote.textContent = 'aggregated (same-quote USDT) — awaiting synced USDT books (nothing summed yet; nothing interpolated)';
        emptySlice();
        return;
      }
      const best = { bid: mb.bids[0] || null, ask: mb.asks[0] || null };
      const mid = (best.bid && best.ask) ? (best.bid[0] + best.ask[0]) / 2 : NaN;
      const reads = ladderReads(mb, best, mid);
      const inc = merged.includedLegs.map((k) => LEG_LABEL[k] || k).join(', ') || '—';
      const exc = merged.excludedLegs.map((k) => LEG_LABEL[k] || k).join(', ');
      domNote.hidden = false;
      domNote.textContent = 'AGGREGATED same-quote (USDT) depth — a DISPLAY APPROXIMATION, never a merged book (§4i): '
        + 'separate matching engines share no queue. Summed: ' + inc
        + (exc ? ' · excluded (non-USDT, not rescaled): ' + exc : '');
      domView.render({
        lad: S.ladderRows(mb, mid, tick, 14),
        best, mid, spread: reads.spread, tobImb: reads.tobImb, depthImb: reads.depthImb,
        imprint: null,   // cross-venue imprint would need a merge (§0.7) — off here
        walls: [], tickSize: tick, cumOn: settings.domCum, nakedPocs: [],
      });
      return;
    }

    // Single-venue source.
    const ex = src;
    const key = EX_LEG[ex];
    const isBybit = ex === 'bybit';
    const book = aggBook.books.get(ex);
    const hasDepth = !!book && (book.bids.size > 0 || book.asks.size > 0);
    if (!hasDepth) {
      domNote.hidden = false;
      domNote.textContent = (key && !legReg.isEnabled(key))
        ? (LEG_LABEL[key] || ex) + ' — disabled (settings); enable it from the legs menu (topbar)'
        : (LEG_LABEL[key] || ex) + ' — awaiting a synced book (no proven depth yet; nothing is interpolated)';
      emptySlice();
      return;
    }
    const raw = bookPairs(book, DOM_RAW_LEVELS);
    const best = book.best();
    const mid = (best.bid && best.ask) ? (best.bid[0] + best.ask[0]) / 2 : NaN;
    const reads = ladderReads(raw, best, mid);
    // Imprint + walls belong to the SINGLE venue that produced them: imprint is
    // this leg's own rolling flow; the walls ledger is bybit-grid (§4g), so it
    // rides the bybit·lin source only.
    const imprint = imprints[ex] ? imprints[ex].map() : null;
    const wallRows = isBybit ? walls.list().map((w) => ({ price: w.price, side: w.side, status: w.status })) : [];
    domNote.hidden = isBybit;
    if (!isBybit) domNote.textContent = 'source: ' + (LEG_LABEL[key] || ex)
      + ' book · full local depth (§4h, display windowed) · imprint = this leg’s rolling flow · walls ledger is bybit-only (hidden here, §0.7)';
    domView.render({
      lad: S.ladderRows(raw, mid, tick, 14),
      best, mid, spread: reads.spread, tobImb: reads.tobImb, depthImb: reads.depthImb,
      imprint, walls: wallRows, tickSize: tick, cumOn: settings.domCum,
      nakedPocs: (isBybit && levelsDrawOn) ? nakedPocs() : [],
    });
  }

  const tapeView = V.TapeView();
  const tapeMinInput = $('set-tape-min');
  const tapeMarketSel = $('set-tape-market');
  const tapeVenueSel = $('set-tape-venue');
  const tapeMetricSel = $('set-tape-metric');
  tapeMinInput.value = String(settings.tapeMin);
  tapeMarketSel.value = settings.tapeMarket;
  tapeVenueSel.value = settings.tapeVenue;
  tapeMetricSel.value = settings.tapeMetric;
  tapeView.mount($('view-tape'), {
    filterInput: tapeMinInput,   // the input lives in the settings row; the view owns its behavior
    onFilter: (v) => { settings.tapeMin = v; saveSettings(); dirty.tape = true; },
    marketSel: tapeMarketSel,
    onMarket: (v) => { if (TAPE_MARKETS.indexOf(v) >= 0) { settings.tapeMarket = v; saveSettings(); dirty.tape = true; } },
    venueSel: tapeVenueSel,
    onVenue: (v) => { if (TAPE_VENUES.indexOf(v) >= 0) { settings.tapeVenue = v; saveSettings(); dirty.tape = true; } },
    metricSel: tapeMetricSel,
    onMetric: (v) => { if (TAPE_METRICS.indexOf(v) >= 0) { settings.tapeMetric = v; saveSettings(); dirty.tape = true; } },
  });

  /** Compact USD for the tape honesty line ($100k / $1M / $5M). */
  function usdShort(x) {
    if (!Number.isFinite(x)) return '—';
    const a = Math.abs(x);
    if (a >= 1e9) return '$' + (a / 1e9).toFixed(2) + 'B';
    if (a >= 1e6) return '$' + (a / 1e6).toFixed(a >= 1e7 ? 0 : 1) + 'M';
    if (a >= 1e3) return '$' + (a / 1e3).toFixed(0) + 'k';
    return '$' + a.toFixed(0);
  }

  /** Which ENABLED trade legs feed the merged tape (honesty line, §4i):
   *  binance·fut is excluded — no futures trades flow on this network (§0.2),
   *  so it contributes nothing to the tape and listing it would overstate it. */
  function tapeLegsNote() {
    const legs = [];
    for (const key of LEG_KEYS) {
      if (key === 'binancef' || !legReg.isEnabled(key)) continue;
      legs.push(LEG_LABEL[key]);
    }
    const t = settings.tapeTiers;
    return 'merges ' + (legs.join(', ') || '— (no trade leg enabled)')
      + ' · tiers sig ≥ ' + usdShort(t.sig) + ' · large ≥ ' + usdShort(t.large)
      + ' · huge ≥ ' + usdShort(t.huge) + ' · whale ≥ ' + usdShort(t.whale)
      + ' — labeled display conventions, not signals (§4i)';
  }

  /** T-3 (§4i): build the aggregated-tape slice — the ONE TapeAggregator's
   *  rows filtered by market / single-venue / min-notional (each row is a
   *  single-venue block, so venue/market filtering is exact), tier-tagged with
   *  the active thresholds, plus the big-print rail and the honesty line. */
  function renderTape() {
    const tiers = settings.tapeTiers;
    // ex → market label: an ex with no leg key defaults perp (the primary legs
    // are perp; an unmapped code never masquerades as spot). filterTapeRows
    // owns the min-notional / single-venue / market filter + tier+market tag.
    const marketOf = (ex) => { const key = EX_LEG[ex]; return key && !legReg.isPerp(key) ? 'spot' : 'perp'; };
    const out = S.filterTapeRows(tapeAgg.list(), {
      market: settings.tapeMarket, venue: settings.tapeVenue, minN: settings.tapeMin,
      tiers, marketOf,
    });
    const rail = bigPrints.list().map((r) => Object.assign({}, r, {
      market: EX_LEG[r.ex] && legReg.isPerp(EX_LEG[r.ex]) ? 'perp' : 'spot',
    }));
    tapeView.render({
      rows: out, bigPrints: rail, tiers, tick: settings.tick,
      nowMs: Date.now(), legsNote: tapeLegsNote(), metric: settings.tapeMetric,
    });
  }

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

  // ── T-1 views (§4g) — store-fed, so they mount in BOTH modes (replay
  // drives them deterministically; REST-free by construction). ──
  const tapeIntView = V.TapeIntensityView();
  tapeIntView.mount($('view-tapeint'));
  const wallsView = V.WallsLedgerView();
  wallsView.mount($('view-walls'));
  const vpinView = V.VpinView();
  vpinView.mount($('view-vpin'));
  const basisView = V.BasisView();
  basisView.mount($('view-basis'));
  let klevDrawOn = settings.klevDraw;
  const klevView = V.KeyLevelsView();
  const klevDrawInput = $('set-klev-draw');
  klevDrawInput.checked = klevDrawOn;
  klevView.mount($('view-keylevels'), {
    drawInput: klevDrawInput,
    onDraw: (on) => { klevDrawOn = on; settings.klevDraw = on; saveSettings(); dirty.fp = true; },
  });

  // ─── Live legs: the 7-leg venue matrix (§4h) + one REST poller ──────────
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
  // N1 verification hook: ?fault=<panelKey> under ?replay=1 forces that ONE
  // panel to throw on every paint, to PROVE the paint-loop quarantine in the
  // browser (L1). DOUBLE-gated on REPLAY — inert in production: with no ?replay
  // this is null, and ?fault alone (REPLAY false) is null, so it can NEVER fire
  // on a live page. Consumed in safePanel().
  const FAULT_KEY = REPLAY ? new URLSearchParams(location.search).get('fault') : null;
  function startLeg(name, adapter, api) {
    // O-3 BYOD seam (§4c): sink rides along as the optional 4th arg — under
    // ?replay=byod the driver feeds collector rows to the sink DIRECTLY
    // (rows are already normalized; adapters bypassed); under ?replay=1 the
    // 4th arg is ignored and fixture replay is bit-for-bit unchanged.
    // Returns the socket handle ({close}) so a symbol switch can close the
    // leg (§4g); replay legs have no handle — switching is disabled there.
    if (REPLAY) { window.BTCQ_TERMINAL_REPLAY.drive(name, adapter, api, sink); return null; }
    return LW.makeSocket(adapter, api);
  }

  // ── T-2 (§4h): leg lifecycle — the 7-leg venue×market matrix ──
  //
  // startAllLegs derives per-leg ids for the CURRENT symbol (deriveLegIds)
  // and opens each ENABLED leg; the honest-degrade ladder per leg is:
  //   disabled in the registry → chip 'disabled (settings)', socket never
  //   opens (§4h — the freeze is user-chosen, amber not red);
  //   null mapping → 'no leg' chip (§4g naming rule);
  //   derived-but-unprobed id → listability probe first (T-1 probes extended
  //   to the spot legs — a guessed id would open a socket that never
  //   delivers and the watchdog would claim a transient outage forever);
  //   unknown okx SWAP ctVal → 'no leg' (§4b unit rail).
  // legGen guards async starts (probes, ctVal fetch, snapshot fetches)
  // against a switch that happened mid-flight.
  //
  // Book-sync legs (§4h): binance spot/fut diffs and the coinbase l2 feed go
  // adapter → ENGINE (terminal-books.js), not adapter → sink; flushBookLegs
  // below turns each SYNCED engine into ordinary §4 depth events. OKX books
  // are seq-chain-guarded in the adapter and emit depth directly (the CRC
  // engine has nothing to verify on today's wire — terminal-adapters.js T-2
  // header; the adapter requests a resubscribe on a chain break and the
  // flush performs it).
  const legHandles = {};   // legKey → socket handle ({close}) | null
  let restPoller = null;
  let legGen = 0;
  let bookLegs = {};       // legKey → engine-flush bookkeeping (see regBookLeg)
  let okxLegs = {};        // legKey → {adapter, restart, gen, lastRestartAt}
  let lastBookFlush = 0;

  // Per-leg async generation (§4h leg manager): the probe / ctVal /
  // continuation guards check BOTH counters — legGen catches a whole-matrix
  // restart (symbol switch), legGens[key] catches a single-leg toggle, so a
  // disable-then-enable mid-probe can never double-start one leg's socket
  // while leaving the other six untouched.
  const legGens = {};
  for (const k of LEG_KEYS) legGens[k] = 0;

  /** Start ONE matrix leg for the CURRENT symbol (§4h degrade ladder):
   *    disabled in the registry → chip 'disabled (settings)', socket never
   *    opens; null mapping → 'no leg' chip; derived-but-unprobed id →
   *    listability probe first; unknown okx SWAP ctVal → 'no leg'. */
  function startMatrixLeg(key) {
    const gen = legGen;
    const lgen = ++legGens[key];
    const ex = LEG_EX[key];
    const HIST_ = window.BTCQ_TERMINAL_HIST;
    // Registry + naming gate (§4h/§4g): honest chip, then stop.
    if (!legReg.isEnabled(key)) { chipStatus(ex)('stale', 'disabled (settings)'); return; }
    const id = S.deriveLegIds(SYM)[key];
    if (!id) { chipStatus(ex)('error', 'no leg for ' + SYM); return; }
    /** True when this start is obsolete: matrix restarted, THIS leg toggled
     *  again, or the user disabled it while an async step was in flight. */
    const dead = () => gen !== legGen || lgen !== legGens[key] || !legReg.isEnabled(key);

    /** Probe-then-start plumbing (T-1 rail, §4h "probes reused per leg"):
     *  pinned BTCUSDT ids (and replay, which forces BTCUSDT) start straight
     *  away; any other derived id asks the venue first. */
    function probed(probe, start) {
      if (SYM === 'BTCUSDT' || REPLAY) { start(); return; }
      chipStatus(ex)('stale', 'probing ' + id + '…');
      probe(id).then((listed) => {
        if (dead()) return;
        if (listed) start();
        else chipStatus(ex)('error', 'no leg for ' + SYM + (listed === false ? ' (not listed)' : ' (probe unreachable)'));
      });
    }

    /** Register a book-sync leg for flushBookLegs: wireTs is stamped by the
     *  tap wrappers below from each frame's OWN exchange ts, so the emitted
     *  depth events stay event-time-driven (replay rail — the 100ms flush
     *  clock is transport cadence, never a store clock). */
    function regBookLeg(eng, extra) {
      const bl = Object.assign(
        { key, ex, eng, gen, wireTs: NaN, lastEmitTs: NaN, snapBusy: false, lastSnapAt: 0 },
        extra);
      bookLegs[key] = bl;
      return bl;
    }
    const stampTs = (bl) => (ts) => { if (Number.isFinite(ts) && !(bl.wireTs >= ts)) bl.wireTs = ts; };
    // Engine-feed taps: adapters pass the frame's wire ts as the LAST arg
    // (terminal-adapters.js T-2 header); the engines ignore it — these
    // wrappers capture it for the flush and delegate untouched.
    function tapDiff(eng, bl) {
      const st = stampTs(bl);
      return { onDiff(ev, ts) { st(ts); eng.onDiff(ev); } };
    }
    function tapCoinbase(eng, bl) {
      const st = stampTs(bl);
      return {
        onSnapshot(b, a, ts) { st(ts); eng.onSnapshot(b, a, ts); },
        onL2Update(ch, ts) { st(ts); eng.onL2Update(ch, ts); },
      };
    }

    // ── okx legs — books (seq-chained) + trades, one socket each. ctVal
    // rail (§4b): SWAP sizes are CONTRACTS (0.01 pinned for BTC-USDT-SWAP,
    // fetched otherwise, leg skipped when unknown); SPOT sz is coin units. ──
    function startOkxLeg(ctVal) {
      const adapter = A.makeOkxBooksAdapter(id, sink, { ex, ctVal });
      legHandles[key] = startLeg(key === 'okx_swap' ? 'okx' : key, adapter, { onStatus: chipStatus(ex) });
      okxLegs[key] = {
        adapter, gen, key,
        lastRestartAt: okxLegs[key] ? okxLegs[key].lastRestartAt : 0,
        // Seq-gap remedy (§4h reality): a fresh socket → fresh books
        // snapshot → chain reset. The adapter only FLAGS (it never sees the
        // socket); the flush polls the flag and calls this.
        restart() {
          if (legHandles[key] && legHandles[key].close) legHandles[key].close();
          startOkxLeg(ctVal);
        },
      };
    }

    switch (key) {
      case 'bybit_linear':
        // PRIMARY leg (§2), path unchanged from T-1.
        legHandles.bybit_linear = startLeg('bybit', A.makeBybitAdapter(id, sink), { onStatus: chipStatus('bybit') });
        break;

      case 'bybit_spot':
        // Same venue family, spot endpoint (§4h).
        probed(HIST_.probeBybitSpotSymbol, () => {
          legHandles.bybit_spot = startLeg('bybit_spot', A.makeBybitSpotAdapter(id, sink), { onStatus: chipStatus('bybit_spot') });
        });
        break;

      case 'binancef':
        // Diff depth → BinanceBookSync futures + REST mark/OI.
        probed(HIST_.probeBinanceFutSymbol, () => {
          if (REPLAY) {
            // Replay keeps the PROVEN depth20 fixture leg: the diff engine can
            // only sync off a REST snapshot and replay forbids network — a
            // forever-buffering engine would leave an honest but empty book
            // where the fixtures can render a real one.
            legHandles.binancef = startLeg('binancef', A.makeBinanceDepthAdapter(id, sink), { onStatus: chipStatus('binancef') });
            return;
          }
          const eng = B.BinanceBookSync({ mode: 'futures' });   // pu-chain continuity (§4h)
          const bl = regBookLeg(eng, { market: 'futures', id });
          legHandles.binancef = startLeg('binancef',
            A.makeBinanceFutDepthDiff(id, { book: tapDiff(eng, bl) }), { onStatus: chipStatus('binancef') });
          // Trades stay ABSENT by wire reality (§0.2 topic filter; the
          // collector's REST aggTrades poller records them — never duplicated
          // here); mark/OI columns come from the REST poller as before.
          restPoller = A.makeBinanceRestPoller(id, sink);   // mark 5s / OI 60s → 'binancef' columns
          restPoller.start();
        });
        break;

      case 'binance_spot':
        // aggTrade + diff depth → BinanceBookSync spot (§4h).
        probed(HIST_.probeBinanceSpotSymbol, () => {
          const eng = B.BinanceBookSync({ mode: 'spot' });   // U ≤ lastId+1 ≤ u continuity
          const bl = regBookLeg(eng, { market: 'spot', id });
          legHandles.binance_spot = startLeg('binance_spot',
            A.makeBinanceSpotAdapter(id, sink, { book: tapDiff(eng, bl) }), { onStatus: chipStatus('binance_spot') });
          // In replay this engine never syncs (no REST) — trades still flow;
          // the flush simply never emits an unsynced book (honest absence).
        });
        break;

      case 'okx_swap':
        if (id === 'BTC-USDT-SWAP' || REPLAY) {
          startOkxLeg(0.01);
        } else {
          HIST_.fetchOkxCtVal(id).then((ctVal) => {
            if (dead()) return;
            if (ctVal === null) {
              chipStatus('okx')('error', 'no leg for ' + SYM + ' (ctVal unknown)');
              return;
            }
            startOkxLeg(ctVal);
          });
        }
        break;

      case 'okx_spot':
        probed(HIST_.probeOkxSpotInst, () => startOkxLeg(1));
        break;

      case 'coinbase':
        // Exchange-feed l2 book + matches tape (§4h). No sequence number
        // exists on level2_batch (engine header): the continuity rail is
        // liveness — a stalled channel stops marking alive, makeSocket's
        // watchdog reconnects, and the resubscribe's fresh >1MB snapshot
        // replaces the book wholesale.
        probed(HIST_.probeCoinbaseProduct, () => {
          const eng = B.CoinbaseBookSync();
          const bl = regBookLeg(eng, {});
          legHandles.coinbase = startLeg('coinbase',
            A.makeCoinbaseL2Adapter(id, sink, { book: tapCoinbase(eng, bl) }), { onStatus: chipStatus('coinbase') });
        });
        break;

      default:
        break;   // unknown key = registry/lifecycle drift — start nothing
    }
  }

  function startAllLegs() {
    legGen++;
    bookLegs = {};
    okxLegs = {};
    const ids = S.deriveLegIds(SYM);
    SPOT = ids.coinbase;
    OKX_INST = ids.okx_swap;
    for (const key of LEG_KEYS) startMatrixLeg(key);
  }

  /** Stop ONE leg (§4h leg manager): close the socket, drop its flush /
   *  restart bookkeeping, bump its per-leg gen so in-flight async starts
   *  die. The engine object goes out of scope; the leg's last depth stays
   *  frozen in aggBook (never interpolated) but the aggIncludeExs enabled-gate
   *  drops it from the merge — honest absence on-canvas, no stale-as-live. */
  function stopLeg(key) {
    legGens[key]++;
    if (legHandles[key] && legHandles[key].close) legHandles[key].close();
    legHandles[key] = null;
    delete bookLegs[key];
    delete okxLegs[key];
    // The REST poller is the binancef leg's other half (mark/OI columns).
    if (key === 'binancef' && restPoller) { restPoller.stop(); restPoller = null; }
  }

  function stopAllLegs() {
    legGen++;   // invalidate any in-flight async leg start / snapshot fetch
    for (const key in legHandles) {
      if (legHandles[key] && legHandles[key].close) legHandles[key].close();
      legHandles[key] = null;
    }
    bookLegs = {};
    okxLegs = {};
    if (restPoller) { restPoller.stop(); restPoller = null; }
  }

  /** THE leg toggle (§4h): registry flip → persist → per-leg lifecycle.
   *  Disabling closes the socket NOW and the chip states the honest reason;
   *  panels freeze at their last real data (no interpolation). Disabled in
   *  replay — the deterministic harness drives all legs (sym-btn rule). */
  function setLegEnabled(key, on) {
    if (REPLAY) return;
    if (!legReg.setEnabled(key, on)) return;   // no real transition → no restart
    settings.legs = legReg.enabledMap();
    saveSettings();
    if (on) {
      startMatrixLeg(key);
    } else {
      stopLeg(key);
      chipStatus(LEG_EX[key])('stale', 'disabled (settings)');
    }
    dirty.header = true; dirty.agg = true; dirty.spcvd = true;
    renderLegRows();
  }

  // ── T-2 (§4h): book-engine flush — the 100ms depth tick for the engine
  // legs. Turns each SYNCED engine book into the EXISTING per-venue depth
  // event (topN ladder, isSnapshot:true — the wholesale-replace semantics
  // BookStore already speaks for binance depth20), at the same ~100ms cadence
  // the wire legs deliver, so the agg book / samplers / paint gates see
  // nothing new. The 100ms gate is WALL clock on purpose: it replaces the
  // transport's delivery cadence (livewire territory), while every emitted
  // event carries the leg's newest WIRE ts — stores stay event-time-driven
  // (replay rail). No new wire frames → no emit (a re-emitted unchanged book
  // would fabricate liveness). Runs from frame() with the other ingestion-
  // side work: never gated by pause/visibility (§4e.2 — paint gates skip
  // PAINT, never data).
  const BOOK_EMIT_LEVELS = 200;   // bybit-book parity; bounds the per-emit copy
  function flushBookLegs(now) {
    if (now - lastBookFlush < 100) return;
    lastBookFlush = now;
    for (const key in bookLegs) {
      const bl = bookLegs[key];
      if (bl.gen !== legGen) continue;
      const eng = bl.eng;
      // Binance snapshot loop (live only — REST is forbidden in replay): the
      // official algo is stream-first, snapshot-on-demand; a 2s floor between
      // attempts retries a failing endpoint politely instead of hammering it.
      if (!REPLAY && bl.market && eng.needsSnapshot() && !bl.snapBusy && now - bl.lastSnapAt >= 2000) {
        bl.snapBusy = true;
        bl.lastSnapAt = now;
        window.BTCQ_TERMINAL_HIST.fetchBinanceDepthSnapshot(bl.market, bl.id).then((snap) => {
          bl.snapBusy = false;
          if (bl.gen !== legGen) return;   // symbol switched mid-fetch
          if (snap) eng.onSnapshot(snap.lastUpdateId, snap.bids, snap.asks);
          // null → transient REST failure; the next flush tick retries (§4c idiom)
        });
      }
      if (eng.needsSnapshot && eng.needsSnapshot()) continue;   // unsynced: nothing honest to emit
      const ts = bl.wireTs;
      if (!Number.isFinite(ts) || ts === bl.lastEmitTs) continue;   // no new frames → no fabricated tick
      // topN sorts the engine's whole side maps (its stated materialization
      // cost) — bounded at 10 Hz by this gate. Coinbase is the heavy case
      // (~44k levels): a few ms/s, accepted; the emitted event itself carries
      // only the top-200 window downstream.
      const top = eng.topN(BOOK_EMIT_LEVELS);
      if (!top.bids.length && !top.asks.length) continue;   // coinbase pre-snapshot
      bl.lastEmitTs = ts;
      sink({ kind: 'depth', ex: bl.ex, ts, bids: top.bids, asks: top.asks, isSnapshot: true });
    }
    // OKX seq-gap restarts (live only; replay chains are intact by capture):
    // the adapter halted its book on a broken chain (§0.7) and flagged — a
    // fresh subscribe delivers a fresh snapshot. 2s floor against thrash.
    if (!REPLAY) {
      for (const key in okxLegs) {
        const ol = okxLegs[key];
        if (ol.gen !== legGen) continue;
        if (ol.adapter.bookGapped() && now - ol.lastRestartAt >= 2000) {
          ol.lastRestartAt = now;
          ol.restart();   // replaces okxLegs[key], carrying lastRestartAt over
        }
      }
    }
  }

  startAllLegs();

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
  let restRefresh = null;   // T-1 (§4g): re-fetches the SYM-parameterized REST panels on a symbol switch
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
      const el = $(id);
      el.innerHTML = NOTE;
      // Presentation only: a note-only panel renders compact (.panel--empty,
      // terminal.css) — the honest note stays visible, just not full-height.
      const panel = el.closest('.panel');
      if (panel) panel.classList.add('panel--empty');
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

    // T-1 (§4g): a symbol switch re-fetches every SYM-parameterized REST
    // panel; caches are dropped FIRST so the old symbol's bars never render
    // under the new label while the fetch is in flight.
    restRefresh = () => {
      histBars = null; vpData = null; tpoSessions = null;
      dirty.hist = true; dirty.vp = true; dirty.tpo = true;
      refreshHist();
      refreshTpo();
      pollOkx();
    };

    // ── OKX funding/OI: NEW 60s REST poll (§4c FundingArbView leg). A null
    // result simply keeps the previous value's staleness visible via the
    // countdown / leaves '—' — silent-null tolerated by contract. §4g: no
    // derivable okx instId → the cells go honestly absent, never stale. ──
    function pollOkx() {
      if (!OKX_INST) { okxFund = null; okxOiEv = null; dirty.farb = true; return; }
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
        // §4g: with another symbol selected the bybit mark ISN'T BTC — the
        // BTC session leg simply stops sampling (a gap, never a mislabel).
        if (SYM === 'BTCUSDT' && marks.bybit && Number.isFinite(marks.bybit.mark)) sessStore.onSample(now, 'BTC', marks.bybit.mark);
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
  let alertEngine = S.AlertEngine({ rules: engineRules(), cooldownMs: 60000 });   // let: rebuilt on symbol switch (honest restart)

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
      const el = $(id);
      el.innerHTML = NOTE4;
      // Presentation only: note-only panel → compact (.panel--empty).
      const panel = el.closest('.panel');
      if (panel) panel.classList.add('panel--empty');
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
          // T-1 (§4g): first universe answer — derive the persisted symbol's
          // real tick options from its price (boot had no price to derive
          // from; the user's persisted tick is kept).
          if (!tickDerived && btcTicker) {
            tickDerived = true;
            applyTickOptions(btcTicker.last, true);
          }
          renderSymList();   // refresh the picker/universe-fed lists in place
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
      const el = $(id);
      el.innerHTML = NOTE5;
      // Presentation only: note-only panel → compact (.panel--empty).
      const panel = el.closest('.panel');
      if (panel) panel.classList.add('panel--empty');
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

  // ─── I-1 AUCTION section (§4f): auction profile / levels registry /
  // microstructure + hist-chart VWAP bands & levels overlay ────────────────
  //
  // Transport rules (§4f wiring contract):
  //   - /v1/profile + /v1/levels + /v1/vwap are LIVE-LOCAL collector reads
  //     (127.0.0.1:8788) — fetched ONLY outside replay modes AND only after a
  //     one-shot /health probe answers. On the public Pages deployment the
  //     page is https and the browser BLOCKS http://127.0.0.1 as mixed
  //     content — the probe's fetch rejects, apiUp goes false, and the panels
  //     degrade to the honest 'collector API offline' note instead of
  //     spinning forever (stated here because it is the deployment's normal
  //     state, not an error).
  //   - Archived days are read straight from the public HF dataset
  //     (terminal-hfdata.js, CORS-proven §4f) — ALWAYS behind a size-warning
  //     confirm() that states the byte cost BEFORE the download (the WhaleView
  //     33 MB leaderboard gate idiom; trades ≈ 27 MiB/day), and every render
  //     is labeled 'archived day · hf dataset'.
  //   - Poll cadences: profile on-demand + 60s refresh for 'today'; levels
  //     5min; vwap piggybacks the live trade stream (AnchoredVwap — no
  //     polling; ONE /v1/vwap fetch per anchor change as a labeled parity
  //     read).
  //   - Replay modes: every I-1 panel shows an honest disabled note — the
  //     endpoints are live-local and the microstructure panes read the live
  //     book sampler; neither has a deterministic replay story (§7 L1).
  const BYOD_API = 'http://127.0.0.1:8788';   // collector.py --api-port default (terminal-replay.js pins the same)
  const HFD = window.BTCQ_TERMINAL_HFDATA || null;   // absent ⇒ archived days simply aren't offered
  const LEVELS_TICK = 10;                      // §4f server default ($10 levels — the BTC-perp contract example)
  const AUCTION_BUCKETS = [10000, 100000];     // → b0 ≤$10k · b1 ≤$100k · b2 >$100k (CVD bucket family)
  const AUCTION_BUCKET_LABELS = ['≤$10k', '≤$100k', '>$100k'];
  const DAY_MS = 86400000;
  const API_OFFLINE_NOTE = 'collector API offline — run `make collector-api` (127.0.0.1:8788). '
    + 'On the public Pages deployment this is expected: an https page cannot fetch http://127.0.0.1 (mixed content).';
  // T-1 (§4g): the recorded store holds BTCUSDT only — every store-backed
  // panel says so (compact honest note) while another symbol is selected.
  const byodSymNote = () => 'collector records BTCUSDT only (§4g) — recorded-store data resumes on BTCUSDT; '
    + 'nothing is fabricated for ' + SYM + '.';

  let auctionView = null, levelsView = null, microView = null;
  let apiUp = null;             // null = probing, false = offline, true = answering
  let levelsDays = null;        // /v1/levels days (date-ascending) | null before first answer
  let levelsNote = 'awaiting /v1/levels (collector API probe in flight)…';
  let archDates = null;         // HF archived dates (ascending) | null
  let auctionSource = 'today';
  let auctionComposite = { on: false, days: [] };
  let auctionState = { profile: null, delta: null, label: '', note: 'probing collector API…', status: '' };
  let auctionGen = 0;           // load generation — a newer source pick abandons stale completions
  const profCache = new Map();  // 'YYYY-MM-DD' → per-day profile (local fetch or hf aggregation)
  let levelsDrawOn = settings.levelsDraw;
  let vwapOn = settings.vwapOn;

  function utcDayStart(ts) { return Math.floor(ts / DAY_MS) * DAY_MS; }
  function dateStr(ts) { return new Date(ts).toISOString().slice(0, 10); }

  /** Abortable JSON GET (terminal-hist.js idiom, but THROWING — these loads
   *  are user-visible and the failure text is the render). 410 = the §4f
   *  'archived to HF' answer: surfaced with the server's own hint. */
  function fetchJson(url, timeoutMs) {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), timeoutMs || 10000);
    return fetch(url, { signal: ctl.signal }).then((r) => {
      clearTimeout(t);
      if (r.status === 410) {
        return r.json().catch(() => ({})).then((j) => {
          const e = new Error(j && j.hint ? String(j.hint) : 'day file archived (410)');
          e.gone = true;
          throw e;
        });
      }
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    }, (err) => { clearTimeout(t); throw err; });
  }

  /** ProfileStore-parity 70% value-area expansion over §4f profile levels
   *  (weight = buy+sell). Mirrors ProfileStore.profile() / collector _poc_va
   *  step for step — ONE convention everywhere (§4f binding): expand from
   *  the max-volume level (ties → lowest price), absorb the bigger single
   *  neighbor (ties expand upward), until ≥70% of total volume is inside. */
  function pocVa70(levels) {
    if (!levels.length) return { poc: NaN, vah: NaN, val: NaN };
    let pocIdx = 0, total = 0;
    const wt = levels.map((lv) => lv.buy_vol + lv.sell_vol);
    for (let i = 0; i < levels.length; i++) {
      total += wt[i];
      if (wt[i] > wt[pocIdx]) pocIdx = i;
    }
    const target = 0.7 * total;
    let covered = wt[pocIdx];
    let up = pocIdx + 1, dn = pocIdx - 1;
    while (covered < target && (up < levels.length || dn >= 0)) {
      const vu = up < levels.length ? wt[up] : -1;
      const vd = dn >= 0 ? wt[dn] : -1;
      if (vu >= vd) { covered += vu; up++; }
      else { covered += vd; dn--; }
    }
    return { poc: levels[pocIdx].lvl, vah: levels[up - 1].lvl, val: levels[dn + 1].lvl };
  }

  /** Client-side aggregation of ARCHIVED trade rows (BYOD row shapes from
   *  terminal-hfdata.js) into the /v1/profile response shape — same floor
   *  bucketing, same notional size-buckets, same VA convention (pocVa70),
   *  vwap/σ via the weighted-Welford update (AnchoredVwap's precision
   *  argument: naive Σqp² cancels catastrophically at 1e5-scale prices). */
  function aggregateTradeRows(rows, tick, bucketsUsd) {
    const acc = new Map();
    let W = 0, mean = 0, Sw = 0, totalVol = 0;
    for (const r of rows) {
      if (!r || r.exchange !== 'bybit' || r.symbol !== SYM) continue;   // one leg, like the endpoint
      const price = r.price, qty = r.qty;
      if (!Number.isFinite(price) || !(qty > 0)) continue;
      const lvl = Math.floor(price / tick) * tick;
      let e = acc.get(lvl);
      if (!e) { e = { buy: 0, sell: 0, prints: 0, bk: new Array(bucketsUsd.length + 1).fill(0) }; acc.set(lvl, e); }
      if (r.aggressor_buy) e.buy += qty; else e.sell += qty;
      e.prints++;
      const ntl = price * qty;
      let bi = bucketsUsd.length;
      for (let i = 0; i < bucketsUsd.length; i++) { if (ntl <= bucketsUsd[i]) { bi = i; break; } }
      e.bk[bi] += qty;
      totalVol += qty;
      W += qty;
      const d = price - mean;
      mean += (qty / W) * d;
      Sw += qty * d * (price - mean);
    }
    const levels = [...acc.entries()].map(([lvl, e]) => {
      const row = { lvl, buy_vol: e.buy, sell_vol: e.sell, prints: e.prints };
      for (let i = 0; i < e.bk.length; i++) row['b' + i] = e.bk[i];
      return row;
    }).sort((a, b) => a.lvl - b.lvl);
    const va = pocVa70(levels);
    return {
      levels, poc: va.poc, vah: va.vah, val: va.val, total_vol: totalVol,
      vwap: W > 0 ? mean : null, sigma: W > 0 ? Math.sqrt(Math.max(0, Sw / W)) : null,
    };
  }

  /** Merge several per-day profiles into one composite (client-side, §4f):
   *  level sums are EXACT (disjoint trade sets); POC/VA recomputed with the
   *  one shared convention. vwap/σ are deliberately OMITTED — recomputing
   *  them from $10 level midpoints would approximate and present it as the
   *  measured value (§0.7); the per-day profiles keep theirs. */
  function mergeProfiles(profs) {
    const acc = new Map();
    let totalVol = 0;
    for (const p of profs) {
      for (const lv of p.levels) {
        let e = acc.get(lv.lvl);
        if (!e) { e = { buy: 0, sell: 0, prints: 0, bk: new Array(AUCTION_BUCKETS.length + 1).fill(0) }; acc.set(lv.lvl, e); }
        e.buy += lv.buy_vol; e.sell += lv.sell_vol; e.prints += lv.prints || 0;
        for (let i = 0; i < e.bk.length; i++) e.bk[i] += Number.isFinite(lv['b' + i]) ? lv['b' + i] : 0;
        totalVol += lv.buy_vol + lv.sell_vol;
      }
    }
    const levels = [...acc.entries()].map(([lvl, e]) => {
      const row = { lvl, buy_vol: e.buy, sell_vol: e.sell, prints: e.prints };
      for (let i = 0; i < e.bk.length; i++) row['b' + i] = e.bk[i];
      return row;
    }).sort((a, b) => a.lvl - b.lvl);
    const va = pocVa70(levels);
    return { levels, poc: va.poc, vah: va.vah, val: va.val, total_vol: totalVol, vwap: null, sigma: null };
  }

  function setAuctionProfile(prof, label, note) {
    auctionState = {
      profile: prof,
      // delta precomputed HERE via the tested pure builder (Σdelta ≡ Σbuy−Σsell
      // stays in the check_terminal-covered layer, the view only paints it).
      delta: prof ? S.buildDeltaProfile(prof.levels) : null,
      label, note: note || '', status: '',
    };
    dirty.auct = true;
  }
  function setAuctionStatus(txt) {
    auctionState.status = txt || '';
    dirty.auct = true;
  }

  /** Naked POCs for overlays (auction panel always; ladder/hist behind the
   *  LevelsView toggle) — age in days from the row's UTC date to today. */
  function nakedPocs() {
    // §4g: registry rows are recorded BTCUSDT days — never overlaid on
    // another symbol's panels (that would mislabel BTC levels as its own).
    if (SYM !== 'BTCUSDT' || !levelsDays) return [];
    const today0 = utcDayStart(Date.now());
    const out = [];
    for (const d of levelsDays) {
      if (!d || d.naked !== true || !Number.isFinite(d.poc)) continue;
      const dayMs = Date.parse(String(d.date) + 'T00:00:00Z');
      out.push({
        price: d.poc, date: String(d.date),
        ageDays: Number.isFinite(dayMs) ? Math.max(0, Math.round((today0 - dayMs) / DAY_MS)) : 0,
      });
    }
    return out;
  }

  /** Hist-chart levels overlay (LevelsView 'draw on charts'): prior recorded
   *  day's POC/VA + every naked POC (cap applied in the view). */
  function overlayLevels() {
    if (SYM !== 'BTCUSDT') return null;   // §4g: BTC registry levels never draw over another symbol's chart
    if (!levelsDrawOn || !levelsDays || !levelsDays.length) return null;
    const out = [];
    const prior = levelsDays[levelsDays.length - 1];   // newest recorded (closed) day
    if (Number.isFinite(prior.poc)) out.push({ price: prior.poc, label: 'pPOC ' + String(prior.date).slice(5), kind: 'poc' });
    if (Number.isFinite(prior.vah)) out.push({ price: prior.vah, label: 'pVAH', kind: 'va' });
    if (Number.isFinite(prior.val)) out.push({ price: prior.val, label: 'pVAL', kind: 'va' });
    for (let i = levelsDays.length - 2; i >= 0; i--) {   // prior day already drawn as pPOC
      const d = levelsDays[i];
      if (!d || d.naked !== true || !Number.isFinite(d.poc)) continue;
      out.push({ price: d.poc, label: 'nPOC ' + String(d.date).slice(5), kind: 'naked' });
    }
    return out;
  }

  /** Book-heatmap session boxes: SessionClock.boxesFor over every UTC day the
   *  sample ring touches (pure arithmetic — works in replay too, §4f). */
  function sessionBoxes(samples) {
    if (!samples || !samples.length) return [];
    const d0 = Math.floor(samples[0].ts / DAY_MS);
    const d1 = Math.floor(samples[samples.length - 1].ts / DAY_MS);
    const out = [];
    for (let d = d0; d <= d1 && out.length < 30; d++) {
      for (const b of sessionClock.boxesFor(d * DAY_MS)) out.push(b);
    }
    return out;
  }

  // ── VWAP anchor + slices (hist chart) ──
  function vwapAnchorMs() {
    const base = Number.isFinite(lastBybitTs) ? lastBybitTs : Date.now();
    if (settings.vwapAnchor === 'week') {
      const day0 = utcDayStart(base);
      const dow = (new Date(day0).getUTCDay() + 6) % 7;   // Mon = 0 (ISO week, UTC)
      return day0 - dow * DAY_MS;
    }
    if (settings.vwapAnchor === 'custom') {
      const el = $('set-vwap-ts');
      // datetime-local has no zone — parsed as UTC by appending 'Z' (the
      // input's title says UTC; guessing the browser zone would mislabel).
      const t = el && el.value ? Date.parse(el.value + 'Z') : NaN;
      if (Number.isFinite(t)) return t;
    }
    return utcDayStart(base);
  }

  const VWAP_ANCHOR_LABEL = { day: 'day · UTC 00:00', week: 'week · UTC Mon', custom: 'custom · UTC' };
  function applyVwapAnchor() {
    const ts = vwapAnchorMs();
    anchoredVwap.reset(ts);
    vwapHist = [];
    storeVwapTxt = null;
    dirty.hist = true;
    // ONE parity fetch per anchor change (§4f: no vwap polling) — the
    // recorded store's tick-exact answer over the FULL anchor range, shown
    // as labeled legend text, never merged into the live series (§0.7).
    // §4g: BTCUSDT only — the store has no other symbol to answer with.
    if (apiUp === true && !REPLAY && SYM === 'BTCUSDT') {
      fetchJson(BYOD_API + '/v1/vwap?symbol=' + SYM + '&exchange=bybit&anchor_ms=' + ts, 15000)
        .then((r) => {
          if (r && Number.isFinite(r.vwap)) {
            storeVwapTxt = '$' + r.vwap.toFixed(1)
              + (Number.isFinite(r.sigma) ? ' ± ' + r.sigma.toFixed(1) : '')
              + ' · n=' + (r.n || 0) + ' recorded trades';
            dirty.hist = true;
          }
        })
        .catch(() => { /* parity read is optional garnish — the live bands stand alone */ });
    }
  }

  function vwapSlice() {
    if (!vwapOn || !vwapHist.length) return null;
    const IV_MS = { 5: 300000, 30: 1800000, 60: 3600000, 240: 14400000, D: DAY_MS };
    const b = anchoredVwap.bands();
    return {
      points: vwapHist,
      intervalMs: IV_MS[histInterval] || 3600000,
      anchorLabel: VWAP_ANCHOR_LABEL[settings.vwapAnchor] || 'day',
      n: b.n,
      storeTxt: storeVwapTxt,
    };
  }

  // ── Auction source loading ──
  function profileUrl(startMs, endMs) {
    return BYOD_API + '/v1/profile?symbol=' + SYM + '&exchange=bybit&start_ms=' + startMs
      + '&end_ms=' + endMs + '&tick=' + LEVELS_TICK + '&buckets_usd=' + AUCTION_BUCKETS.join(',');
  }

  /** Failure text with the one actionable special case named: HTTP 404 from
   *  a /health-answering API means the RUNNING daemon predates the §4f
   *  endpoints — the fix is a restart, and the note should say so. */
  function apiErrText(e) {
    return e.message + (String(e.message) === 'HTTP 404'
      ? ' — the running collector API predates I-1; restart `make collector-api`' : '');
  }

  /** Is `date` still served by the LOCAL store? Rotation keeps today +
   *  yesterday; older days answer 410 with the HF hint (§3c). */
  function isLocalDate(date) {
    return date >= dateStr(Date.now() - DAY_MS);
  }

  /** One recorded day's profile (cache → local API → throw). Archived days
   *  are NEVER auto-downloaded here — the size gate must be a user click
   *  (loadArchivedDay), so composite merges only what is already consented. */
  function ensureDayProfile(date) {
    if (profCache.has(date)) return Promise.resolve(profCache.get(date));
    if (!isLocalDate(date)) {
      return Promise.reject(new Error(date + ' is archived — load it once via the source select (size-gated) first'));
    }
    const d0 = Date.parse(date + 'T00:00:00Z');
    return fetchJson(profileUrl(d0, d0 + DAY_MS), 20000).then((p) => {
      profCache.set(date, p);
      return p;
    });
  }

  function loadArchivedDay(date, gen) {
    if (profCache.has(date)) {
      setAuctionProfile(profCache.get(date), date + ' · archived day · hf dataset', '');
      return;
    }
    if (!HFD) {
      setAuctionProfile(null, '', 'terminal-hfdata.js not loaded — archived days unavailable');
      return;
    }
    setAuctionStatus('checking archive size…');
    HFD.listArchivedTables(HFD.DEFAULT_REPO, date).then((tabs) => {
      if (gen !== auctionGen) return;
      const tr = (tabs || []).find((t) => t.table === 'trades');
      const mib = tr && Number.isFinite(tr.bytes) ? (tr.bytes / 1048576).toFixed(1) : '?';
      // §4f honest size gate (WhaleView idiom): the cost is IN the dialog at
      // the moment of consent, not just implied by a label.
      if (!window.confirm('Download the FULL trades parquet for ' + date + ' from the HF dataset now?\n\n'
        + 'This is a ~' + mib + ' MiB one-shot transfer (whole file by design — one signed redirect, '
        + 'byte-true progress; ~2.8M rows parse in <1s). The day is then cached for this session.')) {
        setAuctionStatus('');
        setAuctionProfile(null, '', date + ' not loaded — download declined (nothing fetched)');
        return;
      }
      return HFD.fetchArchivedTable(HFD.DEFAULT_REPO, date, 'trades', {
        columns: ['exchange', 'symbol', 'ts_ms', 'price', 'qty', 'aggressor_buy'],
        onProgress: (pr) => {
          if (gen !== auctionGen) return;
          if (pr.phase === 'download') {
            setAuctionStatus('downloading ' + (pr.received / 1048576).toFixed(1)
              + (Number.isFinite(pr.total) ? ' / ' + (pr.total / 1048576).toFixed(1) : '') + ' MiB…');
          } else {
            setAuctionStatus('parsed ' + pr.rows + ' rows in ' + Math.round(pr.ms) + ' ms — aggregating…');
          }
        },
      }).then((rows) => {
        if (gen !== auctionGen) return;
        const prof = aggregateTradeRows(rows, LEVELS_TICK, AUCTION_BUCKETS);
        profCache.set(date, prof);
        setAuctionProfile(prof, date + ' · archived day · hf dataset', '');
      });
    }).catch((e) => {
      if (gen !== auctionGen) return;
      setAuctionProfile(null, '', 'archived load failed: ' + e.message);
    });
  }

  function loadComposite() {
    const gen = ++auctionGen;
    const days = auctionComposite.days;
    if (days.length < 2) {
      setAuctionProfile(null, '', 'composite: pick ≥ 2 recorded days in the multi-select');
      return;
    }
    setAuctionStatus('merging ' + days.length + ' days…');
    Promise.all(days.map((d) => ensureDayProfile(d).then(
      (p) => ({ date: d, prof: p }),
      (e) => ({ date: d, err: e.message })
    ))).then((results) => {
      if (gen !== auctionGen) return;
      const ok = results.filter((r) => r.prof);
      const skipped = results.filter((r) => r.err);
      if (!ok.length) {
        setAuctionProfile(null, '', 'composite: no day loadable — ' + skipped.map((r) => r.err).join(' · '));
        return;
      }
      const merged = mergeProfiles(ok.map((r) => r.prof));
      setAuctionProfile(merged,
        'composite · ' + ok.map((r) => r.date).join(' + ') + ' (client-side merge)',
        skipped.length ? skipped.length + ' day(s) skipped: ' + skipped.map((r) => r.err).join(' · ') : '');
    });
  }

  function loadAuctionSource(src) {
    const gen = ++auctionGen;
    // §4g symbol gate FIRST (before composite): no recorded bytes exist for
    // another symbol — the panel states it instead of fetching a mislabel.
    if (SYM !== 'BTCUSDT') { setAuctionProfile(null, '', byodSymNote()); return; }
    if (auctionComposite.on) { loadComposite(); return; }
    if (src === 'today') {
      if (apiUp !== true) {
        setAuctionProfile(null, '', apiUp === false ? API_OFFLINE_NOTE : 'probing collector API…');
        return;
      }
      const now = Date.now();
      fetchJson(profileUrl(utcDayStart(now), now), 20000).then((p) => {
        if (gen !== auctionGen) return;
        setAuctionProfile(p, 'today (UTC) · live-local store · refreshes 60s', '');
      }).catch((e) => {
        if (gen !== auctionGen) return;
        setAuctionProfile(null, '', 'today profile failed: ' + apiErrText(e));
      });
      return;
    }
    if (isLocalDate(src) && apiUp === true) {
      ensureDayProfile(src).then((p) => {
        if (gen !== auctionGen) return;
        setAuctionProfile(p, src + ' · local day file', '');
      }).catch((e) => {
        if (gen !== auctionGen) return;
        // 410 = rotated out; fall through to the archive when it exists there.
        if (e.gone && archDates && archDates.indexOf(src) >= 0) loadArchivedDay(src, gen);
        else setAuctionProfile(null, '', src + ': ' + apiErrText(e));
      });
      return;
    }
    if (archDates && archDates.indexOf(src) >= 0) { loadArchivedDay(src, gen); return; }
    setAuctionProfile(null, '', apiUp === false ? API_OFFLINE_NOTE : src + ': not local and not in the HF archive listing');
  }

  /** (Re)build the source select + composite multi-select: 'today' + the
   *  union of registry days and archived days, newest first, each labeled by
   *  where its bytes would come from. Selection preserved across rebuilds. */
  function rebuildAuctionSources() {
    const sel = $('set-auction-src'), daysSel = $('set-auction-days');
    if (!sel || !daysSel) return;
    const dates = new Set();
    if (levelsDays) for (const d of levelsDays) if (d && d.date) dates.add(String(d.date));
    if (archDates) for (const d of archDates) dates.add(String(d));
    const sorted = [...dates].sort().reverse();
    const opt = (v, label) => '<option value="' + v + '">' + label + '</option>';
    let html = opt('today', 'today (live BYOD)');
    let dhtml = '';
    for (const d of sorted) {
      const local = isLocalDate(d);
      const arch = archDates && archDates.indexOf(d) >= 0;
      html += opt(d, d + (local ? ' · local' : arch ? ' · hf archive' : ' · registry only'));
      dhtml += opt(d, d + (local ? ' · local' : ' · needs prior hf load'));
    }
    const keepSrc = auctionSource, keepDays = auctionComposite.days;
    sel.innerHTML = html;
    sel.value = [...sel.options].some((o) => o.value === keepSrc) ? keepSrc : 'today';
    daysSel.innerHTML = dhtml;
    for (const o of daysSel.options) o.selected = keepDays.indexOf(o.value) >= 0;
  }

  function pollLevels() {
    if (SYM !== 'BTCUSDT') return;   // §4g: the registry is recorded BTCUSDT days — nothing to poll for
    fetchJson(BYOD_API + '/v1/levels', 10000).then((r) => {
      levelsDays = r && Array.isArray(r.days) ? r.days : [];
      levelsNote = '';
      rebuildAuctionSources();
      dirty.lvls = true; dirty.hist = true; dirty.dom = true; dirty.auct = true;
    }).catch((e) => {
      // Name the failure — a daemon started before I-1 answers 404 here even
      // though /health passes; 'probe in flight' would be a lie at that point.
      levelsNote = '/v1/levels failed: ' + e.message
        + ' — if the collector daemon predates I-1, restart `make collector-api` to pick up the endpoint';
      dirty.lvls = true;
    });
  }

  if (REPLAY) {
    // Honest replay note (§4f wiring rule — same text discipline as O-3/O-4/
    // O-5): the collector endpoints are live-local and the microstructure
    // panes read the live book sampler; neither belongs in the deterministic
    // fixture harness, so the panels say why they are empty instead of
    // mounting blank canvases.
    const NOTE_I1 = '<div class="chart-na">auction suite disabled in replay — /v1/profile · /v1/levels · '
      + '/v1/vwap are live-local collector endpoints (make collector-api) and the microstructure panes read '
      + 'the live book sampler; the deterministic L1 harness allows no network beyond the fixture file. '
      + 'Nothing is fabricated to fill these panels (§0.7).</div>';
    for (const id of ['view-auction', 'view-levels', 'view-micro']) {
      const el = $(id);
      el.innerHTML = NOTE_I1;
      // Presentation only: note-only panel → compact (.panel--empty).
      const panel = el.closest('.panel');
      if (panel) panel.classList.add('panel--empty');
    }
    for (const id of ['set-auction-src', 'set-auction-mode', 'set-auction-comp', 'set-auction-days',
      'set-levels-draw', 'set-vwap-on', 'set-vwap-anchor', 'set-vwap-ts']) {
      $(id).disabled = true;
    }
  } else {
    auctionView = V.AuctionProfileView();
    auctionView.mount($('view-auction'), {
      modeSel: $('set-auction-mode'),
      srcSel: $('set-auction-src'),
      compInput: $('set-auction-comp'),
      daysSel: $('set-auction-days'),
      statusEl: $('auction-status'),
      onSource: (v) => { auctionSource = v; loadAuctionSource(v); },
      onComposite: (on, days) => {
        auctionComposite = { on: !!on, days: days || [] };
        if (on) loadComposite();
        else loadAuctionSource(auctionSource);
      },
    });
    levelsView = V.LevelsView();
    const levelsDrawInput = $('set-levels-draw');
    levelsDrawInput.checked = levelsDrawOn;
    levelsView.mount($('view-levels'), {
      drawInput: levelsDrawInput,
      onDraw: (on) => {
        levelsDrawOn = on;
        settings.levelsDraw = on;
        saveSettings();
        dirty.hist = true; dirty.dom = true;
      },
    });
    microView = V.MicrostructureView();
    microView.mount($('view-micro'));

    // VWAP band controls (hist panel chrome — wired here like the heatmap
    // venue select: they drive DATA composition, not just display).
    const vwapOnInput = $('set-vwap-on'), vwapAnchorSel = $('set-vwap-anchor'), vwapTsInput = $('set-vwap-ts');
    vwapOnInput.checked = vwapOn;
    vwapAnchorSel.value = settings.vwapAnchor;
    vwapTsInput.hidden = settings.vwapAnchor !== 'custom';
    vwapOnInput.addEventListener('change', () => {
      vwapOn = !!vwapOnInput.checked;
      settings.vwapOn = vwapOn;
      saveSettings();
      dirty.hist = true;
    });
    vwapAnchorSel.addEventListener('change', () => {
      if (VWAP_ANCHORS.indexOf(vwapAnchorSel.value) < 0) return;
      settings.vwapAnchor = vwapAnchorSel.value;
      saveSettings();
      vwapTsInput.hidden = settings.vwapAnchor !== 'custom';
      applyVwapAnchor();
    });
    vwapTsInput.addEventListener('change', () => { if (settings.vwapAnchor === 'custom') applyVwapAnchor(); });
    applyVwapAnchor();   // initial anchor (day, UTC) — before any trade arrives

    // One-shot /health probe (see section header for the mixed-content note).
    fetchJson(BYOD_API + '/health', 5000).then(() => {
      apiUp = true;
      pollLevels();
      setInterval(pollLevels, 300000);   // §4f cadence: levels every 5min
      loadAuctionSource(auctionSource);
      // 'today' is the one live-moving profile — 60s refresh, single-flight
      // by generation; day/composite sources refresh only on user action.
      setInterval(() => {
        if (auctionSource === 'today' && !auctionComposite.on) loadAuctionSource('today');
      }, 60000);
      applyVwapAnchor();   // re-run so the /v1/vwap parity read fires now the API is known up
    }).catch(() => {
      apiUp = false;
      setAuctionProfile(null, '', API_OFFLINE_NOTE);
      dirty.lvls = true;
    });

    // Archived-day listing (public HF tree API, CORS-open — §4f): populates
    // the source select; the actual 27 MiB download stays behind its gate.
    if (HFD) {
      HFD.listArchivedDates(HFD.DEFAULT_REPO).then((ds) => {
        archDates = ds || [];
        rebuildAuctionSources();
        dirty.auct = true;
      }).catch(() => { archDates = null; /* archive unreachable — sources stay local-only */ });
    }
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

    // ── T-1 (§4g): VPIN bucket-volume arming on the same event-ts gate —
    // V = sessionVol/50 after 5 min of flow, re-estimated hourly (future
    // buckets only; the store never restates a completed bucket). ──
    if (Number.isFinite(sessVolT0) && sessVol > 0) {
      if (!vpin && ts - sessVolT0 >= 300000) {
        vpin = S.VpinStore(sessVol / 50);
        lastVpinArmTs = ts;
        dirty.vpin = true;
      } else if (vpin && ts - lastVpinArmTs >= 3600000) {
        vpin.setBucketVol(sessVol / 50);
        lastVpinArmTs = ts;
        dirty.vpin = true;
      }
    }

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

    // ── I-1 (§4f): sample the running anchored VWAP on the SAME 5s event-ts
    // gate — each point is the value the accumulator genuinely held at that
    // moment (a true series, no back-computation §0.7). Skipped in replay
    // (the hist chart is REST-fed and honestly disabled there anyway). ──
    if (!REPLAY && vwapOn) {
      const vb = anchoredVwap.bands();
      if (Number.isFinite(vb.vwap)) {
        vwapHist.push({ ts, vwap: vb.vwap, s1: vb.s1, s2: vb.s2 });
        if (vwapHist.length > 4096) vwapHist.shift();   // ~5.7h at the 5s gate — plenty for a session
        dirty.hist = true;
      }
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
      base: BASE,   // OI unit label — venue OI is base-denominated (§4c unit rail)
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
      // §4g: the bybit mark is only BTC when BTCUSDT is selected — otherwise
      // the BTC cell goes honestly absent rather than wearing another symbol.
      else px = (SYM === 'BTCUSDT' && marks.bybit) ? marks.bybit.mark : NaN;
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

  // ── T-1 render-slice composers (§4g) — read stores/caches, mutate nothing ──

  /** Opening-type chip slice: pending → unwitnessed → the class + evidence.
   *  Citation + not-a-signal label ride the tooltip (the classifier stamps
   *  its own label on every result). */
  function openingSlice() {
    const LABEL = 'Dalton, Mind over Markets — descriptive session read, not a signal';
    if (!openState) return { text: '—', title: 'no primary-leg prints yet this UTC session · ' + LABEL };
    if (!openWitnessed()) {
      return {
        text: 'unwitnessed',
        title: 'page began ingesting at ' + new Date(openState.firstTs).toISOString().slice(11, 19)
          + ' UTC — the opening auction was not observed, so nothing is classified from a partial window (§0.7) · ' + LABEL,
      };
    }
    const r = openState.cls.classify();
    if (r.type === 'pending') {
      return { text: 'pending', title: 'classifies 60 min after the 00:00 UTC open · ' + LABEL };
    }
    const e = r.evidence;
    return {
      text: r.type,
      title: r.label + ' (Dalton, Mind over Markets; cutoffs are conventions of this implementation) · evidence: '
        + 'open ' + e.open.toFixed(1) + ' · hi ' + e.hi.toFixed(1) + ' · lo ' + e.lo.toFixed(1)
        + ' · range ' + e.range.toFixed(1) + ' · dir ' + e.dir + ' · first-extreme ' + e.firstSide
        + ' · open-crosses ' + e.crossCount + ' · n ' + e.n,
    };
  }

  /** Key-levels slice: registry portion (BTCUSDT only, honest note otherwise)
   *  + the live IB (any symbol — it is THIS session's own prints). */
  function klevSlice() {
    let prior = null, priorDate = '', weekly = null, regNote = '';
    if (REPLAY) regNote = 'registry disabled in replay — /v1/levels is a live-local collector endpoint (nothing fabricated, §0.7)';
    else if (SYM !== 'BTCUSDT') regNote = byodSymNote();
    else if (apiUp === false) regNote = API_OFFLINE_NOTE;
    else if (!levelsDays) regNote = 'awaiting /v1/levels (collector API probe in flight)…';
    else if (!levelsDays.length) regNote = 'registry empty — it fills as the collector closes UTC days';
    else {
      prior = levelsDays[levelsDays.length - 1];   // newest recorded (closed) day
      priorDate = String(prior.date || '');
      // Weekly open CONVENTION (§4g, comment mandated): the registry row
      // dated this UTC week's Monday — its daily open IS the weekly open
      // (UTC week); honestly absent when that Monday was not recorded.
      const base = Number.isFinite(lastBybitTs) ? lastBybitTs : Date.now();
      const day0 = utcDayStart(base);
      const monday = dateStr(day0 - ((new Date(day0).getUTCDay() + 6) % 7) * DAY_MS);
      for (const d of levelsDays) {
        if (String(d.date) === monday && Number.isFinite(d.o)) { weekly = { price: d.o, date: monday }; break; }
      }
    }
    // Live IB: witnessed-open sessions only, shown once the first hour has
    // elapsed on the EVENT clock (never a wall-clock guess).
    let ib = null, ibNote = '';
    if (!openState) ibNote = 'IB: no prints yet this session';
    else if (!openWitnessed()) ibNote = 'IB withheld — the page did not witness the 00:00 UTC open (§0.7: a partial range is not the IB)';
    else if (!(lastBybitTs >= openState.openTs + 3600000)) ibNote = 'IB forming — first 2×30 min UTC not yet elapsed';
    else if (Number.isFinite(openState.ibHigh) && Number.isFinite(openState.ibLow)) {
      ib = { high: openState.ibHigh, low: openState.ibLow };
    }
    return { prior, priorDate, weekly, naked: nakedPocs(), regNote, ib, ibNote };
  }

  /** Footprint key-level markers (toggle, default on): registry levels +
   *  live IB as {price, label, kind} — empty when toggled off. */
  function keyLevelMarks() {
    if (!klevDrawOn) return [];
    const s = klevSlice();
    const out = [];
    if (s.prior) {
      out.push({ price: s.prior.h, label: 'pdH', kind: 'ref' });
      out.push({ price: s.prior.l, label: 'pdL', kind: 'ref' });
      out.push({ price: s.prior.c, label: 'pdC', kind: 'ref' });
      out.push({ price: s.prior.poc, label: 'pPOC', kind: 'poc' });
      out.push({ price: s.prior.vah, label: 'pVAH', kind: 'ref' });
      out.push({ price: s.prior.val, label: 'pVAL', kind: 'ref' });
    }
    if (s.weekly) out.push({ price: s.weekly.price, label: 'wkO', kind: 'ref' });
    for (const n of s.naked) out.push({ price: n.price, label: 'nPOC', kind: 'naked' });
    if (s.ib) {
      out.push({ price: s.ib.high, label: 'IBH', kind: 'ib' });
      out.push({ price: s.ib.low, label: 'IBL', kind: 'ib' });
    }
    return out;
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
        // T-1 (§4g) store liveness for the harness.
        basisPoints: basisSeries.length,
        wallsRows: walls.list().length,
        vpinBuckets: vpin ? vpin.buckets().length : 0,
        tapeIntBuckets: tapeInt.sparkline().length,
        // T-2 (§4h) matrix liveness for the harness / live check: the spot-vs-
        // perp strip's completed buckets, whether it has a live cumulative read,
        // enabled legs, and how many legs currently carry a non-empty book.
        spotPerpBuckets: spotPerp.list().length,
        spotPerpLive: spotPerp.latest() ? 1 : 0,
        enabledLegs: legReg.snapshot().filter((l) => l.enabled).length,
        aggLegs: (() => { let n = 0; for (const [, b] of aggBook.books) if (b.bids.size > 0 || b.asks.size > 0) n++; return n; })(),
        // T-3 (§4i): aggregated-tape liveness — merged blocks + rail size (rail
        // is honestly 0 in a short replay with no huge/whale block) + the bybit
        // imprint level count (rolling volume-at-price the ladder paints).
        tapeAggRows: tapeAgg.length,
        bigPrints: bigPrints.length,
        imprintLevels: imprints.bybit ? imprints.bybit.size : 0,
      };
    },
    sym() { return SYM; },
    // N1: per-panel render-guard snapshot for the L1 fault-injection proof —
    // {key: {dead, consecutive, failures, threshold}}. Read-only (stats() is a
    // pure snapshot); mutates nothing.
    guards() {
      const out = {};
      for (const k in guards) out[k] = guards[k].stats();
      return out;
    },
    // N1: monotonic frame() invocation count — the loop's heartbeat. The L1
    // fault proof samples it twice to show the rAF loop survived quarantine.
    frames() { return frameCount; },
    // T-2 (§4h): per-leg matrix snapshot — {enabled, kind (chip state), hasBook}
    // keyed by leg key. The live-check harness asserts ≥5 legs synced from this.
    legs() {
      const out = {};
      for (const key of LEG_KEYS) {
        const ex = LEG_EX[key];
        const st = statuses[ex] || {};
        const book = aggBook.books.get(ex);
        out[key] = {
          enabled: legReg.isEnabled(key),
          kind: st.kind || null,
          hasBook: !!book && (book.bids.size > 0 || book.asks.size > 0),
        };
      }
      return out;
    },
  };

  // ─── Settings row wiring ────────────────────────────────────────────────
  const tickSel = $('set-tick');
  // T-1 (§4g): options are symbol-dependent — build them now (keeping a
  // persisted off-list tick); the real per-symbol derivation re-runs when
  // the tickers universe first answers with the symbol's price.
  applyTickOptions(NaN, true);
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

  // ─── T-3 (§4i): tape tier thresholds + audio UX aid (settings row) ───────
  //
  // The four size-tier cuts are LABELED DISPLAY CONVENTIONS (not signals) —
  // editable and persisted. A change re-classifies every block and re-seeds
  // the big-print rail with the current tiers (honest re-read, nothing
  // synthesized). Audio is a UX aid, default OFF + muted; toggling it ON is the
  // gesture that lets the AudioContext start (browser autoplay policy).
  const tierInputs = { sig: $('set-tier-sig'), large: $('set-tier-large'), huge: $('set-tier-huge'), whale: $('set-tier-whale') };
  const audioBox = $('set-audio'), audioVol = $('set-audio-vol');
  function reflectTierInputs() {
    for (const k in tierInputs) tierInputs[k].value = String(Math.round(settings.tapeTiers[k]));
  }
  reflectTierInputs();
  audioBox.checked = settings.tapeAudio.on;
  audioVol.value = String(settings.tapeAudio.vol);
  /** Read the four inputs; adopt ONLY a strictly-increasing positive set
   *  (a mis-ordered tier would mis-classify blocks — reject, keep the last
   *  good set, and restore the inputs to it). */
  function applyTierInputs() {
    const t = {};
    for (const k in tierInputs) t[k] = Number(tierInputs[k].value);
    const ok = ['sig', 'large', 'huge', 'whale'].every((k) => Number.isFinite(t[k]) && t[k] > 0)
      && t.sig < t.large && t.large < t.huge && t.huge < t.whale;
    if (!ok) { reflectTierInputs(); return; }   // invalid → revert the inputs
    settings.tapeTiers = t;
    rebuildBigPrints();   // re-classify what a "block" is under the new tiers
    saveSettings();
    dirty.tape = true;
  }
  for (const k in tierInputs) tierInputs[k].addEventListener('change', applyTierInputs);
  function toggleTapeAudio(force) {
    const on = typeof force === 'boolean' ? force : !settings.tapeAudio.on;
    settings.tapeAudio.on = on;
    if (on) ensureAudioCtx();   // create/resume on the toggle gesture (autoplay policy)
    audioBox.checked = on;
    saveSettings();
  }
  audioBox.addEventListener('change', () => toggleTapeAudio(audioBox.checked));
  audioVol.addEventListener('input', () => {
    const v = Number(audioVol.value);
    settings.tapeAudio.vol = Number.isFinite(v) ? Math.max(0, Math.min(1, v)) : 0.4;
    saveSettings();
  });

  // Palette-reachable setters (also reflect the panel selects) — §4i cmdk adds.
  function setTapeMarket(v) { if (TAPE_MARKETS.indexOf(v) < 0) return; settings.tapeMarket = v; tapeMarketSel.value = v; saveSettings(); dirty.tape = true; }
  function setTapeVenue(v) { if (TAPE_VENUES.indexOf(v) < 0) return; settings.tapeVenue = v; tapeVenueSel.value = v; saveSettings(); dirty.tape = true; }
  function setTapeMetric(v) { if (TAPE_METRICS.indexOf(v) < 0) return; settings.tapeMetric = v; tapeMetricSel.value = v; saveSettings(); dirty.tape = true; }
  function setDomSource(v) { if (DOM_SOURCES.indexOf(v) < 0) return; settings.domSource = v; domSourceSel.value = v; saveSettings(); dirty.dom = true; }

  // ─── T-1 (§4g): symbol picker + switch (the multi-symbol headline) ───────
  //
  // Universe = the EXISTING fetchBybitAllTickers 30s poll (tickerRows) —
  // top-N by turnover + substring search; no new endpoint. Switching is the
  // HONEST RESTART (settings hint): every session store rebuilds, all WS
  // legs close and re-subscribe with derived venue ids, and the symbol-
  // parameterized REST panels refetch. Disabled in replay (fixtures are
  // recorded BTCUSDT frames — switching would mislabel them).
  let tickDerived = SYM === 'BTCUSDT';   // BTC keeps its pinned tick set — nothing to derive
  const symBtn = $('sym-btn'), symPop = $('sym-pop'), symSearch = $('sym-search'), symList = $('sym-list');
  let symSel = 0;   // keyboard-selected row index in the CURRENT filtered list
  const fmtTurnover = (x) => !Number.isFinite(x) ? '—'
    : x >= 1e9 ? '$' + (x / 1e9).toFixed(1) + 'B'
      : x >= 1e6 ? '$' + (x / 1e6).toFixed(1) + 'M' : '$' + Math.round(x / 1e3) + 'k';

  function refreshSymLabels() {
    symBtn.textContent = SYM + ' ▾';
    document.querySelectorAll('.js-sym').forEach((el) => { el.textContent = SYM; });
    // Unit mentions in static hints follow the symbol too — a '(BTC)' left
    // behind under ETHUSDT would mislabel the quantities it describes (§0).
    document.querySelectorAll('.js-base').forEach((el) => { el.textContent = BASE || 'base units'; });
  }

  /** Filtered universe rows for the picker: top-30 by turnover, or every
   *  substring match when searching (already turnover-sorted upstream). */
  function symRows() {
    const q = (symSearch.value || '').trim().toUpperCase();
    const rows = (tickerRows || []).slice().sort((a, b) => b.turnover24h - a.turnover24h);
    return (q ? rows.filter((r) => r.sym.indexOf(q) >= 0) : rows).slice(0, 30);
  }

  function renderSymList() {
    if (symPop.hidden) return;
    const rows = symRows();
    if (symSel >= rows.length) symSel = Math.max(0, rows.length - 1);
    if (!rows.length) {
      symList.innerHTML = '<li class="sym-note">' + (tickerRows
        ? 'no match in the bybit linear universe'
        : 'awaiting the tickers universe (30s poll)…') + '</li>';
      return;
    }
    symList.innerHTML = rows.map((r, i) =>
      '<li role="option" data-sym="' + r.sym + '" aria-selected="' + (i === symSel)
      + '" class="' + (r.sym === SYM ? 'cur ' : '') + (i === symSel ? 'sel' : '') + '">'
      + '<span class="s-sym">' + r.sym + '</span>'
      + '<span class="s-px">' + (Number.isFinite(r.last) ? r.last : '—') + '</span>'
      + '<span class="s-to">' + fmtTurnover(r.turnover24h) + '</span></li>').join('');
  }

  function openSymPop() {
    if (REPLAY) return;
    symPop.hidden = false;
    symBtn.setAttribute('aria-expanded', 'true');
    symSearch.value = '';
    symSel = 0;
    renderSymList();
    symSearch.focus();
  }
  function closeSymPop() {
    symPop.hidden = true;
    symBtn.setAttribute('aria-expanded', 'false');
  }

  /** THE honest restart (§4g): a new symbol is a new session. Order matters —
   *  legs close first so no old-symbol frame lands in a fresh store. */
  function switchSymbol(sym) {
    if (REPLAY || !sym || sym === SYM) return;
    stopAllLegs();
    SYM = sym;
    BASE = baseAsset(sym);
    settings.sym = sym;
    // Session stores: flow + grid-bound + edge families all restart (the
    // browser keeps no raw tick store to re-bucket from — settings hint).
    let row = null;
    for (const r of tickerRows || []) if (r.sym === sym) { row = r; break; }
    applyTickOptions(row ? row.last : NaN);   // re-derived default tick (§4g convention)
    saveSettings();
    rebuildFlowStores();
    rebuildFootprint();
    rebuildProfile();
    rebuildHeatmapStores();
    rebuildEdgeStores();
    // Latest-value caches + event clocks: everything symbol-scoped resets —
    // stale values from the old symbol must never render under the new one.
    for (const k in marks) delete marks[k];
    for (const k in ois) delete ois[k];
    for (const k in lastDepthTs) delete lastDepthTs[k];
    for (const k in lastPriceByEx) delete lastPriceByEx[k];
    sessionHigh = NaN; sessionLow = NaN; lastPrice = NaN; lastBybitTs = NaN;
    pendingTrades.length = 0;
    oiHistBybit.length = 0;
    intelWin.price.length = 0; intelWin.cvd.length = 0;
    detSeenIntel = null; detLastEvt = null;
    lastIntelTs = -Infinity;
    confData = null;
    // Alert engine: same honest restart — the events feed and the per-rule
    // trackers (price-cross prev, funding sign) are symbol-scoped; carried
    // over they would read phantom crosses/flips against the new symbol's
    // first snapshot, and the feed would mix eras with no per-source label
    // (§0.7). This keeps the settings hint's 'EVERY session store' literal.
    alertEngine = S.AlertEngine({ rules: engineRules(), cooldownMs: 60000 });
    alertFresh.length = 0;
    okxFund = null; okxOiEv = null;
    btcTicker = row;
    vwapHist = []; storeVwapTxt = null;
    anchoredVwap.reset(vwapAnchorMs());
    startAllLegs();
    refreshSymLabels();
    if (restRefresh) restRefresh();          // O-3: klines/TPO/VP refetch under the new symbol
    loadAuctionSource(auctionSource);        // I-1: renders the §4g honest note when sym ≠ BTCUSDT
    if (SYM === 'BTCUSDT' && apiUp === true) pollLevels();
    dirtyAll();
    // N1: symbol-switch re-init rebuilds EVERY store above, so a render fault's
    // stale inputs are gone — the one moment a quarantined panel legitimately
    // revives. Clear each breaker + drop its dead-chip so the new symbol paints
    // fresh (a reconnect/un-pause does NOT reach here — a code fault must not
    // clear on those, only on a full re-init or reload).
    for (const k in guards) { guards[k].reset(); clearPanelDead(k); }
  }

  if (REPLAY) {
    symBtn.disabled = true;
    symBtn.title = 'symbol switching is disabled in replay — the fixtures are recorded BTCUSDT frames; '
      + 'driving them under another label would mislabel every panel (§0)';
  } else {
    symBtn.addEventListener('click', () => { if (symPop.hidden) openSymPop(); else closeSymPop(); });
    symSearch.addEventListener('input', () => { symSel = 0; renderSymList(); });
    symSearch.addEventListener('keydown', (e) => {
      const rows = symRows();
      if (e.key === 'ArrowDown') { e.preventDefault(); symSel = Math.min(rows.length - 1, symSel + 1); renderSymList(); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); symSel = Math.max(0, symSel - 1); renderSymList(); }
      else if (e.key === 'Enter') { e.preventDefault(); if (rows[symSel]) { closeSymPop(); switchSymbol(rows[symSel].sym); } }
      else if (e.key === 'Escape') { e.preventDefault(); closeSymPop(); symBtn.focus(); }
    });
    symList.addEventListener('click', (e) => {
      const li = e.target.closest('li[data-sym]');
      if (li) { closeSymPop(); switchSymbol(li.getAttribute('data-sym')); }
    });
    document.addEventListener('click', (e) => {
      if (!symPop.hidden && !e.target.closest('.sym-picker')) closeSymPop();
    });
  }
  refreshSymLabels();

  // ─── T-2 (§4h): leg manager — the 7-leg matrix enable/disable popover ────
  //
  // Same topbar-popover idiom as the symbol picker. Each row: live status dot
  // + a persisted enable/disable toggle + the leg's chip state. Toggling wires
  // straight to setLegEnabled (registry flip → persist → socket restart/close);
  // disabling mid-session closes the socket and the leg's panels state
  // 'disabled (settings)' honestly. Toggles are DISABLED in replay (the
  // deterministic harness drives all legs) but the popover still OPENS so the
  // per-leg status is inspectable. renderLegRows is a hoisted declaration so
  // setLegEnabled (defined earlier) can repaint the list after a flip; a
  // change-keyed cache keeps the live-status repaint from eating a click.
  const legsBtn = $('legs-btn'), legsPop = $('legs-pop'), legsList = $('legs-list');
  let legsPopOpen = false;
  let legRowsKey = '';
  function renderLegRows() {
    const parts = legReg.snapshot().map((l) => {
      const st = statuses[LEG_EX[l.key]] || {};
      // Dot state mirrors the header chip taxonomy; a disabled leg is neutral
      // (grey), not error — the freeze is user-chosen (§4h amber-not-red rule).
      const cls = !l.enabled ? '' : st.kind === 'open' ? 'live' : st.kind === 'error' ? 'error' : 'stale';
      const text = !l.enabled ? 'disabled (settings)' : (st.msg || 'connecting…');
      return { key: l.key, enabled: l.enabled, cls, text };
    });
    const k = parts.map((r) => r.key + r.enabled + r.cls + r.text).join('|');
    if (k === legRowsKey) return;   // nothing changed — don't rebuild under a click
    legRowsKey = k;
    legsList.innerHTML = parts.map((r) =>
      '<li class="leg-row ' + r.cls + '">'
      + '<i class="leg-dot"></i>'
      + '<input type="checkbox" data-leg="' + r.key + '"' + (r.enabled ? ' checked' : '') + (REPLAY ? ' disabled' : '') + ' />'
      + '<span class="leg-name"></span><span class="leg-status"></span></li>').join('');
    // Labels/status via textContent (never innerHTML) — no escaping question.
    const lis = legsList.children;
    for (let i = 0; i < parts.length; i++) {
      lis[i].querySelector('.leg-name').textContent = LEG_LABEL[parts[i].key];
      lis[i].querySelector('.leg-status').textContent = parts[i].text;
    }
  }
  function openLegPop() { legsPop.hidden = false; legsBtn.setAttribute('aria-expanded', 'true'); legsPopOpen = true; renderLegRows(); }
  function closeLegPop() { legsPop.hidden = true; legsBtn.setAttribute('aria-expanded', 'false'); legsPopOpen = false; }
  legsBtn.addEventListener('click', () => { if (legsPop.hidden) openLegPop(); else closeLegPop(); });
  legsList.addEventListener('change', (e) => {
    const box = e.target.closest('input[data-leg]');
    if (box) setLegEnabled(box.getAttribute('data-leg'), box.checked);   // no-op in replay (setLegEnabled guards)
  });
  document.addEventListener('click', (e) => {
    if (!legsPop.hidden && !e.target.closest('.leg-mgr')) closeLegPop();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !legsPop.hidden) { closeLegPop(); legsBtn.focus(); }
  });
  renderLegRows();   // seed the list before first open (statuses fill in as legs connect)

  // ─── T-1 (§4g): workspace presets — named collapsed-set combos ──────────
  const wsSel = $('workspace-sel');
  wsSel.value = settings.workspace;
  function applyWorkspace(name) {
    if (WORKSPACE_NAMES.indexOf(name) < 0) return;
    settings.workspace = name;
    settings.collapsed = Object.assign({},
      name === 'last' ? settings.lastCollapsed : WORKSPACES[name]);
    wsSel.value = name;
    saveSettings();
    applyCollapse();
    for (const k in SEC_OF) if (!settings.collapsed[SEC_OF[k]]) dirty[k] = true;   // repaint what re-expanded
  }
  wsSel.addEventListener('change', () => applyWorkspace(wsSel.value));

  // ─── N3 (Gap 7): a11y toggles ported from the analytics page ─────────────
  //
  // CVD-safe (Okabe-Ito) palette + compact density — the analytics page has
  // them (app.js applyCvd/applyDensity); the terminal did not, so a colour-
  // blind user landed on red/green footprint/heatmap with no escape. These
  // reuse app.js's semantics and the SAME standalone localStorage keys
  // (btcq-cvd / btcq-density, separate from the btcq-terminal settings JSON) so
  // the preference is SHARED across both pages — set once, applies everywhere.
  //
  // One deliberate divergence from app.js: it toggles the class on <body>; we
  // toggle it on documentElement. The footprint/heatmap are canvas views whose
  // palette reader (terminal-views.js pal() → cssVar()) reads custom props off
  // getComputedStyle(document.documentElement) at draw time. Custom properties
  // inherit downward only, so a body-level override never reaches
  // documentElement — the canvas would not recolour. Keying the class on
  // documentElement (with the :root.cvd-strict / :root.density-compact rules in
  // terminal.css) makes the canvas reader see it AND every descendant inherit
  // it. Presentation only: flips a class + one repaint, never a recompute — no
  // store, bar, tick grid, or numeric value is touched.
  const A11Y = {
    get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : v; } catch (_) { return d; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (_) { /* private mode / disabled */ } },
  };
  function applyCvd(strict) {
    document.documentElement.classList.toggle('cvd-strict', !!strict);
    const b = $('set-cvd-strict'); if (b) b.checked = !!strict;
    A11Y.set('btcq-cvd', strict ? '1' : '0');
    dirtyAll();   // force one repaint; the canvas palette re-reads --up/--down next rAF
  }
  function toggleCvd() { applyCvd(!document.documentElement.classList.contains('cvd-strict')); }
  function applyDensity(mode) {
    const compact = mode === 'compact';
    document.documentElement.classList.toggle('density-compact', compact);
    const b = $('set-density-compact'); if (b) b.checked = compact;
    A11Y.set('btcq-density', mode);
    dirtyAll();   // compact retightens the token scale → panels re-measure on repaint
  }
  function toggleDensity() { applyDensity(document.documentElement.classList.contains('density-compact') ? 'comfortable' : 'compact'); }
  // Keys never fire while typing in a field (symbol search, cmdk input, whale/
  // tick/datetime number boxes) — the terminal has no other single-key shortcut.
  function typingInField(el) {
    if (!el) return false;
    const t = (el.tagName || '').toLowerCase();
    return t === 'input' || t === 'select' || t === 'textarea' || el.isContentEditable;
  }

  // Restore the shared a11y preference at boot (default comfortable / standard
  // palette) — reflects a value the user may have set on the analytics page.
  applyCvd(A11Y.get('btcq-cvd', '0') === '1');
  applyDensity(A11Y.get('btcq-density', 'comfortable') === 'compact' ? 'compact' : 'comfortable');
  const cvdBox = $('set-cvd-strict');
  if (cvdBox) cvdBox.addEventListener('change', () => applyCvd(cvdBox.checked));
  const denBox = $('set-density-compact');
  if (denBox) denBox.addEventListener('change', () => applyDensity(denBox.checked ? 'compact' : 'comfortable'));

  // ─── T-1 (§4g): command palette — index.html §5.1 idiom (fuzzy filter,
  // arrow nav, Enter runs, Esc closes, focus restored) ─────────────────────
  const cmdk = { items: [], filtered: [], sel: 0, lastFocus: null };
  const escapeHtml = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  function jumpToPanel(panel) {
    if (!panel) return;
    // A collapsed section un-collapses first (a jump into a hidden panel
    // would land nowhere) — that is a manual layout change, so it flips the
    // workspace to 'last' like any section toggle.
    const host = panel.closest('[data-sec]');
    const sec = host ? host.getAttribute('data-sec') : null;
    if (sec && settings.collapsed[sec]) {
      settings.collapsed[sec] = false;
      settings.workspace = 'last';
      settings.lastCollapsed = Object.assign({}, settings.collapsed);
      wsSel.value = 'last';
      saveSettings();
      applyCollapse();
      for (const k in SEC_OF) if (SEC_OF[k] === sec) dirty[k] = true;
    }
    panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
    const prev = panel.getAttribute('tabindex');
    panel.setAttribute('tabindex', '-1');
    try { panel.focus({ preventScroll: true }); } catch (_) { /* ignore */ }
    if (prev == null) setTimeout(() => panel.removeAttribute('tabindex'), 0);
  }

  function buildCommands() {
    const items = [];
    document.querySelectorAll('main .panel').forEach((p) => {
      const h2 = p.querySelector('h2');
      // Label = the h2's leading text plus its .js-sym symbol span, stopping
      // at the first other element (status tags / controls). The first text
      // node alone dropped the symbol span — 'HISTORICAL CHART — BTCUSDT'
      // listed as a dangling 'historical chart —'.
      let label = '';
      for (const n of h2 ? h2.childNodes : []) {
        if (n.nodeType === Node.TEXT_NODE) label += n.textContent;
        else if (n.nodeType === Node.ELEMENT_NODE && n.classList.contains('js-sym')) label += n.textContent;
        else break;
      }
      label = label.replace(/\s+/g, ' ').trim() || (p.getAttribute('aria-label') || '');
      if (!label) return;
      items.push({ kind: 'panel', label, run: () => jumpToPanel(p) });
    });
    for (const sec of SECTIONS) {
      items.push({
        kind: 'section',
        label: 'Toggle ' + sec + ' (collapse/expand)',
        run: () => {
          const btn = document.querySelector('.sec-toggle[data-collapse="' + sec + '"]');
          if (btn) btn.click();   // the toggle's own handler persists + flips workspace to 'last'
        },
      });
    }
    for (const name of WORKSPACE_NAMES) {
      items.push({ kind: 'workspace', label: 'Workspace → ' + name, run: () => applyWorkspace(name) });
    }
    // T-3 (§4i): tape filters, audio toggle, ladder source — view state, so
    // they work in replay too (no sockets touched). Labels reflect the CURRENT
    // value (buildCommands re-runs on every open).
    for (const m of TAPE_MARKETS) items.push({ kind: 'tape', label: 'Tape market → ' + m, run: () => setTapeMarket(m) });
    for (const v of TAPE_VENUES) items.push({ kind: 'tape', label: 'Tape venue → ' + (v === 'all' ? 'all venues' : (LEG_LABEL[EX_LEG[v]] || v)), run: () => setTapeVenue(v) });
    for (const mt of TAPE_METRICS) items.push({ kind: 'tape', label: 'Tape metric → ' + mt, run: () => setTapeMetric(mt) });
    items.push({ kind: 'tape', label: 'Tape audio → ' + (settings.tapeAudio.on ? 'off' : 'on') + ' (UX aid, not a signal)', run: () => toggleTapeAudio() });
    for (const s of DOM_SOURCES) items.push({ kind: 'ladder', label: 'Ladder source → ' + (s === '__agg' ? 'aggregated same-quote (USDT)' : (LEG_LABEL[EX_LEG[s]] || s)), run: () => setDomSource(s) });
    items.push({ kind: 'ladder', label: 'Ladder cumulative depth → ' + (settings.domCum ? 'off' : 'on'), run: () => { settings.domCum = !settings.domCum; domCumBox.checked = settings.domCum; saveSettings(); dirty.dom = true; } });
    // N3 (Gap 7): a11y toggles — view state, so they work in replay too (no
    // sockets/data touched). Labels reflect the CURRENT value (re-runs on open).
    items.push({ kind: 'a11y', label: 'Colour-blind palette (Okabe-Ito) → ' + (document.documentElement.classList.contains('cvd-strict') ? 'off' : 'on'), run: toggleCvd });
    items.push({ kind: 'a11y', label: 'Density → ' + (document.documentElement.classList.contains('density-compact') ? 'comfortable' : 'compact'), run: toggleDensity });
    // T-2 (§4h): leg enable/disable — one entry per matrix leg (label reflects
    // the CURRENT state; buildCommands re-runs on every open). Live only: replay
    // drives all legs deterministically (setLegEnabled no-ops there anyway).
    if (!REPLAY) {
      for (const key of LEG_KEYS) {
        const on = legReg.isEnabled(key);
        items.push({
          kind: 'leg',
          label: (on ? 'Disable' : 'Enable') + ' leg — ' + LEG_LABEL[key],
          run: () => setLegEnabled(key, !legReg.isEnabled(key)),
        });
      }
    }
    // Symbol switching: fuzzy over the WHOLE tickers universe (turnover-
    // sorted so the liquid names win ties). Live modes only — replay pins
    // BTCUSDT (see the picker note).
    if (!REPLAY && tickerRows) {
      const rows = tickerRows.slice().sort((a, b) => b.turnover24h - a.turnover24h);
      for (const r of rows) {
        items.push({
          kind: 'symbol',
          label: r.sym + ' · ' + fmtTurnover(r.turnover24h) + ' 24h',
          run: () => switchSymbol(r.sym),
        });
      }
    }
    cmdk.items = items;
  }

  // Subsequence fuzzy score (app.js §5.1, verbatim semantics): prefix >
  // contiguous substring > scattered subsequence; -1 = no match.
  function fuzzyScore(text, needle) {
    const hay = text.toLowerCase();
    if (!needle) return 0;
    const sub = hay.indexOf(needle);
    if (sub === 0) return 10000;
    if (sub > 0) return 6000 - sub;
    let hi = 0, score = 2000, last = -2;
    for (let i = 0; i < needle.length; i++) {
      const found = hay.indexOf(needle[i], hi);
      if (found < 0) return -1;
      if (found === last + 1) score += 18;
      score -= found;
      last = found; hi = found + 1;
    }
    return score;
  }

  function renderCmdkList() {
    const list = $('cmdk-list');
    if (!cmdk.filtered.length) {
      list.innerHTML = '<li class="cmdk-empty">No matching command.</li>';
      return;
    }
    list.innerHTML = cmdk.filtered.map((it, i) =>
      '<li class="cmdk-item" role="option" id="cmdk-opt-' + i + '" aria-selected="' + (i === cmdk.sel) + '" data-i="' + i + '">'
      + '<span class="cmdk-kind">' + it.kind + '</span><span class="cmdk-label">' + escapeHtml(it.label) + '</span></li>').join('');
    const selEl = list.querySelector('[aria-selected="true"]');
    if (selEl) selEl.scrollIntoView({ block: 'nearest' });
  }

  function filterCmdk(q) {
    const needle = q.trim().toLowerCase();
    cmdk.filtered = !needle ? cmdk.items.slice()
      : cmdk.items
        .map((it) => ({ it, s: fuzzyScore(it.kind + ' ' + it.label, needle) }))
        .filter((r) => r.s >= 0)
        .sort((a, b) => b.s - a.s)
        .map((r) => r.it);
    cmdk.sel = 0;
    renderCmdkList();
  }

  function openCmdk() {
    const ov = $('cmdk'), input = $('cmdk-input');
    cmdk.lastFocus = document.activeElement;
    buildCommands();
    ov.hidden = false;
    input.value = '';
    filterCmdk('');
    input.focus();
  }
  function closeCmdk() {
    const ov = $('cmdk');
    if (ov.hidden) return;
    ov.hidden = true;
    if (cmdk.lastFocus && cmdk.lastFocus.focus) try { cmdk.lastFocus.focus(); } catch (_) { /* ignore */ }
  }
  function runCmdk(i) {
    const it = cmdk.filtered[i];
    closeCmdk();
    if (it && it.run) try { it.run(); } catch (_) { /* ignore */ }
  }

  $('cmdk-open').addEventListener('click', openCmdk);
  {
    const input = $('cmdk-input'), list = $('cmdk-list');
    input.addEventListener('input', () => filterCmdk(input.value));
    input.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowDown') { e.preventDefault(); cmdk.sel = Math.min(cmdk.filtered.length - 1, cmdk.sel + 1); renderCmdkList(); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); cmdk.sel = Math.max(0, cmdk.sel - 1); renderCmdkList(); }
      else if (e.key === 'Enter') { e.preventDefault(); runCmdk(cmdk.sel); }
      else if (e.key === 'Home') { e.preventDefault(); cmdk.sel = 0; renderCmdkList(); }
      else if (e.key === 'End') { e.preventDefault(); cmdk.sel = cmdk.filtered.length - 1; renderCmdkList(); }
    });
    list.addEventListener('click', (e) => {
      const li = e.target.closest('.cmdk-item');
      if (li && li.dataset.i != null) runCmdk(+li.dataset.i);
    });
    list.addEventListener('mousemove', (e) => {
      const li = e.target.closest('.cmdk-item');
      if (li && li.dataset.i != null && +li.dataset.i !== cmdk.sel) { cmdk.sel = +li.dataset.i; renderCmdkList(); }
    });
    document.querySelectorAll('[data-cmdk-dismiss]').forEach((el) => el.addEventListener('click', closeCmdk));
    document.addEventListener('keydown', (e) => {
      const open = !$('cmdk').hidden;
      if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) {
        e.preventDefault();
        if (open) closeCmdk(); else openCmdk();
        return;
      }
      if (e.key === 'Escape' && open) { e.preventDefault(); closeCmdk(); }
      // N3 (Gap 7): single-key a11y shortcuts, mirroring analytics v/d. Guarded
      // — never while the palette is open, never while typing in a field.
      if (open) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (typingInField(e.target)) return;
      if (e.key === 'v' || e.key === 'V') { e.preventDefault(); toggleCvd(); }
      else if (e.key === 'd' || e.key === 'D') { e.preventDefault(); toggleDensity(); }
    });
  }

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
    // I-1 budgets: auct moves on fetch/60s refresh; lvls at its 5min poll;
    // micro at the 1/s sampler cadence (the view throttles setData further).
    auct: 800, lvls: 1000, micro: 500,
    scr: 800, rsi: 500, opts: 1000, whale: 600, alerts: 300, conf: 800,
    // O-5 budgets: jour/cal move on user actions; poly/news/econ at their
    // 30–60s poll cadence — budgets just cap redraw bursts.
    jour: 400, cal: 600, poly: 1000, news: 800, econ: 1000,
    // T-1 budgets (§4g): tapeint ticks with the tape burst (a text strip +
    // 120px spark — cheap); walls/klev move at the 1/s sampler / 5min poll;
    // vpin per completed bucket; basis at the ~1s mark cadence (the view
    // throttles setData further, the CVD budget).
    tapeint: 500, walls: 1000, vpin: 800, klev: 1000, basis: 600,
    // T-2 (§4h): spot-vs-perp CVD strip — the CVD chart's setData budget.
    spcvd: 600,
  };
  const lastAt = {
    fp: 0, dom: 0, tape: 0, agg: 0, header: 0, liq: 0, heat: 0, liqmap: 0, det: 0,
    hist: 0, tpo: 0, vp: 0, farb: 0, macro: 0,
    auct: 0, lvls: 0, micro: 0,
    scr: 0, rsi: 0, opts: 0, whale: 0, alerts: 0, conf: 0,
    jour: 0, cal: 0, poly: 0, news: 0, econ: 0,
    tapeint: 0, walls: 0, vpin: 0, klev: 0, basis: 0,
    spcvd: 0,
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
    auct: 'auction', lvls: 'auction', micro: 'auction',
    scr: 'intelligence', rsi: 'intelligence', opts: 'intelligence',
    whale: 'intelligence', alerts: 'intelligence', conf: 'intelligence',
    jour: 'portfolio', cal: 'portfolio', poly: 'portfolio', news: 'portfolio', econ: 'portfolio',
    tapeint: 'orderflow', walls: 'orderflow', basis: 'structure', klev: 'auction', vpin: 'auction',
    spcvd: 'orderflow',   // T-2 (§4h): spot-vs-perp CVD strip (ORDERFLOW section)
  };
  const VIEW_ANCHOR = {
    fp: 'view-footprint', dom: 'view-dom', tape: 'view-tape', agg: 'view-aggbook',
    liq: 'view-liq', heat: 'view-bookheat', liqmap: 'view-liqheat', det: 'view-detect',
    hist: 'view-hist', tpo: 'view-tpo', vp: 'view-klinevp', farb: 'view-farb', macro: 'view-macro',
    auct: 'view-auction', lvls: 'view-levels', micro: 'view-micro',
    scr: 'view-screener', rsi: 'view-rsi', opts: 'view-options',
    whale: 'view-whale', alerts: 'view-alerts', conf: 'view-conf',
    jour: 'view-journal', cal: 'view-calendar', poly: 'view-polymarket', news: 'view-news', econ: 'view-econ',
    tapeint: 'view-tapeint', walls: 'view-walls', basis: 'view-basis', klev: 'view-keylevels', vpin: 'view-vpin',
    spcvd: 'view-spotperp',   // T-2 (§4h)
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
      // T-1 (§4g): a manual toggle makes the layout the user's OWN — the
      // workspace flips to 'last' and remembers this state.
      settings.workspace = 'last';
      settings.lastCollapsed = Object.assign({}, settings.collapsed);
      wsSel.value = 'last';
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
        const g40 = book.grouped(settings.tick, 40);
        detector.onDepthSample(ts, g40);
        // I-1 (§4f): OFI + microprice ride the SAME event-ts-gated sampler
        // (the MicrostructureView contract) — a stalled feed stops producing
        // samples instead of recording fake flat flow (§0.7). OfiStore takes
        // the top-5 of the same grouped ladder; microprice reads best-of-book
        // (grid-free). An empty-ladder gap re-seeds OFI inside the store.
        ofi.onDepthSample(ts, g40);
        // T-1 (§4g): the walls ledger rides the SAME 1/s grouped sample —
        // same venue, same tick grid as the detector it cross-references.
        feedWalls(ts, g40, book);
        const mp = S.microprice(book);
        const best = book.best();
        if (mp !== null && best && best.bid && best.ask) {
          mpHist.push({ ts, d: mp - (best.bid[0] + best.ask[0]) / 2 });
          if (mpHist.length > 3600) mpHist.shift();   // same horizon as the sample ring
        }
        dirty.micro = true;
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

  // ── T-1 (§4g): walls-ledger feed — one WallsLedger.update per grouped
  // ladder level per 1/s bybit sample (the store's caller contract) ──
  //
  // Baseline = p95 of the CURRENT sample's grouped level sizes (both sides;
  // §4g: p95, one notch stricter than the detector's median, so one whale
  // neighbor cannot hide a wall). Disappearance is observed by diffing
  // against the PREVIOUS sample's levels — but only for prices still INSIDE
  // the side's current 40-level window: a level that scrolled out of
  // coverage is unobservable and is never judged (coverage limit, stated;
  // the detector shares it).
  function feedWalls(ts, g, book) {
    const best = book.best();
    if (!best || !best.bid || !best.ask) return;
    const mid = (best.bid[0] + best.ask[0]) / 2;
    const qtys = [];
    for (const r of g.bids) qtys.push(r.qty);
    for (const r of g.asks) qtys.push(r.qty);
    if (qtys.length < 20) return;   // too thin a ladder for a p95 baseline to mean anything
    qtys.sort((a, b) => a - b);
    const p95 = qtys[Math.floor(0.95 * (qtys.length - 1))];
    if (!(p95 > 0)) return;
    const ticksFrom = (price) => Math.abs(price - mid) / settings.tick;
    const cur = { bid: new Map(), ask: new Map() };
    for (const r of g.bids) { cur.bid.set(r.price, r.qty); walls.update(ts, 'bid', r.price, r.qty, p95, ticksFrom(r.price), mid); }
    for (const r of g.asks) { cur.ask.set(r.price, r.qty); walls.update(ts, 'ask', r.price, r.qty, p95, ticksFrom(r.price), mid); }
    for (const side of ['bid', 'ask']) {
      let lo = Infinity, hi = -Infinity;
      for (const px of cur[side].keys()) { if (px < lo) lo = px; if (px > hi) hi = px; }
      for (const px of prevLadder[side].keys()) {
        if (cur[side].has(px)) continue;
        if (px < lo || px > hi) continue;   // scrolled out of the window — unobservable, not judged
        walls.update(ts, side, px, 0, p95, ticksFrom(px), mid);
      }
    }
    prevLadder = cur;
    dirty.walls = true;
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
    frameCount++;   // N1: loop heartbeat (see frames() debug hook)
    // N1: the WHOLE frame body runs in a try/finally so scheduleFrame() —
    // the loop's ONLY re-arm — ALWAYS fires, even if ingestion or shared code
    // throws before the paint section (Gap 2: a throw before the old last-
    // statement scheduleFrame() froze every panel permanently). safePanel below
    // is the fine-grained per-panel boundary; this is the coarse backstop.
    try {
      // INGESTION-SIDE work runs on every tick unconditionally — §4e.2 honesty:
      // the visibility gates below skip PAINT only, never data. These keep
      // sampling/evaluating while the tab is hidden (frame() then ticks on the
      // background timer in scheduleFrame instead of rAF).
      //
      // N1 (findings 1/6): the prologue is SHARED code that runs BEFORE any
      // panel, so it gets its own breaker ('ingest') — same idiom as safePanel
      // for a panel. Un-guarded, a persistent throw here re-raised out of
      // frame() every frame (the finally re-arms but does not suppress) and
      // unwound before any panel painted. Guarded: caught, logged ONCE, and
      // surfaced on the header (stats dim; the connection chips stay bright — a
      // dead feed must never hide). Once quarantined the WS-fed stores keep
      // updating; only the flush-derived views stall, honestly, rather than the
      // whole frame aborting on every tick.
      safePanel('ingest', () => {
        flushBookLegs(now);   // T-2 (§4h): engine books → depth events (before the sampler reads them)
        sampleDepth();
        maybeEstimateLiq();
        maybeIntel();   // O-4 (§4d): confluence + alert evaluation on the 5s event-ts gate
      });

      // §4e.2: document.hidden pauses ALL painting (nobody is looking; browser
      // notifications from the alert engine cover the hidden-tab case) — the
      // same presentation-only rule as the pause button, and dirtyAll() on
      // visibilitychange repaints everything that moved the moment eyes return.
      if (document.hidden) return;

      // The header (with its conn chips) renders even while paused: pause
      // freezes the MARKET panels, never connection health — a paused page that
      // also froze its chips could hide a dead feed behind the pause button.
      if (due('header', now)) safePanel('header', () => {
        // §0 honesty rail note: no chip relabeling happens here anymore —
        // HeaderStatsView now renders the status MESSAGE the transport itself
        // supplied ('replay' from terminal-replay.js, 'live feed connected'
        // from livewire.js), so replay is labeled at the source instead of
        // being patched over after the fact. verify_terminal_browser.py still
        // asserts no chip ever says 'live' in replay.
        headerView.render({ marks, ois, statuses, sessionHigh, sessionLow, opening: openingSlice(), tickSize: settings.tick, base: BASE, nowMs: now });
        // T-2 (§4h): keep the open leg-manager's status dots live at the header
        // cadence (change-keyed inside — a no-op when nothing moved).
        if (legsPopOpen) renderLegRows();
        // §4g: decimals resolve one tick of the current grid — the fixed 1 dp
        // rendered every sub-$1 symbol's price as $0.0.
        const pxDp = settings.tick >= 1 ? 1 : Math.min(8, Math.ceil(-Math.log10(settings.tick)));
        priceEl.textContent = Number.isFinite(lastPrice)
          ? '$' + lastPrice.toLocaleString('en-US', { minimumFractionDigits: pxDp, maximumFractionDigits: pxDp })
          : '—';
      });

      if (!paused) {
        if (due('fp', now)) safePanel('fp', () => {
          // cvd: per-leg series map (§4b/§4h) — the view draws one labeled
          // line per trade leg, the exact Σ, and the bybit by-size bucket lines.
          const cvdExs = {};
          for (const ex of CVD_LEG_EXS) cvdExs[ex] = cvds[ex].series();
          const fpBars = footprint.bars();
          // I-1 (§4f): feed the absorption detector every NEWLY finished bar
          // (onBar wants each exactly once, in order — absFedT tracks it), then
          // compose the pro-footprint slice: zones + heuristic flags + cumΔ,
          // all from the tested pure builders.
          for (const b of fpBars) {
            if (b.finished && Number.isFinite(b.t) && b.t > absFedT) { absDet.onBar(b); absFedT = b.t; }
          }
          fpView.render({
            bars: fpBars,
            profile: profile.profile(),
            cvd: { exs: cvdExs },
            tickSize: settings.tick,
            nowMs: now,
            zones: S.stackedImbalances(fpBars, { k: 3, minRun: 3, tickSize: settings.tick, minVol: 1 }),
            absorb: absDet.events(),
            cum: S.cumDelta(fpBars),
            keyLevels: keyLevelMarks(),   // T-1 (§4g): registry + live-IB markers (toggle, default on)
          });
        });
        if (due('dom', now)) safePanel('dom', () => {
          // T-2 (§4h): source-selectable ladder. bybit·lin is the default and the
          // only source that carries the session sold/bought/Δ columns — those are
          // bybit FOOTPRINT data, and pouring another venue's book into bybit's
          // session volume would blend venues (§0.7), so the other sources render
          // book-only and the note says why. A source with no synced book yet (or a
          // disabled leg) shows an honest note instead of an empty ladder.
          renderDom();
        });
        if (due('agg', now)) safePanel('agg', () => {
          // T-2 (§4h): include filter (display-side merge; books keep ingesting)
          // + the honest per-leg depth-quality strip (synced/syncing/desync ×N/
          // disabled — resync counts visible by contract).
          aggView.render({
            grouped: aggBook.grouped(settings.tick, 14, aggIncludeExs()),
            tick: settings.tick,
            legQuality: legQualityRows(),
          });
        });
        if (due('spcvd', now)) safePanel('spcvd', () => {
          // T-2 (§4h): spot vs perp CVD strip — Σ enabled spot legs vs perp legs,
          // with the live composition; descriptive lead/lag, never a signal.
          spotPerpView.render({
            list: spotPerp.list(),
            latest: spotPerp.latest(),
            comp: spotPerpComp(),
            nowMs: now,
          });
        });
        if (due('tape', now)) safePanel('tape', () => {
          renderTape();   // T-3 (§4i): aggregated tiered tape + big-print rail
        });
        if (due('tapeint', now)) safePanel('tapeint', () => {
          tapeIntView.render({ stats: tapeInt.stats(), spark: tapeInt.sparkline() });
        });
        if (due('walls', now)) safePanel('walls', () => {
          wallsView.render({ entries: walls.list(), tickSize: settings.tick });
        });
        if (due('liq', now)) safePanel('liq', () => {
          // Wall-clock nowTs so the rolling 1m/5m sums DECAY during quiet spells
          // (the store's default anchor is the last event — replay-honest but a
          // live view wants live windows; LiqStore doc invites exactly this).
          liqView.render({
            tick: settings.tick,
            // Tier-tag each row upstream with the pure §4i classifier (the same
            // pattern as the tape's sizeTier tagging) — the view reads r.tier and
            // never re-derives a threshold.
            recent: liq.recent(40).map((l) => Object.assign({}, l, { tier: S.liqTier(l.notionalUsd) })),
            sum1m: liq.sumWindow(60000, now),
            sum5m: liq.sumWindow(300000, now),
          });
        });
        if (due('heat', now)) safePanel('heat', () => {
          const dh = depthHist[heatVenue];
          const dhSamples = dh.samples();
          bookHeatView.render({
            samples: dhSamples,
            range: dh.priceRange(),
            tickSize: settings.tick,
            trail: priceTrail[heatVenue],   // empty for binancef (no trades leg, §0.2) — honestly absent
            // Detector markers belong to the venue they were computed on: shown
            // on the BYBIT heatmap only (drawing bybit flags over another
            // venue's book would misattribute them, §0.7 per-source rail).
            events: heatVenue === 'bybit' ? detector.events() : [],
            ex: heatVenue,
            base: BASE,
            // I-1 (§4f): Asia/London/NY boxes over the sample span (pure UTC
            // arithmetic — deterministic in replay too).
            sessions: sessionBoxes(dhSamples),
          });
        });
        if (due('liqmap', now)) safePanel('liqmap', () => {
          liqHeatView.render({
            est: liqEst,
            mark: marks.bybit ? marks.bybit.mark : NaN,
            tickSize: settings.tick,
          });
        });
        if (due('det', now)) safePanel('det', () => {
          detView.render({ events: detector.events(), tickSize: settings.tick, base: BASE });
        });
        // O-3 STRUCTURE panels (§4c) — null views in replay (honest notes were
        // rendered instead; the dirty flags simply expire unread).
        if (histView && due('hist', now)) safePanel('hist', () => {
          // I-1 (§4f): + VWAP band series (AnchoredVwap samples) and the
          // LevelsView overlay — both composed here, painted by the view.
          histView.render({ bars: histBars, vwap: vwapSlice(), overlays: overlayLevels() });
        });
        if (tpoView && due('tpo', now)) safePanel('tpo', () => {
          tpoView.render({ sessions: tpoSessions, tickSize: tpoTick });
        });
        if (vpView && due('vp', now)) safePanel('vp', () => {
          vpView.render({ vp: vpData, tick: vpTick, lastPrice, interval: histInterval });
        });
        if (farbView && due('farb', now)) safePanel('farb', () => {
          farbView.render(farbSlice(now));
        });
        if (macroView && due('macro', now)) safePanel('macro', () => {
          macroView.render(macroSlice());
        });
        // T-1 (§4g): basis/funding mini-chart — store-fed, runs in replay too.
        if (due('basis', now)) safePanel('basis', () => {
          basisView.render({ list: basisSeries.list(), nowMs: now });
        });
        // I-1 AUCTION panels (§4f) — null views in replay (honest notes were
        // rendered instead; the dirty flags simply expire unread).
        if (auctionView && due('auct', now)) safePanel('auct', () => {
          auctionView.render({
            profile: auctionState.profile,
            delta: auctionState.delta,
            naked: nakedPocs(),   // the §4f naked-POC overlay is part of the panel, not toggle-gated
            label: auctionState.label,
            note: auctionState.note,
            status: auctionState.status,
            tick: LEVELS_TICK,
            bucketLabels: AUCTION_BUCKET_LABELS,
          });
        });
        if (levelsView && due('lvls', now)) safePanel('lvls', () => {
          // §4g: with another symbol selected the registry table shows the
          // honest note through the view's existing note path (days: null).
          levelsView.render(SYM === 'BTCUSDT'
            ? { days: levelsDays, note: apiUp === false ? API_OFFLINE_NOTE : levelsNote }
            : { days: null, note: byodSymNote() });
        });
        // T-1 (§4g): key-levels strip + VPIN — store/registry-fed, both modes
        // (the registry portion degrades to its honest note in replay/non-BTC).
        if (due('klev', now)) safePanel('klev', () => {
          klevView.render(klevSlice());
        });
        if (due('vpin', now)) safePanel('vpin', () => {
          vpinView.render({
            vpin: vpin ? vpin.vpin() : null,
            buckets: vpin ? vpin.buckets() : [],
            bucketVol: vpin ? vpin.bucketVol : NaN,
            note: vpin ? '' : 'estimating V from the first 5 min of session flow — VPIN accrues from arming (nothing backfilled)',
          });
        });
        if (microView && due('micro', now)) safePanel('micro', () => {
          const best = bybitBook.best();
          const mid = (best && best.bid && best.ask) ? (best.bid[0] + best.ask[0]) / 2 : NaN;
          const mpNow = S.microprice(bybitBook);
          microView.render({
            tickSize: settings.tick,
            ofi: ofi.series(60000),
            mp: mpHist,
            z: ofi.zscore(300),
            micro: mpNow === null ? NaN : mpNow,
            mid,
            imb: bookImb10(),
            nowMs: now,
          });
        });
        // O-4 INTELLIGENCE panels (§4d). REST-fed views are null in replay
        // (honest notes were rendered instead); conf/alerts run in both modes.
        if (screenerView && due('scr', now)) safePanel('scr', () => {
          const topN = settings.screenerTop === 'all' ? 0 : 40;   // buildScreener: topN ≤ 0 → whole universe
          const scr = S.buildScreener(tickerRows || [], { topN });
          screenerView.render({ rows: scr.rows, total: scr.total, topMode: settings.screenerTop, sym: SYM, base: BASE });
        });
        if (rsiView && due('rsi', now)) safePanel('rsi', () => {
          rsiView.render(rsiState);
        });
        if (optionsView && due('opts', now)) safePanel('opts', () => {
          // nowTs rides the slice (frame clock) — the view must not read
          // Date.now() itself for T-to-expiry (§4d GEX contract).
          optionsView.render({ chain: chainData, dvol: dvolVal, nowTs: now });
        });
        if (whaleView && due('whale', now)) safePanel('whale', () => {
          const entries = settings.whaleAddrs.map((a) => {
            const st = whaleState.get(a);
            return { addr: a, positions: st ? st.positions : undefined, ts: st ? st.ts : NaN };
          });
          whaleView.render({ entries, discovering: whaleDiscovering, note: whaleNote });
        });
        if (due('conf', now)) safePanel('conf', () => {
          confView.render({ conf: confData });
        });
        if (due('alerts', now)) safePanel('alerts', () => {
          alertsView.render({ events: alertEngine.events(), fresh: alertFresh.splice(0) });
        });
        // O-5 PORTFOLIO panels (§4e). Journal + calendar run in both modes
        // (localStorage-fed); poly/news/econ are null in replay (honest notes
        // were rendered instead). Stats/calendar recompute on render — pure
        // functions over ≤ a few hundred journal rows, gated by dirty.jour/cal
        // which only user actions flip.
        if (due('jour', now)) safePanel('jour', () => {
          journalView.render({
            trades: journalTrades,
            stats: S.journalStats(journalTrades),
            note: journalNote,
            importErrors,
          });
        });
        if (due('cal', now)) safePanel('cal', () => {
          calView.render({ cal: S.calendarReturns(journalTrades), nowMs: now });
        });
        if (polyView && due('poly', now)) safePanel('poly', () => {
          polyView.render({ events: polyEvents, nowMs: now });
        });
        if (newsView && due('news', now)) safePanel('news', () => {
          newsView.render({ items: newsItems, nowMs: now });
        });
        if (econView && due('econ', now)) safePanel('econ', () => {
          econView.render({ data: econData, nowMs: now });
        });
      }
    } finally {
      // N1: single re-arm site — runs on the document.hidden early return, on a
      // throw that escaped a panel wrapper (ingestion/shared code), and on the
      // normal path. The loop can no longer die.
      scheduleFrame();
    }
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
    dirty.auct = true;   // I-1: profile canvas re-measures; micro panes resize themselves in-view
  });
})();
