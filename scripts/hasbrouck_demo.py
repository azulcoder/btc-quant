"""hasbrouck_demo.py — run every estimator in `btcquant/hasbrouck.py` on SIMULATED
series and write a self-contained page you can read at a glance.

    make hasbrouck-demo          # regenerate dashboard/hasbrouck.html
    make dash                    # then http://127.0.0.1:8787/hasbrouck.html

WHAT THIS IS FOR. The estimators arrived with a verification document
(`docs/VERIFY-hasbrouck-extraction.md`) that is authoritative but long. This turns the
part of it that a reader can *judge* into six panels, each printing its conclusion beside
the numbers that produced it, and each stating the planted truth so agreement is
checkable rather than asserted (blindness class G).

RULE-EXTRACT-5, and it is the reason this file has no data access
------------------------------------------------------------------
Nothing here reads `data/`, queries `hf://`, opens the tick store, or approaches the
LockBox. Every series is generated in-process from a seeded RNG with known parameters.
Simulation with controls costs **0 looks**; a real partition costs one, and would need a
pre-registration declaring the whole specification set with an `N_trials` cap first. The
only input any estimator here receives is a numpy array this script made.

Conventions
-----------
* Units are **log price**, so ``1e-4 == 1 bps``. Planted values follow the fixtures in
  `docs/VERIFY-hasbrouck-extraction.md` §4 (``c = 4e-04``, ``lambda = 3e-04``); they are
  [DIASUMSIKAN], NOT calibrated to BTCUSDT — this repo's own measurement puts the median
  perp spread at one tick, ``0.0157 bps`` (VERIFY §6), roughly 400x tighter.
* ``t`` is trade time, not calendar time. Nothing here is per-second.
* Every number on the page carries a label: [DIUKUR] measured on the simulated series,
  [DISIMPULKAN] derived from the planted parameters, [DIASUMSIKAN] chosen by this script,
  [UNVERIFIED] claimed but with no checker on this machine.
* Deterministic by construction: one master seed, fixed sample sizes, no wall-clock in
  the output. Two runs produce byte-identical HTML — that is the check, so it is stated
  on the page rather than hoped for.
* Sample sizes are 1e5..4e5. Bigger would tighten the error bars and cost more than the
  panels are worth; where a number is at the sample's noise floor the panel says so
  instead of quietly printing it.

Decisions this script makes, and why
------------------------------------
1. **One series drives panels A, B and F.** They are three readings of the same
   generalized-Roll draw, so a reader can carry a number from one panel to the next
   instead of re-anchoring on a fresh sample each time.
2. **Closed form is the authority; simulation is the confirmation.** Where an exact
   expression exists (MA(q) autocovariances, the AR failure ratios, the null variance of
   the gate statistic) it is computed and printed next to the measurement. A simulation
   alone cannot separate "correct" from "coincidentally close on one parameter".
3. **The page reports what the estimators refuse to do.** ABSTAIN and FAIL verdicts are
   rendered as results, not as gaps, because the refusals are the product.

Research only. Reads nothing, writes one HTML file under `dashboard/`.
"""

from __future__ import annotations

import html
import sys
from pathlib import Path
from typing import Callable, Sequence

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from btcquant import hasbrouck as H  # noqa: E402  (path set above)

OUT = REPO / "dashboard" / "hasbrouck.html"

# --------------------------------------------------------------------------- #
# The planted world. Everything downstream is a function of these.             #
# --------------------------------------------------------------------------- #
SEED = 20260806                  # master seed; every panel derives its own from it
C_TRUE = 4.0e-4                  # half-spread, log price -> 4.0 bps        [DIASUMSIKAN]
LAM_TRUE = 3.0e-4                # adverse selection                        [DIASUMSIKAN]
SU_TRUE = 2.0e-4                 # public-information sd                    [DIASUMSIKAN]
N_MAIN = 400_000                 # panels A, B, F
N_PANEL = 300_000                # panels C, D
SIGMA_EPS = 1.0e-4               # MA fixtures in panels C and D

GOLDEN = (5.0 ** 0.5 - 1.0) / 2.0   # 0.6180339887 — where the doubly-wrong AR reading is exact

MA3_PATTERNS = [
    ([0.6, -0.3, 0.15], "the s9-s12 fixture"),
    ([0.6, 0.3, 0.15], "all positive"),
    ([-0.6, -0.3, -0.15], "theta(1) ~ 0, silent"),
    ([-0.6, 0.3, -0.15], "alternating, negative"),
]
AR_SWEEP = (-0.8, -0.4, -0.1, 0.1, 0.4, GOLDEN, 0.8)
AR_SEEDS = 5                     # per theta; the AR route has ~1.7% sd at n = 3e5


# --------------------------------------------------------------------------- #
# Simulators — pure, seeded, no I/O                                            #
# --------------------------------------------------------------------------- #
def generalized_roll(n: int, c: float, lam: float, sigma_u: float,
                     rng: np.random.Generator) -> np.ndarray:
    """`dp_t = -c*q_{t-1} + (c+lambda)*q_t + u_t` with `q` iid +/-1 and `u` iid Gaussian.

    This is the data-generating process the whole module is written against. It is a true
    MA(1) in `dp`, which is what makes it a positive control for the E2 gate.
    """
    q = rng.choice([-1.0, 1.0], size=n + 1)
    u = rng.normal(0.0, sigma_u, size=n + 1)
    return -c * q[:-1] + (c + lam) * q[1:] + u[1:]


def ma_process(theta: Sequence[float], n: int, sigma_eps: float,
               rng: np.random.Generator) -> np.ndarray:
    """`dp_t = eps_t + theta_1*eps_{t-1} + ... + theta_q*eps_{t-q}`, Gaussian innovations."""
    th = np.concatenate([[1.0], np.asarray(theta, dtype=float)])
    e = rng.normal(0.0, sigma_eps, size=n + th.size - 1)
    out = np.zeros(n)
    for j, coef in enumerate(th):
        start = th.size - 1 - j
        out += coef * e[start:start + n]
    return out


def random_walk(n: int, sigma: float, rng: np.random.Generator) -> np.ndarray:
    """Price changes of a pure random walk: no spread, no impact, nothing to find."""
    return rng.normal(0.0, sigma, size=n)


def momentum_ar1(n: int, rho: float, sigma: float, rng: np.random.Generator) -> np.ndarray:
    """`dp_t = rho*dp_{t-1} + e_t` with `rho > 0` — positive gamma_1, the opposite signature."""
    e = rng.normal(0.0, sigma, size=n)
    out = np.empty(n)
    prev = 0.0
    for i in range(n):
        prev = rho * prev + e[i]
        out[i] = prev
    return out


# --------------------------------------------------------------------------- #
# Closed forms — the authority the simulations are checked against             #
# --------------------------------------------------------------------------- #
def ma_autocovariances_closed(theta: Sequence[float], sigma_eps: float,
                              max_lag: int) -> np.ndarray:
    """`gamma_h = sigma2_eps * sum_j theta_j*theta_{j+h}` with `theta_0 = 1`. Exact."""
    th = np.concatenate([[1.0], np.asarray(theta, dtype=float)])
    q = th.size - 1
    return np.array([sigma_eps ** 2 * float(sum(th[j] * th[j + h] for j in range(q - h + 1)))
                     if h <= q else 0.0 for h in range(max_lag + 1)])


def sigma2_w_closed_ma(theta: Sequence[float], sigma_eps: float) -> float:
    """`sigma2_w = theta(1)^2 * sigma2_eps` — the quantity every route is aiming at."""
    return float((1.0 + sum(theta)) ** 2 * sigma_eps ** 2)


def gate_null_moments(rho1: float, n_lags: int) -> dict:
    """Mean and variance of the gate's Q statistic under a TRUE MA(1). [repo derivation]

    The gate sums squared z-scores over lags 2..m and compares to `chi2_{m-1}`, which is
    only right if those z-scores are independent. They are not. Bartlett's formula for an
    MA(1) (`rho_k = 0`, `k >= 2`) gives

        var(rho_k)          = (1 + 2*rho_1^2) / n
        cov(rho_k, rho_k+1) = 2*rho_1 / n
        cov(rho_k, rho_k+2) = rho_1^2 / n
        cov beyond lag 2    = 0

    so the correlation matrix `R` of the z-scores is banded, and for a quadratic form in
    correlated standard normals `E[Q] = tr(R)`, `Var[Q] = 2*tr(R^2)`. The mean survives
    (`tr(R) = m-1`) but the variance does not: at `rho_1 = -0.41` it is ~30 against
    `chi2_9`'s 18, so the 95% point is too low and the gate over-refuses.

    This is a proposition of this repo, derived here and checked against the measured
    spread of Q on the same page — NOT a Hasbrouck citation.
    """
    a = 2.0 * rho1 / (1.0 + 2.0 * rho1 ** 2)
    b = rho1 ** 2 / (1.0 + 2.0 * rho1 ** 2)
    R = np.eye(n_lags)
    for i in range(n_lags):
        for j in range(n_lags):
            if abs(i - j) == 1:
                R[i, j] = a
            elif abs(i - j) == 2:
                R[i, j] = b
    return {"corr_adjacent": float(a), "corr_lag2": float(b),
            "mean": float(np.trace(R)), "var": float(2.0 * np.trace(R @ R)),
            "chi2_mean": float(n_lags), "chi2_var": float(2 * n_lags)}


