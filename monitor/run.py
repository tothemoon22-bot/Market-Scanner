"""Standing monitor entry point.

    python -m monitor.run                      # sweep, archive, compare, alert
    python -m monitor.run --from <snapshot>    # recompute from a stored snapshot
    python -m monitor.run --write-baseline     # regenerate monitor/baseline.json

Read-only, public endpoints, no credentials. Intended to run weekly and say
nothing. Every run is appended to the archive whether or not it alerts.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

from monitor import aggregate, collect, pipeline
from monitor import alerts as alerts_mod

BASELINE = Path("monitor/baseline.json")
ARCHIVE = Path("data/monitor")

CATEGORIES = [
    "Climate and Weather",
    "Commodities",
    "Companies",
    "Crypto",
    "Culture",
    "Economics",
    "Elections",
    "Entertainment",
    "Exotics",
    "Financials",
    "Health",
    "Mentions",
    "Politics",
    "Science and Technology",
    "Sports",
    "Transportation",
    "World",
]


def fetch_fee_changes() -> dict:
    """Scheduled fee overrides, the one coefficient signal the API exposes.

    The taker coefficient and rounding rule live in a published PDF, not the
    API, so they cannot be read programmatically. These two endpoints are the
    available proxy: the exchange publishes pending per-series and per-event fee
    changes here. A non-empty response means read the schedule by hand.
    """
    with httpx.Client(timeout=30.0) as client:
        series = collect._get(client, f"{collect.BASE}/series/fee_changes").json()
        events = collect._get(client, f"{collect.BASE}/events/fee_changes").json()
    return {
        "series": series.get("series_fee_change_arr") or [],
        "events": events.get("event_fee_changes") or [],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="source", type=Path, default=None)
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument("--no-archive", action="store_true")
    args = parser.parse_args()

    # Both paths stream: nothing that scales with market count is materialised,
    # here or in the sweep. See monitor/aggregate.py.
    if args.source:
        agg = aggregate.SweepAggregate().fold(collect.iter_snapshot(args.source))
        fee_changes = None
        snapshot_dir = args.source
    else:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        snapshot_dir = ARCHIVE / stamp
        agg, _manifest = collect.sweep(
            CATEGORIES, archive_to=None if args.no_archive else snapshot_dir
        )
        fee_changes = fetch_fee_changes()

    current = agg.result()
    # Bounded by series count. Captured before the aggregate is released so the
    # breadth criterion can tell a cross-category schedule revision from a batch
    # of listing operations inside one product line.
    series_categories = dict(agg.series_category)
    del agg

    if args.write_baseline:
        BASELINE.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n")
        print(f"wrote {BASELINE} from {snapshot_dir}")
        return 0

    baseline = json.loads(BASELINE.read_text())
    # One assessment path, shared with scanner/engine.py. This call site used to
    # omit `bands`, which defaults to None, so the weekly job's below-par
    # classification silently skipped its band branch -- the same shape as the
    # fee_changes bug, in the other direction. See monitor/pipeline.py.
    assessment = pipeline.assess(
        baseline, current, fee_changes=fee_changes, series_categories=series_categories
    )
    fired = assessment.alerts

    if not args.source and not args.no_archive:
        (snapshot_dir / "metrics.json").write_text(
            json.dumps(current, indent=2, sort_keys=True) + "\n"
        )

    print(alerts_mod.render(fired))
    return 1 if fired else 0


if __name__ == "__main__":
    raise SystemExit(main())
