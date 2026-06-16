"""features.py — pure, no-look-ahead indicators / signals for btc-quant.

Every function operates on pandas ``Series``/``DataFrame`` and uses **only past
data at each point in time** (no look-ahead). Where a function consumes a window
of ``n`` observations, the value at index ``t`` is computed from observations at
``t-n+1 .. t`` (inclusive) — i.e. the bar at ``t`` is allowed to use its own
*close*, but never any future bar. Signals derived from these features must still
be shifted by one bar before they trade (that shift is the backtester's job; see
``backtest.run`` in DESIGN.md), so a feature value computed at the close of bar
``t`` is only actionable on bar ``t+1``.

Conventions
-----------
* ``close`` / ``s`` : a price (or generic) ``pd.Series`` indexed by a
  ``DatetimeIndex`` (ascending). Returns are simple unless stated.
* ``periods_per_year=365`` for daily crypto bars (24/7 market — no weekends/holidays).
* Annualization of volatility uses ``sqrt(periods_per_year)``.
* NaNs at the head of rolling outputs are preserved (warm-up); they are *not*
  forward-filled, so downstream code never silently trades on an unwarmed window.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

__all__ = [
    "log_returns",
    "simple_returns",
    "realized_vol",
    "realized_vol_from_prices",
    "variance_risk_premium",
    "atr",
    "sma",
    "ema",
    "momentum",
    "zscore",
    "ou_half_life",
    "ou_sigma_eq",
    "rsi",
    "rolling_sharpe",
    "drawdown",
    "max_drawdown",
    # Regime / mean-reversion diagnostics (trend-vs-revert gate + OHLC vol).
    "yang_zhang_vol",
    "hurst",
    "variance_ratio",
    "adx",
    # Option-surface helpers (consume data.get_option_chain frames).
    "year_fraction_to_expiry",
    "atm_iv",
    "iv_term_structure",
    "iv_skew_25d",
    "smile",
    "black76_greeks",
    "max_pain",
    "gamma_concentration",
]


# --------------------------------------------------------------------------- #
# Returns                                                                      #
# --------------------------------------------------------------------------- #
def log_returns(close: pd.Series) -> pd.Series:
    """Continuously-compounded (log) returns ``r_t = ln(P_t / P_{t-1})``.

    No look-ahead: ``r_t`` uses only ``P_t`` and ``P_{t-1}``. The first value is
    NaN (no prior price).
    """
    close = pd.Series(close, dtype="float64")
    return np.log(close / close.shift(1))


def simple_returns(close: pd.Series) -> pd.Series:
    """Simple (arithmetic) returns ``r_t = P_t / P_{t-1} - 1``.

    No look-ahead: uses only ``P_t`` and ``P_{t-1}``; first value is NaN.
    """
    close = pd.Series(close, dtype="float64")
    return close.pct_change()


# --------------------------------------------------------------------------- #
# Volatility                                                                   #
# --------------------------------------------------------------------------- #
def realized_vol(
    returns: pd.Series,
    window: int = 20,
    periods_per_year: int = 365,
) -> pd.Series:
    """Annualized rolling realized volatility of a *returns* series.

    ``sigma_t = std(r_{t-window+1 .. t}) * sqrt(periods_per_year)``.

    Uses a trailing window ending at ``t`` (inclusive) → no look-ahead. The
    sample std (``ddof=1``) is used. The leading ``window-1`` values are NaN.

    Parameters
    ----------
    returns : pd.Series
        Per-period returns (simple or log; std is nearly identical for small r).
    window : int, default 20
        Trailing window length in bars.
    periods_per_year : int, default 365
        Bars per year (365 for daily crypto, 24*365 for hourly).
    """
    returns = pd.Series(returns, dtype="float64")
    return returns.rolling(window).std(ddof=1) * np.sqrt(periods_per_year)


def realized_vol_from_prices(
    close: pd.Series,
    window: int = 20,
    periods_per_year: int = 365,
) -> pd.Series:
    """Annualized rolling realized vol computed straight from a *price* series.

    Convenience aligner for :func:`variance_risk_premium`: it turns prices into log
    returns and applies :func:`realized_vol`, so the realized leg of the VRP comes
    out on the same trailing-``window``, annualized basis as the close-driven
    realized vol used elsewhere. No look-ahead (trailing window ending at ``t``);
    the leading ``window`` values are NaN (one lost to the return diff).

    Parameters
    ----------
    close : pd.Series
        Price (close) series.
    window : int, default 20
        Trailing window length in bars.
    periods_per_year : int, default 365
        Bars per year (365 daily crypto, ``24*365`` hourly).

    Returns
    -------
    pd.Series
        Annualized realized volatility as a **decimal** (e.g. ``0.55`` == 55%).
    """
    rets = log_returns(close)
    return realized_vol(rets, window=window, periods_per_year=periods_per_year)


def variance_risk_premium(
    implied_vol: pd.Series,
    realized_vol: pd.Series,
) -> pd.Series:
    """Variance risk premium (in *volatility* terms): ``VRP = implied - realized``.

    Aligns ``implied_vol`` and ``realized_vol`` on their common index and returns
    their difference. **Both must be in the same units** — both annualized, both
    either decimals (``0.55``) or both percent (``55.0``). In particular Deribit's
    DVOL (``data.get_dvol``) is published in **percent**, whereas
    :func:`realized_vol` / :func:`realized_vol_from_prices` return **decimals**, so
    convert one side before calling (e.g. ``dvol / 100`` to match a decimal
    realized vol). The output carries whatever common unit you pass in.

    This is the *vol-spread* form (``IV - RV``); the variance form ``IV^2 - RV^2``
    is a monotone transform of it and is not computed here. The realized leg here
    is the *trailing* realized vol; a forward-looking VRP would instead compare IV
    at ``t`` against the realized vol over ``(t, t+horizon]`` — that is **not** done
    here (it would be look-ahead), so treat this as a contemporaneous diagnostic.

    Interpretation / honest caveat
        A **positive** VRP (implied > realized) is the normal state and is the
        premium a vol *seller* is paid: option buyers systematically overpay for
        insurance. But it is **descriptive, not a tradeable signal here** — earning
        it means being **short a fat left tail** (RESEARCH.md §2.8): the payoff is
        sharply **negatively skewed**, VRP spikes (and short-vol loses badly)
        around large moves in *either* direction, and short samples systematically
        understate the left-tail losses. A high Sharpe on this payoff is exactly
        the trap the Deflated Sharpe is built to catch. Size small, never naked —
        and note this function only *measures* the premium; it does not size or
        trade it.

    Parameters
    ----------
    implied_vol : pd.Series
        Option-implied (annualized) volatility, e.g. Deribit DVOL.
    realized_vol : pd.Series
        Realized (annualized) volatility over the comparison horizon, in the **same
        units** as ``implied_vol``.

    Returns
    -------
    pd.Series
        ``implied_vol - realized_vol`` on the intersection of the two indices,
        named ``variance_risk_premium``.
    """
    iv = pd.Series(implied_vol, dtype="float64")
    rv = pd.Series(realized_vol, dtype="float64")
    idx = iv.index.intersection(rv.index)
    vrp = iv.reindex(idx) - rv.reindex(idx)
    return vrp.rename("variance_risk_premium")


def atr(df: pd.DataFrame, window: int = 14) -> pd.Series:
    """Average True Range (Wilder) over a trailing ``window``.

    True range at ``t`` is ``max(high-low, |high - prev_close|,
    |low - prev_close|)`` and the ATR is its Wilder EMA (``alpha = 1/window``).
    Needs columns ``high``, ``low``, ``close``. No look-ahead: every component at
    ``t`` uses only bar ``t`` and the prior close.

    Parameters
    ----------
    df : pd.DataFrame
        Must contain ``high``, ``low``, ``close`` columns.
    window : int, default 14
    """
    high = df["high"].astype("float64")
    low = df["low"].astype("float64")
    close = df["close"].astype("float64")
    prev_close = close.shift(1)

    true_range = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    # Wilder smoothing == EMA with alpha = 1/window.
    return true_range.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()


# --------------------------------------------------------------------------- #
# Moving averages                                                              #
# --------------------------------------------------------------------------- #
def sma(s: pd.Series, n: int) -> pd.Series:
    """Simple moving average over a trailing window of ``n`` bars (no look-ahead).

    Leading ``n-1`` values are NaN.
    """
    s = pd.Series(s, dtype="float64")
    return s.rolling(n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    """Exponential moving average with span ``n`` (``alpha = 2/(n+1)``).

    Uses ``adjust=False`` (recursive form) so the value at ``t`` depends only on
    past and present observations — no look-ahead. ``min_periods=n`` keeps the
    warm-up region NaN.
    """
    s = pd.Series(s, dtype="float64")
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


# --------------------------------------------------------------------------- #
# Momentum / dispersion                                                        #
# --------------------------------------------------------------------------- #
def momentum(close: pd.Series, lookback: int = 90) -> pd.Series:
    """Total (cumulative) return over the trailing ``lookback`` bars — the TSMOM signal.

    ``m_t = P_t / P_{t-lookback} - 1``.

    Positive ⇒ up-trend over the lookback. No look-ahead: uses ``P_t`` and
    ``P_{t-lookback}`` only. Leading ``lookback`` values are NaN.

    See RESEARCH.md §2.1 (Shen, Urquhart & Wang 2022): crypto TSMOM works mainly
    at short lookbacks (days–4 weeks); do NOT use a 12-month lookback.
    """
    close = pd.Series(close, dtype="float64")
    return close / close.shift(lookback) - 1.0


def zscore(s: pd.Series, window: int = 30) -> pd.Series:
    """Rolling z-score ``(s_t - mean) / std`` over a trailing ``window``.

    Mean and sample std (``ddof=1``) are computed over ``t-window+1 .. t`` → no
    look-ahead. Used by the pairs/OU spread-reversion signals (RESEARCH.md §2.9).
    Leading ``window-1`` values are NaN.
    """
    s = pd.Series(s, dtype="float64")
    roll = s.rolling(window)
    return (s - roll.mean()) / roll.std(ddof=1)


def ou_half_life(spread: pd.Series) -> float:
    """Ornstein-Uhlenbeck mean-reversion half-life from an AR(1) fit.

    Fits the discrete AR(1) ``ΔX_t = a + b * X_{t-1} + e_t`` by OLS. With the
    continuous OU SDE ``dX = kappa*(mu - X) dt + sigma dW``, the discretization
    gives ``b = -kappa`` (per unit time-step), so::

        kappa     = -b
        half_life = ln(2) / kappa = -ln(2) / b

    Returns the half-life **in bars**. If the series is not mean-reverting
    (``b >= 0``, i.e. a random walk or trending), there is no finite reversion
    and ``np.inf`` is returned — callers should reject such a spread (RESEARCH.md
    §2.10: parameter non-stationarity is the killer; use only short, stable
    half-lives on a series independently established as stationary).

    Reference: Leung & Li (2015), *Optimal Mean Reversion Trading*.

    Parameters
    ----------
    spread : pd.Series
        The (assumed stationary) spread / residual series.

    Returns
    -------
    float
        Half-life in bars; ``np.inf`` if non-mean-reverting; ``np.nan`` if too
        few observations to fit.
    """
    x = pd.Series(spread, dtype="float64").dropna()
    if len(x) < 3:
        return float("nan")

    x_lag = x.shift(1)
    delta = x - x_lag

    df = pd.concat([delta, x_lag], axis=1).dropna()
    delta = df.iloc[:, 0].to_numpy()
    x_lag = df.iloc[:, 1].to_numpy()

    # OLS of delta on [1, x_lag]; slope is b.
    design = np.column_stack([np.ones_like(x_lag), x_lag])
    coef, *_ = np.linalg.lstsq(design, delta, rcond=None)
    b = coef[1]

    if b >= 0:  # not mean-reverting
        return float("inf")
    return float(np.log(2.0) / -b)


def ou_sigma_eq(spread: pd.Series) -> float:
    """Stationary (equilibrium) standard deviation of an OU/AR(1) spread.

    Fits the same discrete AR(1) ``ΔX_t = a + b·X_{t-1} + e_t`` as
    :func:`ou_half_life` (so the two share one fit convention), then returns the
    standard deviation of the AR(1) **stationary distribution**::

        phi      = 1 + b                         # AR(1) coefficient
        sigma_eq = sqrt( var(e) / (1 - phi^2) )  # valid only when |phi| < 1

    where ``var(e)`` is the OLS residual variance (RSS / (n - 2)). This is the
    *model-implied* dispersion of the spread — the OU counterpart to the
    empirical rolling standard deviation used by the z-score. Used by the
    ``pairs_ou`` research variant to normalize deviations by the OU model rather
    than the sample std, isolating "model vs empirical" (RESEARCH.md §2.10).

    Returns ``inf`` when the series is not mean-reverting (``b >= 0`` ⇒ |phi| ≥ 1,
    no finite stationary variance) and ``nan`` when there are too few observations
    or the residual variance is degenerate. Callers should treat a non-finite
    result the same way they treat a non-finite half-life: stand aside.

    Reference: Leung & Li (2015), *Optimal Mean Reversion Trading*.
    """
    x = pd.Series(spread, dtype="float64").dropna()
    if len(x) < 3:
        return float("nan")

    x_lag = x.shift(1)
    delta = x - x_lag
    df = pd.concat([delta, x_lag], axis=1).dropna()
    delta = df.iloc[:, 0].to_numpy()
    x_lag = df.iloc[:, 1].to_numpy()

    design = np.column_stack([np.ones_like(x_lag), x_lag])
    with np.errstate(all="ignore"):  # extreme/explosive inputs must not warn
        coef, *_ = np.linalg.lstsq(design, delta, rcond=None)
        b = coef[1]
        phi = 1.0 + b
        if not np.isfinite(b) or b >= 0 or abs(phi) >= 1.0:
            return float("inf")  # not mean-reverting → no finite stationary variance
        n = len(delta)
        if n <= 2:
            return float("nan")
        resid = delta - design @ coef
        var_e = float(resid @ resid) / (n - 2)
        var_eq = var_e / (1.0 - phi * phi)
    if not np.isfinite(var_eq) or var_eq <= 0:
        return float("nan")
    return float(np.sqrt(var_eq))


# --------------------------------------------------------------------------- #
# Oscillators                                                                  #
# --------------------------------------------------------------------------- #
def rsi(close: pd.Series, window: int = 14) -> pd.Series:
    """Wilder's Relative Strength Index over ``window`` bars (0–100).

    Gains/losses are smoothed with Wilder's EMA (``alpha = 1/window``).
    ``RSI = 100 - 100 / (1 + avg_gain / avg_loss)``. No look-ahead: each value
    uses only past/present price changes. When ``avg_loss == 0`` the RSI is 100.

    Parameters
    ----------
    close : pd.Series
    window : int, default 14
    """
    close = pd.Series(close, dtype="float64")
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)

    avg_gain = gain.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()
    avg_loss = loss.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()

    rs = avg_gain / avg_loss
    out = 100.0 - 100.0 / (1.0 + rs)
    # avg_loss == 0 ⇒ rs == inf ⇒ out already 100; force exact when fully warmed.
    out = out.where(avg_loss != 0.0, 100.0)
    # Keep warm-up region NaN.
    out[avg_gain.isna()] = np.nan
    return out


# --------------------------------------------------------------------------- #
# Rolling performance                                                          #
# --------------------------------------------------------------------------- #
def rolling_sharpe(
    returns: pd.Series,
    window: int = 90,
    periods_per_year: int = 365,
) -> pd.Series:
    """Annualized rolling Sharpe ratio of a *returns* series (excess rf assumed 0).

    ``SR_t = (mean / std) * sqrt(periods_per_year)`` over the trailing ``window``
    (sample std, ``ddof=1``). No look-ahead. Leading ``window-1`` values are NaN;
    windows with zero variance yield NaN/inf and are left as-is for the caller.
    """
    returns = pd.Series(returns, dtype="float64")
    roll = returns.rolling(window)
    mean = roll.mean()
    std = roll.std(ddof=1)
    return (mean / std) * np.sqrt(periods_per_year)


# --------------------------------------------------------------------------- #
# Drawdown                                                                     #
# --------------------------------------------------------------------------- #
def drawdown(equity: pd.Series) -> pd.Series:
    """Drawdown series ``equity / running_peak - 1`` (≤ 0).

    ``running_peak`` is the cumulative max up to and including ``t`` → no
    look-ahead. ``equity`` is a wealth/level series (e.g. cumulative product of
    ``1 + returns``), not a returns series.
    """
    equity = pd.Series(equity, dtype="float64")
    running_peak = equity.cummax()
    return equity / running_peak - 1.0


def max_drawdown(equity: pd.Series) -> float:
    """Maximum drawdown of an equity/level series as a negative float.

    Returns the most negative value of :func:`drawdown` (e.g. ``-0.85`` for an
    85% peak-to-trough loss). Empty input returns ``np.nan``.
    """
    dd = drawdown(equity)
    if dd.empty or dd.isna().all():
        return float("nan")
    return float(dd.min())


# --------------------------------------------------------------------------- #
# Regime / mean-reversion diagnostics (trend-vs-revert classifiers + OHLC vol)  #
# --------------------------------------------------------------------------- #
# These gate mean-reversion: a fade is only positive-expectancy in a ranging /
# anti-persistent regime. All trailing-window → causal (no look-ahead). Python-only
# research layer (no dashboard consumer yet → not mirrored in quant.js).
def yang_zhang_vol(df: pd.DataFrame, window: int = 20, periods_per_year: int = 365) -> pd.Series:
    """Yang-Zhang (2000) drift-independent OHLC realized volatility, annualized.

    ``σ²_YZ = σ²_O + k·σ²_C + (1−k)·σ²_RS`` with ``k = 0.34/(1.34 + (n+1)/(n−1))``,
    combining the overnight-gap variance (``ln(O_t/C_{t−1})``), the open-to-close
    variance (``ln(C_t/O_t)``), and the Rogers-Satchell term. It is the most efficient
    and least biased RV estimator for gapping/24-7 data; close-to-close (``realized_vol``)
    is the noisy fallback. Trailing ``window`` → causal."""
    o = pd.Series(df["open"], dtype="float64")
    h = pd.Series(df["high"], dtype="float64")
    lo = pd.Series(df["low"], dtype="float64")
    c = pd.Series(df["close"], dtype="float64")
    log_o = np.log(o / c.shift(1))                                   # overnight gap
    log_c = np.log(c / o)                                            # open-to-close
    rs = np.log(h / c) * np.log(h / o) + np.log(lo / c) * np.log(lo / o)  # Rogers-Satchell
    n = int(window)
    k = 0.34 / (1.34 + (n + 1.0) / (n - 1.0)) if n > 1 else 0.34
    var_o = log_o.rolling(n).var(ddof=1)
    var_c = log_c.rolling(n).var(ddof=1)
    var_rs = rs.rolling(n).mean()
    yz = np.sqrt((var_o + k * var_c + (1.0 - k) * var_rs).clip(lower=0.0)) * math.sqrt(periods_per_year)
    return yz.rename("yang_zhang_vol")


def _hurst_estimate(arr: np.ndarray, max_lag: int = 20) -> float:
    """Hurst via the slope of log(RMS of lag-τ differences) vs log(τ)."""
    a = np.asarray(arr, dtype="float64")
    a = a[np.isfinite(a)]
    n = len(a)
    if n < 20:
        return float("nan")
    tau, lg = [], []
    for lag in range(2, min(int(max_lag), n // 2)):
        diff = a[lag:] - a[:-lag]
        s = math.sqrt(float(np.mean(diff * diff)))
        if s > 0.0:
            tau.append(math.log(s)); lg.append(math.log(lag))
    if len(tau) < 3:
        return float("nan")
    return float(np.polyfit(lg, tau, 1)[0])


def hurst(series: pd.Series, window: int | None = None, max_lag: int = 20):
    """Hurst exponent: ``H<0.5`` anti-persistent (mean-reverting), ``≈0.5`` random walk,
    ``>0.5`` trending. With ``window`` set, returns a TRAILING rolling ``H`` (the causal
    regime gate); else a single whole-sample float. Coarse on short windows — a regime
    flag, not a precise measurement (the audit's R/S-bias caveat applies)."""
    s = pd.Series(series, dtype="float64")
    if window is None:
        return _hurst_estimate(s.to_numpy(), max_lag)
    return s.rolling(int(window)).apply(lambda a: _hurst_estimate(a, max_lag), raw=True).rename("hurst")


def variance_ratio(returns: pd.Series, q: int = 2) -> dict:
    """Lo-MacKinlay (1988) variance ratio ``VR(q) = Var(q-period)/(q·Var(1-period))`` with
    the **heteroskedasticity-robust** ``z*`` (valid under conditional heteroskedasticity —
    the crypto case, and per Lo-MacKinlay more reliable than ADF/Box-Pierce here).
    ``VR<1`` mean-reverting, ``>1`` trending, ``≈1`` random walk. Returns
    ``{vr, z_star, p_value, n}`` (two-sided p via ``erfc``)."""
    r = pd.Series(returns, dtype="float64").dropna().to_numpy()
    nq = len(r)
    if nq < q + 2 or q < 2:
        return {"vr": float("nan"), "z_star": float("nan"), "p_value": float("nan"), "n": nq}
    mu = r.mean()
    dev = r - mu
    sse = float(np.sum(dev * dev))
    var1 = sse / (nq - 1)
    if var1 <= 0:
        return {"vr": float("nan"), "z_star": float("nan"), "p_value": float("nan"), "n": nq}
    qsum = np.convolve(r, np.ones(q), mode="valid")          # overlapping q-period sums
    m = q * (nq - q + 1) * (1.0 - q / nq)                    # m already divides out the q
    varq = float(np.sum((qsum - q * mu) ** 2)) / m           # ⇒ σ²_b is PER-PERIOD
    vr = varq / var1
    theta = 0.0                                              # robust asymptotic variance
    for j in range(1, q):
        dj = float(np.sum((dev[j:] ** 2) * (dev[:-j] ** 2))) / (sse * sse)
        w = 2.0 * (q - j) / q
        theta += (w * w) * dj
    # z* = (VR−1)/√θ* is asymptotically N(0,1); the 1/T scaling is already inside θ*.
    z = (vr - 1.0) / math.sqrt(theta) if theta > 0 else float("nan")
    p = math.erfc(abs(z) / math.sqrt(2.0)) if z == z else float("nan")
    return {"vr": float(vr), "z_star": float(z), "p_value": float(p), "n": nq}


def adx(df: pd.DataFrame, window: int = 14) -> pd.Series:
    """Wilder's Average Directional Index (trend strength, 0-100). ``ADX>~25`` ⇒ trending,
    ``<~20`` ⇒ ranging — the trend gate for mean reversion. Wilder-smoothed (RMA,
    ``α=1/window``) → causal."""
    h = pd.Series(df["high"], dtype="float64")
    lo = pd.Series(df["low"], dtype="float64")
    c = pd.Series(df["close"], dtype="float64")
    up, dn = h.diff(), -lo.diff()
    plus_dm = pd.Series(np.where((up > dn) & (up > 0.0), up, 0.0), index=h.index)
    minus_dm = pd.Series(np.where((dn > up) & (dn > 0.0), dn, 0.0), index=h.index)
    tr = pd.concat([h - lo, (h - c.shift(1)).abs(), (lo - c.shift(1)).abs()], axis=1).max(axis=1)
    n = int(window)
    rma = lambda x: x.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    atr_ = rma(tr).replace(0.0, np.nan)
    plus_di = 100.0 * rma(plus_dm) / atr_
    minus_di = 100.0 * rma(minus_dm) / atr_
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)
    return rma(dx).rename("adx")


