#!/usr/bin/env python3
"""Publish v57/v54 for the read-only verified bench-pg-row disk3 profile."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OLD_COORD = (
    ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260817_v56"
    / "coordination_contract.json"
)
NEW_COORD = (
    ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260817_v57"
    / "coordination_contract.json"
)
OLD_OPT = (
    ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260817_v53"
    / "optimization_contract.json"
)
NEW_OPT = (
    ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260817_v54"
    / "optimization_contract.json"
)
FAILED_PREFLIGHT = (
    ROOT / "preflight/mysql_four_scene_20260809"
    / "v54_recovered_xp_rebind_r0_20260817_attempt006_preflight.json"
)
RECOVERED_XP = (
    ROOT / "history/mysql_four_scene_20260809"
    / "algorithm_c_rank2_partial_all_xp_continuation_v1_attempt006_20260817"
    / "recovered_xp_binding.json"
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT))


def load(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular non-symlink JSON required: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def bind(bindings: dict[str, Any], label: str, path: Path) -> None:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular binding required: {path}")
    bindings[f"{label}_path"] = rel(path)
    bindings[f"{label}_sha256"] = sha(path)


def verify_existing_bindings(
    bindings: dict[str, Any], labels: set[str],
) -> None:
    for label in labels:
        key = f"{label}_path"
        sha_key = f"{label}_sha256"
        value = bindings.get(key)
        if value is None or sha_key not in bindings:
            raise RuntimeError(f"predecessor binding absent: {key}")
        path = ROOT / str(value)
        if not path.is_file() or path.is_symlink() or sha(path) != bindings[sha_key]:
            raise RuntimeError(f"predecessor binding differs: {key}")


def write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def main() -> int:
    failed = load(FAILED_PREFLIGHT)
    last = (failed.get("samples") or [])[-1]
    isolation = last.get("co_resident_pg_isolation") or {}
    row = next(
        item for item in (
            (isolation.get("container_readback") or {})
            .get("additional_containers") or []
        )
        if item.get("name") == "bench-pg-row"
    )
    disk3_source = subprocess.run(
        ["findmnt", "-n", "-o", "SOURCE", "--target", "/disk3"],
        capture_output=True, text=True, check=True, timeout=15,
    ).stdout.strip()
    disk2_source = subprocess.run(
        ["findmnt", "-n", "-o", "SOURCE", "--target", "/disk2"],
        capture_output=True, text=True, check=True, timeout=15,
    ).stdout.strip()
    if (
        failed.get("status") != "BLOCKED_FAIL_CLOSED"
        or row.get("running") is not True
        or row.get("mount_sources") != ["/disk3/wxr-data/cmp/row"]
        or row.get("cpuset_cpus") != "144-159,400-415"
        or set(row.get("errors") or []) != {
            "additional_container_required_mount_missing",
            "additional_container_mount_outside_sealed_roots",
        }
        or disk3_source != "/dev/sda1"
        or disk2_source != "/dev/nvme2n1"
        or disk3_source == disk2_source
    ):
        raise RuntimeError("bench-pg-row disk3 release evidence differs")

    coordination = load(OLD_COORD)
    verify_existing_bindings(coordination["source_bindings"], {
        "coordination_source", "boundary_resume_controller", "runtime_watchdog",
        "live_preflight", "co_resident_profile_installer_v4",
        "algorithm_c_v56_entrypoint", "algorithm_c_v56_launcher",
    })
    coordination["predecessor"] = {
        "path": rel(OLD_COORD), "sha256": sha(OLD_COORD),
    }
    coordination["release_version"] = "v57"
    coordination["successor_reason"] = (
        "seal the live bench-pg-row /disk3 bind mount after read-only proof that "
        "its CPU set and storage device are disjoint from the frozen MySQL /disk2 "
        "envelope; no external mutation and no workload, resource, Top-5, score, "
        "binary shrink, acceptance or convergence change"
    )
    policy = coordination["co_resident_isolated_pg_allowance"]
    profile = next(
        item for item in policy["additional_container_profiles"]
        if item["name"] == "bench-pg-row"
    )
    profile["required_mount_sources"] = ["/disk3/wxr-data/cmp/row"]
    profile["allowed_mount_prefixes"] = ["/disk3/wxr-data/cmp/"]
    evidence = policy["additional_container_release_evidence"]
    evidence.update({
        "bench_pg_row_storage_mount": "/disk3",
        "bench_pg_row_storage_source": disk3_source,
        "bench_pg_row_mysql_storage_overlap": False,
        "bench_pg_row_profile_readback_path": rel(FAILED_PREFLIGHT),
        "bench_pg_row_profile_readback_sha256": sha(FAILED_PREFLIGHT),
        "external_mutation": False,
        "read_only_profile_validation": True,
    })
    sources = coordination["source_bindings"]
    additions = {
        "co_resident_profile_installer_v5": ROOT / "scripts/mysql_co_resident_pg_isolation_v5.py",
        "runtime_watchdog_v54_entrypoint": ROOT / "scripts/watch_mysql_formal_external_postgres_interference_v5.py",
        "algorithm_c_v54_entrypoint": ROOT / "scripts/run_mysql_four_scene_algorithm_c_boundary_resume_v13.py",
        "algorithm_c_v54_launcher": ROOT / "scripts/launch_mysql_algorithm_c_boundary_resume_v13.py",
        "recovered_xp_preflight": ROOT / "scripts/preflight_mysql_four_scene_live_v18_recovered_xp_v1.py",
        "recovered_xp_r0_wrapper": ROOT / "scripts/run_mysql_four_scene_r0_v18_recovered_xp_v1.py",
        "bench_pg_row_disk3_release_builder": Path(__file__).resolve(),
        "bench_pg_row_disk3_test": ROOT / "tests/test_mysql_co_resident_pg_isolation_v5.py",
        "bench_pg_row_profile_failure": FAILED_PREFLIGHT,
        "recovered_xp_binding": RECOVERED_XP,
    }
    for label, path in additions.items():
        bind(sources, label, path)
    write(NEW_COORD, coordination)

    optimization = load(OLD_OPT)
    verify_existing_bindings(optimization["bindings"], {
        "resource_freeze", "reset_source", "algorithm_source",
        "boundary_resume_controller",
        "wave_coordination_contract", "runtime_watchdog_v53_entrypoint",
        "algorithm_c_v53_entrypoint", "algorithm_c_v53_launcher",
    })
    optimization["schema_version"] = "mysql_algorithm_c_outer_runtime_optimization_v54"
    optimization["predecessor"] = {
        "path": rel(OLD_OPT), "sha256": sha(OLD_OPT),
    }
    optimization["successor_reason"] = coordination["successor_reason"]
    allowance = optimization["co_resident_isolated_pg_allowance"]
    allowance.update({
        "coordination_contract_path": rel(NEW_COORD),
        "coordination_contract_sha256": sha(NEW_COORD),
        "bench_pg_row_disk3_profile_sealed": True,
        "external_mutation": False,
        "algorithm_acceptance_and_convergence_semantics_changed": False,
    })
    optimization["runtime_watchdog_confirmation_v54"] = {
        **optimization["runtime_watchdog_confirmation_v53"],
        "coordination_release": "v57",
        "bench_pg_row_disk3_profile_sealed": True,
        "workload_resource_top5_scoring_acceptance_and_convergence_changed": False,
    }
    bindings = optimization["bindings"]
    bind(
        bindings, "boundary_launch_source",
        ROOT / "scripts/launch_mysql_algorithm_c_boundary_resume_v2.py",
    )
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
