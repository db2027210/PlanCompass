#!/usr/bin/env python3
"""Freeze XP8 Algorithm-A v10 refreshed-binding successor."""

from pathlib import Path
from typing import Any


SOURCE = Path(__file__).resolve().with_name(
    "build_mysql_xp_top4_algorithm_a_mixed_eight_worker_release_v9.py"
)
text = SOURCE.read_text(encoding="utf-8")
replacements = {
    "frozen_mysql_xp_top4_algorithm_a_mixed_eight_worker_20260827_v9":
        "frozen_mysql_xp_top4_algorithm_a_mixed_eight_worker_20260827_v10",
    "run_mysql_algorithm_a_xp_top4_mixed_eight_worker_v9.py":
        "run_mysql_algorithm_a_xp_top4_mixed_eight_worker_v10.py",
    "launch_mysql_algorithm_a_xp_top4_mixed_eight_worker_v9.py":
        "launch_mysql_algorithm_a_xp_top4_mixed_eight_worker_v10.py",
    "monitor_mysql_b_then_a_mixed_eight_worker_scene_v10.py":
        "monitor_mysql_b_then_a_mixed_eight_worker_scene_v13.py",
}
for old, new in replacements.items():
    if text.count(old) != 1:
        raise RuntimeError(f"XP8 A release v10 predecessor differs: {old}")
    text = text.replace(old, new)
implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._build_mysql_xp8_a_release_v10_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)


if __name__ == "__main__":
    raise SystemExit(implementation["main"]())
