#!/usr/bin/env python3
"""Resume XP Algorithm A after an external same-disk SSSP interruption."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_algorithm_a_all_scene_parallel_policy_v1 as parallel
from scripts import mysql_co_resident_pg_isolation_v15 as pg_wrapper
from scripts import run_mysql_algorithm_a_partial_round_codefix_resume_v2 as resume


R0_POINT_PREFLIGHT = ROOT / "scripts/preflight_mysql_four_scene_v3_pg_wrapper_v2.py"
TRANSITION_CONTRACT = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_a_external_sssp_memory_recovery_20260823_v1"
    / "transition_contract.json"
)
_ORIGINAL_VALIDATE = resume.base.validate_fresh_authority
_ORIGINAL_FRESH_STATE = resume.base.fresh_state
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
    contract = load(TRANSITION_CONTRACT)
    row = (contract.get("scenes") or {}).get(scene) or {}
    frozen_predecessor = ROOT / str(row.get("predecessor_state") or "")
    readback = ROOT / str(contract.get("recovery_readback") or "")
    if (
        contract.get("schema_version")
        != "mysql_algorithm_a_external_sssp_memory_recovery_contract_v1"
        or contract.get("release_version") != "v1"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("cause")
        != "external_sssp_same_disk_host_memory_exhaustion"
        or contract.get("concurrent_scene_execution") is not True
        or contract.get("numa_exclusive_scheduler_lock_required") is not False
        or contract.get("rolling_dynamic_noninterference_gate_retained") is not True
        or row.get("physical_replay_parallelism") != 2
        or row.get("cached_completed_candidate_prefix_reused") is not True
        or row.get("interrupted_inflight_wave_credited") is not False
        or frozen_predecessor.resolve() != predecessor_path.resolve()
        or frozen_predecessor.is_symlink()
        or not frozen_predecessor.is_file()
        or sha(frozen_predecessor) != row.get("predecessor_state_sha256")
        or not readback.is_file()
        or readback.is_symlink()
        or sha(readback) != contract.get("recovery_readback_sha256")
    ):
        raise RuntimeError("XP external SSSP transition contract differs")
    readback_document = load(readback)
    if (
        readback_document.get("status") != "PASS"
        or readback_document.get("external_sssp_process_count") != 0
        or readback_document.get("same_disk_external_sssp_absent") is not True
    ):
        raise RuntimeError("XP external SSSP recovery readback is not clean")
    for binding in (contract.get("source_bindings") or {}).values():
        path = ROOT / str(binding.get("path") or "")
        if path.is_symlink() or not path.is_file() or sha(path) != binding.get("sha256"):
            raise RuntimeError(f"XP external SSSP recovery source differs: {path}")
    return contract


def validate_fresh_authority(**kwargs):
    previous_preflight = resume.base.alg.r0mod.PREFLIGHT_SOURCE
    resume.base.alg.r0mod.PREFLIGHT_SOURCE = R0_POINT_PREFLIGHT
    try:
        result = _ORIGINAL_VALIDATE(**kwargs)
    finally:
        resume.base.alg.r0mod.PREFLIGHT_SOURCE = previous_preflight
    parallel.validate_contract()
    pg_wrapper.load_contract()
    pg_wrapper.install()
    return result


def fresh_state(**kwargs):
    if _ACTIVE_SCENE is None or _ACTIVE_CONTRACT is None:
        raise RuntimeError("XP external SSSP recovery scene was not selected")
    state = parallel.annotate_state(_ORIGINAL_FRESH_STATE(**kwargs))
    state["external_sssp_partial_round_resume"] = {
        "transition_contract_path": str(TRANSITION_CONTRACT),
        "transition_contract_sha256": sha(TRANSITION_CONTRACT),
        "scene": _ACTIVE_SCENE,
        "cached_completed_candidate_prefix_reused": True,
        "interrupted_inflight_wave_credited": False,
        "new_physical_measurement_starts_after_cached_prefix": True,
        "native_xp_workload_unchanged": True,
        "numa_exclusive_scheduler_lock_required": False,
        "rolling_dynamic_noninterference_gate_retained": True,
    }
    return state


def main() -> int:
    global _ACTIVE_SCENE, _ACTIVE_CONTRACT
    if "--scene" not in sys.argv or "--predecessor-state" not in sys.argv:
        raise RuntimeError("scene and predecessor state are required")
    scene = sys.argv[sys.argv.index("--scene") + 1]
    predecessor = Path(sys.argv[sys.argv.index("--predecessor-state") + 1]).resolve()
    if scene not in {"hybench_xp_cpu", "hybench_xp_io"}:
        raise RuntimeError("external SSSP XP continuation is XP-only")
    _ACTIVE_SCENE = scene
    _ACTIVE_CONTRACT = validate_transition_contract(scene, predecessor)
    parallel.validate_contract()
    pg_wrapper.load_contract()
    resume.base.validate_fresh_authority = validate_fresh_authority
    resume.base.fresh_state = fresh_state
    pg_wrapper.install()
    return resume.main()


if __name__ == "__main__":
    raise SystemExit(main())