# --------------------------------------------------------------------------- #
# Option surface — IV smile / term structure / skew (consume option chains)    #
# --------------------------------------------------------------------------- #
# These helpers operate on the tidy per-contract frame returned by
# :func:`btcquant.data.get_option_chain` (one row per option, columns
# ``[expiry, strike, opt_type, iv, ..., underlying_price, bid_price, ask_price]``;
# ``iv`` is already a **decimal** annualized vol because data.py applied the
# brief-§1.2 ``mark_iv / 100`` fix). They are **pure** (no network, no mutation of
# the input) and carry the brief-§2 SIGNAL-vs-DESCRIPTIVE classification in their
# docstrings.
#
# Conventions used throughout this block
# --------------------------------------
# * Forward anchor: the per-expiry forward ``F`` is taken from ``underlying_price``
#   (Deribit's own forward; ``ticker.interest_rate ≈ 0`` so r is absorbed — brief
#   §1.3 fast-path). We do **not** solve put-call parity here (that needs both
#   legs cleanly quoted); when ``underlying_price`` is missing we fall back to the
#   median strike as a last resort and the caller should treat the result as soft.
# * Year-fraction ``T``: ACT/365 to the 08:00-UTC expiry (brief §1.5), via
#   :func:`year_fraction_to_expiry`.
# * Delta convention (brief §1.4d caveat): Deribit ships **plain Black-Scholes
#   spot delta on the inverse payoff**, not an FX premium-adjusted/forward delta.
#   We locate the 25-delta strikes by evaluating a Black-Scholes delta on the
#   *observed* per-strike IV, using ``F`` as the forward and ``r = 0`` (consistent
#   with the forward anchor). One convention, labelled, applied to every expiry —
#   a "25-delta" here is therefore a Deribit-style 25d, not an FX-desk 25d.

