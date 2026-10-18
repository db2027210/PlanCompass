#!/usr/bin/env python3
"""Publish pruning-v2 optimization and coordination contract successors."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OLD_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260812_v1/optimization_contract.json"
NEW_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260813_v2/optimization_contract.json"
OLD_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260813_v4/coordination_contract.json"
NEW_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260813_v5/coordination_contract.json"
PRUNING = ROOT / "config/formal/mysql_algorithm_c_plan_replay_pruning_20260813_v2/contract.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def bind(bindings: dict, label: str, path: Path) -> None:
    bindings[f"{label}_path"] = str(path.relative_to(ROOT))
    bindings[f"{label}_sha256"] = sha(path)


def write(path: Path, value: dict) -> None:
    if path.exists():
        raise SystemExit(f"refusing to replace frozen successor: {path}")
    path.parent.mkdir(parents=True, exist_ok=False)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    path.with_name(path.name + ".sha256").write_text(f"{sha(path)}  {path.name}\n")


def main() -> int:
    opt = load(OLD_OPT)
    opt["schema_version"] = "mysql_algorithm_c_outer_runtime_optimization_v2"
    opt["predecessor"] = {"path": str(OLD_OPT.relative_to(ROOT)), "sha256": sha(OLD_OPT)}
    opt["successor_reason"] = "full_plan_cross_round_qps_decline_pruning_v2"
    opt["plan_replay_pruning_v2"] = {
        "contract_path": str(PRUNING.relative_to(ROOT)), "contract_sha256": sha(PRUNING),
        "single_reject": "soft_ranking_penalty_only",
        "hard_prune_requires_distinct_rounds": 2,
        "hard_prune_requires_distinct_parameter_points": 2,
        "accepted_counterexample_prohibits_pruning": True,
        "acceptance_tier_insufficiency_learnable": False,
        "qps_used_for_resource_prediction_gradient_or_candidate_score": False,
    }
    bindings = opt["bindings"]
    bind(bindings, "resource_freeze", ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260813_v18/four_scene_effective_resource_successor.json")
    bind(bindings, "reset_source", ROOT / "scripts/reset_mysql_xp_data_planes_v5.py")
    bind(bindings, "algorithm_source", ROOT / "scripts/run_mysql_four_scene_algorithm_c_v2.py")
    bind(bindings, "boundary_resume_controller", ROOT / "scripts/run_mysql_four_scene_algorithm_c_boundary_resume_v2.py")
    bind(bindings, "boundary_launch_source", ROOT / "scripts/launch_mysql_algorithm_c_boundary_resume_v2.py")
    bind(bindings, "plan_replay_pruning_source", ROOT / "mysql_algorithm_c_plan_replay_pruning.py")
    bind(bindings, "plan_replay_pruning_contract", PRUNING)
    write(NEW_OPT, opt)

    coord = load(OLD_COORD)
    coord["predecessor"] = {"path": str(OLD_COORD.relative_to(ROOT)), "sha256": sha(OLD_COORD)}
    coord["successor_reason"] = "bind_pruning_v2_boundary_controller_without_scheduling_change"
    coord["pruning_contract"] = {"path": str(PRUNING.relative_to(ROOT)), "sha256": sha(PRUNING)}
    sources = coord["source_bindings"]
    bind(sources, "coordination_source", ROOT / "scripts/mysql_cross_project_wave_coordination_v1.py")
    bind(sources, "boundary_resume_controller", ROOT / "scripts/run_mysql_four_scene_algorithm_c_boundary_resume_v2.py")
    write(NEW_COORD, coord)
    print(json.dumps({"optimization": {"path": str(NEW_OPT), "sha256": sha(NEW_OPT)}, "coordination": {"path": str(NEW_COORD), "sha256": sha(NEW_COORD)}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
