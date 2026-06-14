"""test_core.py — honesty-rail unit tests for btc-quant.

These tests are the *teeth* behind the project's honesty rails (DESIGN.md
non-negotiables; RESEARCH.md §3). They are fully **deterministic**: every input is
seeded synthetic data built locally — **no network, no cached files**.

What is asserted
----------------
1. **No look-ahead in** ``backtest.run`` — a signal known only at bar ``t`` cannot
   affect P&L before bar ``t+1`` (the backtester shifts positions by one bar; a
   one-bar spike in the signal moves only the *next* bar's return).
2. **Vectorized == reference loop** for realized volatility and rolling/aggregate
   Sharpe (the fast pandas path matches a plain Python loop).
3. **Deflated Sharpe < raw Sharpe**, and the Deflated Sharpe **decreases as
   ``n_trials`` rises** (selection-bias deflation, Bailey & López de Prado 2014).
4. **Drawdown ≤ 0 everywhere** and ``max_drawdown`` matches the min of the
   drawdown series (and a hand-computed reference).
5. **Every strategy output stays within [-1, 1]** (a valid backtester target
   weight), across long/flat, long/short, dual-cross, vol-scaled, carry, and pairs.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from btcquant import backtest, data, features, risk, strategies


# --------------------------------------------------------------------------- #
# Deterministic synthetic fixtures (no network)                                #
# --------------------------------------------------------------------------- #
def _make_prices(n: int = 600, seed: int = 42, mu: float = 0.0008,
                 sigma: float = 0.03, start: float = 20_000.0) -> pd.Series:
    """A seeded geometric-random-walk close series on a daily UTC index."""
    rng = np.random.default_rng(seed)
    rets = rng.normal(mu, sigma, n)
    close = start * np.exp(np.cumsum(rets))
    idx = pd.date_range("2020-01-01", periods=n, freq="D", tz="UTC")
    return pd.Series(close, index=idx, name="close")


def _make_ohlcv(n: int = 600, seed: int = 42) -> pd.DataFrame:
    """A seeded OHLCV frame derived from the synthetic close (no network)."""
    close = _make_prices(n=n, seed=seed)
    rng = np.random.default_rng(seed + 1)
    wiggle = np.abs(rng.normal(0.0, 0.01, n))
    high = close * (1.0 + wiggle)
    low = close * (1.0 - wiggle)
    open_ = close.shift(1).fillna(close.iloc[0])
    volume = pd.Series(rng.uniform(100, 1000, n), index=close.index)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume}
    )


def _make_returns(n: int = 500, seed: int = 7, mu: float = 0.002,
                  sigma: float = 0.02) -> pd.Series:
    """A seeded positive-mean returns series (positive raw Sharpe)."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2021-01-01", periods=n, freq="D", tz="UTC")
    return pd.Series(rng.normal(mu, sigma, n), index=idx)


# --------------------------------------------------------------------------- #
# 1. No look-ahead in backtest.run                                             #
# --------------------------------------------------------------------------- #
def test_no_lookahead_signal_only_moves_next_bar():
    """A signal nonzero only at bar t must affect P&L at t+1, never at t or before."""
    prices = _make_prices(n=200, seed=1)
    spike_at = 100  # the single bar where the position is "on"

    pos = pd.Series(0.0, index=prices.index)
    pos.iloc[spike_at] = 1.0  # known only at the close of bar `spike_at`

    res = backtest.run(pos, prices, cost_bps=0.0, slippage_bps=0.0)
    gross = res["gross_returns"]

    # The position at `spike_at` earns the asset return of `spike_at + 1` only.
    asset_ret = prices.pct_change()
    expected_next = float(asset_ret.iloc[spike_at + 1])

    # Every gross return up to and including `spike_at` must be 0 (no leakage back).
    assert np.allclose(gross.iloc[: spike_at + 1].fillna(0.0).to_numpy(), 0.0), (
        "look-ahead: a signal at bar t leaked P&L into bar <= t"
    )
    # The t+1 bar carries the trade.
    assert gross.iloc[spike_at + 1] == pytest.approx(expected_next, rel=1e-12, abs=1e-12)
    # And only that bar (positions return to 0 afterwards).
    assert np.allclose(gross.iloc[spike_at + 2 :].fillna(0.0).to_numpy(), 0.0)


def test_no_lookahead_guard_rejects_unshifted_positions():
    """The internal guard must fire if traded positions are not the 1-bar lag."""
    raw = pd.Series([0.0, 1.0, 1.0, 0.0])
    # Hand-build a *wrong* (unshifted) traded series and confirm the guard catches it.
    with pytest.raises(AssertionError):
        backtest._assert_no_lookahead(raw, raw)  # not shifted -> must raise
    # The correct shift must pass.
    backtest._assert_no_lookahead(raw, raw.shift(1))


