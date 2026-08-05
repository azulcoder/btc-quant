"""churn_threshold.py — where are we against the §14b pre-registration?

Idempotent. Run it any time; it answers from `data/ticks/gaps.jsonl` alone and
changes nothing. The point is that the "has 36 hours passed?" decision must not
depend on anyone remembering, or on a monitor the harness can stop.

THE PRE-REGISTRATION IT REPORTS AGAINST (docs/EDA-microstructure-001.md §14b,
declared 2026-08-05 before the window it judges, and NOT revisable):

  CONFIRMS a real change  clean stretch >= 36.0 h
  REFUTES it              churn returns at >= 1.0 bursts/h over any 6 h window
  AMBIGUOUS               12-36 h clean, or 0.2-1.0 bursts/h — confirms nothing,
                          refutes nothing; the answer is more history

THIS SCRIPT IS A NEW INSTRUMENT, so per STRATEGY.md it must reproduce known values
before its own numbers are worth anything. FOUR were measured independently before
this file existed (docs/EDA-microstructure-001.md §14a):

    pre-fix ledger events        755        <- parse
    pre-fix bursts, 30 s join    119        <- burst collapse (join-sensitive)
    2026-08-01 06:03Z            31.29 h    <- gap arithmetic
    2026-08-04 10:45Z            11.42 h    <- gap arithmetic

The two counts exist because a negative test showed the durations alone were weak:
flipping BURST_JOIN_MS from 30 s to 300 s left both durations unchanged, since gaps
of 11-31 HOURS cannot be merged by a join measured in seconds. With the counts in,
that same perturbation now fails the control and the script refuses to report.

**The control runs on EVERY invocation, not once at authoring time.** If it fails,
the script refuses to report the current window at all — a broken instrument that
still prints a number is worse than one that stops (blindness class F/I).

Research only. Read-only: opens one file and writes nothing.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LEDGER = REPO / "data" / "ticks" / "gaps.jsonl"

BURST_JOIN_MS = 30_000          # baseline.md's method, unchanged so numbers compare
CONFIRM_H = 36.0                # §14b, binding
AMBIGUOUS_LOW_H = 12.0          # §14b, binding
REFUTE_RATE = 1.0               # bursts/h over a 6 h window, §14b, binding
AMBIGUOUS_RATE_LOW = 0.2        # §14b, binding
REFUTE_WINDOW_H = 6.0

# The fix went live here; §14b judges post-fix behaviour, so this is the clock's zero.
FIX_ACTIVE_MS = int(dt.datetime(2026, 8, 5, 0, 5, 28, tzinfo=dt.timezone.utc).timestamp() * 1000)

# Known values, measured before this script existed. Tolerance is 0.05 h — tight
# enough that a changed burst-join or a mis-parse fails, loose enough for rounding.
CONTROLS = [("2026-08-01 06:03Z", 31.29), ("2026-08-04 10:45Z", 11.42)]
CONTROL_TOL_H = 0.05

# Those two durations alone are a WEAK control, and a negative test proved it:
# changing BURST_JOIN_MS from 30 s to 300 s left both unchanged, because gaps of
# 11-31 HOURS cannot be merged by a join measured in seconds. So they validate the
# ledger parse and the gap arithmetic, and say nothing about the burst collapse.
# These two counts are join-sensitive and close the hole. Both are over the CLOSED
# pre-fix window, so they do not drift as the ledger grows.
CONTROL_PRE_FIX_EVENTS = 755    # raw ledger lines before the fix went live
CONTROL_PRE_FIX_BURSTS = 119    # after the 30 s collapse — changes if the join changes


def load_bursts(path: Path) -> list[int]:
    """Ledger events collapsed at a 30 s join -> one start timestamp per burst."""
    ev = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            ev.append(json.loads(line)["ts_ms"])
        except Exception:  # noqa: BLE001 — a malformed line is skipped, never guessed at
            continue
    ev.sort()
    if not ev:
        return []
    load_bursts.n_events = len(ev)   # noqa: B010 — the control needs the raw count
    out = [ev[0]]
    last = ev[0]
    for t in ev[1:]:
        if t - last > BURST_JOIN_MS:
            out.append(t)
        last = t
    return out


def quiet_periods(bursts: list[int]) -> list[tuple[int, float]]:
    """(start_ms, hours) for every gap between consecutive bursts."""
    return [(bursts[i - 1], (bursts[i] - bursts[i - 1]) / 3.6e6) for i in range(1, len(bursts))]


def run_control(quiet: list[tuple[int, float]], n_events: int,
                bursts: list[int]) -> tuple[bool, list[str]]:
    """Reproduce four known values. Returns (passed, lines to print).

    Two are durations (validate the parse and the gap arithmetic) and two are counts
    over the closed pre-fix window (validate the burst collapse, which the durations
    cannot reach). Every line prints its measurement beside its verdict.
    """
    lines, ok = [], True
    n_pre = sum(1 for b in bursts if b < FIX_ACTIVE_MS)
    for label, want, got in (("pre-fix ledger events", CONTROL_PRE_FIX_EVENTS, n_events),
                             ("pre-fix bursts (30 s join)", CONTROL_PRE_FIX_BURSTS, n_pre)):
        hit = want == got
        ok &= hit
        lines.append(f"    {label:<28} expected {want:<6} measured {got:<6} "
                     f"->  {'MATCH' if hit else 'MISMATCH'}")
    for label, expected in CONTROLS:
        want_start = dt.datetime.strptime(label, "%Y-%m-%d %H:%MZ").replace(
            tzinfo=dt.timezone.utc).timestamp() * 1000
        near = [(s, h) for s, h in quiet if abs(s - want_start) < 3_600_000]
        if not near:
            lines.append(f"    {label}  expected {expected:.2f} h  ->  NOT FOUND")
            ok = False
            continue
        s, h = max(near, key=lambda x: x[1])
        hit = abs(h - expected) <= CONTROL_TOL_H
        ok &= hit
        lines.append(f"    {label}  expected {expected:.2f} h  measured {h:.2f} h  "
                     f"->  {'MATCH' if hit else 'MISMATCH'}")
    return ok, lines


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ledger", type=Path, default=LEDGER)
    ap.add_argument("--now-ms", type=int, default=None,
                    help="override wall clock (testing only)")
    a = ap.parse_args()

    if not a.ledger.exists():
        print(f"churn-threshold: no ledger at {a.ledger} — cannot answer.")
        return 1

    bursts = load_bursts(a.ledger)
    quiet = quiet_periods(bursts)
    now = a.now_ms if a.now_ms is not None else int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)

    print("churn-threshold — against docs/EDA-microstructure-001.md §14b (binding)")
    print(f"  ledger {a.ledger}: {len(bursts):,} bursts (30 s join)\n")

    # ---- CONTROL FIRST. Its numbers print beside its verdict, never apart. ----
    print("  CONTROL — reproduce four values measured before this script existed:")
    passed, lines = run_control(quiet, getattr(load_bursts, 'n_events', 0), bursts)
    for ln in lines:
        print(ln)
    if not passed:
        print("\n  CONTROL FAILED -> refusing to report the current window.")
        print("  The script is wrong, not the data. Fix the instrument before trusting it.")
        return 2
    print("  CONTROL PASSED.\n")

    # ---- current clean stretch, measured from when the fix went live ----
    after_fix = [b for b in bursts if b >= FIX_ACTIVE_MS]
    start = max(after_fix[-1], FIX_ACTIVE_MS) if after_fix else FIX_ACTIVE_MS
    clean_h = (now - start) / 3.6e6
    last_led = (now - bursts[-1]) / 3.6e6 if bursts else float("nan")

    # ---- refute test: densest 6 h window since the fix ----
    worst = 0.0
    if after_fix:
        w = int(REFUTE_WINDOW_H * 3.6e6)
        for b in after_fix:
            n = sum(1 for x in after_fix if b <= x < b + w)
            worst = max(worst, n / REFUTE_WINDOW_H)

    if clean_h >= CONFIRM_H:
        verdict = f"CONFIRMS — {clean_h:.2f} h clean >= {CONFIRM_H} h"
    elif worst >= REFUTE_RATE:
        verdict = f"REFUTES — {worst:.2f} bursts/h >= {REFUTE_RATE} in a {REFUTE_WINDOW_H:.0f} h window"
    elif clean_h >= AMBIGUOUS_LOW_H or worst >= AMBIGUOUS_RATE_LOW:
        verdict = (f"AMBIGUOUS BAND — {clean_h:.2f} h clean, {worst:.2f} bursts/h. "
                   "Confirms nothing, refutes nothing; the answer is more history.")
    else:
        verdict = (f"TOO EARLY — {clean_h:.2f} h clean, below the {AMBIGUOUS_LOW_H} h band floor. "
                   "Not yet a result of any kind.")

    print(f"  clean since fix went live : {clean_h:>7.2f} h   (fix active 2026-08-05 00:05:28Z)")
    print(f"  clean per the ledger alone: {last_led:>7.2f} h   (last burst, ignoring the restart)")
    print(f"  densest 6 h window since  : {worst:>7.2f} bursts/h")
    print(f"  bursts since fix          : {len(after_fix):>7}")
    print(f"\n  >>> {verdict}")
    print(f"\n  remaining to CONFIRM: {max(0.0, CONFIRM_H - clean_h):.2f} h")
    return 0


if __name__ == "__main__":
    sys.exit(main())
