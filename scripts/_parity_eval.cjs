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
        varN1, srMid, nMid, nTrialsMid, varMid, folds, neffCols,
        cpcvBlocks, cpcvKTest, cpcvPurge, cpcvEmbargo,
        costBps, slipBps, fwd, strike, iv, t,
        btcPairs, ethPairs, pairsWindow,
        optChain, optT } = fx;

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
  folds, costBps, slippageBps: slipBps, periodsPerYear: ppy, purge: 0, embargo: 0,
});
// M4 CPCV probe: default (legacy embargoPct trim) + an int purge/embargo edge-trim run.
const cp = Q.cpcv(positions, close, {
  nBlocks: cpcvBlocks, kTest: cpcvKTest, costBps, slippageBps: slipBps, periodsPerYear: ppy });
const cpPe = Q.cpcv(positions, close, {
  nBlocks: cpcvBlocks, kTest: cpcvKTest, costBps, slippageBps: slipBps, periodsPerYear: ppy,
  purge: cpcvPurge, embargo: cpcvEmbargo });
// M2 pairs two-leg-cost probe: sigPairs exposes beta; ETH-leg turnover |Δ(beta·state)|
// is fed to backtest as extraCostTurnover — mirror of strategies.pairs_legs + run().
const pr = Q.sigPairs(btcPairs, ethPairs, { window: pairsWindow, entry: 2, exit: 0.5, stop: 4.0, maxHalfLife: 60 });
const ethNotional = pr.positions.map((s, i) =>
  (Number.isFinite(s) && Number.isFinite(pr.beta[i]) ? s * pr.beta[i] : NaN));
const ethTurn = ethNotional.map((v, i) => (i === 0 ? NaN : Math.abs(v - ethNotional[i - 1])));
const prNone = Q.backtest(pr.positions, btcPairs, { costBps, slippageBps: slipBps, periodsPerYear: ppy });
const prExt = Q.backtest(pr.positions, btcPairs, { costBps, slippageBps: slipBps, periodsPerYear: ppy, extraCostTurnover: ethTurn });
// M9 delta-neutral P&L: gross = state·(btc_ret - beta_{t-1}·eth_ret), two-leg cost —
// mirror of the Python pairs_legs + run(hedge_return=beta.shift(1)·eth_ret) probe.
const ethRetP = Q.simpleReturns(ethPairs);
const hedgeRetP = pr.beta.map((b, i) =>
  (i > 0 && Number.isFinite(pr.beta[i - 1]) && Number.isFinite(ethRetP[i]) ? pr.beta[i - 1] * ethRetP[i] : NaN));
const prDn = Q.backtest(pr.positions, btcPairs, { costBps, slippageBps: slipBps, periodsPerYear: ppy, extraCostTurnover: ethTurn, hedgeReturn: hedgeRetP });
// M8 options-parity probe: max_pain + gamma_concentration on the fixed synthetic chain
// — mirror of features.max_pain / features.gamma_concentration. The chain rows carry the
// option feed's column names; map to the mirror's {strike,type,oi,iv,underlying} slice.
const optSlice = optChain.map((r) => ({
  strike: r.strike, type: r.opt_type, oi: r.open_interest, iv: r.iv, underlying: r.underlying_price }));
const mp = Q.maxPain(optSlice);
const gc = Q.gammaConcentration(optSlice, mp.forward, optT);  // fwd from the same slice, T = 30/365
const gcSum = gc.gammaOi.reduce((a, v) => a + v, 0);
const gcDot = gc.strikes.reduce((a, kk, i) => a + kk * gc.gammaOi[i], 0);
let gcPeak = 0;
for (let i = 1; i < gc.gammaOi.length; i++) if (gc.gammaOi[i] > gc.gammaOi[gcPeak]) gcPeak = i;

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
  // FST (False Strategy Theorem, Bailey-LdP 2014) — mirror of risk.expected_max_sharpe_ratio /
  // false_strategy_threshold / effective_number_of_trials / probability_false_strategy.
  emaxN5: Q.expectedMaxSharpeRatio(5, 1),
  emaxN10: Q.expectedMaxSharpeRatio(10, 1),
  fstThreshold: Q.falseStrategyThreshold(nTrialsMid, varMid, nMid, skew, kurt, 0.95),
  neffTrials: Q.effectiveNumberOfTrials(neffCols),  // columns (2 identical + 2 independent)
  probFalseStrategy: Q.probabilityFalseStrategy(srMid, nTrialsMid, varMid, nMid, skew, kurt),
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
  // M8 options analytics — max_pain + gamma_concentration (mirror of features.*)
  mp_maxPain: mp.maxPain,
  mp_pcOiRatio: mp.pcRatio,
  mp_forward: mp.forward,
  gc_sum: gcSum,
  gc_dot: gcDot,
  gc_peakStrike: gc.strikes[gcPeak],
  // end-to-end backtest stats (engine parity)
  bt_sharpe: bt.stats.sharpe,
  bt_maxDrawdown: bt.stats.maxDrawdown,
  bt_deflatedSharpe: bt.stats.deflatedSharpe,
  // walk-forward fold-V (M6 C2/C3) — mirrors wf["oos"] on the Python side
  wf_oosSharpe: wf.oosStats ? wf.oosStats.sharpe : NaN,
  wf_varTrialsSr: wf.oosStats ? wf.oosStats.varTrialsSr : NaN,
  wf_deflatedSharpe: wf.oosStats ? wf.oosStats.deflatedSharpe : NaN,
  wf_varFallback: wf.oosStats ? !!wf.oosStats.varFallback : null,
  // M4 CPCV multi-path dispersion — mirror of backtest.cpcv (median_sharpe etc.)
  cpcv_nPaths: cp.nPaths,
  cpcv_median: cp.median,
  cpcv_p25: cp.p25,
  cpcv_p75: cp.p75,
  cpcv_iqr: cp.iqr,
  cpcv_min: cp.min,
  cpcv_max: cp.max,
  cpcv_pe_median: cpPe.median,
  cpcv_pe_nPaths: cpPe.nPaths,
  // M2 pairs two-leg cost — mirror of the Python pairs_legs + run() probe
  pairs_beta_last: pr.beta[pr.beta.length - 1],
  pairs_ethTurnover: ethTurn.reduce((a, v) => a + (Number.isFinite(v) ? v : 0), 0),
  pairs_btcTurnover: prNone.stats.turnover,
  pairs_totalTurnover: prExt.stats.turnover,
  pairs_netEquity: prExt.equity[prExt.equity.length - 1],
  // M9 delta-neutral P&L (spread return, two-leg cost) — the completion of M2
  pairs_dnGrossSum: prDn.grossReturns.reduce((a, v) => a + (Number.isFinite(v) ? v : 0), 0),
  pairs_dnNetEquity: prDn.equity[prDn.equity.length - 1],
};

process.stdout.write(JSON.stringify(out));
