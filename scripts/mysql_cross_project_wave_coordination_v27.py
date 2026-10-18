#!/usr/bin/env python3
"""Docker-API-free rolling non-interference gate for formal MySQL waves.

The predecessor obtained both target and co-resident state through nested
``docker inspect``/``docker ps`` calls.  A daemon control-plane stall therefore
failed an otherwise healthy measurement before the workload could start.  The
formal resource authority already freezes immutable container IDs and cgroup
v1 paths, so this successor reads those kernel interfaces directly.

No process is stopped, signalled, reprioritized, or otherwise mutated here.
Sibling scenes are evaluated independently and never create a completion
dependency.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / (
    "config/formal/"
    "frozen_mysql_cross_project_wave_coordination_20260906_v110/"
    "coordination_contract.json"
)
# Compatibility-only identity for the inherited validator.  The v110 runtime
# records lease_mode=none and never acquires this lock.
LOCK_PATH = Path("/tmp/resource_adaptive_pg_ssd_measurement_v1.lock")
FREEZE = ROOT / (
    "config/formal/frozen_mysql_four_scene_two_worker_20260903_v20/"
    "four_scene_effective_resource_successor.json"
)
PREPARED = ROOT / (
    "history/mysql_four_scene_20260809/"
    "v1192_to_c_client_primary_live_authority_20260903/authority/"
    "prepared_binding.json"
)
TARGET_DEVICE = "259:6"
MINIMUM_HOST_MEM_AVAILABLE_BYTES = 256 * 1024**3
MAXIMUM_MEMORY_FULL_PSI_AVG10 = 0.10
PERSISTENT_D_STATE_SAMPLES = 3

CGROUP_ROOTS = {
    "cpuset": Path("/sys/fs/cgroup/cpuset/docker-limited"),
    "cpu": Path("/sys/fs/cgroup/cpu,cpuacct/docker-limited"),
    "memory": Path("/sys/fs/cgroup/memory/docker-limited"),
    "pids": Path("/sys/fs/cgroup/pids/docker-limited"),
    "blkio": Path("/sys/fs/cgroup/blkio/docker-limited"),
}
DATABASE_COMMS = {"postgres", "postmaster", "mysqld", "mariadbd"}
CLIENT_COMMS = {"mysql", "mysqlslap", "psql", "pgbench", "sysbench"}
COMMAND_MARKERS = (
    "/resource_adaptive/shell/run_pg_",
    "/resource_adaptive_mysql/scripts/run_mysql_",
    "hybench", "run_job", "run_tpch", "tpch_runner", "job_runner",
    "postgresql", "pgbench", "sysbench",
)
COMMAND_INSPECTION_COMMS = {
    "python", "python3", "bash", "sh", "zsh", "docker", "java", "timeout",
}


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def _cpus(text: str) -> set[int]:
    values: set[int] = set()
    for token in str(text).split(","):
        left, separator, right = token.strip().partition("-")
        if not left:
            continue
        values.update(range(int(left), int(right) + 1) if separator else [int(left)])
    return values


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
        return None


def _read_int(path: Path) -> int | None:
    text = _read_text(path)
    try:
        return None if text is None else int(text)
    except ValueError:
        return None


def _selected_workers(target_cpus: set[int]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    freeze = _load(FREEZE)
    prepared = _load(PREPARED)
    authority = {
        str(row["worker_id"]): dict(row) for row in prepared.get("workers") or []
    }
    selected: list[dict[str, Any]] = []
    selected_union: set[int] = set()
    for raw in freeze.get("workers") or []:
        row = dict(raw)
        worker_cpus = _cpus(str(row["database_cpuset"])) | _cpus(
            str(row["client_cpuset"])
        )
        if worker_cpus & target_cpus:
            row["authority"] = authority.get(str(row["worker_id"])) or {}
            row["worker_cpus"] = sorted(worker_cpus)
            selected.append(row)
            selected_union.update(worker_cpus)
    return selected, {
        "target_cpu_scope_exact": bool(selected) and selected_union == target_cpus,
        "selected_union": sorted(selected_union),
    }


def _target_worker_readback(row: Mapping[str, Any]) -> dict[str, Any]:
    authority = dict(row.get("authority") or {})
    container_id = str(authority.get("container_id") or "")
    paths = {name: root / container_id for name, root in CGROUP_ROOTS.items()}
    expected_db_cpus = _cpus(str(row["database_cpuset"]))
    procs_text = _read_text(paths["pids"] / "cgroup.procs")
    pids = [] if not procs_text else [int(value) for value in procs_text.split()]
    process_affinities: dict[str, str | None] = {}
    for pid in pids:
        status = _status(Path("/proc") / str(pid))
        process_affinities[str(pid)] = status.get("Cpus_allowed_list")
    quota = _read_int(paths["cpu"] / "cpu.cfs_quota_us")
    period = _read_int(paths["cpu"] / "cpu.cfs_period_us")
    quota_cores = None if quota is None or not period else quota / period
    read_lines = (_read_text(paths["blkio"] / "blkio.throttle.read_bps_device") or "").splitlines()
    write_lines = (_read_text(paths["blkio"] / "blkio.throttle.write_bps_device") or "").splitlines()
    device = str((authority.get("blkio_authority") or {}).get("device_major_minor") or TARGET_DEVICE)
    checks = {
        "authority_identity_present": len(container_id) == 64,
        "cgroup_directories_present": all(path.is_dir() for path in paths.values()),
        "running_process_present": bool(pids),
        "cpuset": _read_text(paths["cpuset"] / "cpuset.cpus") == str(row["database_cpuset"]),
        "numa": _read_text(paths["cpuset"] / "cpuset.mems") == str(row["database_cpuset_mems"]),
        "effective_process_affinity": bool(pids) and all(
            value is not None and _cpus(value).issubset(expected_db_cpus)
            for value in process_affinities.values()
        ),
        "cpu_quota": quota_cores is not None and abs(
            quota_cores - float(row["cpu_quota_cores"])
        ) < 1e-9,
        "memory": _read_int(paths["memory"] / "memory.limit_in_bytes") == int(row["memory_bytes"]),
        "swappiness_zero": _read_int(paths["memory"] / "memory.swappiness")
        == int(row["memory_swappiness"]) == 0,
        "pids": _read_int(paths["pids"] / "pids.max") == int(row["pids_max"]),
        "read_bps": f"{device} {int(row['io_read_bps'])}" in read_lines,
        "write_bps": f"{device} {int(row['io_write_bps'])}" in write_lines,
        "prepared_swap_policy": authority.get("effective_swap_policy")
        == "anonymous_swap_disabled_swappiness_0",
    }
    errors = [name for name, passed in checks.items() if not passed]
    return {
        "worker_id": row["worker_id"],
        "container": row["container"],
        "container_id": container_id,
        "passed": not errors,
        "errors": errors,
        "checks": checks,
        "pids": pids,
        "process_affinities": process_affinities,
        "database_cpuset": row["database_cpuset"],
        "client_cpuset": row["client_cpuset"],
        "database_cpuset_mems": row["database_cpuset_mems"],
        "cpu_quota_cores": quota_cores,
        "memory_bytes": _read_int(paths["memory"] / "memory.limit_in_bytes"),
        "memory_swappiness": _read_int(paths["memory"] / "memory.swappiness"),
        "pids_max": _read_int(paths["pids"] / "pids.max"),
        "device_major_minor": device,
        "read_throttle": read_lines,
        "write_throttle": write_lines,
    }


def target_local_resource_proof(target_cpus: set[int]) -> dict[str, Any]:
    selected, scope = _selected_workers(target_cpus)
    rows = [_target_worker_readback(row) for row in selected]
    expected_client_cpus = set().union(*(
        _cpus(str(row["client_cpuset"])) for row in selected
    )) if selected else set()
    controller_cpus = set(os.sched_getaffinity(0))
    controller_check = controller_cpus == expected_client_cpus
    errors: list[str] = []
    if not scope["target_cpu_scope_exact"]:
        errors.append("target_cpu_scope_does_not_match_complete_frozen_workers")
    if not controller_check:
        errors.append("scene_controller_not_bound_to_frozen_client_cpuset")
    errors.extend(
        f"target_{row['worker_id']}:{error}"
        for row in rows for error in row["errors"]
    )
    return {
        "passed": not errors,
        "errors": errors,
        "target_cpus": sorted(target_cpus),
        "selected_workers": [str(row["worker_id"]) for row in selected],
        "selected_union": scope["selected_union"],
        "controller_affinity": sorted(controller_cpus),
        "expected_controller_affinity": sorted(expected_client_cpus),
        "controller_affinity_exact": controller_check,
        "workers": rows,
        "freeze_path": str(FREEZE),
        "freeze_sha256": _sha(FREEZE),
        "prepared_binding_path": str(PREPARED),
        "prepared_binding_sha256": _sha(PREPARED),
        "effective_swap_policy": "anonymous_swap_disabled_swappiness_0",
        "host_swap_state_is_audited_not_mutated": True,
    }


def _status(proc: Path) -> dict[str, str]:
    text = _read_text(proc / "status") or ""
    result: dict[str, str] = {}
    for line in text.splitlines():
        key, separator, value = line.partition(":")
        if separator:
            result[key] = value.strip()
    return result


def _command(proc: Path) -> tuple[str, str]:
    comm = _read_text(proc / "comm") or ""
    try:
        raw = (proc / "cmdline").read_bytes()
        cmdline = raw.replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
    except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
        cmdline = ""
    return comm, cmdline


def _stat_ticks(proc: Path) -> int | None:
    text = _read_text(proc / "stat")
    if not text or ") " not in text:
        return None
    fields = text[text.rfind(") ") + 2:].split()
    try:
        return int(fields[11]) + int(fields[12])
    except (IndexError, ValueError):
        return None


def _stat_processor(proc: Path) -> int | None:
    text = _read_text(proc / "stat")
    if not text or ") " not in text:
        return None
    fields = text[text.rfind(") ") + 2:].split()
    try:
        return int(fields[36])
    except (IndexError, ValueError):
        return None


def _io_bytes(proc: Path) -> int | None:
    text = _read_text(proc / "io")
    if text is None:
        return None
    values: dict[str, int] = {}
    try:
        for line in text.splitlines():
            key, separator, raw = line.partition(":")
            if separator and key in {"read_bytes", "write_bytes"}:
                values[key] = int(raw.strip())
    except ValueError:
        return None
    if set(values) != {"read_bytes", "write_bytes"}:
        return None
    return values["read_bytes"] + values["write_bytes"]


def _open_devices(proc: Path) -> set[str]:
    result: set[str] = set()
    try:
        entries = list((proc / "fd").iterdir())
    except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
        return result
    for entry in entries:
        try:
            device = entry.stat().st_dev
            result.add(f"{os.major(device)}:{os.minor(device)}")
        except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
            continue
    return result


def _mount_devices(proc: Path) -> set[str]:
    text = _read_text(proc / "mountinfo") or ""
    return {
        fields[2] for line in text.splitlines()
        if len(fields := line.split()) >= 6
    }


def _same_process_tree() -> set[int]:
    result: set[int] = set()
    pid = os.getpid()
    while pid > 0 and pid not in result:
        result.add(pid)
        status = _status(Path("/proc") / str(pid))
        try:
            pid = int(status.get("PPid") or 0)
        except ValueError:
            break
    return result


def _all_frozen_container_ids() -> dict[str, str]:
    prepared = _load(PREPARED)
    return {
        str(row["container_id"]): str(row["worker_id"])
        for row in prepared.get("workers") or []
        if row.get("container_id") and row.get("worker_id")
    }


def _inventory(target_cpus: set[int], target_worker_ids: set[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], set[int]]:
    own_tree = _same_process_tree()
    frozen_ids = _all_frozen_container_ids()
    relevant: list[dict[str, Any]] = []
    d_state_rows: list[dict[str, Any]] = []
    ticks: dict[int, int] = {}
    io_bytes: dict[int, int] = {}
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        pid = int(proc.name)
        status = _status(proc)
        if not status:
            continue
        state = (status.get("State") or "?")[:1]
        comm = _read_text(proc / "comm") or ""
        known_command = comm in DATABASE_COMMS | CLIENT_COMMS
        if known_command or comm in COMMAND_INSPECTION_COMMS:
            _, cmdline = _command(proc)
        else:
            cmdline = ""
        relevant_command = known_command or any(
            marker in cmdline.lower() for marker in COMMAND_MARKERS
        )
        if not relevant_command and state != "D":
            continue
        allowed = _cpus(status.get("Cpus_allowed_list") or "")
        overlap = sorted(allowed & target_cpus)
        cgroup = _read_text(proc / "cgroup") or ""
        frozen_worker = next(
            (worker for container_id, worker in frozen_ids.items() if container_id in cgroup),
            None,
        )
        devices = _mount_devices(proc) if relevant_command or state == "D" else set()
        open_devices = _open_devices(proc) if relevant_command or state == "D" else set()
        processor = _stat_processor(proc)
        row = {
            "pid": pid,
            "ppid": int(status.get("PPid") or 0),
            "state": state,
            "comm": comm,
            "command_class": (
                "database" if comm in DATABASE_COMMS
                else "database_client_or_benchmark" if relevant_command
                else "other"
            ),
            "command_sha256": hashlib.sha256(cmdline.encode("utf-8")).hexdigest(),
            "cpus_allowed_list": status.get("Cpus_allowed_list"),
            "mems_allowed_list": status.get("Mems_allowed_list"),
            "target_cpu_overlap": overlap,
            "sampled_processor": processor,
            "sampled_processor_on_target": processor in target_cpus,
            "target_device_mounted": TARGET_DEVICE in devices,
            "target_device_open": TARGET_DEVICE in open_devices,
            "frozen_mysql_worker": frozen_worker,
            "same_gate_process_tree": pid in own_tree,
        }
        if state == "D" and pid not in own_tree and frozen_worker not in target_worker_ids:
            d_state_rows.append(row)
        if not relevant_command or pid in own_tree or frozen_worker in target_worker_ids:
            continue
        tick = _stat_ticks(proc)
        if tick is not None:
            ticks[pid] = tick
        io_value = _io_bytes(proc)
        if io_value is not None:
            io_bytes[pid] = io_value
        row["cpu_disjoint"] = not overlap
        row["storage_disjoint"] = (
            frozen_worker is not None or TARGET_DEVICE not in open_devices
        )
        row["allowed_disjoint_frozen_mysql_sibling"] = (
            frozen_worker is not None and not overlap
        )
        relevant.append(row)
    time.sleep(0.25)
    for row in relevant:
        proc = Path("/proc") / str(row["pid"])
        process_exited = not proc.exists()
        after = _stat_ticks(proc)
        before = ticks.get(int(row["pid"]))
        row["cpu_ticks_250ms"] = None if before is None or after is None else max(0, after - before)
        after_io = _io_bytes(proc)
        before_io = io_bytes.get(int(row["pid"]))
        row["io_bytes_250ms"] = (
            None if before_io is None or after_io is None
            else max(0, after_io - before_io)
        )
        # Short-lived clients can legitimately finish during the activity
        # sample.  Treating their missing second /proc read as unknown active
        # work creates a permanent false blocker when a controller repeatedly
        # launches small psql commands.  PID disappearance is stronger evidence
        # than a zero delta: that sampled process can no longer interfere.
        row["process_exited_during_sample"] = process_exited
        io_proved_quiet = row.get("io_bytes_250ms") == 0 or (
            row.get("io_bytes_250ms") is None
            and not row.get("target_device_open")
        )
        row["dynamically_idle"] = (
            process_exited
            or (
                row.get("cpu_ticks_250ms") is not None
                and int(row["cpu_ticks_250ms"]) <= 2
                and (
                    row.get("state") == "S"
                    or not row.get("sampled_processor_on_target")
                )
                and io_proved_quiet
            )
        )
    return relevant, d_state_rows, own_tree


def _host_memory() -> tuple[int | None, float | None, dict[str, int]]:
    values: dict[str, int] = {}
    for line in (_read_text(Path("/proc/meminfo")) or "").splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[0].rstrip(":") in {"MemAvailable", "SwapTotal", "SwapFree"}:
            values[fields[0].rstrip(":")] = int(fields[1]) * 1024
    psi: float | None = None
    for line in (_read_text(Path("/proc/pressure/memory")) or "").splitlines():
        if line.startswith("full "):
            for token in line.split():
                if token.startswith("avg10="):
                    psi = float(token.split("=", 1)[1])
                    break
    return values.get("MemAvailable"), psi, values


def rolling_quiet_gate(
    *, run_id: str, required_seconds: float, maximum_wait_seconds: float,
    maximum_load1: float = 512.0, interval_seconds: float = 1.0,
    target_cpus: set[int] | None = None,
) -> dict[str, Any]:
    """Require a continuous clean window from kernel-authoritative state."""

    del maximum_load1  # Global load is diagnostic; direct placement is authoritative.
    scoped = {int(value) for value in target_cpus or set()}
    if not scoped:
        raise ValueError("actual target CPU scope is required")
    if required_seconds < 15.0 or maximum_wait_seconds < required_seconds:
        raise ValueError("invalid dynamic non-interference bounds")
    started = time.monotonic()
    clean_since: float | None = None
    samples: list[dict[str, Any]] = []
    d_streaks: dict[int, int] = {}
    terminal_errors: list[str] = []
    while True:
        target = target_local_resource_proof(scoped)
        target_workers = set(target["selected_workers"])
        workloads, d_rows, _ = _inventory(scoped, target_workers)
        blocking_workloads = [
            row for row in workloads
            if not row.get("allowed_disjoint_frozen_mysql_sibling")
            and not row.get("dynamically_idle")
            and (not row.get("cpu_disjoint") or not row.get("storage_disjoint"))
        ]
        current_d = {
            int(row["pid"]) for row in d_rows
            if row.get("sampled_processor_on_target") or row.get("target_device_open")
        }
        d_streaks = {pid: d_streaks.get(pid, 0) + 1 for pid in current_d}
        persistent_d = sorted(
            pid for pid, count in d_streaks.items()
            if count >= PERSISTENT_D_STATE_SAMPLES
        )
        available, psi, memory = _host_memory()
        errors: list[str] = []
        if target.get("passed") is not True:
            errors.append("target_mysql_local_resource_isolation_unproven")
        if blocking_workloads:
            errors.append("external_database_or_benchmark_isolation_unproven")
        if persistent_d:
            errors.append("persistent_target_overlapping_uninterruptible_sleep")
        if available is None or available < MINIMUM_HOST_MEM_AVAILABLE_BYTES:
            errors.append("dynamic_host_memory_headroom_below_minimum")
        if psi is None or psi > MAXIMUM_MEMORY_FULL_PSI_AVG10:
            errors.append("host_memory_full_psi_exceeds_dynamic_maximum")
        now = time.monotonic()
        if errors:
            clean_since = None
        elif clean_since is None:
            clean_since = now
        clean_elapsed = 0.0 if clean_since is None else now - clean_since
        samples.append({
            "captured_at": _now(),
            "errors": errors,
            "target_local_resource_proof": target,
            "external_workloads": workloads,
            "blocking_external_workloads": blocking_workloads,
            "d_state_inventory": d_rows,
            "persistent_blocking_d_state_pids": persistent_d,
            "host_memory": memory,
            "host_memory_full_psi_avg10": psi,
            "host_load1": os.getloadavg()[0],
            "load1_diagnostic_only": True,
            "global_memory_decline_diagnostic_only": True,
            "clean_elapsed_seconds": clean_elapsed,
        })
        if not errors and clean_elapsed >= required_seconds:
            break
        if now - started >= maximum_wait_seconds:
            terminal_errors = ["dynamic_noninterference_window_timeout"]
            if errors:
                terminal_errors.extend(errors)
            break
        time.sleep(max(0.2, interval_seconds))
    passed = not terminal_errors and samples[-1]["clean_elapsed_seconds"] >= required_seconds
    return {
        "schema_version": "mysql_cross_project_wave_gate_v27",
        "release_version": "v110-complete-lifecycle-recovery",
        "status": "PASS" if passed else "BLOCKED_FAIL_CLOSED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "run_id": run_id,
        "target_cpus": sorted(scoped),
        "required_clean_seconds": required_seconds,
        "maximum_wait_seconds": maximum_wait_seconds,
        "observed_seconds": time.monotonic() - started,
        "minimum_host_mem_available_bytes": MINIMUM_HOST_MEM_AVAILABLE_BYTES,
        "maximum_memory_full_psi_avg10": MAXIMUM_MEMORY_FULL_PSI_AVG10,
        "persistent_d_state_required_consecutive_samples": PERSISTENT_D_STATE_SAMPLES,
        "docker_api_used": False,
        "kernel_proc_and_cgroup_readback_authoritative": True,
        "external_process_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
        "cross_scene_completion_dependency": False,
        "shared_nvme_mutual_exclusion": False,
        "numa_exclusive_scheduler_lock": False,
        "whole_round_resource_lease": False,
        "host_swap_policy": "audit_only_target_anonymous_swap_disabled",
        "errors": terminal_errors,
        "samples": samples,
        "completed_at": _now(),
    }
