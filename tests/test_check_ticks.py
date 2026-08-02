"""test_check_ticks.py — L3 report-card tests (scripts/check_ticks.py §6 readiness).

Fully deterministic, **no network**: synthetic §3c day stores are seeded through
``collector.open_db`` (the canonical schema) plus a hand-written ``levels.jsonl``
registry, and every MinBTL expectation is HAND-COMPUTED here from the Bailey et
al. (2014) closed form — an independent restatement, so a silent convention
change in ``btcquant.risk.min_backtest_length`` (or in the script's years->days
conversion, 365 d/yr for the 24/7 1h-bar market) fails these tests loudly.

The readiness meter is INFO only, by design (a young store is time-gated, not
defective — DESIGN §6): these tests also pin that INFO never outranks OK and
never touches the exit code.

Collector deps are opt-in (requirements-collector.txt): skips cleanly without duckdb.
"""

from __future__ import annotations

import importlib.util
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import pytest

duckdb = pytest.importorskip("duckdb")  # opt-in dep — requirements-collector.txt

from scipy import stats  # noqa: E402 — core dep (requirements.txt)

from btcquant import collector  # noqa: E402

# --------------------------------------------------------------------------- #
# Load scripts/check_ticks.py as a module (scripts/ is not a package).         #
# --------------------------------------------------------------------------- #
_REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "check_ticks", _REPO / "scripts" / "check_ticks.py"
)
ct = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ct)


# --------------------------------------------------------------------------- #
# Independent hand computation of MinBTL (Bailey et al. 2014) — NOT imported   #
# from btcquant.risk, so the test cross-checks the implementation.             #
# --------------------------------------------------------------------------- #
def _minbtl_years(n: int) -> float:
    """MinBTL (yrs) = 2·ln(N) / E[max_N], E[max_N] via the Bailey-LdP form."""
    gamma = 0.5772156649015329  # Euler-Mascheroni constant
    z1 = stats.norm.ppf(1.0 - 1.0 / n)
    z2 = stats.norm.ppf(1.0 - 1.0 / (n * math.e))
    return 2.0 * math.log(n) / ((1.0 - gamma) * z1 + gamma * z2)


def _ms(date: str, h: int = 12) -> int:
    return int(
        datetime.strptime(date, "%Y-%m-%d")
        .replace(hour=h, tzinfo=timezone.utc)
        .timestamp()
        * 1000
    )


def _seed_day(root: Path, date: str) -> None:
    """One §3c day file with a single honest trade (canonical schema)."""
    root.mkdir(parents=True, exist_ok=True)
    con = collector.open_db(root / f"{date}.duckdb")
    con.execute(
        "INSERT INTO trades VALUES (?,?,?,?,?,?,?)",
        ["bybit", "BTCUSDT", f"t-{date}", _ms(date), 60_000.0, 0.01, True],
    )
    con.close()


def _write_registry(root: Path, dates: list[str]) -> None:
    """Hand-written §4f levels.jsonl — full row shape, only `date` matters here."""
    rows = [
        {"date": d, "o": 1.0, "h": 2.0, "l": 0.5, "c": 1.5,
         "poc": 1.0, "vah": 2.0, "val": 0.5, "vol": 10.0}
        for d in dates
    ]
    (root / "levels.jsonl").write_text(
        "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows),
        encoding="utf-8",
    )


def _run_json(root: Path, capsys) -> dict:
    rc = ct.main(["--db", str(root), "--json"])
    report = json.loads(capsys.readouterr().out)
    assert rc == report["exit_code"]
    return report


def _readiness(report: dict) -> dict:
    return next(s for s in report["sections"] if s["name"] == "research readiness")


