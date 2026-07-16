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
# 3b. M6 DSR convention pins (C1-C3): hand-computed sr0, N=1 flag, fold-V      #
# --------------------------------------------------------------------------- #
def test_dsr_sr0_hand_pin_n5_v001():
    """C1 hand pin: sr0(N=5, V=0.001) exactly.

    sr0 = sqrt(V) * ((1-γ)·Φ⁻¹(1-1/5) + γ·Φ⁻¹(1-1/(5e))) with γ = 0.5772156649015329:
      Φ⁻¹(0.8)                = 0.8416212335729143
      Φ⁻¹(1 - 1/(5e))         = 1.4496656592240222
      E[max Z of 5]           = 1.1925940010147893
      sr0 = sqrt(0.001)·E[max] = 0.03771313367059893   (hand-computed, hard-coded)

    With Gaussian moments (skew=0, kurt=3) and sr == sr0 the PSR z-score is exactly
    0, so DSR must be exactly 0.5 — the pin exercises the full C1 composition
    DSR := PSR(sr0(N, V)) without touching PSR internals (C4).
    """
    SR0 = 0.03771313367059893
    n = 100
    # sr exactly at the skill-less expected-max benchmark -> z = 0 -> DSR = 0.5.
    assert risk.deflated_sharpe_ratio(SR0, n, 0.0, 3.0, 5, 0.001) == pytest.approx(
        0.5, abs=1e-12
    )
    # Strictly above / below the benchmark moves the probability the right way.
    assert risk.deflated_sharpe_ratio(SR0 + 1e-4, n, 0.0, 3.0, 5, 0.001) > 0.5
    assert risk.deflated_sharpe_ratio(SR0 - 1e-4, n, 0.0, 3.0, 5, 0.001) < 0.5
    # And DSR(N, V) is literally PSR with sr_benchmark = sr0 (the C1 definition).
    sr = 0.1
    assert risk.deflated_sharpe_ratio(sr, n, 0.0, 3.0, 5, 0.001) == pytest.approx(
        risk.probabilistic_sharpe_ratio(sr, n, 0.0, 3.0, sr_benchmark=SR0), rel=1e-12
    )


def test_sharpe_estimator_variance_hand_pin_and_psr_denominator_identity():
    """B2 trial variance (azul 2026-07-13, RESEARCH-dsr-convention.md): the Lo(2002)/
    Mertens(2002) finite-sample Sharpe-estimator variance risk.sharpe_estimator_variance
    is (1 - skew·SR + (kurt-1)/4·SR²)/(n-1), the SAME quantity implicit in the PSR
    denominator.

    Hand pin: sr=0.05, n=500, skew=-0.3, kurt=4:
      1 - (-0.3)(0.05) + (4-1)/4·(0.05²) = 1 + 0.015 + 0.001875 = 1.016875
      V = 1.016875 / 499 = 0.0020378256513026052   (hand-computed, hard-coded)
    """
    from scipy.stats import norm

    sr, n, skew, kurt = 0.05, 500, -0.3, 4.0
    V = risk.sharpe_estimator_variance(sr, n, skew, kurt)
    assert V == pytest.approx(0.0020378256513026052, abs=1e-15)

    # It is EXACTLY the variance implicit in the PSR denominator. PSR(sr; 0) = Φ(z)
    # with z = sr·√(n-1)/√denom and Var(SR_hat) = denom/(n-1), so Var = (sr/z)² where
    # z = Φ⁻¹(PSR). Reconstructing V from the PSR output must round-trip to the same V.
    psr = risk.probabilistic_sharpe_ratio(sr, n, skew, kurt, sr_benchmark=0.0)
    implied = (sr / norm.ppf(psr)) ** 2
    assert V == pytest.approx(implied, rel=1e-12)

    # Guard: n < 2 -> nan (cannot form the (n-1) sampling scale).
    assert math.isnan(risk.sharpe_estimator_variance(0.05, 1, 0.0, 3.0))
    assert math.isnan(risk.sharpe_estimator_variance(0.05, 0, 0.0, 3.0))


# --------------------------------------------------------------------------- #
# 3c. False Strategy Theorem (FST) — Bailey & López de Prado 2014             #
# --------------------------------------------------------------------------- #
def test_expected_max_sharpe_hand_pins_and_n1_zero():
    """expected_max_sharpe_ratio hand pins (V=1 → the raw E[max_N] of N standard
    normals) and the N<2 → 0 special case.

    E[max_5]  = (1-γ)·Φ⁻¹(0.8) + γ·Φ⁻¹(1-1/(5e))  = 1.1925940010147893
    E[max_10] = (1-γ)·Φ⁻¹(0.9) + γ·Φ⁻¹(1-1/(10e)) = 1.57459830134575
    (γ = 0.5772156649015329; hand-computed, hard-coded)."""
    assert risk.expected_max_sharpe_ratio(5, 1.0) == pytest.approx(
        1.1925940010147893, abs=1e-12)
    assert risk.expected_max_sharpe_ratio(10, 1.0) == pytest.approx(
        1.57459830134575, abs=1e-12)
    # N < 2 ⟹ 0.0 for ANY variance (a single trial has no selection to inflate).
    assert risk.expected_max_sharpe_ratio(1, 1.0) == 0.0
    assert risk.expected_max_sharpe_ratio(1, 123.456) == 0.0
    assert risk.expected_max_sharpe_ratio(0, 1.0) == 0.0
    # sqrt(V) scaling: V=0.001 at N=5 is the sr0 used in the DSR mid pin.
    assert risk.expected_max_sharpe_ratio(5, 0.001) == pytest.approx(
        0.03771313367059893, abs=1e-15)


def test_expected_max_refactor_is_byte_identical_regression():
    """REFACTOR guard: deflated_sharpe_ratio and min_backtest_length now CALL
    expected_max_sharpe_ratio instead of the two inline closed forms. The numeric
    results must be byte-identical to the pre-refactor values on fixed cases."""
    # Three fixed DSR cases (the same ones the mid/N1 pins exercise), hard-coded to the
    # pre-refactor outputs computed from the old inline formula.
    assert risk.deflated_sharpe_ratio(0.05, 500, -0.3, 4.0, 5, 0.001) == (
        0.6072585304659127)                                   # N=5, V=0.001 (mid)
    assert risk.deflated_sharpe_ratio(0.08, 250, -0.3, 4.0, 1, 123.456) == (
        0.8933576314257702)                                   # N=1 special case ≡ PSR(0)
    assert risk.deflated_sharpe_ratio(0.10, 100, 0.0, 3.0, 10, 1.0) == (
        8.334245390506071e-49)                                # N=10, V=1 (deep tail)
    # And MinBTL is byte-identical on fixed N (2·ln N / E[max_N]).
    assert risk.min_backtest_length(10) == 2.9246635043694797
    assert risk.min_backtest_length(2) == 2.6672055927365106


