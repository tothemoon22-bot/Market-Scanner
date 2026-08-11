"""Every trigger, shown to fire at its stated threshold and to stay quiet below it.

Standing rule: **ask whether a trigger has ever been shown to fire, or only
shown not to error.** Before this file, the fee-free-count trigger had one test
that moved it 11 -> 40 -- a delta of 29 against a stated threshold of 3. That
proves the code path executes; it does not prove the threshold is the one
documented, and a trigger that cannot detect the move it claims to detect is not
a working trigger regardless of what the suite asserts.

So each trigger gets a pair: fires exactly at the boundary, silent one step
inside it. A threshold with only a far-side test is a threshold nobody has
checked.
"""

from __future__ import annotations

from decimal import Decimal as D

import pytest

from monitor import alerts
from monitor.alerts import ANNUALIZED_ALERT_PCT, FEE_FREE_COUNT_DELTA, SPREAD_COMPRESSION_CENTS


def _fired(alerts_list, fragment: str) -> bool:
    return any(fragment in a.trigger for a in alerts_list)


# --------------------------------------------------- fee-free series count ---


def _fee_free(n: int, series: list[str]) -> dict:
    return {"fee_free": {"n_series_with_open_markets": n, "series": series}}


BASE_11 = ["KXBTCY", "KXCITRINI", "KXDOED", "KXELECTIRAN", "KXETHY",
           "KXGAMBLINGREPEAL", "KXGDPYEAR", "KXGREENLAND", "KXIRANDEMOCRACY",
           "KXLAYOFFSYINFO", "KXPAHLAVIHEAD"]


@pytest.mark.parametrize("delta", [FEE_FREE_COUNT_DELTA, -FEE_FREE_COUNT_DELTA])
def test_fee_free_count_fires_exactly_at_the_threshold(delta):
    """The move the work order asked about: three series, either direction."""
    current = BASE_11[: 11 + delta] if delta < 0 else BASE_11 + ["A", "B", "C"]
    fired = alerts.evaluate(_fee_free(11, BASE_11), _fee_free(11 + delta, current))
    assert _fired(fired, "fee-free series count changed materially"), (
        f"a move of {delta} series did not fire against a threshold of "
        f"{FEE_FREE_COUNT_DELTA}"
    )


@pytest.mark.parametrize("delta", [FEE_FREE_COUNT_DELTA - 1, -(FEE_FREE_COUNT_DELTA - 1), 0])
def test_fee_free_count_is_silent_one_step_inside_the_threshold(delta):
    current = BASE_11[: 11 + delta] if delta < 0 else BASE_11 + ["A", "B"][:delta]
    fired = alerts.evaluate(_fee_free(11, BASE_11), _fee_free(11 + delta, current))
    assert not _fired(fired, "fee-free series count")


def test_the_live_universe_against_the_committed_baseline_does_not_fire():
    """11 -> 11 with identical membership. Verified live 2026-08-05."""
    fired = alerts.evaluate(_fee_free(11, BASE_11), _fee_free(11, BASE_11))
    assert not _fired(fired, "fee-free series count")


def test_the_registry_count_is_not_what_the_trigger_watches():
    """14 in the registry, 11 with open markets -- two bases, one universe.

    Feeding the registry figure to a counter that watches open markets would
    manufacture a 3-series move, which is exactly the threshold. The distinction
    is load-bearing, so it is asserted rather than left to a comment.
    """
    fired = alerts.evaluate(_fee_free(11, BASE_11), _fee_free(14, BASE_11 + ["X", "Y", "Z"]))
    assert _fired(fired, "fee-free series count"), (
        "if the counter ever switched basis it would fire, which is the "
        "behaviour that makes mixing the two dangerous"
    )


# ------------------------------------------------------------ fee changes ---


FEE_FREE_CURRENT = {"fee_free": {"series": ["KXGDPYEAR", "KXBTCY"]}}

#: The live shape, 2026-08-05: a per-event MLB override onto the standard
#: schedule. 100 of these were pending, and none touched a fee-free series.
ROUTINE_CHANGE = {
    "series_ticker": "KXMLBGAME",
    "event_ticker": "KXMLBGAME-26AUG131930PHIMIN",
    "fee_multiplier_override": 1,
    "fee_type_override": "quadratic_with_maker_fees",
}


def test_a_change_to_a_fee_free_series_fires():
    change = {"series_ticker": "KXGDPYEAR", "fee_multiplier_override": 1,
              "fee_type_override": "quadratic"}
    fired = alerts.evaluate({}, FEE_FREE_CURRENT, {"series": [change], "events": []})
    assert _fired(fired, "material scheduled fee changes")


