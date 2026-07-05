#!/usr/bin/env python3
"""archive_ticks.py — archive-then-prune data lifecycle for the tick store (DESIGN §3).

The collector (btcquant/collector.py) accumulates ``data/ticks.duckdb`` at roughly
2.4 GB/month on a machine with single-digit GiB free — the store outgrows the disk
by design. This script is how the research dataset SURVIVES that: closed UTC months
are exported per table to ZSTD parquet, checksummed + provenance-stamped into a
manifest, attached to a GitHub Release (tag ``ticks-YYYY-MM``), and only THEN — after
the upload is byte-verified — pruned from the live store, which is rebuilt to
actually reclaim the disk (DuckDB files never shrink in place).

Honesty rails (DESIGN §0, binding)
----------------------------------
* **Archive-then-prune, never prune-then-hope.** ``--prune`` refuses to run unless a
  verified upload happened in the SAME run. ``--force-local-prune`` exists for
  offline emergencies and shouts that the only copy is now local parquet.
* **Archives are immutable.** Data parquets are never clobbered on a release (only
  the manifest asset is re-uploadable); a range already covered by a manifest is
  refused outright — double-archived rows corrupt a later merge. Partial months are
  explicit (``--partial``) and suffixed ``_pN``.
* **A failed step never leaves the store worse than it started.** Exports are
  re-read and verified before anything else happens; the rebuild works on a side
  file and swaps in last; every abort path is exit-nonzero with nothing destroyed.
* **The archive window is an honest maintenance gap.** The collector must be
  stopped (this script refuses to run otherwise) and the downtime is a real hole in
  ``ts_ms`` — reported, never filled (§0.7).

Exit codes
----------
* 0  — done (including the legitimate "no closed months yet" no-op).
* 1  — a step failed/aborted; nothing destructive was done past a verified point.
* 2  — collector is running (HTTP probe answered, or DuckDB lock held).
* 64 — usage error (e.g. ``--prune`` without ``--upload``).

Usage
-----
    python3 scripts/archive_ticks.py                       # export closed months locally
    python3 scripts/archive_ticks.py --upload --prune      # the real lifecycle (make archive)
    python3 scripts/archive_ticks.py --month 2026-06 --partial
    python3 scripts/archive_ticks.py --list                # what is already offsite

Requires the opt-in collector deps (pip install -r requirements-collector.txt) and,
for --upload/--list, an authenticated ``gh`` CLI.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# --------------------------------------------------------------------------- #
# Guarded opt-in import (requirements-collector.txt) — same discipline as       #
# btcquant/collector.py and scripts/check_ticks.py: importing this file never   #
# explodes; the actionable hint fires only when the store is actually opened.   #
# --------------------------------------------------------------------------- #
try:  # optional dependency — see requirements-collector.txt
    import duckdb  # type: ignore
except Exception:  # noqa: BLE001 — any import failure means "store unavailable"
    duckdb = None  # type: ignore[assignment]

_INSTALL_HINT = "pip install -r requirements-collector.txt"

# Repo root on sys.path so the rebuild step can import btcquant.collector.open_db
# (the CANONICAL schema+indexes — restating DDL here would let the two drift).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Tables mirror collector._SCHEMA_DDL (DESIGN §3) — the whitelist every SQL string
# in this file draws table names from.
TABLES = ("trades", "liquidations", "depth_snapshots", "funding_mark", "open_interest")

# Provenance stamp baked into every manifest + release note (DESIGN §3: keyless,
# research-only; an archive without provenance is just a mystery blob).
PROVENANCE = "keyless public market data recorded by btcquant/collector.py (DESIGN §3)"

MS_PER_HOUR = 3_600_000

EXIT_OK, EXIT_FAIL, EXIT_LOCKED, EXIT_USAGE = 0, 1, 2, 64

_STOP_COLLECTOR_MSG = (
    "stop the collector first (launchctl unload "
    "~/Library/LaunchAgents/com.btcquant.collector.plist if installed, or Ctrl-C "
    "the `make collector` session); the archive window is an honest maintenance "
    "gap — keep it short"
)


class ArchiveAbort(Exception):
    """Abort the run with a message; nothing destructive has been done."""

    def __init__(self, msg: str, code: int = EXIT_FAIL) -> None:
        super().__init__(msg)
        self.code = code


# --------------------------------------------------------------------------- #
# Small pure helpers.                                                          #
# --------------------------------------------------------------------------- #
def _now_ms() -> int:
    """Wall clock, epoch ms. A function (not inline) so tests can freeze time."""
    return int(time.time() * 1000)


def _fmt_ts(ms: Optional[int]) -> str:
    if ms is None:
        return "n/a"
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fmt_bytes(n: int) -> str:
    size = float(n)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024.0 or unit == "GiB":
            return f"{size:,.1f} {unit}"
        size /= 1024.0
    return f"{size:,.1f} GiB"  # unreachable, keeps type-checkers calm


def month_bounds(month: str) -> tuple[int, int]:
    """'YYYY-MM' -> [start_ms, end_ms) in UTC. Raises ValueError on bad input."""
    m = re.fullmatch(r"(\d{4})-(\d{2})", month)
    if not m:
        raise ValueError(f"bad month {month!r} — expected YYYY-MM")
    y, mo = int(m.group(1)), int(m.group(2))
    if not 1 <= mo <= 12:
        raise ValueError(f"bad month {month!r} — month must be 01..12")
    start = datetime(y, mo, 1, tzinfo=timezone.utc)
    end = datetime(y + (mo == 12), mo % 12 + 1, 1, tzinfo=timezone.utc)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def month_of_ms(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime("%Y-%m")


def sha256_file(path: Path) -> str:
    """Streamed sha256 — archive parquets run to 100s of MB; never slurp them."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sq(s: Any) -> str:
    """Escape a string for embedding in a single-quoted SQL literal (COPY paths —
    DuckDB cannot bind parameters inside COPY; everything else uses ?-binding)."""
    return str(s).replace("'", "''")


