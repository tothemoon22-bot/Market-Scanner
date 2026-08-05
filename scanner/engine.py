"""Continuous scanner. Three loops at three cadences, one shared state.

    full sweep      every FULL_SWEEP_INTERVAL, ~75s of work, exchange-wide stats
    tracked subset  every TRACKED_INTERVAL, the ~210 markets the findings rest on
    reference spot  1 Hz, Binance mirror and Coinbase, reference only

**Kalshi's WebSocket is not used, and cannot be.** The handshake requires
authentication -- verified: `wss://external-api-ws.kalshi.com/trade-api/ws/v2`
and the elections host both return HTTP 401 without credentials. The spec asked
for live books over WS *and* for no authenticated endpoints; those cannot both
hold. The no-credentials constraint wins, because it is the constraint the whole
project has been built and grep-tested against. The tracked subset is polled
over public REST instead, and the achieved interval is measured and reported
rather than assumed.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from monitor import aggregate as aggregate_mod
from monitor import alerts as alerts_mod
from monitor import collect, population
from monitor.checks import tradeable_size
from scanner import funnel, history, notify, reference, triggers
from scanner.state import ScannerState, now

D = Decimal

# Exchange-wide statistics have a time constant of days. Hourly is already far
# finer than the phenomenon; faster buys nothing and risks an IP block, which
# would kill the monitor for zero benefit. The tracked subset stays fast because
# it is 14 requests, not 77 pages.
FULL_SWEEP_INTERVAL = 3600.0
TRACKED_INTERVAL = 15.0
REFERENCE_INTERVAL = 1.0
REQUEST_SPACING = 0.12

#: Monthly proof of life, so a quiet monitor is distinguishable from a dead one.
HEARTBEAT_INTERVAL = 30 * 86400.0


async def _paced_get(
    client: httpx.AsyncClient, url: str, params: dict | None = None, retries: int = 4
) -> httpx.Response:
    delay = 1.0
    for attempt in range(retries):
        resp = await client.get(url, params=params, timeout=45.0)
        if resp.status_code == 429 or resp.status_code >= 500:
            if attempt == retries - 1:
                resp.raise_for_status()
            await asyncio.sleep(delay)
            delay *= 2
            continue
        resp.raise_for_status()
        return resp
    raise RuntimeError(f"retries exhausted for {url}")


def tracked_series(state: ScannerState) -> list[str]:
    """Fee-free series from the last sweep. Empty until the first sweep lands."""
    if not state.metrics:
        return []
    return list(state.metrics.get("fee_free", {}).get("series", []))


def _row_from_market(market: dict, series_meta: dict) -> dict[str, Any]:
    return collect.to_row(market, series_meta).__dict__


async def full_sweep_loop(state: ScannerState, baseline: dict) -> None:
    source = state.source("kalshi_sweep")
    last_started: float | None = None
    while True:
        started = time.monotonic()
        if last_started is not None:
            state.sweep_intervals.append(started - last_started)
        last_started = started
        try:
            # Streaming: the sweep folds each page into bounded aggregates and
            # discards it. Nothing that scales with market count is held, here
            # or in the aggregate. See monitor/aggregate.py.
            stamp = now().strftime("%Y%m%dT%H%M%SZ")
            ledger = population.ledger_path(stamp)
            agg, manifest = await asyncio.to_thread(
                collect.sweep, collect_categories(), None, ledger
            )

            computed = agg.result()
            try:
                fee_changes = await asyncio.to_thread(_fee_changes)
                computed["fee_changes"] = fee_changes
            except Exception as exc:  # noqa: BLE001 - surfaced, not swallowed
                state.source("kalshi_fee_changes").failed(f"{type(exc).__name__}: {exc}")

            state.metrics = computed
            state.metrics_at = now()
            state.sweep_count += 1
            state.sweep_seconds = time.monotonic() - started
            state.triggers = [t.__dict__ for t in triggers.evaluate(baseline, computed)]
            state.funnel = funnel.as_dict(funnel.build_from(agg))

            # Key-count growth bounds the aggregate's memory *and* is how a
            # tick-structure change would first surface -- earlier than the
            # share thresholds, which need 5pp of the exchange to move.
            state.spread_cardinality = len(agg.spread_all.counts)
            if state.spread_cardinality >= aggregate_mod.ALERT_DISTINCT_SPREADS:
                state.log(
                    "cardinality",
                    f"spread count map holds {state.spread_cardinality:,} distinct values, "
                    f"at or past the {aggregate_mod.ALERT_DISTINCT_SPREADS:,} alert level "
                    f"(hard stop {aggregate_mod.MAX_DISTINCT_SPREADS:,}); the price grid has "
                    "changed shape, and the measured memory figures no longer hold",
                )
            del agg  # release the candidate legs before the loop sleeps
            await asyncio.to_thread(_reconcile_population, state, ledger, manifest, computed)
            state.partitions = computed["verified_partitions"]["fee_free_detail"]
            state.tripwire = computed["deci_cent_fee_free_tripwire"]
            _record_proximity(state)
            state.ready = True
            source.ok()

            history.record(state.partitions or [])
            bands = history.bands()
            state.bands = {k: v.as_dict() for k, v in sorted(bands.items())}

            # Date each UNKNOWN -> KNOWN crossing once, in the ledger rather
            # than in memory, so a restart does not re-announce every band.
            for event in history.record_band_transitions(bands):
                state.log(
                    "band",
                    f"{event['series']} band established after "
                    f"{event['observations']} observations: "
                    f"{event['min_cost_cents']}c to {event['max_cost_cents']}c",
                    series=event["series"],
                )

            below = alerts_mod.classify_below_par(baseline, computed, bands)
            history.record_suppressed(below.suppressed)
            state.below_par = {
                "pushed": below.pushed,
                "suppressed": below.suppressed,
                "window": history.suppressed_window(),
            }

            fired = alerts_mod.evaluate(baseline, computed, bands=bands)
            state.triggers = [t.__dict__ for t in triggers.evaluate(baseline, computed, bands)]

            hits = collect.rate_limit_hits
            if hits > state.rate_limit_hits:
                fresh = hits - state.rate_limit_hits
                for _ in range(fresh):
                    state.rate_limit_at.append(now())
                state.log(
                    "rate-limit",
                    f"exchange returned 429 {fresh} time(s) during this "
                    "sweep; backed off and retried",
                )
                state.rate_limit_hits = hits

            state.log(
                "sweep",
                f"full sweep complete: {manifest['n_markets']:,} markets in "
                f"{state.sweep_seconds:.0f}s",
                markets=manifest["n_markets"],
            )
            for item in below.suppressed:
                state.log(
                    "suppressed",
                    f"{item['event']} below par at {item['cost_cents']}c but not pushed: "
                    f"{item['suppressed_because']}",
                )
            for alert in fired:
                state.log("alert", alert.trigger, baseline=alert.baseline, current=alert.current)
            await _push(state, fired)
        except Exception as exc:  # noqa: BLE001
            source.failed(f"{type(exc).__name__}: {exc}")
            state.log("error", f"full sweep failed: {type(exc).__name__}: {exc}")

        state.beat()
        await asyncio.sleep(max(0.0, FULL_SWEEP_INTERVAL - (time.monotonic() - started)))


def _reconcile_population(
    state: ScannerState, ledger: Path, manifest: dict, computed: dict
) -> None:
    """Attribute this sweep's population change against the previous sweep.

    Runs every sweep rather than when somebody notices a count moved: by the
    time a count is surprising, every baseline comparison since the last check
    is already suspect. Never raises into the loop -- a reconciliation failure
    must not cost a sweep.
    """
    universe = computed.get("universe", {})
    try:
        prior = population.previous_ledger(ledger)
        report = None
        if prior is not None:
            report = population.reconcile(prior, ledger, population.captured_at(prior))
            state.population = report.as_dict()
            if report.fires:
                state.log(
                    "population",
                    f"{report.unattributed} markets moved for a reason the attribution "
                    f"rules do not explain ({report.unattributed_pct:.3f}% of "
                    f"{report.moved:,} moved); baseline comparison is suspect until "
                    "this is understood",
                )
            elif report.unattributed:
                state.log(
                    "population",
                    f"{report.unattributed} unattributed of {report.moved:,} moved "
                    f"- below the {population.UNATTRIBUTED_ALERT_THRESHOLD} threshold",
                )
        population.record_trend(
            now(),
            universe.get("n_markets", 0),
            universe.get("n_two_sided", 0),
            report,
        )
        state.trend = population.trend()
        population.prune_ledgers()
    except Exception as exc:  # noqa: BLE001 - surfaced, never fatal to the sweep
        state.source("population").failed(f"{type(exc).__name__}: {exc}")
        state.log("error", f"population reconciliation failed: {type(exc).__name__}: {exc}")
    else:
        state.source("population").ok()


async def _push(state: ScannerState, fired: list) -> None:
    """Deliver fired alerts to the phone via ntfy. Never raises into the loop."""
    cfg = notify.config_from_env()
    source = state.source("ntfy")
    if not cfg.configured:
        state.ntfy = {"configured": False, "target": cfg.redacted()}
        return
    state.ntfy = {"configured": True, "target": cfg.redacted()}
    if not fired:
        return
    try:
        async with httpx.AsyncClient() as client:
            sent = await notify.push_alerts(client, cfg, fired)
        source.ok()
        state.log("push", f"pushed {sent} alert(s) to ntfy")
    except Exception as exc:  # noqa: BLE001
        source.failed(f"{type(exc).__name__}: {exc}")
        state.log("error", f"ntfy push failed: {type(exc).__name__}")


def heartbeat_summary(state: ScannerState) -> str:
    """The monthly payload: proof of life, achieved cadence, and the ledger review.

    The suppressed-ledger review rides here rather than pushing on its own. It
    is a reporting job, and a job that reports monthly by raising its own
    notification is a job that gets muted.
    """
    snap = state.snapshot()
    sweep, tracked = snap["sweep"], snap["tracked_loop"]
    rl, proc = snap["rate_limit"], snap["process"]

    def cadence(label: str, block: dict) -> str:
        achieved = block["achieved_interval_seconds"]
        configured = block["configured_interval_seconds"]
        if achieved is None or configured is None:
            return f"{label}: not yet measured"
        return (
            f"{label}: {achieved:.0f}s achieved vs {configured:.0f}s configured "
            f"({block['drift_seconds']:+.0f}s over {block['samples']} samples)"
        )

    lines = [
        f"{state.sweep_count} sweeps, uptime {(now() - state.started_at).days}d",
        cadence("full sweep", sweep),
        cadence("tracked subset", tracked),
        f"429s: {rl['lifetime']} lifetime, {rl['last_24h']} in the last 24h",
        f"rss: {proc['rss_mb']} MB" if proc["rss_mb"] is not None else "rss: not readable here",
        "",
        history.render_review(history.review()),
    ]
    return "\n".join(lines)


async def heartbeat_loop(state: ScannerState) -> None:
    """Monthly, low priority, distinct tag. A silent monitor must still prove
    it is alive, or its silence stops being evidence of anything."""
    cfg = notify.config_from_env()
    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL)
        if not cfg.configured:
            continue
        try:
            async with httpx.AsyncClient() as client:
                await notify.push_heartbeat(client, cfg, heartbeat_summary(state))
            state.source("ntfy").ok()
            state.log("push", "monthly heartbeat sent, with suppressed-ledger review")
        except Exception as exc:  # noqa: BLE001
            state.source("ntfy").failed(f"{type(exc).__name__}: {exc}")


def _record_proximity(state: ScannerState) -> None:
    """Track the peak trigger proximity, so the hero's claim is bounded by
    the window actually observed rather than asserted about all time."""
    measured = [
        float(t["proximity_pct"]) for t in (state.triggers or []) if t["proximity_pct"] is not None
    ]
    if not measured:
        return
    peak = max(measured)
    state.peak_proximity_pct = peak
    if peak >= 20:
        state.last_within_20_at = now()


def collect_categories() -> list[str]:
    from monitor.run import CATEGORIES

    return CATEGORIES


def _fee_changes() -> dict[str, Any]:
    from monitor.run import fetch_fee_changes

    changes = fetch_fee_changes()
    return {"count": len(changes["series"]) + len(changes["events"]), **changes}


async def tracked_loop(state: ScannerState) -> None:
    """Poll the markets the findings actually rest on, far faster than the sweep."""
    source = state.source("kalshi_tracked")
    last_started: float | None = None
    async with httpx.AsyncClient(headers={"Accept": "application/json"}) as client:
        while True:
            started = time.monotonic()
            series = tracked_series(state)
            if not series:
                # Not a cycle: no interval is recorded for a tick that had
                # nothing to poll, or the achieved interval would read healthy
                # while the loop was doing nothing.
                await asyncio.sleep(TRACKED_INTERVAL)
                continue
            if last_started is not None:
                state.tracked_intervals.append(started - last_started)
            last_started = started
            try:
                rows: list[dict[str, Any]] = []
                meta = {
                    s: {
                        "category": "",
                        "fee_type": "quadratic",
                        "fee_multiplier": "0",
                    }
                    for s in series
                }
                for ticker in series:
                    resp = await _paced_get(
                        client,
                        f"{collect.BASE}/markets",
                        {"series_ticker": ticker, "status": "open", "limit": 1000},
                    )
                    for market in resp.json().get("markets") or []:
                        rows.append(_row_from_market(market, meta))
                    await asyncio.sleep(REQUEST_SPACING)

                violations = _invariant_violations(rows)
                if violations:
                    state.invariant_violations += len(violations)
                    state.invariant_last = violations[0]
                    state.log(
                        "invariant",
                        f"{len(violations)} market(s) with bid_YES + bid_NO > 100c "
                        "- our book is wrong, not the exchange's",
                        example=violations[0],
                    )

                state.tracked = _tracked_view(rows)
                state.tracked_at = now()
                state.tracked_poll_seconds = time.monotonic() - started
                source.ok()
            except Exception as exc:  # noqa: BLE001
                source.failed(f"{type(exc).__name__}: {exc}")
                state.log("error", f"tracked poll failed: {type(exc).__name__}: {exc}")

            state.beat()
            await asyncio.sleep(max(0.0, TRACKED_INTERVAL - (time.monotonic() - started)))


def _invariant_violations(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """bid_YES + bid_NO > 100 cannot happen in a correctly reconstructed book.

    Observing it means our view is wrong -- a stale read or two sides captured
    at different moments. It is a health signal, never a detector.
    """
    out = []
    for row in rows:
        total = D(row["bid_yes_cents"]) + D(row["bid_no_cents"])
        if total > 100:
            out.append(
                {
                    "ticker": row["ticker"],
                    "bid_yes": row["bid_yes_cents"],
                    "bid_no": row["bid_no_cents"],
                    "sum": str(total),
                    "at": now().isoformat(),
                }
            )
    return out


def _tracked_view(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-event live basket cost for the tracked subset."""
    from monitor.checks import verify_partition
    from monitor.metrics import _as_legs, _events

    out = []
    for event, legs in sorted(_events(rows).items()):
        if len(legs) < 2:
            continue
        result = verify_partition(_as_legs(legs))
        cost = sum(D(leg["ask_yes_cents"]) for leg in legs)
        capacity = min(tradeable_size(leg["ask_size"]) for leg in legs)
        out.append(
            {
                "event": event,
                "legs": len(legs),
                "verified": bool(result),
                "verify_reason": result.reason,
                "tick_structure": legs[0]["tick_structure"],
                "cost_cents": str(cost.quantize(D("0.01"))),
                "distance_from_par_cents": str((cost - 100).quantize(D("0.01"))),
                "capacity_contracts": str(capacity),
                "all_two_sided": all(leg["two_sided"] == "True" for leg in legs),
            }
        )
    return out


