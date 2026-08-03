# Phase 0.5a — Spread Study

Source sweep: `20260803T070632Z_t0`. Read-only, public endpoints, no credentials.

The derived data backing every number here is committed under
`research/snapshots/`, so this report stays auditable after the working data is
gone. Reproduce it exactly with::

    python -m src.research.spread_study \
        --sweep research/snapshots/20260803T070632Z_t0 \
        --compare research/snapshots/<second sweep>

Take a fresh sweep with `python -m src.research.spread_sweep --label <tag>`.

**`100 - (bid_YES + bid_NO)` is the YES bid-ask spread.** It is the same
expression the deleted complementary detector was testing, and it governs
whether any taker-side detector can ever fire. Each leg crossed costs about
half a spread, so an N-leg basket needs true mispricing of at least
`fee_gate + sum(s_i / 2)` before it is visible on the book.

## Universe

| | Count |
| --- | --- |
| Open markets (multivariate parlays excluded) | 70,820 |
| Two-sided (both YES and NO bids resting) | 44,453 (63%) |
| Open events | 8,747 |

Multivariate parlay markets are excluded at the API via `mve_filter=exclude`.
A first pass without that filter found **26,852 of 27,000 sampled open markets
were `KXMVE*` combination shells**, essentially none with a two-sided book: they
are generated one per requested combination and quoted through the RFQ system
rather than resting on a book. Any market count quoted for Kalshi that includes
them is measuring auto-generated inventory, not tradeable venues.

## Spread distribution, whole exchange

| Spread | Markets | Share |  |
| --- | --- | --- | --- |
| 1c | 6,402 | 14.4% | ####### |
| 2c | 3,012 | 6.8% | ### |
| 3-4c | 6,067 | 13.6% | ####### |
| 5-8c | 15,732 | 35.4% | ################## |
| 9-16c | 3,472 | 7.8% | #### |
| 17-32c | 1,718 | 3.9% | ## |
| 33-100c | 6,218 | 14.0% | ####### |

Median 6c, p10 1c,
p25 3c, p75 9c, p90 55c.
23.9% of two-sided markets are quoted 2c wide or tighter;
14.4% are at the 1c minimum.

## By price bucket

| Price bucket | n | p10 | median | p75 | p90 | share <=2c |
| --- | --- | --- | --- | --- | --- | --- |
| <=8c | 8,202 | 1c | 3c | 5c | 7c | 46.1% |
| 8-25c | 9,443 | 1c | 6c | 8c | 16c | 22.0% |
| 25-75c | 19,829 | 2c | 7c | 47c | 95c | 15.4% |
| 75-92c | 4,253 | 1c | 6c | 8c | 22c | 18.0% |
| >=92c | 2,726 | 1c | 4c | 6c | 8c | 34.7% |

## By fee treatment

| Segment | n | p10 | median | p75 | p90 | share <=2c |
| --- | --- | --- | --- | --- | --- | --- |
| standard quadratic | 42,607 | 1c | 6c | 9c | 60c | 21.8% |
| maker-fee series | 1,640 | 1c | 1c | 3c | 7c | 71.5% |
| fee-free (multiplier 0) | 206 | 0c | 1c | 2c | 4c | 83.0% |

## By category

| Category | n | p10 | median | p75 | p90 | share <=2c |
| --- | --- | --- | --- | --- | --- | --- |
| Sports | 16,084 | 1c | 7c | 41c | 93c | 25.5% |
| Elections | 10,617 | 2c | 6c | 7c | 8c | 14.2% |
| Entertainment | 4,234 | 1c | 5c | 8c | 10c | 18.6% |
| Financials | 4,081 | 1c | 6c | 9c | 35c | 15.9% |
| Science and Technology | 2,448 | 1c | 5c | 95c | 97c | 39.5% |
| Economics | 2,396 | 1c | 6c | 11c | 37c | 25.9% |
| Politics | 1,732 | 1c | 4c | 6c | 7c | 36.5% |
| Mentions | 988 | 1c | 2c | 5c | 7c | 56.4% |
| Crypto | 624 | 1c | 3c | 6c | 10c | 44.9% |
| Commodities | 615 | 1c | 3c | 6c | 11c | 48.1% |
| Climate and Weather | 462 | 1c | 3c | 6c | 9c | 45.2% |
| (unmapped) | 124 | 2c | 7c | 11c | 34c | 11.3% |
| Companies | 37 | 4c | 6c | 8c | 9c | 8.1% |

## Required mispricing — the real gate

Uniform N-leg basket, legs priced 1/N, 100 contracts a leg, all crossing.
Fee term is the summed per-leg ceiling from `src/venues/kalshi/fees.py`; spread
term is `N x s/2` at that bucket's spread.

