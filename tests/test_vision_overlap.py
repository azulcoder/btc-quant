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


DAMAGE = json.loads((_REPO / "reports" / "recorded-damage.json").read_text())


def _documented_missing(date: str, venue: str = "binancef", symbol: str = "BTCUSDT") -> set:
    """The EXACT set of ids a recorded damage entry accounts for. Empty if none."""
    out: set = set()
    for e in DAMAGE["entries"]:
        if e["date"] == date and e["venue"] == venue and e["symbol"] == symbol:
            for lo, hi in e["id_ranges"]:
                out.update(range(int(lo), int(hi) + 1))
    return out


@pytest.fixture(scope="module")
def measured(ingested):
    """Measure once; the assertions below are separate so they can differ in kind.

    FIDELITY and COMPLETENESS are not the same claim and must not share a gate.
    Completeness can carry a labelled, itemised exception (the recorded store has
    permanent holes and pretending otherwise would be the lie). Fidelity cannot:
    a collector that invents a print, or disagrees with the venue about one it did
    record, is broken in a way no record of past damage can excuse.
    """
    out, date = ingested
    pq = out / "binancef" / "BTCUSDT" / "aggTrades" / f"date={date}" / "trades.parquet"

    # Copy the day file: the live collector must never contend for a lock, and a
    # closed day is immutable so a copy is the same bytes.
    scratch = out / f"{date}.copy.duckdb"
    shutil.copy2(STORE / f"{date}.duckdb", scratch)

    con = duckdb.connect()
    try:
        con.execute("SET enable_progress_bar=false")
        con.execute(f"CREATE VIEW v AS SELECT {_TRADES_PROJ} FROM "
                    f"read_parquet('{str(pq).replace(chr(39), chr(39) * 2)}')")
        con.execute(f"ATTACH '{str(scratch).replace(chr(39), chr(39) * 2)}' AS rec (READ_ONLY)")
        con.execute("CREATE VIEW r AS SELECT * FROM rec.trades "
                    "WHERE exchange = 'binancef' AND symbol = 'BTCUSDT'")

        m = {"date": date}
        m["v_rows"], m["v_ids"], m["v_min"], m["v_max"] = con.execute(
            "SELECT count(*), count(DISTINCT trade_id), "
            "min(CAST(trade_id AS BIGINT)), max(CAST(trade_id AS BIGINT)) FROM v").fetchone()
        m["r_rows"], m["r_ids"] = con.execute(
            "SELECT count(*), count(DISTINCT trade_id) FROM r").fetchone()
        m["dup_keys"], m["dup_surplus"] = con.execute(
            "SELECT count(*), coalesce(sum(c - 1), 0) FROM (SELECT count(*) AS c FROM r "
            "GROUP BY trade_id HAVING count(*) > 1)").fetchone()
        # The missing IDS, not merely how many: the completeness gate matches the
        # exact set, so one print of NEW loss cannot hide behind a documented count.
        m["v_only"] = {int(x[0]) for x in con.execute(
            "SELECT CAST(trade_id AS BIGINT) FROM (SELECT trade_id FROM v EXCEPT "
            "SELECT trade_id FROM r)").fetchall()}
        m["r_only"] = con.execute(
            "SELECT count(*) FROM (SELECT trade_id FROM r EXCEPT "
            "SELECT trade_id FROM v)").fetchone()[0]
        (m["n"], m["ts_mm"], m["d_px"], m["d_qty"], m["side_mm"]) = con.execute(
            """SELECT count(*),
                      count(*) FILTER (WHERE v.ts_ms <> d.ts_ms),
                      max(abs(v.price - d.price)), max(abs(v.qty - d.qty)),
                      count(*) FILTER (WHERE v.aggressor_buy <> d.aggressor_buy)
               FROM v JOIN (SELECT DISTINCT trade_id, ts_ms, price, qty, aggressor_buy
                            FROM r) d USING (trade_id)""").fetchone()
    finally:
        con.close()
    return m