# --------------------------------------------------------------------------- #
# Panel A — four estimators, three independent routes                          #
# --------------------------------------------------------------------------- #
def panel_a(dp: np.ndarray) -> dict:
    """E2, E3, E4, E9 against the planted `sigma2_w`, plus an E11 subsampling SE.

    The headline is agreement. The caveat printed with it is that E2 and E3 are NOT
    independent — see `e2_e3_identity`, which is checked numerically here rather than
    asserted.
    """
    true_w = LAM_TRUE ** 2 + SU_TRUE ** 2
    gate = H.ma1_order_gate(dp)
    e2 = H.sigma2_w_ma1(dp)
    e3 = H.sigma2_w_wold(dp)
    e4 = H.sigma2_w_variance_ratio(dp)
    e9 = H.sigma2_w_ar(dp, K=30)
    e11 = H.subsample_estimate(dp, lambda x: H.sigma2_w_ar(x, K=30), n_blocks=20)

    routes = [
        {"tag": "E2", "name": "gamma_0 + 2*gamma_1", "fn": "sigma2_w_ma1",
         "res": e2, "note": "gated on the MA(1) order test"},
        {"tag": "E3", "name": "theta(1)^2 * sigma2_eps", "fn": "sigma2_w_wold",
         "res": e3, "note": "invertible Wold root"},
        {"tag": "E4", "name": "intercept of Var(dp_k)/k on 1/k", "fn": "sigma2_w_variance_ratio",
         "res": e4, "note": "intercept only, never the slope"},
        {"tag": "E9", "name": "sigma2_eps / phi(1)^2", "fn": "sigma2_w_ar",
         "res": e9, "note": "AR(30) truncation, Yule-Walker"},
    ]
    for r in routes:
        v = float(r["res"].get("sigma2_w", float("nan")))
        r["value"] = v
        r["rel_err"] = v / true_w - 1.0 if np.isfinite(v) else float("nan")

    # Is E2 == E3 an identity, or does this one draw just happen to agree? For an MA(1)
    # the two Wold roots satisfy s2_a + s2_b = gamma_0 and s2_a*s2_b = gamma_1^2, hence
    # (1+theta)^2*s2_a = s2_a + 2*gamma_1 + gamma_1^2/s2_a = gamma_0 + 2*gamma_1. Checked
    # here on random admissible moment pairs, not on the panel's single draw.
    rng = np.random.default_rng(SEED + 101)
    worst = 0.0
    for _ in range(20_000):
        g0 = float(rng.uniform(1e-9, 1.0))
        g1 = -float(rng.uniform(0.0, g0 / 2.0))
        d = np.sqrt(g0 * g0 - 4.0 * g1 * g1)
        s2 = 0.5 * (g0 + d)
        lhs = (1.0 + g1 / s2) ** 2 * s2
        worst = max(worst, abs(lhs - (g0 + 2.0 * g1)) / abs(g0 + 2.0 * g1))

    return {
        "true_w": true_w, "gate": gate, "routes": routes, "e11": e11,
        "e2_e3_gap": abs(e2["sigma2_w"] - e3["sigma2_w"]),
        "e2_e3_identity_worst": float(worst),
        "spread": max(r["value"] for r in routes) - min(r["value"] for r in routes),
    }


# --------------------------------------------------------------------------- #
# Panel B — the identified interval                                            #
# --------------------------------------------------------------------------- #
def panel_b(dp: np.ndarray) -> dict:
    """E1 and E5 as the two ends of one interval, plus E5's two-case control.

    The control matters more than the interval: E5 must be EXACT when `sigma2_u = 0` and
    strictly BELOW `c^2` when `lambda = 0`. An implementation that fails to separate those
    two cases is wrong in a way no single series would reveal.
    """
    e1 = H.roll(dp)
    e5 = H.pricing_error_lower_bound(dp)
    iv = H.identified_interval_c(dp)
    s_true = C_TRUE + LAM_TRUE

    controls = []
    for label, (c, lam, su), expect in (
        ("private information only (sigma_u = 0)", (C_TRUE, LAM_TRUE, 0.0), "exactly c^2"),
        ("public information only (lambda = 0)", (C_TRUE, 0.0, SU_TRUE), "strictly below c^2"),
    ):
        series = generalized_roll(N_PANEL, c, lam, su, np.random.default_rng(SEED + 202))
        b = H.pricing_error_lower_bound(series)
        # the same bound evaluated on the POPULATION moments — no sampling error at all
        g0 = c * c + (c + lam) ** 2 + su * su
        g1 = -c * (c + lam)
        closed = 0.5 * (g0 - float(np.sqrt(g0 * g0 - 4.0 * g1 * g1)))
        controls.append({"label": label, "c": c, "lam": lam, "su": su,
                         "bound": b["lower_bound"], "c2": c * c,
                         "ratio": b["lower_bound"] / (c * c), "expect": expect,
                         "closed": closed, "closed_ratio": closed / (c * c),
                         "verdict": b["verdict"]})
    return {"e1": e1, "e5": e5, "iv": iv, "c_true": C_TRUE, "s_true": s_true,
            "geometric_mean": float(np.sqrt(C_TRUE * s_true)), "controls": controls,
            "contains_true_c": bool(iv["c_lo"] <= C_TRUE <= iv["c_hi"])}


# --------------------------------------------------------------------------- #
# Panel C — why the gate exists, and what it costs                             #
# --------------------------------------------------------------------------- #
def panel_c() -> dict:
    """Four true MA(3) fixtures, E2 forced through the gate, plus the gate's own control.

    The second half is the part this repo's rules demand and the extraction documents do
    not have: a verifier tested on cases known to PASS (blindness class I). A gate that
    refuses 8.5% of genuinely MA(1) series when it advertises 5% is still usable, but the
    number belongs on the page, not in someone's head.
    """
    rows = []
    for i, (theta, note) in enumerate(MA3_PATTERNS):
        g_closed = ma_autocovariances_closed(theta, SIGMA_EPS, 3)
        true_w = sigma2_w_closed_ma(theta, SIGMA_EPS)
        e2_closed = float(g_closed[0] + 2.0 * g_closed[1])
        dp = ma_process(theta, N_PANEL, SIGMA_EPS, np.random.default_rng(SEED + 300 + i))
        g_hat = H.autocovariances(dp, 3)
        correct_order = float(g_hat[0] + 2.0 * (g_hat[1] + g_hat[2] + g_hat[3]))
        forced = H.sigma2_w_ma1(dp, force=True)
        gated = H.sigma2_w_ma1(dp)
        rows.append({
            "theta": list(theta), "note": note, "true_w": true_w,
            "e2_closed": e2_closed, "ratio_closed": e2_closed / true_w,
            "correct_order": correct_order, "correct_ratio": correct_order / true_w,
            "forced": float(forced["sigma2_w"]), "forced_verdict": forced["verdict"],
            "forced_ratio": float(forced["sigma2_w"]) / true_w,
            "gated_verdict": gated["verdict"],
            "gate_q": float(gated["gate"]["q_stat"]), "gate_crit": float(gated["gate"]["q_crit"]),
            "at_noise_floor": abs(true_w) < 1e-10,
        })

    # --- the control: how often does the gate refuse a series it should accept? ---
    n_seeds, n_ctrl = 400, 100_000
    qs, rejects = [], 0
    for s in range(n_seeds):
        series = generalized_roll(n_ctrl, C_TRUE, LAM_TRUE, SU_TRUE,
                                  np.random.default_rng(SEED + 4000 + s))
        g = H.ma1_order_gate(series)
        qs.append(g["q_stat"])
        rejects += (not g["passed"])
    qs = np.array(qs)
    # SE of the sample variance from the sample's own fourth moment — Q is not normal,
    # so 2*var^2/(n-1) would understate it. Without this the comparison below is a
    # number with no error bar, which is a claim with no checker.
    m4 = float(((qs - qs.mean()) ** 4).mean())
    s2 = float(qs.var(ddof=1))
    var_se = float(np.sqrt(max(m4 - (n_seeds - 3) / (n_seeds - 1) * s2 ** 2, 0.0) / n_seeds))
    rho1 = -C_TRUE * (C_TRUE + LAM_TRUE) / (C_TRUE ** 2 + (C_TRUE + LAM_TRUE) ** 2 + SU_TRUE ** 2)
    pred = gate_null_moments(rho1, 9)
    return {"rows": rows, "control": {
        "n_seeds": n_seeds, "n": n_ctrl, "rejects": rejects, "rate": rejects / n_seeds,
        "nominal": 0.05, "binom_se": float(np.sqrt(0.05 * 0.95 / n_seeds)),
        "q_mean": float(qs.mean()), "q_var": s2, "q_var_se": var_se,
        "q_mean_se": float(qs.std(ddof=1) / np.sqrt(n_seeds)),
        "rho1": float(rho1), "pred": pred,
        "q_crit": float(H._chi2_ppf(0.95, 9)),
    }}


# --------------------------------------------------------------------------- #
# Panel D — the AR formula failure map                                         #
# --------------------------------------------------------------------------- #
def panel_d() -> dict:
    """One correct reading of `sigma2_eps/phi(1)^2` and the three wrong ones, swept over theta.

    Ratios to truth in closed form, using `sum(phi) = theta/(1+theta)` and
    `phi(1) = 1/(1+theta)` for an MA(1):

        sigma2_eps / phi(1)^2   ->  1                 (correct)
        sigma2_eps / phi(1)     ->  1/(1+theta)       (square dropped)
        sigma2_eps / sum(phi)   ->  1/(theta*(1+theta))   (square dropped AND phi(1) misread)
        sigma2_eps / sum(phi)^2 ->  1/theta^2         (phi(1) misread)

    The third is exactly 1 at `theta^2 + theta - 1 = 0`, i.e. `theta = (sqrt(5)-1)/2`.
    """
    rows = []
    for i, th in enumerate(AR_SWEEP):
        true_w = (1.0 + th) ** 2 * SIGMA_EPS ** 2
        vals = {"correct": [], "no_square": [], "sum_phi": [], "sum_phi_sq": []}
        verdicts = set()
        for s in range(AR_SEEDS):
            dp = ma_process([th], N_PANEL, SIGMA_EPS,
                            np.random.default_rng(SEED + 500 + 17 * i + s))
            r = H.sigma2_w_ar(dp, K=30)
            verdicts.add(r["verdict"])
            if r["verdict"] != "OK":
                continue
            s2e, p1, sp = r["sigma2_eps"], r["phi_one"], r["sum_phi"]
            vals["correct"].append(s2e / p1 ** 2)
            vals["no_square"].append(s2e / p1)
            vals["sum_phi"].append(s2e / sp)
            vals["sum_phi_sq"].append(s2e / sp ** 2)
        mean = {k: float(np.mean(v)) for k, v in vals.items()}
        sd = {k: float(np.std(v, ddof=1)) for k, v in vals.items()}
        rows.append({
            "theta": th, "true_w": true_w, "is_golden": abs(th - GOLDEN) < 1e-9,
            "verdicts": sorted(verdicts),
            "measured": mean, "sd": sd,
            "ratio": {k: mean[k] / true_w for k in mean},
            "ratio_sd": {k: sd[k] / true_w for k in sd},
            "closed": {"correct": 1.0, "no_square": 1.0 / (1.0 + th),
                       "sum_phi": 1.0 / (th * (1.0 + th)), "sum_phi_sq": 1.0 / th ** 2},
        })
    return {"rows": rows, "n_seeds": AR_SEEDS, "golden": GOLDEN}


