#!/usr/bin/env python3
"""Generate Algorithm B's estimated grid Top-5 inside C-derived intervals.

This module is deliberately offline: it does not touch MySQL, Docker, or a
benchmark.  The runtime controller must capture complete plan evidence for the
selected Top-5 and replay them through the frozen four-scene executor.
"""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
FRAMEWORK_ROOT = Path("/home/anonymous/program/resource_adaptive")
# Keep this project's common_outer_loop authoritative.  The shared framework
# directory is a lookup fallback only; putting it first silently imports the
# PostgreSQL project's module with the same name and corrupts later imports in
# a long-lived controller or test process.
if str(ROOT) in sys.path:
    sys.path.remove(str(ROOT))
sys.path.insert(0, str(ROOT))
if str(FRAMEWORK_ROOT) not in sys.path:
    sys.path.append(str(FRAMEWORK_ROOT))

from double_loop_grid_optimizer import DoubleLoopGridOptimizer, GridInnerLoopConfig
from double_loop_search_common import OuterLoopConfig, ParameterRange
from double_loop_scenario import FreshBaselineObjective, ScenarioMeasurement


SCENES = (
    "job_memory", "tpch_sf4_memory", "hybench_xp_cpu", "hybench_xp_io",
)
TARGET = {
    "job_memory": "memory",
    "tpch_sf4_memory": "memory",
    "hybench_xp_cpu": "cpu",
    "hybench_xp_io": "io",
}
TABLES = {
    "job_memory": "T.job_memory.operator_physics_v1.json",
    "tpch_sf4_memory": "T.tpch_memory.operator_physics_v1.json",
    "hybench_xp_cpu": "T.hybench_xp_io.operator_physics_v1.json",
    "hybench_xp_io": "T.hybench_xp_io.operator_physics_v1.json",
}
TABLE_ROOT = (
    ROOT / "config/formal/frozen_mysql_operator_physics_experience_tables_20260724_v1"
)
GRID_POINTS = 15
INNER_PASSES = 20
CONVERGENCE_PATIENCE = 2
COMBINATION_MIN_SIZE = 2
COMBINATION_MAX_SIZE = 4
COMBINATION_VALUES_PER_PARAMETER = 3
REAL_TOP_K = 5


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} object required")
    return dict(value)


def _load(path: Path) -> dict[str, Any]:
    return _mapping(json.loads(path.read_text(encoding="utf-8")), str(path))


def _same_params(one: Mapping[str, float], two: Mapping[str, float]) -> bool:
    return set(one) == set(two) and all(
        math.isclose(float(one[name]), float(two[name]), rel_tol=0.0, abs_tol=1e-12)
        for name in one
    )


