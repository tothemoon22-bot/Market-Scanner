# Kalshi fees — the evidence trail

**Verdict: the 7% variant is correct. The "0% fees in 2026" claim is wrong as a
general statement, but it is not made up — a handful of specific series really
do trade fee-free, and resting orders really are free on almost every series.**

Retrieved 2026-08-02.

## What we found

| Question | Answer | Confidence |
| --- | --- | --- |
| Taker fee formula | `round up(0.07 x multiplier x C x P x (1-P))` | High — primary |
| Maker fee formula | `round up(0.0175 x multiplier x C x P x (1-P))` | Medium-high — multiple consistent secondaries, exactly 25% of taker |
| Maker fees charged on all series? | **No.** Only where `fee_type == quadratic_with_maker_fees` | High — live API |
| Zero-fee series exist? | **Yes**, via `fee_multiplier: 0` | High — live API |
| Higher multiplier for crypto? | **No.** Every live series is multiplier 1 or 0 | High — live API |
| Settlement fee | None | High — primary |
| Rounding | Up to the next cent, per fill, per leg | High — primary |

## Primary sources

**The exchange's own API is the strongest source and the one we build against.**
Kalshi's OpenAPI spec documents a `FeeType` enum (`quadratic`,
`quadratic_with_maker_fees`, `flat`) and a numeric `fee_multiplier` on every
series object, and states directly:

> FeeType is a string representing the series' fee structure. Fee structures can
> be found at https://kalshi.com/docs/kalshi-fee-schedule.pdf. 'quadratic' is
> described by the General Trading Fees Table, 'quadratic_with_maker_fees' is
> described by the General Trading Fees Table with maker fees described in the
> Maker Fees section, 'flat' is described by the Specific Trading Fees Table.

— `spec/openapi.yaml`, `Series.fee_type` (vendored copy in this directory).

**KalshiEX LLC Exchange Fee Schedule, filed with the CFTC 2022-09-12** (vendored
as `spec/cftc-fee-schedule-2022-09-12.pdf`) gives the formula verbatim:

> All trading fees are charged as a variable percentage fee of the expected
> earnings on an individual contract [...] the current general fee charged for a
> trade in dollars is given by the following formula:
>
> `fees = round up(0.07 x C x P x (1-P))`
>
> P = the price of a contract in dollars (50 cents is 0.5)
> C = the number of contracts being traded
> round up = rounds to the next cent

and, on maker orders:

> Trading fees are only charged for orders that are immediately matched with
> orders sitting on the orderbook. Trading fees are not charged for orders
> placed that are not immediately matched and are instead left as resting orders
> on the orderbook.

Its published fee table ($0.07 / $1.32 / $1.75 / $1.32 / $0.07 per 100 contracts
at 1c / 25c / 50c / 75c / 99c) is the acceptance test in `tests/test_fees.py`.
The same document contains the "Specific Trading Fees Table" for S&P 500 and
Nasdaq-100 markets at half the rate (`0.035`) — this is what `fee_type: flat`
refers to.

The current schedule PDF (`https://kalshi.com/docs/kalshi-fee-schedule.pdf`,
titled "Fee Schedule for July 2026 - 7.7.26 Update") could **not** be retrieved
from this environment: `kalshi.com` returns HTTP 429 to every request from this
IP range, and archive.org is blocked by the network policy. Get a copy manually
and drop it in `spec/` when convenient. Nothing below depends on it, but the
maker coefficient would move from "medium-high" to "high" confidence.

## Live API survey, 2026-08-02

`GET /series?category=<...>` across all 14 categories, 10,755 series:

| `fee_type` | `fee_multiplier` | Count | Share |
| --- | --- | --- | --- |
| `quadratic` | 1 | 10,613 | 98.68% |
| `quadratic_with_maker_fees` | 1 | 132 | 1.23% |
| `quadratic` | 0 | 10 | 0.09% |
| `flat` | — | 0 | 0% |

**Fee-free series** (`fee_multiplier: 0`) as of the survey: `KXBTCY` (BTC price
range EOY), `KXETHY` (ETH price EOY), `KXGREENLAND`, `KXGAMBLINGREPEAL`,
`KXPAHLAVIHEAD`, `KXEXPAND`, `KXDOED`, `KXGDPYEAR`, `KXLAYOFFSYINFO`.
Two of these are crypto. They are long-dated annual markets, which is exactly
the wrong horizon for a latency-sensitive scanner, but a fee-free series is the
only place where a 1-tick edge is capturable, so they are worth watching.

