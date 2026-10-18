#!/usr/bin/env python3
"""Resume XP/I/O Algorithm A after a proven stale client-session cleanup."""

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
from scripts import run_mysql_algorithm_a_partial_round_codefix_resume_v2 as resume


SCENE = "hybench_xp_io"
R0_POINT_PREFLIGHT = ROOT / "scripts/preflight_mysql_four_scene_v3_pg_wrapper_v2.py"
TRANSITION_CONTRACT = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_a_xp_io_stale_session_resume_20260823_v1"
    / "transition_contract.json"
)
ERROR_PREFIX = (
    "RuntimeError: XP active client sessions were not proven drained before "
    "the 60s bound:"
)
_ORIGINAL_VALIDATE = resume.base.validate_fresh_authority
_ORIGINAL_FRESH_STATE = resume.base.fresh_state
_ACTIVE_CONTRACT: dict[str, Any] | None = None


def sha(path: Path) -> str:
    return pg_wrapper.sha(path)


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def validate_transition_contract(predecessor_path: Path) -> dict[str, Any]:
    if TRANSITION_CONTRACT.is_symlink() or not TRANSITION_CONTRACT.is_file():
        raise RuntimeError("XP/I/O stale-session transition contract is absent")
    contract = load(TRANSITION_CONTRACT)
    predecessor = contract.get("predecessor") or {}
    frozen_predecessor = ROOT / str(predecessor.get("path") or "")
    cleanup = contract.get("stale_session_cleanup_readback") or {}
    cleanup_path = ROOT / str(cleanup.get("path") or "")
    if (
        contract.get("schema_version")
        != "mysql_algorithm_a_xp_io_stale_session_partial_resume_contract_v1"
        or contract.get("release_version") != "v1"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("algorithm") != "A"
        or contract.get("scene") != SCENE
        or int(contract.get("physical_replay_parallelism") or 0) != 2
        or contract.get("cached_attempted_prefix_reused") is not True
        or contract.get("completed_candidate_measurements_replayed") is not False
        or contract.get("failed_wave_credited") is not False
        or contract.get("native_xp_workload_unchanged") is not True
        or contract.get("cpu_pinning_retained") is not True
        or contract.get("numa_exclusive_scheduler_lock_required") is not False
        or contract.get("postgresql_process_control_or_stop_prohibited") is not True
        or contract.get("all_scene_full_parallel_execution_retained") is not True
        or frozen_predecessor.resolve() != predecessor_path.resolve()
        or frozen_predecessor.is_symlink()
        or not frozen_predecessor.is_file()
        or sha(frozen_predecessor) != predecessor.get("sha256")
        or cleanup_path.is_symlink()
        or not cleanup_path.is_file()
        or sha(cleanup_path) != cleanup.get("sha256")
    ):
        raise RuntimeError("XP/I/O stale-session transition contract differs")

    failed = load(frozen_predecessor)
    readback = load(cleanup_path)
    checkpoint = failed.get("round_checkpoint") or {}
    if (
        failed.get("status") != "TECHNICAL_FAILURE_STOPPED_CLEAN"
        or failed.get("stage") != "ALGORITHM_A_INCOMPLETE_WAVE_TECHNICAL"
        or not str(failed.get("error") or "").startswith(ERROR_PREFIX)
        or int(failed.get("accepted") or 0) != int(predecessor.get("accepted") or 0)
        or list(checkpoint.get("attempted_ranks") or [])
        != list(predecessor.get("attempted_ranks") or [])
        or readback.get("status") != "PASS"
        or readback.get("worker_id") != "xp_io_w2"
        or int(readback.get("active_sessions_before") or 0) != 1
        or int(readback.get("active_sessions_after") or -1) != 0
        or readback.get("stale_connection_terminated") is not True
    ):
        raise RuntimeError("XP/I/O stale-session cleanup evidence differs")
    for binding in (contract.get("source_bindings") or {}).values():
        source = ROOT / str(binding.get("path") or "")
        if source.is_symlink() or not source.is_file() or sha(source) != binding.get("sha256"):
            raise RuntimeError(f"XP/I/O stale-session source differs: {source}")
    return contract


def validate_fresh_authority(**kwargs):
    previous_preflight = resume.base.alg.r0mod.PREFLIGHT_SOURCE
    resume.base.alg.r0mod.PREFLIGHT_SOURCE = R0_POINT_PREFLIGHT
    try:
        freeze, workers, authority = _ORIGINAL_VALIDATE(**kwargs)
    finally:
        resume.base.alg.r0mod.PREFLIGHT_SOURCE = previous_preflight
    parallel.validate_contract()
    pg_wrapper.load_contract()
    pg_wrapper.install()
    counts = {
        worker_id: resume.base.alg.xp_active_client_session_count(worker)
        for worker_id, worker in workers.items()
    }
    if not counts or any(value != 0 for value in counts.values()):
        raise RuntimeError(f"XP/I/O sessions reappeared before resume: {counts}")
    authority = copy.deepcopy(authority)
    authority["stale_session_prelaunch_readback"] = {
        "status": "PASS",
        "active_sessions": counts,
    }
    return freeze, workers, authority


def fresh_state(**kwargs):
    if _ACTIVE_CONTRACT is None:
        raise RuntimeError("XP/I/O stale-session contract was not selected")
    state = parallel.annotate_state(_ORIGINAL_FRESH_STATE(**kwargs))
    state["xp_io_stale_session_partial_resume"] = {
        "transition_contract_path": str(TRANSITION_CONTRACT),
        "transition_contract_sha256": sha(TRANSITION_CONTRACT),
        "cached_attempted_prefix_reused": True,
        "completed_candidate_measurements_replayed": False,
        "failed_wave_credited": False,
        "new_physical_work_starts_after_rank": 46,
        "native_xp_workload_unchanged": True,
        "cpu_pinning_retained": True,
        "numa_exclusive_scheduler_lock_required": False,
        "postgresql_process_control_or_stop_prohibited": True,
        "all_scene_full_parallel_execution_retained": True,
    }
    return state


def main() -> int:
    global _ACTIVE_CONTRACT
    if "--scene" not in sys.argv or "--predecessor-state" not in sys.argv:
        raise RuntimeError("scene and predecessor state are required")
    scene = sys.argv[sys.argv.index("--scene") + 1]
    predecessor = Path(
        sys.argv[sys.argv.index("--predecessor-state") + 1]
    ).resolve()
    if scene != SCENE:
        raise RuntimeError("stale-session continuation is XP/I/O-only")
    _ACTIVE_CONTRACT = validate_transition_contract(predecessor)
    parallel.validate_contract()
    pg_wrapper.load_contract()
    resume.base.validate_fresh_authority = validate_fresh_authority
    resume.base.fresh_state = fresh_state
    pg_wrapper.install()
    return resume.main()


if __name__ == "__main__":
    raise SystemExit(main())
