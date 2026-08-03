"""Phase 0.5b — does cross-market inconsistency persist on Kalshi?

Detectors 2-5 all rest on the premise that Kalshi does not match orders across
markets. The rulebook supports it (Rule 5.9/5.10 describe matching entirely
within a Contract's own book) but absence of a rule is weak evidence. This
tests the premise observationally: if the engine enforced consistency between
markets, a strike-ladder monotonicity violation could never be observed.

For K2 > K1 the event {X >= K2} is a subset of {X >= K1}, so P(>=K2) <= P(>=K1)
must hold. The riskless capture is buy K1 / sell K2, whose payoff is
1{K1 <= X < K2} >= 0 in every state, for a cost of ask(K1) - bid(K2). A
violation worth acting on therefore needs **bid(K2) > ask(K1)**: being paid to
hold a non-negative payoff.

Two filters matter enormously and both were found the hard way:

1. **Duplicate strikes in one event.** Sports spread events list markets for
   both competitors at the same numeric strike. Sorting by strike interleaves
   two different underlyings and manufactures violations.
2. **Mixed underlyings at different strikes.** Tennis spread events list
   "Player A -1.5" and "Player B -3.5" in one event. Same trap, and the
   duplicate-strike filter does not catch it. Requiring every leg's subtitle to
   share a shape once digits are stripped does.

Without both filters this check reports thousands of violations and every one
is an artifact. **`strike_type` and `floor_strike` do not establish that two
markets are on the same underlying in the same direction.** Detector 3 needs a
verified underlying identity, not an event grouping.

Usage::

    python -m src.research.cross_market_check [--sweep data/sweeps/<dir>]
"""

from __future__ import annotations

import argparse
import glob
import gzip
import json
import re
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from src.venues.kalshi.fees import FeeModel, FeeType, Role, basket_fee

D = Decimal
QUADRATIC = FeeModel(FeeType.QUADRATIC)
MAX_TEST_SIZE = 100


def underlying_shape(subtitle: str) -> str:
    """Subtitle with every number stripped, to identify the underlying."""
    return re.sub(r"[\d,.\$\-+]+", "#", subtitle).strip()


def load_ladders(sweep: Path) -> dict[str, list[tuple]]:
    ladders: dict[str, list[tuple]] = defaultdict(list)
    for page in sorted(glob.glob(str(sweep / "raw" / "markets_page_*.json.gz"))):
        with gzip.open(page, "rt") as fh:
            for m in json.load(fh).get("markets", []):
                if m.get("strike_type") != "greater" or m.get("floor_strike") is None:
                    continue
                bid_yes = D(m.get("yes_bid_dollars") or "0") * 100
                bid_no = D(m.get("no_bid_dollars") or "0") * 100
                if bid_yes <= 0 or bid_no <= 0:
                    continue
                ladders[m["event_ticker"]].append(
                    (
                        float(m["floor_strike"]),
                        bid_yes,
                        D(100) - bid_no,
                        m.get("yes_sub_title", ""),
                        m.get("close_time", ""),
                        D(m.get("yes_bid_size_fp") or "0"),
                        D(m.get("yes_ask_size_fp") or "0"),
                    )
                )
    return ladders


def run(sweep: Path) -> None:
    ladders = load_ladders(sweep)
    now = datetime.now(UTC)

    dropped = 0
    clean = 0
    pairs = 0
    findings = []

    for event, legs in ladders.items():
        strikes = [leg[0] for leg in legs]
        shapes = {underlying_shape(leg[3]) for leg in legs}
        if len(set(strikes)) != len(strikes) or len(shapes) != 1:
            dropped += 1
            continue
        if len(legs) < 2:
            continue
        clean += 1
        legs.sort()
        for low, high in zip(legs, legs[1:], strict=False):
            pairs += 1
            k1, _, ask1, _, close1, _, ask_size1 = low
            k2, bid2, _, _, _, bid_size2, _ = high
            if bid2 <= ask1:
                continue

            gross = bid2 - ask1
            available = min(ask_size1, bid_size2)
            size = max(1, int(min(available, MAX_TEST_SIZE)))
            fee = basket_fee(
                [
                    (QUADRATIC, size, ask1 / 100, Role.TAKER),
                    (QUADRATIC, size, bid2 / 100, Role.TAKER),
                ]
            )
            fee_cents = fee * 100 / size
            closes = datetime.fromisoformat(close1.replace("Z", "+00:00"))
            years = max((closes - now).days, 1) / 365.25
            findings.append(
                {
                    "event": event,
                    "strikes": f"{k1:g}->{k2:g}",
                    "gross_cents": gross,
                    "fee_cents": fee_cents,
                    "net_cents": gross - fee_cents,
                    "available": available,
                    "years": years,
                }
            )

    findings.sort(key=lambda f: -f["net_cents"])
    survivors = [f for f in findings if f["net_cents"] > 0]

    print(f"sweep: {sweep.name}")
    print(f"  events dropped (duplicate strikes or mixed underlyings): {dropped:,}")
    print(f"  clean single-underlying ladders: {clean:,}")
    print(f"  adjacent strike pairs checked:   {pairs:,}")
    print(f"  monotonicity violations bid(K2) > ask(K1): {len(findings)}")
    print(f"  surviving the fee gate at available size:  {len(survivors)}")
    if findings:
        print(f"\n  {'net':>7} {'gross':>7} {'fee':>7} {'size':>8} {'yrs':>6}  event")
        for f in findings:
            print(
                f"  {f['net_cents']:7.2f} {f['gross_cents']:7.1f} {f['fee_cents']:7.2f} "
                f"{f['available']:8.0f} {f['years']:6.2f}  {f['event']} {f['strikes']}"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sweep", type=Path, default=None)
    args = parser.parse_args()
    sweep = args.sweep or sorted(Path("data/sweeps").iterdir())[0]
    run(sweep)


if __name__ == "__main__":
    main()
