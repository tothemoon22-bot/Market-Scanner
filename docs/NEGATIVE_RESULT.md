# No exploitable no-arbitrage edge on Kalshi at retail latency

**Status: closed.** Investigation ran 2026-08-02/03 against live production data.
Everything below is measured unless labelled otherwise.

This document is written to be read without the repository. If you are deciding
whether to attempt this, read it before you write anything.

---

## The cleanest single result

There are 61 Kalshi markets where **both** obstacles to this strategy vanish at
once: the tick is 0.1¢ instead of 1¢, and the trading fee is exactly zero.
Three of them form verified-exhaustive partitions — sets of contracts that must
sum to $1 by construction, whatever happens in the world.

| Partition | Legs | Sum of asks |
| --- | --- | --- |
| BTC price range, end of 2026 | 28 | 105.2¢ |
| ETH price range, end of 2026 | 18 | 107.8¢ |
| US GDP growth 2026 | 14 | 100.3¢ |

All three cost **more** than the dollar they are guaranteed to pay. In the one
corner of the exchange where every known obstacle has been removed, the trade is
still a loss. That is the finding in one table.

---

## The question

> Does a fee-adjusted, executable pricing inconsistency exist on Kalshi at
> retail latency, and how often?

Not a forecasting question. The target was *internal mathematical
inconsistency* — sets of contracts that cannot all be correctly priced
simultaneously regardless of outcome. Those are verifiable rather than
predictive, which is what made the project worth attempting.

**Answer: no, and the reasons are structural rather than contingent.**

---

## Method and scale

One exchange-wide sweep of every open market, repeated for a persistence check.
Public endpoints only; no credentials were used at any point.

| Population | n |
| --- | --- |
| Open markets swept (multivariate parlay shells excluded) | 70,820 |
| With a two-sided book | 44,453 |
| Venue-flagged mutually-exclusive events, all legs two-sided | 2,650 |
| Adjacent strike pairs on verified single-underlying ladders | 10,847 |
| Verified-exhaustive partitions in the zero-fee universe | 13 |
| Independent snapshots | 2 |

Sample sizes are stated throughout because they are what separate a finding
from an anecdote.

---

## Finding 1 — the spread dominates the fee, and worsens with leg count

The original design treated the fee schedule as the central risk. It was never
the binding constraint.

Median YES bid-ask spread across 44,453 two-sided books: **6¢**. Buying a leg
costs roughly half a spread beyond fair value, so an N-leg basket needs a true
mispricing of at least `fee + Σ(sᵢ/2)` before it is even visible on the book:

| Legs | Fee term | Spread term | **Threshold** |
| --- | --- | --- | --- |
| 2 | 3.50¢ | 7.0¢ | **10.5¢** |
| 4 | 5.28¢ | 14.0¢ | **19.3¢** |
| 10 | 6.30¢ | 30.0¢ | **36.3¢** |

The spread term exceeds the fee term at every leg count and the ratio grows with
N. A four-leg basket needs a 19¢ mispricing to become visible. A 19¢ mispricing
in a market resolving to a verifiable public fact is not a pricing error; it is
a different question being priced.

---

## Finding 2 — the mispricing distribution was measured, not assumed

Two populations, deliberately kept separate because they support different
strength claims.

**Venue-flagged mutually-exclusive events (n=2,650).** Exhaustiveness *not*
verified — Kalshi's `mutually_exclusive` flag asserts at most one leg resolves
YES and says nothing about whether at least one does. Median basket cost
**+4¢** over par, p10 **+1¢**. No fat tail below par.

**Verified-exhaustive partitions in the zero-fee universe (n=13).** Buckets
checked to tile the real line with no gap or overlap. Median **+5.2¢** over par,
p10 95¢, min 91¢, max 144¢.

Neither distribution overlaps the threshold table above. The gap is not
marginal.

---

## Finding 3 — the residual identity

**This is the most transferable result and it generalizes to any partition-based
market, on any venue.**

