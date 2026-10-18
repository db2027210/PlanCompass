#!/usr/bin/env python3
"""Rebuild external-baseline XP workers from the sealed template.

This project-local successor keeps the production MySQL reset implementation
unchanged while allowing the isolated external-baseline runtime to live on
``/disk1`` (the PostgreSQL experiment SSD).  Immutable source templates remain
read-only on ``/disk2``.
"""

from __future__ import annotations

import argparse
import copy
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import threading
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import prepare_mysql_four_scene_v4 as prep


FREEZE = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260809_v6"
    / "four_scene_effective_resource_successor.json"
)
PREPARED = (
    ROOT / "history/mysql_four_scene_20260809/xp_native_iq1_iq6_reprepare2_v5_20260809T173300p0800"
    / "prepared_binding.json"
)
INVALID_R0 = (
    ROOT / "history/mysql_four_scene_20260809"
    / "four_scene_native_iq1_iq6_r0_v5_20260809T174100p0800/r0_binding.json"
)
XP_IDS = {"xp_cpu_w1", "xp_cpu_w2", "xp_io_w1", "xp_io_w2"}
XP_CONTAINER_ALLOWLIST = {
    "xp_cpu_w1": "ra_mysql_disk2_algc_xp_cpu_w1_20260809",
    "xp_cpu_w2": "ra_mysql_disk2_algc_xp_cpu_w2_20260809",
    "xp_io_w1": "ra_mysql_disk2_algc_xp_io_w1_20260809",
    "xp_io_w2": "ra_mysql_disk2_algc_xp_io_w2_20260809",
}
BENCHMARK_USER = "ra_benchmark"
IMAGE = prep.IMAGE
IMAGE_ID = prep.IMAGE_ID
RESET_SAME_FREEZE = False
OUTPUT_SCHEMA = "mysql_four_scene_v6_xp_reprepared_binding_v1"
AUTHORITY_VERSION = "v6"
EXTERNAL_OWNER = "mysql_external_baseline_disk1_v1"
DOCKER_REMOVE_TOTAL_TIMEOUT_SECONDS = 180
DOCKER_REMOVE_ATTEMPT_TIMEOUT_SECONDS = 60
CLEAN_MYSQL_SHUTDOWN_TIMEOUT_SECONDS = 30
DISCARD_MYSQL_EXIT_WAIT_SECONDS = 30
DISCARD_DOCKER_KILL_WAIT_SECONDS = 30
DOCKER_CONTROL_TIMEOUT_SECONDS = 240
DOCKER_JSON_ATTEMPT_TIMEOUT_SECONDS = 90
DOCKER_JSON_MAX_ATTEMPTS = 3
DOCKER_JSON_RETRY_BACKOFF_SECONDS = (2.0, 5.0)
# Docker control calls are metadata-plane operations and are not on the
# measured workload path.  Serializing them avoids daemon head-of-line stalls
# when four XP reset workers inspect/start/stop containers concurrently.
DOCKER_CONTROL_CONCURRENCY = 1
_DOCKER_CONTROL_SEMAPHORE = threading.BoundedSemaphore(DOCKER_CONTROL_CONCURRENCY)


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"regular non-symlink JSON required: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def purge_completed_discard_quarantine(
    *, quarantine_root: Path, reset_workers: Mapping[str, Mapping[str, Any]],
    binding_path: Path, cleanup_path: Path,
) -> dict[str, Any]:
    """Delete predecessor datadirs only after a successful reset binding.

    The complete datadir is useful while a reset can still be resumed.  Once
    the replacement containers, accounts, resource readbacks, and binding are
    complete, however, every predecessor is explicitly discard-only.  Keep a
    small audit artifact and remove the large trees so formal batches cannot
    accumulate terabytes of unreachable state.
    """
    binding = _load(binding_path)
    binding_sha256 = _sha(binding_path)
    if binding.get("status") not in {"PASS", "PREPARED"}:
        raise RuntimeError("quarantine cleanup requires a successful reset binding")
    quarantine = quarantine_root.resolve(strict=True)
    if (
        not str(quarantine).startswith(("/disk1/", "/disk2/"))
        or not (
            quarantine.parent.name == "quarantine"
            or quarantine.parent.parent.name == "quarantine"
        )
        or quarantine.is_symlink()
        or cleanup_path.parent.resolve() == quarantine
    ):
        raise RuntimeError("quarantine cleanup root authority differs")
    bound_workers = {
        str(row["worker_id"]): dict(row)
        for row in (
            binding.get("workers") or binding.get("reset_workers") or []
        )
        if row.get("worker_id") in reset_workers
    }
    if set(bound_workers) != set(reset_workers):
        raise RuntimeError("quarantine cleanup binding worker set differs")
    if cleanup_path.exists():
        prior = _load(cleanup_path)
        if (
            prior.get("status") != "PASS"
            or prior.get("reset_binding_sha256") != binding_sha256
            or any(Path(str(row["path"])).exists() for row in prior.get("deleted") or [])
        ):
            raise RuntimeError("existing quarantine cleanup evidence differs")
        return prior

    targets: list[dict[str, Any]] = []
    for worker_id in sorted(reset_workers):
        row = dict(reset_workers[worker_id])
        bound = bound_workers[worker_id]
        target = Path(str(row.get("quarantined_invalid_runtime_root") or ""))
        if (
            row.get("ready") is not True
            or bound.get("ready") is not True
            or bound.get("container_id") != row.get("container_id")
            or target.is_symlink()
            or not target.is_dir()
            or target.parent.resolve() != quarantine
            or target.name != worker_id
            or row.get("quarantined_predecessor_runtime_root") != str(target)
        ):
            raise RuntimeError(f"{worker_id} quarantine cleanup authority differs")
        inspect = _docker_json("inspect", str(row["container"]))
        mount_sources = [
            str(mount.get("Source") or "")
            for mount in (inspect.get("Mounts") or [])
        ]
        if (
            inspect.get("Id") != row.get("container_id")
            or (inspect.get("State") or {}).get("Running") is not True
            or any(
                source == str(target) or source.startswith(str(target) + "/")
                for source in mount_sources
            )
            or str(row.get("datadir") or "") not in mount_sources
        ):
            raise RuntimeError(f"{worker_id} live mount cleanup gate differs")
        targets.append({
            "worker_id": worker_id,
            "path": str(target),
            "bytes_before_delete": _host_tree_bytes(target),
            "replacement_container_id": row["container_id"],
            "replacement_datadir": row["datadir"],
            "worker_evidence_path": row.get("worker_evidence_path"),
            "worker_evidence_sha256": row.get("worker_evidence_sha256"),
            "discarded_datadir_never_reused": True,
        })
    for target_row in targets:
        target = Path(str(target_row["path"]))
        completed = subprocess.run(
            ["sudo", "-n", "rm", "-rf", "--", str(target)],
            capture_output=True, text=True, check=False, timeout=1800,
        )
        if completed.returncode != 0 or target.exists():
            raise RuntimeError(
                f"quarantine cleanup failed for {target}: {completed.stderr[-500:]}"
            )
    document = {
        "schema_version": "mysql_algorithm_c_completed_reset_quarantine_cleanup_v1",
        "status": "PASS",
        "created_at": _now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "policy": "zero_retention_after_successful_reset_binding",
        "interrupted_reset_quarantine_retained_until_recovery_complete": True,
        "quarantine_root": str(quarantine),
        "reset_binding_path": str(binding_path.resolve()),
        "reset_binding_sha256": binding_sha256,
        "deleted": targets,
        "deleted_worker_count": len(targets),
        "deleted_bytes": sum(int(row["bytes_before_delete"]) for row in targets),
        "all_targets_absent": True,
        "live_mounts_deleted": False,
        "sealed_templates_deleted": False,
        "recoverable": False,
    }
    temporary = cleanup_path.with_name(
        f".{cleanup_path.name}.{os.getpid()}.tmp"
    )
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, cleanup_path)
    cleanup_path.with_name(cleanup_path.name + ".sha256").write_text(
        f"{_sha(cleanup_path)}  {cleanup_path.name}\n", encoding="ascii",
    )
    return document


