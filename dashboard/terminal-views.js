// terminal-views.js — orderflow terminal: canvas/DOM renderers (DESIGN-orderflow-terminal.md §4).
//
// RESEARCH / DESCRIPTIVE ONLY (§0.1). These views draw exactly what the stores
// hold — i.e. only what arrived over the wire this session (§0.7). They never
// interpolate, backfill, or animate history as if it were live.
//
// Contract (§4): ONE global `BTCQ_TERMINAL_VIEWS`; each view is a FACTORY
// returning `{ mount(el, opts), render(slice) }`. The bootstrap (terminal.js)
// owns dirty flags and calls render() ONLY when the underlying store changed,
// so a render may assume "something is new" — but must still be cheap enough
// to run several times a second (BTC prints ~10–20 trades/s across legs).
// Views hold NO market state of their own beyond render caches (crosshair,
// flash-diff maps) — the stores in terminal-state.js are the single source of
// truth; a view fed the same slice twice must draw the same pixels.
//
// Color discipline (§4): every hue comes from styles.css custom properties —
// --up/--down for aggressor/P&L semantics (CVD-safe pair, survives the
// body.cvd-strict Okabe-Ito toggle because we read the vars at draw time),
// --c1…--c6 for categorical series (exchanges, CVD buckets), --accent for
// reference markers (POC), --accent-2 for secondary annotations. NO new hues.
// Color is never the only cue: sell/buy also differ by POSITION (left/right of
// the × in footprint cells, left/right ladder column), liq sides carry a text
// badge, deltas carry a sign.
//
// DOM/canvas access happens only inside mount()/render() — the file parses
// under plain `node --check` and loads in the fixture-smoke vm sandbox without
// a real DOM (quant.js dual-export pattern at the bottom).
'use strict';

