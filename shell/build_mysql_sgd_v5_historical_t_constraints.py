#!/usr/bin/env python3
"""Publish the immutable MySQL-only v4 readiness constraint bundle."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "config/formal/frozen_mysql_deployment_schedule_20260723_v2_draft"
TARGET = ROOT / "config/formal/frozen_mysql_deployment_schedule_20260723_v4_mysql_only"
FREEZE_ID = "frozen_mysql_deployment_schedule_20260723_v4_mysql_only"


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pointer(relative_path: str, data: bytes) -> dict[str, str]:
    return {"path": relative_path, "sha256": digest(data)}


def source_pointer(path: Path) -> dict[str, str]:
    relative = path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else str(path)
    return {"path": relative, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(raw)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    old_manifest = json.loads((SOURCE / "readiness_manifest.json").read_text())
    old_method = json.loads((SOURCE / "experience_table_sgd_method_manifest.json").read_text())
    old_formal_config = (SOURCE / "experience_table_sgd_formal_config.json").read_bytes()

    frozen_config = f"""# MySQL  SGD  v4MySQL-only

##

- Freeze ID`{FREEZE_ID}`
- Formal scope`mysql_only`
- `FROZEN CONSTRAINT / NOT_READY / NO FORMAL LAUNCH`
- `2026-07-23`
- `../frozen_mysql_deployment_schedule_20260723_v2_draft/FROZEN_CONFIG.md`

##

 MySQL-only readiness  PostgreSQL counterpart
 PostgreSQL/MySQL resource envelope
`postgresql_envelope` PostgreSQL
 paired-scope
 PostgreSQL/MySQL

##  fail-closed

1. MySQL  CPU quotamemoryswapI/OpidsCPU isolation  NUMA isolation
    MySQL  scope-binding wrapper
    SHA-256
2.  CPU affinityCPUNUMAmemory/swap
3.  30  180  30
   600  90%
4.  host
   admission 5%P95/P99  10%
   5
5. R0 Tlane lease5→10→K10
    SGD  READY

##

 launch manifestmethod manifestREADY JSON
`formal_scope=mysql_only`  `cross_engine_comparison_authorized=false` v2/v3
 paired
