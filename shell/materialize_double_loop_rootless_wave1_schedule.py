#!/usr/bin/env python3
"""Module."""


from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict


PYTHON = "/home/anonymous/miniconda3/envs/resource_adaptive/bin/python"
LEASE_SUBDIR = "endpoint_locks"

COMMON_ARGS = [
    "--mode", "live",
    "--active-k", "7",
    "--pruning-workers", "7",
    "--inner-workers", "32",
    "--max-parallel-scenarios", "1",
    "--max-iter", "30",
    "--learning-rate", "0.15",
    "--learning-rate-decay", "0.85",
    "--finite-difference-ratio", "0.03",
    "--convergence-patience", "2",
    "--outer-rounds", "0",
    "--no-improvement-patience", "2",
    "--real-top-k", "30",
    "--adaptive-topk-min-k", "15",
    "--adaptive-topk-batch-size", "1",
    "--adaptive-topk-inversion-threshold", "0.10",
    "--inner-search-mode", "random",
    "--inner-search-seed", "20260720",
    "--model-quality-min-samples", "15",
    "--model-quality-calibration-replays", "15",
    "--model-quality-max-relative-error-p95", "1.0",
    "--model-quality-max-relative-error-max", "5.0",
    "--model-quality-min-r2", "0",
    "--model-quality-ranking-inversion-threshold", "0.10",
    "--model-max-neighbors", "2",
    "--grid-points", "15",
    "--epsilon", "0.0001",
    "--io-reward-weight", "0.25",
    "--selection-policy", "real_score_topk",
    "--score-policy", "sigmoid_linear_resource",
    "--topk-controller", "decision_tree",
    "--min-qps-gain-ratio", "0.15",
    "--min-target-drop-ratio", "0.05",
    "--max-target-drop-ratio", "0.10",
    "--postgres-ready-timeout", "600",
    "--forbid-source-root", "/home/anonymous/program/resource_adaptive",
    "--forbid-source-root", "/home/anonymous/program/resource_adaptive_double_loop_sgd_isolated",
]

LANES: Dict[str, Dict[str, Any]] = {
    "cpu": {
        "slot_id": "cpu_rootless",
        "exp_id": "A28",
        "benchmark": "hybench_tp",
        "priority": "high",
        "controller_root": "/home/anonymous/program/resource_adaptive_sgd_isolated_v14_cpu_matrix_20260718",
        "config_root": "/home/anonymous/program/resource_adaptive_sgd_isolated_v14_cpu_matrix_20260718/config/generated/double_loop_rootless_hybench_tp_cpu_20260718",
        "vm_name": "double-loop-sgd-rootless-cpu",
        "expected_vm_uuid": "51fe494f-4b0b-5b59-a63a-69e9b48928b9",
        "postgres_endpoint": "127.0.0.1:25701",
    },
    "memory": {
        "slot_id": "memory_rootless",
        "exp_id": "A25",
        "benchmark": "hybench_ap",
        "priority": "high",
        "controller_root": "/home/anonymous/program/resource_adaptive_sgd_isolated_v15_memory_matrix_20260718",
        "config_root": "/home/anonymous/program/resource_adaptive_sgd_isolated_v15_memory_matrix_20260718/config/generated/double_loop_rootless_hybench_ap_memory_20260718",
        "vm_name": "double-loop-sgd-rootless-memory",
        "expected_vm_uuid": "fc3d9cce-d093-53d8-b078-4b9a47ae9777",
        "postgres_endpoint": "127.0.0.1:25702",
    },
    "io": {
        "slot_id": "io_rootless",
        "exp_id": "A30",
        "benchmark": "hybench_xp",
        "priority": "high",
        "controller_root": "/home/anonymous/program/resource_adaptive_sgd_isolated_v16_io_matrix_20260718",
        "config_root": "/home/anonymous/program/resource_adaptive_sgd_isolated_v16_io_matrix_20260718/config/generated/double_loop_rootless_hybench_xp_io_20260718",
        "vm_name": "double-loop-sgd-rootless-io",
        "expected_vm_uuid": "eec913c6-64f0-50a3-a40a-7c58e6d52cbe",
        "postgres_endpoint": "127.0.0.1:25703",
    },
    "none": {
        "slot_id": "none_rootless",
        "exp_id": "E25",
        "benchmark": "hybench_ap",
        "priority": "low",
        "controller_root": "/home/anonymous/program/resource_adaptive_sgd_isolated_v17_none_matrix_20260718",
        "config_root": "/home/anonymous/program/resource_adaptive_sgd_isolated_v17_none_matrix_20260718/config/generated/double_loop_rootless_hybench_ap_none_20260718",
        "vm_name": "double-loop-sgd-rootless-none",
        "expected_vm_uuid": "4ff5e894-356e-5522-9a80-b803cbe4c352",
        "postgres_endpoint": "127.0.0.1:25704",
    },
}


