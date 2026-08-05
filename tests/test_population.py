"""Population reconciliation: attribution rules, the residual, and the trend.

The residual is the whole point, so the tests that matter most are the ones that
stop it being quietly absorbed. An attribution rule that matches everything
would drive the residual to zero permanently and the alert would never fire
again -- a check that is vacuously satisfied, which is the failure mode this
project keeps finding in its own instruments.

Market shapes here are the ones actually observed on the exchange (a new
listing, one created-then-opened later, a settled market, an early close, and
the KXCAGOAT anomaly), not invented values.
"""

from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from monitor import population
from src.research.reconcile import (
    ADDED_CREATED,
    ADDED_OPENED,
    ADDED_RFQ,
    REMOVED_EARLY,
    REMOVED_SETTLED,
    UNATTRIBUTED,
    attribute_added,
    attribute_removed,
)

BOUNDARY = datetime(2026, 8, 4, 16, 22, 39, tzinfo=UTC)
NOW = datetime(2026, 8, 5, 0, 35, 33, tzinfo=UTC)


# ------------------------------------------------------------- attribution --


@pytest.mark.parametrize(
    "market,expected",
    [
        # Listed after the prior sweep: the ordinary case, 9,503 of 15,707.
        ({"ticker": "KXNASDAQ100U-26AUG05", "created_time": "2026-08-04T22:00:00Z",
          "open_time": "2026-08-04T22:05:00Z"}, ADDED_CREATED),
        # Created earlier, opened later: correctly excluded before, 6,203 of them.
        ({"ticker": "KXMLBHRR-26AUG05", "created_time": "2026-08-03T10:00:00Z",
          "open_time": "2026-08-04T18:00:00Z"}, ADDED_OPENED),
        # Both before the boundary and absent anyway: the KXCAGOAT anomaly.
        ({"ticker": "KXCAGOAT-26SEP30", "created_time": "2026-07-31T20:11:07Z",
          "open_time": "2026-07-31T20:45:00Z"}, UNATTRIBUTED),
        ({"ticker": "KXMVE-SOMETHING", "created_time": "2026-08-04T22:00:00Z"}, ADDED_RFQ),
        ({"ticker": "KXNOFIELDS-26"}, UNATTRIBUTED),
    ],
)
def test_added_markets_are_attributed_or_left_unattributed(market, expected):
    assert attribute_added(market, BOUNDARY) == expected


@pytest.mark.parametrize(
    "market,expected",
    [
        ({"ticker": "KXMLBTB-26AUG04", "close_time": "2026-08-04T23:00:00Z",
          "can_close_early": "True"}, REMOVED_SETTLED),
        ({"ticker": "KXTENNIS-26AUG06", "close_time": "2026-08-06T12:00:00Z",
          "can_close_early": "True"}, REMOVED_EARLY),
        ({"ticker": "KXLONG-27JAN01", "close_time": "2027-01-01T12:00:00Z",
          "can_close_early": "False"}, UNATTRIBUTED),
        ({"ticker": "KXNOFIELDS-26"}, UNATTRIBUTED),
    ],
)
def test_removed_markets_are_attributed_or_left_unattributed(market, expected):
    assert attribute_removed(market, NOW) == expected


def test_created_is_checked_before_opened():
    """A market created after the boundary is new whatever its open time.

    If the order flipped, every new listing with a future open would be filed
    as "correctly excluded before", which is a different claim about the
    exchange.
    """
    market = {
        "ticker": "KXNEW-26",
        "created_time": "2026-08-04T22:00:00Z",
        "open_time": "2026-08-04T22:30:00Z",
    }
    assert attribute_added(market, BOUNDARY) == ADDED_CREATED


def test_an_unexplained_market_is_never_quietly_attributed():
    """The rule that keeps the residual meaningful.

    A market whose timestamps predate the boundary has no explanation for being
    absent then and present now. If any rule claimed it, the residual would go
    to zero permanently and the alert would be vacuous.
    """
    stale = {
        "ticker": "KXWHATEVER-26",
        "created_time": "2026-01-01T00:00:00Z",
        "open_time": "2026-01-01T00:00:00Z",
        "close_time": "2027-01-01T00:00:00Z",
        "can_close_early": "False",
    }
    assert attribute_added(stale, BOUNDARY) == UNATTRIBUTED
    assert attribute_removed(stale, NOW) == UNATTRIBUTED


def test_malformed_timestamps_do_not_attribute():
    """Garbage must land in the residual, not be parsed into an explanation."""
    assert attribute_added(
        {"ticker": "KX-1", "created_time": "not-a-date", "open_time": ""}, BOUNDARY
    ) == UNATTRIBUTED
    assert attribute_removed({"ticker": "KX-1", "close_time": "not-a-date"}, NOW) == UNATTRIBUTED


# ------------------------------------------------------------ reconciliation --