# --------------------------------------------------------------------------- #
# Panel E — negative controls                                                  #
# --------------------------------------------------------------------------- #
def panel_e() -> dict:
    """A pure random walk and a momentum series: both must produce refusals, not numbers."""
    n_seeds, n = 200, 50_000
    abstains, fabricated = 0, []
    for s in range(n_seeds):
        dp = random_walk(n, SU_TRUE, np.random.default_rng(SEED + 6000 + s))
        r = H.roll(dp)
        if r["verdict"] == H.ABSTAIN:
            abstains += 1
            fabricated.append(float(np.sqrt(abs(r["gamma_1"]))))
    fabricated = np.array(fabricated)

    # what the estimators that do NOT need a sign say about the same kind of series
    dp_rw = random_walk(N_PANEL, SU_TRUE, np.random.default_rng(SEED + 6500))
    rw_wold = H.sigma2_w_wold(dp_rw)
    rw_vr = H.sigma2_w_variance_ratio(dp_rw)
    slopes, intercepts = [], []
    for s in range(20):
        d = random_walk(N_PANEL, SU_TRUE, np.random.default_rng(SEED + 6600 + s))
        v = H.sigma2_w_variance_ratio(d)
        slopes.append(v["slope_DIAGNOSTIC_ONLY"])
        intercepts.append(v["sigma2_w"])
    slopes, intercepts = np.array(slopes), np.array(intercepts)

    dp_mom = momentum_ar1(200_000, 0.35, SU_TRUE, np.random.default_rng(SEED + 7000))
    mom = H.roll(dp_mom)
    mom_iv = H.identified_interval_c(dp_mom)
    mom_e5 = H.pricing_error_lower_bound(dp_mom)
    rw_e5 = H.pricing_error_lower_bound(dp_rw)

    return {
        "rw": {"n_seeds": n_seeds, "n": n, "abstains": abstains, "rate": abstains / n_seeds,
               "expected": 0.5, "binom_se": float(np.sqrt(0.25 / n_seeds)),
               "fabricated_median_c": float(np.median(fabricated)),
               "fabricated_median_spread_bps": float(2 * np.median(fabricated) * 1e4),
               "fabricated_max_spread_bps": float(2 * fabricated.max() * 1e4)},
        "rw_wold": rw_wold, "rw_vr": rw_vr, "rw_e5": rw_e5,
        "rw_true_w": SU_TRUE ** 2,
        "slope_mean": float(slopes.mean()), "slope_sd": float(slopes.std(ddof=1)),
        "slope_t": float(abs(slopes.mean()) / slopes.std(ddof=1)),
        "intercept_mean": float(intercepts.mean()), "intercept_sd": float(intercepts.std(ddof=1)),
        "n_slope_seeds": 20,
        "momentum": {"rho": 0.35, "n": 200_000, "roll": mom, "iv": mom_iv, "e5": mom_e5},
    }


# --------------------------------------------------------------------------- #
# Panel F — impulse response                                                   #
# --------------------------------------------------------------------------- #
def panel_f(dp: np.ndarray) -> dict:
    """E10 on the AR(30) fit of the panel-A series, read as a LEVEL response.

    The planted long run is `theta(1) = 1 + theta` from the population `(gamma_0, gamma_1)`
    of the planted parameters — a [DISIMPULKAN] number, not a fitted one.
    """
    ar = H.sigma2_w_ar(dp, K=30)
    irf = H.cumulative_impulse_response(ar["phi"], horizon=20)
    g0 = C_TRUE ** 2 + (C_TRUE + LAM_TRUE) ** 2 + SU_TRUE ** 2
    g1 = -C_TRUE * (C_TRUE + LAM_TRUE)
    s2_eps = 0.5 * (g0 + float(np.sqrt(g0 * g0 - 4.0 * g1 * g1)))
    theta_pop = g1 / s2_eps
    return {"ar": ar, "irf": irf,
            "planted_long_run": float(1.0 + theta_pop),
            "planted_theta": float(theta_pop),
            "ar_limit": float(1.0 / ar["phi_one"]),
            "horizon_20": float(irf["cumulative"][-1]),
            "impact_ratio": float(irf["cumulative"][0] / (1.0 + theta_pop))}


# --------------------------------------------------------------------------- #
# Formatting helpers                                                           #
# --------------------------------------------------------------------------- #
def esc(s) -> str:
    return html.escape(str(s), quote=True)


def sci(x, digits: int = 4) -> str:
    if x is None or not np.isfinite(x):
        return "n/a"
    return f"{x:.{digits}e}"


def ratio(x, digits: int = 3) -> str:
    if x is None or not np.isfinite(x):
        return "n/a"
    if abs(x) >= 100:
        return f"{x:,.1f}x"
    return f"{x:.{digits}f}x"


def pct(x, digits: int = 2) -> str:
    if x is None or not np.isfinite(x):
        return "n/a"
    return f"{x * 100:+.{digits}f}%"


def bps(x, digits: int = 3) -> str:
    return f"{x * 1e4:.{digits}f}"


def verdict_badge(v: str) -> str:
    cls = {"OK": "ok", "OK_WITH_WARNING": "warn", "ABSTAIN": "abstain", "FAIL": "fail"}
    return f'<span class="v v-{cls.get(v, "warn")}">{esc(v)}</span>'


def tag(kind: str) -> str:
    return f'<span class="tag t-{kind.lower()}">[{kind}]</span>'


def table(headers: Sequence[str], rows: Sequence[Sequence[str]],
          numeric: Sequence[bool] | None = None, cls: str = "") -> str:
    numeric = numeric or [False] * len(headers)
    head = "".join(f'<th class="{"n" if numeric[i] else ""}">{h}</th>'
                   for i, h in enumerate(headers))
    body = []
    for row in rows:
        cells = "".join(f'<td class="{"n" if numeric[i] else ""}">{c}</td>'
                        for i, c in enumerate(row))
        body.append(f"<tr>{cells}</tr>")
    return (f'<div class="scroll"><table class="{cls}"><thead><tr>{head}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>')


def panel(anchor: str, title: str, planted: str, figure: str, rail: str) -> str:
    return f"""
<section class="panel" id="{anchor}">
  <h2><span class="pid">{anchor.upper()}</span>{esc(title)}</h2>
  <p class="planted"><strong>Planted truth.</strong> {planted}</p>
  <div class="body">
    <div class="figure">{figure}</div>
    <aside class="rail">{rail}</aside>
  </div>
</section>"""


# --------------------------------------------------------------------------- #
# Inline SVG                                                                   #
# --------------------------------------------------------------------------- #
def svg_interval(b: dict) -> str:
    """Panel B: the identified set for c as a bar, with the planted truth marked inside."""
    lo_bps, hi_bps = b["iv"]["c_lo"] * 1e4, b["iv"]["c_hi"] * 1e4
    c_bps, s_bps = b["c_true"] * 1e4, b["s_true"] * 1e4
    x0, x1 = 3.4, 7.6                      # axis range in bps
    px0, px1 = 78.0, 720.0

    def X(v: float) -> float:
        return px0 + (v - x0) / (x1 - x0) * (px1 - px0)

    ticks = []
    t = 3.5
    while t <= 7.51:
        x = X(t)
        ticks.append(f'<line class="tick" x1="{x:.1f}" y1="112" x2="{x:.1f}" y2="118"/>'
                     f'<text class="axis-lab" x="{x:.1f}" y="132" text-anchor="middle">{t:.1f}</text>')
        t += 0.5
    band = (f'<rect class="band" x="{X(lo_bps):.1f}" y="62" width="{X(hi_bps) - X(lo_bps):.1f}" '
            f'height="34" rx="4"><title>identified set for c: '
            f'{lo_bps:.4f} to {hi_bps:.4f} bps</title></rect>')

    def marker(v_bps: float, label: str, cls: str, above: bool) -> str:
        x = X(v_bps)
        y1, y2 = (48, 100) if above else (58, 124)
        ty = 40 if above else 148
        return (f'<line class="{cls}" x1="{x:.1f}" y1="{y1}" x2="{x:.1f}" y2="{y2}"/>'
                f'<text class="mark-lab {cls}-lab" x="{x:.1f}" y="{ty}" text-anchor="middle">'
                f'{esc(label)}</text><circle class="hit" cx="{x:.1f}" cy="79" r="12">'
                f'<title>{esc(label)}</title></circle>')

    return f"""<svg class="fig" viewBox="0 0 760 200" role="img"
     aria-labelledby="ivt ivd" preserveAspectRatio="xMidYMid meet">
  <title id="ivt">The identified set for the half-spread c</title>
  <desc id="ivd">A horizontal bar from the E5 bound at {lo_bps:.4f} bps to the Roll estimate at
  {hi_bps:.4f} bps. The planted c of {c_bps:.3f} bps lies inside it; the planted c+lambda of
  {s_bps:.3f} bps lies outside to the right.</desc>
  <line class="axis" x1="{px0}" y1="112" x2="{px1}" y2="112"/>
  {''.join(ticks)}
  <text class="axis-cap" x="{(px0 + px1) / 2:.0f}" y="172" text-anchor="middle">half-spread, bps of log price (1e-4 = 1 bps)</text>
  {band}
  <text class="band-lab" x="{(X(lo_bps) + X(hi_bps)) / 2:.1f}" y="84" text-anchor="middle">identified set for c</text>
  {marker(c_bps, f'planted c = {c_bps:.3f}', 'truth', True)}
  {marker(s_bps, f'planted c+lambda = {s_bps:.3f}', 'truth', True)}
  {marker(lo_bps, f'E5 bound = {lo_bps:.4f}', 'edge', False)}
  {marker(hi_bps, f'Roll = {hi_bps:.4f}', 'edge', False)}
</svg>"""


