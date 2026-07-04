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
   *  (tape tag, agg-book stack, CVD venue lines, legend) so one venue is always
   *  one color. okx = --c3 (O-2, §4b — the CVD 'whale' bucket line moved to
   *  --c6 so venue-pink stays venue-pink inside the same chart).
   *  Deliberately NOT --up/--down (venues aren't P&L) and NOT --accent (chrome). */
  const EX_TOKEN = { bybit: 'c2', binancef: 'c1', coinbase: 'c4', okx: 'c3' };
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
  // per-EXCHANGE lines + an exact Σ (O-2, §4b) plus the O-1 per-notional-bucket
  // lines, every one labeled in the legend. If the vendored lib is missing we
  // show an honest note instead of hand-rolling a fake — the page must never
  // carry a silently-broken chart.
  function FootprintView() {
    let root = null, canvas = null, cvdEl = null;
    let cvdChart = null, cvdSeries = null, cvdExs = null, cvdBuckets = null, cvdNote = false;
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
      if (cvdEl) initCvd(o.buckets || [], o.cvdExs || ['bybit']);
    }

    function scheduleDraw() {
      if (drawQueued || !lastSlice) return;
      drawQueued = true;
      requestAnimationFrame(() => { drawQueued = false; if (lastSlice) draw(lastSlice); });
    }

    // ── CVD subchart (lightweight-charts; vendored, no CDN — DESIGN §4) ──
    //
    // O-2 (§4b) line families, every one labeled in the legend:
    //   1) 'Σ all venues' (--c1, 2px) — EXACT sum of the per-exchange cumsums
    //      (see mergeStepSum: step-function arithmetic, never interpolation).
    //   2) one line per exchange (bybit/okx/coinbase) in the fixed EX_TOKEN
    //      hues — the same color a venue wears on the tape and agg-book stack,
    //      so "which venue is diverging" reads across panels (§0.7 per-source
    //      labels; §4b "per-exchange, per-labeled series").
    //   3) bybit by trade-size buckets (O-1 feature, kept): ONE hue (--c5)
    //      with a distinct dash pattern per bucket, whale = --c6 — dash is the
    //      non-color cue that separates the size family from the venue lines.
    function initCvd(buckets, exs) {
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
        // scaleMargins keep the ONE lastValue label (the Σ line, below) clear
        // of the pane edges — without them the label clips at the top when Σ
        // is the session extreme (visual-defect fix; §4 legibility).
        rightPriceScale: { borderColor: p.border, scaleMargins: { top: 0.12, bottom: 0.1 } },
        crosshair: { mode: 0 },
        localization: { priceFormatter: fmtCompactUsd },   // CVD is USD notional — compact axis
      });
      cvdExs = exs.slice();
      cvdBuckets = buckets.slice();
      cvdSeries = {};
      const legend = document.createElement('div');
      legend.className = 'term-cvd-legend';
      const addLine = (key, color, width, style, label) => {
        cvdSeries[key] = cvdChart.addLineSeries({
          color, lineWidth: width, lineStyle: style,
          // Right-axis labels: ONLY the Σ all-venues line keeps its lastValue
          // pill — with 8 series the per-line pills stack into an unreadable
          // pileup on the price scale (visual-defect fix). Identification of
          // the other lines is the LEGEND's job (every line is labeled there,
          // §4b per-source labels — nothing becomes anonymous by this).
          priceLineVisible: false, lastValueVisible: key === 'sum',
          crosshairMarkerVisible: false,
        });
        legend.insertAdjacentHTML('beforeend',
          '<span><i class="sw" style="background:' + color + '"></i>' + label + '</span>');
      };
      // LC.LineStyle: 0 Solid, 1 Dotted, 2 Dashed, 3 LargeDashed.
      addLine('sum', p.c1, 2, 0, '&Sigma; all venues');
      for (const ex of cvdExs) addLine('ex:' + ex, exColor(p, ex), 1, 0, esc(ex));
      const bucketStyle = [2, 1, 3];   // dashed / dotted / large-dashed, thresholds ascending
      cvdBuckets.forEach((k, i) => {
        if (k === 'whale') addLine('bucket:whale', p.c6, 1, 0, 'bybit &gt; largest bucket (whale)');
        else addLine('bucket:' + k, p.c5, 1, bucketStyle[Math.min(i, bucketStyle.length - 1)],
          'bybit &le; ' + fmtCompactUsd(Number(k)));
      });
      // Session-anchor honesty label (§4 CvdStore doc: CVD has no natural zero).
      legend.insertAdjacentHTML('beforeend',
        '<span class="cvd-anchor">anchored at page open — slope/divergence only, level is meaningless</span>');
      cvdEl.appendChild(legend);
    }

    /** EXACT sum of k session-anchored cumsum step series on the UNION of
     *  their sample times. A session-anchored CVD is a right-continuous STEP
     *  function, so at any union timestamp each venue contributes its last
     *  cumsum (0 before its first sample — its true anchored value) and the
     *  sum is plain arithmetic, never interpolation — no value is fabricated
     *  (§0.7). Output is plot-decimated to ≤20k points by keeping every Nth
     *  (resolution only; every KEPT point is still an exact sum). */
    function mergeStepSum(list) {
      const k = list.length;
      const idx = new Array(k).fill(0);
      const cur = new Array(k).fill(0);
      let t = [], v = [];
      for (;;) {
        let next = Infinity;
        for (let i = 0; i < k; i++) {
          if (idx[i] < list[i].t.length && list[i].t[idx[i]] < next) next = list[i].t[idx[i]];
        }
        if (next === Infinity) break;
        let s = 0;
        for (let i = 0; i < k; i++) {
          while (idx[i] < list[i].t.length && list[i].t[idx[i]] <= next) {
            cur[i] = list[i].overall[idx[i]];
            idx[i]++;
          }
          s += cur[i];
        }
        t.push(next); v.push(s);
      }
      const MAX = 20000;   // matches CvdStore's own plot cap
      if (t.length > MAX) {
        const stride = Math.ceil(t.length / MAX);
        const dt = [], dv = [];
        for (let i = 0; i < t.length; i += stride) { dt.push(t[i]); dv.push(v[i]); }
        // Always keep the newest point — the live edge must not lag a stride.
        if (dt[dt.length - 1] !== t[t.length - 1]) { dt.push(t[t.length - 1]); dv.push(v[v.length - 1]); }
        t = dt; v = dv;
      }
      return { t, v };
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

    /** cvd = { exs: {ex → CvdStore.series() ({t, overall, byBucket})} } — a
     *  map of PER-EXCHANGE stores (§4b), each independently session-anchored.
     *  Bucket lines read the BYBIT store only (buckets stay single-venue, same
     *  §0.7 reasoning as the footprint). */
    function renderCvd(cvd, now) {
      if (!cvdChart || !cvd || !cvd.exs) return;
      if (now - lastCvdAt < CVD_MIN_MS) return;   // setData is O(points) — throttle beyond dirty flags
      lastCvdAt = now;
      const present = [];
      for (const ex of cvdExs) {
        const s = cvd.exs[ex];
        if (!s) continue;
        cvdSeries['ex:' + ex].setData(toLcSeries(s.t, s.overall));
        present.push(s);
      }
      if (present.length) {
        const m = mergeStepSum(present);
        cvdSeries.sum.setData(toLcSeries(m.t, m.v));
      }
      const by = cvd.exs.bybit;
      if (by && by.byBucket) {
        for (const k of cvdBuckets) {
          const key = 'bucket:' + k;
          if (cvdSeries[key] && by.byBucket[k]) cvdSeries[key].setData(toLcSeries(by.t, by.byBucket[k]));
        }
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

      // Fixed CENTER PRICE GUTTER (visual-defect fix): price labels used to
      // draw at the bars' inner edge — i.e. ON TOP of the stacked segments —
      // and the qty label shared the same anchor, so price+qty+bars collided
      // into mush. Bars now START at the gutter edges and grow outward, so
      // the gutter is bar-free by construction: bid prices live in its left
      // half, ask prices in its right half, and nothing ever paints under
      // them. Rows still pair by RANK (see view header note).
      const centerX = w / 2, PRICE_GUT = 104;   // 52px/side fits '$xxx,xxx' at 9px mono
      const bidEdge = centerX - PRICE_GUT / 2;  // bid bars grow LEFT from here
      const askEdge = centerX + PRICE_GUT / 2;  // ask bars grow RIGHT from here
      const halfW = bidEdge;                    // usable bar+curve span per side
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

      // Gutter edge hairlines (replace the old single center divider): they
      // frame the price lane so it reads as chrome, not data.
      ctx.strokeStyle = p.border;
      ctx.beginPath(); ctx.moveTo(bidEdge + 0.5, 0); ctx.lineTo(bidEdge + 0.5, h); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(askEdge - 0.5, 0); ctx.lineTo(askEdge - 0.5, h); ctx.stroke();

      // Collision skip for price labels: 9px mono needs ~12px of row to stay
      // apart, so label every row only when rowH ≥ 12, else every OTHER row
      // (visual-defect fix — overlapping price text is worse than fewer labels).
      const priceStep = rowH >= 12 ? 1 : 2;

      const drawSide = (rows, isBid) => {
        const sideCol = isBid ? p.up : p.down;
        const cumPts = [];
        let cum = 0;
        for (let i = 0; i < rows.length; i++) {
          const r = rows[i];
          const y = i * rowH;
          // Stacked segments from the gutter edge outward, per exchange
          // (sorted for stable stacking order frame-to-frame).
          let off = 0;
          const exKeys = Object.keys(r.byEx).sort();
          for (const ex of exKeys) {
            const seg = r.byEx[ex] * barScale;
            ctx.fillStyle = rgba(exColor(p, ex), 0.8);
            if (isBid) ctx.fillRect(bidEdge - off - seg, y + 1.5, seg, rowH - 3);
            else ctx.fillRect(askEdge + off, y + 1.5, seg, rowH - 3);
            off += seg;
          }
          cum += r.total;
          cumPts.push([isBid ? bidEdge - cum * cumScale : askEdge + cum * cumScale, y + rowH / 2]);
          // Price in the CENTER GUTTER (side-colored — plus side POSITION as
          // the redundant cue), thinned by priceStep so labels never touch.
          if (rowH >= 11 && i % priceStep === 0) {
            ctx.fillStyle = sideCol; ctx.textAlign = isBid ? 'right' : 'left';
            ctx.fillText(fmtUsd(r.price), isBid ? centerX - 4 : centerX + 4, y + rowH / 2);
          }
          // Qty at the bar END (just past the tip, in empty space) — only
          // when the bar is long enough (≥28px) that the label reads as
          // "this bar's size" instead of piling onto short-bar neighbours.
          if (rowH >= 11 && off >= 28) {
            ctx.fillStyle = p.muted;
            ctx.textAlign = isBid ? 'right' : 'left';
            ctx.fillText(fmtQty(r.total), isBid ? bidEdge - off - 3 : askEdge + off + 3, y + rowH / 2);
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
      // okx joined in O-2 (§4b): agg-book leg + its own labeled CVD line.
      for (const ex of ['bybit', 'binancef', 'coinbase', 'okx']) {
        const chip = document.createElement('span');
        chip.className = 'statchip term-chip';
        chip.innerHTML = '<span class="dot"></span><span class="chip-text">' + ex + ': connecting…</span>';
        chipRow.appendChild(chip);
        chips[ex] = { el: chip, text: chip.querySelector('.chip-text') };
      }
      chipRow.insertAdjacentHTML('beforeend',
        '<span class="chips-note">bybit = primary WS (trades/book/liq/mark/OI) · binancef = depth WS + REST mark/OI · coinbase = spot tape · okx = agg book + CVD leg</span>');
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
        // kind 'open' renders the transport's OWN message, never a hardcoded
        // 'live' (§0 honesty rail): livewire.js says 'live feed connected',
        // the replay driver says 'replay' — hardcoding 'live' here mislabeled
        // fixture replay as a live feed (verify_terminal_browser.py asserts
        // no chip says 'live' in replay). The .live CLASS stays: it styles
        // the healthy/green dot, it is not user-visible text.
        if (s.kind === 'open') { chip.el.classList.add('live'); chip.text.textContent = ex + ': ' + (s.msg || 'live'); }
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

  // ═══ BookHeatmapView — historical resting-depth heatmap (O-2, §4b) ═══
  //
  // X = session time (DepthHistoryStore ring, EVENT timestamps — a linear time
  // axis, so a reconnect gap renders as blank space, never as the last ladder
  // smeared across time it did not actually stand, §0.7). Y = price bucket
  // (the store's tick grid). Cell alpha ∝ resting qty, normalized by the
  // ring-wide p95: normalizing by MAX would let one whale wall (say 400 BTC
  // against a 5–15 BTC level body) compress every ordinary level into
  // near-invisible alpha — p95 keeps the body of the distribution legible and
  // lets true walls simply saturate at full alpha.
  //
  // Default hues: bids --up, asks --down (their position relative to the
  // last-price polyline is the redundant non-color cue; hover names the side
  // in text). Velocity tint toggle (opts.velocityInput): hue switches to the
  // SIGN of the resting-qty change at that bucket vs the previous drawn
  // column — building = --up, draining = --down, flat = --muted; alpha still
  // ∝ qty. In tint mode side is no longer color-coded — the hover readout
  // still reports it.
  //
  // Overlays: last-price polyline (--fg reference trace) + detector event
  // markers — ▽ spoof-pull (--accent), ◈ iceberg-refill (--accent-2), both
  // HEURISTIC flags (§4b; the detection feed panel carries the per-event badge).
  function BookHeatmapView() {
    let root = null, canvas = null, velInput = null, velOn = false;
    let lastSlice = null, mouse = null, drawQueued = false;
    const GUT_AXIS = 64, ROW_TIME = 16;

    function mount(el, opts) {
      root = el;
      const o = opts || {};
      canvas = document.createElement('canvas');
      canvas.className = 'term-canvas';
      root.appendChild(canvas);
      // Crosshair/hover: cached-slice redraws only (FootprintView pattern —
      // the mouse never fabricates a new data frame).
      canvas.addEventListener('mousemove', (e) => {
        const r = canvas.getBoundingClientRect();
        mouse = { x: e.clientX - r.left, y: e.clientY - r.top };
        scheduleDraw();
      });
      canvas.addEventListener('mouseleave', () => { mouse = null; scheduleDraw(); });
      // Velocity toggle lives in the panel chrome (terminal.html) — the view
      // owns its behavior, same split as TapeView's min-notional input.
      velInput = o.velocityInput || null;
      if (velInput) {
        velOn = !!velInput.checked;
        velInput.addEventListener('change', () => { velOn = !!velInput.checked; scheduleDraw(); });
      }
    }

    function scheduleDraw() {
      if (drawQueued || !lastSlice) return;
      drawQueued = true;
      requestAnimationFrame(() => { drawQueued = false; if (lastSlice) draw(lastSlice); });
    }

    function draw(slice) {
      const { ctx, w, h } = fitCanvas(canvas);
      const p = pal();
      ctx.clearRect(0, 0, w, h);
      ctx.textBaseline = 'middle';
      const font = (px, bold) => { ctx.font = (bold ? '600 ' : '') + px + 'px ' + cssVar('--mono', 'monospace'); };

      const samples = slice.samples || [];
      const tick = slice.tickSize || 1;
      const range = slice.range || { min: NaN, max: NaN };
      if (!samples.length || !Number.isFinite(range.min) || !Number.isFinite(range.max)) {
        font(11); ctx.fillStyle = p.muted; ctx.textAlign = 'left';
        ctx.fillText('waiting for book history — one real ladder per second, event-time gated; nothing is backfilled (§0.7)', 10, 18);
        return;
      }

      const plotW = w - GUT_AXIS, plotH = h - ROW_TIME;
      const nRows = Math.round((range.max - range.min) / tick) + 1;
      if (nRows > 2000) {
        // Same guard as the footprint: sub-pixel mush helps nobody — say so.
        font(11); ctx.fillStyle = p.muted; ctx.textAlign = 'left';
        ctx.fillText('history spans ' + nRows + ' rows at $' + tick + ' grouping — pick a coarser tick group', 10, 18);
        return;
      }
      const rowH = plotH / nRows;
      const cellH = Math.max(rowH, 1);
      const yOf = (price) => ((range.max - price) / tick) * rowH;   // row TOP edge

      const t0 = samples[0].ts, tN = samples[samples.length - 1].ts;
      const span = Math.max(tN - t0, 1000);
      const X = (ts) => plotW * (ts - t0) / span;

      // Column decimation: keep every stride-th sample so columns stay ≥ ~1.5
      // CSS px. Plot RESOLUTION only — every drawn column is a real ladder
      // that stood on the wire; we drop columns, we never average two ladders
      // into a fictitious one (§0.7).
      const stride = Math.max(1, Math.ceil(samples.length / Math.max(1, Math.floor(plotW / 1.5))));
      const kept = [];
      for (let i = 0; i < samples.length; i += stride) kept.push(samples[i]);
      if (kept[kept.length - 1] !== samples[samples.length - 1]) kept.push(samples[samples.length - 1]);

      // Median inter-column dt caps column width: past ~1.5× the median the
      // book state is UNKNOWN (reconnect gap) and stays blank — gaps are gaps.
      const dts = [];
      for (let i = 1; i < kept.length; i++) dts.push(kept[i].ts - kept[i - 1].ts);
      dts.sort((a, b) => a - b);
      const dtMed = dts.length ? Math.max(dts[Math.floor(dts.length / 2)], 1) : 1000;

      // Ring-wide p95 of resting qty (see header note on why not max).
      const qs = [];
      for (const s of kept) {
        for (const m of [s.bids, s.asks]) for (const q of m.values()) qs.push(q);
      }
      if (!qs.length) return;
      qs.sort((a, b) => a - b);
      const p95 = qs[Math.floor(0.95 * (qs.length - 1))];
      if (!(p95 > 0)) return;
      const alphaOf = (q) => 0.05 + 0.72 * Math.min(1, q / p95);

      let prevComb = null;   // velocity-tint diff base (previous DRAWN column)
      for (let i = 0; i < kept.length; i++) {
        const s = kept[i];
        const x0 = X(s.ts);
        const nextTs = i + 1 < kept.length ? kept[i + 1].ts : s.ts + dtMed;
        const wCol = Math.max(1, X(Math.min(nextTs, s.ts + 1.5 * dtMed)) - x0);
        if (velOn) {
          // Combined bucket qty (bids+asks — same semantics as the store's
          // velocity()); hue = sign of the change vs the previous column.
          const comb = new Map();
          for (const m of [s.bids, s.asks]) for (const [b, q] of m) comb.set(b, (comb.get(b) || 0) + q);
          for (const [b, q] of comb) {
            const dq = prevComb ? q - (prevComb.get(b) || 0) : 0;
            ctx.fillStyle = rgba(dq > 0 ? p.up : dq < 0 ? p.down : p.muted, alphaOf(q));
            ctx.fillRect(x0, yOf(b), wCol, cellH);
          }
          prevComb = comb;
        } else {
          for (const [b, q] of s.bids) { ctx.fillStyle = rgba(p.up, alphaOf(q)); ctx.fillRect(x0, yOf(b), wCol, cellH); }
          for (const [b, q] of s.asks) { ctx.fillStyle = rgba(p.down, alphaOf(q)); ctx.fillRect(x0, yOf(b), wCol, cellH); }
        }
      }

      // Last-price polyline overlay (--fg — a reference trace, not P&L).
      const trail = slice.trail || [];
      if (trail.length > 1) {
        ctx.strokeStyle = rgba(p.fg, 0.85); ctx.lineWidth = 1.25;
        ctx.beginPath();
        let started = false;
        for (const pt of trail) {
          if (!Number.isFinite(pt.ts) || !Number.isFinite(pt.price) || pt.ts < t0) continue;
          const x = X(pt.ts);
          const y = Math.min(plotH, Math.max(0, yOf(pt.price) + rowH / 2));
          if (!started) { ctx.moveTo(x, y); started = true; } else ctx.lineTo(x, y);
        }
        ctx.stroke();
      }

      // Detector markers (§4b): drawn ONLY inside the visible window — an
      // off-screen marker would imply knowledge the eye can't verify.
      for (const ev of slice.events || []) {
        if (!ev || ev.ts < t0 || ev.ts > tN) continue;
        if (!(ev.price >= range.min && ev.price <= range.max)) continue;
        font(11, true); ctx.textAlign = 'center';
        ctx.fillStyle = ev.kind === 'spoof-pull' ? p.accent : p.accent2;
        ctx.fillText(ev.kind === 'spoof-pull' ? '▽' : '◈', X(ev.ts), yOf(ev.price) + rowH / 2);
      }

      // Price axis (right), thinned to ~14px spacing.
      const labStep = Math.max(1, Math.ceil(14 / rowH));
      font(9); ctx.fillStyle = p.muted; ctx.textAlign = 'right';
      for (let r = 0; r < nRows; r += labStep) {
        const price = range.max - r * tick;
        ctx.fillText(fmtUsd(price), w - 2, yOf(price) + rowH / 2);
      }
      // Time axis (bottom): labels at fixed fractions of the linear time span.
      font(9); ctx.textAlign = 'center';
      for (const f of [0.02, 0.25, 0.5, 0.75, 0.98]) {
        ctx.fillText(hms(t0 + f * span), plotW * f, plotH + ROW_TIME / 2);
      }
      // Corner tag: venue + scale statement (per-source label, §0.7).
      font(9, true); ctx.textAlign = 'left'; ctx.fillStyle = p.muted;
      ctx.fillText((slice.ex || '') + ' · α ∝ resting qty (p95-scaled)' + (velOn ? ' · velocity tint ON' : ''), 6, 8);

      // Hover readout: ts · price · resting qty (+ side) at the cell.
      if (mouse && mouse.x >= 0 && mouse.x < plotW && mouse.y >= 0 && mouse.y < plotH) {
        const tsAt = t0 + span * (mouse.x / plotW);
        // Last kept sample at/before the cursor time (linear scan is fine at
        // ≤ plotW/1.5 columns, and only on mouse-move redraws).
        let s = null;
        for (const k of kept) { if (k.ts <= tsAt) s = k; else break; }
        const ri = Math.min(nRows - 1, Math.floor(mouse.y / rowH));
        const bucket = Math.round((range.max - ri * tick) * 1e8) / 1e8;   // roundPx-canonical (store grid)
        ctx.save();
        ctx.setLineDash([3, 3]);
        ctx.strokeStyle = rgba(p.fg, 0.4); ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(0, mouse.y + 0.5); ctx.lineTo(plotW, mouse.y + 0.5); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(mouse.x + 0.5, 0); ctx.lineTo(mouse.x + 0.5, plotH); ctx.stroke();
        ctx.restore();
        let txt;
        if (!s || tsAt - s.ts > 1.5 * dtMed) {
          txt = hms(tsAt) + ' · ' + fmtUsd(bucket) + ' · no sample (gap — gaps stay gaps)';
        } else {
          const bq = s.bids.get(bucket) || 0, aq = s.asks.get(bucket) || 0;
          const side = bq && aq ? 'bid+ask' : bq ? 'bid' : aq ? 'ask' : '—';
          txt = hms(s.ts) + ' · ' + fmtUsd(bucket) + ' · resting ' + fmtQty(bq + aq) + ' BTC (' + side + ')';
        }
        font(10);
        const tw = ctx.measureText(txt).width;
        ctx.fillStyle = rgba(p.panel2, 0.92);
        ctx.fillRect(6, 14, tw + 14, 18);
        ctx.strokeStyle = p.border; ctx.strokeRect(6.5, 14.5, tw + 13, 17);
        ctx.fillStyle = p.fg; ctx.textAlign = 'left';
        ctx.fillText(txt, 13, 23.5);
      }
    }

    /** slice = { samples, range, tickSize, trail, events, ex } — samples/range
     *  straight from DepthHistoryStore (LIVE refs, read-only), trail from the
     *  bootstrap's per-venue price ring, events from the detector. */
    function render(slice) {
      lastSlice = slice;
      draw(slice);
    }

    return { mount, render };
  }

  // ═══ LiqHeatmapView — ESTIMATED liquidation bands + observed prints (O-2, §4b) ═══
  //
  // ⚠ MODEL ESTIMATE (§0.4): bands come from LiqHeatmapModel — a volume-at-
  // price entry proxy × equal-weighted leverage tiers, NOT observed positions.
  // The 'ESTIMATED (model)' badge is drawn INTO the canvas every frame (it
  // cannot scroll away or be covered) and the panel header hint repeats it.
  // Observed liquidation prints render as discrete DOTS in their own left
  // lane — visually and spatially separate from the bands, never blended
  // (§4b: estimates and observations must not be confusable).
  //
  // Colors: side 'long' (est. long-liq below mark → forced SELLS) = --down;
  // side 'short' (above mark → forced BUY-backs) = --up — the same semantic
  // pair the liquidation feed badges use. Position (below/above the mark
  // line) is the redundant non-color cue.
  //
  // VIEW WINDOW (visual-defect fix): the y-axis used to span the FULL band +
  // observed-print extent — the 5× tier alone puts bands ~±20% from every
  // entry bucket, and a single low-price observed print dragged the axis to
  // (and past) zero, compressing everything readable into a sliver. Default
  // window is now mark ± 6% (covers the 25×/50×/100× bands and part of the
  // 10× spread); an in-panel toggle (opts.rangeInput, persisted by the
  // bootstrap) switches to 'all tiers' which derives the range from every
  // band/print again. In BOTH modes the axis is clamped strictly > 0 — a
  // negative BTC price axis asserts a price that cannot exist. Anything the
  // window excludes is COUNTED into compact '+n bands above/below' overflow
  // markers at the plot edges — nothing is silently hidden (§0 honesty rails).
  function LiqHeatmapView() {
    let root = null, canvas = null;
    let rangeMode = 'pct6';   // 'pct6' (mark ± 6%, default) | 'all' (full tier extent)
    let lastSlice = null, drawQueued = false;
    const OBS_LANE = 56, GUT_AXIS = 64;
    const WINDOW_PCT = 0.06;  // ± fraction of mark for the default window

    function mount(el, opts) {
      root = el;
      const o = opts || {};
      canvas = document.createElement('canvas');
      canvas.className = 'term-canvas';
      root.appendChild(canvas);
      // Range toggle lives in the panel chrome (terminal.html); the view owns
      // its behavior, persistence stays in terminal.js — the same ownership
      // split as TapeView's min-notional input and BookHeatmapView's velocity
      // toggle. Redraws come from the CACHED slice (a toggle flip never
      // fabricates a new data frame).
      if (o.rangeInput) {
        rangeMode = o.rangeInput.value === 'all' ? 'all' : 'pct6';
        o.rangeInput.addEventListener('change', () => {
          rangeMode = o.rangeInput.value === 'all' ? 'all' : 'pct6';
          if (typeof o.onRange === 'function') o.onRange(rangeMode);
          scheduleDraw();
        });
      }
    }

    function scheduleDraw() {
      if (drawQueued || !lastSlice) return;
      drawQueued = true;
      requestAnimationFrame(() => { drawQueued = false; if (lastSlice) draw(lastSlice); });
    }

    /** Permanent in-canvas badge — top-right, warn palette (§0.4 label rail). */
    function drawBadge(ctx, w, p) {
      const txt = 'ESTIMATED (model)';
      ctx.font = '600 9px ' + cssVar('--mono', 'monospace');
      const tw = ctx.measureText(txt).width;
      const x = w - tw - 14, y = 4;
      ctx.fillStyle = cssVar('--warn-bg', '#3a2a12');
      ctx.fillRect(x, y, tw + 10, 15);
      ctx.strokeStyle = cssVar('--warn-fg', '#ffd591');
      ctx.strokeRect(x + 0.5, y + 0.5, tw + 9, 14);
      ctx.fillStyle = cssVar('--warn-fg', '#ffd591');
      ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
      ctx.fillText(txt, x + 5, y + 8);
    }

    /** Compact overflow marker (visual-defect fix): says how many estimated
     *  bands (and observed prints) sit beyond the current window edge, so the
     *  windowed view hides nothing silently (§0 honesty rails). */
    function drawOverflow(ctx, p, x, y, w, arrow, nBands, nObs, where) {
      const parts = [];
      if (nBands) parts.push('+' + nBands + ' band' + (nBands === 1 ? '' : 's'));
      if (nObs) parts.push('+' + nObs + ' print' + (nObs === 1 ? '' : 's'));
      const txt = arrow + ' ' + parts.join(' · ') + ' ' + where;
      ctx.font = '600 9px ' + cssVar('--mono', 'monospace');
      const tw = ctx.measureText(txt).width;
      const bx = x + (w - tw) / 2 - 5;
      ctx.fillStyle = rgba(p.panel2, 0.92);
      ctx.fillRect(bx, y - 7, tw + 10, 14);
      ctx.strokeStyle = p.border; ctx.strokeRect(bx + 0.5, y - 6.5, tw + 9, 13);
      ctx.fillStyle = p.muted; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillText(txt, x + w / 2, y);
    }

    function draw(slice) {
      if (!canvas) return;
      const { ctx, w, h } = fitCanvas(canvas);
      const p = pal();
      ctx.clearRect(0, 0, w, h);
      ctx.textBaseline = 'middle';
      const font = (px, bold) => { ctx.font = (bold ? '600 ' : '') + px + 'px ' + cssVar('--mono', 'monospace'); };

      const est = slice.est;
      const mark = slice.mark;
      const tick = slice.tickSize || 1;
      if (!est || !Number.isFinite(mark)) {
        font(11); ctx.fillStyle = p.muted; ctx.textAlign = 'left';
        ctx.fillText('waiting for model inputs (bybit mark + session volume profile)…', 10, 30);
        drawBadge(ctx, w, p);
        return;
      }

      // Y window (see view header note). Default 'pct6' = mark ± 6% — a FIXED
      // window around the one real anchor (mark), NEVER derived from the band
      // extent, so one far-out 5×-tier band or a bad-tick observed print
      // can't flatten the readable range. 'all' derives from every band +
      // print + mark (the old behavior) with a small pad. Both modes clamp
      // the floor strictly above 0: a $-negative axis asserts prices that
      // cannot exist (§0 honesty — the axis never extends past reality).
      let pmin, pmax;
      if (rangeMode === 'all') {
        pmin = mark; pmax = mark;
        for (const b of est.bands) { if (b.price < pmin) pmin = b.price; if (b.price > pmax) pmax = b.price; }
        for (const o of est.observed) { if (o.price < pmin) pmin = o.price; if (o.price > pmax) pmax = o.price; }
        const pad = Math.max((pmax - pmin) * 0.04, tick * 2);
        pmin -= pad; pmax += pad;
      } else {
        pmin = mark * (1 - WINDOW_PCT);
        pmax = mark * (1 + WINDOW_PCT);
      }
      pmin = Math.max(pmin, tick);   // strictly > 0 (tick = smallest drawable bucket)

      const plotH = h - 4;
      const yOf = (price) => plotH * (pmax - price) / (pmax - pmin);
      const bandX = OBS_LANE + 4, bandW = w - GUT_AXIS - bandX;
      const bandH = Math.max(2, plotH * tick / (pmax - pmin));

      // Estimated bands: alpha ∝ normalized weight (max = 1 by construction).
      // Off-window bands are COUNTED (drawn as edge markers below), never
      // silently dropped.
      let bandsAbove = 0, bandsBelow = 0, obsAbove = 0, obsBelow = 0;
      for (const b of est.bands) {
        if (b.price > pmax) { bandsAbove++; continue; }
        if (b.price < pmin) { bandsBelow++; continue; }
        ctx.fillStyle = rgba(b.side === 'long' ? p.down : p.up, 0.07 + 0.6 * b.weight);
        ctx.fillRect(bandX, yOf(b.price) - bandH / 2, bandW, bandH);
      }

      // Mark line (--accent, dashed). Label split so nothing clips: the PRICE
      // sits right-aligned in the axis gutter like every other axis label
      // (the old 'mark $…' string was wider than the 64px gutter and clipped
      // mid-digit), the word 'mark' rides the line inside the plot.
      const my = yOf(mark);
      ctx.save();
      ctx.setLineDash([5, 4]);
      ctx.strokeStyle = p.accent; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(0, my); ctx.lineTo(w - GUT_AXIS, my); ctx.stroke();
      ctx.restore();
      font(9, true); ctx.fillStyle = p.accent;
      ctx.textAlign = 'right'; ctx.fillText(fmtUsd(mark), w - 2, my);
      ctx.textAlign = 'left'; ctx.fillText('mark', bandX + 2, my - 6);

      // Side captions — the redundant text cue for what each half means.
      font(9, true); ctx.textAlign = 'left';
      ctx.fillStyle = p.up; ctx.fillText('est. SHORT-liq bands (above mark)', bandX + 2, 26);
      ctx.fillStyle = p.down; ctx.fillText('est. LONG-liq bands (below mark)', bandX + 2, plotH - 8);

      // Observed lane (left): real liquidation PRINTS as dots — separate lane,
      // never blended into band alpha (§4b). X inside the lane = print time
      // across the session's observed span (single print → centered).
      ctx.strokeStyle = p.border;
      ctx.beginPath(); ctx.moveTo(OBS_LANE + 0.5, 0); ctx.lineTo(OBS_LANE + 0.5, plotH); ctx.stroke();
      font(8); ctx.fillStyle = p.muted; ctx.textAlign = 'left';
      ctx.fillText('observed', 4, 8);
      const obs = est.observed;
      if (obs.length) {
        let tsMin = Infinity, tsMax = -Infinity;
        for (const o of obs) { if (o.ts < tsMin) tsMin = o.ts; if (o.ts > tsMax) tsMax = o.ts; }
        const tspan = tsMax - tsMin;
        for (const o of obs) {
          // Off-window prints are counted into the edge overflow markers —
          // same no-silent-hiding rule as the bands.
          if (o.price > pmax) { obsAbove++; continue; }
          if (o.price < pmin) { obsBelow++; continue; }
          const x = tspan > 0 ? 8 + (OBS_LANE - 16) * (o.ts - tsMin) / tspan : OBS_LANE / 2;
          const y = yOf(o.price);
          if (y < 0 || y > plotH) continue;
          ctx.fillStyle = o.side === 'long' ? p.down : p.up;
          ctx.beginPath(); ctx.arc(x, y, 2.5, 0, Math.PI * 2); ctx.fill();
          ctx.strokeStyle = rgba(p.fg, 0.6); ctx.lineWidth = 0.75; ctx.stroke();
        }
      } else {
        font(8); ctx.fillStyle = p.muted;
        ctx.save();
        ctx.translate(10, plotH / 2); ctx.rotate(-Math.PI / 2); ctx.textAlign = 'center';
        ctx.fillText('no prints yet', 0, 0);
        ctx.restore();
      }

      // Price axis (right), ~40px spacing.
      const nLab = Math.max(2, Math.floor(plotH / 40));
      font(9); ctx.fillStyle = p.muted; ctx.textAlign = 'right';
      for (let i = 0; i <= nLab; i++) {
        const price = pmax - (i / nLab) * (pmax - pmin);
        ctx.fillText(fmtUsd(price), w - 2, yOf(price));
      }

      // Overflow markers at the window edges — the honest accounting for
      // everything the window excludes (see view header note). Drawn last so
      // no band/dot can paint over them.
      if (bandsAbove + obsAbove > 0) drawOverflow(ctx, p, bandX, 10, bandW, '▲', bandsAbove, obsAbove, 'above');
      if (bandsBelow + obsBelow > 0) drawOverflow(ctx, p, bandX, plotH - 20, bandW, '▼', bandsBelow, obsBelow, 'below');

      drawBadge(ctx, w, p);
    }

    /** slice = { est: LiqHeatmapModel.estimate() result ({bands, observed,
     *  label}) or null before first estimate, mark, tickSize } — cached so
     *  the range toggle can redraw without waiting for new data. */
    function render(slice) {
      lastSlice = slice;
      draw(slice);
    }

    return { mount, render };
  }

  // ═══ DetectionFeedView — spoof/iceberg heuristic event list (O-2, §4b) ═══
  //
  // Newest-first list of SpoofIcebergDetector events. EVERY row carries the
  // 'heuristic' badge (§4b label rail — the store stamps the label on the
  // event; this view refuses to render a row without showing it). Kind glyphs
  // match the heatmap overlay markers: ▽ spoof-pull, ◈ iceberg-refill —
  // glyph + kind text are the non-color cues.
  function DetectionFeedView() {
    let root = null, list = null;
    const MAX_ROWS = 50;

    function mount(el) {
      root = el;
      root.insertAdjacentHTML('beforeend',
        '<div class="det-row det-head"><span>UTC</span><span>kind</span><span>price</span><span>size</span><span>life</span><span></span></div>');
      list = document.createElement('div');
      list.className = 'det-list';
      root.appendChild(list);
    }

    /** slice = { events } — detector.events(), oldest→newest (reversed here). */
    function render(slice) {
      if (!list) return;
      const evs = (slice.events || []).slice().reverse().slice(0, MAX_ROWS);
      if (!evs.length) {
        list.innerHTML = '<div class="chart-na">no heuristic flags this session — flags mark book patterns '
          + '<i>consistent with</i> spoofing/icebergs, never proof (intent is unobservable from public L2).</div>';
        return;
      }
      let html = '';
      for (const ev of evs) {
        const spoof = ev.kind === 'spoof-pull';
        const size = spoof
          ? fmtQty(ev.size)
          : fmtQty(ev.tradedQty) + '/' + fmtQty(ev.maxDisplayed);   // traded / max displayed
        const life = spoof && Number.isFinite(ev.lifetimeMs) ? (ev.lifetimeMs / 1000).toFixed(1) + 's' : '—';
        html += '<div class="det-row">'
          + '<span class="ts">' + hms(ev.ts) + '</span>'
          + '<span class="kind ' + (spoof ? 'spoof' : 'iceberg') + '">' + (spoof ? '▽ spoof-pull' : '◈ iceberg') + '</span>'
          + '<span class="px">' + fmtUsd(ev.price) + '</span>'
          + '<span class="qty" title="' + (spoof ? 'max displayed wall size (BTC)' : 'traded / max displayed (BTC)') + '">' + size + '</span>'
          + '<span class="life">' + life + '</span>'
          + '<span class="det-badge">' + esc(ev.label || 'heuristic') + '</span>'
          + '</div>';
      }
      list.innerHTML = html;
    }

    return { mount, render };
  }

  // ─── O-3 shared chrome (§4c): permanent in-canvas honesty badge ──────────
  //
  // Same warn-palette badge as LiqHeatmapView's 'ESTIMATED (model)' (§0.4
  // label rail), parameterized so KlineVpView can wear its mandatory
  // 'bar-range approximation' text: drawn INTO the canvas every frame — it
  // can never scroll away or be covered by data.
  function drawWarnBadge(ctx, w, text) {
    ctx.font = '600 9px ' + cssVar('--mono', 'monospace');
    const tw = ctx.measureText(text).width;
    const x = w - tw - 14, y = 4;
    ctx.fillStyle = cssVar('--warn-bg', '#3a2a12');
    ctx.fillRect(x, y, tw + 10, 15);
    ctx.strokeStyle = cssVar('--warn-fg', '#ffd591');
    ctx.strokeRect(x + 0.5, y + 0.5, tw + 9, 14);
    ctx.fillStyle = cssVar('--warn-fg', '#ffd591');
    ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
    ctx.fillText(text, x + 5, y + 8);
  }

  /** Signed percent with '—' for non-finite (never a fabricated 0 — §0.7). */
  function fmtPct(x, dp) {
    if (!Number.isFinite(x)) return '—';
    const d = dp == null ? 2 : dp;
    return (x > 0 ? '+' : '') + x.toFixed(d) + '%';
  }

  // ═══ HistChartView — Bybit kline candlesticks + volume + bar replay (O-3, §4c) ═══
  //
  // HISTORICAL PANEL, REST-fed, per-source labeled 'bybit linear klines ·
  // REST' in the header. Deliberately NO live-WS merging (§0.7 honesty rail):
  // appending live trades onto REST klines would splice two transports into
  // one unlabeled series — this panel stays a pure REST snapshot, refreshed
  // only on interval change, while the live panels above carry the session.
  //
  // BAR REPLAY (CryExc feature 13): a cursor reveals bars strictly in order —
  // chart.setData only ever receives bars[0..cursor], so NOTHING right of the
  // cursor exists in the chart (no peeking; scrubbing back cannot leak the
  // future via autoscale or crosshair). While cursor < last bar the panel
  // header shows a 'REPLAY (historical bars)' flag (§0 — a rewound chart must
  // never read as the live edge); 'live edge' restores the full data set.
  //
  // Indicators: SMA 20/50/200 from window.Quant.sma (quant.js — house rule:
  // never reimplement math that exists there). An SMA is a trailing window,
  // so precomputing it over the FULL series and slicing to the cursor is
  // bit-identical to computing on the revealed slice — no lookahead leaks.
  // Heikin-Ashi is implemented LOCALLY below: it is a presentation transform
  // (candle re-drawing convention), not portfolio math — quant.js is the home
  // of testable math, not display recodings. SMAs stay computed on RAW closes
  // even in HA mode (HA closes are synthetic; averaging them would present a
  // made-up series as the market's moving average).
  function HistChartView() {
    let root = null, chart = null, candle = null, volume = null, legend = null, note = null;
    let smaSeries = {};             // period → line series
    let smaFull = {};               // period → full-length SMA array (raw closes)
    const SMA_PERIODS = [20, 50, 200];
    const SMA_TOKEN = { 20: 'c1', 50: 'c2', 200: 'c6' };   // categorical tokens — indicators aren't P&L
    let bars = null;                // chronological bars (LIVE ref from bootstrap — read-only)
    let cursor = -1;                // index of the last VISIBLE bar
    let playing = false, timer = null, speed = 1;
    let ha = false;
    const smaOn = { 20: false, 50: false, 200: false };
    let ctl = {};                   // control elements (from terminal.html chrome)

    /** Heikin-Ashi transform — PRESENTATION ONLY (see view header): standard
     *  recursion haC=(o+h+l+c)/4, haO=(prevHaO+prevHaC)/2 seeded at (o+c)/2.
     *  Pure function of the input slice; input bars are never mutated. */
    function heikinAshi(src) {
      const out = new Array(src.length);
      let prevO = NaN, prevC = NaN;
      for (let i = 0; i < src.length; i++) {
        const b = src[i];
        const hc = (b.o + b.h + b.l + b.c) / 4;
        const ho = i === 0 ? (b.o + b.c) / 2 : (prevO + prevC) / 2;
        out[i] = { ts: b.ts, o: ho, h: Math.max(b.h, ho, hc), l: Math.min(b.l, ho, hc), c: hc, v: b.v };
        prevO = ho; prevC = hc;
      }
      return out;
    }

    function stopPlay() {
      playing = false;
      if (timer) { clearInterval(timer); timer = null; }
      if (ctl.playBtn) ctl.playBtn.textContent = '▶ play';
    }

    function startPlay() {
      if (!bars || bars.length < 2) return;
      // At the live edge there is nothing left to reveal — wrap to bar 0 so
      // 'play' always means "watch the history unfold from the start".
      if (cursor >= bars.length - 1) cursor = 0;
      playing = true;
      if (ctl.playBtn) ctl.playBtn.textContent = '⏸ pause';
      if (timer) clearInterval(timer);
      timer = setInterval(() => {
        if (!bars || cursor >= bars.length - 1) { stopPlay(); return; }
        cursor++;
        setView();
      }, Math.max(50, Math.round(1000 / speed)));
    }

    /** Push bars[0..cursor] into the chart. THE no-peek boundary: this is the
     *  only place series data is set, and it never reads past the cursor. */
    function setView() {
      if (!chart || !bars || !bars.length) return;
      const n = bars.length;
      cursor = Math.max(0, Math.min(cursor, n - 1));
      const p = pal();
      const vis = bars.slice(0, cursor + 1);
      const disp = ha ? heikinAshi(vis) : vis;
      candle.setData(disp.map((b) => ({ time: b.ts / 1000, open: b.o, high: b.h, low: b.l, close: b.c })));
      // Volume histogram always uses RAW bar volume + raw up/down coloring —
      // HA recolors candles, not what actually traded.
      volume.setData(vis.map((b) => ({ time: b.ts / 1000, value: b.v, color: rgba(b.c >= b.o ? p.up : p.down, 0.45) })));
      for (const per of SMA_PERIODS) {
        const s = smaSeries[per];
        if (!s) continue;
        if (smaOn[per] && smaFull[per]) {
          const pts = [];
          const arr = smaFull[per];
          for (let i = 0; i <= cursor; i++) {
            if (Number.isFinite(arr[i])) pts.push({ time: bars[i].ts / 1000, value: arr[i] });
          }
          s.setData(pts);
          s.applyOptions({ visible: true });
        } else {
          s.applyOptions({ visible: false });
        }
      }
      const replaying = cursor < n - 1;
      if (ctl.flagEl) ctl.flagEl.hidden = !replaying;   // 'REPLAY (historical bars)' — §0 flag
      if (ctl.scrub) { ctl.scrub.max = String(n - 1); ctl.scrub.value = String(cursor); }
      renderLegend();
    }

    function renderLegend() {
      if (!legend) return;
      const p = pal();
      let html = '';
      for (const per of SMA_PERIODS) {
        if (smaOn[per]) html += '<span><i class="sw" style="background:' + p[SMA_TOKEN[per]] + '"></i>SMA ' + per + '</span>';
      }
      if (ha) html += '<span>Heikin-Ashi (display transform — SMAs stay on raw closes)</span>';
      html += '<span class="cvd-anchor">bybit linear klines · REST · no live merge (§0.7)</span>';
      legend.innerHTML = html;
    }

    function mount(el, opts) {
      root = el;
      ctl = opts || {};
      const LC = global.LightweightCharts;
      if (!LC || !LC.createChart) {
        // Honest degrade (index.html vendoring rule): say why, fabricate nothing.
        root.innerHTML = '<div class="chart-na">vendored lightweight-charts unavailable — historical chart disabled.</div>';
        return;
      }
      const p = pal();
      chart = LC.createChart(root, {
        height: root.clientHeight || 380,
        layout: { background: { color: p.bg }, textColor: p.fg, fontFamily: cssVar('--mono', 'monospace') },
        grid: { vertLines: { color: p.grid }, horzLines: { color: p.grid } },
        timeScale: { timeVisible: true, secondsVisible: false, borderColor: p.border },
        rightPriceScale: { borderColor: p.border, scaleMargins: { top: 0.06, bottom: 0.22 } },
        crosshair: { mode: 0 },
      });
      candle = chart.addCandlestickSeries({
        upColor: p.up, downColor: p.down, wickUpColor: p.up, wickDownColor: p.down, borderVisible: false,
      });
      // Volume rides its own overlay scale pinned to the bottom ~16% of the pane.
      volume = chart.addHistogramSeries({ priceScaleId: 'vol', priceFormat: { type: 'volume' }, lastValueVisible: false, priceLineVisible: false });
      chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.84, bottom: 0 } });
      for (const per of SMA_PERIODS) {
        smaSeries[per] = chart.addLineSeries({
          color: p[SMA_TOKEN[per]], lineWidth: 1, visible: false,
          priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
        });
      }
      legend = document.createElement('div');
      legend.className = 'term-cvd-legend';
      root.appendChild(legend);
      note = document.createElement('div');
      note.className = 'chart-na hist-note';
      note.textContent = 'awaiting bybit kline history (REST)…';
      root.appendChild(note);

      // Chart width tracks the container (lightweight-charts sizes once at
      // create; without this a window resize leaves a stale-width canvas).
      window.addEventListener('resize', () => {
        if (chart) chart.applyOptions({ width: root.clientWidth });
      });

      // ── Control wiring — elements live in the panel chrome (terminal.html);
      // the view owns their behavior (TapeView filter-input split). ──
      if (ctl.intervalSel && typeof ctl.onInterval === 'function') {
        ctl.intervalSel.addEventListener('change', () => ctl.onInterval(ctl.intervalSel.value));
      }
      if (ctl.smaInputs) {
        for (const per of SMA_PERIODS) {
          const inp = ctl.smaInputs[per];
          if (!inp) continue;
          smaOn[per] = !!inp.checked;
          inp.addEventListener('change', () => { smaOn[per] = !!inp.checked; setView(); });
        }
      }
      if (ctl.haInput) {
        ha = !!ctl.haInput.checked;
        ctl.haInput.addEventListener('change', () => { ha = !!ctl.haInput.checked; setView(); });
      }
      if (ctl.playBtn) ctl.playBtn.addEventListener('click', () => { if (playing) stopPlay(); else startPlay(); });
      if (ctl.stepBtn) ctl.stepBtn.addEventListener('click', () => { stopPlay(); cursor++; setView(); });
      if (ctl.speedSel) {
        speed = Number(ctl.speedSel.value) || 1;
        ctl.speedSel.addEventListener('change', () => {
          speed = Number(ctl.speedSel.value) || 1;
          if (playing) startPlay();   // re-arm the interval at the new cadence
        });
      }
      if (ctl.scrub) ctl.scrub.addEventListener('input', () => { stopPlay(); cursor = Number(ctl.scrub.value) || 0; setView(); });
      if (ctl.liveBtn) ctl.liveBtn.addEventListener('click', () => { stopPlay(); cursor = bars ? bars.length - 1 : -1; setView(); });
    }

    /** slice = { bars } — chronological klines from terminal-hist.js (already
     *  reversed from Bybit's NEWEST-FIRST wire order) or null on fetch failure. */
    function render(slice) {
      if (!chart) return;
      const next = slice && slice.bars;
      if (next === bars) return;   // identity check: REST data only changes on (re)fetch
      stopPlay();
      bars = next;
      if (!bars || !bars.length) {
        note.hidden = false;
        note.textContent = 'no kline history — bybit REST fetch failed or returned empty (transient; will retry on interval change)';
        candle.setData([]); volume.setData([]);
        for (const per of SMA_PERIODS) if (smaSeries[per]) smaSeries[per].setData([]);
        return;
      }
      note.hidden = true;
      const closes = bars.map((b) => b.c);
      const Q = global.Quant;
      for (const per of SMA_PERIODS) {
        // quant.js sma — house rule: indicator math is never reimplemented here.
        smaFull[per] = (Q && Q.sma) ? Q.sma(closes, per) : null;
      }
      cursor = bars.length - 1;   // fresh data always lands at the live edge
      setView();
      chart.timeScale().fitContent();
    }

    return { mount, render };
  }

  // ═══ TpoView — Market Profile letter profile from 30m klines (O-3, §4c) ═══
  //
  // CLASSICAL TPO (see buildTpo in terminal-state.js): each 30m bar marks its
  // full H–L range for its clock period — letters A..Z then a..v map period
  // index 0..47 of the UTC day. Column-stacking per price row: the k-th
  // letter at a row sits in the k-th letter column, so row width literally IS
  // the TPO count. Header label 'kline-range TPO · 30m · bybit' (per-source,
  // §0.7). POC line = --accent, VAH/VAL dashed --muted (same vocabulary as
  // the footprint gutter); single prints get a '•' gutter mark; the initial
  // balance (first 2 OBSERVED periods) draws as a left bracket.
  function TpoView() {
    let root = null, canvas = null, sessionSel = null;
    let sessions = null, tick = 1;
    let selDate = null;             // selected session date ('YYYY-MM-DD') — survives refreshes
    const LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuv';   // 48 × 30m periods/UTC day
    const GUT_LEFT = 26, GUT_AXIS = 58;

    function mount(el, opts) {
      root = el;
      canvas = document.createElement('canvas');
      canvas.className = 'term-canvas';
      root.appendChild(canvas);
      sessionSel = (opts || {}).sessionSel || null;
      if (sessionSel) sessionSel.addEventListener('change', () => { selDate = sessionSel.value; draw(); });
    }

    function syncSelect() {
      if (!sessionSel || !sessions) return;
      const dates = sessions.map((s) => s.date);
      const want = dates.join(',');
      if (sessionSel.dataset.dates !== want) {
        sessionSel.dataset.dates = want;
        sessionSel.innerHTML = dates.map((d) => '<option value="' + esc(d) + '">' + esc(d) + '</option>').join('');
      }
      if (dates.indexOf(selDate) < 0) selDate = dates[0] || null;   // sessions are NEWEST-FIRST → [0] = today
      if (selDate) sessionSel.value = selDate;
    }

    function draw() {
      if (!canvas) return;
      const { ctx, w, h } = fitCanvas(canvas);
      const p = pal();
      ctx.clearRect(0, 0, w, h);
      ctx.textBaseline = 'middle';
      const font = (px, bold) => { ctx.font = (bold ? '600 ' : '') + px + 'px ' + cssVar('--mono', 'monospace'); };

      if (!sessions || !sessions.length) {
        font(11); ctx.fillStyle = p.muted; ctx.textAlign = 'left';
        ctx.fillText('awaiting 30m kline history (bybit REST)…', 10, 18);
        return;
      }
      let s = null;
      for (const cand of sessions) if (cand.date === selDate) { s = cand; break; }
      if (!s) s = sessions[0];
      const rows = s.rows;   // price-ASCENDING (buildTpo contract)
      const lo = rows[0].price, hi = rows[rows.length - 1].price;
      const nRows = Math.round((hi - lo) / tick) + 1;
      if (nRows > 400) {
        font(11); ctx.fillStyle = p.muted; ctx.textAlign = 'left';
        ctx.fillText('session spans ' + nRows + ' rows at $' + tick + ' — degenerate tick, not drawn', 10, 18);
        return;
      }
      const capH = 16;                        // caption strip at the top
      const plotH = h - capH - 4;
      const rowH = plotH / nRows;
      const yOf = (price) => capH + ((hi - price) / tick) * rowH;   // row CENTER-ish top edge

      // Letter geometry: fit the widest row; mono glyphs read down to ~6px.
      let maxLetters = 1;
      for (const r of rows) if (r.periods.length > maxLetters) maxLetters = r.periods.length;
      const plotW = w - GUT_LEFT - GUT_AXIS;
      const charW = Math.max(5, Math.min(10, Math.floor(plotW / maxLetters)));
      const fpx = Math.max(6, Math.min(10, Math.floor(Math.min(rowH, charW) + 1)));

      // Rows: letters column-stacked; VA membership brightens, POC row accents.
      const singleSet = new Set(s.singles);
      for (const r of rows) {
        const y = yOf(r.price) + rowH / 2;
        const inVa = r.price >= s.val && r.price <= s.vah;
        font(fpx, r.price === s.poc);
        ctx.textAlign = 'left';
        for (let k = 0; k < r.periods.length; k++) {
          const idx = r.periods[k];
          ctx.fillStyle = r.price === s.poc ? p.accent : inVa ? p.fg : p.muted;
          ctx.fillText(LETTERS[idx] || '?', GUT_LEFT + k * charW, y);
        }
        // Single prints (interior one-period rows): '•' gutter mark — the
        // structure read is "price rejected fast", flagged without color.
        if (singleSet.has(r.price)) {
          font(9, true); ctx.fillStyle = p.accent2;
          ctx.fillText('•', GUT_LEFT - 10, y);
        }
      }

      // POC / VAH / VAL reference lines (footprint-gutter vocabulary). Labels
      // sit in the same right gutter as the price axis, so they get an opaque
      // backing box — otherwise they overprint the axis label at their row
      // (observed collision in the live dogfood pass).
      const hline = (price, color, dash, label) => {
        if (!Number.isFinite(price)) return;
        const y = yOf(price) + rowH / 2;
        ctx.save();
        if (dash) ctx.setLineDash([5, 4]);
        ctx.strokeStyle = color; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(GUT_LEFT - 4, y); ctx.lineTo(w - GUT_AXIS, y); ctx.stroke();
        ctx.restore();
        font(9, true);
        const txt = label + ' ' + fmtUsd(price);
        const tw = ctx.measureText(txt).width;
        ctx.fillStyle = rgba(p.panel2, 0.95);
        ctx.fillRect(w - GUT_AXIS + 1, y - 6, tw + 4, 12);
        ctx.fillStyle = color; ctx.textAlign = 'left';
        ctx.fillText(txt, w - GUT_AXIS + 2, y);
      };
      hline(s.poc, p.accent, false, 'POC');
      hline(s.vah, p.muted, true, 'VAH');
      hline(s.val, p.muted, true, 'VAL');

      // IB bracket (left edge): first 2 OBSERVED periods' raw range — a
      // bracket, not a row (raw l/h, clamped to the plot).
      if (Number.isFinite(s.ib.hi) && Number.isFinite(s.ib.lo) && s.ib.hi >= s.ib.lo) {
        const yT = Math.max(capH, yOf(Math.min(s.ib.hi, hi)));
        const yB = Math.min(capH + plotH, yOf(Math.max(s.ib.lo, lo)) + rowH);
        ctx.strokeStyle = p.accent2; ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.moveTo(8, yT); ctx.lineTo(4, yT); ctx.lineTo(4, yB); ctx.lineTo(8, yB);
        ctx.stroke();
        font(8, true); ctx.fillStyle = p.accent2;
        ctx.save();
        ctx.translate(10, (yT + yB) / 2); ctx.rotate(-Math.PI / 2); ctx.textAlign = 'center';
        ctx.fillText('IB', 0, 0);
        ctx.restore();
      }

      // Price axis (right), thinned to ~16px spacing.
      const labStep = Math.max(1, Math.ceil(16 / rowH));
      font(9); ctx.fillStyle = p.muted; ctx.textAlign = 'right';
      for (let r = 0; r < nRows; r += labStep) {
        const price = hi - r * tick;
        ctx.fillText(fmtUsd(price), w - 2, yOf(price) + rowH / 2);
      }

      // Caption: session date + construction statement (per-source label).
      font(9, true); ctx.fillStyle = p.muted; ctx.textAlign = 'left';
      ctx.fillText(s.date + ' UTC · 30m letters · $' + tick + ' rows · • = single print', 4, 8);
    }

    /** slice = { sessions (buildTpo output, NEWEST-FIRST), tickSize } */
    function render(slice) {
      sessions = slice.sessions || null;
      tick = slice.tickSize || 1;
      syncSelect();
      draw();
    }

    return { mount, render };
  }

  // ═══ KlineVpView — composite volume profile from klines (O-3, §4c) ═══
  //
  // ⚠ BAR-RANGE APPROXIMATION, permanently badged (§4c rail): buildKlineVp
  // spreads each bar's volume uniformly across its H–L ticks because OHLCV
  // bars don't say where volume printed — tick-accurate VP is the footprint
  // gutter (live session) or the collector's stored trades, never this panel.
  // Follows the historical chart's lookback+interval (same bars, stated in
  // the hint). HVN/LVN carry glyph ticks; levels away from the CURRENT price
  // get dashed extension lines — untested at today's price until price
  // returns to them (the CryExc 'extension' read).
  function KlineVpView() {
    let root = null, canvas = null;
    const GUT_AXIS = 58;

    function mount(el) {
      root = el;
      canvas = document.createElement('canvas');
      canvas.className = 'term-canvas';
      root.appendChild(canvas);
    }

    /** slice = { vp: buildKlineVp output, lastPrice, interval } */
    function render(slice) {
      if (!canvas) return;
      const { ctx, w, h } = fitCanvas(canvas);
      const p = pal();
      ctx.clearRect(0, 0, w, h);
      ctx.textBaseline = 'middle';
      const font = (px, bold) => { ctx.font = (bold ? '600 ' : '') + px + 'px ' + cssVar('--mono', 'monospace'); };

      const vp = slice.vp;
      if (!vp || !vp.levels || !vp.levels.length) {
        font(11); ctx.fillStyle = p.muted; ctx.textAlign = 'left';
        ctx.fillText('awaiting kline history for the composite profile…', 10, 30);
        drawWarnBadge(ctx, w, 'bar-range approximation');
        return;
      }
      const levels = vp.levels;   // price-ASCENDING (buildKlineVp contract)
      const lo = levels[0].price, hi = levels[levels.length - 1].price;
      const span = Math.max(hi - lo, 1e-9);
      const capH = 16;
      const plotH = h - capH - 4;
      const rowH = Math.max(1, plotH / levels.length);
      const yOf = (price) => capH + plotH * (hi - price) / span;
      let maxVol = 0;
      for (const lv of levels) if (lv.vol > maxVol) maxVol = lv.vol;
      if (!(maxVol > 0)) return;
      const barMaxW = w - GUT_AXIS - 8;

      // Levels: in-VA bars brighter (--accent2 family), outside dimmer — the
      // VA boundary is also drawn, so color is not the only cue.
      for (const lv of levels) {
        const y = yOf(lv.price);
        const inVa = lv.price >= vp.val && lv.price <= vp.vah;
        ctx.fillStyle = rgba(p.accent2, inVa ? 0.55 : 0.22);
        ctx.fillRect(0, y - rowH / 2 + 0.5, barMaxW * (lv.vol / maxVol), Math.max(1, rowH - 1));
      }

      const last = slice.lastPrice;
      // HVN/LVN glyph ticks at the bar tip + dashed EXTENSION lines out to the
      // axis for nodes away from the current price (untested until revisited).
      const mark = (price, glyph, color) => {
        const y = yOf(price);
        let vol = 0;
        for (const lv of levels) if (lv.price === price) { vol = lv.vol; break; }
        const xTip = barMaxW * (vol / maxVol);
        if (Number.isFinite(last) && Math.abs(price - last) > span * 0.005) {
          ctx.save();
          ctx.setLineDash([3, 4]);
          ctx.strokeStyle = rgba(color, 0.6); ctx.lineWidth = 1;
          ctx.beginPath(); ctx.moveTo(xTip + 8, y); ctx.lineTo(w - GUT_AXIS, y); ctx.stroke();
          ctx.restore();
        }
        font(9, true); ctx.fillStyle = color; ctx.textAlign = 'left';
        ctx.fillText(glyph, xTip + 2, y);
      };
      for (const price of vp.hvns) mark(price, '◆', p.accent);
      for (const price of vp.lvns) mark(price, '◇', p.accent2);

      // POC / VAH / VAL lines + labels (same vocabulary as footprint/TPO).
      // Labels get the same opaque backing as TpoView's: VAH/POC/VAL/last can
      // land within a text-height of each other and would overprint.
      const hline = (price, color, dash, label) => {
        if (!Number.isFinite(price)) return;
        const y = yOf(price);
        ctx.save();
        if (dash) ctx.setLineDash([5, 4]);
        ctx.strokeStyle = color; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w - GUT_AXIS, y); ctx.stroke();
        ctx.restore();
        font(9, true);
        const txt = label + ' ' + fmtUsd(price);
        const tw = ctx.measureText(txt).width;
        ctx.fillStyle = rgba(p.panel2, 0.95);
        ctx.fillRect(w - GUT_AXIS + 1, y - 6, tw + 4, 12);
        ctx.fillStyle = color; ctx.textAlign = 'left';
        ctx.fillText(txt, w - GUT_AXIS + 2, y);
      };
      hline(vp.poc, p.accent, false, 'POC');
      hline(vp.vah, p.muted, true, 'VAH');
      hline(vp.val, p.muted, true, 'VAL');

      // Current price reference (bybit live trades — same venue as the kline
      // profile, so the overlay is single-source; labeled 'last').
      if (Number.isFinite(last) && last >= lo && last <= hi) {
        const y = yOf(last);
        ctx.save();
        ctx.setLineDash([2, 3]);
        ctx.strokeStyle = p.fg; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w - GUT_AXIS, y); ctx.stroke();
        ctx.restore();
        font(9, true);
        const txt = 'last ' + fmtUsd(last);
        const tw = ctx.measureText(txt).width;
        ctx.fillStyle = rgba(p.panel2, 0.95);
        ctx.fillRect(w - GUT_AXIS + 1, y - 6, tw + 4, 12);
        ctx.fillStyle = p.fg; ctx.textAlign = 'left';
        ctx.fillText(txt, w - GUT_AXIS + 2, y);
      }

      // Caption + permanent badge (§4c label rail — never scrolls away).
      font(9, true); ctx.fillStyle = p.muted; ctx.textAlign = 'left';
      // Canvas text, not markup — no esc() (that helper emits HTML entities);
      // the interval string is our own whitelisted select value anyway.
      ctx.fillText('composite VP · bybit klines (' + String(slice.interval || '') + ') · ◆ HVN · ◇ LVN · dashed = untested extension', 4, 8);
      drawWarnBadge(ctx, w, 'bar-range approximation');
    }

    return { mount, render };
  }

  // ═══ FundingArbView — cross-venue funding table (O-3, §4c) ═══
  //
  // DESCRIPTIVE ONLY — the note row states it verbatim: carry remains
  // off-board (B3, RESEARCH.md); a funding spread here is a fact about three
  // venues' prints, never a trade instruction. Sources are per-row labeled:
  // bybit = live WS tickers, binancef = existing 5s/60s REST poller, okx =
  // the O-3 60s REST poll (silent-null tolerated → '—' cells, §4c).
  // Annualized = rate × 8760/intervalH (§4c formula); intervalH is 8 for
  // bybit/binancef (their BTC-perp nextFundingTs spacing) and comes from the
  // OKX funding response for okx (normalizeOkxFunding derives it, fallback 8).
  function FundingArbView() {
    let root = null, rows = {}, spreadEl = null;
    const VENUES = ['bybit', 'binancef', 'okx'];
    const SRC = { bybit: 'WS tickers', binancef: 'REST 5s/60s', okx: 'REST 60s' };

    function mount(el) {
      root = el;
      const table = document.createElement('table');
      table.className = 'farb-table';
      table.innerHTML = '<thead><tr>'
        + '<th>venue</th><th>mark</th><th>funding</th>'
        + '<th title="rate × 8760/intervalH — descriptive, ignores compounding and rate drift">annualized</th>'
        + '<th title="countdown to the displayed rate’s settlement">next in</th>'
        + '<th>OI</th><th>OI $</th>'
        + '</tr></thead>';
      const tbody = document.createElement('tbody');
      for (const ex of VENUES) {
        const tr = document.createElement('tr');
        tr.innerHTML = '<td class="ex ex-' + ex + '">' + ex + ' <span class="farb-src">' + SRC[ex] + '</span></td>'
          + '<td class="num mark">—</td><td class="num fund">—</td><td class="num ann">—</td>'
          + '<td class="num next">—</td><td class="num oi">—</td><td class="num oiusd">—</td>';
        tbody.appendChild(tr);
        rows[ex] = tr;
      }
      const sp = document.createElement('tr');
      sp.className = 'farb-spread';
      sp.innerHTML = '<td colspan="7">spread: —</td>';
      tbody.appendChild(sp);
      spreadEl = sp.firstChild;
      table.appendChild(tbody);
      root.appendChild(table);
      root.insertAdjacentHTML('beforeend',
        '<div class="farb-note">descriptive only — carry remains off-board (B3); a spread is a fact, not a trade.</div>');
    }

    /** slice = { venues: {ex → {mark, last, fundingRate, nextFundingTs,
     *  intervalH, oi, oiUsd}}, nowMs } — bootstrap-composed; '—' for absent. */
    function render(slice) {
      if (!root) return;
      const anns = [];   // [ex, annualized %] for the spread footer
      for (const ex of VENUES) {
        const v = (slice.venues || {})[ex] || {};
        const tds = rows[ex].children;
        // OKX has no keyless mark feed on this page — its price cell shows the
        // venue's own LAST TRADE, labeled, rather than borrowing another
        // venue's mark (§0.7 per-source rail).
        if (Number.isFinite(v.mark)) tds[1].textContent = fmtUsd(v.mark, 1);
        else if (Number.isFinite(v.last)) tds[1].textContent = fmtUsd(v.last, 1) + ' (last)';
        else tds[1].textContent = '—';
        const fr = v.fundingRate;
        if (Number.isFinite(fr)) {
          tds[2].textContent = (fr * 100).toFixed(4) + '%';
          tds[2].className = 'num fund ' + (fr > 0 ? 'pos' : fr < 0 ? 'neg' : '');
          const iv = Number.isFinite(v.intervalH) && v.intervalH > 0 ? v.intervalH : 8;
          const ann = fr * (8760 / iv) * 100;   // §4c: rate × 8760/intervalH
          tds[3].textContent = fmtPct(ann, 1);
          tds[3].className = 'num ann ' + (ann > 0 ? 'pos' : ann < 0 ? 'neg' : '');
          anns.push([ex, ann]);
        } else {
          tds[2].textContent = '—'; tds[2].className = 'num fund';
          tds[3].textContent = '—'; tds[3].className = 'num ann';
        }
        tds[4].textContent = Number.isFinite(v.nextFundingTs) ? countdown(v.nextFundingTs - slice.nowMs) : '—';
        tds[5].textContent = Number.isFinite(v.oi) ? fmtQty(v.oi) + ' BTC' : '—';
        tds[6].textContent = Number.isFinite(v.oiUsd) ? fmtCompactUsd(v.oiUsd) : '—';
      }
      if (anns.length >= 2) {
        let mx = anns[0], mn = anns[0];
        for (const a of anns) { if (a[1] > mx[1]) mx = a; if (a[1] < mn[1]) mn = a; }
        // Annualized %-points → basis points (1% = 100 bp).
        spreadEl.textContent = 'annualized spread: ' + ((mx[1] - mn[1]) * 100).toFixed(1)
          + ' bp (' + mx[0] + ' − ' + mn[0] + ')';
      } else {
        spreadEl.textContent = 'annualized spread: — (needs ≥ 2 venues reporting)';
      }
    }

    return { mount, render };
  }

  // ═══ MacroView — HIP-3 mids strip + correlation block (O-3, §4c) ═══
  //
  // The honest macro panel keyless crypto rails allow (§4c empirical map):
  // Hyperliquid HIP-3 index/commodity perps expose LIVE MIDS ONLY (no
  // keyless history), so HIP-3 legs show session-% from OUR polled samples
  // and session correlation labeled with n — cells hide behind 'accruing'
  // below n=30 because a 20-minute correlation is an anecdote wearing a
  // number. History-backed legs (BTC/ETH/PAXG, Bybit klines) get a real 7d
  // rolling correlation, labeled '7d · 1h bars'. km:GOLD vs PAXG divergence
  // is its own cell: the HIP-3 gold perp persistently trades ~4% rich vs the
  // tokenized-gold proxy — that tracking error is exactly why the caveat
  // line exists, so we SHOW it instead of averaging it away.
  function MacroView() {
    let root = null, stripEl = null, corrEl = null;

    function mount(el) {
      root = el;
      stripEl = document.createElement('div');
      stripEl.className = 'macro-strip';
      root.appendChild(stripEl);
      corrEl = document.createElement('div');
      corrEl.className = 'macro-corr';
      root.appendChild(corrEl);
      root.insertAdjacentHTML('beforeend',
        '<div class="farb-note">on-chain perp/token proxies — tracking error vs the real index · no CME feeds.</div>');
    }

    /** slice = { strip:[{label, px, pctOnly, sessPct, src}],
     *            corr7d:{btcEth,btcPaxg,ethPaxg}|null,
     *            sessCorr:[{label, r, n}], goldPrem } — bootstrap-composed. */
    function render(slice) {
      if (!root) return;
      let html = '';
      for (const it of slice.strip || []) {
        // km:US500 is a SCALED contract — its mid is not the index level, so
        // the price cell is suppressed and only the %-change is meaningful.
        const px = it.pctOnly ? '<span class="macro-scaled">(scaled — % only)</span>'
          : (Number.isFinite(it.px) ? fmtUsd(it.px, it.px < 100 ? 2 : it.px < 10000 ? 1 : 0) : '—');
        const cls = it.sessPct > 0 ? 'pos' : it.sessPct < 0 ? 'neg' : '';
        html += '<div class="macro-cell">'
          + '<span class="k">' + esc(it.label) + ' <i class="macro-src">' + esc(it.src || '') + '</i></span>'
          + '<span class="v num">' + px + '</span>'
          + '<span class="v num ' + cls + '" title="change since this page’s first sample — session-local, no backfill (§0.7)">' + fmtPct(it.sessPct) + ' <i class="macro-src">session</i></span>'
          + '</div>';
      }
      stripEl.innerHTML = html;

      const fmtR = (r) => Number.isFinite(r) ? r.toFixed(2) : '—';
      const c7 = slice.corr7d;
      let ch = '<div class="macro-corr-head">correlation</div>';
      const row = (label, val, tag) =>
        '<div class="macro-corr-row"><span>' + label + '</span><span class="num">' + val + '</span><span class="macro-src">' + tag + '</span></div>';
      ch += row('BTC × ETH', fmtR(c7 ? c7.btcEth : NaN), '7d · 1h bars');
      ch += row('BTC × PAXG', fmtR(c7 ? c7.btcPaxg : NaN), '7d · 1h bars');
      ch += row('ETH × PAXG', fmtR(c7 ? c7.ethPaxg : NaN), '7d · 1h bars');
      for (const sc of slice.sessCorr || []) {
        // Small-n honesty (§4c): below n=30 the cell says it is ACCRUING —
        // the sample count is part of the result, not droppable metadata.
        if (sc.n >= 30) ch += row(esc(sc.label), fmtR(sc.r), 'session · n=' + sc.n);
        else ch += row(esc(sc.label), 'n=' + (sc.n || 0) + ' — accruing', 'session');
      }
      ch += row('km:GOLD vs PAXG', fmtPct(slice.goldPrem, 2),
        'divergence — the HIP-3 gold perp trades rich vs tokenized gold; informative, not an error to hide');
      corrEl.innerHTML = ch;
    }

    return { mount, render };
  }

  // ─── Export — ONE global + Node (quant.js dual-export pattern) ──────────

  const TerminalViews = {
    FootprintView, DomLadderView, TapeView, AggBookView, HeaderStatsView, LiqFeedView,
    // O-2 (§4b): depth-history heatmap + labeled model/heuristic panels.
    BookHeatmapView, LiqHeatmapView, DetectionFeedView,
    // O-3 (§4c): structure panels — historical chart + TPO + composite VP +
    // funding table + macro strip, every one per-source labeled.
    HistChartView, TpoView, KlineVpView, FundingArbView, MacroView,
  };

  if (typeof module !== 'undefined' && module.exports) module.exports = TerminalViews;
  if (typeof global !== 'undefined') global.BTCQ_TERMINAL_VIEWS = TerminalViews;
})(typeof globalThis !== 'undefined' ? globalThis : this);
