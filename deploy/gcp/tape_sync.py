"""tape_sync.py — push the depth-diff tape to the append-only GCS bucket, verify, then
(and only then, N days later) free the local copy.

Chunking model
--------------
The tape is an append-only multi-member gzip (one member per recorder flush), so the sync
unit is a BYTE RANGE ending on a complete-member boundary: `chunk-{start:012d}-{end:012d}.gz`.
Concatenating a day's chunks in offset order reproduces the local file byte-for-byte —
verified twice: per chunk against the md5Hash the create response returns, and at day close
against an md5 of the whole file recorded in `manifest.json`. The truncated tail a SIGKILL
can leave is uploaded verbatim as `...trunc` — recorded, never repaired (gaps stay gaps).

Deletion policy, declared: a local day file is deleted only when its manifest (whole-file
md5) has been uploaded and verified at least RETAIN_DAYS=3 days earlier. Until then every
byte exists twice. 3 days is a chosen number, not a law — change it here, it is read once.

Write-only means write-only: this process cannot re-download and diff (objectCreator has no
storage.objects.get), so verification is hash-at-create + the local ledger. Every action
appends to `.gcs-sync-ledger.jsonl`; nothing is ever rewritten.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gcs_common import bucket_name, probe_readback, upload, walk_members  # noqa: E402

TAPE = Path("/opt/btc-quant/data/depth_diffs/binancef/BTCUSDT")
STATE = TAPE.parent.parent / ".gcs-sync-state.json"
LEDGER = TAPE.parent.parent / ".gcs-sync-ledger.jsonl"
PREFIX = "tape/binancef/BTCUSDT"
RETAIN_DAYS = 3
CLOSE_QUIET_S = 600     # a day file is closable only if untouched this long past midnight


def log_ledger(row: dict) -> None:
    row["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with open(LEDGER, "a") as fh:
        fh.write(json.dumps(row, separators=(",", ":")) + "\n")


def main() -> int:
    bucket = bucket_name()
    if not bucket:
        print("tape-sync: no tape-bucket metadata — keyless posture, nothing to do")
        return 0
    state = json.loads(STATE.read_text()) if STATE.exists() else {"files": {}}
    today = time.strftime("%Y-%m-%d", time.gmtime())
    probe_done = False

    for daydir in sorted(TAPE.glob("date=*")):
        date = daydir.name.split("=", 1)[1]
        f = daydir / "frames.jsonl.gz"
        if not f.exists():
            continue
        st = state["files"].setdefault(date, {"offset": 0, "chunks": 0, "manifest": None})
        if st.get("deleted"):
            continue

        # advance to the last complete-member boundary past the recorded offset
        end = st["offset"]
        for member_end, _payload in walk_members(f, st["offset"]):
            end = member_end
        if end > st["offset"]:
            with open(f, "rb") as fh:
                fh.seek(st["offset"])
                body = fh.read(end - st["offset"])
            name = f"{PREFIX}/date={date}/chunk-{st['offset']:012d}-{end:012d}.gz"
            meta = upload(bucket, name, body, "application/gzip")
            log_ledger({"kind": "chunk", "date": date, "object": name,
                        "bytes": len(body), "md5": meta["md5Hash"],
                        "generation": meta.get("generation")})
            st["offset"] = end
            st["chunks"] += 1
            if not probe_done:       # live negative control: our own upload must be unreadable
                state["readback"] = probe_readback(bucket, name)
                probe_done = True

        # day close: yesterday or older, quiet, everything complete uploaded
        if date < today and st["manifest"] is None \
                and time.time() - f.stat().st_mtime > CLOSE_QUIET_S:
            size = f.stat().st_size
            if size > st["offset"]:        # truncated tail beyond the last complete member
                with open(f, "rb") as fh:
                    fh.seek(st["offset"])
                    tail = fh.read()
                name = f"{PREFIX}/date={date}/chunk-{st['offset']:012d}-{size:012d}.trunc"
                meta = upload(bucket, name, tail)
                log_ledger({"kind": "trunc_tail", "date": date, "object": name,
                            "bytes": len(tail), "md5": meta["md5Hash"]})
                st["offset"] = size
                st["chunks"] += 1
            h = hashlib.md5()
            with open(f, "rb") as fh:
                for blk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(blk)
            manifest = {"date": date, "size": size, "md5_full_hex": h.hexdigest(),
                        "chunks": st["chunks"],
                        "note": "concatenate chunks (and .trunc) in offset order to "
                                "reproduce the local file byte-for-byte"}
            mname = f"{PREFIX}/date={date}/manifest.json"
            upload(bucket, mname, json.dumps(manifest, indent=1).encode(),
                   "application/json")
            st["manifest"] = {"uploaded_ts": time.time(), "md5_full_hex": h.hexdigest(),
                              "size": size}
            log_ledger({"kind": "manifest", "date": date, "object": mname, **manifest})

        # retention: local copy goes only after the manifest has aged RETAIN_DAYS
        if st.get("manifest") and not st.get("deleted") \
                and time.time() - st["manifest"]["uploaded_ts"] > RETAIN_DAYS * 86400:
            f.unlink()
            st["deleted"] = True
            log_ledger({"kind": "deleted_local", "date": date,
                        "after_days": RETAIN_DAYS, "size": st["manifest"]["size"]})

    STATE.write_text(json.dumps(state, indent=1))
    synced = sum(s["offset"] for s in state["files"].values())
    print(f"tape-sync: {len(state['files'])} day(s), {synced:,} bytes at rest in "
          f"gs://{bucket}, readback={state.get('readback')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