def test_run_traded_position_is_one_bar_lagged():
    """End-to-end: gross return at t equals position_{t-1} * asset_return_t."""
    prices = _make_prices(n=120, seed=3)
    ohlcv = _make_ohlcv(n=120, seed=3)
    pos = strategies.ma_trend_filter(ohlcv, n=20)

    res = backtest.run(pos, prices, cost_bps=0.0, slippage_bps=0.0)
    asset_ret = prices.pct_change()
    expected = (pos.shift(1) * asset_ret).reindex(res["gross_returns"].index)

    a = res["gross_returns"].to_numpy()
    b = expected.to_numpy()
    both_nan = np.isnan(a) & np.isnan(b)
    assert np.allclose(np.where(both_nan, 0.0, a), np.where(both_nan, 0.0, b))


# --------------------------------------------------------------------------- #
# 2. Vectorized == reference loop (realized vol, Sharpe)                        #
# --------------------------------------------------------------------------- #
def test_realized_vol_matches_reference_loop():
    """features.realized_vol equals a plain trailing-window std * sqrt(ppy) loop."""
    rets = _make_returns(n=300, seed=11)
    window = 20
    ppy = 365

    fast = features.realized_vol(rets, window=window, periods_per_year=ppy)

    ref = pd.Series(np.nan, index=rets.index)
    vals = rets.to_numpy()
    for i in range(len(vals)):
        if i + 1 < window:
            continue
        w = vals[i - window + 1 : i + 1]
        ref.iloc[i] = np.std(w, ddof=1) * math.sqrt(ppy)

    pd.testing.assert_series_equal(
        fast.dropna(), ref.dropna(), check_names=False, rtol=1e-12, atol=1e-12
    )


def test_rolling_sharpe_matches_reference_loop():
    """features.rolling_sharpe equals a plain trailing-window mean/std * sqrt(ppy) loop."""
    rets = _make_returns(n=300, seed=13)
    window = 30
    ppy = 365

    fast = features.rolling_sharpe(rets, window=window, periods_per_year=ppy)

    ref = pd.Series(np.nan, index=rets.index)
    vals = rets.to_numpy()
    for i in range(len(vals)):
        if i + 1 < window:
            continue
        w = vals[i - window + 1 : i + 1]
        sd = np.std(w, ddof=1)
        ref.iloc[i] = (np.mean(w) / sd) * math.sqrt(ppy) if sd > 0 else np.nan

    pd.testing.assert_series_equal(
        fast.dropna(), ref.dropna(), check_names=False, rtol=1e-12, atol=1e-12
    )


def test_aggregate_sharpe_matches_reference():
    """risk.sharpe equals the closed-form mean/std * sqrt(ppy)."""
    rets = _make_returns(n=400, seed=17)
    ppy = 365
    ref = float(rets.mean() / rets.std(ddof=1) * math.sqrt(ppy))
    assert risk.sharpe(rets, periods_per_year=ppy) == pytest.approx(ref, rel=1e-12)


# --------------------------------------------------------------------------- #
# 3. Deflated Sharpe < raw Sharpe, and decreasing in n_trials                   #
# --------------------------------------------------------------------------- #
def _period_sharpe_moments(rets: pd.Series) -> tuple[float, int, float, float]:
    """(per-period Sharpe, n, skew, non-excess kurtosis) for the DSR/PSR inputs."""
    r = rets.dropna()
    n = len(r)
    sr = float(r.mean() / r.std(ddof=1))
    sk = float(stats.skew(r.to_numpy(), bias=False))
    ku = float(stats.kurtosis(r.to_numpy(), fisher=False, bias=False))
    return sr, n, sk, ku


def test_deflated_sharpe_below_raw_and_decreasing_in_trials():
    """DSR(n_trials>1) < raw PSR(benchmark 0), and DSR strictly falls as N rises."""
    rets = _make_returns(n=500, seed=3)  # positive-mean -> positive raw Sharpe
    sr, n, sk, ku = _period_sharpe_moments(rets)
    var_sr = 1.0 / n

    raw_psr = risk.probabilistic_sharpe_ratio(sr, n, sk, ku, sr_benchmark=0.0)
    assert sr > 0, "fixture must have a positive Sharpe for a meaningful deflation"

    trials = [2, 5, 20, 100, 1000]
    dsrs = [risk.deflated_sharpe_ratio(sr, n, sk, ku, t, var_sr) for t in trials]

    # Every multi-trial DSR is strictly below the raw (no-selection) significance.
    for t, d in zip(trials, dsrs):
        assert d < raw_psr, f"DSR at n_trials={t} ({d}) should be < raw PSR ({raw_psr})"

    # And the more trials searched, the lower the deflated Sharpe (monotone).
    for earlier, later in zip(dsrs, dsrs[1:]):
        assert later < earlier, "DSR must decrease as n_trials increases"

    # At n_trials == 1 the benchmark is 0, so DSR collapses to the raw PSR.
    dsr_one = risk.deflated_sharpe_ratio(sr, n, sk, ku, 1, var_sr)
    assert dsr_one == pytest.approx(raw_psr, rel=1e-9)