def _norm_cdf(x: np.ndarray) -> np.ndarray:
    """Standard-normal CDF via ``erf`` (no scipy dependency)."""
    return 0.5 * (1.0 + np.vectorize(math.erf)(np.asarray(x, dtype="float64") / math.sqrt(2.0)))


def year_fraction_to_expiry(
    expiry: pd.Timestamp,
    now: pd.Timestamp | None = None,
) -> float:
    """ACT/365 year-fraction from ``now`` to an 08:00-UTC option ``expiry`` (brief §1.5).

    DESCRIPTIVE helper. Deribit options expire at 08:00 UTC and IV is annualized
    ACT/365, so ``T = (expiry - now) / 365 days`` matches the quote convention.
    Counting calendar days, a 360-day year, or to midnight mis-annualizes IV and
    distorts the front of the term structure most. ``now`` defaults to the current
    UTC time. Returns ``T`` in **years** (can be ≤ 0 for an already-expired
    contract; callers exclude the very front where ``T → 0`` makes ATM IV
    unstable).
    """
    expiry = pd.Timestamp(expiry)
    if expiry.tz is None:
        expiry = expiry.tz_localize("UTC")
    if now is None:
        now = pd.Timestamp.utcnow().tz_localize(None).tz_localize("UTC")
    else:
        now = pd.Timestamp(now)
        if now.tz is None:
            now = now.tz_localize("UTC")
    seconds = (expiry - now).total_seconds()
    return seconds / (365.0 * 24.0 * 3600.0)