def _docker_run(
    argv: list[str], *, timeout: int = DOCKER_CONTROL_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    """Bound Docker control concurrency while tolerating daemon response lag."""
    with _DOCKER_CONTROL_SEMAPHORE:
        return prep._run(argv, timeout=timeout)


def _docker_json(*argv: str) -> dict[str, Any]:
    completed: subprocess.CompletedProcess[str] | None = None
    last_timeout: subprocess.TimeoutExpired | None = None
    for attempt in range(1, DOCKER_JSON_MAX_ATTEMPTS + 1):
        try:
            with _DOCKER_CONTROL_SEMAPHORE:
                completed = subprocess.run(
                    ["docker", *argv], capture_output=True, text=True,
                    check=True, timeout=DOCKER_JSON_ATTEMPT_TIMEOUT_SECONDS,
                )
            break
        except subprocess.TimeoutExpired as error:
            last_timeout = error
            if attempt >= DOCKER_JSON_MAX_ATTEMPTS:
                raise
            time.sleep(DOCKER_JSON_RETRY_BACKOFF_SECONDS[attempt - 1])
    if completed is None:
        raise RuntimeError(
            f"Docker {' '.join(argv)} produced no result after retries"
        ) from last_timeout
    value = json.loads(completed.stdout)
    if not isinstance(value, list) or len(value) != 1:
        raise RuntimeError(f"Docker {' '.join(argv)} cardinality differs")
    return dict(value[0])


def _host_tree_bytes(path: Path) -> int:
    value = subprocess.run(
        ["sudo", "-n", "du", "-sb", str(path)], capture_output=True, text=True,
        check=True, timeout=120,
    ).stdout.split()[0]
    return int(value)


def _host_mysql(
    worker: dict[str, Any], defaults: Path, sql: str, *, timeout: int = 60,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "mysql", f"--defaults-file={defaults}", "--host=127.0.0.1",
            f"--port={worker['host_port']}", "--connect-timeout=10", "-NBe", sql,
        ],
        capture_output=True, text=True, check=True, timeout=timeout,
    )


def _benchmark_defaults(password: str) -> str:
    return (
        "[client]\n"
        f"user={BENCHMARK_USER}\n"
        f"password={password}\n"
        "protocol=tcp\nssl-mode=DISABLED\nget-server-public-key=TRUE\n"
    )


def _write_private(path: Path, text: str) -> None:
    """Atomically create or replace a credential without a readable window."""
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        if temporary.exists():
            temporary.unlink()


def _namespace_run(
    container: str, command: list[str], *, timeout: int,
) -> subprocess.CompletedProcess[str]:
    inspect = _docker_json("inspect", container)
    state = inspect.get("State") or {}
    pid = int(state.get("Pid") or 0)
    if state.get("Running") is not True or pid <= 0:
        raise RuntimeError(f"{container} namespace target is not running")
    return subprocess.run(
        [
            "sudo", "-n", "nsenter", "-t", str(pid), "-m", "-n", "-p",
            "--", *command,
        ],
        capture_output=True, text=True, check=True, timeout=timeout,
    )


def _wait_mysql_host(
    worker: dict[str, Any], defaults: Path, *, timeout_seconds: int,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        inspect = _docker_json("inspect", str(worker["container"]))
        if (inspect.get("State") or {}).get("Running") is not True:
            raise RuntimeError(f"{worker['worker_id']} exited before MySQL readiness")
        try:
            _host_mysql(worker, defaults, "SELECT 1", timeout=15)
            return
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            time.sleep(1)
    raise RuntimeError(f"{worker['worker_id']} host TCP MySQL readiness timed out")


def _zero_swap_readback(
    inspect: Mapping[str, Any], worker: Mapping[str, Any]
) -> dict[str, Any]:
    """Prove zero effective swap when this host lacks memsw accounting."""
    container_id = str(inspect.get("Id") or "")
    pid = int((inspect.get("State") or {}).get("Pid") or 0)
    host = inspect.get("HostConfig") or {}
    docker_memory_swap = int(host.get("MemorySwap") or 0)
    memory_cgroup = prep._controller_path(
        pid, container_id, "memory", "memory"
    )
    swappiness_path = memory_cgroup / "memory.swappiness"
    write = subprocess.run(
        ["sudo", "-n", "tee", str(swappiness_path)],
        input="0\n", text=True, stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE, check=False, timeout=30,
    )
    if write.returncode != 0:
        raise RuntimeError(
            f"{worker['worker_id']} zero-swap cgroup write failed: "
            f"{write.stderr.strip()}"
        )
    kernel_swappiness = int(
        swappiness_path.read_text(encoding="ascii").strip()
    )
    memory_bytes = int(worker["memory_bytes"])
    if docker_memory_swap not in {-1, memory_bytes}:
        raise RuntimeError(
            f"{worker['worker_id']} Docker MemorySwap readback differs: "
            f"{docker_memory_swap}"
        )
    if kernel_swappiness != 0:
        raise RuntimeError(
            f"{worker['worker_id']} zero-swap swappiness readback differs: "
            f"kernel={kernel_swappiness}"
        )
    return {
        "DockerMemorySwap": docker_memory_swap,
        "DockerMemorySwappiness": host.get("MemorySwappiness"),
        "KernelMemorySwappiness": kernel_swappiness,
        "KernelMemorySwappinessPath": str(swappiness_path),
        "HostSwapDevices": prep._host_swap_devices(),
        "HostSwapLimitSupported": docker_memory_swap != -1,
        "EffectiveSwapBytes": 0,
    }


def _container_id(container: str) -> str:
    return _docker_run(
        ["docker", "ps", "-aq", "--filter", f"name=^{container}$"],
        timeout=DOCKER_CONTROL_TIMEOUT_SECONDS,
    ).stdout.strip()


def _remove_stopped_container(
    container: str,
    *,
    total_timeout_seconds: int = DOCKER_REMOVE_TOTAL_TIMEOUT_SECONDS,
) -> None:
    """Remove a stopped container and confirm the daemon-side outcome.

    Under concurrent XP resets the Docker client can exceed its local timeout
    while the daemon still completes removal.  Treat that timeout as an
    indeterminate outcome, poll the authoritative container inventory, and
    retry only while the exact name still exists.  ``docker rm`` remains
    non-forced, so an unexpectedly running container still fails closed.
    """
    if total_timeout_seconds <= 0:
        raise ValueError("Docker removal timeout must be positive")
    deadline = time.monotonic() + total_timeout_seconds
    last_timeout: subprocess.TimeoutExpired | None = None
    while True:
        if not _container_id(container):
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            if last_timeout is not None:
                raise subprocess.TimeoutExpired(
                    last_timeout.cmd,
                    total_timeout_seconds,
                    output=last_timeout.output,
                    stderr=last_timeout.stderr,
                )
            raise subprocess.TimeoutExpired(
                ["docker", "rm", container], total_timeout_seconds,
            )
        try:
            _docker_run(
                ["docker", "rm", container],
                timeout=max(
                    1,
                    min(DOCKER_REMOVE_ATTEMPT_TIMEOUT_SECONDS, int(remaining)),
                ),
            )
        except subprocess.TimeoutExpired as error:
            last_timeout = error
        except subprocess.CalledProcessError as error:
            if not _container_id(container):
                return
            diagnostic = f"{error.stdout or ''}\n{error.stderr or ''}".lower()
            if "removal of container" not in diagnostic or "in progress" not in diagnostic:
                raise
        if not _container_id(container):
            return
        time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))


