#!/usr/bin/env python3
"""Run formal Algorithm B from an audited converged Algorithm C release.

The frozen four-scene executor and its acceptance/shrink implementation are
reused without copying them.  Only the inner candidate producer is replaced:
Algorithm B uses the released <=1% intervals, an estimated coordinate grid,
and complete live EXPLAIN evidence for the fixed real Top-5.
"""

from __future__ import annotations

import argparse
import copy
from pathlib import Path
import sys
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_b_from_algorithm_c_release_v1 as release_mod
from scripts import mysql_co_resident_pg_isolation_v2 as isolation
from scripts import mysql_four_scene_algorithm_b_grid_v1 as grid
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v2 as executor


SCENES = executor.SCENES
STATE_SCHEMA = "mysql_four_scene_algorithm_b_from_c_state_v1"
RANKING_SCHEMA = "mysql_four_scene_algorithm_b_estimated_grid_runtime_v1"


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} object required")
    return dict(value)


def build_seed_checkpoint(
    checkpoint: Mapping[str, Any], release: Mapping[str, Any],
) -> dict[str, Any]:
    """Create an in-memory B state; the immutable C checkpoint stays unchanged."""
    result = copy.deepcopy(dict(checkpoint))
    reconstructed = copy.deepcopy(_mapping(
        checkpoint.get("reconstructed_state"), "Algorithm C reconstructed state",
    ))
    scenes = _mapping(release.get("scenes"), "Algorithm B released scenes")
    zeros = {scene: 0 for scene in SCENES}
    reconstructed.update({
        "outer_attempt": 0,
        "accepted_rounds": copy.deepcopy(zeros),
        "rejected_outer_attempts": copy.deepcopy(zeros),
        "acceptance_tier": {scene: "strict_three_level" for scene in SCENES},
        "consecutive_failed_rounds": copy.deepcopy(zeros),
        "converged": {scene: False for scene in SCENES},
        "interval_stable_rounds": copy.deepcopy(zeros),
        "stable_intervals": {scene: None for scene in SCENES},
        "params_by_scene": {
            scene: copy.deepcopy(_mapping(
                scenes[scene]["algorithm_c_final_incumbent_params"],
                f"{scene} released incumbent",
            ))
            for scene in SCENES
        },
        "ranges_by_scene": {
            scene: copy.deepcopy(_mapping(
                scenes[scene]["algorithm_b_initial_binary_intervals"],
                f"{scene} released intervals",
            ))
            for scene in SCENES
        },
        "active_by_scene": {
            scene: list(scenes[scene]["active_parameters"]) for scene in SCENES
        },
        "incumbents": {
            scene: copy.deepcopy(_mapping(
                scenes[scene]["algorithm_c_final_real_measurement"],
                f"{scene} released real incumbent",
            ))
            for scene in SCENES
        },
        "replayed_param_keys_by_scene": {scene: [] for scene in SCENES},
        "full_plan_replay_history_v2": {scene: [] for scene in SCENES},
    })
    result["completed_outer_attempts"] = 0
    result["reconstructed_state"] = reconstructed
    return result


