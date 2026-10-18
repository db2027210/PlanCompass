#!/usr/bin/env python3
"""Publish v25/v32 for Outer-6 technical-timeout checkpoint recovery."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OLD_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260814_v24/optimization_contract.json"
NEW_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260814_v25/optimization_contract.json"
OLD_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260814_v31/coordination_contract.json"
NEW_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260814_v32/coordination_contract.json"
CHECKPOINT = ROOT / "history/mysql_four_scene_20260809/algorithm_c_v24_outer6_checkpoint_v8_20260814T2345p0800/prepared_binding.json"
QUARANTINE = ROOT / "history/mysql_four_scene_20260809/algorithm_c_v24_replay_quarantine_v6_outer6_v8_20260814T2348p0800/replay_quarantine.json"
RECOVERY = ROOT / "history/mysql_four_scene_20260809/xp_w1_post_outer6_rank5_timeout_recovery_v1_20260814T2338p0800/recovered_xp_binding.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


def rebind(bindings: dict) -> None:
    for key, value in list(bindings.items()):
        sha_key = key[:-5] + "_sha256" if key.endswith("_path") else None
        if sha_key is None or sha_key not in bindings:
            continue
        path = (ROOT / str(value)).resolve()
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"runtime release binding differs: {path}")
        bindings[sha_key] = sha(path)


def bind(bindings: dict, label: str, path: Path) -> None:
    bindings[f"{label}_path"] = rel(path)
    bindings[f"{label}_sha256"] = sha(path)


def write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=False)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def main() -> int:
    checkpoint = json.loads(CHECKPOINT.read_text(encoding="utf-8"))
    quarantine = json.loads(QUARANTINE.read_text(encoding="utf-8"))
    recovery = json.loads(RECOVERY.read_text(encoding="utf-8"))
    if (
        checkpoint.get("status") != "PREPARED"
        or checkpoint.get("completed_outer_attempts") != 6
        or quarantine.get("status") != "PASS"
        or quarantine.get("resume_checkpoint_outer_attempt") != 6
        or recovery.get("status") != "PASS"
        or recovery.get("accepted_state_mutation") is not False
    ):
        raise RuntimeError("Outer-6 timeout release evidence differs")

    coordination = json.loads(OLD_COORD.read_text(encoding="utf-8"))
    coordination["predecessor"] = {
        "path": rel(OLD_COORD), "sha256": sha(OLD_COORD),
    }
    coordination["successor_reason"] = (
        "bind technical-timeout checkpoint and XP w1 recovery; frozen PG "
        "cohort and dynamic isolation thresholds are unchanged"
    )
    rebind(coordination["source_bindings"])
    write(NEW_COORD, coordination)

    optimization = json.loads(OLD_OPT.read_text(encoding="utf-8"))
    optimization["schema_version"] = "mysql_algorithm_c_outer_runtime_optimization_v25"
    optimization["predecessor"] = {
        "path": rel(OLD_OPT), "sha256": sha(OLD_OPT),
    }
    optimization["successor_reason"] = (
        "exclude the timed-out rank-5 measurement without state credit, bind "
        "the completed Outer-6 decisions, and resume from a clean XP w1 reset"
    )
    optimization["co_resident_isolated_pg_allowance"][
        "coordination_contract_path"
    ] = rel(NEW_COORD)
    optimization["co_resident_isolated_pg_allowance"][
        "coordination_contract_sha256"
    ] = sha(NEW_COORD)
    optimization["outer6_technical_timeout_recovery"] = {
        "timed_out_rank": 5,
        "timed_out_rank_state_credit": 0,
        "completed_outer_decisions_preserved": True,
        "xp_w1_reset_from_sealed_templates": True,
        "xp_w2_mutation": False,
        "algorithm_acceptance_and_convergence_semantics_changed": False,
    }
    builder = Path(__file__).resolve()
    bindings = optimization["bindings"]
    bind(bindings, "outer6_timeout_release_builder", builder)
    bind(bindings, "outer6_checkpoint", CHECKPOINT)
    bind(bindings, "outer6_replay_quarantine", QUARANTINE)
    bind(bindings, "post_timeout_xp_w1_recovery", RECOVERY)
    bind(bindings, "post_timeout_xp_w1_recovery_source", ROOT / "scripts/recover_mysql_algorithm_c_post_timeout_xp_w1_v1.py")
    bind(bindings, "outer6_replay_quarantine_builder", ROOT / "scripts/build_mysql_algorithm_c_outer6_empty_replay_quarantine_v1.py")
    bind(bindings, "boundary_technical_isolation_test", ROOT / "tests/test_mysql_algorithm_c_boundary_technical_isolation.py")
    rebind(bindings)
    write(NEW_OPT, optimization)
    print(json.dumps({
        "coordination_sha256": sha(NEW_COORD),
        "optimization_sha256": sha(NEW_OPT),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
