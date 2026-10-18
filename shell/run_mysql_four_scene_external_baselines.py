#!/usr/bin/env python3
"""Run one MySQL GPTuner/AQETuner four-scene external-baseline campaign.

Publication mode gives each method a balanced pre/post default bracket, an
equal sequential black-box budget per scene, and two frozen-champion repeats
on each of the scene's two isolated workers.
"""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
from typing import Any, Dict, List, Mapping, MutableMapping, Optional, Sequence, Set, Tuple
import zipfile

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
MYSQL_ROOT = Path("/home/anonymous/program/resource_adaptive_mysql")
MYSQL_ASYNC_CONTRACT = (
    MYSQL_ROOT
    / "config/formal/frozen_mysql_async_outer_20260820_v38/"
    "algorithm_c_async_contract.json"
)
MYSQL_HYBENCH_RUNNER_V4 = (
    MYSQL_ROOT
    / "config/formal/frozen_mysql_async_outer_20260820_v38/runtime/"
    "hybench-adaptive-runner-v4.jar"
)
MYSQL_HYBENCH_RUNNER_V4_SHA256 = (
    "c8f8e23fe7c195e21b5cd3e59a37ce7bb78742761edbea66f011d98c583febe3"
)
MYSQL_WORKLOAD_ADAPTER_V2 = MYSQL_ROOT / "mysql_workload_adapter_v2.py"
MYSQL_WORKLOAD_ADAPTER_V2_SHA256 = (
    "bd88222170ca6cd9cfd8da791deedc5e9379bd77571ab8fbabcb27eba11930d8"
)
# Both repositories contain modules named ``common_outer_loop``.  The workload
# runtime must resolve the MySQL implementation first regardless of inherited
# PYTHONPATH ordering; the local project remains second for baseline policies.
for import_root in (str(MYSQL_ROOT), str(ROOT)):
    while import_root in sys.path:
        sys.path.remove(import_root)
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(MYSQL_ROOT))

from shell.mysql_external_tuner_policy import (  # noqa: E402
    AQETUNER_WARM_SAMPLES,
    AQETunerPolicy,
    GPTUNER_COARSE_TRIALS,
    GPTunerPolicy,
    exact_signature,
    git_commit,
)
from scripts import run_mysql_four_scene_algorithm_c_v2 as mysql_alg  # noqa: E402
from mysql_workload_adapter_v2 import run_hybench as run_hybench_v2  # noqa: E402


def load_external_reset_module() -> Any:
    """Load the project-local reset adapter without mutating MySQL A/B/C."""
    path = ROOT / "shell/reset_mysql_external_xp_data_planes_v1.py"
    spec = importlib.util.spec_from_file_location(
        "reset_mysql_external_xp_data_planes_v1", path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"external reset adapter is not importable: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


EXTERNAL_RESET = load_external_reset_module()


MYSQL_NATIVE_RUN_HYBENCH = run_hybench_v2
MYSQL_NATIVE_SAMPLER_FINISH = mysql_alg.r0mod.CgroupSampler.finish
MYSQL_NATIVE_APPLY_COSTS = mysql_alg.apply_costs
MYSQL_NATIVE_MYSQL_ROOT = mysql_alg.mysql_root
EXTERNAL_XP_GRACEFUL_QUIESCENCE_SECONDS = 300
MYSQL_APPLY_COST_MAX_ATTEMPTS = 3
MYSQL_APPLY_COST_RETRY_BACKOFF_SECONDS = (2.0, 5.0)
MYSQL_APPLY_COST_RETRY_LOG: Optional[Path] = None
CLEAN_STOP_SIGNALS = (signal.SIGTERM, signal.SIGINT)


def validate_hybench_successor_runtime() -> Dict[str, Any]:
    """Prove the frozen Python-v2/Java-v4 300-second runtime closure."""
    successor_contract = load(MYSQL_ASYNC_CONTRACT)
    source_bindings = successor_contract.get("source_bindings") or {}
    adapter_binding = source_bindings.get("xp_quiescence_successor_adapter") or {}
    runner_binding = source_bindings.get("hybench_runner_v4") or {}

    def bound_path(binding: Mapping[str, Any]) -> Path:
        path = Path(str(binding.get("path") or ""))
        return path if path.is_absolute() else MYSQL_ROOT / path

    adapter_path = bound_path(adapter_binding).resolve()
    runner_path = bound_path(runner_binding).resolve()
    if (
        successor_contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or adapter_path != MYSQL_WORKLOAD_ADAPTER_V2
        or adapter_binding.get("sha256") != MYSQL_WORKLOAD_ADAPTER_V2_SHA256
        or sha(adapter_path) != MYSQL_WORKLOAD_ADAPTER_V2_SHA256
        or runner_path != MYSQL_HYBENCH_RUNNER_V4
        or runner_binding.get("sha256") != MYSQL_HYBENCH_RUNNER_V4_SHA256
        or sha(runner_path) != MYSQL_HYBENCH_RUNNER_V4_SHA256
        or MYSQL_NATIVE_RUN_HYBENCH.__module__ != "mysql_workload_adapter_v2"
    ):
        raise RuntimeError("frozen MySQL HyBench successor closure differs")
    with zipfile.ZipFile(runner_path) as archive:
        runner_class = archive.read("com/hybench/HyBenchAdaptiveMain.class")
    expected_contract_text = (
        b"graceful quiescence in [30,300] seconds and stop-file"
    )
    if expected_contract_text not in runner_class:
        raise RuntimeError("HyBench v4 bytecode lacks the frozen 300-second contract")
    return {
        "status": "PASS",
        "authority": str(MYSQL_ASYNC_CONTRACT),
        "adapter": {
            "path": str(adapter_path),
            "sha256": MYSQL_WORKLOAD_ADAPTER_V2_SHA256,
            "module": MYSQL_NATIVE_RUN_HYBENCH.__module__,
        },
        "runner": {
            "path": str(runner_path),
            "sha256": MYSQL_HYBENCH_RUNNER_V4_SHA256,
            "bytecode_300_second_contract": True,
        },
    }


def parse_cpuset(raw: str) -> Set[int]:
    """Expand a Linux cpuset list such as ``0-3,8``."""
    cpus: Set[int] = set()
    for token in (part.strip() for part in raw.split(",")):
        if not token:
            continue
        if "-" in token:
            start_text, end_text = token.split("-", 1)
            start = int(start_text)
            end = int(end_text)
            if start < 0 or end < start:
                raise RuntimeError(f"invalid cpuset range: {token!r}")
            cpus.update(range(start, end + 1))
        else:
            cpu = int(token)
            if cpu < 0:
                raise RuntimeError(f"invalid cpuset CPU: {token!r}")
            cpus.add(cpu)
    if not cpus:
        raise RuntimeError(f"empty cpuset: {raw!r}")
    return cpus


def validate_live_affinity(
    binding: Mapping[str, Any],
    workers: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Any]:
    """Fail before workload when the dedicated CPU domains overlap live work."""
    domains: Dict[str, Set[int]] = {}
    for worker_id, worker in workers.items():
        domains[f"{worker_id}:database"] = parse_cpuset(
            str(worker["database_cpuset"])
        )
        domains[f"{worker_id}:client"] = parse_cpuset(
            str(worker["client_cpuset"])
        )

    occupied: Dict[int, str] = {}
    for label, cpus in domains.items():
        for cpu in cpus:
            previous = occupied.get(cpu)
            if previous is not None:
                raise RuntimeError(
                    f"external baseline CPU {cpu} overlaps {previous} and {label}"
                )
            occupied[cpu] = label
    external_cpus = set(occupied)

    rebind = binding.get("client_affinity_rebind") or {}
    formal_path = Path(str(rebind.get("formal_target_binding") or "")).resolve()
    if not formal_path.is_file():
        raise RuntimeError("formal MySQL target CPU binding is missing")
    formal = load(formal_path)
    formal_cpus = set(
        int(cpu)
        for cpu in formal["rolling_dynamic_noninterference_gate"]
        ["actual_target_cpu_scope_release"]["target_cpus"]
    )
    formal_overlap = sorted(external_cpus & formal_cpus)
    if formal_overlap:
        raise RuntimeError(
            "external baseline overlaps formal MySQL CPU scope: "
            f"{formal_overlap}"
        )

    own_containers = {str(worker["container"]) for worker in workers.values()}
    listed = docker_metadata_read(["docker", "ps", "-q"], timeout=30).stdout.split()
    other_container_cpus: Dict[str, List[int]] = {}
    if listed:
        inspected = docker_metadata_read(
            ["docker", "inspect", *listed], timeout=90
        )
        for row in json.loads(inspected.stdout):
            name = str(row.get("Name") or "").lstrip("/")
            if name in own_containers:
                continue
            raw_cpuset = str((row.get("HostConfig") or {}).get("CpusetCpus") or "")
            if not raw_cpuset:
                continue
            overlap = sorted(external_cpus & parse_cpuset(raw_cpuset))
            if overlap:
                other_container_cpus[name] = overlap
    if other_container_cpus:
        raise RuntimeError(
            "external baseline overlaps running container CPU scope: "
            f"{other_container_cpus}"
        )

    return {
        "status": "PASS",
        "checked_at": now(),
        "formal_binding_path": str(formal_path),
        "formal_binding_sha256": sha(formal_path),
        "formal_cpu_count": len(formal_cpus),
        "external_cpu_count": len(external_cpus),
        "formal_overlap": [],
        "running_container_overlap": {},
        "domains": {
            label: sorted(cpus) for label, cpus in sorted(domains.items())
        },
    }


def docker_metadata_read(
    argv: Sequence[str], *, timeout: int, attempts: int = 3
) -> subprocess.CompletedProcess[str]:
    """Serialize and retry idempotent Docker metadata reads only."""
    last_timeout: Optional[subprocess.TimeoutExpired] = None
    for attempt in range(1, attempts + 1):
        try:
            return EXTERNAL_RESET._docker_run(list(argv), timeout=timeout)
        except subprocess.TimeoutExpired as error:
            last_timeout = error
            if attempt >= attempts:
                raise
            time.sleep(2.0 * attempt)
    raise RuntimeError("Docker metadata read produced no result") from last_timeout


def external_classify_xp_mysql_liveness(
    worker: Mapping[str, Any],
) -> Dict[str, Any]:
    """Prove a dedicated external XP predecessor has no live mysqld.

    The upstream checker deliberately accepts only the production A/B/C owner
    label.  This equivalent process-local checker accepts only the dedicated
    external-baseline owner and plane, while retaining the two-inspect and
    exact process-table proof required before discarding an unresponsive
    predecessor.
    """
    worker_id = str(worker.get("worker_id") or "")
    expected_container = EXTERNAL_RESET.XP_CONTAINER_ALLOWLIST.get(worker_id)
    container = str(worker.get("container") or "")
    expected_id = str(worker.get("container_id") or "")
    base = {
        "worker_id": worker_id,
        "expected_container": expected_container,
        "observed_container": container,
        "expected_container_id": expected_id,
        "safe_to_discard_unresponsive_predecessor": False,
        "session_count_semantics": "explicit_sql_probe_required",
    }
    if (
        not expected_container
        or container != expected_container
        or len(expected_id) != 64
    ):
        return {**base, "status": "IDENTITY_MISMATCH"}
    try:
        first = EXTERNAL_RESET._docker_json("inspect", container)
        first_state = first.get("State") or {}
        labels = (first.get("Config") or {}).get("Labels") or {}
        identity_matches = (
            first.get("Id") == expected_id
            and first_state.get("Running") is True
            and labels.get("ra.owner") == EXTERNAL_RESET.EXTERNAL_OWNER
            and labels.get("ra.worker_id") == worker_id
            and labels.get("ra.external_baseline_plane") == "disk1_v2"
        )
        if not identity_matches:
            return {
                **base,
                "status": "IDENTITY_MISMATCH",
                "first_container_id": first.get("Id"),
                "docker_running": first_state.get("Running"),
                "owner_label": labels.get("ra.owner"),
                "worker_label": labels.get("ra.worker_id"),
                "plane_label": labels.get("ra.external_baseline_plane"),
            }
        process_table = EXTERNAL_RESET._docker_run(
            ["docker", "top", container, "-eo", "pid,stat,comm,args"],
            timeout=30,
        ).stdout
        rows: List[Dict[str, Any]] = []
        for line in process_table.splitlines()[1:]:
            fields = line.strip().split(maxsplit=3)
            if len(fields) < 3:
                continue
            pid, state, command = fields[:3]
            arguments = fields[3] if len(fields) == 4 else ""
            if command == "mysqld" or re.search(
                r"(^|/)mysqld(?:\s|$)", arguments
            ):
                rows.append(
                    {
                        "pid": int(pid),
                        "state": state,
                        "command": command,
                        "arguments_sha256": hashlib.sha256(
                            arguments.encode("utf-8", errors="replace")
                        ).hexdigest(),
                    }
                )
        second = EXTERNAL_RESET._docker_json("inspect", container)
        second_state = second.get("State") or {}
        second_labels = (second.get("Config") or {}).get("Labels") or {}
        stable_identity = (
            second.get("Id") == expected_id
            and second_state.get("Running") is True
            and second_labels.get("ra.owner") == EXTERNAL_RESET.EXTERNAL_OWNER
            and second_labels.get("ra.worker_id") == worker_id
            and second_labels.get("ra.external_baseline_plane") == "disk1_v2"
        )
        live_rows = [
            row for row in rows if not str(row["state"]).startswith("Z")
        ]
        safe = stable_identity and not live_rows
        return {
            **base,
            "status": "PROVEN_NO_LIVE_MYSQLD" if safe else "LIVE_OR_UNPROVEN",
            "first_container_id": first.get("Id"),
            "second_container_id": second.get("Id"),
            "docker_running": second_state.get("Running"),
            "container_identity_stable": stable_identity,
            "mysqld_rows": rows,
            "live_mysqld_rows": live_rows,
            "safe_to_discard_unresponsive_predecessor": safe,
            "session_count_semantics": (
                "zero_by_proven_absence_of_live_mysqld"
                if safe
                else "explicit_sql_probe_required"
            ),
        }
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, RuntimeError) as error:
        return {
            **base,
            "status": "LIVENESS_READ_FAILED",
            "error_type": type(error).__name__,
            "error": str(error),
        }


