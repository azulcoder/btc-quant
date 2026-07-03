#!/usr/bin/env python3
"""run_collector.py — O-0 tick collector CLI (DESIGN-orderflow-terminal.md §3).

Thin argparse wrapper over :func:`btcquant.collector.run`. Records keyless public
BTC perp microstructure (trades, liquidations, 1/s depth, funding/mark, OI) into a
local DuckDB file. **No API keys, no authenticated endpoints, no orders.**

Honesty reminder (DESIGN §0.3): running this makes tick families *time-gated*, not
*validated* — nothing recorded here enters the OOS harness without MinBTL-clearing
history AND a pre-registered, greenlit hypothesis (DEVELOPMENT.md §6).

Examples
--------
Default run (Bybit primary WS + Binance depth/REST, keep-all retention)::

    python3 scripts/run_collector.py --symbol BTCUSDT --exchanges binancef,bybit

With the BYOD replay API and a 90-day retention cap::

    python3 scripts/run_collector.py --api-port 8788 --retention-days 90

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
            "O-0 tick collector daemon: keyless public WS/REST -> DuckDB. "
            "Research history only — no keys, no orders. Ctrl-C flushes and exits."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--symbol",
        default="BTCUSDT",
        help="Perp symbol, exchange-style (Bybit linear / Binance futures).",
    )
    parser.add_argument(
        "--exchanges",
        default="binancef,bybit",
        help="Comma-separated source codes (accepted: binancef, bybit).",
    )
    parser.add_argument(
        "--db",
        default="data/ticks.duckdb",
        help="DuckDB store path (gitignored; single writer owns the file).",
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
        help="Optional daily prune of rows older than N days (default: keep-all).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point: parse flags and hand off to the daemon (blocks until SIGINT)."""
    args = _build_parser().parse_args(argv)
    exchanges = tuple(e.strip() for e in args.exchanges.split(",") if e.strip())
    try:
        collector.run(
            symbol=args.symbol,
            exchanges=exchanges,
            db=args.db,
            api_port=args.api_port,
            retention_days=args.retention_days,
        )
    except RuntimeError as exc:  # missing opt-in deps — actionable hint, not a trace
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:  # bad exchange code / retention value
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
