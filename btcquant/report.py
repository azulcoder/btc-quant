"""report.py — matplotlib tearsheet + dashboard JSON export for btc-quant.

Reporting only — this module computes nothing new about *significance* (that is
``risk.py``'s job); it visualizes and serializes a backtest result so the honest
story is unavoidable:

* the strategy equity curve is **always drawn against the buy-and-hold baseline**
  (DESIGN.md non-negotiable — never a lone equity curve),
* a persistent **"NOT FINANCIAL ADVICE · backtest ≠ forecast"** banner, and
* the headline **net-of-cost, Deflated Sharpe** stamped on the sheet, not a raw
  Sharpe.

Public API
----------
* :func:`tearsheet` — a 4-panel matplotlib figure (equity vs B&H, drawdown,
  rolling Sharpe, return histogram).
* :func:`to_dashboard_json` / :func:`export_json` — export the result series +
  stats to a JSON the static web dashboard can ``fetch()`` (DESIGN.md: the
  dashboard mirrors ``backtest.py`` conventions). ``export_json`` is an alias so
  the package re-export in ``__init__.py`` resolves.
"""

from __future__ import annotations

import json
import math
from typing import Optional

import numpy as np
import pandas as pd

from . import features

__all__ = ["tearsheet", "to_dashboard_json", "export_json"]

_DISCLAIMER = "NOT FINANCIAL ADVICE · backtest ≠ forecast"


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #
def _buy_and_hold_equity(prices: pd.Series, index: pd.Index) -> pd.Series:
    """Buy-and-hold wealth curve aligned to ``index`` (the baseline).

    Normalized to start at 1.0 on the first bar of ``index`` so it is directly
    comparable to the strategy's net equity curve.
    """
    px = pd.Series(prices, dtype="float64").reindex(index).ffill()
    base = px.dropna()
    if base.empty:
        return pd.Series(index=index, dtype="float64")
    return px / base.iloc[0]


def _safe_float(x) -> Optional[float]:
    """Convert to a JSON-safe float, mapping NaN/±inf to ``None``."""
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _series_payload(s: pd.Series) -> dict:
    """Serialize a time-indexed Series to ``{t: [...ISO...], v: [...floats...]}``.

    Timestamps are emitted as ISO-8601 UTC strings (the dashboard parses them
    directly); NaN/inf values become ``null`` so the front-end can gap the line.
    """
    s = pd.Series(s)
    idx = pd.to_datetime(s.index, utc=True)
    return {
        "t": [ts.isoformat() for ts in idx],
        "v": [_safe_float(v) for v in s.to_numpy()],
    }


