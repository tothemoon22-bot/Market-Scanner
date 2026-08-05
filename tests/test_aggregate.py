"""The correctness gate on the streaming rewrite.

``metrics.compute`` is the reference. The streaming aggregate must reproduce it
**byte-identically** from the same snapshot data -- if any figure moves, the
rewrite is wrong, and the baseline is not the thing to adjust.

The standard of correctness is sourced from outside the code under test: the
reference implementation, and the committed snapshots it was verified against.
A streaming rewrite checked only against its own output would be a check sharing
a failure mode with its subject.
"""

from __future__ import annotations

import json
from decimal import Decimal as D
from pathlib import Path

import pytest

from monitor import collect, metrics
from monitor.aggregate import (
    MAX_DISTINCT_SPREADS,
    SpreadDistribution,
    SweepAggregate,
)

SNAPSHOTS = [
    Path("monitor/snapshots/20260803T070632Z_t0"),
    Path("monitor/snapshots/20260803T071019Z_t1"),
]
BASELINE = Path("monitor/baseline.json")


@pytest.fixture(scope="module")
def snapshot_rows():
    return {p.name: collect.read_snapshot(p) for p in SNAPSHOTS}


def _canonical(payload: dict) -> str:
    return json.dumps(payload, indent=2, sort_keys=True)


@pytest.mark.parametrize("name", [p.name for p in SNAPSHOTS])
def test_streaming_reproduces_the_reference_byte_for_byte(snapshot_rows, name):
    rows = snapshot_rows[name]
    reference = metrics.compute(rows)
    streamed = SweepAggregate().fold(rows).result()

    # Annualized return moves with the clock, so it is compared structurally and
    # excluded from the byte comparison -- the same carve-out the baseline gate
    # already makes, and the only one.
    for payload in (reference, streamed):
        for p in payload["verified_partitions"]["fee_free_detail"]:
            p["annualized_pct"] = "<clock>"
        for p in payload["deci_cent_fee_free_tripwire"]["partitions"]:
            p["annualized_pct"] = "<clock>"

    assert _canonical(streamed) == _canonical(reference)


def test_streaming_reproduces_the_committed_baseline(snapshot_rows):
    """The gate as the work order states it: the baseline must not move."""
    baseline = json.loads(BASELINE.read_text())
    streamed = SweepAggregate().fold(snapshot_rows[SNAPSHOTS[0].name]).result()

    for payload in (baseline, streamed):
        for p in payload["verified_partitions"]["fee_free_detail"]:
            p["annualized_pct"] = "<clock>"
        for p in payload["deci_cent_fee_free_tripwire"]["partitions"]:
            p["annualized_pct"] = "<clock>"

    assert _canonical(streamed) == _canonical(baseline)


def test_folding_order_does_not_change_the_result(snapshot_rows):
    """Pages arrive in whatever order the cursor yields them."""
    rows = snapshot_rows[SNAPSHOTS[0].name]
    forward = SweepAggregate().fold(rows).result()
    backward = SweepAggregate().fold(list(reversed(rows))).result()
    for payload in (forward, backward):
        for p in payload["verified_partitions"]["fee_free_detail"]:
            p["annualized_pct"] = "<clock>"
        for p in payload["deci_cent_fee_free_tripwire"]["partitions"]:
            p["annualized_pct"] = "<clock>"
    assert _canonical(forward) == _canonical(backward)


def test_legs_of_one_event_may_arrive_on_different_pages(snapshot_rows):
    """Cursor pagination does not group by event, and nothing may assume it."""
    rows = snapshot_rows[SNAPSHOTS[0].name]
    interleaved = [r for i in (0, 1) for r in rows[i::2]]
    assert len(interleaved) == len(rows)
    a = SweepAggregate().fold(rows).result()
    b = SweepAggregate().fold(interleaved).result()
    for payload in (a, b):
        for p in payload["verified_partitions"]["fee_free_detail"]:
            p["annualized_pct"] = "<clock>"
        for p in payload["deci_cent_fee_free_tripwire"]["partitions"]:
            p["annualized_pct"] = "<clock>"
    assert _canonical(a) == _canonical(b)


