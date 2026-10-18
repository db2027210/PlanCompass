#!/usr/bin/env python3
"""Launch the sealed v29 trust-diverse Algorithm B continuation."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import (
    launch_mysql_four_scene_algorithm_b_independent_parallel_221_resume_v9
    as predecessor,
)


predecessor.CONTROLLER = (
    ROOT / "scripts/run_mysql_four_scene_algorithm_b_trust_diverse_resume_v29.py"
)
predecessor.CONTRACT = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_b_trust_diverse_20260819_v29"
    / "algorithm_b_resume_contract.json"
)
predecessor.RESUME_SOURCE = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v26_formal_fresh_plan_stability_20260819_attempt025"
)


if __name__ == "__main__":
    raise SystemExit(predecessor.main())
