#!/usr/bin/env python3
"""Fair, fail-closed workload-wave coordination for co-resident PG/MySQL runs.

The PostgreSQL campaign already serializes every cache-eviction/physical-workload
wave through ``/tmp/resource_adaptive_pg_ssd_measurement_v1.lock``.  MySQL uses
the same lock exclusively for one formal candidate batch at a time.  It releases
the lease between batches so a waiting PostgreSQL lane cannot be starved for the
entire MySQL outer round.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import time
from typing import Any, Callable, Iterable


ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = Path("/tmp/resource_adaptive_pg_ssd_measurement_v1.lock")
CONTRACT_PATH = (
    ROOT
    / "config/formal/frozen_mysql_cross_project_wave_coordination_20260815_v51"
    / "coordination_contract.json"
)
RESOURCE_FREEZE_PATH = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260813_v18"
    / "four_scene_effective_resource_successor.json"
)

CONTROLLER_PATTERNS = (
    re.compile(r"pg_algorithm_c_lane_entry\.py"),
    re.compile(r"run_pg_algorithm_c_calibrate_then_scene\.py"),
    re.compile(r"run_pg_algorithm_[bc]_scene\.py"),
    re.compile(r"run_pg_algorithm_c_cohort_support_supervisor\.py"),
)
WORKLOAD_PATTERNS = (
    re.compile(r"stmt_postgres\.toml"),
    re.compile(r"\bpsql\s+postgresql://"),
    re.compile(r"\bpsql\b.*(?:\s-c\s|\s-f\s)"),
    re.compile(r"\bpgbench\b"),
    re.compile(r"\bbenchbase\.jar\b"),
    re.compile(
        r"^postgres: .*\b(?:SELECT|COPY|INSERT|UPDATE|DELETE|EXPLAIN|CREATE DATABASE)\b"
    ),
)


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cpus(value: str) -> set[int]:
    result: set[int] = set()
    for field in value.split(","):
        start, separator, end = field.strip().partition("-")
        if not start:
            continue
        result.update(range(int(start), int(end) + 1) if separator else [int(start)])
    return result


def command_kind(command: str) -> str | None:
    if any(pattern.search(command) for pattern in WORKLOAD_PATTERNS):
        return "workload"
    if any(pattern.search(command) for pattern in CONTROLLER_PATTERNS):
        return "controller"
    return None


def external_process_inventory(
    run_id: str, target_mems: set[int], *, ignored_pids: set[int] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Inventory external PG controllers and physical workloads separately."""

    result: dict[str, list[dict[str, Any]]] = {
        "controllers": [], "workloads": [],
    }
    ignored = set(ignored_pids or ())
    if ignored_pids is None and "_async_outer_" in run_id:
        ignored = same_run_process_pids(run_id, include_descendants=True)
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        if int(entry.name) in ignored:
            continue
        try:
            command = (
                (entry / "cmdline").read_bytes().replace(b"\0", b" ")
                .decode("utf-8", errors="replace").strip()
            )
            kind = command_kind(command)
            if not command or run_id in command or kind is None:
                continue
            status = (entry / "status").read_text(
                encoding="utf-8", errors="replace",
            )
            mems_match = re.search(
                r"^Mems_allowed_list:\s*(\S+)$", status, re.MULTILINE,
            )
            cpus_match = re.search(
                r"^Cpus_allowed_list:\s*(\S+)$", status, re.MULTILINE,
            )
            ppid_match = re.search(r"^PPid:\s*(\d+)$", status, re.MULTILINE)
            mems_text = mems_match.group(1) if mems_match else ""
            overlap = target_mems & cpus(mems_text)
            if not overlap:
                continue
            row = {
                "pid": int(entry.name),
                "ppid": int(ppid_match.group(1)) if ppid_match else None,
                "command": command,
                "command_sha256": hashlib.sha256(command.encode()).hexdigest(),
                "cpus_allowed_list": cpus_match.group(1) if cpus_match else "",
                "mems_allowed_list": mems_text,
                "overlap_mems": sorted(overlap),
                "kind": kind,
            }
            result[f"{kind}s"].append(row)
        except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
            continue
    for rows in result.values():
        rows.sort(key=lambda row: int(row["pid"]))
    return result


