# Phase 0.5 — Kill-shot experiments

Date: 2026-08-03. Five experiments, each capable of ending the project before
five weeks of capture. All read-only, public endpoints, zero credentials.

| | Experiment | Verdict |
| --- | --- | --- |
| 0.5a | Spread distribution | **NO-GO for taker-side detection** |
| 0.5b | Cross-market matching | **GO** — premise confirmed, and it reinforces 0.5a |
| 0.5c | All-or-none mechanics | **BLOCKED** — needs credentials and a constraint decision |
| 0.5d | Maker coefficient | **BLOCKED** — needs a manual human trade |
| 0.5e | Jurisdiction and hosting | **GO** on hosting; one question left for you |

Full data: [`SPREAD_STUDY.md`](SPREAD_STUDY.md).

---

## 0.5a — Spread distribution → NO-GO

Swept every open market on Kalshi: 70,820 markets, 44,453 with a two-sided
book, 8,747 events. Second sweep 4 minutes later for a stability sanity check.

**Median spread is 6c. The p10 is 1c. 23.9% of two-sided markets are quoted 2c
or tighter.**

Required mispricing for a uniform N-leg taker-side basket, `fee_gate + Σ(sᵢ/2)`:

| Legs | Leg price | Fee term | Spread term (median) | **Threshold (median)** | **Threshold (p10)** |
| --- | --- | --- | --- | --- | --- |
| 2 | 50c | 3.50c | 7.0c | **10.5c** | **5.5c** |
| 3 | 33c | 4.65c | 10.5c | **15.2c** | **7.6c** |
| 4 | 25c | 5.28c | 14.0c | **19.3c** | **9.3c** |
| 10 | 10c | 6.30c | 30.0c | **36.3c** | **11.3c** |

**You were right that this dominates the fee term, and it is worse than that:
the spread term exceeds the fee term at every leg count, and the ratio grows
with N.** At the median a 4-leg basket needs a 19c mispricing to become visible.
A 19c mispricing in a market that resolves to a verifiable public fact is not a
pricing error, it is a different question being priced.

I did not have to reason about "plausible mispricing magnitude", because the
sweep measures the answer directly. Across 2,650 mutually-exclusive events with
all legs two-sided, **the median basket costs +4c relative to par** — you pay 4c
over $1 to buy a $1 payoff. The p10 is +1c. There is no fat tail of
underpriced baskets on the buy side.

### The 69 sub-par baskets are the residual, not edge

69 events did price below 100c, some dramatically. A structural detector with
no exhaustiveness gate would flag every one. Every one inspected is a
**listed-subset market**:

| sum(ask) | Legs | Implied residual | Event |
| --- | --- | --- | --- |
| 10c | 2 | 90% | LA-01 Republican nominee? |
| 16c | 8 | 84% | What will be the 51st state in Trump's term? |
| 22c | 3 | 78% | LA-03 Democratic nominee? |

For a basket paying $1 only if a *listed* leg wins, break-even residual
probability is `r = 1 − cost/100`. **The discount to par and the implied
residual probability are arithmetically the same number.** "LA-01 Republican
nominee?" lists two candidates and trades at 10c because the market thinks
there is a 90% chance the nominee is someone Kalshi has not listed.

This is a stronger argument for the exhaustiveness check than the safety one in
the spec. Without a verified partition, `Σask < 100` carries **no information
about mispricing at all**. The detector would not be trading hidden risk for
real edge — it would be measuring the residual and reporting it as edge. Your
`EV = (1−r)×edge − r×max_loss` formulation is what makes these evaluable, and
on all 69 it returns approximately zero, because the market has priced `r`
correctly.

### What this closes and what it leaves open

**Closed: taker-side basket detection on the standard exchange.** Median
thresholds of 10–36c cannot be met by real mispricings.

**Not closed:** the tails and the fee-free series, which the spread data
singles out as a different regime:

| Segment | n | p10 | median | share ≤2c |
| --- | --- | --- | --- | --- |
| fee-free (`fee_multiplier: 0`) | 206 | 0c | **1c** | 83.0% |
| maker-fee series | 1,640 | 1c | **1c** | 71.5% |
| standard quadratic | 42,607 | 1c | 6c | 21.8% |
| price ≤8c bucket | 8,202 | 1c | 3c | 46.1% |

The fee-free and maker-fee series are quoted six times tighter than the
exchange median. That is not a coincidence — they are the series Kalshi
subsidises to attract market makers, and tight quotes are what the subsidy buys.
A 2-leg basket at a 1c median spread has a threshold of 1c + fee, and on the
fee-free series the fee term is zero. **The only place on this exchange where a
taker-side basket can clear its gate is roughly 200 markets deep.** Phase 1
should confirm that number before anything else.