def svg_irf(f: dict) -> str:
    """Panel F: the cumulative response path of the price LEVEL."""
    cum = f["irf"]["cumulative"]
    hz = len(cum) - 1
    px0, px1, py0, py1 = 78.0, 720.0, 40.0, 210.0
    ymax = 1.08

    def X(k: int) -> float:
        return px0 + k / hz * (px1 - px0)

    def Y(v: float) -> float:
        return py1 - (v / ymax) * (py1 - py0)

    grid = []
    for gv in (0.0, 0.25, 0.5, 0.75, 1.0):
        y = Y(gv)
        grid.append(f'<line class="grid" x1="{px0}" y1="{y:.1f}" x2="{px1}" y2="{y:.1f}"/>'
                    f'<text class="axis-lab" x="{px0 - 10:.0f}" y="{y + 4:.1f}" '
                    f'text-anchor="end">{gv:.2f}</text>')
    xt = []
    for k in range(0, hz + 1, 4):
        xt.append(f'<line class="tick" x1="{X(k):.1f}" y1="{py1:.0f}" x2="{X(k):.1f}" '
                  f'y2="{py1 + 6:.0f}"/><text class="axis-lab" x="{X(k):.1f}" '
                  f'y="{py1 + 22:.0f}" text-anchor="middle">{k}</text>')
    pts = " ".join(f"{X(k):.1f},{Y(v):.1f}" for k, v in enumerate(cum))
    dots = "".join(
        f'<circle class="dot" cx="{X(k):.1f}" cy="{Y(v):.1f}" r="3"/>'
        f'<circle class="hit" cx="{X(k):.1f}" cy="{Y(v):.1f}" r="11">'
        f'<title>k = {k}: cumulative {v:.5f}</title></circle>'
        for k, v in enumerate(cum))
    lr = f["planted_long_run"]
    return f"""<svg class="fig" viewBox="0 0 760 272" role="img"
     aria-labelledby="irt ird" preserveAspectRatio="xMidYMid meet">
  <title id="irt">Cumulative impulse response of the price level</title>
  <desc id="ird">The cumulative response starts at 1.00 on impact and settles at about
  {cum[-1]:.4f} by horizon {hz}, against a planted long run of {lr:.4f}.</desc>
  {''.join(grid)}
  {''.join(xt)}
  <line class="axis" x1="{px0}" y1="{py1:.0f}" x2="{px1}" y2="{py1:.0f}"/>
  <line class="target" x1="{px0}" y1="{Y(lr):.1f}" x2="{px1}" y2="{Y(lr):.1f}"/>
  <text class="target-lab" x="{px1 - 4:.0f}" y="{Y(lr) - 8:.1f}" text-anchor="end">planted long run theta(1) = {lr:.4f}</text>
  <polyline class="path" points="{pts}"/>
  {dots}
  <text class="pt-lab" x="{X(0) + 8:.1f}" y="{Y(cum[0]) - 10:.1f}">impact 1.0000</text>
  <text class="pt-lab" x="{X(hz):.1f}" y="{Y(cum[-1]) + 26:.1f}" text-anchor="end">k=20: {cum[-1]:.4f}</text>
  <text class="axis-cap" x="{(px0 + px1) / 2:.0f}" y="{py1 + 44:.0f}" text-anchor="middle">horizon k, in trades after the shock</text>
</svg>"""


# --------------------------------------------------------------------------- #
# The page                                                                     #
# --------------------------------------------------------------------------- #
CSS = """
:root{
  color-scheme:light;
  --page:#f9f9f7; --surface:#fcfcfb; --ink:#0b0b0b; --ink2:#52514e; --muted:#898781;
  --grid:#e1e0d9; --axis:#c3c2b7; --rule:rgba(11,11,11,.12); --accent:#2a78d6;
  --accent-soft:rgba(42,120,214,.16); --good:#0ca30c; --warn:#fab219; --serious:#ec835a;
  --crit:#d03b3b; --band:rgba(42,120,214,.18);
}
@media (prefers-color-scheme:dark){
  :root:where(:not([data-theme="light"])){
    color-scheme:dark;
    --page:#0d0d0d; --surface:#1a1a19; --ink:#fff; --ink2:#c3c2b7; --muted:#898781;
    --grid:#2c2c2a; --axis:#383835; --rule:rgba(255,255,255,.14); --accent:#3987e5;
    --accent-soft:rgba(57,135,229,.22); --band:rgba(57,135,229,.24);
  }
}
:root[data-theme="dark"]{
  color-scheme:dark;
  --page:#0d0d0d; --surface:#1a1a19; --ink:#fff; --ink2:#c3c2b7; --muted:#898781;
  --grid:#2c2c2a; --axis:#383835; --rule:rgba(255,255,255,.14); --accent:#3987e5;
  --accent-soft:rgba(57,135,229,.22); --band:rgba(57,135,229,.24);
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{
  margin:0; background:var(--page); color:var(--ink);
  font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif;
  overflow-x:hidden;
}
.wrap{max-width:1180px; margin:0 auto; padding:28px 20px 80px}
h1{font-size:26px; line-height:1.25; margin:0 0 6px; letter-spacing:-.01em}
h2{font-size:18px; margin:0 0 10px; display:flex; align-items:baseline; gap:10px}
h3{font-size:14px; margin:22px 0 8px; color:var(--ink2); text-transform:uppercase;
   letter-spacing:.06em}
p{margin:0 0 10px}
a{color:var(--accent)}
code,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; font-size:.92em}
.sub{color:var(--ink2); font-size:14px; margin:0 0 4px}
.topbar{display:flex; flex-wrap:wrap; gap:14px; align-items:flex-start;
        justify-content:space-between; margin-bottom:18px}
.themer{display:flex; gap:0; border:1px solid var(--rule); border-radius:7px; overflow:hidden}
.themer button{background:transparent; border:0; color:var(--ink2); font:inherit; font-size:12px;
  padding:5px 10px; cursor:pointer; border-right:1px solid var(--rule)}
.themer button:last-child{border-right:0}
.themer button:hover{background:var(--accent-soft); color:var(--ink)}
.banner{border:1px solid var(--rule); border-left:3px solid var(--accent); background:var(--surface);
  border-radius:8px; padding:14px 16px; margin:0 0 22px}
.banner strong{letter-spacing:.02em}
.panel{background:var(--surface); border:1px solid var(--rule); border-radius:10px;
  padding:18px 18px 14px; margin:0 0 20px}
.pid{display:inline-block; min-width:1.6em; font:600 13px/1 ui-monospace,SFMono-Regular,Menlo,monospace;
  color:var(--surface); background:var(--ink2); padding:5px 7px; border-radius:5px}
.planted{font-size:13.5px; color:var(--ink2); border-left:2px solid var(--accent);
  padding-left:12px; margin:0 0 14px}
.body{display:grid; grid-template-columns:minmax(0,1fr) minmax(15rem,19rem); gap:22px;
  align-items:start}
@media (max-width:960px){ .body{grid-template-columns:minmax(0,1fr)} }
.figure{min-width:0}
.rail{font-size:13.5px; color:var(--ink2); border-left:1px solid var(--rule); padding-left:16px;
  position:sticky; top:16px}
.rail h4{margin:0 0 6px; font-size:11px; letter-spacing:.09em; text-transform:uppercase;
  color:var(--muted)}
.rail p{margin:0 0 10px}
.rail .concl{color:var(--ink); font-weight:600}
@media (max-width:960px){ .rail{position:static; border-left:0; border-top:1px solid var(--rule);
  padding:14px 0 0} }
.scroll{overflow-x:auto; margin:0 0 12px; border:1px solid var(--rule); border-radius:8px}
table{border-collapse:collapse; width:100%; min-width:max-content; font-size:13px}
th,td{padding:7px 10px; text-align:left; border-bottom:1px solid var(--rule); white-space:nowrap}
thead th{background:var(--page); font-weight:600; font-size:11.5px; letter-spacing:.04em;
  text-transform:uppercase; color:var(--ink2); position:sticky; top:0}
tbody tr:last-child td{border-bottom:0}
td.n,th.n{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  font-variant-numeric:tabular-nums; text-align:right}
tr.hi td{background:var(--accent-soft)}
.v{display:inline-block; font:600 11px/1 ui-monospace,SFMono-Regular,Menlo,monospace;
  padding:4px 6px; border-radius:4px; border:1px solid currentColor; white-space:nowrap}
.v-ok{color:var(--good)} .v-warn{color:var(--serious)} .v-abstain{color:var(--accent)}
.v-fail{color:var(--crit)}
.tag{font:600 10px/1 ui-monospace,SFMono-Regular,Menlo,monospace; color:var(--muted);
  letter-spacing:.02em; white-space:nowrap}
.t-diukur{color:var(--good)} .t-disimpulkan{color:var(--accent)}
.t-diasumsikan{color:var(--serious)} .t-unverified{color:var(--crit)}
pre.eq{background:var(--page); border:1px solid var(--rule); border-radius:8px; padding:12px 14px;
  overflow-x:auto; font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  font-size:12.5px; line-height:1.5; margin:0 0 12px}
.fig{width:100%; height:auto; display:block; margin:4px 0 10px}
.fig .axis{stroke:var(--axis); stroke-width:1}
.fig .grid{stroke:var(--grid); stroke-width:1}
.fig .tick{stroke:var(--axis); stroke-width:1}
.fig .axis-lab{fill:var(--muted); font:11px ui-monospace,SFMono-Regular,Menlo,monospace}
.fig .axis-cap{fill:var(--ink2); font:12px system-ui,sans-serif}
.fig .band{fill:var(--band); stroke:var(--accent); stroke-width:2}
.fig .band-lab{fill:var(--ink2); font:11px system-ui,sans-serif}
.fig .truth{stroke:var(--ink); stroke-width:2}
.fig .edge{stroke:var(--accent); stroke-width:2; stroke-dasharray:4 3}
.fig .mark-lab{font:11px ui-monospace,SFMono-Regular,Menlo,monospace}
.fig .truth-lab{fill:var(--ink)}
.fig .edge-lab{fill:var(--accent)}
.fig .path{fill:none; stroke:var(--accent); stroke-width:2; stroke-linejoin:round}
.fig .dot{fill:var(--accent); stroke:var(--surface); stroke-width:2}
.fig .hit{fill:transparent; stroke:none}
.fig .target{stroke:var(--ink2); stroke-width:1; stroke-dasharray:5 4}
.fig .target-lab{fill:var(--ink2); font:11px ui-monospace,SFMono-Regular,Menlo,monospace}
.fig .pt-lab{fill:var(--ink); font:11px ui-monospace,SFMono-Regular,Menlo,monospace}
.note{font-size:12.5px; color:var(--muted); margin:0 0 10px}
.legend{display:flex; flex-wrap:wrap; gap:14px; font-size:12px; color:var(--ink2); margin:10px 0 0}
footer{border-top:1px solid var(--rule); margin-top:34px; padding-top:20px; font-size:13.5px;
  color:var(--ink2)}
footer h2{font-size:16px; color:var(--ink)}
footer ul{margin:0 0 12px; padding-left:20px}
footer li{margin:0 0 6px}
.stack{display:grid; gap:16px; grid-template-columns:repeat(auto-fit,minmax(230px,1fr))}
.kv{border:1px solid var(--rule); border-radius:8px; padding:12px 14px; background:var(--page)}
.kv .k{font-size:11px; letter-spacing:.07em; text-transform:uppercase; color:var(--muted)}
.kv .val{font:600 19px/1.3 ui-monospace,SFMono-Regular,Menlo,monospace;
  font-variant-numeric:tabular-nums; margin:4px 0 2px}
.kv .cap{font-size:12px; color:var(--ink2)}
"""