def _ledger(path: Path, markets: list[dict]) -> Path:
    with population.LedgerWriter(path) as writer:
        for m in markets:
            writer.write(m)
    return path


def test_reconciliation_accounts_for_every_moved_market(tmp_path):
    prior = _ledger(tmp_path / "a.csv.gz", [
        {"ticker": "STAYS-1", "close_time": "2027-01-01T00:00:00Z"},
        {"ticker": "SETTLES-1", "close_time": "2026-08-04T23:00:00Z"},
        {"ticker": "EARLY-1", "close_time": "2027-01-01T00:00:00Z",
         "can_close_early": "True"},
    ])
    current = _ledger(tmp_path / "b.csv.gz", [
        {"ticker": "STAYS-1", "close_time": "2027-01-01T00:00:00Z"},
        {"ticker": "NEW-1", "created_time": "2026-08-04T22:00:00Z"},
        {"ticker": "OPENED-1", "created_time": "2026-08-01T00:00:00Z",
         "open_time": "2026-08-04T20:00:00Z"},
    ])

    r = population.reconcile(prior, current, BOUNDARY, NOW)
    assert (r.n_prior, r.n_current) == (3, 3)
    assert r.added == 2 and r.removed == 2 and r.moved == 4
    assert sum(r.added_causes.values()) == r.added
    assert sum(r.removed_causes.values()) == r.removed
    assert r.added_causes == {ADDED_CREATED: 1, ADDED_OPENED: 1}
    assert r.removed_causes == {REMOVED_SETTLED: 1, REMOVED_EARLY: 1}
    assert r.unattributed == 0


def test_the_residual_counts_both_sides(tmp_path):
    prior = _ledger(tmp_path / "a.csv.gz", [
        {"ticker": "VANISHES-1", "close_time": "2027-01-01T00:00:00Z"},
    ])
    current = _ledger(tmp_path / "b.csv.gz", [
        {"ticker": "APPEARS-1", "created_time": "2026-01-01T00:00:00Z",
         "open_time": "2026-01-01T00:00:00Z"},
    ])
    r = population.reconcile(prior, current, BOUNDARY, NOW)
    assert r.unattributed == 2, "one added-side and one removed-side"
    assert r.unattributed_pct == 100.0


def test_the_alert_is_on_the_residual_not_on_the_count(tmp_path):
    """A large count change with everything attributed must not fire."""
    prior = _ledger(tmp_path / "a.csv.gz", [
        {"ticker": f"OLD-{i}", "close_time": "2026-08-04T23:00:00Z"} for i in range(500)
    ])
    current = _ledger(tmp_path / "b.csv.gz", [
        {"ticker": f"NEW-{i}", "created_time": "2026-08-04T22:00:00Z"} for i in range(5000)
    ])
    r = population.reconcile(prior, current, BOUNDARY, NOW)
    assert r.n_current - r.n_prior == 4500, "a large, unambiguous count change"
    assert r.moved == 5500
    assert r.unattributed == 0
    assert not r.fires, "a fully attributed change is expected and uninteresting"


def test_the_alert_fires_at_the_threshold_and_not_below(tmp_path):
    threshold = population.UNATTRIBUTED_ALERT_THRESHOLD

    def build(n_unattributed):
        prior = _ledger(tmp_path / f"a{n_unattributed}.csv.gz", [
            {"ticker": "STAYS-1", "close_time": "2027-01-01T00:00:00Z"}
        ])
        current = _ledger(tmp_path / f"b{n_unattributed}.csv.gz", [
            {"ticker": "STAYS-1", "close_time": "2027-01-01T00:00:00Z"},
            *[{"ticker": f"ODD-{i}", "created_time": "2026-01-01T00:00:00Z",
               "open_time": "2026-01-01T00:00:00Z"} for i in range(n_unattributed)],
        ])
        return population.reconcile(prior, current, BOUNDARY, NOW)

    assert not build(threshold - 1).fires
    assert build(threshold).fires


def test_ledger_holds_only_the_five_reconciliation_fields(tmp_path):
    """No market objects on disk either -- the ledger is a projection."""
    path = _ledger(tmp_path / "a.csv.gz", [
        {"ticker": "KX-1", "created_time": "2026-08-04T22:00:00Z", "open_time": "",
         "close_time": "2027-01-01T00:00:00Z", "can_close_early": "False",
         "bid_yes_cents": "50.0000", "underlying": "should not be written"},
    ])
    with gzip.open(path, "rt") as fh:
        header = fh.readline().strip().split(",")
    assert header == list(population.LEDGER_FIELDS)
    assert "should not be written" not in path.read_bytes().decode("latin-1", "ignore")