# --------------------------------------------------------------------------- #
# Tearsheet                                                                    #
# --------------------------------------------------------------------------- #
def tearsheet(
    result: dict,
    prices: Optional[pd.Series] = None,
    periods_per_year: int = 365,
    title: str = "btc-quant backtest",
    rolling_window: int = 90,
):
    """Render a 4-panel matplotlib tearsheet for a :func:`backtest.run` result.

    Panels:

    1. **Equity vs buy-and-hold** — the net-of-cost strategy curve against the
       B&H baseline (drawn iff ``prices`` is supplied). Log y-scale.
    2. **Drawdown** — the strategy's underwater curve.
    3. **Rolling Sharpe** — annualized, ``rolling_window``-bar.
    4. **Return histogram** — per-bar net return distribution.

    The headline stats (CAGR, net Sharpe, **Deflated Sharpe**, max drawdown) are
    stamped in the suptitle alongside the mandatory disclaimer banner.

    Parameters
    ----------
    result : dict
        Output of :func:`backtest.run` (needs ``equity``, ``returns``, ``stats``).
    prices : pd.Series, optional
        Price series for the buy-and-hold baseline; if omitted the baseline panel
        is skipped (and a note is shown) — but the baseline is the whole point, so
        pass it whenever you can.
    periods_per_year : int, default 365
    title : str
    rolling_window : int, default 90

    Returns
    -------
    matplotlib.figure.Figure
        The composed figure (caller can ``savefig`` / ``show``). Uses the Agg-safe
        pyplot API so it works headless.
    """
    import matplotlib

    matplotlib.use("Agg", force=False)
    import matplotlib.pyplot as plt

    equity = pd.Series(result["equity"], dtype="float64")
    returns = pd.Series(result["returns"], dtype="float64")
    stats = result.get("stats", {})

    fig, axes = plt.subplots(2, 2, figsize=(13, 8))
    ax_eq, ax_dd = axes[0]
    ax_rs, ax_hist = axes[1]

    # --- 1. Equity vs buy-and-hold ---------------------------------------- #
    ax_eq.plot(equity.index, equity.to_numpy(), label="strategy (net)", color="#1f77b4", lw=1.6)
    if prices is not None:
        bh = _buy_and_hold_equity(prices, equity.index)
        ax_eq.plot(bh.index, bh.to_numpy(), label="buy & hold", color="#888888", lw=1.3, ls="--")
    else:
        ax_eq.text(
            0.5, 0.05, "buy-and-hold baseline not supplied",
            transform=ax_eq.transAxes, ha="center", va="bottom",
            fontsize=8, color="crimson",
        )
    ax_eq.set_title("Equity vs buy-and-hold (net of cost)")
    ax_eq.set_yscale("log")
    ax_eq.set_ylabel("growth of $1 (log)")
    ax_eq.legend(loc="upper left", fontsize=8)
    ax_eq.grid(True, alpha=0.25)

    # --- 2. Drawdown ------------------------------------------------------- #
    dd = features.drawdown(equity)
    ax_dd.fill_between(dd.index, dd.to_numpy() * 100.0, 0.0, color="#d62728", alpha=0.4)
    ax_dd.plot(dd.index, dd.to_numpy() * 100.0, color="#d62728", lw=1.0)
    ax_dd.set_title("Drawdown")
    ax_dd.set_ylabel("drawdown (%)")
    ax_dd.grid(True, alpha=0.25)

    # --- 3. Rolling Sharpe ------------------------------------------------- #
    rs = features.rolling_sharpe(returns, window=rolling_window, periods_per_year=periods_per_year)
    ax_rs.plot(rs.index, rs.to_numpy(), color="#2ca02c", lw=1.1)
    ax_rs.axhline(0.0, color="#444444", lw=0.8)
    ax_rs.set_title(f"Rolling Sharpe ({rolling_window}-bar, annualized)")
    ax_rs.set_ylabel("Sharpe")
    ax_rs.grid(True, alpha=0.25)

    # --- 4. Return histogram ---------------------------------------------- #
    r = returns.dropna().to_numpy()
    if r.size:
        ax_hist.hist(r * 100.0, bins=min(60, max(10, r.size // 5)),
                     color="#9467bd", alpha=0.8)
        ax_hist.axvline(float(np.mean(r)) * 100.0, color="black", lw=1.0, ls="--",
                        label=f"mean {np.mean(r) * 100:.3f}%")
        ax_hist.legend(loc="upper left", fontsize=8)
    ax_hist.set_title("Per-bar net return distribution")
    ax_hist.set_xlabel("return (%)")
    ax_hist.grid(True, alpha=0.25)

    # --- Headline + disclaimer -------------------------------------------- #
    def _fmt(key, pct=False, dp=2):
        v = stats.get(key, float("nan"))
        try:
            v = float(v)
        except (TypeError, ValueError):
            return "n/a"
        if math.isnan(v):
            return "n/a"
        return f"{v * 100:.{dp}f}%" if pct else f"{v:.{dp}f}"

    headline = (
        f"{title}   |   CAGR {_fmt('cagr', pct=True)}   "
        f"Sharpe(net) {_fmt('sharpe')}   "
        f"Deflated Sharpe {_fmt('deflated_sharpe')}   "
        f"maxDD {_fmt('max_drawdown', pct=True)}   "
        f"trades {stats.get('trades', 'n/a')}"
    )
    fig.suptitle(headline, fontsize=11, y=0.99)
    fig.text(0.5, 0.005, _DISCLAIMER, ha="center", va="bottom",
             fontsize=9, color="crimson", weight="bold")

    fig.tight_layout(rect=(0, 0.025, 1, 0.965))
    return fig


# --------------------------------------------------------------------------- #
# Dashboard JSON export                                                        #
# --------------------------------------------------------------------------- #
def to_dashboard_json(
    result: dict,
    path: str,
    prices: Optional[pd.Series] = None,
    periods_per_year: int = 365,
    meta: Optional[dict] = None,
) -> dict:
    """Export a backtest result to a JSON file the static dashboard can fetch.

    The payload mirrors ``backtest.py``'s conventions (net equity, the
    buy-and-hold baseline, drawdown, rolling Sharpe, the cost-bearing turnover,
    and the full stats dict incl. the **Deflated Sharpe**) so the web terminal can
    redraw the same honest picture client-side without recomputation.

    Parameters
    ----------
    result : dict
        Output of :func:`backtest.run` (``equity``, ``returns``, ``gross_returns``,
        ``turnover``, ``stats``).
    path : str
        Destination ``.json`` path (written UTF-8).
    prices : pd.Series, optional
        Price series; if given, a normalized buy-and-hold baseline series is
        included so the dashboard always shows the benchmark.
    periods_per_year : int, default 365
    meta : dict, optional
        Free-form metadata (strategy name, params, symbol, date range) merged into
        the payload's ``meta`` block.

    Returns
    -------
    dict
        The exported payload (also written to ``path``).
    """
    equity = pd.Series(result["equity"], dtype="float64")
    returns = pd.Series(result["returns"], dtype="float64")
    gross = pd.Series(result.get("gross_returns", returns), dtype="float64")
    turnover = pd.Series(result.get("turnover", pd.Series(dtype="float64")), dtype="float64")
    stats = dict(result.get("stats", {}))

    # JSON-sanitize the stats dict (NaN/inf -> null; numpy scalars -> python).
    clean_stats = {}
    for k, v in stats.items():
        if isinstance(v, (int, np.integer)) and not isinstance(v, bool):
            clean_stats[k] = int(v)
        else:
            f = _safe_float(v)
            clean_stats[k] = f if f is not None else v if isinstance(v, str) else None

    dd = features.drawdown(equity)
    rs = features.rolling_sharpe(returns, window=90, periods_per_year=periods_per_year)

    payload: dict = {
        "schema": "btc-quant/backtest-result@1",
        "disclaimer": _DISCLAIMER,
        "meta": {
            "periods_per_year": int(periods_per_year),
            **(meta or {}),
        },
        "stats": clean_stats,
        "series": {
            "equity": _series_payload(equity),
            "returns": _series_payload(returns),
            "gross_returns": _series_payload(gross),
            "drawdown": _series_payload(dd),
            "rolling_sharpe": _series_payload(rs),
            "turnover": _series_payload(turnover),
        },
    }
    if prices is not None:
        payload["series"]["buy_and_hold"] = _series_payload(
            _buy_and_hold_equity(prices, equity.index)
        )

    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, allow_nan=False)

    return payload


# Alias so ``__init__.py``'s ``_reexport("report", ("tearsheet", "export_json"))``
# resolves to the dashboard exporter.
export_json = to_dashboard_json
