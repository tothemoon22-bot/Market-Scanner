"""Partition drift between two sweeps — the memo's "First dynamics" numbers.

Findings 1-5 are a still photograph. This produces the first motion: how far the
fee-free verified partitions move between two observations, and whether a
below-par crossing is a repricing or the basket's own spread collapsing.

The distinction matters because it decides what a below-par alert means. If Sum(ask)
crosses par while Sum(bid) rises to meet it, nothing was revalued -- the basket's
width shrank and the ask fell along with it. A partition's width cannot fall
below ``n_legs x tick``, so ``Sum(ask)`` wanders +/- half that around the midpoint on
tick granularity alone. That half-width is the noise floor a below-par excursion
has to clear before it means anything.

Usage::

    python -m src.research.drift --a monitor/snapshots/<dir> --b monitor/snapshots/<dir>
    python -m src.research.drift --a monitor/snapshots/<dir> --b-raw data/live/<dir>

``--b-raw`` reads a sweep persisted as raw API pages (what
``src.research.reconcile --sweep-now`` writes) and derives series fee metadata
from sweep ``a``, so no extra API calls are made. Emits
``research/drift.json``.
"""

from __future__ import annotations

import argparse
import json
import statistics
from dataclasses import asdict
from decimal import Decimal as D
from pathlib import Path
from typing import Any

from monitor.collect import read_snapshot, to_row
from monitor.metrics import _events, partition_report

OUT = Path("research/drift.json")


def series_meta(rows: list[dict]) -> dict[str, dict]:
    return {
        r["series_ticker"]: {
            "category": r["category"],
            "fee_type": r["fee_type"],
            "fee_multiplier": r["fee_multiplier"],
        }
        for r in rows
    }


def rows_from_raw(sweep: Path, meta: dict[str, dict]) -> list[dict]:
    from src.research.reconcile import load_raw

    return [asdict(to_row(m, meta)) for m in load_raw(sweep).values()]


def fee_free_partitions(rows: list[dict]) -> dict[str, dict]:
    return {p["event"]: p for p in partition_report(rows) if p["fee_multiplier"] == "0"}


def basket_sums(rows: list[dict], event: str) -> tuple[D, D] | None:
    """(Sum of asks, Sum of bids) for one event, in cents."""
    legs = _events(rows).get(event, [])
    if not legs:
        return None
    ask = sum(D(r["ask_yes_cents"]) for r in legs)
    bid = sum(D(r["bid_yes_cents"]) for r in legs)
    return ask.quantize(D("0.01")), bid.quantize(D("0.01"))


def compare(rows_a: list[dict], rows_b: list[dict]) -> dict[str, Any]:
    part_a = fee_free_partitions(rows_a)
    part_b = fee_free_partitions(rows_b)

    detail: list[dict[str, Any]] = []
    for event in sorted(set(part_a) | set(part_b)):
        sums_a, sums_b = basket_sums(rows_a, event), basket_sums(rows_b, event)
        if sums_a is None or sums_b is None:
            continue
        ask_a, bid_a = sums_a
        ask_b, bid_b = sums_b
        pa, pb = part_a.get(event), part_b.get(event)
        detail.append(
            {
                "event": event,
                "legs": (pb or pa)["legs"],
                "ask_a": str(ask_a),
                "ask_b": str(ask_b),
                "d_ask": str(ask_b - ask_a),
                "bid_a": str(bid_a),
                "bid_b": str(bid_b),
                "d_bid": str(bid_b - bid_a),
                "width_a": str(ask_a - bid_a),
                "width_b": str(ask_b - bid_b),
                "below_par_a": ask_a < 100,
                "below_par_b": ask_b < 100,
                "capacity_a": None if pa is None else pa["capacity_contracts"],
                "capacity_b": None if pb is None else pb["capacity_contracts"],
                "tradeable_a": None if pa is None else pa["tradeable"],
                "tradeable_b": None if pb is None else pb["tradeable"],
            }
        )

    moves = [abs(D(d["d_ask"])) for d in detail]
    signed_ask = [D(d["d_ask"]) for d in detail]
    signed_bid = [D(d["d_bid"]) for d in detail]
    # A basket cannot quote tighter than one tick per leg, so Sum(ask) sits at least
    # half that above the midpoint. Reported per basket, not assumed uniform.
    floors = {
        d["event"]: str(D(d["legs"]) * D("0.5"))
        for d in detail
        if not d["event"].startswith(("KXBTCY", "KXETHY"))
    }

    return {
        "n_partitions": len(detail),
        "median_abs_move_cents": str(statistics.median(sorted(moves))) if moves else None,
        "max_abs_move_cents": str(max(moves)) if moves else None,
        "n_unchanged": sum(1 for m in moves if m == 0),
        "median_signed_d_ask": str(sorted(signed_ask)[len(signed_ask) // 2]),
        "median_signed_d_bid": str(sorted(signed_bid)[len(signed_bid) // 2]),
        "n_below_par_a": sum(1 for d in detail if d["below_par_a"]),
        "n_below_par_b": sum(1 for d in detail if d["below_par_b"]),
        "entered_below_par": [
            d["event"] for d in detail if d["below_par_b"] and not d["below_par_a"]
        ],
        "left_below_par": [d["event"] for d in detail if d["below_par_a"] and not d["below_par_b"]],
        "n_below_par_tradeable_a": sum(1 for d in detail if d["below_par_a"] and d["tradeable_a"]),
        "n_below_par_tradeable_b": sum(1 for d in detail if d["below_par_b"] and d["tradeable_b"]),
        "half_width_noise_floor_cents": floors,
        "detail": detail,
    }


def render(report: dict[str, Any]) -> str:
    lines = [
        f"{'event':<22} {'ask a':>8} {'ask b':>8} {'d_ask':>7} "
        f"{'d_bid':>7} {'width a':>8} {'width b':>8}"
    ]
    for d in report["detail"]:
        lines.append(
            f"{d['event']:<22} {d['ask_a']:>8} {d['ask_b']:>8} {D(d['d_ask']):>+7} "
            f"{D(d['d_bid']):>+7} {d['width_a']:>8} {d['width_b']:>8}"
        )
    lines += [
        "",
        f"n                     {report['n_partitions']}",
        f"median |move|         {report['median_abs_move_cents']}c",
        f"max |move|            {report['max_abs_move_cents']}c",
        f"unchanged             {report['n_unchanged']}",
        f"median signed d_ask   {report['median_signed_d_ask']}c",
        f"median signed d_bid   {report['median_signed_d_bid']}c",
        f"below par             {report['n_below_par_a']} -> {report['n_below_par_b']}",
        f"  entered             {report['entered_below_par']}",
        f"  left                {report['left_below_par']}",
        f"below par + tradeable {report['n_below_par_tradeable_a']} -> "
        f"{report['n_below_par_tradeable_b']}",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--a", type=Path, required=True, help="earlier snapshot directory")
    parser.add_argument("--b", type=Path, help="later snapshot directory")
    parser.add_argument("--b-raw", type=Path, help="later sweep as raw API pages")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()

    rows_a = read_snapshot(args.a)
    if args.b:
        rows_b = read_snapshot(args.b)
        label_b = args.b.name
    elif args.b_raw:
        rows_b = rows_from_raw(args.b_raw, series_meta(rows_a))
        label_b = args.b_raw.name
    else:
        parser.error("pass --b <snapshot> or --b-raw <sweep>")

    report = compare(rows_a, rows_b)
    report["a"] = args.a.name
    report["b"] = label_b
    print(render(report))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
