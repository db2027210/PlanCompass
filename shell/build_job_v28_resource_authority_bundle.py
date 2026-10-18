#!/usr/bin/env python3
"""Publish immutable JOB v28 with one CPU/resource and storage authority."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shell import build_job_v27_local_authority_bundle as base


V27_ID = "19ef0e0199cbc53fb7a86983f5cbcf35df42060512e63610789629298871f2f3"
INCOMPLETE_V28_VERIFIER_BINDING_ID = (
    "429bdc667bd1a7a4faa8c22d8e30bea38b08af904bb585a1a2fcccb3bddb2cbb"
)
COSMETIC_V27_LAUNCH_LABEL_ID = (
    "5c0751398d0c8d258527970e7deeaa5bd19e07563ff5205f627b809ac323d554"
)
V27_BUNDLE = ROOT / "artifacts/mysql_job_v27_debug" / V27_ID
V27_MANIFEST = V27_BUNDLE / "bundle_manifest.json"
V27_READINESS = next(V27_BUNDLE.glob("readiness_manifest-*.json"))
V27_METHOD = V27_BUNDLE / "experience_table_sgd_method_manifest.json"
V27_SOURCE = Path(
    json.loads(V27_MANIFEST.read_text(encoding="utf-8"))["source_directory"]
)
SERVICE = "shell.mysql_algorithm_c_local_services_v28:build"
SERVICE_SOURCE = ROOT / "shell/mysql_algorithm_c_local_services_v28.py"
V27_SERVICE_SOURCE = ROOT / "shell/mysql_algorithm_c_local_services_v27.py"
VERIFIER = ROOT / "shell/verify_mysql_job_v28_readiness.py"
V27_VERIFIER = ROOT / "shell/verify_mysql_job_v27_readiness.py"
PROFILE_SOURCE = (
    ROOT / "config/formal/mysql_sgd_production/profiles/job_memory_v28.json"
)
RESOURCE_TEST = ROOT / "tests/test_mysql_job_v28_resource_authority.py"
STORAGE_TEST = ROOT / "tests/test_mysql_job_v27_storage_authority.py"
BUILDER = Path(__file__).resolve()
SOURCE_ROOT = (
    ROOT
    / "history/mysql_gradient_debug/readiness_job_v28_resource_authority_v1"
)
BUNDLE_ROOT = ROOT / "artifacts/mysql_job_v28_debug"
OUTPUT_ROOT = (
    ROOT
    / "history/mysql_gradient_debug/"
    "job_v28_original_gradient_fresh_r0_debug_20260729"
)
PROFILE_RELATIVE = (
    "config/formal/mysql_sgd_production/profiles/job_memory.json"
)


def configure_base() -> None:
    base.PREDECESSOR_ID = V27_ID
    base.PREDECESSOR = V27_BUNDLE
    base.PREDECESSOR_MANIFEST = V27_MANIFEST
    base.PREDECESSOR_READINESS = V27_READINESS
    base.PREDECESSOR_METHOD = V27_METHOD
    base.BASE_SOURCE = V27_SOURCE
    base.SOURCE_ROOT = SOURCE_ROOT
    base.BUNDLE_ROOT = BUNDLE_ROOT
    base.OUTPUT_ROOT = OUTPUT_ROOT
    base.VERIFIER = VERIFIER
    base.VERIFIER_RELATIVE = "shell/verify_mysql_job_v28_readiness.py"
    base.LAUNCH_LABEL = "JOB v28"
    base.SERVICE_SOURCE = SERVICE_SOURCE
    base.AUTHORITY_TEST = RESOURCE_TEST
    base.BUILDER = BUILDER
    base.SERVICE = SERVICE
    base.EXTRA_SOURCES = {
        "shell/mysql_algorithm_c_local_services_v27.py": V27_SERVICE_SOURCE,
        "shell/mysql_algorithm_c_local_services_v28.py": SERVICE_SOURCE,
        "shell/verify_mysql_job_v27_readiness.py": V27_VERIFIER,
        "shell/verify_mysql_job_v28_readiness.py": VERIFIER,
        "tests/test_mysql_job_v27_storage_authority.py": STORAGE_TEST,
        "tests/test_mysql_job_v28_resource_authority.py": RESOURCE_TEST,
        PROFILE_RELATIVE: PROFILE_SOURCE,
    }


def execution_resource_authority(source: Path) -> dict[str, Any]:
    profile = source / PROFILE_RELATIVE
    return {
        "schema_version": "mysql_job_execution_resource_authority_v1",
        "authority_role": "sole_execution_authority",
        "execution_authorized": True,
        "scenario": "job_memory",
        "host_id": "compute00",
        "cpuset_cpus": "176-183,432-439",
        "cpuset_mems": "1",
        "cpu_quota_cores": 1.15,
        "memory_bytes": 4294967296,
        "memory_swap_bytes": 4294967296,
        "io_read_bps": 7340032,
        "io_write_bps": 7340032,
        "pids_max": 512,
        "buffer_pool_bytes": 3221225472,
        "storage_device": "/dev/nvme1n1",
        "storage_device_major_minor": "259:5",
        "profile_path": str(profile),
        "profile_sha256": base.sha(profile),
    }


def config_payload(source: Path) -> bytes:
    config = json.loads(base.config_payload(source))
    resource = execution_resource_authority(source)
    config.update({
        "exp_id": "mysql_algorithm_c.job_memory.v28_one_round_debug",
        "profile": resource["profile_path"],
        "execution_resource_authority": resource,
        "resource_authority_policy": {
            "schema_version": "mysql_job_single_resource_authority_policy_v1",
            "executable_authority_count": 1,
            "second_cpu_authority_action": "FAIL_CLOSED",
            "profile_mismatch_action": "FAIL_CLOSED",
            "compiled_lane_mismatch_action": "FAIL_CLOSED",
            "runtime_readback_mismatch_action": "FAIL_CLOSED",
            "mirrors": [
                "workload.host_control_cpuset",
                "profile",
                "readiness.scenarios.job_memory.resource_envelope",
                "runtime_effective_resource_readback",
            ],
        },
    })
    config["workload"]["host_control_cpuset"] = resource["cpuset_cpus"]
    config["production"]["service_factory"] = SERVICE
    config["template_provenance"]["source_bundle_id"] = V27_ID
    return base.canonical(config)


def verify_existing(
    final: Path, bundle_id: str,
) -> tuple[Path, dict[str, int]]:
    manifest = json.loads(
        (final / "bundle_manifest.json").read_text(encoding="utf-8")
    )
    if manifest.get("bundle_id") != bundle_id:
        raise RuntimeError("existing v28 bundle identity differs")
    for name, expected in manifest.get("files", {}).items():
        candidate = final / name
        if not candidate.is_file() or base.sha(candidate) != expected:
            raise RuntimeError(f"existing v28 bundle differs: {name}")
    readiness = list(final.glob("readiness_manifest-*.json"))
    if len(readiness) != 1:
        raise RuntimeError("existing v28 readiness cardinality differs")
    return readiness[0], base.verify_readiness(readiness[0])


def main() -> int:
    configure_base()
    source_manifest = base.validate_inputs()
    source, source_files, source_id = base.publish_source(source_manifest)
    config = config_payload(source)
    launcher = base.launcher_payload(source)
    resource = execution_resource_authority(source)
    storage = base.execution_storage_authority()
    normalized, effective = base.normalized_envelope()
    normalized.update({
        "cpuset_cpus": resource["cpuset_cpus"],
        "cpuset_mems": resource["cpuset_mems"],
        "cpu_quota_cores": resource["cpu_quota_cores"],
        "memory_bytes": resource["memory_bytes"],
        "memory_swap_bytes": resource["memory_swap_bytes"],
        "io_read_bps": resource["io_read_bps"],
        "io_write_bps": resource["io_write_bps"],
        "pids_max": resource["pids_max"],
        "storage_device": resource["storage_device"],
        "storage_device_major_minor":
            resource["storage_device_major_minor"],
    })
    closure = {
        "schema_version": "mysql_job_v28_bundle_closure_v1",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "immutable": True,
        "accepted_round_authorized": False,
        "supersedes_bundle_ids": [
            V27_ID,
            INCOMPLETE_V28_VERIFIER_BINDING_ID,
            COSMETIC_V27_LAUNCH_LABEL_ID,
        ],
        "single_local_storage_authority": True,
        "single_cpu_resource_authority": True,
        "runtime_interference_gate_revision": 2,
        "pre_session_cpu_device_first_clean_sample": True,
        "workload_start_requires_runtime_gate_ready": True,
        "debug_outer_attempts": 1,
        "source_bundle_id": source_id,
        "source_manifest_sha256": base.sha(
            source / "source_manifest.json"
        ),
        "config_sha256": base.sha_bytes(config),
        "launcher_sha256": base.sha_bytes(launcher),
        "storage_manifest_sha256": base.sha(base.STORAGE_MANIFEST),
        "execution_storage_authority": storage,
        "execution_resource_authority": resource,
        "normalized_envelope": normalized,
        "builder_sha256": base.sha(BUILDER),
    }
    bundle_id = base.sha_bytes(base.canonical(closure))
    final = BUNDLE_ROOT / bundle_id
    if final.exists():
        readiness, result = verify_existing(final, bundle_id)
        print(json.dumps({
            "status": "EXISTS_VERIFIED",
            "bundle_id": bundle_id,
            "source_bundle_id": source_id,
            "directory": str(final),
            "config": str(final / "config.json"),
            "readiness_manifest": str(readiness),
            "launcher": str(final / "launch_job_v28_debug.py"),
            **result,
        }, sort_keys=True))
        return 0

    BUNDLE_ROOT.mkdir(parents=True, exist_ok=True)
    stage = BUNDLE_ROOT / f".{bundle_id}.tmp.{os.getpid()}"
    stage.mkdir(parents=False, exist_ok=False)
    readiness_name = ""
    try:
        (stage / "config.json").write_bytes(config)
        (stage / "launch_job_v28_debug.py").write_bytes(launcher)
        (stage / "launch_job_v28_debug.py").chmod(0o555)
        final_config = final / "config.json"
        final_launcher = final / "launch_job_v28_debug.py"

        readback = final / "job_v28_mysql_only_resource_readback.json"
        readback_payload = base.canonical({
            "schema_version": "mysql_job_v28_scope_resource_readback_v1",
            "scenario": "job_memory",
            "engine": "mysql",
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "status": "FROZEN_FRESH_IN_BAND_READBACK_REQUIRED",
            "execution_storage_authority": storage,
            "execution_resource_authority": resource,
            "normalized_envelope": normalized,
            "runtime_readback_required": {
                "cpuset_cpus": resource["cpuset_cpus"],
                "cpuset_mems": resource["cpuset_mems"],
                "cpu_quota_cores": resource["cpu_quota_cores"],
                "memory_bytes": resource["memory_bytes"],
                "io_read_bps": resource["io_read_bps"],
                "io_write_bps": resource["io_write_bps"],
                "pids_max": resource["pids_max"],
                "storage_device_major_minor":
                    resource["storage_device_major_minor"],
                "mismatch_action": "FAIL_CLOSED",
            },
            "source_effective_readback": {
                "path": str(
                    base.v8.R0_POINT / "effective_resource_readback_v2.json"
                ),
                "sha256": base.sha(
                    base.v8.R0_POINT / "effective_resource_readback_v2.json"
                ),
                "authority_role": "envelope_readiness_only",
                "fresh_in_band_r0_required": True,
            },
            "raw_swap_readback": effective["memory_swap_bytes"],
        })
        (stage / readback.name).write_bytes(readback_payload)

        entries = [
            base.pointer(source / relative, "source")
            for relative in sorted(source_files)
        ]
        entries.extend([
            base.pointer(source / "source_manifest.json", "source_manifest"),
            {
                "path": str(final_config),
                "sha256": base.sha_bytes(config),
                "role": "sole_execution_config",
                "scenario_scope": ["job_memory"],
            },
            {
                "path": str(final_launcher),
                "sha256": base.sha_bytes(launcher),
                "role": "launch_wrapper",
                "scenario_scope": ["job_memory"],
            },
            base.pointer(
                base.STORAGE_MANIFEST,
                "sole_execution_storage_manifest",
            ),
            base.pointer(
                source / PROFILE_RELATIVE,
                "sole_execution_resource_profile",
            ),
            {
                "path": str(readback),
                "sha256": base.sha_bytes(readback_payload),
                "role": "mysql_only_resource_readback",
                "scenario_scope": ["job_memory"],
            },
        ])
        method = json.loads(V27_METHOD.read_text(encoding="utf-8"))
        method.update({
            "schema_version": "mysql_job_v28_method_manifest_v1",
            "registered_sources": entries,
            "formal_launch_authorized": True,
            "formal_scope": "mysql_only",
            "cross_engine_comparison_authorized": False,
            "execution_authorized_scenarios": ["job_memory"],
            "runtime_binding": {
                "entrypoint": str(final_launcher),
                "source_directory": str(source),
                "config": str(final_config),
                "profile": resource["profile_path"],
                "profile_sha256": resource["profile_sha256"],
                "service_factory": SERVICE,
                "accepted_round_authorized": False,
                "debug_outer_attempts": 1,
                "single_local_storage_authority": True,
                "single_cpu_resource_authority": True,
            },
        })
        method_payload = base.canonical(method)
        method_name = "experience_table_sgd_method_manifest.json"
        (stage / method_name).write_bytes(method_payload)

        old_readiness = json.loads(V27_READINESS.read_text(encoding="utf-8"))
        readiness = {
            "schema_version": "mysql_job_v28_readiness_v1",
            "status": "READY",
            "launch_authorized": True,
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "paired_result_merge_prohibited": True,
            "execution_authorized_scenarios": ["job_memory"],
            "scenarios": {
                "job_memory": {
                    "benchmark": "job",
                    "host_id": "compute00",
                    "execution_authorized": True,
                    "accepted_round_authorized": False,
                    "fresh_in_band_r0_required": True,
                    "historical_r0_role": "readiness_only",
                    "resource_envelope": resource,
                    "storage": storage,
                },
            },
            "execution_authority": {
                "schema_version": "mysql_job_v28_execution_authority_v1",
                "bundle_id": bundle_id,
                "source_bundle_id": source_id,
                "scenario": "job_memory",
                "formal_scope": "mysql_only",
                "cross_engine_comparison_prohibited": True,
                "storage": storage,
                "resource": resource,
                "runtime_interference_gate_revision": 2,
                "pre_session_cpu_device_first_clean_sample": True,
                "workload_start_requires_runtime_gate_ready": True,
                "fresh_full_physical_copy_required": True,
            },
            "method_bundle": {
                "method_manifest": {
                    "path": str(final / method_name),
                    "sha256": base.sha_bytes(method_payload),
                },
                "config": {
                    "path": str(final_config),
                    "sha256": base.sha_bytes(config),
                },
            },
            "provenance_only": {
                "authority_role": "provenance_only",
                "execution_authorized": False,
                "may_participate_in_launch": False,
                "source_predecessor_bundle_id": V27_ID,
                "legacy_v27_readiness": {
                    "path": str(V27_READINESS),
                    "sha256": base.sha(V27_READINESS),
                    "known_mismatch":
                        "profile cpuset 18-20,54-56 versus execution "
                        "176-183,432-439",
                },
                "legacy_remote_scenarios": (
                    old_readiness.get("provenance_only", {}).get(
                        "legacy_remote_scenarios", {}
                    )
                ),
            },
        }
        readiness_payload = base.canonical(readiness)
        readiness_sha = base.sha_bytes(readiness_payload)
        readiness_name = f"readiness_manifest-{readiness_sha}.json"
        (stage / readiness_name).write_bytes(readiness_payload)
        (stage / "closure.json").write_bytes(base.canonical(closure))
        files = {
            path.name: base.sha(path)
            for path in sorted(stage.iterdir())
            if path.is_file()
        }
        (stage / "bundle_manifest.json").write_bytes(base.canonical({
            **closure,
            "bundle_id": bundle_id,
            "source_directory": str(source),
            "output_root": str(OUTPUT_ROOT),
            "files": files,
            "publication": "staged_then_atomic_rename",
        }))
        base.readonly(stage)
        stage.rename(final)
        try:
            result = base.verify_readiness(final / readiness_name)
        except BaseException:
            final.rename(BUNDLE_ROOT / f".{bundle_id}.failed.{os.getpid()}")
            raise
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage)
        raise

    print(json.dumps({
        "status": "PUBLISHED_VERIFIED",
        "bundle_id": bundle_id,
        "source_bundle_id": source_id,
        "directory": str(final),
        "config": str(final / "config.json"),
        "readiness_manifest": str(final / readiness_name),
        "launcher": str(final / "launch_job_v28_debug.py"),
        **result,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
