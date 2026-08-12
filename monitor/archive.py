"""Ledger durability: the weekly job pulls, the box holds no credential.

Partition history, band transitions, the suppression ledger and the population
trend are written only by the scanner, on its own disk. The weekly archive job
runs from a fresh checkout and cannot accumulate them, so instance loss would
take the lot -- while ``docs/DEPLOY.md`` requires the archive to outlive the
instance.

**The direction of the transfer is the security property.** The scanner exposes
the ledgers on a read-only, unauthenticated endpoint and the weekly GitHub
Action fetches and commits them. The Action already holds repository write
permission; the box gains nothing. Pushing from the box would have meant putting
a repository credential on a machine whose entire design is that it holds none.

Ledger contents are market statistics -- no credentials, no positions, no
personal data -- so public readability is acceptable, and mildly useful to
anyone reproducing the study.

Usage, from the Action::

    python -m monitor.archive --from https://scanner.example.com

Exits non-zero on any failure so the Action fails loudly rather than committing
a silent no-op. Staleness is *also* visible from the other side: the scanner
records each successful fetch and the dashboard shows its age, so an Action that
stops running shows up on the panel rather than only in a workflow history
nobody reads.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

import httpx

#: Ledgers the scanner exposes, by stable name. **An allowlist, not a path
#: join**: serving `data/monitor/<name>` with a caller-supplied name lets a
#: caller escape the directory, and this endpoint is unauthenticated.
LEDGER_FILES: dict[str, Path] = {
    "series_history": Path("data/monitor/series_history.jsonl"),
    "suppressed": Path("data/monitor/suppressed.jsonl"),
    "band_events": Path("data/monitor/band_events.jsonl"),
    "population_trend": Path("data/monitor/population.jsonl"),
    "manual_reviews": Path("data/monitor/manual_reviews.jsonl"),
    "discontinuities": Path("data/monitor/discontinuities.jsonl"),
}

#: Where the weekly job commits them. **Not** under ``data/``, which is
#: gitignored: force-adding into an ignored path survives until someone runs
#: ``git clean -X`` and silently takes the archive with it. Restoring onto a
#: fresh box is a copy back into ``data/monitor/``; see docs/DEPLOY.md.
ARCHIVE_DIR = Path("archive/ledgers")

#: A weekly job that has not run in this long is not late, it is broken.
ARCHIVE_STALE_AFTER_DAYS = 10


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def manifest(root: Path = Path()) -> dict[str, dict]:
    """Name, size, line count and digest for each ledger that exists.

    The digest lets the fetcher skip an unchanged file and the Action skip an
    empty commit, without either side having to diff.
    """
    out: dict[str, dict] = {}
    for name, rel in LEDGER_FILES.items():
        path = root / rel
        if not path.exists():
            out[name] = {"present": False, "bytes": 0, "lines": 0, "sha256": None}
            continue
        text = path.read_text()
        out[name] = {
            "present": True,
            "bytes": len(text.encode("utf-8")),
            "lines": sum(1 for line in text.splitlines() if line.strip()),
            "sha256": digest(text),
        }
    return out


def fetch(base_url: str, into: Path | None = None, timeout: float = 60.0) -> dict[str, dict]:
    """Pull every ledger the scanner reports, write it, and report what changed.

    Raises on any transport or integrity failure. The caller is the weekly
    Action, and a silent partial archive is worse than a failed one.
    """
    base = base_url.rstrip("/")
    into = ARCHIVE_DIR if into is None else into
    results: dict[str, dict] = {}
    with httpx.Client(timeout=timeout, headers={"Accept": "application/json"}) as client:
        resp = client.get(f"{base}/api/ledgers")
        resp.raise_for_status()
        remote = resp.json()["ledgers"]

        for name, meta in sorted(remote.items()):
            if name not in LEDGER_FILES:
                # The scanner offered something this version does not know.
                # Recorded rather than silently skipped.
                results[name] = {"status": "unknown-ledger", "written": False}
                continue
            if not meta["present"]:
                results[name] = {"status": "absent-on-scanner", "written": False}
                continue

            body = client.get(f"{base}/api/ledgers/{name}")
            body.raise_for_status()
            text = body.text
            got = digest(text)
            if got != meta["sha256"]:
                raise ValueError(
                    f"{name}: digest mismatch, manifest said {meta['sha256'][:12]} "
                    f"and the body hashes to {got[:12]}"
                )

            target = into / f"{name}.jsonl"
            unchanged = target.exists() and digest(target.read_text()) == got
            if not unchanged:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text)
            results[name] = {
                "status": "unchanged" if unchanged else "written",
                "written": not unchanged,
                "lines": meta["lines"],
                "bytes": meta["bytes"],
            }
    return results


def render(results: dict[str, dict]) -> str:
    lines = []
    for name, r in sorted(results.items()):
        detail = (
            f"{r.get('lines', 0):,} lines, {r.get('bytes', 0):,} bytes"
            if "lines" in r
            else ""
        )
        lines.append(f"  {name:<20} {r['status']:<18} {detail}")
    changed = sum(1 for r in results.values() if r.get("written"))
    lines.append(f"  {changed} of {len(results)} ledger(s) changed")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--from", dest="base_url", required=True, help="scanner base URL, HTTPS"
    )
    parser.add_argument("--into", type=Path, default=ARCHIVE_DIR)
    args = parser.parse_args()

    try:
        results = fetch(args.base_url, args.into)
    except Exception as exc:  # noqa: BLE001 - the Action must fail loudly
        print(f"ledger archive FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(render(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
