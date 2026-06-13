"""risk.py — performance & risk statistics for btc-quant.

Pure functions on a *returns* ``pd.Series`` (per-period simple returns) unless
noted. The headline honesty metrics are the Probabilistic and **Deflated** Sharpe
ratios (Bailey & López de Prado): a raw Sharpe means little in a field that tested
hundreds of trials on a short, regime-dominated crypto sample, so every backtest
should surface the deflated value (DESIGN.md non-negotiables; RESEARCH.md §3).

Conventions
-----------
* ``returns`` : per-period simple returns ``pd.Series``; NaNs are dropped.
* ``periods_per_year=365`` for daily crypto bars (24/7 market).
* Risk-free rate is assumed 0 unless a ``rf`` arg is provided (so Sharpe ==
  mean/std * sqrt(ppy)). Sharpe/Sortino are reported **annualized**.
* All ratios degrade gracefully to ``np.nan`` on empty / zero-variance input.
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np
import pandas as pd
from scipy import stats

__all__ = [
    "sharpe",
    "sortino",
    "cagr",
    "volatility",
    "calmar",
    "max_drawdown",
    "hit_rate",
    "var",
    "cvar",
    "kelly_fraction",
    "kelly",
    "probabilistic_sharpe_ratio",
    "deflated_sharpe_ratio",
    "summary",
]


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #
def _clean(returns: pd.Series) -> pd.Series:
    """Coerce to float Series and drop NaNs (warm-up / missing bars)."""
    return pd.Series(returns, dtype="float64").dropna()


def _equity_from_returns(returns: pd.Series) -> pd.Series:
    """Cumulative wealth curve ``∏(1 + r)`` starting from 1.0 (pre-first-bar)."""
    r = _clean(returns)
    return (1.0 + r).cumprod()


# --------------------------------------------------------------------------- #
# Core performance ratios                                                      #
# --------------------------------------------------------------------------- #
def sharpe(
    returns: pd.Series,
    rf: float = 0.0,
    periods_per_year: int = 365,
) -> float:
    """Annualized Sharpe ratio.

    ``SR = (mean(r) - rf_per_period) / std(r) * sqrt(periods_per_year)``,
    using sample std (``ddof=1``). ``rf`` is an *annual* risk-free rate, converted
    to per-period as ``rf / periods_per_year``. Returns ``np.nan`` on <2 obs or
    zero variance.
    """
    r = _clean(returns)
    if len(r) < 2:
        return float("nan")
    sd = r.std(ddof=1)
    if sd == 0 or np.isnan(sd):
        return float("nan")
    rf_per = rf / periods_per_year
    return float((r.mean() - rf_per) / sd * math.sqrt(periods_per_year))


def sortino(
    returns: pd.Series,
    rf: float = 0.0,
    periods_per_year: int = 365,
) -> float:
    """Annualized Sortino ratio (downside-deviation denominator).

    ``Sortino = (mean(r) - rf_per) / downside_std * sqrt(periods_per_year)``,
    where downside deviation uses only returns below the per-period target
    (``rf_per``): ``sqrt(mean(min(r - rf_per, 0)^2))``. Returns ``np.nan`` if
    there is no downside variance.
    """
    r = _clean(returns)
    if len(r) < 2:
        return float("nan")
    rf_per = rf / periods_per_year
    downside = np.minimum(r - rf_per, 0.0)
    dd = math.sqrt(np.mean(np.square(downside)))
    if dd == 0 or np.isnan(dd):
        return float("nan")
    return float((r.mean() - rf_per) / dd * math.sqrt(periods_per_year))


def volatility(returns: pd.Series, periods_per_year: int = 365) -> float:
    """Annualized volatility ``std(r, ddof=1) * sqrt(periods_per_year)``."""
    r = _clean(returns)
    if len(r) < 2:
        return float("nan")
    return float(r.std(ddof=1) * math.sqrt(periods_per_year))


def cagr(returns: pd.Series, periods_per_year: int = 365) -> float:
    """Compound annual growth rate from a returns series.

    ``CAGR = (∏(1+r))^(periods_per_year / n) - 1`` where ``n`` is the number of
    periods. Returns ``np.nan`` on empty input; if terminal wealth ≤ 0 (total
    wipe-out) returns ``-1.0``.
    """
    r = _clean(returns)
    n = len(r)
    if n == 0:
        return float("nan")
    growth = float((1.0 + r).prod())
    if growth <= 0:
        return -1.0
    return float(growth ** (periods_per_year / n) - 1.0)


def max_drawdown(returns: pd.Series) -> float:
    """Maximum drawdown (negative float) of the wealth curve built from returns.

    Builds equity ``∏(1+r)`` then returns ``min(equity/cummax - 1)``. Note: this
    takes a *returns* series (whereas ``features.max_drawdown`` takes an equity
    series) so ``risk.summary`` can run from returns alone.
    """
    r = _clean(returns)
    if len(r) == 0:
        return float("nan")
    equity = (1.0 + r).cumprod()
    dd = equity / equity.cummax() - 1.0
    return float(dd.min())


def calmar(returns: pd.Series, periods_per_year: int = 365) -> float:
    """Calmar ratio = CAGR / |max drawdown|. ``np.nan`` if drawdown is 0."""
    mdd = max_drawdown(returns)
    if mdd == 0 or np.isnan(mdd):
        return float("nan")
    return float(cagr(returns, periods_per_year) / abs(mdd))


def hit_rate(returns: pd.Series) -> float:
    """Fraction of strictly-positive periods (win rate), in [0, 1].

    Zero-return periods (e.g. flat/out-of-market bars) are excluded from the
    denominator so an all-flat strategy doesn't read as a 0% hit rate.
    """
    r = _clean(returns)
    nonzero = r[r != 0.0]
    if len(nonzero) == 0:
        return float("nan")
    return float((nonzero > 0).mean())


# --------------------------------------------------------------------------- #
# Tail risk                                                                    #
# --------------------------------------------------------------------------- #
def var(returns: pd.Series, alpha: float = 0.05) -> float:
    """Historical Value-at-Risk at confidence ``1 - alpha`` (negative float).

    The ``alpha``-quantile of the empirical return distribution (e.g. the 5th
    percentile for ``alpha=0.05``). Returned as a signed return — a typical loss
    cutoff is negative. Empty input → ``np.nan``.
    """
    r = _clean(returns)
    if len(r) == 0:
        return float("nan")
    return float(np.quantile(r.to_numpy(), alpha))


def cvar(returns: pd.Series, alpha: float = 0.05) -> float:
    """Historical Conditional VaR / Expected Shortfall at ``alpha`` (negative float).

    Mean of returns at or below the historical VaR quantile — the average loss in
    the worst ``alpha`` tail. Falls back to the VaR itself if no observations sit
    at/below the quantile. Empty input → ``np.nan``.
    """
    r = _clean(returns)
    if len(r) == 0:
        return float("nan")
    v = np.quantile(r.to_numpy(), alpha)
    tail = r[r <= v]
    if len(tail) == 0:
        return float(v)
    return float(tail.mean())


# --------------------------------------------------------------------------- #
# Position sizing (Kelly)                                                      #
# --------------------------------------------------------------------------- #
def kelly_fraction(mean: float, var: float) -> float:
    """Continuous (Merton) Kelly fraction ``f* = mean / variance``.

    For a continuous return process with excess mean ``mean`` and variance
    ``var``, the growth-optimal leverage is ``f* = mu / sigma^2`` (Merton; see
    RESEARCH.md §2.13). Pass an *excess* mean if a risk-free rate applies.

    Caveat (in docstring per the brief): extremely sensitive to the estimated
    inputs; BTC's fat tails mean the safe fraction is *lower* than this — use a
    fractional Kelly (``c ∈ [0.25, 0.5]``) and hard caps. Returns ``np.nan`` for
    non-positive variance.
    """
    if var is None or var <= 0 or np.isnan(var):
        return float("nan")
    return float(mean / var)


def kelly(p: float, b: float) -> float:
    """Discrete binary-bet Kelly fraction ``f* = (b*p - q) / b``.

    For a bet that wins ``b`` per unit staked with probability ``p`` and loses the
    stake with probability ``q = 1 - p``: ``f* = (b*p - q) / b = p - q/b``
    (Kelly 1956). A negative result means no edge → don't bet (caller may clamp to
    0). Requires ``0 <= p <= 1`` and ``b > 0``.
    """
    if not (0.0 <= p <= 1.0):
        raise ValueError(f"p must be in [0, 1], got {p}")
    if b <= 0:
        raise ValueError(f"b (payoff ratio) must be > 0, got {b}")
    q = 1.0 - p
    return float((b * p - q) / b)


# --------------------------------------------------------------------------- #
# Probabilistic & Deflated Sharpe (the headline honesty metrics)              #
# --------------------------------------------------------------------------- #
def probabilistic_sharpe_ratio(
    sr: float,
    n: int,
    skew: float,
    kurt: float,
    sr_benchmark: float = 0.0,
) -> float:
    """Probabilistic Sharpe Ratio — Bailey & López de Prado (2012).

    The probability that the *true* Sharpe ratio exceeds a benchmark ``sr0``,
    accounting for sample length and non-normality (skew/kurtosis)::

        PSR(sr0) = Phi( (SR_hat - sr0) * sqrt(n - 1)
                        / sqrt(1 - skew*SR_hat + ((kurt - 1)/4) * SR_hat^2) )

    where ``Phi`` is the standard-normal CDF and ``kurt`` is the **non-excess**
    (raw) kurtosis (3 for a normal). All Sharpe inputs must be on the **same
    (per-period, non-annualized) frequency** as ``n`` so the ``sqrt(n-1)`` scaling
    is correct.

    Parameters
    ----------
    sr : float
        Observed (per-period) Sharpe ratio ``SR_hat``.
    n : int
        Number of return observations.
    skew : float
        Sample skewness of returns.
    kurt : float
        Sample (non-excess) kurtosis of returns; pass 3.0 for Gaussian.
    sr_benchmark : float, default 0.0
        Benchmark Sharpe ``sr0`` (e.g. the deflated benchmark from
        :func:`deflated_sharpe_ratio`).

    Returns
    -------
    float
        Probability in [0, 1]. Significant when ``> 0.95``.

    Reference
    ---------
    Bailey & López de Prado (2012), "The Sharpe Ratio Efficiency Frontier",
    *Journal of Risk* 15(2); SSRN 1821643.
    """
    if n is None or n < 2 or sr is None or np.isnan(sr):
        return float("nan")
    denom = 1.0 - skew * sr + ((kurt - 1.0) / 4.0) * sr * sr
    if denom <= 0 or np.isnan(denom):
        return float("nan")
    z = (sr - sr_benchmark) * math.sqrt(n - 1) / math.sqrt(denom)
    return float(stats.norm.cdf(z))


def deflated_sharpe_ratio(
    sr: float,
    n: int,
    skew: float,
    kurt: float,
    n_trials: int,
    var_trials_sr: float,
) -> float:
    """Deflated Sharpe Ratio — Bailey & López de Prado (2014).

    Benchmarks the observed Sharpe against the **expected maximum Sharpe of N
    skill-less trials**, then runs that benchmark through the PSR. This is the
    headline honesty metric: it deflates for the number of strategy
    configurations tried (``n_trials``), the sample length (``n``), and
    non-normality (``skew``/``kurt``).

    The expected-max benchmark uses the Bailey-LdP closed form::

        sr0 = sqrt(var_trials_sr) * [ (1 - gamma) * Phi^{-1}(1 - 1/N)
                                      +    gamma   * Phi^{-1}(1 - 1/(N*e)) ]

    with Euler-Mascheroni ``gamma ≈ 0.5772`` and ``e`` Euler's number, then::

        DSR = PSR(sr0) = Phi( (SR_hat - sr0) * sqrt(n - 1)
                              / sqrt(1 - skew*SR_hat + ((kurt - 1)/4)*SR_hat^2) )

    Parameters
    ----------
    sr : float
        Observed (per-period) Sharpe of the *selected* strategy.
    n : int
        Number of return observations.
    skew, kurt : float
        Sample skewness and **non-excess** kurtosis of the selected strategy's
        returns (pass 3.0 for Gaussian kurtosis).
    n_trials : int
        Number ``N`` of independent strategy configurations tried (the more you
        searched, the higher the skill-less benchmark, the lower the DSR).
    var_trials_sr : float
        Variance of the (per-period) Sharpe ratios *across* the ``n_trials``.

    Returns
    -------
    float
        Deflated Sharpe probability in [0, 1]. Significant when ``> 0.95``.
        ``np.nan`` on degenerate input.

    Reference
    ---------
    Bailey & López de Prado (2014), "The Deflated Sharpe Ratio: Correcting for
    Selection Bias, Backtest Overfitting, and Non-Normality", *Journal of
    Portfolio Management* 40(5):94-107; SSRN 2460551.
    """
    if n_trials is None or n_trials < 1 or var_trials_sr is None or var_trials_sr < 0:
        return float("nan")
    if np.isnan(var_trials_sr):
        return float("nan")

    gamma = 0.5772156649015329  # Euler-Mascheroni constant
    if n_trials == 1:
        # A single trial: no selection inflation, benchmark is 0.
        sr0 = 0.0
    else:
        e = math.e
        z1 = stats.norm.ppf(1.0 - 1.0 / n_trials)
        z2 = stats.norm.ppf(1.0 - 1.0 / (n_trials * e))
        expected_max_z = (1.0 - gamma) * z1 + gamma * z2
        sr0 = math.sqrt(var_trials_sr) * expected_max_z

    return probabilistic_sharpe_ratio(sr, n, skew, kurt, sr_benchmark=sr0)


# --------------------------------------------------------------------------- #
# Bundle                                                                       #
# --------------------------------------------------------------------------- #
def summary(
    returns: pd.Series,
    equity: Optional[pd.Series] = None,
    periods_per_year: int = 365,
) -> dict:
    """Bundle the core performance & risk stats into a dict.

    Computes annualized Sharpe/Sortino/vol, CAGR, Calmar, max drawdown, hit rate,
    historical VaR/CVaR (5%), and a single-trial Probabilistic Sharpe Ratio
    (``sr_benchmark=0``, ``n_trials=1``). The PSR/DSR here use the **per-period**
    Sharpe (de-annualized) so the ``sqrt(n-1)`` scaling is correct, with the
    sample skew and non-excess kurtosis of the realized returns.

    Note: a meaningful **deflated** Sharpe requires the number of trials ``N`` and
    the cross-trial Sharpe variance from the search harness (DESIGN.md), so it is
    *not* computed here — call :func:`deflated_sharpe_ratio` from the backtest/scan
    layer with those values. ``n_trials=1`` here is the honest "no-selection" floor.

    Parameters
    ----------
    returns : pd.Series
        Per-period (net-of-cost) returns.
    equity : pd.Series, optional
        Pre-built wealth curve; if omitted it is derived as ``∏(1+r)``. Only used
        to surface a terminal-equity figure; drawdown is computed from returns.
    periods_per_year : int, default 365

    Returns
    -------
    dict
        Keyed performance/risk statistics, all native floats/ints for easy JSON
        export to the dashboard.
    """
    r = _clean(returns)
    n = int(len(r))

    if equity is None:
        equity = _equity_from_returns(r)
    else:
        equity = pd.Series(equity, dtype="float64").dropna()

    sr_ann = sharpe(r, periods_per_year=periods_per_year)

    # Per-period Sharpe & moments for PSR (must match the n used in sqrt(n-1)).
    if n >= 2 and r.std(ddof=1) not in (0.0, np.nan) and not np.isnan(r.std(ddof=1)):
        sr_period = float(r.mean() / r.std(ddof=1))
        skew = float(stats.skew(r.to_numpy(), bias=False))
        # Non-excess (raw) kurtosis: scipy's fisher=False gives Pearson kurtosis.
        kurt = float(stats.kurtosis(r.to_numpy(), fisher=False, bias=False))
        psr = probabilistic_sharpe_ratio(sr_period, n, skew, kurt, sr_benchmark=0.0)
    else:
        sr_period = float("nan")
        skew = float("nan")
        kurt = float("nan")
        psr = float("nan")

    return {
        "n_periods": n,
        "cagr": cagr(r, periods_per_year),
        "sharpe": sr_ann,
        "sortino": sortino(r, periods_per_year=periods_per_year),
        "volatility": volatility(r, periods_per_year),
        "calmar": calmar(r, periods_per_year),
        "max_drawdown": max_drawdown(r),
        "hit_rate": hit_rate(r),
        "var_5pct": var(r, 0.05),
        "cvar_5pct": cvar(r, 0.05),
        "skew": skew,
        "kurtosis": kurt,
        "sharpe_per_period": sr_period,
        "psr": psr,
        "terminal_equity": float(equity.iloc[-1]) if len(equity) else float("nan"),
    }
