"""Tests for the GCS tape sync/heartbeat/QC path — all pure-logic, no network.

The upload boundary is faked at the module seam (`tape_sync.upload` etc.), and the fake
enforces the same contract the real one does: it returns the md5Hash of what it was
GIVEN, so a chunking bug shows up as a reassembly mismatch, not as a green run.
"""
import base64
import gzip
import hashlib
import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parent.parent / "deploy" / "gcp"
sys.path.insert(0, str(DEPLOY))


def load(name):
    spec = importlib.util.spec_from_file_location(name, DEPLOY / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


gcs_common = load("gcs_common")
tape_sync = load("tape_sync")
tape_heartbeat = load("tape_heartbeat")
tape_qc = load("tape_qc")


def members(*payloads: bytes) -> bytes:
    return b"".join(gzip.compress(p) for p in payloads)


def rows(*dicts) -> bytes:
    return b"".join(json.dumps(d, separators=(",", ":")).encode() + b"\n" for d in dicts)


# --------------------------------------------------------------------------- #
# walk_members                                                                 #
# --------------------------------------------------------------------------- #
def test_walk_members_yields_every_complete_member(tmp_path):
    p1, p2, p3 = rows({"a": 1}), rows({"b": 2}, {"b": 3}), rows({"c": 4})
    f = tmp_path / "t.gz"
    f.write_bytes(members(p1, p2, p3))
    got = list(gcs_common.walk_members(f, 0))
    assert [payload for _, payload in got] == [p1, p2, p3]
    assert got[-1][0] == f.stat().st_size          # last boundary is EOF

def test_walk_members_stops_at_truncated_tail_and_resumes_from_offset(tmp_path):
    p1, p2 = rows({"a": 1}), rows({"b": 2})
    whole = members(p1, p2)
    f = tmp_path / "t.gz"
    f.write_bytes(whole + members(rows({"c": 3}))[:-7])     # torn final member
    got = list(gcs_common.walk_members(f, 0))
    assert [payload for _, payload in got] == [p1, p2]      # torn tail not invented
    boundary = got[0][0]
    resumed = list(gcs_common.walk_members(f, boundary))    # restart mid-file
    assert [payload for _, payload in resumed] == [p2]

def test_walk_members_empty_and_garbage(tmp_path):
    f = tmp_path / "t.gz"
    f.write_bytes(b"")
    assert list(gcs_common.walk_members(f, 0)) == []
    f.write_bytes(b"this is not gzip at all")
    assert list(gcs_common.walk_members(f, 0)) == []


# --------------------------------------------------------------------------- #
# tape_sync                                                                    #
# --------------------------------------------------------------------------- #
class FakeGCS:
    def __init__(self):
        self.objects = {}
    def upload(self, bucket, name, body, content_type="application/octet-stream",
               timeout=300):
        assert name not in self.objects, f"overwrite attempted: {name}"
        self.objects[name] = bytes(body)
        return {"md5Hash": base64.b64encode(hashlib.md5(body).digest()).decode(),
                "generation": str(len(self.objects))}


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    tape = tmp_path / "data" / "depth_diffs" / "binancef" / "BTCUSDT"
    tape.mkdir(parents=True)
    fake = FakeGCS()
    for mod in (tape_sync, tape_heartbeat, tape_qc):
        monkeypatch.setattr(mod, "TAPE", tape)
        monkeypatch.setattr(mod, "bucket_name", lambda: "test-bucket")
        if hasattr(mod, "upload"):
            monkeypatch.setattr(mod, "upload", fake.upload)
    monkeypatch.setattr(tape_sync, "STATE", tmp_path / "data" / "depth_diffs" / ".s.json")
    monkeypatch.setattr(tape_sync, "LEDGER", tmp_path / "data" / "depth_diffs" / ".l.jsonl")
    monkeypatch.setattr(tape_sync, "probe_readback",
                        lambda b, n: {"readback_denied": True, "detail": "HTTP 403"})
    monkeypatch.setattr(tape_heartbeat, "HB_STATE",
                        tmp_path / "data" / "depth_diffs" / ".hb.json")
    monkeypatch.setattr(tape_heartbeat, "SYNC_STATE",
                        tmp_path / "data" / "depth_diffs" / ".s.json")
    return tape, fake


def test_sync_chunks_reassemble_byte_identically(sandbox):
    tape, fake = sandbox
    today = time.strftime("%Y-%m-%d", time.gmtime())
    f = tape / f"date={today}" / "frames.jsonl.gz"
    f.parent.mkdir(parents=True)
    f.write_bytes(members(rows({"kind": "frame"}), rows({"kind": "gap"})))
    assert tape_sync.main() == 0
    f.write_bytes(f.read_bytes() + members(rows({"kind": "frame"})))   # tape grows
    assert tape_sync.main() == 0
    chunks = sorted(n for n in fake.objects if "/chunk-" in n)
    assert len(chunks) == 2
    reassembled = b"".join(fake.objects[n] for n in chunks)
    assert reassembled == f.read_bytes()

def test_sync_closes_yesterday_with_manifest_and_trunc_tail(sandbox, monkeypatch):
    tape, fake = sandbox
    y = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 86400))
    f = tape / f"date={y}" / "frames.jsonl.gz"
    f.parent.mkdir(parents=True)
    torn = members(rows({"kind": "frame"}))[:-5]
    f.write_bytes(members(rows({"kind": "frame"}, {"kind": "snapshot"})) + torn)
    old = time.time() - 3600
    import os
    os.utime(f, (old, old))                                  # quiet long past midnight
    assert tape_sync.main() == 0
    mname = [n for n in fake.objects if n.endswith("manifest.json")]
    assert len(mname) == 1
    manifest = json.loads(fake.objects[mname[0]])
    assert manifest["size"] == f.stat().st_size
    assert manifest["md5_full_hex"] == hashlib.md5(f.read_bytes()).hexdigest()
    trunc = [n for n in fake.objects if n.endswith(".trunc")]
    assert len(trunc) == 1 and fake.objects[trunc[0]] == torn
    parts = sorted(n for n in fake.objects if "/chunk-" in n)
    assert b"".join(fake.objects[n] for n in parts) == f.read_bytes()

