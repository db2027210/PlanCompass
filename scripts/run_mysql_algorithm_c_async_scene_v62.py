#!/usr/bin/env python3
"""Run four-scene two-worker C with strict-QPS and no-lease dynamic gating."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v8 as gate_v8
from scripts import mysql_algorithm_b_no_lease_measurement_v1 as no_lease
from scripts import mysql_outer_qps_strict_progress_v1 as qps_policy
from scripts import mysql_outer_rejection_cycle_v1 as rejection_cycle
from scripts import preflight_mysql_four_scene_v8 as preflight_v8
from scripts import run_mysql_algorithm_c_async_scene_v61 as predecessor


alg = predecessor.alg
runtime = predecessor.runtime
CONTRACT_ROOT = (
    ROOT / "config/formal/frozen_mysql_four_scene_algorithm_c_qps_20260826_v14"
)
ASYNC_CONTRACT = CONTRACT_ROOT / "algorithm_c_async_contract.json"
OPTIMIZATION_CONTRACT = CONTRACT_ROOT / "outer_runtime_optimization.json"
FREEZE = (
    ROOT / "config/formal/frozen_mysql_four_scene_two_worker_20260826_v19"
    / "four_scene_effective_resource_successor.json"
)


def validate_contract() -> dict[str, Any]:
    contract = alg.load(ASYNC_CONTRACT)
    if (
        contract.get("release_version") != "v62"
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
    ):
        raise RuntimeError("v62 Algorithm C full-parallel contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        if not isinstance(binding, Mapping):
            raise RuntimeError("v62 Algorithm C source binding is not an object")
        path = ROOT / str(binding.get("path") or "")
        if (
            not path.is_file() or path.is_symlink()
            or alg.sha(path) != str(binding.get("sha256") or "")
        ):
            raise RuntimeError(f"v62 Algorithm C source binding differs: {path}")
    return contract


def install() -> None:
    predecessor.ASYNC_CONTRACT = ASYNC_CONTRACT
    predecessor.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    predecessor.validate_contract = validate_contract
    predecessor.install()
    alg.FREEZE = FREEZE
    alg.r0mod.FREEZE = FREEZE
    alg.r0mod.PREFLIGHT_SOURCE = Path(preflight_v8.__file__).resolve()
    runtime.alg.FREEZE = FREEZE
    runtime.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    # Keep compatibility-only constants used by legacy state construction,
    # while removing both the prestart and per-wave resource leases.
    runtime.coordination.rolling_quiet_gate = gate_v8.rolling_quiet_gate

    def independent_start_authority(**kwargs: Any) -> dict[str, Any]:
        scene = str(kwargs["scene"])
        return {
            "status": "PASS",
            "scene": scene,
            "ready_scenes": [scene],
            "wait_seconds": 0.0,
            "per_round_scene_barrier": False,
            "one_time_prestart_authority_barrier": False,
            "sibling_scene_wait": False,
        }

    runtime.scene_start_barrier.wait_ready = independent_start_authority

    original_validate_authority = alg.r0mod._validate_authority

    def validate_authority_without_lease():
        saved_pid = alg.r0mod.COORDINATED_LEASE_HOLDER_PID
        saved_contract = alg.r0mod.COORDINATION_CONTRACT
        alg.r0mod.COORDINATED_LEASE_HOLDER_PID = None
        alg.r0mod.COORDINATION_CONTRACT = None
        try:
            freeze, workers, point_gate = original_validate_authority()
        finally:
            alg.r0mod.COORDINATED_LEASE_HOLDER_PID = saved_pid
            alg.r0mod.COORDINATION_CONTRACT = saved_contract
        point_gate.update({
            "prestart_lease_mode": "none",
            "shared_nvme_mutual_exclusion": False,
            "numa_exclusive_scheduler_lock": False,
            "whole_round_resource_lease": False,
            "sibling_mysql_scene_wait_required": False,
        })

        class NoLeasePointGate(dict):
            def __setitem__(self, key: Any, value: Any) -> None:
                if key == "prestart_lease_mode" and value == "shared_mysql_scene":
                    value = "none"
                super().__setitem__(key, value)

        return freeze, workers, NoLeasePointGate(point_gate)

    alg.r0mod._validate_authority = validate_authority_without_lease

    class NoLeaseToken:
        inode = 0
        acquired_at = "not_acquired"
        wait_seconds = 0.0

    runtime.coordination.acquire_shared_blocking = (
        lambda **_kwargs: NoLeaseToken()
    )
    runtime.coordination.release = lambda _token: 0.0

    installed_measure = runtime._measure_batch_with_fair_coordination

    def measure_without_lease(**kwargs: Any):
        if not (kwargs.get("assignments") or {}):
            return installed_measure(**kwargs)
        adapted = dict(kwargs)
        contract = dict(adapted["contract"])
        contract["maximum_quiet_wait_seconds"] = float(
            contract.get("maximum_quiet_wait_seconds")
            or contract.get("maximum_quiet_wait_while_held_seconds")
            or 180.0
        )
        adapted["contract"] = contract
        no_lease.alg = alg
        no_lease.coordination = gate_v8
        return no_lease.measure_batch(**adapted)

    runtime._measure_batch_with_fair_coordination = measure_without_lease

    original_state_document = runtime._state_document

    def state_document_without_lease(**kwargs: Any):
        document = original_state_document(**kwargs)
        document["lease_mode"] = "none"
        coordination = document.setdefault("cross_project_wave_coordination", {})
        coordination.update({
            "contract": "mysql_dynamic_noninterference_no_lease_v1",
            "phase": "not_started",
            "lease_mode": "none",
            "shared_nvme_mutual_exclusion": False,
            "numa_exclusive_scheduler_lock": False,
            "whole_round_resource_lease": False,
            "other_scene_completion_required": False,
        })
        return document

    runtime._state_document = state_document_without_lease
    runtime.pg_outer.outer_semantic_decision = qps_policy.pg_outer_qps_strict_progress
    runtime.pg_outer.policy_contract = qps_policy.policy_contract


def main() -> int:
    validate_contract()
    install()
    # v61 names its v60 module ``predecessor`` rather than exporting v59
    # directly.  Enter the established v60->v34 controller chain through
    # that explicit predecessor link.
    return predecessor.predecessor.v59.v58.v57.v56.v55.v54.v53.v52.v51.v50.v49.v45.v34.main()


if __name__ == "__main__":
    raise SystemExit(main())
