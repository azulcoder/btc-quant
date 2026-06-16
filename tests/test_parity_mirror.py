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
