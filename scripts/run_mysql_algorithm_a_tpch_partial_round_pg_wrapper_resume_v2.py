#!/usr/bin/env python3
"""Resume TPC-H after inventory/evaluation PG-wrapper race repair."""

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
from scripts import run_mysql_algorithm_a_partial_round_codefix_resume_v1 as resume


R0_POINT_PREFLIGHT = ROOT / "scripts/preflight_mysql_four_scene_v3_pg_wrapper_v2.py"
TRANSITION_CONTRACT = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_a_tpch_pg_wrapper_resume_20260823_v2"
    / "transition_contract.json"
)
_ORIGINAL_VALIDATE = resume.base.validate_fresh_authority
_ORIGINAL_FRESH_STATE = resume.base.fresh_state


def sha(path: Path) -> str:
    return pg_wrapper.sha(path)


def validate_transition_contract() -> dict[str, Any]:
    if TRANSITION_CONTRACT.is_symlink() or not TRANSITION_CONTRACT.is_file():
        raise RuntimeError("TPC-H PG-wrapper snapshot transition contract is absent")
    value = json.loads(TRANSITION_CONTRACT.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError("TPC-H PG-wrapper snapshot transition contract is invalid")
    contract = dict(value)
    predecessor = contract.get("predecessor") or {}
    predecessor_path = ROOT / str(predecessor.get("path") or "")
    if (
        contract.get("schema_version")
        != "mysql_algorithm_a_tpch_pg_wrapper_partial_resume_contract_v2"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("scene") != "tpch_sf4_memory"
        or int(contract.get("physical_replay_parallelism") or 0) != 2
        or contract.get("cached_valid_candidate_prefix_reused") is not True
        or contract.get("completed_candidate_measurements_replayed") is not False
        or contract.get("inventory_time_wrapper_proof_required") is not True
        or contract.get("cpu_pinning_retained") is not True
        or contract.get("numa_exclusive_scheduler_lock_required") is not False
        or contract.get("postgresql_process_control_or_stop_prohibited") is not True
        or contract.get("rolling_dynamic_noninterference_gate_retained") is not True
        or predecessor_path.is_symlink()
        or not predecessor_path.is_file()
        or sha(predecessor_path) != predecessor.get("sha256")
    ):
        raise RuntimeError("TPC-H PG-wrapper snapshot transition contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        path = ROOT / str(binding.get("path") or "")
        if path.is_symlink() or not path.is_file() or sha(path) != binding.get("sha256"):
            raise RuntimeError(f"TPC-H PG-wrapper snapshot source differs: {path}")
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
    state = parallel.annotate_state(_ORIGINAL_FRESH_STATE(**kwargs))
    state["tpch_pg_capture_wrapper_partial_resume"] = {
        "transition_contract_path": str(TRANSITION_CONTRACT),
        "transition_contract_sha256": sha(TRANSITION_CONTRACT),
        "cached_valid_candidate_prefix_reused": True,
        "completed_candidate_measurements_replayed": False,
        "inventory_time_wrapper_proof_required": True,
        "r0_point_noninterference_gate_retained": True,
        "rolling_dynamic_noninterference_gate_retained": True,
        "postgresql_process_control_or_stop_prohibited": True,
        "numa_exclusive_scheduler_lock_required": False,
    }
    return state


def main() -> int:
    transition = validate_transition_contract()
    if (
        "--scene" not in sys.argv
        or sys.argv[sys.argv.index("--scene") + 1] != transition["scene"]
    ):
        raise RuntimeError("TPC-H PG-wrapper snapshot continuation scene differs")
    parallel.validate_contract()
    pg_wrapper.load_contract()
    resume.base.validate_fresh_authority = validate_fresh_authority
    resume.base.fresh_state = fresh_state
    pg_wrapper.install()
    return resume.main()


if __name__ == "__main__":
    raise SystemExit(main())