def test_previous_ledger_excludes_the_one_just_written(tmp_path):
    for stamp in ("20260804T000000Z", "20260805T000000Z", "20260806T000000Z"):
        _ledger(population.ledger_path(stamp, tmp_path), [{"ticker": "KX-1"}])
    current = population.ledger_path("20260806T000000Z", tmp_path)
    assert population.previous_ledger(current, tmp_path).name == "20260805T000000Z.csv.gz"


def test_previous_ledger_is_none_on_the_first_sweep(tmp_path):
    current = population.ledger_path("20260806T000000Z", tmp_path)
    assert population.previous_ledger(current, tmp_path) is None
    _ledger(current, [{"ticker": "KX-1"}])
    assert population.previous_ledger(current, tmp_path) is None


def test_pruning_keeps_the_most_recent_and_deletes_the_rest(tmp_path):
    stamps = [f"2026080{i}T000000Z" for i in range(1, 7)]
    for stamp in stamps:
        _ledger(population.ledger_path(stamp, tmp_path), [{"ticker": "KX-1"}])
    removed = population.prune_ledgers(tmp_path, keep=3)
    assert len(removed) == 3
    kept = sorted(population.stamp_of(p) for p in tmp_path.glob("*.csv.gz"))
    assert kept == stamps[-3:]


def test_the_ledger_stamp_survives_the_double_extension():
    """Path.stem leaves "...Z.csv" on a .csv.gz name, which will not parse.

    The engine reads the prior sweep's time from the filename, so a stamp that
    silently fails to parse takes the reconciliation down on every sweep.
    """
    path = population.ledger_path("20260805T003519Z", Path("data/monitor/population"))
    assert path.stem.endswith(".csv"), "the trap this guards"
    assert population.stamp_of(path) == "20260805T003519Z"
    assert population.captured_at(path) == datetime(2026, 8, 5, 0, 35, 19, tzinfo=UTC)


# -------------------------------------------------------------------- trend --


def test_trend_records_total_and_two_sided_as_separate_series(tmp_path):
    """Growth in total with a flat two-sided count is a different event."""
    path = tmp_path / "population.jsonl"
    population.record_trend(NOW, 84240, 54074, None, path)
    population.record_trend(NOW + timedelta(hours=1), 90000, 54100, None, path)
    points = population.trend(path)
    assert [p["n_markets"] for p in points] == [84240, 90000]
    assert [p["n_two_sided"] for p in points] == [54074, 54100]
    # The whole reason for two series: one moved 7%, the other 0.05%.
    assert points[1]["n_markets"] > points[0]["n_markets"]
    assert points[1]["n_two_sided"] - points[0]["n_two_sided"] < 100


def test_trend_is_empty_rather_than_zero_before_the_first_sweep(tmp_path):
    assert population.trend(tmp_path / "absent.jsonl") == []


def test_trend_carries_the_residual_when_a_reconciliation_ran(tmp_path):
    path = tmp_path / "population.jsonl"
    prior = _ledger(tmp_path / "a.csv.gz", [{"ticker": "GONE-1"}])
    current = _ledger(tmp_path / "b.csv.gz", [{"ticker": "NEW-1"}])
    r = population.reconcile(prior, current, BOUNDARY, NOW)
    population.record_trend(NOW, 84240, 54074, r, path)
    point = population.trend(path)[0]
    assert point["unattributed"] == r.unattributed
    assert point["added"] == 1 and point["removed"] == 1


def test_trend_omits_reconciliation_keys_rather_than_zeroing_them(tmp_path):
    """First sweep has no prior. Zero added would be a measurement it never made."""
    path = tmp_path / "population.jsonl"
    population.record_trend(NOW, 84240, 54074, None, path)
    point = json.loads(path.read_text().strip())
    assert "added" not in point and "unattributed" not in point


# -------------------------------------------------------- horizon buckets --


@pytest.mark.parametrize(
    "hours,expected",
    [
        (0.5, "<24h"),
        (23.9, "<24h"),
        (24.0, "1-7d"),
        (24 * 6, "1-7d"),
        (24 * 7, "7-30d"),
        (24 * 29, "7-30d"),
        (24 * 30, "30d-1y"),
        (24 * 364, "30d-1y"),
        (24 * 365, ">1y"),
        (24 * 3000, ">1y"),
    ],
)
def test_horizon_buckets_have_no_gaps_or_overlaps(hours, expected):
    """A market lands in exactly one bucket, and the boundaries do not double-count."""
    close = (NOW + timedelta(hours=hours)).isoformat()
    assert population.horizon_bucket(close, NOW) == expected


def test_a_market_without_a_close_time_is_unknown_rather_than_bucketed():
    assert population.horizon_bucket(None, NOW) == "unknown"
    assert population.horizon_bucket("", NOW) == "unknown"
    assert population.horizon_bucket("not-a-date", NOW) == "unknown"


