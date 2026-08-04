"""Scanner and dashboard gates.

The governing rule is that no panel may display a number the system does not
measure. Most of these tests exist to make that mechanical rather than a matter
of care: a placeholder constant, a credential, or an order-placement reference
introduced later fails the suite.
"""

from __future__ import annotations

import json
import re
from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path

import pytest

from dashboard import app as dashboard_app
from monitor import collect
from scanner import funnel, guard, triggers
from scanner.state import STALE_AFTER_SECONDS, ScannerState, now

SNAPSHOT = Path("monitor/snapshots/20260803T070632Z_t0")
BASELINE = Path("monitor/baseline.json")
STATIC = Path("dashboard/static")


@pytest.fixture(scope="module")
def rows() -> list[dict]:
    return collect.read_snapshot(SNAPSHOT)


@pytest.fixture(scope="module")
def baseline() -> dict:
    return json.loads(BASELINE.read_text())


# --------------------------------------------------------------------------
# The startup assertion. A host provisioned to trade must not run the scanner.
# --------------------------------------------------------------------------
def test_guard_exits_when_a_credential_is_present():
    with pytest.raises(SystemExit) as exc:
        guard.assert_no_credentials({"KALSHI_PRIVATE_KEY_PATH": "/etc/kalshi.pem"})
    assert exc.value.code == 2


def test_guard_ignores_empty_and_unrelated_variables():
    guard.assert_no_credentials({"KALSHI_KEY_ID": "  ", "PATH": "/usr/bin", "HOME": "/root"})


def test_guard_names_every_credential_it_finds():
    found = guard.find_credentials({"KALSHI_KEY_ID": "x", "KALSHI_API_SECRET": "y"})
    assert found == ["KALSHI_API_SECRET", "KALSHI_KEY_ID"]


# --------------------------------------------------------------------------
# Offline detection. A dashboard rendering stale numbers cheerfully is the
# same silent death as a disabled cron.
# --------------------------------------------------------------------------
def test_state_reports_offline_once_the_heartbeat_ages_past_the_threshold():
    state = ScannerState()
    state.beat()
    assert state.snapshot()["online"] is True

    state.heartbeat_at = now() - timedelta(seconds=STALE_AFTER_SECONDS + 1)
    snap = state.snapshot()
    assert snap["online"] is False
    assert snap["heartbeat_age_seconds"] > STALE_AFTER_SECONDS


def test_state_is_offline_before_the_first_heartbeat():
    assert ScannerState().snapshot()["online"] is False


def test_health_endpoint_returns_503_when_offline():
    from fastapi.testclient import TestClient

    dashboard_app.state.heartbeat_at = now() - timedelta(seconds=STALE_AFTER_SECONDS + 5)
    with TestClient(dashboard_app.app) as client:
        assert client.get("/api/health").status_code == 503


# --------------------------------------------------------------------------
# Absence is never rendered as zero.
# --------------------------------------------------------------------------
def test_unmeasured_fields_are_null_rather_than_zero():
    snap = ScannerState().snapshot()
    for path in ("metrics", "triggers", "funnel", "partitions", "tripwire", "tracked"):
        assert snap[path] is None, f"{path} must be null before measurement, not empty-or-zero"
    assert snap["sweep"]["last_duration_seconds"] is None
    assert snap["sweep"]["achieved_interval_seconds"] is None


def test_trigger_without_a_measurable_distance_reports_none_not_zero(baseline):
    """A baseline sitting on its own threshold has no distance to report."""
    degenerate = json.loads(json.dumps(baseline))
    degenerate["spread_by_segment"]["tick_structure"]["linear_cent"]["median"] = "2"
    current = json.loads(json.dumps(degenerate))
    spread = next(t for t in triggers.evaluate(degenerate, current) if t.key == "spread")
    assert spread.proximity_pct is None
    assert "no distance to measure" in spread.no_data_reason