JS = """
(function(){
  var root=document.documentElement;
  document.querySelectorAll('[data-set-theme]').forEach(function(b){
    b.addEventListener('click',function(){
      var v=b.getAttribute('data-set-theme');
      if(v==='auto'){ root.removeAttribute('data-theme'); } else { root.setAttribute('data-theme',v); }
      document.querySelectorAll('[data-set-theme]').forEach(function(o){
        o.setAttribute('aria-pressed', String(o===b));
      });
    });
  });
})();
"""


def render(a: dict, b: dict, c: dict, d: dict, e: dict, f: dict) -> str:
    # ---------------------------------------------------------------- panel A
    rows_a = []
    for r in a["routes"]:
        res = r["res"]
        rows_a.append([
            f'<strong>{r["tag"]}</strong>',
            f'<code>{esc(r["fn"])}</code>',
            f'{esc(r["name"])}<br><span class="tag">{esc(r["note"])}</span>',
            sci(r["value"]),
            pct(r["rel_err"]),
            verdict_badge(res.get("verdict", "?")),
        ])
    fig_a = table(
        ["route", "function", "expression", "sigma2_w", "rel. error", "verdict"],
        rows_a, [False, False, False, True, True, False])
    fig_a += f"""
<div class="stack">
  <div class="kv"><div class="k">planted sigma2_w</div><div class="val">{sci(a["true_w"])}</div>
    <div class="cap">lambda^2 + sigma_u^2 {tag("DISIMPULKAN")}</div></div>
  <div class="kv"><div class="k">spread across routes</div><div class="val">{sci(a["spread"])}</div>
    <div class="cap">{a["spread"] / a["true_w"] * 100:.2f}% of the planted value {tag("DIUKUR")}</div></div>
  <div class="kv"><div class="k">E11 subsampling SE</div><div class="val">{sci(a["e11"]["se"], 3)}</div>
    <div class="cap">{a["e11"]["se"] / a["e11"]["mean"] * 100:.2f}% of the mean, {a["e11"]["n_used"]} blocks,
    {a["e11"]["n_skipped"]} skipped {tag("DIUKUR")}</div></div>
</div>
<p class="note">E11 <code>subsample_estimate</code> over 20 blocks of the same series, E9 per block:
mean {sci(a["e11"]["mean"])} against the planted {sci(a["true_w"])}. The whole spread across the four
routes is smaller than one SE of any single one of them, which is what "they agree" has to mean
before it means anything.</p>
<p class="note">Order gate on this series: {verdict_badge("OK" if a["gate"]["passed"] else "ABSTAIN")}
Q = {a["gate"]["q_stat"]:.2f} against the chi2(9) 95% point {a["gate"]["q_crit"]:.2f} {tag("DIUKUR")}.
E2 is only allowed to speak because that test passed.</p>"""
    rail_a = f"""
<h4>Reading</h4>
<p class="concl">Three independent routes and one algebraic duplicate land within
{max(abs(r["rel_err"]) for r in a["routes"]) * 100:.2f}% of a value none of them was told.</p>
<p>That is the evidence for the implementation. Each route uses a different part of the series:
E2/E3 the first two autocovariances, E4 the variance of long-horizon differences, E9 a
30-lag autoregression. A coding error would have to be shared by all three to survive this.</p>
<p><strong>The honest correction to the headline:</strong> E2 and E3 are not independent. They
differ by {sci(a["e2_e3_gap"], 2)} here, and over 20,000 random admissible moment pairs the worst
relative gap is {a["e2_e3_identity_worst"]:.1e} {tag("DIUKUR")} — a machine-precision identity, not
an agreement. For an MA(1) the two Wold roots satisfy s2_a + s2_b = gamma_0 and s2_a*s2_b =
gamma_1^2, so theta(1)^2*sigma2_eps collapses to gamma_0 + 2*gamma_1 exactly.</p>
<p>So this panel shows <strong>three</strong> independent routes, not four. E3 earns its place by
carrying the invertibility choice and by abstaining where E2's gate would not fire, not by being
a second opinion.</p>"""

    # ---------------------------------------------------------------- panel B
    iv, e1, e5 = b["iv"], b["e1"], b["e5"]
    rows_b = [
        ["E5 lower bound on sigma2_s", "<code>pricing_error_lower_bound</code>",
         sci(e5["lower_bound"]), bps(np.sqrt(e5["lower_bound"])), verdict_badge(e5["verdict"])],
        ["c^2 lower end of the identified set", "<code>identified_interval_c</code>",
         sci(iv["c2_lo"]), bps(iv["c_lo"]), verdict_badge(iv["verdict"])],
        ["c^2 upper end (= -gamma_1, Roll)", "<code>identified_interval_c</code>",
         sci(iv["c2_hi"]), bps(iv["c_hi"]), verdict_badge(iv["verdict"])],
        ["Roll half-spread estimate", "<code>roll</code>",
         sci(e1["c_hat"] ** 2), bps(e1["c_hat"]), verdict_badge(e1["verdict"])],
        ["planted c", "&mdash;", sci(b["c_true"] ** 2), bps(b["c_true"]), tag("DIASUMSIKAN")],
        ["planted c + lambda", "&mdash;", sci(b["s_true"] ** 2), bps(b["s_true"]), tag("DIASUMSIKAN")],
        ["geometric mean sqrt(c*(c+lambda))", "&mdash;",
         sci(b["geometric_mean"] ** 2), bps(b["geometric_mean"]), tag("DISIMPULKAN")],
    ]
    rows_bc = [[esc(ct["label"]), sci(ct["c2"]),
                f'{ratio(ct["closed_ratio"], 4)}<br><span class="tag">closed form</span>',
                f'{ratio(ct["ratio"], 4)}<br><span class="tag">n = {N_PANEL:,}</span>',
                esc(ct["expect"]), verdict_badge(ct["verdict"])] for ct in b["controls"]]
    fig_b = svg_interval(b)
    fig_b += table(["quantity", "function", "value (variance)", "as bps", "verdict"],
                   rows_b, [False, False, True, True, False])
    fig_b += "<h3>E5 has to bite in two directions, and it does</h3>"
    fig_b += table(["control series", "c^2", "bound / c^2", "measured", "must be", "verdict"],
                   rows_bc, [False, True, True, True, False, False])
    fig_b += (f'<p class="note">The population bound is exactly c^2 when sigma_u = 0 — '
              f'gamma_0^2 - 4*gamma_1^2 collapses to (s^2 - c^2)^2 there, so the square root is '
              f'exact and the two terms cancel {tag("DISIMPULKAN")}. The measured column carries '
              f'ordinary sampling error; what the control establishes is the SEPARATION between '
              f'the two cases ({ratio(b["controls"][0]["ratio"], 3)} against '
              f'{ratio(b["controls"][1]["ratio"], 3)}), which no implementation that confuses '
              f'them could produce.</p>')
    rail_b = f"""
<h4>Reading</h4>
<p class="concl">c is identified only up to [{bps(iv["c_lo"], 4)}, {bps(iv["c_hi"], 4)}] bps.
The planted {bps(b["c_true"])} bps is inside: {"yes" if b["contains_true_c"] else "NO"} {tag("DIUKUR")}.</p>
<p>A point estimate of c is a point chosen by an assumption. The Roll number is the point the
assumption lambda = 0 picks out; the E5 bound is the point sigma2_u = 0 picks out. Nothing in
(gamma_0, gamma_1) can choose between them, because both ends generate the same two moments.</p>
<p><strong>Roll is the geometric mean of c and c+lambda.</strong> gamma_1 = -c*(c+lambda), so
sqrt(-gamma_1) = sqrt(c*(c+lambda)) = {bps(b["geometric_mean"], 4)} bps against the measured
{bps(e1["c_hat"], 4)} bps {tag("DIUKUR")}. By AM-GM it sits between its two terms, so it
overstates the half-spread and understates the full spread — one number wrong in two directions
at once.</p>
<p>The interval costs nothing: both ends come from gamma_0 and gamma_1, which were computed
anyway. Reporting the point instead of the interval buys precision that is not there.</p>
<p><strong>Not calibrated to BTCUSDT.</strong> These planted values are the VERIFY §4 fixtures.
This repo's own measurement puts the median perp spread at one tick, 0.0157 bps {tag("DIUKUR")} —
around 400x tighter, and a regime where discretisation binds for the whole Roll family
(VERIFY §6). The interval shown here is a property of the estimator, not a claim about the
instrument.</p>"""

    # ---------------------------------------------------------------- panel C
    rows_c = []
    for r in c["rows"]:
        theta_s = "[" + ", ".join(f"{t:g}" for t in r["theta"]) + "]"
        correct_cell = sci(r["correct_order"])
        if r["at_noise_floor"]:
            correct_cell += '<br><span class="tag t-unverified">below noise floor</span>'
        rows_c.append([
            f'<code>{theta_s}</code><br><span class="tag">{esc(r["note"])}</span>',
            sci(r["true_w"]),
            correct_cell,
            sci(r["forced"]),
            f'{ratio(r["ratio_closed"])}<br><span class="tag">'
            f'measured {ratio(r["forced_ratio"])}</span>',
            f'{verdict_badge(r["forced_verdict"])} <span class="tag">forced</span><br>'
            f'{verdict_badge(r["gated_verdict"])} <span class="tag">gate on</span>',
        ])
    ctl = c["control"]
    fig_c = table(["MA(3) theta", "sigma2_w true<br>closed form",
                   "correct order<br>g0+2*(g1+g2+g3)", "E2 anyway<br>g0+2*g1, forced",
                   "ratio to truth<br>closed form", "verdict"],
                  rows_c, [False, True, True, True, True, False])
    fig_c += f"""
<p class="note">Closed form is the authority here: gamma_h for an MA(3) is exact, so the ratio
column labelled "closed form" has no sampling error at all. The measured column confirms it on
{N_PANEL:,} draws per pattern {tag("DIUKUR")}. Row 3's correct-order estimate is honestly useless —
with theta(1) = -0.05 the true sigma2_w is {sci(c["rows"][2]["true_w"])}, which sits below the
sampling noise of any estimator at this n. That is the point of the row: the wrong formula returns
a confident {sci(c["rows"][2]["forced"])} there.</p>
<h3>What the gate costs — the control this repo's rules require</h3>
<p class="note">A verifier has to be tested on cases known to PASS, not only on cases known to
fail (blindness class I). {ctl["n_seeds"]} independent draws of a <em>genuinely</em> MA(1) series,
the same generalized-Roll process as panel A, n = {ctl["n"]:,} each.</p>
<div class="stack">
  <div class="kv"><div class="k">gate refuses a true MA(1)</div>
    <div class="val">{ctl["rate"]:.1%}</div>
    <div class="cap">{ctl["rejects"]}/{ctl["n_seeds"]} seeds, against a nominal
    {ctl["nominal"]:.0%} +/- {ctl["binom_se"]:.1%} {tag("DIUKUR")}</div></div>
  <div class="kv"><div class="k">mean of Q</div>
    <div class="val">{ctl["q_mean"]:.2f} &plusmn; {ctl["q_mean_se"]:.2f}</div>
    <div class="cap">chi2(9) mean is 9.00 — the centring is right {tag("DIUKUR")}</div></div>
  <div class="kv"><div class="k">variance of Q</div>
    <div class="val">{ctl["q_var"]:.1f} &plusmn; {ctl["q_var_se"]:.1f}</div>
    <div class="cap">chi2(9) variance is 18.0; Bartlett predicts
    {ctl["pred"]["var"]:.1f} {tag("DISIMPULKAN")}</div></div>
</div>
<p class="note">The mechanism, in closed form: under an MA(1) the sample autocorrelations at
lags 2..10 are <em>correlated</em> — Bartlett gives adjacent correlation
2*rho_1/(1+2*rho_1^2) = {ctl["pred"]["corr_adjacent"]:.3f} at rho_1 = {ctl["rho1"]:.4f}, and
{ctl["pred"]["corr_lag2"]:.3f} two lags apart. Summing their squares as if they were independent
keeps the mean (tr(R) = 9) but inflates the variance to 2*tr(R^2) = {ctl["pred"]["var"]:.1f}.
Measured: {ctl["q_var"]:.1f} &plusmn; {ctl["q_var_se"]:.1f} {tag("DIUKUR")} — within
{abs(ctl["q_var"] - ctl["pred"]["var"]) / ctl["q_var_se"]:.1f} SE of the prediction and
{abs(ctl["q_var"] - ctl["pred"]["chi2_var"]) / ctl["q_var_se"]:.1f}
SE from chi2(9)'s {ctl["pred"]["chi2_var"]:.1f}. The rejection rate is the blunter and more direct measurement, and it is
{(ctl["rate"] - ctl["nominal"]) / ctl["binom_se"]:.1f} SE above nominal: the chi2(9) 95% point
{ctl["q_crit"]:.2f} sits too low, so the gate refuses about {ctl["rate"]:.0%} of the series it
should accept.</p>"""
    rail_c = f"""
<h4>Reading</h4>
<p class="concl">On a true MA(3), gamma_0 + 2*gamma_1 is out by
{ratio(c["rows"][0]["ratio_closed"], 2)}, {ratio(c["rows"][1]["ratio_closed"], 2)},
{ratio(c["rows"][2]["ratio_closed"], 1)} and {ratio(c["rows"][3]["ratio_closed"], 2)} — the last
one a negative variance.</p>
<p>The refusal is what makes that legible. Every one of these four returns ABSTAIN when the gate
is left on; the numbers above exist only because <code>force=True</code> switched it off, which
is a test affordance and never a production path.</p>
<p>Two failure modes, and only one of them is loud. The alternating pattern returns
{sci(c["rows"][3]["forced"])}, a negative variance, which cannot be missed. The all-negative
pattern returns a small, positive, plausible {sci(c["rows"][2]["forced"])} that is wrong by a
factor of {ratio(c["rows"][2]["ratio_closed"], 0)}. Nothing about that number looks wrong.</p>
<p class="concl">The gate's own error rate is {ctl["rate"]:.1%}, not the 5% it advertises.</p>
<p>The direction is safe — it refuses work it could have done, rather than passing work it
should not — and E4 and E9 need no gate at all, so nothing is stranded. But a 5% label on an
{ctl["rate"]:.0%} test is a claim without a checker, and it now has one.</p>"""

    # ---------------------------------------------------------------- panel D
    rows_d = []
    for r in d["rows"]:
        cells = [f'<code>{r["theta"]:+.4f}</code>' + (' <strong>&larr; golden</strong>'
                                                      if r["is_golden"] else ''),
                 sci(r["true_w"])]
        for key in ("correct", "no_square", "sum_phi", "sum_phi_sq"):
            cells.append(f'{ratio(r["ratio"][key])}<br><span class="tag">cf '
                         f'{ratio(r["closed"][key])}</span>')
        rows_d.append((r, cells))
    body_d = []
    for r, cells in rows_d:
        cls = ' class="hi"' if r["is_golden"] else ""
        tds = "".join(f'<td class="{"n" if i > 0 else ""}">{cv}</td>' for i, cv in enumerate(cells))
        body_d.append(f"<tr{cls}>{tds}</tr>")
    head_d = ("<th>theta</th><th class='n'>sigma2_w true</th>"
              "<th class='n'>s2e / phi(1)^2<br><span class='tag t-diukur'>CORRECT</span></th>"
              "<th class='n'>s2e / phi(1)<br><span class='tag'>square dropped</span></th>"
              "<th class='n'>s2e / sum(phi)<br><span class='tag'>both wrong</span></th>"
              "<th class='n'>s2e / sum(phi)^2<br><span class='tag'>phi(1) misread</span></th>")
    fig_d = (f'<div class="scroll"><table><thead><tr>{head_d}</tr></thead>'
             f'<tbody>{"".join(body_d)}</tbody></table></div>')
    gold_row = next(r for r in d["rows"] if r["is_golden"])
    fig_d += f"""
<p class="note">Each cell is the ratio to the planted sigma2_w: measured on top (mean of
{d["n_seeds"]} seeds, n = {N_PANEL:,}, AR(30)) {tag("DIUKUR")}, closed form below {tag("DISIMPULKAN")}.
<code>s2e</code> is the AR innovation variance sigma2_eps, and every column divides the same
<code>s2e</code> by a different reading of the same fitted coefficients — only the divisor changes.
The closed forms are 1, 1/(1+theta), 1/(theta*(1+theta)) and 1/theta^2 — they hold for any MA(1)
and are what the measurement is being checked against.</p>
<div class="stack">
  <div class="kv"><div class="k">the correct formula, across the sweep</div>
    <div class="val">{min(r["ratio"]["correct"] for r in d["rows"]):.3f}&ndash;{max(r["ratio"]["correct"] for r in d["rows"]):.3f}x</div>
    <div class="cap">AR(30) at n = {N_PANEL:,}; per-seed sd is about 1.7% {tag("DIUKUR")}</div></div>
  <div class="kv"><div class="k">square dropped</div>
    <div class="val">{min(r["closed"]["no_square"] for r in d["rows"]):.3f}&ndash;{max(r["closed"]["no_square"] for r in d["rows"]):.3f}x</div>
    <div class="cap">crosses 1.0 near theta = 0, so no calibration can rescue it {tag("DISIMPULKAN")}</div></div>
  <div class="kv"><div class="k">at theta = (sqrt(5)-1)/2</div>
    <div class="val">{gold_row["closed"]["sum_phi"]:.4f}x</div>
    <div class="cap">the doubly-wrong reading is EXACTLY right; measured
    {ratio(gold_row["ratio"]["sum_phi"])} {tag("DIUKUR")}</div></div>
</div>"""
    rail_d = f"""
<h4>Reading</h4>
<p class="concl">A formula that is wrong twice returns the right number at
theta = {d["golden"]:.6f}.</p>
<p>1/(theta*(1+theta)) equals 1 when theta^2 + theta - 1 = 0, whose invertible root is the
golden-ratio conjugate. At that point sigma2_eps/sum(phi) — which drops the square
<em>and</em> reads phi(1) as sum(phi) instead of 1 - sum(phi) — agrees with the truth to within
the sampling error of the correct formula.</p>
<p>This has no practical use. It has a rhetorical one, and it is the whole argument for positive
controls: <strong>"the number came out plausible" is evidence about nothing.</strong> A single-point
check at the wrong theta would have certified this implementation.</p>
<p>The rest of the map matters for a different reason. sigma2_eps/sum(phi) is negative for every
theta &lt; 0 here, which is the bounce-driven case, so the bug looks like it announces itself. It
does not reliably: VERIFY §5 measures it staying positive on 27.5% of seeds at theta = -0.01.
That is why <code>sigma2_w_ar</code> checks phi(1) &gt; 0 explicitly instead of waiting for a
negative number to appear.</p>"""

    # ---------------------------------------------------------------- panel E
    rw, mom = e["rw"], e["momentum"]
    rows_e = [
        ["pure random walk, sqrt(-gamma_1)", f'{rw["n_seeds"]} seeds &times; n = {rw["n"]:,}',
         f'{rw["rate"]:.1%}', f'50% &plusmn; {rw["binom_se"]:.1%}', verdict_badge("ABSTAIN")],
        ["momentum AR(1), rho = 0.35", f'1 series &times; n = {mom["n"]:,}',
         "100%", "100%", verdict_badge(mom["roll"]["verdict"])],
    ]
    fig_e = table(["negative control", "sample", "ABSTAIN rate", "expected", "verdict"],
                  rows_e, [False, False, True, True, False])
    fig_e += f"""
<div class="stack">
  <div class="kv"><div class="k">what the silent alternative would have printed</div>
    <div class="val">{rw["fabricated_median_spread_bps"]:.3f} bps</div>
    <div class="cap">median 2*sqrt(|gamma_1|) on the abstaining seeds; worst
    {rw["fabricated_max_spread_bps"]:.3f} bps. The true spread is zero {tag("DIUKUR")}</div></div>
  <div class="kv"><div class="k">momentum gamma_1</div>
    <div class="val">{sci(mom["roll"]["gamma_1"], 3)}</div>
    <div class="cap">positive, so sqrt(-gamma_1) has no real solution {tag("DIUKUR")}</div></div>
  <div class="kv"><div class="k">E4 slope on a random walk</div>
    <div class="val">{e["slope_t"]:.2f}</div>
    <div class="cap">|mean|/sd over {e["n_slope_seeds"]} seeds; the true slope is 0 {tag("DIUKUR")}</div></div>
</div>
<p class="note"><strong>The alternative to ABSTAIN is not a missing number, it is a wrong one.</strong>
Setting c = 0 on the abstaining seeds would report "no spread" on a series that has none — right
by accident, since the same reflex reports "no spread" whenever gamma_1 happens to come out
positive on a series that <em>does</em> have one. Taking sqrt(|gamma_1|) instead fabricates a
median spread of {rw["fabricated_median_spread_bps"]:.3f} bps out of a series with no spread at
all. Both are silent; neither would fail a test that only checks the output is a number.</p>
<p class="note">The estimators that do not need a sign stay honest on the same random walk:
E3 returns theta = {e["rw_wold"]["theta"]:+.5f} and sigma2_w/sigma2_eps =
{e["rw_wold"]["sigma2_w"] / e["rw_wold"]["sigma2_eps"]:.5f} against a true 1.0 {tag("DIUKUR")}, and
E4's intercept is {sci(e["intercept_mean"])} against the planted {sci(e["rw_true_w"])}. E4's slope
averages {sci(e["slope_mean"], 2)} with sd {sci(e["slope_sd"], 2)} across
{e["n_slope_seeds"]} seeds — indistinguishable from zero, and a reminder of why the module labels
it <code>slope_DIAGNOSTIC_ONLY</code> and refuses to derive c from it.</p>
<p class="note"><code>identified_interval_c</code> refuses the momentum series for the same reason
Roll does — {verdict_badge(mom["iv"]["verdict"])} <em>{esc(mom["iv"]["reason"])}</em>. E5 does not:
<code>pricing_error_lower_bound</code> returns {verdict_badge(mom["e5"]["verdict"])}
{sci(mom["e5"]["lower_bound"])} there. That is defensible arithmetic — the bound is on the Wold
pricing error, which exists for any covariance-stationary series and does not need a spread to be
defined — but on a trending series the number is not a half-spread and nothing should read it as
one. Three functions over the same two moments, two refusing and one answering, is worth knowing
before any of them is pointed at a tape.</p>
<p class="note">E5's own negative control passes cleanly: on the pure random walk the bound is
{sci(e["rw_e5"]["lower_bound"], 3)}, which is {e["rw_e5"]["lower_bound"] / e["rw_true_w"]:.1e}
of sigma_u^2 — zero to five decimal places, as it must be when there is no pricing error to
bound {tag("DIUKUR")}.</p>"""
    rail_e = f"""
<h4>Reading</h4>
<p class="concl">On a series with no spread, the Roll estimator refuses
{rw["rate"]:.1%} of the time and never reports a spread of zero.</p>
<p>Roughly half is the correct answer, not a shortfall: with c = 0 the sample gamma_1 is a
coin flip about zero, so half the seeds have no real sqrt(-gamma_1). RULE-EXTRACT-2 makes that
half a refusal with a reason attached.</p>
<p>The momentum control is the sharper one. Positive first-order autocovariance is a momentum
signature, and an estimator that returned sqrt(|gamma_1|) there would report a spread with the
wrong sign of evidence entirely — reading trend as friction.</p>
<p><strong>An abstention is a result.</strong> It is the only output here that separates "nothing
is there" from "nothing was measurable", which is blindness class B and the thing a silent zero
destroys.</p>"""

    # ---------------------------------------------------------------- panel F
    cum = f["irf"]["cumulative"]
    th = f["irf"]["theta"]
    rows_f = [[str(k), f"{th[k]:+.6f}", f"{cum[k]:.6f}",
               pct(cum[k] / f["planted_long_run"] - 1.0)]
              for k in (0, 1, 2, 3, 4, 5, 10, 20)]
    fig_f = svg_irf(f)
    fig_f += table(["horizon k", "MA coefficient theta_k", "cumulative response",
                    "vs planted long run"], rows_f, [True, True, True, True])
    fig_f += f"""
<div class="stack">
  <div class="kv"><div class="k">planted long run theta(1)</div>
    <div class="val">{f["planted_long_run"]:.6f}</div>
    <div class="cap">1 + theta from the population moments {tag("DISIMPULKAN")}</div></div>
  <div class="kv"><div class="k">cumulative at k = 20</div>
    <div class="val">{f["horizon_20"]:.6f}</div>
    <div class="cap">{pct(f["horizon_20"] / f["planted_long_run"] - 1.0)} vs planted {tag("DIUKUR")}</div></div>
  <div class="kv"><div class="k">1 / phi(1), the AR limit</div>
    <div class="val">{f["ar_limit"]:.6f}</div>
    <div class="cap">{pct(f["ar_limit"] / f["planted_long_run"] - 1.0)} vs planted {tag("DIUKUR")}</div></div>
</div>
<p class="note">Two routes to the same limit, from the same fit: summing the MA coefficients to
k = 20 gives {f["horizon_20"]:.6f}, and 1/phi(1) — the limit of that sum — gives
{f["ar_limit"]:.6f}. They differ by {pct(f["horizon_20"] / f["ar_limit"] - 1.0)}, which is the
truncation left in a 20-step sum, not disagreement.</p>"""
    rail_f = f"""
<h4>Reading</h4>
<p class="concl">The price level moves {f["impact_ratio"]:.2f}x its permanent displacement on
impact, then gives most of it back.</p>
<p>This is the LEVEL response, not the response of the price change: the cumulative sum
of the MA coefficients, which is the object RULE-EXTRACT-3 points at. It starts at 1.00 by
construction and settles at theta(1) = {f["planted_long_run"]:.4f} {tag("DISIMPULKAN")}. The gap
between the two is the transient part — the half-spread paid and then reversed.</p>
<p><strong>The whole transient is one step, and that is a property of the fixture, not a finding.</strong>
The planted process is MA(1), so all of the reversal lands at k = 1 and the path is flat afterwards
— which is exactly why the flat part is a useful check: the AR(30) fit recovers coefficients that
are near zero past lag 1 without being told the order. Real order flow is autocorrelated, so a
real path would decay over many trades instead.</p>
<p><strong>What this is not.</strong> The AR here is univariate, fitted on Delta p alone, so
the response is to a Wold innovation and not to a trade. Attributing any part of it to order flow
needs the price-trade VAR (E6), which is not implemented — see the footer. Reading a single
coefficient of any such VAR as lambda is forbidden by RULE-EXTRACT-3 in the first place; the
cumulative path is what the rule points to instead.</p>"""

    # ---------------------------------------------------------------- header
    roster = table(
        ["id", "estimator", "function", "status"],
        [["E1", "Roll spread estimator", "<code>roll</code>", verdict_badge("OK")],
         ["E2", "gamma_0 + 2*gamma_1, gated", "<code>sigma2_w_ma1</code>", verdict_badge("OK")],
         ["E3", "Wold theta(1)^2 sigma2_eps", "<code>sigma2_w_wold</code>", verdict_badge("OK")],
         ["E4", "long-horizon variance ratio", "<code>sigma2_w_variance_ratio</code>",
          verdict_badge("OK")],
         ["E5", "pricing-error lower bound", "<code>pricing_error_lower_bound</code>",
          verdict_badge("OK")],
         ["E8", "GMM / MA(q) moment estimator", "&mdash;",
          '<span class="tag t-diasumsikan">deliberately not written</span>'],
         ["E9", "sigma2_eps / phi(1)^2, AR(K)", "<code>sigma2_w_ar</code>", verdict_badge("OK")],
         ["E10", "cumulative impulse response", "<code>cumulative_impulse_response</code>",
          verdict_badge("OK")],
         ["E11", "subsampling standard error", "<code>subsample_estimate</code>",
          verdict_badge("OK")],
         ["&mdash;", "MA(1) order gate", "<code>ma1_order_gate</code>", verdict_badge("OK")],
         ["&mdash;", "identified interval for c", "<code>identified_interval_c</code>",
          verdict_badge("OK")],
         ["E6", "price-trade VAR, lambda, R^2_w", "&mdash;",
          '<span class="v v-fail">NOT IMPLEMENTED</span>'],
         ["E7", "VECM / information share", "&mdash;",
          '<span class="v v-fail">NOT IMPLEMENTED</span>']],
        [False, False, False, False])

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hasbrouck estimators on simulated series — btc-quant</title>
<meta name="description" content="Six panels checking btcquant/hasbrouck.py against planted truth on simulated series. No real market data.">
<style>{CSS}</style>
</head>
<body>
<div class="wrap">

