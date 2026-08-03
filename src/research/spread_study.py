"""Phase 0.5a — spread study. Generates research/SPREAD_STUDY.md from a sweep.

Usage::

    python -m src.research.spread_study                     # newest sweep
    python -m src.research.spread_study --sweep data/sweeps/<dir> [--compare <dir>]

The governing quantity is the YES bid-ask spread, ``100 - (bid_YES + bid_NO)``.
An N-leg taker-side basket must overcome the fee gate *plus* half a spread on
every leg before a true mispricing becomes visible on the book. The spread term
scales with leg count and dominates the fee term almost everywhere.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import statistics
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from src.venues.kalshi.fees import FeeModel, FeeType, Role, basket_fee

D = Decimal
QUADRATIC = FeeModel(FeeType.QUADRATIC)
REPORT = Path("research/SPREAD_STUDY.md")

BUCKET_ORDER = ["<=8c", "8-25c", "25-75c", "75-92c", ">=92c"]
# Leg count -> the price bucket a uniform basket's legs land in.
BASKET_SHAPES = [(2, D("0.50"), "25-75c"), (3, D("0.33"), "25-75c"),
                 (4, D("0.25"), "25-75c"), (10, D("0.10"), "8-25c")]


def _open_csv(sweep: Path, name: str):
    """Open a sweep CSV, gzipped or not.

    Live sweeps under data/ are plain; the snapshot committed alongside a
    published report is gzipped so the report stays auditable after the
    working data is gone.
    """
    plain = sweep / f"{name}.csv"
    if plain.exists():
        return plain.open()
    packed = sweep / f"{name}.csv.gz"
    if packed.exists():
        return gzip.open(packed, "rt")
    return None


def load(sweep: Path) -> tuple[list[dict], dict[str, dict]]:
    handle = _open_csv(sweep, "markets")
    if handle is None:
        raise FileNotFoundError(f"no markets.csv or markets.csv.gz in {sweep}")
    with handle:
        markets = list(csv.DictReader(handle))

    events: dict[str, dict] = {}
    handle = _open_csv(sweep, "events")
    if handle is not None:
        with handle:
            for row in csv.DictReader(handle):
                events[row["event_ticker"]] = row
    return markets, events


def two_sided(markets: list[dict]) -> list[dict]:
    return [m for m in markets if m["two_sided"] == "True"]


def pct(values: list[float], q: float) -> float:
    """Percentile by nearest rank. Small-sample safe, no interpolation."""
    if not values:
        return float("nan")
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, int(round(q / 100 * (len(ordered) - 1)))))
    return ordered[idx]


def _row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _stats_table(groups: dict[str, list[float]], label: str, min_n: int = 30) -> str:
    lines = [
        _row([label, "n", "p10", "median", "p75", "p90", "share <=2c"]),
        _row(["---"] * 7),
    ]
    for key in sorted(groups, key=lambda k: -len(groups[k])):
        vals = groups[key]
        if len(vals) < min_n:
            continue
        tight = sum(1 for v in vals if v <= 2) / len(vals) * 100
        lines.append(
            _row(
                [
                    key or "(unmapped)",
                    f"{len(vals):,}",
                    f"{pct(vals, 10):.0f}c",
                    f"{statistics.median(vals):.0f}c",
                    f"{pct(vals, 75):.0f}c",
                    f"{pct(vals, 90):.0f}c",
                    f"{tight:.1f}%",
                ]
            )
        )
    return "\n".join(lines)


def histogram(values: list[float]) -> str:
    edges = [(1, 1), (2, 2), (3, 4), (5, 8), (9, 16), (17, 32), (33, 100)]
    total = len(values)
    lines = [_row(["Spread", "Markets", "Share", ""]), _row(["---"] * 4)]
    for lo, hi in edges:
        n = sum(1 for v in values if lo <= v <= hi)
        share = n / total * 100 if total else 0
        bar = "#" * int(round(share / 2))
        name = f"{lo}c" if lo == hi else f"{lo}-{hi}c"
        lines.append(_row([name, f"{n:,}", f"{share:.1f}%", bar]))
    return "\n".join(lines)


def required_mispricing(bucket_spreads: dict[str, list[float]]) -> str:
    """fee_gate + sum(s_i / 2), the visibility threshold for a uniform basket."""
    lines = [
        _row(
            [
                "Legs",
                "Leg price",
                "Bucket",
                "Fee term",
                "Spread term (median)",
                "**Threshold (median)**",
                "Spread term (p10)",
                "**Threshold (p10)**",
            ]
        ),
        _row(["---"] * 8),
    ]
    for n, price, bucket in BASKET_SHAPES:
        spreads = bucket_spreads.get(bucket, [])
        if not spreads:
            continue
        fee = basket_fee([(QUADRATIC, 100, price, Role.TAKER)] * n) * 100 / 100
        med = D(str(statistics.median(spreads)))
        p10 = D(str(pct(spreads, 10)))
        s_med = n * med / 2
        s_p10 = n * p10 / 2
        lines.append(
            _row(
                [
                    str(n),
                    f"{price * 100:.0f}c",
                    bucket,
                    f"{fee:.2f}c",
                    f"{s_med:.1f}c",
                    f"**{fee + s_med:.1f}c**",
                    f"{s_p10:.1f}c",
                    f"**{fee + s_p10:.1f}c**",
                ]
            )
        )
    return "\n".join(lines)


def basket_cost(markets: list[dict], events: dict[str, dict]) -> tuple[str, dict]:
    """What it actually costs, right now, to buy every leg of an event.

    Restricted to events the venue flags mutually exclusive and where every leg
    is two-sided (an unbuyable leg makes the basket unbuyable). Exhaustiveness
    is NOT established for any of these, so none of them is an opportunity --
    this measures the cost of crossing, not edge.
    """
    by_event: dict[str, list[dict]] = defaultdict(list)
    for m in markets:
        by_event[m["event_ticker"]].append(m)

    excess: list[float] = []
    by_legs: dict[int, list[float]] = defaultdict(list)
    best: list[tuple[float, str, int]] = []

    for ticker, legs in by_event.items():
        meta = events.get(ticker)
        if not meta or meta["mutually_exclusive"] != "True" or len(legs) < 2:
            continue
        if any(leg["two_sided"] != "True" for leg in legs):
            continue
        ask_sum = sum(float(leg["ask_yes_cents"]) for leg in legs)
        over = ask_sum - 100
        excess.append(over)
        by_legs[len(legs)].append(over)
        best.append((over, ticker, len(legs)))

    lines = [
        _row(["Legs", "Events", "min", "p10", "median", "p90"]),
        _row(["---"] * 6),
    ]
    for n in sorted(by_legs):
        vals = by_legs[n]
        if len(vals) < 10:
            continue
        lines.append(
            _row(
                [
                    str(n),
                    f"{len(vals):,}",
                    f"{min(vals):+.0f}c",
                    f"{pct(vals, 10):+.0f}c",
                    f"{statistics.median(vals):+.0f}c",
                    f"{pct(vals, 90):+.0f}c",
                ]
            )
        )
    best.sort()
    # For a basket that pays $1 only if one of the LISTED legs wins, the
    # break-even residual probability is r = 1 - cost/100. So the discount to
    # par is not edge -- it is the market's implied probability that none of
    # the listed outcomes occurs.
    sub_par = [(100 + over, ticker, n) for over, ticker, n in best if over < 0]
    sub_par_lines = [
        _row(["sum(ask)", "Legs", "Implied residual r", "Event", "Title"]),
        _row(["---"] * 5),
    ]
    for cost, ticker, n in sub_par[:8]:
        meta = events.get(ticker, {})
        sub_par_lines.append(
            _row(
                [
                    f"{cost:.0f}c",
                    str(n),
                    f"{1 - cost / 100:.0%}",
                    f"`{ticker}`",
                    meta.get("title", "")[:44],
                ]
            )
        )

    summary = {
        "n_events": len(excess),
        "min": min(excess) if excess else float("nan"),
        "median": statistics.median(excess) if excess else float("nan"),
        "n_below_100": len(sub_par),
        "sub_par_table": "\n".join(sub_par_lines),
    }
    return "\n".join(lines), summary


def build(sweep: Path, compare: Path | None) -> str:
    markets, events = load(sweep)
    live = two_sided(markets)
    spreads = [float(m["spread_cents"]) for m in live]

    by_category: dict[str, list[float]] = defaultdict(list)
    by_fee: dict[str, list[float]] = defaultdict(list)
    by_bucket: dict[str, list[float]] = defaultdict(list)
    for m in live:
        s = float(m["spread_cents"])
        by_category[m["category"]].append(s)
        by_bucket[m["price_bucket"]].append(s)
        if m["fee_multiplier"] == "0":
            by_fee["fee-free (multiplier 0)"].append(s)
        elif m["fee_type"] == "quadratic_with_maker_fees":
            by_fee["maker-fee series"].append(s)
        else:
            by_fee["standard quadratic"].append(s)

    bucket_table = _row(["Price bucket", "n", "p10", "median", "p75", "p90", "share <=2c"]) + "\n"
    bucket_table += _row(["---"] * 7) + "\n"
    for b in BUCKET_ORDER:
        vals = by_bucket.get(b, [])
        if not vals:
            continue
        tight = sum(1 for v in vals if v <= 2) / len(vals) * 100
        bucket_table += (
            _row(
                [
                    b,
                    f"{len(vals):,}",
                    f"{pct(vals, 10):.0f}c",
                    f"{statistics.median(vals):.0f}c",
                    f"{pct(vals, 75):.0f}c",
                    f"{pct(vals, 90):.0f}c",
                    f"{tight:.1f}%",
                ]
            )
            + "\n"
        )

    basket_table, basket_summary = basket_cost(markets, events)
    tight_share = sum(1 for s in spreads if s <= 2) / len(spreads) * 100
    one_tick_share = sum(1 for s in spreads if s == 1) / len(spreads) * 100

    sizes = [float(m["bid_size"] or 0) for m in live]
    ask_sizes = [float(m["ask_size"] or 0) for m in live]

    stability = ""
    if compare is not None:
        m2, _ = load(compare)
        live2 = two_sided(m2)
        s2 = [float(m["spread_cents"]) for m in live2]
        common = {m["ticker"]: float(m["spread_cents"]) for m in live}
        moved = [
            abs(common[m["ticker"]] - float(m["spread_cents"]))
            for m in live2
            if m["ticker"] in common
        ]
        def captured(path: Path) -> datetime:
            return datetime.fromisoformat(
                json.loads((path / "manifest.json").read_text())["captured_at"]
            )

        gap_min = (captured(compare) - captured(sweep)).total_seconds() / 60
        stability = f"""
