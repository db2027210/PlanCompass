#!/usr/bin/env python3
"""Static isolation guard for the coordinate-grid double-loop entry point."""

from __future__ import annotations

import ast
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SHELL = ROOT / "shell"
for search_path in (ROOT, SHELL):
    if str(search_path) not in sys.path:
        sys.path.insert(0, str(search_path))

from binary_plan_resource_tuner import hybench_xp_config_for_label, infer_target_from_label
from double_loop_scenario import ScenarioMeasurement
from run_double_loop_grid_scenarios import (
    double_loop_validation_label,
    effective_outer_round_budget,
    isolated_fresh_runtime_config,
    measurement_invalid_reason,
    physical_target_sweet_upper,
    sigmoid_linear_resource_limits,
)


CORE_FILES = (
    ROOT / "double_loop_grid_optimizer.py",
    ROOT / "double_loop_scenario.py",
)
RUNNER = ROOT / "shell" / "run_double_loop_grid_scenarios.py"
FORBIDDEN_MODULES = {
    "gradient_descent_optimizer",
    "parameter_optimizer",
    "resource_utility",
}
FORBIDDEN_CALLS = {
    "compare_actual",
    "compare_against_baseline_goal",
    "load_active_params",
    "load_best_params",
    "load_history",
    "load_sensitivity",
    "reduce_candidates",
}
ALLOWED_TRANSPORT_IMPORTS = {
    "COST_PARAMS",
    "PlanFeatureProbe",
    "endpoint_probe_task",
    "load_config",
    "param_ranges",
    "pg_conn_from_worker",
    "psql_query",
    "run_real_validation_safe",
    "validation_workers",
    "wait_for_postgres",
    "workload_queries_with_sampling",
}


def imported_modules(tree: ast.AST) -> set[str]:
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module.split(".")[0])
    return result


def called_names(tree: ast.AST) -> set[str]:
    result: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            result.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            result.add(node.func.attr)
    return result