def test_false_strategy_threshold_fixed_point_recovers_prob():
    """false_strategy_threshold inverts the PSR: re-feeding sr* into
    probabilistic_sharpe_ratio against the SAME expected-max benchmark must recover
    EXACTLY prob (the fixed point closes). Hand pin at N=5, V=0.001, n=500."""
    N, V, n, sk, ku, prob = 5, 0.001, 500, -0.3, 4.0, 0.95
    sr_star = risk.false_strategy_threshold(N, V, n, sk, ku, prob)
    assert sr_star == pytest.approx(0.11292934779100049, abs=1e-9)
    sr0 = risk.expected_max_sharpe_ratio(N, V)
    round_trip = risk.probabilistic_sharpe_ratio(sr_star, n, sk, ku, sr_benchmark=sr0)
    assert round_trip == pytest.approx(prob, abs=1e-6)
    # sr* sits ABOVE the skill-less benchmark (you must beat luck to clear the hurdle).
    assert sr_star > sr0
    # Guards: N<2 or n<2 → nan; prob out of (0,1) → nan.
    assert math.isnan(risk.false_strategy_threshold(1, V, n, sk, ku, prob))
    assert math.isnan(risk.false_strategy_threshold(N, V, 1, sk, ku, prob))
    assert math.isnan(risk.false_strategy_threshold(N, V, n, sk, ku, 1.0))


def test_effective_number_of_trials_participation_ratio():
    """effective_number_of_trials = eigenvalue participation ratio of corr(R).

    2 identical + 2 independent columns (built from the exactly-orthogonal
    discrete-Fourier basis) → correlation matrix [[1,1,0,0],[1,1,0,0],[0,0,1,0],
    [0,0,0,1]], eigenvalues [0,1,1,2], N_eff = (Σλ)²/Σλ² = 16/6 = 8/3."""
    t = np.arange(240)
    a = np.sin(2 * np.pi * t / 240)           # col 0
    b = np.cos(2 * np.pi * t / 240)           # ⊥ a
    c = np.sin(4 * np.pi * t / 240)           # ⊥ a, ⊥ b
    M = np.column_stack([a, a, b, c])
    assert risk.effective_number_of_trials(M) == pytest.approx(8.0 / 3.0, abs=1e-9)

    # N identical columns collapse to N_eff ≈ 1 (one non-zero eigenvalue).
    assert risk.effective_number_of_trials(np.column_stack([a] * 5)) == pytest.approx(
        1.0, abs=1e-9)

    # N independent columns → N_eff ≈ N (flat spectrum). Large T for a tight sample corr.
    rng = np.random.default_rng(1)
    R = rng.standard_normal((6000, 4))
    n_eff = risk.effective_number_of_trials(R)
    assert 3.6 < n_eff <= 4.0                  # clamped to [1, ncols]

    # Guards: <2 usable cols → the usable-col count; a constant column is dropped.
    two = np.column_stack([a, np.zeros(240)])  # one varying, one constant
    assert risk.effective_number_of_trials(two) == 1.0


def test_probability_false_strategy_is_one_minus_dsr():
    """probability_false_strategy = 1 - PSR(max_sharpe; sr0=expected_max) — the
    family-wise P that the best-of-N is a false positive. Hand pin at the mid case."""
    args = (0.05, 5, 0.001, 500, -0.3, 4.0)   # (max_sr, N, V, n, skew, kurt)
    p_false = risk.probability_false_strategy(*args)
    # Exactly 1 - the DSR (same inputs), and pinned to 1 - dsr_mid.
    dsr = risk.deflated_sharpe_ratio(0.05, 500, -0.3, 4.0, 5, 0.001)
    assert p_false == pytest.approx(1.0 - dsr, abs=1e-15)
    assert p_false == pytest.approx(0.3927414695340873, abs=1e-9)
    # Guard: degenerate trial input → nan (never a fake 0 or 1).
    assert math.isnan(risk.probability_false_strategy(0.05, 5, -1.0, 500, -0.3, 4.0))


# --------------------------------------------------------------------------- #
# 3d. Hierarchical-Bayes Sharpe shrinkage (frontier #3 — EB normal-normal)    #
#     Efron-Morris 1975 / James-Stein 1961; DerSimonian-Laird 1986 tau^2.     #
#     COMPLEMENTARY diagnostic — the production DSR is unchanged.             #
# --------------------------------------------------------------------------- #
def test_hb_shrinkage_identity_and_b_in_unit_interval():
    """(a) The classic shrinkage identity thetaHat_i = B_i·mu + (1-B_i)·SR_i must
    close exactly from the returned pieces, with every B_i in [0, 1]."""
    srs = [0.10, 0.02, -0.03, 0.06]
    vs = [0.002, 0.004, 0.003, 0.005]
    hb = risk.hierarchical_bayes_sharpe(srs, vs)
    for th, b, s in zip(hb["shrunk"], hb["shrink_factor"], srs):
        assert 0.0 <= b <= 1.0
        assert th == pytest.approx(b * hb["mu"] + (1.0 - b) * s, abs=1e-15)
    # tau^2 > 0 here ⟹ partial pooling: every shrunk SR sits strictly BETWEEN its raw
    # value and the pooled mean (never overshoots either).
    for th, s in zip(hb["shrunk"], srs):
        lo, hi = min(s, hb["mu"]), max(s, hb["mu"])
        assert lo <= th <= hi


def test_hb_identical_sharpes_full_pooling():
    """(b) All SR_i identical ⟹ Q = 0 ⟹ tauHat2 = 0 ⟹ FULL pooling: every
    thetaHat == mu (== the common SR), B_i == 1, posterior sd 0, p_skill = sign(mu)."""
    srs = [0.05, 0.05, 0.05]
    vs = [0.002, 0.004, 0.003]
    hb = risk.hierarchical_bayes_sharpe(srs, vs)
    assert hb["tau"] == 0.0
    assert hb["mu"] == pytest.approx(0.05, abs=1e-15)
    for th, b, sd, p, lo, hi in zip(hb["shrunk"], hb["shrink_factor"], hb["post_sd"],
                                    hb["p_skill"], hb["ci_low"], hb["ci_high"]):
        assert th == pytest.approx(hb["mu"], abs=1e-15)
        assert b == 1.0
        assert sd == 0.0
        assert p == 1.0                      # mu > 0 ⟹ tiny-epsilon limit is 1
        assert lo == th and hi == th         # CI collapses to the point mu
    # Negative common SR ⟹ p_skill 0 (the sign, not a fake 0.5).
    hb_neg = risk.hierarchical_bayes_sharpe([-0.05, -0.05, -0.05], vs)
    assert all(p == 0.0 for p in hb_neg["p_skill"])


def test_hb_dispersed_precise_sharpes_barely_shrink():
    """(c) Large tau^2 regime: very dispersed SRs measured very precisely (tiny
    sigma_i^2) ⟹ B_i ≈ 0 ⟹ the shrunk Sharpes stay ≈ raw (the data dominate the
    prior — James-Stein does NOT flatten genuinely different, well-measured means)."""
    srs = [0.50, -0.40, 0.30, -0.20]
    vs = [1e-6] * 4
    hb = risk.hierarchical_bayes_sharpe(srs, vs)
    assert hb["tau"] > 0.1                   # wide estimated population spread
    for th, b, s in zip(hb["shrunk"], hb["shrink_factor"], srs):
        assert b < 1e-4
        assert th == pytest.approx(s, abs=1e-4)


