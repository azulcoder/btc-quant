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
* **Overlap-corrected significance.** For horizon ``k`` the forward windows overlap, so
  the ``N`` pairs carry only ≈ ``N/k`` independent observations. The 95% band is widened
  accordingly: ``crit = 1.96 · √(k / N)`` (it reduces to the textbook ``1.96/√N`` at
  ``k = 1``). A naive ``1.96/√N`` at ``k > 1`` would over-state significance.
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


def ic_significance(ic: float, n: int, k: int = 1) -> dict:
    """Overlap-corrected 95% significance for an IC over ``n`` pairs at horizon ``k``.
    ``crit = 1.96·√(k/n)`` (textbook ``1.96/√n`` at ``k=1``). Returns
    ``{ic, n, k, crit, significant}``."""
    crit = 1.96 * math.sqrt(k / n) if n and n > 0 else float("nan")
    sig = bool(np.isfinite(ic) and np.isfinite(crit) and abs(ic) > crit)
    return {"ic": float(ic), "n": int(n), "k": int(k), "crit": float(crit), "significant": sig}


def ic_profile(signal: pd.Series, prices: pd.Series,
               horizons: Sequence[int] = (1, 3, 5, 10),
               method: str = "spearman") -> dict:
    """IC at each horizon with overlap-corrected significance — the lead-time profile.
    Returns ``{k: {ic, n, k, crit, significant}}``. A profile that peaks at ``k>1`` and
    fades is a genuine *lead*; one that is largest at ``k=1`` and small is, at best,
    contemporaneous."""
    out = {}
    s = pd.Series(signal, dtype="float64")
    for k in horizons:
        fr = forward_returns(prices, k).reindex(s.index)
        n = int(pd.concat([s, fr], axis=1).dropna().shape[0])
        ic = information_coefficient(s, prices, k, method=method)
        out[int(k)] = ic_significance(ic, n, k)
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
    not a universal lead. Returns ``{"in": {...}, "out": {...}}`` significance dicts."""
    s = pd.Series(signal, dtype="float64")
    m = pd.Series(mask, dtype="bool").reindex(s.index).fillna(False)
    fr = forward_returns(prices, k).reindex(s.index)
    base = pd.concat([s.rename("sig"), fr.rename("fwd"), m.rename("m")], axis=1).dropna()

    def _ic(sub: pd.DataFrame) -> dict:
        if len(sub) < 3 or sub["sig"].std() == 0 or sub["fwd"].std() == 0:
            return ic_significance(float("nan"), len(sub), k)
        ic = float(sub["sig"].corr(sub["fwd"], method=method))
        return ic_significance(ic, len(sub), k)

    return {"in": _ic(base[base["m"]]), "out": _ic(base[~base["m"]])}
