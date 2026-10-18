#!/usr/bin/env python3
"""Inventory released external MySQL containers without blocking a new run.

The external baseline controller is the workload authority.  Once that
controller has exited, its still-running ``ra_mysql_ext_*`` server containers
are residual inventory; the rolling runtime gate remains responsible for
detecting actual CPU or I/O activity before every measurement wave.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from scripts import preflight_mysql_four_scene_v7 as predecessor
from scripts.preflight_mysql_four_scene_v7 import *  # noqa: F401,F403


EXTERNAL_PREFIX = "ra_mysql_ext_"
EXTERNAL_CONTROLLER_MARKERS = (
    "run_mysql_external_baseline_pipeline.sh",
    "run_mysql_four_scene_external_baselines.py",
    "run_mysql_four_scene_external_baselines.sh",
    "mysql_external_baselines_20260826",
)


def _external_controller_pids() -> list[int]:
    result: list[int] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            command = (
                (entry / "cmdline").read_bytes().replace(b"\0", b" ")
                .decode("utf-8", errors="replace").strip()
            )
        except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
            continue
        if command and any(marker in command for marker in EXTERNAL_CONTROLLER_MARKERS):
            result.append(int(entry.name))
    return sorted(result)


def _docker_inventory(
    target_cpus: set[int], intended_names: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    inventory, blockers = predecessor._docker_inventory(target_cpus, intended_names)
    controller_pids = _external_controller_pids()
    if controller_pids:
        return inventory, blockers

    residual_names = {
        str(row.get("name") or "")
        for row in blockers
        if str(row.get("name") or "").startswith(EXTERNAL_PREFIX)
    }
    retained = [
        row for row in blockers
        if str(row.get("name") or "") not in residual_names
    ]
    for row in inventory:
        if str(row.get("name") or "") in residual_names:
            row.update({
                "released_external_mysql_inventory_only": True,
                "external_controller_pids": [],
                "runtime_rolling_noninterference_gate_required": True,
                "external_container_mutation_performed": False,
            })
    return inventory, retained


__all__ = [
    "_compress", "_cpus", "_database_workload_processes",
    "_docker_inventory", "_process_inventory",
]
