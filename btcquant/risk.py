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
    "min_backtest_length",
    "probability_of_backtest_overfitting",
    "trade_ledger",
    "expectancy_report",
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


def min_backtest_length(n_trials: int) -> float:
    """Minimum Backtest Length (years) — Bailey, Borwein, López de Prado & Zhu (2014).

    Below this many years of data, selecting the best of ``N`` skill-less trials
    yields an in-sample Sharpe whose *out-of-sample* expectation is ~0 — i.e. the
    backtest is too short for the number of configurations tried::

        MinBTL (yrs) ≈ 2 · ln(N) / E[max_N]

    where ``E[max_N]`` is the expected maximum Sharpe of ``N`` standard-normal
    (zero-skill) trials, using the same Bailey-LdP closed form as the Deflated
    Sharpe. This is the brief's stated form (RESEARCH.md §3) — an order-of-magnitude
    guide, not a hard threshold. Correlated parameter sweeps inflate the *effective*
    ``N``, so treat the strategy count as a lower bound on trials.

    Returns the minimum length in **years** (annualized-Sharpe convention); ``nan``
    for ``N < 2``.

    Reference: Bailey et al. (2014), "Pseudo-Mathematics and Financial Charlatanism",
    *Notices of the AMS* 61(5):458-471; SSRN 2308659.
    """
    if n_trials is None or n_trials < 2:
        return float("nan")
    gamma = 0.5772156649015329
    z1 = stats.norm.ppf(1.0 - 1.0 / n_trials)
    z2 = stats.norm.ppf(1.0 - 1.0 / (n_trials * math.e))
    expected_max = (1.0 - gamma) * z1 + gamma * z2
    if expected_max <= 0 or np.isnan(expected_max):
        return float("nan")
    return float(2.0 * math.log(n_trials) / expected_max)


def probability_of_backtest_overfitting(returns_matrix, n_blocks: int = 8) -> dict:
    """Probability of Backtest Overfitting (PBO) via CSCV — Bailey-Borwein-LdP-Zhu (2017).

    Given a ``T × N`` matrix of per-bar returns (rows = aligned time, columns =
    the ``N`` strategies/trials the leaderboard chose among), split the rows into
    ``S`` contiguous blocks and, over **every** way to use half the blocks as
    in-sample (``C(S, S/2)`` combinations), pick the IS-best strategy and check
    where it ranks out-of-sample. ``PBO`` is the fraction of splits where the
    IS-best strategy lands **below the OOS median** — i.e. how often "keep the
    backtest winner" would have picked an OOS underperformer.

    ``PBO`` near 0 ⇒ the leaderboard's selection is robust; ``PBO > ~0.5`` ⇒ the
    ranking is essentially noise (you are overfitting by picking the best of N).

    Parameters
    ----------
    returns_matrix : array-like, shape (T, N)
        Per-bar returns, columns = strategies. Use the OOS (walk-forward) returns
        so PBO measures cross-strategy *selection* overfit on held-out data.
    n_blocks : int, default 8
        Number ``S`` of contiguous CSCV blocks (forced even). ``C(S, S/2)`` splits.

    Returns
    -------
    dict
        ``{pbo, n_combos, n_strategies, n_blocks}``; ``pbo`` is ``nan`` if there
        are fewer than 2 strategies or too few rows to block.

    Reference: Bailey, Borwein, López de Prado & Zhu (2017), "The Probability of
    Backtest Overfitting", *J. Computational Finance* 20(4); SSRN 2326253.
    """
    import itertools

    nan_out = {"pbo": float("nan"), "n_combos": 0, "n_strategies": 0, "n_blocks": 0}
    M = np.asarray(returns_matrix, dtype=float)
    if M.ndim != 2 or M.shape[1] < 2:
        return nan_out
    T, N = M.shape
    S = n_blocks if n_blocks % 2 == 0 else n_blocks - 1
    S = max(2, min(S, T))
    edges = np.linspace(0, T, S + 1, dtype=int)
    blocks = [np.arange(edges[i], edges[i + 1]) for i in range(S)]
    blocks = [b for b in blocks if b.size > 0]
    S = len(blocks)
    if S < 2:
        return nan_out
    half = S // 2

    def _block_sharpe(idx: np.ndarray, col: int) -> float:
        r = M[idx, col]
        r = r[np.isfinite(r)]
        if r.size < 2:
            return 0.0
        sd = r.std(ddof=1)
        return float(r.mean() / sd) if sd > 0 else 0.0

    below = 0
    total = 0
    for is_combo in itertools.combinations(range(S), half):
        is_set = set(is_combo)
        is_idx = np.concatenate([blocks[i] for i in is_combo])
        oos_idx = np.concatenate([blocks[i] for i in range(S) if i not in is_set])
        is_sr = [_block_sharpe(is_idx, c) for c in range(N)]
        oos_sr = [_block_sharpe(oos_idx, c) for c in range(N)]
        best = int(np.argmax(is_sr))
        oos_best = oos_sr[best]
        # Relative OOS rank of the IS-best (fraction of strategies it beats OOS).
        rank = float(np.mean([1.0 if oos_best > v else 0.0 for v in oos_sr]))
        if rank < 0.5:                       # below the OOS median ⇒ an overfit pick
            below += 1
        total += 1
    return {"pbo": below / total if total else float("nan"),
            "n_combos": total, "n_strategies": N, "n_blocks": S}


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


