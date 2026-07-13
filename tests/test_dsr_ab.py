"""test_dsr_ab.py — teeth for the A/B/B Deflated-Sharpe decision aid (scripts/dsr_ab.py).

Deterministic, no network: a FIXED synthetic set of N=4 strategies with hand-chosen
(SR, n, skew, kurt). What is asserted:

Since 2026-07-13 (RESEARCH-dsr-convention.md) the PRODUCTION leaderboard convention is
**B2** (per-strategy own-Sharpe variance); A is the historical/reference column.

(a) ``sr0(V, N)`` matches an independent hand-computed value for each V convention
    (A shared, B1 = 1/n, B2 = own Lo/Mertens variance).
(b) DSR_B2 equals ``risk.deflated_sharpe_ratio`` with the per-strategy own-Sharpe
    variance ``risk.sharpe_estimator_variance`` — ties the tool's PRODUCTION column
    (B2) to the production function; and ``v_b2`` delegates to that single source of
    truth. DSR_A still equals the same function with the shared empirical V (the
    historical column).
(c) DECOUPLING (the core claim): perturbing strategy 4's SR changes EVERY DSR_A but
    leaves DSR_B1 and DSR_B2 for strategies 1–3 BIT-IDENTICAL (asserted to exactly 0).
(d) B2 reduces to ~B1 when skew=0, kurt=3, SR=0 (sanity: V_B2 = 1/(n-1) ≈ 1/n).

References: Bailey & López de Prado 2014 (DSR / expected-max-of-N); Lo 2002 (Sharpe-
estimator variance); Mertens 2002 (skew-kurt-corrected Sharpe variance).
"""
from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import pytest
from scipy.stats import norm

from btcquant import risk

# Load scripts/dsr_ab.py as a module (scripts/ is not a package).
_SPEC = importlib.util.spec_from_file_location(
    "dsr_ab", Path(__file__).resolve().parent.parent / "scripts" / "dsr_ab.py")
dsr_ab = importlib.util.module_from_spec(_SPEC)
sys.modules["dsr_ab"] = dsr_ab   # so dataclass string annotations resolve at exec time
_SPEC.loader.exec_module(dsr_ab)

GAMMA = 0.5772156649015329


def _ref_sr0(V: float, N: int) -> float:
    """Independent reference for the expected-max benchmark (hand formula)."""
    z1 = norm.ppf(1.0 - 1.0 / N)
    z2 = norm.ppf(1.0 - 1.0 / (N * math.e))
    return math.sqrt(V) * ((1.0 - GAMMA) * z1 + z2 * GAMMA)


def _stats():
    """Fixed N=4 synthetic strategies (hand-chosen)."""
    return [
        dsr_ab.StratStat("s1", sr=0.05, n=1000, skew=-0.2, kurt=4.0, sr_ann=float("nan")),
        dsr_ab.StratStat("s2", sr=0.03, n=1000, skew=0.1, kurt=3.5, sr_ann=float("nan")),
        dsr_ab.StratStat("s3", sr=0.04, n=1000, skew=0.0, kurt=3.0, sr_ann=float("nan")),
        dsr_ab.StratStat("s4", sr=0.06, n=1000, skew=-0.1, kurt=5.0, sr_ann=float("nan")),
    ]


# --------------------------------------------------------------------------- #
# (a) sr0(V, N) closed form                                                    #
# --------------------------------------------------------------------------- #
def test_a_sr0_matches_hand_computed():
    N = 4
    # Pinned literals (computed independently with scipy.norm.ppf); guards a
    # systematic ppf/γ mistake, not just a re-derivation.
    assert dsr_ab.sr0(0.001, N) == pytest.approx(0.03327104502687088, abs=1e-12)
    assert dsr_ab.sr0(0.0025, N) == pytest.approx(0.052606141209465024, abs=1e-12)
    # And matches the independent reference across each convention's V on real stats.
    st = _stats()
    srs = [s.sr for s in st]
    V_A, fb = dsr_ab.v_shared(srs)
    assert not fb
    assert dsr_ab.sr0(V_A, N) == pytest.approx(_ref_sr0(V_A, N), abs=1e-12)
    for s in st:
        vB1 = dsr_ab.v_b1(s.n)
        vB2 = dsr_ab.v_b2(s.sr, s.n, s.skew, s.kurt)
        assert dsr_ab.sr0(vB1, N) == pytest.approx(_ref_sr0(vB1, N), abs=1e-12)
        assert dsr_ab.sr0(vB2, N) == pytest.approx(_ref_sr0(vB2, N), abs=1e-12)
    # N == 1 ⇒ no selection inflation.
    assert dsr_ab.sr0(V_A, 1) == 0.0


