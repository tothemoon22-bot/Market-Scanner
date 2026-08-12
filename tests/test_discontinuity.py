"""The 509db99 proximity marker, and the machinery that surfaces it."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from monitor import archive, discontinuity


def test_the_509db99_marker_is_seeded_and_complete() -> None:
    """A marker nobody can interpret is not a marker. Every field a reader needs
    to decide which readings to distrust, and in which direction, is present."""
    found = [m for m in discontinuity.markers("proximity_watch") if m.commit == "509db99"]
    assert len(found) == 1, "the proximity discontinuity must be seeded exactly once"
    m = found[0]

    assert m.at == "2026-08-12T02:03:11+00:00"
    assert m.regime_from == "2026-08-04T15:39:37+00:00"
    assert m.regime_from_commit == "e3692b5"
    assert m.direction == "overstated"
    # The earlier regime pinned proximity at 100 on routine fee-change noise,
    # so days_since_within_20 read ~0 continuously. Overstated, not understated.
    assert "100%" in m.effect
    assert "bands" in m.earlier_regime
    assert "recomputed" in m.note or "recompute" in m.note


def test_the_span_is_stated_and_is_not_brief() -> None:
    """More than a few hours, so it has to be said wherever the figure is
    quoted -- not waited out. Derived from the two timestamps rather than
    hardcoded, so it cannot drift away from them."""
    m = next(m for m in discontinuity.markers("proximity_watch") if m.commit == "509db99")
    assert m.span_hours == pytest.approx(178.39, abs=0.01)
    assert m.span_hours / 24 == pytest.approx(7.43, abs=0.01)
    assert m.brief is False, "7.4 days is not a span that can be waited out"


def test_span_hours_is_none_when_the_regime_start_is_unknown() -> None:
    m = discontinuity.Marker(
        measurement="x", commit="abc1234", at="2026-01-01T00:00:00+00:00",
        earlier_regime="something",
    )
    assert m.span_hours is None
    assert m.brief is False, "unknown span must not read as brief"
    assert m.as_dict()["span_hours"] is None


def test_spanning_detects_a_window_that_crosses_the_boundary() -> None:
    boundary = datetime(2026, 8, 12, 2, 3, 11, tzinfo=UTC)

    crossing = discontinuity.spanning(
        boundary - timedelta(days=1), boundary + timedelta(days=1), "proximity_watch"
    )
    assert [m.commit for m in crossing] == ["509db99"]

    after = discontinuity.spanning(
        boundary + timedelta(seconds=1), boundary + timedelta(days=30), "proximity_watch"
    )
    assert after == [], "a window entirely after the boundary spans nothing"

    before = discontinuity.spanning(
        boundary - timedelta(days=30), boundary - timedelta(seconds=1), "proximity_watch"
    )
    assert before == [], "a window entirely before the boundary spans nothing"


def test_measurement_filter_does_not_leak_other_series() -> None:
    assert discontinuity.spanning(
        datetime(2020, 1, 1, tzinfo=UTC), datetime(2030, 1, 1, tzinfo=UTC), "population"
    ) == []


def test_record_appends_and_never_edits(tmp_path: Path) -> None:
    path = tmp_path / "d.jsonl"
    discontinuity.record(
        "population", "aaaaaaa", "counted rows", at="2026-09-01T00:00:00+00:00", path=path
    )
    discontinuity.record(
        "population", "bbbbbbb", "counted events", at="2026-09-02T00:00:00+00:00", path=path
    )

    lines = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
    assert [row["commit"] for row in lines] == ["aaaaaaa", "bbbbbbb"]

    got = discontinuity.markers("population", path=path)
    assert [m.commit for m in got] == ["aaaaaaa", "bbbbbbb"]
    # Seeded markers survive alongside the ledger.
    assert any(m.commit == "509db99" for m in discontinuity.markers(path=path))


def test_a_marker_without_a_commit_or_a_description_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="commit"):
        discontinuity.record("x", "  ", "described", path=tmp_path / "d.jsonl")
    with pytest.raises(ValueError, match="earlier regime measured"):
        discontinuity.record("x", "abc1234", "   ", path=tmp_path / "d.jsonl")
    assert not (tmp_path / "d.jsonl").exists(), "a refused marker must not be written"


def test_the_marker_ledger_is_archived() -> None:
    """Durability: the weekly Action pulls it, so instance loss cannot take the
    record of a measurement change with it."""
    assert "discontinuities" in archive.LEDGER_FILES
    assert archive.LEDGER_FILES["discontinuities"] == discontinuity.LEDGER_PATH


def test_proximity_watch_publishes_a_spanning_marker() -> None:
    from scanner.state import ScannerState

    state = ScannerState()
    state.observing_since = datetime(2026, 8, 10, tzinfo=UTC)
    state.peak_proximity_pct = 3.0

    watch = state.snapshot()["proximity_watch"]
    assert [d["commit"] for d in watch["discontinuities"]] == ["509db99"]
    assert watch["discontinuities"][0]["direction"] == "overstated"
    assert watch["discontinuities"][0]["brief"] is False


def test_a_process_started_after_the_boundary_shows_no_marker() -> None:
    """The honest case, and the usual one.

    ``peak_proximity_pct`` and ``last_within_20_at`` live only in memory, so a
    restart resets them. Deploying the fix restarts the process, which means the
    earlier regime's readings are gone rather than mislabelled -- there is no
    cross-boundary window to warn about. The marker is documentary here, and the
    spanning machinery is what catches the next change.
    """
    from scanner.state import ScannerState

    state = ScannerState()
    state.observing_since = datetime(2026, 8, 12, 3, 0, 0, tzinfo=UTC)
    state.peak_proximity_pct = 3.0

    assert state.snapshot()["proximity_watch"]["discontinuities"] == []
