#!/usr/bin/env python3
"""Retry the complete Docker inventory on transient per-container inspect errors.

No isolation error is waived.  A snapshot is accepted only when the unchanged
v9 evaluator returns without an inspect-timeout blocker; all other blockers
are returned immediately.
"""

from __future__ import annotations

import time
from typing import Any

from scripts import preflight_mysql_four_scene_v9 as predecessor
from scripts.preflight_mysql_four_scene_v9 import *  # noqa: F401,F403


TRANSIENT_REASON = "database_or_intended_container_inspect_failed_closed"
MAXIMUM_WAIT_SECONDS = 180.0


def _docker_inventory(
    target_cpus: set[int], intended_names: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    started = time.monotonic()
    attempts = 0
    transient_errors: list[dict[str, Any]] = []
    while True:
        attempts += 1
        inventory, blockers = predecessor._docker_inventory(
            target_cpus, intended_names,
        )
        transient = [
            row for row in blockers
            if row.get("reason") == TRANSIENT_REASON
            and row.get("inspect_status")
            == "TIMEOUT_OR_TRANSIENT_ERROR_SUMMARY_RECORDED"
        ]
        nontransient = [row for row in blockers if row not in transient]
        if nontransient or not transient:
            evidence = {
                "attempts": attempts,
                "wait_seconds": round(time.monotonic() - started, 6),
                "maximum_wait_seconds": MAXIMUM_WAIT_SECONDS,
                "transient_inspect_retries": len(transient_errors),
                "authority_or_isolation_checks_changed": False,
                "blocker_waiver_performed": False,
            }
            for row in inventory:
                row["docker_inspect_retry_successor"] = evidence
            return inventory, blockers
        transient_errors.extend(transient)
        if time.monotonic() - started >= MAXIMUM_WAIT_SECONDS:
            for row in inventory:
                row["docker_inspect_retry_successor"] = {
                    "attempts": attempts,
                    "wait_seconds": round(time.monotonic() - started, 6),
                    "maximum_wait_seconds": MAXIMUM_WAIT_SECONDS,
                    "transient_inspect_retries": len(transient_errors),
                    "authority_or_isolation_checks_changed": False,
                    "blocker_waiver_performed": False,
                    "status": "EXHAUSTED_FAIL_CLOSED",
                }
            return inventory, blockers
        time.sleep(2.0)


__all__ = [
    "_compress", "_cpus", "_database_workload_processes",
    "_docker_inventory", "_process_inventory",
]
