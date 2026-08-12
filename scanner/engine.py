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
from monitor import archive, collect, disk, pipeline, population, reviews, watch
from monitor.checks import tradeable_size
from scanner import funnel, history, notify, reference, triggers
from scanner.state import CONSECUTIVE_FAILURE_ALERT, Outcome, ScannerState, now

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
            # A failed fee-change fetch is not "no scheduled fee changes". If it
            # is swallowed into an absent key, that trigger is dead and reads as
            # a quiet all-clear -- so the outcome is recorded either way and the
            # subsystem's failure count carries it to the health panel.
            try:
                computed["fee_changes"] = await asyncio.to_thread(_fee_changes)
                state.source("kalshi_fee_changes").ok()
                # Positive confirmation, so silence from this trigger means
                # "checked, nothing scheduled" and never "unknown". The
                # material/routine counts are filled in from the assessment
                # below rather than computed here, so classify_fee_changes has
                # exactly one call site.
                state.fee_changes = {
                    "polled_at": now().isoformat(),
                    "n_series": len(computed["fee_changes"].get("series", [])),
                    "n_events": len(computed["fee_changes"].get("events", [])),
                }
            except Exception as exc:  # noqa: BLE001 - surfaced, not swallowed
                state.source("kalshi_fee_changes").failed(f"{type(exc).__name__}: {exc}", exc)
                computed["fee_changes_outcome"] = Outcome.make(
                    Outcome.FAILED,
                    f"{type(exc).__name__}: {exc}; the scheduled-fee-change trigger "
                    "did not run this sweep",
                )

            state.metrics = computed
            state.metrics_at = now()
            state.sweep_count += 1
            state.sweep_seconds = time.monotonic() - started
            funnel.unknown_fee_types.clear()
            state.funnel = funnel.as_dict(funnel.build_from(agg))
            state.unknown_fee_types = dict(funnel.unknown_fee_types)
            if funnel.unknown_fee_types:
                state.log(
                    "fee-model",
                    "priced as quadratic because the fee model does not recognise: "
                    + ", ".join(f"{k} x{v}" for k, v in funnel.unknown_fee_types.items()),
                )

            # An unresolved series has no fee_multiplier, so it drops out of the
            # fee-free universe without appearing to. Surface the count.
            # The pass ran, so the subsystem is up: unresolved series are a
            # *degraded reading*, not an outage, and collapsing the two would
            # be the same conflation this audit exists to remove. It shows on
            # the degraded-readings row, and a chronic handful must not page.
            state.unresolved_series = manifest.get("unresolved_series") or []
            # The registry is not a complete enumeration of the swept universe,
            # so the exposure is measured on the rows rather than inferred from
            # the lookup failures: a fee-free series could sit here unseen.
            state.fee_model_exposure = agg.fee_model_exposure
            series_categories = dict(agg.series_category)
            state.source("series_metadata").ok()
            if state.unresolved_series:
                state.log(
                    "series",
                    f"{len(state.unresolved_series)} series metadata lookups failed; "
                    "those markets carry no fee_multiplier and are excluded from the "
                    f"fee-free universe: {state.unresolved_series[:5]}",
                )

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
            state.ready = True
            source.ok()
            _assess_archive(state)
            _record(state, "disk_sample", disk.sample)

            history.record(state.partitions or [])

            # One assessment path, shared with monitor/run.py. Arguments are
            # assembled inside pipeline.assess so neither caller can omit one --
            # see monitor/pipeline.py for why that stopped being optional.
            assessment = pipeline.assess(
                baseline,
                computed,
                fee_changes=computed.get("fee_changes"),
                series_categories=series_categories,
            )
            bands = assessment.bands
            state.bands = {k: v.as_dict() for k, v in sorted(bands.items())}
            if state.fee_changes is not None and assessment.fee_change_split:
                # Routine per-event overrides are not alerted on but are counted,
                # so the volume stays visible and nothing is hidden.
                state.fee_changes["n_material"] = assessment.fee_change_split["n_material"]
                state.fee_changes["n_routine"] = assessment.fee_change_split["n_routine"]

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

            below = assessment.below_par
            history.record_suppressed(below.suppressed)
            state.below_par = {
                "pushed": below.pushed,
                "suppressed": below.suppressed,
                "window": history.suppressed_window(),
            }

            fired = assessment.alerts
            # The only trigger evaluation. It runs after assess so the board sees
            # the same bands and the same fee-change materiality split the alert
            # layer used -- two evaluations meant the board and the alerts could
            # disagree about what fired, and the proximity watch recorded the
            # weaker of the two.
            state.triggers = [t.as_dict() for t in triggers.evaluate(baseline, computed, bands)]
            _record_proximity(state)

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
            source.failed(f"{type(exc).__name__}: {exc}", exc)
            state.log("error", f"full sweep failed: {type(exc).__name__}: {exc}")

        # Outside the try: a subsystem being down is exactly the case where the
        # sweep may also have thrown, and this must still run.
        with contextlib.suppress(Exception):
            await _push_subsystem_failures(state)

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
        if prior is None:
            # The one case where "needs two sweeps" is the truth.
            state.population = Outcome.make(
                Outcome.NOT_RUN, "no prior sweep ledger exists yet"
            )
        else:
            report = population.reconcile(prior, ledger, population.captured_at(prior))
            state.population = Outcome.make(
                Outcome.OK, "reconciled against the prior sweep", report.as_dict()
            )
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
        # The instinct is right: a reconciliation failure must not cost a sweep.
        # The consequence is what has to be visible. Without this the panel
        # would keep saying "needs two sweeps to compare" while the subsystem
        # threw every hour -- which is exactly how the .csv.gz bug would have
        # presented had a test not caught it.
        state.source("population").failed(f"{type(exc).__name__}: {exc}", exc)
        state.population = Outcome.make(
            Outcome.FAILED,
            f"reconciliation raised {type(exc).__name__}: {exc}",
        )
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
        state.undelivered_alerts = []
        state.log("push", f"pushed {sent} alert(s) to ntfy")
    except Exception as exc:  # noqa: BLE001
        # An alert that failed to send is not an alert that did not fire. The
        # trigger board would otherwise show it as delivered, and the only
        # trace would be a log line nobody reads.
        source.failed(f"{type(exc).__name__}: {exc}", exc)
        state.undelivered_alerts = [
            {"trigger": a.trigger, "at": now().isoformat(), "why": type(exc).__name__}
            for a in fired
        ]
        state.log(
            "error",
            f"ntfy push failed: {type(exc).__name__}; {len(fired)} alert(s) fired but "
            "were not delivered",
        )


