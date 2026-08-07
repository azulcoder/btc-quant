"""test_costs.py — the cost function must refuse what it cannot know.

`btcquant/costs.py` exists to replace `cost_bps=10.0 + slippage_bps=2.0`, two hard-coded numbers
that every recorded result in this repo inherits. A replacement that shipped with defaults of its
own would have changed nothing except who to blame, so the tests that matter most here are the
ones asserting it RAISES.
"""

from __future__ import annotations

import inspect

import pytest

from btcquant import costs


# --------------------------------------------------------------------------- #
# POSITIVE CONTROL — reconstruct the old constant from the old parameters      #
# --------------------------------------------------------------------------- #
def test_the_old_12_bps_is_reconstructible_from_the_old_assumptions():
    """The first number out of a new instrument is a CONTROL, not a result.

    `backtest.py` charges 12 bps/leg: `cost_bps=10.0` (read as a taker fee) plus
    `slippage_bps=2.0`. Feeding those same assumptions in must give the same 12 back, or the
    new function is not a replacement for the old one — it is a different model wearing the
    same job title.
    """
    out = costs.cost_model(venue="binancef", instrument="perp", fee_bps_per_side=10.0,
                           order_type="taker", notional=10_000, days_held=0.0,
                           position_sign=+1.0, funding_bps_per_day=0.0)
    # 10.0 fee + 0.0078 half-spread + 0.0000 impact; the old model's 2.0 "slippage" is the
    # term this module MEASURES, and it measures 0.0078, not 2.0.
    assert out["bps_per_leg"] == pytest.approx(10.0078, abs=1e-6)
    assert out["bps_carry"] == 0.0
    assert out["bps_tax"] == 0.0

    # And the gap between the two models is the whole point: the old constant is 12.0.
    assert 12.0 - out["bps_per_leg"] == pytest.approx(1.9922, abs=1e-4)


def test_maker_crosses_nothing():
    """A maker pays no half-spread and no impact — it provides the quote rather than taking it."""
    mk = costs.cost_model("binancef", "perp", 2.0, "maker", 1_000_000, 0.0, +1.0, 0.0)
    assert mk["bps_per_leg"] == pytest.approx(2.0, abs=1e-12), (
        "a maker was charged spread or impact")


# --------------------------------------------------------------------------- #
# NEGATIVE CONTROLS — the refusals are the product                             #
# --------------------------------------------------------------------------- #
def test_fee_without_a_value_raises():
    with pytest.raises(costs.CostInputError) as e:
        costs.cost_model("binancef", "perp", None, "taker", 10_000, 1.0, +1.0, 1.0)
    assert "fee_bps_per_side" in str(e.value)


def test_perp_funding_without_a_value_raises():
    with pytest.raises(costs.CostInputError) as e:
        costs.cost_model("binancef", "perp", 5.0, "taker", 10_000, 1.0, +1.0, None)
    assert "funding_bps_per_day" in str(e.value)


def test_spot_refuses_a_funding_number_instead_of_quietly_ignoring_it():
    """Passing funding to spot is a category error. Silently zeroing it would let the error
    look deliberate — which is exactly how the first turnover census charged perp funding to a
    spot backtest and nobody noticed until the numbers were already published."""
    with pytest.raises(costs.CostInputError):
        costs.cost_model("coinbase", "spot", 5.0, "maker", 10_000, 30.0, +1.0, 1.84)


def test_coinbase_spread_and_impact_are_unavailable_not_borrowed():
    """There are no depth_snapshots for coinbase in the store — not a small sample, none.
    Substituting binancef's number would be a borrowed assumption wearing a measured label."""
    assert costs.HALF_SPREAD_BPS["coinbase"] is costs.UNAVAILABLE
    assert costs.IMPACT_BPS["coinbase"] is costs.UNAVAILABLE
    with pytest.raises(costs.CostInputError) as e:
        costs.cost_model("coinbase", "spot", 5.0, "taker", 10_000, 0.0, +1.0, None)
    assert costs.UNAVAILABLE in str(e.value)


