#!/usr/bin/env python3
"""Run one TPC-C 32+1 point with frozen warmup and online stable early stop."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import signal
import statistics
import subprocess
import sys
import threading
import time
import traceback
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import psycopg2


ROOT = Path(__file__).resolve().parents[1]
ISOLATED_ROOT = Path("/home/anonymous/program/resource_adaptive_double_loop_sgd_isolated")
for import_root in (ROOT, ISOLATED_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from formal_measurement import FrozenMeasurementProtocol, assess_measurement_samples  # noqa: E402
from formal_contracts import (  # noqa: E402
    PG5_IO_UTILIZATION_SHA256,
    PG5_R0_GATE_SHA256,
    PG5_SCORING_SHA256,
    PG5_STABILITY_GATE_SHA256,
    evaluate_r0_profile_construction_gate,
    evaluate_r0_single_bottleneck_gate,
    evaluate_six_window_mean_stability,
    validate_benchmark_target_resource,
)
from system_resource_monitor import _cgroup_snapshot, _find_cgroup_paths  # noqa: E402


_FAILURE_CONTEXT: Dict[str, Any] = {}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--container", default="pg5_tpcc_d2_20260722")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=59640)
    parser.add_argument("--database", default="fullgrid_tpcc")
    parser.add_argument("--user", default="postgres")
    parser.add_argument("--password", default="postgres")
    parser.add_argument(
        "--props",
        type=Path,
        default=ROOT / "config/formal/pg5_common_outer_bgallowed_20260722_v1/tpcc_baseline.props",
    )
    parser.add_argument("--cpu-limit-cores", type=float, default=4.0)
    parser.add_argument("--memory-limit-bytes", type=float, default=16 * 1024 ** 3)
    parser.add_argument(
        "--io-total-limit-mib-s",
        "--io-limit-mib-s",
        dest="io_total_limit_mib_s",
        type=float,
        default=1150.0,
        help="ReadCap+WriteCap in MiB/s; the old option name is retained as an alias.",
    )
    parser.add_argument("--io-read-cap-mib-s", type=float, default=None)
    parser.add_argument("--io-write-cap-mib-s", type=float, default=None)
    parser.add_argument("--seed", type=int, default=4508333)
    parser.add_argument(
        "--db-lease-lock",
        type=Path,
        default=Path("/tmp/pg5_tpcc_59640_db_lease.lock"),
        help="Exclusive TPC-C database lease shared with the snapshot restore wrapper.",
    )
    parser.add_argument(
        "--r0-target-resource",
        choices=("cpu", "memory", "io"),
        default=None,
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


def workload_command(args: argparse.Namespace, output_dir: Path, duration_s: int) -> str:
    environment = {
        "BENCHBASE_JAVA_XMX": "-Xmx4g",
        "BENCHBASE_RUNTIME_ROOT": str(output_dir / "benchbase_runtime"),
        "BENCHBASE_SCALEFACTOR": "10",
        "BENCHBASE_SOURCE_ROOT": "/home/anonymous/benchbase-postgres",
        "BENCHBASE_TERMINALS": "32",
        "BENCHBASE_TPCC_WEIGHTS": "45,43,4,4,4",
        "GENERIC_RUN_SECONDS": str(duration_s),
        "TPCC_SENSITIVE_CLIENTS": "1",
        "TPCC_SENSITIVE_SQL_FILE": str(
            ISOLATED_ROOT / "config/workloads/tpcc_sensitive_stocklevel.sql"
        ),
    }
    words = ["env"]
    words.extend(f"{key}={value}" for key, value in environment.items())
    words.extend(
        [
            str(ISOLATED_ROOT / "shell/run_tpcc_sensitive_mix.sh"),
            "--mode",
            "generic",
            "--props",
            str(args.props.resolve()),
        ]
    )
    return shlex.join(words)


def append_jsonl(path: Path, row: Mapping[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()


def connect_database(args: argparse.Namespace) -> Any:
    return psycopg2.connect(
        host=args.host,
        port=args.port,
        user=args.user,
        password=args.password,
        dbname=args.database,
        connect_timeout=10,
        application_name="resource-adaptive-pg5-tpcc-counter",
    )


def database_counters(connection: Any) -> Tuple[float, float]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT COALESCE(xact_commit + xact_rollback, 0)::double precision
            FROM pg_stat_database
            WHERE datname = current_database()
            """
        )
        transactions = float(cursor.fetchone()[0])
        cursor.execute(
            """
            SELECT COALESCE(sum(calls), 0)::double precision
            FROM pg_stat_statements
            WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
              AND query NOT ILIKE '%%pg_stat_statements%%'
              AND query NOT ILIKE 'SET %%'
            """
        )
        calls = float(cursor.fetchone()[0])
    return transactions, calls


