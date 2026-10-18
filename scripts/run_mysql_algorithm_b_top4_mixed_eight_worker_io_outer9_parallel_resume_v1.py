#!/usr/bin/env python3
"""Resume IO-B after committed Outer 9 and discard unmeasured Outer 10 setup."""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_b_top4_mixed_eight_worker_boundary_resume_v2 as boundary_v2
from scripts import run_mysql_algorithm_b_xp_top4_mixed_eight_worker_v7 as runtime_v7


HISTORY = ROOT / "history/mysql_four_scene_20260809"
SOURCE = HISTORY / "v561_to_b_io_mixed_eight_worker_v6_outer7_resume_20260827_xp_io"
OUTER_1_ROOT = HISTORY / "v505_to_b_io_mixed_four_worker_boundary_resume_v71_20260827_xp_io"
OUTER_2_TO_5_ROOT = HISTORY / "v521_to_b_io_mixed_four_worker_boundary_resume_v73_20260827_xp_io"
OUTER_6_TO_7_ROOT = HISTORY / "v554_to_b_io_mixed_eight_worker_v2_recovered_20260827_xp_io"
CONTRACT_ROOT = ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_b_mixed_eight_worker_20260827_v7"
TECHNICAL_ERROR = "TechnicalBoundaryStop: Outer 9 committed; Outer 10 unmeasured"


def install_runtime() -> Any:
    wrapper = boundary_v2.implementation
    wrapper["runtime8"] = runtime_v7
    wrapper["CONTRACT_ROOT"] = CONTRACT_ROOT
    wrapper["EXPECTED_ERROR"] = TECHNICAL_ERROR
    wrapper["CONFIG"]["hybench_xp_io"] = {
        "source": SOURCE,
        "accepted": 2,
        "rejected": 7,
        "outer": 9,
        "decision_roots": {
            1: OUTER_1_ROOT,
            **{outer: OUTER_2_TO_5_ROOT for outer in range(2, 6)},
            **{outer: OUTER_6_TO_7_ROOT for outer in range(6, 8)},
        },
    }
    core = wrapper["install"]("hybench_xp_io")
    impl = core._impl
    alg = impl["alg"]
    predecessor_validate = impl["validate_resume_source"]
    state_path = SOURCE / "run_state.json"

    def validate_resume_source(source: Path) -> dict[str, Any]:
        if source.resolve() != SOURCE.resolve():
            raise RuntimeError("IO-B Outer-9 continuation source differs")
        actual = alg.load(state_path)
        reset_root = SOURCE / "xp_resets/algorithm_b_outer_010_rank_1"
        batch = SOURCE / "outer_010/top5_batch_1/batch_result.json"
        controller_pid = int(actual.get("pid") or 0)
        if (
            actual.get("algorithm") != "B"
            or actual.get("async_scene") != "hybench_xp_io"
            or actual.get("status") != "RUNNING"
            or actual.get("stage")
            != "ALGORITHM_B_OUTER_010_RANK_1_RESETTING_XP"
            or int(actual.get("outer_attempt") or 0) != 9
            or int((actual.get("accepted_rounds") or {}).get("hybench_xp_io") or 0) != 2
            or int((actual.get("rejected_outer_attempts") or {}).get("hybench_xp_io") or 0) != 7
            or int((actual.get("consecutive_failed_rounds") or {}).get("hybench_xp_io") or 0) != 0
            or int((actual.get("incumbent_centered_forced_shrinks") or {}).get("hybench_xp_io") or 0) != 3
            or (
                reset_root.exists()
                and any(
                    item.is_file() or item.is_symlink()
                    for item in reset_root.rglob("*")
                )
            )
            or batch.exists()
            or (controller_pid > 1 and Path(f"/proc/{controller_pid}").exists())
        ):
            raise RuntimeError("IO-B technical Outer-9 boundary differs")
        original_load = alg.load

        def mapped_load(path: Path):
            value = original_load(path)
            if Path(path).resolve() == state_path.resolve():
                value = copy.deepcopy(value)
                value.update({
                    "status": "FAILED_CLOSED",
                    "stage": "FAILED",
                    "error": TECHNICAL_ERROR,
                })
            return value

        alg.load = mapped_load
        try:
            evidence = predecessor_validate(source)
        finally:
            alg.load = original_load
        evidence["technical_boundary_stop"] = {
            "committed_outer": 9,
            "discarded_partial_outer": 10,
            "discarded_physical_measurements": 0,
            "discarded_reset_bindings": 0,
            "accepted_or_rejected_credit": 0,
            "old_controller_pid_absent": True,
            "reason": "replace serialized Docker reset with full-parallel successor",
        }
        return evidence

    predecessor_state_document = impl["state_document"]

    def state_document(run_id: str) -> dict[str, Any]:
        value = predecessor_state_document(run_id)
        value["technical_outer9_parallel_reset_transition"] = {
            "source_run_root": str(SOURCE),
            "committed_outer_reused_through": 9,
            "partial_outer_010_measurements_reused": 0,
            "partial_outer_010_reset_reused": False,
            "accepted_or_rejected_credit_for_partial_outer": 0,
            "docker_create_start_lock": False,
            "worker_reset_parallelism": 8,
        }
        return value

    impl["validate_resume_source"] = validate_resume_source
    impl["state_document"] = state_document
    return core


def main() -> int:
    return int(install_runtime()._implementation_main())


if __name__ == "__main__":
    raise SystemExit(main())
