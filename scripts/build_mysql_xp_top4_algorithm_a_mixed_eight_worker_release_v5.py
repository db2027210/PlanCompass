#!/usr/bin/env python3
"""Freeze XP8 Algorithm-A v5 effective reset-dispatch release."""

from pathlib import Path
from typing import Any


SOURCE = Path(__file__).resolve().with_name(
    "build_mysql_xp_top4_algorithm_a_mixed_eight_worker_release_v4.py"
)
text = SOURCE.read_text(encoding="utf-8")
replacements = {
    "frozen_mysql_xp_top4_algorithm_a_mixed_eight_worker_20260827_v4":
        "frozen_mysql_xp_top4_algorithm_a_mixed_eight_worker_20260827_v5",
    "run_mysql_algorithm_a_xp_top4_mixed_eight_worker_v4.py":
        "run_mysql_algorithm_a_xp_top4_mixed_eight_worker_v5.py",
    "launch_mysql_algorithm_a_xp_top4_mixed_eight_worker_v4.py":
        "launch_mysql_algorithm_a_xp_top4_mixed_eight_worker_v5.py",
    "launch_mysql_algorithm_a_xp8_reset_recovery_v1.py":
        "launch_mysql_algorithm_a_xp8_reset_recovery_v2.py",
    "monitor_mysql_b_then_a_mixed_eight_worker_scene_v4.py":
        "monitor_mysql_b_then_a_mixed_eight_worker_scene_v6.py",
    "reset_mysql_xp_data_planes_mixed_eight_worker_scene_v1.py":
        "reset_mysql_xp_data_planes_mixed_eight_worker_scene_v2.py",
}
for old, new in replacements.items():
    if text.count(old) != 1:
        raise RuntimeError(f"XP8 A release v5 predecessor differs: {old}")
    text = text.replace(old, new)
implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._build_mysql_xp8_a_release_v5_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)
implementation["builder"].SOURCES.update({
    "single_scene_reset_dispatch":
        Path(__file__).resolve().with_name("mysql_single_scene_measurement_v1.py"),
})


if __name__ == "__main__":
    raise SystemExit(implementation["main"]())