def test_run_surfaces_deflated_sharpe_below_raw():
    """backtest.run threads n_trials so the reported DSR sits below the raw PSR."""
    ohlcv = _make_ohlcv(n=500, seed=7)
    prices = ohlcv["close"]
    pos = strategies.tsmom(ohlcv, lookback=20, vol_scaled=True)

    res1 = backtest.run(pos, prices, n_trials=1)
    res50 = backtest.run(pos, prices, n_trials=50)

    psr = res1["stats"]["psr"]
    dsr50 = res50["stats"]["deflated_sharpe"]
    # Only meaningful when the strategy actually has a positive in-sample edge.
    if res1["stats"]["sharpe_per_period"] > 0 and not math.isnan(psr):
        assert dsr50 <= psr + 1e-9
    # n_trials is recorded for the report.
    assert res50["stats"]["n_trials"] == 50


# --------------------------------------------------------------------------- #
# 4. Drawdown <= 0 and max_drawdown matches                                     #
# --------------------------------------------------------------------------- #
def test_drawdown_non_positive_and_max_matches():
    """drawdown is everywhere <= 0 and max_drawdown == its minimum (== reference)."""
    prices = _make_prices(n=400, seed=23)
    pos = strategies.buy_and_hold(pd.DataFrame({"close": prices}))
    res = backtest.run(pos, prices)
    equity = res["equity"]

    dd = features.drawdown(equity)
    # Allow a hair of float noise at the running-peak bars.
    assert (dd.dropna() <= 1e-12).all(), "drawdown must be <= 0 everywhere"

    mdd = features.max_drawdown(equity)
    assert mdd == pytest.approx(float(dd.min()))

    # Independent reference: equity / running cummax - 1.
    eq = equity.to_numpy()
    peak = np.maximum.accumulate(eq)
    ref_dd = eq / peak - 1.0
    assert mdd == pytest.approx(float(ref_dd.min()))
    assert ref_dd.max() <= 1e-12


def test_risk_max_drawdown_from_returns_matches_equity_path():
    """risk.max_drawdown (from returns) equals features.max_drawdown (from equity)."""
    rets = _make_returns(n=300, seed=29, mu=-0.0005)  # a drawdown-prone path
    equity = (1.0 + rets).cumprod()
    assert risk.max_drawdown(rets) == pytest.approx(features.max_drawdown(equity))
    assert risk.max_drawdown(rets) <= 0.0


# --------------------------------------------------------------------------- #
# 5. Every strategy output stays within [-1, 1]                                 #
# --------------------------------------------------------------------------- #
def _assert_in_unit_band(pos: pd.Series, name: str) -> None:
    """A valid backtester target weight: finite values must lie in [-1, 1]."""
    finite = pos.dropna().to_numpy()
    assert len(finite) > 0, f"{name}: produced no finite positions"
    assert np.all(finite >= -1.0 - 1e-9), f"{name}: position < -1"
    assert np.all(finite <= 1.0 + 1e-9), f"{name}: position > 1"


def test_all_strategies_within_unit_band():
    """buy_and_hold / ma_trend / dual-cross / tsmom variants / vol_target all in [-1,1]."""
    ohlcv = _make_ohlcv(n=600, seed=5)

    _assert_in_unit_band(strategies.buy_and_hold(ohlcv), "buy_and_hold")
    _assert_in_unit_band(strategies.ma_trend_filter(ohlcv, n=200), "ma_trend_filter")
    _assert_in_unit_band(
        strategies.ma_trend_filter(ohlcv, n=200, fast=50), "ma_trend_filter(dual)"
    )
    _assert_in_unit_band(
        strategies.tsmom(ohlcv, lookback=20, vol_scaled=False), "tsmom(raw)"
    )
    _assert_in_unit_band(
        strategies.tsmom(ohlcv, lookback=20, vol_scaled=True), "tsmom(vol-scaled)"
    )
    _assert_in_unit_band(
        strategies.tsmom(ohlcv, lookback=20, vol_scaled=True, long_short=True),
        "tsmom(long/short)",
    )
    # vol_target applied to a deliberately oversized signal must clip to [-1, 1].
    big = pd.Series(5.0, index=ohlcv.index)
    _assert_in_unit_band(strategies.vol_target(big, ohlcv, target_vol=0.5), "vol_target")