def sanitized_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {key: value for key, value in row.items() if key != "command"}
        for row in rows
    ]


def same_run_process_pids(
    run_id: str, *, include_descendants: bool = False,
) -> set[int]:
    """Return only processes content-bound to this unique formal run id."""

    result: set[int] = set()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            command = (
                (entry / "cmdline").read_bytes().replace(b"\0", b" ")
                .decode("utf-8", errors="replace")
            )
            if run_id in command:
                result.add(int(entry.name))
        except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
            continue
    if not include_descendants or not result:
        return result
    parents: dict[int, int] = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            status = (entry / "status").read_text(
                encoding="utf-8", errors="replace",
            )
            match = re.search(r"^PPid:\s*(\d+)$", status, re.MULTILINE)
            if match is not None:
                parents[int(entry.name)] = int(match.group(1))
        except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
            continue
    changed = True
    while changed:
        descendants = {
            pid for pid, parent in parents.items()
            if parent in result and pid not in result
        }
        changed = bool(descendants)
        result.update(descendants)
    return result


def _meminfo() -> dict[str, int]:
    values: dict[str, int] = {}
    for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
        key, separator, remainder = line.partition(":")
        if not separator:
            continue
        fields = remainder.strip().split()
        if fields and fields[0].isdigit():
            values[key] = int(fields[0]) * 1024
    return values


def _memory_full_psi_avg10() -> float:
    for line in Path("/proc/pressure/memory").read_text(encoding="ascii").splitlines():
        if line.startswith("full "):
            match = re.search(r"\bavg10=([0-9.]+)", line)
            return float(match.group(1)) if match else float("inf")
    return float("inf")


def _target_cpu_union() -> set[int]:
    freeze = json.loads(RESOURCE_FREEZE_PATH.read_text(encoding="utf-8"))
    result: set[int] = set()
    for worker in freeze["workers"]:
        result.update(cpus(str(worker["database_cpuset"])))
        result.update(cpus(str(worker["client_cpuset"])))
    return result


def _live_frozen_pg_container_readback(policy: dict[str, Any]) -> dict[str, Any]:
    rows = []
    errors = []
    for expected in policy["containers"]:
        completed = subprocess.run(
            ["docker", "inspect", expected["name"]],
            capture_output=True, text=True, check=False, timeout=15,
        )
        if completed.returncode != 0:
            errors.append(f"container_inspect_failed:{expected['name']}")
            continue
        values = json.loads(completed.stdout)
        if not isinstance(values, list) or len(values) != 1:
            errors.append(f"container_inspect_cardinality:{expected['name']}")
            continue
        value = values[0]
        host = value.get("HostConfig") or {}
        mounts = value.get("Mounts") or []
        sources = sorted(str(row.get("Source") or "") for row in mounts)
        actual = {
            "name": expected["name"],
            "container_id": value.get("Id"),
            "running": (value.get("State") or {}).get("Running"),
            "cpuset_cpus": str(host.get("CpusetCpus") or ""),
            "cpuset_mems": str(host.get("CpusetMems") or ""),
            "memory_bytes": int(host.get("Memory") or 0),
            "memory_swap_bytes": int(host.get("MemorySwap") or 0),
            "pids_limit": host.get("PidsLimit"),
            "mount_sources": sources,
        }
        rows.append(actual)
        expected_mount = str(expected["disk1_mount_source"])
        if (
            actual["container_id"] != expected["container_id"]
            or actual["running"] is not True
            or actual["cpuset_cpus"] != expected["cpuset_cpus"]
            or actual["memory_bytes"] != int(expected["memory_bytes"])
            or expected_mount not in sources
            or any(source.startswith("/disk2/") for source in sources)
        ):
            errors.append(f"container_isolation_readback_differs:{expected['name']}")
    return {"passed": not errors, "errors": errors, "containers": rows}


