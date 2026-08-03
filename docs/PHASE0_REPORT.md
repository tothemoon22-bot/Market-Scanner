# Phase 0 — Research findings and gate report

Date: 2026-08-02. Awaiting approval before any detector is written.

## 0. Detector 1 is structurally impossible and should not be built

Kalshi runs **one** book per market. YES and NO are two quotations of the same
instrument, so:

```
ask(YES) + ask(NO) = (100 - bid_NO) + (100 - bid_YES) = 200 - (bid_YES + bid_NO)
```

`ask(YES) + ask(NO) < 100c` therefore requires `bid_YES + bid_NO > 100c`, which
is exactly the condition under which the matching engine crosses those two
resting orders and trades them. The complementary arb cannot exist in a
non-crossed book. It is not gated by fees — it is a state the exchange does not
permit to persist.

Measured across 190 two-sided books (crypto, NFL, MLB, WNBA, Fed, CPI, weather),
2026-08-02:

| Quantity | min | max |
| --- | --- | --- |
| `bid_YES + bid_NO` | 41c | **99c** |
| `ask(YES) + ask(NO)` | **101c** | 159c |

Zero crossed books. The tightest sat one full tick on the wrong side of the
boundary before any fee.

**What survives.** The other four detectors compare *distinct markets with
distinct books*, and no engine matches between them; nothing structural
prevents those violations. The salvageable content of Detector 1 is the
cross-market complementary pair — two separate markets that partition an
event — which is simply the N=2 case of Detector 2 and inherits its mandatory
exhaustiveness check. The spec waived that check for Detector 1 on the grounds
that "the pair settles to $1 regardless of outcome"; that is true only of one
market's own YES/NO, i.e. exactly the case that turns out to be impossible.
**Anyone who "fixes" Detector 1 by pointing it at two markets without routing it
through the exhaustiveness gate has built an uncovered-short generator.**

**Repurpose, don't delete, the computation.** In a correctly reconstructed book
`bid_YES + bid_NO > 100c` cannot happen. If we observe it, our book is wrong —
dropped `orderbook_delta` sequence, stale snapshot, or two sides read at
different times. That makes it a free continuous correctness check on the
ingest pipeline. It belongs in the Phase 1 gap report as an invariant
violation, not in the detector suite as an opportunity.

**Where else this trap could bite:** multivariate event collections are
combinatorial markets built from other markets. Before building against them,
check whether the engine links a collection's book to its components. If it
does, the same reasoning applies.

Credit where due — I flagged the ask-derivation as an implementation hazard and
stopped there. The structural consequence is the more important half and I
missed it.

## 1. The fee question, resolved

**Your 7% source is right. The 0% source is wrong as stated, but it is pointing
at two real things.**

```
taker: fees = round up(0.07   x multiplier x C x P x (1-P))
maker: fees = round up(0.0175 x multiplier x C x P x (1-P))   # where charged at all
```

`P` in dollars, `C` in contracts, ceiling to the next whole cent, no settlement
fee. Confirmed against the fee schedule Kalshi filed with the CFTC (vendored in
`docs/venues/kalshi/spec/`) and reproduced exactly by `src/venues/kalshi/fees.py`,
whose tests assert every row of the published fee table. Your `$1.75 per 100
contracts at 50c` figure is correct.

Where the 0% claim comes from — both of these are true, neither rescues us:

- **Resting orders are free on 98.7% of series.** Kalshi charges maker fees only
  where the series carries `fee_type: quadratic_with_maker_fees` (132 of 10,755
  series surveyed, mostly sports and macro). Everywhere else, a limit order that
  sits on the book and later fills costs nothing. But a resting order is not a
  fill, and a basket that fills three legs of four is an uncovered directional
  bet — the exact thing this design exists to avoid.
