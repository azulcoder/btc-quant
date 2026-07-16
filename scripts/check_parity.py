#!/usr/bin/env python3
"""check_parity.py — enforce the project's ONE RULE: every shared formula in the
Python engine (``btcquant/``) and its dashboard mirror (``dashboard/quant.js``) must
agree.

It builds a single deterministic fixture, computes ~30 named quantities on the Python
side, runs ``scripts/_parity_eval.cjs`` (which evaluates the same names via the
require-able ``quant.js``), and asserts every pair agrees within a documented
tolerance. Pure arithmetic is held to ~machine epsilon; the inverse-normal (Acklam
``normPpf``) and ``erf``-based CDF paths carry the looser, *documented* tolerances from
DEVELOPMENT.md §5. Exit 0 = parity holds; exit 1 = a real divergence; exit 2 = Node
unavailable (the pytest wrapper skips on this, CI always has Node).

Run: ``python scripts/check_parity.py``  (also run in CI on every push/PR).
"""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np
import pandas as pd
from scipy import stats as sps

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from btcquant import backtest, features, risk, strategies  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
EVAL_CJS = os.path.join(HERE, "_parity_eval.cjs")


def build_fixture() -> dict:
    """A fixed, seeded fixture: a positive random-walk close, a long/short position
    series (sign of close-vs-SMA20, flat in warm-up → several trades), and the scalar
    inputs for the risk / options formulas."""
    rng = np.random.default_rng(7)
    n = 260
    close = 100.0 * np.cumprod(1.0 + 0.01 * rng.standard_normal(n))
    close_s = pd.Series(close)
    sma20 = close_s.rolling(20).mean()
    pos = np.where(close_s > sma20, 1.0, -1.0)
    pos[sma20.isna().to_numpy()] = 0.0          # flat during warm-up
    return {
        "close": close.tolist(),
        "positions": pos.tolist(),
        "ppy": 365,
        "volWindow": 20,
        "k": 2.0,
        # fixed scalars for the PSR / DSR math (raw, non-excess kurtosis)
        "sr": 0.08, "n": 250, "skew": -0.3, "kurt": 4.0,
        "nTrials": 10, "varTrialsSr": 1.0,
        # M6 UNSATURATED DSR pins. The nTrials=10/var=1.0 point above deflates the
        # benchmark so hard that the CDF sits ~0-saturated in the tail — any two
        # implementations agree there "for free", so it detects nothing. These two
        # sit where a real formula drift would move the value:
        #   dsr_n1  — N=1 must hit the sr0=0 special case (DSR ≡ PSR(0) ≈ 0.8934,
        #             for ANY trial variance; pre-M6 the JS mirror returned 1.0
        #             identically here via normPpf(0) = -Inf).
        #   dsr_mid — N=5, V=0.001, sr=0.05/period, n=500 → ≈ 0.6073 (mid-range).
        "varN1": 123.456,
        "srMid": 0.05, "nMid": 500, "nTrialsMid": 5, "varMid": 0.001,
        # FST (False Strategy Theorem, Bailey-LdP 2014) probes. expected_max at two N;
        # the per-period false-strategy THRESHOLD (fixed point) + P(best is false) reuse
        # the mid case (nTrialsMid/varMid/nMid/srMid/skew/kurt). effective_number_of_trials
        # runs on a fixed 4-col matrix (2 identical + 2 independent columns built from the
        # discrete-Fourier basis so the columns are EXACTLY orthogonal): correlation matrix
        # [[1,1,0,0],[1,1,0,0],[0,0,1,0],[0,0,0,1]] → eigenvalues [0,1,1,2] → N_eff = 8/3.
        **_neff_fixture(),
        # Hierarchical-Bayes shrinkage probe (frontier #3 — Efron-Morris / James-Stein;
        # DerSimonian-Laird tau^2): a FIXED synthetic family of k=4 strategies with
        # chosen per-period Sharpes and chosen B2-style sampling variances sigma_i^2
        # (fixed literals so BOTH engines evaluate the same inputs; in production the
        # variances come from risk.sharpe_estimator_variance). hbNeff = 2.5 < k probes
        # the correlation-aware tau^2 variant (df = N_eff - 1 ⟹ a LARGER tau^2).
        "hbSharpes": [0.10, 0.02, -0.03, 0.06],
        "hbVariances": [0.002, 0.004, 0.003, 0.005],
        "hbNeff": 2.5,
        # walk-forward fold-V probe (M6 C2/C3): folds-as-trials on the fixture series
        "folds": 5,
        # M4 CPCV probe: cpcv was ABSENT from the harness before M4. Pin the multi-path
        # dispersion + the legacy embargo_pct leading-trim formula, AND a run that
        # exercises the new int purge/embargo edge-trims so a Python↔JS drift is caught.
        "cpcvBlocks": 6, "cpcvKTest": 2, "cpcvPurge": 1, "cpcvEmbargo": 2,
        "costBps": 10.0, "slipBps": 2.0,
        # one option contract for Black-76 greeks (~30d)
        "fwd": 65000.0, "strike": 66000.0, "iv": 0.55, "t": 30.0 / 365.0,
        **_pairs_fixture(),
        **_option_chain_fixture(),
    }


