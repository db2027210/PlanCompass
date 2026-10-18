#!/usr/bin/env python3
"""Inventory-only waiver for a CPU-disjoint external PostgreSQL container."""

from __future__ import annotations

from typing import Any

from scripts import preflight_mysql_four_scene_v9 as predecessor


WAIVED_CONTAINER = "gptuner_cost7_hybench"
WAIVER_ID = "user_authorized_cpu_disjoint_external_storage_inventory_only_v1"


def _process_inventory(target_cpus: set[int]):
    return predecessor._process_inventory(target_cpus)


def _docker_inventory(
    target_cpus: set[int], intended_names: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    inventory, blockers = predecessor._docker_inventory(target_cpus, intended_names)
    waived_ids: set[str] = set()
    retained: list[dict[str, Any]] = []
    for row in blockers:
        if (
            str(row.get("name") or "") == WAIVED_CONTAINER
            and str(row.get("overlap_cpus") or "") == ""
            and row.get("database_like") is True
            and row.get("intended_successor_container") is False
            and str(row.get("reason") or "")
            == "co_resident_database_uses_target_disk2"
        ):
            waived_ids.add(str(row.get("container_id") or ""))
            continue
        retained.append(row)
    for row in inventory:
        if str(row.get("container_id") or "") in waived_ids:
            row["noninterference_disposition"] = "inventory_only_user_authorized"
            row["noninterference_waiver_id"] = WAIVER_ID
            row["external_process_control_or_stop_performed"] = False
    return inventory, retained


__all__ = ["_process_inventory", "_docker_inventory", "WAIVER_ID"]