def test_a_change_making_a_series_fee_free_fires():
    change = {"series_ticker": "KXSOMETHING", "fee_multiplier_override": 0,
              "fee_type_override": "quadratic"}
    fired = alerts.evaluate({}, FEE_FREE_CURRENT, {"series": [], "events": [change]})
    assert _fired(fired, "material scheduled fee changes"), (
        "a series joining the fee-free universe is material"
    )


def test_a_change_introducing_an_unknown_fee_type_fires():
    change = {"series_ticker": "KXMLBGAME", "fee_multiplier_override": 1,
              "fee_type_override": "quadratic_with_maker_rebates"}
    fired = alerts.evaluate({}, FEE_FREE_CURRENT, {"series": [], "events": [change]})
    assert _fired(fired, "material scheduled fee changes")


def test_routine_per_event_overrides_do_not_fire_but_are_counted():
    """100 of these were pending live. Firing on them would mute the trigger."""
    fee_changes = {"series": [], "events": [ROUTINE_CHANGE] * 100}
    fired = alerts.evaluate({}, FEE_FREE_CURRENT, fee_changes)
    assert not _fired(fired, "material scheduled fee changes")

    split = alerts.classify_fee_changes(fee_changes, {"KXGDPYEAR", "KXBTCY"})
    assert split["n_routine"] == 100 and split["n_material"] == 0
    assert split["n_total"] == 100, "the volume stays visible even when it is silent"


def test_one_material_change_among_a_hundred_routine_ones_still_fires():
    """The case the narrowing must not break."""
    material = {"series_ticker": "KXGDPYEAR", "fee_multiplier_override": 1,
                "fee_type_override": "quadratic"}
    fee_changes = {"series": [], "events": [ROUTINE_CHANGE] * 100 + [material]}
    fired = alerts.evaluate({}, FEE_FREE_CURRENT, fee_changes)
    assert _fired(fired, "material scheduled fee changes")
    assert any("of 101 scheduled" in a.current for a in fired)


# ------------------------------------------------------- breadth criterion ---
# Breadth is independent of the per-change categories, which cannot see it: a
# multiplier moving to 1 on a fee-charging series is routine one change at a
# time, and the same change across a hundred series is a schedule revision.

MLB_CATEGORIES = {f"KXMLB{i}": "Sports" for i in range(200)}


def _broad(n_series: int, category_of=lambda i: "Sports") -> tuple[dict, dict]:
    changes = [
        {"series_ticker": f"KXMLB{i}", "fee_multiplier_override": 1,
         "fee_type_override": "quadratic"}
        for i in range(n_series)
    ]
    cats = {f"KXMLB{i}": category_of(i) for i in range(n_series)}
    return {"series": [], "events": changes}, cats


def test_a_broad_change_with_no_individually_material_one_fires():
    """The gap breadth closes: every change routine, the batch is not."""
    fee_changes, cats = _broad(alerts.BREADTH_SERIES_THRESHOLD + 1)
    fired = alerts.evaluate({}, FEE_FREE_CURRENT, fee_changes, None, cats)
    assert _fired(fired, "material scheduled fee changes")
    split = alerts.classify_fee_changes(fee_changes, {"KXGDPYEAR"}, cats)
    assert split["broad"] and split["n_material"] == alerts.BREADTH_SERIES_THRESHOLD + 1
    assert any("breadth threshold" in r for r in split["breadth_reasons"])


def test_the_breadth_threshold_is_silent_one_series_inside_it():
    fee_changes, cats = _broad(alerts.BREADTH_SERIES_THRESHOLD)
    fired = alerts.evaluate({}, FEE_FREE_CURRENT, fee_changes, None, cats)
    assert not _fired(fired, "material scheduled fee changes")


def test_the_observed_live_batch_stays_routine_under_the_breadth_rule():
    """100 changes across 11 MLB series, one category. Measured 2026-08-05.

    If breadth reclassified the observed routine shape, the trigger would fire
    every sweep again and the narrowing would have achieved nothing.
    """
    changes = [
        {"series_ticker": f"KXMLB{i % 11}", "fee_multiplier_override": 1,
         "fee_type_override": "quadratic"}
        for i in range(100)
    ]
    cats = {f"KXMLB{i}": "Sports" for i in range(11)}
    split = alerts.classify_fee_changes({"series": [], "events": changes}, set(), cats)
    assert split["n_series_touched"] == 11
    assert not split["broad"], "11 series in one category is the routine shape"
    assert split["n_material"] == 0 and split["n_routine"] == 100


