#!/usr/bin/env python3
"""Prepare a fresh Algorithm A TPC-H lane with the policy-required 8 clients."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Mapping


SOURCE = Path(
    "/disk1/resource_adaptive/pg_algorithm_a_four_full_factorial_20260816/"
    "20260816_algorithm_a_full_factorial_globalrank_labels_clean_r5/"
    "tpch_memory/optimizer_config.json"
)
DESTINATION = Path(
    "/disk1/resource_adaptive/pg_algorithm_a_four_full_factorial_20260816/"
    "20260816_algorithm_a_tpch_parallel8_clean_r6"
)
TPC_H_CLIENTS = 8
CONTROLLER_CLIENT_CPUSET = "184-199"


def read_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def mapping(value: Any, name: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError(f"missing mapping: {name}")
    return value


def apply_parallel8_contract(cfg: Dict[str, Any]) -> None:
    algorithm_c = mapping(cfg.get("algorithm_c"), "algorithm_c")
    workload_contract = mapping(
        algorithm_c.get("workload_contract"),
        "algorithm_c.workload_contract",
    )
    if str(workload_contract.get("benchmark") or "").lower() != "tpch":
        raise RuntimeError("source config is not the TPC-H lane")

    concurrency = mapping(
        workload_contract.get("benchmark_concurrency"),
        "algorithm_c.workload_contract.benchmark_concurrency",
    )
    concurrency.update(
        {
            "client_count": TPC_H_CLIENTS,
            "database_instances_per_replay": 1,
            "query_execution": "native_Q1_Q22_exactly_once",
            "task_dispatch": "deterministic_historical_LPT_lanes",
            "target_wall_time_s": 900,
            "target_wall_time_is_advisory": False,
            "parallelism_reason": (
                "policy_20260806_requires_one_fixed_lpt_lane_per_database_cpu"
            ),
        }
    )
    workload_contract["baseline_cohort"] = {
        "contract": "algorithm_c_four_lane_no_mutex_baseline_cohort_v1",
        "lane_count": 1,
        "lanes": ["tpch_memory"],
        "synchronized_repetitions": 2,
        "single_lane_repair_campaign": True,
    }

    algorithm_c["controller_client_cpuset"] = CONTROLLER_CLIENT_CPUSET
    algorithm_c["tpch_benchmark_parallelism"] = TPC_H_CLIENTS
    algorithm_c["tpch_parallelism_contract"] = (
        "native_q1_q22_exactly_once_fixed_lpt_8_lanes_v1"
    )

    algorithm_a = mapping(cfg.get("algorithm_a"), "algorithm_a")
    algorithm_a["tpch_benchmark_parallelism"] = TPC_H_CLIENTS
    algorithm_a["tpch_query_execution_contract"] = (
        "native_q1_q22_exactly_once_fixed_lpt_8_lanes_v1"
    )

    tuning = mapping(cfg.get("binary_plan_tuning"), "binary_plan_tuning")
    experiment_meta = mapping(tuning.get("experiment_meta"), "experiment_meta")
    experiment_meta["controller_client_cpuset"] = CONTROLLER_CLIENT_CPUSET
    experiment_meta["tpch_replay_clients"] = TPC_H_CLIENTS
    validation = mapping(tuning.get("validation"), "validation")
    runtime = mapping(validation.get("hybench_xp"), "validation.hybench_xp")
    # The actual BenchBase lane count is frozen by BENCHBASE_TPCH_WORKERS in
    # the launcher.  Keep the generated runtime metadata consistent as well.
    runtime["tpclient"] = TPC_H_CLIENTS
    workload_context = mapping(tuning.get("workload_context"), "workload_context")
    workload_context["concurrency"] = TPC_H_CLIENTS


def validate_parallel8_config(cfg: Mapping[str, Any]) -> None:
    algorithm_c = mapping(cfg.get("algorithm_c"), "algorithm_c")
    workload_contract = mapping(
        algorithm_c.get("workload_contract"),
        "algorithm_c.workload_contract",
    )
    concurrency = mapping(
        workload_contract.get("benchmark_concurrency"),
        "benchmark_concurrency",
    )
    baseline_cohort = mapping(
        workload_contract.get("baseline_cohort"),
        "baseline_cohort",
    )
    algorithm_a = mapping(cfg.get("algorithm_a"), "algorithm_a")
    tuning = mapping(cfg.get("binary_plan_tuning"), "binary_plan_tuning")
    validation = mapping(tuning.get("validation"), "validation")
    runtime = mapping(validation.get("hybench_xp"), "hybench_xp")
    workload_context = mapping(tuning.get("workload_context"), "workload_context")
    plan_gate = mapping(
        mapping(tuning.get("workload"), "workload").get("plan_coverage_gate"),
        "plan_coverage_gate",
    )

    expected = {
        "benchmark": workload_contract.get("benchmark"),
        "client_count": concurrency.get("client_count"),
        "database_instances_per_replay": concurrency.get(
            "database_instances_per_replay"
        ),
        "query_execution": concurrency.get("query_execution"),
        "target_wall_time_s": concurrency.get("target_wall_time_s"),
        "baseline_lane_count": baseline_cohort.get("lane_count"),
        "accepted_target": algorithm_a.get("accepted_target"),
        "candidates_per_round": algorithm_a.get("candidates_per_round"),
        "real_replay_parallelism": algorithm_a.get("real_replay_parallelism"),
        "runtime_tpclient": runtime.get("tpclient"),
        "runtime_timeout_s": runtime.get("timeout_s"),
        "workload_context_concurrency": workload_context.get("concurrency"),
        "expected_query_count": plan_gate.get("expected_query_count"),
        "controller_client_cpuset": algorithm_c.get("controller_client_cpuset"),
    }
    required = {
        "benchmark": "tpch",
        "client_count": 8,
        "database_instances_per_replay": 1,
        "query_execution": "native_Q1_Q22_exactly_once",
        "target_wall_time_s": 900,
        "baseline_lane_count": 1,
        "accepted_target": 7,
        "candidates_per_round": 128,
        "real_replay_parallelism": 2,
        "runtime_tpclient": 8,
        "runtime_timeout_s": 1020,
        "workload_context_concurrency": 8,
        "expected_query_count": 22,
        "controller_client_cpuset": CONTROLLER_CLIENT_CPUSET,
    }
    if expected != required:
        raise RuntimeError(
            "TPC-H parallel-8 contract mismatch: "
            + json.dumps({"actual": expected, "required": required}, sort_keys=True)
        )


def main() -> int:
    if DESTINATION.exists():
        raise RuntimeError(f"fresh destination already exists: {DESTINATION}")
    cfg = copy.deepcopy(read_json(SOURCE))
    apply_parallel8_contract(cfg)
    validate_parallel8_config(cfg)

    lane_root = DESTINATION / "tpch_memory"
    config_path = lane_root / "optimizer_config.json"
    write_json(config_path, cfg)
    (DESTINATION / "benchbase_runtime").mkdir(parents=True)
    manifest = {
        "contract": "pg_algorithm_a_tpch_parallel8_repair_campaign_v1",
        "algorithm": "A",
        "scene": "tpch_memory",
        "reason": "r5_launcher_regressed_policy_required_tpch_clients_from_8_to_1",
        "source_config": str(SOURCE),
        "source_config_sha256": sha256(SOURCE),
        "config": str(config_path),
        "config_sha256": sha256(config_path),
        "historical_r5_evidence_read_only": True,
        "historical_r5_state_inherited": False,
        "historical_r5_valid_replays_inherited": False,
        "fresh_baseline_required": True,
        "accepted_start": 0,
        "accepted_target": 7,
        "candidates_per_round": 128,
        "real_replay_parallelism": 2,
        "tpch_benchmark_parallelism_per_candidate": TPC_H_CLIENTS,
        "tpch_query_contract": "native_Q1_Q22_exactly_once",
        "tpch_dispatch": "deterministic_historical_LPT_lanes",
        "controller_client_cpuset": CONTROLLER_CLIENT_CPUSET,
        "target_wall_time_s": 900,
        "process_timeout_s": 1020,
    }
    manifest_path = DESTINATION / "fresh_campaign_manifest.json"
    write_json(manifest_path, manifest)
    print(str(manifest_path), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
