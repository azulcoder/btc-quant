"""test_vision.py — M7 public-archive ingest (scripts/ingest_vision.py, DESIGN §3d).

Fully deterministic and **network-free**: every HTTP call goes through an
injected fake opener serving zips built from ``tests/fixtures_vision.json``,
whose rows are REAL lines captured from ``data.binance.vision`` on 2026-08-02.
The one test that does touch the network lives in ``test_vision_overlap.py`` and
skips cleanly.

What is asserted here is the rails, not the happy path:

* the header sniff (the spec said "differed by year"; it differs per FILE);
* the spot layout is REFUSED (8 columns, ``True``/``False``, microseconds);
* the vision normalizer and ``collector.normalize_binance_aggtrades`` produce the
  **identical 7-tuple** — which is what makes "same stream, same aggTradeId
  space" mechanical rather than prose;
* an absent day stays ABSENT (no parquet, no zero-row file, no interpolation);
* ID holes are REPORTED and never filled; duplicates ABORT;
* the output tree can never be placed inside the recorded tick store;
* the honest-limit sentences are verbatim.

Collector deps are opt-in (requirements-collector.txt): skips cleanly without duckdb.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import urllib.error
import zipfile
from pathlib import Path

import pytest

duckdb = pytest.importorskip("duckdb")  # opt-in dep — requirements-collector.txt

from btcquant import collector  # noqa: E402

_REPO = Path(__file__).resolve().parent.parent
FIXTURES = json.loads((Path(__file__).parent / "fixtures_vision.json").read_text())


def _load_module():
    """Load ``scripts/ingest_vision.py`` by path (scripts/ is not a package)."""
    spec = importlib.util.spec_from_file_location(
        "ingest_vision", _REPO / "scripts" / "ingest_vision.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


iv = _load_module()


# --------------------------------------------------------------------------- #
# A fake archive: zips built in memory, served by an injected opener.          #
# --------------------------------------------------------------------------- #
def _zip_bytes(member: str, lines) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(member, "\n".join(lines) + "\n")
    return buf.getvalue()


class _FakeResponse:
    """Supports both ``read()`` and the chunked ``read(n)`` the streaming
    downloader uses — the real ingest path streams to disk so a ~1 GB monthly
    object is never buffered whole, and the fake has to behave the same way or
    the tests would exercise a path production does not take."""

    def __init__(self, body: bytes, headers=None):
        self._buf = io.BytesIO(body)
        self.headers = headers or {"ETag": '"fake"', "Last-Modified": "now"}

    def read(self, n: int = -1):
        return self._buf.read(n if n is not None else -1)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _opener(objects: dict):
    """``{url: bytes}`` -> an opener; anything not in the map answers 404."""
    def _open(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if url in objects:
            return _FakeResponse(objects[url])
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
    return _open


def _archive(date: str, lines, *, market="futures/um", family="aggTrades",
             symbol="BTCUSDT", corrupt_checksum=False, checksum_name=None):
    """Build the two objects (zip + CHECKSUM) the ingester fetches for one day."""
    name = f"{symbol}-{family}-{date}.zip"
    url = iv.archive_url(market, "daily", family, symbol, date)
    blob = _zip_bytes(f"{symbol}-{family}-{date}.csv", lines)
    digest = hashlib.sha256(blob).hexdigest()
    if corrupt_checksum:
        digest = "0" * 64
    return {url: blob, url + ".CHECKSUM": f"{digest}  {checksum_name or name}\n".encode()}


def _rows(n=5, *, first_id=1000, ts0=None, date="2026-08-01", step_ms=1000,
          skip=()):
    """Synthetic data lines in the real 7-column layout."""
    a, _ = iv._day_bounds(date)
    ts0 = a if ts0 is None else ts0
    out = []
    for i in range(n):
        tid = first_id + i
        if tid in skip:
            continue
        out.append(f"{tid},{60000 + i}.5,0.{i + 1},{tid * 2},{tid * 2 + 1},"
                   f"{ts0 + i * step_ms},{'true' if i % 2 else 'false'}")
    return out


def _ingest(tmp_path, date, lines, **kw):
    objs = _archive(date, lines, **{k: v for k, v in kw.items()
                                    if k in ("corrupt_checksum", "checksum_name")})
    return iv.ingest_day(
        date=date, out_root=tmp_path, market="futures/um", family="aggTrades",
        vendor_symbol="BTCUSDT", venue="binancef", symbol="BTCUSDT",
        opener=_opener(objs), say=lambda *a, **k: None)


# --------------------------------------------------------------------------- #
# 1-3. Layout: header sniff, and the spot refusal                              #
# --------------------------------------------------------------------------- #
def test_header_sniff_accepts_both_layouts(tmp_path):
    """A day WITH and a day WITHOUT a header must yield the identical rows.

    The two fixtures are consecutive REAL days (2021-01-01 has a header,
    2021-01-02 does not), which is the measurement that killed the spec's
    "differed by year" rule.
    """
    with_hdr = FIXTURES["futures_um_2021_01_01_with_header"]["lines"]
    assert iv.sniff_header(with_hdr[0]) is True
    assert iv.sniff_header(with_hdr[1]) is False

    no_hdr = FIXTURES["futures_um_2021_01_02_no_header"]["lines"]
    assert iv.sniff_header(no_hdr[0]) is False

    # Same payload, one served with a header and one without -> same parquet rows.
    data = _rows(4)
    r_no = _ingest(tmp_path / "a", "2026-08-01", data)
    r_yes = _ingest(tmp_path / "b", "2026-08-01", [",".join(iv.AGG_COLUMNS)] + data)
    assert r_no["status"] == r_yes["status"] == "ok"
    assert r_no["rows"] == r_yes["rows"] == 4
    assert r_no["header_row_present"] is False and r_yes["header_row_present"] is True

    con = duckdb.connect()
    proj = ", ".join(c for c, _ in iv.TRADES_COLUMNS)
    for root in (tmp_path / "a", tmp_path / "b"):
        p = root / "binancef/BTCUSDT/aggTrades/date=2026-08-01/trades.parquet"
        con.execute(f"CREATE OR REPLACE VIEW v_{root.name} AS "
                    f"SELECT {proj} FROM read_parquet('{p}')")
    assert con.execute("SELECT count(*) FROM (SELECT * FROM v_a EXCEPT SELECT * FROM v_b)"
                       ).fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM (SELECT * FROM v_b EXCEPT SELECT * FROM v_a)"
                       ).fetchone()[0] == 0
    con.close()


def test_header_row_is_never_ingested_as_a_trade(tmp_path):
    """The header must not become a row — ``min(trade_id)`` is the first DATA id."""
    r = _ingest(tmp_path, "2026-08-01", [",".join(iv.AGG_COLUMNS)] + _rows(3, first_id=777))
    assert r["status"] == "ok" and r["rows"] == 3
    man = json.loads((tmp_path / "binancef/BTCUSDT/aggTrades/manifests/"
                      "MANIFEST-2026-08-01.json").read_text())
    assert man["normalized"]["id_min"] == 777
    assert man["normalized"]["id_max"] == 779


def test_rejects_spot_layout(tmp_path):
    """8 columns + True/False + microsecond ts must ABORT before anything is written.

    The refusal names the column count, because that is the fact: spot BTCUSDT is
    a different instrument in a different aggTradeId space, so the exact-dedup
    argument that licenses this whole item does not hold for it.
    """
    spot = FIXTURES["spot_2026_08_01_REFUSED"]["lines"]
    with pytest.raises(iv.VisionError) as exc:
        iv.check_columns(spot[0], header_present=False)
    assert "8" in str(exc.value) and "SPOT" in str(exc.value)

    r = _ingest(tmp_path, "2026-08-01", spot)
    assert r["status"] == "failed"
    assert not (tmp_path / "binancef/BTCUSDT/aggTrades/date=2026-08-01"
                / "trades.parquet").exists()
    assert (tmp_path / "binancef/BTCUSDT/aggTrades/manifests/"
            "FAILED-2026-08-01.json").exists()


def test_scope_allowlist_refuses_everything_but_futures_um_aggtrades():
    """Rail 2 lives in code, and every refusal states the measured reason."""
    iv.check_scope("futures/um", "aggTrades")  # the one allowed pair
    for market, family, needle in (
        ("spot", "aggTrades", "DIFFERENT INSTRUMENT"),
        ("futures/um", "metrics", "+300,000 ms"),
        ("futures/um", "liquidationSnapshot", "does NOT EXIST"),
        ("futures/um", "bookDepth", "NOT a book"),
        ("futures/um", "bookTicker", "320 days"),
        ("futures/um", "trades", "RAW tradeId space"),
    ):
        with pytest.raises(iv.VisionError) as exc:
            iv.check_scope(market, family)
        assert needle in str(exc.value), (market, family)


# --------------------------------------------------------------------------- #
# 4. The claim that licenses the whole item, made mechanical                    #
# --------------------------------------------------------------------------- #
def test_aggressor_convention_matches_collector_normalizer():
    """The vision normalizer == ``collector.normalize_binance_aggtrades``, exactly.

    Same venue, same stream, same aggTradeId space — asserted as a 7-tuple
    equality on real archive rows rather than argued in a docstring. If either
    side ever changes its aggressor convention (§0.6: ``m`` true means the buyer
    was the maker, so the aggressor SOLD), this fails.
    """
    lines = FIXTURES["futures_um_2026_08_01_with_header"]["lines"][1:]
    for line in lines:
        f = line.split(",")
        mine = iv.normalize_agg_row(f, "binancef", "BTCUSDT")
        theirs, next_id = collector.normalize_binance_aggtrades(
            [{"a": int(f[0]), "p": f[1], "q": f[2], "T": int(f[5]),
              "m": f[6].strip().lower() == "true"}], "BTCUSDT")
        assert mine == theirs[0], line
        assert next_id == int(f[0]) + 1
    # And at least one row of each side, so the test cannot pass on a constant.
    sides = {iv.normalize_agg_row(l.split(","), "binancef", "BTCUSDT")[6] for l in lines}
    assert sides == {True, False}


def test_trades_schema_mirrors_the_collector_table():
    """The restated schema must still match ``collector._TABLE_COLUMNS['trades']``."""
    assert tuple(c for c, _ in iv.TRADES_COLUMNS) == collector._TABLE_COLUMNS["trades"]


def test_duckdb_path_and_pure_normalizer_agree(tmp_path):
    """Two routes, same rows: the pure function is the DEFINITION, SQL is the speed."""
    lines = _rows(25, first_id=5_000)
    r = _ingest(tmp_path, "2026-08-01", lines)
    assert r["status"] == "ok"
    p = tmp_path / "binancef/BTCUSDT/aggTrades/date=2026-08-01/trades.parquet"
    con = duckdb.connect()
    proj = ", ".join(c for c, _ in iv.TRADES_COLUMNS)
    got = con.execute(f"SELECT {proj} FROM read_parquet('{p}') ORDER BY ts_ms").fetchall()
    con.close()
    want = sorted((iv.normalize_agg_row(l.split(","), "binancef", "BTCUSDT")
                   for l in lines), key=lambda r: r[3])
    assert got == want


# --------------------------------------------------------------------------- #
# 5. Unit guard — magnitude, never a date cutoff                                #
# --------------------------------------------------------------------------- #
def test_ts_unit_guard_is_magnitude_not_a_date_cutoff(tmp_path):
    """16-digit (microsecond) timestamps abort; 13-digit ones pass."""
    ok = "1,60000.0,0.1,2,3,1785542400081,true"
    assert iv.normalize_agg_row(ok.split(","), "binancef", "BTCUSDT")[3] == 1785542400081
    us = "1,60000.0,0.1,2,3,1785542400207874,true"
    with pytest.raises(iv.VisionError) as exc:
        iv.normalize_agg_row(us.split(","), "binancef", "BTCUSDT")
    assert "MILLISECONDS" in str(exc.value)
    # And end to end: nothing is written.
    r = _ingest(tmp_path, "2026-08-01", [us])
    assert r["status"] == "failed"
    assert not (tmp_path / "binancef/BTCUSDT/aggTrades/date=2026-08-01"
                / "trades.parquet").exists()


# --------------------------------------------------------------------------- #
# 6-7. Gaps stay gaps; a partition IS its day                                   #
# --------------------------------------------------------------------------- #
def test_id_continuity_reports_holes_and_never_fills(tmp_path):
    """Missing ids 1002..1004 are COUNTED and RANGED — and never invented."""
    lines = _rows(10, first_id=1000, skip=(1002, 1003, 1004))
    r = _ingest(tmp_path, "2026-08-01", lines)
    assert r["status"] == "ok"
    assert r["rows"] == 7 and r["id_holes"] == 3
    man = json.loads((tmp_path / "binancef/BTCUSDT/aggTrades/manifests/"
                      "MANIFEST-2026-08-01.json").read_text())["normalized"]
    assert man["id_holes"] == 3
    assert man["id_hole_ranges"] == [[1002, 1004]]
    assert man["rows"] == 7 and man["id_span"] == 10
    p = tmp_path / "binancef/BTCUSDT/aggTrades/date=2026-08-01/trades.parquet"
    con = duckdb.connect()
    ids = [int(r[0]) for r in con.execute(
        f"SELECT trade_id FROM read_parquet('{p}') ORDER BY 1").fetchall()]
    con.close()
    assert 1002 not in ids and 1003 not in ids and 1004 not in ids
    assert len(ids) == 7  # the holes are reported, NOT padded


def test_duplicate_ids_in_the_source_abort(tmp_path):
    """A duplicate is corruption whatever its provenance — no archive exemption."""
    lines = _rows(4, first_id=10)
    r = _ingest(tmp_path, "2026-08-01", lines + [lines[1]])
    assert r["status"] == "failed"
    assert "duplicate" in r["error"]
    assert not (tmp_path / "binancef/BTCUSDT/aggTrades/date=2026-08-01"
                / "trades.parquet").exists()


def test_day_boundary_containment_refuses_a_stray_row(tmp_path):
    """One row from the next day means the partition is not what its name claims."""
    a, b = iv._day_bounds("2026-08-01")
    lines = _rows(3, first_id=1) + [f"99,60000.0,0.1,1,2,{b + 5},true"]
    r = _ingest(tmp_path, "2026-08-01", lines)
    assert r["status"] == "failed"
    assert "event-time day" in r["error"]
    assert not (tmp_path / "binancef/BTCUSDT/aggTrades/date=2026-08-01"
                / "trades.parquet").exists()


def test_utc_day_bucketing_is_integer_not_localtime(monkeypatch):
    """A 23:30 UTC row must land on its UTC day under ANY session timezone.

    This test exists because the trap was hit for real: DuckDB's
    ``strftime(to_timestamp(ts/1000))`` formats in the SESSION timezone and
    silently mis-buckets rows near midnight (53,006 vs the true 71,359 rows for
    2020-01-01 under Asia/Jakarta). Integer division has no timezone.
    """
    a, _ = iv._day_bounds("2026-08-01")
    late = a + 23 * 3_600_000 + 30 * 60_000  # 23:30 UTC
    monkeypatch.setenv("TZ", "Asia/Jakarta")
    assert iv._day_of(late) == "2026-08-01"
    assert iv._day_of(a) == "2026-08-01"
    assert iv._day_of(a - 1) == "2026-07-31"
    monkeypatch.setenv("TZ", "America/Los_Angeles")
    assert iv._day_of(late) == "2026-08-01"
    assert iv._day_of(a + 1_000) == "2026-08-01"


# --------------------------------------------------------------------------- #
# 9-10. The checksum gate                                                       #
# --------------------------------------------------------------------------- #
def test_checksum_gate_rejects_a_flipped_byte(tmp_path):
    r = _ingest(tmp_path, "2026-08-01", _rows(3), corrupt_checksum=True)
    assert r["status"] == "failed"
    assert "CHECKSUM mismatch" in r["error"]
    assert not (tmp_path / "binancef/BTCUSDT/aggTrades/date=2026-08-01"
                / "trades.parquet").exists()


def test_checksum_filename_field_must_match(tmp_path):
    """A checksum for a DIFFERENT file proves nothing about this one."""
    line = FIXTURES["checksum_2026_08_01"]["line"]
    assert iv.parse_checksum(line, "BTCUSDT-aggTrades-2026-08-01.zip") == \
        FIXTURES["checksum_2026_08_01"]["sha256"]
    with pytest.raises(iv.VisionError) as exc:
        iv.parse_checksum(line, "BTCUSDT-aggTrades-2026-07-31.zip")
    assert "names" in str(exc.value)
    r = _ingest(tmp_path, "2026-08-01", _rows(3), checksum_name="somebody-elses.zip")
    assert r["status"] == "failed"


def test_parse_checksum_refuses_garbage():
    for bad in ("", "not a checksum", "zz" * 32 + "  f.zip", "abc  f.zip"):
        with pytest.raises(iv.VisionError):
            iv.parse_checksum(bad, "f.zip")


def test_zip_shape_gate(tmp_path):
    """Exactly one entry, named after the object. Anything else aborts (G2)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("a.csv", "1,2,3\n")
        z.writestr("b.csv", "4,5,6\n")
    with pytest.raises(iv.VisionError) as exc:
        iv.open_single_csv(buf.getvalue(), "BTCUSDT-aggTrades-2026-08-01")
    assert "1 zip entry" in str(exc.value)


