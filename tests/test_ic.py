"""Tests for btcquant.ic — forward Information Coefficient validation."""
import math

import numpy as np
import pandas as pd
import statsmodels.api as sm

from btcquant import features, ic


def _prices(n=400, seed=11):
    rng = np.random.default_rng(seed)
    return pd.Series(100.0 * np.cumprod(1.0 + 0.01 * rng.standard_normal(n)))


def test_forward_returns_alignment_is_causal_scoring():
    """forward_returns(p,1)_t == simple_returns(p)_{t+1}; the last k entries are NaN
    (no future leaks into the present)."""
    p = _prices()
    fr1 = ic.forward_returns(p, 1)
    sr_shift = features.simple_returns(p).shift(-1)
    a = fr1.dropna().to_numpy()
    b = sr_shift.reindex(fr1.index).dropna().to_numpy()
    assert np.allclose(a, b)
    assert math.isnan(fr1.iloc[-1])              # last bar has no forward return
    assert ic.forward_returns(p, 5).iloc[-5:].isna().all()


def _align(signal, fwd):
    """The aligned (dropna-joined) signal/forward columns ic_significance now expects."""
    df = pd.concat([pd.Series(signal).rename("sig"), pd.Series(fwd).rename("fwd")],
                   axis=1).dropna()
    return df["sig"], df["fwd"]


def test_perfect_predictor_scores_ic_one():
    """A signal that equals the k-ahead forward return scores IC≈1 at that k and is
    flagged significant. ic_significance now takes the aligned (signal, fwd) series and
    reports the rank slope as ``ic`` (== the Spearman IC)."""
    p = _prices()
    sig = ic.forward_returns(p, 3)               # a (non-causal) perfect 3-bar predictor
    val = ic.information_coefficient(sig, p, 3, method="spearman")
    assert val > 0.999
    s, f = _align(sig, ic.forward_returns(p, 3))
    res = ic.ic_significance(s, f, k=3)
    assert res["ic"] > 0.999                      # rank slope == Spearman IC
    assert res["significant"]
    assert res["method"] == "newey-west-k-1"


def test_noise_signal_is_not_significant():
    """An independent random signal has |IC| well inside the band (no leading edge)."""
    p = _prices(n=500)
    rng = np.random.default_rng(99)
    noise = pd.Series(rng.standard_normal(len(p)), index=p.index)
    prof = ic.ic_profile(noise, p, horizons=(1, 3, 5, 10), method="spearman")
    assert all(not prof[k]["significant"] for k in (1, 3, 5, 10))
    assert abs(prof[1]["ic"]) < 0.2


def test_lead_time_profile_peaks_at_true_horizon():
    """A signal built as the 5-bar-ahead return scores its highest IC at k=5."""
    p = _prices(seed=7)
    sig = ic.forward_returns(p, 5)
    prof = ic.ic_profile(sig, p, horizons=(1, 3, 5, 10), method="spearman")
    best_k = max(prof, key=lambda k: prof[k]["ic"])
    assert best_k == 5
    assert prof[5]["ic"] > prof[1]["ic"]


def test_spearman_is_rank_invariant_pearson_is_not():
    """A monotone nonlinear transform of the signal leaves the Spearman IC unchanged
    but generally moves the Pearson IC."""
    p = _prices(seed=3)
    sig = ic.forward_returns(p, 2).fillna(0.0)
    mono = np.sign(sig) * (sig.abs() ** 3)        # strictly monotone in the signal
    sp_a = ic.information_coefficient(sig, p, 2, method="spearman")
    sp_b = ic.information_coefficient(mono, p, 2, method="spearman")
    pe_a = ic.information_coefficient(sig, p, 2, method="pearson")
    pe_b = ic.information_coefficient(mono, p, 2, method="pearson")
    assert abs(sp_a - sp_b) < 1e-9
    assert abs(pe_a - pe_b) > 1e-3


def test_regime_conditional_ic_isolates_the_regime():
    """A signal that predicts only inside the regime is significant in-regime and null
    out-of-regime."""
    p = _prices(n=600, seed=21)
    rng = np.random.default_rng(5)
    fr = ic.forward_returns(p, 1)
    mask = pd.Series(np.arange(len(p)) % 2 == 0, index=p.index)   # alternating regime
    sig = fr.where(mask, pd.Series(rng.standard_normal(len(p)), index=p.index))
    res = ic.regime_conditional_ic(sig.fillna(0.0), p, mask, k=1, method="spearman")
    assert res["in"]["ic"] > res["out"]["ic"]
    assert res["in"]["significant"]
    assert abs(res["out"]["ic"]) < 0.2


def _persistent_ar1(n, seed, phi):
    """An AR(1) path x_t = phi·x_{t-1} + e_t — used to manufacture strong serial
    dependence in both the signal and the (overlapping) forward returns."""
    rng = np.random.default_rng(seed)
    e = rng.standard_normal(n)
    x = np.zeros(n)
    for i in range(1, n):
        x[i] = phi * x[i - 1] + e[i]
    return x


# ---------------------------------------------------------------------------
# M5 — Newey-West / HAC significance of the forward IC (replaces the crude
# fixed band crit = 1.96·√(k/n)). Cases (a)-(e) from the audit spec.
# ---------------------------------------------------------------------------