# --------------------------------------------------------------------------- #
# 1. Registry + store -> exact day math for N in {5, 20, 100}.                 #
# --------------------------------------------------------------------------- #
def test_readiness_exact_day_math(tmp_path, capsys):
    root = tmp_path / "ticks"
    # Registry: 3 closed days; day files extend the LOCAL span one day further
    # (2026-07-04 = the still-accumulating day, no registry row yet).
    _seed_day(root, "2026-07-03")
    _seed_day(root, "2026-07-04")
    _write_registry(root, ["2026-07-01", "2026-07-02", "2026-07-03"])

    report = _run_json(root, capsys)
    sec = _readiness(report)
    assert sec["verdict"] == "INFO"
    d = sec["data"]

    # Span is first REGISTRY date -> newest LOCAL day, inclusive: 07-01..07-04.
    assert d["first_registry_date"] == "2026-07-01"
    assert d["newest_local_date"] == "2026-07-04"
    assert d["registry_days"] == 3  # holes stay visible: 3 rows inside a 4-day span
    assert d["span_days"] == 4
    assert d["bars_1h"] == 4 * 24 == 96

    # MinBTL per trial count, in DAYS of 1h bars: years (hand-computed) * 365
    # (24/7 market — the repo's 1h year is 24*365 bars, compare._ppy).
    for n in (5, 20, 100):
        expect_yrs = _minbtl_years(n)
        assert d["minbtl"][str(n)]["years"] == pytest.approx(expect_yrs, rel=1e-9)
        assert d["minbtl"][str(n)]["days"] == pytest.approx(expect_yrs * 365.0, rel=1e-9)

    # The verdict line quotes the N=20 target exactly as computed.
    target_days = _minbtl_years(20) * 365.0
    assert d["target_n"] == 20
    assert d["pct_toward_target"] == pytest.approx(4 / target_days * 100.0, rel=1e-9)
    want = (
        f"4 of {target_days:,.0f} days toward N=20 OOS candidacy "
        f"({4 / target_days * 100.0:.1f}%)"
    )
    assert any(want in line for line in sec["lines"])
    # Necessary-not-sufficient is stated in the section itself, verbatim intent.
    assert any("NECESSARY, not sufficient" in line for line in sec["lines"])


# --------------------------------------------------------------------------- #
# 2. Empty registry -> honest "no recorded days yet", still INFO, still exit 0. #
# --------------------------------------------------------------------------- #
def test_readiness_empty_registry_is_honest(tmp_path, capsys):
    root = tmp_path / "ticks"
    _seed_day(root, "2026-07-04")  # day file exists, but no day has CLOSED yet

    report = _run_json(root, capsys)
    assert report["exit_code"] == 0  # young store never fails the gate
    sec = _readiness(report)
    assert sec["verdict"] == "INFO"
    d = sec["data"]
    assert d["registry_days"] == 0
    assert d["first_registry_date"] is None
    assert d["span_days"] == 0 and d["bars_1h"] == 0
    assert any("no recorded days yet" in line for line in sec["lines"])
    # The countdown still shows the targets — 0 recorded is a position, not a blank.
    target_days = _minbtl_years(20) * 365.0
    assert any(
        f"0 of {target_days:,.0f} days toward N=20 OOS candidacy (0.0%)" in line
        for line in sec["lines"]
    )


# --------------------------------------------------------------------------- #
# 3. INFO informs, never gates: roll-up rank ties OK and loses to WARN/FAIL.   #
# --------------------------------------------------------------------------- #
def test_info_verdict_never_outranks(tmp_path, capsys):
    assert ct._worst("INFO") == "INFO"
    assert ct._worst("OK", "INFO") == "OK"  # overall roll-up stays OK
    assert ct._worst("INFO", "WARN") == "WARN"
    assert ct._worst("INFO", "FAIL") == "FAIL"

    # End-to-end: a clean synthetic store overall-grades OK/WARN (never INFO),
    # with the readiness section present as section 6.
    root = tmp_path / "ticks"
    _seed_day(root, "2026-07-03")
    _write_registry(root, ["2026-07-03"])
    report = _run_json(root, capsys)
    assert report["overall"] in ("OK", "WARN")
    assert report["sections"][5]["name"] == "research readiness"


