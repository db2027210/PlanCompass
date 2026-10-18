#!/usr/bin/env python3
"""Replay PostgreSQL frozen champions in a declared comparable cohort.

This runner performs no search.  It evaluates the shared initial vector and
the already frozen Algorithm A/B/C, GPTuner, and AQETuner champions.  Every
By default every stage starts all four scenes together, with two physical
Docker workers per scene.  A scene/worker subset may be selected explicitly
for paper-domain isolated replays.  Only the seven PostgreSQL planner cost
GUCs may differ between points; all other ``pg_settings`` values and the
database execution data plane are protected by fail-closed fingerprints.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import random
import re
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from binary_plan_resource_tuner import (
    PlanFeatureProbe,
    aggregate_parallel_baseline_validation,
    apply_pg_params,
    initial_params,
    pg_conn_from_worker,
    psql_query,
    read_effective_params,
    restore_worker_from_hybench_template,
    run_real_validation_safe,
    validation_workers,
    worker_with_prepared_restore,
    workload_queries_with_sampling,
)
from shell.pg_fixed_guc_guard import (
    audit_fixed_guc_domain,
    canonical_sha256,
    write_receipt,
)
from shell.run_pg_algorithm_c_scene import (
    ACTIVE_PARAMS,
    DEFAULT_RANGES,
    apply_ssd_wave_qualification,
    physical_valid,
    worker_key,
)
from shell.run_pg_none_external_baseline import slim_validation


CONTRACT = "pg_declared_scene_initial_baseline_frozen_champion_replay_v5"
BASELINE_NORMALIZATION_CONTRACT = "initial_baseline_same_worker_normalized_v1"
SCENES: Tuple[Tuple[str, str], ...] = (
    ("job_memory", "memory"),
    ("tpch_memory", "memory"),
    ("xp_cpu", "cpu"),
    ("xp_io", "io"),
)
METHODS = (
    "algorithm_a",
    "algorithm_b",
    "algorithm_c",
    "gptuner",
    "aqetuner",
)
GENERIC_WORKLOAD_BY_SCENE = {
    "job_memory": "job",
    "tpch_memory": "tpch",
    "xp_cpu": "hybenchxp",
    "xp_io": "hybenchxp",
}
JOB_FIXED_SCHEDULE_MANIFEST = Path(
    "/disk1/resource_adaptive/pg_external_four_scene_baselines_20260825/"
    "20260825_gptuner_aqetuner_four_r1/job_fixed_schedule_authority.json"
)
JOB_FIXED_SCHEDULE_SHA256 = (
    "950fde0959033c8da695fae91aedf56481008ddf5de4c2e781fb00b30fb36513"
)
DEFAULT_SEED = 20260826
DEFAULT_SSD_DEVICE = "nvme1n1"
EXTERNAL_WORKLOAD_SAMPLE_S = 1.0
EXTERNAL_WORKLOAD_GUARD_CONTRACT = "pg_external_workload_contamination_guard_v3"
STANDALONE_BACKEND_MIN_COUNT = 2
STANDALONE_BACKEND_STATE_PERSISTENCE_S = 2.0
_standalone_backend_first_seen: Dict[str, float] = {}
_standalone_backend_lock = threading.Lock()
IO_BASELINE_SSD_WORKER_SCALING_CONTRACT = (
    "io_target_baseline_ssd_minimum_worker_scaling_v1"
)
CONTAINER_START_PREFLIGHT_CONTRACT = "pg_declared_container_start_preflight_v1"
RUNTIME_SOURCE_FILES = (
    ROOT / "binary_plan_resource_tuner.py",
    ROOT / "shell" / "run_pg_algorithm_c_scene.py",
    ROOT / "shell" / "run_pg_none_external_baseline.py",
    ROOT / "shell" / "run_pg_job_fixed_workload_isolated.sh",
    ROOT / "shell" / "run_pg_full_workload_isolated.sh",
    ROOT / "shell" / "run_hybench_native_xp_isolated.sh",
)


def parse_binary_size(value: object) -> int:
    """Parse the compact Docker memory values used by the scene contracts."""
    text = str(value or "").strip().lower()
    if not text:
        return 0
    multipliers = {
        "k": 1024,
        "kb": 1024,
        "m": 1024**2,
        "mb": 1024**2,
        "g": 1024**3,
        "gb": 1024**3,
        "t": 1024**4,
        "tb": 1024**4,
    }
    for suffix in ("kb", "mb", "gb", "tb", "k", "m", "g", "t"):
        if text.endswith(suffix):
            return int(float(text[: -len(suffix)]) * multipliers[suffix])
    return int(float(text))


def normalized_cpuset(value: object) -> List[int]:
    """Expand a Docker cpuset expression into a stable sorted CPU list."""
    cpus: set[int] = set()
    for item in str(value or "").split(","):
        item = item.strip()
        if not item:
            continue
        if "-" in item:
            start_text, end_text = item.split("-", 1)
            start = int(start_text)
            end = int(end_text)
            if end < start:
                raise ValueError(f"invalid cpuset range: {item}")
            cpus.update(range(start, end + 1))
        else:
            cpus.add(int(item))
    return sorted(cpus)


def diskstats_device_snapshot(device: str) -> Dict[str, int]:
    """Read cumulative bytes and busy milliseconds for one block device."""
    for line in Path("/proc/diskstats").read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if len(fields) < 14 or fields[2] != device:
            continue
        return {
            "read_bytes": int(fields[5]) * 512,
            "write_bytes": int(fields[9]) * 512,
            "busy_ms": int(fields[12]),
        }
    raise RuntimeError(f"diskstats device not found: {device}")


def audit_pre_stage_ssd_quiet(
    *,
    device: str,
    sample_s: float,
    maximum_util_pct: float,
) -> Dict[str, Any]:
    """Fail closed if unrelated storage traffic is active before a stage."""
    started_at = time.time()
    before = diskstats_device_snapshot(device)
    monotonic_started = time.perf_counter()
    time.sleep(max(0.1, sample_s))
    elapsed_s = time.perf_counter() - monotonic_started
    after = diskstats_device_snapshot(device)
    util_pct = max(0.0, after["busy_ms"] - before["busy_ms"]) / max(
        elapsed_s * 10.0,
        1e-12,
    )
    receipt = {
        "contract": "pg_common_domain_pre_stage_ssd_quiet_v1",
        "device": device,
        "sample_s": elapsed_s,
        "maximum_util_pct": maximum_util_pct,
        "observed_util_pct": util_pct,
        "read_mib_s": max(0, after["read_bytes"] - before["read_bytes"])
        / elapsed_s
        / (1024.0**2),
        "write_mib_s": max(0, after["write_bytes"] - before["write_bytes"])
        / elapsed_s
        / (1024.0**2),
        "passed": util_pct <= maximum_util_pct,
        "captured_at_epoch_s": started_at,
    }
    receipt["receipt_sha256"] = canonical_sha256(receipt)
    return receipt


def wait_for_post_restore_ssd_quiet(
    *,
    device: str,
    sample_s: float,
    maximum_util_pct: float,
    maximum_wait_s: float,
) -> Dict[str, Any]:
    """Wait until restore writeback has left one full quiet SSD window."""
    started_at = time.time()
    monotonic_started = time.perf_counter()
    observations: List[Dict[str, Any]] = []
    while True:
        observation = audit_pre_stage_ssd_quiet(
            device=device,
            sample_s=sample_s,
            maximum_util_pct=maximum_util_pct,
        )
        observations.append(observation)
        elapsed_s = time.perf_counter() - monotonic_started
        if observation.get("passed") is True:
            passed = True
            break
        if elapsed_s >= maximum_wait_s:
            passed = False
            break
    receipt = {
        "contract": "pg_common_domain_post_restore_ssd_quiet_v1",
        "device": device,
        "sample_s": sample_s,
        "maximum_util_pct": maximum_util_pct,
        "maximum_wait_s": maximum_wait_s,
        "waited_s": time.perf_counter() - monotonic_started,
        "observation_count": len(observations),
        "observations": observations,
        "passed": passed,
        "started_at_epoch_s": started_at,
        "finished_at_epoch_s": time.time(),
    }
    receipt["receipt_sha256"] = canonical_sha256(receipt)
    return receipt


def _parse_external_workload_candidates(
    ps_output: str,
) -> List[Tuple[Dict[str, Any], str]]:
    """Return relevant process rows and their driver/backend kind."""
    candidates: List[Tuple[Dict[str, Any], str]] = []
    for line in ps_output.splitlines():
        fields = line.strip().split(None, 3)
        if len(fields) != 4:
            continue
        pid_text, user, elapsed_text, command = fields
        # Only the exact plain-idle backend state is inactive. Keep active
        # queries, idle-in-transaction sessions and runnable controllers guarded.
        is_benchbase_backend = (
            command.startswith("postgres: postgres benchbase ")
            and not command.endswith(" idle")
        )
        benchmark_match = re.search(
            r"(?:^|\s)-b(?:\s+|=)([^\s]+)",
            command,
        )
        benchmark_names = {
            name.strip().lower()
            for name in (
                benchmark_match.group(1).split(",")
                if benchmark_match is not None
                else []
            )
            if name.strip()
        }
        is_chbenchmark_client = (
            "benchbase.jar" in command
            and bool({"tpcc", "chbenchmark"} & benchmark_names)
        )
        is_chbenchmark_controller = (
            command.strip() == "bash run-join.sh"
            or (
                "RUN_WORKLOADS=" in command
                and "bash run-join.sh" in command
            )
        )
        if not (is_benchbase_backend or is_chbenchmark_client or is_chbenchmark_controller):
            continue
        try:
            pid = int(pid_text)
            elapsed_s = int(elapsed_text)
        except ValueError:
            continue
        kind = "backend"
        if is_chbenchmark_client:
            kind = "client"
        elif is_chbenchmark_controller:
            kind = "controller"
        candidates.append(({
            "pid": pid,
            "user": user,
            "elapsed_s": elapsed_s,
            "command": command,
        }, kind))
    return candidates


def _filter_external_workload_candidates(
    candidates: Sequence[Tuple[Dict[str, Any], str]],
    *,
    now: float,
    standalone_backend_first_seen: Optional[Dict[str, float]],
) -> List[Dict[str, Any]]:
    """Apply the fail-closed driver, quorum, and observed-persistence policy."""
    has_driver = any(kind != "backend" for _, kind in candidates)
    backend_rows = [row for row, kind in candidates if kind == "backend"]
    if has_driver or len(backend_rows) >= STANDALONE_BACKEND_MIN_COUNT:
        if standalone_backend_first_seen is not None:
            standalone_backend_first_seen.clear()
        return [row for row, _ in candidates]

    if len(backend_rows) != 1:
        if standalone_backend_first_seen is not None:
            standalone_backend_first_seen.clear()
        return [row for row, kind in candidates if kind != "backend"]

    # ``ps etimes`` is the lifetime of the PostgreSQL session process, not the
    # duration of its current query state. Measure persistence with consecutive
    # guard observations of the exact PID/state instead.
    if standalone_backend_first_seen is None:
        return [row for row, kind in candidates if kind != "backend"]
    backend = backend_rows[0]
    key = f"{backend['pid']}|{backend['command']}"
    first_seen = standalone_backend_first_seen.get(key, now)
    standalone_backend_first_seen.clear()
    standalone_backend_first_seen[key] = first_seen
    matches = [row for row, kind in candidates if kind != "backend"]
    if now - first_seen >= STANDALONE_BACKEND_STATE_PERSISTENCE_S:
        matches.append(backend)
    return matches


def parse_external_workload_processes(ps_output: str) -> List[Dict[str, Any]]:
    """Return snapshot-proven CH-benCHmark/BenchBase workload processes."""
    candidates = _parse_external_workload_candidates(ps_output)

    # A real local BenchBase workload always has a Java client/controller and
    # normally many active backends. Some external orchestration scripts leave
    # a database alive and issue a sub-second, single-backend SELECT between
    # completed runs. Treating that probe as a full workload resets a long
    # quiet gate forever. Snapshot classification remains fail-closed for any
    # client/controller or two concurrent backends. The scanner below adds the
    # observed-persistence rule for a lone backend.
    return _filter_external_workload_candidates(
        candidates,
        now=0.0,
        standalone_backend_first_seen=None,
    )


def pid_descends_from(pid: int, ancestor_pids: Set[int]) -> bool:
    """Return whether a host PID belongs to one of the declared containers."""
    seen: Set[int] = set()
    current = pid
    while current > 1 and current not in seen:
        if current in ancestor_pids:
            return True
        seen.add(current)
        try:
            fields = Path(f"/proc/{current}/stat").read_text(encoding="utf-8").split()
            current = int(fields[3])
        except (FileNotFoundError, IndexError, OSError, ValueError):
            return False
    return current in ancestor_pids


def scan_external_workload_processes(
    *,
    ignored_backend_ancestor_pids: Optional[Set[int]] = None,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Inspect the host process table without mutating an external workload."""
    try:
        completed = subprocess.run(
            ["ps", "-eo", "pid=,user=,etimes=,args="],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        with _standalone_backend_lock:
            _standalone_backend_first_seen.clear()
        return [], [f"host process scan failed: {type(exc).__name__}: {exc}"]
    candidates = _parse_external_workload_candidates(completed.stdout)
    ignored = set(ignored_backend_ancestor_pids or set())
    if ignored:
        # Only PostgreSQL backends proven to be descendants of this experiment's
        # declared container postmasters are owned. Driver/controller processes
        # remain external even when they connect to an owned worker.
        candidates = [
            (row, kind)
            for row, kind in candidates
            if not (kind == "backend" and pid_descends_from(int(row["pid"]), ignored))
        ]
    with _standalone_backend_lock:
        matches = _filter_external_workload_candidates(
            candidates,
            now=time.monotonic(),
            standalone_backend_first_seen=_standalone_backend_first_seen,
        )
    return matches, []


class RetryableInterference(RuntimeError):
    """Transient host interference before any workload has been launched."""


def wait_for_external_quiet(
    output_dir: Path,
    label: str,
    *,
    quiet_s: float,
    should_stop: Callable[[], bool],
    ignored_backend_ancestor_pids: Optional[Set[int]] = None,
) -> bool:
    """Require a continuous quiet interval; retain every activity observation."""
    quiet_since = time.monotonic()
    last_notice = -math.inf
    while not should_stop():
        matches, errors = (
            scan_external_workload_processes(
                ignored_backend_ancestor_pids=ignored_backend_ancestor_pids,
            )
            if ignored_backend_ancestor_pids is not None
            else scan_external_workload_processes()
        )
        now = time.monotonic()
        if matches or errors:
            quiet_since = now
            append_jsonl(output_dir / "external_wait_events.jsonl", {
                "time": time.time(), "label": label,
                "matches": matches, "errors": errors,
            })
        elapsed = now - quiet_since
        passed = not matches and not errors and elapsed >= quiet_s
        write_json(output_dir / "external_wait_status.json", {
            "phase": "released" if passed else "waiting_for_external_quiet",
            "label": label, "updated_at_epoch_s": time.time(),
            "quiet_elapsed_s": elapsed, "required_quiet_s": quiet_s,
            "active_matches": matches, "scan_errors": errors,
        })
        if passed:
            return True
        if now - last_notice >= 30.0:
            print(
                f"[{label}] waiting for external quiet: active={len(matches)} "
                f"errors={len(errors)} quiet={elapsed:.0f}/{quiet_s:.0f}s",
                flush=True,
            )
            last_notice = now
        time.sleep(1.0)
    return False


def monitor_external_workloads(
    stop_event: threading.Event,
    receipt: Dict[str, Any],
    sample_s: float,
    ignored_backend_ancestor_pids: Optional[Set[int]] = None,
) -> None:
    """Continuously record external DB workloads that can contaminate a stage."""
    detections: Dict[str, Dict[str, Any]] = {}
    errors: List[str] = []
    detection_sample_count = 0
    sample_count = 0
    while True:
        matches, scan_errors = (
            scan_external_workload_processes(
                ignored_backend_ancestor_pids=ignored_backend_ancestor_pids,
            )
            if ignored_backend_ancestor_pids is not None
            else scan_external_workload_processes()
        )
        sample_count += 1
        if matches:
            detection_sample_count += 1
        for match in matches:
            key = f"{match['pid']}|{match['command']}"
            now = time.time()
            row = detections.setdefault(
                key,
                {
                    **match,
                    "first_detected_at_epoch_s": now,
                    "last_detected_at_epoch_s": now,
                },
            )
            row["last_detected_at_epoch_s"] = now
        errors.extend(scan_errors)
        receipt.update(
            {
                "sample_count": sample_count,
                "detection_sample_count": detection_sample_count,
                "detections": sorted(
                    detections.values(), key=lambda row: row["pid"]
                ),
                "errors": list(errors),
                "contaminated": bool(detections or errors),
            }
        )
        if stop_event.wait(max(0.1, sample_s)):
            break
    receipt.update(
        {
            "sample_count": sample_count,
            "detection_sample_count": detection_sample_count,
            "detections": sorted(detections.values(), key=lambda row: row["pid"]),
            "errors": errors,
            "finished_at_epoch_s": time.time(),
            "passed": not detections and not errors,
            "contaminated": bool(detections or errors),
        }
    )
    receipt["receipt_sha256"] = canonical_sha256(receipt)


def ensure_declared_containers_running(
    contracts: Mapping[str, Mapping[str, Any]],
    *,
    readiness_timeout_s: float = 180.0,
) -> Dict[str, Any]:
    """Start stopped declared containers and wait for PostgreSQL readiness."""
    container_ports: Dict[str, int] = {}
    for contract in contracts.values():
        for worker in contract["workers"]:
            name = str(worker.get("container") or "")
            port = int(worker.get("port") or 5432)
            if name in container_ports and container_ports[name] != port:
                raise RuntimeError(
                    f"declared container {name} has conflicting PostgreSQL ports"
                )
            if not 0 < port <= 65535:
                raise RuntimeError(
                    f"declared container {name} has invalid PostgreSQL port {port}"
                )
            container_ports[name] = port
    containers = sorted(container_ports)
    if not containers or any(not name for name in containers):
        raise RuntimeError("container start preflight found an empty name")
    inspect_proc = subprocess.run(
        ["docker", "inspect", *containers],
        text=True,
        capture_output=True,
        check=False,
        timeout=60.0,
    )
    if inspect_proc.returncode != 0:
        raise RuntimeError(
            "container start preflight inspect failed: "
            f"returncode={inspect_proc.returncode} "
            f"stderr={inspect_proc.stderr.strip()}"
        )
    documents = json.loads(inspect_proc.stdout)
    by_name = {
        str(document.get("Name") or "").lstrip("/"): document
        for document in documents
    }
    missing = sorted(set(containers) - set(by_name))
    if missing:
        raise RuntimeError(
            "container start preflight missing declared containers: "
            + ", ".join(missing)
        )
    initially_running = sorted(
        name
        for name in containers
        if bool((by_name[name].get("State") or {}).get("Running"))
    )
    started: List[str] = []
    for name in containers:
        if name in initially_running:
            continue
        start_proc = subprocess.run(
            ["docker", "start", name],
            text=True,
            capture_output=True,
            check=False,
            timeout=120.0,
        )
        if start_proc.returncode != 0:
            raise RuntimeError(
                f"failed to start declared container {name}: "
                f"returncode={start_proc.returncode} "
                f"stderr={start_proc.stderr.strip()}"
            )
        started.append(name)

    deadline = time.monotonic() + max(1.0, readiness_timeout_s)
    pending = set(containers)
    readiness_attempts = {name: 0 for name in containers}
    readiness_probe_timeouts = {name: 0 for name in containers}
    while pending:
        for name in sorted(pending):
            readiness_attempts[name] += 1
            try:
                ready_proc = subprocess.run(
                    [
                        "docker", "exec", name, "pg_isready",
                        "-U", "postgres", "-d", "postgres",
                        "-p", str(container_ports[name]),
                    ],
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=15.0,
                )
            except subprocess.TimeoutExpired:
                readiness_probe_timeouts[name] += 1
                continue
            if ready_proc.returncode == 0:
                pending.remove(name)
        if not pending:
            break
        if time.monotonic() >= deadline:
            raise RuntimeError(
                "PostgreSQL readiness timed out for declared containers: "
                + ", ".join(sorted(pending))
            )
        time.sleep(2.0)

    receipt = {
        "contract": CONTAINER_START_PREFLIGHT_CONTRACT,
        "passed": True,
        "declared_containers": containers,
        "initially_running_containers": initially_running,
        "started_containers": started,
        "ready_containers": containers,
        "postgres_ports": container_ports,
        "readiness_attempts": readiness_attempts,
        "readiness_probe_timeouts": readiness_probe_timeouts,
        "starts_only_declared_containers": True,
        "captured_at_epoch_s": time.time(),
    }
    receipt["receipt_sha256"] = canonical_sha256(receipt)
    return receipt


def audit_container_resource_domain(
    contracts: Mapping[str, Mapping[str, Any]],
    *,
    expected_semantic_sha256: Optional[str] = None,
) -> Dict[str, Any]:
    """Fail closed when a scene container drifts from its CPU/Memory contract."""
    containers = sorted(
        {
            str(worker.get("container") or "")
            for contract in contracts.values()
            for worker in contract["workers"]
        }
    )
    if not containers or any(not name for name in containers):
        raise RuntimeError("container resource audit found an empty container name")
    proc = subprocess.run(
        ["docker", "inspect", *containers],
        text=True,
        capture_output=True,
        check=False,
        timeout=60.0,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "docker resource audit failed: "
            f"returncode={proc.returncode} stderr={proc.stderr.strip()}"
        )
    documents = json.loads(proc.stdout)
    by_name = {
        str(document.get("Name") or "").lstrip("/"): document
        for document in documents
    }
    rows: List[Dict[str, Any]] = []
    errors: List[str] = []
    for scene, contract in sorted(contracts.items()):
        for worker in sorted(contract["workers"], key=worker_key):
            name = str(worker.get("container") or "")
            document = by_name.get(name)
            if document is None:
                errors.append(f"{scene}/{worker_key(worker)}: missing container {name}")
                continue
            host = document.get("HostConfig") or {}
            state = document.get("State") or {}
            expected_cpuset = normalized_cpuset(worker.get("cpuset_cpus"))
            actual_cpuset = normalized_cpuset(host.get("CpusetCpus"))
            expected_nano_cpus = int(float(worker.get("cpus") or 0.0) * 1_000_000_000)
            actual_nano_cpus = int(host.get("NanoCpus") or 0)
            expected_memory = parse_binary_size(worker.get("memory"))
            actual_memory = int(host.get("Memory") or 0)
            if not bool(state.get("Running")):
                errors.append(f"{scene}/{worker_key(worker)}: container is not running")
            if actual_cpuset != expected_cpuset:
                errors.append(
                    f"{scene}/{worker_key(worker)}: cpuset actual={host.get('CpusetCpus')} "
                    f"expected={worker.get('cpuset_cpus')}"
                )
            if actual_nano_cpus != expected_nano_cpus:
                errors.append(
                    f"{scene}/{worker_key(worker)}: NanoCpus actual={actual_nano_cpus} "
                    f"expected={expected_nano_cpus}"
                )
            if actual_memory != expected_memory:
                errors.append(
                    f"{scene}/{worker_key(worker)}: memory actual={actual_memory} "
                    f"expected={expected_memory}"
                )
            rows.append(
                {
                    "scene": scene,
                    "worker": worker_key(worker),
                    "container": name,
                    "running": bool(state.get("Running")),
                    "cpuset_cpus": str(host.get("CpusetCpus") or ""),
                    "nano_cpus": actual_nano_cpus,
                    "memory_bytes": actual_memory,
                    "memory_swap_bytes": int(host.get("MemorySwap") or 0),
                    "shm_size_bytes": int(host.get("ShmSize") or 0),
                    "network_mode": str(host.get("NetworkMode") or ""),
                }
            )
    semantic = {"workers": rows}
    semantic_sha256 = canonical_sha256(semantic)
    if (
        expected_semantic_sha256 is not None
        and semantic_sha256 != expected_semantic_sha256
    ):
        errors.append(
            "container resource fingerprint drift: "
            f"actual={semantic_sha256} expected={expected_semantic_sha256}"
        )
    receipt = {
        "contract": "pg_common_domain_container_resource_guard_v1",
        "semantic": semantic,
        "semantic_sha256": semantic_sha256,
        "errors": errors,
        "passed": not errors,
        "captured_at_epoch_s": time.time(),
    }
    receipt["receipt_sha256"] = canonical_sha256(receipt)
    if errors:
        raise RuntimeError("container resource domain audit failed: " + "; ".join(errors))
    return receipt


def read_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"expected JSON object: {path}")
    return value


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def append_jsonl(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def text_sha256(value: str) -> str:
    """Hash a stable PostgreSQL catalog serialization."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def quote_pg_identifier(value: str) -> str:
    """Quote one catalog-provided PostgreSQL identifier."""
    return '"' + value.replace('"', '""') + '"'


def database_execution_fingerprint(
    cfg: Mapping[str, Any],
    worker: Mapping[str, Any],
) -> Dict[str, Any]:
    """Fingerprint the restored database state that can change a query plan/QPS.

    The fingerprint deliberately excludes cluster-local OIDs and transaction
    IDs.  Equality requires exact table cardinalities, index definitions,
    PostgreSQL version, encoding, and collation.  Physical relation layout and
    planner-statistics hashes are retained as diagnostics because a logical
    cross-cluster restore can legitimately rebuild their storage details.
    This is substantially stronger than checking only the database name or
    the seven tunable GUCs.
    """
    pg = pg_conn_from_worker(dict(cfg), dict(worker))
    validation_cfg = ((cfg.get("binary_plan_tuning") or {}).get("validation") or {})
    hybench_cfg = validation_cfg.get("hybench_xp") or {}
    template_db = str(hybench_cfg.get("template_db") or f"{pg.dbname}_base")
    escaped_template = template_db.replace("'", "''")
    template_row = psql_query(
        pg,
        (
            "SELECT datistemplate, datallowconn, pg_database_size(datname) "
            "FROM pg_database "
            f"WHERE datname = '{escaped_template}';"
        ),
        dbname="postgres",
        timeout_s=60.0,
    ).strip()
    template_fields = template_row.split("\t")
    if len(template_fields) != 3:
        raise RuntimeError(
            f"missing template metadata for {worker_key(worker)}: {template_db}"
        )
    if template_fields[:2] != ["t", "f"]:
        raise RuntimeError(
            f"template is not sealed for {worker_key(worker)}: "
            f"{template_db} metadata={template_row!r}"
        )

    relation_rows = psql_query(
        pg,
        (
            "SELECT n.nspname, c.relname, c.relkind::text, "
            "c.reltuples::bigint, c.relpages, pg_relation_size(c.oid), "
            "pg_total_relation_size(c.oid), coalesce(c.reloptions::text, '') "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast') "
            "AND c.relkind IN ('r', 'm', 'i') "
            "ORDER BY n.nspname, c.relname, c.relkind;"
        ),
        timeout_s=120.0,
    ).strip()
    index_rows = psql_query(
        pg,
        (
            "SELECT schemaname, tablename, indexname, indexdef "
            "FROM pg_indexes "
            "WHERE schemaname NOT IN ('pg_catalog', 'information_schema') "
            "ORDER BY schemaname, tablename, indexname;"
        ),
        timeout_s=120.0,
    ).strip()
    statistics_rows = psql_query(
        pg,
        (
            "SELECT schemaname, tablename, attname, inherited, null_frac, "
            "avg_width, n_distinct, coalesce(most_common_vals::text, ''), "
            "coalesce(most_common_freqs::text, ''), "
            "coalesce(histogram_bounds::text, ''), correlation, "
            "coalesce(most_common_elems::text, ''), "
            "coalesce(most_common_elem_freqs::text, ''), "
            "coalesce(elem_count_histogram::text, '') "
            "FROM pg_stats "
            "WHERE schemaname NOT IN ('pg_catalog', 'information_schema') "
            "ORDER BY schemaname, tablename, attname, inherited;"
        ),
        timeout_s=180.0,
    ).strip()
    table_rows = psql_query(
        pg,
        (
            "SELECT n.nspname, c.relname "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast') "
            "AND c.relkind IN ('r', 'm') "
            "ORDER BY n.nspname, c.relname;"
        ),
        timeout_s=120.0,
    ).strip()
    exact_cardinalities: Dict[str, int] = {}
    for row in table_rows.splitlines():
        if not row:
            continue
        fields = row.split("\t", 1)
        if len(fields) != 2:
            raise RuntimeError(f"invalid table catalog row: {row!r}")
        schema_name, table_name = fields
        qualified = (
            f"{quote_pg_identifier(schema_name)}."
            f"{quote_pg_identifier(table_name)}"
        )
        count_text = psql_query(
            pg,
            f"SELECT count(*) FROM {qualified};",
            timeout_s=900.0,
        ).strip()
        exact_cardinalities[f"{schema_name}.{table_name}"] = int(count_text)

    semantic = {
        "server_version_num": psql_query(
            pg,
            "SELECT current_setting('server_version_num');",
            timeout_s=60.0,
        ).strip(),
        "database_encoding_collation": psql_query(
            pg,
            (
                "SELECT pg_encoding_to_char(encoding), datcollate, datctype "
                "FROM pg_database WHERE datname = current_database();"
            ),
            timeout_s=60.0,
        ).strip(),
        "index_definitions_sha256": text_sha256(index_rows),
        "exact_table_cardinalities": exact_cardinalities,
    }
    return {
        "worker": worker_key(worker),
        "host": pg.host,
        "port": pg.port,
        "database": pg.dbname,
        "template_database": template_db,
        "template_is_template": True,
        "template_allows_connections": False,
        "template_database_size_bytes": int(template_fields[2]),
        "relation_layout_sha256": text_sha256(relation_rows),
        "planner_statistics_sha256": text_sha256(statistics_rows),
        "semantic": semantic,
        "semantic_sha256": canonical_sha256(semantic),
    }


def validate_database_cohort_fingerprints(
    fingerprints: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> None:
    """Reject a scene when its selected workers use different data planes."""
    errors: List[str] = []
    for scene, rows in sorted(fingerprints.items()):
        worker_names = sorted(rows)
        if not worker_names:
            errors.append(f"{scene}: no selected workers")
            continue
        hashes = {str(rows[name].get("semantic_sha256") or "") for name in worker_names}
        if len(hashes) != 1 or "" in hashes:
            cardinalities = {
                name: ((rows[name].get("semantic") or {}).get("exact_table_cardinalities") or {})
                for name in worker_names
            }
            errors.append(
                f"{scene}: worker database fingerprints differ; "
                f"exact_table_cardinalities={cardinalities}"
            )
    if errors:
        raise RuntimeError("database execution cohort audit failed: " + "; ".join(errors))


def database_plan_fingerprints(
    contract: Mapping[str, Any],
    worker: Mapping[str, Any],
) -> Dict[str, Any]:
    """Capture workload-wide plan fingerprints for every replayed parameter point."""
    cfg = copy.deepcopy(dict(contract["cfg"]))
    cfg["postgres"] = {
        "host": str(worker.get("host") or "127.0.0.1"),
        "port": str(worker.get("port") or "5432"),
        "user": str(worker.get("user") or "postgres"),
        "dbname": str(worker.get("dbname") or "postgres"),
        "password": str(worker.get("password") or ""),
    }
    probe = PlanFeatureProbe(cfg)
    queries, sampling = workload_queries_with_sampling(cfg, probe)
    if sampling.get("sampling_applied"):
        raise RuntimeError(
            f"plan fingerprint sampled workload for {worker_key(worker)}: {sampling}"
        )
    points = {
        "common_baseline_00": dict(contract["defaults"]),
        **{
            method: dict(row["params"])
            for method, row in contract["methods"].items()
        },
    }
    point_rows: Dict[str, Any] = {}
    for label, params in points.items():
        result = probe.probe(queries, params)
        errors = result.get("errors") or []
        if errors or int(result.get("explained_query_count") or 0) != len(queries):
            raise RuntimeError(
                f"incomplete plan fingerprint for {worker_key(worker)}/{label}: "
                f"explained={result.get('explained_query_count')} "
                f"expected={len(queries)} errors={errors[:3]}"
            )
        signatures = result.get("plan_signatures") or {}
        point_rows[label] = {
            "query_count": len(queries),
            "plan_signatures_sha256": canonical_sha256(signatures),
            "plan_signatures": signatures,
            "exact_plan_fingerprint": (
                (result.get("exact_plan_metadata") or {}).get("fingerprint")
            ),
        }
    return {
        "worker": worker_key(worker),
        "points": point_rows,
        "sampling": sampling,
    }


def validate_database_plan_cohort(
    plans: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> None:
    """Require identical full-workload plan families across selected workers."""
    errors: List[str] = []
    for scene, workers in sorted(plans.items()):
        worker_names = sorted(workers)
        if not worker_names:
            errors.append(f"{scene}: no plan workers")
            continue
        reference_name = worker_names[0]
        reference = workers[reference_name].get("points") or {}
        for worker_name in worker_names[1:]:
            candidate = workers[worker_name].get("points") or {}
            if set(reference) != set(candidate):
                errors.append(f"{scene}: plan point coverage mismatch")
                continue
            for label in sorted(reference):
                reference_hash = str(
                    (reference[label] or {}).get("plan_signatures_sha256") or ""
                )
                candidate_hash = str(
                    (candidate[label] or {}).get("plan_signatures_sha256") or ""
                )
                if reference_hash and reference_hash == candidate_hash:
                    continue
                errors.append(
                    f"{scene}/{label}: worker plan fingerprints differ "
                    f"{reference_name}={reference_hash} "
                    f"{worker_name}={candidate_hash}"
                )
    if errors:
        raise RuntimeError("database plan cohort audit failed: " + "; ".join(errors))


def audit_database_execution_cohort(
    contracts: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Any]:
    """Capture and validate all database fingerprints in parallel."""
    fingerprints: Dict[str, Dict[str, Dict[str, Any]]] = {
        scene: {} for scene in contracts
    }
    with ThreadPoolExecutor(max_workers=8) as executor:
        future_map = {
            executor.submit(
                database_execution_fingerprint,
                contract["cfg"],
                worker,
            ): (scene, worker)
            for scene, contract in contracts.items()
            for worker in contract["workers"]
        }
        for future in as_completed(future_map):
            scene, worker = future_map[future]
            fingerprints[scene][worker_key(worker)] = future.result()
    validate_database_cohort_fingerprints(fingerprints)
    plans: Dict[str, Dict[str, Dict[str, Any]]] = {
        scene: {} for scene in contracts
    }
    with ThreadPoolExecutor(max_workers=8) as executor:
        future_map = {
            executor.submit(database_plan_fingerprints, contract, worker): (
                scene,
                worker,
            )
            for scene, contract in contracts.items()
            for worker in contract["workers"]
        }
        for future in as_completed(future_map):
            scene, worker = future_map[future]
            plans[scene][worker_key(worker)] = future.result()
    validate_database_plan_cohort(plans)
    receipt = {
        "contract": "pg_database_execution_cohort_fingerprint_v1",
        "scenes": fingerprints,
        "plans": plans,
        "passed": True,
        "captured_at_epoch_s": time.time(),
    }
    receipt["receipt_sha256"] = canonical_sha256(receipt)
    return receipt


def exact_params(value: Mapping[str, Any], *, source: str) -> Dict[str, float]:
    params = {str(name): float(number) for name, number in value.items()}
    if set(params) != set(ACTIVE_PARAMS):
        raise RuntimeError(
            f"{source} does not contain exactly the seven cost GUCs: "
            f"observed={sorted(params)} expected={sorted(ACTIVE_PARAMS)}"
        )
    for name, number in params.items():
        low, high = DEFAULT_RANGES[name]
        if not math.isfinite(number) or number < low or number > high:
            raise RuntimeError(
                f"{source} parameter outside shared range: "
                f"{name}={number} not in [{low}, {high}]"
            )
    return params


def champion_params(authority: Mapping[str, Any], method: str) -> Dict[str, float]:
    if method in {"algorithm_a", "algorithm_b", "algorithm_c"}:
        champion = authority.get("final_champion") or {}
        value = (champion.get("normalized") or {}).get("params") or champion.get("params")
        if method == "algorithm_c" and not isinstance(value, Mapping):
            trial = authority.get("trial") or {}
            validation = trial.get("validation") or {}
            if (
                authority.get("status") != "validated"
                or trial.get("valid_for_quality_or_efficiency_claim") is not True
                or validation.get("passed") is not True
            ):
                raise RuntimeError("terminal adaptive Algorithm C champion is not validated")
            value = (trial.get("finalist") or {}).get("params")
    else:
        champion = authority.get("validated_champion") or {}
        value = champion.get("params")
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{method} authority has no frozen champion params")
    return exact_params(value, source=f"{method} authority")


def default_sources(
    *,
    methods: Sequence[str] = METHODS,
    algorithm_c_formal_root: Optional[Path] = None,
    config_root: Optional[Path] = None,
    gptuner_xp_io_authority: Optional[Path] = None,
    aqetuner_xp_io_authority: Optional[Path] = None,
) -> Dict[str, Any]:
    selected_methods = tuple(methods)
    if not selected_methods or len(set(selected_methods)) != len(selected_methods):
        raise RuntimeError("common-domain methods must be non-empty and unique")
    if any(method not in METHODS for method in selected_methods):
        raise RuntimeError(f"unsupported common-domain method set: {selected_methods}")
    abc_roots = {
        "algorithm_a": Path(
            "/disk1/resource_adaptive/pg_algorithm_a_four_full_factorial_20260816/"
            "20260816_algorithm_a_full_factorial_globalrank_labels_clean_r5"
        ),
        "algorithm_b": Path(
            "/disk1/resource_adaptive/pg_algorithm_b_four_grid_20260814/"
            "20260823_algorithm_b_training_boundary_clean_r12"
        ),
        "algorithm_c": Path(
            "/disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809/"
            "20260823_algorithm_c_training_boundary_clean_r75"
        ),
    }
    if algorithm_c_formal_root is not None:
        formal_root = algorithm_c_formal_root.resolve()
        launcher_path = formal_root / "launcher_status.json"
        launcher = read_json(launcher_path) if launcher_path.is_file() else {}
        if (
            launcher.get("phase") != "finished"
            or any(int(value) != 0 for value in (launcher.get("returncodes") or {}).values())
            or set((launcher.get("returncodes") or {}).keys())
            != {scene for scene, _target in SCENES}
        ):
            raise RuntimeError(
                "adaptive Algorithm C must be successfully terminal before A/B/C common-domain replay"
            )
        abc_roots["algorithm_c"] = formal_root
    external_root = Path(
        "/disk1/resource_adaptive/pg_external_four_scene_baselines_20260825/"
        "20260825_gptuner_aqetuner_four_r1"
    )
    execution_config_root = (
        config_root.resolve()
        if config_root is not None
        else abc_roots["algorithm_c"]
    )
    sources = {
        "configs": {
            scene: execution_config_root / scene / "optimizer_config.json"
            for scene, _target in SCENES
        },
        "authorities": {
            method: {
                scene: (
                    root / scene / (
                        "budgeted_formal_result.json"
                        if method == "algorithm_c" and algorithm_c_formal_root is not None
                        else "authority_manifest.json"
                    )
                    if method.startswith("algorithm_")
                    else external_root / method / scene / "authority_manifest.json"
                )
                for scene, _target in SCENES
            }
            for method, root in {
                **abc_roots,
                "gptuner": external_root / "gptuner",
                "aqetuner": external_root / "aqetuner",
            }.items() if method in selected_methods
        },
    }
    if gptuner_xp_io_authority is not None:
        sources["authorities"]["gptuner"]["xp_io"] = (
            gptuner_xp_io_authority.resolve()
        )
    if aqetuner_xp_io_authority is not None:
        sources["authorities"]["aqetuner"]["xp_io"] = (
            aqetuner_xp_io_authority.resolve()
        )
    return sources


def scene_contracts(
    *,
    methods: Sequence[str] = METHODS,
    algorithm_c_formal_root: Optional[Path] = None,
    config_root: Optional[Path] = None,
    gptuner_xp_io_authority: Optional[Path] = None,
    aqetuner_xp_io_authority: Optional[Path] = None,
) -> Dict[str, Dict[str, Any]]:
    selected_methods = tuple(methods)
    sources = default_sources(
        methods=selected_methods,
        algorithm_c_formal_root=algorithm_c_formal_root,
        config_root=config_root,
        gptuner_xp_io_authority=gptuner_xp_io_authority,
        aqetuner_xp_io_authority=aqetuner_xp_io_authority,
    )
    result: Dict[str, Dict[str, Any]] = {}
    for scene, target in SCENES:
        config_path = Path(sources["configs"][scene]).resolve()
        cfg = read_json(config_path)
        workers = validation_workers(cfg, target)
        if len(workers) != 2 or len({worker_key(worker) for worker in workers}) != 2:
            raise RuntimeError(f"{scene} must have exactly two distinct workers")
        defaults = exact_params(initial_params(cfg), source=f"{scene} initial_params")
        method_rows: Dict[str, Any] = {}
        for method in selected_methods:
            authority_path = Path(sources["authorities"][method][scene]).resolve()
            authority = read_json(authority_path)
            if str(authority.get("scene") or "") != scene:
                raise RuntimeError(f"authority scene mismatch: {authority_path}")
            params = champion_params(authority, method)
            if method in {"gptuner", "aqetuner"}:
                if authority.get("parameter_names") != list(ACTIVE_PARAMS):
                    raise RuntimeError(f"{method}/{scene} parameter names mismatch")
                expected_ranges = {
                    name: list(DEFAULT_RANGES[name]) for name in ACTIVE_PARAMS
                }
                if authority.get("parameter_ranges") != expected_ranges:
                    raise RuntimeError(f"{method}/{scene} parameter ranges mismatch")
            method_rows[method] = {
                "params": params,
                "authority_path": str(authority_path),
                "authority_file_sha256": file_sha256(authority_path),
            }
        result[scene] = {
            "target": target,
            "config_path": str(config_path),
            "config_sha256": file_sha256(config_path),
            "cfg": cfg,
            "workers": workers,
            "defaults": defaults,
            "methods": method_rows,
        }
    return result


def stage_params(
    contracts: Mapping[str, Mapping[str, Any]],
    stage: str,
) -> Dict[str, Dict[str, float]]:
    if stage.startswith("common_baseline"):
        return {
            scene: copy.deepcopy(dict(contract["defaults"]))
            for scene, contract in contracts.items()
        }
    return {
        scene: copy.deepcopy(dict(contract["methods"][stage]["params"]))
        for scene, contract in contracts.items()
    }


def workload_environment(scene: str, output_dir: Path) -> Dict[str, str]:
    """Return an isolated environment contract for one physical workload."""
    environment = {
        "GENERIC_WORKLOAD": GENERIC_WORKLOAD_BY_SCENE[scene],
        "GENERIC_RUN_SECONDS": "0",
    }
    if scene == "job_memory":
        environment.update(
            {
                "JOB_FIXED_SCHEDULE_MANIFEST": str(JOB_FIXED_SCHEDULE_MANIFEST),
                "JOB_FIXED_SCHEDULE_SHA256": JOB_FIXED_SCHEDULE_SHA256,
                "JOB_WORKERS": "2",
                "JOB_MAX_CONCURRENT_LONG_QUERIES": "1",
                "JOB_MAX_CONCURRENT_LONG_QUERIES_PER_FAMILY": "1",
                "JOB_TARGET_WALL_TIME_S": "900",
            }
        )
    elif scene == "tpch_memory":
        environment.update(
            {
                "BENCHBASE_SOURCE_ROOT": "/home/anonymous/benchbase-postgres",
                "BENCHBASE_RUNTIME_ROOT": str(output_dir / "benchbase_runtime"),
                "BENCHBASE_SCALEFACTOR": "5",
                "BENCHBASE_TPCH_WORKERS": "1",
                "BENCHBASE_JAVA_XMX": "-Xmx2g",
                "TPCH_TARGET_WALL_TIME_S": "900",
            }
        )
    return environment


def host_ssd_is_informational_for_scene(
    contract: Mapping[str, Any],
) -> bool:
    """Whether other cohort lanes make host SSD utilization non-attributable."""
    cfg = contract.get("cfg") if isinstance(contract.get("cfg"), Mapping) else {}
    algorithm_cfg = (
        cfg.get("algorithm_c")
        if isinstance(cfg.get("algorithm_c"), Mapping)
        else {}
    )
    gate_cfg = (
        algorithm_cfg.get("ssd_replay_qualification")
        if isinstance(algorithm_cfg.get("ssd_replay_qualification"), Mapping)
        else {}
    )
    return bool(
        str(contract.get("target") or "") != "io"
        and str(gate_cfg.get("non_target_shared_ssd_policy") or "")
        == "container_io_capacity"
    )


def apply_worker_scaled_io_baseline_ssd_gate(
    contracts: Dict[str, Dict[str, Any]],
    *,
    enabled: bool,
    calibration_workers: int,
) -> Dict[str, Any]:
    """Scale the paper's aggregate XP-I/O baseline floor to replay width.

    The source config remains immutable and hash-checked. This opt-in is
    deliberately restricted to an isolated XP-I/O replay, because the 65%
    source floor was calibrated with two simultaneous XP-I/O workers while
    the paper-domain replay uses one.
    """
    receipt: Dict[str, Any] = {
        "contract": IO_BASELINE_SSD_WORKER_SCALING_CONTRACT,
        "enabled": bool(enabled),
    }
    if not enabled:
        return receipt
    if set(contracts) != {"xp_io"}:
        raise ValueError(
            "worker-scaled IO baseline SSD gate requires isolated xp_io"
        )
    contract = contracts["xp_io"]
    if str(contract.get("target") or "") != "io":
        raise ValueError("worker-scaled SSD gate requires an IO target")
    replay_workers = len(contract.get("workers") or [])
    if calibration_workers <= 0 or replay_workers <= 0:
        raise ValueError("SSD gate worker counts must be positive")
    if replay_workers > calibration_workers:
        raise ValueError(
            "replay worker count exceeds SSD gate calibration worker count"
        )
    cfg = copy.deepcopy(dict(contract.get("cfg") or {}))
    algorithm_cfg = cfg.get("algorithm_c")
    if not isinstance(algorithm_cfg, dict):
        raise ValueError("xp_io config has no mutable algorithm_c mapping")
    gate_cfg = algorithm_cfg.get("ssd_replay_qualification")
    if not isinstance(gate_cfg, dict):
        raise ValueError("xp_io config has no SSD replay qualification mapping")
    source_minimum = float(
        gate_cfg.get("io_target_baseline_minimum_ssd_avg_pct") or 65.0
    )
    if not math.isfinite(source_minimum) or source_minimum <= 0.0:
        raise ValueError("source IO baseline SSD minimum is invalid")
    scale_factor = replay_workers / calibration_workers
    effective_minimum = source_minimum * scale_factor
    gate_cfg["io_target_baseline_minimum_ssd_avg_pct"] = effective_minimum
    contract["cfg"] = cfg
    receipt.update(
        {
            "scene": "xp_io",
            "source_config_path": str(contract.get("config_path") or ""),
            "source_minimum_ssd_avg_pct": source_minimum,
            "source_calibration_workers": calibration_workers,
            "replay_workers": replay_workers,
            "scale_factor": scale_factor,
            "effective_minimum_ssd_avg_pct": effective_minimum,
            "source_config_unchanged": True,
            "container_io_measurement_retained": True,
            "host_ssd_upper_bounds_retained": True,
            "rationale": (
                "source host-SSD floor was calibrated by two simultaneous "
                "XP-I/O workers; isolated replay uses proportional width"
            ),
        }
    )
    return receipt


def audit_all_scenes(
    contracts: Mapping[str, Mapping[str, Any]],
    output_dir: Path,
    label: str,
    expected_hashes: Optional[Mapping[str, str]],
) -> Dict[str, Dict[str, Any]]:
    receipts: Dict[str, Dict[str, Any]] = {}
    for scene, contract in contracts.items():
        expected = expected_hashes.get(scene) if expected_hashes else None
        receipt = audit_fixed_guc_domain(
            contract["cfg"],
            contract["workers"],
            expected_semantic_sha256=expected,
        )
        path = output_dir / "guc_receipts" / scene / f"{label}.json"
        write_receipt(path, receipt)
        receipts[scene] = {
            "path": str(path),
            "receipt_sha256": receipt["receipt_sha256"],
            "semantic_sha256": receipt["semantic_sha256"],
        }
    return receipts


def restore_all_workers(
    contracts: Mapping[str, Mapping[str, Any]],
    stage: str,
    attempt: int,
) -> Dict[str, Dict[str, Any]]:
    tasks: List[Tuple[str, int, Mapping[str, Any], Mapping[str, Any]]] = []
    for scene, contract in contracts.items():
        for index, worker in enumerate(contract["workers"]):
            tasks.append((scene, index, contract, worker))
    results: Dict[str, Dict[str, Any]] = {scene: {} for scene in contracts}
    with ThreadPoolExecutor(max_workers=2) as executor:
        future_map = {
            executor.submit(
                restore_worker_from_hybench_template,
                dict(contract["cfg"]),
                dict(worker),
                label=f"common_{stage}_a{attempt}_{scene}_{worker_key(worker)}",
            ): (scene, index, worker)
            for scene, index, contract, worker in tasks
        }
        for future in as_completed(future_map):
            scene, _index, worker = future_map[future]
            receipt = future.result()
            if receipt.get("status") != "ok":
                raise RuntimeError(f"restore failed for {scene}/{worker_key(worker)}: {receipt}")
            results[scene][worker_key(worker)] = receipt
    return results


def run_stage_attempt(
    contracts: Mapping[str, Mapping[str, Any]],
    output_dir: Path,
    stage: str,
    attempt: int,
    params_by_scene: Mapping[str, Mapping[str, float]],
    *,
    ssd_device: str,
    post_restore_ssd_sample_s: float,
    maximum_post_restore_ssd_util_pct: float,
    post_restore_ssd_maximum_wait_s: float,
    external_workload_guard_enabled: bool = True,
) -> Dict[str, Any]:
    external_workload_receipt: Dict[str, Any] = {
        "contract": EXTERNAL_WORKLOAD_GUARD_CONTRACT,
        "stage": stage,
        "attempt": attempt,
        "sample_s": EXTERNAL_WORKLOAD_SAMPLE_S,
        "standalone_backend_min_count": STANDALONE_BACKEND_MIN_COUNT,
        "standalone_backend_state_persistence_s": STANDALONE_BACKEND_STATE_PERSISTENCE_S,
        "started_at_epoch_s": time.time(),
        "enabled": external_workload_guard_enabled,
    }
    external_workload_stop: Optional[threading.Event] = None
    external_workload_thread: Optional[threading.Thread] = None
    if external_workload_guard_enabled:
        external_workload_stop = threading.Event()
        external_workload_thread = threading.Thread(
            target=monitor_external_workloads,
            args=(
                external_workload_stop,
                external_workload_receipt,
                EXTERNAL_WORKLOAD_SAMPLE_S,
            ),
            name=f"external-workload-guard-{stage}-a{attempt}",
            daemon=True,
        )
        external_workload_thread.start()
    else:
        external_workload_receipt.update(
            {
                "policy": "user_authorized_uncontrolled_external_workloads_v1",
                "external_workloads_observed": "not_monitored_by_user_instruction",
                "passed": True,
                "contaminated": False,
            }
        )

    def stop_external_workload_guard() -> None:
        if external_workload_stop is None or external_workload_thread is None:
            return
        external_workload_stop.set()
        external_workload_thread.join(
            timeout=max(2.0, EXTERNAL_WORKLOAD_SAMPLE_S * 3.0)
        )

    try:
        restores = restore_all_workers(contracts, stage, attempt)
    except Exception:
        stop_external_workload_guard()
        raise
    post_restore_matches, post_restore_errors = ([], [])
    if external_workload_guard_enabled:
        post_restore_matches, post_restore_errors = scan_external_workload_processes()
    if (
        external_workload_receipt.get("contaminated") is True
        or post_restore_matches
        or post_restore_errors
    ):
        stop_external_workload_guard()
        write_json(
            output_dir / "interference_receipts" / f"{stage}_a{attempt}_restore.json",
            {"guard": external_workload_receipt,
             "matches": post_restore_matches, "errors": post_restore_errors},
        )
        raise RetryableInterference(
            "external workload detected during stage restore: "
            f"matches={post_restore_matches[:3]} "
            f"errors={post_restore_errors} "
            f"monitor_detections="
            f"{external_workload_receipt.get('detection_sample_count', 0)}"
        )
    post_restore_quiet = wait_for_post_restore_ssd_quiet(
        device=ssd_device,
        sample_s=post_restore_ssd_sample_s,
        maximum_util_pct=maximum_post_restore_ssd_util_pct,
        maximum_wait_s=post_restore_ssd_maximum_wait_s,
    )
    post_restore_quiet_path = (
        output_dir
        / "ssd_quiet_receipts"
        / f"{stage}_attempt_{attempt:02d}_post_restore.json"
    )
    write_json(post_restore_quiet_path, post_restore_quiet)
    if post_restore_quiet.get("passed") is not True:
        stop_external_workload_guard()
        final_observation = (post_restore_quiet.get("observations") or [{}])[-1]
        raise RetryableInterference(
            "shared SSD did not become quiet after database restore: "
            f"device={ssd_device} "
            f"util={float(final_observation.get('observed_util_pct') or 0.0):.3f}% "
            f"waited={float(post_restore_quiet.get('waited_s') or 0.0):.3f}s"
        )
    post_quiet_matches, post_quiet_errors = ([], [])
    if external_workload_guard_enabled:
        post_quiet_matches, post_quiet_errors = scan_external_workload_processes()
    if (
        external_workload_receipt.get("contaminated") is True
        or post_quiet_matches
        or post_quiet_errors
    ):
        stop_external_workload_guard()
        write_json(
            output_dir / "interference_receipts" / f"{stage}_a{attempt}_quiet.json",
            {"guard": external_workload_receipt,
             "matches": post_quiet_matches, "errors": post_quiet_errors},
        )
        raise RetryableInterference(
            "external workload detected during post-restore quiet gate: "
            f"matches={post_quiet_matches[:3]} errors={post_quiet_errors}"
        )
    tasks: List[Tuple[str, Mapping[str, Any], Mapping[str, Any]]] = []
    for scene, contract in contracts.items():
        for worker in contract["workers"]:
            tasks.append((scene, contract, worker))
    rows_by_scene: Dict[str, List[Dict[str, Any]]] = {
        scene: [] for scene in contracts
    }
    try:
        with ThreadPoolExecutor(max_workers=len(tasks)) as executor:
            future_map = {}
            for scene, contract, worker in tasks:
                key = worker_key(worker)
                label = f"common_{stage}_a{attempt}_{scene}_{key}"
                future = executor.submit(
                    run_real_validation_safe,
                    dict(contract["cfg"]),
                    dict(params_by_scene[scene]),
                    output_dir=output_dir / "replays" / stage / f"attempt_{attempt:02d}" / scene,
                    label=label,
                    worker=worker_with_prepared_restore(dict(worker)),
                    force_new_replay=True,
                    environment_overrides=workload_environment(scene, output_dir),
                )
                future_map[future] = (scene, worker, label)
            for future in as_completed(future_map):
                scene, worker, label = future_map[future]
                row = future.result()
                row["worker_key"] = worker_key(worker)
                row["restore"] = restores[scene][worker_key(worker)]
                row["common_domain_stage"] = stage
                row["common_domain_attempt"] = attempt
                row_path = output_dir / "validations" / stage / f"attempt_{attempt:02d}" / f"{label}.json"
                write_json(row_path, row)
                row["common_domain_validation_path"] = str(row_path)
                rows_by_scene[scene].append(row)
    finally:
        stop_external_workload_guard()
    if external_workload_thread is not None and external_workload_thread.is_alive():
        external_workload_receipt.update(
            {
                "passed": False,
                "errors": ["external workload monitor did not stop"],
                "finished_at_epoch_s": time.time(),
            }
        )
        external_workload_receipt["receipt_sha256"] = canonical_sha256(
            external_workload_receipt
        )
    elif not external_workload_guard_enabled:
        external_workload_receipt["finished_at_epoch_s"] = time.time()
        external_workload_receipt["receipt_sha256"] = canonical_sha256(
            external_workload_receipt
        )
    contaminated = external_workload_receipt.get("passed") is not True
    for rows in rows_by_scene.values():
        for row in rows:
            row["external_workload_guard"] = copy.deepcopy(
                external_workload_receipt
            )
            if contaminated:
                row["technical_failure"] = True
                row["status"] = "external_workload_contamination"
                row.setdefault("errors", []).append(
                    "external CH-benCHmark/benchbase workload detected during stage"
                )
    scene_results: Dict[str, Any] = {}
    for scene, contract in contracts.items():
        rows = sorted(rows_by_scene[scene], key=lambda row: str(row.get("worker_key") or ""))
        apply_ssd_wave_qualification(
            rows,
            target=str(contract["target"]),
            phase=f"common_domain_{stage}_attempt_{attempt}",
            cfg=dict(contract["cfg"]),
            global_ssd_utilization_is_informational=(
                host_ssd_is_informational_for_scene(contract)
            ),
        )
        for row in rows:
            row_path = Path(str(row.pop("common_domain_validation_path")))
            write_json(row_path, row)
            row["common_domain_validation_path"] = str(row_path)
        expected_worker_count = len(contract["workers"])
        valid = len(rows) == expected_worker_count and all(
            physical_valid(row) for row in rows
        )
        aggregate: Optional[Dict[str, Any]] = None
        if valid:
            aggregate = slim_validation(aggregate_parallel_baseline_validation(rows))
            aggregate["params"] = copy.deepcopy(dict(params_by_scene[scene]))
            aggregate["stage"] = stage
            aggregate["physical_worker_count"] = expected_worker_count
        scene_results[scene] = {
            "valid": valid,
            "technical_failures": sum(not physical_valid(row) for row in rows),
            "physical_invocations": len(rows),
            "row_paths": [row["common_domain_validation_path"] for row in rows],
            "aggregate": aggregate,
        }
    return {
        "stage": stage,
        "attempt": attempt,
        "scene_results": scene_results,
        "all_valid": all(value["valid"] for value in scene_results.values()),
        "external_workload_guard": external_workload_receipt,
        "post_restore_ssd_quiet_receipt": {
            "path": str(post_restore_quiet_path),
            "receipt_sha256": post_restore_quiet["receipt_sha256"],
        },
    }


def restore_defaults(
    contracts: Mapping[str, Mapping[str, Any]],
    output_dir: Path,
) -> Dict[str, Any]:
    restores = restore_all_workers(contracts, "final_clean_restore", 1)
    readbacks: Dict[str, Dict[str, Any]] = {}
    tasks: List[Tuple[str, Mapping[str, Any], Mapping[str, Any]]] = []
    for scene, contract in contracts.items():
        for worker in contract["workers"]:
            tasks.append((scene, contract, worker))
    with ThreadPoolExecutor(max_workers=len(tasks)) as executor:
        future_map = {
            executor.submit(
                apply_pg_params,
                pg_conn_from_worker(dict(contract["cfg"]), dict(worker)),
                dict(contract["defaults"]),
            ): (scene, worker)
            for scene, contract, worker in tasks
        }
        for future in as_completed(future_map):
            scene, worker = future_map[future]
            readback = future.result()
            expected = contracts[scene]["defaults"]
            for name in ACTIVE_PARAMS:
                if not math.isclose(
                    float(readback[name]),
                    float(format(float(expected[name]), ".6g")),
                    rel_tol=0.0,
                    abs_tol=1e-12,
                ):
                    raise RuntimeError(
                        f"default restore readback mismatch: {scene}/{worker_key(worker)}/{name}"
                    )
            readbacks.setdefault(scene, {})[worker_key(worker)] = readback
    receipt = {
        "contract": "pg_common_domain_final_clean_restore_v1",
        "restores": restores,
        "default_cost_guc_readbacks": readbacks,
        "physical_workload_invocations": 0,
        "completed_at_epoch_s": time.time(),
    }
    receipt["receipt_sha256"] = canonical_sha256(receipt)
    write_json(output_dir / "clean_restore_receipt.json", receipt)
    return receipt


def worker_qps_by_key(scene_result: Mapping[str, Any]) -> Dict[str, float]:
    """Load the two physical worker QPS values retained by a stage result."""
    values: Dict[str, float] = {}
    for raw_path in scene_result.get("row_paths") or []:
        path = Path(str(raw_path))
        if not path.is_file():
            raise RuntimeError(f"missing physical worker validation: {path}")
        row = read_json(path)
        key = str(row.get("worker_key") or "")
        if not key or key in values:
            raise RuntimeError(f"invalid or duplicate worker key in {path}: {key!r}")
        qps = float(row.get("db_qps") or 0.0)
        if not math.isfinite(qps) or qps <= 0.0:
            raise RuntimeError(f"invalid worker QPS in {path}: {qps}")
        values[key] = qps
    return values


def build_scene_comparison(
    *,
    state: Mapping[str, Any],
    scene: str,
    stages: Sequence[str],
    methods: Sequence[str],
    method_baseline_brackets: Mapping[str, Tuple[str, str]],
    maximum_baseline_drift_pct: float,
) -> Tuple[Dict[str, Any], List[str]]:
    """Build one result against the original, same-worker baseline.

    Later baseline stages diagnose runtime drift only.  They never replace the
    original baseline as the denominator and never invalidate a completed
    measurement merely because the host state evolved during optimization.
    """
    if not methods:
        raise RuntimeError("at least one frozen method is required")
    stage_results = state.get("stage_results")
    if not isinstance(stage_results, Mapping):
        raise RuntimeError("common-domain stage results are missing")
    baseline_qps_by_stage = {
        stage: float(stage_results[stage]["scene_results"][scene]["aggregate"]["db_qps"])
        for stage in stages
        if stage.startswith("common_baseline_")
    }
    start_qps = baseline_qps_by_stage["common_baseline_00"]
    end_qps = baseline_qps_by_stage[f"common_baseline_{len(methods):02d}"]
    common_qps = sum(baseline_qps_by_stage.values()) / len(baseline_qps_by_stage)
    global_drift_pct = (
        abs(end_qps - start_qps)
        / max((start_qps + end_qps) / 2.0, 1e-12)
        * 100.0
    )
    initial_scene_result = stage_results["common_baseline_00"]["scene_results"][scene]
    initial_worker_qps = worker_qps_by_key(initial_scene_result)
    method_rows: Dict[str, Any] = {}
    drift_diagnostics: List[Dict[str, Any]] = []
    for method in methods:
        before_stage, after_stage = method_baseline_brackets[method]
        before_qps = baseline_qps_by_stage[before_stage]
        after_qps = baseline_qps_by_stage[after_stage]
        paired_qps = (before_qps + after_qps) / 2.0
        paired_drift_pct = (
            abs(after_qps - before_qps)
            / max(paired_qps, 1e-12)
            * 100.0
        )
        method_scene_result = stage_results[method]["scene_results"][scene]
        aggregate = method_scene_result["aggregate"]
        qps = float(aggregate["db_qps"])
        method_worker_qps = worker_qps_by_key(method_scene_result)
        if initial_worker_qps and method_worker_qps:
            if set(initial_worker_qps) != set(method_worker_qps):
                raise RuntimeError(
                    f"{scene}/{method}: initial/method worker sets differ: "
                    f"{sorted(initial_worker_qps)} vs {sorted(method_worker_qps)}"
                )
            worker_ratios = {
                worker: method_worker_qps[worker] / initial_worker_qps[worker]
                for worker in sorted(initial_worker_qps)
            }
            normalized_qps = start_qps * (
                sum(worker_ratios.values()) / len(worker_ratios)
            )
        else:
            # Unit fixtures and legacy state files may contain only aggregates.
            worker_ratios = {}
            normalized_qps = qps
        exceeds_diagnostic_threshold = (
            paired_drift_pct > maximum_baseline_drift_pct
        )
        drift_diagnostics.append(
            {
                "scene": scene,
                "method": method,
                "before_stage": before_stage,
                "after_stage": after_stage,
                "drift_pct": paired_drift_pct,
                "diagnostic_threshold_pct": maximum_baseline_drift_pct,
                "exceeds_diagnostic_threshold": exceeds_diagnostic_threshold,
            }
        )
        method_rows[method] = {
            "db_qps": qps,
            "normalized_db_qps": normalized_qps,
            "qps_improvement_pct": (normalized_qps / start_qps - 1.0) * 100.0,
            "qps_reference_stage": "common_baseline_00",
            "qps_reference": "initial_same_worker_baseline",
            "initial_baseline_qps": start_qps,
            "initial_baseline_worker_qps": initial_worker_qps,
            "method_worker_qps": method_worker_qps,
            "same_worker_qps_ratios": worker_ratios,
            "paired_baseline_before_stage": before_stage,
            "paired_baseline_after_stage": after_stage,
            "paired_baseline_before_qps": before_qps,
            "paired_baseline_after_qps": after_qps,
            "paired_baseline_qps": paired_qps,
            "paired_baseline_drift_pct": paired_drift_pct,
            "paired_baseline_is_diagnostic_only": True,
            "paired_baseline_drift_exceeds_diagnostic_threshold": (
                exceeds_diagnostic_threshold
            ),
            "resources": aggregate.get("resources") or {},
            "p95_ms": aggregate.get("p95_ms"),
            "p99_ms": aggregate.get("p99_ms"),
            "params": aggregate.get("params") or {},
        }
    return ({
        "baseline_start_qps": start_qps,
        "baseline_end_qps": end_qps,
        "common_baseline_qps": common_qps,
        "baseline_drift_pct": max(
            value["paired_baseline_drift_pct"]
            for value in method_rows.values()
        ),
        "baseline_global_drift_pct": global_drift_pct,
        "baseline_qps_by_stage": baseline_qps_by_stage,
        "baseline_normalization_contract": BASELINE_NORMALIZATION_CONTRACT,
        "baseline_drift_diagnostics": drift_diagnostics,
        "methods": method_rows,
    }, [])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--max-stage-attempts", type=int, default=3)
    parser.add_argument("--external-quiet-s", type=float, default=120.0)
    parser.add_argument("--maximum-baseline-drift-pct", type=float, default=10.0)
    parser.add_argument("--ssd-device", default=DEFAULT_SSD_DEVICE)
    parser.add_argument("--pre-stage-ssd-sample-s", type=float, default=5.0)
    parser.add_argument("--maximum-pre-stage-ssd-util-pct", type=float, default=20.0)
    parser.add_argument("--post-restore-ssd-sample-s", type=float, default=15.0)
    parser.add_argument(
        "--maximum-post-restore-ssd-util-pct",
        type=float,
        default=5.0,
    )
    parser.add_argument(
        "--post-restore-ssd-maximum-wait-s",
        type=float,
        default=600.0,
    )
    parser.add_argument(
        "--scenes",
        nargs="+",
        choices=[scene for scene, _target in SCENES],
        default=[scene for scene, _target in SCENES],
        help="Scenes to replay concurrently; select one for a paper-domain isolated replay",
    )
    parser.add_argument(
        "--workers-per-scene",
        type=int,
        choices=(1, 2),
        default=2,
        help="Use the first N declared workers in each selected scene",
    )
    parser.add_argument(
        "--scale-io-baseline-ssd-minimum-by-worker-count",
        action="store_true",
        help=(
            "For an isolated XP-I/O replay, scale the source two-worker "
            "baseline SSD floor to the selected replay worker count"
        ),
    )
    parser.add_argument(
        "--io-baseline-ssd-calibration-workers",
        type=int,
        default=2,
        help="Worker width used to calibrate the source XP-I/O SSD floor",
    )
    parser.add_argument(
        "--methods",
        nargs="+",
        choices=METHODS,
        default=list(METHODS),
        help="Frozen methods to replay; use algorithm_a algorithm_b algorithm_c for the paper ABC comparison",
    )
    parser.add_argument(
        "--algorithm-c-formal-root",
        type=Path,
        default=None,
        help="Successfully terminal adaptive Algorithm C root; read only after its launcher finishes",
    )
    parser.add_argument(
        "--config-root",
        type=Path,
        default=None,
        help=(
            "Execution-only scene config root. Frozen champion authorities remain "
            "unchanged unless --algorithm-c-formal-root is also supplied"
        ),
    )
    parser.add_argument(
        "--gptuner-xp-io-authority",
        type=Path,
        default=None,
        help="Override only the GPTuner XP-I/O authority used by the final replay",
    )
    parser.add_argument(
        "--aqetuner-xp-io-authority",
        type=Path,
        default=None,
        help="Override only the AQETuner XP-I/O authority used by the final replay",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not math.isfinite(args.external_quiet_s) or args.external_quiet_s < 0:
        raise ValueError("external quiet interval must be finite and nonnegative")
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    event_path = output_dir / "events.jsonl"
    state_path = output_dir / "state.json"
    authority_path = output_dir / "authority_manifest.json"
    methods = tuple(args.methods)
    contracts = scene_contracts(
        methods=methods,
        algorithm_c_formal_root=args.algorithm_c_formal_root,
        config_root=args.config_root,
        gptuner_xp_io_authority=args.gptuner_xp_io_authority,
        aqetuner_xp_io_authority=args.aqetuner_xp_io_authority,
    )
    selected_scene_names = tuple(dict.fromkeys(args.scenes))
    contracts = {
        scene: {
            **contract,
            "workers": list(contract["workers"][: args.workers_per_scene]),
        }
        for scene, contract in contracts.items()
        if scene in selected_scene_names
    }
    if set(contracts) != set(selected_scene_names):
        raise RuntimeError("selected scene coverage mismatch")
    io_baseline_ssd_worker_scaling = apply_worker_scaled_io_baseline_ssd_gate(
        contracts,
        enabled=args.scale_io_baseline_ssd_minimum_by_worker_count,
        calibration_workers=args.io_baseline_ssd_calibration_workers,
    )
    if "job_memory" in contracts:
        job_schedule = read_json(JOB_FIXED_SCHEDULE_MANIFEST)
        if (
            job_schedule.get("contract") != "canonical_job_113_fixed_worker_lanes_v1"
            or job_schedule.get("schedule_sha256") != JOB_FIXED_SCHEDULE_SHA256
            or job_schedule.get("query_count") != 113
            or job_schedule.get("worker_count") != 2
        ):
            raise RuntimeError("frozen JOB schedule authority mismatch")
    physical_workers_per_stage = sum(
        len(contract["workers"]) for contract in contracts.values()
    )
    method_order = list(methods)
    random.Random(args.seed).shuffle(method_order)
    # The first baseline is the sole comparison denominator.  Later baseline
    # stages expose time-varying SSD/cache pressure, but never redefine the
    # optimization objective after the run has started.
    stages = ["common_baseline_00"]
    method_baseline_brackets: Dict[str, Tuple[str, str]] = {}
    for index, method in enumerate(method_order):
        before = f"common_baseline_{index:02d}"
        after = f"common_baseline_{index + 1:02d}"
        stages.extend([method, after])
        method_baseline_brackets[method] = (before, after)
    contract_manifest = {
        "contract": CONTRACT,
        "seed": args.seed,
        "stages": stages,
        "method_order": method_order,
        "baseline_normalization_contract": BASELINE_NORMALIZATION_CONTRACT,
        "method_baseline_brackets": {
            method: list(bracket)
            for method, bracket in method_baseline_brackets.items()
        },
        "search_performed": False,
        "allowed_mutable_gucs": list(ACTIVE_PARAMS),
        "parameter_ranges": {
            name: list(DEFAULT_RANGES[name]) for name in ACTIVE_PARAMS
        },
        "replay_parallelism_per_scene": args.workers_per_scene,
        "scene_cohort_size": len(contracts),
        "physical_workers_per_stage": physical_workers_per_stage,
        "ssd_qualification_contract": {
            "cohort_execution": (
                "single_scene_paper_domain_isolated"
                if len(contracts) == 1
                else "selected_scenes_simultaneous_without_ssd_mutex"
            ),
            "non_target_host_ssd": "informational_when_container_io_capacity",
            "non_target_container_io_capacity": "enforced_per_worker_and_aggregate",
            "io_target_host_ssd_stable_window": "enforced",
            "scene_host_ssd_is_informational": {
                scene: host_ssd_is_informational_for_scene(contract)
                for scene, contract in contracts.items()
            },
            "io_target_baseline_minimum_worker_scaling": (
                io_baseline_ssd_worker_scaling
            ),
        },
        "maximum_baseline_drift_pct": args.maximum_baseline_drift_pct,
        "pre_stage_ssd_quiet": {
            "device": args.ssd_device,
            "sample_s": args.pre_stage_ssd_sample_s,
            "maximum_util_pct": args.maximum_pre_stage_ssd_util_pct,
        },
        "post_restore_ssd_quiet": {
            "contract": "pg_common_domain_post_restore_ssd_quiet_v1",
            "device": args.ssd_device,
            "sample_s": args.post_restore_ssd_sample_s,
            "maximum_util_pct": args.maximum_post_restore_ssd_util_pct,
            "maximum_wait_s": args.post_restore_ssd_maximum_wait_s,
        },
        "external_workload_guard": {
            "contract": EXTERNAL_WORKLOAD_GUARD_CONTRACT,
            "sample_s": EXTERNAL_WORKLOAD_SAMPLE_S,
            "fail_closed": True,
            "plain_idle_sessions_are_inactive": True,
            "standalone_backend_min_count": STANDALONE_BACKEND_MIN_COUNT,
            "standalone_backend_state_persistence_s": STANDALONE_BACKEND_STATE_PERSISTENCE_S,
            "quiet_before_attempt_s": args.external_quiet_s,
            "interference_retry_policy": "wait_then_retry_same_point_keep_initial_baseline",
        },
        "container_start_preflight": {
            "contract": CONTAINER_START_PREFLIGHT_CONTRACT,
            "after_external_quiet": True,
            "starts_only_declared_stopped_containers": True,
            "postgres_readiness_required": True,
            "resource_fingerprint_after_start": True,
        },
        "runner_path": str(Path(__file__).resolve()),
        "runner_sha256": file_sha256(Path(__file__).resolve()),
        "fixed_guc_guard_path": str(
            (ROOT / "shell" / "pg_fixed_guc_guard.py").resolve()
        ),
        "fixed_guc_guard_sha256": file_sha256(
            (ROOT / "shell" / "pg_fixed_guc_guard.py").resolve()
        ),
        "runtime_source_sha256": {
            str(path.resolve()): file_sha256(path.resolve())
            for path in RUNTIME_SOURCE_FILES
        },
        "workload_environments": {
            scene: workload_environment(scene, output_dir)
            for scene in contracts
        },
        "job_fixed_schedule": {
            "path": str(JOB_FIXED_SCHEDULE_MANIFEST),
            "file_sha256": file_sha256(JOB_FIXED_SCHEDULE_MANIFEST),
            "schedule_sha256": JOB_FIXED_SCHEDULE_SHA256,
            "query_count": 113,
            "worker_count": 2,
        },
        "scenes": {
            scene: {
                "target": contract["target"],
                "config_path": contract["config_path"],
                "config_sha256": contract["config_sha256"],
                "defaults": contract["defaults"],
                "workers": [
                    {
                        "name": worker_key(worker),
                        "container": worker.get("container"),
                        "port": worker.get("port"),
                        "cpus": worker.get("cpus"),
                        "cpuset_cpus": worker.get("cpuset_cpus"),
                        "memory": worker.get("memory"),
                    }
                    for worker in contract["workers"]
                ],
                "methods": contract["methods"],
            }
            for scene, contract in contracts.items()
        },
    }
    contract_manifest["contract_sha256"] = canonical_sha256(contract_manifest)
    if state_path.is_file():
        state = read_json(state_path)
        if state.get("contract_sha256") != contract_manifest["contract_sha256"]:
            raise RuntimeError("existing common-domain state contract mismatch")
    else:
        state = {
            "contract": CONTRACT,
            "contract_sha256": contract_manifest["contract_sha256"],
            "phase": "running",
            "stage_results": {},
            "stage_attempts": {},
            "technical_failures": 0,
            "physical_workload_invocations": 0,
            "valid_replays": 0,
            "manual_mutation_after_start": False,
            "started_at_epoch_s": time.time(),
        }
        write_json(state_path, state)
    write_json(output_dir / "contract.json", contract_manifest)

    stop_requested = False

    def request_stop(_signum: int, _frame: Any) -> None:
        nonlocal stop_requested
        stop_requested = True

    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, request_stop)

    if not wait_for_external_quiet(
        output_dir, "database_identity_preflight",
        quiet_s=args.external_quiet_s, should_stop=lambda: stop_requested,
    ):
        state["phase"] = "clean_stopped"
        write_json(state_path, state)
        return 0

    container_start_preflight = ensure_declared_containers_running(contracts)
    container_start_preflight_path = output_dir / "container_start_preflight.json"
    write_json(container_start_preflight_path, container_start_preflight)
    state["container_start_preflight"] = {
        "path": str(container_start_preflight_path),
        "receipt_sha256": container_start_preflight["receipt_sha256"],
    }
    write_json(state_path, state)

    initial_container_resources = audit_container_resource_domain(contracts)
    initial_container_resource_path = output_dir / "container_resources_initial.json"
    write_json(initial_container_resource_path, initial_container_resources)
    expected_container_resource_sha256 = initial_container_resources[
        "semantic_sha256"
    ]
    state["container_resource_semantic_sha256"] = (
        expected_container_resource_sha256
    )
    state["initial_container_resource_receipt"] = {
        "path": str(initial_container_resource_path),
        "receipt_sha256": initial_container_resources["receipt_sha256"],
        "semantic_sha256": expected_container_resource_sha256,
    }

    initial_receipts = audit_all_scenes(
        contracts,
        output_dir,
        "initial",
        expected_hashes=None,
    )
    expected_hashes = {
        scene: receipt["semantic_sha256"]
        for scene, receipt in initial_receipts.items()
    }
    state["fixed_guc_semantic_sha256"] = expected_hashes
    state["initial_guc_receipts"] = initial_receipts
    preflight_restores = restore_all_workers(
        contracts,
        "database_identity_preflight",
        1,
    )
    database_identity = audit_database_execution_cohort(contracts)
    database_identity_path = output_dir / "database_execution_cohort.json"
    write_json(database_identity_path, database_identity)
    state["database_execution_cohort"] = {
        "path": str(database_identity_path),
        "receipt_sha256": database_identity["receipt_sha256"],
        "preflight_restores": preflight_restores,
    }
    write_json(state_path, state)

    cleanup_done = False
    try:
        for stage in stages:
            if stage in state["stage_results"]:
                continue
            if stop_requested:
                state["phase"] = "clean_stop_requested"
                break
            params_by_scene = stage_params(contracts, stage)
            successful: Optional[Dict[str, Any]] = None
            attempt = 0
            non_interference_failures = 0
            while non_interference_failures < args.max_stage_attempts:
                attempt += 1
                if stop_requested:
                    break
                if not wait_for_external_quiet(
                    output_dir, f"{stage}_a{attempt}",
                    quiet_s=args.external_quiet_s, should_stop=lambda: stop_requested,
                ):
                    break
                quiet_receipt = audit_pre_stage_ssd_quiet(
                    device=args.ssd_device,
                    sample_s=args.pre_stage_ssd_sample_s,
                    maximum_util_pct=args.maximum_pre_stage_ssd_util_pct,
                )
                quiet_path = (
                    output_dir
                    / "ssd_quiet_receipts"
                    / f"{stage}_attempt_{attempt:02d}.json"
                )
                write_json(quiet_path, quiet_receipt)
                state.setdefault("pre_stage_ssd_quiet_receipts", []).append(
                    {
                        "stage": stage,
                        "attempt": attempt,
                        "path": str(quiet_path),
                        "receipt_sha256": quiet_receipt["receipt_sha256"],
                        "passed": quiet_receipt["passed"],
                    }
                )
                write_json(state_path, state)
                if quiet_receipt["passed"] is not True:
                    print(f"[{stage}] shared SSD busy; waiting before retry", flush=True)
                    continue
                pre = audit_all_scenes(
                    contracts,
                    output_dir,
                    f"{stage}_attempt_{attempt:02d}_pre",
                    expected_hashes,
                )
                pre_container_resources = audit_container_resource_domain(
                    contracts,
                    expected_semantic_sha256=expected_container_resource_sha256,
                )
                pre_container_resource_path = (
                    output_dir
                    / "container_resource_receipts"
                    / f"{stage}_attempt_{attempt:02d}_pre.json"
                )
                write_json(pre_container_resource_path, pre_container_resources)
                try:
                    result = run_stage_attempt(
                        contracts, output_dir, stage, attempt, params_by_scene,
                        ssd_device=args.ssd_device,
                        post_restore_ssd_sample_s=args.post_restore_ssd_sample_s,
                        maximum_post_restore_ssd_util_pct=args.maximum_post_restore_ssd_util_pct,
                        post_restore_ssd_maximum_wait_s=args.post_restore_ssd_maximum_wait_s,
                    )
                except RetryableInterference as exc:
                    append_jsonl(event_path, {
                        "event": "interference_before_workload_retry",
                        "time": time.time(), "stage": stage, "attempt": attempt,
                        "reason": str(exc), "physical_workload_invocations": 0,
                    })
                    print(f"[{stage}] {exc}; waiting before retry", flush=True)
                    continue
                post = audit_all_scenes(
                    contracts,
                    output_dir,
                    f"{stage}_attempt_{attempt:02d}_post",
                    expected_hashes,
                )
                post_container_resources = audit_container_resource_domain(
                    contracts,
                    expected_semantic_sha256=expected_container_resource_sha256,
                )
                post_container_resource_path = (
                    output_dir
                    / "container_resource_receipts"
                    / f"{stage}_attempt_{attempt:02d}_post.json"
                )
                write_json(post_container_resource_path, post_container_resources)
                result["pre_guc_receipts"] = pre
                result["post_guc_receipts"] = post
                result["pre_stage_ssd_quiet_receipt"] = {
                    "path": str(quiet_path),
                    "receipt_sha256": quiet_receipt["receipt_sha256"],
                }
                result["pre_container_resource_receipt"] = {
                    "path": str(pre_container_resource_path),
                    "receipt_sha256": pre_container_resources["receipt_sha256"],
                }
                result["post_container_resource_receipt"] = {
                    "path": str(post_container_resource_path),
                    "receipt_sha256": post_container_resources["receipt_sha256"],
                }
                state["stage_attempts"].setdefault(stage, []).append(result)
                physical = sum(
                    row["physical_invocations"]
                    for row in result["scene_results"].values()
                )
                technical = sum(
                    row["technical_failures"]
                    for row in result["scene_results"].values()
                )
                state["physical_workload_invocations"] += physical
                state["technical_failures"] += technical
                state["valid_replays"] += physical - technical
                write_json(state_path, state)
                append_jsonl(
                    event_path,
                    {
                        "event": "common_domain_stage_attempt_complete",
                        "time": time.time(),
                        "stage": stage,
                        "attempt": attempt,
                        "all_valid": result["all_valid"],
                        "physical_workload_invocations": physical,
                        "technical_failures": technical,
                    },
                )
                if result["all_valid"]:
                    successful = result
                    break
                if (result.get("external_workload_guard") or {}).get("passed") is True:
                    non_interference_failures += 1
                if stop_requested:
                    break
            if successful is None:
                if stop_requested:
                    state["phase"] = "clean_stop_requested"
                    break
                raise RuntimeError(
                    f"stage exhausted without {physical_workers_per_stage} valid rows: {stage}"
                )
            state["stage_results"][stage] = successful
            write_json(state_path, state)

            # Later baselines are drift diagnostics.  The original baseline
            # remains the denominator even if state changes during the cohort.
            if stage.startswith("common_baseline_"):
                baseline_index = int(stage.rsplit("_", 1)[1])
                if baseline_index > 0:
                    previous_stage = f"common_baseline_{baseline_index - 1:02d}"
                    if previous_stage not in state["stage_results"]:
                        raise RuntimeError(
                            f"missing previous adjacent baseline: {previous_stage}"
                        )
                    drift_rows: List[Dict[str, Any]] = []
                    drift_errors: List[str] = []
                    for scene in contracts:
                        before_qps = float(
                            state["stage_results"][previous_stage]["scene_results"][scene][
                                "aggregate"
                            ]["db_qps"]
                        )
                        after_qps = float(
                            state["stage_results"][stage]["scene_results"][scene][
                                "aggregate"
                            ]["db_qps"]
                        )
                        paired_qps = (before_qps + after_qps) / 2.0
                        drift_pct = (
                            abs(after_qps - before_qps)
                            / max(paired_qps, 1e-12)
                            * 100.0
                        )
                        passed = drift_pct <= args.maximum_baseline_drift_pct
                        drift_rows.append(
                            {
                                "scene": scene,
                                "before_stage": previous_stage,
                                "after_stage": stage,
                                "before_qps": before_qps,
                                "after_qps": after_qps,
                                "paired_qps": paired_qps,
                                "drift_pct": drift_pct,
                                "maximum_drift_pct": args.maximum_baseline_drift_pct,
                                "passed": passed,
                            }
                        )
                        if not passed:
                            drift_errors.append(
                                f"{scene}: adjacent baseline drift {drift_pct:.6f}% > "
                                f"{args.maximum_baseline_drift_pct:.6f}%"
                            )
                    state.setdefault("baseline_drift_diagnostics", []).append(
                        {
                            "before_stage": previous_stage,
                            "after_stage": stage,
                            "rows": drift_rows,
                            "warnings": drift_errors,
                            "affects_primary_initial_baseline_comparison": False,
                        }
                    )
                    write_json(state_path, state)

        if stop_requested:
            state["phase"] = "clean_stopped"
            return 0

        comparisons: Dict[str, Any] = {}
        drift_errors: List[str] = []
        for scene in contracts:
            comparison, scene_drift_errors = build_scene_comparison(
                state=state,
                scene=scene,
                stages=stages,
                methods=methods,
                method_baseline_brackets=method_baseline_brackets,
                maximum_baseline_drift_pct=args.maximum_baseline_drift_pct,
            )
            comparisons[scene] = comparison
            drift_errors.extend(scene_drift_errors)
        if drift_errors:
            raise RuntimeError(
                "unexpected comparison construction errors: "
                + "; ".join(drift_errors)
            )

        receipt = restore_defaults(contracts, output_dir)
        final_receipts = audit_all_scenes(
            contracts,
            output_dir,
            "final_clean_restore",
            expected_hashes,
        )
        state["clean_restore_receipt_sha256"] = receipt["receipt_sha256"]
        state["final_guc_receipts"] = final_receipts
        comparison_valid_replays = len(stages) * physical_workers_per_stage
        state["comparison_valid_replays"] = comparison_valid_replays
        state["discarded_valid_replays"] = (
            state["valid_replays"] - comparison_valid_replays
        )
        if state["discarded_valid_replays"] < 0:
            raise RuntimeError("comparison-valid replay accounting underflow")
        cleanup_done = True
        state["phase"] = "complete"
        state["comparisons"] = comparisons
        state["completed_at_epoch_s"] = time.time()
        write_json(state_path, state)
        authority = {
            "contract": CONTRACT,
            "phase": "complete",
            "contract_sha256": contract_manifest["contract_sha256"],
            "state_path": str(state_path),
            "state_sha256": file_sha256(state_path),
            "search_performed": False,
            "only_frozen_champions_replayed": True,
            "allowed_mutable_gucs": list(ACTIVE_PARAMS),
            "fixed_non_search_guc_sha256": expected_hashes,
            "fixed_container_resource_sha256": expected_container_resource_sha256,
            "container_start_preflight": state["container_start_preflight"],
            "database_execution_cohort": state["database_execution_cohort"],
            "runtime_source_sha256": contract_manifest["runtime_source_sha256"],
            "replay_parallelism_per_scene": args.workers_per_scene,
            "scene_cohort_size": len(contracts),
            "baseline_normalization_contract": BASELINE_NORMALIZATION_CONTRACT,
            "method_baseline_brackets": {
                method: list(bracket)
                for method, bracket in method_baseline_brackets.items()
            },
            "physical_workload_invocations": state["physical_workload_invocations"],
            "valid_replays": state["valid_replays"],
            "technical_failures": state["technical_failures"],
            "comparison_valid_replays": state["comparison_valid_replays"],
            "discarded_valid_replays": state["discarded_valid_replays"],
            "technical_failures_excluded_from_comparisons": True,
            "manual_mutation_after_start": False,
            "clean_restore_receipt_sha256": receipt["receipt_sha256"],
            "final_guc_receipts": final_receipts,
            "comparisons": comparisons,
            "completed_at_epoch_s": state["completed_at_epoch_s"],
        }
        authority["authority_sha256"] = canonical_sha256(authority)
        write_json(authority_path, authority)
        append_jsonl(
            event_path,
            {
                "event": "common_domain_campaign_complete",
                "time": time.time(),
                "authority_path": str(authority_path),
                "authority_sha256": authority["authority_sha256"],
            },
        )
        return 0
    finally:
        if not cleanup_done:
            receipt = restore_defaults(contracts, output_dir)
            final_receipts = audit_all_scenes(
                contracts,
                output_dir,
                "final_clean_restore",
                expected_hashes,
            )
            state["clean_restore_receipt_sha256"] = receipt["receipt_sha256"]
            state["final_guc_receipts"] = final_receipts
            if state.get("phase") == "running":
                state["phase"] = "failed_closed"
            write_json(state_path, state)


if __name__ == "__main__":
    raise SystemExit(main())
