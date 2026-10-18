#!/usr/bin/env python3
"""Continue XP/I/O Top4-B using its already-valid four-lane baseline."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_b_four_scene_top4_mixed_four_worker_v3 as runtime
from scripts import run_mysql_algorithm_b_top4_boundary_resume_v2 as base
from scripts import run_mysql_algorithm_b_top4_mixed_four_worker_boundary_resume_v2 as successor

base.SOURCE = ROOT / "history/mysql_four_scene_20260809/v445_to_b_io_complete_reset_facade_v60_20260826_xp_io"
base.SCENE = "hybench_xp_io"
base.EXPECTED_ERROR = "RuntimeError: Algorithm B calibration wave 9 failed closed"
base.EXPECTED_ACCEPTED = 0
base.EXPECTED_REJECTED = 0
base.CALIBRATION_COMPLETED = 8
base.COMMITTED_OUTER = 0
base.PARTIAL_OUTER = 0
base.PARTIAL_RANKS = ()
base.COMMITTED_DECISION_ROOTS = {}
base.VERIFIED_BASELINE_ROOT = ROOT / "history/mysql_four_scene_20260809/v490_to_b_io_mixed_four_worker_boundary_resume_v70_20260827_xp_io"
base.EXPECTED_BASELINE_WORKERS = 4
base.RUNTIME_PREDECESSOR = runtime
base.CONTRACT_ROOT = runtime.CONTRACT_ROOT
base.CONTRACT = runtime.CONTRACT
base.COORDINATION = runtime.COORDINATION

if __name__ == "__main__":
    raise SystemExit(successor.main())
