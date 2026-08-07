"""costs.py — execution cost as a FUNCTION of the things that actually vary.

`backtest.py` charges `cost_bps=10.0 + slippage_bps=2.0` per leg. Those are hard-coded defaults
that were never measured, and every result in this repo inherits them. This module replaces the
constant with a function, and refuses to carry defaults of its own.

Why there are no defaults for fee or funding
--------------------------------------------
Two of the inputs cannot be given a safe default, and the reason is different for each:

* **`fee_bps_per_side` cannot be measured from data at all.** It is a published rate that depends
  on the venue, the account's tier and whether the order was maker or taker. Nothing in the tape
  reveals it. A default here would be exactly `cost_bps=10.0` again — an assumption inherited by
  everything downstream and attributed to no one.
* **`funding_bps_per_day` varies by regime.** It was measured at ~1.84 bps/day on a single frozen
  30-day window; a different month is a different number, and the settled history exists
  precisely so a caller can pick the regime it means.

So both RAISE when absent. That is the point of the module, not an inconvenience in it.

What this module does NOT do
----------------------------
It is not wired into `backtest.py`. Nothing calls it from the harness yet, deliberately: swapping
the cost model changes every recorded number, and that is a decision with its own approval.

Tax is out of scope on purpose — it is a jurisdiction layer, not a property of the system. The
parameter exists in the signature with a value of 0 so it cannot be silently forgotten later, and
it is never populated here.

Units: everything is in **bps** (1 bps = 1e-4). `bps_per_leg` is charged per unit of turnover;
`bps_carry` is charged once for the whole holding period, already signed.
"""

from __future__ import annotations

from typing import Optional

__all__ = ["cost_model", "HALF_SPREAD_BPS", "IMPACT_BPS", "UNAVAILABLE", "CostInputError"]

UNAVAILABLE = "UNAVAILABLE"


class CostInputError(ValueError):
    """A required input was not supplied. Never downgraded to a default."""


# Half-spread per venue, measured. `docs/BOOK-001-quoted-spread.md`: the book is one tick wide
# from p25 to p99 on all three venues, so c = 0.5 tick, and one tick is ~0.0156 bps at the
# reference mid. coinbase has NO depth_snapshots in the store at all — not a small sample, none —
# so it is UNAVAILABLE rather than borrowed from binancef.
HALF_SPREAD_BPS = {
    "binancef": 0.00780,      # [DIUKUR] BOOK-001, 2*p50 = 0.01561 bps full spread
    "bybit": 0.00781,         # [DIUKUR] BOOK-001
    "okx": 0.00781,           # [DIUKUR] BOOK-001
    "coinbase": UNAVAILABLE,  # no depth_snapshots recorded for this venue, ever
}

# Impact beyond the half-spread, by notional. `docs/DIAG-cost-ledger-001.md` §1c, p50.
# The $1M row is measured on the 32% of snapshots whose STORED book (top-20/top-50) was deep
# enough; the rest are counted thin and NOT extrapolated, so $1M carries that caveat with it.
IMPACT_BPS = {
    "binancef": {10_000: 0.0000, 100_000: 0.0000, 1_000_000: 0.0000},
    "bybit":    {10_000: 0.0000, 100_000: 0.0000, 1_000_000: 0.3286},
    "okx":      {10_000: 0.0000, 100_000: 0.0000, 1_000_000: 0.1394},
    "coinbase": UNAVAILABLE,
}


def _impact_for(venue: str, notional: float) -> float:
    table = IMPACT_BPS.get(venue)
    if table is None or table is UNAVAILABLE:
        raise CostInputError(
            f"impact for venue {venue!r} is {UNAVAILABLE}: no depth_snapshots were ever recorded "
            "for it, so there is nothing to measure. Using another venue's number would be a "
            "borrowed assumption wearing a measured label.")
    # step function on the measured grid; anything above the largest measured notional is
    # refused rather than extrapolated, because the thin-book caveat only grows above it.
    grid = sorted(table)
    if notional > grid[-1]:
        raise CostInputError(
            f"notional {notional:,.0f} exceeds the largest MEASURED notional {grid[-1]:,.0f} for "
            f"{venue!r}. Extrapolating past it is not supported: at $1M already 68% of binancef "
            "snapshots could not fill from the stored book.")
    for g in grid:
        if notional <= g:
            return float(table[g])
    return float(table[grid[-1]])


def cost_model(
    venue: str,
    instrument: str,
    fee_bps_per_side: Optional[float],
    order_type: str,
    notional: float,
    days_held: float,
    position_sign: float,
    funding_bps_per_day: Optional[float],
    tax_bps_per_sale: float = 0.0,
) -> dict:
    """Cost of one execution, split into the parts that behave differently.

    Returns ``{bps_per_leg, bps_carry, bps_tax}``.

    * ``bps_per_leg`` — charged per unit of turnover: fee + half-spread + impact. A maker
      crosses nothing, so the half-spread and impact terms are zero for ``order_type='maker'``.
    * ``bps_carry`` — charged once for the whole holding period, and **signed by the position**.
      A short RECEIVES funding when the rate is positive. The earlier turnover census charged
      funding as a pure cost to long and short alike; that was wrong, and it is fixed here.
    * ``bps_tax`` — always the passthrough of ``tax_bps_per_sale``. Jurisdiction, not system.

    ``instrument='spot'`` sets carry to zero STRUCTURALLY — spot has no funding leg at all, so
    the zero does not come from a data lookup that could later be repointed at a number.
    """
    if fee_bps_per_side is None:
        raise CostInputError(
            "fee_bps_per_side is required and has no default. It cannot be measured from the "
            "tape — it is a published rate that depends on venue and account tier — so a default "
            "here would repeat cost_bps=10.0: an assumption inherited by everything downstream "
            "and attributed to no one.")
    if instrument not in ("spot", "perp"):
        raise CostInputError(f"instrument must be 'spot' or 'perp', got {instrument!r}")
    if order_type not in ("maker", "taker"):
        raise CostInputError(f"order_type must be 'maker' or 'taker', got {order_type!r}")

    if instrument == "perp":
        if funding_bps_per_day is None:
            raise CostInputError(
                "funding_bps_per_day is required for a perp and has no default. It varies by "
                "regime — the frozen 30-day window gave ~1.84 bps/day, and a different month is "
                "a different number — which is why the settled history exists for the caller to "
                "choose from.")
        carry = -float(position_sign) * float(funding_bps_per_day) * float(days_held)
    else:
        # STRUCTURAL zero: spot has no funding leg. Not a lookup that returned 0.
        if funding_bps_per_day not in (None, 0, 0.0):
            raise CostInputError(
                "instrument='spot' pays no funding; passing funding_bps_per_day for it would "
                "make a category error look deliberate. Pass None.")
        carry = 0.0

    per_leg = float(fee_bps_per_side)
    if order_type == "taker":
        hs = HALF_SPREAD_BPS.get(venue)
        if hs is None or hs is UNAVAILABLE:
            raise CostInputError(
                f"half-spread for venue {venue!r} is {UNAVAILABLE} — no depth_snapshots were "
                "recorded for it. binancef's number is not a substitute.")
        per_leg += float(hs) + _impact_for(venue, float(notional))

    return {"bps_per_leg": per_leg, "bps_carry": carry, "bps_tax": float(tax_bps_per_sale)}
