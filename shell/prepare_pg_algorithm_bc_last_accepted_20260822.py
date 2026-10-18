#!/usr/bin/env python3
"""Prepare fresh B/C campaigns with QPS chained to the last accepted round."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, Mapping


PROJECT = Path("/home/anonymous/program/resource_adaptive")
C_SOURCE = Path(
    "/disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809/"
    "20260815_algorithm_c_io_cost_champion_clean_r66"
)
B_SOURCE = Path(
    "/disk1/resource_adaptive/pg_algorithm_b_four_grid_20260814/"
    "20260815_algorithm_b_grid15_io_cost_champion_clean_r2"
)
C_DESTINATION = Path(
    "/disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809/"
    "20260822_algorithm_c_last_accepted_qps_clean_r67"
)
B_DESTINATION = Path(
    "/disk1/resource_adaptive/pg_algorithm_b_four_grid_20260814/"
    "20260822_algorithm_b_last_accepted_qps_clean_r3"
)
SCENES = ("job_memory", "tpch_memory", "xp_cpu", "xp_io")
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


def completed_source_audit(
    source: Path,
    scene: str,
    *,
    algorithm: str,
) -> Dict[str, Any]:
    state_name = f"algorithm_{algorithm.lower()}_state.json"
    history_name = f"algorithm_{algorithm.lower()}.jsonl"
    state_path = source / scene / state_name
    history_path = source / scene / history_name
    config_path = source / scene / "optimizer_config.json"
    for path in (state_path, history_path, config_path):
        if not path.is_file():
            raise FileNotFoundError(path)
    state = read_json(state_path)
    if state.get("phase") != "complete" or int(state.get("accepted") or 0) != 7:
        raise RuntimeError(
            f"source authority is not complete accepted=7: {state_path}"
        )
    return {
        "config": str(config_path),
        "config_sha256": sha256(config_path),
        "state": str(state_path),
        "state_sha256": sha256(state_path),
        "history": str(history_path),
        "history_sha256": sha256(history_path),
        "source_phase": state.get("phase"),
        "source_accepted": int(state.get("accepted") or 0),
    }


def update_config(config: Dict[str, Any], *, algorithm: str) -> None:
    shared = config.get("algorithm_c")
    if not isinstance(shared, dict):
        raise RuntimeError("shared Algorithm C outer framework config missing")
    shared.pop("historical_valid_replay_seed_path", None)
    shared["accepted_start"] = 0
    shared["accepted_target"] = 7
    shared["normalized_width_threshold"] = 0.01
    shared["outer_qps_progress_contract"] = QPS_PROGRESS_CONTRACT
    shared["qps_first_round_reference"] = "canonical_baseline"
    shared["qps_subsequent_round_reference"] = "previous_accepted_round"
    shared["rejected_round_updates_qps_reference"] = False
    shared["technical_failure_updates_qps_reference"] = False
    workload_contract = shared.get("workload_contract")
    if not isinstance(workload_contract, dict):
        raise RuntimeError("workload contract metadata missing")
    workload_contract["outer_qps_acceptance"] = {
        "contract": QPS_PROGRESS_CONTRACT,
        "first_round_reference": "canonical_baseline",
        "subsequent_round_reference": "previous_accepted_round",
        "threshold_source": "outer_acceptance.min_db_qps_gain_pct",
        "resource_reference": "last_accepted_incumbent",
        "qps_noise_model_role": "required_audit_not_compounded_threshold",
        "rejected_round_updates_reference": False,
        "technical_failure_updates_reference": False,
    }
    search = ((config.get("binary_plan_tuning") or {}).get("binary_search") or {})
    if not isinstance(search, dict):
        raise RuntimeError("binary search config missing")
    outer = ((search.get("nested_loop") or {}).get("outer_acceptance") or {})
    if not isinstance(outer, dict):
        raise RuntimeError("outer acceptance config missing")
    outer["qps_reference_mode"] = "last_accepted"
    outer["qps_threshold_mode"] = "static_floor"
    if float(outer.get("min_db_qps_gain_pct") or 0.0) <= 0.0:
        raise RuntimeError("positive QPS gain floor is required")
    if algorithm == "B":
        algorithm_b = config.get("algorithm_b")
        if not isinstance(algorithm_b, dict):
            raise RuntimeError("Algorithm B config missing")
        algorithm_b["outer_qps_progress_contract"] = QPS_PROGRESS_CONTRACT


def prepare_one(
    source: Path,
    destination: Path,
    *,
    algorithm: str,
) -> Dict[str, Any]:
    if destination.exists():
        raise RuntimeError(f"refusing to overwrite campaign: {destination}")
    destination.mkdir(parents=True)
    manifest: Dict[str, Any] = {
        "contract": "pg_algorithm_bc_last_accepted_qps_fresh_campaign_v1",
        "algorithm": algorithm,
        "project": str(PROJECT),
        "source_authority": str(source),
        "destination": str(destination),
        "qps_progress_contract": QPS_PROGRESS_CONTRACT,
        "first_round_reference": "canonical_baseline",
        "subsequent_round_reference": "previous_accepted_round",
        "three_tier_acceptance_unchanged": True,
        "accepted_state_inherited": False,
        "range_state_inherited": False,
        "valid_replay_state_inherited": False,
        "technical_failures_inherited": False,
        "prediction_calibration_reused": True,
        "source_authority_read_only": True,
        "scenes": {},
    }
    for scene in SCENES:
        source_audit = completed_source_audit(
            source,
            scene,
            algorithm=algorithm,
        )
        config = copy.deepcopy(read_json(Path(source_audit["config"])))
        update_config(config, algorithm=algorithm)
        scene_dir = destination / scene
        scene_dir.mkdir()
        config_path = scene_dir / "optimizer_config.json"
        atomic_json(config_path, config)
        state_name = f"algorithm_{algorithm.lower()}_state.json"
        history_name = f"algorithm_{algorithm.lower()}.jsonl"
        if (scene_dir / state_name).exists() or (scene_dir / history_name).exists():
            raise RuntimeError(f"fresh-state invariant failed: {scene_dir}")
        manifest["scenes"][scene] = {
            "source_authority": source_audit,
            "new_config": str(config_path),
            "new_config_sha256": sha256(config_path),
            "fresh_state_path": str(scene_dir / state_name),
            "fresh_history_path": str(scene_dir / history_name),
            "fresh_state_absent": True,
            "fresh_history_absent": True,
        }
    (destination / "cohort_support").mkdir()
    (destination / "benchbase_runtime").mkdir()
    manifest_path = destination / "fresh_campaign_manifest.json"
    atomic_json(manifest_path, manifest)
    return {
        "algorithm": algorithm,
        "destination": str(destination),
        "manifest": str(manifest_path),
        "manifest_sha256": sha256(manifest_path),
    }


def main() -> int:
    prepared = [
        prepare_one(C_SOURCE, C_DESTINATION, algorithm="C"),
        prepare_one(B_SOURCE, B_DESTINATION, algorithm="B"),
    ]
    print(json.dumps(prepared, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
