"""diag_cost_sweep_001.py — cost sensitivity across the dimensions that actually vary.

Cost side ONLY. No return, P&L, Sharpe or equity curve is computed, read or displayed; the
strategies appear here solely as turnover and holding profiles. Tax stays out — the parameter is
held at 0 and no tax number is produced.

Runs only because `diag_funding_paired_001` closed with SETTLED as the correct funding source.
Route B is retired and is not used here.

Look: PROVENANCE DIAGNOSTIC.
"""
from __future__ import annotations
import argparse, ast, datetime as dt, importlib.util, json, sys
from pathlib import Path
import numpy as np, pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "scripts"))
from btcquant import data, costs  # noqa: E402

PERP_START = "2019-09-10"          # binancef contract birth; the perp window starts here
NOTIONAL = 100_000                 # declared: impact is 0.0000 at this size on both venues
VENUES = ["binancef", "bybit"]     # okx excluded: its API publishes 286 records, ~95 days only
FEES = [1.0, 2.0, 5.0, 10.0, 20.0]
ORDERS = ["maker", "taker"]
INSTRUMENTS = ["spot", "perp"]


def say(*a): print(*a, flush=True)


def positions(end="2026-08-04"):
    spec = importlib.util.spec_from_file_location("compare", REPO / "scripts" / "compare.py")
    compare = importlib.util.module_from_spec(spec); spec.loader.exec_module(compare)
    tree = ast.parse((REPO / "scripts" / "compare.py").read_text())
    defs = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                and n.func.attr == "add_argument":
            flags = [a.value for a in n.args if isinstance(a, ast.Constant)]
            dest = next((f.lstrip("-").replace("-", "_") for f in flags if f.startswith("--")), None)
            if dest is None:
                continue
            dv = None
            for kw in n.keywords:
                if kw.arg == "default":
                    try: dv = ast.literal_eval(kw.value)
                    except Exception: dv = None  # noqa: BLE001
                if kw.arg == "action" and getattr(kw.value, "value", None) == "store_true":
                    dv = False
            defs[dest] = dv
    args = argparse.Namespace(**defs)
    df = data.get_ohlcv(symbol="BTC-USD", source="coinbase", granularity="1d",
                        start="2010-01-01", end=end, cache=True)
    px = pd.Series(df["close"], dtype="float64")
    eth = data.get_ohlcv(symbol="ETH-USD", source="coinbase", granularity="1d",
                         start="2010-01-01", end=end, cache=True)["close"]
    out = {}
    for name in compare.RESEARCH_STRATS:
        try:
            pos = pd.Series(compare._make_positions_fn(name, args, 365, eth)(px)).astype(float).fillna(0.0)
        except Exception as e:  # noqa: BLE001
            say(f"  {name}: SKIPPED ({type(e).__name__})"); continue
        out[name] = pos.shift(1).fillna(0.0)      # the harness trades t's weight at t+1
    return out


def profile(w: pd.Series, lo=None):
    """Turnover and SIGNED exposure for a window. Signed is what funding is charged on."""
    if lo is not None:
        # the OHLCV index is tz-aware (UTC); a naive Timestamp raises on comparison rather
        # than silently coercing, which is the behaviour we want — match its tz explicitly
        cut = pd.Timestamp(lo, tz=getattr(w.index, "tz", None))
        w = w[w.index >= cut]
    if w.empty:
        return None
    yrs = len(w) / 365.0
    turn = w.diff().abs().fillna(abs(float(w.iloc[0])))
    return {"years": yrs, "legs_per_year": float(turn.sum()) / yrs,
            "signed_exposure_days_per_year": float(w.sum()) / yrs,
            "abs_exposure_days_per_year": float(w.abs().sum()) / yrs,
            "bars": len(w)}


