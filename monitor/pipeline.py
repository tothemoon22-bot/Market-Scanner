"""The one assessment path. Both callers go through here.

**Two implementations of one pipeline, with the tests pointed at the one that
isn't production.** That is how the scheduled-fee-change trigger came to be dead
in the continuous scanner while `monitor/run.py` exercised it happily: the
scanner fetched `fee_changes` and then called ``evaluate()`` without them, and
nothing in a signature check could see it because the parameter has a permissive
default.

The audit that followed found the same shape a second time, live:
``monitor/run.py`` called ``evaluate(baseline, current, fee_changes)`` and
omitted ``bands``, so the weekly job's below-par classification never ran its
band branch at all.

So the arguments are no longer a caller's responsibility. Everything that both
paths must agree on is assembled here, once:

* the per-event oscillation bands, read from the history ledger
* the below-par push/suppress split
* the alert set
* the trigger board
* the scheduled-fee-change materiality split

Callers supply only what genuinely differs between them -- the sweep result, the
fee-change payload, and where the history ledger lives -- and differ only in
what they *do* with the assessment: the scanner pushes and renders, the weekly
job archives and prints.

``tests/test_pipeline_parity.py`` asserts the two call sites pass identical
keyword sets, which is the check that would have caught the original bug.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from monitor import alerts as alerts_mod
from monitor.alerts import Alert, BelowParResult


@dataclass(frozen=True)
class Assessment:
    """Everything derived from one sweep that both paths must agree on."""

    alerts: list[Alert]
    below_par: BelowParResult
    bands: dict[str, Any]
    fee_changes: dict[str, Any] | None
    fee_change_split: dict[str, Any] = field(default_factory=dict)

    @property
    def fired(self) -> bool:
        return bool(self.alerts)


def assess(
    baseline: dict,
    current: dict,
    *,
    fee_changes: dict | None = None,
    series_categories: dict[str, str] | None = None,
    history_path: Path | None = None,
) -> Assessment:
    """Assess one sweep. The only place ``evaluate`` is called from.

    ``bands`` are computed here rather than passed in, because a band argument a
    caller can forget is exactly the defect this module exists to remove.
    """
    from scanner import history

    bands = history.bands(history_path) if history_path else history.bands()

    split: dict[str, Any] = {}
    if fee_changes is not None:
        split = alerts_mod.classify_fee_changes(
            fee_changes,
            set(current.get("fee_free", {}).get("series", [])),
            series_categories,
        )
        # Published into `current` rather than passed to the trigger board as an
        # argument. The board must apply the same materiality rule as the alert
        # -- 100 routine per-event overrides are not a fired trigger -- and an
        # extra parameter one caller forgets is the divergence shape this
        # module exists to remove.
        current["fee_change_split"] = {
            k: v for k, v in split.items() if k not in ("material", "routine")
        }

    return Assessment(
        alerts=alerts_mod.evaluate(
            baseline, current, fee_changes, bands, series_categories
        ),
        below_par=alerts_mod.classify_below_par(baseline, current, bands),
        bands=bands,
        fee_changes=fee_changes,
        fee_change_split=split,
    )
