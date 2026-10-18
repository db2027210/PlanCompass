#!/usr/bin/env python3
"""Launch one current eight-worker XP Top4 Algorithm-A scene after B."""

from pathlib import Path
from typing import Any


SOURCE = Path(__file__).resolve().with_name(
    "launch_mysql_algorithm_a_xp_top4_scene_v9.py"
)
text = SOURCE.read_text(encoding="utf-8")
replacements = {
    "scripts/run_mysql_algorithm_a_xp_top4_async_v9.py":
        "scripts/run_mysql_algorithm_a_xp_top4_cpu_a_slots_eight_worker_v1.py",
    "scripts/refresh_mysql_xp_combined_v4_live_authority_v2.py":
        "scripts/refresh_mysql_xp8_cpu_a_slots_live_authority_from_reset_v1.py",
    "frozen_mysql_xp_top4_algorithm_a_20260826_v9":
        "frozen_mysql_xp_top4_algorithm_a_cpu_a_slots_eight_worker_20260828_v15",
    'MANAGEMENT_CPUSET = "240-241,496-497"':
        'MANAGEMENT_CPUSET = "484-487"',
    "range(1, 5)": "range(1, 9)",
    'state.get("physical_replay_parallelism") == 4':
        'state.get("physical_replay_parallelism") == 8',
    "MySQL v9 Top4 Algorithm A": "MySQL current eight-worker Top4 Algorithm A",
}
for old, new in replacements.items():
    if text.count(old) != 1:
        raise RuntimeError(f"current XP8 A launcher predecessor differs: {old}")
    text = text.replace(old, new)
implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._launch_mysql_a_xp8_cpu_a_slots_v1_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)


if __name__ == "__main__":
    raise SystemExit(implementation["main"]())