def main() -> int:
    fh = json.loads((REPO / "reports" / "funding-history-001.json").read_text())
    say("diag_cost_sweep_001 — cost only, settled funding, route B retired")
    say(f"  perp window: {PERP_START} onward   ·   spot window: full sample")
    say(f"  notional pinned at ${NOTIONAL:,} (impact measured 0.0000 there on both venues)\n")

    pos = positions()
    prof = {n: {"spot": profile(w), "perp": profile(w, PERP_START)} for n, w in pos.items()}

    # ---- controls, before any cell is reported ---- #
    say("CONTROLS")
    z = costs.cost_model("binancef", "perp", 0.0, "maker", NOTIONAL, 0.0, +1.0, 0.0)
    ok_zero = (z["bps_per_leg"] == 0.0 and z["bps_carry"] == 0.0 and z["bps_tax"] == 0.0)
    say(f"  (i)  all terms zero -> zero: {z}  {'PASS' if ok_zero else 'FAIL'}")
    balance = sorted(prof, key=lambda n: abs(prof[n]['spot']['signed_exposure_days_per_year'])
                     / max(prof[n]['spot']['abs_exposure_days_per_year'], 1e-9))[0]
    r = abs(prof[balance]['spot']['signed_exposure_days_per_year']) / \
        max(prof[balance]['spot']['abs_exposure_days_per_year'], 1e-9)
    carry_bal = -prof[balance]["spot"]["signed_exposure_days_per_year"] * 1.0
    say(f"  (ii) most balanced long/short book is {balance!r}: |signed|/|abs| exposure = {r:.4f}, "
        f"carry at 1.0 bps/day constant funding = {carry_bal:+.2f} bps/yr  "
        f"{'PASS (near zero)' if r < 0.25 else 'NOTE: no strategy here is near-balanced'}")
    # (iii) DIRECTIONAL control — the one that catches a merged sign convention.
    bh = prof.get("buy_and_hold", {}).get("perp")
    fpd_2026 = float(fh["binancef"]["2026"]["p50"]) * 3.0
    bh_carry = +bh["signed_exposure_days_per_year"] * fpd_2026 if bh else float("nan")
    say(f"  (iii) buy_and_hold is long ~365 d/yr, so at +{fpd_2026:.4f} bps/day it must PAY: "
        f"carry = {bh_carry:+.2f} bps/yr  {'PASS' if bh_carry > 0 else 'FAIL — sign merged'}")
    if not ok_zero or not (bh_carry > 0):
        say("  a control failed — stopping"); return 2

    # ---- sweep ---- #
    rows = []
    for name, p in prof.items():
        for inst in INSTRUMENTS:
            pr = p[inst]
            if pr is None:
                continue
            for ven in VENUES:
                for fee in FEES:
                    for ot in ORDERS:
                        regimes = ([("n/a", None)] if inst == "spot" else
                                   [(f"{y}:{q}", fh[ven][y][q])
                                    for y in sorted(k for k in fh[ven] if k.isdigit())
                                    for q in ("p05", "p50", "p95")])
                        for lab, rate in regimes:
                            fpd = None if inst == "spot" else float(rate) * 3.0
                            c = costs.cost_model(ven, inst, fee, ot, NOTIONAL, 0.0, +1.0,
                                                 0.0 if inst == "perp" else None)
                            per_leg = c["bps_per_leg"]
                            fee_part = fee * pr["legs_per_year"]
                            si_part = (per_leg - fee) * pr["legs_per_year"]
                            # COST convention: costs.cost_model returns bps_carry as a P&L
                            # term (negative = you pay), while fee and spread are costs
                            # (positive = you pay). Summing them raw reported buy_and_hold as
                            # CHEAPER on a perp. carry_cost NEGATES it back to a cost.
                            carry = 0.0 if inst == "spot" else \
                                +pr["signed_exposure_days_per_year"] * fpd
                            rows.append({"strategy": name, "instrument": inst, "venue": ven,
                                         "fee": fee, "order_type": ot, "regime": lab,
                                         "fee_bps_yr": fee_part, "spread_impact_bps_yr": si_part,
                                         "carry_bps_yr": carry,
                                         "total_bps_yr": fee_part + si_part + carry})
    df = pd.DataFrame(rows)
    say(f"\n  {len(df):,} cells\n")

    # ---- variance decomposition, per strategy ---- #
    say("VARIANCE DECOMPOSITION of total cost, per strategy (eta^2, descending)")
    dec = {}
    for name, g in df.groupby("strategy"):
        tot = float(((g["total_bps_yr"] - g["total_bps_yr"].mean()) ** 2).sum())
        parts = {}
        for f in ("instrument", "venue", "fee", "order_type", "regime"):
            ss = float(sum(len(h) * (h["total_bps_yr"].mean() - g["total_bps_yr"].mean()) ** 2
                           for _, h in g.groupby(f)))
            parts[f] = ss / tot if tot > 0 else float("nan")
        dec[name] = parts
        say(f"  {name:<18}" + " · ".join(
            f"{k} {v:.1%}" for k, v in sorted(parts.items(), key=lambda kv: -kv[1])))

    # ---- item 5: cheaper instrument at p50, per year ---- #
    say("\n5. CHEAPER INSTRUMENT AT p50 FUNDING, per calendar year (binancef, taker, fee 5.0)")
    say("   spot window = full sample; perp window = 2019-09-10 onward. DIFFERENT WINDOWS.")
    yrs = sorted(k for k in fh["binancef"] if k.isdigit())
    say(f"  {'strategy':<18}" + "".join(f"{y:>7}" for y in yrs))
    per_strat = {}
    for name in sorted(prof):
        sub = df[(df.strategy == name) & (df.venue == "binancef") & (df.fee == 5.0)
                 & (df.order_type == "taker")]
        sp = sub[sub.instrument == "spot"]["total_bps_yr"]
        if sp.empty:
            continue
        sp = float(sp.iloc[0]); line = []
        for y in yrs:
            pp = sub[(sub.instrument == "perp") & (sub.regime == f"{y}:p50")]["total_bps_yr"]
            line.append("—" if pp.empty else ("spot" if sp < float(pp.iloc[0]) else "perp"))
        per_strat[name] = dict(zip(yrs, line))
        say(f"  {name:<18}" + "".join(f"{v:>7}" for v in line))
    uniq = {tuple(v.values()) for v in per_strat.values()}
    say(f"\n  distinct answer patterns across strategies: {len(uniq)}")
    if len(uniq) > 1:
        say("  -> the answer DIFFERS by strategy: instrument choice is a PER-STRATEGY decision,")
        say("     not a global one.")
    else:
        say("  -> every strategy gives the same answer in every year.")

    (REPO / "reports" / "cost-sweep-001.json").write_text(json.dumps(
        {"perp_start": PERP_START, "notional": NOTIONAL, "profiles": prof,
         "variance_decomposition": dec, "cheaper_instrument_p50": per_strat,
         "n_cells": len(df)}, indent=2, default=float) + "\n")
    df.to_json(REPO / "reports" / "cost-sweep-001-cells.json", orient="records")
    say("  result -> reports/cost-sweep-001.json (+ -cells.json)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
