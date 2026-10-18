#!/usr/bin/env python3
"""Publish v41/v47 with reset-before-liveness XP candidate selection."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OLD_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260815_v40/optimization_contract.json"
NEW_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260815_v41/optimization_contract.json"
OLD_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260815_v46/coordination_contract.json"
NEW_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260815_v47/coordination_contract.json"
FAILED_ROOT = ROOT / "history/mysql_four_scene_20260809/v40_formal_outer10_rank3_resume_20260815T1304p0800"
FAILED_STATE = FAILED_ROOT / "run_state.json"
BOUNDARY_TEST = ROOT / "tests/test_mysql_algorithm_c_boundary_technical_isolation.py"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


def bind(bindings: dict, label: str, path: Path) -> None:
    bindings[f"{label}_path"] = rel(path)
    bindings[f"{label}_sha256"] = sha(path)


def rebind(bindings: dict) -> None:
    for key, value in list(bindings.items()):
        sha_key = key[:-5] + "_sha256" if key.endswith("_path") else None
        if sha_key is None or sha_key not in bindings:
            continue
        path = (ROOT / str(value)).resolve()
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"runtime release binding differs: {path}")
        bindings[sha_key] = sha(path)


def write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=False)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii"
    )


def main() -> int:
    state = json.loads(FAILED_STATE.read_text(encoding="utf-8"))
    if (
        state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or state.get("outer_attempt") != 9
        or state.get("error")
        != "RuntimeError: hybench_xp_cpu has no live MySQL lane for pilot"
        or (FAILED_ROOT / "outer_010/top5_batch_3/batch_result.json").exists()
        or (FAILED_ROOT / "xp_resets").exists()
    ):
        raise RuntimeError("pre-reset XP selection failure evidence differs")

    policy = {
        "formal_resume_rank": 3,
        "xp_worker_selection": "deterministic_frozen_w1_reset_target",
        "xp_sql_liveness_before_reset_required": False,
        "exact_identity_and_session_drain_still_required_in_reset": True,
        "job_tpch_live_sql_lane_still_required": True,
        "failed_rank_credit": 0,
    }
    coordination = json.loads(OLD_COORD.read_text(encoding="utf-8"))
    coordination["predecessor"] = {"path": rel(OLD_COORD), "sha256": sha(OLD_COORD)}
    coordination["successor_reason"] = (
        "select the frozen XP reset target before SQL liveness because the "
        "predecessor may be the dead data plane that reset must replace"
    )
    coordination["pre_reset_xp_candidate_selection"] = policy
    rebind(coordination["source_bindings"])
    bind(coordination["source_bindings"], "pre_reset_xp_selection_release_builder", Path(__file__).resolve())
    bind(coordination["source_bindings"], "pre_reset_xp_selection_failure_state", FAILED_STATE)
    bind(coordination["source_bindings"], "pre_reset_xp_selection_test", BOUNDARY_TEST)
    write(NEW_COORD, coordination)

    optimization = json.loads(OLD_OPT.read_text(encoding="utf-8"))
    optimization["schema_version"] = "mysql_algorithm_c_outer_runtime_optimization_v41"
    optimization["predecessor"] = {"path": rel(OLD_OPT), "sha256": sha(OLD_OPT)}
    optimization["successor_reason"] = (
        "resume Outer 10 rank 3 by selecting the exact XP reset target before "
        "requiring post-reset MySQL readiness"
    )
    optimization["pre_reset_xp_candidate_selection"] = policy
    optimization["co_resident_isolated_pg_allowance"]["coordination_contract_path"] = rel(NEW_COORD)
    optimization["co_resident_isolated_pg_allowance"]["coordination_contract_sha256"] = sha(NEW_COORD)
    bindings = optimization["bindings"]
    bind(bindings, "pre_reset_xp_selection_release_builder", Path(__file__).resolve())
    bind(bindings, "pre_reset_xp_selection_failure_state", FAILED_STATE)
    bind(bindings, "pre_reset_xp_selection_test", BOUNDARY_TEST)
    rebind(bindings)
    write(NEW_OPT, optimization)
    print(json.dumps({
        "coordination_sha256": sha(NEW_COORD),
        "optimization_sha256": sha(NEW_OPT),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