def evaluate_co_resident_pg_isolation(workloads: list[dict[str, Any]]) -> dict[str, Any]:
    """Allow only the frozen disk1/CPU-disjoint PG cohort with live headroom."""

    if not workloads:
        return {
            "status": "PASS_NO_EXTERNAL_WORKLOAD",
            "passed": True,
            "errors": [],
            "workload_count": 0,
        }
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    policy = contract.get("co_resident_isolated_pg_allowance") or {}
    target_cpus = _target_cpu_union()
    allowed_cpus = cpus(str(policy.get("allowed_external_cpu_union") or ""))
    allowed_ports = {str(value) for value in policy.get("allowed_postgres_ports", [])}
    errors: list[str] = []
    workload_rows = []
    total_rss_bytes = 0
    client_rss_bytes = 0
    for row in workloads:
        pid = int(row["pid"])
        row_cpus = cpus(str(row.get("cpus_allowed_list") or ""))
        command = str(row.get("command") or "")
        cpu_overlap = sorted(row_cpus & target_cpus)
        command_is_disk1_client = (
            "/disk1/resource_adaptive/" in command and "/disk2/" not in command
        )
        command_is_frozen_backend = bool(
            re.match(r"^postgres: .+\b(?:SELECT|COPY|INSERT|UPDATE|DELETE|EXPLAIN|CREATE DATABASE)\b", command)
        )
        command_is_frozen_psql = bool(
            re.search(r"\bpsql\b", command)
            and any(
                re.search(
                    rf"(?:^|\s)(?:-p\s+|--port(?:=|\s+)){re.escape(port)}(?:\s|$)",
                    command,
                )
                or re.search(
                    rf"postgresql://[^\s]+@[^\s/:]+:{re.escape(port)}(?:/|\s|$)",
                    command,
                )
                for port in allowed_ports
            )
        )
        try:
            status = Path(f"/proc/{pid}/status").read_text(
                encoding="utf-8", errors="replace",
            )
            rss_match = re.search(r"^VmRSS:\s+(\d+)\s+kB$", status, re.MULTILINE)
            rss_bytes = int(rss_match.group(1)) * 1024 if rss_match else 0
        except OSError:
            rss_bytes = 0
        total_rss_bytes += rss_bytes
        if not command_is_frozen_backend:
            client_rss_bytes += rss_bytes
        row_errors = []
        if not row_cpus or not row_cpus <= allowed_cpus:
            row_errors.append("external_cpu_not_in_frozen_union")
        if cpu_overlap:
            row_errors.append("external_cpu_overlaps_mysql")
        if not (
            command_is_disk1_client
            or command_is_frozen_backend
            or command_is_frozen_psql
        ):
            row_errors.append("external_command_not_in_frozen_pg_cohort")
        errors.extend(f"pid_{pid}:{value}" for value in row_errors)
        workload_rows.append({
            "pid": pid,
            "cpus_allowed_list": row.get("cpus_allowed_list"),
            "overlap_target_cpus": cpu_overlap,
            "rss_bytes": rss_bytes,
            "command_sha256": row.get("command_sha256"),
            "errors": row_errors,
        })
    meminfo = _meminfo()
    available = int(meminfo.get("MemAvailable", 0))
    swap_total = int(meminfo.get("SwapTotal", -1))
    memory_full_psi = _memory_full_psi_avg10()
    if len(workloads) > int(policy.get("maximum_external_workload_processes", 0)):
        errors.append("external_workload_process_count_exceeds_frozen_limit")
    if client_rss_bytes > int(policy.get("maximum_external_client_rss_bytes", 0)):
        errors.append("external_client_rss_exceeds_frozen_limit")
    if available < int(policy.get("minimum_host_mem_available_bytes", 0)):
        errors.append("host_memory_headroom_below_frozen_minimum")
    if swap_total != 0:
        errors.append("host_swap_must_remain_disabled")
    if memory_full_psi > float(policy.get("maximum_memory_full_psi_avg10", 0)):
        errors.append("host_memory_full_psi_exceeds_frozen_maximum")
    container_readback = _live_frozen_pg_container_readback(policy)
    errors.extend(container_readback["errors"])
    return {
        "status": "PASS_ISOLATED_CO_RESIDENT_PG" if not errors else "BLOCKED_FAIL_CLOSED",
        "passed": not errors,
        "errors": errors,
        "workload_count": len(workloads),
        "workload_rss_bytes": total_rss_bytes,
        "external_client_rss_bytes": client_rss_bytes,
        "minimum_host_mem_available_bytes": policy.get("minimum_host_mem_available_bytes"),
        "host_mem_available_bytes": available,
        "host_swap_total_bytes": swap_total,
        "host_memory_full_psi_avg10": memory_full_psi,
        "target_cpu_union": sorted(target_cpus),
        "allowed_external_cpu_union": sorted(allowed_cpus),
        "workloads": workload_rows,
        "container_readback": container_readback,
    }


