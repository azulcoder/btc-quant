"""btc-quant — a Bitcoin quant **research / backtest** terminal.

Research and backtesting only. **No live trading, no orders, no API keys, no
authenticated endpoints.** See ``DISCLAIMER.md`` at the project root.

The honesty rails are the product, not decoration:

- **No look-ahead** — a signal computed at bar *t* trades bar *t+1*.
- **Costs + slippage are on by default** — no gross-only equity curves stand alone.
- **Buy-and-hold is the baseline** every strategy is scored against.
- **The headline metric is the net-of-cost, out-of-sample, Deflated Sharpe** — never
  a single equity curve.

Package layout
--------------
- :mod:`btcquant.data`       — fetch + cache public market data (this module is keyless).
- :mod:`btcquant.features`   — pure indicator / signal functions on pandas Series/DataFrame.
- :mod:`btcquant.backtest`   — vectorized backtester (sizing, costs, slippage, walk-forward).
- :mod:`btcquant.risk`       — performance & risk stats incl. deflated / probabilistic Sharpe.
- :mod:`btcquant.strategies` — the strategy library; each cites its edge and caveats.
- :mod:`btcquant.report`     — matplotlib tearsheet + JSON export for the dashboard.

Convenience re-exports
----------------------
The data-layer entry points are re-exported at the package root::

    from btcquant import get_ohlcv, get_funding

Sibling modules (``features``, ``backtest``, ``risk``, ``strategies``, ``report``) are
re-exported opportunistically: if a module is not yet present, importing :mod:`btcquant`
still succeeds and only the available names are bound.
"""

from __future__ import annotations

__version__ = "0.1.0"

# --- Data layer (always available; this is the keyless public-data fetcher). ---
from .data import DataError, get_funding, get_ohlcv, http_get  # noqa: E402

__all__ = [
    "__version__",
    "DataError",
    "get_ohlcv",
    "get_funding",
    "http_get",
]


def _reexport(module_name: str, names: tuple[str, ...]) -> None:
    """Best-effort re-export of ``names`` from a sibling submodule.

    Sibling modules are written by other build agents and may not exist yet. We
    import lazily and silently skip anything missing so ``import btcquant`` never
    fails just because a peer module is incomplete.
    """
    try:
        module = __import__(f"{__name__}.{module_name}", fromlist=list(names))
    except Exception:  # pragma: no cover - peer module absent/under construction
        return
    for name in names:
        obj = getattr(module, name, None)
        if obj is not None:
            globals()[name] = obj
            if name not in __all__:
                __all__.append(name)


# Pull up the most commonly used entry points from sibling modules when present.
_reexport("backtest", ("run", "walk_forward"))
_reexport("risk", ("summary", "sharpe", "deflated_sharpe_ratio",
                   "probabilistic_sharpe_ratio"))
_reexport("report", ("tearsheet", "export_json"))