def test_carry_strategy_within_unit_band():
    """carry positions live in {-1, 0, +1} (perp-leg target weight)."""
    rng = np.random.default_rng(31)
    idx = pd.date_range("2021-01-01", periods=400, freq="8h", tz="UTC")
    # Funding swings positive and negative to exercise carry + inversion.
    rate = pd.Series(0.0005 * np.sin(np.linspace(0, 20, len(idx))), index=idx)
    funding = pd.DataFrame({"funding_rate": rate})
    pos = strategies.carry(funding)
    _assert_in_unit_band(pos, "carry")
    assert set(np.unique(pos.dropna().to_numpy())).issubset({-1.0, 0.0, 1.0})


def test_pairs_coint_strategy_within_unit_band():
    """pairs_coint positions live in {-1, 0, +1} (BTC-leg target weight)."""
    btc = _make_prices(n=400, seed=41)
    rng = np.random.default_rng(43)
    # ETH cointegrated-ish with BTC plus stationary noise around it.
    eth = pd.Series(
        btc.to_numpy() * 0.07 * np.exp(rng.normal(0, 0.01, len(btc))),
        index=btc.index,
    )
    pos = strategies.pairs_coint(btc, eth, window=60)
    _assert_in_unit_band(pos, "pairs_coint")
    assert set(np.unique(pos.dropna().to_numpy())).issubset({-1.0, 0.0, 1.0})


# --------------------------------------------------------------------------- #
# Part B research candidates (pre-registered; RESEARCH-partB-runlog.md)        #
# --------------------------------------------------------------------------- #
def test_ou_sigma_eq_finite_for_mean_reverting_inf_for_trending():
    """ou_sigma_eq (B2 normalizer): finite, positive equilibrium std for a
    mean-reverting AR(1); inf for a trending/explosive series (b >= 0, no finite
    stationary variance). NB: a pure random walk's finite-sample AR(1) fit is
    Dickey-Fuller biased toward *spurious* mean-reversion (finite half-life) — that
    non-stationarity trap is exactly what B2 is designed to expose, so it is not
    asserted as inf here; the run-log documents it as a finding."""
    rng = np.random.default_rng(11)
    n = 600
    x = np.zeros(n)
    for i in range(1, n):  # AR(1) phi=0.8 -> stationary, sigma_e=1
        x[i] = 0.8 * x[i - 1] + rng.normal(0.0, 1.0)
    s_mr = pd.Series(x)
    sig = features.ou_sigma_eq(s_mr)
    assert np.isfinite(sig) and sig > 0
    # Theoretical sigma_eq = sigma_e / sqrt(1 - phi^2) = 1 / sqrt(1 - 0.64) ~ 1.667.
    assert abs(sig - 1.0 / math.sqrt(1.0 - 0.64)) < 0.6
    assert np.isfinite(features.ou_half_life(s_mr))
    # Deterministic non-mean-reverting case: a bounded exponential trend (b > 0).
    trend = pd.Series(np.exp(np.linspace(0.0, 8.0, n)))
    assert math.isinf(features.ou_half_life(trend))
    assert math.isinf(features.ou_sigma_eq(trend))


def test_pairs_ou_within_unit_band_and_distinct_from_fixed_z():
    """pairs_ou stays in {-1,0,+1} and is a genuine variant of pairs_coint — the OU
    normalizer changes the thresholds, so the position series is not identical."""
    btc = _make_prices(n=400, seed=41)
    rng = np.random.default_rng(43)
    eth = pd.Series(btc.to_numpy() * 0.07 * np.exp(rng.normal(0, 0.01, len(btc))), index=btc.index)
    pos_ou = strategies.pairs_ou(btc, eth, window=60)
    pos_fz = strategies.pairs_coint(btc, eth, window=60)
    _assert_in_unit_band(pos_ou, "pairs_ou")
    assert set(np.unique(pos_ou.dropna().to_numpy())).issubset({-1.0, 0.0, 1.0})
    a = pos_ou.fillna(-9).to_numpy()
    b = pos_fz.reindex(pos_ou.index).fillna(-9).to_numpy()
    assert (a != b).any(), "pairs_ou must differ from fixed-z pairs (it is a distinct variant)"


def test_pairs_ou_is_causal_prefix_stable():
    """No look-ahead: positions computed on a prefix match the full-series positions
    over that prefix's settled region (rolling/OU stats use only trailing data)."""
    btc = _make_prices(n=300, seed=41)
    rng = np.random.default_rng(43)
    eth = pd.Series(btc.to_numpy() * 0.07 * np.exp(rng.normal(0, 0.01, len(btc))), index=btc.index)
    full = strategies.pairs_ou(btc, eth, window=60)
    k = 220
    pref = strategies.pairs_ou(btc.iloc[:k], eth.iloc[:k], window=60)
    lo, hi = 60, k - 1  # after warm-up, before the prefix end
    a = np.nan_to_num(full.iloc[lo:hi].to_numpy(), nan=-9.0)
    b = np.nan_to_num(pref.iloc[lo:hi].to_numpy(), nan=-9.0)
    assert np.allclose(a, b)


