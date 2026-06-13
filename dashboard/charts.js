// charts.js — dependency-free SVG charting for the btc-quant terminal.
//
// Pure SVG, no D3 / Chart.js / CDN — same no-build, no-framework ethos as
// Lattice's viz.js. Every chart is a function (root: HTMLElement, data, opts)
// that wipes `root` and mounts one <svg>. Colours come from CSS custom
// properties so the dark terminal theme stays in one place (styles.css).
//
// Charts provided:
//   candles(root, ohlc, opts)        candlesticks + optional MA overlays
//   lineChart(root, series, opts)     one-or-more line series (e.g. equity curves)
//   drawdownArea(root, dd, opts)      filled drawdown (underwater) area
//   histogram(root, values, opts)     returns distribution
//   rollingChart(root, series, opts)  rolling vol / Sharpe line(s)
//   fundingBars(root, rates, opts)    funding-rate bar chart (signed colours)
//   xyChart(root, series, opts)       x-NUMERIC scatter+line (option smile /
//                                      term structure: irregular x spacing that
//                                      lineChart's index-x axis cannot express)
'use strict';

(function (global) {
  const NS = 'http://www.w3.org/2000/svg';

  function el(name, attrs) {
    const node = document.createElementNS(NS, name);
    if (attrs) for (const k in attrs) node.setAttribute(k, attrs[k]);
    return node;
  }

  function clear(root) { while (root.firstChild) root.removeChild(root.firstChild); }

  // Resolve a CSS custom property (falls back to a literal default).
  function cssVar(name, fallback) {
    try {
      const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
      return v || fallback;
    } catch (_) { return fallback; }
  }

  const COLORS = {
    up: () => cssVar('--up', '#36d399'),
    down: () => cssVar('--down', '#f87272'),
    grid: () => cssVar('--grid', '#23314a'),
    axis: () => cssVar('--muted', '#7b8db0'),
    accent: () => cssVar('--accent', '#7aa2ff'),
    accent2: () => cssVar('--accent-2', '#ffcf6b'),
    baseline: () => cssVar('--muted', '#7b8db0'),
    text: () => cssVar('--fg', '#d6e0f5'),
  };

  // Build an <svg> sized to the host element (responsive via viewBox).
  function makeSvg(root, opts) {
    clear(root);
    const w = opts.width || root.clientWidth || 640;
    const h = opts.height || 240;
    const svg = el('svg', {
      viewBox: `0 0 ${w} ${h}`,
      width: '100%',
      height: h,
      preserveAspectRatio: 'none',
      class: 'chart-svg',
      role: 'img',
    });
    root.appendChild(svg);
    return { svg, w, h };
  }

  const PAD = { l: 52, r: 12, t: 12, b: 22 };

  function scaleLin(domainMin, domainMax, rangeMin, rangeMax) {
    const d = domainMax - domainMin || 1;
    return (v) => rangeMin + ((v - domainMin) / d) * (rangeMax - rangeMin);
  }

  function fmtNum(v) {
    if (!Number.isFinite(v)) return '—';
    const a = Math.abs(v);
    if (a >= 1e9) return (v / 1e9).toFixed(2) + 'B';
    if (a >= 1e6) return (v / 1e6).toFixed(2) + 'M';
    if (a >= 1e3) return (v / 1e3).toFixed(1) + 'k';
    if (a >= 1) return v.toFixed(2);
    return v.toFixed(4);
  }

  // Draw y-axis gridlines + labels. Returns the plotting rect.
  function drawGrid(svg, w, h, yMin, yMax, opts = {}) {
    const x0 = PAD.l, x1 = w - PAD.r, y0 = PAD.t, y1 = h - PAD.b;
    const yScale = scaleLin(yMin, yMax, y1, y0);
    const ticks = opts.ticks || 4;
    const fmt = opts.fmt || fmtNum;
    for (let i = 0; i <= ticks; i++) {
      const v = yMin + (i / ticks) * (yMax - yMin);
      const y = yScale(v);
      svg.appendChild(el('line', { x1: x0, y1: y, x2: x1, y2: y, stroke: COLORS.grid(), 'stroke-width': 1, opacity: 0.5 }));
      const t = el('text', { x: x0 - 6, y: y + 3, 'text-anchor': 'end', class: 'chart-axis-label' });
      t.textContent = fmt(v);
      svg.appendChild(t);
    }
    return { x0, x1, y0, y1, yScale };
  }

  function path(points) {
    return points.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(2) + ' ' + p[1].toFixed(2)).join(' ');
  }

  // ─── Candlesticks + MA overlays ───────────────────────────────────────

  /**
   * @param {object} ohlc { time[], open[], high[], low[], close[] }
   * @param {object} opts { height, overlays: [{values, color, label}] }
   */
  function candles(root, ohlc, opts = {}) {
    const { svg, w, h } = makeSvg(root, opts);
    const n = ohlc.close.length;
    if (!n) return drawEmpty(svg, w, h);
    let lo = Infinity, hi = -Infinity;
    for (let i = 0; i < n; i++) {
      if (Number.isFinite(ohlc.low[i])) lo = Math.min(lo, ohlc.low[i]);
      if (Number.isFinite(ohlc.high[i])) hi = Math.max(hi, ohlc.high[i]);
    }
    (opts.overlays || []).forEach((ov) => {
      ov.values.forEach((v) => { if (Number.isFinite(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); } });
    });
    const pad = (hi - lo) * 0.04 || 1;
    lo -= pad; hi += pad;
    const { x0, x1, yScale } = drawGrid(svg, w, h, lo, hi);
    const cw = (x1 - x0) / n;
    const bodyW = Math.max(1, cw * 0.62);
    for (let i = 0; i < n; i++) {
      const cx = x0 + (i + 0.5) * cw;
      const o = ohlc.open[i], c = ohlc.close[i], hgh = ohlc.high[i], low = ohlc.low[i];
      if (![o, c, hgh, low].every(Number.isFinite)) continue;
      const color = c >= o ? COLORS.up() : COLORS.down();
      svg.appendChild(el('line', { x1: cx, y1: yScale(hgh), x2: cx, y2: yScale(low), stroke: color, 'stroke-width': 1 }));
      const yTop = yScale(Math.max(o, c)), yBot = yScale(Math.min(o, c));
      svg.appendChild(el('rect', {
        x: cx - bodyW / 2, y: yTop, width: bodyW, height: Math.max(1, yBot - yTop),
        fill: color, opacity: 0.9,
      }));
    }
    (opts.overlays || []).forEach((ov) => {
      const pts = [];
      for (let i = 0; i < n; i++) {
        if (Number.isFinite(ov.values[i])) pts.push([x0 + (i + 0.5) * cw, yScale(ov.values[i])]);
      }
      if (pts.length > 1) svg.appendChild(el('path', { d: path(pts), fill: 'none', stroke: ov.color || COLORS.accent(), 'stroke-width': 1.5, opacity: 0.95 }));
    });
    if (opts.overlays && opts.overlays.length) drawLegend(svg, w, opts.overlays);
  }

  // ─── Multi-line chart (equity curves etc.) ────────────────────────────

  /**
   * @param {object[]} series [{values, color, label, dash}]
   * @param {object} opts { height, baseline (y value), logY, fmt }
   */
  function lineChart(root, series, opts = {}) {
    const { svg, w, h } = makeSvg(root, opts);
    const all = [];
    series.forEach((s) => s.values.forEach((v) => { if (Number.isFinite(v)) all.push(v); }));
    if (!all.length) return drawEmpty(svg, w, h);
    let lo = Math.min(...all), hi = Math.max(...all);
    if (lo === hi) { lo -= 1; hi += 1; }
    const pad = (hi - lo) * 0.05;
    lo -= pad; hi += pad;
    const len = Math.max(...series.map((s) => s.values.length));
    const { x0, x1, yScale } = drawGrid(svg, w, h, lo, hi, { fmt: opts.fmt });
    const xScale = scaleLin(0, len - 1, x0, x1);
    if (opts.baseline != null && opts.baseline >= lo && opts.baseline <= hi) {
      svg.appendChild(el('line', { x1: x0, y1: yScale(opts.baseline), x2: x1, y2: yScale(opts.baseline), stroke: COLORS.baseline(), 'stroke-width': 1, 'stroke-dasharray': '4 4', opacity: 0.6 }));
    }
    series.forEach((s) => {
      const pts = [];
      for (let i = 0; i < s.values.length; i++) if (Number.isFinite(s.values[i])) pts.push([xScale(i), yScale(s.values[i])]);
      if (pts.length > 1) {
        const attrs = { d: path(pts), fill: 'none', stroke: s.color || COLORS.accent(), 'stroke-width': s.width || 1.6 };
        if (s.dash) attrs['stroke-dasharray'] = s.dash;
        svg.appendChild(el('path', attrs));
      }
    });
    drawLegend(svg, w, series);
  }

  // ─── Drawdown (underwater) area ───────────────────────────────────────

  function drawdownArea(root, dd, opts = {}) {
    const { svg, w, h } = makeSvg(root, opts);
    if (!dd.length) return drawEmpty(svg, w, h);
    const lo = Math.min(0, ...dd.filter(Number.isFinite));
    const { x0, x1, yScale } = drawGrid(svg, w, h, lo, 0, { fmt: (v) => (v * 100).toFixed(0) + '%' });
    const xScale = scaleLin(0, dd.length - 1, x0, x1);
    const pts = [[x0, yScale(0)]];
    for (let i = 0; i < dd.length; i++) pts.push([xScale(i), yScale(Number.isFinite(dd[i]) ? dd[i] : 0)]);
    pts.push([x1, yScale(0)]);
    svg.appendChild(el('path', { d: path(pts) + ' Z', fill: COLORS.down(), opacity: 0.28 }));
    const line = [];
    for (let i = 0; i < dd.length; i++) line.push([xScale(i), yScale(Number.isFinite(dd[i]) ? dd[i] : 0)]);
    svg.appendChild(el('path', { d: path(line), fill: 'none', stroke: COLORS.down(), 'stroke-width': 1.3 }));
  }

  // ─── Returns histogram ────────────────────────────────────────────────

  function histogram(root, values, opts = {}) {
    const { svg, w, h } = makeSvg(root, opts);
    const fin = values.filter(Number.isFinite);
    if (!fin.length) return drawEmpty(svg, w, h);
    const bins = opts.bins || 31;
    let lo = Math.min(...fin), hi = Math.max(...fin);
    if (lo === hi) { lo -= 0.01; hi += 0.01; }
    const counts = new Array(bins).fill(0);
    const bw = (hi - lo) / bins;
    for (const v of fin) {
      let idx = Math.floor((v - lo) / bw);
      if (idx >= bins) idx = bins - 1;
      if (idx < 0) idx = 0;
      counts[idx]++;
    }
    const maxC = Math.max(...counts);
    const { x0, x1, y0, y1 } = drawGrid(svg, w, h, 0, maxC, { ticks: 3, fmt: (v) => v.toFixed(0) });
    const xScale = scaleLin(lo, hi, x0, x1);
    const yScale = scaleLin(0, maxC, y1, y0);
    const cw = (x1 - x0) / bins;
    for (let i = 0; i < bins; i++) {
      const binMid = lo + (i + 0.5) * bw;
      const bx = xScale(lo + i * bw);
      const by = yScale(counts[i]);
      svg.appendChild(el('rect', {
        x: bx + 0.5, y: by, width: Math.max(1, cw - 1), height: Math.max(0, y1 - by),
        fill: binMid >= 0 ? COLORS.up() : COLORS.down(), opacity: 0.8,
      }));
    }
    // zero line
    if (lo < 0 && hi > 0) svg.appendChild(el('line', { x1: xScale(0), y1: y0, x2: xScale(0), y2: y1, stroke: COLORS.axis(), 'stroke-width': 1, 'stroke-dasharray': '3 3' }));
  }

  // ─── Rolling vol / Sharpe ─────────────────────────────────────────────

  function rollingChart(root, series, opts = {}) {
    lineChart(root, series, Object.assign({ baseline: 0 }, opts));
  }

  // ─── Funding bars ─────────────────────────────────────────────────────

  /** @param {number[]} rates per-interval funding (positive longs-pay-shorts). */
  function fundingBars(root, rates, opts = {}) {
    const { svg, w, h } = makeSvg(root, opts);
    const fin = rates.filter(Number.isFinite);
    if (!fin.length) return drawEmpty(svg, w, h);
    let lo = Math.min(0, ...fin), hi = Math.max(0, ...fin);
    const pad = (hi - lo) * 0.08 || 1e-5;
    lo -= pad; hi += pad;
    const { x0, x1, yScale } = drawGrid(svg, w, h, lo, hi, { fmt: (v) => (v * 100).toFixed(3) + '%' });
    const xScale = scaleLin(0, rates.length, x0, x1);
    const cw = (x1 - x0) / rates.length;
    const zeroY = yScale(0);
    svg.appendChild(el('line', { x1: x0, y1: zeroY, x2: x1, y2: zeroY, stroke: COLORS.axis(), 'stroke-width': 1 }));
    for (let i = 0; i < rates.length; i++) {
      const v = rates[i];
      if (!Number.isFinite(v)) continue;
      const bx = xScale(i);
      const by = yScale(v);
      svg.appendChild(el('rect', {
        x: bx + 0.3, y: Math.min(zeroY, by), width: Math.max(0.6, cw - 0.6), height: Math.max(0.5, Math.abs(by - zeroY)),
        fill: v >= 0 ? COLORS.up() : COLORS.down(), opacity: 0.85,
      }));
    }
  }

  // ─── x-NUMERIC scatter + line (option smile / ATM term structure) ───────

  /**
   * Plot one-or-more series against a REAL numeric x-axis (log-moneyness,
   * strike, or days-to-expiry) — unlike lineChart/rollingChart, which place
   * points at array index i. Each series supplies parallel x[] and y[] arrays;
   * non-finite pairs are skipped. Markers (circle) double as a non-color cue
   * (§4.7). markers:'lineOnly' suppresses dots for a dense overlay (e.g. DVOL).
   *
   * @param {object[]} series [{x[], y[], color, label, dash, markers}]
   * @param {object} opts { height, xFmt, yFmt, xLabel, vlines:[{x,color,label}] }
   */
  function xyChart(root, series, opts = {}) {
    const { svg, w, h } = makeSvg(root, opts);
    const xs = [], ys = [];
    series.forEach((s) => {
      const n = Math.min(s.x.length, s.y.length);
      for (let i = 0; i < n; i++) {
        if (Number.isFinite(s.x[i]) && Number.isFinite(s.y[i])) { xs.push(s.x[i]); ys.push(s.y[i]); }
      }
    });
    if (!xs.length) return drawEmpty(svg, w, h);
    let xLo = Math.min(...xs), xHi = Math.max(...xs);
    let yLo = Math.min(...ys), yHi = Math.max(...ys);
    if (opts.vlines) opts.vlines.forEach((vl) => { if (Number.isFinite(vl.x)) { xLo = Math.min(xLo, vl.x); xHi = Math.max(xHi, vl.x); } });
    if (xLo === xHi) { xLo -= 1; xHi += 1; }
    if (yLo === yHi) { yLo -= 1; yHi += 1; }
    const xPad = (xHi - xLo) * 0.05, yPad = (yHi - yLo) * 0.08;
    xLo -= xPad; xHi += xPad; yLo -= yPad; yHi += yPad;

    const yFmt = opts.yFmt || fmtNum, xFmt = opts.xFmt || fmtNum;
    const { x0, x1, y0, y1, yScale } = drawGrid(svg, w, h, yLo, yHi, { fmt: yFmt });
    const xScale = scaleLin(xLo, xHi, x0, x1);

    // x-axis ticks + labels (this axis is meaningful, unlike the index charts).
    const xt = 5;
    for (let i = 0; i <= xt; i++) {
      const v = xLo + (i / xt) * (xHi - xLo);
      const x = xScale(v);
      svg.appendChild(el('line', { x1: x, y1: y0, x2: x, y2: y1, stroke: COLORS.grid(), 'stroke-width': 1, opacity: 0.35 }));
      const t = el('text', { x, y: y1 + 14, 'text-anchor': 'middle', class: 'chart-axis-label' });
      t.textContent = xFmt(v);
      svg.appendChild(t);
    }
    if (opts.xLabel) {
      const xl = el('text', { x: (x0 + x1) / 2, y: h - 2, 'text-anchor': 'middle', class: 'chart-axis-label' });
      xl.textContent = opts.xLabel;
      svg.appendChild(xl);
    }

    // Vertical reference lines (e.g. ATM forward at k=0).
    (opts.vlines || []).forEach((vl) => {
      if (!Number.isFinite(vl.x)) return;
      const x = xScale(vl.x);
      svg.appendChild(el('line', { x1: x, y1: y0, x2: x, y2: y1, stroke: vl.color || COLORS.axis(), 'stroke-width': 1, 'stroke-dasharray': '3 4', opacity: 0.8 }));
      if (vl.label) {
        const t = el('text', { x: x + 3, y: y0 + 10, 'text-anchor': 'start', class: 'chart-axis-label' });
        t.textContent = vl.label;
        svg.appendChild(t);
      }
    });

    series.forEach((s) => {
      const pts = [];
      const n = Math.min(s.x.length, s.y.length);
      for (let i = 0; i < n; i++) if (Number.isFinite(s.x[i]) && Number.isFinite(s.y[i])) pts.push([xScale(s.x[i]), yScale(s.y[i])]);
      pts.sort((a, b) => a[0] - b[0]);   // monotone x for a clean polyline
      const color = s.color || COLORS.accent();
      if (pts.length > 1) {
        const attrs = { d: path(pts), fill: 'none', stroke: color, 'stroke-width': s.width || 1.6 };
        if (s.dash) attrs['stroke-dasharray'] = s.dash;
        svg.appendChild(el('path', attrs));
      }
      if (s.markers !== 'lineOnly') {
        pts.forEach((p) => svg.appendChild(el('circle', { cx: p[0], cy: p[1], r: 2.4, fill: color, opacity: 0.95 })));
      }
    });
    drawLegend(svg, w, series);
  }

  // ─── Shared decorations ───────────────────────────────────────────────

  function drawLegend(svg, w, items) {
    const g = el('g', { class: 'chart-legend' });
    let x = PAD.l + 4;
    const y = PAD.t + 4;
    items.forEach((it) => {
      if (!it.label) return;
      g.appendChild(el('rect', { x, y: y - 7, width: 10, height: 3, fill: it.color || COLORS.accent() }));
      const t = el('text', { x: x + 14, y: y - 2, class: 'chart-legend-label' });
      t.textContent = it.label;
      g.appendChild(t);
      x += 16 + (it.label.length * 6.2);
    });
    svg.appendChild(g);
  }

  function drawEmpty(svg, w, h) {
    const t = el('text', { x: w / 2, y: h / 2, 'text-anchor': 'middle', class: 'chart-empty' });
    t.textContent = 'no data';
    svg.appendChild(t);
  }

  const Charts = { candles, lineChart, drawdownArea, histogram, rollingChart, fundingBars, xyChart };

  if (typeof module !== 'undefined' && module.exports) module.exports = Charts;
  if (typeof global !== 'undefined') global.Charts = Charts;
})(typeof globalThis !== 'undefined' ? globalThis : this);
