#!/usr/bin/env python3
"""Launch Top4-A after the corrected CPU-B or fresh IO-B completes."""

from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / "scripts/launch_mysql_algorithm_a_xp_top4_scene_v6.py").read_text(
    encoding="utf-8"
)
replacements = {
    "run_mysql_algorithm_a_xp_top4_async_v6.py":
        "run_mysql_algorithm_a_xp_top4_async_v7.py",
    "frozen_mysql_xp_top4_algorithm_a_20260826_v6":
        "frozen_mysql_xp_top4_algorithm_a_20260826_v7",
    "v255_to_b_top4_fresh_20260826_attempt028_xp_cpu":
        "v265_to_b_top4_score_resume_20260826_attempt029_xp_cpu",
    'f"v258_to_a_top4_fresh_20260826_attempt006_{suffix}"':
        'f"v266_to_a_top4_fresh_20260826_attempt007_{suffix}"',
    '"v258_to_a_top4_pipeline_20260826"':
        '"v266_to_a_top4_pipeline_20260826"',
    '"v258_to_a_top4_live_authority_20260826"':
        '"v266_to_a_top4_live_authority_20260826"',
    'f"mysql-alga-top4-v258-{suffix}.service"':
        'f"mysql-alga-top4-v266-{suffix}.service"',
}
for old, new in replacements.items():
    if source.count(old) != 1:
        raise RuntimeError(f"Top4-A v7 launcher fragment differs: {old}")
    source = source.replace(old, new)

implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._a_v7_launch",
    "__package__": "scripts",
}
exec(compile(source, str(Path(__file__).resolve()), "exec"), implementation)

if __name__ == "__main__":
    raise SystemExit(int(implementation["main"]()))
