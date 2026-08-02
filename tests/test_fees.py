"""Unit tests for the Kalshi fee model.

The tables below are transcribed from the published Kalshi Exchange Fee
Schedule as filed with the CFTC (vendored at
``docs/venues/kalshi/spec/cftc-fee-schedule-2022-09-12.pdf``). They are the
acceptance criteria for this module: if a change to ``fees.py`` breaks these,
the change is wrong, not the tables.
"""

from decimal import Decimal

import pytest

from src.venues.kalshi.fees import (
    FeeModel,
    FeeType,
    Role,
    basket_fee,
    trading_fee,
)

D = Decimal

# "General Trading Fees Table" --- price -> (fee for 1 contract, fee for 100)
GENERAL_TABLE = {
    "0.01": ("0.01", "0.07"),
    "0.05": ("0.01", "0.34"),
    "0.10": ("0.01", "0.63"),
    "0.15": ("0.01", "0.90"),
    "0.20": ("0.02", "1.12"),
    "0.25": ("0.02", "1.32"),
    "0.30": ("0.02", "1.47"),
    "0.35": ("0.02", "1.60"),
    "0.40": ("0.02", "1.68"),
    "0.45": ("0.02", "1.74"),
    "0.50": ("0.02", "1.75"),
    "0.55": ("0.02", "1.74"),
    "0.60": ("0.02", "1.68"),
    "0.65": ("0.02", "1.60"),
    "0.70": ("0.02", "1.47"),
    "0.75": ("0.02", "1.32"),
    "0.80": ("0.02", "1.12"),
    "0.85": ("0.01", "0.90"),
    "0.90": ("0.01", "0.63"),
    "0.95": ("0.01", "0.34"),
    "0.99": ("0.01", "0.07"),
}

# "Specific Trading Fees Table" (S&P 500 / Nasdaq-100 in the 2022 schedule),
# which the OpenAPI spec maps to fee_type == "flat".
FLAT_TABLE_100 = {
    "0.01": "0.04",
    "0.05": "0.17",
    "0.10": "0.32",
    "0.15": "0.45",
    "0.20": "0.56",
    "0.25": "0.66",
    "0.30": "0.74",
    "0.35": "0.80",
    "0.40": "0.84",
    "0.45": "0.87",
    "0.50": "0.88",
    "0.55": "0.87",
    "0.60": "0.84",
    "0.65": "0.80",
    "0.70": "0.74",
    "0.75": "0.66",
    "0.80": "0.56",
    "0.85": "0.45",
    "0.90": "0.32",
    "0.95": "0.17",
    "0.99": "0.04",
}

QUADRATIC = FeeModel(FeeType.QUADRATIC)
WITH_MAKER = FeeModel(FeeType.QUADRATIC_WITH_MAKER_FEES)
FLAT = FeeModel(FeeType.FLAT)
FEE_FREE = FeeModel(FeeType.QUADRATIC, Decimal(0))


@pytest.mark.parametrize("price,expected", [(p, v[1]) for p, v in GENERAL_TABLE.items()])
def test_taker_fee_matches_published_table_100_contracts(price, expected):
    assert QUADRATIC.fee(100, price) == D(expected)


@pytest.mark.parametrize("price,expected", [(p, v[0]) for p, v in GENERAL_TABLE.items()])
def test_taker_fee_matches_published_table_1_contract(price, expected):
    assert QUADRATIC.fee(1, price) == D(expected)


@pytest.mark.parametrize("price,expected", list(FLAT_TABLE_100.items()))
def test_flat_fee_matches_specific_table_100_contracts(price, expected):
    assert FLAT.fee(100, price) == D(expected)


def test_maker_fee_is_zero_on_plain_quadratic_series():
    """Resting orders are free on the ~99% of series without maker fees.

    "Trading fees are not charged for orders placed that are not immediately
    matched and are instead left as resting orders on the orderbook."
    """
    assert QUADRATIC.fee(100, "0.50", Role.MAKER) == D("0.00")
    assert not QUADRATIC.charges_maker_fees