def _container_running(container: str) -> bool:
    inspected = _docker_json("inspect", container)
    return (inspected.get("State") or {}).get("Running") is True


def _lift_discard_blkio(
    worker: dict[str, Any], inspect: dict[str, Any],
) -> dict[str, Any]:
    """Remove old-point throttles only after workload drain and before discard."""
    authority = dict(worker.get("blkio_authority") or {})
    container_id = str(inspect.get("Id") or "")
    pid = int((inspect.get("State") or {}).get("Pid") or 0)
    if len(container_id) != 64 or pid <= 1:
        raise RuntimeError("discard blkio container identity differs")
    expected_cgroup = (
        Path("/sys/fs/cgroup/blkio/docker-limited") / container_id
    )
    tasks_path = expected_cgroup / "tasks"
    if (
        str(expected_cgroup) != authority.get("cgroup_path")
        or expected_cgroup.is_symlink()
        or not expected_cgroup.is_dir()
        or tasks_path.is_symlink()
        or not tasks_path.is_file()
    ):
        raise RuntimeError("discard blkio cgroup binding differs")
    task_ids = [
        int(value) for value in tasks_path.read_text(encoding="ascii").split()
        if value.isdigit()
    ]
    bound_tasks = []
    for task_id in task_ids:
        try:
            task_cgroup = prep._controller_path(
                task_id, container_id, "blkio", "blkio",
            )
        except RuntimeError:
            continue
        if task_cgroup == expected_cgroup:
            bound_tasks.append(task_id)
    if not bound_tasks:
        raise RuntimeError("discard blkio cgroup has no reverse-bound tasks")
    device = str(authority.get("device_major_minor") or "")
    paths = {
        "read": Path(str(authority.get("read_throttle_file") or "")),
        "write": Path(str(authority.get("write_throttle_file") or "")),
    }
    prior = {
        "read": int(authority.get("read_bps") or 0),
        "write": int(authority.get("write_bps") or 0),
    }
    if (
        not device
        or any(path.parent != expected_cgroup for path in paths.values())
        or any(path.is_symlink() or not path.is_file() for path in paths.values())
        or any(rate <= 0 for rate in prior.values())
    ):
        raise RuntimeError("discard blkio throttle authority differs")
    for path in paths.values():
        result = subprocess.run(
            ["sudo", "-n", "tee", str(path)],
            input=f"{device} 0\n", text=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            check=False, timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"discard blkio lift failed: {result.stderr[-500:]}"
            )
    readback = {
        direction: prep._target_device_rate(path, device)
        for direction, path in paths.items()
    }
    if readback != {"read": None, "write": None}:
        raise RuntimeError(f"discard blkio lift readback differs: {readback}")
    return {
        "status": "LIFTED_FOR_DISCARD_ONLY",
        "container_id": container_id,
        "container_pid": pid,
        "reverse_bound_task_ids": bound_tasks,
        "cgroup_path": str(expected_cgroup),
        "device_major_minor": device,
        "prior_bps": prior,
        "readback_after_lift": readback,
        "new_measurement_container_must_restore_frozen_limits": True,
    }


