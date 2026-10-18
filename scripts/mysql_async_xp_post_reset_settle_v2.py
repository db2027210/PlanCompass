#!/usr/bin/env python3
"""Parallel, scene-scoped XP reset followed by the frozen settle gate.

The v1 adapter serialized every Docker reset even though all formal XP
workers have disjoint CPU placement and independent datadirs.  This successor
keeps the same reset implementation, syncfs barrier, target-cgroup writeback
threshold, and 15-second quiet gate, but resets every worker assigned to the
current scene wave concurrently.  It does not introduce a cross-scene lock.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import time
from typing import Any, Mapping

from scripts import mysql_async_xp_post_reset_settle_v1 as v1


alg = v1.alg
ORIGINAL_RESET_XP = v1.ORIGINAL_RESET_XP


def reset_xp(
    freeze: Mapping[str, Any], workers: dict[str, dict[str, Any]],
    output: Path, token: str, *, worker_ids: set[str] | None = None,
) -> None:
    selected = set(worker_ids or workers)
    if not selected or not selected.issubset(workers):
        raise RuntimeError("parallel async XP reset worker scope differs")
    if len({str(workers[worker_id]["scene"]) for worker_id in selected}) != 1:
        raise RuntimeError("parallel async XP reset must contain exactly one scene")

    original_reset_workers = alg.XP_RESET_MAX_WORKERS
    reset_started = time.monotonic()
    alg.XP_RESET_MAX_WORKERS = len(selected)
    try:
        ORIGINAL_RESET_XP(
            freeze, workers, output, token, worker_ids=selected,
        )
    finally:
        alg.XP_RESET_MAX_WORKERS = original_reset_workers

    stage = output / "xp_resets" / token
    alg.atomic(stage / "async_docker_reset_parallelism.json", {
        "schema_version": "mysql_async_xp_docker_reset_parallelism_v2",
        "status": "PASS",
        "created_at": alg.now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "selected_worker_ids": sorted(selected),
        "worker_reset_parallelism": len(selected),
        "reset_elapsed_seconds": time.monotonic() - reset_started,
        "cross_scene_lock_used": False,
        "shared_nvme_lease_used": False,
        "measurement_or_outer_round_barrier": False,
        "other_scene_measurement_may_continue": True,
    })

    runtime_root = Path(str(freeze["storage"]["runtime_root"])).resolve()
    if (
        runtime_root != v1.RUNTIME_ROOT
        or not runtime_root.is_dir()
        or not v1.DEVICE_DEV.is_file()
        or not v1.DEVICE_STAT.is_file()
    ):
        raise RuntimeError("parallel async post-reset storage authority differs")

    evidence_path = stage / "post_reset_storage_settle.json"
    if evidence_path.exists() or evidence_path.is_symlink():
        raise RuntimeError("fresh parallel async post-reset settle evidence required")

    cgroup_paths = {
        worker_id: v1._cgroup_blkio_dir(
            v1._container_pid(str(workers[worker_id]["container"]))
        )
        for worker_id in sorted(selected)
    }
    device_key = v1._device_key()

    sync_started = time.monotonic()
    synced = subprocess.run(
        ["sync", "-f", str(runtime_root)], text=True, capture_output=True,
        check=False, timeout=300,
    )
    sync_seconds = time.monotonic() - sync_started
    if synced.returncode != 0:
        raise RuntimeError(
            "disk2 syncfs failed after parallel asynchronous XP reset: "
            + synced.stderr[-500:]
        )

    quiet_started = time.monotonic()
    previous_bytes, previous_ios = v1._io_totals(cgroup_paths, device_key)
    consecutive = 0
    samples: list[dict[str, Any]] = []
    while time.monotonic() - quiet_started < v1.QUIET_MAXIMUM_SECONDS:
        sample_started = time.monotonic()
        time.sleep(1.0)
        write_bytes, write_ios = v1._io_totals(cgroup_paths, device_key)
        elapsed = max(1e-9, time.monotonic() - sample_started)
        write_bps = max(0, write_bytes - previous_bytes) / elapsed
        write_iops = max(0, write_ios - previous_ios) / elapsed
        previous_bytes, previous_ios = write_bytes, write_ios
        passed = write_bps <= v1.MAX_WRITE_BYTES_PER_SECOND
        consecutive = consecutive + 1 if passed else 0
        samples.append({
            "elapsed_seconds": time.monotonic() - quiet_started,
            "target_cgroup_write_bytes_per_second": write_bps,
            "target_cgroup_write_ios_per_second_diagnostic": write_iops,
            "whole_device_diagnostic": v1._device_diagnostic(),
            "sample_passed": passed,
            "consecutive_quiet_seconds": consecutive,
        })
        if consecutive >= v1.QUIET_REQUIRED_SECONDS:
            break

    passed = consecutive >= v1.QUIET_REQUIRED_SECONDS
    document = {
        "schema_version": "mysql_async_xp_target_cgroup_post_reset_settle_v2",
        "status": "PASS" if passed else "FAILED_CLOSED",
        "created_at": alg.now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "runtime_root": str(runtime_root),
        "block_device": v1.DEVICE_NAME,
        "block_device_major_minor": device_key,
        "selected_worker_ids": sorted(selected),
        "target_cgroup_blkio_paths": {
            worker_id: str(path) for worker_id, path in cgroup_paths.items()
        },
        "worker_reset_parallelism": len(selected),
        "cross_scene_lock_used": False,
        "shared_nvme_lease_used": False,
        "same_campaign_other_scene_io_excluded_from_decision": True,
        "whole_device_counters_diagnostic_only": True,
        "syncfs_command": ["sync", "-f", str(runtime_root)],
        "syncfs_returncode": synced.returncode,
        "syncfs_seconds": sync_seconds,
        "quiet_required_seconds": v1.QUIET_REQUIRED_SECONDS,
        "quiet_maximum_seconds": v1.QUIET_MAXIMUM_SECONDS,
        "maximum_target_cgroup_write_bytes_per_second": (
            v1.MAX_WRITE_BYTES_PER_SECOND
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
