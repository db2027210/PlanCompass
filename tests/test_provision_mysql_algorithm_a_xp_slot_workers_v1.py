from __future__ import annotations

import json
from pathlib import Path

from scripts import mysql_algorithm_a_dynamic_slots_v1 as slots
from scripts import provision_mysql_algorithm_a_xp_slot_workers_v1 as provision


ROOT = Path(__file__).resolve().parents[1]
FREEZE = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260813_v18"
    / "four_scene_effective_resource_successor.json"
)


def test_successor_freeze_has_four_workers_per_xp_scene_only():
    source = json.loads(FREEZE.read_text(encoding="utf-8"))
    additions = slots.build_released_xp_workers(source)
    terminal = {
        scene: {"complete": True, "accepted": 7} for scene in slots.MEMORY_SCENES
    }
    result = provision.build_successor_freeze(source, additions, terminal)
    assert result["schema_version"] == (
        "mysql_algorithm_a_xp_four_worker_resource_successor_v1"
    )
    assert len(result["workers"]) == 8
    counts = {
        scene: sum(row["scene"] == scene for row in result["workers"])
        for scene in slots.XP_SCENES
    }
    assert counts == {"hybench_xp_cpu": 4, "hybench_xp_io": 4}
    assert set(result["templates"]) == set(slots.XP_SCENES)
    assert result["worker_cohort_transition"]["native_xp_workload_unchanged"]