def _stop_mysql_container(
    container: str, *, worker: dict[str, Any] | None = None,
    defaults: Path | None = None, bootstrap: bool = False,
    allow_failed_workload_nonzero_exit: bool = False,
) -> dict[str, Any]:
    """Bound shutdown of an old data plane that will never be reused.

    The next point always comes from the sealed template and the predecessor
    datadir is quarantined.  Set ``innodb_fast_shutdown=2`` only after client
    drain, then request authenticated shutdown.  If the discard-only instance
    still has not exited in 30 seconds, SIGKILL is safer than spending another
    120 seconds flushing a datadir that formal execution can never reuse.
    """
    if bootstrap or worker is None or defaults is None:
        raise ValueError("discard shutdown requires host worker credentials")
    started = time.monotonic()
    initial_inspect = _docker_json("inspect", container)
    initial_state = initial_inspect.get("State") or {}
    initial_container_id = str(initial_inspect.get("Id") or "")
    initial_container_pid = int(initial_state.get("Pid") or 0)
    if (
        initial_state.get("Running") is not True
        or initial_container_pid <= 1
        or not initial_container_id
    ):
        raise RuntimeError(f"{container} discard identity is not live")
    clean_error: str | None = None
    forced_kill = False
    forced_remove = False
    forced_host_pid_kill = False
    force_error: str | None = None
    fast_shutdown_readback: int | None = None
    discard_blkio: dict[str, Any] | None = None
    try:
        fast = _host_mysql(
            worker, defaults,
            "SET GLOBAL innodb_fast_shutdown=2; "
            "SELECT @@global.innodb_fast_shutdown;",
            timeout=30,
        ).stdout.strip()
        fast_shutdown_readback = int(fast)
        if fast_shutdown_readback != 2:
            raise RuntimeError("discard fast-shutdown readback differs")
        discard_blkio = _lift_discard_blkio(worker, initial_inspect)
        completed = subprocess.run(
            [
                "mysqladmin", f"--defaults-file={defaults}",
                "--host=127.0.0.1", f"--port={worker['host_port']}",
                "--connect-timeout=10", "shutdown",
            ],
            capture_output=True, text=True, check=False,
            timeout=CLEAN_MYSQL_SHUTDOWN_TIMEOUT_SECONDS,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"mysqladmin shutdown failed: {completed.stderr[-500:]}"
            )
        _docker_run(
            ["docker", "wait", container],
            timeout=DISCARD_MYSQL_EXIT_WAIT_SECONDS,
        )
    except Exception as error:
        clean_error = f"{type(error).__name__}: {error}"
        if discard_blkio is None:
            discard_blkio = _lift_discard_blkio(worker, initial_inspect)
        unthrottle_deadline = time.monotonic() + 10.0
        while (
            _container_id(container)
            and _container_running(container)
            and time.monotonic() < unthrottle_deadline
        ):
            time.sleep(0.5)
        if _container_id(container) and _container_running(container):
            try:
                _docker_run(
                    ["docker", "kill", "--signal=KILL", container], timeout=30,
                )
                forced_kill = True
                _docker_run(
                    ["docker", "wait", container],
                    timeout=DISCARD_DOCKER_KILL_WAIT_SECONDS,
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as kill_error:
                force_error = f"{type(kill_error).__name__}: {kill_error}"
                # A dead mysqld can remain as a zombie beneath docker-init;
                # Docker then reports Running while `docker kill` has no exit
                # event.  The container and its datadir are discard-only, so
                # force-remove the wrapper and prove the identity disappeared.
                if _container_id(container):
                    try:
                        _docker_run(
                            ["docker", "rm", "-f", container], timeout=60,
                        )
                    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                        pass
                    if _container_id(container):
                        try:
                            current = _docker_json("inspect", container)
                        except subprocess.CalledProcessError:
                            # The discard may complete between inventory and
                            # inspect.  Accept only after a second independent
                            # inventory proves the exact name is now absent.
                            if _container_id(container):
                                raise
                            forced_remove = True
                            current = None
                        if current is None:
                            pass
                        else:
                            current_state = current.get("State") or {}
                            if current.get("Id") != initial_container_id:
                                raise RuntimeError(
                                    f"{container} discard container identity changed"
                                )
                            # The pending SIGKILL may finish while `docker rm -f`
                            # is timing out.  A stopped container with the same ID
                            # is the expected completion, not an identity change.
                            if current_state.get("Running") is True:
                                if (
                                    int(current_state.get("Pid") or 0)
                                    != initial_container_pid
                                ):
                                    raise RuntimeError(
                                        f"{container} live discard PID changed"
                                    )
                                killed = subprocess.run(
                                    [
                                        "sudo", "-n", "kill", "-KILL",
                                        str(initial_container_pid),
                                    ],
                                    capture_output=True, text=True, check=False,
                                    timeout=30,
                                )
                                if killed.returncode != 0:
                                    raise RuntimeError(
                                        f"{container} host-PID kill failed: "
                                        f"{killed.stderr[-500:]}"
                                    )
                                forced_host_pid_kill = True
                                stopped_deadline = time.monotonic() + 30.0
                                while (
                                    _container_id(container)
                                    and _container_running(container)
                                    and time.monotonic() < stopped_deadline
                                ):
                                    time.sleep(0.5)
                                if _container_id(container) and _container_running(container):
                                    raise RuntimeError(
                                        f"{container} remained running after bound host-PID kill"
                                    )
                    else:
                        forced_remove = True
    if _container_id(container):
        inspect = _docker_json("inspect", container)
        state = inspect.get("State") or {}
        if state.get("Running") is True:
            raise RuntimeError(f"{container} remained running after discard shutdown")
        exit_code: int | None = int(state.get("ExitCode"))
    else:
        exit_code = None
    if forced_remove:
        method = "discard_zombie_wrapper_forced_remove"
    elif forced_host_pid_kill:
        if exit_code != 137:
            raise RuntimeError(
                f"{container} host-PID discard exit differs: {exit_code}"
            )
        method = "discard_zombie_wrapper_host_pid_kill"
    elif forced_kill:
        if exit_code != 137:
            raise RuntimeError(f"{container} discard kill exit code differs: {exit_code}")
        method = "discard_fast_shutdown_sigkill_fallback"
    else:
        if exit_code == 137 and clean_error is not None:
            method = "discard_pending_sigkill_completed_after_blkio_lift"
        elif exit_code != 0 and allow_failed_workload_nonzero_exit:
            method = "discard_failed_workload_nonzero_exit"
        elif exit_code != 0:
            raise RuntimeError(f"{container} authenticated shutdown exit differs: {exit_code}")
        else:
            method = "discard_fast_shutdown_authenticated"
    return {
        "container": container,
        "method": method,
        "clean_shutdown_error": clean_error,
        "innodb_fast_shutdown_readback": fast_shutdown_readback,
        "discard_blkio": discard_blkio,
        "forced_kill": forced_kill,
        "forced_remove": forced_remove,
        "forced_host_pid_kill": forced_host_pid_kill,
        "force_error": force_error,
        "initial_container_id": initial_container_id,
        "initial_container_pid": initial_container_pid,
        "container_exit_code": exit_code,
        "container_absent_after_shutdown": not bool(_container_id(container)),
        "predecessor_discard_only": True,
        "discarded_datadir_never_reused": True,
        "elapsed_seconds": time.monotonic() - started,
        "verified_stopped": True,
    }


def _prepare_fresh_xp(
    freeze: dict[str, Any], worker: dict[str, Any], prior: dict[str, Any],
    quarantine_root: Path, output_root: Path, resume_stalled_copy: bool,
    reset_context: str = "invalid_r0",
    allow_unresponsive_old_container: bool = False,
    allow_bootstrap_touched_staging: bool = False,
    force_fresh_copy_on_resume: bool = False,
    allow_absent_predecessor_after_interrupted_discard: bool = False,
) -> dict[str, Any]:
    worker_id = str(worker["worker_id"])
    if reset_context not in {"invalid_r0", "formal_point"}:
        raise RuntimeError(f"unsupported XP reset context: {reset_context!r}")
    if worker_id not in XP_IDS or re.fullmatch(r"[a-z0-9_]+", worker_id) is None:
        raise RuntimeError(f"unsafe XP worker id: {worker_id!r}")
    container = str(worker["container"])
    expected_container = XP_CONTAINER_ALLOWLIST.get(worker_id)
    if container != expected_container or "0708" in container:
        raise RuntimeError(
            f"{worker_id} container is outside the experiment allowlist: "
            f"{container!r} != {expected_container!r}"
        )
    if str(prior.get("container") or "") != expected_container:
        raise RuntimeError(
            f"{worker_id} predecessor container is outside the experiment allowlist"
        )
    prior_datadir = Path(str(prior.get("datadir") or "")).resolve()
    runtime_root = prior_datadir.parent
    datadir = runtime_root / "datadir"
    credentials = runtime_root / "credentials"
    old_target = quarantine_root / worker_id
    shutdown_audit: list[dict[str, Any]] = []
    reusable_stopped_container_id: str | None = None
    template = Path(str(freeze["templates"][worker["scene"]])).resolve(strict=True)
    io_profile = freeze.get("xp_innodb_io_profile") or {}
    if (
        worker["scene"] not in (io_profile.get("applies_to_scenes") or [])
        or io_profile.get("container_init") is not True
        or io_profile.get("innodb_use_native_aio") is not False
        or io_profile.get("innodb_flush_method") != "fsync"
        or io_profile.get("runtime_readback_required") is not True
        or io_profile.get("resource_envelope_unchanged") is not True
    ):
        raise RuntimeError(f"{worker_id} frozen XP InnoDB I/O profile differs")
    local_image = _docker_json("image", "inspect", IMAGE)
    if local_image.get("Id") != IMAGE_ID:
        raise RuntimeError(f"{worker_id} frozen local MySQL image identity differs")
    if (
        prior_datadir != datadir
        or Path(str(worker.get("datadir") or "")).resolve() != datadir
        or not str(runtime_root).startswith(("/disk1/", "/disk2/"))
        or not str(old_target).startswith(("/disk1/", "/disk2/"))
        or not str(template).startswith("/disk2/")
    ):
        raise RuntimeError(f"{worker_id} reset path authority differs")
    existing = _docker_run(
        ["docker", "ps", "-aq", "--filter", f"name=^{container}$"],
        timeout=DOCKER_CONTROL_TIMEOUT_SECONDS,
    ).stdout.strip()
    resuming_this_worker = resume_stalled_copy and old_target.is_dir()
    if resuming_this_worker:
        if not datadir.is_dir() or not credentials.is_dir():
            raise RuntimeError(f"{worker_id} stalled-copy staging authority differs")
        if existing:
            staged = _docker_json("inspect", container)
            staged_state = staged.get("State") or {}
            staged_labels = (staged.get("Config") or {}).get("Labels") or {}
            staged_mounts = [
                row for row in (staged.get("Mounts") or [])
                if row.get("Destination") == "/var/lib/mysql"
            ]
            if (
                staged_labels.get("ra.owner") != EXTERNAL_OWNER
                or staged_labels.get("ra.worker_id") != worker_id
                or len(staged_mounts) != 1
                or staged_mounts[0].get("Source") != str(datadir)
            ):
                raise RuntimeError(f"{worker_id} resumed container identity differs")
            if staged_state.get("Running") is True:
                shutdown_audit.append(_stop_mysql_container(
                    container, worker=prior,
                    defaults=Path(str(prior["credential_defaults"])),
                    allow_failed_workload_nonzero_exit=(
                        allow_unresponsive_old_container
                    ),
                ))
            surviving_id = _container_id(container)
            if surviving_id:
                surviving = _docker_json("inspect", container)
                if surviving.get("Id") != prior.get("container_id"):
                    raise RuntimeError(
                        f"{worker_id} reusable stopped container identity differs"
                    )
                reusable_stopped_container_id = str(surviving["Id"])
    else:
        if old_target.exists():
            raise RuntimeError(f"{worker_id} quarantine target already exists")
        inspected = _docker_json("inspect", container) if existing else {}
        state = inspected.get("State") or {}
        old_container_ready = state.get("Running") is True
        old_container_stopped_by_stalled_run = (
            resume_stalled_copy
            and state.get("Status") == "exited"
            and (
                state.get("ExitCode") == 0
                or (
                    allow_unresponsive_old_container
                    and int(state.get("ExitCode") or 0) != 0
                )
            )
        )
        stopped_after_failed_workload_discard = (
            old_container_stopped_by_stalled_run
            and allow_unresponsive_old_container
            and int(state.get("ExitCode") or 0) != 0
        )
        absent_after_interrupted_discard = (
            resume_stalled_copy
            and allow_unresponsive_old_container
            and allow_absent_predecessor_after_interrupted_discard
            and not existing
        )
        if (
            (
                not absent_after_interrupted_discard
                and (
                    not existing
                    or inspected.get("Id") != prior.get("container_id")
                    or not (
                        old_container_ready
                        or old_container_stopped_by_stalled_run
                    )
                    or (inspected.get("Config") or {}).get("Labels", {}).get(
                        "ra.owner"
                    ) != EXTERNAL_OWNER
                )
            )
        ):
            raise RuntimeError(f"{worker_id} old container identity differs")
        if absent_after_interrupted_discard:
            prior_container_id = str(prior.get("container_id") or "")
            prior_cgroup = Path(str(
                (prior.get("blkio_authority") or {}).get("cgroup_path") or ""
            ))
            expected_cgroup = (
                Path("/sys/fs/cgroup/blkio/docker-limited")
                / prior_container_id
            )
            if (
                len(prior_container_id) != 64
                or prior_cgroup != expected_cgroup
                or prior_cgroup.exists()
                or not runtime_root.is_dir()
                or not datadir.is_dir()
                or not credentials.is_dir()
            ):
                raise RuntimeError(
                    f"{worker_id} interrupted discard recovery authority differs"
                )
            shutdown_audit.append({
                "container": container,
                "method": "discard_interrupted_completion_recovered",
                "clean_shutdown_error": (
                    "prior discard worker ended after verified predecessor exit"
                ),
                "innodb_fast_shutdown_readback": None,
                "discard_blkio": {
                    "status": "CGROUP_ABSENT_AFTER_PRIOR_DISCARD_LIFT",
                    "container_id": prior_container_id,
                    "cgroup_path": str(prior_cgroup),
                    "new_measurement_container_must_restore_frozen_limits": True,
                },
                "forced_kill": False,
                "forced_remove": False,
                "forced_host_pid_kill": False,
                "force_error": None,
                "initial_container_id": prior_container_id,
                "initial_container_pid": int(prior.get("container_pid") or 0),
                "container_exit_code": None,
                "container_absent_after_shutdown": True,
                "predecessor_discard_only": True,
                "discarded_datadir_never_reused": True,
                "elapsed_seconds": 0.0,
                "verified_stopped": True,
                "interrupted_resume_authority": True,
            })
        if stopped_after_failed_workload_discard:
            prior_container_id = str(prior.get("container_id") or "")
            prior_cgroup = Path(str(
                (prior.get("blkio_authority") or {}).get("cgroup_path") or ""
            ))
            expected_cgroup = (
                Path("/sys/fs/cgroup/blkio/docker-limited")
                / prior_container_id
            )
            if (
                inspected.get("Id") != prior_container_id
                or len(prior_container_id) != 64
                or prior_cgroup != expected_cgroup
                or prior_cgroup.exists()
            ):
                raise RuntimeError(
                    f"{worker_id} stopped failed-workload discard authority differs"
                )
            shutdown_audit.append({
                "container": container,
                "method": "discard_failed_workload_nonzero_exit_recovered",
                "clean_shutdown_error": (
                    "prior discard completed with nonzero failed-workload exit"
                ),
                "innodb_fast_shutdown_readback": None,
                "discard_blkio": {
                    "status": "CGROUP_ABSENT_AFTER_PRIOR_DISCARD_LIFT",
                    "container_id": prior_container_id,
                    "cgroup_path": str(prior_cgroup),
                    "new_measurement_container_must_restore_frozen_limits": True,
                },
                "forced_kill": False,
                "forced_remove": False,
                "forced_host_pid_kill": False,
                "force_error": None,
                "initial_container_id": prior_container_id,
                "initial_container_pid": int(prior.get("container_pid") or 0),
                "container_exit_code": int(state["ExitCode"]),
                "container_absent_after_shutdown": False,
                "predecessor_discard_only": True,
                "discarded_datadir_never_reused": True,
                "elapsed_seconds": 0.0,
                "verified_stopped": True,
                "interrupted_resume_authority": True,
            })
        if old_container_ready:
            if not allow_unresponsive_old_container:
                active = _host_mysql(
                    worker,
                    Path(str(prior["credential_defaults"])),
                    "SELECT COUNT(*) FROM information_schema.processlist "
                    "WHERE ID<>CONNECTION_ID() AND COMMAND<>'Daemon' "
                    "AND USER<>'event_scheduler'",
                    timeout=30,
                ).stdout.strip()
                if active != "0":
                    raise RuntimeError(
                        f"{worker_id} still has active non-system sessions"
                    )
            shutdown_audit.append(_stop_mysql_container(
                container, worker=prior,
                defaults=Path(str(prior["credential_defaults"])),
                allow_failed_workload_nonzero_exit=(
                    allow_unresponsive_old_container
                ),
            ))
        surviving_id = _container_id(container)
        if surviving_id:
            surviving = _docker_json("inspect", container)
            if surviving.get("Id") != prior.get("container_id"):
                raise RuntimeError(
                    f"{worker_id} reusable stopped container identity differs"
                )
            reusable_stopped_container_id = str(surviving["Id"])
        old_target.parent.mkdir(parents=True, exist_ok=True)
        os.rename(runtime_root, old_target)
        runtime_root.mkdir(mode=0o700)
        datadir.mkdir(mode=0o700)
        credentials.mkdir(mode=0o700)
    password = secrets.token_hex(32)
    benchmark_password = secrets.token_hex(32)
    defaults = credentials / "selected.cnf"
    benchmark_defaults = credentials / "benchmark.cnf"
    credential_init = credentials / "reset-init.sql"
    _write_private(defaults, prep._defaults(password))
    _write_private(
        benchmark_defaults,
        _benchmark_defaults(benchmark_password),
    )
    credential_init_sql = (
        "CREATE USER IF NOT EXISTS 'root'@'%' IDENTIFIED WITH "
        f"caching_sha2_password BY '{password}';\n"
        "ALTER USER 'root'@'%' IDENTIFIED WITH caching_sha2_password "
        f"BY '{password}';\n"
        "CREATE USER IF NOT EXISTS 'root'@'localhost' IDENTIFIED WITH "
        f"caching_sha2_password BY '{password}';\n"
        "ALTER USER 'root'@'localhost' IDENTIFIED WITH caching_sha2_password "
        f"BY '{password}';\n"
        f"CREATE USER IF NOT EXISTS '{BENCHMARK_USER}'@'%' IDENTIFIED WITH "
        f"caching_sha2_password BY '{benchmark_password}';\n"
        f"ALTER USER '{BENCHMARK_USER}'@'%' IDENTIFIED WITH "
        f"caching_sha2_password BY '{benchmark_password}';\n"
        f"GRANT ALL PRIVILEGES ON `hybench_sf10x`.* TO '{BENCHMARK_USER}'@'%';\n"
        "FLUSH PRIVILEGES;\n"
    )
    _write_private(credential_init, credential_init_sql)
    credential_init_sha256 = _sha(credential_init)
    subprocess.run(
        ["sudo", "-n", "chown", "999:999", str(credential_init)],
        check=True, timeout=30,
    )
    subprocess.run(
        ["sudo", "-n", "chmod", "0400", str(credential_init)],
        check=True, timeout=30,
    )

    first_cpu_segment = str(worker["database_cpuset"]).split(",", 1)[0]
    copied_at = time.monotonic()
    source_bytes = _host_tree_bytes(template)
    staged_bytes = _host_tree_bytes(datadir)
    byte_equivalent_staging = (
        resuming_this_worker
        and abs(staged_bytes - source_bytes) <= 4 * 1024 * 1024
    )
    bootstrap_staging_structure_complete = False
    if resuming_this_worker and allow_bootstrap_touched_staging:
        bootstrap_staging_required_paths = (
            ("-f", "auto.cnf"), ("-f", "ibdata1"), ("-f", "mysql.ibd"),
            ("-d", "hybench_sf10x"), ("-d", "mysql"),
        )
        bootstrap_staging_structure_complete = all(
            subprocess.run(
                ["sudo", "-n", "test", predicate, str(datadir / name)],
                check=False, timeout=15,
            ).returncode == 0
            for predicate, name in bootstrap_staging_required_paths
        )
    bootstrap_touched_staging = (
        resuming_this_worker
        and allow_bootstrap_touched_staging
        and staged_bytes >= int(source_bytes * 0.90)
        and bootstrap_staging_structure_complete
    )
    copy_reused = (
        not force_fresh_copy_on_resume
        and (byte_equivalent_staging or bootstrap_touched_staging)
    )
    if not copy_reused:
        if staged_bytes > 1024 * 1024:
            if not resuming_this_worker:
                raise RuntimeError(f"{worker_id} partial staging copy is not reusable")
            failed_staging_suffix = (
                f"after_forced_fresh_copy_{output_root.name}"
                if force_fresh_copy_on_resume
                else "after_exec_timeout"
            )
            failed_staging = old_target.parent / (
                f"{worker_id}_failed_staging_{failed_staging_suffix}"
            )
            if failed_staging.exists():
                raise RuntimeError(f"{worker_id} failed-staging quarantine already exists")
            subprocess.run(
                ["sudo", "-n", "mv", str(datadir), str(failed_staging)],
                check=True, timeout=60,
            )
            datadir.mkdir(mode=0o700)
        subprocess.run(
            [
                "taskset", "-c", first_cpu_segment, "sudo", "-n", "cp", "-a",
                "--reflink=never", f"{template}/.", str(datadir),
            ], check=True, timeout=1800,
        )
    subprocess.run(
        ["sudo", "-n", "rm", "-f", str(datadir / "mysql.sock"),
         str(datadir / "mysqld.sock"), str(datadir / "mysqld.pid")],
        check=True, timeout=30,
    )
    subprocess.run(
        ["sudo", "-n", "chown", "-R", "999:999", str(datadir)],
        check=True, timeout=600,
    )
    subprocess.run(
        ["sudo", "-n", "find", str(datadir), "-type", "d", "-exec",
         "chmod", "0750", "{}", "+"],
        check=True, timeout=600,
    )
    subprocess.run(
        ["sudo", "-n", "find", str(datadir), "-type", "f", "-exec",
         "chmod", "0640", "{}", "+"],
        check=True, timeout=600,
    )
    copy_seconds = time.monotonic() - copied_at
    copied_bytes = _host_tree_bytes(datadir)
    if (
        not bootstrap_touched_staging
        and abs(copied_bytes - source_bytes) > 4 * 1024 * 1024
    ):
        raise RuntimeError(f"{worker_id} fresh copy byte size differs")

    docker_create_argv = [
            "docker", "run", "-d", "--init", "--name", container,
            "--label", f"ra.owner={EXTERNAL_OWNER}",
            "--label", "ra.external_baseline_plane=disk1_v2",
            "--label", f"ra.scenario={worker['scene']}",
            "--label", f"ra.worker_id={worker_id}",
            "--label", f"ra.data_plane_origin=xp_template_reset_after_{reset_context}",
            "--cpus", str(worker["cpu_quota_cores"]),
            "--cpuset-cpus", str(worker["database_cpuset"]),
            "--cpuset-mems", str(worker["database_cpuset_mems"]),
            "--memory", str(worker["memory_bytes"]),
            "--memory-swap", str(worker["memory_bytes"]),
            "--memory-swappiness", "0",
            "--pids-limit", str(worker["pids_max"]),
            "--device-read-bps", f"{freeze['storage']['device']}:{worker['io_read_bps']}",
            "--device-write-bps", f"{freeze['storage']['device']}:{worker['io_write_bps']}",
            "-p", f"127.0.0.1:{worker['host_port']}:3306",
            "-v", f"{datadir}:/var/lib/mysql:rw",
            "-v", f"{credentials}:/run/ra-credentials:ro",
            # Mount the private file directly so mysqld uid 999 does not need
            # traversal permission on the host-only 0700 credentials directory.
            "-v", f"{credential_init}:/run/ra-init/reset.sql:ro",
            IMAGE, "mysqld", "--init-file=/run/ra-init/reset.sql",
            "--persisted-globals-load=OFF", "--bind-address=0.0.0.0",
            f"--innodb-buffer-pool-size={worker['innodb_buffer_pool_bytes']}",
            "--innodb-use-native-aio=OFF", "--innodb-flush-method=fsync",
            "--innodb-flush-log-at-trx-commit=2",
            "--skip-log-bin", "--max-connections=128", "--performance-schema=ON",
        ]
    container_creation_avoided = reusable_stopped_container_id is not None
    recovery_after_docker_start_response_timeout = False
    if container_creation_avoided:
        stopped = _docker_json("inspect", container)
        stopped_state = stopped.get("State") or {}
        stopped_mounts = {
            str(row.get("Destination")): str(row.get("Source"))
            for row in (stopped.get("Mounts") or [])
        }
        stopped_command = (stopped.get("Config") or {}).get("Cmd") or []
        if (
            stopped.get("Id") != reusable_stopped_container_id
            or stopped_state.get("Running") is not False
            or stopped_mounts.get("/var/lib/mysql") != str(datadir)
            or stopped_mounts.get("/run/ra-credentials") != str(credentials)
            or stopped_mounts.get("/run/ra-init/reset.sql")
            != str(credential_init)
            or "--init-file=/run/ra-init/reset.sql" not in stopped_command
        ):
            raise RuntimeError(f"{worker_id} reusable stopped container differs")
        try:
            _docker_run(["docker", "start", container], timeout=120)
        except subprocess.TimeoutExpired:
            recovery_after_docker_start_response_timeout = True
            started = _docker_json("inspect", container)
            if (
                started.get("Id") != reusable_stopped_container_id
                or (started.get("State") or {}).get("Running") is not True
            ):
                raise
    else:
        _docker_run(docker_create_argv, timeout=300)
    _wait_mysql_host(worker, defaults, timeout_seconds=300)
    _host_mysql(
        worker, defaults,
        "UPDATE mysql.server_cost SET cost_value=NULL; "
        "UPDATE mysql.engine_cost SET cost_value=NULL; FLUSH OPTIMIZER_COSTS;",
        timeout=60,
    )
    query = _host_mysql(
        worker, defaults,
        "SELECT @@version,@@global.innodb_buffer_pool_size,"
        "@@global.innodb_use_native_aio,@@global.innodb_flush_method,"
        "@@global.innodb_fast_shutdown; "
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_schema='hybench_sf10x';",
        timeout=60,
    ).stdout.splitlines()
    if len(query) != 2 or int(query[1]) != 8:
        raise RuntimeError(f"{worker_id} fresh schema readback differs")
    mysql_version, buffer_pool, native_aio, flush_method, fast_shutdown = (
        query[0].split("\t")
    )
    if int(buffer_pool) != int(worker["innodb_buffer_pool_bytes"]):
        raise RuntimeError(f"{worker_id} buffer pool readback differs")
    if native_aio != "0" or flush_method.lower() != "fsync":
        raise RuntimeError(f"{worker_id} InnoDB I/O readback differs")
    if fast_shutdown != "1":
        raise RuntimeError(f"{worker_id} measurement fast-shutdown setting differs")
    inspect = _docker_json("inspect", container)
    host = inspect.get("HostConfig") or {}
    expected_host = {
        "CpusetCpus": worker["database_cpuset"],
        "CpusetMems": worker["database_cpuset_mems"],
        "NanoCpus": int(round(float(worker["cpu_quota_cores"]) * 1_000_000_000)),
        "Memory": int(worker["memory_bytes"]),
        # Docker interprets MemorySwap == Memory as zero effective container
        # swap.  Host swap-device presence is therefore not a container-level
        # resource violation and must not fail an isolated worker reset.
        "PidsLimit": int(worker["pids_max"]),
        "Init": True,
    }
    differences = {
        key: {"actual": host.get(key), "expected": value}
        for key, value in expected_host.items() if host.get(key) != value
    }
    if differences:
        raise RuntimeError(f"{worker_id} Docker/reset resource differs: {differences}")
    kernel = prep._kernel_resource_readback(inspect, worker)
    swap_authority = _zero_swap_readback(inspect, worker)
    blkio = prep._enforce_blkio(inspect, freeze, worker)
    row = {
        **prior,
        **worker,
        "container_id": inspect["Id"],
        "container_pid": (inspect.get("State") or {}).get("Pid"),
        "datadir": str(datadir),
        "credential_defaults": str(defaults),
        "template": str(template),
        "source_bytes": source_bytes,
        "copied_bytes_before_bootstrap": copied_bytes,
        "copy_mode": "cp_a_reflink_never",
        "copy_seconds": copy_seconds,
        "reset_bootstrap_attempts": [],
        "credential_initialization": "mysqld_init_file",
        "credential_init_file": str(credential_init),
        "credential_init_file_sha256": credential_init_sha256,
        "credential_init_file_mode": "0o400",
        "benchmark_defaults": str(benchmark_defaults),
        "mysql_shutdown_audit": shutdown_audit,
        "staged_physical_copy_reused": copy_reused,
        "container_creation_avoided": container_creation_avoided,
        "container_start_mode": (
            "restart_stopped_container_with_replaced_bind_sources"
            if container_creation_avoided else "fresh_docker_run"
        ),
        "reused_stopped_container_id": reusable_stopped_container_id,
        "recovery_after_docker_start_response_timeout": (
            recovery_after_docker_start_response_timeout
        ),
        "recovery_after_docker_run_response_timeout": False,
        "bootstrap_touched_staging_reused": bootstrap_touched_staging,
        "mysql_version": mysql_version,
        "database": "hybench_sf10x",
        "table_count": 8,
        "innodb_buffer_pool_bytes": int(buffer_pool),
        "innodb_use_native_aio": False,
        "innodb_flush_method": flush_method,
        "innodb_fast_shutdown": int(fast_shutdown),
        "container_init": True,
        "frozen_xp_innodb_io_profile": copy.deepcopy(io_profile),
        "docker_host_resources": {
            **expected_host,
            **swap_authority,
        },
        "kernel_cgroup_resources": kernel,
        "blkio_authority": blkio,
        "quarantined_predecessor_runtime_root": str(old_target),
        "quarantined_invalid_runtime_root": str(old_target),
        "reset_context": reset_context,
        "unresponsive_predecessor_reset": allow_unresponsive_old_container,
        "reset_after_invalid_r0": reset_context == "invalid_r0",
        "ready": True,
    }
    worker_output = output_root / worker_id
    worker_output.mkdir(parents=True, exist_ok=True)
    evidence = worker_output / "reset_worker.json"
    evidence.write_text(json.dumps(row, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    row["worker_evidence_path"] = str(evidence)
    row["worker_evidence_sha256"] = _sha(evidence)
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--copy-workers", type=int, default=2, choices=(1, 2))
    parser.add_argument("--resume-stalled-copy", action="store_true")
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,128}", args.run_id) is None:
        parser.error("unsafe run-id")
    freeze = _load(FREEZE)
    prepared = _load(PREPARED)
    invalid = _load(INVALID_R0)
    predecessor = freeze.get("predecessor") or {}
    predecessor_path = (ROOT / str(predecessor.get("path"))).resolve(strict=True)
    expected_prepared_freeze_sha = (
        _sha(FREEZE) if RESET_SAME_FREEZE else predecessor.get("sha256")
    )
    expected_invalid_freeze_sha = (
        _sha(FREEZE) if RESET_SAME_FREEZE else predecessor.get("sha256")
    )
    if (
        prepared.get("status") != "PREPARED"
        or predecessor.get("sha256") != _sha(predecessor_path)
        or prepared.get("freeze_sha256") != expected_prepared_freeze_sha
        or prepared.get("formal_outer_started") is not False
        or invalid.get("status") != "R0_FAILED_CLOSED"
        or invalid.get("freeze_sha256") != expected_invalid_freeze_sha
        or invalid.get("fresh_r0_complete") is not False
        or invalid.get("formal_outer_launch_authorized") is not False
        or any((invalid.get("accepted_rounds") or {}).values())
    ):
        raise RuntimeError(f"{AUTHORITY_VERSION} XP reset authority chain differs")
    previous = {row["worker_id"]: row for row in prepared["workers"]}
    frozen = {row["worker_id"]: row for row in freeze["workers"]}
    if set(previous) != set(frozen) or not XP_IDS.issubset(previous):
        raise RuntimeError("XP reset worker set differs")
    output_root = ROOT / "history/mysql_four_scene_20260809" / args.run_id
    if output_root.exists() and not args.resume_stalled_copy:
        raise RuntimeError(f"fresh XP reset output exists: {output_root}")
    if output_root.exists() and (output_root / "prepared_binding.json").is_file():
        binding = output_root / "prepared_binding.json"
        checksum = output_root / "prepared_binding.json.sha256"
        stalled = _load(binding)
        allowed_existing = {
            binding.name, checksum.name,
            "stalled_image_tag_attempt.json",
            "stalled_image_tag_attempt.json.sha256",
            "stalled_readonly_mode_attempt.json",
            "stalled_readonly_mode_attempt.json.sha256",
        }
        if (
            not args.resume_stalled_copy
            or stalled.get("status") != "FAILED_CLOSED"
            or {row.get("worker_id") for row in stalled.get("failures") or []}
            != XP_IDS
            or not set(path.name for path in output_root.iterdir()).issubset(allowed_existing)
        ):
            raise RuntimeError(f"stalled-copy output is not resumable: {output_root}")
        errors = "\n".join(row.get("error", "") for row in stalled["failures"])
        if "reset bootstrap exited" in errors:
            archive_stem = "stalled_readonly_mode_attempt"
        elif "Command '['du', '-sb'" in errors:
            archive_stem = "stalled_permission_measure_attempt"
        else:
            archive_stem = "stalled_image_tag_attempt"
        archive = output_root / f"{archive_stem}.json"
        archive_checksum = output_root / f"{archive_stem}.json.sha256"
        if archive.exists() or archive_checksum.exists():
            raise RuntimeError(f"stalled evidence archive already exists: {archive}")
        os.replace(binding, archive)
        os.replace(checksum, archive_checksum)
    output_root.mkdir(parents=True, mode=0o700, exist_ok=args.resume_stalled_copy)
    quarantine_root = (
        Path(str(freeze["storage"]["runtime_root"])) / "quarantine" / args.run_id
    )
    results: dict[str, dict[str, Any]] = {}
    failures: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=args.copy_workers, thread_name_prefix="xp-v5-reset") as pool:
        futures = {
            pool.submit(
                _prepare_fresh_xp, freeze, frozen[worker_id], previous[worker_id],
                quarantine_root, output_root, args.resume_stalled_copy,
            ): worker_id
            for worker_id in sorted(XP_IDS)
        }
        for future in as_completed(futures):
            worker_id = futures[future]
            try:
                results[worker_id] = future.result()
                print(json.dumps({"worker_id": worker_id, "ready": True}), flush=True)
            except BaseException as error:
                failures.append({
                    "worker_id": worker_id,
                    "error": f"{type(error).__name__}: {error}",
                })
                print(json.dumps(failures[-1]), flush=True)
    workers = [results.get(worker_id, previous[worker_id]) for worker_id in previous]
    passed = not failures and set(results) == XP_IDS
    document = {
        "schema_version": OUTPUT_SCHEMA,
        "status": "PREPARED" if passed else "FAILED_CLOSED",
        "created_at": _now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "freeze_path": str(FREEZE),
        "freeze_sha256": _sha(FREEZE),
        "predecessor_prepared_path": str(PREPARED),
        "predecessor_prepared_sha256": _sha(PREPARED),
        "invalid_r0_path": str(INVALID_R0),
        "invalid_r0_sha256": _sha(INVALID_R0),
        "reset_source_path": str(Path(__file__).resolve()),
        "reset_source_sha256": _sha(Path(__file__).resolve()),
        "quarantine_root": str(quarantine_root),
        "workers": workers,
        "failures": failures,
        "workload_started": False,
        "fresh_r0_complete": False,
        "formal_outer_started": False,
        "accepted_rounds": {scene: 0 for scene in freeze["active_scenes"]},
    }
    binding = output_root / "prepared_binding.json"
    binding.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    binding.with_name(binding.name + ".sha256").write_text(
        f"{_sha(binding)}  {binding.name}\n", encoding="ascii"
    )
    if passed:
        purge_completed_discard_quarantine(
            quarantine_root=quarantine_root,
            reset_workers=results,
            binding_path=binding,
            cleanup_path=output_root / "quarantine_cleanup.json",
        )
    print(json.dumps({
        "status": document["status"], "ready": len(results),
        "failed": len(failures), "binding": str(binding),
    }), flush=True)
    return 0 if passed else 4


if __name__ == "__main__":
    raise SystemExit(main())
