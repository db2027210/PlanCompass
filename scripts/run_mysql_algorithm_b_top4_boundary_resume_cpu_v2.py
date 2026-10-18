#!/usr/bin/env python3
"""Resume CPU Outer-1 rank 5 on healthy w2, then hand off at boundary."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_b_top4_boundary_resume_cpu_v1 as configuration  # noqa: F401
from scripts import run_mysql_algorithm_b_top4_boundary_resume_v1 as base

base.CONTRACT_ROOT = ROOT / "config/formal/frozen_mysql_four_scene_top4_algorithm_b_20260827_v65"
base.CONTRACT = base.CONTRACT_ROOT / "algorithm_b_top4_contract.json"
base.COORDINATION = base.CONTRACT_ROOT / "coordination_contract.json"


def main() -> int:
    core = base.install_runtime()
    impl = core._impl
    original_install_scene = impl["install_scene"]

    def install_scene(scene: str) -> None:
        original_install_scene(scene)
        runtime = impl["runtime"]
        original_assignments = runtime._build_single_candidate_assignments

        def assignments(**kwargs: Any):
            mapped, companions = original_assignments(**kwargs)
            rankings = kwargs["rankings"]
            rank = int(kwargs["rank"])
            calibration = bool(rankings) and all(
                rows and "calibration_id" in rows[0]
                for rows in rankings.values()
            )
            if not calibration and rank == 5 and "xp_cpu_w1" in mapped:
                if "xp_cpu_w2" in mapped:
                    raise RuntimeError("CPU rank-5 healthy-lane remap collision")
                mapped["xp_cpu_w2"] = mapped.pop("xp_cpu_w1")
                companions.discard("xp_cpu_w1")
                companions.discard("xp_cpu_w2")
            return mapped, companions

        runtime._build_single_candidate_assignments = assignments

    impl["install_scene"] = install_scene
    original_state_document = impl["state_document"]

    def state_document(run_id: str) -> dict[str, Any]:
        value = original_state_document(run_id)
        value["cpu_rank5_healthy_lane_release"] = {
            "failed_lane": "xp_cpu_w1",
            "failed_lane_container_state": "docker_init_with_zombie_mysqld",
            "replacement_lane_for_partial_outer_rank5": "xp_cpu_w2",
            "completed_ranks_replayed": False,
            "handoff_required_after_outer1_boundary": True,
        }
        return value

    impl["state_document"] = state_document
    return int(core._implementation_main())


if __name__ == "__main__":
    raise SystemExit(main())
