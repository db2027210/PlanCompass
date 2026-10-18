#!/usr/bin/env python3
"""TPC-H measurement with a frozen, non-scoring warm-cache precondition.

The historical MySQL TPC-H controllers reused long-lived InnoDB instances.
Consequently, a cold campaign baseline could be compared with progressively
warmer candidate replays.  This wrapper fixes only the MySQL measurement
interface: before every scored TPC-H replay, each assigned worker executes the
same default-cost Q1--Q22 suite once.  The precondition result is validated and
recorded, but it is never returned to the optimizer and cannot affect accepted
rounds, candidate ranking, or interval shrinkage.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from scripts import mysql_single_scene_measurement_v1 as predecessor


alg = predecessor.alg
CONTRACT_PATH = (
    Path(__file__).resolve().parents[1]
    / "config/formal/frozen_mysql_tpch_cache_state_20260823_v1"
    / "cache_state_contract.json"
)
REQUIRED_QUERIES = [f"Q{query_id}" for query_id in range(1, 23)]


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_contract() -> dict[str, Any]:
    if not CONTRACT_PATH.is_file() or CONTRACT_PATH.is_symlink():
        raise RuntimeError(f"TPC-H cache-state contract absent: {CONTRACT_PATH}")
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    bindings = contract.get("source_bindings") or {}
    root = Path(__file__).resolve().parents[1]
    if (
        contract.get("schema_version")
        != "mysql_tpch_cache_state_contract_v1"
        or contract.get("release_version") != "v1"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("scene") != "tpch_sf4_memory"
        or contract.get("precondition_parameter_vector") != alg.DEFAULTS
        or contract.get("precondition_query_set") != REQUIRED_QUERIES
        or contract.get("precondition_replays_per_scored_measurement") != 1
        or contract.get("precondition_metrics_used_for_scoring") is not False
        or contract.get("candidate_parameter_application_after_precondition")
        is not True
        or contract.get("formal_measurement_replays_per_candidate") != 1
        or contract.get("outer_loop_semantics_changed") is not False
    ):
        raise RuntimeError("TPC-H cache-state contract differs")
    for binding in bindings.values():
        path = root / str(binding["path"])
        if (
            not path.is_file()
            or path.is_symlink()
            or _sha(path) != str(binding["sha256"])
        ):
            raise RuntimeError(f"TPC-H cache-state source binding differs: {path}")
    return contract


def _status(worker: Mapping[str, Any]) -> dict[str, Any]:
    config = alg.mysql_root(worker, (
        "SELECT @@GLOBAL.innodb_flush_method,"
        "@@GLOBAL.innodb_buffer_pool_size,"
        "@@GLOBAL.innodb_buffer_pool_dump_at_shutdown,"
        "@@GLOBAL.innodb_buffer_pool_load_at_startup"
    )).split("\t")
    if len(config) != 4:
        raise RuntimeError(f"{worker['worker_id']} buffer-pool readback differs")
    rows = alg.mysql_root(worker, (
        "SHOW GLOBAL STATUS WHERE Variable_name IN "
        "('Innodb_buffer_pool_read_requests','Innodb_buffer_pool_reads')"
    )).splitlines()
    counters = {
        name: int(value) for name, value in
        (row.split("\t", 1) for row in rows if "\t" in row)
    }
    required = {"Innodb_buffer_pool_read_requests", "Innodb_buffer_pool_reads"}
    if set(counters) != required:
        raise RuntimeError(f"{worker['worker_id']} buffer-pool counters differ")
    return {
        "innodb_flush_method": config[0],
        "innodb_buffer_pool_size": int(config[1]),
        "innodb_buffer_pool_dump_at_shutdown": int(config[2]),
        "innodb_buffer_pool_load_at_startup": int(config[3]),
        "counters": counters,
    }


def _drain(
    workers: Mapping[str, Mapping[str, Any]], worker_ids: list[str],
) -> dict[str, Any]:
    document = predecessor.v3.session_drain.drain_workers(workers, worker_ids)
    if (
        document.get("status") != "PASS"
        or document.get("all_workers_zero_sessions_after") is not True
        or document.get("all_tpch_revenue0_views_absent_after") is not True
    ):
        raise RuntimeError("TPC-H cache precondition session drain failed closed")
    return document


def _precondition(
    workers: dict[str, dict[str, Any]],
    assignments: Mapping[str, Mapping[str, Any]],
    output: Path,
    *, state_path: Path | None,
    stage_prefix: str,
) -> dict[str, Any]:
    worker_ids = sorted(assignments)
    precondition_output = output.parent / f"{output.name}_tpch_cache_precondition"
    if precondition_output.exists() or precondition_output.is_symlink():
        raise FileExistsError(
            f"fresh TPC-H cache precondition output required: {precondition_output}"
        )
    precondition_output.mkdir(parents=True, mode=0o700)
    alg.set_runtime_stage(
        state_path, f"{stage_prefix}_TPCH_CACHE_PRECONDITION_DEFAULTS"
    )
    for worker_id in worker_ids:
        readback = alg.apply_costs(workers[worker_id], alg.DEFAULTS)
        if readback != alg.DEFAULTS:
            raise RuntimeError(
                f"{worker_id} default-cost cache precondition readback differs"
            )
        workers[worker_id]["formal_client_log"] = str(
            precondition_output / worker_id / "client_processes.jsonl"
        )
    pre_drain = _drain(workers, worker_ids)
    before = {worker_id: _status(workers[worker_id]) for worker_id in worker_ids}
    runners = {
        worker_id: alg.r0mod.PinnedRunner(
            workers[worker_id],
            Path(workers[worker_id]["formal_client_log"]),
        )
        for worker_id in worker_ids
    }
    raw: dict[str, dict[str, Any]] = {}
    failures: list[dict[str, str]] = []
    alg.set_runtime_stage(
        state_path, f"{stage_prefix}_TPCH_CACHE_PRECONDITION_Q1_Q22"
    )
    with ThreadPoolExecutor(
        max_workers=len(worker_ids),
        thread_name_prefix="tpch-cache-precondition",
    ) as pool:
        futures = {
            pool.submit(
                predecessor.tpch_measure,
                int(worker_id[-1]), workers, precondition_output, runners,
            ): worker_id
            for worker_id in worker_ids
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
    post_drain = _drain(workers, worker_ids)
    after = {worker_id: _status(workers[worker_id]) for worker_id in worker_ids}
    coverage: dict[str, Any] = {}
    for worker_id in worker_ids:
        row = raw.get(worker_id) or {}
        query_coverage = row.get("query_coverage") or {}
        completed = list(query_coverage.get("completed_queries") or [])
        valid = (
            query_coverage.get("coverage_complete") is True
            and completed == REQUIRED_QUERIES
            and float((row.get("metrics") or {}).get("qps") or 0.0) > 0.0
            and float(row.get("independent_wall_time_s") or 0.0) <= 900.0
        )
        coverage[worker_id] = {
            "valid": valid,
            "completed_queries": completed,
            "qps_diagnostic_only": (row.get("metrics") or {}).get("qps"),
            "wall_time_seconds": row.get("independent_wall_time_s"),
        }
        if not valid:
            failures.append({
                "worker_id": worker_id,
                "error": "default-cost Q1-Q22 cache precondition failed coverage",
            })
    document = {
        "schema_version": "mysql_tpch_cache_precondition_result_v1",
        "status": "PASS" if not failures else "FAILED_CLOSED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "scene": "tpch_sf4_memory",
        "purpose": "cache_preconditioning_only",
        "metrics_used_for_scoring": False,
        "accepted_state_credit": 0,
        "interval_shrink_credit": 0,
        "default_params": copy.deepcopy(alg.DEFAULTS),
        "required_queries": REQUIRED_QUERIES,
        "coverage": coverage,
        "buffer_pool_before": before,
        "buffer_pool_after": after,
        "pre_drain": pre_drain,
        "post_drain": post_drain,
        "failures": failures,
        "contract_path": str(CONTRACT_PATH),
        "contract_sha256": _sha(CONTRACT_PATH),
    }
    artifact = precondition_output / "cache_precondition_result.json"
    alg.atomic(artifact, document)
    if failures:
        raise RuntimeError(
            "TPC-H default-cost Q1-Q22 cache precondition failed closed"
        )
    return {
        "path": str(artifact),
        "sha256": _sha(artifact),
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
    precondition: dict[str, Any] | None = None
    if scenes == {"tpch_sf4_memory"}:
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
    document["tpch_cache_state_contract"] = {
        "path": str(CONTRACT_PATH),
        "sha256": _sha(CONTRACT_PATH),
        "release_version": contract["release_version"],
        "applied": precondition is not None,
    }
    document["tpch_cache_precondition"] = precondition
    document["precondition_metrics_used_for_scoring"] = False
    document["outer_loop_semantics_changed"] = False
    alg.atomic(batch_path, document)
    return measured


__all__ = ["CONTRACT_PATH", "measure_batch"]
