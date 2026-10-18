#!/usr/bin/env python3
"""Launch Top4-A after corrected CPU-B or fresh IO-B."""

from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / "scripts/launch_mysql_algorithm_a_xp_top4_scene_v7.py").read_text(
    encoding="utf-8"
)
replacements = {
    "run_mysql_algorithm_a_xp_top4_async_v7.py":
        "run_mysql_algorithm_a_xp_top4_async_v8.py",
    "frozen_mysql_xp_top4_algorithm_a_20260826_v7":
        "frozen_mysql_xp_top4_algorithm_a_20260826_v8",
    "v265_to_b_top4_score_resume_20260826_attempt029_xp_cpu":
        "v267_to_b_top4_score_resume_20260826_attempt030_xp_cpu",
    'f"v266_to_a_top4_fresh_20260826_attempt007_{suffix}"':
        'f"v268_to_a_top4_fresh_20260826_attempt008_{suffix}"',
    '"v266_to_a_top4_pipeline_20260826"':
        '"v268_to_a_top4_pipeline_20260826"',
    '"v266_to_a_top4_live_authority_20260826"':
        '"v268_to_a_top4_live_authority_20260826"',
    'f"mysql-alga-top4-v266-{suffix}.service"':
        'f"mysql-alga-top4-v268-{suffix}.service"',
}
for old, new in replacements.items():
    if source.count(old) != 1:
        raise RuntimeError(f"Top4-A launcher v8 fragment differs: {old}")
    source = source.replace(old, new)

implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._a_v8_launch",
    "__package__": "scripts",
}
exec(compile(source, str(Path(__file__).resolve()), "exec"), implementation)

if __name__ == "__main__":
    raise SystemExit(int(implementation["main"]()))
