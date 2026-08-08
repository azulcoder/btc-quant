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


def walk_members(path, start: int = 0):
    """Stream the append-only multi-member gzip from byte `start`, yielding
    (member_end_offset, decompressed_bytes) per COMPLETE member. Stops cleanly at a
    truncated tail (the recorder may be mid-flush); the caller sees only what is whole.
    Memory stays bounded: 1 MiB compressed blocks in, one member's payload out."""
    import zlib
    with open(path, "rb") as fh:
        fh.seek(start)
        pos = start
        pending = b""
        while True:
            d = zlib.decompressobj(wbits=31)
            out = []
            feed = pending
            pending = b""
            while True:
                if not feed:
                    feed = fh.read(1 << 20)
                    if not feed:
                        return                      # clean EOF or truncated member: stop
                try:
                    out.append(d.decompress(feed))
                except zlib.error:
                    return                          # corrupt/truncated tail: stop, do not invent
                pos += len(feed) - len(d.unused_data)
                if d.eof:
                    pending = d.unused_data
                    yield pos, b"".join(out)
                    break
                feed = b""