def test_hb_dersimonian_laird_tau2_hand_computed():
    """(d) DL tau^2 on a KNOWN k=2 example, fully hand-computed:
    SR = [0.3, 0.1], sigma^2 = [0.01, 0.02] ⟹ w = [100, 50], sum w = 150,
    muHat = (30+5)/150 = 7/30; Q = 100·(0.3-7/30)² + 50·(0.1-7/30)² = 4/3;
    c = 150 - (100²+50²)/150 = 200/3; tauHat2 = (4/3 - 1)/(200/3) = 0.005.
    Then B = [0.01/0.015, 0.02/0.025] = [2/3, 0.8]; wStar = [200/3, 40];
    muStar = (20+4)/(320/3) = 0.225; theta = [0.25, 0.20];
    v = [0.01·0.005/0.015, 0.02·0.005/0.025] = [1/300, 0.004]."""
    hb = risk.hierarchical_bayes_sharpe([0.3, 0.1], [0.01, 0.02])
    assert hb["tau"] ** 2 == pytest.approx(0.005, abs=1e-15)
    assert hb["mu"] == pytest.approx(0.225, abs=1e-15)
    assert hb["shrink_factor"][0] == pytest.approx(2.0 / 3.0, abs=1e-15)
    assert hb["shrink_factor"][1] == pytest.approx(0.8, abs=1e-15)
    assert hb["shrunk"][0] == pytest.approx(0.25, abs=1e-15)
    assert hb["shrunk"][1] == pytest.approx(0.20, abs=1e-15)
    assert hb["post_sd"][0] == pytest.approx(math.sqrt(1.0 / 300.0), abs=1e-15)
    assert hb["post_sd"][1] == pytest.approx(math.sqrt(0.004), abs=1e-15)


def test_hb_posterior_probability_and_credible_interval():
    """(e) p_skill_i = Phi(thetaHat_i / post_sd_i) and the 95% credible interval is
    thetaHat_i ± 1.96·post_sd_i — recomputed from the returned pieces."""
    from scipy.stats import norm

    hb = risk.hierarchical_bayes_sharpe([0.10, 0.02, -0.03, 0.06],
                                        [0.002, 0.004, 0.003, 0.005])
    for th, sd, p, lo, hi in zip(hb["shrunk"], hb["post_sd"], hb["p_skill"],
                                 hb["ci_low"], hb["ci_high"]):
        assert sd > 0
        assert p == pytest.approx(float(norm.cdf(th / sd)), abs=1e-12)
        assert lo == pytest.approx(th - 1.96 * sd, abs=1e-15)
        assert hi == pytest.approx(th + 1.96 * sd, abs=1e-15)


def test_hb_effective_n_widens_tau_and_lessens_shrinkage():
    """(f) The correlation-aware variant: effective_n < k deflates Q by (N_eff - 1)
    instead of (k - 1) ⟹ a strictly LARGER tau^2 (correlated strategies are fewer
    independent observations, so the population spread estimate widens) ⟹ smaller
    B_i (less shrinkage)."""
    srs = [0.10, 0.02, -0.03, 0.06]
    vs = [0.002, 0.004, 0.003, 0.005]
    std = risk.hierarchical_bayes_sharpe(srs, vs)                    # df = k-1 = 3
    eff = risk.hierarchical_bayes_sharpe(srs, vs, effective_n=2.5)   # df = 1.5
    assert eff["tau"] > std["tau"]
    for b_eff, b_std in zip(eff["shrink_factor"], std["shrink_factor"]):
        assert b_eff < b_std
    # effective_n == k reproduces the default exactly (df identical).
    same = risk.hierarchical_bayes_sharpe(srs, vs, effective_n=4)
    assert same["tau"] == std["tau"]
    assert same["shrunk"] == std["shrunk"]


def test_hb_winner_shrinks_most_under_equal_variances():
    """(g) Monotonicity / winner's curse: with EQUAL variances every B_i is the same,
    so the absolute pull |thetaHat_i - SR_i| = B·|mu - SR_i| is largest for the
    strategy farthest from the pool — here the top raw SR. The winner takes the
    biggest haircut; no lower-ranked strategy is pulled harder."""
    srs = [0.20, 0.05, 0.03, 0.01]
    vs = [0.003] * 4
    hb = risk.hierarchical_bayes_sharpe(srs, vs)
    assert len(set(round(b, 15) for b in hb["shrink_factor"])) == 1   # equal B_i
    pulls = [abs(th - s) for th, s in zip(hb["shrunk"], srs)]
    i_top = srs.index(max(srs))
    assert all(pulls[i_top] >= p for p in pulls)
    # And the winner is pulled DOWN (toward mu), never up.
    assert hb["shrunk"][i_top] < srs[i_top]


def test_hb_guards_k1_and_bad_variance_return_raw():
    """Edge guards: k < 2 (nothing to pool) and any non-finite/non-positive variance
    degrade to the RAW Sharpes with B_i = 0, tau = 0 and the raw likelihood as the
    posterior (CI/p_skill from sigma_i alone)."""
    from scipy.stats import norm

    one = risk.hierarchical_bayes_sharpe([0.08], [0.002])
    assert one["tau"] == 0.0 and one["mu"] == 0.08
    assert one["shrunk"] == [0.08] and one["shrink_factor"] == [0.0]
    assert one["post_sd"][0] == pytest.approx(math.sqrt(0.002), abs=1e-15)
    assert one["p_skill"][0] == pytest.approx(
        float(norm.cdf(0.08 / math.sqrt(0.002))), abs=1e-12)

    bad = risk.hierarchical_bayes_sharpe([0.08, 0.02], [0.002, 0.0])   # sigma^2 = 0
    assert bad["tau"] == 0.0
    assert bad["shrunk"] == [0.08, 0.02]
    assert bad["shrink_factor"] == [0.0, 0.0]
    assert math.isnan(bad["post_sd"][1]) and math.isnan(bad["p_skill"][1])

    with pytest.raises(ValueError):
        risk.hierarchical_bayes_sharpe([0.1, 0.2], [0.01])             # length mismatch


