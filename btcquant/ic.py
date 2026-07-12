"""ic.py — forward Information Coefficient (IC): does a signal actually *lead* returns?

The IC is the correlation between a signal known at bar ``t`` and the **forward**
return realized over ``t → t+k``. It is the honest answer to "is this a leading
indicator, or am I fooling myself?" — a near-zero IC means no predictive content,
whatever the backtest equity curve looks like. (Grinold & Kahn, *Active Portfolio
Management* — the "fundamental law" ``IR ≈ IC · √breadth``; IC is the per-bet skill.)

Conventions / honesty rails:

* **No look-ahead in the signal.** ``signal_t`` must use only data through ``t``
  (btc-quant strategy positions already satisfy this). The *forward return* is, by
  definition, future data — it is used only to *score* the signal, never to build it.
* **Rank IC (Spearman) is the default** — robust to the heavy tails and outliers of
  crypto returns; Pearson is offered for comparison.
* **Overlap-corrected significance (Newey-West HAC).** For horizon ``k`` the forward
  windows overlap, so the scoring errors of adjacent pairs share ``k-1`` of their forward
  bars — an ``MA(k-1)`` autocorrelation structure that a naive (homoskedastic) standard
  error understates. We therefore test the IC with a **Newey-West HAC** covariance at lag
  ``k-1`` (exactly the overlap-induced MA order). Because the default IC is Spearman
  (rank), the test is run on the *rank* relationship — see :func:`ic_significance`.
* **IC-IR** (information ratio of the IC) uses **non-overlapping** blocks, so its
  t-stat is not inflated by autocorrelation.

This is an **evaluation** layer (like ``risk``/``expectancy_report``), not a signal.
Score strategies **out-of-sample** (e.g. on ``backtest.walk_forward``'s ``oos_positions``).
"""
from __future__ import annotations

import math
from typing import Optional, Sequence

import numpy as np
import pandas as pd
import statsmodels.api as sm

__all__ = [
    "forward_returns",
    "information_coefficient",
    "ic_significance",
    "ic_profile",
    "ic_ir",
    "regime_conditional_ic",
]


def forward_returns(prices: pd.Series, k: int = 1) -> pd.Series:
    """Forward simple return over ``t → t+k``, **indexed at t** so it aligns with a
    signal known at ``t``::

        r_t = prices_{t+k} / prices_t - 1

    The last ``k`` entries are ``NaN`` (no future yet). This *is* forward-looking — it
    exists only to score a signal, and must never feed one."""
    if k < 1:
        raise ValueError(f"k must be >= 1, got {k}")
    p = pd.Series(prices, dtype="float64")
    return (p.shift(-k) / p - 1.0).rename(f"fwd_ret_{k}")


def information_coefficient(signal: pd.Series, prices: pd.Series, k: int = 1,
                            method: str = "spearman") -> float:
    """IC = corr(``signal_t``, forward return ``t→t+k``). ``method`` is ``"spearman"``
    (rank, default) or ``"pearson"``. Returns ``NaN`` on < 3 valid pairs or zero
    variance. A signed position series scores positive when it anticipates the signed
    move (short before a drop → positive IC)."""
    s = pd.Series(signal, dtype="float64")
    fr = forward_returns(prices, k).reindex(s.index)
    df = pd.concat([s.rename("sig"), fr.rename("fwd")], axis=1).dropna()
    if len(df) < 3:
        return float("nan")
    if df["sig"].std() == 0 or df["fwd"].std() == 0:
        return float("nan")
    return float(df["sig"].corr(df["fwd"], method=method))


def ic_significance(signal: pd.Series, fwd: pd.Series, k: int = 1) -> dict:
    """Newey-West (HAC) significance of a ``k``-horizon forward IC on **overlapping**
    windows.

    A ``k``-bar forward IC is scored on overlapping windows: consecutive pairs share
    ``k-1`` of their forward bars, so the scoring errors follow an ``MA(k-1)`` process and
    a naive (homoskedastic) standard error *understates* the uncertainty — the classic
    overlapping-returns pitfall. We correct it with a **Newey-West heteroskedasticity- and
    autocorrelation-consistent (HAC)** covariance at lag ``k-1``, exactly the overlap-
    induced MA order (Newey & West 1987, *Econometrica* 55(3); Lopez de Prado, *Advances
    in Financial Machine Learning* §4-5 on overlapping-label serial dependence). At ``k=1``
    there is no overlap and ``maxlags`` collapses to 0.

    Because the default IC is Spearman (**rank**), we test the *rank* relationship: rank-
    transform ``signal`` and ``fwd`` over the aligned sample, then fit
    ``fwd_rank ~ const + signal_rank`` by OLS with ``cov_type="HAC"`` and
    ``cov_kwds={"maxlags": max(k-1, 0)}``. Ranks of a common sample have (near-)equal
    variance, so the OLS **slope equals** ``corr(signal_rank, fwd_rank)`` = the Spearman IC
    itself; the slope's HAC t-stat and two-sided p-value therefore test *exactly*
    ``H0: IC = 0``. (This is a rank-HAC: the reported ``ic`` is the standardized rank
    slope, i.e. the Spearman coefficient.)

    ``signal`` and ``fwd`` are the **aligned** series (signal known at ``t`` and its forward
    return; callers pass the already-``dropna``-joined columns). Returns
    ``{ic, n, k, t_stat, p_value, se, significant (p<0.05), method}`` with
    ``method == "newey-west-k-1"``. Degenerate samples (too few points or zero variance)
    return ``NaN`` stats and ``significant=False`` rather than raising.

    See :func:`ic_ir` for the complementary **non-overlapping** block IC-IR t-stat."""
    s = pd.Series(signal, dtype="float64")
    f = pd.Series(fwd, dtype="float64")
    df = pd.concat([s.rename("sig"), f.rename("fwd")], axis=1).dropna()
    n = int(len(df))
    maxlags = max(int(k) - 1, 0)
    nan = float("nan")
    out = {"ic": nan, "n": n, "k": int(k), "t_stat": nan, "p_value": nan,
           "se": nan, "significant": False, "method": "newey-west-k-1"}
    # Need enough points to fit the slope and estimate a HAC kernel of width ``maxlags``.
    if n < 3 or n <= maxlags + 1 or df["sig"].std() == 0 or df["fwd"].std() == 0:
        return out
    # Spearman == Pearson on average-ranked data; the OLS slope of these ranks IS the IC.
    x_rank = df["sig"].rank().to_numpy()
    y_rank = df["fwd"].rank().to_numpy()
    fit = sm.OLS(y_rank, sm.add_constant(x_rank)).fit(
        cov_type="HAC", cov_kwds={"maxlags": maxlags})
    slope, se, t_stat, p_value = (float(fit.params[1]), float(fit.bse[1]),
                                  float(fit.tvalues[1]), float(fit.pvalues[1]))
    out.update(ic=slope, se=se, t_stat=t_stat, p_value=p_value,
               significant=bool(np.isfinite(p_value) and p_value < 0.05))
    return out


