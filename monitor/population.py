"""Per-sweep population reconciliation, and the trend series behind it.

The market count has moved 70,820 -> 77,047 -> 84,625 in two days. Each move was
attributed only when somebody noticed the number had changed, which is the wrong
trigger: by the time a count is surprising, every baseline comparison made since
the last check is already suspect.

So the reconciliation runs on every full sweep, against the immediately prior
one, and **the alert is on the unattributed residual rather than on the count**.
A count that moves is expected and uninteresting. A market that moved for a
reason none of the attribution rules explains is the condition that invalidates
baseline comparison, and it is the only one worth a push.

Attribution rules live in :mod:`src.research.reconcile` and are shared with the
one-shot CLI, so the scheduled figures and the investigated ones cannot come to
mean different things.

Memory
------

Reconciliation needs identifiers, which scale with market count -- the one
sanctioned exception to the streaming rule. It is kept to that:

* During the sweep, a compact ledger is streamed to disk (five short fields per
  market, gzipped). Nothing is held.
* After the sweep, three streaming passes over the two ledgers hold at most two
  ticker sets, ~8 MB each at current population. No market objects, and the
  peak lands after the sweep's own peak has been released.
"""

from __future__ import annotations

import csv
import gzip
import json
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.research.reconcile import (
    UNATTRIBUTED,
    attribute_added,
    attribute_removed,
)

LEDGER_DIR = Path("data/monitor/population")
TREND_PATH = Path("data/monitor/population.jsonl")

#: Ledgers to keep. Two is what reconciliation needs; the third is slack for a
#: sweep that fails partway and leaves a truncated file behind.
KEEP_LEDGERS = 3

#: --- PROVISIONAL. ITS ORIGINAL ANCHOR WAS RETRACTED. ----------------------
#: Unattributed markets in one reconciliation before the residual is pushed.
#:
#: This figure was first justified by "25 would still have fired on the 66
#: unattributed markets in the first reconciliation". **That anchor is gone.**
#: The 66 was a classifier artifact: the original `classify` never checked
#: `open_time`, so markets created before the boundary but not yet tradeable
#: were filed as unexplained. Corrected attribution puts that reconciliation's
#: true residual far lower.
#:
#: What remains is a single clean observation:
#:
#:   2026-08-04 -> 2026-08-05 (8.2h, 23,836 moved):  1 unattributed (0.004%)
#:
#: One point is not a distribution, and neither is two points plus a retracted
#: one, so **the threshold is deliberately not re-derived from the corrected
#: history**. 25 is retained as a provisional figure: comfortably above the one
#: clean observation, low enough that a structural change would clear it.
#:
#: The review is dated rather than left to memory -- see
#: :data:`THRESHOLD_REVIEW_DUE`. The residual and residual rate are recorded
#: every sweep, so by then the archive answers it.
UNATTRIBUTED_ALERT_THRESHOLD = 25

THRESHOLD_STATUS = "provisional"

#: Eight weeks of residual-rate data from the date the anchor was retracted.
#: A dated item, not an intention. Surfaced on the panel once due.
THRESHOLD_SET_ON = "2026-08-05"
THRESHOLD_REVIEW_DUE = "2026-09-30"
THRESHOLD_REVIEW_REASON = (
    "The original anchor (66 unattributed) was a classifier artifact and was "
    "retracted. 25 rests on one clean observation of 1. Review against eight "
    "weeks of recorded residual rates; decide then whether an absolute count or "
    "a rate rule is right. Do not re-derive from the corrected history."
)


def threshold_review() -> dict[str, Any]:
    """The dated review item, and whether it has come due."""
    due = datetime.fromisoformat(THRESHOLD_REVIEW_DUE).replace(tzinfo=UTC)
    days = (due - datetime.now(UTC)).days
    return {
        "value": UNATTRIBUTED_ALERT_THRESHOLD,
        "status": THRESHOLD_STATUS,
        "set_on": THRESHOLD_SET_ON,
        "review_due": THRESHOLD_REVIEW_DUE,
        "days_until_due": days,
        "due": days <= 0,
        "reason": THRESHOLD_REVIEW_REASON,
    }

LEDGER_FIELDS = ("ticker", "created_time", "open_time", "close_time", "can_close_early")

#: Time-to-resolution buckets, in hours. **This decides whether growth
#: compounds.** Growth concentrated under 24h is listing cadence -- recurring
#: daily and hourly series replacing expired ones, with total open count
#: plateauing. Growth in the long buckets is genuine exchange expansion and
#: compounds. The same headline percentage implies completely different
#: ceilings depending on which it is.
HORIZON_BUCKETS: tuple[tuple[str, float | None], ...] = (
    ("<24h", 24),
    ("1-7d", 24 * 7),
    ("7-30d", 24 * 30),
    ("30d-1y", 24 * 365),
    (">1y", None),
)


