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

| Phase | State |
| --- | --- |
| 0 — Research | **Complete, awaiting gate approval.** See [`docs/PHASE0_REPORT.md`](docs/PHASE0_REPORT.md) |

> Phase 0 finding that changes the Phase 2 scope: **Detector 1 (single-market
> complementary) is structurally impossible, not merely unprofitable.** Kalshi
> runs one book per market, so `ask(YES) + ask(NO) < 100c` is the same condition
> as `bid(YES) + bid(NO) > 100c`, which the matching engine crosses on sight.
> Confirmed against 190 live books. Detectors 2–5 compare distinct markets with
> distinct books and are unaffected. Detail in
> [`docs/PHASE0_REPORT.md`](docs/PHASE0_REPORT.md) section 0.

| 1 — Read-only data pipeline | Not started |
| 2 — Detectors (observe only) | Not started |
| 3 — Dashboard | Not started |
| 4 — Paper execution (demo only) | Not started |
| 5 — Go / no-go review | Not started |

Phases are worked in order and each stops at a gate for explicit approval. Do
not build ahead.

## Hard constraints

1. Phases 0–3 are read-only. **No order-placing code exists in this repo until
   Phase 4** — `src/execution/` is empty by construction.
2. Phase 4 places orders against the Kalshi **demo environment only**.
   Production credentials must not be loadable by the application; a startup
   assertion enforces it.
3. No strategy acts on a "riskless" basket without passing the Phase 2
   exhaustiveness check. Exhaustiveness is never inferred from series structure
   or title.
4. Secrets live in `.env` (gitignored) or the OS keychain. Never in logs or
   error messages. `tools/secret_scan.py` runs pre-commit.
5. Every order carries an idempotent client order ID.

## Start here

- [`docs/PHASE0_REPORT.md`](docs/PHASE0_REPORT.md) — the fee finding and what it
  does to the opportunity set
- [`docs/COST_MODEL.md`](docs/COST_MODEL.md) — every detector threshold derives
  from this file
- [`docs/venues/kalshi/fees.md`](docs/venues/kalshi/fees.md) — evidence trail
- [`docs/venues/kalshi/README.md`](docs/venues/kalshi/README.md) — API notes

## Layout

```
src/
  venues/kalshi/      REST + WS client, fee model, taxonomy
  venues/reference/   binance.py, coinbase.py — public data only
  detectors/          no-arb violation checks
  research/           analysis + report generation
  execution/          EMPTY until Phase 4
  risk/
  dashboard/
  storage/
docs/venues/          venue research, vendored API specs and regulatory filings
tests/
tools/                secret scanner
```

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pre-commit install
.venv/bin/python -m pytest
```

No credentials are required for Phases 0–3: Kalshi's public market data is
unauthenticated, and the reference feeds are public.

## Regenerating the cost model

```bash
.venv/bin/python -m src.research.cost_model
```

`docs/COST_MODEL.md` is derived from `src/venues/kalshi/fees.py`. Change the fee
model, re-run, paste. Never hand-edit the tables.

## LLM usage policy

The language model builds and researches. It does not make per-tick decisions.
Detectors are deterministic arithmetic — a fee comparison or a monotonicity
check is never routed through an LLM. Legitimate uses: parsing and comparing
resolution-criteria text, flagging ambiguity for human review, generating
research reports. Token spend is logged per component and surfaced next to paper
P&L.

## What this project does not do

- Any strategy that requires predicting an outcome
- Martingale, averaging down, or size that increases after a loss
- Auto-approval of event series as exhaustive
- Trading Detector 6 divergence as if it were arbitrage
- Parameter tuning to make the feasibility report look better
