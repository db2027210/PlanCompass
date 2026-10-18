#!/usr/bin/env python3
"""Publish v40/v46 with exact dead-mysqld XP reset recovery."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OLD_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260815_v39/optimization_contract.json"
NEW_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260815_v40/optimization_contract.json"
OLD_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260815_v45/coordination_contract.json"
NEW_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260815_v46/coordination_contract.json"
FAILED_ROOT = ROOT / "history/mysql_four_scene_20260809/v39_formal_outer10_rank3_resume_20260815T1247p0800"
FAILED_STATE = FAILED_ROOT / "run_state.json"
EMPTY_RESET_ROOT = FAILED_ROOT / "xp_resets/outer_010_batch_3"
ALGORITHM = ROOT / "scripts/run_mysql_four_scene_algorithm_c_v2.py"
SESSION_TEST = ROOT / "tests/test_mysql_algorithm_c_interval_convergence.py"


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
    expected_prefix = (
        "RuntimeError: XP active client sessions were not proven drained before "
        "the 60s bound:"
    )
    if (
        state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or state.get("outer_attempt") != 9
        or not str(state.get("error") or "").startswith(expected_prefix)
        or (FAILED_ROOT / "outer_010/top5_batch_3/batch_result.json").exists()
        or not EMPTY_RESET_ROOT.is_dir()
        or list(EMPTY_RESET_ROOT.rglob("*"))
    ):
        raise RuntimeError("dead-mysqld pre-reset failure evidence differs")

    coordination = json.loads(OLD_COORD.read_text(encoding="utf-8"))
    coordination["predecessor"] = {"path": rel(OLD_COORD), "sha256": sha(OLD_COORD)}
    coordination["successor_reason"] = (
        "recover an exact allowlisted XP predecessor only when two stable Docker "
        "identity reads and its process table prove no live mysqld"
    )
    coordination["dead_mysqld_predecessor_recovery"] = {
        "formal_resume_rank": 3,
        "sql_probe_timeout_alone_never_sufficient": True,
        "exact_container_name_id_and_labels_required": True,
        "docker_identity_reread_required": True,
        "live_or_d_state_mysqld_remains_fail_closed": True,
        "only_absent_or_zombie_mysqld_is_discardable": True,
        "predecessor_datadir_discard_only": True,
        "failed_rank_credit": 0,
    }
    rebind(coordination["source_bindings"])
    bind(coordination["source_bindings"], "dead_mysqld_reset_release_builder", Path(__file__).resolve())
    bind(coordination["source_bindings"], "dead_mysqld_failure_state", FAILED_STATE)
    bind(coordination["source_bindings"], "dead_mysqld_session_test", SESSION_TEST)
    write(NEW_COORD, coordination)

    optimization = json.loads(OLD_OPT.read_text(encoding="utf-8"))
    optimization["schema_version"] = "mysql_algorithm_c_outer_runtime_optimization_v40"
    optimization["predecessor"] = {"path": rel(OLD_OPT), "sha256": sha(OLD_OPT)}
    optimization["successor_reason"] = (
        "resume Outer 10 rank 3 after safely rebuilding only exact XP containers "
        "proven to have no live mysqld"
    )
    optimization["dead_mysqld_predecessor_recovery"] = coordination[
        "dead_mysqld_predecessor_recovery"
    ]
    optimization["co_resident_isolated_pg_allowance"]["coordination_contract_path"] = rel(NEW_COORD)
    optimization["co_resident_isolated_pg_allowance"]["coordination_contract_sha256"] = sha(NEW_COORD)
    bindings = optimization["bindings"]
    bind(bindings, "dead_mysqld_reset_release_builder", Path(__file__).resolve())
    bind(bindings, "dead_mysqld_failure_state", FAILED_STATE)
    bind(bindings, "dead_mysqld_session_test", SESSION_TEST)
    bind(bindings, "algorithm_source", ALGORITHM)
    rebind(bindings)
    write(NEW_OPT, optimization)
    print(json.dumps({
        "coordination_sha256": sha(NEW_COORD),
        "optimization_sha256": sha(NEW_OPT),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
