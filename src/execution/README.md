# src/execution — intentionally empty until Phase 4

Hard constraint 1: **Phases 0–3 are read-only. No order-placing code exists in
this repo until Phase 4.**

Do not add a client, a stub, a type definition or a "just in case" interface
here. The point of the empty directory is that a reviewer can verify the
constraint by looking at it.

When Phase 4 opens, the first thing built here is the startup assertion that
makes production credentials unloadable, before anything that can send an order.

Research notes for that phase — Kalshi's order API surface, the absence of a
native all-or-none multi-leg order, and what that implies — are in
[`../../docs/venues/kalshi/README.md`](../../docs/venues/kalshi/README.md).
