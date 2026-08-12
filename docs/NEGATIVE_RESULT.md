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

> **No verified partition has presented edge clearing 20%/yr annualized with
> capacity beyond the exchange's minimum quote. Observed maximum in the fee-free
> universe: 3.92%/yr on $0.021 of realisable profit.**

That is the finding, stated on the criterion the close decision actually used.

### The strongest observation the project has produced

`KXGDPYEAR-26`, measured 2026-08-11. It is worth setting out in full, because it
is the best case the exchange has offered in the entire monitored window:

| | |
| --- | --- |
| Legs | 14, verified exhaustive — tiles the line, no gap, no overlap |
| Tick structure | `deci_cent` on every leg — the finest on the exchange |
| Fee | `fee_multiplier: 0` on every leg — zero, not merely low |
| Σ asks | **97.90¢** — genuinely below par |
| Capacity at `size ≥ 1` | **1.00 contracts** — executable |
| Edge | 2.1¢ × 1 contract = **$0.021** |
| Horizon | 0.55 years |
| **Annualized** | **3.92%/yr** |

Every obstacle removed, the basket below par, and the trade executable. And it
returns **3.92%/yr against a 20%/yr decision threshold** — roughly
three-quarters of an order of magnitude short — on two cents of realisable
profit. Two of its binding legs quote exactly 1.00 contracts, the exchange's
minimum quotable unit.

Observed cost range across the monitored window, four observations per partition
(2026-08-03, 08-04, 08-05, 08-11):

| Partition | Legs | Range observed |
| --- | --- | --- |
| BTC price range, end of 2026 | 28 | 100.5¢ – 107.8¢ |
| ETH price range, end of 2026 | 18 | 106.0¢ – 114.5¢ |
| US GDP growth 2026 | 14 | **97.9¢** – 106.3¢ |

### Where the 20%/yr threshold comes from

**It is not new, and it was not chosen to accommodate this observation.** Without
that provenance a reader cannot distinguish this restatement from
goalpost-moving, and they would be right not to.

The Part 0 closing query — the analysis that produced the decision to close —
was built around a decision rule fixed in advance: *close unless something
survives a zero fee gate and annualizes above roughly 20%.* It is in the code
that ran that query:

```python
# src/research/fee_free_check.py
# Annualized-return threshold from the closing spec's decision rule.
ANNUALIZED_GATE = D("0.20")
```

Checkable in the history rather than asserted:

| | Commit | When |
| --- | --- | --- |
| `ANNUALIZED_GATE = 0.20` first committed | `58b1ab4` | 2026-08-03 18:21 UTC |
| Memo first written, price headline | `43fc327` | 2026-08-03 18:52 UTC |
| Headline restated on capacity | `5d9fd0b` | 2026-08-11 |

The threshold predates the first headline by half an hour and the second by
eight days. It is also the figure the standing monitor has used since it was
built, as the return that would have changed the close decision.

### The excursion is stronger evidence than its absence would have been

"These baskets never price below par" is an argument from absence. It is only
ever as strong as the observation window, and every additional day it survives
adds very little — while a single counterexample would end it.

"A basket went below par, was executable, and returned 3.92%/yr" is an
observation. The falsifying condition *occurred*, under the most favourable
circumstances the exchange offers, and the finding held anyway — because the
magnitude was nowhere near the threshold that would have changed the decision.
**The tripwire firing improved this result rather than damaging it.**

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
intersection — the baskets still price above par. See the opening table, and
the dated update beneath it.

**A fourth tick structure appeared on 2026-08-05**, which is what the falsifier
was built to catch, so it is recorded rather than folded in silently:

| Tick structure | Markets | Series | Spread range | Sub-1¢ |
| --- | --- | --- | --- | --- |
| `center_half_edge_half_cent` | 30 | `KXBRASILEIROGAME` | 1.0¢ – 9.0¢ | **0 of 30** |