def test_all_buckets_are_present_at_zero_so_a_missing_one_is_never_inferred():
    empty = population.empty_horizons()
    assert set(empty) == {name for name, _ in population.HORIZON_BUCKETS} | {"unknown"}
    assert all(v == 0 for v in empty.values())


def test_growth_in_short_and_long_buckets_is_distinguishable(tmp_path):
    """The decisive distinction: cadence plateaus, expansion compounds.

    Measured on the real 77,047 -> 84,625 move, 97.1% of net growth was
    sub-7-day. This asserts the machinery can tell the two apart at all.
    """
    prior = _ledger(tmp_path / "a.csv.gz", [
        {"ticker": "OLD-1", "close_time": (NOW + timedelta(days=200)).isoformat()},
    ])
    current = _ledger(tmp_path / "b.csv.gz", [
        {"ticker": "OLD-1", "close_time": (NOW + timedelta(days=200)).isoformat()},
        # Three daily markets and one long-dated one.
        *[{"ticker": f"DAILY-{i}", "created_time": "2026-08-04T22:00:00Z",
           "close_time": (NOW + timedelta(hours=6)).isoformat()} for i in range(3)],
        {"ticker": "LONG-1", "created_time": "2026-08-04T22:00:00Z",
         "close_time": (NOW + timedelta(days=400)).isoformat()},
    ])
    r = population.reconcile(prior, current, BOUNDARY, NOW)

    assert r.added == 4
    assert r.added_horizons["<24h"] == 3
    assert r.added_horizons[">1y"] == 1
    assert r.added_short_dated_pct == 75.0
    # The whole population is a different shape from what was added.
    assert r.horizons["30d-1y"] == 1 and r.horizons["<24h"] == 3


def test_horizons_are_recorded_per_sweep_so_net_change_is_answerable(tmp_path):
    """One count cannot separate cadence from expansion; the archive can."""
    path = tmp_path / "population.jsonl"
    prior = _ledger(tmp_path / "a.csv.gz", [{"ticker": "GONE-1"}])
    current = _ledger(tmp_path / "b.csv.gz", [
        {"ticker": "NEW-1", "created_time": "2026-08-04T22:00:00Z",
         "close_time": (NOW + timedelta(hours=2)).isoformat()},
    ])
    r = population.reconcile(prior, current, BOUNDARY, NOW)
    population.record_trend(NOW, 84240, 54074, r, path)
    point = population.trend(path)[0]
    assert point["horizons"]["<24h"] == 1
    assert point["added_horizons"]["<24h"] == 1


# ------------------------------------------------------ provisional threshold --


def test_the_threshold_is_recorded_as_provisional_with_its_reason():
    """Its original anchor was retracted, and the record has to say so."""
    review = population.threshold_review()
    assert review["status"] == "provisional"
    assert review["value"] == population.UNATTRIBUTED_ALERT_THRESHOLD
    assert "retracted" in review["reason"]
    assert "Do not re-derive" in review["reason"]


def test_the_threshold_review_is_a_dated_item_not_an_intention():
    review = population.threshold_review()
    due = datetime.fromisoformat(review["review_due"]).replace(tzinfo=UTC)
    set_on = datetime.fromisoformat(review["set_on"]).replace(tzinfo=UTC)
    assert (due - set_on).days >= 8 * 7, "eight weeks of residual-rate data"
    assert isinstance(review["days_until_due"], int)
    assert review["due"] == (review["days_until_due"] <= 0)


def test_the_reconciliation_payload_carries_the_provisional_marker(tmp_path):
    """A threshold shown without its status reads as settled."""
    prior = _ledger(tmp_path / "a.csv.gz", [{"ticker": "GONE-1"}])
    current = _ledger(tmp_path / "b.csv.gz", [{"ticker": "NEW-1"}])
    payload = population.reconcile(prior, current, BOUNDARY, NOW).as_dict()
    assert payload["threshold_status"] == "provisional"


def test_the_retracted_anchor_is_not_used_to_justify_the_threshold():
    """The 66 was a classifier artifact. It must not read as evidence.

    Quoting the retracted claim in order to retract it is not asserting it, so
    the check is that the retraction follows the quote -- banning the phrase
    outright would fail on the very sentence that withdraws it.
    """
    source = Path("monitor/population.py").read_text()
    block = source.split("UNATTRIBUTED_ALERT_THRESHOLD = ")[0]
    assert "classifier artifact" in block

    quote = block.index("would still have fired on the 66")
    retraction = block.index("That anchor is gone")
    assert retraction > quote, "the claim must be withdrawn where it is quoted"
    assert block.index("single clean observation") > retraction


def test_the_dashboard_reads_the_residual_and_not_the_delta():
    js = Path("dashboard/static/app.js").read_text()
    assert "renderPopulation" in js
    assert "unattributed" in js and "Unattributed residual" in js
    assert "n_two_sided" in js, "the two-sided series must be drawn separately"
