#!/usr/bin/env python3
"""Conservatively continue full-grid repeat runs on isolated Docker pools.

The watcher never stops or replaces an external process.  It starts only the
pre-created PostgreSQL worker containers referenced by a run config, and only
when the projected task-owned Docker/VM occupancy remains within the configured
limit and the shared host is not under pressure.  It launches the next repeat
only after the preceding user service has exited successfully and its JSONL
proves a complete, zero-error, all-real Cartesian search.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple


STATE_LOG: Optional[Path] = None


@dataclass(frozen=True)
class Entry:
    exp_id: str
    logical_exp_ids: Tuple[str, ...]
    service: str
    config: Path
    target: str
    log: Path
    launcher_args: Tuple[str, ...]


class CapacityUnavailable(RuntimeError):
    """The shared host cannot safely admit the requested task workers yet."""


def run(command: List[str], *, timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=False, capture_output=True, text=True, timeout=timeout)


def machine_capacity_snapshot(raw_guard: Any) -> Dict[str, Any]:
    """Return a conservative, read-only host-capacity decision.

    The guard never stops containers or VMs.  It only prevents this watcher
    from launching/resuming another tuner while the shared host is occupied or
    under pressure.
    """
    guard = raw_guard if isinstance(raw_guard, dict) else {}
    if guard.get("enabled") is not True:
        return {"ok": True, "enabled": False, "reasons": []}

    prefixes = tuple(
        str(value) for value in (guard.get("experiment_container_prefixes") or [])
        if str(value)
    )
    environment_limit_scope = str(
        guard.get("environment_limit_scope") or "observed"
    ).strip().lower()
    if environment_limit_scope not in {"observed", "task_owned"}:
        raise ValueError(
            f"unsupported environment_limit_scope={environment_limit_scope!r}"
        )
    task_container_names = {
        str(value) for value in (guard.get("task_container_names") or [])
        if str(value)
    }
    task_vm_names = {
        str(value) for value in (guard.get("task_vm_names") or [])
        if str(value)
    }
    docker = run(["docker", "ps", "--format", "{{.Names}}"], timeout=90)
    if docker.returncode != 0:
        raise RuntimeError(f"machine guard docker ps failed: {(docker.stderr or docker.stdout).strip()}")
    running_containers = [line.strip() for line in docker.stdout.splitlines() if line.strip()]
    experiment_containers = sorted(
        name for name in running_containers
        if prefixes and name.startswith(prefixes)
    )
    task_containers = sorted(
        name for name in running_containers if name in task_container_names
    )
    external_experiment_containers = sorted(
        set(experiment_containers) - set(task_containers)
    )

    virsh = run(["virsh", "list", "--name", "--state-running"], timeout=30)
    if virsh.returncode != 0:
        raise RuntimeError(f"machine guard virsh failed: {(virsh.stderr or virsh.stdout).strip()}")
    running_vms = sorted(line.strip() for line in virsh.stdout.splitlines() if line.strip())

    qemu_ps = run(["ps", "-eo", "pid=,comm=,args="], timeout=60)
    if qemu_ps.returncode != 0:
        raise RuntimeError(
            f"machine guard qemu process inspection failed: "
            f"{(qemu_ps.stderr or qemu_ps.stdout).strip()}"
        )
    direct_qemu_processes: List[Dict[str, Any]] = []
    for line in qemu_ps.stdout.splitlines():
        fields = line.strip().split(None, 2)
        if len(fields) < 2 or not fields[1].startswith("qemu-system-"):
            continue
        name = ""
        args = fields[2] if len(fields) == 3 else ""
        match = re.search(r"(?:^|\s)-name\s+([^,\s]+)", args)
        if match:
            name = match.group(1)
        direct_qemu_processes.append(
            {"pid": int(fields[0]), "name": name or f"pid-{fields[0]}"}
        )

    task_running_vms = sorted(name for name in running_vms if name in task_vm_names)
    task_direct_qemu_processes = [
        row for row in direct_qemu_processes if str(row.get("name") or "") in task_vm_names
    ]
    task_vm_environment_names = set(task_running_vms)
    task_vm_environment_names.update(
        str(row.get("name") or "") for row in task_direct_qemu_processes
        if str(row.get("name") or "")
    )

    cpu_count = max(1, int(os.cpu_count() or 1))
    load_1m = float(os.getloadavg()[0])
    load_per_cpu = load_1m / cpu_count

    mem_available_bytes = 0
    with Path("/proc/meminfo").open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("MemAvailable:"):
                mem_available_bytes = int(line.split()[1]) * 1024
                break
    mem_available_gb = mem_available_bytes / (1024 ** 3)

    root_usage = shutil.disk_usage("/")
    root_used_pct = 100.0 * root_usage.used / max(1, root_usage.total)
    root_free_gb = root_usage.free / (1024 ** 3)

    observed_vm_count = max(len(running_vms), len(direct_qemu_processes))
    observed_environments = len(experiment_containers) + observed_vm_count
    active_task_vm_count = len(task_vm_environment_names)
    active_task_environments = len(task_containers) + active_task_vm_count
    active_environments = (
        active_task_environments
        if environment_limit_scope == "task_owned"
        else observed_environments
    )
    max_environments = max(0, int(guard.get("max_active_experiment_environments") or 0))
    min_memory_gb = max(0.0, float(guard.get("min_available_memory_gb") or 0.0))
    max_load_per_cpu = max(0.0, float(guard.get("max_load_per_cpu") or 0.0))
    max_root_used_pct = max(0.0, float(guard.get("max_root_disk_used_pct") or 0.0))
    min_root_free_gb = max(0.0, float(guard.get("min_root_disk_free_gb") or 0.0))

    reasons: List[str] = []
    if max_environments and active_environments > max_environments:
        reasons.append(f"active_experiment_environments={active_environments}>max={max_environments}")
    if min_memory_gb and mem_available_gb < min_memory_gb:
        reasons.append(f"available_memory_gb={mem_available_gb:.1f}<min={min_memory_gb:.1f}")
    if max_load_per_cpu and load_per_cpu > max_load_per_cpu:
        reasons.append(f"load_per_cpu={load_per_cpu:.3f}>max={max_load_per_cpu:.3f}")
    if max_root_used_pct and root_used_pct > max_root_used_pct:
        reasons.append(f"root_used_pct={root_used_pct:.1f}>max={max_root_used_pct:.1f}")
    if min_root_free_gb and root_free_gb < min_root_free_gb:
        reasons.append(f"root_free_gb={root_free_gb:.1f}<min={min_root_free_gb:.1f}")

    monitored_disks: List[Dict[str, Any]] = []
    for raw_disk in guard.get("monitored_disks") or []:
        if not isinstance(raw_disk, dict):
            continue
        path = str(raw_disk.get("path") or "").strip()
        if not path:
            continue
        expected_source = str(raw_disk.get("expected_source") or "").strip()
        max_used_pct = max(0.0, float(raw_disk.get("max_used_pct") or 0.0))
        min_free_gb = max(0.0, float(raw_disk.get("min_free_gb") or 0.0))
        try:
            usage = shutil.disk_usage(path)
        except OSError as exc:
            reasons.append(f"disk_unavailable={path}:{type(exc).__name__}")
            monitored_disks.append({"path": path, "error": f"{type(exc).__name__}:{exc}"})
            continue
        source_proc = run(["findmnt", "-n", "-o", "SOURCE", "-T", path], timeout=30)
        source = source_proc.stdout.strip() if source_proc.returncode == 0 else ""
        used_pct = 100.0 * usage.used / max(1, usage.total)
        free_gb = usage.free / (1024 ** 3)
        row = {
            "path": path,
            "source": source,
            "expected_source": expected_source,
            "used_pct": used_pct,
            "free_gb": free_gb,
            "max_used_pct": max_used_pct,
            "min_free_gb": min_free_gb,
        }
        monitored_disks.append(row)
        if expected_source and source != expected_source:
            reasons.append(f"disk_source={path}:{source or 'missing'}!=expected={expected_source}")
        if max_used_pct and used_pct > max_used_pct:
            reasons.append(f"disk_used_pct={path}:{used_pct:.1f}>max={max_used_pct:.1f}")
        if min_free_gb and free_gb < min_free_gb:
            reasons.append(f"disk_free_gb={path}:{free_gb:.1f}<min={min_free_gb:.1f}")

    return {
        "ok": not reasons,
        "enabled": True,
        "reasons": reasons,
        "environment_limit_scope": environment_limit_scope,
        "active_experiment_environments": active_environments,
        "active_task_experiment_environments": active_task_environments,
        "observed_experiment_environments": observed_environments,
        "max_active_experiment_environments": max_environments,
        "running_containers": running_containers,
        "experiment_containers": experiment_containers,
        "task_containers": task_containers,
        "external_experiment_containers": external_experiment_containers,
        "running_vms": running_vms,
        "direct_qemu_processes": direct_qemu_processes,
        "active_vm_count": observed_vm_count,
        "task_running_vms": task_running_vms,
        "task_direct_qemu_processes": task_direct_qemu_processes,
        "active_task_vm_count": active_task_vm_count,
        "cpu_count": cpu_count,
        "load_1m": load_1m,
        "load_per_cpu": load_per_cpu,
        "available_memory_gb": mem_available_gb,
        "root_used_pct": root_used_pct,
        "root_free_gb": root_free_gb,
        "monitored_disks": monitored_disks,
    }


def emit(event: str, **fields: Any) -> None:
    payload = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "event": event,
        **fields,
    }
    line = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    print(line, flush=True)
    if STATE_LOG is not None:
        try:
            STATE_LOG.parent.mkdir(parents=True, exist_ok=True)
            with STATE_LOG.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError as exc:
            print(f"state log append failed: {exc}", file=sys.stderr, flush=True)


def parse_cpu_set(value: str) -> Set[int]:
    cpus: Set[int] = set()
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo_raw, hi_raw = part.split("-", 1)
            lo = int(lo_raw)
            hi = int(hi_raw)
            if hi < lo:
                raise ValueError(f"invalid CPU range: {part}")
            cpus.update(range(lo, hi + 1))
        else:
            cpus.add(int(part))
    return cpus


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def run_suite_audit(
    config_path: Path,
    config: Dict[str, Any],
    *,
    allow_incomplete: bool = False,
) -> Dict[str, Any]:
    """Run the independent 18-physical/21-logical audit before watcher exit."""
    suite = config.get("suite_audit")
    if not isinstance(suite, dict):
        raise ValueError("suite_audit configuration is required")
    auditor_raw = str(suite.get("auditor") or "").strip()
    output_raw = str(suite.get("output") or "").strip()
    if not auditor_raw or not output_raw:
        raise ValueError("suite_audit.auditor and suite_audit.output are required")
    auditor = Path(auditor_raw).resolve()
    output = Path(output_raw).resolve()
    proc = run(
        [sys.executable, str(auditor), "--config", str(config_path), "--output", str(output)],
        timeout=3600,
    )
    allowed_returncodes = {0, 10}
    if allow_incomplete:
        allowed_returncodes.add(2)
    if proc.returncode not in allowed_returncodes:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"suite auditor failed rc={proc.returncode}: {detail}")
    payload = load_json(output)
    expected_status_by_returncode = {
        0: "verified",
        2: "incomplete",
        10: "complete_with_semantic_misses",
    }
    expected_status = expected_status_by_returncode[proc.returncode]
    if payload.get("status") != expected_status:
        raise RuntimeError(f"suite auditor status/return-code mismatch: {payload.get('status')}")
    return {
        "status": payload.get("status"),
        "output": str(output),
        "summary": payload.get("summary"),
        "requirements": payload.get("requirements"),
    }


def workers_from_config(config: Path, target: str) -> List[Dict[str, Any]]:
    cfg = load_json(config)
    tuning = cfg.get("binary_plan_tuning") or {}
    validation = tuning.get("validation") or {}
    docker_workers = validation.get("docker_workers") or {}
    workers = docker_workers.get("workers") or []
    selected = [
        item for item in workers
        if isinstance(item, dict) and target in (item.get("targets") or [])
    ]
    if not selected:
        raise RuntimeError(f"no Docker workers for target={target}: {config}")
    return selected


def worker_start_capacity_reason(
    snapshot: Dict[str, Any],
    missing_worker_count: int,
) -> Optional[str]:
    """Return why starting missing workers would exceed a hard host limit."""
    reasons = [
        str(reason) for reason in (snapshot.get("reasons") or [])
        if not str(reason).startswith("active_experiment_environments=")
    ]
    if reasons:
        return ";".join(reasons)

    active = max(0, int(snapshot.get("active_experiment_environments") or 0))
    maximum = max(0, int(snapshot.get("max_active_experiment_environments") or 0))
    projected = active + max(0, int(missing_worker_count))
    if maximum and projected > maximum:
        return (
            f"projected_active_experiment_environments={projected}>max={maximum}"
            f":active={active}:missing_workers={missing_worker_count}"
        )
    return None


def explicit_container_cpuset_conflicts(
    workers: Iterable[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Find explicit CPU pinning conflicts before starting stopped workers."""
    expected_names = {str(worker.get("container") or "") for worker in workers}
    expected_cpus: Set[int] = set()
    for worker in workers:
        configured = str(worker.get("cpuset_cpus") or "").strip()
        if not configured:
            raise RuntimeError(f"worker missing cpuset_cpus: {worker.get('container')}")
        expected_cpus.update(parse_cpu_set(configured))

    ps = run(["docker", "ps", "-q"], timeout=90)
    if ps.returncode != 0:
        raise RuntimeError(f"docker ps failed: {(ps.stderr or ps.stdout).strip()}")
    container_ids = [line.strip() for line in ps.stdout.splitlines() if line.strip()]
    if not container_ids:
        return []
    inspected = run(["docker", "inspect", *container_ids], timeout=120)
    if inspected.returncode != 0:
        raise RuntimeError(
            f"docker inspect failed: {(inspected.stderr or inspected.stdout).strip()}"
        )

    conflicts: List[Dict[str, Any]] = []
    for info in json.loads(inspected.stdout):
        name = str(info.get("Name") or "").lstrip("/")
        if not name or name in expected_names:
            continue
        cpuset = str((info.get("HostConfig") or {}).get("CpusetCpus") or "").strip()
        if not cpuset:
            continue
        overlap = sorted(parse_cpu_set(cpuset) & expected_cpus)
        if overlap:
            conflicts.append(
                {"container": name, "cpuset_cpus": cpuset, "overlap": overlap}
            )
    return sorted(conflicts, key=lambda row: str(row["container"]))


