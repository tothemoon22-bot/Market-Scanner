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
BAND_EVENTS_PATH = Path("data/monitor/band_events.jsonl")

#: Rolling window for the suppressed count and sparkline on the trigger board.
SUPPRESSED_WINDOW_DAYS = 30

#: A series suppressed more often than this in one window is named individually
#: in the monthly review. Not a threshold that acts -- a threshold that reports.
REPEAT_SUPPRESSION_THRESHOLD = 4


@dataclass(frozen=True)
class Band:
    """One series' observed cost range, and how much time it actually covers.

    ``observations`` counts distinct sweeps; ``rows`` counts ledger lines and
    ``events`` the contracts they came from. Keeping all three visible is what
    makes the difference between "seen eleven times" and "eleven contracts seen
    once" legible on the panel instead of collapsed into one flattering number.

    **Known limitation, flagged rather than changed.** The range pools every
    event in the series, so KXGDPYEAR-28 and KXGDPYEAR-36 share one band even
    though they are different contracts with genuinely different fair values.
    That makes the band wider than it should be, and a wider band is *less*
    sensitive: ``is_outside`` can only ever promote a detection to "new", never
    demote one, so this under-detects novelty rather than manufacturing
    suppression. Re-keying bands per event would fix it and is a spec change,
    not a bug fix -- see monitor/README.md.
    """

    series: str
    observations: int
    min_cost_cents: D | None
    max_cost_cents: D | None
    crossings: int
    rows: int = 0
    events: int = 0

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
            "threshold": MIN_OBSERVATIONS_FOR_BAND,
            "needs": max(0, MIN_OBSERVATIONS_FOR_BAND - self.observations),
            "rows": self.rows,
            "events": self.events,
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
    """One band per series, from whatever history exists.

    **An observation is a sweep, not a row.** ``record()`` writes one row per
    partition per sweep, so a series with eleven listed years produced eleven
    rows the first time it was ever seen -- and counting rows declared its band
    established, with a "range" that was a cross-section of eleven different
    contracts at one instant rather than one contract over eleven moments.
    Caught on the first live run: KXGDPYEAR reported 11 observations, KNOWN,
    90c-118c, after a single sweep.

    The count is therefore distinct capture times. Eight of those is eight
    sweeps, which is what :data:`MIN_OBSERVATIONS_FOR_BAND` has always claimed
    to mean.
    """
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
            observations=len({o["at"] for o in observations}),
            rows=len(observations),
            events=len(by_event),
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