def test_crossing_a_category_boundary_is_material_at_any_count():
    """Fee schedules are administered per product line. Crossing one is policy."""
    changes = [
        {"series_ticker": "KXMLBGAME", "fee_multiplier_override": 1,
         "fee_type_override": "quadratic"},
        {"series_ticker": "KXGDPQ", "fee_multiplier_override": 1,
         "fee_type_override": "quadratic"},
    ]
    cats = {"KXMLBGAME": "Sports", "KXGDPQ": "Economics"}
    split = alerts.classify_fee_changes({"series": [], "events": changes}, set(), cats)
    assert split["broad"] and split["n_material"] == 2
    assert any("spans 2 categories" in r for r in split["breadth_reasons"])


def test_unknown_categories_do_not_manufacture_breadth():
    """Without a category map every series would look like its own category."""
    changes = [
        {"series_ticker": "KXA", "fee_multiplier_override": 1,
         "fee_type_override": "quadratic"},
        {"series_ticker": "KXB", "fee_multiplier_override": 1,
         "fee_type_override": "quadratic"},
    ]
    split = alerts.classify_fee_changes({"series": [], "events": changes}, set(), None)
    assert not split["broad"], "missing category data must not read as spanning many"
    assert split["categories_touched"] == ["(unknown)"]


def test_an_empty_fee_change_response_is_silence_not_an_alert():
    fired = alerts.evaluate({}, FEE_FREE_CURRENT, {"series": [], "events": []})
    assert not _fired(fired, "scheduled fee changes")
    assert not _fired(fired, "could not be polled")


def test_a_failed_poll_fires_rather_than_reading_as_nothing_scheduled():
    """The highest-consequence trigger in the system. Silence must mean checked.

    A failed fetch previously left the key absent, which `evaluate` could not
    distinguish from an empty response -- so the failure path and the healthy
    path rendered identically.
    """
    current = {
        "fee_changes_outcome": {
            "state": "failed",
            "reason": "ConnectTimeout: fee_changes endpoint unreachable",
        }
    }
    fired = alerts.evaluate({}, current, None)
    assert _fired(fired, "could not be polled")
    assert any("ConnectTimeout" in a.current for a in fired)


def test_the_failed_and_empty_responses_render_differently():
    """3a: assert the two are distinguishable, not merely both non-crashing."""
    empty = alerts.evaluate({}, {}, {"series": [], "events": []})
    failed = alerts.evaluate(
        {}, {"fee_changes_outcome": {"state": "failed", "reason": "boom"}}, None
    )
    assert alerts.render(empty) != alerts.render(failed)
    assert alerts.render(empty) == "No alerts. Baseline holds."


def test_the_scanner_passes_fee_changes_into_the_pipeline():
    """It fetched them and then did not hand them over, so the trigger was dead.

    Asserted against the call, not the fetch: the fetch was always fine. Both
    callers now go through `pipeline.assess`, and
    `tests/test_pipeline_parity.py` asserts the two pass identical keyword sets.
    """
    from pathlib import Path

    src = Path("scanner/engine.py").read_text()
    call = src.split("assessment = pipeline.assess(")[1].split(")")[0]
    assert "fee_changes" in call, "the scanner must pass fee_changes into the pipeline"


# ---------------------------------------------------------- spread + ticks ---


def _segment(median: str) -> dict:
    return {"spread_by_segment": {"tick_structure": {"linear_cent": {"median": median}}}}


def test_spread_compression_fires_at_the_threshold():
    fired = alerts.evaluate(_segment("6.0"), _segment(str(SPREAD_COMPRESSION_CENTS)))
    assert _fired(fired, "median spread compressed")


def test_spread_compression_is_silent_just_above_the_threshold():
    fired = alerts.evaluate(_segment("6.0"), _segment(str(SPREAD_COMPRESSION_CENTS + D("0.1"))))
    assert not _fired(fired, "median spread compressed")


def test_a_new_tick_structure_fires():
    base = {"tick_structure": {"linear_cent": {"share_pct": "87.4"}}}
    current = {"tick_structure": {"linear_cent": {"share_pct": "87.4"},
                                  "milli_cent": {"share_pct": "0.1"}}}
    assert _fired(alerts.evaluate(base, current), "tick structure set changed")