Prices land on half-cent boundaries (60.5¢, 26.5¢, 34.5¢) and spreads take
half-cent values, so the tick is 0.5¢ — finer than a cent, coarser than the
deci-cent segment. **It does not move the finding**: its tightest observed
spread is 1.0¢, against a 0.6¢ median in the existing `deci_cent` segment, so
the most favourable location on the exchange is unchanged. Thirty
three-way football markets are also not where a partition arbitrage lives.

The trigger fired correctly and this is what it is for — the tick-structure set
changing is the one thing Finding 4 said would need re-examination.

### The falsifier track record

Worth stating explicitly, because a reader a year from now cannot reconstruct it
from the raw data and it changes how much weight this document deserves.

The original analysis listed conditions that would overturn the conclusion. **Two
of them have now occurred. The conclusion survived both.**

| Falsifier | Fired | What happened | Conclusion |
| --- | --- | --- | --- |
| Sub-cent tick sizes appear | 2026-08-03 | Already true — 12.6% of the exchange, and `tapered_deci_cent` puts its fine tick exactly in the tails where the fee gate is easiest to clear | survived |
| The tick-structure set changes | 2026-08-05 | `center_half_edge_half_cent` appeared, 30 markets on a half-cent grid | survived |
| A verified partition prices below par in the deci-cent ∩ fee-free segment | 2026-08-05 | `KXGDPYEAR-26` at 98.0¢, binding leg under one contract | survived |

**A falsifier list that has fired three times without moving the conclusion is a
materially stronger position than an untested one.** An untested falsifier list
is a promise; a fired one is evidence. The first two fired in the most favourable
locations the exchange offers — the finest tick, and then a *new* finer tick —
and the third fired on the exact condition named as the cleanest possible
counterexample.

None of this makes the conclusion permanent. It makes it *tested*, which is a
different and better claim than untested agreement with the data.

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

## Phantom liquidity is a cause, not a filter

This is the most consequential correction in the document for anyone repeating
the study, and it took two observations a day apart to see it.

| Observation | Cost | Binding leg size |
| --- | --- | --- |
| `KXGDPYEAR-33`, baseline 2026-08-03 | 91¢ — 9¢ below par | **0.01 contracts** |
| `KXGDPYEAR-26`, 2026-08-05 | 98¢ — 2¢ below par | **under 1 contract** |

Two deepest-discount observations in the fee-free universe. Both unbuyable. That
is not a coincidence, and treating it as one is the mistake.

**Kalshi quotes accept fractional size, down to 0.01 contracts.** The best offer
sets the displayed price whether it is backed by a thousand contracts or by one
hundredth of one. So a leg whose only resting offer is fractional contributes
its price to the basket total while contributing essentially no capacity — and
because such an offer costs almost nothing to leave sitting, it can sit at a
level a real offer would not.

The causality therefore runs the other way from the intuitive reading:

> **Fractional quoting does not merely fail to remove a below-par price. It is
> among the reasons the price is below par in the first place.**

The consequence generalizes past this venue:

> **In a market with fractional quote granularity, a price below par carries no
> information on its own.** The `size ≥ 1` gate is not prudence applied after
> the fact — it is a precondition for the price reading to mean anything. Any
> study of this exchange that reads prices without a size gate is measuring
> quoting minimums and reporting them as mispricings.

### At the minimum quote, capacity and quoting granularity are the same number

`KXGDPYEAR-26` on 2026-08-11 sharpens this rather than softening it. Two of its
binding legs quoted **exactly 1.00 contracts** — the exchange's minimum quotable
unit. The basket passed the `size ≥ 1` gate and was genuinely executable, for
$0.021.

So the gate admits the quoting minimum, which means:

> **`size ≥ 1` measures quoting minimums, not depth.**

That confirms the section rather than undermining it. The gate was introduced as
a *precondition* for a price reading to be meaningful — never as a sufficient
condition for tradeability — and this observation shows exactly why it cannot
carry a claim on its own. At one contract there is no way to tell a real offer
from a placeholder: capacity and quoting granularity are indistinguishable,
because they are the same number.

**The gate stays at 1.** Raising it to 2, or to any other value, would be tuning
a gate so that a claim survives, which is the failure this project has spent its
entire life avoiding — and it is the exact move Finding 5 exists to warn about.
The claim moves to magnitude instead; the gate stays where the data put it.

