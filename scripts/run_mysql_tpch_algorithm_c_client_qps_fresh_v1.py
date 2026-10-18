#!/usr/bin/env python3
"""Fresh TPC-H Algorithm C using client-QPS and parameter coverage rescue."""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_outer_qps_strict_progress_v1 as qps_policy
from scripts import mysql_outer_rejection_cycle_v2 as rejection_cycle_v2
from scripts import mysql_cross_project_wave_coordination_v24 as gate_v24
from scripts import mysql_safe_host_pressure_v1 as safe_pressure
from scripts import mysql_tpch_disk2_two_worker_runtime_authority_v1 as runtime_authority
from scripts import preflight_mysql_four_scene_live_v8 as preflight
from scripts import mysql_tpch_client_qps_measurement_v1 as client_qps
from scripts import reset_mysql_xp_data_planes_mixed_four_worker_v4 as reset_v4
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v35 as predecessor


CONTRACT_ROOT = (
    ROOT / "config/formal/frozen_mysql_tpch_bc_client_qps_20260830_v1"
)
CONTRACT = CONTRACT_ROOT / "algorithm_c_tpch_successor_contract.json"
OPTIMIZATION = CONTRACT_ROOT / "algorithm_c_outer_runtime_optimization.json"
FREEZE = (
    ROOT / "config/formal/frozen_mysql_tpch_bc_disk2_two_worker_20260830_v1"
    / "four_scene_effective_resource_successor.json"
)
AUTHORITY_ROOT = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v1115_tpch_bc_disk2_two_worker_live_authority_20260830"
)


def _validate_contract() -> dict[str, Any]:
    value = predecessor.alg.load(CONTRACT)
    if (
        value.get("schema_version")
        != "mysql_tpch_algorithm_c_client_qps_successor_v1"
        or value.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("scene") != client_qps.SCENE
        or value.get("qps_metric_contract") != client_qps.contract()
        or value.get("coverage_admission_identity")
        != "parameter_vector_with_actual_plan_preserved_for_audit"
        or value.get("external_algorithm_evidence_used") is not False
    ):
        raise RuntimeError("TPC-H C client-QPS successor contract differs")
    for binding in (value.get("source_bindings") or {}).values():
        if not isinstance(binding, Mapping):
            raise RuntimeError("TPC-H C successor binding is not an object")
        path = ROOT / str(binding.get("path") or "")
        if (
            not path.is_file() or path.is_symlink()
            or predecessor.alg.sha(path) != binding.get("sha256")
        ):
            raise RuntimeError(f"TPC-H C successor binding differs: {path}")
    return value


