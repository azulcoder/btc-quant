"""test_archive.py — archive pipeline tests (scripts/archive_ticks.py, DESIGN §3).

Fully deterministic, **no network, no gh**: every GitHub touch in the script is
factored behind small module-level functions, and an autouse fixture replaces the
single subprocess choke point (``_run_gh``) with a tripwire that fails the test if
anything ever tries to spawn the real CLI. Time is frozen by monkeypatching the
module's ``_now_ms`` — closed-month selection and the --partial hourly cutoff are
asserted against a fixed synthetic clock, not the wall.

The synthetic store is seeded through ``collector.open_db`` (the canonical schema)
across TWO closed months + a partial third, including the exact month-boundary
rows (last ms of April, first ms of May) that a sloppy [a,b] range would misfile.

Collector deps are opt-in (requirements-collector.txt): skips cleanly without duckdb.
"""

from __future__ import annotations

import importlib.util
import json
import socket
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

duckdb = pytest.importorskip("duckdb")  # opt-in dep — requirements-collector.txt

from btcquant import collector  # noqa: E402

# --------------------------------------------------------------------------- #
# Load scripts/archive_ticks.py as a module (scripts/ is not a package).       #
# --------------------------------------------------------------------------- #
_REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "archive_ticks", _REPO / "scripts" / "archive_ticks.py"
)
arch = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(arch)


# --------------------------------------------------------------------------- #
# Frozen clock + gh tripwire.                                                  #
# --------------------------------------------------------------------------- #
def ms(y, mo, d, h=0, mi=0, s=0, msec=0):
    return int(datetime(y, mo, d, h, mi, s, msec * 1000, tzinfo=timezone.utc).timestamp() * 1000)


NOW_MS = ms(2026, 6, 15, 12, 34, 56)  # "now": mid-June -> April+May closed, June partial
CUTOFF = ms(2026, 6, 15, 12)  # floor(now, hour) — the --partial export boundary
MS_H = 3_600_000

APR_LAST_MS = ms(2026, 5, 1) - 1  # boundary rows: last ms of April ...
MAY_FIRST_MS = ms(2026, 5, 1)  # ... and first ms of May


@pytest.fixture(autouse=True)
def _frozen_time_and_no_gh(monkeypatch):
    """Freeze the module clock; make ANY real gh spawn an immediate test failure."""
    monkeypatch.setattr(arch, "_now_ms", lambda: NOW_MS)

    def _no_gh(args):  # pragma: no cover — reaching this IS the failure
        raise AssertionError(f"tests must never spawn the real gh CLI (args={args})")

    monkeypatch.setattr(arch, "_run_gh", _no_gh)
    yield


# --------------------------------------------------------------------------- #
# Synthetic store: two closed months + a partial third, exact boundary rows.   #
# --------------------------------------------------------------------------- #
def _trade(ts, tid, price=60_000.0, qty=0.01):
    return ("bybit", "BTCUSDT", tid, ts, price, qty, True)


def _liq(ts, side="short", price=60_000.0, qty=0.5):
    return ("bybit", "BTCUSDT", ts, side, price, qty, price * qty)


TRADES_APR = [_trade(ms(2026, 4, 10, 12), "a1"), _trade(ms(2026, 4, 20, 8), "a2"),
              _trade(APR_LAST_MS, "a3")]
TRADES_MAY = [_trade(MAY_FIRST_MS, "m1"), _trade(ms(2026, 5, 15, 6), "m2"),
              _trade(ms(2026, 5, 31, 23, 59, 59, 999), "m3")]
TRADES_JUN_HEAD = [_trade(ms(2026, 6, 1), "j1"), _trade(ms(2026, 6, 15, 11), "j2")]  # < CUTOFF
TRADES_JUN_TAIL = [_trade(CUTOFF, "j3"), _trade(CUTOFF + 5 * 60_000, "j4")]  # >= CUTOFF
LIQS = [_liq(ms(2026, 4, 15)), _liq(ms(2026, 5, 10)), _liq(ms(2026, 6, 10))]