async def reference_loop(state: ScannerState) -> None:
    async with httpx.AsyncClient() as client:
        while True:
            started = time.monotonic()
            for name, fetch in (
                ("binance_vision", reference.fetch_binance),
                ("coinbase", reference.fetch_coinbase),
            ):
                source = state.source(name)
                try:
                    state.reference[name] = await fetch(client)
                    source.ok()
                except Exception as exc:  # noqa: BLE001
                    source.failed(f"{type(exc).__name__}: {exc}")
                    state.reference.pop(name, None)
                    state.log(
                        "error",
                        f"{name} unavailable: {type(exc).__name__}. "
                        "No failover - Binance.US is a different exchange.",
                    )
            state.beat()
            await asyncio.sleep(max(0.0, REFERENCE_INTERVAL - (time.monotonic() - started)))


async def run(state: ScannerState, baseline: dict) -> None:
    # Publish what the loops were told to do, so the panel can show achieved
    # against configured rather than achieved alone.
    state.configured_sweep_interval = FULL_SWEEP_INTERVAL
    state.configured_tracked_interval = TRACKED_INTERVAL
    state.log("start", "scanner starting; read-only, no credentials")
    tasks = [
        asyncio.create_task(full_sweep_loop(state, baseline)),
        asyncio.create_task(tracked_loop(state)),
        asyncio.create_task(reference_loop(state)),
        asyncio.create_task(heartbeat_loop(state)),
    ]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.gather(*tasks, return_exceptions=True)
