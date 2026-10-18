#!/usr/bin/env python3
"""Create clean r65 after the r64 pre-formal restore collision."""

from __future__ import annotations

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from shell import prepare_pg_algorithm_c_nomutex_cohort_r63 as prior  # noqa: E402


ROOT = Path(
    "/disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809"
)


def main() -> int:
    prior.R61 = ROOT / "20260813_algorithm_c_same_plan_numeric_clean_r62"
    prior.R62 = ROOT / "20260813_algorithm_c_prephysical_history_clean_r63"
    prior.R63 = ROOT / "20260814_algorithm_c_same_round_three_tier_clean_r65"
    return prior.main()


if __name__ == "__main__":
    raise SystemExit(main())
