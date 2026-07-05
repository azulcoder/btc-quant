#!/usr/bin/env python3
"""run_collector.py — tick collector CLI (DESIGN-orderflow-terminal.md §3 + §3c).

Thin argparse wrapper over :func:`btcquant.collector.run`. Records keyless public
BTC perp microstructure (trades, liquidations, 1/s depth, funding/mark, OI,
crowding, DVOL, hourly option chain) into a local DuckDB store — by default a
DIRECTORY of per-UTC-day files (``data/ticks/YYYY-MM-DD.duckdb``, §3c rotation);
point ``--db`` at a ``.duckdb`` FILE for the legacy single-file mode. **No API
keys, no authenticated endpoints, no orders.**

Honesty reminder (DESIGN §0.3): running this makes tick families *time-gated*, not
*validated* — nothing recorded here enters the OOS harness without MinBTL-clearing
history AND a pre-registered, greenlit hypothesis (DEVELOPMENT.md §6).

Examples
--------
Default run (all five venues, daily rotation, keep-all)::

    python3 scripts/run_collector.py

With the BYOD replay API::

    python3 scripts/run_collector.py --api-port 8788

One-shot split of a legacy single-file store into day files (§3c; verifies
per-day counts sum to the original and NEVER deletes the original)::

    python3 scripts/run_collector.py --migrate-legacy data/ticks.duckdb --db data/ticks

Requires the opt-in deps:  pip install -r requirements-collector.txt
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make the package importable when run as a bare script from the repo root.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from btcquant import collector  # noqa: E402


def _build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser for the collector CLI (DESIGN §3 flags)."""
    parser = argparse.ArgumentParser(
        prog="run_collector.py",
        description=(
            "Tick collector daemon: keyless public WS/REST -> DuckDB day files "
            "(§3c rotation). Research history only — no keys, no orders. "
            "Ctrl-C flushes and exits."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--symbol",
        default="BTCUSDT",
        help=(
            "Perp symbol, exchange-style (Bybit linear / Binance futures); the "
            "OKX/Coinbase/Deribit ids are derived and logged at startup."
        ),
    )
    parser.add_argument(
        "--exchanges",
        default="binancef,bybit,okx,coinbase,deribit",
        help="Comma-separated source codes (accepted: binancef, bybit, okx, coinbase, deribit).",
    )
    parser.add_argument(
        "--db",
        default="data/ticks",
        help=(
            "Store path (gitignored). A DIRECTORY selects §3c daily rotation "
            "(per-UTC-day files); a .duckdb FILE selects legacy single-file mode."
        ),
    )
    parser.add_argument(
        "--api-port",
        type=int,
        default=None,
        help="Enable the BYOD HTTP API on this port (default: OFF).",
    )
    parser.add_argument(
        "--retention-days",
        type=int,
        default=None,
        help=(
            "Optional daily prune of rows older than N days — legacy single-file "
            "mode ONLY (rotation pruning belongs to the HF lifecycle; default: keep-all)."
        ),
    )
    parser.add_argument(
        "--migrate-legacy",
        metavar="FILE",
        default=None,
        help=(
            "One-shot: split this legacy single-file store into per-day files "
            "under --db, verify counts, print an rm hint, and exit (§3c). "
            "The original is never deleted."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point: parse flags and hand off to the daemon (blocks until SIGINT)."""
    args = _build_parser().parse_args(argv)
    exchanges = tuple(e.strip() for e in args.exchanges.split(",") if e.strip())
    try:
        if args.migrate_legacy is not None:
            # One-shot maintenance path (§3c) — no daemon, no network.
            collector.migrate_legacy(args.migrate_legacy, args.db)
            return 0
        collector.run(
            symbol=args.symbol,
            exchanges=exchanges,
            db=args.db,
            api_port=args.api_port,
            retention_days=args.retention_days,
        )
    except RuntimeError as exc:  # missing deps / failed migration verification
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:  # bad exchange code / retention value / migrate args
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