| Legs | Leg price | Bucket | Fee term | Spread term (median) | **Threshold (median)** | Spread term (p10) | **Threshold (p10)** |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2 | 50c | 25-75c | 3.50c | 7.0c | **10.5c** | 2.0c | **5.5c** |
| 3 | 33c | 25-75c | 4.65c | 10.5c | **15.2c** | 3.0c | **7.6c** |
| 4 | 25c | 25-75c | 5.28c | 14.0c | **19.3c** | 4.0c | **9.3c** |
| 10 | 10c | 8-25c | 6.30c | 30.0c | **36.3c** | 5.0c | **11.3c** |

The spread term exceeds the fee term at every leg count, by a factor that grows
with N. **The fee schedule was the wrong thing to worry about.**

## What a basket actually costs right now

Sum of `ask(YES)` across every leg of events the venue flags mutually
exclusive, where all legs are two-sided. Exhaustiveness is not established for
any of these, so **none is an opportunity** — this measures the cost of
crossing, not edge. A negative number would be a basket buyable below $1.

| Legs | Events | min | p10 | median | p90 |
| --- | --- | --- | --- | --- | --- |
| 2 | 1,929 | -90c | +1c | +3c | +42c |
| 3 | 414 | -78c | +1c | +9c | +143c |
| 4 | 78 | -81c | +1c | +7c | +14c |
| 5 | 52 | -85c | +3c | +10c | +18c |
| 6 | 58 | -71c | +0c | +16c | +24c |
| 7 | 20 | -73c | -5c | +14c | +24c |
| 8 | 24 | -84c | -16c | +14c | +299c |
| 10 | 18 | +10c | +14c | +21c | +79c |
| 14 | 13 | -9c | +2c | +16c | +44c |

Across 2,650 such events the median basket costs
**+4c** relative to par, the cheapest observed was
**-90c**, and **69** were priced below 100c.

### The sub-par baskets are not mispricings

A structural detector without an exhaustiveness gate would flag all
69 of those as arbitrage. Every one inspected is a
**listed-subset market**: Kalshi lists some candidate outcomes, not all of them,
and the unlisted residual carries most of the probability.

| sum(ask) | Legs | Implied residual r | Event | Title |
| --- | --- | --- | --- | --- |
| 10c | 2 | 90% | `KXLAPRIMARY-01R26` | LA-01 Republican nominee? |
| 11c | 2 | 89% | `KXLAPRIMARY-01D26` | LA-01 Democratic nominee? |
| 13c | 2 | 87% | `KXLAPRIMARY-02D26` | LA-02 Democratic nominee? |
| 15c | 5 | 85% | `KXLAPRIMARY-05D26` | LA-05 Democratic nominee? |
| 16c | 8 | 84% | `KXSTATE51-29` | What will be the 51st state in Trump's term? |
| 18c | 2 | 82% | `KXLAPRIMARY-04D26` | LA-04 Democratic nominee? |
| 19c | 4 | 81% | `KXLAPRIMARY-06R26` | LA-06 Republican nominee? |
| 22c | 3 | 78% | `KXLAPRIMARY-03D26` | LA-03 Democratic nominee? |

For a basket paying $1 only if one of the *listed* legs wins, break-even
residual probability is `r = 1 - cost/100`. The discount to par is therefore
not edge — **it is the market's implied probability that none of the listed
outcomes occurs**, and the two are arithmetically the same number.

`KXLAPRIMARY-01R26` ("LA-01 Republican nominee?") lists two candidates and
trades at 10c. That is not a 90c arbitrage; it is the market saying there is a
90% chance the nominee is somebody Kalshi has not listed. Buying both legs is a
short of "someone else wins" at fair value.

This is the strongest available argument for the mandatory exhaustiveness
check, and it is stronger than the safety argument: without a verified
partition, `sum(ask) < 100` carries **no** information about mispricing. The
detector would not be taking on hidden risk in exchange for real edge — it
would be measuring the residual and reporting it as edge.

## Size at the touch

Median resting size 96 contracts on the bid and
69 on the offer; p10 5 and
5. Size is not the binding constraint — the spread is.

## Stability across sweeps

Second sweep `20260803T071019Z_t1`, taken **4 minutes** after the first:
44,456 two-sided markets, median spread 6c against
6c at t0. Of 44,422 markets present in both, median
absolute change in spread was 0c and
93% were unchanged.

**This is a short-interval check and is weak evidence.** At this gap most books
have simply not been requoted, so a high unchanged rate is close to
uninformative about intraday stability. The 6-hour comparison the experiment
calls for is outstanding; it changes nothing about the required-mispricing
tables, which depend on the level of the spread rather than its persistence.

