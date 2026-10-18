#!/usr/bin/env python3
"""Run v93 C with one campaign and B-completion-gated scene releases."""

from __future__ import annotations

from pathlib import Path
import os
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_tpch_client_qps_measurement_v2 as tpch_metric
from scripts import mysql_c_external_storage_inventory_only_v1 as external_inventory
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v3 as predecessor


CONTRACT_ROOT = ROOT / (
    "config/formal/"
    "frozen_mysql_four_scene_algorithm_c_client_primary_20260903_v93"
)


def mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} object required")
    return dict(value)


def validate_contract_v93() -> dict[str, Any]:
    alg = predecessor.predecessor.predecessor.predecessor.alg
    parent_path = CONTRACT_ROOT / "algorithm_c_async_contract.json"
    optimization_path = CONTRACT_ROOT / "outer_runtime_optimization.json"
    successor_path = CONTRACT_ROOT / "global_monotonic_incumbent_contract.json"
    parent = alg.load(parent_path)
    optimization = alg.load(optimization_path)
    successor = alg.load(successor_path)
    release = mapping(successor.get("full_parallel_launch"), "scene release")
    if (
        parent.get("release_version") != "v93-staged-client-primary"
        or parent.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or parent.get("formal_scope") != "mysql_only"
        or parent.get("cross_engine_comparison_prohibited") is not True
        or parent.get("outer_acceptance_policy")
        != predecessor.predecessor.predecessor.predecessor.qps_policy.policy_contract()
        or parent.get("outer_rejection_cycle_policy")
        != predecessor.predecessor.predecessor.predecessor.rejection_cycle.policy_contract()
        or parent.get("candidate_batches") != [[1, 2], [3, 4], [5]]
        or parent.get("physical_replay_parallelism_per_scene") != 2
        or parent.get("maximum_concurrent_mysql_workers") != 8
        or parent.get("four_scene_wave_barrier") is not False
        or parent.get("sibling_scene_wait") is not False
        or parent.get("lease_mode") != "none"
        or parent.get("post_search_stability_confirmation") is not False
        or parent.get("all_four_scenes_started_together") is not False
        or parent.get("all_four_scenes_declared_in_same_formal_campaign") is not True
        or parent.get("per_scene_b_completion_release") is not True
        or parent.get("tpch_qps_metric_contract") != tpch_metric.contract()
        or optimization.get("release_version") != "v93-staged-client-primary"
        or successor.get("release_version") != "v93-staged-client-primary"
        or successor.get("parent_contract", {}).get("sha256") != alg.sha(parent_path)
        or successor.get("xp_outer_primary_metric")
        != "native_hybench_xp_client_xap_qps"
        or successor.get("tpch_outer_primary_metric") != tpch_metric.METRIC
        or successor.get("tpch_server_questions_role") != "audit_only"
        or release.get("all_four_scenes_started_together") is not False
        or release.get("all_four_scenes_declared_in_same_formal_campaign") is not True
        or release.get("per_scene_b_completion_release") is not True
        or release.get("completed_scene_early_release_allowed") is not True
        or release.get("physical_workers_per_scene") != 2
        or release.get("maximum_concurrent_mysql_workers") != 8
        or release.get("four_scene_wave_barrier") is not False
        or release.get("sibling_scene_wait") is not False
        or release.get("lease_mode") != "none"
        or successor.get("external_storage_co_residency_override", {}).get(
            "waiver_id"
        ) != external_inventory.WAIVER_ID
    ):
        raise RuntimeError("Algorithm C v93 staged client-primary contract differs")
    for document in (parent, successor):
        for name, raw in mapping(
            document.get("source_bindings"), "v93 source bindings",
        ).items():
            row = mapping(raw, f"v93 {name}")
            path = (ROOT / str(row.get("path") or "")).resolve()
            if (
                not path.is_file() or path.is_symlink()
                or alg.sha(path) != str(row.get("sha256") or "")
            ):
                raise RuntimeError(f"Algorithm C v93 source differs: {name}")
    return {"parent": parent, "optimization": optimization, "successor": successor}


