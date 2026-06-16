"""Tests for the regime-gate primitives: hurst / variance_ratio / adx / yang_zhang_vol."""
import numpy as np
import pandas as pd

from btcquant import features


def _ohlc_from_close(close, seed=0):
    """Build a plausible OHLC frame from a close path (open = prior close + small noise)."""
    rng = np.random.default_rng(seed)
    c = pd.Series(close, dtype="float64")
    o = c.shift(1).fillna(c.iloc[0])
    wick = np.abs(rng.standard_normal(len(c))) * (c.abs().mean() * 1e-3 + 1e-6)
    hi = np.maximum(o, c) + wick
    lo = np.minimum(o, c) - wick
    return pd.DataFrame({"open": o, "high": hi, "low": lo, "close": c})


def _ar1(n, phi, seed):
    rng = np.random.default_rng(seed)
    e = rng.standard_normal(n)
    x = np.zeros(n)
    for t in range(1, n):
        x[t] = phi * x[t - 1] + e[t]
    return x


def test_hurst_orders_trend_above_randomwalk_above_meanreversion():
    rng = np.random.default_rng(1)
    n = 2000
    trend = pd.Series(100 + np.cumsum(0.5 + 0.1 * rng.standard_normal(n)))   # strong drift
    rw = pd.Series(100 + np.cumsum(rng.standard_normal(n)))                  # random walk
    # mean-reverting LEVEL: an OU path around 100 (anti-persistent)
    mr = np.zeros(n); mr[0] = 100.0
    e = rng.standard_normal(n)
    for t in range(1, n):
        mr[t] = mr[t - 1] + 0.4 * (100.0 - mr[t - 1]) + e[t]
    mr = pd.Series(mr)
    h_tr = features.hurst(trend)
    h_rw = features.hurst(rw)
    h_mr = features.hurst(mr)
    assert h_tr > 0.55          # trending
    assert h_mr < 0.45          # mean-reverting
    assert h_tr > h_rw > h_mr   # monotone ordering


def test_hurst_rolling_returns_series_causal_length():
    s = pd.Series(100 + np.cumsum(np.random.default_rng(2).standard_normal(600)))
    roll = features.hurst(s, window=200)
    assert isinstance(roll, pd.Series) and len(roll) == len(s)
    assert roll.iloc[:199].isna().all()      # warm-up NaN (trailing window, causal)
    assert np.isfinite(roll.iloc[-1])


def test_variance_ratio_classifies_momentum_meanrev_randomwalk():
    rw = features.variance_ratio(pd.Series(np.random.default_rng(3).standard_normal(3000)), q=4)
    mom = features.variance_ratio(pd.Series(_ar1(3000, 0.3, 4)), q=4)     # positively autocorr
    mr = features.variance_ratio(pd.Series(_ar1(3000, -0.3, 5)), q=4)     # negatively autocorr
    assert abs(rw["vr"] - 1.0) < 0.15 and abs(rw["z_star"]) < 2.5         # random walk ~ 1
    assert mom["vr"] > 1.1 and mom["z_star"] > 2.0                        # trending, significant
    assert mr["vr"] < 0.9 and mr["z_star"] < -2.0                         # mean-reverting, significant


def test_adx_high_in_trend_low_in_range():
    n = 600
    trend = _ohlc_from_close(100 + np.arange(n) * 0.5, seed=6)            # monotone up
    rng = np.random.default_rng(7)
    rangey = _ohlc_from_close(100 + 5.0 * np.sin(np.arange(n) * 0.3) + 0.2 * rng.standard_normal(n), seed=7)
    adx_trend = features.adx(trend, window=14).iloc[-1]
    adx_range = features.adx(rangey, window=14).iloc[-1]
    assert adx_trend > 40.0
    assert adx_range < 25.0
    assert adx_trend > adx_range


def test_yang_zhang_vol_positive_and_scales_with_volatility():
    rng = np.random.default_rng(8)
    n = 500
    lo_vol = _ohlc_from_close(100 * np.cumprod(1 + 0.005 * rng.standard_normal(n)), seed=8)
    hi_vol = _ohlc_from_close(100 * np.cumprod(1 + 0.03 * rng.standard_normal(n)), seed=9)
    yz_lo = features.yang_zhang_vol(lo_vol, window=20, periods_per_year=365)
    yz_hi = features.yang_zhang_vol(hi_vol, window=20, periods_per_year=365)
    assert yz_lo.dropna().gt(0).all() and yz_hi.dropna().gt(0).all()
    assert yz_hi.iloc[-1] > yz_lo.iloc[-1]                                # higher-vol series ⇒ higher YZ
    # truly flat OHLC (no intraday range at all) ⇒ ~zero vol
    flat = pd.DataFrame({"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0},
                        index=range(n))
    assert features.yang_zhang_vol(flat, window=20).dropna().iloc[-1] < 1e-9
