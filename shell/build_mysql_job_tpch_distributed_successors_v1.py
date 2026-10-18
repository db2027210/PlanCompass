#!/usr/bin/env python3
"""Build immutable, non-launchable JOB/TPC-H distributed successor bundles.

This builder is deliberately local-only.  It copies each predecessor source
and bundle closure in full, freezes the requested remote placement, and
publishes by content address with staged atomic renames.  The generated
service factory always fails closed: a later promotion must add fresh remote
runtime/readback/interference evidence and receives a new source and bundle
identity.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shell.audit_mysql_job_tpch_distributed_successors import audit
from shell import runtime_tree_content_identity_v3 as runtime_tree


BUILDER = Path(__file__).resolve()
CONTRACT = (
    ROOT
    / "preflight/mysql_job_tpch_distributed_successor_contract_20260729.json"
)
PREDECESSORS = {
    "job": (
        ROOT / "artifacts/mysql_job_v30_debug"
        / "b888870bc6ef136932c26676c797823d2b99858cf6b2b3891f7afe1ef42d3190"
    ),
    "tpch": (
        ROOT / "artifacts/mysql_tpch_v36_debug"
        / "1332ab2e571e981761fc957cd65c36ff6e782663eb70ba4c8952ddc815f14dc4"
    ),
}
SOURCE_ROOTS = {
    "job": (
        ROOT
        / "history/mysql_gradient_debug/"
        "readiness_mysql_job_distributed_successor_v1_staged"
    ),
    "tpch": (
        ROOT
        / "history/mysql_gradient_debug/"
        "readiness_mysql_tpch_distributed_successor_v1_staged"
    ),
}
BUNDLE_ROOTS = {
    "job": ROOT / "artifacts/mysql_job_v31_distributed_staged",
    "tpch": ROOT / "artifacts/mysql_tpch_v37_distributed_staged",
}
OUTPUT_ROOTS = {
    "job": (
        ROOT
        / "history/mysql_gradient_debug/"
        "job_v31_distributed_fresh_r0_staged_20260729"
    ),
    "tpch": (
        ROOT
        / "history/mysql_gradient_debug/"
        "tpch_v37_distributed_fresh_r0_staged_20260729"
    ),
}
SERVICE = "shell.mysql_algorithm_c_distributed_successor_staged_v1:build"
VERIFIER = "shell/verify_mysql_distributed_successor_staged_v1.py"
PROFILE_REL = {
    "job": "profiles/job_distributed_successor_v1.json",
    "tpch": "profiles/tpch_distributed_successor_v1.json",
}
LAUNCHER_NAME = {
    "job": "launch_job_v31_distributed_staged.py",
    "tpch": "launch_tpch_v37_distributed_staged.py",
}
PYTHON_PUBLICATION_MANIFEST = (
    ROOT / "preflight/runtime_manifests_v3_20260729/"
    "node04_python311_v3-"
    "0db72f3e4e14b3902ff94d17eca9e5c8af97577efdf6ab8b3941fbe189da8c33.json"
)
JDK_PUBLICATION_MANIFEST = (
    ROOT / "preflight/runtime_manifests_v3_20260729/"
    "node04_jdk23_v3-"
    "bf3b427e9510ac77fe6618a60f99f51e1562884d3e81f0cec3cd9f47bfa4852e.json"
)
PYTHON_PORTABLE_MANIFEST = (
    ROOT / "preflight/runtime_portable_manifests_v3_20260729/"
    "node04_python311_v3-"
    "4eeab2e0e5302d20a1fbf4a664fc4938abed84c06f3415e11a462b6e616696c4.json"
)
JDK_PORTABLE_MANIFEST = (
    ROOT / "preflight/runtime_portable_manifests_v3_20260729/"
    "node04_jdk23_v3-"
    "9b00b2613e0fe5669c614c608510db0d0239ce8f1904e99c0877a1e894652bee.json"
)
RUNTIME_TREE_HELPER = ROOT / "shell/runtime_tree_content_identity_v3.py"
QUALIFICATION_COLLECTOR = (
    ROOT / "shell/mysql_distributed_live_readback_qualification_v1.py"
)
QUALIFICATION_METRIC_ADAPTER = (
    ROOT / "shell/mysql_sql_debug_metric_adapter_v1.py"
)
FOUR_SCENE_CONTRACT = (
    ROOT
    / "preflight/"
    "mysql_algorithm_c_distributed_four_scene_successor_contract_20260729.json"
)
TPCH_SQL_REGISTRY = (
    ROOT
    / "history/mysql_gradient_debug/tpch_binding_registry_q1_q22_v10/"
    "tpch-registry-v10-20260728T112123Z"
)
PYTHON_TREE_CONTENT_SHA256 = (
    "3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89"
)
JDK_TREE_CONTENT_SHA256 = (
    "578395e883d31c692a1c01c37910d22dbdafbcf95e373c7e0f6776a26f5c844c"
)
PYTHON_PORTABLE_MANIFEST_REL = (
    "runtime_authority_manifests/python_runtime_tree_manifest_v3.json"
)
JDK_PORTABLE_MANIFEST_REL = (
    "runtime_authority_manifests/jdk_runtime_tree_manifest_v3.json"
)
PYTHON_PORTABLE_MANIFEST_SHA256 = (
    "4eeab2e0e5302d20a1fbf4a664fc4938abed84c06f3415e11a462b6e616696c4"
)
JDK_PORTABLE_MANIFEST_SHA256 = (
    "9b00b2613e0fe5669c614c608510db0d0239ce8f1904e99c0877a1e894652bee"
)
RUNTIME_V3_AUTHORITY_INTEGRATED = True
RUNTIME = {
    "job": {
        "schema_version": "mysql_python_runtime_authority_requirement_v1",
        "scenario": "job_memory",
        "host_id": "node02",
        "state": "TARGET_DEPLOYMENT_AND_FRESH_READBACK_REQUIRED",
        "python_version": "3.11.11",
        "runtime_install_root": (
            "/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/job/"
            "runtime_algorithm_c_successor_20260729/runtime"
        ),
        "content_address_algorithm":
            "sha256_canonical_relative_tree_entries_v3",
        "deployment_path_rule":
            "runtime_install_root/python311-<tree_content_sha256>",
        "runtime_content_address_state":
            "SOURCE_MANIFEST_SEALED_TARGET_READBACK_PENDING",
        "source_runtime_candidate": {
            "host_id": "node04",
            "path": "/home/anonymous/recost_runtime_py311_24_11_1",
            "python_version": "3.11.11",
            "authority_role": "candidate_only_not_reusable_without_hash",
        },
        "interpreter_path": (
            "/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/job/"
            "runtime_algorithm_c_successor_20260729/runtime/"
            f"python311-{PYTHON_TREE_CONTENT_SHA256}/bin/python"
        ),
        "interpreter_sha256":
            "05bd6d6d900bd990b8c00d17df3b3e1425d6c32f0408f07cd29bc495ac4aff39",
        "tree_sha256":
            "1d53a2c90b5a6cac15ffa5e1631c6ac04729accd271f6cd2a05e6b162dd9f9f7",
        "tree_sha256_semantics":
            "provenance_only_legacy_location_bound_inventory_digest",
        "tree_content_sha256": PYTHON_TREE_CONTENT_SHA256,
        "tree_content_identity_algorithm":
            "sha256_canonical_relative_tree_entries_v3",
        "portable_manifest_path": PYTHON_PORTABLE_MANIFEST_REL,
        "portable_manifest_sha256": PYTHON_PORTABLE_MANIFEST_SHA256,
        "source_manifest_sha256":
            "0db72f3e4e14b3902ff94d17eca9e5c8af97577efdf6ab8b3941fbe189da8c33",
        "exact_entry_set_readback_required": True,
        "directory_entry_set_readback_required": True,
        "file_sha256_size_mode_readback_required": True,
        "symlink_target_readback_required": True,
        "special_entries_rejected": True,
        "fresh_interpreter_and_tree_hash_readback_required": True,
        "python36_prohibited": True,
        "launch_authorized": False,
    },
    "tpch": {
        "schema_version": "mysql_python_runtime_authority_requirement_v1",
        "scenario": "tpch_memory",
        "host_id": "node04",
        "state": "SOURCE_MANIFEST_SEALED_FRESH_LIVE_READBACK_REQUIRED",
        "python_version": "3.11.11",
        "runtime_install_root": "/home/anonymous/recost_runtime_py311_24_11_1",
        "content_address_algorithm":
            "sha256_canonical_relative_tree_entries_v3",
        "deployment_path_rule":
            "existing_path_must_be_promoted_with_tree_and_interpreter_hash",
        "runtime_content_address_state":
            "SOURCE_MANIFEST_SEALED_TARGET_READBACK_PENDING",
        "source_runtime_candidate": None,
        "interpreter_path": (
            "/home/anonymous/recost_runtime_py311_24_11_1/bin/python"
        ),
        "interpreter_sha256":
            "05bd6d6d900bd990b8c00d17df3b3e1425d6c32f0408f07cd29bc495ac4aff39",
        "tree_sha256":
            "1d53a2c90b5a6cac15ffa5e1631c6ac04729accd271f6cd2a05e6b162dd9f9f7",
        "tree_sha256_semantics":
            "provenance_only_legacy_location_bound_inventory_digest",
        "tree_content_sha256": PYTHON_TREE_CONTENT_SHA256,
        "tree_content_identity_algorithm":
            "sha256_canonical_relative_tree_entries_v3",
        "portable_manifest_path": PYTHON_PORTABLE_MANIFEST_REL,
        "portable_manifest_sha256": PYTHON_PORTABLE_MANIFEST_SHA256,
        "source_manifest_sha256":
            "0db72f3e4e14b3902ff94d17eca9e5c8af97577efdf6ab8b3941fbe189da8c33",
        "exact_entry_set_readback_required": True,
        "directory_entry_set_readback_required": True,
        "file_sha256_size_mode_readback_required": True,
        "symlink_target_readback_required": True,
        "special_entries_rejected": True,
        "fresh_interpreter_and_tree_hash_readback_required": True,
        "python36_prohibited": True,
        "launch_authorized": False,
    },
}
JDK_RUNTIME = {
    "schema_version": "mysql_jdk_runtime_authority_requirement_v1",
    "source_host_id": "node04",
    "source_root":
        "/home/anonymous/recost_deploy_20260721_v1/runtime/"
        "jdk-23.0.2-oracle-x64",
    "tree_sha256":
        "5d6558a8c933daa4bbf412d18f90a4526c57988c9632a2114365e23b55df7139",
    "tree_sha256_semantics":
        "provenance_only_legacy_location_bound_inventory_digest",
    "tree_content_sha256": JDK_TREE_CONTENT_SHA256,
    "tree_content_identity_algorithm":
        "sha256_canonical_relative_tree_entries_v3",
    "portable_manifest_path": JDK_PORTABLE_MANIFEST_REL,
    "portable_manifest_sha256": JDK_PORTABLE_MANIFEST_SHA256,
    "java_sha256":
        "c8f339ba5d433ef5ecacef4de0640b0a35c99381804413bfbc24375a0410e51c",
    "source_manifest_sha256":
        "bf3b427e9510ac77fe6618a60f99f51e1562884d3e81f0cec3cd9f47bfa4852e",
    "exact_entry_set_readback_required": True,
    "directory_entry_set_readback_required": True,
    "file_sha256_size_mode_readback_required": True,
    "symlink_target_readback_required": True,
    "special_entries_rejected": True,
    "fresh_live_readback_required": True,
    "launch_authorized": False,
}


class BuildError(RuntimeError):
    pass


def jdk_runtime_authority(name: str) -> dict[str, Any]:
    result = copy.deepcopy(JDK_RUNTIME)
    result.update({
        "scenario": f"{name}_memory",
        "required_by_workload": name == "tpch",
        "target_host_id": "node04" if name == "tpch" else "node02",
        "target_root": (
            JDK_RUNTIME["source_root"] if name == "tpch" else None
        ),
    })
    return result


def canonical(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def sha_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def load_object(path: Path, label: str) -> dict[str, Any]:
    if not path.is_absolute() or not path.is_file() or path.is_symlink():
        raise BuildError(f"{label} absent, relative, or symlinked: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BuildError(f"{label} invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise BuildError(f"{label} must be an object")
    return value


def portable_runtime_manifest_payload(kind: str) -> bytes:
    if kind == "python":
        publication_path = PYTHON_PUBLICATION_MANIFEST
        portable_path = PYTHON_PORTABLE_MANIFEST
        expected_content_id = PYTHON_TREE_CONTENT_SHA256
        expected_payload_sha = PYTHON_PORTABLE_MANIFEST_SHA256
    elif kind == "jdk":
        publication_path = JDK_PUBLICATION_MANIFEST
        portable_path = JDK_PORTABLE_MANIFEST
        expected_content_id = JDK_TREE_CONTENT_SHA256
        expected_payload_sha = JDK_PORTABLE_MANIFEST_SHA256
    else:
        raise BuildError(f"unknown runtime manifest kind: {kind}")
    publication = load_object(
        publication_path, f"{kind} runtime publication manifest"
    )
    document = load_object(
        portable_path, f"{kind} portable runtime manifest"
    )
    try:
        publication_identity = runtime_tree.validate_publication(publication)
        verified = runtime_tree.validate_portable_manifest(document)
    except runtime_tree.RuntimeTreeError as exc:
        raise BuildError(
            f"{kind} portable runtime manifest invalid: {exc}"
        ) from exc
    payload = runtime_tree.canonical(document)
    if (
        document.get("schema_version")
        != "mysql_algorithm_c_runtime_tree_manifest_v3"
        or document.get("algorithm")
        != "sha256_canonical_relative_tree_entries_v3"
        or verified.get("tree_content_sha256") != expected_content_id
        or publication_identity.get("tree_content_sha256")
        != expected_content_id
        or document.get("source_provenance", {}).get(
            "source_manifest_sha256"
        )
        != sha256(publication_path)
        or sha_bytes(payload) != expected_payload_sha
        or sha256(portable_path) != expected_payload_sha
    ):
        raise BuildError(f"{kind} portable runtime authority differs")
    return payload


def qualification_sql_payloads(
    name: str, source: Path
) -> dict[str, bytes]:
    if name == "job":
        expected_names = {
            "01_job_1a.sql",
            "02_job_5a.sql",
            "03_job_9a.sql",
            "04_job_12a.sql",
            "05_job_16a.sql",
            "06_job_20a.sql",
            "07_job_26a.sql",
            "08_job_33a.sql",
            "09_job_sensitive_title_cast_role5_movie100k.sql",
        }
        candidates = (
            source / "qualification/sql/job",
            source
            / "config/formal/mysql_sgd_production/workloads/job_v1",
            predecessor("job")[1]
            / "config/formal/mysql_sgd_production/workloads/job_v1",
        )
        root = next(
            (
                candidate
                for candidate in candidates
                if candidate.is_dir()
                and {
                    path.name for path in candidate.glob("*.sql")
                    if path.name in expected_names
                }
                == expected_names
            ),
            candidates[-1],
        )
    elif name == "tpch":
        registry = load_object(
            TPCH_SQL_REGISTRY / "registry_seal.json",
            "TPC-H production SQL registry",
        )
        root = TPCH_SQL_REGISTRY / "production_bindings"
        expected_names = {"Q%d.sql" % index for index in range(1, 23)}
        files = registry.get("files") or {}
        for filename in expected_names:
            path = root / filename
            if (
                filename not in files
                or files[filename].get("path") != str(path)
                or files[filename].get("sha256") != sha256(path)
            ):
                raise BuildError(
                    f"TPC-H production SQL registry differs: {filename}"
                )
    else:
        raise BuildError(f"unknown qualification SQL scenario: {name}")
    actual_names = {
        path.name for path in root.glob("*.sql")
        if path.name in expected_names
    }
    if actual_names != expected_names:
        raise BuildError(f"{name} qualification SQL closure differs")
    return {
        f"qualification/sql/{name}/{filename}":
            (root / filename).read_bytes()
        for filename in sorted(expected_names)
    }


def distributed_runtime_contract(
    name: str,
    layout: Mapping[str, Any],
    env: Mapping[str, Any],
) -> dict[str, Any]:
    global_contract = load_object(
        FOUR_SCENE_CONTRACT, "four-scene distributed contract"
    )
    scene = global_contract["scenarios"][name]
    if (
        scene["scenario"] != layout["scenario"]
        or scene["storage"]["device"] != layout["storage_device"]
        or scene["storage"]["expected_live_major_minor"]
        != layout["storage_device_major_minor"]
    ):
        raise BuildError(f"{name} four-scene storage authority differs")
    resource = scene["resource_envelope"]
    expected_resource = {
        "cpu_quota_cores": env["cpu_quota_cores"],
        "cpuset_cpus": layout["cpuset_cpus"],
        "cpuset_mems": layout["cpuset_mems"],
        "memory_bytes": env["memory_bytes"],
        "memory_swap_bytes": env["memory_swap_bytes"],
        "pids_max": env["pids_max"],
        "io_read_bps": env["io_read_bps"],
        "io_write_bps": env["io_write_bps"],
        "innodb_buffer_pool_bytes": env["buffer_pool_bytes"],
        "mysql_port": layout["planner_port"],
    }
    if resource != expected_resource:
        raise BuildError(f"{name} four-scene resource authority differs")
    runtime = RUNTIME[name]
    toolchain = {
        "python_required": True,
        "python_runtime_root": runtime["runtime_install_root"],
        "python_runtime": runtime["interpreter_path"],
        "python_tree_content_sha256": PYTHON_TREE_CONTENT_SHA256,
        "python_tree_content_identity_algorithm":
            "sha256_canonical_relative_tree_entries_v3",
        "python_tree_manifest":
            "runtime/python_runtime_tree_manifest_v3.json",
        "python_binary_relative": "bin/python3.11",
        "python_binary_sha256": runtime["interpreter_sha256"],
        "python_version_expected": "Python 3.11.11",
        "java_required": name == "tpch",
    }
    if name == "tpch":
        jdk = jdk_runtime_authority(name)
        toolchain.update({
            "deployment_target": jdk["source_root"],
            "tree_content_sha256": JDK_TREE_CONTENT_SHA256,
            "tree_content_identity_algorithm":
                "sha256_canonical_relative_tree_entries_v3",
            "tree_manifest": "runtime/jdk_runtime_tree_manifest_v3.json",
            "java_binary_relative": "bin/java",
            "java_binary_sha256": jdk["java_sha256"],
            "release_sha256":
                "c0cb3f9c13a88cac28beb94aa6139473af9ce3ecdb6f3fb17589d8cdb470648d",
        })
    return {
        "schema_version": "mysql_algorithm_c_distributed_runtime_contract_v1",
        "scenario": layout["scenario"],
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "data_plane_launch_authority": "main_agent_only",
        "host": scene["host"],
        "storage": scene["storage"],
        "resource_envelope": expected_resource,
        "toolchain": toolchain,
        "qualification_policy": {
            "debug_only": True,
            "main_agent_authorization_required": True,
            "duration_seconds": 180,
            "sample_period_seconds": 1,
            "minimum_samples_per_stream": 181,
            "dynamic_gate_schema":
                "mysql_algorithm_c_distributed_dynamic_gate_evidence_v1",
            "all_required_streams_missing_or_timeout_result": "FAIL",
            "capture_r0_formal_accounting_prohibited": True,
        },
        "live_readback": {
            "same_attempt_required": True,
            "collector_and_qualification_entrypoint":
                "qualification/mysql_distributed_live_readback_qualification_v1.py",
            "workload_metric_adapter":
                "qualification/mysql_sql_debug_metric_adapter_v1.py",
            "workload_metric_schema":
                "mysql_algorithm_c_debug_workload_metric_sample_v1",
            "collect_only_is_read_only": True,
            "qualification_main_agent_token_and_sidecars_required": True,
            "missing_or_timeout_result": "FAIL",
        },
        "fresh_r0_required": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    }


def qualification_workload_contract(
    name: str, layout: Mapping[str, Any]
) -> dict[str, Any]:
    if name == "job":
        database = "imdb_job"
        concurrency = 1
        weights = "10,10,10,10,10,10,30,10,7.5"
    elif name == "tpch":
        database = "tpch"
        concurrency = 4
        weights = ",".join(
            "16" if index == 18 else "4"
            for index in range(1, 23)
        )
    else:
        raise BuildError(f"unknown qualification workload: {name}")
    return {
        "schema_version":
            "mysql_algorithm_c_debug_sql_workload_contract_v1",
        "scenario": layout["scenario"],
        "debug_only": True,
        "capture_authorized": False,
        "formal_r0_authorized": False,
        "formal_authorized": False,
        "accepted_round_authorized": False,
        "native_metric_adapter": {
            "schema_version":
                "mysql_algorithm_c_debug_workload_metric_adapter_v1",
            "kind": "sql_query_native",
            "sample_schema":
                "mysql_algorithm_c_debug_workload_metric_sample_v1",
            "completed_total_source":
                "successful_mysql_client_process_return",
            "latency_source":
                "exact_mysql_client_invocation_elapsed_monotonic_time",
            "synthetic_metric_prohibited": True,
            "mysql_questions_as_throughput_prohibited": True,
        },
        "query_closure": {
            "directory_relative": f"qualification/sql/{name}",
            "file_count": 9 if name == "job" else 22,
            "production_sql_only": True,
        },
        "invocation": {
            "python_relative":
                "qualification/mysql_sql_debug_metric_adapter_v1.py",
            "mysql_binary": "/usr/bin/mysql",
            "host": "127.0.0.1",
            "port": layout["planner_port"],
            "database": database,
            "user": "root",
            "concurrency": concurrency,
            "weights": weights,
            "seed": 4508333,
            "query_timeout_seconds": 600,
            "adapter_lifetime_seconds": 240,
            "collector_window_seconds": 180,
        },
        "command_spec_binding": {
            "attempt_id_required": True,
            "metric_stream_absolute_path_required": True,
            "defaults_extra_file_absolute_path_required": True,
            "all_argv_code_files_hash_bound_required": True,
            "main_agent_token_and_sidecar_required": True,
        },
    }


def readonly(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        path.chmod(0o555 if path.is_dir() else 0o444)
    root.chmod(0o555)


def remove_private_stage(root: Path) -> None:
    """Remove only a builder-owned, explicitly named temporary directory."""
    if not root.name.startswith(".") or f".tmp.{os.getpid()}" not in root.name:
        raise BuildError(f"refusing to remove non-private stage: {root}")
    if root.exists():
        for path in sorted(root.rglob("*")):
            if path.is_dir():
                path.chmod(0o700)
            else:
                path.chmod(0o600)
        root.chmod(0o700)
        shutil.rmtree(root)


def verify_file_map(root: Path, files: Mapping[str, Any], label: str) -> None:
    if not files:
        raise BuildError(f"{label} closure is empty")
    for relative, digest in files.items():
        path = root / str(relative)
        if (
            not path.is_file()
            or path.is_symlink()
            or sha256(path) != digest
        ):
            raise BuildError(f"{label} closure differs: {relative}")


def predecessor(name: str) -> tuple[dict[str, Any], Path, dict[str, Any]]:
    bundle = PREDECESSORS[name]
    manifest = load_object(bundle / "bundle_manifest.json", f"{name} manifest")
    if manifest.get("bundle_id") != bundle.name:
        raise BuildError(f"{name} predecessor identity differs")
    verify_file_map(bundle, manifest.get("files") or {}, f"{name} predecessor")
    source = Path(str(manifest.get("source_directory") or ""))
    source_manifest = load_object(
        source / "source_manifest.json", f"{name} source manifest"
    )
    if source_manifest.get("bundle_id") != manifest.get("source_bundle_id"):
        raise BuildError(f"{name} predecessor source identity differs")
    verify_file_map(
        source, source_manifest.get("files") or {}, f"{name} predecessor source"
    )
    return manifest, source, source_manifest


def lane_block(name: str, layout: Mapping[str, Any], env: Mapping[str, Any]) -> str:
    if name == "job":
        database = "imdb_job"
        target = "memory"
        counter = "Com_select"
        experience = (
            '(str(HISTORY / "job/job_plan_effect_t_20260723T003507Z/'
            'T.mysql_job_physical_plan_effect_v1.json"),)'
        )
    else:
        database = "tpch_sf10"
        target = "memory"
        counter = "Com_select"
        experience = (
            "(\n"
            '            str(ROOT / "config/formal/'
            'frozen_mysql_experience_table_source_policy_20260723_v1/'
            'source_policy.json"),\n'
            '            str(ROOT / "history/'
            'brute_formal_mixed_20260722_v6_remaining_v1/mysql/brute_force/'
            'A7_r1"),\n'
            "        )"
        )
    return (
        f'    "{name}": Lane(\n'
        f'        "{name}", "{name}_memory", "{name}", '
        f'{layout["ssh_target"]!r}, {layout["host_id"]!r},\n'
        f'        {layout["remote_project_path"]!r},\n'
        f'        {layout["remote_template_path"]!r},\n'
        f'        {layout["storage_device"]!r}, '
        f'{layout["cpuset_cpus"]!r}, {layout["cpuset_mems"]!r}, '
        f'{env["cpu_quota_cores"]!r}, {env["memory_bytes"]}, '
        f'{env["pids_max"]},\n'
        f'        {env["io_read_bps"]}, {env["io_write_bps"]}, '
        f'{layout["planner_port"]}, {database!r}, {target!r}, {counter!r},\n'
        f"        {experience},\n"
        f'        buffer_pool_bytes={env["buffer_pool_bytes"]},\n'
        "    ),"
    )


def patch_manual_campaign(
    payload: bytes,
    name: str,
    layout: Mapping[str, Any],
    env: Mapping[str, Any],
) -> bytes:
    text = payload.decode("utf-8")
    following = "xp" if name == "job" else "tpcc"
    pattern = (
        rf'    "{name}": Lane\(\n.*?\n    \),\n'
        rf'    "{following}": Lane\('
    )
    replacement = (
        lane_block(name, layout, env)
        + f'\n    "{following}": Lane('
    )
    text, count = re.subn(pattern, replacement, text, count=1, flags=re.DOTALL)
    if count != 1:
        raise BuildError(f"{name} remote Lane replacement cardinality differs")
    if text.count("cp -a --reflink=auto") != 1:
        raise BuildError(f"{name} predecessor reflink copy cardinality differs")
    text = text.replace(
        "cp -a --reflink=auto", "cp -a --reflink=never", 1
    )
    old_lock = (
        'CONTROL_PLANE_SSH_LOCK = HISTORY / '
        '"control_plane/compute00_remote_ssh.lock"'
    )
    if text.count(old_lock) != 1:
        raise BuildError(
            f"{name} control-plane lock replacement cardinality differs"
        )
    text = text.replace(
        old_lock,
        'CONTROL_PLANE_SSH_LOCK = Path(os.environ.get(\n'
        '    "RA_CONTROL_PLANE_SSH_LOCK",\n'
        '    str(HISTORY / "control_plane/distributed_remote_ssh.lock"),\n'
        "))",
        1,
    )
    old_java = 'JAVA = Path("/usr/bin/java")'
    if text.count(old_java) != 1:
        raise BuildError(f"{name} Java replacement cardinality differs")
    text = text.replace(
        old_java,
        'JAVA = Path(os.environ.get("RA_JAVA_BINARY", "/usr/bin/java"))',
        1,
    )
    return text.encode("utf-8")


def profile_payload(
    layout: Mapping[str, Any], env: Mapping[str, Any]
) -> bytes:
    return canonical({
        "cpuset": layout["cpuset_cpus"],
        "mems": layout["cpuset_mems"],
        "cpu_cores": env["cpu_quota_cores"],
        "memory_bytes": env["memory_bytes"],
        "pids": env["pids_max"],
        "read_bps": env["io_read_bps"],
        "write_bps": env["io_write_bps"],
        "buffer_pool_bytes": env["buffer_pool_bytes"],
    })


def staged_service_payload(
    name: str, layout: Mapping[str, Any], env: Mapping[str, Any]
) -> bytes:
    expected = {
        "scenario": f"{name}_memory",
        "service_factory": SERVICE,
        "host_id": layout["host_id"],
        "ssh_target": layout["ssh_target"],
        "remote_project_path": layout["remote_project_path"],
        "remote_template_path": layout["remote_template_path"],
        "storage_device": layout["storage_device"],
        "storage_device_major_minor": layout["storage_device_major_minor"],
        "cpuset_cpus": layout["cpuset_cpus"],
        "cpuset_mems": layout["cpuset_mems"],
        "planner_host_port": layout["planner_port"],
        "replay_host_port": layout["replay_port"],
        **dict(env),
    }
    text = f'''"""Fail-closed staged distributed service authority."""
from __future__ import annotations
from typing import Any, Mapping

EXPECTED = {expected!r}

class StagedDistributedAuthorityError(RuntimeError):
    pass

def validate_static_authority(config: Mapping[str, Any]) -> None:
    production = config.get("production") or {{}}
    resource = config.get("execution_resource_authority") or {{}}
    storage = config.get("execution_storage_authority") or {{}}
    endpoint = config.get("distributed_endpoint_authority") or {{}}
    runtime = config.get("runtime_authority") or {{}}
    if config.get("formal_scope") != "mysql_only" or config.get("cross_engine_comparison_prohibited") is not True:
        raise StagedDistributedAuthorityError("mysql_only scope differs")
    if production.get("service_factory") != EXPECTED["service_factory"]:
        raise StagedDistributedAuthorityError("service factory differs")
    if production.get("launch_authorized") is not False or config.get("accepted_round_authorized") is not False:
        raise StagedDistributedAuthorityError("staged authority must remain non-launchable")
    for key in ("host_id", "cpuset_cpus", "cpuset_mems", "storage_device", "storage_device_major_minor", "cpu_quota_cores", "memory_bytes", "memory_swap_bytes", "io_read_bps", "io_write_bps", "pids_max", "buffer_pool_bytes"):
        if resource.get(key) != EXPECTED[key]:
            raise StagedDistributedAuthorityError("resource authority differs at " + key)
    for key, expected_key in (("host_id", "host_id"), ("template_path", "remote_template_path"), ("device", "storage_device"), ("device_major_minor", "storage_device_major_minor")):
        if storage.get(key) != EXPECTED[expected_key]:
            raise StagedDistributedAuthorityError("storage authority differs at " + key)
    for key in ("ssh_target", "remote_project_path", "remote_template_path", "planner_host_port", "replay_host_port"):
        if endpoint.get(key) != EXPECTED[key]:
            raise StagedDistributedAuthorityError("endpoint authority differs at " + key)
    if endpoint.get("container_port") != 3306 or endpoint.get("replay_host_port") != endpoint.get("planner_host_port") + 100:
        raise StagedDistributedAuthorityError("planner/replay port authority differs")
    mysql = config.get("mysql") or {{}}
    if mysql.get("host") != "127.0.0.1" or mysql.get("port") != EXPECTED["planner_host_port"]:
        raise StagedDistributedAuthorityError("MySQL data endpoint mirror differs")
    if (
        runtime.get("launch_authorized") is not False
        or runtime.get("fresh_interpreter_and_tree_hash_readback_required") is not True
        or runtime.get("tree_content_identity_algorithm")
        != "sha256_canonical_relative_tree_entries_v3"
        or runtime.get("tree_sha256_semantics")
        != "provenance_only_legacy_location_bound_inventory_digest"
        or runtime.get("exact_entry_set_readback_required") is not True
        or runtime.get("directory_entry_set_readback_required") is not True
        or runtime.get("file_sha256_size_mode_readback_required") is not True
        or runtime.get("symlink_target_readback_required") is not True
        or runtime.get("special_entries_rejected") is not True
    ):
        raise StagedDistributedAuthorityError("runtime must remain pending fresh hash readback")

def build(config: Mapping[str, Any]) -> Mapping[str, Any]:
    validate_static_authority(config)
    raise StagedDistributedAuthorityError(
        "STAGED_NOT_LAUNCH_AUTHORIZED: fresh remote runtime, resource, "
        "storage and interference evidence must be promoted into a new bundle"
    )
'''
    return text.encode("utf-8")


def staged_verifier_payload() -> bytes:
    return b'''"""Readiness verifier for non-launchable distributed staging."""
from __future__ import annotations
from typing import Any, Mapping

def verify_document(document: Mapping[str, Any]) -> list[str]:
    problems = []
    if document.get("status") != "STAGED_NOT_LAUNCH_AUTHORIZED":
        problems.append("staged status differs")
    if document.get("launch_authorized") is not False:
        problems.append("launch authority must be false")
    if document.get("accepted_round_authorized") is not False:
        problems.append("accepted authority must be false")
    if document.get("fresh_remote_authorization_required") is not True:
        problems.append("fresh remote authorization requirement absent")
    return problems

def verify_ready(document: Mapping[str, Any]) -> None:
    problems = verify_document(document)
    if not problems:
        problems.append("staged successor is intentionally non-launchable")
    raise RuntimeError("NOT_READY: " + "; ".join(problems))
'''


def source_payloads(
    name: str,
    source: Path,
    source_manifest: Mapping[str, Any],
    layout: Mapping[str, Any],
    env: Mapping[str, Any],
) -> dict[str, bytes]:
    result = {
        str(relative): (source / str(relative)).read_bytes()
        for relative in (source_manifest.get("files") or {})
    }
    manual = "shell/manual_mysql_sgd_r0_campaign.py"
    result[manual] = patch_manual_campaign(result[manual], name, layout, env)
    result[PROFILE_REL[name]] = profile_payload(layout, env)
    result[
        "shell/mysql_algorithm_c_distributed_successor_staged_v1.py"
    ] = staged_service_payload(name, layout, env)
    result[VERIFIER] = staged_verifier_payload()
    result[
        "shell/runtime_tree_content_identity_v3.py"
    ] = RUNTIME_TREE_HELPER.read_bytes()
    result[PYTHON_PORTABLE_MANIFEST_REL] = (
        portable_runtime_manifest_payload("python")
    )
    result[JDK_PORTABLE_MANIFEST_REL] = (
        portable_runtime_manifest_payload("jdk")
    )
    result[
        "qualification/mysql_distributed_live_readback_qualification_v1.py"
    ] = QUALIFICATION_COLLECTOR.read_bytes()
    result[
        "qualification/mysql_sql_debug_metric_adapter_v1.py"
    ] = QUALIFICATION_METRIC_ADAPTER.read_bytes()
    result[
        "qualification/runtime_tree_content_identity_v3.py"
    ] = RUNTIME_TREE_HELPER.read_bytes()
    result["distributed_runtime_contract.json"] = canonical(
        distributed_runtime_contract(name, layout, env)
    )
    result["qualification/debug_sql_workload_contract.json"] = canonical(
        qualification_workload_contract(name, layout)
    )
    result.update(qualification_sql_payloads(name, source))
    return result


def verify_source(
    path: Path,
    identity: Mapping[str, Any],
    source_id: str,
    *,
    require_directory_name: bool = True,
) -> dict[str, Any]:
    manifest = load_object(path / "source_manifest.json", "successor source")
    expected = {**identity, "bundle_id": source_id}
    if (
        manifest != expected
        or (
            require_directory_name
            and path.name != f"source-{source_id}"
        )
    ):
        raise BuildError("successor source identity differs")
    verify_file_map(path, identity["files"], "successor source")
    return manifest


def publish_source(
    name: str,
    predecessor_source: Path,
    predecessor_manifest: Mapping[str, Any],
    layout: Mapping[str, Any],
    env: Mapping[str, Any],
    contract_sha: str,
    execute: bool,
) -> tuple[Path, str, str]:
    payloads = source_payloads(
        name, predecessor_source, predecessor_manifest, layout, env
    )
    identity = {
        "schema_version":
            "mysql_distributed_successor_source_closure_v1",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "scenario": f"{name}_memory",
        "staged_not_launch_authorized": True,
        "predecessor_source_bundle_id": predecessor_manifest["bundle_id"],
        "predecessor_source_manifest_sha256":
            sha256(predecessor_source / "source_manifest.json"),
        "contract_sha256": contract_sha,
        "builder_sha256": sha256(BUILDER),
        "files": {
            relative: sha_bytes(payload)
            for relative, payload in sorted(payloads.items())
        },
    }
    source_id = sha_bytes(canonical(identity))
    final = SOURCE_ROOTS[name] / f"source-{source_id}"
    if final.exists():
        verify_source(final, identity, source_id)
        return final, source_id, sha256(final / "source_manifest.json")
    if not execute:
        manifest_sha = sha_bytes(canonical({**identity, "bundle_id": source_id}))
        return final, source_id, manifest_sha
    SOURCE_ROOTS[name].mkdir(parents=True, exist_ok=True)
    stage = SOURCE_ROOTS[name] / f".source-{source_id}.tmp.{os.getpid()}"
    stage.mkdir()
    try:
        for relative, payload in payloads.items():
            path = stage / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        (stage / "source_manifest.json").write_bytes(
            canonical({**identity, "bundle_id": source_id})
        )
        verify_source(
            stage, identity, source_id, require_directory_name=False
        )
        readonly(stage)
        try:
            stage.rename(final)
        except FileExistsError:
            remove_private_stage(stage)
            verify_source(final, identity, source_id)
    except BaseException:
        if stage.exists():
            remove_private_stage(stage)
        raise
    return final, source_id, sha256(final / "source_manifest.json")


def successor_config(
    name: str,
    predecessor_config: Mapping[str, Any],
    source: Path,
    source_id: str,
    source_manifest_sha: str,
    layout: Mapping[str, Any],
    env: Mapping[str, Any],
) -> dict[str, Any]:
    result = copy.deepcopy(dict(predecessor_config))
    profile = source / PROFILE_REL[name]
    resource = dict(result.get("execution_resource_authority") or {})
    resource.update({
        "execution_authorized": False,
        "host_id": layout["host_id"],
        "cpuset_cpus": layout["cpuset_cpus"],
        "cpuset_mems": layout["cpuset_mems"],
        "storage_device": layout["storage_device"],
        "storage_device_major_minor":
            layout["storage_device_major_minor"],
        "profile_path": str(profile),
        "profile_sha256": sha256(profile) if profile.is_file()
        else sha_bytes(profile_payload(layout, env)),
        **dict(env),
    })
    storage = dict(result.get("execution_storage_authority") or {})
    storage.update({
        "execution_authorized": False,
        "host_id": layout["host_id"],
        "template_path": layout["remote_template_path"],
        "manifest_path":
            layout["remote_project_path"] + "/template_manifest.json",
        "manifest_sha256": None,
        "profile_id": f"{name}_distributed_successor_v1",
        "device": layout["storage_device"],
        "device_major_minor": layout["storage_device_major_minor"],
        "fresh_copy_mode": "full_physical_copy_no_overlay_no_reflink",
        "overlay": False,
        "reflink": False,
        "fresh_remote_manifest_readback_required": True,
    })
    production = dict(result.get("production") or {})
    production.update({
        "service_factory": SERVICE,
        "launch_authorized": False,
    })
    result.update({
        "schema_version":
            "mysql_distributed_successor_staged_config_v1",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "accepted_round_authorized": False,
        "outer_execution_authorized": False,
        "authorization_stage": "staged_not_launch_authorized",
        "production": production,
        "profile": str(profile),
        "execution_resource_authority": resource,
        "execution_storage_authority": storage,
        "runtime_authority": copy.deepcopy(RUNTIME[name]),
        "jdk_runtime_authority": jdk_runtime_authority(name),
        "distributed_endpoint_authority": {
            "authority_role": "sole_execution_authority",
            "execution_authorized": False,
            "host_id": layout["host_id"],
            "ssh_target": layout["ssh_target"],
            "remote_project_path": layout["remote_project_path"],
            "remote_template_path": layout["remote_template_path"],
            "planner_host_port": layout["planner_port"],
            "replay_host_port": layout["replay_port"],
            "container_port": 3306,
            "port_semantics":
                "planner_and_replay_are_distinct_host_data_ports",
            "cli_override_prohibited": True,
        },
        "fresh_remote_authorization": {
            "status": "REQUIRED",
            "historical_readback_reusable": False,
            "fresh_in_band_r0_required": True,
            "readback_requirement_sibling":
                "remote_readback_requirement.json",
        },
        "source_bundle": {
            "source": str(source),
            "source_bundle_id": source_id,
            "source_manifest_sha256": source_manifest_sha,
        },
    })
    workload = dict(result.get("workload") or {})
    workload["host_control_cpuset"] = layout["cpuset_cpus"]
    workload["fresh_copy_mode"] = (
        "full_physical_copy_no_overlay_no_reflink"
    )
    workload["fresh_full_physical_copy_required"] = True
    result["workload"] = workload
    mysql = dict(result.get("mysql") or {})
    mysql.update({
        "host": "127.0.0.1",
        "port": layout["planner_port"],
    })
    result["mysql"] = mysql
    provenance = dict(result.get("provenance_only") or {})
    provenance.update({
        "authority_role": "provenance_only",
        "execution_authorized": False,
        "may_participate_in_launch": False,
        "predecessor_local_storage": {
            key: result.pop(key)
            for key in (
                "local_storage_profile_id",
                "local_storage_manifest",
                "local_storage_manifest_sha256",
            )
            if key in result
        },
    })
    result["provenance_only"] = provenance
    output = OUTPUT_ROOTS[name]
    result["output"] = {
        **dict(result.get("output") or {}),
        "directory": str(output),
    }
    outer = dict(result.get("formal_outer_protocol") or {})
    outer.update({
        "formal_checkpoint_path": str(
            output / "formal_outer_checkpoint.json"
        ),
        "formal_real_r0_checkpoint_path": str(output / "formal_real_r0.json"),
    })
    result["formal_outer_protocol"] = outer
    if isinstance(result.get("early_stop_source_bundle"), Mapping):
        early = dict(result["early_stop_source_bundle"])
        early.update({
            "source": str(source),
            "source_bundle_id": source_id,
            "source_manifest_sha256": source_manifest_sha,
        })
        result["early_stop_source_bundle"] = early
    mutex = dict(result.get("host02_job_xp_mutex") or {})
    mutex.update({
        "required": False,
        "execution_authority": False,
        "mode": "superseded_by_distributed_host_specific_interference_gate",
    })
    mutex.pop("lock_path", None)
    mutex.pop("journal_path", None)
    result["host02_job_xp_mutex"] = mutex
    return result


def readiness_payload(
    name: str,
    source_id: str,
    config_sha: str,
    layout: Mapping[str, Any],
    env: Mapping[str, Any],
) -> bytes:
    return canonical({
        "schema_version":
            "mysql_distributed_successor_staged_readiness_v1",
        "status": "STAGED_NOT_LAUNCH_AUTHORIZED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "scenario": f"{name}_memory",
        "launch_authorized": False,
        "accepted_round_authorized": False,
        "fresh_remote_authorization_required": True,
        "historical_r0_or_readback_reusable": False,
        "source_bundle_id": source_id,
        "config_sha256": config_sha,
        "service_factory": SERVICE,
        "layout": dict(layout),
        "preserved_envelope": dict(env),
        "runtime_authority": copy.deepcopy(RUNTIME[name]),
        "jdk_runtime_authority": jdk_runtime_authority(name),
    })


def launcher_payload(name: str, source: Path) -> bytes:
    launcher = LAUNCHER_NAME[name]
    expected_runtime = RUNTIME[name]["interpreter_path"]
    text = f'''#!/usr/bin/env python3
"""Immutable staged launcher; no remote data-plane action is authorized."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import sys

