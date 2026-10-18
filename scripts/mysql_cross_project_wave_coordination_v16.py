#!/usr/bin/env python3
"""Rolling gate that probes only containers present in the running inventory."""

from __future__ import annotations

import subprocess
from typing import Any

from scripts import mysql_cross_project_wave_coordination_v15 as predecessor


def _running_names() -> frozenset[str]:
    result = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return frozenset(value.strip() for value in result.stdout.splitlines() if value.strip())


def rolling_quiet_gate(**kwargs: Any) -> dict[str, Any]:
    # Presence is about actual co-resident work.  An exited container is not a
    # running database process and must not create a completion dependency.
    names = _running_names()
    original_present = predecessor._present
    predecessor._present = lambda name: name in names
    try:
        value = predecessor.rolling_quiet_gate(**kwargs)
    finally:
        predecessor._present = original_present
    value["schema_version"] = "mysql_cross_project_wave_gate_v16"
    value["release_version"] = "v72"
    value["container_presence_source"] = {
        "command": ["docker", "ps", "--format", "{{.Names}}"],
        "running_names": sorted(names),
        "exited_or_absent_is_not_active_interference": True,
        "present_running_containers_remain_fail_closed": True,
        "external_process_control_or_stop_performed": False,
    }
    return value


for _name in dir(predecessor):
    if not _name.startswith("__") and _name != "rolling_quiet_gate":
        globals().setdefault(_name, getattr(predecessor, _name))
