#!/usr/bin/env python3
"""Materialize and execute fail-closed TPC-C v3 adopted-service commands.

The five phases share one attempt/handoff token and one live DB identity.
Materialization never grants accepted-round credit. Qualification and capture
are explicitly non-formal; R0/debug require their own sealed phase authority;
formal10 additionally requires a READY audit produced by the sealed readiness
verifier.  Execution re-adopts the service before and after the workload.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time
from typing import Any, Mapping, Sequence
import zipfile


PHASES = ("qualification", "typed_capture", "fresh_r0", "debug", "formal10_launch")
LEDGER_SCHEMA = "mysql_tpcc_same_live_container_formal10_handoff_v2"
JAVA = "/opt/mysql-tpcc-jdk/bin/java"
PYTHON = "/opt/mysql-tpcc-python/bin/python3.11"
JDK_TREE_SHA256 = (
    "578395e883d31c692a1c01c37910d22dbdafbcf95e373c7e0f6776a26f5c844c"
)
PYTHON_TREE_SHA256 = (
    "3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89"
)
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
PLACEHOLDER_RE = re.compile(r"(?:REPLACE_WITH|OMIT_FOR_|COPY_EXACTLY|\$\{)")
BENCHBASE_METRICS_PREFIX = "BENCHBASE_FORMAL_METRICS_JSON="
STOP_PROTOCOL_ERRORS = (
    "stop request must be decimal",
    "stop request must be one of 180,210,...,",
    "BENCHBASE_FORMAL_STOP_FILE must be a regular file",
)


class PhaseError(RuntimeError):
    pass


def _require_host_root() -> None:
    if os.geteuid() != 0 or os.getegid() != 0:
        raise PhaseError(
            "TPC-C phase authority requires effective uid/gid 0"
        )


def canonical(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False)
        + "\n"
    ).encode("utf-8")


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise PhaseError(f"regular JSON file required: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise PhaseError(f"JSON object required: {path}")
    sidecar = path.with_name(path.name + ".sha256")
    if sidecar.exists():
        expected = f"{sha256(path)}  {path.name}\n"
        if sidecar.is_symlink() or sidecar.read_text(encoding="ascii") != expected:
            raise PhaseError(f"SHA-256 sidecar differs: {path}")
    return value


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    sidecar = path.with_name(path.name + ".sha256")
    sidecar_temporary = sidecar.with_name(f".{sidecar.name}.{os.getpid()}.tmp")
    payload = canonical(value)
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        digest = sha256(temporary)
        with sidecar_temporary.open("xb") as stream:
            stream.write(f"{digest}  {path.name}\n".encode("ascii"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.replace(sidecar_temporary, sidecar)
    finally:
        for candidate in (temporary, sidecar_temporary):
            if candidate.exists():
                candidate.unlink()


def atomic_bytes(path: Path, payload: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    sidecar = path.with_name(path.name + ".sha256")
    sidecar_temporary = sidecar.with_name(
        f".{sidecar.name}.{os.getpid()}.{time.time_ns()}.tmp"
    )
    try:
        with temporary.open("xb") as stream:
            os.chmod(temporary, mode)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        digest = sha256(temporary)
        with sidecar_temporary.open("xb") as stream:
            os.chmod(sidecar_temporary, mode)
            stream.write(f"{digest}  {path.name}\n".encode("ascii"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.replace(sidecar_temporary, sidecar)
    finally:
        for candidate in (temporary, sidecar_temporary):
            if candidate.exists():
                candidate.unlink()


def _output_bytes(value: bytes | str | None) -> bytes:
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    raise PhaseError("subprocess output type differs")


def qualification_terminal_metrics(
    stdout: bytes | str | None, stderr: bytes | str | None
) -> dict[str, Any]:
    streams = {
        "stdout": _output_bytes(stdout).decode("utf-8", errors="strict"),
        "stderr": _output_bytes(stderr).decode("utf-8", errors="strict"),
    }
    stop_errors = [
        marker
        for marker in STOP_PROTOCOL_ERRORS
        if any(marker in text for text in streams.values())
    ]
    if stop_errors:
        raise PhaseError(
            "BenchBase qualification stop-file protocol error: "
            + ", ".join(stop_errors)
        )
    marked = [
        (stream_name, line)
        for stream_name, text in streams.items()
        for line in text.splitlines()
        if BENCHBASE_METRICS_PREFIX in line
    ]
    if len(marked) != 1:
        raise PhaseError(
            "qualification requires exactly one BENCHBASE_FORMAL_METRICS_JSON"
        )
    stream_name, line = marked[0]
    if stream_name != "stdout" or not line.startswith(BENCHBASE_METRICS_PREFIX):
        raise PhaseError("qualification terminal metrics marker placement differs")
    payload = line[len(BENCHBASE_METRICS_PREFIX):]
    try:
        metrics = json.loads(
            payload,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON value: {value}")
            ),
        )
    except (json.JSONDecodeError, TypeError, ValueError) as error:
        raise PhaseError("qualification terminal metrics JSON is malformed") from error
    if not isinstance(metrics, dict):
        raise PhaseError("qualification terminal metrics must be an object")
    measurement_seconds = metrics.get("measurement_seconds")
    stop_observed = metrics.get("stop_request_observed_epoch_ms")
    completed = metrics.get("completed_total")
    unexpected_errors = metrics.get("unexpected_errors")
    if (
        not isinstance(measurement_seconds, int)
        or isinstance(measurement_seconds, bool)
        or measurement_seconds < 180
        or measurement_seconds > 600
        or measurement_seconds % 30 != 0
    ):
        raise PhaseError("qualification measured window must be 180..600 by 30")
    if metrics.get("stop_requested") is not True:
        raise PhaseError("qualification stop request was not acknowledged")
    if (
        not isinstance(stop_observed, int)
        or isinstance(stop_observed, bool)
        or stop_observed <= 0
    ):
        raise PhaseError("qualification stop request was not observed")
    if (
        not isinstance(completed, int)
        or isinstance(completed, bool)
        or completed <= 0
    ):
        raise PhaseError("qualification completed_total must be positive")
    if (
        not isinstance(unexpected_errors, (int, float))
        or isinstance(unexpected_errors, bool)
        or unexpected_errors != 0
    ):
        raise PhaseError("qualification unexpected_errors must be zero")
    if metrics.get("outputs_written") is not True:
        raise PhaseError("qualification outputs were not written")
    return dict(metrics)


def persist_execution_evidence(
    ledger_path: Path,
    *,
    command: Mapping[str, Any],
    completed: subprocess.CompletedProcess[Any],
    started_monotonic_ns: int,
    ended_monotonic_ns: int,
    qualification_metrics: Mapping[str, Any] | None,
    qualification_error: str | None,
) -> dict[str, Any]:
    phase = str(command["phase"])
    execution_id = (
        f"{phase}-{time.time_ns()}-{os.getpid()}-"
        f"{str(command['command_spec_sha256'])[:12]}"
    )
    root = ledger_path.parent / f"{ledger_path.name}.execution_evidence"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    evidence_root = root / execution_id
    evidence_root.mkdir(mode=0o700)
    stdout_path = evidence_root / "stdout.log"
    stderr_path = evidence_root / "stderr.log"
    stdout = _output_bytes(completed.stdout)
    stderr = _output_bytes(completed.stderr)
    atomic_bytes(stdout_path, stdout)
    atomic_bytes(stderr_path, stderr)
    receipt = {
        "schema_version": "mysql_tpcc_phase_execution_evidence_v1",
        "status": (
            "PASS_TERMINAL_GATE"
            if qualification_error is None and completed.returncode == 0
            else "FAIL_TERMINAL_GATE_NO_LEDGER_CREDIT"
        ),
        "phase": phase,
        "bundle_id": command["bundle_id"],
        "attempt_id": command["attempt_id"],
        "host_effective_uid_gid": [0, 0],
        "command_spec_sha256": command["command_spec_sha256"],
        "returncode": completed.returncode,
        "started_monotonic_ns": started_monotonic_ns,
        "ended_monotonic_ns": ended_monotonic_ns,
        "elapsed_monotonic_ns": ended_monotonic_ns - started_monotonic_ns,
        "stdout_path": str(stdout_path),
        "stdout_sha256": sha256(stdout_path),
        "stderr_path": str(stderr_path),
        "stderr_sha256": sha256(stderr_path),
        "qualification_terminal_metrics": (
            dict(qualification_metrics) if qualification_metrics is not None else None
        ),
        "qualification_gate_error": qualification_error,
        "accepted_round_authorized": False,
    }
    receipt_path = evidence_root / "execution_receipt.json"
    atomic_json(receipt_path, receipt)
    return {
        **receipt,
        "execution_receipt_path": str(receipt_path),
        "execution_receipt_sha256": sha256(receipt_path),
    }


def write_stop_request(
    bundle: Path,
    service_path: Path,
    phase: str,
    seconds: int,
) -> dict[str, Any]:
    _require_host_root()
    service = load(service_path)
    validate_service(
        service, bundle.resolve(strict=True), str(service.get("attempt_id") or "")
    )
    if phase not in PHASES:
        raise PhaseError("stop request phase differs")
    if (
        not isinstance(seconds, int)
        or isinstance(seconds, bool)
        or seconds < 180
        or seconds > 600
        or seconds % 30 != 0
    ):
        raise PhaseError("stop request must be one of 180,210,...,600")
    bridge = Path(service["measurement_bridge"]["host_path"])
    bridge_stat = bridge.lstat()
    expected_uid = int(service["measurement_bridge"]["uid"])
    expected_gid = int(service["measurement_bridge"]["gid"])
    if (
        bridge.is_symlink()
        or not bridge.is_dir()
        or stat.S_IMODE(bridge_stat.st_mode) != 0o700
        or bridge_stat.st_uid != expected_uid
        or bridge_stat.st_gid != expected_gid
    ):
        raise PhaseError("measurement bridge identity differs for stop request")
    target = bridge / f"{phase}-stop"
    if target.exists() or target.is_symlink():
        raise PhaseError("stale stop request exists; refusing overwrite")
    payload = f"{seconds}\n".encode("ascii")
    temporary = bridge / (
        f".{phase}-stop.{service['attempt_id']}.{os.getpid()}.tmp"
    )
    descriptor = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        os.fchown(descriptor, expected_uid, expected_gid)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = None
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, target, follow_symlinks=False)
        temporary.unlink()
        directory_fd = os.open(bridge, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary.exists():
            temporary.unlink()
    return {
        "schema_version": "mysql_tpcc_atomic_stop_request_v1",
        "status": "PASS_ATOMIC_STOP_REQUEST_WRITTEN",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "bundle_id": service["bundle_id"],
        "attempt_id": service["attempt_id"],
        "host_effective_uid_gid": [0, 0],
        "phase": phase,
        "measurement_seconds": seconds,
        "payload_sha256": sha_bytes(payload),
        "host_path": str(target),
        "container_path": f"/measurement/{phase}-stop",
        "atomic_no_overwrite": True,
        "accepted_round_authorized": False,
    }


def _service_module(bundle: Path):
    path = bundle / "authority/mysql_tpcc_node04_adopted_service_v2.py"
    spec = importlib.util.spec_from_file_location("sealed_tpcc_adopted_service", path)
    if spec is None or spec.loader is None:
        raise PhaseError("sealed adopted-service module cannot be loaded")
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def validate_service(
    service: Mapping[str, Any], bundle: Path, token: str
) -> None:
    if not isinstance(token, str) or re.fullmatch(r"[A-Za-z0-9._-]{16,128}", token) is None:
        raise PhaseError("service attempt token differs")
    manifest = load(bundle / "bundle_manifest.json")
    if (
        service.get("schema_version")
        != "mysql_tpcc_node04_adopted_service_authority_v2"
        or service.get("status") != "PASS"
        or service.get("service_ready") is not True
        or service.get("formal_scope") != "mysql_only"
        or service.get("cross_engine_comparison_prohibited") is not True
        or service.get("bundle_id") != manifest.get("bundle_id")
        or service.get("attempt_id") != token
        or service.get("handoff_token_sha256")
        != sha_bytes(token.encode("utf-8"))
        or service.get("formal_launch_authorized") is not False
        or service.get("accepted_round_authorized") is not False
        or service.get("host") != "node04"
        or service.get("host_effective_uid_gid") != [0, 0]
        or service.get("same_live_database_required") is not True
        or service.get("container_recreated") is not False
        or service.get("database_restarted") is not False
        or service.get("datadir_replaced") is not False
    ):
        raise PhaseError("service authority identity/scope differs")
    database = service.get("database") or {}
    client = service.get("client") or {}
    runtimes = service.get("runtime_tree_readback") or {}
    if (
        SHA_RE.fullmatch(str(database.get("container_id") or "")) is None
        or int(database.get("pid") or 0) <= 1
        or int(database.get("process_start_ticks") or 0) <= 0
        or database.get("name") != f"ra_mysql_tpcc_v2_db_{token}"
        or database.get("device") != "/dev/nvme3n1"
        or database.get("device_major_minor") != "259:2"
        or database.get("cpuset_cpus") != "21-24,57-60"
        or database.get("cpuset_mems") != "1"
        or database.get("cpu_quota_cores") != 4.0
        or database.get("memory_bytes") != 8 * 1024**3
        or database.get("memory_swap_bytes") != 8 * 1024**3
        or database.get("pids_max") != 512
        or database.get("restart_count") != 0
        or database.get("mysql_port") != 65106
        or SHA_RE.fullmatch(str(client.get("container_id") or "")) is None
        or int(client.get("pid") or 0) <= 1
        or int(client.get("process_start_ticks") or 0) <= 0
        or client.get("name") != f"ra_mysql_tpcc_v2_client_{token}"
        or client.get("cpuset_cpus") != "8-15,44-51"
        or client.get("cpuset_mems") != "0"
        or client.get("cpu_quota_cores") != 16.0
        or client.get("memory_bytes") != 8 * 1024**3
        or client.get("memory_swap_bytes") != 8 * 1024**3
        or client.get("pids_max") != 1024
        or client.get("restart_count") != 0
        or client.get("configured_user") != "1008:1008"
        or client.get("root_filesystem_read_only") is not True
        or client.get("network_mode") != "host"
        or client.get("target_mysql_device_write_prohibited") is not True
        or (service.get("measurement_bridge") or {}).get("filesystem") != "tmpfs"
        or not re.fullmatch(
            r"sha256:[0-9a-f]{64}", str(database.get("image_id") or "")
        )
        or client.get("image_id") != database.get("image_id")
        or (runtimes.get("jdk") or {}).get("tree_content_sha256")
        != JDK_TREE_SHA256
        or (runtimes.get("python") or {}).get("tree_content_sha256")
        != PYTHON_TREE_SHA256
        or (runtimes.get("jdk") or {}).get("mounted_read_only") is not True
        or (runtimes.get("python") or {}).get("mounted_read_only") is not True
    ):
        raise PhaseError("service DB/client/tmpfs envelope differs")
    bridge = service.get("measurement_bridge") or {}
    expected_bridge_root = (
        "/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/"
        "runtime_algorithm_c_successor_20260729/" + token
    )
    if (
        bridge.get("schema_version")
        != "mysql_tpcc_measurement_bridge_authority_v1"
        or bridge.get("status") != "PASS"
        or bridge.get("formal_scope") != "mysql_only"
        or bridge.get("cross_engine_comparison_prohibited") is not True
        or bridge.get("bundle_id") != service.get("bundle_id")
        or bridge.get("attempt_id") != token
        or bridge.get("host_effective_uid_gid") != [0, 0]
        or bridge.get("host_path") != expected_bridge_root + "/measurement"
        or bridge.get("container_path") != "/measurement"
        or bridge.get("filesystem") != "tmpfs"
        or bridge.get("mount_target") != expected_bridge_root + "/measurement"
        or not isinstance(bridge.get("mount_source"), str)
        or not bridge.get("mount_source")
        or re.fullmatch(
            r"[0-9]+:[0-9]+", str(bridge.get("device_major_minor") or "")
        ) is None
        or bridge.get("device_major_minor") == "259:2"
        or bridge.get("size_bytes") != 2 * 1024**3
        or bridge.get("uid") != 1008
        or bridge.get("gid") != 1008
        or bridge.get("mode") != "0700"
        or bridge.get("nosuid") is not True
        or bridge.get("nodev") is not True
        or bridge.get("noexec") is not True
        or bridge.get("rootless_client_writable_by_owner_mode") is not True
        or bridge.get("nested_readonly_plugin_mount_required") is not True
        or bridge.get("authority_path")
        != expected_bridge_root + "/measurement_bridge_authority.json"
        or SHA_RE.fullmatch(str(bridge.get("authority_sha256") or "")) is None
        or bridge.get("authority_file_uid") != 1008
        or bridge.get("authority_file_gid") != 1008
        or bridge.get("authority_file_mode") != "0600"
        or bridge.get("live_readback_verified") is not True
        or bridge.get("formal_launch_authorized") is not False
        or bridge.get("accepted_round_authorized") is not False
    ):
        raise PhaseError("service measurement bridge authority differs")
    private_config = service.get("client_private_config") or {}
    private_files = private_config.get("files") or {}
    if (
        private_config.get("configured_user") != "1008:1008"
        or private_config.get("uid") != 1008
        or private_config.get("gid") != 1008
        or private_config.get("directory_mode") != "0700"
        or private_config.get("ownership_transactional_before_publish")
        is not True
        or private_config.get("host_effective_uid_gid") != [0, 0]
        or SHA_RE.fullmatch(
            str(
                private_config.get(
                    "credential_provisioning_authority_sha256"
                )
                or ""
            )
        )
        is None
        or private_config.get("authority_path")
        != expected_bridge_root + "/config_materialization.json"
        or SHA_RE.fullmatch(
            str(private_config.get("authority_sha256") or "")
        ) is None
        or private_config.get("authority_mode") != "0600"
        or not isinstance(private_files, Mapping)
        or set(private_files) != {"mysql.cnf", "tpcc.xml"}
        or any(
            not isinstance(private_files[name], Mapping)
            or SHA_RE.fullmatch(str(private_files[name].get("sha256") or ""))
            is None
            or int(private_files[name].get("bytes") or 0) <= 0
            or private_files[name].get("mode") != "0600"
            or private_files[name].get("uid") != 1008
            or private_files[name].get("gid") != 1008
            for name in ("mysql.cnf", "tpcc.xml")
        )
    ):
        raise PhaseError("service client private config authority differs")
    readability = service.get("client_readability_smoke") or {}
    readable_paths = readability.get("paths") or {}
    expected_readable_paths = {
        "jdk": "/opt/mysql-tpcc-jdk/bin/java",
        "bundle":
            "/opt/mysql-tpcc-bundle/predecessor/runtime/benchbase-formal.jar",
        "plugin": "/measurement/config/plugin.xml",
        "python": "/opt/mysql-tpcc-python/bin/python3.11",
        "tpcc_config": "/opt/mysql-tpcc-config/tpcc.xml",
        "mysql_defaults": "/opt/mysql-tpcc-config/mysql.cnf",
    }
    if (
        readability.get("schema_version")
        != "mysql_tpcc_client_readability_smoke_v1"
        or readability.get("status") != "PASS"
        or readability.get("configured_user") != "1008:1008"
        or readability.get("effective_uid") != 1008
        or readability.get("effective_gid") != 1008
        or readability.get("effective_capabilities_hex") != "0000000000000000"
        or readability.get("client_container_id") != client.get("container_id")
        or readability.get("client_pid") != client.get("pid")
        or readability.get("client_process_start_ticks")
        != client.get("process_start_ticks")
        or not isinstance(readable_paths, Mapping)
        or set(readable_paths) != set(expected_readable_paths)
        or any(
            not isinstance(readable_paths[name], Mapping)
            or readable_paths[name].get("path") != path
            or readable_paths[name].get("bytes_read") != 1
            for name, path in expected_readable_paths.items()
        )
        or readable_paths["plugin"].get("sha256")
        != "a770865560e0d1044c98a2954bacfd3d0c58ed3e956d3e783b2e830c7951d7e2"
        or readable_paths["tpcc_config"].get("sha256")
        != private_files["tpcc.xml"]["sha256"]
        or readable_paths["mysql_defaults"].get("sha256")
        != private_files["mysql.cnf"]["sha256"]
        or any(
            readable_paths[name].get("sha256") is not None
            for name in ("jdk", "bundle", "python")
        )
        or readability.get("measurement_write") != {
            "directory": "/measurement",
            "bytes": 27,
            "file_uid": 1008,
            "file_gid": 1008,
            "file_mode": "0600",
        }
    ):
        raise PhaseError("service client default-user readability differs")
    blkio = service.get("blkio_v1_throttle") or {}
    cgroup = "/sys/fs/cgroup/blkio/docker/" + database["container_id"]
    if (
        blkio.get("schema_version")
        != "mysql_tpcc_blkio_v1_throttle_authority_v1"
        or blkio.get("status") != "PASS"
        or blkio.get("formal_scope") != "mysql_only"
        or blkio.get("cross_engine_comparison_prohibited") is not True
        or blkio.get("bundle_id") != service.get("bundle_id")
        or blkio.get("attempt_id") != token
        or blkio.get("database_container") != database.get("name")
        or blkio.get("database_container_id") != database.get("container_id")
        or blkio.get("database_pid") != database.get("pid")
        or blkio.get("host_effective_uid_gid") != [0, 0]
        or blkio.get("cgroup_version") != "v1"
        or blkio.get("cgroup_path") != cgroup
        or blkio.get("device_major_minor") != "259:2"
        or blkio.get("read_bps") != 1_205_862_400
        or blkio.get("write_bps") != 1_205_862_400
        or blkio.get("read_throttle_file")
        != cgroup + "/blkio.throttle.read_bps_device"
        or blkio.get("write_throttle_file")
        != cgroup + "/blkio.throttle.write_bps_device"
        or blkio.get("docker_hostconfig_readback_not_authoritative") is not True
        or blkio.get("database_or_client_started") is not True
        or blkio.get("workload_started") is not False
        or blkio.get("formal_launch_authorized") is not False
        or blkio.get("accepted_round_authorized") is not False
    ):
        raise PhaseError("service cgroup-v1 blkio authority differs")
    plugin = service.get("benchbase_plugin_mount") or {}
    expected_plugin_source = (
        "/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/"
        "runtime_algorithm_c_successor_20260729/bundles/"
        + str(service.get("bundle_id"))
        + "/benchbase_runtime/config"
    )
    if plugin != {
        "source": expected_plugin_source,
        "destination": "/measurement/config",
        "plugin_relative": "plugin.xml",
        "plugin_sha256":
            "a770865560e0d1044c98a2954bacfd3d0c58ed3e956d3e783b2e830c7951d7e2",
        "read_only": True,
    }:
        raise PhaseError("service production BenchBase plugin mount differs")
    unsigned = dict(service)
    live_digest = unsigned.pop("live_readback_sha256", None)
    if (
        SHA_RE.fullmatch(str(live_digest or "")) is None
        or live_digest != sha_bytes(canonical(unsigned))
    ):
        raise PhaseError("service live readback identity differs")


def validate_prefault(
    prefault: Mapping[str, Any], service: Mapping[str, Any]
) -> None:
    client = service["client"]
    if (
        prefault.get("schema_version")
        != "mysql_tpcc_client_target_device_prefault_v1"
        or prefault.get("status") != "PASS_PREFAULT_ZERO_IO_BASELINE"
        or prefault.get("formal_scope") != "mysql_only"
        or prefault.get("cross_engine_comparison_prohibited") is not True
        or prefault.get("bundle_id") != service.get("bundle_id")
        or prefault.get("attempt_id") != service.get("attempt_id")
        or prefault.get("handoff_token_sha256")
        != service.get("handoff_token_sha256")
        or prefault.get("client_container_id") != client.get("container_id")
        or prefault.get("client_pid") != client.get("pid")
        or prefault.get("client_process_start_ticks")
        != client.get("process_start_ticks")
        or prefault.get("target_device_major_minor") != "259:2"
        or prefault.get("before_prefault")
        != {
            "rbytes": 0, "wbytes": 0, "rios": 0,
            "wios": 0, "dbytes": 0, "dios": 0,
        }
        or prefault.get("after_prefault")
        != {
            "rbytes": 0, "wbytes": 0, "rios": 0,
            "wios": 0, "dbytes": 0, "dios": 0,
        }
        or prefault.get("real_window_baseline")
        != {
            "rbytes": 0, "wbytes": 0, "rios": 0,
            "wios": 0, "dbytes": 0, "dios": 0,
        }
        or prefault.get("quiet_window_seconds") != 2
        or prefault.get("quiet_window_delta")
        != {
            "rbytes": 0, "wbytes": 0, "rios": 0,
            "wios": 0, "dbytes": 0, "dios": 0,
        }
        or prefault.get("real_window_all_target_device_io_must_be_zero")
        is not True
        or prefault.get("v2_same_live_adoption_verified") is not True
        or prefault.get("formal_launch_authorized") is not False
        or prefault.get("accepted_round_authorized") is not False
    ):
        raise PhaseError("client target-device prefault authority differs")
    destinations = prefault.get("prefaulted_target_mount_destinations")
    plugin = service.get("benchbase_plugin_mount") or {}
    plugin_destination = plugin.get("destination")
    allowed_destinations = {
        "/opt/mysql-tpcc-bundle", "/opt/mysql-tpcc-jdk",
        "/opt/mysql-tpcc-python", "/opt/mysql-tpcc-config",
        "/measurement/config",
    }
    if (
        plugin_destination != "/measurement/config"
        or plugin.get("read_only") is not True
        or not isinstance(destinations, list)
        or destinations != sorted(set(destinations))
        or any(value not in allowed_destinations for value in destinations)
        or plugin_destination not in destinations
    ):
        raise PhaseError("client target-device prefault destinations differ")
    snapshots = prefault.get("client_cgroup_io_snapshot_evidence")
    if not isinstance(snapshots, Mapping) or set(snapshots) != {
        "before_prefault", "after_prefault", "real_window_baseline",
    }:
        raise PhaseError("client target-device prefault snapshots differ")
    expected_counters = {
        "rbytes": 0, "wbytes": 0, "rios": 0,
        "wios": 0, "dbytes": 0, "dios": 0,
    }
    expected_sync_async = {
        "sync_bytes": 0, "async_bytes": 0,
        "sync_ios": 0, "async_ios": 0,
    }
    root = "/sys/fs/cgroup/blkio/docker/" + client["container_id"]
    expected_authority = {
        "cgroup_version": 1,
        "counter_family": "throttle",
        "target_device_major_minor": "259:2",
        "counter_files": [
            root + "/blkio.throttle.io_service_bytes",
            root + "/blkio.throttle.io_serviced",
        ],
    }
    for name in ("before_prefault", "after_prefault", "real_window_baseline"):
        snapshot = snapshots[name]
        if (
            not isinstance(snapshot, Mapping)
            or snapshot.get("counters") != expected_counters
            or snapshot.get("sync_async") != expected_sync_async
            or snapshot.get("absence_as_zero") is not True
            or snapshot.get("authority") != expected_authority
        ):
            raise PhaseError("client target-device prefault snapshot identity differs")


def _replace(value: Any, replacements: Mapping[str, str]) -> Any:
    if isinstance(value, str):
        for before, after in replacements.items():
            value = value.replace(before, after)
        return value
    if isinstance(value, list):
        return [_replace(item, replacements) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _replace(item, replacements) for key, item in value.items()}
    return value


def _audit_production_classpath(
    bundle: Path,
    manifest: Mapping[str, Any],
    materialized: Mapping[str, Any],
    java_argv: Sequence[Any],
) -> dict[str, Any]:
    recipe = materialized.get("native_java_launch_recipe")
    entries = (recipe or {}).get("classpath_relative_entries")
    if (
        not isinstance(recipe, Mapping)
        or recipe.get("main_class") != "com.oltpbenchmark.DBWorkload"
        or recipe.get("java_jar_mode_prohibited") is not True
        or recipe.get("all_classpath_regular_files_must_be_hash_bound") is not True
        or not isinstance(entries, list)
        or not entries
        or any(not isinstance(value, str) for value in entries)
        or entries[0] != "qualification/native_latency_producer/classes"
        or "predecessor/runtime/benchbase-formal.jar" not in entries
    ):
        raise PhaseError("production BenchBase classpath recipe differs")
    if (
        len(java_argv) < 4
        or java_argv[0] != JAVA
        or java_argv[1] != "-cp"
        or java_argv[3] != "com.oltpbenchmark.DBWorkload"
    ):
        raise PhaseError("production BenchBase Java argv differs")
    expected_classpath = ":".join(
        "/opt/mysql-tpcc-bundle/" + relative for relative in entries
    )
    if java_argv[2] != expected_classpath:
        raise PhaseError("production BenchBase materialized classpath differs")
    files = manifest.get("files")
    if not isinstance(files, Mapping):
        raise PhaseError("bundle file closure is absent")
    if stat.S_IMODE(bundle.stat().st_mode) != 0o555:
        raise PhaseError("container bundle root is not read-only traversable")
    audited_files: list[dict[str, Any]] = []
    for relative in entries:
        path = bundle / relative
        if path.is_symlink():
            raise PhaseError("classpath symlink is prohibited")
        if path.is_dir():
            descendants = sorted(
                item for item in path.rglob("*") if item.is_file()
            )
            if not descendants:
                raise PhaseError("classpath directory is empty")
            for item in descendants:
                item_relative = item.relative_to(bundle).as_posix()
                if (
                    item.is_symlink()
                    or files.get(item_relative) != sha256(item)
                    or not stat.S_IMODE(item.stat().st_mode) & 0o004
                ):
                    raise PhaseError("classpath directory file closure/access differs")
                audited_files.append({
                    "relative": item_relative, "sha256": files[item_relative],
                })
        elif (
            not path.is_file()
            or files.get(relative) != sha256(path)
            or not stat.S_IMODE(path.stat().st_mode) & 0o004
        ):
            raise PhaseError("classpath regular-file closure/access differs")
        else:
            audited_files.append({"relative": relative, "sha256": files[relative]})
        for parent in (path, *path.parents):
            if parent == bundle.parent:
                break
            if parent.is_dir() and not stat.S_IMODE(parent.stat().st_mode) & 0o001:
                raise PhaseError("classpath directory is not container-traversable")
    jar = bundle / "predecessor/runtime/benchbase-formal.jar"
    plugin_relative = "benchbase_runtime/config/plugin.xml"
    plugin = bundle / plugin_relative
    plugin_sha256 = "a770865560e0d1044c98a2954bacfd3d0c58ed3e956d3e783b2e830c7951d7e2"
    if (
        plugin.is_symlink()
        or not plugin.is_file()
        or files.get(plugin_relative) != plugin_sha256
        or sha256(plugin) != plugin_sha256
        or not stat.S_IMODE(plugin.stat().st_mode) & 0o004
    ):
        raise PhaseError("production BenchBase plugin closure/access differs")
    required_classes = {
        "com/oltpbenchmark/DBWorkload.class",
        "com/oltpbenchmark/benchmarks/tpcc/TPCCBenchmark.class",
        "com/oltpbenchmark/benchmarks/tpcc/TPCCWorker.class",
    }
    try:
        with zipfile.ZipFile(jar) as archive:
            names = set(archive.namelist())
            if not required_classes.issubset(names):
                raise PhaseError("production BenchBase JAR class coverage differs")
            if archive.testzip() is not None:
                raise PhaseError("production BenchBase JAR CRC differs")
    except (OSError, zipfile.BadZipFile) as exc:
        raise PhaseError("production BenchBase JAR cannot be audited") from exc
    return {
        "schema_version": "mysql_tpcc_production_benchbase_classpath_v1",
        "bundle_mount": "/opt/mysql-tpcc-bundle",
        "main_class": "com.oltpbenchmark.DBWorkload",
        "benchbase_jar_relative": "predecessor/runtime/benchbase-formal.jar",
        "benchbase_jar_sha256": files["predecessor/runtime/benchbase-formal.jar"],
        "plugin_relative": plugin_relative,
        "plugin_sha256": plugin_sha256,
        "container_plugin_path": "/measurement/config/plugin.xml",
        "required_classes": sorted(required_classes),
        "classpath_relative_entries": list(entries),
        "audited_regular_file_count": len(audited_files),
        "all_regular_files_manifest_bound": True,
        "container_readable_and_traversable": True,
    }


def _phase_authorized(
    phase: str,
    authority: Mapping[str, Any] | None,
    service: Mapping[str, Any],
) -> bool:
    if phase in {"qualification", "typed_capture"}:
        return True
    expected = {
        "fresh_r0": "mysql_tpcc_fresh_r0_authority_v1",
        "debug": "mysql_tpcc_debug_authority_v1",
        "formal10_launch": "mysql_tpcc_formal10_launch_authority_v1",
    }[phase]
    return bool(
        authority
        and authority.get("schema_version") == expected
        and authority.get("status") == "PASS"
        and authority.get("phase") == phase
        and authority.get("formal_scope") == "mysql_only"
        and authority.get("bundle_id") == service.get("bundle_id")
        and authority.get("attempt_id") == service.get("attempt_id")
        and authority.get("handoff_token_sha256")
        == service.get("handoff_token_sha256")
        and authority.get("db_container_id")
        == (service.get("database") or {}).get("container_id")
        and authority.get("db_pid")
        == (service.get("database") or {}).get("pid")
        and authority.get("db_process_start_ticks")
        == (service.get("database") or {}).get("process_start_ticks")
        and authority.get("accepted_round_authorized") is False
    )


def materialize(
    *,
    bundle: Path,
    service: Mapping[str, Any],
    token: str,
    phase: str,
    config_in_container: str,
    mysql_defaults_in_container: str,
    phase_authority: Mapping[str, Any] | None,
    readiness_audit: Mapping[str, Any] | None,
    prefault_authority: Mapping[str, Any],
) -> dict[str, Any]:
    _require_host_root()
    bundle = bundle.resolve(strict=True)
    validate_service(service, bundle, token)
    validate_prefault(prefault_authority, service)
    if phase not in PHASES:
        raise PhaseError(f"unknown phase: {phase}")
    if not config_in_container.startswith("/opt/mysql-tpcc-config/"):
        raise PhaseError("config must come from the sealed read-only config mount")
    if not mysql_defaults_in_container.startswith("/opt/mysql-tpcc-config/"):
        raise PhaseError("MySQL credentials must be referenced by defaults file")
    if not _phase_authorized(phase, phase_authority, service):
        raise PhaseError(f"{phase} lacks its sealed phase authority")
    template_name = (
        "typed_capture_command_template_v2.json"
        if phase == "typed_capture"
        else "qualification_command_template.json"
    )
    template = load(bundle / "qualification" / template_name)
    replacements = {
        "${JAVA_BINARY}": JAVA,
        "${PYTHON_BINARY}": PYTHON,
        "${BUNDLE_ROOT}": "/opt/mysql-tpcc-bundle",
        "${CONFIG_ABSOLUTE}": config_in_container,
        "${MYSQL_DEFAULTS_FILE}": mysql_defaults_in_container,
        "${MEASUREMENT_TMPFS}": "/measurement",
        "${QUALIFICATION_ATTEMPT_ID}": token,
        "${MYSQL_JDBC_URL}": "jdbc:mysql://127.0.0.1:65106/tpcc",
    }
    materialized = _replace(template, replacements)
    java_argv = materialized.get(
        "actual_java_argv",
        (materialized.get("native_java_launch_recipe") or {}).get(
            "materialized_argv"
        ),
    )
    if not isinstance(java_argv, list):
        raise PhaseError("materialized native DBWorkload argv is absent")
    classpath_audit = _audit_production_classpath(
        bundle, load(bundle / "bundle_manifest.json"), materialized, java_argv,
    )
    workload_argv = materialized["argv"]
    client = service["client"]
    environment = dict(materialized.get("environment") or {})
    environment.update({
        "BENCHBASE_FORMAL_WARMUP_SECONDS": "60",
        "BENCHBASE_FORMAL_MAXIMUM_MEASUREMENT_SECONDS": "600",
        "BENCHBASE_FORMAL_STOP_FILE": f"/measurement/{phase}-stop",
        "MYSQL_TPCC_PHASE": phase,
        "MYSQL_TPCC_ATTEMPT_ID": token,
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    docker_exec = ["docker", "exec"]
    for key in sorted(environment):
        docker_exec += ["--env", f"{key}={environment[key]}"]
    docker_exec += [
        "--workdir", "/measurement",
        client["name"],
        *workload_argv,
    ]
    status = {
        "qualification": "SEALED_QUALIFICATION_COMMAND",
        "typed_capture": "SEALED_CAPTURE_COMMAND",
        "fresh_r0": "SEALED_R0_COMMAND",
        "debug": "SEALED_DEBUG_COMMAND",
        "formal10_launch": "SEALED_FORMAL10_COMMAND",
    }[phase]
    value = {
        "schema_version": (
            "mysql_tpcc_formal10_materialized_command_v1"
            if phase == "formal10_launch"
            else "mysql_tpcc_materialized_phase_command_v1"
        ),
        "status": status,
        "phase": phase,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "bundle_id": service["bundle_id"],
        "attempt_id": token,
        "handoff_token_sha256": service["handoff_token_sha256"],
        "host_effective_uid_gid": [0, 0],
        "db_identity": {
            key: service["database"][key]
            for key in (
                "container_id", "pid", "process_start_ticks", "datadir",
                "device_major_minor", "cpuset_cpus", "cpuset_mems", "mysql_port",
            )
        },
        "client_container": dict(client),
        "native_java_launch_recipe": materialized["native_java_launch_recipe"],
        "actual_java_argv": java_argv,
        "argv": workload_argv,
        "docker_exec_argv": docker_exec,
        "environment": environment,
        "graceful_stop_request": {
            "container_path": environment["BENCHBASE_FORMAL_STOP_FILE"],
            "host_path": str(
                Path(service["measurement_bridge"]["host_path"])
                / f"{phase}-stop"
            ),
            "payload_utf8": "180\n",
            "measurement_seconds": 180,
            "valid_seconds": list(range(180, 601, 30)),
            "atomic_create_required": True,
            "empty_file_prohibited": True,
            "touch_command_prohibited": True,
            "stale_file_must_be_absent_before_start": True,
            "sealed_writer_action": (
                "mysql_tpcc_materialize_phase_command_v2.py write-stop "
                f"--phase {phase} --seconds 180"
            ),
        },
        "config_in_container": config_in_container,
        "mysql_defaults_in_container": mysql_defaults_in_container,
        "actual_main_class": "com.oltpbenchmark.DBWorkload",
        "classpath_overlay_first": True,
        "production_benchbase_classpath": classpath_audit,
        "same_live_database_required": True,
        "manifest_bound_java_binary": JAVA,
        "manifest_bound_python_binary": PYTHON,
        "jdk_tree_content_sha256": JDK_TREE_SHA256,
        "python_tree_content_sha256": PYTHON_TREE_SHA256,
        "prefault_authority_sha256":
            sha_bytes(canonical(prefault_authority)),
        "target_device_real_window_io_required_zero": True,
        "capture_only": phase == "typed_capture",
        "formal_r0_authorized": phase == "fresh_r0",
        "formal_authorized": phase == "formal10_launch",
        "accepted_round_authorized": False,
        "qps_resource_input_prohibited": True,
        "readiness_audit_required_for_execute":
            phase == "formal10_launch",
    }
    if PLACEHOLDER_RE.search(json.dumps(value, sort_keys=True)):
        raise PhaseError("materialized command retains a placeholder")
    reject_qps(value)
    value["command_spec_sha256"] = sha_bytes(canonical(value))
    return value


def reject_qps(value: Any, location: str = "command") -> None:
    forbidden = {"qps", "throughput", "transactions_per_second"}
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).lower() in forbidden:
                raise PhaseError(f"QPS resource input prohibited: {location}.{key}")
            reject_qps(item, f"{location}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            reject_qps(item, f"{location}[{index}]")


def validate_materialized_command(
    command: Mapping[str, Any],
    service: Mapping[str, Any],
    prefault: Mapping[str, Any],
) -> None:
    phase = command.get("phase")
    if phase not in PHASES:
        raise PhaseError("materialized command phase differs")
    expected_schema = (
        "mysql_tpcc_formal10_materialized_command_v1"
        if phase == "formal10_launch"
        else "mysql_tpcc_materialized_phase_command_v1"
    )
    expected_status = {
        "qualification": "SEALED_QUALIFICATION_COMMAND",
        "typed_capture": "SEALED_CAPTURE_COMMAND",
        "fresh_r0": "SEALED_R0_COMMAND",
        "debug": "SEALED_DEBUG_COMMAND",
        "formal10_launch": "SEALED_FORMAL10_COMMAND",
    }[phase]
    unsigned = dict(command)
    digest = unsigned.pop("command_spec_sha256", None)
    argv = command.get("argv")
    environment = command.get("environment")
    if (
        command.get("schema_version") != expected_schema
        or command.get("status") != expected_status
        or command.get("formal_scope") != "mysql_only"
        or command.get("cross_engine_comparison_prohibited") is not True
        or command.get("bundle_id") != service.get("bundle_id")
        or command.get("attempt_id") != service.get("attempt_id")
        or command.get("handoff_token_sha256")
        != service.get("handoff_token_sha256")
        or command.get("host_effective_uid_gid") != [0, 0]
        or command.get("db_identity") != {
            key: service["database"][key]
            for key in (
                "container_id", "pid", "process_start_ticks", "datadir",
                "device_major_minor", "cpuset_cpus", "cpuset_mems", "mysql_port",
            )
        }
        or command.get("client_container") != service.get("client")
        or command.get("prefault_authority_sha256")
        != sha_bytes(canonical(prefault))
        or command.get("same_live_database_required") is not True
        or command.get("target_device_real_window_io_required_zero") is not True
        or command.get("capture_only") is not (phase == "typed_capture")
        or command.get("formal_r0_authorized") is not (phase == "fresh_r0")
        or command.get("formal_authorized") is not (phase == "formal10_launch")
        or command.get("accepted_round_authorized") is not False
        or command.get("qps_resource_input_prohibited") is not True
        or command.get("readiness_audit_required_for_execute")
        is not (phase == "formal10_launch")
        or command.get("graceful_stop_request") != {
            "container_path": environment.get("BENCHBASE_FORMAL_STOP_FILE")
            if isinstance(environment, Mapping)
            else None,
            "host_path": str(
                Path(service["measurement_bridge"]["host_path"])
                / f"{phase}-stop"
            ),
            "payload_utf8": "180\n",
            "measurement_seconds": 180,
            "valid_seconds": list(range(180, 601, 30)),
            "atomic_create_required": True,
            "empty_file_prohibited": True,
            "touch_command_prohibited": True,
            "stale_file_must_be_absent_before_start": True,
            "sealed_writer_action": (
                "mysql_tpcc_materialize_phase_command_v2.py write-stop "
                f"--phase {phase} --seconds 180"
            ),
        }
        or SHA_RE.fullmatch(str(digest or "")) is None
        or digest != sha_bytes(canonical(unsigned))
    ):
        raise PhaseError("materialized command identity/authority differs")
    if not isinstance(argv, list) or not isinstance(environment, Mapping):
        raise PhaseError("materialized command argv/environment differs")
    expected_exec = ["docker", "exec"]
    for key in sorted(environment):
        expected_exec += ["--env", f"{key}={environment[key]}"]
    expected_exec += [
        "--workdir", "/measurement", service["client"]["name"], *argv,
    ]
    if command.get("docker_exec_argv") != expected_exec:
        raise PhaseError("materialized Docker executor argv differs")
    reject_qps(command)


def _same_database(before: Mapping[str, Any], after: Mapping[str, Any]) -> None:
    fields = (
        "container_id", "pid", "process_start_ticks", "datadir",
        "device_major_minor", "cpuset_cpus", "cpuset_mems", "mysql_port",
    )
    if any(before["database"].get(key) != after["database"].get(key) for key in fields):
        raise PhaseError("same-live database identity changed during phase")
    if before.get("handoff_token_sha256") != after.get("handoff_token_sha256"):
        raise PhaseError("handoff token changed during phase")
    if (
        before.get("bundle_id") != after.get("bundle_id")
        or before.get("attempt_id") != after.get("attempt_id")
    ):
        raise PhaseError("bundle/attempt identity changed during phase")
    client_fields = ("container_id", "pid", "process_start_ticks")
    if any(
        before["client"].get(key) != after["client"].get(key)
        for key in client_fields
    ):
        raise PhaseError("same-live client identity changed during phase")


def _ordered_phase_names(phases: Mapping[str, Any]) -> list[str]:
    """Return phase names by durable event time, never JSON key order.

    Atomic JSON uses sort_keys=True, so object insertion order is destroyed as
    soon as a ledger is read back.  ``observed_epoch_ms`` is the sealed order
    authority and must be strictly unique.
    """
    rows: list[tuple[int, str]] = []
    for name, value in phases.items():
        if name not in PHASES or not isinstance(value, Mapping):
            raise PhaseError("ledger phase name/payload differs")
        observed = value.get("observed_epoch_ms")
        if not isinstance(observed, int) or observed <= 0:
            raise PhaseError("ledger phase timestamp differs")
        rows.append((observed, name))
    rows.sort()
    if len({epoch for epoch, _name in rows}) != len(rows):
        raise PhaseError("ledger phase timestamps are not unique")
    return [name for _epoch, name in rows]


def record_phase(
    ledger_path: Path,
    *,
    command: Mapping[str, Any],
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    returncode: int,
    target_device_io_delta: Mapping[str, int],
    execution_evidence: Mapping[str, Any] | None = None,
    qualification_metrics: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    phase = str(command["phase"])
    _same_database(before, after)
    existing = load(ledger_path) if ledger_path.exists() else None
    if existing is None:
        phases: dict[str, Any] = {}
        ledger = {
            "schema_version": LEDGER_SCHEMA,
            "status": "INCOMPLETE",
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "attempt_id": command["attempt_id"],
            "bundle_id": command["bundle_id"],
            "db_container_id": before["database"]["container_id"],
            "db_pid": before["database"]["pid"],
            "db_process_start_ticks": before["database"]["process_start_ticks"],
            "db_datadir": before["database"]["datadir"],
            "container_recreated": False,
            "datadir_replaced": False,
            "database_restarted": False,
            "phases": phases,
            "accepted_round_authorized": False,
        }
    else:
        ledger = dict(existing)
        if ledger.get("schema_version") != LEDGER_SCHEMA:
            raise PhaseError("ledger schema differs; v1 key-order ledger is not resumable")
        phases = dict(ledger.get("phases") or {})
    ordered_before = _ordered_phase_names(phases)
    expected_index = len(ordered_before)
    if (ordered_before != list(PHASES[:expected_index]) or
            expected_index >= len(PHASES) or PHASES[expected_index] != phase):
        raise PhaseError("phase order/replay differs; refusing ledger mutation")
    if returncode != 0:
        raise PhaseError("failed phase cannot enter PASS handoff ledger")
    if phase == "qualification":
        if (
            not isinstance(execution_evidence, Mapping)
            or execution_evidence.get("schema_version")
            != "mysql_tpcc_phase_execution_evidence_v1"
            or execution_evidence.get("status") != "PASS_TERMINAL_GATE"
            or execution_evidence.get("phase") != phase
            or execution_evidence.get("bundle_id") != command["bundle_id"]
            or execution_evidence.get("attempt_id") != command["attempt_id"]
            or execution_evidence.get("command_spec_sha256")
            != command["command_spec_sha256"]
            or execution_evidence.get("returncode") != 0
            or execution_evidence.get("qualification_gate_error") is not None
            or dict(execution_evidence.get("qualification_terminal_metrics") or {})
            != dict(qualification_metrics or {})
            or SHA_RE.fullmatch(
                str(execution_evidence.get("execution_receipt_sha256") or "")
            )
            is None
        ):
            raise PhaseError(
                "qualification terminal execution evidence is not PASS"
            )
        qualification_metrics = qualification_terminal_metrics(
            BENCHBASE_METRICS_PREFIX
            + json.dumps(qualification_metrics, separators=(",", ":")),
            "",
        )
    if (
        set(target_device_io_delta)
        != {"rbytes", "wbytes", "rios", "wios", "dbytes", "dios"}
        or any(int(value) != 0 for value in target_device_io_delta.values())
    ):
        raise PhaseError(
            "client target-device IO was nonzero in the real phase window"
        )
    db = after["database"]
    client = after["client"]
    previous_epochs = [
        value.get("observed_epoch_ms")
        for value in phases.values()
        if isinstance(value, Mapping)
        and isinstance(value.get("observed_epoch_ms"), int)
    ]
    observed_epoch_ms = max(time.time_ns() // 1_000_000,
                            max(previous_epochs, default=0) + 1)
    phases[phase] = {
        "status": "PASS",
        "attempt_id": command["attempt_id"],
        "bundle_id": command["bundle_id"],
        "db_container_id": db["container_id"],
        "db_pid": db["pid"],
        "db_process_start_ticks": db["process_start_ticks"],
        "db_datadir": db["datadir"],
        "db_device_major_minor": db["device_major_minor"],
        "db_cpuset_cpus": db["cpuset_cpus"],
        "db_cpuset_mems": db["cpuset_mems"],
        "mysql_port": db["mysql_port"],
        "client_container_id": client["container_id"],
        "client_pid": client["pid"],
        "client_cpuset_cpus": client["cpuset_cpus"],
        "client_cpuset_mems": client["cpuset_mems"],
        "live_readback_sha256": after["live_readback_sha256"],
        "command_spec_sha256": command["command_spec_sha256"],
        "host_effective_uid_gid": [0, 0],
        "observed_epoch_ms": observed_epoch_ms,
        "accepted_round_authorized": False,
        "client_target_device_io_delta":
            dict(target_device_io_delta),
        "client_target_device_io_zero_verified": True,
        "execution_evidence": (
            dict(execution_evidence) if execution_evidence is not None else None
        ),
        "benchbase_formal_metrics": (
            dict(qualification_metrics)
            if phase == "qualification" and qualification_metrics is not None
            else None
        ),
    }
    ledger["phases"] = phases
    ordered_after = _ordered_phase_names(phases)
    if ordered_after == list(PHASES):
        ledger["status"] = "PASS"
    elif ordered_after == list(PHASES[:-1]):
        ledger["status"] = "PRELAUNCH_READY"
    else:
        ledger["status"] = "INCOMPLETE"
    return ledger


def execute(
    bundle: Path,
    plan_path: Path,
    service_path: Path,
    command_path: Path,
    ledger_path: Path,
    readiness_audit_path: Path | None,
    prefault_authority_path: Path,
) -> int:
    _require_host_root()
    command = load(command_path)
    service = load(service_path)
    prefault = load(prefault_authority_path)
    validate_service(service, bundle.resolve(strict=True), command.get("attempt_id"))
    validate_prefault(prefault, service)
    validate_materialized_command(command, service, prefault)
    if command["phase"] == "formal10_launch":
        if readiness_audit_path is None:
            raise PhaseError("formal10 execution requires --readiness-audit")
        readiness = load(readiness_audit_path)
        if not (
            readiness.get("schema_version")
            == "mysql_tpcc_v3_formal10_readiness_audit_v1"
            and readiness.get("status") == "READY"
            and readiness.get("bundle_id") == command["bundle_id"]
            and readiness.get("blockers") == []
        ):
            raise PhaseError("formal10 execution readiness audit differs")
    service_module = _service_module(bundle)
    before = service_module.adopt(load(plan_path))
    validate_service(before, bundle, command["attempt_id"])
    _same_database(service, before)
    io_before = service_module._target_io_snapshot(
        before["client"]["container_id"], before["client"]["pid"]
    )
    started_monotonic_ns = time.monotonic_ns()
    completed = subprocess.run(
        command["docker_exec_argv"], check=False, capture_output=True,
    )
    ended_monotonic_ns = time.monotonic_ns()
    io_after = service_module._target_io_snapshot(
        before["client"]["container_id"], before["client"]["pid"]
    )
    io_delta = service_module._io_delta(io_before, io_after)
    after = service_module.adopt(load(plan_path))
    qualification_metrics = None
    qualification_error = None
    if command["phase"] == "qualification":
        try:
            qualification_metrics = qualification_terminal_metrics(
                completed.stdout, completed.stderr
            )
        except (PhaseError, UnicodeDecodeError) as error:
            qualification_error = str(error)
    execution_evidence = persist_execution_evidence(
        ledger_path,
        command=command,
        completed=completed,
        started_monotonic_ns=started_monotonic_ns,
        ended_monotonic_ns=ended_monotonic_ns,
        qualification_metrics=qualification_metrics,
        qualification_error=qualification_error,
    )
    if qualification_error is not None:
        raise PhaseError(qualification_error)
    ledger = record_phase(
        ledger_path, command=command, before=before, after=after,
        returncode=completed.returncode,
        target_device_io_delta=io_delta,
        execution_evidence=execution_evidence,
        qualification_metrics=qualification_metrics,
    )
    atomic_json(service_path, after)
    atomic_json(ledger_path, ledger)
    return completed.returncode


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("materialize", "execute", "write-stop"))
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--service", type=Path, required=True)
    parser.add_argument("--token-file", type=Path)
    parser.add_argument("--phase", choices=PHASES)
    parser.add_argument("--config-in-container")
    parser.add_argument("--mysql-defaults-in-container")
    parser.add_argument("--phase-authority", type=Path)
    parser.add_argument("--readiness-audit", type=Path)
    parser.add_argument("--prefault-authority", type=Path)
    parser.add_argument("--seconds", type=int)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--command", type=Path)
    parser.add_argument("--ledger", type=Path)
    args = parser.parse_args(argv)
    if args.action == "write-stop":
        if args.phase is None or args.seconds is None:
            raise PhaseError("write-stop lacks --phase/--seconds")
        print(json.dumps(
            write_stop_request(args.bundle, args.service, args.phase, args.seconds),
            sort_keys=True,
        ))
        return 0
    if args.action == "materialize":
        if not all((
            args.token_file, args.phase, args.config_in_container,
            args.mysql_defaults_in_container, args.output,
            args.prefault_authority,
        )):
            raise PhaseError("materialize lacks required arguments")
        token = args.token_file.read_text(encoding="utf-8").strip()
        value = materialize(
            bundle=args.bundle,
            service=load(args.service),
            token=token,
            phase=args.phase,
            config_in_container=args.config_in_container,
            mysql_defaults_in_container=args.mysql_defaults_in_container,
            phase_authority=(
                load(args.phase_authority) if args.phase_authority else None
            ),
            readiness_audit=(
                load(args.readiness_audit) if args.readiness_audit else None
            ),
            prefault_authority=load(args.prefault_authority),
        )
        atomic_json(args.output, value)
        print(json.dumps({"status": "PASS", "output": str(args.output)}, sort_keys=True))
        return 0
    if not all((args.plan, args.command, args.ledger, args.prefault_authority)):
        raise PhaseError("execute lacks --plan/--command/--ledger/--prefault-authority")
    return execute(
        args.bundle, args.plan, args.service, args.command, args.ledger,
        args.readiness_audit, args.prefault_authority,
    )


if __name__ == "__main__":
    raise SystemExit(main())
