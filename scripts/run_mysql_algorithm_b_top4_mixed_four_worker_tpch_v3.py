#!/usr/bin/env python3
"""Continue TPC-H Top4-B with explicit per-round evidence roots."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_b_four_scene_top4_mixed_four_worker_v3 as runtime
from scripts import run_mysql_algorithm_b_top4_boundary_resume_v2 as base
from scripts import run_mysql_algorithm_b_top4_mixed_four_worker_boundary_resume_v2 as successor

early = ROOT / "history/mysql_four_scene_20260809/v434_to_b_tpch_gate_continuation_20260826_tpch"
late = ROOT / "history/mysql_four_scene_20260809/v449_to_b_tpch_outer6_relational_resume_v63_20260827_tpch"
base.SOURCE = late
base.SCENE = "tpch_sf4_memory"
base.EXPECTED_ERROR = "RuntimeError: dynamic non-interference gate failed closed"
base.EXPECTED_ACCEPTED = 4
base.EXPECTED_REJECTED = 3
base.CALIBRATION_COMPLETED = 15
base.COMMITTED_OUTER = 7
base.PARTIAL_OUTER = 8
base.PARTIAL_RANKS = (1, 2, 3, 4)
base.COMMITTED_DECISION_ROOTS = {
    **{outer: early for outer in range(1, 6)},
    **{outer: late for outer in range(6, 8)},
}
base.VERIFIED_BASELINE_ROOT = None
base.EXPECTED_BASELINE_WORKERS = 2
base.RUNTIME_PREDECESSOR = runtime
base.CONTRACT_ROOT = runtime.CONTRACT_ROOT
base.CONTRACT = runtime.CONTRACT
base.COORDINATION = runtime.COORDINATION

if __name__ == "__main__":
    raise SystemExit(successor.main())
