"""test_prereg_verdict_routing.py — a PREREG runner may not have an unreachable verdict.

A pre-registration is only binding if every outcome it can produce was mapped in advance.
PREREG-microstructure-001 declared INDETERMINATE for `gamma_1 >= 0` and then reached
INDETERMINATE through a NEGATIVE DISCRIMINANT, a door that was never declared. The verdict
happened to be right; the routing was luck.

So this asserts the mechanical half of the rule in the prereg-research skill (item 3b): a
runner's verdict assignment must be EXHAUSTIVE — every path through it ends at a named
verdict, with a fallback that catches what nobody anticipated. A runner that can fall off
the end of its own if/elif chain has an outcome it never declared.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RUNNERS = sorted(REPO.glob("scripts/prereg_*.py"))
VERDICT_NAMES = {"verdict", "final"}


def _assigns_verdict(node: ast.AST) -> bool:
    """Does this branch ASSIGN a verdict name?

    Deliberately narrower than "does it decide anything". A guard that RETURNS a verdict
    dict is already exhaustive — control leaves the function, and whatever follows is the
    fall-through case. The dangerous shape is an if/elif that BINDS a verdict variable and
    then continues: on an unanticipated input the name keeps a stale value or never binds,
    and the script reports a verdict nobody declared. The first version of this detector
    counted returns too and flagged four guards in a runner that were perfectly sound —
    a checker that cries wolf, which is the class-I failure this repo rails against.
    """
    for n in ast.walk(node):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name) and t.id in VERDICT_NAMES:
                    return True
                if isinstance(t, ast.Tuple) and any(
                        isinstance(e, ast.Name) and e.id in VERDICT_NAMES for e in t.elts):
                    return True
    return False


def test_there_is_at_least_one_prereg_runner_to_check():
    """Without this, the suite below passes vacuously the day the glob stops matching."""
    assert RUNNERS, "no scripts/prereg_*.py found — this file would pass on nothing"


def test_every_verdict_chain_is_exhaustive():
    """Every if/elif chain that assigns a verdict must end in an `else`.

    An `if/elif` with no `else` is a branch that was never declared: on an input the author
    did not imagine, the name keeps whatever value it had, or the code proceeds with none.
    """
    holes = []
    for path in RUNNERS:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.If):
                continue
            # only chains that actually decide a verdict
            if not any(_assigns_verdict(b) for b in node.body):
                continue
            tail = node
            while tail.orelse and len(tail.orelse) == 1 and isinstance(tail.orelse[0], ast.If):
                tail = tail.orelse[0]
            if not tail.orelse:
                holes.append(f"{path.name}:{node.lineno} verdict chain has no else branch")
    assert not holes, (
        "a PREREG runner can produce a verdict its declaration never mapped:\n  "
        + "\n  ".join(holes)
        + "\nAdd an else that routes to INDETERMINATE and amend the declaration (skill 3b).")


def test_the_detector_actually_catches_a_hole(tmp_path):
    """Negative control. Narrowing the detector fixed its precision; this measures recall.

    A checker that no longer fires on the sound guards is only half-verified — the other
    half is that it still fires on the shape it exists for. Without this, narrowing could
    have been quietly widened into "never complains", which is the failure mode that made
    the first version worthless in the opposite direction.
    """
    holed = tmp_path / "prereg_synthetic_hole.py"
    holed.write_text(
        "def main():\n"
        "    x = compute()\n"
        "    if x > 1:\n"
        "        verdict = 'PASS'\n"
        "    elif x < 0:\n"
        "        verdict = 'FAIL'\n"          # no else: 0 <= x <= 1 is undeclared
        "    return verdict\n")
    tree = ast.parse(holed.read_text())
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If) or not any(_assigns_verdict(b) for b in node.body):
            continue
        tail = node
        while tail.orelse and len(tail.orelse) == 1 and isinstance(tail.orelse[0], ast.If):
            tail = tail.orelse[0]
        if not tail.orelse:
            found.append(node.lineno)
    assert found, "the detector missed an if/elif verdict chain with no else branch"

    sound = tmp_path / "prereg_synthetic_sound.py"
    sound.write_text(
        "def main():\n"
        "    x = compute()\n"
        "    if x > 1:\n"
        "        verdict = 'PASS'\n"
        "    elif x < 0:\n"
        "        verdict = 'FAIL'\n"
        "    else:\n"
        "        verdict = 'INDETERMINATE'\n"
        "    return verdict\n")
    tree = ast.parse(sound.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and any(_assigns_verdict(b) for b in node.body):
            tail = node
            while tail.orelse and len(tail.orelse) == 1 and isinstance(tail.orelse[0], ast.If):
                tail = tail.orelse[0]
            assert tail.orelse, "the detector fires on a chain that IS exhaustive"
