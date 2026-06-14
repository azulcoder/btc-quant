// app.js — btc-quant terminal: live public-data fetch + panel wiring.
//
// RESEARCH / BACKTEST ONLY. No orders, no API keys, no authenticated calls.
// All endpoints below are PUBLIC and unauthenticated:
//   - Coinbase Exchange candles  (api.exchange.coinbase.com)
//   - Kraken OHLC (fallback)     (api.kraken.com)
//   - CoinGecko market_chart     (api.coingecko.com)   spot context / fallback
//   - Bybit funding history      (api.bybit.com)       perp funding
//
// Browsers enforce CORS and these venues are sometimes geo-blocked or
// rate-limited. We try sources in order, fall back, and if everything fails
// we load nothing rather than fabricate — a visible STALE-DATA banner explains
// what happened. Never invent prices.
'use strict';

(function () {
  const Q = globalThis.Quant;
  const C = globalThis.Charts;

  // ─── Tiny HTTP helper: timeout + graceful failure ─────────────────────
  async function fetchJSON(url, { timeout = 12000 } = {}) {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), timeout);
    try {
      const res = await fetch(url, { signal: ctrl.signal, headers: { Accept: 'application/json' } });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return await res.json();
    } finally {
      clearTimeout(t);
    }
  }

  // ─── Data sources (public, keyless) ───────────────────────────────────

  // Coinbase candles: max 300/request, [time, low, high, open, close, volume],
  // newest-first. We PAGINATE backward in <=300-bar windows to assemble multi-
  // year history (a 200-bar MA needs ~200 bars just to warm up — 300 total
  // leaves a degenerate, often all-flat backtest). granSec is the bar width in
  // seconds: 86400 = 1d, 3600 = 1h. Hourly needs FAR more 300-bar windows to
  // span the same calendar span, so the loop guard scales with how many windows
  // `days` actually requires (a fixed 16-window cap silently truncated 1h to a
  // few days). We still bound it so a pathological request can't run forever.
  async function coinbaseCandles(product, days, granSec = 86400) {
    const gran = granSec, maxPer = 300;
    const nowSec = Math.floor(Date.now() / 1000);
    const earliest = nowSec - days * 86400; // `days` is always calendar days
    let endSec = nowSec;
    const byTime = new Map();
    // windows needed ≈ (total bars)/300; pad by 2 and clamp to a sane ceiling.
    const barsNeeded = Math.ceil((days * 86400) / gran);
    const maxWindows = Math.min(400, Math.ceil(barsNeeded / maxPer) + 2);
    for (let guard = 0; guard < maxWindows && endSec > earliest; guard++) {
      const startSec = Math.max(earliest, endSec - maxPer * gran);
      const url = `https://api.exchange.coinbase.com/products/${product}/candles?granularity=${gran}&start=${new Date(startSec * 1000).toISOString()}&end=${new Date(endSec * 1000).toISOString()}`;
      let rows;
      try { rows = await fetchJSON(url); }
      catch (e) { if (byTime.size) break; throw e; }   // keep what we already have
      if (!Array.isArray(rows) || !rows.length) break;
      for (const r of rows) byTime.set(r[0], r);
      const oldest = Math.min(...rows.map((r) => r[0]));
      if (oldest >= endSec) break;                     // no backward progress
      endSec = oldest - gran;
      if (rows.length < maxPer) break;                 // reached start of history
      await new Promise((res) => setTimeout(res, 120)); // gentle on the rate limit
    }
    return Array.from(byTime.values()).sort((a, b) => a[0] - b[0]);
  }

  // Pick fetch span by timeframe: ~1400 daily bars (~3.8y, enough to warm a
  // 200d MA across regimes) vs ~720 hourly bars (~30 days). Hourly history is
  // intentionally a RECENT window — the brief routes serious hourly work to the
  // Python engine; the dashboard 1h mode is for recent-window exploration.
  function granSec() { return state.gran === '1h' ? 3600 : 86400; }
  function fetchDays() { return state.gran === '1h' ? 30 : 1400; }

  async function fetchCoinbase(days = fetchDays()) {
    const rows = await coinbaseCandles('BTC-USD', days, granSec());
    if (!rows.length) throw new Error('empty coinbase');
    return {
      source: 'Coinbase',
      time: rows.map((r) => r[0] * 1000),
      open: rows.map((r) => r[3]),
      high: rows.map((r) => r[2]),
      low: rows.map((r) => r[1]),
      close: rows.map((r) => r[4]),
      volume: rows.map((r) => r[5]),
    };
  }

  // Kraken OHLC fallback. result.XXBTZUSD = [time, o, h, l, c, vwap, vol, count].
  // interval is MINUTES (60 = 1h, 1440 = 1d); pick it from the active timeframe
  // so a fallback never silently serves daily bars while we annualize hourly.
  async function fetchKraken() {
    const interval = state.gran === '1h' ? 60 : 1440;
    const url = `https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval=${interval}`;
    const data = await fetchJSON(url);
    if (data.error && data.error.length) throw new Error(data.error.join(';'));
    const key = Object.keys(data.result).find((k) => k !== 'last');
    const rows = data.result[key];
    if (!rows || !rows.length) throw new Error('empty kraken');
    return {
      source: 'Kraken',
      time: rows.map((r) => r[0] * 1000),
      open: rows.map((r) => +r[1]),
      high: rows.map((r) => +r[2]),
      low: rows.map((r) => +r[3]),
      close: rows.map((r) => +r[4]),
      volume: rows.map((r) => +r[6]),
    };
  }

  // CoinGecko fallback (close-only; we synthesize OHLC ≈ close so charts still
  // render — flagged as derived so we never pretend it's a real candle).
  async function fetchCoinGecko(days = 365) {
    const url = `https://api.coingecko.com/api/v3/coins/bitcoin/market_chart?vs_currency=usd&days=${days}&interval=daily`;
    const data = await fetchJSON(url);
    const prices = (data.prices || []).map((p) => p[1]);
    const time = (data.prices || []).map((p) => p[0]);
    const vol = (data.total_volumes || []).map((v) => v[1]);
    if (!prices.length) throw new Error('empty coingecko');
    return {
      source: 'CoinGecko (close-only)',
      time, open: prices.slice(), high: prices.slice(), low: prices.slice(),
      close: prices, volume: vol.length ? vol : prices.map(() => NaN),
      derivedOHLC: true,
    };
  }

  // ETH for the BTC–ETH pairs strategy (best-effort; pairs panel degrades if absent).
  // Paginated to match BTC history so the cointegration window spans real regimes.
  async function fetchETH(days = fetchDays()) {
    try {
      const rows = await coinbaseCandles('ETH-USD', days, granSec());
      if (rows.length) return { time: rows.map((r) => r[0] * 1000), close: rows.map((r) => r[4]) };
    } catch (_) { /* fall through */ }
    try {
      const data = await fetchJSON('https://api.coingecko.com/api/v3/coins/ethereum/market_chart?vs_currency=usd&days=365&interval=daily');
      return { time: (data.prices || []).map((p) => p[0]), close: (data.prices || []).map((p) => p[1]) };
    } catch (e) { return null; }
  }

  // Bybit funding history (perp). result.list newest-first; rate is a string.
  async function fetchFunding(limit = 200) {
    const url = `https://api.bybit.com/v5/market/funding/history?category=linear&symbol=BTCUSDT&limit=${limit}`;
    const data = await fetchJSON(url);
    const list = data && data.result && data.result.list;
    if (!Array.isArray(list) || !list.length) throw new Error('empty funding');
    const sorted = list.slice().sort((a, b) => +a.fundingRateTimestamp - +b.fundingRateTimestamp);
    return {
      source: 'Bybit',
      time: sorted.map((r) => +r.fundingRateTimestamp),
      rate: sorted.map((r) => +r.fundingRate),
    };
  }

  // Try OHLCV sources in order; return first success + a stale flag if all fail.
  // CoinGecko's public endpoint is daily-only, so it is excluded under 1h — we
  // refuse to serve daily bars while annualizing hourly (that is exactly the
  // silent-mis-annualization trap Phase 1 guards against). Coinbase and Kraken
  // both return genuine hourly bars when state.gran === '1h'.
  async function loadOHLCV() {
    const sources = state.gran === '1h'
      ? [fetchCoinbase, fetchKraken]
      : [fetchCoinbase, fetchKraken, fetchCoinGecko];
    const errors = [];
    for (const fn of sources) {
      try { return { data: await fn(), stale: false, errors }; }
      catch (e) { errors.push(fn.name + ': ' + e.message); }
    }
    return { data: null, stale: true, errors };
  }

  // ─── DOM helpers ──────────────────────────────────────────────────────
  const $ = (id) => document.getElementById(id);
  function setText(id, txt) { const n = $(id); if (n) n.textContent = txt; }
  function pct(x, dp = 2) { return Number.isFinite(x) ? (x * 100).toFixed(dp) + '%' : '—'; }
  function num(x, dp = 2) { return Number.isFinite(x) ? x.toFixed(dp) : '—'; }

  function showBanner(kind, msg) {
    const b = $('stale-banner');
    if (!b) return;
    state._bannerKind = msg ? (kind || 'warn') : null;   // so the age watchdog only re-arms when the banner is clear
    if (!msg) { b.hidden = true; return; }
    b.hidden = false;
    b.textContent = msg;
    b.className = 'stale-banner ' + (kind || 'warn');
  }

  // ─── §4.8 Per-panel "Updated HH:MM:SS" + stale/error treatment ──────────
  // Each data panel is a small state machine. On a SUCCESSFUL load we stamp the
  // panel and clear stale/error. On a FAILED refresh we toggle .panel.is-stale
  // (dim + amber chip, numbers stay visible) rather than wiping the rendered
  // panel — exactly the §4.8 "do not wipe to a skeleton on background refresh"
  // rule. `panelId` is the <section> id, `stampId` the .updated-at span id.
  function utcHMS() { return new Date().toISOString().slice(11, 19) + ' UTC'; }
  function fmtAge(ms) {
    const s = Math.round(ms / 1000);
    if (s < 90) return s + 's';
    const m = Math.round(s / 60);
    if (m < 90) return m + 'm';
    return Math.round(m / 60) + 'h';
  }
  function setPanelState(panelId, status, stampId, ageMs) {
    const panel = $(panelId);
    if (panel) {
      panel.classList.toggle('is-stale', status === 'stale');
      panel.classList.toggle('is-error', status === 'error');
      // One stale/error chip per panel, created on demand so every panel is uniform
      // (some markup shipped a .stale-chip, some did not). CSS reveals it only when
      // the panel is .is-stale/.is-error. Age text makes silent aging visible.
      let chip = panel.querySelector('.stale-chip');
      if (!chip && (status === 'stale' || status === 'error')) {
        chip = document.createElement('span');
        chip.className = 'stale-chip';
        (panel.querySelector('h2') || panel).appendChild(chip);
      }
      if (chip) {
        if (status === 'stale') chip.textContent = Number.isFinite(ageMs) ? 'stale · ' + fmtAge(ageMs) + ' old' : 'stale';
        else if (status === 'error') chip.textContent = 'unavailable';
        else chip.textContent = '';            // ready → clear so no stale text lingers (even though CSS hides it)
      }
    }
    if (stampId) {
      const stamp = $(stampId);
      if (stamp) {
        // Only advance the timestamp on a fresh, successful load. On failure we
        // KEEP the last-good "updated …" so the user sees how old the data is.
        if (status === 'ready') stamp.textContent = 'updated ' + utcHMS();
        else if (status === 'error' && !stamp.textContent) stamp.textContent = 'no data yet';
      }
    }
  }
  // Record a feed's last successful load (drives the staleness watchdog).
  function markFeed(key) { state.feedAt[key] = Date.now(); }

  // ─── State ────────────────────────────────────────────────────────────
  // state.gran is the bar timeframe ('1d' | '1h'). It drives BOTH the candle
  // fetch granularity AND the annualization factor everywhere returns are
  // annualized. Threading one ppy() through every site is the fix for the bug
  // that forced the 1h selector's removal (a literal 365 left at any
  // annualization site silently mis-annualizes hourly Sharpe/vol by sqrt(24)).
  const state = { ohlcv: null, eth: null, funding: null, gran: '1d', optionChain: null, feedAt: {}, _bannerKind: null, ohlcvLastMs: NaN, ohlcvStaleH: 36 };

  // Bars per year for annualization — mirrors the Python engine's
  // run_backtest._periods_per_year (24*365 hourly, 365 daily). 24/7 crypto, so
  // no trading-calendar discount. The dashboard self-test (--check) asserts
  // this mapping matches Python and that no literal 365 survives at an
  // annualization site while gran=1h.
  function ppy() { return state.gran === '1h' ? 24 * 365 : 365; }

  // ─── Strategy registry (mirrors strategies.py ranked first cut) ───────
  const STRATEGIES = {
    buy_and_hold: {
      label: 'Buy & Hold (baseline)',
      tag: 'BASELINE',
      build: (o) => Q.sigBuyAndHold(o.close),
      note: 'The benchmark every strategy is scored against, net of cost.',
    },
    ma_trend: {
      label: 'MA trend filter (200 bars)',
      tag: '[Practitioner]',
      build: (o) => Q.sigMaTrend(o.close, 200),
      note: 'Long above the 200-bar SMA, else flat. Risk management (drawdown control), not alpha. Grayscale; Glucksmann 2019.',
    },
    ma_cross: {
      label: 'Golden cross (50/200 bars)',
      tag: '[Practitioner]',
      build: (o) => Q.sigMaCross(o.close, 50, 200),
      note: 'Long when SMA50 > SMA200. Very few independent signals → low statistical power, high overfit risk.',
    },
    tsmom: {
      label: 'TSMOM (20 bars, vol-scaled)',
      tag: '[Mixed]',
      build: (o, p) => Q.sigTsmom(o.close, 20, { volScaled: true, longOnly: true, targetVol: 0.15, periodsPerYear: p }),
      note: 'Short-lookback time-series momentum. Cost-fragile: break-even ~3–10 bps (Shen-Urquhart-Wang 2022). Watch the net vs gross gap.',
    },
    tsmom_ls: {
      label: 'TSMOM long/short (20 bars)',
      tag: '[Mixed]',
      build: (o, p) => Q.sigTsmom(o.close, 20, { volScaled: true, longOnly: false, targetVol: 0.15, periodsPerYear: p }),
      note: 'Long/short variant. Shorting BTC has its own funding/borrow cost not modelled here — treat as a teaching case.',
    },
    ma_voltarget: {
      label: 'MA trend + vol target',
      tag: '[Mixed; tail control]',
      build: (o, p) => Q.applyVolTarget(Q.sigMaTrend(o.close, 200), o.close, { targetVol: 0.15, cap: 2, periodsPerYear: p }),
      note: 'Vol-targeting sizing layer (Harvey 2018) on the 200-bar trend. Durable benefit is tail control, not Sharpe — BTC vol-return link is unstable.',
    },
    pairs: {
      label: 'BTC–ETH pairs (z-score)',
      tag: '[Mixed]',
      build: null, // handled specially: needs ETH
      note: 'Cointegration spread reversion with a de-cointegration stop. Published Sharpes are in-sample and fragile (Krauss 2017).',
    },
  };

  // ─── Run a strategy + render every panel ──────────────────────────────
  function runStrategy(key) {
    const o = state.ohlcv;
    if (!o) return;
    const spec = STRATEGIES[key] || STRATEGIES.buy_and_hold;
    setText('strat-note', spec.note);
    setText('strat-tag', spec.tag);
    setText('status-strategy', spec.label || key);
    setText('status-tf', state.gran === '1h' ? '1h · hourly' : '1d · daily');

    const p = ppy();
    let positions, zSeries = null;
    if (key === 'pairs') {
      if (!state.eth || state.eth.close.length < 60) {
        setText('strat-note', spec.note + '  (ETH data unavailable — pairs panel disabled.)');
        positions = Q.sigBuyAndHold(o.close); // fall back to baseline so panels still render
      } else {
        // align ETH to BTC length (trailing overlap)
        const m = Math.min(o.close.length, state.eth.close.length);
        const r = Q.sigPairs(o.close.slice(-m), state.eth.close.slice(-m), { window: 60, entry: 2, exit: 0.5, stop: 3.5 });
        positions = new Array(o.close.length - m).fill(0).concat(r.positions);
        zSeries = new Array(o.close.length - m).fill(NaN).concat(r.z);
      }
    } else {
      positions = spec.build(o, p);
    }

    const costBps = +($('cost-bps') ? $('cost-bps').value : 10);
    const slipBps = +($('slip-bps') ? $('slip-bps').value : 2);
    // Full-history backtest = the DESCRIPTIVE curve/CAGR/Sharpe/maxDD context only. Its own
    // deflated Sharpe is no longer surfaced — the headline DSR comes from the walk-forward OOS
    // leaderboard row (single source of truth), so no DSR-tuning params are needed here.
    const bt = Q.backtest(positions, o.close, {
      costBps, slippageBps: slipBps, periodsPerYear: p,
    });
    state.last = { o, bt, zSeries, p, key };   // cached so a tab switch can re-render without recomputing

    // Leaderboard FIRST: it owns the walk-forward OOS stats. The Performance panel + KPI hero
    // read the selected strategy's row straight from this map, so the hero Deflated Sharpe is
    // LITERALLY the leaderboard row — not a parallel recompute that could drift back apart.
    const lbMap = renderLeaderboard(o);
    const oos = lbMap[key] || null;
    renderStats(bt, key, p, oos);
    renderKpiStrip(bt, key, p, oos);
    renderCharts(o, bt, zSeries, p);
    // Live candle panel reflects the CURRENT backtest's markers + stop/target,
    // on cached OHLC + the live WS tail (§3.2). Context only — never the backtest series.
    renderLiveCandle(o, bt);
    setText('live-candle-summary',
      `Cached ${o.source} ${state.gran} bars (last ${Math.min(o.close.length, 240)} shown) + live Coinbase tick on the current bar · markers/lines from "${(STRATEGIES[key] || {}).label || key}". Illustrative ±2-bar-ATR stop/target — a visual risk frame, NOT a live order.`);
  }

  function renderStats(bt, key, p, oos) {
    const s = bt.stats;
    setText('stat-net-cagr', pct(s.cagr));
    setText('stat-bh-cagr', pct(s.bhCagr));
    setText('stat-sharpe', num(s.sharpe));
    setText('stat-bh-sharpe', num(s.bhSharpe));
    setText('stat-sortino', num(s.sortino));
    setText('stat-vol', pct(s.volatility));
    setText('stat-mdd', pct(s.maxDrawdown));
    setText('stat-bh-mdd', pct(s.bhMaxDrawdown));
    setText('stat-calmar', num(s.calmar));
    setText('stat-hit', pct(s.hitRate));
    setText('stat-trades', String(bt.trades));
    setText('stat-skew', num(s.skew));
    setText('stat-kurt', num(s.kurtosis));

    // ── The honest headline: walk-forward OUT-OF-SAMPLE deflated Sharpe, read straight from
    // the leaderboard's per-strategy map (single source of truth). The IS→OOS Sharpe pair
    // mirrors the leaderboard's two Sharpe columns; the old anchored 70/30 split is gone.
    // If the strategy/timeframe is too short to walk forward, we degrade HONESTLY — we never
    // fall back to the in-sample number (that would resurrect the very inconsistency we killed).
    const oosStats = oos && oos.oosStats ? oos.oosStats : null;
    setText('stat-sr-is', num(s.sharpe));                                  // full-history (in-sample) net Sharpe
    setText('stat-sr-oos', oosStats ? num(oosStats.sharpe) : '—');
    setText('stat-psr', oosStats ? pct(oosStats.probabilisticSharpe, 1) : '—');
    setText('stat-dsr', oosStats ? pct(oosStats.deflatedSharpe, 1) : '— · insufficient history for OOS');
    setText('stat-ntrials', oosStats ? String(oosStats.nTrials) : '—');

    // Verdict line — the honest read, now OUT-OF-SAMPLE.
    const dsr = oosStats ? oosStats.deflatedSharpe : NaN;
    const nT = oosStats ? oosStats.nTrials : NaN;
    const beatsBH = s.sharpe > s.bhSharpe;
    let verdict, vclass;
    if (key === 'buy_and_hold') { verdict = 'This IS the baseline. Every other strategy must beat it net of cost, out-of-sample.'; vclass = 'neutral'; }
    else if (!oosStats) { verdict = 'Insufficient history to walk this strategy forward on the current timeframe — no out-of-sample deflated Sharpe to report (try the daily timeframe or a longer window). The in-sample number is deliberately NOT shown as a substitute.'; vclass = 'warn'; }
    else if (Number.isFinite(dsr) && dsr > 0.95) { verdict = 'Deflated Sharpe > 0.95, walk-forward out-of-sample: survives the multiple-testing deflation OUT-OF-SAMPLE. Still verify live.'; vclass = 'good'; }
    else { verdict = `Deflated Sharpe ${pct(dsr, 0)} ≤ 95%, walk-forward out-of-sample: NOT distinguishable from luck after deflating for ${nT} trials. ${beatsBH ? 'Beats B&H Sharpe in-sample but' : 'Does not beat B&H and'} treat as noise.`; vclass = 'warn'; }
    setText('verdict', verdict);
    const v = $('verdict'); if (v) v.className = 'verdict ' + vclass;

    // Highlight the net-vs-gross gap (the cost-fragility tell).
    const grossSharpe = Q.sharpe(bt.grossReturns, p);
    setText('stat-gross-sharpe', num(grossSharpe));
  }

  // Headline KPI strip: the deflated Sharpe is the hero; secondaries carry the
  // B&H delta + a sparkline. The Performance panel below holds the full detail.
  function renderKpiStrip(bt, key, p, oos) {
    const s = bt.stats;
    const isBH = key === 'buy_and_hold';
    const ds = (a, n = 72) => {                       // downsample for a tiny sparkline
      const f = (a || []).filter(Number.isFinite);
      if (f.length <= n) return f;
      const k = Math.ceil(f.length / n);
      return f.filter((_, i) => i % k === 0);
    };
    // Hero — walk-forward OUT-OF-SAMPLE deflated Sharpe, read straight from the leaderboard's
    // selected-strategy row (same float, never a recompute). Amber by default = "treat as noise";
    // green only if it actually clears 0.95 OOS. Honest framing: the curve is NOT the headline.
    const oosStats = oos && oos.oosStats ? oos.oosStats : null;
    const dsr = oosStats ? oosStats.deflatedSharpe : NaN;
    const nT = oosStats ? oosStats.nTrials : NaN;
    setText('kpi-dsr', oosStats ? pct(dsr, 1) : '—');
    setText('kpi-ntrials', oosStats ? String(nT) : '—');
    const sig = Number.isFinite(dsr) && dsr > 0.95;
    const hero = $('kpi-hero'); if (hero) hero.classList.toggle('is-sig', sig);
    setText('kpi-dsr-verdict', isBH
      ? 'The baseline. Every strategy is measured against this, net of cost, out-of-sample.'
      : !oosStats ? 'Insufficient history for a walk-forward out-of-sample test on this timeframe — no deflated Sharpe to report. The in-sample number is deliberately not shown.'
      : sig ? 'Above 0.95 — survives the multiple-testing deflation OUT-OF-SAMPLE (walk-forward). Verify live before believing it.'
            : `At/below 0.95 — not distinguishable from luck after deflating for ${nT} trials, walk-forward out-of-sample. Treat as noise, not alpha.`);
    // Secondary KPIs — value sign-coloured, with the B&H delta arrow.
    const setV = (id, txt, cls) => { const e = $(id); if (e) { e.textContent = txt; e.classList.remove('pos', 'neg'); if (cls) e.classList.add(cls); } };
    const setD = (id, txt, dir) => { const e = $(id); if (e) { e.textContent = txt; e.classList.remove('up', 'down'); if (dir) e.classList.add(dir); } };
    setV('kpi-sharpe', num(s.sharpe), s.sharpe >= 0 ? 'pos' : 'neg');
    setD('kpi-sharpe-d', 'B&H ' + num(s.bhSharpe), isBH ? '' : (s.sharpe > s.bhSharpe ? 'up' : 'down'));
    setV('kpi-cagr', pct(s.cagr), s.cagr >= 0 ? 'pos' : 'neg');
    setD('kpi-cagr-d', 'B&H ' + pct(s.bhCagr), isBH ? '' : (s.cagr > s.bhCagr ? 'up' : 'down'));
    setV('kpi-mdd', pct(s.maxDrawdown), 'neg');
    setD('kpi-mdd-d', 'B&H ' + pct(s.bhMaxDrawdown), isBH ? '' : (s.maxDrawdown > s.bhMaxDrawdown ? 'up' : 'down')); // less-negative = better
    setText('kpi-psr', oosStats ? pct(oosStats.probabilisticSharpe, 1) : '—');   // OOS, to match the hero DSR + panel
    // Sparklines (static downsampled history — never a live tick, §3.5).
    if (C.sparkline) {
      C.sparkline($('kpi-hero-spark'), ds(bt.equity), { color: sig ? 'var(--up)' : 'var(--accent)' });
      C.sparkline($('kpi-sharpe-spark'), ds(Q.rollingSharpe(bt.returns, 90, p)), { baseline: 0, color: 'var(--accent-2)' });
      C.sparkline($('kpi-cagr-spark'), ds(bt.equity), { color: s.cagr >= 0 ? 'var(--up)' : 'var(--down)' });
      C.sparkline($('kpi-mdd-spark'), ds(Q.drawdownSeries(bt.equity)), { color: 'var(--down)' });
    }
  }

  function renderCharts(o, bt, zSeries, p) {
    const bars = state.gran === '1h' ? 'bars' : 'd';
    setText('candles-heading', `BTC-USD ${state.gran === '1h' ? 'hourly' : 'daily'} candles + SMA 50 / 200 (bars)`);
    const ma50 = Q.sma(o.close, 50);
    const ma200 = Q.sma(o.close, 200);
    // Backtest/equity use the FULL paginated history; the candle panel shows only
    // the most recent window so it stays legible (and the 200d MA is populated).
    const N = o.close.length, W = Math.min(N, 400), s0 = N - W, sl = (a) => a.slice(s0);
    const oWin = {
      source: o.source, derivedOHLC: o.derivedOHLC,
      time: sl(o.time), open: sl(o.open), high: sl(o.high), low: sl(o.low),
      close: sl(o.close), volume: o.volume ? sl(o.volume) : undefined,
    };
    C.candles($('chart-candles'), oWin, {
      height: 260,
      overlays: [
        { values: sl(ma50), color: 'var(--c1)', label: 'SMA50' },
        { values: sl(ma200), color: 'var(--accent-2)', label: 'SMA200' },
      ],
    });

    C.lineChart($('chart-equity'), [
      { values: bt.bhEquity, color: 'var(--muted)', label: 'Buy & Hold', dash: '5 4' },
      { values: Q.compound(bt.grossReturns), color: 'var(--accent-2)', label: 'Strategy (gross)', width: 1.2 },
      { values: bt.equity, color: 'var(--c1)', label: 'Strategy (net)' },
    ], { height: 240, baseline: 1, fmt: (v) => v.toFixed(2) + 'x' });

    C.drawdownArea($('chart-drawdown'), Q.drawdownSeries(bt.equity), { height: 150, dates: o.time });

    C.histogram($('chart-hist'), bt.returns.filter((r) => r !== 0), { height: 170, bins: 35 });

    const rollVol = Q.realizedVol(bt.returns, 30, p);
    const rollSharpe = Q.rollingSharpe(bt.returns, 90, p);
    C.rollingChart($('chart-rolling'), [
      { values: rollVol, color: 'var(--down)', label: `Rolling vol (30 ${bars}, ann.)` },
      { values: rollSharpe, color: 'var(--c1)', label: `Rolling Sharpe (90 ${bars})` },
    ], { height: 180 });
  }

  function renderFunding() {
    const f = state.funding;
    const root = $('chart-funding');
    if (!root) return;
    if (!f || !f.rate.length) {
      // Keep any previously-rendered chart visible and mark the panel stale
      // (§4.8) rather than wiping it; only show the empty message on first load.
      if (!root.querySelector('.chart-svg')) {
        root.innerHTML = '<div class="chart-na">Funding unavailable (Bybit CORS/geo/rate-limit). Perp-only metric — backtests above use spot OHLCV only.</div>';
        setText('funding-summary', 'no funding data');
        setPanelState('panel-funding', 'error', 'funding-updated');
      } else {
        setPanelState('panel-funding', 'stale', 'funding-updated');
      }
      return;
    }
    C.fundingBars(root, f.rate, { height: 160 });
    setPanelState('panel-funding', 'ready', 'funding-updated'); markFeed('funding');
    const avg = Q.mean(f.rate);
    const annual = avg * 3 * 365; // 8h intervals → 3/day
    const negShare = f.rate.filter((r) => r < 0).length / f.rate.length;
    setText('funding-summary',
      `mean ${(avg * 100).toFixed(4)}%/8h ≈ ${(annual * 100).toFixed(1)}% APR  ·  negative ${(negShare * 100).toFixed(0)}% of intervals`);

    // Carry sleeve backtest on the realized funding stream.
    const carry = Q.carryBacktest(f.rate, { costBps: 4, threshold: 0 });
    const carryAnn = Q.mean(carry.net) * 3 * 365;
    setText('carry-summary',
      `Carry sleeve (short perp when funding > 0, ${'≈'}delta-neutral, 4bps/rebalance): net ${(carryAnn * 100).toFixed(1)}% APR over the window. Inverts when funding < 0 — the FTX-2022 / 2025 risk.`);

    // OOS-insufficient (B3, RESEARCH-partB-runlog.md): carry is a funding-stream sleeve, not a
    // price-position strategy, and our keyless history is far below MinBTL → descriptive only,
    // never an OOS Deflated Sharpe or a leaderboard slot. (Honest by construction.)
    const fN = f.rate.length;
    const fYears = fN / (3 * 365);                  // 8h funding intervals → 3/day
    const mbN = Object.keys(STRATEGIES).length;     // same trial count the leaderboard deflates for
    const mb = Q.minBacktestLength(mbN);
    setText('carry-oos',
      `OOS-insufficient: ${fN} funding intervals ≈ ${fYears.toFixed(2)} yr of keyless history ≪ MinBTL ~${Number.isFinite(mb) ? mb.toFixed(1) : '—'} yr (N=${mbN}). A funding-stream sleeve, not a price strategy — shown DESCRIPTIVELY, never given an out-of-sample Deflated Sharpe or a leaderboard slot.`);
  }

  // ─── Strategy leaderboard: run EVERY strategy, rank by deflated Sharpe ──
  // ─── Perpetual extras: open interest, basis/premium, long/short ratio ────
  // All Bybit v5 public/keyless. Positioning + carry context, kept honest:
  // funding/basis is the (risk-premium) carry SIGNAL; OI + L/S are DESCRIPTIVE.
  async function fetchOpenInterest() {
    try {
      const d = await fetchJSON('https://api.bybit.com/v5/market/open-interest?category=linear&symbol=BTCUSDT&intervalTime=1d&limit=60');
      const l = (d && d.result && d.result.list) || [];
      if (!l.length) throw new Error('empty oi');
      const s = l.slice().sort((a, b) => +a.timestamp - +b.timestamp);
      return { time: s.map((r) => +r.timestamp), oi: s.map((r) => +r.openInterest) };
    } catch (_) { return null; }
  }
  async function fetchLongShort() {
    try {
      const d = await fetchJSON('https://api.bybit.com/v5/market/account-ratio?category=linear&symbol=BTCUSDT&period=1d&limit=40');
      const l = (d && d.result && d.result.list) || [];
      if (!l.length) throw new Error('empty ls');
      const s = l.slice().sort((a, b) => +a.timestamp - +b.timestamp);
      return { time: s.map((r) => +r.timestamp), buy: s.map((r) => +r.buyRatio) };
    } catch (_) { return null; }
  }
  async function fetchPerpTicker() {
    try {
      const d = await fetchJSON('https://api.bybit.com/v5/market/tickers?category=linear&symbol=BTCUSDT');
      const r = d && d.result && d.result.list && d.result.list[0];
      if (!r) throw new Error('empty ticker');
      return { last: +r.lastPrice, mark: +r.markPrice, index: +r.indexPrice, funding: +r.fundingRate, nextFunding: +r.nextFundingTime, oi: +r.openInterest, oiValue: +r.openInterestValue };
    } catch (_) { return null; }
  }

  function renderOpenInterest() {
    const root = $('chart-oi'); if (!root) return;
    const oi = state.oi;
    if (!oi || !oi.oi.length) { root.innerHTML = '<div class="chart-na">Open interest unavailable (Bybit CORS/geo/rate-limit).</div>'; setText('oi-summary', 'no OI data'); return; }
    C.lineChart(root, [{ values: oi.oi, color: 'var(--accent-2)', label: 'Open interest (BTC)' }], { height: 170, fmt: (v) => (v / 1e3).toFixed(0) + 'k' });
    const latest = oi.oi[oi.oi.length - 1], first = oi.oi[0];
    const chg = first ? (latest / first - 1) * 100 : NaN;
    const t = state.perpTicker;
    const notional = t && Number.isFinite(t.oiValue) ? t.oiValue : (t ? latest * t.mark : NaN);
    setText('oi-summary', `latest ${(latest / 1e3).toFixed(1)}k BTC`
      + (Number.isFinite(notional) ? ` ≈ $${(notional / 1e9).toFixed(2)}B notional` : '')
      + (Number.isFinite(chg) ? ` · ${chg >= 0 ? '+' : ''}${chg.toFixed(0)}% over ${oi.oi.length} days` : ''));
    setPanelState('panel-oi', 'ready', 'oi-updated'); markFeed('oi');
  }

  function renderLongShort() {
    const root = $('chart-lsratio'); if (!root) return;
    const ls = state.ls;
    if (!ls || !ls.buy.length) { root.innerHTML = '<div class="chart-na">Long/short ratio unavailable (Bybit CORS/geo/rate-limit).</div>'; setText('ls-summary', 'no long/short data'); return; }
    const longPct = ls.buy.map((b) => b * 100);
    C.lineChart(root, [{ values: longPct, color: 'var(--accent-2)', label: '% accounts net-long' }], { height: 160, baseline: 50, fmt: (v) => v.toFixed(0) + '%' });
    const latest = longPct[longPct.length - 1];
    setText('ls-summary', `${latest.toFixed(0)}% of accounts net-long`
      + (latest > 60 ? ' — crowded long (contrarian caution)' : latest < 40 ? ' — crowded short (contrarian caution)' : ' — roughly balanced')
      + '. Retail account-ratio is a weak, noisy signal.');
    setPanelState('panel-lsratio', 'ready', 'ls-updated'); markFeed('ls');
  }

  function renderBasis() {
    const root = $('basis-grid'); if (!root) return;
    const t = state.perpTicker;
    if (!t) { root.innerHTML = '<div class="chart-na">Perp ticker unavailable (Bybit CORS/geo/rate-limit).</div>'; return; }
    const premium = Number.isFinite(t.index) && t.index ? (t.mark / t.index - 1) : NaN;   // perp mark vs spot index
    const fundingApr = Number.isFinite(t.funding) ? t.funding * 3 * 365 : NaN;             // 8h funding → APR
    const mins = Number.isFinite(t.nextFunding) ? Math.max(0, (t.nextFunding - Date.now()) / 60000) : NaN;
    const nextStr = Number.isFinite(mins) ? (mins >= 60 ? (mins / 60).toFixed(1) + 'h' : mins.toFixed(0) + 'm') : '—';
    const usd = (x) => '$' + (x || 0).toLocaleString(undefined, { maximumFractionDigits: 0 });
    const cell = (k, v, cls) => `<div class="stat"><span class="k">${k}</span><span class="v num${cls ? ' ' + cls : ''}">${v}</span></div>`;
    root.innerHTML =
      cell('Last / Mark', usd(t.last))
      + cell('Spot index', usd(t.index))
      + cell('Premium (mark−index)', Number.isFinite(premium) ? (premium * 100).toFixed(3) + '%' : '—', premium >= 0 ? 'pos' : 'neg')
      + cell('Funding / 8h', Number.isFinite(t.funding) ? (t.funding * 100).toFixed(4) + '%' : '—', t.funding >= 0 ? 'pos' : 'neg')
      + cell('Funding APR', Number.isFinite(fundingApr) ? (fundingApr * 100).toFixed(1) + '%' : '—', fundingApr >= 0 ? 'pos' : 'neg')
      + cell('Next funding in', nextStr)
      + cell('Open interest', Number.isFinite(t.oi) ? (t.oi / 1e3).toFixed(1) + 'k BTC' : '—')
      + cell('OI notional', Number.isFinite(t.oiValue) ? '$' + (t.oiValue / 1e9).toFixed(2) + 'B' : '—');
    setPanelState('panel-basis', 'ready', 'basis-updated'); markFeed('basis');
  }

  function renderLeaderboard(o) {
    const body = $('leaderboard-body');
    if (!body || !o) return;
    const p = ppy();
    const costBps = +($('cost-bps') ? $('cost-bps').value : 10);
    const slipBps = +($('slip-bps') ? $('slip-bps').value : 2);
    const keys = Object.keys(STRATEGIES);
    const nTrials = keys.length;   // selection-count deflation (best of this many)
    const rows = [];
    const oosCols = [];            // {key, ret, positions} for the PBO/CPCV matrix
    const lbMap = {};              // key → { oosStats, isSharpe } — single source for the Performance panel
    let bhOosSharpe = NaN;
    for (const key of keys) {
      try {
        let positions;
        if (key === 'pairs') {
          if (!state.eth || state.eth.close.length < 60) continue;
          const m = Math.min(o.close.length, state.eth.close.length);
          const r = Q.sigPairs(o.close.slice(-m), state.eth.close.slice(-m), { window: 60, entry: 2, exit: 0.5, stop: 3.5 });
          positions = new Array(o.close.length - m).fill(0).concat(r.positions);
        } else {
          positions = STRATEGIES[key].build(o, p);
        }
        // In-sample full-history (for the IS Sharpe contrast) + walk-forward OOS (the rank basis).
        const is = Q.backtest(positions, o.close, { costBps, slippageBps: slipBps, periodsPerYear: p });
        const wf = Q.walkForward(positions, o.close, { folds: 5, costBps, slippageBps: slipBps, periodsPerYear: p, nTrials });
        // Record into the map BEFORE any skip, so the Performance panel reads the exact same
        // OOS stats this leaderboard row uses (or null → the panel degrades, never falls back to IS).
        lbMap[key] = { oosStats: wf.oosStats || null, isSharpe: is.stats.sharpe };
        if (!wf.oosStats) continue;
        const s = wf.oosStats;
        oosCols.push({ key, ret: wf.oosReturns, positions });
        if (key === 'buy_and_hold') bhOosSharpe = s.sharpe;
        rows.push({ key, label: STRATEGIES[key].label, cagr: s.cagr, isSharpe: is.stats.sharpe,
          oosSharpe: s.sharpe, dsr: s.deflatedSharpe, mdd: s.maxDrawdown });
      } catch (_) { /* skip a strategy that can't run on this data */ }
    }
    const dval = (r) => (Number.isFinite(r.dsr) ? r.dsr : -9);
    rows.sort((a, b) => dval(b) - dval(a));
    body.innerHTML = rows.map((r) => {
      const beats = r.key === 'buy_and_hold' ? '—' : (r.oosSharpe > bhOosSharpe ? 'yes' : 'no');
      const good = Number.isFinite(r.dsr) && r.dsr > 0.95;
      const cls = r.key === 'buy_and_hold' ? ' class="baseline-row"' : '';
      return `<tr${cls}><td>${r.label}</td><td class="num">${pct(r.cagr)}</td>`
        + `<td class="num" style="color:var(--muted)">${num(r.isSharpe)}</td>`
        + `<td class="num">${num(r.oosSharpe)}</td>`
        + `<td class="num" style="color:${good ? 'var(--up)' : 'var(--muted)'}">${pct(r.dsr, 0)}</td>`
        + `<td class="num">${pct(r.mdd)}</td>`
        + `<td class="num">${beats}</td></tr>`;
    }).join('');

    // Selection-overfit guards: PBO across the OOS matrix + MinBTL + CPCV(top strategy).
    const guards = $('leaderboard-guards');
    if (guards) {
      let html = '';
      if (oosCols.length >= 2) {
        const minLen = Math.min.apply(null, oosCols.map((c) => c.ret.length));
        const pb = Q.pbo(oosCols.map((c) => c.ret.slice(0, minLen)), { nBlocks: 8 });
        const years = (o.time && o.time.length > 1) ? (o.time[o.time.length - 1] - o.time[0]) / (365.25 * 86400000) : NaN;
        const minbtl = Q.minBacktestLength(nTrials);
        const short = Number.isFinite(minbtl) && Number.isFinite(years) && years < minbtl;
        const top = rows.find((r) => r.key !== 'buy_and_hold');
        let cpStr = '';
        if (top) {
          const col = oosCols.find((c) => c.key === top.key);
          if (col) {
            const cp = Q.cpcv(col.positions, o.close, { periodsPerYear: p, costBps, slippageBps: slipBps });
            if (cp.nPaths) cpStr = ` &nbsp;·&nbsp; CPCV OOS Sharpe (${top.label}, top): median <b>${num(cp.median)}</b> [${num(cp.p25)}, ${num(cp.p75)}] over ${cp.nPaths} paths`;
          }
        }
        html = `PBO (selection overfit, CSCV ${pb.nCombos} splits): <b>${Number.isFinite(pb.pbo) ? (pb.pbo * 100).toFixed(0) + '%' : '—'}</b> <span class="hint">[&gt;50% = ranking is noise]</span>`
          + ` &nbsp;·&nbsp; MinBTL for N=${nTrials}: <b>${Number.isFinite(minbtl) ? minbtl.toFixed(1) : '—'}y</b> vs ${Number.isFinite(years) ? years.toFixed(1) : '—'}y data ${short ? '<b style="color:var(--down)">⚠ under-powered</b>' : '<span class="hint">(ok)</span>'}`
          + cpStr;
      }
      guards.innerHTML = html;
    }
    return lbMap;
  }

  // ─── Variance risk premium: Deribit DVOL (implied) vs realized vol ──────
  async function loadVrp() {
    try {
      const end = Date.now(), start = end - 365 * 86400000;
      const url = `https://www.deribit.com/api/v2/public/get_volatility_index_data?currency=BTC&start_timestamp=${start}&end_timestamp=${end}&resolution=1D`;
      const d = await fetchJSON(url);
      const rows = (d && d.result && d.result.data) || [];
      if (!rows.length) throw new Error('empty dvol');
      return { time: rows.map((r) => r[0]), iv: rows.map((r) => r[4]) }; // [ts,o,h,l,c]; close = DVOL %
    } catch (_) { return null; }
  }
  function renderVrp(o) {
    const root = $('chart-vrp');
    if (!root) return;
    const dvol = state.dvol;
    if (!dvol || !dvol.iv.length || !o) {
      if (!root.querySelector('.chart-svg')) {
        root.innerHTML = '<div class="chart-na">Deribit DVOL unavailable (CORS/geo/rate-limit). Implied vol is option-derived; spot backtests above are unaffected.</div>';
        setText('vrp-summary', 'no implied-vol data');
        setPanelState('panel-vrp', 'error', 'vrp-updated');
      } else {
        setPanelState('panel-vrp', 'stale', 'vrp-updated');
      }
      return;
    }
    const rets = o.close.map((c, i) => (i ? Math.log(c / o.close[i - 1]) : 0));
    // Annualize realized vol at the loaded bar frequency (ppy()), then match to
    // DVOL by ISO date. DVOL is a daily 30-day index; on 1d this is a clean
    // 30-bar=30-day comparison, on 1h the realized series is intraday and noisier
    // (the brief routes serious vol work to the Python engine) — but the
    // annualization itself must never be a stale literal 365 while gran=1h.
    const rvFrac = Q.realizedVol(rets, 30, ppy()); // annualized fraction, aligned to o.close
    const rvByDate = {};
    for (let i = 0; i < o.time.length; i++) rvByDate[new Date(o.time[i]).toISOString().slice(0, 10)] = rvFrac[i] * 100;
    const impl = [], real = [], vrp = [];
    for (let i = 0; i < dvol.time.length; i++) {
      const d = new Date(dvol.time[i]).toISOString().slice(0, 10);
      const r = rvByDate[d];
      impl.push(dvol.iv[i]); real.push(Number.isFinite(r) ? r : NaN);
      if (Number.isFinite(dvol.iv[i]) && Number.isFinite(r)) vrp.push(dvol.iv[i] - r);
    }
    C.lineChart(root, [
      { values: impl, color: 'var(--c1)', label: 'Implied (DVOL %)' },
      { values: real, color: 'var(--down)', label: 'Realized 30d (%)' },
    ], { height: 200, fmt: (v) => v.toFixed(0) + '%' });
    setPanelState('panel-vrp', 'ready', 'vrp-updated'); markFeed('vrp');
    const m = vrp.length ? vrp.reduce((a, b) => a + b, 0) / vrp.length : NaN;
    const posShare = vrp.length ? vrp.filter((v) => v > 0).length / vrp.length : NaN;
    setText('vrp-summary',
      `mean VRP ${Number.isFinite(m) ? m.toFixed(1) : '—'} vol-pts (implied − realized) · positive ${Number.isFinite(posShare) ? (posShare * 100).toFixed(0) : '—'}% of days. Persistently positive = the harvestable short-vol premium (carry with crash beta), NOT a sell signal.`);

    // §2.1: evaluate short-vol with CVaR / max-DD, NEVER headline Sharpe. The
    // tail the vol-seller is exposed to is BTC's own large moves (in EITHER
    // direction). We report CVaR5% and max-DD of the realized BTC daily returns
    // over the DVOL-overlap window — the loss distribution short-vol pays for.
    const tailRets = [];
    for (let i = 1; i < o.close.length; i++) {
      const r = Math.log(o.close[i] / o.close[i - 1]);
      if (Number.isFinite(r)) tailRets.push(r);
    }
    if (tailRets.length > 20) {
      const sorted = tailRets.slice().sort((a, b) => a - b);
      const k = Math.max(1, Math.floor(0.05 * sorted.length));
      const cvar5 = sorted.slice(0, k).reduce((a, b) => a + b, 0) / k; // mean of worst 5%
      const mdd = Q.maxDrawdown(Q.compound(tailRets));
      const worst = sorted[0];
      setText('vrp-tail-summary',
        `Tail readout (the short-vol risk, NOT Sharpe): underlying CVaR₅% ${(cvar5 * 100).toFixed(1)}%/day · worst day ${(worst * 100).toFixed(1)}% · buy-&-hold max-DD ${(mdd * 100).toFixed(0)}% over this window. Short-vol monetizes the premium until one of these tails arrives — Deribit's own backtest: +29.7% APR but 23.8% (→~45%) max-DD. Tail-lethal, not a sell button.`);
    } else {
      setText('vrp-tail-summary', 'Tail readout unavailable (insufficient return history in the DVOL-overlap window).');
    }
  }

  // ─── Option chain (Deribit, ONE public call, no key) ───────────────────
  // Mirrors the Python btcquant.data.get_option_chain + features (atm_iv,
  // iv_term_structure, iv_skew_25d, smile). All formulas live in §1 of the
  // brief; the client mirror lets the dashboard render the surface without the
  // Python engine, degrading to .chart-na exactly like loadVrp on any failure.

  const DERIBIT_MONTHS = {
    JAN: 1, FEB: 2, MAR: 3, APR: 4, MAY: 5, JUN: 6,
    JUL: 7, AUG: 8, SEP: 9, OCT: 10, NOV: 11, DEC: 12,
  };

  // Parse BTC-DDMMMYY-STRIKE-C/P -> {expiryMs, strike, type} (08:00 UTC expiry,
  // European cash-settled per brief §1.5). null for non-option names.
  function parseInstrument(name) {
    const parts = String(name).split('-');
    if (parts.length !== 4) return null;
    const [, dateTok, strikeTok, cpRaw] = parts;
    const cp = cpRaw.toUpperCase();
    if (cp !== 'C' && cp !== 'P') return null;
    if (dateTok.length < 6) return null;
    const mon = dateTok.slice(-5, -2).toUpperCase();
    const month = DERIBIT_MONTHS[mon];
    if (!month) return null;
    const day = parseInt(dateTok.slice(0, dateTok.length - 5), 10);
    const year = 2000 + parseInt(dateTok.slice(-2), 10);
    const strike = parseFloat(strikeTok);
    if (!Number.isFinite(day) || !Number.isFinite(year) || !Number.isFinite(strike)) return null;
    // 08:00:00 UTC on the contract date.
    const expiryMs = Date.UTC(year, month - 1, day, 8, 0, 0);
    if (!Number.isFinite(expiryMs)) return null;
    return { expiryMs, strike, type: cp };
  }

  // One public call: enumerate the whole BTC option universe (brief §1.1).
  // mark_iv is in PERCENT -> divide by 100 (brief §1.2, the silent 100x bug).
  // No key, no ticker fan-out (that is per-instrument & rate-limit hostile).
  async function loadOptionChain() {
    try {
      const url = 'https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency=BTC&kind=option';
      const d = await fetchJSON(url);
      const rows = (d && d.result) || [];
      if (!Array.isArray(rows) || !rows.length) throw new Error('empty chain');
      const out = [];
      for (const r of rows) {
        const p = parseInstrument(r.instrument_name);
        if (!p) continue;
        const markIv = Number(r.mark_iv);
        out.push({
          instrument: r.instrument_name,
          expiryMs: p.expiryMs,
          strike: p.strike,
          type: p.type,
          iv: Number.isFinite(markIv) ? markIv / 100 : NaN,   // decimal
          markIv: Number.isFinite(markIv) ? markIv : NaN,     // percent (ref)
          oi: Number(r.open_interest),
          volume: Number(r.volume),
          underlying: Number(r.underlying_price),
          underlyingIndex: r.underlying_index,
          mid: Number(r.mid_price),
          bid: r.bid_price == null ? null : Number(r.bid_price),
          ask: r.ask_price == null ? null : Number(r.ask_price),
          mark: Number(r.mark_price),
        });
      }
      if (!out.length) throw new Error('no parseable contracts');
      return out;
    } catch (_) { return null; }
  }

  // ── client-side feature mirrors (brief §1.3–1.5) ──────────────────────
  const YEAR_SECONDS = 365 * 24 * 3600;
  function yearFractionToExpiry(expiryMs, nowMs) {
    return (expiryMs - (nowMs == null ? Date.now() : nowMs)) / 1000 / YEAR_SECONDS;
  }
  function median(arr) {
    const a = arr.filter(Number.isFinite).slice().sort((x, y) => x - y);
    if (!a.length) return NaN;
    const m = Math.floor(a.length / 2);
    return a.length % 2 ? a[m] : (a[m - 1] + a[m]) / 2;
  }

  // Per-expiry forward F = median underlying_price (parity fast-path, §1.3).
  function expiryForward(slice) {
    const f = median(slice.map((r) => r.underlying));
    if (Number.isFinite(f)) return f;
    return median(slice.map((r) => r.strike));
  }

  // OTM-only (strike, iv) ladder: puts below F, calls above F (§1.4a/§1.7),
  // averaging duplicate strikes, sorted ascending.
  function otmLadder(slice, fwd) {
    const valid = slice.filter((r) => Number.isFinite(r.strike) && Number.isFinite(r.iv) && r.iv > 0);
    if (!valid.length || !Number.isFinite(fwd)) return [];
    let otm = valid.filter((r) => (r.type === 'P' && r.strike <= fwd) || (r.type === 'C' && r.strike >= fwd));
    if (!otm.length) otm = valid;   // degrade rather than empty the smile
    const byStrike = new Map();
    for (const r of otm) {
      const g = byStrike.get(r.strike) || { sum: 0, n: 0 };
      g.sum += r.iv; g.n += 1; byStrike.set(r.strike, g);
    }
    return Array.from(byStrike.entries())
      .map(([strike, g]) => ({ strike, iv: g.sum / g.n }))
      .sort((a, b) => a.strike - b.strike);
  }

  // Shape-preserving-ish IV interpolation at a strike. We mirror the Python
  // PChip intent with a monotone-safe local scheme: clamp outside the observed
  // range (no extrapolation, §1.4a), Fritsch-Carlson monotone cubic Hermite for
  // ≥3 points (never a wiggly global cubic), linear for 2. Pure JS, no deps.
  function interpIvAtStrike(ladder, target) {
    if (!ladder.length) return NaN;
    const ks = ladder.map((p) => p.strike), vs = ladder.map((p) => p.iv);
    const n = ks.length;
    if (n === 1) return vs[0];
    if (target <= ks[0]) return vs[0];
    if (target >= ks[n - 1]) return vs[n - 1];
    // locate interval
    let i = 0;
    while (i < n - 1 && ks[i + 1] < target) i++;
    if (n === 2) {
      const t = (target - ks[i]) / (ks[i + 1] - ks[i]);
      return vs[i] + t * (vs[i + 1] - vs[i]);
    }
    // PCHIP (Fritsch-Carlson) monotone slopes.
    const h = [], delta = [];
    for (let k = 0; k < n - 1; k++) { h[k] = ks[k + 1] - ks[k]; delta[k] = (vs[k + 1] - vs[k]) / h[k]; }
    const m = new Array(n);
    m[0] = delta[0]; m[n - 1] = delta[n - 2];
    for (let k = 1; k < n - 1; k++) {
      if (delta[k - 1] * delta[k] <= 0) { m[k] = 0; }
      else {
        const w1 = 2 * h[k] + h[k - 1], w2 = h[k] + 2 * h[k - 1];
        m[k] = (w1 + w2) / (w1 / delta[k - 1] + w2 / delta[k]);
      }
    }
    const t = target - ks[i], hh = h[i];
    const t2 = t * t, t3 = t2 * t;
    const h00 = 2 * t3 / (hh * hh * hh) - 3 * t2 / (hh * hh) + 1;
    const h10 = t3 / (hh * hh) - 2 * t2 / hh + t;
    const h01 = -2 * t3 / (hh * hh * hh) + 3 * t2 / (hh * hh);
    const h11 = t3 / (hh * hh) - t2 / hh;
    return h00 * vs[i] + h10 * m[i] + h01 * vs[i + 1] + h11 * m[i + 1];
  }

  // Standard normal CDF (Abramowitz-Stegun 7.1.26) for BS delta.
  function normCdf(x) {
    const t = 1 / (1 + 0.2316419 * Math.abs(x));
    const d = 0.3989422804014327 * Math.exp(-x * x / 2);
    let p = d * t * (0.31938153 + t * (-0.356563782 + t * (1.781477937 + t * (-1.821255978 + t * 1.330274429))));
    p = 1 - p;
    return x >= 0 ? p : 1 - p;
  }
  // Plain BS (forward, r=0) call delta on the observed IV (brief §1.4d). NOT the
  // FX premium-adjusted delta — Deribit-style spot/BS 25Δ, applied consistently.
  function bsCallDelta(fwd, strike, iv, t) {
    if (!(Number.isFinite(fwd) && Number.isFinite(strike) && Number.isFinite(iv))) return NaN;
    if (iv <= 0 || t <= 0 || strike <= 0 || fwd <= 0) return NaN;
    const d1 = (Math.log(fwd / strike) + 0.5 * iv * iv * t) / (iv * Math.sqrt(t));
    return normCdf(d1);
  }
  // Solve the strike whose BS call delta == target, scanning the smile (§1.4d).
  function strikeForCallDelta(ladder, fwd, t, target) {
    if (!ladder.length || !Number.isFinite(fwd) || t <= 0) return NaN;
    const ks = ladder.map((p) => p.strike);
    const lo = ks[0], hi = ks[ks.length - 1];
    if (!(hi > lo)) return NaN;
    const G = 200, grid = [], dels = [];
    for (let j = 0; j < G; j++) {
      const k = lo + (hi - lo) * (j / (G - 1));
      grid.push(k);
      dels.push(bsCallDelta(fwd, k, interpIvAtStrike(ladder, k), t));
    }
    let prev = null;
    for (let j = 0; j < G; j++) {
      if (!Number.isFinite(dels[j])) continue;
      const diff = dels[j] - target;
      if (prev && ((prev.diff <= 0 && diff >= 0) || (prev.diff >= 0 && diff <= 0))) {
        const dd = diff - prev.diff;
        return dd !== 0 ? prev.k + (grid[j] - prev.k) * (0 - prev.diff) / dd : prev.k;
      }
      prev = { k: grid[j], diff };
    }
    // no crossing: closest delta
    let best = NaN, bestErr = Infinity;
    for (let j = 0; j < G; j++) {
      if (!Number.isFinite(dels[j])) continue;
      const e = Math.abs(dels[j] - target);
      if (e < bestErr) { bestErr = e; best = grid[j]; }
    }
    return best;
  }

  // ATMF IV (interpolate OTM ladder at F) for one expiry slice (§1.4b).
  function atmIv(slice) {
    const fwd = expiryForward(slice);
    return interpIvAtStrike(otmLadder(slice, fwd), fwd);
  }
  // RR25 = IV(25Δc) − IV(25Δp); BF25 = ½(IV25c+IV25p) − ATM (§1.4d).
  // 25Δ put ⇔ +0.75 BS call delta (Δp = Δc − 1).
  function rrBf25(slice, expiryMs, nowMs) {
    const t = yearFractionToExpiry(expiryMs, nowMs);
    const fwd = expiryForward(slice);
    const ladder = otmLadder(slice, fwd);
    if (!ladder.length || t <= 0) return { rr: NaN, bf: NaN, atm: NaN };
    const k25c = strikeForCallDelta(ladder, fwd, t, 0.25);
    const k25p = strikeForCallDelta(ladder, fwd, t, 0.75);
    const iv25c = interpIvAtStrike(ladder, k25c);
    const iv25p = interpIvAtStrike(ladder, k25p);
    const atm = interpIvAtStrike(ladder, fwd);
    const rr = (Number.isFinite(iv25c) && Number.isFinite(iv25p)) ? iv25c - iv25p : NaN;
    const bf = (Number.isFinite(iv25c) && Number.isFinite(iv25p) && Number.isFinite(atm))
      ? 0.5 * (iv25c + iv25p) - atm : NaN;
    return { rr, bf, atm };
  }

  // Group chain by expiry -> sorted [{expiryMs, T, rows}], dropping expired.
  function expiriesByT(chain, nowMs) {
    const byExp = new Map();
    for (const r of chain) {
      if (!byExp.has(r.expiryMs)) byExp.set(r.expiryMs, []);
      byExp.get(r.expiryMs).push(r);
    }
    return Array.from(byExp.entries())
      .map(([expiryMs, rows]) => ({ expiryMs, T: yearFractionToExpiry(expiryMs, nowMs), rows }))
      .filter((e) => e.T > 0)
      .sort((a, b) => a.T - b.T);
  }

  // OTM-only gated smile points for one expiry (§1.7): both bid&ask present,
  // OTM side, |BS delta| >= dropWingDelta. Returns {strike, logm, iv, type}[].
  function smilePoints(slice, expiryMs, nowMs, dropWingDelta = 0.05) {
    const fwd = expiryForward(slice);
    const t = yearFractionToExpiry(expiryMs, nowMs);
    let df = slice.filter((r) => Number.isFinite(r.strike) && Number.isFinite(r.iv) && r.iv > 0
      && r.bid != null && r.ask != null);
    if (Number.isFinite(fwd)) {
      df = df.filter((r) => (r.type === 'P' && r.strike <= fwd) || (r.type === 'C' && r.strike >= fwd));
    }
    if (t > 0 && Number.isFinite(fwd)) {
      df = df.filter((r) => {
        const cd = bsCallDelta(fwd, r.strike, r.iv, t);
        if (!Number.isFinite(cd)) return true;          // can't compute -> keep
        const d = r.type === 'C' ? cd : cd - 1;
        return Math.abs(d) >= dropWingDelta;
      });
    }
    return df
      .map((r) => ({ strike: r.strike, logm: Number.isFinite(fwd) ? Math.log(r.strike / fwd) : NaN, iv: r.iv, type: r.type }))
      .sort((a, b) => a.strike - b.strike);
  }

  function expiryLabel(ms) { return new Date(ms).toISOString().slice(0, 10); }

  // ── option-chain renderers ────────────────────────────────────────────
  function renderOptionChain() {
    const smileRoot = $('chart-smile'), termRoot = $('chart-term'),
      rrTermRoot = $('chart-rr-term');
    const chain = state.optionChain;
    if (!chain || !chain.length) {
      // Keep any prior surface visible + mark the panels stale on a failed
      // refresh; only show the empty message + clear readouts on first load.
      const haveSurface = !!(smileRoot && smileRoot.querySelector('.chart-svg'));
      if (haveSurface) {
        [['panel-smile', 'smile-updated'], ['panel-term', 'term-updated'], ['panel-rrbf', 'rrbf-updated']]
          .forEach(([p, s]) => setPanelState(p, 'stale', s));
      } else {
        const na = '<div class="chart-na">Deribit option chain unavailable (CORS/geo/rate-limit). One public get_book_summary call; spot backtests above are unaffected.</div>';
        [smileRoot, termRoot, rrTermRoot].forEach((r) => { if (r) r.innerHTML = na; });
        setText('smile-summary', 'no option-chain data');
        setText('term-summary', 'no option-chain data');
        setText('rrbf-summary', 'no option-chain data');
        ['rrbf-atm', 'rrbf-rr', 'rrbf-bf'].forEach((id) => setText(id, '—'));
        [['panel-smile', 'smile-updated'], ['panel-term', 'term-updated'], ['panel-rrbf', 'rrbf-updated']]
          .forEach(([p, s]) => setPanelState(p, 'error', s));
      }
      return;
    }
    const nowMs = Date.now();
    const exps = expiriesByT(chain, nowMs);
    // Successful load: stamp + clear stale/error on all three option-chain panels.
    [['panel-smile', 'smile-updated'], ['panel-term', 'term-updated'], ['panel-rrbf', 'rrbf-updated']]
      .forEach(([p, s]) => setPanelState(p, 'ready', s));
    markFeed('options');

    // Populate the expiry selector once (keep selection across reloads).
    const sel = $('smile-expiry');
    if (sel) {
      const prev = sel.value;
      sel.innerHTML = '';
      exps.forEach((e) => {
        const opt = document.createElement('option');
        opt.value = String(e.expiryMs);
        opt.textContent = `${expiryLabel(e.expiryMs)}  (${(e.T * 365).toFixed(0)}d)`;
        sel.appendChild(opt);
      });
      if (prev && exps.some((e) => String(e.expiryMs) === prev)) sel.value = prev;
      else if (exps.length) sel.value = String(exps[Math.min(1, exps.length - 1)].expiryMs); // skip the unstable front by default
    }

    renderSmile();
    renderTerm(exps, nowMs);
    renderRrBf(exps, nowMs);
  }

  function renderSmile() {
    const root = $('chart-smile');
    const chain = state.optionChain;
    if (!root || !chain) return;
    const sel = $('smile-expiry'), xsel = $('smile-x');
    const expiryMs = sel ? +sel.value : NaN;
    const xMode = xsel ? xsel.value : 'logm';
    if (!Number.isFinite(expiryMs)) { setText('smile-summary', 'no expiry selected'); return; }
    const slice = chain.filter((r) => r.expiryMs === expiryMs);
    const nowMs = Date.now();
    const pts = smilePoints(slice, expiryMs, nowMs);
    if (pts.length < 2) {
      root.innerHTML = '<div class="chart-na">Too few gated OTM contracts for this expiry to draw a smile (sparse / wide spreads).</div>';
      setText('smile-summary', 'smile too sparse after the OTM + bid&ask + |delta|≥0.05 gate'); return;
    }
    const fwd = expiryForward(slice);
    const useLogm = xMode === 'logm';
    const puts = pts.filter((p) => p.type === 'P');
    const calls = pts.filter((p) => p.type === 'C');
    const toXY = (arr) => ({ x: arr.map((p) => (useLogm ? p.logm : p.strike)), y: arr.map((p) => p.iv * 100) });
    const series = [];
    if (puts.length) series.push(Object.assign({ color: 'var(--down)', label: 'OTM puts' }, toXY(puts)));
    if (calls.length) series.push(Object.assign({ color: 'var(--up)', label: 'OTM calls' }, toXY(calls)));
    C.xyChart(root, series, {
      height: 240,
      yFmt: (v) => v.toFixed(0) + '%',
      xFmt: useLogm ? (v) => v.toFixed(2) : (v) => (v >= 1000 ? (v / 1000).toFixed(0) + 'k' : v.toFixed(0)),
      xLabel: useLogm ? 'log-moneyness ln(K/F)' : 'strike',
      vlines: [{ x: useLogm ? 0 : fwd, color: 'var(--accent)', label: 'F' }],
    });
    // DESCRIPTIVE sentiment read from the front-expiry skew sign (§2.3).
    const rb = rrBf25(slice, expiryMs, nowMs);
    let sentiment = 'roughly symmetric';
    if (Number.isFinite(rb.rr)) {
      sentiment = rb.rr < -0.01 ? 'put-skewed (downside protection bid up — the typical BTC fear read)'
        : rb.rr > 0.01 ? 'call-skewed (upside demand richer — often euphoric regimes)'
          : 'roughly symmetric';
    }
    setText('smile-summary',
      `${expiryLabel(expiryMs)} · F≈$${Number.isFinite(fwd) ? fwd.toLocaleString(undefined, { maximumFractionDigits: 0 }) : '—'} · ${pts.length} gated OTM strikes · smile is ${sentiment}. DESCRIPTIVE sentiment only — a MARK smile, not a tradable bid/ask smile.`);
  }

  function renderTerm(exps, nowMs) {
    const root = $('chart-term');
    if (!root) return;
    const usable = exps.filter((e) => e.T > 1.5 / 365); // exclude the very front (T→0 unstable, §1.4b)
    const days = [], atm = [];
    for (const e of usable) {
      const v = atmIv(e.rows);
      if (Number.isFinite(v)) { days.push(e.T * 365); atm.push(v * 100); }
    }
    if (days.length < 2) {
      root.innerHTML = '<div class="chart-na">Too few expiries with a clean ATM IV to draw a term structure.</div>';
      setText('term-summary', 'term structure too sparse'); return;
    }
    const series = [{ x: days, y: atm, color: 'var(--accent-2)', label: 'ATM IV (interp at F)' }];
    // Overlay the latest DVOL point at its 30-day tenor (brief §1.6).
    const dvol = state.dvol;
    let dvolLast = NaN;
    if (dvol && dvol.iv && dvol.iv.length) {
      for (let i = dvol.iv.length - 1; i >= 0; i--) { if (Number.isFinite(dvol.iv[i])) { dvolLast = dvol.iv[i]; break; } }
      if (Number.isFinite(dvolLast)) {
        series.push({ x: [30], y: [dvolLast], color: 'var(--c1)', label: 'DVOL (30d benchmark)' });
      }
    }
    C.xyChart(root, series, {
      height: 220,
      yFmt: (v) => v.toFixed(0) + '%',
      xFmt: (v) => v.toFixed(0) + 'd',
      xLabel: 'days to expiry',
      vlines: Number.isFinite(dvolLast) ? [{ x: 30, color: 'var(--accent)', label: 'DVOL 30d' }] : [],
    });
    // SIGNAL read: term slope (front vs ~90d) = vol-forecast / regime (§2.2).
    const front = atm[0];
    let far = front, farD = days[0];
    for (let i = 0; i < days.length; i++) { if (days[i] >= 80 && days[i] <= 110) { far = atm[i]; farD = days[i]; } }
    if (far === front) { far = atm[atm.length - 1]; farD = days[days.length - 1]; }
    const slope = far - front;
    const regime = slope > 1 ? 'contango (far > front) — calmer near-term regime, normal upward vol term structure'
      : slope < -1 ? 'BACKWARDATION (front > far) — near-term stress / event premium, often precedes large |moves|'
        : 'roughly flat';
    let dvolNote = '';
    if (Number.isFinite(dvolLast)) {
      const interp30 = atmAtTenor(usable, 30);
      if (Number.isFinite(interp30)) {
        dvolNote = ` DVOL(30d)=${dvolLast.toFixed(1)}% vs our interp ATM(30d)≈${interp30.toFixed(1)}% — DVOL sits ABOVE by ≈${(dvolLast - interp30).toFixed(1)} vol-pts, the BF/convexity premium (expected, §1.6).`;
      }
    }
    setText('term-summary',
      `Term slope front(${days[0].toFixed(0)}d ${front.toFixed(0)}%) → ${farD.toFixed(0)}d ${far.toFixed(0)}%: ${regime}.${dvolNote} SIGNAL for vol-forecast/regime/sizing, NOT a return-timing signal (§2.2).`);
  }

  // Total-variance interpolation of ATM IV at a target tenor in DAYS (§1.4b):
  // never interpolate in IV directly. Returns IV in PERCENT.
  function atmAtTenor(usable, tenorDays) {
    const pts = [];
    for (const e of usable) {
      const iv = atmIv(e.rows);
      if (Number.isFinite(iv)) pts.push({ T: e.T, iv });
    }
    if (pts.length < 2) return NaN;
    const tgt = tenorDays / 365;
    if (tgt <= pts[0].T) return pts[0].iv * 100;
    if (tgt >= pts[pts.length - 1].T) return pts[pts.length - 1].iv * 100;
    for (let i = 0; i < pts.length - 1; i++) {
      if (tgt >= pts[i].T && tgt <= pts[i + 1].T) {
        const w1 = pts[i].iv * pts[i].iv * pts[i].T;     // total variance
        const w2 = pts[i + 1].iv * pts[i + 1].iv * pts[i + 1].T;
        const w = w1 + (w2 - w1) * (tgt - pts[i].T) / (pts[i + 1].T - pts[i].T);
        return Math.sqrt(w / tgt) * 100;
      }
    }
    return NaN;
  }

  function renderRrBf(exps, nowMs) {
    const root = $('chart-rr-term');
    if (!root) return;
    const usable = exps.filter((e) => e.T > 1.5 / 365);
    // Front readouts: first usable expiry.
    if (usable.length) {
      const f = usable[0];
      const rb = rrBf25(f.rows, f.expiryMs, nowMs);
      setText('rrbf-atm', Number.isFinite(rb.atm) ? (rb.atm * 100).toFixed(1) + '%' : '—');
      const rrEl = $('rrbf-rr');
      if (rrEl) {
        rrEl.textContent = Number.isFinite(rb.rr) ? (rb.rr * 100).toFixed(1) + ' vp' : '—';
        rrEl.style.color = Number.isFinite(rb.rr) ? (rb.rr < 0 ? 'var(--down)' : rb.rr > 0 ? 'var(--up)' : 'var(--muted)') : '';
      }
      setText('rrbf-bf', Number.isFinite(rb.bf) ? (rb.bf * 100).toFixed(1) + ' vp' : '—');
    }
    // RR25(T) term-of-skew.
    const days = [], rr = [];
    for (const e of usable) {
      const rb = rrBf25(e.rows, e.expiryMs, nowMs);
      if (Number.isFinite(rb.rr)) { days.push(e.T * 365); rr.push(rb.rr * 100); }
    }
    if (days.length < 2) {
      root.innerHTML = '<div class="chart-na">Too few expiries with locatable 25Δ wings for an RR25 term structure.</div>';
      setText('rrbf-summary', 'RR25 term structure too sparse'); return;
    }
    C.xyChart(root, [{ x: days, y: rr, color: 'var(--accent-2)', label: 'RR25(T) = IV(25dC) − IV(25dP)' }], {
      height: 170,
      yFmt: (v) => v.toFixed(1) + ' vp',
      xFmt: (v) => v.toFixed(0) + 'd',
      xLabel: 'days to expiry · RR25 in vol-points (negative = put-richer)',
    });
    const frontRr = rr[0];
    const read = frontRr < -1 ? 'NEGATIVE (puts richer) — downside protection bid up, the BTC fear/put-skew read'
      : frontRr > 1 ? 'POSITIVE (calls richer) — upside-demand / call skew, often euphoric regimes'
        : 'near zero — roughly symmetric skew';
    setText('rrbf-summary',
      `Front RR25 = ${frontRr.toFixed(1)} vol-pts: ${read}. Sign convention RR25 = IV(25dC) − IV(25dP); ±25Δ strikes from plain BS spot delta (Deribit-style, NOT FX premium-adjusted). DESCRIPTIVE sentiment — BTC skew changes sign with regime; the z-score timing rule historically underperformed naive carry (§2.3).`);
  }

  // ─── On-chain context (descriptive ONLY — not a signal) ────────────────
  async function loadOnchain() {
    try {
      const url = 'https://api.blockchain.info/charts/n-transactions?timespan=2years&format=json&cors=true';
      const d = await fetchJSON(url);
      const vals = (d && d.values) || [];
      if (!vals.length) throw new Error('empty onchain');
      return { name: (d && d.name) || 'Transactions / day', y: vals.map((v) => v.y) };
    } catch (_) { return null; }
  }
  function renderOnchain() {
    const root = $('chart-onchain');
    if (!root) return;
    const oc = state.onchain;
    if (!oc || !oc.y.length) {
      if (!root.querySelector('.chart-svg')) {
        root.innerHTML = '<div class="chart-na">On-chain data unavailable (blockchain.info CORS/rate-limit).</div>';
        setText('onchain-summary', 'no on-chain data');
        setPanelState('panel-onchain', 'error', 'onchain-updated');
      } else {
        setPanelState('panel-onchain', 'stale', 'onchain-updated');
      }
      return;
    }
    setText('onchain-metric-name', oc.name);
    C.lineChart(root, [{ values: oc.y, color: 'var(--accent-2)', label: oc.name }],
      { height: 180, fmt: (v) => (v >= 1e6 ? (v / 1e6).toFixed(1) + 'M' : v >= 1e3 ? (v / 1e3).toFixed(0) + 'k' : v.toFixed(0)) });
    setText('onchain-summary', 'DESCRIPTIVE context only — on-chain/sentiment metrics are dominated by a revision/look-ahead trap and do NOT reliably predict returns (RESEARCH.md §2.17). Not a tradeable signal.');
    setPanelState('panel-onchain', 'ready', 'onchain-updated'); markFeed('onchain');
  }

  // ─── Live charts: WebSocket tape + candle overlay (brief §3) ────────────
  //
  // RESEARCH CONTEXT ONLY. The live tick is a context panel showing the
  // current price/tape; it is NEVER blended into a backtested series and the
  // historical bars are never flash-animated as if real-time (brief §3.5).
  // PUBLIC channels only — no keys, no signing (brief §3.3/§3.6).

  // Shared reconnect skeleton: capped exponential backoff + jitter, re-subscribe
  // on every reopen, ping/heartbeat timer gated to the socket lifecycle (§3.3).
  // An adapter supplies { url, subscribe(ws), onMessage(msg, api), ping }.
  function makeSocket(adapter, api) {
    let ws = null, attempt = 0, hbTimer = null, closedByUs = false;
    // Module D — liveness watchdog. A socket can stay OPEN while the feed silently
    // stalls (proxy, dozing tab, dropped subscription); onclose never fires, so the
    // status would otherwise stay green "live" over a frozen price — an honesty-rail
    // violation. The adapter stamps lastAliveAt via api.markAlive() on every ticker/
    // heartbeat frame (NOT trades — a quiet market_trades window is normal). While the
    // socket is OPEN we flip to amber "stale" after STALE_MS, and force ONE reconnect
    // after DEAD_MS which routes through the EXISTING backoff (we never fight it).
    let lastAliveAt = 0, stale = false, forcedDead = false, wdTimer = null;
    const MAX_BACKOFF = 30000, STALE_MS = 12000, DEAD_MS = 40000, WATCHDOG_MS = 2000;

    function clearHeartbeat() { if (hbTimer) { clearInterval(hbTimer); hbTimer = null; } }

    // Adapter calls this on a healthy-feed frame (ticker tick / heartbeat). Recovery
    // is a single clean transition back to green — no flicker.
    const liveApi = Object.assign({}, api, {
      markAlive() {
        lastAliveAt = Date.now();
        if (stale) { stale = false; forcedDead = false; api.onStatus('open', 'live feed recovered'); }
      },
    });

    function scheduleReconnect() {
      clearHeartbeat();
      if (closedByUs) return;
      // capped exponential backoff (1s,2s,4s,…,30s) + up to 1s jitter.
      const base = Math.min(MAX_BACKOFF, 1000 * Math.pow(2, attempt));
      const delay = base + Math.random() * 1000;
      attempt++;
      api.onStatus('reconnecting', `live feed dropped — retrying in ${(delay / 1000).toFixed(0)}s`);
      setTimeout(connect, delay);
    }

    function connect() {
      if (closedByUs) return;
      try { ws = new WebSocket(adapter.url); }
      catch (e) { api.onStatus('error', 'live feed unavailable (' + e.message + ')'); scheduleReconnect(); return; }

      ws.onopen = () => {
        attempt = 0;                       // reset backoff on a clean open
        lastAliveAt = Date.now();          // fresh liveness baseline → no instant false-stale
        stale = false; forcedDead = false;
        api.onStatus('open', 'live feed connected');
        try { adapter.subscribe(ws); }     // (re-)subscribe on EVERY (re)open
        catch (_) { /* subscribe error -> socket will close, backoff handles it */ }
        // Lifecycle-gated heartbeat: only ticks while THIS socket is open.
        if (adapter.ping) {
          clearHeartbeat();
          hbTimer = setInterval(() => {
            if (ws && ws.readyState === WebSocket.OPEN) { try { adapter.ping(ws); } catch (_) { /* ignore */ } }
          }, adapter.pingMs || 20000);
        }
      };
      ws.onmessage = (ev) => {
        let msg; try { msg = JSON.parse(ev.data); } catch (_) { return; }
        try { adapter.onMessage(msg, liveApi); } catch (_) { /* never let a bad frame kill the socket */ }
      };
      ws.onerror = () => { /* onclose fires next; handled there */ };
      ws.onclose = () => { clearHeartbeat(); scheduleReconnect(); };
    }

    // Judge ONLY an OPEN socket — a CONNECTING/closed one is the backoff's job, so the
    // watchdog never double-drives reconnection. One interval for the socket's lifetime.
    function startWatchdog() {
      if (wdTimer) return;
      wdTimer = setInterval(() => {
        if (closedByUs || !ws || ws.readyState !== WebSocket.OPEN) return;
        const gap = Date.now() - lastAliveAt;
        if (gap >= DEAD_MS) {
          if (!forcedDead) {
            forcedDead = true;
            api.onStatus('reconnecting', 'live feed stalled — reconnecting');
            try { ws.close(); } catch (_) { /* onclose → scheduleReconnect (existing backoff) */ }
          }
          return;
        }
        if (gap >= STALE_MS) { stale = true; api.onStatus('stale', `stale — no data for ${Math.round(gap / 1000)}s`); }
      }, WATCHDOG_MS);
    }

    connect();
    startWatchdog();
    return {
      close() {
        closedByUs = true; clearHeartbeat();
        if (wdTimer) { clearInterval(wdTimer); wdTimer = null; }
        if (ws) try { ws.close(); } catch (_) { /* ignore */ }
      },
    };
  }

  // Coinbase Advanced Trade adapter (§3.3) on wss://advanced-trade-ws.coinbase.com.
  // THREE public channels, no auth/signing (product id BTC-USD, dash):
  //   • ticker        → header price + live candle (high-frequency price stream)
  //   • market_trades → the tape (real per-trade size + aggressor side + trade time;
  //                     the ticker channel carries NO size, hence the old "—" column)
  //   • heartbeats    → keepalive (~1/s). MUST subscribe within 5s of connect;
  //                     channels go stale ~60–90s without updates.
  const coinbaseAdapter = {
    url: 'wss://advanced-trade-ws.coinbase.com',
    pingMs: 20000,
    subscribe(ws) {
      ws.send(JSON.stringify({ type: 'subscribe', product_ids: ['BTC-USD'], channel: 'ticker' }));
      ws.send(JSON.stringify({ type: 'subscribe', product_ids: ['BTC-USD'], channel: 'market_trades' }));
      ws.send(JSON.stringify({ type: 'subscribe', product_ids: ['BTC-USD'], channel: 'heartbeats' }));
    },
    // Coinbase has no client ping frame for this feed; the heartbeats channel is
    // the keepalive. Re-assert the subscription as a liveness nudge.
    ping(ws) { ws.send(JSON.stringify({ type: 'subscribe', product_ids: ['BTC-USD'], channel: 'heartbeats' })); },
    onMessage(msg, api) {
      // heartbeats (~1/s) are the steady liveness signal → feed the watchdog and stop.
      if (msg.channel === 'heartbeats') { if (api.markAlive) api.markAlive(); return; }
      if (!Array.isArray(msg.events)) return;
      if (msg.channel === 'ticker') {
        // Price feed → header + candle, and a liveness signal for the watchdog.
        // (ticker has no per-trade size field; the tape comes from market_trades.)
        if (api.markAlive) api.markAlive();
        for (const ev of msg.events) {
          for (const t of (ev.tickers || [])) {
            const price = Number(t.price);
            if (Number.isFinite(price)) api.onTick({ price, time: Date.now() });
          }
        }
      } else if (msg.channel === 'market_trades') {
        // The tape: each event batch is a 'snapshot' (initial / on re-subscribe) or
        // an 'update'. onTrades seeds once from the first snapshot and ignores later
        // ones, so a reconnect never re-dumps the whole batch into the tape. Trades do
        // NOT mark liveness — a quiet (low-volume) window is normal, not a stall.
        for (const ev of msg.events) api.onTrades(ev.trades || [], ev.type === 'snapshot');
      }
    },
  };

  // Live state: header price flash + Live tape + live candle (separate from
  // the backtest series — never merged, §3.5).
  const live = { socket: null, lastPrice: NaN, tape: [], tradesSeeded: false, lastTradeId: -1, lcChart: null, lcCandles: null, lastBarSec: NaN };
  const TAPE_MAX = 40;

  function fmtUsd(x) { return Number.isFinite(x) ? '$' + x.toLocaleString(undefined, { maximumFractionDigits: 0 }) : '—'; }

  function updateLiveStatus(kind, msg) {
    const stamp = new Date().toISOString().slice(11, 19) + ' UTC';
    setText('live-updated', kind === 'open' ? 'live · ' + stamp : msg);
    const na = $('live-tape-na');
    if (na && kind !== 'open') na.textContent = msg + ' — page works fully without the live feed.';
    // Status-bar connection chip: green = live, amber = reconnecting, red = offline.
    const cs = $('conn-status');
    if (cs) {
      cs.classList.remove('live', 'stale', 'error');
      if (kind === 'open') { cs.classList.add('live'); setText('conn-text', 'live · ' + stamp); }
      else if (kind === 'stale') { cs.classList.add('stale'); setText('conn-text', msg); }   // socket open but no data — honest "stale", not a fake "live"
      else if (kind === 'reconnecting') { cs.classList.add('stale'); setText('conn-text', 'reconnecting…'); }
      else if (kind === 'error') { cs.classList.add('error'); setText('conn-text', 'live feed offline'); }
    }
  }

  function onLiveTick(tick) {
    const prev = live.lastPrice;
    live.lastPrice = tick.price;

    // Header live price + CVD-safe flash on a genuine change (§4.9). This is a
    // real live tick (allowed to flash), NOT a faked animation of static history.
    const priceEl = $('last-price');
    if (priceEl) {
      priceEl.textContent = fmtUsd(tick.price);
      if (Number.isFinite(prev) && tick.price !== prev) {
        const cls = tick.price > prev ? 'tick-up' : 'tick-down';
        priceEl.classList.remove('tick-up', 'tick-down');
        void priceEl.offsetWidth;             // restart the CSS animation
        priceEl.classList.add(cls);
      }
    }

    // Keep the status-bar "live · HH:MM:SS" stamp fresh while ticks flow.
    const cs = $('conn-status');
    if (cs && cs.classList.contains('live')) {
      setText('conn-text', 'live · ' + new Date(tick.time).toISOString().slice(11, 19) + ' UTC');
    }

    // Push the tick into the live candle panel (updates the CURRENT bar only).
    // The tape is fed by market_trades (onLiveTrades), NOT by ticker — ticker
    // carries no trade size, which is what left the size column blank.
    updateLiveCandle(tick);
  }

  // Live tape ← Coinbase market_trades: real per-trade size + aggressor side +
  // trade timestamp. Newest-first, capped. The FIRST snapshot seeds the tape;
  // later snapshots (re-fired on every re-subscribe/reconnect) are ignored so the
  // batch is never re-dumped. Updates are deduped by the monotonic trade_id.
  function onLiveTrades(trades, isSnapshot) {
    const norm = (trades || []).map((tr) => ({
      price: Number(tr.price), size: Number(tr.size), side: tr.side,
      time: Date.parse(tr.time), id: Number(tr.trade_id),
    })).filter((t) => Number.isFinite(t.price) && Number.isFinite(t.id));
    if (!norm.length) return;
    norm.sort((a, b) => b.id - a.id);              // newest first (trade_id is monotonic)
    if (isSnapshot) {
      if (live.tradesSeeded) return;               // ignore reconnect snapshots
      live.tape = norm.slice(0, TAPE_MAX);
      live.lastTradeId = norm[0].id;
      live.tradesSeeded = true;
    } else {
      if (!live.tradesSeeded) return;              // wait for the seed snapshot first
      const fresh = norm.filter((t) => t.id > live.lastTradeId);
      if (!fresh.length) return;
      live.lastTradeId = fresh[0].id;              // fresh is desc → [0] is the max id
      live.tape = fresh.concat(live.tape);         // newest on top
      if (live.tape.length > TAPE_MAX) live.tape.length = TAPE_MAX;
    }
    renderTape();
  }

  // Color by AGGRESSOR side. Coinbase market_trades `side` is the MAKER's side
  // (verified live: side=BUY trades tick DOWN, side=SELL tick UP), so the aggressor
  // is the OPPOSITE: side=SELL ⇒ a resting ask was lifted by an aggressive BUYER
  // (up/green); side=BUY ⇒ a resting bid was hit by an aggressive SELLER (down/red).
  // CVD-safe via the .delta glyphs. Price shows CENTS so sub-dollar moves are visible
  // (whole-dollar rounding made a busy same-$ second look frozen). Real trade time, so
  // a stalled tape shows OLD timestamps rather than "now".
  function renderTape() {
    const root = $('live-tape');
    if (!root) return;
    const rows = live.tape.map((t) => {
      const ts = Number.isFinite(t.time) ? new Date(t.time).toISOString().slice(11, 19) : '—';
      const dir = t.side === 'SELL' ? 'up' : t.side === 'BUY' ? 'down' : '';
      const px = Number.isFinite(t.price) ? '$' + t.price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—';
      const sz = Number.isFinite(t.size) ? t.size.toFixed(4) : '—';
      return `<div style="display:flex; justify-content:space-between; gap:var(--sp-3); padding:1px 0;">`
        + `<span class="num" style="color:var(--muted)">${ts}</span>`
        + `<span class="num delta ${dir}">${px}</span>`
        + `<span class="num" style="color:var(--muted)">${sz}</span></div>`;
    }).join('');
    root.innerHTML = `<div style="display:flex; justify-content:space-between; gap:var(--sp-3); color:var(--muted); font-size:var(--fs-xs); border-bottom:1px solid var(--border); padding-bottom:2px; margin-bottom:2px;">`
      + `<span>UTC</span><span>price</span><span>size BTC</span></div>` + rows;
  }

  // ── Live candle panel: Lightweight Charts if vendored OK, else native SVG ──
  const hasLC = () => typeof globalThis.LightweightCharts !== 'undefined' && globalThis.LightweightCharts.createChart;

  // Build entry/exit markers + stop/target lines from the CURRENT backtest's
  // per-bar position series (transitions). These annotate history; they are not
  // live orders. UTC epoch SECONDS everywhere (§3.2) so markers land correctly.
  function backtestOverlay(o, bt) {
    const markers = [];
    const pos = bt && bt.position;
    if (o && pos && pos.length) {
      for (let i = 1; i < pos.length && i < o.time.length; i++) {
        const wasIn = pos[i - 1] > 0, isIn = pos[i] > 0;
        if (!wasIn && isIn) markers.push({ time: Math.floor(o.time[i] / 1000), position: 'belowBar', color: COLOR('--up', '#26A69A'), shape: 'arrowUp', text: 'BUY' });
        else if (wasIn && !isIn) markers.push({ time: Math.floor(o.time[i] / 1000), position: 'aboveBar', color: COLOR('--down', '#EF5350'), shape: 'arrowDown', text: 'EXIT' });
      }
    }
    // Stop/target reference lines: ±1 ATR-ish band off the last close as an
    // illustrative risk frame (the engine's stops are strategy-specific; this
    // is a visual anchor, clearly labelled, never a live order).
    let stop = NaN, target = NaN;
    if (o && o.close.length > 20) {
      const last = o.close[o.close.length - 1];
      const rets = [];
      for (let i = o.close.length - 20; i < o.close.length; i++) rets.push(Math.abs(Math.log(o.close[i] / o.close[i - 1])));
      const atr = (rets.reduce((a, b) => a + b, 0) / rets.length) * last;
      stop = last - 2 * atr; target = last + 2 * atr;
    }
    return { markers, stop, target };
  }

  function COLOR(varName, fallback) {
    try { const v = getComputedStyle(document.documentElement).getPropertyValue(varName).trim(); return v || fallback; }
    catch (_) { return fallback; }
  }

  // Render the live candle panel from cached OHLCV; overlay backtest markers +
  // stop/target lines (§3.2). Lightweight Charts when available, else native SVG.
  function renderLiveCandle(o, bt) {
    const root = $('chart-live');
    if (!root) return;
    if (!o || !o.close.length) {
      root.innerHTML = '<div class="chart-na">No cached OHLCV yet — the live candle panel mirrors the loaded history with a live tail once data arrives.</div>';
      return;
    }
    const N = o.close.length, W = Math.min(N, 240), s0 = N - W;
    const bars = [];
    for (let i = s0; i < N; i++) {
      const tSec = Math.floor(o.time[i] / 1000);
      if (!Number.isFinite(tSec)) continue;
      bars.push({ time: tSec, open: o.open[i], high: o.high[i], low: o.low[i], close: o.close[i] });
    }
    live.lastBarSec = bars.length ? bars[bars.length - 1].time : NaN;
    const ov = backtestOverlay(o, bt);

    if (hasLC()) {
      const LC = globalThis.LightweightCharts;
      // (Re)create the chart on each backtest run so markers/lines stay in sync.
      root.innerHTML = '';
      live.lcChart = LC.createChart(root, {
        height: root.clientHeight || 360,
        layout: { background: { color: COLOR('--bg', '#0b0f18') }, textColor: COLOR('--fg', '#d6e0f5') },
        grid: { vertLines: { color: COLOR('--grid', '#23314a') }, horzLines: { color: COLOR('--grid', '#23314a') } },
        timeScale: { timeVisible: true, secondsVisible: false, borderColor: COLOR('--border', '#1d2840') },
        rightPriceScale: { borderColor: COLOR('--border', '#1d2840') },
        crosshair: { mode: 0 },
      });
      live.lcCandles = live.lcChart.addCandlestickSeries({
        upColor: COLOR('--up', '#26A69A'), downColor: COLOR('--down', '#EF5350'),
        borderUpColor: COLOR('--up', '#26A69A'), borderDownColor: COLOR('--down', '#EF5350'),
        wickUpColor: COLOR('--up', '#26A69A'), wickDownColor: COLOR('--down', '#EF5350'),
      });
      live.lcCandles.setData(bars);
      if (ov.markers.length) live.lcCandles.setMarkers(ov.markers.slice(-60));
      if (Number.isFinite(ov.stop)) live.lcCandles.createPriceLine({ price: ov.stop, color: COLOR('--down', '#EF5350'), lineStyle: 2, lineWidth: 1, axisLabelVisible: true, title: 'stop (illus.)' });
      if (Number.isFinite(ov.target)) live.lcCandles.createPriceLine({ price: ov.target, color: COLOR('--up', '#26A69A'), lineStyle: 2, lineWidth: 1, axisLabelVisible: true, title: 'target (illus.)' });
      live.lcChart.timeScale().fitContent();
    } else {
      // Native SVG fallback (charts.js candles) — markers/lines degrade to the
      // backtest panel above; we still show the live candle window honestly.
      live.lcChart = null; live.lcCandles = null;
      const oWin = {
        source: o.source, derivedOHLC: o.derivedOHLC,
        time: o.time.slice(s0), open: o.open.slice(s0), high: o.high.slice(s0),
        low: o.low.slice(s0), close: o.close.slice(s0), volume: o.volume ? o.volume.slice(s0) : undefined,
      };
      C.candles(root, oWin, { height: root.clientHeight || 360 });
    }
  }

  // Live tick -> update ONLY the current (right-most) candle. Never appends a
  // synthetic bar into the backtest history; purely the live tail (§3.2/§3.5).
  function updateLiveCandle(tick) {
    if (!hasLC() || !live.lcCandles || !Number.isFinite(live.lastBarSec) || !Number.isFinite(tick.price)) return;
    try {
      live.lcCandles.update({
        time: live.lastBarSec,
        open: tick.price, high: tick.price, low: tick.price, close: tick.price,
      });
    } catch (_) { /* out-of-order ts can throw; ignore the stray tick */ }
  }

  // Start the live feed once (Coinbase default — no geoblock, §3.3). Graceful:
  // if the browser blocks the WSS upgrade, the page is fully usable without it.
  // ─── Per-feed staleness watchdog (REST analog of the live-feed watchdog) ────
  // The perp / options / on-chain panels are one-shot REST snapshots (no WS), so a
  // tab left open ages them silently — and there is no live candle beside them to
  // betray a frozen value. markFeed(key) stamps each successful load; this watchdog
  // flips a panel to an honest "stale · Nh old" once it exceeds a per-feed max-age
  // matched to that feed's natural cadence (conservative, so a slow daily feed does
  // not false-positive). The basis / perp-ticker feed — the one genuinely live-ish
  // panel — is additionally refreshed in the background so it stays current rather
  // than merely honestly-stale. All public, keyless; no new endpoints.
  const FEEDS = [
    { key: 'basis',   panels: [['panel-basis', 'basis-updated']],     maxAge: 240000 },    // live-ish (refreshed) → ~4 min
    { key: 'funding', panels: [['panel-funding', 'funding-updated']], maxAge: 7200000 },   // 8h funding cadence → ~2h
    { key: 'oi',      panels: [['panel-oi', 'oi-updated']],           maxAge: 21600000 },  // daily bars → ~6h
    { key: 'ls',      panels: [['panel-lsratio', 'ls-updated']],      maxAge: 21600000 },  // daily bars → ~6h
    { key: 'vrp',     panels: [['panel-vrp', 'vrp-updated']],         maxAge: 21600000 },  // DVOL daily → ~6h
    { key: 'options', panels: [['panel-smile', 'smile-updated'], ['panel-term', 'term-updated'], ['panel-rrbf', 'rrbf-updated']], maxAge: 3600000 }, // live option marks → ~1h
    { key: 'onchain', panels: [['panel-onchain', 'onchain-updated']], maxAge: 43200000 },  // daily, revision-lagged → ~12h
  ];

  function checkFeedAges() {
    const now = Date.now();
    for (const f of FEEDS) {
      const at = state.feedAt[f.key];
      if (!at) continue;                                   // never loaded OK → the panel already shows its own error/NA state
      const age = now - at;
      if (age > f.maxAge) f.panels.forEach(([p, s]) => setPanelState(p, 'stale', null, age));
    }
    // Re-arm the OHLCV last-bar banner — but only when the banner is otherwise clear,
    // so this never clobbers a loading / degraded / error message.
    if (Number.isFinite(state.ohlcvLastMs) && state.ohlcv && !state.ohlcv.derivedOHLC && state._bannerKind == null) {
      const ageH = (now - state.ohlcvLastMs) / 36e5;
      if (ageH > state.ohlcvStaleH) showBanner('warn', `STALE DATA: last bar is ${ageH.toFixed(ageH < 10 ? 1 : 0)}h old. Endpoint may be lagging or rate-limited.`);
    }
  }

  // Background refresh for the one live-ish panel. On success it re-renders + re-stamps
  // (clearing any stale state); on failure it keeps the last-good values and lets the
  // age watchdog flag it — a transient miss never wipes the panel.
  async function refreshBasis() {
    try {
      const t = await fetchPerpTicker();
      if (t) { state.perpTicker = t; renderBasis(); }
    } catch (_) { /* keep last-good; watchdog handles staleness */ }
  }

  let feedWatch = null;
  function startFeedWatchdog() {
    if (feedWatch) return;                                 // start once; init() re-runs on reload/timeframe change
    feedWatch = setInterval(checkFeedAges, 30000);
    setInterval(refreshBasis, 90000);
  }

  function startLive() {
    if (typeof WebSocket === 'undefined' || live.socket) return;
    updateLiveStatus('reconnecting', 'connecting to live feed…');
    live.socket = makeSocket(coinbaseAdapter, {
      onTick: onLiveTick,
      onTrades: onLiveTrades,
      onStatus: (kind, msg) => updateLiveStatus(kind, msg),
    });
  }

  // ─── §5.1-5.2 Power-user UX: command palette, help, density + CVD toggles ─
  //
  // All vanilla, no deps, unobtrusive. Persists density + CVD-strict in
  // localStorage; all motion is gated by the CSS prefers-reduced-motion guard.
  // Keys are ignored while typing in a field so they never fight the controls.

  const LS = {
    get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : v; } catch (_) { return d; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (_) { /* private mode / disabled */ } },
  };

  // Density (comfortable | compact) — toggles body.density-compact (§4.6).
  function applyDensity(mode) {
    const compact = mode === 'compact';
    document.body.classList.toggle('density-compact', compact);
    const btn = $('density-toggle');
    if (btn) { btn.setAttribute('aria-pressed', compact ? 'true' : 'false'); btn.textContent = compact ? 'compact' : 'comfortable'; }
    LS.set('btcq-density', mode);
    // Charts read pixel sizes at render time, so re-render the active backtest so
    // the SVG/Lightweight panels pick up the tightened spacing immediately.
    if (state.ohlcv) { try { runStrategy(currentStrategy()); } catch (_) { /* ignore */ } }
  }
  function toggleDensity() {
    applyDensity(document.body.classList.contains('density-compact') ? 'comfortable' : 'compact');
  }

  // Strict CVD-safe (Okabe-Ito) palette — toggles body.cvd-strict (§4.1). The
  // class + variable overrides already live in styles.css; we only flip it.
  function applyCvd(strict) {
    document.body.classList.toggle('cvd-strict', !!strict);
    const btn = $('cvd-toggle');
    if (btn) btn.setAttribute('aria-pressed', strict ? 'true' : 'false');
    LS.set('btcq-cvd', strict ? '1' : '0');
    // Re-render so charts.js / Lightweight Charts re-read the swapped --up/--down.
    if (state.ohlcv) { try { runStrategy(currentStrategy()); renderVrp(state.ohlcv); renderOptionChain(); } catch (_) { /* ignore */ } }
  }
  function toggleCvd() { applyCvd(!document.body.classList.contains('cvd-strict')); }

  function currentStrategy() { const s = $('strategy-select'); return s ? s.value : 'ma_trend'; }

  // ── Command palette (Cmd/Ctrl+K) ──────────────────────────────────────
  // ─── Module 3: tabbed information architecture ──────────────────────────
  // Each panel belongs to a region; the tab bar shows one region at a time so
  // the page stops being a wall. SVG charts measure clientWidth, so a region's
  // charts are RE-RENDERED when its tab is shown (a hidden panel reports width 0).
  const REGIONS = {
    backtest: ['panel-leaderboard', 'panel-performance', 'panel-candles', 'panel-equity', 'panel-drawdown', 'panel-hist', 'panel-rolling'],
    live: ['panel-live'],
    perpetual: ['panel-funding', 'panel-basis', 'panel-oi', 'panel-lsratio'],
    options: ['panel-vrp', 'panel-smile', 'panel-term', 'panel-rrbf'],
    onchain: ['panel-onchain'],
  };
  const REGION_ORDER = ['backtest', 'live', 'perpetual', 'options', 'onchain'];
  const TAB_KEY = 'btcq-tab';
  let activeRegion = 'backtest';

  function regionOfPanel(id) {
    for (const r of REGION_ORDER) if (REGIONS[r].includes(id)) return r;
    return null;
  }

  function setActiveTab(name, opts = {}) {
    if (!REGIONS[name]) name = 'backtest';
    activeRegion = name;
    for (const r of REGION_ORDER) {
      const show = r === name;
      for (const id of REGIONS[r]) { const el = $(id); if (el) el.style.display = show ? '' : 'none'; }
    }
    document.querySelectorAll('.region-tab').forEach((b) => {
      const on = b.dataset.tab === name;
      b.classList.toggle('active', on);
      b.setAttribute('aria-selected', on ? 'true' : 'false');
    });
    try { localStorage.setItem(TAB_KEY, name); } catch (_) { /* ignore */ }
    renderRegion(name);                       // re-render now-visible charts at real width
    if (!opts.noScroll) { const t = $('region-tabs'); if (t) t.scrollIntoView({ behavior: prefersReducedMotion() ? 'auto' : 'smooth', block: 'start' }); }
  }

  // Re-render the charts in a region from cached state (cheap; SVG redraw only).
  function renderRegion(name) {
    const L = state.last;
    try {
      if (name === 'backtest' && L) renderCharts(L.o, L.bt, L.zSeries, L.p);
      else if (name === 'live' && L) renderLiveCandle(L.o, L.bt);
      else if (name === 'perpetual') { renderFunding(); renderBasis(); renderOpenInterest(); renderLongShort(); }
      else if (name === 'options') { renderVrp(state.ohlcv); renderOptionChain(); }
      else if (name === 'onchain') renderOnchain();
    } catch (_) { /* a degraded panel must never break the tab switch */ }
  }

  function wireTabs() {
    document.querySelectorAll('.region-tab').forEach((b) => {
      b.addEventListener('click', () => setActiveTab(b.dataset.tab));
    });
  }

  function applyActiveTab() {
    let saved = 'backtest';
    try { saved = localStorage.getItem(TAB_KEY) || 'backtest'; } catch (_) { /* ignore */ }
    setActiveTab(saved, { noScroll: true });   // initial apply: no scroll jump
  }

  // Commands: jump to any panel (built from .panel[data-panel-title]) + switch
  // strategy + switch timeframe. Substring "fuzzy-ish" filter, arrow-key nav,
  // Enter runs, Esc closes. Focus is restored to the trigger on close.
  const cmdk = { items: [], filtered: [], sel: 0, lastFocus: null };

  function buildCommands() {
    const items = [];
    // Jump-to-panel commands.
    document.querySelectorAll('.panel[data-panel-title]').forEach((p) => {
      items.push({ kind: 'panel', label: p.getAttribute('data-panel-title'), run: () => jumpToPanel(p) });
    });
    // Switch-strategy commands.
    Object.keys(STRATEGIES).forEach((key) => {
      items.push({
        kind: 'strategy', label: STRATEGIES[key].label,
        run: () => { const s = $('strategy-select'); if (s) { s.value = key; runStrategy(key); } jumpToPanel($('panel-performance')); },
      });
    });
    // Switch-timeframe commands.
    [['1d', '1d daily (365 bars/yr)'], ['1h', '1h hourly (8760 bars/yr)']].forEach(([g, label]) => {
      items.push({
        kind: 'timeframe', label: 'Timeframe → ' + label,
        run: () => { const t = $('granularity-select'); if (t && state.gran !== g) { t.value = g; state.gran = g; init(); } },
      });
    });
    // Switch-section (tab) commands.
    [['backtest', 'Backtest'], ['live', 'Live charts'], ['perpetual', 'Perpetual'], ['options', 'Options'], ['onchain', 'On-chain']].forEach(([k, label]) => {
      items.push({ kind: 'section', label: 'Section → ' + label, run: () => setActiveTab(k) });
    });
    cmdk.items = items;
  }

  function jumpToPanel(panel) {
    if (!panel) return;
    const r = regionOfPanel(panel.id);                       // reveal its tab if hidden
    if (r && r !== activeRegion) setActiveTab(r, { noScroll: true });
    panel.scrollIntoView({ behavior: prefersReducedMotion() ? 'auto' : 'smooth', block: 'start' });
    // Briefly mark focus so keyboard users land on the right region.
    const prev = panel.getAttribute('tabindex');
    panel.setAttribute('tabindex', '-1');
    try { panel.focus({ preventScroll: true }); } catch (_) { /* ignore */ }
    if (prev == null) setTimeout(() => panel.removeAttribute('tabindex'), 0);
  }

  function prefersReducedMotion() {
    try { return window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches; }
    catch (_) { return false; }
  }

  function renderCmdkList() {
    const list = $('cmdk-list');
    if (!list) return;
    if (!cmdk.filtered.length) {
      list.innerHTML = '<li class="cmdk-empty">No matching command.</li>';
      return;
    }
    list.innerHTML = cmdk.filtered.map((it, i) =>
      `<li class="cmdk-item" role="option" id="cmdk-opt-${i}" aria-selected="${i === cmdk.sel}" data-i="${i}">`
      + `<span class="cmdk-kind">${it.kind}</span><span class="cmdk-label">${escapeHtml(it.label)}</span></li>`
    ).join('');
    const selEl = list.querySelector('[aria-selected="true"]');
    if (selEl) selEl.scrollIntoView({ block: 'nearest' });
  }

  function escapeHtml(s) { return String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }

  // Subsequence fuzzy score: prefix > contiguous substring > scattered subsequence
  // (chars in order). Returns -1 for no match. Earlier + adjacent matches rank higher,
  // so "smile" finds "IV Smile" and "rrbf" finds "RR25 / BF25".
  function fuzzyScore(text, needle) {
    const hay = text.toLowerCase();
    if (!needle) return 0;
    const sub = hay.indexOf(needle);
    if (sub === 0) return 10000;            // prefix — best
    if (sub > 0) return 6000 - sub;         // contiguous substring
    let hi = 0, score = 2000, last = -2;
    for (let i = 0; i < needle.length; i++) {
      const found = hay.indexOf(needle[i], hi);
      if (found < 0) return -1;             // a needle char is missing → no match
      if (found === last + 1) score += 18;  // reward adjacency
      score -= found;                        // reward earlier matches
      last = found; hi = found + 1;
    }
    return score;
  }

  function filterCmdk(q) {
    const needle = q.trim().toLowerCase();
    if (!needle) {
      cmdk.filtered = cmdk.items.slice();
    } else {
      cmdk.filtered = cmdk.items
        .map((it) => ({ it, s: fuzzyScore(it.kind + ' ' + it.label, needle) }))
        .filter((r) => r.s >= 0)
        .sort((a, b) => b.s - a.s)
        .map((r) => r.it);
    }
    cmdk.sel = 0;
    renderCmdkList();
  }

  function openCmdk() {
    const ov = $('cmdk'), input = $('cmdk-input');
    if (!ov || !input) return;
    cmdk.lastFocus = document.activeElement;
    buildCommands();
    ov.hidden = false;
    input.value = '';
    filterCmdk('');
    input.focus();
  }
  function closeCmdk() {
    const ov = $('cmdk');
    if (!ov || ov.hidden) return;
    ov.hidden = true;
    if (cmdk.lastFocus && cmdk.lastFocus.focus) try { cmdk.lastFocus.focus(); } catch (_) { /* ignore */ }
  }
  function runCmdk(i) {
    const it = cmdk.filtered[i];
    closeCmdk();
    if (it && it.run) try { it.run(); } catch (_) { /* ignore */ }
  }

  function openHelp() {
    const ov = $('help');
    if (!ov) return;
    cmdk.lastFocus = document.activeElement;
    ov.hidden = false;
  }
  function closeHelp() {
    const ov = $('help');
    if (!ov || ov.hidden) return;
    ov.hidden = true;
    if (cmdk.lastFocus && cmdk.lastFocus.focus) try { cmdk.lastFocus.focus(); } catch (_) { /* ignore */ }
  }

  function typingInField(el) {
    if (!el) return false;
    const tag = (el.tagName || '').toLowerCase();
    return tag === 'input' || tag === 'select' || tag === 'textarea' || el.isContentEditable;
  }

  function wirePowerUserUX() {
    // Restore persisted preferences (default comfortable / standard palette).
    applyDensity(LS.get('btcq-density', 'comfortable') === 'compact' ? 'compact' : 'comfortable');
    applyCvd(LS.get('btcq-cvd', '0') === '1');

    // Toolbar buttons.
    const dBtn = $('density-toggle'); if (dBtn) dBtn.addEventListener('click', toggleDensity);
    const cBtn = $('cvd-toggle'); if (cBtn) cBtn.addEventListener('click', toggleCvd);
    const kBtn = $('cmdk-open'); if (kBtn) kBtn.addEventListener('click', openCmdk);
    const hBtn = $('help-open'); if (hBtn) hBtn.addEventListener('click', openHelp);

    // Command-palette wiring.
    const input = $('cmdk-input'), list = $('cmdk-list');
    if (input) {
      input.addEventListener('input', () => filterCmdk(input.value));
      input.addEventListener('keydown', (e) => {
        if (e.key === 'ArrowDown') { e.preventDefault(); cmdk.sel = Math.min(cmdk.filtered.length - 1, cmdk.sel + 1); renderCmdkList(); }
        else if (e.key === 'ArrowUp') { e.preventDefault(); cmdk.sel = Math.max(0, cmdk.sel - 1); renderCmdkList(); }
        else if (e.key === 'Enter') { e.preventDefault(); runCmdk(cmdk.sel); }
        else if (e.key === 'Home') { e.preventDefault(); cmdk.sel = 0; renderCmdkList(); }
        else if (e.key === 'End') { e.preventDefault(); cmdk.sel = cmdk.filtered.length - 1; renderCmdkList(); }
      });
    }
    if (list) {
      list.addEventListener('click', (e) => {
        const li = e.target.closest('.cmdk-item');
        if (li && li.dataset.i != null) runCmdk(+li.dataset.i);
      });
      list.addEventListener('mousemove', (e) => {
        const li = e.target.closest('.cmdk-item');
        if (li && li.dataset.i != null && +li.dataset.i !== cmdk.sel) { cmdk.sel = +li.dataset.i; renderCmdkList(); }
      });
    }
    // Backdrop click dismisses either overlay.
    document.querySelectorAll('[data-cmdk-dismiss]').forEach((el) => el.addEventListener('click', closeCmdk));
    document.querySelectorAll('[data-help-dismiss]').forEach((el) => el.addEventListener('click', closeHelp));

    // Global keyboard shortcuts. Cmd/Ctrl+K and Esc always work; single-letter
    // shortcuts are ignored while typing in a field so they never clobber input.
    document.addEventListener('keydown', (e) => {
      const cmdkOpen = !($('cmdk') && $('cmdk').hidden);
      const helpOpen = !($('help') && $('help').hidden);

      if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) {
        e.preventDefault();
        if (cmdkOpen) closeCmdk(); else { closeHelp(); openCmdk(); }
        return;
      }
      if (e.key === 'Escape') {
        if (cmdkOpen) { e.preventDefault(); closeCmdk(); }
        else if (helpOpen) { e.preventDefault(); closeHelp(); }
        return;
      }
      if (cmdkOpen) return;                          // palette owns its own keys
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (typingInField(e.target)) return;           // never fight the controls

      if (e.key === '?') { e.preventDefault(); if (helpOpen) closeHelp(); else openHelp(); }
      else if (e.key === 'd' || e.key === 'D') { e.preventDefault(); toggleDensity(); }
      else if (e.key === 'v' || e.key === 'V') { e.preventDefault(); toggleCvd(); }
      else if (e.key === 'r' || e.key === 'R') { e.preventDefault(); init(); }
    });
  }

  // ─── Boot ─────────────────────────────────────────────────────────────
  let wired = false;
  // Module 5: keep --header-h in sync with the sticky app-header's real height so
  // the (also-sticky) section tabs dock exactly beneath it — the header grows/shrinks
  // as the stale banner toggles or the topbar wraps on narrow widths.
  function syncHeaderHeight() {
    try {
      const h = document.querySelector('.app-header');
      if (h) document.documentElement.style.setProperty('--header-h', h.offsetHeight + 'px');
    } catch (_) { /* fall back to the CSS default */ }
  }

  // Module 5: drop a shimmer placeholder into each empty chart box before the
  // fetch resolves. Charts.* clears the container on first render, so skeletons
  // disappear on their own — and .chart's min-height means no layout shift.
  function showSkeletons() {
    try {
      document.querySelectorAll('.chart').forEach((c) => {
        if (!c.children.length) { const s = document.createElement('div'); s.className = 'skel'; c.appendChild(s); }
      });
    } catch (_) { /* purely cosmetic — never block load */ }
  }

  async function init() {
    if (!wired) { wireControls(); wired = true; } // wire once; init() re-runs on reload/timeframe change
    syncHeaderHeight();
    showSkeletons();
    showBanner('warn', 'Loading live public data…');
    const [ohlcvRes, eth, fundingRes, dvol, onchain, optionChain, oi, ls, perpTicker] = await Promise.all([
      loadOHLCV(),
      fetchETH().catch(() => null),
      fetchFunding().then((d) => ({ data: d, ok: true })).catch((e) => ({ data: null, ok: false, err: e.message })),
      loadVrp(),
      loadOnchain(),
      loadOptionChain(),
      fetchOpenInterest(),
      fetchLongShort(),
      fetchPerpTicker(),
    ]);

    state.eth = eth;
    state.funding = fundingRes.ok ? fundingRes.data : null;
    state.dvol = dvol;
    state.onchain = onchain;
    state.optionChain = optionChain;
    state.oi = oi;
    state.ls = ls;
    state.perpTicker = perpTicker;

    if (ohlcvRes.data) {
      state.ohlcv = ohlcvRes.data;
      const last = new Date(state.ohlcv.time[state.ohlcv.time.length - 1]);
      const ageH = (Date.now() - last.getTime()) / 36e5;
      // Granularity-aware staleness: a fresh hourly bar is ~hours old, a daily
      // bar can legitimately be a day-plus old before the next one prints.
      const staleH = state.gran === '1h' ? 3 : 36;
      state.ohlcvLastMs = last.getTime(); state.ohlcvStaleH = staleH;   // for the watchdog re-arm
      const lastLabel = state.gran === '1h' ? last.toISOString().slice(0, 16).replace('T', ' ') + ' UTC' : last.toISOString().slice(0, 10);
      const srcLine = `Source: ${state.ohlcv.source} · ${state.ohlcv.close.length} ${state.gran} bars · last bar ${lastLabel}`;
      setText('source-line', srcLine);
      setText('last-price', '$' + (state.ohlcv.close[state.ohlcv.close.length - 1] || 0).toLocaleString(undefined, { maximumFractionDigits: 0 }));
      if (state.ohlcv.derivedOHLC) showBanner('warn', 'STALE/DEGRADED: primary candle sources failed; showing CoinGecko close-only data (synthetic OHLC). Backtests still valid on close. ' + ohlcvRes.errors.join(' | '));
      else if (ageH > staleH) showBanner('warn', `STALE DATA: last bar is ${ageH.toFixed(ageH < 10 ? 1 : 0)}h old. Endpoint may be lagging or rate-limited.`);
      else showBanner(null, null);
      runStrategy($('strategy-select') ? $('strategy-select').value : 'ma_trend');
    } else {
      showBanner('error', 'NO LIVE DATA: all public sources failed (CORS, geo-block, or rate-limit). Nothing is fabricated — try again later or run the Python engine offline. ' + ohlcvRes.errors.join(' | '));
      setText('source-line', 'all sources failed');
    }
    renderFunding();
    renderBasis();
    renderOpenInterest();
    renderLongShort();
    renderVrp(state.ohlcv);
    renderOnchain();
    renderOptionChain();
    // If OHLCV failed, still show an honest live-candle placeholder (no fabrication).
    if (!ohlcvRes.data) renderLiveCandle(null, null);
    // Live WS tape/price (Coinbase, public, keyless) — starts once, survives reloads.
    startLive();
    // Per-feed staleness watchdog + basis live-refresh — starts once, survives reloads.
    startFeedWatchdog();
    // Module 3: apply the persisted section tab (after all panels have rendered
    // once at full width), hiding the others + re-rendering the active region.
    applyActiveTab();
  }

  function wireControls() {
    const sel = $('strategy-select');
    if (sel) {
      sel.innerHTML = '';
      for (const key in STRATEGIES) {
        const opt = document.createElement('option');
        opt.value = key; opt.textContent = STRATEGIES[key].label;
        sel.appendChild(opt);
      }
      sel.value = 'ma_trend';
      sel.addEventListener('change', () => runStrategy(sel.value));
    }
    // Timeframe selector: changing it changes BOTH the fetch granularity and the
    // annualization factor (ppy()), so we must re-FETCH at the new granularity,
    // not just re-render the existing (wrong-frequency) bars. init() reloads.
    const gsel = $('granularity-select');
    if (gsel) {
      gsel.value = state.gran;
      gsel.addEventListener('change', () => {
        state.gran = gsel.value === '1h' ? '1h' : '1d';
        init();
      });
    }
    ['cost-bps', 'slip-bps'].forEach((id) => {
      const n = $(id);
      if (n) n.addEventListener('change', () => runStrategy(sel ? sel.value : 'ma_trend'));
    });
    // Option-chain smile controls: re-render only the smile (no re-fetch — the
    // chain is one snapshot, fetched once in init()).
    const esel = $('smile-expiry'); if (esel) esel.addEventListener('change', () => renderSmile());
    const xsel = $('smile-x'); if (xsel) xsel.addEventListener('change', () => renderSmile());

    const reload = $('reload-btn');
    if (reload) reload.addEventListener('click', () => init());
    window.addEventListener('resize', debounce(() => { syncHeaderHeight(); if (state.ohlcv) runStrategy(sel ? sel.value : 'ma_trend'); renderFunding(); }, 250));
    // Keep the sticky-tabs offset exact as the header reflows (banner toggles, wrap).
    try { if (window.ResizeObserver) { const ro = new ResizeObserver(() => syncHeaderHeight()); const hdr = document.querySelector('.app-header'); if (hdr) ro.observe(hdr); } } catch (_) { /* ignore */ }

    wireTabs();   // Module 3: section tab bar (Backtest / Live / Volatility / On-chain)
    // §5.1-5.2 power-user affordances (command palette, help, density + CVD toggles).
    wirePowerUserUX();
  }

  function debounce(fn, ms) {
    let t;
    return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
  }

  // ─── Self-check (Phase 1, brief §1.2) ─────────────────────────────────
  // Annualization-threading guard. Runs under node via `node dashboard/app.js
  // --check` (the dashboard SELF-CHECK). Two assertions:
  //   (1) ppy() mirrors the Python engine's _periods_per_year (1d->365, 1h->8760).
  //   (2) NO literal 365 survives at an annualization site in this file — every
  //       Q.backtest periodsPerYear / Q.sharpe / Q.realizedVol / Q.rollingSharpe
  //       annualization argument must be `p` or `ppy()`, never `365`. This is the
  //       exact bug class (silent sqrt(24) mis-annualization on 1h) that forced
  //       the prior removal of the 1h selector.
  function selfCheck() {
    const fails = [];

    // (1) ppy mapping mirrors Python _periods_per_year.
    const cases = [['1d', 365], ['1h', 24 * 365]];
    for (const [g, want] of cases) {
      state.gran = g;
      const got = ppy();
      if (got !== want) fails.push(`ppy() for ${g}: got ${got}, want ${want} (Python _periods_per_year)`);
    }
    if (24 * 365 !== 8760) fails.push('sanity: 24*365 !== 8760');
    state.gran = '1d';

    // (2) Source scan: no literal 365 at an annualization site.
    const fs = require('fs');
    const src = fs.readFileSync(__filename, 'utf8');
    const lines = src.split('\n');
    // Annualization sites = calls that take a periods-per-year argument.
    const patterns = [
      /periodsPerYear\s*:\s*365\b/,             // Q.backtest / sigTsmom / applyVolTarget opts
      /Q\.sharpe\([^)]*,\s*365\s*\)/,            // grossSharpe
      /Q\.realizedVol\([^)]*,\s*365\s*\)/,       // rolling vol + VRP realized vol
      /Q\.rollingSharpe\([^)]*,\s*365\s*\)/,     // rolling sharpe
    ];
    lines.forEach((line, i) => {
      // The funding APR (`* 3 * 365`) is an 8h-funding-cadence constant, NOT a
      // bar-frequency annualization site — explicitly exempt it.
      if (/\*\s*3\s*\*\s*365/.test(line)) return;
      for (const re of patterns) {
        if (re.test(line)) fails.push(`literal 365 at annualization site, app.js:${i + 1}: ${line.trim()}`);
      }
    });

    if (fails.length) {
      console.error('SELF-CHECK FAIL (' + fails.length + '):');
      for (const f of fails) console.error('  - ' + f);
      process.exit(1);
    }
    console.log('SELF-CHECK PASS: ppy() mirrors Python _periods_per_year (1d=365, 1h=8760); no literal 365 at any annualization site in app.js.');
  }

  // Boot in the browser; run the self-check under node (no document).
  if (typeof document === 'undefined') {
    if (typeof process !== 'undefined' && process.argv && process.argv.includes('--check')) selfCheck();
  } else if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
