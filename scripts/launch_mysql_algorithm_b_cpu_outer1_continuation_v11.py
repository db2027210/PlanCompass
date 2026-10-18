#!/usr/bin/env python3
"""Launch corrected CPU-B score continuation v34."""

from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / "scripts/launch_mysql_algorithm_b_cpu_outer1_continuation_v10.py").read_text(
    encoding="utf-8"
)
replacements = {
    "run_mysql_algorithm_b_cpu_outer1_continuation_v25.py":
        "run_mysql_algorithm_b_cpu_outer1_continuation_v26.py",
    "frozen_mysql_xp_top4_algorithm_b_20260826_v33":
        "frozen_mysql_xp_top4_algorithm_b_20260826_v34",
    "v265_to_b_top4_score_resume_20260826_attempt029_xp_cpu":
        "v267_to_b_top4_score_resume_20260826_attempt030_xp_cpu",
    "v265_to_b_top4_pipeline_20260826":
        "v267_to_b_top4_pipeline_20260826",
    "mysql-algb-top4-v265-xp_cpu.service":
        "mysql-algb-top4-v267-xp_cpu.service",
}
for old, new in replacements.items():
    if source.count(old) != 1:
        raise RuntimeError(f"CPU-B launcher v11 fragment differs: {old}")
    source = source.replace(old, new)

implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._cpu_b_outer1_launch_v11_impl",
    "__package__": "scripts",
}
exec(compile(source, str(Path(__file__).resolve()), "exec"), implementation)

if __name__ == "__main__":
    raise SystemExit(int(implementation["main"]()))
