#!/usr/bin/env python3
"""Freeze Top4-A v8 after corrected CPU-B v267."""

from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / "scripts/build_mysql_xp_top4_algorithm_a_release_v7.py").read_text(
    encoding="utf-8"
)
replacements = {
    "frozen_mysql_xp_top4_algorithm_a_20260826_v7":
        "frozen_mysql_xp_top4_algorithm_a_20260826_v8",
    "run_mysql_algorithm_a_xp_top4_async_v7.py":
        "run_mysql_algorithm_a_xp_top4_async_v8.py",
    "launch_mysql_algorithm_a_xp_top4_scene_v7.py":
        "launch_mysql_algorithm_a_xp_top4_scene_v8.py",
    "monitor_mysql_four_scene_abc_pipeline_v9.py":
        "monitor_mysql_four_scene_abc_pipeline_v10.py",
    "launch_mysql_four_scene_abc_top4_monitor_v9.py":
        "launch_mysql_four_scene_abc_top4_monitor_v10.py",
    "v265_to_b_top4_score_resume_20260826_attempt029_xp_cpu":
        "v267_to_b_top4_score_resume_20260826_attempt030_xp_cpu",
}
for old, new in replacements.items():
    if source.count(old) != 1:
        raise RuntimeError(f"Top4-A builder v8 fragment differs: {old}")
    source = source.replace(old, new)

implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._build_a_v8_impl",
    "__package__": "scripts",
}
exec(compile(source, str(Path(__file__).resolve()), "exec"), implementation)

if __name__ == "__main__":
    raise SystemExit(int(implementation["main"]()))
