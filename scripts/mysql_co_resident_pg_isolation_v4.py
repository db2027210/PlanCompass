#!/usr/bin/env python3
"""Install the v56 read-only co-resident PostgreSQL isolation policy.

The v55 policy only recognized the frozen ``/disk1/resource_adaptive``
campaign.  The separately owned BenchBase mixstrg campaign now runs on CPU
sets and a storage device disjoint from the MySQL formal envelope.  This
successor admits only explicitly sealed Docker profiles and proves process
membership through the live Docker cgroup.  It never changes a process,
container, database, affinity, or file.
"""

from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor, as_completed
import http.client
import json
from pathlib import Path
import shlex
import socket
import time
from typing import Any, Mapping
from urllib.parse import quote

from scripts import mysql_co_resident_pg_isolation_v2 as predecessor
from scripts import mysql_cross_project_wave_coordination_v1 as coordination


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = (
    ROOT
    / "config/formal/frozen_mysql_cross_project_wave_coordination_20260817_v56"
    / "coordination_contract.json"
)
ORIGINAL_EVALUATE = coordination.evaluate_co_resident_pg_isolation
ORIGINAL_COMMAND_KIND = coordination.command_kind
ADDITIONAL_SERVER_NAMES = frozenset({
    "bench-pg-mixstrg-0817",
    "bench-pg-row",
})


class _DockerUnixConnection(http.client.HTTPConnection):
    def __init__(self, timeout: float = 5.0) -> None:
        super().__init__("localhost", timeout=timeout)

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect("/var/run/docker.sock")


def _docker_document(name: str) -> tuple[dict[str, Any] | None, str]:
    """Read one inspect document through Docker's read-only HTTP GET API."""

    connection = _DockerUnixConnection(timeout=5.0)
    try:
        connection.request("GET", f"/containers/{quote(name, safe='')}/json")
        response = connection.getresponse()
        payload = response.read()
        if response.status == 404:
            return None, ""
        if response.status != 200:
            return None, f"docker_api_http_{response.status}:{name}"
        value = json.loads(payload)
        if not isinstance(value, Mapping):
            return None, f"docker_api_json_not_object:{name}"
        return dict(value), ""
    except (OSError, TimeoutError, http.client.HTTPException, json.JSONDecodeError):
        return None, f"docker_api_read_failed:{name}"
    finally:
        connection.close()


def _docker_documents_once(
    names: list[str],
) -> tuple[dict[str, dict[str, Any]], str]:
    """Read profiles concurrently so one busy CLI client cannot serialize them."""

    documents: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=min(16, max(1, len(names)))) as pool:
        futures = {pool.submit(_docker_document, name): name for name in names}
        for future in as_completed(futures):
            name = futures[future]
            try:
                value, error = future.result()
            except BaseException as exception:  # fail closed on worker defects
                value = None
                error = f"docker_api_worker_failed:{name}:{type(exception).__name__}"
            if value is not None:
                documents[name] = value
            if error:
                errors.append(error)
    return documents, "\n".join(sorted(errors))


def _docker_documents(names: list[str]) -> tuple[dict[str, dict[str, Any]], str]:
    """Retry one transient daemon delay without relaxing any profile check."""

    last_error = ""
    for attempt in range(2):
        documents, error = _docker_documents_once(names)
        if documents:
            return documents, error
        last_error = error
        if attempt == 0:
            time.sleep(0.25)
    return {}, last_error


def _actual_row(name: str, value: Mapping[str, Any]) -> dict[str, Any]:
    host = value.get("HostConfig") or {}
    mounts = value.get("Mounts") or []
    state = value.get("State") or {}
    return {
        "name": name,
        "container_id": str(value.get("Id") or ""),
        "image_id": str(value.get("Image") or ""),
        "status": str(state.get("Status") or ""),
        "running": state.get("Running") is True,
        "pid": int(state.get("Pid") or 0),
        "cpuset_cpus": str(host.get("CpusetCpus") or ""),
        "cpuset_mems": str(host.get("CpusetMems") or ""),
        "nano_cpus": int(host.get("NanoCpus") or 0),
        "memory_bytes": int(host.get("Memory") or 0),
        "memory_swap_bytes": int(host.get("MemorySwap") or 0),
        "pids_limit": host.get("PidsLimit"),
        "mount_sources": sorted(
            str(item.get("Source") or "")
            for item in mounts
            if isinstance(item, Mapping)
        ),
        "labels": dict((value.get("Config") or {}).get("Labels") or {}),
    }


