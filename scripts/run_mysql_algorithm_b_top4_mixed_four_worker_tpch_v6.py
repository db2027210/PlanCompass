#!/usr/bin/env python3
"""Resume TPC-H Outer-9 rank-5 after Docker-wide inventory timeout."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_b_four_scene_top4_mixed_four_worker_v8 as runtime
from scripts import run_mysql_algorithm_b_top4_boundary_resume_v2 as base
from scripts import run_mysql_algorithm_b_top4_mixed_four_worker_boundary_resume_v5 as successor

early = ROOT / "history/mysql_four_scene_20260809/v434_to_b_tpch_gate_continuation_20260826_tpch"
middle = ROOT / "history/mysql_four_scene_20260809/v449_to_b_tpch_outer6_relational_resume_v63_20260827_tpch"
outer8 = ROOT / "history/mysql_four_scene_20260809/v507_to_b_tpch_mixed_four_worker_boundary_resume_v71_20260827_tpch"
source = ROOT / "history/mysql_four_scene_20260809/v513_to_b_tpch_mixed_four_worker_boundary_resume_v72_20260827_tpch"
base.SOURCE = source
base.SCENE = "tpch_sf4_memory"
base.EXPECTED_ERROR = "TimeoutExpired: Command '['docker', 'inspect', '914ccb945455', '3b1e948903d3', 'ea4f70090dc2', 'ce598a72f3b6', 'e2695a42beb2', '2c10eb14e63a', '7c1110de1f30', 'f6696fa5aa9a', 'df8878741186', 'adf2d4660b93', '12482ff3a9a4', 'f0ea3bdae7b8', '7ea476c6386f', '5c5ce747115d', '74cb82f02999', '55f2f6fc2e05', '5c1d34740d89', '301e7d0643ef', 'a467c3e387ed', 'bfa05ff9f085', 'f34d0ca64b1e', '1ee9ca4ee76a', '60795cb17abf', 'c7be677c9df9', 'b60d76aece75', 'b6d4292d8a15', 'b447fc532eea', 'd3eaae0769da', '24506255b5f6', '82cb32e2c4e6', 'd945b08e1a2a', '6ffed7c09c37', '92b9442e4455', '705ab75bf430', 'c05c08c46647', '3f03773b12d0', 'beff322dfa43', '4e0f56d16ce0']' timed out after 60 seconds"
base.EXPECTED_ACCEPTED = 4
base.EXPECTED_REJECTED = 4
base.CALIBRATION_COMPLETED = 15
base.COMMITTED_OUTER = 8
base.PARTIAL_OUTER = 9
base.PARTIAL_RANKS = (1, 2, 3, 4)
base.COMMITTED_DECISION_ROOTS = {
    **{outer: early for outer in range(1, 6)},
    **{outer: middle for outer in range(6, 8)},
    8: outer8,
}
base.VERIFIED_BASELINE_ROOT = source
base.EXPECTED_BASELINE_WORKERS = 4
base.RUNTIME_PREDECESSOR = runtime
base.CONTRACT_ROOT = runtime.CONTRACT_ROOT
base.CONTRACT = runtime.CONTRACT
base.COORDINATION = runtime.COORDINATION


if __name__ == "__main__":
    raise SystemExit(successor.main())