def _option_chain_fixture() -> dict:
    """M8 options-parity probe: a FIXED synthetic single-expiry chain (6 strikes ×
    {call, put}, each with open_interest + iv, one underlying) for a Python-vs-JS pin
    on ``max_pain`` (argmin over strikes — an exact price match) and the
    ``gamma_concentration`` profile (Black-76 |gamma|·OI density by strike). These two
    analytics have a live quant.js mirror + Python source but crossed the mirror
    UNPINNED before M8 (only the Black-76 greeks themselves were pinned). ``optNow`` is
    exactly 30 calendar days before the 08:00-UTC ``optExpiry`` so ``T = 30/365`` matches
    the Black-76 probe; the underlying is a single 65 000 mark so ``forward = 65 000``."""
    strikes = [60000.0, 62000.0, 64000.0, 66000.0, 68000.0, 70000.0]
    call_oi = [120.0, 200.0, 500.0, 640.0, 260.0, 150.0]
    put_oi = [350.0, 300.0, 260.0, 180.0, 120.0, 90.0]
    call_iv = [0.72, 0.66, 0.60, 0.56, 0.61, 0.67]
    put_iv = [0.70, 0.64, 0.58, 0.55, 0.62, 0.69]
    rows = []
    for i, k in enumerate(strikes):
        rows.append({"strike": k, "opt_type": "C", "open_interest": call_oi[i],
                     "iv": call_iv[i], "underlying_price": 65000.0})
        rows.append({"strike": k, "opt_type": "P", "open_interest": put_oi[i],
                     "iv": put_iv[i], "underlying_price": 65000.0})
    return {
        "optChain": rows,
        "optExpiry": "2026-01-31T08:00:00Z",
        "optNow": "2026-01-01T08:00:00Z",
        "optT": 30.0 / 365.0,
    }


def _neff_fixture() -> dict:
    """FST effective_number_of_trials probe: a fixed 4-column matrix with 2 IDENTICAL and
    2 INDEPENDENT columns. Built from the discrete-Fourier basis (sin/cos over a full
    period are exactly orthogonal), so the sample correlation matrix is
    [[1,1,0,0],[1,1,0,0],[0,0,1,0],[0,0,0,1]] with eigenvalues [0,1,1,2] and the
    participation ratio N_eff = (Σλ)²/Σλ² = 4²/6 = 8/3. Passed as a list of COLUMNS
    (matrix like pbo); the Python side transposes to (T, N)."""
    tt = np.arange(240)
    a = np.sin(2.0 * np.pi * tt / 240.0)      # col 0
    b = np.cos(2.0 * np.pi * tt / 240.0)      # col 2 (⊥ a)
    c = np.sin(4.0 * np.pi * tt / 240.0)      # col 3 (⊥ a, ⊥ b)
    return {"neffCols": [a.tolist(), a.tolist(), b.tolist(), c.tolist()]}


