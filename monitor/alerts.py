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


#: --- PROVISIONAL, flagged rather than settled -----------------------------
#: A scheduled fee change is material on *breadth* alone once it touches more
#: than this share of the active series in any one category.
#:
#: **An absolute count was tried first and failed within a week.** The threshold
#: was 15 series, set from a single observation of 11; six days later the same
#: routine MLB batch spanned 19. On an exchange whose population moves ~10% a
#: day, an absolute count is a moving target and every one of them will fail
#: this way on the same timescale. Proportions and rates survive; counts do not.
#:
#: Category alone is not the replacement either. It misses a *single-category*
#: schedule revision: 19 of ~750 active Sports series is noise, and 19 of 20
#: crypto series would be news. Identical count, opposite meaning -- which is
#: exactly what a proportion distinguishes and a count cannot.
#:
#: Observed, both routine, both single-category, against active series in the
#: category (series with open markets in the same sweep):
#:
#:   2026-08-05  100 changes, 11 MLB series   =  1.47% of active Sports
#:   2026-08-11  100 changes, 19 MLB series   =  2.54% of active Sports
#:
#: 10% leaves roughly 4x headroom over the observed maximum while still being
#: unambiguously "a lot of that product line". **n = 2, both from one category.**
#: Known weakness: a small category inflates the share for the same absolute
#: noise -- one change in a 2-series category is 50%. No floor is added, because
#: a floor is an absolute count and that is the thing being removed.
BREADTH_CATEGORY_SHARE_PCT = D(10)

#: A change spanning more than one category is material regardless of size.
#: Fee schedules are administered per product line; a revision that crosses
#: category boundaries is a policy change, not a listing operation.
BREADTH_CATEGORY_THRESHOLD = 1


def _series_category(series_ticker: str, categories: dict[str, str]) -> str:
    return categories.get(series_ticker, "(unknown)")


def classify_fee_changes(
    fee_changes: dict,
    fee_free_series: set[str],
    series_categories: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Split scheduled fee changes into material and routine.

    **This narrowing rests on one observation and is flagged as such.** Measured
    2026-08-05: the endpoints returned 100 scheduled changes, all of them
    per-event MLB overrides moving individual games onto the standard schedule
    (`fee_multiplier_override: 1`, `quadratic` or `quadratic_with_maker_fees`).
    Zero touched a fee-free series; zero set a multiplier to 0.

    Firing on *any* scheduled change would therefore fire on every sweep, and a
    trigger that fires every sweep is one that gets muted — which is
    unacceptable for the closest thing this system has to a fee-coefficient
    alarm. So the alert is on material changes only.

    Material means one of:

    * it touches a series currently in the fee-free universe — that series would
      leave it
    * it sets ``fee_multiplier_override`` to 0 — a series would *join* it
    * it introduces a ``fee_type`` the fee model does not recognise
    * **the batch is broad** — see below

    Breadth is an independent criterion because the other three are per-change
    and cannot see it. A multiplier moving to 1 on a fee-charging series reads
    routine one change at a time; the same change repeated across a hundred
    series in several categories is a schedule revision, which is the event this
    trigger exists for. Breadth is therefore a property of the whole batch,
    decided once and applied to every change in it.

    **The routine count is still reported** in `n_routine` and on the health
    panel, so nothing is hidden and a change in the routine volume is visible.
    Revisit if the material rate proves noisy; one observation is not a
    distribution.
    """
    scheduled = list(fee_changes.get("series") or []) + list(fee_changes.get("events") or [])
    categories = series_categories or {}

    touched_series = {c.get("series_ticker") for c in scheduled if c.get("series_ticker")}
    touched_categories = {_series_category(s, categories) for s in touched_series}

    # Breadth is a property of the whole batch, not of any one change, so it is
    # decided before the per-change loop and applied to all of them.
    #
    # The denominator is *active* series -- those with open markets in this same
    # sweep -- rather than the registry, because the registry lists thousands of
    # series with nothing listed and would understate every share. It is also the
    # figure the scanner already has, with no extra call.
    category_size: Counter[str] = Counter(categories.values())
    touched_per_category: Counter[str] = Counter(
        _series_category(s, categories) for s in touched_series
    )
    shares: dict[str, D] = {}
    for category, touched in touched_per_category.items():
        size = category_size.get(category, 0)
        if category == "(unknown)" or not size:
            continue
        shares[category] = (D(touched) * 100 / D(size)).quantize(D("0.01"))

    over_share = {c: s for c, s in shares.items() if s > BREADTH_CATEGORY_SHARE_PCT}
    broad_by_share = bool(over_share)
    broad_by_category = len(touched_categories - {"(unknown)"}) > BREADTH_CATEGORY_THRESHOLD
    broad = broad_by_share or broad_by_category

    material, routine = [], []
    for change in scheduled:
        multiplier = change.get("fee_multiplier_override")
        fee_type = change.get("fee_type_override")
        is_material = (
            broad
            or change.get("series_ticker") in fee_free_series
            or multiplier == 0
            or str(multiplier) == "0"
            or (fee_type is not None and fee_type not in KNOWN_FEE_TYPES)
        )
        (material if is_material else routine).append(change)

    reasons = []
    if broad_by_share:
        reasons.append(
            "; ".join(
                f"{share}% of active {category} series touched, over the "
                f"{BREADTH_CATEGORY_SHARE_PCT}% threshold"
                for category, share in sorted(over_share.items())
            )
        )
    if broad_by_category:
        reasons.append(
            f"spans {len(touched_categories - {'(unknown)'})} categories: "
            f"{sorted(touched_categories - {'(unknown)'})}"
        )

    return {
        "material": material,
        "routine": routine,
        "n_material": len(material),
        "n_routine": len(routine),
        "n_total": len(scheduled),
        "n_series_touched": len(touched_series),
        "categories_touched": sorted(touched_categories),
        "category_shares_pct": {c: str(s) for c, s in sorted(shares.items())},
        "broad": broad,
        "breadth_reasons": reasons,
    }


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
    series_categories: dict[str, str] | None = None,
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
    # **Silence from this trigger must mean "checked, nothing scheduled", never
    # "unknown".** The fee-change endpoints are one of only two programmatic
    # proxies for a change to the 0.07 coefficient -- the single structural
    # change that would most directly invalidate the negative result -- and the
    # coefficient itself lives in a PDF that cannot be polled at all. So a
    # failed poll is itself an alert.
    outcome = _get(current, "fee_changes_outcome", default=None)
    if outcome and outcome.get("state") == "failed":
        alerts.append(
            Alert(
                "fee-change endpoint could not be polled - this trigger did not run",
                "polled every sweep; silence means nothing scheduled",
                str(outcome.get("reason", "unknown error")),
                "What would change the conclusion",
            )
        )
    elif fee_changes is not None:
        split = classify_fee_changes(
            fee_changes,
            set(_get(current, "fee_free", "series", default=[])),
            series_categories,
        )
        if split["material"]:
            why = (
                "; ".join(split["breadth_reasons"])
                if split["broad"]
                else "affects the fee-free universe, sets a multiplier to 0, "
                "or introduces an unknown fee_type"
            )
            alerts.append(
                Alert(
                    "exchange has published material scheduled fee changes",
                    "routine per-event overrides only, within one category",
                    f"{len(split['material'])} material change(s) of "
                    f"{split['n_total']} scheduled across "
                    f"{split['n_series_touched']} series - {why}",
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
