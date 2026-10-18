#!/usr/bin/env python3
"""Freeze the timeout-safe current-cohort TPC-H B rerun."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_tpch_b_fresh_rerun_release_v1 as predecessor
from scripts import mysql_tpch_client_qps_measurement_v2 as metric
from scripts import mysql_tpch_worker_qps_normalization_v1 as normalization


OUTPUT = ROOT / "config/formal/frozen_mysql_tpch_b_fresh_rerun_20260830_v9"
SOURCES = dict(predecessor.SOURCES)
SOURCES.update({
    "builder": Path(__file__).resolve(),
    "controller": ROOT / "scripts/run_mysql_tpch_algorithm_b_client_qps_fresh_rerun_v6.py",
    "launcher": ROOT / "scripts/launch_mysql_tpch_b_current_baseline_fresh_rerun_v1.sh",
    "metric_adapter": ROOT / "scripts/mysql_tpch_client_qps_measurement_v2.py",
    "pg_rejection_core": ROOT / "scripts/run_mysql_algorithm_b_xp_top4_async_v2.py",
    "pg_rejection_loader": ROOT / "scripts/run_mysql_algorithm_b_xp_top4_async_v31.py",
})


def main() -> int:
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise RuntimeError(f"fresh B timeout-safe release required: {OUTPUT}")
    for path in SOURCES.values():
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"B timeout-safe source absent: {path}")
    contract = {
        "schema_version": "mysql_tpch_algorithm_b_fresh_rerun_v2",
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
        "outer_rejected_signature_memory": "persistent_across_acceptance_and_forced_shrink",
        "rejected_signature_added_per_rejected_round": "real_small_loop_best_only",
        "two_rejected_outer_rounds_force_incumbent_centered_shrink": True,
        "convergence_max_normalized_width": 0.01,
        "invalid_qps_rows_are_infeasible_not_controller_fatal": True,
        "source_bindings": {
            name: {
                "path": os.path.relpath(path, ROOT),
                "sha256": predecessor.sha(path),
            }
            for name, path in sorted(SOURCES.items())
        },
    }
    contract_path = OUTPUT / "algorithm_b_fresh_rerun_contract.json"
    predecessor.atomic(contract_path, contract)
    print(contract_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
