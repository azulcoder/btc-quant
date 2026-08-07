"""hasbrouck.py — microstructure estimators on a transaction-price series.

Implements the estimators extracted in `docs/EXTRACT-hasbrouck-001.md` and
`docs/EXTRACT-hasbrouck-s9-s12.md`, in the corrected form established by
`docs/VERIFY-hasbrouck-extraction.md`. Every deviation from the extraction documents
below is one that verification forced, and each is named where it happens.

Model (generalized Roll)
------------------------
    m_t  = m_{t-1} + w_t          efficient price, a random walk
    w_t  = lambda*q_t + u_t       trades carry information
    p_t  = m_t + c*q_t            observed transaction price
    dp_t = -c*q_{t-1} + (c+lambda)*q_t + u_t

    gamma_0 = c^2 + s^2 + su^2,  gamma_1 = -c*s,  gamma_k = 0 (k >= 2),  s := c + lambda
    sigma2_w = lambda^2 + su^2 = gamma_0 + 2*gamma_1

What is identified and what is not
----------------------------------
`sigma2_w` is identified. `{c, lambda, sigma2_u}` individually are NOT — they lie on a
one-parameter family that produces identical moments. Anything here that returns a
*point* estimate of `c` or `lambda` is therefore returning a point chosen by an
assumption, and says so in its own output.

Binding rules carried from the extraction documents
---------------------------------------------------
* **RULE-EXTRACT-2** — `gamma_1 > 0` returns ABSTAIN with a reason, never 0 and never
  `sqrt(|gamma_1|)`. Same semantics as the PBO verdict `INDETERMINATE`.
* **RULE-EXTRACT-5** — nothing here may be pointed at real market data before a
  pre-registration declares the whole specification set with an `N_trials` cap.
  Simulation with controls is 0 looks; a real partition is not. This module has no
  data access of its own by design: it takes an array.
* **RULE-EXTRACT-6** — the AR path is `sigma2_eps / phi(1)^2` with `phi(1) = 1 - sum(phi_i)`.
  A negative variance FAILS LOUDLY; it is never clipped or absolute-valued.

Three corrections verification forced, which the extraction documents do not carry
-----------------------------------------------------------------------------------
1. **E2 is gated, not diagnosed.** `gamma_0 + 2*gamma_1` is only `sigma2_w` when the true
   MA order is 1. When it is not, closed form gives errors of 0.74x, 289x, and one sign
   pattern (`theta = [-.6, +.3, -.15]`) where it returns a NEGATIVE variance. So
   `sigma2_w_ma1` refuses unless the order test passes, rather than reporting and warning.
2. **E4 returns the INTERCEPT only, never the slope.** For an MA(q) with k > q,
   `Var(dp_k)/k = sigma2_w - (2/k) * sum_h h*gamma_h`. The intercept is order-independent;
   the slope is not, and there are fixtures where the exact slope is 0 while `-2*gamma_1`
   is large. The extraction document's "free estimator for c" does not exist.
3. **The sign of an estimate is not a safety mechanism.** `sum(phi_i)` has sampling error
   of order `sqrt(K/N)`, so a wrong AR formula that "would come out negative" leaks: at
   `theta = -0.01, K = 60, N = 2e5` it stays positive on ~27% of seeds. The guard here is
   an explicit check, not a hoped-for sign.

Everything is a pure function of a numpy array. No I/O, no data access, no caching.
"""

from __future__ import annotations

from typing import Optional, Sequence

import numpy as np

__all__ = [
    "autocovariances",
    "ma1_order_gate",
    "roll",
    "sigma2_w_ma1",
    "sigma2_w_wold",
    "sigma2_w_variance_ratio",
    "sigma2_w_ar",
    "pricing_error_lower_bound",
    "identified_interval_c",
    "cumulative_impulse_response",
    "subsample_estimate",
    "ABSTAIN",
]

ABSTAIN = "ABSTAIN"


