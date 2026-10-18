#!/usr/bin/env python3
"""Run one XP Top4-B scene with four current CPU-A-slot workers."""

from pathlib import Path
from typing import Any


SOURCE = Path(__file__).resolve().with_name(
    "run_mysql_algorithm_b_four_scene_top4_mixed_four_worker_v1.py"
)
text = SOURCE.read_text(encoding="utf-8")
replacements = {
    "mysql_cross_project_wave_coordination_v14 as gate_v14":
        "mysql_cross_project_wave_coordination_cpu_a_slots_v13 as gate_v13",
    "reset_mysql_xp_data_planes_mixed_four_worker_v1 as reset_mixed":
        "reset_mysql_xp_data_planes_mixed_four_worker_v2 as reset_mixed",
    "frozen_mysql_four_scene_mixed_disk_four_worker_20260827_v3":
        "frozen_mysql_four_scene_cpu_a_slots_four_worker_20260828_v3",
    "mysql_four_scene_mixed_disk_four_worker_successor_v3":
        "mysql_four_scene_cpu_a_slots_four_worker_successor_v3",
    "frozen_mysql_four_scene_top4_algorithm_b_mixed_four_worker_20260827_v14":
        "frozen_mysql_xp_top4_algorithm_b_cpu_a_slots_four_worker_20260828_v46",
    "for prefix in (\"job\", \"tpch\", \"xp_cpu\", \"xp_io\")\n            for index in range(1, 5)":
        "for prefix, count in ((\"job\", 2), (\"tpch\", 2), (\"xp_cpu\", 4), (\"xp_io\", 4))\n            for index in range(1, count + 1)",
    "no_lease.coordination = gate_v14": "no_lease.coordination = gate_v13",
}
for old, new in replacements.items():
    if text.count(old) != 1:
        raise RuntimeError(f"CPU-A-slot four-worker B predecessor differs: {old}")
    text = text.replace(old, new)

implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._run_mysql_algorithm_b_xp_top4_cpu_a_slots_four_worker_v1_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)


if __name__ == "__main__":
    raise SystemExit(implementation["main"]())
