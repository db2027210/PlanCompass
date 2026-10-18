#!/usr/bin/env python3
"""Module."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import random
import statistics
import sys
import threading
import time
from typing import Any, Dict, List, Mapping, Optional, Sequence

import psycopg2


ROOT = Path(__file__).resolve().parents[1]
ISOLATED_ROOT = Path("/home/anonymous/program/resource_adaptive_double_loop_sgd_isolated")
for import_root in (ROOT, ISOLATED_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from formal_measurement import (  # noqa: E402
    FrozenMeasurementProtocol,
    assess_measurement_samples,
)
from formal_contracts import (  # noqa: E402
    PG5_IO_UTILIZATION_SHA256,
    PG5_R0_GATE_SHA256,
    PG5_STABILITY_GATE_SHA256,
    PG5_SCORING_SHA256,
    evaluate_r0_single_bottleneck_gate,
    evaluate_six_window_mean_stability,
    validate_benchmark_target_resource,
)
from system_resource_monitor import _cgroup_snapshot, _find_cgroup_paths  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--user", default="postgres")
    parser.add_argument("--password", default="postgres")
    parser.add_argument("--database", required=True)
    parser.add_argument("--container", required=True)
    parser.add_argument("--sql-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--seed", type=int, default=20260722)
    parser.add_argument("--warmup-seconds", type=int, default=60)
    parser.add_argument("--minimum-seconds", type=int, default=180)
    parser.add_argument("--maximum-seconds", type=int, default=600)
    parser.add_argument("--statement-timeout-ms", type=int, default=120000)
    parser.add_argument("--cpu-limit-cores", type=float, required=True)
    parser.add_argument("--memory-limit-bytes", type=float, required=True)
    parser.add_argument(
        "--io-total-limit-mib-s",
        "--io-limit-mib-s",
        dest="io_total_limit_mib_s",
        type=float,
        required=True,
        help="ReadCap+WriteCap in MiB/s; the old option name is retained as an alias.",
    )
    parser.add_argument("--io-read-cap-mib-s", type=float, default=None)
    parser.add_argument("--io-write-cap-mib-s", type=float, default=None)
    parser.add_argument(
        "--session-work-mem",
        default=None,
        help="Optional fixed work_mem applied to every benchmark connection.",
    )
    parser.add_argument("--special-query", default="q18.sql")
    parser.add_argument("--special-weight", type=int, default=16)
    parser.add_argument("--default-weight", type=int, default=4)
    parser.add_argument(
        "--r0-target-resource",
        choices=("cpu", "memory", "io"),
        default=None,
    )
    parser.add_argument(
        "--r0-target-min-pct",
        type=float,
        default=85.0,
        help="Inclusive lower utilization bound for the R0 target resource.",
    )
    parser.add_argument(
        "--r0-target-max-pct",
        type=float,
        default=95.0,
        help="Inclusive upper utilization bound for the R0 target resource.",
    )
    parser.add_argument(
        "--stability-target-resource",
        choices=("cpu", "memory", "io"),
        default=None,
        help=(
            "Evaluate early-stop stability only for this bottleneck resource. "
            "Unlike --r0-target-resource, this does not enable the R0 profile gate."
        ),
    )
    return parser.parse_args()


def evaluate_r0_profile_gate_with_bounds(
    utilization_pct: Dict[str, Any],
    target_resource: str,
    *,
    target_min_pct: float,
    target_max_pct: float,
) -> Tuple[bool, List[Dict[str, Any]]]:
    """Evaluate R0 construction using the lane's explicit target band."""

    target = str(target_resource).strip().lower()
    if target not in {"cpu", "memory", "io"}:
        raise ValueError(f"invalid R0 target resource: {target_resource!r}")
    if (
        not math.isfinite(target_min_pct)
        or not math.isfinite(target_max_pct)
        or target_min_pct > target_max_pct
    ):
        raise ValueError("R0 target bounds must be finite and ordered")
    checks: List[Dict[str, Any]] = []
    for resource in ("cpu", "memory", "io"):
        try:
            value = float(utilization_pct.get(resource))
        except (TypeError, ValueError):
            value = math.nan
        finite = math.isfinite(value)
        is_target = resource == target
        minimum = float(target_min_pct) if is_target else 30.0
        maximum = float(target_max_pct) if is_target else 50.0
        checks.append(
            {
                "resource": resource,
                "role": "target" if is_target else "non_target",
                "metric_value_pct": value if finite else None,
                "required_min_inclusive": minimum,
                "required_max_inclusive": maximum,
                "passed": bool(finite and minimum <= value <= maximum),
            }
        )
    return all(check["passed"] for check in checks), checks