def _as_array(x) -> np.ndarray:
    """Coerce to a 1-D float array, REFUSING any non-finite element.

    The first version of this function dropped non-finite values silently. That is a
    splice: removing `dp_t` makes `dp_{t-1}` and `dp_{t+1}` adjacent, and every estimator
    here is built on `gamma_1`, which is precisely a statement about adjacency. The effect
    is to attenuate `gamma_1` toward zero — that is, to under-report the spread — with no
    flag anywhere in the output. It is the module-level version of backfilling a hole, and
    this repo's standing rail is that gaps stay gaps.

    Refusing is the honest option because this module has no timestamps. It cannot tell an
    interior hole from a trailing one, so it cannot decide what "contiguous" means for the
    caller. The caller must split its own series into contiguous segments and combine the
    per-segment autocovariances, which it can do because it does have the timestamps.
    """
    a = np.asarray(x, dtype=float).ravel()
    bad = ~np.isfinite(a)
    if bad.any():
        first = int(np.argmax(bad))
        raise ValueError(
            f"{int(bad.sum())} of {a.size} observations are not finite (first at index "
            f"{first}). Dropping them would splice the series and attenuate gamma_1 — the "
            "one quantity every estimator here depends on — without any flag. Pass "
            "contiguous segments instead and combine their autocovariances.")
    return a


def autocovariances(dp, max_lag: int = 10) -> np.ndarray:
    """Sample autocovariances gamma_0..gamma_max_lag of a price-change series.

    Uses the 1/n (biased, positive-semidefinite) convention, which is what the
    Yule-Walker path in `sigma2_w_ar` requires to stay solvable.
    """
    x = _as_array(dp)
    n = x.size
    if n <= max_lag + 1:
        raise ValueError(f"need more than {max_lag + 1} finite observations, got {n}")
    x = x - x.mean()
    return np.array([float((x[k:] * x[: n - k]).sum() / n) for k in range(max_lag + 1)])


def ma1_order_gate(dp, max_lag: int = 10, alpha: float = 0.05) -> dict:
    """Is the MA(1) assumption that E2 rests on actually true of this series?

    This is a GATE, not a diagnostic — see correction 1 in the module docstring.

    **It is a JOINT test, and that is not a detail.** The first version of this function
    compared each of lags 2..max_lag to its own two-sided 5% critical value and failed the
    gate if any exceeded. On a genuinely MA(1) series that rejects about
    `1 - 0.95^(max_lag-1)` ~ 37% of the time — a verifier that cries wolf, which is class I
    in this repo's taxonomy and does more damage than no verifier at all. It was caught by
    running the gate on a series known to PASS, which is the only reason it is not still here.

    The statistic is a Ljung-Box form over lags 2..max_lag, scaled by Bartlett's MA(1)
    variance `(1 + 2*rho_1^2)/n` so the null is "MA(1)" rather than "white noise"::

        Q = n * (n+2) * sum_{k=2..m} rho_k^2 / ((n-k) * (1 + 2*rho_1^2))   ~   chi2_{m-1}

    Per-lag z-scores are still returned, for reading — but they do not decide.
    """
    x = _as_array(dp)
    g = autocovariances(x, max_lag)
    n = x.size
    if g[0] <= 0:
        return {"passed": False, "reason": "gamma_0 is not positive — degenerate series",
                "n": n, "rho": [], "q_stat": float("nan"), "q_crit": float("nan"),
                "df": 0, "worst_lag": None, "worst_z": float("nan")}
    rho = g / g[0]
    bartlett = 1.0 + 2.0 * rho[1] ** 2
    ks = np.arange(2, max_lag + 1)
    q_stat = float(n * (n + 2) * np.sum(rho[2:] ** 2 / (n - ks)) / bartlett)
    m = int(ks.size)
    # The reference distribution is NOT chi2(m), and getting that wrong was this gate's
    # second calibration bug. Bartlett's formula for an MA(1) gives not only the variance
    # 1 + 2*rho_1^2 but also NON-ZERO covariances between neighbouring autocorrelations:
    # cov(rho_k, rho_k+1) = 2*rho_1/n and cov(rho_k, rho_k+2) = rho_1^2/n. So Q is a
    # quadratic form in CORRELATED normals. Its mean is still tr(R) = m, but its variance
    # is 2*tr(R^2), which at the rho_1 this microstructure model produces (~ -0.41) is
    # 30.4 rather than chi2(9)'s 18. Comparing to chi2(9) therefore rejects ~9% of
    # genuinely MA(1) series at a nominal 5%. Measured, and monotone in |rho_1|.
    # The fix is a Satterthwaite match: approximate Q by c * chi2(v) with the same mean
    # and variance. It degenerates EXACTLY to chi2(m) when rho_1 = 0, so nothing is lost
    # on a white-noise series, and it costs no power against MA(3).
    a_off = 2.0 * float(rho[1]) / bartlett          # cov(z_k, z_k+1) after scaling
    b_off = float(rho[1]) ** 2 / bartlett           # cov(z_k, z_k+2) after scaling
    tr_R = float(m)
    tr_R2 = float(m + 2 * (m - 1) * a_off ** 2 + 2 * (m - 2) * b_off ** 2)
    c_scale = tr_R2 / tr_R
    df = tr_R * tr_R / tr_R2
    q_crit = c_scale * _chi2_ppf(1.0 - alpha, df)
    se = float(np.sqrt(bartlett / n))
    z = np.abs(rho[2:]) / se
    worst = int(np.argmax(z)) + 2 if z.size else None
    passed = bool(q_stat <= q_crit)
    return {
        "passed": passed,
        "reason": ("" if passed else
                   f"Q = {q_stat:.2f} over lags 2..{max_lag} exceeds the "
                   f"{1 - alpha:.0%} point {q_crit:.2f} of the Satterthwaite reference "
                   f"{c_scale:.3f}*chi2({df:.2f}) (worst single lag {worst}, {z.max():.2f} SE) "
                   "— the series is not MA(1), so gamma_0 + 2*gamma_1 is not sigma2_w"),
        "n": n,
        "rho": [float(v) for v in rho[: max_lag + 1]],
        "q_stat": q_stat,
        "q_crit": float(q_crit),
        "df": float(df),
        "scale": float(c_scale),
        "tr_R": tr_R,
        "tr_R2": tr_R2,
        "reference": (f"Satterthwaite {c_scale:.4f}*chi2({df:.3f}); a plain chi2({m}) would "
                      f"put the critical value at {_chi2_ppf(1.0 - alpha, m):.2f} and "
                      "over-reject, because the per-lag z-scores are correlated under MA(1)"),
        "se": se,
        "worst_lag": worst,
        "worst_z": float(z.max()) if z.size else float("nan"),
    }