def _passthrough_plan_history(
    rankings: list[dict[str, Any]], _history: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """B's contract prohibits candidate hard pruning before real Top-5 replay."""
    return copy.deepcopy(rankings), [], {
        "algorithm": "B",
        "candidate_hard_pruning": False,
        "model_only_hard_pruning": False,
        "reason": "fixed_real_top5_requires_full_replay",
    }


def _rank_scene_factory(
    release: Mapping[str, Any], round_by_scene: dict[str, int],
):
    released_scenes = _mapping(release.get("scenes"), "released scenes")

    def rank_scene(
        *, scene: str, params: Mapping[str, float], ranges: Mapping[str, Any],
        resources: Mapping[str, float], freeze: Mapping[str, Any],
        worker: Mapping[str, Any],
    ) -> dict[str, Any]:
        started = time.monotonic()
        round_by_scene[scene] += 1
        dynamic_release = copy.deepcopy(_mapping(
            released_scenes[scene], f"{scene} release",
        ))
        dynamic_release["algorithm_c_final_incumbent_params"] = dict(params)
        dynamic_release["algorithm_b_initial_binary_intervals"] = copy.deepcopy(
            dict(ranges)
        )
        real = copy.deepcopy(_mapping(
            dynamic_release["algorithm_c_final_real_measurement"],
            f"{scene} real incumbent",
        ))
        real["resource_means"] = {
            name: float(resources[name]) for name in ("cpu", "memory", "io")
        }
        dynamic_release["algorithm_c_final_real_measurement"] = real
        document = grid.generate_scene_top5(
            scene, dynamic_release, round_index=round_by_scene[scene],
        )
        rankings = copy.deepcopy(document["selected_top5"])

        executor.alg.apply_costs(worker, params)
        baseline_plans = executor.alg.capture_scene_plans(freeze, scene, worker)
        stats = executor.alg.relation_stats(worker)
        executor_parameters = executor.alg.executor_parameters(worker)
        try:
            for row in rankings:
                readback = executor.alg.apply_costs(
                    worker, row["optimized_params"],
                )
                candidate_plans = executor.alg.capture_scene_plans(
                    freeze, scene, worker,
                )
                plan_diagnostic = executor.alg.enhance_resource_prediction(
                    incumbent_resources=resources,
                    experience_prediction=row["estimated_resources"],
                    baseline_plans=baseline_plans,
                    candidate_plans=candidate_plans,
                    relation_stats=stats,
                    executor_parameters=executor_parameters,
                    concurrency=executor.alg.SCENE_CONCURRENCY[scene],
                    cache_profile=executor.alg.PLAN_CACHE_PROFILE,
                )
                row.update({
                    "experience_prediction": copy.deepcopy(
                        row["estimated_resources"]
                    ),
                    "predicted_resources": copy.deepcopy(
                        row["estimated_resources"]
                    ),
                    "candidate_score_before_confidence": float(
                        row["estimated_score"]
                    ),
                    "candidate_score": float(row["estimated_score"]),
                    "effective_optimizer_cost_readback": readback,
                    "optimizer_cost_readback_tolerance": {
                        "mysql_column_type": "FLOAT",
                        "relative": executor.alg.MYSQL_FLOAT_READBACK_REL_TOL,
                        "absolute": executor.alg.MYSQL_FLOAT_READBACK_ABS_TOL,
                    },
                    "resource_model": {
                        **plan_diagnostic,
                        "kind": "algorithm_b_estimated_grid_with_full_plan_evidence_v1",
                        "estimated_grid_resources": copy.deepcopy(
                            row["estimated_resources"]
                        ),
                        "estimated_grid_score": float(row["estimated_score"]),
                        "qps_used_for_resource_prediction_or_score": False,
                        "candidate_hard_pruning": False,
                        "complete_full_plan_evidence": True,
                    },
                })
        finally:
            executor.alg.apply_costs(worker, params)
        return {
            "scene": scene,
            "representative_worker_id": worker["worker_id"],
            "elapsed_seconds": time.monotonic() - started,
            "rankings": rankings,
        }

    return rank_scene


def main() -> int:
    isolation.install()
    executor.WAVE_COORDINATION_CONTRACT = isolation.CONTRACT_PATH
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--algorithm-b-release", type=Path, required=True)
    pre_parser.add_argument("--checkpoint", type=Path, required=True)
    known, remaining = pre_parser.parse_known_args()
    release_path = known.algorithm_b_release.expanduser().resolve()
    release = release_mod.verify_release(release_path)
    checkpoint_path = known.checkpoint.expanduser().resolve()
    bound_checkpoint = Path(str(_mapping(
        release.get("algorithm_c_checkpoint"), "C checkpoint binding",
    )["path"])).resolve()
    if checkpoint_path != bound_checkpoint:
        raise RuntimeError("Algorithm B release and C checkpoint differ")

    original_verify = executor.checkpointmod.verify_checkpoint
    original_checkpoint = original_verify(checkpoint_path)
    seed_checkpoint = build_seed_checkpoint(original_checkpoint, release)
    original_state_document = executor._state_document
    original_atomic = executor.alg.atomic

    def verify_checkpoint(path: Path) -> dict[str, Any]:
        if Path(path).resolve() == checkpoint_path:
            return copy.deepcopy(seed_checkpoint)
        return original_verify(path)

    def empty_b_quarantine(
        path: Path, _checkpoint: Mapping[str, Any],
    ) -> dict[str, dict[str, list[str]]]:
        if not Path(path).is_file() or Path(path).is_symlink():
            raise RuntimeError("regular compatibility quarantine binding required")
        return {
            kind: {scene: [] for scene in SCENES}
            for kind in ("combined", "soft", "permanent")
        }

    def state_document(**kwargs: Any) -> dict[str, Any]:
        document = original_state_document(**kwargs)
        document.update({
            "schema_version": STATE_SCHEMA,
            "algorithm": "B",
            "method": "outer_binary_inner_estimated_coordinate_grid",
            "algorithm_b_release_path": str(release_path),
            "algorithm_b_release_sha256": executor.alg.sha(release_path),
            "algorithm_b_controller_source_path": str(Path(__file__).resolve()),
            "algorithm_b_controller_source_sha256": executor.alg.sha(
                Path(__file__).resolve()
            ),
            "algorithm_c_checkpoint_role": "converged_seed_only",
            "algorithm_c_replay_quarantine_applied": False,
            "fresh_runtime_dynamic_noninterference_gate_required": True,
            "fresh_incumbent_baseline_required": True,
            "co_resident_isolation_runtime": {
                "policy": "sealed_multi_profile_cpu_disk_memory_readback_v2",
                "installer_source_path": str(Path(isolation.__file__).resolve()),
                "installer_source_sha256": executor.alg.sha(
                    Path(isolation.__file__).resolve()
                ),
                "coordination_contract_path": str(isolation.CONTRACT_PATH),
                "coordination_contract_sha256": executor.alg.sha(
                    isolation.CONTRACT_PATH
                ),
            },
        })
        document["cross_project_wave_coordination"].update({
            "active_external_physical_workloads_remain_fail_closed": False,
            "isolated_co_resident_external_pg_allowed": True,
            "external_cpu_overlap_remains_fail_closed": True,
            "external_disk2_mount_remains_fail_closed": True,
            "unsealed_container_profile_remains_fail_closed": True,
        })
        return document

    def atomic(path: Path, value: Mapping[str, Any]) -> None:
        document = copy.deepcopy(dict(value))
        if path.name == "run_state.json":
            document.update({
                "schema_version": STATE_SCHEMA,
                "algorithm": "B",
                "method": "outer_binary_inner_estimated_coordinate_grid",
                "algorithm_b_release_path": str(release_path),
                "algorithm_b_release_sha256": executor.alg.sha(release_path),
            })
        elif path.name == "predicted_rankings.json":
            document.update({
                "schema_version": RANKING_SCHEMA,
                "algorithm": "B",
                "resource_model": "estimated_coordinate_grid_plus_full_plan_evidence",
                "candidate_hard_pruning": False,
                "algorithm_b_release_path": str(release_path),
                "algorithm_b_release_sha256": executor.alg.sha(release_path),
            })
        elif path.name.endswith(".decision.json"):
            document.update({
                "algorithm": "B",
                "method": "outer_binary_inner_estimated_coordinate_grid",
                "algorithm_b_release_sha256": executor.alg.sha(release_path),
            })
        original_atomic(path, document)

    executor.checkpointmod.verify_checkpoint = verify_checkpoint
    executor._validate_replay_quarantine = empty_b_quarantine
    executor._state_document = state_document
    executor._rank_scene = _rank_scene_factory(
        release, {scene: 0 for scene in SCENES},
    )
    executor.rank_and_prune_candidates = _passthrough_plan_history
    executor.alg.atomic = atomic

    # The reused executor still requires its checkpoint option; restore the two
    # arguments consumed by the pre-parser and remove only B's release option.
    sys.argv = [sys.argv[0], "--checkpoint", str(checkpoint_path), *remaining]
    return executor.main()


if __name__ == "__main__":
    raise SystemExit(main())