def _expiry_slice(chain: pd.DataFrame, expiry) -> pd.DataFrame:
    """Return the rows of ``chain`` for a single ``expiry`` (UTC-coerced match)."""
    exp = pd.Timestamp(expiry)
    if exp.tz is None:
        exp = exp.tz_localize("UTC")
    col = pd.to_datetime(chain["expiry"], utc=True)
    return chain[col == exp]


def _forward(slice_df: pd.DataFrame) -> float:
    """Per-expiry forward ``F`` from ``underlying_price`` (brief §1.3 fast-path).

    Uses the median of the non-null ``underlying_price`` across the strip (robust
    to one stale row). Falls back to the median strike if it is entirely missing,
    in which case the anchor is soft and the caller's result is approximate.
    """
    up = pd.to_numeric(slice_df.get("underlying_price"), errors="coerce").dropna()
    if len(up):
        return float(up.median())
    strikes = pd.to_numeric(slice_df["strike"], errors="coerce").dropna()
    return float(strikes.median()) if len(strikes) else float("nan")


def _otm_iv_by_strike(slice_df: pd.DataFrame, forward: float) -> pd.DataFrame:
    """Collapse one expiry to a clean **OTM-only** (strike, iv) ladder.

    Keeps OTM puts (``K < F``) and OTM calls (``K > F``) — the tighter, more liquid
    side; ITM IV is parity-redundant (brief §1.4a). At a strike where both an OTM
    call and OTM put could appear we prefer the OTM contract for that side. Drops
    rows with a null/≤0 IV. Returns a frame sorted by strike with a single ``iv``
    per strike (mean if duplicates remain), suitable for interpolation.
    """
    df = slice_df.copy()
    df["strike"] = pd.to_numeric(df["strike"], errors="coerce")
    df["iv"] = pd.to_numeric(df["iv"], errors="coerce")
    df = df.dropna(subset=["strike", "iv"])
    df = df[df["iv"] > 0.0]
    if df.empty or not np.isfinite(forward):
        return pd.DataFrame(columns=["strike", "iv"])
    # OTM side: puts below F, calls above F.
    is_otm = ((df["opt_type"] == "P") & (df["strike"] <= forward)) | (
        (df["opt_type"] == "C") & (df["strike"] >= forward)
    )
    otm = df[is_otm]
    if otm.empty:
        otm = df  # degrade: keep everything rather than emptying the smile
    grouped = otm.groupby("strike", as_index=False)["iv"].mean()
    return grouped.sort_values("strike").reset_index(drop=True)


