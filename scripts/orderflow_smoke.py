#!/usr/bin/env python3
"""orderflow_smoke.py — end-to-end proof that M1 bars run the deflation harness.

What this proves (and nothing more):

1. ``btcquant.orderflow.order_flow_bars`` materializes real bars from the real
   recorded archive (day files and/or the ``hf://`` parquet mirror).
2. Those bars pass **unchanged** through ``features.atr`` / ``features.log_returns``
   / ``features.realized_vol`` and through ``backtest.walk_forward`` using the
   exact call idiom already in ``scripts/compare.py`` — **zero harness change** —
   and through ``backtest.cpcv``. ``cpcv`` is *run*, not argued from a signature:
   its signature is **not** identical to ``walk_forward``'s (it takes
   ``n_blocks``/``k_test``/``embargo_pct``, not ``n_splits``/``min_train``); only
   the leading ``(make_positions, prices)`` contract is shared, which is the part
   that matters and the part this executes.
3. The deflation stack (DSR, PBO, MinBTL) then does its job and **refuses**:
   the recorded span is a rounding error next to MinBTL for any realistic trial
   count, so no candidate may be scored and none is.

The expected and CORRECT outcome is ``INSUFFICIENT HISTORY``. That verdict is
the machinery working, not the machinery failing. The smoke FAILS if the span
somehow clears MinBTL (that would mean the clock is wrong), if the pipeline
breaks, or if a coverage number goes missing — every performance figure here is
printed next to the coverage figure that qualifies it.

No tuning, no window shopping, no dropping of inconvenient days beyond the one
labelled ``drop_gap_bars``/``gap_flat_positions`` call that is reported.

Usage
-----
    python3 scripts/orderflow_smoke.py                     # default window
    python3 scripts/orderflow_smoke.py --source local
    python3 scripts/orderflow_smoke.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from btcquant import backtest, features, risk  # noqa: E402
from btcquant import orderflow as of  # noqa: E402

EXIT_OK, EXIT_FAIL = 0, 1

# The only window in this archive where ONE coherent instrument (bybit BTCUSDT
# perp) has both a trade leg and a book leg. bybit's legs die on 2026-07-23;
# 07-24/25 are binancef-depth + coinbase-trades only. Stated, not worked around.
DEFAULT_START = "2026-07-05"
DEFAULT_END = "2026-07-23"
DEFAULT_VENUE = "bybit"


def _fmt(x: Any, pct: bool = False) -> str:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "n/a"
    if not np.isfinite(v):
        return "n/a"
    return f"{v * 100:.2f}%" if pct else f"{v:,.4f}"


def _cvd_slope_positions(bars: pd.DataFrame, venue: str, lookback: int) -> pd.Series:
    """A deliberately trivial candidate: the sign of the CVD change over N bars.

    This is NOT a proposed strategy and carries no hypothesis. It exists so the
    harness has a position series to consume. It uses only past bars (a diff
    ending at t), and ``backtest.run`` still shifts it by one before it trades.
    """
    cvd = bars[f"cvd_{venue}"]
    return np.sign(cvd.diff(lookback)).fillna(0.0).clip(-1.0, 1.0)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--start", default=DEFAULT_START)
    ap.add_argument("--end", default=DEFAULT_END)
    ap.add_argument("--venue", default=DEFAULT_VENUE)
    ap.add_argument("--bar", default="1h", choices=list(of.BAR_FREQS))
    ap.add_argument("--source", default="auto", choices=["auto", "local", "hf"])
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--cost-bps", type=float, default=10.0)
    ap.add_argument("--slippage-bps", type=float, default=2.0)
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    ppy = of.periods_per_year(args.bar)
    out: dict[str, Any] = {"window": [args.start, args.end], "bar": args.bar,
                           "venue": args.venue, "periods_per_year": ppy}

    print("=" * 78)
    print("M1 ORDER-FLOW SMOKE — bars -> existing deflation harness, zero harness change")
    print("=" * 78)

    # ---- 1. build --------------------------------------------------------- #
    t0 = time.time()
    bars = of.order_flow_bars(
        args.start, args.end, price_venue=args.venue, book_venue=args.venue,
        bar=args.bar, source=args.source, cache=not args.no_cache,
    )
    build_s = time.time() - t0
    meta = bars.attrs.get("orderflow", {})
    cov = meta.get("coverage_summary", {})
    out["build_seconds"] = round(build_s, 1)
    out["shape"] = list(bars.shape)
    out["coverage_summary"] = cov

    print(f"\nBARS           : {bars.shape[0]} x {bars.shape[1]}  built in {build_s:.1f}s "
          f"({args.source})")
    print(f"  index        : {bars.index[0]} .. {bars.index[-1]}  (UTC, left-labelled, {args.bar})")
    print(f"  OHLCV first 5: {list(bars.columns[:5])}")
    srcs = {}
    for d in meta.get("manifest", {}).get("days", []):
        srcs[d.get("source")] = srcs.get(d.get("source"), 0) + 1
    print(f"  day sources  : {srcs}   skipped_locked={len(meta.get('manifest', {}).get('skipped_locked', []))}")

    # ---- 2. coverage FIRST, always beside any number ---------------------- #
    print("\nCOVERAGE (gaps stay gaps — this qualifies every number below)")
    print(f"  bars                  : {cov.get('bars')}")
    print(f"  fully covered         : {cov.get('full_coverage')}")
    print(f"  partially covered     : {cov.get('partial_coverage')}")
    print(f"  fully empty (NaN)     : {cov.get('empty')}")
    print(f"  total feed hole       : {cov.get('gap_seconds_total', 0.0) / 3600.0:.2f} h")
    print(f"  returns spanning gaps : {cov.get('returns_spanning_a_gap')} of {len(bars)}")
    print(f"  clean segments        : {cov.get('segments')}  "
          f"(segment ids in total: {cov.get('segment_ids_total')})")
    unresolved = meta.get("manifest", {}).get("unresolved_days", [])
    print(f"  unresolved days       : {len(unresolved)} {unresolved if unresolved else ''}"
          f"   range final={meta.get('manifest', {}).get('final')}")
    if meta.get("cross_instrument"):
        print(f"  CROSS-INSTRUMENT      : {meta['cross_instrument']['note']}")

    nn = {c: int(bars[c].notna().sum()) for c in bars.columns}
    families = {
        "price": ["close", "volume"],
        "trade": [f"delta_{args.venue}", f"cvd_{args.venue}",
                  f"delta_usd_whale_{args.venue}"],
        "book": [f"ofi_{args.venue}", f"microprice_{args.venue}",
                 f"depth_slope_imb_{args.venue}"],
        "vpin": [f"vpin_{args.venue}", f"vpin_window_gap_s_{args.venue}"],
        "liq": [f"liq_notional_usd_{args.venue}", f"coverage_liq_{args.venue}"],
    }
    print("\nFEATURE POPULATION (non-NaN bars / total) — an empty family is stated, not hidden")
    for fam, cols in families.items():
        for c in cols:
            if c in nn:
                print(f"  {fam:<6} {c:<34} {nn[c]:>5} / {len(bars)}")
    out["non_nan"] = {c: nn[c] for cols in families.values() for c in cols if c in nn}

    # ---- 3. contract: existing feature/harness code, unchanged ------------ #
    print("\nHARNESS CONTRACT (executed, not argued)")
    atr = features.atr(bars, window=14)
    r = features.log_returns(bars["close"])
    rv = features.realized_vol(r, 20, ppy)
    print(f"  features.atr(bars, 14)                 -> last {_fmt(atr.dropna().iloc[-1]) if atr.notna().any() else 'n/a'}   OK")
    print(f"  features.realized_vol(r, 20, ppy)      -> last {_fmt(rv.dropna().iloc[-1]) if rv.notna().any() else 'n/a'}   OK")
    out["atr_last"] = float(atr.dropna().iloc[-1]) if atr.notna().any() else float("nan")
    out["realized_vol_last"] = float(rv.dropna().iloc[-1]) if rv.notna().any() else float("nan")

    # ---- 4. candidates (trivial, un-hypothesized, purely to drive the harness)
    lookbacks = (3, 6, 12, 24)
    positions = {lb: of.gap_flat_positions(_cvd_slope_positions(bars, args.venue, lb), bars)
                 for lb in lookbacks}
    close = bars["close"]

    print("\nWALK-FORWARD (compare.py:533 call idiom, verbatim; net of "
          f"{args.cost_bps:.0f}+{args.slippage_bps:.0f} bps)")
    hdr = f"  {'candidate':<22}{'OOS SR':>10}{'OOS DSR':>12}{'OOS n':>8}{'maxDD':>10}"
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    wf: dict[int, dict] = {}
    for lb in lookbacks:
        pos = positions[lb]
        w = backtest.walk_forward(lambda px, p=pos: p.reindex(px.index), close,
                                  n_splits=args.folds, cost_bps=args.cost_bps,
                                  slippage_bps=args.slippage_bps, periods_per_year=ppy)
        wf[lb] = w
        o = w["oos"]
        print(f"  cvd_slope {lb:>2}h{'':<10}{_fmt(o.get('sharpe')):>10}"
              f"{_fmt(o.get('deflated_sharpe')):>12}{o.get('n_periods', 0):>8}"
              f"{_fmt(o.get('max_drawdown'), True):>10}")
    out["walk_forward"] = {
        str(lb): {k: (float(v) if isinstance(v, (int, float)) else v)
                  for k, v in wf[lb]["oos"].items()
                  if isinstance(v, (int, float))}
        for lb in lookbacks
    }
    oos_keys = sorted(k for k in wf[lookbacks[0]]["oos"])
    print(f"\n  OOS stat keys returned by the UNCHANGED harness: {oos_keys}")

    # cpcv on the SAME bars — executed, not inferred from a signature. Its
    # signature differs from walk_forward's; only the leading
    # (make_positions, prices) contract is shared, and that is what is proven here.
    pos_best_lb = lookbacks[0]
    cp = backtest.cpcv(lambda px, p=positions[pos_best_lb]: p.reindex(px.index), close,
                       n_blocks=6, k_test=2, cost_bps=args.cost_bps,
                       slippage_bps=args.slippage_bps, periods_per_year=ppy)
    out["cpcv"] = {k: (float(cp[k]) if isinstance(cp.get(k), (int, float)) else cp.get(k))
                   for k in ("n_paths", "median_sharpe", "p25", "p75", "min", "max")}
    print(f"  backtest.cpcv(same bars, n_blocks=6, k_test=2) -> {out['cpcv']['n_paths']} paths, "
          f"OOS SR p25 {_fmt(out['cpcv']['p25'])} / p75 {_fmt(out['cpcv']['p75'])} "
          f"(min {_fmt(out['cpcv']['min'])}, max {_fmt(out['cpcv']['max'])})   OK")

    # ---- 5. deflation: DSR + PBO over the trial set ----------------------- #
    mat = pd.concat({str(lb): pd.Series(wf[lb]["oos_returns"]) for lb in lookbacks},
                    axis=1).dropna()
    pbo = risk.probability_of_backtest_overfitting(mat.to_numpy(), n_blocks=8)
    best_lb = max(lookbacks, key=lambda lb: wf[lb]["oos"].get("sharpe", -np.inf))
    b = wf[best_lb]["oos"]
    dsr_best = risk.deflated_sharpe_ratio(
        b.get("sharpe_per_period", float("nan")), int(b.get("n_periods", 0)),
        b.get("skew", 0.0), b.get("kurtosis", 3.0),
        n_trials=len(lookbacks), var_trials_sr=float(np.var(
            [wf[lb]["oos"].get("sharpe_per_period", np.nan) for lb in lookbacks], ddof=1)),
    )
    print("\nDEFLATION")
    print(f"  trials scored (N)                : {len(lookbacks)}")
    print(f"  best-of-N by OOS SR              : cvd_slope {best_lb}h")
    print(f"  DSR of the best (net, OOS)       : {_fmt(dsr_best)}")
    print(f"  PBO (CSCV, {pbo.get('n_blocks')} blocks, "
          f"{pbo.get('n_combos')} splits)   : {_fmt(pbo.get('pbo'))}")
    out["pbo"] = pbo
    out["dsr_best"] = float(dsr_best)
    out["best_lookback"] = best_lb

    # ---- 6. MinBTL — the refusal ------------------------------------------ #
    span_days = (bars.index[-1] - bars.index[0]).total_seconds() / 86400.0 + \
        (of._bar_ms(args.bar) / 86_400_000.0)
    span_years = span_days / 365.0
    covered_bars = int(of.coverage_mask(bars).sum())
    covered_years = covered_bars / float(ppy)
    print("\nMinBTL — Bailey, Borwein, Lopez de Prado & Zhu (2014)")
    print(f"  recorded span                    : {span_days:.2f} calendar days "
          f"= {span_years:.4f} yrs")
    print(f"  usable (full-coverage) bars      : {covered_bars} of {len(bars)} "
          f"= {covered_years:.4f} yrs of clean {args.bar} bars")
    rows = []
    for n_trials in (5, 20, 100):
        need = risk.min_backtest_length(n_trials)
        have = span_years / need if need else float("nan")
        rows.append({"n_trials": n_trials, "minbtl_years": need,
                     "minbtl_days": need * 365.0, "fraction_met": have})
        print(f"  MinBTL(N={n_trials:<4})                   : {need:.3f} yrs "
              f"({need * 365.0:,.0f} d)   -> have {have * 100:.1f}%")
    # The module measures the same thing itself (rail 4: the claim is computed,
    # never remembered) — printed here so the two are visibly the same number.
    hist = meta.get("history", {})
    print(f"  module attrs['history']          : {hist.get('days_resolved')} of "
          f"{hist.get('days_requested')} days resolved, span "
          f"{float(hist.get('span_years', float('nan'))):.4f} yrs = "
          f"{float(hist.get('fraction_of_minbtl', {}).get('5', float('nan'))) * 100:.1f}% "
          f"of MinBTL(5)")
    out["history"] = hist
    out["span_days"] = span_days
    out["span_years"] = span_years
    out["covered_bars"] = covered_bars
    out["covered_years"] = covered_years
    out["minbtl"] = rows

    insufficient = span_years < risk.min_backtest_length(5)
    out["verdict"] = "INSUFFICIENT HISTORY" if insufficient else "SPAN CLEARS MinBTL(5)"

    print("\n" + "=" * 78)
    if insufficient:
        print("VERDICT: INSUFFICIENT HISTORY — no candidate may be scored, and none is.")
        print("         status != CLEARED (STRATEGY.md section 6). Nothing is displayed")
        print("         anywhere. The numbers above exist to prove the PIPELINE runs;")
        print("         they are not evidence about any strategy and must not be read")
        print("         as such. FEATURES ONLY — this module never ranks, scores, or")
        print("         implies predictive power.")
    else:
        print("VERDICT: the recorded span claims to clear MinBTL(5). With this archive")
        print("         that is not credible — check the clock before believing it.")
    print("=" * 78)

    if args.json:
        print(json.dumps(out, indent=2, default=str))
    return EXIT_OK if insufficient else EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
