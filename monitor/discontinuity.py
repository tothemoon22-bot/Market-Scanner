"""Dated markers where a measurement's *definition* changed under it.

A series that changes meaning mid-flight is worse than a gap. A gap is visible;
a redefinition is not, and every reading on both sides looks equally valid.

The rule this module exists to enforce:

> **Never delete, backfill or recompute a measurement across a definition
> change.** Recomputing the old regime under the new definition invents readings
> that were never taken. Deleting it hides that the change happened. The only
> honest option is to leave both sides untouched and mark the boundary, so a
> reader can see that a window spans one.

Markers are append-only and seeded in code, so a fresh box carries the same
history as one with the ledger on disk -- the same arrangement as
``monitor/acknowledgments.py``, for the same reason.

The consumer is ``ScannerState.snapshot``: any marker falling inside
``[observing_since, now]`` is published on ``proximity_watch.discontinuities``,
and the dashboard labels the window rather than the number being quietly wrong.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

LEDGER_PATH = Path("data/monitor/discontinuities.jsonl")


def _parse(stamp: str) -> datetime:
    dt = datetime.fromisoformat(stamp)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


#: Committed markers. The runtime ledger is additive on top of these.
SEEDED: tuple[dict[str, Any], ...] = (
    {
        "measurement": "proximity_watch",
        "commit": "509db99",
        "at": "2026-08-12T02:03:11+00:00",
        "regime_from": "2026-08-04T15:39:37+00:00",
        "regime_from_commit": "e3692b5",
        "earlier_regime": (
            "The trigger board was evaluated twice per sweep. The proximity "
            "watch read the earlier of the two, which was called without "
            "`bands` and before the fee-change materiality split existed, so "
            "the scheduled-fee-change trigger scored proximity on the raw "
            "count of pending changes against a threshold of 'any'."
        ),
        "effect": (
            "Kalshi publishes routine per-event fee overrides more or less "
            "continuously -- 100 pending, all classified routine, at the one "
            "moment a live board was captured. Whenever any were pending the "
            "trigger read 100%, so `peak_proximity_pct` was pinned at 100.0 "
            "and `last_within_20_at` was refreshed every sweep. "
            "`days_since_within_20` therefore read approximately zero "
            "continuously, for a reason unrelated to edge."
        ),
        "direction": "overstated",
        "note": (
            "Readings before the boundary are not comparable with readings "
            "after it and are not corrected here. Nothing is recomputed: the "
            "earlier regime measured a different quantity, and re-deriving it "
            "under the current definition would invent observations that were "
            "never taken."
        ),
    },
)


@dataclass(frozen=True)
class Marker:
    measurement: str
    commit: str
    at: str
    earlier_regime: str
    effect: str = ""
    regime_from: str | None = None
    regime_from_commit: str | None = None
    direction: str = "unknown"
    note: str = ""
    extra: dict[str, Any] = field(default_factory=dict, compare=False)

    @property
    def boundary(self) -> datetime:
        return _parse(self.at)

    @property
    def span_hours(self) -> float | None:
        """How long the earlier regime ran. ``None`` when its start is unknown."""
        if not self.regime_from:
            return None
        return (self.boundary - _parse(self.regime_from)).total_seconds() / 3600

    @property
    def brief(self) -> bool:
        """Under six hours. A brief span can reasonably be waited out; a long
        one has to be stated wherever the measurement is quoted."""
        span = self.span_hours
        return span is not None and span < 6

    def as_dict(self) -> dict[str, Any]:
        return {
            "measurement": self.measurement,
            "commit": self.commit,
            "at": self.at,
            "regime_from": self.regime_from,
            "regime_from_commit": self.regime_from_commit,
            "span_hours": self.span_hours,
            "brief": self.brief,
            "earlier_regime": self.earlier_regime,
            "effect": self.effect,
            "direction": self.direction,
            "note": self.note,
            **self.extra,
        }


def _from_row(row: dict[str, Any]) -> Marker:
    known = {
        "measurement", "commit", "at", "earlier_regime", "effect",
        "regime_from", "regime_from_commit", "direction", "note",
    }
    return Marker(
        measurement=row["measurement"],
        commit=row["commit"],
        at=row["at"],
        earlier_regime=row["earlier_regime"],
        effect=row.get("effect", ""),
        regime_from=row.get("regime_from"),
        regime_from_commit=row.get("regime_from_commit"),
        direction=row.get("direction", "unknown"),
        note=row.get("note", ""),
        extra={k: v for k, v in row.items() if k not in known},
    )


def markers(measurement: str | None = None, path: Path = LEDGER_PATH) -> list[Marker]:
    """Seeded markers plus whatever the ledger holds, oldest first."""
    rows = [_from_row(dict(r)) for r in SEEDED]
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip():
                rows.append(_from_row(json.loads(line)))
    if measurement is not None:
        rows = [m for m in rows if m.measurement == measurement]
    return sorted(rows, key=lambda m: m.boundary)


def record(
    measurement: str,
    commit: str,
    earlier_regime: str,
    *,
    at: str | None = None,
    regime_from: str | None = None,
    regime_from_commit: str | None = None,
    effect: str = "",
    direction: str = "unknown",
    note: str = "",
    path: Path = LEDGER_PATH,
) -> Marker:
    """Append one marker. Requires a commit and a description of what the
    earlier regime measured -- a boundary nobody can interpret is not a marker.
    """
    if not commit.strip():
        raise ValueError("a discontinuity marker must name the commit that moved the boundary")
    if not earlier_regime.strip():
        raise ValueError(
            "a discontinuity marker must say what the earlier regime measured; "
            "without it a reader cannot tell which readings to distrust or how"
        )
    marker = _from_row(
        {
            "measurement": measurement,
            "commit": commit,
            "at": at or datetime.now(UTC).isoformat(),
            "regime_from": regime_from,
            "regime_from_commit": regime_from_commit,
            "earlier_regime": earlier_regime,
            "effect": effect,
            "direction": direction,
            "note": note,
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(json.dumps(marker.as_dict()) + "\n")
    return marker


def spanning(
    window_start: datetime,
    window_end: datetime,
    measurement: str | None = None,
    path: Path = LEDGER_PATH,
) -> list[Marker]:
    """Markers whose boundary falls inside the window.

    A window that contains a boundary is measured under two definitions, and
    any figure quoted over it -- "no trigger within 20% for N days" -- is a
    statement about both.
    """
    return [
        m
        for m in markers(measurement, path)
        if window_start <= m.boundary <= window_end
    ]
