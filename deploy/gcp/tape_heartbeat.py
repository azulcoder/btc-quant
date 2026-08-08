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
from gcs_common import bucket_name, upload, walk_members  # noqa: E402

TAPE = Path("/opt/btc-quant/data/depth_diffs/binancef/BTCUSDT")
HB_STATE = TAPE.parent.parent / ".hb-state.json"
SYNC_STATE = TAPE.parent.parent / ".gcs-sync-state.json"


def count_new(f: Path, offset: int) -> tuple[int, dict]:
    kinds = {"frame": 0, "gap": 0, "snapshot": 0, "start": 0, "stop": 0}
    end = offset
    for member_end, payload in walk_members(f, offset):
        end = member_end
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
        day = {"date": today, "offset": 0, "frames": 0, "gaps": 0, "snapshots": 0}

    f = TAPE / f"date={today}" / "frames.jsonl.gz"
    fresh = {}
    if f.exists():
        day["offset"], fresh = count_new(f, day["offset"])
        day["frames"] += fresh.get("frame", 0)
        day["gaps"] += fresh.get("gap", 0)
        day["snapshots"] += fresh.get("snapshot", 0)

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
        "day_file_bytes": f.stat().st_size if f.exists() else 0,
        "disk_free_gb": round(vfs.f_bavail * vfs.f_frsize / 1e9, 2),
        "synced_bytes_total": sum(s.get("offset", 0)
                                  for s in sync.get("files", {}).values()),
        "readback_denied": (sync.get("readback") or {}).get("readback_denied"),
    }
    st["day"] = day
    HB_STATE.write_text(json.dumps(st))
    line = (f"hb {hb['ts']} svc={svc} frames_today={hb['frames_today']:,} "
            f"gaps={hb['gaps_today']} disk_free={hb['disk_free_gb']}G")
    bucket = bucket_name()
    if not bucket:
        print(line + " (no bucket configured — heartbeat local only)")
        return 0
    # Milliseconds in the name: objects here are un-overwritable by design, and the test
    # that models that constraint produced two heartbeats in one second — which the real
    # bucket would refuse with a 403. Persistent=true timer catch-ups make that reachable.
    ms = int(time.time() * 1000) % 1000
    name = f"heartbeat/date={today}/hb-{time.strftime('%H%M%S', now)}{ms:03d}Z.json"
    upload(bucket, name, json.dumps(hb, indent=1).encode(), "application/json",
           timeout=60)
    print(line + f" -> gs://{bucket}/{name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
