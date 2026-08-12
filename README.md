# Market Scanner

A research instrument, not a trading system. It exists to answer one empirical
question:

> Does a fee-adjusted, executable pricing inconsistency exist on Kalshi at our
> latency, and how often?

"No" is a successful outcome. Parameters do not get tuned until the answer
becomes yes.

We hunt **internal mathematical inconsistencies** in Kalshi's own prices — sets
of contracts that cannot all be correctly priced simultaneously regardless of
what happens in the world. Nothing here forecasts anything. Binance and Coinbase
are reference data only and are never traded.

## Status

> # CLOSED — no exploitable edge found
>
> **Read [`docs/NEGATIVE_RESULT.md`](docs/NEGATIVE_RESULT.md) first.** It is
> written to be read without this repository.
>
> The cleanest single result: 61 markets exist where both obstacles vanish at
> once — a 0.1c tick instead of 1c, and a fee of exactly zero. The three
> verified-exhaustive partitions among them price at 105.2c, 107.8c and 100.3c.
> Every one costs more than the dollar it is guaranteed to pay. The thesis fails
> even where all known obstacles are removed.
>
> Phases 1–5 were never built. A standing monitor in [`monitor/`](monitor/)
> watches for the conditions that would change the conclusion, and a continuous
> scanner ([`scanner/`](scanner/)) with a phone dashboard
> ([`dashboard/`](dashboard/)) renders that watch live. **An alert is a prompt to
> re-read the memo, not to trade.**

| Phase | State |
| --- | --- |
| 0 — Research | Complete. [`docs/PHASE0_REPORT.md`](docs/PHASE0_REPORT.md) |
| 0.5 — Kill-shot experiments | Complete. [`research/PHASE05_REPORT.md`](research/PHASE05_REPORT.md) |
| Closing query | Complete. Fee-free universe re-tested with the fee gate removed |
| 1–5 | **Not built. Closed before capture began.** |

## Hard constraints

These held throughout and still hold. **No order-placing code was ever written**
— `src/execution/` is empty by construction and `monitor/` is grep-tested to
stay that way. Nothing in this repository has ever authenticated to Kalshi or
held a credential; every measurement here came from public endpoints.

1. Read-only. No order-placing code, no credentials, no authenticated calls.
2. No strategy acts on a "riskless" basket without a verified partition.
   Exhaustiveness is never inferred from series structure, title, or the venue's
   `mutually_exclusive` flag.
3. Secrets would live in `.env` (gitignored) or the OS keychain. None were ever
   needed. `tools/secret_scan.py` runs pre-commit regardless.
4. **When building a check, ask whether the check shares a failure mode with
   what it checks.** Freshness monitors that go stale, gap detectors with gaps,
   consistency checks that are vacuously consistent. The defence is to source
   the standard of correctness from somewhere other than the thing under test.
5. **Ask whether a failure in this path would be visible, or merely logged.**
   A `try/except` that keeps a subsystem from killing a sweep is right, and it
   is also how a permanently broken subsystem comes to look like a normal empty
   state. Catching must produce a state: a named failure counter, an empty that
   says which of *computed-empty*, *not-yet-run* or *failed-run* it is, and a
   push when a subsystem is down for three sweeps running.

6. **Ask whether a trigger has ever been shown to fire, or only shown not to
   error.** Every threshold gets a pair: fires at the stated boundary, silent
   one step inside it. The fee-change trigger was dead twice over — once
   swallowed, once never passed to the evaluator — and every test it had
   asserted only that it did not raise.

7. **Ask which code path production runs, and whether that is the one under
   test.** Two implementations of one pipeline, with the tests on the one that
   is not production, is how a trigger stays dead through a green suite. Both
   callers now go through one `pipeline.assess`, and a test compares the keyword
   sets each passes — equal outputs on one fixture would not have caught it.

8. **Ask which environment production installs into, and whether that is the one
   under test.** The suite was green on every commit while `pip install -e .` on
   a clean box could not import the dashboard: `fastapi` and `uvicorn` were
   imported but never declared, and present in the dev environment by accident.
   No test that shares the developer's interpreter can see this.
   `tests/test_clean_install.py` builds a virtualenv from the declared metadata
   alone and imports every shipped module with it — including imports nested
   inside functions, which is where `uvicorn` was hiding.

   All five questions, in [`docs/NEGATIVE_RESULT.md`](docs/NEGATIVE_RESULT.md)
   § "The one pattern behind every broken check".

## Start here

