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

Deflated-Sharpe convention (M6, 2026-07-10 — Bailey & López de Prado 2014 as written)
-------------------------------------------------------------------------------------
* **C1** — ``DSR := PSR(sr0(N, V))`` with
  ``sr0 = sqrt(V) * ((1 - γ) * Φ⁻¹(1 - 1/N) + γ * Φ⁻¹(1 - 1/(N·e)))``,
  γ = 0.5772156649015329 (Euler–Mascheroni). ``N = 1 ⟹ sr0 = 0 ⟹ DSR ≡ PSR``
  and must be **labeled** ``'PSR (single trial — no deflation)'`` wherever
  displayed; the producing stats dict carries ``dsr_is_psr: true`` in that case
  (the numeric key ``deflated_sharpe`` is unchanged for compatibility).
* **C2** — ``V`` is the **empirical variance (ddof=1) of the per-period Sharpe
  ratios across the N trials** whenever the trial SRs are in hand — honest in
  both directions (no invented ``max(V, 1/n)`` floor). The ``1/n_periods`` null
  fallback is permitted ONLY when trial SRs are genuinely unavailable (a bare
  ``--n-trials`` declaration), and every such output carries the printed caveat
  ``'null-variance fallback — deflation may be under- or over-stated'``
  (stats carry ``var_fallback: true`` when it fired with ``n_trials > 1``).
* **C3** — N by surface: the leaderboard (``scripts/compare.py``) uses
  ``N =`` strategies ranked in that run with ``V =`` empirical var of their OOS
  per-period SRs; ``backtest.walk_forward`` keeps ``N = n_splits``
  (folds-as-trials, regime-stability reading) with ``V =`` empirical var of the
  per-fold OOS per-period SRs; ``backtest.run`` / ``backtest.run_funding`` keep
  the caller's ``n_trials`` and both store ``var_trials_sr`` in stats.
* **C4** — PSR internals are unchanged (per-period SR, unbiased moments
  ``bias=False``, non-excess kurtosis, denominator
  ``sqrt(1 - skew·SR + (kurt - 1)/4 · SR²)``, ``sqrt(n - 1)`` scaling); the JS
  mirror (``dashboard/quant.js``) carries identical semantics.
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
    "evt_pot_tail",
    "kelly_fraction",
    "kelly",
    "probabilistic_sharpe_ratio",
    "sharpe_estimator_variance",
    "expected_max_sharpe_ratio",
    "deflated_sharpe_ratio",
    "min_backtest_length",
    "false_strategy_threshold",
    "effective_number_of_trials",
    "probability_false_strategy",
    "hierarchical_bayes_sharpe",
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


# Minimum number of exceedances to attempt a GPD fit. Below ~30 the PWM shape
# estimator's sampling error dominates (its sd at n_u=30 is ~0.2 for realistic
# xi — wider than the whole "thin vs fat tail" question), so reporting a fitted
# xi would be noise dressed as measurement. Honest answer: NaN + the count.
_POT_MIN_EXCEEDANCES = 30


def _gpd_pwm(excesses) -> tuple:
    """Closed-form GPD(xi, beta) fit by probability-weighted moments — Hosking &
    Wallis (1987). Deterministic and JS-mirrorable (no optimizer).

    PWM convention (documented because it is the exact contract the JS mirror
    keeps): with the excesses sorted **ascending** ``y_(0) <= ... <= y_(m-1)``,

        b0 = mean(y)                                  (= alpha_0 = E[Y])
        b1 = (1/m) * sum_i ((m-1-i)/(m-1)) * y_(i)    (= alpha_1 = E[Y*(1-F(Y))])

    i.e. ``b1`` is the *unbiased plotting-position estimator of the (1-F)-weighted
    PWM* — the largest excess gets weight 0, the smallest weight 1. (The mirrored
    F-weighted variant ``i/(m-1)`` makes ``b0 - 2*b1`` negative for every sample,
    so the closed forms below would be degenerate always; the (1-F) weighting is
    the one Hosking-Wallis eq. for the GPD actually uses.) Then::

        xi   = 2 - b0 / (b0 - 2*b1)
        beta = 2 * b0 * b1 / (b0 - 2*b1)

    (Hosking & Wallis 1987 state these with k = -xi.) Verified numerically: on
    GPD(0.3, 0.02) samples this recovers both parameters and agrees with the
    scipy ``genpareto`` MLE (see tests). Guard: ``b0 - 2*b1 <= 0`` (degenerate,
    cannot happen for a genuine GPD sample with xi < 1 in expectation) -> NaNs.

    Reference: Hosking & Wallis (1987), "Parameter and Quantile Estimation for
    the Generalized Pareto Distribution", *Technometrics* 29(3):339-349.
    """
    y = np.sort(np.asarray(excesses, dtype="float64"))
    m = int(y.size)
    if m < 2:
        return float("nan"), float("nan")
    b0 = float(y.mean())
    w = (m - 1.0 - np.arange(m)) / (m - 1.0)     # (1-F) plotting positions, ascending y
    b1 = float(np.sum(w * y) / m)
    d = b0 - 2.0 * b1
    if not math.isfinite(d) or d <= 0:
        return float("nan"), float("nan")
    return float(2.0 - b0 / d), float(2.0 * b0 * b1 / d)


