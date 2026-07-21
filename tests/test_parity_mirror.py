"""JS<->Python mirror parity, as a first-class test.

Runs ``scripts/check_parity.py`` (which evaluates every shared formula in both the
Python engine and ``dashboard/quant.js`` on one fixed fixture) and asserts they agree.
Skipped when Node is unavailable — CI always installs Node, so it runs there.
"""
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "check_parity.py")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH; parity runs in CI")
def test_js_python_parity_holds():
    proc = subprocess.run([sys.executable, SCRIPT], capture_output=True, text=True)
    if proc.returncode == 2:
        pytest.skip("node unavailable at run time")
    assert proc.returncode == 0, (
        "JS<->Python parity FAILED — a shared formula diverged:\n"
        + proc.stdout + "\n" + proc.stderr
    )


# ─── M6 unsaturated DSR pins (C1/C2/C3/C5) ─────────────────────────────────
# The old nTrials=10/var=1.0 parity point is ~0-saturated (deep normal tail) and
# detects nothing; these pin the N=1 special case and a mid-range value on the
# Python side (node-free), while check_parity.py holds JS to the same numbers.

def test_dsr_n1_is_psr_and_pinned():
    """C1: N=1 ⟹ sr0=0 ⟹ DSR ≡ PSR(0) — for ANY trial variance — ≈0.8934."""
    from btcquant import risk

    psr = risk.probabilistic_sharpe_ratio(0.08, 250, -0.3, 4.0)
    for var_trials in (123.456, 1.0, 0.0, 1e-9):
        assert risk.deflated_sharpe_ratio(0.08, 250, -0.3, 4.0, 1, var_trials) == psr
    assert psr == pytest.approx(0.8933576314257702, abs=1e-7)


def test_dsr_mid_range_pinned():
    """Mid-range point (N=5, V=0.001, sr=0.05/period, n=500) — where drift shows."""
    from btcquant import risk

    assert risk.deflated_sharpe_ratio(0.05, 500, -0.3, 4.0, 5, 0.001) == pytest.approx(
        0.6072585304659127, abs=1e-7
    )


def test_parity_harness_covers_unsaturated_and_walkforward_probes():
    """The new probes must stay wired in BOTH halves of the parity harness —
    dropping them from either side would silently un-pin the mirror."""
    py_src = open(SCRIPT, encoding="utf-8").read()
    js_src = open(os.path.join(ROOT, "scripts", "_parity_eval.cjs"), encoding="utf-8").read()
    for name in ("dsr_n1", "dsr_mid", "wf_varTrialsSr", "wf_deflatedSharpe", "wf_varFallback",
                 # M2 pairs two-leg-cost probe (the pairs path was UNPINNED before M2)
                 "pairs_ethTurnover", "pairs_btcTurnover", "pairs_totalTurnover",
                 # M9 pairs delta-neutral P&L probe (completes M2: spread return + two-leg cost)
                 "pairs_dnGrossSum", "pairs_dnNetEquity",
                 # M8 options-parity probe (max_pain + gamma_concentration past the greeks)
                 "mp_maxPain", "mp_pcOiRatio", "mp_forward",
                 "gc_sum", "gc_dot", "gc_peakStrike",
                 # FST (False Strategy Theorem, Bailey-LdP 2014) probes — the expected-max
                 # refactor + the new surfaced diagnostics (threshold, N_eff, P(false)).
                 "emaxN5", "emaxN10", "fstThreshold", "neffTrials", "probFalseStrategy",
                 # Hierarchical-Bayes shrinkage probes (frontier #3) — mu/tau scalars,
                 # the ELEMENTWISE shrunk/B/p vectors, and the correlation-aware tau.
                 "hb_mu", "hb_tau", "hb_shrunk", "hb_shrinkFactor", "hb_pSkill",
                 "hb_neffTau",
                 # EVT POT-GPD tail probes (frontier #2) — the PWM fit (xi/beta), the
                 # threshold u, and the 99% tail VaR/ES on the fixed LCG series.
                 "evt_xi", "evt_beta", "evt_u", "evt_var", "evt_cvar"):
        assert name in py_src, f"{name} probe missing from check_parity.py"
        assert name in js_src, f"{name} probe missing from _parity_eval.cjs"
    # the anchor constants themselves (a joint drift must fail the pins, not parity)
    assert "0.8933576314257702" in py_src
    assert "0.6072585304659127" in py_src
    # M8 options anchors — max_pain strike + total gamma density are pre-registered too
    assert "64000.0" in py_src
    assert "0.10701664008807263" in py_src
    # FST anchors — expected-max at N=5/N=10, the false-strategy threshold, N_eff=8/3,
    # and P(false); pre-registered so a joint Python↔JS drift fails the pins, not parity.
    assert "1.1925940010147893" in py_src
    assert "1.57459830134575" in py_src
    assert "0.11292934779100049" in py_src
    assert "2.6666666666666665" in py_src
    assert "0.3927414695340873" in py_src
    # Hierarchical-Bayes anchors — the pooled mu, the DL tau, and the correlation-aware
    # tau (df = N_eff-1) on the fixed k=4 family; pre-registered so a joint Python↔JS
    # drift fails the pins, not parity.
    assert "0.042615507958796955" in py_src
    assert "0.025259219485448958" in py_src
    assert "0.04758979651558059" in py_src
    # EVT POT-GPD anchors (frontier #2) — the PWM shape xi and the 99% tail VaR on the
    # fixed LCG fat-tailed series; pre-registered so a joint Python↔JS drift (e.g. both
    # sides flipping the PWM weight convention) fails the pins, not parity.
    assert "0.2483976106087591" in py_src
    assert "-0.0876387597442414" in py_src


def test_parity_options_fields_present_and_agree():
    """M8: the new options-parity fields (max_pain + gamma_concentration) must be
    computed on BOTH sides and agree — completing options parity past the Black-76
    greeks. Runs the harness and asserts the M8 fields appear and parity holds."""
    if shutil.which("node") is None:
        pytest.skip("node not on PATH; parity runs in CI")
    proc = subprocess.run([sys.executable, SCRIPT], capture_output=True, text=True)
    if proc.returncode == 2:
        pytest.skip("node unavailable at run time")
    assert proc.returncode == 0, (
        "M8 options parity FAILED:\n" + proc.stdout + "\n" + proc.stderr
    )
    for name in ("mp_maxPain", "mp_pcOiRatio", "mp_forward",
                 "gc_sum", "gc_dot", "gc_peakStrike"):
        assert name in proc.stdout, f"{name} missing from parity harness output"
