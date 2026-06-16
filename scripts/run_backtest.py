#!/usr/bin/env python3
"""run_backtest.py — run a btc-quant strategy backtest and print the honest stats.

Loads (or fetches + caches) public OHLCV, builds the chosen strategy's target
positions, runs the no-look-ahead, cost-charged backtester, and prints a stats
table that **always** shows the buy-and-hold baseline alongside the strategy and
the headline **Deflated Sharpe**. It then saves a matplotlib tearsheet PNG and a
dashboard JSON (both via :mod:`btcquant.report`).

The honesty rails are not optional (DESIGN.md / RESEARCH.md):

* a signal at bar *t* trades bar *t+1* (the backtester shifts by one bar),
* transaction costs + slippage are ON by default,
* every run reports the net-of-cost **Deflated Sharpe** and the buy-and-hold
  baseline — never a lone equity curve.

**Research/backtest only — no keys, no orders.** See ``DISCLAIMER.md``.

Examples
--------
::

    python3 scripts/run_backtest.py --strategy ma_trend_filter --granularity 1d
    python3 scripts/run_backtest.py --strategy tsmom --lookback 20 --n-trials 25
    python3 scripts/run_backtest.py --strategy pairs_coint --eth-symbol ETH-USD
    python3 scripts/run_backtest.py --strategy carry   # uses cached/fetched funding
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

# Make the package importable when run as a bare script from the repo root.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd  # noqa: E402

from btcquant import backtest, data, report, strategies, tracking  # noqa: E402

# Strategies this CLI can build (short_vol is a documented data-less stub and is
# intentionally excluded — it raises NotImplementedError by design).
_PRICE_STRATEGIES = ("buy_and_hold", "ma_trend_filter", "tsmom")
_PAIRS_STRATEGIES = ("pairs_coint",)
_FUNDING_STRATEGIES = ("carry",)
_ALL_STRATEGIES = _PRICE_STRATEGIES + _PAIRS_STRATEGIES + _FUNDING_STRATEGIES


def _build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser for the backtest CLI."""
    parser = argparse.ArgumentParser(
        prog="run_backtest.py",
        description=(
            "Backtest a btc-quant strategy net of cost, print a stats table with the "
            "Deflated Sharpe and the buy-and-hold baseline, and save a tearsheet PNG "
            "+ dashboard JSON. Research only — no keys, no orders."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--strategy",
        default="ma_trend_filter",
        choices=_ALL_STRATEGIES,
        help="Strategy to backtest (short_vol is a data-less stub, excluded).",
    )
    parser.add_argument(
        "--granularity",
        default="1d",
        choices=["1h", "1d"],
        help="Candle granularity.",
    )
    parser.add_argument("--symbol", default="BTC-USD", help="Primary product symbol.")
    parser.add_argument(
        "--eth-symbol",
        default="ETH-USD",
        help="Second leg symbol for pairs_coint.",
    )
    parser.add_argument(
        "--source",
        default="coinbase",
        choices=["coinbase", "kraken", "coingecko"],
        help="Public OHLCV source.",
    )
    parser.add_argument("--start", default=None, help="Inclusive UTC start bound.")
    parser.add_argument("--end", default=None, help="Inclusive UTC end bound.")
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Do not use the on-disk cache (force live fetch).",
    )

    # Cost model (ON by default — the honesty rail).
    parser.add_argument("--cost-bps", type=float, default=10.0, help="One-way fee (bps).")
    parser.add_argument("--slippage-bps", type=float, default=2.0, help="One-way slippage (bps).")
    parser.add_argument("--walk", action="store_true",
                        help="Also run anchored walk-forward and report OOS vs in-sample.")
    parser.add_argument("--folds", type=int, default=5, help="Walk-forward OOS folds (with --walk).")

    # Deflated-Sharpe selection bias.
    parser.add_argument(
        "--n-trials",
        type=int,
        default=1,
        help="Number of strategy configs searched (deflates the Sharpe).",
    )

    # Strategy params.
    parser.add_argument("--ma-n", type=int, default=200, help="ma_trend_filter slow MA window.")
    parser.add_argument(
        "--ma-fast",
        type=int,
        default=None,
        help="ma_trend_filter fast MA (enables dual-cross golden-cross variant).",
    )
    parser.add_argument("--lookback", type=int, default=20, help="tsmom trailing-return window.")
    parser.add_argument(
        "--no-vol-scale",
        action="store_true",
        help="tsmom: disable vol targeting (use the raw sign).",
    )
    parser.add_argument(
        "--long-short",
        action="store_true",
        help="tsmom: short on negative momentum (default long/flat).",
    )
    parser.add_argument("--target-vol", type=float, default=0.15, help="Vol-target (annualized).")

    # Funding (carry).
    parser.add_argument("--funding-symbol", default="BTCUSDT", help="Perp symbol for carry funding.")

    # Output.
    parser.add_argument(
        "--outdir",
        default=str(_ROOT / "data"),
        help="Directory for the tearsheet PNG + dashboard JSON.",
    )
    parser.add_argument(
        "--track",
        action="store_true",
        help="Log this run's params + (OOS) metrics + artifacts to MLflow "
             "(optional; needs requirements-dev.txt — no-ops with a hint otherwise).",
    )
    parser.add_argument(
        "--no-plot",
        action="store_true",
        help="Skip the matplotlib tearsheet PNG (still writes the JSON).",
    )
    return parser


