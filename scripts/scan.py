#!/usr/bin/env python3
"""scan.py — current signal snapshot for btc-quant (research only).

Prints a one-shot dashboard of the *latest* readings the first-cut strategies key
off, computed from public OHLCV (+ optional perp funding) with the same
no-look-ahead feature functions the backtester uses:

* latest price (and last bar timestamp),
* momentum sign over a chosen lookback (the TSMOM signal direction),
* volatility regime (annualized realized vol vs its own rolling median),
* MA-trend state (price vs the 200-day MA / golden-cross),
* latest funding rate + crude APR (perp-only),
* options snapshot: ATM 30d IV (total-variance interpolated), DVOL, the
  term-structure slope (front vs ~90d) and 25-delta risk reversal — each tagged
  SIGNAL / DESCRIPTIVE per the design brief §2 (Deribit, degrades gracefully).

This is a **descriptive snapshot, not a recommendation** — a signal seen at the
latest close would only trade the NEXT bar (the backtester's 1-bar shift). No
keys, no orders. See ``DISCLAIMER.md``.

Examples
--------
::

    python3 scripts/scan.py
    python3 scripts/scan.py --symbol BTC-USD --granularity 1d --lookback 20
    python3 scripts/scan.py --no-funding
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

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from btcquant import data, features  # noqa: E402


def _build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser for the scan CLI."""
    parser = argparse.ArgumentParser(
        prog="scan.py",
        description=(
            "Print a current signal snapshot (price, momentum sign, vol regime, "
            "MA-trend state, latest funding). Descriptive only — no keys, no orders."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--symbol", default="BTC-USD", help="Product symbol.")
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
        help="Public OHLCV source.",
    )
    parser.add_argument("--lookback", type=int, default=20, help="Momentum lookback (bars).")
    parser.add_argument("--ma-n", type=int, default=200, help="Long MA window (bars).")
    parser.add_argument("--ma-fast", type=int, default=50, help="Fast MA window for golden-cross.")
    parser.add_argument("--vol-window", type=int, default=20, help="Realized-vol window (bars).")
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Do not use the on-disk cache (force live fetch).",
    )
    parser.add_argument(
        "--no-funding",
        action="store_true",
        help="Skip the perp funding readout.",
    )
    parser.add_argument(
        "--funding-symbol",
        default="BTCUSDT",
        help="Perp symbol for the funding readout (Bybit).",
    )
    parser.add_argument(
        "--no-options",
        action="store_true",
        help="Skip the Deribit options snapshot (ATM 30d IV / DVOL / slope / RR25).",
    )
    parser.add_argument(
        "--options-currency",
        default="BTC",
        help="Currency for the Deribit option chain + DVOL snapshot.",
    )
    return parser


def _periods_per_year(granularity: str) -> int:
    """Bars per year for vol annualization (365 daily, 24*365 hourly)."""
    return 24 * 365 if granularity == "1h" else 365


def _last_finite(s: pd.Series) -> float:
    """Last non-NaN value of a Series, or NaN if none."""
    s = s.dropna()
    return float(s.iloc[-1]) if len(s) else float("nan")


def _fmt(v: float, dp: int = 2, suffix: str = "") -> str:
    """Format a float, mapping NaN/inf to 'n/a'."""
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return "n/a"
    return f"{v:.{dp}f}{suffix}"


def _scan_funding(symbol: str, cache: bool) -> None:
    """Print the latest perp funding rate + crude APR (perp-only concept)."""
    try:
        funding = data.get_funding(symbol=symbol, source="bybit", cache=cache)
    except data.DataError as exc:
        print(f"  funding         : unavailable ({exc})")
        return
    if funding.empty:
        print("  funding         : unavailable (no rows)")
        return
    last_rate = float(funding["funding_rate"].iloc[-1])
    last_ts = funding.index[-1]
    # Common 8h cadence -> 3x/day x 365 intervals/yr for a crude APR.
    apr = last_rate * 1095.0
    carry_side = (
        "positive -> longs pay shorts (short-perp leg RECEIVES)"
        if last_rate > 0
        else "negative -> shorts pay longs (carry INVERTS)"
        if last_rate < 0
        else "flat"
    )
    print(f"  funding (perp)  : {last_rate * 100:+.4f}% / interval  (~{apr * 100:+.1f}% APR)")
    print(f"                    {carry_side}")
    print(f"                    as of {last_ts}  [{symbol}, Bybit]")


