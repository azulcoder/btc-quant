#!/usr/bin/env python3
"""Strategy leaderboard — run every strategy on the same BTC data and rank them
by Deflated Sharpe, net of cost, against the buy-and-hold baseline.

This is the honest centrepiece: the Deflated Sharpe is benchmarked against the
expected max Sharpe of N skill-less trials (Bailey & Lopez de Prado 2014), where
N is the number of strategies compared here — so trying many strategies and
keeping the best is penalised exactly as it should be. Most strategies do NOT
beat buy-and-hold after costs + deflation; that is the point.

Research / backtest only. Not financial advice. No keys, no orders.

Usage:
    python3 scripts/compare.py --start 2018-01-01
    python3 scripts/compare.py --granularity 1h --start 2025-01-01 --cost-bps 5
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from btcquant import backtest, data, strategies  # noqa: E402


def _ppy(granularity: str) -> int:
    return 24 * 365 if granularity == "1h" else 365


def _fmt(v: object, pct: bool = False, dp: int = 2) -> str:
    try:
        f = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "n/a"
    if math.isnan(f) or math.isinf(f):
        return "n/a"
    return f"{f * 100:.{dp}f}%" if pct else f"{f:.{dp}f}"


def _build(name: str, df: pd.DataFrame, args: argparse.Namespace, ppy: int):
    """Return (positions, prices) for a strategy, fetching extra data as needed."""
    close = df["close"]
    if name == "buy_and_hold":
        return strategies.buy_and_hold(df), close
    if name == "ma_trend_filter":
        return strategies.ma_trend_filter(df, n=args.ma_n, fast=args.ma_fast), close
    if name == "tsmom":
        return strategies.tsmom(df, lookback=args.lookback, vol_scaled=True,
                                long_short=False, target_vol=args.target_vol,
                                periods_per_year=ppy), close
    if name == "tsmom_ls":
        return strategies.tsmom(df, lookback=args.lookback, vol_scaled=True,
                                long_short=True, target_vol=args.target_vol,
                                periods_per_year=ppy), close
    if name == "pairs_coint":
        eth = data.get_ohlcv(symbol=args.eth_symbol, source=args.source,
                             granularity=args.granularity, start=args.start,
                             end=args.end, cache=not args.no_cache)
        return strategies.pairs_coint(df["close"], eth["close"]), close
    if name == "carry":
        funding = data.get_funding(symbol=args.funding_symbol, source="bybit",
                                   cache=not args.no_cache)
        pos = strategies.carry(funding)
        px = close.reindex(funding.index).ffill().bfill().dropna()
        return pos.reindex(px.index), px
    raise ValueError(name)


STRATS = ["buy_and_hold", "ma_trend_filter", "tsmom", "tsmom_ls", "pairs_coint", "carry"]


def main() -> int:
    p = argparse.ArgumentParser(
        description="Leaderboard: every strategy on the same data, ranked by Deflated Sharpe "
                    "vs buy-and-hold. Research only — no keys, no orders.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--symbol", default="BTC-USD")
    p.add_argument("--eth-symbol", default="ETH-USD")
    p.add_argument("--funding-symbol", default="BTCUSDT")
    p.add_argument("--granularity", choices=["1h", "1d"], default="1d")
    p.add_argument("--source", choices=["coinbase", "kraken", "coingecko"], default="coinbase")
    p.add_argument("--start", default="2018-01-01")
    p.add_argument("--end", default=None)
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--cost-bps", type=float, default=10.0)
    p.add_argument("--slippage-bps", type=float, default=2.0)
    p.add_argument("--ma-n", type=int, default=200)
    p.add_argument("--ma-fast", type=int, default=50)
    p.add_argument("--lookback", type=int, default=20)
    p.add_argument("--target-vol", type=float, default=0.15)
    args = p.parse_args()

    ppy = _ppy(args.granularity)
    df = data.get_ohlcv(symbol=args.symbol, source=args.source,
                        granularity=args.granularity, start=args.start,
                        end=args.end, cache=not args.no_cache)
    n_trials = len(STRATS)  # honest deflation: we are comparing this many strategies

    rows, bh_sharpe = [], float("nan")
    for name in STRATS:
        try:
            pos, px = _build(name, df, args, ppy)
            res = backtest.run(pos, px, cost_bps=args.cost_bps,
                               slippage_bps=args.slippage_bps,
                               periods_per_year=ppy, n_trials=n_trials)
            s = res["stats"]
            rows.append({
                "name": name, "cagr": s.get("cagr"), "sharpe": s.get("sharpe"),
                "dsr": s.get("deflated_sharpe"), "mdd": s.get("max_drawdown"),
                "trades": s.get("trades"),
            })
            if name == "buy_and_hold":
                bh_sharpe = float(s.get("sharpe", float("nan")))
        except Exception as exc:  # noqa: BLE001  — skip a strategy whose data is unavailable
            rows.append({"name": name, "err": str(exc)[:60]})

    ok = [r for r in rows if "err" not in r]
    ok.sort(key=lambda r: (r["dsr"] if isinstance(r["dsr"], (int, float))
                           and not math.isnan(float(r["dsr"])) else -9e9), reverse=True)
    bad = [r for r in rows if "err" in r]

    bars = len(df)
    span = f"{df.index[0].date()} -> {df.index[-1].date()}"
    print(f"\nbtc-quant leaderboard | {args.symbol} {args.granularity} | {span} | {bars} bars | "
          f"cost {args.cost_bps}+{args.slippage_bps} bps/side | N={n_trials} trials\n")
    hdr = f"{'strategy':<18}{'CAGR':>9}{'Sharpe':>9}{'DeflSR':>9}{'MaxDD':>9}{'Trades':>8}  {'beatsB&H':>8}"
    print("=" * len(hdr)); print(hdr); print("-" * len(hdr))
    for r in ok:
        beats = ""
        if r["name"] != "buy_and_hold" and isinstance(r["sharpe"], (int, float)):
            beats = "yes" if float(r["sharpe"]) > bh_sharpe else "no"
        tag = "  (baseline)" if r["name"] == "buy_and_hold" else ""
        sig = "*" if isinstance(r["dsr"], (int, float)) and float(r["dsr"]) > 0.95 else " "
        print(f"{r['name']:<18}{_fmt(r['cagr'], True):>9}{_fmt(r['sharpe']):>9}"
              f"{_fmt(r['dsr']):>8}{sig}{_fmt(r['mdd'], True):>9}{str(r['trades']):>8}  {beats:>8}{tag}")
    print("=" * len(hdr))
    for r in bad:
        print(f"  (skipped {r['name']}: {r['err']})")
    print("\n* Deflated Sharpe > 0.95 = distinguishable from luck after deflating for N trials")
    print("  (Bailey & Lopez de Prado 2014). Most strategies do NOT clear it, and many do not")
    print("  beat buy-and-hold net of cost — that is the honest result, not a bug.")
    print("  NOT FINANCIAL ADVICE - backtest != forecast. Edges decay.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
