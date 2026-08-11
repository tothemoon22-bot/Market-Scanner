"""Path divergence: two implementations of one pipeline, tests on the wrong one.

The scheduled-fee-change trigger was dead in the continuous scanner while
`monitor/run.py` exercised it happily. Nothing in a signature check could see
it, because `evaluate(baseline, current, fee_changes=None, bands=None)` accepts
a caller that simply omits an argument.

The audit found the same shape a second time, live and in the other direction:
`monitor/run.py` called `evaluate(baseline, current, fee_changes)` and omitted
`bands`, so the weekly job's below-par classification never ran its band branch.

**Equal outputs on one fixture would not have caught either.** Both paths would
have produced identical alerts on any fixture without a band-promotable or
fee-change event. So these tests compare the *inputs* each caller assembles, and
assert there is only one place they can be assembled.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from monitor import pipeline

CALLERS = {
    "scanner/engine.py": "the continuous scanner (production, 24/7)",
    "monitor/run.py": "the weekly archive job",
}

#: Stages that must not be reachable except through pipeline.assess. Each is a
#: function with at least one permissive default, which is the shape that hides
#: a divergence from every signature check.
UNIFIED_STAGES = ("evaluate", "classify_below_par")


def _calls_to(path: Path, attr: str) -> list[ast.Call]:
    tree = ast.parse(path.read_text())
    return [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == attr
    ]


def _assess_call(path: Path) -> ast.Call:
    calls = _calls_to(path, "assess")
    assert len(calls) == 1, f"{path} should reach the pipeline exactly once, found {len(calls)}"
    return calls[0]


@pytest.mark.parametrize("caller", CALLERS)
def test_no_caller_reaches_the_shared_stages_directly(caller):
    """Collapse to one call site, so divergence is structurally impossible."""
    path = Path(caller)
    for attr in UNIFIED_STAGES:
        offenders = [
            n
            for n in _calls_to(path, attr)
            # triggers.evaluate is the dashboard's trigger board, a different
            # function with a different contract; it is checked separately.
            if ast.unparse(n.func).startswith("alerts")
        ]
        assert not offenders, (
            f"{caller} calls alerts.{attr} directly at line "
            f"{offenders[0].lineno}. It must go through pipeline.assess, or the "
            "next omitted argument will be invisible again."
        )


def test_both_callers_pass_identical_keyword_sets():
    """The check that would have caught the original bug.

    An argument one caller omits does not appear as a signature mismatch, so the
    keyword sets are compared directly rather than the resulting alerts.
    """
    keywords = {}
    for caller in CALLERS:
        call = _assess_call(Path(caller))
        keywords[caller] = sorted(k.arg for k in call.keywords)

    engine, run = keywords["scanner/engine.py"], keywords["monitor/run.py"]
    assert engine == run, (
        f"the two paths assemble different arguments: scanner passes {engine}, "
        f"weekly job passes {run}. That difference is invisible to every "
        "signature check and is exactly how the fee-change trigger died."
    )


def test_every_permissive_default_on_assess_is_supplied_by_both_callers():
    """A default nobody passes is a default nobody has decided about."""
    import inspect

    optional = {
        name
        for name, p in inspect.signature(pipeline.assess).parameters.items()
        if p.default is not inspect.Parameter.empty
    }
    supplied = {
        caller: {k.arg for k in _assess_call(Path(caller)).keywords} for caller in CALLERS
    }
    # history_path is deliberately caller-agnostic: both use the default ledger,
    # and a test that forced it would be asserting a path, not a behaviour.
    must_supply = optional - {"history_path"}
    for caller, keys in supplied.items():
        missing = must_supply - keys
        assert not missing, f"{caller} omits {sorted(missing)} from pipeline.assess"


def test_bands_cannot_be_omitted_because_no_caller_supplies_them():
    """The specific fix: bands are assembled inside, not passed in.

    Previously the scanner passed bands and the weekly job did not. Making it a
    parameter with a default would have preserved the defect; it is computed in
    `assess` so there is nothing to forget.
    """
    import inspect

    params = inspect.signature(pipeline.assess).parameters
    assert "bands" not in params, (
        "bands must not be a parameter of assess -- a band argument a caller can "
        "forget is the defect this module exists to remove"
    )
    source = Path("monitor/pipeline.py").read_text()
    assert "history.bands(" in source


def test_the_assessment_carries_everything_both_paths_need():
    """If a caller has to reach past the Assessment, divergence returns."""
    fields = set(pipeline.Assessment.__dataclass_fields__)
    assert {"alerts", "below_par", "bands", "fee_changes", "fee_change_split"} <= fields


def test_assess_runs_end_to_end_on_the_committed_snapshot(tmp_path):
    """Real data through the unified path, with no history ledger present."""
    import json

    from monitor import aggregate, collect

    rows = collect.read_snapshot(Path("monitor/snapshots/20260803T070632Z_t0"))
    current = aggregate.SweepAggregate().fold(rows).result()
    baseline = json.loads(Path("monitor/baseline.json").read_text())

    result = pipeline.assess(
        baseline, current, fee_changes=None, history_path=tmp_path / "absent.jsonl"
    )
    assert result.alerts == [], "the committed baseline against itself must be quiet"
    assert result.bands == {}, "no history ledger means no bands, not an error"
    assert result.fee_change_split == {}, "no fee_changes means no split, not an empty split"
    assert result.fired is False


def test_a_fee_change_reaches_evaluate_through_the_unified_path(tmp_path):
    """The original bug, asserted end-to-end rather than at the call site."""
    material = {
        "series_ticker": "KXGDPYEAR",
        "fee_multiplier_override": 1,
        "fee_type_override": "quadratic",
    }
    result = pipeline.assess(
        {},
        {"fee_free": {"series": ["KXGDPYEAR"]}},
        fee_changes={"series": [material], "events": []},
        history_path=tmp_path / "absent.jsonl",
    )
    assert any("material scheduled fee changes" in a.trigger for a in result.alerts)
    assert result.fee_change_split["n_material"] == 1
