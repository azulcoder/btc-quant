"""gcs_common.py — shared plumbing for the tape sync/heartbeat/QC scripts on the VM.

Stdlib only, on purpose: these run on a minimal Debian VM whose only pip install is
`websockets` for the recorder, and adding a cloud SDK dependency would widen the surface
the honest way to say it is — for no verification we cannot do with urllib and hashlib.

Auth is the METADATA SERVER, never a key file: the VM's attached service account holds
exactly one grant (roles/storage.objectCreator on the tape bucket) and the instance scope
is devstorage.write_only. Nothing here can read, list, overwrite, or delete an object —
uploads verify through the md5Hash the CREATE RESPONSE itself returns, which is the only
read-shaped fact a write-only principal gets back.
"""
from __future__ import annotations

import base64
import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request

META = "http://metadata.google.internal/computeMetadata/v1"
MH = {"Metadata-Flavor": "Google"}


def metadata(path: str, default: str | None = None) -> str | None:
    try:
        with urllib.request.urlopen(
                urllib.request.Request(f"{META}/{path}", headers=MH), timeout=5) as r:
            return r.read().decode()
    except (urllib.error.URLError, OSError):
        return default


def bucket_name() -> str | None:
    """The bucket is configured as instance metadata `tape-bucket`, NOT in this public
    repo — same placeholder discipline as the rest of the runbook. No metadata key means
    the VM is running in the old keyless posture and every caller must no-op cleanly."""
    return metadata("instance/attributes/tape-bucket")


def token() -> str:
    raw = metadata("instance/service-accounts/default/token")
    if raw is None:
        raise RuntimeError("no service account on this VM (keyless posture)")
    return json.loads(raw)["access_token"]


def upload(bucket: str, name: str, body: bytes, content_type: str = "application/octet-stream",
           timeout: int = 300) -> dict:
    """objects.insert (media). Returns the API's object resource. Raises RuntimeError on
    an md5 mismatch between what we sent and what GCS says it stored — exit 0 is not
    verification, the stored hash is."""
    url = (f"https://storage.googleapis.com/upload/storage/v1/b/{bucket}/o"
           f"?uploadType=media&name={urllib.parse.quote(name, safe='')}")
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": f"Bearer {token()}", "Content-Type": content_type})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        meta = json.loads(r.read())
    want = base64.b64encode(hashlib.md5(body).digest()).decode()
    got = meta.get("md5Hash")
    if got != want:
        raise RuntimeError(f"md5 mismatch on {name}: local {want} stored {got}")
    return meta


def probe_readback(bucket: str, name: str) -> dict:
    """Negative control, run live on every sync: a write-only principal asking to READ
    its own upload must be refused. 200 here means the posture drifted and the heartbeat
    will say so out loud."""
    url = (f"https://storage.googleapis.com/storage/v1/b/{bucket}/o/"
           f"{urllib.parse.quote(name, safe='')}")
    try:
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token()}"})
        with urllib.request.urlopen(req, timeout=15):
            return {"readback_denied": False, "detail": "HTTP 200 — POSTURE DRIFT"}
    except urllib.error.HTTPError as e:
        return {"readback_denied": e.code in (401, 403), "detail": f"HTTP {e.code}"}
    except (urllib.error.URLError, OSError) as e:
        return {"readback_denied": None, "detail": f"probe error: {type(e).__name__}"}


GZIP_MAGIC = b"\x1f\x8b\x08"


def walk_tape(path, start: int = 0):
    """Stream the append-only multi-member gzip from byte `start`, yielding
    ("member", end_offset, payload) per complete member and ("hole", end_offset,
    raw_bytes) per unreadable span. Stops at EOF or at a truncated TAIL.

    Holes are real on this tape: a hard reset (power cycle, not SIGTERM) can cut a
    member mid-write, and the restarted recorder then APPENDS healthy members after
    the torn one. The first version of this walker assumed tears only occur at the
    tail and stopped at the first zlib error — which blinded sync, heartbeat, and QC
    to 96.6% of a real day file (measured 2026-08-08: all three readers stalled at
    byte 912,208 of a 26.4 MB tape). Recovery = scan for the next gzip magic and
    hand the skipped bytes back as an explicit hole — recorded, never repaired, so
    the GCS copy stays byte-identical and the loss is visible instead of silent.
    A magic match inside torn garbage is possible (3-byte pattern); a false match
    fails to decompress and simply extends the hole to the next candidate.
    """
    import zlib
    size = path.stat().st_size
    with open(path, "rb") as fh:
        mstart = start
        carry = b""
        fh.seek(start)
        while True:
            d = zlib.decompressobj(wbits=31)
            out = []
            fed = 0
            feed = carry or fh.read(1 << 20)
            carry = b""
            status = "eof"                       # ran out of file mid/at member start
            while feed:
                try:
                    out.append(d.decompress(feed))
                except zlib.error:
                    status = "torn"
                    break
                fed += len(feed)
                if d.eof:
                    end = mstart + fed - len(d.unused_data)
                    yield "member", end, b"".join(out)
                    carry = bytes(d.unused_data)
                    mstart = end
                    status = "ok"
                    break
                feed = fh.read(1 << 20)
            if status == "ok":
                if not carry and fh.tell() >= size:
                    return
                continue
            if status == "eof":
                return                           # truncated tail: stop, do not invent
            # torn member at mstart: find the next magic strictly after it
            fh.seek(mstart + 1)
            scan_pos = mstart + 1
            tail2 = b""
            found = -1
            while found < 0:
                blk = fh.read(1 << 20)
                if not blk:
                    return                       # tear runs to EOF: it IS the tail
                probe = tail2 + blk
                i = probe.find(GZIP_MAGIC)
                if i >= 0:
                    found = scan_pos - len(tail2) + i
                else:
                    scan_pos += len(blk)
                    tail2 = probe[-2:]
            fh.seek(mstart)
            raw = fh.read(found - mstart)
            yield "hole", found, raw
            mstart = found
            fh.seek(found)


def walk_members(path, start: int = 0):
    """Members only — the original interface. Holes are skipped HERE; callers that
    must count or preserve them (sync, heartbeat, QC) use walk_tape directly."""
    for kind, end, payload in walk_tape(path, start):
        if kind == "member":
            yield end, payload
