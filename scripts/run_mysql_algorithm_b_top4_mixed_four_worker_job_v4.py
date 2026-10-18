#!/usr/bin/env python3
"""Resume JOB Outer-5 with a scene-suffixed run identity."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_b_four_scene_top4_mixed_four_worker_v7 as runtime
from scripts import run_mysql_algorithm_b_top4_boundary_resume_v2 as base
from scripts import run_mysql_algorithm_b_top4_mixed_four_worker_boundary_resume_v4 as successor

early = ROOT / "history/mysql_four_scene_20260809/v433_to_b_job_gate_continuation_20260826_job"
late = ROOT / "history/mysql_four_scene_20260809/v489_to_b_job_mixed_four_worker_boundary_resume_v70_20260827_job"
source = ROOT / "history/mysql_four_scene_20260809/v509_to_b_job_mixed_four_worker_boundary_resume_v72_20260827"
base.SOURCE = source
base.SCENE = "job_memory"
base.EXPECTED_ERROR = "RuntimeError: independent Top-4 run id lacks a formal scene suffix"
base.EXPECTED_ACCEPTED = 4
base.EXPECTED_REJECTED = 0
base.CALIBRATION_COMPLETED = 15
base.COMMITTED_OUTER = 4
base.PARTIAL_OUTER = 5
base.PARTIAL_RANKS = (1, 2, 3, 4)
base.COMMITTED_DECISION_ROOTS = {
    **{outer: early for outer in range(1, 4)},
    4: late,
}
base.VERIFIED_BASELINE_ROOT = source
base.EXPECTED_BASELINE_WORKERS = 4
base.RUNTIME_PREDECESSOR = runtime
base.CONTRACT_ROOT = runtime.CONTRACT_ROOT
base.CONTRACT = runtime.CONTRACT
base.COORDINATION = runtime.COORDINATION


if __name__ == "__main__":
    raise SystemExit(successor.main())