""".encode()

    migration = {
        "schema_version": "mysql_formal_scope_migration_v1",
        "status": "PUBLISHED_NOT_READY",
        "freeze_id": FREEZE_ID,
        "published_at": "2026-07-23T07:50:00+08:00",
        "authorization": "explicit_operator_authorization_in_control_session",
        "from_scope": "paired_cross_engine",
        "to_scope": "mysql_only",
        "removed_readiness_requirements": [
            "postgresql_counterpart_envelope",
            "postgresql_envelope_readback",
            "postgresql_mysql_field_equality",
        ],
        "retained_fail_closed_requirements": [
            "mysql_effective_envelope_freeze_and_readback",
            "co_resident_process_inventory",
            "fixed_cpu_affinity",
            "cpu_numa_memory_swap_disk_process_isolation",
            "latest_six_30_second_window_dynamic_non_interference",
            "explicit_ready_launch_authorization",
        ],
        "publication_policy": {
            "cross_engine_comparison_authorized": False,
            "paired_result_merge_prohibited": True,
            "old_bundles_mutated": False,
        },
    }
    migration_data = canonical_json(migration)

    formal_config_path = (
        "config/formal/frozen_mysql_deployment_schedule_20260723_v4_mysql_only/"
        "experience_table_sgd_formal_config.json"
    )
    method = copy.deepcopy(old_method)
    method.update(
        draft_id=FREEZE_ID,
        formal_scope="mysql_only",
        cross_engine_comparison_authorized=False,
        formal_launch_authorized=False,
    )
    method["formal_config"] = pointer(formal_config_path, old_formal_config)
    method_data = canonical_json(method)

    manifest = copy.deepcopy(old_manifest)
    manifest.update(
        schema_version=3,
        draft_id=FREEZE_ID,
        formal_scope="mysql_only",
        status="NOT_READY",
        launch_authorized=False,
    )
    policy = manifest["policy"]
    limits = policy.pop("pg_interference_limits")
    policy.update(
        cross_engine_envelope_equality_required=False,
        postgresql_counterpart_required=False,
        cross_engine_comparison_authorized=False,
        co_resident_non_interference_required=True,
        co_resident_interference_limits=limits,
    )
    manifest["publication_policy"] = {
        "database_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "paired_result_merge_prohibited": True,
        "scope_label_required_in_final_json": True,
    }
    manifest["requirement_index"]["R02"] = (
        "MySQL-only scope: no PostgreSQL counterpart/equality requirement; "
        "cross-engine comparison and paired-result merge are prohibited"
    )

    target_prefix = "config/formal/frozen_mysql_deployment_schedule_20260723_v4_mysql_only"
    scope_bindings: dict[str, bytes] = {}
    for scenario_name, scenario in manifest["scenarios"].items():
        scenario.pop("postgresql_envelope", None)
        mysql = scenario["mysql_envelope"]
        old_readback_pointer = copy.deepcopy(mysql["readback"])
        old_readback_path = ROOT / old_readback_pointer["path"]
        if hashlib.sha256(old_readback_path.read_bytes()).hexdigest() != old_readback_pointer["sha256"]:
            raise RuntimeError(f"source MySQL readback checksum drift: {scenario_name}")
        binding = {
            "schema_version": "mysql_only_scope_bound_envelope_readback_v1",
            "status": "SCOPE_BOUND_FROM_AUTHENTIC_MYSQL_READBACK",
            "scenario": scenario_name,
            "engine": "mysql",
            "formal_scope_name": "mysql_only",
            "freeze_id": FREEZE_ID,
            "postgresql_counterpart_required": False,
            "cross_engine_envelope_equality_required": False,
            "normalized_envelope": mysql["normalized"],
            "source_mysql_readback": old_readback_pointer,
            "source_freeze_id": mysql["freeze_id"],
            "source_bytes_unchanged": True,
        }
        binding_data = canonical_json(binding)
        relative = f"{target_prefix}/evidence/{scenario_name}.mysql_only_envelope_readback.json"
        scope_bindings[relative] = binding_data
        mysql["freeze_id"] = FREEZE_ID
        mysql["readback"] = pointer(relative, binding_data)

    method_path = f"{target_prefix}/experience_table_sgd_method_manifest.json"
    manifest["global_requirements"]["method_bundle"]["method_manifest"] = pointer(
        method_path, method_data
    )
    manifest["global_requirements"]["method_bundle"]["formal_config"] = pointer(
        formal_config_path, old_formal_config
    )
    manifest["global_requirements"]["bundle_publication"] = {
        "ready": False,
        "checksums": None,
        "independent_audit": None,
        "ready_file": None,
        "ready_file_authorizes_launch": False,
    }

    sibling = Path(old_manifest["source_documents"]["sibling_v1"]["path"])
    manifest["source_documents"] = {
        "draft_schedule": pointer(f"{target_prefix}/FROZEN_CONFIG.md", frozen_config),
        "measurement_and_logging_contract": source_pointer(
            ROOT / "preflight/mysql_five_benchmark_resource_envelope_and_search_algorithms_20260722.md"
        ),
        "scope_migration": pointer(f"{target_prefix}/scope_migration_audit.json", migration_data),
        "sibling_v1": source_pointer(sibling),
    }
    manifest_data = canonical_json(manifest)

    files: dict[str, bytes] = {
        "FROZEN_CONFIG.md": frozen_config,
        "scope_migration_audit.json": migration_data,
        "experience_table_sgd_formal_config.json": old_formal_config,
        "experience_table_sgd_method_manifest.json": method_data,
        "readiness_manifest.json": manifest_data,
        **{relative.removeprefix(f"{target_prefix}/"): data for relative, data in scope_bindings.items()},
    }
    sidecars: dict[str, bytes] = {}
    for relative, data in files.items():
        sidecars[f"{relative}.sha256"] = f"{digest(data)}  {Path(relative).name}\n".encode()
    files.update(sidecars)
    checksum_lines = [
        f"{digest(data)}  {relative}"
        for relative, data in sorted(files.items())
    ]
    files["checksums.sha256"] = ("\n".join(checksum_lines) + "\n").encode()

    for relative, data in files.items():
        atomic_write(TARGET / relative, data)
    print(json.dumps({
        "status": "PUBLISHED_NOT_READY",
        "freeze_id": FREEZE_ID,
        "target": str(TARGET),
        "file_count": len(files),
        "manifest_sha256": digest(manifest_data),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