def test_tsmom_voltarget_is_bounded():
    """B1 sanity: the vol-target overlay on directional tsmom is a valid target
    weight in [-1, 1] (it composes already-tested pieces)."""
    df = _make_ohlcv(n=400, seed=8)
    raw = strategies.tsmom(df, lookback=20, vol_scaled=False, long_short=False)
    sized = strategies.vol_target(raw, df, target_vol=0.15, max_leverage=2.0)
    _assert_in_unit_band(sized, "tsmom_voltarget")


def test_short_vol_is_documented_stub():
    """short_vol must refuse to fabricate option data (honesty rail)."""
    with pytest.warns(UserWarning):
        with pytest.raises(NotImplementedError):
            strategies.short_vol()


# --------------------------------------------------------------------------- #
# 6. Option chain — OFFLINE parse / unit / skew / interpolation (no network)    #
# --------------------------------------------------------------------------- #
# These tests synthesize a Deribit ``get_book_summary_by_currency`` payload and
# monkeypatch ``data.http_get`` so nothing touches the network. They assert the
# brief-§1 contracts: instrument_name parse, the *_iv /100 unit fix (§1.2), the
# 08:00-UTC expiry parse (§1.5), the RR25 sign convention (§1.4d) and ATMF IV
# interpolation (§1.4b), plus graceful degrade on a network failure (§5).

# A near-fixed valuation time so T (ACT/365) is deterministic across the suite.
_OPT_NOW = pd.Timestamp("2025-06-01 00:00:00", tz="UTC")


def _synthetic_book_summary(forward: float = 30_000.0) -> list[dict]:
    """A deterministic ``get_book_summary_by_currency`` result for BTC options.

    Builds two expiries (a near ~30d and a far ~90d) on a put-skewed smile:
    OTM puts (K < F) carry a *higher* mark_iv than OTM calls (K > F), so the
    25-delta risk reversal ``IV(25dC) - IV(25dP)`` is negative (downside bid).
    ``mark_iv`` is emitted in **percent** (Deribit's convention) so the /100 fix
    is exercised. The far expiry sits at a higher ATM level (contango).
    """
    rows: list[dict] = []
    strikes = [20_000, 24_000, 27_000, 30_000, 33_000, 36_000, 42_000]
    # (date_token, atm_iv_percent) for the two expiries.
    expiries = [("01JUL25", 60.0), ("30AUG25", 70.0)]
    for date_token, atm_pct in expiries:
        for k in strikes:
            cp = "P" if k < forward else "C"
            # Put-skewed smile in PERCENT: puts above ATM, calls below ATM.
            log_m = math.log(k / forward)
            if k < forward:  # OTM put: richer the further OTM
                mark_iv = atm_pct + 14.0 * (-log_m)
            elif k > forward:  # OTM call: cheaper the further OTM
                mark_iv = atm_pct - 6.0 * log_m
            else:  # exactly ATM
                mark_iv = atm_pct
            # mid/bid/ask are present (so the smile gate keeps the contract).
            mid = max(50.0, 800.0 - 0.01 * abs(k - forward))
            rows.append(
                {
                    "instrument_name": f"BTC-{date_token}-{k}-{cp}",
                    "mark_iv": mark_iv,  # PERCENT
                    "open_interest": 100.0,
                    "volume": 10.0,
                    "underlying_price": forward,
                    "underlying_index": "btc_usd",
                    "mid_price": mid,
                    "bid_price": mid * 0.98,
                    "ask_price": mid * 1.02,
                    "mark_price": mid,
                }
            )
    # A non-option instrument that must be parsed-out (e.g. a future leaking in).
    rows.append(
        {
            "instrument_name": "BTC-PERPETUAL",
            "mark_iv": None,
            "open_interest": 1.0,
            "volume": 1.0,
            "underlying_price": forward,
            "underlying_index": "btc_usd",
            "mid_price": forward,
            "bid_price": forward,
            "ask_price": forward,
            "mark_price": forward,
        }
    )
    return rows


