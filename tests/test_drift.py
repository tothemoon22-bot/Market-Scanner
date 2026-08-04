"""Tests for the partition-drift generator behind the memo's "First dynamics".

The section makes three load-bearing claims: that Sum(ask) crossed par while Sum(bid)
rose to meet it, that a basket's width cannot go below one tick per leg, and
that the below-par excursions observed are inside that floor. Each is checked
here against the committed snapshots rather than against prose.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal as D
from pathlib import Path

import pytest

from monitor.collect import read_snapshot
from src.research.drift import basket_sums, compare, fee_free_partitions, render

T0 = Path("monitor/snapshots/20260803T070632Z_t0")
T1 = Path("monitor/snapshots/20260803T071019Z_t1")
DRIFT_JSON = Path("research/drift.json")
MEMO = Path("docs/NEGATIVE_RESULT.md")


@pytest.fixture(scope="module")
def rows_t0():
    return read_snapshot(T0)


@pytest.fixture(scope="module")
def rows_t1():
    return read_snapshot(T1)


def test_four_minutes_apart_is_almost_but_not_entirely_static(rows_t0, rows_t1):
    """The memo cites 12 of 13 unchanged with one 3c move. Both halves matter."""
    report = compare(rows_t0, rows_t1)
    assert report["n_partitions"] == 13
    assert report["n_unchanged"] == 12
    assert D(report["max_abs_move_cents"]) == D(3)
    movers = [d for d in report["detail"] if D(d["d_ask"]) != 0]
    assert [d["event"] for d in movers] == ["KXGDPYEAR-30"]


def test_a_basket_never_quotes_tighter_than_one_tick_per_leg(rows_t0, rows_t1):
    """The noise floor the memo rests on. If this fails the section is wrong."""
    for rows in (rows_t0, rows_t1):
        for event, partition in fee_free_partitions(rows).items():
            sums = basket_sums(rows, event)
            assert sums is not None
            ask, bid = sums
            width = ask - bid
            assert width > 0, f"{event} has a crossed basket book"
            if partition["tick_structure"] == "linear_cent":
                assert width >= D(partition["legs"]), (
                    f"{event} quotes {width}c wide on {partition['legs']} cent-tick legs, "
                    "which is tighter than the tick allows"
                )


def test_below_par_excursions_sit_inside_the_basket_own_spread(rows_t0, rows_t1):
    """The memo's general claim: distance below par < the basket's own bid-ask.

    Stated on the full width rather than the half-width, because one observation
    (KXGDPYEAR-33 at t0) exceeds its half-width -- see the next test. An
    excursion wider than the whole spread would overturn the section.
    """
    for rows in (rows_t0, rows_t1):
        for event, partition in fee_free_partitions(rows).items():
            cost = D(partition["cost_cents"])
            if cost >= 100:
                continue
            sums = basket_sums(rows, event)
            assert sums is not None
            ask, bid = sums
            assert D(100) - cost < ask - bid, (
                f"{event} is {D(100) - cost}c below par on a {ask - bid}c-wide basket -- "
                "an excursion exceeding the basket's own spread would overturn "
                "the First dynamics section"
            )


def test_the_one_excursion_beyond_its_half_width_is_the_phantom_liquidity_case(rows_t0):
    """Named in the memo rather than smoothed over. If another appears, say so."""
    beyond = []
    for event, partition in fee_free_partitions(rows_t0).items():
        cost = D(partition["cost_cents"])
        if cost >= 100:
            continue
        ask, bid = basket_sums(rows_t0, event)
        if D(100) - cost > (ask - bid) / 2:
            beyond.append((event, partition["capacity_contracts"]))

    assert [e for e, _ in beyond] == ["KXGDPYEAR-33"], (
        f"a below-par excursion beyond half the basket's width appeared: {beyond}. "
        "The memo names exactly one and attributes it to phantom liquidity."
    )
    # The size gate, not the noise floor, is what disqualifies it.
    assert D(beyond[0][1]) < 1
    assert "KXGDPYEAR-33" in MEMO.read_text().split("## First dynamics")[1].split("\n## ")[0]


def test_comparison_is_decimal_throughout(rows_t0, rows_t1):
    """Floats produced three phantom violations once. Nothing here may reintroduce them."""
    report = compare(rows_t0, rows_t1)
    for d in report["detail"]:
        for key in ("ask_a", "ask_b", "d_ask", "bid_a", "bid_b", "d_bid", "width_a", "width_b"):
            assert isinstance(d[key], str)
            D(d[key])  # raises rather than silently coercing


def test_render_emits_no_number_the_comparison_did_not_produce(rows_t0, rows_t1):
    report = compare(rows_t0, rows_t1)
    text = render(report)
    for d in report["detail"]:
        assert d["event"] in text
        assert d["ask_a"] in text


def test_committed_drift_json_matches_the_memo(rows_t0):
    """The memo's headline figures are read from the artifact, not typed in."""
    report = json.loads(DRIFT_JSON.read_text())
    memo = MEMO.read_text()
    section = memo.split("## First dynamics")[1].split("\n## ")[0]

    assert report["n_partitions"] == 13
    assert f"| Median absolute move | **{D(report['median_abs_move_cents']):.1f}¢** |" in section

    biggest = max(report["detail"], key=lambda d: abs(D(d["d_ask"])))
    assert (
        f"| Largest move | {D(report['max_abs_move_cents']):.1f}¢ "
        f"(`{biggest['event']}`, {D(biggest['ask_a']):.0f}¢ → {D(biggest['ask_b']):.0f}¢) |"
    ) in section

    assert f"| Unchanged | {report['n_unchanged']} of 13 |" in section
    assert f"| Below par | {report['n_below_par_a']} → **{report['n_below_par_b']}** |" in section
    assert (
        f"| Below par *and* tradeable at size ≥ 1 | {report['n_below_par_tradeable_a']} → "
        f"**{report['n_below_par_tradeable_b']}** |"
    ) in section
    assert (
        f"median of {D(report['median_signed_d_bid']):.0f}¢" in section
        and f"median of {abs(D(report['median_signed_d_ask'])):.0f}¢" in section
    )
    assert report["entered_below_par"] == ["KXGDPYEAR-28"]
    assert report["left_below_par"] == []
    for event in report["entered_below_par"]:
        assert event in section


def test_memo_never_calls_three_sweeps_a_time_series():
    """Overclaiming here is the failure the section exists to prevent."""
    section = MEMO.read_text().split("## First dynamics")[1].split("\n## ")[0]
    assert "not weekly data" in section or "not a time series" in section
    # No "per week" / "week to week" framing on data spanning 33 hours.
    assert not re.search(r"week[- ]to[- ]week", section, re.I)
