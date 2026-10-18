#!/usr/bin/env python3
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / "scripts/launch_mysql_algorithm_a_xp_top4_scene_v1.py").read_text()
replacements = {
    'CONTROLLER = ROOT / "scripts/run_mysql_algorithm_a_xp_top4_async_v1.py"':
        'CONTROLLER = ROOT / "scripts/run_mysql_algorithm_a_xp_top4_async_v4.py"',
    'REFRESH = ROOT / "scripts/refresh_mysql_xp4_live_authority_from_reset_v1.py"':
        'REFRESH = ROOT / "scripts/refresh_mysql_xp_combined_v4_live_authority_v2.py"',
    "frozen_mysql_xp_top4_algorithm_a_20260826_v1":
        "frozen_mysql_xp_top4_algorithm_a_20260826_v4",
    "v179_to_b_top4_fresh_20260826_attempt009_xp_cpu":
        "v234_to_b_top4_fresh_20260826_attempt021_xp_cpu",
    "v179_to_b_top4_fresh_20260826_attempt009_xp_io":
        "v230_to_b_top4_fresh_20260826_attempt019_xp_io",
    'f"v180_to_a_top4_fresh_20260826_attempt001_{suffix}"':
        'f"v235_to_a_top4_fresh_20260826_attempt004_{suffix}"',
    '"v180_to_a_top4_pipeline_20260826"':
        '"v235_to_a_top4_pipeline_20260826"',
    '"v180_to_a_top4_live_authority_20260826"':
        '"v235_to_a_top4_live_authority_20260826"',
    'f"mysql-alga-top4-v180-{suffix}.service"':
        'f"mysql-alga-top4-v235-{suffix}.service"',
}
for old, new in replacements.items():
    if source.count(old) != 1:
        raise RuntimeError(f"Top4 A v4 launcher fragment differs: {old}")
    source = source.replace(old, new)

implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._a_v4_launch",
    "__package__": "scripts",
}
exec(compile(source, str(Path(__file__).resolve()), "exec"), implementation)

if __name__ == "__main__":
    raise SystemExit(int(implementation["main"]()))
