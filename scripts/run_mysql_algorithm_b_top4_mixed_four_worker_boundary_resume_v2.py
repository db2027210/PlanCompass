#!/usr/bin/env python3
"""Finalize four-lane B continuation and reuse a verified four-lane baseline."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v14 as gate_v14
from scripts import run_mysql_algorithm_b_top4_boundary_resume_v2 as base


def main() -> int:
    core = base.install_runtime()
    impl = core._impl
    alg = impl["alg"]
    original_state_document = impl["state_document"]
    original_configure_runtime = impl["configure_runtime"]
    reuse_four_lane_baseline = base.VERIFIED_BASELINE_ROOT is not None
    impl["REUSE_VERIFIED_BASELINE"] = reuse_four_lane_baseline

    def configure_runtime():
        contract = original_configure_runtime()
        alg.r0mod.dynamic_gate = gate_v14
        impl["no_lease_runtime"].coordination = gate_v14
        return contract

    def state_document(run_id: str) -> dict[str, Any]:
        value = original_state_document(run_id)
        value["mixed_four_worker_boundary_expansion"] = {
            "source_worker_parallelism": 2,
            "target_worker_parallelism": 4,
            "fresh_four_lane_baseline_required": not reuse_four_lane_baseline,
            "verified_four_lane_baseline_reused": reuse_four_lane_baseline,
            "verified_four_lane_baseline_root": (
                str(base.VERIFIED_BASELINE_ROOT)
                if base.VERIFIED_BASELINE_ROOT is not None else None
            ),
            "completed_calibration_measurements_replayed": False,
            "completed_candidate_measurements_replayed": False,
            "committed_outer_decisions_replayed": False,
            "accepted_round_history_preserved": True,
            "candidate_batches": [[1, 2, 3, 4], [5]],
            "scene_local_completed_boundary": True,
            "sibling_scene_wait": False,
            "resource_freeze_path": str(impl["FREEZE"]),
            "resource_freeze_sha256": alg.sha(impl["FREEZE"]),
        }
        return value

    impl["state_document"] = state_document
    impl["configure_runtime"] = configure_runtime
    return int(core._implementation_main())


if __name__ == "__main__":
    raise SystemExit(main())
