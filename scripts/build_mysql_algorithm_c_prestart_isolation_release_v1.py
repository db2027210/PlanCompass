#!/usr/bin/env python3
"""Publish v36/v42 with one direct-isolation policy at prestart and runtime."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OLD_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260815_v35/optimization_contract.json"
NEW_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260815_v36/optimization_contract.json"
OLD_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260815_v41/coordination_contract.json"
NEW_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260815_v42/coordination_contract.json"
FAILED_ROOT = ROOT / "history/mysql_four_scene_20260809/v35_formal_outer9_rank5_resume_20260815T1128p0800"
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
    error = str(state.get("error") or "")
    if (
        state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or "fresh R0 point non-interference gate blocked" not in error
        or "active_benchmark_or_controller_cpu_overlap" not in error
        or "stmt_postgres.toml" not in error
        or state.get("accepted_rounds")
        != {"hybench_xp_cpu": 3, "hybench_xp_io": 2, "job_memory": 4, "tpch_sf4_memory": 4}
    ):
        raise RuntimeError("prestart direct-isolation mismatch evidence differs")

    coordination = json.loads(OLD_COORD.read_text(encoding="utf-8"))
    coordination["predecessor"] = {"path": rel(OLD_COORD), "sha256": sha(OLD_COORD)}
    coordination["successor_reason"] = (
        "apply the frozen direct co-resident PG isolation evaluator consistently at "
        "prestart, R0 full interval, and runtime with a three-second persistence check"
    )
    coordination["runtime_affinity_startup_confirmation"] = {
        "confirmation_seconds": 3.0,
        "same_pid_and_error_required": True,
        "transient_startup_mask_action": "continue_and_reinventory",
        "persistent_overlap_action": "fail_closed",
        "applies_at": ["prestart_point_gate", "r0_full_interval", "candidate_runtime"],
        "interrupted_rank_reuse": [1, 2, 3, 4],
        "interrupted_rank_credit": 0,
    }
    rebind(coordination["source_bindings"])
    bind(coordination["source_bindings"], "prestart_isolation_release_builder", Path(__file__).resolve())
    bind(coordination["source_bindings"], "prestart_isolation_failure_state", FAILED_STATE)
    write(NEW_COORD, coordination)

    optimization = json.loads(OLD_OPT.read_text(encoding="utf-8"))
    optimization["schema_version"] = "mysql_algorithm_c_outer_runtime_optimization_v36"
    optimization["predecessor"] = {"path": rel(OLD_OPT), "sha256": sha(OLD_OPT)}
    optimization["successor_reason"] = (
        "resume Outer 9 rank 5 with the same direct isolated-PG policy at every formal gate"
    )
    optimization["co_resident_isolated_pg_allowance"]["coordination_contract_path"] = rel(NEW_COORD)
    optimization["co_resident_isolated_pg_allowance"]["coordination_contract_sha256"] = sha(NEW_COORD)
    bindings = optimization["bindings"]
    bind(bindings, "prestart_isolation_release_builder", Path(__file__).resolve())
    bind(bindings, "prestart_isolation_failure_state", FAILED_STATE)
    bind(bindings, "r0_direct_isolation_source", ROOT / "scripts/run_mysql_four_scene_r0_v5.py")
    rebind(bindings)
    write(NEW_OPT, optimization)
    print(json.dumps({
        "coordination_sha256": sha(NEW_COORD),
        "optimization_sha256": sha(NEW_OPT),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
