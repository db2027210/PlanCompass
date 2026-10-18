#!/usr/bin/env python3
"""Prepare a fresh Algorithm C campaign with a sealed training/runtime boundary."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, Mapping


PROJECT = Path("/home/anonymous/program/resource_adaptive")
RUN_BASE = Path(
    "/disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809"
)
SOURCE = RUN_BASE / (
    "20260822_algorithm_c_monotonic_qps_top10_current_interval_clean_r74"
)
SCENES = ("job_memory", "tpch_memory", "xp_cpu", "xp_io")
MODEL_ROOT = Path("/tmp/algorithm_c_resource_models_v14_20260823")
QPS_CONTRACT = "candidate_qps_strictly_above_previous_accepted_round_v1"
RUNTIME_FIREWALL_CONTRACT = "algorithm_c_runtime_candidate_prior_firewall_v1"

SOURCE_FILES = (
    "algorithm_c_calibration_release.py",
    "binary_plan_resource_tuner.py",
    "bonect_interval_lineage.py",
    "common_outer_loop.py",
    "experience_table_subset_optimizer.py",
    "extract_plan_trees.py",
    "gradient_descent_optimizer.py",
    "logger_utils.py",
    "operator_resource_rules.py",
    "plan_resource_estimator.py",
    "resource_feature_predictor.py",
    "resource_utility.py",
    "same_ssd_concurrency.py",
    "system_resource_monitor.py",
    "shell/algorithm_c_parameter_support.py",
    "shell/algorithm_c_training_boundary.py",
    "shell/build_algorithm_c_experience_table_from_replays.py",
    "shell/launch_pg_algorithm_c_four_experience_sgd_20260809.py",
    "shell/launch_pg_algorithm_c_four_parallel2_20260809.py",
    "shell/pg_algorithm_c_lane_entry.py",
    "shell/run_benchmark_capture.py",
    "shell/run_job_fixed_benchmark_capture.py",
    "shell/run_hybench_native_xp_isolated.sh",
    "shell/run_pg_algorithm_c_calibrate_then_scene.py",
    "shell/run_pg_algorithm_c_scene.py",
    "shell/run_pg_full_workload_isolated.sh",
    "shell/run_pg_job_fixed_workload_isolated.sh",
    "tools/bonect_fadvise_dontneed.c",
)


def read_json(path: Path) -> Dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return payload


def atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_provenance() -> Dict[str, Any]:
    files: Dict[str, str] = {}
    for relative_name in SOURCE_FILES:
        path = PROJECT / relative_name
        if not path.is_file():
            raise FileNotFoundError(path)
        files[relative_name] = sha256(path)
    return {
        "contract": "algorithm_c_launch_source_sha256_v1",
        "files": files,
    }


def update_config(config: Dict[str, Any], runtime_model_path: Path) -> None:
    algorithm_c = config.get("algorithm_c")
    if not isinstance(algorithm_c, dict):
        raise RuntimeError("Algorithm C configuration missing")
    algorithm_c.pop("historical_valid_replay_seed_path", None)
    algorithm_c["accepted_start"] = 0
    algorithm_c["accepted_target"] = 7
    algorithm_c["normalized_width_threshold"] = 0.01
    algorithm_c["outer_qps_progress_contract"] = QPS_CONTRACT
    algorithm_c["qps_first_round_reference"] = "canonical_baseline"
    algorithm_c["qps_subsequent_round_reference"] = "previous_accepted_round"
    algorithm_c["rejected_round_updates_qps_reference"] = False
    algorithm_c["technical_failure_updates_qps_reference"] = False
    algorithm_c["runtime_candidate_prior_firewall"] = {
        "contract": RUNTIME_FIREWALL_CONTRACT,
        "algorithm_a_runtime_candidate_inputs_allowed": False,
        "algorithm_b_runtime_candidate_inputs_allowed": False,
        "historical_candidate_parameters_allowed": False,
        "historical_plan_fingerprints_allowed": False,
        "historical_candidate_rankings_allowed": False,
        "historical_accept_reject_labels_allowed": False,
        "qps_used_for_resource_prediction": False,
        "qps_used_for_gradient": False,
        "qps_used_for_candidate_score": False,
        "qps_used_for_candidate_ranking": False,
        "qps_role": "outer_real_replay_acceptance_only",
    }
    algorithm_c["offline_resource_training_audit"] = {
        "contract": "algorithm_c_abc_resource_training_c_holdout_audit_v1",
        "artifact": str(runtime_model_path),
        "training_sources": ["algorithm_a", "algorithm_b", "algorithm_c"],
        "holdout_source": "algorithm_c",
        "qps_used": False,
        "formal_admission_ready": True,
        "promoted_to_runtime": True,
        "failure_policy": "fail_closed_before_physical_replay",
    }
    algorithm_c["experience_table_path"] = str(runtime_model_path)
    algorithm_c["runtime_resource_model_policy"] = {
        "contract": "algorithm_c_new_abc_resource_model_c_holdout_admitted_v1",
        "experience_table_path": str(runtime_model_path),
        "historical_runtime_candidates_imported": False,
        "qps_used": False,
    }

    workload_contract = algorithm_c.get("workload_contract")
    if not isinstance(workload_contract, dict):
        raise RuntimeError("workload contract missing")
    workload_contract["training_runtime_boundary"] = {
        "contract": RUNTIME_FIREWALL_CONTRACT,
        "offline_a_b_c_resource_training_allowed": True,
        "algorithm_a_runtime_search_prior_allowed": False,
        "algorithm_b_runtime_search_prior_allowed": False,
        "qps_role": "outer_real_replay_acceptance_only",
    }
    workload_contract["outer_qps_acceptance"] = {
        "contract": QPS_CONTRACT,
        "first_round_reference": "canonical_baseline",
        "subsequent_round_reference": "previous_accepted_round",
        "threshold_source": "outer_acceptance.min_db_qps_gain_pct",
        "resource_reference": "last_accepted_incumbent",
        "rejected_round_updates_reference": False,
        "technical_failure_updates_reference": False,
    }

    search = ((config.get("binary_plan_tuning") or {}).get("binary_search") or {})
    outer = ((search.get("nested_loop") or {}).get("outer_acceptance") or {})
    if not isinstance(outer, dict):
        raise RuntimeError("outer acceptance config missing")
    outer["qps_reference_mode"] = "last_accepted"
    outer["qps_threshold_mode"] = "static_floor"
    if float(outer.get("min_db_qps_gain_pct") or 0.0) <= 0.0:
        raise RuntimeError("positive QPS progress threshold required")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--tag",
        default="20260823_algorithm_c_training_boundary_clean_r75",
    )
    args = parser.parse_args()
    destination = RUN_BASE / args.tag
    if destination.exists():
        raise RuntimeError(f"refusing to overwrite campaign: {destination}")

    provenance = source_provenance()
    destination.mkdir(parents=True)
    (destination / "cohort_support").mkdir()
    (destination / "benchbase_runtime").mkdir()
    manifest: Dict[str, Any] = {
        "contract": "pg_algorithm_c_training_runtime_boundary_fresh_campaign_v1",
        "algorithm": "C",
        "source_authority": str(SOURCE),
        "destination": str(destination),
        "created_at_epoch_s": time.time(),
        "accepted_start": 0,
        "accepted_target": 7,
        "accepted_state_inherited": False,
        "range_state_inherited": False,
        "valid_replay_state_inherited": False,
        "technical_failures_inherited": False,
        "preexisting_campaigns_read_only": True,
        "qps_progress_contract": QPS_CONTRACT,
        "qps_inner_model_or_score_used": False,
        "three_tier_same_batch": True,
        "algorithm_a_runtime_prior_used": False,
        "algorithm_b_runtime_prior_used": False,
        "offline_abc_training_diagnostics_promoted": True,
        "scenes": {},
        "source_provenance": provenance,
    }

    launch_scenes: Dict[str, str] = {}
    for scene in SCENES:
        source_dir = SOURCE / scene
        source_config = source_dir / "optimizer_config.json"
        source_state = source_dir / "algorithm_c_state.json"
        source_history = source_dir / "algorithm_c.jsonl"
        for path in (source_config, source_state, source_history):
            if not path.is_file():
                raise FileNotFoundError(path)
        model_source = MODEL_ROOT / f"{scene}_abc_train_c_holdout.json"
        if not model_source.is_file():
            raise FileNotFoundError(model_source)
        scene_dir = destination / scene
        scene_dir.mkdir()
        runtime_model_destination = (
            scene_dir / "experience_table_T_abc_c_holdout_admitted.json"
        )
        shutil.copy2(model_source, runtime_model_destination)
        config = copy.deepcopy(read_json(source_config))
        update_config(config, runtime_model_destination)
        config_path = scene_dir / "optimizer_config.json"
        atomic_json(config_path, config)
        table_path = Path(str(config["algorithm_c"]["experience_table_path"]))
        table = read_json(table_path)
        admission = table.get("formal_admission")
        if not isinstance(admission, dict) or admission.get("ready") is not True:
            raise RuntimeError(f"runtime experience table not admitted: {table_path}")
        if (scene_dir / "algorithm_c_state.json").exists() or (
            scene_dir / "algorithm_c.jsonl"
        ).exists():
            raise RuntimeError(f"fresh state invariant failed: {scene_dir}")
        launch_scenes[scene] = str(config_path)
        manifest["scenes"][scene] = {
            "source_config": str(source_config),
            "source_config_sha256": sha256(source_config),
            "source_state": str(source_state),
            "source_state_sha256": sha256(source_state),
            "source_history": str(source_history),
            "source_history_sha256": sha256(source_history),
            "new_config": str(config_path),
            "new_config_sha256": sha256(config_path),
            "runtime_experience_table": str(table_path),
            "runtime_experience_table_sha256": sha256(table_path),
            "runtime_experience_table_schema": int(table.get("schema_version") or 0),
            "runtime_experience_table_ready": True,
            "offline_training_artifact": str(runtime_model_destination),
            "offline_training_artifact_sha256": sha256(runtime_model_destination),
            "offline_training_artifact_ready": True,
            "offline_training_artifact_target_safety_mode": (
                (table.get("formal_admission") or {}).get(
                    "target_prediction_safety_mode"
                )
            ),
            "fresh_state_absent": True,
            "fresh_history_absent": True,
        }

    launch_manifest = {
        "contract": "algorithm_c_launch_manifest_training_boundary_v1",
        "run_root": str(destination),
        "method": "experience_table_all_subset_sgd_algorithm_c_v1",
        "scenes": launch_scenes,
        "source_provenance": provenance,
        "accepted_start": 0,
        "accepted_target": 7,
        "normalized_width_threshold": 0.01,
        "top_k": 5,
        "parallelism_per_scene": 2,
        "qps_progress_contract": QPS_CONTRACT,
        "qps_inner_model_or_score_used": False,
        "algorithm_a_runtime_prior_used": False,
        "offline_abc_resource_training_promoted": True,
        "final_holdout_source": "algorithm_c",
        "final_holdout_used_for_model_selection": False,
        "mysql_containers_untouched": True,
    }
    atomic_json(destination / "launch_manifest.json", launch_manifest)
    atomic_json(destination / "fresh_campaign_manifest.json", manifest)
    print(str(destination), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