def test_option_instrument_name_parse():
    """instrument_name 'BTC-DDMMMYY-STRIKE-C/P' parses to (08:00-UTC expiry, K, cp)."""
    parsed = data._parse_option_instrument("BTC-27JUN25-100000-C")
    assert parsed is not None
    expiry, strike, cp = parsed
    assert (expiry.year, expiry.month, expiry.day) == (2025, 6, 27)
    # Expiry is pinned to 08:00:00 UTC (brief §1.5).
    assert (expiry.hour, expiry.minute, expiry.second) == (8, 0, 0)
    assert str(expiry.tz) == "UTC"
    assert strike == 100_000.0
    assert cp == "C"

    put = data._parse_option_instrument("BTC-1AUG25-50000-P")
    assert put is not None and put[2] == "P" and put[1] == 50_000.0
    assert put[0].day == 1 and put[0].month == 8

    # Non-options / malformed names parse to None (dropped, not crashed).
    assert data._parse_option_instrument("BTC-PERPETUAL") is None
    assert data._parse_option_instrument("BTC-27JUN25-100000-X") is None
    assert data._parse_option_instrument("garbage") is None


def test_option_chain_iv_unit_divided_by_100(monkeypatch):
    """mark_iv is PERCENT; the returned 'iv' column is the decimal (mark_iv/100)."""
    payload = {"result": _synthetic_book_summary()}
    monkeypatch.setattr(data, "http_get", lambda *a, **k: payload)

    chain = data.get_option_chain(currency="BTC", cache=False)
    assert not chain.empty
    # Every row: iv == mark_iv / 100 (the §1.2 unit fix), and iv is a sane decimal.
    valid = chain.dropna(subset=["iv", "mark_iv"])
    assert np.allclose(valid["iv"].to_numpy(), valid["mark_iv"].to_numpy() / 100.0)
    assert (valid["iv"] > 0.05).all() and (valid["iv"] < 5.0).all()
    # The non-option BTC-PERPETUAL row was dropped.
    assert not chain["instrument_name"].str.contains("PERPETUAL").any()
    # Expiries are 08:00 UTC and the columns are present + typed.
    assert (chain["expiry"].dt.hour == 8).all()
    for col in ("expiry", "strike", "opt_type", "iv", "underlying_price"):
        assert col in chain.columns


def test_option_chain_atm_iv_interpolation(monkeypatch):
    """atm_iv interpolates the OTM ladder at the forward to ~ the seeded ATM level."""
    payload = {"result": _synthetic_book_summary(forward=30_000.0)}
    monkeypatch.setattr(data, "http_get", lambda *a, **k: payload)
    chain = data.get_option_chain(currency="BTC", cache=False)

    near = chain["expiry"].min()
    iv_atm = features.atm_iv(chain, near, now=_OPT_NOW)
    # The near expiry was seeded with a 60% ATM level (decimal 0.60).
    assert iv_atm == pytest.approx(0.60, abs=0.02)


def test_option_term_structure_and_total_variance_30d(monkeypatch):
    """iv_term_structure returns ATM IV vs T, ACT/365, sorted, T>0; far ATM > near."""
    payload = {"result": _synthetic_book_summary()}
    monkeypatch.setattr(data, "http_get", lambda *a, **k: payload)
    chain = data.get_option_chain(currency="BTC", cache=False)

    term = features.iv_term_structure(chain, now=_OPT_NOW)
    assert list(term.columns) == ["expiry", "T", "atm_iv"]
    assert len(term) == 2
    # Sorted ascending in T, all positive (already-expired dropped).
    assert term["T"].is_monotonic_increasing
    assert (term["T"] > 0).all()
    # Seeded contango: the far expiry's ATM IV is higher than the near's.
    assert term["atm_iv"].iloc[-1] > term["atm_iv"].iloc[0]
    # ACT/365 sanity: ~30 days to 01JUL25 from 01JUN25 -> T ~= 30/365.
    assert term["T"].iloc[0] == pytest.approx(30.0 / 365.0, abs=2.0 / 365.0)


def test_option_skew_25d_sign_convention(monkeypatch):
    """RR25 = IV(25dC) - IV(25dP) < 0 on a put-skewed smile (downside bid)."""
    payload = {"result": _synthetic_book_summary()}
    monkeypatch.setattr(data, "http_get", lambda *a, **k: payload)
    chain = data.get_option_chain(currency="BTC", cache=False)

    near = chain["expiry"].min()
    rr = features.iv_skew_25d(chain, near, now=_OPT_NOW)
    assert not math.isnan(rr)
    # Puts richer than calls -> call-minus-put risk reversal is negative.
    assert rr < 0.0, f"put-skewed smile must give RR25 < 0, got {rr}"


