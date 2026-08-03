"""Monitor correctness tests.

The fixtures are the Phase 0.5 false-positive history. Every one of them
produced a confident wrong answer during the investigation, and every one must
be rejected by this suite. If a change to the monitor lets one through, the
change is wrong -- see docs/NEGATIVE_RESULT.md -> "Methodological errors caught".
"""

from __future__ import annotations

import json
import re
from decimal import Decimal as D
from pathlib import Path

import pytest

from monitor import alerts, collect, metrics
from monitor.checks import (
    annualized_return,
    is_rfq_shell,
    persisted,
    same_ladder,
    tradeable_size,
    underlying_shape,
    verify_partition,
)

BASELINE = Path("monitor/baseline.json")
SNAPSHOT = Path("monitor/snapshots/20260803T070632Z_t0")
SNAPSHOT_T1 = Path("monitor/snapshots/20260803T071019Z_t1")


# --------------------------------------------------------------------------
# Fixture 1: KXDEELRIP-40. Flagged mutually exclusive by the venue, and not
# exhaustive -- if neither company IPOs, both legs resolve NO.
# --------------------------------------------------------------------------
KXDEELRIP = [
    {"strike_type": "custom", "yes_sub_title": "Deel"},
    {"strike_type": "custom", "yes_sub_title": "Rippling"},
]

# Fixture 2: listed-subset market. Two named candidates out of an open field.
LA01_PRIMARY = [
    {"strike_type": "custom", "yes_sub_title": "S. Scalise"},
    {"strike_type": "custom", "yes_sub_title": "R. Arroyo"},
]

# Fixture 3: nested cumulative horizons. Sum to 22c; read as a partition they
# annualize at 884%.
KXGREENLAND = [
    {"strike_type": None, "yes_sub_title": "Before 2027"},
    {"strike_type": None, "yes_sub_title": "Before January 20, 2029"},
]

# Fixture 4: sports spread event listing BOTH competitors at the same strike.
SAME_STRIKE_BOTH_SIDES = [
    {"floor_strike": "3.5", "yes_sub_title": "Toronto -3.5 games"},
    {"floor_strike": "3.5", "yes_sub_title": "Golden State -3.5 games"},
]

# Fixture 5: both competitors at DIFFERENT strikes -- defeats a duplicate-strike
# filter alone, which is how 350 artifacts survived the first fix.
DIFFERENT_STRIKE_BOTH_SIDES = [
    {"floor_strike": "1.5", "yes_sub_title": "Duncan Chan -1.5 games"},
    {"floor_strike": "3.5", "yes_sub_title": "Thiago Agustin Tirante -3.5 games"},
]

# Genuine single-underlying ladder: same subject, ascending strikes.
REAL_LADDER = [
    {"floor_strike": "2.75", "yes_sub_title": "Above 2.75%"},
    {"floor_strike": "3.0", "yes_sub_title": "Above 3.00%"},
]

# Genuine partitions. BTC tiles at 0.01; US GDP at 0.1 percentage points.
BTC_PARTITION = [
    {"strike_type": "less", "cap_strike": "20000", "yes_sub_title": "19,999.99 or below"},
    {"strike_type": "between", "floor_strike": "20000", "cap_strike": "24999.99",
     "yes_sub_title": "20,000 to 24,999.99"},
    {"strike_type": "between", "floor_strike": "25000", "cap_strike": "29999.99",
     "yes_sub_title": "25,000 to 29,999.99"},
    {"strike_type": "greater", "floor_strike": "29999.99", "yes_sub_title": "30,000 or above"},
]
GDP_PARTITION = [
    {"strike_type": "less", "cap_strike": "0.1", "yes_sub_title": "0.0% or Below"},
    {"strike_type": "between", "floor_strike": "0.1", "cap_strike": "0.5",
     "yes_sub_title": "0.1% to 0.5%"},
    {"strike_type": "between", "floor_strike": "0.6", "cap_strike": "0.9",
     "yes_sub_title": "0.6% to 0.9%"},
    {"strike_type": "greater", "floor_strike": "0.9", "yes_sub_title": "Above 0.9%"},
]


@pytest.mark.parametrize(
    "name,legs",
    [
        ("KXDEELRIP-40 mutually exclusive but not exhaustive", KXDEELRIP),
        ("LA-01 listed subset", LA01_PRIMARY),
        ("KXGREENLAND nested cumulative horizons", KXGREENLAND),
    ],
)
def test_partition_resemblance_is_rejected(name, legs):
    result = verify_partition(legs)
    assert not result, f"{name} must not verify as a partition"
    assert result.reason


def test_real_partitions_are_accepted_at_their_own_granularity():
    btc = verify_partition(BTC_PARTITION)
    assert btc and btc.granularity == D("0.01")
    gdp = verify_partition(GDP_PARTITION)
    assert gdp and gdp.granularity == D("0.1"), "GDP tiles at 0.1pp, not 0.01"


def test_partition_with_a_real_hole_is_rejected():
    holed = [dict(m) for m in BTC_PARTITION]
    holed[2]["floor_strike"] = "25000.05"  # one bucket starts late
    assert not verify_partition(holed)


def test_overlapping_buckets_are_rejected():
    overlap = [dict(m) for m in BTC_PARTITION]
    overlap[2]["floor_strike"] = "24999.98"
    assert not verify_partition(overlap)


