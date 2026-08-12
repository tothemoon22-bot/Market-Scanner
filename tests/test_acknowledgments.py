"""Acknowledged observations: append-only, per-observation, baseline untouched.

`center_half_edge_half_cent` appeared after the baseline was frozen, so the
tick-structure trigger reads 100% permanently. A permanently-firing trigger is a
permanently-ignored one, and it holds the hero slot forever.

This is the failure the below-par alert was designed around and this one missed,
so the tests are written against the properties that failure needs: the baseline
is never edited, a *different* observation of the same kind still fires, and an
acknowledged trigger stays visible rather than disappearing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from monitor import acknowledgments as acks
from scanner import triggers

BASELINE = json.loads(Path("monitor/baseline.json").read_text())


def _current_with_structure(name: str) -> dict:
    ticks = dict(BASELINE["tick_structure"])
    ticks[name] = {"n": 30, "share_pct": "0.0"}
    return {**BASELINE, "tick_structure": ticks}


def _tick(board):
    return next(t for t in board if t.key == "tick_structure")


# ------------------------------------------------------------ the ledger ---


def test_the_observed_structure_ships_acknowledged():
    """The memo records it, so the board should not keep pointing at it."""
    ledger = acks.load(Path("does-not-exist.jsonl"))
    ack = ledger[("tick_structure", "center_half_edge_half_cent")]
    assert ack.at == "2026-08-05"
    assert ack.memo_section == "Finding 4 - the falsifier fired"
    assert "half-cent" in ack.note


def test_an_acknowledgment_requires_a_memo_section(tmp_path):
    """No write-up, no acknowledgment -- otherwise it is quiet dismissal."""
    with pytest.raises(ValueError, match="memo section"):
        acks.record("tick_structure", "x", memo_section="  ", path=tmp_path / "a.jsonl")
    assert not (tmp_path / "a.jsonl").exists()


def test_recording_appends_and_never_edits_the_baseline(tmp_path):
    before = Path("monitor/baseline.json").read_bytes()
    path = tmp_path / "acknowledgments.jsonl"
    acks.record("tick_structure", "new_thing", "Finding 4", "seen it", path)
    acks.record("tick_structure", "another", "Finding 4", "seen it too", path)

    assert len(path.read_text().splitlines()) == 2, "append-only"
    assert Path("monitor/baseline.json").read_bytes() == before, (
        "acknowledgment must never touch the immutable baseline"
    )
    ledger = acks.load(path)
    assert ("tick_structure", "new_thing") in ledger
    # And the seeded entries survive alongside the file's.
    assert ("tick_structure", "center_half_edge_half_cent") in ledger


def test_the_ledger_is_keyed_per_observation_not_per_trigger():
    ledger = acks.load(Path("does-not-exist.jsonl"))
    assert all(isinstance(k, tuple) and len(k) == 2 for k in ledger)
    assert all(k[1] for k in ledger), "the observation is part of the key"


# ------------------------------------------------------- board behaviour ---


def test_an_acknowledged_trigger_is_excluded_from_ranking():
    board = triggers.evaluate(BASELINE, _current_with_structure("center_half_edge_half_cent"))
    tick = _tick(board)
    assert tick.fired is True, "it still fired"
    assert tick.measured is True, "and it is still measured"
    assert tick.acknowledged is not None
    assert tick.rankable is False, "but it is not the closest thing to watch"

    ranked = triggers.closest(board)
    assert ranked is not None and ranked.key != "tick_structure"


def test_an_acknowledged_trigger_stays_visible_with_its_date():
    board = triggers.evaluate(BASELINE, _current_with_structure("center_half_edge_half_cent"))
    tick = _tick(board)
    assert tick in board, "nothing disappears"
    assert tick.acknowledged["at"] == "2026-08-05"
    assert tick.acknowledged["memo_section"] == "Finding 4 - the falsifier fired"
    assert triggers.acknowledged(board) == [tick]
    assert tick.as_dict()["acknowledged"]["at"] == "2026-08-05"


def test_a_different_structure_fires_again_at_full_priority():
    """Per-observation, not per-trigger. This is the property that matters."""
    board = triggers.evaluate(BASELINE, _current_with_structure("something_else_entirely"))
    tick = _tick(board)
    assert tick.fired is True
    assert tick.acknowledged is None, "a new observation is not covered by an old ack"
    assert tick.rankable is True
    assert triggers.closest(board).key == "tick_structure"


def test_a_new_observation_alongside_an_acknowledged_one_still_fires():
    """All observations acknowledged, or none of it is."""
    ticks = dict(BASELINE["tick_structure"])
    ticks["center_half_edge_half_cent"] = {"n": 30, "share_pct": "0.0"}
    ticks["brand_new_structure"] = {"n": 5, "share_pct": "0.0"}
    tick = _tick(triggers.evaluate(BASELINE, {**BASELINE, "tick_structure": ticks}))
    assert tick.acknowledged is None, (
        "one unacknowledged observation keeps the whole trigger at full priority"
    )
    assert tick.rankable is True


def test_an_unfired_trigger_is_never_acknowledged():
    board = triggers.evaluate(BASELINE, {**BASELINE})
    for t in board:
        if not t.fired:
            assert t.acknowledged is None


def test_acknowledgment_does_not_suppress_the_alert_only_the_ranking():
    """The board de-prioritises; the alert layer is untouched by this module."""
    from monitor import alerts

    ticks = dict(BASELINE["tick_structure"])
    ticks["center_half_edge_half_cent"] = {"n": 30, "share_pct": "0.0"}
    fired = alerts.evaluate(BASELINE, {**BASELINE, "tick_structure": ticks})
    assert any("tick structure set changed" in a.trigger for a in fired), (
        "acknowledgment must not reach into the alert path"
    )
    assert "acknowledg" not in Path("monitor/alerts.py").read_text().lower()


# ------------------------------------------------------------ the hero ---


def test_the_hero_promotes_the_closest_unacknowledged_trigger():
    board = triggers.evaluate(BASELINE, _current_with_structure("center_half_edge_half_cent"))
    ranked = triggers.closest(board)
    rankable = [t for t in board if t.rankable]
    assert ranked is not None
    assert ranked.proximity_pct == max(t.proximity_pct for t in rankable)
    assert ranked.acknowledged is None and ranked.value is not None


def test_the_client_ranks_on_rankable_not_on_measured():
    js = Path("dashboard/static/app.js").read_text()
    hero = js.split("function renderHero(")[1].split("\nfunction ")[0]
    assert "t.rankable" in hero
    assert "hero-acknowledged" in hero, "acknowledged triggers must stay visible"


def test_the_trigger_row_shows_the_acknowledgment():
    js = Path("dashboard/static/app.js").read_text()
    assert "acknowledged" in js
    assert "NEGATIVE_RESULT.md" in js, "the row must name the memo section"
