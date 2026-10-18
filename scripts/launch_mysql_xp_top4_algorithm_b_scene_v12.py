#!/usr/bin/env python3
"""Launch the post-C IO B successor; CPU remains independently addressable."""

from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / "scripts/launch_mysql_xp_top4_algorithm_b_scene_v6.py").read_text()
replacements = {
    "run_mysql_algorithm_b_xp_top4_async_v6.py":
        "run_mysql_algorithm_b_xp_top4_async_v12.py",
    "refresh_mysql_xp_cpu_current_gap_live_authority_v1.py":
        "refresh_mysql_xp_combined_v4_live_authority_v2.py",
    "frozen_mysql_xp_top4_algorithm_b_20260826_v15":
        "frozen_mysql_xp_top4_algorithm_b_20260826_v21",
    "v178_to_c_top4_io_xp4_20260826_attempt011_xp_io/run_state.json":
        "v225_to_c_top4_io_xp4_20260826_attempt020_xp_io/run_state.json",
    "v161_to_b_top4_fresh_bootstrap_20260826_v15_cpu":
        "v161_to_b_top4_fresh_bootstrap_20260826_v21_cpu",
    "v161_to_b_top4_fresh_bootstrap_20260826_v15_io":
        "v161_to_b_top4_fresh_bootstrap_20260826_v21_io",
    "v210_to_xp_cpu_current_gap_transition_20260826/reset_binding.json":
        "v221_to_xp_cpu_combined_v4_transition_20260826/reset_binding.json",
    'f"v203_to_b_top4_fresh_20260826_attempt013_{suffix}"':
        'f"v230_to_b_top4_fresh_20260826_attempt019_{suffix}"',
    'HISTORY / "v203_to_b_top4_pipeline_20260826"':
        'HISTORY / "v230_to_b_top4_pipeline_20260826"',
    'HISTORY / "v211_to_xp_cpu_current_gap_live_authority_20260826" / scene':
        'HISTORY / "v230_to_b_top4_live_authority_20260826" / scene',
    'f"mysql-algb-top4-v203-{suffix}.service"':
        'f"mysql-algb-top4-v230-{suffix}.service"',
}
for old, new in replacements.items():
    if source.count(old) != 1:
        raise RuntimeError(f"B v21 launcher fragment differs: {old}")
    source = source.replace(old, new)

implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._b_v21_launch",
    "__package__": "scripts",
}
exec(compile(source, str(Path(__file__).resolve()), "exec"), implementation)

if __name__ == "__main__":
    raise SystemExit(int(implementation["main"]()))
