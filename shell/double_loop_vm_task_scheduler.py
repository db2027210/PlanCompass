#!/usr/bin/env python3
"""Work-stealing scheduler for the isolated 18-run double-loop VM matrix."""

from __future__ import annotations

import argparse
import fcntl
import glob
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

try:
    from .provision_double_loop_matrix_vms import VmSpec, vm_specs
except ImportError:
    from provision_double_loop_matrix_vms import VmSpec, vm_specs


ROOT = Path(__file__).resolve().parents[1]
RUNNING_STATES = {"preparing", "running"}
TERMINAL_STATES = {"completed", "needs_review"}
TRANSIENT_MARKERS = (
    "no space left on device",
    "connection timed out",
    "connection reset",
    "temporary failure",
    "ssh command timed out",
)


def load_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def process_alive(pid: Any) -> bool:
    try:
        value = int(pid)
        if value <= 0:
            return False
        status_path = Path(f"/proc/{value}/status")
        status = status_path.read_text(encoding="utf-8", errors="replace")
        state_line = next(
            (line for line in status.splitlines() if line.startswith("State:")),
            "",
        )
        # A child that has exited but has not yet been wait(2)-reaped still
        # answers kill(pid, 0).  Treat zombies/dead tasks as finished so the
        # scheduler can consume their durable worker_result.json.
        state = state_line.split()[1].upper() if len(state_line.split()) >= 2 else ""
        if state in {"Z", "X"}:
            return False
        os.kill(value, 0)
    except (TypeError, ValueError, FileNotFoundError, ProcessLookupError, PermissionError):
        return False
    return True


def parse_cpu_set(value: Any) -> set[int]:
    """Parse Linux cpuset notation into logical CPU ids."""
    cpus: set[int] = set()
    for raw_part in str(value or "").split(","):
        part = raw_part.strip()
        if not part:
            continue
        if "-" not in part:
            cpus.add(int(part))
            continue
        raw_low, raw_high = part.split("-", 1)
        low = int(raw_low)
        high = int(raw_high)
        if high < low:
            raise ValueError(f"invalid descending cpuset range: {part}")
        cpus.update(range(low, high + 1))
    return cpus


def task_slot_cpuset(task: Mapping[str, Any]) -> Optional[str]:
    """Resolve a reserved slot cpuset, including older durable state rows."""
    direct = str(task.get("slot_cpuset_cpus") or "").strip()
    if direct:
        return direct
    worker_result = str(task.get("worker_result") or "").strip()
    if not worker_result:
        return None
    launch_spec = Path(worker_result).parent / "launch_spec.json"
    try:
        raw = load_json(launch_spec)
    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
        return None
    slot = raw.get("slot")
    if not isinstance(slot, Mapping):
        return None
    resolved = str(slot.get("cpuset_cpus") or "").strip()
    return resolved or None


def occupied_cpu_ids(global_concurrency: Mapping[str, Any]) -> set[int]:
    """Return CPUs reserved by all live formal tasks in the campaign."""
    occupied: set[int] = set()
    for task in global_concurrency.get("active_tasks") or []:
        if not isinstance(task, Mapping):
            continue
        try:
            occupied.update(parse_cpu_set(task.get("slot_cpuset_cpus")))
        except (TypeError, ValueError):
            # Invalid legacy metadata must not crash state refresh.  The owning
            # task remains counted by the global VM cap and is exposed in the
            # audit for manual review.
            continue
    return occupied


def reap_finished_children() -> List[int]:
    """Reap detached provisioning children that have already exited.

    Provision workers are started in their own sessions, but they remain direct
    children of the long-lived scheduler.  The durable result file is the source
    of truth for state transitions; waitpid here only prevents completed workers
    from accumulating as zombies while watch mode continues running.
    """
    reaped: List[int] = []
    while True:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            break
        if pid <= 0:
            break
        reaped.append(pid)
    return reaped


def global_dispatch_lock_path(config: Mapping[str, Any]) -> Optional[Path]:
    """Return the campaign-wide lock used to reserve a VM slot atomically."""
    if int(config.get("global_max_active") or 0) <= 0:
        return None
    explicit = str(config.get("global_dispatch_lock") or "").strip()
    if explicit:
        return Path(explicit).resolve()
    pattern = str(config.get("global_scheduler_state_glob") or "").strip()
    if not pattern:
        return None
    wildcard_index = min(
        (index for marker in "*[?" if (index := pattern.find(marker)) >= 0),
        default=-1,
    )
    prefix = pattern if wildcard_index < 0 else pattern[:wildcard_index]
    prefix_path = Path(prefix).resolve()
    lock_dir = prefix_path if prefix.endswith(os.sep) else prefix_path.parent
    return lock_dir / ".double_loop_global_dispatch.lock"


def acquire_global_dispatch_lock(config: Mapping[str, Any]) -> Optional[Any]:
    """Serialize cross-root capacity checks and durable slot reservations."""
    path = global_dispatch_lock_path(config)
    if path is None:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+", encoding="utf-8")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    return handle


