#!/usr/bin/env python3
"""Run Top4-B without a cross-scene disk-wide post-reset quiet barrier."""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_outer_qps_strict_progress_v1 as qps_policy
from scripts import mysql_outer_rejection_cycle_v2 as rejection_cycle
from scripts import run_mysql_four_scene_algorithm_b_scene_independent_measurement_v5 as single_measurement
from scripts import run_mysql_algorithm_b_xp_top4_two_worker_v1 as predecessor


CONTRACT_ROOT = ROOT / "config/formal/frozen_mysql_four_scene_top4_algorithm_b_20260826_v47"
CONTRACT = CONTRACT_ROOT / "algorithm_b_top4_contract.json"
COORDINATION = CONTRACT_ROOT / "coordination_contract.json"
TABLES = {
    "job_memory": "T.job_memory.operator_physics_v1.json",
    "tpch_sf4_memory": "T.tpch_memory.operator_physics_v1.json",
    "hybench_xp_cpu": "T.hybench_xp_cpu.top4.operator_physics_v1.json",
    "hybench_xp_io": "T.hybench_xp_io.top4.operator_physics_v1.json",
}


def validate_contract(alg: Any) -> dict[str, Any]:
    contract = alg.load(CONTRACT)
    if (
        contract.get("schema_version")
        != "mysql_algorithm_b_four_scene_top4_two_worker_contract_v1"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("candidate_batches") != [[1, 2], [3, 4], [5]]
        or contract.get("physical_replay_parallelism") != 2
        or contract.get("outer_acceptance_policy") != qps_policy.policy_contract()
        or contract.get("outer_rejection_cycle_policy")
        != rejection_cycle.policy_contract()
        or contract.get("four_scene_barrier") is not False
        or contract.get("sibling_scene_wait") is not False
        or contract.get("lease_mode") != "none"
        or contract.get("whole_device_post_reset_quiet_gate") is not False
        or contract.get("cross_scene_completion_dependency") is not False
    ):
        raise RuntimeError("four-scene Top4-B two-worker contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        if not isinstance(binding, Mapping):
            raise RuntimeError("four-scene Top4-B binding is not an object")
        path = ROOT / str(binding.get("path") or "")
        if not path.is_file() or path.is_symlink() or alg.sha(path) != binding.get("sha256"):
            raise RuntimeError(f"four-scene Top4-B binding differs: {path}")
    return contract


def install_runtime() -> Any:
    core = predecessor.install_runtime()
    impl = core._impl
    alg = impl["alg"]
    runtime_globals = core._implementation_main.__globals__
    loaded_rejection_cycle = runtime_globals.get("rejection_cycle")
    loaded_policy = getattr(loaded_rejection_cycle, "POLICY_ID", None)
    if loaded_policy not in {
        "mysql_bc_qps_strict_two_reject_incumbent_shrink_v1",
        rejection_cycle.POLICY_ID,
    }:
        raise RuntimeError("Top4-B runtime rejection-cycle binding differs")
    runtime_globals["rejection_cycle"] = rejection_cycle
    impl["rejection_cycle"] = rejection_cycle
    runtime_globals["single_measurement"] = single_measurement
    impl["single_measurement"] = single_measurement
    original_configure_runtime = impl["configure_runtime"]

    def configure_runtime() -> Mapping[str, Any]:
        contract = original_configure_runtime()
        # The predecessor installs a whole-nvme2 write-quiescence gate after
        # each XP reset.  Full-parallel execution guarantees sibling scenes
        # continue writing the same device, so that gate creates a prohibited
        # cross-scene completion dependency.  The scene-scoped reset already
        # proves both owned containers, data roots, limits, and credentials.
        impl["runtime"].alg.reset_xp = predecessor.scene_scoped_reset
        alg.reset_xp = predecessor.scene_scoped_reset
        return contract
    impl["TOP4_TABLES"] = copy.deepcopy(TABLES)
    impl["CONTRACT"] = CONTRACT
    impl["COORDINATION_CONTRACT"] = COORDINATION

    original_state_document = impl["state_document"]

    def state_document(run_id: str) -> dict[str, Any]:
        document = original_state_document(run_id)
        release = document.setdefault("xp_top4_parameter_release", {})
        release["contract_path"] = str(CONTRACT)
        release["four_scene_top4_successor"] = True
        document["outer_rejection_cycle_policy"] = (
            rejection_cycle.policy_contract()
        )
        document["mysql_float_boundary_tolerance_release"] = {
            "policy_id": rejection_cycle.POLICY_ID,
            "source_path": str(ROOT / "scripts/mysql_outer_rejection_cycle_v2.py"),
            "source_sha256": alg.sha(
                ROOT / "scripts/mysql_outer_rejection_cycle_v2.py"
            ),
            "semantic_out_of_range_remains_fail_closed": True,
        }
        document["scene_scoped_relational_measurement_release"] = {
            "source_path": str(
                ROOT / "scripts/run_mysql_four_scene_algorithm_b_scene_independent_measurement_v5.py"
            ),
            "source_sha256": alg.sha(
                ROOT / "scripts/run_mysql_four_scene_algorithm_b_scene_independent_measurement_v5.py"
            ),
            "counterpart_workload_started": False,
            "counterpart_database_connection_attempted": False,
        }
        document["scene_scoped_xp_reset_release"] = {
            "scene_reset_source_path": str(
                ROOT / "scripts/run_mysql_algorithm_b_xp_top4_two_worker_v1.py"
            ),
            "scene_reset_source_sha256": alg.sha(
                ROOT / "scripts/run_mysql_algorithm_b_xp_top4_two_worker_v1.py"
            ),
            "whole_device_post_reset_quiet_gate": False,
            "shared_nvme_mutual_exclusion": False,
            "cross_scene_completion_dependency": False,
            "owned_container_readback_required": True,
        }
        return document

    def validate_release(scene: str) -> dict[str, Any]:
        contract = validate_contract(alg)
        expected = list(alg.load(impl["TOP4_ROOT"] / TABLES[scene])[
            "parameter_space"
        ]["active_params"])
        if (contract.get("active_parameters_by_scene") or {}).get(scene) != expected:
            raise RuntimeError("four-scene Top4-B active parameter set differs")
        return contract

    impl["state_document"] = state_document
    impl["validate_release"] = validate_release
    impl["configure_runtime"] = configure_runtime
    return core


def main() -> int:
    core = install_runtime()
    return int(core._implementation_main())


if __name__ == "__main__":
    raise SystemExit(main())