def run_counter_sampler(
    args: argparse.Namespace,
    *,
    stop_event: threading.Event,
    state: Dict[str, float],
    state_lock: threading.Lock,
    samples_path: Path,
    errors_path: Path,
    initial_transactions: float,
    initial_calls: float,
) -> None:
    """Sample counters independently and expose any continuity loss to the runner.

    A database restore or restart invalidates the point.  The sampler therefore
    records the first error for the main loop instead of dying silently.  It
    still reconnects so diagnostics contain the post-failure state, but the
    point is never accepted after counter continuity has been lost.
    """
    connection: Optional[Any] = None
    previous_transactions = initial_transactions
    previous_calls = initial_calls
    previous_mono = time.monotonic()
    try:
        while not stop_event.wait(1.0):
            query_started = time.monotonic()
            try:
                if connection is None or connection.closed:
                    connection = connect_database(args)
                    connection.autocommit = True
                    previous_transactions, previous_calls = database_counters(connection)
                    previous_mono = time.monotonic()
                    with state_lock:
                        state["reconnect_count"] = float(
                            state.get("reconnect_count", 0.0)
                        ) + 1.0
                    continue
                transactions, calls = database_counters(connection)
                now_mono = time.monotonic()
                delta_s = max(1e-6, now_mono - previous_mono)
                transaction_delta = transactions - previous_transactions
                calls_delta = calls - previous_calls
                counter_reset = transaction_delta < 0.0 or calls_delta < 0.0
                combined_tps = max(0.0, transaction_delta) / delta_s
                db_qps = max(0.0, calls_delta) / delta_s
                row = {
                    "epoch_seconds": time.time(),
                    "monotonic_seconds": now_mono,
                    "query_duration_seconds": now_mono - query_started,
                    "counter_interval_seconds": delta_s,
                    "transactions": transactions,
                    "calls": calls,
                    "combined_tps": combined_tps,
                    "db_qps": db_qps,
                    "counter_reset": counter_reset,
                }
                append_jsonl(samples_path, row)
                with state_lock:
                    state.update(
                        {
                            "combined_tps": combined_tps,
                            "db_qps": db_qps,
                            "transactions": transactions,
                            "calls": calls,
                            "last_update_mono": now_mono,
                            "accumulated_transactions": float(
                                state.get("accumulated_transactions", 0.0)
                            )
                            + max(0.0, transaction_delta),
                            "accumulated_calls": float(
                                state.get("accumulated_calls", 0.0)
                            )
                            + max(0.0, calls_delta),
                            "counter_reset_count": float(
                                state.get("counter_reset_count", 0.0)
                            )
                            + (1.0 if counter_reset else 0.0),
                        }
                    )
                previous_transactions = transactions
                previous_calls = calls
                previous_mono = now_mono
            except (psycopg2.Error, OSError) as exc:
                error_row = {
                    "epoch_seconds": time.time(),
                    "monotonic_seconds": time.monotonic(),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
                append_jsonl(errors_path, error_row)
                with state_lock:
                    state.update(
                        {
                            "error_count": float(state.get("error_count", 0.0))
                            + 1.0,
                            "last_error": str(exc),
                            "last_error_mono": time.monotonic(),
                        }
                    )
                if connection is not None:
                    try:
                        connection.close()
                    except Exception:
                        pass
                connection = None
    finally:
        if connection is not None:
            connection.close()


def terminate_group(process: subprocess.Popen[Any], timeout_s: float = 30.0) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=timeout_s)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    process.wait(timeout=10.0)