That is why the headline of this document is stated on annualized magnitude
rather than on price or on executability, and why Finding 5's capacity column is
the finding rather than a caveat to it.

### The same story as spread compression, from the other side

§ "First dynamics" shows below-par crossings arriving from the *width* of the
basket collapsing rather than from its fair value moving. This section shows the
deepest excursions arriving from *who is quoting* rather than from what the
structure is worth.

Both say the same thing about the same events: **below-par excursions in this
segment are artifacts of how the book is quoted, not signals about what the
contracts are worth.** One is about the spread's arithmetic, the other about the
size behind it. Neither leaves room for a reading in which the discount is
information.

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

**What it cost, concretely.** The highest-consequence trigger in the system was
silently dead, and an audit rather than a symptom found it.

The scheduled-fee-change endpoints are one of only *two* programmatic proxies for
a change to the 0.07 taker coefficient — the single structural change that would
most directly invalidate this document — and the coefficient itself lives in a
published PDF that no endpoint exposes. That trigger had two independent
failures, both of this shape:

1. A failed fetch was swallowed into an absent key, and an absent key is what a
   successful fetch of *nothing scheduled* also looks like. The failure path and
   the healthy path rendered identically.
2. The continuous scanner fetched the data and then **never passed it to the
   evaluator**. `monitor/run.py` passes it; `scanner/engine.py` did not. The
   trigger could not fire in the scanner at all, at any value.

No test caught either. Every test asserted the trigger did not error, and both
failures were errorless. The second was found only by asking the audit's
question of the whole path rather than of the handler — the fetch was always
fine, and the defect was one call site along.

**6. Two implementations of one pipeline, with the tests on the one that is not
production.** The second fee-change failure above is an instance of a class in
its own right, and the class is the more useful object.

`monitor/run.py` and `scanner/engine.py` both assembled the arguments to
`evaluate()` independently. The weekly job was the one the tests exercised; the
scanner is the one that runs 24/7. **A parameter with a permissive default that
one caller omits does not appear as a signature mismatch, does not raise, and
does not change any output on a fixture that lacks the relevant event.** Every
mechanism that normally catches a wiring error was blind to it.

Auditing the class rather than the instance found a second live case
immediately, in the other direction: `monitor/run.py` omitted `bands`, so the
*weekly* job's below-par classification never ran its band branch. Neither
implementation was wrong; they simply disagreed, and nothing was looking at the
disagreement.

The fix is structural rather than diligent. Both callers now go through one
`pipeline.assess`, the arguments are assembled inside it, and `bands` is not a
parameter at all — a band argument a caller can forget is exactly the defect.
What cannot be unified is checked directly: a test compares the *keyword sets*
each call site passes, because equal outputs on one fixture would not have
caught either bug.

Three standing questions now, because none implies the others:

> **Would a failure in this path be visible, or merely logged?**
>
> **Has this trigger ever been shown to fire, or only shown not to error?**
>
> **Which code path does production run, and is that the one under test?**

The third belongs in this section rather than beside it: the test suite is an
instrument, and it was measuring the wrong subject.

Every trigger now has a pair of tests: fires exactly at its stated threshold,
silent one step inside it. Before that, the fee-free-series trigger's only
coverage moved it by 29 against a documented threshold of 3.

**6. And the inversion, which is the same insight paying out.** `bid(YES) +
bid(NO) > 100¢` is impossible in a correctly reconstructed book. That makes it
worthless as an opportunity detector — and therefore *valuable* as a continuous
correctness check on our own ingest: observing it means our book is wrong, not
that the market is. See the venue notes. That reading is only available once you
have asked what a check's own failure looks like.

Three of the seven are inside the monitor's own correctness and safety machinery,
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

**7. A headline stated on a proxy, twice, falsified by the proxy both times.**
This one is not about a check at all. It is about what a document claims, and it
is the most easily repeated of the set.

The headline of this memo has been stated three times:

