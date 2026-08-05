"""Read-only collection for the standing monitor.

Public endpoints only. **No credentials are read, no authenticated endpoint is
called, and no order-placing code exists in this package.** If a future change
appears to require authentication, that is a new project and a new spec — see
``docs/NEGATIVE_RESULT.md``.

Emits one row per open market, a superset of what the Phase 0.5 sweep captured:
tick structure, strike geometry and underlying shape are included so the
monitor can verify partitions and ladders without re-fetching raw pages.
"""

from __future__ import annotations

import csv
import gzip
import json
import time
from collections.abc import Iterator
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx

from monitor.aggregate import SweepAggregate
from monitor.checks import underlying_shape

D = Decimal

BASE = "https://api.elections.kalshi.com/trade-api/v2"
PAGE_SIZE = 1000
MVE_FILTER = "exclude"
MIN_REQUEST_INTERVAL = 0.12
MAX_RETRIES = 6

PRICE_BUCKETS = [
    ("<=8c", D(0), D(8)),
    ("8-25c", D(8), D(25)),
    ("25-75c", D(25), D(75)),
    ("75-92c", D(75), D(92)),
    (">=92c", D(92), D(101)),
]

_last_request_at = 0.0

#: Incremented on every 429. The engine reads and reports it as a health event;
#: a rate-limited scanner that stays silent is a scanner that gets IP-blocked.
rate_limit_hits = 0


def price_bucket(mid_cents: D) -> str:
    for name, lo, hi in PRICE_BUCKETS:
        if lo <= mid_cents < hi:
            return name
    return ">=92c"


@dataclass(frozen=True)
class Row:
    ticker: str
    event_ticker: str
    series_ticker: str
    category: str
    fee_type: str
    fee_multiplier: str
    tick_structure: str
    strike_type: str
    floor_strike: str
    cap_strike: str
    underlying: str
    bid_yes_cents: str
    bid_no_cents: str
    ask_yes_cents: str
    spread_cents: str
    mid_cents: str
    price_bucket: str
    bid_size: str
    ask_size: str
    close_time: str
    two_sided: str


def _get(client: httpx.Client, url: str, params: dict | None = None) -> httpx.Response:
    global _last_request_at
    delay = 1.0
    for attempt in range(MAX_RETRIES):
        elapsed = time.monotonic() - _last_request_at
        if elapsed < MIN_REQUEST_INTERVAL:
            time.sleep(MIN_REQUEST_INTERVAL - elapsed)
        _last_request_at = time.monotonic()
        resp = client.get(url, params=params)
        if resp.status_code == 429:
            global rate_limit_hits
            rate_limit_hits += 1
        if resp.status_code == 429 or resp.status_code >= 500:
            if attempt == MAX_RETRIES - 1:
                resp.raise_for_status()
            time.sleep(delay)
            delay *= 2
            continue
        resp.raise_for_status()
        return resp
    raise RuntimeError(f"retries exhausted for {url}")


def _cents(dollars: str | None) -> D:
    return D(0) if dollars in (None, "") else D(dollars) * 100


def to_row(market: dict, series_meta: dict[str, dict]) -> Row:
    event_ticker = market.get("event_ticker", "")
    series_ticker = event_ticker.split("-")[0] if event_ticker else ""
    meta = series_meta.get(series_ticker, {"category": "", "fee_type": "", "fee_multiplier": ""})

    bid_yes = _cents(market.get("yes_bid_dollars"))
    bid_no = _cents(market.get("no_bid_dollars"))
    ask_yes = D(100) - bid_no  # never read an ask field; always derive
    two_sided = bid_yes > 0 and bid_no > 0

    return Row(
        ticker=market.get("ticker", ""),
        event_ticker=event_ticker,
        series_ticker=series_ticker,
        category=meta["category"],
        fee_type=meta["fee_type"],
        fee_multiplier=meta["fee_multiplier"],
        tick_structure=market.get("price_level_structure") or "",
        strike_type=market.get("strike_type") or "",
        floor_strike="" if market.get("floor_strike") is None else str(market["floor_strike"]),
        cap_strike="" if market.get("cap_strike") is None else str(market["cap_strike"]),
        underlying=underlying_shape(market.get("yes_sub_title", "")),
        bid_yes_cents=f"{bid_yes:.4f}",
        bid_no_cents=f"{bid_no:.4f}",
        ask_yes_cents=f"{ask_yes:.4f}",
        spread_cents=f"{ask_yes - bid_yes:.4f}",
        mid_cents=f"{(ask_yes + bid_yes) / 2:.4f}",
        price_bucket=price_bucket((ask_yes + bid_yes) / 2),
        bid_size=str(market.get("yes_bid_size_fp") or "0"),
        ask_size=str(market.get("yes_ask_size_fp") or "0"),
        close_time=market.get("close_time", ""),
        two_sided=str(two_sided),
    )