# --------------------------------------------------------------------------- #
# 11. Gap honesty — an absent day is ABSENT                                     #
# --------------------------------------------------------------------------- #
def test_absent_day_is_absent(tmp_path):
    """404 -> no parquet, no zero-row file, no interpolation. Just a ledger row.

    The archive publishes a day around D+1 07:00 UTC, so "today" always 404s.
    That must be an ANSWER, not an error and never a zero.
    """
    r = iv.ingest_day(
        date="2026-08-02", out_root=tmp_path, market="futures/um", family="aggTrades",
        vendor_symbol="BTCUSDT", venue="binancef", symbol="BTCUSDT",
        opener=_opener({}), say=lambda *a, **k: None)
    assert r["status"] == "absent" and r["http_status"] == 404
    assert r["rows"] == 0
    assert not (tmp_path / "binancef/BTCUSDT/aggTrades/date=2026-08-02").exists()
    assert list(tmp_path.rglob("*.parquet")) == []
    ledger = [json.loads(l) for l in
              (tmp_path / "_ledger.jsonl").read_text().splitlines() if l.strip()]
    assert len(ledger) == 1 and ledger[0]["status"] == "absent"
    assert ledger[0]["date"] == "2026-08-02"


def test_download_streams_and_hashes_without_buffering(tmp_path):
    """The zip goes to disk in chunks, hashed as it goes — never held whole.

    The full backfill is 79 monthly objects averaging ~530 MB; buffering one in
    memory, then its extracted CSV, then a DuckDB load of it is three copies of
    the same gigabyte. The digest is returned by the same pass that writes the
    file, so G1 verifies exactly the bytes that landed.
    """
    body = b"x" * (9 * 1024 * 1024 + 7)          # spans several 4 MiB chunks
    dest = tmp_path / "big.zip.part"
    n, digest, headers = iv.http_download(
        "https://example.invalid/big.zip", dest,
        opener=_opener({"https://example.invalid/big.zip": body}), chunk=1024 * 1024)
    assert n == len(body) == dest.stat().st_size
    assert digest == hashlib.sha256(body).hexdigest()
    assert digest == iv.sha256_file(dest)
    assert "etag" in headers


