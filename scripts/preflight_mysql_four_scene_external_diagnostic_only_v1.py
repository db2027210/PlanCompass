#!/usr/bin/env python3
"""Keep external process inventory, but never gate MySQL on external rows."""

from __future__ import annotations

from typing import Any

from scripts import preflight_mysql_four_scene_v3_pg_wrapper_v2 as predecessor


def _process_inventory(target_cpus: set[int]) -> tuple[Any, list[Any]]:
    inventory, blockers = predecessor._process_inventory(target_cpus)
    if isinstance(inventory, dict):
        inventory = dict(inventory)
        inventory["external_rows_diagnostic_only"] = True
        inventory["observed_external_blocker_count"] = len(blockers)
    return inventory, []


def _docker_inventory(
    target_cpus: set[int], intended_names: set[str],
) -> tuple[Any, list[Any]]:
    # Container read-back remains authoritative.  Only host processes were
    # explicitly made diagnostic-only; own MySQL container mismatches still
    # fail closed here.
    return predecessor._docker_inventory(target_cpus, intended_names)


def __getattr__(name: str) -> Any:
    return getattr(predecessor, name)