@dataclass
class ExclusiveLease:
    stream: Any
    acquired_at_monotonic: float
    acquired_at: str
    wait_seconds: float

    @property
    def holder_pid(self) -> int:
        return os.getpid()

    @property
    def inode(self) -> int:
        return int(os.fstat(self.stream.fileno()).st_ino)


def acquire_exclusive(
    *, timeout_seconds: float, poll_seconds: float = 0.1,
    cancel_requested: Callable[[], bool] | None = None,
    lock_path: Path = LOCK_PATH,
) -> ExclusiveLease:
    if timeout_seconds <= 0:
        raise ValueError("positive lease acquisition timeout required")
    started = time.monotonic()
    stream = lock_path.open("a+", encoding="utf-8")
    try:
        while True:
            if cancel_requested is not None and cancel_requested():
                raise RuntimeError("cross-project workload lease acquisition cancelled")
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = time.monotonic()
                return ExclusiveLease(
                    stream=stream,
                    acquired_at_monotonic=acquired,
                    acquired_at=now(),
                    wait_seconds=acquired - started,
                )
            except BlockingIOError:
                elapsed = time.monotonic() - started
                if elapsed >= timeout_seconds:
                    raise TimeoutError(
                        "cross-project workload lease timed out after "
                        f"{timeout_seconds:.3f}s"
                    )
                time.sleep(min(poll_seconds, max(0.0, timeout_seconds - elapsed)))
    except BaseException:
        stream.close()
        raise


def try_acquire_exclusive(lock_path: Path = LOCK_PATH) -> ExclusiveLease | None:
    stream = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        stream.close()
        return None
    acquired = time.monotonic()
    return ExclusiveLease(
        stream=stream,
        acquired_at_monotonic=acquired,
        acquired_at=now(),
        wait_seconds=0.0,
    )


def acquire_exclusive_blocking(
    *, timeout_seconds: float, lock_path: Path = LOCK_PATH,
) -> ExclusiveLease:
    """Join the kernel's blocking flock wait queue with a bounded deadline.

    This is used only by the prelaunch orchestrator.  Formal batch execution
    retains cancellable non-blocking polling.  A blocking waiter avoids
    starvation when several PostgreSQL controllers poll the same lock every
    100 ms.
    """

    if timeout_seconds <= 0:
        raise ValueError("positive blocking lease acquisition timeout required")
    if __import__("threading").current_thread() is not __import__("threading").main_thread():
        raise RuntimeError("blocking timed flock requires the main thread")
    started = time.monotonic()
    stream = lock_path.open("a+", encoding="utf-8")
    previous_handler = signal.getsignal(signal.SIGALRM)

    def deadline(_signum: int, _frame: Any) -> None:
        raise TimeoutError(
            "blocking cross-project workload lease timed out after "
            f"{timeout_seconds:.3f}s"
        )

    try:
        signal.signal(signal.SIGALRM, deadline)
        signal.setitimer(signal.ITIMER_REAL, timeout_seconds)
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        acquired = time.monotonic()
        return ExclusiveLease(
            stream=stream,
            acquired_at_monotonic=acquired,
            acquired_at=now(),
            wait_seconds=acquired - started,
        )
    except BaseException:
        stream.close()
        raise
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        signal.signal(signal.SIGALRM, previous_handler)