def test_download_truncates_a_failed_attempt_rather_than_splicing(tmp_path):
    """A retry restarts the file. A silently spliced download is worse than a re-fetch."""
    body = b"payload" * 1000
    url = "https://example.invalid/x.zip"
    state = {"n": 0}

    def _open(req, timeout=None):
        state["n"] += 1
        if state["n"] == 1:
            raise urllib.error.URLError("boom")
        return _FakeResponse(body)

    dest = tmp_path / "x.zip.part"
    n, digest, _ = iv.http_download(url, dest, opener=_open, retries=3)
    assert state["n"] == 2
    assert n == len(body)
    assert digest == hashlib.sha256(body).hexdigest()


def test_404_is_not_retried(tmp_path):
    """404 is an answer. Retrying it six times is both wrong and rude."""
    calls = []

    def _open(req, timeout=None):
        calls.append(req.full_url)
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

    with pytest.raises(iv.HttpAbsent):
        iv.http_get("https://example.invalid/x.zip", opener=_open, retries=6)
    assert len(calls) == 1


# --------------------------------------------------------------------------- #
# 12. Resume                                                                    #
# --------------------------------------------------------------------------- #
def test_resume_skips_a_complete_day_and_redoes_a_truncated_one(tmp_path):
    lines = _rows(6, first_id=42)
    assert _ingest(tmp_path, "2026-08-01", lines)["status"] == "ok"
    # Second pass: complete -> "already", and the network is never touched
    # (an empty opener would 404 and produce "absent" if it were).
    r = iv.ingest_day(
        date="2026-08-01", out_root=tmp_path, market="futures/um", family="aggTrades",
        vendor_symbol="BTCUSDT", venue="binancef", symbol="BTCUSDT",
        opener=_opener({}), say=lambda *a, **k: None)
    assert r["status"] == "already" and r["rows"] == 6

    # Truncate the parquet: the size no longer matches the manifest -> redo.
    p = tmp_path / "binancef/BTCUSDT/aggTrades/date=2026-08-01/trades.parquet"
    p.write_bytes(p.read_bytes()[:100])
    assert iv.day_state(tmp_path, "binancef", "BTCUSDT", "aggTrades", "2026-08-01") is None
    assert _ingest(tmp_path, "2026-08-01", lines)["status"] == "ok"
    assert p.stat().st_size > 100


