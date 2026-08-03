"""Compute the tracked statistics from a snapshot.

Output is a plain dict of strings and ints, ordered deterministically, so that
``json.dumps(..., sort_keys=True)`` of the same snapshot is byte-identical on
any machine. That property is the monitor's gate: if it cannot reproduce the
committed baseline it cannot detect change.

Decimals are serialised as strings. No floats cross this boundary.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from decimal import Decimal
from typing import Any

from monitor.checks import (
    annualized_return,
    is_rfq_shell,
    tradeable_size,
    verify_partition,
)

D = Decimal

SEGMENT_KEYS = ("category", "fee_type", "price_bucket", "tick_structure")


def _pct(values: list[D], q: int) -> D:
    """Percentile by nearest rank. No interpolation, so the result is exact."""
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, int(round(q / 100 * (len(ordered) - 1)))))
    return ordered[idx]


def _spread_stats(values: list[D]) -> dict[str, Any]:
    return {
        "n": len(values),
        "p10": str(_pct(values, 10)),
        "median": str(statistics.median(sorted(values))),
        "p90": str(_pct(values, 90)),
        "sub_1c_share_pct": str(
            (D(sum(1 for v in values if v < 1)) * 100 / len(values)).quantize(D("0.1"))
        ),
    }


def _fee_free_series(rows: list[dict]) -> set[str]:
    return {r["series_ticker"] for r in rows if r["fee_multiplier"] == "0"}


def _events(rows: list[dict]) -> dict[str, list[dict]]:
    by_event: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_event[r["event_ticker"]].append(r)
    return by_event


def _as_legs(rows: list[dict]) -> list[dict]:
    """Snapshot rows in the shape verify_partition expects."""
    out = []
    for r in rows:
        if not r["floor_strike"] and not r["cap_strike"]:
            out.append({"strike_type": r["strike_type"] or None})
            continue
        out.append(
            {
                "strike_type": r["strike_type"] or None,
                "floor_strike": r["floor_strike"] or "0",
                "cap_strike": r["cap_strike"] or "0",
                "yes_sub_title": r["underlying"],
            }
        )
    return out


def partition_report(rows: list[dict]) -> list[dict[str, Any]]:
    """Every verified-exhaustive partition, with capacity at size >= 1.

    Annualized return is computed only for verified partitions -- see
    monitor.checks.annualized_return. Unverified structures never get a number.
    """
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    out: list[dict[str, Any]] = []
    for event, legs in sorted(_events(rows).items()):
        if len(legs) < 2 or any(is_rfq_shell(leg["ticker"]) for leg in legs):
            continue
        result = verify_partition(_as_legs(legs))
        if not result:
            continue
        cost = sum(D(leg["ask_yes_cents"]) for leg in legs)
        capacity = min(tradeable_size(leg["ask_size"]) for leg in legs)
        close = min(leg["close_time"] for leg in legs)
        years = D(max((datetime.fromisoformat(close.replace("Z", "+00:00")) - now).days, 1))
        years /= D("365.25")
        ann = annualized_return(D(100) - cost, cost, years, verified=True) if cost < 100 else None
        out.append(
            {
                "event": event,
                "legs": len(legs),
                "fee_multiplier": legs[0]["fee_multiplier"],
                "tick_structure": legs[0]["tick_structure"],
                "cost_cents": str(cost.quantize(D("0.01"))),
                "below_par": cost < 100,
                "capacity_contracts": str(capacity),
                "tradeable": capacity >= 1,
                "annualized_pct": None if ann is None else str((ann * 100).quantize(D("0.01"))),
            }
        )
    return out


def compute(rows: list[dict]) -> dict[str, Any]:
    live = [r for r in rows if r["two_sided"] == "True"]
    spreads = [D(r["spread_cents"]) for r in live]

    segments: dict[str, dict[str, Any]] = {}
    for key in SEGMENT_KEYS:
        grouped: dict[str, list[D]] = defaultdict(list)
        for r in live:
            grouped[r[key] or "(unmapped)"].append(D(r["spread_cents"]))
        segments[key] = {
            name: _spread_stats(vals) for name, vals in sorted(grouped.items()) if len(vals) >= 30
        }

    fee_free = _fee_free_series(rows)
    tick_counts = Counter(r["tick_structure"] or "(none)" for r in rows)

    # The named tripwire: where the tick constraint and the fee gate vanish
    # together. Every market here priced above par at baseline.
    intersection = [
        r for r in rows if r["tick_structure"] == "deci_cent" and r["fee_multiplier"] == "0"
    ]
    intersection_partitions = [
        p for p in partition_report(intersection) if p["event"]
    ]

    partitions = partition_report(rows)
    fee_free_partitions = [p for p in partitions if p["fee_multiplier"] == "0"]
    costs = [D(p["cost_cents"]) for p in fee_free_partitions]

    return {
        "universe": {
            "n_markets": len(rows),
            "n_two_sided": len(live),
            "n_events": len({r["event_ticker"] for r in rows}),
            "rfq_shells_in_sweep": sum(1 for r in rows if is_rfq_shell(r["ticker"])),
        },
        "spread_exchange_wide": _spread_stats(spreads),
        "spread_by_segment": segments,
        "tick_structure": {
            name: {"n": n, "share_pct": str((D(n) * 100 / len(rows)).quantize(D("0.1")))}
            for name, n in sorted(tick_counts.items())
        },
        "fee_free": {
            # Series with at least one OPEN market. The memo's "14 series" is
            # the full registry count, which includes series with nothing
            # currently listed; only this figure is recomputable from a snapshot.
            "n_series_with_open_markets": len(fee_free),
            "series": sorted(fee_free),
            "n_markets": sum(1 for r in rows if r["fee_multiplier"] == "0"),
        },
        "fee_types": {
            name: n for name, n in sorted(Counter(r["fee_type"] or "(none)" for r in rows).items())
        },
        "deci_cent_fee_free_tripwire": {
            "n_markets": len(intersection),
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
            "fee_free_cost_median": str(statistics.median(sorted(costs))) if costs else None,
            "fee_free_cost_p10": str(_pct(costs, 10)) if costs else None,
            "fee_free_detail": fee_free_partitions,
        },
    }
