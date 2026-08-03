"""Part 0 closing query — the fee-free universe, where the fee gate is zero.

The exchange-wide Phase 0.5 result was computed under the standard fee
schedule. Series with ``fee_multiplier: 0`` are the one regime where the fee
term vanishes, so every Phase 0.5 rejection that leaned on the fee gate has to
be re-tested there before the file can close.

**The maker-fee subset is NOT part of that regime.** ``quadratic_with_maker_fees``
carries the same 0.07 taker coefficient as a standard series; it merely adds a
maker fee on top. For anyone crossing the spread the fee is identical, so those
series are never cheaper and are excluded from the zero-fee analysis. What makes
them interesting is their 1c median spread, which is a separate matter.

Bound semantics, which took three attempts to get right:

    less(C)        covers (-inf, C)   -- C exclusive
    between[F, C]  covers [F, C]      -- both inclusive
    greater(F)     covers (F, +inf)   -- F exclusive

So a tiling has a join of **zero** at both open ends and of exactly one
granularity step between adjacent interior buckets. Requiring every join to be
identical wrongly rejects every partition on the exchange; requiring a fixed
0.01 step wrongly rejects every partition whose underlying is not reported in
cents. Granularity is inferred from the interior joins and must be consistent.

Usage::

    python -m src.research.fee_free_check [--sweep <dir>] [--compare <dir>]
"""

from __future__ import annotations

import argparse
import glob
import gzip
import json
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal as D
from pathlib import Path

# Annualized-return threshold from the closing spec's decision rule.
ANNUALIZED_GATE = D("0.20")


def load_markets(sweep: Path) -> dict[str, list[dict]]:
    by_event: dict[str, list[dict]] = defaultdict(list)
    for page in sorted(glob.glob(str(sweep / "raw" / "markets_page_*.json.gz"))):
        with gzip.open(page, "rt") as fh:
            for m in json.load(fh).get("markets", []):
                by_event[m.get("event_ticker", "")].append(m)
    return by_event


def fee_free_series(sweep: Path) -> set[str]:
    """Series with fee_multiplier 0, read from the sweep's own series capture."""
    out: set[str] = set()
    for path in glob.glob(str(sweep / "raw" / "series_*.json.gz")):
        with gzip.open(path, "rt") as fh:
            for s in json.load(fh).get("series") or []:
                if D(str(s.get("fee_multiplier", 1))) == 0:
                    out.add(s["ticker"])
    return out


def ask_cents(market: dict) -> D:
    """ask(YES) = 100 - bid(NO). Never read an ask field."""
    return D(100) - D(market.get("no_bid_dollars") or "0") * 100


def ask_size(market: dict) -> D:
    return D(market.get("yes_ask_size_fp") or "0")


def years_to_close(market: dict) -> D:
    closes = datetime.fromisoformat(market["close_time"].replace("Z", "+00:00"))
    return D(max((closes - datetime.now(UTC)).days, 1)) / D("365.25")


def annualized(gross_cents: D, cost_cents: D, years: D) -> D:
    """Simple annualization. Compounding would flatter these numbers, not save them."""
    if cost_cents <= 0 or years <= 0:
        return D(0)
    return (gross_cents / cost_cents) / years


def verify_partition(legs: list[dict]) -> tuple[bool, str]:
    lows = [m for m in legs if m.get("strike_type") == "less"]
    mids = sorted(
        (m for m in legs if m.get("strike_type") == "between"),
        key=lambda m: D(str(m["floor_strike"])),
    )
    highs = [m for m in legs if m.get("strike_type") == "greater"]
    if len(lows) != 1 or len(highs) != 1:
        return False, "no open bucket at one or both ends"
    if len(lows) + len(mids) + len(highs) != len(legs):
        return False, "event contains markets that are not range buckets"

    if D(str(mids[0]["floor_strike"])) != D(str(lows[0]["cap_strike"])):
        return False, "gap between the bottom bucket and the first interior bucket"

    steps = set()
    cursor = D(str(mids[0]["cap_strike"]))
    for m in mids[1:]:
        steps.add(D(str(m["floor_strike"])) - cursor)
        cursor = D(str(m["cap_strike"]))
    if len(steps) != 1:
        return False, f"interior joins disagree: {sorted(steps)}"
    if D(str(highs[0]["floor_strike"])) != cursor:
        return False, "gap between the last interior bucket and the top bucket"

    step = steps.pop()
    if step <= 0:
        return False, f"buckets overlap by {-step}"
    return True, f"tiles the line at granularity {step}"


