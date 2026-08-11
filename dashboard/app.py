"""FastAPI backend: serves the PWA and pushes scanner state over WebSocket.

Read-only. The startup assertion runs before anything binds a port.

Every value the API emits comes from the scanner. There is no seed data, no
placeholder, and no default that could be mistaken for a measurement -- a field
the scanner has not computed is ``null`` and the client renders NO DATA.

``--snapshot`` runs the dashboard against a committed snapshot instead of the
live exchange. That is real measured data, not synthetic, but it is not live, so
the response carries ``snapshot_source`` and the UI shows a persistent banner.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from monitor import archive, collect, metrics
from scanner import engine, funnel, guard, triggers
from scanner.state import ScannerState, now

STATIC = Path(__file__).parent / "static"
BASELINE = Path("monitor/baseline.json")
PUSH_INTERVAL = 2.0

state = ScannerState()
app = FastAPI(title="Kalshi scanner", docs_url=None, redoc_url=None)
_runtime: dict[str, Any] = {"snapshot_source": None, "task": None}


def load_baseline() -> dict:
    return json.loads(BASELINE.read_text())


def seed_from_snapshot(path: Path) -> None:
    """Populate state from a committed snapshot. Real data, explicitly not live."""
    rows = collect.read_snapshot(path)
    baseline = load_baseline()
    computed = metrics.compute(rows)
    state.metrics = computed
    state.metrics_at = now()
    state.triggers = [t.as_dict() for t in triggers.evaluate(baseline, computed)]
    state.funnel = funnel.as_dict(funnel.build(rows))
    state.partitions = computed["verified_partitions"]["fee_free_detail"]
    state.tripwire = computed["deci_cent_fee_free_tripwire"]
    state.sweep_count = 1
    engine._record_proximity(state)
    state.ready = True
    state.source("kalshi_sweep").ok()
    state.beat()
    _runtime["snapshot_source"] = path.name
    state.log("start", f"loaded committed snapshot {path.name} - not live data")


def payload() -> dict[str, Any]:
    body = state.snapshot()
    body["snapshot_source"] = _runtime["snapshot_source"]
    return body


@app.get("/api/state")
async def api_state() -> JSONResponse:
    return JSONResponse(payload())


@app.get("/api/health")
async def api_health() -> JSONResponse:
    body = payload()
    return JSONResponse(
        {"online": body["online"], "ready": body["ready"], "uptime": body["uptime_seconds"]},
        status_code=200 if body["online"] else 503,
    )


@app.get("/api/ledgers")
async def api_ledgers() -> JSONResponse:
    """Manifest of the durable ledgers, for the weekly archive job.

    Read-only and unauthenticated by design: the weekly GitHub Action pulls
    these and commits them, so the box never holds a repository credential. The
    contents are market statistics -- no credentials, no positions, no personal
    data.
    """
    state.ledgers_served_at = now()
    return JSONResponse({"ledgers": archive.manifest(), "served_at": now().isoformat()})


@app.get("/api/ledgers/{name}")
async def api_ledger(name: str) -> Any:
    """One ledger, by allowlisted name.

    **The name indexes a fixed dict; it is never joined onto a path.** This
    endpoint is unauthenticated, and joining a caller-supplied name onto
    `data/monitor/` would let a caller escape the directory.
    """
    path = archive.LEDGER_FILES.get(name)
    if path is None:
        return JSONResponse({"error": "unknown ledger"}, status_code=404)
    if not path.exists():
        return JSONResponse({"error": "ledger not yet written"}, status_code=404)
    state.ledgers_served_at = now()
    return PlainTextResponse(path.read_text())


@app.websocket("/ws")
async def ws(socket: WebSocket) -> None:
    await socket.accept()
    try:
        while True:
            await socket.send_json(payload())
            await asyncio.sleep(PUSH_INTERVAL)
    except (WebSocketDisconnect, RuntimeError):
        return


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/sw.js")
async def service_worker() -> FileResponse:
    # Must be served from the root scope to control the whole app.
    return FileResponse(STATIC / "sw.js", media_type="application/javascript")


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.on_event("startup")
async def startup() -> None:
    guard.assert_no_credentials()
    if _runtime["snapshot_source"] is None:
        _runtime["task"] = asyncio.create_task(engine.run(state, load_baseline()))


@app.on_event("shutdown")
async def shutdown() -> None:
    task = _runtime.get("task")
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, default=None)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    guard.assert_no_credentials()
    if args.snapshot:
        seed_from_snapshot(args.snapshot)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
