#!/usr/bin/env python3
"""Launch Top4-A independently after each current Top4-B scene."""

from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / "scripts/launch_mysql_algorithm_a_xp_top4_scene_v4.py").read_text(
    encoding="utf-8"
)
replacements = {
    "run_mysql_algorithm_a_xp_top4_async_v4.py":
        "run_mysql_algorithm_a_xp_top4_async_v6.py",
    "frozen_mysql_xp_top4_algorithm_a_20260826_v4":
        "frozen_mysql_xp_top4_algorithm_a_20260826_v6",
    "v234_to_b_top4_fresh_20260826_attempt021_xp_cpu":
        "v255_to_b_top4_fresh_20260826_attempt028_xp_cpu",
    "v230_to_b_top4_fresh_20260826_attempt019_xp_io":
        "v257_to_b_top4_fresh_20260826_attempt020_xp_io",
    'f"v235_to_a_top4_fresh_20260826_attempt004_{suffix}"':
        'f"v258_to_a_top4_fresh_20260826_attempt006_{suffix}"',
    '"v235_to_a_top4_pipeline_20260826"':
        '"v258_to_a_top4_pipeline_20260826"',
    '"v235_to_a_top4_live_authority_20260826"':
        '"v258_to_a_top4_live_authority_20260826"',
    'f"mysql-alga-top4-v235-{suffix}.service"':
        'f"mysql-alga-top4-v258-{suffix}.service"',
}
for old, new in replacements.items():
    if source.count(old) != 1:
        raise RuntimeError(f"Top4-A v6 launcher fragment differs: {old}")
    source = source.replace(old, new)

implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._a_v6_launch",
    "__package__": "scripts",
}
exec(compile(source, str(Path(__file__).resolve()), "exec"), implementation)

if __name__ == "__main__":
    raise SystemExit(int(implementation["main"]()))
