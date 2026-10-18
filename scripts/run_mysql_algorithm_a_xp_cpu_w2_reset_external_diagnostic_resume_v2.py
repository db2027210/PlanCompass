#!/usr/bin/env python3
"""Resume Algorithm A XP/CPU after recovering the stopped w2 data plane."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_algorithm_a_all_scene_parallel_policy_v1 as parallel
from scripts import mysql_external_workload_diagnostic_gate_v1 as diagnostic_gate
from scripts import run_mysql_algorithm_a_async_scene_v3 as algorithm_a
from scripts import run_mysql_algorithm_a_partial_round_codefix_resume_v2 as resume


SCENE = "hybench_xp_cpu"
PREFLIGHT_SOURCE = ROOT / "scripts/preflight_mysql_four_scene_external_containers_diagnostic_only_v2.py"
TRANSITION_CONTRACT = ROOT / "config/formal/frozen_mysql_algorithm_a_xp_cpu_w2_reset_recovery_20260824_v2/transition_contract.json"
_ORIGINAL_VALIDATE = resume.base.validate_fresh_authority
_ORIGINAL_FRESH_STATE = resume.base.fresh_state
_ACTIVE_CONTRACT: dict[str, Any] | None = None


def sha(path: Path) -> str:
    return algorithm_a.alg.sha(path)


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def validate_transition_contract(predecessor_path: Path) -> dict[str, Any]:
    contract = load(TRANSITION_CONTRACT)
    predecessor = contract.get("predecessor") or {}
    recovery = contract.get("recovery_readback") or {}
    frozen_predecessor = ROOT / str(predecessor.get("path") or "")
    recovery_path = ROOT / str(recovery.get("path") or "")
    if (
        contract.get("schema_version") != "mysql_algorithm_a_xp_cpu_w2_reset_recovery_contract_v2"
        or contract.get("release_version") != "v2"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("algorithm") != "A"
        or contract.get("scene") != SCENE
        or int(contract.get("physical_replay_parallelism") or 0) != 2
        or contract.get("external_workloads_diagnostic_only") is not True
        or contract.get("external_workload_wait_required") is not False
        or contract.get("cached_completed_candidate_prefix_reused") is not True
        or contract.get("completed_candidate_measurements_replayed") is not False
        or contract.get("failed_wave_credited") is not False
        or contract.get("new_physical_work_starts_after_rank") != 88
        or contract.get("native_xp_workload_unchanged") is not True
        or contract.get("mysql_fixed_cpu_affinity_retained") is not True
        or contract.get("numa_exclusive_scheduler_lock_required") is not False
        or frozen_predecessor.resolve() != predecessor_path.resolve()
        or frozen_predecessor.is_symlink() or not frozen_predecessor.is_file()
        or sha(frozen_predecessor) != predecessor.get("sha256")
        or recovery_path.is_symlink() or not recovery_path.is_file()
        or sha(recovery_path) != recovery.get("sha256")
    ):
        raise RuntimeError("XP/CPU w2 reset recovery contract differs")
    failed, readback = load(frozen_predecessor), load(recovery_path)
    checkpoint = failed.get("round_checkpoint") or {}
    if (
        failed.get("status") != "FAILED_CLOSED"
        or failed.get("stage") != "FAILED"
        or int(failed.get("accepted") or 0) != 2
        or int(failed.get("round_index") or 0) != 3
        or list(checkpoint.get("attempted_ranks") or []) != list(range(1, 89))
        or "--port=65332" not in str(failed.get("error") or "")
        or readback.get("status") != "PASS"
        or readback.get("failed_worker") != "xp_cpu_w2"
        or readback.get("cached_attempted_ranks") != list(range(1, 89))
        or readback.get("failed_wave_credited") is not False
        or readback.get("new_physical_work_starts_after_rank") != 88
        or any(
            (row or {}).get("running") is not True
            or ((row or {}).get("mysql_probe") or {}).get("select_one") != 1
            or ((row or {}).get("mysql_probe") or {}).get("other_non_system_sessions") != 0
            for row in (readback.get("workers") or {}).values()
        )
    ):
        raise RuntimeError("XP/CPU w2 recovery readback differs")
    for binding in (contract.get("source_bindings") or {}).values():
        source = ROOT / str(binding.get("path") or "")
        if source.is_symlink() or not source.is_file() or sha(source) != binding.get("sha256"):
            raise RuntimeError(f"XP/CPU w2 recovery source differs: {source}")
    return contract


def validate_fresh_authority(**kwargs: Any):
    previous_source = resume.base.alg.r0mod.PREFLIGHT_SOURCE
    resume.base.alg.r0mod.PREFLIGHT_SOURCE = PREFLIGHT_SOURCE
    try:
        result = _ORIGINAL_VALIDATE(**kwargs)
    finally:
        resume.base.alg.r0mod.PREFLIGHT_SOURCE = previous_source
    parallel.validate_contract()
    resume.base.runtime.coordination.rolling_quiet_gate = diagnostic_gate.rolling_quiet_gate
    return result


def fresh_state(**kwargs: Any) -> dict[str, Any]:
    if _ACTIVE_CONTRACT is None:
        raise RuntimeError("XP/CPU w2 recovery contract was not selected")
    state = parallel.annotate_state(_ORIGINAL_FRESH_STATE(**kwargs))
    state["all_scene_parallel_execution"]["rolling_dynamic_noninterference_gate_required"] = False
    state["all_scene_parallel_execution"]["external_workload_inventory_diagnostic_only"] = True
    state["external_workload_policy"] = {
        "transition_contract_path": str(TRANSITION_CONTRACT),
        "transition_contract_sha256": sha(TRANSITION_CONTRACT),
        "external_workloads_diagnostic_only": True,
        "external_workload_wait_required": False,
        "external_process_control_or_stop_prohibited": True,
        "mysql_fixed_cpu_affinity_retained": True,
        "numa_exclusive_scheduler_lock_required": False,
        "all_four_scenes_concurrent": True,
    }
    state["xp_cpu_w2_reset_partial_resume"] = {
        "cached_completed_candidate_prefix_reused": True,
        "completed_candidate_measurements_replayed": False,
        "failed_wave_credited": False,
        "new_physical_work_starts_after_rank": 88,
        "native_xp_workload_unchanged": True,
    }
    return state


def main() -> int:
    global _ACTIVE_CONTRACT
    if "--scene" not in sys.argv or "--predecessor-state" not in sys.argv:
        raise RuntimeError("scene and predecessor state are required")
    scene = sys.argv[sys.argv.index("--scene") + 1]
    predecessor = Path(sys.argv[sys.argv.index("--predecessor-state") + 1]).resolve()
    if scene != SCENE:
        raise RuntimeError("w2 reset continuation is XP/CPU-only")
    _ACTIVE_CONTRACT = validate_transition_contract(predecessor)
    parallel.validate_contract()
    resume.base.validate_fresh_authority = validate_fresh_authority
    resume.base.fresh_state = fresh_state
    resume.base.runtime.coordination.rolling_quiet_gate = diagnostic_gate.rolling_quiet_gate
    return resume.main()


if __name__ == "__main__":
    raise SystemExit(main())