def ic_profile(signal: pd.Series, prices: pd.Series,
               horizons: Sequence[int] = (1, 3, 5, 10),
               method: str = "spearman") -> dict:
    """IC at each horizon with Newey-West (HAC) significance — the lead-time profile.
    Returns ``{k: {ic, n, k, t_stat, p_value, se, significant, method}}`` (see
    :func:`ic_significance`). A profile that peaks at ``k>1`` and fades is a genuine
    *lead*; one that is largest at ``k=1`` and small is, at best, contemporaneous.

    ``method`` selects how the *displayed* IC magnitude is computed; the HAC significance
    is always run on the rank relationship (the default, robust Spearman notion — so for
    ``method="spearman"`` the returned ``ic`` and the significance coincide)."""
    out = {}
    s = pd.Series(signal, dtype="float64")
    for k in horizons:
        fr = forward_returns(prices, k).reindex(s.index)
        df = pd.concat([s.rename("sig"), fr.rename("fwd")], axis=1).dropna()
        res = ic_significance(df["sig"], df["fwd"], k)
        if method != "spearman":
            res["ic"] = information_coefficient(s, prices, k, method=method)
        out[int(k)] = res
    return out


def ic_ir(signal: pd.Series, prices: pd.Series, k: int = 1, block: int = 21,
          method: str = "spearman") -> dict:
    """IC information ratio from **non-overlapping** blocks: split the sample into
    ``block``-bar windows, take one IC per window, and report ``mean/std`` plus a
    t-stat ``= IR·√(n_blocks)``. Non-overlap keeps the t-stat free of the autocorrelation
    that would inflate a rolling-window version. Returns
    ``{mean_ic, std_ic, ir, t_stat, n_blocks}``."""
    s = pd.Series(signal, dtype="float64")
    fr = forward_returns(prices, k).reindex(s.index)
    df = pd.concat([s.rename("sig"), fr.rename("fwd")], axis=1).dropna()
    ics = []
    for start in range(0, len(df) - block + 1, block):
        w = df.iloc[start:start + block]
        if w["sig"].std() > 0 and w["fwd"].std() > 0:
            ics.append(float(w["sig"].corr(w["fwd"], method=method)))
    nb = len(ics)
    if nb < 2:
        return {"mean_ic": float("nan"), "std_ic": float("nan"), "ir": float("nan"),
                "t_stat": float("nan"), "n_blocks": nb}
    arr = np.asarray(ics, dtype="float64")
    mean_ic, std_ic = float(arr.mean()), float(arr.std(ddof=1))
    ir = mean_ic / std_ic if std_ic > 0 else float("nan")
    t_stat = ir * math.sqrt(nb) if np.isfinite(ir) else float("nan")
    return {"mean_ic": mean_ic, "std_ic": std_ic, "ir": ir, "t_stat": t_stat, "n_blocks": nb}


def regime_conditional_ic(signal: pd.Series, prices: pd.Series, mask: pd.Series,
                          k: int = 1, method: str = "spearman") -> dict:
    """IC computed separately on the bars where ``mask`` is True vs False — the question
    "does the signal lead *only* in a particular regime?" (e.g. ``mask = ADX ≥ 25``).
    A signal that is significant inside the regime and null outside is regime-conditional,
    not a universal lead. Returns ``{"in": {...}, "out": {...}}`` HAC-significance dicts
    (see :func:`ic_significance`)."""
    s = pd.Series(signal, dtype="float64")
    m = pd.Series(mask, dtype="bool").reindex(s.index).fillna(False)
    fr = forward_returns(prices, k).reindex(s.index)
    base = pd.concat([s.rename("sig"), fr.rename("fwd"), m.rename("m")], axis=1).dropna()

    def _ic(sub: pd.DataFrame) -> dict:
        res = ic_significance(sub["sig"], sub["fwd"], k)
        if method != "spearman" and np.isfinite(res["ic"]):
            res["ic"] = float(sub["sig"].corr(sub["fwd"], method=method))
        return res

    return {"in": _ic(base[base["m"]]), "out": _ic(base[~base["m"]])}
