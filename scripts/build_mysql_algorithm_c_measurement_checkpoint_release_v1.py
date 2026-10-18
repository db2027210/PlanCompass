#!/usr/bin/env python3
"""Publish v38/v44 with measurement-boundary checkpoint recovery."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OLD_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260815_v37/optimization_contract.json"
NEW_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260815_v38/optimization_contract.json"
OLD_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260815_v43/coordination_contract.json"
NEW_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260815_v44/coordination_contract.json"
CHECKPOINT_BUILDER = ROOT / "scripts/build_mysql_algorithm_c_outer_checkpoint_v4.py"
FAILED_ROOT = ROOT / "history/mysql_four_scene_20260809/v36_formal_outer9_rank5_resume_20260815T1132p0800"
FAILED_STATE = FAILED_ROOT / "run_state.json"


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
    interruption = state.get("watchdog_interruption") or {}
    if (
        state.get("outer_attempt") != 9
        or interruption.get("runtime_stage") != "OUTER_010_BATCH_3_MEASURING"
        or interruption.get("formal_state_credit_increment") != 0
        or interruption.get("interrupted_candidate_replay_prohibited") is not True
    ):
        raise RuntimeError("measurement checkpoint failure evidence differs")

    coordination = json.loads(OLD_COORD.read_text(encoding="utf-8"))
    coordination["predecessor"] = {"path": rel(OLD_COORD), "sha256": sha(OLD_COORD)}
    coordination["successor_reason"] = (
        "bind clean in-measurement watchdog recovery to an Outer 9 checkpoint; "
        "interrupted rank receives zero credit and is replayed"
    )
    coordination["measurement_checkpoint_recovery"] = {
        "builder_path": rel(CHECKPOINT_BUILDER),
        "builder_sha256": sha(CHECKPOINT_BUILDER),
        "completed_outer_attempt": 9,
        "interrupted_outer_attempt": 10,
        "reusable_completed_ranks": [1, 2],
        "replayed_rank": 3,
        "interrupted_rank_credit": 0,
    }
    rebind(coordination["source_bindings"])
    bind(coordination["source_bindings"], "measurement_checkpoint_release_builder", Path(__file__).resolve())
    bind(coordination["source_bindings"], "measurement_checkpoint_builder", CHECKPOINT_BUILDER)
    bind(coordination["source_bindings"], "measurement_checkpoint_failure_state", FAILED_STATE)
    write(NEW_COORD, coordination)

    optimization = json.loads(OLD_OPT.read_text(encoding="utf-8"))
    optimization["schema_version"] = "mysql_algorithm_c_outer_runtime_optimization_v38"
    optimization["predecessor"] = {"path": rel(OLD_OPT), "sha256": sha(OLD_OPT)}
    optimization["successor_reason"] = (
        "resume Outer 10 rank 3 from a content-bound completed Outer 9 checkpoint"
    )
    optimization["co_resident_isolated_pg_allowance"]["coordination_contract_path"] = rel(NEW_COORD)
    optimization["co_resident_isolated_pg_allowance"]["coordination_contract_sha256"] = sha(NEW_COORD)
    bindings = optimization["bindings"]
    bind(bindings, "measurement_checkpoint_release_builder", Path(__file__).resolve())
    bind(bindings, "outer_checkpoint_builder", CHECKPOINT_BUILDER)
    bind(bindings, "measurement_checkpoint_failure_state", FAILED_STATE)
    rebind(bindings)
    write(NEW_OPT, optimization)
    print(json.dumps({
        "coordination_sha256": sha(NEW_COORD),
        "optimization_sha256": sha(NEW_OPT),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
