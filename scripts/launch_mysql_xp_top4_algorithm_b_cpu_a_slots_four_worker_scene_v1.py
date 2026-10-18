#!/usr/bin/env python3
"""Launch one current four-worker XP Top4-B scene after terminal C."""

from pathlib import Path
from typing import Any


SOURCE = Path(__file__).resolve().with_name(
    "launch_mysql_xp_top4_algorithm_b_cpu_a_slots_scene_v2.py"
)
text = SOURCE.read_text(encoding="utf-8")
replacements = {
    "run_mysql_algorithm_b_xp_top4_cpu_a_slots_v2.py":
        "run_mysql_algorithm_b_xp_top4_cpu_a_slots_four_worker_v1.py",
    "refresh_mysql_cpu_a_slots_two_worker_scene_authority_v2.py":
        "refresh_mysql_cpu_a_slots_four_worker_scene_authority_v1.py",
    "frozen_mysql_xp_top4_algorithm_b_cpu_a_slots_20260828_v44":
        "frozen_mysql_xp_top4_algorithm_b_cpu_a_slots_four_worker_20260828_v46",
    'MANAGEMENT_CPUSET = "240-241,496-497"': 'MANAGEMENT_CPUSET = "484-487"',
    "two-worker B authority": "four-worker B authority",
    "two-worker Top4 Algorithm B": "four-worker Top4 Algorithm B",
    "MYSQL_TWO_WORKER_AUTHORITY_ROOT": "MYSQL_MIXED_FOUR_WORKER_AUTHORITY_ROOT",
    "[[1, 2], [3, 4], [5]]": "[[1, 2, 3, 4], [5]]",
    'int(state.get("candidate_parallelism") or 0) == 2':
        'int(state.get("candidate_parallelism") or 0) == 4',
    "mysql_algorithm_b_xp_top4_cpu_a_slots_scene_launch_v2":
        "mysql_algorithm_b_xp_top4_cpu_a_slots_four_worker_scene_launch_v1",
    '"physical_replay_parallelism": 2': '"physical_replay_parallelism": 4',
    "two-worker B exited": "four-worker B exited",
    "two-worker Algorithm B startup": "four-worker Algorithm B startup",
}
for old, new in replacements.items():
    expected_count = 2 if old == "two-worker B authority" else 1
    if text.count(old) != expected_count:
        raise RuntimeError(f"four-worker B launcher predecessor differs: {old}")
    text = text.replace(old, new)

implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._launch_mysql_xp_top4_b_cpu_a_slots_four_worker_v1_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)


if __name__ == "__main__":
    raise SystemExit(implementation["main"]())
