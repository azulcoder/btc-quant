"""diag_turnover_census_001.py — turnover per strategy, and what the wrong constant costs.

NO RETURN, P&L, SHARPE OR EQUITY CURVE IS COMPUTED, READ OR DISPLAYED. This script never calls
`backtest.run`, `walk_forward` or `cpcv`. It calls each strategy's POSITION builder — the same
`prices -> positions` closure `scripts/compare.py` hands to the harness — and works only with
`|delta position|`. `scripts/compare.py` is imported for that builder alone and is guarded by
`if __name__ == "__main__"`, so importing it executes no backtest.

What it produces is arithmetic over an instrument property: how many legs a strategy trades per
year, how long it holds, and how far apart the OLD cost constant and the MEASURED one put its
annual cost. It does not rank anything, does not evaluate any strategy, and says nothing about
whether any of them clears any bar.

Look: PROVENANCE DIAGNOSTIC (diagnostic column).
"""
from __future__ import annotations
import argparse, importlib.util, json, sys
from pathlib import Path
import numpy as np, pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "scripts"))
from btcquant import data  # noqa: E402

OLD_PER_LEG = 12.0            # cost_bps 10.0 + slippage_bps 2.0, single-leg (quoted in backtest.py)
NEW_PER_LEG = 5.008           # fee taker 5.0 [DIASUMSIKAN] + half-spread 0.0078 + impact 0.0 <= $100k
FUNDING_PER_DAY = 1.8441      # binancef p50 x 3 [DIUKUR, DIAG-cost-ledger-001] — pending BOOK verify