def make_store(tmp_path: Path, june_only: bool = False) -> tuple[Path, Path]:
    """Seed a store through collector.open_db (canonical schema). Returns (db, out)."""
    db = tmp_path / "ticks.duckdb"
    con = collector.open_db(db)
    trades = TRADES_JUN_HEAD + TRADES_JUN_TAIL
    liqs = [LIQS[2]]
    if not june_only:
        trades = TRADES_APR + TRADES_MAY + trades
        liqs = list(LIQS)
    con.executemany("INSERT INTO trades VALUES (?,?,?,?,?,?,?)", trades)
    con.executemany("INSERT INTO liquidations VALUES (?,?,?,?,?,?,?)", liqs)
    con.close()
    return db, tmp_path / "archive"


def _dead_port() -> int:
    """An ephemeral port with nothing listening — the REAL collector may be live
    on 8788 on this machine, and these tests must not depend on (or disturb) it."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _run(db, out, *extra):
    argv = ["--db", str(db), "--out", str(out), *extra]
    if "--api-check-port" not in extra:
        argv += ["--api-check-port", str(_dead_port())]
    return arch.main(argv)


def _parquet_stats(path: Path):
    con = duckdb.connect()
    try:
        return con.execute(
            "SELECT count(*), min(ts_ms), max(ts_ms) FROM read_parquet(?)", [str(path)]
        ).fetchone()
    finally:
        con.close()


# --------------------------------------------------------------------------- #
# 1. Closed-month default: exactly the two closed months, never the running one #
# --------------------------------------------------------------------------- #
def test_default_selects_exactly_the_two_closed_months(tmp_path):
    db, out = make_store(tmp_path)
    assert _run(db, out) == 0
    for month in ("2026-04", "2026-05"):
        assert (out / f"trades_{month}.parquet").exists()
        assert (out / f"liquidations_{month}.parquet").exists()
        assert (out / f"MANIFEST-ticks-{month}.json").exists()
    # The running month is untouched by the default (needs an explicit --partial).
    assert not list(out.glob("*2026-06*"))
    # Empty tables were skipped, not exported as empty files.
    assert not list(out.glob("depth_snapshots_*")) and not list(out.glob("funding_mark_*"))


def test_store_with_only_running_month_noops_with_message(tmp_path, capsys):
    db, out = make_store(tmp_path, june_only=True)
    assert _run(db, out) == 0
    assert "no CLOSED UTC month" in capsys.readouterr().out
    assert not out.exists() or not list(out.glob("*.parquet"))


# --------------------------------------------------------------------------- #
# 2+3. Export counts / ranges / sha match; boundary rows land correctly.       #
# --------------------------------------------------------------------------- #
def test_export_counts_ranges_sha_and_month_boundaries(tmp_path):
    db, out = make_store(tmp_path)
    assert _run(db, out) == 0

    # Boundary discipline: the [a, b) half-open range files the last-ms-of-April
    # row in April ONLY, and the first-ms-of-May row in May ONLY.
    n_apr, min_apr, max_apr = _parquet_stats(out / "trades_2026-04.parquet")
    n_may, min_may, max_may = _parquet_stats(out / "trades_2026-05.parquet")
    assert (n_apr, max_apr) == (len(TRADES_APR), APR_LAST_MS)
    assert (n_may, min_may) == (len(TRADES_MAY), MAY_FIRST_MS)
    assert min_apr == TRADES_APR[0][3] and max_may == TRADES_MAY[2][3]

    # Manifest entries: rows/ts range/bytes/sha256 all verifiable from disk.
    for month, expected_trades in (("2026-04", TRADES_APR), ("2026-05", TRADES_MAY)):
        manifest = json.loads((out / f"MANIFEST-ticks-{month}.json").read_text())
        assert manifest["tag"] == f"ticks-{month}"
        assert manifest["provenance"] == arch.PROVENANCE
        by_table = {e["table"]: e for e in manifest["entries"]}
        assert set(by_table) == {"trades", "liquidations"}
        e = by_table["trades"]
        f = out / e["file"]
        assert e["rows"] == len(expected_trades)
        assert e["partial"] is False
        a, b = arch.month_bounds(month)
        assert a <= e["ts_min"] <= e["ts_max"] < b
        assert e["bytes"] == f.stat().st_size
        assert e["sha256"] == arch.sha256_file(f)
        # A clean manifest re-verifies with zero problems.
        assert arch.verify_manifest_files(manifest, out) == []


# --------------------------------------------------------------------------- #
# 4. Overlap refusal against a pre-seeded manifest (double-archive guard).     #
# --------------------------------------------------------------------------- #
def test_overlap_with_preseeded_manifest_is_refused(tmp_path, capsys):
    db, out = make_store(tmp_path)
    out.mkdir(parents=True)
    (out / "MANIFEST-ticks-2026-04.json").write_text(json.dumps({
        "tag": "ticks-2026-04", "createdMs": 0, "tool": "archive_ticks.py",
        "db": str(db), "provenance": arch.PROVENANCE,
        "entries": [{
            "table": "trades", "file": "trades_2026-04.parquet", "month": "2026-04",
            "partial": False, "rows": 3, "ts_min": ms(2026, 4, 10, 12),
            "ts_max": APR_LAST_MS, "bytes": 1, "sha256": "0" * 64,
        }],
    }))
    assert _run(db, out, "--month", "2026-04") == 1
    err = capsys.readouterr().err
    assert "OVERLAP" in err and "trades_2026-04.parquet" in err
    # Refused BEFORE any export — no new file, store untouched.
    assert not (out / "trades_2026-04.parquet").exists()


# --------------------------------------------------------------------------- #
# 5. Prune requires a verified upload in the same run (usage error 64).        #
# --------------------------------------------------------------------------- #
def test_prune_without_upload_is_a_usage_error(tmp_path):
    db, out = make_store(tmp_path)
    assert _run(db, out, "--prune") == 64
    assert _run(db, out, "--prune", "--force-local-prune") == 64  # pick one
    assert _run(db, out, "--force-local-prune", "--upload") == 64  # force is the NO-upload path
    # And nothing was exported by the refused runs.
    assert not out.exists() or not list(out.glob("*.parquet"))


def test_running_month_without_partial_is_a_usage_error(tmp_path):
    db, out = make_store(tmp_path)
    assert _run(db, out, "--month", "2026-06") == 64


# --------------------------------------------------------------------------- #
# 6. --force-local-prune end-to-end: prune + verified rebuild on a tmp store.  #
# --------------------------------------------------------------------------- #
def test_force_local_prune_end_to_end(tmp_path):
    db, out = make_store(tmp_path)
    assert _run(db, out, "--force-local-prune", "--yes") == 0

    con = duckdb.connect(str(db), read_only=True)
    try:
        # Remaining rows are EXACTLY the un-exported tail (the running month).
        left = [r[0] for r in con.execute("SELECT trade_id FROM trades ORDER BY ts_ms").fetchall()]
        assert left == ["j1", "j2", "j3", "j4"]
        assert con.execute("SELECT count(*) FROM liquidations").fetchone()[0] == 1
        assert con.execute("SELECT min(ts_ms) FROM liquidations").fetchone()[0] == LIQS[2][2]
        # The rebuilt file carries the CANONICAL indexes (collector.open_db schema).
        idx = {r[0] for r in con.execute("SELECT index_name FROM duckdb_indexes()").fetchall()}
        assert {
            "idx_trades_symbol_ts", "idx_liquidations_symbol_ts", "idx_depth_symbol_ts",
            "idx_funding_symbol_ts", "idx_oi_symbol_ts",
        } <= idx
    finally:
        con.close()
    # Old file was replaced; backup deleted by default (disk-limited machine).
    assert not Path(str(db) + ".pre-archive.bak").exists()
    assert not Path(str(db) + ".rebuild").exists()
    # The archived rows still exist — as verified local parquet.
    assert _parquet_stats(out / "trades_2026-04.parquet")[0] == len(TRADES_APR)
    assert _parquet_stats(out / "trades_2026-05.parquet")[0] == len(TRADES_MAY)


def test_keep_backup_retains_pre_archive_bak(tmp_path):
    db, out = make_store(tmp_path)
    assert _run(db, out, "--force-local-prune", "--yes", "--keep-backup") == 0
    assert Path(str(db) + ".pre-archive.bak").exists()


# --------------------------------------------------------------------------- #
# 7. _pN suffix increments across successive --partial passes.                 #
# --------------------------------------------------------------------------- #
def test_partial_pN_suffix_increments(tmp_path, monkeypatch):
    db, out = make_store(tmp_path)
    # Pass 1: archive the running month up to CUTOFF, then prune it locally —
    # the store keeps only the tail rows at/after CUTOFF.
    assert _run(db, out, "--month", "2026-06", "--partial", "--force-local-prune", "--yes") == 0
    p1 = out / "trades_2026-06_p1.parquet"
    assert p1.exists()
    n1, min1, max1 = _parquet_stats(p1)
    assert n1 == len(TRADES_JUN_HEAD) and max1 < CUTOFF

    # Pass 2, two hours later: the old tail is now before the new cutoff; the
    # remainder exports beside (never on top of) _p1, and the pruned-away head
    # no longer overlaps because the overlap check grades actual data extents.
    monkeypatch.setattr(arch, "_now_ms", lambda: NOW_MS + 2 * MS_H)
    assert _run(db, out, "--month", "2026-06", "--partial") == 0
    p2 = out / "trades_2026-06_p2.parquet"
    assert p2.exists()
    n2, min2, _ = _parquet_stats(p2)
    assert n2 == len(TRADES_JUN_TAIL) and min2 == CUTOFF

    manifest = json.loads((out / "MANIFEST-ticks-2026-06.json").read_text())
    trade_files = sorted(e["file"] for e in manifest["entries"] if e["table"] == "trades")
    assert trade_files == ["trades_2026-06_p1.parquet", "trades_2026-06_p2.parquet"]
    assert all(e["partial"] is True for e in manifest["entries"] if e["table"] == "trades")


def test_next_partial_index_helper():
    entries = [
        {"table": "trades", "month": "2026-06", "partial": True, "file": "trades_2026-06_p1.parquet"},
        {"table": "trades", "month": "2026-06", "partial": True, "file": "trades_2026-06_p3.parquet"},
        {"table": "liquidations", "month": "2026-06", "partial": True,
         "file": "liquidations_2026-06_p9.parquet"},
    ]
    assert arch.next_partial_index(entries, "trades", "2026-06") == 4
    assert arch.next_partial_index(entries, "liquidations", "2026-06") == 10
    assert arch.next_partial_index(entries, "open_interest", "2026-06") == 1


# --------------------------------------------------------------------------- #
# 8. Collector-alive refusal: a live /health on the check port -> exit 2.      #
# --------------------------------------------------------------------------- #
def test_refuses_to_run_while_collector_answers_health(tmp_path, capsys):
    db, out = make_store(tmp_path)

    class _Health(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 — http.server API
            body = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):  # noqa: A002
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Health)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        rc = _run(db, out, "--api-check-port", str(srv.server_address[1]))
    finally:
        srv.shutdown()
        srv.server_close()
    assert rc == 2
    assert "stop the collector first" in capsys.readouterr().err
    assert not out.exists() or not list(out.glob("*.parquet"))  # nothing touched


# --------------------------------------------------------------------------- #
# 9. Manifest sha mismatch detection: corrupt one byte -> verify flags it.     #
# --------------------------------------------------------------------------- #
def test_verify_manifest_flags_corrupted_file(tmp_path):
    db, out = make_store(tmp_path)
    assert _run(db, out) == 0
    manifest = json.loads((out / "MANIFEST-ticks-2026-04.json").read_text())
    assert arch.verify_manifest_files(manifest, out) == []

    target = out / "trades_2026-04.parquet"
    blob = bytearray(target.read_bytes())
    blob[len(blob) // 2] ^= 0xFF  # flip one byte, same length — only the sha can catch it
    target.write_bytes(bytes(blob))

    problems = arch.verify_manifest_files(manifest, out)
    assert any("trades_2026-04.parquet" in p and "sha256 mismatch" in p for p in problems)


# --------------------------------------------------------------------------- #
# 10. Adversarial pass regressions — usage-error contract (exit 64, no        #
# tracebacks; exit 2 stays reserved for 'collector running').                 #
# --------------------------------------------------------------------------- #
def test_garbage_month_is_a_clean_usage_error(tmp_path, capsys):
    """--month 2026-13 used to escape as a raw ValueError traceback (exit 1);
    the contract says usage errors are exit 64 with a clean message."""
    db, out = make_store(tmp_path)
    for bad in ("2026-13", "2026-00", "garbage", "26-01"):
        assert _run(db, out, "--month", bad) == 64, bad
        err = capsys.readouterr().err
        assert "usage error" in err and bad in err
    assert not out.exists()  # refused before touching anything


def test_unknown_flag_exits_64_not_2(tmp_path, capsys):
    """argparse's default error exit is 2 — which this script reserves for
    'collector running'. A typo'd flag must never read as a live collector."""
    with pytest.raises(SystemExit) as ei:
        arch.main(["--no-such-flag"])
    assert ei.value.code == 64
    assert "usage error" in capsys.readouterr().err


