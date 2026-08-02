"""test_vision_overlap.py — the LOAD-BEARING claim, against the real archive.

M7 is admissible under DESIGN §0.7 for exactly one reason: the public archive is
the **same venue, same stream, same aggTradeId space** the collector's
``binancef-aggTrades`` leg already records. Everything else — exact dedup on
``(exchange, symbol, trade_id)``, gap detection by ID continuity instead of by a
timestamp guess — follows from that identity. If it is false, the item is the
mixed-history backfill STRATEGY §6 refuses.

So it is checked against the real thing rather than against a fixture. That makes
this file **not a CI gate**: it needs network AND a closed recorded day file for a
date the archive publishes, and it skips cleanly when either is missing. Run it
deliberately::

    python3 -m pytest tests/test_vision_overlap.py -q -rs

Measured twice independently on 2026-08-02 for 2026-08-01: 399,219 archive rows,
399,219 distinct ids spanning exactly 399,219 (zero in-day holes), set difference
**0 in both directions**, and **0 mismatches** on ``ts_ms`` / ``price`` / ``qty``
/ ``aggressor_buy`` across all 399,219 joined rows. Cross-day seam
``3399378199 -> 3399378200``, exactly +1.

ONE HONEST ACCOMMODATION, stated rather than absorbed: the RECORDED side of that
comparison currently carries **duplicate** ``(exchange, symbol, trade_id)`` rows
(971 binancef + 68 coinbase on 2026-08-01) because ``trades`` has no unique
constraint and the aggTrades dedup guard is in-memory only, so a collector
restart re-serves a range it already wrote. That is a real, separate bug — not
M7's — and this test handles it the only honest way: it compares through
``SELECT DISTINCT`` **and prints the surplus**. Swallowing it silently would let
a recorded defect hide inside a passing archive test.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

duckdb = pytest.importorskip("duckdb")  # opt-in dep — requirements-collector.txt

_REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "ingest_vision", _REPO / "scripts" / "ingest_vision.py")
iv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(iv)

STORE = _REPO / "data" / "ticks"
_TRADES_PROJ = ", ".join(c for c, _ in iv.TRADES_COLUMNS)

pytestmark = pytest.mark.skipif(
    os.environ.get("BTCQ_SKIP_NETWORK_TESTS") == "1",
    reason="BTCQ_SKIP_NETWORK_TESTS=1")


def _online() -> bool:
    try:
        req = urllib.request.Request(
            iv.archive_url("futures/um", "daily", "aggTrades", "BTCUSDT", "2026-08-01"),
            headers={"User-Agent": iv.UA}, method="HEAD")
        urllib.request.urlopen(req, timeout=10).close()
        return True
    except Exception:  # noqa: BLE001 — offline is a skip, not a failure
        return False


def _closed_recorded_days() -> list[str]:
    """Closed §3c day files, newest first. Today's open file is never a candidate."""
    if not STORE.is_dir():
        return []
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    yday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    out = [p.stem for p in STORE.glob("*.duckdb")
           if len(p.stem) == 10 and p.stem[4] == "-" and p.stem < today]
    # Yesterday is only usable once the archive has published it (~D+1 07:00 UTC).
    return sorted(out, reverse=True)


def _pick_day() -> str:
    for date in _closed_recorded_days():
        try:
            req = urllib.request.Request(
                iv.archive_url("futures/um", "daily", "aggTrades", "BTCUSDT", date),
                headers={"User-Agent": iv.UA}, method="HEAD")
            urllib.request.urlopen(req, timeout=10).close()
            return date
        except urllib.error.HTTPError:
            continue
        except Exception:  # noqa: BLE001
            break
    return ""


@pytest.fixture(scope="module")
def overlap_day() -> str:
    if not _online():
        pytest.skip("data.binance.vision unreachable — network test")
    date = _pick_day()
    if not date:
        pytest.skip("no closed recorded day file that the archive also publishes")
    return date


@pytest.fixture(scope="module")
def ingested(tmp_path_factory, overlap_day):
    """Ingest the chosen day into a TEMP tree. The live store is never touched."""
    out = tmp_path_factory.mktemp("vision-overlap")
    row = iv.ingest_day(
        date=overlap_day, out_root=out, market="futures/um", family="aggTrades",
        vendor_symbol="BTCUSDT", venue="binancef", symbol="BTCUSDT",
        say=lambda *a, **k: None)
    if row["status"] != "ok":
        pytest.skip(f"archive ingest for {overlap_day} did not complete: {row}")
    return out, overlap_day


