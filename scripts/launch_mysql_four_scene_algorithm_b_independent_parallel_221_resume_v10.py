#!/usr/bin/env python3
"""Launch the v10 state-initialized Algorithm B 2+2+1 continuation."""

from pathlib import Path

from scripts import (
    launch_mysql_four_scene_algorithm_b_independent_parallel_221_resume_v9
    as predecessor,
)


ROOT = Path(__file__).resolve().parents[1]
predecessor.CONTROLLER = (
    ROOT
    / "scripts/run_mysql_four_scene_algorithm_b_independent_parallel_221_resume_v10.py"
)
predecessor.CONTRACT = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_b_parallel_221_20260818_v10"
    / "algorithm_b_parallel_221_contract.json"
)


if __name__ == "__main__":
    raise SystemExit(predecessor.main())
