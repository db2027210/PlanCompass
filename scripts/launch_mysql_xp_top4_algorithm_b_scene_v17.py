#!/usr/bin/env python3
"""Single-layer launcher for fresh IO Top4-B after recovered IO-C."""

from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / "scripts/launch_mysql_xp_top4_algorithm_b_scene_v2.py").read_text(
    encoding="utf-8"
)
replacements = {
    'CONTROLLER = ROOT / "scripts/run_mysql_algorithm_b_xp_top4_async_v2.py"':
        'CONTROLLER = ROOT / "scripts/run_mysql_algorithm_b_xp_top4_async_v28.py"',
    'REFRESH = ROOT / "scripts/refresh_mysql_xp4_live_authority_from_reset_v1.py"':
        'REFRESH = ROOT / "scripts/refresh_mysql_xp_combined_v4_live_authority_v2.py"',
    "frozen_mysql_xp_top4_algorithm_b_20260826_v11":
        "frozen_mysql_xp_top4_algorithm_b_20260826_v35",
    "v178_to_c_top4_io_xp4_20260826_attempt011_xp_io/run_state.json":
        "v263_to_c_top4_io_empty_wave_20260826_attempt024_xp_io/run_state.json",
    "v161_to_b_top4_fresh_bootstrap_20260826_v11_cpu":
        "v161_to_b_top4_fresh_bootstrap_20260826_v35_cpu",
    "v161_to_b_top4_fresh_bootstrap_20260826_v11_io":
        "v161_to_b_top4_fresh_bootstrap_20260826_v35_io",
    "v161_to_b_top4_fresh_20260826_attempt008_xp_cpu/xp_resets/algorithm_b_independent_fresh_baseline/reset_binding.json":
        "v221_to_xp_cpu_combined_v4_transition_20260826/reset_binding.json",
    'f"v179_to_b_top4_fresh_20260826_attempt009_{suffix}"':
        'f"v257_to_b_top4_fresh_20260826_attempt020_{suffix}"',
    'HISTORY / "v179_to_b_top4_pipeline_20260826"':
        'HISTORY / "v257_to_b_top4_pipeline_20260826"',
    'HISTORY / "v179_to_b_top4_live_authority_20260826" / scene':
        'HISTORY / "v257_to_b_top4_live_authority_20260826" / scene',
    'f"mysql-algb-top4-v179-{suffix}.service"':
        'f"mysql-algb-top4-v257-{suffix}.service"',
}
for old, new in replacements.items():
    if source.count(old) != 1:
        raise RuntimeError(f"Top4-B single-layer launcher fragment differs: {old}")
    source = source.replace(old, new)

implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._b_v35_single_layer_launch",
    "__package__": "scripts",
}
exec(compile(source, str(Path(__file__).resolve()), "exec"), implementation)

if __name__ == "__main__":
    raise SystemExit(int(implementation["main"]()))
