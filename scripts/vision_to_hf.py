"""vision_to_hf.py — move ONE Vision day-partition to HF, then delete it locally.

Remote-first migration for the public-archive partitions (`DESIGN-vision-remote-first.md`).
Nothing here is new discipline: `upload_hf.py` already established it for the tick store —
*"No offsite verification, no local delete"* — and this reuses its primitives unchanged.
`hf_upload_file` and `hf_download_file` already take an arbitrary `path_in_repo`, so **no
generalisation of `upload_hf.py` was required**; only the prefix differs.

Seven states, and local deletion is gated on state 6, never on state 5:

    1 FETCH daily zip            (reuses ingest_vision.ingest_day)
    2 VERIFY zip sha256 vs the venue's published checksum   (ingest_day does this)
    3 NORMALIZE to parquet       (ingest_day)
    4 SHA256 the parquet         (ingest_day writes it into the manifest)
    5 UPLOAD to HF
    6 READ BACK from the hub and recompute sha256
    7 DELETE LOCAL — only if 6 matched

**DAILY granularity, never monthly.** Measured peak disk is ~51 MB/day against ~3.1 GB/month
(`DESIGN-vision-remote-first.md` §17a), and the monthly form is what caused the original ENOSPC.

Peak disk is SAMPLED during the run by a background thread, not estimated afterwards.

Research only. Deletes exactly one local file, and only after a verified remote read-back.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HF_REPO = "azulcoder/btc-quant-ticks"
VISION_PREFIX = "vision/binancef/BTCUSDT/aggTrades"
LOCAL_ROOT = REPO / "data" / "vision"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(4 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class DiskWatch:
    """Samples free bytes on a thread so peak usage is MEASURED, not inferred."""

    def __init__(self, path: Path, interval: float = 0.25):
        self.path, self.interval = path, interval
        self.start_free = shutil.disk_usage(path).free
        self.min_free = self.start_free
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.is_set():
            self.min_free = min(self.min_free, shutil.disk_usage(self.path).free)
            self._stop.wait(self.interval)

    def __enter__(self):
        self._t.start()
        return self

    def __exit__(self, *a):
        self._stop.set()
        self._t.join(timeout=2)

    @property
    def peak_used(self) -> int:
        return self.start_free - self.min_free


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", required=True, help="UTC day, YYYY-MM-DD")
    ap.add_argument("--dry-run", action="store_true",
                    help="run states 1-6 and report; never delete local")
    a = ap.parse_args()

    iv = _load("ingest_vision", REPO / "scripts" / "ingest_vision.py")
    uh = _load("upload_hf", REPO / "scripts" / "upload_hf.py")

    stage_dir = Path("/private/tmp/claude-501/-Users-azul/vision-stage")
    if stage_dir.exists():
        shutil.rmtree(stage_dir, ignore_errors=True)
    stage_dir.mkdir(parents=True, exist_ok=True)

    times: dict[str, float] = {}
    t0 = time.time()
    print(f"vision->hf  {a.date}  (daily granularity)")
    print(f"  free at start: {shutil.disk_usage(REPO).free / 1e9:.2f} GB\n")

    with DiskWatch(REPO) as watch:
        # ---- states 1-4, all inside ingest_day: fetch, checksum, normalize, sha256 ----
        t = time.time()
        row = iv.ingest_day(date=a.date, out_root=stage_dir, market="futures/um",
                            family="aggTrades", vendor_symbol="BTCUSDT",
                            venue="binancef", symbol="BTCUSDT", granularity="daily",
                            say=lambda *x, **k: None)
        times["1-4 fetch+verify+normalize+sha256"] = time.time() - t
        if row.get("status") != "ok":
            print(f"  STATE 1-4 FAILED: status={row.get('status')} — {row}")
            return 2
        base = stage_dir / "binancef" / "BTCUSDT" / "aggTrades"
        pq = base / f"date={a.date}" / "trades.parquet"
        mf = base / "manifests" / f"MANIFEST-{a.date}.json"
        # The MANIFEST is the durable artefact, not ingest_day's return shape — read it.
        man = json.loads(mf.read_text())
        norm, src = man["normalized"], man["source"]
        local_sha = sha256_file(pq)
        print(f"  1-4 ok  rows={norm['rows']:,}  parquet={pq.stat().st_size/1e6:.2f} MB")
        print(f"         zip {src['zip_bytes']/1e6:.1f} MB · sha256 verified vs venue: {src['checksum_verified']}")
        print(f"         parquet sha256: {local_sha[:16]}...")
        if local_sha != norm["sha256"]:
            print(f"  STATE 4 FAILED: my sha256 {local_sha[:16]} != manifest {norm['sha256'][:16]}")
            return 2

        # ---- state 5: upload ----
        t = time.time()
        dest = f"{VISION_PREFIX}/date={a.date}/trades.parquet"
        uh.hf_upload_file(HF_REPO, pq, dest, f"vision aggTrades {a.date}")
        uh.hf_upload_file(HF_REPO, mf, f"{VISION_PREFIX}/manifests/MANIFEST-{a.date}.json",
                          f"vision manifest {a.date}")
        times["5 upload"] = time.time() - t
        mb = pq.stat().st_size / 1e6
        print(f"  5   uploaded  {mb:.2f} MB in {times['5 upload']:.1f}s "
              f"= {mb / max(times['5 upload'], 1e-9):.2f} MB/s")

        # ---- state 6: READ BACK and recompute. This is what licenses the delete. ----
        t = time.time()
        back_dir = stage_dir / "readback"
        back_dir.mkdir(exist_ok=True)
        got = uh.hf_download_file(HF_REPO, dest, back_dir)
        remote_sha = sha256_file(Path(got))
        times["6 read-back"] = time.time() - t
        match = remote_sha == local_sha
        print(f"  6   read back {Path(got).stat().st_size/1e6:.2f} MB in {times['6 read-back']:.1f}s")
        print(f"      local  {local_sha}")
        print(f"      remote {remote_sha}")
        print(f"      ->  {'MATCH' if match else 'MISMATCH'}")

    # ---- state 7: delete local, only on a verified match ----
    live = LOCAL_ROOT / "binancef" / "BTCUSDT" / "aggTrades" / f"date={a.date}" / "trades.parquet"
    if not match:
        print("\n  STATE 6 FAILED -> state = remote_verify_failed. LOCAL RETAINED.")
        return 2
    if a.dry_run:
        print(f"\n  7   DRY RUN — local retained: {live}")
    elif live.exists():
        n = live.stat().st_size
        live.unlink()
        try:
            live.parent.rmdir()
        except OSError:
            pass
        print(f"\n  7   deleted local {live.relative_to(REPO)} ({n/1e6:.2f} MB)")
    else:
        print(f"\n  7   no local copy at {live} — nothing to delete")

    shutil.rmtree(stage_dir, ignore_errors=True)
    total = time.time() - t0
    print(f"\n  === CONTROL RESULT (this partition is a control, not a result) ===")
    for k, v in times.items():
        print(f"    {k:<38} {v:>7.1f}s")
    print(f"    {'TOTAL wall clock':<38} {total:>7.1f}s")
    print(f"    peak disk USED during run          {watch.peak_used/1e6:>7.1f} MB")
    print(f"    free at end                        {shutil.disk_usage(REPO).free/1e9:>7.2f} GB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
