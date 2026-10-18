#!/usr/bin/env python3
"""Create a fresh Algorithm B campaign from the completed r65 framework."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict


SOURCE = Path(
    "/disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809/"
    "20260814_algorithm_c_same_round_three_tier_clean_r65"
)
DESTINATION = Path(
    "/disk1/resource_adaptive/pg_algorithm_b_four_grid_20260814/"
    "20260814_algorithm_b_grid15_same_framework_r1"
)
SCENES = ("job_memory", "tpch_memory", "xp_cpu", "xp_io")


def read_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=False)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    if DESTINATION.exists():
        raise RuntimeError(f"fresh Algorithm B destination already exists: {DESTINATION}")
    DESTINATION.mkdir(parents=True)
    manifest: Dict[str, Any] = {
        "contract": "pg_algorithm_b_same_outer_framework_fresh_campaign_v1",
        "algorithm": "B",
        "method": "coordinate_grid_algorithm_b_v1",
        "source_framework": str(SOURCE),
        "destination": str(DESTINATION),
        "prediction_recalibration_required": False,
        "prediction_calibration_reused": True,
        "algorithm_c_semantic_state_inherited": False,
        "accepted_inherited": False,
        "range_state_inherited": False,
        "historical_valid_replay_inherited": False,
        "accepted_target": 7,
        "normalized_width_threshold": 0.01,
        "stability_contract": (
            "normalized_width_1pct_two_consecutive_same_worker_temporal_v3"
        ),
        "scenes": {},
    }
    for scene in SCENES:
        source_config = SOURCE / scene / "optimizer_config.json"
        cfg = read_json(source_config)
        shared = cfg.get("algorithm_c")
        if not isinstance(shared, dict):
            raise RuntimeError(f"source lane lacks shared Algorithm C framework: {scene}")
        shared.pop("historical_valid_replay_seed_path", None)
        shared["method"] = "coordinate_grid_algorithm_b_v1"
        shared["algorithm_variant"] = "B"
        shared["candidate_search_contract"] = (
            "coordinate_grid15_plus_exhaustive_interactions_2_to_4_v1"
        )
        shared["prediction_calibration_reused"] = True
        shared["prediction_recalibration_required"] = False
        shared["fresh_t_calibration_required"] = False
        shared["accepted_start"] = 0
        shared["accepted_target"] = 7
        shared["normalized_width_threshold"] = 0.01
        cfg["algorithm_b"] = {
            "method": "coordinate_grid_algorithm_b_v1",
            "grid_points": 15,
            "max_workers": 8,
            "max_passes": 20,
            "no_improvement_patience": 2,
            "combination_probe_enabled": True,
            "combination_min_size": 2,
            "combination_max_size": 4,
            "combination_values_per_param": 3,
            "combination_max_candidates": 0,
            "resource_prediction_artifact": shared["experience_table_path"],
            "prediction_recalibration_required": False,
            "outer_framework": "algorithm_c_proven_outer_framework_v65",
            "real_replay_top_k": 5,
            "real_replay_parallelism": 2,
            "accepted_target": 7,
            "normalized_width_threshold": 0.01,
            "stability_confirmation_rounds": 2,
        }
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
            "prediction_artifact": str(artifact),
            "prediction_artifact_sha256": sha256(artifact),
            "fresh_state": str(DESTINATION / scene / "algorithm_b_state.json"),
            "fresh_history": str(DESTINATION / scene / "algorithm_b.jsonl"),
        }
    (DESTINATION / "cohort_support").mkdir()
    (DESTINATION / "benchbase_runtime").mkdir()
    manifest_path = DESTINATION / "fresh_campaign_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(str(manifest_path), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