def test_non_duckdb_db_file_aborts_cleanly(tmp_path, capsys):
    """A garbage --db path aborts with exit 1 + message, not a duckdb traceback."""
    junk = tmp_path / "not-a-store.duckdb"
    junk.write_text("this is not a duckdb file\n")
    assert _run(junk, tmp_path / "archive") == 1
    assert "cannot open" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# 11. Double-archive refusal end-to-end: a second identical run must refuse   #
# (the pre-seeded-manifest test above covers the unit; this covers the flow). #
# --------------------------------------------------------------------------- #
def test_second_identical_run_refuses_double_archive(tmp_path, capsys):
    db, out = make_store(tmp_path)
    assert _run(db, out) == 0
    before = sorted(f.name for f in out.iterdir())
    assert _run(db, out) == 1
    assert "OVERLAP" in capsys.readouterr().err
    assert sorted(f.name for f in out.iterdir()) == before  # nothing new written


def test_partial_then_more_rows_without_prune_refuses(tmp_path, monkeypatch, capsys):
    """--partial pass, collector adds rows, second --partial WITHOUT a prune in
    between: the already-archived head is still in the store, so the extents
    overlap _p1 and the run must refuse (double-archived rows corrupt a merge)."""
    db, out = make_store(tmp_path)
    assert _run(db, out, "--month", "2026-06", "--partial") == 0
    con = duckdb.connect(str(db))
    con.execute(
        "INSERT INTO trades VALUES (?,?,?,?,?,?,?)",
        _trade(NOW_MS + int(1.5 * MS_H), "j5"),
    )
    con.close()
    monkeypatch.setattr(arch, "_now_ms", lambda: NOW_MS + 2 * MS_H)
    capsys.readouterr()
    assert _run(db, out, "--month", "2026-06", "--partial") == 1
    err = capsys.readouterr().err
    assert "OVERLAP" in err and "trades_2026-06_p1.parquet" in err
    assert not (out / "trades_2026-06_p2.parquet").exists()