def read_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--run-output-root", required=True, type=Path)
    args = parser.parse_args()

    output_path = args.output.resolve()
    run_output_root = args.run_output_root.resolve()
    lease_dir = run_output_root.parent / LEASE_SUBDIR
    workers: Dict[str, Dict[str, Any]] = {}
    queues: Dict[str, list] = {scenario: [] for scenario in LANES}

    for scenario, spec in LANES.items():
        slot_id = str(spec["slot_id"])
        config_root = Path(spec["config_root"]).resolve()
        identity_path = config_root / "isolated_runtime_identity.json"
        identity = read_json(identity_path)
        expected_identity = {
            "scenario": scenario,
            "benchmark": str(spec["benchmark"]),
            "vm_name": str(spec["vm_name"]),
            "expected_vm_uuid": str(spec["expected_vm_uuid"]),
            "postgres_endpoint": str(spec["postgres_endpoint"]),
        }
        observed_identity = {
            key: str(identity.get(key) or "") for key in expected_identity
        }
        if observed_identity != expected_identity:
            raise ValueError(
                f"isolated route identity mismatch: {identity_path}: "
                f"{observed_identity!r} != {expected_identity!r}"
            )

        scenario_config = config_root / scenario / "scenario_config.json"
        runner_config = config_root / "runner_config.json"
        for path in (scenario_config, runner_config):
            if not path.is_file():
                raise FileNotFoundError(path)

        workers[slot_id] = {
            "scenario": scenario,
            "vm_name": spec["vm_name"],
            "expected_vm_uuid": spec["expected_vm_uuid"],
            "postgres_endpoint": spec["postgres_endpoint"],
        }
        run_id = f"{spec['exp_id']}_r1"
        output_dir = run_output_root / scenario / run_id
        command = [
            PYTHON,
            "-u",
            "shell/run_double_loop_grid_scenarios.py",
            *COMMON_ARGS,
            "--endpoint-lease-dir", str(lease_dir),
            "--scenarios", scenario,
            "--scenario-config", f"{scenario}={scenario_config}",
            "--worker-config", str(runner_config),
            "--exp-id", str(spec["exp_id"]),
            "--benchmark", str(spec["benchmark"]),
            "--db-label", "pg17",
            "--repeat-id", "1",
            "--output-dir", str(output_dir),
        ]
        route = {
            "slot_id": slot_id,
            "controller_root": str(Path(spec["controller_root"]).resolve()),
            "config_root": str(config_root),
            "scenario_config": str(scenario_config),
            "runner_config": str(runner_config),
            "vm_name": spec["vm_name"],
            "expected_vm_uuid": spec["expected_vm_uuid"],
            "postgres_endpoint": spec["postgres_endpoint"],
            "command": command,
        }
        queues[scenario].append(
            {
                "run_id": run_id,
                "exp_id": spec["exp_id"],
                "repeat_id": 1,
                "benchmark": spec["benchmark"],
                "scenario": scenario,
                "priority": spec["priority"],
                "output_dir": str(output_dir),
                "eligible_worker_slots": [slot_id],
                "database_prerequisite": None,
                "routes": {slot_id: route},
            }
        )

    schedule = {
        "schema_version": 3,
        "wave": "rootless_hybench_wave1",
        "run_output_root": str(run_output_root),
        "total_runs": 4,
        "lane_order": ["cpu", "memory", "io", "none"],
        "lane_counts": {scenario: 1 for scenario in LANES},
        "cross_lane_policy": "four_dedicated_lanes_no_cross_profile_stealing",
        "within_worker_policy": "serial_outer_loop_feedback_dependency",
        "max_parallel_runs": 4,
        "workers": workers,
        "queues": queues,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(schedule, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(str(output_path), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
