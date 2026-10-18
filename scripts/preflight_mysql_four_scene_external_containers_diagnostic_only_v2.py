#!/usr/bin/env python3
"""Diagnose CPU-disjoint external containers without blocking a scene resume."""

from __future__ import annotations

from typing import Any

from scripts import preflight_mysql_four_scene_external_diagnostic_only_v1 as predecessor


def _process_inventory(target_cpus: set[int]) -> tuple[Any, list[Any]]:
    return predecessor._process_inventory(target_cpus)


def _docker_inventory(
    target_cpus: set[int], intended_names: set[str],
) -> tuple[Any, list[Any]]:
    inventory, blockers = predecessor._docker_inventory(target_cpus, intended_names)
    retained: list[Any] = []
    diagnostic: list[Any] = []
    for row in blockers:
        overlap = row.get("overlap_cpus") if isinstance(row, dict) else None
        name = str(row.get("name") or "") if isinstance(row, dict) else ""
        cpu_disjoint = overlap in (None, "", [], ())
        if cpu_disjoint and name not in intended_names:
            diagnostic.append(row)
        else:
            retained.append(row)
    if isinstance(inventory, dict):
        inventory = dict(inventory)
        inventory["external_cpu_disjoint_containers_diagnostic_only"] = diagnostic
        inventory["external_cpu_disjoint_container_count"] = len(diagnostic)
        inventory["retained_blocker_count"] = len(retained)
    return inventory, retained


def __getattr__(name: str) -> Any:
    return getattr(predecessor, name)
