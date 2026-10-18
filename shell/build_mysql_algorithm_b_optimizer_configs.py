#!/usr/bin/env python3
"""Build validated MySQL Algorithm B optimizer configs from frozen evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
FREEZE_ID = "frozen_mysql_workloads_and_parameters_20260723_v3"
TABLE_ROOT = ROOT / "config/formal/frozen_mysql_operator_physics_experience_tables_20260724_v1"
R0_ROOT = ROOT / "config/formal/frozen_mysql_r0_calibration_20260724_v1"
REGISTRY_ROOT = ROOT / f"config/formal/{FREEZE_ID}/plan_workload_registries"
OUTPUT_ROOT = ROOT / "config/formal/mysql_sgd_production"

DEFAULT_COSTS = {
    "io_block_read_cost": 1.0,
    "memory_block_read_cost": 0.25,
    "row_evaluate_cost": 0.1,
    "key_compare_cost": 0.05,
    "disk_temptable_create_cost": 20.0,
    "disk_temptable_row_cost": 0.5,
    "memory_temptable_create_cost": 1.0,
    "memory_temptable_row_cost": 0.1,
}

JOB_INPUTS = {
    "scenario": "job_memory",
    "benchmark": "job",
    "target_resource": "memory",
    "table": TABLE_ROOT / "T.job_memory.operator_physics_v1.json",
    "r0": R0_ROOT / "job_memory.r0_calibration_v1.json",
    "feature_source": REGISTRY_ROOT / "job.trained_estimator_feature_source.json",
    "registry": REGISTRY_ROOT / "job.mysql_plan_workload_registry_v1.json",
    "mysql": {
        "mysql_binary": "mysql",
        "host": "127.0.0.1",
        "port": 65102,
        "user": "root",
        "database": "imdb_job",
        "engine_name": "default",
        "device_type": 0,
        "query_timeout_sec": 120,
    },
}


def _load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return payload


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_job_config(root: Path = ROOT) -> dict[str, Any]:
    inputs = {
        key: (root / value.relative_to(ROOT) if isinstance(value, Path) else value)
        for key, value in JOB_INPUTS.items()
    }
    table_path = inputs["table"]
    r0_path = inputs["r0"]
    feature_path = inputs["feature_source"]
    registry_path = inputs["registry"]
    table = _load_object(table_path)
    r0 = _load_object(r0_path)
    feature_source = _load_object(feature_path)
    registry = _load_object(registry_path)

    scenario = str(inputs["scenario"])
    benchmark = str(inputs["benchmark"])
    for label, payload in (("table", table), ("r0", r0)):
        if payload.get("scenario") != scenario or payload.get("benchmark") != benchmark:
            raise ValueError(f"{label} scenario/benchmark binding mismatch")
    if table.get("workload_freeze_id") != FREEZE_ID:
        raise ValueError("experience table freeze mismatch")
    if registry.get("freeze_id") != FREEZE_ID or registry.get("benchmark") != benchmark:
        raise ValueError("workload registry freeze/benchmark binding mismatch")
    if registry.get("immutable") is not True:
        raise ValueError("JOB workload registry must be immutable")
    registry_sha = _sha256(registry_path)
    if table.get("workload_registry_sha256") != registry_sha:
        raise ValueError("experience table workload registry SHA-256 mismatch")
    if feature_source.get("workload_registry_sha256") != registry_sha:
        raise ValueError("feature source workload registry SHA-256 mismatch")
    if feature_source.get("workload_freeze_id") != FREEZE_ID:
        raise ValueError("feature source freeze mismatch")
    if feature_source.get("benchmark_id") != benchmark:
        raise ValueError("feature source benchmark mismatch")
    if r0.get("passed") is not True or r0.get("correctness", {}).get("passed") is not True:
        raise ValueError("R0 evidence did not pass promotion and correctness gates")

    parameter_space = table.get("parameter_space")
    sensitivity = table.get("table")
    if not isinstance(parameter_space, Mapping) or not isinstance(sensitivity, Mapping):
        raise ValueError("experience table lacks parameter space or sensitivity table")
    active = [str(name) for name in parameter_space.get("active_params", [])]
    ranges = parameter_space.get("ranges")
    if not active or not isinstance(ranges, Mapping):
        raise ValueError("experience table parameter space is incomplete")
    if set(active) != set(sensitivity):
        raise ValueError("active parameter and sensitivity rows differ")

    resource_utilization = r0.get("resource_utilization")
    if not isinstance(resource_utilization, Mapping):
        raise ValueError("R0 evidence lacks resource utilization")
    baseline_real = {
        resource: float(resource_utilization[resource])
        for resource in ("cpu", "memory", "io")
    }
    optimizer_ranges = {
        name: {
            "min_value": float(ranges[name]["lower"]),
            "max_value": float(ranges[name]["upper"]),
        }
        for name in active
    }
    baseline_values = {name: DEFAULT_COSTS[name] for name in active}
    feature_source["workload_registry_path"] = str(registry_path.resolve())

    return {
        "schema_version": "mysql_algorithm_b_optimizer_config_v1",
        "formal_scope": "mysql_only",
        "freeze_id": FREEZE_ID,
        "scenario": scenario,
        "benchmark": benchmark,
        "exp_id": "mysql_algorithm_b_job_memory_v3",
        "target_resource": str(inputs["target_resource"]),
        "mysql": dict(inputs["mysql"]),
        "capability": {"cpu": 1.0, "memory": 1.0, "io": 1.0},
        "cost_parameters": {
            "active": active,
            "ranges": optimizer_ranges,
            "baseline_values": baseline_values,
        },
        "sensitivity_table": {
            name: {
                resource: float(sensitivity[name][resource])
                for resource in ("cpu", "memory", "io")
            }
            for name in active
        },
        "scoring": {
            "overload_threshold": 0.8,
            "score_mode": "band_target",
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
            "scenario": "job_memory",
            "lock_path": (
                "/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/"
                "host02_control/job_xp_pressure.lock"
            ),
            "journal_path": (
                "/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/"
                "host02_control/job_xp_lease_journal.jsonl"
            ),
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
            "immutable": True,
        },
        "evidence": {
            "experience_table": {"path": str(table_path.resolve()), "sha256": _sha256(table_path)},
            "r0_estimator_scale": {"path": str(r0_path.resolve()), "sha256": _sha256(r0_path)},
            "feature_source": {"path": str(feature_path.resolve()), "sha256": _sha256(feature_path)},
            "formal_incumbent_requires_fresh_measurement": True,
            "historical_r0_stable": bool(r0.get("stability", {}).get("stable", False)),
        },
        "output": {
            "directory": "history/mysql_sgd_algorithm_b/job_memory",
            "verbose": True,
        },
        "commit_best": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT_ROOT / "job_memory.algorithm_b.optimizer.json")
    args = parser.parse_args()
    config = build_job_config()
    from mysql_parameter_optimizer import validate_config

    validate_config(config)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