**Maker-fee series** are concentrated in Sports (108 of 132) and Economics (10):
`KXFEDDECISION`, `KXCPI`, `KXPAYROLLS`, `KXU3`, `KXNFL*`, `KXMLB*`, `KXATPMATCH`,
`KXWTAMATCH` and similar. Only two crypto series carry them (`KXBTCMAX125`,
`KXBTCMAX150`).

Re-run the survey on a schedule. `fee_type` and `fee_multiplier` are per-series
mutable state, and Kalshi publishes scheduled changes through
`GET /series/fee_changes` and `GET /events/fee_changes` (both empty on
2026-08-02). Events can override their parent series' fee model via
`fee_type_override` / `fee_multiplier_override`.

## Where the conflicting claims come from

- **"0% trading fees"** — true for resting orders on 98.7% of series, and true
  outright for the ten `fee_multiplier: 0` series. False as a general statement
  about crossing the spread, which is what every detector in this design does.
- **"7% taker"** — correct, and the "$1.75 per 100 contracts at 50c" figure
  checks out against the published table. The phrasing "7%" is loose: it is 7%
  of `P x (1-P)`, so the worst case is 1.75% of notional at 50c and less
  everywhere else.
- **Secondary sources are unreliable on the per-series detail.** One widely
  cited fee guide states "all series on Kalshi carry maker fees — there are no
  excluded contract types" and "no zero-fee series exist". The live API
  contradicts both. **Read `fee_type` and `fee_multiplier` from the series
  object; do not trust any static table, including ours.**

## Consequences for the scanner

Under the 7% variant, at mid-range prices a complementary pair must show 4 whole
ticks of mispricing before it clears the fee gate, and a 10-leg basket must be
buyable at 93.7c. See [`../../COST_MODEL.md`](../../COST_MODEL.md).

## Nuances still open

- **Maker fee sub-cent rounding.** At least one secondary source claims maker
  fees round to a "centicent" with monthly reimbursement of rounding overage
  above $10. The fill message reports `maker_fees_dollars` at 4 decimal places,
  which is consistent with sub-cent precision. `fees.py` rounds up to the cent,
  which over-states the maker fee slightly — conservative, and therefore safe
  for a gate. Verify against the fills ledger in Phase 4.
- **Volume tiers, rebates and incentive programs.** Kalshi runs a Fee Rebate
  Program (rebates start above $100/month of fees), a Volume Incentive Program,
  a Liquidity Incentive Program and various hedging rebate programs. **None of
  them apply at $100 of deployed capital** and none are modelled. If this ever
  scales, revisit.
- **Perpetual futures** use an entirely different tiered bps schedule (12.0 bps
  taker / 5.0 bps maker at tier 0, on notional). Out of scope — we trade event
  contracts. Vendored at `spec/cftc-fee-schedule-perps-2026-06-24.pdf` so
  nobody re-derives it later.

## Sources

- [Kalshi OpenAPI spec](https://docs.kalshi.com/openapi.yaml) (vendored: `spec/openapi.yaml`)
- [Kalshi AsyncAPI spec](https://docs.kalshi.com/asyncapi.yaml) (vendored: `spec/asyncapi.yaml`)
- [KalshiEX Fee Schedule, CFTC filing 2022-09-12](https://www.cftc.gov/sites/default/files/filings/orgrules/22/09/rule091222kexdcm003.pdf) (vendored)
- [KalshiEX Fee Schedule Update (RFQs), CFTC filing 2026-07-12](https://www.cftc.gov/filings/orgrules/rules0712269458.pdf) (vendored)
- [KalshiEX Fee Schedule (Perpetual Futures), CFTC filing 2026-06-24](https://www.cftc.gov/filings/orgrules/rules0624267243.pdf) (vendored)
- [Kalshi Help Center — Fees](https://help.kalshi.com/en/articles/13823805-fees)
- [Kalshi Fee Schedule PDF](https://kalshi.com/docs/kalshi-fee-schedule.pdf) — current version, not retrievable from this environment
- Secondary, used only for cross-checking the maker coefficient:
  [OddsShopper](https://www.oddsshopper.com/articles/prediction-markets/kalshi-fees),
  [Market Math](https://marketmath.io/blog/kalshi-fees-guide-2026),
  [InGame on the 2025-07-01 maker fee change](https://www.ingame.com/kalshis-change-may-increase-fee-sports-traders/)