def test_seam_is_reported_never_patched(tmp_path):
    """A cross-day ID discontinuity is recorded in the manifest and left alone."""
    a1, _ = iv._day_bounds("2026-08-01")
    a2, _ = iv._day_bounds("2026-08-02")
    assert _ingest(tmp_path, "2026-08-01", _rows(4, first_id=100, date="2026-08-01"))["status"] == "ok"
    # Contiguous next day: 100..103 then 104.
    r = _ingest(tmp_path, "2026-08-02", _rows(3, first_id=104, date="2026-08-02"))
    assert r["seam_contiguous"] is True
    man = json.loads((tmp_path / "binancef/BTCUSDT/aggTrades/manifests/"
                      "MANIFEST-2026-08-02.json").read_text())
    assert man["seam"] == {"prev_date": "2026-08-01", "prev_present": True,
                           "prev_id_max": 103, "first_id": 104, "gap": 0,
                           "contiguous": True}
    # Now a discontinuous third day: reported, and the missing ids stay missing.
    r = _ingest(tmp_path, "2026-08-03", _rows(3, first_id=500, date="2026-08-03"))
    assert r["status"] == "ok" and r["seam_contiguous"] is False
    man = json.loads((tmp_path / "binancef/BTCUSDT/aggTrades/manifests/"
                      "MANIFEST-2026-08-03.json").read_text())
    assert man["seam"]["gap"] == 500 - 106 - 1
    con = duckdb.connect()
    n = con.execute("SELECT count(*) FROM read_parquet('" + str(
        tmp_path / "binancef/BTCUSDT/aggTrades/date=2026-08-03/trades.parquet") + "')"
    ).fetchone()[0]
    con.close()
    assert n == 3  # nothing was invented to close the seam