- **Ten series are genuinely fee-free** via `fee_multiplier: 0`, including two
  crypto ones (`KXBTCY`, `KXETHY`). They are annual-horizon markets. They are
  also the only place on the exchange where a 1-tick edge is capturable.

Two secondary claims I could not confirm and which turn out to be false: there
is **no** elevated crypto multiplier (every live series is multiplier 1 or 0),
and it is **not** true that all series carry maker fees. Widely-cited fee guides
say both. The exchange API says otherwise, and it is the API we build against —
`fee_type` and `fee_multiplier` are read per series, never assumed.

One gap: `kalshi.com` returns HTTP 429 to every request from this environment
and archive.org is blocked by the network policy, so I could not pull the
current PDF (titled "Fee Schedule for July 2026"). The taker coefficient and the
rounding rule are from the primary CFTC filing; the maker coefficient rests on
several independent sources that quote the current schedule verbatim and agree
it is exactly 25% of taker. If you can save that PDF and drop it in
`docs/venues/kalshi/spec/`, the maker number moves to fully primary.

## 2. How much the fee shrinks the opportunity set

You asked for this directly. The honest answer has a measured part and an
assumed part, and I am keeping them separate.

**Measured, from the fee model.** The tick is 1c, so what matters is how many
whole ticks of visible mispricing the fee eats before we break even. For a
two-leg complementary pair bought at the ask and held to settlement — now
strictly a *cross-market* structure, per section 0:

| Price split | Fee per contract | Ticks of edge needed |
| --- | --- | --- |
| 5c / 95c | 0.68c | 1 |
| 25c / 75c | 2.64c | 3 |
| 50c / 50c | 3.50c | 4 |

For an N-leg exhaustive basket the gate tightens monotonically with leg count: a
10-leg basket must be buyable for 93.7c or less. Wide baskets are the worst
place to look, not the best.

At our actual order size the fee is worse than the headline rate, because the
ceiling applies per fill and per leg: 1.8c per contract on a 10-lot at 50c
against 1.75c asymptotically, and a four-leg basket of 1-lots pays $0.08 where a
single 4-lot pays $0.07.

**Assumed, and to be measured in Phase 2.** How much of the opportunity set that
destroys depends entirely on the size distribution of violations, which we do
not have yet. If violation frequency falls by roughly an order of magnitude per
additional tick — the usual shape in a tight electronic market, but an
assumption, not a finding — then moving the gate from 1 tick to 4 removes well
over 99% of mid-range candidates, and essentially all of what survives lives in
the tails where the fee is small. **That is a hypothesis for Phase 2 to
falsify, and item 5 of the Phase 2 gate is exactly the measurement that settles
it.** I am not going to pretend the analytic argument substitutes for the data.

The structural conclusion I am confident in: **if a fee-adjusted edge exists on
Kalshi, it is at extreme prices, in few-leg structures, and — where they exist —
on fee-free series.** That is where Phase 1 capture should be pointed, and it is
a narrower target than the original spec implies.

## 3. Findings that change the design

Things I did not go looking for that matter more than the fee answer:

- **Public market data needs no authentication.** Orderbooks, markets, series
  and trades all returned 200 unauthenticated. Phases 1–3 can run with zero
  credentials on disk. That is a stronger version of hard constraint 2 than the
  spec asks for and I intend to hold to it.
- **The orderbook has no ask side.** Kalshi returns resting bids for YES and NO
  only; `ask(YES) = 1 - best_bid(NO)`. Verified empirically against top-of-book
  fields. This is what section 0 is built on.
- **`mutually_exclusive` is a venue claim, not exhaustiveness.** Events carry
  the flag; it asserts at most one leg resolves YES and says nothing about
  whether at least one does. Live example in `docs/venues/kalshi/README.md`:
  `KXDEELRIP-40` ("Will Deel or Rippling IPO first?") is flagged mutually
  exclusive, and if neither company IPOs by 2040 **both legs resolve NO and a
  holder of both loses everything**. It is one API field away from being
  auto-approved by mistake. Keep it out of the Phase 2 approval flow entirely.
