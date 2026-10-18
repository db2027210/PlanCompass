#!/usr/bin/env python3
"""Freeze the timeout-safe TPC-H C semantic-batch successor."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_tpch_c_semantic_batch_release_v1 as predecessor
from scripts import mysql_tpch_client_qps_measurement_v2 as metric
from scripts import mysql_tpch_worker_qps_normalization_v1 as normalization


OUTPUT = ROOT / "config/formal/frozen_mysql_tpch_c_semantic_batch_20260830_v7"
SOURCES = dict(predecessor.SOURCES)
SOURCES.update({
    "builder": Path(__file__).resolve(),
    "controller": ROOT / "scripts/run_mysql_tpch_algorithm_c_client_qps_normalized_v7.py",
})


def main() -> int:
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise RuntimeError(f"fresh C semantic-batch release required: {OUTPUT}")
    for path in SOURCES.values():
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"C semantic-batch source absent: {path}")
    contract = {
        "schema_version": "mysql_tpch_algorithm_c_semantic_batch_v7",
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
        "scene_scoped_relational_measurement_required": True,
        "sibling_relational_scene_required": False,
        "cached_no_move_prephysical_exclusion_required": True,
        "pg_first_accepting_semantic_batch_preserved": True,
        "later_semantic_batches_grant_state_credit": False,
        "active_scene_only_terminal_status": True,
        "predicted_and_plan_tree_expansion_after_first_reject": True,
        "candidate_batches": [[1, 2], [3, 4], [5]],
        "semantic_batch_order": [
            "base_top5", "fallback_plan_change",
            "current_interval_plan_coverage", "full_range_plan_basin_escape",
        ],
        "physical_replay_parallelism": 2,
        "failed_rows_receive_acceptance_credit": False,
        "two_rejected_outer_rounds_force_incumbent_centered_shrink": True,
        "convergence_max_normalized_width": 0.01,
        "algorithm_a_parameter_or_measurement_read": False,
        "external_algorithm_evidence_used": False,
        "timeout_safe_client_qps_adapter": True,
        "source_bindings": {
            name: {
                "path": str(path.relative_to(ROOT)),
                "sha256": predecessor.sha(path),
            }
            for name, path in sorted(SOURCES.items())
        },
    }
    contract_path = OUTPUT / "algorithm_c_semantic_batch_contract.json"
    predecessor.atomic(contract_path, contract)
    print(contract_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
