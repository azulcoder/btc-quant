"""verify_tape_hf.py — positive control for the GCS->HF link: pull the tape BACK from
Hugging Face, reassemble it, count what is in it, and compare with the VM's own heartbeat.

This is the same control the GCS link already passed, one hop further out. It is run from
a machine that is neither the VM nor the bucket, over a transport that shares no library
with the uploader, so agreement is evidence about the bytes rather than about one client
agreeing with itself.

Two measured hazards are handled here rather than assumed away [DIUKUR 2026-08-08]:

* Hugging Face's CDN truncates large streams — a 26 MB chunk came back as 15,320,498 of
  26,205,121 bytes, and a plain reader sees the drop as a clean EOF. Every fetch therefore
  checks the byte count against the size encoded in the object name and retries.
* Chunk names carry their byte range, so continuity is checkable without trusting any
  index: chunk N's start must equal chunk N-1's end, or there is a hole in the mirror and
  this script says so instead of concatenating across it.
"""

from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import shutil
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HF_REPO = "azulcoder/btc-quant-ticks"
UA = {"User-Agent": "btc-quant/1.0 (research; keyless)"}


def _walker():
    spec = importlib.util.spec_from_file_location(
        "gcs_common", REPO / "deploy" / "gcp" / "gcs_common.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def fetch(path_in_repo: str, want: int, tries: int = 5, timeout: int = 300) -> bytes:
    url = (f"https://huggingface.co/datasets/{HF_REPO}/resolve/main/"
           f"{urllib.parse.quote(path_in_repo)}")
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                buf = bytearray()
                while True:
                    blk = r.read(1 << 20)
                    if not blk:
                        break
                    buf.extend(blk)
            if len(buf) != want:
                raise IOError(f"short read {len(buf)} of {want} B")
            return bytes(buf)
        except Exception as e:                      # noqa: BLE001 — retry any transport fault
            last = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"{path_in_repo}: {last}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", required=True)
    ap.add_argument("--expect-frames", type=int, default=None,
                    help="heartbeat frames_today to compare against")
    a = ap.parse_args()
    gc = _walker()

    from huggingface_hub import HfApi
    pref = f"depth_tape/binancef/BTCUSDT/date={a.date}"
    paths = [f.path for f in HfApi().list_repo_tree(HF_REPO, path_in_repo=pref,
                                                    repo_type="dataset")]
    chunks = sorted((p for p in paths if "/chunk-" in p),
                    key=lambda p: int(p.split("chunk-")[1].split("-")[0]))
    if not chunks:
        print(f"no mirrored chunks for {a.date}")
        return 1

    tmp = Path(tempfile.mkdtemp())
    try:
        out = tmp / "reassembled.gz"
        prev = 0
        with open(out, "wb") as fh:
            for p in chunks:
                s, e = p.split("chunk-")[1].split(".")[0].split("-")
                if int(s) != prev:
                    print(f"  GAP IN MIRROR at byte {s} (expected {prev})")
                    return 2
                fh.write(fetch(p, int(e) - int(s)))
                prev = int(e)
        print(f"continuity: OK 0..{prev:,} B across {len(chunks)} object(s), no gap")

        kinds = collections.Counter()
        holes = hole_bytes = 0
        for kind, _end, payload in gc.walk_tape(out, 0):
            if kind == "hole":
                holes += 1
                hole_bytes += len(payload)
                continue
            for line in payload.splitlines():
                try:
                    kinds[json.loads(line).get("kind")] += 1
                except json.JSONDecodeError:
                    pass
        print(f"from HF: bytes={prev:,} frames={kinds['frame']:,} "
              f"snapshots={kinds['snapshot']} gaps={kinds['gap']} "
              f"holes={holes} ({hole_bytes:,} B)")
        if a.expect_frames is not None:
            ok = kinds["frame"] == a.expect_frames
            print(f"vs heartbeat {a.expect_frames:,}: "
                  f"{'MATCH' if ok else 'MISMATCH'}")
            return 0 if ok else 2
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
