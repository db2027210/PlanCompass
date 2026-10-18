#!/usr/bin/env python3
"""Launch one XP Top4-B v38 scene after its own v61 C completes."""

from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / "scripts/launch_mysql_xp_top4_algorithm_b_scene_v2.py").read_text(
    encoding="utf-8"
)
replacements = {
    'CONTROLLER = ROOT / "scripts/run_mysql_algorithm_b_xp_top4_async_v2.py"':
        'CONTROLLER = ROOT / "scripts/run_mysql_algorithm_b_xp_top4_async_v31.py"',
    'REFRESH = ROOT / "scripts/refresh_mysql_xp4_live_authority_from_reset_v1.py"':
        'REFRESH = ROOT / "scripts/refresh_mysql_xp_combined_v4_live_authority_v2.py"',
    'frozen_mysql_xp_top4_algorithm_b_20260826_v11':
        'frozen_mysql_xp_top4_algorithm_b_20260826_v38',
    'v59_to_c_xp_top4_20260825_attempt033_cpu_zero_safe_xp_cpu/run_state.json':
        'v272_to_c_qps_rejection_20260826_attempt001_cpu_scene_xp_cpu/run_state.json',
    'v178_to_c_top4_io_xp4_20260826_attempt011_xp_io/run_state.json':
        'v272_to_c_qps_rejection_20260826_attempt001_io_scene_xp_io/run_state.json',
    'v161_to_b_top4_fresh_bootstrap_20260826_v11_cpu':
        'v161_to_b_top4_fresh_bootstrap_20260826_v38_cpu',
    'v161_to_b_top4_fresh_bootstrap_20260826_v11_io':
        'v161_to_b_top4_fresh_bootstrap_20260826_v38_io',
    '"reset_binding": HISTORY / "v161_to_b_top4_fresh_20260826_attempt008_xp_cpu/xp_resets/algorithm_b_independent_fresh_baseline/reset_binding.json",':
        '"reset_binding": None,',
    'f"v179_to_b_top4_fresh_20260826_attempt009_{suffix}"':
        'f"v274_to_b_qps_rejection_20260826_attempt001_{suffix}"',
    'HISTORY / "v179_to_b_top4_pipeline_20260826"':
        'HISTORY / "v274_to_b_qps_rejection_pipeline_20260826"',
    'HISTORY / "v179_to_b_top4_live_authority_20260826" / scene':
        'HISTORY / "v274_to_b_qps_rejection_live_authority_20260826" / scene',
    'f"mysql-algb-top4-v179-{suffix}.service"':
        'f"mysql-algb-top4-v274-{suffix}.service"',
    'and int((c_state.get("accepted_rounds") or {}).get(scene) or 0) >= 10\n':
        '',
}
for old, new in replacements.items():
    if source.count(old) != 1:
        raise RuntimeError(f"Top4-B v20 launcher fragment differs: {old}")
    source = source.replace(old, new)

implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._b_v38_qps_rejection_launch",
    "__package__": "scripts",
}
exec(compile(source, str(Path(__file__).resolve()), "exec"), implementation)


if __name__ == "__main__":
    raise SystemExit(int(implementation["main"]()))
