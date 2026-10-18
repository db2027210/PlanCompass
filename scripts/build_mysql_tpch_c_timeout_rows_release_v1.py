#!/usr/bin/env python3
"""Freeze the normalized TPC-H C timeout-row successor."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
OUTPUT = ROOT / "config/formal/frozen_mysql_tpch_c_timeout_rows_20260830_v3"

from scripts import mysql_tpch_client_qps_measurement_v2 as metric
from scripts import mysql_tpch_worker_qps_normalization_v1 as normalization


SOURCES = {
    "builder": Path(__file__).resolve(),
    "controller": ROOT / "scripts/run_mysql_tpch_algorithm_c_client_qps_normalized_v3.py",
    "metric_adapter": ROOT / "scripts/mysql_tpch_client_qps_measurement_v2.py",
    "normalization": ROOT / "scripts/mysql_tpch_worker_qps_normalization_v1.py",
    "client_qps_predecessor": ROOT / "scripts/run_mysql_tpch_algorithm_c_client_qps_fresh_v1.py",
    "normalized_predecessor": ROOT / "scripts/run_mysql_tpch_algorithm_c_client_qps_normalized_v2.py",
    "algorithm_c_runtime": ROOT / "scripts/run_mysql_four_scene_algorithm_c_boundary_resume_v35.py",
    "strict_progress": ROOT / "scripts/mysql_outer_qps_strict_progress_v1.py",
    "two_reject_shrink": ROOT / "scripts/mysql_outer_rejection_cycle_v2.py",
    "top4_table": ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_c_20260825_v1/T.tpch_memory.operator_physics_v1.json",
    "resource_freeze": ROOT / "config/formal/frozen_mysql_tpch_bc_disk2_two_worker_20260830_v1/four_scene_effective_resource_successor.json",
    "runtime_preflight": ROOT / "history/mysql_four_scene_20260809/v1115_tpch_bc_disk2_two_worker_live_authority_20260830/transition_preflight.json",
    "prepared_binding": ROOT / "history/mysql_four_scene_20260809/v1115_tpch_bc_disk2_two_worker_live_authority_20260830/prepared_binding.json",
    "benchmark_accounts": ROOT / "history/mysql_four_scene_20260809/v1115_tpch_bc_disk2_two_worker_live_authority_20260830/benchmark_accounts_binding.json",
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def main() -> int:
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise RuntimeError(f"fresh C timeout-row release required: {OUTPUT}")
    for path in SOURCES.values():
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"C timeout-row source absent: {path}")
    contract = {
        "schema_version": "mysql_tpch_algorithm_c_timeout_rows_v3",
        "status": "FROZEN_RUNTIME_VALIDATED",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "scene": metric.SCENE,
        "active_parameters": [
            "io_block_read_cost", "memory_block_read_cost",
            "row_evaluate_cost", "disk_temptable_row_cost",
        ],
        "qps_metric_contract": metric.contract(),
        "normalization_schema_version": normalization.SCHEMA_VERSION,
        "fresh_baseline_capture_required": True,
        "coverage_admission_identity": (
            "parameter_vector_with_actual_plan_preserved_for_audit"
        ),
        "candidate_batches": [[1, 2], [3, 4], [5]],
        "physical_replay_parallelism": 2,
        "failed_rows_receive_acceptance_credit": False,
        "two_rejected_outer_rounds_force_incumbent_centered_shrink": True,
        "convergence_max_normalized_width": 0.01,
        "algorithm_a_parameter_or_measurement_read": False,
        "external_algorithm_evidence_used": False,
        "source_bindings": {
            name: {"path": str(path.relative_to(ROOT)), "sha256": sha(path)}
            for name, path in sorted(SOURCES.items())
        },
    }
    atomic(OUTPUT / "algorithm_c_timeout_rows_contract.json", contract)
    print(json.dumps({
        "status": "FROZEN_RUNTIME_VALIDATED",
        "contract": str(OUTPUT / "algorithm_c_timeout_rows_contract.json"),
        "contract_sha256": sha(OUTPUT / "algorithm_c_timeout_rows_contract.json"),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
