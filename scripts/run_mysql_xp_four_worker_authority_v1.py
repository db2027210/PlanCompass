#!/usr/bin/env python3
"""Validate and measure an independent four-worker MySQL XP authority."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mysql_benchmark_command import (
    HEAVY_QUERY_THRESHOLD_S,
    _job_estimates_from_manifest,
    discover_job_query_files,
    run_job_tpch_parallel,
)
from mysql_workload_adapter import (
    MySQLEndpoint,
    build_job_manifest,
    run_benchbase,
    run_hybench,
    run_job,
)
from scripts import mysql_four_scene_adaptive_measurement_v1 as adaptive_measurement
from scripts import mysql_cross_project_wave_coordination_v8 as dynamic_gate
from scripts import reset_mysql_xp_data_planes_four_worker_v1 as reset4


FREEZE = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260811_v15"
    / "four_scene_effective_resource_successor.json"
)
PREFLIGHT = ROOT / "preflight/mysql_four_scene_20260809/runtime_preflight_v27.json"
RUNTIME_PREFLIGHT = PREFLIGHT
PREPARED = (
    ROOT / "history/mysql_four_scene_20260809/four_scene_live_rebind_v26_20260811T143500p0800"
    / "prepared_binding.json"
)
BENCHMARK_ACCOUNTS = (
    ROOT / "history/mysql_four_scene_20260809"
    / "benchmark_accounts_v27_20260811T150600p0800"
    / "benchmark_accounts_binding.json"
)
FREEZE_SCHEMA = "mysql_disk2_four_scene_two_worker_effective_resource_successor_v15"
WORKLOAD_FREEZE = (
    ROOT / "config/formal/frozen_mysql_four_scene_job_schedule_20260810_v3"
    / "workload_successor.json"
)
XP_WORKLOAD_FREEZE = (
    ROOT / "config/formal/frozen_mysql_four_scene_native_xp_iq1_iq7_20260809_v2"
    / "workload_successor.json"
)
BENCHBASE_JAR = Path("/home/anonymous/benchbase-mysql/benchbase.jar")
HYBENCH_HOME = Path("/home/anonymous/HyBench-2024-master")
NATIVE_XP_JAR = (
    ROOT / "artifacts/mysql_xp_native_iq1_iq7_successor_v2"
    / "e3e3a32e6b095e10d5e229be5ec211131f626374bc82764d98ad3f910344e017"
    / "runtime/HyBench-native-IQ1-IQ7-v2.jar"
)
NATIVE_XP_MANIFEST = NATIVE_XP_JAR.parent.parent / "manifest.json"
HYBENCH_ADAPTIVE_RUNNER_JAR = (
    ROOT / "config/formal/frozen_mysql_pg_outer_20260819_v35/runtime"
    / "hybench-adaptive-runner-v3.jar"
)
PREFLIGHT_SOURCE = ROOT / "scripts/preflight_mysql_four_scene_v3.py"
DEFAULT_OPTIMIZER_COSTS = {
    "io_block_read_cost": 1.0,
    "memory_block_read_cost": 0.25,
    "row_evaluate_cost": 0.1,
    "key_compare_cost": 0.05,
    "disk_temptable_create_cost": 20.0,
    "disk_temptable_row_cost": 0.5,
    "memory_temptable_create_cost": 1.0,
    "memory_temptable_row_cost": 0.1,
}
COORDINATED_LEASE_HOLDER_PID: int | None = None
COORDINATION_CONTRACT: Path | None = None
AUTHORITY_SCENE_SCOPE: str | None = None


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"regular non-symlink JSON required: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def _private_credential(worker: Mapping[str, Any]) -> tuple[str, str]:
    path = Path(str(worker["benchmark_defaults"]))
    if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
        raise RuntimeError(f"{worker['worker_id']} credential authority differs")
    users = re.findall(r"(?m)^user=(\S+)$", path.read_text(encoding="utf-8"))
    matches = re.findall(r"(?m)^password=(\S+)$", path.read_text(encoding="utf-8"))
    if users != ["ra_benchmark"] or len(matches) != 1:
        raise RuntimeError(f"{worker['worker_id']} benchmark credential differs")
    return users[0], matches[0]


def _mysql_root(worker: Mapping[str, Any], sql: str) -> str:
    defaults = Path(str(worker["credential_defaults"]))
    completed = subprocess.run(
        ["mysql", f"--defaults-file={defaults}", "--host=127.0.0.1",
         f"--port={worker['host_port']}", "--connect-timeout=10", "-NBe", sql],
        text=True, capture_output=True, check=True, timeout=60,
    )
    return completed.stdout.strip()


def _apply_and_readback_default_optimizer_costs(
    worker: Mapping[str, Any],
) -> dict[str, float]:
    server = [
        name for name in DEFAULT_OPTIMIZER_COSTS
        if name not in {"io_block_read_cost", "memory_block_read_cost"}
    ]
    server_case = " ".join(
        f"WHEN '{name}' THEN {DEFAULT_OPTIMIZER_COSTS[name]:.17g}"
        for name in server
    )
    engine_case = " ".join(
        f"WHEN '{name}' THEN {DEFAULT_OPTIMIZER_COSTS[name]:.17g}"
        for name in ("io_block_read_cost", "memory_block_read_cost")
    )
    _mysql_root(worker, (
        "START TRANSACTION; "
        f"UPDATE mysql.server_cost SET cost_value=CASE cost_name {server_case} ELSE cost_value END "
        f"WHERE cost_name IN ({','.join(repr(name) for name in server)}); "
        f"UPDATE mysql.engine_cost SET cost_value=CASE cost_name {engine_case} ELSE cost_value END "
        "WHERE engine_name='default' AND device_type=0 AND cost_name IN "
        "('io_block_read_cost','memory_block_read_cost'); COMMIT; FLUSH OPTIMIZER_COSTS;"
    ))
    rows = _mysql_root(worker, (
        "SELECT cost_name,cost_value FROM mysql.server_cost WHERE cost_name IN "
        f"({','.join(repr(name) for name in server)}) UNION ALL "
        "SELECT cost_name,cost_value FROM mysql.engine_cost WHERE engine_name='default' "
        "AND device_type=0 AND cost_name IN "
        "('io_block_read_cost','memory_block_read_cost')"
    )).splitlines()
    readback = {
        name: float(value) for name, value in (row.split("\t") for row in rows)
    }
    if (
        set(readback) != set(DEFAULT_OPTIMIZER_COSTS)
        or any(
            readback[name].hex() != float(expected).hex()
            for name, expected in DEFAULT_OPTIMIZER_COSTS.items()
        )
    ):
        raise RuntimeError(
            f"{worker['worker_id']} default optimizer cost readback differs"
        )
    return readback


def _status_field(pid: int, key: str) -> str | None:
    try:
        text = Path(f"/proc/{pid}/status").read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        return None
    match = re.search(rf"^{re.escape(key)}:\s*(\S+)$", text, re.MULTILINE)
    return match.group(1) if match else None


def _numa_policy(pid: int) -> str | None:
    try:
        lines = Path(f"/proc/{pid}/numa_maps").read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        return None
    policies = {
        field
        for line in lines[:128]
        for field in line.split()[1:2]
        if field.startswith(("bind:", "interleave:", "preferred:", "default"))
    }
    return next(iter(policies)) if len(policies) == 1 else None


def _descendant_pids(pid: int) -> list[int]:
    descendants: list[int] = []
    pending = [pid]
    while pending:
        parent = pending.pop()
        try:
            children = [
                int(value) for value in
                Path(f"/proc/{parent}/task/{parent}/children").read_text().split()
            ]
        except (FileNotFoundError, PermissionError, ProcessLookupError, ValueError):
            children = []
        descendants.extend(children)
        pending.extend(children)
    return descendants


class PinnedRunner:
    """subprocess.run-compatible runner with runtime CPU/NUMA evidence."""

    def __init__(self, worker: Mapping[str, Any], evidence: Path) -> None:
        self.worker = worker
        self.evidence = evidence
        self.evidence.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()

    def __call__(self, argv: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if kwargs.pop("capture_output", False) is not True:
            raise RuntimeError("pinned runner requires captured output")
        if kwargs.pop("check", False) is not False:
            raise RuntimeError("pinned runner owns return-code handling")
        timeout = kwargs.pop("timeout", None)
        input_text = kwargs.pop("input", None)
        cwd = kwargs.pop("cwd", None)
        env = kwargs.pop("env", None)
        text_mode = kwargs.pop("text", True)
        if kwargs or text_mode is not True:
            raise RuntimeError(f"unsupported pinned runner kwargs: {sorted(kwargs)}")
        cpu_locations = [
            f"pu:{segment}" for segment in str(self.worker["client_cpuset"]).split(",")
        ]
        full = [
            "/usr/bin/hwloc-bind", "--physical", "--cpubind", *cpu_locations,
            "--membind", f"node:{self.worker['client_cpuset_mems']}",
            "--mempolicy", "bind", "--strict", "--", *map(str, argv),
        ]
        process = subprocess.Popen(
            full, cwd=cwd, env=env, text=True, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        readback = {
            "cpus_allowed_list": None,
            "mems_allowed_list": None,
            "numa_policy": None,
        }
        workload_pid = process.pid
        # Concurrent hwloc discovery across the 512-CPU topology can exceed
        # three seconds.  Wait for the post-exec affinity itself, rather than
        # recording the still-unbound launcher and rejecting a valid run.
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline and process.poll() is None:
            for candidate_pid in [process.pid, *_descendant_pids(process.pid)]:
                candidate = {
                    "cpus_allowed_list": _status_field(candidate_pid, "Cpus_allowed_list"),
                    "mems_allowed_list": _status_field(candidate_pid, "Mems_allowed_list"),
                    "numa_policy": _numa_policy(candidate_pid),
                }
                readback = candidate
                workload_pid = candidate_pid
                if (
                    candidate["cpus_allowed_list"] == self.worker["client_cpuset"]
                    and candidate["numa_policy"]
                    == f"bind:{self.worker['client_cpuset_mems']}"
                ):
                    break
            else:
                time.sleep(0.005)
                continue
            break
        evidence = {
            "captured_at": _now(),
            "worker_id": self.worker["worker_id"],
            "launcher_pid": process.pid,
            "workload_pid": workload_pid,
            "argv_sha256": hashlib.sha256(
                "\0".join(full).encode("utf-8")
            ).hexdigest(),
            "argv_executable": str(argv[0]),
            "requested_client_cpuset": self.worker["client_cpuset"],
            "requested_client_cpuset_mems": self.worker["client_cpuset_mems"],
            "requested_numa_policy": f"bind:{self.worker['client_cpuset_mems']}",
            **readback,
        }
        with self.lock:
            with self.evidence.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(evidence, ensure_ascii=False) + "\n")
        try:
            stdout, stderr = process.communicate(input=input_text, timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
            raise
        return subprocess.CompletedProcess(full, process.returncode, stdout, stderr)


class CgroupSampler:
    def __init__(self, workers: list[dict[str, Any]], output: Path) -> None:
        self.workers = workers
        self.output = output
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, name="mysql-v5-r0-sampler")
        self.rows: dict[str, list[dict[str, Any]]] = {
            str(worker["worker_id"]): [] for worker in workers
        }

    @staticmethod
    def _paths(worker: Mapping[str, Any]) -> dict[str, Path]:
        cgroup = Path(str(worker["blkio_authority"]["cgroup_path"]))
        relative = Path(*cgroup.parts[cgroup.parts.index("docker-limited"):])
        return {
            "cpu": Path("/sys/fs/cgroup/cpu,cpuacct") / relative,
            "memory": Path("/sys/fs/cgroup/memory") / relative,
            "blkio": Path("/sys/fs/cgroup/blkio") / relative,
        }

    @staticmethod
    def _io(path: Path, device: str) -> tuple[int, int]:
        read_bytes = write_bytes = 0
        device_seen = False
        for line in path.read_text(encoding="ascii").splitlines():
            parts = line.split()
            if len(parts) == 3 and parts[0] == device:
                device_seen = True
                if parts[1] == "Read":
                    read_bytes = int(parts[2])
                elif parts[1] == "Write":
                    write_bytes = int(parts[2])
        if not device_seen:
            raise RuntimeError(
                f"blkio service bytes lacks worker device {device}: {path}"
            )
        return read_bytes, write_bytes

    def _sample(self, worker: Mapping[str, Any]) -> dict[str, Any]:
        paths = self._paths(worker)
        memory_usage = int((paths["memory"] / "memory.usage_in_bytes").read_text())
        inactive_file = 0
        for line in (paths["memory"] / "memory.stat").read_text().splitlines():
            fields = line.split()
            if len(fields) == 2 and fields[0] == "total_inactive_file":
                inactive_file = int(fields[1])
                break
        device = str(
            (worker.get("blkio_authority") or {}).get("device_major_minor") or ""
        )
        if not re.fullmatch(r"[0-9]+:[0-9]+", device):
            raise RuntimeError(
                f"{worker.get('worker_id')} lacks a valid blkio worker device"
            )
        read_bytes, write_bytes = self._io(
            paths["blkio"] / "blkio.throttle.io_service_bytes", device,
        )
        return {
            "captured_monotonic": time.monotonic(),
            "captured_at": _now(),
            "worker_id": worker["worker_id"],
            "device_major_minor": device,
            "cpu_ns": int((paths["cpu"] / "cpuacct.usage").read_text()),
            "memory_usage_bytes": memory_usage,
            "memory_working_set_bytes": max(0, memory_usage - inactive_file),
            "read_bytes": read_bytes,
            "write_bytes": write_bytes,
        }

    def _run(self) -> None:
        self.output.parent.mkdir(parents=True, exist_ok=True)
        while not self.stop_event.is_set():
            for worker in self.workers:
                row = self._sample(worker)
                self.rows[str(worker["worker_id"])].append(row)
                with self.output.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            self.stop_event.wait(1.0)
        for worker in self.workers:
            row = self._sample(worker)
            self.rows[str(worker["worker_id"])].append(row)
            with self.output.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")

    def start(self) -> None:
        self.thread.start()

    def finish(
        self,
        active_windows: Mapping[str, tuple[float, float]] | None = None,
    ) -> dict[str, Any]:
        self.stop_event.set()
        self.thread.join(timeout=10)
        if self.thread.is_alive():
            raise RuntimeError("resource sampler did not stop")
        summary: dict[str, Any] = {}
        by_id = {str(worker["worker_id"]): worker for worker in self.workers}
        for worker_id, rows in self.rows.items():
            all_sample_count = len(rows)
            window = (active_windows or {}).get(worker_id)
            if window is not None:
                window_started, window_finished = window
                rows = [
                    row for row in rows
                    if window_started <= row["captured_monotonic"] <= window_finished
                ]
            if len(rows) < 2:
                raise RuntimeError(f"{worker_id} has insufficient R0 resource samples")
            first, last = rows[0], rows[-1]
            elapsed = max(last["captured_monotonic"] - first["captured_monotonic"], 1e-9)
            worker = by_id[worker_id]
            read_bps = max(0, last["read_bytes"] - first["read_bytes"]) / elapsed
            write_bps = max(0, last["write_bytes"] - first["write_bytes"]) / elapsed
            summary[worker_id] = {
                "sample_count": len(rows),
                "all_batch_sample_count": all_sample_count,
                "elapsed_seconds": elapsed,
                "active_window_bound": window is not None,
                "device_major_minor": first["device_major_minor"],
                "cpu_fraction_of_quota": (
                    max(0, last["cpu_ns"] - first["cpu_ns"]) / 1e9
                    / elapsed / float(worker["cpu_quota_cores"])
                ),
                "memory_peak_fraction": max(
                    row["memory_working_set_bytes"] for row in rows
                ) / float(worker["memory_bytes"]),
                "read_bps": read_bps,
                "write_bps": write_bps,
                "read_fraction_of_cap": read_bps / float(worker["io_read_bps"]),
                "write_fraction_of_cap": write_bps / float(worker["io_write_bps"]),
                "raw_samples_path": str(self.output),
            }
            if (
                worker.get("scene") == "hybench_xp_io"
                and read_bps == 0.0
                and write_bps == 0.0
            ):
                raise RuntimeError(
                    f"{worker_id} XP/IO active window has zero target-device IO"
                )
        return summary


class ExternalWorkloadWatchdog:
    """Record co-resident physical workloads throughout the R0 measurement.

    The prestart gate cannot protect against a legacy/no-mutex controller that
    launches after R0 begins.  Any overlapping physical workload observed in
    the active R0 interval is therefore a fail-closed result, even when it
    disappears before the workload workers finish.
    """

    def __init__(
        self, run_id: str, target_mems: set[int], output: Path,
        *, target_cpus: set[int] | None = None,
    ) -> None:
        self.run_id = run_id
        self.target_mems = target_mems
        self.target_cpus = set(target_cpus or ())
        self.output = output
        self.stop_event = threading.Event()
        self.thread = threading.Thread(
            target=self._run, name="mysql-r0-external-workload-watchdog"
        )
        self.sample_count = 0
        self.observation_count = 0
        self.workload_rows: list[dict[str, Any]] = []
        self.allowed_sibling_mysql_rows: list[dict[str, Any]] = []
        self.errors: list[str] = []
        self.transient_readback_errors: list[str] = []
        self.consecutive_readback_errors = 0

    def _run(self) -> None:
        from scripts import mysql_cross_project_wave_coordination_v1 as coordination

        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.output.touch(mode=0o600, exist_ok=True)
        while not self.stop_event.is_set():
            captured_at = _now()
            try:
                inventory = coordination.external_process_inventory(
                    self.run_id, self.target_mems
                )
                raw_workloads = inventory["workloads"]
                allowed_sibling_mysql = [
                    row for row in raw_workloads
                    if self.target_cpus
                    and str(ROOT) in str(row.get("command") or "")
                    and _cpus(str(row.get("cpus_allowed_list") or ""))
                    .isdisjoint(self.target_cpus)
                ]
                evaluated_workloads = [
                    row for row in raw_workloads
                    if row not in allowed_sibling_mysql
                ]
                workloads = coordination.sanitized_rows(raw_workloads)
                allowed_rows = coordination.sanitized_rows(allowed_sibling_mysql)
                controllers = coordination.sanitized_rows(inventory["controllers"])
                isolation = coordination.evaluate_co_resident_pg_isolation(
                    evaluated_workloads
                )
                blocking_workloads = coordination.sanitized_rows(
                    evaluated_workloads
                )
                if evaluated_workloads and isolation["passed"] is not True:
                    from scripts import watch_mysql_formal_external_postgres_interference_v1 as runtime_watchdog
                    confirmed = runtime_watchdog.confirmed_workload_isolation_failure(
                        self.run_id,
                        isolation,
                        target_mems=self.target_mems,
                    )
                    if confirmed is None:
                        blocking_workloads = []
                    else:
                        isolation = confirmed["isolation"]
                        blocking_workloads = coordination.sanitized_rows(
                            confirmed["inventory"]["workloads"]
                        )
                elif isolation["passed"] is True:
                    blocking_workloads = []
                self.sample_count += 1
                self.consecutive_readback_errors = 0
                if workloads or controllers:
                    row = {
                        "captured_at": captured_at,
                        "workloads": workloads,
                        "controllers": controllers,
                        "co_resident_pg_isolation": isolation,
                        "blocking_workloads": blocking_workloads,
                        "allowed_cpu_disjoint_sibling_mysql_workloads": allowed_rows,
                    }
                    with self.output.open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                    self.observation_count += 1
                    self.workload_rows.extend(blocking_workloads)
                    self.allowed_sibling_mysql_rows.extend(allowed_rows)
            except BaseException as error:
                message = f"{type(error).__name__}: {error}"
                self.transient_readback_errors.append(message)
                self.consecutive_readback_errors += 1
                if self.consecutive_readback_errors >= 3:
                    self.errors.append(message)
            self.stop_event.wait(1.0)

    def start(self) -> None:
        self.thread.start()

    def finish(self) -> dict[str, Any]:
        self.stop_event.set()
        self.thread.join(timeout=10)
        if self.thread.is_alive():
            self.errors.append("external workload watchdog did not stop")
        unique_workloads = {
            (row.get("pid"), row.get("command_sha256")): row
            for row in self.workload_rows
        }
        unique_allowed_siblings = {
            (row.get("pid"), row.get("command_sha256")): row
            for row in self.allowed_sibling_mysql_rows
        }
        return {
            "contract": "mysql_r0_full_interval_external_workload_watchdog_v1",
            "sample_interval_seconds": 1.0,
            "sample_count": self.sample_count,
            "observation_count": self.observation_count,
            "target_mems": sorted(self.target_mems),
            "workload_observation_count": len(self.workload_rows),
            "unique_external_workloads": list(unique_workloads.values()),
            "allowed_cpu_disjoint_sibling_mysql_workloads": list(
                unique_allowed_siblings.values()
            ),
            "scoped_sibling_mysql_wait_required": False,
            "errors": self.errors,
            "transient_readback_errors": self.transient_readback_errors,
            "maximum_tolerated_consecutive_readback_errors": 2,
            "evidence_path": str(self.output),
            "evidence_sha256": _sha(self.output),
            "passed": not self.workload_rows and not self.errors,
        }


def _cpus(value: str) -> set[int]:
    result: set[int] = set()
    for field in value.split(","):
        start, separator, end = field.strip().partition("-")
        result.update(range(int(start), int(end) + 1) if separator else [int(start)])
    return result


def _preparation_chain_reaches_preflight(document: dict[str, Any], path: Path) -> bool:
    seen: set[Path] = set()
    for _ in range(8):
        resolved = path.resolve()
        if resolved in seen:
            return False
        seen.add(resolved)
        if document.get("preflight_sha256") == _sha(PREFLIGHT):
            return True
        predecessor_value = document.get("predecessor_prepared_path")
        predecessor_sha = document.get("predecessor_prepared_sha256")
        if not predecessor_value or not predecessor_sha:
            return False
        path = Path(str(predecessor_value))
        if predecessor_sha != _sha(path):
            return False
        document = _load(path)
    return False


def _workload_freeze_chain_reaches(path: Path, target_sha256: str) -> bool:
    """Verify an immutable workload-successor chain reaches the bound predecessor."""
    seen: set[Path] = set()
    for _ in range(8):
        resolved = path.resolve()
        if resolved in seen or not resolved.is_file():
            return False
        seen.add(resolved)
        if _sha(resolved) == target_sha256:
            return True
        document = _load(resolved)
        predecessor = document.get("predecessor_workload_freeze") or {}
        predecessor_value = predecessor.get("path")
        predecessor_sha = predecessor.get("sha256")
        if not predecessor_value or not predecessor_sha:
            return False
        candidate = (ROOT / str(predecessor_value)).resolve()
        if not candidate.is_file() or _sha(candidate) != predecessor_sha:
            return False
        path = candidate
    return False


def _validate_authority() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    freeze = _load(FREEZE)
    preflight = _load(PREFLIGHT)
    runtime_preflight = _load(RUNTIME_PREFLIGHT)
    prepared = _load(PREPARED)
    accounts = _load(BENCHMARK_ACCOUNTS)
    if (
        freeze.get("schema_version")
        != FREEZE_SCHEMA
        or preflight.get("status") != "PASS"
        or preflight.get("freeze_sha256") != _sha(FREEZE)
        or runtime_preflight.get("status") != "PASS"
        or runtime_preflight.get("freeze_sha256") != _sha(FREEZE)
        or float(runtime_preflight.get("observed_seconds", 0)) < 60
        or runtime_preflight.get("formal_launch_authorized") is not True
        or any((runtime_preflight.get("accepted_rounds") or {}).values())
        or prepared.get("status") != "PREPARED"
        or prepared.get("freeze_sha256") != _sha(FREEZE)
        or not _preparation_chain_reaches_preflight(prepared, PREPARED)
        or prepared.get("workload_started") is not False
        or prepared.get("fresh_r0_complete") is not False
        or prepared.get("formal_outer_started") is not False
        or len(prepared.get("workers") or []) != 8
        or accounts.get("status") != "PASS"
        or accounts.get("prepared_binding_sha256") != _sha(PREPARED)
        or accounts.get("freeze_sha256") != _sha(FREEZE)
        or accounts.get("workload_started") is not False
        or len(accounts.get("workers") or []) != 8
    ):
        raise RuntimeError("v15 R0 authority chain differs")
    if not all(path.is_file() for path in (
        BENCHBASE_JAR, NATIVE_XP_JAR, NATIVE_XP_MANIFEST, WORKLOAD_FREEZE,
        XP_WORKLOAD_FREEZE,
    )):
        raise RuntimeError("R0 workload dependency is absent")
    native_xp_authority = _load(NATIVE_XP_MANIFEST)
    xp_workload_freeze = _load(XP_WORKLOAD_FREEZE)
    if (
        native_xp_authority.get("status") != "EXECUTION_AUTHORIZED"
        or native_xp_authority.get("execution_authorized") is not True
        or native_xp_authority.get("runtime_jar_sha256") != _sha(NATIVE_XP_JAR)
        or native_xp_authority.get("workload_semantics", {}).get("iq_selection")
        != "uniform_random_IQ1_IQ7"
        or native_xp_authority.get("workload_semantics", {}).get("exclusive_random_range")
        != [1, 8]
        or xp_workload_freeze.get("status") != "FROZEN"
        or xp_workload_freeze.get("execution_authorized") is not True
        or xp_workload_freeze.get("formal_scope") != "mysql_only"
        or xp_workload_freeze.get("active_scenes") != freeze.get("active_scenes")
        or not _workload_freeze_chain_reaches(
            WORKLOAD_FREEZE,
            str(xp_workload_freeze.get("job_tpch_predecessor", {}).get("sha256")),
        )
        or xp_workload_freeze.get("native_hybench_xp", {}).get("iq_selection")
        != "uniform_random_IQ1_IQ7"
        or xp_workload_freeze.get("native_hybench_xp", {}).get("at_mix_percent")
        != [35, 25, 15, 15, 7, 3]
        or xp_workload_freeze.get("native_hybench_xp", {}).get("xap_xtp_reduced_ratio")
        != [1, 1]
        or xp_workload_freeze.get("native_hybench_xp", {}).get("runtime_manifest", {}).get("sha256")
        != _sha(NATIVE_XP_MANIFEST)
        or xp_workload_freeze.get("iq1_iq6_artifacts_prohibited") is not True
        or xp_workload_freeze.get("incomplete_iq1_iq7_v1_artifacts_prohibited") is not True
    ):
        raise RuntimeError("native XP runtime is not execution-authorized")
    freeze_workers = {row["worker_id"]: row for row in freeze["workers"]}
    prepared_workers = {row["worker_id"]: row for row in prepared["workers"]}
    account_workers = {row["worker_id"]: row for row in accounts["workers"]}
    if set(freeze_workers) != set(prepared_workers) or set(freeze_workers) != set(account_workers):
        raise RuntimeError("v5 prepared worker set differs")
    authority_worker_ids = {
        worker_id for worker_id, worker in freeze_workers.items()
        if AUTHORITY_SCENE_SCOPE is None
        or str(worker.get("scene")) == AUTHORITY_SCENE_SCOPE
    }
    if not authority_worker_ids or (
        AUTHORITY_SCENE_SCOPE is not None and len(authority_worker_ids) != 4
    ):
        raise RuntimeError("scoped point-authority worker set differs")
    merged: list[dict[str, Any]] = []
    for worker_id in sorted(authority_worker_ids):
        frozen = freeze_workers[worker_id]
        adopted = prepared_workers[worker_id]
        account = account_workers[worker_id]
        inspected = json.loads(subprocess.run(
            ["docker", "inspect", frozen["container"]], capture_output=True,
            text=True, check=True, timeout=30,
        ).stdout)[0]
        inspected_state = inspected.get("State") or {}
        inspected_command = (inspected.get("Config") or {}).get("Cmd") or []
        stopped_initial_slot_recovery = bool(
            worker_id.endswith(("_w3", "_w4"))
            and inspected.get("Id") == adopted.get("container_id")
            and inspected_state.get("Running") is False
            and int(inspected_state.get("Pid") or 0) == 0
            and "--init-file=/run/ra-init/slot.sql" in inspected_command
            and (inspected.get("Config") or {}).get("Labels", {}).get("ra.owner")
            == "mysql_four_scene_v5"
        )
        live_identity = bool(
            inspected.get("Id") != adopted.get("container_id")
            or inspected_state.get("Running") is not True
            or inspected_state.get("Restarting") is True
            or (inspected.get("Config") or {}).get("Labels", {}).get("ra.owner")
            != "mysql_four_scene_v5"
        ) is False
        if not live_identity and not stopped_initial_slot_recovery:
            raise RuntimeError(f"{worker_id} live container identity differs")
        if (
            account.get("container_id") != adopted.get("container_id")
            or account.get("database") != adopted.get("database")
            or account.get("benchmark_user") != "ra_benchmark"
            or account.get("ready") is not True
        ):
            raise RuntimeError(f"{worker_id} benchmark account authority differs")
        effective = {**frozen, **adopted, **account}
        if stopped_initial_slot_recovery:
            effective["stopped_initial_slot_recovery_authorized"] = True
            effective["formal_measurement_before_recreation_prohibited"] = True
        else:
            effective["kernel_cgroup_resources"] = (
                reset4.prep._kernel_resource_readback(inspected, effective)
            )
            effective["blkio_authority"] = reset4.prep._enforce_blkio(
                inspected, freeze, effective,
            )
        merged.append(effective)
    target_cpus: set[int] = set()
    # Each XP scene crosses the 2 -> 4 worker boundary independently.  Do not
    # make one scene's authority depend on the sibling scene's two successor
    # containers having been provisioned already.
    intended_names = {
        str(worker["container"])
        for worker in freeze["workers"]
        if str(worker["worker_id"]) in authority_worker_ids
    }
    for worker in freeze["workers"]:
        if str(worker["worker_id"]) not in authority_worker_ids:
            continue
        target_cpus.update(_cpus(str(worker["database_cpuset"])))
        target_cpus.update(_cpus(str(worker["client_cpuset"])))
    spec = importlib.util.spec_from_file_location("mysql_v5_preflight", PREFLIGHT_SOURCE)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load frozen preflight inventory helpers")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    process_inventory, process_blockers = module._process_inventory(target_cpus)
    docker_inventory, docker_blockers = module._docker_inventory(target_cpus, intended_names)
    # The full-parallel successor explicitly permits the independently owned
    # sibling XP scene on the same NVMe when its fixed CPU placement and exact
    # frozen container identity are disjoint.  The legacy point inventory only
    # knew its older profile list, so validate current-successor siblings here
    # against the live freeze rather than misclassifying them as external DBs.
    sibling_by_name = {
        str(worker["container"]): worker
        for worker in freeze["workers"]
        if str(worker["container"]) not in intended_names
    }
    retained_docker_blockers = []
    admitted_sibling_names: set[str] = set()
    for blocker in docker_blockers:
        name = str(blocker.get("name") or "")
        frozen = sibling_by_name.get(name)
        if (
            blocker.get("reason") == "co_resident_database_uses_target_disk2"
            and frozen is not None
            and not str(blocker.get("overlap_cpus") or "")
        ):
            inspected = json.loads(subprocess.run(
                ["docker", "inspect", name], capture_output=True, text=True,
                check=True, timeout=30,
            ).stdout)[0]
            host = inspected.get("HostConfig") or {}
            mounts = {
                str(row.get("Destination") or ""): str(row.get("Source") or "")
                for row in inspected.get("Mounts") or []
            }
            if (
                (inspected.get("State") or {}).get("Running") is True
                and str(host.get("CpusetCpus") or "")
                == str(frozen["database_cpuset"])
                and str(host.get("CpusetMems") or "")
                == str(frozen["database_cpuset_mems"])
                and mounts.get("/var/lib/mysql") == str(
                    Path(str(freeze["storage"]["runtime_root"]))
                    / str(frozen["worker_id"]) / "datadir"
                )
                and int(host.get("Memory") or 0) == int(frozen["memory_bytes"])
                and int(host.get("PidsLimit") or 0) == int(frozen["pids_max"])
                and (inspected.get("Config") or {}).get("Labels", {}).get("ra.owner")
                == "mysql_four_scene_v5"
            ):
                admitted_sibling_names.add(name)
                continue
        retained_docker_blockers.append(blocker)
    docker_blockers = retained_docker_blockers
    for row in docker_inventory:
        if str(row.get("name") or "") in admitted_sibling_names:
            row.update({
                "allowed_current_successor_cpu_disjoint_sibling": True,
                "sibling_scene_completion_dependency": False,
                "sibling_container_mutation_performed": False,
                "validated_against_current_resource_freeze": str(FREEZE),
            })
    rolling_gate = dynamic_gate.rolling_quiet_gate(
        run_id=f"mysql-xp4-authority-{os.getpid()}",
        required_seconds=15.0,
        maximum_wait_seconds=60.0,
        maximum_load1=16.0,
        target_cpus=target_cpus,
    )
    # The versioned rolling gate inventories real co-resident workloads and
    # admits only proved CPU/storage/memory-isolated activity.  The older
    # point preflight's process blockers remain diagnostic; Docker overlap and
    # a failed rolling gate remain fail-closed.  No external process is stopped
    # or mutated and no workload lease is acquired.
    blockers = list(docker_blockers)
    if rolling_gate.get("status") != "PASS":
        blockers.append({
            "kind": "dynamic_noninterference_gate",
            "errors": list(rolling_gate.get("errors") or []),
        })
    if blockers:
        raise RuntimeError(f"fresh R0 point non-interference gate blocked: {blockers}")
    point_gate = {
        "captured_at": _now(),
        "process_inventory": process_inventory,
        "legacy_process_blockers_diagnostic_only": process_blockers,
        "docker_inventory": docker_inventory,
        "blockers": blockers,
        "passed": True,
        "rolling_predecessor_path": str(RUNTIME_PREFLIGHT),
        "rolling_predecessor_sha256": _sha(RUNTIME_PREFLIGHT),
        "coordinated_lease_holder_pid": None,
        "lease_mode": "none",
        "authority_scene_scope": AUTHORITY_SCENE_SCOPE,
        "idle_external_controllers_inventory_only": True,
        "rolling_dynamic_noninterference_gate": rolling_gate,
        "external_process_control_or_stop_performed": False,
    }
    return freeze, {row["worker_id"]: row for row in merged}, point_gate


def _pair_r0(
    pair: int, workers: Mapping[str, dict[str, Any]], output: Path,
    runners: Mapping[str, PinnedRunner],
) -> dict[str, Any]:
    job = workers[f"job_w{pair}"]
    tpch = workers[f"tpch_w{pair}"]
    query_files = discover_job_query_files(
        Path("/home/anonymous/dataset/pg_data/join-order-benchmark")
    )
    manifest = build_job_manifest(query_files, output / f"pair_{pair}" / "manifest")
    estimates = _job_estimates_from_manifest(manifest)
    job_user, job_password = _private_credential(job)
    tpch_user, tpch_password = _private_credential(tpch)

    def job_runner(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return run_job(*args, **kwargs, runner=runners[job["worker_id"]])

    def tpch_runner(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return run_benchbase(*args, **kwargs, runner=runners[tpch["worker_id"]])

    return run_job_tpch_parallel(
        job_manifest=manifest,
        job_endpoint=MySQLEndpoint(
            "127.0.0.1", int(job["host_port"]), "imdb_job", job_user,
            job_password,
        ),
        tpch_endpoint=MySQLEndpoint(
            "127.0.0.1", int(tpch["host_port"]), "tpch", tpch_user,
            tpch_password,
        ),
        output_dir=output / f"pair_{pair}",
        benchbase_jar=BENCHBASE_JAR,
        clients=16,
        tpch_clients=5,
        scale_factor=4,
        timeout_s=900,
        target_wall_time_s=900.0,
        job_heavy_clients=4,
        formal_scope="mysql_only",
        workload_freeze=WORKLOAD_FREEZE,
        job_runner=job_runner,
        tpch_runner=tpch_runner,
    )


def _xp_r0(
    worker: dict[str, Any], output: Path, runner: PinnedRunner,
    sampler: CgroupSampler,
) -> dict[str, Any]:
    user, password = _private_credential(worker)
    worker_output = output / worker["worker_id"]
    stop_file = worker_output / "adaptive_stop_request.txt"
    evidence_file = worker_output / "resource_stability.json"
    workload_done = threading.Event()
    monitor_result: dict[str, Any] = {}
    monitor_error: list[BaseException] = []
    started_monotonic = time.monotonic()

    def monitor() -> None:
        try:
            monitor_result.update(adaptive_measurement.monitor_xp_stop_request(
                sampler=sampler, worker=worker,
                started_monotonic=started_monotonic, stop_file=stop_file,
                evidence_file=evidence_file, workload_done=workload_done,
            ))
        except BaseException as error:
            monitor_error.append(error)

    monitor_thread = threading.Thread(
        target=monitor, name=f"r0-adaptive-stop-{worker['worker_id']}",
        daemon=True,
    )
    monitor_thread.start()
    try:
        result = run_hybench(
            "xp",
            MySQLEndpoint(
                "127.0.0.1", int(worker["host_port"]), "hybench_sf10x", user,
                password,
            ),
            worker_output,
            hybench_home=HYBENCH_HOME,
            duration_s=adaptive_measurement.XP_MAXIMUM_MEASUREMENT_SECONDS,
            clients=8,
            extra_properties={"sf": "10x"},
            runtime_jar=NATIVE_XP_JAR,
            formal_control={
                "runner_jar": HYBENCH_ADAPTIVE_RUNNER_JAR,
                "stop_file": stop_file,
                "minimum_measurement_seconds": (
                    adaptive_measurement.MINIMUM_MEASUREMENT_SECONDS
                ),
                "maximum_measurement_seconds": (
                    adaptive_measurement.XP_MAXIMUM_MEASUREMENT_SECONDS
                ),
                "graceful_quiescence_seconds": 300,
            },
            dry_run=False,
            timeout_s=1210,
            runner=runner,
        )
    finally:
        workload_done.set()
        monitor_thread.join(timeout=10)
    if monitor_thread.is_alive():
        raise RuntimeError(f"{worker['worker_id']} R0 stability monitor did not stop")
    if monitor_error:
        raise RuntimeError(
            f"{worker['worker_id']} R0 stability monitor failed: {monitor_error[0]}"
        ) from monitor_error[0]
    result["resource_stability"] = monitor_result
    return result


def _bottleneck_gate(
    resources: Mapping[str, Mapping[str, Any]], workers: Mapping[str, Mapping[str, Any]],
    results: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    threshold = 0.8
    rows: dict[str, Any] = {}
    for worker_id, metrics in resources.items():
        scene = str(workers[worker_id]["scene"])
        if scene in {"job_memory", "tpch_sf4_memory"}:
            value = float(metrics["memory_peak_fraction"])
            resource = "memory"
        elif scene == "hybench_xp_cpu":
            stability = results.get(worker_id, {}).get("resource_stability") or {}
            final_windows = (stability.get("decisions") or [{}])[-1].get("windows") or []
            values = [float(row["target_mean"]) for row in final_windows]
            value = math.fsum(values) / len(values) if values else math.nan
            resource = "cpu"
        else:
            stability = results.get(worker_id, {}).get("resource_stability") or {}
            final_windows = (stability.get("decisions") or [{}])[-1].get("windows") or []
            values = [float(row["target_mean"]) for row in final_windows]
            value = math.fsum(values) / len(values) if values else math.nan
            resource = "io"
        rows[worker_id] = {
            "scene": scene,
            "target_resource": resource,
            "observed_fraction": value,
            "minimum_fraction": threshold,
            "measurement_basis": (
                "selected_stable_six_window_mean"
                if scene.startswith("hybench_xp_")
                else "full_suite_resource_summary"
            ),
            "passed": math.isfinite(value) and value >= threshold,
        }
    return {
        "threshold": threshold,
        "workers": rows,
        "passed": all(row["passed"] for row in rows.values()),
    }


def main() -> int:
    global AUTHORITY_SCENE_SCOPE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--scene", choices=(
        "job_memory", "tpch_sf4_memory", "hybench_xp_cpu", "hybench_xp_io",
    ))
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,128}", args.run_id) is None:
        parser.error("unsafe run-id")
    if args.scene is not None:
        AUTHORITY_SCENE_SCOPE = args.scene
    freeze, workers, point_gate = _validate_authority()
    output = ROOT / "history/mysql_four_scene_20260809" / args.run_id
    if output.exists():
        raise RuntimeError(f"fresh R0 output already exists: {output}")
    output.mkdir(parents=True, mode=0o700)
    lock_suffix = args.scene or "all"
    lock = Path(f"/tmp/mysql_four_scene_v5_r0_{lock_suffix}.lock").open("w")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        raise RuntimeError("another v5 R0 owns the lock") from error
    optimizer_cost_readback = {
        worker_id: _apply_and_readback_default_optimizer_costs(worker)
        for worker_id, worker in workers.items()
    }
    optimizer_cost_binding = {
        "applied_before_measurement": True,
        "flushed_before_measurement": True,
        "expected": DEFAULT_OPTIMIZER_COSTS,
        "workers": optimizer_cost_readback,
    }
    optimizer_cost_path = output / "baseline_optimizer_cost_readback.json"
    optimizer_cost_path.write_text(
        json.dumps(optimizer_cost_binding, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    optimizer_cost_path.with_name(optimizer_cost_path.name + ".sha256").write_text(
        f"{_sha(optimizer_cost_path)}  {optimizer_cost_path.name}\n", encoding="ascii"
    )
    runners = {
        worker_id: PinnedRunner(worker, output / worker_id / "client_processes.jsonl")
        for worker_id, worker in workers.items()
    }
    sampler = CgroupSampler(list(workers.values()), output / "resource_samples.jsonl")
    target_mems = {
        numa_node
        for worker in workers.values()
        for numa_node in _cpus(str(worker["database_cpuset_mems"]))
        | _cpus(str(worker["client_cpuset_mems"]))
    }
    target_cpus = {
        cpu
        for worker in workers.values()
        for cpu in _cpus(str(worker["database_cpuset"]))
        | _cpus(str(worker["client_cpuset"]))
    }
    interference_watchdog = ExternalWorkloadWatchdog(
        args.run_id, target_mems, output / "external_workload_watchdog.jsonl",
        target_cpus=target_cpus if args.scene is not None else None,
    )
    started = time.monotonic()
    started_at = _now()
    results: dict[str, Any] = {}
    failures: list[dict[str, Any]] = []
    task_windows: dict[str, tuple[float, float]] = {}
    task_window_lock = threading.Lock()

    def timed(task: str, callback: Any, *callback_args: Any) -> Any:
        task_started = time.monotonic()
        try:
            return callback(*callback_args)
        finally:
            with task_window_lock:
                task_windows[task] = (task_started, time.monotonic())

    sampler.start()
    interference_watchdog.start()
    try:
        with ThreadPoolExecutor(max_workers=6, thread_name_prefix="mysql-v5-r0") as pool:
            futures: dict[Any, str] = {}
            if args.scene is None:
                futures.update({
                    pool.submit(
                        timed, f"job_tpch_pair_{pair}",
                        _pair_r0, pair, workers, output, runners,
                    ): f"job_tpch_pair_{pair}"
                    for pair in (1, 2)
                })
                xp_worker_ids = (
                    "xp_cpu_w1", "xp_cpu_w2", "xp_io_w1", "xp_io_w2",
                )
            elif args.scene in {"job_memory", "tpch_sf4_memory"}:
                from scripts import mysql_single_scene_measurement_v1 as single_scene
                callback = (
                    single_scene.job_measure
                    if args.scene == "job_memory"
                    else single_scene.tpch_measure
                )
                prefix = "job" if args.scene == "job_memory" else "tpch"
                for pair in (1, 2):
                    worker_id = f"{prefix}_w{pair}"
                    futures[pool.submit(
                        timed, worker_id, callback,
                        pair, workers, output, runners,
                    )] = worker_id
                xp_worker_ids = ()
            else:
                prefix = "xp_cpu" if args.scene == "hybench_xp_cpu" else "xp_io"
                xp_worker_ids = (f"{prefix}_w1", f"{prefix}_w2")
            for worker_id in xp_worker_ids:
                futures[pool.submit(
                    timed, worker_id, _xp_r0,
                    workers[worker_id], output, runners[worker_id], sampler,
                )] = worker_id
            for future in as_completed(futures):
                task = futures[future]
                try:
                    results[task] = future.result()
                    print(json.dumps({"task": task, "status": "PASS"}), flush=True)
                except BaseException as error:
                    failures.append({"task": task, "error": f"{type(error).__name__}: {error}"})
                    print(json.dumps(failures[-1]), flush=True)
    finally:
        worker_windows = {}
        for worker_id in workers:
            if (
                args.scene is None
                and worker_id.startswith(("job_", "tpch_"))
            ):
                task = f"job_tpch_pair_{worker_id[-1]}"
            else:
                task = worker_id
            if task in task_windows:
                worker_windows[worker_id] = task_windows[task]
        try:
            resources = sampler.finish(worker_windows)
        finally:
            runtime_non_interference = interference_watchdog.finish()
    bottleneck = _bottleneck_gate(resources, workers, results)
    client_isolation_failures: list[dict[str, Any]] = []
    for worker_id, worker in workers.items():
        evidence = output / worker_id / "client_processes.jsonl"
        rows = [json.loads(line) for line in evidence.read_text().splitlines()] if evidence.is_file() else []
        valid_rows = [
            row for row in rows
            if row.get("cpus_allowed_list") == worker["client_cpuset"]
            and row.get("numa_policy") == f"bind:{worker['client_cpuset_mems']}"
        ]
        if not rows or not valid_rows:
            client_isolation_failures.append({
                "worker_id": worker_id,
                "reason": "no_runtime_client_cpu_numa_readback_match",
            })
    if args.scene is None:
        pair_valid = all(
            results.get(f"job_tpch_pair_{pair}", {}).get("formal_measurement_valid") is True
            and results.get(f"job_tpch_pair_{pair}", {}).get("all_ap_sql_completed") is True
            for pair in (1, 2)
        )
        xp_ids_for_validation = (
            "xp_cpu_w1", "xp_cpu_w2", "xp_io_w1", "xp_io_w2",
        )
    elif args.scene in {"job_memory", "tpch_sf4_memory"}:
        prefix = "job" if args.scene == "job_memory" else "tpch"
        required_count = 113 if prefix == "job" else 22
        pair_valid = all(
            float(results.get(f"{prefix}_w{pair}", {}).get("metrics", {}).get("qps", 0.0)) > 0
            and results.get(f"{prefix}_w{pair}", {}).get("query_coverage", {}).get("coverage_complete") is True
            and len(results.get(f"{prefix}_w{pair}", {}).get("query_coverage", {}).get("completed_queries") or []) == required_count
            for pair in (1, 2)
        )
        xp_ids_for_validation = ()
    else:
        pair_valid = True
        prefix = "xp_cpu" if args.scene == "hybench_xp_cpu" else "xp_io"
        xp_ids_for_validation = (f"{prefix}_w1", f"{prefix}_w2")
    xp_valid = all(
        float(results.get(worker_id, {}).get("metrics", {}).get("qps", 0.0)) > 0
        and results.get(worker_id, {}).get("workload_coverage", {}).get("coverage_complete") is True
        and results.get(worker_id, {}).get("resource_stability", {}).get("stable") is True
        for worker_id in xp_ids_for_validation
    )
    passed = (
        not failures and pair_valid and xp_valid and bottleneck["passed"]
        and not client_isolation_failures and runtime_non_interference["passed"]
    )
    document = {
        "schema_version": "mysql_four_scene_fresh_r0_v20",
        "status": "PASS" if passed else "R0_FAILED_CLOSED",
        "started_at": started_at,
        "completed_at": _now(),
        "wall_time_seconds": time.monotonic() - started,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "authority_scene_scope": args.scene,
        "other_scene_completion_required": False if args.scene else None,
        "freeze_path": str(FREEZE),
        "freeze_sha256": _sha(FREEZE),
        "preflight_path": str(RUNTIME_PREFLIGHT),
        "preflight_sha256": _sha(RUNTIME_PREFLIGHT),
        "preparation_preflight_path": str(PREFLIGHT),
        "preparation_preflight_sha256": _sha(PREFLIGHT),
        "prepared_binding_path": str(PREPARED),
        "prepared_binding_sha256": _sha(PREPARED),
        "benchmark_accounts_path": str(BENCHMARK_ACCOUNTS),
        "benchmark_accounts_sha256": _sha(BENCHMARK_ACCOUNTS),
        "r0_source_path": str(Path(__file__).resolve()),
        "r0_source_sha256": _sha(Path(__file__).resolve()),
        "workload_freeze_path": str(WORKLOAD_FREEZE),
        "workload_freeze_sha256": _sha(WORKLOAD_FREEZE),
        "xp_workload_freeze_path": str(XP_WORKLOAD_FREEZE),
        "xp_workload_freeze_sha256": _sha(XP_WORKLOAD_FREEZE),
        "prestart_point_non_interference_gate": point_gate,
        "runtime_non_interference_gate": runtime_non_interference,
        "baseline_optimizer_cost_readback_path": str(optimizer_cost_path),
        "baseline_optimizer_cost_readback_sha256": _sha(optimizer_cost_path),
        "baseline_optimizer_cost_readback": optimizer_cost_binding,
        "native_xp_runtime_jar": str(NATIVE_XP_JAR),
        "native_xp_runtime_jar_sha256": _sha(NATIVE_XP_JAR),
        "native_xp_runtime_manifest": str(NATIVE_XP_MANIFEST),
        "native_xp_runtime_manifest_sha256": _sha(NATIVE_XP_MANIFEST),
        "results": results,
        "resource_summary": resources,
        "target_bottleneck_gate": bottleneck,
        "client_isolation_failures": client_isolation_failures,
        "failures": failures,
        "fresh_r0_complete": passed,
        "formal_outer_launch_authorized": passed,
        "formal_outer_started": False,
        "accepted_rounds": {scene: 0 for scene in freeze["active_scenes"]},
    }
    result_path = output / "r0_binding.json"
    result_path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result_path.with_name(result_path.name + ".sha256").write_text(
        f"{_sha(result_path)}  {result_path.name}\n", encoding="ascii"
    )
    print(json.dumps({
        "status": document["status"], "binding": str(result_path),
        "wall_time_seconds": document["wall_time_seconds"],
        "failure_count": len(failures) + len(client_isolation_failures),
    }), flush=True)
    return 0 if passed else 4


if __name__ == "__main__":
    raise SystemExit(main())
