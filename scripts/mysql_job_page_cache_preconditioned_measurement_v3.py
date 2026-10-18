#!/usr/bin/env python3
"""Permission-safe binding of the verified JOB page-cache precondition."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from scripts import mysql_job_page_cache_preconditioned_measurement_v2 as legacy


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = (
    ROOT / "config/formal/frozen_mysql_job_cache_state_20260823_v3"
    / "cache_state_contract.json"
)


def _database_dir(worker: Mapping[str, Any]) -> Path:
    worker_id = str(worker.get("worker_id") or "")
    datadir_text = str(worker.get("datadir") or "")
    expected = f"/disk2/resource_adaptive_mysql_algorithm_c_disk2_v4/{worker_id}/datadir"
    if (
        worker_id not in {f"job_w{index}" for index in range(1, 5)}
        or datadir_text != expected
        or ".." in Path(datadir_text).parts
    ):
        raise RuntimeError(f"unsafe JOB page-cache target: {worker_id}")
    # The unprivileged controller cannot traverse the 0700 MySQL datadir.
    # The exact 21-file/byte inventory performed by sudo find in legacy._files
    # is the authoritative existence, non-symlink-root and scope check.
    return Path(datadir_text) / "imdb_job"


def _validate_contract() -> dict[str, Any]:
    if not CONTRACT_PATH.is_file() or CONTRACT_PATH.is_symlink():
        raise RuntimeError(f"JOB page-cache contract absent: {CONTRACT_PATH}")
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    if (
        contract.get("schema_version") != "mysql_job_cache_state_contract_v3"
        or contract.get("release_version") != "v3"
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
        or contract.get("precondition_metrics_used_for_scoring") is not False
        or contract.get("formal_measurement_replays_per_candidate") != 1
        or contract.get("outer_loop_semantics_changed") is not False
    ):
        raise RuntimeError("JOB page-cache v3 contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        path = ROOT / str(binding["path"])
        if not path.is_file() or path.is_symlink() or legacy._sha(path) != binding["sha256"]:
            raise RuntimeError(f"JOB page-cache v3 source binding differs: {path}")
    return contract


legacy.CONTRACT_PATH = CONTRACT_PATH
legacy._database_dir = _database_dir
legacy._validate_contract = _validate_contract
measure_batch = legacy.measure_batch

__all__ = ["CONTRACT_PATH", "measure_batch"]