For a basket that pays $1 only if one of the *listed* legs wins, the break-even
probability of the unlisted residual is:

```
r = 1 − cost/100
```

**The discount to par and the market's implied residual probability are the same
number.** They are not two quantities to compare; they are one quantity written
two ways.

Worked example, live during the study: *"LA-01 Republican nominee?"* listed two
candidates and the pair traded at 10¢ combined. That is not a 90¢ arbitrage. It
is the market saying there is a 90% chance the nominee is somebody Kalshi has
not listed. Buying both legs is a short of "someone else wins" at fair value.

The consequence is not a safety caveat, it is a closure:

> **Detector 2's below-par branch was never an arbitrage detector.** It was a
> residual-probability disagreement detector. Trading it requires beating the
> market's estimate of `r` — which is forecasting, the exact thing this design
> existed to avoid.

Combined with Finding 2 closing the above-par branch, the detector closes on
both sides. Absent a verified partition, `Σask < 100` carries **no information
about mispricing whatsoever.**

---

## Finding 4 — the falsifier fired, in the best available place, and lost

The prior version of this analysis listed "sub-cent tick size" as an untested
hypothetical that would change the conclusion. It is not hypothetical. **12.6%
of the exchange already ticks finer than a cent.**

| Tick structure | Markets | Median spread | Sub-1¢ share |
| --- | --- | --- | --- |
| `linear_cent` (1¢ throughout) | 61,913 | 6.0¢ | 0% |
| `tapered_deci_cent` (0.1¢ below 10¢ and above 90¢) | 8,315 | 5.8¢ | 8.0% |
| `deci_cent` (0.1¢ throughout) | 592 | **0.6¢** | 63.5% |

`tapered_deci_cent` places its fine tick **below 10¢ and above 90¢ — exactly the
tails where the fee gate is easiest to clear**, since the quadratic fee shrinks
toward the extremes. The falsifier fired in the single most favourable location
available on the exchange, and the conclusion survived.

**The mechanism inverts, and both directions close the thesis.** Exchange-wide,
the spread term dominates: a 6¢ spread against a 3.5¢ fee on a two-leg pair. In
deci-cent markets it reverses: a 1.75¢ fee at 50¢ dwarfs a 0.6¢ spread. Two
opposite structural causes, one conclusion. A thesis that fails under both is
not failing for a contingent reason.

And where both vanish together — the 61-market deci-cent ∩ fee-free
intersection — the baskets still price above par. See the opening table.

---

## Finding 5 — the two below-par results, and why capacity is the point

Two of the 13 verified zero-fee partitions did price below par. Both persisted
across snapshots. Neither is a business:

| Partition | Cost | Gross | Horizon | Annualized | Binding size | **Total capacity** |
| --- | --- | --- | --- | --- | --- | --- |
| US GDP growth 2029 | 95¢ | 5¢ | 3.57y | 1.47% | 10 contracts | **$0.50** |
| US GDP growth 2033 | 91¢ | 9¢ | 7.57y | 1.31% | 0.01 contracts | **$0.0009** |

The capacity column is the point. A 9¢ gross credit is a headline; nine
hundredths of a cent of realisable profit is the trade. Any screen that reports
edge without capacity will show these as opportunities.

Annualization is the second filter. Both returns sit an order of magnitude below
any threshold worth the operational risk, because the fee-free series are
long-dated: a 5¢ credit locked for 3.6 years is not the same instrument as a 5¢
credit locked for a week.

---

## Detector disposition

