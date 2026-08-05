"""The rejection funnel: where every candidate dies.

Terminates at zero. The panel's job is to show *which stage* kills the last
candidate, because that is the finding — not that the count is zero.

The unit changes partway down, from markets to candidate baskets, and each
stage carries its own unit so the drop from 44,453 to 2,650 is not misread as
attrition when it is a change of denominator.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from monitor.checks import is_rfq_shell, tradeable_size, verify_partition
from monitor.metrics import _as_legs, _events
from src.venues.kalshi.fees import FeeModel, FeeType, Role, basket_fee

D = Decimal


@dataclass(frozen=True)
class Stage:
    key: str
    label: str
    count: int
    unit: str
    note: str = ""


def _fee_model(leg: dict) -> FeeModel:
    raw_type = leg.get("fee_type") or "quadratic"
    try:
        fee_type = FeeType(raw_type)
    except ValueError:
        fee_type = FeeType.QUADRATIC
    multiplier = leg.get("fee_multiplier")
    return FeeModel(fee_type, D(multiplier) if multiplier not in (None, "") else D(1))


def basket_fee_cents(legs: list[dict], contracts: int) -> D:
    """Taker fee for crossing every leg once, in cents per basket."""
    priced = [
        (_fee_model(leg), contracts, D(leg["ask_yes_cents"]) / 100, Role.TAKER)
        for leg in legs
    ]
    return basket_fee(priced) * 100 / contracts


def build(rows: list[dict]) -> list[Stage]:
    """Reference implementation, over materialised rows.

    Kept as the standard :func:`build_from` is checked against; see
    ``tests/test_aggregate.py``.
    """
    live = [r for r in rows if r["two_sided"] == "True"]

    candidates: list[list[dict]] = []
    for _event, legs in sorted(_events(rows).items()):
        if len(legs) < 2 or any(is_rfq_shell(leg["ticker"]) for leg in legs):
            continue
        if any(leg["two_sided"] != "True" for leg in legs):
            continue
        candidates.append(legs)

    return _stages(len(rows), len(live), candidates)


def build_from(aggregate) -> list[Stage]:
    """Same funnel, from the streaming aggregate.

    The candidate predicate is evaluated from per-event counters rather than
    from the legs, because a funnel candidate need not be a range partition --
    only the *verified* stage onward needs leg detail, and that is exactly what
    the aggregate retains.
    """
    candidates: list[list[dict]] = []
    retained = aggregate.candidate_legs()
    for event, state in sorted(aggregate.events.items()):
        if state.n_legs < 2 or state.has_rfq or not state.all_two_sided:
            continue
        legs = retained.get(event)
        # No retained legs means the event held a non-range bucket, which
        # verify_partition rejects; it still counts as a candidate.
        candidates.append(legs if legs is not None else [])

    return _stages(aggregate.n_markets, aggregate.n_two_sided, candidates)


def _stages(n_markets: int, n_live: int, candidates: list[list[dict]]) -> list[Stage]:
    verified = [legs for legs in candidates if legs and verify_partition(_as_legs(legs))]

    with_capacity = [
        legs for legs in verified if min(tradeable_size(leg["ask_size"]) for leg in legs) >= 1
    ]

    clears_fee = []
    actionable = []
    for legs in with_capacity:
        cost = sum(D(leg["ask_yes_cents"]) for leg in legs)
        contracts = int(min(tradeable_size(leg["ask_size"]) for leg in legs))
        total = cost + basket_fee_cents(legs, contracts)
        if total < 100:
            clears_fee.append(legs)
            if D(100) - total > 0:
                actionable.append(legs)

    return [
        Stage("scanned", "Markets scanned", n_markets, "markets"),
        Stage("two_sided", "Two-sided book", n_live, "markets", "both YES and NO bids resting"),
        Stage(
            "candidates",
            "Candidate baskets",
            len(candidates),
            "events",
            "multi-leg, every leg buyable - unit changes here",
        ),
        Stage(
            "verified",
            "Verified partition",
            len(verified),
            "events",
            "buckets tile the line; the venue's mutually_exclusive flag is not used",
        ),
        Stage(
            "capacity",
            "Capacity >= 1 contract",
            len(with_capacity),
            "events",
            "fractional quotes below 1 contract are not liquidity",
        ),
        Stage(
            "fee_gate",
            "Clears the fee gate",
            len(clears_fee),
            "events",
            "cost + taker fee on every leg < 100c",
        ),
        Stage("actionable", "Actionable", len(actionable), "events"),
    ]


def terminates_at(stages: list[Stage]) -> Stage | None:
    """The first stage whose count is zero -- where the funnel dies."""
    for stage in stages:
        if stage.count == 0:
            return stage
    return None


def as_dict(stages: list[Stage]) -> list[dict[str, Any]]:
    return [
        {
            "key": s.key,
            "label": s.label,
            "count": s.count,
            "unit": s.unit,
            "note": s.note,
        }
        for s in stages
    ]
