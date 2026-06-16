#!/usr/bin/env python3
"""reversion_sweep.py — gated vs ungated mean_reversion, OOS on 1d + 1h.

Tests the pre-registered hypothesis (RESEARCH-reversion-runlog.md): does the ranging-regime
gate (Hurst<0.45 AND ADX<22) turn a negative-expectancy price-reversion fade positive? A/B:
the ONLY difference between the two rows per timeframe is `gated`. Scored on walk-forward OOS
Deflated Sharpe (folds-as-trials) + the forward-IC profile. Net of cost. Research-only — a
candidate joins the board ONLY if the gated variant clears OOS DSR>0.95 AND beats its ungated
twin. Run: `python3 scripts/reversion_sweep.py`.
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from btcquant import backtest, data, ic, strategies  # noqa: E402


def _fmt(v, pct: bool = False) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "n/a"
    if math.isnan(f) or math.isinf(f):
        return "n/a"
    return f"{f * 100:.2f}%" if pct else f"{f:.3f}"


def _run_tf(symbol: str, granularity: str, ppy: int, folds: int, start: str) -> None:
    try:
        df = data.get_ohlcv(symbol=symbol, granularity=granularity, start=start, source="coinbase")
    except Exception as exc:  # noqa: BLE001
        print(f"  {granularity}: data unavailable ({str(exc)[:60]})")
        return
    close = df["close"]
    span = f"{df.index[0].date()} -> {df.index[-1].date()}"
    print(f"\n[{granularity}]  {len(df)} bars  {span}  ({folds} folds, ppy={ppy})")
    print(f"  {'variant':<10}{'OOS DSR':>9}{'OOS SR':>9}{'OOS MaxDD':>11}{'IC k=1':>9}{'%in-trade':>10}  verdict")
    print("  " + "-" * 70)
    results = {}
    for gated in (False, True):
        pos = strategies.mean_reversion(df, gated=gated)
        wf = backtest.walk_forward(lambda px, p=pos: p.reindex(px.index), close,
                                   n_splits=folds, periods_per_year=ppy)
        o, op = wf["oos"], wf["oos_positions"]
        prof = ic.ic_profile(op, close.reindex(op.index), horizons=(1, 3, 5, 10), method="spearman")
        dsr = o.get("deflated_sharpe")
        ic1 = prof.get(1, {})
        ic1s = (f"{ic1.get('ic'):+.3f}{'*' if ic1.get('significant') else ''}"
                if isinstance(ic1.get("ic"), (int, float)) and ic1.get("ic") == ic1.get("ic") else "n/a")
        in_trade = float((pos.fillna(0.0) != 0.0).mean()) * 100.0
        survives = isinstance(dsr, (int, float)) and dsr == dsr and dsr > 0.95
        results[gated] = (dsr, survives)
        label = "gated" if gated else "ungated"
        print(f"  {label:<10}{_fmt(dsr):>9}{_fmt(o.get('sharpe')):>9}{_fmt(o.get('max_drawdown'), True):>11}"
              f"{ic1s:>9}{in_trade:>9.0f}%  {'SURVIVES' if survives else 'KILL (≤0.95)'}")
    gd, gs = results.get(True, (float('nan'), False))
    ud, _ = results.get(False, (float('nan'), False))
    beats = isinstance(gd, (int, float)) and isinstance(ud, (int, float)) and gd == gd and ud == ud and gd > ud
    print(f"  → gate verdict: gated {'BEATS' if beats else 'does NOT beat'} ungated on OOS DSR "
          f"({_fmt(gd)} vs {_fmt(ud)}); "
          f"{'promotable — re-verify' if (gs and beats) else 'KILL — gate adds no validated OOS edge'}.")


def main() -> int:
    folds = 5
    print("REGIME-GATED MEAN REVERSION — gated vs ungated A/B (RESEARCH-reversion-runlog.md)")
    print("kill = gated OOS DSR>0.95 AND gated beats ungated; else KILL (off board).")
    _run_tf("BTC-USD", "1d", 365, folds, "2018-01-01")
    _run_tf("BTC-USD", "1h", 24 * 365, folds, "2023-06-01")
    print("\n(IC k=1: Spearman rank corr of signalₜ vs forward return; * = 95% significant.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