def _interp_iv_at_strike(ladder: pd.DataFrame, target_strike: float) -> float:
    """Shape-preserving (PCHIP) interpolation of IV at ``target_strike``.

    Falls back to linear (and then to nearest) when too few strikes are present
    for PCHIP. Never extrapolates with a wiggly cubic spline (brief §1.4a: cubic
    splines manufacture butterfly arbitrage on sparse BTC strikes). Outside the
    observed strike range the value is clamped to the nearest endpoint IV.
    """
    if ladder.empty:
        return float("nan")
    ks = ladder["strike"].to_numpy(dtype="float64")
    ivs = ladder["iv"].to_numpy(dtype="float64")
    if len(ks) == 1:
        return float(ivs[0])
    # Clamp to the observed range (no extrapolation).
    if target_strike <= ks[0]:
        return float(ivs[0])
    if target_strike >= ks[-1]:
        return float(ivs[-1])
    try:
        from scipy.interpolate import PchipInterpolator

        if len(ks) >= 3:
            return float(PchipInterpolator(ks, ivs)(target_strike))
    except Exception:  # pragma: no cover - scipy edge cases
        pass
    return float(np.interp(target_strike, ks, ivs))


def atm_iv(chain: pd.DataFrame, expiry, now: pd.Timestamp | None = None) -> float:
    """At-the-money-forward implied vol for one ``expiry`` (decimal). **DESCRIPTIVE.**

    Reads the ATMF vol by interpolating the OTM-only IV ladder at the per-expiry
    forward ``F = underlying_price`` (brief §1.3 fast-path, §1.4b). The IV is a
    **decimal** (data.py already divided ``mark_iv`` by 100). Returns ``np.nan`` if
    the expiry has no usable strikes.

    Tag: **DESCRIPTIVE** for returns. ATM IV is the anchor for the term structure
    and DVOL cross-check (brief §1.6); the *level* is a vol forecast / regime
    input, not a return-timing signal (brief §2.2).

    Parameters
    ----------
    chain : pd.DataFrame
        Output of :func:`btcquant.data.get_option_chain`.
    expiry : Timestamp-like
        The expiry to read (matched against the chain's ``expiry`` column, UTC).
    now : Timestamp, optional
        Unused for the level itself (kept for signature symmetry / future
        parity-solve); ATM IV does not depend on ``T``.
    """
    slice_df = _expiry_slice(chain, expiry)
    if slice_df.empty:
        return float("nan")
    fwd = _forward(slice_df)
    ladder = _otm_iv_by_strike(slice_df, fwd)
    return _interp_iv_at_strike(ladder, fwd)


