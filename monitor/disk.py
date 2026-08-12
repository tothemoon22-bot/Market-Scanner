"""Disk headroom, measured the way memory already is.

The memory projection exists because RSS growth precedes the box dying. Disk is
the other way the same box dies, and nothing measured it -- the sweep ledgers
under ``data/`` and the population CSVs grow on every sweep, forever, on 40 GB.

Same rules as every other gauge here:

* A rate needs **two samples separated by real time**. One sample is not a
  trend, and two taken a minute apart divide noise by a small number and call
  the result a growth rate. Below :data:`MIN_SPAN_HOURS` the answer is ``None``
  with a reason, not a large number.
* ``None`` is never ``0``. "Not measured yet" and "measured, not growing" are
  different states and the panel renders them differently.
* An exhaustion date is projected **only when the trend is positive**. A
  shrinking disk has no exhaustion date, and reporting a negative one as if it
  were a forecast would be inventing a measurement.

Samples go in the existing ``data/monitor/`` ledger infrastructure, one row per
full sweep -- hourly, so ~9k rows a year and under a megabyte, which is
comfortably less than the thing it is watching.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

LEDGER_PATH = Path("data/monitor/disk.jsonl")
DATA_DIR = Path("data")

#: Below this the two endpoints are too close together for the difference to
#: mean anything. Six hours of hourly sweeps is six samples.
MIN_SPAN_HOURS = 6.0

#: Projected exhaustion inside this many days is a warning on the panel.
#: **PROVISIONAL** -- chosen so there is time to act, not derived from anything.
EXHAUSTION_WARN_DAYS = 90.0


def _now() -> datetime:
    return datetime.now(UTC)


def _parse(stamp: str) -> datetime:
    dt = datetime.fromisoformat(stamp)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def tree_bytes(root: Path = DATA_DIR) -> int | None:
    """Total size of the ledger tree, or None if it cannot be read."""
    if not root.exists():
        return None
    total = 0
    try:
        for path in root.rglob("*"):
            if path.is_file():
                total += path.stat().st_size
    except OSError:
        return None
    return total


def usage(root: Path = DATA_DIR) -> dict[str, int | None]:
    target = root if root.exists() else root.resolve().parent
    try:
        du = shutil.disk_usage(target)
    except OSError:
        return {"total_bytes": None, "free_bytes": None, "used_bytes": None}
    return {"total_bytes": du.total, "free_bytes": du.free, "used_bytes": du.used}


def sample(
    path: Path = LEDGER_PATH, root: Path = DATA_DIR, at: datetime | None = None
) -> dict[str, Any]:
    """Append one measurement. Returns the row written."""
    row: dict[str, Any] = {
        "at": (at or _now()).isoformat(),
        "ledger_bytes": tree_bytes(root),
        **usage(root),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(json.dumps(row) + "\n")
    return row


def _rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


@dataclass(frozen=True)
class Headroom:
    free_bytes: int | None
    total_bytes: int | None
    ledger_bytes: int | None
    growth_bytes_per_day: float | None
    span_hours: float | None
    samples: int
    days_to_full: float | None
    exhaustion_at: str | None
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "free_bytes": self.free_bytes,
            "free_gb": None if self.free_bytes is None else round(self.free_bytes / 2**30, 2),
            "total_bytes": self.total_bytes,
            "total_gb": None if self.total_bytes is None else round(self.total_bytes / 2**30, 2),
            "used_pct": (
                None
                if not self.total_bytes or self.free_bytes is None
                else round(100 * (1 - self.free_bytes / self.total_bytes), 1)
            ),
            "ledger_bytes": self.ledger_bytes,
            "ledger_mb": None if self.ledger_bytes is None else round(self.ledger_bytes / 2**20, 1),
            "growth_mb_per_day": (
                None
                if self.growth_bytes_per_day is None
                else round(self.growth_bytes_per_day / 2**20, 2)
            ),
            "span_hours": self.span_hours,
            "samples": self.samples,
            "days_to_full": self.days_to_full,
            "exhaustion_at": self.exhaustion_at,
            "warn": self.days_to_full is not None and self.days_to_full <= EXHAUSTION_WARN_DAYS,
            "reason": self.reason,
        }


def headroom(
    path: Path = LEDGER_PATH, root: Path = DATA_DIR, at: datetime | None = None
) -> Headroom:
    """Current free space, ledger size, growth rate and projected exhaustion."""
    now = at or _now()
    rows = [r for r in _rows(path) if r.get("at")]
    live = usage(root)
    current_ledger = tree_bytes(root)

    def bare(reason: str, span: float | None = None) -> Headroom:
        return Headroom(
            free_bytes=live["free_bytes"],
            total_bytes=live["total_bytes"],
            ledger_bytes=current_ledger,
            growth_bytes_per_day=None,
            span_hours=span,
            samples=len(rows),
            days_to_full=None,
            exhaustion_at=None,
            reason=reason,
        )

    usable = [r for r in rows if r.get("used_bytes") is not None]
    if len(usable) < 2:
        return bare("needs two samples to measure a rate; one sample is not a trend")

    first, last = usable[0], usable[-1]
    span_hours = (_parse(last["at"]) - _parse(first["at"])).total_seconds() / 3600
    if span_hours < MIN_SPAN_HOURS:
        return bare(
            f"samples span {span_hours:.1f}h; a rate needs at least "
            f"{MIN_SPAN_HOURS:.0f}h or noise dominates the difference",
            span_hours,
        )

    per_day = (last["used_bytes"] - first["used_bytes"]) / (span_hours / 24)

    if per_day <= 0:
        return Headroom(
            free_bytes=live["free_bytes"],
            total_bytes=live["total_bytes"],
            ledger_bytes=current_ledger,
            growth_bytes_per_day=per_day,
            span_hours=span_hours,
            samples=len(rows),
            days_to_full=None,
            # Not growing means no exhaustion date. Projecting one anyway would
            # be a negative number rendered as a forecast.
            exhaustion_at=None,
            reason="not growing over the observed window",
        )

    free = live["free_bytes"]
    if free is None:
        return bare("free space is not readable on this filesystem", span_hours)

    days = free / per_day
    return Headroom(
        free_bytes=free,
        total_bytes=live["total_bytes"],
        ledger_bytes=current_ledger,
        growth_bytes_per_day=per_day,
        span_hours=span_hours,
        samples=len(rows),
        days_to_full=round(days, 1),
        exhaustion_at=(now + timedelta(days=days)).date().isoformat(),
        reason=f"projected from {span_hours:.1f}h of samples",
    )
