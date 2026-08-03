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

from monitor import alerts as alerts_mod
from monitor import collect, metrics

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

    if args.source:
        rows = collect.read_snapshot(args.source)
        fee_changes = None
        snapshot_dir = args.source
    else:
        rows_typed, manifest = collect.collect(CATEGORIES)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        snapshot_dir = ARCHIVE / stamp
        if not args.no_archive:
            collect.write_snapshot(rows_typed, manifest, snapshot_dir)
        rows = collect.read_snapshot(snapshot_dir) if not args.no_archive else []
        fee_changes = fetch_fee_changes()

    current = metrics.compute(rows)

    if args.write_baseline:
        BASELINE.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n")
        print(f"wrote {BASELINE} from {snapshot_dir}")
        return 0

    baseline = json.loads(BASELINE.read_text())
    fired = alerts_mod.evaluate(baseline, current, fee_changes)

    if not args.source and not args.no_archive:
        (snapshot_dir / "metrics.json").write_text(
            json.dumps(current, indent=2, sort_keys=True) + "\n"
        )

    print(alerts_mod.render(fired))
    return 1 if fired else 0


if __name__ == "__main__":
    raise SystemExit(main())