(function (global) {
  // ─── Shared formatting helpers (mono, tabular; '—' for absent, never 0) ──
  //
  // Absent/non-finite values render as '—', NEVER as 0 — a fabricated zero is
  // indistinguishable from a real zero print (§0.7 honesty rail).

  function fmtUsd(x, dp) {
    if (!Number.isFinite(x)) return '—';
    const d = dp == null ? 0 : dp;
    return '$' + x.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
  }

  /** Signed compact USD for notionals/CVD ($12.5k / $3.42M / -$1.10B). */
  function fmtCompactUsd(x) {
    if (!Number.isFinite(x)) return '—';
    const s = x < 0 ? '-' : '';
    const a = Math.abs(x);
    if (a >= 1e9) return s + '$' + (a / 1e9).toFixed(2) + 'B';
    if (a >= 1e6) return s + '$' + (a / 1e6).toFixed(2) + 'M';
    if (a >= 1e3) return s + '$' + (a / 1e3).toFixed(1) + 'k';
    return s + '$' + a.toFixed(0);
  }

  /** Base-asset (BTC) volume with decimals scaled to magnitude — footprint
   *  cells are tiny, so precision yields to legibility as volume grows. */
  function fmtVol(v) {
    if (!Number.isFinite(v)) return '—';
    const a = Math.abs(v);
    if (a >= 100) return v.toFixed(0);
    if (a >= 10) return v.toFixed(1);
    return v.toFixed(2);
  }

  /** Trade/ladder quantity (BTC). */
  function fmtQty(q) {
    if (!Number.isFinite(q)) return '—';
    if (q >= 100) return q.toFixed(1);
    if (q >= 1) return q.toFixed(3);
    return q.toFixed(4);
  }

  /** UTC HH:MM:SS from epoch-ms — real event time, never wall clock, so a
   *  stalled feed shows OLD timestamps rather than pretending "now" (app.js
   *  renderTape rule). */
  function hms(ts) { return Number.isFinite(ts) ? new Date(ts).toISOString().slice(11, 19) : '—'; }
  function hm(ts) { return Number.isFinite(ts) ? new Date(ts).toISOString().slice(11, 16) : '—'; }

  /** h/mm/ss countdown for funding (negative → '—', a past nextFundingTs means
   *  the venue hasn't rolled the field yet — we don't guess the next window). */
  function countdown(ms) {
    if (!Number.isFinite(ms) || ms < 0) return '—';
    const s = Math.floor(ms / 1000);
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), ss = s % 60;
    return (h > 0 ? h + 'h ' : '') + String(m).padStart(2, '0') + 'm ' + String(ss).padStart(2, '0') + 's';
  }

  // ─── Shared color helpers — styles.css tokens are the ONLY hue source ────

  function cssVar(name, fallback) {
    try {
      const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
      return v || fallback;
    } catch (_) { return fallback; }
  }

  /** Read the live palette each draw (cheap: ~15 var reads at a few Hz). Read
   *  fresh, not cached at mount, so a body.cvd-strict toggle or theme change
   *  takes effect without a reload — same reasoning as app.js COLOR(). */
  function pal() {
    return {
      up: cssVar('--up', '#26A69A'), down: cssVar('--down', '#EF5350'),
      fg: cssVar('--fg', '#e4e8ee'), muted: cssVar('--muted', '#9aa3b2'),
      grid: cssVar('--grid', '#2a2e36'), border: cssVar('--border', '#23262d'),
      bg: cssVar('--bg', '#0a0b0d'), panel: cssVar('--bg-panel', '#15171b'),
      panel2: cssVar('--bg-panel-2', '#0f1114'),
      accent: cssVar('--accent', '#E0A33E'), accent2: cssVar('--accent-2', '#5BA3F5'),
      c1: cssVar('--c1', '#C792EA'), c2: cssVar('--c2', '#5BA3F5'), c3: cssVar('--c3', '#F2A6C2'),
      c4: cssVar('--c4', '#58C7E0'), c5: cssVar('--c5', '#A8B0C0'), c6: cssVar('--c6', '#D6B3FF'),
    };
  }

  /** '#rrggbb' → 'rgba(r,g,b,a)' so token hues can carry intensity without
   *  inventing new colors (the token IS the hue; alpha is just weight). */
  function rgba(hex, a) {
    const h = String(hex).replace('#', '');
    if (h.length !== 6) return 'rgba(154,163,178,' + a + ')';   // --muted fallback
    const r = parseInt(h.slice(0, 2), 16), g = parseInt(h.slice(2, 4), 16), b = parseInt(h.slice(4, 6), 16);
    return 'rgba(' + r + ',' + g + ',' + b + ',' + a + ')';
  }

  /** Fixed exchange → categorical-token mapping, consistent across ALL panels
   *  (tape tag, agg-book stack, legend) so one venue is always one color.
   *  Deliberately NOT --up/--down (venues aren't P&L) and NOT --accent (chrome). */
  const EX_TOKEN = { bybit: 'c2', binancef: 'c1', coinbase: 'c4' };
  function exColor(p, ex) { return p[EX_TOKEN[ex]] || p.c5; }

  // ─── Shared canvas helper — DPR-aware sizing (crisp on retina) ───────────
  //
  // Canvas backing store = CSS px × devicePixelRatio; the transform maps 1
  // drawing unit to 1 CSS px. Re-measured every draw (cheap when unchanged) so
  // container resizes are picked up on the next dirty render without a
  // ResizeObserver dependency.
  function fitCanvas(canvas) {
    const rect = canvas.getBoundingClientRect();
    const dpr = (typeof window !== 'undefined' && window.devicePixelRatio) || 1;
    const w = Math.max(40, Math.floor(rect.width));
    const h = Math.max(40, Math.floor(rect.height));
    const bw = Math.floor(w * dpr), bh = Math.floor(h * dpr);
    if (canvas.width !== bw || canvas.height !== bh) { canvas.width = bw; canvas.height = bh; }
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { ctx, w, h };
  }

  /** Escape-free text guard for innerHTML builders: every dynamic value we
   *  inject is a Number we formatted or a whitelisted exchange code — but ids
   *  from the wire are strings, so anything not formatted by us goes through
   *  here (defense in depth; no wire string should ever execute as markup). */
  function esc(s) {
    return String(s).replace(/[&<>"']/g, (c) => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
    ));
  }

  // ═══ FootprintView — bid×ask cells per price per bar + VP gutter + CVD ═══
  //
  // Canvas layout (all CSS px):
  //   ┌──────────────── cells (bars × price rows) ─────────┬─ VP ─┬ axis ┐
  //   │ each cell: 'sellVol × buyVol' — sell LEFT (--down),│ hist │price │
  //   │ buy RIGHT (--up); half-cell bg alpha ∝ that side's │ +POC │labels│
  //   │ volume; imbalance flags = outlined half            │ /VA  │      │
  //   ├─ delta row (signed, colored) ──────────────────────┤      │      │
  //   ├─ total-volume row ─────────────────────────────────┤      │      │
  //   └─ bar time labels ──────────────────────────────────┴──────┴──────┘
  // Position (left/right of '×') is the redundant non-color cue for sell/buy
  // (WCAG 1.4.1 — same rule as the .delta glyphs in styles.css).
  //
  // The CVD subchart mounts into opts.cvdEl (physically the full-width footer
  // panel in terminal.html §4 layout, logically part of this view per §4):
  // one lightweight-charts line per notional bucket + overall. If the vendored
  // lib is missing we show an honest note instead of hand-rolling a fake — the
  // page must never carry a silently-broken chart.
  function FootprintView() {
    let root = null, canvas = null, cvdEl = null;
    let cvdChart = null, cvdSeries = null, cvdKeys = null, cvdNote = false;
    let lastSlice = null;      // cached so crosshair moves can redraw without new data
    let mouse = null;          // {x,y} in CSS px, or null
    let drawQueued = false;    // rAF coalescing for mouse-driven redraws
    let lastCvdAt = 0;         // CVD setData throttle (see renderCvd)
    const CVD_MIN_MS = 600;    // setData on ~20k pts is the priciest op here — cap it

    // Fixed gutter/footer geometry.
    const GUT_VP = 84, GUT_AXIS = 60;
    const ROW_DELTA = 16, ROW_TVOL = 16, ROW_TIME = 14;
    const BAR_W_MIN = 64;      // 'sellVol × buyVol' needs ~9 mono chars + padding

    function mount(el, opts) {
      root = el;
      const o = opts || {};
      canvas = document.createElement('canvas');
      canvas.className = 'term-canvas';
      root.appendChild(canvas);
      // Crosshair: track in CSS px; redraw from the CACHED slice (no store
      // access — the mouse never fabricates a new data frame).
      canvas.addEventListener('mousemove', (e) => {
        const r = canvas.getBoundingClientRect();
        mouse = { x: e.clientX - r.left, y: e.clientY - r.top };
        scheduleDraw();
      });
      canvas.addEventListener('mouseleave', () => { mouse = null; scheduleDraw(); });

      cvdEl = o.cvdEl || null;
      if (cvdEl) initCvd(o.buckets || []);
    }

    function scheduleDraw() {
      if (drawQueued || !lastSlice) return;
      drawQueued = true;
      requestAnimationFrame(() => { drawQueued = false; if (lastSlice) draw(lastSlice); });
    }

    // ── CVD subchart (lightweight-charts; vendored, no CDN — DESIGN §4) ──
    function initCvd(buckets) {
      const LC = global.LightweightCharts;
      if (!LC || !LC.createChart) {
        // Honest degrade (index.html vendoring rule): say why, fabricate nothing.
        cvdEl.innerHTML = '<div class="chart-na">vendored lightweight-charts unavailable — '
          + 'CVD subchart disabled (no fallback series is fabricated).</div>';
        cvdNote = true;
        return;
      }
      const p = pal();
      cvdChart = LC.createChart(cvdEl, {
        height: cvdEl.clientHeight || 180,
        layout: { background: { color: p.bg }, textColor: p.fg, fontFamily: cssVar('--mono', 'monospace') },
        grid: { vertLines: { color: p.grid }, horzLines: { color: p.grid } },
        timeScale: { timeVisible: true, secondsVisible: true, borderColor: p.border },
        rightPriceScale: { borderColor: p.border },
        crosshair: { mode: 0 },
        localization: { priceFormatter: fmtCompactUsd },   // CVD is USD notional — compact axis
      });
      // Series order/colors: overall = --c1 (primary data line token), then one
      // categorical token per bucket. 'whale' (> largest threshold) = --c3.
      const bucketTok = ['c5', 'c2', 'c4', 'c6'];   // thresholds ascending, ≤4 supported hues
      cvdKeys = ['overall'].concat(buckets);
      cvdSeries = {};
      const legend = document.createElement('div');
      legend.className = 'term-cvd-legend';
      cvdKeys.forEach((k, i) => {
        const color = k === 'overall' ? p.c1
          : k === 'whale' ? p.c3
            : p[bucketTok[Math.min(i - 1, bucketTok.length - 1)]];
        cvdSeries[k] = cvdChart.addLineSeries({
          color, lineWidth: k === 'overall' ? 2 : 1,
          priceLineVisible: false, lastValueVisible: true,
          crosshairMarkerVisible: false,
        });
        const label = k === 'overall' ? 'overall'
          : k === 'whale' ? '&gt; largest bucket (whale)'
            : '&le; ' + fmtCompactUsd(Number(k));
        legend.insertAdjacentHTML('beforeend',
          '<span><i class="sw" style="background:' + color + '"></i>' + label + '</span>');
      });
      // Session-anchor honesty label (§4 CvdStore doc: CVD has no natural zero).
      legend.insertAdjacentHTML('beforeend',
        '<span class="cvd-anchor">anchored at page open — slope/divergence only, level is meaningless</span>');
      cvdEl.appendChild(legend);
    }

    /** cvd.series() arrays are LIVE refs (read-only, terminal-state.js §4) with
     *  epoch-ms sample times that can repeat within one second; lightweight-
     *  charts wants strictly-ascending unique times → bucket to seconds keeping
     *  the LAST value per second (a coarser view of the same exact cumsums —
     *  values are never altered, only plot resolution). */
    function toLcSeries(tArr, vArr) {
      const out = [];
      let prevSec = -1;
      for (let i = 0; i < tArr.length; i++) {
        const s = Math.floor(tArr[i] / 1000);
        if (!Number.isFinite(s)) continue;
        if (s === prevSec) out[out.length - 1].value = vArr[i];
        else if (s > prevSec) { out.push({ time: s, value: vArr[i] }); prevSec = s; }
        // s < prevSec (cross-feed clock skew) — dropped, same rail as FootprintStore late prints
      }
      return out;
    }

    function renderCvd(cvd, now) {
      if (!cvdChart || !cvd) return;
      if (now - lastCvdAt < CVD_MIN_MS) return;   // setData is O(points) — throttle beyond dirty flags
      lastCvdAt = now;
      const s = cvd;   // {t, overall, byBucket}
      cvdSeries.overall.setData(toLcSeries(s.t, s.overall));
      for (const k of cvdKeys) {
        if (k === 'overall') continue;
        if (s.byBucket[k]) cvdSeries[k].setData(toLcSeries(s.t, s.byBucket[k]));
      }
    }

    // ── Main canvas draw ──
    function draw(slice) {
      const { ctx, w, h } = fitCanvas(canvas);
      const p = pal();
      ctx.clearRect(0, 0, w, h);
      ctx.textBaseline = 'middle';
      const font = (px, bold) => { ctx.font = (bold ? '600 ' : '') + px + 'px ' + cssVar('--mono', 'monospace'); };

      const bars = slice.bars || [];
      const tick = slice.tickSize || 1;
      if (!bars.length) {
        font(11); ctx.fillStyle = p.muted; ctx.textAlign = 'left';
        ctx.fillText('waiting for live trades — footprint renders only what arrives this session (§0.7)', 10, 18);
        return;
      }

      // Visible window: as many most-recent bars as fit at ≥ BAR_W_MIN each.
      const plotW = w - GUT_VP - GUT_AXIS;
      const cellsH = h - ROW_DELTA - ROW_TVOL - ROW_TIME;
      const nFit = Math.max(1, Math.floor(plotW / BAR_W_MIN));
      const vis = bars.slice(-nFit);
      const barW = plotW / vis.length;

      // Price range across visible bars, snapped DOWN to the store's tick grid
      // (levels are floor-bucketed — FootprintStore doc) so rows align exactly.
      let minP = Infinity, maxP = -Infinity, maxSide = 0;
      for (const b of vis) {
        for (const lv of b.levels) {
          if (lv.price < minP) minP = lv.price;
          if (lv.price > maxP) maxP = lv.price;
          if (lv.buy > maxSide) maxSide = lv.buy;
          if (lv.sell > maxSide) maxSide = lv.sell;
        }
      }
      if (!Number.isFinite(minP)) return;
      const nRows = Math.round((maxP - minP) / tick) + 1;
      if (nRows > 800) {
        // A degenerate tick/range combo would draw sub-pixel mush; say so.
        font(11); ctx.fillStyle = p.muted; ctx.textAlign = 'left';
        ctx.fillText('price range spans ' + nRows + ' rows at $' + tick + ' grouping — pick a coarser tick group', 10, 18);
        return;
      }
      const rowH = cellsH / nRows;
      const yOf = (price) => ((maxP - price) / tick) * rowH;   // row TOP edge
      const showText = rowH >= 10 && barW >= 56;

      // Cells. Half-cell background alpha ∝ sqrt(side volume / max side volume)
      // — sqrt for perceptual spread (linear alpha buries mid-size levels).
      for (let i = 0; i < vis.length; i++) {
        const b = vis[i];
        const x0 = i * barW;
        const half = barW / 2;
        for (const lv of b.levels) {
          const y = yOf(lv.price);
          const aS = lv.sell > 0 ? 0.07 + 0.48 * Math.sqrt(lv.sell / maxSide) : 0;
          const aB = lv.buy > 0 ? 0.07 + 0.48 * Math.sqrt(lv.buy / maxSide) : 0;
          if (aS > 0) { ctx.fillStyle = rgba(p.down, aS); ctx.fillRect(x0, y, half - 0.5, rowH - 0.5); }
          if (aB > 0) { ctx.fillStyle = rgba(p.up, aB); ctx.fillRect(x0 + half + 0.5, y, half - 1, rowH - 0.5); }
          // Imbalance flags — OUTLINED half-cells; only ever true on finished
          // bars (the store computes flags at bar close, never mid-bar).
          if (lv.sellImb) { ctx.strokeStyle = p.down; ctx.lineWidth = 1.5; ctx.strokeRect(x0 + 1, y + 0.75, half - 2.5, rowH - 2); }
          if (lv.buyImb) { ctx.strokeStyle = p.up; ctx.lineWidth = 1.5; ctx.strokeRect(x0 + half + 1.5, y + 0.75, half - 3, rowH - 2); }
          if (showText) {
            font(9);
            ctx.fillStyle = p.down; ctx.textAlign = 'right';
            ctx.fillText(fmtVol(lv.sell), x0 + half - 8, y + rowH / 2);
            ctx.fillStyle = p.up; ctx.textAlign = 'left';
            ctx.fillText(fmtVol(lv.buy), x0 + half + 8, y + rowH / 2);
            ctx.fillStyle = p.muted; ctx.textAlign = 'center';
            ctx.fillText('×', x0 + half, y + rowH / 2);
          }
        }
        // Column separator; the OPEN bar gets a dashed border + 'live' tag so a
        // half-formed bar can never be misread as a finished print (§0.1).
        ctx.strokeStyle = p.border; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(x0 + 0.5, 0); ctx.lineTo(x0 + 0.5, cellsH); ctx.stroke();
        if (!b.finished) {
          ctx.save();
          ctx.setLineDash([4, 3]);
          ctx.strokeStyle = p.accent2; ctx.lineWidth = 1;
          ctx.strokeRect(x0 + 1, 0.5, barW - 2, cellsH - 1);
          ctx.restore();
          font(9, true); ctx.fillStyle = p.accent2; ctx.textAlign = 'center';
          ctx.fillText('live', x0 + half, 7);
        }

        // Footer: signed delta (colored, with sign — non-color cue) + total.
        const yD = cellsH + ROW_DELTA / 2, yV = cellsH + ROW_DELTA + ROW_TVOL / 2;
        font(10, true); ctx.textAlign = 'center';
        ctx.fillStyle = b.delta > 0 ? p.up : b.delta < 0 ? p.down : p.muted;
        ctx.fillText((b.delta > 0 ? '+' : '') + fmtVol(b.delta), x0 + half, yD);
        font(10); ctx.fillStyle = p.muted;
        ctx.fillText(fmtVol(b.totalVol), x0 + half, yV);
      }

      // Bar time labels (UTC), thinned to avoid overlap.
      const step = Math.max(1, Math.ceil(56 / barW));
      font(9); ctx.fillStyle = p.muted; ctx.textAlign = 'center';
      for (let i = vis.length - 1; i >= 0; i -= step) {
        ctx.fillText(hm(vis[i].t), i * barW + barW / 2, cellsH + ROW_DELTA + ROW_TVOL + ROW_TIME / 2);
      }
      // Footer row captions in the gutter.
      font(9); ctx.textAlign = 'left'; ctx.fillStyle = p.muted;
      ctx.fillText('Δ', plotW + 4, cellsH + ROW_DELTA / 2);
      ctx.fillText('vol', plotW + 4, cellsH + ROW_DELTA + ROW_TVOL / 2);

      // ── Right gutter: session VP histogram + POC/VAH/VAL (ProfileStore) ──
      // The profile is FULL-SESSION; the gutter clips it to the visible price
      // window (levels outside simply aren't drawn — nothing is rescaled to
      // pretend the window is the session).
      const prof = slice.profile;
      const gx = plotW + 14;   // small gap after the Δ/vol captions column
      if (prof && prof.levels && prof.levels.length) {
        let maxV = 0;
        for (const lv of prof.levels) if (lv.price >= minP && lv.price <= maxP && lv.vol > maxV) maxV = lv.vol;
        if (maxV > 0) {
          for (const lv of prof.levels) {
            if (lv.price < minP || lv.price > maxP) continue;
            const y = yOf(lv.price);
            ctx.fillStyle = rgba(p.accent2, 0.45);   // matches index.html's volume-at-price hue
            ctx.fillRect(gx, y + 0.5, (GUT_VP - 18) * (lv.vol / maxV), Math.max(1, rowH - 1));
          }
        }
        // POC (accent solid) / VAH / VAL (muted dashed) across cells + gutter,
        // drawn only when inside the visible window — an off-screen arrow would
        // imply knowledge of levels the eye can't verify.
        const hline = (price, color, dash, label) => {
          if (!Number.isFinite(price) || price < minP || price > maxP) return;
          const y = yOf(price) + rowH / 2;
          ctx.save();
          if (dash) ctx.setLineDash([5, 4]);
          ctx.strokeStyle = color; ctx.lineWidth = 1;
          ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w - GUT_AXIS, y); ctx.stroke();
          ctx.restore();
          font(9, true); ctx.fillStyle = color; ctx.textAlign = 'left';
          ctx.fillText(label, w - GUT_AXIS + 2, y);
        };
        hline(prof.poc, p.accent, false, 'POC');
        hline(prof.vah, p.muted, true, 'VAH');
        hline(prof.val, p.muted, true, 'VAL');
      }

      // Price axis labels (right edge), thinned to ~14px spacing.
      const labStep = Math.max(1, Math.ceil(14 / rowH));
      font(9); ctx.fillStyle = p.muted; ctx.textAlign = 'right';
      for (let r = 0; r < nRows; r += labStep) {
        const price = maxP - r * tick;
        ctx.fillText(fmtUsd(price), w - 2, yOf(price) + rowH / 2);
      }

      // ── Crosshair + readout (cells area only) ──
      if (mouse && mouse.x >= 0 && mouse.x < plotW && mouse.y >= 0 && mouse.y < cellsH) {
        const bi = Math.min(vis.length - 1, Math.floor(mouse.x / barW));
        const ri = Math.min(nRows - 1, Math.floor(mouse.y / rowH));
        const price = maxP - ri * tick;
        const b = vis[bi];
        let lv = null;
        for (const l of b.levels) if (Math.abs(l.price - price) < tick / 2) { lv = l; break; }
        ctx.save();
        ctx.setLineDash([3, 3]);
        ctx.strokeStyle = rgba(p.fg, 0.4); ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(0, mouse.y + 0.5); ctx.lineTo(w - GUT_AXIS, mouse.y + 0.5); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(bi * barW + barW / 2, 0); ctx.lineTo(bi * barW + barW / 2, cellsH); ctx.stroke();
        ctx.restore();
        // Readout box, top-left: bar time · level price · sell×buy · bar Δ.
        const txt = hm(b.t) + (b.finished ? '' : ' (live)') + ' · ' + fmtUsd(price)
          + ' · sell ' + (lv ? fmtVol(lv.sell) : '0') + ' × buy ' + (lv ? fmtVol(lv.buy) : '0')
          + ' · bar Δ ' + (b.delta > 0 ? '+' : '') + fmtVol(b.delta);
        font(10);
        const tw = ctx.measureText(txt).width;
        ctx.fillStyle = rgba(p.panel2, 0.92);
        ctx.fillRect(6, 4, tw + 14, 18);
        ctx.strokeStyle = p.border; ctx.strokeRect(6.5, 4.5, tw + 13, 17);
        ctx.fillStyle = p.fg; ctx.textAlign = 'left';
        ctx.fillText(txt, 13, 13.5);
      }
    }

    /** slice = { bars, profile, cvd, tickSize, nowMs } — all straight from the
     *  stores; nothing here mutates them. */
    function render(slice) {
      lastSlice = slice;
      draw(slice);
      renderCvd(slice.cvd, slice.nowMs || Date.now());
    }

    return { mount, render };
  }

  // ═══ DomLadderView — grouped price ladder around the spread ═══
  //
  // Fixed row pool (nLevels asks + spread + nLevels bids) built once at mount;
  // render() only rewrites text/styles — no per-frame DOM churn, and flash
  // animations survive because the elements persist. Columns:
  //   sold(session) | size(ask) | price | size(bid) | bought(session) | Δ(session)
  // Session sold/bought/Δ per level come from FootprintStore's bars — i.e. the
  // finished-bar ring (≤120 bars) plus the open bar, NOT all-of-session once
  // the ring wraps. Stated on the panel; we don't stretch the label.
  //
  // Grid-alignment honesty note: ladder ask rows bucket UP and footprint
  // levels bucket DOWN (both per snapTick's conservative convention), so an
  // ask row at grid price P shows the session volume of the floor bucket AT P
  // — the [P, P+tick) bucket. Same grid, half-open interval semantics; the
  // alternative (re-bucketing trades up for asks) would double-count.
  function DomLadderView() {
    let root = null, tbody = null, rows = [], nLevels = 12;
    let prevQty = new Map();   // 'a'/'b'+price → qty, for change-flash detection

    function mount(el, opts) {
      root = el;
      nLevels = (opts && opts.levels) || 12;
      const table = document.createElement('table');
      table.className = 'ladder';
      table.innerHTML = '<thead><tr>'
        + '<th title="session sell volume at level (footprint ring window)">sold</th>'
        + '<th title="resting ask size (grouped)">ask</th>'
        + '<th>price</th>'
        + '<th title="resting bid size (grouped)">bid</th>'
        + '<th title="session buy volume at level (footprint ring window)">bought</th>'
        + '<th title="session buy − sell at level">Δ</th>'
        + '</tr></thead>';
      tbody = document.createElement('tbody');
      // Ask block (worst→best downward), spread row, bid block (best→worst).
      for (let i = 0; i < nLevels; i++) tbody.appendChild(mkRow('ask'));
      const sp = document.createElement('tr');
      sp.className = 'spread';
      sp.innerHTML = '<td colspan="6">—</td>';
      tbody.appendChild(sp);
      for (let i = 0; i < nLevels; i++) tbody.appendChild(mkRow('bid'));
      table.appendChild(tbody);
      root.appendChild(table);
      rows = Array.prototype.slice.call(tbody.children);
    }

    function mkRow(side) {
      const tr = document.createElement('tr');
      tr.className = side;
      tr.innerHTML = '<td class="sess"></td><td class="sz ask-sz"></td><td class="px"></td>'
        + '<td class="sz bid-sz"></td><td class="sess"></td><td class="dl"></td>';
      return tr;
    }

    /** Restart the one-shot flash animation on a cell (classic reflow trick —
     *  remove class, force style flush, re-add). */
    function flash(cell) {
      cell.classList.remove('cell-flash');
      void cell.offsetWidth;
      cell.classList.add('cell-flash');
    }

    /** slice = { grouped:{bids,asks} (best-first), best:{bid,ask}, bars, tickSize } */
    function render(slice) {
      if (!tbody) return;
      const g = slice.grouped || { bids: [], asks: [] };
      const bars = slice.bars || [];
      const tick = slice.tickSize || 1;
      const dp = tick >= 1 ? 0 : 2;

      // Session per-level aggressor volume from the footprint bars (≤121 bars ×
      // their levels — small; recomputed only on dirty renders).
      const sess = new Map();
      for (const b of bars) {
        for (const lv of b.levels) {
          let e = sess.get(lv.price);
          if (!e) { e = { buy: 0, sell: 0 }; sess.set(lv.price, e); }
          e.buy += lv.buy; e.sell += lv.sell;
        }
      }

      const asks = g.asks.slice(0, nLevels);   // best-first ascending
      const bids = g.bids.slice(0, nLevels);   // best-first descending
      let maxQty = 0;
      for (const r of asks) if (r.qty > maxQty) maxQty = r.qty;
      for (const r of bids) if (r.qty > maxQty) maxQty = r.qty;
      const nextQty = new Map();
      const p = pal();

      const setRow = (tr, lvl, side) => {
        const tds = tr.children;
        if (!lvl) {
          for (let i = 0; i < 6; i++) tds[i].textContent = '';
          tds[1].style.background = ''; tds[3].style.background = '';
          return;
        }
        const s = sess.get(lvl.price);
        const buy = s ? s.buy : 0, sell = s ? s.sell : 0, d = buy - sell;
        tds[0].textContent = sell > 0 ? fmtVol(sell) : '';
        tds[4].textContent = buy > 0 ? fmtVol(buy) : '';
        tds[5].textContent = (buy || sell) ? ((d > 0 ? '+' : '') + fmtVol(d)) : '';
        tds[5].className = 'dl ' + (d > 0 ? 'pos' : d < 0 ? 'neg' : '');
        tds[2].textContent = fmtUsd(lvl.price, dp);
        // Size + depth bar: ask bar grows leftward from the price column, bid
        // bar rightward — mirrored direction is the second non-color cue.
        const wPct = maxQty > 0 ? Math.round(100 * lvl.qty / maxQty) : 0;
        const askCell = tds[1], bidCell = tds[3];
        if (side === 'ask') {
          askCell.textContent = fmtQty(lvl.qty);
          askCell.style.background = 'linear-gradient(to left,' + rgba(p.down, 0.22) + ' ' + wPct + '%,transparent ' + wPct + '%)';
          bidCell.textContent = ''; bidCell.style.background = '';
        } else {
          bidCell.textContent = fmtQty(lvl.qty);
          bidCell.style.background = 'linear-gradient(to right,' + rgba(p.up, 0.22) + ' ' + wPct + '%,transparent ' + wPct + '%)';
          askCell.textContent = ''; askCell.style.background = '';
        }
        // Flash the size cell on a qty change at the SAME price (a level that
        // merely scrolled into a row is not a change).
        const key = (side === 'ask' ? 'a' : 'b') + lvl.price;
        nextQty.set(key, lvl.qty);
        if (prevQty.has(key) && prevQty.get(key) !== lvl.qty) flash(side === 'ask' ? askCell : bidCell);
      };

      // Ask block: rows[0..nLevels-1] top→down = worst→best (asks reversed).
      for (let i = 0; i < nLevels; i++) setRow(rows[i], asks[nLevels - 1 - i] || null, 'ask');
      // Spread row.
      const best = slice.best || {};
      const spTd = rows[nLevels].firstChild;
      spTd.textContent = (best.bid && best.ask)
        ? 'spread ' + fmtUsd(best.ask[0] - best.bid[0], 2) + ' · mid ' + fmtUsd((best.ask[0] + best.bid[0]) / 2, 1)
        : 'waiting for book…';
      // Bid block.
      for (let i = 0; i < nLevels; i++) setRow(rows[nLevels + 1 + i], bids[i] || null, 'bid');

      prevQty = nextQty;
    }

    return { mount, render };
  }

  // ═══ TapeView — time & sales, newest-first ═══
  //
  // The min-notional filter INPUT physically lives in the settings row
  // (terminal.html); this view wires its listener via opts.filterInput +
  // opts.onFilter so filter behavior is view-owned while persistence stays in
  // terminal.js (which also does the actual filtering via TapeStore.filtered —
  // the view renders an already-filtered slice).
  //
  // Whale emphasis: notional ≥ $250k (§4) → bold + '◆' marker (marker = the
  // non-color cue). Every print carries its exchange tag — mixed-venue tape is
  // deliberate and each row says which wire it came from (§0.7 per-source label).
  function TapeView() {
    let root = null, list = null, whaleUsd = 250000;
    const MAX_ROWS = 60;   // DOM budget; the ring holds more, the eye doesn't

    function mount(el, opts) {
      root = el;
      const o = opts || {};
      whaleUsd = Number.isFinite(o.whaleUsd) ? o.whaleUsd : 250000;
      if (o.filterInput && typeof o.onFilter === 'function') {
        o.filterInput.addEventListener('input', () => {
          const v = Number(o.filterInput.value);
          o.onFilter(Number.isFinite(v) && v > 0 ? v : 0);
        });
      }
      root.insertAdjacentHTML('beforeend',
        '<div class="tape-row tape-head"><span>UTC</span><span>ex</span><span>price</span><span>size</span><span>notional</span></div>');
      list = document.createElement('div');
      list.className = 'tape-list';
      root.appendChild(list);
    }

    /** slice = { trades } — newest-first, already min-notional-filtered. */
    function render(slice) {
      if (!list) return;
      const trades = (slice.trades || []).slice(0, MAX_ROWS);
      if (!trades.length) {
        list.innerHTML = '<div class="chart-na">no prints yet (or none clear the filter) — the tape shows only trades that arrived this session.</div>';
        return;
      }
      let html = '';
      for (const t of trades) {
        const notional = t.price * t.qty;
        const whale = notional >= whaleUsd;
        const dir = t.aggressorBuy ? 'up' : 'down';   // aggressor coloring (§0.6 normalized upstream)
        html += '<div class="tape-row' + (whale ? ' whale' : '') + '">'
          + '<span class="ts">' + hms(t.ts) + '</span>'
          + '<span class="ex ex-' + esc(t.ex) + '">' + esc(t.ex === 'coinbase' ? 'cb' : t.ex) + '</span>'
          + '<span class="px delta ' + dir + '">' + fmtUsd(t.price, 1) + '</span>'
          + '<span class="qty">' + fmtQty(t.qty) + '</span>'
          + '<span class="ntl">' + (whale ? '◆ ' : '') + fmtCompactUsd(notional) + '</span>'
          + '</div>';
      }
      list.innerHTML = html;
    }

    return { mount, render };
  }

  // ═══ AggBookView — multi-exchange aggregated book (canvas) ═══
  //
  // Two mirrored columns: bids (left, best at top) and asks (right, best at
  // top), one horizontal bar per grouped level STACKED by exchange (fixed
  // EX_TOKEN colors), plus a cumulative-depth step curve per side. Rows pair
  // by RANK, not price — each side lists its own best-first ladder (that is
  // what an aggregated book is; a price-aligned merge is the DOM ladder's job).
  function AggBookView() {
    let root = null, canvas = null, legend = null, nLevels = 14;
    let legendKey = '';   // rebuilt only when the participating-exchange set changes

    function mount(el, opts) {
      root = el;
      nLevels = (opts && opts.levels) || 14;
      canvas = document.createElement('canvas');
      canvas.className = 'term-canvas agg-canvas';
      root.appendChild(canvas);
      legend = document.createElement('div');
      legend.className = 'term-legend';
      root.appendChild(legend);
    }

    /** slice = { grouped:{bids,asks} } from AggBookStore.grouped(tick, nLevels):
     *  rows are {price, total, byEx} best-first. */
    function render(slice) {
      if (!canvas) return;
      const g = slice.grouped || { bids: [], asks: [] };
      const bids = g.bids.slice(0, nLevels), asks = g.asks.slice(0, nLevels);
      const { ctx, w, h } = fitCanvas(canvas);
      const p = pal();
      ctx.clearRect(0, 0, w, h);
      ctx.textBaseline = 'middle';
      ctx.font = '9px ' + cssVar('--mono', 'monospace');

      // Legend: one chip per exchange actually present (a dead leg contributes
      // no levels and silently leaves the legend — no leg is required, §4).
      const exs = [];
      const seen = {};
      for (const rows of [bids, asks]) {
        for (const r of rows) for (const ex in r.byEx) if (!seen[ex]) { seen[ex] = 1; exs.push(ex); }
      }
      exs.sort();
      const lk = exs.join(',');
      if (lk !== legendKey) {
        legendKey = lk;
        legend.innerHTML = exs.map((ex) =>
          '<span><i class="sw" style="background:' + exColor(p, ex) + '"></i>' + esc(ex) + '</span>'
        ).join('') + '<span class="lg-note">stacked size per $-grouped level · line = cumulative depth</span>';
      }

      if (!bids.length && !asks.length) {
        ctx.fillStyle = p.muted; ctx.textAlign = 'left'; ctx.font = '11px ' + cssVar('--mono', 'monospace');
        ctx.fillText('waiting for depth…', 10, 16);
        return;
      }

      const centerX = w / 2, GAP = 6;
      const halfW = centerX - GAP;
      const rowH = h / nLevels;
      let maxTot = 0, cumMax = 0, c = 0;
      for (const r of bids) { if (r.total > maxTot) maxTot = r.total; }
      for (const r of asks) { if (r.total > maxTot) maxTot = r.total; }
      c = 0; for (const r of bids) { c += r.total; } if (c > cumMax) cumMax = c;
      c = 0; for (const r of asks) { c += r.total; } if (c > cumMax) cumMax = c;
      if (maxTot <= 0) return;
      // Bars use ~62% of the half width; the cumulative curve the full half —
      // two different x-scales, visually separable (bar vs line), both labeled.
      const barScale = (halfW * 0.62) / maxTot;
      const cumScale = (halfW - 4) / cumMax;

      // Center divider.
      ctx.strokeStyle = p.border;
      ctx.beginPath(); ctx.moveTo(centerX + 0.5, 0); ctx.lineTo(centerX + 0.5, h); ctx.stroke();

      const drawSide = (rows, isBid) => {
        const sideCol = isBid ? p.up : p.down;
        const cumPts = [];
        let cum = 0;
        for (let i = 0; i < rows.length; i++) {
          const r = rows[i];
          const y = i * rowH;
          // Stacked segments from the center outward, per exchange (sorted for
          // stable stacking order frame-to-frame).
          let off = 0;
          const exKeys = Object.keys(r.byEx).sort();
          for (const ex of exKeys) {
            const seg = r.byEx[ex] * barScale;
            ctx.fillStyle = rgba(exColor(p, ex), 0.8);
            if (isBid) ctx.fillRect(centerX - GAP - off - seg, y + 1.5, seg, rowH - 3);
            else ctx.fillRect(centerX + GAP + off, y + 1.5, seg, rowH - 3);
            off += seg;
          }
          cum += r.total;
          cumPts.push([isBid ? centerX - GAP - cum * cumScale : centerX + GAP + cum * cumScale, y + rowH / 2]);
          // Price at the inner edge (side-colored — plus side POSITION as the
          // redundant cue), qty at the bar tip.
          if (rowH >= 11) {
            ctx.fillStyle = sideCol; ctx.textAlign = isBid ? 'right' : 'left';
            ctx.fillText(fmtUsd(r.price), isBid ? centerX - GAP - 3 : centerX + GAP + 3, y + rowH / 2 - (rowH >= 20 ? 4 : 0));
            if (rowH >= 20) {
              ctx.fillStyle = p.muted;
              ctx.fillText(fmtQty(r.total), isBid ? centerX - GAP - 3 : centerX + GAP + 3, y + rowH / 2 + 5);
            }
          }
        }
        // Cumulative depth step-curve.
        if (cumPts.length > 1) {
          ctx.strokeStyle = rgba(sideCol, 0.9); ctx.lineWidth = 1.25;
          ctx.beginPath();
          ctx.moveTo(cumPts[0][0], cumPts[0][1]);
          for (let i = 1; i < cumPts.length; i++) {
            ctx.lineTo(cumPts[i][0], cumPts[i - 1][1]);   // step: horizontal…
            ctx.lineTo(cumPts[i][0], cumPts[i][1]);       // …then down
          }
          ctx.stroke();
        }
      };
      drawSide(bids, true);
      drawSide(asks, false);

      // Side captions.
      ctx.font = '600 9px ' + cssVar('--mono', 'monospace');
      ctx.fillStyle = p.up; ctx.textAlign = 'left'; ctx.fillText('BIDS', 4, 8);
      ctx.fillStyle = p.down; ctx.textAlign = 'right'; ctx.fillText('ASKS', w - 4, 8);
    }

    return { mount, render };
  }

  // ═══ HeaderStatsView — mark/index/basis/funding/OI + session range + chips ═══
  //
  // Primary row = Bybit (the primary WS leg, §2); a secondary short row carries
  // the Binance-Futures REST-polled funding/OI so the cross-exchange columns
  // are labeled per source, never blended (§0.7). Basis bp = (mark−index)/
  // index·1e4. The funding countdown is the ONE place wall-clock time enters a
  // view (slice.nowMs, supplied by terminal.js) — it measures time-until, not
  // data.
  //
  // Conn chips reuse the statchip visual language from index.html/styles.css;
  // state classes (.live/.stale/.error) are class-scoped in terminal.css
  // because the analytics page scoped them to #conn-status ids.
  function HeaderStatsView() {
    let root = null;
    const cells = {};   // key → value <span>
    const chips = {};   // ex → { el, text }

    function mkStat(grid, key, label, title) {
      const d = document.createElement('div');
      d.className = 'tstat';
      d.title = title || '';
      d.innerHTML = '<span class="k">' + label + '</span><span class="v num">—</span>';
      grid.appendChild(d);
      cells[key] = d.lastChild;
    }

    function mount(el) {
      root = el;
      // Chip row first: connection health above everything — a dead feed must
      // be the first thing the eye hits (watchdog rail, livewire.js).
      const chipRow = document.createElement('div');
      chipRow.className = 'term-chips';
      for (const ex of ['bybit', 'binancef', 'coinbase']) {
        const chip = document.createElement('span');
        chip.className = 'statchip term-chip';
        chip.innerHTML = '<span class="dot"></span><span class="chip-text">' + ex + ': connecting…</span>';
        chipRow.appendChild(chip);
        chips[ex] = { el: chip, text: chip.querySelector('.chip-text') };
      }
      chipRow.insertAdjacentHTML('beforeend',
        '<span class="chips-note">bybit = primary WS (trades/book/liq/mark/OI) · binancef = depth WS + REST mark/OI · coinbase = spot tape</span>');
      root.appendChild(chipRow);

      const grid = document.createElement('div');
      grid.className = 'term-stats';
      mkStat(grid, 'mark', 'mark (bybit)', 'Bybit perp mark price');
      mkStat(grid, 'index', 'index (bybit)', 'Bybit index price');
      mkStat(grid, 'basis', 'basis', '(mark − index) / index · 1e4, basis points');
      mkStat(grid, 'funding', 'funding / next', 'Current funding rate + countdown to next funding');
      mkStat(grid, 'oi', 'OI (bybit)', 'Open interest, BTC');
      mkStat(grid, 'oiUsd', 'OI $ @ mark', 'Open interest × mark price (USD)');
      mkStat(grid, 'hi', 'session high', 'Highest Bybit perp print since page open — session-local, no backfill (§0.7)');
      mkStat(grid, 'lo', 'session low', 'Lowest Bybit perp print since page open');
      mkStat(grid, 'bnFunding', 'funding (binancef)', 'Binance Futures funding (REST poll, 5s)');
      mkStat(grid, 'bnBasis', 'basis (binancef)', 'Binance Futures (mark − index) / index · 1e4');
      mkStat(grid, 'bnOi', 'OI (binancef)', 'Binance Futures open interest, BTC (REST poll, 60s)');
      root.appendChild(grid);
    }

    /** slice = { marks:{ex→mark ev}, ois:{ex→oi ev}, statuses:{ex→{kind,msg}},
     *  sessionHigh, sessionLow, nowMs } */
    function render(slice) {
      const set = (k, v, cls) => {
        cells[k].textContent = v;
        cells[k].className = 'v num' + (cls ? ' ' + cls : '');
      };
      const bp = (m) => (m && Number.isFinite(m.mark) && Number.isFinite(m.index) && m.index !== 0)
        ? ((m.mark - m.index) / m.index) * 1e4 : NaN;

      const by = slice.marks && slice.marks.bybit;
      set('mark', by ? fmtUsd(by.mark, 1) : '—');
      set('index', by ? fmtUsd(by.index, 1) : '—');
      const bby = bp(by);
      set('basis', Number.isFinite(bby) ? bby.toFixed(1) + ' bp' : '—', bby > 0 ? 'pos' : bby < 0 ? 'neg' : '');
      if (by && Number.isFinite(by.fundingRate)) {
        const cd = countdown(by.nextFundingTs - slice.nowMs);
        set('funding', (by.fundingRate * 100).toFixed(4) + '% · ' + cd,
          by.fundingRate > 0 ? 'pos' : by.fundingRate < 0 ? 'neg' : '');
      } else set('funding', '—');
      const oiBy = slice.ois && slice.ois.bybit;
      set('oi', oiBy ? fmtQty(oiBy.oi) + ' BTC' : '—');
      set('oiUsd', (oiBy && by && Number.isFinite(by.mark)) ? fmtCompactUsd(oiBy.oi * by.mark) : '—');
      set('hi', fmtUsd(slice.sessionHigh, 1));
      set('lo', fmtUsd(slice.sessionLow, 1));

      const bn = slice.marks && slice.marks.binancef;
      set('bnFunding', (bn && Number.isFinite(bn.fundingRate)) ? (bn.fundingRate * 100).toFixed(4) + '%' : '—',
        bn && bn.fundingRate > 0 ? 'pos' : bn && bn.fundingRate < 0 ? 'neg' : '');
      const bbn = bp(bn);
      set('bnBasis', Number.isFinite(bbn) ? bbn.toFixed(1) + ' bp' : '—', bbn > 0 ? 'pos' : bbn < 0 ? 'neg' : '');
      const oiBn = slice.ois && slice.ois.binancef;
      set('bnOi', oiBn ? fmtQty(oiBn.oi) + ' BTC' : '—');

      // Conn chips: mirror app.js updateLiveStatus semantics — 'stale' and
      // 'reconnecting' share the amber dot ('reconnecting' is just stale with
      // intent); 'error' is red; anything unseen stays grey "connecting…".
      const st = slice.statuses || {};
      for (const ex in chips) {
        const s = st[ex];
        const chip = chips[ex];
        chip.el.classList.remove('live', 'stale', 'error');
        if (!s) continue;
        if (s.kind === 'open') { chip.el.classList.add('live'); chip.text.textContent = ex + ': live'; }
        else if (s.kind === 'stale') { chip.el.classList.add('stale'); chip.text.textContent = ex + ': ' + (s.msg || 'stale'); }
        else if (s.kind === 'reconnecting') { chip.el.classList.add('stale'); chip.text.textContent = ex + ': reconnecting…'; }
        else if (s.kind === 'error') { chip.el.classList.add('error'); chip.text.textContent = ex + ': offline'; }
      }
    }

    return { mount, render };
  }

  // ═══ LiqFeedView — recent liquidations + rolling notional sums ═══
  //
  // side = the LIQUIDATED position (normalized upstream, §3 schema): a LONG
  // liquidation is a forced SELL → --down badge; a SHORT liquidation is a
  // forced BUY(-back) → --up badge. The text badge is the non-color cue.
  // Rolling 1m/5m sums come precomputed from LiqStore.sumWindow with the
  // caller's wall clock (the store itself never reads a clock — replay rail).
  function LiqFeedView() {
    let root = null, sumsEl = null, list = null;
    const MAX_ROWS = 40;

    function mount(el) {
      root = el;
      sumsEl = document.createElement('div');
      sumsEl.className = 'liq-sums num';
      sumsEl.innerHTML = '<span>1m: <b>—</b></span><span>5m: <b>—</b></span>'
        + '<span class="lg-note">forced-order notional, rolling window</span>';
      root.appendChild(sumsEl);
      root.insertAdjacentHTML('beforeend',
        '<div class="liq-row liq-head"><span>UTC</span><span>side</span><span>price</span><span>size</span><span>notional</span></div>');
      list = document.createElement('div');
      list.className = 'liq-list';
      root.appendChild(list);
    }

    /** slice = { recent (newest-first), sum1m, sum5m } */
    function render(slice) {
      if (!list) return;
      const bs = sumsEl.querySelectorAll('b');
      bs[0].textContent = fmtCompactUsd(slice.sum1m || 0);
      bs[1].textContent = fmtCompactUsd(slice.sum5m || 0);
      const recent = (slice.recent || []).slice(0, MAX_ROWS);
      if (!recent.length) {
        list.innerHTML = '<div class="chart-na">no liquidations seen this session — only what actually printed on the wire is shown (§0.7).</div>';
        return;
      }
      let html = '';
      for (const l of recent) {
        const long = l.side === 'long';
        html += '<div class="liq-row">'
          + '<span class="ts">' + hms(l.ts) + '</span>'
          + '<span class="liq-badge ' + (long ? 'long' : 'short') + '">' + (long ? 'LONG LIQ' : 'SHORT LIQ') + '</span>'
          + '<span class="px">' + fmtUsd(l.price, 1) + '</span>'
          + '<span class="qty">' + fmtQty(l.qty) + '</span>'
          + '<span class="ntl">' + fmtCompactUsd(l.notionalUsd) + '</span>'
          + '</div>';
      }
      list.innerHTML = html;
    }

    return { mount, render };
  }

  // ─── Export — ONE global + Node (quant.js dual-export pattern) ──────────

  const TerminalViews = {
    FootprintView, DomLadderView, TapeView, AggBookView, HeaderStatsView, LiqFeedView,
  };

  if (typeof module !== 'undefined' && module.exports) module.exports = TerminalViews;
  if (typeof global !== 'undefined') global.BTCQ_TERMINAL_VIEWS = TerminalViews;
})(typeof globalThis !== 'undefined' ? globalThis : this);
