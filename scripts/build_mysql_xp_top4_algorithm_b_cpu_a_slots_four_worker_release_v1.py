#!/usr/bin/env python3
"""Freeze current CPU-A-slot four-worker XP Top4-B."""

from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/build_mysql_four_scene_top4_algorithm_b_mixed_four_worker_release_v1.py"
text = SOURCE.read_text(encoding="utf-8")
replacements = {
    "frozen_mysql_four_scene_top4_algorithm_b_mixed_four_worker_20260827_v14":
        "frozen_mysql_xp_top4_algorithm_b_cpu_a_slots_four_worker_20260828_v46",
    "frozen_mysql_four_scene_mixed_disk_four_worker_20260827_v3":
        "frozen_mysql_four_scene_cpu_a_slots_four_worker_20260828_v3",
    "mysql_four_scene_mixed_disk_four_worker_successor_v3":
        "mysql_four_scene_cpu_a_slots_four_worker_successor_v3",
    "len(freeze.get(\"workers\") or []) != 16":
        "len(freeze.get(\"workers\") or []) != 12",
    '"maximum_concurrent_mysql_workers": 16':
        '"maximum_concurrent_mysql_workers": 12',
    '"disk1_worker_count": 8, "disk2_worker_count": 8':
        '"disk1_worker_count": 6, "disk2_worker_count": 6',
}
for old, new in replacements.items():
    expected_count = 2 if old == '"maximum_concurrent_mysql_workers": 16' else 1
    if text.count(old) != expected_count:
        raise RuntimeError(f"CPU-A-slot four-worker B builder predecessor differs: {old}")
    text = text.replace(old, new)

implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._build_mysql_xp_top4_b_cpu_a_slots_four_worker_v1_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)
sources = dict(implementation["SOURCES"])
sources.update({
    "builder": Path(__file__).resolve(),
    "runtime": ROOT / "scripts/run_mysql_algorithm_b_xp_top4_cpu_a_slots_four_worker_v1.py",
    "scene_launcher": ROOT / "scripts/launch_mysql_xp_top4_algorithm_b_cpu_a_slots_four_worker_scene_v1.py",
    "current_authority_refresh": ROOT / "scripts/refresh_mysql_cpu_a_slots_four_worker_scene_authority_v1.py",
    "dynamic_noninterference_gate": ROOT / "scripts/mysql_cross_project_wave_coordination_cpu_a_slots_v13.py",
    "resource_freeze_builder": ROOT / "scripts/build_mysql_algorithm_c_cpu_a_slots_four_worker_freeze_v1.py",
    "reset_authority": ROOT / "scripts/reset_mysql_xp_data_planes_mixed_four_worker_v2.py",
})
implementation["SOURCES"] = sources


if __name__ == "__main__":
    raise SystemExit(implementation["main"]())
