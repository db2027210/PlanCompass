#!/usr/bin/env python3
"""Bounded retry for transient Docker container-list congestion."""

from __future__ import annotations

import time
from typing import Any

from scripts import preflight_mysql_four_scene_v8 as predecessor
from scripts.preflight_mysql_four_scene_v8 import *  # noqa: F401,F403


def _docker_inventory(
    target_cpus: set[int], intended_names: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    started = time.monotonic()
    deadline = started + 120.0
    errors: list[str] = []
    attempts = 0
    while True:
        attempts += 1
        try:
            inventory, blockers = predecessor._docker_inventory(
                target_cpus, intended_names,
            )
            for row in inventory:
                row["docker_list_retry_successor"] = {
                    "attempts": attempts,
                    "wait_seconds": round(time.monotonic() - started, 6),
                    "maximum_wait_seconds": 120.0,
                    "authority_or_isolation_checks_changed": False,
                }
            return inventory, blockers
        except TimeoutError as error:
            errors.append(f"{type(error).__name__}: {error}")
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    "Docker API container list remained unavailable for "
                    f"120 seconds across {attempts} bounded retries: "
                    + errors[-1]
                ) from error
            time.sleep(2.0)


__all__ = [
    "_compress", "_cpus", "_database_workload_processes",
    "_docker_inventory", "_process_inventory",
]
