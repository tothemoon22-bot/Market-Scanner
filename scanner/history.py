"""Per-series par-crossing history, and the oscillation band derived from it.

"New" should mean new relative to a baseline that knows `KXGDPYEAR-*` crosses
par routinely. A series that has always oscillated between 95c and 105c has not
done anything novel by printing 98c.

**Cold start is the whole difficulty, and it is not faked.** A band computed
from two observations is not a band; it is two points with a line drawn through
them. So a series below :data:`MIN_OBSERVATIONS_FOR_BAND` reports
``UNKNOWN`` and gets the dollar floor alone, and the band state is surfaced on
the trigger board so an UNKNOWN is visibly not a verified range.

Bands tighten as the archive grows. Nothing here back-fills, interpolates, or
assumes a distribution.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal as D
from pathlib import Path
from typing import Any

#: Below this, a series has no band. Eight weekly observations is two months --
#: enough to have seen a series cross par more than once if it does.
MIN_OBSERVATIONS_FOR_BAND = 8

#: Capacity x edge floor for the "new structure" branch. A detection worth less
#: than this is recorded but does not push.
MIN_DOLLAR_VALUE = D(25)

HISTORY_PATH = Path("data/monitor/series_history.jsonl")
SUPPRESSED_PATH = Path("data/monitor/suppressed.jsonl")

#: Rolling window for the suppressed count shown on the trigger board.
SUPPRESSED_WINDOW_DAYS = 90


@dataclass(frozen=True)
class Band:
    series: str
    observations: int
    min_cost_cents: D | None
    max_cost_cents: D | None
    crossings: int

    @property
    def known(self) -> bool:
        return self.observations >= MIN_OBSERVATIONS_FOR_BAND

    @property
    def state(self) -> str:
        return "KNOWN" if self.known else "UNKNOWN"

    def is_outside(self, cost: D) -> bool:
        """True when this observation falls outside everything seen before.

        An UNKNOWN band never claims an observation is outside it -- with too
        few points, "outside the observed range" is a statement about the
        sample, not the series.
        """
        if not self.known or self.min_cost_cents is None or self.max_cost_cents is None:
            return False
        return cost < self.min_cost_cents or cost > self.max_cost_cents

    def as_dict(self) -> dict[str, Any]:
        return {
            "series": self.series,
            "observations": self.observations,
            "state": self.state,
            "min_cost_cents": None if self.min_cost_cents is None else str(self.min_cost_cents),
            "max_cost_cents": None if self.max_cost_cents is None else str(self.max_cost_cents),
            "crossings": self.crossings,
            "needs": max(0, MIN_OBSERVATIONS_FOR_BAND - self.observations),
        }


def series_of(event: str) -> str:
    return event.split("-")[0]


def record(partitions: list[dict[str, Any]], path: Path = HISTORY_PATH) -> None:
    """Append one line per verified partition per sweep. Record everything."""
    if not partitions:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    at = datetime.now(UTC).isoformat()
    with path.open("a") as fh:
        for p in partitions:
            fh.write(
                json.dumps(
                    {
                        "at": at,
                        "event": p["event"],
                        "series": series_of(p["event"]),
                        "cost_cents": p["cost_cents"],
                        "capacity_contracts": p["capacity_contracts"],
                        "below_par": p["below_par"],
                    }
                )
                + "\n"
            )


def load(path: Path = HISTORY_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def bands(path: Path = HISTORY_PATH) -> dict[str, Band]:
    """One band per series, from whatever history exists."""
    rows = load(path)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row["series"], []).append(row)

    out: dict[str, Band] = {}
    for series, observations in grouped.items():
        costs = [D(o["cost_cents"]) for o in observations]
        # A crossing is a change in which side of par the series sits on,
        # between consecutive observations of the same event.
        crossings = 0
        by_event: dict[str, list[dict[str, Any]]] = {}
        for o in observations:
            by_event.setdefault(o["event"], []).append(o)
        for series_obs in by_event.values():
            ordered = sorted(series_obs, key=lambda o: o["at"])
            for a, b in zip(ordered, ordered[1:], strict=False):
                if a["below_par"] != b["below_par"]:
                    crossings += 1
        out[series] = Band(
            series=series,
            observations=len(observations),
            min_cost_cents=min(costs),
            max_cost_cents=max(costs),
            crossings=crossings,
        )
    return out


def dollar_value(cost_cents: D, capacity_contracts: D) -> D:
    """Total realisable profit if the basket settles: edge x capacity."""
    edge = D(100) - cost_cents
    if edge <= 0 or capacity_contracts < 1:
        return D(0)
    return (edge * capacity_contracts / 100).quantize(D("0.01"))


def record_suppressed(items: list[dict[str, Any]], path: Path = SUPPRESSED_PATH) -> None:
    """Every sub-floor detection is written, whether or not it pushed.

    Suppression applies to the notification, never to the record. A spike in
    this ledger is a signal in its own right even when nothing clears the floor.
    """
    if not items:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    at = datetime.now(UTC).isoformat()
    with path.open("a") as fh:
        for item in items:
            fh.write(
                json.dumps(
                    {
                        "at": at,
                        "event": item["event"],
                        "cost_cents": item["cost_cents"],
                        "capacity_contracts": item["capacity_contracts"],
                        "dollar_value": item.get("dollar_value"),
                        "because": item.get("suppressed_because", ""),
                    }
                )
                + "\n"
            )


def suppressed_window(
    path: Path = SUPPRESSED_PATH, days: int = SUPPRESSED_WINDOW_DAYS
) -> dict[str, Any]:
    """Rolling count of suppressed detections, for the trigger board."""
    if not path.exists():
        return {"count": 0, "window_days": days, "observing_since": None, "by_reason": {}}
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if not rows:
        return {"count": 0, "window_days": days, "observing_since": None, "by_reason": {}}
    cutoff = datetime.now(UTC).timestamp() - days * 86400
    recent = [
        r for r in rows
        if datetime.fromisoformat(r["at"]).timestamp() >= cutoff
    ]
    by_reason: dict[str, int] = {}
    for r in recent:
        by_reason[r["because"]] = by_reason.get(r["because"], 0) + 1
    return {
        "count": len(recent),
        "window_days": days,
        "observing_since": min(r["at"] for r in rows),
        "by_reason": by_reason,
    }