def test_option_smile_gate_otm_only(monkeypatch):
    """smile keeps OTM-only, gated points; OTM puts have strike <= F, calls >= F."""
    payload = {"result": _synthetic_book_summary(forward=30_000.0)}
    monkeypatch.setattr(data, "http_get", lambda *a, **k: payload)
    chain = data.get_option_chain(currency="BTC", cache=False)

    near = chain["expiry"].min()
    sm = features.smile(chain, near, x="log_moneyness", now=_OPT_NOW)
    assert not sm.empty
    assert {"strike", "x", "iv", "opt_type"}.issubset(sm.columns)
    # OTM-only: every put strike <= F and every call strike >= F.
    f = 30_000.0
    puts = sm[sm["opt_type"] == "P"]
    calls = sm[sm["opt_type"] == "C"]
    assert (puts["strike"] <= f).all()
    assert (calls["strike"] >= f).all()
    # iv stays a sane decimal and log-moneyness is signed about the forward.
    assert (sm["iv"] > 0).all() and (sm["iv"] < 5.0).all()


def test_option_chain_degrades_on_network_failure(monkeypatch, tmp_path):
    """get_option_chain must not crash when Deribit is unreachable and no cache.

    With cache disabled it raises a clear DataError (never fabricates); with a
    cache present it degrades to the stale snapshot with a warning.
    """
    def _boom(*a, **k):
        raise data.DataError("simulated network failure")

    monkeypatch.setattr(data, "http_get", _boom)

    # No cache -> a clear DataError (not a crash, not fabricated data).
    with pytest.raises(data.DataError):
        data.get_option_chain(currency="BTC", cache=False)

    # Now seed a cache from a good payload, then fail the network: must degrade.
    good = {"result": _synthetic_book_summary()}
    monkeypatch.setattr(data, "http_get", lambda *a, **k: good)
    monkeypatch.setattr(data, "DATA_DIR", tmp_path)
    fresh = data.get_option_chain(currency="BTC", cache=True)
    assert not fresh.empty

    monkeypatch.setattr(data, "http_get", _boom)
    with pytest.warns(UserWarning):
        stale = data.get_option_chain(currency="BTC", cache=True)
    # Stale cache reload reproduces the parsed snapshot (same contract count).
    assert len(stale) == len(fresh)
    assert (stale["expiry"].dt.hour == 8).all()


# --------------------------------------------------------------------------- #
# OOS validation harness — walk-forward, PBO (CSCV), MinBTL, CPCV               #
# (RESEARCH.md §3: the selection-bias / overfitting machinery)                 #
# --------------------------------------------------------------------------- #
def test_min_backtest_length_monotone_and_guards():
    """MinBTL is NaN for N<2, finite for N>=2, and strictly increases with N
    (more trials searched -> longer history needed to trust the winner)."""
    assert math.isnan(risk.min_backtest_length(1))
    vals = [risk.min_backtest_length(n) for n in (2, 5, 20, 100, 500)]
    assert all(math.isfinite(v) and v > 0 for v in vals)
    assert vals == sorted(vals) and len(set(vals)) == len(vals)  # strictly increasing


def test_pbo_noise_is_near_half_and_real_edge_is_low():
    """CSCV PBO ~ 0.5 when columns are pure noise (selection is a coin flip),
    and low when one column carries a persistent edge present in every split."""
    rng = np.random.default_rng(7)
    noise = rng.normal(0.0, 0.01, size=(800, 6))
    pbo_noise = risk.probability_of_backtest_overfitting(noise, n_blocks=8)
    assert pbo_noise["n_combos"] == math.comb(8, 4)          # C(S, S/2)
    assert 0.0 <= pbo_noise["pbo"] <= 1.0
    assert abs(pbo_noise["pbo"] - 0.5) < 0.2                 # no real winner -> ~half

    edged = noise.copy()
    edged[:, 0] += 0.003                                     # a persistent winner
    pbo_edge = risk.probability_of_backtest_overfitting(edged, n_blocks=8)
    assert pbo_edge["pbo"] < pbo_noise["pbo"]                # robust selection
    assert pbo_edge["pbo"] < 0.2

    # Degenerate input (single strategy) -> NaN, never a crash.
    assert math.isnan(risk.probability_of_backtest_overfitting(noise[:, :1])["pbo"])


def test_walk_forward_is_out_of_sample_and_folds_are_trials():
    """walk_forward returns IS/OOS bundles, treats each fold as a trial for the OOS
    Deflated Sharpe, and routes OOS through backtest.run (so no-look-ahead holds)."""
    px = _make_prices(n=900, seed=11)
    make_pos = lambda p: (p > p.rolling(50).mean()).astype(float)
    wf = backtest.walk_forward(make_pos, px, n_splits=5)
    assert set(("oos", "is_", "folds", "oos_equity", "oos_returns")) <= set(wf)
    assert len(wf["folds"]) == 5
    assert len(wf["oos_returns"]) > 0
    assert wf["oos"]["n_trials"] == 5                        # folds-as-trials
    assert 0.0 <= wf["oos"]["deflated_sharpe"] <= 1.0
    # OOS window is strictly later than the first in-sample bar (held out, not refit).
    assert wf["oos_returns"].index[0] > px.index[0]


