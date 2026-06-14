// quant.js — vanilla-JS reimplementation of the btc-quant core.
//
// Mirrors the Python engine (features.py / backtest.py / risk.py / strategies.py)
// so the static dashboard is self-contained. Pure functions on plain Arrays of
// numbers. NO look-ahead anywhere: a signal computed at bar t trades bar t+1
// (the backtester shifts the position series by one bar before applying it).
// Costs + slippage are ON by default and charged on turnover = |Δposition|.
//
// Honesty rails reproduced from the Python side:
//   - every backtest reports NET and GROSS returns plus the buy-and-hold baseline
//   - the headline metric bundle includes a Deflated Sharpe Ratio (Bailey &
//     López de Prado 2014) computed against the expected max SR of N skill-less
//     trials, plus a naive in/out-of-sample split (the overfitting tell)
//
// No dependencies, no DOM access here — see charts.js / app.js for rendering.
'use strict';

(function (global) {
  // ─── Small numeric helpers ───────────────────────────────────────────

  /** Arithmetic mean of an array (NaN-safe: ignores non-finite entries). */
  function mean(xs) {
    let s = 0, n = 0;
    for (const x of xs) if (Number.isFinite(x)) { s += x; n++; }
    return n ? s / n : NaN;
  }

  /** Sample standard deviation (ddof=1), NaN-safe. */
  function std(xs, ddof = 1) {
    const fin = xs.filter(Number.isFinite);
    const n = fin.length;
    if (n <= ddof) return NaN;
    const m = mean(fin);
    let s = 0;
    for (const x of fin) s += (x - m) * (x - m);
    return Math.sqrt(s / (n - ddof));
  }

  /** Sample skewness (bias-corrected Fisher-Pearson g1 → adjusted). */
  function skewness(xs) {
    const fin = xs.filter(Number.isFinite);
    const n = fin.length;
    if (n < 3) return 0;
    const m = mean(fin);
    const sd = std(fin, 0); // population sd for the moment ratio
    if (!(sd > 0)) return 0;
    let s = 0;
    for (const x of fin) s += ((x - m) / sd) ** 3;
    const g1 = s / n;
    return (Math.sqrt(n * (n - 1)) / (n - 2)) * g1;
  }

  /** Sample excess kurtosis (Fisher). Returns the *non-excess* kurtosis when
   *  asked, but the DSR formula below uses the full kurtosis (excess + 3). */
  function kurtosis(xs, excess = true) {
    const fin = xs.filter(Number.isFinite);
    const n = fin.length;
    if (n < 4) return excess ? 0 : 3;
    const m = mean(fin);
    const sd = std(fin, 0);
    if (!(sd > 0)) return excess ? 0 : 3;
    let s = 0;
    for (const x of fin) s += ((x - m) / sd) ** 4;
    const g2 = s / n - 3;
    return excess ? g2 : g2 + 3;
  }

  // Standard normal CDF (Abramowitz & Stegun 7.1.26) and its inverse
  // (Acklam's rational approximation). Used for the (deflated) Sharpe.
  function normCdf(x) {
    return 0.5 * (1 + erf(x / Math.SQRT2));
  }
  function erf(x) {
    const sign = x < 0 ? -1 : 1;
    x = Math.abs(x);
    const t = 1 / (1 + 0.3275911 * x);
    const y = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-x * x);
    return sign * y;
  }
  function normPpf(p) {
    if (p <= 0) return -Infinity;
    if (p >= 1) return Infinity;
    const a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02, 1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00];
    const b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02, 6.680131188771972e+01, -1.328068155288572e+01];
    const c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00, -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00];
    const d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00];
    const plow = 0.02425, phigh = 1 - plow;
    let q, r;
    if (p < plow) {
      q = Math.sqrt(-2 * Math.log(p));
      return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) /
             ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1);
    } else if (p <= phigh) {
      q = p - 0.5; r = q * q;
      return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q /
             (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1);
    }
    q = Math.sqrt(-2 * Math.log(1 - p));
    return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) /
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1);
  }

  // ─── features.py mirror (pure functions on a close-price array) ───────

  /** Simple (arithmetic) returns. Element 0 is NaN (no prior bar). */
  function simpleReturns(close) {
    const out = [NaN];
    for (let i = 1; i < close.length; i++) out.push(close[i] / close[i - 1] - 1);
    return out;
  }

  /** Log returns. Element 0 is NaN. */
  function logReturns(close) {
    const out = [NaN];
    for (let i = 1; i < close.length; i++) out.push(Math.log(close[i] / close[i - 1]));
    return out;
  }

  /** Annualized rolling realized volatility of a returns series.
   *  Uses only the trailing `window` observations at each point (no look-ahead). */
  function realizedVol(returns, window = 20, periodsPerYear = 365) {
    const out = new Array(returns.length).fill(NaN);
    for (let i = 0; i < returns.length; i++) {
      if (i + 1 < window) continue;
      const w = returns.slice(i - window + 1, i + 1);
      out[i] = std(w, 1) * Math.sqrt(periodsPerYear);
    }
    return out;
  }

  /** Simple moving average over the trailing `n` bars. */
  function sma(s, n) {
    const out = new Array(s.length).fill(NaN);
    let acc = 0, count = 0;
    const buf = [];
    for (let i = 0; i < s.length; i++) {
      const v = s[i];
      buf.push(v);
      if (Number.isFinite(v)) { acc += v; count++; }
      if (buf.length > n) {
        const old = buf.shift();
        if (Number.isFinite(old)) { acc -= old; count--; }
      }
      if (buf.length === n && count === n) out[i] = acc / n;
    }
    return out;
  }

  /** Exponential moving average (span convention: alpha = 2/(n+1)). */
  function ema(s, n) {
    const out = new Array(s.length).fill(NaN);
    const alpha = 2 / (n + 1);
    let prev = NaN;
    for (let i = 0; i < s.length; i++) {
      const v = s[i];
      if (!Number.isFinite(v)) { out[i] = prev; continue; }
      prev = Number.isFinite(prev) ? alpha * v + (1 - alpha) * prev : v;
      out[i] = prev;
    }
    return out;
  }

  /** Total trailing return over `lookback` bars — the TSMOM signal input. */
  function momentum(close, lookback = 90) {
    const out = new Array(close.length).fill(NaN);
    for (let i = lookback; i < close.length; i++) out[i] = close[i] / close[i - lookback] - 1;
    return out;
  }

  /** Rolling z-score of a series over the trailing `window`. */
  function zscore(s, window = 30) {
    const out = new Array(s.length).fill(NaN);
    for (let i = 0; i < s.length; i++) {
      if (i + 1 < window) continue;
      const w = s.slice(i - window + 1, i + 1);
      const m = mean(w), sd = std(w, 1);
      out[i] = sd > 0 ? (s[i] - m) / sd : NaN;
    }
    return out;
  }

  /** Wilder's RSI over the trailing `window`. */
  function rsi(close, window = 14) {
    const out = new Array(close.length).fill(NaN);
    let avgGain = NaN, avgLoss = NaN;
    for (let i = 1; i < close.length; i++) {
      const diff = close[i] - close[i - 1];
      const gain = Math.max(diff, 0), loss = Math.max(-diff, 0);
      if (i <= window) {
        avgGain = Number.isFinite(avgGain) ? avgGain + gain : gain;
        avgLoss = Number.isFinite(avgLoss) ? avgLoss + loss : loss;
        if (i === window) {
          avgGain /= window; avgLoss /= window;
          out[i] = 100 - 100 / (1 + (avgLoss === 0 ? Infinity : avgGain / avgLoss));
        }
      } else {
        avgGain = (avgGain * (window - 1) + gain) / window;
        avgLoss = (avgLoss * (window - 1) + loss) / window;
        out[i] = 100 - 100 / (1 + (avgLoss === 0 ? Infinity : avgGain / avgLoss));
      }
    }
    return out;
  }

  /** Equity → drawdown series (fraction below running peak, ≤ 0). */
  function drawdownSeries(equity) {
    const out = new Array(equity.length).fill(0);
    let peak = -Infinity;
    for (let i = 0; i < equity.length; i++) {
      if (equity[i] > peak) peak = equity[i];
      out[i] = peak > 0 ? equity[i] / peak - 1 : 0;
    }
    return out;
  }

  /** Maximum drawdown (most-negative value of drawdownSeries). */
  function maxDrawdown(equity) {
    return Math.min(0, ...drawdownSeries(equity));
  }

  /** Annualized Sharpe of a returns series (rf assumed 0). */
  function sharpe(returns, periodsPerYear = 365) {
    const fin = returns.filter(Number.isFinite);
    const sd = std(fin, 1);
    if (!(sd > 0)) return 0;
    return (mean(fin) / sd) * Math.sqrt(periodsPerYear);
  }

  /** Rolling annualized Sharpe over the trailing `window`. */
  function rollingSharpe(returns, window = 90, periodsPerYear = 365) {
    const out = new Array(returns.length).fill(NaN);
    for (let i = 0; i < returns.length; i++) {
      if (i + 1 < window) continue;
      out[i] = sharpe(returns.slice(i - window + 1, i + 1), periodsPerYear);
    }
    return out;
  }

  /** Sortino ratio (downside-deviation denominator). */
  function sortino(returns, periodsPerYear = 365) {
    const fin = returns.filter(Number.isFinite);
    const m = mean(fin);
    let s = 0, n = 0;
    for (const x of fin) { if (x < 0) { s += x * x; n++; } }
    const dd = n ? Math.sqrt(s / n) : 0;
    if (!(dd > 0)) return 0;
    return (m / dd) * Math.sqrt(periodsPerYear);
  }

  /** CAGR implied by an equity curve over its observation count. */
  function cagr(equity, periodsPerYear = 365) {
    if (equity.length < 2) return 0;
    const years = (equity.length - 1) / periodsPerYear;
    if (years <= 0 || equity[0] <= 0) return 0;
    return (equity[equity.length - 1] / equity[0]) ** (1 / years) - 1;
  }

  /** Fraction of finite returns that are strictly positive. */
  function hitRate(returns) {
    let pos = 0, n = 0;
    for (const r of returns) if (Number.isFinite(r)) { n++; if (r > 0) pos++; }
    return n ? pos / n : 0;
  }

  // ─── risk.py mirror: probabilistic & deflated Sharpe ──────────────────

  /**
   * Probabilistic Sharpe Ratio — Bailey & López de Prado (2012).
   * Probability that the true SR exceeds `srBenchmark`, accounting for sample
   * length and the non-normality (skew, kurtosis) of the return stream.
   * @param {number} sr observed (per-period) Sharpe
   * @param {number} n  number of observations
   * @param {number} skew sample skewness
   * @param {number} kurt sample (non-excess) kurtosis
   */
  function probabilisticSharpe(sr, n, skew, kurt, srBenchmark = 0) {
    if (n < 2) return NaN;
    const denom = Math.sqrt(1 - skew * sr + ((kurt - 1) / 4) * sr * sr);
    if (!(denom > 0)) return NaN;
    return normCdf(((sr - srBenchmark) * Math.sqrt(n - 1)) / denom);
  }

  /**
   * Deflated Sharpe Ratio — Bailey & López de Prado (2014).
   * Benchmarks the observed SR against the *expected maximum* SR of `nTrials`
   * skill-less strategies, then runs PSR against that inflated benchmark.
   * This is the headline honesty metric: significance when DSR > 0.95.
   * @param {number} sr observed per-period Sharpe (NOT annualized)
   * @param {number} n number of observations
   * @param {number} nTrials number of independent configurations tried
   * @param {number} varTrialsSr variance of the SR across those trials
   */
  function deflatedSharpe(sr, n, skew, kurt, nTrials = 1, varTrialsSr = 1) {
    const N = Math.max(1, nTrials);
    const sigma = Math.sqrt(Math.max(varTrialsSr, 1e-12));
    const gamma = 0.5772156649; // Euler–Mascheroni
    const e = Math.E;
    // Expected max of N standard normals (Gumbel approximation).
    const sr0 = sigma * ((1 - gamma) * normPpf(1 - 1 / N) + gamma * normPpf(1 - 1 / (N * e)));
    return probabilisticSharpe(sr, n, skew, kurt, sr0);
  }

  // ─── backtest.py mirror ───────────────────────────────────────────────

  /**
   * Vectorized single-asset backtest.
   *
   * @param {number[]} positions target weight per bar in [-1,1] (or [0,1]).
   *        SHIFTED INTERNALLY BY ONE BAR so a signal at t trades the t→t+1
   *        return — there is no look-ahead.
   * @param {number[]} prices close prices aligned to `positions`.
   * @param {object} opts { costBps, slippageBps, periodsPerYear, nTrials,
   *        varTrialsSr }.
   * @returns {object} { equity, returns, grossReturns, position, turnover,
   *        trades, bhEquity, bhReturns, stats } where stats includes the
   *        full-history deflated Sharpe (the walk-forward OOS headline lives in
   *        walkForward()).
   */
  function backtest(positions, prices, opts = {}) {
    const costBps = opts.costBps != null ? opts.costBps : 10;
    const slipBps = opts.slippageBps != null ? opts.slippageBps : 2;
    const ppy = opts.periodsPerYear || 365;
    const nTrials = opts.nTrials || 1;
    const varTrialsSr = opts.varTrialsSr != null ? opts.varTrialsSr : 1;
    const costRate = (costBps + slipBps) / 1e4; // charged per unit turnover

    const n = Math.min(positions.length, prices.length);
    const assetRet = simpleReturns(prices.slice(0, n));

    // Shift the signal by one bar: position effective at bar i is the signal
    // decided at bar i-1. position[0] is flat (nothing decided before bar 0).
    const pos = new Array(n).fill(0);
    for (let i = 1; i < n; i++) {
      const p = positions[i - 1];
      pos[i] = Number.isFinite(p) ? p : 0;
    }

    const grossReturns = new Array(n).fill(0);
    const returns = new Array(n).fill(0);
    const turnover = new Array(n).fill(0);
    let trades = 0;
    let prevPos = 0;
    for (let i = 0; i < n; i++) {
      const r = Number.isFinite(assetRet[i]) ? assetRet[i] : 0;
      const to = Math.abs(pos[i] - prevPos);
      turnover[i] = to;
      if (to > 1e-9) trades++;
      grossReturns[i] = pos[i] * r;
      returns[i] = pos[i] * r - to * costRate;
      prevPos = pos[i];
    }

    const equity = compound(returns);
    const bhReturns = assetRet.map((r) => (Number.isFinite(r) ? r : 0));
    const bhEquity = compound(bhReturns);

    const stats = computeStats(returns, equity, {
      periodsPerYear: ppy, nTrials, varTrialsSr,
      bhReturns, turnover,
    });

    return { equity, returns, grossReturns, position: pos, turnover, trades, bhEquity, bhReturns, stats };
  }

  /** Compound a per-bar returns series into an equity curve starting at 1.0. */
  function compound(returns) {
    const out = new Array(returns.length);
    let eq = 1;
    for (let i = 0; i < returns.length; i++) {
      eq *= 1 + (Number.isFinite(returns[i]) ? returns[i] : 0);
      out[i] = eq;
    }
    return out;
  }

  /** Bundle the headline stats for a returns/equity pair (mirrors risk.summary). */
  function computeStats(returns, equity, opts = {}) {
    const ppy = opts.periodsPerYear || 365;
    const fin = returns.filter(Number.isFinite);
    const n = fin.length;
    const perPeriodSharpe = (function () {
      const sd = std(fin, 1);
      return sd > 0 ? mean(fin) / sd : 0;
    })();
    const sk = skewness(fin);
    const ku = kurtosis(fin, false); // non-excess for the PSR/DSR formula
    const grossEq = equity;

    return {
      n,
      cagr: cagr(grossEq, ppy),
      annReturn: mean(fin) * ppy,
      volatility: std(fin, 1) * Math.sqrt(ppy),
      sharpe: sharpe(returns, ppy),
      sortino: sortino(returns, ppy),
      maxDrawdown: maxDrawdown(grossEq),
      calmar: (function () {
        const mdd = Math.abs(maxDrawdown(grossEq));
        return mdd > 0 ? cagr(grossEq, ppy) / mdd : 0;
      })(),
      hitRate: hitRate(returns),
      skew: sk,
      kurtosis: ku,
      turnover: opts.turnover ? opts.turnover.reduce((a, b) => a + b, 0) : NaN,
      bhSharpe: opts.bhReturns ? sharpe(opts.bhReturns, ppy) : NaN,
      bhCagr: opts.bhReturns ? cagr(compound(opts.bhReturns), ppy) : NaN,
      bhMaxDrawdown: opts.bhReturns ? maxDrawdown(compound(opts.bhReturns)) : NaN,
      probabilisticSharpe: probabilisticSharpe(perPeriodSharpe, n, sk, ku, 0),
      deflatedSharpe: deflatedSharpe(perPeriodSharpe, n, sk, ku, opts.nTrials || 1, opts.varTrialsSr != null ? opts.varTrialsSr : 1),
      nTrials: opts.nTrials || 1,
    };
  }

  // ─── strategies.py mirror: target-position builders ───────────────────
  // Each returns a positions array (length = prices.length) in [0,1] or [-1,1].
  // Positions are NOT pre-shifted — backtest() does the shift-by-one.

  /** Buy-and-hold baseline: always fully long. */
  function sigBuyAndHold(close) {
    return new Array(close.length).fill(1);
  }

  /**
   * 200d MA trend filter: long when price > SMA(n), else flat.
   * Risk management, not alpha [Practitioner] (Grayscale; Glucksmann 2019).
   */
  function sigMaTrend(close, n = 200) {
    const ma = sma(close, n);
    return close.map((c, i) => (Number.isFinite(ma[i]) && c > ma[i] ? 1 : 0));
  }

  /**
   * Dual moving-average cross (golden cross): long when SMA_fast > SMA_slow.
   * [Practitioner].
   */
  function sigMaCross(close, fast = 50, slow = 200) {
    const f = sma(close, fast), s = sma(close, slow);
    return close.map((_, i) =>
      Number.isFinite(f[i]) && Number.isFinite(s[i]) && f[i] > s[i] ? 1 : 0);
  }

  /**
   * Short-lookback time-series momentum, optionally vol-scaled.
   * position = sign(trailing return); vol-scaled multiplies by
   * target_vol / sigma_t (capped). [Mixed], cost-fragile (~3–10 bps breakeven).
   * Shen, Urquhart & Wang (2022). longOnly clamps shorts to flat.
   */
  function sigTsmom(close, lookback = 20, opts = {}) {
    const volScaled = opts.volScaled !== false;
    const longOnly = !!opts.longOnly;
    const targetVol = opts.targetVol != null ? opts.targetVol : 0.15;
    const volWindow = opts.volWindow || 20;
    const cap = opts.cap != null ? opts.cap : 1;
    const ppy = opts.periodsPerYear || 365;
    const mom = momentum(close, lookback);
    const rets = simpleReturns(close);
    const sigma = realizedVol(rets, volWindow, ppy);
    return close.map((_, i) => {
      const m = mom[i];
      if (!Number.isFinite(m)) return 0;
      let sign = m > 0 ? 1 : (longOnly ? 0 : -1);
      if (!volScaled) return Math.max(-cap, Math.min(cap, sign));
      const s = sigma[i];
      const scale = Number.isFinite(s) && s > 0 ? targetVol / s : 0;
      return Math.max(-cap, Math.min(cap, sign * scale));
    });
  }

  /**
   * Volatility-targeting sizing layer wrapping any positions series.
   * w_t = base_t * (target_vol / sigma_t), leverage-capped. [Mixed; tail control].
   * Harvey et al. (2018). sigma_t uses only trailing returns (no look-ahead).
   */
  function applyVolTarget(positions, close, opts = {}) {
    const targetVol = opts.targetVol != null ? opts.targetVol : 0.15;
    const volWindow = opts.volWindow || 20;
    const cap = opts.cap != null ? opts.cap : 3;
    const ppy = opts.periodsPerYear || 365;
    const rets = simpleReturns(close);
    const sigma = realizedVol(rets, volWindow, ppy);
    return positions.map((p, i) => {
      if (!Number.isFinite(p) || p === 0) return 0;
      const s = sigma[i];
      const scale = Number.isFinite(s) && s > 0 ? targetVol / s : 0;
      const w = p * scale;
      return Math.max(-cap, Math.min(cap, w));
    });
  }

  /**
   * BTC–ETH cointegration / z-score spread reversion. [Mixed].
   * spread = log(btc) - beta*log(eth) with a rolling OLS beta; enter when
   * |z| > entry (fade), exit near 0, hard stop at |z| > stop (the
   * de-cointegration guard). Returns BTC-leg positions in [-1,1].
   * Tadi & Witzany (2024); Krauss (2017) documents OOS decay.
   */
  function sigPairs(btc, eth, opts = {}) {
    const window = opts.window || 60;
    const entry = opts.entry != null ? opts.entry : 2.0;
    const exit = opts.exit != null ? opts.exit : 0.5;
    const stop = opts.stop != null ? opts.stop : 3.5;
    const n = Math.min(btc.length, eth.length);
    const lb = btc.slice(0, n).map(Math.log);
    const le = eth.slice(0, n).map(Math.log);
    const z = new Array(n).fill(NaN);
    for (let i = 0; i < n; i++) {
      if (i + 1 < window) continue;
      // Rolling OLS beta of log(btc) on log(eth) over the trailing window.
      const xs = le.slice(i - window + 1, i + 1);
      const ys = lb.slice(i - window + 1, i + 1);
      const mx = mean(xs), my = mean(ys);
      let cov = 0, vx = 0;
      for (let k = 0; k < xs.length; k++) { cov += (xs[k] - mx) * (ys[k] - my); vx += (xs[k] - mx) ** 2; }
      const beta = vx > 0 ? cov / vx : 0;
      const spread = xs.map((x, k) => ys[k] - beta * x);
      const ms = mean(spread), ss = std(spread, 1);
      z[i] = ss > 0 ? (spread[spread.length - 1] - ms) / ss : NaN;
    }
    // Stateful trade logic: fade extremes, exit near mean, stop on breakdown.
    const pos = new Array(n).fill(0);
    let cur = 0;
    for (let i = 0; i < n; i++) {
      const zi = z[i];
      if (!Number.isFinite(zi)) { pos[i] = cur; continue; }
      if (cur !== 0 && Math.abs(zi) > stop) cur = 0;            // de-cointegration guard
      else if (cur === 0 && zi > entry) cur = -1;                // spread rich → short BTC leg
      else if (cur === 0 && zi < -entry) cur = 1;                // spread cheap → long BTC leg
      else if (cur !== 0 && Math.abs(zi) < exit) cur = 0;        // reverted → flat
      pos[i] = cur;
    }
    return { positions: pos, z };
  }

  /**
   * Carry (long spot / short perp funding harvest), modelled on the realized
   * funding stream rather than price. [Established, decaying]. Returns a
   * per-funding-interval net yield series and a cumulative equity curve so the
   * dashboard can show the 2021→2025 decay and negative-funding inversion.
   * Schmeling, Schrimpf & Todorov (2023); He et al. (2024).
   *
   * @param {number[]} fundingRates per-interval funding (longs pay shorts when +)
   * @param {object} opts { costBps per rebalance, threshold to be in-position }
   */
  function carryBacktest(fundingRates, opts = {}) {
    const costBps = opts.costBps != null ? opts.costBps : 4; // per entry/exit, delta-neutral
    const threshold = opts.threshold != null ? opts.threshold : 0; // only hold when funding > threshold
    const costRate = costBps / 1e4;
    const n = fundingRates.length;
    const net = new Array(n).fill(0);
    let inPos = false;
    for (let i = 0; i < n; i++) {
      const f = Number.isFinite(fundingRates[i]) ? fundingRates[i] : 0;
      const want = f > threshold; // short perp earns funding when funding positive
      let pnl = want ? f : 0;     // delta-neutral: collect funding (negative when inverted & still in)
      if (want !== inPos) pnl -= costRate; // rebalance cost on entry/exit
      net[i] = pnl;
      inPos = want;
    }
    const equity = compound(net);
    return { net, equity };
  }

  // ─── Public API ────────────────────────────────────────────────────────

  // ─── OOS validation harness (mirrors btcquant/{backtest,risk}.py) ──────────

  /**
   * Anchored walk-forward. Split the series into `folds`+1 contiguous blocks; for
   * each fold trade the *next* out-of-sample block with positions decided as data
   * arrived (the strategies are causal). Returns the concatenated OOS returns +
   * stats; the OOS Deflated Sharpe is deflated for `nTrials` with the skill-less
   * Sharpe variance 1/n — matching the Python engine (backtest.walk_forward).
   * @param {number[]} positions full-history target weights
   * @param {number[]} prices    aligned close series
   * @param {object} opts { folds, periodsPerYear, costBps, slippageBps, nTrials }
   */
  function walkForward(positions, prices, opts = {}) {
    const folds = opts.folds || 5;
    const ppy = opts.periodsPerYear || 365;
    const n = Math.min(positions.length, prices.length);
    const out = { oosReturns: [], oosStats: null };
    if (n < (folds + 1) * 2) return out;
    const edge = (i) => Math.floor((i * n) / (folds + 1));
    const oos = [];
    for (let k = 1; k <= folds; k++) {
      const a = edge(k), b = edge(k + 1);
      if (b <= a) continue;
      const bt = backtest(positions.slice(a, b), prices.slice(a, b),
        { costBps: opts.costBps, slippageBps: opts.slippageBps, periodsPerYear: ppy });
      for (let i = 0; i < bt.returns.length; i++) oos.push(bt.returns[i]);
    }
    if (!oos.length) return out;
    const nOos = oos.filter(Number.isFinite).length;
    out.oosReturns = oos;
    out.oosStats = computeStats(oos, compound(oos), {
      periodsPerYear: ppy,
      nTrials: opts.nTrials || 1,
      varTrialsSr: nOos > 0 ? 1 / nOos : 1,   // Python-parity (skill-less Sharpe variance ≈ 1/n)
    });
    return out;
  }

  /**
   * Probability of Backtest Overfitting (PBO) via CSCV — Bailey-Borwein-LdP-Zhu (2017).
   * `matrix` = array of per-strategy return arrays (columns), positionally aligned.
   * Over every C(S, S/2) in-sample/out-of-sample block split, pick the IS-best column
   * and check whether it lands below the OOS median; PBO = fraction where it does.
   */
  function pbo(matrix, opts = {}) {
    const N = matrix.length;
    const out = { pbo: NaN, nCombos: 0, nStrategies: N };
    if (N < 2) return out;
    const T = Math.min.apply(null, matrix.map((c) => c.length));
    if (!(T >= 8)) return out;
    let S = opts.nBlocks || 8; if (S % 2) S -= 1; S = Math.max(2, Math.min(S, T));
    const edges = []; for (let i = 0; i <= S; i++) edges.push(Math.floor((i * T) / S));
    const blocks = [];
    for (let i = 0; i < S; i++) { const a = []; for (let j = edges[i]; j < edges[i + 1]; j++) a.push(j); if (a.length) blocks.push(a); }
    S = blocks.length; if (S < 2) return out;
    const half = Math.floor(S / 2);
    const blkSharpe = (idx, col) => {
      const r = []; for (let k = 0; k < idx.length; k++) { const v = matrix[col][idx[k]]; if (Number.isFinite(v)) r.push(v); }
      if (r.length < 2) return 0;
      const sd = std(r, 1); return sd > 0 ? mean(r) / sd : 0;
    };
    const combos = [];
    (function choose(start, picked) {
      if (picked.length === half) { combos.push(picked.slice()); return; }
      for (let i = start; i < S; i++) { picked.push(i); choose(i + 1, picked); picked.pop(); }
    })(0, []);
    let below = 0;
    for (let ci = 0; ci < combos.length; ci++) {
      const isSet = new Set(combos[ci]);
      const isIdx = [], oosIdx = [];
      for (let i = 0; i < S; i++) { const tgt = isSet.has(i) ? isIdx : oosIdx; for (let j = 0; j < blocks[i].length; j++) tgt.push(blocks[i][j]); }
      let best = 0, bestSr = -Infinity;
      for (let c = 0; c < N; c++) { const s = blkSharpe(isIdx, c); if (s > bestSr) { bestSr = s; best = c; } }
      const oosBest = blkSharpe(oosIdx, best);
      let beats = 0; for (let c = 0; c < N; c++) if (oosBest > blkSharpe(oosIdx, c)) beats++;
      if (beats / N < 0.5) below++;
    }
    out.nCombos = combos.length;
    out.pbo = combos.length ? below / combos.length : NaN;
    return out;
  }

  /** Minimum Backtest Length (years) — Bailey et al. 2014; brief §3 form 2·ln(N)/E[max_N]. */
  function minBacktestLength(nTrials) {
    if (!(nTrials >= 2)) return NaN;
    const gamma = 0.5772156649015329;
    const z1 = normPpf(1 - 1 / nTrials), z2 = normPpf(1 - 1 / (nTrials * Math.E));
    const emax = (1 - gamma) * z1 + gamma * z2;
    return emax > 0 ? (2 * Math.log(nTrials)) / emax : NaN;
  }

  /**
   * Combinatorial Purged CV — the distribution of OOS Sharpe across time-block
   * subsets (mirrors backtest.cpcv). Returns { paths, nPaths, median, p25, p75, iqr }.
   */
  function cpcv(positions, prices, opts = {}) {
    const nBlocks = opts.nBlocks || 6, kTest = opts.kTest || 2;
    const ppy = opts.periodsPerYear || 365, embargo = opts.embargo != null ? opts.embargo : 0.01;
    const n = Math.min(positions.length, prices.length);
    const out = { paths: [], nPaths: 0, median: NaN, p25: NaN, p75: NaN, iqr: NaN, min: NaN, max: NaN };
    if (nBlocks < 2 || kTest < 1 || kTest >= nBlocks || n < (nBlocks + 1) * 2) return out;
    const bt = backtest(positions, prices, { costBps: opts.costBps, slippageBps: opts.slippageBps, periodsPerYear: ppy });
    const rets = bt.returns;
    const edges = []; for (let i = 0; i <= nBlocks; i++) edges.push(Math.floor((i * n) / nBlocks));
    const blocks = [];
    for (let i = 0; i < nBlocks; i++) { const a = []; for (let j = edges[i]; j < edges[i + 1]; j++) a.push(j); if (a.length) blocks.push(a); }
    const B = blocks.length; if (B < 2) return out;
    const combos = [];
    (function choose(start, picked) {
      if (picked.length === kTest) { combos.push(picked.slice()); return; }
      for (let i = start; i < B; i++) { picked.push(i); choose(i + 1, picked); picked.pop(); }
    })(0, []);
    const paths = [];
    for (let ci = 0; ci < combos.length; ci++) {
      const idx = [];
      for (const bi of combos[ci]) {
        const blk = blocks[bi];
        const drop = blk.length > 1 ? Math.min(blk.length - 1, Math.max(1, Math.round(embargo * blk.length))) : 0;
        for (let j = drop; j < blk.length; j++) idx.push(blk[j]);
      }
      if (idx.length < 2) continue;
      paths.push(sharpe(idx.map((j) => rets[j]), ppy));
    }
    const fin = paths.filter(Number.isFinite).slice().sort((a, b) => a - b);
    if (!fin.length) return out;
    const q = (pp) => { const i = (fin.length - 1) * pp, lo = Math.floor(i), hi = Math.ceil(i); return lo === hi ? fin[lo] : fin[lo] + (fin[hi] - fin[lo]) * (i - lo); };
    out.paths = paths; out.nPaths = fin.length;
    out.median = q(0.5); out.p25 = q(0.25); out.p75 = q(0.75); out.iqr = out.p75 - out.p25;
    out.min = fin[0]; out.max = fin[fin.length - 1];
    return out;
  }

  const Quant = {
    // numeric
    mean, std, skewness, kurtosis, normCdf, normPpf,
    // features
    simpleReturns, logReturns, realizedVol, sma, ema, momentum, zscore, rsi,
    drawdownSeries, maxDrawdown, sharpe, rollingSharpe, sortino, cagr, hitRate,
    // risk
    probabilisticSharpe, deflatedSharpe,
    // backtest
    backtest, compound, computeStats,
    // OOS validation harness
    walkForward, pbo, minBacktestLength, cpcv,
    // strategy signals
    sigBuyAndHold, sigMaTrend, sigMaCross, sigTsmom, applyVolTarget, sigPairs,
    carryBacktest,
  };

  if (typeof module !== 'undefined' && module.exports) module.exports = Quant;
  if (typeof global !== 'undefined') global.Quant = Quant;
})(typeof globalThis !== 'undefined' ? globalThis : this);