def _pairs_fixture() -> dict:
    """M2 pairs two-leg-cost probe: a fixed synthetic (btc, eth) with a mean-reverting
    log-spread (so the z-score fades actually trade), for a Python-vs-JS parity pin on
    the ETH-leg turnover |Δ(beta·state)| and the two-leg cost base. This is the pairs
    path that was UNPINNED before M2 — a real mirror gap."""
    rng = np.random.default_rng(19)
    npair = 180
    btc = 30000.0 * np.cumprod(1.0 + 0.01 * rng.standard_normal(npair))
    sp = np.zeros(npair)
    noise = 0.02 * rng.standard_normal(npair)
    for i in range(1, npair):          # AR(1) φ=0.7 stationary spread → reversion trades
        sp[i] = 0.7 * sp[i - 1] + noise[i]
    eth = btc * 0.07 * np.exp(sp)
    return {"btcPairs": btc.tolist(), "ethPairs": eth.tolist(), "pairsWindow": 30}


def _hb_fields(fx: dict) -> dict:
    """Hierarchical-Bayes probe fields (Python side). One standard (df = k-1) run —
    mu, tau, and the full shrunk / shrink_factor / p_skill vectors — plus the tau of
    the correlation-aware variant (df = hbNeff - 1), which must exceed the standard
    tau (N_eff < k widens the estimated population spread)."""
    hb = risk.hierarchical_bayes_sharpe(fx["hbSharpes"], fx["hbVariances"])
    hbn = risk.hierarchical_bayes_sharpe(fx["hbSharpes"], fx["hbVariances"],
                                         effective_n=fx["hbNeff"])
    return {
        "hb_mu": float(hb["mu"]),
        "hb_tau": float(hb["tau"]),
        "hb_shrunk": [float(x) for x in hb["shrunk"]],
        "hb_shrinkFactor": [float(x) for x in hb["shrink_factor"]],
        "hb_pSkill": [float(x) for x in hb["p_skill"]],
        "hb_neffTau": float(hbn["tau"]),
    }