def iv_term_structure(chain: pd.DataFrame, now: pd.Timestamp | None = None) -> pd.DataFrame:
    """ATM IV vs time-to-expiry across the whole chain. **SIGNAL (vol-forecast/regime).**

    For every distinct expiry, compute ``T`` (ACT/365 to 08:00 UTC, brief §1.5) and
    the ATMF IV (:func:`atm_iv`), returning a frame sorted by ``T`` with columns
    ``[expiry, T, atm_iv]`` (``atm_iv`` decimal). Expiries with ``T <= 0`` (already
    expired) are dropped; the very front (``T`` within a few hours) is unstable as
    ``T → 0`` and should be down-weighted by the caller (brief §1.4b).

    Tag: **SIGNAL for realized-vol forecasting / regime classification**,
    **DESCRIPTIVE / NOT a return-timing signal** (brief §2.2: Caporin et al. 2024 —
    smile/term slopes forecast weekly realized vol but do **not** predict returns).
    Use the slope (e.g. front vs ~90d) for sizing/regime, never as a buy/sell.

    Note (brief §1.4b): a correct *interpolation across tenors* must be done in
    **total variance** ``w = IV^2 * T`` (never in IV), or you manufacture calendar
    arbitrage. This function returns the raw per-expiry ATM IVs; any tenor
    interpolation (e.g. a constant-30d point) is the consumer's job and must use
    the total-variance form.
    """
    out_rows = []
    expiries = pd.to_datetime(chain["expiry"], utc=True).dropna().unique()
    for exp in sorted(pd.to_datetime(expiries, utc=True)):
        t = year_fraction_to_expiry(exp, now=now)
        if t <= 0.0:
            continue
        iv = atm_iv(chain, exp, now=now)
        out_rows.append({"expiry": exp, "T": t, "atm_iv": iv})
    out = pd.DataFrame(out_rows, columns=["expiry", "T", "atm_iv"])
    return out.sort_values("T").reset_index(drop=True)


def _bs_call_delta(forward: float, strike: float, iv: float, t: float) -> float:
    """Black-Scholes (forward, r=0) call delta on the observed IV (brief §1.4d).

    ``delta_call = N(d1)`` with ``d1 = (ln(F/K) + 0.5 σ² T) / (σ √T)``. With
    ``r = 0`` the spot and forward deltas coincide. This is the **plain BS delta**
    used to locate the 25-delta strikes; it is *not* the FX premium-adjusted delta
    (documented caveat). Put delta is ``N(d1) - 1``.
    """
    if not (np.isfinite(forward) and np.isfinite(strike) and np.isfinite(iv)):
        return float("nan")
    if iv <= 0.0 or t <= 0.0 or strike <= 0.0 or forward <= 0.0:
        return float("nan")
    d1 = (math.log(forward / strike) + 0.5 * iv * iv * t) / (iv * math.sqrt(t))
    return float(_norm_cdf(np.array([d1]))[0])


def _strike_for_call_delta(
    ladder: pd.DataFrame, forward: float, t: float, target_delta: float
) -> float:
    """Solve for the strike whose BS call delta == ``target_delta`` on the smile.

    Delta depends on IV which depends on strike, so we scan the observed strike
    range, interpolate IV at each candidate (:func:`_interp_iv_at_strike`), compute
    the BS call delta, and pick the strike where the delta crosses ``target_delta``
    (linear interpolation between the bracketing candidates). Returns ``np.nan`` if
    the smile cannot be evaluated. (Brief §1.4d: "solve delta(K)=±0.25 on the
    fitted smile, iterating because delta depends on IV.")
    """
    if ladder.empty or not np.isfinite(forward) or t <= 0.0:
        return float("nan")
    ks = ladder["strike"].to_numpy(dtype="float64")
    lo, hi = float(ks.min()), float(ks.max())
    if not (hi > lo):
        return float("nan")
    grid = np.linspace(lo, hi, 200)
    deltas = np.array(
        [_bs_call_delta(forward, k, _interp_iv_at_strike(ladder, k), t) for k in grid]
    )
    valid = np.isfinite(deltas)
    if valid.sum() < 2:
        return float("nan")
    grid, deltas = grid[valid], deltas[valid]
    # Call delta is monotone decreasing in strike; find the crossing of target.
    diff = deltas - target_delta
    sign = np.sign(diff)
    crossings = np.where(np.diff(sign) != 0)[0]
    if len(crossings):
        i = crossings[0]
        k0, k1 = grid[i], grid[i + 1]
        d0, d1 = diff[i], diff[i + 1]
        if d1 != d0:
            return float(k0 + (k1 - k0) * (0.0 - d0) / (d1 - d0))
        return float(k0)
    # No exact crossing in range: take the strike with the closest delta.
    return float(grid[int(np.argmin(np.abs(diff)))])


def black76_greeks(
    forward: float,
    strike: float,
    iv: float,
    t: float,
    opt_type: str = "C",
    r: float = 0.0,
) -> dict:
    """Black-76 option greeks (delta / gamma / vega) for one contract. **DESCRIPTIVE.**

    Computed client-side because Deribit's public ``get_book_summary_by_currency``
    returns **no greeks**. Black (1976) on the forward ``F`` (with ``r = 0`` the spot
    and forward deltas coincide; an optional ``r`` applies the ``e^{-rT}`` discount):

        d1 = [ln(F/K) + ½σ²T] / (σ√T),   d2 = d1 − σ√T
        delta_call = e^{-rT}·Φ(d1),      delta_put = e^{-rT}·(Φ(d1) − 1)
        gamma      = e^{-rT}·φ(d1) / (F·σ·√T)                 (per $1 of F)
        vega       = F·e^{-rT}·φ(d1)·√T · 0.01                (per 1 vol-point)

    σ is a **decimal** annualized vol (the chain's ``iv`` column, i.e. ``mark_iv/100``),
    so these are **MARK greeks** (no bid/ask IV exists in the public feed). gamma and
    vega are identical for calls and puts. Returns ``nan`` greeks for non-finite or
    degenerate inputs (``iv ≤ 0``, ``T ≤ 0``) — callers must filter the ``T → 0``
    singularity (gamma blows up) and deep-OTM wings (IV noise) before display.

    Validated against Deribit's own per-contract ``get_ticker`` greeks
    (RESEARCH-options-runlog.md) — desks do not trust self-computed greeks unchecked.

    Reference: Black, F. (1976), "The pricing of commodity contracts", *J. Financial
    Economics* 3.
    """
    nan = float("nan")
    if not (np.isfinite(forward) and np.isfinite(strike) and np.isfinite(iv) and np.isfinite(t)):
        return {"delta": nan, "gamma": nan, "vega": nan}
    if iv <= 0.0 or t <= 0.0 or strike <= 0.0 or forward <= 0.0:
        return {"delta": nan, "gamma": nan, "vega": nan}
    sqrt_t = math.sqrt(t)
    d1 = (math.log(forward / strike) + 0.5 * iv * iv * t) / (iv * sqrt_t)
    disc = math.exp(-r * t)
    cdf_d1 = float(_norm_cdf(np.array([d1]))[0])
    pdf_d1 = math.exp(-0.5 * d1 * d1) / math.sqrt(2.0 * math.pi)
    is_put = str(opt_type).upper().startswith("P")
    delta = disc * (cdf_d1 - 1.0) if is_put else disc * cdf_d1
    gamma = disc * pdf_d1 / (forward * iv * sqrt_t)
    vega = forward * disc * pdf_d1 * sqrt_t * 0.01
    return {"delta": float(delta), "gamma": float(gamma), "vega": float(vega)}