def test_hac_k1_reduces_to_ols_t_on_ranks():
    """(a) At k=1 there is no window overlap, so maxlags=0 and the HAC covariance
    collapses to the White (HC0) sandwich: the reported HAC t-stat equals the OLS t on the
    ranks under that same covariance, and sits close to the textbook Spearman-correlation
    t-stat. The reported ``ic`` is exactly the Spearman coefficient (the rank slope)."""
    p = _prices()
    s, f = _align(ic.forward_returns(p, 3), ic.forward_returns(p, 1))
    res = ic.ic_significance(s, f, k=1)
    assert res["k"] == 1 and res["method"] == "newey-west-k-1"

    xr, yr = s.rank().to_numpy(), f.rank().to_numpy()
    # ic is the rank slope == the Spearman IC itself.
    assert abs(res["ic"] - s.corr(f, method="spearman")) < 1e-9
    # maxlags=0 NW == OLS with HC0 robust covariance: the "OLS t on ranks".
    hc0 = sm.OLS(yr, sm.add_constant(xr)).fit(cov_type="HC0")
    assert np.isclose(res["t_stat"], float(hc0.tvalues[1]), rtol=1e-6)
    # ...and it is within a few % of the classical (homoskedastic) Spearman-t.
    r, n = float(s.corr(f, method="spearman")), len(s)
    t_classical = r * math.sqrt((n - 2) / (1.0 - r * r))
    assert abs(res["t_stat"] - t_classical) / abs(t_classical) < 0.10


def test_hac_widens_se_on_overlapping_autocorrelated_series():
    """(b) On a strongly serially-dependent, OVERLAPPING k=5 sample the Newey-West HAC
    standard error is LARGER than the naive homoskedastic OLS SE (and the HAC |t| smaller):
    correcting the MA(k-1) overlap makes significance strictly harder to reach than an
    un-corrected test. This is the direction that can only *strengthen* a NONE-significant
    run-log verdict."""
    n, k = 600, 5
    sig = pd.Series(_persistent_ar1(n, seed=8, phi=0.95))
    prices = pd.Series(100.0 * np.cumprod(1.0 + 0.01 * _persistent_ar1(n, seed=7, phi=0.95)))
    s, f = _align(sig, ic.forward_returns(prices, k))
    res = ic.ic_significance(s, f, k=k)

    xr, yr = s.rank().to_numpy(), f.rank().to_numpy()
    naive = sm.OLS(yr, sm.add_constant(xr)).fit()          # homoskedastic OLS
    naive_se, naive_t = float(naive.bse[1]), float(naive.tvalues[1])

    assert res["se"] > naive_se                             # HAC SE strictly larger
    assert abs(res["t_stat"]) < abs(naive_t)               # ...so |t| strictly smaller
    # sanity: ic is still the rank slope, HAC p-value is finite and two-sided.
    assert abs(res["ic"] - s.corr(f, method="spearman")) < 1e-9
    assert np.isfinite(res["p_value"])


def test_hac_zero_information_signal_not_significant():
    """(c) A signal independent of the forward return yields an IC≈0 with a HAC p-value
    near 1 — correctly NOT significant."""
    p = _prices(n=500)
    noise = pd.Series(np.random.default_rng(55).standard_normal(len(p)), index=p.index)
    s, f = _align(noise, ic.forward_returns(p, 5))
    res = ic.ic_significance(s, f, k=5)
    assert abs(res["ic"]) < 0.05
    assert res["p_value"] > 0.5                             # p near 1
    assert not res["significant"]


def test_hac_perfect_monotone_signal_significant():
    """(d) A signal strictly monotone in the forward return has rank-IC = 1; the HAC test
    flags it significant (near-zero SE, p≈0) without crashing on the degenerate residual."""
    p = _prices(seed=5)
    fwd = ic.forward_returns(p, 1)
    mono = np.sign(fwd) * (fwd.abs() ** 3)                  # strictly monotone in fwd
    s, f = _align(mono, fwd)
    res = ic.ic_significance(s, f, k=1)
    assert res["ic"] > 0.999
    assert res["p_value"] < 1e-6
    assert res["significant"]


def test_hac_too_few_points_returns_nan_no_crash():
    """(e) Below the minimum sample the estimator returns NaN stats and significant=False
    instead of raising — a HAC kernel of width k-1 needs more than k-1+1 points."""
    p = _prices()
    s, f = _align(ic.forward_returns(p, 3), ic.forward_returns(p, 1))
    tiny_s, tiny_f = s.iloc[:2], f.iloc[:2]                 # n = 2
    res = ic.ic_significance(tiny_s, tiny_f, k=5)
    assert res["n"] == 2
    assert math.isnan(res["t_stat"]) and math.isnan(res["p_value"]) and math.isnan(res["ic"])
    assert not res["significant"]
    assert res["method"] == "newey-west-k-1"


def test_ic_ir_uses_nonoverlapping_blocks():
    """IC-IR on a strong predictor yields a large positive t-stat; on noise it is small."""
    p = _prices(n=600, seed=4)
    rng = np.random.default_rng(4)
    # strong but IMPERFECT predictor: forward return + noise AT THE RETURN SCALE (so the
    # signal is not swamped), giving high-but-varying block ICs -> finite, positive IR.
    fr = ic.forward_returns(p, 1)
    strong = (fr + 0.7 * fr.std() * pd.Series(rng.standard_normal(len(p)), index=p.index)).fillna(0.0)
    weak = pd.Series(np.random.default_rng(1).standard_normal(len(p)), index=p.index)
    s_ir = ic.ic_ir(strong, p, k=1, block=21)
    w_ir = ic.ic_ir(weak, p, k=1, block=21)
    assert s_ir["n_blocks"] >= 5
    assert s_ir["t_stat"] > w_ir["t_stat"]
    assert s_ir["ir"] > 0
