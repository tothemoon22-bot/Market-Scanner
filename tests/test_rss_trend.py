"""RSS trend: a point reading cannot answer a 24h memory gate.

The gate criterion is whether resident memory is *growing* over a day. A single
current value renders steady and climbing identically, which means the criterion
could only be evaluated by watching the panel for 24 hours or by shelling into
the box. Both defeat the point of the gate being readable from a phone.
"""

from __future__ import annotations

from pathlib import Path

from scanner.state import ScannerState


def _status(tmp_path: Path, kb: int) -> Path:
    p = tmp_path / f"status_{kb}"
    p.write_text(f"Name:\tpython\nVmRSS:\t{kb} kB\nThreads:\t4\n")
    return p


def test_first_and_peak_are_recorded_and_growth_is_derived(tmp_path, monkeypatch) -> None:
    from scanner import process

    state = ScannerState()
    for kb in (100_000, 140_000, 120_000):
        monkeypatch.setattr(process, "rss_bytes", lambda _p=None, _kb=kb: _kb * 1024)
        state.note_rss()

    proc = state.snapshot()["process"]
    assert proc["rss_first_mb"] == 97.7
    assert proc["rss_peak_mb"] == 136.7
    # Peak minus first, so a spike that later subsides is still reported.
    assert proc["rss_growth_mb"] == 39.1
    assert proc["rss_observed_hours"] >= 0
    assert proc["rss_peak_at"] is not None


def test_a_flat_process_reports_zero_growth_not_absent(tmp_path, monkeypatch) -> None:
    """A confirmed zero, not a missing field. The gate needs to distinguish
    'measured, did not grow' from 'not measured'."""
    from scanner import process

    monkeypatch.setattr(process, "rss_bytes", lambda _p=None: 100_000 * 1024)
    state = ScannerState()
    state.note_rss()
    state.note_rss()

    proc = state.snapshot()["process"]
    assert proc["rss_growth_mb"] == 0.0
    assert proc["rss_peak_mb"] == proc["rss_first_mb"]


def test_unreadable_rss_stays_none_and_never_becomes_zero(monkeypatch) -> None:
    from scanner import process

    monkeypatch.setattr(process, "rss_bytes", lambda _p=None: None)
    state = ScannerState()
    state.note_rss()

    proc = state.snapshot()["process"]
    for key in ("rss_first_mb", "rss_peak_mb", "rss_growth_mb", "rss_observed_hours"):
        assert proc[key] is None, f"{key} must be None, not 0 -- an unread gauge is not a reading"


def test_beat_samples_rss(monkeypatch) -> None:
    """The sampling point matters: `beat` runs on the heartbeat, so the trend
    keeps advancing even if sweeps stall."""
    from scanner import process

    monkeypatch.setattr(process, "rss_bytes", lambda _p=None: 50_000 * 1024)
    state = ScannerState()
    assert state.rss_peak_bytes is None
    state.beat()
    assert state.rss_peak_bytes == 50_000 * 1024
