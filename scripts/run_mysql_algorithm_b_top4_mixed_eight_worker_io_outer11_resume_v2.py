#!/usr/bin/env python3
"""Resume IO-B v10 after committed Outer11; discard partial Outer12 reset."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_b_top4_mixed_eight_worker_boundary_resume_v2 as boundary_v2
from scripts import run_mysql_algorithm_b_xp_top4_mixed_eight_worker_v10 as runtime_v8


HISTORY = ROOT / "history/mysql_four_scene_20260809"
FAILED_RUN = HISTORY / "v588_to_b_io_mixed_eight_worker_v7_outer10_resume_20260827_xp_io"
SOURCE = HISTORY / "v593_to_b_io_outer11_unlocked_continuation_source_20260827"
CALIBRATION_ROOT = HISTORY / "v580_to_b_io_mixed_eight_worker_v7_outer9_resume_20260827_xp_io"
OUTER_1_ROOT = HISTORY / "v505_to_b_io_mixed_four_worker_boundary_resume_v71_20260827_xp_io"
OUTER_2_TO_5_ROOT = HISTORY / "v521_to_b_io_mixed_four_worker_boundary_resume_v73_20260827_xp_io"
OUTER_6_TO_7_ROOT = HISTORY / "v554_to_b_io_mixed_eight_worker_v2_recovered_20260827_xp_io"
OUTER_8_TO_9_ROOT = HISTORY / "v561_to_b_io_mixed_eight_worker_v6_outer7_resume_20260827_xp_io"
CONTRACT_ROOT = ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_b_mixed_eight_worker_20260827_v10"
EXPECTED_ERROR = (
    "TimeoutExpired: Command '['docker', 'inspect', "
    "'ra_pgssd_mysql_baseline_xp_io_w1_20260826']' timed out after 240 seconds"
)


def install_runtime() -> Any:
    wrapper = boundary_v2.implementation
    wrapper["runtime8"] = runtime_v8
    wrapper["CONTRACT_ROOT"] = CONTRACT_ROOT
    wrapper["EXPECTED_ERROR"] = EXPECTED_ERROR
    wrapper["CONFIG"]["hybench_xp_io"] = {
        "source": SOURCE,
        "accepted": 2,
        "rejected": 9,
        "outer": 11,
        "decision_roots": {
            1: OUTER_1_ROOT,
            **{outer: OUTER_2_TO_5_ROOT for outer in range(2, 6)},
            **{outer: OUTER_6_TO_7_ROOT for outer in range(6, 8)},
            **{outer: OUTER_8_TO_9_ROOT for outer in range(8, 10)},
            10: CALIBRATION_ROOT,
            11: FAILED_RUN,
        },
    }
    core = wrapper["install"]("hybench_xp_io")
    impl = core._impl
    original_state_document = impl["state_document"]

    def state_document(run_id: str) -> dict[str, Any]:
        value = original_state_document(run_id)
        value["outer12_reset_failure_continuation"] = {
            "source_run_root": str(FAILED_RUN),
            "composed_continuation_source": str(SOURCE),
            "committed_outer_reused_through": 11,
            "discarded_partial_outer": 12,
            "discarded_candidate_measurements": 0,
            "discarded_partial_reset_workers": 5,
            "accepted_or_rejected_credit_for_partial_outer": 0,
            "docker_create_start_lock": False,
            "docker_control_concurrency_per_scene": 8,
            "worker_reset_parallelism": 8,
        }
        return value

    impl["state_document"] = state_document
    return core


def main() -> int:
    return int(install_runtime()._implementation_main())


if __name__ == "__main__":
    raise SystemExit(main())
