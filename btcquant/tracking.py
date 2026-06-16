"""tracking.py — OPTIONAL, dependency-light MLflow run-tracking for reproducible research.

Why it exists: the headline metric is the **net-of-cost, out-of-sample Deflated Sharpe**,
which deflates for the number of configurations tried (``n_trials``). For that number to
mean anything, every run's params (strategy, costs, folds, **n_trials**) and OOS metrics
(DSR / PSR / Sharpe / MaxDD) must be recorded, so a reported figure is always reproducible
and attributable to its exact config. This module does that via MLflow.

Honesty / footprint discipline:

* MLflow lives in ``requirements-dev.txt``, **not** the core ``requirements.txt``. The
  import is guarded — if MLflow is absent, every call is a graceful no-op with a one-line
  hint, so the engine never hard-depends on it and ``import btcquant`` stays lean.
* Only finite scalar metrics are logged (NaN/None are dropped, never coerced to 0) — the
  same "never fabricate a number" rule the rest of the project follows.

Default store is a local ``sqlite:///mlflow.db`` (git-ignored; MLflow 3.x deprecated the
old ``file:./mlruns`` backend, and SQLite works on 2.x too); artifacts land in ``./mlartifacts``.
Override the store with ``MLFLOW_TRACKING_URI``. View with ``mlflow ui --backend-store-uri
sqlite:///mlflow.db`` once runs exist.
"""
from __future__ import annotations

import math
import os
from typing import Optional, Sequence

try:  # optional dependency — see requirements-dev.txt
    import mlflow  # type: ignore
    _HAVE_MLFLOW = True
except Exception:  # noqa: BLE001 — any import failure means "tracking unavailable"
    _HAVE_MLFLOW = False

__all__ = ["available", "log_run"]


def available() -> bool:
    """True iff MLflow is importable (i.e. tracking will actually record)."""
    return _HAVE_MLFLOW


def _finite_scalars(metrics: dict) -> dict:
    out = {}
    for k, v in metrics.items():
        if isinstance(v, bool):
            out[k] = float(v)
        elif isinstance(v, (int, float)) and math.isfinite(float(v)):
            out[k] = float(v)
        # else: drop None / NaN / non-numeric — do not invent a value
    return out


def log_run(
    run_name: str,
    params: dict,
    metrics: dict,
    artifacts: Optional[Sequence[str]] = None,
    experiment: str = "btc-quant",
    tracking_uri: Optional[str] = None,
) -> Optional[str]:
    """Log one backtest run to MLflow; return its ``run_id`` (or ``None`` if MLflow is
    not installed — an honest no-op, not an error).

    Parameters
    ----------
    run_name : a stable, human label for the run (e.g. ``"ma_trend_filter_BTC-USD_1d"``).
    params   : config knobs (strategy, costs, folds, n_trials, date span, bar count…).
    metrics  : numeric results; non-finite/non-scalar entries are dropped, not zero-filled.
    artifacts: file paths to attach (e.g. the dashboard JSON, the tearsheet PNG).
    """
    if not _HAVE_MLFLOW:
        print("[tracking] MLflow not installed — run not logged. "
              "Enable with: pip install -r requirements-dev.txt")
        return None

    uri = tracking_uri or os.environ.get("MLFLOW_TRACKING_URI") or "sqlite:///mlflow.db"
    mlflow.set_tracking_uri(uri)
    mlflow.set_experiment(experiment)
    with mlflow.start_run(run_name=run_name) as run:
        mlflow.log_params({k: v for k, v in params.items() if v is not None})
        mlflow.log_metrics(_finite_scalars(metrics))
        for path in (artifacts or []):
            if path and os.path.exists(path):
                mlflow.log_artifact(path)
        return run.info.run_id
