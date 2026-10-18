#!/usr/bin/env python3
"""Resume I/O Top4-B by retrying failed calibration rank 9 only."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_b_four_scene_top4_two_worker_v17 as runtime
from scripts import run_mysql_algorithm_b_top4_boundary_resume_v1 as base

base.SOURCE = ROOT / "history/mysql_four_scene_20260809/v445_to_b_io_complete_reset_facade_v60_20260826_xp_io"
base.SCENE = "hybench_xp_io"
base.EXPECTED_ERROR = "RuntimeError: Algorithm B calibration wave 9 failed closed"
base.EXPECTED_ACCEPTED = 0
base.EXPECTED_REJECTED = 0
base.CALIBRATION_COMPLETED = 8
base.COMMITTED_OUTER = 0
base.PARTIAL_OUTER = 0
base.PARTIAL_RANKS = ()
base.RUNTIME_PREDECESSOR = runtime
base.CONTRACT_ROOT = ROOT / "config/formal/frozen_mysql_four_scene_top4_algorithm_b_20260827_v64"
base.CONTRACT = base.CONTRACT_ROOT / "algorithm_b_top4_contract.json"
base.COORDINATION = base.CONTRACT_ROOT / "coordination_contract.json"

if __name__ == "__main__":
    raise SystemExit(base.main())
