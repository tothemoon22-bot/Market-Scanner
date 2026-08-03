"""Phase 0.5a — exchange-wide top-of-book sweep.

Read-only. Public endpoints only. No credentials are used or needed.

Captures one snapshot of every open market on Kalshi plus the series metadata
needed to segment it, writes the raw API pages to disk unmodified, and derives
a flat row per market.

The quantity under study is the YES bid-ask spread:

    spread = ask(YES) - bid(YES) = (100 - bid_NO) - bid_YES = 100 - (bid_YES + bid_NO)

which is the same expression the deleted complementary detector was testing.
Half of it is the cost of crossing on each leg, so an N-leg taker-side basket
needs true mispricing of at least ``fee_gate + sum(s_i / 2)`` before it is even
visible on the book.

Usage::

    python -m src.research.spread_sweep            # writes data/sweeps/<utc>/
    python -m src.research.spread_sweep --label t1
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx

BASE = "https://api.elections.kalshi.com/trade-api/v2"
PAGE_SIZE = 1000
DATA_ROOT = Path("data/sweeps")

# Categories are not enumerable through the API; this list is the set observed
# on the exchange. An unknown category shows up as "" in the output rather than
# being silently dropped -- see `unmapped_series` in the manifest.
CATEGORIES = [
    "Politics",
    "Sports",
    "Culture",
    "Crypto",
    "Climate and Weather",
    "Economics",
    "Companies",
    "Financials",
    "Health",
    "Science and Technology",
    "Transportation",
    "World",
    "Entertainment",
    "Exotics",  # multivariate parlay series (KXMVE*) live here
    "Elections",
]

# Multivariate parlay markets are excluded at the API. They are auto-generated
# one-per-requested-combination shells quoted through the RFQ system rather than
# a resting book: a first pass without this filter found 26,852 of 27,000
# sampled open markets were KXMVE* combos, essentially none with a two-sided
# book. Including them buys nothing and costs gigabytes.
MVE_FILTER = "exclude"

PRICE_BUCKETS = [
    ("<=8c", Decimal(0), Decimal(8)),
    ("8-25c", Decimal(8), Decimal(25)),
    ("25-75c", Decimal(25), Decimal(75)),
    ("75-92c", Decimal(75), Decimal(92)),
    (">=92c", Decimal(92), Decimal(100)),
]


# Unauthenticated reads are rate limited more tightly than the documented Basic
# tier (200 read tokens/s at 10 tokens per call = 20 req/s). A sequential httpx
# client runs right at that edge and gets 429s, so pace below it and back off
# when the exchange pushes back. Phase 1 needs this logic regardless.
MIN_REQUEST_INTERVAL = 0.12
MAX_RETRIES = 6

_last_request_at = 0.0


def _get(client: httpx.Client, url: str, params: dict | None = None) -> httpx.Response:
    """Paced GET with exponential backoff on 429 and 5xx."""
    global _last_request_at
    delay = 1.0
    for attempt in range(MAX_RETRIES):
        elapsed = time.monotonic() - _last_request_at
        if elapsed < MIN_REQUEST_INTERVAL:
            time.sleep(MIN_REQUEST_INTERVAL - elapsed)
        _last_request_at = time.monotonic()

        resp = client.get(url, params=params)
        if resp.status_code == 429 or resp.status_code >= 500:
            if attempt == MAX_RETRIES - 1:
                resp.raise_for_status()
            time.sleep(delay)
            delay *= 2
            continue
        resp.raise_for_status()
        return resp
    raise RuntimeError(f"unreachable: retries exhausted for {url}")


def price_bucket(mid_cents: Decimal) -> str:
    for name, lo, hi in PRICE_BUCKETS:
        if lo <= mid_cents < hi:
            return name
    return ">=92c"


@dataclass(frozen=True)
class MarketRow:
    ticker: str
    event_ticker: str
    series_ticker: str
    category: str
    fee_type: str
    fee_multiplier: str
    bid_yes_cents: str
    bid_no_cents: str
    ask_yes_cents: str
    spread_cents: str
    mid_cents: str
    price_bucket: str
    bid_size: str
    ask_size: str
    open_interest: str
    volume_24h: str
    close_time: str
    hours_to_resolution: str
    two_sided: str


def _cents(dollars: str | None) -> Decimal:
    """Kalshi returns fixed-point dollar strings. Decimal only, never float."""
    if dollars in (None, ""):
        return Decimal(0)
    return Decimal(dollars) * 100


def fetch_series(client: httpx.Client, raw_dir: Path) -> dict[str, dict]:
    """Series metadata keyed by ticker. Fee model is read per series, per run."""
    out: dict[str, dict] = {}
    for category in CATEGORIES:
        resp = _get(client, f"{BASE}/series", {"category": category})
        payload = resp.json()
        with gzip.open(raw_dir / f"series_{category.replace(' ', '_')}.json.gz", "wt") as fh:
            fh.write(resp.text)
        for series in payload.get("series") or []:
            out[series["ticker"]] = {
                "category": category,
                "fee_type": series.get("fee_type", ""),
                "fee_multiplier": str(series.get("fee_multiplier", "")),
            }
    return out


def fetch_markets(client: httpx.Client, raw_dir: Path) -> list[dict]:
    """Every open market, one page at a time, raw pages persisted as fetched."""
    markets: list[dict] = []
    cursor: str | None = None
    page = 0
    while True:
        params: dict[str, str | int] = {
            "status": "open",
            "limit": PAGE_SIZE,
            "mve_filter": MVE_FILTER,
        }
        if cursor:
            params["cursor"] = cursor
        resp = _get(client, f"{BASE}/markets", params)
        with gzip.open(raw_dir / f"markets_page_{page:04d}.json.gz", "wt") as fh:
            fh.write(resp.text)
        payload = resp.json()
        batch = payload.get("markets") or []
        markets.extend(batch)
        cursor = payload.get("cursor") or None
        page += 1
        if not cursor or not batch:
            break
    return markets


def fetch_events(client: httpx.Client, raw_dir: Path) -> dict[str, dict]:
    """Open events, for grouping markets into candidate baskets.

    `mutually_exclusive` is captured for *measurement segmentation only*. It is
    a venue claim about at-most-one-YES and says nothing about exhaustiveness;
    it must never reach the Phase 2 approval flow. KXDEELRIP-40 is the standing
    counterexample.
    """
    out: dict[str, dict] = {}
    cursor: str | None = None
    page = 0
    while True:
        params: dict[str, str | int] = {"status": "open", "limit": 200}
        if cursor:
            params["cursor"] = cursor
        resp = _get(client, f"{BASE}/events", params)
        with gzip.open(raw_dir / f"events_page_{page:04d}.json.gz", "wt") as fh:
            fh.write(resp.text)
        payload = resp.json()
        batch = payload.get("events") or []
        for event in batch:
            out[event["event_ticker"]] = {
                "mutually_exclusive": bool(event.get("mutually_exclusive")),
                "title": event.get("title", ""),
                "series_ticker": event.get("series_ticker", ""),
            }
        cursor = payload.get("cursor") or None
        page += 1
        if not cursor or not batch:
            break
    return out


def backfill_series(
    client: httpx.Client, series_meta: dict[str, dict], tickers: set[str]
) -> set[str]:
    """Fetch series that the category sweep missed, one by one.

    `GET /series?category=` requires knowing the category names in advance and
    the API does not enumerate them, so a new category silently drops every
    series under it. Rather than trust the hardcoded list, resolve any prefix
    the sweep could not map. Whatever is still unresolved after this is a real
    anomaly and is reported, never guessed at.
    """
    still_missing: set[str] = set()
    for ticker in sorted(tickers):
        try:
            resp = _get(client, f"{BASE}/series/{ticker}")
            series = resp.json()["series"]
        except (httpx.HTTPError, KeyError):
            still_missing.add(ticker)
            continue
        series_meta[ticker] = {
            "category": series.get("category", ""),
            "fee_type": series.get("fee_type", ""),
            "fee_multiplier": str(series.get("fee_multiplier", "")),
        }
    return still_missing


def derive_rows(
    markets: list[dict], series_meta: dict[str, dict], captured_at: datetime
) -> tuple[list[MarketRow], set[str]]:
    rows: list[MarketRow] = []
    unmapped: set[str] = set()

    for market in markets:
        event_ticker = market.get("event_ticker", "")
        # Series ticker is the event ticker up to its first separator. Validated
        # against the series list below; misses are reported, not guessed at.
        series_ticker = event_ticker.split("-")[0] if event_ticker else ""
        meta = series_meta.get(series_ticker)
        if meta is None:
            unmapped.add(series_ticker)
            meta = {"category": "", "fee_type": "", "fee_multiplier": ""}

        bid_yes = _cents(market.get("yes_bid_dollars"))
        # ask(YES) = 100 - bid(NO); we recompute rather than trust the field.
        bid_no = _cents(market.get("no_bid_dollars"))
        ask_yes = Decimal(100) - bid_no

        two_sided = bid_yes > 0 and bid_no > 0
        spread = ask_yes - bid_yes
        mid = (ask_yes + bid_yes) / 2

        close_time = market.get("close_time", "")
        hours = Decimal(0)
        if close_time:
            closes = datetime.fromisoformat(close_time.replace("Z", "+00:00"))
            hours = Decimal((closes - captured_at).total_seconds()) / 3600

        rows.append(
            MarketRow(
                ticker=market.get("ticker", ""),
                event_ticker=event_ticker,
                series_ticker=series_ticker,
                category=meta["category"],
                fee_type=meta["fee_type"],
                fee_multiplier=meta["fee_multiplier"],
                bid_yes_cents=f"{bid_yes:.2f}",
                bid_no_cents=f"{bid_no:.2f}",
                ask_yes_cents=f"{ask_yes:.2f}",
                spread_cents=f"{spread:.2f}",
                mid_cents=f"{mid:.2f}",
                price_bucket=price_bucket(mid),
                bid_size=str(market.get("yes_bid_size_fp", "")),
                ask_size=str(market.get("yes_ask_size_fp", "")),
                open_interest=str(market.get("open_interest_fp", "")),
                volume_24h=str(market.get("volume_24h_fp", "")),
                close_time=close_time,
                hours_to_resolution=f"{hours:.2f}",
                two_sided=str(two_sided),
            )
        )
    return rows, unmapped


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", default="", help="tag for this sweep, e.g. t0 / t1")
    args = parser.parse_args()

    captured_at = datetime.now(UTC)
    stamp = captured_at.strftime("%Y%m%dT%H%M%SZ")
    name = f"{stamp}_{args.label}" if args.label else stamp
    sweep_dir = DATA_ROOT / name
    raw_dir = sweep_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    with httpx.Client(timeout=60.0, headers={"Accept": "application/json"}) as client:
        series_meta = fetch_series(client, raw_dir)
        print(f"series from category sweep: {len(series_meta)}")
        markets = fetch_markets(client, raw_dir)
        print(f"open markets (MVE excluded): {len(markets)}")
        events = fetch_events(client, raw_dir)
        print(f"open events: {len(events)}")

        _, unmapped = derive_rows(markets, series_meta, captured_at)
        if unmapped:
            print(f"backfilling {len(unmapped)} unmapped series...")
            unmapped = backfill_series(client, series_meta, unmapped)

    rows, still_unmapped = derive_rows(markets, series_meta, captured_at)
    two_sided = sum(1 for r in rows if r.two_sided == "True")

    csv_path = sweep_dir / "markets.csv"
    with csv_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(MarketRow.__dataclass_fields__))
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))

    events_path = sweep_dir / "events.csv"
    with events_path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["event_ticker", "series_ticker", "mutually_exclusive", "title"])
        for ticker, meta in sorted(events.items()):
            writer.writerow(
                [ticker, meta["series_ticker"], meta["mutually_exclusive"], meta["title"]]
            )

    manifest = {
        "captured_at": captured_at.isoformat(),
        "label": args.label,
        "base_url": BASE,
        "n_series": len(series_meta),
        "n_events": len(events),
        "n_markets": len(rows),
        "n_two_sided": two_sided,
        "series_resolved": len(series_meta),
        "unmapped_series": sorted(still_unmapped),
    }
    (sweep_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"two-sided: {two_sided}  unresolved series prefixes: {len(still_unmapped)}")
    print(f"wrote {csv_path}")


if __name__ == "__main__":
    main()