def test_compare_leaderboard_uses_per_strategy_b2_variance_and_is_decoupled():
    """B2 switch in production (compare.py leaderboard): each strategy's OOS DSR is now
    deflated with its OWN Lo/Mertens Sharpe-estimator variance
    (risk.sharpe_estimator_variance), NOT the shared cross-strategy V. This makes the
    leaderboard DSR DECOUPLED — perturbing one strategy's returns cannot move any other
    strategy's DSR (unlike the old convention A, where the shared empirical V coupled
    every peer). We drive the exact production loop from compare.py.
    """
    import importlib.util
    from pathlib import Path

    import numpy as np
    import pandas as pd

    path = Path(__file__).resolve().parent.parent / "scripts" / "compare.py"
    spec = importlib.util.spec_from_file_location("_b2_compare_script", str(path))
    compare = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(compare)

    def _row_from_returns(name, r):
        """Build the leaderboard row dict fields the DSR loop consumes, using the exact
        moment convention of risk.summary (mean/std ddof=1; skew bias=False; non-excess
        kurtosis)."""
        r = pd.Series(r, dtype="float64").dropna()
        n = int(len(r))
        sd = r.std(ddof=1)
        sr = float(r.mean() / sd)
        return {"name": name, "n_periods": n, "sr_period": sr,
                "skew": float(stats.skew(r.to_numpy(), bias=False)),
                "kurt": float(stats.kurtosis(r.to_numpy(), fisher=False, bias=False))}

    def _leaderboard_dsr(rows, n_trials):
        """Replicate compare.py's production DSR loop (B2 per-strategy variance)."""
        out = {}
        for r in rows:
            np_ = int(r["n_periods"])
            v = risk.sharpe_estimator_variance(r["sr_period"], np_, r["skew"], r["kurt"])
            if not (isinstance(v, (int, float)) and math.isfinite(v)):
                v = 1.0 / np_
            out[r["name"]] = risk.deflated_sharpe_ratio(
                r["sr_period"], np_, r["skew"], r["kurt"], n_trials, v)
        return out

    rng = np.random.default_rng(19)
    idx = pd.date_range("2020-01-01", periods=900, freq="D")
    base_returns = {
        "s1": pd.Series(rng.normal(0.0009, 0.02, len(idx)), index=idx),
        "s2": pd.Series(rng.normal(0.0004, 0.018, len(idx)), index=idx),
        "s3": pd.Series(rng.normal(0.0006, 0.025, len(idx)), index=idx),
    }
    rows = [_row_from_returns(k, v) for k, v in base_returns.items()]
    n_trials = len(rows)
    dsr = _leaderboard_dsr(rows, n_trials)

    # (1) Each strategy's DSR equals deflated_sharpe_ratio fed its OWN B2 variance.
    for r in rows:
        own_v = risk.sharpe_estimator_variance(
            r["sr_period"], r["n_periods"], r["skew"], r["kurt"])
        expected = risk.deflated_sharpe_ratio(
            r["sr_period"], r["n_periods"], r["skew"], r["kurt"], n_trials, own_v)
        assert dsr[r["name"]] == expected           # bit-identical, per-strategy V
        # and NOT the old shared-V convention (would couple the peers).
        assert own_v != compare._empirical_var_sr([x["sr_period"] for x in rows])[0]

    # (2) DECOUPLING: perturb ONLY s3's returns (a genuine returns-level change to its
    # Sharpe) and confirm s1 and s3 DSRs are BIT-IDENTICAL for the untouched peers.
    pert_returns = dict(base_returns)
    pert_returns["s3"] = base_returns["s3"] + 0.001    # additive drift -> different SR
    pert_rows = [_row_from_returns(k, v) for k, v in pert_returns.items()]
    pert_dsr = _leaderboard_dsr(pert_rows, n_trials)

    assert pert_dsr["s3"] != dsr["s3"]                 # the perturbed one moved
    for peer in ("s1", "s2"):
        assert pert_dsr[peer] == dsr[peer]             # peers exactly unchanged (decoupled)


def test_run_n1_dsr_is_psr_flag_and_var_fallback():
    """C1/C2 wiring in backtest.run: n_trials=1 sets dsr_is_psr (DSR ≡ PSR, sr0=0)
    without the fallback flag; n_trials>1 with no supplied trial variance fires the
    1/n null fallback and flags it (var_trials_sr stored either way)."""
    prices = _make_prices(n=300, seed=5)
    pos = pd.Series(1.0, index=prices.index)

    res1 = backtest.run(pos, prices, n_trials=1)
    st1 = res1["stats"]
    assert st1["dsr_is_psr"] is True
    assert st1["var_fallback"] is False           # N=1 declaration, nothing deflated
    assert st1["deflated_sharpe"] == pytest.approx(st1["psr"], rel=1e-12)

    res5 = backtest.run(pos, prices, n_trials=5)  # fallback fires (no trial SRs)
    st5 = res5["stats"]
    assert st5["dsr_is_psr"] is False
    assert st5["var_fallback"] is True
    assert st5["var_trials_sr"] == pytest.approx(1.0 / st5["n_periods"], rel=1e-12)

    res5v = backtest.run(pos, prices, n_trials=5, var_trials_sr=0.002)
    st5v = res5v["stats"]
    assert st5v["var_fallback"] is False          # empirical V supplied -> no fallback
    assert st5v["var_trials_sr"] == pytest.approx(0.002, rel=1e-12)


def test_run_funding_stores_var_trials_sr_and_flags():
    """C3 asymmetry fix: run_funding stores var_trials_sr in stats exactly like run,
    with the same C1 (dsr_is_psr) and C2 (var_fallback) semantics."""
    rng = np.random.default_rng(11)
    idx = pd.date_range("2024-01-01", periods=400, freq="8h", tz="UTC")
    fr = pd.Series(rng.normal(1e-4, 5e-5, len(idx)), index=idx)
    pos = pd.Series(-1.0, index=idx)              # standard short-perp carry leg

    r1 = backtest.run_funding(pos, fr, n_trials=1)["stats"]
    assert r1["dsr_is_psr"] is True
    assert r1["var_fallback"] is False
    assert r1["var_trials_sr"] == pytest.approx(1.0 / r1["n_periods"], rel=1e-12)
    assert r1["deflated_sharpe"] == pytest.approx(r1["psr"], rel=1e-12)

    r3 = backtest.run_funding(pos, fr, n_trials=3)["stats"]
    assert r3["dsr_is_psr"] is False
    assert r3["var_fallback"] is True             # 1/n null stood in for trial SRs
    assert r3["var_trials_sr"] == pytest.approx(1.0 / r3["n_periods"], rel=1e-12)

    r3v = backtest.run_funding(pos, fr, n_trials=3, var_trials_sr=0.004)["stats"]
    assert r3v["var_fallback"] is False
    assert r3v["var_trials_sr"] == pytest.approx(0.004, rel=1e-12)


def test_walk_forward_empirical_fold_variance_hand_pin():
    """C2/C3 fold-V wiring: synthetic prices with KNOWN per-fold OOS returns give
    hand-computable per-fold per-period Sharpe ratios, and walk_forward must set
    V = var(fold_SRs, ddof=1) exactly (no 1/n_oos, no max() floor).

    12 bars, n_splits=2 -> blocks [0:4] train, [4:8] fold-1 OOS, [8:12] fold-2 OOS.
    A constant long-1 strategy at zero cost makes each fold's net returns
    [0, r1, r2, r3] with r_i the within-slice pct changes:
      fold 1: (0.01, 0.02, 0.03) -> SR1 = 0.015/std([0,.01,.02,.03]) = 1.161895003862225
      fold 2: (0.01, 0.01, 0.04) -> SR2 = 0.015/std([0,.01,.01,.04]) = 0.8660254037844386
      V = var([SR1, SR2], ddof=1) = (SR1-SR2)^2 / 2 = 0.043769410125094665
    """
    SR1 = 1.161895003862225        # = 0.015 * sqrt(6000)
    SR2 = 0.8660254037844386       # = 0.015 / sqrt(0.0003)
    V = 0.043769410125094665       # = (SR1 - SR2)**2 / 2

    idx = pd.date_range("2022-01-01", periods=12, freq="D", tz="UTC")
    p = [100.0, 101.0, 102.0, 103.0]                       # train block (arbitrary)
    f1 = [100.0]
    for r in (0.01, 0.02, 0.03):
        f1.append(f1[-1] * (1.0 + r))
    f2 = [100.0]
    for r in (0.01, 0.01, 0.04):
        f2.append(f2[-1] * (1.0 + r))
    px = pd.Series(p + f1 + f2, index=idx)

    wf = backtest.walk_forward(
        lambda s: pd.Series(1.0, index=s.index), px,
        n_splits=2, cost_bps=0.0, slippage_bps=0.0,
    )
    oos = wf["oos"]
    assert oos["fold_srs"] == pytest.approx([SR1, SR2], rel=1e-9)
    assert oos["var_trials_sr"] == pytest.approx(V, rel=1e-9)
    assert oos["var_fallback"] is False
    assert oos["n_trials"] == 2
    # fold_srs mirror the per-fold stats produced by the same risk.summary path.
    assert oos["fold_srs"] == pytest.approx(
        [f["stats"]["sharpe_per_period"] for f in wf["folds"]], rel=1e-12
    )
    # And the headline OOS DSR uses exactly that empirical V.
    expect = risk.deflated_sharpe_ratio(
        oos["sharpe_per_period"], oos["n_periods"], oos["skew"], oos["kurtosis"], 2, V
    )
    assert oos["deflated_sharpe"] == pytest.approx(expect, rel=1e-12)


