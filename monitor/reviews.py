"""Dated manual obligations, and a ledger so a missed one is visible.

Some things this monitor depends on cannot be polled. The **0.07 taker
coefficient and the rounding rule live in a published PDF filed with the CFTC**,
not in any API — a change to them is the single structural change that would
most directly invalidate the negative result, and no code can see it.

The available programmatic proxies (`fee_type` / `fee_multiplier` per series,
and the scheduled-fee-change endpoints) are tracked continuously. The
coefficient itself is a standing manual obligation, and a manual obligation with
no due date is one that quietly stops happening.

So each item carries a period and a ledger of when it was last done. The
heartbeat states the due date and how overdue it is; a missed quarter shows as a
number, not as an absence.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

LEDGER_PATH = Path("data/monitor/manual_reviews.jsonl")


@dataclass(frozen=True)
class ManualReview:
    key: str
    what: str
    why: str
    period_days: int


REVIEWS = (
    ManualReview(
        key="fee_schedule_pdf",
        what="Re-read Kalshi's published fee schedule by hand and confirm the "
        "0.07 taker coefficient and the round-up rule are unchanged.",
        why="The coefficient is not exposed by any endpoint. No alert in this "
        "system can detect a change to it. This is the only check that can.",
        period_days=91,
    ),
    ManualReview(
        key="residual_threshold",
        what="Review the unattributed-residual threshold against the recorded "
        "residual rates, and decide between an absolute count and a rate rule.",
        why="25 is provisional: its original anchor was a classifier artifact "
        "and was retracted, leaving one clean observation.",
        period_days=56,
    ),
)


def _ledger(path: Path = LEDGER_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def record_done(key: str, note: str = "", path: Path = LEDGER_PATH) -> dict[str, Any]:
    """Log that a manual review was carried out. The only way the clock resets."""
    if key not in {r.key for r in REVIEWS}:
        raise KeyError(f"unknown review {key!r}")
    entry = {"at": datetime.now(UTC).isoformat(), "key": key, "note": note}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(json.dumps(entry) + "\n")
    return entry


def status(
    since: datetime | None = None, path: Path = LEDGER_PATH, now: datetime | None = None
) -> list[dict[str, Any]]:
    """Each review, when it was last done, and how overdue it is.

    ``since`` anchors an item that has never been done -- normally the date the
    monitor started. Without it a never-done item would report no due date at
    all, which is the absence this module exists to remove.
    """
    now = now or datetime.now(UTC)
    done: dict[str, datetime] = {}
    for row in _ledger(path):
        at = datetime.fromisoformat(row["at"])
        if row["key"] not in done or at > done[row["key"]]:
            done[row["key"]] = at

    out = []
    for review in REVIEWS:
        last = done.get(review.key)
        anchor = last or since or now
        due = anchor + timedelta(days=review.period_days)
        overdue_days = (now - due).days
        out.append(
            {
                "key": review.key,
                "what": review.what,
                "why": review.why,
                "period_days": review.period_days,
                "last_done": last.isoformat() if last else None,
                "ever_done": last is not None,
                "due": due.isoformat(),
                "overdue_days": max(0, overdue_days),
                "is_overdue": overdue_days >= 0,
            }
        )
    return out


def render(items: list[dict[str, Any]]) -> str:
    lines = ["manual checks (nothing in the API can do these):"]
    for item in items:
        when = item["last_done"][:10] if item["ever_done"] else "never"
        state = f"OVERDUE by {item['overdue_days']}d" if item["is_overdue"] else "ok"
        lines.append(f"  {item['key']}: last {when}, due {item['due'][:10]} -- {state}")
    return "\n".join(lines)