def terminate_owned_runtime_processes(
    runtime_root: Path,
    *,
    timeout_s: float = 15.0,
) -> Dict[str, Any]:
    """Release detached workload children whose cwd/cmdline belongs to one point."""
    root = runtime_root.resolve()
    own_pid = os.getpid()
    own_group = os.getpgrp()

    def matching_processes() -> List[Tuple[int, int]]:
        matches: List[Tuple[int, int]] = []
        for proc_dir in Path("/proc").iterdir():
            if not proc_dir.name.isdigit():
                continue
            pid = int(proc_dir.name)
            if pid in (1, own_pid):
                continue
            try:
                command = (proc_dir / "cmdline").read_bytes().replace(b"\0", b" ").decode(
                    "utf-8", errors="replace"
                )
                cwd = (proc_dir / "cwd").resolve()
                cwd_owned = cwd == root or root in cwd.parents
                command_owned = str(root) in command
                if not (cwd_owned or command_owned):
                    continue
                process_group = os.getpgid(pid)
            except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
                continue
            if process_group == own_group:
                continue
            matches.append((pid, process_group))
        return matches

    initial = matching_processes()
    groups = sorted({group for _, group in initial if group > 1})
    for process_group in groups:
        try:
            os.killpg(process_group, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + timeout_s
    remaining = matching_processes()
    while remaining and time.monotonic() < deadline:
        time.sleep(0.2)
        remaining = matching_processes()
    if remaining:
        for process_group in sorted({group for _, group in remaining if group > 1}):
            try:
                os.killpg(process_group, signal.SIGKILL)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + 5.0
        while remaining and time.monotonic() < deadline:
            time.sleep(0.1)
            remaining = matching_processes()
    return {
        "runtime_root": str(root),
        "matched_pids": sorted(pid for pid, _ in initial),
        "process_groups": groups,
        "remaining_pids": sorted(pid for pid, _ in remaining),
        "fully_released": not remaining,
    }


def newest_log(output_dir: Path, suffix: str) -> Optional[Path]:
    paths = list((output_dir / "benchbase_runtime").glob(f"**/*{suffix}"))
    return max(paths, key=lambda path: path.stat().st_mtime_ns, default=None)


def sensitive_progress(path: Optional[Path]) -> Tuple[float, float]:
    if path is None or not path.is_file():
        return 0.0, 0.0
    text = path.read_text(encoding="utf-8", errors="replace")
    matches = re.findall(
        r"progress:\s+[0-9.]+\s+s,\s+([0-9.]+)\s+tps,\s+lat\s+([0-9.]+)\s+ms",
        text,
    )
    if not matches:
        return 0.0, 0.0
    tps_values = [float(item[0]) for item in matches]
    latency_values = [float(item[1]) for item in matches]
    return statistics.fmean(tps_values), statistics.fmean(latency_values)


def percentile(values: Sequence[float], ratio: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * ratio) - 1))
    return float(ordered[index])


def r0_profile_window_means_pct(
    assessment: Any,
    *,
    end_elapsed_seconds: int,
) -> Dict[str, float]:
    """Read all-resource six-window means for the R0 construction gate.

    Early-stop stability may intentionally be target-only, but the R0 profile
    gate always requires CPU, Memory and IO.  Reusing the target-only stability
    field list would turn the two non-target resources into missing values.
    """
    if isinstance(assessment, Mapping):
        payload = dict(assessment)
    elif hasattr(assessment, "to_dict"):
        payload = dict(assessment.to_dict())
    else:
        raise TypeError("assessment must be a mapping or expose to_dict()")
    matching = [
        item
        for item in (payload.get("cross_window_resource_checks") or [])
        if isinstance(item, Mapping)
        and int(item.get("end_elapsed_seconds") or 0) == int(end_elapsed_seconds)
    ]
    if not matching:
        return {}
    result: Dict[str, float] = {}
    for item in matching[-1].get("fields") or []:
        if not isinstance(item, Mapping):
            continue
        field = str(item.get("field") or "")
        if field not in {
            "cpu_utilization",
            "memory_utilization",
            "io_utilization",
        }:
            continue
        try:
            value = float(item.get("mean_of_window_means"))
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and value >= 0.0:
            result[field.rsplit("_utilization", 1)[0]] = value * 100.0
    return result


