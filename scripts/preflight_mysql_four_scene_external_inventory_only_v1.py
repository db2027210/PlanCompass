#!/usr/bin/env python3
"""Inventory external processes and containers without making them blockers.

This policy is intentionally scoped to the user-directed MySQL-only run.  The
target MySQL workers are still validated by the runtime cgroup readback gate;
rows outside those workers remain visible here for provenance only.
"""

from __future__ import annotations

import copy
from typing import Any

from scripts import preflight_mysql_four_scene_v8 as predecessor
from scripts.preflight_mysql_four_scene_v8 import *  # noqa: F401,F403


RELEASE = "v245-external-process-inventory-only"


def _mark(rows: list[dict[str, Any]], source: str) -> list[dict[str, Any]]:
    result = copy.deepcopy(rows)
    for row in result:
        row.update({
            "external_inventory_policy_release": RELEASE,
            "external_inventory_source": source,
            "external_process_or_container_nonblocking": True,
            "external_mutation_performed": False,
        })
    return result


def _process_inventory(
    target_cpus: set[int],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    inventory, blockers = predecessor._process_inventory(target_cpus)
    marked = _mark(list(inventory), "process")
    blocker_pids = {
        int(row["pid"]) for row in blockers if row.get("pid") is not None
    }
    for row in marked:
        if row.get("pid") is not None and int(row["pid"]) in blocker_pids:
            row["would_block_under_strict_external_noninterference"] = True
    return marked, []


def _docker_inventory(
    target_cpus: set[int], intended_names: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    inventory, blockers = predecessor._docker_inventory(target_cpus, intended_names)
    marked = _mark(list(inventory), "container")
    blocker_names = {str(row.get("name") or "") for row in blockers}
    for row in marked:
        if str(row.get("name") or "") in blocker_names:
            row["would_block_under_strict_external_noninterference"] = True
    return marked, []


__all__ = [
    "_compress", "_cpus", "_database_workload_processes",
    "_docker_inventory", "_process_inventory",
]
