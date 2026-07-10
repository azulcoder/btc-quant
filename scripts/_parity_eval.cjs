'use strict';
// _parity_eval.cjs — the JS half of the JS<->Python parity check.
//
// Loads the REQUIREABLE dashboard mirror (dashboard/quant.js) and evaluates every
// shared formula on the fixed fixture passed as argv[2] (a JSON file written by
// scripts/check_parity.py). Emits one flat JSON object of named scalars to stdout;
// the Python side recomputes the same names and asserts agreement within documented
// tolerances. This file has NO analytics of its own — it only calls Q.* so that any
// drift it reports is a true Python<->JS mirror divergence, never a third source.

const fs = require('fs');
const path = require('path');
const Q = require(path.join(__dirname, '..', 'dashboard', 'quant.js'));

const fx = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const { close, positions, ppy, volWindow, k, sr, n, skew, kurt, nTrials, varTrialsSr,
        varN1, srMid, nMid, nTrialsMid, varMid, folds,
        costBps, slipBps, fwd, strike, iv, t } = fx;

const last = (a) => a[a.length - 1];
const ret = Q.simpleReturns(close);
const retClean = ret.filter(Number.isFinite);
const eq = Q.compound(ret.map((x) => (Number.isFinite(x) ? x : 0)));
const vol = Q.realizedVol(ret, volWindow, ppy);

const er = Q.expectancyReport(positions, close, vol, ppy, k);
const g = Q.black76Greeks(fwd, strike, iv, t, 'C', 0);
const bt = Q.backtest(positions, close, {
  costBps, slippageBps: slipBps, periodsPerYear: ppy, nTrials, varTrialsSr,
});
// Walk-forward fold-V probe (M6 C2/C3): JS walkForward vs Python backtest.walk_forward
// on the same fixture series — empirical ddof=1 fold-SR variance, folds-deflated DSR.
const wf = Q.walkForward(positions, close, {
  folds, costBps, slippageBps: slipBps, periodsPerYear: ppy,
});

const out = {
  // numeric
  mean: Q.mean(retClean),
  std: Q.std(retClean, 1),
  skewness: Q.skewness(retClean),
  kurtosis: Q.kurtosis(retClean, true),
  normCdf: Q.normCdf(0.7),
  normPpf: Q.normPpf(0.975),
  normPdf: Q.normPdf(0.3),
  // features (last element of each series)
  simpleRet_last: last(ret),
  logRet_last: last(Q.logReturns(close)),
  realizedVol_last: last(vol),
  sma_last: last(Q.sma(close, 10)),
  ema_last: last(Q.ema(close, 10)),
  momentum_last: last(Q.momentum(close, 30)),
  zscore_last: last(Q.zscore(close, 30)),
  rsi_last: last(Q.rsi(close, 14)),
  maxDrawdown: Q.maxDrawdown(eq),
  // risk
  sharpe: Q.sharpe(ret, ppy),
  sortino: Q.sortino(ret, ppy),
  cagr: Q.cagr(eq, ppy),
  hitRate: Q.hitRate(ret),
  psr: Q.probabilisticSharpe(sr, n, skew, kurt, 0),
  dsr: Q.deflatedSharpe(sr, n, skew, kurt, nTrials, varTrialsSr),
  // M6 unsaturated DSR pins: N=1 special case (≡ PSR(0), any variance) + mid-range point
  dsr_n1: Q.deflatedSharpe(sr, n, skew, kurt, 1, varN1),
  dsr_mid: Q.deflatedSharpe(srMid, nMid, skew, kurt, nTrialsMid, varMid),
  minBTL: Q.minBacktestLength(nTrials),
  // Tharp eval layer (camelCase -> snake_case mapped on the Python side)
  er_nTrades: er.nTrades,
  er_expectancyR: er.expectancyR,
  er_winRate: er.winRate,
  er_payoffRatio: er.payoffRatio,
  er_sqn: er.sqn,
  er_profitFactor: er.profitFactor,
  // options structural
  b76_delta: g.delta,
  b76_gamma: g.gamma,
  b76_vega: g.vega,
  // end-to-end backtest stats (engine parity)
  bt_sharpe: bt.stats.sharpe,
  bt_maxDrawdown: bt.stats.maxDrawdown,
  bt_deflatedSharpe: bt.stats.deflatedSharpe,
  // walk-forward fold-V (M6 C2/C3) — mirrors wf["oos"] on the Python side
  wf_oosSharpe: wf.oosStats ? wf.oosStats.sharpe : NaN,
  wf_varTrialsSr: wf.oosStats ? wf.oosStats.varTrialsSr : NaN,
  wf_deflatedSharpe: wf.oosStats ? wf.oosStats.deflatedSharpe : NaN,
  wf_varFallback: wf.oosStats ? !!wf.oosStats.varFallback : null,
};

process.stdout.write(JSON.stringify(out));