def ensure_workers_running(
    workers: List[Dict[str, Any]],
    machine_guard: Dict[str, Any],
    all_entries: Optional[List[Entry]] = None,
) -> Dict[str, Any]:
    """Start only missing run-owned workers after a live capacity recheck."""
    expected_names = sorted({str(worker.get("container") or "") for worker in workers})
    if not expected_names or "" in expected_names:
        raise RuntimeError("worker missing container name")

    snapshot = machine_capacity_snapshot(machine_guard)
    running = set(str(name) for name in (snapshot.get("running_containers") or []))
    missing = [name for name in expected_names if name not in running]
    reclaimed: List[str] = []
    if all_entries:
        snapshot, reclaimed = reclaim_idle_owned_workers(
            required_worker_names=set(expected_names),
            all_entries=all_entries,
            snapshot=snapshot,
            missing_worker_count=len(missing),
            machine_guard=machine_guard,
        )
    reason = worker_start_capacity_reason(snapshot, len(missing))
    if reason:
        raise CapacityUnavailable(reason)
    if not missing:
        return {
            "reclaimed_idle_workers": reclaimed,
            "started_workers": [],
            "capacity_before": snapshot,
        }

    check_known_cpuset_blockers(workers)
    conflicts = explicit_container_cpuset_conflicts(workers)
    if conflicts:
        raise CapacityUnavailable(f"explicit_cpuset_conflicts={conflicts}")

    inspected = run(["docker", "inspect", *missing], timeout=120)
    if inspected.returncode != 0:
        raise RuntimeError(
            f"pre-created worker container missing: {(inspected.stderr or inspected.stdout).strip()}"
        )
    start = run(["docker", "start", *missing], timeout=180)
    if start.returncode != 0:
        raise RuntimeError(f"docker start failed: {(start.stderr or start.stdout).strip()}")

    deadline = time.monotonic() + 120.0
    while True:
        state_proc = run(
            ["docker", "inspect", "--format", "{{.Name}}\t{{.State.Running}}", *missing],
            timeout=60,
        )
        if state_proc.returncode == 0:
            states = {
                line.split("\t", 1)[0].lstrip("/"): line.split("\t", 1)[1].strip()
                for line in state_proc.stdout.splitlines()
                if "\t" in line
            }
            if all(states.get(name) == "true" for name in missing):
                break
        if time.monotonic() >= deadline:
            run(["docker", "stop", "--time", "30", *missing], timeout=90)
            raise RuntimeError(f"workers did not enter running state: {missing}")
        time.sleep(2)

    capacity_after = machine_capacity_snapshot(machine_guard)
    maximum = int(capacity_after.get("max_active_experiment_environments") or 0)
    active_after = int(capacity_after.get("active_experiment_environments") or 0)
    pressure_reasons = [
        str(value) for value in (capacity_after.get("reasons") or [])
        if not str(value).startswith("active_experiment_environments=")
    ]
    if (maximum and active_after > maximum) or pressure_reasons:
        run(["docker", "stop", "--time", "30", *missing], timeout=90)
        raise CapacityUnavailable(
            f"post_start_guard_failed:active={active_after}:max={maximum}:"
            f"reasons={pressure_reasons}:rolled_back={missing}"
        )

    return {
        "reclaimed_idle_workers": reclaimed,
        "started_workers": missing,
        "capacity_before": snapshot,
        "capacity_after": capacity_after,
    }