def iv_skew_25d(chain: pd.DataFrame, expiry, now: pd.Timestamp | None = None) -> float:
    """25-delta risk reversal ``RR25 = IV(25d call) − IV(25d put)``. **DESCRIPTIVE.**

    Sign convention (brief §1.4d/e — pinned and documented here):

        ``RR25 = IV(25Δ call) − IV(25Δ put)``      (call-minus-put)

    so ``RR25 < 0`` ⇒ the 25Δ **put is richer** than the 25Δ call ⇒ downside
    protection is bid up (the typical BTC "put skew" / fear read). ``RR25 > 0`` ⇒
    upside calls richer (call skew, often in euphoric BTC regimes). The equivalent
    normalized put-richness skew is ``(IV_25p − IV_25c)/IV_atm = −RR25/IV_atm``
    (brief §1.4e). **Pick one convention and label every chart** — vendors disagree
    (call−put vs put−call); this is call−put.

    Delta convention (brief §1.4d caveat): the ±25Δ strikes are located by solving
    a **plain Black-Scholes** call delta on the observed IV with ``F`` as the
    forward and ``r = 0`` (a 25Δ put corresponds to a +0.75 BS call delta, since
    ``Δ_put = Δ_call − 1`` ⇒ ``Δ_put = −0.25`` ⇔ ``Δ_call = +0.75``). This is a
    **Deribit-style spot/BS 25Δ**, not the FX premium-adjusted 25Δ — one
    convention, applied to every expiry. Returns ``np.nan`` if the smile is too
    sparse to locate both wings.

    Tag: **DESCRIPTIVE (sentiment / positioning)** — brief §2.3. BTC skew changes
    sign with regime; Deribit's own 4-year backtest found a *structural* skew-carry
    premium but the skew **z-score timing rule underperformed** the naive carry, so
    this is a sentiment gauge, **not** a validated return-timing signal.

    Returns
    -------
    float
        ``RR25`` in **vol points as a decimal** (e.g. ``-0.04`` == the 25Δ put IV is
        4 vol-points above the 25Δ call IV).
    """
    slice_df = _expiry_slice(chain, expiry)
    if slice_df.empty:
        return float("nan")
    t = year_fraction_to_expiry(expiry, now=now)
    if t <= 0.0:
        return float("nan")
    fwd = _forward(slice_df)
    ladder = _otm_iv_by_strike(slice_df, fwd)
    if ladder.empty:
        return float("nan")
    # 25Δ call: BS call delta = +0.25. 25Δ put: BS call delta = +0.75 (Δp = Δc − 1).
    k_25c = _strike_for_call_delta(ladder, fwd, t, 0.25)
    k_25p = _strike_for_call_delta(ladder, fwd, t, 0.75)
    iv_25c = _interp_iv_at_strike(ladder, k_25c)
    iv_25p = _interp_iv_at_strike(ladder, k_25p)
    if not (np.isfinite(iv_25c) and np.isfinite(iv_25p)):
        return float("nan")
    return float(iv_25c - iv_25p)