def export_parquet_verified(
    con, table: str, dest: Path, a: int, b: int, src_stats: tuple
) -> tuple[int, Optional[int], Optional[int], int]:
    """Export-then-verify seam shared by BOTH lifecycles (this script's monthly
    GitHub-Release path and scripts/upload_hf.py's daily HF path — DESIGN §3c):
    COPY the ``[a, b)`` ts_ms range of ``table`` to ZSTD parquet at ``dest``, then
    RE-READ the written file and verify it against ``src_stats`` = (count, ts_min,
    ts_max) from the source query — exact row count, exact ts extent, and extent
    inside ``[a, b)``. Returns (rows, ts_min, ts_max, bytes).

    Raises ArchiveAbort on any mismatch; the bad file is KEPT for inspection and
    the caller prunes/deletes nothing (archive-then-prune creed, §0 rails).
    ``table`` must come from a caller-side whitelist/validated name — it is
    interpolated, not bound (COPY cannot take parameters).
    """
    con.execute(
        f"COPY (SELECT * FROM {table} "  # noqa: S608 — caller-whitelisted name
        f"WHERE ts_ms >= {int(a)} AND ts_ms < {int(b)} ORDER BY ts_ms) "
        f"TO '{_sq(dest)}' (FORMAT PARQUET, COMPRESSION ZSTD)"
    )
    n, tmin, tmax = con.execute(
        "SELECT count(*), min(ts_ms), max(ts_ms) FROM read_parquet(?)", [str(dest)]
    ).fetchone()
    src_n, src_min, src_max = src_stats
    if n != src_n or tmin != src_min or tmax != src_max or not (a <= tmin and tmax < b):
        raise ArchiveAbort(
            f"export verification FAILED for {dest}: parquet has {n} rows "
            f"[{_fmt_ts(tmin)}..{_fmt_ts(tmax)}], source had {src_n} "
            f"[{_fmt_ts(src_min)}..{_fmt_ts(src_max)}] in "
            f"[{_fmt_ts(a)}..{_fmt_ts(b)}) — file kept for inspection, "
            "nothing pruned/deleted"
        )
    return n, tmin, tmax, dest.stat().st_size


def next_partial_index(entries: list[dict], table: str, month: str) -> int:
    """N for the next ``_pN`` suffix = 1 + max existing partial index on the
    release/manifest for this table+month (data assets are immutable — a second
    partial pass must land beside, never on top of, the first)."""
    pat = re.compile(re.escape(f"{table}_{month}_p") + r"(\d+)\.parquet$")
    mx = 0
    for e in entries:
        if e.get("table") == table and e.get("month") == month and e.get("partial"):
            m = pat.search(e.get("file", ""))
            if m:
                mx = max(mx, int(m.group(1)))
    return mx + 1


def verify_manifest_files(manifest: dict, out_dir: Path) -> list[str]:
    """Re-verify every manifest entry against the files on disk.

    Returns a list of human-readable problems (empty == all good). Byte size AND
    streamed sha256 are both checked — a same-length corruption only trips the sha.
    """
    problems: list[str] = []
    for e in manifest.get("entries", []):
        f = out_dir / e["file"]
        if not f.exists():
            problems.append(f"{e['file']}: MISSING from {out_dir}")
            continue
        if f.stat().st_size != e["bytes"]:
            problems.append(
                f"{e['file']}: size {f.stat().st_size} != manifest {e['bytes']} bytes"
            )
        if sha256_file(f) != e["sha256"]:
            problems.append(f"{e['file']}: sha256 mismatch — file corrupt or tampered")
    return problems


