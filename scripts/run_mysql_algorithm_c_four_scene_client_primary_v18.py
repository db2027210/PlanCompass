#!/usr/bin/env python3
"""Algorithm C v109 controller with Docker-API-free formal gates."""

from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_c_external_storage_inventory_only_v3 as kernel_preflight
from scripts import mysql_cross_project_wave_coordination_v27 as kernel_gate
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v9 as gate_binding
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v16 as predecessor
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v44 as runtime


CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_pg_latest_outer_20260905_v109/"
    "algorithm_c_pg_latest_outer_contract.json"
)
base = predecessor.base
base.latest_runtime = runtime
base.CONTRACT = CONTRACT


def validate_contract() -> dict[str, Any]:
    contract = runtime.alg.load(CONTRACT)
    flow = dict(contract.get("outer_flow") or {})
    recovery = dict(contract.get("independent_scene_recovery") or {})
    boundary = dict(contract.get("algorithm_a_independent_check_boundary") or {})
    kernel = dict(contract.get("kernel_gate_recovery") or {})
    if (
        contract.get("release_version") != "v109-kernel-gate-recovery"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or flow.get("local_real_replay_limit") != 10
        or flow.get("global_coverage_real_replay_limit") != 10
        or flow.get("strict_higher_qps_acceptance") is not True
        or flow.get("coverage_after_every_exhausted_local_round") is not True
        or flow.get("two_rejections_before_incumbent_shrink") is not True
        or recovery.get("standalone_relational_timeout_is_candidate_local") is not True
        or recovery.get("valid_sibling_retained") is not True
        or recovery.get("completed_outer_history_preserved") is not True
        or recovery.get("incomplete_outer_credit") != 0
        or recovery.get("resume_replay_archive_preserved") is not True
        or boundary.get("algorithm_a_feedback_to_c") is not False
        or kernel.get("docker_api_used") is not False
        or kernel.get("prestart_and_per_wave_gate_both_replaced") is not True
        or kernel.get("maximum_wait_seconds") != 60
    ):
        raise RuntimeError("Algorithm C v109 recovery contract differs")
    pg = dict(contract.get("postgresql_reference") or {})
    pg_path = Path(str(pg.get("path") or "")).resolve()
    if (
        not pg_path.is_file() or pg_path.is_symlink()
        or runtime.alg.sha(pg_path) != pg.get("sha256")
    ):
        raise RuntimeError("Algorithm C v109 PostgreSQL reference differs")
    for name, raw in dict(contract.get("source_bindings") or {}).items():
        row = dict(raw or {})
        path = (ROOT / str(row.get("path") or "")).resolve()
        if (
            not path.is_file() or path.is_symlink()
            or runtime.alg.sha(path) != row.get("sha256")
        ):
            raise RuntimeError(f"Algorithm C v109 source differs: {name}: {path}")
    return contract


base.validate_contract = validate_contract


def main() -> int:
    old_gate = gate_binding.gate_v25
    old_preflight = base.external_inventory_v2
    old_no_lease = runtime.NO_LEASE_RUNTIME
    old_independent = runtime.INDEPENDENT_SCENE_EXECUTION
    gate_binding.gate_v25 = kernel_gate
    base.external_inventory_v2 = kernel_preflight
    runtime.NO_LEASE_RUNTIME = True
    runtime.INDEPENDENT_SCENE_EXECUTION = True
    try:
        return int(base.main())
    finally:
        runtime.INDEPENDENT_SCENE_EXECUTION = old_independent
        runtime.NO_LEASE_RUNTIME = old_no_lease
        base.external_inventory_v2 = old_preflight
        gate_binding.gate_v25 = old_gate


if __name__ == "__main__":
    raise SystemExit(main())