def test_okx_null_mark_index_exempt_but_other_venues_still_fail(tmp_path):
    """§0.7 no-invention: OKX funding rows carry NULL mark/index BY DESIGN and
    must not FAIL integrity; the same NULL on any other venue is still a bug.
    Regression for the 2026-07-21 live false-positive (1,360 okx cells)."""
    import importlib.util
    from pathlib import Path
    repo = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("check_ticks", repo / "scripts" / "check_ticks.py")
    ct = importlib.util.module_from_spec(spec); spec.loader.exec_module(ct)
    import duckdb
    from btcquant import collector

    db = tmp_path / "d.duckdb"
    con = collector.open_db(db)
    con.execute("INSERT INTO funding_mark VALUES ('okx','BTC-USDT-SWAP',1000,NULL,NULL,0.0001,2000)")
    con.execute("INSERT INTO funding_mark VALUES ('bybit','BTCUSDT',1000,50000.0,50010.0,0.0001,2000)")
    con.close()
    rep = ct.sec_integrity(duckdb.connect(str(db), read_only=True), {'funding_mark'}, 0)
    assert rep["data"]["bad_values"]["funding_mark"] == 0, "okx NULLs must be exempt"

    con = duckdb.connect(str(db))
    con.execute("INSERT INTO funding_mark VALUES ('bybit','BTCUSDT',3000,NULL,50010.0,0.0001,4000)")
    con.close()
    rep2 = ct.sec_integrity(duckdb.connect(str(db), read_only=True), {'funding_mark'}, 0)
    assert rep2["data"]["bad_values"]["funding_mark"] == 1, "non-okx NULL must still count"


# --------------------------------------------------------------------------- #
# M7 — vision mode (DESIGN §3d): L3 QA runs over the PUBLIC-ARCHIVE partition. #
#                                                                              #
# Guard-rail 4 is "the same gate, no exemption for being an archive", and      #
# guard-rail 3 is "readiness counts RECORDED days only". Both are asserted     #
# here against a partition written exactly the way scripts/ingest_vision.py    #
# writes one.                                                                  #
# --------------------------------------------------------------------------- #
def _seed_vision(root: Path, date: str, rows) -> Path:
    """One §3d archive partition: ``<root>/date=<date>/trades.parquet``."""
    part = root / f"date={date}"
    part.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    try:
        con.execute("CREATE TEMP TABLE t (exchange VARCHAR, symbol VARCHAR, "
                    "trade_id VARCHAR, ts_ms BIGINT, price DOUBLE, qty DOUBLE, "
                    "aggressor_buy BOOLEAN)")
        for r in rows:
            con.execute("INSERT INTO t VALUES (?,?,?,?,?,?,?)", list(r))
        dest = str(part / "trades.parquet").replace("'", "''")
        con.execute(f"COPY (SELECT * FROM t ORDER BY ts_ms) TO '{dest}' "
                    "(FORMAT PARQUET, COMPRESSION ZSTD)")
    finally:
        con.close()
    return part / "trades.parquet"


def _vision_rows(date: str, *, first_id: int, n: int = 20, step_s: int = 10,
                 skip=()):
    t0 = _ms(date, h=0)
    out = []
    for i in range(n):
        tid = first_id + i
        if tid in skip:
            continue
        out.append(("binancef", "BTCUSDT", str(tid), t0 + i * step_s * 1000,
                    60_000.0 + i, 0.5, i % 2 == 0))
    return out


def _run_vision(root: Path, capsys) -> dict:
    rc = ct.main(["--vision", str(root), "--json"])
    report = json.loads(capsys.readouterr().out)
    assert rc == report["exit_code"]
    return report


def _section(report: dict, name: str) -> dict:
    return next(s for s in report["sections"] if s["name"] == name)


