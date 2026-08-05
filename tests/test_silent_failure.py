"""The silent-failure audit: catching must produce a state.

A `try/except` that stops a subsystem killing a sweep is correct and stays. What
it must not do is leave the caller unable to tell "ran and found nothing" from
"did not run" -- that is how the `.csv.gz` filename bug would have presented:
`strptime` throwing every sweep, logged and swallowed, with the panel reading
"needs two sweeps to compare" indefinitely while the scanner reported healthy.

These tests are written against observed behaviour rather than against the shape
of the handlers, because a handler that looks right on its own line is exactly
what this failure mode is made of.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from scanner.state import (
    CONSECUTIVE_FAILURE_ALERT,
    SUBSYSTEMS,
    Outcome,
    ScannerState,
)

APP_JS = Path("dashboard/static/app.js")
ENGINE = Path("scanner/engine.py")


# ------------------------------------------------------- failure counters ---


def test_every_subsystem_is_registered_before_it_ever_runs():
    """A subsystem absent from the payload cannot be seen to be failing."""
    snap = ScannerState().snapshot()
    assert set(snap["sources"]) == set(SUBSYSTEMS)
    for name, source in snap["sources"].items():
        assert source["state"] == "NEVER_RUN", name
        assert source["failures"] == 0
        assert source["successes"] == 0


def test_a_zero_failure_subsystem_renders_a_confirmed_zero_not_an_absence():
    state = ScannerState()
    state.source("population").ok()
    payload = state.snapshot()["sources"]["population"]
    assert payload["state"] == "OK"
    assert payload["failures"] == 0
    assert "0 failures" in APP_JS.read_text(), "the confirmed zero must be rendered"


def test_failure_counter_records_count_timestamp_and_exception_type():
    state = ScannerState()
    try:
        raise ValueError("time data '...Z.csv' does not match format")
    except ValueError as exc:
        state.source("population").failed(f"{type(exc).__name__}: {exc}", exc)

    payload = state.snapshot()["sources"]["population"]
    assert payload["failures"] == 1
    assert payload["consecutive_failures"] == 1
    assert payload["last_error_type"] == "ValueError"
    assert payload["last_error_at"] is not None
    assert payload["last_error_age_seconds"] is not None
    assert payload["state"] == "FAILING"
    assert not payload["healthy"]


def test_consecutive_failures_reset_on_success_and_totals_do_not():
    state = ScannerState()
    source = state.source("kalshi_tracked")
    for _ in range(4):
        source.failed("ValueError: boom", ValueError("boom"))
    assert source.consecutive_failures == 4 and source.failures == 4
    source.ok()
    assert source.consecutive_failures == 0, "it is back up"
    assert source.failures == 4, "but it was down, and that stays on the record"
    assert source.state == "OK"


def test_failing_subsystems_are_listed_separately_for_the_alert_path():
    state = ScannerState()
    for _ in range(CONSECUTIVE_FAILURE_ALERT):
        state.source("population").failed("ValueError: boom", ValueError("boom"))
    state.source("coinbase").ok()

    failing = state.snapshot()["failing_subsystems"]
    assert [f["name"] for f in failing] == ["population"]
    assert failing[0]["consecutive_failures"] == CONSECUTIVE_FAILURE_ALERT


# ---------------------------------------------------------- empty states ---


def test_the_three_empty_states_are_distinct_values():
    assert len({Outcome.NOT_RUN, Outcome.EMPTY, Outcome.FAILED, Outcome.OK}) == 4


def test_population_defaults_to_not_run_rather_than_absent():
    """Before any sweep, the honest answer is "not run", not "no data"."""
    outcome = ScannerState().snapshot()["population"]
    assert outcome["state"] == Outcome.NOT_RUN
    assert outcome["reason"]


def test_a_failed_reconciliation_does_not_render_as_needs_two_sweeps():
    """The exact regression: a thrown comparison must not read as a young one."""
    state = ScannerState()
    state.population = Outcome.make(
        Outcome.FAILED, "reconciliation raised ValueError: time data does not match"
    )
    outcome = state.snapshot()["population"]
    assert outcome["state"] == Outcome.FAILED
    assert "ValueError" in outcome["reason"]
    assert outcome["state"] != Outcome.NOT_RUN

    js = APP_JS.read_text()
    assert "outcomeChip" in js
    assert "NOT_RUN" in js and "FAILED" in js
    # The "needs two sweeps" copy must not be reachable for a failed state.
    assert "needs two sweeps to compare" not in js


def test_the_ui_renders_three_states_with_three_marks():
    js, css = APP_JS.read_text(), Path("dashboard/static/styles.css").read_text()
    for fn in ("const NO_DATA", "const NOT_RUN", "const FAILED"):
        assert fn in js, f"{fn} must exist as its own renderer"
    for cls in (".nodata", ".notrun", ".failed"):
        assert cls in css, f"{cls} needs its own visual mark"


# ------------------------------------------------------- degraded readings ---


def test_a_substituted_fee_type_is_counted_rather_than_applied_silently():
    """An unrecognised fee schedule priced as quadratic is a substituted number."""
    from scanner import funnel

    funnel.unknown_fee_types.clear()
    model = funnel._fee_model({"fee_type": "some_new_schedule", "fee_multiplier": "1"})
    assert model is not None, "the funnel still computes"
    assert funnel.unknown_fee_types["some_new_schedule"] == 1
    funnel.unknown_fee_types.clear()

    state = ScannerState()
    state.unknown_fee_types = {"some_new_schedule": 3}
    assert state.snapshot()["unknown_fee_types"] == {"some_new_schedule": 3}
    assert "Unrecognised fee types" in APP_JS.read_text()


def test_undelivered_alerts_are_distinguished_from_alerts_that_did_not_fire():
    state = ScannerState()
    assert state.snapshot()["undelivered_alerts"] is None
    state.undelivered_alerts = [{"trigger": "x", "at": "2026-08-05T00:00:00Z", "why": "Timeout"}]
    assert len(state.snapshot()["undelivered_alerts"]) == 1
    assert "did not send" in APP_JS.read_text()


def test_unresolved_series_are_surfaced_because_they_shrink_the_fee_free_universe():
    """Live sweeps show 2 (KXMLBWINS, KXNEWOUTBREAK) against 11 fee-free series.

    A series with no fee_multiplier cannot be counted as fee-free, so the
    headline universe shrinks with no visible cause.
    """
    state = ScannerState()
    state.unresolved_series = ["KXMLBWINS", "KXNEWOUTBREAK"]
    assert state.snapshot()["unresolved_series"] == ["KXMLBWINS", "KXNEWOUTBREAK"]
    assert "Unresolved series" in APP_JS.read_text()


def test_a_degraded_reading_is_not_recorded_as_a_subsystem_outage():
    """Partial degradation and "did not run" are different, and must stay so.

    The metadata pass completing with two lookups failed is a degraded reading.
    Filing it as a subsystem failure would make consecutive_failures climb on a
    chronic, benign condition and page every sweep -- collapsing two states into
    one, which is the conflation this audit removes.
    """
    body = ENGINE.read_text()
    section = body.split("# The pass ran, so the subsystem is up")[1].split("below = ")[0]
    assert 'state.source("series_metadata").ok()' in section
    assert "degraded reading" in section
    assert '"series_metadata").failed' not in body, (
        "unresolved series must never increment the outage counter"
    )


# ------------------------------------------------------------ alert path ---


def test_the_transport_does_not_alert_about_its_own_failure():
    """ntfy cannot carry news that ntfy is down.

    A handler that tried would be a check sharing a failure mode with its
    subject. It is excluded by name, and the missing heartbeat is the
    out-of-band signal instead.
    """
    body = ENGINE.read_text().split("async def _push_subsystem_failures")[1].split("\ndef ")[0]
    assert 'h.name != "ntfy"' in body
    assert "cannot carry news of its own failure" in body


def test_the_subsystem_alert_runs_even_when_the_sweep_itself_threw():
    """The case where it matters most is the one where everything else failed."""
    src = ENGINE.read_text()
    loop = src.split("async def full_sweep_loop")[1].split("\nasync def ")[0]
    push_at = loop.index("_push_subsystem_failures")
    except_at = loop.index("except Exception as exc:")
    assert push_at > except_at, "the subsystem push must sit outside the try block"
    assert "Outside the try" in loop


SOURCE_FILES = sorted(
    p for p in [*Path("scanner").rglob("*.py"), *Path("monitor").rglob("*.py")]
)


def _except_handlers_that_bind(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.name:
            yield node


@pytest.mark.parametrize("path", SOURCE_FILES, ids=lambda p: str(p))
def test_failed_calls_inside_except_blocks_pass_the_exception(path):
    """`failed(msg)` loses the type; `failed(msg, exc)` keeps it.

    Checked against the syntax tree rather than by matching lines: the calls
    span lines, and a `.failed(` outside an except block is legitimately
    reporting a count rather than a caught exception.
    """
    tree = ast.parse(path.read_text())
    bare = []
    for handler in _except_handlers_that_bind(tree):
        for node in ast.walk(handler):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "failed"
                and len(node.args) < 2
            ):
                bare.append(f"{path}:{node.lineno}")
    assert not bare, f"these drop the exception type on the floor: {bare}"


# --------------------------------------------------------------------------
# The audit, in executable form.
#
# Every swallowing handler in scanner/ and monitor/ is listed with the answer to
# one question: **can the caller tell "ran and found nothing" from "did not
# run"?** A handler that records into a counter or an Outcome answers yes by
# construction. The ones below answer yes some other way, and each says how.
#
# A new handler that is neither will fail this test, which is the point: the
# next one of these should not need a bug to be noticed.
# --------------------------------------------------------------------------
RECORDING_MARKERS = ("failed", "log", "raise", "unresolved", "Outcome", "unknown_fee_types")

REVIEWED_SILENT_HANDLERS = {
    "scanner/process.py:24": (
        "rss_bytes returns None when /proc is unreadable. The caller renders NO "
        "DATA naming the platform, so absence is visible and is never zero."
    ),
    "scanner/process.py:33": (
        "Same, for an unexpected VmRSS unit. Refusing to guess the unit is the "
        "point; None reaches the panel."
    ),
    "monitor/population.py:142": (
        "_at_or_none returns None for an unparseable timestamp, which sends the "
        "market to the UNATTRIBUTED residual -- the most visible counter in the "
        "system. Asserted in test_population.py."
    ),
}


def _silent_handlers() -> dict[str, int]:
    found = {}
    for path in SOURCE_FILES:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            body = ast.dump(ast.Module(body=node.body, type_ignores=[]))
            if not any(marker in body for marker in RECORDING_MARKERS):
                found[f"{path}:{node.lineno}"] = node.lineno
    return found


def test_every_swallowing_handler_is_either_recorded_or_reviewed():
    unreviewed = sorted(set(_silent_handlers()) - set(REVIEWED_SILENT_HANDLERS))
    assert not unreviewed, (
        "these handlers swallow without recording and are not in the reviewed "
        f"allowlist: {unreviewed}. Either record the failure, or add it with the "
        "reason the caller can still tell 'did not run' from 'found nothing'."
    )


def test_the_allowlist_does_not_outlive_its_entries():
    """A stale exemption is how an unreviewed handler slips back in."""
    stale = sorted(set(REVIEWED_SILENT_HANDLERS) - set(_silent_handlers()))
    assert not stale, (
        f"these allowlist entries no longer match a silent handler: {stale}. "
        "Line numbers moved, or the handler now records -- either way the "
        "exemption is not describing the code."
    )


def test_every_reviewed_exemption_states_a_reason():
    for location, reason in REVIEWED_SILENT_HANDLERS.items():
        assert len(reason) > 40, f"{location} needs a real reason, not a placeholder"