def check_known_cpuset_blockers(workers: Iterable[Dict[str, Any]]) -> None:
    """Fail fast while explicitly recorded external cpuset owners are alive."""
    active: List[Dict[str, Any]] = []
    roots = (
        Path("/sys/fs/cgroup/cpuset/docker-limited"),
        Path("/sys/fs/cgroup/cpuset/docker"),
    )
    for worker in workers:
        blocker_ids = worker.get("blocked_cpuset_cgroup_ids") or []
        if isinstance(blocker_ids, str):
            blocker_ids = [blocker_ids]
        for blocker_id in blocker_ids:
            blocker = str(blocker_id).strip()
            if not blocker:
                continue
            for root in roots:
                tasks_path = root / blocker / "tasks"
                try:
                    pids = [line.strip() for line in tasks_path.read_text().splitlines() if line.strip()]
                except FileNotFoundError:
                    continue
                except OSError as exc:
                    raise RuntimeError(f"cannot inspect cpuset blocker {blocker}: {exc}") from exc
                if pids:
                    active.append(
                        {
                            "worker": worker.get("name"),
                            "container": worker.get("container"),
                            "cpuset_cpus": worker.get("cpuset_cpus"),
                            "blocker_cgroup": blocker,
                            "task_count": len(pids),
                        }
                    )
                break
    if active:
        raise RuntimeError(f"known external cpuset blockers are still active: {active}")


