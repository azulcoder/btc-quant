"""test_upload_hf.py — HF day-file lifecycle tests (scripts/upload_hf.py, DESIGN §3c).

Fully deterministic, **no network, no Hugging Face**: every Hub touch in the
script goes through five seam functions (hf_ensure_repo / hf_list_files /
hf_upload_folder / hf_upload_file / hf_download_file), and an autouse fixture
replaces ALL of them with a tripwire that fails the test if anything reaches
for the real Hub — tests that want a Hub install the in-memory ``FakeHF``
over the tripwire. Time is frozen by monkeypatching the module's ``_now_ms``;
day-file mtimes are set explicitly with os.utime, so the closed-day rule
(mtime vs the grace end, writer-lock probe) is asserted against a fixed
synthetic clock, never the wall.

Synthetic day files are seeded through ``collector.open_db`` (the canonical
schema) — empty tables must be SKIPPED by the export, not shipped as empty
parquet. Collector deps are opt-in: skips cleanly without duckdb.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest

duckdb = pytest.importorskip("duckdb")  # opt-in dep — requirements-collector.txt

from btcquant import collector  # noqa: E402

# --------------------------------------------------------------------------- #
# Load scripts/upload_hf.py as a module (scripts/ is not a package).           #
# --------------------------------------------------------------------------- #
_REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("upload_hf", _REPO / "scripts" / "upload_hf.py")
uph = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(uph)


# --------------------------------------------------------------------------- #
# Frozen clock + Hub tripwire.                                                 #
# --------------------------------------------------------------------------- #
def ms(y, mo, d, h=0, mi=0, s=0):
    return int(datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc).timestamp() * 1000)


TODAY, YDAY, OLD2, OLD1 = "2026-07-05", "2026-07-04", "2026-07-03", "2026-07-02"
NOW_MS = ms(2026, 7, 5, 0, 20)  # 00:20 UTC — the scheduled slot, past the grace window
GRACE_END = ms(2026, 7, 5, 0) + uph.GRACE_CLOSE_MIN * 60_000  # 00:06 UTC today
REPO = "azulcoder/btc-quant-ticks"

_SEAMS = ("hf_ensure_repo", "hf_list_files", "hf_upload_folder", "hf_upload_file",
          "hf_download_file")


@pytest.fixture(autouse=True)
def _frozen_time_and_no_hub(monkeypatch):
    """Freeze the module clock; make ANY un-faked Hub seam an immediate failure."""
    monkeypatch.setattr(uph, "_now_ms", lambda: NOW_MS)
    for name in _SEAMS:
        def _boom(*a, _n=name, **kw):  # pragma: no cover — reaching this IS the failure
            raise AssertionError(f"tests must never touch the real Hub ({_n} called)")
        monkeypatch.setattr(uph, name, _boom)
    yield


# --------------------------------------------------------------------------- #
# In-memory fake Hub, recording every upload.                                  #
# --------------------------------------------------------------------------- #
class FakeHF:
    """{repo path: bytes}. ``size_lie`` shifts every reported size (a corrupted/
    truncated upload); ``expose_sha`` toggles the LFS-sha path vs the
    size+spot-check-download fallback."""

    def __init__(self, size_lie: int = 0, expose_sha: bool = True):
        self.store: dict[str, bytes] = {}
        self.size_lie = size_lie
        self.expose_sha = expose_sha
        self.exists = False
        self.uploads: list[str] = []  # path_in_repo of every uploaded file, in order
        self.downloads: list[str] = []

    def install(self, monkeypatch):
        monkeypatch.setattr(uph, "hf_ensure_repo", self._ensure)
        monkeypatch.setattr(uph, "hf_list_files", self._list)
        monkeypatch.setattr(uph, "hf_upload_folder", self._up_folder)
        monkeypatch.setattr(uph, "hf_upload_file", self._up_file)
        monkeypatch.setattr(uph, "hf_download_file", self._download)
        return self

    def seed(self, path: str, blob: bytes):
        self.store[path] = blob
        self.exists = True
        return self

    def _ensure(self, repo):
        created = not self.exists
        self.exists = True
        return created

    def _list(self, repo, prefix):
        import hashlib
        out = []
        for path, blob in sorted(self.store.items()):
            if not path.startswith(prefix):
                continue
            out.append({
                "path": path,
                "size": len(blob) + self.size_lie,
                "lfs_sha256": hashlib.sha256(blob).hexdigest() if self.expose_sha else None,
            })
        return out

    def _up_folder(self, repo, folder, path_in_repo, message):
        for f in sorted(Path(folder).rglob("*")):
            if f.is_file():
                rel = f.relative_to(folder).as_posix()
                self._up_file(repo, f, f"{path_in_repo}/{rel}", message)

    def _up_file(self, repo, local, path_in_repo, message):
        self.store[path_in_repo] = Path(local).read_bytes()
        self.uploads.append(path_in_repo)

    def _download(self, repo, path_in_repo, dest_dir):
        self.downloads.append(path_in_repo)
        dest = Path(dest_dir) / Path(path_in_repo).name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.store[path_in_repo])
        return dest


# --------------------------------------------------------------------------- #
# Synthetic day files (canonical schema via collector.open_db).                #
# --------------------------------------------------------------------------- #
def make_day(ticks_dir: Path, date: str, n_trades: int = 3, mtime_ms=None) -> Path:
    a, b = uph.day_bounds(date)
    db = ticks_dir / f"{date}.duckdb"
    ticks_dir.mkdir(parents=True, exist_ok=True)
    con = collector.open_db(db)
    rows = [("bybit", "BTCUSDT", f"{date}-{j}", a + 1000 * (j + 1), 60_000.0, 0.01, True)
            for j in range(n_trades - 1)]
    rows.append(("bybit", "BTCUSDT", f"{date}-last", b - 1, 60_100.0, 0.02, False))
    con.executemany("INSERT INTO trades VALUES (?,?,?,?,?,?,?)", rows)
    con.execute(
        "INSERT INTO liquidations VALUES ('bybit','BTCUSDT',?, 'short', 60000.0, 0.5, 30000.0)",
        [a + 5000],
    )
    con.close()
    if mtime_ms is not None:  # deterministic closed-day detection (never the wall)
        os.utime(db, (mtime_ms / 1000.0, mtime_ms / 1000.0))
    return db


def make_store(tmp_path: Path) -> tuple[Path, Path]:
    """Two old days + a closed yesterday + today; returns (ticks_dir, stage_dir)."""
    d = tmp_path / "ticks"
    for date in (OLD1, OLD2, YDAY, TODAY):
        make_day(d, date, mtime_ms=GRACE_END - 60_000)  # every mtime predates 00:06
    return d, tmp_path / "hf-stage"


def _run(ticks_dir, out, *extra):
    return uph.main(["--ticks-dir", str(ticks_dir), "--out", str(out), *extra])


@contextmanager
def hold_writer(db: Path):
    """Hold a REAL writer lock from a separate process (test_archive idiom):
    in-process, duckdb reports 'different configuration' instead of a lock
    conflict, which is not what a live collector looks like."""
    holder = subprocess.Popen(
        [sys.executable, "-c",
         "import duckdb, sys, time; con = duckdb.connect(sys.argv[1]); "
         "print('ready', flush=True); time.sleep(30)",
         str(db)],
        stdout=subprocess.PIPE, text=True,
    )
    try:
        assert holder.stdout.readline().strip() == "ready"
        yield
    finally:
        holder.kill()
        holder.wait()


def _parquet_stats(path: Path):
    con = duckdb.connect()
    try:
        return con.execute(
            "SELECT count(*), min(ts_ms), max(ts_ms) FROM read_parquet(?)", [str(path)]
        ).fetchone()
    finally:
        con.close()


# --------------------------------------------------------------------------- #
# 1. --dry-run: stage + verify + manifest only — no Hub seam, no deletes.      #
# --------------------------------------------------------------------------- #
def test_dry_run_stages_verifies_and_never_deletes(tmp_path, capsys):
    ticks, out = make_store(tmp_path)
    # The Hub tripwire stays armed: a dry-run that calls ANY seam fails here.
    assert _run(ticks, out, "--dry-run", "--yes") == 0

    # Non-empty tables staged; empty ones (depth/funding/oi) skipped, not shipped.
    for date in (OLD1, OLD2, YDAY):
        part = out / f"date={date}"
        assert sorted(p.name for p in part.glob("*.parquet")) == [
            "liquidations.parquet", "trades.parquet",
        ]
        n, tmin, tmax = _parquet_stats(part / "trades.parquet")
        a, b = uph.day_bounds(date)
        assert n == 3 and a <= tmin and tmax == b - 1  # event-time range inside the day
    assert not (out / f"date={TODAY}").exists()  # today is never a candidate

    # Every local day file survives a dry-run.
    for date in (OLD1, OLD2, YDAY, TODAY):
        assert (ticks / f"{date}.duckdb").exists()
    assert "dry-run" in capsys.readouterr().out


def test_manifest_content_matches_disk(tmp_path):
    ticks, out = make_store(tmp_path)
    assert _run(ticks, out, "--dry-run", "--yes") == 0
    manifest = json.loads((out / "manifests" / f"MANIFEST-{OLD1}.json").read_text())
    assert manifest["date"] == OLD1
    assert manifest["createdMs"] == NOW_MS
    assert manifest["provenance"] == uph.PROVENANCE
    assert "§3c" in manifest["provenance"] or "3c" in manifest["provenance"]
    by_table = {e["table"]: e for e in manifest["entries"]}
    assert set(by_table) == {"trades", "liquidations"}
    e = by_table["trades"]
    f = out / f"date={OLD1}" / "trades.parquet"
    a, b = uph.day_bounds(OLD1)
    assert e["rows"] == 3
    assert a <= e["ts_min"] <= e["ts_max"] < b
    assert e["bytes"] == f.stat().st_size
    assert e["sha256"] == uph.sha256_file(f)


# --------------------------------------------------------------------------- #
# 2. Closed-day selection: today + yesterday-in-grace excluded; yesterday      #
#    joins once closed (old mtime / no writer lock); a held lock skips it.     #
# --------------------------------------------------------------------------- #
def test_selection_excludes_today_and_in_grace_yesterday(tmp_path, monkeypatch):
    ticks, _ = make_store(tmp_path)
    # 00:03 UTC — inside the 5-min rotation grace (+1 min slack): yesterday is
    # NOT closed regardless of mtime/lock; today never is.
    monkeypatch.setattr(uph, "_now_ms", lambda: ms(2026, 7, 5, 0, 3))
    picked, notes = uph.select_days(ticks, ms(2026, 7, 5, 0, 3))
    assert picked == [OLD1, OLD2]
    assert any(YDAY in n and "grace" in n for n in notes)
    assert any(TODAY in n for n in notes)


def test_selection_includes_yesterday_once_closed_by_mtime(tmp_path):
    ticks, _ = make_store(tmp_path)  # every mtime predates the grace end
    picked, _ = uph.select_days(ticks, NOW_MS)
    assert picked == [OLD1, OLD2, YDAY]
    assert TODAY not in picked


def test_selection_includes_yesterday_via_no_writer_lock(tmp_path):
    ticks, _ = make_store(tmp_path)
    yday = ticks / f"{YDAY}.duckdb"
    late = GRACE_END + 60_000  # mtime AFTER the grace end -> falls to the lock probe
    os.utime(yday, (late / 1000.0, late / 1000.0))
    picked, _ = uph.select_days(ticks, NOW_MS)
    assert YDAY in picked  # no writer holds it -> closed


def test_selection_skips_yesterday_while_writer_holds_the_lock(tmp_path):
    ticks, _ = make_store(tmp_path)
    yday = ticks / f"{YDAY}.duckdb"
    late = GRACE_END + 60_000
    os.utime(yday, (late / 1000.0, late / 1000.0))
    with hold_writer(yday):
        picked, notes = uph.select_days(ticks, NOW_MS)
    assert YDAY not in picked and picked == [OLD1, OLD2]
    assert any(YDAY in n and "lock" in n for n in notes)


# --------------------------------------------------------------------------- #
# 3. Happy path: upload + Hub verify + delete ONLY after verify; yesterday is  #
#    uploaded but NEVER deleted (§3c keep-local window).                       #
# --------------------------------------------------------------------------- #
def test_happy_path_uploads_verifies_deletes_old_days_keeps_yesterday(tmp_path, monkeypatch, capsys):
    ticks, out = make_store(tmp_path)
    hub = FakeHF(expose_sha=True).install(monkeypatch)
    assert _run(ticks, out, "--yes") == 0

    # Every closed day landed as a partition + manifest on the (fake) Hub.
    for date in (OLD1, OLD2, YDAY):
        assert f"data/date={date}/trades.parquet" in hub.store
        assert f"data/date={date}/liquidations.parquet" in hub.store
        remote = json.loads(hub.store[f"manifests/MANIFEST-{date}.json"])
        assert remote["provenance"] == uph.PROVENANCE
    # Delete discipline: strictly-older-than-yesterday days died AFTER verify;
    # yesterday + today stay local for the BYOD API (§3c).
    assert not (ticks / f"{OLD1}.duckdb").exists()
    assert not (ticks / f"{OLD2}.duckdb").exists()
    assert (ticks / f"{YDAY}.duckdb").exists()
    assert (ticks / f"{TODAY}.duckdb").exists()
    out_txt = capsys.readouterr().out
    assert "freed" in out_txt and "kept local" in out_txt
    assert "hf://datasets/" in out_txt  # the query-back line
    # New repo -> the dataset card was written too.
    assert "README.md" in hub.store
    assert "gaps" in hub.store["README.md"].decode().lower()


def test_no_lfs_sha_triggers_spot_check_download(tmp_path, monkeypatch):
    ticks, out = make_store(tmp_path)
    hub = FakeHF(expose_sha=False).install(monkeypatch)  # sizes only, no sha on the API
    assert _run(ticks, out, "--yes") == 0
    # The verifier re-downloaded (at least) one data file per uploaded day.
    assert any(d.startswith("data/date=") for d in hub.downloads)
    assert not (ticks / f"{OLD1}.duckdb").exists()  # verify passed -> delete happened


def test_size_mismatch_on_hub_blocks_delete(tmp_path, monkeypatch, capsys):
    """The Hub reporting even ONE byte off must abort BEFORE any local delete."""
    ticks, out = make_store(tmp_path)
    FakeHF(size_lie=-1, expose_sha=False).install(monkeypatch)
    assert _run(ticks, out, "--yes") == 1
    assert "verification FAILED" in capsys.readouterr().err
    for date in (OLD1, OLD2, YDAY, TODAY):  # nothing deleted anywhere
        assert (ticks / f"{date}.duckdb").exists()


def test_delete_failure_after_verified_upload_is_honest_not_a_crash(tmp_path, monkeypatch, capsys):
    """A local delete failing AFTER the Hub verify (permissions, snapshot lock,
    ...) must not crash with a traceback or abort the remaining days: the data
    is verified offsite, only the cleanup failed. The run says exactly that per
    day, keeps syncing, and exits 1 so the scheduled job surfaces the state."""
    ticks, out = make_store(tmp_path)
    hub = FakeHF().install(monkeypatch)

    def _boom(day_file):
        raise OSError(13, "Permission denied", str(day_file))

    monkeypatch.setattr(uph, "_delete_day_file", _boom)
    assert _run(ticks, out, "--yes") == 1  # done-with-errors, not a crash
    io = capsys.readouterr()
    # Every day (incl. the ones whose delete failed) still made it to the Hub.
    for date in (OLD1, OLD2, YDAY):
        assert f"data/date={date}/trades.parquet" in hub.store
    # Nothing lost locally either — the failed deletes left the files in place.
    for date in (OLD1, OLD2, YDAY, TODAY):
        assert (ticks / f"{date}.duckdb").exists()
    assert io.out.count("local delete FAILED") == 2  # OLD1 + OLD2, per-day honesty
    assert "safe to re-run" in io.out
    assert "[done-with-errors]" in io.err and OLD1 in io.err and OLD2 in io.err


def test_keep_local_uploads_but_never_deletes(tmp_path, monkeypatch):
    ticks, out = make_store(tmp_path)
    hub = FakeHF().install(monkeypatch)
    assert _run(ticks, out, "--yes", "--keep-local") == 0
    assert f"data/date={OLD1}/trades.parquet" in hub.store
    for date in (OLD1, OLD2, YDAY, TODAY):
        assert (ticks / f"{date}.duckdb").exists()


# --------------------------------------------------------------------------- #
# 4. Immutability: an existing partition with DIFFERENT content is refused.    #
# --------------------------------------------------------------------------- #
def test_existing_partition_with_different_content_is_refused(tmp_path, monkeypatch, capsys):
    ticks, out = make_store(tmp_path)
    hub = FakeHF().install(monkeypatch)
    hub.seed(f"data/date={OLD1}/trades.parquet", b"someone else's bytes")  # no manifest
    assert _run(ticks, out, "--yes") == 1
    err = capsys.readouterr().err
    assert "IMMUTABLE" in err and OLD1 in err
    # Refused BEFORE any delete; nothing new for that date on the Hub either.
    assert (ticks / f"{OLD1}.duckdb").exists()
    assert f"manifests/MANIFEST-{OLD1}.json" not in hub.store


def test_existing_partition_with_matching_manifest_completes_lifecycle(tmp_path, monkeypatch, capsys):
    """The idempotent-cron case: yesterday was uploaded on D+1 and kept local;
    on D+2 it is strictly older than yesterday, the Hub manifest matches, so the
    run does NOT re-upload — it just deletes the now-eligible local file."""
    ticks, out = make_store(tmp_path)
    hub = FakeHF().install(monkeypatch)
    assert _run(ticks, out, "--yes") == 0
    assert (ticks / f"{YDAY}.duckdb").exists()
    uploads_before = list(hub.uploads)

    # One day later: YDAY has aged out of the keep-local window.
    monkeypatch.setattr(uph, "_now_ms", lambda: NOW_MS + 86_400_000)
    assert _run(ticks, out, "--yes") == 0
    # No re-upload of the already-archived partition...
    assert [u for u in hub.uploads if f"date={YDAY}" in u] == \
        [u for u in uploads_before if f"date={YDAY}" in u]
    # ...but the lifecycle completed: the local YDAY file is gone now.
    assert not (ticks / f"{YDAY}.duckdb").exists()
    assert "already on the Hub" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# 5. Skips + usage rails.                                                      #
# --------------------------------------------------------------------------- #
def test_locked_old_day_is_skipped_not_fought_over(tmp_path, monkeypatch, capsys):
    ticks, out = make_store(tmp_path)
    hub = FakeHF().install(monkeypatch)
    with hold_writer(ticks / f"{OLD1}.duckdb"):  # stray writer on an old day
        assert _run(ticks, out, "--yes") == 0
    assert "SKIPPED" in capsys.readouterr().out
    assert (ticks / f"{OLD1}.duckdb").exists()  # untouched
    assert f"data/date={OLD2}/trades.parquet" in hub.store  # the rest still synced


def test_bad_date_and_today_are_usage_errors(tmp_path, capsys):
    ticks, out = make_store(tmp_path)
    assert _run(ticks, out, "--date", "garbage") == 64
    assert "usage error" in capsys.readouterr().err
    assert _run(ticks, out, "--date", TODAY) == 64  # the writer still owns today
    assert "usage error" in capsys.readouterr().err
    for date in (OLD1, OLD2, YDAY, TODAY):
        assert (ticks / f"{date}.duckdb").exists()


def test_explicit_date_syncs_exactly_that_day(tmp_path, monkeypatch):
    ticks, out = make_store(tmp_path)
    hub = FakeHF().install(monkeypatch)
    assert _run(ticks, out, "--date", OLD1, "--yes") == 0
    assert f"data/date={OLD1}/trades.parquet" in hub.store
    assert f"data/date={OLD2}/trades.parquet" not in hub.store
    assert not (ticks / f"{OLD1}.duckdb").exists()
    assert (ticks / f"{OLD2}.duckdb").exists()


def test_empty_ticks_dir_is_an_honest_noop(tmp_path, capsys):
    d = tmp_path / "ticks"
    d.mkdir()
    assert _run(d, tmp_path / "stage") == 0
    assert "nothing to sync" in capsys.readouterr().out


def test_missing_duckdb_dep_is_actionable(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(uph, "duckdb", None)
    assert uph.main(["--ticks-dir", str(tmp_path)]) == 1
    assert "requirements-collector" in capsys.readouterr().err
