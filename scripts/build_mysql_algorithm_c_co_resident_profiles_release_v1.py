#!/usr/bin/env python3
"""Publish v47/v52 for sealed CPU-disjoint co-resident PG profiles."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any

from scripts import mysql_cross_project_wave_coordination_v1 as coordination


ROOT = Path(__file__).resolve().parents[1]
OLD_COORD = (
    ROOT
    / "config/formal/frozen_mysql_cross_project_wave_coordination_20260815_v51"
    / "coordination_contract.json"
)
NEW_COORD = (
    ROOT
    / "config/formal/frozen_mysql_cross_project_wave_coordination_20260816_v52"
    / "coordination_contract.json"
)
OLD_OPT = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260815_v46"
    / "optimization_contract.json"
)
NEW_OPT = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260816_v47"
    / "optimization_contract.json"
)
FAILED_ROOT = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v46_formal_outer11_top5_supplement_20260815_attempt001"
)
FAILED_STATE = FAILED_ROOT / "run_state.json"
FAILED_WATCHDOG = FAILED_ROOT / "runtime_external_interference_watchdog.jsonl"
FAILED_GATE = (
    FAILED_ROOT / "outer_013"
    / "top5_batch_2_cross_project_wave_quiet_gate_attempt_001.json"
)
CONTAMINATED_RANK1 = FAILED_ROOT / "outer_013/top5_batch_1/batch_result.json"
CHECKPOINT = (
    ROOT / "history/mysql_four_scene_20260809"
    / "algorithm_c_v46_outer12_checkpoint_v13_20260816T0210p0800"
    / "prepared_binding.json"
)
QUARANTINE = (
    ROOT / "history/mysql_four_scene_20260809"
    / "algorithm_c_v47_replay_quarantine_v10_outer12_v13_20260816T1435p0800"
    / "replay_quarantine.json"
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT))


def load(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular release input required: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def bind(bindings: dict[str, Any], label: str, path: Path) -> None:
    bindings[f"{label}_path"] = rel(path)
    bindings[f"{label}_sha256"] = sha(path)


def rebind(bindings: dict[str, Any]) -> None:
    for key, value in list(bindings.items()):
        if not key.endswith("_path"):
            continue
        sha_key = key[:-5] + "_sha256"
        if sha_key not in bindings:
            continue
        path = (ROOT / str(value)).resolve()
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"bound release input differs: {path}")
        bindings[sha_key] = sha(path)


def write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=False, mode=0o700)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def verify_failure_boundary() -> None:
    state = load(FAILED_STATE)
    gate = load(FAILED_GATE)
    checkpoint = load(CHECKPOINT)
    quarantine = load(QUARANTINE)
    rows = [
        json.loads(line)
        for line in FAILED_WATCHDOG.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    rank1_mtime = datetime.fromtimestamp(
        CONTAMINATED_RANK1.stat().st_mtime
    ).astimezone()
    overlap = [
        row for row in rows
        if row.get("coordination_phase") == "measuring"
        and row.get("physical_workloads")
        and datetime.fromisoformat(str(row["captured_at"])) <= rank1_mtime
    ]
    if (
        state.get("status") != "FAILED_CLOSED"
        or state.get("outer_attempt") != 12
        or state.get("error")
        != "RuntimeError: external physical workload bypassed the exclusive wave lease"
        or gate.get("status") != "BLOCKED_FAIL_CLOSED"
        or gate.get("errors")
        != ["external_database_physical_workload_present_while_exclusive"]
        or not overlap
        or checkpoint.get("completed_outer_attempts") != 12
        or checkpoint.get("status") != "PREPARED"
        or quarantine.get("resume_checkpoint_outer_attempt") != 12
        or quarantine.get("formal_state_mutation") is not False
    ):
        raise RuntimeError("v47/v52 failure-boundary authority differs")


def seal_container_profiles(policy: dict[str, Any]) -> None:
    wide = {
        "ra_fullgrid_hybench_none_20260718_w1": (
            "32-39,88-95,132-133,184-189,196-197,228-241"
        ),
        "ra_fullgrid_hybench_none_20260718_w2": (
            "40-47,96-103,134-135,190-195,198-199,242-255"
        ),
    }
    policy["allowed_external_cpu_union"] = "32-103,128-199,228-255"
    policy["container_cpuset_readback_mode"] = (
        "one_of_explicitly_sealed_cpu_disjoint_profiles"
    )
    policy["container_identity_mount_and_memory_readback_required"] = True
    policy["arbitrary_cpuset_or_memory_drift_action"] = "fail_closed"
    policy["numa_memory_isolation_evidence"] = (
        "target cpuset_mems remains frozen; PG containers retain sealed 8-24GiB "
        "memory cgroup profiles; host headroom, swap and full-memory PSI are gated"
    )
    allowed_union = coordination.cpus(policy["allowed_external_cpu_union"])
    target_union = coordination._target_cpu_union()
    if allowed_union & target_union:
        raise RuntimeError("sealed external CPU union overlaps MySQL")
    for row in policy["containers"]:
        row["allowed_cpuset_cpus"] = [row["cpuset_cpus"]]
        row["allowed_memory_bytes"] = [int(row["memory_bytes"])]
        if row["name"] in wide:
            row["allowed_cpuset_cpus"].append(wide[row["name"]])
            row["allowed_memory_bytes"].append(24 * 1024**3)
        for profile in row["allowed_cpuset_cpus"]:
            profile_cpus = coordination.cpus(profile)
            if (
                not profile_cpus
                or not profile_cpus <= allowed_union
                or profile_cpus & target_union
            ):
                raise RuntimeError(f"unsealed or overlapping profile: {row['name']}")


def main() -> int:
    verify_failure_boundary()
    coordination_release = load(OLD_COORD)
    coordination_release["predecessor"] = {
        "path": rel(OLD_COORD), "sha256": sha(OLD_COORD),
    }
    coordination_release["release_version"] = "v52"
    coordination_release["successor_reason"] = (
        "accept only explicitly sealed CPU-disjoint/disk1-only PG container "
        "profiles; preserve fail-closed CPU, disk2, memory, process and "
        "persistent-D-state gates"
    )
    seal_container_profiles(
        coordination_release["co_resident_isolated_pg_allowance"]
    )
    coordination_release["isolated_co_resident_runtime_v2"] = {
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "workload_or_resource_envelope_changed": False,
        "candidate_schedule_scoring_acceptance_and_convergence_changed": False,
        "isolated_external_physical_workload_allowed": True,
        "cpu_overlap_or_disk2_mount_action": "fail_closed",
        "unsealed_container_profile_action": "fail_closed",
        "persistent_d_state_required_consecutive_samples": 3,
        "outer13_rank1_reuse_prohibited_due_to_runtime_overlap": True,
        "resume_checkpoint_outer_attempt": 12,
        "resume_outer_attempt": 13,
        "resume_rank": 1,
    }
    source_bindings = coordination_release["source_bindings"]
    rebind(source_bindings)
    additions = {
        "co_resident_profile_installer": ROOT / "scripts/mysql_co_resident_pg_isolation_v2.py",
        "algorithm_c_v47_entrypoint": ROOT / "scripts/run_mysql_four_scene_algorithm_c_boundary_resume_v3.py",
        "algorithm_c_v47_launcher": ROOT / "scripts/launch_mysql_algorithm_c_boundary_resume_v3.py",
        "algorithm_c_v47_waiter": ROOT / "scripts/wait_launch_mysql_algorithm_c_boundary_resume_v2.py",
        "runtime_watchdog_v52_entrypoint": ROOT / "scripts/watch_mysql_formal_external_postgres_interference_v2.py",
        "algorithm_b_controller": ROOT / "scripts/run_mysql_four_scene_algorithm_b_from_c_v1.py",
        "algorithm_b_launcher": ROOT / "scripts/launch_mysql_four_scene_algorithm_b_from_c_v1.py",
        "algorithm_b_auto_waiter": ROOT / "scripts/wait_and_launch_mysql_algorithm_b_after_c_v1.py",
        "empty_quarantine_multi_outer_builder": ROOT / "scripts/rebind_mysql_algorithm_c_empty_replay_quarantine_v2.py",
        "co_resident_profile_release_builder": Path(__file__).resolve(),
        "co_resident_profile_test": ROOT / "tests/test_mysql_co_resident_pg_isolation_v2.py",
        "outer13_failure_state": FAILED_STATE,
        "outer13_failure_watchdog": FAILED_WATCHDOG,
        "outer13_failure_gate": FAILED_GATE,
        "outer13_contaminated_rank1": CONTAMINATED_RANK1,
        "outer12_checkpoint": CHECKPOINT,
        "outer12_replay_quarantine": QUARANTINE,
    }
    for label, path in additions.items():
        bind(source_bindings, label, path)
    write(NEW_COORD, coordination_release)

    optimization = load(OLD_OPT)
    optimization["schema_version"] = (
        "mysql_algorithm_c_outer_runtime_optimization_v47"
    )
    optimization["predecessor"] = {
        "path": rel(OLD_OPT), "sha256": sha(OLD_OPT),
    }
    optimization["successor_reason"] = (
        "resume clean Outer 12 state while allowing only sealed, dynamically "
        "read-back, CPU-disjoint disk1 PostgreSQL profiles"
    )
    allowance = optimization["co_resident_isolated_pg_allowance"]
    allowance.update({
        "coordination_contract_path": rel(NEW_COORD),
        "coordination_contract_sha256": sha(NEW_COORD),
        "unknown_or_changed_external_workload_action": "fail_closed",
        "sealed_isolated_external_workload_action": "allow_with_runtime_watchdog",
        "formal_completed_rank1_reuse_after_runtime_overlap": False,
        "algorithm_acceptance_and_convergence_semantics_changed": False,
    })
    bindings = optimization["bindings"]
    rebind(bindings)
    bindings["wave_coordination_contract_path"] = rel(NEW_COORD)
    bindings["wave_coordination_contract_sha256"] = sha(NEW_COORD)
    for label, path in additions.items():
        bind(bindings, label, path)
    write(NEW_OPT, optimization)
    print(json.dumps({
        "coordination_contract": str(NEW_COORD),
        "coordination_sha256": sha(NEW_COORD),
        "optimization_contract": str(NEW_OPT),
        "optimization_sha256": sha(NEW_OPT),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

