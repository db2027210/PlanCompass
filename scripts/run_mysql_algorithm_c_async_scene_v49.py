#!/usr/bin/env python3
"""Run one MySQL Algorithm C scene with asynchronous outer progression."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_four_scene_algorithm_c_pg_outer_fresh_v45 as v45
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v27 as v27
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v28 as v28
from scripts import mysql_single_scene_measurement_v1 as single_measurement
from scripts import mysql_async_shared_prestart_authority_v1 as shared_prestart
from scripts import mysql_cross_project_wave_coordination_v2 as scoped_gate
from scripts import mysql_co_resident_pg_isolation_v13 as isolation_v13


runtime = v45.runtime
alg = runtime.alg
FREEZE_ROOT = ROOT / "config/formal/frozen_mysql_async_outer_20260819_v37"
OPTIMIZATION_CONTRACT = FREEZE_ROOT / "algorithm_c_async_optimization_contract.json"
COORDINATION_CONTRACT = (
    ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260819_v65"
    / "coordination_contract.json"
)
ASYNC_CONTRACT = FREEZE_ROOT / "algorithm_c_async_contract.json"
ORIGINAL_V27_STATE_DOCUMENT = v28.ORIGINAL_V27_STATE_DOCUMENT


def async_fresh_state_document(**kwargs: Any) -> dict[str, Any]:
    args = kwargs.get("args")
    if args is None or getattr(args, "fresh_search", False) is not True:
        return ORIGINAL_V27_STATE_DOCUMENT(**kwargs)
    document = v27.ORIGINAL_V22_STATE_DOCUMENT(**kwargs)
    document["post_drain_liveness_refresh_release"] = {
        "release_version": "v27",
        "source_path": str(Path(v27.__file__).resolve()),
        "source_sha256": runtime.alg.sha(Path(v27.__file__).resolve()),
        "reset_uses_final_drain_liveness": True,
        "dead_mysqld_discard_only": True,
        "workload_scoring_top5_binary_shrink_and_convergence_changed": False,
    }
    document["async_fresh_search_checkpoint_compatibility"] = {
        "release_version": "v49-per-scene",
        "legacy_post_target_confirmation_state_ignored": True,
        "post_search_stability_confirmation": False,
        "accepted_state_credit_inherited": False,
    }
    return document


def validate_v65_wave_coordination_contract() -> dict[str, Any]:
    contract = alg.load(COORDINATION_CONTRACT)
    async_contract = alg.load(ASYNC_CONTRACT)
    binding = async_contract.get("coordination") or {}
    policy = contract.get("co_resident_isolated_pg_allowance") or {}
    ports = set(policy.get("allowed_postgres_ports") or [])
    containers = {
        str(row.get("name")): row
        for row in policy.get("containers") or []
        if isinstance(row, Mapping)
    }
    budget = contract.get("wave_lease_budget_v62") or {}
    allowance = policy.get("thin_benchbase_tpch_driver_allowance") or {}
    if (
        contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("release_version") != "v65"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or binding.get("resource_isolation_contract")
        != str(COORDINATION_CONTRACT.relative_to(ROOT))
        or binding.get("resource_isolation_contract_sha256")
        != alg.sha(COORDINATION_CONTRACT)
        or not {59513, 59514, 59521, 59522, 59523, 59524, 59525, 59526}
        <= ports
        or not {
            "ra_fullgrid_generic_mem_20260718_w3",
            "ra_fullgrid_generic_mem_20260718_w4",
        } <= set(containers)
        or float(contract.get("per_wave_quiet_seconds") or 0) != 15.0
        or float(contract.get("maximum_quiet_wait_while_held_seconds") or 0)
        != 60.0
        or int(contract.get("maximum_gate_attempts_per_wave") or 0) != 1
        or float(contract.get("maximum_wave_lease_seconds") or 0) != 1800.0
        or budget.get("formal_workload_maximum_seconds_unchanged") != 900
        or budget.get("per_workload_900_second_fail_close_unchanged") is not True
        or budget.get("release_between_candidate_batches_unchanged") is not True
        or allowance.get("maximum_processes") != 10
        or allowance.get("maximum_rss_bytes_per_process") != 256 * 1024 * 1024
        or float(allowance.get("maximum_total_lifetime_cpu_cores") or 0) != 0.5
        or allowance.get("external_mutation") is not False
        or (policy.get("inactive_sealed_base_container_policy") or {}).get(
            "enabled"
        ) is not True
        or (policy.get("inactive_sealed_base_container_policy") or {}).get(
            "external_mutation"
        ) is not False
    ):
        raise RuntimeError("v65 asynchronous C wave authority differs")
    # Co-resident PostgreSQL control files are mutable inventory, not MySQL
    # launch authority.  Current resource isolation is checked independently.
    return contract


def validate_contract() -> dict[str, Any]:
    if not ASYNC_CONTRACT.is_file() or ASYNC_CONTRACT.is_symlink():
        raise RuntimeError(f"sealed asynchronous C contract absent: {ASYNC_CONTRACT}")
    contract = alg.load(ASYNC_CONTRACT)
    coordination = contract.get("coordination") or {}
    technical = contract.get("technical_candidate_failure_policy") or {}
    if (
        contract.get("release_version") != "v49"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("four_scene_wave_barrier") is not False
        or contract.get("per_scene_outer_state_machine") is not True
        or contract.get("scene_may_advance_immediately") is not True
        or contract.get("candidate_batches") != [[1, 2], [3, 4], [5]]
        or contract.get("post_search_stability_confirmation") is not False
        or coordination.get("prestart_authority_lease") != "shared"
        or coordination.get("sibling_mysql_scene_wait_required") is not False
        or technical.get("postgresql_outer_semantics") is not True
        or technical.get("failed_candidate_excluded_without_outer_state_mutation")
        is not True
        or technical.get("valid_parallel_sibling_retained") is not True
        or technical.get("xp_adaptive_stop_drain_timeout_requires_artifact_proof")
        is not True
    ):
        raise RuntimeError("v49 Algorithm C asynchronous contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        path = ROOT / str(binding["path"])
        if (
            not path.is_file() or path.is_symlink()
            or alg.sha(path) != str(binding["sha256"])
        ):
            raise RuntimeError(f"v49 Algorithm C source binding differs: {path}")
    return contract


def install() -> None:
    # Resource-envelope and isolation calibration remain the sealed v62
    # authority.  The asynchronous successor changes controller scheduling
    # only; its changed sources are bound by ASYNC_CONTRACT.
    isolation_v13.CONTRACT_PATH = COORDINATION_CONTRACT
    isolation_v13.predecessor.CONTRACT_PATH = COORDINATION_CONTRACT
    isolation_v13.predecessor.predecessor.CONTRACT_PATH = COORDINATION_CONTRACT
    isolation_v13.install()
    v45.isolation = isolation_v13
    v45.v24.isolation = isolation_v13
    v45.v22.isolation = isolation_v13
    v45.v22.v18.isolation_v9 = isolation_v13
    v45.v22.validate_wave_coordination_contract = (
        validate_v65_wave_coordination_contract
    )
    runtime.WAVE_COORDINATION_CONTRACT = COORDINATION_CONTRACT
    runtime.coordination.CONTRACT_PATH = COORDINATION_CONTRACT
    v45.v18.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    runtime.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    v45.v34.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    runtime.alg.measure_batch = single_measurement.measure_batch
    runtime.coordination.rolling_quiet_gate = scoped_gate.rolling_quiet_gate
    v28.ORIGINAL_V27_STATE_DOCUMENT = async_fresh_state_document
    shared_prestart.install_shared_lock_verifier(runtime.coordination)


def main() -> int:
    validate_contract()
    install()
    return v45.v34.main()


if __name__ == "__main__":
    raise SystemExit(main())