@pytest.mark.parametrize("name", [p.name for p in SNAPSHOTS])
def test_streaming_funnel_matches_the_reference_funnel(snapshot_rows, name):
    from scanner import funnel

    rows = snapshot_rows[name]
    reference = funnel.as_dict(funnel.build(rows))
    streamed = funnel.as_dict(funnel.build_from(SweepAggregate().fold(rows)))
    assert streamed == reference


def test_a_funnel_candidate_need_not_be_a_range_partition(snapshot_rows):
    """The prefilter drops legs the funnel still has to *count*.

    Candidates are events with two-plus two-sided non-RFQ legs, whatever their
    strike type; only the verified stage onward needs leg detail. If these two
    numbers were equal the test would prove nothing.
    """
    from scanner import funnel

    agg = SweepAggregate().fold(snapshot_rows[SNAPSHOTS[0].name])
    stages = {s.key: s.count for s in funnel.build_from(agg)}
    assert stages["candidates"] > stages["verified"] > 0
    assert stages["candidates"] > len(agg.candidate_legs())


# ------------------------------------------------------- spread distribution --


def _reference_stats(values: list[D]) -> dict:
    return metrics._spread_stats(values)


def _streamed_stats(values: list[D]) -> dict:
    dist = SpreadDistribution()
    for v in values:
        dist.add(v)
    return dist.stats()


@pytest.mark.parametrize(
    "values",
    [
        pytest.param([D("6")], id="single"),
        pytest.param([D("5"), D("6")], id="even-differing-midpoints"),
        pytest.param([D("6"), D("6")], id="even-equal-midpoints"),
        pytest.param([D("1"), D("2"), D("3")], id="odd"),
        pytest.param([D("0.1")] * 7 + [D("99.9")] * 3, id="lopsided"),
        pytest.param([D("0"), D("0.9"), D("1"), D("1.1")], id="sub-1c-boundary"),
        pytest.param([D("0.5000")] * 3 + [D("0.6000")] * 2, id="four-decimal-form"),
        pytest.param([D(i) / 10 for i in range(1001)], id="whole-grid"),
    ],
)
def test_percentiles_and_median_match_the_reference_exactly(values):
    assert _streamed_stats(values) == _reference_stats(values)


def test_the_one_precondition_the_count_map_rests_on_holds_in_the_data(snapshot_rows):
    """Equal-valued spreads must share one string form, or `str()` is ambiguous.

    ``Decimal("0.5") == Decimal("0.5000")``, so a value->count map merges them
    and renders whichever form it stored; the reference's ``sorted()`` renders
    whichever came first in the row order. **Neither has a canonical answer** --
    the reference is order-dependent here too, so this is a precondition on the
    input rather than a defect in either implementation.

    ``to_row`` formats every price with ``f"{x:.4f}"``, so the form is uniform by
    construction. This asserts that rather than assuming it, because it is the
    assumption the byte-identity gate quietly rests on.
    """
    forms: dict[D, set[str]] = {}
    for row in snapshot_rows[SNAPSHOTS[0].name]:
        raw = row["spread_cents"]
        forms.setdefault(D(raw), set()).add(raw)
    ambiguous = {value: shapes for value, shapes in forms.items() if len(shapes) > 1}
    assert not ambiguous, (
        f"the same spread appears in more than one string form: {ambiguous}. "
        "Percentile rendering is then order-dependent in both implementations."
    )


def test_even_length_median_can_return_a_value_not_in_the_data(values=None):
    """The case a naive "pick the middle key" implementation gets wrong."""
    dist = SpreadDistribution()
    dist.add(D("5"))
    dist.add(D("6"))
    assert dist.median() == D("5.5")
    assert D("5.5") not in dist.counts


