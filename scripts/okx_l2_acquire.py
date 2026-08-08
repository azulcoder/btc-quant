"""okx_l2_acquire.py — acquire the OKX L2 400-level daily archive per the declared rule.

The sampling frame is NOT decided here. It is declared in `docs/SAMPLE-okx-l2-001.md`,
committed before this script downloaded a single byte, and this file only executes it:
the 7th and the 21st of every month from 2024-01 through 2026-07, tranche A (the 7ths)
before tranche B (the 21sts), chronological, missing days counted and never substituted.

Integrity, per file, in order — any failure stops that day and is written to the ledger:

1. HEAD `content-length` equals the bytes actually downloaded.
2. `sha256` computed locally.
3. The archive OPENS: the tar is readable and its first data member decompresses.
4. Uploaded to HF under `okx_l2/` (its own prefix, beside `vision/`, `data/`,
   `depth_tape/`).
5. Verified against the sha256 **Hugging Face itself computed on receipt** (`lfs.sha256`),
   which is the same shape of evidence the GCS link uses (`md5Hash`): a hash produced by
   the receiving side, not by the sender repeating itself. A full byte-for-byte readback
   is used for the SAMPLE day only — measured 2026-08-08, HF's CDN truncates large
   streams (26 MB came back as 15.3 MB), so a 450 MB readback per day would spend hours
   proving what the receipt hash already proves.
6. Local copy deleted immediately. Peak local usage stays ONE file (~0.5 GB): this repo
   has hit ENOSPC twice and the Mac has 17 GiB free.

Nothing predictive is computed here, by design — see the rule document, section 6.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HF_REPO = "azulcoder/btc-quant-ticks"
HF_PREFIX = "okx_l2/BTC-USDT-SWAP"
LEDGER = REPO / "reports" / "okx-l2-ledger.jsonl"
STAGE = REPO / "data" / "okx-stage"
INST = "BTC-USDT-SWAP"
BASES = (
    "https://static.okx.com/cdn/okx/match/orderbook/pro/L2/400lv/daily",
    "https://static.okx.com/cdn/okx/match/orderbook/L2/400lv/daily",
)
UA = {"User-Agent": "btc-quant/1.0 (research; keyless)"}


def frame_days(tranche: str) -> list[str]:
    """The declared frame. Pure calendar arithmetic — no data is consulted."""
    days = []
    for year in (2024, 2025, 2026):
        for month in range(1, 13):
            if year == 2026 and month > 7:
                continue
            for dom in ((7,) if tranche == "A" else (21,) if tranche == "B" else (7, 21)):
                days.append(f"{year:04d}-{month:02d}-{dom:02d}")
    return sorted(days)


def resolve(date: str) -> tuple[str, int] | None:
    """Find which published path serves this day, and its declared size."""
    name = f"{INST}-L2orderbook-400lv-{date}.tar.gz"
    for base in BASES:
        url = f"{base}/{date.replace('-', '')}/{name}"
        try:
            req = urllib.request.Request(url, method="HEAD", headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                if r.status == 200:
                    return url, int(r.headers["content-length"])
        except (urllib.error.HTTPError, urllib.error.URLError, OSError, KeyError):
            continue
    return None


def download(url: str, dest: Path, want: int, tries: int = 4) -> str:
    last = None
    for attempt in range(tries):
        try:
            h = hashlib.sha256()
            n = 0
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=600) as r, open(dest, "wb") as fh:
                while True:
                    blk = r.read(1 << 20)
                    if not blk:
                        break
                    fh.write(blk)
                    h.update(blk)
                    n += len(blk)
            if n != want:
                raise IOError(f"short read {n} of {want} B")
            return h.hexdigest()
        except Exception as e:                      # noqa: BLE001
            last = e
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"download failed after {tries}: {last}")


def opens(p: Path) -> tuple[bool, str]:
    """Control 3: the bytes are not merely present, they are a readable archive."""
    try:
        with tarfile.open(p, "r:gz") as tf:
            m = tf.next()
            while m is not None and not m.isfile():
                m = tf.next()
            if m is None:
                return False, "no file member"
            fh = tf.extractfile(m)
            head = fh.read(4096) if fh else b""
            return bool(head), f"{m.name} ({m.size:,} B)"
    except Exception as e:                          # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def log(row: dict) -> None:
    row["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with open(LEDGER, "a") as fh:
        fh.write(json.dumps(row, separators=(",", ":")) + "\n")


def done() -> set[str]:
    if not LEDGER.exists():
        return set()
    out = set()
    for line in LEDGER.read_text().splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("state") in ("stored", "absent"):
            out.add(r["date"])
    return out


def hf_receipt_sha(path_in_repo: str) -> str | None:
    from huggingface_hub import HfApi
    for f in HfApi().list_repo_tree(HF_REPO, path_in_repo=path_in_repo.rsplit("/", 1)[0],
                                    repo_type="dataset", expand=True):
        if f.path == path_in_repo:
            lfs = getattr(f, "lfs", None)
            return getattr(lfs, "sha256", None) if lfs else None
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tranche", choices=("A", "B", "AB"), default="A")
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()

    spec = importlib.util.spec_from_file_location("upload_hf",
                                                  REPO / "scripts" / "upload_hf.py")
    uh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(uh)

    have = done()
    days = [d for d in frame_days(a.tranche) if d not in have]
    if a.limit:
        days = days[: a.limit]
    print(f"tranche {a.tranche}: {len(days)} day(s) to acquire "
          f"({len(have)} already in ledger)")
    STAGE.mkdir(parents=True, exist_ok=True)

    stored = absent = 0
    for date in days:
        got = resolve(date)
        if got is None:
            log({"date": date, "state": "absent",
                 "note": "no published file at either path — counted, NOT substituted"})
            absent += 1
            print(f"  {date}  ABSENT (counted, not substituted)")
            continue
        url, size = got
        local = STAGE / f"{INST}-L2orderbook-400lv-{date}.tar.gz"
        t0 = time.time()
        try:
            sha = download(url, local, size)
        except RuntimeError as e:
            log({"date": date, "state": "download_failed", "url": url, "error": str(e)})
            print(f"  {date}  DOWNLOAD FAILED: {e}")
            local.unlink(missing_ok=True)
            return 2
        ok, detail = opens(local)
        if not ok:
            log({"date": date, "state": "corrupt", "sha256": sha, "bytes": size,
                 "detail": detail})
            print(f"  {date}  ARCHIVE DOES NOT OPEN: {detail}")
            local.unlink(missing_ok=True)
            return 2
        dest = f"{HF_PREFIX}/date={date}/{local.name}"
        uh.hf_upload_file(HF_REPO, local, dest, f"okx l2 sample: {date}")
        receipt = hf_receipt_sha(dest)
        verified = receipt == sha
        log({"date": date, "state": "stored" if verified else "receipt_mismatch",
             "url": url, "bytes": size, "sha256": sha, "hf_path": dest,
             "hf_receipt_sha256": receipt, "receipt_ok": verified,
             "opens": True, "first_member": detail,
             "seconds": round(time.time() - t0, 1)})
        local.unlink(missing_ok=True)              # ENOSPC discipline: one file at a time
        if not verified:
            print(f"  {date}  RECEIPT MISMATCH: local {sha} vs HF {receipt}")
            return 2
        stored += 1
        print(f"  {date}  {size:,} B  opens={detail}  receipt_ok  "
              f"{time.time() - t0:.0f}s")
    print(f"tranche {a.tranche}: {stored} stored, {absent} absent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