def _chi2_ppf(p: float, df: float) -> float:
    """Chi-square quantile via Wilson-Hilferty; ample for a 5% gate, no scipy needed.

    Accepts a NON-INTEGER `df`, which the Satterthwaite match in `ma1_order_gate` needs.
    Accuracy is ~0.1% for df >= 5 and ~2.5% at df = 1, well inside the sampling noise of
    Q itself. Checked against published table values in the test suite.
    """
    if df <= 0:
        return float("nan")
    z = float(np.sqrt(2.0) * _erfinv(2.0 * p - 1.0))
    t = 2.0 / (9.0 * df)
    return float(df * (1.0 - t + z * np.sqrt(t)) ** 3)


def _erfinv(y: float) -> float:
    """Inverse error function, Newton-refined from a rational seed (no scipy needed)."""
    if not -1.0 < y < 1.0:
        raise ValueError("erfinv domain is (-1, 1)")
    # Winitzki's approximation as the seed, then two Newton steps on erf(x) - y.
    a = 0.147
    ln1 = np.log(1.0 - y * y)
    t1 = 2.0 / (np.pi * a) + ln1 / 2.0
    x = float(np.sign(y) * np.sqrt(max(np.sqrt(t1 * t1 - ln1 / a) - t1, 0.0)))
    for _ in range(3):
        err = _erf(x) - y
        x -= err / (2.0 / np.sqrt(np.pi) * np.exp(-x * x))
    return x


def _erf(x: float) -> float:
    """Abramowitz-Stegun 7.1.26 — |error| < 1.5e-7, ample for a 5% critical value."""
    s = 1.0 if x >= 0 else -1.0
    x = abs(x)
    t = 1.0 / (1.0 + 0.3275911 * x)
    y = 1.0 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t
                - 0.284496736) * t + 0.254829592) * t * np.exp(-x * x)
    return s * y