def test_proximity_is_zero_at_baseline_and_one_hundred_at_threshold(baseline, rows):
    from monitor import metrics

    current = metrics.compute(rows)
    evaluated = triggers.evaluate(baseline, current)
    spread = next(t for t in evaluated if t.key == "spread")
    assert spread.proximity_pct == "0.0", "unchanged from baseline must read 0%"

    breached = json.loads(json.dumps(current))
    breached["spread_by_segment"]["tick_structure"]["linear_cent"]["median"] = "2"
    fired = next(t for t in triggers.evaluate(baseline, breached) if t.key == "spread")
    assert fired.proximity_pct == "100.0"
    assert fired.fired is True


# --------------------------------------------------------------------------
# Forced breach: the gate's "force a trigger breach in a fixture" check.
# --------------------------------------------------------------------------
def test_forced_tripwire_breach_fires_and_renders(baseline, rows):
    from monitor import alerts, metrics

    current = metrics.compute(rows)
    breached = json.loads(json.dumps(current))
    for partition in breached["deci_cent_fee_free_tripwire"]["partitions"]:
        partition["cost_cents"] = "99.10"
        partition["below_par"] = True
    breached["deci_cent_fee_free_tripwire"]["n_below_par"] = len(
        breached["deci_cent_fee_free_tripwire"]["partitions"]
    )

    fired = [t for t in triggers.evaluate(baseline, breached) if t.fired]
    assert any(t.key == "tripwire" for t in fired)

    rendered = alerts.render(alerts.evaluate(baseline, breached))
    assert "deci-cent" in rendered
    assert alerts.FOOTER in rendered
    assert "NEGATIVE_RESULT.md" in rendered


def test_every_trigger_carries_a_memo_section(baseline, rows):
    from monitor import metrics

    for trigger in triggers.evaluate(baseline, metrics.compute(rows)):
        assert trigger.memo_section, f"{trigger.key} has no memo section to point at"


# --------------------------------------------------------------------------
# The funnel terminates, and says where.
# --------------------------------------------------------------------------
def test_funnel_terminates_and_names_the_stage(rows):
    stages = funnel.build(rows)
    assert [s.key for s in stages][0] == "scanned"
    assert stages[-1].key == "actionable"
    assert stages[-1].count == 0
    dead = funnel.terminates_at(stages)
    assert dead is not None and dead.key == "fee_gate"


def test_funnel_is_monotonically_non_increasing_within_each_unit(rows):
    stages = funnel.build(rows)
    for unit in {s.unit for s in stages}:
        counts = [s.count for s in stages if s.unit == unit]
        assert counts == sorted(counts, reverse=True), f"{unit} stage counts increase"


def test_funnel_fee_gate_uses_the_real_fee_model(rows):
    """A basket at par must fail the gate once the taker fee is added."""
    legs = [
        {"ask_yes_cents": "50.00", "fee_type": "quadratic", "fee_multiplier": "1"},
        {"ask_yes_cents": "50.00", "fee_type": "quadratic", "fee_multiplier": "1"},
    ]
    assert funnel.basket_fee_cents(legs, 100) == D("3.50")
    free = [dict(leg, fee_multiplier="0") for leg in legs]
    assert funnel.basket_fee_cents(free, 100) == D("0.00")


# --------------------------------------------------------------------------
# No synthetic values, no credentials, no orders -- mechanically.
# --------------------------------------------------------------------------
FORBIDDEN_CODE = re.compile(
    r"portfolio/orders|client_order_id|KALSHI-ACCESS|RSA|api_key|private_key", re.IGNORECASE
)


def test_no_auth_or_order_references_in_scanner_or_dashboard():
    for path in [*Path("scanner").rglob("*.py"), *Path("dashboard").rglob("*.py")]:
        if path.name == "guard.py":
            continue  # names credentials in order to refuse them
        hit = FORBIDDEN_CODE.search(path.read_text())
        assert hit is None, f"{path} references {hit.group(0)!r}"