# --------------------------------------------------------------------------- #
# 13. Rail 1 in code: the tree can never be placed inside the recorded store    #
# --------------------------------------------------------------------------- #
def test_out_root_refuses_to_be_inside_the_tick_store(tmp_path):
    for bad in (collector.DEFAULT_DB,
                Path(collector.DEFAULT_DB) / "vision",
                _REPO / "data" / "orderflow",
                _REPO / "data" / "orderflow" / "x",
                _REPO / "data"):            # a parent of the tick store counts too
        with pytest.raises(iv.VisionError) as exc:
            iv.assert_out_root_is_separate(Path(bad))
        assert "overlaps" in str(exc.value), bad
    # A genuinely separate tree is fine.
    assert iv.assert_out_root_is_separate(tmp_path / "vision") == \
        (tmp_path / "vision").resolve()


def test_ingest_writes_no_duckdb_file_and_no_levels_registry(tmp_path):
    """Rail 3, structurally: the MinBTL clock's only input is never touched."""
    assert _ingest(tmp_path, "2026-08-01", _rows(5))["status"] == "ok"
    assert list(tmp_path.rglob("*.duckdb")) == []
    assert list(tmp_path.rglob("levels.jsonl")) == []
    assert list(tmp_path.rglob("*.duckdb.wal")) == []


