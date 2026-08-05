"""Threshold evaluation against the immutable baseline.

Record everything, alert on little. The archive is the asset; the alerts exist
only to say "the description in the memo has stopped being true."
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

D = Decimal

FOOTER = "An alert is a prompt to re-read the memo, not to trade."

# fee_type values the fee model knows how to price. Anything else means the
# exchange has introduced a structure we have not analysed -- including, in
# particular, a maker rebate, which is a different economic object from a
# maker fee of zero and would need its own analysis.
KNOWN_FEE_TYPES = {"quadratic", "quadratic_with_maker_fees", "flat", "(none)", ""}

FEE_FREE_COUNT_DELTA = 3
SPREAD_COMPRESSION_CENTS = D(2)

# The annualized return that would have changed the Part 0 close decision.
ANNUALIZED_ALERT_PCT = D(20)


@dataclass(frozen=True)
class Alert:
    trigger: str
    baseline: str
    current: str
    memo_section: str

    def render(self) -> str:
        return (
            f"[{self.trigger}]\n"
            f"  baseline: {self.baseline}\n"
            f"  current:  {self.current}\n"
            f"  see:      docs/NEGATIVE_RESULT.md -> {self.memo_section}\n"
            f"  {FOOTER}"
        )


def _get(d: dict, *path: str, default: Any = None) -> Any:
    cur: Any = d
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


@dataclass(frozen=True)
class BelowParResult:
    """Split of below-par detections into what pushes and what is only recorded.

    **Suppression applies to the push, never to the record.** Every sub-floor
    detection stays in `suppressed`, is written to the time series, and is shown
    on the trigger board as a rolling count. A spike in the suppressed count is
    itself a signal even when no individual detection clears -- which is the
    guard against a threshold quietly hiding a real change.
    """

    pushed: list[dict]
    suppressed: list[dict]
    known_events: set[str]

    @property
    def reasons(self) -> Counter:
        return Counter(p["suppressed_because"] for p in self.suppressed)


def classify_below_par(
    baseline: dict, current: dict, bands: dict | None = None
) -> BelowParResult:
    """Decide which below-par partitions are worth waking someone for.

    Three ways through, in order of authority:

    1. **Annualized return >= 20%/yr** pushes regardless of size. A genuinely
       high-return structure is news at any capacity -- this branch is unfloored
       on purpose.
    2. **New structure** pushes only if capacity x edge clears the dollar floor.
       Live evidence: GDP partitions drift across par week to week, so "new"
       alone fired on $0.30 of capacity.
    3. **Outside the structure's own observed band** counts as new. Bands are
       keyed per event, not per series: a series-keyed band described several
       contracts with different fair values at once. An event with fewer than
       the minimum observations has no band, reports UNKNOWN, and falls back to
       the dollar floor alone.
    """
    from scanner.history import MIN_DOLLAR_VALUE, dollar_value

    known_events = {
        p["event"]
        for p in _get(baseline, "verified_partitions", "fee_free_detail", default=[])
        if p["below_par"]
    }
    pushed: list[dict] = []
    suppressed: list[dict] = []

    for raw in _get(current, "verified_partitions", "fee_free_detail", default=[]):
        if not (raw["below_par"] and raw["tradeable"]):
            continue
        p = dict(raw)
        cost = D(p["cost_cents"])
        capacity = D(p["capacity_contracts"])
        value = dollar_value(cost, capacity)
        ann = D(p["annualized_pct"]) if p["annualized_pct"] is not None else D(0)
        band = (bands or {}).get(p["event"])

        p["dollar_value"] = str(value)
        p["band_state"] = band.state if band else "UNKNOWN"
        p["band_observations"] = band.observations if band else 0

        if ann >= ANNUALIZED_ALERT_PCT:
            p["pushed_because"] = f"annualized {ann}%/yr >= {ANNUALIZED_ALERT_PCT}%"
            pushed.append(p)
            continue

        is_new = p["event"] not in known_events
        if band is not None and band.known and band.is_outside(cost):
            is_new = True

        if not is_new:
            p["suppressed_because"] = "already below par at baseline, and inside its band"
            suppressed.append(p)
        elif value < MIN_DOLLAR_VALUE:
            p["suppressed_because"] = f"capacity x edge ${value} < ${MIN_DOLLAR_VALUE} floor"
            suppressed.append(p)
        else:
            p["pushed_because"] = f"new structure worth ${value}"
            pushed.append(p)

    return BelowParResult(pushed=pushed, suppressed=suppressed, known_events=known_events)


def evaluate(
    baseline: dict,
    current: dict,
    fee_changes: dict | None = None,
    bands: dict | None = None,
) -> list[Alert]:
    alerts: list[Alert] = []

    # --- tick structure mix -------------------------------------------------
    base_ticks = _get(baseline, "tick_structure", default={})
    cur_ticks = _get(current, "tick_structure", default={})
    if set(base_ticks) != set(cur_ticks):
        alerts.append(
            Alert(
                "tick structure set changed",
                ", ".join(sorted(base_ticks)),
                ", ".join(sorted(cur_ticks)),
                "Finding 4 - the falsifier fired",
            )
        )
    else:
        for name in sorted(base_ticks):
            b = D(base_ticks[name]["share_pct"])
            c = D(cur_ticks[name]["share_pct"])
            if abs(c - b) >= 5:
                alerts.append(
                    Alert(
                        f"tick structure mix shifted: {name}",
                        f"{b}% of markets",
                        f"{c}% of markets",
                        "Finding 4 - the falsifier fired",
                    )
                )

    # --- fee structure ------------------------------------------------------
    unknown = {t for t in _get(current, "fee_types", default={}) if t not in KNOWN_FEE_TYPES}
    if unknown:
        alerts.append(
            Alert(
                "unrecognised fee_type - possible maker rebate or new schedule",
                ", ".join(sorted(KNOWN_FEE_TYPES - {"", "(none)"})),
                ", ".join(sorted(unknown)),
                "What would change the conclusion",
            )
        )
    if fee_changes:
        scheduled = fee_changes.get("series", []) + fee_changes.get("events", [])
        if scheduled:
            alerts.append(
                Alert(
                    "exchange has published scheduled fee changes",
                    "no scheduled changes",
                    f"{len(scheduled)} scheduled change(s): {scheduled[:5]}",
                    "What would change the conclusion",
                )
            )

    # --- spread compression in the main segment -----------------------------
    base_median = _get(baseline, "spread_by_segment", "tick_structure", "linear_cent", "median")
    cur_median = _get(current, "spread_by_segment", "tick_structure", "linear_cent", "median")
    if cur_median is not None and D(cur_median) <= SPREAD_COMPRESSION_CENTS:
        alerts.append(
            Alert(
                "linear_cent median spread compressed to the alert threshold",
                f"{base_median}c",
                f"{cur_median}c",
                "Finding 1 - the spread dominates the fee",
            )
        )

    # --- fee-free universe --------------------------------------------------
    base_n = _get(baseline, "fee_free", "n_series_with_open_markets", default=0)
    cur_n = _get(current, "fee_free", "n_series_with_open_markets", default=0)
    if abs(cur_n - base_n) >= FEE_FREE_COUNT_DELTA:
        base_set = set(_get(baseline, "fee_free", "series", default=[]))
        cur_set = set(_get(current, "fee_free", "series", default=[]))
        alerts.append(
            Alert(
                "fee-free series count changed materially",
                f"{base_n} series",
                f"{cur_n} series; added {sorted(cur_set - base_set)}, "
                f"removed {sorted(base_set - cur_set)}",
                "What would change the conclusion",
            )
        )

    # --- the two findings that would reopen the file ------------------------
    # A below-par tradeable partition is NOT by itself news: the baseline
    # already contains one (KXGDPYEAR-29, 95c, 10 contracts, 1.47%/yr), which
    # Part 0 examined and dismissed on return and capacity. Firing on "any"
    # would page every week about a known non-opportunity, which is the failure
    # this monitor is explicitly designed to avoid. Fire when the structure is
    # new, or when a known one crosses the return threshold that would have
    # changed the Part 0 decision.
    result = classify_below_par(baseline, current, bands)
    if result.pushed:
        alerts.append(
            Alert(
                "verified partition below par, tradeable, and newsworthy",
                f"{len(result.known_events)} known below-par partitions, max 1.47%/yr, "
                f"capacities 10 and 0.01 contracts",
                "; ".join(
                    f"{p['event']} at {p['cost_cents']}c, {p['capacity_contracts']} contracts, "
                    f"{p['annualized_pct']}%/yr, ${p['dollar_value']} total"
                    for p in result.pushed
                ),
                "Finding 5 - the two below-par results",
            )
        )

    tripwire = _get(current, "deci_cent_fee_free_tripwire", "n_below_par", default=0)
    if tripwire:
        alerts.append(
            Alert(
                "deci-cent AND fee-free market priced below par",
                "0 of 61 markets below par",
                f"{tripwire} partition(s) below par out of "
                f"{_get(current, 'deci_cent_fee_free_tripwire', 'n_markets', default=0)} markets",
                "The cleanest single result",
            )
        )

    return alerts


def render(alerts: list[Alert]) -> str:
    if not alerts:
        return "No alerts. Baseline holds."
    body = "\n\n".join(a.render() for a in alerts)
    return f"{len(alerts)} alert(s)\n\n{body}"
