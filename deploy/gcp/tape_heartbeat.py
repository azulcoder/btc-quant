"""tape_heartbeat.py — a 5-minute status object, so the recorder can be SEEN from a
browser with no SSH and no trust in this machine's own opinion of itself.

Each run appends one small JSON object under `heartbeat/date=*/hb-HHMMSSZ.json` (append-only
like everything else in the bucket: a write-only principal cannot overwrite, so `latest` is
simply the lexically last object of today). The numbers come from the tape itself — frames
and gaps are counted from the bytes the recorder actually wrote since the previous
heartbeat, not from any in-process counter that would die with the process (class A: the
witness must not share fate with the observed). Disk and service state come from the OS.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gcs_common import bucket_name, upload, walk_tape  # noqa: E402

TAPE = Path("/opt/btc-quant/data/depth_diffs/binancef/BTCUSDT")
HB_STATE = TAPE.parent.parent / ".hb-state.json"
SYNC_STATE = TAPE.parent.parent / ".gcs-sync-state.json"


def count_new(f: Path, offset: int) -> tuple[int, dict]:
    kinds = {"frame": 0, "gap": 0, "snapshot": 0, "start": 0, "stop": 0,
             "hole": 0, "hole_bytes": 0}
    end = offset
    for kind, span_end, payload in walk_tape(f, offset):
        end = span_end
        if kind == "hole":       # torn span: count it loudly, decode nothing from it
            kinds["hole"] += 1
            kinds["hole_bytes"] += len(payload)
            continue
        for line in payload.splitlines():
            try:
                k = json.loads(line).get("kind", "?")
            except json.JSONDecodeError:
                k = "?"
            kinds[k] = kinds.get(k, 0) + 1
    return end, kinds


def main() -> int:
    now = time.gmtime()
    today = time.strftime("%Y-%m-%d", now)
    st = json.loads(HB_STATE.read_text()) if HB_STATE.exists() else {}
    day = st.get("day", {})
    if day.get("date") != today:
        day = {"date": today, "offset": 0, "frames": 0, "gaps": 0, "snapshots": 0,
               "holes": 0, "hole_bytes": 0}

    f = TAPE / f"date={today}" / "frames.jsonl.gz"
    fresh = {}
    if f.exists():
        day["offset"], fresh = count_new(f, day["offset"])
        day["frames"] += fresh.get("frame", 0)
        day["gaps"] += fresh.get("gap", 0)
        day["snapshots"] += fresh.get("snapshot", 0)
        day["holes"] = day.get("holes", 0) + fresh.get("hole", 0)
        day["hole_bytes"] = day.get("hole_bytes", 0) + fresh.get("hole_bytes", 0)

    vfs = os.statvfs("/")
    svc = subprocess.run(["systemctl", "is-active", "btcquant-depth-recorder"],
                         capture_output=True, text=True).stdout.strip()
    sync = json.loads(SYNC_STATE.read_text()) if SYNC_STATE.exists() else {}
    hb = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", now),
        "service": svc,
        "frames_today": day["frames"], "gaps_today": day["gaps"],
        "snapshots_today": day["snapshots"],
        "frames_since_last_hb": fresh.get("frame", 0),
        "tape_holes_today": day.get("holes", 0),
        "tape_hole_bytes_today": day.get("hole_bytes", 0),
        "day_file_bytes": f.stat().st_size if f.exists() else 0,
        "disk_free_gb": round(vfs.f_bavail * vfs.f_frsize / 1e9, 2),
        "synced_bytes_total": sum(s.get("offset", 0)
                                  for s in sync.get("files", {}).values()),
        "readback_denied": (sync.get("readback") or {}).get("readback_denied"),
    }
    # A MONOTONIC COUNTER, not a finer clock. Objects here are un-overwritable by design,
    # so a duplicate name is a 403, and the first fix — appending milliseconds — only made
    # the collision rarer: two runs inside one millisecond still collide, which is exactly
    # how the test that models the constraint failed [DIUKUR 2026-08-08]. A counter kept in
    # the state file cannot collide however fast the runs are, and it survives the clock
    # moving backwards, which milliseconds do not. It is incremented and PERSISTED before
    # the upload, so a crash between the two burns a number rather than reusing one.
    seq = int(st.get("seq", 0)) + 1
    st["seq"] = seq
    st["day"] = day
    HB_STATE.write_text(json.dumps(st))
    line = (f"hb {hb['ts']} svc={svc} frames_today={hb['frames_today']:,} "
            f"gaps={hb['gaps_today']} disk_free={hb['disk_free_gb']}G")
    bucket = bucket_name()
    if not bucket:
        print(line + " (no bucket configured — heartbeat local only)")
        return 0
    name = (f"heartbeat/date={today}/"
            f"hb-{time.strftime('%H%M%S', now)}-{seq:08d}Z.json")
    upload(bucket, name, json.dumps(hb, indent=1).encode(), "application/json",
           timeout=60)
    print(line + f" -> gs://{bucket}/{name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