def main() -> None:
    valid_measurement = ScenarioMeasurement(100.0, 4.0, 2.0, 10.0)
    assert not measurement_invalid_reason(
        valid_measurement,
        {"resource_samples": {"cpu_cores": 90, "memory_bytes": 90, "io_bps": 90}},
    )
    placeholder = ScenarioMeasurement(100.0, 0.0, 0.0, 0.0)
    assert measurement_invalid_reason(
        placeholder,
        {"resource_samples": {"cpu_cores": 0, "memory_bytes": 0, "io_bps": 0}},
    )
    assert measurement_invalid_reason(
        valid_measurement,
        {"resource_samples": {"cpu_cores": 0, "memory_bytes": 90, "io_bps": 90}},
    )

    for scenario in ("cpu", "memory", "io", "none"):
        label = double_loop_validation_label(scenario, 12)
        assert label == f"{scenario}_double_loop_0012"
        assert infer_target_from_label(label) == scenario
        effective = hybench_xp_config_for_label(
            {
                "xtpclient": 32,
                "target_overrides": {scenario: {"xtpclient": 1}},
            },
            label,
        )
        assert effective["xtpclient"] == 1

    assert effective_outer_round_budget(
        {"a": (0.0, 1.0), "b": (0.0, 0.5)},
        ["a", "b"],
        0.125,
        0,
    ) == 6
    assert effective_outer_round_budget(
        {"a": (0.0, 1.0)}, ["a"], 0.125, 7
    ) == 7

    scenario_cfg = {
        "initial_params": {"seq_page_cost": 99.0},
        "_teacher_params": {"seq_page_cost": 2.0},
        "_teacher_source": "history/run.jsonl",
        "sensitivity_table": {"seq_page_cost": {"cpu": -1.0}},
        "candidate_gate": {"enabled": True},
        "optimizer": {"accepted": [1], "rejected": [2]},
        "capability": {"cpu": 10.0, "memory": 1.7, "io": 2400.0},
        "scoring": {
            "overload_threshold": 0.8,
            "resource_target_ranges": {
                "cpu": {"min_pct": 65.0, "max_pct": 80.0},
                "memory": {"min_pct": 70.0, "max_pct": 80.0},
                "io": {"min_pct": 50.0, "max_pct": 75.0},
            },
        },
        "metrics": {
            "plan_estimator": {
                "model_retraining": {"enabled": True, "history": "old"},
                "cpu": {"learned_model": {"path": "old-cpu.json"}},
                "memory": {"learned_model": {"path": "old-memory.json"}},
                "io": {"learned_model": {"path": "old-io.json"}},
            }
        },
    }
    runtime_cfg = {
        "binary_plan_tuning": {
            "binary_search": {"legacy_gate": True},
            "acceptance_policy": {"legacy_gate": True},
            "validation": {
                "hybench_xp": {"enabled": True},
                "vm_replay": {"enabled": True},
            },
        }
    }
    isolated, isolation_audit = isolated_fresh_runtime_config(
        scenario_cfg,
        runtime_cfg,
    )
    assert isolated["initial_params"] == {
        "seq_page_cost": 1.0,
        "random_page_cost": 4.0,
        "cpu_tuple_cost": 0.01,
        "cpu_index_tuple_cost": 0.005,
        "cpu_operator_cost": 0.0025,
        "parallel_setup_cost": 1000.0,
        "parallel_tuple_cost": 0.1,
    }
    assert all(
        key not in isolated
        for key in ("_teacher_params", "_teacher_source", "sensitivity_table", "candidate_gate", "optimizer")
    )
    assert "model_retraining" not in isolated["metrics"]["plan_estimator"]
    assert all(
        "learned_model" not in isolated["metrics"]["plan_estimator"][name]
        for name in ("cpu", "memory", "io")
    )
    assert set(isolated["binary_plan_tuning"]["validation"]) == {
        "hybench_xp",
        "vm_replay",
    }
    assert "binary_search" not in isolated["binary_plan_tuning"]
    assert "acceptance_policy" not in isolated["binary_plan_tuning"]
    assert isolation_audit["historical_resource_model_loaded"] is False

    worker = {
        "name": "dedicated-memory",
        "resource_capacity": {"cpu": 13.0, "memory": 8.9, "io": 2400.0},
    }
    limits = sigmoid_linear_resource_limits("memory", isolated, worker)
    assert abs(limits["cpu"] - 10.4) < 1e-12
    assert abs(limits["memory"] - 7.12) < 1e-12
    assert abs(limits["io"] - 1800.0) < 1e-12
    target_upper, physical = physical_target_sweet_upper(
        "memory",
        isolated,
        ScenarioMeasurement(100.0, 9.0, 7.9, 100.0),
        worker=worker,
    )
    assert abs(float(target_upper) - 7.12) < 1e-12
    assert abs(physical["target_sweet_lower"] - 7.11) < 1e-12
    assert abs(physical["configured_or_derived_target_sweet_lower"] - 6.23) < 1e-12
    assert abs(physical["configured_or_derived_target_sweet_upper"] - 7.12) < 1e-12
    assert physical["resource_capacity_provenance"]["memory"] == "worker.resource_capacity"
    clipped_upper, clipped = physical_target_sweet_upper(
        "memory",
        isolated,
        ScenarioMeasurement(100.0, 9.0, 7.4, 100.0),
        worker=worker,
    )
    assert abs(float(clipped_upper) - 7.03) < 1e-12
    assert abs(clipped["target_sweet_lower"] - 6.66) < 1e-12
    try:
        physical_target_sweet_upper(
            "memory",
            isolated,
            ScenarioMeasurement(100.0, 9.0, 8.0, 100.0),
            worker=worker,
        )
    except ValueError as exc:
        assert "no feasible intersection" in str(exc)
    else:
        raise AssertionError("an unreachable 5%-10% target band must fail before pruning")

    for path in CORE_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        assert imported_modules(tree).isdisjoint(FORBIDDEN_MODULES), path
        assert called_names(tree).isdisjoint(FORBIDDEN_CALLS), path

    runner_tree = ast.parse(RUNNER.read_text(encoding="utf-8"), filename=str(RUNNER))
    assert called_names(runner_tree).isdisjoint(FORBIDDEN_CALLS)
    runner_source = RUNNER.read_text(encoding="utf-8")
    assert "--model-training-seed-dir" in runner_source
    assert "def load_anchored_resource_training_seed" in runner_source
    assert "old_scores_loaded" in runner_source
    assert "old_ranks_loaded" in runner_source
    assert "DoubleLoopGridOptimizer" in runner_source
    assert "DoubleLoopSGDOptimizer" not in runner_source
    assert "combination_max_candidates" in runner_source
    transport_imports = {
        alias.name
        for node in ast.walk(runner_tree)
        if isinstance(node, ast.ImportFrom) and node.module == "binary_plan_resource_tuner"
        for alias in node.names
    }
    assert transport_imports == ALLOWED_TRANSPORT_IMPORTS

    optimizer_tree = ast.parse(CORE_FILES[0].read_text(encoding="utf-8"))
    imports = imported_modules(optimizer_tree)
    assert "random" not in imports
    attributes = {
        node.attr for node in ast.walk(optimizer_tree) if isinstance(node, ast.Attribute)
    }
    assert "combinations" in attributes
    assert "permutations" not in attributes

    scenario_source = CORE_FILES[1].read_text(encoding="utf-8")
    scenario_tree = ast.parse(scenario_source, filename=str(CORE_FILES[1]))
    experience_class = next(
        node
        for node in scenario_tree.body
        if isinstance(node, ast.ClassDef) and node.name == "FreshExperience"
    )
    experience_fields = {
        node.target.id
        for node in experience_class.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    assert experience_fields == {"params", "measurement", "objective"}
    assert any(
        isinstance(node, ast.Attribute) and node.attr == "measurement"
        for node in ast.walk(scenario_tree)
    )
    assert "current_run_resource_vector_selected_mode" in scenario_source
    assert '"training_target": "resource_vector"' in scenario_source
    assert "sigmoid_linear_sweet_band_weighted_mean_v2" in scenario_source
    assert "exploration_directions" not in scenario_source
    runner_source = RUNNER.read_text(encoding="utf-8")
    assert "fresh_endpoint_exploration_directions" not in runner_source
    assert "double_loop_active_parameter_override" not in runner_source
    assert "--force-active-param is forbidden" in runner_source
    assert 'if result.successful:' in runner_source
    assert (
        'if result.stop_reason == "epsilon_converged" and result.successful:'
        not in runner_source
    )
    assert 'default="sigmoid_linear_resource"' in runner_source
    print("double_loop isolation tests passed", flush=True)


if __name__ == "__main__":
    main()
