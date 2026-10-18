#!/usr/bin/env python3
"""Freeze Top4-B with the nonblocking host-process inventory gate."""

from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SOURCE = ROOT / "scripts/build_mysql_four_scene_top4_algorithm_b_mixed_four_worker_release_v1.py"
text = SOURCE.read_text(encoding="utf-8")
replacements = {
    "frozen_mysql_four_scene_top4_algorithm_b_mixed_four_worker_20260827_v14":
        "frozen_mysql_four_scene_top4_algorithm_b_mixed_four_worker_20260827_v22",
    "frozen_mysql_four_scene_mixed_disk_four_worker_20260827_v3":
        "frozen_mysql_four_scene_mixed_disk_four_worker_20260827_v4",
    "mysql_four_scene_mixed_disk_four_worker_successor_v3":
        "mysql_four_scene_mixed_disk_four_worker_successor_v4",
}
for old, new in replacements.items():
    if text.count(old) != 1:
        raise RuntimeError(f"mixed B v22 predecessor differs: {old}")
    text = text.replace(old, new)
implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._build_mysql_mixed_b_release_v8_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)
sources = dict(implementation["SOURCES"])
sources.update({
    "builder": Path(__file__).resolve(),
    "runtime": ROOT / "scripts/run_mysql_algorithm_b_four_scene_top4_mixed_four_worker_v8.py",
    "boundary_controller": ROOT / "scripts/run_mysql_algorithm_b_top4_boundary_resume_v2.py",
    "mixed_boundary_controller": ROOT / "scripts/run_mysql_algorithm_b_top4_mixed_four_worker_boundary_resume_v5.py",
    "job_controller": ROOT / "scripts/run_mysql_algorithm_b_top4_mixed_four_worker_job_v4.py",
    "tpch_controller": ROOT / "scripts/run_mysql_algorithm_b_top4_mixed_four_worker_tpch_v6.py",
    "cpu_controller": ROOT / "scripts/run_mysql_algorithm_b_top4_mixed_four_worker_cpu_v5.py",
    "io_controller": ROOT / "scripts/run_mysql_algorithm_b_top4_mixed_four_worker_io_v3.py",
    "reset_authority": ROOT / "scripts/reset_mysql_xp_data_planes_mixed_four_worker_v2.py",
    "adoption": ROOT / "scripts/adopt_mysql_mixed_disk_scene_w34_v2.py",
    "authority_builder": ROOT / "scripts/build_mysql_mixed_disk_four_worker_scene_authority_v4.py",
    "dynamic_noninterference_gate": ROOT / "scripts/mysql_cross_project_wave_coordination_v17.py",
    "resource_freeze_builder": ROOT / "scripts/build_mysql_mixed_disk_four_worker_resource_release_v4.py",
})
implementation["SOURCES"] = sources


if __name__ == "__main__":
    raise SystemExit(implementation["main"]())