def _gpd_pot_var(xi: float, beta: float, u: float, fu: float, alpha: float) -> float:
    """POT tail quantile (in LOSS units) — McNeil, Frey & Embrechts (2005) eq. 7.18::

        VaR_alpha = u + (beta/xi) * ( ((1-alpha)/Fu)^(-xi) - 1 )     (xi != 0)

    with the xi -> 0 (exponential-tail) limit ``u + beta * ln(Fu/(1-alpha))``
    taken analytically for ``|xi| < 1e-9`` (the two branches agree to ~1e-10
    at the switch point — continuity is asserted in tests)."""
    if abs(xi) < 1e-9:
        return u + beta * math.log(fu / (1.0 - alpha))
    return u + (beta / xi) * (((1.0 - alpha) / fu) ** (-xi) - 1.0)


def evt_pot_tail(returns: pd.Series, threshold_q: float = 0.95, alpha: float = 0.99) -> dict:
    """EVT Peaks-Over-Threshold tail VaR/ES — GPD fit to threshold exceedances.

    RISK MEASUREMENT, not an edge claim: the empirical ``var``/``cvar`` read the
    handful of worst observed bars; this fits a Generalized Pareto Distribution to
    *all* losses beyond a high threshold and extrapolates the tail the sample has
    barely seen. The Pickands-Balkema-de Haan theorem is the licence: for a wide
    class of distributions the conditional excess distribution over a high
    threshold converges to a GPD, so the fit is theory-grounded rather than a
    curve choice. On fat-tailed BTC returns the EVT 99% VaR/ES typically sit
    beyond the empirical quantile — that gap is the honest headline.

    Method (deterministic, closed-form, mirrored in ``dashboard/quant.js``):

    1. Work on LOSSES ``x = -returns`` (non-finite dropped). Threshold ``u`` =
       the ``threshold_q`` empirical quantile of the losses (linear-interpolation
       quantile, numpy default — the JS mirror implements the same rule).
    2. Exceedances ``y_i = x_i - u`` for ``x_i > u`` (strict); ``n_u`` of them.
       Fewer than 30 -> all-NaN result with ``n_exceed`` reported (see
       ``_POT_MIN_EXCEEDANCES`` for the floor rationale).
    3. Fit GPD(xi, beta) to ``y`` by probability-weighted moments
       (:func:`_gpd_pwm`, Hosking-Wallis 1987 — the exact weight convention is
       documented there and kept identical in JS).
    4. Tail quantities with ``Fu = n_u / n`` (McNeil-Frey-Embrechts 2005, §7.2)::

           VaR_a = u + (beta/xi) * ( ((1-a)/Fu)^(-xi) - 1 )    (xi->0 limit branch)
           ES_a  = (VaR_a + beta - xi*u) / (1 - xi)            (xi < 1; else NaN,
                                                                mean is infinite)

    Sign convention: ``var`` / ``cvar`` are returned as **signed returns**
    (negative = loss), the same orientation as :func:`var` / :func:`cvar`, so
    ``evt_pot_tail(r, alpha=0.99)['var']`` is directly comparable to
    ``var(r, alpha=0.01)``. Internally ``u``/``beta`` are in positive LOSS units
    (``u = 0.03`` means the threshold is a -3% bar) and are reported as such.

    Parameters
    ----------
    returns : pd.Series
        Per-period simple returns (per-period, NOT annualized).
    threshold_q : float, default 0.95
        Loss quantile used as the POT threshold ``u`` (0.95 = fit the worst 5%).
    alpha : float, default 0.99
        Tail confidence for VaR/ES. Must satisfy ``1 - alpha <= Fu`` (the GPD
        only models the region beyond ``u``; asking for a quantile *inside* the
        empirical body would silently extrapolate backwards -> NaN + note).

    Returns
    -------
    dict
        ``{xi, beta, u, n_exceed, n, var, cvar, threshold_q, alpha,
        method: 'pot-gpd-pwm', note}`` — NaNs (with ``note``) on any degenerate
        branch; ``cvar`` NaN with finite ``var`` when ``xi >= 1`` (ES infinite).

    References
    ----------
    McNeil, Frey & Embrechts (2005), *Quantitative Risk Management*, ch. 7.
    Balkema & de Haan (1974); Pickands (1975) — the GPD limit theorem.
    Hosking & Wallis (1987), *Technometrics* 29(3):339-349 — PWM estimation.
    """
    r = pd.Series(returns, dtype="float64").to_numpy()
    x = -r[np.isfinite(r)]                       # LOSSES: positive = a losing period
    n = int(x.size)
    out = {
        "xi": float("nan"), "beta": float("nan"), "u": float("nan"),
        "n_exceed": 0, "n": n, "var": float("nan"), "cvar": float("nan"),
        "threshold_q": float(threshold_q), "alpha": float(alpha),
        "method": "pot-gpd-pwm", "note": "",
    }
    if n == 0:
        out["note"] = "empty input"
        return out
    u = float(np.quantile(x, threshold_q))
    y = x[x > u] - u
    n_u = int(y.size)
    out["n_exceed"] = n_u
    if n_u < _POT_MIN_EXCEEDANCES:
        out["note"] = (f"only {n_u} exceedances above the threshold_q={threshold_q:g} "
                       f"loss quantile (< {_POT_MIN_EXCEEDANCES}) — too few to fit a tail")
        return out
    out["u"] = u
    fu = n_u / n
    if (1.0 - alpha) > fu:
        # The GPD models the tail BEYOND u only; alpha inside the empirical body
        # would extrapolate the fit backwards into data it never saw.
        out["note"] = (f"alpha={alpha:g} lies inside the empirical body "
                       f"(1-alpha > Fu={fu:g}) — POT is only valid beyond u")
        return out
    xi, beta = _gpd_pwm(y)
    if not (math.isfinite(xi) and math.isfinite(beta) and beta > 0):
        out["note"] = "degenerate PWM fit (b0 - 2*b1 <= 0)"
        return out
    out["xi"], out["beta"] = xi, beta
    var_loss = _gpd_pot_var(xi, beta, u, fu, alpha)
    out["var"] = -var_loss                       # signed-return orientation (see docstring)
    if xi >= 1.0:
        # GPD mean is infinite for xi >= 1 -> ES diverges. NaN, never a fake number.
        out["note"] = "xi >= 1: GPD mean infinite — ES undefined (VaR still reported)"
        return out
    out["cvar"] = -((var_loss + beta - xi * u) / (1.0 - xi))
    return out


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


