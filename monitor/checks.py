"""Correctness checks the monitor must pass before it is allowed to report.

Every function here exists because its absence produced a confident wrong answer
during the Phase 0.5 investigation. The false positives are shipped as test
fixtures in ``tests/test_monitor.py``; if a change to this module lets any of
them through, the change is wrong.

See ``docs/NEGATIVE_RESULT.md`` -> "Methodological errors caught".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

D = Decimal

# A quote for less than one contract is not liquidity. Kalshi contracts trade
# down to 0.01, so a leg can display an offer worth nine hundredths of a cent.
MIN_TRADEABLE_SIZE = D(1)

# Multivariate parlay markets are generated one per requested combination and
# quoted through RFQ rather than resting on a book. 26,852 of 27,000 sampled
# open markets were these.
RFQ_SHELL_PREFIX = "KXMVE"


def is_rfq_shell(ticker: str) -> bool:
    return ticker.startswith(RFQ_SHELL_PREFIX)


def underlying_shape(subtitle: str) -> str:
    """Subtitle with every number stripped, identifying the underlying.

    Sports events list markets for both competitors -- at the same strike, and
    also at different strikes, which defeats a duplicate-strike filter alone.
    Two legs belong to the same ladder only if this matches.
    """
    return re.sub(r"[\d,.\$\-+]+", "#", subtitle).strip()


def tradeable_size(size: Any) -> D:
    """Size floored to whole contracts. Anything under 1 is no liquidity."""
    value = D(str(size or 0))
    return value if value >= MIN_TRADEABLE_SIZE else D(0)


@dataclass(frozen=True)
class PartitionResult:
    verified: bool
    reason: str
    granularity: D | None = None

    def __bool__(self) -> bool:
        return self.verified


def verify_partition(legs: list[dict]) -> PartitionResult:
    """True only if the legs tile the real line with no gap and no overlap.

    Bound semantics differ by strike type, which is what made this subtle:

        less(C)        covers (-inf, C)   -- C exclusive
        between[F, C]  covers [F, C]      -- both inclusive
        greater(F)     covers (F, +inf)   -- F exclusive

    So a valid tiling has zero-width joins at both open ends and exactly one
    granularity step between adjacent interior buckets. Granularity is inferred,
    never assumed to be 0.01 -- US GDP tiles at 0.1 percentage points.

    Anything that is not a range partition -- listed candidate subsets, nested
    cumulative horizons, complementary pairs -- returns False. Those are the
    partition-resemblance traps and every one of them produced a spurious edge.
    """
    if len(legs) < 2:
        return PartitionResult(False, "fewer than two legs")

    lows = [m for m in legs if m.get("strike_type") == "less"]
    mids = sorted(
        (m for m in legs if m.get("strike_type") == "between"),
        key=lambda m: D(str(m["floor_strike"])),
    )
    highs = [m for m in legs if m.get("strike_type") == "greater"]

    if len(lows) + len(mids) + len(highs) != len(legs):
        return PartitionResult(False, "contains legs that are not range buckets")
    if len(lows) != 1 or len(highs) != 1:
        return PartitionResult(False, "no open bucket at one or both ends")
    if not mids:
        return PartitionResult(False, "no interior buckets")

    if D(str(mids[0]["floor_strike"])) != D(str(lows[0]["cap_strike"])):
        return PartitionResult(False, "gap below the first interior bucket")

    steps: set[D] = set()
    cursor = D(str(mids[0]["cap_strike"]))
    for m in mids[1:]:
        steps.add(D(str(m["floor_strike"])) - cursor)
        cursor = D(str(m["cap_strike"]))
    if len(steps) != 1:
        return PartitionResult(False, f"interior joins disagree: {sorted(steps)}")
    if D(str(highs[0]["floor_strike"])) != cursor:
        return PartitionResult(False, "gap above the last interior bucket")

    step = steps.pop()
    if step <= 0:
        return PartitionResult(False, f"buckets overlap by {-step}")

    # Consistency alone is not enough. With only two interior buckets there is
    # exactly one join, so ANY value would look "consistent" -- a 0.05 hole
    # simply reads as a 0.06 granularity. Pin the step to the precision the
    # strikes are themselves quoted at: a valid tiling joins at exactly one unit
    # of the underlying's reporting grid, never more.
    grid = _quoted_grid(legs)
    if step != grid:
        return PartitionResult(
            False,
            f"join of {step} does not match the {grid} grid the strikes are quoted on",
        )
    return PartitionResult(True, "tiles the line", step)


def _quoted_grid(legs: list[dict]) -> D:
    """Smallest unit the strike values are expressed in, e.g. 0.01 or 0.1."""
    places = 0
    for m in legs:
        for key in ("floor_strike", "cap_strike"):
            raw = m.get(key)
            if raw in (None, ""):
                continue
            exponent = D(str(raw)).normalize().as_tuple().exponent
            places = max(places, -int(exponent) if isinstance(exponent, int) else 0)
    return D(1).scaleb(-places)


def same_ladder(legs: list[dict]) -> bool:
    """True if the legs form one single-underlying threshold ladder."""
    if len(legs) < 2:
        return False
    strikes = [D(str(m["floor_strike"])) for m in legs]
    if len(set(strikes)) != len(strikes):
        return False  # both competitors quoted at the same strike
    return len({underlying_shape(m.get("yes_sub_title", "")) for m in legs}) == 1


def annualized_return(gross_cents: D, cost_cents: D, years: D, verified: bool) -> D | None:
    """Annualized return, or None when the structure is not a verified partition.

    **Suppression is mandatory, not discretionary.** Nested cumulative horizons
    read as partitions annualize at 884% and 4,367%. A number that large in a
    log file will be screenshotted and believed by someone without the context,
    so it must never be computed for an unverified structure in the first place.
    """
    if not verified:
        return None
    if cost_cents <= 0 or years <= 0:
        return None
    return (gross_cents / cost_cents) / years


def persisted(value_a: Any, value_b: Any) -> bool:
    """Decimal equality across two snapshots.

    Float comparison produced three phantom violations on exactly-equal prices.
    Everything crossing this boundary is compared as Decimal.
    """
    if value_a is None or value_b is None:
        return False
    return D(str(value_a)) == D(str(value_b))
