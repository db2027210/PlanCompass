#!/usr/bin/env python3
"""Bind the verified JOB page-cache measurement to the v6 contract."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts import mysql_job_page_cache_preconditioned_measurement_v5 as v5

legacy = v5.legacy
ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = (
    ROOT / "config/formal/frozen_mysql_job_cache_state_20260823_v6"
    / "cache_state_contract.json"
)


def _validate_contract() -> dict[str, Any]:
    if not CONTRACT_PATH.is_file() or CONTRACT_PATH.is_symlink():
        raise RuntimeError(f"JOB page-cache v6 contract absent: {CONTRACT_PATH}")
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    if (
        contract.get("schema_version") != "mysql_job_cache_state_contract_v6"
        or contract.get("release_version") != "v6"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("scene") != "job_memory"
        or contract.get("precondition_mode")
        != "verified_host_page_cache_full_imdb_job_ibd"
        or contract.get("expected_ibd_file_count_per_worker")
        != legacy.EXPECTED_IBD_FILE_COUNT
        or contract.get("expected_ibd_bytes_per_worker")
        != legacy.EXPECTED_IBD_BYTES
        or contract.get("minimum_resident_fraction") != 1.0
        or contract.get("precondition_query_count") != 0
        or contract.get("formal_measurement_replays_per_candidate") != 1
        or contract.get("prestart_read_only_retry_seconds") != 600
        or contract.get("prestart_retry_interval_seconds") != 0.5
        or contract.get("outer_loop_semantics_changed") is not False
    ):
        raise RuntimeError("JOB page-cache v6 contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        path = ROOT / str(binding["path"])
        if not path.is_file() or path.is_symlink() or legacy._sha(path) != binding["sha256"]:
            raise RuntimeError(f"JOB page-cache v6 source binding differs: {path}")
    return contract


legacy.CONTRACT_PATH = CONTRACT_PATH
legacy._validate_contract = _validate_contract
measure_batch = legacy.measure_batch

__all__ = ["CONTRACT_PATH", "measure_batch"]