def horizon_bucket(close_time: str | None, now: datetime) -> str:
    """Which resolution bucket a market falls in, or "unknown" without a time."""
    at = _at_or_none(close_time)
    if at is None:
        return "unknown"
    hours = (at - now).total_seconds() / 3600
    for name, upper in HORIZON_BUCKETS:
        if upper is None or hours < upper:
            return name
    return HORIZON_BUCKETS[-1][0]


def _at_or_none(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def empty_horizons() -> dict[str, int]:
    """All buckets present at zero, so a missing bucket is never inferred."""
    return {name: 0 for name, _ in HORIZON_BUCKETS} | {"unknown": 0}


@dataclass(frozen=True)
class Reconciliation:
    prior: str
    current: str
    prior_captured_at: str
    n_prior: int
    n_current: int
    added: int
    removed: int
    added_causes: dict[str, int]
    removed_causes: dict[str, int]
    unattributed: int
    #: Resolution horizon of the whole current population, and of the markets
    #: added since the prior sweep. The second is the one that answers whether
    #: growth compounds.
    horizons: dict[str, int]
    added_horizons: dict[str, int]

    @property
    def moved(self) -> int:
        return self.added + self.removed

    @property
    def unattributed_pct(self) -> float:
        return 0.0 if not self.moved else self.unattributed * 100 / self.moved

    @property
    def fires(self) -> bool:
        return self.unattributed >= UNATTRIBUTED_ALERT_THRESHOLD

    def as_dict(self) -> dict[str, Any]:
        return {
            "prior": self.prior,
            "current": self.current,
            "prior_captured_at": self.prior_captured_at,
            "n_prior": self.n_prior,
            "n_current": self.n_current,
            "delta": self.n_current - self.n_prior,
            "added": self.added,
            "removed": self.removed,
            "moved": self.moved,
            "added_causes": self.added_causes,
            "removed_causes": self.removed_causes,
            "unattributed": self.unattributed,
            "unattributed_pct": round(self.unattributed_pct, 4),
            "threshold": UNATTRIBUTED_ALERT_THRESHOLD,
            "threshold_status": THRESHOLD_STATUS,
            "fires": self.fires,
            "horizons": self.horizons,
            "added_horizons": self.added_horizons,
            "added_short_dated_pct": round(self.added_short_dated_pct, 1),
        }

    @property
    def added_short_dated_pct(self) -> float:
        """Share of added markets resolving within 24 hours.

        High means listing cadence and a plateauing open count. Low means the
        long-dated population is growing, and that is what compounds.
        """
        added = sum(self.added_horizons.values())
        return 0.0 if not added else self.added_horizons.get("<24h", 0) * 100 / added


class LedgerWriter:
    """Streams the reconciliation projection to disk during a sweep."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fh = None
        self._writer = None
        self.n_rows = 0

    def __enter__(self) -> LedgerWriter:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = gzip.open(self.path, "wt", newline="")
        self._writer = csv.DictWriter(self._fh, fieldnames=LEDGER_FIELDS)
        self._writer.writeheader()
        return self

    def write(self, row: Any) -> None:
        source = row if isinstance(row, dict) else row.__dict__
        self._writer.writerow({f: source.get(f, "") for f in LEDGER_FIELDS})
        self.n_rows += 1

    def __exit__(self, *exc: object) -> None:
        if self._fh is not None:
            self._fh.close()


def read_ledger(path: Path) -> Iterator[dict[str, str]]:
    with gzip.open(path, "rt") as fh:
        yield from csv.DictReader(fh)


LEDGER_SUFFIX = ".csv.gz"
LEDGER_STAMP_FORMAT = "%Y%m%dT%H%M%SZ"


def ledger_path(stamp: str, directory: Path = LEDGER_DIR) -> Path:
    return directory / f"{stamp}{LEDGER_SUFFIX}"


def stamp_of(path: Path) -> str:
    """The sweep stamp in a ledger filename.

    Not ``Path.stem``: that strips one suffix, leaving ``...Z.csv`` for a
    double-extension name, which then fails to parse as a time.
    """
    return path.name[: -len(LEDGER_SUFFIX)] if path.name.endswith(LEDGER_SUFFIX) else path.stem


def captured_at(path: Path) -> datetime:
    """The sweep time encoded in a ledger's filename."""
    return datetime.strptime(stamp_of(path), LEDGER_STAMP_FORMAT).replace(tzinfo=UTC)


def previous_ledger(current: Path, directory: Path = LEDGER_DIR) -> Path | None:
    """The most recent ledger that is not the one just written."""
    if not directory.exists():
        return None
    others = sorted(p for p in directory.glob("*.csv.gz") if p != current)
    return others[-1] if others else None


def prune_ledgers(directory: Path = LEDGER_DIR, keep: int = KEEP_LEDGERS) -> list[Path]:
    if not directory.exists():
        return []
    ledgers = sorted(directory.glob("*.csv.gz"))
    stale = ledgers[:-keep] if len(ledgers) > keep else []
    for path in stale:
        path.unlink()
    return stale


def reconcile(
    prior_path: Path,
    current_path: Path,
    prior_captured_at: datetime,
    now: datetime | None = None,
) -> Reconciliation:
    """Set difference between two sweeps, attributed cause by cause.

    Three streaming passes; at most two ticker sets resident.
    """
    now = now or datetime.now(UTC)

    prior_tickers = {row["ticker"] for row in read_ledger(prior_path)}

    added_causes: Counter[str] = Counter()
    horizons: Counter[str] = Counter()
    added_horizons: Counter[str] = Counter()
    current_tickers: set[str] = set()
    for row in read_ledger(current_path):
        current_tickers.add(row["ticker"])
        bucket = horizon_bucket(row.get("close_time"), now)
        horizons[bucket] += 1
        if row["ticker"] not in prior_tickers:
            added_causes[attribute_added(row, prior_captured_at)] += 1
            added_horizons[bucket] += 1

    removed_causes: Counter[str] = Counter()
    for row in read_ledger(prior_path):
        if row["ticker"] not in current_tickers:
            removed_causes[attribute_removed(row, now)] += 1

    n_prior, n_current = len(prior_tickers), len(current_tickers)
    del prior_tickers, current_tickers

    return Reconciliation(
        prior=prior_path.name,
        current=current_path.name,
        prior_captured_at=prior_captured_at.isoformat(),
        n_prior=n_prior,
        n_current=n_current,
        added=sum(added_causes.values()),
        removed=sum(removed_causes.values()),
        added_causes=dict(added_causes),
        removed_causes=dict(removed_causes),
        unattributed=added_causes[UNATTRIBUTED] + removed_causes[UNATTRIBUTED],
        horizons=empty_horizons() | dict(horizons),
        added_horizons=empty_horizons() | dict(added_horizons),
    )


def record_trend(
    at: datetime,
    n_markets: int,
    n_two_sided: int,
    reconciliation: Reconciliation | None,
    path: Path = TREND_PATH,
) -> None:
    """Append one point per sweep.

    Total count and the two-sided subset are recorded separately, because
    growth in total with a flat two-sided count means something different from
    both growing together -- new listings that never attract a book are not the
    same event as the tradeable universe expanding.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    point: dict[str, Any] = {
        "at": at.isoformat(),
        "n_markets": n_markets,
        "n_two_sided": n_two_sided,
    }
    if reconciliation is not None:
        point.update(
            {
                "added": reconciliation.added,
                "removed": reconciliation.removed,
                "unattributed": reconciliation.unattributed,
                # Recorded per sweep so net change *per bucket* is answerable
                # from the archive: the headline count cannot distinguish
                # listing cadence from expansion, and the buckets can.
                "horizons": reconciliation.horizons,
                "added_horizons": reconciliation.added_horizons,
            }
        )
    with path.open("a") as fh:
        fh.write(json.dumps(point) + "\n")


def trend(path: Path = TREND_PATH, limit: int = 240) -> list[dict[str, Any]]:
    """The most recent points, oldest first. Empty until a sweep has run."""
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return rows[-limit:]


def render(r: Reconciliation) -> str:
    lines = [
        f"{r.prior} -> {r.current}: {r.n_prior:,} -> {r.n_current:,} "
        f"({r.n_current - r.n_prior:+,})",
        f"  added {r.added:,}, removed {r.removed:,}, moved {r.moved:,}",
    ]
    for cause, n in sorted(r.added_causes.items(), key=lambda kv: -kv[1]):
        lines.append(f"    +{n:>7,}  {cause}")
    for cause, n in sorted(r.removed_causes.items(), key=lambda kv: -kv[1]):
        lines.append(f"    -{n:>7,}  {cause}")
    lines.append(
        f"  unattributed {r.unattributed:,} ({r.unattributed_pct:.3f}% of moved) "
        f"against a {THRESHOLD_STATUS} threshold of {UNATTRIBUTED_ALERT_THRESHOLD}"
    )
    lines.append(
        "  added by horizon: "
        + ", ".join(f"{k} {v:,}" for k, v in r.added_horizons.items() if v)
        + f"  ({r.added_short_dated_pct:.0f}% resolve within 24h)"
    )
    return "\n".join(lines)
