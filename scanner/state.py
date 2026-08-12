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

from monitor import discontinuity, reviews
from monitor.aggregate import ALERT_DISTINCT_SPREADS, MAX_DISTINCT_SPREADS
from monitor.archive import ARCHIVE_STALE_AFTER_DAYS
from scanner import process

#: Beyond this the dashboard treats the scanner as offline rather than quiet.
STALE_AFTER_SECONDS = 60


def now() -> datetime:
    return datetime.now(UTC)


def _age(ts: datetime | None) -> float | None:
    return None if ts is None else (now() - ts).total_seconds()


def _mean(samples: deque[float]) -> float | None:
    """None until there is something to average. Never 0.0 as a stand-in."""
    return sum(samples) / len(samples) if samples else None


def _drift(achieved: float | None, configured: float | None) -> float | None:
    return None if achieved is None or configured is None else achieved - configured


def _mb(nbytes: int | None) -> float | None:
    """None stays None. An unread gauge is not a gauge reading zero."""
    return None if nbytes is None else round(nbytes / 1048576, 1)


#: Every named subsystem that can fail. Registered up front so one that has
#: never run renders as an explicit NEVER RUN rather than being absent from the
#: payload -- absence and zero are the confusion this whole module exists to
#: prevent, and a subsystem that silently never registered is the worst case of
#: it.
SUBSYSTEMS = (
    "kalshi_sweep",
    "kalshi_tracked",
    "kalshi_fee_changes",
    "population",
    "series_metadata",
    "binance_vision",
    "coinbase",
    "ntfy",
    "ledger_archive",
)

#: Consecutive failures of one subsystem before it pushes on its own account,
#: with no detection threshold involved. Three hourly sweeps is three hours of a
#: dead subsystem, which is long enough to rule out a transient and short enough
#: to matter.
CONSECUTIVE_FAILURE_ALERT = 3


class Outcome:
    """Why a panel has no data. Three states, never collapsed into one.

    ``NOT_RUN``  the work has not been attempted yet
    ``EMPTY``    it ran, and there was genuinely nothing
    ``FAILED``   it was attempted and threw

    The `.csv.gz` filename bug rendered as NOT_RUN forever while the subsystem
    threw on every sweep: a permanently broken subsystem was indistinguishable
    from a normal empty state. Collapsing these three is that bug's shape.
    """

    NOT_RUN = "not_run"
    EMPTY = "empty"
    FAILED = "failed"
    OK = "ok"

    @staticmethod
    def make(state: str, reason: str, data: Any = None) -> dict[str, Any]:
        return {"state": state, "reason": reason, "data": data, "at": now().isoformat()}