<div class="topbar">
  <div>
    <h1>Microstructure estimators, checked against planted truth</h1>
    <p class="sub"><code>btcquant/hasbrouck.py</code> &middot; six panels, six simulated worlds
    &middot; generated by <code>scripts/hasbrouck_demo.py</code></p>
  </div>
  <div class="themer" role="group" aria-label="colour theme">
    <button type="button" data-set-theme="auto" aria-pressed="true">auto</button>
    <button type="button" data-set-theme="light" aria-pressed="false">light</button>
    <button type="button" data-set-theme="dark" aria-pressed="false">dark</button>
  </div>
</div>

<div class="banner">
  <p><strong>EVERY SERIES ON THIS PAGE IS SIMULATED.</strong> No real market data was read to
  build it: no <code>data/</code>, no <code>hf://</code>, no tick store, no LockBox. Each panel
  states the truth that was planted before the estimator ran, so agreement can be judged rather
  than taken. This page adds <strong>zero</strong> to the look counter.</p>
  <p class="note" style="margin:0">Model, in log price (1e-4 = 1 bps), t in trade time:</p>
  <pre class="eq">m_t  = m_&#123;t-1&#125; + w_t          efficient price, a random walk
w_t  = lambda*q_t + u_t       trades carry information
p_t  = m_t + c*q_t            observed transaction price
dp_t = -c*q_&#123;t-1&#125; + (c+lambda)*q_t + u_t

