#!/usr/bin/env python3
"""Resume IO-B after committed Outer13 and the drained stale w7 query."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_b_top4_mixed_eight_worker_boundary_resume_v2 as boundary_v2
from scripts import run_mysql_algorithm_b_xp_top4_mixed_eight_worker_v11 as runtime_v11


HISTORY = ROOT / "history/mysql_four_scene_20260809"
SOURCE = HISTORY / "v755_to_b_io_outer13_recovered_resume_20260827_xp_io"
OUTER_1_ROOT = HISTORY / "v505_to_b_io_mixed_four_worker_boundary_resume_v71_20260827_xp_io"
OUTER_2_TO_5_ROOT = HISTORY / "v521_to_b_io_mixed_four_worker_boundary_resume_v73_20260827_xp_io"
OUTER_6_TO_7_ROOT = HISTORY / "v554_to_b_io_mixed_eight_worker_v2_recovered_20260827_xp_io"
OUTER_8_TO_9_ROOT = HISTORY / "v561_to_b_io_mixed_eight_worker_v6_outer7_resume_20260827_xp_io"
OUTER_10_ROOT = HISTORY / "v580_to_b_io_mixed_eight_worker_v7_outer9_resume_20260827_xp_io"
OUTER_11_ROOT = HISTORY / "v588_to_b_io_mixed_eight_worker_v7_outer10_resume_20260827_xp_io"
OUTER_12_ROOT = HISTORY / "v735_to_b_io_outer11_entrypoint_repaired_resume_20260827_xp_io"
CONTRACT_ROOT = ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_b_mixed_eight_worker_20260827_v11"
EXPECTED_ERROR = (
    "RuntimeError: XP active client sessions were not proven drained before the 60s bound: "
    "{'observed_at': '2026-08-27T18:46:31.130072+08:00', 'active_sessions': "
    "{'xp_io_w1': 0, 'xp_io_w2': 0, 'xp_io_w3': 0, 'xp_io_w4': 0, "
    "'xp_io_w5': 0, 'xp_io_w6': 0, 'xp_io_w7': 1, 'xp_io_w8': 0}}"
)


def install_runtime() -> Any:
    wrapper = boundary_v2.implementation
    wrapper["runtime8"] = runtime_v11
    wrapper["CONTRACT_ROOT"] = CONTRACT_ROOT
    wrapper["EXPECTED_ERROR"] = EXPECTED_ERROR
    wrapper["CONFIG"]["hybench_xp_io"] = {
        "source": SOURCE,
        "accepted": 2,
        "rejected": 11,
        "outer": 13,
        "decision_roots": {
            1: OUTER_1_ROOT,
            **{outer: OUTER_2_TO_5_ROOT for outer in range(2, 6)},
            **{outer: OUTER_6_TO_7_ROOT for outer in range(6, 8)},
            **{outer: OUTER_8_TO_9_ROOT for outer in range(8, 10)},
            10: OUTER_10_ROOT,
            11: OUTER_11_ROOT,
            12: OUTER_12_ROOT,
            13: SOURCE,
        },
    }
    core = wrapper["install"]("hybench_xp_io")
    impl = core._impl
    original_state_document = impl["state_document"]

    def state_document(run_id: str) -> dict[str, Any]:
        value = original_state_document(run_id)
        value["outer14_session_drain_continuation"] = {
            "source_run_root": str(SOURCE),
            "committed_outer_reused_through": 13,
            "discarded_candidate_measurements": 0,
            "accepted_or_rejected_credit_for_partial_outer": 0,
            "stale_session_worker": "xp_io_w7",
            "stale_session_user": "ra_benchmark",
            "stale_session_was_read_only_select": True,
            "stale_session_terminated_before_launch": True,
            "cross_scene_lock_used": False,
        }
        return value

    impl["state_document"] = state_document
    return core


def main() -> int:
    return int(install_runtime()._implementation_main())


if __name__ == "__main__":
    raise SystemExit(main())
