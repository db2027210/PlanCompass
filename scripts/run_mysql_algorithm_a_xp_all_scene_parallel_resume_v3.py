#!/usr/bin/env python3
"""Resume a two-worker XP scene with bounded PG wrapper recognition."""

from __future__ import annotations

from pathlib import Path
import json
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_algorithm_a_all_scene_parallel_policy_v1 as parallel
from scripts import mysql_co_resident_pg_isolation_v14 as pg_wrapper
from scripts import run_mysql_algorithm_a_same_cohort_codefix_resume_v1 as resume


_ORIGINAL_VALIDATE = resume.base.validate_fresh_authority
_ORIGINAL_FRESH_STATE = resume.base.fresh_state
R0_POINT_PREFLIGHT = ROOT / "scripts/preflight_mysql_four_scene_v3_pg_wrapper_v1.py"
TRANSITION_CONTRACT = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_a_xp_cpu_r0_wrapper_20260823_v1"
    / "transition_contract.json"
)


def validate_transition_contract() -> dict[str, Any]:
    if TRANSITION_CONTRACT.is_symlink() or not TRANSITION_CONTRACT.is_file():
        raise RuntimeError("XP CPU R0-wrapper transition contract is absent")
    value = json.loads(TRANSITION_CONTRACT.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError("XP CPU R0-wrapper transition contract is invalid")
    contract = dict(value)
    if (
        contract.get("schema_version")
        != "mysql_algorithm_a_xp_cpu_r0_wrapper_transition_v1"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("scene") != "hybench_xp_cpu"
        or int(contract.get("physical_replay_parallelism") or 0) != 2
        or contract.get("cpu_pinning_retained") is not True
        or contract.get("numa_scheduler_lock") is not False
        or contract.get("postgresql_process_control_or_stop_prohibited") is not True
        or contract.get("rolling_dynamic_noninterference_gate_retained") is not True
        or contract.get("resource_recalibration_required") is not False
    ):
        raise RuntimeError("XP CPU R0-wrapper transition contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        path = ROOT / str(binding.get("path") or "")
        if path.is_symlink() or not path.is_file() or pg_wrapper.sha(path) != binding.get("sha256"):
            raise RuntimeError(f"XP CPU R0-wrapper source differs: {path}")
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
    state["pg_capture_wrapper_allowance"] = {
        "contract_path": str(pg_wrapper.CONTRACT),
        "contract_sha256": pg_wrapper.sha(pg_wrapper.CONTRACT),
        "rolling_dynamic_noninterference_gate_retained": True,
        "r0_point_noninterference_gate_retained": True,
        "r0_point_inventory_source": str(R0_POINT_PREFLIGHT),
        "r0_point_inventory_sha256": pg_wrapper.sha(R0_POINT_PREFLIGHT),
        "postgresql_process_control_or_stop_prohibited": True,
        "numa_scheduler_lock": False,
    }
    state["xp_cpu_r0_wrapper_transition"] = {
        "contract_path": str(TRANSITION_CONTRACT),
        "contract_sha256": pg_wrapper.sha(TRANSITION_CONTRACT),
        "cpu_pinning_retained": True,
        "numa_scheduler_lock": False,
        "four_scene_full_parallel_policy_retained": True,
    }
    return state


def main() -> int:
    if "--scene" not in sys.argv:
        raise RuntimeError("XP scene is required")
    scene = sys.argv[sys.argv.index("--scene") + 1]
    if scene not in {"hybench_xp_cpu", "hybench_xp_io"}:
        raise RuntimeError("PG-wrapper successor is XP-only")
    transition = validate_transition_contract()
    if scene != transition["scene"]:
        raise RuntimeError("R0-wrapper transition is authorized only for XP CPU")
    parallel.validate_contract()
    pg_wrapper.load_contract()
    resume.base.validate_fresh_authority = validate_fresh_authority
    resume.base.fresh_state = fresh_state
    pg_wrapper.install()
    return resume.main()


if __name__ == "__main__":
    raise SystemExit(main())