# --------------------------------------------------------------------------- #
# (b) PRODUCTION column is B2 == risk.deflated_sharpe_ratio(own-Sharpe V);      #
#     A is the historical reference column (shared empirical V).                #
# --------------------------------------------------------------------------- #
def test_b_dsr_b2_is_production_and_a_is_historical():
    st = _stats()
    n_trials = len(st)
    rows, meta = dsr_ab.compute_dsrs(st, n_trials)

    # v_b2 delegates to the single source of truth (risk.sharpe_estimator_variance).
    for s in st:
        assert dsr_ab.v_b2(s.sr, s.n, s.skew, s.kurt) == \
            risk.sharpe_estimator_variance(s.sr, s.n, s.skew, s.kurt)

    # PRODUCTION (B2): each strategy's DSR_B2 == deflated_sharpe_ratio with its OWN
    # per-strategy Sharpe-estimator variance — the exact wiring compare.py now ships.
    for s in st:
        own_v = risk.sharpe_estimator_variance(s.sr, s.n, s.skew, s.kurt)
        expected_b2 = risk.deflated_sharpe_ratio(s.sr, s.n, s.skew, s.kurt, n_trials, own_v)
        assert rows[s.name]["dsr_b2"] == expected_b2   # bit-identical: same fn, own V

    # HISTORICAL (A): still the shared empirical cross-strategy V (the reference column).
    V_A, fb = dsr_ab.v_shared([s.sr for s in st])
    assert not fb
    assert meta["V_A"] == V_A
    for s in st:
        expected_a = risk.deflated_sharpe_ratio(s.sr, s.n, s.skew, s.kurt, n_trials, V_A)
        assert rows[s.name]["dsr_a"] == expected_a     # bit-identical: same fn, shared V

    # The independent hand-formula self-check passes to 1e-9 for all three columns.
    assert dsr_ab._selfcheck_1e9(rows, n_trials) < 1e-9


# --------------------------------------------------------------------------- #
# (c) DECOUPLING — the core claim                                             #
# --------------------------------------------------------------------------- #
def test_c_perturbing_s4_couples_A_but_not_B1_B2():
    st = _stats()
    n_trials = len(st)
    base, _ = dsr_ab.compute_dsrs(st, n_trials)

    # Perturb ONLY strategy 4's Sharpe (a peer change), everything else fixed.
    pert_st = [
        s if s.name != "s4"
        else dsr_ab.StratStat(s.name, sr=s.sr * 1.5, n=s.n, skew=s.skew, kurt=s.kurt)
        for s in st
    ]
    pert, _ = dsr_ab.compute_dsrs(pert_st, n_trials)

    # A couples: EVERY strategy's DSR_A moves (including the untouched peers 1–3).
    for name in ("s1", "s2", "s3", "s4"):
        assert abs(pert[name]["dsr_a"] - base[name]["dsr_a"]) > 1e-6

    # B1 and B2 are DECOUPLED: peers 1–3 are BIT-IDENTICAL (exactly 0 change).
    for name in ("s1", "s2", "s3"):
        assert pert[name]["dsr_b1"] - base[name]["dsr_b1"] == 0.0
        assert pert[name]["dsr_b2"] - base[name]["dsr_b2"] == 0.0
        # and their V inputs never moved either
        assert pert[name]["vB1"] == base[name]["vB1"]
        assert pert[name]["vB2"] == base[name]["vB2"]

    # The shared V_A DID change (that is the coupling channel).
    assert base["s1"]["vA"] != pert["s1"]["vA"]


def test_c_coupling_via_returns_perturbation_holds_to_1e12():
    """End-to-end coupling_experiment: peers invariant under B1/B2 to < 1e-12."""
    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(7)
    idx = pd.date_range("2020-01-01", periods=800, freq="D")
    st = []
    for i, mu in enumerate((0.0008, 0.0004, 0.0006, -0.0003)):
        r = pd.Series(rng.normal(mu, 0.02, len(idx)), index=idx)
        s = dsr_ab._stat_from_returns(f"strat{i}" if i else "pairs_coint", r, ppy=365)
        st.append(s)
    exp = dsr_ab.coupling_experiment(st, n_trials=len(st), ppy=365,
                                     target="pairs_coint", scales=(1.5, 0.5))
    assert exp["present"]
    moved_A = False
    for blk in exp["scales"].values():
        for d in blk["peers"].values():
            assert abs(d["dB1"]) < 1e-12
            assert abs(d["dB2"]) < 1e-12
            if abs(d["dA"]) > 1e-6:
                moved_A = True
    assert moved_A  # A actually coupled the peers


# --------------------------------------------------------------------------- #
# (d) B2 → B1 when skew=0, kurt=3, SR=0                                         #
# --------------------------------------------------------------------------- #
def test_d_b2_reduces_to_b1_when_gaussian_zero_sr():
    n = 1000
    vB2 = dsr_ab.v_b2(sr=0.0, n=n, skew=0.0, kurt=3.0)
    vB1 = dsr_ab.v_b1(n)
    assert vB2 == pytest.approx(1.0 / (n - 1), abs=1e-15)   # exact own-variance form
    assert vB2 == pytest.approx(vB1, rel=2e-3)             # ≈ 1/n for large n (differ by n/(n-1))
    # DSRs agree closely too (same SR/skew/kurt, only V differs by the (n-1) vs n).
    N = 4
    d_b1 = risk.deflated_sharpe_ratio(0.0, n, 0.0, 3.0, N, vB1)
    d_b2 = risk.deflated_sharpe_ratio(0.0, n, 0.0, 3.0, N, vB2)
    assert d_b2 == pytest.approx(d_b1, abs=1e-3)
