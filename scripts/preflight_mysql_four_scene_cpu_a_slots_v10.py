#!/usr/bin/env python3
"""Admit the frozen, CPU-disjoint predecessor I/O-A cohort during C.

The full-parallel successor prohibits a shared-NVMe completion barrier.  An
older worker is admitted only when its live CPU/NUMA placement and disk2 bind
root still exactly match the frozen v19 resource row and its CPU set is
disjoint from the new CPU-A-slot C cohort.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import time
from typing import Any

from scripts import preflight_mysql_four_scene_v9 as predecessor
from scripts.preflight_mysql_four_scene_v9 import *  # noqa: F401,F403


ROOT = Path(__file__).resolve().parents[1]
CO_RESIDENT_FREEZE = (
    ROOT / "config/formal/frozen_mysql_four_scene_two_worker_20260826_v19"
    / "four_scene_effective_resource_successor.json"
)


def _profiles() -> dict[str, dict[str, Any]]:
    value = json.loads(CO_RESIDENT_FREEZE.read_text(encoding="utf-8"))
    root = Path(str((value.get("storage") or {}).get("runtime_root") or ""))
    profiles: dict[str, dict[str, Any]] = {}
    for raw in value.get("workers") or []:
        row = dict(raw)
        row["runtime_root"] = str(row.get("runtime_root") or root / str(row["worker_id"]))
        profiles[str(row["container"])] = row
    return profiles


def _retry_inspect(name: str, maximum_seconds: float = 300.0) -> dict[str, Any] | None:
    deadline = time.monotonic() + maximum_seconds
    while time.monotonic() < deadline:
        try:
            result = subprocess.run(
                ["docker", "inspect", name], text=True, capture_output=True,
                check=False, timeout=60,
            )
        except subprocess.TimeoutExpired:
            time.sleep(2)
            continue
        if result.returncode != 0:
            return None
        values = json.loads(result.stdout)
        if not isinstance(values, list) or len(values) != 1:
            return None
        return dict(values[0])
    return None


def _docker_inventory(
    target_cpus: set[int], intended_names: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    profiles = _profiles()
    co_resident = set(profiles) - set(intended_names)
    inventory_deadline = time.monotonic() + 300.0
    while True:
        try:
            inventory, blockers = predecessor._docker_inventory(
                target_cpus, set(intended_names) | co_resident,
            )
            break
        except TimeoutError:
            if time.monotonic() >= inventory_deadline:
                raise
            time.sleep(2)
    retained = [row for row in blockers if str(row.get("name") or "") not in co_resident]
    seen: set[str] = set()
    for row in inventory:
        name = str(row.get("name") or "")
        if name not in co_resident:
            continue
        seen.add(name)
        profile = profiles[name]
        expected_root = str(profile.get("runtime_root") or "").rstrip("/") + "/"
        if row.get("inspect_status") != "PASS":
            document = _retry_inspect(name)
            if document is not None:
                host = dict(document.get("HostConfig") or {})
                mounts = list(document.get("Mounts") or [])
                row.update({
                    "pid": (document.get("State") or {}).get("Pid"),
                    "cpuset_cpus": str(host.get("CpusetCpus") or ""),
                    "cpuset_mems": str(host.get("CpusetMems") or ""),
                    "disk2_bind_sources": sorted(
                        str(mount.get("Source")) for mount in mounts
                        if str(mount.get("Source") or "").startswith("/disk2/")
                    ),
                    "inspect_status": "PASS_AFTER_BOUNDED_DOCKER_INSPECT_RETRY",
                    "docker_inspect_retry_maximum_seconds": 300.0,
                })
        disk2_sources = [str(value) for value in row.get("disk2_bind_sources") or []]
        passed = bool(
            str(row.get("inspect_status") or "").startswith("PASS")
            and row.get("database_like") is True
            and str(row.get("cpuset_cpus") or "") == str(profile.get("database_cpuset") or "")
            and str(row.get("cpuset_mems") or "") == str(profile.get("database_cpuset_mems") or "")
            and not (target_cpus & predecessor._cpus(str(row.get("cpuset_cpus") or "")))
            and disk2_sources
            and all(value.startswith(expected_root) for value in disk2_sources)
        )
        if passed:
            row.update({
                "allowed_frozen_cpu_disjoint_co_resident_mysql": True,
                "co_resident_resource_freeze": str(CO_RESIDENT_FREEZE),
                "shared_nvme_completion_dependency": False,
                "external_container_mutation_performed": False,
            })
        else:
            retained.append({
                **row,
                "reason": "co_resident_mysql_frozen_cpu_numa_storage_readback_differs",
                "expected_database_cpuset": profile.get("database_cpuset"),
                "expected_database_cpuset_mems": profile.get("database_cpuset_mems"),
                "expected_runtime_root": profile.get("runtime_root"),
            })
    # A stopped predecessor lane is harmless and is not required to exist.
    return inventory, retained


__all__ = [
    "_compress", "_cpus", "_database_workload_processes",
    "_docker_inventory", "_process_inventory",
]
