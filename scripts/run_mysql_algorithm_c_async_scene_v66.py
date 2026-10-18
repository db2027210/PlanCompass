#!/usr/bin/env python3
"""Run strict-QPS Algorithm C with Top-4 boundary integrity v66."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v10 as gate_v10
from scripts import mysql_outer_qps_strict_progress_v1 as qps_policy
from scripts import mysql_outer_rejection_cycle_v1 as rejection_cycle
from scripts import preflight_mysql_four_scene_v9 as preflight_v9
from scripts import build_mysql_algorithm_c_outer_checkpoint_v4 as checkpointmod
from scripts import run_mysql_algorithm_c_async_scene_v52 as top4
from scripts import run_mysql_algorithm_c_async_scene_v63 as predecessor


alg = predecessor.alg
CONTRACT_ROOT = (
    ROOT / "config/formal/frozen_mysql_four_scene_algorithm_c_qps_20260826_v18"
)
ASYNC_CONTRACT = CONTRACT_ROOT / "algorithm_c_async_contract.json"
OPTIMIZATION_CONTRACT = CONTRACT_ROOT / "outer_runtime_optimization.json"


def validate_contract() -> dict[str, Any]:
    contract = alg.load(ASYNC_CONTRACT)
    if (
        contract.get("release_version") != "v66"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("outer_acceptance_policy") != qps_policy.policy_contract()
        or contract.get("outer_rejection_cycle_policy")
        != rejection_cycle.policy_contract()
        or contract.get("candidate_batches") != [[1, 2], [3, 4], [5]]
        or contract.get("physical_replay_parallelism_per_scene") != 2
        or contract.get("maximum_concurrent_mysql_workers") != 8
        or contract.get("four_scene_wave_barrier") is not False
        or contract.get("sibling_scene_wait") is not False
        or contract.get("lease_mode") != "none"
        or contract.get("post_search_stability_confirmation") is not False
        or contract.get("inactive_sealed_container_policy", {}).get(
            "inactive_without_live_workload_owner_is_not_co_resident"
        ) is not True
        or contract.get("disjoint_storage_journal_policy", {}).get(
            "proven_disk1_journal_does_not_block_disk2"
        ) is not True
        or contract.get("xp_top4_boundary_integrity", {}).get(
            "active_and_range_keys_must_match_frozen_top4"
        ) is not True
    ):
        raise RuntimeError("v66 Algorithm C recovery contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        if not isinstance(binding, Mapping):
            raise RuntimeError("v66 Algorithm C source binding is not an object")
        path = ROOT / str(binding.get("path") or "")
        if (
            not path.is_file() or path.is_symlink()
            or alg.sha(path) != str(binding.get("sha256") or "")
        ):
            raise RuntimeError(f"v66 Algorithm C source binding differs: {path}")
    return contract


_ORIGINAL_CHECKPOINT_VERIFY = checkpointmod.verify_checkpoint


def verify_top4_checkpoint(path: Path) -> dict[str, Any]:
    checkpoint = _ORIGINAL_CHECKPOINT_VERIFY(path)
    boundary = checkpoint.get("independent_scene_boundary") or {}
    scene = str(boundary.get("scene") or "")
    if scene in top4.EXPECTED_ACTIVE:
        reconstructed = checkpoint.get("reconstructed_state") or {}
        active = list((reconstructed.get("active_by_scene") or {}).get(scene) or [])
        ranges = (reconstructed.get("ranges_by_scene") or {}).get(scene) or {}
        expected = list(top4.EXPECTED_ACTIVE[scene])
        if active != expected or set(ranges) != set(expected):
            raise RuntimeError(f"{scene} boundary is not frozen Top-4")
    return checkpoint


def main() -> int:
    v62 = predecessor.predecessor
    v62.gate_v8 = gate_v10
    v62.preflight_v8 = preflight_v9
    predecessor.ASYNC_CONTRACT = ASYNC_CONTRACT
    predecessor.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    predecessor.validate_contract = validate_contract
    original = checkpointmod.verify_checkpoint
    checkpointmod.verify_checkpoint = verify_top4_checkpoint
    try:
        return predecessor.main()
    finally:
        checkpointmod.verify_checkpoint = original


if __name__ == "__main__":
    raise SystemExit(main())