def python_side(fx: dict) -> dict:
    """Compute every named quantity on the Python (source-of-truth) side."""
    close = pd.Series(fx["close"])
    pos = pd.Series(fx["positions"])
    ppy = fx["ppy"]
    ret = features.simple_returns(close)
    ret_clean = ret.dropna()
    eq = (1.0 + ret.fillna(0.0)).cumprod()
    vol = features.realized_vol(ret, fx["volWindow"], ppy)

    er = risk.expectancy_report(pos, close, vol, periods_per_year=ppy, k=fx["k"])
    g = features.black76_greeks(fx["fwd"], fx["strike"], fx["iv"], fx["t"], "C", 0.0)
    run = backtest.run(pos, close, cost_bps=fx["costBps"], slippage_bps=fx["slipBps"],
                       periods_per_year=ppy, n_trials=fx["nTrials"],
                       var_trials_sr=fx["varTrialsSr"])
    st = run["stats"]

    # Walk-forward fold-V probe (M6 C2/C3): the engine's own walk_forward must agree
    # with the JS mirror on the empirical ddof=1 fold-SR variance, the fold-deflated
    # DSR (N = n_splits), and the fallback flag — on the same fixture series.
    # M4: thread purge=0/embargo=0 EXPLICITLY (the audit convention default) — the OOS
    # headline must be byte-identical to the no-arg call, mirrored on the JS side.
    wf = backtest.walk_forward(
        lambda px: pd.Series(fx["positions"], index=px.index), close,
        n_splits=fx["folds"], cost_bps=fx["costBps"], slippage_bps=fx["slipBps"],
        periods_per_year=ppy, purge=0, embargo=0,
    )
    wfo = wf["oos"]

    # M4 CPCV probe. Default (embargo_pct=0.01 legacy trim) + an int purge/embargo run.
    cpcv_pos = lambda p: pd.Series(fx["positions"], index=p.index)
    cp = backtest.cpcv(cpcv_pos, close, n_blocks=fx["cpcvBlocks"], k_test=fx["cpcvKTest"],
                       cost_bps=fx["costBps"], slippage_bps=fx["slipBps"], periods_per_year=ppy)
    cp_pe = backtest.cpcv(cpcv_pos, close, n_blocks=fx["cpcvBlocks"], k_test=fx["cpcvKTest"],
                          cost_bps=fx["costBps"], slippage_bps=fx["slipBps"], periods_per_year=ppy,
                          purge=fx["cpcvPurge"], embargo=fx["cpcvEmbargo"])

    # M2 pairs two-leg-cost probe: pairs_legs is the ONE source of (state, beta_t); the
    # ETH-leg turnover |Δ(beta·state)| is fed to run() as extra_cost_turnover. Pin both
    # the ETH-leg turnover and the two-leg cost base vs the JS mirror.
    btc_p = pd.Series(fx["btcPairs"])
    eth_p = pd.Series(fx["ethPairs"])
    pos_p, beta_p = strategies.pairs_legs(btc_p, eth_p, window=fx["pairsWindow"], model="coint")
    eth_turn = (beta_p * pos_p).diff().abs()
    pr_none = backtest.run(pos_p, btc_p, cost_bps=fx["costBps"], slippage_bps=fx["slipBps"],
                           periods_per_year=ppy)
    pr_ext = backtest.run(pos_p, btc_p, cost_bps=fx["costBps"], slippage_bps=fx["slipBps"],
                          periods_per_year=ppy, extra_cost_turnover=eth_turn)

    # M9 pairs delta-neutral P&L probe: the BTC-leg state earns the SPREAD return, so
    # book gross = state·(btc_ret - beta_{t-1}·eth_ret) AND charge the two-leg cost.
    # Pin the net-equity + gross-return sum vs the JS mirror (this closes M2's P&L gap).
    hedge_ret = beta_p.shift(1) * eth_p.pct_change()
    pr_dn = backtest.run(pos_p, btc_p, cost_bps=fx["costBps"], slippage_bps=fx["slipBps"],
                         periods_per_year=ppy, extra_cost_turnover=eth_turn,
                         hedge_return=hedge_ret)

    # M8 options-parity probe: max_pain (argmin over strikes → exact price match) and
    # the gamma_concentration profile (|gamma|·OI density) on the fixed synthetic chain.
    # Both have a live quant.js mirror + Python source but crossed the mirror UNPINNED
    # before M8. The chain rows serialize with the option feed's own column names.
    oc = pd.DataFrame(fx["optChain"])
    oc["expiry"] = fx["optExpiry"]
    mp = features.max_pain(oc, fx["optExpiry"])
    gc = features.gamma_concentration(oc, fx["optExpiry"], now=pd.Timestamp(fx["optNow"]))
    gc_strikes = np.asarray(gc["strikes"], dtype=float)
    gc_oi = np.asarray(gc["gamma_oi"], dtype=float)

    return {
        # numeric
        "mean": float(ret_clean.mean()),
        "std": float(ret_clean.std(ddof=1)),
        "skewness": float(sps.skew(ret_clean, bias=False)),  # JS applies the adjusted Fisher-Pearson correction
        "kurtosis": float(sps.kurtosis(ret_clean, fisher=True, bias=True)),
        "normCdf": float(sps.norm.cdf(0.7)),
        "normPpf": float(sps.norm.ppf(0.975)),
        "normPdf": float(sps.norm.pdf(0.3)),
        # features
        "simpleRet_last": float(ret.iloc[-1]),
        "logRet_last": float(features.log_returns(close).iloc[-1]),
        "realizedVol_last": float(vol.iloc[-1]),
        "sma_last": float(features.sma(close, 10).iloc[-1]),
        "ema_last": float(features.ema(close, 10).iloc[-1]),
        "momentum_last": float(features.momentum(close, 30).iloc[-1]),
        "zscore_last": float(features.zscore(close, 30).iloc[-1]),
        "rsi_last": float(features.rsi(close, 14).iloc[-1]),
        "maxDrawdown": float(features.max_drawdown(eq)),
        # risk
        "sharpe": float(risk.sharpe(ret, periods_per_year=ppy)),
        "sortino": float(risk.sortino(ret, periods_per_year=ppy)),
        "cagr": float(risk.cagr(ret, ppy)),
        "hitRate": float(risk.hit_rate(ret)),
        "psr": float(risk.probabilistic_sharpe_ratio(fx["sr"], fx["n"], fx["skew"], fx["kurt"])),
        "dsr": float(risk.deflated_sharpe_ratio(fx["sr"], fx["n"], fx["skew"], fx["kurt"],
                                                fx["nTrials"], fx["varTrialsSr"])),
        # M6 unsaturated DSR pins (see build_fixture; also anchored to constants in PINS)
        "dsr_n1": float(risk.deflated_sharpe_ratio(fx["sr"], fx["n"], fx["skew"], fx["kurt"],
                                                   1, fx["varN1"])),
        "dsr_mid": float(risk.deflated_sharpe_ratio(fx["srMid"], fx["nMid"], fx["skew"],
                                                    fx["kurt"], fx["nTrialsMid"], fx["varMid"])),
        "minBTL": float(risk.min_backtest_length(fx["nTrials"])),
        # FST (False Strategy Theorem, Bailey-LdP 2014) — new surfaced diagnostics.
        "emaxN5": float(risk.expected_max_sharpe_ratio(5, 1.0)),
        "emaxN10": float(risk.expected_max_sharpe_ratio(10, 1.0)),
        "fstThreshold": float(risk.false_strategy_threshold(
            fx["nTrialsMid"], fx["varMid"], fx["nMid"], fx["skew"], fx["kurt"], 0.95)),
        "neffTrials": float(risk.effective_number_of_trials(
            np.asarray(fx["neffCols"], dtype=float).T)),
        "probFalseStrategy": float(risk.probability_false_strategy(
            fx["srMid"], fx["nTrialsMid"], fx["varMid"], fx["nMid"], fx["skew"], fx["kurt"])),
        # Hierarchical-Bayes shrinkage (frontier #3) — risk.hierarchical_bayes_sharpe vs
        # the quant.js hierarchicalBayesSharpe mirror on the fixed k=4 family. mu/tau are
        # scalars; shrunk/B/p are compared ELEMENTWISE (each of the k=4 values must
        # agree). hb_neffTau pins the correlation-aware DL variant (df = hbNeff - 1).
        **_hb_fields(fx),
        # Tharp eval layer
        "er_nTrades": int(er["n_trades"]),
        "er_expectancyR": float(er["expectancy_r"]),
        "er_winRate": float(er["win_rate"]),
        "er_payoffRatio": float(er["payoff_ratio"]),
        "er_sqn": float(er["sqn"]),
        "er_profitFactor": float(er["profit_factor"]),
        # options structural
        "b76_delta": float(g["delta"]),
        "b76_gamma": float(g["gamma"]),
        "b76_vega": float(g["vega"]),
        # M8 options analytics — max_pain + gamma_concentration (Python source-of-truth
        # vs quant.js mirror), completing the options-parity coverage past the greeks.
        "mp_maxPain": float(mp["max_pain"]),          # argmin over strikes — exact match
        "mp_pcOiRatio": float(mp["pc_oi_ratio"]),
        "mp_forward": float(mp["forward"]),
        "gc_sum": float(gc_oi.sum()),                 # total |gamma|·OI density
        "gc_dot": float((gc_strikes * gc_oi).sum()),  # strike-weighted profile shape
        "gc_peakStrike": float(gc_strikes[int(np.argmax(gc_oi))]),  # densest-gamma strike
        # end-to-end engine
        "bt_sharpe": float(st["sharpe"]),
        "bt_maxDrawdown": float(st["max_drawdown"]),
        "bt_deflatedSharpe": float(st["deflated_sharpe"]),
        # walk-forward fold-V (M6 C2/C3) — engine walk_forward vs JS walkForward
        "wf_oosSharpe": float(wfo["sharpe"]),
        "wf_varTrialsSr": float(wfo["var_trials_sr"]),
        "wf_deflatedSharpe": float(wfo["deflated_sharpe"]),
        "wf_varFallback": bool(wfo["var_fallback"]),
        # M4 CPCV multi-path dispersion (Python source-of-truth vs quant.js mirror)
        "cpcv_nPaths": int(cp["n_paths"]),
        "cpcv_median": float(cp["median_sharpe"]),
        "cpcv_p25": float(cp["p25"]),
        "cpcv_p75": float(cp["p75"]),
        "cpcv_iqr": float(cp["iqr"]),
        "cpcv_min": float(cp["min"]),
        "cpcv_max": float(cp["max"]),
        # M4 CPCV with int purge=1/embargo=2 edge-trims (new-param mirror)
        "cpcv_pe_median": float(cp_pe["median_sharpe"]),
        "cpcv_pe_nPaths": int(cp_pe["n_paths"]),
        # M2 pairs two-leg cost (Python source-of-truth vs quant.js mirror)
        "pairs_beta_last": float(beta_p.iloc[-1]),
        "pairs_ethTurnover": float(eth_turn.fillna(0.0).sum()),
        "pairs_btcTurnover": float(pr_none["stats"]["total_turnover"]),
        "pairs_totalTurnover": float(pr_ext["stats"]["total_turnover"]),
        "pairs_netEquity": float(pr_ext["equity"].iloc[-1]),
        # M9 delta-neutral P&L (spread return, two-leg cost) — the completion of M2
        "pairs_dnGrossSum": float(pr_dn["gross_returns"].fillna(0.0).sum()),
        "pairs_dnNetEquity": float(pr_dn["equity"].iloc[-1]),
    }