def test_vision_mode_grades_a_clean_partition(tmp_path, capsys):
    root = tmp_path / "vision" / "binancef" / "BTCUSDT" / "aggTrades"
    _seed_vision(root, "2026-08-01", _vision_rows("2026-08-01", first_id=100))
    _seed_vision(root, "2026-08-02", _vision_rows("2026-08-02", first_id=120))
    report = _run_vision(root, capsys)
    assert report["mode"] == "vision"
    # WARN, not FAIL, and exit 0: this 40-row fixture has a 24 h silence between
    # its two days, which the coverage census correctly calls a hole. That is the
    # gate working — WARNs never fail it (§0.7: honest reporting must not go red).
    assert report["exit_code"] == 0
    assert _section(report, "integrity")["verdict"] == "OK"
    assert _section(report, "coverage")["verdict"] == "WARN"

    inv = _section(report, "inventory")
    assert inv["verdict"] == "OK"
    assert inv["data"]["tables"]["trades"]["rows"] == 40
    for t in ("depth_snapshots", "liquidations", "funding_mark", "open_interest"):
        assert inv["data"]["tables"][t]["absent_by_construction"] is True
    assert any("TRADES ONLY" in l for l in inv["lines"])
    assert any("ABSENT BY CONSTRUCTION" in l for l in inv["lines"])

    cont = _section(report, "id continuity (archive)")
    assert cont["data"]["per_pair"]["binancef/BTCUSDT"]["id_holes"] == 0
    assert cont["data"]["seams_checked"] == 1
    assert cont["data"]["seams_contiguous"] == 1


def test_vision_mode_fails_on_a_duplicate_trade_id(tmp_path, capsys):
    """Guard-rail 4: duplicates are corruption. Being an archive is no exemption."""
    root = tmp_path / "vision" / "binancef" / "BTCUSDT" / "aggTrades"
    rows = _vision_rows("2026-08-01", first_id=100, n=5)
    _seed_vision(root, "2026-08-01", rows + [rows[2]])
    report = _run_vision(root, capsys)
    assert report["exit_code"] == 1 and report["overall"] == "FAIL"
    integ = _section(report, "integrity")
    assert integ["verdict"] == "FAIL"
    assert integ["data"]["duplicate_trade_keys"] == 1
    assert integ["data"]["duplicate_surplus_rows"] == 1
    assert any("[FAIL] duplicate" in l for l in integ["lines"])


def test_vision_mode_reports_id_holes_and_seam_gaps_never_fills(tmp_path, capsys):
    """Gaps stay gaps — and here they are stated by the venue's own counter."""
    root = tmp_path / "vision" / "binancef" / "BTCUSDT" / "aggTrades"
    # 100..119 minus 105,106,107 -> 3 in-day holes.
    _seed_vision(root, "2026-08-01",
                 _vision_rows("2026-08-01", first_id=100, skip=(105, 106, 107)))
    # Next day starts at 200, not 120 -> a seam gap of 79.
    _seed_vision(root, "2026-08-02", _vision_rows("2026-08-02", first_id=200))
    report = _run_vision(root, capsys)
    cont = _section(report, "id continuity (archive)")
    assert cont["verdict"] == "WARN"            # reported, never failed, never filled
    pair = cont["data"]["per_pair"]["binancef/BTCUSDT"]
    assert pair["id_holes"] == 3 + 80           # 3 in-day + the 120..199 seam run
    assert pair["id_holes_in_day"] == 3
    assert pair["id_holes_across_adjacent_days"] == 80
    assert pair["ids_between_non_adjacent_days"] == 0
    assert cont["data"]["seams_checked"] == 1
    assert cont["data"]["seams_contiguous"] == 0
    assert cont["data"]["seams"][0]["gap"] == 200 - 119 - 1
    # WARN never fails the gate (§0.7 spirit: honest reporting must not go red).
    assert report["exit_code"] == 0
    # And nothing was invented to close them.
    assert _section(report, "inventory")["data"]["tables"]["trades"]["rows"] == 17 + 20


