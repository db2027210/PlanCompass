#!/usr/bin/env python3
"""Target-cgroup XP post-reset writeback settlement for async scenes.

The legacy gate samples the complete ``nvme2n1`` device.  That is correct for
an exclusive four-scene wave, but an asynchronous scene controller may share
the device with other, resource-isolated MySQL scenes.  This release keeps the
same numeric write threshold and bounded wait while charging writeback only to
the containers reset by the current scene.  Whole-device counters remain
diagnostic evidence and never decide the scoped gate.
"""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
import subprocess
import time
from typing import Any, Mapping

from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v32 as c32


alg = c32.runtime.alg
ORIGINAL_RESET_XP = c32.ORIGINAL_RESET_XP
BLKIO_CGROUP_ROOT = Path("/sys/fs/cgroup/blkio")
DEVICE_NAME = "nvme2n1"
DEVICE_DEV = Path(f"/sys/class/block/{DEVICE_NAME}/dev")
DEVICE_STAT = Path(f"/sys/class/block/{DEVICE_NAME}/stat")
RUNTIME_ROOT = Path("/disk2/resource_adaptive_mysql_algorithm_c_disk2_v4")
QUIET_REQUIRED_SECONDS = 15
QUIET_MAXIMUM_SECONDS = 120
MAX_WRITE_BYTES_PER_SECOND = 8 * 1024 * 1024
XP_DOCKER_RESET_CONTROL_LOCK = Path("/tmp/mysql_async_xp_docker_reset_v1.lock")


def _container_pid(container: str) -> int:
    inspected = alg.reset.prep._docker_json("inspect", container)
    if not isinstance(inspected, Mapping):
        raise RuntimeError(f"container inspect cardinality differs: {container}")
    state = inspected.get("State") or {}
    pid = int(state.get("Pid") or 0)
    if state.get("Running") is not True or pid <= 1:
        raise RuntimeError(f"reset container is not running: {container}")
    return pid


def _cgroup_blkio_dir(pid: int) -> Path:
    membership = Path(f"/proc/{pid}/cgroup").read_text(encoding="utf-8")
    rows = []
    for line in membership.splitlines():
        fields = line.split(":", 2)
        if len(fields) == 3 and "blkio" in fields[1].split(","):
            rows.append(fields[2])
    if len(rows) != 1:
        raise RuntimeError(f"blkio cgroup membership differs for pid {pid}")
    path = (BLKIO_CGROUP_ROOT / rows[0].lstrip("/")).resolve()
    required = (
        path / "blkio.throttle.io_service_bytes_recursive",
        path / "blkio.throttle.io_serviced_recursive",
    )
    if (
        BLKIO_CGROUP_ROOT.resolve() not in path.parents
        or not path.is_dir()
        or any(not item.is_file() for item in required)
    ):
        raise RuntimeError(f"container blkio cgroup counters absent for pid {pid}")
    return path


def _device_key() -> str:
    value = DEVICE_DEV.read_text(encoding="ascii").strip()
    major, separator, minor = value.partition(":")
    if separator != ":" or not major.isdigit() or not minor.isdigit():
        raise RuntimeError("disk2 device major:minor authority differs")
    return value


def _counter_value(path: Path, device_key: str) -> int:
    matching = [
        line for line in path.read_text(encoding="ascii").splitlines()
        if line.split()[:2] == [device_key, "Write"]
    ]
    if len(matching) != 1:
        raise RuntimeError(f"disk2 Write counter cardinality differs: {path}")
    fields = matching[0].split()
    if len(fields) != 3 or not fields[2].isdigit():
        raise RuntimeError(f"disk2 Write counter format differs: {path}")
    return int(fields[2])


def _io_totals(paths: Mapping[str, Path], device_key: str) -> tuple[int, int]:
    write_bytes = 0
    write_ios = 0
    for path in paths.values():
        write_bytes += _counter_value(
            path / "blkio.throttle.io_service_bytes_recursive", device_key,
        )
        write_ios += _counter_value(
            path / "blkio.throttle.io_serviced_recursive", device_key,
        )
    return write_bytes, write_ios


def _device_diagnostic() -> dict[str, int]:
    fields = DEVICE_STAT.read_text(encoding="ascii").split()
    if len(fields) < 11:
        raise RuntimeError("disk2 block-device stat is incomplete")
    return {
        "write_sectors": int(fields[6]),
        "in_flight_ios": int(fields[8]),
    }


