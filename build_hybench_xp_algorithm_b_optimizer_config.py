#!/usr/bin/env python3
"""Build the fail-closed HyBench XP Algorithm B production config.

This builder records the real XP binding contract but never promotes it to
execution authorization.  The shared launcher must make the final readiness
decision.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parent
FREEZE_ID = "frozen_mysql_workloads_and_parameters_20260723_v3"
TABLE = ROOT / "config/formal/frozen_mysql_operator_physics_experience_tables_20260724_v1/T.hybench_xp_io.operator_physics_v1.json"
R0 = ROOT / "config/formal/frozen_mysql_r0_calibration_20260724_v1/hybench_xp_io.r0_calibration_v1.json"
REGISTRY = ROOT / f"config/formal/{FREEZE_ID}/plan_workload_registries/hybench_xp.production_binding_capture_contract_v1.json"
FACTORY = ROOT / "mysql_algorithm_b_production_services.py"
OUTPUT = ROOT / "config/formal/mysql_sgd_production/hybench_xp_io.algorithm_b.optimizer.json"
BINDING_OUTPUT = ROOT / "config/formal/mysql_sgd_production/hybench_xp_io.production_factory_binding.json"


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return payload


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_xp_config(root: Path = ROOT) -> dict[str, Any]:
    table_path = root / TABLE.relative_to(ROOT)
    r0_path = root / R0.relative_to(ROOT)
    registry_path = root / REGISTRY.relative_to(ROOT)
    factory_path = root / FACTORY.relative_to(ROOT)
    table = _load(table_path)
    r0 = _load(r0_path)
    registry = _load(registry_path)

    if table.get("scenario") != "hybench_xp_io" or table.get("benchmark") != "hybench_xp":
        raise ValueError("XP experience table binding mismatch")
    if table.get("workload_freeze_id") != FREEZE_ID:
        raise ValueError("XP experience table freeze mismatch")
    registry_sha = _sha(registry_path)
    if table.get("workload_registry_sha256") != registry_sha:
        raise ValueError("XP experience table registry SHA-256 mismatch")
    if registry.get("benchmark") != "hybench_xp" or registry.get("freeze_id") != FREEZE_ID:
        raise ValueError("XP registry freeze/benchmark binding mismatch")
    if r0.get("scenario") != "hybench_xp_io" or r0.get("benchmark") != "hybench_xp":
        raise ValueError("XP R0 binding mismatch")
    if r0.get("passed") is not True or (r0.get("correctness") or {}).get("passed") is not True:
        raise ValueError("XP R0 evidence did not pass its recorded correctness gate")
    if not factory_path.is_file() or factory_path.is_symlink():
        raise ValueError("production service factory is absent or unsafe")

    parameter_space = table.get("parameter_space")
    sensitivity = table.get("table")
    if not isinstance(parameter_space, Mapping) or not isinstance(sensitivity, Mapping):
        raise ValueError("XP experience table lacks parameter space or sensitivity table")
    active = [str(name) for name in parameter_space.get("active_params", [])]
    ranges = parameter_space.get("ranges")
    if len(active) != 7 or not isinstance(ranges, Mapping) or set(active) != set(sensitivity):
        raise ValueError("XP active parameter/range/sensitivity coverage is incomplete")

    resource_utilization = r0.get("resource_utilization")
    if not isinstance(resource_utilization, Mapping):
        raise ValueError("XP R0 lacks resource utilization")
    baseline_real = {name: float(resource_utilization[name]) for name in ("cpu", "memory", "io")}
    baseline_values = {
        "io_block_read_cost": 1.0,
        "memory_block_read_cost": 0.25,
        "row_evaluate_cost": 0.1,
        "key_compare_cost": 0.05,
        "disk_temptable_create_cost": 20.0,
        "disk_temptable_row_cost": 0.5,
        "memory_temptable_row_cost": 0.1,
    }
    optimizer_ranges = {
        name: {"min_value": float(ranges[name]["lower"]), "max_value": float(ranges[name]["upper"])}
        for name in active
    }

    # Deliberately omit registry_query_ids and query SQL: XP has no real
    # production binding capture yet, and inventing them would authorize a
    # workload the contract has not observed.
    feature_source = {
        "workload_registry_path": str(registry_path.resolve()),
        "workload_registry_sha256": registry_sha,
        "workload_freeze_id": FREEZE_ID,
        "benchmark_id": "hybench_xp",
        "require_strict_plan_registry": True,
        "binding_capture_status": "pending_exact_production_capture",
        "registry_execution_authorized": registry.get("execution_authorized") is True,
        "strict_cluster_usable": (registry.get("strict_plan_decision") or {}).get("strict_cluster_usable_now") is True,
    }

    return {
        "schema_version": "mysql_algorithm_b_optimizer_config_v1",
        "formal_scope": "mysql_only",
        "freeze_id": FREEZE_ID,
        "scenario": "hybench_xp_io",
        "benchmark": "hybench_xp",
        "exp_id": "mysql_algorithm_b_hybench_xp_io_v3",
        "target_resource": "io",
        "execution_authorized": False,
        "mysql": {
            "mysql_binary": "mysql",
            "host": "127.0.0.1",
            "port": 65103,
            "user": "root",
            "database": "hybench_sf10x",
            "engine_name": "default",
            "device_type": 0,
            "query_timeout_sec": 120,
        },
        "capability": {"cpu": 1.0, "memory": 1.0, "io": 1.0},
        "cost_parameters": {
            "active": active,
            "ranges": optimizer_ranges,
            "baseline_values": {name: baseline_values[name] for name in active},
        },
        "sensitivity_table": {
            name: {resource: float(sensitivity[name][resource]) for resource in ("cpu", "memory", "io")}
            for name in active
        },
        "scoring": {
            "overload_threshold": 0.8,
            "score_mode": "resource_only_band_target",
            "resource_weights": {"cpu": 1.0, "memory": 1.0, "io": 1.0},
        },
        "optimizer": {
            "candidate_generator": "all_nonempty_subsets_experience_table_analytic_sgd_v1",
            "subset_max_workers": 4,
            "eta": 0.0051,
            "max_iter": 1,
            "patience": 1,
            "gamma": 0.5,
            "max_param_delta_ratio": 0.2,
        },
        "formal_outer_protocol": {
            "target_accepted_shrinks": 10,
            "max_outer_attempts": 100,
            "formal_search_initial_replay_top_k": 5,
            "formal_search_max_replay_top_k": 5,
            "plan_basin_escape": {
                "orthogonal_radius": 0.08,
                "maximum_radius": 0.32,
                "radius_gamma": 0.5,
                "maximum_rejected_batches": 5,
                "minimum_eta": 0.00031875,
            },
        },
        "host02_job_xp_mutex": {
            "required": True,
            "scenario": "hybench_xp_io",
            "lock_path": "/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/host02_control/job_xp_pressure.lock",
            "journal_path": "/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/host02_control/job_xp_lease_journal.jsonl",
        },
        "estimator": {
            "type": "ratio_scale",
            "baseline_real": baseline_real,
            "uncalibrated_transition_log_step": 0.05,
            "feature_source": feature_source,
        },
        "workload_registry": {
            "path": str(registry_path.resolve()),
            "sha256": registry_sha,
            "immutable": False,
            "execution_authorized": False,
            "binding_capture_status": "pending_exact_production_capture",
        },
        "evidence": {
            "experience_table": {"path": str(table_path.resolve()), "sha256": _sha(table_path)},
            "r0_estimator_scale": {"path": str(r0_path.resolve()), "sha256": _sha(r0_path)},
            "binding_contract": {"path": str(registry_path.resolve()), "sha256": registry_sha},
            "production_binding_required": True,
            "raw_explain_required": True,
            "formal_incumbent_requires_fresh_measurement": True,
            "historical_r0_stable": bool((r0.get("stability") or {}).get("stable", False)),
        },
        "output": {"directory": "history/mysql_sgd_algorithm_b/hybench_xp_io", "verbose": True},
        "commit_best": False,
    }


def write_artifacts(root: Path = ROOT, output: Path = OUTPUT) -> tuple[Path, Path]:
    config = build_xp_config(root)
    output = root / output.relative_to(ROOT) if output.is_absolute() else root / output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    factory_path = root / FACTORY.relative_to(ROOT)
    binding = {
        "schema_version": "mysql_algorithm_b_hybench_xp_io_production_factory_binding_v1",
        "scenario": "hybench_xp_io",
        "benchmark": "hybench_xp",
        "optimizer_config": str(output.resolve()),
        "production_entry": "formal_mysql_sgd_campaign:run_production_campaign",
        "service_factory": "mysql_algorithm_b_production_services:build",
        "readiness_required": True,
        "launch_authorized": False,
        "launch_authorization_reason": "XP binding capture, raw EXPLAIN, and global readiness are incomplete",
        "algorithm_contract": {
            "global_fixed_replay_top_k": 5,
            "qps_role": "outer_acceptance_only",
            "non_target_resource_limit": "strictly_below_0.8",
            "maximum_rejected_batches": 5,
        },
        "factory_static_checks": {
            "py_compile": True,
            "returns_formal_point_dependencies": True,
            "fresh_r0_measurement": True,
            "campaign_cleanup_zero_pressure_gate": True,
            "host02_pressure_probe": True,
            "hybench_xp_session_factory": True,
        },
        "factory_source_sha256": _sha(factory_path),
    }
    binding_path = root / BINDING_OUTPUT.relative_to(ROOT)
    binding_path.write_text(json.dumps(binding, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output, binding_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    output, binding = write_artifacts(ROOT, args.output)
    print(json.dumps({"config": str(output), "factory_binding": str(binding), "launch_authorized": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
