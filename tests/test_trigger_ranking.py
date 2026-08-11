"""The hero ranks measured triggers only, and drift always has a comparison point.

Two defects, one symptom. The tick-structure trigger reported
``proximity_pct="100.0"`` with ``value=None`` whenever the set of tick
structures changed: it had a distance but no reading. The hero filtered on
``proximity_pct !== null``, so that trigger took the slot at 100% while
rendering NO DATA, displacing every trigger that was actually being watched.

The reading was absent because a structure the baseline does not list has no
baseline share to subtract. It has one: **zero**. A structure that did not exist
had a zero share, which is its true prior value rather than a substituted one.
"""

from __future__ import annotations

import json
from decimal import Decimal as D
from pathlib import Path

import pytest

from scanner import triggers
from scanner.triggers import Trigger

APP_JS = Path("dashboard/static/app.js")


def _trigger(key: str, value: str | None, prox: str | None, reason: str = "") -> Trigger:
    return Trigger(
        key=key,
        label=key,
        value=value,
        unit="u",
        baseline="0",
        threshold="1",
        condition="c",
        proximity_pct=prox,
        fired=prox == "100.0",
        memo_section="m",
        no_data_reason=reason,
    )


# ------------------------------------------------------------------ ranking --


def test_a_trigger_with_a_distance_but_no_reading_is_not_measured():
    """The exact shape that took the hero slot."""
    t = _trigger("tick_structure", None, "100.0", "set changed")
    assert t.has_distance is True, "it does have a distance"
    assert t.measured is False, "but it is not a measurement of anything"


def test_the_closest_selector_excludes_unmeasured_triggers():
    ranked = triggers.closest(
        [
            _trigger("unmeasured", None, "100.0", "no reading"),
            _trigger("measured", "3", "42.0"),
        ]
    )
    assert ranked is not None
    assert ranked.key == "measured", "a NO DATA trigger must not displace a measured one"
    assert ranked.proximity_pct == "42.0"


def test_unmeasured_triggers_are_listed_rather_than_dropped():
    """Absent and zero must not look alike -- they are reported separately."""
    board = [
        _trigger("a", None, "100.0", "endpoints not yet polled"),
        _trigger("b", "3", "42.0"),
        _trigger("c", None, None, "segment absent"),
    ]
    assert [t.key for t in triggers.unmeasured(board)] == ["a", "c"]
    assert triggers.closest(board).key == "b"


def test_every_unmeasured_trigger_states_why():
    board = [_trigger("a", None, "100.0", "endpoints not yet polled")]
    for t in triggers.unmeasured(board):
        assert t.no_data_reason, f"{t.key} renders NO DATA without saying why"


def test_no_measured_trigger_means_none_rather_than_an_arbitrary_pick():
    assert triggers.closest([_trigger("a", None, "100.0", "why")]) is None
    assert triggers.closest([]) is None


def test_the_payload_carries_measured_so_the_client_cannot_re_derive_it():
    payload = _trigger("a", None, "100.0", "why").as_dict()
    assert payload["measured"] is False
    assert payload["has_distance"] is True
    assert payload["no_data_reason"] == "why"
    json.dumps(payload)  # must stay serialisable


def test_the_hero_ranks_on_measured_not_on_proximity_alone():
    js = APP_JS.read_text()
    hero = js.split("function renderHero(")[1].split("\nfunction ")[0]
    assert "t.measured" in hero, "the hero must rank on the measured flag"
    assert "t.proximity_pct !== null" not in hero, (
        "ranking on proximity alone is the defect: it admits a trigger with no reading"
    )
    assert "hero-unmeasured" in hero, "unmeasured triggers must be listed, not dropped"


# ------------------------------------------------- tick structure baseline --


BASELINE = json.loads(Path("monitor/baseline.json").read_text())


def _current_with(ticks: dict) -> dict:
    return {**BASELINE, "tick_structure": ticks}


def _tick(board: list[Trigger]) -> Trigger:
    return next(t for t in board if t.key == "tick_structure")


