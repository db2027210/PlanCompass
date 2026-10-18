#!/usr/bin/env python3
"""Freeze corrected no-lease Top4-B v34."""

from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / "scripts/build_mysql_xp_top4_algorithm_b_release_v23.py").read_text(
    encoding="utf-8"
)
replacements = {
    "frozen_mysql_xp_top4_algorithm_b_20260826_v33":
        "frozen_mysql_xp_top4_algorithm_b_20260826_v34",
    "run_mysql_algorithm_b_xp_top4_async_v24.py":
        "run_mysql_algorithm_b_xp_top4_async_v27.py",
    "run_mysql_algorithm_b_cpu_outer1_continuation_v25.py":
        "run_mysql_algorithm_b_cpu_outer1_continuation_v26.py",
    "launch_mysql_xp_top4_algorithm_b_scene_v15.py":
        "launch_mysql_xp_top4_algorithm_b_scene_v16.py",
    "launch_mysql_algorithm_b_cpu_outer1_continuation_v10.py":
        "launch_mysql_algorithm_b_cpu_outer1_continuation_v11.py",
    "monitor_mysql_four_scene_abc_pipeline_v9.py":
        "monitor_mysql_four_scene_abc_pipeline_v10.py",
    "launch_mysql_four_scene_abc_top4_monitor_v9.py":
        "launch_mysql_four_scene_abc_top4_monitor_v10.py",
    'builder.BOOTSTRAP_VERSION = "v33"':
        'builder.BOOTSTRAP_VERSION = "v34"',
    "v161_to_b_top4_fresh_bootstrap_20260826_v33_":
        "v161_to_b_top4_fresh_bootstrap_20260826_v34_",
}
for old, new in replacements.items():
    if source.count(old) != 1:
        raise RuntimeError(f"Top4-B builder v24 fragment differs: {old}")
    source = source.replace(old, new)

implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._build_b_v34_impl",
    "__package__": "scripts",
}
exec(compile(source, str(Path(__file__).resolve()), "exec"), implementation)

if __name__ == "__main__":
    raise SystemExit(int(implementation["main"]()))