def _load_ohlcv(args: argparse.Namespace, symbol: str) -> pd.DataFrame:
    """Fetch (or load from cache) an OHLCV frame for ``symbol``."""
    return data.get_ohlcv(
        symbol=symbol,
        source=args.source,
        granularity=args.granularity,
        start=args.start,
        end=args.end,
        cache=not args.no_cache,
    )


def _periods_per_year(granularity: str) -> int:
    """Bars per year for the annualization factor (365 daily, 24*365 hourly)."""
    return 24 * 365 if granularity == "1h" else 365


def _build_positions(
    args: argparse.Namespace,
    df: pd.DataFrame,
    ppy: int,
) -> tuple[pd.Series, pd.Series]:
    """Build the (positions, prices) pair for the requested strategy.

    Returns the target-position Series and the price (close) Series the backtester
    will trade against. Pairs and funding strategies pull their extra data inside.
    """
    name = args.strategy
    close = df["close"]

    if name == "buy_and_hold":
        return strategies.buy_and_hold(df), close

    if name == "ma_trend_filter":
        return strategies.ma_trend_filter(df, n=args.ma_n, fast=args.ma_fast), close

    if name == "tsmom":
        pos = strategies.tsmom(
            df,
            lookback=args.lookback,
            vol_scaled=not args.no_vol_scale,
            long_short=args.long_short,
            target_vol=args.target_vol,
            periods_per_year=ppy,
        )
        return pos, close

    if name == "pairs_coint":
        eth = _load_ohlcv(args, args.eth_symbol)
        pos = strategies.pairs_coint(df["close"], eth["close"])
        # Trade the BTC leg's price against the BTC-leg target position.
        return pos, close

    if name == "carry":
        funding = data.get_funding(
            symbol=args.funding_symbol,
            source="bybit",
            cache=not args.no_cache,
        )
        pos = strategies.carry(funding)
        # The carry P&L is driven by the funding cadence, not spot price moves;
        # we proxy the perp-leg price path with the spot close reindexed to the
        # funding clock so the harness can mark turnover/exposure consistently.
        px = close.reindex(funding.index).ffill().bfill()
        px = px.dropna()
        pos = pos.reindex(px.index)
        return pos, px

    raise ValueError(f"unknown strategy {name!r}")


def _fmt(value: object, pct: bool = False, dp: int = 3) -> str:
    """Format a stat for the table; NaN/None -> 'n/a'."""
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "n/a"
    if math.isnan(f) or math.isinf(f):
        return "n/a"
    return f"{f * 100:.{dp}f}%" if pct else f"{f:.{dp}f}"