def test_distribution_is_exact_not_binned():
    """No rounding to a grid: an off-grid value survives as itself."""
    dist = SpreadDistribution()
    dist.add(D("0.05"))
    dist.add(D("0.05"))
    dist.add(D("0.07"))
    assert dist.median() == D("0.05")
    assert dist.percentile(90) == D("0.07")
    assert sorted(dist.counts) == [D("0.05"), D("0.07")]


def test_unbounded_growth_raises_rather_than_truncating():
    """A silent cap would make the aggregate wrong and the process look healthy."""
    dist = SpreadDistribution()
    with pytest.raises(ValueError, match="price grid assumption"):
        for i in range(MAX_DISTINCT_SPREADS + 2):
            dist.add(D(i) / 1000000)


def test_real_snapshot_spreads_stay_far_inside_the_bound(snapshot_rows):
    rows = snapshot_rows[SNAPSHOTS[0].name]
    agg = SweepAggregate().fold(rows)
    assert len(agg.spread_all.counts) < MAX_DISTINCT_SPREADS / 10
    assert all(v * 10 == (v * 10).to_integral_value() for v in agg.spread_all.counts), (
        "spreads left the 0.1c grid; the bound's justification no longer holds"
    )


# --------------------------------------------------------------- retention --


def test_only_partition_candidates_are_retained(snapshot_rows):
    """The one structure that scales with market count, and by how much."""
    rows = snapshot_rows[SNAPSHOTS[0].name]
    agg = SweepAggregate().fold(rows)
    retained = sum(len(s.legs) for s in agg.events.values() if s.legs is not None)
    assert retained < len(rows) / 3, (
        f"retained {retained} legs of {len(rows)} markets; the strike-type "
        "prefilter is not doing its job"
    )
    # And no market objects: legs are tuples of short strings.
    for state in agg.events.values():
        if state.legs:
            assert isinstance(state.legs[0], tuple)


def test_an_event_is_dropped_the_moment_a_non_range_leg_arrives():
    agg = SweepAggregate()
    base = {
        "event_ticker": "KXTEST-1", "ticker": "KXTEST-1-A", "series_ticker": "KXTEST",
        "category": "", "fee_type": "quadratic", "fee_multiplier": "1",
        "tick_structure": "linear_cent", "strike_type": "less", "floor_strike": "",
        "cap_strike": "1", "underlying": "x", "bid_yes_cents": "0", "bid_no_cents": "0",
        "ask_yes_cents": "50", "spread_cents": "1", "mid_cents": "50",
        "price_bucket": "25-75c", "bid_size": "1", "ask_size": "1",
        "close_time": "2027-01-01T00:00:00Z", "two_sided": "True",
    }
    agg.add(base)
    assert agg.events["KXTEST-1"].legs is not None
    agg.add({**base, "ticker": "KXTEST-1-B", "strike_type": "structured"})
    assert agg.events["KXTEST-1"].legs is None, "a non-range leg disqualifies the event"
    # And the disqualification is permanent: a later range leg does not revive it.
    agg.add({**base, "ticker": "KXTEST-1-C", "strike_type": "greater"})
    assert agg.events["KXTEST-1"].legs is None
    assert agg.events["KXTEST-1"].n_legs == 3


def test_an_rfq_shell_disqualifies_its_event():
    agg = SweepAggregate()
    agg.add({
        "event_ticker": "KXMVE-1", "ticker": "KXMVE-1-A", "series_ticker": "KXMVE",
        "category": "", "fee_type": "quadratic", "fee_multiplier": "1",
        "tick_structure": "linear_cent", "strike_type": "less", "floor_strike": "",
        "cap_strike": "1", "underlying": "x", "bid_yes_cents": "0", "bid_no_cents": "0",
        "ask_yes_cents": "50", "spread_cents": "1", "mid_cents": "50",
        "price_bucket": "25-75c", "bid_size": "1", "ask_size": "1",
        "close_time": "2027-01-01T00:00:00Z", "two_sided": "False",
    })
    assert agg.events["KXMVE-1"].legs is None
    assert agg.rfq_shells == 1
