#!/usr/bin/env python3
"""Execute resumable attempt16 TPC-C SET LOCAL direction calibration."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
from pathlib import Path
import signal
import statistics
import subprocess
import sys
import time
from typing import Any, Dict, Mapping, Optional, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tpcc_session_local_calibration import (
    PARAMETERS,
    canonical_params,
    prepare_bundle,
    seal_calibration,
    sha256,
    snapshot_defaults,
    write_json_new,
)


ATTEMPT_NAMESPACE = "attempt16_setlocal_direction_calibration"
RESTORE_SHA256 = "1af8eca4b8fbd229f525b627293eb58a51aa3510472c97340b5ea317be5cde59"
FROZEN_DATABASE = {
    "container": "pg5_tpcc_d2_20260722",
    "host": "127.0.0.1",
    "port": 59640,
    "database": "fullgrid_tpcc",
    "user": "postgres",
}
FROZEN_PROFILE = {
    "cpu_limit_cores": 2.05,
    "memory_limit_bytes": 4294967296,
    "io_device": "259:6",
    "io_read_cap_mib_s": 13.5,
    "io_write_cap_mib_s": 13.5,
    "io_total_limit_mib_s": 27.0,
    "cpuset_cpus": "104-107,360-363",
}
R0_PROFILE_BANDS = {
    "cpu": (0.85, 0.90),
    "memory": (0.30, 0.50),
    "io": (0.30, 0.50),
}
DEFAULT_PARAMS = {
    "seq_page_cost": 1.0,
    "random_page_cost": 4.0,
    "cpu_tuple_cost": 0.01,
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0025,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.1,
}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def append_jsonl(path: Path, value: Mapping[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(value), ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()


def read_json(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"expected JSON object: {path}")
    return value


def frozen_campaign_contract(args: argparse.Namespace) -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "artifact_type": "tpcc_setlocal_direction_attempt_manifest_v1",
        "namespace": ATTEMPT_NAMESPACE,
        "restore": {
            "path": str(args.restore_command.resolve()),
            "sha256": RESTORE_SHA256,
        },
        "profile": {
            "container": args.container,
            "cpu_limit_cores": args.cpu_limit_cores,
            "memory_limit_bytes": args.memory_limit_bytes,
            "io_device": args.io_device,
            "io_read_cap_mib_s": args.io_read_cap_mib_s,
            "io_write_cap_mib_s": args.io_write_cap_mib_s,
            "io_total_limit_mib_s": args.io_total_limit_mib_s,
            "cpuset_cpus": args.cpuset_cpus,
        },
        "database": {
            "host": args.host,
            "port": args.port,
            "database": args.database,
            "user": args.user,
        },
        "workload_artifacts": {
            "benchbase_xml": str(args.benchbase_xml.resolve()),
            "benchbase_xml_sha256": sha256(args.benchbase_xml),
            "benchbase_jar": str(args.benchbase_jar.resolve()),
            "benchbase_jar_sha256": sha256(args.benchbase_jar),
            "postgresql_jdbc_jar": str(args.postgresql_jdbc_jar.resolve()),
            "postgresql_jdbc_jar_sha256": sha256(args.postgresql_jdbc_jar),
        },
        "calibration": {
            "scopes": args.scopes,
            "baseline_only": args.baseline_only,
            "endpoint_count_per_scope": 14,
            "factors": [1.25, 1.5, 2.0][: args.scopes],
            "native_terminals": args.native_terminals,
            "sensitive_clients": args.sensitive_clients,
        },
        "measurement": {
            "warmup_seconds": args.warmup_seconds,
            "sample_interval_seconds": args.sample_interval_seconds,
            "window_seconds": args.window_seconds,
            "required_windows": args.required_windows,
            "coverage_min": args.coverage_min,
            "cv_max": args.cv_max,
            "minimum_seconds": args.minimum_measurement_seconds,
            "maximum_seconds": args.maximum_measurement_seconds,
        },
        "parameter_contract": "SET_LOCAL_plus_immediate_readback",
        "alter_system_forbidden": True,
    }


def initialize_attempt(args: argparse.Namespace) -> Mapping[str, Any]:
    output_root = args.output_root.resolve()
    if ATTEMPT_NAMESPACE not in output_root.name:
        raise ValueError(
            f"output root basename must contain {ATTEMPT_NAMESPACE!r}: {output_root}"
        )
    if "attempt15" in str(output_root):
        raise ValueError("attempt16 output must not be nested in attempt15 evidence")
    contract = frozen_campaign_contract(args)
    manifest_path = output_root / "attempt_manifest.json"
    if args.resume:
        if not manifest_path.is_file():
            raise ValueError("resume requires an existing attempt manifest")
        existing = read_json(manifest_path)
        if existing != contract:
            raise ValueError("resume contract differs from the frozen attempt16 manifest")
    else:
        if output_root.exists():
            raise ValueError(f"new attempt output already exists: {output_root}")
        output_root.mkdir(parents=True)
        write_json_new(manifest_path, contract)
    return contract


def calibration_design(baseline: Mapping[str, Any], scope_index: int) -> list[Dict[str, Any]]:
    factor = (1.25, 1.5, 2.0)[scope_index - 1]
    rows = []
    for parameter in PARAMETERS:
        value = float(baseline[parameter])
        for direction, candidate_value in (
            ("down", value / factor),
            ("up", value * factor),
        ):
            params = dict(baseline)
            params[parameter] = candidate_value
            rows.append(
                {
                    "calibration_id": f"scope{scope_index:02d}_{parameter}_{direction}",
                    "parameter": parameter,
                    "direction": direction,
                    "params": params,
                    "factor": factor,
                }
            )
    return rows


def completed_ids(path: Path) -> set[str]:
    statuses: Dict[str, str] = {}
    if not path.is_file():
        return set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        calibration_id = str(row.get("calibration_id") or "")
        status = str(row.get("status") or "")
        if calibration_id and status in {"usable", "technical_failure"}:
            statuses[calibration_id] = status
    return set(statuses)


def assess_stability(
    samples: Sequence[Mapping[str, float]],
    *,
    sample_interval_seconds: float,
    window_seconds: float,
    required_windows: int,
    coverage_min: float,
    cv_max: float,
) -> Dict[str, Any]:
    samples_per_window = int(round(window_seconds / sample_interval_seconds))
    required_samples = samples_per_window * required_windows
    latest_elapsed = float(samples[-1].get("elapsed_seconds") or 0) if samples else 0
    complete_windows = int(latest_elapsed // window_seconds)
    if samples_per_window <= 0 or complete_windows < required_windows:
        return {
            "stable": False,
            "reason": "insufficient_samples",
            "required_samples": required_samples,
            "observed_samples": len(samples),
        }
    first_window = complete_windows - required_windows
    fields = ("cpu_utilization",)
    checks = []
    all_passed = True
    for field in fields:
        window_means = []
        coverages = []
        for index in range(required_windows):
            start = (first_window + index) * window_seconds
            end = start + window_seconds
            window = [
                row
                for row in samples
                if start < float(row.get("elapsed_seconds") or 0) <= end
            ]
            finite = [
                float(row[field])
                for row in window
                if field in row and math.isfinite(float(row[field]))
            ]
            coverage = len(finite) / samples_per_window
            coverages.append(coverage)
            if not finite:
                window_means.append(float("nan"))
            else:
                window_means.append(statistics.fmean(finite))
        finite_means = [value for value in window_means if math.isfinite(value)]
        mean = statistics.fmean(finite_means) if finite_means else float("nan")
        cv = (
            statistics.pstdev(finite_means) / abs(mean)
            if finite_means and mean != 0
            else float("inf")
        )
        passed = (
            len(finite_means) == required_windows
            and min(coverages) >= coverage_min
            and cv <= cv_max
        )
        all_passed = all_passed and passed
        checks.append(
            {
                "field": field,
                "window_means": window_means,
                "window_coverages": coverages,
                "coefficient_of_variation": cv,
                "passed": passed,
            }
        )
    return {
        "mode": "consecutive_window_mean_cv_and_coverage",
        "stable": all_passed,
        "required_window_count": required_windows,
        "window_seconds": window_seconds,
        "coverage_min": coverage_min,
        "coefficient_of_variation_max": cv_max,
        "field_checks": checks,
}


def workload_cwd(kind: str, args: argparse.Namespace, runtime_dir: Path) -> Path:
    if kind == "native":
        return args.benchbase_jar.parent
    return runtime_dir


class RealBackend:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        try:
            from system_resource_monitor import _cgroup_snapshot, _find_cgroup_paths
        except ImportError as exc:
            raise RuntimeError("Docker cgroup sampler is unavailable") from exc
        self._snapshot = _cgroup_snapshot
        self._find_paths = _find_cgroup_paths
        self._paths: Mapping[str, Any] = {}
        self._counter_connection: Any = None

    def restore(self) -> None:
        if sha256(self.args.restore_command) != RESTORE_SHA256:
            raise RuntimeError("attempt15 restore command SHA256 mismatch")
        subprocess.run([str(self.args.restore_command.resolve())], check=True)

    def verify_profile(self) -> Mapping[str, Any]:
        self._paths = self._find_paths(self.args.container)
        result = subprocess.run(
            ["docker", "inspect", self.args.container],
            check=True,
            capture_output=True,
            text=True,
        )
        document = json.loads(result.stdout)
        host = document[0]["HostConfig"]
        actual = {
            "cpu_limit_cores": int(host.get("NanoCpus") or 0) / 1e9,
            "memory_limit_bytes": int(host.get("Memory") or 0),
            "cpuset_cpus": str(host.get("CpusetCpus") or ""),
        }
        expected = {
            "cpu_limit_cores": self.args.cpu_limit_cores,
            "memory_limit_bytes": self.args.memory_limit_bytes,
            "cpuset_cpus": self.args.cpuset_cpus,
        }
        if actual != expected:
            raise RuntimeError(f"Docker profile mismatch: expected={expected}, actual={actual}")
        io_actual = self._read_io_limits()
        io_expected = {
            "io_device": self.args.io_device,
            "io_read_bps": int(self.args.io_read_cap_mib_s * 1024 ** 2),
            "io_write_bps": int(self.args.io_write_cap_mib_s * 1024 ** 2),
        }
        if io_actual != io_expected:
            raise RuntimeError(
                f"Docker I/O profile mismatch: expected={io_expected}, actual={io_actual}"
            )
        return {**actual, **io_actual}

    def _read_io_limits(self) -> Mapping[str, Any]:
        if self._paths.get("version") == "v1":
            io_value = self._paths.get("io_bytes")
            if not io_value:
                raise RuntimeError("cgroup v1 I/O accounting path is unavailable")
            io_path = Path(str(io_value))
            if not io_path.is_file():
                raise RuntimeError(f"cgroup v1 I/O accounting path is missing: {io_path}")
            cgroup_dir = io_path.parent

            def read_device_limit(name: str) -> int:
                path = cgroup_dir / name
                for line in path.read_text(encoding="utf-8").splitlines():
                    fields = line.split()
                    if len(fields) == 2 and fields[0] == self.args.io_device:
                        return int(fields[1])
                raise RuntimeError(f"I/O device {self.args.io_device} missing from {path}")

            return {
                "io_device": self.args.io_device,
                "io_read_bps": read_device_limit("blkio.throttle.read_bps_device"),
                "io_write_bps": read_device_limit("blkio.throttle.write_bps_device"),
            }
        io_value = self._paths.get("io")
        if not io_value:
            raise RuntimeError("cgroup v2 I/O accounting path is unavailable")
        io_path = Path(str(io_value))
        if not io_path.is_file():
            raise RuntimeError(f"cgroup v2 I/O accounting path is missing: {io_path}")
        for line in (io_path.parent / "io.max").read_text(encoding="utf-8").splitlines():
            fields = line.split()
            if fields and fields[0] == self.args.io_device:
                values = {
                    key: value
                    for key, value in (
                        field.split("=", 1) for field in fields[1:] if "=" in field
                    )
                }
                return {
                    "io_device": self.args.io_device,
                    "io_read_bps": int(values["rbps"]),
                    "io_write_bps": int(values["wbps"]),
                }
        raise RuntimeError(f"I/O device {self.args.io_device} missing from cgroup v2 io.max")

    def snapshot_defaults(self, output: Path) -> Mapping[str, Any]:
        password = os.environ.get(self.args.password_env)
        if password is None:
            raise RuntimeError(f"password environment is missing: {self.args.password_env}")
        namespace = argparse.Namespace(
            host=self.args.host,
            port=self.args.port,
            database=self.args.database,
            user=self.args.user,
            password=password,
            output=output,
        )
        snapshot_defaults(namespace)
        return read_json(output)

    def prepare_bundle(
        self,
        point_dir: Path,
        calibration_id: str,
        params_path: Path,
    ) -> Path:
        return prepare_bundle(
            argparse.Namespace(
                output_dir=point_dir / "bundle",
                calibration_id=calibration_id,
                params=params_path,
                benchbase_xml=self.args.benchbase_xml,
                benchbase_jar=self.args.benchbase_jar,
                postgresql_jdbc_jar=self.args.postgresql_jdbc_jar,
                database_user=self.args.user,
                database_password_env=self.args.password_env,
                jdbc_url=(
                    f"jdbc:postgresql://{self.args.host}:{self.args.port}/"
                    f"{self.args.database}?sslmode=disable"
                ),
                native_terminals=self.args.native_terminals,
                sensitive_clients=self.args.sensitive_clients,
                duration_seconds=(
                    self.args.warmup_seconds + self.args.maximum_measurement_seconds
                ),
                warehouses=self.args.warehouses,
                java_xmx=self.args.java_xmx,
            )
        )

    def launch(self, manifest_path: Path, point_dir: Path) -> Mapping[str, Any]:
        manifest = read_json(manifest_path)
        contract = manifest["connection_contract"]
        password = os.environ.get(self.args.password_env)
        if password is None:
            raise RuntimeError(f"password environment is missing: {self.args.password_env}")
        processes = {}
        runtime_dir = point_dir / "bundle/runtime"
        runtime_dir.mkdir(exist_ok=True)
        (runtime_dir / "results").mkdir(exist_ok=True)
        try:
            for kind in ("native", "sensitive"):
                item = contract[kind]
                environment = os.environ.copy()
                environment.update(
                    {str(k): str(v) for k, v in item["environment"].items()}
                )
                if kind == "sensitive":
                    environment["TPCC_DB_PASSWORD"] = password
                log_path = point_dir / f"{kind}.log"
                log_handle = log_path.open("w", encoding="utf-8")
                try:
                    process_cwd = workload_cwd(kind, self.args, runtime_dir)
                    process = subprocess.Popen(
                        [str(value) for value in item["command"]],
                        cwd=str(process_cwd),
                        env=environment,
                        stdout=log_handle,
                        stderr=subprocess.STDOUT,
                        text=True,
                        start_new_session=True,
                    )
                except Exception:
                    log_handle.close()
                    raise
                processes[kind] = {
                    "process": process,
                    "log_handle": log_handle,
                    "log_path": log_path,
                }
            self._open_counter()
        except Exception:
            self.stop(processes)
            raise
        return processes

    def _open_counter(self) -> None:
        import psycopg2

        password = os.environ.get(self.args.password_env)
        self._counter_connection = psycopg2.connect(
            host=self.args.host,
            port=self.args.port,
            dbname=self.args.database,
            user=self.args.user,
            password=password,
            application_name="ra-tpcc-attempt16-counter",
            connect_timeout=10,
        )
        self._counter_connection.autocommit = True

    def db_calls(self) -> float:
        with self._counter_connection.cursor() as cursor:
            cursor.execute("SELECT COALESCE(sum(calls), 0)::double precision FROM pg_stat_statements")
            return float(cursor.fetchone()[0])

    def snapshot(self) -> Mapping[str, float]:
        return self._snapshot(self._paths, memory_mode="working_set")

    def wait_interval(self, seconds: float) -> None:
        time.sleep(seconds)

    def monotonic(self) -> float:
        return time.monotonic()

    def alive(self, processes: Mapping[str, Any]) -> bool:
        return all(item["process"].poll() is None for item in processes.values())

    def stop(self, processes: Mapping[str, Any]) -> Mapping[str, Any]:
        process_groups = []
        for item in processes.values():
            process = item["process"]
            if process.poll() is None:
                process_groups.append(process.pid)
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and any(
            item["process"].poll() is None for item in processes.values()
        ):
            time.sleep(0.1)
        for item in processes.values():
            process = item["process"]
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.wait(timeout=5)
            item["log_handle"].close()
        if self._counter_connection is not None:
            self._counter_connection.close()
            self._counter_connection = None
        return {
            "process_groups": process_groups,
            "remaining_pids": [],
            "fully_released": True,
        }


def measure_point(
    args: argparse.Namespace,
    backend: Any,
    point_dir: Path,
    processes: Mapping[str, Any],
) -> Mapping[str, Any]:
    if args.warmup_seconds > 0:
        backend.wait_interval(args.warmup_seconds)
        if not backend.alive(processes):
            backend.stop(processes)
            raise RuntimeError("native or sensitive workload exited during warmup")
    initial = backend.snapshot()
    initial_calls = backend.db_calls()
    previous = initial
    previous_calls = initial_calls
    started_mono = backend.monotonic()
    previous_mono = started_mono
    samples = []
    samples_path = point_dir / "resource_samples.jsonl"
    stability: Mapping[str, Any] = {"stable": False}
    stopped_early = False
    maximum_samples = int(
        math.ceil(args.maximum_measurement_seconds / args.sample_interval_seconds)
    )
    for _sample_index in range(1, maximum_samples + 1):
        backend.wait_interval(args.sample_interval_seconds)
        if not backend.alive(processes):
            break
        now_mono = backend.monotonic()
        elapsed = now_mono - started_mono
        interval = max(1e-9, now_mono - previous_mono)
        current = backend.snapshot()
        calls = backend.db_calls()
        cpu_cores = max(0.0, current["cpu_ns"] - previous["cpu_ns"]) / (interval * 1e9)
        io_read = max(0.0, current["io_read_bytes"] - previous["io_read_bytes"]) / interval
        io_write = max(0.0, current["io_write_bytes"] - previous["io_write_bytes"]) / interval
        row = {
            "elapsed_seconds": elapsed,
            "cpu_utilization": cpu_cores / args.cpu_limit_cores,
            "memory_utilization": current["memory_bytes"] / args.memory_limit_bytes,
            "io_utilization": (
                io_read + io_write
            ) / (args.io_total_limit_mib_s * 1024 ** 2),
            "db_qps": max(0.0, calls - previous_calls) / interval,
            "raw_cpu_cores": cpu_cores,
            "raw_memory_bytes": current["memory_bytes"],
            "raw_io_read_bytes_s": io_read,
            "raw_io_write_bytes_s": io_write,
        }
        samples.append(row)
        append_jsonl(samples_path, row)
        previous = current
        previous_calls = calls
        previous_mono = now_mono
        if row["elapsed_seconds"] >= args.minimum_measurement_seconds:
            stability = assess_stability(
                samples,
                sample_interval_seconds=args.sample_interval_seconds,
                window_seconds=args.window_seconds,
                required_windows=args.required_windows,
                coverage_min=args.coverage_min,
                cv_max=args.cv_max,
            )
            if stability.get("stable") is True:
                stopped_early = True
                break
        if elapsed >= args.maximum_measurement_seconds:
            break
    cleanup = backend.stop(processes)
    if not samples:
        raise RuntimeError("workload produced no cgroup samples")
    means = {
        resource: statistics.fmean(float(row[f"{resource}_utilization"]) for row in samples)
        for resource in ("cpu", "memory", "io")
    }
    db_qps = statistics.fmean(float(row["db_qps"]) for row in samples)
    native_log = point_dir / "native.log"
    sql_exceptions = (
        native_log.read_text(encoding="utf-8", errors="replace").count("SQLException occurred")
        if native_log.is_file()
        else 0
    )
    return {
        "status": "complete" if stability.get("stable") is True else "failed",
        "db_qps": db_qps,
        "resource_utilization": means,
        "window_mean_stability": stability,
        "worker_cleanup": cleanup,
        "native_sql_exception_occurrences": sql_exceptions,
        "effective_measurement_seconds": float(samples[-1]["elapsed_seconds"]),
        "stop_reason": "stable_early_stop" if stopped_early else "workload_exit_or_maximum",
    }


def raw_result(args: argparse.Namespace, summary: Mapping[str, Any], seal_path: Path) -> Dict[str, Any]:
    usable = (
        summary.get("status") == "complete"
        and (summary.get("window_mean_stability") or {}).get("stable") is True
        and (summary.get("worker_cleanup") or {}).get("fully_released") is True
        and int(summary.get("native_sql_exception_occurrences") or 0) == 0
    )
    return {
        "status": "ok" if usable else "failed",
        "double_loop_measurement_usable": usable,
        "workload": "tpcc_sensitive",
        "target_resource": "cpu",
        "scenario": "cpu",
        "db_qps": summary.get("db_qps"),
        "resource_utilization": summary.get("resource_utilization"),
        "runtime_capability": {
            "cpu": args.cpu_limit_cores,
            "memory": args.memory_limit_bytes / 1024 ** 3,
            "io": args.io_total_limit_mib_s,
            "memory_limit_bytes": args.memory_limit_bytes,
            "io_read_cap_mib_s": args.io_read_cap_mib_s,
            "io_write_cap_mib_s": args.io_write_cap_mib_s,
        },
        "candidate_measurement_protocol": {
            "stable": (summary.get("window_mean_stability") or {}).get("stable"),
            "worker_cleanup_passed": (summary.get("worker_cleanup") or {}).get(
                "fully_released"
            ),
            "native_sql_exception_occurrences": summary.get(
                "native_sql_exception_occurrences"
            ),
            "effective_measurement_seconds": summary.get("effective_measurement_seconds"),
            "stop_reason": summary.get("stop_reason"),
            "parameter_contract": "SET_LOCAL_plus_immediate_readback",
        },
        "session_local_calibration_seal": str(seal_path.resolve()),
        "summary_path": str((seal_path.parent / "summary.json").resolve()),
    }


def execute_point(
    args: argparse.Namespace,
    backend: Any,
    point_dir: Path,
    calibration_id: str,
    params: Mapping[str, Any],
) -> tuple[Mapping[str, Any], Path]:
    point_dir.mkdir(parents=True, exist_ok=False)
    params_path = point_dir / "candidate_params.json"
    write_json_new(params_path, canonical_params(params))
    backend.restore()
    profile = backend.verify_profile()
    write_json_new(point_dir / "runtime_profile_readback.json", dict(profile))
    before_path = point_dir / "global_defaults_before.json"
    before = backend.snapshot_defaults(before_path)
    actual_defaults = {
        name: (before.get("parameters") or {}).get(name, {}).get("setting")
        for name in PARAMETERS
    }
    if canonical_params(actual_defaults) != canonical_params(DEFAULT_PARAMS):
        raise RuntimeError(f"restored global defaults differ from frozen defaults: {actual_defaults}")
    manifest_path = backend.prepare_bundle(point_dir, calibration_id, params_path)
    processes = backend.launch(manifest_path, point_dir)
    try:
        summary = measure_point(args, backend, point_dir, processes)
    except Exception:
        try:
            backend.stop(processes)
        except Exception:
            pass
        raise
    summary_path = point_dir / "summary.json"
    write_json_new(summary_path, dict(summary))
    after_path = point_dir / "global_defaults_after.json"
    backend.snapshot_defaults(after_path)
    seal_path = point_dir / "session_local_calibration_seal.json"
    manifest = read_json(manifest_path)
    seal_calibration(
        argparse.Namespace(
            manifest=manifest_path,
            readbacks=Path(manifest["connection_contract"]["readback_path"]),
            defaults_before=before_path,
            defaults_after=after_path,
            measurement_summary=summary_path,
            output=seal_path,
        )
    )
    raw = raw_result(args, summary, seal_path)
    if raw["double_loop_measurement_usable"] is not True:
        raise RuntimeError("point measurement was not usable")
    return raw, seal_path


def technical_failure(
    calibration_id: str,
    error: Exception,
    point_dir: Path,
) -> Dict[str, Any]:
    return {
        "event": "fresh_model_calibration_replay",
        "calibration_id": calibration_id,
        "status": "technical_failure",
        "current_run_only": True,
        "used_for_optimizer_candidate_selection": False,
        "error": f"{type(error).__name__}: {error}",
        "point_dir": str(point_dir.resolve()),
        "recorded_at": utc_now(),
    }


def assess_r0_profile(raw: Mapping[str, Any]) -> Dict[str, Any]:
    resources = raw.get("resource_utilization") or {}
    checks: Dict[str, Any] = {}
    passed = True
    for resource, (lower, upper) in R0_PROFILE_BANDS.items():
        try:
            value = float(resources[resource])
        except (KeyError, TypeError, ValueError):
            value = float("nan")
        field_passed = math.isfinite(value) and lower <= value <= upper
        passed = passed and field_passed
        checks[resource] = {
            "value": value if math.isfinite(value) else None,
            "inclusive_bounds": [lower, upper],
            "passed": field_passed,
        }
    return {
        "schema_version": 1,
        "target_resource": "cpu",
        "passed": passed,
        "checks": checks,
    }


def run_campaign(args: argparse.Namespace, backend: Any) -> int:
    initialize_attempt(args)
    baseline_failure_count = 0
    for scope_index in range(1, args.scopes + 1):
        scope_dir = (
            args.output_root.resolve()
            / "calibration"
            / f"scope_{scope_index:02d}"
            / "tpcc_sensitive"
            / "cpu"
        )
        scope_dir.mkdir(parents=True, exist_ok=True)
        baseline_path = scope_dir / "fresh_baseline.json"
        baseline_failure_path = scope_dir / "fresh_baseline_technical_failure.json"
        baseline_profile_failure_path = scope_dir / "fresh_baseline_profile_failure.json"
        if baseline_failure_path.is_file() or baseline_profile_failure_path.is_file():
            baseline_failure_count += 1
            continue
        if not baseline_path.is_file():
            baseline_id = f"scope{scope_index:02d}_baseline"
            point_dir = scope_dir / "points" / baseline_id
            try:
                raw, seal_path = execute_point(
                    args,
                    backend,
                    point_dir,
                    baseline_id,
                    DEFAULT_PARAMS,
                )
                profile_gate = assess_r0_profile(raw)
                baseline_document = {
                    "event": "fresh_baseline",
                    "scenario": "cpu",
                    "fresh_prefix_source": (
                        f"tpcc_setlocal_direction_calibration_scope_{scope_index:02d}"
                    ),
                    "params": DEFAULT_PARAMS,
                    "measurement": {
                        "db_qps": raw["db_qps"],
                        **dict(raw["resource_utilization"]),
                    },
                    "raw": raw,
                    "r0_profile_gate": profile_gate,
                    "session_local_calibration_seal": str(seal_path.resolve()),
                }
                if profile_gate["passed"] is not True:
                    write_json_new(
                        baseline_profile_failure_path,
                        {
                            **baseline_document,
                            "event": "fresh_baseline_profile_failure",
                            "status": "r0_profile_failed",
                        },
                    )
                    baseline_failure_count += 1
                    continue
                write_json_new(
                    baseline_path,
                    baseline_document,
                )
            except Exception as error:
                if not baseline_failure_path.exists():
                    write_json_new(
                        baseline_failure_path,
                        technical_failure(baseline_id, error, point_dir),
                    )
                baseline_failure_count += 1
                continue
        if args.baseline_only:
            continue
        calibration_path = scope_dir / "fresh_model_calibration.jsonl"
        done = completed_ids(calibration_path)
        for design in calibration_design(DEFAULT_PARAMS, scope_index):
            calibration_id = design["calibration_id"]
            if calibration_id in done:
                continue
            point_dir = scope_dir / "points" / calibration_id
            try:
                raw, seal_path = execute_point(
                    args,
                    backend,
                    point_dir,
                    calibration_id,
                    design["params"],
                )
                row = {
                    "event": "fresh_model_calibration_replay",
                    "calibration_id": calibration_id,
                    "phase": "coordinate_axis_bidirectional_setlocal_calibration",
                    "design": "single_coordinate_current_profile",
                    "parameter": design["parameter"],
                    "direction": design["direction"],
                    "changed_parameters": [design["parameter"]],
                    "params": design["params"],
                    "status": "usable",
                    "current_run_only": True,
                    "used_for_optimizer_candidate_selection": False,
                    "raw_result": raw,
                    "session_local_calibration_seal": str(seal_path.resolve()),
                }
            except Exception as error:
                row = technical_failure(calibration_id, error, point_dir)
            append_jsonl(calibration_path, row)
            done.add(calibration_id)
    return 4 if baseline_failure_count else 0


def build_parser() -> argparse.ArgumentParser:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--baseline-only", action="store_true")
    parser.add_argument("--scopes", type=int, choices=(1, 2, 3), default=1)
    parser.add_argument(
        "--restore-command",
        type=Path,
        default=root / "shell/restore_pg5_tpcc_from_template.sh",
    )
    parser.add_argument(
        "--benchbase-xml",
        type=Path,
        default=Path(
            "/home/anonymous/benchbase-postgres/config/postgres/sample_tpcc_config.xml"
        ),
    )
    parser.add_argument(
        "--benchbase-jar",
        type=Path,
        default=Path("/home/anonymous/benchbase-postgres/benchbase.jar"),
    )
    parser.add_argument(
        "--postgresql-jdbc-jar",
        type=Path,
        default=Path("/home/anonymous/benchbase-postgres/lib/postgresql-42.7.4.jar"),
    )
    parser.add_argument("--container", default="pg5_tpcc_d2_20260722")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=59640)
    parser.add_argument("--database", default="fullgrid_tpcc")
    parser.add_argument("--user", default="postgres")
    parser.add_argument("--password-env", default="PGPASSWORD")
    parser.add_argument("--cpu-limit-cores", type=float, default=2.05)
    parser.add_argument("--memory-limit-bytes", type=int, default=4294967296)
    parser.add_argument("--io-device", default="259:6")
    parser.add_argument("--io-read-cap-mib-s", type=float, default=13.5)
    parser.add_argument("--io-write-cap-mib-s", type=float, default=13.5)
    parser.add_argument("--io-total-limit-mib-s", type=float, default=27)
    parser.add_argument("--cpuset-cpus", default="104-107,360-363")
    parser.add_argument("--native-terminals", type=int, default=32)
    parser.add_argument("--sensitive-clients", type=int, default=1)
    parser.add_argument("--warehouses", type=int, default=10)
    parser.add_argument("--java-xmx", default="-Xmx4g")
    parser.add_argument("--sample-interval-seconds", type=float, default=1)
    parser.add_argument("--warmup-seconds", type=float, default=300)
    parser.add_argument("--window-seconds", type=float, default=30)
    parser.add_argument("--required-windows", type=int, default=6)
    parser.add_argument("--coverage-min", type=float, default=0.9)
    parser.add_argument("--cv-max", type=float, default=0.1)
    parser.add_argument("--minimum-measurement-seconds", type=float, default=600)
    parser.add_argument("--maximum-measurement-seconds", type=float, default=600)
    return parser


def validate_args(args: argparse.Namespace) -> None:
    actual_database = {
        name: getattr(args, name)
        for name in ("container", "host", "port", "database", "user")
    }
    if actual_database != FROZEN_DATABASE:
        raise ValueError(
            f"database target differs from frozen attempt15 target: "
            f"expected={FROZEN_DATABASE}, actual={actual_database}"
        )
    actual_profile = {
        name: getattr(args, name)
        for name in FROZEN_PROFILE
    }
    if actual_profile != FROZEN_PROFILE:
        raise ValueError(
            f"resource profile differs from frozen attempt15 profile: "
            f"expected={FROZEN_PROFILE}, actual={actual_profile}"
        )
    if not math.isclose(
        args.io_read_cap_mib_s + args.io_write_cap_mib_s,
        args.io_total_limit_mib_s,
        rel_tol=0,
        abs_tol=1e-9,
    ):
        raise ValueError("read and write I/O caps must sum to total")
    if (
        args.warmup_seconds != 300
        or args.sample_interval_seconds != 1
        or args.window_seconds != 30
    ):
        raise ValueError("formal execution requires 300s warmup, 1s samples, and 30s windows")
    if (
        args.required_windows != 6
        or args.minimum_measurement_seconds != 600
        or args.maximum_measurement_seconds != 600
    ):
        raise ValueError(
            "formal execution requires six stability windows and a 600s measurement"
        )
    if not args.restore_command.is_file():
        raise ValueError(f"--restore-command is missing: {args.restore_command}")
    if sha256(args.restore_command) != RESTORE_SHA256:
        raise ValueError("--restore-command does not match the frozen attempt15 snapshot")
    if not args.benchbase_xml.is_file() or args.benchbase_xml.suffix != ".xml":
        raise ValueError("--benchbase-xml must reference a BenchBase XML template")
    if not args.benchbase_jar.is_file():
        raise ValueError(f"--benchbase-jar is missing: {args.benchbase_jar}")
    if not args.postgresql_jdbc_jar.is_file():
        raise ValueError(f"--postgresql-jdbc-jar is missing: {args.postgresql_jdbc_jar}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    validate_args(args)
    return run_campaign(args, RealBackend(args))


if __name__ == "__main__":
    raise SystemExit(main())