def _interp_iv_total_variance(term: pd.DataFrame, target_t: float) -> float:
    """Interpolate ATM IV to ``target_t`` years in **total variance** (brief §1.4b).

    ``w_i = IV_i^2 * T_i`` is linear-interpolated in ``T`` (never IV directly — that
    manufactures calendar arbitrage), then ``IV(T) = sqrt(w(T)/T)``. ``term`` is the
    ``[expiry, T, atm_iv]`` frame from ``features.iv_term_structure``. Clamps to the
    nearest tenor outside the observed range. Returns a decimal IV or NaN.
    """
    t = term.dropna(subset=["T", "atm_iv"])
    t = t[t["atm_iv"] > 0.0].sort_values("T")
    if t.empty or target_t <= 0.0:
        return float("nan")
    ts = t["T"].to_numpy(dtype="float64")
    ws = (t["atm_iv"].to_numpy(dtype="float64") ** 2) * ts  # total variance
    if len(ts) == 1:
        return float(np.sqrt(ws[0] / ts[0]))
    if target_t <= ts[0]:
        return float(t["atm_iv"].iloc[0])
    if target_t >= ts[-1]:
        return float(t["atm_iv"].iloc[-1])
    w = float(np.interp(target_t, ts, ws))
    return float(np.sqrt(w / target_t))


def _nearest_expiry(term: pd.DataFrame, target_t: float):
    """Return the (expiry, T, atm_iv) row whose T is closest to ``target_t``."""
    t = term.dropna(subset=["T", "atm_iv"])
    if t.empty:
        return None
    i = int((t["T"] - target_t).abs().to_numpy().argmin())
    row = t.iloc[i]
    return row["expiry"], float(row["T"]), float(row["atm_iv"])


def _scan_options(currency: str, cache: bool) -> None:
    """Print the Deribit options snapshot (ATM 30d IV / DVOL / slope / RR25).

    Degrades gracefully: if Deribit is unreachable and no cache exists, each line
    reports 'unavailable' rather than crashing or fabricating a value.
    """
    try:
        chain = data.get_option_chain(currency=currency, cache=cache)
    except data.DataError as exc:
        print(f"  options         : unavailable ({exc})")
        return
    if chain.empty:
        print("  options         : unavailable (empty chain)")
        return

    term = features.iv_term_structure(chain)
    # --- ATM 30d IV, total-variance interpolated (SIGNAL: vol-forecast/regime) ---
    t30 = 30.0 / 365.0
    iv30 = _interp_iv_total_variance(term, t30)
    print(
        f"  ATM 30d IV      : {_fmt(iv30 * 100 if not math.isnan(iv30) else iv30, 1, '%')}"
        "   [SIGNAL: vol-forecast/regime, NOT a return signal]"
    )

    # --- DVOL benchmark (DESCRIPTIVE; sits above ATM IV by the BF premium) ------ #
    try:
        dvol = data.get_dvol(currency=currency, cache=cache)
        dvol_last = float(dvol["implied_vol"].iloc[-1]) if not dvol.empty else float("nan")
    except data.DataError:
        dvol_last = float("nan")
    print(
        f"  DVOL (30d MFIV) : {_fmt(dvol_last, 1, '%')}"
        "   [DESCRIPTIVE benchmark; expect >= ATM IV by the smile (BF) premium]"
    )

    # --- Term-structure slope: front vs ~90d (SIGNAL: regime/sizing) ----------- #
    front = _nearest_expiry(term, 7.0 / 365.0)
    far = _nearest_expiry(term, 90.0 / 365.0)
    if front is not None and far is not None and front[0] != far[0]:
        slope = far[2] - front[2]  # far - front: >0 contango, <0 backwardation
        shape = "contango (far>front)" if slope > 0 else "backwardation (front>far)"
        front_days = front[1] * 365.0
        far_days = far[1] * 365.0
        print(
            f"  term slope      : {_fmt(slope * 100 if not math.isnan(slope) else slope, 1, ' vol-pts')}"
            f"   {shape}"
        )
        print(
            f"                    front ~{front_days:.0f}d {_fmt(front[2] * 100, 1, '%')} -> "
            f"~{far_days:.0f}d {_fmt(far[2] * 100, 1, '%')}"
        )
        print("                    [SIGNAL: regime/sizing, NOT a return signal]")
    else:
        print("  term slope      : n/a (need >= 2 distinct expiries)")

    # --- 25-delta risk reversal at the ~30d expiry (DESCRIPTIVE: sentiment) ---- #
    near30 = _nearest_expiry(term, t30)
    if near30 is not None:
        rr = features.iv_skew_25d(chain, near30[0])
        rr_read = (
            "put skew / downside bid" if (not math.isnan(rr) and rr < 0)
            else "call skew / upside bid" if (not math.isnan(rr) and rr > 0)
            else "flat"
        )
        print(
            f"  25d RR (~30d)   : {_fmt(rr * 100 if not math.isnan(rr) else rr, 1, ' vol-pts')}"
            f"   {rr_read}"
        )
        print("                    [DESCRIPTIVE: sentiment; RR = IV(25dC) - IV(25dP)]")
    else:
        print("  25d RR (~30d)   : n/a (no usable expiry)")

    last_ul = pd.to_numeric(chain["underlying_price"], errors="coerce").dropna()
    if len(last_ul):
        print(f"                    underlying ~{float(last_ul.median()):,.0f}  [{currency}, Deribit]")