gamma_0 = c^2 + s^2 + su^2,  gamma_1 = -c*s,  gamma_k = 0 (k &gt;= 2),  s := c + lambda
sigma2_w = lambda^2 + su^2 = gamma_0 + 2*gamma_1     &lt;- identified; c, lambda, su^2 are NOT</pre>
  <p style="margin:0">Planted for panels A, B and F: c = {sci(C_TRUE)} ({bps(C_TRUE)} bps),
  lambda = {sci(LAM_TRUE)}, sigma_u = {sci(SU_TRUE)}, n = {N_MAIN:,}, seed {SEED}
  {tag("DIASUMSIKAN")}. Deterministic: same seed, same sample sizes, no clock in the output, so
  re-running the generator reproduces this file byte for byte.</p>
  <div class="legend">
    <span>{tag("DIUKUR")} measured on the simulated series</span>
    <span>{tag("DISIMPULKAN")} derived from the planted parameters</span>
    <span>{tag("DIASUMSIKAN")} chosen by the script</span>
    <span>{tag("UNVERIFIED")} claimed, no checker on this machine</span>
  </div>
</div>

<section class="panel">
  <h2><span class="pid">&#8226;</span>What is on the bench</h2>
  {roster}
  <p class="note">Every function in the module's <code>__all__</code> is exercised on this page.
  E6 and E7 are not implemented at all; E8 is deliberately not written — a hand-rolled MA(q) root
  finder has 2^q admissible parameter sets and only one invertible, and the AR route (E9) reaches
  the same quantity without needing the MA order.</p>
