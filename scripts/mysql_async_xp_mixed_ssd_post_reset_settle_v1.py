#!/usr/bin/env python3
"""Parallel mixed-SSD XP reset with per-cgroup, per-device settle accounting."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import time
from typing import Any, Mapping

from scripts import mysql_async_xp_post_reset_settle_v1 as predecessor


alg = predecessor.alg
ORIGINAL_RESET_XP = predecessor.ORIGINAL_RESET_XP
MOUNTS = {"disk1": Path("/disk1"), "disk2": Path("/disk2")}
DEVICES = {
    "disk1": Path("/sys/class/block/nvme1n1"),
    "disk2": Path("/sys/class/block/nvme2n1"),
}


def _device_key(device: Path) -> str:
    value = (device / "dev").read_text(encoding="ascii").strip()
    major, separator, minor = value.partition(":")
    if separator != ":" or not major.isdigit() or not minor.isdigit():
        raise RuntimeError(f"mixed-SSD device major:minor differs: {device}")
    return value


def _worker_disk(worker: Mapping[str, Any]) -> str:
    datadir = Path(str(worker.get("datadir") or "")).resolve()
    for name, mount in MOUNTS.items():
        if mount.resolve() in datadir.parents:
            return name
    raise RuntimeError(f"mixed-SSD worker datadir is outside disk1/disk2: {datadir}")


def _counter_value(path: Path, device_key: str) -> int:
    matching = [
        line for line in path.read_text(encoding="ascii").splitlines()
        if line.split()[:2] == [device_key, "Write"]
    ]
    # cgroup-v1 omits a device/operation row until its first charged I/O.  A
    # missing exact device row therefore means an authoritative zero, while
    # duplicate exact rows or malformed counters remain fail-closed.
    if not matching:
        return 0
    if len(matching) != 1:
        raise RuntimeError(f"mixed-SSD Write counter cardinality differs: {path}")
    fields = matching[0].split()
    if len(fields) != 3 or not fields[2].isdigit():
        raise RuntimeError(f"mixed-SSD Write counter format differs: {path}")
    return int(fields[2])


def _io_totals(
    paths: Mapping[str, Path], worker_disks: Mapping[str, str],
    device_keys: Mapping[str, str],
) -> tuple[int, int]:
    write_bytes = 0
    write_ios = 0
    for worker_id, path in paths.items():
        key = device_keys[worker_disks[worker_id]]
        write_bytes += _counter_value(
            path / "blkio.throttle.io_service_bytes_recursive", key
        )
        write_ios += _counter_value(
            path / "blkio.throttle.io_serviced_recursive", key
        )
    return write_bytes, write_ios


def _device_diagnostic(device: Path) -> dict[str, int]:
    fields = (device / "stat").read_text(encoding="ascii").split()
    if len(fields) < 11:
        raise RuntimeError(f"mixed-SSD block-device stat is incomplete: {device}")
    return {"write_sectors": int(fields[6]), "in_flight_ios": int(fields[8])}


def reset_xp(
    freeze: Mapping[str, Any], workers: dict[str, dict[str, Any]],
    output: Path, token: str, *, worker_ids: set[str] | None = None,
) -> None:
    selected = set(worker_ids or workers)
    if not selected or not selected.issubset(workers):
        raise RuntimeError("mixed-SSD async XP reset worker scope differs")
    if len({str(workers[worker_id]["scene"]) for worker_id in selected}) != 1:
        raise RuntimeError("mixed-SSD async XP reset crossed a scene boundary")

    original_reset_workers = alg.XP_RESET_MAX_WORKERS
    reset_started = time.monotonic()
    alg.XP_RESET_MAX_WORKERS = len(selected)
    try:
        ORIGINAL_RESET_XP(freeze, workers, output, token, worker_ids=selected)
    finally:
        alg.XP_RESET_MAX_WORKERS = original_reset_workers

    stage = output / "xp_resets" / token
    worker_disks = {
        worker_id: _worker_disk(workers[worker_id])
        for worker_id in sorted(selected)
    }
    if set(worker_disks.values()) != {"disk1", "disk2"}:
        raise RuntimeError("mixed-SSD reset must cover both frozen devices")
    device_keys = {name: _device_key(path) for name, path in DEVICES.items()}
    cgroup_paths = {
        worker_id: predecessor._cgroup_blkio_dir(
            predecessor._container_pid(str(workers[worker_id]["container"]))
        )
        for worker_id in sorted(selected)
    }

    sync_rows: list[dict[str, Any]] = []
    for name in sorted(set(worker_disks.values())):
        started = time.monotonic()
        synced = subprocess.run(
            ["sync", "-f", str(MOUNTS[name])], capture_output=True, text=True,
            check=False, timeout=300,
        )
        if synced.returncode != 0:
            raise RuntimeError(f"{name} syncfs failed: {synced.stderr[-500:]}")
        sync_rows.append({
            "disk": name, "mount": str(MOUNTS[name]),
            "returncode": synced.returncode,
            "elapsed_seconds": time.monotonic() - started,
        })

    quiet_started = time.monotonic()
    previous_bytes, previous_ios = _io_totals(
        cgroup_paths, worker_disks, device_keys
    )
    consecutive = 0
    samples: list[dict[str, Any]] = []
    while time.monotonic() - quiet_started < predecessor.QUIET_MAXIMUM_SECONDS:
        sample_started = time.monotonic()
        time.sleep(1.0)
        write_bytes, write_ios = _io_totals(
            cgroup_paths, worker_disks, device_keys
        )
        elapsed = max(1e-9, time.monotonic() - sample_started)
        write_bps = max(0, write_bytes - previous_bytes) / elapsed
        write_iops = max(0, write_ios - previous_ios) / elapsed
        previous_bytes, previous_ios = write_bytes, write_ios
        passed = write_bps <= predecessor.MAX_WRITE_BYTES_PER_SECOND
        consecutive = consecutive + 1 if passed else 0
        samples.append({
            "elapsed_seconds": time.monotonic() - quiet_started,
            "target_cgroup_write_bytes_per_second": write_bps,
            "target_cgroup_write_ios_per_second_diagnostic": write_iops,
            "whole_device_diagnostic": {
                name: _device_diagnostic(path) for name, path in DEVICES.items()
            },
            "sample_passed": passed,
            "consecutive_quiet_seconds": consecutive,
        })
        if consecutive >= predecessor.QUIET_REQUIRED_SECONDS:
            break

    passed = consecutive >= predecessor.QUIET_REQUIRED_SECONDS
    document = {
        "schema_version": "mysql_async_xp_mixed_ssd_post_reset_settle_v1",
        "status": "PASS" if passed else "FAILED_CLOSED",
        "created_at": alg.now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "selected_worker_ids": sorted(selected),
        "worker_reset_parallelism": len(selected),
        "reset_elapsed_seconds": time.monotonic() - reset_started,
        "worker_storage_devices": worker_disks,
        "device_major_minor": device_keys,
        "target_cgroup_blkio_paths": {
            worker_id: str(path) for worker_id, path in cgroup_paths.items()
        },
        "zero_before_first_charged_io_is_valid": True,
        "duplicate_exact_device_operation_rows_fail_closed": True,
        "cross_scene_lock_used": False,
        "shared_nvme_lease_used": False,
        "whole_round_or_scene_barrier": False,
        "syncfs": sync_rows,
        "quiet_required_seconds": predecessor.QUIET_REQUIRED_SECONDS,
        "quiet_maximum_seconds": predecessor.QUIET_MAXIMUM_SECONDS,
        "maximum_target_cgroup_write_bytes_per_second": (
            predecessor.MAX_WRITE_BYTES_PER_SECOND
        ),
        "quiet_elapsed_seconds": time.monotonic() - quiet_started,
        "samples": samples,
        "workload_started_before_gate": False,
        "accepted_state_credit_mutated": False,
        "workload_or_resource_envelope_changed": False,
        "recalibration_performed": False,
        "external_process_control_or_stop_performed": False,
        "controller_pid": os.getpid(),
    }
    alg.atomic(stage / "post_reset_storage_settle.json", document)
    if not passed:
        raise RuntimeError("mixed-SSD target-cgroup writeback did not settle in 120s")


__all__ = ["reset_xp"]
