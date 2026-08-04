"""Reference spot feeds. Public, unauthenticated, and never traded on.

Binance's main API geo-blocks US traffic with HTTP 451, and this host is in the
US, which is what Kalshi requires. ``data-api.binance.vision`` is Binance's
public market-data mirror and is used instead.

**There is no failover to Binance.US.** It is a different exchange with a
different order book; silently substituting it would corrupt the series while
looking healthy. When the mirror fails, the source is marked failed, the error
is surfaced on the health panel, and the price goes to NO DATA.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import httpx

D = Decimal

BINANCE = "https://data-api.binance.vision/api/v3/ticker/bookTicker"
COINBASE = "https://api.exchange.coinbase.com/products/{product}/ticker"

BINANCE_SYMBOLS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT"}
COINBASE_PRODUCTS = {"BTC": "BTC-USD", "ETH": "ETH-USD", "SOL": "SOL-USD"}


def _mid(bid: str, ask: str) -> str:
    return str(((D(bid) + D(ask)) / 2).quantize(D("0.01")))


async def fetch_binance(client: httpx.AsyncClient) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for asset, symbol in BINANCE_SYMBOLS.items():
        resp = await client.get(BINANCE, params={"symbol": symbol}, timeout=10.0)
        resp.raise_for_status()
        body = resp.json()
        out[asset] = {
            "bid": body["bidPrice"],
            "ask": body["askPrice"],
            "mid": _mid(body["bidPrice"], body["askPrice"]),
            "source_host": "data-api.binance.vision",
        }
    return out


async def fetch_coinbase(client: httpx.AsyncClient) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for asset, product in COINBASE_PRODUCTS.items():
        resp = await client.get(COINBASE.format(product=product), timeout=10.0)
        resp.raise_for_status()
        body = resp.json()
        out[asset] = {
            "bid": body["bid"],
            "ask": body["ask"],
            "mid": _mid(body["bid"], body["ask"]),
            "source_host": "api.exchange.coinbase.com",
        }
    return out