def _print_stats_table(strat_name: str, strat_stats: dict, bh_stats: dict) -> None:
    """Print a side-by-side strategy vs buy-and-hold stats table.

    The headline is the net-of-cost **Deflated Sharpe**; buy-and-hold is always the
    reference column (DESIGN.md non-negotiable).
    """
    rows = [
        ("CAGR", "cagr", True, 2),
        ("Sharpe (net, ann.)", "sharpe", False, 3),
        ("Deflated Sharpe *", "deflated_sharpe", False, 3),
        ("Prob. Sharpe (PSR)", "psr", False, 3),
        ("Sortino", "sortino", False, 3),
        ("Volatility (ann.)", "volatility", True, 2),
        ("Max drawdown", "max_drawdown", True, 2),
        ("Calmar", "calmar", False, 3),
        ("Hit rate", "hit_rate", True, 1),
        ("VaR 5%", "var_5pct", True, 2),
        ("CVaR 5%", "cvar_5pct", True, 2),
        ("Skew", "skew", False, 3),
        ("Kurtosis", "kurtosis", False, 3),
        ("Trades", "trades", False, 0),
        ("Terminal equity (x)", "terminal_equity", False, 2),
    ]

    label_w = 22
    col_w = 16
    header = f"{'metric':<{label_w}}{strat_name:>{col_w}}{'buy_and_hold':>{col_w}}"
    print("=" * len(header))
    print(header)
    print("-" * len(header))
    for label, key, pct, dp in rows:
        s = strat_stats.get(key, float("nan"))
        b = bh_stats.get(key, float("nan"))
        if key == "trades":
            s_str = str(int(s)) if isinstance(s, (int, float)) and not math.isnan(float(s)) else "n/a"
            b_str = str(int(b)) if isinstance(b, (int, float)) and not math.isnan(float(b)) else "n/a"
        else:
            s_str = _fmt(s, pct=pct, dp=dp)
            b_str = _fmt(b, pct=pct, dp=dp)
        print(f"{label:<{label_w}}{s_str:>{col_w}}{b_str:>{col_w}}")
    print("=" * len(header))
    n_trials = strat_stats.get("n_trials", 1)
    cost = strat_stats.get("cost_bps", 0.0)
    slip = strat_stats.get("slippage_bps", 0.0)
    print(
        f"* Deflated Sharpe benchmarks the observed SR against the expected max of "
        f"N={n_trials} skill-less trials (Bailey & Lopez de Prado 2014). "
        f"Significant when > 0.95."
    )
    print(f"  Costs ON: {cost:.1f} bps fee + {slip:.1f} bps slippage per unit turnover (one-way).")
    print("  NOT FINANCIAL ADVICE - backtest != forecast.")


