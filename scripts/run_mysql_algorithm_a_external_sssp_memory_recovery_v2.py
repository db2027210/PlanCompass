#!/usr/bin/env python3
"""Recover JOB/TPC-H Algorithm A after external SSSP host-memory SIGKILL.

The interrupted outer round is discarded.  Complete accepted and rejected
outer history is retained, a fresh two-worker baseline and incumbent replay
establish a new execution-evidence epoch, and the interrupted round is then
repeated from rank 1.  The current asynchronous PG-aligned Algorithm A
controller, all-scene parallel policy, cache adapter and PG inventory wrapper
remain authoritative.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_algorithm_a_all_scene_parallel_policy_v1 as parallel
from scripts import mysql_co_resident_pg_isolation_v15 as pg_wrapper
from scripts import mysql_job_buffer_pool_preconditioned_measurement_v9 as job_measurement
from scripts import run_mysql_algorithm_a_async_scene_v3 as base
from scripts import run_mysql_algorithm_a_reboot_resume_v2 as recovery


R0_POINT_PREFLIGHT = ROOT / "scripts/preflight_mysql_four_scene_v3_pg_wrapper_v2.py"
TRANSITION_CONTRACT = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_a_external_sssp_memory_recovery_20260823_v2"
    / "transition_contract.json"
)
RECOVERY_CONTRACT = "mysql_algorithm_a_external_sssp_memory_sigkill_recovery_v2"
_ORIGINAL_VALIDATE = base.validate_fresh_authority
_ORIGINAL_FRESH_STATE = base.fresh_state
_ORIGINAL_SEARCH_PAYLOAD = recovery.slots.search_state_payload
_ACTIVE_SCENE: str | None = None
_ACTIVE_CONTRACT: dict[str, Any] | None = None


def sha(path: Path) -> str:
    return pg_wrapper.sha(path)


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def validate_transition_contract(scene: str, predecessor_path: Path) -> dict[str, Any]:
    if TRANSITION_CONTRACT.is_symlink() or not TRANSITION_CONTRACT.is_file():
        raise RuntimeError("external SSSP recovery transition contract is absent")
    contract = load(TRANSITION_CONTRACT)
    scene_row = (contract.get("scenes") or {}).get(scene) or {}
    frozen_predecessor = ROOT / str(scene_row.get("predecessor_snapshot") or "")
    readback = ROOT / str(contract.get("recovery_readback") or "")
    if (
        contract.get("schema_version")
        != "mysql_algorithm_a_external_sssp_memory_recovery_contract_v2"
        or contract.get("release_version") != "v2"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("cause")
        != "external_sssp_same_disk_host_memory_exhaustion"
        or contract.get("concurrent_scene_execution") is not True
        or contract.get("discard_interrupted_partial_round") is not True
        or contract.get("preserve_complete_accepted_and_rejected_outer_history") is not True
        or contract.get("fresh_baseline_per_worker") != 1
        or contract.get("incumbent_replay_required") is not True
        or contract.get("numa_exclusive_scheduler_lock_required") is not False
        or contract.get("rolling_dynamic_noninterference_gate_retained") is not True
        or scene_row.get("physical_replay_parallelism") != 2
        or frozen_predecessor.resolve() != predecessor_path.resolve()
        or frozen_predecessor.is_symlink()
        or not frozen_predecessor.is_file()
        or sha(frozen_predecessor) != scene_row.get("predecessor_snapshot_sha256")
        or readback.is_symlink()
        or not readback.is_file()
        or sha(readback) != contract.get("recovery_readback_sha256")
    ):
        raise RuntimeError("external SSSP recovery transition contract differs")
    readback_document = load(readback)
    if (
        readback_document.get("status") != "PASS"
        or readback_document.get("external_sssp_process_count") != 0
        or readback_document.get("same_disk_external_sssp_absent") is not True
        or readback_document.get("all_eight_worker_profiles_read_back") is not True
    ):
        raise RuntimeError("external SSSP recovery readback is not clean")
    for binding in (contract.get("source_bindings") or {}).values():
        path = ROOT / str(binding.get("path") or "")
        if path.is_symlink() or not path.is_file() or sha(path) != binding.get("sha256"):
            raise RuntimeError(f"external SSSP recovery source differs: {path}")
    return contract


def validate_fresh_authority(**kwargs):
    previous_preflight = base.alg.r0mod.PREFLIGHT_SOURCE
    base.alg.r0mod.PREFLIGHT_SOURCE = R0_POINT_PREFLIGHT
    try:
        result = _ORIGINAL_VALIDATE(**kwargs)
    finally:
        base.alg.r0mod.PREFLIGHT_SOURCE = previous_preflight
    parallel.validate_contract()
    pg_wrapper.load_contract()
    pg_wrapper.install()
    if kwargs.get("scene") == "job_memory":
        job_measurement._validate_contract()
        base.runtime.alg.measure_batch = job_measurement.measure_batch
    return result


def fresh_state(**kwargs):
    if _ACTIVE_SCENE is None or _ACTIVE_CONTRACT is None:
        raise RuntimeError("external SSSP recovery scene was not selected")
    state = parallel.annotate_state(_ORIGINAL_FRESH_STATE(**kwargs))
    state["external_sssp_memory_recovery"] = {
        "contract": RECOVERY_CONTRACT,
        "transition_contract_path": str(TRANSITION_CONTRACT),
        "transition_contract_sha256": sha(TRANSITION_CONTRACT),
        "scene": _ACTIVE_SCENE,
        "recovery_readback": _ACTIVE_CONTRACT["recovery_readback"],
        "recovery_readback_sha256": _ACTIVE_CONTRACT["recovery_readback_sha256"],
        "interrupted_partial_round_credited": False,
        "fresh_baseline_per_worker": 1,
        "incumbent_replay_required": True,
        "numa_exclusive_scheduler_lock_required": False,
        "rolling_dynamic_noninterference_gate_retained": True,
    }
    return state


def search_state_payload(state: Mapping[str, Any]) -> dict[str, Any]:
    value = _ORIGINAL_SEARCH_PAYLOAD(state)
    for field in (
        "rejected_rounds",
        "consecutive_rejected_rounds",
        "incumbent_reference_refresh_count",
        "incumbent_reference_source",
        "last_incumbent_reference_refresh",
        "rejected_history",
    ):
        if field not in state:
            raise RuntimeError(f"external SSSP recovery field absent: {field}")
        value[field] = copy.deepcopy(state[field])
    return value


def completed_round(predecessor: Mapping[str, Any]) -> int:
    interrupted = int(predecessor.get("interrupted_round_index") or 0)
    archived = predecessor.get("archived_interrupted_round_checkpoint") or {}
    if interrupted < 1 or int(archived.get("round_index") or 0) != interrupted:
        raise RuntimeError("external SSSP interrupted round identity differs")
    return interrupted - 1


def main() -> int:
    global _ACTIVE_SCENE, _ACTIVE_CONTRACT
    if "--scene" not in sys.argv or "--predecessor-state" not in sys.argv:
        raise RuntimeError("scene and predecessor snapshot are required")
    scene = sys.argv[sys.argv.index("--scene") + 1]
    predecessor = Path(sys.argv[sys.argv.index("--predecessor-state") + 1]).resolve()
    if scene not in {"job_memory", "tpch_sf4_memory"}:
        raise RuntimeError("external SSSP database recovery is JOB/TPC-H only")
    _ACTIVE_SCENE = scene
    _ACTIVE_CONTRACT = validate_transition_contract(scene, predecessor)
    parallel.validate_contract()
    pg_wrapper.load_contract()
    pg_wrapper.install()
    recovery.base = base
    recovery.RECOVERY_CONTRACT = RECOVERY_CONTRACT
    recovery.install_recovery_pg_a_tpch_allowance = lambda: None
    recovery.last_complete_round = completed_round
    recovery.slots.search_state_payload = search_state_payload
    base.validate_fresh_authority = validate_fresh_authority
    base.fresh_state = fresh_state
    if scene == "job_memory":
        job_measurement._validate_contract()
        base.runtime.alg.measure_batch = job_measurement.measure_batch
    return recovery.main()


if __name__ == "__main__":
    raise SystemExit(main())
