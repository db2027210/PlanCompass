#!/usr/bin/env python3
"""Prepare fresh Algorithm C r66 with weak IO cost and final champions."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict


SOURCE = Path(
    "/disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809/"
    "20260814_algorithm_c_same_round_three_tier_clean_r65"
)
DESTINATION = Path(
    "/disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809/"
    "20260815_algorithm_c_io_cost_champion_clean_r66"
)
SCENES = ("job_memory", "tpch_memory", "xp_cpu", "xp_io")
TARGETS = {
    "job_memory": "memory",
    "tpch_memory": "memory",
    "xp_cpu": "cpu",
    "xp_io": "io",
}
SCORING_CONTRACT = "algorithm_c_directional_band_target_io_cost_v2"


def read_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    repair_existing = DESTINATION.exists()
    if repair_existing:
        contaminated = [
            path
            for scene in SCENES
            for path in (
                DESTINATION / scene / "algorithm_c_state.json",
                DESTINATION / scene / "algorithm_c.jsonl",
            )
            if path.exists()
        ]
        if contaminated:
            raise RuntimeError(
                f"refusing to repair a started campaign: {contaminated}"
            )
    else:
        DESTINATION.mkdir(parents=True)
    manifest: Dict[str, Any] = {
        "contract": "pg_algorithm_c_weak_io_cost_champion_fresh_campaign_v1",
        "algorithm": "C",
        "source_configs": str(SOURCE),
        "destination": str(DESTINATION),
        "fresh_state": True,
        "accepted_inherited": False,
        "ranges_inherited": False,
        "historical_valid_replay_inherited": False,
        "prediction_calibration_reused": True,
        "prediction_recalibration_required": False,
        "experience_table_coefficients_modified": False,
        "outer_three_tier_acceptance_modified": False,
        "inner_scoring_contract": SCORING_CONTRACT,
        "champion_selection_contract": (
            "strict_then_secondary_then_fallback_qps_resource_v1"
        ),
        "scenes": {},
    }
    for scene in SCENES:
        source_config = SOURCE / scene / "optimizer_config.json"
        config = read_json(source_config)
        algorithm = config.get("algorithm_c")
        if not isinstance(algorithm, dict):
            raise RuntimeError(f"algorithm_c config missing: {scene}")
        algorithm.pop("historical_valid_replay_seed_path", None)
        algorithm["inner_scoring_contract"] = SCORING_CONTRACT
        algorithm["prediction_calibration_reused"] = True
        algorithm["prediction_recalibration_required"] = False
        algorithm["best_feasible_champion_enabled"] = True
        algorithm["champion_selection_contract"] = (
            "strict_then_secondary_then_fallback_qps_resource_v1"
        )
        workload = algorithm.get("workload_contract")
        if not isinstance(workload, dict):
            raise RuntimeError(f"workload contract missing: {scene}")
        target = TARGETS[scene]
        inner = workload.get("inner_candidate_scoring")
        if not isinstance(inner, dict):
            raise RuntimeError(f"inner scoring metadata missing: {scene}")
        inner["contract"] = SCORING_CONTRACT
        inner["resource_weights"] = {
            "cpu": 1.0,
            "memory": 1.0,
            "io": 3.0 if target == "io" else 0.5,
        }
        inner["non_target_io_policy"] = (
            {
                "applicable": False,
                "mode": "target_bottleneck_lower_is_better",
                "weight": 3.0,
                "hard_reject": False,
            }
            if target == "io"
            else {
                "applicable": True,
                "mode": "weak_monotonic_cost",
                "weight": 0.5,
                "dScore_dU_non_positive": True,
                "hard_reject": False,
            }
        )
        inner["qps_feature_forbidden"] = True
        search = ((config.get("binary_plan_tuning") or {}).get("binary_search") or {})
        if not isinstance(search, dict):
            raise RuntimeError(f"binary search config missing: {scene}")
        for score_name in ("real_score_b", "real_score_c"):
            score = search.setdefault(score_name, {})
            if not isinstance(score, dict):
                raise RuntimeError(f"invalid {score_name} config: {scene}")
            score.setdefault("resource_up_scale", 0.10)
            score.setdefault("bonus_coefficient", 0.02)
            score["io_up_penalty_coefficient"] = 0.01
        target_dir = DESTINATION / scene
        target_dir.mkdir(exist_ok=repair_existing)
        output_config = target_dir / "optimizer_config.json"
        atomic_json(output_config, config)
        artifact = Path(str(algorithm.get("experience_table_path") or ""))
        if not artifact.is_file():
            raise FileNotFoundError(artifact)
        manifest["scenes"][scene] = {
            "config": str(output_config),
            "config_sha256": sha256(output_config),
            "experience_table": str(artifact),
            "experience_table_sha256": sha256(artifact),
            "fresh_state_path": str(target_dir / "algorithm_c_state.json"),
            "fresh_history_path": str(target_dir / "algorithm_c.jsonl"),
        }
    (DESTINATION / "cohort_support").mkdir(exist_ok=repair_existing)
    (DESTINATION / "benchbase_runtime").mkdir(exist_ok=repair_existing)
    atomic_json(DESTINATION / "fresh_campaign_manifest.json", manifest)
    print(str(DESTINATION / "fresh_campaign_manifest.json"), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