def main(argv: list[str] | None = None) -> int:
    """Entry point: run the backtest, print stats, save tearsheet + JSON."""
    args = _build_parser().parse_args(argv)
    ppy = _periods_per_year(args.granularity)

    try:
        df = _load_ohlcv(args, args.symbol)
    except data.DataError as exc:
        print(f"ERROR loading OHLCV: {exc}", file=sys.stderr)
        return 1

    if len(df) < 2:
        print(f"ERROR: only {len(df)} bars loaded; need >= 2.", file=sys.stderr)
        return 1

    try:
        positions, prices = _build_positions(args, df, ppy)
    except (data.DataError, NotImplementedError, ValueError, KeyError) as exc:
        print(f"ERROR building positions for {args.strategy!r}: {exc}", file=sys.stderr)
        return 1

    result = backtest.run(
        positions,
        prices,
        cost_bps=args.cost_bps,
        slippage_bps=args.slippage_bps,
        periods_per_year=ppy,
        n_trials=args.n_trials,
    )

    # Buy-and-hold baseline on the SAME price series (always shown).
    bh_df = pd.DataFrame({"close": prices})
    bh_result = backtest.run(
        strategies.buy_and_hold(bh_df),
        prices,
        cost_bps=args.cost_bps,
        slippage_bps=args.slippage_bps,
        periods_per_year=ppy,
        n_trials=1,
    )

    start = prices.index[0]
    end = prices.index[-1]
    print(
        f"\nbtc-quant backtest | {args.strategy} | {args.symbol} {args.granularity} "
        f"| {start.date()} -> {end.date()} | {len(prices)} bars\n"
    )
    _print_stats_table(args.strategy, result["stats"], bh_result["stats"])

    # --- Walk-forward OOS (the honest out-of-sample view) --------------------- #
    wf_oos = None  # captured for optional MLflow logging below
    if args.walk:
        try:
            make_pos = lambda px: _build_positions(args, pd.DataFrame({"close": px}), ppy)[0]
            wf = backtest.walk_forward(make_pos, prices, n_splits=args.folds,
                                       cost_bps=args.cost_bps, slippage_bps=args.slippage_bps,
                                       periods_per_year=ppy)
            oos, is_ = wf["oos"], wf["is_"]
            wf_oos = oos
            print(f"\nwalk-forward ({args.folds} folds) — the IS→OOS drop is the overfitting tell:")
            print(f"  in-sample  Sharpe {is_.get('sharpe', float('nan')):.2f}")
            print(f"  OUT-OF-SAMPLE Sharpe {oos.get('sharpe', float('nan')):.2f} | "
                  f"OOS Deflated Sharpe {oos.get('deflated_sharpe', float('nan')):.2f} "
                  f"(folds as trials){' *' if oos.get('deflated_sharpe', 0) > 0.95 else ''}")
            cp = backtest.cpcv(make_pos, prices, periods_per_year=ppy)
            if cp["n_paths"]:
                print(f"  CPCV multi-path OOS Sharpe: median {cp['median_sharpe']:.2f} "
                      f"[p25 {cp['p25']:.2f}, p75 {cp['p75']:.2f}] over {cp['n_paths']} paths "
                      f"— wide/sign-flipping dispersion ⇒ regime-dependent, not a stable edge.")
        except Exception as exc:  # noqa: BLE001
            print(f"\nwalk-forward unavailable for {args.strategy!r} "
                  f"(use scripts/compare.py for the OOS leaderboard): {str(exc)[:70]}")

    # --- Outputs: tearsheet PNG + dashboard JSON ------------------------------ #
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    stem = f"{args.strategy}_{args.symbol.replace('/', '-')}_{args.granularity}"

    json_path = outdir / f"{stem}.json"
    report.to_dashboard_json(
        result,
        str(json_path),
        prices=prices,
        periods_per_year=ppy,
        meta={
            "strategy": args.strategy,
            "symbol": args.symbol,
            "granularity": args.granularity,
            "source": args.source,
            "cost_bps": args.cost_bps,
            "slippage_bps": args.slippage_bps,
            "n_trials": args.n_trials,
            "start": str(start.date()),
            "end": str(end.date()),
        },
    )
    print(f"\nsaved dashboard JSON -> {json_path}")

    png_path = None
    if not args.no_plot:
        png_path = outdir / f"{stem}.png"
        fig = report.tearsheet(
            result,
            prices=prices,
            periods_per_year=ppy,
            title=f"btc-quant: {args.strategy} ({args.symbol} {args.granularity})",
        )
        fig.savefig(str(png_path), dpi=110)
        print(f"saved tearsheet PNG  -> {png_path}")

    # --- Optional MLflow run-tracking (reproducibility spine) ------------------ #
    if args.track:
        st = result["stats"]
        params = {
            "strategy": args.strategy, "symbol": args.symbol, "granularity": args.granularity,
            "source": args.source, "start": str(start.date()), "end": str(end.date()),
            "bars": len(prices), "cost_bps": args.cost_bps, "slippage_bps": args.slippage_bps,
            "n_trials": args.n_trials, "walk": bool(args.walk), "folds": args.folds,
        }
        metrics = {
            "sharpe": st.get("sharpe"), "deflated_sharpe": st.get("deflated_sharpe"),
            "psr": st.get("psr"), "sortino": st.get("sortino"), "cagr": st.get("cagr"),
            "max_drawdown": st.get("max_drawdown"), "calmar": st.get("calmar"),
            "trades": st.get("trades"), "terminal_equity": st.get("terminal_equity"),
        }
        if wf_oos is not None:  # the honest out-of-sample numbers are what matter
            metrics["oos_sharpe"] = wf_oos.get("sharpe")
            metrics["oos_deflated_sharpe"] = wf_oos.get("deflated_sharpe")
        artifacts = [str(json_path)] + ([str(png_path)] if png_path else [])
        run_id = tracking.log_run(stem, params, metrics, artifacts=artifacts)
        if run_id:
            print(f"logged MLflow run {run_id[:8]} -> experiment 'btc-quant' "
                  f"(view: mlflow ui --backend-store-uri sqlite:///mlflow.db)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