def test_maker_fee_is_one_quarter_of_taker_where_charged():
    # 0.0175 * 100 * 0.5 * 0.5 = 0.4375 -> 0.44
    assert WITH_MAKER.fee(100, "0.50", Role.MAKER) == D("0.44")
    assert WITH_MAKER.fee(100, "0.50", Role.TAKER) == D("1.75")
    assert WITH_MAKER.charges_maker_fees


def test_fee_multiplier_zero_means_no_fee():
    assert FEE_FREE.fee(100, "0.50") == D("0.00")
    assert FEE_FREE.is_fee_free


def test_multiplier_scales_the_fee_before_rounding():
    # 0.07 * 0.5 * 200 * 0.5 * 0.5 = 1.75
    half = FeeModel(FeeType.QUADRATIC, D("0.5"))
    assert half.fee(200, "0.50") == D("1.75")


def test_ceiling_applies_to_the_whole_fill_not_per_contract():
    """A 1-lot at 50c costs 2 cents, not 1.75 --- a 14% rounding surcharge."""
    assert QUADRATIC.fee(1, "0.50") == D("0.02")
    assert QUADRATIC.fee_cents_per_contract(1, "0.50") == D("2")
    # The surcharge shrinks as size grows.
    assert QUADRATIC.fee_cents_per_contract(1000, "0.50") == D("1.75")


def test_fee_is_symmetric_about_fifty_cents():
    for cents in range(1, 50):
        low = D(cents) / 100
        high = D(1) - low
        assert QUADRATIC.fee(100, low) == QUADRATIC.fee(100, high)


def test_fee_peaks_at_fifty_cents():
    fees = [QUADRATIC.fee(1000, D(c) / 100) for c in range(1, 100)]
    assert max(fees) == QUADRATIC.fee(1000, "0.50")


def test_round_trip_to_settlement_charges_entry_only():
    """No settlement fee, so a held-to-expiry basket pays once."""
    assert QUADRATIC.round_trip_fee(100, "0.50") == D("1.75")


def test_round_trip_by_trading_out_charges_twice():
    assert QUADRATIC.round_trip_fee(100, "0.50", exit_price="0.50") == D("3.50")


def test_basket_rounds_up_once_per_leg():
    """The per-leg ceiling costs more than one ceiling over the same size.

    Four 1-lots at 50c cost 4 x $0.02 = $0.08, where a single 4-lot costs
    $0.07. At 10 contracts a leg the gap is $0.72 against $0.70. The penalty is
    small in absolute terms and large relative to a 1-2 cent edge.
    """
    assert basket_fee([(QUADRATIC, 1, "0.50", Role.TAKER)] * 4) == D("0.08")
    assert QUADRATIC.fee(4, "0.50") == D("0.07")

    assert basket_fee([(QUADRATIC, 10, "0.50", Role.TAKER)] * 4) == D("0.72")
    assert QUADRATIC.fee(40, "0.50") == D("0.70")


def test_from_series_reads_the_live_api_shape():
    payload = {
        "series": {
            "ticker": "KXBTCD",
            "fee_type": "quadratic",
            "fee_multiplier": 1,
        }
    }
    model = FeeModel.from_series(payload)
    assert model.fee_type is FeeType.QUADRATIC
    assert model.fee_multiplier == D(1)
    assert model.is_verified


def test_from_series_refuses_to_guess_a_missing_fee_model():
    with pytest.raises(KeyError):
        FeeModel.from_series({"series": {"ticker": "KXNOFEEFIELD"}})


def test_flat_fee_type_is_flagged_unverified():
    """No live series uses `flat`; we have not confirmed its 2026 coefficient."""
    assert not FLAT.is_verified
    assert QUADRATIC.is_verified and WITH_MAKER.is_verified


def test_floats_are_rejected():
    with pytest.raises(TypeError):
        QUADRATIC.fee(100, 0.5)
    with pytest.raises(TypeError):
        QUADRATIC.fee(100.0, "0.50")


@pytest.mark.parametrize("price", ["-0.01", "1.01"])
def test_price_outside_the_contract_range_is_rejected(price):
    with pytest.raises(ValueError):
        QUADRATIC.fee(100, price)


def test_trading_fee_wrapper_matches_the_model():
    assert trading_fee(100, "0.25") == QUADRATIC.fee(100, "0.25")
    assert trading_fee(100, "0.25", Role.MAKER, "quadratic_with_maker_fees") == D("0.33")
