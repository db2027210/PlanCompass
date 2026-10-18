#!/usr/bin/env python3
"""Bind the JOB InnoDB primer to MySQL's exact persisted readback precision."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts import mysql_job_buffer_pool_preconditioned_measurement_v7 as v7

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = (
    ROOT / "config/formal/frozen_mysql_job_cache_state_20260823_v8"
    / "cache_state_contract.json"
)
PRIMER_PARAMS = {
    "disk_temptable_create_cost": 20,
    "disk_temptable_row_cost": 0.5,
    "io_block_read_cost": 0.57544,
    "key_compare_cost": 0.05,
    "memory_block_read_cost": 0.43445,
    "memory_temptable_create_cost": 1,
    "memory_temptable_row_cost": 0.17378,
    "row_evaluate_cost": 0.057544,
}


def _validate_contract() -> dict[str, Any]:
    if CONTRACT_PATH.is_symlink() or not CONTRACT_PATH.is_file():
        raise RuntimeError(f"JOB buffer-pool v8 contract absent: {CONTRACT_PATH}")
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    if (
        contract.get("schema_version") != "mysql_job_cache_state_contract_v8"
        or contract.get("release_version") != "v8"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("scene") != "job_memory"
        or contract.get("precondition_mode")
        != "fixed_optimizer_plan_full_job113_innodb_working_set_primer"
        or contract.get("precondition_parameter_vector") != PRIMER_PARAMS
        or contract.get("mysql_exact_persisted_readback_required") is not True
        or contract.get("precondition_query_count") != 113
        or contract.get("precondition_replays_per_scored_measurement") != 1
        or contract.get("precondition_metrics_used_for_scoring") is not False
        or contract.get("candidate_parameter_application_after_precondition") is not True
        or contract.get("formal_measurement_replays_per_candidate") != 1
        or contract.get("outer_loop_semantics_changed") is not False
    ):
        raise RuntimeError("JOB buffer-pool v8 contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        path = ROOT / str(binding["path"])
        if path.is_symlink() or not path.is_file() or v7.legacy._sha(path) != binding["sha256"]:
            raise RuntimeError(f"JOB buffer-pool v8 source differs: {path}")
    return contract


v7.CONTRACT_PATH = CONTRACT_PATH
v7.PRIMER_PARAMS = PRIMER_PARAMS
v7._validate_contract = _validate_contract
measure_batch = v7.measure_batch

__all__ = ["CONTRACT_PATH", "PRIMER_PARAMS", "measure_batch"]
