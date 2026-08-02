# Reference venues — Binance and Coinbase

**Reference data only. We do not trade here.** No authenticated adapter exists
in this repo and none should be built. Everything below uses public,
unauthenticated endpoints, confirmed by probe on 2026-08-02.

Purpose: a spot mid for BTC / ETH / SOL, once per second, as an input to
Detector 6 (cross-venue reference divergence), which is a research signal and
not an arbitrage. See the warning in the Phase 2 spec.

## Binance — geo-blocked from this environment

`api.binance.com` returns **HTTP 451** from here:

> Service unavailable from a restricted location according to 'b. Eligibility'
> in https://www.binance.com/en/terms.

Two working alternatives, both unauthenticated:

| Host | Status | Notes |
| --- | --- | --- |
| `https://api.binance.com` | **451** | Blocked. Do not build against it |
| `https://data-api.binance.vision` | 200 | Binance's public market-data mirror. Read-only by design, no account, no key |
| `https://api.binance.us` | 200 | Binance.US — a **different exchange with a different order book**. Prices differ from Binance global |

Use `data-api.binance.vision` for the Binance reference series. It serves the
same `/api/v3/*` market-data routes:

```
GET /api/v3/ticker/bookTicker?symbol=BTCUSDT   -> {"bidPrice": "...", "askPrice": "..."}
GET /api/v3/depth?symbol=BTCUSDT&limit=5
```

Symbols: `BTCUSDT`, `ETHUSDT`, `SOLUSDT`.

If the mirror is ever unavailable, `api.binance.us` is a fallback **only if the
record says so** — Binance.US is a separate venue and mixing the two inside one
time series would silently corrupt the divergence study. Store the source host
on every tick.

## Coinbase — works, no key needed

| Host | Status | Notes |
| --- | --- | --- |
| `https://api.exchange.coinbase.com` | 200 | Coinbase Exchange public API |
| `https://api.coinbase.com/api/v3/brokerage/market/*` | 200 | Advanced Trade public market data |

```
GET https://api.exchange.coinbase.com/products/BTC-USD/ticker   -> {"bid": "...", "ask": "...", "price": "..."}
GET https://api.exchange.coinbase.com/products/BTC-USD/book?level=2
```

Products: `BTC-USD`, `ETH-USD`, `SOL-USD`. WebSocket at
`wss://ws-feed.exchange.coinbase.com`, public channels need no auth — prefer it
over 1 Hz polling once the pipeline is real.

## The reference these markets actually settle against

Kalshi's crypto series do **not** settle to Binance or Coinbase. They settle to
the CF Benchmarks Real-Time Index (BRTI for BTC), specifically the average of 60
index prints in the final minute before expiry. Kalshi streams those values on
the authenticated `cfbenchmarks_value` WebSocket channel.

So there are two different comparisons, and they must not be conflated:

- **Kalshi vs CF Benchmarks index** — same underlying, and a divergence here is
  closer to a real pricing question.
- **Kalshi vs Binance/Coinbase spot** — different underlying. Part of any
  observed divergence is basis between the index and a single venue's last
  trade, not mispricing.

Detector 6 should log both, labelled separately.

## Recording rules

- Timestamp every tick with local receive time *and* the venue timestamp.
- Store the raw response body. If this data is wrong, every conclusion
  downstream is fiction.
- Record the source host per tick; never silently fail over between hosts.
- No API keys. If a change ever requires one, that is a design change to
  escalate, not a config edit.