| # | Detector | Status | Closing evidence | n |
| --- | --- | --- | --- | --- |
| 1 | Complementary (single market) | **Structurally impossible** | One book per market. `ask(YES)+ask(NO)<100¢` requires `bid(YES)+bid(NO)>100¢`, which the matching engine crosses. Measured: bid sum never exceeded 99¢, ask sum never below 101¢ | 190 books |
| 2 | Event basket | **Closed, both branches** | Above par: verified partitions median +5.2¢, no overlap with threshold. Below par: the residual identity — the discount *is* the residual | 2,650 events; 13 verified partitions |
| 3 | Strike ladder monotonicity | **Closed** | 10 genuine violations persist (the engine does not enforce cross-market consistency), none survives the fee gate; largest 2¢ gross against 3.01¢ fee. No fee-free instruments exist: on a partition, monotonicity reduces to non-negative bucket prices | 10,847 pairs |
| 4 | Convexity / butterfly | **Closed structurally** | Same as 3. On a range partition the bucket price *is* the density, so the convexity condition reduces to "prices ≥ 0", which the tick structure guarantees. Nothing to test | — |
| 5 | Calendar monotonicity | **Closed** | Zero violations on the fee-free nested horizons | 2 nested pairs |
| 6 | Cross-venue divergence | **Never an arbitrage** | Research-only by design. Risk-neutral and real-world probabilities differ by a variance risk premium; divergence is not edge. Additionally, Kalshi crypto settles to a CF Benchmarks index, not to Coinbase or Binance last trade, so part of any observed divergence is index basis | — |

Note on 3 and 4: their closure is **structural**, not empirical, and is stronger
for it. There is no fee-free instrument on which to run them, because the zero-fee
universe contains range partitions rather than cumulative threshold ladders.

---

## The one pattern behind every false positive

Three separate near-misses during the investigation share a single shape.
**Partition-resemblance**: a structure that looks like a mutually-exclusive
exhaustive partition, is not one, and manufactures a spurious edge large enough
to be persuasive.

1. **Detector 1's exhaustiveness waiver.** The original design waived the
   exhaustiveness check for complementary pairs, on the grounds that YES/NO
   settles to $1 regardless of outcome. True — but only for a *single market's*
   own two sides, which is exactly the case that turns out to be structurally
   impossible. Carried over to a cross-market pair, the waiver becomes an
   uncovered-short generator. A live example during the study: two markets
   flagged mutually exclusive, asking whether Deel or Rippling IPOs first. If
   neither company IPOs, both legs resolve NO and a holder of both loses
   everything.

2. **Listed subsets.** The LA-01 case above. 69 events priced below par
   exchange-wide; every one inspected was a listed-subset market.

3. **Nested cumulative horizons.** Two fee-free events pair "before date X" with
   "before later date Y". They sum to 22¢ and 23¢. Read as partitions they
   annualize at **884%** and **4,367%**. They are cumulative, not exclusive —
   the second contains the first.

All three are the residual identity wearing different costumes. The defence is
the same in each case and it is not a heuristic: **verify the partition, by
naming the residual outcome, before computing anything.**

---

## Methodological errors caught

Reusable. Each cost real time and each would have produced a confident wrong
answer.

- **Phantom liquidity via fractional size.** Kalshi contracts trade down to 0.01,
  so a leg can display an offer while being economically empty. The 91¢ basket
  above had 0.01 contracts of capacity. **`size > 0` is not a liquidity gate;
  `size ≥ 1` is.**
- **Shared-underlying validation.** Naive ladder scans reported 6,327 violations,
  then 350 after one filter. Every one was an artifact of sports events listing
  markets for *both competitors* — at the same strike, and then at different
  strikes, which defeats the obvious fix. **`strike_type` and `floor_strike` do
  not establish that two markets share an underlying, and neither does event
  grouping.** The surviving count after correct filtering was 10.
- **Float comparison.** Three phantom violations arose from exactly-equal prices
  that float arithmetic made look unequal. A Decimal-only rule in the fee model
  caught them. This is not fastidiousness: the error direction turns a
  break-even trade into a winner on paper.
- **RFQ shells.** 26,852 of 27,000 sampled open markets were auto-generated
  multivariate parlay combinations quoted through a request-for-quote system,
  with no resting book. Any market count for Kalshi that includes them is
  measuring generated inventory, not tradeable venues.
- **Structural impossibility versus implementation bug.** Detector 1's
  complementary arbitrage was deleted rather than fixed. The distinction matters:
  a bug invites a patch, and patching this one produces the uncovered-short
  generator in item 1 above.
