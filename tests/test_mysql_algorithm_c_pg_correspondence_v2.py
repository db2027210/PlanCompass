"""Executable correspondence checks against the current PostgreSQL C policy."""

from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import sys

import pytest

from scripts import mysql_algorithm_c_interval_control_v2 as mysql_interval
from scripts import mysql_outer_rejection_cycle_v3 as mysql_rejection
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v54 as runtime


ROOT = Path(__file__).resolve().parents[1]
PG_ROOT = ROOT.parent / "resource_adaptive"


def _load(name: str, path: Path):
    specification = importlib.util.spec_from_file_location(name, path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


pg_search = _load("pg_algorithm_c_adaptive_search_for_mysql_test", PG_ROOT / "algorithm_c_adaptive_search.py")
pg_regions = _load("pg_algorithm_c_budget_policy_for_mysql_test", PG_ROOT / "algorithm_c_budget_policy.py")


def _ranges(box: dict[str, tuple[float, float]]) -> dict[str, dict[str, float]]:
    return {
        name: {"lower": limits[0], "upper": limits[1]}
        for name, limits in box.items()
    }


def _positive_overlap(left: dict, right: dict) -> bool:
    return all(
        min(float(left[name]["upper"]), float(right[name]["upper"]))
        > max(float(left[name]["lower"]), float(right[name]["lower"]))
        for name in left
    )


def test_threshold_and_miss_transitions_match_current_pg() -> None:
    pg_policy = pg_search.AdaptiveSearchPolicy()
    assert pg_policy.rejected_batches_before_expansion == 1
    assert pg_policy.rejected_batches_before_shrink == 2
    assert mysql_rejection.REJECTIONS_BEFORE_SHRINK == 2
    assert mysql_rejection.TERMINAL_NORMALIZED_WIDTH == 0.01

    evidence = pg_search.AdaptiveSearchEvidence(
        completed_waves=0,
        attempted_search_replays=0,
        valid_search_replays=0,
        active_wall_s=0.0,
        consecutive_no_progress_waves=0,
        novel_plan_fingerprints=0,
        duplicate_plan_fraction=0.0,
        normalized_resource_gain=0.0,
        maximum_model_uncertainty=0.0,
        pending_region_count=0,
        best_pending_region_optimistic_gain=0.0,
        outer_qps_accepted=False,
        normalized_interval_width=0.01,
    )
    decision = pg_search.adaptive_search_decision(pg_policy, evidence)
    assert decision.action == "finish_search"
    assert decision.converged is True
    assert mysql_rejection.convergence_document(
        ranges={"x": {"lower": 1.0, "upper": math.exp(0.01)}},
        initial_ranges={"x": {"lower": 1.0, "upper": math.e}},
        active_params=["x"],
    )["converged"] is True
    assert mysql_rejection.policy_contract()["stability_confirmation_required"] is False


@pytest.mark.parametrize(
    ("anchor", "expected"),
    [
        (2.0, (1.0, 4.0)),
        (6.0, (4.0, 16.0)),
        (8.0, (4.0, 16.0)),
        (12.0, (4.0, 16.0)),
    ],
)
def test_second_miss_keeps_pg_half_interval(anchor: float, expected: tuple[float, float]) -> None:
    initial = {"x": {"lower": 1.0, "upper": 16.0}}
    unchanged, first = mysql_rejection.advance_rejection_cycle(
        accepted=False,
        consecutive_rejections=0,
        ranges=initial,
        incumbent_params={"x": anchor},
        active_params=["x"],
    )
    assert unchanged == initial
    assert first["consecutive_rejections_after"] == 1
    narrowed, second = mysql_rejection.advance_rejection_cycle(
        accepted=False,
        consecutive_rejections=1,
        ranges=unchanged,
        incumbent_params={"x": anchor},
        active_params=["x"],
    )
    assert narrowed["x"]["lower"] == pytest.approx(expected[0])
    assert narrowed["x"]["upper"] == pytest.approx(expected[1])
    assert second["forced_incumbent_shrink"] is True
    assert second["range_changes"]["x"]["selected_half"] in {"left", "right"}


def test_retained_regions_are_the_same_partition_as_pg_and_are_deduplicated() -> None:
    before_box = {"a": (1.0, 16.0), "b": (1.0, 16.0)}
    after_box = {"a": (1.0, 4.0), "b": (4.0, 16.0)}
    expected = pg_regions.complement_boxes(before_box, after_box)
    actual = mysql_interval.retained_complement_regions(
        _ranges(before_box),
        _ranges(after_box),
        ["a", "b"],
        resource_potential=0.5,
        created_outer_attempt=7,
    )
    actual_boxes = [
        {
            name: (float(spec["lower"]), float(spec["upper"]))
            for name, spec in row["ranges"].items()
        }
        for row in actual
    ]
    assert actual_boxes == expected
    for position, left in enumerate(actual):
        for right in actual[position + 1:]:
            assert not _positive_overlap(left["ranges"], right["ranges"])

    queue, first_audit = mysql_interval.append_new_regions([], [], actual)
    queue, second_audit = mysql_interval.append_new_regions(queue, [], actual)
    assert len(queue) == len(actual)
    assert len(first_audit["added_region_ids"]) == len(actual)
    assert len(second_audit["duplicate_region_ids_skipped"]) == len(actual)

    selected_id = queue[0]["id"]
    remaining, visited_audit = mysql_interval.append_new_regions(
        queue[1:], [selected_id], [queue[0]],
    )
    assert len(remaining) == len(queue) - 1
    assert visited_audit["duplicate_region_ids_skipped"] == [selected_id]


def test_effective_runtime_binds_the_pg_aligned_successors() -> None:
    assert runtime.interval_control is mysql_interval
    assert runtime.rejection_cycle is mysql_rejection
    assert "algorithm_c_visited_region_ids" in runtime._source
    assert "append_new_regions" in runtime._source
    assert "maximum_outer_attempts_exhausted\": False" in runtime._source