def run_point() -> int:
    args = parse_args()
    if args.r0_target_resource is not None:
        validate_benchmark_target_resource("tpcc", args.r0_target_resource)
    stability_target_resource = (
        args.stability_target_resource or args.r0_target_resource
    )
    stability_metric_fields = (
        (f"{stability_target_resource}_utilization",)
        if stability_target_resource is not None
        else ("cpu_utilization", "memory_utilization", "io_utilization")
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
    run_token = f"point_{time.strftime('%Y%m%d_%H%M%S')}_{os.getpid()}"
    output_dir = (args.output_root / run_token).resolve()
    output_dir.mkdir(parents=True, mode=0o700)
    _FAILURE_CONTEXT.update(
        {
            "output_dir": output_dir,
            "run_token": run_token,
            "samples": [],
            "stage": "manifest",
        }
    )
    args.db_lease_lock.parent.mkdir(parents=True, exist_ok=True)
    lease_handle = args.db_lease_lock.open("a+", encoding="utf-8")
    try:
        fcntl.flock(lease_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        lease_handle.close()
        raise RuntimeError(
            f"TPC-C database lane is already leased: {args.db_lease_lock}"
        ) from exc
    _FAILURE_CONTEXT["db_lease_handle"] = lease_handle
    _FAILURE_CONTEXT["db_lease_lock"] = str(args.db_lease_lock)
    (output_dir / "run_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "campaign_id": "pg5_common_outer_bgallowed_20260722_v1",
                "benchmark": "tpcc",
                "workload": "45/43/4/4/4 + 32 native + 1 sensitive",
                "seed": args.seed,
                "timing": {
                    "warmup": 60,
                    "minimum": 180,
                    "initial_probe": 300,
                    "maximum": 600,
                },
                "resource_stability": "CPU/Memory/IO six-window mean CV <= 10%",
                "throughput_stability_required": False,
                "db_qps": "pg_stat_statements sum(calls) delta / second",
                "background_load_allowed": True,
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
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    _FAILURE_CONTEXT["stage"] = "warmup"
    warmup = subprocess.run(
        workload_command(args, output_dir / "warmup", 60),
        shell=True,
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )
    (output_dir / "warmup.log").write_text(
        (warmup.stdout or "") + (warmup.stderr or ""), encoding="utf-8"
    )
    if warmup.returncode != 0:
        raise RuntimeError(f"TPC-C warmup failed: returncode={warmup.returncode}")

    _FAILURE_CONTEXT["stage"] = "database_counter_reset"
    connection = connect_database(args)
    connection.autocommit = True
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_stat_statements_reset()")
    initial_transactions, initial_calls = database_counters(connection)
    connection.close()
    cgroup_paths = _find_cgroup_paths(args.container)
    measurement_log = (output_dir / "measurement.log").open("w", encoding="utf-8")
    process = subprocess.Popen(
        workload_command(args, output_dir / "measurement", frozen.maximum_measurement_seconds),
        shell=True,
        stdout=measurement_log,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    started_epoch = time.time()
    started_mono = time.monotonic()
    previous_mono = started_mono
    previous_snapshot = _cgroup_snapshot(
        cgroup_paths, memory_mode="working_set"
    )
    counter_state: Dict[str, float] = {
        "combined_tps": 0.0,
        "db_qps": 0.0,
        "transactions": initial_transactions,
        "calls": initial_calls,
        "last_update_mono": started_mono,
        "accumulated_transactions": 0.0,
        "accumulated_calls": 0.0,
        "counter_reset_count": 0.0,
        "error_count": 0.0,
        "reconnect_count": 0.0,
    }
    counter_lock = threading.Lock()
    counter_stop = threading.Event()
    counter_samples_path = output_dir / "database_counter_samples.jsonl"
    counter_errors_path = output_dir / "database_counter_errors.jsonl"
    counter_thread = threading.Thread(
        target=run_counter_sampler,
        kwargs={
            "args": args,
            "stop_event": counter_stop,
            "state": counter_state,
            "state_lock": counter_lock,
            "samples_path": counter_samples_path,
            "errors_path": counter_errors_path,
            "initial_transactions": initial_transactions,
            "initial_calls": initial_calls,
        },
        name="tpcc-database-counter-sampler",
    )
    counter_thread.start()
    samples: List[Dict[str, Any]] = []
    _FAILURE_CONTEXT["samples"] = samples
    _FAILURE_CONTEXT["counter_state"] = counter_state
    _FAILURE_CONTEXT["stage"] = "measurement"
    samples_path = output_dir / "resource_samples.jsonl"
    assessment = None
    window_mean_stability = None
    r0_profile_gate_passed: Optional[bool] = None
    r0_profile_gate_checks: List[Dict[str, Any]] = []
    stop_reason = "hard_maximum"
    intentionally_terminated = False
    worker_cleanup: Dict[str, Any] = {
        "runtime_root": str(output_dir / "measurement" / "benchbase_runtime"),
        "matched_pids": [],
        "process_groups": [],
        "remaining_pids": [],
        "fully_released": False,
    }
    next_decision_seconds = frozen.minimum_measurement_seconds
    try:
        for sample_index in range(1, frozen.maximum_measurement_seconds + 1):
            deadline = started_mono + sample_index
            time.sleep(max(0.0, deadline - time.monotonic()))
            now_mono = time.monotonic()
            now_epoch = time.time()
            snapshot = _cgroup_snapshot(
                cgroup_paths, memory_mode="working_set"
            )
            delta_s = max(1e-6, now_mono - previous_mono)
            cpu_cores = max(0.0, snapshot["cpu_ns"] - previous_snapshot["cpu_ns"]) / (
                delta_s * 1e9
            )
            io_read_bytes_s = max(
                0.0,
                snapshot["io_read_bytes"] - previous_snapshot["io_read_bytes"],
            ) / delta_s
            io_write_bytes_s = max(
                0.0,
                snapshot["io_write_bytes"] - previous_snapshot["io_write_bytes"],
            ) / delta_s
            io_read_cap_bytes_s = io_read_cap_mib_s * 1024.0 * 1024.0
            io_write_cap_bytes_s = io_write_cap_mib_s * 1024.0 * 1024.0
            io_total_cap_bytes_s = args.io_total_limit_mib_s * 1024.0 * 1024.0
            with counter_lock:
                combined_tps = float(counter_state["combined_tps"])
                db_qps = float(counter_state["db_qps"])
                counter_age_s = max(
                    0.0, now_mono - float(counter_state["last_update_mono"])
                )
            row = {
                "epoch_seconds": now_epoch,
                "cpu_utilization": cpu_cores / args.cpu_limit_cores,
                "memory_utilization": snapshot["memory_bytes"] / args.memory_limit_bytes,
                "io_utilization": (
                    io_read_bytes_s + io_write_bytes_s
                ) / io_total_cap_bytes_s,
                "qps": combined_tps,
                "xap_qps": combined_tps,
                "xtp_tps": combined_tps,
                "db_qps": db_qps,
                "database_counter_age_seconds": counter_age_s,
                "raw_cpu_cores": cpu_cores,
                "raw_memory_bytes": snapshot["memory_bytes"],
                "raw_io_read_bytes_s": io_read_bytes_s,
                "raw_io_write_bytes_s": io_write_bytes_s,
                "raw_io_bytes_s": io_read_bytes_s + io_write_bytes_s,
                "io_total_cap_bytes_s": io_total_cap_bytes_s,
                "io_read_cap_bytes_s": io_read_cap_bytes_s,
                "io_write_cap_bytes_s": io_write_cap_bytes_s,
            }
            samples.append(row)
            append_jsonl(samples_path, row)
            previous_mono = now_mono
            previous_snapshot = snapshot
            elapsed = now_mono - started_mono
            with counter_lock:
                counter_error_count = int(counter_state.get("error_count", 0.0))
                counter_reset_count = int(
                    counter_state.get("counter_reset_count", 0.0)
                )
            if counter_error_count > 0 or counter_reset_count > 0:
                stop_reason = "database_counter_continuity_lost"
                intentionally_terminated = True
                break
            if elapsed >= next_decision_seconds:
                decision_checkpoint_seconds = int(next_decision_seconds)
                # Never jump directly from 180 seconds to the hard maximum.
                # One controller transition owns exactly one 30-second step.
                next_decision_seconds += frozen.stability_window_seconds
                # Throughput is recorded for outer acceptance but does not gate
                # early stopping. Only CPU/Memory/IO must be stable.
                assessment = assess_measurement_samples(
                    samples,
                    measurement_start_epoch_seconds=started_epoch,
                    now_epoch_seconds=now_epoch,
                    xp=True,
                    protocol=frozen,
                )
                window_mean_stability = evaluate_six_window_mean_stability(
                    assessment,
                    stability_metric_fields,
                    coefficient_of_variation_max=0.10,
                )
                checkpoint_groups = [
                    group
                    for group in window_mean_stability["groups"]
                    if int(group.get("end_elapsed_seconds") or 0)
                    == decision_checkpoint_seconds
                ]
                latest_group = checkpoint_groups[-1] if checkpoint_groups else None
                resources_stable = bool(latest_group and latest_group.get("stable"))
                if resources_stable and args.r0_target_resource is not None:
                    latest_means = r0_profile_window_means_pct(
                        assessment,
                        end_elapsed_seconds=decision_checkpoint_seconds,
                    )
                    r0_profile_gate_passed, r0_profile_gate_checks = (
                        evaluate_r0_profile_construction_gate(
                            latest_means,
                            args.r0_target_resource,
                        )
                    )
                    if not r0_profile_gate_passed:
                        stop_reason = "r0_profile_reconstruct_early_stop"
                        intentionally_terminated = True
                        break
                if resources_stable:
                    stop_reason = "stable_early_stop"
                    intentionally_terminated = True
                    break
            if process.poll() is not None:
                stop_reason = "workload_exited_before_hard_maximum"
                break
    finally:
        counter_stop.set()
        counter_thread.join(timeout=90.0)
        if process.poll() is None:
            if stop_reason == "hard_maximum":
                try:
                    process.wait(timeout=120.0)
                except subprocess.TimeoutExpired:
                    intentionally_terminated = True
                    terminate_group(process)
            else:
                terminate_group(process)
        returncode = process.wait()
        worker_cleanup = terminate_owned_runtime_processes(
            output_dir / "measurement" / "benchbase_runtime"
        )
        measurement_log.close()

    if counter_thread.is_alive():
        raise RuntimeError("TPC-C database counter sampler did not stop")

    _FAILURE_CONTEXT["stage"] = "finalize"
    final_epoch = time.time()
    elapsed = min(float(frozen.maximum_measurement_seconds), time.monotonic() - started_mono)
    if assessment is None:
        assessment = assess_measurement_samples(
            samples,
            measurement_start_epoch_seconds=started_epoch,
            now_epoch_seconds=started_epoch + elapsed,
            xp=True,
            protocol=frozen,
        )
    if window_mean_stability is None:
        window_mean_stability = evaluate_six_window_mean_stability(
            assessment,
            stability_metric_fields,
            coefficient_of_variation_max=0.10,
        )

    native_log = newest_log(output_dir / "measurement", ".native.log")
    sensitive_log = newest_log(output_dir / "measurement", ".sensitive.log")
    sensitive_tps, sensitive_latency_ms = sensitive_progress(sensitive_log)
    with counter_lock:
        accumulated_transactions = float(
            counter_state.get("accumulated_transactions", 0.0)
        )
        accumulated_calls = float(counter_state.get("accumulated_calls", 0.0))
        counter_error_count = int(counter_state.get("error_count", 0.0))
        counter_reset_count = int(counter_state.get("counter_reset_count", 0.0))
        counter_reconnect_count = int(counter_state.get("reconnect_count", 0.0))
        counter_last_error = str(counter_state.get("last_error", ""))
    combined_tps = accumulated_transactions / max(1e-9, elapsed)
    native_tps = max(0.0, combined_tps - sensitive_tps)
    db_qps = accumulated_calls / max(1e-9, elapsed)
    native_text = (
        native_log.read_text(encoding="utf-8", errors="replace")
        if native_log is not None and native_log.is_file()
        else ""
    )
    native_log_present = bool(
        native_log is not None
        and native_log.is_file()
        and native_log.stat().st_size > 0
    )
    native_log_sha256 = (
        hashlib.sha256(native_log.read_bytes()).hexdigest()
        if native_log_present and native_log is not None
        else None
    )
    sql_exception_count = native_text.count("SQLException occurred")
    status = "complete"
    if elapsed < frozen.minimum_measurement_seconds:
        status = "failed"
    if returncode not in (0, -signal.SIGTERM, -signal.SIGKILL) and not intentionally_terminated:
        status = "failed"
    if not window_mean_stability["stable"]:
        status = "failed"
    counter_continuity_passed = (
        counter_error_count == 0 and counter_reset_count == 0
    )
    if not counter_continuity_passed:
        status = "failed"
    if not worker_cleanup["fully_released"]:
        status = "failed"
    if not native_log_present or sql_exception_count != 0:
        status = "failed"
    worker_cleanup["counter_thread_stopped"] = not counter_thread.is_alive()
    resource_utilization = {
        "cpu": statistics.fmean(row["cpu_utilization"] for row in samples),
        "memory": statistics.fmean(row["memory_utilization"] for row in samples),
        "io": statistics.fmean(row["io_utilization"] for row in samples),
    }
    r0_gate_passed: Optional[bool] = None
    r0_gate_checks: List[Dict[str, Any]] = []
    if args.r0_target_resource is not None:
        r0_gate_passed, r0_gate_checks = evaluate_r0_single_bottleneck_gate(
            {name: value * 100.0 for name, value in resource_utilization.items()},
            args.r0_target_resource,
        )
        if not r0_gate_passed:
            status = "failed"
        if r0_profile_gate_passed is None:
            r0_profile_gate_passed, r0_profile_gate_checks = (
                evaluate_r0_profile_construction_gate(
                    {name: value * 100.0 for name, value in resource_utilization.items()},
                    args.r0_target_resource,
                )
            )
        if not r0_profile_gate_passed:
            status = "failed"
    summary = {
        "schema_version": 1,
        "status": status,
        "stop_reason": stop_reason,
        "effective_measurement_seconds": elapsed,
        "workload_returncode": returncode,
        "intentionally_terminated_after_stability": intentionally_terminated,
        "native_tps": native_tps,
        "sensitive_tps": sensitive_tps,
        "combined_tps": combined_tps,
        "db_qps": db_qps,
        "sensitive_latency_ms": sensitive_latency_ms,
        "native_sql_exception_occurrences": sql_exception_count,
        "native_log_sha256": native_log_sha256,
        "worker_cleanup": worker_cleanup,
        "database_counter_continuity": {
            "passed": counter_continuity_passed,
            "error_count": counter_error_count,
            "counter_reset_count": counter_reset_count,
            "reconnect_count": counter_reconnect_count,
            "last_error": counter_last_error or None,
            "samples_path": str(counter_samples_path),
            "errors_path": (
                str(counter_errors_path) if counter_errors_path.is_file() else None
            ),
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
        "measurement_assessment": assessment.to_dict(),
        "window_mean_stability": window_mean_stability,
        "stability_target_resource": stability_target_resource,
        "measurement_cutoff_epoch": final_epoch,
        "native_log": str(native_log) if native_log is not None else None,
        "sensitive_log": str(sensitive_log) if sensitive_log is not None else None,
    }
    summary_path = output_dir / "summary.json"
    summary_bytes = (
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    summary_path.write_bytes(summary_bytes)
    summary_sha256 = hashlib.sha256(summary_bytes).hexdigest()
    runner_sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    metrics = {
        "native_tps": native_tps,
        "sensitive_tps": sensitive_tps,
        "combined_tps": combined_tps,
        "sensitive_latency_ms": sensitive_latency_ms,
        "native_terminals": 32,
        "sensitive_clients": 1,
        "elapsed_s": elapsed,
    }
    aggregate = {
        "cpu_utilization": resource_utilization["cpu"],
        "memory_utilization": resource_utilization["memory"],
        "io_utilization": resource_utilization["io"],
        "read_bps": statistics.fmean(row["raw_io_read_bytes_s"] for row in samples),
        "write_bps": statistics.fmean(row["raw_io_write_bytes_s"] for row in samples),
        "total_bps": statistics.fmean(row["raw_io_bytes_s"] for row in samples),
        "memory_working_set_bytes": statistics.fmean(
            row["raw_memory_bytes"] for row in samples
        ),
    }
    formal_resource = {
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
    }
    marker = {
        "benchmark": {
            "xp_qps": combined_tps,
            "xp_tps": combined_tps,
            "tp_tps": combined_tps,
            "db_qps": db_qps,
            "native_tps": native_tps,
            "sensitive_tps": sensitive_tps,
        },
        "benchmark_latency": {
            "all_p90_avg": sensitive_latency_ms,
            "all_p90_max": sensitive_latency_ms,
            "all_p95_avg": sensitive_latency_ms,
            "all_p95_max": sensitive_latency_ms,
            "all_p99_avg": sensitive_latency_ms,
            "all_p99_max": sensitive_latency_ms,
        },
        "formal_pg_resource": formal_resource,
        "candidate_measurement_protocol": {
            "correctness_passed": status == "complete",
            "stable": bool(window_mean_stability["stable"]),
            "workload_contract": "tpcc_sensitive_stable_summary_v2",
            "summary_schema": "tpcc_sensitive_summary_v1",
            "point_id": output_dir.name,
            "stop_reason": stop_reason,
            "effective_measurement_seconds": elapsed,
            "minimum_measurement_seconds": frozen.minimum_measurement_seconds,
            "initial_probe_seconds": frozen.minimum_measurement_seconds,
            "maximum_measurement_seconds": frozen.maximum_measurement_seconds,
            "throughput_stability_required": False,
            "complete_window_count": assessment.complete_window_count,
            "stability_contract_sha256": PG5_STABILITY_GATE_SHA256,
            "summary_path": str(summary_path),
            "summary_sha256": summary_sha256,
            "runner_sha256": runner_sha256,
            "qps_used_for_inner_scoring": False,
            "stability_target_resource": stability_target_resource,
        },
    }
    print("TPCC_SENSITIVE_METRICS_JSON=" + json.dumps(metrics, sort_keys=True), flush=True)
    print(
        "RESOURCE_ADAPTIVE_BENCHMARK_JSON="
        + json.dumps(marker, ensure_ascii=False, sort_keys=True),
        flush=True,
    )
    print(f"XP-QPS is {combined_tps:.12g}", flush=True)
    print(f"XP-TPS is {combined_tps:.12g}", flush=True)
    print(
        "Analytical Transaction generic : "
        f"max rt : {sensitive_latency_ms:.6f} | min rt : 0.000000 | "
        f"avg rt : {sensitive_latency_ms:.6f} | 90% rt : {sensitive_latency_ms:.6f} | "
        f"95% rt : {sensitive_latency_ms:.6f} | 99% rt : {sensitive_latency_ms:.6f}",
        flush=True,
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True), flush=True)
    return 0 if status == "complete" else 2


def persist_failure_summary(exc: BaseException) -> Optional[Path]:
    """Persist diagnostics even when a point fails before the normal summary."""
    output_dir = _FAILURE_CONTEXT.get("output_dir")
    if not isinstance(output_dir, Path):
        return None
    summary_path = output_dir / "summary.json"
    if summary_path.is_file():
        return summary_path
    samples = [
        item
        for item in (_FAILURE_CONTEXT.get("samples") or [])
        if isinstance(item, Mapping)
    ]
    resource_utilization: Dict[str, Optional[float]] = {}
    for resource in ("cpu", "memory", "io"):
        field = f"{resource}_utilization"
        values = [
            float(item[field])
            for item in samples
            if isinstance(item.get(field), (int, float))
            and math.isfinite(float(item[field]))
        ]
        resource_utilization[resource] = (
            statistics.fmean(values) if values else None
        )
    counter_state = _FAILURE_CONTEXT.get("counter_state")
    counter_diagnostics: Dict[str, Any] = {}
    if isinstance(counter_state, Mapping):
        counter_diagnostics = {
            "error_count": int(counter_state.get("error_count", 0.0)),
            "counter_reset_count": int(
                counter_state.get("counter_reset_count", 0.0)
            ),
            "reconnect_count": int(counter_state.get("reconnect_count", 0.0)),
            "last_error": counter_state.get("last_error"),
        }
    payload = {
        "schema_version": 1,
        "status": "failed",
        "stop_reason": "runner_exception",
        "failure_stage": _FAILURE_CONTEXT.get("stage"),
        "error_type": type(exc).__name__,
        "error": str(exc),
        "traceback": traceback.format_exc(),
        "resource_utilization": resource_utilization,
        "sample_count": len(samples),
        "database_counter_continuity": {
            "passed": False,
            **counter_diagnostics,
        },
    }
    temporary = summary_path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(summary_path)
    return summary_path


def main() -> int:
    try:
        return run_point()
    except Exception as exc:
        summary_path = persist_failure_summary(exc)
        suffix = f"; summary={summary_path}" if summary_path is not None else ""
        print(
            f"TPC-C stable point failed: {type(exc).__name__}: {exc}{suffix}",
            file=sys.stderr,
            flush=True,
        )
        return 2
    finally:
        lease_handle = _FAILURE_CONTEXT.pop("db_lease_handle", None)
        if lease_handle is not None:
            try:
                fcntl.flock(lease_handle.fileno(), fcntl.LOCK_UN)
            finally:
                lease_handle.close()


if __name__ == "__main__":
    raise SystemExit(main())
