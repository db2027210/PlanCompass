#!/usr/bin/env python3
"""Launch one XP Top4-B v37 scene after its own v61 C completes."""

from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / "scripts/launch_mysql_xp_top4_algorithm_b_scene_v18.py").read_text(
    encoding="utf-8"
)
replacements = {
    'run_mysql_algorithm_b_xp_top4_async_v29.py':
        'run_mysql_algorithm_b_xp_top4_async_v30.py',
    'frozen_mysql_xp_top4_algorithm_b_20260826_v36':
        'frozen_mysql_xp_top4_algorithm_b_20260826_v37',
    'v270_to_c_qps_only_20260826_attempt001_xp_cpu/run_state.json':
        'v272_to_c_qps_rejection_20260826_attempt001_cpu_scene_xp_cpu/run_state.json',
    'v270_to_c_qps_only_20260826_attempt001_xp_io/run_state.json':
        'v272_to_c_qps_rejection_20260826_attempt001_io_scene_xp_io/run_state.json',
    'and int((c_state.get("accepted_rounds") or {}).get(scene) or 0) >= 10\n':
        '',
    'v161_to_b_top4_fresh_bootstrap_20260826_v36_cpu':
        'v161_to_b_top4_fresh_bootstrap_20260826_v37_cpu',
    'v161_to_b_top4_fresh_bootstrap_20260826_v36_io':
        'v161_to_b_top4_fresh_bootstrap_20260826_v37_io',
    'v271_to_b_qps_only_20260826_attempt001_':
        'v273_to_b_qps_rejection_20260826_attempt001_',
    'v271_to_b_qps_only_pipeline_20260826':
        'v273_to_b_qps_rejection_pipeline_20260826',
    'v271_to_b_qps_only_live_authority_20260826':
        'v273_to_b_qps_rejection_live_authority_20260826',
    'mysql-algb-top4-v271-': 'mysql-algb-top4-v273-',
}
for old, new in replacements.items():
    if source.count(old) != 1:
        raise RuntimeError(f"Top4-B v19 launcher fragment differs: {old}")
    source = source.replace(old, new)

implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._b_v37_qps_rejection_launch",
    "__package__": "scripts",
}
exec(compile(source, str(Path(__file__).resolve()), "exec"), implementation)


if __name__ == "__main__":
    raise SystemExit(int(implementation["main"]()))