def release_global_dispatch_lock(handle: Optional[Any]) -> None:
    if handle is None:
        return
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def global_active_audit(
    config: Mapping[str, Any],
    current_state: Mapping[str, Any],
    scheduler_root: Path,
) -> Dict[str, Any]:
    """Count live formal tasks across independently managed scheduler roots.

    Other schedulers are read-only inputs. Stale durable rows count only when
    their worker or runner PID is still alive, so an abandoned queue cannot
    permanently consume the campaign-wide VM budget.
    """
    cap = int(config.get("global_max_active") or 0)
    pattern = str(config.get("global_scheduler_state_glob") or "").strip()
    if cap <= 0 or not pattern:
        return {
            "enabled": False,
            "max_active": None,
            "active_count": None,
            "available": None,
            "state_glob": pattern or None,
            "active_tasks": [],
            "state_errors": [],
        }

    current_path = (scheduler_root / "scheduler_state.json").resolve()
    paths = {Path(value).resolve() for value in glob.glob(pattern)}
    paths.add(current_path)
    active_tasks: List[Dict[str, Any]] = []
    state_errors: List[Dict[str, str]] = []
    for path in sorted(paths, key=str):
        try:
            state = current_state if path == current_path else load_json(path)
        except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError) as exc:
            state_errors.append(
                {"path": str(path), "error": f"{type(exc).__name__}: {exc}"}
            )
            continue
        tasks = state.get("tasks") if isinstance(state, Mapping) else None
        if not isinstance(tasks, Mapping):
            state_errors.append({"path": str(path), "error": "missing tasks mapping"})
            continue
        for identity, task in tasks.items():
            if not isinstance(task, Mapping) or task.get("status") not in RUNNING_STATES:
                continue
            worker_alive = process_alive(task.get("worker_pid"))
            runner_alive = process_alive(task.get("runner_pid"))
            if not worker_alive and not runner_alive:
                continue
            active_tasks.append(
                {
                    "scheduler_root": str(path.parent),
                    "task_id": str(task.get("task_id") or identity),
                    "status": str(task.get("status")),
                    "worker_pid": task.get("worker_pid"),
                    "runner_pid": task.get("runner_pid"),
                    "worker_alive": worker_alive,
                    "runner_alive": runner_alive,
                    "slot_cpuset_cpus": task_slot_cpuset(task),
                }
            )
    active_count = len(active_tasks)
    return {
        "enabled": True,
        "max_active": cap,
        "active_count": active_count,
        "available": max(0, cap - active_count),
        "state_glob": pattern,
        "active_tasks": active_tasks,
        "state_errors": state_errors,
    }


def task_id(spec: VmSpec) -> str:
    return f"{spec.exp_id.lower()}_r{spec.repeat_id}"


