#!/usr/bin/env python3
"""Launch fresh IO Top4-B after the current IO Top4-C completes."""

from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / "scripts/launch_mysql_xp_top4_algorithm_b_scene_v13.py").read_text(
    encoding="utf-8"
)
replacements = {
    "run_mysql_algorithm_b_xp_top4_async_v13.py":
        "run_mysql_algorithm_b_xp_top4_async_v23.py",
    "frozen_mysql_xp_top4_algorithm_b_20260826_v22":
        "frozen_mysql_xp_top4_algorithm_b_20260826_v32",
    "v225_to_c_top4_io_xp4_20260826_attempt020_xp_io/run_state.json":
        "v256_to_c_top4_io_outer6_partial_20260826_attempt023_xp_io/run_state.json",
    "v161_to_b_top4_fresh_bootstrap_20260826_v22_cpu":
        "v161_to_b_top4_fresh_bootstrap_20260826_v32_cpu",
    "v161_to_b_top4_fresh_bootstrap_20260826_v22_io":
        "v161_to_b_top4_fresh_bootstrap_20260826_v32_io",
    'f"v230_to_b_top4_fresh_20260826_attempt019_{suffix}"':
        'f"v257_to_b_top4_fresh_20260826_attempt020_{suffix}"',
    'HISTORY / "v230_to_b_top4_pipeline_20260826"':
        'HISTORY / "v257_to_b_top4_pipeline_20260826"',
    'HISTORY / "v230_to_b_top4_live_authority_20260826" / scene':
        'HISTORY / "v257_to_b_top4_live_authority_20260826" / scene',
    'f"mysql-algb-top4-v230-{suffix}.service"':
        'f"mysql-algb-top4-v257-{suffix}.service"',
}
for old, new in replacements.items():
    if source.count(old) != 1:
        raise RuntimeError(f"Top4-B v32 launcher fragment differs: {old}")
    source = source.replace(old, new)

implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._b_v32_io_launch",
    "__package__": "scripts",
}
exec(compile(source, str(Path(__file__).resolve()), "exec"), implementation)
implementation["SCENES"] = {
    "hybench_xp_io": implementation["SCENES"]["hybench_xp_io"]
}

if __name__ == "__main__":
    raise SystemExit(int(implementation["main"]()))