def test_full_month_after_unpruned_partial_refuses(tmp_path, monkeypatch, capsys):
    """Once the month closes, a FULL-month run must refuse while rows already
    covered by a _pN partial are still in the store."""
    db, out = make_store(tmp_path)
    assert _run(db, out, "--month", "2026-06", "--partial") == 0  # head -> _p1, no prune
    monkeypatch.setattr(arch, "_now_ms", lambda: ms(2026, 7, 15))  # June is now closed
    capsys.readouterr()
    assert _run(db, out, "--month", "2026-06") == 1
    assert "OVERLAP" in capsys.readouterr().err


def test_explicitly_requested_empty_month_is_honest_noop(tmp_path, capsys):
    db, out = make_store(tmp_path)
    assert _run(db, out, "--month", "2026-03") == 0
    assert "nothing to archive" in capsys.readouterr().out
    assert not (out / "MANIFEST-ticks-2026-03.json").exists()  # no empty manifest


# --------------------------------------------------------------------------- #
# 12. Swap-failure honesty: a crash on either rename must say EXACTLY what     #
# state the store is in and how to finish (was: a bare OSError traceback).     #
# --------------------------------------------------------------------------- #
def test_swap_failure_before_any_rename_reports_state(tmp_path, monkeypatch, capsys):
    db, out = make_store(tmp_path)

    def bomb(src, dst):
        raise OSError("simulated crash")

    monkeypatch.setattr(arch.os, "replace", bomb)
    assert _run(db, out, "--force-local-prune", "--yes") == 1
    err = capsys.readouterr().err
    assert "INTACT" in err and ".rebuild" in err and "mv" in err
    # The store is openable and holds exactly the post-delete rows; the verified
    # compacted copy is still on disk for a by-hand finish.
    con = duckdb.connect(str(db), read_only=True)
    try:
        left = [r[0] for r in con.execute("SELECT trade_id FROM trades ORDER BY ts_ms").fetchall()]
    finally:
        con.close()
    assert left == ["j1", "j2", "j3", "j4"]
    assert Path(str(db) + ".rebuild").exists()


