# Kalshi — venue notes

Primary venue. Everything here was verified against `spec/openapi.yaml`,
`spec/asyncapi.yaml` (both vendored from `docs.kalshi.com` on 2026-08-02) or by
probing the live API from this environment on the same date. Claims that were
not verified are labelled **UNVERIFIED**.

Fees have their own document: [`fees.md`](fees.md).

## Hosts

| Environment | REST | WebSocket |
| --- | --- | --- |
| Production | `https://external-api.kalshi.com/trade-api/v2` | `wss://external-api-ws.kalshi.com/trade-api/ws/v2` |
| Production (alt, also supported) | `https://api.elections.kalshi.com/trade-api/v2` | — |
| Demo | `https://external-api.demo.kalshi.co/trade-api/v2` | **UNVERIFIED** — the AsyncAPI spec lists no demo server |
| Demo (alt, also supported) | `https://demo-api.kalshi.co/trade-api/v2` | — |

All four REST hosts answered `GET /exchange/status` with 200 from this
environment. The demo WebSocket host has to be confirmed in Phase 4; do not
guess it into a config file before then.

Demo accounts are separate from production accounts and need their own API key,
created at the demo site. Demo balances are play money.

## Authentication

RSA-PSS request signing. Three headers:

| Header | Value |
| --- | --- |
| `KALSHI-ACCESS-KEY` | API key ID (UUID) |
| `KALSHI-ACCESS-TIMESTAMP` | Current time in **milliseconds** |
| `KALSHI-ACCESS-SIGNATURE` | Base64 RSA-PSS signature |

The signed message is the concatenation `timestamp + METHOD + path`, e.g.
`1703123456789GET/trade-api/v2/portfolio/balance`. The path is the full path
from the API root **with query parameters stripped**. Signature parameters:
RSA-PSS, SHA-256 digest, MGF1-SHA256, salt length = digest length.

WebSocket connections authenticate with the same headers on the HTTP upgrade
handshake.

**Public market data needs no authentication at all.** Verified: `GET /markets`,
`GET /markets/{ticker}/orderbook`, `GET /series`, `GET /exchange/status` all
returned 200 unauthenticated. This matters for Phase 1 — the read-only capture
pipeline can run without any credential on disk, and it should.

## Market data endpoints

| Endpoint | Use |
| --- | --- |
| `GET /markets` | Market list + top of book (`yes_bid_dollars`, `yes_ask_dollars`, sizes). Filter by `series_ticker`, `event_ticker`, `status` |
| `GET /markets/{ticker}/orderbook` | Full depth for one market |
| `GET /markets/orderbooks` | **Batch** orderbooks — the right call for a scanner |
| `GET /markets/trades` | Public trade prints |
| `GET /markets/candlesticks`, `GET /series/{s}/markets/{t}/candlesticks` | OHLC history |
| `GET /events`, `GET /events/{ticker}` | Event grouping, `GET /events/{t}` returns member markets |
| `GET /series`, `GET /series/{ticker}` | Series metadata incl. `fee_type`, `fee_multiplier`, `contract_terms_url`, `settlement_sources` |
| `GET /series/fee_changes`, `GET /events/fee_changes` | Scheduled fee overrides. Poll these |
| `GET /historical/markets`, `GET /historical/trades`, `GET /historical/markets/{t}/candlesticks` | Historical data. Auth requirement **UNVERIFIED** |

### Orderbook representation — read this before writing the ingest

The orderbook returns **resting bids on both sides only**. There is no ask side.

```json
{"orderbook_fp": {"yes_dollars": [], "no_dollars": [["0.9700","10.00"],["0.9800","5059.00"],["0.9900","12744.00"]]}}
```

Levels are `[price, size]` ascending, as decimal strings. The YES ask is derived
from the NO bid:

```
ask(YES) = 1 - best_bid(NO)      size = size at that NO bid level
ask(NO)  = 1 - best_bid(YES)
```

Verified against the same market's top-of-book fields: best NO bid 0.9900 x
12744 corresponded exactly to `yes_ask_dollars: "0.0100"`,
`yes_ask_size_fp: "12744.00"`. **Detector 1 (`ask(YES) + ask(NO) < 100c`) is
therefore arithmetically equivalent to `bid(YES) + bid(NO) > 100c` on the raw
book** — get this transformation wrong and the detector fires on every market
in the exchange.

### Fixed-point fields

The API has migrated to string-encoded decimals: `*_dollars` for prices (4
decimal places) and `*_fp` for counts (2 decimal places). The older integer-cent
fields (`yes_bid`, `volume`, `open_interest`) are absent from current responses.
Parse into `Decimal`, never `float`, and store the raw string.

**Fractional contracts are supported down to 0.01 of a contract.** Combined with
a fee that rounds up to the whole cent, a fractional fill can pay a fee larger
than its own notional. Order sizes must be whole contracts, and fills must be
checked for fractional residue.

## WebSocket

Single connection, JSON messages, subscribe by channel plus `market_ticker` /
`market_tickers`. Kalshi sends a Ping control frame every 10 seconds.