def load_suppressed(path: Path = SUPPRESSED_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _empty_window(days: int) -> dict[str, Any]:
    return {
        "count": 0,
        "window_days": days,
        "observing_since": None,
        "by_reason": {},
        "by_series": {},
        "daily": None,
        "median_dollar_value": None,
        "max_dollar_value": None,
    }


def _daily(
    rows: list[dict[str, Any]], observing_since: datetime, days: int
) -> list[dict[str, Any]]:
    """One bucket per day for the sparkline.

    ``observed`` distinguishes a day with no suppressions from a day before the
    ledger existed. A sparkline that draws both as zero is asserting an
    observation it never made.
    """
    today = datetime.now(UTC).date()
    counts: dict[str, int] = {}
    for r in rows:
        counts[datetime.fromisoformat(r["at"]).date().isoformat()] = (
            counts.get(datetime.fromisoformat(r["at"]).date().isoformat(), 0) + 1
        )
    out = []
    for back in range(days - 1, -1, -1):
        day = today.fromordinal(today.toordinal() - back)
        out.append(
            {
                "day": day.isoformat(),
                "count": counts.get(day.isoformat(), 0),
                "observed": day >= observing_since.date(),
            }
        )
    return out


def suppressed_window(
    path: Path = SUPPRESSED_PATH, days: int = SUPPRESSED_WINDOW_DAYS
) -> dict[str, Any]:
    """Rolling suppressed-detection stats, for the trigger board and sparkline.

    **Accumulating means the dollar floor is masking a real change.** That is a
    read-the-ledger event, not a raise-the-floor event -- the count exists so a
    threshold cannot quietly hide a change while the monitor still looks healthy.
    """
    rows = load_suppressed(path)
    if not rows:
        return _empty_window(days)

    observing_since = min(datetime.fromisoformat(r["at"]) for r in rows)
    cutoff = datetime.now(UTC).timestamp() - days * 86400
    recent = [r for r in rows if datetime.fromisoformat(r["at"]).timestamp() >= cutoff]

    by_reason: dict[str, int] = {}
    by_series: dict[str, int] = {}
    for r in recent:
        by_reason[r["because"]] = by_reason.get(r["because"], 0) + 1
        s = series_of(r["event"])
        by_series[s] = by_series.get(s, 0) + 1

    values = sorted(D(str(r["dollar_value"])) for r in recent if r.get("dollar_value") is not None)
    return {
        "count": len(recent),
        "window_days": days,
        "observing_since": observing_since.isoformat(),
        "by_reason": by_reason,
        "by_series": dict(sorted(by_series.items(), key=lambda kv: (-kv[1], kv[0]))),
        "daily": _daily(recent, observing_since, days),
        "median_dollar_value": str(values[len(values) // 2]) if values else None,
        "max_dollar_value": str(values[-1]) if values else None,
    }


def review(
    path: Path = SUPPRESSED_PATH, days: int = SUPPRESSED_WINDOW_DAYS
) -> dict[str, Any]:
    """Monthly suppressed-ledger review. Reports; never acts.

    If ``max_dollar_value`` approaches the floor from below over consecutive
    months, that is a structural change the floor is hiding. It is surfaced in
    the heartbeat so it is on the record, and it is deliberately not wired to
    anything that would move the floor on its own.
    """
    window = suppressed_window(path, days)
    repeat = {
        series: n
        for series, n in window["by_series"].items()
        if n > REPEAT_SUPPRESSION_THRESHOLD
    }
    return {
        "window_days": days,
        "count": window["count"],
        "by_series": window["by_series"],
        "by_reason": window["by_reason"],
        "repeat_series": repeat,
        "repeat_threshold": REPEAT_SUPPRESSION_THRESHOLD,
        "median_dollar_value": window["median_dollar_value"],
        "max_dollar_value": window["max_dollar_value"],
        "floor_dollars": str(MIN_DOLLAR_VALUE),
        "observing_since": window["observing_since"],
    }


def render_review(r: dict[str, Any]) -> str:
    """Plain text for the heartbeat body. No push of its own."""
    if not r["count"]:
        return f"suppressed ledger: nothing in the last {r['window_days']}d."
    lines = [
        f"suppressed ledger, last {r['window_days']}d: {r['count']} detection(s)",
        f"  capacity x edge: median ${r['median_dollar_value']}, "
        f"max ${r['max_dollar_value']} against a ${r['floor_dollars']} floor",
    ]
    for series, n in list(r["by_series"].items())[:5]:
        lines.append(f"  {series}: {n}")
    if r["repeat_series"]:
        named = ", ".join(f"{s} ({n}x)" for s, n in r["repeat_series"].items())
        lines.append(f"  suppressed more than {r['repeat_threshold']}x: {named}")
    return "\n".join(lines)


def established_series(path: Path = BAND_EVENTS_PATH) -> set[str]:
    """Series already recorded as having crossed into an established band.

    Read from the ledger rather than from in-process memory on purpose: a
    transition detector whose "previous state" resets on restart re-fires every
    band it has ever seen, every time the box reboots.
    """
    if not path.exists():
        return set()
    return {
        json.loads(line)["series"]
        for line in path.read_text().splitlines()
        if line.strip()
    }


def record_band_transitions(
    band_map: dict[str, Band], path: Path = BAND_EVENTS_PATH
) -> list[dict[str, Any]]:
    """Date the UNKNOWN -> KNOWN crossings, once each. Returns the new ones."""
    already = established_series(path)
    fresh = [b for name, b in sorted(band_map.items()) if b.known and name not in already]
    if not fresh:
        return []
    path.parent.mkdir(parents=True, exist_ok=True)
    at = datetime.now(UTC).isoformat()
    events = []
    with path.open("a") as fh:
        for band in fresh:
            event = {
                "at": at,
                "series": band.series,
                "observations": band.observations,
                "min_cost_cents": str(band.min_cost_cents),
                "max_cost_cents": str(band.max_cost_cents),
            }
            fh.write(json.dumps(event) + "\n")
            events.append(event)
    return events