def matrix_rows(config: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    path = (ROOT / str(config["matrix_config"])).resolve()
    matrix = load_json(path)
    run_semantics = matrix.get("run_count_semantics") or {}
    if run_semantics.get("b_group_total_runs_include_anchor_a_run") is not True:
        raise RuntimeError("B group run count must explicitly include its A anchor run")
    rows: Dict[str, Dict[str, Any]] = {}
    for raw in matrix.get("experiments") or []:
        exp_id = str(raw["exp_id"])
        repeat_ids = raw.get("repeat_ids")
        if repeat_ids is None:
            repeat_ids = list(range(1, int(raw.get("runs") or 0) + 1))
        if raw.get("anchor_exp_id"):
            expected_new_repeats = list(run_semantics.get("b_group_new_repeat_ids") or [])
            total_including_anchor = int(raw.get("total_runs_including_anchor") or 0)
            if list(repeat_ids) != expected_new_repeats or total_including_anchor != 1 + len(repeat_ids):
                raise RuntimeError(
                    f"{exp_id} must add only repeats {expected_new_repeats} after its A anchor; "
                    f"got repeat_ids={repeat_ids}, total_including_anchor={total_including_anchor}"
                )
        for repeat_id in repeat_ids:
            rows[f"{exp_id.lower()}_r{int(repeat_id)}"] = {
                **raw,
                "repeat_id": int(repeat_id),
            }
    if len(rows) != 18:
        raise RuntimeError(f"formal matrix must expand to 18 tasks, got {len(rows)}")
    return rows


def build_task_catalog(config: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    rows = matrix_rows(config)
    specs = {task_id(spec): spec for spec in vm_specs()}
    if set(rows) != set(specs):
        raise RuntimeError(
            f"matrix/VM task mismatch: missing_specs={sorted(set(rows) - set(specs))} "
            f"extra_specs={sorted(set(specs) - set(rows))}"
        )
    profiles = config.get("profiles") or {}
    task_profile_overrides = config.get("task_profile_overrides") or {}
    if not isinstance(task_profile_overrides, Mapping):
        raise RuntimeError("task_profile_overrides must be a mapping")
    unknown_overrides = set(task_profile_overrides) - set(rows)
    if unknown_overrides:
        raise RuntimeError(
            "task_profile_overrides contains unknown tasks: "
            f"{sorted(unknown_overrides)}"
        )
    catalog: Dict[str, Dict[str, Any]] = {}
    for order, (identity, raw) in enumerate(rows.items()):
        spec = specs[identity]
        profile_key = f"{spec.target}:{spec.benchmark}"
        profile = profiles.get(profile_key)
        if not isinstance(profile, dict):
            raise RuntimeError(f"task {identity} has no profile {profile_key}")
        profile = dict(profile)
        override = task_profile_overrides.get(identity)
        if override is not None:
            if not isinstance(override, Mapping):
                raise RuntimeError(
                    f"task profile override for {identity} must be a mapping"
                )
            profile.update(override)
        anchor = raw.get("anchor_exp_id")
        dependencies = [f"{str(anchor).lower()}_r1"] if anchor else []
        catalog[identity] = {
            "task_id": identity,
            "order": order,
            "exp_id": spec.exp_id,
            "repeat_id": spec.repeat_id,
            "benchmark": spec.benchmark,
            "target": spec.target,
            "lane": spec.target,
            "selector": f"{spec.exp_id}:{spec.repeat_id}",
            "vm_name": spec.name,
            "mac": spec.mac,
            "profile_key": profile_key,
            "profile": profile,
            "dependencies": dependencies,
        }
    return catalog


def initial_state(config: Mapping[str, Any], catalog: Mapping[str, Any]) -> Dict[str, Any]:
    adopted = config.get("adopted_runs") or {}
    holds = config.get("holds") or {}
    tasks: Dict[str, Dict[str, Any]] = {}
    for identity, task in catalog.items():
        row = dict(task)
        row.update({"status": "pending", "attempt": 0, "history": []})
        if identity in holds:
            row.update({"status": "held", "hold_reason": str(holds[identity])})
        adoption = adopted.get(identity)
        if isinstance(adoption, dict):
            row.update(
                {
                    "status": "running" if process_alive(adoption.get("pid")) else "needs_review",
                    "attempt": 1,
                    "runner_pid": adoption.get("pid"),
                    "run_dir": adoption.get("run_dir"),
                    "adopted": True,
                }
            )
            if row["status"] == "needs_review":
                row["last_error"] = "configured adopted process is not alive"
        tasks[identity] = row
    return {
        "schema_version": 1,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "next_lane_index": 0,
        "tasks": tasks,
    }


def merge_catalog(state: Dict[str, Any], catalog: Mapping[str, Any]) -> None:
    tasks = state.setdefault("tasks", {})
    obsolete = set(tasks) - set(catalog)
    if obsolete:
        raise RuntimeError(f"state contains obsolete tasks: {sorted(obsolete)}")
    for identity, task in catalog.items():
        if identity not in tasks:
            tasks[identity] = {
                **task,
                "status": "pending",
                "attempt": 0,
                "history": [],
            }
        else:
            for key, value in task.items():
                tasks[identity][key] = value


def scenario_summary(task: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    raw = task.get("run_dir")
    if not raw:
        return None
    path = Path(str(raw)) / str(task["target"]) / "scenario_summary.json"
    if not path.is_file():
        return None
    return load_json(path)


def guest_exit_failure(task: Mapping[str, Any]) -> Optional[str]:
    """Return a fail-closed reason for guest instability in this attempt."""
    raw_root = task.get("task_root")
    if raw_root:
        task_root = Path(str(raw_root))
    else:
        raw_run = task.get("run_dir")
        if not raw_run:
            return None
        run_dir = Path(str(raw_run))
        try:
            task_root = run_dir.parents[1]
        except IndexError:
            return None
    diagnostics_path = task_root / "guest_exit_diagnostics.json"
    outputs: List[str] = []
    try:
        if diagnostics_path.is_file():
            outputs.append(str(load_json(diagnostics_path).get("output") or ""))
    except (json.JSONDecodeError, OSError, ValueError):
        return "guest diagnostics are unreadable"
    serial_path = task_root / "rootless_vm" / "serial.log"
    try:
        if serial_path.is_file():
            outputs.append(serial_path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return "guest serial diagnostics are unreadable"
    output = "\n".join(outputs).lower()
    if not output:
        return None
    if "out of memory: killed process" in output or "oom-killer" in output:
        return "guest OOM occurred during formal real replay"
    if "no space left on device" in output:
        return "guest disk became full during formal real replay"
    if "terminated by signal 9: killed" in output:
        return "guest process was killed by SIGKILL during formal real replay"
    return None


def transient_failure(message: str) -> bool:
    lowered = message.lower()
    return any(marker in lowered for marker in TRANSIENT_MARKERS)


def append_history(task: Dict[str, Any], event: str, **fields: Any) -> None:
    task.setdefault("history", []).append(
        {"timestamp": time.strftime("%Y-%m-%d %H:%M:%S"), "event": event, **fields}
    )


def export_completed(task: Dict[str, Any], scheduler_root: Path) -> Tuple[bool, str]:
    scenario_dir = Path(str(task["run_dir"])) / str(task["target"])
    output = scheduler_root / "exports" / f"{task['task_id']}.json"
    command = [
        sys.executable,
        str(ROOT / "shell/export_double_loop_experiment_log.py"),
        str(scenario_dir),
        "--exp-id",
        str(task["exp_id"]),
        "--benchmark",
        str(task["benchmark"]),
        "--bottleneck",
        str(task["target"]),
        "--repeat-id",
        str(task["repeat_id"]),
        "--output",
        str(output),
        "--require-complete",
    ]
    completed = subprocess.run(
        command,
        cwd=str(ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=300,
        check=False,
    )
    if completed.returncode != 0:
        return False, completed.stdout.strip()
    task["export_path"] = str(output)
    return True, completed.stdout.strip()


def finish_or_retry(
    task: Dict[str, Any],
    *,
    config: Mapping[str, Any],
    scheduler_root: Path,
    message: str,
) -> None:
    guest_failure = guest_exit_failure(task)
    if guest_failure is not None:
        message = guest_failure
        summary = None
    else:
        summary = scenario_summary(task)
    if summary is not None and summary.get("successful") is True:
        final = summary.get("final_confirmation") or {}
        final_decision = final.get("final_goal_decision") or summary.get("final_decision") or {}
        if final.get("independent_real_replay") is True and final_decision.get("successful") is True:
            passed, detail = export_completed(task, scheduler_root)
            if passed:
                task.update({"status": "completed", "completed_at": time.time()})
                append_history(task, "completed", export=task.get("export_path"))
                return
            message = f"standard export failed: {detail}"
        else:
            message = "run lacks a successful independent final replay"
    elif summary is not None:
        message = str(summary.get("error") or summary.get("stop_reason") or message)
    retries = int(config.get("max_auto_retries") or 0)
    if transient_failure(message) and int(task.get("attempt") or 0) <= retries:
        task.update({"status": "pending", "last_error": message})
        append_history(task, "auto_retry_queued", reason=message)
    else:
        task.update({"status": "needs_review", "last_error": message})
        append_history(task, "needs_review", reason=message)


def refresh_state(state: Dict[str, Any], config: Mapping[str, Any], scheduler_root: Path) -> None:
    for task in state["tasks"].values():
        status = task.get("status")
        if status == "preparing":
            if process_alive(task.get("worker_pid")):
                continue
            result_path = Path(str(task.get("worker_result") or ""))
            if not result_path.is_file():
                finish_or_retry(
                    task,
                    config=config,
                    scheduler_root=scheduler_root,
                    message="provision worker exited without result",
                )
                continue
            result = load_json(result_path)
            if result.get("status") in {"launched", "finished"}:
                task.update(
                    {
                        "status": "running",
                        "runner_pid": result.get("runner_pid"),
                        "run_dir": result.get("run_dir"),
                        "runtime_dir": result.get("runtime_dir"),
                        "vm_ip": result.get("vm_ip"),
                        "launch_manifest": result.get("launch_manifest"),
                    }
                )
                append_history(task, "formal_run_launched", runner_pid=result.get("runner_pid"))
                if not process_alive(task.get("runner_pid")):
                    finish_or_retry(
                        task,
                        config=config,
                        scheduler_root=scheduler_root,
                        message="formal runner exited before scheduler refresh",
                    )
            else:
                finish_or_retry(
                    task,
                    config=config,
                    scheduler_root=scheduler_root,
                    message=str(result.get("error") or "provision worker failed"),
                )
        elif status == "running" and not process_alive(task.get("runner_pid")):
            finish_or_retry(
                task,
                config=config,
                scheduler_root=scheduler_root,
                message="formal runner exited without successful completion evidence",
            )


def gib_free(path: Path) -> float:
    stat = os.statvfs(path)
    return stat.f_bavail * stat.f_frsize / (1024.0 ** 3)


def pressure_avg10(resource: str, kind: str) -> float:
    path = Path("/proc/pressure") / resource
    try:
        rows = path.read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, PermissionError, OSError):
        return 0.0
    for row in rows:
        fields = row.split()
        if fields and fields[0] == kind:
            for field in fields[1:]:
                if field.startswith("avg10="):
                    return float(field.split("=", 1)[1])
    return 0.0


def safety_snapshot(config: Mapping[str, Any]) -> Dict[str, float]:
    mem_available_kib = 0
    for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
        if line.startswith("MemAvailable:"):
            mem_available_kib = int(line.split()[1])
            break
    scheduler_root = Path(str(config["scheduler_root"]))
    scheduler_root.mkdir(parents=True, exist_ok=True)
    load_1m, load_5m, load_15m = os.getloadavg()
    cpu_count = max(1, int(os.cpu_count() or 1))
    return {
        "mem_available_gib": mem_available_kib / (1024.0 ** 2),
        "shm_free_gib": gib_free(Path("/dev/shm")),
        "output_disk_free_gib": gib_free(scheduler_root),
        "root_disk_free_gib": gib_free(Path("/")),
        "host_cpu_count": float(cpu_count),
        "host_load_1m": float(load_1m),
        "host_load_5m": float(load_5m),
        "host_load_15m": float(load_15m),
        "host_load_1m_per_cpu": float(load_1m) / float(cpu_count),
        "cpu_pressure_some_avg10": pressure_avg10("cpu", "some"),
        "io_pressure_some_avg10": pressure_avg10("io", "some"),
        "io_pressure_full_avg10": pressure_avg10("io", "full"),
    }


def safe_for_task(config: Mapping[str, Any], task: Mapping[str, Any], snapshot: Mapping[str, float]) -> Tuple[bool, str]:
    limits = config.get("safety") or {}
    requested = float(task["profile"]["memory_mib"]) / 1024.0
    checks = {
        "mem_available_after": snapshot["mem_available_gib"] - requested
        - float(limits.get("min_mem_available_gib_after_launch") or 0.0),
        "shm_free": snapshot["shm_free_gib"] - float(limits.get("min_shm_free_gib") or 0.0),
        "output_disk_free": snapshot["output_disk_free_gib"]
        - float(limits.get("min_output_disk_free_gib") or 0.0),
        "root_disk_free": snapshot.get("root_disk_free_gib", float("inf"))
        - float(limits.get("min_root_disk_free_gib") or 0.0),
    }
    max_load = float(limits.get("max_host_load_1m_per_cpu") or 0.0)
    if max_load > 0.0:
        checks["host_load_1m_per_cpu"] = max_load - float(
            snapshot.get("host_load_1m_per_cpu") or 0.0
        )
    max_cpu_pressure = float(limits.get("max_cpu_pressure_some_avg10") or 0.0)
    if max_cpu_pressure > 0.0:
        checks["cpu_pressure_some_avg10"] = max_cpu_pressure - float(
            snapshot.get("cpu_pressure_some_avg10") or 0.0
        )
    max_io_pressure = float(limits.get("max_io_pressure_full_avg10") or 0.0)
    if max_io_pressure > 0.0:
        checks["io_pressure_full_avg10"] = max_io_pressure - float(
            snapshot.get("io_pressure_full_avg10") or 0.0
        )
    failed = [name for name, margin in checks.items() if margin < 0.0]
    return (not failed, "ok" if not failed else "safety margins failed: " + ",".join(failed))


def exact_vm_live(vm_name: str) -> bool:
    completed = subprocess.run(
        ["ps", "-eo", "args="],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=20,
        check=False,
    )
    needle = f"-name {vm_name} "
    return any("qemu-system" in line and needle in line for line in completed.stdout.splitlines())


def dependencies_complete(task: Mapping[str, Any], tasks: Mapping[str, Any]) -> bool:
    return all((tasks.get(item) or {}).get("status") == "completed" for item in task.get("dependencies") or [])


def choose_next(
    state: Dict[str, Any],
    config: Mapping[str, Any],
    snapshot: Optional[Mapping[str, float]] = None,
    *,
    excluded_task_ids: Iterable[str] = (),
) -> Optional[Dict[str, Any]]:
    tasks = state["tasks"]
    excluded = {str(value) for value in excluded_task_ids}
    lanes = list(config.get("lane_order") or ["cpu", "memory", "io", "none"])
    start = int(state.get("next_lane_index") or 0) % len(lanes)
    for offset in range(len(lanes)):
        index = (start + offset) % len(lanes)
        lane = lanes[index]
        candidates = sorted(
            (
                task
                for task in tasks.values()
                if task.get("status") == "pending"
                and str(task.get("task_id")) not in excluded
                and task.get("lane") == lane
                and dependencies_complete(task, tasks)
                and (
                    str(config.get("execution_mode") or "") != "rootless_qemu_pool"
                    or Path(
                        str((config.get("benchmark_seeds") or {}).get(task["benchmark"]) or "")
                    ).is_file()
                )
            ),
            key=lambda task: int(task["order"]),
        )
        if snapshot is not None:
            candidates = [
                task
                for task in candidates
                if safe_for_task(config, task, snapshot)[0]
            ]
        if candidates:
            state["next_lane_index"] = (index + 1) % len(lanes)
            return candidates[0]
    return None


def launch_task(
    task: Dict[str, Any],
    config: Mapping[str, Any],
    scheduler_root: Path,
    *,
    slot: Optional[Mapping[str, Any]] = None,
) -> None:
    rootless_pool = str(config.get("execution_mode") or "") == "rootless_qemu_pool"
    if not rootless_pool and exact_vm_live(str(task["vm_name"])) and not task.get("history"):
        task.update(
            {
                "status": "needs_review",
                "last_error": "exact VM name is live but not owned by scheduler state",
            }
        )
        append_history(task, "unmanaged_exact_vm_collision")
        return
    attempt = int(task.get("attempt") or 0) + 1
    task_root = scheduler_root / "runs" / task["task_id"] / f"attempt_{attempt}"
    control = scheduler_root / "control" / task["task_id"] / f"attempt_{attempt}"
    launch_spec = control / "launch_spec.json"
    result = control / "worker_result.json"
    log = control / "worker.log"
    overlay_root = Path(str(config["overlay_root"])).resolve()
    profile_key = f"{task['target']}:{task['benchmark']}"
    current_profiles = config.get("profiles")
    current_profile = (
        current_profiles.get(profile_key)
        if isinstance(current_profiles, Mapping)
        else None
    )
    effective_profile = (
        dict(current_profile)
        if isinstance(current_profile, Mapping)
        else dict(task["profile"])
    )
    # Keep per-task recovery/experiment overrides effective at the actual
    # launch boundary.  The scheduler intentionally re-reads the shared
    # profile here so a long-lived watcher can pick up safe workload changes,
    # but replacing the catalog profile outright used to discard
    # ``task_profile_overrides`` (notably the Top15/candidate resume paths).
    task_profile_overrides = config.get("task_profile_overrides") or {}
    task_override = (
        task_profile_overrides.get(str(task["task_id"]))
        if isinstance(task_profile_overrides, Mapping)
        else None
    )
    if isinstance(task_override, Mapping):
        effective_profile.update(task_override)
    spec = {
        **{
            key: task[key]
            for key in (
                "task_id",
                "exp_id",
                "repeat_id",
                "selector",
                "vm_name",
                "benchmark",
                "target",
            )
        },
        "attempt": attempt,
        "task_root": str(task_root),
        "overlay_root": str(overlay_root),
        "seed_overlay": str(overlay_root.parent / "vm_seeds" / f"{task['vm_name']}.qcow2"),
        "prepared_image": str(Path(str(config["prepared_image"])).resolve()),
        "profile": effective_profile,
    }
    scheduler_config_path = config.get("_scheduler_config_path")
    if scheduler_config_path:
        spec["scheduler_config_path"] = str(
            Path(str(scheduler_config_path)).resolve()
        )
    worker_script = ROOT / "shell/double_loop_vm_task_worker.py"
    if rootless_pool:
        if not isinstance(slot, Mapping):
            raise RuntimeError("rootless pool launch requires a free slot")
        seeds = config.get("benchmark_seeds") or {}
        seed = seeds.get(str(task["benchmark"]))
        if not seed:
            raise RuntimeError(f"no rootless benchmark seed configured for {task['benchmark']}")
        spec.update(
            {
                "slot": dict(slot),
                "seed_overlay": str(Path(str(seed)).resolve()),
                "execution_mode": "rootless_qemu_pool",
            }
        )
        # A rootless QEMU backed by tmpfs has to use writeback caching, which
        # makes host page-cache state part of an IO measurement.  A profile may
        # opt into a disk-backed runtime overlay instead; the worker validates
        # the requested direct-cache mode before launching QEMU.
        runtime_overlay_root = effective_profile.get("runtime_overlay_root")
        if not runtime_overlay_root:
            runtime_overlay_root = config.get("runtime_overlay_root")
        if runtime_overlay_root:
            spec["runtime_overlay_root"] = str(
                Path(str(runtime_overlay_root)).resolve()
            )
        direct_kernel_boot = config.get("direct_kernel_boot")
        if isinstance(direct_kernel_boot, Mapping):
            spec["direct_kernel_boot"] = dict(direct_kernel_boot)
        worker_script = ROOT / "shell/double_loop_rootless_vm_task_worker.py"
    write_json(launch_spec, spec)
    control.mkdir(parents=True, exist_ok=True)
    handle = log.open("ab", buffering=0)
    process = subprocess.Popen(
        [
            sys.executable,
            "-u",
            str(worker_script),
            "--launch-spec",
            str(launch_spec),
            "--result",
            str(result),
        ],
        cwd=str(ROOT),
        stdout=handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    handle.close()
    for stale_key in (
        "runner_pid",
        "run_dir",
        "runtime_dir",
        "launch_manifest",
        "export_path",
        "completed_at",
    ):
        task.pop(stale_key, None)
    task.update(
        {
            "status": "preparing",
            "attempt": attempt,
            "worker_pid": process.pid,
            "worker_result": str(result),
            "worker_log": str(log),
            "task_root": str(task_root),
            "started_at": time.time(),
            "slot_id": (str(slot["slot_id"]) if slot is not None else None),
            "slot_cpuset_cpus": (
                str(slot.get("cpuset_cpus") or "") if slot is not None else None
            ),
            # Per-attempt evidence must never fall through to a previous
            # run when provisioning fails before a new runner is created.
            "runner_pid": None,
            "run_dir": None,
            "runtime_dir": None,
            "launch_manifest": None,
            "export_path": None,
            "last_error": None,
        }
    )
    append_history(task, "provision_started", worker_pid=process.pid)


def status_payload(
    state: Mapping[str, Any],
    config: Mapping[str, Any],
    snapshot: Mapping[str, float],
    global_concurrency: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    tasks = list(state["tasks"].values())
    counts: Dict[str, int] = {}
    for task in tasks:
        status = str(task.get("status"))
        counts[status] = counts.get(status, 0) + 1
    lanes: Dict[str, List[Dict[str, Any]]] = {}
    for lane in config.get("lane_order") or []:
        lanes[str(lane)] = [
            {
                "task_id": task["task_id"],
                "benchmark": task["benchmark"],
                "status": task["status"],
                "attempt": task.get("attempt"),
                "dependencies": task.get("dependencies"),
                "last_error": task.get("last_error"),
            }
            for task in sorted(tasks, key=lambda item: int(item["order"]))
            if task["lane"] == lane
        ]
    slots = []
    for slot in config.get("slots") or []:
        assigned = next(
            (
                task
                for task in tasks
                if task.get("status") in RUNNING_STATES
                and str(task.get("slot_id") or "") == str(slot.get("slot_id") or "")
            ),
            None,
        )
        slots.append(
            {
                **dict(slot),
                "status": "busy" if assigned else "idle",
                "task_id": assigned.get("task_id") if assigned else None,
                "benchmark": assigned.get("benchmark") if assigned else None,
                "target": assigned.get("target") if assigned else None,
            }
        )
    return {
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "formal_task_count": len(tasks),
        "max_active": int(config["max_active"]),
        "counts": counts,
        "safety": dict(snapshot),
        "global_concurrency": dict(global_concurrency or {"enabled": False}),
        "dispatch_policy": "global_work_stealing",
        "slots": slots,
        "lanes": lanes,
    }


def render_status_markdown(payload: Mapping[str, Any]) -> str:
    lines = [
        "# Double-loop VM scheduler queue",
        "",
        f"Updated: {payload['updated_at']}",
        "",
        f"Formal runs: {payload['formal_task_count']}; max active slots: {payload['max_active']}",
        "",
        "Dispatch policy: global work stealing; an idle slot claims the next eligible task.",
        "",
        "| VM slot | SSH port | PostgreSQL port | Status | Current task | Benchmark | Target |",
        "|---|---:|---:|---|---|---|---|",
    ]
    for slot in payload.get("slots") or []:
        lines.append(
            f"| {slot['slot_id']} | {slot['ssh_port']} | {slot['postgres_port']} | "
            f"{slot['status']} | {slot.get('task_id') or '-'} | "
            f"{slot.get('benchmark') or '-'} | {slot.get('target') or '-'} |"
        )
    lines.extend(
        [
        "",
        "| Lane | Queue order | Benchmark | Status | Attempt | Dependency | Last error |",
        "|---|---|---|---|---:|---|---|",
        ]
    )
    for lane, tasks in (payload.get("lanes") or {}).items():
        for index, task in enumerate(tasks, start=1):
            dependencies = ", ".join(task.get("dependencies") or []) or "-"
            error = str(task.get("last_error") or "-").replace("|", "\\|").replace("\n", " ")
            lines.append(
                f"| {lane} | {index}. {task['task_id']} | {task['benchmark']} | "
                f"{task['status']} | {task.get('attempt') or 0} | {dependencies} | {error} |"
            )
    safety = payload.get("safety") or {}
    global_concurrency = payload.get("global_concurrency") or {}
    lines.extend(
        [
            "",
            "## Safety snapshot",
            "",
            f"- MemAvailable: {float(safety.get('mem_available_gib') or 0.0):.1f} GiB",
            f"- /dev/shm free: {float(safety.get('shm_free_gib') or 0.0):.1f} GiB",
            f"- output disk free: {float(safety.get('output_disk_free_gib') or 0.0):.1f} GiB",
            f"- root disk free: {float(safety.get('root_disk_free_gib') or 0.0):.1f} GiB",
            f"- host load 1m/5m/15m: {float(safety.get('host_load_1m') or 0.0):.1f} / "
            f"{float(safety.get('host_load_5m') or 0.0):.1f} / "
            f"{float(safety.get('host_load_15m') or 0.0):.1f}",
            f"- host load 1m per CPU: {float(safety.get('host_load_1m_per_cpu') or 0.0):.3f}",
            f"- CPU PSI some avg10: {float(safety.get('cpu_pressure_some_avg10') or 0.0):.2f}%",
            f"- IO PSI some/full avg10: {float(safety.get('io_pressure_some_avg10') or 0.0):.2f}% / "
            f"{float(safety.get('io_pressure_full_avg10') or 0.0):.2f}%",
            f"- Global formal concurrency: "
            f"{global_concurrency.get('active_count', '-')} / "
            f"{global_concurrency.get('max_active', '-')} "
            f"(enabled={bool(global_concurrency.get('enabled'))})",
            "",
        ]
    )
    return "\n".join(lines)


def release_holds(state: Dict[str, Any], identities: Iterable[str]) -> None:
    for raw in identities:
        identity = raw.strip().lower()
        task = state["tasks"].get(identity)
        if not task:
            raise ValueError(f"unknown task for --release-hold: {identity}")
        if task.get("status") != "held":
            raise ValueError(f"task is not held: {identity} status={task.get('status')}")
        task.pop("hold_reason", None)
        task["status"] = "pending"
        append_history(task, "hold_released")


def apply_config_holds(state: Dict[str, Any], config: Mapping[str, Any]) -> None:
    """Apply newly configured ownership holds to an existing durable queue."""
    for raw_identity, raw_reason in (config.get("holds") or {}).items():
        identity = str(raw_identity).strip().lower()
        task = state["tasks"].get(identity)
        if not task:
            raise ValueError(f"unknown task in configured holds: {identity}")
        if task.get("status") not in {"pending", "needs_review"}:
            continue
        reason = str(raw_reason or "configured hold")
        task["status"] = "held"
        task["hold_reason"] = reason
        append_history(task, "config_hold_applied", reason=reason)


def retry_tasks(state: Dict[str, Any], identities: Iterable[str]) -> None:
    for raw in identities:
        identity = raw.strip().lower()
        task = state["tasks"].get(identity)
        if not task:
            raise ValueError(f"unknown task for --retry-task: {identity}")
        if task.get("status") != "needs_review":
            raise ValueError(f"task is not reviewable: {identity} status={task.get('status')}")
        task["status"] = "pending"
        task.pop("last_error", None)
        append_history(task, "manual_retry_queued")


def tick(
    state: Dict[str, Any],
    config: Dict[str, Any],
    *,
    execute: bool,
) -> Dict[str, Any]:
    scheduler_root = Path(str(config["scheduler_root"])).resolve()
    refresh_state(state, config, scheduler_root)
    reap_finished_children()
    snapshot = safety_snapshot(config)
    global_concurrency = global_active_audit(config, state, scheduler_root)
    if execute:
        while sum(task.get("status") in RUNNING_STATES for task in state["tasks"].values()) < int(config["max_active"]):
            dispatch_lock = acquire_global_dispatch_lock(config)
            try:
                # The global audit and the durable local reservation must be one
                # atomic operation.  Otherwise two independent scheduler roots
                # can both observe the last free campaign slot and launch a fifth
                # VM before either writes its state.
                global_concurrency = global_active_audit(config, state, scheduler_root)
                if (
                    global_concurrency.get("enabled") is True
                    and int(global_concurrency.get("active_count") or 0)
                    >= int(global_concurrency.get("max_active") or 0)
                ):
                    state["last_safety_pause"] = "global formal concurrency limit reached"
                    break
                slot: Optional[Mapping[str, Any]] = None
                if str(config.get("execution_mode") or "") == "rootless_qemu_pool":
                    occupied = {
                        str(task.get("slot_id"))
                        for task in state["tasks"].values()
                        if task.get("status") in RUNNING_STATES and task.get("slot_id")
                    }
                    globally_occupied_cpus = occupied_cpu_ids(global_concurrency)
                    slot = next(
                        (
                            candidate
                            for candidate in (config.get("slots") or [])
                            if str(candidate.get("slot_id")) not in occupied
                            and not (
                                parse_cpu_set(candidate.get("cpuset_cpus"))
                                & globally_occupied_cpus
                            )
                        ),
                        None,
                    )
                    if slot is None:
                        state["last_safety_pause"] = (
                            "no non-overlapping formal VM cpuset is currently free"
                        )
                        break
                task = choose_next(
                    state,
                    config,
                    snapshot,
                    excluded_task_ids={
                        str(item.get("task_id"))
                        for item in global_concurrency.get("active_tasks") or []
                        if isinstance(item, Mapping)
                    },
                )
                if task is None:
                    state["last_safety_pause"] = (
                        "no eligible pending task fits current safety margins"
                    )
                    break
                launch_task(task, config, scheduler_root, slot=slot)
                if task.get("status") in RUNNING_STATES:
                    # Publish the reservation before another scheduler root can
                    # acquire the global dispatch lock and repeat the audit.
                    state["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                    write_json(scheduler_root / "scheduler_state.json", state)
                    snapshot["mem_available_gib"] -= (
                        float(task["profile"]["memory_mib"]) / 1024.0
                    )
            finally:
                release_global_dispatch_lock(dispatch_lock)
        global_concurrency = global_active_audit(config, state, scheduler_root)
    state["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    payload = status_payload(state, config, snapshot, global_concurrency)
    write_json(scheduler_root / "queue_status.json", payload)
    with (scheduler_root / "host_resource_monitor.jsonl").open(
        "a", encoding="utf-8"
    ) as handle:
        handle.write(
            json.dumps(
                {
                    "timestamp": payload["updated_at"],
                    "active_tasks": sum(
                        task.get("status") in RUNNING_STATES
                        for task in state["tasks"].values()
                    ),
                    "max_active": int(config["max_active"]),
                    "global_concurrency": global_concurrency,
                    "safety": snapshot,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n"
        )
    (scheduler_root / "queue_status.md").write_text(
        render_status_markdown(payload),
        encoding="utf-8",
    )
    write_json(scheduler_root / "scheduler_state.json", state)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "config/double_loop_vm_scheduler_20260718.json",
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--max-active", type=int)
    parser.add_argument(
        "--scheduler-root",
        type=Path,
        help="Override scheduler_root so a new campaign cannot inherit stale task state.",
    )
    parser.add_argument(
        "--runtime-overlay-root",
        type=Path,
        help="Override the tmpfs overlay root for an isolated campaign.",
    )
    parser.add_argument(
        "--overlay-root",
        type=Path,
        help="Override the persistent seed-overlay root for an isolated campaign.",
    )
    parser.add_argument(
        "--promote-max-active-when-file",
        type=Path,
        help="Raise the slot limit only after this readiness artifact exists.",
    )
    parser.add_argument(
        "--promoted-max-active",
        type=int,
        help="Slot limit used after --promote-max-active-when-file appears.",
    )
    parser.add_argument("--release-hold", action="append", default=[])
    parser.add_argument("--retry-task", action="append", default=[])
    parser.add_argument(
        "--only-task",
        action="append",
        default=[],
        help="Hold every matrix task except the selected task; may be repeated.",
    )
    args = parser.parse_args()
    resolved_config_path = args.config.resolve()
    config = load_json(resolved_config_path)
    config["_scheduler_config_path"] = str(resolved_config_path)
    if args.scheduler_root is not None:
        config["scheduler_root"] = str(args.scheduler_root.resolve())
    if args.runtime_overlay_root is not None:
        config["runtime_overlay_root"] = str(args.runtime_overlay_root.resolve())
    if args.overlay_root is not None:
        config["overlay_root"] = str(args.overlay_root.resolve())
    if args.max_active is not None:
        if args.max_active < 1:
            raise ValueError("--max-active must be positive")
        config["max_active"] = args.max_active
    if args.promoted_max_active is not None and args.promoted_max_active < 1:
        raise ValueError("--promoted-max-active must be positive")
    if (args.promote_max_active_when_file is None) != (
        args.promoted_max_active is None
    ):
        parser.error(
            "--promote-max-active-when-file and --promoted-max-active must be used together"
        )
    scheduler_root = Path(str(config["scheduler_root"])).resolve()
    scheduler_root.mkdir(parents=True, exist_ok=True)
    lock_path = scheduler_root / "scheduler.lock"
    lock_handle = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise RuntimeError(f"another scheduler owns {lock_path}") from exc
    catalog = build_task_catalog(config)
    if args.only_task:
        selected_tasks = {str(value).strip().lower() for value in args.only_task}
        unknown_tasks = selected_tasks - set(catalog)
        if unknown_tasks:
            raise ValueError(f"unknown task for --only-task: {sorted(unknown_tasks)}")
        holds = dict(config.get("holds") or {})
        for identity in catalog:
            if identity not in selected_tasks:
                holds[identity] = "excluded by --only-task"
        config["holds"] = holds
    state_path = scheduler_root / "scheduler_state.json"
    state = load_json(state_path) if state_path.is_file() else initial_state(config, catalog)
    merge_catalog(state, catalog)
    # A restarted watcher can inherit durable ``preparing``/``running`` rows
    # whose detached workers have already finished.  Refresh those results
    # before applying an explicit retry request; otherwise a legitimate
    # ``--retry-task`` is rejected solely because the last persisted tick is
    # stale.
    refresh_state(state, config, scheduler_root)
    apply_config_holds(state, config)
    release_holds(state, args.release_hold)
    retry_tasks(state, args.retry_task)
    while True:
        if (
            args.promote_max_active_when_file is not None
            and args.promote_max_active_when_file.is_file()
        ):
            config["max_active"] = int(args.promoted_max_active)
        payload = tick(state, config, execute=args.execute)
        print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
        if not args.watch:
            return 0
        time.sleep(max(5.0, float(config.get("poll_interval_s") or 30.0)))


if __name__ == "__main__":
    raise SystemExit(main())
