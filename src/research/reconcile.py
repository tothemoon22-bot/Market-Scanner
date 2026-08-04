"""Population reconciliation: baseline versus a live sweep, by set difference.

A market count that moves is not evidence of anything on its own. Two
populations differing by 9% could be genuine new listings, a changed status
filter, RFQ-shell handling, pagination truncation, or a changed API default --
and until it is known which, every baseline-versus-live comparison risks
measuring two different things.

So this compares identifiers, not counts, and attributes every difference to a
cause. Causes are read off the market objects themselves (``created_time``,
``status``, ``market_type``, ticker prefix) rather than inferred from the size of
the gap.

Usage::

    python -m src.research.reconcile --baseline data/sweeps/<dir> --live data/live/<dir>
    python -m src.research.reconcile --baseline data/sweeps/<dir> --sweep-now
"""

from __future__ import annotations

import argparse
import glob
import gzip
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RFQ_PREFIX = "KXMVE"


def load_raw(sweep: Path) -> dict[str, dict[str, Any]]:
    """Every market object from a sweep's raw pages, keyed by ticker."""
    out: dict[str, dict[str, Any]] = {}
    pages = sorted(glob.glob(str(sweep / "raw" / "markets_page_*.json.gz")))
    if not pages:
        raise FileNotFoundError(f"no raw market pages under {sweep}")
    for page in pages:
        with gzip.open(page, "rt") as fh:
            for market in json.load(fh).get("markets", []):
                out[market["ticker"]] = market
    return out


def sweep_now(out_dir: Path) -> dict[str, dict[str, Any]]:
    """Take a fresh sweep, persisting raw pages so this is auditable later."""
    import httpx

    from monitor.collect import BASE, MVE_FILTER, PAGE_SIZE, _get

    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    markets: dict[str, dict[str, Any]] = {}
    cursor: str | None = None
    page = 0
    with httpx.Client(timeout=60.0, headers={"Accept": "application/json"}) as client:
        while True:
            params: dict[str, str | int] = {
                "status": "open",
                "limit": PAGE_SIZE,
                "mve_filter": MVE_FILTER,
            }
            if cursor:
                params["cursor"] = cursor
            resp = _get(client, f"{BASE}/markets", params)
            with gzip.open(raw_dir / f"markets_page_{page:04d}.json.gz", "wt") as fh:
                fh.write(resp.text)
            payload = resp.json()
            batch = payload.get("markets") or []
            for market in batch:
                markets[market["ticker"]] = market
            cursor = payload.get("cursor") or None
            page += 1
            if not cursor or not batch:
                break
    (out_dir / "manifest.json").write_text(
        json.dumps(
            {"captured_at": datetime.now(UTC).isoformat(), "n_markets": len(markets)}, indent=2
        )
    )
    return markets


def series_of(market: dict) -> str:
    return market.get("event_ticker", "").split("-")[0]


def classify(market: dict, boundary: datetime | None) -> str:
    """Why is this market in one population and not the other?"""
    ticker = market["ticker"]
    if ticker.startswith(RFQ_PREFIX):
        return "rfq-shell leaked past mve_filter"
    if market.get("status") != "active":
        return f"status={market.get('status')!r} (not active)"
    created = market.get("created_time")
    if created and boundary:
        when = datetime.fromisoformat(created.replace("Z", "+00:00"))
        if when > boundary:
            return "listed after the baseline sweep"
        return "present before the baseline sweep but absent from it"
    return "no created_time to attribute"


