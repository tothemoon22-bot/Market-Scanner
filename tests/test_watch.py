"""The persisted proximity window and the restart record."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from monitor import archive, watch
from scanner.state import ScannerState


def _at(hours: float) -> datetime:
    return datetime(2026, 8, 12, 12, 0, tzinfo=UTC) + timedelta(hours=hours)


def test_a_cold_ledger_reports_no_restarts_not_one(tmp_path: Path) -> None:
    """A machine that has never restarted must not read as having restarted
    once. The read happens before the write for exactly this reason."""
    path = tmp_path / "watch.jsonl"
    first = watch.begin_session(path, at=_at(0))

    assert first.restarts == 0
    assert first.last_restart_at is None
    assert first.observing_since == _at(0)
    assert first.peak_proximity_pct is None
    assert first.last_within_20_at is None


def test_the_window_survives_a_restart(tmp_path: Path) -> None:
    """The defect this module exists to fix: seven days of observed quiet
    becoming 0.4 days because a process restarted."""
    path = tmp_path / "watch.jsonl"
    watch.begin_session(path, at=_at(0))
    watch.record_peak(3.4, path, at=_at(1))
    watch.record_within_20(21.0, path, at=_at(2))
    watch.record_peak(9.1, path, at=_at(3))

    after = watch.begin_session(path, at=_at(24 * 7))

    assert after.observing_since == _at(0), "the window must not restart with the process"
    assert after.peak_proximity_pct == 9.1
    assert after.last_within_20_at == _at(2)
    assert after.restarts == 1
    assert after.last_restart_at == _at(0), "the *previous* start, not this one"


def test_peak_is_all_time_not_this_session(tmp_path: Path) -> None:
    path = tmp_path / "watch.jsonl"
    watch.begin_session(path, at=_at(0))
    watch.record_peak(44.0, path, at=_at(1))
    watch.begin_session(path, at=_at(2))
    watch.record_peak(5.0, path, at=_at(3))

    assert watch.load(path, at=_at(4)).peak_proximity_pct == 44.0


def test_restarts_24h_counts_only_the_last_day(tmp_path: Path) -> None:
    path = tmp_path / "watch.jsonl"
    for h in (-100, -50, -30, -20, -2, 0):
        watch.begin_session(path, at=_at(h))

    got = watch.load(path, at=_at(0))
    assert got.restarts == 5
    assert got.restarts_24h == 3, "only -20, -2 and 0 are inside 24h"


def test_a_torn_final_line_costs_one_record_not_the_file(tmp_path: Path) -> None:
    """A kill mid-write leaves a partial line. Refusing to parse the file would
    lose the whole history to protect one row."""
    path = tmp_path / "watch.jsonl"
    watch.begin_session(path, at=_at(0))
    watch.record_peak(7.0, path, at=_at(1))
    with path.open("a") as fh:
        fh.write('{"kind": "peak", "at": "2026-')

    got = watch.load(path, at=_at(2))
    assert got.peak_proximity_pct == 7.0
    assert got.observing_since == _at(0)


def test_nothing_is_backfilled(tmp_path: Path) -> None:
    """The pre-509db99 readings measured a different quantity and are gone.

    The series begins at the first start row; it must not claim to reach back
    before persistence existed.
    """
    path = tmp_path / "watch.jsonl"
    started = watch.begin_session(path, at=_at(0))

    rows = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
    assert [r["kind"] for r in rows] == ["start"], "a first run must write one row"
    assert started.observing_since == _at(0)
    assert started.peak_proximity_pct is None, (
        "a fresh ledger must report no peak rather than inventing one"
    )


def test_state_restores_and_publishes_the_window(tmp_path: Path) -> None:
    path = tmp_path / "watch.jsonl"
    watch.begin_session(path, at=_at(0))
    watch.record_peak(6.5, path, at=_at(1))
    watch.record_within_20(25.0, path, at=_at(2))

    state = ScannerState()
    assert state.watch_persisted is False, "constructing a state must not touch disk"
    state.begin_session(path)

    w = state.snapshot()["proximity_watch"]
    assert w["persisted"] is True
    assert w["peak_proximity_pct"] == 6.5
    assert w["restarts"] == 1
    assert w["last_within_20_at"] == _at(2).isoformat()
    # The window predates this process, which is the whole point.
    assert w["observed_days"] > (state.snapshot()["uptime_seconds"] / 86400)


def test_crash_looping_is_a_distinct_state_from_a_deploy(tmp_path: Path) -> None:
    """3b: small uptime after one deploy and small uptime inside a crash-loop
    must not render identically.

    ``ScannerState.begin_session`` reads the real clock, so the fixtures are
    written relative to it rather than to a fixed date.
    """
    real_now = datetime.now(UTC)

    deploy = tmp_path / "deploy.jsonl"
    watch.begin_session(deploy, at=real_now - timedelta(hours=6))
    calm = ScannerState()
    calm.begin_session(deploy)
    w = calm.snapshot()["proximity_watch"]
    assert w["restarts"] == 1 and w["crash_looping"] is False

    loop = tmp_path / "loop.jsonl"
    for minutes in (40, 30, 20, 10):
        watch.begin_session(loop, at=real_now - timedelta(minutes=minutes))
    sick = ScannerState()
    sick.begin_session(loop)
    w = sick.snapshot()["proximity_watch"]
    assert w["crash_looping"] is True
    assert w["restarts_24h"] >= watch.RESTART_ALERT_24H


def test_a_clock_running_ahead_does_not_fake_a_crash_loop(tmp_path: Path) -> None:
    """`now - s <= 24h` is true of every future timestamp. One row from a box
    whose clock ran ahead would otherwise report a crash-loop that never
    happened -- and an alert that fires on a bad clock trains you to ignore it.
    """
    path = tmp_path / "watch.jsonl"
    for h in (100, 200, 300, 400):
        watch.begin_session(path, at=_at(h))

    got = watch.load(path, at=_at(0))
    assert got.restarts == 3, "the rows are still counted as restarts"
    assert got.restarts_24h == 0, "but none of them happened in the last 24 hours"


def test_the_watch_ledger_is_archived_and_a_known_subsystem() -> None:
    from scanner.state import SUBSYSTEMS

    assert archive.LEDGER_FILES["watch"] == watch.LEDGER_PATH
    assert "watch_ledger" in SUBSYSTEMS, (
        "a ledger write that fails must land on the health panel like every "
        "other subsystem, not be swallowed"
    )


def test_a_failed_ledger_write_is_recorded_not_swallowed(tmp_path, monkeypatch) -> None:
    from scanner import engine

    state = ScannerState()
    state.begin_session(tmp_path / "watch.jsonl")
    state.triggers = [{"proximity_pct": "44.0"}]

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(engine.watch, "record_peak", boom)
    monkeypatch.setattr(engine.watch, "record_within_20", boom)
    engine._record_proximity(state)

    health = state.source("watch_ledger")
    assert health.state == "FAILING"
    assert health.last_error_type == "OSError"
    # The sweep still got its reading; only the durability failed.
    assert state.peak_proximity_pct == 44.0
