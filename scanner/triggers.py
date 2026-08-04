"""Distance-to-fire for the seven alert triggers.

The dashboard's hero is a falsification surface, so every trigger has to answer
"how close is this to firing?" in a way that is *measured* rather than invented.

The normalisation is stated rather than assumed:

    proximity = (baseline_value - current_value) / (baseline_value - threshold)

clamped to [0, 1]. **0% means unchanged from the committed baseline; 100% means
firing.** That gives the number an unambiguous meaning in both directions, and
it degrades honestly: when the baseline already sits on the threshold there is
no denominator, so the trigger reports `proximity=None` and the UI renders
NO DATA rather than a fabricated percentage.

Binary triggers (a set changed, an unrecognised value appeared) have no
continuum. They report 0 or 100 and say so.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from monitor.alerts import (
    ANNUALIZED_ALERT_PCT,
    FEE_FREE_COUNT_DELTA,
    KNOWN_FEE_TYPES,
    SPREAD_COMPRESSION_CENTS,
    _get,
)

D = Decimal


@dataclass(frozen=True)
class Trigger:
    key: str
    label: str
    #: What the trigger is watching, in its own units.
    value: str | None
    unit: str
    baseline: str | None
    threshold: str
    #: Plain-language statement of the condition, shown in the UI.
    condition: str
    #: 0-100, or None when the system cannot measure a distance.
    proximity_pct: str | None
    fired: bool
    memo_section: str
    #: Why proximity is None, when it is.
    no_data_reason: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def has_distance(self) -> bool:
        return self.proximity_pct is not None


def _proximity(value: D, baseline: D, threshold: D) -> tuple[str | None, str]:
    """Fraction of the way from baseline to threshold, as a percentage string."""
    span = baseline - threshold
    if span == 0:
        return None, "baseline already sits on the threshold; no distance to measure"
    fraction = (baseline - value) / span
    clamped = max(D(0), min(D(1), fraction))
    return str((clamped * 100).quantize(D("0.1"))), ""


def _binary(fired: bool) -> str:
    return "100.0" if fired else "0.0"


def evaluate(baseline: dict, current: dict, bands: dict | None = None) -> list[Trigger]:
    """One Trigger per alert condition in monitor/alerts.py, in board order."""
    out: list[Trigger] = []

    # 1 --- tick structure -------------------------------------------------
    base_ticks = _get(baseline, "tick_structure", default={})
    cur_ticks = _get(current, "tick_structure", default={})
    set_changed = set(base_ticks) != set(cur_ticks)
    drift = D(0)
    if not set_changed:
        for name in base_ticks:
            moved = abs(D(cur_ticks[name]["share_pct"]) - D(base_ticks[name]["share_pct"]))
            drift = max(drift, moved)
    out.append(
        Trigger(
            key="tick_structure",
            label="Tick structure",
            value=None if set_changed else str(drift),
            unit="pp drift",
            baseline="0",
            threshold="5",
            condition="set changes, or any structure's share moves 5pp",
            proximity_pct=_binary(True) if set_changed else _proximity(D(5) - drift, D(5), D(0))[0],
            fired=set_changed or drift >= 5,
            memo_section="Finding 4 - the falsifier fired",
            detail={"structures": sorted(cur_ticks)},
        )
    )

    # 2 --- fee type -------------------------------------------------------
    unknown = sorted(t for t in _get(current, "fee_types", default={}) if t not in KNOWN_FEE_TYPES)
    out.append(
        Trigger(
            key="fee_type",
            label="Fee type / maker rebate",
            value=", ".join(unknown) if unknown else "none",
            unit="unrecognised types",
            baseline="none",
            threshold="any",
            condition="an unrecognised fee_type appears, including a maker rebate",
            proximity_pct=_binary(bool(unknown)),
            fired=bool(unknown),
            memo_section="What would change the conclusion",
            detail={"known": sorted(KNOWN_FEE_TYPES - {"", "(none)"})},
        )
    )

    # 3 --- scheduled fee changes -----------------------------------------
    scheduled = _get(current, "fee_changes", "count", default=None)
    out.append(
        Trigger(
            key="fee_changes",
            label="Scheduled fee changes",
            value=None if scheduled is None else str(scheduled),
            unit="pending changes",
            baseline="0",
            threshold="any",
            condition="the exchange publishes a pending per-series or per-event fee change",
            proximity_pct=None if scheduled is None else _binary(scheduled > 0),
            fired=bool(scheduled),
            memo_section="What would change the conclusion",
            no_data_reason="" if scheduled is not None else "fee-change endpoints not yet polled",
        )
    )

    # 4 --- spread compression --------------------------------------------
    base_spread = _get(baseline, "spread_by_segment", "tick_structure", "linear_cent", "median")
    cur_spread = _get(current, "spread_by_segment", "tick_structure", "linear_cent", "median")
    prox, reason = (
        _proximity(D(cur_spread), D(base_spread), SPREAD_COMPRESSION_CENTS)
        if cur_spread is not None and base_spread is not None
        else (None, "linear_cent segment absent from this sweep")
    )
    out.append(
        Trigger(
            key="spread",
            label="linear_cent median spread",
            value=cur_spread,
            unit="cents",
            baseline=base_spread,
            threshold=str(SPREAD_COMPRESSION_CENTS),
            condition=f"median spread falls to {SPREAD_COMPRESSION_CENTS}c or below",
            proximity_pct=prox,
            fired=cur_spread is not None and D(cur_spread) <= SPREAD_COMPRESSION_CENTS,
            memo_section="Finding 1 - the spread dominates the fee",
            no_data_reason=reason,
        )
    )

    # 5 --- fee-free universe ---------------------------------------------
    base_n = _get(baseline, "fee_free", "n_series_with_open_markets")
    cur_n = _get(current, "fee_free", "n_series_with_open_markets")
    if base_n is None or cur_n is None:
        prox, reason, delta = None, "fee-free series count unavailable", None
    else:
        delta = abs(cur_n - base_n)
        prox, reason = _proximity(D(FEE_FREE_COUNT_DELTA) - D(delta), D(FEE_FREE_COUNT_DELTA), D(0))
    out.append(
        Trigger(
            key="fee_free_count",
            label="Fee-free series",
            value=None if cur_n is None else str(cur_n),
            unit="series with open markets",
            baseline=None if base_n is None else str(base_n),
            threshold=f"+/-{FEE_FREE_COUNT_DELTA}",
            condition=f"count moves by {FEE_FREE_COUNT_DELTA} or more from baseline",
            proximity_pct=prox,
            fired=delta is not None and delta >= FEE_FREE_COUNT_DELTA,
            memo_section="What would change the conclusion",
            no_data_reason=reason,
            detail={"delta": delta},
        )
    )

    # 6 --- a tradeable below-par partition --------------------------------
    from monitor.alerts import classify_below_par

    result = classify_below_par(baseline, current, bands)
    best_ann = D(0)
    for p in _get(current, "verified_partitions", "fee_free_detail", default=[]):
        if p["below_par"] and p["tradeable"] and p["annualized_pct"] is not None:
            best_ann = max(best_ann, D(p["annualized_pct"]))
    newsworthy = [p["event"] for p in result.pushed]
    band_states = sorted({p.get("band_state", "UNKNOWN") for p in
                          result.pushed + result.suppressed}) or ["UNKNOWN"]
    out.append(
        Trigger(
            key="below_par_partition",
            label="Tradeable below-par partition",
            value=str(best_ann),
            unit="%/yr, best tradeable",
            baseline="1.47",
            threshold=str(ANNUALIZED_ALERT_PCT),
            condition=f"a new one appears, or a known one reaches {ANNUALIZED_ALERT_PCT}%/yr",
            proximity_pct=_binary(True)
            if newsworthy
            else _proximity(ANNUALIZED_ALERT_PCT - best_ann, ANNUALIZED_ALERT_PCT, D(0))[0],
            fired=bool(newsworthy),
            memo_section="Finding 5 - the two below-par results",
            detail={
                "newsworthy": sorted(newsworthy),
                "suppressed_now": len(result.suppressed),
                "suppressed_reasons": dict(result.reasons),
                "band_states": band_states,
            },
        )
    )

    # 7 --- the tripwire ---------------------------------------------------
    partitions = _get(current, "deci_cent_fee_free_tripwire", "partitions", default=[])
    costs = [D(p["cost_cents"]) for p in partitions]
    base_partitions = _get(baseline, "deci_cent_fee_free_tripwire", "partitions", default=[])
    base_costs = [D(p["cost_cents"]) for p in base_partitions]
    if not costs or not base_costs:
        prox, reason, cheapest = None, "tripwire markets absent from this sweep", None
    else:
        cheapest = min(costs)
        prox, reason = _proximity(cheapest, min(base_costs), D(100))
    out.append(
        Trigger(
            key="tripwire",
            label="deci-cent AND fee-free below par",
            value=None if cheapest is None else str(cheapest),
            unit="cents, cheapest basket",
            baseline=str(min(base_costs)) if base_costs else None,
            threshold="100",
            condition="any verified partition in the intersection prices below par",
            proximity_pct=prox,
            fired=any(c < 100 for c in costs),
            memo_section="The cleanest single result",
            no_data_reason=reason,
            detail={"n_markets": _get(current, "deci_cent_fee_free_tripwire", "n_markets")},
        )
    )

    return out


def closest(triggers: list[Trigger]) -> Trigger | None:
    """The trigger nearest to firing, for the hero. None if none is measurable."""
    measurable = [t for t in triggers if t.has_distance]
    if not measurable:
        return None
    return max(measurable, key=lambda t: D(t.proximity_pct or "0"))
