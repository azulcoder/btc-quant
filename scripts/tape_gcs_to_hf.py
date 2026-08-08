"""tape_gcs_to_hf.py — third link of the tape chain: GCS -> Mac -> Hugging Face.

Why this exists
---------------
The depth tape is copied VM -> GCS by a write-only identity, which removes the
single-disk risk but leaves a single-BILLING risk: a Google Cloud trial that ends
takes the bucket with it, and the identity running that project is not a billing
admin (measured 2026-08-08 — it cannot even read the billing account). This script
moves the tape one hop further, onto storage this project already owns and pays
nothing for.

The VM's posture is UNCHANGED by this file. The VM never sees a Hugging Face token
and never talks to Hugging Face; it still holds exactly one grant
(``roles/storage.objectCreator`` on one bucket). The HF token lives only on the Mac,
which is where it already lives for the tick and vision pipelines. The chain is
therefore: VM --write-only--> GCS --pull--> Mac --existing token--> HF.

Verification, not exit codes
----------------------------
Every object crosses two checks before its ledger row is written:

* ``transport_ok`` — the md5 of the downloaded bytes equals the ``md5Hash`` GCS
  recorded when the VM created the object. GCS computed that hash on ITS side, so a
  match proves the bytes survived both hops (VM->GCS and GCS->Mac).
* ``roundtrip_ok`` — the object is downloaded BACK from Hugging Face after upload
  and compared byte-for-byte. "Upload returned without raising" is not evidence.

Disk safety is a first-class constraint here: this repo has hit ENOSPC twice, and
the second time cost a 295-day hole in the archive. Work proceeds ONE UTC DAY at a
time and the staging directory is deleted before the next day starts, so peak local
usage is one day of tape (~180 MB at depth@100ms, ~680 MB at depth@0ms) regardless
of how far behind the mirror is.

Bucket name is NEVER committed: pass ``--bucket`` or set ``BTCQ_TAPE_BUCKET``. The
runbook in this public repo uses placeholders only.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HF_REPO = "azulcoder/btc-quant-ticks"
HF_PREFIX = "depth_tape"          # deliberately NOT under vision/ or data/
LEDGER = REPO / "reports" / "tape-hf-ledger.jsonl"
STAGE = REPO / "data" / "hf-stage" / "tape"


def _upload_hf():
    spec = importlib.util.spec_from_file_location(
        "upload_hf", REPO / "scripts" / "upload_hf.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def gcs_token() -> str:
    """The Mac's OWN credentials, not the VM's. This side may read; the VM may not."""
    return subprocess.run(["gcloud", "auth", "print-access-token"],
                          capture_output=True, text=True, check=True).stdout.strip()


def gcs_list(bucket: str, prefix: str) -> list[dict]:
    """objects.list, paged. Returns name/size/md5Hash/generation per object."""
    out, token, page = [], gcs_token(), None
    while True:
        q = {"prefix": prefix, "maxResults": "1000"}
        if page:
            q["pageToken"] = page
        url = (f"https://storage.googleapis.com/storage/v1/b/{bucket}/o?"
               + urllib.parse.urlencode(q))
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=60) as r:
            body = json.loads(r.read())
        out.extend(body.get("items", []))
        page = body.get("nextPageToken")
        if not page:
            return out


def gcs_download(bucket: str, name: str, dest: Path) -> None:
    url = (f"https://storage.googleapis.com/storage/v1/b/{bucket}/o/"
           f"{urllib.parse.quote(name, safe='')}?alt=media")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {gcs_token()}"})
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(req, timeout=600) as r, open(dest, "wb") as fh:
        shutil.copyfileobj(r, fh, 1 << 20)


def md5_b64(p: Path) -> str:
    import base64
    h = hashlib.md5()
    with open(p, "rb") as fh:
        for blk in iter(lambda: fh.read(1 << 20), b""):
            h.update(blk)
    return base64.b64encode(h.digest()).decode()


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for blk in iter(lambda: fh.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def hf_readback(path_in_repo: str, timeout: int = 120) -> tuple[str, str, int]:
    """Read an uploaded file back over PLAIN HTTPS and hash it while streaming.

    Deliberately NOT `hf_hub_download`. Two reasons, one of them measured:

    * [DIUKUR 2026-08-08] the cached/xet download path hung this mirror for 25 minutes
      with a socket in CLOSE_WAIT and a 0-byte `.incomplete` file, no error, no timeout.
      A script whose whole job is beating a billing deadline must never be able to stall
      silently; every network call here carries a wall-clock bound.
    * A round-trip that reuses the upload library shares its bugs. This path uses a
      different client, no cache, and no token (the dataset is public), so agreement is
      evidence about the BYTES rather than about one library agreeing with itself.

    Returns (md5_b64, sha256_hex, bytes) computed on the stream — nothing is kept.
    """
    import base64
    url = (f"https://huggingface.co/datasets/{HF_REPO}/resolve/main/"
           f"{urllib.parse.quote(path_in_repo)}")
    last = None
    for attempt in range(4):
        m, s, n = hashlib.md5(), hashlib.sha256(), 0
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "btc-quant/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                declared = r.headers.get("content-length")
                declared = int(declared) if declared else None
                while True:
                    blk = r.read(1 << 20)
                    if not blk:
                        break
                    m.update(blk)
                    s.update(blk)
                    n += len(blk)
            # A dropped CDN stream returns b"" and looks EXACTLY like a clean EOF, so a
            # short read would otherwise pass as a successful download with a wrong hash.
            # [DIUKUR 2026-08-08] this is not hypothetical: the 18 MB parquet truncated
            # mid-stream on the first attempt while 2 KB and 8 KB files came back exact.
            if declared is not None and n != declared:
                raise IOError(f"short read: {n} of {declared} B")
            return base64.b64encode(m.digest()).decode(), s.hexdigest(), n
        except (urllib.error.URLError, IOError, OSError) as e:
            last = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"readback failed after 4 attempts for {path_in_repo}: {last}")


def log(row: dict) -> None:
    row["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with open(LEDGER, "a") as fh:
        fh.write(json.dumps(row, separators=(",", ":")) + "\n")


def done_objects() -> set[str]:
    if not LEDGER.exists():
        return set()
    out = set()
    for line in LEDGER.read_text().splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("kind") == "mirrored" and r.get("roundtrip_ok"):
            out.add(r["object"])
    return out


def mirror_tape(bucket: str, uh, limit_days: int | None) -> int:
    """One UTC day per iteration; staging is wiped between days (ENOSPC discipline)."""
    objs = [o for o in gcs_list(bucket, "tape/") if not o["name"].endswith("/")]
    objs += [o for o in gcs_list(bucket, "qc/") if not o["name"].endswith("/")]
    have = done_objects()
    todo = [o for o in objs if o["name"] not in have]
    if not todo:
        print("tape->HF: nothing new")
        return 0

    days: dict[str, list[dict]] = {}
    for o in todo:
        # tape/binancef/BTCUSDT/date=YYYY-MM-DD/chunk-*  |  qc/qc-YYYYMMDDT*.json
        seg = [s for s in o["name"].split("/") if s.startswith("date=")]
        days.setdefault(seg[0][5:] if seg else "qc", []).append(o)

    n_ok = 0
    for day in sorted(days)[: limit_days or len(days)]:
        batch = sorted(days[day], key=lambda o: o["name"])
        if STAGE.exists():
            shutil.rmtree(STAGE)
        STAGE.mkdir(parents=True)
        staged = []
        for o in batch:
            local = STAGE / Path(o["name"]).name
            gcs_download(bucket, o["name"], local)
            got = md5_b64(local)
            if got != o["md5Hash"]:
                log({"kind": "transport_fail", "object": o["name"],
                     "gcs_md5": o["md5Hash"], "local_md5": got})
                print(f"  TRANSPORT FAIL {o['name']}: {o['md5Hash']} != {got}")
                return 2
            staged.append((o, local, got))

        dest_dir = f"{HF_PREFIX}/binancef/BTCUSDT/date={day}" if day != "qc" \
            else f"{HF_PREFIX}/qc"
        uh.hf_upload_folder(HF_REPO, STAGE, dest_dir,
                            f"depth tape mirror: {day} ({len(staged)} object(s))")

        # roundtrip: the upload is not believed until it is read back
        for o, local, want in staged:
            got_md5, _sha, got_n = hf_readback(f"{dest_dir}/{local.name}")
            rt = got_md5 == want and got_n == int(o["size"])
            log({"kind": "mirrored", "object": o["name"], "day": day,
                 "hf_path": f"{dest_dir}/{local.name}", "bytes": int(o["size"]),
                 "gcs_md5": o["md5Hash"], "transport_ok": True, "roundtrip_ok": bool(rt),
                 "generation": o.get("generation")})
            if not rt:
                print(f"  ROUNDTRIP FAIL {o['name']}: md5 {want} vs {got_md5}, "
                      f"{o['size']} vs {got_n} B")
                return 2
            n_ok += 1
        print(f"  {day}: {len(staged)} object(s), "
              f"{sum(int(o['size']) for o, _, _ in staged):,} B -> {dest_dir}")
        shutil.rmtree(STAGE, ignore_errors=True)
    print(f"tape->HF: {n_ok} object(s) mirrored and read back")
    return 0


def mirror_archive(uh) -> int:
    """Item 1c: data/archive/ was the last single-copy store on this machine."""
    src = REPO / "data" / "archive"
    if not src.exists():
        print("archive: nothing to mirror")
        return 0
    have = done_objects()
    files = sorted(p for p in src.iterdir() if p.is_file())
    todo = [p for p in files if f"archive/{p.name}" not in have]
    if not todo:
        print("archive->HF: nothing new")
        return 0
    for p in todo:
        sha = sha256_file(p)
        uh.hf_upload_file(HF_REPO, p, f"archive/{p.name}",
                          f"archive mirror: {p.name}")
        _md5, got_sha, got_n = hf_readback(f"archive/{p.name}")
        ok = got_sha == sha and got_n == p.stat().st_size
        log({"kind": "mirrored", "object": f"archive/{p.name}",
             "hf_path": f"archive/{p.name}", "bytes": p.stat().st_size,
             "sha256": sha, "transport_ok": True, "roundtrip_ok": bool(ok)})
        print(f"  archive/{p.name}: {p.stat().st_size:,} B  roundtrip_ok={ok}")
        if not ok:
            return 2
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bucket", default=os.environ.get("BTCQ_TAPE_BUCKET"),
                    help="GCS tape bucket (or env BTCQ_TAPE_BUCKET); never committed")
    ap.add_argument("--days", type=int, default=None, help="cap days per run")
    ap.add_argument("--archive", action="store_true", help="also mirror data/archive/")
    a = ap.parse_args()
    uh = _upload_hf()
    rc = 0
    if a.archive:
        rc = mirror_archive(uh)
        if rc:
            return rc
    if not a.bucket:
        print("no --bucket / BTCQ_TAPE_BUCKET: tape mirror skipped")
        return rc
    return mirror_tape(a.bucket, uh, a.days)


if __name__ == "__main__":
    sys.exit(main())
