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
from btcquant import backtest, features, risk  # noqa: E402

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
        "costBps": 10.0, "slipBps": 2.0,
        # one option contract for Black-76 greeks (~30d)
        "fwd": 65000.0, "strike": 66000.0, "iv": 0.55, "t": 30.0 / 365.0,
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
        "minBTL": float(risk.min_backtest_length(fx["nTrials"])),
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
        # end-to-end engine
        "bt_sharpe": float(st["sharpe"]),
        "bt_maxDrawdown": float(st["max_drawdown"]),
        "bt_deflatedSharpe": float(st["deflated_sharpe"]),
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
    "psr": 1e-7, "dsr": 1e-7, "minBTL": 1e-9,
    "er_nTrades": 0, "er_expectancyR": 1e-12, "er_winRate": 1e-12,
    "er_payoffRatio": 1e-12, "er_sqn": 1e-12, "er_profitFactor": 1e-12,
    "b76_delta": 5e-7, "b76_gamma": 1e-9, "b76_vega": 1e-9,
    "bt_sharpe": 1e-9, "bt_maxDrawdown": 1e-12, "bt_deflatedSharpe": 1e-7,
}


def _agree(a, b, tol) -> bool:
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
