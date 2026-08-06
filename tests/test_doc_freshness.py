"""test_doc_freshness.py — the gate must bite, and must not bite correct work.

`scripts/doc_freshness.py` is a VERIFIER, so per the class-I rail it is tested on cases
known to PASS as well as cases known to FAIL. A checker validated only against failures
has measured recall and never precision, and bad precision in a checker destroys correct
work rather than merely adding noise.

Fixtures live in `tests/fixtures/doc_freshness/`:
  clean/            a correct corpus — MUST pass (precision)
  exempt_paths/     the same violations, correctly marked — MUST pass (precision)
  violate_a1_*/     a `FILE:NNN` pointer in a living doc — MUST fail on A1
  violate_a2_*/     a look-counter value outside its owner — MUST fail on A2
  violate_a3_*/     fast-moving state outside `docs/STATUS.md` — MUST fail on A3

Each negative control asserts the RIGHT assert fired, not merely that something did — a
gate that fails for the wrong reason is a gate whose next failure teaches nothing.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "doc_freshness.py"
FIX = REPO / "tests" / "fixtures" / "doc_freshness"


def run(root: Path) -> tuple[int, str]:
    p = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root)],
        capture_output=True, text=True, cwd=str(REPO))
    return p.returncode, p.stdout


def counts(out: str) -> dict[str, int]:
    """Parse the per-assert violation counts out of the report."""
    got = {}
    for line in out.splitlines():
        for key in ("A1", "A2", "A3"):
            if line.strip().startswith(key) and "violation" in line:
                got[key] = int(line.split(":")[-1].split("violation")[0].strip())
    return got


# --------------------------------------------------------------------------- #
# PRECISION — the half a failure-only test suite never measures.               #
# --------------------------------------------------------------------------- #
def test_clean_corpus_passes():
    """A correct corpus must PASS. Without this the gate could be `return 1`."""
    code, out = run(FIX / "clean")
    assert code == 0, f"clean corpus rejected:\n{out}"
    assert counts(out) == {"A1": 0, "A2": 0, "A3": 0}, out
    assert "PASS" in out


def test_marked_exemptions_pass_and_are_reported():
    """[HISTORICAL], strikethrough and dated measurements are legitimate — and every
    exemption must be COUNTED, so nothing can hide behind one."""
    code, out = run(FIX / "exempt_paths")
    assert code == 0, f"correctly-marked exemptions rejected:\n{out}"
    assert "exemptions taken:" in out
    assert "[HISTORICAL] marker 1" in out or "[HISTORICAL] marker" in out
    # the exemptions are listed, not merely tallied
    assert "exempt (" in out


# --------------------------------------------------------------------------- #
# RECALL — each negative control fires the RIGHT assert.                       #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("fixture,expect", [
    ("violate_a1_pointer", "A1"),
    ("violate_a2_counter", "A2"),
    ("violate_a3_state", "A3"),
])
def test_each_violation_is_caught_by_its_own_assert(fixture, expect):
    code, out = run(FIX / fixture)
    assert code == 1, f"{fixture} was not caught:\n{out}"
    c = counts(out)
    assert c[expect] > 0, f"{fixture} did not trip {expect}: {c}\n{out}"
    for other in ("A1", "A2", "A3"):
        if other != expect:
            assert c[other] == 0, f"{fixture} also tripped {other} — cross-talk: {c}"
    assert "FAIL" in out


def test_a1_names_the_offending_pointer():
    """A failure message that does not name the pointer cannot be acted on."""
    _, out = run(FIX / "violate_a1_pointer")
    assert "STRATEGY.md:804" in out


def test_a2_names_the_file_carrying_the_duplicate_value():
    _, out = run(FIX / "violate_a2_counter")
    assert "STATUS.md" in out and "535" in out


def test_a3_labels_which_kind_of_fast_moving_fact():
    _, out = run(FIX / "violate_a3_state")
    assert "migration state" in out or "partition count" in out


# --------------------------------------------------------------------------- #
# The live repo. This is the gate doing its job, not a fixture.                #
# --------------------------------------------------------------------------- #
def test_the_repo_itself_is_free_of_documentation_rot():
    code, out = run(REPO)
    assert code == 0, (
        "documentation rot detected in the repo — fix the DOC, never widen the "
        f"checker:\n{out}")