def protocol() -> FrozenMeasurementProtocol:
    value = FrozenMeasurementProtocol(
        sample_interval_seconds=1,
        warmup_seconds=60,
        minimum_measurement_seconds=180,
        maximum_measurement_seconds=600,
        stability_window_seconds=30,
        required_consecutive_stability_windows=6,
        sample_coverage_min=0.9,
        cpu_cv_max=0.05,
        memory_cv_max=0.03,
        io_cv_max=0.05,
        normal_throughput_cv_max=0.05,
        xp_xap_qps_cv_max=0.1,
        xp_xtp_tps_cv_max=0.1,
        low_utilization_ratio_threshold=0.1,
        low_utilization_absolute_range_max=0.05,
    )
    value.validate()
    return value


def append_jsonl(path: Path, row: Mapping[str, Any], lock: threading.Lock) -> None:
    with lock:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()


def query_files(sql_dir: Path) -> List[Path]:
    files = sorted(path for path in sql_dir.glob("q*.sql") if path.is_file())
    expected = [f"q{index:02d}.sql" for index in range(1, 23)]
    observed = [path.name for path in files]
    if observed != expected:
        raise RuntimeError(f"TPC-H SQL registry mismatch: {observed}")
    return files


def percentile(values: Sequence[float], ratio: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * ratio) - 1))
    return float(ordered[index])


