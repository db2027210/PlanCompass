#!/usr/bin/env python3
"""JOB primer whose non-scoring wall time cannot invalidate scored work."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
import json
from pathlib import Path
from typing import Any, Mapping

from scripts import mysql_job_buffer_pool_preconditioned_measurement_v8 as v8


legacy = v8.v7.legacy
alg = legacy.alg
predecessor = legacy.predecessor
ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = (
    ROOT / "config/formal/frozen_mysql_job_cache_state_20260823_v9"
    / "cache_state_contract.json"
)
PRIMER_PARAMS = copy.deepcopy(v8.PRIMER_PARAMS)


def _validate_contract() -> dict[str, Any]:
    if CONTRACT_PATH.is_symlink() or not CONTRACT_PATH.is_file():
        raise RuntimeError(f"JOB buffer-pool v9 contract absent: {CONTRACT_PATH}")
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    if (
        contract.get("schema_version") != "mysql_job_cache_state_contract_v9"
        or contract.get("release_version") != "v9"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("scene") != "job_memory"
        or contract.get("precondition_mode")
        != "fixed_optimizer_plan_full_job113_innodb_working_set_primer"
        or contract.get("precondition_parameter_vector") != PRIMER_PARAMS
        or contract.get("precondition_query_count") != 113
        or contract.get("precondition_metrics_used_for_scoring") is not False
        or contract.get("precondition_wall_time_used_for_validity") is not False
        or contract.get("precondition_full_coverage_required") is not True
        or contract.get("candidate_parameter_application_after_precondition") is not True
        or contract.get("formal_measurement_replays_per_candidate") != 1
        or contract.get("scored_measurement_wall_time_limit_seconds") != 900
        or contract.get("outer_loop_semantics_changed") is not False
    ):
        raise RuntimeError("JOB buffer-pool v9 contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        source = ROOT / str(binding.get("path") or "")
        if (
            source.is_symlink() or not source.is_file()
            or legacy._sha(source) != binding.get("sha256")
        ):
            raise RuntimeError(f"JOB buffer-pool v9 source differs: {source}")
    return contract


def _precondition(
    workers: dict[str, dict[str, Any]],
    assignments: Mapping[str, Mapping[str, Any]], output: Path,
    *, state_path: Path | None, stage_prefix: str,
) -> dict[str, Any]:
    worker_ids = sorted(assignments)
    precondition_output = output.parent / f"{output.name}_job_cache_precondition"
    if precondition_output.exists() or precondition_output.is_symlink():
        raise FileExistsError(f"fresh JOB cache precondition required: {precondition_output}")
    precondition_output.mkdir(parents=True, mode=0o700)
    alg.set_runtime_stage(state_path, f"{stage_prefix}_JOB_CACHE_PRECONDITION_PRIMER")
    for worker_id in worker_ids:
        if alg.apply_costs(workers[worker_id], PRIMER_PARAMS) != PRIMER_PARAMS:
            raise RuntimeError(f"{worker_id} JOB primer-cost readback differs")
        workers[worker_id]["formal_client_log"] = str(
            precondition_output / worker_id / "client_processes.jsonl"
        )
    pre_drain = legacy._drain(workers, worker_ids)
    before = {worker_id: legacy._status(workers[worker_id]) for worker_id in worker_ids}
    runners = {
        worker_id: alg.r0mod.PinnedRunner(
            workers[worker_id], Path(workers[worker_id]["formal_client_log"]),
        ) for worker_id in worker_ids
    }
    raw: dict[str, dict[str, Any]] = {}
    failures: list[dict[str, str]] = []
    alg.set_runtime_stage(state_path, f"{stage_prefix}_JOB_CACHE_PRECONDITION_FULL113")
    with ThreadPoolExecutor(max_workers=len(worker_ids), thread_name_prefix="job-cache-v9") as pool:
        futures = {
            pool.submit(
                predecessor.job_measure, int(worker_id[-1]), workers,
                precondition_output, runners,
            ): worker_id for worker_id in worker_ids
        }
        for future in as_completed(futures):
            worker_id = futures[future]
            try:
                raw[worker_id] = future.result()
            except BaseException as error:
                failures.append({
                    "worker_id": worker_id,
                    "error": f"{type(error).__name__}: {error}",
                })
    post_drain = legacy._drain(workers, worker_ids)
    after = {worker_id: legacy._status(workers[worker_id]) for worker_id in worker_ids}
    coverage: dict[str, Any] = {}
    for worker_id in worker_ids:
        row = raw.get(worker_id) or {}
        query_coverage = row.get("query_coverage") or {}
        completed = list(query_coverage.get("completed_queries") or [])
        required = list(query_coverage.get("required_queries") or [])
        full_coverage = (
            query_coverage.get("coverage_complete") is True
            and len(completed) == 113 and len(required) == 113
            and sorted(completed) == sorted(required)
            and not (query_coverage.get("missing_queries") or [])
        )
        qps_positive = float((row.get("metrics") or {}).get("qps") or 0.0) > 0.0
        valid = full_coverage and qps_positive
        elapsed = float(row.get("independent_wall_time_s") or 0.0)
        coverage[worker_id] = {
            "valid": valid,
            "completed_query_count": len(completed),
            "qps_diagnostic_only": (row.get("metrics") or {}).get("qps"),
            "wall_time_seconds": row.get("independent_wall_time_s"),
            "within_scored_measurement_wall_time_limit": elapsed <= 900.0,
            "wall_time_used_for_precondition_validity": False,
            "full_coverage_required": True,
        }
        if not valid:
            failures.append({
                "worker_id": worker_id,
                "error": "full JOB primer coverage or positive-QPS gate failed",
            })
    document = {
        "schema_version": "mysql_job_buffer_pool_precondition_result_v9",
        "status": "PASS" if not failures else "FAILED_CLOSED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "scene": "job_memory",
        "purpose": "cache_preconditioning_only",
        "metrics_used_for_scoring": False,
        "wall_time_used_for_validity": False,
        "scored_measurement_wall_time_limit_seconds": 900,
        "scored_measurement_runs_after_this_precondition": True,
        "accepted_state_credit": 0,
        "interval_shrink_credit": 0,
        "precondition_mode": "fixed_optimizer_plan_full_job113_innodb_working_set_primer",
        "precondition_parameter_role": "cache_priming_only_not_algorithm_default_or_scored_candidate",
        "precondition_parameter_vector": copy.deepcopy(PRIMER_PARAMS),
        "required_query_count": 113,
        "coverage": coverage,
        "buffer_pool_before": before,
        "buffer_pool_after": after,
        "pre_drain": pre_drain,
        "post_drain": post_drain,
        "failures": failures,
        "contract_path": str(CONTRACT_PATH),
        "contract_sha256": legacy._sha(CONTRACT_PATH),
    }
    artifact = precondition_output / "cache_precondition_result.json"
    alg.atomic(artifact, document)
    if failures:
        raise RuntimeError("JOB full-suite cache primer failed closed")
    return {
        "path": str(artifact),
        "sha256": legacy._sha(artifact),
        "worker_count": len(worker_ids),
    }


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
        "path": str(CONTRACT_PATH),
        "sha256": legacy._sha(CONTRACT_PATH),
        "release_version": contract["release_version"],
        "applied": precondition is not None,
    }
    document["job_buffer_pool_precondition"] = precondition
    document["precondition_metrics_used_for_scoring"] = False
    document["precondition_wall_time_used_for_validity"] = False
    document["scored_measurement_wall_time_limit_seconds"] = 900
    document["outer_loop_semantics_changed"] = False
    alg.atomic(batch_path, document)
    return measured


__all__ = ["CONTRACT_PATH", "PRIMER_PARAMS", "measure_batch"]