@dataclass
class SourceHealth:
    """One subsystem. `last_error` is kept: a source that fails loudly.

    ``consecutive_failures`` is the field that matters most. A total is easy to
    dismiss as historical; a run of them is a subsystem that is down now.
    """

    name: str
    last_success: datetime | None = None
    last_error: str = ""
    last_error_type: str = ""
    last_error_at: datetime | None = None
    successes: int = 0
    failures: int = 0
    consecutive_failures: int = 0

    def ok(self, at: datetime | None = None) -> None:
        self.last_success = at or now()
        self.successes += 1
        self.consecutive_failures = 0

    def failed(self, message: str, exc: BaseException | None = None) -> None:
        self.last_error = message
        self.last_error_type = type(exc).__name__ if exc is not None else message.split(":")[0]
        self.last_error_at = now()
        self.failures += 1
        self.consecutive_failures += 1

    @property
    def state(self) -> str:
        """NEVER RUN, FAILING and OK are three different things."""
        if self.successes == 0 and self.failures == 0:
            return "NEVER_RUN"
        if self.consecutive_failures:
            return "FAILING"
        return "OK"

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state,
            "age_seconds": _age(self.last_success),
            "last_success": self.last_success.isoformat() if self.last_success else None,
            "successes": self.successes,
            "failures": self.failures,
            "consecutive_failures": self.consecutive_failures,
            "last_error": self.last_error,
            "last_error_type": self.last_error_type,
            "last_error_at": self.last_error_at.isoformat() if self.last_error_at else None,
            "last_error_age_seconds": _age(self.last_error_at),
            "healthy": self.state == "OK"
            and self.last_success is not None
            and (_age(self.last_success) or 0) < 120,
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

    #: What the loops were *told* to do, set at startup from the engine's
    #: constants. None until then, because a configured interval nobody
    #: configured is not a measurement either. Drift between configured and
    #: achieved is the early signal of throttling or backpressure.
    configured_sweep_interval: float | None = None
    configured_tracked_interval: float | None = None

    triggers: list[dict[str, Any]] | None = None
    funnel: list[dict[str, Any]] | None = None
    partitions: list[dict[str, Any]] | None = None
    tripwire: dict[str, Any] | None = None

    # Tracked subset, polled far more often than the full sweep.
    # `poll_seconds` is how long one cycle took; `intervals` is the gap between
    # cycle starts. They are different numbers and the panel labels them so --
    # showing a duration under the word "interval" is a number the system does
    # not measure.
    tracked: list[dict[str, Any]] | None = None
    tracked_at: datetime | None = None
    tracked_poll_seconds: float | None = None
    tracked_intervals: deque[float] = field(default_factory=lambda: deque(maxlen=40))

    reference: dict[str, dict[str, Any]] = field(default_factory=dict)

    #: Per-event oscillation bands and the below-par push/suppress split.
    bands: dict[str, Any] | None = None
    below_par: dict[str, Any] | None = None

    #: Population reconciliation against the previous sweep, and the trend
    #: series behind the market-count panel. `population` is an Outcome
    #: envelope, never a bare payload: "no prior sweep" and "the comparison
    #: threw" must not render identically.
    population: dict[str, Any] | None = None
    trend: list[dict[str, Any]] | None = None

    #: Last successful poll of the scheduled-fee-change endpoints, and what it
    #: returned. One of only two programmatic proxies for a change to the 0.07
    #: coefficient, so its silence has to be positively confirmed.
    fee_changes: dict[str, Any] | None = None

    #: Last time the weekly archive job pulled the ledgers. The scanner cannot
    #: see the Action, but it can see the fetch -- so an Action that stops
    #: running shows as a growing age on the panel rather than only in a
    #: workflow history nobody reads.
    ledgers_served_at: datetime | None = None

    #: Markets whose fee model is unknown because their series is not in the
    #: registry. "Unknown" is not "not fee-free": the registry is not a complete
    #: enumeration of the swept universe, so a fee-free series could sit here.
    fee_model_exposure: dict[str, Any] | None = None

    #: Series whose metadata could not be resolved. An unresolved series has an
    #: empty fee_multiplier, so it silently drops out of the fee-free universe
    #: -- a shrinking headline number with no visible cause.
    unresolved_series: list[str] | None = None

    #: Alerts that fired but could not be delivered. A push that failed is not
    #: an alert that did not fire.
    undelivered_alerts: list[dict[str, Any]] | None = None

    #: Legs whose fee_type the model does not recognise. funnel._fee_model
    #: falls back to quadratic so the funnel still computes; the substitution
    #: is counted here rather than made silently.
    unknown_fee_types: dict[str, int] | None = None

    #: Distinct keys in the spread count map. Growth here is how a tick
    #: structure change would first appear, and it is also what bounds the
    #: aggregate's memory.
    spread_cardinality: int | None = None
    ntfy: dict[str, Any] | None = None

    #: Lifetime 429 count, plus the timestamps behind it so a 24h figure is
    #: counted rather than estimated from the lifetime total.
    rate_limit_hits: int = 0
    rate_limit_at: deque[datetime] = field(default_factory=lambda: deque(maxlen=500))

    invariant_violations: int = 0
    invariant_last: dict[str, Any] | None = None

    sources: dict[str, SourceHealth] = field(
        default_factory=lambda: {name: SourceHealth(name) for name in SUBSYSTEMS}
    )
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

    #: Memory trend. The current reading alone cannot distinguish steady from
    #: growing, which is the only thing a 24h memory gate cares about. None
    #: until the first successful sample -- never zero; see scanner/process.py.
    rss_first_bytes: int | None = None
    rss_first_at: datetime | None = None
    rss_peak_bytes: int | None = None
    rss_peak_at: datetime | None = None

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
        self.note_rss()

    def note_rss(self) -> None:
        """Sample RSS and keep the first and highest readings.

        A single current value cannot answer the question a 24-hour memory gate
        actually asks, which is whether the number is *growing*. 73 MB steady
        and 73 MB on the way up render identically, and telling them apart
        otherwise means either watching the panel for a day or shelling in.
        Three integers make the trend readable in one glance.

        Deliberately not a series: a memory gauge that accumulates samples
        shares the failure mode of the thing it measures.
        """
        rss = process.rss_bytes()
        if rss is None:
            return
        if self.rss_first_bytes is None:
            self.rss_first_bytes = rss
            self.rss_first_at = now()
        if self.rss_peak_bytes is None or rss > self.rss_peak_bytes:
            self.rss_peak_bytes = rss
            self.rss_peak_at = now()

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
                "started_at": self.started_at.isoformat(),
                "process": {
                    **process.as_dict(),
                    "rss_first_bytes": self.rss_first_bytes,
                    "rss_first_mb": _mb(self.rss_first_bytes),
                    "rss_peak_bytes": self.rss_peak_bytes,
                    "rss_peak_mb": _mb(self.rss_peak_bytes),
                    "rss_peak_at": (
                        self.rss_peak_at.isoformat() if self.rss_peak_at else None
                    ),
                    "rss_growth_mb": (
                        None
                        if self.rss_first_bytes is None or self.rss_peak_bytes is None
                        else round((self.rss_peak_bytes - self.rss_first_bytes) / 1048576, 1)
                    ),
                    "rss_observed_hours": (
                        None
                        if self.rss_first_at is None
                        else (now() - self.rss_first_at).total_seconds() / 3600
                    ),
                },
                "metrics": self.metrics,
                "metrics_age_seconds": _age(self.metrics_at),
                "sweep": {
                    "count": self.sweep_count,
                    "last_duration_seconds": self.sweep_seconds,
                    "achieved_interval_seconds": _mean(self.sweep_intervals),
                    "configured_interval_seconds": self.configured_sweep_interval,
                    "drift_seconds": _drift(
                        _mean(self.sweep_intervals), self.configured_sweep_interval
                    ),
                    "samples": len(self.sweep_intervals),
                },
                "tracked_loop": {
                    "achieved_interval_seconds": _mean(self.tracked_intervals),
                    "configured_interval_seconds": self.configured_tracked_interval,
                    "drift_seconds": _drift(
                        _mean(self.tracked_intervals), self.configured_tracked_interval
                    ),
                    "last_cycle_seconds": self.tracked_poll_seconds,
                    "samples": len(self.tracked_intervals),
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
                    # A window that contains a definition change is measured
                    # under two definitions, and every figure above is a
                    # statement about both. Published rather than corrected:
                    # see monitor/discontinuity.py.
                    "discontinuities": [
                        m.as_dict()
                        for m in discontinuity.spanning(
                            self.observing_since, now(), "proximity_watch"
                        )
                    ],
                },
                "bands": self.bands,
                "below_par": self.below_par,
                "population": self.population
                or Outcome.make(Outcome.NOT_RUN, "no full sweep has completed"),
                "trend": self.trend,
                "fee_changes": self.fee_changes,
                "manual_reviews": reviews.status(since=self.started_at),
                "unresolved_series": self.unresolved_series,
                "fee_model_exposure": self.fee_model_exposure,
                "ledger_archive": {
                    "last_served": (
                        self.ledgers_served_at.isoformat() if self.ledgers_served_at else None
                    ),
                    "age_seconds": _age(self.ledgers_served_at),
                    "stale_after_days": ARCHIVE_STALE_AFTER_DAYS,
                    "stale": (
                        self.ledgers_served_at is not None
                        and (_age(self.ledgers_served_at) or 0)
                        > ARCHIVE_STALE_AFTER_DAYS * 86400
                    ),
                },
                "undelivered_alerts": self.undelivered_alerts,
                "unknown_fee_types": self.unknown_fee_types,
                "failing_subsystems": [
                    h.as_dict()
                    for h in sorted(self.sources.values(), key=lambda s: s.name)
                    if h.consecutive_failures
                ],
                "spread_cardinality": (
                    None
                    if self.spread_cardinality is None
                    else {
                        "distinct_keys": self.spread_cardinality,
                        "alert_at": ALERT_DISTINCT_SPREADS,
                        "hard_ceiling": MAX_DISTINCT_SPREADS,
                        "over": self.spread_cardinality >= ALERT_DISTINCT_SPREADS,
                    }
                ),
                "ntfy": self.ntfy,
                "rate_limit_hits": self.rate_limit_hits,
                "rate_limit": {
                    "lifetime": self.rate_limit_hits,
                    "last_24h": sum(
                        1 for at in self.rate_limit_at if (now() - at).total_seconds() <= 86400
                    ),
                    "last_at": (
                        max(self.rate_limit_at).isoformat() if self.rate_limit_at else None
                    ),
                    # The deque is bounded, so a lifetime count beyond its
                    # capacity cannot be re-derived from timestamps. Say so
                    # rather than implying the 24h figure covers everything.
                    "timestamps_retained": len(self.rate_limit_at),
                    "timestamps_capacity": self.rate_limit_at.maxlen,
                },
                "invariant": {
                    "violations": self.invariant_violations,
                    "last": self.invariant_last,
                },
                "sources": {name: h.as_dict() for name, h in sorted(self.sources.items())},
                "events": list(self.events)[:60],
            }
