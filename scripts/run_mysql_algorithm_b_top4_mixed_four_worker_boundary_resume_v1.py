#!/usr/bin/env python3
"""Finalize a two-to-four lane B continuation without replaying evidence."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_b_top4_boundary_resume_v1 as base
from scripts import mysql_cross_project_wave_coordination_v14 as gate_v14


def main() -> int:
    core = base.install_runtime()
    impl = core._impl
    alg = impl["alg"]
    original_state_document = impl["state_document"]
    original_configure_runtime = impl["configure_runtime"]

    # The old two-lane baseline is retained as provenance only.  A fresh
    # four-lane baseline is measured once, while completed calibration points,
    # candidates, decisions, and accepted/rejected counts remain preserved.
    impl["REUSE_VERIFIED_BASELINE"] = False

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
            "fresh_four_lane_baseline_required": True,
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