def main() -> int:
    predecessor.CONTRACT_ROOT = CONTRACT_ROOT
    predecessor.validate_contract_v92 = validate_contract_v93

    v1 = predecessor.predecessor
    v74 = v1.predecessor
    original_v74_main = v74.main

    def staged_v74_main() -> int:
        v73 = v74.predecessor
        runtime = v73.runtime
        original_runtime_main = runtime.main

        def runtime_main_with_final_client_binding() -> int:
            # Several historical wrappers install after v62 and can restore
            # the legacy reset module.  Bind the frozen client-primary reset
            # at the final runtime boundary, immediately before validation.
            v73.alg.reset = v1.xp_reset
            runtime.alg.reset = v1.xp_reset
            v73.alg.r0mod.PREFLIGHT_SOURCE = Path(external_inventory.__file__).resolve()
            memory_pair = os.environ.get("MYSQL_C_SCENE_GROUP") == "memory_pair"
            original_scenes = runtime.SCENES
            original_independent = runtime.INDEPENDENT_SCENE_EXECUTION
            original_predecessor_scenes = runtime.predecessor.SCENES
            original_validate_authority = v73.alg.r0mod._validate_authority
            if memory_pair:
                memory_scenes = ("job_memory", "tpch_sf4_memory")
                runtime.SCENES = memory_scenes
                runtime.predecessor.SCENES = memory_scenes

                def validate_memory_pair_authority():
                    previous_scope = v73.alg.r0mod.AUTHORITY_SCENE_SCOPE
                    workers: dict[str, Any] = {}
                    scene_gates: dict[str, Any] = {}
                    freeze = None
                    try:
                        for scene in memory_scenes:
                            v73.alg.r0mod.AUTHORITY_SCENE_SCOPE = scene
                            scene_freeze, scene_workers, scene_gate = (
                                original_validate_authority()
                            )
                            freeze = scene_freeze
                            workers.update(scene_workers)
                            scene_gates[scene] = scene_gate
                    finally:
                        v73.alg.r0mod.AUTHORITY_SCENE_SCOPE = previous_scope
                    if len(workers) != 4:
                        raise RuntimeError("memory-pair worker authority differs")
                    point_gate = {
                        "captured_at": max(
                            str(row.get("captured_at") or "")
                            for row in scene_gates.values()
                        ),
                        "passed": all(
                            row.get("passed") is True for row in scene_gates.values()
                        ),
                        "authority_scene_scope": "memory_pair",
                        "memory_pair_worker_ids": sorted(workers),
                        "per_scene_point_gates": scene_gates,
                        "external_process_control_or_stop_performed": False,
                        "postgresql_process_control_or_stop_performed": False,
                    }
                    if freeze is None or point_gate["passed"] is not True:
                        raise RuntimeError("memory-pair point authority differs")
                    return freeze, workers, point_gate

                v73.alg.r0mod._validate_authority = validate_memory_pair_authority
            original_state = runtime._state_document

            def state_document(*args: Any, **kwargs: Any) -> dict[str, Any]:
                value = original_state(*args, **kwargs)
                value.update({
                    "all_four_scenes_started_in_same_formal_launch": False,
                    "all_four_scenes_declared_in_same_formal_campaign": True,
                    "per_scene_b_completion_release": True,
                    "completed_scene_early_release_allowed": True,
                    "scene_release_policy": "after_same_scene_algorithm_b_complete",
                    "external_storage_co_residency_override": {
                        "waiver_id": external_inventory.WAIVER_ID,
                        "container": external_inventory.WAIVED_CONTAINER,
                        "cpu_overlap": False,
                        "role": "inventory_only",
                        "external_process_control_or_stop_performed": False,
                    },
                    "memory_pair_controller": memory_pair,
                })
                return value

            runtime._state_document = state_document
            try:
                return int(original_runtime_main())
            finally:
                runtime._state_document = original_state
                runtime.SCENES = original_scenes
                runtime.INDEPENDENT_SCENE_EXECUTION = original_independent
                runtime.predecessor.SCENES = original_predecessor_scenes
                v73.alg.r0mod._validate_authority = original_validate_authority

        runtime.main = runtime_main_with_final_client_binding
        try:
            return int(original_v74_main())
        finally:
            runtime.main = original_runtime_main

    v74.main = staged_v74_main
    try:
        return int(predecessor.main())
    finally:
        v74.main = original_v74_main


if __name__ == "__main__":
    raise SystemExit(main())