SOURCE = Path({str(source)!r})
SERVICE = {SERVICE!r}
VERIFIER = SOURCE / {VERIFIER!r}
DEBUG = SOURCE / "shell/debug_mysql_gradient_round_v25.py"
EXPECTED_RUNTIME = {expected_runtime!r}
FORBIDDEN_OVERRIDES = {{
    "--config", "--readiness-manifest", "--service-factory",
    "--r0-authority-readiness-manifest", "--resume-directory",
    "--profile-json", "--host", "--port", "--project", "--template",
    "--device", "--cpuset", "--mems", "--runtime",
}}

def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def verify_staged() -> dict:
    bundle = Path(__file__).resolve().parent
    manifest = json.loads((bundle / "bundle_manifest.json").read_text())
    if manifest.get("bundle_id") != bundle.name:
        raise RuntimeError("bundle identity differs")
    for relative, digest in (manifest.get("files") or {{}}).items():
        path = bundle / relative
        if not path.is_file() or path.is_symlink() or sha256(path) != digest:
            raise RuntimeError("bundle closure differs: " + relative)
    config = json.loads((bundle / "config.json").read_text())
    if config.get("production", {{}}).get("service_factory") != SERVICE:
        raise RuntimeError("service factory differs")
    if config.get("production", {{}}).get("launch_authorized") is not False:
        raise RuntimeError("staged launch boundary differs")
    runtime = config.get("runtime_authority") or {{}}
    if runtime.get("interpreter_path") != EXPECTED_RUNTIME:
        raise RuntimeError("runtime binding differs")
    return {{
        "status": "STAGED_NOT_LAUNCH_AUTHORIZED",
        "bundle_id": bundle.name,
        "remote_commands_executed": False,
    }}