# --------------------------------------------------------------------------- #
# E1 — Roll                                                                    #
# --------------------------------------------------------------------------- #
def roll(dp, max_lag: int = 10, alpha: float = 0.05, force: bool = False) -> dict:
    """E1 — the Roll (1984) spread estimator, with its refusal honoured.

    **Gated on the MA(1) order test.** `gamma_1 >= 0` is not the only way this estimator can be
    meaningless: on a series that is not MA(1) at all, `gamma_1` is not `-c*s` and
    `sqrt(-gamma_1)` is a number about something else. Measured on real BTCUSDT perp trades,
    where `rho_2` runs +0.31 to +0.52 and the ACF oscillates, it returned 8x to 30x the
    book-measured half-spread (`docs/DIAG-provenance-001.md`). Before the gate was added it
    also returned verdict `OK` on a simulated MA(3) while reporting a NEGATIVE `sigma2_u`
    inside the same dict.

    `c_hat = sqrt(-gamma_1)`; the spread is `2 * c_hat`.

    **What this number actually is.** Under the generalized model `gamma_1 = -c*s` with
    `s = c + lambda`, so `sqrt(-gamma_1) = sqrt(c*s)` — the GEOMETRIC MEAN of the fixed
    cost and the full half-spread. By AM-GM it therefore satisfies `c <= c_hat <= s`
    whenever `lambda >= 0`, with equality only at `lambda = 0`. Read as a half-spread it
    is an upper bound; read as a spread it is a lower bound. It is a point estimate only
    under the assumption `lambda = 0`, and that assumption is not testable from
    `(gamma_0, gamma_1)` alone. `identified_interval_c` reports the honest object.

    `gamma_1 > 0` returns ABSTAIN (RULE-EXTRACT-2). On a spreadless series that happens
    roughly half the time by construction, and reporting 0 or `sqrt(|gamma_1|)` there
    would be a silent lie.
    """
    gate = ma1_order_gate(dp, max_lag=max_lag, alpha=alpha)
    if not gate["passed"] and not force:
        return {"verdict": ABSTAIN, "reason": gate["reason"], "gate": gate,
                "c_hat": float("nan"), "spread": float("nan"), "sigma2_u": float("nan"),
                "gamma_0": float("nan"), "gamma_1": float("nan")}
    g = autocovariances(dp, 2)
    g0, g1 = float(g[0]), float(g[1])
    if g1 >= 0:
        return {
            "verdict": ABSTAIN,
            "reason": (f"gamma_1 = {g1:+.6e} is not negative, so sqrt(-gamma_1) has no real "
                       "solution. Positive first-order autocovariance is a momentum "
                       "signature, not a spread; returning 0 or |gamma_1| would be a "
                       "silent lie (RULE-EXTRACT-2)."),
            "gamma_0": g0, "gamma_1": g1,
            "c_hat": float("nan"), "spread": float("nan"), "sigma2_u": float("nan"),
        }
    c_hat = float(np.sqrt(-g1))
    return {
        "verdict": "OK",
        "reason": "",
        "gamma_0": g0, "gamma_1": g1,
        "c_hat": c_hat,
        "spread": 2.0 * c_hat,
        "sigma2_u": g0 + 2.0 * g1,
        "interpretation": ("sqrt(c*s), the geometric mean of c and c+lambda: an UPPER bound "
                           "on the half-spread and a LOWER bound on the spread, for lambda >= 0"),
    }


# --------------------------------------------------------------------------- #
# E2 / E3 / E4 / E9 — four routes to sigma2_w                                  #
# --------------------------------------------------------------------------- #
def sigma2_w_ma1(dp, max_lag: int = 10, alpha: float = 0.05, force: bool = False) -> dict:
    """E2 — `sigma2_w = gamma_0 + 2*gamma_1`, valid ONLY if the series is MA(1).

    Refuses when `ma1_order_gate` fails. That refusal is the whole point: on a true MA(3)
    this expression is out by anything from 0.74x to 289x, and on one sign pattern it
    returns a negative variance. `force=True` bypasses the gate and is provided so tests
    can measure the damage, never for production use.
    """
    gate = ma1_order_gate(dp, max_lag=max_lag, alpha=alpha)
    g = autocovariances(dp, 2)
    val = float(g[0] + 2.0 * g[1])
    if not gate["passed"] and not force:
        return {"verdict": ABSTAIN, "reason": gate["reason"], "sigma2_w": float("nan"),
                "gate": gate}
    if val < 0:
        return {"verdict": "FAIL",
                "reason": (f"gamma_0 + 2*gamma_1 = {val:.6e} is negative. A variance cannot be "
                           "negative; this means the MA(1) premise is false. Not clipped."),
                "sigma2_w": val, "gate": gate}
    return {"verdict": "OK", "reason": "" if gate["passed"] else "GATE BYPASSED (force=True)",
            "sigma2_w": val, "gate": gate}


