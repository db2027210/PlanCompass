#!/usr/bin/env python3
"""JOB measurement with an explicit, verified host page-cache precondition."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any, Mapping

from scripts import mysql_single_scene_measurement_v1 as predecessor


alg = predecessor.alg
ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = (
    ROOT / "config/formal/frozen_mysql_job_cache_state_20260823_v2"
    / "cache_state_contract.json"
)
EXPECTED_IBD_FILE_COUNT = 21
EXPECTED_IBD_BYTES = 9_728_327_680


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validate_contract() -> dict[str, Any]:
    if not CONTRACT_PATH.is_file() or CONTRACT_PATH.is_symlink():
        raise RuntimeError(f"JOB page-cache contract absent: {CONTRACT_PATH}")
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    if (
        contract.get("schema_version") != "mysql_job_cache_state_contract_v2"
        or contract.get("release_version") != "v2"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("scene") != "job_memory"
        or contract.get("precondition_mode")
        != "verified_host_page_cache_full_imdb_job_ibd"
        or contract.get("expected_ibd_file_count_per_worker")
        != EXPECTED_IBD_FILE_COUNT
        or contract.get("expected_ibd_bytes_per_worker") != EXPECTED_IBD_BYTES
        or contract.get("minimum_resident_fraction") != 1.0
        or contract.get("precondition_query_count") != 0
        or contract.get("precondition_metrics_used_for_scoring") is not False
        or contract.get("formal_measurement_replays_per_candidate") != 1
        or contract.get("outer_loop_semantics_changed") is not False
    ):
        raise RuntimeError("JOB page-cache contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        path = ROOT / str(binding["path"])
        if not path.is_file() or path.is_symlink() or _sha(path) != binding["sha256"]:
            raise RuntimeError(f"JOB page-cache source binding differs: {path}")
    return contract


def _drain(
    workers: Mapping[str, Mapping[str, Any]], worker_ids: list[str],
) -> dict[str, Any]:
    document = predecessor.v3.session_drain.drain_workers(workers, worker_ids)
    if (
        document.get("status") != "PASS"
        or document.get("all_workers_zero_sessions_after") is not True
        or document.get("all_tpch_revenue0_views_absent_after") is not True
    ):
        raise RuntimeError("JOB page-cache precondition session drain failed closed")
    return document


def _database_dir(worker: Mapping[str, Any]) -> Path:
    worker_id = str(worker.get("worker_id") or "")
    runtime_root = Path(str(worker.get("datadir") or "")).resolve(strict=True)
    expected = Path(
        f"/disk2/resource_adaptive_mysql_algorithm_c_disk2_v4/{worker_id}/datadir"
    )
    if (
        worker_id not in {f"job_w{index}" for index in range(1, 5)}
        or runtime_root != expected
        or runtime_root.is_symlink()
    ):
        raise RuntimeError(f"unsafe JOB page-cache target: {worker_id}")
    database_dir = runtime_root / "imdb_job"
    if not database_dir.is_dir() or database_dir.is_symlink():
        raise RuntimeError(f"JOB database directory differs: {database_dir}")
    return database_dir


def _files(worker: Mapping[str, Any]) -> list[Path]:
    database_dir = _database_dir(worker)
    completed = subprocess.run(
        ["sudo", "-n", "find", str(database_dir), "-xdev", "-type", "f",
         "-name", "*.ibd", "-print0"],
        capture_output=True, check=True, timeout=60,
    )
    paths = sorted(
        Path(raw.decode("utf-8"))
        for raw in completed.stdout.split(b"\0") if raw
    )
    sizes = [path.stat().st_size for path in paths]
    if len(paths) != EXPECTED_IBD_FILE_COUNT or sum(sizes) != EXPECTED_IBD_BYTES:
        raise RuntimeError(
            f"{worker['worker_id']} JOB .ibd inventory differs: "
            f"files={len(paths)} bytes={sum(sizes)}"
        )
    if any(path.parent != database_dir or path.suffix != ".ibd" for path in paths):
        raise RuntimeError(f"{worker['worker_id']} JOB .ibd path differs")
    return paths


def _residency(paths: list[Path]) -> list[dict[str, Any]]:
    completed = subprocess.run(
        ["sudo", "-n", "fincore", "-b", "-n", "-o", "RES,SIZE,FILE",
         *map(str, paths)],
        capture_output=True, text=True, check=True, timeout=120,
    )
    rows: list[dict[str, Any]] = []
    for line in completed.stdout.splitlines():
        resident_text, size_text, file_text = line.split(maxsplit=2)
        resident = int(resident_text)
        size = int(size_text)
        rows.append({
            "path": file_text, "size_bytes": size,
            "resident_bytes": resident,
            "resident_fraction": resident / size if size else 1.0,
        })
    if len(rows) != len(paths) or {row["path"] for row in rows} != {
        str(path) for path in paths
    }:
        raise RuntimeError("JOB fincore inventory differs")
    return rows


def _prefetch_worker(worker: Mapping[str, Any]) -> dict[str, Any]:
    paths = _files(worker)
    before = _residency(paths)
    for path in paths:
        subprocess.run(
            ["ionice", "-c", "3", "sudo", "-n", "dd", f"if={path}",
             "of=/dev/null", "bs=16M", "status=none"],
            capture_output=True, check=True, timeout=900,
        )
    after = _residency(paths)
    if any(row["resident_bytes"] < row["size_bytes"] for row in after):
        raise RuntimeError(f"{worker['worker_id']} JOB page-cache residency incomplete")
    return {
        "worker_id": worker["worker_id"],
        "database_dir": str(_database_dir(worker)),
        "file_count": len(paths), "total_bytes": sum(path.stat().st_size for path in paths),
        "resident_bytes_before": sum(row["resident_bytes"] for row in before),
        "resident_bytes_after": sum(row["resident_bytes"] for row in after),
        "minimum_resident_fraction_after": min(
            row["resident_fraction"] for row in after
        ),
        "files": after,
    }


def _precondition(
    workers: dict[str, dict[str, Any]],
    assignments: Mapping[str, Mapping[str, Any]], output: Path,
    *, state_path: Path | None, stage_prefix: str,
) -> dict[str, Any]:
    worker_ids = sorted(assignments)
    artifact = output.parent / f"{output.name}_job_page_cache_precondition.json"
    if artifact.exists() or artifact.is_symlink():
        raise FileExistsError(f"fresh JOB page-cache artifact required: {artifact}")
    alg.set_runtime_stage(state_path, f"{stage_prefix}_JOB_PAGE_CACHE_PRECONDITION")
    pre_drain = _drain(workers, worker_ids)
    rows: dict[str, dict[str, Any]] = {}
    failures: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=len(worker_ids), thread_name_prefix="job-cache") as pool:
        futures = {
            pool.submit(_prefetch_worker, workers[worker_id]): worker_id
            for worker_id in worker_ids
        }
        for future in as_completed(futures):
            worker_id = futures[future]
            try:
                rows[worker_id] = future.result()
            except BaseException as error:
                failures.append({
                    "worker_id": worker_id,
                    "error": f"{type(error).__name__}: {error}",
                })
    document = {
        "schema_version": "mysql_job_page_cache_precondition_result_v2",
        "status": "PASS" if not failures else "FAILED_CLOSED",
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "scene": "job_memory", "purpose": "cache_preconditioning_only",
        "metrics_used_for_scoring": False, "accepted_state_credit": 0,
        "interval_shrink_credit": 0, "precondition_query_count": 0,
        "minimum_resident_fraction": 1.0, "workers": rows,
        "pre_drain": pre_drain, "failures": failures,
        "contract_path": str(CONTRACT_PATH), "contract_sha256": _sha(CONTRACT_PATH),
    }
    alg.atomic(artifact, document)
    if failures:
        raise RuntimeError("JOB page-cache precondition failed closed")
    return {"path": str(artifact), "sha256": _sha(artifact),
            "worker_count": len(worker_ids)}


def measure_batch(
    freeze: Mapping[str, Any], workers: dict[str, dict[str, Any]],
    assignments: Mapping[str, Mapping[str, Any]], output: Path,
    reset_token: str, *, state_path: Path | None = None,
    stage_prefix: str = "FORMAL_POINT",
) -> dict[str, dict[str, Any]]:
    contract = _validate_contract()
    scenes = {str(workers[worker_id]["scene"]) for worker_id in assignments}
    precondition = None
    if scenes == {"job_memory"}:
        precondition = _precondition(
            workers, assignments, output,
            state_path=state_path, stage_prefix=stage_prefix,
        )
    measured = predecessor.measure_batch(
        freeze, workers, assignments, output, reset_token,
        state_path=state_path, stage_prefix=stage_prefix,
    )
    batch_path = output / "batch_result.json"
    document = alg.load(batch_path)
    document["job_cache_state_contract"] = {
        "path": str(CONTRACT_PATH), "sha256": _sha(CONTRACT_PATH),
        "release_version": contract["release_version"],
        "applied": precondition is not None,
    }
    document["job_page_cache_precondition"] = precondition
    document["precondition_metrics_used_for_scoring"] = False
    document["outer_loop_semantics_changed"] = False
    alg.atomic(batch_path, document)
    return measured


__all__ = ["CONTRACT_PATH", "measure_batch"]