def sharpe_estimator_variance(sr: float, n: int, skew: float, kurt: float) -> float:
    """Finite-sample variance of the Sharpe-ratio *estimator* — Lo (2002) / Mertens (2002).

    The asymptotic variance of the sample Sharpe ratio ``SR_hat`` under non-normal
    returns, with a skew/kurtosis correction::

        Var(SR_hat) = (1 - skew*SR + ((kurt - 1) / 4) * SR^2) / (n - 1)

    where ``kurt`` is the **non-excess** (raw/Pearson) kurtosis (3 for a normal) and
    all inputs are on the same **per-period** frequency as ``n``. This is *exactly the
    quantity inside the PSR denominator*: :func:`probabilistic_sharpe_ratio` scales
    ``(SR - sr0)`` by ``sqrt(n - 1) / sqrt(1 - skew*SR + (kurt-1)/4*SR^2)``, i.e. it
    divides by ``sqrt(Var(SR_hat))`` for this same variance — so
    ``sharpe_estimator_variance(sr, n, skew, kurt)`` equals the PSR-denominator-implied
    per-Sharpe variance.

    Used as the **per-strategy own-Sharpe trial variance ``V``** fed to
    :func:`deflated_sharpe_ratio` under the leaderboard's B2 convention (each
    strategy's DSR judged against its own finite-sample sampling error, decoupled from
    peers — the convention azul selected 2026-07-13, RESEARCH-dsr-convention.md).

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

    Returns
    -------
    float
        The finite-sample Sharpe-estimator variance; ``np.nan`` for ``n < 2``.

    References
    ----------
    Lo (2002), "The Statistics of Sharpe Ratios", *Financial Analysts Journal* 58(4).
    Mertens (2002), "Comments on Variance of the IID estimator in Lo (2002)".
    """
    if n is None or n < 2:
        return float("nan")
    return float((1.0 - skew * sr + ((kurt - 1.0) / 4.0) * sr * sr) / (n - 1))


