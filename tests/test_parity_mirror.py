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
    for name in ("dsr_n1", "dsr_mid", "wf_varTrialsSr", "wf_deflatedSharpe", "wf_varFallback"):
        assert name in py_src, f"{name} probe missing from check_parity.py"
        assert name in js_src, f"{name} probe missing from _parity_eval.cjs"
    # the anchor constants themselves (a joint drift must fail the pins, not parity)
    assert "0.8933576314257702" in py_src
    assert "0.6072585304659127" in py_src
