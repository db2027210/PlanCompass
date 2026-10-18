#!/usr/bin/env python3
"""Create the fresh four-lane Algorithm A full-factorial campaign."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Dict

from shell import run_pg_algorithm_c_scene as framework


SOURCE = Path(
    "/disk1/resource_adaptive/pg_algorithm_b_four_grid_20260814/"
    "20260815_algorithm_b_grid15_io_cost_champion_clean_r2"
)
DESTINATION = Path(
    "/disk1/resource_adaptive/pg_algorithm_a_four_full_factorial_20260816/"
    "20260816_algorithm_a_full_factorial_clean_r1"
)
SCENES = ("job_memory", "tpch_memory", "xp_cpu", "xp_io")
METHOD = "exhaustive_binary_factorial_algorithm_a_v2"
SEARCH_CONTRACT = "lane_local_complete_lr_cartesian_physical_attempt_v2"
REPLAY_LABEL_CONTRACT = "algorithm_a_global_candidate_rank_wave_attempt_v1"
FORMAL_INITIAL_PARAMS = {
    "seq_page_cost": 1.0,
    "random_page_cost": 4.0,
    "cpu_tuple_cost": 0.01,
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0025,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.1,
}


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


def main() -> int:
    if DESTINATION.exists():
        raise RuntimeError(
            f"fresh Algorithm A destination already exists: {DESTINATION}"
        )
    if not SOURCE.is_dir():
        raise FileNotFoundError(SOURCE)
    DESTINATION.mkdir(parents=True)
    active_parameters = list(framework.ACTIVE_PARAMS)
    candidates_per_round = 1 << len(active_parameters)
    formal_ranges = {
        name: {
            "min_value": float(framework.DEFAULT_RANGES[name][0]),
            "max_value": float(framework.DEFAULT_RANGES[name][1]),
        }
        for name in active_parameters
    }
    manifest: Dict[str, Any] = {
        "contract": "pg_algorithm_a_full_factorial_fresh_campaign_v2",
        "algorithm": "A",
        "method": METHOD,
        "candidate_search_contract": SEARCH_CONTRACT,
        "physical_replay_label_contract": REPLAY_LABEL_CONTRACT,
        "physical_replay_candidate_slot_source": "global_candidate_rank",
        "physical_replay_worker_assignment_source": "global_candidate_rank_mod_worker_count",
        "physical_replay_attempt_identity_source": "lane_wave_sequence",
        "source_workload_framework": str(SOURCE),
        "destination": str(DESTINATION),
        "active_parameters": active_parameters,
        "formal_initial_params": copy.deepcopy(FORMAL_INITIAL_PARAMS),
        "formal_initial_params_shared_between_lanes": True,
        "formal_initial_ranges": formal_ranges,
        "factor_levels_per_parameter": 2,
        "candidates_per_round": candidates_per_round,
        "candidate_values_are_lane_local": True,
        "candidate_pool_shared_between_lanes": False,
        "complete_round_requires_physical_attempt_not_valid_label": True,
        "technical_attempts_excluded_from_valid_and_acceptance": True,
        "prediction_used_for_selection": False,
        "prediction_used_for_ordering": False,
        "prediction_recalibration_required": False,
        "prediction_artifact_role": "candidate_audit_only",
        "accepted_inherited": False,
        "range_state_inherited": False,
        "historical_valid_replay_inherited": False,
        "accepted_target": 7,
        "normalized_width_threshold": 0.01,
        "expected_normalized_width": 0.5 ** 7,
        "stability_confirmation_rounds": 2,
        "physical_replay_parallelism_per_lane": 2,
        "candidate_wave_cohort_between_lanes": False,
        "scenes": {},
    }
    for scene in SCENES:
        source_config = SOURCE / scene / "optimizer_config.json"
        cfg = read_json(source_config)
        shared = cfg.get("algorithm_c")
        if not isinstance(shared, dict):
            raise RuntimeError(f"source lane lacks shared outer framework: {scene}")
        # Algorithm comparisons must start from one immutable PostgreSQL cost
        # baseline.  Never inherit a prior B/C lane champion as Algorithm A's
        # starting point, even when the workload machinery is reused.
        shared["initial_params"] = copy.deepcopy(FORMAL_INITIAL_PARAMS)
        cfg["initial_params"] = copy.deepcopy(FORMAL_INITIAL_PARAMS)
        shared.pop("historical_valid_replay_seed_path", None)
        shared["method"] = METHOD
        shared["algorithm_variant"] = "A"
        shared["candidate_search_contract"] = SEARCH_CONTRACT
        shared["active_parameters"] = active_parameters
        shared["accepted_start"] = 0
        shared["accepted_target"] = 7
        shared["normalized_width_threshold"] = 0.01
        shared["real_replay_top_k"] = candidates_per_round
        shared["real_replay_parallelism"] = 2
        shared["same_plan_different_parameters_retained"] = True
        shared["base_topk_dedupe_key"] = "exact_parameter_vector_only"
        shared["target_support_abstention_enabled"] = False
        shared["prediction_calibration_reused"] = True
        shared["prediction_recalibration_required"] = False
        shared["fresh_t_calibration_required"] = False
        outer_pruning = shared.get("outer_accept_reject_pruning")
        outer_pruning = (
            copy.deepcopy(outer_pruning)
            if isinstance(outer_pruning, dict)
            else {}
        )
        outer_pruning["enabled"] = False
        outer_pruning["algorithm_a_reason"] = (
            "complete_parameter_factorial_must_not_be_pre_replay_pruned"
        )
        shared["outer_accept_reject_pruning"] = outer_pruning
        workload_contract = shared.get("workload_contract")
        if not isinstance(workload_contract, dict):
            raise RuntimeError(f"source workload contract is missing: {scene}")
        workload_contract["candidate_search"] = {
            "contract": SEARCH_CONTRACT,
            "active_parameters": active_parameters,
            "factor_levels_per_parameter": 2,
            "candidates_per_round": candidates_per_round,
            "complete_cartesian_product_physically_replayed": True,
            "same_plan_parameter_vectors_retained": True,
            "complete_round_requires_physical_attempt_not_valid_label": True,
            "technical_attempts_excluded_from_valid_and_acceptance": True,
            "prediction_used_for_selection": False,
            "prediction_used_for_ordering": False,
            "physical_replay_label_contract": REPLAY_LABEL_CONTRACT,
            "candidate_slot_source": "global_candidate_rank",
            "worker_assignment_source": "global_candidate_rank_mod_worker_count",
            "attempt_identity_source": "lane_wave_sequence",
        }
        workload_contract["candidate_cohort"] = {
            "contract": "algorithm_a_lane_independent_candidate_waves_v1",
            "lane_count": 1,
            "synchronization": "none_between_lanes",
            "reason": "lanes_can_reach_acceptance_tiers_at_different_times",
        }
        workload_contract["full_factorial_replay_parallelism"] = 2
        workload_contract["full_factorial_wave_count"] = (
            candidates_per_round // 2
        )
        workload_contract["wave_shape"] = [2] * (candidates_per_round // 2)
        validation_cfg = (
            ((cfg.get("binary_plan_tuning") or {}).get("validation") or {})
        )
        docker_runtime = validation_cfg.get("docker_runtime")
        if not isinstance(docker_runtime, dict):
            raise RuntimeError(f"source Docker runtime is missing: {scene}")
        docker_runtime["disk_devices"] = ["nvme1n1"]
        docker_workers = validation_cfg.get("docker_workers")
        if not isinstance(docker_workers, dict):
            raise RuntimeError(f"source Docker workers are missing: {scene}")
        workers = docker_workers.get("workers")
        if not isinstance(workers, list) or len(workers) != 2:
            raise RuntimeError(f"Algorithm A requires exactly two workers: {scene}")
        for worker in workers:
            if not isinstance(worker, dict):
                raise RuntimeError(f"invalid Algorithm A worker: {scene}")
            worker["disk_devices"] = ["nvme1n1"]
        cfg["algorithm_a"] = {
            "method": METHOD,
            "candidate_search_contract": SEARCH_CONTRACT,
            "active_parameters": active_parameters,
            "formal_initial_ranges": formal_ranges,
            "factor_levels": ["L", "R"],
            "factor_point_rule": "log_or_linear_interval_quarter_three_quarter",
            "candidates_per_round": candidates_per_round,
            "same_plan_parameter_vectors_retained": True,
            "complete_round_requires_physical_attempt_not_valid_label": True,
            "technical_attempts_excluded_from_valid_and_acceptance": True,
            "prediction_artifact": shared["experience_table_path"],
            "prediction_role": "audit_only",
            "prediction_used_for_selection": False,
            "prediction_used_for_ordering": False,
            "real_replay_parallelism": 2,
            "accepted_target": 7,
            "normalized_width_threshold": 0.01,
            "stability_confirmation_rounds": 2,
            "max_technical_retries_per_round": 3,
            "crash_resume_exact_valid_points": True,
            "technical_failures_mutate_semantic_state": False,
            "physical_replay_label_contract": REPLAY_LABEL_CONTRACT,
            "physical_replay_label_format": (
                "algorithm_c_rRRR_algorithm_a_full_factorial_cNNN_aWWWW_worker"
            ),
            "candidate_slot_source": "global_candidate_rank",
            "worker_assignment_source": "global_candidate_rank_mod_worker_count",
            "attempt_identity_source": "lane_wave_sequence",
        }
        binary_search = (
            ((cfg.get("binary_plan_tuning") or {}).get("binary_search") or {})
        )
        binary_search["max_rounds"] = 64
        replay_pruning = binary_search.get("replay_candidate_pruning")
        replay_pruning = replay_pruning if isinstance(replay_pruning, dict) else {}
        replay_pruning["enabled"] = False
        binary_search["replay_candidate_pruning"] = replay_pruning

        output_config = DESTINATION / scene / "optimizer_config.json"
        write_json(output_config, cfg)
        artifact = Path(str(shared["experience_table_path"]))
        if not artifact.is_file():
            raise FileNotFoundError(artifact)
        manifest["scenes"][scene] = {
            "source_config": str(source_config),
            "source_config_sha256": sha256(source_config),
            "config": str(output_config),
            "config_sha256": sha256(output_config),
            "initial_params": copy.deepcopy(FORMAL_INITIAL_PARAMS),
            "initial_params_shared_between_lanes": True,
            "formal_initial_ranges": copy.deepcopy(formal_ranges),
            "candidate_values_derived_independently_from_lane_state": True,
            "ssd_observer_devices": ["nvme1n1"],
            "ssd_observer_device_source": "explicit_frozen_not_findmnt",
            "physical_replay_label_contract": REPLAY_LABEL_CONTRACT,
            "prediction_artifact": str(artifact),
            "prediction_artifact_sha256": sha256(artifact),
            "fresh_state": str(DESTINATION / scene / "algorithm_a_state.json"),
            "fresh_history": str(DESTINATION / scene / "algorithm_a.jsonl"),
        }
    (DESTINATION / "cohort_support").mkdir()
    (DESTINATION / "benchbase_runtime").mkdir()
    manifest_path = DESTINATION / "fresh_campaign_manifest.json"
    write_json(manifest_path, manifest)
    print(str(manifest_path), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