def test_swap_failure_between_renames_reports_state(tmp_path, monkeypatch, capsys):
    db, out = make_store(tmp_path)
    real_replace = arch.os.replace
    state = {"n": 0}

    def bomb(src, dst):
        state["n"] += 1
        if state["n"] == 2:
            raise OSError("simulated crash between renames")
        return real_replace(src, dst)

    monkeypatch.setattr(arch.os, "replace", bomb)
    assert _run(db, out, "--force-local-prune", "--yes") == 1
    err = capsys.readouterr().err
    # The message must name both surviving files and give the finish command.
    assert "MISSING" in err and ".pre-archive.bak" in err and ".rebuild" in err and "mv" in err
    assert not db.exists()
    assert Path(str(db) + ".pre-archive.bak").exists()
    assert Path(str(db) + ".rebuild").exists()
    # Nothing lost: the verified compacted copy carries the full post-delete rows.
    con = duckdb.connect(str(db) + ".rebuild", read_only=True)
    try:
        assert con.execute("SELECT count(*) FROM trades").fetchone()[0] == 4
    finally:
        con.close()


# --------------------------------------------------------------------------- #
# 13. Stubbed-gh upload path (in-memory release): clobber discipline, byte     #
# verification gate, and the happy path that actually permits a prune.         #
# --------------------------------------------------------------------------- #
class _FakeGH:
    """In-memory GitHub release: {tag: {asset_name: bytes}}. `lie` shifts every
    reported asset size to simulate a corrupted/truncated upload."""

    def __init__(self, lie: int = 0):
        self.releases: dict[str, dict[str, int]] = {}
        self.uploads: list[tuple[str, list[str], bool]] = []
        self.lie = lie

    def install(self, monkeypatch):
        monkeypatch.setattr(arch, "gh_auth_ok", lambda: (True, ""))
        monkeypatch.setattr(arch, "detect_repo", lambda: ("owner", "btc-quant"))
        monkeypatch.setattr(arch, "gh_release_download_manifest", lambda tag, d: None)
        monkeypatch.setattr(arch, "gh_release_assets", self._assets)
        monkeypatch.setattr(arch, "gh_release_create", self._create)
        monkeypatch.setattr(arch, "gh_release_upload", self._upload)
        return self

    def _assets(self, tag):
        if tag not in self.releases:
            return None
        return [{"name": n, "size": s + self.lie} for n, s in self.releases[tag].items()]

    def _create(self, tag, title, notes):
        assert tag not in self.releases
        self.releases[tag] = {}

    def _upload(self, tag, files, clobber):
        self.uploads.append((tag, [Path(f).name for f in files], clobber))
        for f in files:
            p = Path(f)
            if p.name in self.releases[tag] and not clobber:
                raise arch.ArchiveAbort(f"asset {p.name} already exists (immutable, no clobber)")
            self.releases[tag][p.name] = p.stat().st_size