def service_state(service: str) -> Dict[str, str]:
    proc = run(
        [
            "systemctl", "--user", "show", service,
            "-p", "LoadState", "-p", "ActiveState", "-p", "SubState",
            "-p", "Result", "-p", "ExecMainStatus", "-p", "NRestarts",
        ]
    )
    if proc.returncode != 0:
        return {"LoadState": "not-found", "ActiveState": "inactive"}
    values: Dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def strict_log_complete(
    path: Path,
    expected_candidates: int,
    min_rounds: int,
    *,
    require_search_complete: bool = True,
) -> Tuple[bool, str]:
    if not path.is_file():
        return False, "log_missing"

    by_round: Dict[int, Dict[int, Dict[str, Any]]] = defaultdict(dict)
    summary_rounds: Set[int] = set()
    search_complete = False
    malformed = 0
    with path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                malformed += 1
                continue
            if not isinstance(event, dict):
                continue
            name = event.get("event")
            if name == "candidate_validation":
                try:
                    round_id = int(event.get("round"))
                    candidate_index = int(event.get("candidate_index"))
                except (TypeError, ValueError):
                    return False, "candidate_without_round_or_index"
                # A failed real replay may be retried on resume. Keep every raw
                # attempt in JSONL, but the newest attempt is authoritative for
                # structural completion of this Cartesian point.
                by_round[round_id][candidate_index] = event
            elif name == "round_summary":
                try:
                    summary_rounds.add(int(event.get("round")))
                except (TypeError, ValueError):
                    return False, "summary_without_round"
            elif name == "search_complete":
                search_complete = True

    if malformed:
        return False, f"malformed_json_lines={malformed}"
    if len(by_round) < min_rounds:
        return False, f"round_count={len(by_round)}<{min_rounds}"
    if set(by_round) != summary_rounds:
        return False, "candidate_rounds_do_not_match_round_summaries"

    for round_id, points in sorted(by_round.items()):
        events = list(points.values())
        if len(points) != expected_candidates:
            return False, (
                f"round={round_id} authoritative_candidates={len(points)} "
                f"expected={expected_candidates}"
            )
        failures = [event for event in events if event.get("status") != "ok"]
        if failures:
            return False, f"round={round_id} non_ok_validations={len(failures)}"
        nonzero_returncodes = [
            event for event in events
            if (
                isinstance(event.get("returncode"), bool)
                or not isinstance(event.get("returncode"), (int, float))
                or event.get("returncode") != 0
            )
        ]
        if nonzero_returncodes:
            return False, f"round={round_id} nonzero_returncodes={len(nonzero_returncodes)}"
        missing_backend = [event for event in events if not event.get("validation_backend")]
        if missing_backend:
            return False, f"round={round_id} missing_validation_backend={len(missing_backend)}"
        estimated_backend = [
            event for event in events
            if str(event.get("validation_backend") or "").lower()
            in {"estimator", "plan_estimator", "resource_estimator", "estimated"}
        ]
        if estimated_backend:
            return False, f"round={round_id} estimated_validation_backend={len(estimated_backend)}"
        monitor_failures = [
            event for event in events
            if (event.get("resource_stats") or {}).get("resource_monitor_ok") is not True
        ]
        if monitor_failures:
            return False, f"round={round_id} resource_monitor_not_ok={len(monitor_failures)}"
        invalid_qps = [
            event for event in events
            if (
                isinstance(event.get("db_qps"), bool)
                or not isinstance(event.get("db_qps"), (int, float))
                or float(event.get("db_qps")) <= 0.0
            )
        ]
        if invalid_qps:
            return False, f"round={round_id} invalid_db_qps={len(invalid_qps)}"
    if require_search_complete and not search_complete:
        return False, "search_complete_missing"
    return True, f"complete_rounds={len(by_round)}"


