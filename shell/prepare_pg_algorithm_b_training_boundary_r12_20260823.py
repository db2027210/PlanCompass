#!/usr/bin/env python3
"""Prepare a fresh Algorithm B r12 campaign with admitted resource models.

The shared outer-loop and workload contracts come from the currently running
Algorithm C r75 campaign.  Algorithm B receives only its native deterministic
grid configuration from the previous B template.  No optimizer state,
candidate list, plan rank, QPS label, or accepted point is copied.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, Mapping


PROJECT = Path("/home/anonymous/program/resource_adaptive")
C_CONFIG_SOURCE = Path(
    "/disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809/"
    "20260823_algorithm_c_training_boundary_clean_r75"
)
B_GRID_SOURCE = Path(
    "/disk1/resource_adaptive/pg_algorithm_b_four_grid_20260814/"
    "20260822_algorithm_b_monotonic_qps_top10_current_interval_clean_r10"
)
B_DESTINATION = Path(
    "/disk1/resource_adaptive/pg_algorithm_b_four_grid_20260814/"
    "20260823_algorithm_b_training_boundary_clean_r12"
)
SCENES = ("job_memory", "tpch_memory", "xp_cpu", "xp_io")
ARTIFACT_NAME = "experience_table_T_abc_c_holdout_admitted.json"
B_METHOD = "coordinate_grid_algorithm_b_v1"
B_SEARCH_CONTRACT = (
    "coordinate_grid15_plus_exhaustive_interactions_2_to_4_v1"
)
QPS_PROGRESS_CONTRACT = (
    "candidate_qps_strictly_above_previous_accepted_round_v1"
)


def read_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def replace_exact_strings(value: Any, old: str, new: str) -> Any:
    """Replace an exact artifact path without rewriting unrelated provenance."""

    if isinstance(value, dict):
        return {
            key: replace_exact_strings(child, old, new)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [replace_exact_strings(child, old, new) for child in value]
    if isinstance(value, str) and value == old:
        return new
    return value


def validate_shared_outer(config: Mapping[str, Any], scene: str) -> None:
    shared = config.get("algorithm_c")
    if not isinstance(shared, Mapping):
        raise RuntimeError(f"{scene}: shared outer config missing")
    search = ((config.get("binary_plan_tuning") or {}).get("binary_search") or {})
    outer = ((search.get("nested_loop") or {}).get("outer_acceptance") or {})
    required = {
        "accepted_target": int(shared.get("accepted_target") or 0) == 7,
        "outer_round_limit": int(shared.get("outer_round_limit") or 0) == 13,
        "no_improvement_rounds": int(
            shared.get("no_improvement_convergence_rounds") or 0
        )
        == 2,
        "qps_contract": (
            shared.get("outer_qps_progress_contract")
            == QPS_PROGRESS_CONTRACT
        ),
        "qps_reference": outer.get("qps_reference_mode") == "last_accepted",
        "physical_top5": int(search.get("max_candidates_per_round") or 0) == 5,
        "replay_parallelism": int(shared.get("real_replay_parallelism") or 0)
        == 2,
        "post_stability_disabled": (
            shared.get("post_convergence_stability_required") is False
        ),
    }
    errors = [name for name, passed in required.items() if not passed]
    if errors:
        raise RuntimeError(f"{scene}: shared outer contract failed: {errors}")


def prepare_scene(scene: str) -> Dict[str, Any]:
    source_dir = C_CONFIG_SOURCE / scene
    grid_dir = B_GRID_SOURCE / scene
    destination_dir = B_DESTINATION / scene
    config_source = source_dir / "optimizer_config.json"
    artifact_source = source_dir / ARTIFACT_NAME
    grid_source = grid_dir / "optimizer_config.json"
    for path in (config_source, artifact_source, grid_source):
        if not path.is_file():
            raise FileNotFoundError(path)

    destination_dir.mkdir(parents=True, exist_ok=False)
    artifact_destination = destination_dir / ARTIFACT_NAME
    shutil.copy2(artifact_source, artifact_destination)

    config = copy.deepcopy(read_json(config_source))
    grid_config = read_json(grid_source)
    algorithm_b = copy.deepcopy(grid_config.get("algorithm_b"))
    if not isinstance(algorithm_b, dict):
        raise RuntimeError(f"{scene}: Algorithm B grid template missing")

    old_artifact = str(
        ((config.get("algorithm_c") or {}).get("experience_table_path") or "")
    )
    if old_artifact != str(artifact_source):
        raise RuntimeError(
            f"{scene}: Algorithm C artifact source mismatch: {old_artifact}"
        )
    config = replace_exact_strings(
        config,
        old_artifact,
        str(artifact_destination),
    )
    shared = config["algorithm_c"]
    shared["method"] = B_METHOD
    shared["candidate_search_contract"] = B_SEARCH_CONTRACT
    shared["accepted_start"] = 0
    shared["accepted_target"] = 7
    shared["outer_qps_progress_contract"] = QPS_PROGRESS_CONTRACT
    shared["post_convergence_stability_required"] = False
    shared["no_improvement_convergence_rounds"] = 2
    shared["stability_confirmation_rounds"] = 0
    shared["stability_confirmation_max_attempts"] = 0
    shared["runtime_native_algorithm_policy"] = {
        "contract": "algorithm_b_native_grid_without_cross_algorithm_candidate_prior_v1",
        "native_algorithm_b_grid_allowed": True,
        "algorithm_a_candidate_prior_allowed": False,
        "algorithm_c_candidate_prior_allowed": False,
        "historical_candidate_parameters_allowed": False,
        "historical_candidate_rankings_allowed": False,
        "historical_plan_fingerprints_allowed": False,
        "historical_accept_reject_labels_allowed": False,
        "qps_role": "outer_real_replay_acceptance_only",
    }

    algorithm_b.update(
        {
            "method": B_METHOD,
            "accepted_target": 7,
            "normalized_width_threshold": 0.01,
            "no_improvement_convergence_rounds": 2,
            "stability_confirmation_rounds": 0,
            "stability_confirmation_max_attempts": 0,
            "outer_qps_progress_contract": QPS_PROGRESS_CONTRACT,
            "resource_prediction_artifact": str(artifact_destination),
            "prediction_recalibration_required": False,
            "real_replay_top_k": 5,
            "real_replay_parallelism": 2,
            "outer_framework": (
                "algorithm_c_training_boundary_r75_shared_outer_framework"
            ),
            "shared_outer_top10_policy": True,
            "cross_algorithm_runtime_candidate_prior_used": False,
        }
    )
    config["algorithm_b"] = algorithm_b
    validate_shared_outer(config, scene)

    config_destination = destination_dir / "optimizer_config.json"
    atomic_json(config_destination, config)
    return {
        "scene": scene,
        "config_source": str(config_source),
        "config_source_sha256": sha256(config_source),
        "grid_source": str(grid_source),
        "grid_source_sha256": sha256(grid_source),
        "artifact_source": str(artifact_source),
        "artifact_source_sha256": sha256(artifact_source),
        "config": str(config_destination),
        "config_sha256": sha256(config_destination),
        "artifact": str(artifact_destination),
        "artifact_sha256": sha256(artifact_destination),
        "fresh_state_absent": not (
            destination_dir / "algorithm_b_state.json"
        ).exists(),
        "fresh_history_absent": not (
            destination_dir / "algorithm_b.jsonl"
        ).exists(),
    }


def main() -> int:
    if B_DESTINATION.exists():
        raise RuntimeError(f"refusing to overwrite campaign: {B_DESTINATION}")
    B_DESTINATION.mkdir(parents=True, exist_ok=False)
    (B_DESTINATION / "cohort_support").mkdir()
    (B_DESTINATION / "benchbase_runtime").mkdir()
    scenes = {scene: prepare_scene(scene) for scene in SCENES}
    manifest = {
        "contract": "pg_algorithm_b_training_boundary_fresh_campaign_v1",
        "algorithm": "B",
        "destination": str(B_DESTINATION),
        "created_at_epoch_s": time.time(),
        "shared_outer_source": str(C_CONFIG_SOURCE),
        "native_grid_source": str(B_GRID_SOURCE),
        "accepted_target": 7,
        "normalized_width_threshold": 0.01,
        "qps_progress_contract": QPS_PROGRESS_CONTRACT,
        "three_tier_acceptance": (
            "same_batch_strict_then_secondary_then_fallback"
        ),
        "accepted_requires_qps_strictly_above_previous_accepted": True,
        "qps_used_for_inner_prediction_or_ranking": False,
        "post_convergence_stability_required": False,
        "fresh_baseline_required": True,
        "algorithm_c_incumbent_inherited": False,
        "algorithm_c_candidate_prior_inherited": False,
        "preexisting_campaigns_read_only": True,
        "scenes": scenes,
    }
    manifest_path = B_DESTINATION / "fresh_campaign_manifest.json"
    atomic_json(manifest_path, manifest)
    print(
        json.dumps(
            {
                "destination": str(B_DESTINATION),
                "manifest": str(manifest_path),
                "manifest_sha256": sha256(manifest_path),
                "scenes": scenes,
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