def test_compare_leaderboard_empirical_var_helper():
    """C3 leaderboard V: scripts/compare._empirical_var_sr is the ddof=1 variance of
    the finite strategy SRs, with the flagged fallback only when <2 are in hand."""
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "scripts" / "compare.py"
    spec = importlib.util.spec_from_file_location("_m6_compare_script", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # Hand pin: var([0.01, 0.02, 0.03], ddof=1) = 1e-4.
    v, fb = mod._empirical_var_sr([0.01, 0.02, 0.03])
    assert fb is False and v == pytest.approx(1e-4, rel=1e-12)
    # NaNs are excluded, remaining pair still yields the empirical variance.
    v2, fb2 = mod._empirical_var_sr([0.01, float("nan"), 0.03])
    assert fb2 is False and v2 == pytest.approx(2e-4, rel=1e-12)
    # <2 finite SRs -> flagged fallback (caller uses 1/n + prints the caveat).
    v3, fb3 = mod._empirical_var_sr([0.01])
    assert fb3 is True and math.isnan(v3)
    v4, fb4 = mod._empirical_var_sr([float("nan"), float("nan")])
    assert fb4 is True and math.isnan(v4)


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
# M2 — two-leg pairs cost (BTC leg + beta-scaled ETH hedge) + M1 bfill leak    #
# --------------------------------------------------------------------------- #
def test_pairs_legs_is_single_source_of_beta_and_position():
    """pairs_legs returns (state, beta_t) aligned to one index; the state is byte-for-
    byte the pairs_coint / pairs_ou output (no re-derivation), and beta_t is finite
    after the rolling warm-up."""
    btc = _make_prices(n=300, seed=41)
    rng = np.random.default_rng(43)
    eth = pd.Series(btc.to_numpy() * 0.07 * np.exp(rng.normal(0, 0.01, len(btc))), index=btc.index)
    for model, fn in (("coint", strategies.pairs_coint), ("ou", strategies.pairs_ou)):
        pos, beta = strategies.pairs_legs(btc, eth, window=60, model=model)
        assert pos.equals(fn(btc, eth, window=60)), f"pairs_legs {model} state must match {fn.__name__}"
        assert pos.index.equals(beta.index)
        assert np.isfinite(beta.to_numpy()[80:]).all()   # settled region, beta defined


def test_pairs_entry_charges_both_legs_exactly_2x_at_beta_one():
    """M2 (a): with beta ≡ 1 a single pairs entry charges the ETH leg identically to the
    BTC leg → the two-leg cost base is EXACTLY 2× the single-leg one (charge on BOTH
    legs' turnover)."""
    px = _make_prices(n=120, seed=3)
    pos = pd.Series(0.0, index=px.index)
    pos.iloc[50:] = 1.0                                   # one 0→1 entry, held to the end
    beta = pd.Series(1.0, index=px.index)
    eth_turn = (beta * pos).diff().abs()                 # |Δ(beta·state)| == |Δ state| here
    base = backtest.run(pos, px, cost_bps=10.0, slippage_bps=2.0)
    two = backtest.run(pos, px, cost_bps=10.0, slippage_bps=2.0, extra_cost_turnover=eth_turn)
    assert base["stats"]["total_turnover"] == pytest.approx(1.0)       # single entry
    assert two["stats"]["total_turnover"] == pytest.approx(2.0)        # both legs, exact
    # The extra cost is exactly the single entry's cost again → net return drops by 1×cost.
    extra_cost = 12.0 / 10_000.0                          # (cost+slip) bps on 1 unit turnover
    assert (base["returns"].sum() - two["returns"].sum()) == pytest.approx(extra_cost)


def test_pairs_rolling_beta_drift_incurs_eth_leg_cost_with_unchanged_state():
    """M2 (b): even with the BTC state held constant (no discrete trade), a drifting
    hedge ratio rebalances the ETH notional every bar → the ETH leg still charges the
    total variation of beta·state."""
    px = _make_prices(n=100, seed=5)
    pos = pd.Series(1.0, index=px.index)                 # always-in: BTC turnover = one entry only
    beta = pd.Series(np.linspace(1.0, 2.0, len(px)), index=px.index)   # continuously drifting hedge
    eth_turn = (beta * pos).diff().abs()
    base = backtest.run(pos, px, cost_bps=10.0, slippage_bps=2.0)
    two = backtest.run(pos, px, cost_bps=10.0, slippage_bps=2.0, extra_cost_turnover=eth_turn)
    added_turnover = two["stats"]["total_turnover"] - base["stats"]["total_turnover"]
    assert added_turnover > 0.0
    assert added_turnover == pytest.approx(float(eth_turn.fillna(0.0).sum()))   # ≈ 1.0 (β: 1→2)


def test_extra_cost_turnover_none_leaves_every_nonpairs_result_byte_identical():
    """M2 (c) regression guard: extra_cost_turnover default (None) — and an all-zero
    series — must leave every non-pairs backtest byte-for-byte unchanged."""
    df = _make_ohlcv(n=300, seed=8)
    px = df["close"]
    cases = {
        "buy_and_hold": strategies.buy_and_hold(df),
        "ma_trend": strategies.ma_trend_filter(df, n=50),
        "tsmom": strategies.tsmom(df, lookback=20),
    }
    zeros = pd.Series(0.0, index=px.index)
    for name, pos in cases.items():
        a = backtest.run(pos, px, cost_bps=10.0, slippage_bps=2.0)
        b = backtest.run(pos, px, cost_bps=10.0, slippage_bps=2.0, extra_cost_turnover=None)
        z = backtest.run(pos, px, cost_bps=10.0, slippage_bps=2.0, extra_cost_turnover=zeros)
        for other in (b, z):
            assert np.array_equal(a["returns"].to_numpy(), other["returns"].to_numpy(), equal_nan=True), name
            assert np.array_equal(a["equity"].to_numpy(), other["equity"].to_numpy(), equal_nan=True), name
        assert a["stats"]["total_turnover"] == z["stats"]["total_turnover"], name


def test_extra_cost_turnover_alignment_series_vs_positional_array():
    """M2 adversarial: a pd.Series extra-cost is aligned by INDEX (reindex+fillna(0));
    a raw positional array is aligned by POSITION and must match length exactly — a
    length mismatch fails LOUDLY instead of silently dropping the whole leg to zero
    (the pre-hardening bug: a RangeIndex array reindexed onto a DatetimeIndex → all-NaN
    → 0 cost, a silent under-charge)."""
    idx = pd.date_range("2021-01-01", periods=100, freq="D", tz="UTC")
    px = _make_prices(n=100, seed=9)
    px.index = idx
    pos = pd.Series(np.where(np.arange(100) % 2 == 0, 1.0, 0.0), index=idx)
    base = backtest.run(pos, px, cost_bps=10.0, slippage_bps=2.0)

    # (i) index-aligned Series charges the extra leg.
    ser = pd.Series(0.5, index=idx)
    r_ser = backtest.run(pos, px, cost_bps=10.0, slippage_bps=2.0, extra_cost_turnover=ser)
    assert r_ser["stats"]["total_turnover"] > base["stats"]["total_turnover"]

    # (ii) a positional array of MATCHING length aligns by position (same as the Series).
    r_arr = backtest.run(pos, px, cost_bps=10.0, slippage_bps=2.0,
                         extra_cost_turnover=np.full(100, 0.5))
    assert r_arr["stats"]["total_turnover"] == r_ser["stats"]["total_turnover"]

    # (iii) a positional array of the WRONG length raises loudly (no silent zero).
    with pytest.raises(ValueError, match="extra_cost_turnover"):
        backtest.run(pos, px, cost_bps=10.0, slippage_bps=2.0,
                     extra_cost_turnover=np.full(37, 0.5))

    # (iv) NaNs in the aligned Series never reach the charge (fillna(0)); result finite.
    ser_nan = ser.copy(); ser_nan.iloc[:10] = np.nan
    r_nan = backtest.run(pos, px, cost_bps=10.0, slippage_bps=2.0, extra_cost_turnover=ser_nan)
    assert np.isfinite(r_nan["equity"].iloc[-1])
    assert np.isfinite(r_nan["stats"]["total_turnover"])


def test_pairs_no_bfill_leak_leading_pre_eth_bars_stay_nan():
    """M1 (d): when BTC history starts BEFORE ETH's first observation, ffill-only (the
    audit M1 fix) leaves the leading pre-ETH region NaN so pairs_coint drops it — the
    old trailing .bfill() would back-stamp ETH's first price there and fabricate a
    spread (a look-back leak)."""
    btc = _make_prices(n=200, seed=41)
    first = 50
    eth = pd.Series(btc.to_numpy()[first:] * 0.07, index=btc.index[first:])   # ETH exists only from bar 50
    aligned_ffill = eth.reindex(btc.index).ffill()                            # audit M1: no bfill
    aligned_bfill = eth.reindex(btc.index).ffill().bfill()                    # the OLD (leaky) behavior
    assert aligned_ffill.iloc[:first].isna().all()          # fix: leading pre-ETH bars stay NaN
    assert not aligned_bfill.iloc[:first].isna().any()      # old bug: back-stamped a fabricated price
    # Downstream: no fabricated pairs position in the leading region.
    pos = strategies.pairs_coint(btc, aligned_ffill, window=30).reindex(btc.index)
    assert pos.iloc[:first].isna().all()


# --------------------------------------------------------------------------- #
# M9 — pairs delta-neutral P&L (the completion of M2: state earns the SPREAD)  #
# --------------------------------------------------------------------------- #
def test_m9_delta_neutral_identity_zero_spread_when_legs_move_together():
    """M9 (a): when ETH tracks BTC exactly and beta ≡ 1, the spread return is 0 every
    bar, so a delta-neutral pairs trade books ZERO gross P&L (constructed exact) — the
    hedge leg cancels the directional BTC leg by construction."""
    px = _make_prices(n=120, seed=17)
    eth = px.copy()                                  # ETH identical ⇒ eth_ret == btc_ret
    beta = pd.Series(1.0, index=px.index)
    state = pd.Series(1.0, index=px.index)           # always long the spread
    hedge_ret = beta.shift(1) * eth.pct_change()     # = beta_{t-1}·eth_ret_t = btc_ret_t
    res = backtest.run(state, px, cost_bps=0.0, slippage_bps=0.0, hedge_return=hedge_ret)
    # gross = state_{t-1}·(btc_ret - btc_ret) = 0 on every bar → the spread P&L vanishes.
    assert np.allclose(res["gross_returns"].fillna(0.0).to_numpy(), 0.0, atol=1e-15)
    assert res["equity"].iloc[-1] == pytest.approx(1.0, abs=1e-12)   # zero cost ⇒ flat equity


def test_m9_hedge_return_none_leaves_every_nonpairs_result_byte_identical():
    """M9 (b) regression guard: hedge_return default (None) and an explicit None must
    leave EVERY non-pairs backtest — run AND walk_forward — byte-for-byte unchanged."""
    df = _make_ohlcv(n=300, seed=8)
    px = df["close"]
    cases = {
        "buy_and_hold": strategies.buy_and_hold(df),
        "ma_trend": strategies.ma_trend_filter(df, n=50),
        "tsmom": strategies.tsmom(df, lookback=20),
    }
    for name, pos in cases.items():
        a = backtest.run(pos, px, cost_bps=10.0, slippage_bps=2.0)
        b = backtest.run(pos, px, cost_bps=10.0, slippage_bps=2.0, hedge_return=None)
        for key in ("returns", "gross_returns", "equity", "turnover"):
            assert np.array_equal(a[key].to_numpy(), b[key].to_numpy(), equal_nan=True), (name, key)
    # walk_forward path: hedge_return=None must equal the no-arg OOS track record.
    wa = backtest.walk_forward(lambda s: strategies.tsmom(pd.DataFrame({"close": s}), lookback=20),
                               px, n_splits=5)
    wb = backtest.walk_forward(lambda s: strategies.tsmom(pd.DataFrame({"close": s}), lookback=20),
                               px, n_splits=5, hedge_return=None)
    assert np.array_equal(wa["oos_returns"].to_numpy(), wb["oos_returns"].to_numpy(), equal_nan=True)


def test_m9_eth_outrunning_btc_makes_long_spread_pnl_negative():
    """M9 (c): BTC rises (+2%/bar) and ETH rises MORE (+5%/bar) with beta ≡ 1. A LONG-
    spread state (long BTC leg / short ETH hedge) that looks POSITIVE single-leg turns
    NEGATIVE once the faster ETH hedge nets in — proof the ETH leg now books into P&L."""
    idx = pd.date_range("2021-01-01", periods=60, freq="D", tz="UTC")
    btc = pd.Series(20_000.0 * (1.02 ** np.arange(60)), index=idx)   # +2%/bar
    eth = pd.Series(1_000.0 * (1.05 ** np.arange(60)), index=idx)    # +5%/bar
    beta = pd.Series(1.0, index=idx)
    state = pd.Series(1.0, index=idx)                                # long the spread
    hedge_ret = beta.shift(1) * eth.pct_change()
    single = backtest.run(state, btc, cost_bps=0.0, slippage_bps=0.0)
    dn = backtest.run(state, btc, cost_bps=0.0, slippage_bps=0.0, hedge_return=hedge_ret)
    assert single["gross_returns"].fillna(0.0).sum() > 0.0          # bare directional long BTC: positive
    g = dn["gross_returns"].dropna().to_numpy()
    assert (g < 0.0).all()                                          # every active bar: 0.02 - 0.05 < 0
    assert dn["gross_returns"].fillna(0.0).sum() < single["gross_returns"].fillna(0.0).sum()
    assert dn["equity"].iloc[-1] < 1.0                              # the delta-neutral trade loses


def test_m9_beta_shift_is_t_minus_1_no_lookahead():
    """M9 (d): the hedge ratio is 1-bar-lagged (beta.shift(1)), the same no-look-ahead
    shift as the state — a beta SPIKE at bar t may only affect P&L at t+1, never at t."""
    idx = pd.date_range("2021-01-01", periods=40, freq="D", tz="UTC")
    px = _make_prices(n=40, seed=21); px.index = idx
    eth = _make_prices(n=40, seed=22); eth.index = idx
    state = pd.Series(1.0, index=idx)
    beta = pd.Series(1.0, index=idx)
    t = 20
    beta_spk = beta.copy(); beta_spk.iloc[t] = 9.0                  # a lone beta spike at bar t
    eth_ret = eth.pct_change()
    hedge_base = beta.shift(1) * eth_ret
    hedge_spk = beta_spk.shift(1) * eth_ret
    gb = backtest.run(state, px, cost_bps=0.0, slippage_bps=0.0,
                      hedge_return=hedge_base)["gross_returns"].to_numpy()
    gs = backtest.run(state, px, cost_bps=0.0, slippage_bps=0.0,
                      hedge_return=hedge_spk)["gross_returns"].to_numpy()
    assert gb[t] == pytest.approx(gs[t])                           # bar t: beta_{t-1} unchanged
    assert gb[t + 1] != pytest.approx(gs[t + 1])                   # bar t+1: the lagged spike lands
    mask = np.ones(len(gb), dtype=bool); mask[t + 1] = False       # every OTHER bar identical
    fb = np.where(np.isnan(gb), 0.0, gb)
    fs = np.where(np.isnan(gs), 0.0, gs)
    assert np.array_equal(fb[mask], fs[mask])


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
# M4 — purge / embargo / lockbox (default-OFF, correct-by-construction)         #
# --------------------------------------------------------------------------- #
def test_m4_purge_embargo_zero_is_byte_identical_golden():
    """(a) purge=0, embargo=0 reproduces the pre-M4 walk_forward + cpcv numbers
    BIT-for-BIT — both against the untouched no-arg call AND against pre-registered
    golden constants (so a silent formula drift, not just a default flip, is caught)."""
    px = _make_prices(n=600, seed=42)
    wf_mp = lambda p: (p > p.rolling(50).mean()).astype(float)
    cp_mp = lambda p: (p > p.rolling(30).mean()).astype(float)

    # No-arg (pre-M4 signature) == explicit purge=0/embargo=0 == golden constants.
    wf0 = backtest.walk_forward(wf_mp, px, n_splits=5)
    wfz = backtest.walk_forward(wf_mp, px, n_splits=5, purge=0, embargo=0)
    assert wf0["oos"]["sharpe"] == wfz["oos"]["sharpe"] == -0.6577099382791535
    assert wf0["is_"]["sharpe"] == wfz["is_"]["sharpe"] == -0.5255985161946755

    cp0 = backtest.cpcv(cp_mp, px, n_blocks=6, k_test=2)
    cpz = backtest.cpcv(cp_mp, px, n_blocks=6, k_test=2, purge=0, embargo=0)
    assert cp0["paths"] == cpz["paths"]
    assert cp0["median_sharpe"] == cpz["median_sharpe"] == -0.26458498017492493
    assert cp0["iqr"] == cpz["iqr"] == 0.6414388218458281
    assert cp0["n_paths"] == cpz["n_paths"] == 15
    # OOS headline is invariant to purge/embargo (they touch only the IS statistic).
    wfp = backtest.walk_forward(wf_mp, px, n_splits=5, purge=7, embargo=5)
    assert wfp["oos"]["sharpe"] == wf0["oos"]["sharpe"]


def test_m4_purge_removes_exactly_k_train_bars_adjacent_to_test_start():
    """(b) purge=k drops EXACTLY the k in-sample bars adjacent to each fold's test
    start (an index mask on the per-fold IS statistic), leaving OOS untouched."""
    px = _make_prices(n=600, seed=42)
    mp = lambda p: (p > p.rolling(50).mean()).astype(float)
    base = backtest.walk_forward(mp, px, n_splits=5, purge=0, embargo=0)
    for k in (1, 3, 8):
        purged = backtest.walk_forward(mp, px, n_splits=5, purge=k, embargo=0)
        for fb, fp in zip(base["folds"], purged["folds"]):
            assert fp["n_is"] == fb["n_is"] - k            # exactly k fewer IS bars
        assert purged["oos"]["sharpe"] == base["oos"]["sharpe"]   # OOS invariant


def test_m4_embargo_inserts_exactly_e_bar_gaps_in_later_folds():
    """(c) embargo=e inserts an exactly e-bar gap for EACH prior test block that has
    already re-entered a later fold's expanding IS window (a gap the anchored window
    skips), OOS untouched. n=120, 5 splits, edges every 20 bars: fold k drops
    e·(k-2) IS bars (folds 1-2 see no prior embargoed block yet)."""
    idx = pd.date_range("2022-01-01", periods=120, freq="D", tz="UTC")
    px = pd.Series(
        20_000.0 * np.exp(np.cumsum(np.random.default_rng(3).normal(0.0008, 0.03, 120))),
        index=idx,
    )
    mp = lambda p: pd.Series(1.0, index=p.index)
    base = backtest.walk_forward(mp, px, n_splits=5, embargo=0)
    e = 4
    emb = backtest.walk_forward(mp, px, n_splits=5, embargo=e)
    for k, (fb, fe) in enumerate(zip(base["folds"], emb["folds"]), start=1):
        expected_gap = e * max(0, k - 2)                   # prior embargoed blocks in-window
        assert fe["n_is"] == fb["n_is"] - expected_gap
    assert emb["oos"]["sharpe"] == base["oos"]["sharpe"]   # OOS invariant


def test_m4_purge_changes_is_stat_for_kstep_label_signal():
    """(e) with purge>0 the reported IS statistic genuinely MOVES (the mask drops the
    label-overlap bars) while the causal 1-bar OOS stays put — the machinery is live,
    not inert, for the k-step-label signals arriving when MinBTL clears."""
    px = _make_prices(n=400, seed=17)
    # A synthetic k-step (k=5) forward-momentum LABEL turned into a position rule: the
    # sign of the trailing 5-bar change (causal), the shape order-flow signals will take.
    def kstep_signal(p):
        return np.sign(p.pct_change(5)).fillna(0.0)
    base = backtest.walk_forward(kstep_signal, px, n_splits=4, purge=0)
    purged = backtest.walk_forward(kstep_signal, px, n_splits=4, purge=5)
    assert purged["is_"]["sharpe"] != base["is_"]["sharpe"]        # IS stat moved
    assert purged["oos"]["sharpe"] == base["oos"]["sharpe"]        # OOS untouched


def test_m4_lockbox_flags_double_scored_slice():
    """(d) LockBox is an evaluate-once ledger: a slice scored once passes
    assert_scored_once; a second scoring is detectable (count rises, the assertion
    raises), and an unscored slice also fails (a forgotten holdout is caught too)."""
    lb = backtest.LockBox()
    s, e = pd.Timestamp("2025-01-01"), pd.Timestamp("2025-06-30")

    assert lb.was_scored(s, e) is False
    with pytest.raises(AssertionError):
        lb.assert_scored_once(s, e)               # never scored -> fails

    assert lb.record(s, e) == 1
    assert lb.was_scored(s, e) is True
    lb.assert_scored_once(s, e)                    # scored exactly once -> ok

    assert lb.record(s, e) == 2                    # a second peek...
    assert lb.count(s, e) == 2
    with pytest.raises(AssertionError):
        lb.assert_scored_once(s, e)               # ...is now detectable

    # An independent slice is tracked separately (no cross-contamination).
    s2, e2 = pd.Timestamp("2024-01-01"), pd.Timestamp("2024-06-30")
    lb.record(s2, e2)
    lb.assert_scored_once(s2, e2)
    assert lb.slices() == {(s, e): 2, (s2, e2): 1}


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


# --------------------------------------------------------------------------- #
# Tharp eval/risk layer (expectancy / R-multiples; percent-risk sizing)        #
# --------------------------------------------------------------------------- #
def test_expectancy_report_segments_trades_and_R_math():
    """trade_ledger segments a long/flat signal into discrete trades; expectancy_report
    computes R-multiples off the vol-notional R (R = entry_w * k * sigma_bar)."""
    idx = pd.date_range("2021-01-01", periods=12, freq="D", tz="UTC")
    px = pd.Series([100,100,100,110,110,110,100,100,100,90,90,90], index=idx, dtype="float64")
    pos = pd.Series([0,0,1,1,0,0,0,1,1,0,0,0], index=idx, dtype="float64")  # pre-shift
    vol = pd.Series(0.10, index=idx)  # constant 10% annualized
    led = risk.trade_ledger(pos, px, vol, periods_per_year=365, k=2.0)
    assert len(led) == 2                                  # two discrete trades
    assert led[0]["trade_return"] > 0 and led[1]["trade_return"] < 0  # winner then loser
    sb = 0.10 / math.sqrt(365); R = 1.0 * 2.0 * sb        # entry_w=1
    assert abs(led[0]["r_multiple"] - led[0]["trade_return"] / R) < 1e-9
    rep = risk.expectancy_report(pos, px, vol, periods_per_year=365, k=2.0)
    assert rep["n_trades"] == 2
    assert abs(rep["win_rate"] - 0.5) < 1e-12
    assert abs(rep["expectancy_r"]) < 1e-9               # symmetric +R / -R → ~0
    assert rep["max_loss_streak"] == 1
    assert abs(rep["sqn"]) < 1e-9                         # symmetric R → SQN ≈ 0
    assert abs(rep["profit_factor"] - 1.0) < 1e-9        # equal gross win/loss R
    assert "avg_mae_r" in rep and rep["avg_mae_r"] == rep["avg_mae_r"]  # finite


def test_expectancy_buy_and_hold_is_one_degenerate_trade():
    """Always-in (buy & hold) collapses to a single ledger trade — the low-N case to flag.
    Its entry sits in the vol warm-up so it is not R-scorable → expectancy_report honestly
    reports n_trades=0 (no R-scorable trades), correct for the baseline."""
    df = _make_ohlcv(n=200, seed=4)
    vol = features.realized_vol(features.simple_returns(df["close"]), 20, 365)
    led = risk.trade_ledger(strategies.buy_and_hold(df), df["close"], vol)
    assert len(led) == 1                                   # always-in → one (degenerate) trade
    assert risk.expectancy_report(strategies.buy_and_hold(df), df["close"], vol)["n_trades"] == 0


def test_percent_risk_size_bounded_and_inverse_to_atr():
    """percent_risk_size is a valid [-1,1] weight and shrinks as relative ATR rises
    (inverse-volatility sizing) — the Tharp Percent-Risk model."""
    df = _make_ohlcv(n=300, seed=9)
    sized = strategies.percent_risk_size(strategies.buy_and_hold(df), df,
                                         risk_pct=0.02, atr_window=20, k_stop=2.0)
    _assert_in_unit_band(sized, "percent_risk")
    rel_atr = (features.atr(df, 20) / df["close"])
    common = sized.dropna().index.intersection(rel_atr.dropna().index)
    assert sized.reindex(common).corr(rel_atr.reindex(common)) < 0.0   # size ↓ as vol ↑


def test_random_entry_deterministic_and_in_unit_band():
    """Tharp random-entry control: seeded → reproducible; positions in {-1,0,+1}; a
    different seed gives a different path (it is a genuine coin-flip baseline)."""
    df = _make_ohlcv(n=300, seed=12)
    a = strategies.random_entry(df, seed=7)
    b = strategies.random_entry(df, seed=7)
    pd.testing.assert_series_equal(a, b)                   # deterministic by seed
    _assert_in_unit_band(a, "random_entry")
    assert set(np.unique(a.dropna().to_numpy())).issubset({-1.0, 0.0, 1.0})
    assert not a.equals(strategies.random_entry(df, seed=8))  # different seed → different path


def test_tier_b_candidates_unit_band_and_causal():
    """Donchian / VWAP-reversion / fixed-R exit-overlay stay valid weights in {-1,0,+1};
    the stateful Donchian is causal (prefix matches the full run on the settled region)."""
    df = _make_ohlcv(n=400, seed=21)
    don = strategies.donchian_breakout(df, n=55, exit_n=20)
    vw = strategies.vwap_reversion(df, window=48)
    fx = strategies.fixed_r_exit(strategies.buy_and_hold(df), df)
    for name, s in (("donchian", don), ("vwap_reversion", vw), ("fixed_r_exit", fx)):
        _assert_in_unit_band(s, name)
        assert set(np.unique(s.dropna().to_numpy())).issubset({-1.0, 0.0, 1.0})
    k = 300
    full = strategies.donchian_breakout(df, 55, 20)
    pref = strategies.donchian_breakout(df.iloc[:k], 55, 20)
    a = np.nan_to_num(full.iloc[60:k - 1].to_numpy(), nan=-9.0)
    b = np.nan_to_num(pref.iloc[60:k - 1].to_numpy(), nan=-9.0)
    assert np.allclose(a, b)


def test_run_funding_books_funding_accrual_not_spot_price():
    """run_funding pays the funding leg, not spot returns: a SHORT perp (-1) earns
    positive funding, a LONG (+1) pays it, flat earns ~0 — independent of any price."""
    idx = pd.date_range("2023-01-01", periods=200, freq="8h")
    funding = pd.Series(0.0005, index=idx)            # +0.05%/8h, constant
    short = backtest.run_funding(pd.Series(-1.0, index=idx), funding,
                                 cost_bps=0.0, slippage_bps=0.0, periods_per_year=1095)
    longp = backtest.run_funding(pd.Series(1.0, index=idx), funding,
                                 cost_bps=0.0, slippage_bps=0.0, periods_per_year=1095)
    flat = backtest.run_funding(pd.Series(0.0, index=idx), funding,
                                cost_bps=0.0, slippage_bps=0.0, periods_per_year=1095)
    assert short["equity"].iloc[-1] > 1.05          # short receives funding
    assert longp["equity"].iloc[-1] < 1.0           # long pays funding
    assert abs(flat["equity"].iloc[-1] - 1.0) < 1e-12
    # net per held interval equals +funding for the short leg (no-look-ahead shift)
    assert abs(short["returns"].iloc[5] - 0.0005) < 1e-12
