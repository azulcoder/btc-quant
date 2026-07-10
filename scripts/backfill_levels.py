#!/usr/bin/env python3
"""backfill_levels.py — one-shot §4f levels-registry backfill from the HF dataset.

The rotation hook (btcquant/collector.py, DayFileManager.close_expired) records
a ``data/ticks/levels.jsonl`` row for every day it closes going FORWARD — but
days already archived to the Hugging Face dataset (DESIGN §3c lifecycle) closed
before the hook existed. This script fills those in: for every HF partition
date under ``data/date=YYYY-MM-DD/trades.parquet`` that is not yet in the
registry, it computes the SAME ``{date, o,h,l,c, poc, vah, val, vol}`` row —
via the SAME ``collector.compute_day_levels`` (bybit leg, fixed $10 tick,
ProfileStore-parity 70 % value area) — and adds it. Two code paths computing
"the day's levels" differently is exactly the drift bug the shared helper
prevents (DESIGN §4f).

Guarantees
----------
* **Idempotent** — dates already present in the registry are skipped; a second
  run is a no-op. (Same creed as the HF lifecycle's overlap checks.)
* **NEVER touches day files** — reads go to ``hf://`` only, the single write is
  the registry JSONL. Safe to run while the collector daemon records.
* **Chronological** — missing dates are processed oldest-first and the registry
  is rewritten atomically as one date-sorted file, so backfilled dates that
  predate rotation-hook rows still land in order (readers sort anyway; the
  on-disk order is for humans running ``tail``).
* **Honest absences** — an archived day with no bybit trades gets NO row
  (compute_day_levels returns None); an invented flat row would poison the
  naked-POC derivation (§0.7).

Needs the opt-in collector deps (duckdb with httpfs — pulled automatically for
``hf://`` reads) but NO HF login: the dataset is public and read-only here.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Optional

# --------------------------------------------------------------------------- #
# Guarded opt-in imports (requirements-collector.txt) — same discipline as      #
# btcquant/collector.py: importing this file never explodes; the actionable    #
# hint fires only when the store is actually touched.                          #
# --------------------------------------------------------------------------- #
try:  # optional dependency — see requirements-collector.txt
    import duckdb  # type: ignore
except Exception:  # noqa: BLE001 — any import failure means "store unavailable"
    duckdb = None  # type: ignore[assignment]

_INSTALL_HINT = "pip install -r requirements-collector.txt"

# Reuse the collector's registry/levels helpers — the rotation hook and this
# backfill MUST produce byte-identical rows for the same trades (§4f).
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
from btcquant import collector  # noqa: E402

DEFAULT_REPO = "azulcoder/btc-quant-ticks"
DEFAULT_REGISTRY = "data/ticks/levels.jsonl"

EXIT_OK, EXIT_FAIL, EXIT_USAGE = 0, 1, 2

# Repo ids are interpolated into read_parquet() paths (table functions cannot
# take ``?`` placeholders) — validate the identifier shape first, same rule as
# upload_hf.py's table-name whitelist.
_REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]*/[A-Za-z0-9][A-Za-z0-9._\-]*$")


def _hf_url(repo: str, date: str) -> str:
    """hf:// path of one archived day's trades partition (§3c hive layout)."""
    return f"hf://datasets/{repo}/data/date={date}/trades.parquet"


# --------------------------------------------------------------------------- #
# HF reader seams — EVERY hf:// touch goes through these two functions so the  #
# tests monkeypatch THEM and never reach the network (the upload_hf.py seam    #
# discipline).                                                                 #
# --------------------------------------------------------------------------- #
def hf_dates(con, repo: str) -> list[str]:
    """Every date partition in the HF dataset, ascending (§4f: duckdb over the
    ``data/date=*/trades.parquet`` glob; ``date`` is the hive partition column,
    so this reads parquet metadata + paths, not the tick payloads)."""
    rows = con.execute(
        f"SELECT DISTINCT date FROM read_parquet('{_hf_url(repo, '*')}', "
        "hive_partitioning=1) ORDER BY date"
    ).fetchall()
    return [str(r[0]) for r in rows]


def hf_day_levels(con, repo: str, date: str) -> Optional[dict]:
    """Registry row for ONE archived day, from its HF trades partition.

    A TEMP VIEW named ``trades`` makes the parquet look like the day-file
    schema, so ``collector.compute_day_levels`` — the exact function the
    rotation hook runs — does the math. None when the day has no bybit trades.
    """
    con.execute(
        "CREATE OR REPLACE TEMP VIEW trades AS "
        f"SELECT * FROM read_parquet('{_hf_url(repo, date)}')"
    )
    try:
        return collector.compute_day_levels(con, date)
    finally:
        con.execute("DROP VIEW IF EXISTS trades")


# --------------------------------------------------------------------------- #
# CLI.                                                                          #
# --------------------------------------------------------------------------- #
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill data/ticks/levels.jsonl (§4f day-levels registry) from the "
            "HF dataset's archived day partitions. Idempotent; never touches day "
            "files; safe while the collector runs."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--repo", default=DEFAULT_REPO, help="HF dataset repo id.")
    parser.add_argument(
        "--registry", default=DEFAULT_REGISTRY,
        help="Path of the levels registry JSONL to fill.",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    if duckdb is None:
        print(f"ERROR: duckdb missing — {_INSTALL_HINT}", file=sys.stderr)
        return EXIT_FAIL
    if not _REPO_RE.match(args.repo):
        print(f"ERROR: bad --repo {args.repo!r} — expected owner/name", file=sys.stderr)
        return EXIT_USAGE

    registry = Path(args.registry)
    existing = collector.read_levels_registry(registry)
    have = {r.get("date") for r in existing}
    print(f"[backfill-levels] registry {registry}: {len(existing)} day row(s)")

    con = duckdb.connect()  # in-memory; httpfs autoloads on the first hf:// read
    try:
        dates = hf_dates(con, args.repo)
        todo = [d for d in dates if d not in have]  # oldest-first (chronological)
        print(
            f"[backfill-levels] hf://datasets/{args.repo}: {len(dates)} archived "
            f"day(s), {len(todo)} missing from the registry"
        )
        if not todo:
            print("[backfill-levels] nothing to backfill — registry already covers HF")
            return EXIT_OK

        added: list[dict] = []
        for date in todo:
            row = hf_day_levels(con, args.repo, date)
            if row is None:
                # No bybit trades that day: no row — an invented one would
                # poison the naked-POC derivation (§0.7).
                print(f"  {date}: no bybit trades in the partition — skipped")
                continue
            added.append(row)
            print(
                f"  {date}: poc {row['poc']}, va [{row['val']}, {row['vah']}], "
                f"o/h/l/c {row['o']}/{row['h']}/{row['l']}/{row['c']}, vol {row['vol']}"
            )
    finally:
        con.close()

    if added:
        # Atomic rewrite of the MERGED, date-sorted registry: keeps the file
        # chronological even when backfilled dates predate rotation-hook rows.
        # `have` guarantees no date is ever duplicated (idempotence).
        merged = sorted(existing + added, key=lambda r: r.get("date") or "")
        registry.parent.mkdir(parents=True, exist_ok=True)
        tmp = registry.with_name(registry.name + ".tmp")
        tmp.write_text(
            "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in merged),
            encoding="utf-8",
        )
        tmp.replace(registry)
        print(f"[backfill-levels] wrote {len(added)} new row(s) -> {registry}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