# name -> tolerance (rel & abs passed to math.isclose). Pure arithmetic ~ machine eps;
# erf-CDF ~1e-7; Acklam normPpf / DSR ~1e-7 (documented in DEVELOPMENT.md §5).
TOL = {
    "mean": 1e-12, "std": 1e-12, "skewness": 1e-12, "kurtosis": 1e-12,
    "normCdf": 1e-7, "normPpf": 1e-8, "normPdf": 1e-12,
    "simpleRet_last": 1e-12, "logRet_last": 1e-12, "realizedVol_last": 1e-12,
    "sma_last": 1e-12, "ema_last": 1e-12, "momentum_last": 1e-12,
    "zscore_last": 1e-12, "rsi_last": 1e-6, "maxDrawdown": 1e-12,
    "sharpe": 1e-12, "sortino": 1e-12, "cagr": 1e-12, "hitRate": 1e-12,
    "psr": 1e-7, "dsr": 1e-7, "dsr_n1": 1e-7, "dsr_mid": 1e-7, "minBTL": 1e-9,
    "emaxN5": 1e-7, "emaxN10": 1e-7, "fstThreshold": 1e-7, "neffTrials": 1e-7,
    "probFalseStrategy": 1e-7,
    # Hierarchical-Bayes shrinkage (frontier #3). mu/tau/shrunk/B are pure arithmetic
    # (same left-to-right summation order both sides); p_skill crosses the erf-based
    # JS normCdf ⟹ the documented 1e-7. Vector fields compare elementwise.
    "hb_mu": 1e-9, "hb_tau": 1e-9, "hb_shrunk": 1e-9, "hb_shrinkFactor": 1e-9,
    "hb_pSkill": 1e-7, "hb_neffTau": 1e-9,
    "er_nTrades": 0, "er_expectancyR": 1e-12, "er_winRate": 1e-12,
    "er_payoffRatio": 1e-12, "er_sqn": 1e-12, "er_profitFactor": 1e-12,
    "b76_delta": 5e-7, "b76_gamma": 1e-9, "b76_vega": 1e-9,
    "mp_maxPain": 0, "mp_pcOiRatio": 1e-12, "mp_forward": 1e-12,
    "gc_sum": 1e-9, "gc_dot": 1e-7, "gc_peakStrike": 0,
    "bt_sharpe": 1e-9, "bt_maxDrawdown": 1e-12, "bt_deflatedSharpe": 1e-7,
    "wf_oosSharpe": 1e-9, "wf_varTrialsSr": 1e-9, "wf_deflatedSharpe": 1e-7,
    "wf_varFallback": 0,
    "cpcv_nPaths": 0, "cpcv_median": 1e-9, "cpcv_p25": 1e-9, "cpcv_p75": 1e-9,
    "cpcv_iqr": 1e-9, "cpcv_min": 1e-9, "cpcv_max": 1e-9,
    "cpcv_pe_median": 1e-9, "cpcv_pe_nPaths": 0,
    "pairs_beta_last": 1e-9, "pairs_ethTurnover": 1e-7, "pairs_btcTurnover": 1e-7,
    "pairs_totalTurnover": 1e-7, "pairs_netEquity": 1e-7,
    "pairs_dnGrossSum": 1e-7, "pairs_dnNetEquity": 1e-7,
}

