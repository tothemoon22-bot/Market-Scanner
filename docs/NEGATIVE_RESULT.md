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

## First dynamics — what the below-par crossings actually are

Findings 1–5 are a still photograph. This section is the first motion, and it
changes how a below-par print should be read. Added 2026-08-04, after the
population reconciliation in
[`research/RECONCILIATION.md`](../research/RECONCILIATION.md) confirmed the two
sweeps describe the same universe rather than two different filters.

**Three observations, not a time series.** t0 2026-08-03T07:06Z, t1 07:10Z
(+4 minutes), and one live sweep 2026-08-04T16:22Z (+33.3 hours). Everything
below is a two-point difference over 33 hours. It is not weekly data and should
not be quoted as if it were.

### The magnitudes

All 13 fee-free verified partitions, Σask at t0 versus live:

| | Value |
| --- | --- |
| Median absolute move | **5.0¢** |
| Largest move | 18.0¢ (`KXGDPYEAR-27`, 144¢ → 126¢) |
| Unchanged | 2 of 13 |
| Below par | 2 → **3** |
| Below par *and* tradeable at size ≥ 1 | 1 → **2** |

The single new entrant is `KXGDPYEAR-28`, 103¢ → 98¢ — the transition that
fired the monitor's first live alert.

**The ordinary 33-hour drift of these baskets is 5¢. The entire below-par
excursion that triggered the alert is 2¢.** The signal is smaller than the
noise it sits in.

### The mechanism is spread compression, not repricing

Σask fell by a median of 3¢ while Σbid *rose* by a median of 7¢. Both sides
moved toward each other, which is not a change of view — it is the basket's own
bid-ask collapsing:

| Partition | Basket width t0 | Basket width live |
| --- | --- | --- |
| `KXGDPYEAR-27` | 83¢ | 30¢ |
| `KXGDPYEAR-31` | 46¢ | 24¢ |
| `KXGDPYEAR-30` | 36¢ | 14¢ |
| `KXGDPYEAR-28` | 29¢ | **14¢** |

Seven of the eleven GDP baskets now sit at a width of exactly 14.00¢ — fourteen
legs at a 1¢ tick, the tightest a 14-leg basket on a cent grid can possibly be.

For `KXGDPYEAR-28` specifically, the basket's midpoint *rose* 2.5¢ (88.5¢ →
91.0¢) over the same interval in which its ask sum fell 5¢. The market moved up
and the ask moved down, because the half-width the ask carries above the mid
shrank from 14.5¢ to 7¢. Nothing was revalued.

### The noise floor is set by the tick

A basket's width cannot fall below `N × tick`, so a 14-leg partition on a cent
grid can never quote tighter than 14¢, and its ask sum sits at least 7¢ above
its own midpoint. That half-width is not a fixed offset. It moves whenever the
width moves, and it drags Σask with it at no cost in opinion.

`KXGDPYEAR-28` is the worked case. Its width halved between the sweeps, 29¢ →
14¢, which lowers Σask by 7.5¢ on its own. Its midpoint over the same interval
*rose* 2.5¢. Net −5¢, and a crossing of par. **All of the crossing came from the
width; the market's own estimate moved the other way.** Against that mechanism
the excursion is 2¢ — under a third of the basket's half-width.

The general form holds for every below-par observation to date: **the basket's
own bid-ask is wider than its distance below par.** At t0, `-33` sat 9¢ below
par on a 16¢-wide book and `-29` 5¢ below on a 14¢ book; at the live sweep,
`-28` 2¢ below on 14¢, `-29` 5¢ below on 14¢, `-33` 5¢ below on 15¢.

One of those does exceed *half* its width, and it is worth naming rather than
smoothing: `KXGDPYEAR-33` at t0, 9¢ below par against an 8¢ half-width. It is
also the partition with **0.01 contracts** of capacity — the phantom-liquidity
case from Finding 5, and the reason the size gate is `≥ 1` rather than `> 0`.
The gate removes it before the noise floor has to.

This is Finding 1 arriving from the other direction. There the per-leg spread
set the threshold a mispricing had to clear to become visible; here the same
quantity sets the amplitude with which the ask sum wanders across par for free.

### The conclusion

> **Below-par excursions are a liquidity artifact, not a mispricing signal.**
> When a basket crosses par, the mechanism is a narrowing spread converging on a
> nearly unchanged fair value — which cannot create value, only reveal where the
> market already was.

Said the other way: a verified partition printing below par is drift noise, not
emergent edge. Crossing par is the expected behaviour of a basket whose own
spread is wider than its distance to par, which describes every one of these.

### What this closes