def sigma2_w_wold(dp, max_lag: int = 10, alpha: float = 0.05, force: bool = False) -> dict:
    """E3 — `sigma2_w = theta(1)^2 * sigma2_eps`, via the MA(1) Wold representation.

    `(gamma_0, gamma_1)` admits TWO solutions whose thetas satisfy `theta_a * theta_b = 1`;
    only the invertible one (`|theta| < 1`) is admissible, because the other makes the
    `eps_t` recursion diverge. The invertible root is the LARGER sigma2_eps, since the two
    roots multiply to `gamma_1^2` and the invertible one must exceed `|gamma_1|`.

    **E3 and E2 return the SAME NUMBER, exactly.** Writing out the fit:
    `(1+theta)^2 * sigma2_eps = sigma2_eps + 2*gamma_1 + gamma_1^2/sigma2_eps`, and the two
    roots sum to `gamma_0` while multiplying to `gamma_1^2`, so `sigma2_eps + gamma_1^2/sigma2_eps`
    collapses to `gamma_0` and the whole expression is `gamma_0 + 2*gamma_1`. Verified to a
    relative difference of 0.0. Two consequences, both of which cost something to learn:

    * E3 is **gated on the same terms as E2** — added after review found it returning 291x
      the truth on a series where the gated E2 correctly refused. An ungated alias for a
      gated estimator is a hole in the gate, not a second opinion.
    * "E2, E3, E4 and E9 agree" is **three** independent routes, not four. E3 earns its
      place by reporting `theta`, `sigma2_eps` and the invertibility check, which E2 cannot
      — but its `sigma2_w` is not extra evidence, and an assertion that E2 matches E3 is a
      tautology that can never fail.
    """
    gate = ma1_order_gate(dp, max_lag=max_lag, alpha=alpha)
    if not gate["passed"] and not force:
        return {"verdict": ABSTAIN, "reason": gate["reason"], "sigma2_w": float("nan"),
                "theta": float("nan"), "sigma2_eps": float("nan"), "gate": gate}
    g = autocovariances(dp, 2)
    g0, g1 = float(g[0]), float(g[1])
    disc = g0 * g0 - 4.0 * g1 * g1
    if disc < 0:
        return {"verdict": ABSTAIN, "sigma2_w": float("nan"), "theta": float("nan"),
                "sigma2_eps": float("nan"),
                "reason": (f"|gamma_1| = {abs(g1):.6e} exceeds gamma_0/2 = {g0 / 2:.6e}, so no "
                           "real MA(1) representation exists. The series is not MA(1).")}
    d = float(np.sqrt(disc))
    s2_inv = 0.5 * (g0 + d)          # invertible root
    s2_non = 0.5 * (g0 - d)
    if s2_inv <= 0:
        return {"verdict": ABSTAIN, "sigma2_w": float("nan"), "theta": float("nan"),
                "sigma2_eps": float("nan"), "reason": "degenerate: sigma2_eps <= 0"}
    theta = g1 / s2_inv
    theta_other = (g1 / s2_non) if s2_non > 0 else float("inf")
    return {
        "verdict": "OK", "reason": "",
        "theta": float(theta),
        "theta_noninvertible": float(theta_other),
        "root_product": float(theta * theta_other) if np.isfinite(theta_other) else float("nan"),
        "sigma2_eps": float(s2_inv),
        "sigma2_w": float((1.0 + theta) ** 2 * s2_inv),
    }