**The remaining general path is resting orders**, free on 98.7% of series. That
is not this project. It relocates the entire problem to adverse selection: your
resting order fills exactly when someone informed wants the other side. Naming
it explicitly, as the spec asks: *that is a market-making project with a
different risk model, a different measurement plan, and a different failure
mode, and it should not be slid into under the name "no-arbitrage scanner".*

### Caveat

The stability check ran 4 minutes apart, not 6 hours. 93% of books were
unchanged, which at that interval is close to uninformative.

**The 6-hour sweep is outstanding and not scheduled.** I tried to schedule a
self-wakeup for it; the tool needs an approval this non-interactive session
cannot obtain. It has to be run by hand, and the recipe is:

```bash
python -m src.research.spread_sweep --label t2
python -m src.research.spread_study \
    --sweep research/snapshots/20260803T070632Z_t0 --compare data/sweeps/<t2 dir>
python -m src.research.cross_market_check --sweep data/sweeps/<t2 dir>
```

The t0 and t1 snapshots are committed under `research/snapshots/`, so the
comparison works from a fresh container. This cannot change the
required-mispricing tables, which depend on the level of the spread rather than
its persistence — it tests whether the 6c median is a stable property of the
exchange or an artifact of one moment.

---

## 0.5b — Cross-market matching → GO (premise confirmed)

Two lines of evidence, held to the same standard as the crossed-book claim.

**Documentary.** KalshiEX Rulebook Rule 5.9: "Kalshi's central limit order book
matches Orders... first by price and then time priority." Rule 5.10(a) describes
filling entirely within one Contract's own book — an order to buy a Contract is
filled against "the best sell offer" in that Contract. No rule provides for
matching an order in one Contract against orders in another. That is absence of
a linkage rule, which on its own is weak.

**Observational — the decisive half.** If the engine enforced consistency
between markets, a strike-ladder monotonicity violation could never be observed.
Across 1,640 clean single-underlying ladders and 10,847 adjacent strike pairs:

- **10 genuine violations** where `bid(K2) > ask(K1)` for `K2 > K1` — being paid
  to hold a payoff that is non-negative in every state.
- **0 survive the fee gate.** Largest gross credit 2c against a 3.01c fee.

So the premise holds: **Kalshi does not enforce cross-market consistency, and
violations do persist.** They are simply too small to pay for the crossing. Note
the horizon on the biggest ones — the two 2c violations are in `KXFEDFUNDSYEAR-31JAN01`,
resolving in 4.4 years. Even free, 2c locked for 4.4 years is not a business,
which is your annualized-ranking point arriving from the data rather than from
theory.

### Multivariate collections: not a book, and a hazard I would have missed

MVE collections are parlays — "the resulting market will only resolve to YES if
every associated market resolves to YES". They do **not** rest on a book: they
are generated one market per requested combination and quoted through the RFQ
system. A first sweep without `mve_filter=exclude` found **26,852 of 27,000
sampled open markets were `KXMVE*` shells**, essentially none two-sided. Any
Kalshi market count that includes them is counting auto-generated inventory.

They are out of detector scope for a better reason than "unlinked": there is
nothing to scan. But they are worth one note for later, since the Fréchet
bounds `max(0, ΣP−(n−1)) ≤ P(A∧B) ≤ min(P(A),P(B))` hold regardless of
dependence, and a parlay quoted above `min(P(A),P(B))` is a dependence-free
arbitrage against its own components. That is a genuine detector class the spec
does not contain. It is also an RFQ workflow, not a scanning one. **Flagging,
not building** — and per the standing rule, note that it would reintroduce
exactly the leg-risk problem 0.5c exists to measure, against a counterparty who
quotes only when asked.

### A methodology finding that costs money if missed

My first two passes at this check reported 6,327 and then 350 violations. Both
were entirely artifacts:

1. Sports spread events list markets for **both competitors at the same numeric
   strike**. Sorting by strike interleaves two different underlyings.
2. Tennis spread events list "Player A -1.5" and "Player B -3.5" in one event —
   different underlyings at *different* strikes, which the first filter misses.

**`strike_type` and `floor_strike` do not establish that two markets are on the
same underlying in the same direction, and event grouping does not either.**
Detector 3 needs verified underlying identity. Requiring every leg's subtitle to
share a shape once digits are stripped is what finally worked; it is a heuristic
and Phase 2 needs something better.

Separately, three of the 13 "violations" my float-based draft found were exactly
equal prices that float arithmetic made look unequal. The Decimal-only rule in
`fees.py` caught them. It is not fastidiousness.

---

## 0.5c — All-or-none mechanics → BLOCKED

**This experiment cannot be run as specified, and the reason is a conflict in
the spec rather than a missing tool.**

