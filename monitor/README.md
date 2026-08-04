# Standing monitor

The project is closed. See [`../docs/NEGATIVE_RESULT.md`](../docs/NEGATIVE_RESULT.md).

This package exists to notice if the exchange stops matching the description in
that memo. It is instrumentation, not a trading system, and it must never
become one.

## Hard constraints

1. **No credentials on disk. No authenticated endpoints. No order-placing code
   in this package, ever.** Enforced by `tests/test_monitor.py`, which greps the
   package for order and auth references and fails on any hit.
2. Read-only public market data only.
3. If a future change appears to require authentication, that is a new project
   and a new spec — not an edit here.
4. Runs unattended for months. Optimised for cheapness and silence.

Two rules exist because breaking them produced wrong answers during the
investigation:

- **Annualized return is never computed for an unverified structure.** Nested
  cumulative horizons read as partitions annualize at 884% and 4,367%. A number
  that large in a log file gets screenshotted and believed by someone without
  the context. `checks.annualized_return` takes `verified` as a required
  argument and returns `None` when it is False.
- **The size gate is `>= 1` contract everywhere.** Kalshi contracts trade down
  to 0.01, so a leg can display an offer worth nine hundredths of a cent.

A third rule was added after the monitor's first live alert:

- **When building a check, ask whether the check shares a failure mode with what
  it checks.** Partition verification once inferred its own granularity from the
  joins it was inspecting, and certified a partition with a hole; the dashboard's
  staleness banner reported an age that froze when the feed froze. See
  [`../docs/NEGATIVE_RESULT.md`](../docs/NEGATIVE_RESULT.md) § "The one pattern
  behind every broken check".

## Relationship to the live scanner

`scanner/` and `dashboard/` render this same logic continuously — they import
`monitor.checks`, `monitor.metrics` and `monitor.alerts` rather than
reimplementing them, so there is one definition of a verified partition and one
set of thresholds. This package remains the durable half: the weekly job writes
the committed archive and never depends on the always-on box. See
[`../docs/DEPLOY.md`](../docs/DEPLOY.md).

## Running it

```bash
python -m monitor.run                                  # sweep, archive, compare, alert
python -m monitor.run --from monitor/snapshots/<dir>   # recompute from a stored snapshot
python -m monitor.run --write-baseline                 # regenerate baseline.json
```

Exit code is 1 when something fired, 0 when the baseline holds — so a scheduler
can treat it as a check. Weekly is the intended cadence:

```
0 12 * * 1  cd /path/to/Market-Scanner && .venv/bin/python -m monitor.run
```

Every run appends a snapshot and its computed metrics to `data/monitor/`
(gitignored). **Record everything, alert on little.** The archive is the asset;
a year of weekly spread distributions answers questions the alerts cannot.

## What fires

| Trigger | Threshold |
| --- | --- |
| Tick structure set changes, or any structure's share moves ≥ 5pp | any |
| Unrecognised `fee_type` (including a maker *rebate*) | any |
| Exchange publishes scheduled fee changes | any |
| `linear_cent` median spread | ≤ 2¢ |
| Fee-free series with open markets | change ≥ 3 |
| Verified partition below par and tradeable | see below |
| Any deci-cent ∩ fee-free market below par | any |

Every alert carries the trigger, the baseline value, the current value, the
relevant memo section, and the line *"An alert is a prompt to re-read the memo,
not to trade."*

### Why the below-par trigger is not "any"

The spec called for firing on any below-par verified partition with capacity
≥ 1 contract. **The baseline already contains one** — KXGDPYEAR-29, 95¢, 10
contracts, 1.47%/yr — which the closing analysis examined and dismissed on
return and capacity. Firing on "any" would page every week about a known
non-opportunity, which is precisely the failure mode this monitor is designed to
avoid.

`alerts.classify_below_par` therefore has three routes through, in order of
authority:

1. **Annualized ≥ 20%/yr pushes regardless of size.** This is the return that
   would have changed the close decision, and the branch is deliberately
   *unfloored*: a genuinely high-return structure is news at any capacity.
2. **A new structure pushes only if capacity × edge ≥ $25.** The floor was added
   on live evidence, not on taste — the first real alert, `KXGDPYEAR-28` at 98¢
   on 15 contracts, was worth **$0.30**, and § "First dynamics" in the memo
   shows these baskets moved a median of 5¢ over 33 hours against a 2¢
   excursion. Below par alone is drift noise.
3. **Falling outside a series' own observed band counts as new.** A series that
   has always oscillated between 95¢ and 105¢ has done nothing novel by printing
   98¢.

### Bands, and an honest cold start

A band computed from two observations is two points with a line through them. So
a series with fewer than `history.MIN_OBSERVATIONS_FOR_BAND` (8) observations
reports `UNKNOWN`, never claims an observation is outside it, and falls back to
the dollar floor alone. The trigger board shows the state and how many more
observations are needed. Bands tighten as the archive grows; nothing back-fills,
interpolates, or assumes a distribution.

### Suppression applies to the push, never to the record

Every sub-floor detection is written to `data/monitor/suppressed.jsonl` with its
reason, and surfaced on the trigger board as a rolling 90-day count broken down
by reason. **A spike in that count is a signal even when no individual detection
clears the floor** — which is the guard against a threshold quietly hiding a
real change while the monitor still looks healthy.

## What it cannot see

The taker coefficient (0.07) and the rounding rule live in a published PDF, not
the API. They cannot be read programmatically. The available proxies are
`fee_type` / `fee_multiplier` per series and the exchange's scheduled-fee-change
endpoints, both of which are tracked. **A silent change to the coefficient
itself would not fire an alert** — if one of the other triggers fires, re-read
the published schedule by hand.

## The baseline and the gate

`baseline.json` is the immutable description of the exchange on 2026-08-03. It
is regenerated only deliberately, and regenerating it is how you accept a new
normal — not something to do to silence an alert.

`snapshots/` holds the two committed sweeps the baseline derives from, so the
gate is checkable from a fresh clone: **one full run must reproduce the baseline
byte-for-byte.** If it cannot reproduce the baseline it cannot detect change.
`tests/test_monitor.py` asserts this, ignoring only `annualized_pct`, which
moves with the clock because it depends on time-to-close.

Note the deliberate duplication: `research/snapshots/` backs
`SPREAD_STUDY.md` in the Phase 0.5 schema, and `monitor/snapshots/` backs
`baseline.json` in the richer monitor schema, which adds tick structure and
strike geometry. Both are kept because each makes a published document
auditable on its own.

## Test fixtures

The suite ships the Phase 0.5 false-positive history as fixtures: KXDEELRIP-40,
the LA-01 listed subset, the nested cumulative horizons, both sports-ladder
artifacts, the float-equality cases, fractional-size quotes, and RFQ shells.
Every one must be rejected. If a change lets one through, the change is wrong.
