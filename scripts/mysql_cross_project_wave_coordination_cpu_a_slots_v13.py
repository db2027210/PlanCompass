#!/usr/bin/env python3
"""Rolling interference gate for four-worker XP cohorts on disk1+disk2."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import Any

from scripts import mysql_cross_project_wave_coordination_v9 as journal_gate
from scripts import mysql_cross_project_wave_coordination_v11 as predecessor


ROOT = Path(__file__).resolve().parents[1]
FREEZE = (
    ROOT / "config/formal/frozen_mysql_four_scene_cpu_a_slots_four_worker_20260828_v3"
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


def _scene_cpu_unions() -> dict[str, set[int]]:
    value = json.loads(FREEZE.read_text(encoding="utf-8"))
    result: dict[str, set[int]] = {}
    for row in value.get("workers") or []:
        scene = str(row["scene"])
        result.setdefault(scene, set()).update(
            _cpus(str(row["database_cpuset"]))
            | _cpus(str(row["client_cpuset"]))
        )
    return result


def _source(mount: str) -> str:
    return subprocess.run(
        ["findmnt", "-n", "-o", "SOURCE", mount], text=True,
        capture_output=True, check=True, timeout=10,
    ).stdout.strip()


def _mixed_journal_authority(target_cpus: set[int]) -> dict[str, Any]:
    matches = [
        scene for scene, cpus in _scene_cpu_unions().items()
        if target_cpus and target_cpus.issubset(cpus)
    ]
    if len(matches) != 1:
        raise RuntimeError(
            "target CPUs do not identify one four-worker CPU-A-slot scene cohort"
        )
    disk1 = _source("/disk1")
    disk2 = _source("/disk2")
    disk1_name = Path(disk1).name
    disk2_name = Path(disk2).name
    disk1_device = Path(f"/sys/class/block/{disk1_name}/device").resolve()
    disk2_device = Path(f"/sys/class/block/{disk2_name}/device").resolve()
    proven = bool(
        disk1.startswith("/dev/nvme") and disk2.startswith("/dev/nvme")
        and disk1 != disk2 and disk1_device != disk2_device
        and disk1_device.exists() and disk2_device.exists()
    )
    return {
        "proven": proven,
        "scene": matches[0],
        "target_mount": "/disk1",
        "target_source": disk1,
        "target_device_path": str(disk1_device),
        "sibling_mount": "/disk2",
        "sibling_source": disk2,
        "sibling_device_path": str(disk2_device),
        "target_journal_prefix": f"jbd2/{disk1_name}-",
        "sibling_journal_prefix": f"jbd2/{disk2_name}-",
        "mixed_target_mounts": ["/disk1", "/disk2"],
        "shared_nvme_completion_dependency": False,
    }


def rolling_quiet_gate(**kwargs: Any) -> dict[str, Any]:
    original = journal_gate._journal_authority
    journal_gate._journal_authority = _mixed_journal_authority
    try:
        result = predecessor.rolling_quiet_gate(**kwargs)
    finally:
        journal_gate._journal_authority = original
    result["release_version"] = "cpu_a_slots_v13_four_worker"
    result["mixed_ssd_scene_successor"] = {
        "resource_freeze": str(FREEZE),
        "physical_workers_per_xp_scene": 4,
        "disk1_and_disk2_are_both_target_capable": True,
        "four_scene_barrier": False,
        "external_process_control_or_stop_performed": False,
    }
    return result


__all__ = ["rolling_quiet_gate"]
