"""test_depth_recorder.py — the chain classifier is pure, so every branch is testable dry.

The recorder's one load-bearing decision is `classify_frame`: it encodes the FUTURES book-sync
rule, which differs from spot (futures: first event STRADDLES lastUpdateId, then pu must equal
the previous u; spot uses U <= lastUpdateId+1 <= u and has no pu). Getting this wrong is
silent — frames still flow, files still grow — so the tests pin each branch, and a negative
control asserts the spot rule would MISCLASSIFY here, which is the mistake most likely to be
introduced by someone "fixing" it from memory of the spot docs.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("rdd", REPO / "scripts" / "record_depth_diffs.py")
rdd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rdd)
classify = rdd.classify_frame


def test_no_snapshot_yet_is_a_gap():
    assert classify(10, 20, 9, None, None) == "gap"


def test_events_before_the_snapshot_are_dropped_from_the_chain_not_the_tape():
    # u < lastUpdateId: predates the snapshot. The recorder still WRITES the frame —
    # recording is not book-keeping — but the chain state must not advance on it.
    assert classify(90, 99, 89, 100, None) == "drop_pre_snapshot"


def test_first_event_must_straddle_the_snapshot():
    assert classify(95, 105, 94, 100, None) == "first_ok"     # U <= L <= u
    assert classify(100, 100, 99, 100, None) == "first_ok"    # boundary: U == L == u
    # U already past L: the snapshot went stale while connecting -> resync, not accept
    assert classify(101, 110, 100, 100, None) == "gap"


def test_continuation_requires_pu_equal_to_previous_u():
    assert classify(106, 112, 105, 100, 105) == "ok"
    assert classify(120, 130, 119, 100, 105) == "gap"         # one event missed


def test_the_spot_rule_would_misclassify_the_futures_boundary():
    """Negative control. Spot's first-event rule is U <= L+1 <= u. An event with
    U == L+1 == u satisfies SPOT but must NOT satisfy the futures straddle (U <= L <= u):
    here U = L+1 > L. If someone rewrites the classifier from the spot docs, this fails."""
    L = 100
    assert classify(L + 1, L + 1, L, L, None) == "gap"


def test_day_path_rotates_on_utc_midnight():
    before = rdd.day_path(1785455999_000)     # 2026-07-30T23:59:59Z
    after = rdd.day_path(1785456000_000)      # 2026-07-31T00:00:00Z
    assert before != after
    assert "date=2026-07-30" in str(before) and "date=2026-07-31" in str(after)


def test_sink_appends_valid_multi_member_gzip(tmp_path, monkeypatch):
    """A kill between flushes must never corrupt the file: each flush is one gzip member,
    and concatenated members are a valid gzip stream by the format's own definition."""
    import gzip as _g
    monkeypatch.setattr(rdd, "OUT_ROOT", tmp_path)
    s = rdd.Sink()
    for i in range(3):
        s.add({"kind": "frame", "recv_ms": 1785455999_000, "i": i})
    s.flush()
    for i in range(2):
        s.add({"kind": "frame", "recv_ms": 1785455999_500, "i": 10 + i})
    s.flush()                                  # second gzip member, same file
    p = tmp_path / "date=2026-07-30" / "frames.jsonl.gz"
    lines = _g.decompress(p.read_bytes()).decode().strip().split("\n")
    assert len(lines) == 5
    import json as _j
    assert [_j.loads(x)["i"] for x in lines] == [0, 1, 2, 10, 11]