def test_the_archive_side_is_internally_sound(measured, capsys):
    """Before comparing against it, the reference must be a valid reference."""
    m = measured
    with capsys.disabled():
        print(f"\noverlap {m['date']}: archive {m['v_rows']:,} rows / {m['v_ids']:,} distinct, "
              f"id span {m['v_max'] - m['v_min'] + 1:,}")
        print(f"  recorded {m['r_rows']:,} rows / {m['r_ids']:,} distinct "
              f"-> duplicate keys {m['dup_keys']:,}, surplus rows {m['dup_surplus']:,} "
              "(a RECORDED defect, separate from M7 — `trades` has no unique "
              "constraint and the aggTrades dedup guard is in-memory only)")
        print(f"  archive\\recorded {len(m['v_only'])} | recorded\\archive {m['r_only']}")
        print(f"  joined {m['n']:,}: ts_mismatch {m['ts_mm']}, max|Δprice| {m['d_px']}, "
              f"max|Δqty| {m['d_qty']}, side_mismatch {m['side_mm']}")
    assert m["v_rows"] == m["v_ids"], "the archive itself must carry no duplicate aggTradeId"
    assert m["v_max"] - m["v_min"] + 1 == m["v_rows"], "no in-day ID holes on a published day"


def test_FIDELITY_recorded_never_invents_a_print(measured):
    """recorded \\ archive == 0. NO EXCEPTION PATH — by construction.

    `reports/recorded-damage.json` is deliberately not consulted here. A print the
    venue never published, appearing in our store, is fabricated history: the exact
    thing DESIGN §0.7 and STRATEGY §6 refuse. There is no damage record that makes
    that acceptable, so there is no code path that reads one.
    """
    assert measured["r_only"] == 0, (
        f"{measured['r_only']} recorded id(s) do not exist in the venue's own archive — "
        "fabricated history, and no damage record can excuse it")


def test_FIDELITY_every_recorded_field_matches_the_venue(measured):
    """Every field of every print we DID record. NO EXCEPTION PATH — by construction."""
    m = measured
    assert m["n"] == m["v_ids"] - len(m["v_only"]), "join lost rows unexpectedly"
    assert m["ts_mm"] == 0, f"{m['ts_mm']} ts_ms mismatches against the venue"
    assert m["side_mm"] == 0, f"{m['side_mm']} aggressor-side mismatches against the venue"
    assert m["d_px"] == 0.0, f"max |Δprice| {m['d_px']} — recorded price disagrees with the venue"
    assert m["d_qty"] == 0.0, f"max |Δqty| {m['d_qty']} — recorded qty disagrees with the venue"


def test_COMPLETENESS_is_zero_or_exactly_the_documented_damage(measured):
    """archive \\ recorded == 0, unless `reports/recorded-damage.json` names it.

    Not a date-based exemption: the documented ID RANGES must equal the measured
    missing set EXACTLY. New loss on a day that already has an entry still fails,
    down to a single print, and the failure names the ids it did not expect.
    """
    m = measured
    documented = _documented_missing(m["date"])
    undocumented = m["v_only"] - documented
    over_claimed = documented - m["v_only"]
    assert not undocumented, (
        f"{len(undocumented)} UNDOCUMENTED missing print(s) on {m['date']} "
        f"(e.g. {sorted(undocumented)[:5]}). Either the collector lost new data, or "
        "reports/recorded-damage.json is stale. Measure the UTC blocks and the cause, "
        "then write an entry — do not widen an existing range to make this pass.")
    assert not over_claimed, (
        f"reports/recorded-damage.json claims {len(over_claimed)} missing id(s) on "
        f"{m['date']} that are actually present (e.g. {sorted(over_claimed)[:5]}). "
        "The record overstates the damage; narrow it to what is measured.")


def test_damage_record_is_a_record_not_a_list_of_dates():
    """Every entry carries every required field, non-empty. Enforced, not hoped for.

    A damage list that degrades into bare dates becomes a place to dump failures.
    The schema is the thing that stops that, so it is asserted rather than asked for.
    """
    required = DAMAGE["_required_fields"]
    for i, e in enumerate(DAMAGE["entries"]):
        for f in required:
            assert f in e, f"entry {i} ({e.get('date', '?')}) is missing '{f}'"
            assert e[f] not in (None, "", [], {}), (
                f"entry {i} ({e.get('date', '?')}) has an empty '{f}' — a field that is "
                "present but blank is the decay this test exists to prevent")
        assert e["missing_rows"] == sum(hi - lo + 1 for lo, hi in e["id_ranges"]), (
            f"entry {i} ({e['date']}): missing_rows disagrees with the id_ranges it lists")
        assert len(e["utc_blocks"]) == len(e["id_ranges"]), (
            f"entry {i} ({e['date']}): every id range must state its UTC block")


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