def test_partition_path_carries_its_provenance(tmp_path):
    p = iv.partition_dir(tmp_path, "binancef", "BTCUSDT", "aggTrades", "2026-08-01")
    parts = p.relative_to(tmp_path).parts
    assert parts == ("binancef", "BTCUSDT", "aggTrades", "date=2026-08-01")


# --------------------------------------------------------------------------- #
# 14. The honest limit, verbatim                                                #
# --------------------------------------------------------------------------- #
def test_honest_limit_sentences_are_verbatim_and_printed(capsys):
    """The limit must survive a refactor, and be printed on EVERY run.

    Including ``--dry-run``: a plan that omits the limit is how the limit gets
    forgotten between planning a backfill and reading its output.
    """
    doc = " ".join((iv.__doc__ or "").split())
    assert "TRADES ONLY" in doc
    assert "244 % of MinBTL(5)" in doc
    assert "1.8 % of MinBTL(5)" in doc
    for s in iv.HONEST_LIMIT_SENTENCES:
        assert s.strip()
    assert len(iv.HONEST_LIMIT_SENTENCES) == 3
    assert "TRADES ONLY" in iv.HONEST_LIMIT_SENTENCES[0]
    assert "sec_readiness" in iv.HONEST_LIMIT_SENTENCES[1]
    assert "ABSENT" in iv.HONEST_LIMIT_SENTENCES[2]

    rc = iv.main(["--start", "2026-08-01", "--end", "2026-08-01", "--dry-run",
                  "--out", "/tmp/btcquant-vision-dryrun-does-not-exist"])
    assert rc == 0
    out = capsys.readouterr().out
    for s in iv.HONEST_LIMIT_SENTENCES:
        assert s in out
    assert "nothing downloaded" in out


def test_manifest_carries_the_honest_limit(tmp_path):
    assert _ingest(tmp_path, "2026-08-01", _rows(3))["status"] == "ok"
    man = json.loads((tmp_path / "binancef/BTCUSDT/aggTrades/manifests/"
                      "MANIFEST-2026-08-01.json").read_text())
    assert man["honest_limit"] == list(iv.HONEST_LIMIT_SENTENCES)
    assert man["source"]["checksum_verified"] is True
    assert man["provenance"]["host"] == "https://data.binance.vision"


# --------------------------------------------------------------------------- #
# Range planning                                                                #
# --------------------------------------------------------------------------- #
def test_dates_inclusive_includes_both_ends():
    assert iv.dates_inclusive("2026-07-30", "2026-08-01") == \
        ["2026-07-30", "2026-07-31", "2026-08-01"]
    assert iv.dates_inclusive("2026-08-01", "2026-08-01") == ["2026-08-01"]
    with pytest.raises(iv.VisionError):
        iv.dates_inclusive("2026-08-02", "2026-08-01")


def test_auto_granularity_uses_monthly_only_for_whole_past_months():
    import datetime as _dt
    today = _dt.date(2026, 8, 2)
    dates = iv.dates_inclusive("2026-06-01", "2026-08-02")
    months, daily = iv.plan_granularity(dates, "auto", today=today)
    assert [m for m, _ in months] == ["2026-06", "2026-07"]
    # The running month stays daily — its monthly file does not exist yet.
    assert daily == ["2026-08-01", "2026-08-02"]
    # A partial past month stays daily too: a monthly file would pull 30 days to
    # write 3, and the extra days would land in the tree unasked.
    months, daily = iv.plan_granularity(
        iv.dates_inclusive("2026-06-10", "2026-06-12"), "auto", today=today)
    assert months == [] and len(daily) == 3


# --------------------------------------------------------------------------- #
# 15. The MONTHLY path — the PRIMARY path of a backfill (`auto` routes every    #
#     whole past month through it), and the one that had no tests at all.       #
# --------------------------------------------------------------------------- #
def _month_archive(month: str, lines, *, market="futures/um", family="aggTrades",
                   symbol="BTCUSDT"):
    """The two objects the ingester fetches for one MONTH."""
    name = f"{symbol}-{family}-{month}.zip"
    url = iv.archive_url(market, "monthly", family, symbol, month)
    blob = _zip_bytes(f"{symbol}-{family}-{month}.csv", lines)
    return {url: blob,
            url + ".CHECKSUM": f"{hashlib.sha256(blob).hexdigest()}  {name}\n".encode()}


def _ingest_month(tmp_path, month, dates, objects, **kw):
    return iv.ingest_month(
        month=month, dates=dates, out_root=tmp_path, market="futures/um",
        family="aggTrades", vendor_symbol="BTCUSDT", venue="binancef", symbol="BTCUSDT",
        opener=_opener(objects), say=lambda *a, **k: None, **kw)