- **Partition verification is subtle.** It took three attempts. Bound semantics
  differ by strike type — `less` is exclusive, `between` inclusive — so a valid
  tiling has zero-width joins at the open ends and one granularity step in the
  interior. Requiring uniform joins rejects every real partition; assuming a
  0.01 step rejects every partition whose underlying is not reported in cents
  (US GDP tiles at 0.1 percentage points).

---

## What would change the conclusion

The prior list's headline item — sub-cent ticks — is spent; see Finding 4. What
remains:

- The zero-fee universe expanding materially **and** containing verified
  multi-leg partitions. Currently 14 series, 13 partitions.
- Any market in the deci-cent ∩ fee-free intersection pricing below par.
  Currently 61 markets, zero below par.
- A reduction in the 0.07 taker coefficient, or a change to the rounding rule.
- Median spread in the `linear_cent` segment compressing to ≤ 2¢.
- **Kalshi introducing maker rebates.** Makers are currently free, not paid. A
  rebate is a different economic object and would require separate analysis
  rather than an adjustment to this one.

A standing monitor tracks all five. Its alerts are prompts to re-read this
document, not to trade.

---

## Open items, never answered

Listed so a future reader does not mistake silence for absence.

- **The maker fee coefficient remains secondary-sourced.** The taker coefficient
  (0.07) is primary, from Kalshi's fee schedule as filed with the CFTC; the
  maker coefficient (0.0175) rests on agreeing secondary sources. The planned
  confirmation — resting one contract by hand and reading the charge — was not
  run. **This does not affect the conclusion**: maker fees are additive on the
  same taker coefficient, and the taker path is what was measured.
- **All-or-none execution mechanics were never tested.** Kalshi has no native
  all-or-none multi-leg order; batched submission is not atomic. The unwind-cost
  experiment was deferred and never run.
- **Jurisdiction and hosting.** KYC residency requirements and whether Kalshi
  permits sustained API access from cloud IP ranges were not resolved.

All three are moot under the close decision.

---

## Cost accounting

The investigation reached this answer in a single working session: two
exchange-wide sweeps at roughly 75 seconds each, about 15 MB of retained derived
data, and no credentials, accounts, or capital. Inference cost was not
instrumented — the per-component token accounting the design called for lived in
a later phase that was never built, and that omission is itself a finding about
where instrumentation should sit.

The counterfactual is the schedule the original plan specified: three days of
continuous capture, then three weeks of detector observation, then a dashboard,
then two weeks of paper execution — before reaching this same answer.

**That comparison is the argument for the phase-gate structure.** The decisive
measurements were cheap; they were simply not the ones the plan scheduled first.
The plan's ordering treated the fee schedule as the central risk and put
infrastructure ahead of measurement. The binding constraint turned out to be the
bid-ask spread, which one snapshot answers.

---

## This does not license a follow-on project

Resting orders are free on 98.7% of Kalshi series, and the tightest segments
quote at a 1¢ median. It is tempting to read that as a market-making
opportunity discovered along the way. **It is not a continuation of this thesis
and it does not inherit this project's evidence.**

- Different risk model. This project sought positions that are riskless by
  construction. Market making is compensated for adverse selection: your resting
  order fills precisely when someone informed wants the other side.
- **The observed tightness is evidence against, not for.** At a 1¢ tick with a
  1¢ spread, price improvement is impossible — there is no price between the bid
  and the ask. Competition reduces to queue priority against incumbents who are
  already there.
- The infrastructure built here — fee model, sweep tooling, correctness checks —
  justifies nothing. Sunk cost is not evidence.

Any such project requires its own case, built from zero.

---

## If the monitor fires

Re-read this document and re-derive from zero. Do not resume from here. The
measurements above describe the exchange as it was on 2026-08-03; an alert means
that description has stopped being true, which is a reason to redo the work
rather than to trust the parts of it that happen to still be committed.