if __name__ == "__main__":
    if sys.argv[1:] == ["--verify-staged"]:
        print(json.dumps(verify_staged(), sort_keys=True))
        raise SystemExit(0)
    verify_staged()
    if any(arg in FORBIDDEN_OVERRIDES for arg in sys.argv[1:]):
        raise RuntimeError("authority parameter override is prohibited")
    raise RuntimeError(
        "STAGED_NOT_LAUNCH_AUTHORIZED: publish fresh runtime/readback/"
        "interference evidence into a new immutable promotion first"
    )
'''
    if launcher not in text:
        pass
    return text.encode("utf-8")


def remote_requirement(
    name: str,
    layout: Mapping[str, Any],
    env: Mapping[str, Any],
) -> bytes:
    return canonical({
        "schema_version":
            "mysql_distributed_fresh_remote_readback_requirement_v1",
        "status": "FRESH_REMOTE_IN_BAND_READBACK_REQUIRED",
        "scenario": f"{name}_memory",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "accepted_round_authorized": False,
        "expected_layout": dict(layout),
        "expected_envelope": dict(env),
        "expected_runtime": copy.deepcopy(RUNTIME[name]),
        "expected_jdk_runtime": jdk_runtime_authority(name),
        "required_gates": [
            "hostname_and_ssh_target_identity",
            "template_manifest_and_tree_checksum",
            "project_template_and_mount_readback",
            "lsblk_device_major_minor",
            "docker_and_live_cgroup_resource_readback",
            "mysql_buffer_pool_and_fixed_settings",
            "same_host_process_cpu_numa_device_port_inventory",
            "versioned_rolling_window_dynamic_interference_pass",
            "planner_and_replay_ports_free",
            "fresh_in_band_r0",
        ],
    })


def method_payload(
    predecessor_bundle: Path,
    name: str,
    source: Path,
    source_id: str,
    layout: Mapping[str, Any],
) -> bytes:
    old = load_object(
        predecessor_bundle / "experience_table_sgd_method_manifest.json",
        f"{name} predecessor method",
    )
    result = copy.deepcopy(old)
    result["distributed_successor"] = {
        "schema_version": "mysql_distributed_successor_method_binding_v1",
        "status": "STAGED_NOT_LAUNCH_AUTHORIZED",
        "source_directory": str(source),
        "source_bundle_id": source_id,
        "service_factory": SERVICE,
        "layout": dict(layout),
        "runtime_authority": copy.deepcopy(RUNTIME[name]),
        "jdk_runtime_authority": jdk_runtime_authority(name),
        "original_gradient_algorithm_unchanged": True,
        "all_nonempty_subspaces_and_fixed_top5_unchanged": True,
        "qps_only_outer_acceptance_unchanged": True,
    }
    return canonical(result)


def bundle_payloads(
    name: str,
    predecessor_bundle: Path,
    source: Path,
    source_id: str,
    source_manifest_sha: str,
    config: Mapping[str, Any],
    layout: Mapping[str, Any],
    env: Mapping[str, Any],
) -> dict[str, bytes]:
    predecessor_manifest = load_object(
        predecessor_bundle / "bundle_manifest.json",
        f"{name} predecessor bundle manifest",
    )
    payloads = {
        "config.json": canonical(config),
        LAUNCHER_NAME[name]: launcher_payload(name, source),
        "experience_table_sgd_method_manifest.json": method_payload(
            predecessor_bundle, name, source, source_id, layout
        ),
        "remote_readback_requirement.json":
            remote_requirement(name, layout, env),
        "runtime_authority.json": canonical(RUNTIME[name]),
        "jdk_runtime_authority.json": canonical(
            jdk_runtime_authority(name)
        ),
        "runtime/runtime_tree_content_identity_v3.py":
            RUNTIME_TREE_HELPER.read_bytes(),
        "runtime/python_runtime_tree_manifest_v3.json":
            portable_runtime_manifest_payload("python"),
        "runtime/jdk_runtime_tree_manifest_v3.json":
            portable_runtime_manifest_payload("jdk"),
        "qualification/mysql_distributed_live_readback_qualification_v1.py":
            QUALIFICATION_COLLECTOR.read_bytes(),
        "qualification/mysql_sql_debug_metric_adapter_v1.py":
            QUALIFICATION_METRIC_ADAPTER.read_bytes(),
        "qualification/runtime_tree_content_identity_v3.py":
            RUNTIME_TREE_HELPER.read_bytes(),
        "distributed_runtime_contract.json": canonical(
            distributed_runtime_contract(name, layout, env)
        ),
        "qualification/debug_sql_workload_contract.json": canonical(
            qualification_workload_contract(name, layout)
        ),
        "predecessor/bundle_manifest.json":
            (predecessor_bundle / "bundle_manifest.json").read_bytes(),
    }
    payloads.update(qualification_sql_payloads(name, source))
    for relative in predecessor_manifest.get("files") or {}:
        payloads[f"predecessor/{relative}"] = (
            predecessor_bundle / relative
        ).read_bytes()
    config_sha = sha_bytes(payloads["config.json"])
    readiness = readiness_payload(
        name, source_id, config_sha, layout, env
    )
    payloads[
        f"readiness_manifest-{sha_bytes(readiness)}.json"
    ] = readiness
    payloads["source_pointer.json"] = canonical({
        "source_directory": str(source),
        "source_bundle_id": source_id,
        "source_manifest_sha256": source_manifest_sha,
    })
    return payloads


def verify_bundle(
    path: Path,
    identity: Mapping[str, Any],
    bundle_id: str,
    *,
    require_directory_name: bool = True,
) -> dict[str, Any]:
    manifest = load_object(path / "bundle_manifest.json", "successor bundle")
    expected = {**identity, "bundle_id": bundle_id}
    if (
        manifest != expected
        or (require_directory_name and path.name != bundle_id)
    ):
        raise BuildError("successor bundle identity differs")
    if sha256(path / "closure.json") != bundle_id:
        raise BuildError("successor closure content address differs")
    verify_file_map(path, manifest["files"], "successor bundle")
    return manifest


def publish_bundle(
    name: str,
    predecessor_bundle: Path,
    predecessor_manifest: Mapping[str, Any],
    source: Path,
    source_id: str,
    source_manifest_sha: str,
    config: Mapping[str, Any],
    layout: Mapping[str, Any],
    env: Mapping[str, Any],
    contract_sha: str,
    execute: bool,
) -> tuple[Path, str, str]:
    payloads = bundle_payloads(
        name,
        predecessor_bundle,
        source,
        source_id,
        source_manifest_sha,
        config,
        layout,
        env,
    )
    files = {
        relative: sha_bytes(payload)
        for relative, payload in sorted(payloads.items())
    }
    identity_base = {
        "schema_version":
            "mysql_distributed_successor_staged_bundle_v1",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "immutable": True,
        "scenario": f"{name}_memory",
        "status": "STAGED_NOT_LAUNCH_AUTHORIZED",
        "launch_authorized": False,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "fresh_in_band_r0_required": True,
        "historical_r0_or_readback_reusable": False,
        "predecessor_bundle_id": predecessor_bundle.name,
        "predecessor_bundle_manifest_sha256":
            sha256(predecessor_bundle / "bundle_manifest.json"),
        "predecessor_source_bundle_id":
            predecessor_manifest["source_bundle_id"],
        "source_directory": str(source),
        "source_bundle_id": source_id,
        "source_manifest_sha256": source_manifest_sha,
        "contract_sha256": contract_sha,
        "builder_sha256": sha256(BUILDER),
        "layout": dict(layout),
        "preserved_envelope": dict(env),
        "runtime_authority": copy.deepcopy(RUNTIME[name]),
        "jdk_runtime_authority": jdk_runtime_authority(name),
        "publication": "staged_then_atomic_rename",
        "idempotency": "content_addressed_existing_verified",
        "authority_parameter_override_prohibited": True,
        "complete_predecessor_bundle_closure_copied": True,
    }
    closure = {
        **identity_base,
        "payload_files": files,
    }
    bundle_id = sha_bytes(canonical(closure))
    payloads["closure.json"] = canonical(closure)
    manifest = {
        **identity_base,
        "payload_files": files,
        "files": {
            relative: sha_bytes(payload)
            for relative, payload in sorted(payloads.items())
        },
        "bundle_id": bundle_id,
    }
    final = BUNDLE_ROOTS[name] / bundle_id
    if final.exists():
        verify_bundle(final, {k: v for k, v in manifest.items() if k != "bundle_id"}, bundle_id)
        return final, bundle_id, sha256(final / "bundle_manifest.json")
    if not execute:
        return final, bundle_id, sha_bytes(canonical(manifest))
    BUNDLE_ROOTS[name].mkdir(parents=True, exist_ok=True)
    stage = BUNDLE_ROOTS[name] / f".{bundle_id}.tmp.{os.getpid()}"
    stage.mkdir()
    try:
        for relative, payload in payloads.items():
            path = stage / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        (stage / "bundle_manifest.json").write_bytes(canonical(manifest))
        verify_bundle(
            stage,
            {k: v for k, v in manifest.items() if k != "bundle_id"},
            bundle_id,
            require_directory_name=False,
        )
        readonly(stage)
        try:
            stage.rename(final)
        except FileExistsError:
            remove_private_stage(stage)
            verify_bundle(
                final,
                {k: v for k, v in manifest.items() if k != "bundle_id"},
                bundle_id,
            )
    except BaseException:
        if stage.exists():
            remove_private_stage(stage)
        raise
    return final, bundle_id, sha256(final / "bundle_manifest.json")


def build(execute: bool = False) -> dict[str, Any]:
    audited = audit(CONTRACT)
    if audited.get("status") != "AUDIT_PASS_STAGED_NOT_LAUNCH_AUTHORIZED":
        raise BuildError("distributed audit contract did not pass")
    contract = load_object(CONTRACT, "distributed contract")
    contract_sha = sha256(CONTRACT)
    built: dict[str, Any] = {}
    for name in ("job", "tpch"):
        pred_manifest, pred_source, pred_source_manifest = predecessor(name)
        layout = contract["layout"][name]
        env = contract["envelope_policy"][name]
        source, source_id, source_manifest_sha = publish_source(
            name,
            pred_source,
            pred_source_manifest,
            layout,
            env,
            contract_sha,
            execute,
        )
        config = successor_config(
            name,
            load_object(
                PREDECESSORS[name] / "config.json",
                f"{name} predecessor config",
            ),
            source,
            source_id,
            source_manifest_sha,
            layout,
            env,
        )
        bundle, bundle_id, bundle_manifest_sha = publish_bundle(
            name,
            PREDECESSORS[name],
            pred_manifest,
            source,
            source_id,
            source_manifest_sha,
            config,
            layout,
            env,
            contract_sha,
            execute,
        )
        built[name] = {
            "source_directory": str(source),
            "source_bundle_id": source_id,
            "source_manifest_sha256": source_manifest_sha,
            "bundle_directory": str(bundle),
            "bundle_id": bundle_id,
            "bundle_manifest_sha256": bundle_manifest_sha,
            "layout": layout,
            "runtime_authority": RUNTIME[name],
            "status": "STAGED_NOT_LAUNCH_AUTHORIZED",
        }
    return {
        "schema_version":
            "mysql_job_tpch_distributed_successor_build_result_v1",
        "status": (
            "PUBLISHED_STAGED_NOT_LAUNCH_AUTHORIZED"
            if execute else
            "READY_TO_PUBLISH_STAGED_NOT_LAUNCH_AUTHORIZED"
        ),
        "executed": execute,
        "remote_commands_executed": False,
        "remote_data_plane_modified": False,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "accepted_round_authorized": False,
        "contract_sha256": contract_sha,
        "successors": built,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    try:
        result = build(execute=args.execute)
    except (BuildError, RuntimeError, OSError, ValueError) as exc:
        print(json.dumps({
            "status": "BLOCKED",
            "executed": args.execute,
            "remote_commands_executed": False,
            "reason": f"{type(exc).__name__}: {exc}",
        }, indent=2, sort_keys=True))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
