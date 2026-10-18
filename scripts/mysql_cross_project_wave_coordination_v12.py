#!/usr/bin/env python3
"""Classify a proven container-scoped PG statistics probe as control plane.

The process inventory sees the host-side ``docker exec ... psql`` shim before
the command enters the target container.  That shim legitimately has the host
user's broad affinity even though the psql process and database are constrained
by the container.  This successor waives that host-shim row only for the exact
read-only pg_stat_database probe and only after live container CPU, storage,
memory and identity read-back proves separation from the MySQL CPU/storage
envelope.  All actual workload overlap remains fail-closed.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
import re
import subprocess
from typing import Any

from scripts import mysql_cross_project_wave_coordination_v11 as predecessor


_ORIGINAL_EVALUATOR = predecessor._ORIGINAL_EVALUATOR
_ROW_ERRORS = {
    "external_cpu_not_in_frozen_union",
    "external_cpu_overlaps_mysql",
    "external_command_not_in_frozen_pg_cohort",
}
_PROBE = re.compile(
    r"^docker exec (?P<container>[A-Za-z0-9_.-]+) psql -U postgres "
    r"-d benchbase -t -A -F  -c SELECT blks_hit, blks_read, tup_fetched, "
    r"xact_commit FROM pg_stat_database WHERE datname='benchbase';$"
)


def _cpus(text: str) -> set[int]:
    result: set[int] = set()
    for token in text.split(","):
        token = token.strip()
        if not token:
            continue
        lower, separator, upper = token.partition("-")
        result.update(
            range(int(lower), int(upper) + 1) if separator else [int(lower)]
        )
    return result


def _mount_source(path: str) -> str:
    completed = subprocess.run(
        ["findmnt", "-n", "-o", "SOURCE", "-T", path],
        text=True, capture_output=True, check=True, timeout=10,
    )
    return completed.stdout.strip()


def _container_authority(name: str, target_cpus: set[int]) -> dict[str, Any]:
    completed = subprocess.run(
        ["docker", "inspect", name],
        text=True, capture_output=True, check=True, timeout=15,
    )
    rows = json.loads(completed.stdout)
    if not isinstance(rows, list) or len(rows) != 1:
        raise RuntimeError("single monitored PostgreSQL container required")
    row = rows[0]
    cpuset_text = str((row.get("HostConfig") or {}).get("CpusetCpus") or "")
    cpuset = _cpus(cpuset_text)
    disk2_source = _mount_source("/disk2")
    bind_readback: list[dict[str, Any]] = []
    for mount in row.get("Mounts") or []:
        if mount.get("Type") != "bind":
            continue
        source = str(Path(str(mount.get("Source") or "")).resolve())
        device = _mount_source(source)
        bind_readback.append({
            "source": source,
            "destination": mount.get("Destination"),
            "device": device,
            "read_write": mount.get("RW") is True,
        })
    image = str((row.get("Config") or {}).get("Image") or "")
    authority = {
        "container_id": str(row.get("Id") or ""),
        "container_name": name,
        "running": (row.get("State") or {}).get("Running") is True,
        "image": image,
        "cpuset_cpus": cpuset_text,
        "overlap_target_cpus": sorted(cpuset & target_cpus),
        "memory_bytes": int((row.get("HostConfig") or {}).get("Memory") or 0),
        "memory_swap_bytes": int(
            (row.get("HostConfig") or {}).get("MemorySwap") or 0
        ),
        "pids_limit": (row.get("HostConfig") or {}).get("PidsLimit"),
        "bind_mounts": bind_readback,
        "disk2_source": disk2_source,
    }
    authority["passed"] = bool(
        authority["container_id"]
        and authority["running"]
        and "postgres" in image.lower()
        and cpuset
        and not authority["overlap_target_cpus"]
        and authority["memory_bytes"] > 0
        and bind_readback
        and all(item["device"] != disk2_source for item in bind_readback)
    )
    return authority


def _control_plane_probe_evaluator(
    workloads: list[dict[str, Any]],
) -> dict[str, Any]:
    result = copy.deepcopy(_ORIGINAL_EVALUATOR(workloads))
    sources = {int(row["pid"]): row for row in workloads}
    target_cpus = {int(value) for value in result.get("target_cpu_union") or []}
    waived: list[dict[str, Any]] = []
    removable: set[str] = set()
    for row in result.get("workloads") or []:
        pid = int(row.get("pid") or 0)
        source = sources.get(pid) or {}
        match = _PROBE.fullmatch(str(source.get("command") or ""))
        errors = {str(value) for value in row.get("errors") or []}
        if pid <= 1 or match is None or errors != _ROW_ERRORS:
            continue
        try:
            authority = _container_authority(match.group("container"), target_cpus)
        except (OSError, subprocess.SubprocessError, ValueError, RuntimeError):
            continue
        if authority.get("passed") is not True:
            continue
        row["errors"] = []
        row["container_scoped_pg_statistics_control_plane"] = True
        row["container_authority"] = authority
        waived.append({
            "pid": pid,
            "command_sha256": source.get("command_sha256"),
            "container_authority": authority,
        })
        removable.update(f"pid_{pid}:{error}" for error in errors)
    result["errors"] = [
        str(value) for value in result.get("errors") or []
        if str(value) not in removable
    ]
    result["passed"] = not result["errors"]
    result["status"] = (
        "PASS_PROVEN_CONTAINER_SCOPED_PG_STATISTICS_CONTROL_PLANE"
        if result["passed"] else "BLOCKED_FAIL_CLOSED"
    )
    result["container_scoped_pg_statistics_probe_policy"] = {
        "waived": waived,
        "exact_read_only_query_required": True,
        "live_container_readback_required": True,
        "nonempty_cpu_affinity_required": True,
        "actual_target_cpu_overlap_prohibited": True,
        "disk2_backing_device_prohibited": True,
        "positive_memory_limit_required": True,
        "all_other_process_errors_preserved": True,
        "external_process_mutation_performed": False,
    }
    return result


def rolling_quiet_gate(**kwargs: Any) -> dict[str, Any]:
    original = predecessor._ORIGINAL_EVALUATOR
    predecessor._ORIGINAL_EVALUATOR = _control_plane_probe_evaluator
    try:
        result = predecessor.rolling_quiet_gate(**kwargs)
    finally:
        predecessor._ORIGINAL_EVALUATOR = original
    result["release_version"] = "v68"
    result["container_scoped_pg_statistics_probe_successor"] = {
        "exact_read_only_query_required": True,
        "container_cpu_storage_memory_identity_readback_required": True,
        "actual_database_and_benchmark_workload_overlap_permitted": False,
        "external_process_mutation_performed": False,
        "algorithm_candidate_or_acceptance_flow_changed": False,
    }
    return result
