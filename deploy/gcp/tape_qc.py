"""tape_qc.py — recorder quality census, computed from the tape and nothing else.

Answers, per UTC hour and per day: actual frame rate, chain-verdict breakdown, explicit
gap rows by action, resyncs (snapshots whose reason is `chain_break`), and the
event-time-vs-receive-time delta (`recv_ms − data.E`), which is the only latency proxy a
single-clock machine can honestly produce — it contains the venue's send delay AND this
box's clock error, and is labeled a proxy for that reason.

Everything here is derivable because the recorder writes raw frames with both timestamps,
lifecycle rows, and explicit gap/snapshot rows. If a future metric cannot be computed from
the tape alone, that is a recorder defect to fix at the source, not to patch here.

Run at boot (startup script prints the summary to the serial console) and on demand.
`--upload` puts the full JSON in the bucket under `qc/`, so quality history accumulates
next to the data it describes.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gcs_common import bucket_name, upload, walk_members  # noqa: E402

TAPE = Path("/opt/btc-quant/data/depth_diffs/binancef/BTCUSDT")


def pct(sorted_vals: list[int], q: float) -> int | None:
    if not sorted_vals:
        return None
    return sorted_vals[min(int(q * len(sorted_vals)), len(sorted_vals) - 1)]


def census() -> dict:
    days = {}
    for daydir in sorted(TAPE.glob("date=*")):
        f = daydir / "frames.jsonl.gz"
        if not f.exists():
            continue
        hours: dict[int, dict] = defaultdict(lambda: {
            "frames": 0, "chain": defaultdict(int), "gap_rows": defaultdict(int),
            "resyncs": 0, "snapshots": 0, "lat": []})
        for _end, payload in walk_members(f, 0):
            for line in payload.splitlines():
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                h = time.gmtime(row.get("recv_ms", 0) / 1000).tm_hour
                b = hours[h]
                kind = row.get("kind")
                if kind == "frame":
                    b["frames"] += 1
                    b["chain"][row.get("chain", "?")] += 1
                    ev = row.get("data", {})
                    if isinstance(ev.get("E"), int):
                        b["lat"].append(row["recv_ms"] - ev["E"])
                elif kind == "gap":
                    b["gap_rows"][row.get("action", "?")] += 1
                elif kind == "snapshot":
                    b["snapshots"] += 1
                    if row.get("reason") == "chain_break":
                        b["resyncs"] += 1
        out = {}
        for h, b in sorted(hours.items()):
            lat = sorted(b["lat"])
            out[f"{h:02d}"] = {
                "frames": b["frames"],
                "fps": round(b["frames"] / 3600, 2),
                "chain": dict(b["chain"]),
                "gap_rows": dict(b["gap_rows"]),
                "resyncs": b["resyncs"],
                "snapshots": b["snapshots"],
                "lat_ms_p50_p90_p99": [pct(lat, .5), pct(lat, .9), pct(lat, .99)],
                "lat_ms_min_max": [lat[0], lat[-1]] if lat else [None, None],
            }
        days[daydir.name.split("=", 1)[1]] = {
            "file_bytes": f.stat().st_size, "hours": out,
            "frames": sum(b["frames"] for b in hours.values()),
            "gap_rows": sum(sum(b["gap_rows"].values()) for b in hours.values()),
            "resyncs": sum(b["resyncs"] for b in hours.values()),
        }
    return {"generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "tape_days": days}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--upload", action="store_true")
    a = ap.parse_args()
    qc = census()
    for d, info in qc["tape_days"].items():
        print(f"QC {d}: {info['frames']:,} frames · {info['gap_rows']} gap rows · "
              f"{info['resyncs']} resyncs · {info['file_bytes']/1e6:.1f} MB")
        for h, b in info["hours"].items():
            print(f"  {h}h {b['fps']:6.2f} fps · lat p50/p90/p99 "
                  f"{b['lat_ms_p50_p90_p99']} ms · gaps {sum(b['gap_rows'].values())} "
                  f"· resyncs {b['resyncs']}")
    if a.upload:
        bucket = bucket_name()
        if bucket:
            name = f"qc/qc-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.json"
            upload(bucket, name, json.dumps(qc, indent=1).encode(), "application/json")
            print(f"-> gs://{bucket}/{name}")
        else:
            print("-> no bucket configured, QC not uploaded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