def seal_strict_fullgrid_log(
    path: Path,
    expected_candidates: int,
    min_rounds: int,
) -> Tuple[bool, str]:
    """Append an idempotent completion marker after strict runtime proof exists.

    Older full-grid tuner snapshots returned status 3 at ``max_rounds`` when
    epsilon convergence was still false.  That is a valid continuation signal
    for convergence experiments, but these matrix runs define completion as at
    least ``min_rounds`` complete Cartesian rounds.  Seal only an inactive run
    whose authoritative real-replay rows and terminal stage summary prove that
    contract; never infer completion from estimates or a partial round.
    """
    complete, reason = strict_log_complete(
        path,
        expected_candidates,
        min_rounds,
        require_search_complete=False,
    )
    if not complete:
        return False, reason

    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            value = json.loads(raw)
            if isinstance(value, dict):
                rows.append(value)
    if any(row.get("event") == "search_complete" for row in rows):
        return strict_log_complete(path, expected_candidates, min_rounds)

    stage_summaries = [row for row in rows if row.get("event") == "stage_summary"]
    if not stage_summaries:
        return False, "stage_summary_missing"
    latest_summary = stage_summaries[-1]
    try:
        completed_round_count = int(latest_summary.get("completed_round_count"))
    except (TypeError, ValueError):
        return False, "stage_summary_completed_round_count_invalid"
    if completed_round_count < min_rounds:
        return False, (
            f"stage_summary_completed_round_count={completed_round_count}<{min_rounds}"
        )
    stop_reason = str(latest_summary.get("stop_reason") or "")
    if stop_reason not in {"max_rounds_reached", "epsilon_converged", "fullgrid_round_limit_reached"}:
        return False, f"stage_summary_not_terminal:{stop_reason or 'missing'}"

    round_ids = sorted({
        int(row["round"])
        for row in rows
        if row.get("event") == "round_summary" and row.get("round") is not None
    })
    event = {
        "event": "search_complete",
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "completion_contract": "strict_real_cartesian_rounds_v1",
        "completion_reason": "fullgrid_round_limit_reached",
        "completed_round_count": len(round_ids),
        "completed_rounds": round_ids,
        "expected_candidates_per_round": expected_candidates,
        "authoritative_strict_candidate_count": len(round_ids) * expected_candidates,
        "resource_estimates_used_as_proof": False,
        "sealed_from_terminal_stage_summary": True,
        "terminal_stage_stop_reason": stop_reason,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return strict_log_complete(path, expected_candidates, min_rounds)


def docker_snapshot(expected_workers: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    expected = {str(worker.get("container")): worker for worker in expected_workers}
    if "" in expected:
        raise RuntimeError("worker missing container name")

    ps = run(
        ["docker", "ps", "--no-trunc", "--format", "{{.ID}}\t{{.Names}}"],
        timeout=60,
    )
    if ps.returncode != 0:
        raise RuntimeError(f"docker ps failed: {(ps.stderr or ps.stdout).strip()}")
    running: Dict[str, str] = {}
    for line in ps.stdout.splitlines():
        if "\t" not in line:
            continue
        container_id, name = line.split("\t", 1)
        if container_id and name:
            running[name.strip()] = container_id.strip()

    inspected: Dict[str, Dict[str, Any]] = {}
    if expected:
        proc = run(["docker", "inspect", *sorted(expected)], timeout=120)
        if proc.returncode != 0:
            raise RuntimeError(f"docker inspect failed: {(proc.stderr or proc.stdout).strip()}")
        values = json.loads(proc.stdout)
        for value in values:
            name = str(value.get("Name") or "").lstrip("/")
            if name:
                inspected[name] = value

    expected_cpus: Set[int] = set()
    worker_rows: List[Dict[str, Any]] = []
    for name, worker in expected.items():
        info = inspected.get(name)
        if not info:
            raise RuntimeError(f"expected worker container is not running: {name}")
        if not bool((info.get("State") or {}).get("Running")):
            raise RuntimeError(f"expected worker container is not in running state: {name}")
        configured_cpuset = str(worker.get("cpuset_cpus") or "")
        actual_cpuset = str((info.get("HostConfig") or {}).get("CpusetCpus") or "")
        if not configured_cpuset or actual_cpuset != configured_cpuset:
            raise RuntimeError(
                f"cpuset mismatch for {name}: config={configured_cpuset!r} actual={actual_cpuset!r}"
            )
        expected_cpus.update(parse_cpu_set(configured_cpuset))
        worker_rows.append(
            {
                "container": name,
                "cpuset_cpus": actual_cpuset,
                "host": worker.get("host"),
                "port": worker.get("port"),
            }
        )

    overlaps: List[Dict[str, Any]] = []
    inherited_cpuset_names: List[str] = []
    cpuset_root = Path("/sys/fs/cgroup/cpuset")
    inherited_cpuset: Set[int] = set()
    for parent_cpuset_path in (
        cpuset_root / "docker-limited" / "cpuset.cpus",
        cpuset_root / "docker" / "cpuset.cpus",
        cpuset_root / "cpuset.cpus",
    ):
        try:
            raw_parent_cpuset = parent_cpuset_path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if raw_parent_cpuset:
            inherited_cpuset = parse_cpu_set(raw_parent_cpuset)
            break
    for name, container_id in running.items():
        if name in expected:
            continue
        candidates = list(cpuset_root.glob(f"*/{container_id}/cpuset.cpus"))
        candidates.extend(cpuset_root.glob(f"*/docker-{container_id}.scope/cpuset.cpus"))
        cpuset = ""
        for candidate in candidates:
            try:
                cpuset = candidate.read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if cpuset:
                break
        parsed_cpuset = parse_cpu_set(cpuset)
        # Docker cgroup v1 materializes an unpinned container's inherited
        # parent cpuset (normally the whole host) in the child file.  That is
        # not evidence of an explicit, exclusive CPU assignment.  Dedicated
        # overlapping cpusets remain a hard conflict.
        if parsed_cpuset and inherited_cpuset and parsed_cpuset == inherited_cpuset:
            inherited_cpuset_names.append(name)
            continue
        if parsed_cpuset & expected_cpus:
            overlaps.append({"container": name, "cpuset_cpus": cpuset})
    if overlaps:
        raise RuntimeError(f"other running containers overlap target CPU set: {overlaps}")

    virsh = run(["virsh", "list", "--name", "--state-running"], timeout=30)
    running_vms = []
    if virsh.returncode == 0:
        running_vms = [line.strip() for line in virsh.stdout.splitlines() if line.strip()]

    return {
        "workers": sorted(worker_rows, key=lambda row: str(row["container"])),
        "other_container_cpu_overlaps": overlaps,
        "inherited_full_host_cpuset_ignored_count": len(inherited_cpuset_names),
        "running_vms_observed": running_vms,
    }


def check_database_workers(workers: Iterable[Dict[str, Any]]) -> None:
    for worker in workers:
        command = [
            "pg_isready",
            "-h", str(worker.get("host") or "127.0.0.1"),
            "-p", str(worker.get("port")),
            "-U", str(worker.get("user") or "postgres"),
            "-d", str(worker.get("dbname") or "postgres"),
            "-t", "10",
        ]
        proc = run(command, timeout=20)
        if proc.returncode != 0:
            raise RuntimeError(
                f"database worker is not ready: {worker.get('name')} "
                f"{(proc.stderr or proc.stdout).strip()}"
            )


def referenced_configs_from_processes() -> Set[Path]:
    proc = run(["ps", "-eo", "args="], timeout=90)
    if proc.returncode != 0:
        raise RuntimeError(f"process inspection failed: {(proc.stderr or proc.stdout).strip()}")
    paths: Set[Path] = set()
    for line in proc.stdout.splitlines():
        for raw in re.findall(r"/[^ ]+\.json", line):
            path = Path(raw)
            try:
                if path.is_file():
                    paths.add(path.resolve())
            except OSError:
                continue
    return paths


def reclaim_idle_owned_workers(
    *,
    required_worker_names: Set[str],
    all_entries: List[Entry],
    snapshot: Dict[str, Any],
    missing_worker_count: int,
    machine_guard: Dict[str, Any],
) -> Tuple[Dict[str, Any], List[str]]:
    """Free only idle workers owned by this experiment manifest.

    External containers are never candidates.  A run-owned worker is also
    protected whenever a live tuner argv or active user service references its
    config.  Reclamation happens only when it is required to admit the next
    worker set without exceeding the hard Docker/VM environment limit.
    """
    maximum = max(0, int(snapshot.get("max_active_experiment_environments") or 0))
    active = max(0, int(snapshot.get("active_experiment_environments") or 0))
    need_to_free = max(0, active + max(0, int(missing_worker_count)) - maximum)
    if not maximum or not need_to_free:
        return snapshot, []

    owned: Set[str] = set()
    protected = set(required_worker_names)
    active_configs = referenced_configs_from_processes()
    seen_configs: Set[Tuple[Path, str]] = set()
    for entry in all_entries:
        key = (entry.config.resolve(), entry.target)
        if key not in seen_configs:
            seen_configs.add(key)
            entry_workers = workers_from_config(entry.config, entry.target)
            owned.update(
                str(worker.get("container") or "") for worker in entry_workers
                if str(worker.get("container") or "")
            )
        else:
            entry_workers = workers_from_config(entry.config, entry.target)
        state = service_state(entry.service)
        if (
            entry.config.resolve() in active_configs
            or state.get("ActiveState") in {"active", "activating", "reloading"}
        ):
            protected.update(
                str(worker.get("container") or "") for worker in entry_workers
                if str(worker.get("container") or "")
            )

    running = set(str(name) for name in (snapshot.get("running_containers") or []))
    reclaimable = sorted((running & owned) - protected)
    selected = reclaimable[:need_to_free]
    if not selected:
        return snapshot, []

    stop = run(["docker", "stop", "--time", "30", *selected], timeout=120)
    if stop.returncode != 0:
        raise RuntimeError(
            f"failed to stop idle run-owned workers: {(stop.stderr or stop.stdout).strip()}"
        )
    refreshed = machine_capacity_snapshot(machine_guard)
    emit(
        "idle_run_owned_workers_reclaimed",
        workers=selected,
        protected_workers=sorted(protected),
        capacity_before=snapshot,
        capacity_after=refreshed,
    )
    return refreshed, selected


def active_tuner_process_count() -> int:
    """Count only live Python tuner processes, excluding tmux/supervisor argv copies."""
    proc = run(["ps", "-eo", "comm=,args="], timeout=90)
    if proc.returncode != 0:
        raise RuntimeError(f"process inspection failed: {(proc.stderr or proc.stdout).strip()}")
    count = 0
    for line in proc.stdout.splitlines():
        fields = line.strip().split(None, 1)
        if len(fields) != 2:
            continue
        comm, args = fields
        if not comm.startswith("python"):
            continue
        if "binary_plan_resource_tuner.py" not in args or " binary-search " not in args:
            continue
        count += 1
    return count


def check_no_tuner_pool_conflict(config: Path, target: str, workers: List[Dict[str, Any]]) -> None:
    expected_names = {str(worker.get("container")) for worker in workers}
    for other_config in referenced_configs_from_processes():
        if other_config == config.resolve():
            continue
        try:
            other_cfg = load_json(other_config)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        tuning = other_cfg.get("binary_plan_tuning") or {}
        validation = tuning.get("validation") or {}
        pool = (validation.get("docker_workers") or {}).get("workers") or []
        other_names = {
            str(item.get("container")) for item in pool
            if isinstance(item, dict) and item.get("container")
        }
        overlap = expected_names & other_names
        if overlap:
            raise RuntimeError(
                f"running process references conflicting config {other_config}: containers={sorted(overlap)}"
            )


def launch_entry(
    entry: Entry,
    launcher: Path,
    machine_guard: Dict[str, Any],
    all_entries: List[Entry],
) -> Dict[str, Any]:
    workers = workers_from_config(entry.config, entry.target)
    worker_start = ensure_workers_running(workers, machine_guard, all_entries)
    snapshot = docker_snapshot(workers)
    check_database_workers(workers)
    check_no_tuner_pool_conflict(entry.config, entry.target, workers)

    service = entry.service[:-8] if entry.service.endswith(".service") else entry.service
    command = [
        sys.executable,
        str(launcher),
        "--config", str(entry.config),
        "--target", entry.target,
        "--service", service,
        "--resume",
    ]
    command.extend(entry.launcher_args)
    proc = run(command, timeout=180)
    if proc.returncode != 0:
        raise RuntimeError(f"launcher failed: {(proc.stderr or proc.stdout).strip()}")
    state = service_state(entry.service)
    if state.get("ActiveState") != "active":
        raise RuntimeError(f"launched service is not active: {state}")
    return {
        "worker_start": worker_start,
        "occupancy": snapshot,
        "launcher_output": proc.stdout.strip(),
        "service_state": state,
    }


def retry_failed_validations_enabled(config: Path) -> bool:
    cfg = load_json(config)
    tuning = cfg.get("binary_plan_tuning") or {}
    search = tuning.get("binary_search") or {}
    value = search.get("retry_failed_validations_on_resume")
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def resume_existing_entry(
    entry: Entry,
    machine_guard: Dict[str, Any],
    all_entries: List[Entry],
) -> Dict[str, Any]:
    """Restart a stopped run-owned unit whose partial log can be resumed safely."""
    workers = workers_from_config(entry.config, entry.target)
    worker_start = ensure_workers_running(workers, machine_guard, all_entries)
    snapshot = docker_snapshot(workers)
    check_database_workers(workers)
    check_no_tuner_pool_conflict(entry.config, entry.target, workers)

    reset = run(["systemctl", "--user", "reset-failed", entry.service], timeout=60)
    if reset.returncode != 0:
        raise RuntimeError(
            f"reset-failed failed for {entry.service}: {(reset.stderr or reset.stdout).strip()}"
        )
    started = run(["systemctl", "--user", "start", entry.service], timeout=180)
    if started.returncode != 0:
        raise RuntimeError(
            f"existing service resume failed for {entry.service}: "
            f"{(started.stderr or started.stdout).strip()}"
        )
    state = service_state(entry.service)
    if state.get("ActiveState") != "active":
        raise RuntimeError(f"resumed service is not active: {state}")
    return {
        "worker_start": worker_start,
        "occupancy": snapshot,
        "service_state": state,
    }


def artifact_paths(entry: Entry) -> Tuple[Path, Path]:
    cfg = load_json(entry.config)
    tuning = cfg.get("binary_plan_tuning") or {}
    output_raw = str(tuning.get("output_dir") or "").strip()
    if not output_raw:
        raise RuntimeError(f"config has no binary_plan_tuning.output_dir: {entry.config}")
    artifact = Path(output_raw).resolve() / "structured_experiment.json"
    return artifact, artifact.with_suffix(".verification.json")


def completed_finalization(entry: Entry) -> Optional[Dict[str, Any]]:
    artifact, verification = artifact_paths(entry)
    if not artifact.is_file() or not verification.is_file():
        return None
    payload = load_json(verification)
    status = str(payload.get("status") or "")
    if status not in {"verified", "semantic_miss"}:
        return None
    if Path(str(payload.get("artifact") or "")).resolve() != artifact:
        raise RuntimeError(f"verification artifact mismatch: {verification}")
    if Path(str(payload.get("config") or "")).resolve() != entry.config:
        raise RuntimeError(f"verification config mismatch: {verification}")
    if Path(str(payload.get("log") or "")).resolve() != entry.log:
        raise RuntimeError(f"verification log mismatch: {verification}")
    alias_rows = payload.get("logical_aliases")
    alias_rows = alias_rows if isinstance(alias_rows, list) else []
    aliases_by_id = {
        str(row.get("logical_exp_id")): row
        for row in alias_rows
        if isinstance(row, dict) and row.get("logical_exp_id")
    }
    if set(aliases_by_id) != set(entry.logical_exp_ids):
        return None
    for logical_exp_id, row in aliases_by_id.items():
        if str(row.get("status") or "") not in {"verified", "semantic_miss"}:
            return None
        alias_artifact = Path(str(row.get("artifact") or ""))
        alias_verification = Path(str(row.get("verification_path") or ""))
        if not alias_artifact.is_file() or not alias_verification.is_file():
            return None
        if logical_exp_id not in alias_artifact.name:
            raise RuntimeError(
                f"logical alias artifact name mismatch for {logical_exp_id}: {alias_artifact}"
            )
    return payload


def finalize_entry(
    entry: Entry,
    finalizer: Path,
    *,
    expected_candidates: int,
    min_rounds: int,
    max_bottleneck_drop_pct: float,
    max_explain_queries: int,
) -> Dict[str, Any]:
    workers = workers_from_config(entry.config, entry.target)
    try:
        snapshot = docker_snapshot(workers)
        check_database_workers(workers)
        check_no_tuner_pool_conflict(entry.config, entry.target, workers)
    except RuntimeError as exc:
        # Completed runs normally retain live workers for optional EXPLAIN
        # backfill.  A reclaimed worker is still safe when every round summary
        # already contains its real EXPLAIN samples; the finalizer verifies
        # that coverage before allowing an offline export.
        snapshot = {
            "status": "workers_unavailable_offline_finalize",
            "reason": str(exc),
            "workers": [str(worker.get("container") or "") for worker in workers],
        }
    artifact, _ = artifact_paths(entry)
    command = [
        sys.executable,
        str(finalizer),
        "--config", str(entry.config),
        "--log", str(entry.log),
        "--artifact", str(artifact),
        "--expected-candidates-per-round", str(expected_candidates),
        "--min-rounds", str(min_rounds),
        "--max-bottleneck-drop-pct", str(max_bottleneck_drop_pct),
        "--max-explain-queries", str(max(1, max_explain_queries)),
    ]
    for logical_exp_id in entry.logical_exp_ids:
        command.extend(["--logical-exp-id", logical_exp_id])
    proc = run(command, timeout=2400)
    if proc.returncode not in {0, 10}:
        raise RuntimeError(
            f"finalizer failed rc={proc.returncode}: {(proc.stderr or proc.stdout).strip()}"
        )
    output = (proc.stdout or "").strip()
    payload: Optional[Dict[str, Any]] = None
    for raw in reversed(output.splitlines()):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            payload = value
            break
    if payload is None:
        raise RuntimeError("finalizer returned no JSON payload")
    expected_status = "verified" if proc.returncode == 0 else "semantic_miss"
    if payload.get("status") != expected_status:
        raise RuntimeError(f"finalizer status/return-code mismatch: {payload}")
    return {"occupancy": snapshot, "finalization": payload}


def parse_entries(raw_queue: Dict[str, Any]) -> List[Entry]:
    entries: List[Entry] = []
    for raw in raw_queue.get("entries") or []:
        logical_exp_ids: List[str] = []
        logical_exp_id = str(raw.get("logical_exp_id") or "").strip()
        if logical_exp_id:
            logical_exp_ids.append(logical_exp_id)
        for value in raw.get("logical_exp_ids") or []:
            value = str(value).strip()
            if value and value not in logical_exp_ids:
                logical_exp_ids.append(value)
        entries.append(
            Entry(
                exp_id=str(raw["exp_id"]),
                logical_exp_ids=tuple(logical_exp_ids),
                service=str(raw["service"]),
                config=Path(str(raw["config"])).resolve(),
                target=str(raw["target"]),
                log=Path(str(raw["log"])).resolve(),
                launcher_args=tuple(str(value) for value in (raw.get("launcher_args") or [])),
            )
        )
    if not entries:
        raise ValueError(f"queue has no entries: {raw_queue.get('pool_id')}")
    return entries


def evaluate_queue(
    raw_queue: Dict[str, Any],
    *,
    launcher: Path,
    finalizer: Path,
    expected_candidates: int,
    min_rounds: int,
    max_bottleneck_drop_pct: float,
    max_explain_queries: int,
    max_active_tuners: int,
    machine_guard: Dict[str, Any],
    all_entries: List[Entry],
    active_configs: Set[Path],
    launch: bool,
) -> str:
    pool_id = str(raw_queue.get("pool_id") or "unknown")
    entries = parse_entries(raw_queue)
    for index, entry in enumerate(entries):
        if entry.config.resolve() in active_configs:
            return f"{pool_id}:{entry.exp_id}:active_config_process"
        state = service_state(entry.service)
        active = state.get("ActiveState") in {"active", "activating", "reloading"}
        if active:
            return f"{pool_id}:{entry.exp_id}:active"

        complete, reason = strict_log_complete(entry.log, expected_candidates, min_rounds)
        if launch and not complete and reason == "search_complete_missing":
            complete, reason = seal_strict_fullgrid_log(
                entry.log,
                expected_candidates,
                min_rounds,
            )
            if complete:
                emit(
                    "strict_fullgrid_log_sealed",
                    pool_id=pool_id,
                    exp_id=entry.exp_id,
                    log=str(entry.log),
                    reason=reason,
                )
        if complete:
            finalized = completed_finalization(entry)
            if finalized is None:
                if not launch:
                    return f"{pool_id}:{entry.exp_id}:ready_to_finalize"
                details = finalize_entry(
                    entry,
                    finalizer,
                    expected_candidates=expected_candidates,
                    min_rounds=min_rounds,
                    max_bottleneck_drop_pct=max_bottleneck_drop_pct,
                    max_explain_queries=max_explain_queries,
                )
                emit("experiment_finalized", pool_id=pool_id, exp_id=entry.exp_id, **details)
                return f"{pool_id}:{entry.exp_id}:finalized:{details['finalization']['status']}"
            continue

        if index > 0:
            previous = entries[index - 1]
            previous_complete, previous_reason = strict_log_complete(
                previous.log,
                expected_candidates,
                min_rounds,
            )
            previous_state = service_state(previous.service)
            previous_load_state = previous_state.get("LoadState")
            previous_result_ok = (
                previous_state.get("Result") == "success"
                and previous_state.get("ExecMainStatus") in {"", "0"}
            )
            previous_process_active = previous.config.resolve() in active_configs
            previous_finalized = completed_finalization(previous) is not None
            previous_success = (
                previous_complete
                and not previous_process_active
                and previous_state.get("ActiveState") in {"inactive", "failed"}
                and (
                    previous_finalized
                    or previous_result_ok
                    or previous_load_state in {"", "not-found"}
                )
            )
            if not previous_success:
                return (
                    f"{pool_id}:{entry.exp_id}:predecessor_not_ready:"
                    f"{previous_reason}:state={previous_state}"
                )

        try:
            orphaned_partial_log = entry.log.is_file() and entry.log.stat().st_size > 0
        except OSError as exc:
            return f"{pool_id}:{entry.exp_id}:log_stat_error:{exc}"
        if state.get("LoadState") not in {"", "not-found"}:
            resumable_state = state.get("ActiveState") in {"inactive", "failed"}
            if (
                launch
                and resumable_state
                and orphaned_partial_log
                and retry_failed_validations_enabled(entry.config)
            ):
                active_tuners = active_tuner_process_count()
                if max_active_tuners > 0 and active_tuners >= max_active_tuners:
                    return (
                        f"{pool_id}:{entry.exp_id}:global_capacity_wait:"
                        f"active_tuners={active_tuners}:max={max_active_tuners}:resume"
                    )
                try:
                    details = resume_existing_entry(entry, machine_guard, all_entries)
                except CapacityUnavailable as exc:
                    return (
                        f"{pool_id}:{entry.exp_id}:global_environment_capacity_wait:"
                        f"resume:{exc}"
                    )
                emit("incomplete_run_resumed", pool_id=pool_id, exp_id=entry.exp_id, **details)
                return f"{pool_id}:{entry.exp_id}:resumed:{reason}"
            return f"{pool_id}:{entry.exp_id}:existing_incomplete_service:{state}:{reason}"

        if orphaned_partial_log:
            if launch and retry_failed_validations_enabled(entry.config):
                active_tuners = active_tuner_process_count()
                if max_active_tuners > 0 and active_tuners >= max_active_tuners:
                    return (
                        f"{pool_id}:{entry.exp_id}:global_capacity_wait:"
                        f"active_tuners={active_tuners}:max={max_active_tuners}:orphan_resume"
                    )
                try:
                    details = launch_entry(entry, launcher, machine_guard, all_entries)
                except CapacityUnavailable as exc:
                    return (
                        f"{pool_id}:{entry.exp_id}:global_environment_capacity_wait:"
                        f"orphan_resume:{exc}"
                    )
                emit("orphaned_run_resumed", pool_id=pool_id, exp_id=entry.exp_id, **details)
                return f"{pool_id}:{entry.exp_id}:orphan_resumed:{reason}"
            return f"{pool_id}:{entry.exp_id}:orphaned_incomplete_log:{reason}"

        if not launch:
            return f"{pool_id}:{entry.exp_id}:ready_to_launch"
        active_tuners = active_tuner_process_count()
        if max_active_tuners > 0 and active_tuners >= max_active_tuners:
            return (
                f"{pool_id}:{entry.exp_id}:global_capacity_wait:"
                f"active_tuners={active_tuners}:max={max_active_tuners}:launch"
            )
        try:
            details = launch_entry(entry, launcher, machine_guard, all_entries)
        except CapacityUnavailable as exc:
            return (
                f"{pool_id}:{entry.exp_id}:global_environment_capacity_wait:"
                f"launch:{exc}"
            )
        emit("repeat_launched", pool_id=pool_id, exp_id=entry.exp_id, **details)
        return f"{pool_id}:{entry.exp_id}:launched"

    return f"{pool_id}:all_repeats_complete"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--watch", action="store_true", help="poll continuously and launch ready repeats")
    parser.add_argument("--once", action="store_true", help="evaluate once without launching")
    return parser.parse_args()


def main() -> int:
    global STATE_LOG
    args = parse_args()
    if args.watch == args.once:
        raise SystemExit("choose exactly one of --watch or --once")
    cfg = load_json(args.config.resolve())
    state_log_raw = str(cfg.get("state_log") or "").strip()
    STATE_LOG = Path(state_log_raw).resolve() if state_log_raw else None
    launcher = Path(str(cfg["launcher"])).resolve()
    finalizer = Path(str(cfg["finalizer"])).resolve()
    expected_candidates = int(cfg.get("expected_candidates_per_round") or 128)
    min_rounds = int(cfg.get("minimum_complete_rounds") or 6)
    max_bottleneck_drop_pct = float(cfg.get("max_bottleneck_drop_pct") or 30.0)
    max_explain_queries = int(cfg.get("max_explain_queries") or 3)
    max_active_tuners = max(0, int(cfg.get("max_active_tuners") or 0))
    machine_guard = cfg.get("machine_guard") or {}
    poll_interval = max(10, int(cfg.get("poll_interval_s") or 60))
    suite_cfg = cfg.get("suite_audit") if isinstance(cfg.get("suite_audit"), dict) else {}
    suite_refresh_interval = max(60, int(suite_cfg.get("refresh_interval_s") or 300))
    queues = cfg.get("queues") or []
    if not queues:
        raise ValueError("no queues configured")
    # Lower values are evaluated first.  Python's sort is stable, so queues
    # without an explicit priority keep their existing order.
    queues = sorted(
        queues,
        key=lambda raw_queue: int(raw_queue.get("priority") or 100),
    )
    all_entries = [entry for raw_queue in queues for entry in parse_entries(raw_queue)]

    last_states: Dict[str, str] = {}
    last_guard_signature = ""
    last_suite_refresh = 0.0
    last_suite_signature = ""
    while True:
        all_complete = True
        try:
            guard_snapshot = machine_capacity_snapshot(machine_guard)
        except Exception as exc:
            emit("machine_guard_inspection_error", error=f"{type(exc).__name__}:{exc}")
            if args.once:
                return 1
            time.sleep(poll_interval)
            continue
        guard_signature = json.dumps(
            {
                "ok": guard_snapshot.get("ok"),
                "reasons": guard_snapshot.get("reasons"),
                "experiment_containers": guard_snapshot.get("experiment_containers"),
                "running_vms": guard_snapshot.get("running_vms"),
                "direct_qemu_processes": guard_snapshot.get("direct_qemu_processes"),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        if guard_signature != last_guard_signature:
            emit(
                "machine_guard_pass" if guard_snapshot.get("ok") else "machine_guard_wait",
                snapshot=guard_snapshot,
            )
            last_guard_signature = guard_signature
        if guard_snapshot.get("ok") is not True:
            reason = ";".join(str(value) for value in (guard_snapshot.get("reasons") or []))
            for raw_queue in queues:
                pool_id = str(raw_queue.get("pool_id") or "unknown")
                state = f"{pool_id}:global_machine_guard_wait:{reason}"
                if state != last_states.get(pool_id):
                    emit("queue_state", pool_id=pool_id, state=state)
                    last_states[pool_id] = state
            if args.once:
                return 1
            time.sleep(poll_interval)
            continue
        try:
            active_configs = referenced_configs_from_processes()
        except Exception as exc:
            emit("watcher_process_inspection_error", error=f"{type(exc).__name__}:{exc}")
            if args.once:
                return 1
            time.sleep(poll_interval)
            continue
        for raw_queue in queues:
            pool_id = str(raw_queue.get("pool_id") or "unknown")
            try:
                state = evaluate_queue(
                    raw_queue,
                    launcher=launcher,
                    finalizer=finalizer,
                    expected_candidates=expected_candidates,
                    min_rounds=min_rounds,
                    max_bottleneck_drop_pct=max_bottleneck_drop_pct,
                    max_explain_queries=max_explain_queries,
                    max_active_tuners=max_active_tuners,
                    machine_guard=machine_guard,
                    all_entries=all_entries,
                    active_configs=active_configs,
                    launch=args.watch,
                )
            except Exception as exc:  # keep other independent queues progressing
                state = f"{pool_id}:error:{type(exc).__name__}:{exc}"
            if state != last_states.get(pool_id):
                emit("queue_state", pool_id=pool_id, state=state)
                last_states[pool_id] = state
            if not state.endswith(":all_repeats_complete"):
                all_complete = False

        if args.once:
            return 0
        now = time.monotonic()
        if not all_complete and now - last_suite_refresh >= suite_refresh_interval:
            try:
                suite_result = run_suite_audit(
                    args.config.resolve(),
                    cfg,
                    allow_incomplete=True,
                )
            except Exception as exc:
                emit("suite_audit_refresh_failed", error=f"{type(exc).__name__}:{exc}")
            else:
                suite_signature = json.dumps(
                    {
                        "status": suite_result.get("status"),
                        "summary": suite_result.get("summary"),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                if suite_signature != last_suite_signature:
                    emit("suite_audit_progress", **suite_result)
                    last_suite_signature = suite_signature
            last_suite_refresh = now
        if all_complete:
            try:
                suite_result = run_suite_audit(args.config.resolve(), cfg)
            except Exception as exc:
                emit("suite_audit_failed", error=f"{type(exc).__name__}:{exc}")
                return 1
            emit("suite_audit_complete", **suite_result)
            return 0
        time.sleep(poll_interval)


if __name__ == "__main__":
    raise SystemExit(main())