</section>

{panel("a", "Four estimators, three independent routes",
       f'One generalized-Roll series, n = {N_MAIN:,}. The planted sigma2_w = lambda^2 + sigma_u^2 = '
       f'{sci(LAM_TRUE ** 2 + SU_TRUE ** 2)} {tag("DISIMPULKAN")}. No estimator is told any of it.',
       fig_a, rail_a)}

{panel("b", "The identified interval",
       f'Same series. Planted c = {sci(C_TRUE)}, lambda = {sci(LAM_TRUE)}, so c+lambda = '
       f'{sci(C_TRUE + LAM_TRUE)} and the geometric mean sqrt(c*(c+lambda)) = '
       f'{sci(b["geometric_mean"])} {tag("DISIMPULKAN")}.',
       fig_b, rail_b)}

{panel("c", "Why the gate exists",
       f'Four true MA(3) processes, sigma_eps = {sci(SIGMA_EPS)}, n = {N_PANEL:,} each. '
       f'sigma2_w = theta(1)^2*sigma2_eps is known exactly for all four {tag("DISIMPULKAN")}; '
       f'the MA(1) premise E2 rests on is false for all four by construction.',
       fig_c, rail_c)}

{panel("d", "The AR formula failure map",
       f'MA(1) processes across theta, sigma_eps = {sci(SIGMA_EPS)}, n = {N_PANEL:,}, '
       f'{AR_SEEDS} seeds per point. Planted sigma2_w = (1+theta)^2*sigma2_eps {tag("DISIMPULKAN")}.',
       fig_d, rail_d)}

{panel("e", "Negative controls",
       f'A pure random walk (c = 0, lambda = 0, sigma_u = {sci(SU_TRUE)}) and a momentum series '
       f'(dp_t = 0.35*dp_&#123;t-1&#125; + e_t). Neither contains a spread. The correct output is a '
       f'refusal {tag("DIASUMSIKAN")}.',
       fig_e, rail_e)}

{panel("f", "Impulse response",
       f'The panel-A series again. Planted long-run level response theta(1) = 1 + theta = '
       f'{f["planted_long_run"]:.6f}, from the population (gamma_0, gamma_1) of the planted '
       f'parameters {tag("DISIMPULKAN")}.',
       fig_f, rail_f)}

<footer id="g">
  <h2>G. What this page is, and what it is not</h2>
  <p><strong>Everything above is simulated.</strong> Every series was generated in-process from a
  seeded RNG with known parameters. No real market data was touched: this script has no data
  access, reads no partition, opens no database, and makes no network call. It adds
  <strong>zero</strong> to the look counter, because a look is a specification evaluated against
  real data and nothing here has seen any.</p>
  <p><strong>RULE-EXTRACT-5 stands.</strong> No estimator in this module may touch a real
  partition before a pre-registration declares the whole specification set with an explicit
  <code>N_trials</code> cap. The choices that would need declaring are already known and are not
  cosmetic: the AR truncation order K, the horizon grid and k_min for E4, the time basis (event
  vs calendar), the treatment of funding windows at 00:00/08:00/16:00 UTC, the subsample unit,
  the handling of days carrying a <code>recorded-damage</code> entry, and the sample window.
  Choosing any of them after seeing a result is an unrecorded look.</p>
  <h3>Not implemented</h3>
  <ul>
    <li><strong>E6 — the price-trade VAR</strong> (lambda from the cumulative impulse response,
      the variance decomposition, R^2_w). Nothing on this page estimates lambda. Panel F's
      response is univariate, to a Wold innovation, and cannot attribute anything to order flow.</li>
    <li><strong>E7 — VECM and information share.</strong> No cointegration, no error correction,
      no information-share bounds. The perp-vs-spot question this repo cares about is not touched.</li>
    <li><strong>E8 — the GMM / MA(q) moment estimator</strong>, deliberately: 2^q parameter sets
      share the same autocovariances and only one is invertible, and E9 reaches sigma2_w without
      knowing the MA order.</li>
    <li><strong>RULE-EXTRACT-9's required test</strong> — that sum_j theta_j*Omega*theta_j' and
      theta(1)*Omega*theta(1)' are different numbers — cannot exist until E6 does, because both
      sides need a VAR.</li>
    <li><strong>The book sections never extracted at all:</strong> §5–§6 (sequential and strategic
      trade models, Glosten-Milgrom and Kyle), §8.e–§8.h (smoothing and filtering approaches to
      sigma2_s), §10–§11 (inventory control; invertibility and Wold revisited), §14 (structural
      models: Glosten-Harris, MRR, Huang-Stoll), §15 (PIN), §16 (what the asymmetric-information
      measures actually measure), §18–§21 (limit orders, uncertain execution, dynamic equilibrium),
      §22 (asset pricing with transaction costs, Amihud-style liquidity measures), and the 2003
      US-equity market-structure appendix.</li>
  </ul>
  <h3>Two caveats that outlive this page</h3>
  <ul>
    <li><strong>Attribution is unverified.</strong> The source PDF is not on this machine, so every
    section-number citation carried by the extraction documents is {tag("UNVERIFIED")}. The
    mathematics on this page stands on its own derivations and these simulations; the attribution
    does not stand at all until the source can be read.</li>
    <li><strong>Simulation proves the formula, not its application.</strong> Every series here has
    iid trade direction and homoskedastic Gaussian innovations. Real order flow has neither —
    direction is autocorrelated (order splitting) and volatility varies within the day. What these
    panels establish is that the algebra and the implementation are right. Whether the assumptions
    hold on any real tape is a separate question, and answering it costs a look.</li>
  </ul>
  <p class="note">Regenerate: <code>make hasbrouck-demo</code>. Sources:
  <code>btcquant/hasbrouck.py</code>, <code>docs/VERIFY-hasbrouck-extraction.md</code> (authoritative
  where it disagrees with the extraction documents), <code>docs/EXTRACT-hasbrouck-001.md</code>,
  <code>docs/EXTRACT-hasbrouck-s9-s12.md</code>.</p>
</footer>

</div>
<script>{JS}</script>
</body>
</html>
"""


# --------------------------------------------------------------------------- #
# Entry point                                                                  #
# --------------------------------------------------------------------------- #
def main() -> int:
    print("hasbrouck-demo — SIMULATION ONLY (RULE-EXTRACT-5: no data/, no hf://, no LockBox)")
    print(f"  master seed {SEED}; planted c={C_TRUE:.1e} lambda={LAM_TRUE:.1e} "
          f"sigma_u={SU_TRUE:.1e}\n")

    dp_main = generalized_roll(N_MAIN, C_TRUE, LAM_TRUE, SU_TRUE,
                               np.random.default_rng(SEED))
    a = panel_a(dp_main)
    b = panel_b(dp_main)
    c = panel_c()
    d = panel_d()
    e = panel_e()
    f = panel_f(dp_main)

    print("  A four estimators / three routes  planted " + sci(a["true_w"]))
    for r in a["routes"]:
        print(f"      {r['tag']:>3} {r['res'].get('verdict', '?'):16s} {sci(r['value'])} "
              f"{pct(r['rel_err'])}")
    print(f"      E2 vs E3 gap {a['e2_e3_gap']:.3e} — an identity, not an agreement "
          f"(worst over 20k moment pairs: {a['e2_e3_identity_worst']:.1e})")
    print(f"  B identified interval       c in [{b['iv']['c_lo']:.4e}, {b['iv']['c_hi']:.4e}], "
          f"planted {C_TRUE:.4e} inside: {b['contains_true_c']}")
    print(f"  C gate                      forced ratios " +
          ", ".join(ratio(r["ratio_closed"]) for r in c["rows"]))
    print(f"      gate refuses a TRUE MA(1) on {c['control']['rate']:.1%} of "
          f"{c['control']['n_seeds']} seeds (nominal 5%); var(Q) measured "
          f"{c['control']['q_var']:.1f} vs Bartlett prediction {c['control']['pred']['var']:.1f}")
    print(f"  D AR failure map            correct formula "
          f"{min(r['ratio']['correct'] for r in d['rows']):.3f}-"
          f"{max(r['ratio']['correct'] for r in d['rows']):.3f}x; doubly-wrong reading at "
          f"theta={GOLDEN:.6f} is exactly 1.0 in closed form")
    print(f"  E negative controls         random walk ABSTAIN {e['rw']['rate']:.1%} "
          f"({e['rw']['abstains']}/{e['rw']['n_seeds']}); momentum "
          f"{e['momentum']['roll']['verdict']}")
    print(f"  F impulse response          planted theta(1) {f['planted_long_run']:.6f}, "
          f"cumulative(20) {f['horizon_20']:.6f}, 1/phi(1) {f['ar_limit']:.6f}")

    html_text = render(a, b, c, d, e, f)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html_text, encoding="utf-8")
    size = OUT.stat().st_size
    print(f"\n  wrote {OUT.relative_to(REPO)}  ({size:,} bytes)")
    print("  make dash   ->  http://127.0.0.1:8787/hasbrouck.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