def test_cpcv_multipath_dispersion():
    """CPCV yields C(n_blocks, k_test) OOS paths with a finite dispersion and a
    non-negative IQR — the multi-path headline, not a single curve."""
    px = _make_prices(n=900, seed=3)
    make_pos = lambda p: (p > p.rolling(30).mean()).astype(float)
    cp = backtest.cpcv(make_pos, px, n_blocks=6, k_test=2)
    assert cp["n_paths"] == math.comb(6, 2)                  # 15 paths
    assert math.isfinite(cp["median_sharpe"]) and cp["iqr"] >= 0.0
    assert cp["min"] <= cp["median_sharpe"] <= cp["max"]


# --------------------------------------------------------------------------- #
# Options structural analytics (black76_greeks / max_pain / gamma_concentration)
# --------------------------------------------------------------------------- #
def _make_option_chain(strikes, fwd=65000.0, iv=0.6, oi=100.0, days=30):
    """Minimal synthetic Deribit-style chain (one expiry, calls+puts at each strike)."""
    exp = pd.Timestamp.now(tz="UTC").normalize() + pd.Timedelta(days=days)
    rows = []
    for k in strikes:
        for cp in ("C", "P"):
            rows.append({"instrument_name": f"BTC-X-{int(k)}-{cp}", "expiry": exp,
                         "strike": float(k), "opt_type": cp, "iv": iv, "mark_iv": iv * 100,
                         "open_interest": oi, "volume": 0.0, "underlying_price": fwd})
    return pd.DataFrame(rows), exp


def test_black76_greeks_identities():
    """Black-76 greeks obey the textbook identities (the math the validation gate checks
    against Deribit): put-call delta parity, gamma = ∂delta/∂F, vega ≥ 0, ATM delta."""
    F, K, iv, t = 65000.0, 65000.0, 0.6, 0.25
    c = features.black76_greeks(F, K, iv, t, "C")
    p = features.black76_greeks(F, K, iv, t, "P")
    # put-call delta parity (r=0): delta_call - delta_put == 1
    assert abs((c["delta"] - p["delta"]) - 1.0) < 1e-12
    # gamma identical for call/put; vega ≥ 0; gamma ≥ 0
    assert abs(c["gamma"] - p["gamma"]) < 1e-15
    assert c["vega"] >= 0.0 and c["gamma"] >= 0.0
    # ATM (K=F): d1 = 0.5σ√t, so call delta = Φ(0.5σ√t)
    expect = float(features._norm_cdf(np.array([0.5 * iv * math.sqrt(t)]))[0])
    assert abs(c["delta"] - expect) < 1e-9
    # gamma == numerical ∂delta/∂F (central difference)
    h = 1.0
    dd = (features.black76_greeks(F + h, K, iv, t, "C")["delta"]
          - features.black76_greeks(F - h, K, iv, t, "C")["delta"]) / (2 * h)
    assert abs(c["gamma"] - dd) < 1e-7
    # degenerate inputs → nan, never a spurious number
    assert math.isnan(features.black76_greeks(F, K, iv, 0.0, "C")["gamma"])


def test_max_pain_minimizes_holder_payout():
    """max_pain is the settlement strike minimizing total intrinsic to holders; with all
    OI piled on one strike it IS that strike (pain there = 0). P/C ratio is reported."""
    chain, exp = _make_option_chain([50000, 60000, 65000, 70000, 80000], oi=0.0)
    # pile all OI at 60000 (both legs) → pain(60000) = 0 → max_pain == 60000
    chain.loc[chain["strike"] == 60000, "open_interest"] = 500.0
    mp = features.max_pain(chain, exp)
    assert mp["max_pain"] == 60000.0
    assert len(mp["strikes"]) == 5 and len(mp["call_oi"]) == 5
    # equal call/put OI everywhere → P/C ratio == 1
    chain2, exp2 = _make_option_chain([60000, 65000, 70000], oi=100.0)
    assert abs(features.max_pain(chain2, exp2)["pc_oi_ratio"] - 1.0) < 1e-12


def test_gamma_concentration_peaks_near_atm_and_is_unsigned():
    """Σ|gamma|·OI by strike is non-negative and peaks at the near-ATM strike (gamma is
    largest ATM) when OI is uniform — a density, never a signed/dealer quantity."""
    strikes = [40000, 55000, 65000, 75000, 90000]
    chain, exp = _make_option_chain(strikes, fwd=65000.0, oi=100.0)
    gc = features.gamma_concentration(chain, exp)
    assert gc["strikes"] and all(v >= 0.0 for v in gc["gamma_oi"])      # unsigned
    peak_strike = gc["strikes"][int(np.argmax(gc["gamma_oi"]))]
    assert peak_strike == 65000.0                                       # ATM has the most gamma
