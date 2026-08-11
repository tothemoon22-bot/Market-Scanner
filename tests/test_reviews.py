"""Dated manual obligations. A missed quarter must show as a number.

The 0.07 coefficient lives in a PDF no endpoint exposes, so re-reading it by
hand is the only check that can detect a change. An obligation with no due date
is one that quietly stops happening, which is this project's failure mode
wearing a calendar.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from monitor import reviews

START = datetime(2026, 8, 5, tzinfo=UTC)


def test_the_fee_schedule_pdf_is_a_tracked_obligation():
    keys = {r.key for r in reviews.REVIEWS}
    assert "fee_schedule_pdf" in keys
    item = next(r for r in reviews.REVIEWS if r.key == "fee_schedule_pdf")
    assert item.period_days == 91, "quarterly"
    assert "no endpoint" in item.why or "not exposed" in item.why or "PDF" in item.what


def test_a_never_done_review_still_has_a_due_date(tmp_path):
    """Without an anchor this would report no due date at all -- an absence."""
    items = reviews.status(since=START, path=tmp_path / "none.jsonl", now=START)
    for item in items:
        assert item["ever_done"] is False
        assert item["last_done"] is None
        assert item["due"], f"{item['key']} has no due date"
        assert not item["is_overdue"], "not overdue on day zero"


def test_a_missed_quarter_shows_as_a_number(tmp_path):
    late = START + timedelta(days=91 + 17)
    items = reviews.status(since=START, path=tmp_path / "none.jsonl", now=late)
    pdf = next(i for i in items if i["key"] == "fee_schedule_pdf")
    assert pdf["is_overdue"]
    assert pdf["overdue_days"] == 17
    assert "OVERDUE by 17d" in reviews.render(items)


def test_recording_a_check_resets_the_clock(tmp_path):
    ledger = tmp_path / "manual_reviews.jsonl"
    late = START + timedelta(days=120)
    assert reviews.status(since=START, path=ledger, now=late)[0]["is_overdue"]

    reviews.record_done("fee_schedule_pdf", "coefficient still 0.07", path=ledger)
    pdf = next(
        i for i in reviews.status(since=START, path=ledger)
        if i["key"] == "fee_schedule_pdf"
    )
    assert pdf["ever_done"] and not pdf["is_overdue"]
    assert pdf["overdue_days"] == 0


def test_only_the_ledger_resets_the_clock(tmp_path):
    """Nothing automatic may mark a manual check done."""
    ledger = tmp_path / "manual_reviews.jsonl"
    with pytest.raises(KeyError):
        reviews.record_done("something_that_does_not_exist", path=ledger)
    assert not ledger.exists()


def test_the_most_recent_completion_is_the_one_that_counts(tmp_path):
    ledger = tmp_path / "manual_reviews.jsonl"
    reviews.record_done("fee_schedule_pdf", "first", path=ledger)
    reviews.record_done("fee_schedule_pdf", "second", path=ledger)
    pdf = next(
        i for i in reviews.status(since=START, path=ledger)
        if i["key"] == "fee_schedule_pdf"
    )
    assert len(ledger.read_text().splitlines()) == 2, "the record keeps both"
    assert pdf["last_done"] is not None


def test_the_heartbeat_carries_the_manual_reviews_and_the_fee_poll():
    from scanner.engine import heartbeat_summary
    from scanner.state import ScannerState

    state = ScannerState()
    body = heartbeat_summary(state)
    assert "manual checks" in body
    assert "fee_schedule_pdf" in body
    # Never polled must say so, not read as "nothing scheduled".
    assert "NEVER POLLED SUCCESSFULLY" in body

    state.fee_changes = {"polled_at": "2026-08-05T00:00:00+00:00", "n_series": 0, "n_events": 0}
    body = heartbeat_summary(state)
    assert "last polled 2026-08-05T00:00:00" in body
    assert "0 series and 0 event changes scheduled" in body
    assert "NEVER POLLED" not in body
