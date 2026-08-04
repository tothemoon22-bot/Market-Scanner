"""The single source of truth the dashboard serves.

Every field is either measured or explicitly absent. There are no defaults that
look like data: a value the scanner has not yet computed is ``None`` and the UI
renders NO DATA for it. ``0`` and "not measured" must never be confusable.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

#: Beyond this the dashboard treats the scanner as offline rather than quiet.
STALE_AFTER_SECONDS = 60


def now() -> datetime:
    return datetime.now(UTC)


def _age(ts: datetime | None) -> float | None:
    return None if ts is None else (now() - ts).total_seconds()


@dataclass
class SourceHealth:
    """One ingest source. `last_error` is kept: a source that fails loudly."""

    name: str
    last_success: datetime | None = None
    last_error: str = ""
    last_error_at: datetime | None = None
    successes: int = 0
    failures: int = 0

    def ok(self, at: datetime | None = None) -> None:
        self.last_success = at or now()
        self.successes += 1

    def failed(self, message: str) -> None:
        self.last_error = message
        self.last_error_at = now()
        self.failures += 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "age_seconds": _age(self.last_success),
            "last_success": self.last_success.isoformat() if self.last_success else None,
            "successes": self.successes,
            "failures": self.failures,
            "last_error": self.last_error,
            "last_error_at": self.last_error_at.isoformat() if self.last_error_at else None,
            "healthy": self.last_success is not None and (_age(self.last_success) or 0) < 120,
        }


@dataclass
class ScannerState:
    started_at: datetime = field(default_factory=now)
    heartbeat_at: datetime | None = None

    # Full-sweep products
    metrics: dict[str, Any] | None = None
    metrics_at: datetime | None = None
    sweep_count: int = 0
    sweep_seconds: float | None = None
    sweep_intervals: deque[float] = field(default_factory=lambda: deque(maxlen=20))

    triggers: list[dict[str, Any]] | None = None
    funnel: list[dict[str, Any]] | None = None
    partitions: list[dict[str, Any]] | None = None
    tripwire: dict[str, Any] | None = None

    # Tracked subset, polled far more often than the full sweep
    tracked: list[dict[str, Any]] | None = None
    tracked_at: datetime | None = None
    tracked_poll_seconds: float | None = None

    reference: dict[str, dict[str, Any]] = field(default_factory=dict)

    #: Per-series oscillation bands and the below-par push/suppress split.
    bands: dict[str, Any] | None = None
    below_par: dict[str, Any] | None = None
    ntfy: dict[str, Any] | None = None
    rate_limit_hits: int = 0

    invariant_violations: int = 0
    invariant_last: dict[str, Any] | None = None

    sources: dict[str, SourceHealth] = field(default_factory=dict)
    events: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=200))

    #: Set once the first full sweep lands. Before that the UI shows NO DATA
    #: everywhere rather than an empty-looking dashboard that reads as "zero".
    ready: bool = False

    #: Proximity history. The hero claims "no trigger within 20% of firing in N
    #: days", which is only true of the window actually observed -- so both ends
    #: of that window are recorded rather than assumed.
    observing_since: datetime = field(default_factory=now)
    last_within_20_at: datetime | None = None
    peak_proximity_pct: float | None = None

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def source(self, name: str) -> SourceHealth:
        return self.sources.setdefault(name, SourceHealth(name))

    def log(self, kind: str, message: str, **extra: Any) -> None:
        with self._lock:
            self.events.appendleft(
                {
                    "at": now().isoformat(),
                    "kind": kind,
                    "message": message,
                    **extra,
                }
            )

    def beat(self) -> None:
        self.heartbeat_at = now()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            heartbeat_age = _age(self.heartbeat_at)
            return {
                "generated_at": now().isoformat(),
                "ready": self.ready,
                "online": heartbeat_age is not None and heartbeat_age < STALE_AFTER_SECONDS,
                "stale_after_seconds": STALE_AFTER_SECONDS,
                "heartbeat_age_seconds": heartbeat_age,
                "uptime_seconds": (now() - self.started_at).total_seconds(),
                "metrics": self.metrics,
                "metrics_age_seconds": _age(self.metrics_at),
                "sweep": {
                    "count": self.sweep_count,
                    "last_duration_seconds": self.sweep_seconds,
                    "achieved_interval_seconds": (
                        sum(self.sweep_intervals) / len(self.sweep_intervals)
                        if self.sweep_intervals
                        else None
                    ),
                },
                "triggers": self.triggers,
                "funnel": self.funnel,
                "partitions": self.partitions,
                "tripwire": self.tripwire,
                "tracked": self.tracked,
                "tracked_age_seconds": _age(self.tracked_at),
                "tracked_poll_seconds": self.tracked_poll_seconds,
                "reference": self.reference,
                "proximity_watch": {
                    "observing_since": self.observing_since.isoformat(),
                    "observed_days": (now() - self.observing_since).total_seconds() / 86400,
                    "last_within_20_at": (
                        self.last_within_20_at.isoformat() if self.last_within_20_at else None
                    ),
                    "days_since_within_20": (
                        None
                        if self.last_within_20_at is None
                        else (now() - self.last_within_20_at).total_seconds() / 86400
                    ),
                    "peak_proximity_pct": self.peak_proximity_pct,
                },
                "bands": self.bands,
                "below_par": self.below_par,
                "ntfy": self.ntfy,
                "rate_limit_hits": self.rate_limit_hits,
                "invariant": {
                    "violations": self.invariant_violations,
                    "last": self.invariant_last,
                },
                "sources": {name: h.as_dict() for name, h in sorted(self.sources.items())},
                "events": list(self.events)[:60],
            }
