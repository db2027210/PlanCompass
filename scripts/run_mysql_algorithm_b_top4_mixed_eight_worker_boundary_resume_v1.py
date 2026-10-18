#!/usr/bin/env python3
"""Resume one XP Algorithm-B scene at the four-to-eight-worker boundary."""

from __future__ import annotations

import argparse
import copy
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v17 as gate_v17
from scripts import run_mysql_algorithm_b_top4_boundary_resume_v2 as boundary
from scripts import run_mysql_algorithm_b_xp_top4_mixed_eight_worker_v1 as runtime8


HISTORY = ROOT / "history/mysql_four_scene_20260809"
CONTRACT_ROOT = ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_b_mixed_eight_worker_20260827_v1"
EXPECTED_ERROR = "EightWorkerBoundaryExpansion: incomplete physical wave discarded"
CONFIG = {
    "hybench_xp_cpu": {
        "source": HISTORY / "v512_to_b_cpu_mixed_four_worker_boundary_resume_v72_20260827_xp_cpu",
        "accepted": 4, "rejected": 4, "outer": 8,
        "decision_roots": {},
    },
    "hybench_xp_io": {
        "source": HISTORY / "v521_to_b_io_mixed_four_worker_boundary_resume_v73_20260827_xp_io",
        "accepted": 2, "rejected": 3, "outer": 5,
        "decision_roots": {
            1: HISTORY / "v505_to_b_io_mixed_four_worker_boundary_resume_v71_20260827_xp_io"
        },
    },
}


def install(scene: str) -> Any:
    cfg = CONFIG[scene]
    boundary.SOURCE = cfg["source"]
    boundary.SCENE = scene
    boundary.EXPECTED_ERROR = EXPECTED_ERROR
    boundary.EXPECTED_ACCEPTED = int(cfg["accepted"])
    boundary.EXPECTED_REJECTED = int(cfg["rejected"])
    boundary.CALIBRATION_COMPLETED = 15
    boundary.COMMITTED_OUTER = int(cfg["outer"])
    boundary.PARTIAL_OUTER = 0
    boundary.PARTIAL_RANKS = ()
    boundary.COMMITTED_DECISION_ROOTS = dict(cfg["decision_roots"])
    boundary.VERIFIED_BASELINE_ROOT = cfg["source"]
    boundary.EXPECTED_BASELINE_WORKERS = 4
    boundary.RUNTIME_PREDECESSOR = runtime8
    boundary.CONTRACT_ROOT = CONTRACT_ROOT
    boundary.CONTRACT = CONTRACT_ROOT / "algorithm_b_top4_contract.json"
    boundary.COORDINATION = CONTRACT_ROOT / "coordination_contract.json"
    core = boundary.install_runtime()
    impl = core._impl
    alg = impl["alg"]
    original_configure = impl["configure_runtime"]
    original_state_document = impl["state_document"]
    original_validate_resume = impl["validate_resume_source"]

    def validate_resume_source(source: Path) -> dict[str, Any]:
        evidence = original_validate_resume(source)
        rows = copy.deepcopy(dict(evidence["baseline_rows"]))
        prefix = "xp_cpu" if scene == "hybench_xp_cpu" else "xp_io"
        if set(rows) != {f"{prefix}_w{i}" for i in range(1, 5)}:
            raise RuntimeError(f"{scene} verified four-lane baseline differs")
        for target_index, source_index in zip(range(5, 9), range(1, 5)):
            source_id = f"{prefix}_w{source_index}"
            target_id = f"{prefix}_w{target_index}"
            clone = copy.deepcopy(rows[source_id])
            clone["historical_baseline_lane_alias"] = True
            clone["historical_baseline_source_worker_id"] = source_id
            clone["worker_id"] = target_id
            rows[target_id] = clone
        evidence["baseline_rows"] = rows
        evidence["eight_lane_baseline_accounting"] = {
            "physical_baseline_measurements": 4,
            "analytical_lane_aliases": 4,
            "new_baseline_measurements": 0,
            "baseline_mean_unchanged": True,
            "accepted_or_outer_credit": 0,
        }
        return evidence

    def configure_runtime():
        contract = original_configure()
        alg.r0mod.dynamic_gate = gate_v17
        impl["no_lease_runtime"].coordination = gate_v17
        return contract

    def state_document(run_id: str) -> dict[str, Any]:
        value = original_state_document(run_id)
        value["mixed_eight_worker_boundary_expansion"] = {
            "source_worker_parallelism": 4,
            "target_worker_parallelism": 8,
            "discarded_incomplete_physical_wave_only": True,
            "completed_candidate_measurements_replayed": False,
            "committed_outer_decisions_replayed": False,
            "accepted_round_history_preserved": True,
            "candidate_batches": [[1, 2, 3, 4, 5]],
            "scene_local_boundary": True,
            "sibling_scene_wait": False,
            "resource_freeze_path": str(impl["FREEZE"]),
            "resource_freeze_sha256": alg.sha(impl["FREEZE"]),
        }
        return value

    impl["configure_runtime"] = configure_runtime
    impl["state_document"] = state_document
    impl["validate_resume_source"] = validate_resume_source
    return core


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--scene", choices=sorted(CONFIG), required=True)
    args, _ = parser.parse_known_args()
    return int(install(args.scene)._implementation_main())


if __name__ == "__main__":
    raise SystemExit(main())