The last standing objection to the negative result was that a two-snapshot study
could miss edge that only appears dynamically — that the exchange might be
efficient in a photograph and inefficient in motion, and that the whole
investigation had simply never looked.

**It doesn't, and now the dynamics have been measured rather than assumed.** The
below-par crossings that a dynamic study was supposed to catch are exactly the
events observed here, and the mechanism generating them is structurally
incapable of producing edge:

- Spread compression moves Σask toward par **without moving fair value**. The
  half-width the ask carries above the midpoint shrinks; the midpoint stays
  roughly where it was. Nothing is created, only revealed.
- The magnitude available from the mechanism is bounded by the basket's own
  width, which is bounded below by `N × tick`. That is the same quantity
  Finding 1 identified as the binding constraint. A dynamic edge would have to
  come from somewhere other than the spread, and the spread is what moved.
- The direction is unhelpful too. Compression is convergence: it narrows the
  window in which a stale quote could be picked off, rather than widening it.

So the still photograph and the motion agree, and they agree for the same
structural reason rather than by coincidence. That is the strongest form the
negative result takes: **the objection was tested on its own terms and the
mechanism it hoped for turns out to be the mechanism that forecloses it.**

Two consequences for the monitor, both already implemented:

- Below par is not by itself a reason to wake anyone. What carries the alert is
  capacity × edge clearing the dollar floor, or an annualized return clearing
  the threshold that would have changed the close decision. `KXGDPYEAR-28` is
  worth $0.30 in total and clears neither.
- Drift is not a weekly phenomenon. The t0/t1 pair, **four minutes apart**,
  leaves 12 of 13 partitions untouched but already contains a 3¢ move
  (`KXGDPYEAR-30`, 116¢ → 113¢) on a market resolving in 2029 — one four-minute
  step worth more than the entire alerting excursion. Any future claim that the
  below-par count is trending has to beat that, and it needs an archive rather
  than three sweeps.

What would overturn this section is a below-par excursion exceeding the basket's
own full width, or a below-par count that rises while widths stay constant.
Neither has been observed — including for `KXGDPYEAR-33`, which exceeds half its
width but not its width. Both conditions are asserted in `tests/test_drift.py`
against the committed snapshots, so the section fails loudly rather than
quietly. The monitor records every sweep, so the question is answerable later
from an archive rather than from these three points.

Regenerate with `python -m src.research.drift`; the numbers above come from
`research/drift.json`, not from prose. The t0/t1 snapshots are committed under
`monitor/snapshots/`; the live sweep's raw pages are not, because `data/` is
gitignored — so `drift.json` is the committed record of that comparison.

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

## The one pattern behind every broken check

The section above is the market-side generalization. This is its instrument-side
twin, and it is arguably the more transferable of the two, because it does not
depend on prediction markets existing.

> **When building a check, ask whether the check shares a failure mode with the
> thing it checks.**

Every check that broke during this project broke that way. Not one broke because
its logic was wrong in isolation.

**1. A gap detector with a gap in it.** Partition verification inferred the
tiling granularity from the joins it observed. With only two interior buckets
there is exactly *one* join, so any value is trivially "consistent" with itself
— a 0.05 hole simply reads as a 0.06 granularity, and the checker certifies a
partition that does not tile. The fix was to stop inferring the standard from
the data under test: the step is now pinned to the grid the strikes are
themselves quoted on (`_quoted_grid`). A gap detector that derives its notion of
"no gap" from the gaps it is inspecting cannot detect the case where there is
only one.

**2. A freshness monitor that went stale.** The dashboard's offline banner
reported the age of the last heartbeat it had received. When the feed froze,
that number froze with it — a 64-second outage displayed as "21s ago", because
the age itself stopped advancing. The banner whose entire job is to say *this
data is old* was, itself, old data. Fixed by taking `max(client silence,
reported age)`, sourcing the clock from the client rather than from the frozen
payload; detection time fell from 64s to 26s.

**3. A test that matched itself.** A guard scanning the dashboard source for
placeholder markers matched the word "placeholder" in its own explanatory
comment, and passed for the wrong reason. Another matched `bar` against the
progress-bar CSS class. A check written in the same language as its subject,
scanning a corpus that includes itself, is the degenerate case of the same
error. Fixed by stripping comments before scanning and narrowing the patterns —
after which the guard immediately caught a real regression it had been blind to.