def test_upload_happy_path_verifies_then_prunes(tmp_path, monkeypatch):
    db, out = make_store(tmp_path)
    gh = _FakeGH().install(monkeypatch)
    assert _run(db, out, "--upload", "--prune", "--yes") == 0
    # Both closed months landed as releases with data + manifest assets.
    assert set(gh.releases) == {"ticks-2026-04", "ticks-2026-05"}
    assert {"trades_2026-04.parquet", "liquidations_2026-04.parquet",
            "MANIFEST-ticks-2026-04.json"} == set(gh.releases["ticks-2026-04"])
    # Clobber discipline: data assets uploaded WITHOUT clobber, manifest WITH.
    for tag, names, clobber in gh.uploads:
        is_manifest = names == [f"MANIFEST-{tag}.json"]
        assert clobber is is_manifest, (tag, names, clobber)
    # Prune actually ran: only the running month remains.
    con = duckdb.connect(str(db), read_only=True)
    try:
        left = [r[0] for r in con.execute("SELECT trade_id FROM trades ORDER BY ts_ms").fetchall()]
    finally:
        con.close()
    assert left == ["j1", "j2", "j3", "j4"]


def test_upload_size_mismatch_blocks_prune(tmp_path, monkeypatch, capsys):
    """Byte verification is the gate every prune stands behind: if the release
    reports a different size than the local file, nothing may be deleted."""
    db, out = make_store(tmp_path)
    _FakeGH(lie=-1).install(monkeypatch)  # release under-reports every asset by 1 byte
    assert _run(db, out, "--upload", "--prune", "--yes") == 1
    assert "upload verification FAILED" in capsys.readouterr().err
    con = duckdb.connect(str(db), read_only=True)
    try:  # store untouched — full row count incl. both closed months
        assert con.execute("SELECT count(*) FROM trades").fetchone()[0] == 10
        assert con.execute("SELECT count(*) FROM liquidations").fetchone()[0] == 3
    finally:
        con.close()