# M6 anchor pins: the PYTHON side must sit on these constants (they were computed
# once from the Bailey-LdP closed form and are pre-registered here) so the two
# engines cannot drift TOGETHER and still "pass". dsr_n1 must additionally equal
# the psr field exactly (C1 identity: N=1 ⟹ sr0 = 0 ⟹ DSR ≡ PSR(0)).
PINS = {
    "psr": (0.8933576314257702, 1e-7),
    "dsr_n1": (0.8933576314257702, 1e-7),   # == psr; ANY trial variance at N=1
    "dsr_mid": (0.6072585304659127, 1e-7),  # N=5, V=0.001, sr=0.05/period, n=500
    # FST anchors (Bailey-LdP 2014) — pre-registered from the closed form so the two
    # engines cannot drift TOGETHER and still pass. emax at N=5/N=10 (V=1); the per-period
    # false-strategy threshold re-feeds into PSR to give EXACTLY 0.95 (asserted below);
    # N_eff = 8/3 for the 2-identical+2-independent matrix; P(false) = 1 - dsr_mid.
    "emaxN5": (1.1925940010147893, 1e-7),
    "emaxN10": (1.57459830134575, 1e-7),
    "fstThreshold": (0.11292934779100049, 1e-7),
    "neffTrials": (2.6666666666666665, 1e-7),
    "probFalseStrategy": (0.3927414695340873, 1e-7),
    # M8 options anchors: pre-registered so the two engines cannot drift TOGETHER and
    # still pass — max_pain is the exact argmin strike, gc_sum the total gamma density.
    "mp_maxPain": (64000.0, 1e-9),
    "gc_sum": (0.10701664008807263, 1e-9),
    # Hierarchical-Bayes anchors (frontier #3) — the pooled mean and between-strategy
    # tau on the fixed k=4 family, pre-registered from the closed form (DerSimonian-
    # Laird tau^2 then random-effects pooling), plus the correlation-aware variant's
    # tau (df = 2.5 - 1 ⟹ larger than hb_tau — asserted as an identity below too).
    "hb_mu": (0.042615507958796955, 1e-9),
    "hb_tau": (0.025259219485448958, 1e-9),
    "hb_neffTau": (0.04758979651558059, 1e-9),
}


