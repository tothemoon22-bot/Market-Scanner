"""Generates the tables in docs/COST_MODEL.md.

Run with ``python -m src.research.cost_model``. The document is derived from
this script, not hand-typed, so a fee-schedule change propagates by editing
``src/venues/kalshi/fees.py`` and re-running.
"""

from __future__ import annotations

from decimal import Decimal

from src.venues.kalshi.fees import FeeModel, FeeType, Role, basket_fee

D = Decimal
QUADRATIC = FeeModel(FeeType.QUADRATIC)
WITH_MAKER = FeeModel(FeeType.QUADRATIC_WITH_MAKER_FEES)

HEADLINE_PRICES = ["0.25", "0.50", "0.75"]
SIZES = [1, 10, 100, 1000]


def _row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def headline_table() -> str:
    """The Phase 0 deliverable: 100 contracts at 25c, 50c, 75c."""
    lines = [
        _row(
            [
                "Price",
                "Entry fee",
                "Round trip",
                "Entry c/contract",
                "Round trip c/contract",
                "Round trip % of stake",
            ]
        ),
        _row(["---"] * 6),
    ]
    for price in HEADLINE_PRICES:
        entry = QUADRATIC.fee(100, price)
        rt = QUADRATIC.round_trip_fee(100, price, exit_price=price)
        stake = D(price) * 100
        lines.append(
            _row(
                [
                    f"{D(price) * 100:.0f}c",
                    f"${entry}",
                    f"${rt}",
                    f"{entry * 100 / 100:.4f}c",
                    f"{rt * 100 / 100:.4f}c",
                    f"{rt / stake * 100:.2f}%",
                ]
            )
        )
    return "\n".join(lines)


def size_sensitivity_table() -> str:
    """How much the per-fill ceiling costs at the sizes we will actually trade."""
    lines = [
        _row(["Contracts"] + [f"{D(p) * 100:.0f}c" for p in HEADLINE_PRICES]),
        _row(["---"] * (1 + len(HEADLINE_PRICES))),
    ]
    for size in SIZES:
        cells = [str(size)]
        for price in HEADLINE_PRICES:
            cents = QUADRATIC.fee_cents_per_contract(size, price)
            cells.append(f"{cents:.4f}c")
        lines.append(_row(cells))
    return "\n".join(lines)


def complementary_threshold_table() -> str:
    """Detector 1 break-even: how far below 100c the two asks must sum.

    Buy YES at `a` and NO at `1-a`, hold to settlement. Both legs cross the
    spread, so both pay taker fees; settlement is free.
    """
    lines = [
        _row(
            [
                "YES ask",
                "NO ask",
                "Fee (100 lots)",
                "Fee c/contract",
                "Max payable sum",
                "Ticks of edge needed",
            ]
        ),
        _row(["---"] * 6),
    ]
    for cents in [5, 10, 25, 40, 50]:
        a = D(cents) / 100
        b = D(1) - a
        fee = basket_fee(
            [
                (QUADRATIC, 100, a, Role.TAKER),
                (QUADRATIC, 100, b, Role.TAKER),
            ]
        )
        per_contract = fee * 100 / 100
        max_sum = D(100) - per_contract
        # The book quotes in whole cents, so the sum must reach the next tick down.
        ticks = (D(100) - max_sum).to_integral_value(rounding="ROUND_CEILING")
        lines.append(
            _row(
                [
                    f"{cents}c",
                    f"{100 - cents}c",
                    f"${fee}",
                    f"{per_contract:.2f}c",
                    f"{max_sum:.2f}c",
                    f"{ticks:.0f}",
                ]
            )
        )
    return "\n".join(lines)


def basket_threshold_table() -> str:
    """Detector 2 break-even for an N-leg exhaustive basket at 100 lots.

    Legs priced 1/N each, which is the fee-maximising configuration for a
    uniform basket.
    """
    lines = [
        _row(["Legs", "Price per leg", "Fee (100 lots)", "Fee c/contract", "Max payable sum"]),
        _row(["---"] * 5),
    ]
    for n in [2, 3, 4, 5, 10, 20]:
        price = (D(1) / n).quantize(D("0.01"))
        fee = basket_fee([(QUADRATIC, 100, price, Role.TAKER)] * n)
        per_contract = fee * 100 / 100
        lines.append(
            _row(
                [
                    str(n),
                    f"{price * 100:.0f}c",
                    f"${fee}",
                    f"{per_contract:.2f}c",
                    f"{D(100) - per_contract:.2f}c",
                ]
            )
        )
    return "\n".join(lines)


def maker_table() -> str:
    lines = [
        _row(["Price", "Taker (100 lots)", "Maker, quadratic", "Maker, with_maker_fees"]),
        _row(["---"] * 4),
    ]
    for price in HEADLINE_PRICES:
        lines.append(
            _row(
                [
                    f"{D(price) * 100:.0f}c",
                    f"${QUADRATIC.fee(100, price, Role.TAKER)}",
                    f"${QUADRATIC.fee(100, price, Role.MAKER)}",
                    f"${WITH_MAKER.fee(100, price, Role.MAKER)}",
                ]
            )
        )
    return "\n".join(lines)


def main() -> None:
    print("## Headline: 100 contracts\n")
    print(headline_table())
    print("\n## Per-contract cost by order size (taker)\n")
    print(size_sensitivity_table())
    print("\n## Detector 1 break-even\n")
    print(complementary_threshold_table())
    print("\n## Detector 2 break-even\n")
    print(basket_threshold_table())
    print("\n## Maker vs taker\n")
    print(maker_table())


if __name__ == "__main__":
    main()
