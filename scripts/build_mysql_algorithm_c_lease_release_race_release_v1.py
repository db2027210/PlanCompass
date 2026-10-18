#!/usr/bin/env python3
"""Publish v34/v40 after the completed-rank lease-release watchdog race."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OLD_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260815_v33/optimization_contract.json"
NEW_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260815_v34/optimization_contract.json"
OLD_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260815_v39/coordination_contract.json"
NEW_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260815_v40/coordination_contract.json"
FAILED_ROOT = ROOT / "history/mysql_four_scene_20260809/v33_formal_outer9_resume_20260815T0853p0800"
FAILED_STATE = FAILED_ROOT / "run_state.json"
WATCHDOG = FAILED_ROOT / "runtime_external_interference_watchdog.jsonl"
BATCH_1 = FAILED_ROOT / "outer_009/top5_batch_1/batch_result.json"
BATCH_2 = FAILED_ROOT / "outer_009/top5_batch_2/batch_result.json"


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
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def main() -> int:
    state = json.loads(FAILED_STATE.read_text(encoding="utf-8"))
    events = [
        json.loads(line) for line in WATCHDOG.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    losses = [
        row for row in events
        if row.get("event") == "cross_project_workload_lease_lost_during_formal_wave"
    ]
    batches = [json.loads(path.read_text(encoding="utf-8")) for path in (BATCH_1, BATCH_2)]
    if (
        state.get("status") != "FAILED_CLOSED"
        or state.get("error") != "KeyboardInterrupt: "
        or state.get("accepted_rounds")
        != {"hybench_xp_cpu": 3, "hybench_xp_io": 2, "job_memory": 4, "tpch_sf4_memory": 4}
        or len(losses) != 1
        or losses[0].get("runtime_stage") != "OUTER_009_BATCH_2_PLAN_CAPTURE"
        or losses[0].get("coordination_phase") != "measuring"
        or losses[0].get("lease_loss_confirmed_after_fresh_state_reread") is True
        or events[-1].get("event") != "formal_failed_closed_after_sigint"
        or any(batch.get("status") != "PASS" or batch.get("failures") for batch in batches)
        or any(
            row.get("hard_gate_passed") is not True or row.get("coverage_complete") is not True
            for batch in batches for row in (batch.get("measurements") or {}).values()
        )
        or any(
            (FAILED_ROOT / f"outer_009/top5_batch_{rank}/batch_result.json").exists()
            for rank in (3, 4, 5)
        )
    ):
        raise RuntimeError("completed-rank release race evidence differs")

    coordination = json.loads(OLD_COORD.read_text(encoding="utf-8"))
    coordination["predecessor"] = {"path": rel(OLD_COORD), "sha256": sha(OLD_COORD)}
    coordination["successor_reason"] = (
        "confirm a missing flock against a fresh run-state snapshot so the published "
        "measuring-to-released transition cannot be misclassified as lease loss"
    )
    coordination["watchdog_lease_release_toctou"] = {
        "fresh_state_reread_required_after_missing_lock": True,
        "second_lock_read_required_when_fresh_phase_is_held": True,
        "fresh_released_phase_action": "continue_without_interrupt",
        "true_held_phase_missing_lock_action": "fail_closed",
        "completed_rank_reuse": [1, 2],
        "uncompleted_rank_credit": 0,
    }
    rebind(coordination["source_bindings"])
    bind(coordination["source_bindings"], "lease_release_race_builder", Path(__file__).resolve())
    bind(coordination["source_bindings"], "lease_release_race_watchdog", WATCHDOG)
    bind(coordination["source_bindings"], "lease_release_race_state", FAILED_STATE)
    write(NEW_COORD, coordination)

    optimization = json.loads(OLD_OPT.read_text(encoding="utf-8"))
    optimization["schema_version"] = "mysql_algorithm_c_outer_runtime_optimization_v34"
    optimization["predecessor"] = {"path": rel(OLD_OPT), "sha256": sha(OLD_OPT)}
    optimization["successor_reason"] = (
        "resume Outer 9 at rank 3 after SHA-valid rank 1/2 PASS evidence and prevent "
        "the lease-release watchdog TOCTOU; Algorithm C decisions remain unchanged"
    )
    optimization["co_resident_isolated_pg_allowance"]["coordination_contract_path"] = rel(NEW_COORD)
    optimization["co_resident_isolated_pg_allowance"]["coordination_contract_sha256"] = sha(NEW_COORD)
    bindings = optimization["bindings"]
    bind(bindings, "lease_release_race_builder", Path(__file__).resolve())
    bind(bindings, "lease_release_race_watchdog", WATCHDOG)
    bind(bindings, "lease_release_race_state", FAILED_STATE)
    bind(bindings, "lease_release_race_batch_1", BATCH_1)
    bind(bindings, "lease_release_race_batch_2", BATCH_2)
    rebind(bindings)
    write(NEW_OPT, optimization)
    print(json.dumps({
        "coordination_sha256": sha(NEW_COORD),
        "optimization_sha256": sha(NEW_OPT),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
