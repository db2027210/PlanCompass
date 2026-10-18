#!/usr/bin/env python3
"""Finalize the generated JOB outer config against the frozen PG5 contracts."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


LANE_ROOT = Path(
    "/mnt/nvme2n1/anonymous/resource_adaptive/"
    "pg5_common_outer_bgallowed_20260722_v1/job_node02"
)
WORKSPACE = LANE_ROOT / "workspace_outer"
CONFIG = (
    LANE_ROOT
    / "history/job/outer/attempt_1/generated_config/"
    "formal_job_outer_attempt1/memory/job.json"
)
T_PATH = (
    WORKSPACE
    / "config/formal/pg5_common_outer_bgallowed_20260722_v1/"
    "experience_table_T_job_memory_v1.json"
)
FROZEN = LANE_ROOT / "bundle/attempt8_frozen"
R0_RESULT = LANE_ROOT / "history/job/baseline/R0/attempt_8/point_result.json"
R0_DECISION = (
    LANE_ROOT / "history/job/baseline/R0/attempt_8/baseline_gate_decision.json"
)
COST_PARAMS = [
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def main() -> int:
    if not CONFIG.is_file():
        raise RuntimeError(f"generated config is missing: {CONFIG}")
    before_sha256 = sha256(CONFIG)
    cfg = read_json(CONFIG)
    table_artifact = read_json(T_PATH)
    r0_decision = read_json(R0_DECISION)
    if r0_decision.get("outer_allowed") is not True:
        raise RuntimeError("R0 attempt_8 does not authorize outer")

    cfg["_campaign"] = {
        "campaign_id": "pg5_common_outer_bgallowed_20260722_v1",
        "lane_id": "job_node02",
        "method_id": "experience_table_all_subset_sgd",
        "outer_attempt": 1,
        "background_load_allowed": True,
    }
    cfg["scoring"] = read_json(FROZEN / "common_scoring_contract.json")
    cfg["scoring_contract"] = {
        "path": str(FROZEN / "common_scoring_contract.json"),
        "sha256": "47db6e60190b79b44419b76f6823d08b59f9e0058917977f468ed8926475f588",
    }
    cfg["r0_resource_gate_contract"] = {
        "path": str(FROZEN / "r0_resource_gate_contract.json"),
        "sha256": "c6c034087393c12da5fa055a0cff8666db0dd70edc2bd39eb83c4146d0d9481e",
    }
    cfg["io_utilization_contract"] = {
        "path": str(FROZEN / "io_utilization_contract.json"),
        "sha256": "35eb6f304627ef915f4cfb463b64e472a760325834df7978ec67cac85c342064",
    }
    cfg["stability_gate_contract"] = {
        "path": str(FROZEN / "stability_gate_contract.json"),
        "sha256": "ef368b7e40ebf13e3ee75fef4144bb8d8ce8893ac9510c42823e16f854b488e2",
    }
    cfg["baseline_r0_evidence"] = {
        "attempt": 8,
        "point_result_path": str(R0_RESULT),
        "point_result_sha256": sha256(R0_RESULT),
        "baseline_gate_decision_path": str(R0_DECISION),
        "baseline_gate_decision_sha256": sha256(R0_DECISION),
        "outer_allowed": True,
        "accepted_shrink_count_before_method": 0,
    }

    cfg["initial_params"] = {
        "seq_page_cost": 1.0,
        "random_page_cost": 4.0,
        "cpu_tuple_cost": 0.01,
        "cpu_index_tuple_cost": 0.005,
        "cpu_operator_cost": 0.0025,
        "parallel_setup_cost": 1000.0,
        "parallel_tuple_cost": 0.1,
    }
    cfg["param_ranges"] = {
        "seq_page_cost": {"min_value": 0.1, "max_value": 10.0, "transform": "log"},
        "random_page_cost": {"min_value": 0.1, "max_value": 20.0, "transform": "log"},
        "cpu_tuple_cost": {"min_value": 0.001, "max_value": 0.1, "transform": "log"},
        "cpu_index_tuple_cost": {"min_value": 0.0005, "max_value": 0.05, "transform": "log"},
        "cpu_operator_cost": {"min_value": 0.00025, "max_value": 0.025, "transform": "log"},
        "parallel_setup_cost": {"min_value": 0.0, "max_value": 10000.0, "transform": "linear"},
        "parallel_tuple_cost": {"min_value": 0.001, "max_value": 1.0, "transform": "log"},
    }
    cfg["sensitivity_table"] = table_artifact["table"]
    cfg["experience_table_contract"] = {
        "required": True,
        "disable_fallback": True,
        "fallback_enabled": False,
        "artifact_path": str(T_PATH),
        "artifact_sha256": "f5bfe293923e3c1122ca7363f3dc9b1b37d5f9c430540976535222188d8d7261",
        "table_id": "pg5_job_memory_experience_table_T_v1",
        "version": "2026-07-22.1",
        "table_sha256": "a2fca76dd0ad04758ceebebb29a675ec43f1e52c5e820efca4b25a5fbaeaf07b",
        "active_params": COST_PARAMS,
        "finite_difference_direction_enabled": False,
        "random_direction_enabled": False,
    }
    gradient = cfg["optimizer"]["gradient"]
    gradient["active_params"] = COST_PARAMS
    gradient["subset_max_workers"] = 8
    gradient["require_explicit_sensitivity_table"] = True
    gradient["finite_difference_direction_enabled"] = False
    gradient["random_direction_enabled"] = False
    gradient["eta"] = 0.2
    gradient["max_iter"] = 20
    gradient["patience"] = 3
    gradient["gamma"] = 0.5

    wrapper = LANE_ROOT / "launch_outer/formal_job_outer_replay.py"
    workload_cmd = f"python3 -u {wrapper}"
    cfg["benchmark"].update(
        {
            "workload_cmd": workload_cmd,
            "workload_timeout_sec": 1800,
            "warmup_enabled": False,
            "validation_warmup_enabled": False,
            "formal_runner_includes_frozen_warmup_seconds": 60,
            "formal_runner_launch_maximum_measurement_seconds": 600,
            "formal_stability_early_stop_minimum_seconds": 180,
            "formal_initial_probe_seconds": 300,
            "formal_initial_probe_role": "observation_only",
        }
    )
    cfg["formal_pg_resource_semantics"] = {
        "enabled": True,
        "semantics": "pg5_io_total_throughput_utilization_v1",
        "qps_source": "formal_fixed_horizon_workload_qps",
        "throughput_stability_role": "outer_acceptance_only",
    }
    cfg["pre_benchmark_recovery"] = {
        "enabled": False,
        "required": False,
        "target_db": "imdb_job",
        "sql": [],
        "snapshot_restore": {"enabled": False, "required": False},
        "restart_db": {"enabled": False},
        "vm_drop_cache": {"enabled": False, "required": False},
        "formal_freshness_owner": str(wrapper),
    }
    cfg["post_benchmark_cleanup"].update(
        {
            "db_container": "ra_pg5_job_node02_v1_outercontrol",
            "restart_db": False,
            "restart_required": False,
        }
    )
    cfg["final_evaluation"]["runtime_source"] = "docker-runtime"
    cfg["final_evaluation"]["actual_score_required"] = True
    cfg["final_evaluation"]["run_validation"] = True
    cfg["final_evaluation"]["feedback_loop"]["enabled"] = True
    cfg["final_evaluation"]["feedback_loop"]["min_db_qps_ratio"] = 1.0
    cfg["final_evaluation"]["feedback_loop"]["min_db_tps_ratio"] = 0.0

    plan = cfg["metrics"]["plan_estimator"]
    plan["model_retraining"]["feature_cache_dir"] = str(
        LANE_ROOT / "work/features_cache_M1"
    )
    plan["model_retraining"]["strict_retrain_gate"] = True
    plan["model_retraining"]["fallback_on_error"] = False
    sanity = plan["model_retraining"]["quality_gate"]["baseline_sanity"]
    sanity["hard_fail"] = True
    sanity["envelope_relaxation_policy"] = (
        "only measured minimum widening after an audited envelope-only failure"
    )
    cfg["metrics"]["docker_runtime"].update(
        {
            "container": "ra_pg5_job_node02_v1_outercontrol",
            "interval_sec": 1.0,
            "warmup_sec": 0.0,
            "memory_mode": "working_set",
            "io_scope": "cgroup",
        }
    )
    cfg["formal_outer_protocol"] = {
        "method_id": "experience_table_all_subset_sgd",
        "all_nonempty_subset_count": 127,
        "subset_parallel_workers": 8,
        "initial_real_replay_count": 5,
        "maximum_cumulative_real_replay_count": 10,
        "maximum_non_default_real_candidates": 10,
        "accepted_shrink_target": 10,
        "range_shrink": "all-active-dimension binary interval after common outer accept",
        "fresh_template_before_every_real_point": True,
        "candidate_failure_consumes_budget": True,
        "duplicate_consumes_budget": True,
    }
    cfg["logging"]["history_path"] = str(
        LANE_ROOT / "history/job/outer/attempt_1/optimizer_history.jsonl"
    )
    cfg["resource_iteration_reports"]["output_dir"] = str(
        LANE_ROOT / "history/job/outer/attempt_1/resource_reports"
    )
    cfg["plan_capture"]["output_dir"] = str(
        LANE_ROOT / "history/job/outer/attempt_1/plan_captures"
    )
    cfg["pre_optimizer_process_cleanup"] = {"enabled": False}

    write_json(CONFIG, cfg)
    audit = {
        "schema_version": 1,
        "config_path": str(CONFIG),
        "generated_config_sha256_before_finalize": before_sha256,
        "formal_config_sha256": sha256(CONFIG),
        "experience_table_artifact_path": str(T_PATH),
        "experience_table_artifact_sha256": sha256(T_PATH),
        "experience_table_table_sha256": table_artifact["table_sha256"],
        "runtime_sources": {
            name: sha256(WORKSPACE / name)
            for name in (
                "parameter_optimizer.py",
                "experience_table_subset_optimizer.py",
                "gradient_descent_optimizer.py",
            )
        },
        "formal_wrapper_path": str(wrapper),
        "formal_wrapper_sha256": sha256(wrapper),
        "r0_outer_allowed": True,
        "accepted_shrink_count": 0,
        "candidate_count": 0,
    }
    write_json(LANE_ROOT / "history/job/outer/attempt_1/config_binding_audit.json", audit)
    print(json.dumps(audit, ensure_ascii=False, sort_keys=True, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