def fetch_series(client: httpx.Client, categories: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for category in categories:
        payload = _get(client, f"{BASE}/series", {"category": category}).json()
        for s in payload.get("series") or []:
            out[s["ticker"]] = {
                "category": category,
                "fee_type": s.get("fee_type", ""),
                "fee_multiplier": str(s.get("fee_multiplier", "")),
            }
    return out


def backfill_series(client: httpx.Client, meta: dict[str, dict], missing: set[str]) -> set[str]:
    unresolved: set[str] = set()
    for ticker in sorted(missing):
        try:
            s = _get(client, f"{BASE}/series/{ticker}").json()["series"]
        except (httpx.HTTPError, KeyError):
            unresolved.add(ticker)
            continue
        meta[ticker] = {
            "category": s.get("category", ""),
            "fee_type": s.get("fee_type", ""),
            "fee_multiplier": str(s.get("fee_multiplier", "")),
        }
    return unresolved


def _resolve_series(
    client: httpx.Client,
    series_meta: dict[str, dict],
    unresolved: set[str],
    series_ticker: str,
) -> None:
    """Backfill one series on first sight, so streaming needs no second pass.

    The materialised version fetched every page, collected the missing series,
    backfilled, then mapped. Streaming cannot map after the fact, so the
    backfill is interleaved. Each missing series is still fetched exactly once
    and the resulting metadata is identical; only the request order changes.
    """
    if series_ticker in series_meta or series_ticker in unresolved or not series_ticker:
        return
    try:
        s = _get(client, f"{BASE}/series/{series_ticker}").json()["series"]
    except (httpx.HTTPError, KeyError):
        unresolved.add(series_ticker)
        return
    series_meta[series_ticker] = {
        "category": s.get("category", ""),
        "fee_type": s.get("fee_type", ""),
        "fee_multiplier": str(s.get("fee_multiplier", "")),
    }


def stream_markets(
    categories: list[str], meta_out: dict | None = None
) -> Iterator[Row]:
    """Yield every open market as a Row, holding no page beyond its own loop.

    **Nothing that scales with market count is retained here.** The caller folds
    each row into bounded aggregates; the raw page is released as soon as its
    markets have been converted. `series_meta` is bounded by series count.

    Series counts land in ``meta_out`` if given -- passed in rather than stashed
    on the function, because the scanner runs sweeps on a worker thread and
    module-level state would be shared across them.
    """
    with httpx.Client(timeout=60.0, headers={"Accept": "application/json"}) as client:
        series_meta = fetch_series(client, categories)
        unresolved: set[str] = set()
        cursor: str | None = None
        while True:
            params: dict[str, str | int] = {
                "status": "open",
                "limit": PAGE_SIZE,
                "mve_filter": MVE_FILTER,
            }
            if cursor:
                params["cursor"] = cursor
            payload = _get(client, f"{BASE}/markets", params).json()
            batch = payload.get("markets") or []
            for market in batch:
                _resolve_series(
                    client,
                    series_meta,
                    unresolved,
                    market.get("event_ticker", "").split("-")[0],
                )
                yield to_row(market, series_meta)
            cursor = payload.get("cursor") or None
            del payload, batch
            if not cursor:
                break
        if meta_out is not None:
            meta_out["n_series"] = len(series_meta)
            meta_out["unresolved_series"] = sorted(unresolved)


class SnapshotWriter:
    """Writes rows to the archive as they stream, so none are held to write later."""

    def __init__(self, out_dir: Path) -> None:
        self.out_dir = out_dir
        self._fh = None
        self._writer = None
        self.n_rows = 0

    def __enter__(self) -> SnapshotWriter:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._fh = gzip.open(self.out_dir / "markets.csv.gz", "wt", newline="")
        self._writer = csv.DictWriter(self._fh, fieldnames=[f.name for f in fields(Row)])
        self._writer.writeheader()
        return self

    def write(self, row: Row) -> None:
        self._writer.writerow(asdict(row))
        self.n_rows += 1

    def finish(self, manifest: dict) -> None:
        (self.out_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True)
        )

    def __exit__(self, *exc: object) -> None:
        if self._fh is not None:
            self._fh.close()


def sweep(
    categories: list[str], archive_to: Path | None = None
) -> tuple[SweepAggregate, dict]:
    """One full sweep, folded into bounded aggregates as it arrives.

    Optionally archives every row to ``archive_to`` on the way past, without
    holding them: the CSV writer consumes each row and drops it.
    """
    captured_at = datetime.now(UTC)
    aggregate = SweepAggregate()
    meta: dict = {"n_series": 0, "unresolved_series": []}
    stream = stream_markets(categories, meta)

    if archive_to is None:
        for row in stream:
            aggregate.add(row.__dict__)
        writer = None
    else:
        writer = SnapshotWriter(archive_to)
        with writer:
            for row in stream:
                aggregate.add(row.__dict__)
                writer.write(row)

    manifest = {
        "captured_at": captured_at.isoformat(),
        "n_markets": aggregate.n_markets,
        "n_series": meta["n_series"],
        "unresolved_series": meta["unresolved_series"],
    }
    if writer is not None:
        writer.finish(manifest)
    return aggregate, manifest


def write_snapshot(rows: list[Row], manifest: dict, out_dir: Path) -> None:
    """Materialised archive write. Retained for callers that already hold rows."""
    with SnapshotWriter(out_dir) as writer:
        for row in rows:
            writer.write(row)
        writer.finish(manifest)


def iter_snapshot(path: Path) -> Iterator[dict]:
    """Stream a stored snapshot, one row at a time."""
    with gzip.open(path / "markets.csv.gz", "rt") as fh:
        yield from csv.DictReader(fh)


def read_snapshot(path: Path) -> list[dict]:
    return list(iter_snapshot(path))