def test_monthly_splits_into_the_same_per_day_partitions_with_id_extents(tmp_path):
    """A month-ingested day must not be structurally poorer than a daily one.

    DESIGN §3d keeps `first_trade_id`/`last_trade_id` extents in the manifest —
    the daily path did, the monthly path did not, which weakens "monthly buys
    request count, never leniency" at the manifest level even when the gates are
    equal.
    """
    lines = _rows(4, first_id=10, date="2026-06-01") + _rows(3, first_id=14,
                                                             date="2026-06-02")
    rows = _ingest_month(tmp_path, "2026-06", ["2026-06-01", "2026-06-02"],
                         _month_archive("2026-06", lines))
    assert [r["status"] for r in rows] == ["ok", "ok"]
    assert [r["rows"] for r in rows] == [4, 3]
    for date, n, first, last in (("2026-06-01", 4, 10, 14), ("2026-06-02", 3, 14, 17)):
        man = json.loads((tmp_path / "binancef/BTCUSDT/aggTrades/manifests"
                          / f"MANIFEST-{date}.json").read_text())
        assert man["normalized"]["rows"] == n
        # _rows() writes first_trade_id = id*2 and last_trade_id = id*2+1.
        assert man["normalized"]["first_trade_id_min"] == first * 2
        assert man["normalized"]["last_trade_id_max"] == (last - 1) * 2 + 1
        assert man["source"]["granularity"] == "monthly"


def test_a_missing_MONTHLY_object_is_not_an_absent_DAY(tmp_path):
    """Rail 5 has an inverse: a day the archive DOES serve is never "not served".

    Measured 2026-08-02 against the real archive: the 2026-08 monthly object was
    HTTP 404 while every published day of it was HTTP 200. Binance publishes the
    monthly bundle days after the month ends, so `--all --yes` run in the first
    days of a month used to write an entire published month into the provenance
    ledger as absent — and exit 0.
    """
    dates = ["2026-06-01", "2026-06-02", "2026-06-03"]
    objs = {}
    for d in ("2026-06-01", "2026-06-02"):          # 06-03 genuinely unpublished
        objs.update(_archive(d, _rows(3, first_id=100 if d.endswith("01") else 200,
                                      date=d)))
    rows = _ingest_month(tmp_path, "2026-06", dates, objs)   # NO monthly object
    by_date = {r["date"]: r for r in rows}
    assert by_date["2026-06-01"]["status"] == "ok" and by_date["2026-06-01"]["rows"] == 3
    assert by_date["2026-06-02"]["status"] == "ok"
    # Only the day the archive really does not publish is absent, and it is
    # recorded against the DAILY url it was actually asked for.
    assert by_date["2026-06-03"]["status"] == "absent"
    ledger = [json.loads(l) for l in
              (tmp_path / "_ledger.jsonl").read_text().splitlines() if l.strip()]
    absent = [r for r in ledger if r["status"] == "absent"]
    assert len(absent) == 1 and absent[0]["date"] == "2026-06-03"
    assert "/daily/" in absent[0]["url"]
    assert (tmp_path / "binancef/BTCUSDT/aggTrades/date=2026-06-01/trades.parquet").exists()
    assert not (tmp_path / "binancef/BTCUSDT/aggTrades/date=2026-06-03").exists()


def test_monthly_resume_never_re_downloads_a_complete_month(tmp_path):
    """~530 MB per month off the wire to write nothing is not a resume.

    The daily path returns `already` before any request; the monthly path
    downloaded first and checked after, so an interrupted `--all` re-pulled every
    finished month.
    """
    lines = _rows(4, first_id=10, date="2026-06-01")
    objs = _month_archive("2026-06", lines)
    first = _ingest_month(tmp_path, "2026-06", ["2026-06-01"], objs)
    assert first[0]["status"] == "ok"

    class _Boom(Exception):
        pass

    def _explode(req, timeout=None):
        raise _Boom("the wire must not be touched for a complete month")

    rows = iv.ingest_month(
        month="2026-06", dates=["2026-06-01"], out_root=tmp_path, market="futures/um",
        family="aggTrades", vendor_symbol="BTCUSDT", venue="binancef", symbol="BTCUSDT",
        opener=_explode, say=lambda *a, **k: None)
    # ...and the counts it reports are the TRUE ones, not zeros.
    assert rows[0]["status"] == "already" and rows[0]["rows"] == 4
    assert rows[0]["bytes"] == (tmp_path / "binancef/BTCUSDT/aggTrades/"
                                "date=2026-06-01/trades.parquet").stat().st_size