def _agree(a, b, tol) -> bool:
    if isinstance(a, (list, tuple)) or isinstance(b, (list, tuple)):
        # vector fields (the HB shrunk/B/p arrays): every element must agree.
        return (isinstance(a, (list, tuple)) and isinstance(b, (list, tuple))
                and len(a) == len(b)
                and all(_agree(x, y, tol) for x, y in zip(a, b)))
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    fa, fb = float(a), float(b)
    if math.isnan(fa) and math.isnan(fb):
        return True
    if tol == 0:
        return fa == fb
    return math.isclose(fa, fb, rel_tol=tol, abs_tol=tol)


def main() -> int:
    node = shutil.which("node")
    if node is None:
        print("check_parity: node not found on PATH — skipping (CI runs it).", file=sys.stderr)
        return 2

    fx = build_fixture()
    py = python_side(fx)

    # Anchor the Python side to the pre-registered constants BEFORE comparing to
    # JS: a joint drift of both engines must fail here, not slide through parity.
    pin_fails = [name for name, (want, tol) in PINS.items()
                 if not math.isclose(py[name], want, rel_tol=tol, abs_tol=tol)]
    if py["dsr_n1"] != py["psr"]:
        pin_fails.append("dsr_n1 != psr (C1 identity: N=1 ⟹ DSR ≡ PSR(0))")
    # FST identity: the false-strategy threshold, re-fed into PSR against the same
    # expected-max benchmark, must recover EXACTLY prob=0.95 (the fixed point closes).
    _fst_sr0 = risk.expected_max_sharpe_ratio(fx["nTrialsMid"], fx["varMid"])
    _fst_psr = risk.probabilistic_sharpe_ratio(
        py["fstThreshold"], fx["nMid"], fx["skew"], fx["kurt"], sr_benchmark=_fst_sr0)
    if not math.isclose(_fst_psr, 0.95, rel_tol=1e-9, abs_tol=1e-9):
        pin_fails.append(f"PSR(fstThreshold; sr0) = {_fst_psr!r} != 0.95 (fixed point)")
    # HB identities: (1) the correlation-aware tau (df = N_eff - 1 < k - 1) must EXCEED
    # the standard tau — correlated trials widen the estimated population spread;
    # (2) the classic shrinkage identity thetaHat = B·mu + (1-B)·SR must close exactly.
    if not py["hb_neffTau"] > py["hb_tau"]:
        pin_fails.append(f"hb_neffTau {py['hb_neffTau']!r} !> hb_tau {py['hb_tau']!r} "
                         "(N_eff < k must WIDEN tau)")
    fx_sr = fx["hbSharpes"]
    for _i, (_th, _b, _s) in enumerate(zip(py["hb_shrunk"], py["hb_shrinkFactor"], fx_sr)):
        if not math.isclose(_th, _b * py["hb_mu"] + (1.0 - _b) * _s,
                            rel_tol=1e-12, abs_tol=1e-12):
            pin_fails.append(f"hb shrinkage identity broken at i={_i}")
    if pin_fails:
        for f in pin_fails:
            print(f"PIN FAIL — {f}")
        for k, (want, tol) in PINS.items():
            print(f"  {k}: python={py[k]!r}  pinned={want!r}  tol={tol}")
        return 1

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(fx, fh)
        fx_path = fh.name
    try:
        proc = subprocess.run([node, EVAL_CJS, fx_path], capture_output=True, text=True)
    finally:
        os.unlink(fx_path)
    if proc.returncode != 0:
        print("check_parity: node evaluator failed:\n" + proc.stderr, file=sys.stderr)
        return 1
    js = json.loads(proc.stdout)

    names = list(py.keys())
    width = max(len(n) for n in names)
    worst = 0.0
    fails = []
    print(f"{'check':<{width}}  {'python':>16}  {'js':>16}  {'|Δ|':>10}  ok")
    print("─" * (width + 50))
    for name in names:
        a, b = py[name], js.get(name)
        ok = _agree(a, b, TOL[name])
        if isinstance(a, (list, tuple)):
            # vector field (HB shrunk/B/p): report the worst elementwise |Δ| and the
            # first element on each side (the full vectors are asserted in _agree).
            try:
                d = max(abs(float(x) - float(y)) for x, y in zip(a, b))
            except (TypeError, ValueError):
                d = float("nan")
            if not math.isnan(d):
                worst = max(worst, d)
            if not ok:
                fails.append(name)
            fa = float(a[0]) if a else float("nan")
            fb = float(b[0]) if isinstance(b, (list, tuple)) and b else float("nan")
            print(f"{name:<{width}}  {fa:>13.8g} ×{len(a)}  {fb:>13.8g} ×{len(a)}  "
                  f"{d:>10.2e}  {'✓' if ok else '✗ FAIL'}")
            continue
        try:
            d = abs(float(a) - float(b))
            if not math.isnan(d):
                worst = max(worst, d)
        except (TypeError, ValueError):
            d = float("nan")
        if not ok:
            fails.append(name)
        print(f"{name:<{width}}  {float(a):>16.8g}  {float(b):>16.8g}  {d:>10.2e}  "
              f"{'✓' if ok else '✗ FAIL'}")

    print("─" * (width + 50))
    if fails:
        print(f"PARITY FAIL — {len(fails)} field(s) diverge: {', '.join(fails)}")
        return 1
    print(f"PARITY PASS — {len(names)} fields agree; worst |Δ| = {worst:.2e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