def install_runtime() -> None:
    _validate_contract()
    predecessor.rejection_cycle = rejection_cycle_v2
    predecessor.alg.reset = reset_v4
    predecessor.OPTIMIZATION_CONTRACT = OPTIMIZATION
    predecessor.NO_LEASE_RUNTIME = True
    predecessor.coordination.rolling_quiet_gate = gate_v24.rolling_quiet_gate
    predecessor._validate_wave_coordination_contract = lambda: {
        "schema_version": "mysql_tpch_c_no_lease_dynamic_gate_v1",
        "status": "FROZEN_RUNTIME_VALIDATED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "per_wave_quiet_seconds": 15.0,
        "maximum_quiet_wait_seconds": 90.0,
        "maximum_idle_load1": 512.0,
        "shared_nvme_mutual_exclusion": False,
        "numa_exclusive_scheduler_lock": False,
        "whole_round_resource_lease": False,
        "other_scene_completion_required": False,
        "external_process_control_or_stop_performed": False,
    }
    predecessor.pg_outer.outer_semantic_decision = (
        qps_policy.pg_outer_qps_strict_progress
    )

    freeze = predecessor.alg.load(FREEZE)
    target_cpus: set[int] = set()
    for row in freeze["workers"]:
        if row["scene"] != client_qps.SCENE:
            continue
        for field in ("database_cpuset", "client_cpuset"):
            target_cpus.update(predecessor.alg.r0mod._cpus(str(row[field])))
    safe_pressure.configure_target_cpus(target_cpus)
    preflight.host_pressure = safe_pressure.host_pressure
    predecessor.alg.FREEZE = FREEZE
    predecessor.alg.r0mod.FREEZE = FREEZE
    predecessor.alg.r0mod.FREEZE_SCHEMA = str(freeze["schema_version"])
    predecessor.alg.r0mod.PREFLIGHT = AUTHORITY_ROOT / "transition_preflight.json"
    predecessor.alg.r0mod.RUNTIME_PREFLIGHT = predecessor.alg.r0mod.PREFLIGHT
    predecessor.alg.r0mod.PREPARED = AUTHORITY_ROOT / "prepared_binding.json"
    predecessor.alg.r0mod.BENCHMARK_ACCOUNTS = (
        AUTHORITY_ROOT / "benchmark_accounts_binding.json"
    )
    predecessor.alg.r0mod.dynamic_gate = gate_v24
    predecessor.alg.r0mod._validate_authority = lambda: runtime_authority.validate(
        freeze_path=FREEZE, authority_root=AUTHORITY_ROOT,
    )

    base_measure_batch = predecessor.alg.measure_batch
    predecessor.alg.measure_batch = client_qps.wrap_measure_batch(
        base_measure_batch,
        load=predecessor.alg.load,
        atomic=predecessor.alg.atomic,
    )

    base_coverage_rankings = predecessor.global_coverage.candidate_rankings

    def parameter_coverage_rankings(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        rows = base_coverage_rankings(*args, **kwargs)
        result: list[dict[str, Any]] = []
        for raw in rows:
            row = copy.deepcopy(dict(raw))
            actual_plans: dict[str, Any] = {}
            for key in (
                "candidate_physical_plan_sha256",
                "full_plan_sha256",
            ):
                if row.get(key):
                    actual_plans[key] = row.pop(key)
            model = copy.deepcopy(dict(row.get("resource_model") or {}))
            if model.get("candidate_plan_sha256"):
                actual_plans["resource_model_candidate_plan_sha256"] = model.pop(
                    "candidate_plan_sha256"
                )
            row["resource_model"] = model
            row.update({
                "coverage_admission_identity": "parameter_vector",
                "coverage_actual_plan_fingerprints": actual_plans,
                "actual_plan_preserved_for_audit": True,
                "duplicate_estimated_plan_does_not_remove_parameter_probe": True,
            })
            result.append(row)
        return result

    predecessor.global_coverage.candidate_rankings = parameter_coverage_rankings

    base_state_document = predecessor._state_document

    def state_document(**kwargs: Any) -> dict[str, Any]:
        document = base_state_document(**kwargs)
        document.update({
            "controller_source_path": str(Path(__file__).resolve()),
            "controller_source_sha256": predecessor.alg.sha(
                Path(__file__).resolve()
            ),
            "tpch_client_qps_successor_contract_path": str(CONTRACT),
            "tpch_client_qps_successor_contract_sha256": predecessor.alg.sha(
                CONTRACT
            ),
            "qps_metric_contract": copy.deepcopy(client_qps.contract()),
            "coverage_admission_identity": (
                "parameter_vector_with_actual_plan_preserved_for_audit"
            ),
            "algorithm_a_parameter_or_measurement_read": False,
            "freeze_path": str(FREEZE),
            "freeze_sha256": predecessor.alg.sha(FREEZE),
            "runtime_preflight_path": str(
                AUTHORITY_ROOT / "transition_preflight.json"
            ),
            "runtime_preflight_sha256": predecessor.alg.sha(
                AUTHORITY_ROOT / "transition_preflight.json"
            ),
            "prepared_binding_path": str(
                AUTHORITY_ROOT / "prepared_binding.json"
            ),
            "prepared_binding_sha256": predecessor.alg.sha(
                AUTHORITY_ROOT / "prepared_binding.json"
            ),
            "benchmark_accounts_path": str(
                AUTHORITY_ROOT / "benchmark_accounts_binding.json"
            ),
            "benchmark_accounts_sha256": predecessor.alg.sha(
                AUTHORITY_ROOT / "benchmark_accounts_binding.json"
            ),
            "dynamic_gate_source_path": str(Path(gate_v24.__file__).resolve()),
            "dynamic_gate_source_sha256": predecessor.alg.sha(
                Path(gate_v24.__file__).resolve()
            ),
            "host_pressure_source_path": str(
                Path(safe_pressure.__file__).resolve()
            ),
            "host_pressure_source_sha256": predecessor.alg.sha(
                Path(safe_pressure.__file__).resolve()
            ),
            "lease_mode": "none",
            "shared_nvme_mutual_exclusion": False,
            "numa_exclusive_scheduler_lock": False,
            "whole_round_resource_lease": False,
            "other_scene_completion_required": False,
            "target_storage_mounts": ["/disk2"],
            "external_postgresql_storage_mounts": ["/disk1"],
        })
        return document

    predecessor._state_document = state_document


def main() -> int:
    install_runtime()
    return int(predecessor.main())


if __name__ == "__main__":
    raise SystemExit(main())
