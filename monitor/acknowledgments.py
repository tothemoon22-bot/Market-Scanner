"""Acknowledged observations: append-only, per-observation, never a baseline edit.

`center_half_edge_half_cent` appeared on 2026-08-05. The falsifier fired, the
conclusion survived, and the memo records it. But **the baseline is immutable and
will never contain that structure**, so the tick-structure trigger reads 100%
for the rest of the project's life — and a permanently-firing trigger is a
permanently-ignored one. Worse, it occupies the hero's closest-trigger slot
forever, hiding whatever is genuinely closest.

This is the failure the below-par alert was designed around: the baseline
already held a known below-par partition, and firing on "any" would have paged
weekly about a known non-opportunity. Caught there; missed here.

The rules, and each one is load-bearing:

**Acknowledgment is an append, never an edit to the baseline.** Regenerating the
baseline is how you accept a new normal, and it destroys the comparison. An
acknowledgment records that a *specific observation* has been examined and
written up; the baseline still says what it always said.

**Acknowledgment is per-observation, not per-trigger.** The key is the thing
observed -- the structure name -- not the trigger it fired. A *different*
tick-structure change fires again at full priority, because it is a different
observation that nobody has looked at.

**Acknowledged triggers stay visible.** They render with their date and memo
reference and are excluded from ranking. Nothing disappears; an acknowledgment
is a statement that something was examined, not that it stopped being true.

Every entry names the memo section that records the finding, so an
acknowledgment cannot be a way of quietly dismissing something -- if it is not
written up, it is not acknowledged.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

LEDGER_PATH = Path("data/monitor/acknowledgments.jsonl")

#: Committed acknowledgments, replayed on every start so a fresh box or a fresh
#: checkout behaves identically to one with the ledger on disk. The runtime
#: ledger is additive on top of these.
SEEDED: tuple[dict[str, str], ...] = (
    {
        "trigger": "tick_structure",
        "observation": "center_half_edge_half_cent",
        "at": "2026-08-05",
        "memo_section": "Finding 4 - the falsifier fired",
        "note": (
            "Fourth tick structure, 30 KXBRASILEIROGAME markets on a half-cent "
            "grid. Tightest spread 1.0c against a 0.6c median in deci_cent, so "
            "the most favourable location on the exchange is unchanged."
        ),
    },
)


@dataclass(frozen=True)
class Acknowledgment:
    trigger: str
    observation: str
    at: str
    memo_section: str
    note: str = ""

    @property
    def key(self) -> tuple[str, str]:
        return (self.trigger, self.observation)

    def as_dict(self) -> dict[str, Any]:
        return {
            "trigger": self.trigger,
            "observation": self.observation,
            "at": self.at,
            "memo_section": self.memo_section,
            "note": self.note,
        }


def _load_file(path: Path) -> list[Acknowledgment]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        if line.strip():
            row = json.loads(line)
            out.append(
                Acknowledgment(
                    trigger=row["trigger"],
                    observation=row["observation"],
                    at=row["at"],
                    memo_section=row["memo_section"],
                    note=row.get("note", ""),
                )
            )
    return out


def load(path: Path = LEDGER_PATH) -> dict[tuple[str, str], Acknowledgment]:
    """Seeded acknowledgments plus whatever the ledger holds, keyed per observation."""
    out: dict[tuple[str, str], Acknowledgment] = {}
    for row in SEEDED:
        ack = Acknowledgment(**row)
        out[ack.key] = ack
    for ack in _load_file(path):
        out[ack.key] = ack
    return out


def record(
    trigger: str,
    observation: str,
    memo_section: str,
    note: str = "",
    path: Path = LEDGER_PATH,
    at: str | None = None,
) -> Acknowledgment:
    """Append one acknowledgment. Requires a memo section: no write-up, no ack."""
    if not memo_section.strip():
        raise ValueError(
            "an acknowledgment must name the memo section that records the "
            "observation; otherwise it is a way of dismissing something quietly"
        )
    ack = Acknowledgment(
        trigger=trigger,
        observation=observation,
        at=at or datetime.now(UTC).date().isoformat(),
        memo_section=memo_section,
        note=note,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(json.dumps(ack.as_dict()) + "\n")
    return ack


def observations_for(trigger_key: str, detail: dict[str, Any]) -> list[str]:
    """What a fired trigger is firing *about*, as acknowledgeable observations.

    Keyed to the thing observed rather than the trigger, so a different change
    of the same kind is a different observation and fires at full priority.
    """
    if trigger_key == "tick_structure":
        return sorted([*detail.get("added", []), *detail.get("removed", [])])
    if trigger_key == "below_par_partition":
        return sorted(detail.get("newsworthy", []))
    if trigger_key == "tripwire":
        return sorted(detail.get("below_par_events", []))
    return []