def test_a_skipped_day_is_a_range_choice_not_a_million_missing_ids(tmp_path, capsys):
    """The census is per DAY. Ids belonging to a day nobody ingested are not holes.

    Measured on the real tree this was 1,106,864 phantom "missing id(s)" — the
    exact row count of the un-ingested 2026-07-31 — reported as a WARN by the one
    census in this file that is EXACT rather than a silence heuristic. A census
    that cries wolf on a partial backfill (a `--max-days` run, an `absent` day, a
    resumed sync) teaches the operator to ignore it.
    """
    root = tmp_path / "vision" / "binancef" / "BTCUSDT" / "aggTrades"
    _seed_vision(root, "2026-08-01", _vision_rows("2026-08-01", first_id=100))
    # 08-02 is NOT ingested; 08-03 continues the counter where 08-02 would have
    # ended, so ids 120..199 belong to the day that was never asked for.
    _seed_vision(root, "2026-08-03", _vision_rows("2026-08-03", first_id=200))
    report = _run_vision(root, capsys)
    cont = _section(report, "id continuity (archive)")
    pair = cont["data"]["per_pair"]["binancef/BTCUSDT"]
    assert pair["id_holes"] == 0                     # nothing is missing
    assert pair["id_holes_in_day"] == 0
    assert pair["id_holes_across_adjacent_days"] == 0
    assert pair["ids_between_non_adjacent_days"] == 80
    assert cont["verdict"] == "OK"
    assert cont["data"]["seams_checked"] == 0        # non-adjacent: not a seam
    assert any("request choice" in l for l in cont["lines"])


def test_vision_mode_FAILS_a_partition_that_is_not_its_own_day(tmp_path, capsys):
    """A `date=D` partition holding foreign rows FAILs, like a duplicate id does.

    No other section can see it: the ids need not collide, the timestamps are
    internally consistent, and the union view drops the partition date. Yet
    `orderflow` resolves provenance per UTC day, so those rows would be read into
    another day's bars — a day that may be labelled RECORDED. The recorded side
    has had this gate all along (`upload_hf.stage_day`: "a day file IS its
    partition"); this is its archive-side twin.
    """
    root = tmp_path / "vision" / "binancef" / "BTCUSDT" / "aggTrades"
    _seed_vision(root, "2026-08-01",
                 _vision_rows("2026-08-01", first_id=100)
                 + _vision_rows("2026-08-02", first_id=500, n=3))
    _seed_vision(root, "2026-08-02", _vision_rows("2026-08-02", first_id=120))
    report = _run_vision(root, capsys)
    sec = _section(report, "partition containment (archive)")
    assert sec["verdict"] == "FAIL"
    assert report["overall"] == "FAIL" and report["exit_code"] == 1
    assert sec["data"]["partitions_checked"] == 2
    assert sec["data"]["partitions_failing"] == 1
    off = sec["data"]["offenders"][0]
    assert off["date"] == "2026-08-01" and off["rows_outside_own_day"] == 3
    assert any("A partition IS its day" in l for l in sec["lines"])
    # Nothing was moved or dropped to make it pass.
    assert _section(report, "inventory")["data"]["tables"]["trades"]["rows"] == 43


def test_a_clean_tree_passes_partition_containment(tmp_path, capsys):
    root = tmp_path / "vision" / "binancef" / "BTCUSDT" / "aggTrades"
    _seed_vision(root, "2026-08-01", _vision_rows("2026-08-01", first_id=100))
    _seed_vision(root, "2026-08-02", _vision_rows("2026-08-02", first_id=120))
    sec = _section(_run_vision(root, capsys), "partition containment (archive)")
    assert sec["verdict"] == "OK"
    assert sec["data"]["partitions_checked"] == 2 and sec["data"]["partitions_failing"] == 0


