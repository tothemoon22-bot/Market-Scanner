"""The proximity watch and the restart record, on disk.

``peak_proximity_pct`` and ``last_within_20_at`` lived only on ``ScannerState``.
They reset on restart, and deploying restarts the process -- so the hero's
"**N** days since a trigger was within 20% of firing" was bounded above by
uptime and could never support the long-horizon claim its wording makes. Seven
days of observed quiet became 0.4 days because a process restarted. **A hero
figure that silently understates its own evidence is the failure this dashboard
exists to prevent**, pointed the other way.

Three record kinds, one append-only ledger in the existing ``data/monitor/``
infrastructure:

``start``
    One per process start. The restart count is the number of these minus one,
    which means a crash-loop is a climbing number rather than an inference from
    small uptime.
``peak``
    Written only when a new all-time peak is set, so the file grows with
    information rather than with time.
``within_20``
    Written when a trigger comes within 20% of firing. This is the row the hero
    figure is measured from.

**Nothing is backfilled.** The pre-``509db99`` readings measured a different
quantity and are gone; inventing them here would be worse than the gap. The
first ``start`` row *is* the beginning of the series, and everything downstream
reports it as such.

What this still does not measure is **coverage**. The span from the first start
to now is wall-clock, and downtime is not subtracted -- a box that was off for a
day reports the same span as one that ran throughout. That is why the restart
count is published beside the span instead of being folded into it: an
unexplained gap is visible as restarts, and the panel says "observed over N days
across R restarts" rather than claiming N days of observation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

LEDGER_PATH = Path("data/monitor/watch.jsonl")

#: Restarts within 24h that indicate a crash-loop rather than a deploy.
#: **PROVISIONAL** -- chosen, not derived. A deploy is one or two restarts; this
#: has no observational basis yet and should be revisited once the box has a
#: month of history. Flagged rather than presented as measured.
RESTART_ALERT_24H = 3

WITHIN_PCT = 20.0


def _now() -> datetime:
    return datetime.now(UTC)


def _parse(stamp: str) -> datetime:
    dt = datetime.fromisoformat(stamp)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


@dataclass(frozen=True)
class Restored:
    """What the ledger knows, before this session adds anything."""

    observing_since: datetime | None
    peak_proximity_pct: float | None
    last_within_20_at: datetime | None
    restarts: int
    last_restart_at: datetime | None
    restarts_24h: int

    @property
    def cold(self) -> bool:
        """No ledger yet. Distinct from a ledger that recorded nothing."""
        return self.observing_since is None


def _rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                # A torn final line from a kill mid-write. Skipping it loses one
                # record; refusing to read the file would lose all of them.
                continue
    return out


def _append(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(json.dumps(row) + "\n")


def load(path: Path = LEDGER_PATH, at: datetime | None = None) -> Restored:
    """Read the ledger. Does not write -- see :func:`begin_session`."""
    now = at or _now()
    rows = _rows(path)

    starts = [_parse(r["at"]) for r in rows if r.get("kind") == "start" and "at" in r]
    peaks = [float(r["pct"]) for r in rows if r.get("kind") == "peak" and "pct" in r]
    within = [_parse(r["at"]) for r in rows if r.get("kind") == "within_20" and "at" in r]

    return Restored(
        observing_since=min(starts) if starts else None,
        peak_proximity_pct=max(peaks) if peaks else None,
        last_within_20_at=max(within) if within else None,
        # The current process's own start row is one of these, so a first-ever
        # run reports zero restarts rather than one.
        restarts=max(0, len(starts) - 1),
        last_restart_at=sorted(starts)[-1] if len(starts) >= 1 else None,
        # Bounded below as well as above. `now - s <= 24h` alone is true for
        # every *future* timestamp, so one row written under clock skew -- or
        # restored from a box whose clock ran ahead -- would report a crash-loop
        # that never happened. An alert that fires on a bad clock is worse than
        # no alert, because it teaches you to ignore it.
        restarts_24h=sum(
            1 for s in starts if timedelta(0) <= now - s <= timedelta(hours=24)
        ),
    )


def begin_session(path: Path = LEDGER_PATH, at: datetime | None = None) -> Restored:
    """Record this process start and return the state to restore from.

    The read happens **before** the write, so ``restarts`` counts prior sessions
    and ``last_restart_at`` is the *previous* start rather than this one. A
    restart counter that includes the current process reads one on a machine
    that has never restarted.
    """
    now = at or _now()
    prior = load(path, at=now)
    _append(path, {"kind": "start", "at": now.isoformat()})
    return Restored(
        observing_since=prior.observing_since or now,
        peak_proximity_pct=prior.peak_proximity_pct,
        last_within_20_at=prior.last_within_20_at,
        restarts=prior.restarts + (1 if prior.observing_since is not None else 0),
        last_restart_at=prior.last_restart_at,
        restarts_24h=prior.restarts_24h + 1,
    )


def record_peak(pct: float, path: Path = LEDGER_PATH, at: datetime | None = None) -> None:
    _append(path, {"kind": "peak", "at": (at or _now()).isoformat(), "pct": pct})


def record_within_20(pct: float, path: Path = LEDGER_PATH, at: datetime | None = None) -> None:
    _append(path, {"kind": "within_20", "at": (at or _now()).isoformat(), "pct": pct})