**4. A maturation check that measured breadth and reported it as time.** The
monitor's per-series bands require eight observations before they claim to
describe a range. The history ledger writes one row per partition per sweep, and
`KXGDPYEAR` lists eleven years — so the *first* sweep wrote eleven rows, the
band counted rows, and it declared itself established with a "range" of
90¢–118¢ that was a cross-section of eleven different contracts at one instant
rather than one contract over eleven moments. The cold-start guard that existed
specifically to prevent a band from being asserted too early was the thing
asserting it. Caught by running the instrument against live data rather than
fixtures. An observation is now a distinct capture time for a distinct event,
and the panel shows sweeps, rows and contracts separately so breadth cannot be
read as time again. Shipped as a permanent fixture in the false-positive suite.

**5. Defensive handling that converted a hard failure into a silent one.** This
is a distinct sub-class and the hardest of the set to catch, because unlike the
others it was produced by a *correct* instinct rather than a modelling error.

The population reconciliation runs inside `try/except` so that a reconciliation
failure cannot cost a sweep. That is right, and it stays. But the ledger
filename was parsed with `Path.stem`, which strips one suffix and leaves
`...Z.csv` on a `.csv.gz` name — so `strptime` would have thrown on every sweep,
been logged, and been swallowed. The panel would have read **"needs two sweeps
to compare"** indefinitely while the scanner reported healthy. A permanently
broken subsystem was indistinguishable from a normal empty state.

Nothing about the handler is wrong. The defect is that the *consequence* of
catching had no representation: a caught exception left the caller unable to
distinguish "ran and found nothing" from "did not run". Review does not catch
this, because the handler reads as good practice on the line where it appears
and the damage is at a call site that looks fine.

The fix is not to stop catching. It is to make catching produce a state:

- every swallowing handler increments a named failure counter with a timestamp
  and exception type, surfaced on the health panel; a subsystem with no
  failures renders a confirmed zero rather than being absent
- empty states name their cause, and *computed-empty*, *not-yet-run* and
  *failed-run* are three distinct renderings, not one
- a subsystem failing three consecutive sweeps pushes on its own account, with
  no detection threshold involved

Standing question, added alongside the one above: **would a failure in this path
be visible, or merely logged?**

**6. And the inversion, which is the same insight paying out.** `bid(YES) +
bid(NO) > 100¢` is impossible in a correctly reconstructed book. That makes it
worthless as an opportunity detector — and therefore *valuable* as a continuous
correctness check on our own ingest: observing it means our book is wrong, not
that the market is. See the venue notes. That reading is only available once you
have asked what a check's own failure looks like.

Three of the six are inside the monitor's own correctness and safety machinery,
which is the uncomfortable part: the code written specifically to stop the
project fooling itself is the code most prone to it, because it is written
against the same mental model as the thing it guards.

Items 1–4 share one structure: **the check drew its standard of correctness
from the same source as the thing it was checking.** The defence is to source
the standard independently — the grid from the quoting convention rather than
from the observed joins, the clock from the client rather than from the payload,
the corpus from something that excludes the checker.

The family is larger than these four and worth naming so it is recognisable on
sight: freshness monitors that go stale, gap detectors with gaps, consistency
checks that are vacuously consistent, coverage tools that do not cover
themselves, secret scanners that log the secret they found, retry logic that
retries the health check that decides whether to retry.

Item 5 is the separate sub-class, and the reason it needs its own name is that
the first structure does not describe it. Nothing there drew a bad standard —
the handler was correct. **The instrument was made silent by a decision to be
robust.** Both questions therefore have to be asked, because neither implies the
other:

> Does this check share a failure mode with what it checks?
>
> Would a failure in this path be visible, or merely logged?

Both are standing rules now; see `README.md` § "Hard constraints".

---

## A measurement note: when the noise floor is the signal

Recorded because it produced a confidently wrong number and was caught only by
disagreeing with a second method.

Projecting the scanner's memory against market count, the first attempt fitted a
growth model to **RSS deltas**. Across a 4× range in market count the total moved
**2.6 MB with ±0.9 MB residuals** — so the residuals were the same order as the
signal, and the fit was reading allocator arena reuse rather than retention. It
extrapolated to a ceiling at *15 million markets*, which is not a number about
Kalshi.

Direct byte counting over the same range gave **404 MB per million markets with
±1.8 MB residuals against 21 MB of signal** — a signal-to-noise ratio an order of
magnitude better, and a projection that survives contact with the anchor
measurement.

> **When a measurement's noise floor is the same order as its signal, the fit
> describes the instrument rather than the subject.**

This belongs with the section above rather than beside it. The failure is not
that RSS is the wrong quantity — RSS is exactly the quantity that matters, and
it is still what anchors the projection. The failure is using a *method* whose
resolution was never checked against the effect being measured. The defence is
the same shape as the others: get the number a second way, from a source with
different failure modes, before believing the first.

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
