"""test_hasbrouck_gates.py — a sixth estimator cannot enter ungated.

`btcquant/hasbrouck.py` holds two kinds of estimator: those whose arithmetic is only valid
when `Delta p` is MA(1), and those that assume no MA order at all. The first kind must call
`ma1_order_gate`; the second must not be required to.

This file exists because the distinction was got wrong twice. An adversarial review found
`sigma2_w_wold` returning 291x the truth while the gated `sigma2_w_ma1` correctly refused on
the same series, and that one instance was fixed. Running PREREG-microstructure-001 on real
data then showed the pattern was three: `roll`, `pricing_error_lower_bound` and
`identified_interval_c` were all ungated as well. That run survived only because its
discriminant happened to go negative — on slightly different moments `identified_interval_c`
would have returned a confident interval on a series whose `rho_1` was -0.71, outside anything
an MA(1) can produce.

So the guard is not "remember to add the gate". It is: **every public name must be classified,
and every MA(1)-dependent one must be shown to call the gate.** Adding a function to `__all__`
without classifying it fails the suite.
"""

from __future__ import annotations

import inspect
import re

from btcquant import hasbrouck as hb

# Estimators whose arithmetic is only valid under MA(1). Each entry says why.
MA1_DEPENDENT = {
    "roll": "c_hat = sqrt(-gamma_1) is the Roll model's own inverse; on a non-MA(1) series "
            "gamma_1 is not -c*s and the number means something else. Measured: 8x-30x the "
            "book half-spread on real BTCUSDT perp data (DIAG-provenance-001).",
    "sigma2_w_ma1": "gamma_0 + 2*gamma_1 equals sigma2_w only when gamma_k = 0 for k >= 2.",
    "sigma2_w_wold": "algebraically identical to sigma2_w_ma1; an ungated alias for a gated "
                     "estimator is a hole in the gate, not a second opinion.",
    "pricing_error_lower_bound": "the bound theta^2*sigma2_eps is derived from the MA(1) Wold "
                                 "representation; without it there is no such representation.",
    "identified_interval_c": "both endpoints are functions of (gamma_0, gamma_1) under the "
                             "generalized Roll model, which is MA(1) by construction.",
}

# Public names that legitimately do NOT need the gate. Each entry says why.
GATE_EXEMPT = {
    "autocovariances": "a primitive; it computes moments and asserts nothing about order",
    "ma1_order_gate": "it IS the gate",
    "sigma2_w_variance_ratio": "the intercept of Var(dp_k)/k on 1/k is sigma2_w for ANY MA "
                               "order — that order-independence is the whole point of E4",
    "sigma2_w_ar": "an AR(K) truncation does not need to know the MA order; that is its "
                   "advantage over a direct MA fit",
    "cumulative_impulse_response": "takes AR coefficients, not a series",
    "subsample_estimate": "a wrapper; it inherits whatever gate the estimator passed to it has",
    "ABSTAIN": "a string constant",
}


def test_every_public_name_is_classified():
    """A new estimator cannot enter `__all__` without a decision about its gate."""
    classified = set(MA1_DEPENDENT) | set(GATE_EXEMPT)
    published = set(hb.__all__)
    unclassified = published - classified
    assert not unclassified, (
        f"{sorted(unclassified)} appear in hasbrouck.__all__ but are in neither MA1_DEPENDENT "
        "nor GATE_EXEMPT. Decide which, with a reason, before the suite can pass — that "
        "decision is the point of this file.")
    stale = classified - published
    assert not stale, f"{sorted(stale)} are classified here but no longer public; drop them"


def test_every_ma1_dependent_estimator_calls_the_order_gate():
    """The assertion that was RED before the fix, on three of the five."""
    missing = []
    for name in sorted(MA1_DEPENDENT):
        fn = getattr(hb, name)
        src = inspect.getsource(fn)
        # the call, not the word in a docstring
        body = re.sub(r'""".*?"""', "", src, flags=re.S)
        if "ma1_order_gate(" not in body:
            missing.append(name)
    assert not missing, (
        f"{missing} rest on the MA(1) premise but never call ma1_order_gate. An ungated "
        "estimator answers confidently on a series where the premise is false; that is how "
        "an interval was nearly published on data whose rho_1 was -0.71.")


def test_gated_estimators_actually_abstain_on_a_non_ma1_series():
    """Classification and a call site are structure; this is behaviour.

    A true MA(3) is not MA(1), so every gated estimator must refuse it. Uses the sign pattern
    from VERIFY-hasbrouck-extraction.md 3 where the ungated expression returns a NEGATIVE
    variance — the case that most needs refusing.
    """
    import numpy as np
    rng = np.random.default_rng(4242)
    e = rng.normal(0, 1e-4, 200_000)
    dp = e[3:] - 0.6 * e[2:-1] + 0.3 * e[1:-2] - 0.15 * e[:-3]
    assert not hb.ma1_order_gate(dp)["passed"], "fixture drifted — the gate should reject MA(3)"

    for name in sorted(MA1_DEPENDENT):
        out = getattr(hb, name)(dp)
        assert out["verdict"] == hb.ABSTAIN, (
            f"{name} returned {out['verdict']} on a true MA(3); every MA(1)-dependent "
            f"estimator must ABSTAIN there. Got: {out}")
        assert out.get("reason"), f"{name} abstained without saying why"


def test_the_gate_itself_still_passes_a_true_ma1_series():
    """Class I: the stricter gates must not make correct work impossible."""
    import numpy as np
    rng = np.random.default_rng(7)
    n, c, lam, su = 200_000, 4e-4, 3e-4, 2e-4
    q = rng.choice([-1.0, 1.0], size=n)
    dp = np.diff(np.cumsum(lam * q + rng.normal(0, su, n)) + c * q)
    assert hb.ma1_order_gate(dp)["passed"], "the gate rejects a series that IS MA(1)"
    for name in sorted(MA1_DEPENDENT):
        out = getattr(hb, name)(dp)
        assert out["verdict"] == "OK", f"{name} refused a genuine MA(1) series: {out}"
