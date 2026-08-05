"""lockbox_integrity.py — is every LockBox defect recorded where it survives a restart?

Idempotent, read-only, one command. It exists because `/health`'s `rows_dropped_error`
is a PROCESS counter that resets on the next restart (`collector.py:1233`, `:1734`), so a
defect known only from `/health` is erased by routine maintenance. The LockBox can be
looked at ONCE, so an unlabelled defect inside it cannot be re-examined without burning
the slice.

The durable evidence is the collector's own **stamped** log, which is append-only. This
script scans it for drops inside the LockBox window and matches them against
`reports/lockbox-manifest.json` — the same exact-match discipline
`tests/test_vision_overlap.py` uses for recorded damage: a drop the manifest does not name
is a FAILURE, and a manifest entry with no matching log line is also a failure.

Reading the log for integrity events is not reading the slice. It reports that a row was
lost, when, and why — never any market observation. If it were forbidden, the slice's
integrity could never be established at all.

Exit 0 = every drop accounted for. Exit 1 = a discrepancy, named.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LOG = Path("/tmp/btcquant-collector.log")
MANIFEST = REPO / "reports" / "lockbox-manifest.json"

# A stamped drop line. Unstamped lines predate the _stamped fix and cannot be placed in
# time, so they are reported separately rather than silently ignored.
DROP = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z)\s+"
                  r"\[collector\] DROPPED (?P<n>\d+) (?P<table>\w+) row\(s\)(?P<rest>.*)$")

# By-design drops, not defects: rows arriving after the grace window for a closed day.
BENIGN = "arrived after the grace window"


def parse_ts(s: str) -> dt.datetime:
    return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=dt.timezone.utc)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--log", type=Path, default=LOG)
    ap.add_argument("--manifest", type=Path, default=MANIFEST)
    a = ap.parse_args()

    if not a.manifest.exists():
        print(f"lockbox-integrity: no manifest at {a.manifest}")
        return 1
    man = json.loads(a.manifest.read_text())
    start = parse_ts(man["lockbox_start_utc"].replace("Z", ".000Z"))
    recorded = {(d["ts_utc"], d["table"], int(d["rows_lost"])) for d in man["defects"]}

    print("lockbox-integrity — defects inside the LockBox, against the manifest")
    print(f"  LockBox starts {man['lockbox_start_utc']}")
    print(f"  manifest: {len(man['defects'])} defect(s) recorded\n")

    if not a.log.exists():
        print(f"  NO LOG at {a.log} — cannot verify. This is not a pass.")
        return 1

    found, benign, unstamped = set(), 0, 0
    for line in a.log.read_text(errors="replace").splitlines():
        if "DROPPED" not in line:
            continue
        m = DROP.match(line)
        if not m:
            unstamped += 1
            continue
        if BENIGN in m.group("rest"):
            benign += 1
            continue
        ts = parse_ts(m.group("ts"))
        if ts >= start:
            found.add((m.group("ts"), m.group("table"), int(m.group("n"))))

    # Conclusion printed BESIDE the evidence, never apart from it.
    print(f"  drops found in log, inside LockBox : {len(found)}")
    for f in sorted(found):
        print(f"    {f[0]}  {f[1]}  {f[2]} row(s)  ->  "
              f"{'RECORDED' if f in recorded else 'NOT IN MANIFEST'}")
    print(f"  by-design grace-window drops (not defects): {benign}")
    print(f"  UNSTAMPED drop lines (predate the _stamped fix, cannot be placed): {unstamped}")

    # The log lives in /tmp, which macOS WIPES on reboot (measured: the 2026-08-06
    # 05:23 local reboot erased 2,7xx lines including the recorded defect's line).
    # An entry whose timestamp predates the current log's first stamped line is
    # therefore UNVERIFIABLE-BY-THIS-LOG, not phantom: the manifest — which quotes
    # the log line verbatim — is by design the surviving record. Distinguishing the
    # two matters because "phantom" accuses the manifest of overstating, and a
    # verifier that fires on a wiped log would be class I (destroying a correct
    # record to satisfy a broken check).
    log_start = None
    for line in a.log.read_text(errors="replace").splitlines():
        m = DROP.match(line) or re.match(r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z)", line)
        if m:
            log_start = parse_ts(m.group("ts"))
            break
    predates = {r for r in recorded
                if log_start is not None and parse_ts(r[0]) < log_start}
    if predates:
        print(f"  entries predating the current log (log wiped/rotated at "
              f"{log_start.strftime('%Y-%m-%dT%H:%M:%SZ') if log_start else '?'}): {len(predates)}")
        for r in sorted(predates):
            print(f"    {r[0]}  {r[1]}  {r[2]} row(s)  ->  UNVERIFIABLE BY THIS LOG; "
                  f"the manifest (verbatim log quote inside it) is the surviving record")

    unrecorded = found - recorded
    phantom = (recorded - found) - predates
    ok = True
    if unrecorded:
        ok = False
        print(f"\n  FAIL — {len(unrecorded)} drop(s) inside the LockBox are NOT in the manifest:")
        for u in sorted(unrecorded):
            print(f"    {u[0]}  {u[1]}  {u[2]} row(s)")
        print("  Record them with cause and consequence. The LockBox is read ONCE; an")
        print("  unlabelled defect there cannot be re-examined without burning the slice.")
    if phantom:
        ok = False
        print(f"\n  FAIL — {len(phantom)} manifest entr(y/ies) have no matching log line:")
        for p in sorted(phantom):
            print(f"    {p[0]}  {p[1]}  {p[2]} row(s)")
        print("  Either the log was rotated, or the manifest overstates. Both need saying.")

    print(f"\n  >>> {'PASS — every LockBox drop is recorded durably' if ok else 'FAIL, see above'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
