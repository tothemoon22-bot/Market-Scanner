"""Tests for the post-deploy instrumentation.

Each of these targets the way the *instrument* fails rather than the way the
market does, per the standing rule: a freshness monitor that goes stale, a gap
detector with a gap, a transition detector that re-fires on restart. The
standard of correctness is sourced from outside the thing under test wherever
that is possible -- fixed timestamps rather than the wall clock, a written
ledger rather than in-process memory.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from pathlib import Path

from scanner import history, process
from scanner.state import ScannerState, now

APP_JS = Path("dashboard/static/app.js")
STYLES = Path("dashboard/static/styles.css")


# --------------------------------------------------------------- RSS gauge ---


def test_rss_is_read_from_proc_and_is_a_plausible_size():
    rss = process.rss_bytes()
    if rss is None:  # non-Linux; the None path is covered below
        return
    assert rss > 1_000_000, "a running Python process is not under 1 MB"
    assert process.as_dict()["rss_mb"] == round(rss / (1024 * 1024), 1)


def test_unreadable_rss_is_none_and_never_zero(tmp_path):
    """None is NO DATA. Zero would read as a healthy, tiny process."""
    assert process.rss_bytes(tmp_path / "does-not-exist") is None
    garbage = tmp_path / "status"
    garbage.write_text("Name:\tpython\nVmRSS:\tnot-a-number kB\n")
    assert process.rss_bytes(garbage) is None
    assert process.as_dict(garbage)["rss_mb"] is None
    assert process.as_dict(garbage)["source"] is None


def test_rss_parses_the_kb_unit_rather_than_assuming_it(tmp_path):
    status = tmp_path / "status"
    status.write_text("VmRSS:\t  4096 kB\n")
    assert process.rss_bytes(status) == 4096 * 1024
    status.write_text("VmRSS:\t  4096 pages\n")
    assert process.rss_bytes(status) is None, "an unexpected unit must not be assumed to be kB"


# ------------------------------------------------------- achieved cadence ---


def test_achieved_and_configured_are_separate_fields_and_drift_is_derived():
    state = ScannerState()
    state.configured_sweep_interval = 3600.0
    state.sweep_intervals.extend([3500.0, 3700.0])
    snap = state.snapshot()["sweep"]
    assert snap["achieved_interval_seconds"] == 3600.0
    assert snap["configured_interval_seconds"] == 3600.0
    assert snap["drift_seconds"] == 0.0
    assert snap["samples"] == 2


def test_drift_is_none_rather_than_zero_when_either_side_is_missing():
    """Zero drift is a claim of agreement. It must not stand in for ignorance."""
    state = ScannerState()
    assert state.snapshot()["sweep"]["drift_seconds"] is None
    assert state.snapshot()["sweep"]["achieved_interval_seconds"] is None

    state.configured_sweep_interval = 3600.0
    assert state.snapshot()["sweep"]["drift_seconds"] is None, "configured alone is not drift"

    state.sweep_intervals.append(4000.0)
    assert state.snapshot()["sweep"]["drift_seconds"] == 400.0


def test_tracked_cadence_and_cycle_duration_are_different_numbers():
    """The panel once showed a cycle duration under the word "interval"."""
    state = ScannerState()
    state.configured_tracked_interval = 15.0
    state.tracked_intervals.extend([15.2, 14.8])
    state.tracked_poll_seconds = 2.1
    tl = state.snapshot()["tracked_loop"]
    assert tl["achieved_interval_seconds"] == 15.0
    assert tl["last_cycle_seconds"] == 2.1
    assert tl["achieved_interval_seconds"] != tl["last_cycle_seconds"]


# ----------------------------------------------------------- 429 counters ---


def test_rate_limit_24h_counts_timestamps_rather_than_scaling_the_lifetime():
    state = ScannerState()
    state.rate_limit_hits = 3
    state.rate_limit_at.append(now() - timedelta(hours=30))
    state.rate_limit_at.append(now() - timedelta(hours=2))
    state.rate_limit_at.append(now() - timedelta(minutes=1))
    rl = state.snapshot()["rate_limit"]
    assert rl["lifetime"] == 3
    assert rl["last_24h"] == 2
    assert rl["last_at"] is not None


def test_rate_limit_discloses_when_timestamps_have_been_evicted():
    """A bounded deque cannot answer for a lifetime beyond its capacity.

    The panel says so instead of implying the 24h figure is complete -- the
    counter must not become the thing that hides the count.
    """
    state = ScannerState()
    state.rate_limit_hits = 10_000
    state.rate_limit_at.append(now())
    rl = state.snapshot()["rate_limit"]
    assert rl["timestamps_retained"] < rl["lifetime"]
    assert rl["timestamps_capacity"] == state.rate_limit_at.maxlen
    assert "timestamps_retained" in APP_JS.read_text(), "the shortfall must be rendered"


# ------------------------------------------------------- suppressed ledger ---


def _ledger(tmp_path: Path, rows: list[tuple[str, str, str, str]]) -> Path:
    path = tmp_path / "suppressed.jsonl"
    with path.open("w") as fh:
        for at, event, value, because in rows:
            fh.write(
                json.dumps(
                    {
                        "at": at,
                        "event": event,
                        "cost_cents": "98.00",
                        "capacity_contracts": "15",
                        "dollar_value": value,
                        "because": because,
                    }
                )
                + "\n"
            )
    return path


def test_window_is_thirty_days_and_excludes_older_rows(tmp_path):
    old = (datetime.now(UTC) - timedelta(days=45)).isoformat()
    recent = (datetime.now(UTC) - timedelta(days=3)).isoformat()
    path = _ledger(
        tmp_path,
        [(old, "KXGDPYEAR-28", "0.30", "below floor"),
         (recent, "KXGDPYEAR-29", "0.70", "below floor")],
    )
    w = history.suppressed_window(path=path)
    assert w["window_days"] == 30
    assert w["count"] == 1
    assert w["by_series"] == {"KXGDPYEAR": 1}
    # observing_since reaches back past the window: it dates the ledger, not
    # the window, or the sparkline would call unobserved days observed.
    assert w["observing_since"] == old


def test_daily_buckets_distinguish_a_zero_day_from_an_unobserved_day(tmp_path):
    """The gap-detector-with-a-gap case, in sparkline form.

    A day before the ledger existed and a day on which nothing was suppressed
    are both "count 0". Drawing them identically asserts an observation that
    was never made.
    """
    five_days_ago = (datetime.now(UTC) - timedelta(days=5)).isoformat()
    path = _ledger(tmp_path, [(five_days_ago, "KXGDPYEAR-28", "0.30", "below floor")])
    daily = history.suppressed_window(path=path)["daily"]

    assert len(daily) == 30
    observed = [d for d in daily if d["observed"]]
    unobserved = [d for d in daily if not d["observed"]]
    assert len(observed) == 6, "the ledger starts 5 days ago, inclusive of today"
    assert unobserved, "days before the ledger existed must be marked unobserved"
    assert all(d["count"] == 0 for d in unobserved)
    zero_but_observed = [d for d in observed if d["count"] == 0]
    assert zero_but_observed, "a genuine zero day is needed for this test to mean anything"
    # Same count, different meaning, and the payload keeps them apart.
    assert zero_but_observed[0]["count"] == unobserved[0]["count"]
    assert zero_but_observed[0]["observed"] != unobserved[0]["observed"]


def test_sparkline_renders_unobserved_days_as_a_distinct_mark():
    js, css = APP_JS.read_text(), STYLES.read_text()
    assert "d.observed" in js, "the renderer must branch on observed"
    assert "unobserved" in js and ".spark i.unobserved" in css
    assert ".spark i.zero" in css, "an observed zero needs its own mark too"


def test_empty_ledger_reports_no_data_rather_than_a_flat_line(tmp_path):
    w = history.suppressed_window(path=tmp_path / "absent.jsonl")
    assert w["count"] == 0
    assert w["daily"] is None, "no ledger is NO DATA, not 30 days of measured zeros"
    assert w["max_dollar_value"] is None


def test_window_reports_median_and_max_capacity_times_edge(tmp_path):
    at = datetime.now(UTC).isoformat()
    path = _ledger(
        tmp_path,
        [(at, "KXGDPYEAR-28", "0.30", "below floor"),
         (at, "KXGDPYEAR-29", "0.70", "below floor"),
         (at, "KXBTCY-27JAN0100", "24.00", "below floor")],
    )
    w = history.suppressed_window(path=path)
    assert D(w["max_dollar_value"]) == D("24.00")
    assert D(w["median_dollar_value"]) == D("0.70")


# --------------------------------------------------------- monthly review ---


def test_review_names_series_suppressed_more_than_four_times(tmp_path):
    at = datetime.now(UTC).isoformat()
    rows = [(at, "KXGDPYEAR-28", "0.30", "below floor") for _ in range(5)]
    rows += [(at, "KXETHY-27JAN0100", "0.10", "below floor")]
    r = history.review(path=_ledger(tmp_path, rows))
    assert r["repeat_threshold"] == 4
    assert r["repeat_series"] == {"KXGDPYEAR": 5}
    assert "KXETHY" not in r["repeat_series"]
    assert "KXGDPYEAR (5x)" in history.render_review(r)


def test_review_surfaces_the_max_against_the_floor_without_acting_on_it(tmp_path):
    """Approaching $25 from below is a structural change the floor is hiding.

    The review's job is to say so. It must not adjust anything, so the floor it
    prints is read from the constant rather than recomputed.
    """
    at = datetime.now(UTC).isoformat()
    r = history.review(path=_ledger(tmp_path, [(at, "KXGDPYEAR-28", "24.90", "below floor")]))
    assert D(r["max_dollar_value"]) < history.MIN_DOLLAR_VALUE
    assert r["floor_dollars"] == str(history.MIN_DOLLAR_VALUE)
    body = history.render_review(r)
    assert "$24.90" in body and "$25 floor" in body


def test_review_of_an_empty_ledger_says_so(tmp_path):
    r = history.review(path=tmp_path / "absent.jsonl")
    assert r["count"] == 0
    assert "nothing in the last 30d" in history.render_review(r)


def test_review_rides_the_heartbeat_and_never_pushes_on_its_own():
    engine = Path("scanner/engine.py").read_text()
    assert "history.render_review" in engine
    body = engine.split("def heartbeat_summary")[1].split("\nasync def")[0]
    assert "render_review" in body, "the review belongs in the heartbeat payload"
    # push_alerts is the high-priority path; the review must not reach it.
    assert "push_alerts" not in body


# ------------------------------------------------------- band maturation ---


def _band(name: str, observations: int) -> history.Band:
    return history.Band(
        series=name,
        observations=observations,
        min_cost_cents=D("95"),
        max_cost_cents=D("105"),
        crossings=1,
    )


def test_band_transition_is_recorded_once_and_survives_a_restart(tmp_path):
    """The transition detector that re-fires on reboot is the failure here.

    Previous state is read from the ledger, not from process memory, so a
    restart cannot re-announce every band the monitor has ever established.
    """
    ledger = tmp_path / "band_events.jsonl"
    bands = {"KXGDPYEAR": _band("KXGDPYEAR", 8)}

    first = history.record_band_transitions(bands, path=ledger)
    assert [e["series"] for e in first] == ["KXGDPYEAR"]
    assert first[0]["observations"] == 8

    # Same process, next sweep.
    assert history.record_band_transitions(bands, path=ledger) == []
    # A restart: nothing in memory, everything in the ledger.
    assert history.established_series(path=ledger) == {"KXGDPYEAR"}
    assert history.record_band_transitions(bands, path=ledger) == []
    assert len(ledger.read_text().splitlines()) == 1


def test_a_band_below_the_threshold_never_records_a_transition(tmp_path):
    ledger = tmp_path / "band_events.jsonl"
    assert history.record_band_transitions({"KXGDPYEAR": _band("KXGDPYEAR", 7)}, ledger) == []
    assert not ledger.exists()
    # And crossing it later does record, once.
    assert history.record_band_transitions({"KXGDPYEAR": _band("KXGDPYEAR", 8)}, ledger)


def test_one_sweep_is_one_observation_however_many_partitions_it_wrote(tmp_path):
    """The bug the first live run found: a band established by breadth, not time.

    record() writes one row per partition per sweep. KXGDPYEAR lists eleven
    years, so a single sweep wrote eleven rows and the band declared itself
    KNOWN with a "range" that was a cross-section of eleven contracts at one
    instant. Eight observations has to mean eight sweeps.
    """
    ledger = tmp_path / "series_history.jsonl"
    partitions = [
        {
            "event": f"KXGDPYEAR-{year}",
            "cost_cents": str(90 + year - 26),
            "capacity_contracts": "15",
            "below_par": year < 30,
        }
        for year in range(26, 37)  # eleven listed years, as the exchange lists them
    ]
    history.record(partitions, path=ledger)

    band = history.bands(path=ledger)["KXGDPYEAR"]
    assert band.rows == 11, "the ledger still records every partition"
    assert band.events == 11
    assert band.observations == 1, "eleven contracts seen once is one observation"
    assert band.state == "UNKNOWN"
    assert not band.known
    assert band.is_outside(D("50")) is False, "an UNKNOWN band claims nothing"


def test_observations_advance_one_per_sweep(tmp_path):
    """Eight sweeps of two partitions: sixteen rows, eight observations."""
    ledger = tmp_path / "series_history.jsonl"
    sweeps = history.MIN_OBSERVATIONS_FOR_BAND
    with ledger.open("w") as fh:
        for sweep in range(sweeps):
            at = f"2026-08-{4 + sweep:02d}T00:00:00+00:00"
            for event, cost in (("KXGDPYEAR-28", "98"), ("KXGDPYEAR-29", "95")):
                fh.write(
                    json.dumps(
                        {
                            "at": at,
                            "event": event,
                            "series": "KXGDPYEAR",
                            "cost_cents": cost,
                            "capacity_contracts": "15",
                            "below_par": True,
                        }
                    )
                    + "\n"
                )

    band = history.bands(path=ledger)["KXGDPYEAR"]
    assert band.rows == sweeps * 2
    assert band.observations == sweeps
    assert band.known and band.state == "KNOWN"

    # One sweep short is not established, however many rows it holds.
    trimmed = tmp_path / "short.jsonl"
    trimmed.write_text("\n".join(ledger.read_text().splitlines()[:-2]) + "\n")
    assert history.bands(path=trimmed)["KXGDPYEAR"].observations == sweeps - 1
    assert not history.bands(path=trimmed)["KXGDPYEAR"].known


def test_band_payload_carries_the_threshold_so_the_ui_need_not_hardcode_it():
    d = _band("KXGDPYEAR", 3).as_dict()
    assert d["threshold"] == history.MIN_OBSERVATIONS_FOR_BAND
    assert d["needs"] == history.MIN_OBSERVATIONS_FOR_BAND - 3
    assert d["state"] == "UNKNOWN"
    js = APP_JS.read_text()
    assert "b.threshold" in js
    assert not re.search(r"/\s*8\b", js), "the UI must not hardcode the observation threshold"


def test_trigger_board_shows_observation_count_against_the_threshold():
    js = APP_JS.read_text()
    assert "renderBandMaturation" in js
    assert "b.observations" in js
    assert "band established" in js and "needs" in js


# ---------------------------------------------------------- panel copy ----


def test_the_accumulating_sentence_appears_in_the_tooltip_and_the_readme():
    """One sentence, two places. Markdown wrapping is not a difference."""

    def flat(text: str) -> str:
        # Collapse newlines and JS string concatenation, keep the words.
        return re.sub(r"\s+", " ", text.replace('" +\n  "', "").replace("**", ""))

    sentence = (
        "Accumulating means the $25 floor is masking a real change "
        "— that is a read-the-ledger event, not a raise-the-floor event."
    )
    assert "SUPPRESSED_TOOLTIP" in APP_JS.read_text()
    assert sentence in flat(APP_JS.read_text()), "the tooltip must carry the sentence in full"
    assert sentence in flat(Path("monitor/README.md").read_text()), (
        "the panel's claim and the README must not drift apart"
    )


def test_heartbeat_summary_reports_achieved_against_configured():
    from scanner.engine import heartbeat_summary

    state = ScannerState()
    state.configured_sweep_interval = 3600.0
    state.configured_tracked_interval = 15.0
    state.sweep_intervals.extend([3600.0, 3600.0])
    state.tracked_intervals.extend([15.0, 15.0])
    body = heartbeat_summary(state)
    assert "3600s achieved vs 3600s configured" in body
    assert "15s achieved vs 15s configured" in body
    assert "429s:" in body
    assert "suppressed ledger" in body


def test_heartbeat_says_not_measured_rather_than_inventing_a_cadence():
    from scanner.engine import heartbeat_summary

    body = heartbeat_summary(ScannerState())
    assert "full sweep: not yet measured" in body
    assert "tracked subset: not yet measured" in body