Hard constraint 1 says no order-placing code exists in the repo until Phase 4.
0.5d reaffirms it: "Phases 0.5–3 stay read-only in code." But 0.5c requires
submitting batched FoK orders, breaking 30+ baskets deliberately, and timing an
unwind path — all of which is order-placing code. Independently, it needs demo
API credentials, which this environment does not have.

Flagging rather than resolving, per the standing rule: writing execution code
now to satisfy 0.5c would delete the constraint that makes "no order-placing
code" verifiable by looking at an empty directory. I am not doing that
unilaterally.

Three ways forward, in my order of preference:

1. **Defer 0.5c to the top of Phase 4.** Its go/no-go compares unwind cost to
   available edge, and after 0.5a there is no measured edge to compare against
   outside ~200 markets. The comparison is not yet meaningful.
2. **Carve out an explicit exception**: a `src/execution/` opened early, demo-only,
   with the startup assertion built first. Say so plainly and I will build it.
3. **Run it on paper against captured book data** — no orders, no credentials.
   This measures the sequencing question (thin leg first vs simultaneous) and
   the unwind cost distribution under assumptions, but it does **not** measure
   what 0.5c is actually for: real partial-fill and cancellation semantics.

What I can say now without running it: Kalshi has no native all-or-none
multi-leg order, `buy_max_cost` forces FoK on a single order, and batched
submission is not atomic. Your thin-leg-first sequencing is the right design and
matches how the fill-probability correlation in Phase 2 will behave.

---

## 0.5d — Maker coefficient primary source → BLOCKED on you

Requires a human placing one 1-contract resting order through the web UI. I
cannot do it and should not.

Protocol, so it costs you two minutes:

1. Pick a series with `fee_type: quadratic_with_maker_fees` — `KXFEDDECISION`,
   `KXCPI`, `KXPAYROLLS`, or any `KXNFLGAME`.
2. Rest **1 contract** as a limit order at a price near 50c, where the fee
   curve peaks and the signal is largest. Do not cross the spread — a taker fill
   measures the wrong coefficient.
3. After it fills, read the actual charge from the fills ledger.
4. Expected under our model: `ceil(0.0175 × 1 × P × (1−P))` = **$0.01** at 50c.
   Report the exact figure and its precision — if it shows as $0.0044 rather
   than $0.01, maker fees round to sub-cent and `fees.py` is conservative by
   more than a rounding step.

Current status: taker coefficient is primary (CFTC filing); maker coefficient
rests on agreeing secondary sources. Nothing in Phase 0.5's conclusions depends
on it — the maker path is not the taker path we just closed.

---

## 0.5e — Jurisdiction and hosting → GO, with one question

The premise in the spec is inverted, and the correction is good news.

This environment egresses from **160.79.106.0, Columbus, Ohio, US** (Google
Cloud, AS396982). Binance returns 451 **because we are in the US** — Binance
geo-blocks US traffic — not because we are somewhere Kalshi would reject. The
451 and Kalshi's US-only requirement are the same fact seen from two sides.

Confirmed working from here: Kalshi production and demo REST, Coinbase public
API, `data-api.binance.vision`. Confirmed blocked: `api.binance.com` (451),
`web.archive.org` (network policy), `kalshi.com` web (429 to this IP range —
which is why the current fee-schedule PDF is still missing).

Open for you, none of which I can resolve:

- **KYC residency.** The account holder's verified residency has to match, and
  Kalshi restricts certain contract types by US state. Where will the account be
  registered?
- **Datacenter IPs.** Whether Kalshi permits sustained API access from cloud IP
  ranges is a terms question, not a technical one. Production REST answered fine
  unauthenticated; authenticated behaviour may differ.
- **Where this actually runs in production.** This container is ephemeral and
  reclaimed on inactivity. A 72-hour Phase 1 capture cannot run here.

---

## What I recommend

**Do not start Phase 1 as specified.** The default scope — full depth on ~10
fee-free series plus top of book on all 10,755 — is now answering a question
0.5a already answered for the standard exchange. Storage is cheap but five weeks
is not.

The version of Phase 1 worth running is narrower and sharper:

1. **Deep capture on the ~200 markets in the fee-free and maker-fee series**,
   which are the only segment where the threshold is 1–2c rather than 10–36c.
   Full depth, deltas, the pipeline invariant, the lot.
2. **Top-of-book on everything else**, unchanged — you are right that scoping to
   where we expect edge makes the feasibility report unable to distinguish
   "edge lives here" from "we only looked here". Keep the control group.
3. **Add the 6-hour and multi-day spread sweep** as a standing job. Spread level
   is the governing parameter and one snapshot is not a distribution. This is
   also the outstanding piece of 0.5a — see the caveat above for how to run it.

And one thing worth deciding before any of it: if the answer is "the only viable
structure is resting orders on maker-free series", that is a market-making
project. It may well be the right project. It is not the one the constraints in
this repo were written for, and it deserves its own spec rather than arriving by
drift.
