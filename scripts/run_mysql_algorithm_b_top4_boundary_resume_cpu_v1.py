#!/usr/bin/env python3
"""Resume CPU Top4-B after its completed Outer-1 ranks 1-4."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_b_four_scene_top4_two_worker_v11 as runtime
from scripts import run_mysql_algorithm_b_top4_boundary_resume_v1 as base

base.SOURCE = ROOT / "history/mysql_four_scene_20260809/v432_to_b_cpu_gate_continuation_20260826_xp_cpu"
base.SCENE = "hybench_xp_cpu"
base.EXPECTED_ERROR = "RuntimeError: ra_mysql_disk2_algc_xp_cpu_w1_20260809 remained running after bound host-PID kill"
base.EXPECTED_ACCEPTED = 0
base.EXPECTED_REJECTED = 0
base.CALIBRATION_COMPLETED = 15
base.COMMITTED_OUTER = 0
base.PARTIAL_OUTER = 1
base.PARTIAL_RANKS = (1, 2, 3, 4)
base.RUNTIME_PREDECESSOR = runtime
base.CONTRACT_ROOT = ROOT / "config/formal/frozen_mysql_four_scene_top4_algorithm_b_20260827_v64"
base.CONTRACT = base.CONTRACT_ROOT / "algorithm_b_top4_contract.json"
base.COORDINATION = base.CONTRACT_ROOT / "coordination_contract.json"

if __name__ == "__main__":
    raise SystemExit(base.main())
