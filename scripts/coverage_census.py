"""coverage_census.py — ON/PARTIAL/OFF cells, normalised by TIME not by sample count.

Why this replaces the ad-hoc census in `docs/EDA-microstructure-001.md` §11.

The first census classified a `(date, hour, source)` cell on **distinct samples in the
hour, as a fraction of the intended cadence**. That looks cadence-normalised and is
not, because the *granularity* differs: a table polled once an hour can only score 0
or 1, so it is never PARTIAL and a 15-minute outage usually misses it entirely; a
table polled every 5 s needs 648 of 720 samples and the same outage drops it straight
out of ON. Comparing their `%ON` compares two different questions. That is instance 7
in the instrument-blindness ledger (`STRATEGY.md`).

This module measures **the fraction of the hour that is covered**. A sample at `t`
covers `[t, t + cadence)`, truncated at the next sample and clipped to the hour; the
covered intervals are unioned and divided by 3600 s. A 15-minute outage then removes
15 minutes of coverage from any table whose cadence is finer than the outage — the
same answer regardless of how often it is polled.

What this deliberately does NOT equalise: a table polled once an hour genuinely loses
little from a 15-minute hole, and this measure says so. That asymmetry is real (§11
Claim 2 measures it directly as interpolation error) rather than an artefact of where
a threshold happens to land.

Research only. Read-only: never writes to the tick store.
"""

from __future__ import annotations

import argparse
import sys

HOUR_MS = 3_600_000

# (table, source column value, intended cadence seconds). `None` = push stream, in
# which case the measured median inter-arrival is substituted at run time.
SPEC: list[tuple[str, str, float | None]] = [
    ("funding_mark", "binancef", 5.0),
    ("funding_mark", "okx", 60.0),
    ("funding_mark", "bybit", None),
    ("open_interest", "binancef", 60.0),
    ("open_interest", "okx", 60.0),
    ("open_interest", "bybit", None),
    ("crowding", "binancef", 300.0),
    ("dvol", "deribit", 60.0),
    ("options_chain", "deribit", 3600.0),
    ("depth_snapshots", "binancef", 1.0),
    ("depth_snapshots", "bybit", 1.0),
    ("depth_snapshots", "okx", 1.0),
]

ON_FRAC, OFF_FRAC = 0.90, 0.10


def _glob(repo_or_hf: str, table: str) -> str:
    return f"{repo_or_hf}/date=*/{table}.parquet"


def census(con, root: str, table: str, source: str, cadence_s: float | None,
           start: str, end: str) -> dict:
    """Time-covered census for one (table, source). Returns counts and %ON."""
    ex = "exchange" if table in ("funding_mark", "open_interest", "crowding",
                                "depth_snapshots") else None
    where = f"date >= '{start}' AND date <= '{end}'"
    if ex:
        where += f" AND {ex} = '{source}'"
    src = _glob(root, table)

    if cadence_s is None:  # push stream: measure it rather than assume it
        med = con.execute(
            f"""SELECT median(d) FROM (SELECT ts_ms - lag(ts_ms) OVER (ORDER BY ts_ms) d
                FROM read_parquet('{src}', hive_partitioning=1) WHERE {where})
                WHERE d IS NOT NULL AND d > 0""").fetchone()[0]
        cadence_s = (med or 1000.0) / 1000.0

    cad_ms = int(cadence_s * 1000)
    # A sample covers [t, t+cadence), truncated at the next sample in the same hour
    # and clipped to the hour end. Time before the first sample of an hour is NOT
    # covered — no data existed then, and pretending otherwise is the whole bug.
    q = f"""
    WITH t AS (
      SELECT DISTINCT date d, ts_ms FROM read_parquet('{src}', hive_partitioning=1)
      WHERE {where}
    ), h AS (
      SELECT d, CAST(floor(ts_ms / {HOUR_MS}.0) AS BIGINT) hb, ts_ms FROM t
    ), g AS (
      SELECT d, hb, ts_ms,
             lead(ts_ms) OVER (PARTITION BY d, hb ORDER BY ts_ms) nxt,
             (hb + 1) * {HOUR_MS} AS hend
      FROM h
    ), cov AS (
      SELECT d, hb, sum(least({cad_ms}, coalesce(nxt, hend) - ts_ms)) AS ms
      FROM g GROUP BY 1, 2
    ), days AS (SELECT DISTINCT d FROM t),
       grid AS (SELECT d, unnest(generate_series(0, 23)) hh FROM days),
       cells AS (
      SELECT g.d, g.hh, coalesce(c.ms, 0) / {HOUR_MS}.0 AS frac
      FROM grid g LEFT JOIN cov c ON c.d = g.d AND (c.hb % 24) = g.hh
    )
    SELECT count(*), sum(CASE WHEN frac >= {ON_FRAC} THEN 1 ELSE 0 END),
           sum(CASE WHEN frac < {OFF_FRAC} THEN 1 ELSE 0 END), avg(frac)
    FROM cells"""
    n, on_, off_, mean = con.execute(q).fetchone()
    return {"table": table, "source": source, "cadence_s": cadence_s, "cells": n,
            "on": on_ or 0, "off": off_ or 0, "partial": n - (on_ or 0) - (off_ or 0),
            "pct_on": 100.0 * (on_ or 0) / n if n else 0.0,
            "mean_covered": 100.0 * (mean or 0.0)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root",
                    default="hf://datasets/azulcoder/btc-quant-ticks/data")
    ap.add_argument("--start", default="2026-07-05")
    ap.add_argument("--end", default="2026-08-03")
    a = ap.parse_args()

    import duckdb
    con = duckdb.connect()
    con.execute("SET enable_progress_bar=false")
    if a.root.startswith("hf://"):
        con.execute("INSTALL httpfs; LOAD httpfs")

    print(f"Coverage census, TIME-normalised — {a.start} .. {a.end}")
    print(f"ON >= {ON_FRAC:.0%} of the hour covered, OFF < {OFF_FRAC:.0%}, else PARTIAL\n")
    print(f"  {'table':<17}{'source':<10}{'cadence':>9}{'ON':>6}{'PART':>6}"
          f"{'OFF':>6}{'%ON':>8}{'mean cov':>10}")
    for table, source, cad in SPEC:
        try:
            r = census(con, a.root, table, source, cad, a.start, a.end)
        except Exception as exc:  # noqa: BLE001 — a missing table is a skip, not a crash
            print(f"  {table:<17}{source:<10}  skipped: {str(exc)[:44]}")
            continue
        print(f"  {r['table']:<17}{r['source']:<10}{r['cadence_s']:>8.1f}s"
              f"{r['on']:>6}{r['partial']:>6}{r['off']:>6}"
              f"{r['pct_on']:>7.1f}%{r['mean_covered']:>9.1f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
