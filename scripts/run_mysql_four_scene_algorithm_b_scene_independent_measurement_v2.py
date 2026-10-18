#!/usr/bin/env python3
"""Scene-independent measurement with full-suite TPC-H plan capture."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import math
from pathlib import Path
import time
from typing import Any, Mapping

from mysql_benchmark_command import run_job_tpch_parallel
from mysql_workload_adapter import MySQLEndpoint
from scripts import run_mysql_four_scene_algorithm_c_v2 as alg


def _fake_job_runner(
    manifest: Path, _endpoint: MySQLEndpoint, _output: Path, **_kwargs: Any,
) -> dict[str, Any]:
    names = [
        Path(str(row.get("path") or row.get("name"))).name
        for row in json.loads(Path(manifest).read_text(encoding="utf-8"))["queries"]
    ]
    return {
        "metrics": {"qps": 1.0, "latency_p95_ms": 1.0, "latency_p99_ms": 1.0},
        "query_coverage": {
            "completed_queries": names,
            "missing_queries": [],
            "coverage_complete": True,
        },
        "discarded_validation_stub": True,
    }


def _fake_tpch_runner(
    _benchmark: str, _endpoint: MySQLEndpoint, _output: Path,
    *, query_order: list[int], **_kwargs: Any,
) -> dict[str, Any]:
    completed = [f"Q{query_id}" for query_id in query_order]
    return {
        "metrics": {"qps": 1.0, "latency_p95_ms": 1.0, "latency_p99_ms": 1.0},
        "query_coverage": {
            "completed_queries": completed,
            "missing_queries": [],
            "unexpected_transaction_names": [],
            "coverage_complete": True,
        },
        "discarded_validation_stub": True,
    }


def _pair_inputs(
    pair: int, workers: Mapping[str, dict[str, Any]], output: Path,
    purpose: str,
) -> tuple[dict[str, Any], dict[str, Any], Path, str, str, str, str]:
    job = workers[f"job_w{pair}"]
    tpch = workers[f"tpch_w{pair}"]
    query_files = alg.discover_job_query_files(alg.JOB_QUERY_ROOT)
    manifest = alg.build_job_manifest(
        query_files,
        output / f"pair_{pair}" / f"independent_manifest_{purpose}",
    )
    job_user, job_password = alg.private_credential(job)
    tpch_user, tpch_password = alg.private_credential(tpch)
    return job, tpch, manifest, job_user, job_password, tpch_user, tpch_password


def job_measure(
    pair: int, workers: Mapping[str, dict[str, Any]], output: Path,
    runners: Mapping[str, alg.r0mod.PinnedRunner],
) -> dict[str, Any]:
    job, tpch, manifest, job_user, job_password, tpch_user, tpch_password = (
        _pair_inputs(pair, workers, output, "job")
    )
    result = run_job_tpch_parallel(
        job_manifest=manifest,
        job_endpoint=MySQLEndpoint(
            "127.0.0.1", int(job["host_port"]), "imdb_job", job_user, job_password,
        ),
        tpch_endpoint=MySQLEndpoint(
            "127.0.0.1", int(tpch["host_port"]), "tpch", tpch_user, tpch_password,
        ),
        output_dir=output / f"pair_{pair}",
        benchbase_jar=alg.r0mod.BENCHBASE_JAR,
        clients=16, tpch_clients=5, scale_factor=4, timeout_s=900,
        target_wall_time_s=900.0, job_heavy_clients=4,
        formal_scope="mysql_only", workload_freeze=alg.r0mod.WORKLOAD_FREEZE,
        capture_job_explain=False,
        job_runner=lambda *args, **kwargs: alg.run_job(
            *args, **kwargs, runner=runners[job["worker_id"]],
        ),
        tpch_runner=_fake_tpch_runner,
    )
    workload = dict(result["workloads"]["job"])
    workload["scene_independent_execution"] = True
    workload["discarded_counterpart_validation_stub"] = "tpch"
    workload["independent_wall_time_s"] = float(result["wall_time_s"])
    return workload


def tpch_measure(
    pair: int, workers: Mapping[str, dict[str, Any]], output: Path,
    runners: Mapping[str, alg.r0mod.PinnedRunner],
) -> dict[str, Any]:
    job, tpch, manifest, job_user, job_password, tpch_user, tpch_password = (
        _pair_inputs(pair, workers, output, "tpch")
    )
    result = run_job_tpch_parallel(
        job_manifest=manifest,
        job_endpoint=MySQLEndpoint(
            "127.0.0.1", int(job["host_port"]), "imdb_job", job_user, job_password,
        ),
        tpch_endpoint=MySQLEndpoint(
            "127.0.0.1", int(tpch["host_port"]), "tpch", tpch_user, tpch_password,
        ),
        output_dir=output / f"pair_{pair}",
        benchbase_jar=alg.r0mod.BENCHBASE_JAR,
        clients=16, tpch_clients=5, scale_factor=4, timeout_s=900,
        target_wall_time_s=900.0, job_heavy_clients=4,
        formal_scope="mysql_only", workload_freeze=alg.r0mod.WORKLOAD_FREEZE,
        capture_job_explain=False,
        job_runner=_fake_job_runner,
        tpch_runner=lambda *args, **kwargs: alg.run_benchbase(
            *args, **kwargs, runner=runners[tpch["worker_id"]],
        ),
    )
    workload = dict(result["workloads"]["tpch"])
    workload["scene_independent_execution"] = True
    workload["discarded_counterpart_validation_stub"] = "job"
    workload["independent_wall_time_s"] = float(result["wall_time_s"])
    return workload


def measure_batch(
    freeze: Mapping[str, Any], workers: dict[str, dict[str, Any]],
    assignments: Mapping[str, Mapping[str, Any]], output: Path, reset_token: str,
    *, state_path: Path | None = None, stage_prefix: str = "FORMAL_POINT",
) -> dict[str, dict[str, Any]]:
    """Run every scene independently while preserving concurrent scheduling."""
    if any(worker_id in assignments for worker_id in alg.XP_IDS):
        alg.set_runtime_stage(state_path, f"{stage_prefix}_RESETTING_XP")
        campaign_root = alg.measurement_campaign_root(output)
        alg.reset_xp(
            freeze, workers, campaign_root, reset_token,
            worker_ids=set(assignments).intersection(alg.XP_IDS),
        )
    output.mkdir(parents=True, mode=0o700)
    for worker_id, assignment in assignments.items():
        alg.apply_costs(workers[worker_id], assignment["params"])
    before_counter = {
        worker_id: alg.db_counter(workers[worker_id]) for worker_id in assignments
    }
    before_inspect = {
        worker_id: alg.reset.prep._docker_json(
            "inspect", workers[worker_id]["container"]
        )
        for worker_id in assignments
    }
    for worker_id in assignments:
        workers[worker_id]["formal_client_log"] = str(
            output / worker_id / "client_processes.jsonl"
        )
    runners = {
        worker_id: alg.r0mod.PinnedRunner(
            workers[worker_id], Path(workers[worker_id]["formal_client_log"])
        )
        for worker_id in assignments
    }
    sampler = alg.r0mod.CgroupSampler(
        [workers[worker_id] for worker_id in assignments],
        output / "resource_samples.jsonl",
    )
    alg.set_runtime_stage(state_path, f"{stage_prefix}_MEASURING")
    sampler.start()
    started = time.monotonic()
    raw: dict[str, Any] = {}
    failures: list[dict[str, str]] = []
    task_windows: dict[str, tuple[float, float]] = {}

    def timed(task: str, callback: Any, *callback_args: Any) -> Any:
        task_started = time.monotonic()
        try:
            return callback(*callback_args)
        finally:
            task_windows[task] = (task_started, time.monotonic())

    try:
        tasks: dict[Any, str] = {}
        with ThreadPoolExecutor(
            max_workers=max(1, len(assignments)),
            thread_name_prefix="formal-independent-scene",
        ) as pool:
            for worker_id in assignments:
                pair = int(worker_id[-1])
                if worker_id.startswith("job_w"):
                    future = pool.submit(
                        timed, worker_id, job_measure,
                        pair, workers, output, runners,
                    )
                elif worker_id.startswith("tpch_w"):
                    future = pool.submit(
                        timed, worker_id, tpch_measure,
                        pair, workers, output, runners,
                    )
                elif worker_id in alg.XP_IDS:
                    future = pool.submit(
                        timed, worker_id, alg.xp_measure,
                        workers[worker_id], output, runners[worker_id],
                    )
                else:
                    raise RuntimeError(f"unknown independent scene worker: {worker_id}")
                tasks[future] = worker_id
            for future in as_completed(tasks):
                worker_id = tasks[future]
                try:
                    raw[worker_id] = future.result()
                except BaseException as error:
                    failures.append({
                        "task": worker_id,
                        "error": f"{type(error).__name__}: {error}",
                    })
    finally:
        resources = sampler.finish({
            worker_id: task_windows[worker_id]
            for worker_id in assignments if worker_id in task_windows
        })
    elapsed = time.monotonic() - started
    after_counter = {
        worker_id: alg.db_counter(workers[worker_id]) for worker_id in assignments
    }
    alg.set_runtime_stage(state_path, f"{stage_prefix}_PLAN_CAPTURE")
    measurements: dict[str, dict[str, Any]] = {}
    for worker_id, assignment in assignments.items():
        scene = str(workers[worker_id]["scene"])
        workload = raw.get(worker_id) or {}
        if scene == "job_memory":
            coverage = workload.get("query_coverage", {}).get(
                "coverage_complete"
            ) is True
        elif scene == "tpch_sf4_memory":
            coverage = workload.get("query_coverage", {}).get(
                "coverage_complete"
            ) is True
        else:
            coverage = workload.get("workload_coverage", {}).get(
                "coverage_complete"
            ) is True
        plan_callback = lambda worker_id=worker_id, scene=scene: (
            alg.capture_scene_plans(freeze, scene, workers[worker_id])
        )
        formal_valid = worker_id in raw
        metrics = workload.get("metrics") or {}
        if formal_valid and coverage and float(metrics.get("qps", 0)) > 0:
            plans, plan_failure = alg.capture_plans_fail_closed(
                worker_id, plan_callback
            )
        else:
            plans, plan_failure = {}, {
                "task": f"{worker_id}_plan_capture",
                "error": "skipped_after_invalid_workload",
            }
        if plan_failure is not None:
            failures.append(plan_failure)
        resource = resources.get(worker_id) or {}
        utilization = {
            "cpu": float(resource.get("cpu_fraction_of_quota", math.nan)),
            "memory": float(resource.get("memory_peak_fraction", math.nan)),
            "io": max(
                float(resource.get("read_fraction_of_cap", math.nan)),
                float(resource.get("write_fraction_of_cap", math.nan)),
            ),
        }
        after = alg.reset.prep._docker_json(
            "inspect", workers[worker_id]["container"]
        )
        server_live = alg.mysql_server_liveness(workers[worker_id])
        restart_ok = (
            after.get("RestartCount")
            == before_inspect[worker_id].get("RestartCount")
            and (after.get("State") or {}).get("OOMKilled") is not True
            and server_live
        )
        active_elapsed = float(resource.get("elapsed_seconds", 0.0))
        db_qps = (
            (after_counter[worker_id] - before_counter[worker_id]) / active_elapsed
            if active_elapsed > 0 else 0.0
        )
        plan_sha = (
            hashlib.sha256(json.dumps(plans, sort_keys=True).encode()).hexdigest()
            if plans else None
        )
        fingerprint = {
            "l1_plan_tree_sha256": plan_sha,
            "l2_parameter_sha256": hashlib.sha256(
                json.dumps(assignment["params"], sort_keys=True).encode()
            ).hexdigest(),
            "l3_resource_workload_sha256": hashlib.sha256(json.dumps({
                "worker": worker_id,
                "freeze": alg.sha(alg.FREEZE),
                "coverage": coverage,
            }, sort_keys=True).encode()).hexdigest(),
            "complete": bool(plan_sha),
        }
        hard_gate_checks = {
            "formal_measurement_valid": formal_valid,
            "coverage_complete": coverage,
            "restart_and_oom_gate_passed": restart_ok,
            "mysql_server_liveness": server_live,
            "three_level_fingerprint_complete": fingerprint["complete"],
            "resource_utilization_finite": all(
                math.isfinite(value) for value in utilization.values()
            ),
            "resource_sample_coverage_sufficient": (
                active_elapsed > 0
                and float(resource.get("sample_count", 0)) >= active_elapsed * 0.85
            ),
            "database_qps_positive": db_qps > 0,
            "client_qps_audit_positive": float(metrics.get("qps", 0)) > 0,
        }
        hard_gate = all(hard_gate_checks.values())
        measurements[worker_id] = {
            "scene": scene,
            "worker_id": worker_id,
            "rank": assignment["rank"],
            "params": assignment["params"],
            "db_qps": db_qps,
            "client_qps_audit": float(metrics.get("qps", 0)),
            "p95_ms": float(metrics.get("latency_p95_ms", 0)),
            "p99_ms": float(metrics.get("latency_p99_ms", 0)),
            "resource_means": utilization,
            "hard_gate_passed": hard_gate,
            "hard_gate_checks": hard_gate_checks,
            "coverage_complete": coverage,
            "restart_gate_passed": restart_ok,
            "three_level_fingerprint": fingerprint,
            "resource_summary": resource,
            "candidate_physical_plan_sha256": assignment.get(
                "candidate_physical_plan_sha256"
            ),
            "workload_result": workload,
            "scene_independent_measurement": True,
        }
    alg.atomic(output / "batch_result.json", {
        "schema_version": "mysql_four_scene_algorithm_b_independent_batch_v3",
        "status": (
            "PASS"
            if not failures and all(
                row["hard_gate_passed"] for row in measurements.values()
            )
            else "FAILED_CLOSED"
        ),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "elapsed_seconds": elapsed,
        "measurements": measurements,
        "failures": failures,
        "scene_failure_isolation": True,
        "global_scene_validity_required": False,
        "tpch_plan_capture_query_count": 22,
    })
    return measurements
