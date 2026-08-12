"""Disk headroom: the rate is the measurement, and a rate needs a window."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from monitor import disk


def _write(path: Path, samples: list[tuple[float, int]], free_at_end: int = 10 * 2**30) -> None:
    """samples: (hours_ago, used_bytes)."""
    base = datetime(2026, 8, 12, tzinfo=UTC)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        for hours, used in samples:
            fh.write(json.dumps({
                "at": (base - timedelta(hours=hours)).isoformat(),
                "used_bytes": used,
                "free_bytes": free_at_end,
                "total_bytes": 40 * 2**30,
                "ledger_bytes": used,
            }) + "\n")


def test_one_sample_is_not_a_trend(tmp_path: Path) -> None:
    path = tmp_path / "disk.jsonl"
    _write(path, [(0, 1000)])
    got = disk.headroom(path, tmp_path).as_dict()

    assert got["growth_mb_per_day"] is None
    assert got["days_to_full"] is None
    assert "two samples" in got["reason"]
    # Free space is still reported -- only the rate is unavailable.
    assert got["free_gb"] is not None


def test_a_short_window_reports_its_reason_not_a_huge_rate(tmp_path: Path) -> None:
    """Two samples a minute apart divide noise by a small number. Extrapolating
    that would produce a confident, wrong exhaustion date."""
    path = tmp_path / "disk.jsonl"
    _write(path, [(0.02, 1_000_000_000), (0, 1_010_000_000)])
    got = disk.headroom(path, tmp_path).as_dict()

    assert got["days_to_full"] is None
    assert "at least" in got["reason"]
    assert got["span_hours"] is not None and got["span_hours"] < disk.MIN_SPAN_HOURS


def test_growth_projects_an_exhaustion_date(tmp_path: Path) -> None:
    path = tmp_path / "disk.jsonl"
    # 1 GB used over 24h, with 10 GB free -> 10 days.
    _write(path, [(24, 0), (0, 2**30)], free_at_end=10 * 2**30)

    got = disk.headroom(
        path, tmp_path, at=datetime(2026, 8, 12, tzinfo=UTC)
    )
    d = got.as_dict()
    # tmp_path's real free space is used for the projection, not the fixture's,
    # so assert the rate rather than the date arithmetic on a fake disk.
    assert d["growth_mb_per_day"] == 1024.0
    assert d["span_hours"] == 24.0
    assert d["days_to_full"] is not None and d["days_to_full"] > 0
    assert d["exhaustion_at"] is not None


def test_a_shrinking_disk_has_no_exhaustion_date(tmp_path: Path) -> None:
    """A negative projection rendered as a forecast is an invented measurement."""
    path = tmp_path / "disk.jsonl"
    _write(path, [(24, 2**30), (0, 0)])
    got = disk.headroom(path, tmp_path).as_dict()

    assert got["growth_mb_per_day"] < 0
    assert got["days_to_full"] is None
    assert got["exhaustion_at"] is None
    assert got["warn"] is False
    assert "not growing" in got["reason"]


def test_flat_is_not_growing_and_is_distinct_from_unmeasured(tmp_path: Path) -> None:
    path = tmp_path / "disk.jsonl"
    _write(path, [(24, 5000), (0, 5000)])
    got = disk.headroom(path, tmp_path).as_dict()

    assert got["growth_mb_per_day"] == 0.0, "a measured zero, not None"
    assert got["days_to_full"] is None
    assert got["reason"] == "not growing over the observed window"


def test_sample_appends_and_headroom_reads_it_back(tmp_path: Path) -> None:
    path = tmp_path / "disk.jsonl"
    (tmp_path / "payload.bin").write_bytes(b"x" * 4096)

    row = disk.sample(path, tmp_path)
    assert row["ledger_bytes"] >= 4096
    assert row["used_bytes"] is not None
    assert disk.headroom(path, tmp_path).samples == 1


def test_the_warn_threshold_fires(tmp_path: Path) -> None:
    """Shown to fire, not merely shown not to error."""
    near = disk.Headroom(
        free_bytes=1, total_bytes=2, ledger_bytes=1, growth_bytes_per_day=1.0,
        span_hours=24.0, samples=2, days_to_full=disk.EXHAUSTION_WARN_DAYS - 1,
        exhaustion_at="2026-10-01", reason="x",
    )
    far = disk.Headroom(
        free_bytes=1, total_bytes=2, ledger_bytes=1, growth_bytes_per_day=1.0,
        span_hours=24.0, samples=2, days_to_full=disk.EXHAUSTION_WARN_DAYS + 1,
        exhaustion_at="2027-10-01", reason="x",
    )
    assert near.as_dict()["warn"] is True
    assert far.as_dict()["warn"] is False


def test_disk_sample_failure_is_recorded_not_swallowed(tmp_path, monkeypatch) -> None:
    from scanner import engine
    from scanner.state import ScannerState

    state = ScannerState()

    def boom(*_a, **_k):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(engine.disk, "sample", boom)
    engine._record(state, "disk_sample", engine.disk.sample)

    health = state.source("disk_sample")
    assert health.state == "FAILING"
    assert health.last_error_type == "OSError"
