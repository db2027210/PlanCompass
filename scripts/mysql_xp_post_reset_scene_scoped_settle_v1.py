#!/usr/bin/env python3
"""Scene-local XP reset completion for full-parallel disk2 execution."""

from __future__ import annotations

import copy
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any, Mapping

from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v32 as predecessor


ROOT = Path(__file__).resolve().parents[1]
alg = predecessor.runtime.alg
ORIGINAL_RESET_XP = predecessor.ORIGINAL_RESET_XP
RUNTIME_ROOT = Path("/disk2/resource_adaptive_mysql_algorithm_c_disk2_v4")
DEVICE_STAT = Path("/sys/class/block/nvme2n1/stat")
REUSABLE_TOKEN = "algorithm_b_calibration_05"
REUSABLE_RESET = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v229_to_b_top4_fresh_20260826_attempt018_xp_cpu"
    / "fresh_calibration/xp_resets/algorithm_b_calibration_05/reset_binding.json"
)


def _device_sample() -> dict[str, Any]:
    fields = DEVICE_STAT.read_text(encoding="ascii").split()
    if len(fields) < 11:
        raise RuntimeError("disk2 diagnostic stat is incomplete")
    return {
        "captured_at": alg.now(),
        "write_sectors": int(fields[6]),
        "in_flight_ios": int(fields[8]),
    }


def _sync_and_record(stage: Path, *, reset_reused: bool) -> None:
    evidence = stage / "post_reset_storage_settle.json"
    if evidence.exists() or evidence.is_symlink():
        raise RuntimeError("fresh scene-scoped post-reset evidence required")
    started = time.monotonic()
    synced = subprocess.run(
        ["sync", "-f", str(RUNTIME_ROOT)], capture_output=True, text=True,
        check=False, timeout=300,
    )
    if synced.returncode != 0:
        raise RuntimeError("disk2 syncfs failed after scene reset: " + synced.stderr[-500:])
    document = {
        "schema_version": "mysql_xp_scene_scoped_post_reset_settle_v1",
        "status": "PASS",
        "created_at": alg.now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "runtime_root": str(RUNTIME_ROOT),
        "syncfs_returncode": synced.returncode,
        "syncfs_seconds": time.monotonic() - started,
        "reset_binding_reused": reset_reused,
        "device_activity_after_sync_diagnostic_only": _device_sample(),
        "whole_device_quiet_required": False,
        "reason": "full_parallel_successor_prohibits_cross_scene_completion_dependency",
        "sibling_scene_io_recorded_but_nonblocking": True,
        "workload_started_before_syncfs": False,
        "accepted_state_credit_mutated": False,
        "workload_or_resource_envelope_changed": False,
    }
    alg.atomic(evidence, document)


def _reuse_completed_reset(
    workers: dict[str, dict[str, Any]], stage: Path,
    worker_ids: set[str] | None,
) -> None:
    source = alg.load(REUSABLE_RESET)
    expected = set(worker_ids or ())
    rows = {
        str(row["worker_id"]): copy.deepcopy(dict(row))
        for row in source.get("workers") or [] if str(row.get("worker_id")) in expected
    }
    if (
        source.get("status") != "PASS"
        or not expected
        or set(rows) != expected
        or any(worker_id not in workers for worker_id in expected)
    ):
        raise RuntimeError("completed calibration-05 reset authority differs")
    for worker_id, row in rows.items():
        current = workers[worker_id]
        if (
            current.get("container_id") != row.get("container_id")
            or current.get("container") != row.get("container")
            or current.get("credential_defaults") != row.get("credential_defaults")
        ):
            raise RuntimeError(f"{worker_id} reusable reset identity differs")
        inspected = alg.reset._docker_json("inspect", str(row["container"]))
        if (
            inspected.get("Id") != row.get("container_id")
            or (inspected.get("State") or {}).get("Running") is not True
        ):
            raise RuntimeError(f"{worker_id} reusable reset is not live")
        alg.reset.prep._kernel_resource_readback(inspected, row)
        workers[worker_id] = {**current, **row}
    stage.mkdir(parents=True, mode=0o700)
    destination = stage / "reset_binding.json"
    shutil.copy2(REUSABLE_RESET, destination)
    shutil.copy2(
        REUSABLE_RESET.with_name(REUSABLE_RESET.name + ".sha256"),
        destination.with_name(destination.name + ".sha256"),
    )
    alg.atomic(stage / "technical_reset_reuse.json", {
        "schema_version": "mysql_b_calibration_reset_technical_reuse_v1",
        "status": "PASS",
        "created_at": alg.now(),
        "source_path": str(REUSABLE_RESET),
        "source_sha256": alg.sha(REUSABLE_RESET),
        "worker_ids": sorted(expected),
        "candidate_measurement_reused": False,
        "accepted_or_rejected_state_credit_increment": 0,
    })


def reset_xp(
    freeze: Mapping[str, Any], workers: dict[str, dict[str, Any]],
    output: Path, token: str, *, worker_ids: set[str] | None = None,
) -> None:
    runtime_root = Path(str(freeze["storage"]["runtime_root"])).resolve()
    if runtime_root != RUNTIME_ROOT or not RUNTIME_ROOT.is_dir() or not DEVICE_STAT.is_file():
        raise RuntimeError("scene-scoped post-reset authority differs")
    stage = output / "xp_resets" / token
    reused = token == REUSABLE_TOKEN and not stage.exists()
    if reused:
        _reuse_completed_reset(workers, stage, worker_ids)
    else:
        ORIGINAL_RESET_XP(freeze, workers, output, token, worker_ids=worker_ids)
    _sync_and_record(stage, reset_reused=reused)
