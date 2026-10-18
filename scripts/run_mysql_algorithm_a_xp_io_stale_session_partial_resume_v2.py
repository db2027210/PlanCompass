#!/usr/bin/env python3
"""Validated v2 entry for the XP/I/O stale-session partial continuation."""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_algorithm_a_all_scene_parallel_policy_v1 as parallel
from scripts import mysql_co_resident_pg_isolation_v15 as pg_wrapper
from scripts import run_mysql_algorithm_a_partial_round_codefix_resume_v2 as resume
from scripts import run_mysql_algorithm_a_xp_io_stale_session_partial_resume_v1 as v1


SCENE = "hybench_xp_io"
TRANSITION_CONTRACT = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_a_xp_io_stale_session_resume_20260823_v2"
    / "transition_contract.json"
)


def sha(path: Path) -> str:
    return pg_wrapper.sha(path)


def validate_transition_contract(predecessor_path: Path) -> dict[str, Any]:
    if TRANSITION_CONTRACT.is_symlink() or not TRANSITION_CONTRACT.is_file():
        raise RuntimeError("XP/I/O stale-session v2 transition contract is absent")
    contract = v1.load(TRANSITION_CONTRACT)
    predecessor = contract.get("predecessor") or {}
    frozen_predecessor = ROOT / str(predecessor.get("path") or "")
    cleanup = contract.get("stale_session_cleanup_readback") or {}
    cleanup_path = ROOT / str(cleanup.get("path") or "")
    required_true = (
        "cached_attempted_prefix_reused",
        "native_xp_workload_unchanged",
        "cpu_pinning_retained",
        "postgresql_process_control_or_stop_prohibited",
        "rolling_dynamic_noninterference_gate_retained",
        "all_scene_full_parallel_execution_retained",
    )
    if (
        contract.get("schema_version")
        != "mysql_algorithm_a_xp_io_stale_session_partial_resume_contract_v2"
        or contract.get("release_version") != "v2"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("algorithm") != "A"
        or contract.get("scene") != SCENE
        or int(contract.get("physical_replay_parallelism") or 0) != 2
        or any(contract.get(key) is not True for key in required_true)
        or contract.get("completed_candidate_measurements_replayed") is not False
        or contract.get("failed_wave_credited") is not False
        or contract.get("numa_exclusive_scheduler_lock_required") is not False
        or frozen_predecessor.resolve() != predecessor_path.resolve()
        or frozen_predecessor.is_symlink()
        or not frozen_predecessor.is_file()
        or sha(frozen_predecessor) != predecessor.get("sha256")
        or cleanup_path.is_symlink()
        or not cleanup_path.is_file()
        or sha(cleanup_path) != cleanup.get("sha256")
    ):
        raise RuntimeError("XP/I/O stale-session v2 transition contract differs")

    failed = v1.load(frozen_predecessor)
    readback = v1.load(cleanup_path)
    checkpoint = failed.get("round_checkpoint") or {}
    if (
        failed.get("status") != "TECHNICAL_FAILURE_STOPPED_CLEAN"
        or failed.get("stage") != "ALGORITHM_A_INCOMPLETE_WAVE_TECHNICAL"
        or not str(failed.get("error") or "").startswith(v1.ERROR_PREFIX)
        or int(failed.get("accepted") or 0) != int(predecessor.get("accepted") or 0)
        or list(checkpoint.get("attempted_ranks") or [])
        != list(predecessor.get("attempted_ranks") or [])
        or readback.get("status") != "PASS"
        or readback.get("worker_id") != "xp_io_w2"
        or readback.get("active_sessions_before") != 1
        or readback.get("active_sessions_after") != 0
        or readback.get("stale_connection_terminated") is not True
        or readback.get("postgresql_process_control_or_stop_performed") is not False
    ):
        raise RuntimeError("XP/I/O stale-session v2 cleanup evidence differs")
    for binding in (contract.get("source_bindings") or {}).values():
        source = ROOT / str(binding.get("path") or "")
        if source.is_symlink() or not source.is_file() or sha(source) != binding.get("sha256"):
            raise RuntimeError(f"XP/I/O stale-session v2 source differs: {source}")
    return contract


def main() -> int:
    if "--scene" not in sys.argv or "--predecessor-state" not in sys.argv:
        raise RuntimeError("scene and predecessor state are required")
    scene = sys.argv[sys.argv.index("--scene") + 1]
    predecessor = Path(
        sys.argv[sys.argv.index("--predecessor-state") + 1]
    ).resolve()
    if scene != SCENE:
        raise RuntimeError("stale-session v2 continuation is XP/I/O-only")
    contract = validate_transition_contract(predecessor)
    parallel.validate_contract()
    pg_wrapper.load_contract()
    v1.TRANSITION_CONTRACT = TRANSITION_CONTRACT
    v1._ACTIVE_CONTRACT = copy.deepcopy(contract)
    resume.base.validate_fresh_authority = v1.validate_fresh_authority
    resume.base.fresh_state = v1.fresh_state
    pg_wrapper.install()
    return resume.main()


if __name__ == "__main__":
    raise SystemExit(main())
