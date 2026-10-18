#!/usr/bin/env python3
"""Publish v46/v51 for fixed-Top5 boundary candidate supplementation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OLD_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260815_v45/optimization_contract.json"
NEW_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260815_v46/optimization_contract.json"
OLD_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260815_v50/coordination_contract.json"
NEW_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260815_v51/coordination_contract.json"
CONTROLLER = ROOT / "scripts/run_mysql_four_scene_algorithm_c_boundary_resume_v2.py"
ALGORITHM = ROOT / "scripts/run_mysql_four_scene_algorithm_c_v2.py"
LAUNCHER = ROOT / "scripts/launch_mysql_algorithm_c_boundary_resume_v2.py"
WAITER = ROOT / "scripts/wait_and_launch_mysql_algorithm_c_v42_rank4_v1.py"
COORDINATION = ROOT / "scripts/mysql_cross_project_wave_coordination_v1.py"
TEST = ROOT / "tests/test_mysql_algorithm_c_boundary_technical_isolation.py"
SUPPLEMENT_TEST = ROOT / "tests/test_mysql_algorithm_c_fixed_top5_supplementation.py"
DIAGNOSTIC_SOURCE = ROOT / "scripts/diagnose_mysql_algorithm_c_top5_shortage_v1.py"
DIAGNOSTIC = ROOT / "history/mysql_four_scene_20260809/algorithm_c_top5_supplementation_diagnostic_v5_20260815T2326p0800/diagnostic.json"
CHECKPOINT_BUILDER = ROOT / "scripts/build_mysql_algorithm_c_outer_checkpoint_v5.py"
CHECKPOINT = ROOT / "history/mysql_four_scene_20260809/algorithm_c_v45_outer10_checkpoint_v12_20260815T2253p0800/prepared_binding.json"
QUARANTINE_BUILDER = ROOT / "scripts/build_mysql_algorithm_c_empty_replay_quarantine_successor_v1.py"
QUARANTINE = ROOT / "history/mysql_four_scene_20260809/algorithm_c_v46_replay_quarantine_v9_outer10_v12_20260815T2330p0800/replay_quarantine.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


def bind(bindings: dict, label: str, path: Path) -> None:
    bindings[f"{label}_path"] = rel(path)
    bindings[f"{label}_sha256"] = sha(path)


def rebind(bindings: dict) -> None:
    for key, value in list(bindings.items()):
        if not key.endswith("_path"):
            continue
        digest_key = key[:-5] + "_sha256"
        if digest_key not in bindings:
            continue
        path = (ROOT / str(value)).resolve()
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"runtime release binding differs: {path}")
        bindings[digest_key] = sha(path)


def write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=False)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii"
    )


def main() -> int:
    if NEW_COORD.exists() or NEW_OPT.exists():
        raise RuntimeError("fresh v46/v51 release directories required")

    coordination = json.loads(OLD_COORD.read_text(encoding="utf-8"))
    coordination["predecessor"] = {
        "path": rel(OLD_COORD), "sha256": sha(OLD_COORD),
    }
    coordination["successor_reason"] = (
        "preserve fixed Top-5 when narrowed intervals collapse multiple subsets "
        "to fewer than five unique optimizer points"
    )
    coordination["coordination_signature_repair"] = {
        "workload_semantics_changed": False,
        "resource_envelope_changed": False,
        "candidate_schedule_changed": True,
        "candidate_rank_schedule_unchanged": [1, 2, 3, 4, 5],
        "candidate_scoring_changed": False,
        "hard_pruning_changed": False,
        "binary_interval_shrink_changed": False,
        "accepted_state_credit_increment": 0,
        "rejected_outer_attempt_increment": 0,
        "resume_outer_attempt": 10,
        "next_outer_attempt": 11,
        "resume_rank": 1,
    }
    rebind(coordination["source_bindings"])
    bind(coordination["source_bindings"], "coordination_source", COORDINATION)
    bind(coordination["source_bindings"], "boundary_resume_controller", CONTROLLER)
    bind(coordination["source_bindings"], "algorithm_source", ALGORITHM)
    bind(coordination["source_bindings"], "top5_supplementation_diagnostic", DIAGNOSTIC)
    bind(coordination["source_bindings"], "top5_supplementation_test", SUPPLEMENT_TEST)
    bind(coordination["source_bindings"], "coordination_signature_release_builder", Path(__file__).resolve())
    write(NEW_COORD, coordination)

    optimization = json.loads(OLD_OPT.read_text(encoding="utf-8"))
    optimization["schema_version"] = "mysql_algorithm_c_outer_runtime_optimization_v46"
    optimization["predecessor"] = {
        "path": rel(OLD_OPT), "sha256": sha(OLD_OPT),
    }
    optimization["successor_reason"] = (
        "supplement rounded or boundary-collapsed candidate pools with audited "
        "in-range points while retaining fixed Top-5 and original scoring"
    )
    optimization["coordination_signature_repair"] = dict(
        coordination["coordination_signature_repair"]
    )
    allowance = optimization["co_resident_isolated_pg_allowance"]
    allowance["coordination_contract_path"] = rel(NEW_COORD)
    allowance["coordination_contract_sha256"] = sha(NEW_COORD)
    bindings = optimization["bindings"]
    rebind(bindings)
    bind(bindings, "algorithm_source", ALGORITHM)
    bind(bindings, "boundary_resume_controller", CONTROLLER)
    bind(bindings, "boundary_launch_source", LAUNCHER)
    bind(bindings, "rank4_auto_wait_source", WAITER)
    bind(bindings, "boundary_technical_isolation_test", TEST)
    bind(bindings, "fixed_top5_supplementation_test", SUPPLEMENT_TEST)
    bind(bindings, "top5_supplementation_diagnostic_source", DIAGNOSTIC_SOURCE)
    bind(bindings, "top5_supplementation_diagnostic", DIAGNOSTIC)
    bind(bindings, "outer10_checkpoint_builder", CHECKPOINT_BUILDER)
    bind(bindings, "outer10_checkpoint", CHECKPOINT)
    bind(bindings, "outer10_replay_quarantine_builder", QUARANTINE_BUILDER)
    bind(bindings, "outer10_replay_quarantine", QUARANTINE)
    bind(bindings, "wave_coordination_contract", NEW_COORD)
    bind(bindings, "coordination_signature_release_builder", Path(__file__).resolve())
    optimization["optimized_plan_ranking"].update({
        "candidate_generation_unchanged": False,
        "boundary_collapsed_in_range_supplementation": True,
        "gradient_direction_preserved_unless_rounded_to_incumbent": True,
        "inward_fallback_only_for_boundary_collapsed_parameters": True,
        "duplicate_parameter_points_explained_once": True,
        "candidate_scoring_unchanged": True,
        "fixed_top_k_unchanged": True,
        "hard_plan_pruning_unchanged": True,
        "qps_used_for_generation_resource_prediction_gradient_or_score": False,
        "real_replay_unchanged": True,
    })
    write(NEW_OPT, optimization)

    print(json.dumps({
        "coordination_contract": str(NEW_COORD),
        "coordination_sha256": sha(NEW_COORD),
        "optimization_contract": str(NEW_OPT),
        "optimization_sha256": sha(NEW_OPT),
        "controller_sha256": sha(CONTROLLER),
        "launcher_sha256": sha(LAUNCHER),
        "waiter_sha256": sha(WAITER),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