Public channels (connection auth only): `orderbook_delta`, `ticker`, `trade`,
`market_lifecycle_v2`, `multivariate_market_lifecycle`.
Private channels: `fill`, `market_positions`, `user_orders`, `order_group_updates`,
`communications`, `cfbenchmarks_value`, `pyth_value`.

`orderbook_delta` is the channel Phase 1 is built on: snapshot then incremental
deltas, with a sequence number for gap detection. A gap means resubscribe and
re-snapshot — and it goes in the gap report, not silently into the database.

`cfbenchmarks_value` streams the CF Benchmarks index values that crypto markets
actually settle against, "each carrying the raw upstream frame plus trailing
60-second and quarter-hour" aggregates. **For Detector 6 this is a better
reference than Binance or Coinbase spot**, because it is the settlement source
itself rather than a correlated proxy. It requires authentication, so it does
not remove the need for the public reference feeds, but the divergence study
should use it.

The `fill` message carries `taker_fees_dollars` and `maker_fees_dollars` per
fill at 4 decimal places — the ground truth for validating `fees.py` in Phase 4,
and confirmation that fees are assessed per fill rather than per order.

## Rate limits

Token buckets, separate for reads and writes. Most calls cost 10 tokens; check
`GET /account/endpoint_costs` for exceptions. Batch operations are charged per
item.

| Tier | Read tokens/s | Write tokens/s |
| --- | --- | --- |
| Basic | 200 | 100 |
| Advanced | 300 | 300 |
| Expert | 600 | 600 |
| Premier | 1,000 | 1,000 |
| Paragon | 2,000 | 2,000 |
| Prime | 4,000 | 4,000 |
| Prestige | 6,000 | 8,000 |

Basic is granted on signup (≈20 reads/s at the default 10-token cost); Advanced
is self-service via the Upgrade Account API Usage Level endpoint; above that is
volume-gated. Buckets hold one to two seconds of budget, so a burst of up to 2x
the per-second budget is possible after a quiet period.

Implication for Phase 1: **20 REST reads/s does not cover polling thousands of
markets.** The pipeline must be WebSocket-first, with REST used for snapshots,
reconciliation and the batch `GET /markets/orderbooks` call.

## Taxonomy

```
series  (KXBTCD)            fee model, settlement sources, contract terms live here
  event (KXBTCD-26AUG0317)  one resolution occasion
    market (KXBTCD-26AUG0317-T72749.99)   one binary contract
```

Market fields that matter to the detectors: `market_type` (`binary`),
`strike_type` (`greater`, `less`, `between`, ...), `floor_strike` / `cap_strike`,
`rules_primary` / `rules_secondary` (the resolution criteria text the Phase 2
exhaustiveness check must hash), `price_level_structure` (`linear_cent`) and
`price_ranges` (`step: "0.0100"` — a 1c tick).

Multivariate event collections (`/multivariate_event_collections`) are
combinatorial markets built from other markets. They are a plausible source of
genuine internal inconsistency and are **out of scope for Phase 1**; note them
and move on.

## Settlement

- No settlement fee. A basket held to expiry pays entry fees only.
- `settlement_timer_seconds` (60 on the crypto series inspected) is the delay
  between expiration and settlement.
- `expiration_time` can be later than `close_time`; `expected_expiration_time`
  is the working estimate.
- `can_close_early: true` on many series — the market can settle before its
  scheduled close. **An early close on one leg of a basket while other legs are
  still open is a real leg-risk scenario**, not a hypothetical one.
- Crypto series settle to CF Benchmarks Real-Time Index values: the average of
  60 index prints in the final minute before expiry. Not the Coinbase or
  Binance last trade. Detector 6's reference distribution must respect this.

## Order placement (Phase 4 — not built yet)

Recorded here only so Phase 4 does not have to re-research it.

`POST /portfolio/events/orders` accepts `client_order_id` — idempotency is
supported natively, satisfying hard constraint 5. Also available: `post_only`,
`reduce_only`, `time_in_force`, `expiration_ts`, `buy_max_cost` (which forces
fill-or-kill behaviour), `self_trade_prevention_type`, `order_group_id`.

**There is no native all-or-none multi-leg order.**
`POST /portfolio/events/orders/batched` submits several orders in one call but
is not atomic across legs, and order groups manage shared limits rather than
guaranteeing joint execution. The Phase 4 requirement that "multi-leg orders are
all-or-none" therefore has to be built in our own execution layer as
fill-or-kill legs plus an unwind path, and the unwind path can lose money. This
is the largest single piece of unresolved execution design and it should be
settled before Phase 4 starts, not during it.

## Sources

- <https://docs.kalshi.com/openapi.yaml> (vendored: `spec/openapi.yaml`)
- <https://docs.kalshi.com/asyncapi.yaml> (vendored: `spec/asyncapi.yaml`)
- <https://docs.kalshi.com/getting_started/quick_start_authenticated_requests>
- <https://docs.kalshi.com/getting_started/rate_limits>
- <https://docs.kalshi.com/websockets>
- Live API probes against `api.elections.kalshi.com`, `demo-api.kalshi.co` and
  `external-api.demo.kalshi.co`, 2026-08-02