def test_remote_manifest_overlap_refused_under_upload(tmp_path, monkeypatch, capsys):
    """Under --upload the RELEASE manifest is the source of truth: rows already
    covered by an offsite entry are refused even with a clean local out dir."""
    db, out = make_store(tmp_path)
    gh = _FakeGH().install(monkeypatch)
    remote = {
        "tag": "ticks-2026-04", "createdMs": 0, "tool": "archive_ticks.py", "db": str(db),
        "provenance": arch.PROVENANCE,
        "entries": [{
            "table": "trades", "file": "trades_2026-04.parquet", "month": "2026-04",
            "partial": False, "rows": 3, "ts_min": ms(2026, 4, 1), "ts_max": APR_LAST_MS,
            "bytes": 1, "sha256": "0" * 64,
        }],
    }
    monkeypatch.setattr(
        arch, "gh_release_download_manifest",
        lambda tag, d: remote if tag == "ticks-2026-04" else None,
    )
    assert _run(db, out, "--month", "2026-04", "--upload") == 1
    assert "OVERLAP" in capsys.readouterr().err
    assert gh.uploads == []  # refused before anything left the machine


# --------------------------------------------------------------------------- #
# 14. Lock honesty: a second WRITER process on the store -> exit 2, same story #
# as the /health probe (single-writer contract, DESIGN §3).                    #
# --------------------------------------------------------------------------- #
def test_store_locked_by_second_writer_exits_2(tmp_path, capsys):
    import subprocess
    import sys as _sys

    db, out = make_store(tmp_path)
    holder = subprocess.Popen(
        [_sys.executable, "-c",
         "import duckdb, sys, time; con = duckdb.connect(sys.argv[1]); "
         "print('ready', flush=True); time.sleep(30)",
         str(db)],
        stdout=subprocess.PIPE, text=True,
    )
    try:
        assert holder.stdout.readline().strip() == "ready"
        rc = _run(db, out)
    finally:
        holder.kill()
        holder.wait()
    assert rc == 2
    assert "locked" in capsys.readouterr().err
    assert not out.exists()  # nothing was exported past the refusal