def _validate_base(
    *, expected: Mapping[str, Any], actual: Mapping[str, Any],
    target_cpus: set[int], allowed_union: set[int],
) -> list[str]:
    allowed_profiles = {
        str(item)
        for item in expected.get("allowed_cpuset_cpus", [expected["cpuset_cpus"]])
    }
    allowed_memory = {
        int(item)
        for item in expected.get("allowed_memory_bytes", [expected["memory_bytes"]])
    }
    sources = list(actual["mount_sources"])
    actual_cpus = coordination.cpus(str(actual["cpuset_cpus"]))
    errors: list[str] = []
    if actual["container_id"] != expected["container_id"]:
        errors.append("container_identity_changed")
    if actual["running"] is not True:
        errors.append("container_not_running")
    if actual["cpuset_cpus"] not in allowed_profiles:
        errors.append("container_cpuset_not_in_sealed_profiles")
    if not actual_cpus or not actual_cpus <= allowed_union:
        errors.append("container_cpu_not_in_external_union")
    if actual_cpus & target_cpus:
        errors.append("container_cpu_overlaps_mysql")
    if actual["memory_bytes"] not in allowed_memory:
        errors.append("container_memory_not_in_sealed_profiles")
    if str(expected["disk1_mount_source"]) not in sources:
        errors.append("container_disk1_mount_changed")
    if any(source.startswith("/disk2/") for source in sources):
        errors.append("container_mounts_mysql_disk2")
    return errors


def _validate_additional(
    *, profile: Mapping[str, Any], actual: Mapping[str, Any],
    target_cpus: set[int], allowed_union: set[int],
) -> list[str]:
    errors: list[str] = []
    actual_cpus = coordination.cpus(str(actual["cpuset_cpus"]))
    if actual["status"] not in set(profile.get("allowed_statuses") or ["running"]):
        errors.append("additional_container_status_not_sealed")
    if actual["image_id"] != profile["image_id"]:
        errors.append("additional_container_image_changed")
    if actual["cpuset_cpus"] != profile["cpuset_cpus"]:
        errors.append("additional_container_cpuset_changed")
    if actual["cpuset_mems"] != profile.get("cpuset_mems", ""):
        errors.append("additional_container_cpuset_mems_changed")
    if not actual_cpus or not actual_cpus <= allowed_union:
        errors.append("additional_container_cpu_not_in_external_union")
    if actual_cpus & target_cpus:
        errors.append("additional_container_cpu_overlaps_mysql")
    for key in ("nano_cpus", "memory_bytes", "memory_swap_bytes", "pids_limit"):
        if actual[key] != profile.get(key):
            errors.append(f"additional_container_{key}_changed")
    sources = list(actual["mount_sources"])
    for required in profile.get("required_mount_sources") or []:
        if required not in sources:
            errors.append("additional_container_required_mount_missing")
    for prefix in profile.get("required_mount_prefixes") or []:
        if not any(source.startswith(prefix) for source in sources):
            errors.append("additional_container_required_mount_prefix_missing")
    allowed_prefixes = tuple(profile.get("allowed_mount_prefixes") or [])
    if allowed_prefixes and any(
        not any(source.startswith(prefix) for prefix in allowed_prefixes)
        for source in sources
    ):
        errors.append("additional_container_mount_outside_sealed_roots")
    if any(source.startswith("/disk2/") for source in sources):
        errors.append("additional_container_mounts_mysql_disk2")
    labels = actual["labels"]
    for key, expected in (profile.get("required_labels") or {}).items():
        if labels.get(key) != expected:
            errors.append(f"additional_container_label_changed:{key}")
    return errors


def live_sealed_pg_container_readback(policy: dict[str, Any]) -> dict[str, Any]:
    """Read back the original cohort plus optional sealed mixstrg profiles."""

    base = list(policy.get("containers") or [])
    additional = list(policy.get("additional_container_profiles") or [])
    names = [str(item["name"]) for item in [*base, *additional]]
    documents, inspect_stderr = _docker_documents(names)
    target_cpus = coordination._target_cpu_union()
    allowed_union = coordination.cpus(
        str(policy.get("allowed_external_cpu_union") or "")
    )
    rows: list[dict[str, Any]] = []
    extra_rows: list[dict[str, Any]] = []
    errors: list[str] = []

    for expected in base:
        name = str(expected["name"])
        value = documents.get(name)
        if value is None:
            errors.append(f"container_inspect_failed:{name}")
            continue
        actual = _actual_row(name, value)
        rows.append(actual)
        errors.extend(
            f"container_isolation_readback_differs:{name}:{message}"
            for message in _validate_base(
                expected=expected,
                actual=actual,
                target_cpus=target_cpus,
                allowed_union=allowed_union,
            )
        )

    for profile in additional:
        name = str(profile["name"])
        value = documents.get(name)
        if value is None:
            extra_rows.append({
                "name": name,
                "present": False,
                "required": bool(profile.get("required", False)),
                "errors": [],
            })
            if profile.get("required") is True:
                errors.append(f"additional_container_inspect_failed:{name}")
            continue
        actual = _actual_row(name, value)
        profile_errors = _validate_additional(
            profile=profile,
            actual=actual,
            target_cpus=target_cpus,
            allowed_union=allowed_union,
        )
        actual["present"] = True
        actual["profile_role"] = profile.get("role")
        actual["errors"] = profile_errors
        extra_rows.append(actual)
        errors.extend(
            f"additional_container_isolation_readback_differs:{name}:{message}"
            for message in profile_errors
        )
    return {
        "passed": not errors,
        "errors": errors,
        "containers": rows,
        "additional_containers": extra_rows,
        "inspect_stderr_sha256": (
            __import__("hashlib").sha256(inspect_stderr.encode()).hexdigest()
            if inspect_stderr else None
        ),
        "read_only": True,
        "external_mutation": False,
    }