def main(argv: list[str] | None = None) -> int:
    """Entry point: compute and print the current signal snapshot."""
    args = _build_parser().parse_args(argv)
    cache = not args.no_cache
    ppy = _periods_per_year(args.granularity)

    try:
        df = data.get_ohlcv(
            symbol=args.symbol,
            source=args.source,
            granularity=args.granularity,
            cache=cache,
        )
    except data.DataError as exc:
        print(f"ERROR loading OHLCV: {exc}", file=sys.stderr)
        return 1

    if len(df) < 2:
        print(f"ERROR: only {len(df)} bars loaded; need >= 2.", file=sys.stderr)
        return 1

    close = df["close"]
    last_px = float(close.iloc[-1])
    last_ts = close.index[-1]

    # --- Momentum sign (the TSMOM direction) --------------------------------- #
    mom = features.momentum(close, lookback=args.lookback)
    mom_last = _last_finite(mom)
    if math.isnan(mom_last):
        mom_state = "n/a (warm-up)"
    elif mom_last > 0:
        mom_state = "LONG (+1)"
    elif mom_last < 0:
        mom_state = "SHORT/FLAT (-1/0)"
    else:
        mom_state = "FLAT (0)"

    # --- Volatility regime --------------------------------------------------- #
    rets = features.simple_returns(close)
    rvol = features.realized_vol(rets, window=args.vol_window, periods_per_year=ppy)
    rvol_last = _last_finite(rvol)
    rvol_median = float(np.nanmedian(rvol.to_numpy())) if rvol.notna().any() else float("nan")
    if math.isnan(rvol_last) or math.isnan(rvol_median):
        vol_regime = "n/a"
    elif rvol_last > 1.25 * rvol_median:
        vol_regime = "HIGH (size down)"
    elif rvol_last < 0.75 * rvol_median:
        vol_regime = "LOW (calm)"
    else:
        vol_regime = "NORMAL"

    # --- MA-trend state ------------------------------------------------------ #
    sma_slow = features.sma(close, args.ma_n)
    sma_fast = features.sma(close, args.ma_fast)
    slow_last = _last_finite(sma_slow)
    fast_last = _last_finite(sma_fast)

    if math.isnan(slow_last):
        ma_state = f"n/a (need {args.ma_n} bars; have {len(close)})"
    else:
        single = "ABOVE -> long" if last_px > slow_last else "BELOW -> flat"
        ma_state = f"price {single} {args.ma_n}d MA"
    if not math.isnan(fast_last) and not math.isnan(slow_last):
        cross = "golden (fast>slow) -> long" if fast_last > slow_last else "death (fast<slow) -> flat"
        ma_cross = f"{args.ma_fast}/{args.ma_n} cross: {cross}"
    else:
        ma_cross = f"{args.ma_fast}/{args.ma_n} cross: n/a (warm-up)"

    # --- Print snapshot ------------------------------------------------------ #
    print("=" * 64)
    print(f"  btc-quant SIGNAL SNAPSHOT  |  {args.symbol}  {args.granularity}  [{args.source}]")
    print("=" * 64)
    print(f"  latest price    : {last_px:,.2f}   as of {last_ts}")
    print(f"  momentum({args.lookback})    : {mom_state}   "
          f"(trailing return {_fmt(mom_last * 100 if not math.isnan(mom_last) else mom_last, 2, '%')})")
    print(f"  vol regime      : {vol_regime}   "
          f"(realized {_fmt(rvol_last * 100 if not math.isnan(rvol_last) else rvol_last, 1, '%')} ann., "
          f"median {_fmt(rvol_median * 100 if not math.isnan(rvol_median) else rvol_median, 1, '%')})")
    print(f"  MA-trend        : {ma_state}")
    print(f"                    {ma_cross}")
    if not args.no_funding:
        _scan_funding(args.funding_symbol, cache)
    if not args.no_options:
        _scan_options(args.options_currency, cache)
    print("-" * 64)
    print("  Descriptive snapshot only. A signal at the latest close trades the")
    print("  NEXT bar. NOT FINANCIAL ADVICE - backtest != forecast.")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
