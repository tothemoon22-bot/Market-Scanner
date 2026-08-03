"""Kalshi trading fee model.

Every number in this module traces to a primary source. See
``docs/venues/kalshi/fees.md`` for the evidence trail and
``docs/COST_MODEL.md`` for the derived thresholds.

The published formula (KalshiEX LLC Exchange Fee Schedule, filed with the CFTC
2022-09-12 and unchanged in structure since) is::

    fees = round up(0.07 x C x P x (1-P))

    P = the price of a contract in dollars (50 cents is 0.5)
    C = the number of contracts being traded
    round up = rounds to the next cent

Three facts about that formula drive everything downstream:

1. The ceiling is applied to the *whole trade*, not per contract. Small orders
   therefore pay a rounding surcharge of up to $0.0099 per fill.
2. Fees are assessed per fill, not per order. A 10-lot that fills in four
   pieces pays the ceiling four times. Confirmed by the WebSocket ``fill``
   message, which carries ``taker_fees_dollars`` / ``maker_fees_dollars`` on
   each individual fill.
3. A multi-leg basket pays the ceiling once per leg. An N-leg basket eats up to
   N x $0.0099 of pure rounding loss on top of the arithmetic fee.

All arithmetic is Decimal. Never introduce a float into this module: a float
error of one ULP in the wrong direction turns a losing basket into a winning
one on paper.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal
from enum import StrEnum
from typing import Any

__all__ = [
    "FeeType",
    "Role",
    "FeeModel",
    "TAKER_COEFFICIENT",
    "MAKER_COEFFICIENT",
    "FLAT_COEFFICIENT",
    "trading_fee",
    "basket_fee",
]

# --- Published coefficients -------------------------------------------------
# Source: https://kalshi.com/docs/kalshi-fee-schedule.pdf (General Trading Fees
# Table / Maker Fees section). The 0.07 taker coefficient is verbatim from the
# CFTC filing vendored at spec/cftc-fee-schedule-2022-09-12.pdf; the 0.0175
# maker coefficient is quoted consistently by every secondary source that
# reproduces the current schedule and equals exactly 25% of the taker rate.
TAKER_COEFFICIENT = Decimal("0.07")
MAKER_COEFFICIENT = Decimal("0.0175")

# The "Specific Trading Fees Table" (fee_type == "flat"). In the 2022 schedule
# this table covered S&P 500 (INX*) and Nasdaq-100 markets at half the general
# rate. We have NOT confirmed the 2026 value, and no live series currently
# reports fee_type == "flat", so this coefficient is marked unverified and
# `FeeModel.is_verified` is False for it. Detectors must refuse to act on an
# unverified fee model rather than guess.
FLAT_COEFFICIENT = Decimal("0.035")

ONE_CENT = Decimal("0.01")


class FeeType(StrEnum):
    """Mirrors the ``FeeType`` enum in Kalshi's OpenAPI spec.

    A series object carries this on the ``fee_type`` field. It is the only
    reliable way to know whether a given series charges maker fees --- third
    party summaries of the fee schedule get this wrong routinely.
    """

    QUADRATIC = "quadratic"
    QUADRATIC_WITH_MAKER_FEES = "quadratic_with_maker_fees"
    FLAT = "flat"


class Role(StrEnum):
    """Which side of the match we were on. Determines the coefficient."""

    TAKER = "taker"
    MAKER = "maker"


def _to_decimal(value: Any, name: str) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        return Decimal(value)
    raise TypeError(
        f"{name} must be Decimal, int or str, got {type(value).__name__}. "
        "Floats are rejected on purpose: this module must not be able to "
        "round a losing trade into a winning one."
    )


@dataclass(frozen=True)
class FeeModel:
    """The fee structure of a single Kalshi series.

    Build one per series from the ``/series/{ticker}`` response and refresh it
    whenever ``/series/fee_changes`` reports a scheduled change.
    """

    fee_type: FeeType
    fee_multiplier: Decimal = Decimal(1)

    @classmethod
    def from_series(cls, series: Mapping[str, Any]) -> FeeModel:
        """Build from a Kalshi series object (or the ``series`` sub-object)."""
        body = series.get("series", series)
        try:
            raw_type = body["fee_type"]
            raw_multiplier = body["fee_multiplier"]
        except KeyError as exc:  # pragma: no cover - defensive
            raise KeyError(
                f"series object is missing {exc}; refusing to assume a fee model"
            ) from exc
        return cls(
            fee_type=FeeType(raw_type),
            fee_multiplier=Decimal(str(raw_multiplier)),
        )

    @property
    def charges_maker_fees(self) -> bool:
        return self.fee_type is FeeType.QUADRATIC_WITH_MAKER_FEES

    @property
    def is_fee_free(self) -> bool:
        """True for series Kalshi has zeroed out via ``fee_multiplier: 0``."""
        return self.fee_multiplier == 0

    @property
    def is_verified(self) -> bool:
        """False when we cannot vouch for the coefficient from a primary source.

        Detectors must treat an unverified model as ineligible rather than
        trading on a guessed fee.
        """
        return self.fee_type is not FeeType.FLAT

    def coefficient(self, role: Role) -> Decimal:
        """Fee coefficient for `role`, before the series multiplier."""
        if self.fee_type is FeeType.FLAT:
            # The Specific Trading Fees Table has no published maker component.
            return FLAT_COEFFICIENT if role is Role.TAKER else Decimal(0)
        if role is Role.MAKER:
            return MAKER_COEFFICIENT if self.charges_maker_fees else Decimal(0)
        return TAKER_COEFFICIENT

    def fee(self, contracts: Any, price: Any, role: Role = Role.TAKER) -> Decimal:
        """Fee in dollars for a single fill of `contracts` at `price`.

        `price` is in dollars (0.5 == 50 cents), matching the published formula.
        The result is rounded up to the next cent, exactly as the schedule
        specifies, and the ceiling applies to the whole fill.
        """
        count = _to_decimal(contracts, "contracts")
        p = _to_decimal(price, "price")
        if count < 0:
            raise ValueError("contracts must be non-negative")
        if not (Decimal(0) <= p <= Decimal(1)):
            raise ValueError(f"price must be within [0, 1] dollars, got {p}")

        raw = self.coefficient(role) * self.fee_multiplier * count * p * (Decimal(1) - p)
        if raw == 0:
            return Decimal("0.00")
        return raw.quantize(ONE_CENT, rounding=ROUND_CEILING)

    def fee_cents_per_contract(
        self, contracts: Any, price: Any, role: Role = Role.TAKER
    ) -> Decimal:
        """Fee expressed in cents per contract, including the rounding surcharge.

        This is the unit detectors compare edges in. It is deliberately *not*
        the smooth 1.75-cents-at-50-cents figure: on small orders the ceiling
        makes the realised per-contract cost strictly higher.
        """
        count = _to_decimal(contracts, "contracts")
        if count == 0:
            raise ValueError("contracts must be positive to express a per-contract fee")
        return self.fee(count, price, role) * 100 / count

    def round_trip_fee(
        self,
        contracts: Any,
        entry_price: Any,
        exit_price: Any | None = None,
        entry_role: Role = Role.TAKER,
        exit_role: Role = Role.TAKER,
    ) -> Decimal:
        """Fee in dollars to open and then close a position by trading out.

        Pass `exit_price=None` for a position held to settlement: Kalshi charges
        no settlement fee, so the round trip collapses to the entry fee alone.
        That is the relevant cost for a no-arbitrage basket, which is opened and
        then left to settle.
        """
        total = self.fee(contracts, entry_price, entry_role)
        if exit_price is not None:
            total += self.fee(contracts, exit_price, exit_role)
        return total


def trading_fee(
    contracts: Any,
    price: Any,
    role: Role = Role.TAKER,
    fee_type: FeeType | str = FeeType.QUADRATIC,
    fee_multiplier: Any = 1,
) -> Decimal:
    """Convenience wrapper around :meth:`FeeModel.fee`."""
    model = FeeModel(
        fee_type=FeeType(fee_type),
        fee_multiplier=_to_decimal(fee_multiplier, "fee_multiplier"),
    )
    return model.fee(contracts, price, role)


def basket_fee(legs: Iterable[tuple[FeeModel, Any, Any, Role]]) -> Decimal:
    """Total fee in dollars for a multi-leg basket.

    Each leg is ``(model, contracts, price, role)``. The per-leg ceiling is
    applied independently and only then summed --- an N-leg basket rounds up N
    times. Do not simplify this into a single ceiling over the sum; that would
    understate the cost, which is the direction that loses money.
    """
    total = Decimal("0.00")
    for model, contracts, price, role in legs:
        total += model.fee(contracts, price, role)
    return total