def external_provision_worker(
    worker: Dict[str, Any], output_root: Path, verify_existing: bool,
    rotate_all: bool,
) -> Dict[str, Any]:
    """Verify credentials for the dedicated external-baseline containers.

    The upstream verifier intentionally accepts only the MySQL A/B/C owner
    label.  These workers have a separate owner so another campaign cannot
    mistake them for A/B/C data planes.  Preserve every identity, liveness,
    session and credential check while accepting only that dedicated owner.
    """
    if not verify_existing or rotate_all:
        raise RuntimeError("external baseline account adapter is verify-only")
    worker_id = str(worker["worker_id"])
    if re.fullmatch(r"[a-z0-9_]+", worker_id) is None:
        raise RuntimeError(f"unsafe worker id: {worker_id!r}")
    container = str(worker["container"])
    root_defaults = Path(str(worker["credential_defaults"]))
    benchmark_defaults = root_defaults.with_name("benchmark.cnf")
    if not root_defaults.is_file() or not benchmark_defaults.is_file():
        raise RuntimeError(f"{worker_id} existing benchmark credential is absent")
    inspected = EXTERNAL_RESET._docker_json("inspect", container)
    labels = (inspected.get("Config") or {}).get("Labels") or {}
    if (
        inspected.get("Id") != worker.get("container_id")
        or (inspected.get("State") or {}).get("Running") is not True
        or labels.get("ra.owner") != EXTERNAL_RESET.EXTERNAL_OWNER
        or labels.get("ra.external_baseline_plane") != "disk1_v2"
    ):
        raise RuntimeError(f"{worker_id} live external container identity differs")
    processlist = subprocess.run(
        [
            "mysql", f"--defaults-file={root_defaults}", "--host=127.0.0.1",
            f"--port={worker['host_port']}", "--connect-timeout=10", "-NBe",
            "SELECT COUNT(*) FROM information_schema.processlist "
            "WHERE ID<>CONNECTION_ID() AND COMMAND<>'Daemon' "
            "AND USER<>'event_scheduler'",
        ],
        capture_output=True, text=True, check=True, timeout=30,
    ).stdout.strip()
    if processlist != "0":
        raise RuntimeError(f"{worker_id} still has non-system client sessions")
    database = str(worker["database"])
    internal = subprocess.run(
        [
            "mysql", f"--defaults-file={root_defaults}", "--host=127.0.0.1",
            f"--port={worker['host_port']}", "--connect-timeout=10", "-NBe",
            "SELECT 1",
        ],
        capture_output=True, text=True, check=False, timeout=30,
    )
    external = subprocess.run(
        [
            "mysql", f"--defaults-file={benchmark_defaults}",
            "--host=127.0.0.1", f"--port={worker['host_port']}",
            "--connect-timeout=5", f"--database={database}", "-NBe",
            "SELECT 1,USER(),CURRENT_USER(),DATABASE()",
        ],
        capture_output=True, text=True, check=False, timeout=30,
    )
    if internal.returncode != 0 or external.returncode != 0:
        raise RuntimeError(
            f"{worker_id} credential verification failed: "
            f"internal={internal.stderr[-500:]!r} external={external.stderr[-500:]!r}"
        )
    fields = external.stdout.strip().split("\t")
    if len(fields) != 4 or fields[0] != "1" or fields[2] != "ra_benchmark@%":
        raise RuntimeError(f"{worker_id} benchmark account readback differs")
    row = {
        "worker_id": worker_id,
        "scene": worker["scene"],
        "container": container,
        "container_id": worker["container_id"],
        "database": database,
        "benchmark_user": "ra_benchmark",
        "benchmark_defaults": str(benchmark_defaults),
        "benchmark_defaults_mode": oct(benchmark_defaults.stat().st_mode & 0o777),
        "root_defaults_rotated": str(root_defaults),
        "external_user": fields[1],
        "external_current_user": fields[2],
        "external_database": fields[3],
        "passwords_emitted": False,
        "account_mutation_performed_in_this_run": False,
        "external_owner_adapter": True,
        "ready": True,
    }
    worker_output = output_root / worker_id
    worker_output.mkdir(parents=True, exist_ok=True)
    evidence = worker_output / "benchmark_user.json"
    evidence.write_text(
        json.dumps(row, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    row["evidence_path"] = str(evidence)
    row["evidence_sha256"] = hashlib.sha256(evidence.read_bytes()).hexdigest()
    return row


SCENES = (
    "job_memory",
    "tpch_sf4_memory",
    "hybench_xp_cpu",
    "hybench_xp_io",
)
EXPECTED_ACTIVE = {
    "job_memory": (
        "io_block_read_cost",
        "memory_block_read_cost",
        "row_evaluate_cost",
        "memory_temptable_row_cost",
    ),
    "tpch_sf4_memory": (
        "io_block_read_cost",
        "memory_block_read_cost",
        "row_evaluate_cost",
        "disk_temptable_row_cost",
    ),
    "hybench_xp_cpu": (
        "row_evaluate_cost",
        "key_compare_cost",
        "disk_temptable_row_cost",
        "io_block_read_cost",
    ),
    "hybench_xp_io": (
        "io_block_read_cost",
        "disk_temptable_row_cost",
        "disk_temptable_create_cost",
        "memory_temptable_row_cost",
    ),
}
METHOD_LABELS = {"gptuner": "GPTuner", "aqetuner": "AQETuner"}
OFFICIAL_COMMITS = {
    "gptuner": "89401e61925c173f283cc283c847fb0943efdb37",
    "aqetuner": "40e656babcf409d1fc5ad60e7edfdaff4d95ba24",
}
PARAMETER_TABLE_ROOT = (
    MYSQL_ROOT
    / "config/formal/frozen_mysql_xp_top4_algorithm_c_20260825_v1"
)
PARAMETER_TABLES = {
    "job_memory": "T.job_memory.operator_physics_v1.json",
    "tpch_sf4_memory": "T.tpch_memory.operator_physics_v1.json",
    "hybench_xp_cpu": "T.hybench_xp_cpu.top4.operator_physics_v1.json",
    "hybench_xp_io": "T.hybench_xp_io.top4.operator_physics_v1.json",
}


def classify_post_wave_candidate_failure(
    error: Mapping[str, Any],
    active_parameters_by_scene: Mapping[str, Sequence[str]],
) -> Optional[str]:
    """Classify a zero-invocation failure after a committed search wave."""
    error_type = str(error.get("type") or "")
    message = str(error.get("message") or "")
    if (
        error_type == "RuntimeError"
        and message.endswith("external search wave is not paired")
    ):
        return "post_wave_candidate_pairing_failure"
    if (
        error_type == "RuntimeError"
        and "Expected all tensors to be on the same device" in message
        and "cuda" in message
        and "cpu" in message
    ):
        return "post_wave_candidate_model_device_failure"
    active_names = {
        str(name)
        for names in active_parameters_by_scene.values()
        for name in names
    }
    if error_type == "KeyError" and message.strip("'\"") in active_names:
        return "post_wave_candidate_configuration_failure"
    if error_type == "KeyError" and message.strip("'\"") == "formal_client_log":
        return "post_wave_plan_capture_runtime_field_failure"
    return None


def external_run_hybench(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    """Preserve the official 300-second native-XP quiescence contract."""
    formal_control = kwargs.get("formal_control")
    if formal_control is not None:
        control = dict(formal_control)
        if (
            int(control.get("minimum_measurement_seconds", 0)) != 300
            or int(control.get("maximum_measurement_seconds", 0)) != 900
        ):
            raise RuntimeError("external XP adaptive measurement contract differs")
        control["graceful_quiescence_seconds"] = (
            EXTERNAL_XP_GRACEFUL_QUIESCENCE_SECONDS
        )
        kwargs["formal_control"] = control
    return MYSQL_NATIVE_RUN_HYBENCH(*args, **kwargs)


def append_apply_cost_retry_event(event: Mapping[str, Any]) -> None:
    """Append one non-secret retry receipt without weakening the main state."""
    if MYSQL_APPLY_COST_RETRY_LOG is None:
        return
    MYSQL_APPLY_COST_RETRY_LOG.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    payload = json.dumps(
        dict(event), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8") + b"\n"
    descriptor = os.open(
        MYSQL_APPLY_COST_RETRY_LOG,
        os.O_APPEND | os.O_CREAT | os.O_WRONLY,
        0o600,
    )
    try:
        os.write(descriptor, payload)
    finally:
        os.close(descriptor)


def external_apply_costs(
    worker: Mapping[str, Any], params: Mapping[str, float]
) -> Dict[str, float]:
    """Retry only a pre-workload timeout; native readback remains authoritative.

    The native operation is idempotent: a killed connection rolls back an
    uncommitted transaction, while retrying an already committed assignment
    writes the identical values.  The native helper still performs the exact
    float readback gate before this wrapper can return success.
    """
    worker_id = str(worker.get("worker_id") or "unknown")
    last_timeout: Optional[subprocess.TimeoutExpired] = None
    for attempt in range(1, MYSQL_APPLY_COST_MAX_ATTEMPTS + 1):
        started = time.monotonic()
        try:
            readback = MYSQL_NATIVE_APPLY_COSTS(worker, params)
        except subprocess.TimeoutExpired as error:
            last_timeout = error
            append_apply_cost_retry_event({
                "event": "apply_cost_timeout",
                "captured_at": now(),
                "worker_id": worker_id,
                "attempt": attempt,
                "maximum_attempts": MYSQL_APPLY_COST_MAX_ATTEMPTS,
                "elapsed_seconds": time.monotonic() - started,
                "parameter_signature": canonical_sha(dict(params)),
                "physical_invocation_started": False,
            })
            if attempt >= MYSQL_APPLY_COST_MAX_ATTEMPTS:
                raise
            backoff = MYSQL_APPLY_COST_RETRY_BACKOFF_SECONDS[attempt - 1]
            time.sleep(backoff)
            try:
                probe = MYSQL_NATIVE_MYSQL_ROOT(
                    worker, "SELECT 1", timeout_seconds=15
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as probe_error:
                append_apply_cost_retry_event({
                    "event": "apply_cost_retry_probe_failed",
                    "captured_at": now(),
                    "worker_id": worker_id,
                    "attempt": attempt,
                    "error_type": type(probe_error).__name__,
                    "physical_invocation_started": False,
                })
            else:
                if probe != "1":
                    raise RuntimeError(
                        f"{worker_id}: apply-cost retry readiness probe differs"
                    )
        else:
            if attempt > 1:
                append_apply_cost_retry_event({
                    "event": "apply_cost_retry_recovered",
                    "captured_at": now(),
                    "worker_id": worker_id,
                    "successful_attempt": attempt,
                    "parameter_signature": canonical_sha(dict(params)),
                    "physical_invocation_started": False,
                    "native_readback_passed": True,
                })
            return readback
    if last_timeout is not None:
        raise last_timeout
    raise RuntimeError(f"{worker_id}: unreachable apply-cost retry state")


def external_sampler_finish(
    sampler: Any,
    active_windows: Mapping[str, tuple[float, float]] | None = None,
) -> Dict[str, Any]:
    """Keep short task failures from being hidden by the sampler exception."""
    if active_windows is None:
        return MYSQL_NATIVE_SAMPLER_FINISH(sampler, None)
    sampler.stop_event.set()
    sampler.thread.join(timeout=10)
    if sampler.thread.is_alive():
        raise RuntimeError("resource sampler did not stop")
    sufficient_windows: Dict[str, tuple[float, float]] = {}
    insufficient: List[str] = []
    for worker_id, window in active_windows.items():
        started, finished = window
        rows = [
            row for row in sampler.rows.get(worker_id, ())
            if started <= float(row["captured_monotonic"]) <= finished
        ]
        if len(rows) >= 2:
            sufficient_windows[worker_id] = window
        else:
            insufficient.append(worker_id)
    summary = MYSQL_NATIVE_SAMPLER_FINISH(sampler, sufficient_windows)
    for worker_id in insufficient:
        receipt = summary[worker_id]
        receipt.update({
            "sample_count": 0,
            "elapsed_seconds": 0.0,
            "active_window_bound": True,
            "insufficient_active_window": True,
            "cpu_fraction_of_quota": math.nan,
            "memory_peak_fraction": math.nan,
            "read_fraction_of_cap": math.nan,
            "write_fraction_of_cap": math.nan,
        })
    return summary


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_sha(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii"
    )


def aggregate_scene_rows(
    rows: Sequence[Mapping[str, Any]], scene: str
) -> Dict[str, Any]:
    selected = [row for row in rows if row.get("scene") == scene]
    if len(selected) != 2:
        raise RuntimeError(f"{scene}: expected exactly two physical rows")
    valid = all(row.get("hard_gate_passed") is True for row in selected)
    return {
        "scene": scene,
        "worker_count": 2,
        "worker_ids": [str(row["worker_id"]) for row in selected],
        "valid": valid,
        "db_qps": (
            sum(float(row.get("db_qps") or 0.0) for row in selected) / 2.0
            if valid else 0.0
        ),
        "resource_means": {
            resource: (
                sum(
                    float((row.get("resource_means") or {}).get(resource, 0.0))
                    for row in selected
                )
                / 2.0
            )
            for resource in ("cpu", "memory", "io")
        },
        "rows": copy.deepcopy(list(selected)),
    }


def aggregate_scene_repetitions(
    rows: Sequence[Mapping[str, Any]],
    scene: str,
    worker_ids: Sequence[str],
    repetitions_per_worker: int,
    label: str,
) -> Dict[str, Any]:
    """Aggregate a balanced set of repeated physical scene measurements."""
    selected = [row for row in rows if row.get("scene") == scene]
    counts = {
        worker_id: sum(str(row.get("worker_id") or "") == worker_id for row in selected)
        for worker_id in worker_ids
    }
    expected = repetitions_per_worker * len(worker_ids)
    valid = (
        len(selected) == expected
        and all(count == repetitions_per_worker for count in counts.values())
        and all(row.get("hard_gate_passed") is True for row in selected)
    )
    return {
        "label": label,
        "scene": scene,
        "worker_count": len(worker_ids),
        "worker_ids": list(worker_ids),
        "measurement_count": len(selected),
        "repetitions_per_worker": repetitions_per_worker,
        "worker_measurement_counts": counts,
        "valid": valid,
        "db_qps": (
            sum(float(row.get("db_qps") or 0.0) for row in selected) / expected
            if valid else 0.0
        ),
        "resource_means": {
            resource: (
                sum(
                    float((row.get("resource_means") or {}).get(resource, 0.0))
                    for row in selected
                )
                / expected
                if valid else 0.0
            )
            for resource in ("cpu", "memory", "io")
        },
        "rows": copy.deepcopy(selected),
    }


def verify_hashed_artifact(path: Path) -> str:
    """Verify the standard adjacent SHA-256 receipt and return the digest."""
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"immutable artifact is absent: {path}")
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.is_file() or sidecar.is_symlink():
        raise RuntimeError(f"immutable artifact receipt is absent: {sidecar}")
    fields = sidecar.read_text(encoding="ascii").strip().split()
    digest = sha(path)
    if len(fields) != 2 or fields[0] != digest or fields[1] != path.name:
        raise RuntimeError(f"immutable artifact receipt differs: {path}")
    return digest


def validate_publication_prelaunch_resume(
    state: Mapping[str, Any],
    output: Path,
    binding_path: Path,
    method: str,
    candidate_budget: int,
) -> Dict[str, Any]:
    """Validate an exact publication wave with no physical invocation.

    Completed observations remain immutable and the failed logical candidate
    may be retried only when every selected worker has a zero-record receipt.
    """
    output = output.resolve()
    binding_path = binding_path.resolve()
    completed_waves = int(state.get("completed_search_waves") or 0)
    logical_wave = completed_waves + 1
    expected_stage = f"SEARCH_WAVE_{logical_wave:03d}"
    error = state.get("error") or {}
    if not (
        state.get("schema_version")
        == "mysql_four_scene_external_baseline_state_v2"
        and state.get("status") == "FAILED_CLOSED"
        and state.get("phase") == "FAILED_CLOSED"
        and state.get("method") == method
        and state.get("publication_mode") is True
        and state.get("candidate_wave_width_per_scene") == 1
        and int(state.get("candidate_budget_per_scene") or 0)
        == candidate_budget
        and 0 < completed_waves < candidate_budget
        and state.get("manual_mutation_after_start") is False
        and error.get("type") == "TimeoutExpired"
        and "mysql" in str(error.get("message") or "")
        and "timed out after 60 seconds" in str(error.get("message") or "")
        and (state.get("clean_restore") or {}).get("status") == "PASS"
    ):
        raise RuntimeError("publication prelaunch resume scalar contract differs")

    verify_hashed_artifact(output / "state.json")
    binding = state.get("independent_worker_binding") or {}
    if (
        Path(str(binding.get("path") or "")).resolve() != binding_path
        or binding.get("sha256") != sha(binding_path)
    ):
        raise RuntimeError("publication prelaunch resume worker binding drifted")
    binding_data = load(binding_path)
    workers = {
        str(worker.get("worker_id") or ""): dict(worker)
        for worker in binding_data.get("workers") or []
    }
    if len(workers) != len(SCENES) * 2 or "" in workers:
        raise RuntimeError("publication prelaunch resume worker set differs")
    worker_ids_by_scene = {
        scene: sorted(
            worker_id
            for worker_id, worker in workers.items()
            if worker.get("scene") == scene
        )
        for scene in SCENES
    }
    if (
        state.get("worker_ids_by_scene") != worker_ids_by_scene
        or any(len(worker_ids) != 2 for worker_ids in worker_ids_by_scene.values())
    ):
        raise RuntimeError("publication prelaunch resume scene binding differs")

    evidence_files: List[Dict[str, Any]] = []

    def validate_invocation_evidence(
        evidence: Mapping[str, Any], expected_path: Optional[Path] = None
    ) -> None:
        path = Path(str(evidence.get("path") or "")).resolve()
        if expected_path is not None and path != expected_path.resolve():
            raise RuntimeError("publication resume invocation path differs")
        if (
            evidence.get("exists") is not True
            or evidence.get("physical_invocation_started") is not True
            or int(evidence.get("record_count") or 0) <= 0
            or not path.is_file()
            or sha(path) != evidence.get("sha256")
        ):
            raise RuntimeError("publication resume invocation evidence differs")
        with path.open("r", encoding="utf-8") as stream:
            record_count = sum(1 for line in stream if line.strip())
        if record_count != int(evidence["record_count"]):
            raise RuntimeError("publication resume invocation record count differs")
        evidence_files.append({"path": str(path), "sha256": sha(path)})

    baseline = state.get("baseline") or {}
    baseline_rows = state.get("baseline_rows") or {}
    if (
        state.get("baseline_wave_count") != 2
        or set(baseline) != set(SCENES)
        or any((baseline.get(scene) or {}).get("valid") is not True for scene in SCENES)
        or set(baseline_rows) != set(SCENES)
    ):
        raise RuntimeError("publication prelaunch resume baseline differs")
    for repetition in (1, 2):
        batch = output / "baseline" / f"rep_{repetition:02d}" / "batch_result.json"
        evidence_files.append({"path": str(batch), "sha256": verify_hashed_artifact(batch)})
    for scene in SCENES:
        rows = baseline_rows.get(scene) or []
        counts = {
            worker_id: sum(row.get("worker_id") == worker_id for row in rows)
            for worker_id in worker_ids_by_scene[scene]
        }
        if (
            len(rows) != 4
            or any(count != 2 for count in counts.values())
            or any(row.get("hard_gate_passed") is not True for row in rows)
        ):
            raise RuntimeError(f"{scene}: publication resume baseline rows differ")
        for row in rows:
            validate_invocation_evidence(row.get("physical_invocation_evidence") or {})

    observations = state.get("observations") or {}
    if set(observations) != set(SCENES):
        raise RuntimeError("publication prelaunch resume observations differ")
    for scene in SCENES:
        rows = observations[scene]
        if (
            len(rows) != completed_waves
            or [int(row.get("wave") or 0) for row in rows]
            != list(range(1, completed_waves + 1))
            or any(row.get("valid") is not True for row in rows)
            or any(row.get("technical_failure") is True for row in rows)
            or any(str(row.get("worker_id") or "") not in worker_ids_by_scene[scene] for row in rows)
        ):
            raise RuntimeError(f"{scene}: publication resume observation history differs")
        for row in rows:
            validate_invocation_evidence(row.get("physical_invocation_evidence") or {})
    for wave in range(1, completed_waves + 1):
        batch = output / "search" / f"wave_{wave:03d}" / "batch_result.json"
        evidence_files.append({"path": str(batch), "sha256": verify_hashed_artifact(batch)})

    expected_candidate_invocations = completed_waves * len(SCENES)
    expected_total_invocations = 16 + expected_candidate_invocations
    if not (
        int(state.get("candidate_physical_invocations") or 0)
        == expected_candidate_invocations
        and int(state.get("physical_workload_invocations") or 0)
        == expected_total_invocations
        and int(state.get("valid_replays") or 0) == expected_total_invocations
        and int(state.get("technical_failures") or 0) == 0
    ):
        raise RuntimeError("publication prelaunch resume physical accounting differs")

    failed = state.get("failed_wave_invocation_evidence") or []
    inflight = state.get("inflight_wave") or {}
    assignments = inflight.get("assignments") or {}
    receipts = failed[-1].get("receipts") or {} if failed else {}
    selected_by_scene = {
        scene: [worker_id for worker_id in assignments if workers[worker_id]["scene"] == scene]
        for scene in SCENES
    } if set(assignments).issubset(workers) else {}
    if not (
        len(failed) == 1
        and failed[-1].get("stage") == expected_stage
        and inflight.get("stage") == expected_stage
        and Path(str(inflight.get("output") or "")).resolve()
        == (output / "search" / f"wave_{logical_wave:03d}").resolve()
        and len(assignments) == len(SCENES)
        and set(receipts) == set(assignments)
        and selected_by_scene
        and all(len(selected_by_scene[scene]) == 1 for scene in SCENES)
    ):
        raise RuntimeError("publication prelaunch failed-wave lineage differs")
    active = state.get("active_parameters_by_scene") or {}
    ranges = state.get("ranges_by_scene") or {}
    for worker_id, assignment in assignments.items():
        scene = str(workers[worker_id]["scene"])
        params = assignment.get("params") or {}
        if (
            int(assignment.get("rank") or 0) != logical_wave
            or set(params) != set(active.get(scene) or [])
            or any(
                not (
                    float(ranges[scene][name]["lower"])
                    <= float(value)
                    <= float(ranges[scene][name]["upper"])
                )
                for name, value in params.items()
            )
        ):
            raise RuntimeError(f"{worker_id}: publication retry assignment differs")
        expected_path = output / "search" / f"wave_{logical_wave:03d}" / worker_id / "client_processes.jsonl"
        receipt = receipts[worker_id]
        if not (
            Path(str(receipt.get("path") or "")).resolve() == expected_path.resolve()
            and receipt.get("exists") is False
            and receipt.get("physical_invocation_started") is False
            and int(receipt.get("record_count") or 0) == 0
            and receipt.get("sha256") is None
            and not expected_path.exists()
        ):
            raise RuntimeError(f"{worker_id}: publication zero-invocation receipt differs")
    if int(state.get("prelaunch_failures") or 0) != len(receipts):
        raise RuntimeError("publication prelaunch failure accounting differs")
    for name in (
        "post_search_baseline",
        "comparison_baseline",
        "champion",
        "champion_validation",
    ):
        if state.get(name):
            raise RuntimeError(f"publication resume premature {name} differs")
    for name in ("post_baseline_rows", "champion_validation_rows"):
        rows_by_scene = state.get(name) or {}
        if set(rows_by_scene) != set(SCENES) or any(rows_by_scene[scene] for scene in SCENES):
            raise RuntimeError(f"publication resume premature {name} differs")
    return {
        "status": "PASS",
        "logical_wave": logical_wave,
        "stage": expected_stage,
        "completed_search_waves": completed_waves,
        "observation_counts": {scene: len(observations[scene]) for scene in SCENES},
        "retry_assignments": copy.deepcopy(assignments),
        "failed_receipts": copy.deepcopy(receipts),
        "verified_evidence_file_count": len(evidence_files),
        "verified_evidence_sha256": canonical_sha(evidence_files),
        "prior_physical_invocation_started": False,
    }


def validate_complete_baseline_recovery(
    state: Mapping[str, Any],
    output: Path,
    binding_path: Path,
    method: str,
    candidate_budget: int,
) -> Dict[str, Any]:
    """Validate the exact post-baseline NameError boundary without replaying it."""
    output = output.resolve()
    binding_path = binding_path.resolve()
    error = state.get("error") or {}
    scalar_contract = bool(
        state.get("schema_version")
        == "mysql_four_scene_external_baseline_state_v2"
        and state.get("status") == "FAILED_CLOSED"
        and state.get("phase") == "FAILED_CLOSED"
        and error.get("type") == "NameError"
        and error.get("message") == "name 'np' is not defined"
        and state.get("method") == method
        and int(state.get("candidate_budget_per_scene") or 0) == candidate_budget
        and state.get("publication_mode") is True
        and state.get("candidate_wave_width_per_scene") == 1
        and state.get("manual_mutation_after_start") is False
        and int(state.get("physical_workload_invocations") or 0) == 16
        and int(state.get("valid_replays") or 0) == 16
        and int(state.get("technical_failures") or 0) == 0
        and int(state.get("prelaunch_failures") or 0) == 0
        and int(state.get("candidate_physical_invocations") or 0) == 0
        and not (state.get("failed_wave_invocation_evidence") or [])
        and state.get("inflight_wave") is None
        and not (state.get("worker_baseline_qps") or {})
        and state.get("baseline_wave_count") is None
        and state.get("baseline_tuned_or_updated") is None
    )
    if not scalar_contract:
        raise RuntimeError("complete-baseline recovery scalar contract differs")

    binding = state.get("independent_worker_binding") or {}
    if (
        Path(str(binding.get("path") or "")).resolve() != binding_path
        or binding.get("sha256") != sha(binding_path)
    ):
        raise RuntimeError("complete-baseline recovery worker binding drifted")

    for name in (
        "observations",
        "post_baseline_rows",
        "champion_validation_rows",
    ):
        value = state.get(name) or {}
        if set(value) != set(SCENES) or any(value.get(scene) for scene in SCENES):
            raise RuntimeError(f"complete-baseline recovery {name} differs")
    for name in (
        "post_search_baseline",
        "comparison_baseline",
        "champion",
        "champion_validation",
    ):
        if state.get(name):
            raise RuntimeError(f"complete-baseline recovery {name} is not empty")
    if state.get("aqetuner_plan_capture"):
        raise RuntimeError("complete-baseline recovery plan capture already exists")
    if (output / "authority_manifest.json").exists():
        raise RuntimeError("complete-baseline recovery authority already exists")
    search_root = output / "search"
    if search_root.exists() and any(path.is_file() for path in search_root.rglob("*")):
        raise RuntimeError("complete-baseline recovery search evidence already exists")

    worker_ids_by_scene = state.get("worker_ids_by_scene") or {}
    if (
        set(worker_ids_by_scene) != set(SCENES)
        or any(len(worker_ids_by_scene.get(scene) or []) != 2 for scene in SCENES)
        or len({worker for rows in worker_ids_by_scene.values() for worker in rows}) != 8
    ):
        raise RuntimeError("complete-baseline recovery worker map differs")

    baseline_rows = state.get("baseline_rows") or {}
    if set(baseline_rows) != set(SCENES):
        raise RuntimeError("complete-baseline recovery baseline scene map differs")
    evidence_hashes: Dict[str, str] = {}
    aggregates: Dict[str, Dict[str, Any]] = {}
    worker_medians: Dict[str, float] = {}
    for scene in SCENES:
        worker_ids = [str(value) for value in worker_ids_by_scene[scene]]
        rows = baseline_rows.get(scene) or []
        if len(rows) != 4:
            raise RuntimeError(f"{scene}: complete-baseline row count differs")
        expected_paths = {
            str(
                output
                / "baseline"
                / f"rep_{repetition:02d}"
                / worker_id
                / "client_processes.jsonl"
            )
            for repetition in (1, 2)
            for worker_id in worker_ids
        }
        actual_paths: Set[str] = set()
        for row in rows:
            worker_id = str(row.get("worker_id") or "")
            evidence = row.get("physical_invocation_evidence") or {}
            evidence_path = Path(str(evidence.get("path") or "")).resolve()
            actual_paths.add(str(evidence_path))
            if (
                row.get("scene") != scene
                or worker_id not in worker_ids
                or row.get("hard_gate_passed") is not True
                or row.get("technical_failure") is True
                or not math.isfinite(float(row.get("db_qps") or 0.0))
                or float(row.get("db_qps") or 0.0) <= 0.0
                or evidence.get("exists") is not True
                or evidence.get("physical_invocation_started") is not True
                or int(evidence.get("record_count") or 0) <= 0
                or not evidence_path.is_file()
                or evidence.get("sha256") != sha(evidence_path)
            ):
                raise RuntimeError(
                    f"{scene}/{worker_id}: complete-baseline evidence differs"
                )
            evidence_hashes[str(evidence_path)] = str(evidence["sha256"])
        if actual_paths != expected_paths:
            raise RuntimeError(f"{scene}: complete-baseline repetition lineage differs")
        aggregate = aggregate_scene_repetitions(
            rows,
            scene,
            worker_ids,
            2,
            "pre_search_baseline_aggregate",
        )
        if aggregate["valid"] is not True:
            raise RuntimeError(f"{scene}: complete-baseline aggregate is invalid")
        aggregates[scene] = aggregate
        for worker_id in worker_ids:
            values = [
                float(row["db_qps"])
                for row in rows
                if str(row.get("worker_id") or "") == worker_id
            ]
            if len(values) != 2:
                raise RuntimeError(f"{worker_id}: complete-baseline balance differs")
            worker_medians[worker_id] = float(np.median(values))

    partial_baseline = state.get("baseline") or {}
    if (
        set(partial_baseline) != {SCENES[0]}
        or partial_baseline[SCENES[0]] != aggregates[SCENES[0]]
    ):
        raise RuntimeError("complete-baseline recovery failure boundary differs")
    for repetition in (1, 2):
        batch_result = (
            output / "baseline" / f"rep_{repetition:02d}" / "batch_result.json"
        )
        checksum_path = batch_result.with_name(batch_result.name + ".sha256")
        if not batch_result.is_file() or not checksum_path.is_file():
            raise RuntimeError("complete-baseline batch result evidence is missing")
        recorded = checksum_path.read_text(encoding="ascii").split()[0]
        if recorded != sha(batch_result):
            raise RuntimeError("complete-baseline batch result checksum differs")
        evidence_hashes[str(batch_result)] = recorded
    return {
        "aggregates": aggregates,
        "worker_medians": worker_medians,
        "evidence_hashes": evidence_hashes,
        "evidence_file_count": len(evidence_hashes),
    }


def _node_type(key: str) -> str:
    lowered = key.lower()
    if "join" in lowered or lowered == "nested_loop":
        return "Join"
    if "table" in lowered or "scan" in lowered:
        return "TableScan"
    if "group" in lowered or "aggregate" in lowered or "duplicate" in lowered:
        return "Aggregating"
    if "sort" in lowered or "order" in lowered:
        return "Sorting"
    if "union" in lowered:
        return "Union"
    if "material" in lowered or "temporary" in lowered:
        return "Buffer"
    if "limit" in lowered:
        return "Limit"
    return "Filter"


def aggregate_mysql_plans(
    plans: Mapping[str, str], *, maximum_nodes: int = 200
) -> Dict[str, Any]:
    """Convert every MySQL EXPLAIN root under a balanced node budget."""
    next_id = 0

    def convert(value: Any, key: str, budget: List[int]) -> Dict[str, Any] | None:
        nonlocal next_id
        if next_id >= maximum_nodes or budget[0] <= 0:
            return None
        if not isinstance(value, (Mapping, list)):
            return None
        budget[0] -= 1
        node_id = next_id
        next_id += 1
        row_count = 0.0
        children: List[Dict[str, Any]] = []
        if isinstance(value, Mapping):
            for count_key in (
                "rows_examined_per_scan",
                "rows_produced_per_join",
                "rows",
            ):
                try:
                    row_count = max(row_count, float(value.get(count_key) or 0.0))
                except (TypeError, ValueError):
                    pass
            for child_key, child_value in value.items():
                if isinstance(child_value, Mapping):
                    child = convert(child_value, str(child_key), budget)
                    if child is not None:
                        children.append(child)
                elif isinstance(child_value, list):
                    for item in child_value:
                        child = convert(item, str(child_key), budget)
                        if child is not None:
                            children.append(child)
        else:
            for item in value:
                child = convert(item, key, budget)
                if child is not None:
                    children.append(child)
        return {
            "NodeId": node_id,
            "NodeType": _node_type(key),
            "Statistic": {"RowCount": row_count},
            "Children": children,
        }

    parsed_plans: List[Tuple[str, Any]] = []
    for name, raw in sorted(plans.items()):
        try:
            parsed_plans.append((name, json.loads(raw)))
        except json.JSONDecodeError as error:
            raise RuntimeError(f"invalid MySQL EXPLAIN JSON for {name}") from error
    if len(parsed_plans) > maximum_nodes - 1:
        raise RuntimeError("AQETuner node budget cannot represent every MySQL query")

    root = {
        "NodeId": next_id,
        "NodeType": "Projection",
        "Statistic": {"RowCount": 0.0},
        "Children": [],
    }
    next_id += 1
    available = maximum_nodes - 1
    base_budget, remainder = divmod(available, max(1, len(parsed_plans)))
    for index, (name, parsed) in enumerate(parsed_plans):
        budget = [base_budget + (1 if index < remainder else 0)]
        child = convert(parsed, name, budget)
        if child is not None:
            root["Children"].append(child)
    if len(root["Children"]) != len(parsed_plans) or not root["Children"]:
        raise RuntimeError("AQETuner MySQL aggregate plan dropped query roots")
    return root


class Campaign:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.output = args.output_dir.resolve()
        self.state_path = self.output / "state.json"
        self.stop_requested = False
        self.alg: Any = None
        self.freeze: Dict[str, Any] = {}
        self.workers: Dict[str, Dict[str, Any]] = {}
        self.active: Dict[str, List[str]] = {}
        self.ranges: Dict[str, Dict[str, tuple[float, float]]] = {}
        self.defaults: Dict[str, float] = {}
        self.policies: Dict[str, Any] = {}
        self.state: Dict[str, Any] = {}

    def request_stop(self, signum: int, _frame: Any) -> None:
        self.stop_requested = True
        if self.state:
            self.state["clean_stop_requested"] = True
            self.state["clean_stop_signal"] = signum
            self.state["clean_stop_requested_at"] = now()
            atomic(self.state_path, self.state)

    def initialize_runtime(self) -> None:
        global MYSQL_APPLY_COST_RETRY_LOG

        self.alg = mysql_alg
        # Process-local compatibility adapters only; the MySQL A/B/C source
        # tree and every running campaign remain untouched.
        self.alg.run_hybench = external_run_hybench
        MYSQL_APPLY_COST_RETRY_LOG = (
            self.output / "control" / "apply_cost_retries.jsonl"
        )
        self.alg.apply_costs = external_apply_costs
        hybench_successor = validate_hybench_successor_runtime()
        self.alg.HYBENCH_ADAPTIVE_RUNNER_JAR = MYSQL_HYBENCH_RUNNER_V4
        self.alg.r0mod.CgroupSampler.finish = external_sampler_finish
        self.alg.reset = EXTERNAL_RESET
        self.alg.users._run_worker = external_provision_worker
        self.alg.classify_xp_mysql_liveness = (
            external_classify_xp_mysql_liveness
        )
        # The upstream helper derives quarantine identity from a generic
        # directory name such as ``baseline``.  Fresh publication launches
        # would therefore collide with a zero-workload failed predecessor.
        # Keep every reset and quarantine lineage beneath this run's unique,
        # recorded campaign root without modifying the official source tree.
        self.alg.measurement_campaign_root = self.measurement_campaign_root
        binding_path = self.args.worker_binding.resolve()
        binding = load(binding_path)
        if (
            binding.get("schema_version")
            != "mysql_external_baseline_independent_workers_v1"
            or binding.get("status") != "PASS"
            or binding.get("immutable_templates_only") is not True
            or binding.get("live_datadirs_copied") is not False
            or binding.get("logical_cpu_sets_pairwise_disjoint") is not True
            or binding.get("logical_cpu_sets_disjoint_from_running_container_cpusets")
            is not True
        ):
            raise RuntimeError("independent MySQL worker binding differs")
        self.freeze = copy.deepcopy(binding["freeze"])
        self.workers = {
            str(worker["worker_id"]): copy.deepcopy(worker)
            for worker in binding.get("workers") or []
        }
        affinity_preflight = validate_live_affinity(binding, self.workers)
        frozen_workers = {
            str(worker["worker_id"]): worker
            for worker in self.freeze.get("workers") or []
        }
        credential_preflight: Dict[str, Any] = {}
        for worker_id, worker in self.workers.items():
            benchmark_defaults = Path(
                str(worker.get("credential_benchmark") or "")
            ).resolve()
            if (
                benchmark_defaults.is_symlink()
                or not benchmark_defaults.is_file()
                or benchmark_defaults.stat().st_mode & 0o077
            ):
                raise RuntimeError(
                    f"{worker_id}: independent benchmark credential differs"
                )
            # The cloned binding deliberately uses a descriptive field name;
            # the sealed runtime expects this compatibility alias.
            worker["benchmark_defaults"] = str(benchmark_defaults)
            if worker_id in frozen_workers:
                frozen_workers[worker_id]["benchmark_defaults"] = str(
                    benchmark_defaults
                )
            user, _password = self.alg.private_credential(worker)
            root_probe = self.alg.mysql_root(
                worker, "SELECT 1", timeout_seconds=15
            )
            benchmark_probe = subprocess.run(
                [
                    "mysql",
                    f"--defaults-file={benchmark_defaults}",
                    "--host=127.0.0.1",
                    f"--port={worker['host_port']}",
                    "--connect-timeout=5",
                    "-NBe",
                    (
                        "SELECT DATABASE(), COUNT(*) "
                        "FROM information_schema.tables "
                        "WHERE table_schema=DATABASE()"
                    ),
                    str(worker["database"]),
                ],
                check=True,
                text=True,
                capture_output=True,
                timeout=15,
            ).stdout.strip().split("\t")
            if (
                root_probe != "1"
                or len(benchmark_probe) != 2
                or benchmark_probe[0] != str(worker["database"])
                or int(benchmark_probe[1]) <= 0
            ):
                raise RuntimeError(f"{worker_id}: read-only SQL preflight differs")
            credential_preflight[worker_id] = {
                "path": str(benchmark_defaults),
                "sha256": sha(benchmark_defaults),
                "user": user,
                "mode": oct(benchmark_defaults.stat().st_mode & 0o777),
                "root_select_one": root_probe,
                "database": benchmark_probe[0],
                "schema_table_count": int(benchmark_probe[1]),
            }
            inspected = EXTERNAL_RESET._docker_json(
                "inspect", str(worker["container"])
            )
            state = inspected.get("State") or {}
            host = inspected.get("HostConfig") or {}
            mounts = {
                str(row.get("Destination")): str(row.get("Source"))
                for row in inspected.get("Mounts") or []
            }
            differences: Dict[str, Any] = {}
            expected_host = {
                "CpusetCpus": worker["database_cpuset"],
                "CpusetMems": worker["database_cpuset_mems"],
                "NanoCpus": int(
                    round(float(worker["cpu_quota_cores"]) * 1_000_000_000)
                ),
                "Memory": int(worker["memory_bytes"]),
                "PidsLimit": int(worker["pids_max"]),
            }
            for key, value in expected_host.items():
                if host.get(key) != value:
                    differences[key] = {
                        "expected": value,
                        "actual": host.get(key),
                    }
            if (
                state.get("Running") is not True
                or mounts.get("/var/lib/mysql") != str(worker["datadir"])
                or differences
            ):
                raise RuntimeError(
                    f"{worker_id}: independent container binding drifted: "
                    f"{differences}"
                )
            # XP containers are replaced after every clean template reset, so
            # rebind the current immutable name/mount to its current ID.
            worker["container_id"] = inspected["Id"]
            worker["container_pid"] = state.get("Pid")
        # The production reset module is deliberately allowlisted.  Admit only
        # the verified clone names inside this process; original A/B/C names
        # are not available to this campaign.
        self.alg.reset.XP_CONTAINER_ALLOWLIST = {
            worker_id: str(self.workers[worker_id]["container"])
            for worker_id in self.alg.XP_IDS
        }
        point_gate = {
            "status": "PASS",
            "contract": "mysql_external_baseline_independent_workers_v1",
            "binding_path": str(binding_path),
            "binding_sha256": sha(binding_path),
            "worker_semantic_sha256": binding["worker_semantic_sha256"],
            "live_affinity_preflight": affinity_preflight,
        }
        by_scene = {
            scene: sorted(
                worker_id
                for worker_id, worker in self.workers.items()
                if worker.get("scene") == scene
            )
            for scene in SCENES
        }
        if any(len(worker_ids) != 2 for worker_ids in by_scene.values()):
            raise RuntimeError(f"MySQL two-worker scene authority differs: {by_scene}")
        self.defaults = {
            name: float(value) for name, value in self.alg.DEFAULTS.items()
        }
        for scene in SCENES:
            table_path = PARAMETER_TABLE_ROOT / PARAMETER_TABLES[scene]
            table = self.alg.load(table_path)
            space = table.get("parameter_space") or {}
            active = [str(name) for name in space.get("active_params") or []]
            if set(active) != set(EXPECTED_ACTIVE[scene]) or len(active) != 4:
                raise RuntimeError(f"{scene}: current A/B/C active cost set differs")
            ranges = {
                name: (
                    float(space["ranges"][name]["lower"]),
                    float(space["ranges"][name]["upper"]),
                )
                for name in active
            }
            if any(
                not (0.0 < low <= self.defaults[name] <= high)
                for name, (low, high) in ranges.items()
            ):
                raise RuntimeError(f"{scene}: default outside frozen cost range")
            self.active[scene] = active
            self.ranges[scene] = ranges
            self.state.setdefault("parameter_table_bindings", {})[scene] = {
                "path": str(table_path),
                "sha256": sha(table_path),
            }
        self.state["prestart_point_gate"] = point_gate
        self.state["worker_ids_by_scene"] = by_scene
        self.state["runtime_compatibility"] = {
            "benchmark_credential_alias": "credential_benchmark->benchmark_defaults",
            "mysql_workload_adapter": MYSQL_NATIVE_RUN_HYBENCH.__module__,
            "hybench_successor": hybench_successor,
            "xp_graceful_quiescence_seconds": (
                EXTERNAL_XP_GRACEFUL_QUIESCENCE_SECONDS
            ),
            "sampler_preserves_original_task_failures": True,
            "apply_cost_timeout_policy": {
                "scope": "pre_workload_only",
                "maximum_attempts": MYSQL_APPLY_COST_MAX_ATTEMPTS,
                "backoff_seconds": list(
                    MYSQL_APPLY_COST_RETRY_BACKOFF_SECONDS
                ),
                "native_float_readback_required": True,
                "retry_log": str(MYSQL_APPLY_COST_RETRY_LOG),
                "candidate_budget_consumed_before_client_start": False,
            },
            "mysql_abc_source_tree_modified": False,
        }
        self.state["credential_preflight"] = credential_preflight

    def measurement_campaign_root(self, _measurement_output: Path) -> Path:
        """Return the unique, audited XP-reset root for this fresh run."""
        campaign_id = str(self.state.get("reset_campaign_id") or "")
        if re.fullmatch(r"[A-Za-z0-9_.-]{8,128}", campaign_id) is None:
            raise RuntimeError("reset campaign identity differs")
        root = self.output / "xp_reset_campaigns" / campaign_id
        root.mkdir(parents=True, mode=0o700, exist_ok=True)
        return root

    def initial_state(self) -> Dict[str, Any]:
        if self.output.exists():
            raise RuntimeError(f"fresh output already exists: {self.output}")
        self.output.mkdir(parents=True, mode=0o700)
        return {
            "schema_version": "mysql_four_scene_external_baseline_state_v2",
            "status": "RUNNING",
            "phase": "INITIALIZING",
            "method": self.args.method,
            "method_label": METHOD_LABELS[self.args.method],
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "started_at": now(),
            "pid": os.getpid(),
            "reset_campaign_id": (
                datetime.now(timezone.utc).strftime("run_%Y%m%dT%H%M%SZ")
                + f"_{os.getpid()}"
            ),
            "candidate_budget_per_scene": self.args.candidate_budget,
            "candidate_wave_width_per_scene": 1 if self.args.publication_mode else 2,
            "baseline_policy": (
                "balanced_pre_post_search_default_bracket"
                if self.args.publication_mode
                else "one_immutable_contemporaneous_default_wave"
            ),
            "comparison_metric": (
                "same_worker_normalized_qps_then_bracketed_final_qps_improvement_pct"
                if self.args.publication_mode
                else "method_own_baseline_relative_qps_improvement_pct"
            ),
            "absolute_qps_cross_method_comparable": False,
            "publication_mode": self.args.publication_mode,
            "active_parameters_by_scene": {},
            "ranges_by_scene": {},
            "default_optimizer_costs": {},
            "observations": {scene: [] for scene in SCENES},
            "baseline": {},
            "baseline_rows": {scene: [] for scene in SCENES},
            "worker_baseline_qps": {},
            "post_search_baseline": {},
            "post_baseline_rows": {scene: [] for scene in SCENES},
            "comparison_baseline": {},
            "champion": {},
            "champion_validation": {},
            "champion_validation_rows": {scene: [] for scene in SCENES},
            "candidate_physical_invocations": 0,
            "physical_workload_invocations": 0,
            "valid_replays": 0,
            "technical_failures": 0,
            "prelaunch_failures": 0,
            "failed_wave_invocation_evidence": [],
            "manual_mutation_after_start": False,
        }

    def load_resume_state(self) -> None:
        """Load a fail-closed prelaunch state without replaying its baseline."""
        if not self.output.is_dir() or not self.state_path.is_file():
            raise RuntimeError("resume output/state does not exist")
        state = load(self.state_path)
        if (
            state.get("schema_version")
            == "mysql_four_scene_external_baseline_state_v2"
            and state.get("publication_mode") is True
        ):
            self.load_publication_prelaunch_resume_state(state)
            return
        failed_closed = bool(
            state.get("status") == "FAILED_CLOSED"
            and state.get("phase") == "FAILED_CLOSED"
        )
        clean_stopped = bool(
            state.get("status") == "CLEAN_STOPPED"
            and state.get("phase") == "CLEAN_STOPPED"
            and state.get("clean_stop_requested") is True
            and int(state.get("clean_stop_signal") or 0) in {
                int(signal.SIGHUP),
                int(signal.SIGTERM),
                int(signal.SIGINT),
            }
            and (state.get("clean_restore") or {}).get("status") == "PASS"
        )
        interrupted_running = bool(
            state.get("status") == "RUNNING"
            and state.get("clean_stop_requested") is True
            and int(state.get("clean_stop_signal") or 0) in {
                int(signal.SIGTERM),
                int(signal.SIGINT),
            }
            and str(state.get("phase") or "").startswith("SEARCH_")
        )
        if interrupted_running:
            predecessor_pid = int(state.get("pid") or 0)
            if predecessor_pid > 0:
                try:
                    os.kill(predecessor_pid, 0)
                except ProcessLookupError:
                    pass
                except PermissionError as error:
                    raise RuntimeError(
                        "resume interrupted predecessor PID is not inspectable"
                    ) from error
                else:
                    raise RuntimeError(
                        "resume interrupted predecessor process is still alive"
                    )
        if (
            state.get("schema_version")
            != "mysql_four_scene_external_baseline_state_v1"
            or not (failed_closed or clean_stopped or interrupted_running)
            or state.get("method") != self.args.method
            or int(state.get("candidate_budget_per_scene") or 0)
            != self.args.candidate_budget
            or state.get("manual_mutation_after_start") is not False
        ):
            raise RuntimeError("resume predecessor state contract differs")
        binding = state.get("independent_worker_binding") or {}
        binding_path = self.args.worker_binding.resolve()
        if (
            Path(str(binding.get("path") or "")).resolve() != binding_path
            or binding.get("sha256") != sha(binding_path)
        ):
            raise RuntimeError("resume worker binding drifted")
        baseline = state.get("baseline") or {}
        if (
            state.get("baseline_wave_count") != 1
            or set(baseline) != set(SCENES)
            or any((baseline.get(scene) or {}).get("valid") is not True for scene in SCENES)
        ):
            raise RuntimeError("resume immutable baseline is incomplete")
        observations = state.get("observations") or {}
        counts = {scene: len(observations.get(scene) or []) for scene in SCENES}
        if (
            len(set(counts.values())) != 1
            or next(iter(counts.values())) <= 0
            or next(iter(counts.values())) >= self.args.candidate_budget
            or next(iter(counts.values())) % 2
        ):
            raise RuntimeError(f"resume observation counts differ: {counts}")
        completed_waves = int(state.get("completed_search_waves") or 0)
        if completed_waves * 2 != next(iter(counts.values())):
            raise RuntimeError("resume completed-wave accounting differs")
        if int(state.get("physical_workload_invocations") or 0) != int(
            state.get("valid_replays") or 0
        ) + int(state.get("technical_failures") or 0):
            raise RuntimeError("resume physical accounting differs")
        failed = state.get("failed_wave_invocation_evidence") or []
        inflight = state.get("inflight_wave") or {}
        expected_stage = f"SEARCH_WAVE_{completed_waves + 1:03d}"
        binding_data = load(binding_path)
        expected_worker_ids = {
            str(worker.get("worker_id") or "")
            for worker in binding_data.get("workers") or []
        }
        if len(expected_worker_ids) != len(SCENES) * 2 or "" in expected_worker_ids:
            raise RuntimeError("resume worker-id contract differs")

        receipts = failed[-1].get("receipts") or {} if failed else {}
        failed_wave_matches = bool(
            failed
            and failed[-1].get("stage") == expected_stage
            and inflight.get("stage") == expected_stage
        )
        prelaunch_failure = bool(
            failed_wave_matches
            and set(receipts) == expected_worker_ids
            and all(
                row.get("physical_invocation_started") is False
                and int(row.get("record_count") or 0) == 0
                for row in receipts.values()
            )
        )
        physical_wave_failure = bool(
            failed_wave_matches
            and set(receipts) == expected_worker_ids
            and all(
                row.get("physical_invocation_started") is True
                and int(row.get("record_count") or 0) > 0
                for row in receipts.values()
            )
        )
        error = state.get("error") or {}
        post_wave_failure_kind = classify_post_wave_candidate_failure(
            error,
            state.get("active_parameters_by_scene") or {},
        )
        scheduling_failure = bool(not inflight and post_wave_failure_kind)
        if clean_stopped:
            next_wave = self.output / "search" / f"wave_{completed_waves + 1:03d}"
            if inflight or any(next_wave.glob("**/client_processes.jsonl")):
                raise RuntimeError(
                    "resume clean stop contains an unfinished physical invocation"
                )
            failure_kind = (
                "post_wave_clean_stop_sighup"
                if int(state.get("clean_stop_signal") or 0) == int(signal.SIGHUP)
                else "post_wave_clean_stop_sigterm_or_sigint"
            )
        elif interrupted_running:
            next_wave = self.output / "search" / f"wave_{completed_waves + 1:03d}"
            if (
                inflight.get("stage") != expected_stage
                or any(next_wave.glob("**/client_processes.jsonl"))
            ):
                raise RuntimeError(
                    "resume interrupted wave contains a physical invocation"
                )
            failure_kind = "orphaned_clean_stop_sigterm"
        elif physical_wave_failure:
            next_wave = self.output / "search" / f"wave_{completed_waves + 1:03d}"
            if not next_wave.is_dir():
                raise RuntimeError(
                    "resume physical failed wave evidence directory is absent"
                )
            failure_kind = "physical_wave_technical_failure_consumed"
        elif prelaunch_failure:
            if int(state.get("prelaunch_failures") or 0) < len(receipts):
                raise RuntimeError("resume prelaunch accounting differs")
            failure_kind = "zero_invocation_prelaunch_failure"
        elif scheduling_failure:
            next_wave = self.output / "search" / f"wave_{completed_waves + 1:03d}"
            if any(next_wave.glob("**/client_processes.jsonl")):
                raise RuntimeError(
                    "resume scheduling failure contains a physical invocation"
                )
            failure_kind = str(post_wave_failure_kind)
        else:
            raise RuntimeError("resume failed-wave lineage differs")

        resume_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        predecessor_label = (
            "clean_stopped_resumable"
            if clean_stopped
            else (
                "interrupted_resumable"
                if interrupted_running
                else "failed_resumable"
            )
        )
        predecessor = self.output / f"state.{predecessor_label}.{resume_id}.json"
        if predecessor.exists() or predecessor.is_symlink():
            raise RuntimeError("fresh resume predecessor archive required")
        atomic(predecessor, state)
        self.state = state
        self.state.setdefault("resume_history", []).append({
            "resume_id": resume_id,
            "requested_at": now(),
            "predecessor_state_path": str(predecessor),
            "predecessor_state_sha256": sha(predecessor),
            "completed_search_waves": completed_waves,
            "observation_counts": counts,
            "failed_stage": expected_stage,
            "failure_kind": failure_kind,
            "prior_error": copy.deepcopy(state.get("error")),
            "prior_cleanup_error": copy.deepcopy(state.get("cleanup_error")),
            "prior_clean_stop": {
                "requested": state.get("clean_stop_requested"),
                "signal": state.get("clean_stop_signal"),
                "requested_at": state.get("clean_stop_requested_at"),
            },
            "physical_failure_started": physical_wave_failure,
        })
        self.state["status"] = "RUNNING"
        self.state["phase"] = "RESUME_PREFLIGHT"
        self.state["stage"] = "RESUME_PREFLIGHT"
        self.state["stage_updated_at"] = now()
        self.state["pid"] = os.getpid()
        self.state["resumed_at"] = now()
        self.state["error"] = None
        self.state["cleanup_error"] = None
        self.state["clean_stop_requested"] = False
        self.state["clean_stop_signal"] = None
        self.state["clean_stop_requested_at"] = None
        self.state["signal_policy"] = {
            "SIGINT": "clean_stop_at_wave_boundary",
            "SIGTERM": "clean_stop_at_wave_boundary",
            "SIGHUP": "ignored_for_detached_unattended_campaign",
        }
        self.save()

    def load_publication_prelaunch_resume_state(
        self, state: Mapping[str, Any]
    ) -> None:
        """Load the audited v2 zero-invocation publication boundary."""
        audit = validate_publication_prelaunch_resume(
            state,
            self.output,
            self.args.worker_binding,
            self.args.method,
            self.args.candidate_budget,
        )
        resume_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        predecessor = self.output / f"state.failed_resumable.{resume_id}.json"
        if predecessor.exists() or predecessor.is_symlink():
            raise RuntimeError("fresh publication resume predecessor archive required")
        atomic(predecessor, state)
        self.state = copy.deepcopy(dict(state))
        self.state.setdefault("resume_history", []).append({
            "resume_id": resume_id,
            "requested_at": now(),
            "predecessor_state_path": str(predecessor),
            "predecessor_state_sha256": sha(predecessor),
            "completed_search_waves": audit["completed_search_waves"],
            "observation_counts": audit["observation_counts"],
            "failed_stage": audit["stage"],
            "failure_kind": "publication_zero_invocation_apply_cost_timeout",
            "prior_error": copy.deepcopy(state.get("error")),
            "prior_cleanup_error": copy.deepcopy(state.get("cleanup_error")),
            "physical_failure_started": False,
            "retry_assignments": copy.deepcopy(audit["retry_assignments"]),
            "failed_receipts": copy.deepcopy(audit["failed_receipts"]),
            "verified_evidence_file_count": audit[
                "verified_evidence_file_count"
            ],
            "verified_evidence_sha256": audit[
                "verified_evidence_sha256"
            ],
            "prior_physical_invocation_started": False,
            "failed_candidate_budget_consumed": False,
        })
        self.state["status"] = "RUNNING"
        self.state["phase"] = "RESUME_PREFLIGHT"
        self.state["stage"] = "RESUME_PREFLIGHT"
        self.state["stage_updated_at"] = now()
        self.state["pid"] = os.getpid()
        self.state["resumed_at"] = now()
        self.state["error"] = None
        self.state["cleanup_error"] = None
        self.state["clean_stop_requested"] = False
        self.state["clean_stop_signal"] = None
        self.state["clean_stop_requested_at"] = None
        self.state["signal_policy"] = {
            "SIGINT": "clean_stop_at_wave_boundary",
            "SIGTERM": "clean_stop_at_wave_boundary",
            "SIGHUP": "ignored_for_detached_unattended_campaign",
        }
        self.save()

    def load_complete_baseline_recovery_state(self) -> None:
        """Archive and admit only the audited NumPy post-baseline failure."""
        if not self.output.is_dir() or not self.state_path.is_file():
            raise RuntimeError("complete-baseline recovery output/state does not exist")
        checksum_path = self.state_path.with_name(self.state_path.name + ".sha256")
        if not checksum_path.is_file():
            raise RuntimeError("complete-baseline recovery state checksum is missing")
        state_sha256 = sha(self.state_path)
        if checksum_path.read_text(encoding="ascii").split()[0] != state_sha256:
            raise RuntimeError("complete-baseline recovery state checksum differs")
        state = load(self.state_path)
        predecessor_pid = int(state.get("pid") or 0)
        if predecessor_pid > 0:
            try:
                os.kill(predecessor_pid, 0)
            except ProcessLookupError:
                pass
            except PermissionError as error:
                raise RuntimeError(
                    "complete-baseline recovery predecessor PID is not inspectable"
                ) from error
            else:
                raise RuntimeError(
                    "complete-baseline recovery predecessor process is still alive"
                )
        audit = validate_complete_baseline_recovery(
            state,
            self.output,
            self.args.worker_binding,
            self.args.method,
            self.args.candidate_budget,
        )
        resume_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        predecessor = (
            self.output / f"state.failed_complete_baseline.{resume_id}.json"
        )
        if predecessor.exists() or predecessor.is_symlink():
            raise RuntimeError("fresh complete-baseline predecessor archive required")
        atomic(predecessor, state)
        if sha(predecessor) != state_sha256:
            raise RuntimeError("complete-baseline predecessor archive differs")
        self.state = state
        self.state.setdefault("resume_history", []).append({
            "resume_id": resume_id,
            "requested_at": now(),
            "failure_kind": "post_complete_baseline_missing_numpy_import",
            "prior_error": copy.deepcopy(state.get("error")),
            "predecessor_state_path": str(predecessor),
            "predecessor_state_sha256": state_sha256,
            "baseline_replayed": False,
            "validated_physical_invocations": 16,
            "validated_evidence_file_count": audit["evidence_file_count"],
            "validated_evidence_sha256": canonical_sha(audit["evidence_hashes"]),
        })
        self.state["status"] = "RUNNING"
        self.state["phase"] = "BASELINE_COMPLETE_RESUME_PREFLIGHT"
        self.state["stage"] = "BASELINE_COMPLETE_RESUME_PREFLIGHT"
        self.state["stage_updated_at"] = now()
        self.state["pid"] = os.getpid()
        self.state["resumed_at"] = now()
        self.state["error"] = None
        self.save()

    def prepare_complete_baseline_recovery_after_runtime(self) -> None:
        """Rebuild only derived baseline aggregates, then enter the search."""
        history = self.state.get("resume_history") or []
        expected_failure = "post_complete_baseline_missing_numpy_import"
        if not history or history[-1].get("failure_kind") != expected_failure:
            raise RuntimeError("complete-baseline recovery history is missing")
        stored_active = self.state.get("active_parameters_by_scene") or {}
        stored_ranges = self.state.get("ranges_by_scene") or {}
        stored_defaults = self.state.get("default_optimizer_costs") or {}
        current_ranges = {
            scene: {
                name: {"lower": low, "upper": high}
                for name, (low, high) in self.ranges[scene].items()
            }
            for scene in SCENES
        }
        if (
            stored_active != self.active
            or stored_ranges != current_ranges
            or stored_defaults != self.defaults
        ):
            raise RuntimeError("complete-baseline recovery cost domain drifted")
        audit_state = dict(self.state)
        audit_state.update({
            "status": "FAILED_CLOSED",
            "phase": "FAILED_CLOSED",
            "error": history[-1]["prior_error"],
            "worker_baseline_qps": {},
            "baseline_wave_count": None,
            "baseline_tuned_or_updated": None,
        })
        audit = validate_complete_baseline_recovery(
            audit_state,
            self.output,
            self.args.worker_binding,
            self.args.method,
            self.args.candidate_budget,
        )
        self.state["baseline"] = audit["aggregates"]
        self.state["worker_baseline_qps"] = audit["worker_medians"]
        self.state["baseline_wave_count"] = 2
        self.state["baseline_tuned_or_updated"] = False
        self.state["inflight_wave"] = None
        receipt = history[-1]
        receipt["runtime_preflight_passed"] = True
        receipt["runtime_preflight_at"] = now()
        receipt["reconstructed_fields"] = [
            "baseline",
            "worker_baseline_qps",
            "baseline_wave_count",
            "baseline_tuned_or_updated",
        ]
        self.restore_defaults("complete_baseline_resume_preflight")
        receipt["default_cost_restore_sha256"] = canonical_sha(
            self.state["clean_restore"]
        )
        self.state["phase"] = "BASELINE_COMPLETE_RESUMED"
        self.state["stage"] = "BASELINE_COMPLETE_RESUMED"
        self.state["stage_updated_at"] = now()
        self.save()

    def prepare_resume_after_runtime(self) -> None:
        """Archive the zero-invocation partial wave and prove a clean resume."""
        history = self.state.get("resume_history") or []
        if not history:
            raise RuntimeError("resume history missing")
        receipt = history[-1]
        resume_id = str(receipt["resume_id"])
        completed_waves = int(self.state.get("completed_search_waves") or 0)
        logical_wave = completed_waves + 1
        token = f"{self.args.method}_search_wave_{logical_wave:03d}"
        wave_dir = self.output / "search" / f"wave_{logical_wave:03d}"
        physical_failure_started = receipt.get("physical_failure_started") is True
        runtime_root = Path(str(self.freeze["storage"]["runtime_root"]))
        inflight_assignments = copy.deepcopy(
            (self.state.get("inflight_wave") or {}).get("assignments") or {}
        )
        archived: List[Dict[str, Any]] = []
        if physical_failure_started:
            evidence = (self.state.get("failed_wave_invocation_evidence") or [])[-1]
            receipts = evidence.get("receipts") or {}
            assignments = (self.state.get("inflight_wave") or {}).get("assignments") or {}
            if (
                set(receipts) != set(self.workers)
                or set(assignments) != set(self.workers)
                or any(
                    row.get("physical_invocation_started") is not True
                    for row in receipts.values()
                )
            ):
                raise RuntimeError("resume physical failed wave receipts differ")
            for worker_id in sorted(self.workers):
                worker = self.workers[worker_id]
                scene = str(worker["scene"])
                assignment = assignments[worker_id]
                params = dict(assignment["params"])
                if set(params) != set(self.active[scene]):
                    raise RuntimeError(
                        f"{worker_id}: consumed technical candidate cost set differs"
                    )
                self.state["observations"][scene].append({
                    "trial": int(assignment["rank"]),
                    "stage": "physical_technical_failure",
                    "policy": f"{self.args.method}_physical_slot_consumed_v1",
                    "params": params,
                    "param_signature": exact_signature(params),
                    "worker_id": worker_id,
                    "wave": logical_wave,
                    "valid": False,
                    "technical_failure": True,
                    "db_qps": 0.0,
                    "cost": 1e6,
                    "resource_means": {},
                    "hard_gate_checks": {
                        "physical_wave_completed": False,
                        "technical_failure_isolated": True,
                    },
                    "elapsed_s": 0.0,
                    "search_only_not_final_result": True,
                    "physical_invocation_evidence": copy.deepcopy(
                        receipts[worker_id]
                    ),
                    "failure_error": copy.deepcopy(receipt.get("prior_error")),
                    "not_told_to_tuner": True,
                    "replay_prohibited": True,
                })
            self.state["completed_search_waves"] = logical_wave
            receipt["consumed_physical_wave"] = {
                "stage": evidence.get("stage"),
                "wave_directory": str(wave_dir),
                "receipt_count": len(receipts),
                "replayed": False,
                "told_to_tuner": False,
                "retained": True,
            }
        else:
            if any(wave_dir.glob("**/client_processes.jsonl")):
                raise RuntimeError(
                    "resume partial wave unexpectedly contains client logs"
                )
            campaign_root = self.measurement_campaign_root(wave_dir)
            paths = (
                wave_dir,
                campaign_root / "xp_resets" / token,
                runtime_root / "quarantine" / campaign_root.name / token,
                # Retain the legacy locations in the audit even when absent.
                self.output / "search" / "xp_resets" / token,
                runtime_root / "quarantine" / "search" / token,
            )
            for source in paths:
                if not source.exists() and not source.is_symlink():
                    archived.append({"source": str(source), "existed": False})
                    continue
                target = source.with_name(
                    f"{source.name}.failed_prelaunch.{resume_id}"
                )
                if target.exists() or target.is_symlink():
                    raise RuntimeError(f"fresh resume archive required: {target}")
                source.rename(target)
                archived.append({
                    "source": str(source),
                    "archive": str(target),
                    "existed": True,
                    "recoverable": True,
                    "deleted": False,
                })
            retry_assignments = receipt.get("retry_assignments") or {}
            if retry_assignments:
                if (
                    retry_assignments != inflight_assignments
                    or receipt.get("failed_stage")
                    != f"SEARCH_WAVE_{logical_wave:03d}"
                ):
                    raise RuntimeError(
                        "publication resume retry assignment lineage differs"
                    )
                self.state["resume_retry_contract"] = {
                    "status": "PENDING_ZERO_INVOCATION_RETRY",
                    "logical_wave": logical_wave,
                    "stage": receipt["failed_stage"],
                    "assignments": copy.deepcopy(retry_assignments),
                    "assignment_sha256": canonical_sha(retry_assignments),
                    "prior_physical_invocation_started": False,
                    "candidate_budget_previously_consumed": False,
                    "retry_reason": "pre_workload_apply_cost_timeout",
                    "resume_id": resume_id,
                    "created_at": now(),
                }
        stored_active = self.state.get("active_parameters_by_scene") or {}
        stored_ranges = self.state.get("ranges_by_scene") or {}
        stored_defaults = self.state.get("default_optimizer_costs") or {}
        current_ranges = {
            scene: {
                name: {"lower": low, "upper": high}
                for name, (low, high) in self.ranges[scene].items()
            }
            for scene in SCENES
        }
        if (
            stored_active != self.active
            or stored_ranges != current_ranges
            or stored_defaults != self.defaults
        ):
            raise RuntimeError("resume cost domain drifted")
        self.state["inflight_wave"] = None
        receipt["partial_wave_archives"] = archived
        receipt["runtime_preflight_passed"] = True
        receipt["runtime_preflight_at"] = now()
        self.restore_defaults("resume_preflight")
        receipt["default_cost_restore_sha256"] = canonical_sha(
            self.state["clean_restore"]
        )
        self.state["phase"] = "SEARCH_RESUMING"
        self.state["stage"] = "SEARCH_RESUMING"
        self.state["stage_updated_at"] = now()
        self.save()

    def save(self) -> None:
        atomic(self.state_path, self.state)

    def measure(
        self,
        assignments: Mapping[str, Mapping[str, Any]],
        directory: Path,
        token: str,
        stage: str,
    ) -> Dict[str, Dict[str, Any]]:
        self.state["phase"] = stage
        self.state["inflight_wave"] = {
            "stage": stage,
            "output": str(directory),
            "assignments": copy.deepcopy(dict(assignments)),
            "started_at": now(),
        }
        self.save()
        try:
            rows = self.alg.measure_batch(
                self.freeze,
                self.workers,
                assignments,
                directory,
                token,
                state_path=self.state_path,
                stage_prefix=stage.upper(),
            )
        except BaseException:
            evidence = self.invocation_evidence(assignments, directory)
            self.account_failed_wave_evidence(stage, evidence)
            raise
        evidence = self.invocation_evidence(assignments, directory)
        for worker_id, row in rows.items():
            row["physical_invocation_evidence"] = evidence[worker_id]
        self.state["inflight_wave"] = None
        return rows

    def invocation_evidence(
        self,
        assignments: Mapping[str, Mapping[str, Any]],
        directory: Path,
    ) -> Dict[str, Dict[str, Any]]:
        receipts: Dict[str, Dict[str, Any]] = {}
        for worker_id in assignments:
            path = directory / worker_id / "client_processes.jsonl"
            line_count = 0
            if path.is_file():
                with path.open("r", encoding="utf-8") as stream:
                    line_count = sum(1 for line in stream if line.strip())
            receipts[worker_id] = {
                "path": str(path),
                "exists": path.is_file(),
                "record_count": line_count,
                "physical_invocation_started": line_count > 0,
                "sha256": sha(path) if path.is_file() else None,
            }
        return receipts

    def account_failed_wave_evidence(
        self, stage: str, evidence: Mapping[str, Mapping[str, Any]]
    ) -> None:
        invoked = sum(
            row.get("physical_invocation_started") is True
            for row in evidence.values()
        )
        self.state["physical_workload_invocations"] += invoked
        self.state["technical_failures"] += invoked
        self.state["prelaunch_failures"] += len(evidence) - invoked
        self.state["failed_wave_invocation_evidence"].append({
            "stage": stage,
            "captured_at": now(),
            "receipts": copy.deepcopy(dict(evidence)),
        })
        self.save()

    def account_rows(self, rows: Mapping[str, Mapping[str, Any]]) -> None:
        for row in rows.values():
            evidence = row.get("physical_invocation_evidence") or {}
            invoked = evidence.get("physical_invocation_started") is True
            if row.get("hard_gate_passed") is True and not invoked:
                raise RuntimeError("valid replay lacks physical invocation evidence")
            if not invoked:
                self.state["prelaunch_failures"] += 1
                continue
            self.state["physical_workload_invocations"] += 1
            if row.get("hard_gate_passed") is True:
                self.state["valid_replays"] += 1
            else:
                self.state["technical_failures"] += 1

    def run_baseline(self) -> None:
        repetitions = 2 if self.args.publication_mode else 1
        for repetition in range(1, repetitions + 1):
            assignments = {
                worker_id: {"rank": 0, "params": dict(self.defaults)}
                for worker_id in self.workers
            }
            rows = self.measure(
                assignments,
                self.output / "baseline" / f"rep_{repetition:02d}",
                f"{self.args.method}_baseline_rep{repetition:02d}",
                f"BASELINE_REP_{repetition:02d}",
            )
            self.account_rows(rows)
            for row in rows.values():
                self.state["baseline_rows"][str(row["scene"])].append(row)
            self.save()
            invalid_workers = sorted(
                worker_id
                for worker_id, row in rows.items()
                if row.get("hard_gate_passed") is not True
            )
            if invalid_workers:
                raise RuntimeError(
                    "baseline repetition hard gate failed: "
                    + ",".join(invalid_workers)
                )
        for scene in SCENES:
            worker_ids = self.state["worker_ids_by_scene"][scene]
            aggregate = aggregate_scene_repetitions(
                self.state["baseline_rows"][scene],
                scene,
                worker_ids,
                repetitions,
                "pre_search_baseline_aggregate",
            )
            if aggregate["valid"] is not True:
                raise RuntimeError(f"{scene}: immutable baseline hard gate failed")
            self.state["baseline"][scene] = aggregate
            for worker_id in worker_ids:
                qps_values = [
                    float(row.get("db_qps") or 0.0)
                    for row in self.state["baseline_rows"][scene]
                    if str(row.get("worker_id") or "") == worker_id
                ]
                self.state["worker_baseline_qps"][worker_id] = float(
                    np.median(qps_values)
                )
        self.state["baseline_wave_count"] = repetitions
        self.state["baseline_tuned_or_updated"] = False
        self.save()

    def run_post_search_baseline(self) -> None:
        if not self.args.publication_mode:
            return
        repetitions = 2
        for repetition in range(1, repetitions + 1):
            assignments = {
                worker_id: {"rank": 0, "params": dict(self.defaults)}
                for worker_id in self.workers
            }
            rows = self.measure(
                assignments,
                self.output / "post_search_baseline" / f"rep_{repetition:02d}",
                f"{self.args.method}_post_baseline_rep{repetition:02d}",
                f"POST_BASELINE_REP_{repetition:02d}",
            )
            self.account_rows(rows)
            for row in rows.values():
                self.state["post_baseline_rows"][str(row["scene"])].append(row)
        for scene in SCENES:
            worker_ids = self.state["worker_ids_by_scene"][scene]
            post = aggregate_scene_repetitions(
                self.state["post_baseline_rows"][scene],
                scene,
                worker_ids,
                repetitions,
                "post_search_baseline_aggregate",
            )
            comparison = aggregate_scene_repetitions(
                self.state["baseline_rows"][scene]
                + self.state["post_baseline_rows"][scene],
                scene,
                worker_ids,
                4,
                "pre_post_search_bracket_aggregate",
            )
            if post["valid"] is not True or comparison["valid"] is not True:
                raise RuntimeError(f"{scene}: post-search baseline hard gate failed")
            comparison["baseline_bracket"] = {
                "contract": "pre_post_search_default_bracket_v1",
                "pre_repetitions_per_worker": 2,
                "post_repetitions_per_worker": 2,
                "pre_db_qps": self.state["baseline"][scene]["db_qps"],
                "post_db_qps": post["db_qps"],
            }
            self.state["post_search_baseline"][scene] = post
            self.state["comparison_baseline"][scene] = comparison
        self.save()

    def capture_aqe_plans(self) -> Dict[str, Dict[str, Any]]:
        plans: Dict[str, Dict[str, Any]] = {}
        for scene in SCENES:
            representative = sorted(
                worker_id
                for worker_id, worker in self.workers.items()
                if worker.get("scene") == scene
            )[0]
            plan_log = (
                self.output
                / "aqetuner_plans"
                / "client_logs"
                / f"{representative}.jsonl"
            )
            plan_log.parent.mkdir(parents=True, exist_ok=True)
            # The normal measurement path injects this runtime-only field for
            # every wave.  Plan capture runs before a resumed measurement, so
            # it must own a dedicated pinned-client receipt instead of relying
            # on a stale baseline/wave mutation of ``self.workers``.
            self.workers[representative]["formal_client_log"] = str(plan_log)
            raw = self.alg.capture_scene_plans(
                self.freeze, scene, self.workers[representative]
            )
            aggregate = aggregate_mysql_plans(raw)
            path = self.output / "aqetuner_plans" / f"{scene}.json"
            atomic(path, aggregate)
            plans[scene] = aggregate
            self.state.setdefault("aqetuner_plan_capture", {})[scene] = {
                "path": str(path),
                "sha256": sha(path),
                "raw_plan_count": len(raw),
                "represented_query_count": len(aggregate.get("Children") or []),
                "all_query_roots_represented": (
                    len(aggregate.get("Children") or []) == len(raw)
                ),
                "maximum_nodes": 200,
                "node_budget_policy": (
                    "equal_per_query_root_then_depth_first_within_each_query"
                ),
                "aggregate_plan_sha256": canonical_sha(aggregate),
                "formal_client_log": str(plan_log),
            }
        self.save()
        return plans

    def load_aqe_plans_for_resume(self) -> Dict[str, Dict[str, Any]]:
        """Reuse the sealed plan inputs instead of recapturing them on resume."""
        capture = self.state.get("aqetuner_plan_capture") or {}
        if set(capture) != set(SCENES):
            raise RuntimeError("AQETuner resume plan-capture set differs")
        plans: Dict[str, Dict[str, Any]] = {}
        for scene in SCENES:
            receipt = capture[scene]
            path = Path(str(receipt.get("path") or "")).resolve()
            expected_path = (self.output / "aqetuner_plans" / f"{scene}.json").resolve()
            if (
                path != expected_path
                or not path.is_file()
                or sha(path) != receipt.get("sha256")
                or receipt.get("all_query_roots_represented") is not True
                or int(receipt.get("maximum_nodes") or 0) != 200
            ):
                raise RuntimeError(f"{scene}: AQETuner sealed resume plan differs")
            plan = load(path)
            if (
                plan.get("NodeType") != "Projection"
                or len(plan.get("Children") or [])
                != int(receipt.get("represented_query_count") or 0)
            ):
                raise RuntimeError(f"{scene}: AQETuner resume plan content differs")
            plans[scene] = plan
        self.state.setdefault("resume_history", [])[-1][
            "aqetuner_plan_capture_replayed"
        ] = False
        self.state["resume_history"][-1]["sealed_plan_reuse_at"] = now()
        self.save()
        return plans

    def build_policies(self) -> None:
        official_root = (
            self.args.gptuner_root.resolve()
            if self.args.method == "gptuner"
            else self.args.aqetuner_root.resolve()
        )
        if git_commit(official_root) != OFFICIAL_COMMITS[self.args.method]:
            raise RuntimeError("official baseline repository commit differs")
        status = subprocess.run(
            ["git", "-C", str(official_root), "status", "--short"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if status:
            raise RuntimeError("official baseline repository is not clean")
        plans = (
            self.load_aqe_plans_for_resume()
            if self.args.method == "aqetuner" and self.args.resume
            else (
                self.capture_aqe_plans()
                if self.args.method == "aqetuner" else {}
            )
        )
        for index, scene in enumerate(SCENES):
            common = {
                "scene": scene,
                "active_params": self.active[scene],
                "ranges": self.ranges[scene],
                "defaults": self.defaults,
                "observations": self.state["observations"][scene],
                "candidate_budget": self.args.candidate_budget,
                "seed": self.args.seed + index,
                "publication_mode": self.args.publication_mode,
            }
            if self.args.method == "gptuner":
                self.policies[scene] = GPTunerPolicy(
                    **common,
                    official_root=self.args.gptuner_root.resolve(),
                    knowledge_snapshot=(
                        self.args.gptuner_knowledge_root.resolve() / scene
                        if self.args.gptuner_knowledge_root is not None
                        else None
                    ),
                    expected_llm_model=self.args.gptuner_expected_llm_model,
                    expected_llm_provider=(
                        self.args.gptuner_expected_llm_provider
                    ),
                    allow_model_substitution=(
                        self.args.allow_gptuner_model_substitution
                    ),
                )
            else:
                scene_output = self.output / "aqetuner_model" / scene
                scene_output.mkdir(parents=True, exist_ok=True)
                self.policies[scene] = AQETunerPolicy(
                    **common,
                    official_root=self.args.aqetuner_root.resolve(),
                    plan=plans[scene],
                    output_dir=scene_output,
                )

    def run_search(self) -> None:
        wave = int(self.state.get("completed_search_waves") or 0)
        width = 1 if self.args.publication_mode else 2
        expected_observations = wave * width
        if any(
            len(self.state["observations"][scene]) != expected_observations
            for scene in SCENES
        ):
            raise RuntimeError("search wave/observation resume boundary differs")
        while len(self.state["observations"][SCENES[0]]) < self.args.candidate_budget:
            if self.stop_requested:
                return
            wave += 1
            width = 1 if self.args.publication_mode else 2
            suggestions = {
                scene: self.policies[scene].suggest(width) for scene in SCENES
            }
            assignments: Dict[str, Dict[str, Any]] = {}
            suggestion_by_worker: Dict[str, Dict[str, Any]] = {}
            for scene in SCENES:
                worker_ids = sorted(
                    worker_id
                    for worker_id, worker in self.workers.items()
                    if worker.get("scene") == scene
                )
                if len(suggestions[scene]) != width:
                    raise RuntimeError(f"{scene}: external search wave width differs")
                selected_workers = (
                    [worker_ids[(int(suggestions[scene][0]["trial"]) - 1) % 2]]
                    if self.args.publication_mode
                    else worker_ids
                )
                for worker_id, suggestion in zip(selected_workers, suggestions[scene], strict=True):
                    assignments[worker_id] = {
                        "rank": int(suggestion["trial"]),
                        "params": dict(suggestion["params"]),
                    }
                    suggestion_by_worker[worker_id] = suggestion
            retry_contract = self.state.get("resume_retry_contract") or {}
            if retry_contract:
                expected_stage = f"SEARCH_WAVE_{wave:03d}"
                if not (
                    retry_contract.get("status")
                    == "PENDING_ZERO_INVOCATION_RETRY"
                    and int(retry_contract.get("logical_wave") or 0) == wave
                    and retry_contract.get("stage") == expected_stage
                    and retry_contract.get("assignments") == assignments
                    and retry_contract.get("assignment_sha256")
                    == canonical_sha(assignments)
                    and retry_contract.get("prior_physical_invocation_started")
                    is False
                    and retry_contract.get("candidate_budget_previously_consumed")
                    is False
                ):
                    raise RuntimeError(
                        "publication zero-invocation retry candidate drifted"
                    )
                retry_contract["status"] = "VERIFIED_BEFORE_RETRY"
                retry_contract["verified_at"] = now()
                self.save()
            rows = self.measure(
                assignments,
                self.output / "search" / f"wave_{wave:03d}",
                f"{self.args.method}_search_wave_{wave:03d}",
                f"SEARCH_WAVE_{wave:03d}",
            )
            self.account_rows(rows)
            prelaunch_workers = sorted(
                worker_id
                for worker_id, row in rows.items()
                if (row.get("physical_invocation_evidence") or {}).get(
                    "physical_invocation_started"
                ) is not True
            )
            if prelaunch_workers:
                self.save()
                raise RuntimeError(
                    "search wave contains a zero-invocation candidate: "
                    + ",".join(prelaunch_workers)
                )
            for worker_id, row in rows.items():
                suggestion = suggestion_by_worker[worker_id]
                scene = str(row["scene"])
                valid = row.get("hard_gate_passed") is True
                baseline_qps = float(
                    self.state["worker_baseline_qps"].get(worker_id) or 0.0
                )
                objective_score = (
                    float(row.get("db_qps") or 0.0) / baseline_qps
                    if valid and baseline_qps > 0.0 else 0.0
                )
                observation = {
                    "trial": int(suggestion["trial"]),
                    "stage": suggestion["stage"],
                    "policy": suggestion["policy"],
                    "params": dict(suggestion["params"]),
                    "param_signature": exact_signature(suggestion["params"]),
                    "worker_id": worker_id,
                    "wave": wave,
                    "valid": valid,
                    "technical_failure": not valid,
                    "model_failure": False,
                    "db_qps": float(row.get("db_qps") or 0.0),
                    "cost": -objective_score if valid else 1e6,
                    "worker_baseline_qps": baseline_qps,
                    "objective_name": "same_worker_qps_ratio",
                    "objective_score": objective_score,
                    "resource_means": copy.deepcopy(row.get("resource_means") or {}),
                    "hard_gate_checks": copy.deepcopy(row.get("hard_gate_checks") or {}),
                    "physical_invocation_evidence": copy.deepcopy(
                        row.get("physical_invocation_evidence") or {}
                    ),
                    "elapsed_s": float(
                        (row.get("resource_summary") or {}).get("elapsed_seconds") or 0.0
                    ),
                    "search_only_not_final_result": True,
                }
                for field in (
                    "predicted_latency",
                    "predicted_failure_probability",
                    "constrained_expected_improvement",
                    "failure_probability_threshold",
                    "acquisition_value",
                    "pso_particle_id",
                    "pso_feedback_applied",
                ):
                    if field in suggestion:
                        observation[field] = suggestion[field]
                self.state["observations"][scene].append(observation)
                self.policies[scene].observe(suggestion, observation)
                self.state["candidate_physical_invocations"] += 1
            self.state["completed_search_waves"] = wave
            if retry_contract:
                completed_retry = copy.deepcopy(retry_contract)
                completed_retry["status"] = "COMPLETED"
                completed_retry["completed_at"] = now()
                completed_retry["physical_invocation_receipts"] = {
                    worker_id: copy.deepcopy(
                        row.get("physical_invocation_evidence") or {}
                    )
                    for worker_id, row in rows.items()
                }
                self.state.setdefault("resume_retry_history", []).append(
                    completed_retry
                )
                self.state["resume_retry_contract"] = None
            self.save()

    def freeze_champions(self) -> None:
        for scene in SCENES:
            baseline = self.state["baseline"][scene]
            candidates = [
                row for row in self.state["observations"][scene]
                if row.get("valid") is True
            ]
            best_candidate = max(
                candidates,
                key=lambda row: (
                    float(row.get("objective_score") or 0.0)
                    if self.args.publication_mode
                    else float(row.get("db_qps") or 0.0)
                ),
                default=None,
            )
            if (
                best_candidate is None
                or (
                    float(best_candidate.get("objective_score") or 0.0) <= 1.0
                    if self.args.publication_mode
                    else float(best_candidate["db_qps"]) <= float(baseline["db_qps"])
                )
            ):
                champion = {
                    "source": "immutable_default_baseline",
                    "trial": 0,
                    "params": dict(self.defaults),
                    "search_db_qps": float(baseline["db_qps"]),
                }
            else:
                champion = {
                    "source": "best_valid_search_observation",
                    "trial": int(best_candidate["trial"]),
                    "params": dict(best_candidate["params"]),
                    "search_db_qps": float(best_candidate["db_qps"]),
                    "search_worker_id": best_candidate["worker_id"],
                }
            if set(champion["params"]) != set(self.active[scene]) and champion["trial"] != 0:
                raise RuntimeError(f"{scene}: frozen champion active cost set differs")
            champion["frozen_param_signature"] = exact_signature(champion["params"])
            champion["frozen_at"] = now()
            self.state["champion"][scene] = champion
        self.state["champions_frozen"] = True
        self.save()

    def validate_champions(self) -> None:
        repetitions = 2 if self.args.publication_mode else 1
        for repetition in range(1, repetitions + 1):
            assignments: Dict[str, Dict[str, Any]] = {}
            for worker_id, worker in self.workers.items():
                scene = str(worker["scene"])
                params = {
                    **self.defaults,
                    **self.state["champion"][scene]["params"],
                }
                assignments[worker_id] = {"rank": 0, "params": params}
            rows = self.measure(
                assignments,
                self.output / "champion_validation" / f"rep_{repetition:02d}",
                f"{self.args.method}_champion_validation_rep{repetition:02d}",
                f"CHAMPION_VALIDATION_REP_{repetition:02d}",
            )
            self.account_rows(rows)
            for row in rows.values():
                self.state["champion_validation_rows"][str(row["scene"])].append(row)
        for scene in SCENES:
            aggregate = aggregate_scene_repetitions(
                self.state["champion_validation_rows"][scene],
                scene,
                self.state["worker_ids_by_scene"][scene],
                repetitions,
                "frozen_champion_aggregate",
            )
            if aggregate["valid"] is not True:
                raise RuntimeError(f"{scene}: frozen champion hard gate failed")
            comparison = (
                self.state["comparison_baseline"].get(scene)
                if self.args.publication_mode
                else self.state["baseline"].get(scene)
            ) or {}
            baseline_qps = float(comparison.get("db_qps") or 0.0)
            final_qps = float(aggregate["db_qps"])
            aggregate["baseline_qps"] = baseline_qps
            aggregate["relative_qps_improvement_pct"] = (
                final_qps / baseline_qps - 1.0
            ) * 100.0
            aggregate["absolute_qps_cross_method_comparable"] = False
            self.state["champion_validation"][scene] = aggregate
        self.save()

    def restore_defaults(self, reason: str) -> None:
        receipts = {
            worker_id: self.alg.apply_costs(worker, self.defaults)
            for worker_id, worker in self.workers.items()
        }
        if any(
            any(
                not math.isclose(
                    float(readback[name]),
                    float(self.defaults[name]),
                    rel_tol=self.alg.MYSQL_FLOAT_READBACK_REL_TOL,
                    abs_tol=self.alg.MYSQL_FLOAT_READBACK_ABS_TOL,
                )
                for name in self.defaults
            )
            for readback in receipts.values()
        ):
            raise RuntimeError("MySQL external baseline default restore differs")
        self.state["clean_restore"] = {
            "status": "PASS",
            "reason": reason,
            "completed_at": now(),
            "readback_by_worker": receipts,
            "readback_sha256": canonical_sha(receipts),
        }
        self.save()

    def finalize(self) -> None:
        method_metadata = {
            scene: self.policies[scene].metadata() for scene in SCENES
        }
        authorities: Dict[str, Any] = {}
        for scene in SCENES:
            authority = {
                "schema_version": "mysql_external_baseline_scene_authority_v2",
                "status": "complete",
                "method": self.args.method,
                "method_label": METHOD_LABELS[self.args.method],
                "seed": self.args.seed,
                "scene": scene,
                "formal_scope": "mysql_only",
                "cross_engine_comparison_prohibited": True,
                "active_parameters": self.active[scene],
                "active_parameter_count": 4,
                "inactive_optimizer_costs_fixed_to_default": sorted(
                    set(self.defaults) - set(self.active[scene])
                ),
                "parameter_ranges": {
                    name: {"lower": low, "upper": high}
                    for name, (low, high) in self.ranges[scene].items()
                },
                "baseline": self.state["baseline"][scene],
                "pre_search_baseline": self.state["baseline"][scene],
                "post_search_baseline": self.state["post_search_baseline"].get(scene),
                "comparison_baseline": (
                    self.state["comparison_baseline"].get(scene)
                    if self.args.publication_mode
                    else self.state["baseline"][scene]
                ),
                "baseline_wave_count": 2 if self.args.publication_mode else 1,
                "baseline_tuned_or_updated": False,
                "candidate_budget": self.args.candidate_budget,
                "candidate_observations": self.state["observations"][scene],
                "frozen_champion": self.state["champion"][scene],
                "final_champion_validation": self.state["champion_validation"][scene],
                "comparison_metric": self.state["comparison_metric"],
                "absolute_qps_cross_method_comparable": False,
                "publication_mode": self.args.publication_mode,
                "publication_protocol": (
                    "mysql_external_publication_fairness_v1"
                    if self.args.publication_mode else None
                ),
                "search_parallelism_per_scene": 1 if self.args.publication_mode else 2,
                "worker_baseline_qps": {
                    worker_id: self.state["worker_baseline_qps"][worker_id]
                    for worker_id in self.state["worker_ids_by_scene"][scene]
                },
                "plan_capture": (
                    (self.state.get("aqetuner_plan_capture") or {}).get(scene)
                    if self.args.method == "aqetuner" else None
                ),
                "method_metadata": method_metadata[scene],
                "clean_restore": self.state["clean_restore"],
                "technical_failures": sum(
                    row.get("technical_failure") is True
                    for row in self.state["observations"][scene]
                ),
                "candidate_physical_invocations": len(
                    self.state["observations"][scene]
                ),
                "worker_binding_sha256": self.state[
                    "independent_worker_binding"
                ]["sha256"],
                "search_observations_are_not_final_results": True,
                "manual_mutation_after_start": False,
            }
            path = self.output / scene / "authority_manifest.json"
            atomic(path, authority)
            authorities[scene] = {"path": str(path), "sha256": sha(path)}
        root_authority = {
            "schema_version": "mysql_four_scene_external_baseline_authority_v2",
            "status": "complete",
            "method": self.args.method,
            "method_label": METHOD_LABELS[self.args.method],
            "seed": self.args.seed,
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "active_scenes": list(SCENES),
            "two_workers_per_scene": True,
            "four_scenes_run_in_parallel": True,
            "baseline_policy": self.state["baseline_policy"],
            "comparison_metric": self.state["comparison_metric"],
            "absolute_qps_cross_method_comparable": False,
            "publication_mode": self.args.publication_mode,
            "publication_protocol": (
                "mysql_external_publication_fairness_v1"
                if self.args.publication_mode else None
            ),
            "candidate_budget_per_scene": self.args.candidate_budget,
            "scene_authorities": authorities,
            "physical_workload_invocations": self.state[
                "physical_workload_invocations"
            ],
            "valid_replays": self.state["valid_replays"],
            "technical_failures": self.state["technical_failures"],
            "candidate_physical_invocations": self.state[
                "candidate_physical_invocations"
            ],
            "worker_binding": copy.deepcopy(
                self.state["independent_worker_binding"]
            ),
            "clean_restore": self.state["clean_restore"],
            "started_at": self.state.get("started_at"),
            "completed_at": now(),
        }
        atomic(self.output / "authority_manifest.json", root_authority)
        self.state["status"] = "COMPLETE"
        self.state["phase"] = "COMPLETE"
        self.state["completed_at"] = now()
        self.state["root_authority"] = {
            "path": str(self.output / "authority_manifest.json"),
            "sha256": sha(self.output / "authority_manifest.json"),
        }
        self.save()

    def run(self) -> int:
        if self.args.resume:
            self.load_resume_state()
        elif self.args.resume_after_complete_baseline:
            self.load_complete_baseline_recovery_state()
        else:
            self.state = self.initial_state()
            self.save()
        try:
            self.initialize_runtime()
            if self.args.resume:
                self.prepare_resume_after_runtime()
            elif self.args.resume_after_complete_baseline:
                self.prepare_complete_baseline_recovery_after_runtime()
            else:
                self.state["active_parameters_by_scene"] = self.active
                self.state["ranges_by_scene"] = {
                    scene: {
                        name: {"lower": low, "upper": high}
                        for name, (low, high) in ranges.items()
                    }
                    for scene, ranges in self.ranges.items()
                }
                self.state["default_optimizer_costs"] = self.defaults
                self.state["independent_worker_binding"] = {
                    "path": str(self.args.worker_binding.resolve()),
                    "sha256": sha(self.args.worker_binding.resolve()),
                }
                self.save()
                self.run_baseline()
            if self.stop_requested:
                self.restore_defaults("clean_stop_after_baseline")
                self.state["status"] = "CLEAN_STOPPED"
                self.state["phase"] = "CLEAN_STOPPED"
                self.save()
                return 0
            self.build_policies()
            self.run_search()
            if self.stop_requested:
                self.restore_defaults("clean_stop_after_search_wave")
                self.state["status"] = "CLEAN_STOPPED"
                self.state["phase"] = "CLEAN_STOPPED"
                self.save()
                return 0
            self.run_post_search_baseline()
            self.freeze_champions()
            self.validate_champions()
            self.restore_defaults("campaign_complete")
            self.finalize()
            return 0
        except BaseException as error:
            self.state["status"] = "FAILED_CLOSED"
            self.state["phase"] = "FAILED_CLOSED"
            self.state["error"] = {
                "type": type(error).__name__,
                "message": str(error),
                "at": now(),
            }
            try:
                if self.workers and self.defaults:
                    self.restore_defaults("failed_closed_exception")
            except BaseException as cleanup_error:
                self.state["cleanup_error"] = {
                    "type": type(cleanup_error).__name__,
                    "message": str(cleanup_error),
                    "at": now(),
                }
            self.save()
            raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--method", choices=tuple(METHOD_LABELS), required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--worker-binding",
        type=Path,
        default=(
            ROOT / "config/formal/"
            "mysql_external_baseline_independent_workers_20260826_v1.json"
        ),
        help="Verified independent eight-container MySQL worker binding.",
    )
    parser.add_argument(
        "--candidate-budget",
        type=int,
        default=35,
        help=(
            "Per-scene physical candidate budget; publication mode uses 35 "
            "sequential feedback observations."
        ),
    )
    parser.add_argument("--seed", type=int, default=20260826)
    parser.add_argument(
        "--resume",
        action="store_true",
        help=(
            "Resume only a fully audited wave boundary or a publication "
            "fail-closed zero-invocation boundary from the same immutable "
            "baseline and observation history."
        ),
    )
    parser.add_argument(
        "--resume-after-complete-baseline",
        action="store_true",
        help=(
            "Recover the exact audited missing-NumPy failure after all 16 "
            "publication baseline invocations, without replaying baseline work."
        ),
    )
    parser.add_argument(
        "--gptuner-root",
        type=Path,
        default=ROOT / "baselines/GPTuner_official",
    )
    parser.add_argument(
        "--gptuner-knowledge-root",
        type=Path,
        default=None,
        help="Root containing one audited online GPT-4 snapshot per MySQL scene.",
    )
    parser.add_argument(
        "--gptuner-expected-llm-model",
        default="gpt-4",
    )
    parser.add_argument(
        "--gptuner-expected-llm-provider",
        default="openai-api",
    )
    parser.add_argument(
        "--allow-gptuner-model-substitution",
        action="store_true",
        help="Run a labelled non-paper-model GPTuner sensitivity variant.",
    )
    parser.add_argument(
        "--publication-mode",
        action="store_true",
        help="Enable the fail-closed sequential, normalized, drift-bracketed protocol.",
    )
    parser.add_argument(
        "--aqetuner-root",
        type=Path,
        default=ROOT / "baselines/AQETuner_official",
    )
    args = parser.parse_args()
    if args.candidate_budget < max(GPTUNER_COARSE_TRIALS, AQETUNER_WARM_SAMPLES):
        parser.error("candidate budget is below the required 9-point first stage")
    if not args.publication_mode and args.candidate_budget % 2:
        parser.error("candidate budget must be even for exact dual-worker waves")
    if args.resume and args.resume_after_complete_baseline:
        parser.error("resume modes are mutually exclusive")
    if args.resume_after_complete_baseline and not args.publication_mode:
        parser.error("complete-baseline recovery requires publication mode")
    if (
        args.gptuner_expected_llm_model != "gpt-4"
        and not args.allow_gptuner_model_substitution
    ):
        parser.error(
            "non-paper GPTuner model requires --allow-gptuner-model-substitution"
        )
    if (
        args.publication_mode
        and args.method == "gptuner"
        and args.gptuner_knowledge_root is None
    ):
        parser.error("publication GPTuner requires --gptuner-knowledge-root")
    if re.fullmatch(r"[A-Za-z0-9_.-]+", args.output_dir.name) is None:
        parser.error("unsafe output directory name")
    return args


def main() -> int:
    args = parse_args()
    lock_path = Path("/tmp/mysql_four_scene_external_baseline_v1.lock")
    with lock_path.open("w", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("another MySQL external baseline owns the lock") from error
        campaign = Campaign(args)
        for signum in CLEAN_STOP_SIGNALS:
            signal.signal(signum, campaign.request_stop)
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        return campaign.run()


if __name__ == "__main__":
    raise SystemExit(main())
