#!/usr/bin/env python3
"""Freeze the current-cohort TPC-H Algorithm B fresh rerun."""

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
OUTPUT = ROOT / "config/formal/frozen_mysql_tpch_b_fresh_rerun_20260830_v6"

from scripts import mysql_tpch_client_qps_measurement_v1 as metric
from scripts import mysql_tpch_worker_qps_normalization_v1 as normalization


SOURCES = {
    "builder": Path(__file__).resolve(),
    "controller": ROOT / "scripts/run_mysql_tpch_algorithm_b_client_qps_fresh_rerun_v4.py",
    "controller_predecessor": ROOT / "scripts/run_mysql_tpch_algorithm_b_client_qps_continue_v2.py",
    "algorithm_b_core": ROOT / "scripts/run_mysql_four_scene_algorithm_b_dynamic_support_resume_core_v27.py",
    "two_worker_scheduler": ROOT / "scripts/mysql_algorithm_b_fresh_parallel_221_scheduler_v26.py",
    "grid_optimizer": ROOT.parent / "resource_adaptive/double_loop_grid_optimizer.py",
    "pg_outer_policy": ROOT / "scripts/mysql_algorithm_b_pg_outer_policy_v31.py",
    "strict_progress": ROOT / "scripts/mysql_outer_qps_strict_progress_v1.py",
    "two_reject_shrink": ROOT / "scripts/mysql_outer_rejection_cycle_v2.py",
    "metric_adapter": ROOT / "scripts/mysql_tpch_client_qps_measurement_v1.py",
    "normalization": ROOT / "scripts/mysql_tpch_worker_qps_normalization_v1.py",
    "resource_freeze": ROOT / "config/formal/frozen_mysql_tpch_bc_disk2_two_worker_20260830_v1/four_scene_effective_resource_successor.json",
    "runtime_preflight": ROOT / "history/mysql_four_scene_20260809/v1115_tpch_bc_disk2_two_worker_live_authority_20260830/transition_preflight.json",
    "prepared_binding": ROOT / "history/mysql_four_scene_20260809/v1115_tpch_bc_disk2_two_worker_live_authority_20260830/prepared_binding.json",
    "benchmark_accounts": ROOT / "history/mysql_four_scene_20260809/v1115_tpch_bc_disk2_two_worker_live_authority_20260830/benchmark_accounts_binding.json",
    "fresh_source_design": ROOT / "history/mysql_four_scene_20260809/v1117_to_b_tpch_client_qps_disk2_fresh_20260830_tpch/calibration_design.json",
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
        raise RuntimeError(f"fresh B rerun release required: {OUTPUT}")
    for path in SOURCES.values():
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"B rerun source absent: {path}")
    contract = {
        "schema_version": "mysql_tpch_algorithm_b_fresh_rerun_v1",
        "status": "FROZEN_RUNTIME_VALIDATED",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "scene": metric.SCENE,
        "qps_metric_contract": metric.contract(),
        "normalization_schema_version": normalization.SCHEMA_VERSION,
        "fresh_baseline_capture_required": True,
        "fresh_top15_calibration_required": True,
        "completed_calibration_measurements_reused": 0,
        "accepted_or_rejected_outer_state_reused": False,
        "algorithm_a_used_as_search_prior": False,
        "external_algorithm_evidence_used": False,
        "active_parameters": [
            "io_block_read_cost", "memory_block_read_cost",
            "row_evaluate_cost", "disk_temptable_row_cost",
        ],
        "candidate_batches": [[1, 2], [3, 4], [5]],
        "physical_worker_count": 2,
        "required_query_coverage": "Q1-Q22 exactly once",
        "hard_timeout_seconds": 900,
        "two_rejected_outer_rounds_force_incumbent_centered_shrink": True,
        "convergence_max_normalized_width": 0.01,
        "source_bindings": {
            name: {"path": os.path.relpath(path, ROOT), "sha256": sha(path)}
            for name, path in sorted(SOURCES.items())
        },
    }
    contract_path = OUTPUT / "algorithm_b_fresh_rerun_contract.json"
    atomic(contract_path, contract)
    print(json.dumps({
        "status": "FROZEN_RUNTIME_VALIDATED",
        "contract": str(contract_path),
        "contract_sha256": sha(contract_path),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