@pytest.mark.parametrize(
    "name,legs",
    [
        ("same strike, both competitors", SAME_STRIKE_BOTH_SIDES),
        ("different strikes, both competitors", DIFFERENT_STRIKE_BOTH_SIDES),
    ],
)
def test_sports_ladder_artifacts_are_rejected(name, legs):
    assert not same_ladder(legs), f"{name} is not a single-underlying ladder"


def test_genuine_ladder_is_accepted():
    assert same_ladder(REAL_LADDER)


def test_underlying_shape_strips_numbers_only():
    assert underlying_shape("Above 2.75%") == underlying_shape("Above 3.00%")
    assert underlying_shape("Duncan Chan -1.5 games") != underlying_shape("Tirante -3.5 games")


def test_fractional_size_is_not_liquidity():
    """KXGDPYEAR-33 quoted a 9c credit on 0.01 contracts: $0.0009 of capacity."""
    assert tradeable_size("0.01") == 0
    assert tradeable_size("0.99") == 0
    assert tradeable_size("1") == 1
    assert tradeable_size("10.00") == 10
    assert tradeable_size(None) == 0


def test_annualized_return_is_suppressed_for_unverified_structures():
    """The nested pair annualizes at 884% if you let it. Never compute it."""
    assert annualized_return(D(78), D(22), D("0.41"), verified=False) is None
    assert annualized_return(D(5), D(95), D("3.57"), verified=True) is not None


def test_decimal_comparison_catches_float_equality_phantoms():
    """0.1 + 0.2 != 0.3 in float; as Decimal strings the prices are equal."""
    assert persisted("0.30", "0.30")
    assert not persisted("0.30", "0.31")
    assert persisted(D("64.00"), "64.0")


def test_rfq_shells_are_identified():
    assert is_rfq_shell("KXMVENFLSINGLEGAME-26AUG06CARARI-ABC")
    assert not is_rfq_shell("KXGDPYEAR-29-B2.3")


# --------------------------------------------------------------------------
# Hard constraint: this package never places orders and never authenticates.
# --------------------------------------------------------------------------
FORBIDDEN = re.compile(
    r"portfolio/orders|/orders\b|client_order_id|KALSHI-ACCESS|private_key|"
    r"RSA|sign(ature)?\(|api_key",
    re.IGNORECASE,
)


def test_monitor_package_contains_no_order_or_auth_code():
    for path in sorted(Path("monitor").rglob("*.py")):
        text = path.read_text()
        hit = FORBIDDEN.search(text)
        assert hit is None, f"{path} references {hit.group(0)!r}; monitor must stay read-only"


def test_monitor_only_talks_to_public_endpoints():
    text = (Path("monitor") / "collect.py").read_text()
    urls = re.findall(r"https?://[^\s\"']+", text)
    assert urls, "expected at least one endpoint"
    assert all(u.startswith("https://api.elections.kalshi.com/trade-api/v2") for u in urls), urls


# --------------------------------------------------------------------------
# Gate: the monitor must reproduce the committed baseline byte-for-byte.
# --------------------------------------------------------------------------
@pytest.mark.skipif(not SNAPSHOT.exists(), reason="baseline snapshot not present")
def test_baseline_reproduces_byte_for_byte_from_the_committed_snapshot():
    rows = collect.read_snapshot(SNAPSHOT)
    recomputed = json.dumps(metrics.compute(rows), indent=2, sort_keys=True) + "\n"
    committed = BASELINE.read_text()

    # Annualized return depends on time-to-close, which moves with the clock.
    # Everything else must match exactly.
    strip = re.compile(r'^\s*"annualized_pct": .*$\n?', re.MULTILINE)
    assert strip.sub("", recomputed) == strip.sub("", committed), (
        "monitor cannot reproduce its own baseline; it therefore cannot detect change"
    )


@pytest.mark.skipif(not SNAPSHOT.exists(), reason="baseline snapshot not present")
def test_baseline_snapshot_produces_no_alerts():
    rows = collect.read_snapshot(SNAPSHOT)
    baseline = json.loads(BASELINE.read_text())
    assert alerts.evaluate(baseline, metrics.compute(rows)) == []


@pytest.mark.skipif(not SNAPSHOT_T1.exists(), reason="second snapshot not present")
def test_second_snapshot_also_produces_no_alerts():
    """Persistence: an artifact present in one snapshot must not page anyone."""
    rows = collect.read_snapshot(SNAPSHOT_T1)
    baseline = json.loads(BASELINE.read_text())
    assert alerts.evaluate(baseline, metrics.compute(rows)) == []


def test_alert_body_carries_the_do_not_trade_line():
    fired = alerts.evaluate(
        {"fee_free": {"n_series_with_open_markets": 11, "series": []}},
        {"fee_free": {"n_series_with_open_markets": 40, "series": ["NEW"]}},
    )
    assert fired
    assert all(alerts.FOOTER in a.render() for a in fired)
    assert all("NEGATIVE_RESULT.md" in a.render() for a in fired)


def test_maker_rebate_or_unknown_fee_type_alerts():
    fired = alerts.evaluate({}, {"fee_types": {"quadratic_with_maker_rebates": 5}})
    assert any("maker rebate" in a.trigger for a in fired)