def test_sync_deletes_local_only_after_retention(sandbox):
    tape, fake = sandbox
    y = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 86400))
    f = tape / f"date={y}" / "frames.jsonl.gz"
    f.parent.mkdir(parents=True)
    f.write_bytes(members(rows({"kind": "frame"})))
    import os
    os.utime(f, (time.time() - 3600,) * 2)
    assert tape_sync.main() == 0
    assert f.exists()                                        # manifest fresh: kept
    st = json.loads(tape_sync.STATE.read_text())
    st["files"][y]["manifest"]["uploaded_ts"] = time.time() - 4 * 86400
    tape_sync.STATE.write_text(json.dumps(st))
    assert tape_sync.main() == 0
    assert not f.exists()                                    # aged out: deleted
    ledger = [json.loads(l) for l in tape_sync.LEDGER.read_text().splitlines()]
    assert ledger[-1]["kind"] == "deleted_local"

def test_sync_no_bucket_is_a_clean_noop(sandbox, monkeypatch):
    tape, fake = sandbox
    monkeypatch.setattr(tape_sync, "bucket_name", lambda: None)
    assert tape_sync.main() == 0
    assert fake.objects == {}


# --------------------------------------------------------------------------- #
# heartbeat + QC                                                               #
# --------------------------------------------------------------------------- #
def test_heartbeat_counts_kinds_incrementally(sandbox, monkeypatch):
    tape, fake = sandbox
    monkeypatch.setattr(tape_heartbeat.subprocess, "run",
                        lambda *a, **k: type("R", (), {"stdout": "active\n"})())
    today = time.strftime("%Y-%m-%d", time.gmtime())
    now = int(time.time() * 1000)
    f = tape / f"date={today}" / "frames.jsonl.gz"
    f.parent.mkdir(parents=True)
    f.write_bytes(members(rows({"kind": "frame", "recv_ms": now},
                               {"kind": "frame", "recv_ms": now},
                               {"kind": "gap", "recv_ms": now})))
    assert tape_heartbeat.main() == 0
    f.write_bytes(f.read_bytes() + members(rows({"kind": "frame", "recv_ms": now})))
    assert tape_heartbeat.main() == 0
    hbs = sorted(n for n in fake.objects if n.startswith("heartbeat/"))
    last = json.loads(fake.objects[hbs[-1]])
    assert last["frames_today"] == 3 and last["gaps_today"] == 1
    assert last["frames_since_last_hb"] == 1                 # incremental, not re-count

def test_qc_census_hours_resyncs_latency(sandbox):
    tape, _ = sandbox
    base = time.mktime(time.strptime("2026-08-07 03:00:00", "%Y-%m-%d %H:%M:%S"))
    ms = int(base * 1000)
    f = tape / "date=2026-08-07" / "frames.jsonl.gz"
    f.parent.mkdir(parents=True)
    f.write_bytes(members(rows(
        {"kind": "frame", "recv_ms": ms, "chain": "ok", "data": {"E": ms - 40}},
        {"kind": "frame", "recv_ms": ms + 1000, "chain": "gap", "data": {"E": ms + 900}},
        {"kind": "gap", "recv_ms": ms + 1001, "action": "resnapshot"},
        {"kind": "snapshot", "recv_ms": ms + 1500, "reason": "chain_break"},
        {"kind": "snapshot", "recv_ms": ms + 2000, "reason": "periodic"})))
    qc = tape_qc.census()
    day = qc["tape_days"]["2026-08-07"]
    assert day["frames"] == 2 and day["gap_rows"] == 1 and day["resyncs"] == 1
    hour = next(iter(day["hours"].values()))
    assert hour["chain"] == {"ok": 1, "gap": 1}
    assert hour["lat_ms_p50_p90_p99"][0] in (40, 100)        # both frames carry E


