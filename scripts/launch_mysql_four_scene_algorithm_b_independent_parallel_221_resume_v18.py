#!/usr/bin/env python3
"""Launch the v18 acceptance-aware/full-plan Algorithm B continuation."""

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
    ROOT
    / "scripts/run_mysql_four_scene_algorithm_b_independent_parallel_221_resume_v18.py"
)
predecessor.CONTRACT = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_b_parallel_221_20260818_v18"
    / "algorithm_b_parallel_221_contract.json"
)


if __name__ == "__main__":
    raise SystemExit(predecessor.main())