## Stability across sweeps

Second sweep `{compare.name}`, taken **{gap_min:.0f} minutes** after the first:
{len(live2):,} two-sided markets, median spread {statistics.median(s2):.0f}c against
{statistics.median(spreads):.0f}c at t0. Of {len(moved):,} markets present in both, median
absolute change in spread was {statistics.median(moved):.0f}c and
{sum(1 for x in moved if x == 0) / len(moved) * 100:.0f}% were unchanged.

**This is a short-interval check and is weak evidence.** At this gap most books
have simply not been requoted, so a high unchanged rate is close to
uninformative about intraday stability. The 6-hour comparison the experiment
calls for is outstanding; it changes nothing about the required-mispricing
tables, which depend on the level of the spread rather than its persistence.
"""

    return f"""# Phase 0.5a — Spread Study

Source sweep: `{sweep.name}`. Read-only, public endpoints, no credentials.

The derived data backing every number here is committed under
`research/snapshots/`, so this report stays auditable after the working data is
gone. Reproduce it exactly with::

    python -m src.research.spread_study \\
        --sweep research/snapshots/{sweep.name} \\
        --compare research/snapshots/<second sweep>

Take a fresh sweep with `python -m src.research.spread_sweep --label <tag>`.

**`100 - (bid_YES + bid_NO)` is the YES bid-ask spread.** It is the same
expression the deleted complementary detector was testing, and it governs
whether any taker-side detector can ever fire. Each leg crossed costs about
half a spread, so an N-leg basket needs true mispricing of at least
`fee_gate + sum(s_i / 2)` before it is visible on the book.