def acquire_shared_blocking(
    *, timeout_seconds: float, lock_path: Path = LOCK_PATH,
) -> ExclusiveLease:
    """Acquire the cross-project lease in MySQL-campaign shared mode.

    Independent MySQL scene controllers may hold this read lease together,
    while PostgreSQL and legacy MySQL waves continue to take the write lease.
    Consequently, scene progress is no longer serialized inside one MySQL
    campaign, but a co-resident cross-project physical wave still cannot enter
    the protected execution domain.
    """

    if timeout_seconds <= 0:
        raise ValueError("positive shared lease acquisition timeout required")
    if __import__("threading").current_thread() is not __import__("threading").main_thread():
        raise RuntimeError("blocking timed flock requires the main thread")
    started = time.monotonic()
    stream = lock_path.open("a+", encoding="utf-8")
    previous_handler = signal.getsignal(signal.SIGALRM)

    def deadline(_signum: int, _frame: Any) -> None:
        raise TimeoutError(
            "shared cross-project workload lease timed out after "
            f"{timeout_seconds:.3f}s"
        )

    try:
        signal.signal(signal.SIGALRM, deadline)
        signal.setitimer(signal.ITIMER_REAL, timeout_seconds)
        fcntl.flock(stream.fileno(), fcntl.LOCK_SH)
        acquired = time.monotonic()
        return ExclusiveLease(
            stream=stream,
            acquired_at_monotonic=acquired,
            acquired_at=now(),
            wait_seconds=acquired - started,
        )
    except BaseException:
        stream.close()
        raise
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        signal.signal(signal.SIGALRM, previous_handler)


def release(lease: ExclusiveLease | None) -> float:
    if lease is None:
        return 0.0
    held = time.monotonic() - lease.acquired_at_monotonic
    try:
        fcntl.flock(lease.stream.fileno(), fcntl.LOCK_UN)
    finally:
        lease.stream.close()
    return held


def lock_held_exclusively_by(pid: int, lock_path: Path = LOCK_PATH) -> bool:
    """Verify the live advisory lock using the kernel lock table."""

    try:
        inode = int(lock_path.stat().st_ino)
        lines = Path("/proc/locks").read_text(encoding="ascii").splitlines()
    except OSError:
        return False
    for line in lines:
        fields = line.split()
        if len(fields) < 6:
            continue
        try:
            holder = int(fields[4])
            locked_inode = int(fields[5].rsplit(":", 1)[-1])
        except (ValueError, IndexError):
            continue
        if fields[1] == "FLOCK" and fields[3] == "WRITE" and holder == pid and locked_inode == inode:
            return True
    return False


