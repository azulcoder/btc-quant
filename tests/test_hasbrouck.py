"""test_hasbrouck.py — the control suite for `btcquant/hasbrouck.py`.

Every estimator in that module is exercised here twice: once on a series where the
answer was planted and must be recovered, and once on a series where the honest answer
is "I cannot tell you" and the estimator must say so. A verifier tested only on cases
known to FAIL has measured its recall and never its precision — class I in this repo's
taxonomy (`STRATEGY.md`, grep `The verifier cries wolf`) — and bad precision in a
checker destroys correct work rather than merely adding noise. The positive controls
here are therefore not decoration; one of them (11) is the reason a real defect in
`ma1_order_gate` was caught.

RULE-EXTRACT-5 — nothing here touches real data
-----------------------------------------------
Every series in this file is generated in-process by `numpy.random.default_rng` (PCG64)
from a literal seed. No `data/`, no `hf://`, no tick store, no LockBox, no network. This
file therefore adds zero to the look counter: a simulation with controls is 0 looks,
a real partition is not and would need a PREREG first.

Conventions
-----------
* `dp` is always a price-CHANGE series (`Delta p_t`), never a level. Units are fractions
  of price (a `c` of 4.0e-04 is 4 bps of half-spread), chosen so the numbers look like
  the ones in `docs/VERIFY-hasbrouck-extraction.md` and stay far from float limits.
* The planted truth is written down in CLOSED FORM before it is estimated, from
  `gamma_0 = c^2 + s^2 + su^2`, `gamma_1 = -c*s`, `sigma2_w = lambda^2 + su^2`,
  `s = c + lambda`. The simulation is checked against the algebra, never the reverse.
* Sizes are n = 1e5..4e5 per series. That is deliberate: the extraction documents ran at
  4e6, which is 10x the runtime for a tolerance improvement this file does not need.
* Every tolerance below was MEASURED on the seeds it is asserted against, then given
  slack; none was guessed and none was widened to make a red test go green. Numbers in
  comments carry `[DIUKUR]` when they came out of a run.
* Seeds are fixed, so the "rates" asserted here (abstain rate, false-rejection rate) are
  deterministic at runtime, not sampled. The bands are wide anyway, because the band is
  a claim about what a NEW seed set would give — that part is `[DISIMPULKAN]`.

Two things this suite found, which are reported rather than silently encoded
---------------------------------------------------------------------------
1. E2 and E3 are not two routes to `sigma2_w`; on an MA(1) fit they are one estimator
   written twice, and their agreement is an algebraic identity that carries no
   validation information. Proof and test: `test_e2_and_e3_are_algebraically_identical`.
2. The `phi_one <= 0` hard-fail inside `sigma2_w_ar` cannot fire through its own
   Yule-Walker path — YW on a positive-semidefinite sample autocovariance always returns
   a causal AR, and a causal `phi(z)` has `phi(1) > 0`. The guard is correct and worth
   keeping, but it is unreachable, so it is controlled here by synthesis rather than by
   a fixture. See `test_e9_phi_one_guard_is_unreachable_through_yule_walker`.

Runtime: the whole file is a few seconds; nothing here needs a marker or a skip.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from btcquant import hasbrouck as hb


# --------------------------------------------------------------------------- #
# Simulators — the only source of data in this file                            #
# --------------------------------------------------------------------------- #
def generalized_roll(n: int, c: float, lam: float, sigma_u: float, seed: int) -> np.ndarray:
    """Generalized-Roll price changes: `dp_t = -c*q_{t-1} + (c+lambda)*q_t + u_t`.

    `q_t` iid +/-1 with equal probability, `u_t` iid `N(0, sigma_u^2)` and independent of
    `q`. Those are exactly the assumptions the moments are derived under, which is what
    makes this a positive control and not a demonstration.
    """
    rng = np.random.default_rng(seed)
    q = rng.choice((-1.0, 1.0), size=n + 1)
    u = rng.normal(0.0, sigma_u, size=n + 1) if sigma_u > 0 else np.zeros(n + 1)
    return -c * q[:-1] + (c + lam) * q[1:] + u[1:]


def gaussian_ma(n: int, thetas, sigma_eps: float, seed: int) -> np.ndarray:
    """A textbook MA(q): `x_t = eps_t + sum_i theta_i*eps_{t-i}`, `eps` iid Gaussian.

    Distinct from `generalized_roll` on purpose. The Bartlett variance the order gate
    uses assumes a linear process in iid innovations; this is that process, so it is the
    right null for the gate's false-rejection rate.
    """
    rng = np.random.default_rng(seed)
    q = len(thetas)
    e = rng.normal(0.0, sigma_eps, size=n + q)
    x = e[q:].copy()
    for i, th in enumerate(thetas, start=1):
        x = x + th * e[q - i: q - i + n]
    return x


def ar1_series(n: int, phi: float, sigma: float, seed: int) -> np.ndarray:
    """AR(1) price changes — the momentum shape, where `gamma_1` is POSITIVE."""
    rng = np.random.default_rng(seed)
    e = rng.normal(0.0, sigma, size=n)
    x = np.empty(n)
    x[0] = e[0]
    for t in range(1, n):
        x[t] = phi * x[t - 1] + e[t]
    return x


# --------------------------------------------------------------------------- #
# The reference fixture, in closed form before anything is estimated           #
# --------------------------------------------------------------------------- #
C_REF, LAM_REF, SU_REF = 4.0e-4, 3.0e-4, 3.0e-4
N_REF, SEED_REF = 400_000, 5

S_REF = C_REF + LAM_REF                                   # full half-spread c + lambda
GAMMA0_REF = C_REF**2 + S_REF**2 + SU_REF**2              # 7.40e-07
GAMMA1_REF = -C_REF * S_REF                               # -2.80e-07
SIGMA2W_REF = LAM_REF**2 + SU_REF**2                      # 1.80e-07 == gamma_0 + 2*gamma_1


def reference_series() -> np.ndarray:
    return generalized_roll(N_REF, C_REF, LAM_REF, SU_REF, SEED_REF)


# =========================================================================== #
# POSITIVE CONTROLS — recover a planted truth                                 #
# =========================================================================== #
def test_generalized_roll_moments_match_closed_form():
    """1. `gamma_0`, `gamma_1`, and `gamma_k = 0` for k >= 2.

    `gamma_k = 0` above lag 1 is the model's signature — it is what separates this
    process from any other dynamics with the same variance, and E2 is void without it.
    """
    g = hb.autocovariances(reference_series(), 8)

    assert g[0] == pytest.approx(GAMMA0_REF, rel=0.01)     # [DIUKUR] -0.07% at this seed
    assert g[1] == pytest.approx(GAMMA1_REF, rel=0.01)     # [DIUKUR] -0.10%
    assert g[1] < 0                                        # the spread signature itself

    # k >= 2 must be zero, so the scale to judge it against is gamma_0, not gamma_k.
    worst = float(np.max(np.abs(g[2:])) / g[0])
    assert worst < 5e-3, f"gamma_k/gamma_0 = {worst:.4f} at some k >= 2; MA(1) premise broken"
    # [DIUKUR] worst = 0.0021 at seed 5, n = 4e5; sampling SE of rho_k is 1/sqrt(n) = 0.0016.

    # sigma2_w is identified even though {c, lambda, sigma2_u} are not.
    assert g[0] + 2 * g[1] == pytest.approx(SIGMA2W_REF, rel=0.01)


def test_pure_roll_special_case_reduces_to_sigma2_u():
    """1b. Cross-check demanded by EXTRACT-001 §E2: at lambda = 0 the same algebra must
    collapse to the pure Roll model, `gamma_0 = 2c^2 + su^2`, `gamma_1 = -c^2`, and
    `gamma_0 + 2*gamma_1 = su^2`. If it does not, the generalization is not one.
    """
    c, su = 4.0e-4, 3.0e-4
    dp = generalized_roll(300_000, c, 0.0, su, seed=17)
    g = hb.autocovariances(dp, 4)

    assert g[0] == pytest.approx(2 * c**2 + su**2, rel=0.01)
    assert g[1] == pytest.approx(-c**2, rel=0.02)
    assert g[0] + 2 * g[1] == pytest.approx(su**2, rel=0.05)   # here it really IS sigma2_u
    # And Roll is exact here: c_hat = sqrt(c*s) with s = c, so the geometric mean collapses.
    assert hb.roll(dp)["c_hat"] == pytest.approx(c, rel=0.02)


def test_four_routes_to_sigma2_w_recover_the_planted_value_and_agree():
    """2. E2, E3 (Wold), E4 (variance ratio) and E9 (AR) on ONE series.

    Each must land on the planted `lambda^2 + sigma2_u`, and they must land on each
    other. Read the tolerances as a ranking: E2/E3 are two moments and are the tightest,
    E9 pays for a 30-lag Yule-Walker fit, E4 pays for extrapolating an intercept from
    overlapping long-horizon windows.

    Note what this does NOT prove — see `test_e2_and_e3_are_algebraically_identical`.
    Only three of these four are distinct instruments.
    """
    dp = reference_series()
    e2 = hb.sigma2_w_ma1(dp)
    e3 = hb.sigma2_w_wold(dp)
    e4 = hb.sigma2_w_variance_ratio(dp)
    e9 = hb.sigma2_w_ar(dp, K=30)

    assert e2["verdict"] == "OK" and e2["gate"]["passed"]   # genuinely MA(1), gate agrees
    assert e3["verdict"] == "OK"
    assert e4["verdict"] in ("OK", "OK_WITH_WARNING")
    assert e9["verdict"] == "OK"

    assert e2["sigma2_w"] == pytest.approx(SIGMA2W_REF, rel=0.005)   # [DIUKUR] -0.002%
    assert e3["sigma2_w"] == pytest.approx(SIGMA2W_REF, rel=0.005)   # [DIUKUR] -0.002%
    assert e4["sigma2_w"] == pytest.approx(SIGMA2W_REF, rel=0.02)    # [DIUKUR] -0.45%
    assert e9["sigma2_w"] == pytest.approx(SIGMA2W_REF, rel=0.02)    # [DIUKUR] -0.29%

    # ...and against each other, which is the part that would catch a shared-scale bug.
    vals = {"E2": e2["sigma2_w"], "E3": e3["sigma2_w"],
            "E4": e4["sigma2_w"], "E9": e9["sigma2_w"]}
    for a, va in vals.items():
        for b, vb in vals.items():
            assert va == pytest.approx(vb, rel=0.01), f"{a} and {b} disagree: {va:.6e} vs {vb:.6e}"
    # [DIUKUR] the widest pairwise gap at this seed is E2 vs E4 = +0.45%.


def test_four_routes_stay_within_their_stated_bands_across_seeds():
    """2b. One seed is an anecdote. Eight seeds, same n, gives the band each route
    actually lives in — which is the number to quote when one of them is later used on a
    real partition, and the reason E4 needs a subsampling SE (E11) rather than one print.

    E2 runs with `force=True` here ON PURPOSE: at seed 4 the order gate false-rejects a
    series that genuinely is MA(1) (that is the type-I rate measured in
    `test_ma1_order_gate_passes_on_a_true_ma1_series`), and this test is about E2's
    numerical error, not about the gate.
    """
    worst = {"E2": 0.0, "E3": 0.0, "E4": 0.0, "E9": 0.0}
    for seed in range(8):
        dp = generalized_roll(N_REF, C_REF, LAM_REF, SU_REF, seed)
        got = {
            "E2": hb.sigma2_w_ma1(dp, force=True)["sigma2_w"],
            "E3": hb.sigma2_w_wold(dp)["sigma2_w"],
            "E4": hb.sigma2_w_variance_ratio(dp)["sigma2_w"],
            "E9": hb.sigma2_w_ar(dp, K=30)["sigma2_w"],
        }
        for k, v in got.items():
            worst[k] = max(worst[k], abs(v / SIGMA2W_REF - 1.0))

    # [DIUKUR] seeds 0..7, n = 4e5: E2 1.75%, E3 1.75%, E4 4.01%, E9 3.39%.
    assert worst["E2"] < 0.03, worst
    assert worst["E3"] < 0.03, worst
    assert worst["E4"] < 0.06, worst
    assert worst["E9"] < 0.05, worst
    # E4 is the loosest of the three instruments at this n. That is a property of the
    # estimator, not of the seed: its intercept is an extrapolation to 1/k -> 0.
    assert worst["E4"] > worst["E2"]


def test_e2_and_e3_are_algebraically_identical():
    """2c. FINDING, encoded so nobody reads their agreement as cross-validation.

    On an MA(1) fit E3 is E2 rearranged, not a second opinion. With
    `sigma2_eps = (gamma_0 + D)/2` and `theta = gamma_1/sigma2_eps`, the two roots satisfy
    `sigma2_inv * sigma2_non = gamma_1^2`, so

        (1+theta)^2 * sigma2_eps = sigma2_eps + 2*gamma_1 + gamma_1^2/sigma2_eps
                                 = sigma2_inv + sigma2_non + 2*gamma_1
                                 = gamma_0 + 2*gamma_1

    identically. [DIUKUR] the difference is exactly 0.0 on the reference fixture and one
    ULP on the second — float rounding from a different evaluation order, fourteen orders
    of magnitude below any sampling tolerance. That is a tautology, not an agreement.
    EXTRACT-001 §6 lists E2 and E3 as mutually validating; on MA(1) they cannot be, and
    the only genuine cross-checks of `sigma2_w` in this module are E4 and E9.
    """
    dp = reference_series()
    e2 = hb.sigma2_w_ma1(dp, force=True)["sigma2_w"]
    e3 = hb.sigma2_w_wold(dp)["sigma2_w"]
    assert e3 - e2 == 0.0, f"expected bit-identical, got {e3 - e2:.3e}"

    # Same identity on an unrelated fixture, so it is not a coincidence of one seed.
    # Tolerance here is a few ULP, NOT a statistical band: 1e-15 relative is ~1e13 times
    # tighter than the 1% the same quantity gets tested at in the four-routes control.
    other = gaussian_ma(200_000, [-0.4], 1e-4, seed=23)
    o2 = hb.sigma2_w_ma1(other, force=True)["sigma2_w"]
    o3 = hb.sigma2_w_wold(other)["sigma2_w"]
    assert o3 == pytest.approx(o2, rel=1e-15), f"drifted beyond rounding: {o3 - o2:.3e}"


def test_roll_sigma2_u_key_is_sigma2_w_under_the_generalized_model():
    """2d. FINDING (naming hazard, low severity but silent).

    `roll()` returns `gamma_0 + 2*gamma_1` under the key `sigma2_u`. That name is right
    only in the pure Roll model. Under the generalized model the same expression is
    `lambda^2 + sigma2_u` — the module's own E2 — so on a series with adverse selection
    the key overstates the public-information variance by exactly `lambda^2`, with no
    flag. On this fixture lambda = sigma_u, so it is out by a factor of 2.
    """
    dp = reference_series()
    assert hb.roll(dp)["sigma2_u"] == hb.sigma2_w_ma1(dp, force=True)["sigma2_w"]   # same number
    assert hb.roll(dp)["sigma2_u"] == pytest.approx(2.0 * SU_REF**2, rel=0.02)      # not su^2


def test_e3_selects_the_invertible_root():
    """3. Two MA(1) representations exist; only `|theta| < 1` is admissible.

    The pair satisfies `theta_a * theta_b = 1` exactly (they are roots of the same
    quadratic), and the non-invertible one makes the `eps_t` recursion diverge, so a
    silent pick of the wrong root would still produce a plausible number.
    """
    dp = reference_series()
    out = hb.sigma2_w_wold(dp)
    g1 = float(hb.autocovariances(dp, 2)[1])

    assert out["verdict"] == "OK"
    assert out["root_product"] == pytest.approx(1.0, rel=1e-12)      # [DIUKUR] 1.0 + 4e-16
    assert abs(out["theta"]) < 1.0                                   # [DIUKUR] -0.4575
    assert abs(out["theta_noninvertible"]) > 1.0                     # [DIUKUR] -2.1860
    assert out["theta"] < 0                                          # gamma_1 < 0 forces the sign

    # The selection rule itself: the two innovation variances multiply to gamma_1^2, and
    # the invertible root is the LARGER one, which must exceed |gamma_1|.
    sigma2_noninvertible = g1**2 / out["sigma2_eps"]
    assert out["sigma2_eps"] > sigma2_noninvertible
    assert out["sigma2_eps"] > abs(g1)
    assert out["theta_noninvertible"] == pytest.approx(g1 / sigma2_noninvertible, rel=1e-12)


def test_e5_bound_is_exact_under_private_info_and_understates_under_public():
    """4. Both directions of the E5 bound, in one test.

    With `sigma2_u = 0` (exclusively private information) the pricing error is exactly
    `c*q_t`, so `Var(s_t) = c^2`, and the bound must MEET it. With `lambda = 0`
    (exclusively public information) the true `Var(s_t)` is still `c^2` but the bound
    must fall strictly BELOW it. An implementation that cannot separate these two is
    reporting a lower bound as a point estimate.
    """
    c, su = 4.0e-4, 3.0e-4
    private = generalized_roll(300_000, c, 3.0e-4, 0.0, seed=11)     # sigma2_u = 0
    public = generalized_roll(300_000, c, 0.0, su, seed=12)          # lambda = 0

    lb_priv = hb.pricing_error_lower_bound(private)
    lb_pub = hb.pricing_error_lower_bound(public)
    assert lb_priv["verdict"] == "OK" and lb_pub["verdict"] == "OK"

    assert lb_priv["lower_bound"] == pytest.approx(c**2, rel=0.02)   # [DIUKUR] ratio 1.0067
    # Closed form for lambda = 0: 0.5*(2c^2 + su^2 - su*sqrt(4c^2 + su^2)) = 0.4803 * c^2.
    exact_public = 0.5 * (2 * c**2 + su**2 - su * math.sqrt(4 * c**2 + su**2))
    assert exact_public / c**2 == pytest.approx(0.4803, rel=1e-3)    # the algebra, first
    assert lb_pub["lower_bound"] == pytest.approx(exact_public, rel=0.03)   # [DIUKUR] 0.4811
    assert lb_pub["lower_bound"] < 0.9 * c**2, "the bound must UNDERSTATE when lambda = 0"

    assert "LOWER BOUND" in lb_priv["label"]      # the qualifier travels with the number


def test_identified_interval_endpoints_are_the_e5_bound_and_roll():
    """5. The interval, its two endpoints, and the geometric-mean identity.

    `[E5 bound, Roll]` is one interval seen from two ends, not two estimators with two
    caveats (VERIFY §4, a proposition owned by this repo). The endpoints must be the
    SAME numbers those two functions return — asserted to machine precision, since all
    three read the same `(gamma_0, gamma_1)`.
    """
    dp = reference_series()
    iv = hb.identified_interval_c(dp)
    e5 = hb.pricing_error_lower_bound(dp)
    e1 = hb.roll(dp)
    assert iv["verdict"] == "OK" and e1["verdict"] == "OK"

    assert iv["c2_lo"] == e5["lower_bound"]        # lower end IS the E5 bound
    assert iv["c_hi"] == e1["c_hat"]               # upper end IS the Roll estimate
    assert iv["c_lo"] <= C_REF <= iv["c_hi"], (iv["c_lo"], C_REF, iv["c_hi"])
    # [DIUKUR] seed 5, n = 4e5: [3.577e-04, 5.289e-04] around a true c of 4.000e-04.

    # Geometric-mean identity: sqrt(-gamma_1) = sqrt(c*(c+lambda)), exact in population.
    assert math.sqrt(-GAMMA1_REF) == pytest.approx(math.sqrt(C_REF * S_REF), rel=1e-15)
    assert e1["c_hat"] == pytest.approx(math.sqrt(C_REF * S_REF), rel=0.02)   # [DIUKUR] -0.05%
    # AM-GM: the Roll number sits between c and c+lambda whenever lambda >= 0, so it
    # overstates the half-spread and understates the spread. Both, from one number.
    assert C_REF < e1["c_hat"] < S_REF
    assert e1["spread"] == 2.0 * e1["c_hat"]
    assert e1["spread"] < 2.0 * S_REF              # Roll's spread is a LOWER bound


def test_cumulative_impulse_response_matches_the_ar1_closed_form():
    """6. On an AR(1) the MA coefficients are `theta_k = phi^k` and the cumulative
    response converges to `1/(1-phi)`. For a differenced series that cumulative path is
    the price LEVEL response — the object RULE-EXTRACT-3 points at when it forbids
    reading a single VAR coefficient as lambda.
    """
    phi = 0.7
    out = hb.cumulative_impulse_response([phi], horizon=80)
    theta = np.array(out["theta"])

    assert np.max(np.abs(theta - phi ** np.arange(81))) < 1e-14    # [DIUKUR] 2.8e-17
    assert out["theta"][0] == 1.0
    assert out["long_run"] == pytest.approx(1.0 / (1.0 - phi), abs=1e-9)   # [DIUKUR] 9.5e-13 off
    assert out["cumulative"][-1] == out["long_run"]
    assert np.all(np.diff(out["cumulative"]) > 0)                  # monotone for phi > 0

    # AR(2) locks the recursion itself: theta_2 = phi_1^2 + phi_2 (EXTRACT s9-s12, E10).
    ar2 = hb.cumulative_impulse_response([0.5, 0.2], horizon=6)
    assert ar2["theta"][:4] == pytest.approx([1.0, 0.5, 0.45, 0.325], rel=1e-12)


# =========================================================================== #
# NEGATIVE CONTROLS — the estimator must refuse, not invent                   #
# =========================================================================== #
def test_roll_abstains_about_half_the_time_on_a_spreadless_random_walk():
    """7. No spread means `gamma_1` is a coin flip, and half of those flips have no real
    `sqrt(-gamma_1)`. The two common reflexes there — return 0, or return
    `sqrt(|gamma_1|)` — are silent lies, so this asserts the refusal AND asserts that
    neither reflex ever fired.
    """
    verdicts, fabricated = [], []
    for seed in range(40):
        dp = np.random.default_rng(1000 + seed).normal(0.0, 1e-4, size=100_000)
        r = hb.roll(dp)
        verdicts.append(r["verdict"])
        if r["verdict"] == hb.ABSTAIN:
            # A NaN here is the point: no number at all, not a zero and not |gamma_1|.
            if not (math.isnan(r["c_hat"]) and math.isnan(r["spread"])):
                fabricated.append((seed, r["gamma_1"], r["c_hat"]))
            # TWO legitimate refusal paths now, and the test must not pin one. A white-noise
            # dp IS MA(1) (all gamma_k = 0 for k >= 2), so the order gate passes on ~95% of
            # seeds and RULE-EXTRACT-2 fires on the coin flip; on the remaining ~5% the gate
            # itself refuses, which is its nominal size and not a defect. Requiring
            # RULE-EXTRACT-2 unconditionally would make the gate's own false-alarm rate look
            # like a bug in roll().
            assert ("RULE-EXTRACT-2" in r["reason"]) or ("not MA(1)" in r["reason"]), r["reason"]
        else:
            # When it does answer, the answer is sqrt(-gamma_1) exactly, never sqrt(|.|).
            assert r["gamma_1"] < 0
            assert r["c_hat"] == math.sqrt(-r["gamma_1"])

    rate = verdicts.count(hb.ABSTAIN) / len(verdicts)
    assert not fabricated, f"a refusal returned a number anyway: {fabricated}"
    assert 0.30 < rate < 0.70, f"abstain rate {rate:.3f} is not coin-flip behaviour"
    # [DIUKUR] 21/40 = 0.525 on seeds 1000..1039. [DISIMPULKAN] a new seed set lands in
    # the same band: the binomial SE at p = 0.5, n = 40 is 0.079.


def test_roll_abstains_on_a_momentum_series():
    """8. Positive first-order autocorrelation is a momentum signature. There is no
    spread to read out of it, and `sqrt(|gamma_1|)` there would report a spread that
    grows with the momentum. This one is decisive rather than probabilistic: rho_1 is
    +0.30 against a sampling SE of order `1/sqrt(n)` = 0.0045, so every seed must refuse.
    """
    # An AR(1) is NOT MA(1) — rho_k = phi^k, so rho_2 = 0.09 is far from zero — and since
    # the order gate was added it refuses this series BEFORE the sign of gamma_1 is ever
    # consulted. That ordering is correct: on a non-MA(1) series gamma_1 is not -c*s, so
    # reading its sign would be reasoning about a quantity the model does not describe.
    for seed in range(6):
        dp = ar1_series(50_000, phi=0.3, sigma=1e-4, seed=2000 + seed)
        r = hb.roll(dp)
        assert r["verdict"] == hb.ABSTAIN, f"seed {seed}: reported a spread on momentum"
        assert math.isnan(r["c_hat"]) and math.isnan(r["spread"])
        assert not hb.ma1_order_gate(dp)["passed"], "the gate should reject an AR(1)"
        assert "not MA(1)" in r["reason"], r["reason"]

    # And RULE-EXTRACT-2 still has its own fixture, isolated: an MA(1) with theta > 0 passes
    # the order gate (it really is MA(1)) and has gamma_1 > 0, so the sign path is the one
    # that must fire. Without this, adding the gate would have silently retired the rule.
    for seed in range(6):
        rng = np.random.default_rng(3000 + seed)
        e = rng.normal(0.0, 1e-4, size=200_001)
        dp = e[1:] + 0.5 * e[:-1]                 # MA(1), theta = +0.5 -> rho_1 = +0.4
        assert hb.ma1_order_gate(dp)["passed"], "fixture is not MA(1) — the gate rejected it"
        r = hb.roll(dp)
        assert r["verdict"] == hb.ABSTAIN
        assert r["gamma_1"] > 0                              # [DIUKUR] rho_1 ~ +0.40
        assert math.isnan(r["c_hat"]) and math.isnan(r["spread"])
        assert "RULE-EXTRACT-2" in r["reason"], r["reason"]

    # Same refusal for an MA(1) with theta > 0, so it is the sign of gamma_1 that drives
    # it and not the AR shape.
    assert hb.roll(gaussian_ma(100_000, [0.4], 1e-4, seed=31))["verdict"] == hb.ABSTAIN


@pytest.mark.parametrize(
    "thetas, closed_form_ratio",
    [
        ([-0.6, 0.3, -0.15], -0.587),    # gamma_0 + 2*gamma_1 is NEGATIVE here
        ([-0.6, -0.3, -0.15], 289.0),    # theta(1) -> 0, so the ratio explodes
        ([0.6, 0.3, 0.15], 0.743),       # and it can UNDERSTATE, so no calibration saves it
    ],
)
def test_ma3_fails_the_gate_and_e2_refuses_and_the_damage_is_large(thetas, closed_form_ratio):
    """9. E2 is only `sigma2_w` when the true MA order is 1. On a true MA(3) it is not,
    and `force=True` measures exactly how wrong — that measured number is what justifies
    the gate existing at all.

    Closed form for MA(3): `gamma_0 = (1 + sum theta_i^2) se^2`,
    `gamma_1 = (t1 + t1*t2 + t2*t3) se^2`, true `sigma2_w = (1 + sum theta_i)^2 se^2`.
    The three sign patterns give -0.59x, 289x and 0.74x — the error has no fixed
    direction and no fixed magnitude, which is why "the number looked plausible" is not
    evidence of anything (VERIFY §3).
    """
    sigma_eps = 1e-4
    dp = gaussian_ma(200_000, thetas, sigma_eps, seed=7)

    t1, t2, t3 = thetas
    g0 = (1 + t1 * t1 + t2 * t2 + t3 * t3) * sigma_eps**2
    g1 = (t1 + t1 * t2 + t2 * t3) * sigma_eps**2
    true_sigma2_w = (1 + t1 + t2 + t3) ** 2 * sigma_eps**2
    assert (g0 + 2 * g1) / true_sigma2_w == pytest.approx(closed_form_ratio, rel=0.01)

    gate = hb.ma1_order_gate(dp)
    assert gate["passed"] is False                       # [DIUKUR] Q = 5.4e3..1.0e4 vs crit 16.9
    assert gate["q_stat"] > gate["q_crit"]
    assert "not MA(1)" in gate["reason"]

    refused = hb.sigma2_w_ma1(dp)
    assert refused["verdict"] == hb.ABSTAIN
    assert math.isnan(refused["sigma2_w"])               # refusal returns no number at all

    forced = hb.sigma2_w_ma1(dp, force=True)
    measured_ratio = forced["sigma2_w"] / true_sigma2_w
    assert measured_ratio == pytest.approx(closed_form_ratio, rel=0.05)
    assert "GATE BYPASSED" in forced["reason"] or forced["verdict"] == "FAIL"

    if closed_form_ratio < 0:
        # The negative-variance pattern: hard failure, and the number is NOT clipped.
        assert forced["verdict"] == "FAIL"
        assert forced["sigma2_w"] < 0                    # [DIUKUR] -1.82e-09
        assert forced["sigma2_w"] != 0.0
        assert "Not clipped" in forced["reason"]
    else:
        assert forced["verdict"] == "OK"                 # positive, plausible, and wrong
        assert abs(math.log(measured_ratio)) > 0.25, "the damage must be large enough to matter"
        # [DIUKUR] 286x on the alternating-negative pattern, 0.74x on the all-positive one.
        if closed_form_ratio > 10:
            # This is the number that justifies the gate: two and a half orders of
            # magnitude of damage, reported as a positive, small, entirely plausible
            # variance. Nothing in the output would look wrong without the gate.
            assert measured_ratio > 100                  # [DIUKUR] 286.2


def test_non_identification_two_triples_one_interval():
    """10. The most important negative control in the extraction documents, and the one
    this repo had no pattern for.

    Two DIFFERENT parameter triples on the same identified curve — pick `c`, then
    `s = -gamma_1/c` and `sigma2_u = gamma_0 - c^2 - s^2` — produce identical
    `(gamma_0, gamma_1, sigma2_w)`. Any estimator claiming to recover `c` or `lambda`
    separately must therefore return the SAME answer for both; if it returns different
    answers it is reading noise. `identified_interval_c` passes by construction: it
    returns an interval that contains both, which is the honest statement of what the
    moments know.
    """
    ca, cb = 4.0e-4, 4.8e-4
    sa, sb = -GAMMA1_REF / ca, -GAMMA1_REF / cb
    sua = math.sqrt(GAMMA0_REF - ca**2 - sa**2)
    sub = math.sqrt(GAMMA0_REF - cb**2 - sb**2)
    lama, lamb = sa - ca, sb - cb

    # The parameters differ materially...
    assert lama / lamb == pytest.approx(2.903, rel=0.01)     # lambda: 3.00e-04 vs 1.03e-04
    assert abs(cb / ca - 1.0) > 0.15
    assert sua > 0 and sub > 0 and lama > 0 and lamb > 0     # both economically admissible

    # ...while every moment the data can see is identical, to machine precision.
    assert ca**2 + sa**2 + sua**2 == pytest.approx(cb**2 + sb**2 + sub**2, rel=1e-12)
    assert -ca * sa == pytest.approx(-cb * sb, rel=1e-12)
    assert lama**2 + sua**2 == pytest.approx(lamb**2 + sub**2, rel=1e-12)

    n = 200_000
    dpa = generalized_roll(n, ca, lama, sua, seed=0)
    dpb = generalized_roll(n, cb, lamb, sub, seed=500)       # different draw on purpose
    iva = hb.identified_interval_c(dpa)
    ivb = hb.identified_interval_c(dpb)
    assert iva["verdict"] == "OK" and ivb["verdict"] == "OK"

    # Everything the estimators can see agrees, up to sampling noise...
    ma, mb = hb.autocovariances(dpa, 2), hb.autocovariances(dpb, 2)
    assert ma[0] == pytest.approx(mb[0], rel=0.04)               # [DIUKUR] -0.51%
    assert ma[1] == pytest.approx(mb[1], rel=0.04)               # [DIUKUR] -1.32%
    assert hb.sigma2_w_ma1(dpa, force=True)["sigma2_w"] == pytest.approx(
        hb.sigma2_w_ma1(dpb, force=True)["sigma2_w"], rel=0.05)

    # ...and so does the interval, which is the whole point: it cannot tell them apart.
    assert iva["c_lo"] == pytest.approx(ivb["c_lo"], rel=0.03)   # [DIUKUR] -1.29%
    assert iva["c_hi"] == pytest.approx(ivb["c_hi"], rel=0.03)   # [DIUKUR] -0.66%

    # And each interval covers BOTH truths, which is the honest reading of that noise.
    for iv in (iva, ivb):
        assert iv["c_lo"] <= ca <= iv["c_hi"]
        assert iv["c_lo"] <= cb <= iv["c_hi"]
    # A point estimate of c here would be a coin toss dressed as a measurement: the Roll
    # point (the lambda = 0 member) is 5.29e-04, which is neither of the two truths.
    assert hb.roll(dpa)["c_hat"] > cb


# =========================================================================== #
# CLASS I — the verifier must not cry wolf                                    #
# =========================================================================== #
def test_ma1_order_gate_passes_on_a_true_ma1_series():
    """11. The test that caught a real defect, kept as the regression.

    The first version of `ma1_order_gate` compared each of lags 2..10 to its own
    two-sided 5% critical value and failed the gate if ANY exceeded. On a genuinely
    MA(1) series that rejects `1 - 0.95^9` ~ 37% of the time. A gate that throws away
    more than a third of the correct work is class I in this repo's taxonomy — bad
    precision in a checker destroys correct work, it does not merely add noise — and it
    was found by running the gate on a series known to PASS, not by reading the code.

    Both rules are measured here on the same 40 series, so the fix is quantified rather
    than asserted.
    """
    joint_rejects, naive_rejects = 0, 0
    for seed in range(40):
        dp = gaussian_ma(100_000, [-0.4], 1e-4, seed)
        gate = hb.ma1_order_gate(dp)
        if not gate["passed"]:
            joint_rejects += 1
        rho = np.array(gate["rho"])
        if np.any(np.abs(rho[2:]) / gate["se"] > 1.959963985):     # the discarded per-lag rule
            naive_rejects += 1

    joint_rate = joint_rejects / 40.0
    naive_rate = naive_rejects / 40.0
    assert joint_rate < 0.20, f"gate cries wolf on true MA(1) at {joint_rate:.3f}"
    # [DIUKUR] joint 2/40 = 0.050 against a nominal alpha of 0.05; naive 16/40 = 0.400
    # against the 0.37 the binomial predicts. The defect reproduces, and the fix holds.
    assert naive_rate > 0.20, "the per-lag rule should still be visibly bad — fixture drifted?"
    assert joint_rate < naive_rate

    # The gate must also carry its evidence, not just a boolean.
    gate = hb.ma1_order_gate(gaussian_ma(200_000, [-0.4], 1e-4, seed=99))
    assert gate["passed"] is True
    assert gate["reason"] == ""
    # The reference is a Satterthwaite-matched scale*chi2(df), NOT chi2(9) — see the
    # second calibration fix below. Effective df is therefore non-integer and shrinks as
    # |rho_1| grows, because the per-lag z-scores are correlated under MA(1).
    assert 3.0 < gate["df"] < 9.0001, gate["df"]
    assert gate["scale"] >= 1.0
    assert gate["q_stat"] < gate["q_crit"]
    assert len(gate["rho"]) == 11 and gate["rho"][0] == 1.0


def test_ma1_order_gate_size_is_nominal_on_three_different_ma1_processes():
    """11b. The gate's SIZE, measured — this test records the second defect it had.

    An earlier version of this file explained a hot type-I rate by saying the
    generalized-Roll series is not a linear process in iid innovations (`q_t` is binary),
    so Bartlett's variance would not be exact for it. **That explanation was wrong**, and
    a Gaussian MA(1) — a textbook linear process where Bartlett IS exact — over-rejected
    *worse*, not better. The real cause was the reference distribution: Bartlett gives
    non-zero covariances between neighbouring autocorrelations (`2*rho_1/n` at lag 1
    apart, `rho_1^2/n` at two), so `Q` is a quadratic form in CORRELATED normals with
    variance `2*tr(R^2)` ~ 30.4 at `rho_1 ~ -0.41`, not chi2(9)'s 18. Comparing to
    chi2(9) rejected ~9% of genuinely MA(1) series at a nominal 5%.

    Both wrong explanations were reached by reasoning; what settled it was running the
    gate on WHITE NOISE, where `rho_1 = 0` makes the correlation vanish and the size
    came back to nominal. That is the control that isolates the mechanism.

    [DIUKUR] with the Satterthwaite reference, 600 reps each at n = 120,000
    (binomial SE 0.89 pp): white noise 4.2%, Gaussian MA(1) theta = -0.4 5.7%,
    generalized Roll 4.8%. All within one SE of the nominal 5%.

    Kept small here so the suite stays fast; the bound is loose enough to survive seed
    noise at 60 reps but tight enough to fail if the reference regresses to chi2(9),
    which would push every one of these back to 8-10%.
    """
    n, reps = 60_000, 60
    cases = {
        "white": lambda s: np.random.default_rng(70_000 + s).normal(0, 1e-4, n),
        "gaussian_ma1": lambda s: gaussian_ma(n, [-0.4], 1e-4, 60_000 + s),
        "generalized_roll": lambda s: generalized_roll(n, C_REF, LAM_REF, SU_REF, 50_000 + s),
    }
    rates = {}
    for name, gen in cases.items():
        rejects = sum(0 if hb.ma1_order_gate(gen(s))["passed"] else 1 for s in range(reps))
        rates[name] = rejects / reps
    for name, rate in rates.items():
        assert rate < 0.15, f"gate size on {name} is {rate:.1%}, nominal 5% — reference regressed?"
    assert max(rates.values()) < 0.15, rates


def test_chi2_ppf_matches_published_table_values():
    """12. The gate's critical value comes from a Wilson-Hilferty approximation, not
    scipy. If it drifts, the gate silently changes its own alpha — a checker whose
    threshold is wrong is worse than no checker.
    """
    table_95 = {5: 11.070, 9: 16.919, 20: 31.410}
    for df, expected in table_95.items():
        got = hb._chi2_ppf(0.95, df)
        assert got == pytest.approx(expected, rel=0.01), f"df={df}: {got:.4f} vs {expected}"
    # [DIUKUR] df=5 -0.24%, df=9 -0.10%, df=20 -0.03%. The approximation is worst at
    # small df (df=1 is -2.5%), which is why the module scopes its claim to df >= 3 and
    # why the gate never runs with fewer than 9.
    assert hb._chi2_ppf(0.95, 9) > hb._chi2_ppf(0.95, 5)      # monotone in df
    assert hb._chi2_ppf(0.99, 9) > hb._chi2_ppf(0.95, 9)      # monotone in p
    assert math.isnan(hb._chi2_ppf(0.95, 0))                  # degenerate df refuses


# =========================================================================== #
# HARD-FAIL SEMANTICS                                                         #
# =========================================================================== #
def test_sigma2_w_ar_hard_fails_when_phi_one_is_not_positive(monkeypatch):
    """13. RULE-EXTRACT-6: `sigma2_w = sigma2_eps / phi(1)^2` with `phi(1) = 1 - sum(phi)`,
    and a non-positive `phi(1)` FAILS. Not clipped, not absolute-valued.

    The case is SYNTHESISED, because it cannot be reached with data — see the next test.
    Yule-Walker is replaced by a solver that returns `phi = (1.5, 0, ..., 0)`, giving
    `phi(1) = -0.5` while the innovation variance stays positive, so the `phi(1)` branch
    is the one that fires.

    The reason this branch matters is that the failure it catches is INVISIBLE: squaring
    a negative `phi(1)` produces a perfectly plausible positive variance. The test
    computes that number and asserts it is finite and positive, then asserts the module
    refused it anyway.
    """
    dp = reference_series()
    g = hb.autocovariances(dp, 30)

    def fake_solve(a, b):
        phi = np.zeros(a.shape[0])
        phi[0] = 1.5                      # sum(phi) = 1.5  =>  phi(1) = -0.5
        return phi

    monkeypatch.setattr(np.linalg, "solve", fake_solve)
    out = hb.sigma2_w_ar(dp, K=30)

    assert out["verdict"] == "FAIL"
    assert out["phi_one"] == pytest.approx(-0.5, rel=1e-12)
    assert math.isnan(out["sigma2_w"])            # no number: not clipped, not |.|
    assert "RULE-EXTRACT-6" in out["reason"] and "NOT clipped" in out["reason"]

    # What it would have returned without the guard: positive, finite, and wrong.
    sigma2_eps = float(g[0] - 1.5 * g[1])
    assert sigma2_eps > 0                          # so the earlier guard did not fire
    silent = sigma2_eps / (-0.5) ** 2
    assert math.isfinite(silent) and silent > 0    # [DIUKUR] 4.64e-06, ~26x the truth
    assert silent > 10 * SIGMA2W_REF


def test_e9_phi_one_guard_is_unreachable_through_yule_walker():
    """13b. FINDING, and the reason the test above has to synthesise its case.

    Yule-Walker fitted to a positive-semidefinite sample autocovariance sequence always
    returns a CAUSAL AR polynomial. A causal `phi(z)` has all roots outside the unit
    circle, and `phi(0) = 1 > 0`, so `phi(1) < 0` would force a real root in (0, 1) by
    the intermediate value theorem. Therefore `phi(1) > 0` strictly, whatever the data.
    `autocovariances` uses the 1/n (PSD) convention, so the premise holds by construction.

    Measured against inputs chosen to break it: an I(1) series, an I(2) series, a linear
    trend, an explosive exponential, a deterministic sinusoid and a near-unit-root AR(1).
    [DIUKUR] the smallest `phi(1)` observed was 5.00e-05, on the linear trend — small,
    and still positive; the I(1) and I(2) series sit at 8.8e-05 and 5.6e-05.

    If this test ever fails, do NOT weaken it: it means the guard acquired a real
    trigger, and the synthesised test above should be rewritten around that real case.
    """
    n = 60_000
    rng = np.random.default_rng(0)
    ar = np.empty(n)
    ar[0] = 0.0
    e = rng.normal(size=n)
    for t in range(1, n):
        ar[t] = 0.9999 * ar[t - 1] + e[t]

    cases = {
        "I(1)": np.cumsum(rng.normal(size=n)),
        "I(2)": np.cumsum(np.cumsum(rng.normal(size=n))),
        "linear trend": np.arange(n, dtype=float) + 1e-9 * rng.normal(size=n),
        "explosive": np.power(1.0001, np.arange(n, dtype=float)),
        "sinusoid": np.sin(2 * np.pi * np.arange(n) / 400.0),
        "near unit root": ar,
    }
    smallest = math.inf
    for name, series in cases.items():
        out = hb.sigma2_w_ar(series, K=30)
        assert out["verdict"] == "OK", f"{name}: {out.get('reason', '')}"
        assert out["phi_one"] > 0.0, f"{name}: phi(1) = {out['phi_one']:.3e}"
        smallest = min(smallest, out["phi_one"])
    assert 0.0 < smallest < 1e-3       # [DIUKUR] 5.00e-05 — close to the boundary, never past it

    # A degenerate series does not sneak past either: it abstains on the singular solve.
    degenerate = hb.sigma2_w_ar(np.ones(n), K=30)
    assert degenerate["verdict"] == hb.ABSTAIN and "singular" in degenerate["reason"]


def test_subsample_counts_skipped_blocks_and_refuses_below_two():
    """14. A mean over an unknown denominator is not a mean. Every block is either used
    or counted as skipped with its reason, `n_used + n_skipped` closes on the block
    count, and fewer than two usable blocks is a refusal rather than a point estimate
    with no spread.
    """
    dp = reference_series()

    # (a) All blocks usable: the estimate and its subsampling SE.
    ok = hb.subsample_estimate(dp, lambda b: hb.sigma2_w_ar(b, K=20), n_blocks=20)
    assert ok["verdict"] == "OK"
    assert ok["n_used"] + ok["n_skipped"] == 20
    assert ok["n_used"] == 20 and ok["n_skipped"] == 0
    assert ok["mean"] == pytest.approx(SIGMA2W_REF, rel=0.05)   # [DIUKUR] +0.28%, SE 1.9e-09
    assert ok["se"] > 0 and len(ok["per_block"]) == 20

    # (b) Every block refuses (a true MA(3) against the MA(1) gate) -> refuse, and say
    #     how many blocks were skipped rather than returning a mean over zero of them.
    bad = hb.subsample_estimate(gaussian_ma(200_000, [-0.6, -0.3, -0.15], 1e-4, seed=7),
                                hb.sigma2_w_ma1, n_blocks=20)
    assert bad["verdict"] == hb.ABSTAIN
    assert bad["n_used"] == 0 and bad["n_skipped"] == 20
    assert bad["n_used"] + bad["n_skipped"] == 20
    assert "0 of 20 blocks" in bad["reason"]
    assert len(bad["skipped"]) == 20 and all(isinstance(i, int) for i, _ in bad["skipped"])

    # (c) A block that RAISES is counted too, not swallowed: 20 blocks of 5 points each.
    raised = hb.subsample_estimate(generalized_roll(100, C_REF, LAM_REF, SU_REF, 3),
                                   hb.sigma2_w_ma1, n_blocks=20)
    assert raised["verdict"] == hb.ABSTAIN
    assert raised["n_used"] + raised["n_skipped"] == 20
    assert raised["skipped"][0][1].startswith("ValueError:")

    # (d) Exactly one usable block is still a refusal — one number has no standard error.
    calls = {"n": 0}

    def only_the_first_block_works(_block):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"verdict": "OK", "sigma2_w": 1.0}
        return {"verdict": hb.ABSTAIN, "sigma2_w": float("nan"), "reason": "synthetic refusal"}

    one = hb.subsample_estimate(dp, only_the_first_block_works, n_blocks=8)
    assert one["verdict"] == hb.ABSTAIN
    assert one["n_used"] == 1 and one["n_skipped"] == 7
    assert one["n_used"] + one["n_skipped"] == 8
    assert math.isnan(one["mean"]) and math.isnan(one["se"])
    assert "only 1 of 8 blocks" in one["reason"]
