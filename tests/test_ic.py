"""Tests for btcquant.ic — forward Information Coefficient validation."""
import math

import numpy as np
import pandas as pd

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


def test_perfect_predictor_scores_ic_one():
    """A signal that equals the k-ahead forward return scores IC≈1 at that k and is
    flagged significant."""
    p = _prices()
    sig = ic.forward_returns(p, 3)               # a (non-causal) perfect 3-bar predictor
    val = ic.information_coefficient(sig, p, 3, method="spearman")
    assert val > 0.999
    n = int(pd.concat([sig, ic.forward_returns(p, 3)], axis=1).dropna().shape[0])
    assert ic.ic_significance(val, n, 3)["significant"]


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
