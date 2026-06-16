#!/usr/bin/env python3
"""Strategy leaderboard — every strategy on the same BTC data, ranked by the
**out-of-sample** Deflated Sharpe (walk-forward), net of cost, vs buy-and-hold.

This is the honest centrepiece. The ranking is NOT the in-sample fit: each strategy
is evaluated walk-forward (fit on each in-sample block, traded on the *next*
out-of-sample block), and the headline Deflated Sharpe is computed on the
concatenated OOS returns, deflated for the number of strategies searched (Bailey &
López de Prado 2014). Two selection-overfit guards are reported alongside:

  * PBO (Probability of Backtest Overfitting, CSCV) — how often "keep the backtest
    winner" would have picked an OOS underperformer. >~0.5 ⇒ the ranking is noise.
  * MinBTL (Minimum Backtest Length) — flags when the history is too short for the
    number of configurations tried.

Most strategies do NOT clear OOS DSR 0.95 and many do not beat buy-and-hold net of
cost out-of-sample; that is the point.

Research / backtest only. Not financial advice. No keys, no orders.

Usage:
    python3 scripts/compare.py --start 2018-01-01
    python3 scripts/compare.py --granularity 1h --start 2025-01-01 --folds 6
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from btcquant import backtest, data, features, ic, risk, strategies  # noqa: E402


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


def _funding_periods_per_year(index) -> int:
    """Funding intervals per year from the median stamp spacing (≈1095 for an 8h cadence).
    Carry stats must annualize on the funding clock, not the spot bar count (audit M3)."""
    try:
        sec = pd.Series(index).diff().dropna().median().total_seconds()
        return int(round(365.25 * 24 * 3600 / sec)) if sec and sec > 0 else 1095
    except Exception:  # noqa: BLE001
        return 1095


# Spot, walk-forward-able directional strategies (the OOS leaderboard). `carry` is
# perp-funding-indexed (8h), not daily spot, and our keyless funding history is far
# too short for an honest OOS — it is reported descriptively below, not ranked.
SPOT_STRATS = ["buy_and_hold", "ma_trend_filter", "tsmom", "tsmom_ls", "pairs_coint"]

# Pre-registered Part B candidates, evaluated only under --research (kept OFF the
# public board until one clears its kill criterion — see RESEARCH-partB-runlog.md).
RESEARCH_STRATS = SPOT_STRATS + ["tsmom_dir", "tsmom_voltarget", "pairs_ou"]


def _make_positions_fn(name: str, args: argparse.Namespace, ppy: int, eth_close):
    """Return a ``prices -> positions`` builder for walk_forward/cpcv."""
    if name == "buy_and_hold":
        return lambda px: strategies.buy_and_hold(pd.DataFrame({"close": px}))
    if name == "ma_trend_filter":
        return lambda px: strategies.ma_trend_filter(pd.DataFrame({"close": px}),
                                                     n=args.ma_n, fast=args.ma_fast)
    if name == "tsmom":
        return lambda px: strategies.tsmom(pd.DataFrame({"close": px}), lookback=args.lookback,
                                           vol_scaled=True, long_short=False,
                                           target_vol=args.target_vol, periods_per_year=ppy)
    if name == "tsmom_ls":
        return lambda px: strategies.tsmom(pd.DataFrame({"close": px}), lookback=args.lookback,
                                           vol_scaled=True, long_short=True,
                                           target_vol=args.target_vol, periods_per_year=ppy)
    if name == "pairs_coint":
        if eth_close is None:
            raise ValueError("no ETH data for pairs")
        return lambda px: strategies.pairs_coint(px, eth_close.reindex(px.index).ffill().bfill())
    # ── Part B research candidates (pre-registered; not on the public board) ──
    if name == "tsmom_dir":   # B1 baseline: raw directional momentum, NO sizing
        return lambda px: strategies.tsmom(pd.DataFrame({"close": px}), lookback=args.lookback,
                                           vol_scaled=False, long_short=False)
    if name == "tsmom_voltarget":   # B1 candidate: vol-target overlay on directional tsmom
        return lambda px: strategies.vol_target(
            strategies.tsmom(pd.DataFrame({"close": px}), lookback=args.lookback,
                             vol_scaled=False, long_short=False),
            pd.DataFrame({"close": px}), target_vol=args.target_vol,
            periods_per_year=ppy, max_leverage=2.0)
    if name == "pairs_ou":   # B2 candidate: OU-σ_eq normalizer instead of empirical z
        if eth_close is None:
            raise ValueError("no ETH data for pairs_ou")
        return lambda px: strategies.pairs_ou(px, eth_close.reindex(px.index).ffill().bfill())
    raise ValueError(name)


def main() -> int:
    p = argparse.ArgumentParser(
        description="OOS leaderboard: every strategy walk-forward-validated on the same data, "
                    "ranked by out-of-sample Deflated Sharpe vs buy-and-hold. Research only.",
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
    p.add_argument("--folds", type=int, default=5, help="walk-forward OOS folds")
    p.add_argument("--ma-n", type=int, default=200)
    p.add_argument("--ma-fast", type=int, default=50)
    p.add_argument("--lookback", type=int, default=20)
    p.add_argument("--target-vol", type=float, default=0.15)
    p.add_argument("--research", action="store_true",
                   help="also evaluate the pre-registered Part B candidates (B1/B2) and print "
                        "their verdict vs baseline; does NOT change the public board.")
    args = p.parse_args()

    ppy = _ppy(args.granularity)
    df = data.get_ohlcv(symbol=args.symbol, source=args.source, granularity=args.granularity,
                        start=args.start, end=args.end, cache=not args.no_cache)
    close = df["close"]
    try:
        eth_close = data.get_ohlcv(symbol=args.eth_symbol, source=args.source,
                                   granularity=args.granularity, start=args.start,
                                   end=args.end, cache=not args.no_cache)["close"]
    except Exception:  # noqa: BLE001
        eth_close = None

    strat_list = RESEARCH_STRATS if args.research else SPOT_STRATS
    n_trials = len(strat_list)   # selection-count deflation (best of this many)
    oos_vol = features.realized_vol(features.simple_returns(close), 30, ppy)  # for Tharp R-multiples
    rows, oos_by_name, bh_oos_sharpe = [], {}, float("nan")
    for name in strat_list:
        try:
            wf = backtest.walk_forward(_make_positions_fn(name, args, ppy, eth_close), close,
                                       n_splits=args.folds, cost_bps=args.cost_bps,
                                       slippage_bps=args.slippage_bps, periods_per_year=ppy)
            oos, is_ = wf["oos"], wf["is_"]
            oos_by_name[name] = wf["oos_returns"]
            # OOS DSR deflated for N strategies (mirrors the selection-count deflation),
            # computed on the held-out OOS returns rather than the in-sample fit.
            np_ = int(oos.get("n_periods", 0))
            oos_dsr = risk.deflated_sharpe_ratio(
                oos.get("sharpe_per_period", float("nan")), np_,
                oos.get("skew", float("nan")), oos.get("kurtosis", float("nan")),
                n_trials, 1.0 / np_ if np_ > 0 else float("nan"))
            # Tharp OOS expectancy / R-multiple on the held-out positions (vol-notional R,
            # k=2σ — see RESEARCH-tharp-runlog.md). Evaluation layer, NOT a signal.
            op = wf["oos_positions"]
            er = risk.expectancy_report(op, close.reindex(op.index),
                                        oos_vol.reindex(op.index), periods_per_year=ppy, k=2.0)
            # Forward IC: does the OOS signal actually LEAD returns? (Spearman rank IC of
            # position_t vs forward return t→t+k; evaluation only, no look-ahead.)
            op_px = close.reindex(op.index)
            ic_prof = ic.ic_profile(op, op_px, horizons=(1, 3, 5, 10), method="spearman")
            icir = ic.ic_ir(op, op_px, k=3, block=21, method="spearman")
            rows.append({"name": name, "oos_cagr": oos.get("cagr"), "oos_sharpe": oos.get("sharpe"),
                         "is_sharpe": is_.get("sharpe", float("nan")), "oos_dsr": oos_dsr,
                         "oos_mdd": oos.get("max_drawdown"),
                         "exp_r": er["expectancy_r"], "sqn": er["sqn"], "win": er["win_rate"], "ntr": er["n_trades"],
                         "ic": ic_prof, "icir_t": icir["t_stat"]})
            if name == "buy_and_hold":
                bh_oos_sharpe = float(oos.get("sharpe", float("nan")))
        except Exception as exc:  # noqa: BLE001
            rows.append({"name": name, "err": str(exc)[:60]})

    ok = [r for r in rows if "err" not in r]
    ok.sort(key=lambda r: (r["oos_dsr"] if isinstance(r["oos_dsr"], (int, float))
                           and not math.isnan(float(r["oos_dsr"])) else -9e9), reverse=True)
    bad = [r for r in rows if "err" in r]

    # PBO across the OOS-returns matrix (cross-strategy selection overfit).
    pbo = {"pbo": float("nan"), "n_combos": 0}
    if len(oos_by_name) >= 2:
        mat = pd.concat(oos_by_name, axis=1).dropna()
        if mat.shape[0] > 8 and mat.shape[1] >= 2:
            pbo = risk.probability_of_backtest_overfitting(mat.to_numpy(), n_blocks=8)

    years = (close.index[-1] - close.index[0]).days / 365.25
    minbtl = risk.min_backtest_length(n_trials)

    bars, span = len(df), f"{df.index[0].date()} -> {df.index[-1].date()}"
    print(f"\nbtc-quant OOS leaderboard | {args.symbol} {args.granularity} | {span} | {bars} bars | "
          f"{args.folds} walk-forward folds | cost {args.cost_bps}+{args.slippage_bps} bps/side | "
          f"N={n_trials} trials\n")
    hdr = (f"{'strategy':<18}{'OOS CAGR':>10}{'OOS SR':>9}{'IS SR':>9}{'OOS DSR':>10}"
           f"{'OOS MaxDD':>11}  {'beats B&H':>9}{'OOS ExpR':>9}{'SQN':>7}{'Win%':>7}{'#T':>5}")
    print("=" * len(hdr)); print(hdr); print("-" * len(hdr))
    for r in ok:
        beats = ""
        if r["name"] != "buy_and_hold" and isinstance(r["oos_sharpe"], (int, float)):
            beats = "yes" if float(r["oos_sharpe"]) > bh_oos_sharpe else "no"
        tag = "  (baseline)" if r["name"] == "buy_and_hold" else ""
        sig = "*" if isinstance(r["oos_dsr"], (int, float)) and float(r["oos_dsr"]) > 0.95 else " "
        # Expectancy/Win% need adequate N to mean anything — suppress < 5 trades (e.g.
        # always-in buy & hold = 1 degenerate trade) so the readout never misleads.
        lowN = r.get("ntr", 0) < 5
        expr = "—" if lowN else _fmt(r.get("exp_r"))
        sqn = "—" if lowN else _fmt(r.get("sqn"))
        win = "—" if lowN else (f"{r['win']*100:.0f}%" if isinstance(r.get("win"), (int, float)) and r["win"] == r["win"] else "—")
        print(f"{r['name']:<18}{_fmt(r['oos_cagr'], True):>10}{_fmt(r['oos_sharpe']):>9}"
              f"{_fmt(r['is_sharpe']):>9}{_fmt(r['oos_dsr']):>9}{sig}{_fmt(r['oos_mdd'], True):>11}"
              f"  {beats:>9}{expr:>9}{sqn:>7}{win:>7}{r.get('ntr',0):>5}")
    print("=" * len(hdr))
    for r in bad:
        print(f"  (skipped {r['name']}: {r['err']})")

    # Selection-overfit guards.
    print(f"\nPBO (selection overfit, CSCV {pbo.get('n_combos', 0)} splits): "
          f"{_fmt(pbo.get('pbo'))}   "
          f"[>0.50 ⇒ the ranking is essentially noise]")
    short = isinstance(minbtl, float) and not math.isnan(minbtl) and years < minbtl
    print(f"MinBTL for N={n_trials}: {_fmt(minbtl)} yrs vs {years:.1f} yrs of data"
          + ("   ⚠ UNDER-POWERED: history shorter than MinBTL" if short else "   (ok)"))

    # ── Lead-time IC: does the OOS signal actually LEAD returns? ──────────────────
    print("\nLEAD-TIME IC (OOS, Spearman rank corr of signalₜ vs forward return t→t+k; "
          "* = significant at 95%, |IC| > 1.96·√(k/N) overlap-corrected):")
    ich = (f"{'strategy':<18}{'IC k=1':>10}{'IC k=3':>10}{'IC k=5':>10}{'IC k=10':>10}"
           f"{'IC-IR t(k=3)':>14}")
    print(ich); print("-" * len(ich))

    def _icCell(prof, k):
        d = (prof or {}).get(k, {})
        v = d.get("ic")
        if not isinstance(v, (int, float)) or v != v:
            return "n/a"
        return f"{v:+.3f}{'*' if d.get('significant') else ''}"

    for r in ok:
        prof = r.get("ic")
        if not prof:
            continue
        print(f"{r['name']:<18}{_icCell(prof, 1):>10}{_icCell(prof, 3):>10}"
              f"{_icCell(prof, 5):>10}{_icCell(prof, 10):>10}{_fmt(r.get('icir_t')):>14}")
    print("  IC = the per-bet LEADING skill (Grinold-Kahn: IR ≈ IC·√breadth). Near-zero / "
          "insignificant ⇒ the signal does NOT lead returns OOS — whatever the equity curve shows.")
    print("  (buy_and_hold has a constant signal ⇒ IC undefined; that is correct, not a bug.)")

    # ── Part B verdicts (pre-registered kill criteria; --research only) ──────────
    if args.research:
        dsr_by = {r["name"]: r.get("oos_dsr") for r in rows if "err" not in r}

        def _num(v):
            try:
                f = float(v)
                return f if not (math.isnan(f) or math.isinf(f)) else None
            except (TypeError, ValueError):
                return None

        def _pbo_over(names):
            cols = {k: oos_by_name[k] for k in names if k in oos_by_name}
            if len(cols) < 2:
                return None
            m = pd.concat(cols, axis=1).dropna()
            if m.shape[0] > 8 and m.shape[1] >= 2:
                return _num(risk.probability_of_backtest_overfitting(m.to_numpy(), n_blocks=8).get("pbo"))
            return None

        print("\n" + "─" * 78)
        print("PART B — pre-registered candidate verdicts (judged on OOS DSR / PBO):")
        print("─" * 78)

        # B1: vol-target overlay vs raw directional tsmom (+ near-duplicate check vs board tsmom).
        d_vt, d_dir = _num(dsr_by.get("tsmom_voltarget")), _num(dsr_by.get("tsmom_dir"))
        corr = None
        try:
            pos_vt = _make_positions_fn("tsmom_voltarget", args, ppy, eth_close)(close)
            pos_ts = _make_positions_fn("tsmom", args, ppy, eth_close)(close)
            dfc = pd.DataFrame({"vt": pos_vt, "ts": pos_ts}).dropna()
            corr = float(dfc["vt"].corr(dfc["ts"])) if len(dfc) > 2 else None
        except Exception:  # noqa: BLE001
            corr = None
        delta1 = (d_vt - d_dir) if (d_vt is not None and d_dir is not None) else None
        dup = corr is not None and abs(corr) > 0.95
        kill1 = (delta1 is None) or (delta1 < 0.05) or dup
        print(f"\nB1 tsmom_voltarget: OOS DSR {_fmt(d_vt)} vs raw directional {_fmt(d_dir)} "
              f"(Δ {_fmt(delta1)})  ·  corr vs board tsmom {_fmt(corr)}")
        print(f"   KILL CRITERION: Δ<+0.05 OR |corr|>0.95  →  "
              + ("KILL — " + ("near-duplicate of the board's vol-scaled tsmom; " if dup else "")
                 + "tail-control-only, NOT promoted." if kill1
                 else "SURVIVES — candidate for promotion (re-check parity before adding)."))

        # B2: OU-σ_eq normalizer vs fixed-z pairs (+ does it make selection more overfit?).
        d_ou, d_fz = _num(dsr_by.get("pairs_ou")), _num(dsr_by.get("pairs_coint"))
        delta2 = (d_ou - d_fz) if (d_ou is not None and d_fz is not None) else None
        pbo_board, pbo_with = _pbo_over(SPOT_STRATS), _pbo_over(SPOT_STRATS + ["pairs_ou"])
        worse = (pbo_board is not None and pbo_with is not None and pbo_with > pbo_board)
        kill2 = (delta2 is None) or (delta2 < 0.05) or worse
        print(f"\nB2 pairs_ou: OOS DSR {_fmt(d_ou)} vs fixed-z pairs {_fmt(d_fz)} (Δ {_fmt(delta2)})  ·  "
              f"PBO board {_fmt(pbo_board)} → +pairs_ou {_fmt(pbo_with)}")
        print(f"   KILL CRITERION: Δ<+0.05 OR PBO worsens  →  "
              + ("KILL — model, not edge; OU params non-stationary, NOT promoted." if kill2
                 else "SURVIVES — candidate for promotion (re-check parity before adding)."))

        # MinBTL headroom cost: public board N vs research N.
        mb_pub = risk.min_backtest_length(len(SPOT_STRATS))
        mb_res = risk.min_backtest_length(len(RESEARCH_STRATS))
        print(f"\nMinBTL headroom: public N={len(SPOT_STRATS)} needs {_fmt(mb_pub)} yrs; "
              f"research N={len(RESEARCH_STRATS)} needs {_fmt(mb_res)} yrs; data = {years:.1f} yrs. "
              f"Every added strategy lowers all DSRs and burns headroom — why losers stay off the board.")
        print("─" * 78)

        # ── Tharp position-sizing sweep (P2, RESEARCH-tharp-runlog.md) ───────────
        # Percent-Volatility sizing IS vol_target (not re-run); percent-risk uses an
        # ATR (range) vol budget. Hypothesis: sizing reshapes max-DD, not the per-bet
        # OOS deflated Sharpe; percent-risk ≈ vol_target (different vol estimator).
        base = strategies.ma_trend_filter(df, n=args.ma_n)
        sized = {
            "ma_trend (raw)":          base,
            "+ vol_target 15%":        strategies.vol_target(base, df, target_vol=args.target_vol, periods_per_year=ppy),
            "+ pct_risk 0.5% ATR20":   strategies.percent_risk_size(base, df, risk_pct=0.005, atr_window=20),
            "+ pct_risk 2.5% ATR20":   strategies.percent_risk_size(base, df, risk_pct=0.025, atr_window=20),
        }
        print("\nTHARP SIZING SWEEP on ma_trend (walk-forward OOS; max-DD is the point, not terminal wealth):")
        sh = f"  {'sizing':<24}{'OOS DSR':>9}{'OOS SR':>9}{'OOS MaxDD':>11}{'corr vs voltgt':>15}"
        print(sh); print("  " + "-" * (len(sh) - 2))
        vt = sized["+ vol_target 15%"]
        for label, pos in sized.items():
            try:
                w = backtest.walk_forward(lambda px, p=pos: p.reindex(px.index), close,
                                          n_splits=args.folds, cost_bps=args.cost_bps,
                                          slippage_bps=args.slippage_bps, periods_per_year=ppy)
                o = w["oos"]
                dfc = pd.concat({"a": pos, "b": vt}, axis=1).dropna()
                corr = float(dfc["a"].corr(dfc["b"])) if len(dfc) > 2 else float("nan")
                print(f"  {label:<24}{_fmt(o.get('deflated_sharpe')):>9}{_fmt(o.get('sharpe')):>9}"
                      f"{_fmt(o.get('max_drawdown'), True):>11}{_fmt(corr):>15}")
            except Exception as exc:  # noqa: BLE001
                print(f"  {label:<24}  (skipped: {str(exc)[:40]})")
        print("  → percent-risk corr ≈0.95 with vol_target ⇒ essentially a duplicate vol estimator "
              "(ATR vs return-σ); keep as a selectable sizing option, NOT a new board entry. Sizing "
              "reshapes max-DD dramatically, not the per-bet OOS Sharpe — the honest Tharp result.")
        print("─" * 78)

        # ---- Tier-B candidate sweep (research-only; NOT added to the board) -------------
        print("\nTIER-B CANDIDATE SWEEP — pre-registered, kill = OOS DSR ≤ 0.95 "
              "(see RESEARCH-tharp-runlog.md). Expected: deflate.")
        cands = {
            "donchian 55/20": strategies.donchian_breakout(df, 55, 20),
            "vwap_reversion 48": strategies.vwap_reversion(df, window=48),
            "ma_trend + fixedR 2:3": strategies.fixed_r_exit(
                strategies.ma_trend_filter(df, n=args.ma_n), df),
            "random_entry (control)": strategies.random_entry(df, seed=7),
        }
        hdr = (f"  {'candidate':<24}{'OOS DSR':>9}{'OOS SR':>9}{'OOS MaxDD':>11}"
               f"{'ExpR':>8}{'SQN':>7}{'#T':>5}  verdict")
        print(hdr)
        print("  " + "-" * (len(hdr) - 2))
        for label, pos in cands.items():
            try:
                w = backtest.walk_forward(lambda px, p=pos: p.reindex(px.index), close,
                                          n_splits=args.folds, cost_bps=args.cost_bps,
                                          slippage_bps=args.slippage_bps, periods_per_year=ppy)
                o = w["oos"]; op = w["oos_positions"]
                er = risk.expectancy_report(op, close.reindex(op.index),
                                            oos_vol.reindex(op.index), periods_per_year=ppy, k=2.0)
                dsr = o.get("deflated_sharpe")
                survives = isinstance(dsr, (int, float)) and dsr == dsr and dsr > 0.95
                verdict = "SURVIVES — re-verify before promoting" if survives else "KILL (≤0.95)"
                nt = er.get("n_trades", 0)
                exp_s = _fmt(er.get("expectancy_r")) if nt >= 5 else "  n/a"
                sqn_s = _fmt(er.get("sqn")) if nt >= 5 else " n/a"
                print(f"  {label:<24}{_fmt(dsr):>9}{_fmt(o.get('sharpe')):>9}"
                      f"{_fmt(o.get('max_drawdown'), True):>11}{exp_s:>8}{sqn_s:>7}{nt:>5}  {verdict}")
            except Exception as exc:  # noqa: BLE001
                print(f"  {label:<24}  (skipped: {str(exc)[:40]})")
        print("  Gamma-regime proxy: REJECTED — no historical option-chain/greek store ⇒ cannot enter "
              "the OOS harness (a data limit, not an opinion).")
        # Day-of-week seasonality — DESCRIPTIVE regime check, not a backtested edge.
        try:
            dow = close.pct_change().groupby(close.index.dayofweek).mean() * 100.0
            names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
            cells = " · ".join(f"{names[d]} {dow.get(d, float('nan')):+.2f}%" for d in range(7))
            print(f"  Day-of-week mean daily return (DESCRIPTIVE, ~noise at this N): {cells}")
        except Exception as exc:  # noqa: BLE001
            print(f"  Day-of-week readout skipped: {str(exc)[:40]}")
        print("─" * 78)

    # Carry: perp-FUNDING accrual (delta-neutral), descriptive only. P&L is the funding
    # RECEIVED on the short-perp leg, NOT spot price moves (audit H1 fix), annualized on
    # the funding cadence inferred from the stamp spacing (audit M3), no bfill (audit M1).
    try:
        funding = data.get_funding(symbol=args.funding_symbol, source="bybit",
                                   cache=not args.no_cache)
        cpos = strategies.carry(funding)
        fpy = _funding_periods_per_year(funding.index)
        cres = backtest.run_funding(cpos, funding["funding_rate"],
                                    cost_bps=args.cost_bps, slippage_bps=args.slippage_bps,
                                    periods_per_year=fpy, n_trials=n_trials)
        cs = cres["stats"]
        in_trade = float((cpos.fillna(0.0) != 0.0).mean()) * 100.0
        print(f"\ncarry (perp FUNDING accrual, delta-neutral; descriptive — OOS n/a): "
              f"funding Sharpe {_fmt(cs.get('sharpe'))}, funding CAGR {_fmt(cs.get('cagr'), True)}, "
              f"{in_trade:.0f}% time in-trade, over {len(funding)} funding intervals @ {fpy}/yr "
              f"(< MinBTL — not OOS-rankable on keyless history).")
    except Exception as exc:  # noqa: BLE001
        print(f"\ncarry: descriptive feed unavailable ({str(exc)[:50]}).")

    print("\n* OOS Deflated Sharpe > 0.95 = distinguishable from luck after deflating for N trials,")
    print("  measured on WALK-FORWARD out-of-sample returns (Bailey & López de Prado 2014). The")
    print("  IS→OOS Sharpe drop is the overfitting tell; PBO and MinBTL guard the *selection*.")
    print("  Most strategies clear neither, and many do not beat buy-and-hold net of cost OOS —")
    print("  that is the honest result, not a bug. NOT FINANCIAL ADVICE - backtest != forecast.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