def test_a_new_tick_structure_still_yields_a_measured_drift():
    """A structure absent from the baseline had a baseline share of zero.

    That is its true prior value, so drift is computable rather than blank --
    which is what left the trigger with a distance and no reading.
    """
    ticks = dict(BASELINE["tick_structure"])
    ticks["center_half_edge_half_cent"] = {"n": 30, "share_pct": "0.0"}
    t = _tick(triggers.evaluate(BASELINE, _current_with(ticks)))

    assert t.fired is True, "the set changed, which is a real event"
    assert t.measured is True, "and it still reports what it measured"
    assert t.value is not None and D(t.value) >= 0
    assert t.detail["added"] == ["center_half_edge_half_cent"]
    assert "center_half_edge_half_cent" in t.detail["fired_because"]


def test_the_drift_reading_reflects_the_new_structures_actual_share():
    ticks = dict(BASELINE["tick_structure"])
    ticks["something_new"] = {"n": 9000, "share_pct": "12.5"}
    t = _tick(triggers.evaluate(BASELINE, _current_with(ticks)))
    assert D(t.value) == D("12.5"), "seeded at zero, so drift is the whole share"
    assert t.fired is True


def test_a_removed_tick_structure_is_measured_from_its_baseline_share():
    ticks = {k: v for k, v in BASELINE["tick_structure"].items() if k != "deci_cent"}
    t = _tick(triggers.evaluate(BASELINE, _current_with(ticks)))
    assert t.measured is True
    assert D(t.value) == D(BASELINE["tick_structure"]["deci_cent"]["share_pct"])
    assert t.detail["removed"] == ["deci_cent"]
    assert "deci_cent" in t.detail["fired_because"]


def test_an_unchanged_tick_mix_reports_zero_drift_and_does_not_fire():
    t = _tick(triggers.evaluate(BASELINE, _current_with(BASELINE["tick_structure"])))
    assert t.fired is False
    assert D(t.value) == 0
    assert t.proximity_pct == "0.0"
    assert t.detail["fired_because"] == ""


def test_a_fired_tick_trigger_names_its_cause():
    """100% with 0.0pp drift needs an explanation on the panel."""
    ticks = dict(BASELINE["tick_structure"])
    ticks["tiny_new_structure"] = {"n": 30, "share_pct": "0.0"}
    t = _tick(triggers.evaluate(BASELINE, _current_with(ticks)))
    assert t.proximity_pct == "100.0" and D(t.value) == 0
    assert t.detail["fired_because"], (
        "a 100% reading against 0pp drift is unexplained without this"
    )
    assert "fired_because" in APP_JS.read_text(), "and the hero must render it"


@pytest.mark.parametrize("side", ["baseline", "current"])
def test_a_missing_tick_mix_is_unmeasured_with_a_reason(side):
    """Seeding at zero is right for a missing *structure*, not a missing sweep."""
    base = BASELINE if side == "current" else {**BASELINE, "tick_structure": {}}
    cur = _current_with({}) if side == "current" else BASELINE
    t = _tick(triggers.evaluate(base, cur))
    assert t.measured is False
    assert t.no_data_reason, "an absent mix must say why, not read as zero drift"


def test_the_live_shape_no_longer_puts_no_data_in_the_hero():
    """End to end on the committed snapshot with the observed new structure."""
    from monitor.aggregate import SweepAggregate
    from monitor.collect import read_snapshot

    rows = read_snapshot(Path("monitor/snapshots/20260803T070632Z_t0"))
    current = SweepAggregate().fold(rows).result()
    current["tick_structure"]["center_half_edge_half_cent"] = {"n": 30, "share_pct": "0.0"}

    board = triggers.evaluate(BASELINE, current)
    ranked = triggers.closest(board)
    assert ranked is not None
    assert ranked.value is not None, "the hero slot must never hold a NO DATA reading"
    assert all(t.value is not None for t in board if t.measured)