# --------------------------------------------------------------------------- #
# Collector-liveness + store access (step 1 & 2 rails).                        #
# --------------------------------------------------------------------------- #
def collector_alive(port: int) -> bool:
    """Probe the BYOD /health endpoint (collector.py) — ANY answer means a live
    collector owns the store. 2 s timeout: this is loopback, not the internet."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2.0):
            return True
    except urllib.error.HTTPError:
        return True  # something answered, even if not 200 — the port is owned
    except Exception:  # noqa: BLE001 — refused/timeout == nothing listening
        return False


def connect_readonly(path: Path) -> "duckdb.DuckDBPyConnection":
    """Open the store read_only=True; a lock conflict means a collector WITHOUT
    the API is running (single-writer contract, DESIGN §3) — same exit-2 story
    as the HTTP probe, raised as ArchiveAbort for main() to translate. Any other
    open failure (corrupt/non-DuckDB file) aborts cleanly too — a traceback is
    not a verdict (check_ticks idiom)."""
    try:
        return duckdb.connect(str(path), read_only=True)
    except duckdb.Error as exc:
        if "lock" in str(exc).lower():
            raise ArchiveAbort(
                f"store is locked by a running process — {_STOP_COLLECTOR_MSG}",
                EXIT_LOCKED,
            ) from exc
        raise ArchiveAbort(f"cannot open {path} as a DuckDB store: {exc}") from exc


def closed_months_in_store(con, now_ms: int) -> tuple[list[str], list[str]]:
    """(closed months with data, ALL months with data), both sorted ascending.

    'Closed' = the month's end is at/before now (UTC). Derived from the data
    actually present, not from a min..max sweep — an empty middle month would
    only produce skip-noise and empty manifests."""
    months: set[str] = set()
    for table in TABLES:
        rows = con.execute(
            f"SELECT DISTINCT strftime(epoch_ms(ts_ms), '%Y-%m') FROM {table}"  # noqa: S608 — whitelist
        ).fetchall()
        months.update(r[0] for r in rows if r[0])
    ordered = sorted(months)
    closed = [m for m in ordered if month_bounds(m)[1] <= now_ms]
    return closed, ordered


# --------------------------------------------------------------------------- #
# gh CLI seam — every network/GitHub touch goes through these tiny functions   #
# so tests monkeypatch THEM and never spawn a real gh (no network in tests).   #
# --------------------------------------------------------------------------- #
def _run_gh(args: list[str]) -> "subprocess.CompletedProcess[str]":
    """The single choke point for gh. cwd=repo root so gh resolves the repo
    from the git remote — never a hardcoded owner/name."""
    return subprocess.run(
        ["gh", *args], capture_output=True, text=True, cwd=str(_REPO_ROOT)
    )


def detect_repo() -> Optional[tuple[str, str]]:
    """(owner, repo) from ``git remote get-url origin`` — no hardcode. None if
    there is no usable GitHub remote (an actionable abort for --upload)."""
    p = subprocess.run(
        ["git", "-C", str(_REPO_ROOT), "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
    )
    if p.returncode != 0:
        return None
    m = re.search(r"github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", p.stdout.strip())
    return (m.group(1), m.group(2)) if m else None


def gh_auth_ok() -> tuple[bool, str]:
    p = _run_gh(["auth", "status"])
    return p.returncode == 0, (p.stderr or p.stdout).strip()


def gh_release_assets(tag: str) -> Optional[list[dict]]:
    """[{name, size}, ...] for the release, or None if the release does not exist."""
    p = _run_gh(["release", "view", tag, "--json", "assets"])
    if p.returncode != 0:
        return None
    return json.loads(p.stdout).get("assets") or []


def gh_release_create(tag: str, title: str, notes: str) -> None:
    p = _run_gh(["release", "create", tag, "--title", title, "--notes", notes])
    if p.returncode != 0:
        raise ArchiveAbort(f"gh release create {tag} failed: {(p.stderr or p.stdout).strip()}")


def gh_release_upload(tag: str, files: list[str], clobber: bool) -> None:
    args = ["release", "upload", tag, *files]
    if clobber:
        args.append("--clobber")
    p = _run_gh(args)
    if p.returncode != 0:
        raise ArchiveAbort(f"gh release upload {tag} failed: {(p.stderr or p.stdout).strip()}")


def gh_release_download_manifest(tag: str, scratch_dir: Path) -> Optional[dict]:
    """Fetch MANIFEST-<tag>.json off the release (the release is the source of
    truth for what is already archived); None if release/asset absent."""
    scratch_dir.mkdir(parents=True, exist_ok=True)
    name = f"MANIFEST-{tag}.json"
    p = _run_gh(
        ["release", "download", tag, "--pattern", name, "--dir", str(scratch_dir), "--clobber"]
    )
    f = scratch_dir / name
    if p.returncode != 0 or not f.exists():
        return None
    return json.loads(f.read_text())


def gh_release_list() -> Optional[list[dict]]:
    p = _run_gh(["release", "list", "--json", "tagName,publishedAt"])
    if p.returncode != 0:
        print(f"ERROR: gh release list failed: {(p.stderr or p.stdout).strip()}", file=sys.stderr)
        return None
    return json.loads(p.stdout or "[]")


def cmd_list() -> int:
    """--list: ticks-* releases with per-asset sizes (what is already offsite)."""
    releases = gh_release_list()
    if releases is None:
        return EXIT_FAIL
    ticks = sorted(
        (r for r in releases if str(r.get("tagName", "")).startswith("ticks-")),
        key=lambda r: r["tagName"],
    )
    if not ticks:
        print("no ticks-* releases yet — nothing archived offsite so far")
        return EXIT_OK
    for r in ticks:
        assets = gh_release_assets(r["tagName"]) or []
        total = sum(int(a.get("size") or 0) for a in assets)
        print(f"{r['tagName']}  {len(assets)} assets  {_fmt_bytes(total)}  {r.get('publishedAt', '')}")
        for a in assets:
            print(f"    {a.get('name', '?'):<44} {_fmt_bytes(int(a.get('size') or 0))}")
    return EXIT_OK


# --------------------------------------------------------------------------- #
# Manifest handling (step 4). One manifest per tag == per month.               #
# --------------------------------------------------------------------------- #
def manifest_path_for(out_dir: Path, tag: str) -> Path:
    return out_dir / f"MANIFEST-{tag}.json"


def load_manifest(out_dir: Path, tag: str, use_remote: bool) -> dict:
    """Existing manifest for the tag: the RELEASE copy wins under --upload (it is
    the truth about what is already offsite); otherwise the local file from a
    prior local-only run; otherwise a fresh skeleton."""
    remote = gh_release_download_manifest(tag, out_dir / ".remote") if use_remote else None
    if remote is not None:
        return remote
    local = manifest_path_for(out_dir, tag)
    if local.exists():
        return json.loads(local.read_text())
    return {
        "tag": tag,
        "createdMs": _now_ms(),
        "tool": "archive_ticks.py",
        "db": "",
        "provenance": PROVENANCE,
        "entries": [],
    }


def find_overlap(entries: list[dict], table: str, src_min: int, src_max: int) -> Optional[dict]:
    """First already-archived entry whose DATA extent overlaps [src_min, src_max].

    WHY data extents rather than nominal month ranges: after a partial archive +
    local prune, the store legitimately holds only the month's un-archived TAIL —
    its data extent starts after the previous entry's ts_max, so the remainder
    passes while genuine double-archiving (rows still present) is refused."""
    for e in entries:
        if e.get("table") != table:
            continue
        if src_min <= int(e["ts_max"]) and int(e["ts_min"]) <= src_max:
            return e
    return None


# --------------------------------------------------------------------------- #
# Prune + rebuild (step 6). Work happens on a side file; swap is last.         #
# --------------------------------------------------------------------------- #
def prune_ranges(db: Path, jobs: list[dict]) -> dict[str, int]:
    """DELETE each exported [a,b) range, CHECKPOINT, return post-delete counts.
    READ-WRITE open is safe here: step 1 proved the collector is stopped."""
    con = duckdb.connect(str(db))
    try:
        for j in jobs:
            con.execute(
                f"DELETE FROM {j['table']} WHERE ts_ms >= ? AND ts_ms < ?",  # noqa: S608 — whitelist
                [j["a"], j["b"]],
            )
        con.execute("CHECKPOINT")
        return {
            t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]  # noqa: S608
            for t in TABLES
        }
    finally:
        con.close()


def rebuild_store(db: Path, expected: dict[str, int], keep_backup: bool, say) -> tuple[int, int]:
    """Rebuild the store to reclaim disk (DuckDB files do NOT shrink after DELETE).

    A fresh file is created via collector.open_db — the CANONICAL schema+indexes,
    imported not restated — the old file is ATTACHed read-only and copied table by
    table with count verification. The original db is untouched until the final
    os.replace swap; any failure before that leaves it exactly as the DELETE left
    it (rows gone, but every deleted row is in a verified archive)."""
    from btcquant import collector  # deferred: only the rebuild needs daemon code

    rebuild = Path(str(db) + ".rebuild")
    if rebuild.exists():
        rebuild.unlink()
    size_before = db.stat().st_size
    con = collector.open_db(rebuild)
    try:
        con.execute(f"ATTACH '{_sq(db)}' AS old (READ_ONLY)")
        for t in TABLES:
            con.execute(f"INSERT INTO {t} SELECT * FROM old.{t}")  # noqa: S608 — whitelist
            n = con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]  # noqa: S608
            if n != expected[t]:
                raise ArchiveAbort(
                    f"rebuild verification failed: {t} has {n} rows, expected {expected[t]} "
                    f"— {db} left exactly as the DELETE left it (every deleted row is in a "
                    "verified archive), .rebuild removed; re-run rebuild by hand or "
                    "investigate before retrying"
                )
        con.execute("DETACH old")
        con.execute("CHECKPOINT")
    except Exception:
        con.close()
        rebuild.unlink(missing_ok=True)
        raise
    con.close()

    # The swap: atomic-ish (two renames — a crash exactly between them leaves
    # .pre-archive.bak + the fully-verified .rebuild on disk, nothing lost).
    # Each rename gets its own honest failure message (§0: a failed step must say
    # EXACTLY what state the store is in and how to finish) — a bare traceback
    # here would hide which side of the swap the live file is on.
    bak = Path(str(db) + ".pre-archive.bak")
    try:
        os.replace(db, bak)
    except OSError as exc:
        raise ArchiveAbort(
            f"rebuild swap failed before anything moved ({exc}). State: {db} is INTACT "
            "and correct (rows already pruned; every pruned row is in a verified "
            f"archive) — only the disk-reclaim swap failed. The verified compacted copy "
            f"is at {rebuild}. Finish by hand: mv '{rebuild}' '{db}'  (or delete "
            f"'{rebuild}' to keep the un-compacted store). Nothing lost."
        ) from exc
    try:
        os.replace(rebuild, db)
    except OSError as exc:
        raise ArchiveAbort(
            f"rebuild swap failed BETWEEN renames ({exc}). State: {db} is MISSING right "
            f"now; the pre-swap store is at {bak}; the fully-verified compacted copy is "
            f"at {rebuild}. Nothing lost. Finish: mv '{rebuild}' '{db}'  (preferred — "
            f"reclaims disk), or restore: mv '{bak}' '{db}'. Delete the leftover file "
            "afterwards."
        ) from exc
    if keep_backup:
        say(f"backup kept: {bak} ({_fmt_bytes(bak.stat().st_size)}) — delete it yourself")
    else:
        bak.unlink()  # default: disk is the whole reason we are here
    return size_before, db.stat().st_size


# --------------------------------------------------------------------------- #
# CLI.                                                                         #
# --------------------------------------------------------------------------- #
class _Parser(argparse.ArgumentParser):
    """argparse exits 2 on a bad flag by default — but this script's exit-code
    contract reserves 2 for 'collector running' (a typo'd flag in a make/cron
    wrapper must never read as a live collector). Usage errors are 64."""

    def error(self, message: str):  # noqa: ANN201 — never returns (raises SystemExit)
        self.print_usage(sys.stderr)
        print(f"usage error: {message}", file=sys.stderr)
        raise SystemExit(EXIT_USAGE)


def _build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="archive_ticks.py",
        description=(
            "Archive closed months of the tick store to GitHub Releases, then "
            "(optionally) prune + rebuild to reclaim disk. Archive-then-prune: "
            "nothing is deleted before an offsite copy is verified. Exit 0 = done, "
            "1 = aborted, 2 = collector running, 64 = usage error."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--db", default="data/ticks.duckdb", help="DuckDB tick store path.")
    parser.add_argument("--out", default="data/archive", help="Local staging dir for parquet + manifests.")
    parser.add_argument(
        "--month",
        action="append",
        metavar="YYYY-MM",
        help="Month to archive (repeatable). Default: every CLOSED UTC month in the store.",
    )
    parser.add_argument(
        "--partial",
        action="store_true",
        help="Allow the current/incomplete month, up to the last full hour (assets get a _pN suffix).",
    )
    parser.add_argument(
        "--upload",
        action="store_true",
        help="Create/extend GitHub Release ticks-YYYY-MM via gh (repo auto-detected from git remote).",
    )
    parser.add_argument(
        "--prune",
        action="store_true",
        help="DELETE exported ranges + rebuild the store. REQUIRES a verified --upload in the same run.",
    )
    parser.add_argument(
        "--force-local-prune",
        action="store_true",
        help="DANGEROUS: prune after a local-only verified export, WITHOUT upload — the only copy is then local parquet.",
    )
    parser.add_argument(
        "--keep-backup",
        action="store_true",
        help="Keep ticks.duckdb.pre-archive.bak after the rebuild (default: delete — disk-limited).",
    )
    parser.add_argument("--list", action="store_true", help="List ticks-* releases (with sizes) and exit.")
    parser.add_argument(
        "--api-check-port",
        type=int,
        default=8788,
        metavar="PORT",
        help="Port probed for a live collector BYOD API before touching the store.",
    )
    parser.add_argument("--yes", action="store_true", help="Skip the interactive confirm before pruning.")
    return parser


def _confirm_prune(jobs: list[dict], force_local: bool, yes: bool) -> None:
    """Interactive gate before the only destructive step. Summarize exactly what
    dies; require a typed 'yes' unless --yes (non-tty without --yes = abort)."""
    total = sum(j["rows"] for j in jobs)
    print(f"    about to DELETE {total:,} rows across {len(jobs)} table-month range(s):")
    for j in jobs:
        print(
            f"      {j['table']:<16} {j['month']}{' (partial)' if j['partial'] else ''}: "
            f"{j['rows']:,} rows [{_fmt_ts(j['a'])} .. {_fmt_ts(j['b'])})"
        )
    if force_local:
        print(
            "\033[31m    WARNING (--force-local-prune): NO offsite copy exists — after this "
            "prune the ONLY copy of these rows is the local parquet. Upload it soon.\033[0m"
        )
    if yes:
        return
    if not sys.stdin.isatty():
        raise ArchiveAbort("refusing to prune without a tty confirm — pass --yes to override")
    if input("    type 'yes' to prune: ").strip().lower() != "yes":
        raise ArchiveAbort("prune not confirmed — nothing deleted")


def _upload_month(tag: str, month: str, entries: list[dict], manifest_file: Path, out_dir: Path) -> None:
    """Step 5 for one tag: create/extend the release, upload, byte-verify.

    Immutability rail: data parquets are NEVER clobbered — a name collision
    aborts (that is what _pN suffixes are for). The manifest is the ONLY
    clobberable asset (it legitimately grows as partials accumulate)."""
    ok, err = gh_auth_ok()
    if not ok:
        raise ArchiveAbort(f"gh is not authenticated — run `gh auth login` first ({err})")
    notes = (
        f"Immutable tick archive for {month}. {PROVENANCE}. "
        "Data assets are never overwritten; the manifest (sha256 per file) is the "
        "only asset that gets replaced as partial exports accumulate. "
        "Gaps in ts_ms are honest collector downtime — never interpolated."
    )
    assets = gh_release_assets(tag)
    if assets is None:
        gh_release_create(tag, f"tick archive {month}", notes)
        assets = []
    existing = {a.get("name") for a in assets}
    clash = [e["file"] for e in entries if e["file"] in existing]
    if clash:
        raise ArchiveAbort(
            f"data asset(s) already exist on release {tag}: {', '.join(clash)} — "
            "archives are immutable; use --partial (_pN suffixes) for incremental exports"
        )
    data_files = [str(out_dir / e["file"]) for e in entries]
    gh_release_upload(tag, data_files, clobber=False)
    gh_release_upload(tag, [str(manifest_file)], clobber=True)

    # VERIFY before anything may be pruned: every uploaded file present, exact bytes.
    after = {a.get("name"): int(a.get("size") or 0) for a in (gh_release_assets(tag) or [])}
    for e in entries:
        if after.get(e["file"]) != e["bytes"]:
            raise ArchiveAbort(
                f"upload verification FAILED for {e['file']} on {tag}: release has "
                f"{after.get(e['file'])!r} bytes, local file is {e['bytes']} — nothing pruned"
            )
    if manifest_file.name not in after:
        raise ArchiveAbort(f"upload verification FAILED: {manifest_file.name} missing from {tag}")


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.list:
        return cmd_list()

    # ---- usage rails (checked before anything runs; exit 64 = fix the flags) ----
    if args.prune and args.force_local_prune:
        print("usage error: pick ONE of --prune (with --upload) or --force-local-prune", file=sys.stderr)
        return EXIT_USAGE
    if args.prune and not args.upload:
        print(
            "usage error: --prune requires --upload (archive-then-prune: nothing is "
            "deleted before an offsite copy is verified). For a local-only prune you "
            "must spell out --force-local-prune.",
            file=sys.stderr,
        )
        return EXIT_USAGE
    if args.force_local_prune and args.upload:
        print("usage error: --force-local-prune is the NO-upload path; with --upload use --prune", file=sys.stderr)
        return EXIT_USAGE
    if args.month:
        # Validate format HERE (exit 64, clean message) — a garbage --month must
        # never surface as a ValueError traceback halfway through the run.
        try:
            for m in set(args.month):
                month_bounds(m)
        except ValueError as exc:
            print(f"usage error: {exc}", file=sys.stderr)
            return EXIT_USAGE
    if duckdb is None:
        print(f"ERROR: duckdb missing — {_INSTALL_HINT}", file=sys.stderr)
        return EXIT_FAIL

    db = Path(args.db)
    out_dir = Path(args.out)
    now_ms = _now_ms()
    # Cutoff captured ONCE, floored to the hour: every partial range in this run
    # shares one boundary — a run that straddles an hour tick stays consistent.
    cutoff_ms = now_ms - now_ms % MS_PER_HOUR

    try:
        # ---- step 1: refuse to run beside a live collector (single-writer, §3) ----
        if collector_alive(args.api_check_port):
            raise ArchiveAbort(
                f"collector is running (answered on 127.0.0.1:{args.api_check_port}/health) — "
                + _STOP_COLLECTOR_MSG,
                EXIT_LOCKED,
            )
        print(f"[1/7] collector check: nothing live on :{args.api_check_port} — proceeding")

        if not db.exists():
            # Absence is a status, not an error (check_ticks idiom).
            print(f"[2/7] no store at {db} — nothing to archive. (exit 0)")
            return EXIT_OK
        con = connect_readonly(db)  # lock conflict -> ArchiveAbort(EXIT_LOCKED)

        # ---- step 2: plan — months, ranges, manifests, overlap refusal ----
        try:
            closed, all_months = closed_months_in_store(con, now_ms)
            if args.month:
                months = sorted(set(args.month))  # format already validated (usage rails)
                not_closed = [m for m in months if month_bounds(m)[1] > cutoff_ms]
                if not_closed and not args.partial:
                    print(
                        f"usage error: month(s) {', '.join(not_closed)} are not closed yet — "
                        "add --partial to archive up to the last full hour",
                        file=sys.stderr,
                    )
                    return EXIT_USAGE
            else:
                months = list(closed)
                if args.partial:
                    cur = month_of_ms(now_ms)
                    if cur in all_months and cur not in months:
                        months.append(cur)
                if not months:
                    print(
                        "[2/7] no CLOSED UTC month in the store (it holds only the running "
                        "month) — nothing to archive yet. Use --partial to archive the "
                        "running month up to the last full hour. (exit 0)"
                    )
                    return EXIT_OK

            plans: list[dict] = []  # one per month: {month, tag, manifest, jobs}
            for month in months:
                m_start, m_end = month_bounds(month)
                b = min(m_end, cutoff_ms)
                if b <= m_start:
                    print(f"[2/7] {month}: cutoff {_fmt_ts(cutoff_ms)} is at/before month start — skipped")
                    continue
                partial = m_end > cutoff_ms
                tag = f"ticks-{month}"
                manifest = load_manifest(out_dir, tag, use_remote=args.upload)
                jobs: list[dict] = []
                for table in TABLES:
                    n, src_min, src_max = con.execute(
                        f"SELECT count(*), min(ts_ms), max(ts_ms) FROM {table} "  # noqa: S608 — whitelist
                        "WHERE ts_ms >= ? AND ts_ms < ?",
                        [m_start, b],
                    ).fetchone()
                    if not n:
                        continue  # skip-and-say happens in the export step print
                    hit = find_overlap(manifest["entries"], table, src_min, src_max)
                    if hit is not None:
                        raise ArchiveAbort(
                            f"{table} {month}: store rows [{_fmt_ts(src_min)} .. {_fmt_ts(src_max)}] "
                            f"OVERLAP already-archived {hit['file']} "
                            f"[{_fmt_ts(int(hit['ts_min']))} .. {_fmt_ts(int(hit['ts_max']))}] — "
                            "double-archived rows corrupt a later merge. Fix: prune the "
                            "already-archived range locally first, or archive only the "
                            "un-archived remainder."
                        )
                    pidx = next_partial_index(manifest["entries"], table, month) if partial else None
                    fname = (
                        f"{table}_{month}_p{pidx}.parquet" if partial else f"{table}_{month}.parquet"
                    )
                    jobs.append(
                        {
                            "table": table, "month": month, "tag": tag, "partial": partial,
                            "a": m_start, "b": b, "src_count": n,
                            "src_min": src_min, "src_max": src_max, "file": fname,
                        }
                    )
                plans.append({"month": month, "tag": tag, "manifest": manifest, "jobs": jobs})
            n_jobs = sum(len(p["jobs"]) for p in plans)
            print(
                f"[2/7] plan: months {', '.join(p['month'] for p in plans) or '(none)'} "
                f"-> {n_jobs} table-month export(s)"
                + (f" (partial cutoff {_fmt_ts(cutoff_ms)})" if args.partial else "")
            )
            if n_jobs == 0:
                print("[2/7] every candidate range is empty — nothing to archive. (exit 0)")
                return EXIT_OK

            # ---- step 3: export + re-read verification (nothing pruned on failure) ----
            out_dir.mkdir(parents=True, exist_ok=True)
            for p in plans:
                for j in p["jobs"]:
                    dest = out_dir / j["file"]
                    if dest.exists():
                        raise ArchiveAbort(
                            f"{dest} already exists locally — refusing to overwrite an archive "
                            "file (immutability rail); move it away or prune the manifest first"
                        )
                    n, tmin, tmax, n_bytes = export_parquet_verified(
                        con, j["table"], dest, j["a"], j["b"],
                        (j["src_count"], j["src_min"], j["src_max"]),
                    )
                    j["rows"] = n
                    j["ts_min"], j["ts_max"] = tmin, tmax
                    j["bytes"] = n_bytes
                    print(
                        f"[3/7] exported {j['file']}: {n:,} rows, {_fmt_bytes(j['bytes'])} "
                        f"(re-read verified{' — PARTIAL month' if j['partial'] else ''})"
                    )
        finally:
            con.close()  # read-only handle released before any read-write step

        # ---- step 4: write/merge one provenance-stamped manifest per tag ----
        for p in plans:
            if not p["jobs"]:
                continue
            for j in p["jobs"]:
                p["manifest"]["entries"].append(
                    {
                        "table": j["table"], "file": j["file"], "month": j["month"],
                        "partial": j["partial"], "rows": j["rows"],
                        "ts_min": j["ts_min"], "ts_max": j["ts_max"],
                        "bytes": j["bytes"], "sha256": sha256_file(out_dir / j["file"]),
                        # export range recorded verbatim — the prune step and any
                        # future merge tool need the *claimed* coverage, not just
                        # the data extent.
                        "range_start_ms": j["a"], "range_end_ms": j["b"],
                    }
                )
            p["manifest"]["db"] = str(db)
            p["manifest"]["updatedMs"] = _now_ms()
            mf = manifest_path_for(out_dir, p["tag"])
            mf.write_text(json.dumps(p["manifest"], indent=2) + "\n")
            print(f"[4/7] manifest {mf.name}: {len(p['manifest']['entries'])} entrie(s) total")

        # ---- step 5: upload + byte-verify (the gate every prune stands behind) ----
        uploaded = False
        if args.upload:
            repo = detect_repo()
            if repo is None:
                raise ArchiveAbort(
                    "cannot detect a GitHub repo from `git remote get-url origin` — "
                    "--upload needs one (no hardcoded owner/name here on purpose)"
                )
            for p in plans:
                if not p["jobs"]:
                    continue
                _upload_month(
                    p["tag"], p["month"], p["jobs"], manifest_path_for(out_dir, p["tag"]), out_dir
                )
                print(f"[5/7] release {p['tag']}: {len(p['jobs'])} asset(s) uploaded + byte-verified")
            uploaded = True
        else:
            print("[5/7] upload: skipped (no --upload) — export is LOCAL-ONLY, not offsite yet")

        # ---- step 6: prune + rebuild (only ever behind a verified copy) ----
        all_jobs = [j for p in plans for j in p["jobs"]]
        bytes_freed = 0
        if args.prune or args.force_local_prune:
            if args.prune and not uploaded:  # belt & braces — the usage rail above already gates this
                raise ArchiveAbort("--prune without a verified upload in this run", EXIT_USAGE)
            _confirm_prune(all_jobs, force_local=args.force_local_prune, yes=args.yes)
            post = prune_ranges(db, all_jobs)
            print(f"[6/7] pruned {sum(j['rows'] for j in all_jobs):,} rows; rebuilding to reclaim disk")
            size_before, size_after = rebuild_store(
                db, post, args.keep_backup, say=lambda m: print(f"[6/7] {m}")
            )
            bytes_freed = size_before - size_after
            print(
                f"[6/7] rebuilt {db.name}: {_fmt_bytes(size_before)} -> {_fmt_bytes(size_after)} "
                f"({_fmt_bytes(bytes_freed)} freed)"
            )
            if args.force_local_prune:
                print(
                    "\033[31m[6/7] REMINDER: the ONLY copy of the pruned rows is the local "
                    f"parquet under {out_dir} — nothing is offsite. Upload it soon.\033[0m"
                )
        else:
            print("[6/7] prune: skipped — store untouched")

        # ---- step 7: honest summary ----
        per_table: dict[str, int] = {}
        for j in all_jobs:
            per_table[j["table"]] = per_table.get(j["table"], 0) + j["rows"]
        parquet_bytes = sum(j["bytes"] for j in all_jobs)
        rows_txt = ", ".join(f"{t}={n:,}" for t, n in per_table.items())
        print(
            f"[7/7] summary: archived {rows_txt}; local parquet {_fmt_bytes(parquet_bytes)}; "
            f"store bytes freed {_fmt_bytes(bytes_freed)}"
        )
        if uploaded:
            repo = detect_repo()
            sample = next(
                (j for j in all_jobs if j["table"] == "trades"), all_jobs[0] if all_jobs else None
            )
            if repo and sample:
                owner, name = repo
                url = (
                    f"https://github.com/{owner}/{name}/releases/download/"
                    f"{sample['tag']}/{sample['file']}"
                )
                print("[7/7] archives stay queryable in place over HTTP, e.g.:")
                print(f"      SELECT count(*) FROM read_parquet('{url}')")
        elif all_jobs:
            print("[7/7] NOTE: no upload happened — these rows have NO offsite copy yet")
        return EXIT_OK

    except ArchiveAbort as exc:
        print(f"ABORT: {exc}", file=sys.stderr)
        return exc.code


if __name__ == "__main__":
    raise SystemExit(main())