# Euler-Mascheroni constant — the Gumbel expected-max coefficient (Bailey-LdP 2014).
_EULER_GAMMA = 0.5772156649015329


def expected_max_sharpe_ratio(n_trials: int, var_trials_sr: float = 1.0) -> float:
    """Expected maximum (per-period) Sharpe of ``N`` skill-less trials — Bailey & López
    de Prado (2014), the False Strategy Theorem core.

    The expected maximum of ``N`` independent standard-normal draws is, to a Gumbel
    approximation::

        E[max_N] = (1 - gamma) * Phi^{-1}(1 - 1/N) + gamma * Phi^{-1}(1 - 1/(N*e))

    with ``gamma`` the Euler-Mascheroni constant and ``e`` Euler's number. Scaled by the
    cross-trial Sharpe dispersion ``sqrt(var_trials_sr)`` this is the **skill-less
    benchmark ``sr0``** the Deflated Sharpe (and Minimum Backtest Length) deflate
    against: pick the best of ``N`` random configurations and you should *expect* this
    Sharpe from noise alone.

    ``N < 2`` returns ``0.0`` (a single trial has no selection to inflate → ``sr0 = 0``).

    This is the single source of truth for the expected-max benchmark: both
    :func:`deflated_sharpe_ratio` (which scales by ``sqrt(var_trials_sr)``) and
    :func:`min_backtest_length` (which uses the unit-variance ``E[max_N]``) call it,
    replacing the two inline duplications that previously lived in each — the numeric
    results are byte-identical (``var_trials_sr=1.0`` gives ``sqrt(1)=1`` exactly).

    Parameters
    ----------
    n_trials : int
        Number ``N`` of independent trials searched.
    var_trials_sr : float, default 1.0
        Variance of the (per-period) Sharpe ratios across the ``N`` trials. The
        default 1.0 recovers the raw expected-max ``E[max_N]`` (standard normals).

    Returns
    -------
    float
        The expected maximum (per-period) Sharpe ``sr0``; ``0.0`` for ``N < 2``.

    Reference
    ---------
    Bailey & López de Prado (2014), "The Deflated Sharpe Ratio", *Journal of Portfolio
    Management* 40(5):94-107; SSRN 2460551.
    """
    if n_trials is None or n_trials < 2:
        return 0.0
    z1 = stats.norm.ppf(1.0 - 1.0 / n_trials)
    z2 = stats.norm.ppf(1.0 - 1.0 / (n_trials * math.e))
    expected_max_z = (1.0 - _EULER_GAMMA) * z1 + _EULER_GAMMA * z2
    return float(math.sqrt(var_trials_sr) * expected_max_z)


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

    # sr0 = expected max Sharpe of N skill-less trials (the single shared source of
    # truth — was inline-duplicated here and in min_backtest_length). N=1 ⟹ sr0=0.
    sr0 = expected_max_sharpe_ratio(n_trials, var_trials_sr)
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
    # E[max_N] of N unit-variance (standard-normal) trials — the shared expected-max
    # benchmark (var_trials_sr=1.0 ⟹ sqrt(1)=1, so this is byte-identical to the old
    # inline closed form that lived here before the refactor).
    expected_max = expected_max_sharpe_ratio(n_trials, 1.0)
    if expected_max <= 0 or np.isnan(expected_max):
        return float("nan")
    return float(2.0 * math.log(n_trials) / expected_max)