def run(sweep: Path, compare: Path | None) -> None:
    free = fee_free_series(sweep)
    t0 = load_markets(sweep)
    t1 = load_markets(compare) if compare else {}

    print(f"sweep {sweep.name}; fee-free series: {len(free)} -> {sorted(free)}\n")

    print("=== 0.1  Detector 2: verified-exhaustive partitions, zero fee gate ===\n")
    print(f"{'event':22s} {'legs':>4} {'partition':>9} {'t0':>6} {'t1':>6} {'yrs':>6} "
          f"{'minsize':>7} {'ann.ret':>8}")
    partitions = 0
    below = []
    for event, legs in sorted(t0.items()):
        if event.split("-")[0] not in free or len(legs) < 2:
            continue
        ok, why = verify_partition(legs)
        total = sum(ask_cents(m) for m in legs)
        t1_total = (
            sum(ask_cents(m) for m in t1[event]) if event in t1 and t1[event] else None
        )
        min_size = min(ask_size(m) for m in legs)
        yrs = years_to_close(legs[0])
        # Only a verified partition has a meaningful sum-of-asks. Nested
        # horizons (KXGREENLAND, KXGAMBLINGREPEAL) sum to well under 100c
        # because they are cumulative, not exclusive -- printing a return for
        # those would manufacture a 4000%/yr "opportunity" out of a category
        # error, which is the Phase 0.5 residual trap in a new costume.
        show_return = ok and total < 100
        ann = annualized(D(100) - total, total, yrs) if show_return else D(0)
        partitions += ok
        if ok and total < 100:
            below.append((event, legs, total, t1_total, min_size, yrs, ann))
        print(
            f"{event:22s} {len(legs):4d} {('YES' if ok else 'no'):>9} {total:6.0f} "
            f"{(f'{t1_total:.0f}' if t1_total is not None else '-'):>6} {yrs:6.2f} "
            f"{min_size:7.0f} {(f'{ann:.1%}' if show_return else '-'):>8}"
            + ("" if ok else f"   <- {why}")
        )

    print(f"\nverified partitions: {partitions};  below par: {len(below)}")
    for event, legs, total, t1_total, min_size, yrs, ann in below:
        gross = D(100) - total
        dead = [m["ticker"] for m in legs if ask_size(m) == 0]
        print(f"\n  {event}: {total:.0f}c, gross {gross:.0f}c over {yrs:.2f}y "
              f"-> {ann:.2%}/yr, persisted at t1: {t1_total == total}")
        print(f"    binding size {min_size:.0f} contracts -> max profit "
              f"{gross * min_size / 100:.2f} dollars for the whole holding period")
        if dead:
            print(f"    NOT BUYABLE: {len(dead)} leg(s) show no offer size -> {dead}")

    print("\n=== 0.2  Detectors 3-5 on fee-free instruments ===\n")
    nested = defaultdict(list)
    for event, legs in t0.items():
        series = event.split("-")[0]
        if series in free and all(m.get("strike_type") in (None, "custom") for m in legs):
            for m in legs:
                nested[series].append(m)

    if not nested:
        print("  no fee-free calendar/nested instruments found")
    for series, legs in sorted(nested.items()):
        if len(legs) < 2:
            continue
        print(f"  {series}: {len(legs)} nested horizons")
        for m in sorted(legs, key=lambda x: x["close_time"]):
            bid = D(m.get("yes_bid_dollars") or "0") * 100
            print(f"    close={m['close_time'][:10]} bid={bid:.0f}c "
                  f"ask={ask_cents(m):.0f}c  {m.get('yes_sub_title', '')[:34]}")
        ordered = sorted(legs, key=lambda x: x["close_time"])
        violations = [
            (a["ticker"], b["ticker"], D(a.get("yes_bid_dollars") or "0") * 100 - ask_cents(b))
            for a, b in zip(ordered, ordered[1:], strict=False)
            if D(a.get("yes_bid_dollars") or "0") * 100 > ask_cents(b)
        ]
        print(f"    calendar monotonicity violations (bid(short) > ask(long)): {len(violations)}")
        for short, long_, credit in violations:
            print(f"      credit {credit:.0f}c  {short} -> {long_}")

    print("\n  Detectors 3 and 4 have no fee-free instruments to run on: the fee-free")
    print("  range-bucket series carry no separate cumulative threshold markets, and on")
    print("  a partition the monotonicity and convexity conditions reduce to")
    print("  'bucket prices are non-negative', which the tick structure guarantees.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sweep", type=Path, default=Path("data/sweeps/20260803T070632Z_t0"))
    parser.add_argument("--compare", type=Path, default=Path("data/sweeps/20260803T071019Z_t1"))
    args = parser.parse_args()
    run(args.sweep, args.compare if args.compare.exists() else None)


if __name__ == "__main__":
    main()