def run(baseline_dir: Path, live: dict[str, dict[str, Any]], live_label: str) -> dict[str, Any]:
    base = load_raw(baseline_dir)
    boundary_raw = json.loads((baseline_dir / "manifest.json").read_text())["captured_at"]
    boundary = datetime.fromisoformat(boundary_raw)

    only_live = {t: m for t, m in live.items() if t not in base}
    only_base = {t: m for t, m in base.items() if t not in live}
    both = set(base) & set(live)

    print(f"baseline {baseline_dir.name}: {len(base):,} markets, captured {boundary_raw}")
    print(f"live     {live_label}: {len(live):,} markets")
    print(f"delta    {len(live) - len(base):+,}  "
          f"({(len(live) - len(base)) / len(base) * 100:+.1f}%)\n")
    print(f"in both:          {len(both):,}")
    print(f"live only:        {len(only_live):,}")
    print(f"baseline only:    {len(only_base):,}")
    print(f"identity check:   {len(both) + len(only_live)} == {len(live)} "
          f"-> {len(both) + len(only_live) == len(live)}\n")

    causes = Counter(classify(m, boundary) for m in only_live.values())
    print("LIVE-ONLY, by cause:")
    for cause, n in causes.most_common():
        print(f"  {n:7,}  {cause}")

    by_series = Counter(series_of(m) for m in only_live.values())
    print(f"\nLIVE-ONLY, top series ({len(by_series)} distinct):")
    for name, n in by_series.most_common(12):
        print(f"  {n:7,}  {name}")

    print("\nBASELINE-ONLY, by cause:")
    for cause, n in Counter(
        "closed or delisted since the baseline" for _ in only_base.values()
    ).most_common():
        print(f"  {n:7,}  {cause}")
    base_series = Counter(series_of(m) for m in only_base.values())
    print(f"\nBASELINE-ONLY, top series ({len(base_series)} distinct):")
    for name, n in base_series.most_common(8):
        print(f"  {n:7,}  {name}")

    close_soon = sum(
        1
        for m in only_base.values()
        if m.get("close_time")
        and datetime.fromisoformat(m["close_time"].replace("Z", "+00:00")) < datetime.now(UTC)
    )
    print(f"\nof the baseline-only markets, {close_soon:,} have a close_time in the past "
          f"({close_soon / max(len(only_base), 1) * 100:.0f}%) - expected to have settled")

    return {
        "baseline": baseline_dir.name,
        "live": live_label,
        "n_baseline": len(base),
        "n_live": len(live),
        "n_both": len(both),
        "n_live_only": len(only_live),
        "n_baseline_only": len(only_base),
        "live_only_causes": dict(causes),
        "baseline_only_settled": close_soon,
    }


def audit_event(baseline_dir: Path, live: dict, event: str) -> None:
    """Was a specific event in the baseline population, and at what price?"""
    from decimal import Decimal as D

    base = load_raw(baseline_dir)

    def basket(pop: dict) -> tuple[int, D, D]:
        legs = [m for m in pop.values() if m.get("event_ticker") == event]
        if not legs:
            return 0, D(0), D(0)
        cost = sum(D(100) - D(m.get("no_bid_dollars") or "0") * 100 for m in legs)
        cap = min(D(m.get("yes_ask_size_fp") or "0") for m in legs)
        return len(legs), cost, cap

    b_legs, b_cost, b_cap = basket(base)
    l_legs, l_cost, l_cap = basket(live)
    print(f"\n=== {event} ===")
    if not b_legs:
        print("  ABSENT from the baseline population -> any 'new' alert on it is an artifact")
    else:
        print(f"  baseline: {b_legs} legs, sum(ask) {b_cost:.2f}c, capacity {b_cap} "
              f"-> {'ABOVE' if b_cost >= 100 else 'BELOW'} par")
    if not l_legs:
        print("  absent from the live population")
    else:
        print(f"  live:     {l_legs} legs, sum(ask) {l_cost:.2f}c, capacity {l_cap} "
              f"-> {'ABOVE' if l_cost >= 100 else 'BELOW'} par")
    if b_legs and l_legs:
        verdict = (
            "GENUINE: present at baseline, above par, now below"
            if b_cost >= 100 > l_cost
            else "not a baseline->below-par transition"
        )
        print(f"  verdict:  {verdict}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=Path("data/sweeps/20260803T070632Z_t0"))
    parser.add_argument("--live", type=Path, default=None)
    parser.add_argument("--sweep-now", action="store_true")
    parser.add_argument("--audit", action="append", default=["KXGDPYEAR-28"])
    args = parser.parse_args()

    if args.sweep_now:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        out = Path("data/live") / stamp
        live = sweep_now(out)
        label = str(out)
    elif args.live:
        live = load_raw(args.live)
        label = args.live.name
    else:
        parser.error("pass --live <dir> or --sweep-now")

    run(args.baseline, live, label)
    for event in args.audit:
        audit_event(args.baseline, live, event)


if __name__ == "__main__":
    main()
