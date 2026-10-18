#!/usr/bin/env python3
"""Publish v42/v48 preserving a completed technical rank before retry."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OLD_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260815_v41/optimization_contract.json"
NEW_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260815_v42/optimization_contract.json"
OLD_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260815_v47/coordination_contract.json"
NEW_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260815_v48/coordination_contract.json"
FAILED_ROOT = ROOT / "history/mysql_four_scene_20260809/v41_formal_outer10_rank3_retry2_20260815T1314p0800"
FAILED_STATE = FAILED_ROOT / "run_state.json"
TECHNICAL_BATCH = FAILED_ROOT / "outer_010/top5_batch_3/batch_result.json"
EMPTY_NEXT_RESET = FAILED_ROOT / "xp_resets/outer_010_batch_4"
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
    batch = json.loads(TECHNICAL_BATCH.read_text(encoding="utf-8"))
    failures = batch.get("failures") or []
    measurements = batch.get("measurements") or {}
    if (
        state.get("status") != "FAILED_CLOSED"
        or state.get("outer_attempt") != 9
        or not str(state.get("error") or "").startswith(
            "RuntimeError: XP active client sessions were not proven drained before "
        )
        or batch.get("status") != "FAILED_CLOSED"
        or len(measurements) != 4
        or {row.get("rank") for row in measurements.values()} != {3}
        or any(
            row.get("hard_gate_passed") is not False
            or row.get("coverage_complete") is not False
            for row in measurements.values()
        )
        or not any(str(row.get("error") or "").startswith("TimeoutExpired:") for row in failures)
        or not EMPTY_NEXT_RESET.is_dir()
        or list(EMPTY_NEXT_RESET.rglob("*"))
    ):
        raise RuntimeError("technical rank continuation evidence differs")

    policy = {
        "formal_resume_rank": 4,
        "completed_technical_rank": 3,
        "technical_rank_batch_status": "FAILED_CLOSED",
        "technical_rank_replayed_without_rerun": True,
        "technical_rank_excluded_from_decisions": True,
        "accepted_state_credit_increment": 0,
        "rejected_outer_attempt_increment": 0,
        "tier_convergence_replay_and_pruning_mutation": False,
        "next_reset_mutation_before_failure": False,
    }
    coordination = json.loads(OLD_COORD.read_text(encoding="utf-8"))
    coordination["predecessor"] = {"path": rel(OLD_COORD), "sha256": sha(OLD_COORD)}
    coordination["successor_reason"] = (
        "preserve an immutable fully classified technical timeout batch and "
        "resume at the next rank after an empty pre-reset drain failure"
    )
    coordination["technical_rank_continuation"] = policy
    rebind(coordination["source_bindings"])
    bind(coordination["source_bindings"], "technical_rank_continuation_release_builder", Path(__file__).resolve())
    bind(coordination["source_bindings"], "technical_rank_failure_state", FAILED_STATE)
    bind(coordination["source_bindings"], "technical_rank_batch", TECHNICAL_BATCH)
    bind(coordination["source_bindings"], "technical_rank_continuation_test", BOUNDARY_TEST)
    write(NEW_COORD, coordination)

    optimization = json.loads(OLD_OPT.read_text(encoding="utf-8"))
    optimization["schema_version"] = "mysql_algorithm_c_outer_runtime_optimization_v42"
    optimization["predecessor"] = {"path": rel(OLD_OPT), "sha256": sha(OLD_OPT)}
    optimization["successor_reason"] = (
        "resume Outer 10 at rank 4 without rerunning the immutable rank 3 "
        "technical timeout wave"
    )
    optimization["technical_rank_continuation"] = policy
    optimization["co_resident_isolated_pg_allowance"]["coordination_contract_path"] = rel(NEW_COORD)
    optimization["co_resident_isolated_pg_allowance"]["coordination_contract_sha256"] = sha(NEW_COORD)
    bindings = optimization["bindings"]
    bind(bindings, "technical_rank_continuation_release_builder", Path(__file__).resolve())
    bind(bindings, "technical_rank_failure_state", FAILED_STATE)
    bind(bindings, "technical_rank_batch", TECHNICAL_BATCH)
    bind(bindings, "technical_rank_continuation_test", BOUNDARY_TEST)
    rebind(bindings)
    write(NEW_OPT, optimization)
    print(json.dumps({
        "coordination_sha256": sha(NEW_COORD),
        "optimization_sha256": sha(NEW_OPT),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