- **There is no native all-or-none multi-leg order.** Batched order submission
  is not atomic and order groups manage limits, not joint execution. The Phase 4
  requirement that baskets be all-or-none has to be built by us out of
  fill-or-kill legs plus an unwind path — and the unwind path can lose money.
  This is the biggest unresolved design question in the project and it should be
  settled before Phase 4 opens, not during it.
- **Markets can close early** (`can_close_early: true` is common). An early
  close on one leg while others are open is a live leg-risk scenario.
- **Fractional contracts exist**, down to 0.01. With a fee that rounds up to the
  whole cent, a fractional fill can pay a fee larger than its own notional.
- **Kalshi streams the settlement source itself** on the `cfbenchmarks_value`
  channel. Crypto markets settle to a 60-print average of the CF Benchmarks
  index, *not* to Coinbase or Binance last trade. For Detector 6 that is a
  better reference than either spot venue, and it also means part of any
  Kalshi-vs-spot divergence is index basis rather than mispricing.
- **Binance's main API is geo-blocked here (HTTP 451).** Use
  `data-api.binance.vision`. `api.binance.us` is a *different exchange* and must
  not be silently swapped in.
- **Rate limits are ~20 REST reads/s at the Basic tier**, which does not cover
  polling thousands of markets. The pipeline has to be WebSocket-first.

## 4. What is in the repo

Phase 0 is specified as "no code", but the deliverables list asks for the fee
formula in `src/venues/kalshi/fees.py` with unit tests, so that exists. Nothing
else does. There is no client, no detector, no pipeline, and `src/execution/` is
empty by construction.

| Path | What |
| --- | --- |
| `docs/COST_MODEL.md` | The Phase 0 deliverable — round-trip cost at 25c/50c/75c, derived thresholds |
| `docs/venues/kalshi/fees.md` | Fee investigation, evidence trail, live API survey |
| `docs/venues/kalshi/README.md` | Auth, endpoints, WebSocket, rate limits, taxonomy, settlement |
| `docs/venues/reference/README.md` | Binance/Coinbase public access, geo-block, settlement-source caveat |
| `docs/venues/kalshi/spec/` | Vendored OpenAPI + AsyncAPI specs and CFTC fee filings |
| `src/venues/kalshi/fees.py` | The fee model. Decimal only — floats raise TypeError |
| `src/research/cost_model.py` | Generates the COST_MODEL tables; the doc is derived, not typed |
| `tests/test_fees.py` | 80 tests, every row of the published fee table |
| `tools/secret_scan.py`, `.pre-commit-config.yaml` | Hard constraint 4 |

## 5. Gate

Approval needed to start Phase 1. Three decisions worth making now rather than
later:

0. **Detector 1.** My recommendation: delete it as specified, keep its
   arithmetic as a Phase 1 book-integrity invariant, and let the cross-market
   complementary pair live inside Detector 2 as the N=2 case — under the
   exhaustiveness gate, with no waiver. The alternative, keeping a separate
   two-leg detector, buys nothing and reintroduces the waiver that made the
   original spec unsafe for cross-market pairs.
1. **Scope of capture.** The fee math says the edge, if any, is at extreme
   prices and in few-leg structures. Capturing every market in every tracked
   series is the spec as written; capturing the tails deeply and the middle
   thinly gets a cleaner answer sooner. My recommendation is to capture broadly
   for the first 72 hours — the gap report is about pipeline integrity, not
   edge — then narrow before the 3-week Phase 2 run.
2. **The all-or-none problem.** It has no clean answer in Kalshi's API. If the
   answer turns out to be "we cannot execute baskets atomically and the unwind
   cost exceeds the edge", that finding kills Phase 4 regardless of what Phase 2
   measures, and it would be cheaper to establish now than after five weeks of
   capture.