def test_a_malformed_MONTHLY_object_fails_per_day_and_never_kills_the_run(tmp_path):
    """`except VisionError` was too narrow — and `auto` routes every month here.

    A duckdb ConversionException (or a zipfile.BadZipFile) escaped ingest_month,
    escaped main(), and aborted a ~2.7 h backfill with a traceback: no ledger
    row, no FAILED json, no record of which day failed.
    """
    # 7 columns, integer first field (so G3 passes) and a non-boolean flag, which
    # DuckDB refuses at CREATE TEMP TABLE — a non-VisionError, by construction.
    a, _ = iv._day_bounds("2026-06-01")
    bad = [f"10,60000.0,0.5,20,21,{a},maybe"]
    rows = _ingest_month(tmp_path, "2026-06", ["2026-06-01", "2026-06-02"],
                         _month_archive("2026-06", bad))
    assert [r["status"] for r in rows] == ["failed", "failed"]
    assert "Conversion" in rows[0]["error"] or "conversion" in rows[0]["error"].lower()
    man = tmp_path / "binancef/BTCUSDT/aggTrades/manifests"
    assert sorted(p.name for p in man.glob("FAILED-*.json")) == \
        ["FAILED-2026-06-01.json", "FAILED-2026-06-02.json"]
    ledger = [json.loads(l) for l in
              (tmp_path / "_ledger.jsonl").read_text().splitlines() if l.strip()]
    assert [r["date"] for r in ledger] == ["2026-06-01", "2026-06-02"]
    assert all(r["status"] == "failed" for r in ledger)
    assert list(tmp_path.rglob("*.parquet")) == []          # nothing was written


# --------------------------------------------------------------------------- #
# 16. The rails live where the WRITE happens, not only at the CLI               #
# --------------------------------------------------------------------------- #
def test_the_out_root_rail_is_enforced_by_the_writers_not_only_by_main(tmp_path):
    """A rail only `main()` enforces is a habit of the CLI, not a rail.

    `assert_out_root_is_separate` had exactly one call site. `ingest_day` /
    `ingest_month` happily wrote the archive tree — parquet, manifests and
    `_ledger.jsonl` — inside `data/ticks/`, next to `levels.jsonl`, which the
    module docstring names as the worst case.
    """
    store = Path(collector.DEFAULT_DB)
    for call in (
        lambda: iv.ingest_day(
            date="2026-08-01", out_root=store, market="futures/um", family="aggTrades",
            vendor_symbol="BTCUSDT", venue="binancef", symbol="BTCUSDT",
            opener=_opener(_archive("2026-08-01", _rows(3))), say=lambda *a, **k: None),
        lambda: iv.ingest_month(
            month="2026-06", dates=["2026-06-01"], out_root=store, market="futures/um",
            family="aggTrades", vendor_symbol="BTCUSDT", venue="binancef",
            symbol="BTCUSDT", opener=_opener({}), say=lambda *a, **k: None),
    ):
        with pytest.raises(iv.VisionError) as exc:
            call()
        assert "overlaps" in str(exc.value)
    # Nothing was written on the way to refusing — in particular no FAILED json,
    # which the day's own error handler would have put inside the tick store.
    assert not (store / "binancef").exists()
    assert not (store / "_ledger.jsonl").exists()


def test_the_vendor_object_can_only_land_where_its_id_space_lives(tmp_path):
    """`--vendor-symbol` picks the URL; `--venue/--symbol` pick the COLUMNS.

    They were never compared, so ETHUSDT rows could be written into
    `binancef/BTCUSDT` — the partition `order_flow_bars` reads by DEFAULT — and
    Binance rows could be written under `bybit`, whose trade-id space is
    unrelated to the aggTradeId space the exact-dedup argument rests on.
    """
    # The instrument is part of the allowlist now, not just the family.
    iv.check_scope("futures/um", "aggTrades", "BTCUSDT")
    with pytest.raises(iv.VisionError, match="aggTradeId space"):
        iv.check_scope("futures/um", "aggTrades", "ETHUSDT")
    with pytest.raises(iv.VisionError, match="registered target"):
        iv.check_target("futures/um", "aggTrades", "BTCUSDT", "bybit", "BTCUSDT")
    with pytest.raises(iv.VisionError, match="registered target"):
        iv.check_target("futures/um", "aggTrades", "BTCUSDT", "binancef", "ETHUSDT")
    iv.check_target("futures/um", "aggTrades", "BTCUSDT", "binancef", "BTCUSDT")

    # ...and the writers refuse before a byte is written.
    with pytest.raises(iv.VisionError):
        iv.ingest_day(
            date="2026-08-01", out_root=tmp_path, market="futures/um", family="aggTrades",
            vendor_symbol="ETHUSDT", venue="binancef", symbol="BTCUSDT",
            opener=_opener({}), say=lambda *a, **k: None)
    with pytest.raises(iv.VisionError):
        iv.ingest_day(
            date="2026-08-01", out_root=tmp_path, market="futures/um", family="aggTrades",
            vendor_symbol="BTCUSDT", venue="bybit", symbol="ETHUSDT",
            opener=_opener({}), say=lambda *a, **k: None)
    assert list(tmp_path.rglob("*")) == []


def test_every_json_branch_carries_the_honest_limit(tmp_path, capsys):
    """--json is the path most likely to be piped into a report or a dashboard.

    That is exactly where "aggTrades is TRADES ONLY" most needs to travel with
    the numbers, and it was the one output shape that dropped it.
    """
    out_dir = tmp_path / "vision"
    rc = iv.main(["--dry-run", "--json", "--start", "2026-06-01", "--end", "2026-06-02",
                  "--out", str(out_dir)])
    assert rc == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["honest_limit"] == list(iv.HONEST_LIMIT_SENTENCES)