def test_vision_mode_refuses_to_print_a_readiness_number(tmp_path, capsys):
    """Guard-rail 3, in code: the MinBTL countdown has exactly one input."""
    root = tmp_path / "vision" / "binancef" / "BTCUSDT" / "aggTrades"
    _seed_vision(root, "2026-08-01", _vision_rows("2026-08-01", first_id=1))
    report = _run_vision(root, capsys)
    r = _readiness(report)
    assert r["verdict"] == "INFO"
    assert r["data"]["computed"] is False
    assert r["data"]["span_days"] is None
    assert r["data"]["minbtl"] == {}
    assert r["data"]["pct_toward_target"] is None
    joined = " ".join(r["lines"])
    assert "NOT computed over an archive partition" in joined
    assert "RECORDED days only" in joined
    # No percentage, no day count, nothing screenshot-able as a countdown.
    assert "%" not in joined.replace("100%", "")
    assert not any("toward" in l for l in r["lines"])


def test_vision_mode_does_not_claim_arrival_order_it_cannot_have(tmp_path, capsys):
    """The parquet is ts-sorted, so an inversion check would pass vacuously."""
    root = tmp_path / "vision" / "binancef" / "BTCUSDT" / "aggTrades"
    _seed_vision(root, "2026-08-01", _vision_rows("2026-08-01", first_id=1))
    report = _run_vision(root, capsys)
    integ = _section(report, "integrity")
    assert integ["data"]["ts_inversion_applicable"] is False
    assert integ["data"]["ts_inversions"] is None
    line = next(l for l in integ["lines"] if "non-monotonic ts" in l)
    assert line.startswith("[INFO]")
    assert "NOT APPLICABLE" in line and "vacuous" in line


def test_vision_mode_on_an_empty_tree_is_not_an_error(tmp_path, capsys):
    """Absence is a status, not corruption — the same §0.3 spirit as a young store."""
    root = tmp_path / "vision" / "binancef" / "BTCUSDT" / "aggTrades"
    assert ct.main(["--vision", str(root)]) == 0
    assert "no archive partition yet" in capsys.readouterr().out
    root.mkdir(parents=True)
    assert ct.main(["--vision", str(root)]) == 0
    assert "nothing to grade" in capsys.readouterr().out


def test_vision_relation_has_the_recorded_column_shape(tmp_path):
    """The hive reader synthesises a `date` column; the view must not expose it."""
    root = tmp_path / "vision" / "binancef" / "BTCUSDT" / "aggTrades"
    _seed_vision(root, "2026-08-01", _vision_rows("2026-08-01", first_id=1))
    con, parts, dates = ct.connect_vision_readonly(root)
    try:
        cols = [r[0] for r in con.execute("DESCRIBE SELECT * FROM trades").fetchall()]
    finally:
        con.close()
    assert cols == list(ct._VISION_TRADES_COLUMNS) + ["rowid"]
    assert "date" not in cols
    assert len(parts) == 1 and dates == ["2026-08-01"]
    assert tuple(ct._VISION_TRADES_COLUMNS) == collector._TABLE_COLUMNS["trades"]


def test_recorded_mode_readiness_is_unchanged_by_an_archive_tree(tmp_path, capsys):
    """Guard-rail 3, end to end: the readiness section must not move.

    A vision tree is planted right beside the recorded store and the readiness
    section is snapshotted before and after. Equal dicts, or the one honest clock
    in the project has a second input.
    """
    root = tmp_path / "ticks"
    _seed_day(root, "2026-07-03")
    _write_registry(root, ["2026-07-01", "2026-07-02", "2026-07-03"])
    before = _readiness(_run_json(root, capsys))["data"]

    vroot = tmp_path / "vision" / "binancef" / "BTCUSDT" / "aggTrades"
    for d in ("2019-12-31", "2020-01-01", "2026-08-01"):
        _seed_vision(vroot, d, _vision_rows(d, first_id=1))
    after = _readiness(_run_json(root, capsys))["data"]

    assert before == after
    assert after["span_days"] == 3            # three RECORDED days, not six years
    assert after["first_registry_date"] == "2026-07-01"
    # The archive tree wrote nothing into the recorded store's registry.
    assert list(root.glob("levels.jsonl")) != []
    assert json.loads((root / "levels.jsonl").read_text().splitlines()[0])["date"] \
        == "2026-07-01"
