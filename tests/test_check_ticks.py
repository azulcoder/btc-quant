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