def false_strategy_threshold(
    n_trials: int,
    var_trials_sr: float,
    n_periods: int,
    skew: float,
    kurt: float,
    prob: float = 0.95,
) -> float:
    """The (per-period) Sharpe you must **exceed** to reject the false-strategy null at
    confidence ``prob``, given ``N`` trials — the False Strategy Theorem hurdle
    (Bailey & López de Prado 2014).

    Inverts the Probabilistic Sharpe Ratio against the expected-max benchmark:
    :func:`deflated_sharpe_ratio` is ``PSR(sr; sr0 = expected_max)``; the threshold
    ``sr*`` is the observed Sharpe at which that DSR equals ``prob``. Setting
    ``PSR(sr*) = prob`` and solving for ``sr*``::

        sr* = sr0 + Phi^{-1}(prob) * sqrt(1 - skew*sr* + (kurt-1)/4 * sr*^2)
                    / sqrt(n_periods - 1)

    The PSR denominator depends on ``sr*`` itself (the Lo/Mertens finite-sample Sharpe
    variance), so this is a fixed point. We iterate from ``sr0`` (whose own moments give
    a fine 1-step approximation, but the fixed point is exact); ~30 iterations converge
    to machine precision for realistic inputs.

    Interpretation: "the Sharpe you must exceed to reject the false-strategy null at
    ``prob``, given ``N`` trials". Below ``sr*`` the best-of-``N`` is statistically
    indistinguishable from the luckiest of ``N`` skill-less strategies.

    Parameters
    ----------
    n_trials : int
        Number ``N`` of trials searched (``N >= 2``; else ``nan``).
    var_trials_sr : float
        Variance of the per-period Sharpe ratios across the ``N`` trials (sets the
        expected-max benchmark ``sr0``).
    n_periods : int
        Number of return observations (``>= 2``; else ``nan``).
    skew, kurt : float
        Sample skewness and **non-excess** kurtosis of the selected strategy's returns
        (pass 3.0 for Gaussian) — they shape the finite-sample Sharpe variance.
    prob : float, default 0.95
        Confidence at which to reject the null.

    Returns
    -------
    float
        The minimum per-period Sharpe ``sr*`` (annualize by ``* sqrt(periods_per_year)``
        for reporting). ``nan`` on degenerate input.

    Reference
    ---------
    Bailey & López de Prado (2014), "The Deflated Sharpe Ratio", SSRN 2460551.
    """
    if (n_trials is None or n_trials < 2 or n_periods is None or n_periods < 2
            or var_trials_sr is None or var_trials_sr < 0 or np.isnan(var_trials_sr)):
        return float("nan")
    if not (0.0 < prob < 1.0):
        return float("nan")
    sr0 = expected_max_sharpe_ratio(n_trials, var_trials_sr)
    z = stats.norm.ppf(prob)
    scale = math.sqrt(n_periods - 1)
    sr = sr0  # seed the fixed point at the benchmark
    for _ in range(30):
        denom = 1.0 - skew * sr + ((kurt - 1.0) / 4.0) * sr * sr
        if denom <= 0 or np.isnan(denom):
            return float("nan")
        sr = sr0 + z * math.sqrt(denom) / scale
    return float(sr)


