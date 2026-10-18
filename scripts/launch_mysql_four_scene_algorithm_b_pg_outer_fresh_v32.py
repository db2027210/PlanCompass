#!/usr/bin/env python3
"""Launch the frozen fresh v32 PG-style MySQL Algorithm B campaign."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import (
    launch_mysql_four_scene_algorithm_b_independent_parallel_221_resume_v9
    as launcher,
)


launcher.CONTROLLER = (
    ROOT / "scripts/run_mysql_four_scene_algorithm_b_pg_outer_fresh_v32.py"
)
launcher.CONTRACT = (
    ROOT / "config/formal/frozen_mysql_pg_outer_20260819_v32"
    / "algorithm_b_fresh_contract.json"
)
launcher.RESUME_SOURCE = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v6_formal_independent_outer_plan_resume_v63_20260818_attempt001"
)


if __name__ == "__main__":
    raise SystemExit(launcher.main())