async def _push_subsystem_failures(state: ScannerState) -> None:
    """Push when a subsystem has failed CONSECUTIVE_FAILURE_ALERT sweeps running.

    No detection threshold is involved. **A monitor whose subsystems fail
    quietly is the failure mode this project exists to avoid**, so a dead
    subsystem is news on its own account.

    ``ntfy`` is deliberately excluded from what this will push about: the
    transport cannot carry news of its own failure, and a handler that tries
    would be a check sharing a failure mode with its subject. It is surfaced on
    the health panel instead, and the missing monthly heartbeat is the
    out-of-band signal -- silence reads as dead, which is the safe direction.
    """
    down = [
        h
        for h in state.sources.values()
        if h.name != "ntfy" and h.consecutive_failures >= CONSECUTIVE_FAILURE_ALERT
    ]
    if not down:
        return

    for h in down:
        state.log(
            "subsystem",
            f"{h.name} has failed {h.consecutive_failures} consecutive sweeps "
            f"({h.last_error_type}); it is not reporting, and its panel is not "
            "evidence of anything",
        )

    cfg = notify.config_from_env()
    if not cfg.configured:
        return
    body = "\n".join(
        f"{h.name}: {h.consecutive_failures} consecutive failures, "
        f"{h.failures} total, last {h.last_error_type}"
        for h in down
    )
    try:
        async with httpx.AsyncClient() as client:
            await notify.push(
                client,
                cfg,
                title="Kalshi scanner: subsystem down",
                body=f"{body}\n\n{alerts_mod.FOOTER}",
                priority="high",
            )
        state.source("ntfy").ok()
    except Exception as exc:  # noqa: BLE001
        state.source("ntfy").failed(f"{type(exc).__name__}: {exc}", exc)


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

    # **Positive confirmation, not absence of error.** This trigger's silence
    # has to mean "checked, nothing scheduled" -- it is one of only two
    # programmatic proxies for a change to the 0.07 coefficient.
    fc = state.fee_changes
    if fc is None:
        fee_line = "fee-change endpoints: NEVER POLLED SUCCESSFULLY - treat this trigger as down"
    else:
        fee_line = (
            f"fee-change endpoints: last polled {fc['polled_at'][:19]}Z, "
            f"{fc['n_series']} series and {fc['n_events']} event changes scheduled "
            f"({fc.get('n_material', 0)} material, {fc.get('n_routine', 0)} routine)"
        )

    lines = [
        f"{state.sweep_count} sweeps, uptime {(now() - state.started_at).days}d",
        cadence("full sweep", sweep),
        cadence("tracked subset", tracked),
        f"429s: {rl['lifetime']} lifetime, {rl['last_24h']} in the last 24h",
        f"rss: {proc['rss_mb']} MB" if proc["rss_mb"] is not None else "rss: not readable here",
        fee_line,
        "",
        history.render_review(history.review()),
        "",
        reviews.render(reviews.status(since=state.started_at)),
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
            state.source("ntfy").failed(f"{type(exc).__name__}: {exc}", exc)


def _record_proximity(state: ScannerState) -> None:
    """Track the peak trigger proximity, so the hero's claim is bounded by
    the window actually observed rather than asserted about all time."""
    measured = [
        float(t["proximity_pct"]) for t in (state.triggers or []) if t["proximity_pct"] is not None
    ]
    if not measured:
        return
    peak = max(measured)
    # Persisted so the window survives a deploy. Only a *new* all-time peak is
    # written, so the ledger grows with information rather than with time --
    # and the in-memory value is the restored one until something beats it,
    # rather than this session's max.
    if state.peak_proximity_pct is None or peak > state.peak_proximity_pct:
        state.peak_proximity_pct = peak
        if state.watch_persisted:
            _record(state, "watch_ledger", lambda: watch.record_peak(peak))
    if peak >= watch.WITHIN_PCT:
        state.last_within_20_at = now()
        if state.watch_persisted:
            _record(state, "watch_ledger", lambda: watch.record_within_20(peak))


def _assess_archive(state: ScannerState) -> None:
    """Turn "nobody has pulled the ledgers" into a subsystem state.

    ``ledger_archive`` was registered in ``SUBSYSTEMS`` and nothing ever set it,
    so it rendered NEVER RUN forever and had no path to the consecutive-failure
    alert every other subsystem has. That is the failure this section is about,
    applied to the mechanism that exists to prevent losing the record of it:
    **an archive that silently never runs looks exactly like one that has not
    run yet.**

    The box cannot see the Action fail. What it can see is that nothing has
    fetched, which is the same thing from the other side.
    """
    served = state.ledgers_served_at
    stale_after = archive.ARCHIVE_STALE_AFTER_DAYS * 86400
    uptime = (now() - state.started_at).total_seconds()

    if served is None:
        # A fresh box legitimately has not been pulled yet. Once it has been up
        # longer than the whole archive window with no fetch at all, "not yet"
        # has become "not happening", and durability is notional.
        if uptime > stale_after:
            state.source("ledger_archive").failed(
                f"no ledger fetch in {uptime / 86400:.1f} days of uptime; "
                "the weekly archive has never run and instance loss would cost "
                "the entire history"
            )
        return

    age = (now() - served).total_seconds()
    if age > stale_after:
        state.source("ledger_archive").failed(
            f"last ledger fetch {age / 86400:.1f} days ago, past the "
            f"{archive.ARCHIVE_STALE_AFTER_DAYS}d window"
        )
    else:
        state.source("ledger_archive").ok()


def _record(state: ScannerState, source: str, write: Any) -> None:
    """Write to a ledger, recording the failure rather than swallowing it.

    A ledger write must not cost a sweep, and a ledger that silently stops
    being written is exactly the shape this project keeps finding -- so the
    handler produces a state instead of a log line.
    """
    try:
        write()
        state.source(source).ok()
    except Exception as exc:  # noqa: BLE001 - recorded, not swallowed
        state.source(source).failed(f"{type(exc).__name__}: {exc}", exc)


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
                source.failed(f"{type(exc).__name__}: {exc}", exc)
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
                    source.failed(f"{type(exc).__name__}: {exc}", exc)
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
