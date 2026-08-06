#!/usr/bin/env python3
"""check_ticks.py — L3 tick-store QA report card (DESIGN-orderflow-terminal.md §3).

The collector daemon (btcquant/collector.py) accumulates the future research dataset
in ``data/ticks.duckdb``. This script is the standing quality gate that keeps that
dataset *honest*: it opens the store READ-ONLY and grades what is actually in the
file — inventory, integrity, coverage/gaps, cross-venue coherence, liquidation
sanity — each section with an OK/WARN/FAIL verdict and the raw numbers behind it,
plus one INFO-only section (research readiness: the MinBTL countdown, § below).

Honesty rails (DESIGN §0, binding)
----------------------------------
* **Gaps stay gaps** (§0.7 / §3 resilience). This script REPORTS holes in the
  recorded series; it never fills, interpolates, or "repairs" anything. A QA gate
  that fixed data would be fabricating history.
* **WARNs do not fail the gate.** A young store legitimately has gaps (the
  collector was simply not running yet), WS out-of-order frames genuinely happen,
  and venues genuinely diverge for minutes at a time. Only *impossible* data —
  duplicate trade ids, NULL/NaN/non-positive prices, unknown liquidation sides —
  is corruption, and only corruption FAILs (exit 1).
* **Absence is not corruption.** A missing DB file means the collector has never
  run — that is a friendly note and exit 0, not an error. Same §0.3 spirit:
  un-ingested is a *status*, not a defect.
* **Read-only, single-writer respect** (§3): the daemon owns the write lock. If it
  holds the file we retry once (2 s — in case the daemon is mid-shutdown) and then
  exit 2 with instructions, instead of fighting for the lock.

Exit codes
----------
* 0 — all sections OK, or OK-with-WARNs (WARNs are listed in the footer), or no
      store file yet.
* 1 — at least one FAIL (real corruption), or the file exists but is unreadable.
* 2 — store locked by the running collector (stop it, or copy the file and point
      ``--db`` at the copy).

Scale limit (vision mode)
-------------------------
Full-archive dedup is NOT feasible on this machine, and pretending otherwise
would just be a query that dies on disk space hours in. The duplicate-trade_id
check is a GROUP BY over every archive row at once; at the audited scale —
2.83 B rows, ~2,052 ``date=`` partitions (``ls`` count 2026-08-06) — DuckDB
needs ~160 GB of aggregate/spill state against ~14 GB free on the only volume
this machine has (audit: docs/STATUS.md, "locally infeasible at full scale").
So grade the archive in month windows: ``--month YYYY-MM`` (repeatable)
restricts the partition scan, ``--temp-dir`` points DuckDB's spill somewhere
explicit (default ``.tmp`` — same volume, because there is no other volume),
and the memory limit is pinned to 4 GB so the spill is predictable instead of
an OOM. A month-window report says so in its output: it grades that month,
never the whole archive. No check's logic or threshold changes under these
flags — only how much data is in scope and where the working state lives.

Usage
-----
    python3 scripts/check_ticks.py                       # data/ticks.duckdb, 24 h
    python3 scripts/check_ticks.py --db /path/copy.duckdb --hours 6
    python3 scripts/check_ticks.py --db data/ticks       # DIRECTORY of day files (§3c
                                                         # rotation) — read-only union
    python3 scripts/check_ticks.py --vision data/vision/binancef/BTCUSDT/aggTrades \
        --month 2026-07                                  # archive QA, one month window
    python3 scripts/check_ticks.py --json | jq .overall  # machine output

Requires the opt-in collector deps:  pip install -r requirements-collector.txt
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# --------------------------------------------------------------------------- #
# Guarded opt-in import (requirements-collector.txt) — same discipline as       #
# btcquant/collector.py: importing this file never explodes; the actionable     #
# hint fires only when the store is actually opened.                            #
# --------------------------------------------------------------------------- #
try:  # optional dependency — see requirements-collector.txt
    import duckdb  # type: ignore
except Exception:  # noqa: BLE001 — any import failure means "store unavailable"
    duckdb = None  # type: ignore[assignment]

# The schema below is deliberately RESTATED (not imported from collector.py) so a
# copied store grades on a duckdb-only machine — but the MinBTL closed form is
# IMPORTED: two copies of the Bailey-LdP math drifting apart would be a
# methodology bug, and btcquant.risk owns the convention (returns YEARS under
# the annualized-Sharpe convention — see its docstring). Guarded the same way:
# without the core deps (scipy), sections 1–5 still run and §6 says so honestly.
try:  # core dependency — see requirements.txt
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from btcquant.risk import min_backtest_length  # Bailey et al. (2014), YEARS
except Exception:  # noqa: BLE001 — §6 reports the miss instead of exploding
    min_backtest_length = None  # type: ignore[assignment]

_INSTALL_HINT = "pip install -r requirements-collector.txt"
_INSTALL_HINT_CORE = "pip install -r requirements.txt"

# --------------------------------------------------------------------------- #
# Schema knowledge (mirrors collector._SCHEMA_DDL — DESIGN §3, verbatim).      #
# Deliberately restated here rather than imported: the QA gate must run        #
# against a *copied* file on a machine that has only duckdb installed, and a   #
# read-only checker importing daemon code would be a smell anyway.             #
# --------------------------------------------------------------------------- #
TABLES = ("trades", "liquidations", "depth_snapshots", "funding_mark", "open_interest")

# Columns that must be strictly positive, per table. NOTE what is *excluded*:
# funding_rate is legitimately negative (shorts pay), next_funding_ts/ts_ms are
# timestamps, and "index" needs quoting (SQL keyword — collector.py schema note).
_POSITIVE_COLS = {
    "trades": ("price", "qty"),
    "liquidations": ("price", "qty", "notional_usd"),
    "funding_mark": ("mark", "index"),
    "open_interest": ("oi",),
}

# --------------------------------------------------------------------------- #
# Thresholds (each one commented — a threshold without a WHY is a magic number) #
# --------------------------------------------------------------------------- #
# Trade inter-arrival gap: BTC perp prints subsecond-to-seconds around the clock
# (DESIGN §3 sizing: 0.5–1.5 M trades/day), so 30 s of tape silence is a feed
# hole (reconnect/backoff window), not a quiet market.
GAP_MS = 30_000

# Non-monotonic ts WARN rate: WS frames DO arrive out of order occasionally
# (reconnect replays, gateway races) — a trickle is wire reality, not corruption.
# Above 0.1% the adapter ordering assumption deserves a look, hence WARN not FAIL.
INVERSION_WARN_RATE = 0.001

# Cadence p95 vs expectation: the Downsampler targets 1 row/s (bybit funding_mark,
# both depth legs) and the Binance premiumIndex poll runs at 5 s (collector.py
# _PREMIUM_POLL_S). Event-loop jitter and flush batching stretch individual
# deltas; a p95 beyond 3x the target means a *sustained* stall, so WARN there.
EXPECTED_CADENCE_MS = {
    "funding_mark": {"bybit": 1_000.0, "binancef": 5_000.0},
    "depth_snapshots": {"bybit": 1_000.0, "binancef": 1_000.0},
}
CADENCE_WARN_MULT = 3.0

# Cross-venue mark divergence: bybit and binancef mark prices track the same
# underlying index; a p95 |Δmark|/mark above 50 bp over the window says one leg
# is stale/broken (or a genuine dislocation — either way a human should look).
MARK_DIVERGENCE_WARN_BP = 50.0

# Funding sign agreement: rates hover near zero and flip independently, so sign
# is a noisy statistic — only a sustained majority disagreement (<90%) is worth
# a WARN (it usually means one venue's funding leg stopped updating).
FUNDING_SIGN_AGREE_WARN = 0.90

# Research-readiness meter (section 6). MinBTL (btcquant.risk.min_backtest_length)
# returns YEARS under the annualized-Sharpe convention; the repo's 1h-bar year is
# 24*365 bars (compare._ppy — 24/7 market, no sessions/holidays), so years -> days
# of 1h bars is a flat *365 with no trading-day calendar to correct for.
DAYS_PER_YEAR = 365.0
# Trial counts for the countdown: 5 = the public board's scale (compare.SPOT_STRATS),
# 20 = a research library once variants/sweeps start inflating effective N
# (compare.RESEARCH_STRATS is already 8), 100 = a modest parameter sweep. MinBTL
# treats trials as independent, so every count here is a LOWER bound on effective N.
READINESS_TRIALS = (5, 20, 100)
READINESS_TARGET_N = 20  # the verdict line's yardstick

# Verdict ordering for section roll-ups. INFO ties OK on purpose: it marks a
# section that INFORMS but never gates (§6 readiness — a young store is
# time-gated, not defective), so it must never darken the overall verdict.
_VERDICT_RANK = {"INFO": 0, "OK": 0, "WARN": 1, "FAIL": 2}


def _worst(*verdicts: str) -> str:
    """Roll up per-check verdicts into a section verdict (FAIL > WARN > OK)."""
    return max(verdicts, key=lambda v: _VERDICT_RANK[v]) if verdicts else "OK"


def _fmt_ts(ms: Optional[int]) -> str:
    """Epoch-ms -> ISO UTC (the store is epoch-ms UTC throughout, DESIGN §3)."""
    if ms is None:
        return "n/a"
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fmt_bytes(n: int) -> str:
    """Human file size (binary units — matches what `ls -lh` shows the user)."""
    size = float(n)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024.0 or unit == "GiB":
            return f"{size:,.1f} {unit}"
        size /= 1024.0
    return f"{size:,.1f} GiB"  # unreachable, keeps type-checkers calm


def _json_safe(obj: Any) -> Any:
    """Replace NaN/inf with None recursively — json.dumps(NaN) is not valid JSON."""
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj


class StoreLocked(Exception):
    """Raised when the collector daemon holds the DuckDB write lock."""


# Day-rotation file names (DESIGN §3c: data/ticks/YYYY-MM-DD.duckdb).
_DAY_FILE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _store_size_bytes(path: Path, mode: str = "recorded") -> int:
    """Store size on disk: the file itself, (dir mode) every day file summed, or
    (vision mode) every parquet partition under the archive root summed."""
    if mode == "vision":
        return sum(f.stat().st_size for f in Path(path).rglob("*.parquet"))
    if path.is_dir():
        return sum(f.stat().st_size for f in path.glob("*.duckdb"))
    return path.stat().st_size


# --------------------------------------------------------------------------- #
# Vision mode (DESIGN §3d): grade the PUBLIC-ARCHIVE partition with the SAME    #
# code path as the recorded store. One gate definition, never two — the same    #
# reason GAP_MS is pinned across this file and btcquant/orderflow.py.           #
# --------------------------------------------------------------------------- #
#: ``collector._TABLE_COLUMNS["trades"]`` — restated, like TABLES above, so a
#: copied tree grades on a duckdb-only machine.
_VISION_TRADES_COLUMNS = (
    "exchange", "symbol", "trade_id", "ts_ms", "price", "qty", "aggressor_buy",
)
#: Hive partition dir under an archive family root.
_VISION_PART_RE = re.compile(r"date=(\d{4}-\d{2}-\d{2})$")

#: Vision-mode memory ceiling. WHY 4GB: the full-archive dedup GROUP BY needs
#: ~160 GB of aggregate state (docs/STATUS.md, "locally infeasible at full
#: scale") — no in-RAM budget survives that, so the cap's job is to make the
#: spill to --temp-dir start early and predictably instead of ending in an OOM.
#: It changes WHERE the working state lives, never what any check computes.
_VISION_MEMORY_LIMIT = "4GB"


def _apply_vision_resource_caps(con: "duckdb.DuckDBPyConnection", temp_dir: str) -> None:
    """SET an explicit temp_directory + memory_limit on the archive connection.

    An in-memory DuckDB has no database file to spill next to, so without an
    explicit ``temp_directory`` a big aggregate either balloons RSS or fails
    outright. The default ``.tmp`` is repo-local ON PURPOSE: this machine has a
    single volume, so "point the spill at a bigger disk" is not an option — the
    honest fix is month windows (``--month``), and these caps just make the
    within-window work bounded and observable.
    """
    tmp = Path(temp_dir)
    tmp.mkdir(parents=True, exist_ok=True)
    q = str(tmp.resolve()).replace(chr(39), chr(39) * 2)
    con.execute(f"SET temp_directory = '{q}'")
    con.execute(f"SET memory_limit = '{_VISION_MEMORY_LIMIT}'")


def connect_vision_readonly(
    root: Path,
    months: Optional[list[str]] = None,
) -> tuple["duckdb.DuckDBPyConnection", list[Path], list[str]]:
    """Read-only view over ``<root>/date=*/trades.parquet`` (DESIGN §3d tree).

    ``months`` (each ``YYYY-MM``) restricts the scan to ``date=`` partitions in
    those calendar months — the scale valve for the full-archive dedup that
    does not fit this machine (module docstring, "Scale limit"). ``None`` keeps
    the historical behaviour: every partition under the root.

    Returns ``(con, parquet_files, dates)``. Built as an explicit file list with
    an explicit column projection rather than a glob-with-``SELECT *``: the hive
    reader synthesises an extra ``date`` column that the recorded ``trades``
    table does not have, and a union view that silently changed shape by backend
    is exactly the class of drift this gate exists to catch.

    Each row carries the same synthetic ``rowid`` scheme dir mode uses
    (``partition_index * 2^40 + row_number``) so every existing query runs
    unchanged — but see :func:`sec_integrity`: in vision mode that rowid is
    **file order, not arrival order**, and the ts-inversion check says so instead
    of passing vacuously.
    """
    parts: list[tuple[str, Path]] = []
    for d in sorted(Path(root).glob("date=*")):
        m = _VISION_PART_RE.search(d.name)
        pq = d / "trades.parquet"
        if m and pq.exists():
            # date is YYYY-MM-DD, so [:7] is its calendar month.
            if months is not None and m.group(1)[:7] not in months:
                continue
            parts.append((m.group(1), pq))
    con = duckdb.connect()
    if not parts:
        return con, [], []
    proj = ", ".join(f'"{c}"' for c in _VISION_TRADES_COLUMNS)
    union = " UNION ALL ".join(
        f"SELECT {proj}, {i}::BIGINT * 1099511627776 + "
        f"(row_number() OVER () - 1) AS \"rowid\" "
        f"FROM read_parquet('{str(p).replace(chr(39), chr(39) * 2)}')"
        for i, (_, p) in enumerate(parts)
    )
    con.execute(f"CREATE VIEW trades AS {union}")  # noqa: S608 — validated paths
    return con, [p for _, p in parts], [d for d, _ in parts]


def connect_dir_readonly(
    day_files: list[Path],
) -> tuple["duckdb.DuckDBPyConnection", list[Path], list[Path]]:
    """Read-only UNION over per-day stores (DESIGN §3c rotation) so the whole
    existing report runs unchanged over the dataset the day files jointly hold.

    ATTACHes each day file READ_ONLY into an in-memory db, then CREATE VIEW
    <table> AS UNION ALL over every attached catalog that has that table
    (v1-migrated days may lack the v2 tables — a view unions what exists).
    Each view exposes a synthetic "rowid" column (day_index * 2^40 + rowid):
    sec_integrity orders by insertion via rowid, per-file rowid restarts at 0,
    and the offset preserves arrival order across day files. A day file locked
    by the live writer (today's open file) is SKIPPED, not fought over — dir
    mode's whole point is grading closed days WITHOUT stopping the collector.
    Returns (con, attached_files, skipped_locked_files).
    """
    con = duckdb.connect()  # in-memory shell; the day files stay read-only
    attached: list[tuple[str, Path]] = []
    skipped: list[Path] = []
    for i, f in enumerate(sorted(day_files)):
        alias = f"d{i}"
        try:
            con.execute(
                f"ATTACH '{str(f).replace(chr(39), chr(39) * 2)}' AS {alias} (READ_ONLY)"
            )
        except duckdb.Error as exc:
            if "lock" in str(exc).lower():
                skipped.append(f)  # the writer owns it — honest skip, not a fight
                continue
            raise
        attached.append((alias, f))

    # Which attached catalog has which table (information_schema spans catalogs).
    by_table: dict[str, list[str]] = {}
    for catalog, name in con.execute(
        "SELECT table_catalog, table_name FROM information_schema.tables "
        "WHERE table_schema = 'main'"
    ).fetchall():
        if catalog in {a for a, _ in attached}:
            by_table.setdefault(name, []).append(catalog)
    for name, catalogs in by_table.items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            continue  # never interpolate a weird identifier from a foreign file
        union = " UNION ALL ".join(
            # Offset = the catalog's attach index (dN == Nth day, files sorted by
            # date) so arrival order is day-then-rowid; 2^40 rows/day is
            # unreachable (~10^12 vs the store's ~10^6/day) — no collisions.
            f'SELECT *, {int(c[1:])}::BIGINT * 1099511627776 + rowid AS "rowid" FROM {c}.{name}'
            for c in sorted(catalogs, key=lambda a: int(a[1:]))
        )
        con.execute(f"CREATE VIEW {name} AS {union}")  # noqa: S608 — validated names
    return con, [f for _, f in attached], skipped


def connect_readonly(path: Path) -> "duckdb.DuckDBPyConnection":
    """Open the store read_only=True, retrying ONCE on a lock conflict.

    WHY the retry: the daemon holds an exclusive lock for its whole lifetime, so
    a lock conflict usually means "collector is running" — but if the user just
    Ctrl-C'd it, the final flush + close (collector.py shutdown rail) completes
    within a couple of seconds. One 2 s retry covers that race; beyond it we
    refuse to fight for the file (single-writer contract, DESIGN §3).
    """
    for attempt in (0, 1):
        try:
            return duckdb.connect(str(path), read_only=True)
        except duckdb.Error as exc:
            if "lock" not in str(exc).lower():
                raise  # not a lock problem — genuine open failure, caller FAILs
            if attempt == 0:
                time.sleep(2.0)
    raise StoreLocked


# --------------------------------------------------------------------------- #
# Section 1 — Inventory: what is in the file, and how fast is it growing?      #
# --------------------------------------------------------------------------- #
def sec_inventory(
    con,
    present: set,
    db_path: Path,
    hours: float,
    wall_ms: int,
    day_files: Optional[list[Path]] = None,
    skipped_locked: Optional[list[Path]] = None,
    mode: str = "recorded",
) -> tuple[dict, Optional[int]]:
    """Rows / exchanges / ts span per table, file size, projected GB/30d.

    Returns (section, anchor_ms). The anchor is the newest ts_ms anywhere in the
    store: every windowed check below is anchored there rather than at the wall
    clock, because the normal way to run this gate is against a STOPPED or copied
    store (read-only rail) — wall-clock windows would be empty and every check
    would degenerate to "no data". Staleness vs wall clock is reported honestly
    instead.
    """
    lines: list[str] = []
    verdicts: list[str] = []
    per_table: dict[str, dict] = {}
    anchor: Optional[int] = None
    total_rows = 0

    if mode == "vision":
        # The archive family being graded publishes ONE table. Saying so up front
        # is the difference between "absent by construction" and "empty", and
        # those are different facts (the §0.7 zero-vs-unknown rail, applied to a
        # whole table instead of a bar).
        lines.append(
            "[INFO] archive family aggTrades publishes TRADES ONLY — depth_snapshots / "
            "liquidations / funding_mark / open_interest are ABSENT BY CONSTRUCTION here, "
            "not empty. Every book-derived feature (OFI, weighted mid, depth-imbalance "
            "slope, walls) gains NOTHING from this partition."
        )

    for table in TABLES:
        if table not in present:
            if mode == "vision":
                # Absence is the documented shape of this partition, not damage.
                verdicts.append("INFO")
                lines.append(f"{table:<16} absent by construction (archive publishes trades only)")
                per_table[table] = {"present": False, "absent_by_construction": True}
                continue
            # A duckdb file without the collector schema is not a tick store —
            # that is corruption territory (wrong file), not a young store.
            verdicts.append("FAIL")
            lines.append(f"{table:<16} MISSING — is --db really the tick store?")
            per_table[table] = {"present": False}
            continue
        n, n_exch, ts_min, ts_max = con.execute(
            f"SELECT count(*), count(DISTINCT exchange), min(ts_ms), max(ts_ms) FROM {table}"
        ).fetchone()
        exchanges = [r[0] for r in con.execute(
            f"SELECT DISTINCT exchange FROM {table} ORDER BY 1"
        ).fetchall()]
        total_rows += n
        if ts_max is not None:
            anchor = ts_max if anchor is None else max(anchor, ts_max)
        per_table[table] = {
            "present": True, "rows": n, "exchanges": exchanges,
            "ts_min_ms": ts_min, "ts_max_ms": ts_max,
        }
        verdicts.append("OK")
        span = f"{_fmt_ts(ts_min)} .. {_fmt_ts(ts_max)}" if n else "(empty)"
        lines.append(
            f"{table:<16} {n:>10,} rows  exchanges={','.join(exchanges) or '-'}  {span}"
        )

    # File size + growth projection. bytes/row from the file as it stands is an
    # ESTIMATE (DuckDB compression + block overhead vary with table mix) — the
    # point is a sanity order-of-magnitude against DESIGN §3's honest sizing note
    # (~0.5–1 GB/month trades + ~0.1 GB/month depth), not an accounting number.
    size_b = _store_size_bytes(db_path, mode)
    window_start = (anchor if anchor is not None else wall_ms) - int(hours * 3_600_000)
    win_rows = 0
    for table in TABLES:
        if table in present:
            win_rows += con.execute(
                f"SELECT count(*) FROM {table} WHERE ts_ms >= ?", [window_start]
            ).fetchone()[0]
    rows_per_h = win_rows / hours if hours > 0 else 0.0
    bytes_per_row = (size_b / total_rows) if total_rows else 0.0
    proj_gb_30d = rows_per_h * 24 * 30 * bytes_per_row / 1e9
    lines.append(
        f"file {_fmt_bytes(size_b)}; last {hours:g}h ingest {rows_per_h:,.0f} rows/h "
        f"-> projected ~{proj_gb_30d:.2f} GB / 30d (estimate: {bytes_per_row:.0f} B/row)"
    )
    if anchor is not None:
        stale_min = (wall_ms - anchor) / 60_000.0
        lines.append(f"newest row {_fmt_ts(anchor)} ({stale_min:,.1f} min before wall clock)")
    else:
        lines.append("store is empty — schema present, no rows yet (young store: OK)")

    # Dir mode (§3c day rotation): one honest inventory line per day file, plus
    # the ones the live writer still holds — skipped, never fought over.
    day_data: Optional[dict] = None
    if day_files is not None:
        day_data = {"attached": [str(f) for f in day_files],
                    "skipped_locked": [str(f) for f in (skipped_locked or [])]}
        if mode == "vision":
            lines.append(
                f"partitions {len(day_files)} parquet file(s) under {db_path}"
            )
        for f in day_files if mode != "vision" else []:
            lines.append(f"day {f.name:<20} {_fmt_bytes(f.stat().st_size)}")
        for f in skipped_locked or []:
            lines.append(
                f"day {f.name:<20} SKIPPED — locked by the live writer (not graded this run)"
            )

    return (
        {
            "name": "inventory",
            "verdict": _worst(*verdicts),
            "lines": lines,
            "data": {
                "tables": per_table, "file_bytes": size_b, "total_rows": total_rows,
                "window_rows": win_rows, "rows_per_hour": rows_per_h,
                "projected_gb_30d": proj_gb_30d, "anchor_ms": anchor,
                "day_files": day_data,
            },
        },
        anchor,
    )


# --------------------------------------------------------------------------- #
# Section 2 — Integrity: things that must NEVER be true of honest data.        #
# --------------------------------------------------------------------------- #
def sec_integrity(con, present: set, window_start: int, mode: str = "recorded") -> dict:
    """Duplicate trade ids (FAIL), ts inversions (WARN), NULL/NaN/<=0 (FAIL).

    ``mode="vision"`` changes exactly one thing, and it changes it toward LESS
    confidence: the archive parquet is written ``ORDER BY ts_ms``, so DuckDB's
    rowid there is FILE order, not arrival order, and an inversion check over it
    would pass by construction while proving nothing. It reports
    ``[INFO] not applicable`` instead of ``[OK]``. The duplicate-``trade_id``
    FAIL is **unchanged** — being an archive earns no exemption.
    """
    lines: list[str] = []
    verdicts: list[str] = []
    data: dict[str, Any] = {}

    # (a) Duplicate (exchange, symbol, trade_id) — the collector never re-inserts
    # (no backfill, §0.7), and exchange trade ids are unique, so ANY duplicate
    # means double-ingest (two daemons on one file?) or replay damage: FAIL.
    if "trades" in present:
        dup_keys, surplus = con.execute(
            """SELECT count(*), coalesce(sum(c - 1), 0) FROM (
                   SELECT count(*) AS c FROM trades
                   GROUP BY exchange, symbol, trade_id HAVING count(*) > 1)"""
        ).fetchone()
        data["duplicate_trade_keys"] = dup_keys
        data["duplicate_surplus_rows"] = surplus
        v = "FAIL" if dup_keys > 0 else "OK"
        verdicts.append(v)
        lines.append(
            f"[{v}] duplicate (exchange,symbol,trade_id): {dup_keys} keys "
            f"({surplus} surplus rows) — FAIL if >0"
        )

        # (b) Non-monotonic ts per (exchange, symbol), sampled to the window.
        # rowid is DuckDB's insertion order — for this append-only single-writer
        # store that IS arrival order, so ts_ms < previous ts_ms flags a frame
        # that arrived out of order. WS out-of-order genuinely happens
        # (reconnect replays), so a small rate is wire reality: WARN >0.1%, not FAIL.
        if mode == "vision":
            data["ts_inversions"] = None
            data["ts_inversion_applicable"] = False
            verdicts.append("INFO")
            lines.append(
                "[INFO] non-monotonic ts: NOT APPLICABLE — the archive parquet is written "
                "ORDER BY ts_ms, so arrival order is not recoverable and a pass here would "
                "be vacuous. Reported as un-checkable rather than as OK."
            )
        else:
            inversions, steps = con.execute(
                """WITH recent AS (
                       SELECT exchange, symbol, ts_ms, rowid AS rid
                       FROM trades WHERE ts_ms >= ?),
                   scan AS (
                       SELECT ts_ms - lag(ts_ms) OVER (
                           PARTITION BY exchange, symbol ORDER BY rid) AS d
                       FROM recent)
                   SELECT count(*) FILTER (WHERE d < 0), count(d) FROM scan""",
                [window_start],
            ).fetchone()
            rate = (inversions / steps) if steps else 0.0
            data["ts_inversions"] = inversions
            data["ts_steps"] = steps
            data["ts_inversion_rate"] = rate
            v = "WARN" if rate > INVERSION_WARN_RATE else "OK"
            verdicts.append(v)
            lines.append(
                f"[{v}] non-monotonic ts (window, arrival order): {inversions}/{steps} "
                f"steps = {rate:.4%} — WARN if >{INVERSION_WARN_RATE:.1%}"
            )

    # (c) NULL / NaN / <=0 in value columns anywhere (whole store — corruption
    # does not expire with the window). The collector normalizers float() real
    # wire fields, so an impossible value can only be a bug or file damage: FAIL.
    #
    # DOCUMENTED EXEMPTION (§0.7 no-invention, collector v2): the OKX funding
    # endpoint carries NO mark/index price, so normalize_okx_funding stores
    # NULL there BY DESIGN rather than inventing one — those NULLs are honest
    # absence, not corruption, and must not FAIL the store. (funding_rate was
    # never in this positive-value check — it is legitimately negative; this
    # exemption changes nothing about it.) First seen live 2026-07-21: 1,360
    # flagged cells, all okx mark/index — a checker false-positive on the
    # repo's own documented design.
    bad_total = 0
    bad_detail: dict[str, int] = {}
    for table, cols in _POSITIVE_COLS.items():
        if table not in present:
            continue
        exempt = " AND exchange <> 'okx'" if table == "funding_mark" else ""
        checks = " + ".join(
            f'count(*) FILTER (WHERE ("{c}" IS NULL OR isnan("{c}") OR "{c}" <= 0){exempt})'
            for c in cols  # "index" is a keyword — quote every column uniformly
        )
        n_bad = con.execute(f"SELECT {checks} FROM {table}").fetchone()[0]
        bad_detail[table] = n_bad
        bad_total += n_bad
    data["bad_values"] = bad_detail
    data["bad_values_total"] = bad_total
    v = "FAIL" if bad_total > 0 else "OK"
    verdicts.append(v)
    detail = ", ".join(f"{t}={n}" for t, n in bad_detail.items())
    lines.append(f"[{v}] NULL/NaN/<=0 value columns: {bad_total} ({detail}) — FAIL if >0")

    return {"name": "integrity", "verdict": _worst(*verdicts), "lines": lines, "data": data}


# --------------------------------------------------------------------------- #
# Section 3 — Coverage/gaps. HONESTY RAIL: report, never fill (§0.7).          #
# --------------------------------------------------------------------------- #
def sec_coverage(con, present: set, window_start: int, anchor: int) -> dict:
    """Trade gaps >30s per exchange; funding/depth cadence p95 vs expectation.

    Gaps are REPORTED and left alone — the collector's own contract is that a
    reconnect window is a hole in ts_ms forever (collector.py §3 rail). A WARN
    here is a prompt to check uptime, not a defect in the file: a young store
    (or one that spans a collector restart) legitimately has gaps.
    """
    lines: list[str] = []
    verdicts: list[str] = []
    data: dict[str, Any] = {"trade_gaps": {}, "cadence": {}}

    # (a) Trade inter-arrival gaps per exchange, plus the tail gap up to the
    # window anchor (a trades leg that died while other streams kept flowing
    # shows up as a tail gap, invisible to inter-arrival alone). NOTE: binancef
    # trades are absent BY DESIGN (WS topic-filtered on this network, §0.2 —
    # documented, not proxied), so only exchanges present in the table are graded.
    if "trades" in present:
        rows = con.execute(
            """WITH t AS (SELECT exchange, ts_ms FROM trades WHERE ts_ms >= ?),
               d AS (SELECT exchange, ts_ms,
                            ts_ms - lag(ts_ms) OVER (
                                PARTITION BY exchange ORDER BY ts_ms) AS gap_ms
                     FROM t)
               SELECT exchange,
                      count(*) FILTER (WHERE gap_ms > ?),
                      coalesce(max(gap_ms), 0),
                      coalesce(sum(gap_ms) FILTER (WHERE gap_ms > ?), 0),
                      max(ts_ms)
               FROM d GROUP BY exchange ORDER BY exchange""",
            [window_start, GAP_MS, GAP_MS],
        ).fetchall()
        if not rows:
            verdicts.append("OK")
            lines.append("[OK] trades: no rows in window (young store / stopped leg — reported, not failed)")
        for exch, n_gaps, max_gap, total_gap, ts_last in rows:
            tail_gap = anchor - ts_last  # silence between last trade and newest store row
            gappy = n_gaps > 0 or tail_gap > GAP_MS
            v = "WARN" if gappy else "OK"
            verdicts.append(v)
            data["trade_gaps"][exch] = {
                "gaps_over_30s": n_gaps, "max_gap_ms": max_gap,
                "total_gap_ms_over_30s": total_gap, "tail_gap_ms": tail_gap,
            }
            lines.append(
                f"[{v}] trades/{exch}: {n_gaps} gaps >30s (largest {max_gap / 1000.0:,.1f}s, "
                f"total {total_gap / 1000.0:,.1f}s, tail {tail_gap / 1000.0:,.1f}s) — WARN if any"
            )

    # (b) Cadence p95 per exchange for funding_mark (~1s bybit downsample, ~5s
    # binancef poll) and depth_snapshots (~1s both). p95 over inter-arrival
    # deltas: robust to a single reconnect hole but catches sustained stalls.
    for table, expectations in EXPECTED_CADENCE_MS.items():
        if table not in present:
            continue
        rows = con.execute(
            f"""WITH t AS (SELECT exchange, ts_ms FROM {table} WHERE ts_ms >= ?),
               d AS (SELECT exchange, ts_ms - lag(ts_ms) OVER (
                             PARTITION BY exchange ORDER BY ts_ms) AS d_ms
                     FROM t)
               SELECT exchange, count(d_ms), quantile_cont(d_ms, 0.95)
               FROM d GROUP BY exchange ORDER BY exchange""",
            [window_start],
        ).fetchall()
        data["cadence"][table] = {}
        for exch, n_deltas, p95 in rows:
            expected = expectations.get(exch)
            entry = {"n_deltas": n_deltas, "p95_ms": p95, "expected_ms": expected}
            data["cadence"][table][exch] = entry
            if p95 is None:  # 0 or 1 rows in window — nothing to grade yet
                verdicts.append("OK")
                lines.append(f"[OK] {table}/{exch}: <2 rows in window — cadence n/a")
            elif expected is None:  # unexpected exchange code: report, don't guess
                verdicts.append("OK")
                lines.append(f"[OK] {table}/{exch}: p95 {p95:,.0f}ms (no expectation on file)")
            else:
                v = "WARN" if p95 > CADENCE_WARN_MULT * expected else "OK"
                verdicts.append(v)
                lines.append(
                    f"[{v}] {table}/{exch}: cadence p95 {p95:,.0f}ms vs ~{expected:,.0f}ms "
                    f"expected ({n_deltas:,} deltas) — WARN if >{CADENCE_WARN_MULT:g}x"
                )

    return {"name": "coverage", "verdict": _worst(*verdicts), "lines": lines, "data": data}


# --------------------------------------------------------------------------- #
# Section 4 — Cross-venue coherence: two independent feeds should agree.       #
# --------------------------------------------------------------------------- #
def sec_coherence(con, present: set, window_start: int) -> dict:
    """bybit vs binancef funding_mark joined by minute: |Δmark| bp, funding sign.

    Both venues mark against near-identical BTC indices, so a sustained mark
    divergence means one leg is stale/broken. Per-minute AVERAGES are joined
    (not last-tick) so bybit's 1/s stream and binance's 5 s poll compare on an
    equal footing. NaN/NULL marks are excluded here — flagging bad values is
    Section 2's job; coherence only grades the clean overlap.
    """
    lines: list[str] = []
    data: dict[str, Any] = {}
    if "funding_mark" not in present:
        return {"name": "coherence", "verdict": "OK", "lines": ["[OK] funding_mark table missing — graded in inventory"], "data": data}

    n_min, p50_bp, p95_bp, sign_agree = con.execute(
        """WITH m AS (
               SELECT exchange, ts_ms // 60000 AS minute,
                      avg(mark) AS mark, avg(funding_rate) AS fr
               FROM funding_mark
               WHERE ts_ms >= ? AND exchange IN ('bybit', 'binancef')
                 AND mark IS NOT NULL AND NOT isnan(mark)
               GROUP BY exchange, minute),
           j AS (
               SELECT b.mark AS bm, f.mark AS fm, b.fr AS bfr, f.fr AS ffr
               FROM m b JOIN m f ON b.minute = f.minute
               WHERE b.exchange = 'bybit' AND f.exchange = 'binancef')
           SELECT count(*),
                  quantile_cont(abs(bm - fm) / nullif((bm + fm) / 2.0, 0) * 1e4, 0.5),
                  quantile_cont(abs(bm - fm) / nullif((bm + fm) / 2.0, 0) * 1e4, 0.95),
                  avg(CASE WHEN sign(bfr) = sign(ffr) THEN 1.0 ELSE 0.0 END)
           FROM j""",
        [window_start],
    ).fetchone()
    data.update({
        "joined_minutes": n_min, "mark_diff_p50_bp": p50_bp,
        "mark_diff_p95_bp": p95_bp, "funding_sign_agreement": sign_agree,
    })

    verdicts: list[str] = []
    if not n_min:
        # One-venue (or empty) windows are a coverage story, not incoherence —
        # you cannot disagree with a feed that is not there.
        verdicts.append("OK")
        lines.append("[OK] no bybit x binancef overlap in window — coherence n/a")
    else:
        v = "WARN" if (p95_bp or 0.0) > MARK_DIVERGENCE_WARN_BP else "OK"
        verdicts.append(v)
        lines.append(
            f"[{v}] |Δmark|/mark over {n_min} joined minutes: p50 {p50_bp:,.1f} bp, "
            f"p95 {p95_bp:,.1f} bp — WARN if p95 >{MARK_DIVERGENCE_WARN_BP:g} bp"
        )
        # Sign is noisy near zero (rates flip independently around 0) — WARN only
        # on sustained majority disagreement, which usually means a stale leg.
        v = "WARN" if sign_agree is not None and sign_agree < FUNDING_SIGN_AGREE_WARN else "OK"
        verdicts.append(v)
        lines.append(
            f"[{v}] funding_rate sign agreement: {sign_agree:.1%} "
            f"— WARN if <{FUNDING_SIGN_AGREE_WARN:.0%} (noisy stat near 0)"
        )

    return {"name": "coherence", "verdict": _worst(*verdicts), "lines": lines, "data": data}


# --------------------------------------------------------------------------- #
# Section 5 — Liquidation sanity: domain checks on the sparse-but-precious set. #
# --------------------------------------------------------------------------- #
def sec_liquidations(con, present: set) -> dict:
    """side ∈ {long,short}, notional > 0, total count (whole table — liqs are
    sparse and the side normalization (§0.6: Bybit print 'Buy' == short liquidated)
    is exactly the kind of convention bug this gate exists to catch)."""
    lines: list[str] = []
    data: dict[str, Any] = {}
    if "liquidations" not in present:
        return {"name": "liquidations", "verdict": "OK", "lines": ["[OK] liquidations table missing — graded in inventory"], "data": data}

    total, bad_side, bad_notional = con.execute(
        """SELECT count(*),
                  count(*) FILTER (WHERE side IS NULL OR side NOT IN ('long', 'short')),
                  count(*) FILTER (WHERE notional_usd IS NULL OR isnan(notional_usd)
                                     OR notional_usd <= 0)
           FROM liquidations"""
    ).fetchone()
    data.update({"rows": total, "bad_side": bad_side, "bad_notional": bad_notional})
    v_side = "FAIL" if bad_side > 0 else "OK"
    v_notional = "FAIL" if bad_notional > 0 else "OK"
    lines.append(f"[{v_side}] side ∈ {{long,short}}: {bad_side} violations of {total:,} rows — FAIL if >0")
    lines.append(f"[{v_notional}] notional_usd > 0: {bad_notional} violations — FAIL if >0")
    return {
        "name": "liquidations", "verdict": _worst(v_side, v_notional),
        "lines": lines, "data": data,
    }


# --------------------------------------------------------------------------- #
# Section 5b — ID continuity (VISION ONLY): the census a clock cannot do.      #
# --------------------------------------------------------------------------- #
def sec_partition_containment(con, parquet_files: Optional[list[Path]]) -> dict:
    """A ``date=D`` partition holds day-D rows and nothing else. FAIL otherwise.

    Vision-only, and it grades at the same severity as the duplicate-``trade_id``
    check because it is the same class of fact: the whole per-UTC-day provenance
    design (DESIGN §0.7 rail a) assumes a partition IS its day. If it is not,
    ``orderflow`` unions those rows into ANOTHER day's bars — a day that may have
    resolved to the recorded store and is therefore labelled RECORDED. Archive
    rows becoming indistinguishable from recorded rows is the failure the whole
    item is designed against, and no other check in this file can see it: the
    ids need not collide, the timestamps are internally consistent, and the
    union view drops the partition date entirely.

    The recorded side has had this gate all along —
    ``upload_hf.stage_day`` aborts with "a day file IS its partition" before
    export. This is its archive-side twin, and it is a READ-side gate on purpose:
    the write-side gate (``ingest_vision`` G4a) cannot cover a tree that was
    copied, rsynced or half-written by an interrupted move, and the §3d tree is
    designed to be copied.

    Deliberately more expensive than the reader's version of the same check:
    ``orderflow`` bounds ``ts_ms`` with the parquet row-group ``min``/``max``
    (0.16 ms/file, statistics only — enough to REFUSE), while a QA card should
    say how many rows offend, which costs a scan of one column
    (measured 1.62 ms per day-file, ≈3.9 s over the full 2,406-day history).
    """
    lines: list[str] = []
    verdicts: list[str] = []
    data: dict[str, Any] = {"partitions": [], "offenders": []}
    for pq in parquet_files or []:
        m = _VISION_PART_RE.search(pq.parent.name)
        if not m:
            continue
        date = m.group(1)
        a = int(datetime.strptime(date, "%Y-%m-%d")
                .replace(tzinfo=timezone.utc).timestamp() * 1000)
        b = a + 86_400_000
        n, tmin, tmax, outside = con.execute(
            f"""SELECT count(*), min(ts_ms), max(ts_ms),
                       count(*) FILTER (WHERE ts_ms < {a} OR ts_ms >= {b})
                FROM read_parquet('{str(pq).replace(chr(39), chr(39) * 2)}')"""
        ).fetchone()
        entry = {"date": date, "path": str(pq), "rows": int(n),
                 "ts_min_ms": int(tmin) if tmin is not None else None,
                 "ts_max_ms": int(tmax) if tmax is not None else None,
                 "rows_outside_own_day": int(outside)}
        data["partitions"].append(entry)
        if outside:
            data["offenders"].append(entry)
            verdicts.append("FAIL")
            lines.append(
                f"[FAIL] date={date}: {outside:,} of {n:,} row(s) fall OUTSIDE their own "
                f"UTC day ({_fmt_ts(tmin)} .. {_fmt_ts(tmax)}). A partition IS its day — "
                "provenance is resolved per UTC day, so these rows would be read into "
                "ANOTHER day's bars, and that day may be labelled RECORDED. Re-ingest it "
                "(scripts/ingest_vision.py --force); nothing here is filled or moved."
            )
        else:
            verdicts.append("OK")
            lines.append(f"[OK] date={date}: {n:,} row(s), all inside the day the path claims")
    if not lines:
        lines.append("[OK] no partitions to check")
        verdicts.append("OK")
    data["partitions_checked"] = len(data["partitions"])
    data["partitions_failing"] = len(data["offenders"])
    return {"name": "partition containment (archive)", "verdict": _worst(*verdicts),
            "lines": lines, "data": data}


def sec_id_continuity(con, present: set, dates: Optional[list[str]] = None) -> dict:
    """Missing aggTradeIds inside each day, and day-to-day seams. REPORT, never fill.

    This is the strongest thing the archive partition makes possible and it is
    the whole reason M7 is admissible under DESIGN §0.7. Everywhere else in this
    file a hole is inferred from SILENCE (>30 s between prints — a heuristic that
    cannot distinguish a dead leg from a quiet market). Here the venue's own
    monotonic counter states it: if ids 5..7 never appear, three trades are
    missing, full stop. No threshold, no guess.

    So it is graded honestly in both directions: a hole is a WARN (it is real
    missing data and someone should look) and it is **never** filled, padded or
    interpolated. A seam mismatch across a day boundary is the same — reported,
    never patched, because patching would be inventing trades that the archive
    did not publish.
    """
    lines: list[str] = []
    verdicts: list[str] = []
    data: dict[str, Any] = {"per_pair": {}, "seams": []}
    if "trades" not in present:
        return {"name": "id continuity (archive)", "verdict": "OK",
                "lines": ["[OK] no trades table — nothing to grade"], "data": data}

    # The census is PER DAY and summed, never one span over the whole union.
    # An ingested range is a range the OPERATOR chose: a tree holding 07-30 and
    # 08-01 has a 1.1 M-id "gap" that is simply the un-ingested 07-31, and
    # calling that missing data reports a deliberate range choice as a defect —
    # the exact reasoning the seam block below already applies. Grading it wrong
    # is worse than not grading it: this is the one census in the file that is
    # EXACT rather than a silence heuristic, and a census that cries wolf on a
    # partial backfill teaches the operator to ignore it.
    rows = con.execute(
        f"""SELECT exchange, symbol, ts_ms // {86_400_000} AS d, count(*),
                   count(DISTINCT trade_id),
                   min(CAST(trade_id AS BIGINT)), max(CAST(trade_id AS BIGINT))
            FROM trades GROUP BY 1, 2, 3 ORDER BY 1, 2, 3"""
    ).fetchall()
    per_pair: dict[str, dict[str, Any]] = {}
    for ex, sym, d, n, ids, imin, imax in rows:
        key = f"{ex}/{sym}"
        e = per_pair.setdefault(key, {
            # `distinct_in_day`, not `distinct`: this sums per-day distinct
            # counts, so it says nothing about an id repeated across two days.
            # That case is graded where it belongs — the global duplicate FAIL in
            # sec_integrity — and a field that quietly meant two things would be
            # the kind of half-true number this census exists to replace.
            "rows": 0, "distinct_in_day": 0, "days": 0, "id_holes_in_day": 0,
            "per_day": [], "id_min": int(imin), "id_max": int(imax),
        })
        span_d = int(imax) - int(imin) + 1
        holes_d = span_d - int(n)
        e["rows"] += int(n)
        e["distinct_in_day"] += int(ids)
        e["days"] += 1
        e["id_holes_in_day"] += int(holes_d)
        e["id_min"] = min(e["id_min"], int(imin))
        e["id_max"] = max(e["id_max"], int(imax))
        date = datetime.fromtimestamp(int(d) * 86400, tz=timezone.utc).strftime("%Y-%m-%d")
        e["per_day"].append({"epoch_day": int(d), "date": date, "rows": int(n),
                             "id_min": int(imin), "id_max": int(imax),
                             "id_span": span_d, "id_holes": int(holes_d)})
    for key, e in per_pair.items():
        e["per_day"].sort(key=lambda p: p["epoch_day"])
        # Ids that fall BETWEEN two ingested days are only missing data when the
        # two days are calendar-ADJACENT — then the venue's counter really did
        # skip them. Between NON-adjacent days they are the days the operator did
        # not ask for, and counting those as missing reports a range choice as a
        # defect (a 2-day tree skipping one day used to report that day's whole
        # row count as "missing ids"). Same reasoning the seam block below
        # already applies; it just was not applied to the census.
        seam_missing = 0
        range_choice = 0
        for p, q in zip(e["per_day"], e["per_day"][1:]):
            # A NEGATIVE gap (the next day starting at or below this day's max)
            # is an overlap, not missing ids; it is not counted here and the seam
            # block below WARNs on it, since first_id(D) != last_id(D-1)+1.
            gap = q["id_min"] - p["id_max"] - 1
            if q["epoch_day"] == p["epoch_day"] + 1:
                seam_missing += max(0, gap)
            else:
                range_choice += max(0, gap)
        e["id_holes_across_adjacent_days"] = int(seam_missing)
        e["ids_between_non_adjacent_days"] = int(range_choice)
        # The one number to quote: genuinely missing ids, in-day + adjacent seams.
        e["id_holes"] = int(e["id_holes_in_day"] + seam_missing)
        e["id_span_overall"] = e["id_max"] - e["id_min"] + 1
        data["per_pair"][key] = e
        v = "WARN" if e["id_holes"] > 0 else "OK"
        verdicts.append(v)
        lines.append(
            f"[{v}] {key}: {e['rows']:,} rows over {e['days']:,} day(s) -> "
            f"{e['id_holes']:,} missing id(s) ({e['id_holes_in_day']:,} inside days, "
            f"{seam_missing:,} across adjacent days) — REPORTED, never filled"
        )
        for p in e["per_day"]:
            if p["id_holes"]:
                lines.append(
                    f"      {p['date']}: {p['rows']:,} rows, id span {p['id_span']:,} "
                    f"[{p['id_min']:,} .. {p['id_max']:,}] -> {p['id_holes']:,} missing"
                )
        if range_choice:
            lines.append(
                f"[INFO] {key}: {range_choice:,} further id(s) lie between NON-ADJACENT "
                "ingested days — that is the un-ingested range, a request choice, not "
                "missing data (overall id extent "
                f"[{e['id_min']:,} .. {e['id_max']:,}])"
            )

    # Seams: first id of each day vs last id of the day before it. Only adjacent
    # calendar days are compared — a deliberate hole in the ingested range is a
    # range choice, not a data defect, and calling it one would be noise.
    seam_rows = con.execute(
        f"""SELECT ts_ms // {86_400_000} AS d,
                   min(CAST(trade_id AS BIGINT)), max(CAST(trade_id AS BIGINT))
            FROM trades GROUP BY 1 ORDER BY 1"""
    ).fetchall()
    checked = contiguous = 0
    for (d0, _mn0, mx0), (d1, mn1, _mx1) in zip(seam_rows, seam_rows[1:]):
        if int(d1) != int(d0) + 1:
            continue  # non-adjacent days: not a seam, just a range the user chose
        checked += 1
        ok = int(mn1) == int(mx0) + 1
        contiguous += int(ok)
        if not ok:
            date = datetime.fromtimestamp(int(d1) * 86400, tz=timezone.utc).strftime("%Y-%m-%d")
            data["seams"].append({"date": date, "prev_id_max": int(mx0),
                                  "first_id": int(mn1), "gap": int(mn1) - int(mx0) - 1})
    v = "WARN" if checked and contiguous < checked else "OK"
    verdicts.append(v)
    lines.append(
        f"[{v}] cross-day seams: {contiguous}/{checked} contiguous "
        "(first_id(D) == last_id(D-1)+1) — REPORTED, never patched"
    )
    data["seams_checked"] = checked
    data["seams_contiguous"] = contiguous
    return {"name": "id continuity (archive)", "verdict": _worst(*verdicts),
            "lines": lines, "data": data}


# --------------------------------------------------------------------------- #
# Section 6 — Research readiness: the honest MinBTL countdown. INFO ONLY.      #
# --------------------------------------------------------------------------- #
def sec_readiness(
    db_path: Path,
    day_files: Optional[list[Path]],
    skipped_locked: Optional[list[Path]],
    anchor_ms: Optional[int],
    mode: str = "recorded",
) -> dict:
    """How far the recorded history is from MinBTL for N in {5, 20, 100} trials.

    Clearing MinBTL is NECESSARY, not sufficient — a pre-registered hypothesis
    + kill criterion is still required (DEVELOPMENT §6). This section is a
    countdown, never a greenlight.

    INFO only, never WARN/FAIL, by design: a young store is a *status*, not a
    defect (§0.3 spirit) — the order-flow research families are time-gated, NOT
    validated (DESIGN §6 "time-gated, not granted"; §3c accumulates the gate's
    clock). Being short of MinBTL is a fact about elapsed calendar time; grading
    it would punish the store for existing recently.

    Recorded-day counting is OFFLINE on purpose (this gate never touches the
    network): the §4f levels registry (levels.jsonl in the rotation root) is
    the local union of ALL recorded history — the rotation hook appends a row
    as each UTC day closes locally, and scripts/backfill_levels.py appends rows
    for days already archived to HF (so pruned-after-upload day files stay
    counted WITHOUT listing HF partitions). ASSUMPTION, stated: a day with no
    registry row is treated as unrecorded — honest, since both maintainers
    write a row for every day that actually closed with data.

    Day math: span = first registry date -> newest LOCAL day (registry dates ∪
    §3c day-file names, including a live-locked today ∪ the newest-row date),
    inclusive. The registry row count is reported alongside so coverage holes
    inside the span stay visible (a span is calendar time, not uptime).
    """
    lines: list[str] = [
        "clearing MinBTL is NECESSARY, not sufficient — a pre-registered "
        "hypothesis + kill criterion is still required (DEVELOPMENT §6)"
    ]
    data: dict[str, Any] = {}

    # DESIGN §0.7 rail b, enforced here rather than merely documented: the MinBTL
    # countdown counts RECORDED days only. Computing ANY number over an archive
    # partition — even a correct one, even labelled — creates a figure that can
    # be screenshotted next to the recorded one and read as the same quantity.
    # It is the one honest clock in this project; it does not get a second input.
    if mode == "vision":
        lines.append(
            "[INFO] readiness is NOT computed over an archive partition — the MinBTL "
            "countdown counts RECORDED days only (data/ticks/levels.jsonl). Archive "
            "history extends the TRADE-derived families and nothing else; letting it "
            "inflate this number would corrupt the gate every downstream verdict stands on."
        )
        data.update({"mode": "vision", "computed": False, "span_days": None,
                     "registry_path": None, "registry_days": None, "minbtl": {},
                     "pct_toward_target": None})
        return {"name": "research readiness", "verdict": "INFO", "lines": lines, "data": data}

    # Registry path: the rotation root in dir mode; the single-file layout keeps
    # the registry in the sibling rotation dir (data/ticks.duckdb <-> data/ticks/).
    reg_path = (db_path if db_path.is_dir() else db_path.with_suffix("")) / "levels.jsonl"
    # Date-only parse, restated from collector.read_levels_registry (same
    # no-collector-import rule as the schema note at the top of this file).
    reg_dates: list[str] = []
    if reg_path.exists():
        for raw in reg_path.read_text(encoding="utf-8").splitlines():
            if raw.strip():
                d = json.loads(raw).get("date")
                if d:
                    reg_dates.append(d)
    reg_dates.sort()

    # Newest local day: ISO YYYY-MM-DD strings compare correctly as text.
    candidates = list(reg_dates)
    candidates += [
        f.stem
        for f in [*(day_files or []), *(skipped_locked or [])]
        if _DAY_FILE_RE.fullmatch(f.stem)
    ]
    if anchor_ms is not None:
        candidates.append(
            datetime.fromtimestamp(anchor_ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")
        )
    newest = max(candidates) if candidates else None

    span_days = 0
    if reg_dates and newest is not None:
        # newest >= reg_dates[0] by construction (candidates ⊇ reg_dates).
        first_day = datetime.strptime(reg_dates[0], "%Y-%m-%d").date()
        last_day = datetime.strptime(newest, "%Y-%m-%d").date()
        span_days = (last_day - first_day).days + 1  # inclusive: 07-01..07-04 = 4
        lines.append(
            f"[INFO] recorded span {reg_dates[0]} -> {newest}: "
            f"{span_days} days (~{span_days * 24:,} 1h bars; "
            f"{len(reg_dates)} closed day(s) in the registry)"
        )
    else:
        lines.append(
            f"[INFO] no recorded days yet — the levels registry ({reg_path}) has no "
            "closed-day rows (the rotation hook writes the first row when a UTC day "
            "closes under `make collector`; scripts/backfill_levels.py covers "
            "HF-archived days)"
        )

    minbtl: dict[str, dict] = {}
    target_days: Optional[float] = None
    if min_backtest_length is None:
        lines.append(f"[INFO] btcquant.risk unavailable — MinBTL not computed ({_INSTALL_HINT_CORE})")
    else:
        for n in READINESS_TRIALS:
            yrs = float(min_backtest_length(n))
            days = yrs * DAYS_PER_YEAR
            minbtl[str(n)] = {"years": yrs, "days": days}
            if n == READINESS_TARGET_N:
                target_days = days
            lines.append(
                f"[INFO] MinBTL N={n:>3}: {yrs:.2f} yrs = {days:,.0f} days of 1h bars "
                f"({days * 24:,.0f} bars) — recorded {span_days} ({span_days / days:.1%})"
            )
        if target_days is not None:
            lines.append(
                f"[INFO] {span_days} of {target_days:,.0f} days toward "
                f"N={READINESS_TARGET_N} OOS candidacy "
                f"({span_days / target_days * 100.0:.1f}%)"
            )

    data.update({
        "registry_path": str(reg_path),
        "registry_days": len(reg_dates),
        "first_registry_date": reg_dates[0] if reg_dates else None,
        "newest_local_date": newest,
        "span_days": span_days,
        "bars_1h": span_days * 24,
        "minbtl": minbtl,
        "target_n": READINESS_TARGET_N,
        "pct_toward_target": (span_days / target_days * 100.0) if target_days else None,
    })
    return {"name": "research readiness", "verdict": "INFO", "lines": lines, "data": data}


# --------------------------------------------------------------------------- #
# CLI + report assembly.                                                       #
# --------------------------------------------------------------------------- #
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="check_ticks.py",
        description=(
            "Tick-store QA report card: read-only DuckDB checks on the collector's "
            "output. Gaps are reported, never filled. Exit 0 = OK/WARN, 1 = FAIL "
            "(corruption), 2 = store locked by the running collector."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--db",
        default="data/ticks.duckdb",
        help="DuckDB store path (the collector's output, or a copy of it) — or a "
        "DIRECTORY of day files (data/ticks/YYYY-MM-DD.duckdb, DESIGN §3c "
        "rotation), unioned read-only; locked day files are skipped honestly.",
    )
    parser.add_argument(
        "--vision",
        default=None,
        help="Grade a PUBLIC-ARCHIVE partition instead (DESIGN §3d): the family root "
        "data/vision/<venue>/<symbol>/<family>, unioned read-only over its "
        "date=*/trades.parquet files. Same duplicate-trade_id FAIL, same "
        "report-never-fill gap census, plus an ID-continuity section — and it REFUSES "
        "to print a MinBTL readiness number (archive days never count toward it).",
    )
    parser.add_argument(
        "--month",
        action="append",
        default=None,
        metavar="YYYY-MM",
        help="Vision mode only; repeatable. Restrict the partition scan to date= "
        "dirs in this calendar month. Full-archive dedup does not fit this "
        "machine (module docstring 'Scale limit': ~2.83 B rows need ~160 GB of "
        "aggregate state vs ~14 GB free, docs/STATUS.md) — month windows do.",
    )
    parser.add_argument(
        "--temp-dir",
        default=".tmp",
        help="Vision mode only: DuckDB temp_directory for aggregate spill "
        "(created if missing; memory_limit is pinned to "
        f"{_VISION_MEMORY_LIMIT}). Default is repo-local on purpose — this "
        "machine has a single volume, so there is no bigger disk to point at.",
    )
    parser.add_argument(
        "--hours",
        type=float,
        default=24.0,
        help="Window for rate/gap/cadence checks, anchored at the newest row.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the full report as machine-readable JSON instead of text.",
    )
    return parser


def build_report(
    con,
    db_path: Path,
    hours: float,
    day_files: Optional[list[Path]] = None,
    skipped_locked: Optional[list[Path]] = None,
    mode: str = "recorded",
) -> dict:
    """Run every section against an open read-only connection -> report dict.

    ``mode="vision"`` grades an archive partition (DESIGN §3d) with the SAME
    sections — one gate definition, never two. What it changes is listed where it
    is changed: inventory calls the four missing tables absent-by-construction,
    integrity reports the ts-inversion check as un-checkable instead of OK,
    readiness refuses to compute, and two archive-only censuses are added — a
    partition-containment gate (FAIL, the twin of ``upload_hf.stage_day``'s "a
    day file IS its partition") and the ID-continuity census.
    """
    wall_ms = int(time.time() * 1000)
    present = {
        r[0]
        for r in con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
        ).fetchall()
    }

    inventory, anchor = sec_inventory(
        con, present, db_path, hours, wall_ms, day_files, skipped_locked, mode
    )
    # Window anchored at the newest observation (see sec_inventory WHY); empty
    # store falls back to wall clock, which leaves every window trivially empty
    # — and empty is graded OK throughout (young store, not corruption).
    anchor_ms = anchor if anchor is not None else wall_ms
    window_start = anchor_ms - int(hours * 3_600_000)

    sections = [
        inventory,
        sec_integrity(con, present, window_start, mode),
        sec_coverage(con, present, window_start, anchor_ms),
        sec_coherence(con, present, window_start),
        sec_liquidations(con, present),
    ]
    if mode == "vision":
        sections.append(sec_partition_containment(con, day_files))
        sections.append(sec_id_continuity(con, present))
    sections.append(
        # (6) readiness gets the RAW anchor (None when the store is empty) — the
        # wall-clock fallback would invent a "newest local day" out of thin air.
        sec_readiness(db_path, day_files, skipped_locked, anchor, mode)
    )
    overall = _worst(*(s["verdict"] for s in sections))
    return {
        "db": str(db_path),
        "mode": mode,
        "generated_utc": _fmt_ts(wall_ms),
        "window_hours": hours,
        "window": {"start_ms": window_start, "anchor_ms": anchor_ms,
                   "anchor_source": "newest row" if anchor is not None else "wall clock (empty store)"},
        "sections": sections,
        "overall": overall,
        # WARNs do not fail the gate (§0.7 spirit): a young store legitimately
        # has gaps, and honest reporting must not punish honesty with a red CI.
        "exit_code": 1 if overall == "FAIL" else 0,
    }


def print_report(report: dict) -> None:
    """Human-readable report card (one [VERDICT] header per section)."""
    what = ("PUBLIC-ARCHIVE partition QA (DESIGN §3d — trades only)"
            if report.get("mode") == "vision" else "tick-store QA")
    print(f"{what} — {report['db']}")
    w = report["window"]
    print(
        f"window: last {report['window_hours']:g}h anchored at {_fmt_ts(w['anchor_ms'])} "
        f"({w['anchor_source']}); generated {report['generated_utc']}"
    )
    for i, sec in enumerate(report["sections"], start=1):
        print(f"\n[{sec['verdict']}] {i}. {sec['name']}")
        for line in sec["lines"]:
            print(f"    {line}")
    warns = [s["name"] for s in report["sections"] if s["verdict"] == "WARN"]
    fails = [s["name"] for s in report["sections"] if s["verdict"] == "FAIL"]
    print(f"\nverdict: {report['overall']} (exit {report['exit_code']})", end="")
    if fails:
        print(f" — FAIL: {', '.join(fails)}", end="")
    if warns:
        print(f" — WARNs (listed, not failed): {', '.join(warns)}", end="")
    print()


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    # --month is a vision-mode valve (module docstring "Scale limit"); accepting
    # it silently on the recorded store would be a flag that does nothing.
    months: Optional[list[str]] = None
    if args.month:
        if not args.vision:
            parser.error("--month restricts the --vision partition scan; pass --vision too")
        for m in args.month:
            if not re.fullmatch(r"\d{4}-\d{2}", m):
                parser.error(f"--month expects YYYY-MM, got {m!r}")
        months = sorted(set(args.month))

    if duckdb is None:  # guarded import — actionable hint, run_collector.py idiom
        print(f"ERROR: duckdb missing — {_INSTALL_HINT}", file=sys.stderr)
        return 1

    if args.vision:
        # Vision mode (DESIGN §3d): grade the archive partition with the same
        # sections. No lock dance — these are immutable parquet files no writer
        # holds, which is exactly why the archive tree is parquet and not duckdb.
        root = Path(args.vision)
        if not root.exists():
            print(f"no archive partition yet at {root} — run `make vision-sync` "
                  "to ingest one. (exit 0)")
            return 0
        con, parts, dates = connect_vision_readonly(root, months)
        if not parts:
            con.close()
            if months is not None:
                # Distinct message on purpose: "no partition matches the window
                # you asked for" and "no partitions at all" are different facts.
                print(f"no date=*/trades.parquet partitions under {root} match "
                      f"--month {', '.join(months)} — nothing to grade. (exit 0)")
            else:
                print(f"no date=*/trades.parquet partitions under {root} — nothing to grade. "
                      "(exit 0)")
            return 0
        # Explicit spill dir + memory cap (module docstring "Scale limit"):
        # bounded, observable out-of-core work instead of an OOM or a full disk.
        _apply_vision_resource_caps(con, args.temp_dir)
        try:
            report = build_report(con, root, args.hours, parts, [], mode="vision")
        finally:
            con.close()
        if months is not None:
            # A month-window report must SAY it is one — a screenshot of a
            # restricted pass must not be readable as a full-archive pass.
            report["month_filter"] = {"months": months, "partitions": len(parts)}
        if args.json:
            print(json.dumps(_json_safe(report), indent=2))
        else:
            if months is not None:
                print(f"note: partition scan RESTRICTED to month(s) "
                      f"{', '.join(months)} ({len(parts)} partition(s)) — this "
                      "report grades that window, not the full archive")
            print_report(report)
        return report["exit_code"]

    db_path = Path(args.db)
    if not db_path.exists():
        # Absence is not corruption (§0.3 spirit: un-ingested is a status). A
        # missing file just means the collector has never run on this machine.
        print(f"no store yet at {db_path} — run `make collector` to start recording. (exit 0)")
        return 0

    day_files: Optional[list[Path]] = None
    skipped: list[Path] = []
    if db_path.is_dir():
        # Dir mode (§3c day rotation): union every day file read-only. Locked
        # files (today's, while the collector writes) are skipped — the closed
        # days get graded WITHOUT a collector stop.
        candidates = sorted(
            f for f in db_path.glob("*.duckdb") if _DAY_FILE_RE.fullmatch(f.stem)
        )
        if not candidates:
            print(
                f"no day files yet in {db_path} — run `make collector` to start "
                "recording. (exit 0)"
            )
            return 0
        try:
            con, day_files, skipped = connect_dir_readonly(candidates)
        except duckdb.Error as exc:
            print(f"ERROR: cannot open store: {exc}", file=sys.stderr)
            return 1
        if not day_files:
            con.close()
            print(
                f"ERROR: every day file in {db_path} is locked by the running "
                "collector — nothing readable yet (try again once a day has closed).",
                file=sys.stderr,
            )
            return 2
    else:
        try:
            con = connect_readonly(db_path)
        except StoreLocked:
            print(
                "ERROR: locked — run while collector is stopped, or copy the file "
                f"(cp {db_path} /tmp/ticks-copy.duckdb && check --db it).",
                file=sys.stderr,
            )
            return 2
        except duckdb.Error as exc:
            # File exists but will not open read-only: that IS a failing report card.
            print(f"ERROR: cannot open store: {exc}", file=sys.stderr)
            return 1

    try:
        report = build_report(con, db_path, args.hours, day_files, skipped)
    finally:
        con.close()

    if args.json:
        print(json.dumps(_json_safe(report), indent=2))
    else:
        print_report(report)
    return report["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
