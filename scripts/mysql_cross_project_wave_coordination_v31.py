#!/usr/bin/env python3
"""Classify exact target-journal D state and allow a full clean-window wait."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from scripts import mysql_cross_project_wave_coordination_v27 as v27
from scripts import mysql_cross_project_wave_coordination_v28 as v28


RELEASE = "v210-target-journal-and-clean-window"
LOCK_PATH = v27.LOCK_PATH
CONTRACT_PATH = v27.CONTRACT_PATH
target_local_resource_proof = v27.target_local_resource_proof
MAXIMUM_CLEAN_WINDOW_WAIT_SECONDS = 120.0
_KERNEL_EXCLUSIONS: list[dict[str, Any]] = []
_ORIGINAL_V28_INVENTORY = v28._inventory
_ORIGINAL_V28_GATE = v28.rolling_quiet_gate


def _exact_target_journal(row: dict[str, Any]) -> bool:
    return bool(
        row.get("comm") == "jbd2/nvme2n1-8"
        and int(row.get("ppid") or 0) == 2
        and row.get("state") == "D"
        and row.get("command_class") == "other"
        and row.get("frozen_mysql_worker") is None
        and row.get("same_gate_process_tree") is False
        and row.get("target_device_mounted") is True
        and row.get("target_device_open") is False
    )


def _inventory(
    target_cpus: set[int], target_worker_ids: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], set[int]]:
    workloads, rows, own_tree = _ORIGINAL_V28_INVENTORY(
        target_cpus, target_worker_ids
    )
    retained: list[dict[str, Any]] = []
    for raw in rows:
        if _exact_target_journal(raw):
            row = copy.deepcopy(raw)
            row.update({
                "classification_release": RELEASE,
                "classification": "target_filesystem_kernel_journal_non_workload",
                "external_database_or_benchmark_process": False,
                "target_device_fd_open": False,
            })
            _KERNEL_EXCLUSIONS.append(row)
        else:
            retained.append(raw)
    return workloads, retained, own_tree


def rolling_quiet_gate(**kwargs: Any) -> dict[str, Any]:
    _KERNEL_EXCLUSIONS.clear()
    requested = float(kwargs["maximum_wait_seconds"])
    kwargs["maximum_wait_seconds"] = max(
        requested, MAXIMUM_CLEAN_WINDOW_WAIT_SECONDS,
    )
    result = _ORIGINAL_V28_GATE(**kwargs)
    result["target_journal_classification_release"] = {
        "release_version": RELEASE,
        "source_path": str(Path(__file__).resolve()),
        "source_sha256": v27._sha(Path(__file__).resolve()),
        "exact_comm": "jbd2/nvme2n1-8",
        "kernel_parent_pid_required": 2,
        "target_device_fd_open_prohibited": True,
        "database_or_benchmark_process_exempted": False,
        "unknown_user_process_exempted": False,
        "requested_maximum_wait_seconds": requested,
        "effective_maximum_wait_seconds": kwargs["maximum_wait_seconds"],
        "clean_window_seconds_unchanged": result["required_clean_seconds"],
        "sibling_scene_completion_checked": False,
        "cross_scene_completion_dependency": False,
    }
    result["excluded_target_journal_d_state_rows"] = copy.deepcopy(
        _KERNEL_EXCLUSIONS
    )
    return result


def install() -> None:
    v27._inventory = _inventory
    v27.rolling_quiet_gate = rolling_quiet_gate
