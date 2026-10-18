#!/usr/bin/env python3
"""Run Algorithm B through the current v27/v11 formal execution chain."""

from __future__ import annotations

import argparse
import copy
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_b_from_algorithm_c_release_v2 as release_mod
from scripts import mysql_co_resident_pg_isolation_v11 as isolation
from scripts import run_mysql_four_scene_algorithm_b_from_c_v1 as predecessor
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v20 as confirmation
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v27 as c27


runtime = c27.runtime
SCENES = runtime.SCENES
STATE_SCHEMA = "mysql_four_scene_algorithm_b_from_c_state_v2"
RANKING_SCHEMA = "mysql_four_scene_algorithm_b_estimated_grid_runtime_v2"
COUNTER_FIELD = "post_target_stability_confirmation_counts"


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} object required")
    return dict(value)


def _rehydrate_b_state(document: dict[str, Any]) -> dict[str, Any]:
    if (
        document.get("schema_version") != STATE_SCHEMA
        or not isinstance(document.get("accepted_rounds"), Mapping)
    ):
        return document
    accepted = confirmation.ConfirmationAcceptedRounds(document["accepted_rounds"])
    persisted = document.get(COUNTER_FIELD) or {}
    if set(persisted) not in ({}, set(accepted)):
        raise RuntimeError("Algorithm B confirmation counter scene set differs")
    accepted.confirmation_acceptances = {
        scene: int(persisted.get(scene, 0)) for scene in accepted
    }
    if any(
        value < 0 or value > confirmation.MAX_POST_TARGET_ACCEPTED_CONFIRMATIONS
        for value in accepted.confirmation_acceptances.values()
    ):
        raise RuntimeError("Algorithm B confirmation counter exceeds bound")
    document["accepted_rounds"] = accepted
    confirmation.ACTIVE_ACCEPTED_ROUNDS = accepted
    return document


def main() -> int:
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

    original_runtime_main = runtime.main

    def algorithm_b_runtime_main() -> int:
        original_verify = runtime.checkpointmod.verify_checkpoint
        original_checkpoint = original_verify(checkpoint_path)
        seed_checkpoint = predecessor.build_seed_checkpoint(
            original_checkpoint, release,
        )
        original_state_document = runtime._state_document
        original_atomic = runtime.alg.atomic
        original_load = runtime.alg.load

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
                "algorithm_b_release_sha256": runtime.alg.sha(release_path),
                "algorithm_b_controller_source_path": str(Path(__file__).resolve()),
                "algorithm_b_controller_source_sha256": runtime.alg.sha(
                    Path(__file__).resolve()
                ),
                "algorithm_c_checkpoint_role": "converged_seed_only",
                "algorithm_c_replay_quarantine_applied": False,
                "fresh_runtime_dynamic_noninterference_gate_required": True,
                "fresh_incumbent_baseline_required": True,
                COUNTER_FIELD: {scene: 0 for scene in SCENES},
                "co_resident_isolation_runtime": {
                    "policy": "sealed_multi_profile_cpu_disk_memory_readback_v3",
                    "installer_source_path": str(Path(isolation.__file__).resolve()),
                    "installer_source_sha256": runtime.alg.sha(
                        Path(isolation.__file__).resolve()
                    ),
                    "coordination_contract_path": str(isolation.CONTRACT_PATH),
                    "coordination_contract_sha256": runtime.alg.sha(
                        isolation.CONTRACT_PATH
                    ),
                },
                "algorithm_b_current_runtime_release": {
                    "release_version": "v2",
                    "algorithm_c_executor_chain": "v27",
                    "isolation_release": "v11",
                    "durable_post_target_confirmation": True,
                    "accepted_round_credit_capped_at_10": True,
                    "maximum_accepted_confirmations": 2,
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

        def load(path: Path) -> dict[str, Any]:
            return _rehydrate_b_state(original_load(path))

        def atomic(path: Path, value: Mapping[str, Any]) -> None:
            document = copy.deepcopy(dict(value))
            accepted = value.get("accepted_rounds")
            if (
                path.name == "run_state.json"
                and isinstance(accepted, confirmation.ConfirmationAcceptedRounds)
            ):
                document[COUNTER_FIELD] = copy.deepcopy(
                    accepted.confirmation_acceptances
                )
            if path.name == "run_state.json":
                document.update({
                    "schema_version": STATE_SCHEMA,
                    "algorithm": "B",
                    "method": "outer_binary_inner_estimated_coordinate_grid",
                    "algorithm_b_release_path": str(release_path),
                    "algorithm_b_release_sha256": runtime.alg.sha(release_path),
                })
            elif path.name == "predicted_rankings.json":
                document.update({
                    "schema_version": RANKING_SCHEMA,
                    "algorithm": "B",
                    "resource_model": (
                        "estimated_coordinate_grid_plus_full_plan_evidence"
                    ),
                    "candidate_hard_pruning": False,
                    "algorithm_b_release_path": str(release_path),
                    "algorithm_b_release_sha256": runtime.alg.sha(release_path),
                })
            elif path.name.endswith(".decision.json"):
                document.update({
                    "algorithm": "B",
                    "method": "outer_binary_inner_estimated_coordinate_grid",
                    "algorithm_b_release_sha256": runtime.alg.sha(release_path),
                })
            original_atomic(path, document)

        runtime.checkpointmod.verify_checkpoint = verify_checkpoint
        runtime._validate_replay_quarantine = empty_b_quarantine
        runtime._state_document = state_document
        runtime._rank_scene = predecessor._rank_scene_factory(
            release, {scene: 0 for scene in SCENES},
        )
        runtime.rank_and_prune_candidates = predecessor._passthrough_plan_history
        runtime.alg.load = load
        runtime.alg.atomic = atomic
        sys.argv = [sys.argv[0], "--checkpoint", str(checkpoint_path), *remaining]
        return original_runtime_main()

    runtime.main = algorithm_b_runtime_main
    isolation.validate_release()
    return c27.main()


if __name__ == "__main__":
    raise SystemExit(main())