def effective_number_of_trials(returns_matrix) -> float:
    """Effective number of *independent* trials ``N_eff`` from a returns matrix — the
    eigenvalue **participation ratio** of the trials' correlation matrix (Bailey & López
    de Prado; Harvey, Liu & Zhu 2016, multiple testing under correlation).

    Naively counting ``N`` strategies over-states the search when the strategies are
    correlated (e.g. a momentum config and its vol-targeted twin are ~1.0 correlated —
    they are not two independent bets). The participation ratio of the eigenvalues
    ``lambda`` of the correlation matrix ``C = corr(returns_matrix)``::

        N_eff = (sum lambda)^2 / sum(lambda^2)

    collapses correlated columns: ``N`` identical trials ⟹ ``N_eff ≈ 1`` (one non-zero
    eigenvalue), ``N`` independent trials ⟹ ``N_eff ≈ N`` (a flat spectrum).

    Parameters
    ----------
    returns_matrix : array-like, shape (T, N)
        Per-period returns, rows = aligned time, columns = the ``N`` trials/strategies.

    Returns
    -------
    float
        ``N_eff`` clamped to ``[1, n_usable_cols]``. All-NaN / constant (zero-variance)
        columns are dropped first (their correlation is undefined); ``< 2`` usable
        columns returns the usable-column count (nothing to deflate).

    Reference
    ---------
    Bailey & López de Prado (2014), SSRN 2460551; Harvey, Liu & Zhu (2016), "…and the
    Cross-Section of Expected Returns", *Review of Financial Studies* 29(1):5-68.
    """
    M = np.asarray(returns_matrix, dtype=float)
    if M.ndim != 2 or M.shape[1] < 1:
        return float("nan")
    ncols = M.shape[1]
    # Drop all-NaN or constant (zero-variance) columns: their correlation is undefined
    # (0/0) and a constant trial carries no information to (de)count.
    keep = []
    for c in range(ncols):
        col = M[:, c]
        finite = col[np.isfinite(col)]
        if finite.size >= 2 and np.nanstd(finite) > 0:
            keep.append(c)
    if len(keep) < 2:
        return float(max(1, len(keep)))
    sub = M[:, keep]
    # Pairwise-complete not needed: rows with any NaN would poison corrcoef, so drop
    # them (align to the common finite window, as PBO/leaderboard already do upstream).
    sub = sub[np.isfinite(sub).all(axis=1)]
    if sub.shape[0] < 2:
        return float(len(keep))
    corr = np.corrcoef(sub, rowvar=False)
    lam = np.linalg.eigvalsh(corr)
    denom = float(np.sum(lam * lam))
    if denom <= 0 or np.isnan(denom):
        return float(len(keep))
    n_eff = float(np.sum(lam)) ** 2 / denom
    return float(min(max(n_eff, 1.0), len(keep)))


def probability_false_strategy(
    max_sharpe_per_period: float,
    n_trials: int,
    var_trials_sr: float,
    n_periods: int,
    skew: float,
    kurt: float,
) -> float:
    """Family-wise probability that the **best-of-``N``** strategy is a false positive —
    the complement of the Deflated Sharpe (Bailey & López de Prado 2014)::

        P(false) = 1 - PSR(max_sharpe ; sr0 = expected_max(N, var_trials_sr))

    i.e. the probability that the selected strategy's Sharpe does **not** exceed what the
    luckiest of ``N`` skill-less trials would produce. Near 0 ⟹ the winner is unlikely to
    be noise; near 1 ⟹ the best of ``N`` is indistinguishable from selection luck.

    Parameters
    ----------
    max_sharpe_per_period : float
        The selected (best) strategy's per-period Sharpe.
    n_trials, var_trials_sr : int, float
        Trials searched and their cross-trial Sharpe variance (set ``sr0``).
    n_periods, skew, kurt : int, float, float
        Sample length and (non-excess) moments feeding the PSR.

    Returns
    -------
    float
        ``P(false) in [0, 1]``; ``nan`` on degenerate input.

    Reference
    ---------
    Bailey & López de Prado (2014), SSRN 2460551.
    """
    dsr = deflated_sharpe_ratio(
        max_sharpe_per_period, n_periods, skew, kurt, n_trials, var_trials_sr
    )
    if dsr is None or np.isnan(dsr):
        return float("nan")
    return float(1.0 - dsr)


