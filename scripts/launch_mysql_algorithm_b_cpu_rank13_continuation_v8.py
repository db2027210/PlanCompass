#!/usr/bin/env python3
"""Launch CPU-B rank-13 continuation with the v4 native-XP drain runner."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
source = (
    ROOT / "scripts/launch_mysql_algorithm_b_cpu_rank13_continuation_v3.py"
).read_text(encoding="utf-8")
replacements = {
    'AUTHORITY = HISTORY / "v233_to_b_top4_live_authority_20260826/hybench_xp_cpu/live_authority.json"':
        'AUTHORITY = HISTORY / "v246_to_xp_top4_live_authority_20260826/hybench_xp_cpu/live_authority.json"',
    'CONTROLLER = ROOT / "scripts/run_mysql_algorithm_b_xp_top4_async_v16.py"':
        'CONTROLLER = ROOT / "scripts/run_mysql_algorithm_b_xp_top4_async_v21.py"',
    "frozen_mysql_xp_top4_algorithm_b_20260826_v25":
        "frozen_mysql_xp_top4_algorithm_b_20260826_v30",
    'RUN_ID = "v237_to_b_top4_fresh_20260826_attempt022_xp_cpu"':
        'RUN_ID = "v254_to_b_top4_fresh_20260826_attempt027_xp_cpu"',
    '"v237_to_b_top4_pipeline_20260826/xp_cpu.launch.json"':
        '"v254_to_b_top4_pipeline_20260826/xp_cpu.launch.json"',
    'UNIT = "mysql-algb-top4-v237-xp_cpu.service"':
        'UNIT = "mysql-algb-top4-v254-xp_cpu.service"',
}
for old, new in replacements.items():
    if source.count(old) != 1:
        raise RuntimeError(f"CPU-B v30 launcher fragment differs: {old}")
    source = source.replace(old, new)

implementation = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._cpu_b_rank13_launch_v8",
    "__package__": "scripts",
}
exec(compile(source, str(Path(__file__).resolve()), "exec"), implementation)

if __name__ == "__main__":
    raise SystemExit(int(implementation["main"]()))
