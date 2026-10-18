#!/usr/bin/env python3
"""Fully permission-safe JOB host-page-cache precondition binding."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import Any, Mapping

from scripts import mysql_job_page_cache_preconditioned_measurement_v3 as v3


legacy = v3.legacy
ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = (
    ROOT / "config/formal/frozen_mysql_job_cache_state_20260823_v4"
    / "cache_state_contract.json"
)


def _files(worker: Mapping[str, Any]) -> list[Path]:
    database_dir = v3._database_dir(worker)
    completed = subprocess.run(
        ["sudo", "-n", "find", str(database_dir), "-xdev", "-type", "f",
         "-name", "*.ibd", "-printf", "%s\t%p\0"],
        capture_output=True, check=True, timeout=60,
    )
    inventory: list[tuple[int, Path]] = []
    for raw in completed.stdout.split(b"\0"):
        if not raw:
            continue
        size_text, path_text = raw.decode("utf-8").split("\t", 1)
        inventory.append((int(size_text), Path(path_text)))
    inventory.sort(key=lambda row: str(row[1]))
    paths = [path for _size, path in inventory]
    total_bytes = sum(size for size, _path in inventory)
    if (
        len(paths) != legacy.EXPECTED_IBD_FILE_COUNT
        or total_bytes != legacy.EXPECTED_IBD_BYTES
        or any(path.parent != database_dir or path.suffix != ".ibd" for path in paths)
    ):
        raise RuntimeError(
            f"{worker['worker_id']} JOB .ibd inventory differs: "
            f"files={len(paths)} bytes={total_bytes}"
        )
    return paths


def _prefetch_worker(worker: Mapping[str, Any]) -> dict[str, Any]:
    paths = _files(worker)
    before = legacy._residency(paths)
    for path in paths:
        subprocess.run(
            ["ionice", "-c", "3", "sudo", "-n", "dd", f"if={path}",
             "of=/dev/null", "bs=16M", "status=none"],
            capture_output=True, check=True, timeout=900,
        )
    after = legacy._residency(paths)
    if any(row["resident_bytes"] < row["size_bytes"] for row in after):
        raise RuntimeError(f"{worker['worker_id']} JOB page-cache residency incomplete")
    return {
        "worker_id": worker["worker_id"],
        "database_dir": str(v3._database_dir(worker)),
        "file_count": len(paths),
        "total_bytes": sum(row["size_bytes"] for row in after),
        "resident_bytes_before": sum(row["resident_bytes"] for row in before),
        "resident_bytes_after": sum(row["resident_bytes"] for row in after),
        "minimum_resident_fraction_after": min(
            row["resident_fraction"] for row in after
        ),
        "files": after,
    }


def _validate_contract() -> dict[str, Any]:
    if not CONTRACT_PATH.is_file() or CONTRACT_PATH.is_symlink():
        raise RuntimeError(f"JOB page-cache contract absent: {CONTRACT_PATH}")
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    if (
        contract.get("schema_version") != "mysql_job_cache_state_contract_v4"
        or contract.get("release_version") != "v4"
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
        raise RuntimeError("JOB page-cache v4 contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        path = ROOT / str(binding["path"])
        if not path.is_file() or path.is_symlink() or legacy._sha(path) != binding["sha256"]:
            raise RuntimeError(f"JOB page-cache v4 source binding differs: {path}")
    return contract


legacy.CONTRACT_PATH = CONTRACT_PATH
legacy._database_dir = v3._database_dir
legacy._files = _files
legacy._prefetch_worker = _prefetch_worker
legacy._validate_contract = _validate_contract
measure_batch = legacy.measure_batch

__all__ = ["CONTRACT_PATH", "measure_batch"]