def smile(
    chain: pd.DataFrame,
    expiry,
    *,
    x: str = "log_moneyness",
    drop_wing_delta: float = 0.05,
    now: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """OTM-only quality-gated IV smile for one expiry. **DESCRIPTIVE.**

    Returns the gated (x, iv) point set for plotting / fitting per the brief-§1.7
    quality gate, applied with **only what the book-summary frame provides**:

    * **drop null bid/ask** — a contract with no live bid *or* ask is non-tradable
      (its ``mark_iv`` is Deribit-interpolated, brief §1.7), so it is excluded;
    * **OTM side only** — OTM puts for ``K < F``, OTM calls for ``K > F`` (tighter,
      more liquid; ITM is parity-redundant, brief §1.4a);
    * **deep-wing cut** ``|delta| < drop_wing_delta`` (default 0.05, the same 5% cut
      DVOL uses, brief §1.7) — *if delta is available*. The book summary has no
      per-contract greeks, so we compute a **plain BS delta on the observed IV**
      (same convention as :func:`iv_skew_25d`) to apply the cut rather than skip it;
    * drops null/≤0 IV.

    Tag: **DESCRIPTIVE** — a MARK smile (mark_iv only), not a tradable bid/ask
    smile; over-aggressive wing filtering flattens measured skew/BF, so the
    ``drop_wing_delta`` threshold is exposed as a knob (brief §1.7).

    Parameters
    ----------
    chain : pd.DataFrame
        Output of :func:`btcquant.data.get_option_chain`.
    expiry : Timestamp-like
        The expiry to build the smile for.
    x : {"log_moneyness", "strike", "delta"}
        The x-coordinate to return alongside ``iv``. ``log_moneyness`` = ``ln(K/F)``
        (best for arbitrage diagnostics, brief §1.4a); ``delta`` = BS call delta.
    drop_wing_delta : float, default 0.05
        Wing cut: drop contracts with ``|BS delta| < drop_wing_delta``.
    now : Timestamp, optional
        Valuation time for ``T`` / delta (defaults to current UTC).

    Returns
    -------
    pd.DataFrame
        Columns ``[strike, x, iv]`` (plus ``opt_type``) sorted by strike. ``x`` is
        the chosen coordinate; ``iv`` is a decimal. Empty frame if no contract
        passes the gate.
    """
    slice_df = _expiry_slice(chain, expiry)
    cols = ["strike", "x", "iv", "opt_type"]
    if slice_df.empty:
        return pd.DataFrame(columns=cols)

    df = slice_df.copy()
    for c in ("strike", "iv", "bid_price", "ask_price"):
        df[c] = pd.to_numeric(df.get(c), errors="coerce")
    fwd = _forward(df)
    t = year_fraction_to_expiry(expiry, now=now)

    # Gate: valid IV, both bid AND ask present, OTM side.
    df = df.dropna(subset=["strike", "iv"])
    df = df[df["iv"] > 0.0]
    df = df.dropna(subset=["bid_price", "ask_price"])
    if np.isfinite(fwd):
        is_otm = ((df["opt_type"] == "P") & (df["strike"] <= fwd)) | (
            (df["opt_type"] == "C") & (df["strike"] >= fwd)
        )
        df = df[is_otm]
    if df.empty:
        return pd.DataFrame(columns=cols)

    # Deep-wing cut via BS delta on the observed IV (|delta| >= drop_wing_delta).
    if t > 0.0 and np.isfinite(fwd):
        def _abs_delta(row) -> float:
            cd = _bs_call_delta(fwd, float(row["strike"]), float(row["iv"]), t)
            if not np.isfinite(cd):
                return float("nan")
            d = cd if row["opt_type"] == "C" else cd - 1.0
            return abs(d)

        adelta = df.apply(_abs_delta, axis=1)
        keep = adelta.isna() | (adelta >= drop_wing_delta)
        df = df[keep]
    if df.empty:
        return pd.DataFrame(columns=cols)

    df = df.sort_values("strike").reset_index(drop=True)
    if x == "strike":
        df["x"] = df["strike"]
    elif x == "delta":
        df["x"] = df.apply(
            lambda r: _bs_call_delta(fwd, float(r["strike"]), float(r["iv"]), t),
            axis=1,
        )
    else:  # log_moneyness (default)
        df["x"] = np.log(df["strike"].to_numpy(dtype="float64") / fwd)
    return df[cols]


def max_pain(chain: pd.DataFrame, expiry) -> dict:
    """Max-pain strike + open-interest by strike for one expiry. **DESCRIPTIVE · positioning.**

    Max-pain is the candidate settlement price (taken over the listed strikes) that
    minimizes the total intrinsic value paid to option *holders* at expiry:

        pain(S) = Σ_calls OI · max(S − K, 0)  +  Σ_puts OI · max(K − S, 0)
        max_pain = argmin_S pain(S)

    using ``open_interest`` per contract. It is **where OI clusters, NOT a forecast**:
    equity expiry "pinning" has some (weak, mechanism-driven) support (Ni, Pearson &
    Poteshman 2005), but there is no credible evidence BTC price gravitates to max-pain.
    Reported as positioning context only — never a price magnet or target.

    Returns ``{max_pain, strikes, call_oi, put_oi, pc_oi_ratio, forward}`` (strikes/
    call_oi/put_oi are ascending-strike-aligned lists). Empty-safe.
    """
    out = {"max_pain": float("nan"), "strikes": [], "call_oi": [], "put_oi": [],
           "pc_oi_ratio": float("nan"), "forward": float("nan")}
    sl = _expiry_slice(chain, expiry)
    if sl.empty:
        return out
    df = sl.copy()
    df["strike"] = pd.to_numeric(df["strike"], errors="coerce")
    df["open_interest"] = pd.to_numeric(df.get("open_interest"), errors="coerce").fillna(0.0)
    df = df.dropna(subset=["strike"])
    if df.empty:
        return out
    strikes = np.sort(df["strike"].unique())
    call_oi = df[df["opt_type"] == "C"].groupby("strike")["open_interest"].sum().reindex(strikes).fillna(0.0).to_numpy()
    put_oi = df[df["opt_type"] == "P"].groupby("strike")["open_interest"].sum().reindex(strikes).fillna(0.0).to_numpy()
    pain = np.array([
        (call_oi * np.maximum(s - strikes, 0.0)).sum() + (put_oi * np.maximum(strikes - s, 0.0)).sum()
        for s in strikes
    ])
    tot_call, tot_put = float(call_oi.sum()), float(put_oi.sum())
    out.update(
        max_pain=float(strikes[int(np.argmin(pain))]),
        strikes=strikes.tolist(), call_oi=call_oi.tolist(), put_oi=put_oi.tolist(),
        forward=_forward(df),
        pc_oi_ratio=(tot_put / tot_call) if tot_call > 0 else float("nan"),
    )
    return out


def gamma_concentration(chain: pd.DataFrame, expiry, now: pd.Timestamp | None = None) -> dict:
    """Unsigned gamma concentration by strike for one expiry. **DESCRIPTIVE · structure.**

    ``GC(K) = Σ_{contracts at K} |gamma| · open_interest`` — **gamma density from open
    interest** (Black-76 gamma on ``mark_iv``). This is **NOT dealer positioning**: who
    is long vs short gamma is unknowable from any keyless public feed, so there is **no
    signed GEX and no flip / pin level** here. Read it as "where option gamma is
    densest"; never as support/resistance, a price magnet, or a gamma-flip level. See
    RESEARCH-options-runlog.md for why the signed version is rejected.

    Returns ``{strikes, gamma_oi, forward, T}`` (ascending strike). Empty-safe; needs T > 0.
    """
    out = {"strikes": [], "gamma_oi": [], "forward": float("nan"), "T": float("nan")}
    sl = _expiry_slice(chain, expiry)
    if sl.empty:
        return out
    df = sl.copy()
    for c in ("strike", "iv", "open_interest"):
        df[c] = pd.to_numeric(df.get(c), errors="coerce")
    df = df.dropna(subset=["strike", "iv"])
    df = df[df["iv"] > 0.0]
    fwd = _forward(df)
    t = year_fraction_to_expiry(expiry, now=now)
    out["forward"], out["T"] = fwd, t
    if df.empty or not np.isfinite(fwd) or t <= 0.0:
        return out
    df["open_interest"] = df["open_interest"].fillna(0.0)
    gc: dict[float, float] = {}
    for _, r in df.iterrows():
        g = black76_greeks(fwd, float(r["strike"]), float(r["iv"]), t, str(r["opt_type"]))["gamma"]
        if np.isfinite(g):
            k = float(r["strike"])
            gc[k] = gc.get(k, 0.0) + abs(g) * float(r["open_interest"])
    ks = sorted(gc)
    out["strikes"], out["gamma_oi"] = ks, [gc[k] for k in ks]
    return out