#: "bar" and "baz" are deliberately absent: a dashboard with progress bars
#: contains the word "bar" legitimately, and a matcher that cries wolf gets
#: deleted. These are the markers that only ever appear in stub data.
PLACEHOLDER = re.compile(
    r"\b(lorem|ipsum|foo|dummy|placeholder|sample_data|fakeData|mockData|"
    r"stubbed|TODO|FIXME|XXX)\b",
    re.IGNORECASE,
)


def strip_comments(source: str) -> str:
    """Remove block and line comments, leaving code and string literals.

    The check targets what executes. Prose that *names* the prohibited thing in
    order to forbid it -- "there are no placeholder constants in this file" --
    is documentation, not a stub.
    """
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    source = re.sub(r"<!--.*?-->", "", source, flags=re.DOTALL)
    return re.sub(r"^\s*//.*$", "", source, flags=re.MULTILINE)


def test_no_placeholder_or_stub_markers_anywhere_in_the_ui():
    for path in [*STATIC.glob("*.js"), *STATIC.glob("*.html"), *STATIC.glob("*.css")]:
        hit = PLACEHOLDER.search(strip_comments(path.read_text()))
        assert hit is None, f"{path} contains placeholder marker {hit.group(0)!r}"


#: Durations and intervals, in seconds or milliseconds. These are the only
#: large literals the view is allowed to hold, because they describe how often
#: it redraws -- not anything about the exchange.
ALLOWED_TIME_LITERALS = {
    "1000",  # ms per second
    "2000",  # re-render tick so ages keep advancing, ms
    "3000",  # websocket reconnect delay, ms
    "5400",  # 90 minutes, the minute/hour cutover in age()
    "3600",  # seconds per hour
    "86400",  # seconds per day
    "172800",  # 48 hours, the hour/day cutover in age()
}


def test_frontend_declares_no_hardcoded_market_figures():
    """The UI may hold redraw timings, never measurements.

    Any large numeric literal that is not a documented duration would be a
    measurement baked into the view, which is exactly the artifact this project
    exists in opposition to.
    """
    source = strip_comments((STATIC / "app.js").read_text())
    offenders = sorted(set(re.findall(r"\b\d{4,}\b", source)) - ALLOWED_TIME_LITERALS)
    assert not offenders, f"app.js contains numeric literals that look like data: {offenders}"


def test_threshold_copy_in_the_ui_matches_the_published_study():
    """The one place the UI states fixed numbers is the required-mispricing
    line. Those are published findings, so the copy must match the study that
    generated them -- if the study is regenerated and moves, this fails."""
    source = (STATIC / "app.js").read_text().replace("<b>", "").replace("</b>", "")
    study = Path("research/SPREAD_STUDY.md").read_text()
    for legs, value in (("2 legs", "10.5"), ("4 legs", "19.3"), ("10 legs", "36.3")):
        assert f"{legs} {value}" in source, f"UI is missing the {legs} threshold"
        assert f"**{value}c**" in study, f"{value}c is not in SPREAD_STUDY.md"


def test_service_worker_never_caches_scanner_state():
    sw = (STATIC / "sw.js").read_text()
    assert '"/api' in sw or "/api" in sw
    assert "startsWith(\"/api\")" in sw, "state responses must bypass the cache"


def test_pwa_manifest_is_installable():
    manifest = json.loads((STATIC / "manifest.webmanifest").read_text())
    assert manifest["display"] == "standalone"
    assert manifest["start_url"] == "/"
    assert manifest["icons"], "an installable PWA needs at least one icon"


def test_snapshot_mode_is_labelled_as_not_live():
    dashboard_app.seed_from_snapshot(SNAPSHOT)
    body = dashboard_app.payload()
    assert body["snapshot_source"] == SNAPSHOT.name
    assert (STATIC / "app.js").read_text().count("snapshot_source") >= 1
