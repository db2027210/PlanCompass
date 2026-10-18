#!/usr/bin/env python3
"""Run v95 C with one-row Top-5-tail TPC-H candidate support."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_algorithm_c_measurement_client_primary_v4 as measurement
from scripts import mysql_c_external_storage_inventory_only_v1 as external_inventory
from scripts import mysql_tpch_client_qps_measurement_v2 as tpch_metric
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v5 as predecessor


CONTRACT_ROOT = ROOT / (
    "config/formal/"
    "frozen_mysql_four_scene_algorithm_c_client_primary_20260904_v95"
)
RELEASE = "v95-memory-pair-tail-client-primary"


def mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} object required")
    return dict(value)


def validate_contract_v95() -> dict[str, Any]:
    alg = (
        predecessor.predecessor.predecessor.predecessor.predecessor.predecessor.alg
    )
    parent_path = CONTRACT_ROOT / "algorithm_c_async_contract.json"
    optimization_path = CONTRACT_ROOT / "outer_runtime_optimization.json"
    successor_path = CONTRACT_ROOT / "global_monotonic_incumbent_contract.json"
    parent = alg.load(parent_path)
    optimization = alg.load(optimization_path)
    successor = alg.load(successor_path)
    release = mapping(successor.get("full_parallel_launch"), "scene release")
    if (
        parent.get("release_version") != RELEASE
        or parent.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or parent.get("formal_scope") != "mysql_only"
        or parent.get("cross_engine_comparison_prohibited") is not True
        or parent.get("outer_acceptance_policy")
        != predecessor.predecessor.predecessor.predecessor.predecessor.predecessor.qps_policy.policy_contract()
        or parent.get("outer_rejection_cycle_policy")
        != predecessor.predecessor.predecessor.predecessor.predecessor.predecessor.rejection_cycle.policy_contract()
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
        or parent.get("mixed_memory_scene_batch_supported") is not True
        or parent.get("single_candidate_tail_batch_supported") is not True
        or optimization.get("release_version") != RELEASE
        or successor.get("release_version") != RELEASE
        or successor.get("parent_contract", {}).get("sha256") != alg.sha(parent_path)
        or successor.get("xp_outer_primary_metric")
        != "native_hybench_xp_client_xap_qps"
        or successor.get("tpch_outer_primary_metric") != tpch_metric.METRIC
        or successor.get("tpch_server_questions_role") != "audit_only"
        or successor.get("mixed_memory_scene_batch_supported") is not True
        or successor.get("single_candidate_tail_batch_supported") is not True
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
        raise RuntimeError("Algorithm C v95 contract differs")
    for document in (parent, successor):
        for name, raw in mapping(
            document.get("source_bindings"), "v95 source bindings",
        ).items():
            row = mapping(raw, f"v95 {name}")
            path = (ROOT / str(row.get("path") or "")).resolve()
            if (
                not path.is_file() or path.is_symlink()
                or alg.sha(path) != str(row.get("sha256") or "")
            ):
                raise RuntimeError(f"Algorithm C v95 source differs: {name}")
    return {"parent": parent, "optimization": optimization, "successor": successor}


def main() -> int:
    old_root = predecessor.CONTRACT_ROOT
    old_validate = predecessor.validate_contract_v94
    old_measurement = predecessor.measurement
    predecessor.CONTRACT_ROOT = CONTRACT_ROOT
    predecessor.validate_contract_v94 = validate_contract_v95
    predecessor.measurement = measurement
    try:
        return int(predecessor.main())
    finally:
        predecessor.measurement = old_measurement
        predecessor.validate_contract_v94 = old_validate
        predecessor.CONTRACT_ROOT = old_root


if __name__ == "__main__":
    raise SystemExit(main())
