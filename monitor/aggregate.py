"""Streaming aggregation for the exchange-wide sweep.

``metrics.compute`` materialises every open market and then maps over the list.
At ~77,000 markets that is 576 MB resident, and Python does not hand it back, so
the peak becomes the steady state. This module folds each market into bounded
aggregates as it arrives, so the page can be discarded.

**``metrics.compute`` remains the reference implementation.** It is not deleted
and not wrapped: it is the independent standard this module is checked against,
because a streaming rewrite that also defines its own notion of correct is a
check sharing a failure mode with its subject. ``tests/test_aggregate.py``
asserts the two produce byte-identical JSON from the committed snapshots.

What stays resident
-------------------

Bounded by the *price grid*:
    Spread distributions, as exact value->count maps (see below).

Bounded by *series* (~14) or *category* (~12):
    Fee types, tick structures, the fee-free set, per-segment spread maps.

Bounded by *event* count (~8,800), not market count:
    One small record per event -- leg count, whether any leg is an RFQ shell,
    whether every leg is two-sided -- plus the event identifier set that
    ``n_events`` is a count of. Identifiers only; no market objects.

Bounded by *partition candidates* (~21,000 legs, 30% of the sweep):
    Compact leg tuples for events that can still be range partitions.
    ``verify_partition`` rejects any event containing a leg whose strike type is
    not one of ``less`` / ``between`` / ``greater``, so the first such leg
    disqualifies the event and its retained legs are dropped immediately. This
    is the one structure that scales with market count, and it is a projection
    of eleven short fields rather than the ~40-key API object.

Why an exact value->count map and not a t-digest or a fixed-width histogram
--------------------------------------------------------------------------

The gate is byte-identical reproduction of the committed baseline. A t-digest is
approximate by construction, so it cannot clear that gate at any compression
setting -- it was ruled out on the requirement, not on taste.

A fixed-width histogram over 0-100c at 0.1c would be exact *if* every spread
lands on the grid and inside the range. Both are true today (measured: 230
distinct values across 44,453 two-sided books, all exact multiples of 0.1c, all
within [0, 100]) -- but the histogram has to assume it, and its failure mode
when the assumption breaks is to clamp, which is silent.

So the spread distribution is kept as an exact ``Decimal -> count`` map. It is a
lossless compression of the multiset, so percentile and median arithmetic is
identical rather than merely close, and nothing is assumed about the grid: an
off-grid value simply becomes another key. The structure is still bounded --
1,001 possible on-grid values -- and :data:`MAX_DISTINCT_SPREADS` guards the
case where that assumption fails, loudly, instead of growing quietly.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from decimal import Decimal
from typing import Any

from monitor.checks import is_rfq_shell
from monitor.metrics import SEGMENT_KEYS, partition_report

D = Decimal

#: A 0-100c range on a 0.1c grid has 1,001 possible values. Beyond a generous
#: multiple of that, the grid assumption has broken and the "bounded" structure
#: is no longer bounded -- which is exactly the failure a memory fix must not
#: hide. Raising rather than truncating: a silent cap would make the aggregate
#: wrong and the process look healthy.
MAX_DISTINCT_SPREADS = 5000

#: Alert well below the hard ceiling. 230 distinct values across 44,453 books is
#: the measured baseline, so 2,000 is an order of magnitude of headroom and
#: still leaves 2.5x before :data:`MAX_DISTINCT_SPREADS` stops the process.
#:
#: **Key-count growth is a structural signal, not only a memory one.** A tick
#: structure change is what would put new values on the grid, so this is the
#: first place in the system such a change would surface -- earlier than the
#: tick-structure share thresholds, which need 5pp of the whole exchange to
#: move.
ALERT_DISTINCT_SPREADS = 2000

#: The deci-cent AND fee-free intersection held 61 markets at baseline. Two
#: orders of magnitude of headroom, then a raise -- because at that size the
#: interesting event is the intersection, not the memory.
MAX_INTERSECTION_LEGS = 20000

#: verify_partition rejects any event holding a leg that is not a range bucket,
#: so one such leg disqualifies the whole event.
RANGE_STRIKES = frozenset({"less", "between", "greater"})

#: The fields partition_report, _as_legs and funnel.build read off a row. Held
#: as a tuple; expanded back into dicts only for surviving candidates, so the
#: same partition_report runs on the same shape.
LEG_FIELDS = (
    "event_ticker",
    "ticker",
    "strike_type",
    "floor_strike",
    "cap_strike",
    "underlying",
    "ask_yes_cents",
    "ask_size",
    "close_time",
    "fee_type",
    "fee_multiplier",
    "tick_structure",
    "two_sided",
)


class SpreadDistribution:
    """Exact multiset of spreads, stored as value -> count.

    Precondition: equal-valued spreads share one string form. ``to_row`` formats
    every price with ``f"{x:.4f}"``, so they do. It matters because
    ``Decimal("0.5") == Decimal("0.5000")`` -- the map would merge them and
    render whichever form it stored, while the reference's ``sorted()`` renders
    whichever arrived first. Neither is canonical, so this is a property of the
    input, asserted in ``tests/test_aggregate.py`` rather than assumed.
    """

    __slots__ = ("counts",)

    def __init__(self) -> None:
        self.counts: dict[D, int] = defaultdict(int)

    def add(self, spread: D) -> None:
        self.counts[spread] += 1
        if len(self.counts) > MAX_DISTINCT_SPREADS:
            raise ValueError(
                f"spread distribution exceeded {MAX_DISTINCT_SPREADS} distinct values; "
                "the price grid assumption this structure's bound rests on has broken"
            )

    @property
    def n(self) -> int:
        return sum(self.counts.values())

    def _ordered(self) -> list[tuple[D, int]]:
        return sorted(self.counts.items())

    def _value_at(self, ordered: list[tuple[D, int]], index: int) -> D:
        """The value that would sit at `index` in the fully sorted sequence."""
        seen = 0
        for value, count in ordered:
            seen += count
            if index < seen:
                return value
        return ordered[-1][0]

    def percentile(self, q: int) -> D:
        """Nearest rank, no interpolation -- the same arithmetic as metrics._pct."""
        n = self.n
        ordered = self._ordered()
        idx = max(0, min(n - 1, int(round(q / 100 * (n - 1)))))
        return self._value_at(ordered, idx)

    def median(self) -> D:
        """statistics.median semantics, including the even-length mean."""
        n = self.n
        ordered = self._ordered()
        if n % 2:
            return self._value_at(ordered, n // 2)
        lo = self._value_at(ordered, n // 2 - 1)
        hi = self._value_at(ordered, n // 2)
        return (lo + hi) / 2

    def sub_1c(self) -> int:
        return sum(count for value, count in self.counts.items() if value < 1)

    def stats(self) -> dict[str, Any]:
        n = self.n
        return {
            "n": n,
            "p10": str(self.percentile(10)),
            "median": str(self.median()),
            "p90": str(self.percentile(90)),
            "sub_1c_share_pct": str((D(self.sub_1c()) * 100 / n).quantize(D("0.1"))),
        }


class _EventState:
    """Per-event running state. Bounded by event count, not market count."""

    __slots__ = ("n_legs", "has_rfq", "all_two_sided", "legs")

    def __init__(self) -> None:
        self.n_legs = 0
        self.has_rfq = False
        self.all_two_sided = True
        #: None once the event can no longer be a range partition.
        self.legs: list[tuple] | None = []

    def disqualify(self) -> None:
        self.legs = None


class SweepAggregate:
    """Folds rows one at a time into the same dict ``metrics.compute`` returns."""

    def __init__(self) -> None:
        self.n_markets = 0
        self.n_two_sided = 0
        self.rfq_shells = 0
        self.event_tickers: set[str] = set()
        self.events: dict[str, _EventState] = {}

        self.spread_all = SpreadDistribution()
        self.spread_by_segment: dict[str, dict[str, SpreadDistribution]] = {
            key: defaultdict(SpreadDistribution) for key in SEGMENT_KEYS
        }

        self.tick_structures: Counter[str] = Counter()
        self.fee_types: Counter[str] = Counter()
        self.fee_free_series: set[str] = set()
        self.fee_free_markets = 0

        #: The deci-cent AND fee-free intersection: 61 markets at baseline, and
        #: the named tripwire. Retained whole rather than derived from the
        #: partition candidates, because the reference verifies the intersection
        #: *subset* of an event's legs -- an event disqualified as a whole could
        #: still contribute a verifiable subset here, and reproducing that
        #: exactly matters more than the handful of bytes.
        self.intersection_legs: list[tuple] = []
        self.intersection_markets = 0

    # ------------------------------------------------------------------ fold --

    def add(self, row: dict[str, Any]) -> None:
        self.n_markets += 1
        event = row["event_ticker"]
        self.event_tickers.add(event)

        if is_rfq_shell(row["ticker"]):
            self.rfq_shells += 1

        self.tick_structures[row["tick_structure"] or "(none)"] += 1
        self.fee_types[row["fee_type"] or "(none)"] += 1
        if row["fee_multiplier"] == "0":
            self.fee_free_series.add(row["series_ticker"])
            self.fee_free_markets += 1

        if row["tick_structure"] == "deci_cent" and row["fee_multiplier"] == "0":
            self.intersection_markets += 1
            if len(self.intersection_legs) >= MAX_INTERSECTION_LEGS:
                raise ValueError(
                    f"deci-cent AND fee-free intersection exceeded "
                    f"{MAX_INTERSECTION_LEGS} markets; it was 61 at baseline, and an "
                    "intersection that large is the tripwire firing, not a sweep to "
                    "quietly truncate"
                )
            self.intersection_legs.append(tuple(row[f] for f in LEG_FIELDS))

        two_sided = row["two_sided"] == "True"
        if two_sided:
            self.n_two_sided += 1
            spread = D(row["spread_cents"])
            self.spread_all.add(spread)
            for key in SEGMENT_KEYS:
                self.spread_by_segment[key][row[key] or "(unmapped)"].add(spread)

        state = self.events.get(event)
        if state is None:
            state = self.events[event] = _EventState()
        state.n_legs += 1
        if not two_sided:
            state.all_two_sided = False
        if is_rfq_shell(row["ticker"]):
            state.has_rfq = True
            state.disqualify()
        elif state.legs is not None:
            if row["strike_type"] not in RANGE_STRIKES:
                # verify_partition would reject the whole event for this leg.
                state.disqualify()
            else:
                state.legs.append(tuple(row[f] for f in LEG_FIELDS))

    def fold(self, rows) -> SweepAggregate:
        for row in rows:
            self.add(row)
        return self

    # ---------------------------------------------------------------- result --

    def candidate_legs(self) -> dict[str, list[dict[str, Any]]]:
        """Surviving candidates, expanded to the row shape partition_report reads."""
        out: dict[str, list[dict[str, Any]]] = {}
        for event, state in self.events.items():
            if state.legs is None or len(state.legs) < 2:
                continue
            out[event] = [dict(zip(LEG_FIELDS, leg, strict=True)) for leg in state.legs]
        return out

    def _segment_stats(self) -> dict[str, dict[str, Any]]:
        return {
            key: {
                name: dist.stats()
                for name, dist in sorted(groups.items())
                if dist.n >= 30
            }
            for key, groups in self.spread_by_segment.items()
        }

    def result(self) -> dict[str, Any]:
        candidates = self.candidate_legs()
        flat = [leg for legs in candidates.values() for leg in legs]

        partitions = partition_report(flat)
        fee_free_partitions = [p for p in partitions if p["fee_multiplier"] == "0"]
        costs = [D(p["cost_cents"]) for p in fee_free_partitions]

        intersection_rows = [
            dict(zip(LEG_FIELDS, leg, strict=True)) for leg in self.intersection_legs
        ]
        intersection_partitions = [p for p in partition_report(intersection_rows) if p["event"]]

        return {
            "universe": {
                "n_markets": self.n_markets,
                "n_two_sided": self.n_two_sided,
                "n_events": len(self.event_tickers),
                "rfq_shells_in_sweep": self.rfq_shells,
            },
            "spread_exchange_wide": self.spread_all.stats(),
            "spread_by_segment": self._segment_stats(),
            "tick_structure": {
                name: {
                    "n": n,
                    "share_pct": str((D(n) * 100 / self.n_markets).quantize(D("0.1"))),
                }
                for name, n in sorted(self.tick_structures.items())
            },
            "fee_free": {
                "n_series_with_open_markets": len(self.fee_free_series),
                "series": sorted(self.fee_free_series),
                "n_markets": self.fee_free_markets,
            },
            "fee_types": dict(sorted(self.fee_types.items())),
            "deci_cent_fee_free_tripwire": {
                "n_markets": self.intersection_markets,
                "n_verified_partitions": len(intersection_partitions),
                "n_below_par": sum(1 for p in intersection_partitions if p["below_par"]),
                "partitions": intersection_partitions,
            },
            "verified_partitions": {
                "n_total": len(partitions),
                "n_fee_free": len(fee_free_partitions),
                "n_below_par": sum(1 for p in fee_free_partitions if p["below_par"]),
                "n_below_par_tradeable": sum(
                    1 for p in fee_free_partitions if p["below_par"] and p["tradeable"]
                ),
                "fee_free_cost_median": (
                    str(statistics.median(sorted(costs))) if costs else None
                ),
                "fee_free_cost_p10": str(_pct_list(costs, 10)) if costs else None,
                "fee_free_detail": fee_free_partitions,
            },
        }


def _pct_list(values: list[D], q: int) -> D:
    """metrics._pct, over the handful of partition costs. Not a hot path."""
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, int(round(q / 100 * (len(ordered) - 1)))))
    return ordered[idx]