def trade_ledger(
    positions: pd.Series,
    prices: pd.Series,
    vol: pd.Series,
    periods_per_year: int = 365,
    k: float = 2.0,
) -> list:
    """Segment a continuous target-weight series into discrete trades + R-multiples.

    Van Tharp's R-multiple = trade P&L / initial risk R. These strategies carry **no
    hard stop**, so R is a **vol-notional** initial risk: ``R = |entry_weight| * k *
    sigma_bar`` at the entry bar, where ``sigma_bar = vol / sqrt(periods_per_year)``
    (per-bar close-to-close vol from ``features.realized_vol``) and ``k`` is the notional
    stop in sigmas (default 2). This is faithful to Tharp's intent (reward measured in
    units of volatility-scaled initial risk) but is **not a stop-based R** — disclose it.

    A trade is a maximal run of bars with constant non-zero **sign** of the *traded*
    (shifted-by-one, no-look-ahead) position; flat bars separate trades and a sign flip
    ends one trade and opens the next. Trade return is the compounded per-bar return over
    the held bars. For continuous (varying-weight) strategies R uses the *entry* weight,
    so the R-multiple is approximate; for long/flat strategies it is exact. Always-in
    strategies (buy & hold) yield a single degenerate trade — flag low N.

    Returns a list of ``{entry, exit, n_bars, trade_return, R, r_multiple}`` (R/r_multiple
    are ``nan`` when the entry-bar risk is unavailable).
    """
    pos, px = positions.align(prices, join="inner")
    vol = vol.reindex(px.index)
    traded = pos.shift(1).fillna(0.0).to_numpy(dtype="float64")
    aret = px.pct_change().fillna(0.0).to_numpy(dtype="float64")
    ret = traded * aret
    sigma_bar = vol.to_numpy(dtype="float64") / np.sqrt(periods_per_year)
    risk_frac = k * sigma_bar

    def sgn(x: float) -> int:
        return int(x > 0) - int(x < 0)

    runs, cur, start = [], 0, None
    for i, w in enumerate(traded):
        s = sgn(w)
        if s != cur:
            if cur != 0 and start is not None:
                runs.append((start, i - 1))
            cur, start = s, (i if s != 0 else None)
    if cur != 0 and start is not None:
        runs.append((start, len(traded) - 1))

    out = []
    for a, b in runs:
        cum = np.cumprod(1.0 + ret[a : b + 1]) - 1.0     # running trade return path
        tr = float(cum[-1])
        mae = float(cum.min())                            # max adverse excursion (≤ 0)
        ew = abs(float(traded[a]))
        R = ew * float(risk_frac[a]) if np.isfinite(risk_frac[a]) else float("nan")
        ok = np.isfinite(R) and R > 0
        rm = tr / R if ok else float("nan")
        mae_r = mae / R if ok else float("nan")           # MAE in R units (Sweeney/Tharp)
        out.append({"entry": a, "exit": b, "n_bars": b - a + 1,
                    "trade_return": tr, "R": R, "r_multiple": rm, "mae_r": mae_r})
    return out


def expectancy_report(
    positions: pd.Series,
    prices: pd.Series,
    vol: pd.Series,
    periods_per_year: int = 365,
    k: float = 2.0,
) -> dict:
    """Tharp expectancy / R-multiple summary over the trade ledger. **Evaluation layer,
    NOT a signal.** Expectancy = mean R-multiple per trade; a system can win often yet have
    negative expectancy if losers are large. Use **out-of-sample only** (in-sample
    expectancy is curve-fit) and treat low ``n_trades`` as unreliable.

    Returns ``{n_trades, expectancy_r, win_rate, avg_win_r, avg_loss_r, payoff_ratio,
    max_loss_streak, sqn, profit_factor, avg_mae_r}`` where **SQN** = System Quality Number
    ``mean(R)/std(R)·√n`` (Tharp; a sample-quality score, NOT significance — PBO/MinBTL remain
    the gate), **profit_factor** = Σ winning-R / |Σ losing-R|, and **avg_mae_r** = mean max-adverse
    excursion in R (how far trades typically went against entry).
    """
    led = trade_ledger(positions, prices, vol, periods_per_year, k)
    rms = [t["r_multiple"] for t in led if t["r_multiple"] == t["r_multiple"]]  # drop nan
    n = len(rms)
    out = {"n_trades": n, "expectancy_r": float("nan"), "win_rate": float("nan"),
           "avg_win_r": float("nan"), "avg_loss_r": float("nan"),
           "payoff_ratio": float("nan"), "max_loss_streak": 0,
           "sqn": float("nan"), "profit_factor": float("nan"), "avg_mae_r": float("nan")}
    if n == 0:
        return out
    arr = np.array(rms, dtype="float64")
    wins, losses = arr[arr > 0], arr[arr < 0]
    out["expectancy_r"] = float(arr.mean())
    out["win_rate"] = float(len(wins) / n)
    out["avg_win_r"] = float(wins.mean()) if len(wins) else 0.0
    out["avg_loss_r"] = float(losses.mean()) if len(losses) else 0.0
    out["payoff_ratio"] = float(out["avg_win_r"] / abs(out["avg_loss_r"])) if out["avg_loss_r"] < 0 else float("nan")
    streak = mx = 0
    for r in arr:
        streak = streak + 1 if r < 0 else 0
        mx = max(mx, streak)
    out["max_loss_streak"] = int(mx)
    sd = float(arr.std(ddof=1)) if n > 1 else float("nan")
    out["sqn"] = float(arr.mean() / sd * math.sqrt(n)) if (sd == sd and sd > 0) else float("nan")
    gross_loss = float(-losses.sum())
    out["profit_factor"] = float(wins.sum() / gross_loss) if gross_loss > 0 else float("nan")
    maes = [t["mae_r"] for t in led if t["mae_r"] == t["mae_r"]]
    out["avg_mae_r"] = float(np.mean(maes)) if maes else float("nan")
    return out
