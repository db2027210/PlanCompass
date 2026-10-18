#!/usr/bin/env python3
"""Resume CPU Outer-1 rank-5 after the old optional-container probe timeout."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_b_four_scene_top4_mixed_four_worker_v7 as runtime
from scripts import run_mysql_algorithm_b_top4_boundary_resume_v2 as base
from scripts import run_mysql_algorithm_b_top4_mixed_four_worker_boundary_resume_v4 as successor

source = ROOT / "history/mysql_four_scene_20260809/v508_to_b_cpu_mixed_four_worker_fresh_outer1_v71_20260827_xp_cpu"
base.SOURCE = source
base.SCENE = "hybench_xp_cpu"
base.EXPECTED_ERROR = "TimeoutExpired: Command '['docker', 'inspect', 'bench-pg-mixstrg-0817']' timed out after 15 seconds"
base.EXPECTED_ACCEPTED = 0
base.EXPECTED_REJECTED = 0
base.CALIBRATION_COMPLETED = 15
base.COMMITTED_OUTER = 0
base.PARTIAL_OUTER = 1
base.PARTIAL_RANKS = (1, 2, 3, 4)
base.COMMITTED_DECISION_ROOTS = {}
base.VERIFIED_BASELINE_ROOT = source
base.EXPECTED_BASELINE_WORKERS = 4
base.RUNTIME_PREDECESSOR = runtime
base.CONTRACT_ROOT = runtime.CONTRACT_ROOT
base.CONTRACT = runtime.CONTRACT
base.COORDINATION = runtime.COORDINATION


if __name__ == "__main__":
    raise SystemExit(successor.main())
