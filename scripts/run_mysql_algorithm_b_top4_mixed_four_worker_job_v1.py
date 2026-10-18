#!/usr/bin/env python3
"""Continue JOB Top4-B on mixed-SSD w1-w4."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_b_four_scene_top4_mixed_four_worker_v1 as runtime
from scripts import run_mysql_algorithm_b_top4_boundary_resume_v1 as base
from scripts import run_mysql_algorithm_b_top4_mixed_four_worker_boundary_resume_v1 as successor

base.SOURCE = ROOT / "history/mysql_four_scene_20260809/v433_to_b_job_gate_continuation_20260826_job"
base.SCENE = "job_memory"
base.EXPECTED_ERROR = "TimeoutExpired: Command '['mysql', '--defaults-file=/disk2/resource_adaptive_mysql_algorithm_c_disk2_v4/job_w1/credentials/selected.cnf', '--host=127.0.0.1', '--port=65311', '--connect-timeout=5', '-NBe', \"SELECT ID,COMMAND,TIME FROM information_schema.PROCESSLIST WHERE USER='ra_benchmark' ORDER BY ID\"]' timed out after 15 seconds"
base.EXPECTED_ACCEPTED = 3
base.EXPECTED_REJECTED = 0
base.CALIBRATION_COMPLETED = 15
base.COMMITTED_OUTER = 3
base.PARTIAL_OUTER = 4
base.PARTIAL_RANKS = (1, 2)
base.RUNTIME_PREDECESSOR = runtime
base.CONTRACT_ROOT = runtime.CONTRACT_ROOT
base.CONTRACT = runtime.CONTRACT
base.COORDINATION = runtime.COORDINATION

if __name__ == "__main__":
    raise SystemExit(successor.main())
