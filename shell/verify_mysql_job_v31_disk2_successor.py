#!/usr/bin/env python3
"""Offline, fail-closed audit for the JOB v30 -> disk2 successor.

This tool never copies a template and never starts or modifies a data-plane
process.  It intentionally reports BLOCKED until both the new JOB disk2
storage manifest and the shared JOB/TPC-H disk2 lane are bound.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = (
    ROOT / "preflight/mysql_job_v31_disk2_successor_contract_20260729.json"
)
JOB_V30 = (
    ROOT / "artifacts/mysql_job_v30_debug"
    / "b888870bc6ef136932c26676c797823d2b99858cf6b2b3891f7afe1ef42d3190"
)
TPCH_V36 = (
    ROOT / "artifacts/mysql_tpch_v36_debug"
    / "1332ab2e571e981761fc957cd65c36ff6e782663eb70ba4c8952ddc815f14dc4"
)


class AuditError(RuntimeError):
    pass


def load_object(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise AuditError(f"{label} absent or symlinked: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AuditError(f"{label} must be a JSON object")
    return value


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def executable_strings(value: Any, *, provenance: bool = False) -> list[str]:
    """Return executable strings while excluding explicit provenance-only data."""
    if isinstance(value, Mapping):
        if (
            value.get("authority_role") == "provenance_only"
            and value.get("execution_authorized") is False
        ):
            return []
        result: list[str] = []
        for key, item in value.items():
            result.extend(
                executable_strings(
                    item,
                    provenance=provenance or key in {"provenance", "supersedes"},
                )
            )
        return result
    if isinstance(value, list):
        result = []
        for item in value:
            result.extend(executable_strings(item, provenance=provenance))
        return result
    return [] if provenance or not isinstance(value, str) else [value]


def validate_storage_manifest(
    document: Mapping[str, Any],
    contract: Mapping[str, Any],
) -> list[str]:
    expected = contract["required_executable_bindings"]
    checks = {
        "schema_version": "mysql_job_local_storage_v3",
        "profile_id": expected["profile_id"],
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "template_path": expected["template_path"],
        "device": expected["device"],
        "device_major_minor": expected["device_major_minor"],
        "filesystem": expected["filesystem"],
        "fresh_copy_mode": expected["fresh_copy_mode"],
        "sealed_read_only": True,
        "clean_shutdown": True,
        "overlay": False,
        "reflink": False,
    }
    problems = [
        f"storage manifest {key} differs"
        for key, wanted in checks.items()
        if document.get(key) != wanted
    ]
    tree_hash = document.get("template_tree_sha256")
    if not isinstance(tree_hash, str) or len(tree_hash) != 64:
        problems.append("storage manifest template tree hash absent")
    if not isinstance(document.get("regular_file_count"), int) or (
        document.get("regular_file_count", 0) <= 0
    ):
        problems.append("storage manifest regular file count invalid")
    return problems


def validate_successor(
    job_manifest: Mapping[str, Any],
    job_config: Mapping[str, Any],
    tpch_manifest: Mapping[str, Any],
    tpch_config: Mapping[str, Any],
    storage_manifest: Mapping[str, Any],
    storage_manifest_sha256: str,
    contract: Mapping[str, Any],
) -> list[str]:
    expected = contract["required_executable_bindings"]
    lane = contract["shared_device_lane"]
    problems = validate_storage_manifest(storage_manifest, contract)
    resource = job_manifest.get("execution_resource_authority") or {}
    storage = job_manifest.get("execution_storage_authority") or {}
    config_resource = job_config.get("execution_resource_authority") or {}
    config_storage = job_config.get("execution_storage_authority") or {}
    for label, authority in (
        ("bundle resource", resource),
        ("config resource", config_resource),
    ):
        if (
            authority.get("storage_device") != expected["device"]
            or authority.get("storage_device_major_minor")
            != expected["device_major_minor"]
        ):
            problems.append(f"{label} does not bind disk2")
    wanted_storage = {
        "device": expected["device"],
        "device_major_minor": expected["device_major_minor"],
        "filesystem": expected["filesystem"],
        "fresh_copy_mode": expected["fresh_copy_mode"],
        "manifest_path": expected["manifest_path"],
        "manifest_sha256": storage_manifest_sha256,
        "profile_id": expected["profile_id"],
        "template_path": expected["template_path"],
        "overlay": False,
        "reflink": False,
        "execution_authorized": True,
    }
    for label, authority in (
        ("bundle storage", storage),
        ("config storage", config_storage),
    ):
        for key, wanted in wanted_storage.items():
            if authority.get(key) != wanted:
                problems.append(f"{label} {key} differs")
    if (
        job_config.get("local_storage_manifest") != expected["manifest_path"]
        or job_config.get("local_storage_manifest_sha256")
        != storage_manifest_sha256
    ):
        problems.append("config storage manifest pointer differs")
    if job_manifest.get("fresh_resource_readback_required") is not True:
        problems.append("fresh resource readback is not required")
    if job_manifest.get("old_resource_readback_reusable") is not False:
        problems.append("old disk1 resource readback is not explicitly rejected")
    if job_manifest.get("accepted_round_authorized") is not False:
        problems.append("migration successor must remain accepted=false")
    for label, document in (
        ("JOB manifest", job_manifest),
        ("JOB config", job_config),
    ):
        if any(
            token in value
            for value in executable_strings(document)
            for token in ("/disk1/", "/dev/nvme1n1", "259:5")
        ):
            problems.append(f"{label} retains executable disk1 authority")
    for label, document in (
        ("JOB manifest", job_manifest),
        ("JOB config", job_config),
        ("TPC-H manifest", tpch_manifest),
        ("TPC-H config", tpch_config),
    ):
        if document.get("shared_device_lane") != lane:
            problems.append(f"{label} lacks the exact shared disk2 lane")
    tpch_storage = tpch_manifest.get("execution_storage_authority") or {}
    if tpch_storage.get("device_major_minor") != lane["device_major_minor"]:
        problems.append("TPC-H is not bound to the shared disk2 device")
    return sorted(set(problems))


def audit_current() -> dict[str, Any]:
    contract = load_object(CONTRACT_PATH, "migration contract")
    job_manifest = load_object(JOB_V30 / "bundle_manifest.json", "JOB v30 manifest")
    job_config = load_object(JOB_V30 / "config.json", "JOB v30 config")
    tpch_manifest = load_object(
        TPCH_V36 / "bundle_manifest.json", "TPC-H v36 manifest"
    )
    tpch_config = load_object(TPCH_V36 / "config.json", "TPC-H v36 config")
    expected = contract["required_executable_bindings"]
    storage_path = Path(expected["manifest_path"])
    if storage_path.is_file() and not storage_path.is_symlink():
        storage = load_object(storage_path, "JOB disk2 storage manifest")
        storage_sha = sha256(storage_path)
    else:
        storage = {}
        storage_sha = "ABSENT"
    problems = validate_successor(
        job_manifest,
        job_config,
        tpch_manifest,
        tpch_config,
        storage,
        storage_sha,
        contract,
    )
    if not storage_path.is_file():
        problems.append("JOB disk2 template/manifest has not been staged")
    return {
        "schema_version": "mysql_job_v31_disk2_successor_audit_v1",
        "status": "READY_TO_PUBLISH" if not problems else "BLOCKED",
        "data_plane_touched": False,
        "template_copied": False,
        "contract_path": str(CONTRACT_PATH),
        "contract_sha256": sha256(CONTRACT_PATH),
        "predecessor_bundle": str(JOB_V30),
        "tpch_bundle": str(TPCH_V36),
        "required_storage_manifest": str(storage_path),
        "job_tpch_must_run_serially": True,
        "shared_device_lane": contract["shared_device_lane"],
        "problems": sorted(set(problems)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    result = audit_current()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] == "READY_TO_PUBLISH" else 2


if __name__ == "__main__":
    raise SystemExit(main())