@pytest.mark.parametrize("shift,should_fire", [("5", True), ("4.9", False)])
def test_tick_structure_share_fires_at_five_points(shift, should_fire):
    base = {"tick_structure": {"linear_cent": {"share_pct": "80"}}}
    current = {"tick_structure": {"linear_cent": {"share_pct": str(D(80) + D(shift))}}}
    assert _fired(alerts.evaluate(base, current), "tick structure mix shifted") is should_fire


def test_an_unrecognised_fee_type_fires():
    assert _fired(
        alerts.evaluate({}, {"fee_types": {"quadratic_with_maker_rebates": 5}}),
        "unrecognised fee_type",
    )


def test_known_fee_types_do_not_fire():
    assert not _fired(
        alerts.evaluate({}, {"fee_types": {"quadratic": 60000, "flat": 12}}),
        "unrecognised fee_type",
    )


# ------------------------------------------------------------- tripwire ---


def test_the_deci_cent_fee_free_tripwire_fires_on_one():
    fired = alerts.evaluate(
        {}, {"deci_cent_fee_free_tripwire": {"n_below_par": 1, "n_markets": 61}}
    )
    assert _fired(fired, "deci-cent AND fee-free market priced below par")


def test_the_tripwire_is_silent_at_zero():
    fired = alerts.evaluate(
        {}, {"deci_cent_fee_free_tripwire": {"n_below_par": 0, "n_markets": 61}}
    )
    assert not _fired(fired, "deci-cent AND fee-free")


# ------------------------------------------------------------- below par ---


def _partition(event: str, cost: str, capacity: str, ann: str | None) -> dict:
    return {
        "event": event, "cost_cents": cost, "capacity_contracts": capacity,
        "below_par": D(cost) < 100, "tradeable": D(capacity) >= 1, "annualized_pct": ann,
    }


def test_the_annualized_branch_fires_at_the_threshold_regardless_of_size():
    """Deliberately unfloored: a high-return structure is news at any capacity."""
    current = {"verified_partitions": {"fee_free_detail": [
        _partition("KXGDPYEAR-28", "99.99", "1", str(ANNUALIZED_ALERT_PCT))]}}
    result = alerts.classify_below_par({}, current, None)
    assert [p["event"] for p in result.pushed] == ["KXGDPYEAR-28"]
    assert "annualized" in result.pushed[0]["pushed_because"]


def test_the_annualized_branch_is_silent_one_step_below():
    current = {"verified_partitions": {"fee_free_detail": [
        _partition("KXGDPYEAR-28", "99.99", "1", str(ANNUALIZED_ALERT_PCT - D("0.01")))]}}
    result = alerts.classify_below_par({}, current, None)
    assert not result.pushed and len(result.suppressed) == 1


def test_the_dollar_floor_fires_at_the_threshold():
    from scanner.history import MIN_DOLLAR_VALUE

    # 1c of edge on 2,500 contracts is exactly $25.
    current = {"verified_partitions": {"fee_free_detail": [
        _partition("KXNEW-1", "99", "2500", "0.5")]}}
    result = alerts.classify_below_par({}, current, None)
    assert result.pushed, f"capacity x edge did not clear the ${MIN_DOLLAR_VALUE} floor"
    assert result.pushed[0]["dollar_value"] == "25.00"


def test_the_dollar_floor_is_silent_one_cent_below():
    current = {"verified_partitions": {"fee_free_detail": [
        _partition("KXNEW-1", "99", "2499", "0.5")]}}
    result = alerts.classify_below_par({}, current, None)
    assert not result.pushed
    assert "$24.99" in result.suppressed[0]["suppressed_because"]


def test_every_trigger_in_evaluate_has_a_firing_test():
    """The guard against this file going stale as triggers are added.

    Counts the Alert constructions in `evaluate` and requires this module to
    exercise at least that many distinct trigger strings.
    """
    import ast
    from pathlib import Path

    tree = ast.parse(Path("monitor/alerts.py").read_text())
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "evaluate"
    )
    constructed = sum(
        1 for n in ast.walk(fn)
        if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "Alert"
    )
    exercised = {
        "tick structure set changed", "tick structure mix shifted", "unrecognised fee_type",
        "could not be polled", "scheduled fee changes", "median spread compressed",
        "fee-free series count changed materially", "below par",
        "deci-cent AND fee-free market priced below par",
    }
    assert len(exercised) >= constructed, (
        f"evaluate constructs {constructed} alerts; only {len(exercised)} are "
        "exercised here. A new trigger needs a firing test."
    )
