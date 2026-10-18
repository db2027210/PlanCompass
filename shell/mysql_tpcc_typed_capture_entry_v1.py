#!/usr/bin/env python3
"""Run one local capture-only TPC-C typed L2/L3 evidence session."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import threading
import time
from typing import Any, Mapping, Sequence


_SOURCE = (
    Path(__file__).resolve().parents[3]
    / "history/mysql_gradient_debug/readiness_mysql_early_stop_v1"
    / "source-v28-b4a7a8bec9b033ae280cf4ad23459ed0a4d0abd81e8e25d2da0c3c6f7412dfb8"
)
if not (_SOURCE / "source_manifest.json").is_file():
    # A published bundle entry is nested below artifacts/<name>/<id>/capture.
    _SOURCE = (
        Path("/home/anonymous/program/resource_adaptive_mysql")
        / "history/mysql_gradient_debug/readiness_mysql_early_stop_v1"
        / "source-v28-b4a7a8bec9b033ae280cf4ad23459ed0a4d0abd81e8e25d2da0c3c6f7412dfb8"
    )
_SOURCE_MANIFEST = json.loads(
    (_SOURCE / "source_manifest.json").read_text(encoding="utf-8")
)
if (
    _SOURCE_MANIFEST.get("bundle_id")
    != "b4a7a8bec9b033ae280cf4ad23459ed0a4d0abd81e8e25d2da0c3c6f7412dfb8"
    or _SOURCE_MANIFEST.get("formal_scope") != "mysql_only"
    or _SOURCE_MANIFEST.get("cross_engine_comparison_prohibited") is not True
):
    raise RuntimeError("TPC-C capture source closure identity differs")
for _relative, _expected in _SOURCE_MANIFEST["files"].items():
    _candidate = _SOURCE / _relative
    if (
        _candidate.is_symlink()
        or not _candidate.is_file()
        or hashlib.sha256(_candidate.read_bytes()).hexdigest() != _expected
    ):
        raise RuntimeError(f"TPC-C capture source closure differs: {_relative}")
sys.path.insert(0, str(_SOURCE))

from shell.manual_mysql_sgd_r0_campaign import MYSQL
from shell.mysql_algorithm_c_local_services_v25 import (
    LocalCampaign,
    LocalProductionServices,
)
try:
    from mysql_process_classification import classify_process
except ModuleNotFoundError:
    from shell.mysql_process_classification_v10 import classify_process


EXPECTED_CPUSET = "208-215,464-471"
EXPECTED_MEMS = "1"
EXPECTED_NUMA_NODE = 1
EXPECTED_PORT = 65206
EXPECTED_DEVICE = "/dev/sda"
EXPECTED_READ_WRITE_BPS = 1205862400
EXPECTED_BUNDLE_SCHEMA = "mysql_tpcc_typed_runtime_identity_v1"
TRACE_SCHEMA = "mysql_tpcc_jdbc_typed_trace_v1"
CAPTURE_MEASUREMENT_SECONDS = 180
SESSION_MAXIMUM_MEASUREMENT_SECONDS = 600
CAPTURE_COVERAGE_RECHECK_SECONDS = 30


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    with temporary.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = value.encode("utf-8")
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    with temporary.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def load_object(path: Path) -> Mapping[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"required local artifact is absent or symlinked: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"JSON object required: {path}")
    return value


def trace_binding_coverage(
    trace_path: Path, manifest_path: Path
) -> dict[str, Any]:
    """Read only complete trace records and report exact manifest coverage."""
    manifest = load_object(manifest_path)
    statements = manifest.get("statements")
    if not isinstance(statements, list):
        raise ValueError("TPC-C typed manifest statements differ")
    required = {
        str(row["binding_id"])
        for row in statements
        if isinstance(row, Mapping) and isinstance(row.get("binding_id"), str)
    }
    if len(required) != 34:
        raise ValueError("TPC-C typed manifest must contain exactly 34 bindings")
    observed: set[str] = set()
    if trace_path.is_file():
        with trace_path.open("r", encoding="utf-8", errors="strict") as handle:
            for raw in handle:
                if not raw.endswith("\n"):
                    # The OWNER may currently be appending this record.
                    continue
                try:
                    record = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if (
                    isinstance(record, Mapping)
                    and record.get("record_type") == "statement"
                    and isinstance(record.get("binding_id"), str)
                ):
                    observed.add(str(record["binding_id"]))
    missing = sorted(required - observed)
    return {
        "required_binding_count": len(required),
        "observed_binding_count": len(required & observed),
        "complete": not missing,
        "missing_binding_ids": missing,
    }


def validate_bundle(bundle: Path) -> Mapping[str, Any]:
    manifest_path = bundle / "bundle_manifest.json"
    manifest = load_object(manifest_path)
    if (
        bundle.name != manifest.get("bundle_id")
        or manifest.get("schema_version") != EXPECTED_BUNDLE_SCHEMA
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("capture_only") is not True
        or manifest.get("outer_execution_authorized") is not False
        or (manifest.get("authorization") or {}).get("production_trace_collected")
        is not False
        or (manifest.get("authorization") or {}).get("formal_r0_authorized")
        is not False
    ):
        raise ValueError("TPC-C typed runtime bundle scope/authorization differs")
    files = manifest.get("files")
    required = {
        "runtime/benchbase-formal.jar",
        "runtime/lib/mysql-connector-java-8.0.30.jar",
        "collector/tpcc_typed_collector.py",
        "config/tpcc_offline_binding_manifest.json",
        "config/mysql_tpcc_sensitive_item_semijoin.sql",
        "orchestration/formal_tpcc_composite_workload.py",
        "capture/mysql_tpcc_typed_capture_entry_v1.py",
        "capture/mysql_tpcc_typed_interference_gate_v1.py",
        "capture/mysql_process_classification.py",
        "capture/mysql_numa_bind_exec_v1.py",
        "capture/tpcc_typed_capture_config_v1.json",
        "capture/tpcc_typed_capture_profile_v1.json",
    }
    if not isinstance(files, Mapping) or not required.issubset(files):
        raise ValueError("TPC-C typed runtime bundle file map differs")
    for relative, expected in files.items():
        candidate = bundle / str(relative)
        if (
            candidate.is_symlink()
            or not candidate.is_file()
            or sha256(candidate) != expected
        ):
            raise ValueError(f"TPC-C typed runtime content differs: {relative}")
    return manifest


def cpu_list(pid: int) -> str:
    for line in Path(f"/proc/{pid}/status").read_text(encoding="utf-8").splitlines():
        if line.startswith("Cpus_allowed_list:"):
            return line.split(":", 1)[1].strip()
    raise RuntimeError(f"process {pid} has no CPU affinity readback")


def memory_list(pid: int) -> str:
    for line in Path(f"/proc/{pid}/status").read_text(encoding="utf-8").splitlines():
        if line.startswith("Mems_allowed_list:"):
            return line.split(":", 1)[1].strip()
    raise RuntimeError(f"process {pid} has no NUMA affinity readback")


def anonymous_numa_evidence(pid: int) -> dict[str, list[Any]]:
    policies: set[str] = set()
    nodes: set[int] = set()
    for line in Path(f"/proc/{pid}/numa_maps").read_text(
        encoding="utf-8", errors="replace"
    ).splitlines():
        if " anon=" not in f" {line}" or " file=" in f" {line}":
            continue
        fields = line.split()
        if len(fields) > 1:
            policies.add(fields[1])
        nodes.update(
            int(value)
            for value in re.findall(
                r"(?:^| )N([0-9]+)=[0-9]+(?: |$)", line
            )
        )
    return {
        "nodes": sorted(nodes),
        "policies": sorted(policies),
    }


def strict_numa_execution(pid: int) -> bool:
    evidence = anonymous_numa_evidence(pid)
    return (
        set(evidence["nodes"]).issubset({EXPECTED_NUMA_NODE})
        and set(evidence["policies"]).issubset(
            {f"bind:{EXPECTED_NUMA_NODE}"}
        )
    )


def current_memory_policy_nodes() -> list[int]:
    class Bitmask(ctypes.Structure):
        _fields_ = [
            ("size", ctypes.c_ulong),
            ("maskp", ctypes.POINTER(ctypes.c_ulong)),
        ]

    numa = ctypes.CDLL("libnuma.so.1", use_errno=True)
    numa.numa_get_membind.restype = ctypes.POINTER(Bitmask)
    numa.numa_bitmask_isbitset.argtypes = [
        ctypes.POINTER(Bitmask),
        ctypes.c_uint,
    ]
    numa.numa_bitmask_isbitset.restype = ctypes.c_int
    numa.numa_bitmask_free.argtypes = [ctypes.POINTER(Bitmask)]
    mask = numa.numa_get_membind()
    if not mask:
        raise RuntimeError("libnuma memory-policy readback failed")
    maximum = int(numa.numa_max_node())
    try:
        return [
            node
            for node in range(maximum + 1)
            if numa.numa_bitmask_isbitset(mask, node)
        ]
    finally:
        numa.numa_bitmask_free(mask)


def true_benchbase_pid(parent: int, timeout_seconds: float = 30.0) -> int:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        children_path = Path(f"/proc/{parent}/task/{parent}/children")
        if children_path.is_file():
            for raw in children_path.read_text(encoding="ascii").split():
                pid = int(raw)
                cmdline = (
                    Path(f"/proc/{pid}/cmdline")
                    .read_bytes()
                    .replace(b"\0", b" ")
                    .decode("utf-8", errors="replace")
                )
                if "com.oltpbenchmark.DBWorkload" in cmdline:
                    return pid
        time.sleep(0.05)
    raise RuntimeError("true BenchBase Java PID was not discovered")


class TypedCaptureCampaign(LocalCampaign):
    bundle: Path
    capture_token: str
    trace_path: Path

    def _runtime_interference_rows(self) -> list[dict[str, Any]]:
        entries: dict[int, dict[str, Any]] = {}
        for directory in Path("/proc").iterdir():
            if not directory.name.isdigit():
                continue
            try:
                raw = (directory / "cmdline").read_bytes()
                argv = [
                    item.decode("utf-8", errors="replace")
                    for item in raw.split(b"\0")
                    if item
                ]
                if not argv:
                    continue
                stat_fields = (
                    (directory / "stat")
                    .read_text(encoding="ascii")
                    .split(") ", 1)[1]
                    .split()
                )
                status = {
                    key: value.strip()
                    for line in (directory / "status").read_text(
                        encoding="utf-8", errors="replace"
                    ).splitlines()
                    if ":" in line
                    for key, value in [line.split(":", 1)]
                }
                pid = int(directory.name)
                entries[pid] = {
                    "pid": pid,
                    "ppid": int(stat_fields[1]),
                    "start_ticks": int(stat_fields[19]),
                    "category": classify_process(" ".join(argv), argv=argv),
                    "command": " ".join(argv),
                    "cpuset_cpus": status.get("Cpus_allowed_list", ""),
                }
            except (
                FileNotFoundError,
                PermissionError,
                ProcessLookupError,
                ValueError,
            ):
                continue
        owned = {os.getpid()}
        changed = True
        while changed:
            before = len(owned)
            owned.update(
                pid
                for pid, row in entries.items()
                if int(row["ppid"]) in owned
            )
            changed = len(owned) != before
        target = {
            *range(208, 216),
            *range(464, 472),
        }
        violations: list[dict[str, Any]] = []
        for pid, row in sorted(entries.items()):
            if pid in owned or row["category"] not in {"benchmark", "controller"}:
                continue
            allowed: set[int] = set()
            try:
                for token in str(row["cpuset_cpus"]).split(","):
                    if "-" in token:
                        low, high = (int(value) for value in token.split("-", 1))
                        allowed.update(range(low, high + 1))
                    elif token:
                        allowed.add(int(token))
            except ValueError:
                allowed = set()
            overlap = sorted(target.intersection(allowed))
            if overlap:
                violations.append({
                    **row,
                    "overlap_cpus": overlap,
                    "command_sha256": hashlib.sha256(
                        str(row["command"]).encode("utf-8")
                    ).hexdigest(),
                })
        return violations

    def _runtime_interference_monitor(self) -> None:
        while not self._runtime_interference_stop.wait(1.0):
            violations = self._runtime_interference_rows()
            self._runtime_interference_sample_count += 1
            if violations:
                self._runtime_interference_error = {
                    "epoch_ms": int(time.time() * 1000),
                    "violations": violations,
                }
                atomic_json(
                    self.output / "runtime_process_interference_gate_v1.json",
                    {
                        "schema_version":
                            "mysql_tpcc_typed_runtime_process_interference_gate_v1",
                        "passed": False,
                        "status": "INTERFERENCE_DETECTED",
                        "formal_scope": "mysql_only",
                        "cross_engine_comparison_prohibited": True,
                        "capture_only": True,
                        "sample_count": self._runtime_interference_sample_count,
                        "first_violation": self._runtime_interference_error,
                    },
                )
                return

    def _start_runtime_interference_monitor(self) -> None:
        self._runtime_interference_stop = threading.Event()
        self._runtime_interference_sample_count = 0
        self._runtime_interference_error = None
        self._runtime_interference_thread = threading.Thread(
            target=self._runtime_interference_monitor,
            name="tpcc-runtime-process-interference",
            daemon=True,
        )
        self._runtime_interference_thread.start()

    def _assert_runtime_interference_clear(self) -> None:
        error = getattr(self, "_runtime_interference_error", None)
        if error is not None:
            raise RuntimeError(
                "TPC-C runtime process interference gate failed closed: "
                + ", ".join(
                    f"pid={row['pid']}:{row['category']}"
                    for row in error["violations"]
                )
            )

    def state(self, phase: str, **extra: Any) -> None:
        super().state(
            phase,
            formal_scope="mysql_only",
            cross_engine_comparison_prohibited=True,
            transport="local_no_ssh",
            capture_only=True,
            algorithm_c_authorized=False,
            outer_execution_authorized=False,
            accepted_round_authorized=False,
            **extra,
        )

    def _mysql_rows(self, sql: str) -> list[list[str]]:
        completed = subprocess.run(
            [
                str(MYSQL),
                f"--defaults-file={self.output / 'private/mysql.cnf'}",
                "--protocol=tcp",
                "--host=127.0.0.1",
                f"--port={self.lane.remote_port}",
                "--user=root",
                "--batch",
                "--raw",
                "--skip-column-names",
                "-e",
                sql,
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                "TPC-C Performance Schema control/readback failed: "
                + completed.stderr[-1000:]
            )
        return [row.split("\t") for row in completed.stdout.splitlines()]

    def enable_typed_performance_schema(self) -> None:
        self._mysql_rows(
            "UPDATE performance_schema.setup_consumers SET ENABLED='YES' "
            "WHERE NAME IN "
            "('events_statements_history_long','events_waits_history_long')"
        )
        rows = self._mysql_rows(
            "SELECT NAME,ENABLED FROM performance_schema.setup_consumers "
            "WHERE NAME IN "
            "('events_statements_history_long','events_waits_history_long') "
            "ORDER BY NAME"
        )
        columns = self._mysql_rows(
            "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA='performance_schema' "
            "AND TABLE_NAME='events_statements_history_long' "
            "AND COLUMN_NAME IN ('EVENT_ID','CPU_TIME','LOCK_TIME') "
            "ORDER BY COLUMN_NAME"
        )
        observed = {row[0]: row[1] for row in rows if len(row) == 2}
        column_names = sorted(row[0] for row in columns if row)
        if observed != {
            "events_statements_history_long": "YES",
            "events_waits_history_long": "YES",
        } or column_names != ["CPU_TIME", "EVENT_ID", "LOCK_TIME"]:
            raise RuntimeError("TPC-C required Performance Schema evidence is unavailable")
        atomic_json(
            self.output / "typed_performance_schema_readback.json",
            {
                "schema_version": "mysql_tpcc_typed_performance_schema_readback_v1",
                "formal_scope": "mysql_only",
                "cross_engine_comparison_prohibited": True,
                "capture_only": True,
                "outer_execution_authorized": False,
                "consumers": observed,
                "statement_columns": column_names,
            },
        )

    def tpcc_session(self, protocol: Mapping[str, Any]) -> Any:
        session = super().tpcc_session(protocol)
        original_builder = session.command_builder
        bundle = self.bundle

        def command(stop: Path) -> Sequence[str]:
            arguments = [str(value) for value in original_builder(stop)]
            composite = str(
                _SOURCE / "shell/formal_tpcc_composite_workload.py"
            )
            bundle_composite = str(
                bundle / "orchestration/formal_tpcc_composite_workload.py"
            )
            arguments = [
                bundle_composite if value == composite else value
                for value in arguments
            ]
            for index, value in enumerate(arguments[:-1]):
                if value == "-cp":
                    arguments[index + 1] = os.pathsep.join(
                        (
                            str(bundle / "runtime/benchbase-formal.jar"),
                            str(bundle / "runtime/lib/*"),
                        )
                    )
            sensitive = str(
                bundle / "config/mysql_tpcc_sensitive_item_semijoin.sql"
            )
            marker = arguments.index("--sensitive-sql")
            arguments[marker + 1] = sensitive
            return arguments

        session.command_builder = command
        return session

    def execute_typed_capture(self) -> None:
        self.output.mkdir(parents=True, exist_ok=False)
        self.state("PREPARING_TYPED_CAPTURE")
        self.prepare_remote()
        self.state("STARTING_TUNNEL_AND_SAMPLER")
        self.start_tunnel()
        self.apply_candidate_costs()
        self.start_sampler()
        self.enable_typed_performance_schema()
        gate_path = self.output / "dynamic_interference_gate_v1.json"
        self.state(
            "DYNAMIC_INTERFERENCE_GATE_RUNNING",
            controller_pid=os.getpid(),
            gate_path=str(gate_path),
            window_seconds=60,
        )
        gate_completed = subprocess.run(
            [
                "sudo",
                "-n",
                "timeout",
                "--signal=TERM",
                "--kill-after=5",
                "180",
                "/home/anonymous/miniconda3/envs/py313/bin/python",
                str(
                    self.bundle
                    / "capture/mysql_tpcc_typed_interference_gate_v1.py"
                ),
                "--resource-readback",
                str(self.output / "resource_readback.json"),
                "--controller-pid",
                str(os.getpid()),
                "--capture-token",
                self.capture_token,
                "--output",
                str(gate_path),
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=210,
            check=False,
        )
        (self.output / "dynamic_interference_gate.stdout.log").write_text(
            gate_completed.stdout, encoding="utf-8"
        )
        (self.output / "dynamic_interference_gate.stderr.log").write_text(
            gate_completed.stderr, encoding="utf-8"
        )
        if not gate_path.is_file() or gate_path.is_symlink():
            self.state(
                "DYNAMIC_INTERFERENCE_GATE_FAILED",
                controller_pid=os.getpid(),
                gate_path=str(gate_path),
                gate_returncode=gate_completed.returncode,
                error="gate process did not publish its fail-closed artifact",
            )
            raise RuntimeError(
                "TPC-C dynamic non-interference gate published no artifact"
            )
        gate = load_object(gate_path)
        gate_sidecar = gate_path.with_name(gate_path.name + ".sha256")
        if (
            gate_completed.returncode != 0
            or gate.get("schema_version")
            != "mysql_tpcc_typed_dynamic_interference_gate_v1"
            or gate.get("passed") is not True
            or gate.get("capture_token") != self.capture_token
            or gate.get("controller_pid") != os.getpid()
            or not gate_sidecar.is_file()
            or gate_sidecar.read_text(encoding="ascii")
            != f"{sha256(gate_path)}  {gate_path.name}\n"
        ):
            self.state(
                "DYNAMIC_INTERFERENCE_GATE_FAILED",
                controller_pid=os.getpid(),
                gate_path=str(gate_path),
                gate_sha256=sha256(gate_path),
                gate_status=gate.get("status"),
                error=gate.get("error"),
            )
            raise RuntimeError("TPC-C dynamic non-interference gate failed closed")
        self.state(
            "DYNAMIC_INTERFERENCE_GATE_PASSED",
            controller_pid=os.getpid(),
            gate_path=str(gate_path),
            gate_sha256=sha256(gate_path),
        )
        self._start_runtime_interference_monitor()

        classpath = os.pathsep.join(
            (
                str(self.bundle / "runtime/benchbase-formal.jar"),
                str(self.bundle / "runtime/lib/*"),
            )
        )
        target_url = (
            f"jdbc:mysql://127.0.0.1:{EXPECTED_PORT}/{self.lane.database}"
            "?useUnicode=true&characterEncoding=utf-8&useSSL=false"
            "&allowPublicKeyRetrieval=true&rewriteBatchedStatements=true"
        )
        environment = {
            "MYSQL_TPCC_TYPED_TRACE_PATH": str(self.trace_path),
            "MYSQL_TPCC_TYPED_MANIFEST_PATH": str(
                self.bundle / "config/tpcc_offline_binding_manifest.json"
            ),
            "MYSQL_TPCC_TYPED_MANIFEST_SHA256": sha256(
                self.bundle / "config/tpcc_offline_binding_manifest.json"
            ),
            "MYSQL_TPCC_TYPED_CAPTURE_TOKEN": self.capture_token,
            "MYSQL_TPCC_TYPED_METRICS_JDBC_URL": target_url,
            "MYSQL_TPCC_TYPED_DEFAULTS_FILE": "/opt/mysql-tpcc-config/mysql.cnf",
            "MYSQL_TPCC_TYPED_TARGET_JDBC_URL": target_url,
            "MYSQL_TPCC_TYPED_CORRECTNESS_VERIFIED": "1",
            "MYSQL_TPCC_TYPED_CAPTURE_ONLY": "1",
            "MYSQL_TPCC_TYPED_OUTER_AUTHORIZED": "0",
            "MYSQL_TPCC_TYPED_TRACE_ROLE": "OWNER",
            "MYSQL_TPCC_TYPED_BENCHBASE_CLASSPATH": classpath,
            "MYSQL_TPCC_TYPED_SENSITIVE_SQL_PATH": str(
                self.bundle / "config/mysql_tpcc_sensitive_item_semijoin.sql"
            ),
        }
        previous = {name: os.environ.get(name) for name in environment}
        os.environ.update(environment)
        try:
            self.session = self.make_session()
            self.session.start(
                warmup_seconds=60,
                maximum_measurement_seconds=SESSION_MAXIMUM_MEASUREMENT_SECONDS,
            )
            self.wait_initial_marker()
            wrapper_pid = int(self.session.process.pid)
            workload_pid = true_benchbase_pid(wrapper_pid)
            if (
                cpu_list(os.getpid()) != EXPECTED_CPUSET
                or cpu_list(wrapper_pid) != EXPECTED_CPUSET
                or cpu_list(workload_pid) != EXPECTED_CPUSET
                or not strict_numa_execution(os.getpid())
                or not strict_numa_execution(wrapper_pid)
                or not strict_numa_execution(workload_pid)
            ):
                raise RuntimeError(
                    "TPC-C controller/wrapper/workload CPU/NUMA affinity differs"
                )
            self.state(
                "TYPED_CAPTURE_RUNNING",
                immutable_runtime_bundle_id=self.bundle.name,
                immutable_runtime_bundle_manifest_sha256=sha256(
                    self.bundle / "bundle_manifest.json"
                ),
                capture_token=self.capture_token,
                controller_pid=os.getpid(),
                wrapper_pid=wrapper_pid,
                workload_pid=workload_pid,
                controller_cpuset=cpu_list(os.getpid()),
                wrapper_cpuset=cpu_list(wrapper_pid),
                workload_cpuset=cpu_list(workload_pid),
                controller_mems=memory_list(os.getpid()),
                wrapper_mems=memory_list(wrapper_pid),
                workload_mems=memory_list(workload_pid),
                controller_anonymous_numa=anonymous_numa_evidence(
                    os.getpid()
                ),
                wrapper_anonymous_numa=anonymous_numa_evidence(wrapper_pid),
                workload_anonymous_numa=anonymous_numa_evidence(
                    workload_pid
                ),
            )
            coverage_history: list[dict[str, Any]] = []
            manifest_path = (
                self.bundle / "config/tpcc_offline_binding_manifest.json"
            )
            result = None
            for measurement_seconds in range(
                CAPTURE_MEASUREMENT_SECONDS,
                SESSION_MAXIMUM_MEASUREMENT_SECONDS + 1,
                CAPTURE_COVERAGE_RECHECK_SECONDS,
            ):
                self._assert_runtime_interference_clear()
                self.session.wait_until_measurement_end(measurement_seconds)
                self._assert_runtime_interference_clear()
                coverage = trace_binding_coverage(
                    self.trace_path, manifest_path
                )
                coverage_history.append(
                    {
                        **coverage,
                        "measurement_seconds": measurement_seconds,
                    }
                )
                atomic_json(
                    self.output / "typed_capture_coverage.json",
                    {
                        "schema_version":
                            "mysql_tpcc_typed_capture_coverage_v1",
                        "capture_only": True,
                        "qps_resource_input_prohibited": True,
                        "checks": coverage_history,
                        "latest": coverage_history[-1],
                    },
                )
                if (
                    coverage["complete"]
                    or measurement_seconds
                    == SESSION_MAXIMUM_MEASUREMENT_SECONDS
                ):
                    result = self.session.stop_after_measurement(
                        measurement_seconds,
                        graceful_drain_seconds=120,
                    )
                    break
            if result is None:
                raise RuntimeError("TPC-C typed coverage decision was not made")
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

        correctness = result.get("correctness")
        self._assert_runtime_interference_clear()
        if not isinstance(correctness, Mapping) or correctness.get("passed") is not True:
            raise RuntimeError("TPC-C capture correctness/invariant did not pass")
        trace_header = json.loads(
            self.trace_path.read_text(encoding="utf-8").splitlines()[0]
        )
        if (
            not isinstance(trace_header, Mapping)
            or trace_header.get("record_type") != "header"
            or trace_header.get("schema_version") != TRACE_SCHEMA
            or trace_header.get("capture_token") != self.capture_token
            or trace_header.get("manifest_sha256") != sha256(manifest_path)
            or trace_header.get("capture_only") is not True
            or trace_header.get("outer_execution_authorized") is not False
            or trace_header.get("correctness_verified") is not True
            or trace_header.get("qps_included") is not False
        ):
            raise RuntimeError("TPC-C typed trace header authority differs")
        trace_sha256 = sha256(self.trace_path)
        atomic_text(
            self.trace_path.with_name(self.trace_path.name + ".sha256"),
            f"{trace_sha256}  {self.trace_path.name}\n",
        )
        collector_output = self.output / "typed_collector_output"
        completed = subprocess.run(
            [
                "/home/anonymous/miniconda3/envs/py313/bin/python",
                str(self.bundle / "collector/tpcc_typed_collector.py"),
                "--manifest",
                str(self.bundle / "config/tpcc_offline_binding_manifest.json"),
                "--trace",
                str(self.trace_path),
                "--output-dir",
                str(collector_output),
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
            check=False,
        )
        (self.output / "typed_collector.stdout.log").write_text(
            completed.stdout, encoding="utf-8"
        )
        (self.output / "typed_collector.stderr.log").write_text(
            completed.stderr, encoding="utf-8"
        )
        if completed.returncode != 0:
            raise RuntimeError(
                "TPC-C typed collector failed closed: " + completed.stderr[-2000:]
            )
        validation = load_object(collector_output / "tpcc_agent_validation.json")
        expected_transaction_types = [
            "NewOrder",
            "Payment",
            "OrderStatus",
            "Delivery",
            "StockLevel",
        ]
        l2_transactions = validation.get("l2_transactions")
        l3_vectors = validation.get("l3_vectors")
        samples_by_transaction = validation.get("sample_count_by_transaction")
        if (
            validation.get("schema_version")
            != "mysql_tpcc_typed_binding_l2_l3_v1"
            or validation.get("formal_scope") != "mysql_only"
            or validation.get("cross_engine_comparison_prohibited") is not True
            or validation.get("manifest_sha256") != sha256(manifest_path)
            or validation.get("trace_sha256") != trace_sha256
            or validation.get("binding_count") != 34
            or validation.get("observed_binding_count") != 34
            or validation.get("transaction_types") != expected_transaction_types
            or not isinstance(l2_transactions, list)
            or not l2_transactions
            or {row.get("transaction_type") for row in l2_transactions}
            != set(expected_transaction_types)
            or not isinstance(l3_vectors, Mapping)
            or set(l3_vectors) != set(expected_transaction_types)
            or any(
                not isinstance(l3_vectors[name], Mapping)
                or set(l3_vectors[name]) != {
                    "cpu_time_ms",
                    "lock_wait_ms",
                    "io_read_bytes",
                    "io_write_bytes",
                    "io_wait_ms",
                    "latency_p50_ms",
                    "latency_p95_ms",
                    "latency_p99_ms",
                }
                for name in expected_transaction_types
            )
            or not isinstance(samples_by_transaction, Mapping)
            or set(samples_by_transaction) != set(expected_transaction_types)
            or any(
                not isinstance(samples_by_transaction[name], int)
                or samples_by_transaction[name] <= 0
                for name in expected_transaction_types
            )
            or validation.get("qps_included") is not False
            or validation.get("correctness_verified") is not True
            or validation.get("capture_workload_authorized") is not True
            or validation.get("outer_execution_authorized") is not False
            or validation.get("execution_authorized") is not False
        ):
            raise RuntimeError("TPC-C typed collector authorization differs")
        collector_validation_path = (
            collector_output / "tpcc_agent_validation.json"
        )
        collector_l2_path = (
            collector_output / "tpcc_agent_l2_transactions.json"
        )
        collector_l3_path = collector_output / "tpcc_agent_l3_vectors.json"
        coverage_path = self.output / "typed_capture_coverage.json"
        resource_readback_path = self.output / "resource_readback.json"
        effective_resource_readback_path = (
            self.output / "effective_resource_readback_v2.json"
        )
        performance_schema_readback_path = (
            self.output / "typed_performance_schema_readback.json"
        )
        required_runtime_evidence = (
            coverage_path,
            resource_readback_path,
            effective_resource_readback_path,
            performance_schema_readback_path,
        )
        if any(
            path.is_symlink() or not path.is_file()
            for path in required_runtime_evidence
        ):
            raise RuntimeError(
                "TPC-C typed capture runtime evidence is absent"
            )
        capture_result_path = self.output / "typed_capture_result.json"
        atomic_json(
            capture_result_path,
            {
                "schema_version": "mysql_tpcc_typed_capture_result_v1",
                "status": "PASS_CAPTURE_ONLY",
                "formal_scope": "mysql_only",
                "cross_engine_comparison_prohibited": True,
                "capture_only": True,
                "r0": False,
                "candidate": False,
                "top5_replay": False,
                "accepted_round": False,
                "qps_resource_input_prohibited": True,
                "capture_token": self.capture_token,
                "immutable_runtime_bundle_id": self.bundle.name,
                "immutable_runtime_bundle_manifest_path": str(
                    self.bundle / "bundle_manifest.json"
                ),
                "immutable_runtime_bundle_manifest_sha256": sha256(
                    self.bundle / "bundle_manifest.json"
                ),
                "bundle_bound_capture_config_path": str(
                    self.bundle / "capture/tpcc_typed_capture_config_v1.json"
                ),
                "bundle_bound_capture_config_sha256": sha256(
                    self.bundle / "capture/tpcc_typed_capture_config_v1.json"
                ),
                "bundle_bound_collector_path": str(
                    self.bundle / "collector/tpcc_typed_collector.py"
                ),
                "bundle_bound_collector_sha256": sha256(
                    self.bundle / "collector/tpcc_typed_collector.py"
                ),
                "typed_binding_manifest_path": str(manifest_path),
                "typed_binding_manifest_sha256": sha256(manifest_path),
                "trace_path": str(self.trace_path),
                "trace_sha256": trace_sha256,
                "trace_sha256_sidecar_path": str(
                    self.trace_path.with_name(self.trace_path.name + ".sha256")
                ),
                "collector_validation_path": str(collector_validation_path),
                "collector_validation_sha256": sha256(
                    collector_validation_path
                ),
                "collector_l2_path": str(collector_l2_path),
                "collector_l2_sha256": sha256(collector_l2_path),
                "collector_l3_path": str(collector_l3_path),
                "collector_l3_sha256": sha256(collector_l3_path),
                "coverage_path": str(coverage_path),
                "coverage_sha256": sha256(coverage_path),
                "resource_readback_path": str(resource_readback_path),
                "resource_readback_sha256": sha256(resource_readback_path),
                "effective_resource_readback_path": str(
                    effective_resource_readback_path
                ),
                "effective_resource_readback_sha256": sha256(
                    effective_resource_readback_path
                ),
                "performance_schema_readback_path": str(
                    performance_schema_readback_path
                ),
                "performance_schema_readback_sha256": sha256(
                    performance_schema_readback_path
                ),
                "correctness_evidence": dict(correctness),
                "correctness_invariant_passed": True,
                "dynamic_interference_gate_path": str(gate_path),
                "dynamic_interference_gate_sha256": sha256(gate_path),
                "raw_benchbase_performance_usable": False,
                "observed_binding_count": 34,
                "measurement_seconds": result.get("measurement_seconds"),
                "coverage_checks": coverage_history,
                "transaction_types": validation.get("transaction_types"),
                "capture_workload_authorized": True,
                "formal_r0_authorized": False,
                "algorithm_c_authorized": False,
                "outer_execution_authorized": False,
                "accepted_round_authorized": False,
                "execution_authorized": False,
            },
        )
        atomic_text(
            capture_result_path.with_name(capture_result_path.name + ".sha256"),
            f"{sha256(capture_result_path)}  {capture_result_path.name}\n",
        )
        self.state(
            "TYPED_CAPTURE_COMPLETE",
            immutable_runtime_bundle_id=self.bundle.name,
            immutable_runtime_bundle_manifest_sha256=sha256(
                self.bundle / "bundle_manifest.json"
            ),
            capture_token=self.capture_token,
            trace_sha256=sha256(self.trace_path),
            observed_binding_count=34,
            capture_workload_authorized=True,
            execution_authorized=False,
        )

    def cleanup(self) -> None:
        stop = getattr(self, "_runtime_interference_stop", None)
        thread = getattr(self, "_runtime_interference_thread", None)
        if stop is not None:
            stop.set()
        if thread is not None:
            thread.join(timeout=5)
            if (
                not thread.is_alive()
                and getattr(self, "_runtime_interference_error", None) is None
            ):
                atomic_json(
                    self.output / "runtime_process_interference_gate_v1.json",
                    {
                        "schema_version":
                            "mysql_tpcc_typed_runtime_process_interference_gate_v1",
                        "passed": True,
                        "status": "PASS",
                        "formal_scope": "mysql_only",
                        "cross_engine_comparison_prohibited": True,
                        "capture_only": True,
                        "sample_count": self._runtime_interference_sample_count,
                        "first_violation": None,
                    },
                )
        super().cleanup()
        capture_result_path = self.output / "typed_capture_result.json"
        runtime_gate_path = (
            self.output / "runtime_process_interference_gate_v1.json"
        )
        dynamic_gate_path = self.output / "dynamic_interference_gate_v1.json"
        if capture_result_path.is_file():
            if (
                not runtime_gate_path.is_file()
                or not dynamic_gate_path.is_file()
            ):
                raise RuntimeError(
                    "TPC-C typed completion gates are absent after cleanup"
                )
            capture_result = load_object(capture_result_path)
            runtime_gate = load_object(runtime_gate_path)
            dynamic_gate = load_object(dynamic_gate_path)
            trace_sidecar_path = Path(
                str(capture_result.get("trace_sha256_sidecar_path"))
            )
            result_sidecar_path = capture_result_path.with_name(
                capture_result_path.name + ".sha256"
            )
            if (
                capture_result.get("status") != "PASS_CAPTURE_ONLY"
                or capture_result.get("capture_token") != self.capture_token
                or capture_result.get("immutable_runtime_bundle_id")
                != self.bundle.name
                or capture_result.get(
                    "immutable_runtime_bundle_manifest_sha256"
                )
                != sha256(self.bundle / "bundle_manifest.json")
                or capture_result.get("bundle_bound_collector_sha256")
                != sha256(
                    self.bundle / "collector/tpcc_typed_collector.py"
                )
                or capture_result.get("bundle_bound_capture_config_sha256")
                != sha256(
                    self.bundle
                    / "capture/tpcc_typed_capture_config_v1.json"
                )
                or capture_result.get("trace_sha256")
                != sha256(Path(str(capture_result.get("trace_path"))))
                or capture_result.get("collector_validation_sha256")
                != sha256(
                    Path(str(capture_result.get("collector_validation_path")))
                )
                or capture_result.get("collector_l2_sha256")
                != sha256(Path(str(capture_result.get("collector_l2_path"))))
                or capture_result.get("collector_l3_sha256")
                != sha256(Path(str(capture_result.get("collector_l3_path"))))
                or capture_result.get("coverage_sha256")
                != sha256(Path(str(capture_result.get("coverage_path"))))
                or capture_result.get("resource_readback_sha256")
                != sha256(
                    Path(str(capture_result.get("resource_readback_path")))
                )
                or capture_result.get("effective_resource_readback_sha256")
                != sha256(
                    Path(
                        str(
                            capture_result.get(
                                "effective_resource_readback_path"
                            )
                        )
                    )
                )
                or capture_result.get("performance_schema_readback_sha256")
                != sha256(
                    Path(
                        str(
                            capture_result.get(
                                "performance_schema_readback_path"
                            )
                        )
                    )
                )
                or not isinstance(
                    capture_result.get("correctness_evidence"), Mapping
                )
                or capture_result["correctness_evidence"].get("passed")
                is not True
                or not trace_sidecar_path.is_file()
                or trace_sidecar_path.read_text(encoding="ascii")
                != (
                    f"{capture_result.get('trace_sha256')}  "
                    f"{Path(str(capture_result.get('trace_path'))).name}\n"
                )
                or not result_sidecar_path.is_file()
                or result_sidecar_path.read_text(encoding="ascii")
                != (
                    f"{sha256(capture_result_path)}  "
                    f"{capture_result_path.name}\n"
                )
                or capture_result.get("capture_only") is not True
                or capture_result.get("r0") is not False
                or capture_result.get("accepted_round") is not False
                or capture_result.get("capture_workload_authorized") is not True
                or capture_result.get("formal_r0_authorized") is not False
                or capture_result.get("algorithm_c_authorized") is not False
                or capture_result.get("outer_execution_authorized") is not False
                or capture_result.get("accepted_round_authorized") is not False
                or capture_result.get("execution_authorized") is not False
                or runtime_gate.get("passed") is not True
                or runtime_gate.get("status") != "PASS"
                or dynamic_gate.get("passed") is not True
                or dynamic_gate.get("status") != "PASS"
            ):
                raise RuntimeError(
                    "TPC-C typed post-cleanup authority differs"
                )
            completion_path = (
                self.output / "typed_capture_completion_authority.json"
            )
            atomic_json(
                completion_path,
                {
                    "schema_version":
                        "mysql_tpcc_typed_capture_completion_authority_v1",
                    "status": "PASS_CAPTURE_ONLY_COMPLETE",
                    "formal_scope": "mysql_only",
                    "cross_engine_comparison_prohibited": True,
                    "capture_token": self.capture_token,
                    "immutable_runtime_bundle_id": self.bundle.name,
                    "immutable_runtime_bundle_manifest_sha256": sha256(
                        self.bundle / "bundle_manifest.json"
                    ),
                    "typed_capture_result_path": str(capture_result_path),
                    "typed_capture_result_sha256": sha256(
                        capture_result_path
                    ),
                    "runtime_process_interference_gate_path": str(
                        runtime_gate_path
                    ),
                    "runtime_process_interference_gate_sha256": sha256(
                        runtime_gate_path
                    ),
                    "dynamic_interference_gate_path": str(
                        dynamic_gate_path
                    ),
                    "dynamic_interference_gate_sha256": sha256(
                        dynamic_gate_path
                    ),
                    "trace_path": capture_result["trace_path"],
                    "trace_sha256": capture_result["trace_sha256"],
                    "collector_validation_sha256":
                        capture_result["collector_validation_sha256"],
                    "collector_l2_sha256":
                        capture_result["collector_l2_sha256"],
                    "collector_l3_sha256":
                        capture_result["collector_l3_sha256"],
                    "coverage_sha256": capture_result["coverage_sha256"],
                    "resource_readback_sha256":
                        capture_result["resource_readback_sha256"],
                    "effective_resource_readback_sha256":
                        capture_result[
                            "effective_resource_readback_sha256"
                        ],
                    "performance_schema_readback_sha256":
                        capture_result[
                            "performance_schema_readback_sha256"
                        ],
                    "correctness_evidence":
                        capture_result["correctness_evidence"],
                    "capture_workload_authorized": True,
                    "capture_result_is_r0": False,
                    "capture_result_is_accepted_round": False,
                    "formal_r0_authorized": False,
                    "algorithm_c_authorized": False,
                    "outer_execution_authorized": False,
                    "accepted_round_authorized": False,
                    "qps_resource_input_prohibited": True,
                },
            )
            atomic_text(
                completion_path.with_name(completion_path.name + ".sha256"),
                f"{sha256(completion_path)}  {completion_path.name}\n",
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--capture-token", required=True)
    args = parser.parse_args()
    if socket.gethostname() != "compute00":
        raise SystemExit("TPC-C typed capture is local to compute00")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{16,128}", args.capture_token):
        raise SystemExit("invalid TPC-C typed capture token")
    expected_config = args.bundle / "capture/tpcc_typed_capture_config_v1.json"
    if args.config.resolve(strict=True) != expected_config.resolve(strict=True):
        raise SystemExit("TPC-C typed capture must use the bundle-bound config")
    config = dict(load_object(args.config))
    config["profile"] = str(
        (args.bundle / "capture/tpcc_typed_capture_profile_v1.json").resolve(
            strict=True
        )
    )
    validate_bundle(args.bundle)
    policy_nodes = current_memory_policy_nodes()
    if policy_nodes != [EXPECTED_NUMA_NODE]:
        raise SystemExit(
            "TPC-C typed capture must be exec-launched with strict NUMA node 1 "
            f"memory policy; observed={policy_nodes}"
        )
    profile = load_object(Path(str(config.get("profile"))))
    if (
        config.get("schema_version") != "mysql_tpcc_typed_capture_config_v1"
        or config.get("formal_scope") != "mysql_only"
        or config.get("cross_engine_comparison_prohibited") is not True
        or config.get("capture_only") is not True
        or config.get("execution_authorized") is not False
        or profile.get("cpuset") != EXPECTED_CPUSET
        or profile.get("cpu_cores") != 4.0
        or profile.get("read_bps") != EXPECTED_READ_WRITE_BPS
        or profile.get("write_bps") != EXPECTED_READ_WRITE_BPS
    ):
        raise SystemExit("TPC-C typed capture config/profile differs")
    services = LocalProductionServices(config)
    if (
        services.lane.remote_port != EXPECTED_PORT
        or services.lane.device != EXPECTED_DEVICE
        or services.lane.cpuset != EXPECTED_CPUSET
    ):
        raise SystemExit("TPC-C typed local lane differs")
    run_id = args.capture_token
    campaign = TypedCaptureCampaign(
        services.lane,
        services.points / run_id,
        run_id,
        candidate_costs=None,
        measurement_seconds=SESSION_MAXIMUM_MEASUREMENT_SECONDS,
        minimum_measurement_seconds=CAPTURE_MEASUREMENT_SECONDS,
        minimum_client_completions=0,
        require_r0_profile_gate=False,
    )
    campaign.bundle = args.bundle.resolve(strict=True)
    campaign.capture_token = args.capture_token
    campaign.trace_path = campaign.output / "tpcc_typed_trace.jsonl"
    prelaunch_path = (
        campaign.output.parent / f".{run_id}.numa_policy_prelaunch.json"
    )
    reserved_paths = (
        prelaunch_path,
        campaign.output / "campaign_runtime.json",
        campaign.trace_path,
        campaign.trace_path.with_name(campaign.trace_path.name + ".lock"),
        campaign.trace_path.with_name(campaign.trace_path.name + ".sha256"),
        campaign.output / "typed_capture_coverage.json",
        campaign.output / "typed_collector_output",
        campaign.output / "typed_capture_result.json",
        campaign.output / "typed_capture_result.json.sha256",
        campaign.output / "typed_capture_completion_authority.json",
        campaign.output / "typed_capture_completion_authority.json.sha256",
        campaign.output / "dynamic_interference_gate_v1.json",
        campaign.output / "runtime_process_interference_gate_v1.json",
    )
    if any(path.exists() or path.is_symlink() for path in reserved_paths):
        raise SystemExit(
            "fresh TPC-C typed capture token/output authority already exists"
        )
    try:
        atomic_json(
            prelaunch_path,
            {
                "schema_version": "mysql_tpcc_typed_numa_policy_prelaunch_v1",
                "formal_scope": "mysql_only",
                "cross_engine_comparison_prohibited": True,
                "capture_only": True,
                "capture_token": run_id,
                "controller_pid": os.getpid(),
                "memory_policy_nodes": policy_nodes,
                "expected_node": EXPECTED_NUMA_NODE,
                "passed": True,
            },
        )
        campaign.execute_typed_capture()
        return 0
    finally:
        campaign.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