## Universe

| | Count |
| --- | --- |
| Open markets (multivariate parlays excluded) | {len(markets):,} |
| Two-sided (both YES and NO bids resting) | {len(live):,} ({len(live) / len(markets) * 100:.0f}%) |
| Open events | {len(events):,} |

Multivariate parlay markets are excluded at the API via `mve_filter=exclude`.
A first pass without that filter found **26,852 of 27,000 sampled open markets
were `KXMVE*` combination shells**, essentially none with a two-sided book: they
are generated one per requested combination and quoted through the RFQ system
rather than resting on a book. Any market count quoted for Kalshi that includes
them is measuring auto-generated inventory, not tradeable venues.

## Spread distribution, whole exchange

{histogram(spreads)}

Median {statistics.median(spreads):.0f}c, p10 {pct(spreads, 10):.0f}c,
p25 {pct(spreads, 25):.0f}c, p75 {pct(spreads, 75):.0f}c, p90 {pct(spreads, 90):.0f}c.
{tight_share:.1f}% of two-sided markets are quoted 2c wide or tighter;
{one_tick_share:.1f}% are at the 1c minimum.

## By price bucket

{bucket_table}
## By fee treatment

{_stats_table(by_fee, "Segment")}

## By category

{_stats_table(by_category, "Category")}