def reset_xp(
    freeze: Mapping[str, Any], workers: dict[str, dict[str, Any]],
    output: Path, token: str, *, worker_ids: set[str] | None = None,
) -> None:
    selected = set(worker_ids or workers)
    if not selected or not selected.issubset(workers):
        raise RuntimeError("async XP reset worker scope differs")
    if len({str(workers[worker_id]["scene"]) for worker_id in selected}) != 1:
        raise RuntimeError("async XP reset must contain exactly one scene")

    original_reset_workers = alg.XP_RESET_MAX_WORKERS
    control_lock = XP_DOCKER_RESET_CONTROL_LOCK.open("a+")
    wait_started = time.monotonic()
    fcntl.flock(control_lock.fileno(), fcntl.LOCK_EX)
    waited_seconds = time.monotonic() - wait_started
    alg.XP_RESET_MAX_WORKERS = 1
    try:
        ORIGINAL_RESET_XP(
            freeze, workers, output, token, worker_ids=selected,
        )
    finally:
        alg.XP_RESET_MAX_WORKERS = original_reset_workers
        fcntl.flock(control_lock.fileno(), fcntl.LOCK_UN)
        control_lock.close()
    stage = output / "xp_resets" / token
    alg.atomic(stage / "async_docker_reset_control_lease.json", {
        "schema_version": "mysql_async_xp_docker_reset_control_lease_v1",
        "status": "PASS",
        "created_at": alg.now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "lock_path": str(XP_DOCKER_RESET_CONTROL_LOCK),
        "lock_mode": "exclusive_docker_control_critical_section_only",
        "holder_pid": os.getpid(),
        "selected_worker_ids": sorted(selected),
        "waited_seconds": waited_seconds,
        "worker_reset_parallelism": 1,
        "measurement_or_outer_round_barrier": False,
        "other_scene_measurement_may_continue": True,
    })
    runtime_root = Path(str(freeze["storage"]["runtime_root"])).resolve()
    if (
        runtime_root != RUNTIME_ROOT
        or not runtime_root.is_dir()
        or not DEVICE_DEV.is_file()
        or not DEVICE_STAT.is_file()
    ):
        raise RuntimeError("async post-reset storage authority differs")

    evidence_path = stage / "post_reset_storage_settle.json"
    if evidence_path.exists() or evidence_path.is_symlink():
        raise RuntimeError("fresh async post-reset settle evidence required")

    cgroup_paths = {
        worker_id: _cgroup_blkio_dir(
            _container_pid(str(workers[worker_id]["container"]))
        )
        for worker_id in sorted(selected)
    }
    device_key = _device_key()

    sync_started = time.monotonic()
    synced = subprocess.run(
        ["sync", "-f", str(runtime_root)], text=True, capture_output=True,
        check=False, timeout=300,
    )
    sync_seconds = time.monotonic() - sync_started
    if synced.returncode != 0:
        raise RuntimeError(
            "disk2 syncfs failed after asynchronous XP reset: "
            + synced.stderr[-500:]
        )

    quiet_started = time.monotonic()
    previous_bytes, previous_ios = _io_totals(cgroup_paths, device_key)
    consecutive = 0
    samples: list[dict[str, Any]] = []
    while time.monotonic() - quiet_started < QUIET_MAXIMUM_SECONDS:
        sample_started = time.monotonic()
        time.sleep(1.0)
        write_bytes, write_ios = _io_totals(cgroup_paths, device_key)
        elapsed = max(1e-9, time.monotonic() - sample_started)
        write_bps = max(0, write_bytes - previous_bytes) / elapsed
        write_iops = max(0, write_ios - previous_ios) / elapsed
        previous_bytes, previous_ios = write_bytes, write_ios
        passed = write_bps <= MAX_WRITE_BYTES_PER_SECOND
        consecutive = consecutive + 1 if passed else 0
        samples.append({
            "elapsed_seconds": time.monotonic() - quiet_started,
            "target_cgroup_write_bytes_per_second": write_bps,
            "target_cgroup_write_ios_per_second_diagnostic": write_iops,
            "whole_device_diagnostic": _device_diagnostic(),
            "sample_passed": passed,
            "consecutive_quiet_seconds": consecutive,
        })
        if consecutive >= QUIET_REQUIRED_SECONDS:
            break

    passed = consecutive >= QUIET_REQUIRED_SECONDS
    document = {
        "schema_version": "mysql_async_xp_target_cgroup_post_reset_settle_v1",
        "status": "PASS" if passed else "FAILED_CLOSED",
        "created_at": alg.now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "runtime_root": str(runtime_root),
        "block_device": DEVICE_NAME,
        "block_device_major_minor": device_key,
        "selected_worker_ids": sorted(selected),
        "target_cgroup_blkio_paths": {
            worker_id: str(path) for worker_id, path in cgroup_paths.items()
        },
        "same_campaign_other_scene_io_excluded_from_decision": True,
        "whole_device_counters_diagnostic_only": True,
        "syncfs_command": ["sync", "-f", str(runtime_root)],
        "syncfs_returncode": synced.returncode,
        "syncfs_seconds": sync_seconds,
        "quiet_required_seconds": QUIET_REQUIRED_SECONDS,
        "quiet_maximum_seconds": QUIET_MAXIMUM_SECONDS,
        "maximum_target_cgroup_write_bytes_per_second": (
            MAX_WRITE_BYTES_PER_SECOND
        ),
        "quiet_elapsed_seconds": time.monotonic() - quiet_started,
        "samples": samples,
        "workload_started_before_gate": False,
        "accepted_state_credit_mutated": False,
        "workload_or_resource_envelope_changed": False,
        "recalibration_performed": False,
    }
    alg.atomic(evidence_path, document)
    if not passed:
        raise RuntimeError(
            "target-cgroup post-reset writeback did not settle in 120s"
        )


__all__ = ["reset_xp"]