def hierarchical_bayes_sharpe(sharpes, variances, effective_n=None) -> dict:
    """Empirical-Bayes hierarchical shrinkage of a family of Sharpe ratios — the
    Bayesian sibling of the Deflated Sharpe / False-Strategy Theorem view of the SAME
    winner's-curse / multiple-testing problem.

    COMPLEMENTARY DIAGNOSTIC, NOT a replacement for the production Deflated Sharpe (which
    stays the headline, unchanged). Where the DSR/FST deflate the *best-of-N* Sharpe
    frequentistly (benchmark = expected max of N skill-less trials), this places a normal
    prior over the family of true per-strategy Sharpes and returns each strategy's
    *posterior mean* — pulling extreme in-sample Sharpes toward the pooled population
    mean by exactly the amount the data's own dispersion warrants. The two views should
    broadly AGREE: a Sharpe that the DSR flags as likely-noise is one this model shrinks
    hard toward the pool.

    The normal-normal hierarchical model (Efron-Morris / James-Stein; Gelman *BDA*):

        SR_i | theta_i ~ Normal(theta_i, sigma_i^2)      (likelihood; sigma_i^2 known)
        theta_i        ~ Normal(mu, tau^2)               (prior over true Sharpes)

    is solved in **empirical Bayes** closed form — the hyperparameters ``(mu, tau^2)`` are
    plugged at their point estimates rather than integrated, giving a fast, MCMC-free,
    JS-mirrorable approximation to the full hierarchical posterior. The remaining step (a
    fully Bayesian, correlation-aware model that also integrates hyperparameter
    uncertainty) requires MCMC and is out of scope here; the optional ``effective_n``
    argument is a first-order, correlation-aware correction to ``tau^2`` in that spirit.

    UNIFICATION. This closes two loops the repo already opened:

    * the **A-vs-B2 variance choice** (RESEARCH-dsr-convention.md): the within-strategy
      ``sigma_i^2`` here is exactly the B2 own-Sharpe likelihood precision
      (:func:`sharpe_estimator_variance`), while the between-strategy ``tau^2`` is a
      *principled, estimated* cross-strategy dispersion ``V`` (rather than a picked one);
    * the **N_eff correlation finding** (:func:`effective_number_of_trials`): pass it as
      ``effective_n`` and the DerSimonian-Laird ``tau^2`` deflates ``Q`` by
      ``(N_eff - 1)`` instead of ``(k - 1)`` — correlated strategies count as fewer
      independent observations, WIDENING the estimated population spread.

    Steps (all in **per-period** Sharpe units — the caller annualizes for display only):

        w_i      = 1 / sigma_i^2                              (fixed-effect weights)
        muHat    = sum(w_i SR_i) / sum(w_i)                   (fixed-effect mean)
        Q        = sum(w_i (SR_i - muHat)^2)                  (weighted heterogeneity)
        c        = sum(w_i) - sum(w_i^2) / sum(w_i)
        tauHat2  = max(0, (Q - (k - 1)) / c)                  (DerSimonian-Laird 1986;
                   with effective_n:  (Q - (N_eff - 1)) / c)  correlation-aware variant)
        wStar_i  = 1 / (sigma_i^2 + tauHat2)                  (random-effects weights)
        muStar   = sum(wStar_i SR_i) / sum(wStar_i)           (pooled population mean)
        B_i      = sigma_i^2 / (sigma_i^2 + tauHat2)          (shrinkage factor in [0,1])
        thetaHat = B_i muStar + (1 - B_i) SR_i                (posterior mean = shrunk SR)
        v_i      = sigma_i^2 tauHat2 / (sigma_i^2 + tauHat2)  (posterior variance)
        CI 95%   = thetaHat +/- 1.96 sqrt(v_i)
        p_skill  = Phi(thetaHat / sqrt(v_i))                  (posterior P(true SR > 0))

    Edge cases:

    * ``k < 2`` — nothing to pool: return the raw Sharpes (``B_i = 0``, ``thetaHat = SR_i``,
      ``tau = 0``, posterior sd ``= sigma_i`` so the CI/``p_skill`` reflect the raw
      likelihood alone).
    * ``tauHat2 == 0`` (no detectable heterogeneity; also the identical-``SR`` case) —
      full pooling: every ``thetaHat == muStar``, ``B_i == 1``, ``v_i == 0``; ``p_skill``
      collapses to the sign of ``muStar`` (1 if > 0, 0 if < 0, 0.5 if exactly 0) and the
      CI to the point ``muStar``.

    Parameters
    ----------
    sharpes : array-like of float
        The ``k`` per-period Sharpe ratios ``SR_i``.
    variances : array-like of float
        Their sampling variances ``sigma_i^2`` — pass the B2 own-Sharpe variances from
        :func:`sharpe_estimator_variance` (``(1 - skew*SR + (kurt-1)/4*SR^2) / (n-1)``),
        the same likelihood precision the leaderboard DSR uses. Same length as ``sharpes``.
    effective_n : float, optional
        If given, the DerSimonian-Laird ``tau^2`` deflates ``Q`` by ``(effective_n - 1)``
        instead of ``(k - 1)`` — the correlation-aware variant. Because ``N_eff <= k``,
        this yields a ``tau^2`` at least as large (a wider population spread ⟹ less
        shrinkage). Default ``None`` uses the standard ``k - 1``.

    Returns
    -------
    dict
        All per-period (caller annualizes by ``* sqrt(ppy)`` for display):
        ``mu`` (pooled population mean muStar), ``tau`` (sqrt tauHat2), ``shrunk``
        (list of thetaHat_i), ``shrink_factor`` (list of B_i in [0,1]), ``post_sd``
        (list of sqrt v_i), ``ci_low`` / ``ci_high`` (list, 95% credible), ``p_skill``
        (list of Phi(thetaHat_i / sqrt v_i)).

    References
    ----------
    Efron & Morris (1975), "Data Analysis Using Stein's Estimator and its
    Generalizations", *JASA* 70(350):311-319.
    James & Stein (1961), "Estimation with Quadratic Loss", *Proc. 4th Berkeley Symp.*
    DerSimonian & Laird (1986), "Meta-analysis in clinical trials", *Controlled Clinical
    Trials* 7(3):177-188 (the ``tau^2`` moment estimator).
    Gelman et al., *Bayesian Data Analysis* 3rd ed., ch. 5 (hierarchical normal model).
    """
    sr = [float(x) for x in sharpes]
    sig2 = [float(x) for x in variances]
    k = len(sr)
    if len(sig2) != k:
        raise ValueError("sharpes and variances must have the same length")

    # k < 2 (or a non-finite / non-positive variance anywhere) ⟹ no pooling: raw SRs
    # with their own likelihood as the posterior (B_i = 0, tau = 0).
    finite_ok = all(math.isfinite(s) and math.isfinite(v) and v > 0
                    for s, v in zip(sr, sig2))
    if k < 2 or not finite_ok:
        mu = sr[0] if k == 1 else float("nan")
        post_sd = [math.sqrt(v) if math.isfinite(v) and v > 0 else float("nan") for v in sig2]
        return {
            "mu": float(mu),
            "tau": 0.0,
            "shrunk": [float(s) for s in sr],
            "shrink_factor": [0.0] * k,
            "post_sd": post_sd,
            "ci_low": [s - 1.96 * sd for s, sd in zip(sr, post_sd)],
            "ci_high": [s + 1.96 * sd for s, sd in zip(sr, post_sd)],
            "p_skill": [float(stats.norm.cdf(s / sd)) if math.isfinite(sd) and sd > 0
                        else float("nan") for s, sd in zip(sr, post_sd)],
        }

    w = [1.0 / v for v in sig2]
    sw = sum(w)
    mu_hat = sum(wi * si for wi, si in zip(w, sr)) / sw
    Q = sum(wi * (si - mu_hat) ** 2 for wi, si in zip(w, sr))
    c = sw - sum(wi * wi for wi in w) / sw
    # DerSimonian-Laird moment estimator; the correlation-aware variant deflates Q by
    # (N_eff - 1) instead of (k - 1). N_eff <= k ⟹ a LARGER tau^2 (wider spread).
    df = (float(effective_n) - 1.0) if effective_n is not None else (k - 1.0)
    tau2 = max(0.0, (Q - df) / c) if c > 0 else 0.0
    tau = math.sqrt(tau2)

    w_star = [1.0 / (v + tau2) for v in sig2]
    sws = sum(w_star)
    mu_star = sum(ws * si for ws, si in zip(w_star, sr)) / sws

    shrunk, shrink_factor, post_sd, ci_low, ci_high, p_skill = [], [], [], [], [], []
    for si, v in zip(sr, sig2):
        b = v / (v + tau2)                      # shrinkage factor in [0, 1]
        theta = b * mu_star + (1.0 - b) * si    # posterior mean (== shrunk SR)
        v_post = v * tau2 / (v + tau2)          # posterior variance (0 when tau2 == 0)
        sd = math.sqrt(v_post)
        shrink_factor.append(float(b))
        shrunk.append(float(theta))
        post_sd.append(float(sd))
        ci_low.append(float(theta - 1.96 * sd))
        ci_high.append(float(theta + 1.96 * sd))
        if sd > 0:
            p_skill.append(float(stats.norm.cdf(theta / sd)))
        else:
            # Full pooling (tau2 == 0): the posterior collapses to mu_star; P(SR>0) is
            # the sign of the pooled mean (a tiny-epsilon limit), 0.5 at exactly 0.
            p_skill.append(1.0 if theta > 0 else (0.0 if theta < 0 else 0.5))

    return {
        "mu": float(mu_star),
        "tau": float(tau),
        "shrunk": shrunk,
        "shrink_factor": shrink_factor,
        "post_sd": post_sd,
        "ci_low": ci_low,
        "ci_high": ci_high,
        "p_skill": p_skill,
    }


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