def _pid_container_id(pid: int) -> str | None:
    try:
        text = Path(f"/proc/{pid}/cgroup").read_text(
            encoding="utf-8", errors="replace"
        )
    except OSError:
        return None
    for field in text.replace("/", " ").replace("-", " ").split():
        if len(field) == 64 and all(ch in "0123456789abcdef" for ch in field):
            return field
    return None


def evaluate_co_resident_pg_isolation(
    workloads: list[dict[str, Any]],
) -> dict[str, Any]:
    """Admit only workload PIDs inside a currently validated extra profile."""

    result = ORIGINAL_EVALUATE(workloads)
    if not workloads or not isinstance(result.get("container_readback"), Mapping):
        return result
    extra = result["container_readback"].get("additional_containers") or []
    valid_ids = {
        str(row.get("container_id")): str(row.get("name"))
        for row in extra
        if row.get("present") is True
        and row.get("running") is True
        and not (row.get("errors") or [])
        and row.get("container_id")
    }
    recognized: dict[int, str] = {}
    for row in workloads:
        pid = int(row["pid"])
        container_id = _pid_container_id(pid)
        if container_id in valid_ids:
            recognized[pid] = valid_ids[container_id]

    errors = list(result.get("errors") or [])
    workload_rows = copy.deepcopy(result.get("workloads") or [])
    for row in workload_rows:
        pid = int(row["pid"])
        if pid not in recognized:
            continue
        row["errors"] = [
            value for value in (row.get("errors") or [])
            if value != "external_command_not_in_frozen_pg_cohort"
        ]
        row["sealed_additional_container"] = recognized[pid]
        errors = [
            value for value in errors
            if value != f"pid_{pid}:external_command_not_in_frozen_pg_cohort"
        ]

    result["errors"] = errors
    result["workloads"] = workload_rows
    result["additional_container_process_recognition"] = {
        "recognized_pid_count": len(recognized),
        "recognized": [
            {"pid": pid, "container": name}
            for pid, name in sorted(recognized.items())
        ],
        "authority": "live_docker_cgroup_membership_and_sealed_profile",
    }
    result["passed"] = not errors
    result["status"] = (
        "PASS_ISOLATED_CO_RESIDENT_PG"
        if not errors else "BLOCKED_FAIL_CLOSED"
    )
    return result


def command_kind(command: str) -> str | None:
    """Classify an exact host ``docker exec`` RPC as control-plane only.

    The psql/pg_isready process created *inside* the container remains a
    workload and is admitted only through the sealed live cgroup profile.
    """

    try:
        fields = shlex.split(command)
    except ValueError:
        fields = command.split()
    if (
        len(fields) >= 4
        and Path(fields[0]).name == "docker"
        and fields[1] == "exec"
        and fields[2] in ADDITIONAL_SERVER_NAMES
        and Path(fields[3]).name in {"psql", "pg_isready"}
    ):
        return "controller"
    return ORIGINAL_COMMAND_KIND(command)


def install() -> None:
    """Install read-only v56 evaluation into the shared coordination module."""

    if not CONTRACT_PATH.is_file() or CONTRACT_PATH.is_symlink():
        raise RuntimeError(f"coordination contract absent: {CONTRACT_PATH}")
    coordination.CONTRACT_PATH = CONTRACT_PATH
    coordination._live_frozen_pg_container_readback = (
        live_sealed_pg_container_readback
    )
    coordination.evaluate_co_resident_pg_isolation = (
        evaluate_co_resident_pg_isolation
    )
    coordination.command_kind = command_kind
