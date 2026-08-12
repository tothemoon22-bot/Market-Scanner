"""`ledger_archive` must be a subsystem, not a decorative row.

It was registered in ``SUBSYSTEMS`` and nothing ever set it, so it rendered
NEVER RUN forever and had no path to the consecutive-failure push every other
subsystem has. That is this section's own failure mode applied to the mechanism
built to preserve the record of it: **an archive that silently never runs looks
exactly like one that has not run yet.**
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from monitor import archive
from scanner import engine
from scanner.state import ScannerState, now

WINDOW = archive.ARCHIVE_STALE_AFTER_DAYS * 86400


def test_a_fresh_box_that_has_never_been_pulled_is_not_yet_a_failure() -> None:
    state = ScannerState()
    engine._assess_archive(state)
    assert state.source("ledger_archive").state == "NEVER_RUN"


def test_never_pulled_past_the_window_is_a_failure_not_a_quiet_never_run() -> None:
    """The state the box is in today: durability is notional and must say so."""
    state = ScannerState()
    state.started_at = now() - timedelta(seconds=WINDOW + 3600)
    engine._assess_archive(state)

    health = state.source("ledger_archive")
    assert health.state == "FAILING"
    assert "never run" in health.last_error
    assert "entire history" in health.last_error


def test_a_recent_fetch_is_ok() -> None:
    state = ScannerState()
    state.started_at = now() - timedelta(seconds=WINDOW + 3600)
    state.ledgers_served_at = now() - timedelta(days=1)
    engine._assess_archive(state)
    assert state.source("ledger_archive").state == "OK"


def test_a_stale_fetch_fails_and_accumulates() -> None:
    state = ScannerState()
    state.ledgers_served_at = now() - timedelta(seconds=WINDOW + 86400)

    for _ in range(3):
        engine._assess_archive(state)

    health = state.source("ledger_archive")
    assert health.state == "FAILING"
    assert health.consecutive_failures == 3, (
        "consecutive failures must accumulate, or the archive never reaches the "
        "push threshold every other subsystem has"
    )


def test_recovery_clears_the_run() -> None:
    state = ScannerState()
    state.ledgers_served_at = now() - timedelta(seconds=WINDOW + 86400)
    engine._assess_archive(state)
    assert state.source("ledger_archive").consecutive_failures == 1

    state.ledgers_served_at = now()
    engine._assess_archive(state)
    assert state.source("ledger_archive").state == "OK"
    assert state.source("ledger_archive").consecutive_failures == 0


def test_serving_the_manifest_marks_the_subsystem_healthy() -> None:
    """The endpoint is the only evidence the box has that the Action ran."""
    from fastapi.testclient import TestClient

    from dashboard import app as dashboard_app

    # Without this the startup hook launches the real engine and appends a
    # restart row to the live watch ledger. A test must not write to the
    # instrument's own history.
    dashboard_app._runtime["snapshot_source"] = "test"
    dashboard_app.state.sources.pop("ledger_archive", None)
    with TestClient(dashboard_app.app) as client:
        assert client.get("/api/ledgers").status_code == 200

    assert dashboard_app.state.source("ledger_archive").state == "OK"
    assert dashboard_app.state.ledgers_served_at is not None


def _yaml_without_comments(text: str) -> str:
    """Strip `#` comment lines before scanning.

    Without this the assertion below matched the string it forbids inside the
    comment *explaining* why it is forbidden -- a check written in the same
    language as its subject, scanning a corpus that includes itself. That is
    item 3 of "the one pattern behind every broken check", and it caught this
    test on the first run.
    """
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


def test_the_workflow_fails_rather_than_skipping_without_the_variable() -> None:
    """The job-level `if:` guard was a silent skip: no failure reported,
    durability notional, workflow list green."""
    path = Path(__file__).resolve().parent.parent / ".github/workflows/archive-ledgers.yml"
    active = _yaml_without_comments(path.read_text())

    assert "if: vars.SCANNER_URL != ''" not in active, (
        "the job-level skip is back; an unconfigured archive is a failure, not "
        "a no-op"
    )
    assert "vars.SCANNER_URL == ''" in active and "exit 1" in active
    assert "https://aimarketscanner.cloud" in active, (
        "the failure message must say what to set, not just that something is unset"
    )
