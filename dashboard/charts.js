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
    accent2: () => cssVar('--accent-2', '#5BA3F5'),
    baseline: () => cssVar('--muted', '#7b8db0'),
    text: () => cssVar('--fg', '#d6e0f5'),
    // Categorical data-series palette (Module 4) — data colours live here, NOT
    // on the amber brand accent. Auto-assigned by series index, wraps at 6.
    series: (i) => {
      const PAL = ['#C792EA', '#5BA3F5', '#F2A6C2', '#58C7E0', '#A8B0C0', '#D6B3FF'];
      const k = ((i | 0) % PAL.length + PAL.length) % PAL.length;
      return cssVar('--c' + (k + 1), PAL[k]);
    },
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
    (opts.overlays || []).forEach((ov, oi) => {
      const pts = [];
      for (let i = 0; i < n; i++) {
        if (Number.isFinite(ov.values[i])) pts.push([x0 + (i + 0.5) * cw, yScale(ov.values[i])]);
      }
      if (pts.length > 1) svg.appendChild(el('path', { d: path(pts), fill: 'none', stroke: ov.color || COLORS.series(oi), 'stroke-width': 1.5, opacity: 0.95 }));
    });
    if (opts.overlays && opts.overlays.length) drawLegend(svg, w, opts.overlays);
    // Crosshair + tooltip: per-bar OHLC + overlays, x-label = the bar's date.
    const cols = [];
    for (let i = 0; i < n; i++) {
      const o = ohlc.open[i], c = ohlc.close[i], hg = ohlc.high[i], lw = ohlc.low[i];
      if (![o, c, hg, lw].every(Number.isFinite)) continue;
      const cc = c >= o ? COLORS.up() : COLORS.down();
      const items = [
        { label: 'O', color: cc, py: yScale(o), valText: fmtNum(o), dot: false },
        { label: 'H', color: cc, py: yScale(hg), valText: fmtNum(hg), dot: false },
        { label: 'L', color: cc, py: yScale(lw), valText: fmtNum(lw), dot: false },
        { label: 'C', color: cc, py: yScale(c), valText: fmtNum(c) },
      ];
      (opts.overlays || []).forEach((ov, oi) => { if (Number.isFinite(ov.values[i])) items.push({ label: ov.label || 'MA', color: ov.color || COLORS.series(oi), py: yScale(ov.values[i]), valText: fmtNum(ov.values[i]) }); });
      cols.push({ px: x0 + (i + 0.5) * cw, xLabel: ohlc.time ? new Date(ohlc.time[i]).toISOString().slice(0, 10) : ('bar ' + (i + 1)), items });
    }
    attachHover(root, svg, { w, x0, x1, y0: PAD.t, y1: h - PAD.b, cols });
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
    series.forEach((s, si) => {
      const pts = [];
      for (let i = 0; i < s.values.length; i++) if (Number.isFinite(s.values[i])) pts.push([xScale(i), yScale(s.values[i])]);
      if (pts.length > 1) {
        const attrs = { d: path(pts), fill: 'none', stroke: s.color || COLORS.series(si), 'stroke-width': s.width || 1.6 };
        if (s.dash) attrs['stroke-dasharray'] = s.dash;
        svg.appendChild(el('path', attrs));
      }
    });
    drawLegend(svg, w, series);
    // Crosshair + tooltip: one column per bar index; each series' value at i.
    const cols = [];
    for (let i = 0; i < len; i++) {
      const items = [];
      series.forEach((s, si) => { if (Number.isFinite(s.values[i])) items.push({ label: s.label || 'series', color: s.color || COLORS.series(si), py: yScale(s.values[i]), valText: (opts.fmt || fmtNum)(s.values[i]) }); });
      if (items.length) cols.push({ px: xScale(i), xLabel: (opts.xLabels && opts.xLabels[i]) || ('bar ' + (i + 1)), items });
    }
    attachHover(root, svg, { w, x0, x1, y0: PAD.t, y1: h - PAD.b, cols });
  }

  // ─── Drawdown (underwater) area ───────────────────────────────────────

  function drawdownArea(root, dd, opts = {}) {
    const { svg, w, h } = makeSvg(root, opts);
    if (!dd.length) return drawEmpty(svg, w, h);
    const lo = Math.min(0, ...dd.filter(Number.isFinite));
    const { x0, x1, y0, y1, yScale } = drawGrid(svg, w, h, lo, 0, { fmt: (v) => (v * 100).toFixed(0) + '%' });
    const xScale = scaleLin(0, dd.length - 1, x0, x1);
    const pts = [[x0, yScale(0)]];
    for (let i = 0; i < dd.length; i++) pts.push([xScale(i), yScale(Number.isFinite(dd[i]) ? dd[i] : 0)]);
    pts.push([x1, yScale(0)]);
    svg.appendChild(el('path', { d: path(pts) + ' Z', fill: COLORS.down(), opacity: 0.28 }));
    const line = [];
    for (let i = 0; i < dd.length; i++) line.push([xScale(i), yScale(Number.isFinite(dd[i]) ? dd[i] : 0)]);
    svg.appendChild(el('path', { d: path(line), fill: 'none', stroke: COLORS.down(), 'stroke-width': 1.3 }));
    // Hover (reuse attachHover — a single series fits): drawdown depth + date, plus the
    // running-peak date it is measured from. The peak is the most recent bar where the
    // drawdown returned to 0 (a new equity high), recoverable from dd alone.
    const dates = opts.dates || [];
    const fmtDate = (ms) => (Number.isFinite(ms) ? new Date(ms).toISOString().slice(0, 10) : '—');
    const cols = [];
    let peakIdx = 0;
    for (let i = 0; i < dd.length; i++) {
      const v = Number.isFinite(dd[i]) ? dd[i] : 0;
      if (v >= -1e-9) peakIdx = i;                   // back to a high → new running peak
      const items = [{ label: 'drawdown', color: COLORS.down(), py: yScale(v), valText: (v * 100).toFixed(1) + '%' }];
      if (dates.length) items.push({ label: 'from peak', color: COLORS.axis(), py: yScale(0), valText: fmtDate(dates[peakIdx]), dot: false });
      cols.push({ px: xScale(i), xLabel: dates.length ? fmtDate(dates[i]) : ('bar ' + (i + 1)), items });
    }
    attachHover(root, svg, { w, x0, x1, y0, y1, cols });
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
    const bars = [];
    for (let i = 0; i < bins; i++) {
      const binMid = lo + (i + 0.5) * bw;
      const bx = xScale(lo + i * bw);
      const by = yScale(counts[i]);
      const r = el('rect', {
        x: bx + 0.5, y: by, width: Math.max(1, cw - 1), height: Math.max(0, y1 - by),
        fill: binMid >= 0 ? COLORS.up() : COLORS.down(), opacity: 0.8,
      });
      svg.appendChild(r);
      bars.push(r);
    }
    // zero line
    if (lo < 0 && hi > 0) svg.appendChild(el('line', { x1: xScale(0), y1: y0, x2: xScale(0), y2: y1, stroke: COLORS.axis(), 'stroke-width': 1, 'stroke-dasharray': '3 3' }));
    // Mean marker — a reference annotation (amber UI marker, not a data series).
    const total = fin.length;
    const mean = fin.reduce((a, b) => a + b, 0) / total;
    if (mean >= lo && mean <= hi) {
      const mx = xScale(mean);
      svg.appendChild(el('line', { x1: mx, y1: y0, x2: mx, y2: y1, stroke: COLORS.accent(), 'stroke-width': 1, 'stroke-dasharray': '2 3', opacity: 0.9 }));
      const ml = el('text', { x: mx + 3, y: y0 + 9, 'text-anchor': 'start', class: 'chart-axis-label', fill: COLORS.accent() });
      ml.textContent = 'μ ' + (mean * 100).toFixed(2) + '%';
      svg.appendChild(ml);
    }
    // Per-bin hover: the BAR is the hit target (a column-snap crosshair is wrong for a
    // histogram). A transparent full-height rect per bin makes even short bars easy to
    // hit; the tooltip shows the bucket range + count/frequency. Self-contained here,
    // reusing the .chart-tip styling. Fail-safe: any error disables hover, not the chart.
    try {
      const pct = (v) => (v * 100).toFixed(1) + '%';
      let tip = root.querySelector('.chart-tip');
      if (!tip) { tip = document.createElement('div'); tip.className = 'chart-tip'; root.appendChild(tip); }
      tip.hidden = true;
      for (let i = 0; i < bins; i++) {
        const bx = xScale(lo + i * bw);
        const hit = el('rect', { x: bx, y: y0, width: Math.max(1, cw), height: y1 - y0, fill: 'transparent' });
        const bi = i;
        hit.addEventListener('mouseenter', () => { if (bars[bi]) bars[bi].setAttribute('opacity', '1'); });
        hit.addEventListener('mouseleave', () => { if (bars[bi]) bars[bi].setAttribute('opacity', '0.8'); tip.hidden = true; });
        hit.addEventListener('mousemove', (ev) => {
          try {
            const e0 = lo + bi * bw, e1 = lo + (bi + 1) * bw, c = counts[bi];
            const sw = ((e0 + e1) / 2) >= 0 ? COLORS.up() : COLORS.down();
            tip.innerHTML = '<div class="chart-tip-x">[' + pct(e0) + ', ' + pct(e1) + ')</div>'
              + '<div class="chart-tip-row"><span class="sw" style="background:' + sw + '"></span>count<b>' + c + ' days</b></div>'
              + '<div class="chart-tip-row"><span class="sw" style="background:transparent"></span>freq<b>' + (total ? (c / total * 100).toFixed(1) + '%' : '—') + '</b></div>';
            tip.hidden = false;
            const rr = root.getBoundingClientRect();
            const tw = tip.offsetWidth || 130;
            let left = ev.clientX - rr.left + 14;
            if (left + tw > rr.width - 4) left = ev.clientX - rr.left - tw - 14;
            tip.style.left = Math.max(4, left) + 'px';
          } catch (_) { tip.hidden = true; }
        });
        svg.appendChild(hit);
      }
      svg.style.cursor = 'crosshair';
    } catch (_) { /* hover disabled, chart unaffected */ }
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

    series.forEach((s, si) => {
      const pts = [];
      const n = Math.min(s.x.length, s.y.length);
      for (let i = 0; i < n; i++) if (Number.isFinite(s.x[i]) && Number.isFinite(s.y[i])) pts.push([xScale(s.x[i]), yScale(s.y[i])]);
      pts.sort((a, b) => a[0] - b[0]);   // monotone x for a clean polyline
      const color = s.color || COLORS.series(si);
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
    // Crosshair + tooltip: irregular x, so each data point is its own column;
    // hover snaps to the nearest point and shows its series / x / y.
    const cols = [];
    series.forEach((s, si) => {
      const color = s.color || COLORS.series(si);
      const m = Math.min(s.x.length, s.y.length);
      for (let i = 0; i < m; i++) if (Number.isFinite(s.x[i]) && Number.isFinite(s.y[i])) {
        cols.push({ px: xScale(s.x[i]), xLabel: (opts.xFmt || fmtNum)(s.x[i]), items: [{ label: s.label || 'series', color, py: yScale(s.y[i]), valText: (opts.yFmt || fmtNum)(s.y[i]) }] });
      }
    });
    cols.sort((a, b) => a.px - b.px);
    attachHover(root, svg, { w, x0, x1, y0, y1, cols });
  }

  // ─── Shared decorations ───────────────────────────────────────────────

  // Legend swatch is a LINE that mirrors the series' dash pattern, so the legend
  // encodes line-style as well as colour (CVD-safe redundant channel, §4.7).
  function drawLegend(svg, w, items) {
    const g = el('g', { class: 'chart-legend' });
    let x = PAD.l + 4;
    const y = PAD.t + 4;
    items.forEach((it, i) => {
      if (!it.label) return;
      const color = it.color || COLORS.series(i);
      const sw = el('line', { x1: x, y1: y - 5, x2: x + 15, y2: y - 5, stroke: color, 'stroke-width': 2.4, 'stroke-linecap': 'round' });
      if (it.dash) sw.setAttribute('stroke-dasharray', it.dash);
      g.appendChild(sw);
      const t = el('text', { x: x + 19, y: y - 2, class: 'chart-legend-label' });
      t.textContent = it.label;
      g.appendChild(t);
      x += 26 + (it.label.length * 6.2);
    });
    svg.appendChild(g);
  }

  function drawEmpty(svg, w, h) {
    const t = el('text', { x: w / 2, y: h / 2, 'text-anchor': 'middle', class: 'chart-empty' });
    t.textContent = 'no data';
    svg.appendChild(t);
  }

  // ─── Module 4b: shared crosshair + hover tooltip ────────────────────────
  // model = { w, x0, x1, y0, y1, cols:[{ px, xLabel, items:[{label,color,py,valText,dot}] }] }
  // The tooltip is an HTML div inside `root` (which is position:relative via CSS).
  // FAIL-SAFE: any error just disables hover — it never breaks the rendered chart.
  function attachHover(root, svg, model) {
    try {
      if (!root || !svg || !model || !model.cols || model.cols.length < 2) return;
      const cross = el('line', { class: 'chart-crosshair', x1: model.x0, x2: model.x0, y1: model.y0, y2: model.y1, stroke: COLORS.axis(), 'stroke-width': 1, 'stroke-dasharray': '3 3', opacity: 0 });
      svg.appendChild(cross);
      const dots = el('g', { opacity: 0 });
      svg.appendChild(dots);
      let tip = root.querySelector('.chart-tip');
      if (!tip) { tip = document.createElement('div'); tip.className = 'chart-tip'; root.appendChild(tip); }
      tip.hidden = true;
      const vbX = (clientX) => { const r = svg.getBoundingClientRect(); return r.width ? (clientX - r.left) / r.width * model.w : 0; };
      const leave = () => { cross.setAttribute('opacity', 0); dots.setAttribute('opacity', 0); tip.hidden = true; };
      function move(ev) {
        try {
          const vx = vbX(ev.clientX);
          if (vx < model.x0 - 3 || vx > model.x1 + 3) return leave();
          let bi = 0, bd = Infinity;
          for (let i = 0; i < model.cols.length; i++) { const d = Math.abs(model.cols[i].px - vx); if (d < bd) { bd = d; bi = i; } }
          const col = model.cols[bi];
          cross.setAttribute('x1', col.px); cross.setAttribute('x2', col.px); cross.setAttribute('opacity', 0.6);
          while (dots.firstChild) dots.removeChild(dots.firstChild);
          col.items.forEach((it) => { if (it.dot !== false && Number.isFinite(it.py)) dots.appendChild(el('circle', { cx: col.px, cy: it.py, r: 3, fill: it.color, stroke: 'var(--bg)', 'stroke-width': 1 })); });
          dots.setAttribute('opacity', 1);
          tip.innerHTML = '<div class="chart-tip-x">' + col.xLabel + '</div>'
            + col.items.map((it) => '<div class="chart-tip-row"><span class="sw" style="background:' + it.color + '"></span>' + it.label + '<b>' + it.valText + '</b></div>').join('');
          tip.hidden = false;
          const rr = root.getBoundingClientRect();
          const tw = tip.offsetWidth || 150;
          let left = ev.clientX - rr.left + 14;
          if (left + tw > rr.width - 4) left = ev.clientX - rr.left - tw - 14;
          tip.style.left = Math.max(4, left) + 'px';
        } catch (_) { leave(); }
      }
      svg.addEventListener('mousemove', move);
      svg.addEventListener('mouseleave', leave);
      svg.style.cursor = 'crosshair';
    } catch (_) { /* hover disabled, chart unaffected */ }
  }

  // ─── Sparkline: minimal axis-less trend line for KPI cards ──────────────
  function sparkline(root, values, opts = {}) {
    if (!root) return;
    const vals = (values || []).filter((v) => Number.isFinite(v));
    clear(root);
    if (vals.length < 2) return;
    const { svg, w, h } = makeSvg(root, { height: opts.height || 26 });
    const min = Math.min(...vals), max = Math.max(...vals), span = (max - min) || 1;
    const n = vals.length;
    const xAt = (i) => (i / (n - 1)) * w;
    const yAt = (v) => (h - 2) - ((v - min) / span) * (h - 4);
    const color = opts.color || COLORS.accent();
    if (opts.baseline === 0 && min < 0 && max > 0) {
      const y0 = yAt(0);
      svg.appendChild(el('line', { x1: 0, y1: y0, x2: w, y2: y0, stroke: COLORS.grid(), 'stroke-width': 1, 'stroke-dasharray': '2 3', opacity: 0.7 }));
    }
    const pts = vals.map((v, i) => `${xAt(i).toFixed(1)},${yAt(v).toFixed(1)}`).join(' ');
    svg.appendChild(el('polyline', { points: pts, fill: 'none', stroke: color, 'stroke-width': 1.4, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }));
    svg.appendChild(el('circle', { cx: xAt(n - 1).toFixed(1), cy: yAt(vals[n - 1]).toFixed(1), r: 1.9, fill: color }));
  }

  const Charts = { candles, lineChart, drawdownArea, histogram, rollingChart, fundingBars, xyChart, sparkline };

  if (typeof module !== 'undefined' && module.exports) module.exports = Charts;
  if (typeof global !== 'undefined') global.Charts = Charts;
})(typeof globalThis !== 'undefined' ? globalThis : this);