def sigma2_w_variance_ratio(dp, k_grid: Optional[Sequence[int]] = None,
                            k_min: int = 8) -> dict:
    """E4 — the long-horizon variance ratio, read as an INTERCEPT.

    For an MA(q) price change and `k > q`::

        Var(p_t - p_{t-k}) / k = sigma2_w - (2/k) * sum_{h=1..q} h * gamma_h

    exactly linear in `1/k`. The **intercept is `sigma2_w` for any MA order** — that, and
    not "it converges", is why E4 is the robust route. The **slope is not** `-2*gamma_1`
    unless `q = 1`, so this function does not report a slope-derived quantity at all
    (correction 2 in the module docstring). `slope` is returned for diagnosis only and is
    explicitly labelled as such.

    `k_min` exists because a grid that reaches down to `k <= q` corrupts the intercept by
    tens of percent. With autocorrelated order flow the price change is not a finite-order
    MA at all, so the honest rule is "k_min large enough", not "k_min > q_hat" — the
    residual nonlinearity reported here is what tells you whether it was large enough.
    """
    x = _as_array(dp)
    p = np.cumsum(x)
    n = p.size
    if k_grid is None:
        k_grid = [k for k in (8, 12, 16, 24, 32, 48, 64, 96, 128, 192, 256) if k >= k_min]
    ks = [int(k) for k in k_grid if k >= k_min and k < n // 8]
    if len(ks) < 3:
        return {"verdict": ABSTAIN, "sigma2_w": float("nan"),
                "reason": (f"only {len(ks)} usable horizons at k >= {k_min} with n = {n}; "
                           "need at least 3 to fit an intercept")}
    inv_k, vals = [], []
    for k in ks:
        d = p[k:] - p[:-k]
        vals.append(float(d.var()) / k)
        inv_k.append(1.0 / k)
    inv_k = np.array(inv_k)
    vals = np.array(vals)
    A = np.column_stack([np.ones_like(inv_k), inv_k])
    coef, *_ = np.linalg.lstsq(A, vals, rcond=None)
    fitted = A @ coef
    resid = vals - fitted
    rel_nonlin = float(np.abs(resid).max() / abs(coef[0])) if coef[0] != 0 else float("inf")
    out = {
        "verdict": "OK" if coef[0] > 0 else "FAIL",
        "sigma2_w": float(coef[0]),
        "k_grid": ks,
        "ratios": [float(v) for v in vals],
        "residual_nonlinearity": rel_nonlin,
        "slope_DIAGNOSTIC_ONLY": float(coef[1]),
        "reason": "",
    }
    if coef[0] <= 0:
        out["reason"] = (f"intercept {coef[0]:.6e} is not positive — a variance cannot be "
                         "negative. Not clipped.")
    elif rel_nonlin > 0.02:
        out["verdict"] = "OK_WITH_WARNING"
        out["reason"] = (f"residual nonlinearity {rel_nonlin:.1%} of the intercept: the "
                         f"1/k relation does not hold on this grid, so k_min = {k_min} is "
                         "too small for this series (autocorrelated order flow does this).")
    return out


def sigma2_w_ar(dp, K: int = 30) -> dict:
    """E9 — `sigma2_w = sigma2_eps / phi(1)^2` with `phi(1) = 1 - sum(phi_i)`.

    The AR(K) truncation is fitted by Yule-Walker, which is cheap and stable at the sample
    sizes this repo has. It does not need to know the true MA order, which is its advantage
    over a direct MA fit.

    **RULE-EXTRACT-6 is enforced here, and the enforcement is a check rather than a hoped-for
    sign.** Three wrong readings of this formula are common — dropping the square, reading
    `phi(1)` as `sum(phi_i)`, or both — and they are out by factors from 0.56x to 289x. One
    of them returns a negative variance for `theta < 0`, which is where people assume it
    will announce itself. It does not always: the sign of `sum(phi_i)` is a random variable
    with error of order `sqrt(K/n)`, so at small `|theta|` it flips on a large minority of
    samples. `phi_one` is therefore checked explicitly and a non-positive value FAILS.
    """
    x = _as_array(dp)
    n = x.size
    if n <= 4 * K:
        return {"verdict": ABSTAIN, "sigma2_w": float("nan"),
                "reason": f"n = {n} is too small for AR({K}); want n > 4K"}
    g = autocovariances(x, K)
    G = np.empty((K, K))
    for i in range(K):
        for j in range(K):
            G[i, j] = g[abs(i - j)]
    try:
        phi = np.linalg.solve(G, g[1:])
    except np.linalg.LinAlgError as e:
        return {"verdict": ABSTAIN, "sigma2_w": float("nan"),
                "reason": f"Yule-Walker system is singular at K={K}: {e}"}
    sigma2_eps = float(g[0] - phi @ g[1:])
    phi_one = float(1.0 - phi.sum())
    if sigma2_eps <= 0:
        return {"verdict": "FAIL", "sigma2_w": float("nan"), "phi_one": phi_one,
                "reason": f"innovation variance {sigma2_eps:.6e} is not positive"}
    if phi_one <= 0:
        return {"verdict": "FAIL", "sigma2_w": float("nan"), "phi_one": phi_one,
                "reason": (f"phi(1) = 1 - sum(phi) = {phi_one:.6e} is not positive, so "
                           "sigma2_eps/phi(1)^2 is not a variance of anything. This is the "
                           "check RULE-EXTRACT-6 requires; it is NOT clipped or made absolute.")}
    return {
        "verdict": "OK", "reason": "",
        "K": K,
        "sum_phi": float(phi.sum()),
        "phi_one": phi_one,
        "sigma2_eps": sigma2_eps,
        "sigma2_w": float(sigma2_eps / phi_one ** 2),
        "phi": [float(v) for v in phi],
    }


# --------------------------------------------------------------------------- #
# E5 + the identified interval                                                 #
# --------------------------------------------------------------------------- #
def pricing_error_lower_bound(dp, max_lag: int = 10, alpha: float = 0.05,
                              force: bool = False) -> dict:
    """E5 — a LOWER BOUND on `sigma2_s = Var(p_t - m_t)`, never a point estimate.

        sigma2_s >= theta^2 * sigma2_eps = 0.5 * (gamma_0 - sqrt(gamma_0^2 - 4*gamma_1^2))

    Exact under exclusively private information (`sigma2_u = 0`), and it strictly
    understates under exclusively public information (`lambda = 0`). Reporting it as
    `sigma2_s` without the qualifier is an overstatement in a known direction.

    **Gated on the MA(1) order test**: the bound is derived FROM the MA(1) Wold representation,
    so on a series that has none there is nothing for it to bound.
    """
    gate = ma1_order_gate(dp, max_lag=max_lag, alpha=alpha)
    if not gate["passed"] and not force:
        return {"verdict": ABSTAIN, "reason": gate["reason"], "gate": gate,
                "lower_bound": float("nan")}
    g = autocovariances(dp, 2)
    g0, g1 = float(g[0]), float(g[1])
    disc = g0 * g0 - 4.0 * g1 * g1
    if disc < 0:
        return {"verdict": ABSTAIN, "lower_bound": float("nan"),
                "reason": "no real MA(1) representation — |gamma_1| > gamma_0/2"}
    lb = 0.5 * (g0 - float(np.sqrt(disc)))
    return {"verdict": "OK", "lower_bound": float(lb),
            "label": "LOWER BOUND on sigma2_s — exact only when sigma2_u = 0",
            "reason": ""}


def identified_interval_c(dp, max_lag: int = 10, alpha: float = 0.05,
                          force: bool = False) -> dict:
    """The honest object for `c`: an interval, not a point. [DIUKUR, this repo's proposition]

    `sigma2_u >= 0` confines `c^2` to `[0.5*(gamma_0 - D), 0.5*(gamma_0 + D)]` with
    `D = sqrt(gamma_0^2 - 4*gamma_1^2)`. Adding the economic restriction `lambda >= 0`
    (adverse selection cannot be negative) cuts the upper end to `c^2 <= -gamma_1`. So::

        c^2  in  [ 0.5*(gamma_0 - D) ,  -gamma_1 ]
                 \\___ the E5 bound __/   \\_ Roll _/

    The E5 bound is the INFIMUM of the identified set and the Roll estimate is its
    SUPREMUM. They are two ends of one interval rather than two estimators with two
    separate caveats. The set is never empty: the condition reduces to
    `4*gamma_1*(gamma_0 + 2*gamma_1) <= 0`, true because `gamma_1 < 0` and
    `gamma_0 + 2*gamma_1 = sigma2_w >= 0`.

    This is a proposition owned by this repo, derived and verified in
    `docs/VERIFY-hasbrouck-extraction.md` §4 — it is NOT a Hasbrouck citation.

    **Gated on the MA(1) order test.** Both endpoints are functions of `(gamma_0, gamma_1)`
    under the generalized Roll model, which is MA(1) by construction. Running
    PREREG-microstructure-001 is what forced this: on pooled real data with `rho_1 = -0.71`
    the discriminant happened to go negative and produced INDETERMINATE by luck. On slightly
    different moments this function would have returned a confident interval.
    """
    gate = ma1_order_gate(dp, max_lag=max_lag, alpha=alpha)
    if not gate["passed"] and not force:
        return {"verdict": ABSTAIN, "reason": gate["reason"], "gate": gate,
                "c_lo": float("nan"), "c_hi": float("nan")}
    g = autocovariances(dp, 2)
    g0, g1 = float(g[0]), float(g[1])
    if g1 >= 0:
        return {"verdict": ABSTAIN, "reason": "gamma_1 >= 0 — no spread to bound",
                "c_lo": float("nan"), "c_hi": float("nan")}
    disc = g0 * g0 - 4.0 * g1 * g1
    if disc < 0:
        return {"verdict": ABSTAIN, "reason": "no real MA(1) representation",
                "c_lo": float("nan"), "c_hi": float("nan")}
    c2_lo = 0.5 * (g0 - float(np.sqrt(disc)))
    c2_hi = -g1
    if c2_lo > c2_hi + 1e-18 * max(abs(c2_hi), 1.0):
        return {"verdict": "FAIL", "reason": "identified set is empty — arithmetic impossible "
                                             "under the model; check the input",
                "c_lo": float("nan"), "c_hi": float("nan")}
    return {
        "verdict": "OK", "reason": "",
        "c2_lo": float(c2_lo), "c2_hi": float(c2_hi),
        "c_lo": float(np.sqrt(max(c2_lo, 0.0))),
        "c_hi": float(np.sqrt(max(c2_hi, 0.0))),
        "spread_lo": float(2.0 * np.sqrt(max(c2_lo, 0.0))),
        "spread_hi_is_unbounded": True,
        "label": ("c is identified only up to this interval; its lower end is the E5 bound "
                  "and its upper end is the Roll estimate"),
    }


# --------------------------------------------------------------------------- #
# E10 — impulse response                                                       #
# --------------------------------------------------------------------------- #
def cumulative_impulse_response(phi: Sequence[float], horizon: int = 20) -> dict:
    """E10 — MA coefficients from AR coefficients, and their cumulative sum.

    `theta_0 = 1`, `theta_k = sum_{j=1..min(k,K)} phi_j * theta_{k-j}`.

    For a DIFFERENCED series the meaningful object is the CUMULATIVE response: the path of
    the price LEVEL after a shock. RULE-EXTRACT-3 forbids reading a single VAR coefficient
    as lambda; the cumulative response is the object that rule points at.
    """
    p = np.asarray(phi, dtype=float).ravel()
    K = p.size
    th = np.zeros(horizon + 1)
    th[0] = 1.0
    for k in range(1, horizon + 1):
        th[k] = sum(p[j - 1] * th[k - j] for j in range(1, min(k, K) + 1))
    return {"theta": [float(v) for v in th],
            "cumulative": [float(v) for v in np.cumsum(th)],
            "long_run": float(np.cumsum(th)[-1]),
            "label": "cumulative response of the price LEVEL to a one-unit innovation"}


# --------------------------------------------------------------------------- #
# E11 — subsampling standard error                                             #
# --------------------------------------------------------------------------- #
def subsample_estimate(dp, estimator, n_blocks: int = 20) -> dict:
    """E11 — Fama-MacBeth style subsampling: an estimate per block, then mean and SE.

    Preferred over the delta method here because the mapping from AR coefficients to
    `sigma2_w` is strongly nonlinear, which is exactly where a Jacobian-based SE is least
    trustworthy — the source makes that judgement itself.

    `estimator` takes an array and returns a dict with a `sigma2_w` key. Blocks that
    ABSTAIN or FAIL are counted and excluded rather than silently dropped: a mean over an
    unknown denominator is not a mean.
    """
    x = _as_array(dp)
    blocks = np.array_split(x, n_blocks)
    vals, skipped = [], []
    for i, b in enumerate(blocks):
        try:
            r = estimator(b)
        except Exception as e:  # noqa: BLE001 — one bad block must not kill the estimate
            skipped.append((i, f"{type(e).__name__}: {e}"))
            continue
        v = r.get("sigma2_w", float("nan"))
        if r.get("verdict") in ("OK", "OK_WITH_WARNING") and np.isfinite(v):
            vals.append(float(v))
        else:
            skipped.append((i, r.get("reason", r.get("verdict", "?"))[:80]))
    if len(vals) < 2:
        return {"verdict": ABSTAIN, "mean": float("nan"), "se": float("nan"),
                "n_used": len(vals), "n_skipped": len(skipped), "skipped": skipped,
                "reason": f"only {len(vals)} of {n_blocks} blocks produced an estimate"}
    a = np.array(vals)
    return {
        "verdict": "OK", "reason": "",
        "mean": float(a.mean()),
        "se": float(a.std(ddof=1) / np.sqrt(a.size)),
        "n_used": int(a.size),
        "n_skipped": len(skipped),
        "skipped": skipped,
        "per_block": vals,
        "label": f"mean over {a.size} blocks; SE = sd/sqrt(n_blocks), valid if blocks are independent",
    }
