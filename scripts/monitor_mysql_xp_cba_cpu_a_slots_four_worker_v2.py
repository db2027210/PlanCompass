#!/usr/bin/env python3
"""Independently advance one XP scene through four-worker C -> B -> A."""

from pathlib import Path
from typing import Any


SOURCE = Path(__file__).resolve().with_name(
    "monitor_mysql_xp_cba_cpu_a_slots_v1.py"
)
text = SOURCE.read_text(encoding="utf-8")
replacements = {
    "launch_mysql_xp_top4_algorithm_b_cpu_a_slots_scene_v2.py":
        "launch_mysql_xp_top4_algorithm_b_cpu_a_slots_four_worker_scene_v1.py",
    "launch_mysql_algorithm_a_xp_top4_cpu_a_slots_scene_v2.py":
        "launch_mysql_algorithm_a_xp_top4_cpu_a_slots_four_worker_scene_v1.py",
    "mysql_xp_cba_cpu_a_slots_independent_pipeline_v1":
        "mysql_xp_cba_cpu_a_slots_four_worker_independent_pipeline_v2",
}
for old, new in replacements.items():
    if text.count(old) != 1:
        raise RuntimeError(f"four-worker CBA monitor predecessor differs: {old}")
    text = text.replace(old, new)

implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._monitor_mysql_xp_cba_cpu_a_slots_four_worker_v2_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)


if __name__ == "__main__":
    raise SystemExit(implementation["main"]())
