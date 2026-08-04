# Population reconciliation — baseline versus live

**Verdict: the populations differ by exchange churn, not by filter drift. The
baseline filter was not wrong. The KXGDPYEAR-28 alert is genuine.**

Reproduce with `python -m src.research.reconcile --sweep-now`. Note that this
compares against raw API pages under `data/sweeps/`, which is gitignored — a
fresh clone has to take its own baseline sweep first. The committed
`monitor/snapshots/` hold the derived CSV, which is enough for the metrics but
not for a set difference on `created_time` / `open_time` / `can_close_early`.

## The question

The baseline swept 70,820 markets; the first live sweep 77,318. Until that +9.2%
is attributed, every baseline-versus-live comparison risks measuring two
different populations — and the first live alert, which called KXGDPYEAR-28 a
*new* below-par partition, assumes it was present in the baseline and above par.
If the live scanner merely sees markets the baseline filtered out, "new" means
"newly visible", which is a false positive of exactly the class the verification
requirements exist to catch.

Counts cannot answer this. Identifiers can.

## Set difference

Baseline `20260803T070632Z_t0` (70,820 markets, captured 2026-08-03T07:06Z)
against a fresh sweep `20260804T162201Z` (77,047 markets), 33.3 hours apart.

| | Markets |
| --- | --- |
| In both | 59,400 |
| Live only | 17,647 |
| Baseline only | 11,420 |
| Net | **+6,227 (+8.8%)** |

The net figure hides an order of magnitude more churn than it shows: 29,067
markets moved in or out over 33 hours, against a net change of 6,227.

## Live-only, attributed

| Cause | Markets | Share |
| --- | --- | --- |
| `created_time` after the baseline sweep — genuinely new listings | 15,710 | 89.0% |
| Created earlier, `open_time` after the baseline — not yet tradeable then, correctly excluded | 1,871 | 10.6% |
| Open before the baseline and absent from it anyway | **66** | **0.4%** |
| RFQ shells leaked past `mve_filter` | 0 | 0% |

The churn is concentrated where listings are mechanically frequent:
`KXNASDAQ100U` (1,600), hourly crypto (`KXBTCD`, `KXETHD`, `KXSOLD`), NFL
spreads and totals.

## Baseline-only, attributed

| Cause | Markets | Share |
| --- | --- | --- |
| `close_time` passed — settled normally | 8,153 | 71% |
| `close_time` still future, but `can_close_early` — settled early | 3,267 | 29% |

Every one of the 3,267 has `can_close_early: true`, and they are tennis, CS2 and
MLB markets — the categories that resolve the moment the game does. 8,153 +
3,267 = 11,420, so the baseline-only set is fully attributed with nothing left
over.

## The 66

Two commodity series, `KXB85` and `KXB65` (Chemical Lean Beef trim), 33 markets
each. Both created and opened 2026-07-08, both `status: active`, closing
2027-01-28. **Zero of them appear in either baseline sweep; all 66 appear live.**

They are not ours to explain: the query is identical, and a market open since
July should have been returned by `status=open` in August. This is an
exchange-side change in what that filter returns. It is recorded here as a known
limitation of `status=open` as a population definition, not as a defect in the
baseline.

Magnitude: 0.09% of the baseline population, 0.4% of the live-only set. It moves
no statistic in the memo.

## Why this is not filter drift

Three checks, none of which relies on the size of the gap.

**The queries are identical.** All three sweep call sites —
`src/research/spread_sweep.py` (which produced the baseline),
`monitor/collect.py` (which the live scanner uses), and
`src/research/reconcile.py` — issue `status=open`, `limit=1000`,
`mve_filter=exclude`. Same endpoint, same parameters, same pagination.

**The method is deterministic.** The two baseline sweeps, taken four minutes
apart with the same code, contain **exactly the same 70,820 tickers** — zero in
one and not the other. Cursor pagination over a mutating dataset could have
truncated or duplicated; it did not. Pagination truncation is ruled out by
observation rather than argument.

**Every difference is attributed to a market-level field.** `created_time`,
`open_time`, `close_time` and `can_close_early` account for 29,001 of the 29,067
moved markets, read off the objects themselves rather than inferred.

## The alert

| | Legs | Sum of asks | Capacity |
| --- | --- | --- | --- |
| Baseline, 2026-08-03 | 14 | **103.00¢** — above par | 15.01 |
| Live, 2026-08-04 | 14 | **98.00¢** — below par | 15.00 |

`KXGDPYEAR-28` was present in the baseline population, with all fourteen legs,
priced above par. It is now below par. **The alert is genuine: a real
baseline-to-below-par transition, not a newly visible market.**

What it is not is an opportunity. 2¢ of edge on 15 contracts is $0.30 of total
capacity over 2.57 years — 0.79%/yr. It is the same category as everything else
the study found, and see [`NEGATIVE_RESULT.md`](../docs/NEGATIVE_RESULT.md)
§ "First dynamics" for what it does tell us.

## Standing limitation

`status=open` is a population definition supplied by the exchange, and the 66
show it can change without notice. Future reconciliations should re-run this
set difference rather than compare counts, and the monitor records
market-population size per sweep so a step change is visible in the archive.