def rolling_quiet_gate(
    *, run_id: str, required_seconds: float, maximum_wait_seconds: float,
    maximum_load1: float = 16.0, interval_seconds: float = 1.0,
) -> dict[str, Any]:
    """Require a continuous clean host window while the exclusive lease is held."""

    if required_seconds < 15 or maximum_wait_seconds < required_seconds:
        raise ValueError("invalid coordinated rolling-window bounds")
    from scripts.preflight_mysql_four_scene_live_v8 import host_pressure

    started = time.monotonic()
    clean_since: float | None = None
    samples: list[dict[str, Any]] = []
    terminal_errors: list[str] = []
    d_state_streaks: dict[int, int] = {}
    persistent_d_state_required_consecutive_samples = 3
    while True:
        inventory = external_process_inventory(run_id, {0})
        workloads = sanitized_rows(inventory["workloads"])
        workload_isolation = evaluate_co_resident_pg_isolation(
            inventory["workloads"]
        )
        pressure = host_pressure()
        # This gate proves *external* non-interference.  A controller or
        # watchdog that is content-bound to the same unique run id is not an
        # external workload.  Keep any such D-state fully visible in the
        # evidence, but do not let the experiment's own observer reset the
        # rolling quiet clock.
        same_run_pids = same_run_process_pids(run_id)
        raw_blocking_d_state_pids = {
            int(pid) for pid in pressure["blocking_d_state_pids"]
        }
        same_run_d_state_pids = sorted(
            raw_blocking_d_state_pids & same_run_pids
        )
        pressure["raw_blocking_d_state_pids"] = sorted(
            raw_blocking_d_state_pids
        )
        pressure["same_run_exempted_d_state_pids"] = same_run_d_state_pids
        pressure["blocking_d_state_pids"] = sorted(
            raw_blocking_d_state_pids - same_run_pids
        )
        errors: list[str] = []
        if workloads and workload_isolation["passed"] is not True:
            errors.append("external_database_physical_workload_present_while_exclusive")
            terminal_errors = errors
        current_d_state_pids = {
            int(pid) for pid in pressure["blocking_d_state_pids"]
        }
        d_state_streaks = {
            pid: d_state_streaks.get(pid, 0) + 1
            for pid in current_d_state_pids
        }
        persistent_d_state_pids = sorted(
            pid for pid, streak in d_state_streaks.items()
            if streak >= persistent_d_state_required_consecutive_samples
        )
        if persistent_d_state_pids:
            errors.append("host_uninterruptible_sleep_present")
        normalized_load1 = float(pressure["load1"]) / max(1, os.cpu_count() or 1)
        # Linux load1 is host-wide and decays over one minute.  It cannot
        # identify pressure on the frozen MySQL CPU/NUMA envelope and, between
        # short PG client waves, may remain high after the directly inventoried
        # isolated workload has exited.  Keep it in every sample for diagnosis,
        # but let the direct CPU-overlap/resource readback above and persistent
        # D-state checks own the formal non-interference decision.
        load1_diagnostic_only = True
        current = time.monotonic()
        if errors:
            clean_since = None
        elif clean_since is None:
            clean_since = current
        clean_elapsed = 0.0 if clean_since is None else current - clean_since
        samples.append({
            "captured_at": now(),
            "errors": errors,
            "host_pressure": pressure,
            "transient_d_state_pids": sorted(current_d_state_pids),
            "persistent_d_state_pids": persistent_d_state_pids,
            "d_state_consecutive_sample_streaks": {
                str(pid): streak for pid, streak in sorted(d_state_streaks.items())
            },
            "external_workloads": workloads,
            "co_resident_pg_isolation": workload_isolation,
            "external_controller_count": len(inventory["controllers"]),
            "normalized_load1": normalized_load1,
            "load1_diagnostic_only": load1_diagnostic_only,
            "clean_elapsed_seconds": clean_elapsed,
        })
        if terminal_errors:
            break
        if clean_elapsed >= required_seconds:
            break
        if current - started >= maximum_wait_seconds:
            terminal_errors = ["coordinated_quiet_window_timeout"]
            break
        time.sleep(max(0.2, interval_seconds))
    passed = not terminal_errors and samples[-1]["clean_elapsed_seconds"] >= required_seconds
    return {
        "schema_version": "mysql_cross_project_wave_quiet_gate_v1",
        "status": "PASS" if passed else "BLOCKED_FAIL_CLOSED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "coordination_contract_path": str(CONTRACT_PATH),
        "coordination_contract_sha256": sha256(CONTRACT_PATH),
        "lock_path": str(LOCK_PATH),
        "required_clean_seconds": required_seconds,
        "maximum_wait_seconds": maximum_wait_seconds,
        "maximum_load1": maximum_load1,
        "maximum_normalized_load1": 0.10,
        "global_load1_policy": "diagnostic_only_direct_isolation_is_authoritative",
        "persistent_d_state_required_consecutive_samples":
            persistent_d_state_required_consecutive_samples,
        "observed_seconds": time.monotonic() - started,
        "errors": terminal_errors,
        "samples": samples,
        "completed_at": now(),
    }
