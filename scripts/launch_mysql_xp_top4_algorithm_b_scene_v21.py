#!/usr/bin/env python3
"""Launch one XP Top4-B v39 scene after its own v61 C completes."""

from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / "scripts/launch_mysql_xp_top4_algorithm_b_scene_v20.py").read_text(
    encoding="utf-8"
)
replacements = {
    'run_mysql_algorithm_b_xp_top4_async_v31.py':
        'run_mysql_algorithm_b_xp_top4_async_v32.py',
    'frozen_mysql_xp_top4_algorithm_b_20260826_v38':
        'frozen_mysql_xp_top4_algorithm_b_20260826_v39',
    'v161_to_b_top4_fresh_bootstrap_20260826_v38_cpu':
        'v161_to_b_top4_fresh_bootstrap_20260826_v39_cpu',
    'v161_to_b_top4_fresh_bootstrap_20260826_v38_io':
        'v161_to_b_top4_fresh_bootstrap_20260826_v39_io',
    'v274_to_b_qps_rejection_20260826_attempt001_':
        'v275_to_b_qps_rejection_20260826_attempt001_',
    'v274_to_b_qps_rejection_pipeline_20260826':
        'v275_to_b_qps_rejection_pipeline_20260826',
    'v274_to_b_qps_rejection_live_authority_20260826':
        'v275_to_b_qps_rejection_live_authority_20260826',
    'mysql-algb-top4-v274-': 'mysql-algb-top4-v275-',
}
for old, new in replacements.items():
    if source.count(old) != 1:
        raise RuntimeError(f"Top4-B v21 launcher fragment differs: {old}")
    source = source.replace(old, new)

implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._b_v39_qps_rejection_launch",
    "__package__": "scripts",
}
exec(compile(source, str(Path(__file__).resolve()), "exec"), implementation)


if __name__ == "__main__":
    raise SystemExit(int(implementation["main"]()))
