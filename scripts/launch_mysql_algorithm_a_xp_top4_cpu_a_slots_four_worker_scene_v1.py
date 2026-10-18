#!/usr/bin/env python3
"""Launch one current four-worker XP Top4-A scene after terminal B."""

from pathlib import Path
from typing import Any


SOURCE = Path(__file__).resolve().with_name(
    "launch_mysql_algorithm_a_xp_top4_cpu_a_slots_scene_v2.py"
)
text = SOURCE.read_text(encoding="utf-8")
replacements = {
    "run_mysql_algorithm_a_xp_top4_cpu_a_slots_v2.py":
        "run_mysql_algorithm_a_xp_top4_cpu_a_slots_four_worker_v1.py",
    "refresh_mysql_cpu_a_slots_two_worker_scene_authority_v2.py":
        "refresh_mysql_cpu_a_slots_four_worker_scene_authority_v1.py",
    "frozen_mysql_xp_top4_algorithm_a_cpu_a_slots_20260828_v11":
        "frozen_mysql_xp_top4_algorithm_a_cpu_a_slots_four_worker_20260828_v13",
    'MANAGEMENT_CPUSET = "240-241,496-497"': 'MANAGEMENT_CPUSET = "484-487"',
    "two-worker A authority": "four-worker A authority",
    "two-worker Top4 Algorithm A": "four-worker Top4 Algorithm A",
    'state.get("physical_replay_parallelism") == 2':
        'state.get("physical_replay_parallelism") == 4',
    "mysql_algorithm_a_xp_top4_cpu_a_slots_scene_launch_v2":
        "mysql_algorithm_a_xp_top4_cpu_a_slots_four_worker_scene_launch_v1",
    '"physical_replay_parallelism": 2': '"physical_replay_parallelism": 4',
    "two-worker A exited": "four-worker A exited",
    "two-worker Algorithm A startup": "four-worker Algorithm A startup",
}
for old, new in replacements.items():
    expected_count = 2 if old == "two-worker A authority" else 1
    if text.count(old) != expected_count:
        raise RuntimeError(f"four-worker A launcher predecessor differs: {old}")
    text = text.replace(old, new)

implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._launch_mysql_a_xp_top4_cpu_a_slots_four_worker_v1_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)


if __name__ == "__main__":
    raise SystemExit(implementation["main"]())