def say(*a): print(*a, flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--end", default="2026-08-04")     # the pin PREREG-pbo-null-001 uses
    ap.add_argument("--start", default="2010-01-01")
    a = ap.parse_args()

    spec = importlib.util.spec_from_file_location("compare", REPO / "scripts" / "compare.py")
    compare = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(compare)                    # guarded: no backtest runs on import

    df = data.get_ohlcv(symbol="BTC-USD", source="coinbase", granularity="1d",
                        start=a.start, end=a.end, cache=True)   # start= is REQUIRED (class-H)
    px = pd.Series(df["close"], dtype="float64")
    eth = data.get_ohlcv(symbol="ETH-USD", source="coinbase", granularity="1d",
                         start=a.start, end=a.end, cache=True)["close"]
    years = len(px) / 365.0
    say(f"diag_turnover_census_001 — {len(px):,} daily bars = {years:.2f} yr "
        f"({px.index[0].date()} .. {px.index[-1].date()})")
    say("  NO returns computed. Positions only.\n")

    # compare.py builds its parser INSIDE main(), so reaching the defaults by calling it
    # would run the whole leaderboard and compute returns — exactly what this turn forbids.
    # They are lifted STATICALLY instead, by parsing the add_argument calls out of the source.
    # Nothing is executed and nothing is invented: if a parameter a strategy needs is missing,
    # the strategy raises and is reported as skipped rather than run on a guessed value.
    import ast as _ast, argparse as _ap
    tree = _ast.parse((REPO / "scripts" / "compare.py").read_text())
    defaults: dict = {}
    for node in _ast.walk(tree):
        if not (isinstance(node, _ast.Call) and isinstance(node.func, _ast.Attribute)
                and node.func.attr == "add_argument"):
            continue
        flags = [a.value for a in node.args if isinstance(a, _ast.Constant)]
        dest = next((f.lstrip("-").replace("-", "_") for f in flags if f.startswith("--")), None)
        if dest is None:
            continue
        dv = None
        for kw in node.keywords:
            if kw.arg == "default":
                try:
                    dv = _ast.literal_eval(kw.value)
                except Exception:  # noqa: BLE001
                    dv = None
            if kw.arg == "action" and getattr(kw.value, "value", None) == "store_true":
                dv = False
        defaults[dest] = dv
    args = _ap.Namespace(**defaults)
    say(f"  {len(defaults)} parameter default(s) lifted STATICALLY from compare.py "
        f"(no execution): " + ", ".join(f"{k}={v}" for k, v in sorted(defaults.items())
                                        if k in ("ma_n", "ma_fast", "lookback", "target_vol",
                                                 "vol_window", "coint_window", "z_entry",
                                                 "z_exit", "ou_window")) + "\n")

    rows = []
    for name in compare.RESEARCH_STRATS:
        try:
            fn = compare._make_positions_fn(name, args, 365, eth)
            pos = pd.Series(fn(px)).astype(float).fillna(0.0)
        except Exception as e:  # noqa: BLE001 — excluded and COUNTED, never silent
            say(f"  {name:<18} SKIPPED ({type(e).__name__}: {str(e)[:60]})")
            rows.append({"name": name, "error": f"{type(e).__name__}: {str(e)[:80]}"})
            continue
        tp = pos.shift(1).fillna(0.0)                   # the harness trades t's weight at t+1
        turn = tp.diff().abs().fillna(abs(float(tp.iloc[0])))
        legs = float(turn.sum())
        legs_yr = legs / years
        held = float((tp.abs() > 1e-12).sum())          # bars with a non-zero position
        held_days_total = held
        avg_hold = held / max(legs / 2.0, 1e-9)         # a round trip is two legs
        # TWO columns, because the venue is not settled and it changes the sign.
        # These strategies are backtested on SPOT (coinbase BTC-USD daily). Spot pays NO
        # funding. Charging perp funding to a spot backtest is a category error, and the
        # first version of this script did exactly that — it is kept as the second column
        # because the intended execution venue is an open question, not a settled one.
        old = OLD_PER_LEG * legs_yr
        new_spot = NEW_PER_LEG * legs_yr
        new_perp = NEW_PER_LEG * legs_yr + FUNDING_PER_DAY * (held_days_total / years)
        rows.append({"name": name, "legs_per_year": legs_yr, "avg_hold_days": avg_hold,
                     "days_held_per_year": held_days_total / years,
                     "cost_old_bps_yr": old,
                     "cost_new_spot_bps_yr": new_spot, "delta_spot_bps_yr": old - new_spot,
                     "cost_new_perp_bps_yr": new_perp, "delta_perp_bps_yr": old - new_perp})

    ok = [r for r in rows if "error" not in r]
    ok.sort(key=lambda r: -r["delta_spot_bps_yr"])
    say(f"  {'strategy':<18}{'legs/yr':>10}{'hold d':>9}{'days/yr':>9}"
        f"{'old':>9}{'new SPOT':>10}{'delta SPOT':>12}{'new PERP':>10}{'delta PERP':>12}")
    for r in ok:
        say(f"  {r['name']:<18}{r['legs_per_year']:>10.2f}{r['avg_hold_days']:>9.1f}"
            f"{r['days_held_per_year']:>9.1f}{r['cost_old_bps_yr']:>9.1f}"
            f"{r['cost_new_spot_bps_yr']:>10.1f}{r['delta_spot_bps_yr']:>12.1f}"
            f"{r['cost_new_perp_bps_yr']:>10.1f}{r['delta_perp_bps_yr']:>12.1f}")
    say(f"\n  old      = {OLD_PER_LEG} x legs/yr           (charges NO funding at all)")
    say(f"  new SPOT = {NEW_PER_LEG} x legs/yr          (what the board is actually backtested on)")
    say(f"  new PERP = {NEW_PER_LEG} x legs/yr + {FUNDING_PER_DAY} x days-held/yr")
    say(f"  n with |delta SPOT| > 50 bps/yr: "
        f"{sum(1 for r in ok if abs(r['delta_spot_bps_yr']) > 50)} of {len(ok)}")
    say("  Pure arithmetic on turnover. No strategy was evaluated and no return was read.")

    p = REPO / "reports" / "turnover-census-001.json"
    p.write_text(json.dumps({"end": a.end, "bars": len(px), "years": years,
                             "old_per_leg": OLD_PER_LEG, "new_per_leg": NEW_PER_LEG,
                             "funding_per_day": FUNDING_PER_DAY, "rows": rows},
                            indent=2, default=float) + "\n")
    say(f"  result -> {p.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