def generate_scene_top5(
    scene: str,
    scene_release: Mapping[str, Any],
    *,
    round_index: int,
    table_root: Path = TABLE_ROOT,
) -> dict[str, Any]:
    """Return deterministic estimated-grid Top-5 without physical mutation."""
    if scene not in SCENES or round_index < 1:
        raise RuntimeError("known scene and positive round index required")
    release = _mapping(scene_release, f"{scene} release")
    active = [str(name) for name in release.get("active_parameters") or []]
    incumbent = {
        str(name): float(value)
        for name, value in _mapping(
            release.get("algorithm_c_final_incumbent_params"),
            f"{scene} incumbent",
        ).items()
    }
    interval_rows = _mapping(
        release.get("algorithm_b_initial_binary_intervals"),
        f"{scene} intervals",
    )
    if not active or len(active) != len(set(active)) or set(interval_rows) != set(active):
        raise RuntimeError(f"{scene} active interval keys differ")
    ranges: dict[str, ParameterRange] = {}
    for priority, name in enumerate(active, 1):
        interval = _mapping(interval_rows[name], f"{scene} {name} interval")
        lower = float(interval["lower"])
        upper = float(interval["upper"])
        if (
            not math.isfinite(lower)
            or not math.isfinite(upper)
            or lower <= 0.0
            or upper < lower
            or upper / lower > 1.01
            or name not in incumbent
            or not lower <= incumbent[name] <= upper
            or interval.get("transform") != "log"
        ):
            raise RuntimeError(f"{scene} {name} C-derived interval differs")
        ranges[name] = ParameterRange(
            lower,
            upper,
            discrete_count=GRID_POINTS,
            priority=priority,
            transform="log",
        )

    real = _mapping(
        release.get("algorithm_c_final_real_measurement"),
        f"{scene} final C measurement",
    )
    baseline_resources = {
        name: float(value)
        for name, value in _mapping(
            real.get("resource_means"), f"{scene} resource means",
        ).items()
    }
    if set(baseline_resources) != {"cpu", "memory", "io"} or not all(
        math.isfinite(value) and value >= 0.0
        for value in baseline_resources.values()
    ):
        raise RuntimeError(f"{scene} baseline resources differ")
    target = TARGET[scene]
    baseline_db_qps = float(real["db_qps"])
    objective = FreshBaselineObjective(
        target,
        ScenarioMeasurement(
            float(real["db_qps"]),
            baseline_resources["cpu"],
            baseline_resources["memory"],
            baseline_resources["io"],
        ),
        resource_limits={"cpu": 1.0, "memory": 1.0, "io": 1.0},
        target_sweet_upper=max(1e-9, baseline_resources[target] * 0.95),
    )
    table_document = _load(table_root / TABLES[scene])
    table_active = [
        str(name)
        for name in _mapping(
            table_document.get("parameter_space"), "experience parameter space",
        ).get("active_params") or []
    ]
    table = _mapping(table_document.get("table"), "experience table")
    if active != table_active or set(table) != set(active):
        raise RuntimeError(f"{scene} experience table parameter identity differs")

    qps_read = False

    def estimate(_sql_set: Any, params: dict[str, float]) -> dict[str, Any]:
        predicted = dict(baseline_resources)
        for name in active:
            value = float(params[name])
            anchor = float(incumbent[name])
            if value <= 0.0 or anchor <= 0.0:
                raise RuntimeError("positive log-space optimizer costs required")
            delta = math.log(value / anchor)
            effects = _mapping(table[name], f"{scene} {name} effects")
            for resource in ("cpu", "memory", "io"):
                predicted[resource] += float(effects[resource]) * delta
        predicted = {
            name: max(1e-9, float(value)) for name, value in predicted.items()
        }
        return {
            # The shared scorer's typed payload requires db_qps to be present,
            # but its resource-only formula is invariant to this fixed value.
            "resource": {**predicted, "db_qps": baseline_db_qps},
            "model": {
                "kind": "algorithm_c_experience_gradient_soft_prior_v1",
                "qps_read": False,
                "hard_pruning_authorized": False,
                "full_plan_evidence_deferred_to_selected_top5": True,
            },
        }

    def no_real_replay(_sql_set: Any, _params: dict[str, float]) -> dict[str, Any]:
        raise RuntimeError("offline Algorithm B grid generation cannot replay")

    optimizer = DoubleLoopGridOptimizer(
        estimate,
        no_real_replay,
        {"cpu": 1.0, "memory": 1.0, "io": 1.0},
        grid_config=GridInnerLoopConfig(
            max_workers=8,
            max_passes=INNER_PASSES,
            no_improvement_patience=CONVERGENCE_PATIENCE,
            combination_probe_enabled=True,
            combination_min_size=COMBINATION_MIN_SIZE,
            combination_max_size=COMBINATION_MAX_SIZE,
            combination_values_per_param=COMBINATION_VALUES_PER_PARAMETER,
            combination_max_candidates=0,
        ),
        outer_config=OuterLoopConfig(
            max_rounds=10,
            real_validation_top_k=REAL_TOP_K,
            real_validation_top_fraction=1.0,
            topk_controller="fixed",
            adaptive_topk_min_k=REAL_TOP_K,
            adaptive_topk_batch_size=1,
            no_improvement_patience=0,
            epsilon=1e-4,
            target_resource=target,
        ),
        estimated_score_func=objective.score,
        real_score_func=objective.score,
        verbose=False,
    )
    points, grid_audit = optimizer._grid_small_loop(
        {"scene": scene},
        incumbent,
        active,
        ranges,
        round_index=round_index,
        iteration_writer=None,
    )
    ranked = [
        point for point in optimizer._deduplicate_and_rank(points)
        if not _same_params(point.params, incumbent)
    ]
    if len(ranked) < REAL_TOP_K:
        raise RuntimeError(f"{scene} estimated grid has fewer than fixed Top-5")
    selected: list[dict[str, Any]] = []
    for rank, point in enumerate(ranked[:REAL_TOP_K], 1):
        normalized_params = {
            name: (
                min(
                    ranges[name].max_value,
                    max(ranges[name].min_value, float(point.params[name])),
                )
                if name in ranges else float(point.params[name])
            )
            for name in sorted(point.params)
        }
        selected.append({
            "rank": rank,
            "subset_id": point.subset_id,
            "active_subset": list(point.active_subset),
            "optimized_params": normalized_params,
            "estimated_resources": {
                name: float(point.estimated_resource[name])
                for name in ("cpu", "memory", "io")
            },
            "estimated_score": float(point.estimated_score),
            "qps_used_for_resource_prediction_or_score": False,
            "hard_pruned": False,
            "full_plan_evidence_required_before_real_replay": True,
        })
    return {
        "schema_version": "mysql_four_scene_algorithm_b_estimated_grid_ranking_v1",
        "status": "PASS",
        "scene": scene,
        "round_index": round_index,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "method": "outer_binary_inner_estimated_coordinate_grid",
        "seed_interval_source": "algorithm_c_final_stable_intervals",
        "seed_interval_expansion": False,
        "active_parameters": active,
        "grid_contract": {
            "grid_points": GRID_POINTS,
            "inner_passes": INNER_PASSES,
            "convergence_patience": CONVERGENCE_PATIENCE,
            "combination_sizes": [COMBINATION_MIN_SIZE, COMBINATION_MAX_SIZE],
            "combination_values_per_parameter": COMBINATION_VALUES_PER_PARAMETER,
            "combination_candidate_cap": 0,
            "fixed_real_top_k": REAL_TOP_K,
        },
        "grid_audit": copy.deepcopy(grid_audit),
        "candidate_count_before_top5": len(ranked),
        "selected_top5": selected,
        "qps_read_by_estimator_or_score": qps_read,
        "candidate_hard_pruning": False,
        "workload_or_docker_mutation": False,
    }


__all__ = ["generate_scene_top5"]