## Required mispricing — the real gate

Uniform N-leg basket, legs priced 1/N, 100 contracts a leg, all crossing.
Fee term is the summed per-leg ceiling from `src/venues/kalshi/fees.py`; spread
term is `N x s/2` at that bucket's spread.

{required_mispricing(by_bucket)}

The spread term exceeds the fee term at every leg count, by a factor that grows
with N. **The fee schedule was the wrong thing to worry about.**

## What a basket actually costs right now

Sum of `ask(YES)` across every leg of events the venue flags mutually
exclusive, where all legs are two-sided. Exhaustiveness is not established for
any of these, so **none is an opportunity** — this measures the cost of
crossing, not edge. A negative number would be a basket buyable below $1.

{basket_table}

Across {basket_summary["n_events"]:,} such events the median basket costs
**{basket_summary["median"]:+.0f}c** relative to par, the cheapest observed was
**{basket_summary["min"]:+.0f}c**, and **{basket_summary["n_below_100"]}** were priced below 100c.

### The sub-par baskets are not mispricings

A structural detector without an exhaustiveness gate would flag all
{basket_summary["n_below_100"]} of those as arbitrage. Every one inspected is a
**listed-subset market**: Kalshi lists some candidate outcomes, not all of them,
and the unlisted residual carries most of the probability.

{basket_summary["sub_par_table"]}

For a basket paying $1 only if one of the *listed* legs wins, break-even
residual probability is `r = 1 - cost/100`. The discount to par is therefore
not edge — **it is the market's implied probability that none of the listed
outcomes occurs**, and the two are arithmetically the same number.

`KXLAPRIMARY-01R26` ("LA-01 Republican nominee?") lists two candidates and
trades at 10c. That is not a 90c arbitrage; it is the market saying there is a
90% chance the nominee is somebody Kalshi has not listed. Buying both legs is a
short of "someone else wins" at fair value.

This is the strongest available argument for the mandatory exhaustiveness
check, and it is stronger than the safety argument: without a verified
partition, `sum(ask) < 100` carries **no** information about mispricing. The
detector would not be taking on hidden risk in exchange for real edge — it
would be measuring the residual and reporting it as edge.

## Size at the touch

Median resting size {statistics.median(sizes):,.0f} contracts on the bid and
{statistics.median(ask_sizes):,.0f} on the offer; p10 {pct(sizes, 10):,.0f} and
{pct(ask_sizes, 10):,.0f}. Size is not the binding constraint — the spread is.
{stability}
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sweep", type=Path, default=None)
    parser.add_argument("--compare", type=Path, default=None)
    args = parser.parse_args()

    sweep = args.sweep or sorted(Path("data/sweeps").iterdir())[-1]
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(build(sweep, args.compare))
    print(f"wrote {REPORT} from {sweep.name}")


if __name__ == "__main__":
    main()
