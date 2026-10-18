#!/usr/bin/env python3
"""Target-local swap and inactive-container successor for the rolling gate.

The v20 MySQL resource freeze disables anonymous swap for every target worker
with ``memory.swappiness=0``.  Requiring the unrelated host swap partition to
be absent contradicts that freeze and caused otherwise clean waves to time
out.  This successor keeps the v11 continuous process/CPU, memory PSI,
storage, and D-state checks, but replaces only that global swap predicate with
live target-container resource read-back.

Exited frozen PostgreSQL containers are inventory, not co-resident work.  The
running ``bench-pg-row`` container has no fixed cpuset, so it is accepted only
while its immutable identity and disk3 mount are intact, its sampled process
CPUs are disjoint from the current MySQL wave, and its measured CPU activity
remains below a conservative background-idle bound.  Actual process overlap
is never waived and no external process is changed.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any, Mapping

from scripts import mysql_cross_project_wave_coordination_v4 as gate_v4
from scripts import mysql_cross_project_wave_coordination_v11 as predecessor


ROOT = Path(__file__).resolve().parents[1]
FREEZE = ROOT / (
    "config/formal/frozen_mysql_four_scene_two_worker_20260903_v20/"
    "four_scene_effective_resource_successor.json"
)
PREPARED = ROOT / (
    "history/mysql_four_scene_20260809/"
    "v1192_to_c_client_primary_live_authority_20260903/authority/"
    "prepared_binding.json"
)
BENCH_ROW_NAME = "bench-pg-row"
BENCH_ROW_ID = "d54603d356d1964e7bcbce5dab488ba3808bd40e4033d0347ff51997b8a860e5"
BENCH_ROW_MOUNT = "/disk3/wxr-data/cmp/row"
MAXIMUM_BENCH_ROW_CPU_CORES = 0.10


def _cpus(text: str) -> set[int]:
    result: set[int] = set()
    for token in str(text).split(","):
        left, separator, right = token.strip().partition("-")
        if left:
            result.update(
                range(int(left), int(right) + 1) if separator else [int(left)]
            )
    return result


def _read_int(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="ascii").strip())
    except (FileNotFoundError, PermissionError, OSError, ValueError):
        return None


def _docker_inspect(names: list[str]) -> dict[str, dict[str, Any]]:
    completed = subprocess.run(
        ["docker", "inspect", *names], capture_output=True, text=True,
        check=True, timeout=60,
    )
    values = json.loads(completed.stdout)
    return {
        str(value.get("Name") or "").lstrip("/"): dict(value)
        for value in values
    }


def target_local_resource_proof(target_cpus: set[int]) -> dict[str, Any]:
    freeze = json.loads(FREEZE.read_text(encoding="utf-8"))
    prepared = json.loads(PREPARED.read_text(encoding="utf-8"))
    expected_by_worker = {
        str(row["worker_id"]): dict(row) for row in freeze["workers"]
    }
    prepared_by_worker = {
        str(row["worker_id"]): dict(row) for row in prepared["workers"]
    }
    selected = {
        worker_id: row for worker_id, row in expected_by_worker.items()
        if _cpus(str(row["database_cpuset"])) & target_cpus
    }
    selected_union = set().union(*(
        _cpus(str(row["database_cpuset"]))
        | _cpus(str(row["client_cpuset"]))
        for row in selected.values()
    )) if selected else set()
    names = [str(row["container"]) for row in selected.values()]
    errors: list[str] = []
    rows: list[dict[str, Any]] = []
    if not selected or selected_union != target_cpus:
        errors.append("target_cpu_scope_does_not_match_complete_frozen_workers")
    try:
        actual_by_name = _docker_inspect(names) if names else {}
    except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError) as error:
        actual_by_name = {}
        errors.append(f"target_container_inspect_failed:{type(error).__name__}")
    for worker_id, expected in sorted(selected.items()):
        name = str(expected["container"])
        actual = actual_by_name.get(name) or {}
        host = actual.get("HostConfig") or {}
        state = actual.get("State") or {}
        authority = prepared_by_worker.get(worker_id) or {}
        container_id = str(actual.get("Id") or "")
        memory_path = Path("/sys/fs/cgroup/memory/docker-limited") / container_id
        blkio_path = Path("/sys/fs/cgroup/blkio/docker-limited") / container_id
        swappiness = _read_int(memory_path / "memory.swappiness")
        read_throttle = ""
        write_throttle = ""
        try:
            read_throttle = (blkio_path / "blkio.throttle.read_bps_device").read_text(
                encoding="ascii"
            )
            write_throttle = (blkio_path / "blkio.throttle.write_bps_device").read_text(
                encoding="ascii"
            )
        except (FileNotFoundError, PermissionError, OSError):
            pass
        device = str((authority.get("blkio_authority") or {}).get(
            "device_major_minor"
        ) or "259:6")
        expected_read = int(expected["io_read_bps"])
        expected_write = int(expected["io_write_bps"])
        row_errors: list[str] = []
        checks = {
            "identity": container_id == str(authority.get("container_id") or ""),
            "running": state.get("Running") is True,
            "cpuset": str(host.get("CpusetCpus") or "") == str(expected["database_cpuset"]),
            "numa": str(host.get("CpusetMems") or "") == str(expected["database_cpuset_mems"]),
            "cpu_quota": int(host.get("NanoCpus") or 0)
            == int(round(float(expected["cpu_quota_cores"]) * 1_000_000_000)),
            "memory": int(host.get("Memory") or 0) == int(expected["memory_bytes"]),
            "swappiness_zero": swappiness == int(expected["memory_swappiness"]) == 0,
            "pids": int(host.get("PidsLimit") or 0) == int(expected["pids_max"]),
            "read_bps": f"{device} {expected_read}" in read_throttle.splitlines(),
            "write_bps": f"{device} {expected_write}" in write_throttle.splitlines(),
            "prepared_swap_policy": authority.get("effective_swap_policy")
            == "anonymous_swap_disabled_swappiness_0",
        }
        row_errors.extend(name for name, passed in checks.items() if not passed)
        errors.extend(f"target_{worker_id}:{name}" for name in row_errors)
        rows.append({
            "worker_id": worker_id, "container": name,
            "container_id": container_id, "checks": checks,
            "errors": row_errors, "memory_swappiness": swappiness,
            "device_major_minor": device,
            "read_throttle": read_throttle.splitlines(),
            "write_throttle": write_throttle.splitlines(),
        })
    return {
        "passed": not errors,
        "errors": errors,
        "target_cpus": sorted(target_cpus),
        "selected_workers": sorted(selected),
        "workers": rows,
        "freeze_path": str(FREEZE),
        "prepared_binding_path": str(PREPARED),
        "effective_swap_policy": "anonymous_swap_disabled_swappiness_0",
        "host_swap_state_is_audited_not_mutated": True,
    }


class BenchRowIdleProof:
    def __init__(self, target_cpus: set[int]):
        self.target_cpus = set(target_cpus)
        self.initial_usage: int | None = None
        self.initial_time: float | None = None
        self.boundary_row = self._live_row()

    @staticmethod
    def _live_row() -> dict[str, Any]:
        try:
            value = _docker_inspect([BENCH_ROW_NAME])[BENCH_ROW_NAME]
        except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError, KeyError):
            return {}
        host = value.get("HostConfig") or {}
        return {
            "name": BENCH_ROW_NAME,
            "container_id": value.get("Id"),
            "running": (value.get("State") or {}).get("Running") is True,
            "memory_bytes": int(host.get("Memory") or 0),
            "nano_cpus": int(host.get("NanoCpus") or 0),
            "mount_sources": sorted(
                str(row.get("Source") or "")
                for row in value.get("Mounts") or [] if row.get("Source")
            ),
        }

    def placement_stable(self) -> bool:
        return self._live_row() == self.boundary_row

    def sample(self, readback: Mapping[str, Any]) -> dict[str, Any]:
        additional = {
            str(row.get("name") or ""): row
            for row in readback.get("additional_containers") or []
        }
        row = dict(additional.get(BENCH_ROW_NAME) or self.boundary_row)
        container_id = str(row.get("container_id") or "")
        cpu_path = Path("/sys/fs/cgroup/cpu,cpuacct/docker-limited") / container_id
        usage = _read_int(cpu_path / "cpuacct.usage")
        now = time.monotonic()
        elapsed = None if self.initial_time is None else now - self.initial_time
        delta = None if self.initial_usage is None or usage is None else usage - self.initial_usage
        cores = None if not elapsed or delta is None else max(0, delta) / (elapsed * 1e9)
        if self.initial_usage is None and usage is not None:
            self.initial_usage, self.initial_time = usage, now
        processors: list[int] = []
        active_processors: list[int] = []
        process_count = 0
        if container_id:
            for proc in Path("/proc").iterdir():
                if not proc.name.isdigit():
                    continue
                try:
                    if container_id not in (proc / "cgroup").read_text(
                        encoding="utf-8", errors="replace"
                    ):
                        continue
                    raw = (proc / "stat").read_text(encoding="ascii")
                    tail = raw[raw.rfind(")") + 2:].split()
                    processor = int(tail[36])
                    processors.append(processor)
                    if tail[0] in {"R", "D"}:
                        active_processors.append(processor)
                    process_count += 1
                except (FileNotFoundError, PermissionError, ProcessLookupError, OSError, ValueError, IndexError):
                    continue
        mounts = [str(value) for value in row.get("mount_sources") or []]
        checks = {
            "present": bool(row),
            "identity": container_id == BENCH_ROW_ID,
            "running": row.get("running") is True,
            "disk3_mount": BENCH_ROW_MOUNT in mounts
            and not any(value.startswith(("/disk1/", "/disk2/")) for value in mounts),
            "memory_limit": int(row.get("memory_bytes") or 0) == 64 * 1024**3,
            "cpu_quota": int(row.get("nano_cpus") or 0) == 32_000_000_000,
            "activity_initialized": cores is not None,
            "background_cpu_bounded": cores is not None and cores <= MAXIMUM_BENCH_ROW_CPU_CORES,
            # A sleeping task's stat processor is only its historical last
            # CPU.  Interference requires a runnable/uninterruptible task on a
            # target CPU; total CPU usage is bounded independently above.
            "active_sampled_processors_disjoint": set(active_processors).isdisjoint(
                self.target_cpus
            ),
        }
        return {
            "passed": all(checks.values()), "checks": checks,
            "cpu_cores_since_gate_start": cores,
            "maximum_cpu_cores": MAXIMUM_BENCH_ROW_CPU_CORES,
            "sampled_processors": sorted(set(processors)),
            "active_sampled_processors": sorted(set(active_processors)),
            "active_target_cpu_overlap": sorted(
                set(active_processors) & self.target_cpus
            ),
            "process_count": process_count,
            "external_process_mutation_performed": False,
        }


def _clean_external_container_readback(
    raw: Mapping[str, Any], bench: Mapping[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    value = copy.deepcopy(dict(raw))
    waived: list[str] = []
    retained: list[str] = []
    inactive = {
        str(row.get("name") or "")
        for row in value.get("containers") or []
        if row.get("running") is False and int(row.get("pid") or 0) == 0
    }
    bench_prefix = f"additional_container_isolation_readback_differs:{BENCH_ROW_NAME}:"
    for error_value in value.get("errors") or []:
        error = str(error_value)
        inactive_error = any(error == f"container_not_running:{name}" for name in inactive)
        bench_error = error.startswith(bench_prefix)
        if inactive_error or (bench_error and bench.get("passed") is True):
            waived.append(error)
        else:
            retained.append(error)
    value["errors"] = retained
    value["passed"] = not retained
    value["inactive_frozen_containers"] = sorted(inactive)
    value["bench_row_idle_isolation_proof"] = copy.deepcopy(dict(bench))
    return value, waived


def _effective(
    result: dict[str, Any], target: Mapping[str, Any], bench_sampler: BenchRowIdleProof,
) -> dict[str, Any]:
    value = copy.deepcopy(result)
    bench = bench_sampler.sample(value.get("container_readback") or {})
    readback, waived_readback = _clean_external_container_readback(
        value.get("container_readback") or {}, bench,
    )
    value["container_readback"] = readback
    legacy_errors = {str(item) for item in value.get("errors") or []}
    tolerated = set(gate_v4.predecessor.TOLERATED_LEGACY_ERRORS)
    tolerated.add("host_swap_must_remain_disabled")
    tolerated.update(waived_readback)
    if bench.get("passed") is True:
        tolerated.update(
            error for error in legacy_errors
            if error.startswith(
                f"additional_container_isolation_readback_differs:{BENCH_ROW_NAME}:"
            )
        )
    retained_legacy = sorted(legacy_errors - tolerated)
    row_errors = [
        str(error)
        for row in value.get("workloads") or [] if isinstance(row, dict)
        for error in row.get("errors") or []
    ]
    available_raw = value.get("host_mem_available_bytes")
    psi_raw = value.get("host_memory_full_psi_avg10")
    if available_raw is None:
        for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
            if line.startswith("MemAvailable:"):
                available_raw = int(line.split()[1]) * 1024
                break
    if psi_raw is None:
        for line in Path("/proc/pressure/memory").read_text(encoding="ascii").splitlines():
            if line.startswith("full "):
                for token in line.split():
                    if token.startswith("avg10="):
                        psi_raw = float(token.split("=", 1)[1])
                        break
    available = 0 if available_raw is None else int(available_raw)
    psi = float("inf") if psi_raw is None else float(psi_raw)
    errors: list[str] = []
    if retained_legacy:
        errors.append("legacy_isolation_failure_not_explicitly_superseded")
    if readback and readback.get("passed") is not True:
        errors.append("external_storage_or_container_isolation_unproven")
    if row_errors:
        errors.append("external_process_cpu_or_identity_isolation_unproven")
    if target.get("passed") is not True:
        errors.append("target_mysql_local_resource_isolation_unproven")
    if bench.get("passed") is not True:
        errors.append("bench_pg_row_idle_cpu_storage_isolation_unproven")
    if available < gate_v4.predecessor.MINIMUM_HOST_MEM_AVAILABLE_BYTES:
        errors.append("dynamic_host_memory_headroom_below_minimum")
    if psi > gate_v4.predecessor.MAXIMUM_MEMORY_FULL_PSI_AVG10:
        errors.append("host_memory_full_psi_exceeds_dynamic_maximum")
    value["legacy_errors"] = sorted(legacy_errors)
    value["passed"] = not errors
    value["status"] = (
        "PASS_TARGET_LOCAL_SWAP_DYNAMIC_ISOLATION"
        if not errors else "BLOCKED_FAIL_CLOSED"
    )
    value["dynamic_noninterference_successor"] = {
        "applied": True,
        "dynamic_errors": errors,
        "retained_legacy_errors": retained_legacy,
        "waived_legacy_errors": sorted(legacy_errors & tolerated),
        "waived_external_readback_errors": waived_readback,
        "target_local_resource_proof": copy.deepcopy(dict(target)),
        "bench_pg_row_idle_isolation_proof": bench,
        "host_swap_total_bytes": value.get("host_swap_total_bytes"),
        "host_swap_policy": "audit_only_target_anonymous_swap_disabled",
        "actual_process_cpu_overlap_permitted": False,
        "external_process_mutation_performed": False,
    }
    return value


def rolling_quiet_gate(**kwargs: Any) -> dict[str, Any]:
    target_cpus = {int(value) for value in kwargs.get("target_cpus") or set()}
    if not target_cpus:
        raise ValueError("actual target CPU scope is required")
    target = target_local_resource_proof(target_cpus)
    bench_sampler = BenchRowIdleProof(target_cpus)
    original = gate_v4.effective_isolation
    gate_v4.effective_isolation = lambda value: _effective(
        value, target, bench_sampler
    )
    try:
        result = predecessor.rolling_quiet_gate(**kwargs)
    finally:
        gate_v4.effective_isolation = original
    placement_stable = bench_sampler.placement_stable()
    if result.get("status") == "PASS" and not placement_stable:
        result["status"] = "BLOCKED_FAIL_CLOSED"
        result["errors"] = ["bench_pg_row_boundary_placement_changed"]
    result["schema_version"] = "mysql_cross_project_wave_gate_v25"
    result["release_version"] = "v81-target-local-swap"
    result["target_local_swap_successor"] = {
        "target_local_resource_proof": target,
        "host_swap_mutation_performed": False,
        "external_process_control_or_stop_performed": False,
        "actual_process_cpu_overlap_permitted": False,
        "inactive_container_is_not_co_resident_work": True,
        "bench_pg_row_requires_continuous_idle_activity_and_cpu_sampling": True,
        "bench_pg_row_boundary_placement_stable": placement_stable,
    }
    return result


for _name in dir(predecessor):
    if not _name.startswith("__") and _name != "rolling_quiet_gate":
        globals().setdefault(_name, getattr(predecessor, _name))
