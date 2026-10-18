#!/usr/bin/env python3
"""Treat proven disk1/disk2 jbd2 threads as observation, not interference."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import re
import subprocess
from typing import Any

from scripts import mysql_cross_project_wave_coordination_v8 as predecessor
from scripts import preflight_mysql_four_scene_live_v8 as pressure_source


ROOT = Path(__file__).resolve().parents[1]
FREEZE = (
    ROOT / "config/formal/frozen_mysql_four_scene_two_worker_20260826_v19"
    / "four_scene_effective_resource_successor.json"
)


def _cpus(text: str) -> set[int]:
    values: set[int] = set()
    for token in text.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            lower, upper = (int(value) for value in token.split("-", 1))
            values.update(range(lower, upper + 1))
        else:
            values.add(int(token))
    return values


def _scene_cpu_unions() -> tuple[set[int], set[int]]:
    freeze = json.loads(FREEZE.read_text(encoding="utf-8"))
    xp: set[int] = set()
    relational: set[int] = set()
    for row in freeze["workers"]:
        union = _cpus(str(row["database_cpuset"])) | _cpus(str(row["client_cpuset"]))
        if str(row["scene"]).startswith("hybench_xp_"):
            xp.update(union)
        else:
            relational.update(union)
    return xp, relational


def _mount_source(mount: str) -> str:
    completed = subprocess.run(
        ["findmnt", "-n", "-o", "SOURCE", mount],
        text=True, capture_output=True, check=True, timeout=10,
    )
    return completed.stdout.strip()


def _journal_authority(target_cpus: set[int]) -> dict[str, Any]:
    xp, relational = _scene_cpu_unions()
    if target_cpus and target_cpus.issubset(xp):
        target_mount, sibling_mount = "/disk2", "/disk1"
    elif target_cpus and target_cpus.issubset(relational):
        target_mount, sibling_mount = "/disk1", "/disk2"
    else:
        raise RuntimeError("target CPUs do not identify one frozen storage cohort")
    target_source = _mount_source(target_mount)
    sibling_source = _mount_source(sibling_mount)
    target_name = Path(target_source).name
    sibling_name = Path(sibling_source).name
    target_device = Path(f"/sys/class/block/{target_name}/device").resolve()
    sibling_device = Path(f"/sys/class/block/{sibling_name}/device").resolve()
    proven = bool(
        target_source.startswith("/dev/nvme")
        and sibling_source.startswith("/dev/nvme")
        and target_source != sibling_source
        and target_device != sibling_device
        and target_device.exists()
        and sibling_device.exists()
    )
    return {
        "proven": proven,
        "target_mount": target_mount,
        "target_source": target_source,
        "target_device_path": str(target_device),
        "sibling_mount": sibling_mount,
        "sibling_source": sibling_source,
        "sibling_device_path": str(sibling_device),
        "target_journal_prefix": f"jbd2/{target_name}-",
        "sibling_journal_prefix": f"jbd2/{sibling_name}-",
    }


def rolling_quiet_gate(
    *, run_id: str, required_seconds: float, maximum_wait_seconds: float,
    maximum_load1: float = 16.0, interval_seconds: float = 1.0,
    target_cpus: set[int] | None = None,
) -> dict[str, Any]:
    scoped = set(target_cpus or ())
    authority = _journal_authority(scoped)
    if authority.get("proven") is not True:
        raise RuntimeError("disk1/disk2 physical-device disjointness is unproven")
    original_host_pressure = pressure_source.host_pressure

    def storage_scoped_host_pressure() -> dict[str, Any]:
        value = copy.deepcopy(original_host_pressure())
        for row in value.get("d_state_details") or []:
            name = str(row.get("name") or "")
            if re.fullmatch(re.escape(str(authority["target_journal_prefix"])) + r"\d+", name):
                row["classification"] = "observed_target_journal_thread_nonblocking"
            elif re.fullmatch(re.escape(str(authority["sibling_journal_prefix"])) + r"\d+", name):
                row["classification"] = "observed_disjoint_sibling_journal_nonblocking"
        details = list(value.get("d_state_details") or [])
        value["blocking_d_state_pids"] = [
            int(row["pid"]) for row in details
            if row.get("classification") == "blocking_unknown_or_target_relevant"
        ]
        value["observed_target_journal_d_state_pids"] = [
            int(row["pid"]) for row in details
            if row.get("classification") == "observed_target_journal_thread_nonblocking"
        ]
        value["exempted_unrelated_d_state_pids"] = sorted(set(
            int(value) for value in value.get("exempted_unrelated_d_state_pids") or []
        ) | {
            int(row["pid"]) for row in details
            if row.get("classification") == "observed_disjoint_sibling_journal_nonblocking"
        })
        value["storage_scoped_journal_authority"] = authority
        return value

    pressure_source.host_pressure = storage_scoped_host_pressure
    try:
        result = predecessor.rolling_quiet_gate(
            run_id=run_id,
            required_seconds=required_seconds,
            maximum_wait_seconds=maximum_wait_seconds,
            maximum_load1=maximum_load1,
            interval_seconds=interval_seconds,
            target_cpus=scoped,
        )
    finally:
        pressure_source.host_pressure = original_host_pressure
    result["release_version"] = "v64"
    result["storage_scoped_journal_release"] = {
        **authority,
        "kernel_journal_thread_alone_proves_external_interference": False,
        "user_process_cpu_storage_and_memory_isolation_checks_preserved": True,
        "persistent_unknown_d_state_remains_fail_closed": True,
        "external_process_control_or_stop_performed": False,
        "algorithm_candidate_or_acceptance_flow_changed": False,
    }
    return result

