#!/usr/bin/env python3
"""Measure exactly one formal scene without a four-scene companion wave."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from mysql_benchmark_command import run_job_tpch_parallel
from mysql_workload_adapter import MySQLEndpoint
from scripts import run_mysql_four_scene_algorithm_b_scene_independent_measurement_v3 as v3
from scripts import run_mysql_four_scene_algorithm_b_scene_independent_measurement_v4 as v4
from scripts import mysql_async_xp_post_reset_settle_v2 as async_xp_settle


alg = v3.alg
_DUMMY_ENDPOINT = MySQLEndpoint(
    "127.0.0.1", 1, "discarded_counterpart", "discarded", "discarded",
)


def _only_worker(
    workers: Mapping[str, Mapping[str, Any]], prefix: str, pair: int,
) -> Mapping[str, Any]:
    worker_id = f"{prefix}_w{pair}"
    if worker_id not in workers:
        raise RuntimeError(f"single-scene worker absent: {worker_id}")
    worker = workers[worker_id]
    if any(
        str(other.get("scene")) != str(worker.get("scene"))
        for other in workers.values()
    ):
        raise RuntimeError("single-scene measurement received mixed scenes")
    return worker


def job_measure(
    pair: int, workers: Mapping[str, dict[str, Any]], output: Path,
    runners: Mapping[str, Any],
) -> dict[str, Any]:
    worker = _only_worker(workers, "job", pair)
    query_files = alg.discover_job_query_files(alg.JOB_QUERY_ROOT)
    manifest = alg.build_job_manifest(
        query_files, output / f"pair_{pair}" / "single_scene_job_manifest",
    )
    user, password = alg.private_credential(worker)
    result = run_job_tpch_parallel(
        job_manifest=manifest,
        job_endpoint=MySQLEndpoint(
            "127.0.0.1", int(worker["host_port"]), "imdb_job", user, password,
        ),
        tpch_endpoint=_DUMMY_ENDPOINT,
        output_dir=output / f"pair_{pair}",
        benchbase_jar=alg.r0mod.BENCHBASE_JAR,
        clients=16,
        tpch_clients=5,
        scale_factor=4,
        timeout_s=900,
        target_wall_time_s=900.0,
        job_heavy_clients=4,
        formal_scope="mysql_only",
        workload_freeze=alg.r0mod.WORKLOAD_FREEZE,
        capture_job_explain=False,
        job_runner=lambda *args, **kwargs: alg.run_job(
            *args, **kwargs, runner=runners[str(worker["worker_id"])],
        ),
        tpch_runner=v3._fake_tpch_runner,
    )
    workload = dict(result["workloads"]["job"])
    workload.update({
        "scene_independent_execution": True,
        "discarded_counterpart_validation_stub": "tpch",
        "independent_wall_time_s": float(result["wall_time_s"]),
        "four_scene_wave_barrier": False,
    })
    return workload


def tpch_measure(
    pair: int, workers: Mapping[str, dict[str, Any]], output: Path,
    runners: Mapping[str, Any],
) -> dict[str, Any]:
    worker = _only_worker(workers, "tpch", pair)
    query_files = alg.discover_job_query_files(alg.JOB_QUERY_ROOT)
    manifest = alg.build_job_manifest(
        query_files, output / f"pair_{pair}" / "discarded_job_manifest",
    )
    user, password = alg.private_credential(worker)
    result = run_job_tpch_parallel(
        job_manifest=manifest,
        job_endpoint=_DUMMY_ENDPOINT,
        tpch_endpoint=MySQLEndpoint(
            "127.0.0.1", int(worker["host_port"]), "tpch", user, password,
        ),
        output_dir=output / f"pair_{pair}",
        benchbase_jar=alg.r0mod.BENCHBASE_JAR,
        clients=16,
        tpch_clients=5,
        scale_factor=4,
        timeout_s=900,
        target_wall_time_s=900.0,
        job_heavy_clients=4,
        formal_scope="mysql_only",
        workload_freeze=alg.r0mod.WORKLOAD_FREEZE,
        capture_job_explain=False,
        job_runner=v3._fake_job_runner,
        tpch_runner=lambda *args, **kwargs: alg.run_benchbase(
            *args, **kwargs, runner=runners[str(worker["worker_id"])],
        ),
    )
    workload = dict(result["workloads"]["tpch"])
    workload.update({
        "scene_independent_execution": True,
        "discarded_counterpart_validation_stub": "job",
        "independent_wall_time_s": float(result["wall_time_s"]),
        "four_scene_wave_barrier": False,
    })
    return workload


def measure_batch(
    freeze: Mapping[str, Any], workers: dict[str, dict[str, Any]],
    assignments: Mapping[str, Mapping[str, Any]], output: Path,
    reset_token: str, *, state_path: Path | None = None,
    stage_prefix: str = "FORMAL_POINT",
) -> dict[str, dict[str, Any]]:
    worker_ids = set(workers)
    assignment_ids = set(assignments)
    if not assignment_ids or not assignment_ids.issubset(worker_ids):
        raise RuntimeError(
            "single-scene measurement received an assignment outside its worker set"
        )
    scenes = {str(workers[worker_id]["scene"]) for worker_id in assignments}
    worker_scenes = {str(worker["scene"]) for worker in workers.values()}
    if len(scenes) != 1 or worker_scenes != scenes:
        raise RuntimeError(
            "single-scene measurement requires one scene; unassigned workers from "
            "that same scene may remain idle"
        )
    original_job = v3.job_measure
    original_tpch = v3.tpch_measure
    original_reset_xp = v3.alg.reset_xp
    v3.job_measure = job_measure
    v3.tpch_measure = tpch_measure
    v3.alg.reset_xp = async_xp_settle.reset_xp
    try:
        measured = v4.measure_batch(
            freeze, workers, assignments, output, reset_token,
            state_path=state_path, stage_prefix=stage_prefix,
        )
    finally:
        v3.job_measure = original_job
        v3.tpch_measure = original_tpch
        v3.alg.reset_xp = original_reset_xp
    batch_path = output / "batch_result.json"
    document = alg.load(batch_path)
    document.update({
        "schema_version": "mysql_single_scene_independent_batch_v2",
        "scene": next(iter(scenes)),
        "four_scene_wave_barrier": False,
        "other_scene_completion_required": False,
        "incumbent_companion_workloads": False,
        "scene_may_advance_immediately": True,
    })
    alg.atomic(batch_path, document)
    return measured


__all__ = ["measure_batch"]