def test_archive_day_equals_recorded_day(ingested, capsys):
    """Same rows, same fields. Set difference 0 both ways, 0 field mismatches.

    The whole item rests on this. Compared through DISTINCT on the recorded side
    (see the module docstring) with the duplicate surplus PRINTED, never absorbed.
    """
    out, date = ingested
    pq = out / "binancef" / "BTCUSDT" / "aggTrades" / f"date={date}" / "trades.parquet"
    day_file = STORE / f"{date}.duckdb"

    # Copy the day file: the live collector must never contend for a lock, and a
    # closed day is immutable so a copy is the same bytes.
    scratch = out / f"{date}.copy.duckdb"
    shutil.copy2(day_file, scratch)

    con = duckdb.connect()
    try:
        con.execute("SET enable_progress_bar=false")
        con.execute(f"CREATE VIEW v AS SELECT {_TRADES_PROJ} FROM "
                    f"read_parquet('{str(pq).replace(chr(39), chr(39) * 2)}')")
        con.execute(f"ATTACH '{str(scratch).replace(chr(39), chr(39) * 2)}' AS rec (READ_ONLY)")
        con.execute("CREATE VIEW r AS SELECT * FROM rec.trades "
                    "WHERE exchange = 'binancef' AND symbol = 'BTCUSDT'")

        v_rows, v_ids, v_min, v_max = con.execute(
            "SELECT count(*), count(DISTINCT trade_id), "
            "min(CAST(trade_id AS BIGINT)), max(CAST(trade_id AS BIGINT)) FROM v"
        ).fetchone()
        r_rows, r_ids = con.execute(
            "SELECT count(*), count(DISTINCT trade_id) FROM r").fetchone()
        dup_keys, dup_surplus = con.execute(
            "SELECT count(*), coalesce(sum(c - 1), 0) FROM (SELECT count(*) AS c FROM r "
            "GROUP BY trade_id HAVING count(*) > 1)").fetchone()

        v_only = con.execute(
            "SELECT count(*) FROM (SELECT trade_id FROM v EXCEPT "
            "SELECT trade_id FROM r)").fetchone()[0]
        r_only = con.execute(
            "SELECT count(*) FROM (SELECT trade_id FROM r EXCEPT "
            "SELECT trade_id FROM v)").fetchone()[0]
        n, ts_mm, d_px, d_qty, side_mm = con.execute(
            """SELECT count(*),
                      count(*) FILTER (WHERE v.ts_ms <> d.ts_ms),
                      max(abs(v.price - d.price)), max(abs(v.qty - d.qty)),
                      count(*) FILTER (WHERE v.aggressor_buy <> d.aggressor_buy)
               FROM v JOIN (SELECT DISTINCT trade_id, ts_ms, price, qty, aggressor_buy
                            FROM r) d USING (trade_id)"""
        ).fetchone()
    finally:
        con.close()

    with capsys.disabled():
        print(f"\noverlap {date}: archive {v_rows:,} rows / {v_ids:,} distinct, "
              f"id span {v_max - v_min + 1:,}")
        print(f"  recorded {r_rows:,} rows / {r_ids:,} distinct "
              f"-> duplicate keys {dup_keys:,}, surplus rows {dup_surplus:,} "
              "(a RECORDED defect, separate from M7 — `trades` has no unique "
              "constraint and the aggTrades dedup guard is in-memory only)")
        print(f"  archive\\recorded {v_only} | recorded\\archive {r_only}")
        print(f"  joined {n:,}: ts_mismatch {ts_mm}, max|Δprice| {d_px}, "
              f"max|Δqty| {d_qty}, side_mismatch {side_mm}")

    assert v_rows == v_ids, "the archive itself must carry no duplicate aggTradeId"
    assert v_max - v_min + 1 == v_rows, "no in-day ID holes on a published day"
    assert v_only == 0 and r_only == 0, "the two are the SAME set of aggTradeIds"
    assert n == v_ids
    assert ts_mm == 0 and side_mm == 0
    assert d_px == 0.0 and d_qty == 0.0


def test_cross_day_seam_is_contiguous(ingested):
    """``first_id(D) == last_id(D-1) + 1`` — the counter, not a timestamp guess.

    This is what makes the archive's gap census categorically stronger than the
    30 s-silence heuristic everywhere else in the repo: a discontinuity is stated
    by the venue's own monotonic id, so there is no threshold to argue about.
    """
    out, date = ingested
    prev = (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    row = iv.ingest_day(
        date=prev, out_root=out, market="futures/um", family="aggTrades",
        vendor_symbol="BTCUSDT", venue="binancef", symbol="BTCUSDT",
        say=lambda *a, **k: None)
    if row["status"] != "ok":
        pytest.skip(f"archive does not publish {prev}: {row['status']}")
    man_dir = out / "binancef" / "BTCUSDT" / "aggTrades" / "manifests"
    prev_max = json.loads((man_dir / f"MANIFEST-{prev}.json").read_text())["normalized"]["id_max"]
    cur_min = json.loads((man_dir / f"MANIFEST-{date}.json").read_text())["normalized"]["id_min"]
    assert cur_min == prev_max + 1, f"seam {prev}->{date}: {prev_max} -> {cur_min}"


def test_l3_qa_passes_over_the_real_archive_partition(ingested, capsys):
    """Guard-rail 4 against real bytes: same gate, no exemption for being an archive."""
    ct_spec = importlib.util.spec_from_file_location(
        "check_ticks", _REPO / "scripts" / "check_ticks.py")
    ct = importlib.util.module_from_spec(ct_spec)
    ct_spec.loader.exec_module(ct)
    out, _date = ingested
    root = out / "binancef" / "BTCUSDT" / "aggTrades"
    rc = ct.main(["--vision", str(root), "--json"])
    report = json.loads(capsys.readouterr().out)
    assert rc == 0 and report["exit_code"] == 0
    integ = next(s for s in report["sections"] if s["name"] == "integrity")
    assert integ["data"]["duplicate_trade_keys"] == 0
    readiness = next(s for s in report["sections"] if s["name"] == "research readiness")
    assert readiness["data"]["computed"] is False