- [`docs/NEGATIVE_RESULT.md`](docs/NEGATIVE_RESULT.md) — **the finding.** Read
  without the repo; everything else is supporting evidence
- [`monitor/README.md`](monitor/README.md) — the standing monitor and what would
  reopen the question

Supporting evidence, in the order it was produced:

- [`docs/PHASE0_REPORT.md`](docs/PHASE0_REPORT.md) — the fee finding, and
  Detector 1's structural impossibility
- [`docs/COST_MODEL.md`](docs/COST_MODEL.md) — fee thresholds, generated from
  the fee model
- [`research/SPREAD_STUDY.md`](research/SPREAD_STUDY.md) — the exchange-wide
  spread distribution that turned out to be the binding constraint
- [`research/PHASE05_REPORT.md`](research/PHASE05_REPORT.md) — the five
  kill-shot experiments
- [`research/RECONCILIATION.md`](research/RECONCILIATION.md) — baseline versus
  live population, reconciled by set difference rather than by counts
- [`research/drift.json`](research/drift.json) — partition drift between sweeps,
  the data behind the memo's "First dynamics"
- [`docs/venues/kalshi/fees.md`](docs/venues/kalshi/fees.md) — fee evidence trail
- [`docs/venues/kalshi/README.md`](docs/venues/kalshi/README.md) — API notes

## Layout

```
docs/NEGATIVE_RESULT.md   the finding
scanner/                  continuous read-only scanner: guard, triggers, funnel
dashboard/                FastAPI + mobile PWA rendering the falsification surface
monitor/                  standing monitor: baseline, checks, alerts, snapshots
research/                 spread study, Phase 0.5 report, sweep snapshots
src/
  venues/kalshi/fees.py   the fee model — Decimal only, floats raise
  research/               the sweeps and analyses the reports are generated from
  detectors/              EMPTY — never built
  execution/              EMPTY — never built, by construction
  risk/ dashboard/ storage/   EMPTY — never built
docs/venues/              venue research, vendored API specs and CFTC filings
tests/                    fee model, plus the Phase 0.5 false positives as fixtures
tools/                    secret scanner
```

The empty directories are deliberate and are left in place: a reviewer can
verify the read-only constraint by looking at them.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pre-commit install
.venv/bin/python -m pytest
```

No credentials are required, then or now. Kalshi's public market data is
unauthenticated.

## Regenerating the reports

```bash
.venv/bin/python -m src.research.cost_model        # docs/COST_MODEL.md tables
.venv/bin/python -m src.research.spread_study      # research/SPREAD_STUDY.md
.venv/bin/python -m src.research.fee_free_check    # the closing query
.venv/bin/python -m monitor.run --from monitor/snapshots/20260803T070632Z_t0
.venv/bin/python -m src.research.drift \
  --a monitor/snapshots/20260803T070632Z_t0 \
  --b monitor/snapshots/20260803T071019Z_t1   # research/drift.json, "First dynamics"
```

Every table in the documentation is generated, not hand-typed. Change the model,
re-run, paste.

## Watching it

```bash
.venv/bin/python -m dashboard.app                      # live
.venv/bin/python -m dashboard.app --snapshot monitor/snapshots/20260803T070632Z_t0
```

Mobile-first, installable as a PWA. The hero is not P&L — it is the
falsification surface: actionable count, the trigger closest to firing, and how
long the window of observation actually is. Deployment, cadences, and the two
things it deliberately does not do are in [`docs/DEPLOY.md`](docs/DEPLOY.md).

**ntfy pushes, the PWA browses.** The dashboard does not solve notification
transport — foreground-only Web Notifications cannot wake a phone, so they were
removed rather than left half-built. Set `NTFY_TOPIC` and the scanner pushes
fired triggers plus a monthly heartbeat.

**No panel displays a number the system does not measure.** Anything unmeasured
renders an explicit NO DATA state; `0 actionable` and `scanner offline` are
visually unmistakable for one another. Tests enforce this mechanically —
placeholder markers, stray numeric literals, credentials and order-placement
references all fail the suite.

## A note on continuation

Resting orders are free on 98.7% of Kalshi series and the tightest segments
quote at a 1¢ median. That is **not** a market-making opportunity this project
discovered — it is a different thesis with a different risk model, and the
observed tightness is evidence against it rather than for it. See the final
section of [`docs/NEGATIVE_RESULT.md`](docs/NEGATIVE_RESULT.md). The
infrastructure here justifies nothing.