# --------------------------------------------------------------------------- #
# mid-file tear (the 2026-08-08 production incident)                           #
# --------------------------------------------------------------------------- #
def torn_tape(now_ms):
    """[good member][torn half-member][good][good] — what a hard reset mid-write
    followed by a restart's appends actually leaves on disk."""
    m1 = rows({"kind": "frame", "recv_ms": now_ms, "chain": "ok", "data": {"E": now_ms}})
    torn = members(rows({"kind": "frame", "recv_ms": now_ms}))[:-9]
    m3 = rows({"kind": "frame", "recv_ms": now_ms + 1000, "chain": "ok",
               "data": {"E": now_ms + 999}},
              {"kind": "frame", "recv_ms": now_ms + 2000, "chain": "ok",
               "data": {"E": now_ms + 1998}})
    m4 = rows({"kind": "snapshot", "recv_ms": now_ms + 3000, "reason": "connect"})
    return members(m1) + torn + members(m3) + members(m4), (m1, torn, m3, m4)


def test_walk_tape_recovers_members_after_midfile_tear(tmp_path):
    now = int(time.time() * 1000)
    blob, (m1, torn, m3, m4) = torn_tape(now)
    f = tmp_path / "t.gz"
    f.write_bytes(blob)
    got = list(gcs_common.walk_tape(f, 0))
    kinds = [k for k, _, _ in got]
    assert kinds == ["member", "hole", "member", "member"]
    assert got[1][2] == torn                      # the hole is byte-exact, not repaired
    assert got[2][2] == m3 and got[3][2] == m4    # everything AFTER the tear is seen
    assert got[-1][0] == "member" and got[-1][1] == len(blob)
    # the old walker's behavior — stopping at the tear — is the bug this guards against
    assert [p for _, p in gcs_common.walk_members(f, 0)] == [m1, m3, m4]


def test_sync_uploads_hole_and_reassembles_torn_file(sandbox):
    tape, fake = sandbox
    today = time.strftime("%Y-%m-%d", time.gmtime())
    blob, _ = torn_tape(int(time.time() * 1000))
    f = tape / f"date={today}" / "frames.jsonl.gz"
    f.parent.mkdir(parents=True)
    f.write_bytes(blob)
    assert tape_sync.main() == 0
    names = sorted(n for n in fake.objects if "/chunk-" in n)
    assert any(n.endswith(".hole") for n in names)
    assert b"".join(fake.objects[n] for n in names) == blob
    st = json.loads(tape_sync.STATE.read_text())
    assert st["files"][today]["offset"] == len(blob)   # reader no longer stalls at the tear


def test_heartbeat_sees_frames_after_tear(sandbox, monkeypatch):
    tape, fake = sandbox
    monkeypatch.setattr(tape_heartbeat.subprocess, "run",
                        lambda *a, **k: type("R", (), {"stdout": "active\n"})())
    today = time.strftime("%Y-%m-%d", time.gmtime())
    blob, _ = torn_tape(int(time.time() * 1000))
    f = tape / f"date={today}" / "frames.jsonl.gz"
    f.parent.mkdir(parents=True)
    f.write_bytes(blob)
    assert tape_heartbeat.main() == 0
    hbs = sorted(n for n in fake.objects if n.startswith("heartbeat/"))
    hb = json.loads(fake.objects[hbs[-1]])
    assert hb["frames_today"] == 3                # 1 before tear + 2 after: all counted
    assert hb["tape_holes_today"] == 1 and hb["tape_hole_bytes_today"] > 0


def test_qc_fps_uses_measured_coverage_not_calendar_hour(sandbox):
    tape, _ = sandbox
    import calendar
    base = calendar.timegm(time.strptime("2026-08-07 05:00:00", "%Y-%m-%d %H:%M:%S"))
    ms = int(base * 1000)
    f = tape / "date=2026-08-07" / "frames.jsonl.gz"
    f.parent.mkdir(parents=True)
    # 6 minutes of tape inside the hour: the old /3600 denominator would report
    # a 10x-too-low fps for exactly this shape (the production false alarm)
    frames = [{"kind": "frame", "recv_ms": ms + i * 10_000, "chain": "ok",
               "data": {"E": ms + i * 10_000 - 5}} for i in range(36)]
    f.write_bytes(members(rows(*frames)))
    qc = tape_qc.census()
    hour = qc["tape_days"]["2026-08-07"]["hours"]["05"]
    assert hour["coverage_s"] == 350              # (36-1) * 10 s span
    assert abs(hour["fps"] - 36 / 350) < 0.01