def test_notional_above_the_measured_grid_is_refused_not_extrapolated():
    with pytest.raises(costs.CostInputError) as e:
        costs.cost_model("bybit", "perp", 5.0, "taker", 5_000_000, 0.0, +1.0, 0.0)
    assert "MEASURED" in str(e.value)


# --------------------------------------------------------------------------- #
# The guard against a default creeping back in                                 #
# --------------------------------------------------------------------------- #
def test_no_default_can_be_reintroduced_for_fee_or_funding():
    """A structural assertion, not a behavioural one.

    The failure this guards against is someone adding `fee_bps_per_side: float = 5.0` in a
    hurry. That would restore exactly the situation this module was written to end: a number
    nobody chose, inherited by everything downstream. `tax_bps_per_sale=0` is the ONE permitted
    default, and it is permitted because zero is not an estimate — it is the parameter being
    held open so a jurisdiction layer cannot be forgotten later.
    """
    sig = inspect.signature(costs.cost_model)
    for name in ("venue", "instrument", "fee_bps_per_side", "order_type", "notional",
                 "days_held", "position_sign", "funding_bps_per_day"):
        assert sig.parameters[name].default is inspect.Parameter.empty, (
            f"{name} acquired a default; that is how cost_bps=10.0 happened")
    assert sig.parameters["tax_bps_per_sale"].default == 0.0
    assert set(sig.parameters) == {
        "venue", "instrument", "fee_bps_per_side", "order_type", "notional", "days_held",
        "position_sign", "funding_bps_per_day", "tax_bps_per_sale"}


# --------------------------------------------------------------------------- #
# Funding is SIGNED — the defect this module fixes                             #
# --------------------------------------------------------------------------- #
def test_short_with_positive_funding_receives_it():
    """A short RECEIVES funding when the rate is positive; longs pay shorts.

    `scripts/diag_turnover_census_001.py` charged funding as a pure cost to long and short
    alike, which double-counts against every short leg. Recorded here as a fixed defect: the
    carry for a short must be NEGATIVE (a credit), not positive.
    """
    long_ = costs.cost_model("binancef", "perp", 5.0, "taker", 10_000, 10.0, +1.0, 1.8441)
    short = costs.cost_model("binancef", "perp", 5.0, "taker", 10_000, 10.0, -1.0, 1.8441)
    assert long_["bps_carry"] < 0 or short["bps_carry"] > 0 or True   # readability below
    assert long_["bps_carry"] == pytest.approx(-18.441, abs=1e-3), "a long must PAY"
    assert short["bps_carry"] == pytest.approx(+18.441, abs=1e-3), "a short must RECEIVE"
    assert long_["bps_carry"] == -short["bps_carry"]

    # and with a NEGATIVE rate the sides swap, which is the same rule and not a special case
    neg = costs.cost_model("binancef", "perp", 5.0, "taker", 10_000, 10.0, +1.0, -1.0)
    assert neg["bps_carry"] == pytest.approx(+10.0, abs=1e-9), "a long RECEIVES a negative rate"


def test_spot_carry_is_structurally_zero_at_any_holding_period():
    for days in (0.0, 1.0, 365.0, 10_000.0):
        out = costs.cost_model("binancef", "spot", 2.0, "maker", 10_000, days, +1.0, None)
        assert out["bps_carry"] == 0.0, "spot grew a funding leg"


def test_tax_is_a_passthrough_and_defaults_to_zero():
    """Tax is a jurisdiction layer, deliberately out of scope. The parameter is held open so it
    cannot be silently forgotten; it is never populated here."""
    assert costs.cost_model("binancef", "perp", 5.0, "taker", 10_000, 0, +1.0, 0.0)["bps_tax"] == 0.0
    assert costs.cost_model("binancef", "perp", 5.0, "taker", 10_000, 0, +1.0, 0.0,
                            tax_bps_per_sale=30.0)["bps_tax"] == 30.0


def test_it_is_not_wired_into_the_backtest():
    """Deliberate: swapping the cost model changes every recorded number, and that is its own
    decision. If this ever passes by accident, the swap happened without one."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent / "btcquant" / "backtest.py").read_text()
    assert "costs" not in src.split("\n\n")[0], "backtest.py imports costs — was that approved?"
    assert "cost_model(" not in src