| | Claim | Outcome |
| --- | --- | --- |
| 1 | All three fee-free partitions price **above par** | Falsified 2026-08-05 — `KXGDPYEAR-26` at 98.0¢ |
| 2 | None presents **executable edge at `size ≥ 1`** | Falsified 2026-08-11 — same partition, 97.9¢, 1.00 contracts |
| 3 | None clears **20%/yr annualized** beyond the minimum quote | Stands; observed max 3.92%/yr |

**Neither falsification touched the finding.** The project's conclusion did not
move on either date, and no threshold was adjusted to keep it — the 20%/yr rule
was written into the Part 0 closing query before the memo existed.

The reason both failed is the same, and it is worth naming:

> **A headline stated on a proxy will be falsified by the proxy rather than by
> the finding.** Price and executability were both convenient stand-ins for a
> decision rule that already existed in writing. Each was easier to state, and
> each was a strictly weaker claim than the rule it stood for.

The generalization:

> **When a decision rule has been written down, the headline belongs on the
> rule.** Anything else is a presentational convenience that will need an
> asterisk within weeks — and the asterisk is what makes a correct result look
> like a retreating one.

The tell is available in advance, before any falsification: **if the headline
and the decision rule are different sentences, the headline is a proxy.** That
was true here from the first draft and nobody noticed, because a proxy claim is
usually the more vivid one — "costs more than the dollar it is guaranteed to
pay" reads better than "below the annualized threshold the close decision used".
Vividness is exactly what makes it tempting and exactly what makes it fragile.

Item 5 is a separate sub-class, and the reason it needs its own name is that
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

### The same family: a framing that determines its answer

The growth projection produced **11 days or 185 days from the same two sweeps**,
differing only in whether the growth was attributed to a component that can
accumulate.

Market count rose 9.8% in 8.2 hours. Bucketed by time to resolution, 97.1% of
that was in markets closing within seven days — which cannot accumulate, because
each one leaves the window it is counted in. Little's Law on that sub-population
(`L = λW`, with `W ≤ 7 days` true by construction) gives an implied residence of
1.12 days that reproduces the observed count: the component was already at its
plateau and the window had caught a listing burst. The part that compounds grew
at 1.9%/day, not 31.6%.

> **A headline growth rate over a population containing a bounded component is
> not a growth rate.** It is a weighted average of a rate and a plateau, and the
> weight is whatever the sampling window happened to catch.

Neither figure is a measurement error. Both are arithmetic on the same two
sweeps. The choice of denominator is doing all the work, and nothing in the
number itself announces that — which is what makes it the same family as the
noise-floor case rather than a separate lesson.

Every derived date in `docs/DEPLOY.md` carries its observation count for this
reason. **n = 1** sits next to 185 days, and the whole-population rate from the
*other* pair on record is 10.8%/day rather than 31.6%, so the headline is not
stable across the two observations that exist.

### An absolute count on a growing population is a moving target

A third instance, and it failed faster than either of the others.

The scheduled-fee-change trigger fires only on *material* changes, and one
criterion was breadth: material if the batch touched more than **15 series**. The
figure came from a single observed batch of 11. **Six days later the same routine
MLB batch spanned 19 series** and the trigger began firing every sweep — on
exactly the routine noise the criterion existed to exclude.

The threshold was not wrong by a little. It was the wrong *kind* of quantity:

> **On an exchange whose market count moves on the order of 10% a day, every
> absolute threshold is a moving target.** It will fail on the timescale of the
> growth, not on the timescale of the phenomenon it was chosen to describe.

The replacement is a proportion — more than 10% of the *active* series in any one
category — which is invariant to the population growing underneath it. The two
observed batches are 1.47% and 2.54% of active Sports series, both comfortably
routine, and the same 19 series would be 95% of a 20-series category and
therefore news. **Identical count, opposite meaning**: precisely what a
proportion distinguishes and a count cannot.

Thresholds on this exchange should be proportions or rates. The `$25`
capacity × edge floor and the `size ≥ 1` gate are the deliberate exceptions —
both are absolute because they denominate in money and contracts, which do not
inflate with listing volume.

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
