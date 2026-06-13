#!/usr/bin/env python3
"""fetch_data.py — cache public market data for btc-quant (research only).

Thin CLI over :func:`btcquant.data.get_ohlcv` and :func:`btcquant.data.get_funding`.
It fetches OHLCV candles (and, optionally, perpetual funding-rate history) from a
**public, keyless** endpoint and writes the normalized frame to ``data/`` so later
backtests run offline and a network outage degrades gracefully to the cache.

**No API keys, no authenticated endpoints, no orders.** See ``DISCLAIMER.md``.

Examples
--------
Cache 3 years of daily BTC candles from Coinbase::

    python3 scripts/fetch_data.py --symbol BTC-USD --granularity 1d --source coinbase \\
        --start 2021-01-01

Also pull Bybit perp funding (for the ``carry`` strategy)::

    python3 scripts/fetch_data.py --funding --funding-symbol BTCUSDT
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make the package importable when run as a bare script from the repo root.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from btcquant import data  # noqa: E402


def _build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser for the fetch CLI."""
    parser = argparse.ArgumentParser(
        prog="fetch_data.py",
        description=(
            "Fetch + cache public OHLCV (and optional perp funding) for btc-quant. "
            "Research/backtest only — no keys, no orders."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--symbol",
        default="BTC-USD",
        help="Product symbol, exchange-style (e.g. BTC-USD, ETH-USD).",
    )
    parser.add_argument(
        "--granularity",
        default="1d",
        choices=["1h", "1d"],
        help="Candle granularity.",
    )
    parser.add_argument(
        "--source",
        default="coinbase",
        choices=["coinbase", "kraken", "coingecko"],
        help="Public OHLCV source (coingecko is 1d only).",
    )
    parser.add_argument(
        "--start",
        default=None,
        help="Inclusive UTC start bound (e.g. 2021-01-01). Default: source's recent window.",
    )
    parser.add_argument(
        "--end",
        default=None,
        help="Inclusive UTC end bound (e.g. 2024-12-31). Default: now.",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Do not write/read the on-disk cache (force a live fetch, no fallback).",
    )
    parser.add_argument(
        "--funding",
        action="store_true",
        help="Also fetch perpetual funding-rate history (Bybit, perp-only).",
    )
    parser.add_argument(
        "--funding-symbol",
        default="BTCUSDT",
        help="Perp symbol for --funding (Bybit linear perp).",
    )
    parser.add_argument(
        "--funding-limit",
        type=int,
        default=200,
        help="Max funding rows to request (Bybit caps at 200).",
    )
    return parser


def _summarize(name: str, df, path: Path) -> None:
    """Print a one-line summary of a fetched/cached frame."""
    if df is None or len(df) == 0:
        print(f"[{name}] EMPTY result", file=sys.stderr)
        return
    first = df.index[0]
    last = df.index[-1]
    print(
        f"[{name}] {len(df):>6} rows  {first.date()} -> {last.date()}  "
        f"cols={list(df.columns)}  cache={path}"
    )


def main(argv: list[str] | None = None) -> int:
    """Entry point: fetch OHLCV (+ optional funding) and report the cache paths."""
    args = _build_parser().parse_args(argv)
    cache = not args.no_cache

    try:
        ohlcv = data.get_ohlcv(
            symbol=args.symbol,
            source=args.source,
            granularity=args.granularity,
            start=args.start,
            end=args.end,
            cache=cache,
        )
    except data.DataError as exc:
        print(f"ERROR fetching OHLCV: {exc}", file=sys.stderr)
        return 1

    safe_symbol = args.symbol.replace("/", "-")
    ohlcv_path = data.DATA_DIR / f"{args.source}_{safe_symbol}_{args.granularity}.csv"
    _summarize("ohlcv", ohlcv, ohlcv_path)

    if args.funding:
        try:
            funding = data.get_funding(
                symbol=args.funding_symbol,
                source="bybit",
                limit=args.funding_limit,
                cache=cache,
            )
        except data.DataError as exc:
            print(f"ERROR fetching funding: {exc}", file=sys.stderr)
            return 1
        safe_fsym = args.funding_symbol.replace("/", "-")
        funding_path = data.DATA_DIR / f"bybit_{safe_fsym}_funding.csv"
        _summarize("funding", funding, funding_path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