def db_calls(connection: Any) -> float:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT COALESCE(sum(calls), 0)::double precision
            FROM pg_stat_statements
            WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
              AND query NOT ILIKE '%%pg_stat_statements%%'
              AND query NOT ILIKE 'SET %%'
            """
        )
        return float(cursor.fetchone()[0])


def summarize(values: Sequence[float]) -> Dict[str, float]:
    if not values:
        return {"min": 0.0, "max": 0.0, "avg": 0.0}
    return {
        "min": float(min(values)),
        "max": float(max(values)),
        "avg": float(statistics.fmean(values)),
    }


def assess_low_frequency_windows(
    assessment: Any,
    *,
    resource_cv_max: float,
    target_resource: Optional[str] = None,
) -> Dict[str, Any]:
    """Apply six-window stability to the constructed bottleneck resource.

    CPU/Memory/IO window means are all retained for the profile gate and audit,
    but an R0 run stops when its configured target bottleneck is stable.  A
    bursty non-target resource must not force an otherwise reproducible
    bottleneck construction to run until 600 seconds.
    """
    all_resources = evaluate_six_window_mean_stability(
        assessment,
        ("cpu_utilization", "memory_utilization", "io_utilization"),
        coefficient_of_variation_max=resource_cv_max,
    )
    if target_resource is None:
        return all_resources

    target_field = f"{target_resource}_utilization"
    target_only = evaluate_six_window_mean_stability(
        assessment,
        (target_field,),
        coefficient_of_variation_max=resource_cv_max,
    )
    target_groups = {
        int(group.get("end_elapsed_seconds") or 0): group
        for group in target_only.get("groups") or []
    }
    for group in all_resources.get("groups") or []:
        target_group = target_groups.get(int(group.get("end_elapsed_seconds") or 0))
        group["all_resources_stable"] = bool(group.get("stable"))
        group["stability_metric_fields"] = [target_field]
        group["stable"] = bool(target_group and target_group.get("stable"))
    all_resources["mode"] = "target_bottleneck_six_consecutive_30s_window_means_common_cv"
    all_resources["stability_metric_fields"] = [target_field]
    all_resources["all_resource_metric_fields_retained"] = [
        "cpu_utilization",
        "memory_utilization",
        "io_utilization",
    ]
    all_resources["stable"] = bool(target_only.get("stable"))
    all_resources["consecutive_stable_windows"] = int(
        target_only.get("consecutive_stable_windows") or 0
    )
    all_resources["earliest_stable_stop_seconds"] = target_only.get(
        "earliest_stable_stop_seconds"
    )
    return all_resources


def latest_six_window_decision(
    low_frequency_assessment: Mapping[str, Any],
    *,
    checkpoint_seconds: Optional[int] = None,
) -> Dict[str, Any]:
    """Return the rolling 6x30s block owned by one decision checkpoint.

    When a checkpoint is supplied, select that checkpoint's exact block instead
    of the newest block currently available.  This keeps a briefly descheduled
    runner from silently skipping 180, 210, 240, ... decisions.
    """
    groups = list(low_frequency_assessment.get("groups") or [])
    if checkpoint_seconds is not None:
        groups = [
            group
            for group in groups
            if int(group.get("end_elapsed_seconds") or 0) == checkpoint_seconds
        ]
    if not groups:
        return {
            "available": False,
            "stable": False,
            "start_window_index": None,
            "end_window_index": None,
            "start_window_number": None,
            "end_window_number": None,
            "end_elapsed_seconds": None,
            "utilization_pct": {},
        }
    latest_group = dict(groups[-1])
    utilization_pct: Dict[str, float] = {}
    for item in latest_group.get("field_checks") or []:
        field = str(item.get("field") or "")
        raw_mean = item.get("mean_of_window_means")
        if not field.endswith("_utilization") or raw_mean is None:
            continue
        value = float(raw_mean)
        if math.isfinite(value) and value >= 0.0:
            utilization_pct[field.rsplit("_utilization", 1)[0]] = value * 100.0
    return {
        "available": len(utilization_pct) == 3,
        "stable": bool(latest_group.get("stable")),
        "start_window_index": latest_group.get("start_window_index"),
        "end_window_index": latest_group.get("end_window_index"),
        "start_window_number": int(latest_group.get("start_window_index") or 0) + 1,
        "end_window_number": int(latest_group.get("end_window_index") or 0) + 1,
        "end_elapsed_seconds": latest_group.get("end_elapsed_seconds"),
        "utilization_pct": utilization_pct,
    }


def main() -> int:
    args = parse_args()
    if args.r0_target_resource is not None:
        validate_benchmark_target_resource("tpch", args.r0_target_resource)
    stability_target_resource = (
        args.stability_target_resource or args.r0_target_resource
    )
    frozen = protocol()
    if (args.io_read_cap_mib_s is None) != (args.io_write_cap_mib_s is None):
        raise ValueError("IO read and write caps must be supplied together")
    io_read_cap_mib_s = (
        float(args.io_read_cap_mib_s)
        if args.io_read_cap_mib_s is not None
        else float(args.io_total_limit_mib_s) / 2.0
    )
    io_write_cap_mib_s = (
        float(args.io_write_cap_mib_s)
        if args.io_write_cap_mib_s is not None
        else float(args.io_total_limit_mib_s) / 2.0
    )
    if not math.isclose(
        io_read_cap_mib_s + io_write_cap_mib_s,
        float(args.io_total_limit_mib_s),
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        raise ValueError("IO total cap must equal read cap plus write cap")
    if (
        args.warmup_seconds != frozen.warmup_seconds
        or args.maximum_seconds != frozen.maximum_measurement_seconds
        or args.minimum_seconds < frozen.minimum_measurement_seconds
        or args.minimum_seconds > args.maximum_seconds
        or args.minimum_seconds % frozen.stability_window_seconds != 0
    ):
        raise ValueError(
            "measurement timing must keep warmup=60/max=600 and use a "
            "30-second-aligned minimum in [180, 600]"
        )
    if args.workers <= 0:
        raise ValueError("workers must be positive")

    output_dir = args.output_dir.resolve()
    if output_dir.exists():
        raise FileExistsError(f"output directory already exists: {output_dir}")
    output_dir.mkdir(parents=True, mode=0o700)
    sql_files = query_files(args.sql_dir.resolve())
    sql_text = {path.name: path.read_text(encoding="utf-8") for path in sql_files}
    weights = [
        args.special_weight if path.name == args.special_query else args.default_weight
        for path in sql_files
    ]
    if sum(weights) != 100:
        raise ValueError(f"query weights must sum to 100, got {sum(weights)}")

    dsn = {
        "host": args.host,
        "port": args.port,
        "user": args.user,
        "password": args.password,
        "dbname": args.database,
    }
    state_lock = threading.Lock()
    log_lock = threading.Lock()
    successes = 0
    failures = 0
    latencies_ms: List[float] = []
    per_query: Dict[str, Dict[str, int]] = {
        path.name: {"ok": 0, "failed": 0} for path in sql_files
    }
    measurement_log = output_dir / "queries.jsonl"

    measurement_started_mono_ref: List[Optional[float]] = [None]
    worker_connections: List[Any] = []
    worker_connections_lock = threading.Lock()

    def run_workload(
        stop_event: threading.Event,
        measurement_active: threading.Event,
    ) -> None:
        def worker(worker_index: int) -> None:
            nonlocal successes, failures
            rng = random.Random(args.seed + worker_index)
            connection = psycopg2.connect(
                **dsn,
                application_name=f"resource-adaptive-tpch-worker-{worker_index}",
            )
            connection.autocommit = True
            with worker_connections_lock:
                worker_connections.append(connection)
            try:
                with connection.cursor() as cursor:
                    cursor.execute(f"SET statement_timeout = {int(args.statement_timeout_ms)}")
                    if args.session_work_mem:
                        cursor.execute(
                            "SELECT set_config('work_mem', %s, false)",
                            (str(args.session_work_mem),),
                        )
                while not stop_event.is_set():
                    selected = rng.choices(sql_files, weights=weights, k=1)[0]
                    began = time.monotonic()
                    record_measurement = measurement_active.is_set()
                    measurement_origin = measurement_started_mono_ref[0]
                    status = "ok"
                    error = None
                    try:
                        with connection.cursor() as cursor:
                            cursor.execute(sql_text[selected.name])
                            if cursor.description is not None:
                                cursor.fetchall()
                    except Exception as exc:
                        status = "failed"
                        error = f"{type(exc).__name__}: {exc}"
                    if stop_event.is_set():
                        # A query cancelled by the controller after the frozen
                        # measurement cutoff is cleanup, not a workload error.
                        record_measurement = False
                    elapsed_ms = (time.monotonic() - began) * 1000.0
                    if record_measurement and measurement_origin is not None:
                        with state_lock:
                            if status == "ok":
                                successes += 1
                                latencies_ms.append(elapsed_ms)
                                per_query[selected.name]["ok"] += 1
                            else:
                                failures += 1
                                per_query[selected.name]["failed"] += 1
                        append_jsonl(
                            measurement_log,
                            {
                                "phase": "measurement",
                                "worker": worker_index,
                                "query": selected.name,
                                "status": status,
                                "latency_ms": elapsed_ms,
                                "error": error,
                                "elapsed_s": time.monotonic() - measurement_origin,
                            },
                            log_lock,
                        )
            finally:
                with worker_connections_lock:
                    if connection in worker_connections:
                        worker_connections.remove(connection)
                connection.close()

        threads = [
            threading.Thread(
                target=worker,
                args=(index,),
                name=f"workload-{index}",
                daemon=True,
            )
            for index in range(args.workers)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    manifest = {
        "schema_version": 1,
        "campaign_id": "pg5_common_outer_bgallowed_20260722_v1",
        "benchmark": "tpch",
        "workload": {
            "query_files": [path.name for path in sql_files],
            "weights": dict(zip((path.name for path in sql_files), weights)),
            "workers": args.workers,
            "worker_lifecycle": "persistent_across_warmup_and_measurement",
        },
        "endpoint": {"host": args.host, "port": args.port, "database": args.database},
        "container": args.container,
        "background_load_allowed": True,
        "timing": {
            "warmup": 60,
            "minimum": args.minimum_seconds,
            "initial_probe": 300,
            "maximum": 600,
            "frozen_base_minimum_seconds": frozen.minimum_measurement_seconds,
            "lane_start_decision_seconds": args.minimum_seconds,
            "lane_decision_checkpoints_are_frozen_subsequence": True,
            "decision_order": "resource_stability_then_r0_profile_band",
            "unstable_at_minimum_action": "continue_until_stable_or_600_seconds",
            "stable_out_of_band_action": "stop_for_profile_reconstruction",
            "decision_step_seconds": 30,
            "decision_checkpoints_seconds": list(
                range(args.minimum_seconds, args.maximum_seconds + 1, 30)
            ),
            "reported_window_numbering": "one_based",
        },
        "fixed_session_work_mem": args.session_work_mem,
        "io_utilization": {
            "formula": "(read_bps+write_bps)/(read_cap_bps+write_cap_bps)",
            "read_cap_mib_s": io_read_cap_mib_s,
            "write_cap_mib_s": io_write_cap_mib_s,
            "total_cap_mib_s": args.io_total_limit_mib_s,
        },
        "contract_sha256": {
            "common_scoring": PG5_SCORING_SHA256,
            "r0_resource_gate": PG5_R0_GATE_SHA256,
            "io_utilization": PG5_IO_UTILIZATION_SHA256,
            "stability_gate": PG5_STABILITY_GATE_SHA256,
        },
        "db_qps_counter": {
            "mode": "post_warmup_measurement_boundary_pg_stat_statements_calls_delta",
            "synchronous_reset_before_measurement": False,
            "resource_loop_database_polling": False,
            "endpoint_statement_timeout_seconds": 5,
            "warmup_calls_excluded": True,
            "cleanup_calls_excluded": True,
            "workload_completion_qps_fallback_allowed": False,
        },
    }
    (output_dir / "run_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    # Establish the control connection before the workload can saturate the
    # constrained cgroup.  Opening it after warmup can block in PostgreSQL
    # backend startup and create a false hole at the start of measurement.
    sampler_connection = psycopg2.connect(**dsn)
    sampler_connection.autocommit = True
    with sampler_connection.cursor() as cursor:
        cursor.execute("SET statement_timeout = '5s'")
    try:
        pre_warmup_db_calls = db_calls(sampler_connection)
        pre_warmup_db_calls_valid = True
        pre_warmup_db_calls_error: Optional[str] = None
    except psycopg2.Error as exc:
        pre_warmup_db_calls = 0.0
        pre_warmup_db_calls_valid = False
        pre_warmup_db_calls_error = f"{type(exc).__name__}: {exc}"

    workload_stop = threading.Event()
    measurement_active = threading.Event()
    workload_thread = threading.Thread(
        target=run_workload,
        args=(workload_stop, measurement_active),
        name="persistent-workload-workers",
        daemon=True,
    )
    workload_thread.start()
    time.sleep(args.warmup_seconds)

    try:
        initial_db_calls = db_calls(sampler_connection)
        initial_db_calls_valid = True
        initial_db_calls_error: Optional[str] = None
    except psycopg2.Error as exc:
        initial_db_calls = 0.0
        initial_db_calls_valid = False
        initial_db_calls_error = f"{type(exc).__name__}: {exc}"
    db_qps_window_started_mono = time.monotonic()
    db_qps_window_started_epoch = time.time()

    cgroup_paths = _find_cgroup_paths(args.container)
    samples: List[Dict[str, Any]] = []
    samples_path = output_dir / "resource_samples.jsonl"
    stability_decisions_path = output_dir / "stability_decisions.jsonl"
    measurement_started_epoch = time.time()
    measurement_started_mono = time.monotonic()
    measurement_started_mono_ref[0] = measurement_started_mono
    measurement_active.set()
    previous_snapshot = _cgroup_snapshot(
        cgroup_paths,
        memory_mode="working_set",
    )
    previous_successes = 0
    previous_mono = measurement_started_mono
    stop_reason = "hard_maximum"
    final_assessment = None
    final_low_frequency_assessment: Optional[Dict[str, Any]] = None
    r0_profile_gate_passed: Optional[bool] = None
    r0_profile_gate_checks: List[Dict[str, Any]] = []
    final_db_calls: Optional[float] = None
    final_db_calls_error: Optional[str] = None
    db_qps_window_ended_mono: Optional[float] = None
    db_qps_window_ended_epoch: Optional[float] = None
    measurement_stopped_mono: Optional[float] = None
    remaining_worker_backend_count: Optional[int] = None
    try:
        sample_index = 1
        next_decision_seconds = args.minimum_seconds
        while sample_index <= args.maximum_seconds:
            deadline = measurement_started_mono + sample_index
            time.sleep(max(0.0, deadline - time.monotonic()))
            now_mono = time.monotonic()
            now_epoch = time.time()
            snapshot = _cgroup_snapshot(
                cgroup_paths,
                memory_mode="working_set",
            )
            with state_lock:
                current_successes = successes
            delta_seconds = max(1e-6, now_mono - previous_mono)
            cpu_cores = max(0.0, snapshot["cpu_ns"] - previous_snapshot["cpu_ns"]) / (
                delta_seconds * 1e9
            )
            io_read_bytes_s = max(
                0.0,
                snapshot["io_read_bytes"] - previous_snapshot["io_read_bytes"],
            ) / delta_seconds
            io_write_bytes_s = max(
                0.0,
                snapshot["io_write_bytes"] - previous_snapshot["io_write_bytes"],
            ) / delta_seconds
            io_read_cap_bytes_s = io_read_cap_mib_s * 1024.0 * 1024.0
            io_write_cap_bytes_s = io_write_cap_mib_s * 1024.0 * 1024.0
            io_total_cap_bytes_s = args.io_total_limit_mib_s * 1024.0 * 1024.0
            row = {
                "epoch_seconds": now_epoch,
                "cpu_utilization": cpu_cores / args.cpu_limit_cores,
                "memory_utilization": snapshot["memory_bytes"] / args.memory_limit_bytes,
                "io_utilization": (
                    io_read_bytes_s + io_write_bytes_s
                ) / io_total_cap_bytes_s,
                "qps": (
                    max(0, current_successes - previous_successes) / delta_seconds
                ),
                "raw_cpu_cores": cpu_cores,
                "raw_memory_bytes": snapshot["memory_bytes"],
                "raw_io_read_bytes_s": io_read_bytes_s,
                "raw_io_write_bytes_s": io_write_bytes_s,
                "raw_io_bytes_s": io_read_bytes_s + io_write_bytes_s,
                "io_total_cap_bytes_s": io_total_cap_bytes_s,
                "io_read_cap_bytes_s": io_read_cap_bytes_s,
                "io_write_cap_bytes_s": io_write_cap_bytes_s,
                "db_calls": None,
                "db_calls_sample_available": False,
                "db_calls_sample_error": "not_polled_in_resource_loop",
                "qps_sample_source": "completed_benchmark_queries",
            }
            samples.append(row)
            append_jsonl(samples_path, row, log_lock)
            previous_snapshot = snapshot
            previous_successes = current_successes
            previous_mono = now_mono
            elapsed = now_mono - measurement_started_mono
            # If this process was descheduled across one or more one-second
            # deadlines, skip those missed slots.  Catch-up sampling would
            # emit several rows in the same frozen one-second slot and must
            # fail the duplicate-sample contract.  Missing slots are instead
            # represented honestly through the existing coverage gate.
            sample_index = max(sample_index + 1, int(math.floor(elapsed)) + 1)
            if elapsed >= next_decision_seconds:
                decision_checkpoint_seconds = int(next_decision_seconds)
                # Advance exactly one 30-second checkpoint.  If execution was
                # delayed, subsequent loop iterations audit each missed
                # checkpoint in order with its own frozen six-window block.
                next_decision_seconds += frozen.stability_window_seconds
                final_assessment = assess_measurement_samples(
                    samples,
                    measurement_start_epoch_seconds=measurement_started_epoch,
                    now_epoch_seconds=now_epoch,
                    xp=False,
                    protocol=frozen,
                )
                final_low_frequency_assessment = assess_low_frequency_windows(
                    final_assessment,
                    resource_cv_max=0.10,
                    target_resource=stability_target_resource,
                )
                latest_decision = latest_six_window_decision(
                    final_low_frequency_assessment,
                    checkpoint_seconds=decision_checkpoint_seconds,
                )
                resources_stable = bool(latest_decision["stable"])
                # The configured minimum is the first stability decision, not an
                # unconditional profile-band cutoff.  An unstable six-window
                # block can have a misleading interim mean, so it must keep
                # running until the first stable block or the 600-second hard
                # maximum.  Only a stable block owns the R0 profile decision.
                if resources_stable and args.r0_target_resource is not None:
                    r0_profile_gate_passed, r0_profile_gate_checks = (
                        evaluate_r0_profile_gate_with_bounds(
                            latest_decision["utilization_pct"],
                            args.r0_target_resource,
                            target_min_pct=args.r0_target_min_pct,
                            target_max_pct=args.r0_target_max_pct,
                        )
                    )
                append_jsonl(
                    stability_decisions_path,
                    {
                        "event": "resource_stability_decision",
                        "scheduled_checkpoint_seconds": decision_checkpoint_seconds,
                        "actual_elapsed_seconds": elapsed,
                        "decision_step_seconds": frozen.stability_window_seconds,
                        "latest_window": latest_decision,
                        "r0_target_resource": args.r0_target_resource,
                        "stability_target_resource": stability_target_resource,
                        "r0_profile_gate_evaluated": bool(resources_stable),
                        "r0_profile_gate_passed": (
                            r0_profile_gate_passed if resources_stable else None
                        ),
                        "r0_profile_gate_checks": (
                            r0_profile_gate_checks if resources_stable else []
                        ),
                        "action": (
                            "stop_stable_profile_pass"
                            if resources_stable and r0_profile_gate_passed is not False
                            else "stop_stable_profile_fail"
                            if resources_stable
                            else "stop_hard_maximum_unstable"
                            if decision_checkpoint_seconds >= args.maximum_seconds
                            else "continue_30_seconds"
                        ),
                    },
                    log_lock,
                )
                if resources_stable and r0_profile_gate_passed is False:
                    stop_reason = "stable_r0_profile_reconstruct_early_stop"
                    break
                if resources_stable:
                    stop_reason = "stable_early_stop"
                    break
    finally:
        measurement_active.clear()
        workload_stop.set()
        measurement_stopped_mono = time.monotonic()
        if initial_db_calls_valid:
            try:
                final_db_calls = db_calls(sampler_connection)
                db_qps_window_ended_mono = time.monotonic()
                db_qps_window_ended_epoch = time.time()
            except psycopg2.Error as exc:
                final_db_calls_error = f"{type(exc).__name__}: {exc}"
        # Cancel all owned workload sessions with one already-established
        # control connection.  Calling libpq cancel serially for every worker
        # can itself block for minutes under an extreme IO throttle.
        try:
            with sampler_connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT pg_cancel_backend(pid)
                    FROM pg_stat_activity
                    WHERE datname = current_database()
                      AND pid <> pg_backend_pid()
                      AND application_name LIKE
                          'resource-adaptive-tpch-worker-%%'
                    """
                )
        except psycopg2.Error:
            pass
        workload_thread.join(timeout=15.0)
        if workload_thread.is_alive():
            # A heavily IO-throttled PostgreSQL backend can remain inside a
            # statement after libpq cancel.  Terminate only the benchmark
            # workers identified by the application_name set above; never
            # terminate unrelated sessions or the sampler connection.
            try:
                with sampler_connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT pg_terminate_backend(pid)
                        FROM pg_stat_activity
                        WHERE datname = current_database()
                          AND pid <> pg_backend_pid()
                          AND application_name LIKE
                              'resource-adaptive-tpch-worker-%%'
                        """
                    )
            except psycopg2.Error:
                pass
            workload_thread.join(timeout=30.0)
        try:
            with sampler_connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT count(*)
                    FROM pg_stat_activity
                    WHERE datname = current_database()
                      AND application_name LIKE
                          'resource-adaptive-tpch-worker-%%'
                    """
                )
                remaining_worker_backend_count = int(cursor.fetchone()[0])
        except psycopg2.Error:
            remaining_worker_backend_count = None
        sampler_connection.close()
    if workload_thread.is_alive() and remaining_worker_backend_count != 0:
        raise RuntimeError("persistent workload workers did not stop after statement timeout")

    measurement_elapsed = min(
        float(args.maximum_seconds),
        (measurement_stopped_mono or time.monotonic()) - measurement_started_mono,
    )
    if final_assessment is None:
        final_assessment = assess_measurement_samples(
            samples,
            measurement_start_epoch_seconds=measurement_started_epoch,
            now_epoch_seconds=measurement_started_epoch + measurement_elapsed,
            xp=False,
            protocol=frozen,
        )
    if final_low_frequency_assessment is None:
        final_low_frequency_assessment = assess_low_frequency_windows(
            final_assessment,
            resource_cv_max=0.10,
            target_resource=stability_target_resource,
        )
    measurement_assessment = final_assessment.to_dict()
    measurement_assessment["legacy_per_second_all_fields_stable"] = bool(
        measurement_assessment.get("stable")
    )
    measurement_assessment["stable"] = bool(final_low_frequency_assessment["stable"])
    measurement_assessment["consecutive_stable_windows"] = int(
        final_low_frequency_assessment["consecutive_stable_windows"]
    )
    measurement_assessment["earliest_stable_stop_seconds"] = (
        final_low_frequency_assessment["earliest_stable_stop_seconds"]
    )
    measurement_assessment["cross_window_resource_stability"] = final_low_frequency_assessment
    resource_utilization = {
        "cpu": summarize([float(row["cpu_utilization"]) for row in samples]),
        "memory": summarize([float(row["memory_utilization"]) for row in samples]),
        "io": summarize([float(row["io_utilization"]) for row in samples]),
    }
    r0_decision_window = latest_six_window_decision(
        final_low_frequency_assessment
    )
    r0_gate_passed: Optional[bool] = None
    r0_gate_checks: List[Dict[str, Any]] = []
    if (
        args.r0_target_resource is not None
        and bool(r0_decision_window["available"])
        and bool(r0_decision_window["stable"])
    ):
        r0_gate_passed, r0_gate_checks = evaluate_r0_single_bottleneck_gate(
            r0_decision_window["utilization_pct"],
            args.r0_target_resource,
        )
        if r0_profile_gate_passed is None:
            r0_profile_gate_passed, r0_profile_gate_checks = (
                evaluate_r0_profile_gate_with_bounds(
                    r0_decision_window["utilization_pct"],
                    args.r0_target_resource,
                    target_min_pct=args.r0_target_min_pct,
                    target_max_pct=args.r0_target_max_pct,
                )
            )
    db_qps_counter_valid = bool(
        initial_db_calls_valid
        and final_db_calls is not None
        and db_qps_window_ended_mono is not None
    )
    db_qps_elapsed_seconds = (
        max(1e-9, float(db_qps_window_ended_mono) - db_qps_window_started_mono)
        if db_qps_counter_valid and db_qps_window_ended_mono is not None
        else None
    )
    db_qps = (
        max(0.0, float(final_db_calls) - initial_db_calls)
        / float(db_qps_elapsed_seconds)
        if db_qps_counter_valid
        else 0.0
    )
    status = "complete" if (
        successes > 0
        and failures == 0
        and bool(final_low_frequency_assessment["stable"])
        and r0_gate_passed is not False
        and r0_profile_gate_passed is not False
        and db_qps_counter_valid
    ) else "failed"
    summary = {
        "schema_version": 1,
        "status": status,
        "stop_reason": stop_reason,
        "effective_measurement_seconds": measurement_elapsed,
        "successful_queries": successes,
        "failed_queries": failures,
        "benchmark_qps": successes / max(1e-9, measurement_elapsed),
        "db_qps": db_qps,
        "db_qps_counter": {
            "valid": db_qps_counter_valid,
            "initial_calls": initial_db_calls if initial_db_calls_valid else None,
            "final_calls": final_db_calls,
            "initial_error": initial_db_calls_error,
            "final_error": final_db_calls_error,
            "pre_warmup_control_probe_calls": (
                pre_warmup_db_calls if pre_warmup_db_calls_valid else None
            ),
            "pre_warmup_control_probe_error": pre_warmup_db_calls_error,
            "window_started_epoch": db_qps_window_started_epoch,
            "window_ended_epoch": db_qps_window_ended_epoch,
            "elapsed_seconds": db_qps_elapsed_seconds,
            "warmup_calls_excluded": True,
            "cleanup_calls_excluded": True,
            "formula": "(final_calls-initial_calls)/db_qps_counter.elapsed_seconds",
            "fallback_to_workload_completion_qps": False,
        },
        "worker_cleanup": {
            "controller_thread_stopped": not workload_thread.is_alive(),
            "remaining_benchmark_backend_count": remaining_worker_backend_count,
            "database_backends_fully_released": remaining_worker_backend_count == 0,
        },
        "latency_ms": {
            "p90": percentile(latencies_ms, 0.90),
            "p95": percentile(latencies_ms, 0.95),
            "p99": percentile(latencies_ms, 0.99),
        },
        "resource_utilization": resource_utilization,
        "r0_resource_gate": {
            "target_resource": args.r0_target_resource,
            "passed": r0_gate_passed,
            "checks": r0_gate_checks,
            "affects_outer_acceptance": False,
        },
        "r0_profile_construction_gate": {
            "target_resource": args.r0_target_resource,
            "passed": r0_profile_gate_passed,
            "checks": r0_profile_gate_checks,
            "scope": "r0_default_only_not_outer_candidates",
        },
        "r0_decision_window": {
            **r0_decision_window,
            "mean_scope": "latest_six_consecutive_30_second_windows",
            "earlier_windows_affect_decision": False,
        },
        "per_query": per_query,
        "measurement_assessment": measurement_assessment,
    }
    summary_path = output_dir / "summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    aggregate = {
        "cpu_utilization": float(resource_utilization["cpu"]["avg"]),
        "memory_utilization": float(resource_utilization["memory"]["avg"]),
        "io_utilization": float(resource_utilization["io"]["avg"]),
        "read_bps": statistics.fmean(
            float(row["raw_io_read_bytes_s"]) for row in samples
        ),
        "write_bps": statistics.fmean(
            float(row["raw_io_write_bytes_s"]) for row in samples
        ),
        "total_bps": statistics.fmean(float(row["raw_io_bytes_s"]) for row in samples),
        "memory_working_set_bytes": statistics.fmean(
            float(row["raw_memory_bytes"]) for row in samples
        ),
    }
    marker = {
        "benchmark": {
            "throughput_qps": summary["benchmark_qps"],
            "db_qps": summary["db_qps"],
            "query_count": successes,
        },
        "benchmark_latency": {
            "all_p90_avg": summary["latency_ms"]["p90"],
            "all_p90_max": summary["latency_ms"]["p90"],
            "all_p95_avg": summary["latency_ms"]["p95"],
            "all_p95_max": summary["latency_ms"]["p95"],
            "all_p99_avg": summary["latency_ms"]["p99"],
            "all_p99_max": summary["latency_ms"]["p99"],
        },
        "formal_pg_resource": {
            "semantics": "pg5_io_total_throughput_utilization_v1",
            "raw_range_validation": "finite_and_nonnegative_no_upper_clip",
            "io_formula": "(read_bps + write_bps) / (read_cap_bps + write_cap_bps)",
            "caps": {
                "cpu_quota_cores": args.cpu_limit_cores,
                "memory_bytes": args.memory_limit_bytes,
                "read_cap_bps": io_read_cap_mib_s * 1024.0 * 1024.0,
                "write_cap_bps": io_write_cap_mib_s * 1024.0 * 1024.0,
                "io_total_cap_bps": args.io_total_limit_mib_s * 1024.0 * 1024.0,
            },
            "sample_count": len(samples),
            "aggregate": aggregate,
            "samples_path": str(samples_path),
        },
        "candidate_measurement_protocol": {
            "stable": bool(final_low_frequency_assessment["stable"]),
            "stop_reason": stop_reason,
            "effective_measurement_seconds": measurement_elapsed,
            "minimum_measurement_seconds": args.minimum_seconds,
            "frozen_base_minimum_seconds": frozen.minimum_measurement_seconds,
            "lane_start_decision_seconds": args.minimum_seconds,
            "initial_probe_seconds": args.minimum_seconds,
            "maximum_measurement_seconds": args.maximum_seconds,
            "throughput_stability_required": False,
            "complete_window_count": final_assessment.complete_window_count,
            "stability_contract_sha256": PG5_STABILITY_GATE_SHA256,
            "summary_path": str(summary_path),
        },
    }
    print(
        "RESOURCE_ADAPTIVE_BENCHMARK_JSON="
        + json.dumps(marker, ensure_ascii=False, sort_keys=True),
        flush=True,
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True), flush=True)
    return 0 if summary["status"] == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
