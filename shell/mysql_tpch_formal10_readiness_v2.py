#!/usr/bin/env python3
"""Fail-closed TPC-H distributed-v2 formal-10 readiness verifier."""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
from pathlib import Path
import re
import stat
import sys
from typing import Any, Mapping

sys.dont_write_bytecode = True

try:
    from .mysql_tpch_client_immutable_identity_v1 import (
        client_container_immutable_projection,
    )
except ImportError:
    from mysql_tpch_client_immutable_identity_v1 import (
        client_container_immutable_projection,
    )


CONTRACT_SHA256 = (
    "52ba2faa2035dfa9035cb0f6ab75d4461430000097f846c3e94e4a81f99aea58"
)
TPCH_CLIENT_IDENTITY_AMENDMENT_SHA256 = (
    "ea0f3a13d44f1f143e63fcd19003f23e90486cfa297df4b07fea99314af82d7b"
)
PYTHON_RUNTIME_SOURCE = "/home/anonymous/recost_runtime_py311_24_11_1"
PYTHON_RUNTIME_TREE_CONTENT_SHA256 = (
    "3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89"
)
PYTHON_RUNTIME_MANIFEST_SHA256 = (
    "4eeab2e0e5302d20a1fbf4a664fc4938abed84c06f3415e11a462b6e616696c4"
)
PYTHON_RUNTIME_TARGET = (
    "/mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch/"
    "runtime_algorithm_c_successor_20260729/runtime/python311-"
    + PYTHON_RUNTIME_TREE_CONTENT_SHA256
)
INVALIDATED_BUNDLE_ID = (
    "948cf38ff8d5c6757143c543fbec6002f1ea638a43d9f935286a256f29620d6c"
)
INVALIDATED_CLOSURE_BUNDLE_ID = (
    "764a5b03627b7a776c59114cc17687ef5bf01cf937c2ca1e971cd9b70a3cb40a"
)
INVALIDATED_LSOF_BUNDLE_ID = (
    "795ee2e905dc680fa593cc56892b42a3d3cb43928d09a5aae7c04a597d3875dc"
)
INVALIDATED_FALLBACK_BUNDLE_ID = (
    "ff1769e482e9e384c038271326e802d515b7aa8408ae895b77cc02b4cbfa04c5"
)
INVALIDATED_NONROOT_FALLBACK_BUNDLE_ID = (
    "2b777b5b66aa03a7fb190a4f1d654ae89f1f277bf16a4cc759acc32d955cdbe1"
)
INVALIDATED_SOCKET_IDENTITY_BUNDLE_ID = (
    "cee512b778291f8cdb335a4dc07bba56646a45d1f5e4b91a82c8e03c57442e0c"
)
INVALIDATED_PERMISSION_BUNDLE_ID = (
    "716544c9dba781b0cea9548225aa61daf4ae04ce141300f76df762b779c061ac"
)
INVALIDATED_PERMISSION_READBACK_BUNDLE_ID = (
    "c659ae9f5d3743c341095c976b03ea6c6b7469c213ede618f7eaf7fa7f597d46"
)
INVALIDATED_TREE_DIGEST_BUNDLE_ID = (
    "6417f98e939aa67ed8042559e716916abb1b798034e4116294190d5e41b12fc4"
)
INVALIDATED_TREE_DIGEST_BYTES_BUNDLE_ID = (
    "703b1382f0833463c42f02566f75bac490034cf6ed915c21bd1b87efaee23728"
)
INVALIDATED_METADATA_SENSITIVE_TREE_IDENTITY_BUNDLE_ID = (
    "9372c125b38343841032592a9b8ce7d0fc8cc7096ced2149ff24de2e1cb98550"
)
INVALIDATED_CLIENT_PERMISSION_BUNDLE_ID = (
    "87fc669e87ae4db5368e129e7cebe2b9d4fccefcbf189dae3387163bdbce84be"
)
INVALIDATED_CLIENT_PERMISSION_MANIFEST_SHA256 = (
    "0060d4cc45f8630e1bc80653adbb434de68907a0c9f3d516e4ee3baeb514335c"
)
INVALIDATED_SHARED_CONTRACT_BUNDLE_ID = (
    "2d4b793d1c68fe1634495629c5207356d96193bdc2fbba3dabe3a201dc4e4aa6"
)
INVALIDATED_SHARED_CONTRACT_MANIFEST_SHA256 = (
    "31d6ddf7e64a8ff0d456cc98dfe9f151a3bdd0580db9b8d320f37a508faacf39"
)
INVALIDATED_HOST_TMPFS_BUNDLE_ID = (
    "2943d88c35c3ceb9e110bc5df7ae3829851e24e75628e2be159ab07debf8bc59"
)
INVALIDATED_HOST_TMPFS_MANIFEST_SHA256 = (
    "5c40fa6a773475d512d9dd8b83e32b4e637a6c4b58ac7154d5b496f37a63b51e"
)
EXPECTED_TEMPLATE_REGULAR_FILE_COUNT = 173
EXPECTED_TEMPLATE_REGULAR_FILE_BYTES = 27_110_986_878
TREE_IDENTITY_NORMALIZATION = {
    "schema_version": "mysql_tpch_tree_identity_normalization_v1",
    "path_type_size_sha256_content_identity": True,
    "ownership_and_mode_excluded_from_content_identity": True,
    "excluded_root_logical_socket": {
        "path": "mysql.sock",
        "target": "/var/run/mysqld/mysqld.sock",
    },
    "other_transient_entries_rejected": True,
    "other_symlinks_rejected": True,
}
MYSQL_DATADIR_UID = 999
MYSQL_DATADIR_GID = 999
MYSQL_DATADIR_DIRECTORY_MODE = "0700"
MYSQL_DATADIR_FILE_MODE = "0600"
HEX64_RE = re.compile(r"[0-9a-f]{64}$")
PARAMETERS = [
    "io_block_read_cost",
    "memory_block_read_cost",
    "row_evaluate_cost",
    "disk_temptable_row_cost",
]
GRADIENT_FIELDS = {
    "feature_ratio",
    "predicted_resource",
    "utilization",
    "dScore/dU",
    "T",
    "confidence",
    "g_j",
    "eta",
    "delta_param",
    "candidate_score",
}
EXPECTED_DB_ENVELOPE = {
    "cpu_quota_cores": 4,
    "cpuset_cpus": "18-20,54-56",
    "cpuset_mems": "1",
    "memory_bytes": 5583454208,
    "memory_swap_bytes": 5583454208,
    "pids_max": 512,
    "io_read_bps": 100663296,
    "io_write_bps": 100663296,
    "innodb_buffer_pool_bytes": 4294967296,
    "mysql_port": 65103,
}
EXPECTED_CLIENT_ENVELOPE = {
    "execution_mode": "dedicated_read_only_docker_client_cgroup",
    "cpuset_cpus": "0-7,36-43",
    "cpuset_mems": "0",
    "cpu_quota_cores": 16,
    "memory_bytes": 4294967296,
    "memory_swap_bytes": 4294967296,
    "pids_max": 512,
    "network_mode": "host",
    "root_filesystem_read_only": True,
    "linux_capabilities_drop": "ALL",
    "no_new_privileges": True,
    "docker_log_driver": "none",
    "runtime_and_bundle_mounts_read_only": True,
    "measurement_output_medium": "host_tmpfs_bind",
    "container_user": "1008:1008",
    "measurement_directory_uid": 1008,
    "measurement_directory_gid": 1008,
    "measurement_directory_mode": "0700",
    "tmpfs_bytes": 1073741824,
    "target_mysql_device_write_prohibited": True,
    "pid_and_all_descendant_affinity_readback_required": True,
}
EXPECTED_DYNAMIC_STREAMS = [
    "workload_throughput_or_completion_rate",
    "p50_p95_p99_or_query_duration",
    "cpu_usage_and_throttle_ratio",
    "cpu_pressure",
    "memory_current_swap_and_pressure",
    "per_declared_device_bytes_iops_latency_queue_and_utilization",
    "database_counters",
    "process_cpu_affinity_and_device_inventory",
]
EXPECTED_DYNAMIC_DIRECTIONS = [
    "A_solo_vs_A_while_B_runs",
    "B_solo_vs_B_while_A_runs",
]


class ReadinessError(RuntimeError):
    pass


def _target_permission_normalization_valid(value: Any, file_count: Any) -> bool:
    if not isinstance(value, Mapping) or not isinstance(file_count, int):
        return False
    return (
        value.get("schema_version")
        == "mysql_tpch_target_datadir_permission_normalization_v1"
        and value.get("target_only") is True
        and value.get("frozen_image_uid") == MYSQL_DATADIR_UID
        and value.get("frozen_image_gid") == MYSQL_DATADIR_GID
        and value.get("directory_mode") == MYSQL_DATADIR_DIRECTORY_MODE
        and value.get("regular_file_mode") == MYSQL_DATADIR_FILE_MODE
        and isinstance(value.get("directory_count"), int)
        and value["directory_count"] >= 1
        and value.get("regular_file_count") == file_count
        and value.get("socket_excluded_from_target") is True
        and HEX64_RE.fullmatch(
            str(value.get("source_metadata_sha256_before") or "")
        ) is not None
        and value.get("source_metadata_sha256_before")
        == value.get("source_metadata_sha256_after")
    )


def canonical(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_object(path: Path, label: str) -> dict[str, Any]:
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ReadinessError(f"{label} must be a regular file: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReadinessError(f"{label} is invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ReadinessError(f"{label} must be a JSON object")
    return value


def verify_sidecar(path: Path, label: str) -> None:
    sidecar = Path(str(path) + ".sha256")
    expected = f"{sha256(path)}  {path.name}\n"
    if (
        sidecar.is_symlink()
        or not sidecar.is_file()
        or sidecar.read_text(encoding="ascii") != expected
    ):
        raise ReadinessError(f"{label} sidecar differs")


def relative_files(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ReadinessError(f"symlink prohibited: {path}")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = sha256(path)
    return result


def verify_bundle(bundle: Path) -> dict[str, Any]:
    bundle = bundle.resolve()
    manifest_path = bundle / "bundle_manifest.json"
    manifest = load_object(manifest_path, "TPC-H v2 bundle manifest")
    verify_sidecar(manifest_path, "TPC-H v2 bundle manifest")
    identity = dict(manifest)
    identity.pop("bundle_id", None)
    if (
        manifest.get("schema_version")
        != "mysql_tpch_distributed_formal10_successor_v2"
        or manifest.get("bundle_id") != bundle.name
        or hashlib.sha256(canonical(identity)).hexdigest() != bundle.name
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("postgresql_mutation_prohibited") is not True
        or manifest.get("immutable") is not True
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
        or manifest.get("four_scene_contract_sha256") != CONTRACT_SHA256
        or manifest.get("tpch_client_identity_amendment_sha256")
        != TPCH_CLIENT_IDENTITY_AMENDMENT_SHA256
    ):
        raise ReadinessError("TPC-H v2 bundle identity/scope differs")
    actual = relative_files(bundle)
    actual.pop("bundle_manifest.json", None)
    actual.pop("bundle_manifest.json.sha256", None)
    if actual != manifest.get("files"):
        raise ReadinessError("TPC-H v2 exact file closure differs")
    for path in [bundle, *sorted(bundle.rglob("*"))]:
        if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) & 0o222:
            raise ReadinessError(f"writable/symlinked bundle entry: {path}")
    contract = load_object(
        bundle / "tpch_formal10_contract.json", "TPC-H formal10 contract"
    )
    if (
        sha256(bundle / "contracts/four_scene_v2.json") != CONTRACT_SHA256
        or sha256(bundle / "contracts/tpch_client_identity_amendment_v3.json")
        != TPCH_CLIENT_IDENTITY_AMENDMENT_SHA256
        or contract.get("schema_version")
        != "mysql_tpch_algorithm_c_formal10_contract_v2"
        or contract.get("scenario") != "tpch_memory"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("formal_launch_authorized") is not False
        or contract.get("accepted_round_authorized") is not False
        or contract.get("database_resource_envelope") != EXPECTED_DB_ENVELOPE
        or contract.get("client_resource_envelope") != EXPECTED_CLIENT_ENVELOPE
    ):
        raise ReadinessError("TPC-H formal10 static contract differs")
    physical_copy = contract.get("physical_copy_authority") or {}
    owner_inventory = (
        contract.get("template_owner_inventory_authority") or {}
    )
    runtime_authority = contract.get("python_runtime_authority") or {}
    client_mount_isolation = contract.get("client_mount_isolation") or {}
    client_immutable_identity = (
        contract.get("client_immutable_identity") or {}
    )
    qualification_execution = contract.get("qualification_execution") or {}
    fresh_r0_execution = contract.get("fresh_r0_execution") or {}
    if (
        physical_copy.get("implementation")
        != "tar_regular_file_byte_stream_target_permission_normalization_plus_fiemap_no_shared_extents"
        or physical_copy.get("cp_reflink_never_dependency") is not False
        or physical_copy.get("clone_api_prohibited") is not True
        or physical_copy.get("shared_physical_extent_bytes_required") != 0
        or physical_copy.get("atomic_rename_required") is not True
        or physical_copy.get("target_only_permission_normalization_required")
        is not True
        or physical_copy.get("target_mysql_image_identity")
        != {
            "uid": 999,
            "gid": 999,
            "directory_mode": "0700",
            "regular_file_mode": "0600",
        }
        or physical_copy.get("source_metadata_stable_before_after_required")
        is not True
        or owner_inventory.get("host_lsof_or_fuser_dependency") is not False
        or owner_inventory.get("primary")
        != "python_stdlib_proc_fd_cwd_root_inventory"
        or owner_inventory.get("permission_fallback")
        != "readonly_host_pid_docker_root_scanner_sys_ptrace_dac_read_search"
        or owner_inventory.get("fallback_runtime_identity_readback_required")
        is not True
        or owner_inventory.get("fallback_root_identity") != {"uid": 0, "gid": 0}
        or owner_inventory.get("fallback_capabilities_exact")
        != ["SYS_PTRACE", "DAC_READ_SEARCH"]
        or owner_inventory.get("fallback_network_none_required") is not True
        or owner_inventory.get("fallback_read_only_rootfs_required") is not True
        or owner_inventory.get("fallback_docker_socket_mount_prohibited")
        is not True
        or owner_inventory.get("fallback_stdout_failure_evidence_required")
        is not True
        or owner_inventory.get("open_owner_result") != "FAIL_CLOSED"
        or owner_inventory.get("unreadable_pid_after_fallback_result")
        != "FAIL_CLOSED"
        or owner_inventory.get("must_pass_before_runtime_or_datadir_mutation")
        is not True
        or runtime_authority.get("source_root") != PYTHON_RUNTIME_SOURCE
        or runtime_authority.get("target_root") != PYTHON_RUNTIME_TARGET
        or runtime_authority.get(
            "source_and_target_different_device_required"
        )
        is not True
        or runtime_authority.get("portable_manifest_sha256")
        != PYTHON_RUNTIME_MANIFEST_SHA256
        or runtime_authority.get("tree_content_sha256")
        != PYTHON_RUNTIME_TREE_CONTENT_SHA256
        or runtime_authority.get("copy_mode")
        != "tar_regular_file_byte_stream_no_clone_api"
        or runtime_authority.get(
            "source_stage_target_exact_readback_required"
        )
        is not True
        or runtime_authority.get(
            "atomic_content_addressed_publish_required"
        )
        is not True
        or client_mount_isolation.get("mysql_image_volume_destination")
        != "/var/lib/mysql"
        or client_mount_isolation.get("manifest_covered_read_only_mask")
        != "qualification/client_volume_masks/mysql_datadir"
        or client_mount_isolation.get(
            "exact_bind_source_and_destination_required"
        )
        is not True
        or client_mount_isolation.get(
            "anonymous_or_named_volume_prohibited"
        )
        is not True
        or client_mount_isolation.get(
            "mutable_application_mount_prohibited"
        )
        is not True
        or client_mount_isolation.get(
            "database_datadir_source_mount_prohibited"
        )
        is not True
        or client_mount_isolation.get("secure_measurement_bind")
        != {
            "source_relative_to_attempt_tmpfs": "client_measurement",
            "destination": "/measurement",
            "mount_type": "bind",
            "read_write_required": True,
            "host_filesystem": "tmpfs",
            "attempt_path_is_exact_mountpoint": True,
            "host_root_mount_required": True,
            "mount_capacity_bytes": 2147483648,
            "mount_options_required": [
                "rw", "nosuid", "nodev", "noexec"
            ],
            "uid": 1008,
            "gid": 1008,
            "mode": "0700",
            "container_user": "1008:1008",
            "container_created_tmpfs_prohibited": True,
            "failure_cleanup_and_readback_evidence_required": True,
        }
        or client_immutable_identity.get("projection")
        != "canonical_client_immutable_projection_v1"
        or client_immutable_identity.get("shared_helper_root")
        != "mysql_tpch_client_immutable_identity_v1.py"
        or client_immutable_identity.get("shared_helper_qualification")
        != "qualification/mysql_tpch_client_immutable_identity_v1.py"
        or client_immutable_identity.get(
            "bootstrap_same_live_uses_projection"
        )
        is not True
        or client_immutable_identity.get(
            "command_materializer_uses_projection"
        )
        is not True
        or client_immutable_identity.get(
            "database_counter_transport_uses_projection"
        )
        is not True
        or client_immutable_identity.get(
            "qualification_collector_uses_projection"
        )
        is not True
        or client_immutable_identity.get(
            "docker_exec_state_execids_excluded"
        )
        is not True
        or client_immutable_identity.get(
            "authority_import_bytecode_disabled_before_helper_import"
        )
        is not True
        or client_immutable_identity.get(
            "readiness_import_bytecode_disabled_before_helper_import"
        )
        is not True
        or sha256(
            bundle / "mysql_tpch_client_immutable_identity_v1.py"
        )
        != sha256(
            bundle
            / "qualification/mysql_tpch_client_immutable_identity_v1.py"
        )
        or client_immutable_identity.get("shared_helper_sha256")
        != sha256(
            bundle / "mysql_tpch_client_immutable_identity_v1.py"
        )
        or qualification_execution.get("working_directory")
        != "/measurement"
        or qualification_execution.get(
            "main_agent_authority_renderer"
        )
        != "qualification/mysql_tpch_v2_qualification_authority_v1.py"
        or qualification_execution.get(
            "short_token_and_sidecar_required"
        )
        is not True
        or qualification_execution.get("qualification_seconds") != 180
        or qualification_execution.get("minimum_samples") != 181
        or fresh_r0_execution.get("runner")
        != "qualification/mysql_tpch_v2_fresh_r0_runner_v1.py"
        or fresh_r0_execution.get(
            "real_workload_and_live_counters_required"
        )
        is not True
        or fresh_r0_execution.get(
            "dynamic_gate_sha256_binding_required"
        )
        is not True
        or fresh_r0_execution.get(
            "same_live_database_and_client_required"
        )
        is not True
        or fresh_r0_execution.get(
            "database_counter_missing_or_timeout_result"
        )
        != "FAIL"
        or fresh_r0_execution.get(
            "l1_l2_l3_fingerprint_required"
        )
        is not True
        or fresh_r0_execution.get("accepted_rounds_credit") != 0
    ):
        raise ReadinessError("physical-copy/runtime static contract differs")
    algorithm = contract.get("algorithm") or {}
    if (
        algorithm.get("active_parameters") != PARAMETERS
        or algorithm.get("active_parameter_count") != 4
        or algorithm.get("required_nonempty_subsets") != 15
        or algorithm.get("subset_enumeration")
        != "all_2^4_minus_1_nonempty_subspaces"
        or algorithm.get("candidate_order")
        != "global_descending_final_candidate_score"
        or algorithm.get("fixed_real_replay_top_k") != 5
        or algorithm.get("target_accepted_rounds") != 10
        or algorithm.get("original_gradient_only") is not True
        or algorithm.get("gradient_formula")
        != "g_j = sum_r(dScore/dU_r * T[j,r] * confidence)"
        or algorithm.get("parameter_update_source") != "g_j_and_eta_only"
        or set(algorithm.get("required_gradient_event_fields") or [])
        != GRADIENT_FIELDS
        or algorithm.get("qps_role")
        != "real_replay_outer_acceptance_only"
        or algorithm.get("replacement_search_algorithm_prohibited") is not True
    ):
        raise ReadinessError("TPC-H Algorithm C invariants differ")
    fingerprint = contract.get("fingerprint") or {}
    if (
        fingerprint.get("l1_required") is not True
        or fingerprint.get("l2_required") is not True
        or fingerprint.get("l3_required") is not True
        or fingerprint.get("job_tpch_l3")
        != ["memory_peak", "dynamic_execution_memory", "spill"]
        or fingerprint.get("missing_any_level_policy")
        != "independent_candidate"
        or fingerprint.get("duplicate_rule")
        != "exact_parameter_signature_and_equal_l1_and_tolerant_l2_l3"
    ):
        raise ReadinessError("TPC-H three-level fingerprint contract differs")
    invalidation = load_object(
        bundle / "historical_v40_invalidation.json",
        "TPC-H v40 invalidation",
    )
    if (
        invalidation.get("schema_version")
        != "mysql_tpch_v40_invalid_r0_invalidation_v1"
        or invalidation.get("status") != "INVALIDATED_NOT_REUSABLE"
        or invalidation.get("run_id")
        != "alg_c_tpch_r0_20260730T210012_414be152"
        or invalidation.get("phase") != "INVALID_R0_UNSTABLE"
        or invalidation.get("host") != "local://compute00"
        or invalidation.get("device") != "/dev/nvme2n1"
        or invalidation.get("r0_complete") is not False
        or invalidation.get("r0_gate_valid") is not False
        or invalidation.get("algorithm_c_authorized") is not False
        or invalidation.get("reusable") is not False
        or invalidation.get("accepted_rounds_credit") != 0
    ):
        raise ReadinessError("TPC-H v40 invalidation semantics differ")
    invalid_bundle = load_object(
        bundle / "invalidated_bootstrap_bundle.json",
        "TPC-H incompatible bootstrap bundle invalidation",
    )
    invalid_closure_bundle = load_object(
        bundle / "invalidated_closure_bundle.json",
        "TPC-H exact-closure bundle invalidation",
    )
    invalid_lsof_bundle = load_object(
        bundle / "invalidated_lsof_bundle.json",
        "TPC-H lsof-dependent bundle invalidation",
    )
    invalid_fallback_bundle = load_object(
        bundle / "invalidated_fallback_bundle.json",
        "TPC-H root-squash fallback bundle invalidation",
    )
    invalid_nonroot_fallback_bundle = load_object(
        bundle / "invalidated_nonroot_fallback_bundle.json",
        "TPC-H non-root fallback bundle invalidation",
    )
    invalid_socket_identity_bundle = load_object(
        bundle / "invalidated_socket_identity_bundle.json",
        "TPC-H logical-socket identity bundle invalidation",
    )
    invalid_permission_bundle = load_object(
        bundle / "invalidated_permission_bundle.json",
        "TPC-H datadir-permission bundle invalidation",
    )
    invalid_permission_readback_bundle = load_object(
        bundle / "invalidated_permission_readback_bundle.json",
        "TPC-H datadir-permission readback bundle invalidation",
    )
    invalid_tree_digest_bundle = load_object(
        bundle / "invalidated_tree_digest_bundle.json",
        "TPC-H tree-digest privilege bundle invalidation",
    )
    invalid_tree_digest_bytes_bundle = load_object(
        bundle / "invalidated_tree_digest_bytes_bundle.json",
        "TPC-H tree-digest byte-count bundle invalidation",
    )
    invalid_metadata_sensitive_tree_identity_bundle = load_object(
        bundle / "invalidated_metadata_sensitive_tree_identity_bundle.json",
        "TPC-H metadata-sensitive tree-identity bundle invalidation",
    )
    invalid_client_permission_bundle = load_object(
        bundle / "invalidated_client_permission_bundle.json",
        "TPC-H client-permission bundle invalidation",
    )
    invalid_shared_contract_bundle = load_object(
        bundle / "invalidated_shared_contract_bundle.json",
        "TPC-H shared-contract bundle invalidation",
    )
    invalid_host_tmpfs_bundle = load_object(
        bundle / "invalidated_host_tmpfs_bundle.json",
        "TPC-H shared-dev-shm bundle invalidation",
    )
    runtime_manifest_path = (
        bundle / "runtime/python_runtime_tree_manifest_v3.json"
    )
    runtime_manifest = load_object(
        runtime_manifest_path, "TPC-H Python runtime manifest"
    )
    if (
        invalid_bundle.get("status") != "INVALIDATED_DO_NOT_LAUNCH"
        or invalid_bundle.get("bundle_id") != INVALIDATED_BUNDLE_ID
        or invalid_bundle.get("reason")
        != "node04_coreutils_8.22_cp_reflink_never_unsupported"
        or invalid_bundle.get("launch_authorized") is not False
        or invalid_bundle.get("accepted_rounds_credit") != 0
        or invalid_closure_bundle.get("status")
        != "INVALIDATED_DO_NOT_LAUNCH"
        or invalid_closure_bundle.get("bundle_id")
        != INVALIDATED_CLOSURE_BUNDLE_ID
        or invalid_closure_bundle.get("reason")
        != "root_manifest_filter_excluded_nested_predecessor_manifest"
        or invalid_closure_bundle.get("excluded_valid_relative_path")
        != "predecessor/bundle_manifest.json"
        or invalid_closure_bundle.get("launch_authorized") is not False
        or invalid_closure_bundle.get("accepted_rounds_credit") != 0
        or invalid_lsof_bundle.get("status") != "INVALIDATED_DO_NOT_LAUNCH"
        or invalid_lsof_bundle.get("bundle_id")
        != INVALIDATED_LSOF_BUNDLE_ID
        or invalid_lsof_bundle.get("reason") != "node04_host_lsof_absent"
        or invalid_lsof_bundle.get("launch_authorized") is not False
        or invalid_lsof_bundle.get("accepted_rounds_credit") != 0
        or invalid_fallback_bundle.get("status") != "INVALIDATED_DO_NOT_LAUNCH"
        or invalid_fallback_bundle.get("bundle_id")
        != INVALIDATED_FALLBACK_BUNDLE_ID
        or invalid_fallback_bundle.get("reason")
        != "docker_fallback_default_root_cannot_read_root_squashed_0700_bundle"
        or invalid_fallback_bundle.get("launch_authorized") is not False
        or invalid_fallback_bundle.get("accepted_rounds_credit") != 0
        or invalid_nonroot_fallback_bundle.get("status")
        != "INVALIDATED_DO_NOT_LAUNCH"
        or invalid_nonroot_fallback_bundle.get("bundle_id")
        != INVALIDATED_NONROOT_FALLBACK_BUNDLE_ID
        or invalid_nonroot_fallback_bundle.get("reason")
        != "nonroot_host_pid_scanner_has_unreadable_pids"
        or invalid_nonroot_fallback_bundle.get("launch_authorized") is not False
        or invalid_nonroot_fallback_bundle.get("accepted_rounds_credit") != 0
        or invalid_socket_identity_bundle.get("status")
        != "INVALIDATED_DO_NOT_LAUNCH"
        or invalid_socket_identity_bundle.get("bundle_id")
        != INVALIDATED_SOCKET_IDENTITY_BUNDLE_ID
        or invalid_socket_identity_bundle.get("database_or_client_started")
        is not False
        or invalid_socket_identity_bundle.get("launch_authorized") is not False
        or invalid_socket_identity_bundle.get("accepted_rounds_credit") != 0
        or invalid_permission_bundle.get("status")
        != "INVALIDATED_DO_NOT_LAUNCH"
        or invalid_permission_bundle.get("bundle_id")
        != INVALIDATED_PERMISSION_BUNDLE_ID
        or invalid_permission_bundle.get("reason")
        != "mysql_bootstrap_failed_mysql_socket_ibdata1_read_only_datadir"
        or invalid_permission_bundle.get("failure_stage")
        != "bootstrap_database_before_database_or_client"
        or invalid_permission_bundle.get("database_or_client_started")
        is not False
        or invalid_permission_bundle.get("pid") != 0
        or invalid_permission_bundle.get("launch_authorized") is not False
        or invalid_permission_bundle.get("accepted_rounds_credit") != 0
        or invalid_permission_readback_bundle.get("status")
        != "INVALIDATED_DO_NOT_LAUNCH"
        or invalid_permission_readback_bundle.get("bundle_id")
        != INVALIDATED_PERMISSION_READBACK_BUNDLE_ID
        or invalid_permission_readback_bundle.get("failure_stage")
        != "post_copy_pre_database_or_client"
        or invalid_permission_readback_bundle.get("database_or_client_started")
        is not False
        or invalid_permission_readback_bundle.get("database_pid") != 0
        or invalid_permission_readback_bundle.get("client_pid") != 0
        or invalid_permission_readback_bundle.get("launch_authorized") is not False
        or invalid_permission_readback_bundle.get("accepted_rounds_credit") != 0
        or invalid_tree_digest_bundle.get("status")
        != "INVALIDATED_DO_NOT_LAUNCH"
        or invalid_tree_digest_bundle.get("bundle_id")
        != INVALIDATED_TREE_DIGEST_BUNDLE_ID
        or invalid_tree_digest_bundle.get("bundle_manifest_sha256")
        != "b545b4e7ae47987a54082bf1a2e5b8ccbec6320f3bd6733f9bcd5d9edb11bb56"
        or invalid_tree_digest_bundle.get("expected_regular_file_count")
        != EXPECTED_TEMPLATE_REGULAR_FILE_COUNT
        or invalid_tree_digest_bundle.get("expected_regular_file_bytes")
        != EXPECTED_TEMPLATE_REGULAR_FILE_BYTES
        or invalid_tree_digest_bundle.get("traversal_or_hash_error_policy")
        != "FAIL_CLOSED"
        or invalid_tree_digest_bundle.get("failure_stage")
        != "source_tree_digest_pre_copy_pre_database_or_client"
        or invalid_tree_digest_bundle.get("database_or_client_started")
        is not False
        or invalid_tree_digest_bundle.get("launch_authorized") is not False
        or invalid_tree_digest_bundle.get("accepted_rounds_credit") != 0
        or invalid_tree_digest_bytes_bundle.get("status")
        != "INVALIDATED_DO_NOT_LAUNCH"
        or invalid_tree_digest_bytes_bundle.get("bundle_id")
        != INVALIDATED_TREE_DIGEST_BYTES_BUNDLE_ID
        or invalid_tree_digest_bytes_bundle.get("stale_expected_regular_file_bytes")
        != 27_111_280_640
        or invalid_tree_digest_bytes_bundle.get("read_only_scanner_regular_file_bytes")
        != EXPECTED_TEMPLATE_REGULAR_FILE_BYTES
        or invalid_tree_digest_bytes_bundle.get("prior_authority_summary_regular_file_bytes")
        != EXPECTED_TEMPLATE_REGULAR_FILE_BYTES
        or invalid_tree_digest_bytes_bundle.get("root_dac_read_search_scanner_preserved")
        is not True
        or invalid_tree_digest_bytes_bundle.get("traversal_or_hash_error_policy")
        != "FAIL_CLOSED"
        or invalid_tree_digest_bytes_bundle.get("failure_stage")
        != "source_tree_digest_pre_attempt_pre_database_or_client"
        or invalid_tree_digest_bytes_bundle.get("attempt_created") is not False
        or invalid_tree_digest_bytes_bundle.get("database_or_client_started")
        is not False
        or invalid_tree_digest_bytes_bundle.get("database_pid") != 0
        or invalid_tree_digest_bytes_bundle.get("client_pid") != 0
        or invalid_tree_digest_bytes_bundle.get("accepted_rounds_credit") != 0
        or invalid_tree_digest_bytes_bundle.get("launch_authorized") is not False
        or invalid_metadata_sensitive_tree_identity_bundle.get("status")
        != "INVALIDATED_DO_NOT_LAUNCH"
        or invalid_metadata_sensitive_tree_identity_bundle.get("bundle_id")
        != INVALIDATED_METADATA_SENSITIVE_TREE_IDENTITY_BUNDLE_ID
        or invalid_metadata_sensitive_tree_identity_bundle.get("reason")
        != "tar_tree_identity_included_owner_and_mode_after_target_permission_normalization"
        or invalid_metadata_sensitive_tree_identity_bundle.get(
            "ownership_and_mode_excluded_from_content_identity"
        )
        is not True
        or invalid_metadata_sensitive_tree_identity_bundle.get(
            "target_permission_readback_remains_separate_and_strict"
        )
        is not True
        or invalid_metadata_sensitive_tree_identity_bundle.get(
            "root_dac_read_search_scanner_preserved"
        )
        is not True
        or invalid_metadata_sensitive_tree_identity_bundle.get(
            "traversal_or_hash_error_policy"
        )
        != "FAIL_CLOSED"
        or invalid_metadata_sensitive_tree_identity_bundle.get("failure_stage")
        != "post_copy_pre_database_or_client"
        or invalid_metadata_sensitive_tree_identity_bundle.get(
            "database_or_client_started"
        )
        is not False
        or invalid_metadata_sensitive_tree_identity_bundle.get("database_pid")
        != 0
        or invalid_metadata_sensitive_tree_identity_bundle.get("client_pid")
        != 0
        or invalid_metadata_sensitive_tree_identity_bundle.get(
            "accepted_rounds_credit"
        )
        != 0
        or invalid_metadata_sensitive_tree_identity_bundle.get(
            "launch_authorized"
        )
        is not False
        or invalid_client_permission_bundle.get("status")
        != "INVALIDATED_DO_NOT_REUSE"
        or invalid_client_permission_bundle.get("bundle_id")
        != INVALIDATED_CLIENT_PERMISSION_BUNDLE_ID
        or invalid_client_permission_bundle.get("bundle_manifest_sha256")
        != INVALIDATED_CLIENT_PERMISSION_MANIFEST_SHA256
        or invalid_client_permission_bundle.get("attempt_token")
        != "tpch-v2-20260731t020337z-c02b2656a8868d38b0dcab8a"
        or invalid_client_permission_bundle.get("workload_started") is not False
        or invalid_client_permission_bundle.get("r0_credit") != 0
        or invalid_client_permission_bundle.get("accepted_rounds_credit") != 0
        or invalid_client_permission_bundle.get("reusable") is not False
        or invalid_client_permission_bundle.get("formal_r0_authorized")
        is not False
        or invalid_client_permission_bundle.get("formal_authorized") is not False
        or invalid_client_permission_bundle.get("accepted_round_authorized")
        is not False
        or invalid_shared_contract_bundle.get("status")
        != "INVALIDATED_DO_NOT_LAUNCH"
        or invalid_shared_contract_bundle.get("bundle_id")
        != INVALIDATED_SHARED_CONTRACT_BUNDLE_ID
        or invalid_shared_contract_bundle.get("bundle_manifest_sha256")
        != INVALIDATED_SHARED_CONTRACT_MANIFEST_SHA256
        or invalid_shared_contract_bundle.get("workload_started") is not False
        or invalid_shared_contract_bundle.get("r0_credit") != 0
        or invalid_shared_contract_bundle.get("accepted_rounds_credit") != 0
        or invalid_shared_contract_bundle.get("reusable") is not False
        or invalid_shared_contract_bundle.get("formal_r0_authorized") is not False
        or invalid_shared_contract_bundle.get("formal_authorized") is not False
        or invalid_shared_contract_bundle.get("accepted_round_authorized")
        is not False
        or invalid_host_tmpfs_bundle.get("status")
        != "INVALIDATED_DO_NOT_LAUNCH"
        or invalid_host_tmpfs_bundle.get("bundle_id")
        != INVALIDATED_HOST_TMPFS_BUNDLE_ID
        or invalid_host_tmpfs_bundle.get("bundle_manifest_sha256")
        != INVALIDATED_HOST_TMPFS_MANIFEST_SHA256
        or invalid_host_tmpfs_bundle.get(
            "bootstrap_reached_container_or_workload_start"
        )
        is not False
        or invalid_host_tmpfs_bundle.get("workload_started") is not False
        or invalid_host_tmpfs_bundle.get("r0_credit") != 0
        or invalid_host_tmpfs_bundle.get("accepted_rounds_credit") != 0
        or invalid_host_tmpfs_bundle.get("reusable") is not False
        or invalid_host_tmpfs_bundle.get("formal_r0_authorized") is not False
        or invalid_host_tmpfs_bundle.get("formal_launch_authorized") is not False
        or sha256(runtime_manifest_path) != PYTHON_RUNTIME_MANIFEST_SHA256
        or runtime_manifest.get("schema_version")
        != "mysql_algorithm_c_runtime_tree_manifest_v3"
        or runtime_manifest.get("tree_content_sha256")
        != PYTHON_RUNTIME_TREE_CONTENT_SHA256
    ):
        raise ReadinessError("runtime/invalidated-bootstrap authority differs")
    return {
        "manifest": manifest,
        "contract": contract,
        "invalidation": invalidation,
        "invalidated_bootstrap_bundle": invalid_bundle,
        "invalidated_closure_bundle": invalid_closure_bundle,
        "invalidated_lsof_bundle": invalid_lsof_bundle,
        "invalidated_fallback_bundle": invalid_fallback_bundle,
        "invalidated_nonroot_fallback_bundle": invalid_nonroot_fallback_bundle,
        "invalidated_socket_identity_bundle": invalid_socket_identity_bundle,
        "invalidated_permission_bundle": invalid_permission_bundle,
        "invalidated_permission_readback_bundle": invalid_permission_readback_bundle,
        "invalidated_tree_digest_bundle": invalid_tree_digest_bundle,
        "invalidated_tree_digest_bytes_bundle": invalid_tree_digest_bytes_bundle,
        "invalidated_metadata_sensitive_tree_identity_bundle":
            invalid_metadata_sensitive_tree_identity_bundle,
        "invalidated_client_permission_bundle": invalid_client_permission_bundle,
        "invalidated_shared_contract_bundle": invalid_shared_contract_bundle,
        "invalidated_host_tmpfs_bundle": invalid_host_tmpfs_bundle,
        "runtime_manifest": runtime_manifest,
    }


def finite(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise ReadinessError(f"{label} must be finite")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ReadinessError(f"{label} must be finite") from exc
    if not math.isfinite(result):
        raise ReadinessError(f"{label} must be finite")
    return result


def _close(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=1e-10, abs_tol=1e-12)


def verify_gradient_event(event: Mapping[str, Any], label: str) -> None:
    if not GRADIENT_FIELDS.issubset(event):
        raise ReadinessError(f"{label} misses required gradient fields")
    if event.get("gradient_source") != "original_dScore_dU_times_T_times_confidence":
        raise ReadinessError(f"{label} gradient source differs")
    t_matrix = event.get("T")
    slopes = event.get("dScore/dU")
    confidence = event.get("confidence")
    gradients = event.get("g_j")
    deltas = event.get("delta_param")
    if not all(
        isinstance(value, Mapping) and value
        for value in (t_matrix, slopes, confidence, gradients, deltas)
    ):
        raise ReadinessError(f"{label} gradient maps are incomplete")
    if set(t_matrix) != set(slopes) or set(t_matrix) != set(gradients):
        raise ReadinessError(f"{label} parameter sets differ")
    eta = finite(event.get("eta"), f"{label}.eta")
    if eta <= 0:
        raise ReadinessError(f"{label}.eta must be positive")
    finite(event.get("candidate_score"), f"{label}.candidate_score")
    for parameter in sorted(t_matrix):
        if parameter not in PARAMETERS:
            raise ReadinessError(f"{label} contains unsealed parameter")
        t_values = t_matrix[parameter]
        slope_values = slopes[parameter]
        if (
            not isinstance(t_values, Mapping)
            or not t_values
            or not isinstance(slope_values, Mapping)
            or set(t_values) != set(slope_values)
        ):
            raise ReadinessError(f"{label}.{parameter} resource maps differ")
        confidence_value = confidence.get(parameter)
        if isinstance(confidence_value, Mapping):
            confidence_map = confidence_value
        else:
            confidence_map = {
                resource: confidence_value for resource in t_values
            }
        expected_gradient = sum(
            finite(slope_values[resource], f"{label}.dScore/dU")
            * finite(t_values[resource], f"{label}.T")
            * finite(confidence_map.get(resource), f"{label}.confidence")
            for resource in sorted(t_values)
        )
        observed = finite(gradients[parameter], f"{label}.g_j")
        if not _close(observed, expected_gradient):
            raise ReadinessError(
                f"{label}.g_j.{parameter} is not sum(dScore/dU*T*confidence)"
            )
        delta = deltas.get(parameter)
        if not isinstance(delta, Mapping):
            raise ReadinessError(f"{label}.delta_param is incomplete")
        before = finite(delta.get("before"), f"{label}.delta.before")
        proposed = finite(
            delta.get("proposed_before_clip"), f"{label}.delta.proposed"
        )
        after = finite(delta.get("after_clip"), f"{label}.delta.after")
        absolute = finite(delta.get("absolute"), f"{label}.delta.absolute")
        relative = finite(delta.get("relative"), f"{label}.delta.relative")
        if before <= 0:
            raise ReadinessError(f"{label}.delta.before must be positive")
        expected_proposed = before * math.exp(eta * observed)
        if (
            not _close(proposed, expected_proposed)
            or not _close(absolute, after - before)
            or not _close(relative, (after - before) / before)
        ):
            raise ReadinessError(
                f"{label}.delta_param is not derived from g_j and eta"
            )
        if delta.get("clipped") is False and not _close(after, proposed):
            raise ReadinessError(f"{label}.unclipped update differs")


def _verify_fingerprint(row: Mapping[str, Any], label: str) -> None:
    if (
        not isinstance(row.get("parameter_signature"), str)
        or not row["parameter_signature"]
        or not isinstance(row.get("l1"), Mapping)
        or not row["l1"]
        or not isinstance(row.get("l2"), Mapping)
        or not row["l2"]
        or not isinstance(row.get("l3"), Mapping)
        or set(row["l3"])
        != {"memory_peak", "dynamic_execution_memory", "spill"}
        or any(row["l3"][key] is None for key in row["l3"])
    ):
        raise ReadinessError(f"{label} L1/L2/L3 evidence is incomplete")


def verify_debug_round_document(
    document: Mapping[str, Any],
    *,
    bundle_id: str,
    attempt_token: str,
    identity: Mapping[str, Any],
) -> dict[str, Any]:
    events = document.get("gradient_events")
    ranking = document.get("global_candidate_ranking")
    journals = document.get("top5_point_journals")
    fingerprints = document.get("candidate_fingerprints")
    if (
        document.get("schema_version")
        != "mysql_tpch_distributed_v2_debug_round_evidence_v1"
        or document.get("status") != "PASS_DEBUG_ROUND"
        or document.get("bundle_id") != bundle_id
        or document.get("attempt_token") != attempt_token
        or document.get("same_live_identity") != identity
        or document.get("completed_nonempty_subsets") != 15
        or document.get("sealed_top5_ranks") != [1, 2, 3, 4, 5]
        or document.get("replayed_top5_ranks") != [1, 2, 3, 4, 5]
        or document.get("fixed_top5_only") is not True
        or document.get("candidate_order")
        != "global_descending_final_candidate_score"
        or document.get("algorithm_formula")
        != "g_j = sum_r(dScore/dU_r * T[j,r] * confidence)"
        or document.get("parameter_update_source") != "g_j_and_eta_only"
        or document.get("qps_role")
        != "outer_acceptance_after_real_replay_only"
        or document.get("fingerprint_duplicate_rule")
        != "exact_parameter_signature_and_equal_l1_and_tolerant_l2_l3"
        or document.get("missing_fingerprint_level_policy")
        != "independent_candidate"
        or document.get("accepted_rounds_credit") != 0
        or document.get("accepted_round_authorized") is not False
        or not isinstance(events, list)
        or len(events) < 15
        or not isinstance(ranking, list)
        or len(ranking) != 15
        or not isinstance(journals, list)
        or len(journals) != 5
        or not isinstance(fingerprints, list)
        or len(fingerprints) != 20
    ):
        raise ReadinessError("TPC-H debug-round structure differs")
    subset_ids: list[str] = []
    subset_definitions: dict[str, tuple[str, ...]] = {}
    for index, event in enumerate(events, 1):
        subset_id = event.get("subset_id")
        if not isinstance(subset_id, str) or not subset_id:
            raise ReadinessError("gradient subset identity is absent")
        active = event.get("active_subset")
        if (
            not isinstance(active, list)
            or not active
            or any(parameter not in PARAMETERS for parameter in active)
            or len(set(active)) != len(active)
        ):
            raise ReadinessError("gradient active subset differs")
        normalized = tuple(
            parameter for parameter in PARAMETERS if parameter in set(active)
        )
        if (
            subset_id in subset_definitions
            and subset_definitions[subset_id] != normalized
        ):
            raise ReadinessError("subset identity maps to multiple subspaces")
        subset_definitions[subset_id] = normalized
        subset_ids.append(subset_id)
        verify_gradient_event(event, f"gradient_events[{index}]")
    if len(set(subset_ids)) != 15:
        raise ReadinessError("all 15 nonempty subspaces were not enumerated")
    expected_subsets = {
        tuple(combination)
        for width in range(1, len(PARAMETERS) + 1)
        for combination in itertools.combinations(PARAMETERS, width)
    }
    if set(subset_definitions.values()) != expected_subsets:
        raise ReadinessError("2^4-1 exact nonempty subspace set differs")
    ranked_ids: list[str] = []
    previous = math.inf
    for index, row in enumerate(ranking, 1):
        if (
            not isinstance(row, Mapping)
            or row.get("rank") != index
            or row.get("subset_id") not in set(subset_ids)
        ):
            raise ReadinessError("global candidate ranking identity differs")
        score = finite(row.get("candidate_score"), "ranking candidate_score")
        if score > previous:
            raise ReadinessError("global ranking is not descending")
        previous = score
        ranked_ids.append(str(row["subset_id"]))
    if len(set(ranked_ids)) != 15:
        raise ReadinessError("global ranking is not a permutation")
    top5_ids = ranked_ids[:5]
    for rank, journal in enumerate(journals, 1):
        if (
            not isinstance(journal, Mapping)
            or journal.get("rank") != rank
            or journal.get("subset_id") != top5_ids[rank - 1]
            or not isinstance(journal.get("sealed_candidate_identity"), str)
            or not journal["sealed_candidate_identity"]
            or not isinstance(journal.get("attempt_token"), str)
            or not journal["attempt_token"]
            or journal.get("started") is not True
            or journal.get("measurement_complete") is not True
            or journal.get("completion_recorded") is not True
            or journal.get("real_replay") is not True
            or journal.get("failed_replay") is not False
            or journal.get("accepted_rounds_credit") != 0
        ):
            raise ReadinessError("fixed Top-5 point journal is incomplete")
    seen_fingerprints: set[tuple[str, str]] = set()
    for index, row in enumerate(fingerprints, 1):
        if not isinstance(row, Mapping):
            raise ReadinessError("candidate fingerprint row is malformed")
        _verify_fingerprint(row, f"candidate_fingerprints[{index}]")
        stage = row.get("stage")
        candidate_id = row.get("candidate_id")
        source_subset_id = row.get("source_subset_id")
        if stage not in ("predicted_candidate", "real_top5_replay"):
            raise ReadinessError("candidate fingerprint stage differs")
        if not isinstance(candidate_id, str) or not candidate_id:
            raise ReadinessError("candidate fingerprint identity is absent")
        if (
            not isinstance(source_subset_id, str)
            or source_subset_id not in set(subset_ids)
        ):
            raise ReadinessError("fingerprint source subset identity differs")
        key = (str(stage), candidate_id)
        if key in seen_fingerprints:
            raise ReadinessError("duplicate fingerprint evidence row")
        seen_fingerprints.add(key)
    if (
        sum(row.get("stage") == "predicted_candidate" for row in fingerprints)
        != 15
        or sum(row.get("stage") == "real_top5_replay" for row in fingerprints)
        != 5
        or {
            row.get("source_subset_id")
            for row in fingerprints
            if row.get("stage") == "predicted_candidate"
        }
        != set(subset_ids)
        or [
            row.get("source_subset_id")
            for row in fingerprints
            if row.get("stage") == "real_top5_replay"
        ]
        != top5_ids
    ):
        raise ReadinessError("fingerprint stage cardinality differs")
    comparison = document.get("prediction_vs_real_analysis")
    if (
        not isinstance(comparison, Mapping)
        or comparison.get("predicted_top5_count") != 5
        or comparison.get("real_top5_count") != 5
        or comparison.get("real_order_complete") is not True
        or comparison.get("classification")
        not in ("gradient", "estimation", "fingerprint", "technical_sampling", "no_issue")
    ):
        raise ReadinessError("prediction-versus-real analysis is incomplete")
    return dict(document)


def _bound_document(
    path: Path, label: str, bundle_id: str, manifest_sha: str
) -> dict[str, Any]:
    value = load_object(path, label)
    verify_sidecar(path, label)
    if (
        value.get("bundle_id") != bundle_id
        or value.get("bundle_manifest_sha256") != manifest_sha
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
    ):
        raise ReadinessError(f"{label} is not bound to this bundle")
    return value


def _inspect_identity(row: Mapping[str, Any]) -> str:
    payload = {
        "Config": row.get("Config"),
        "HostConfig": row.get("HostConfig"),
        "Mounts": row.get("Mounts"),
        "Image": row.get("Image"),
        "Created": row.get("Created"),
    }
    return hashlib.sha256(canonical(payload)).hexdigest()


def _client_inspect_identity(row: Mapping[str, Any]) -> str:
    try:
        projection = client_container_immutable_projection(row)
    except ValueError as exc:
        raise ReadinessError(str(exc)) from exc
    return hashlib.sha256(canonical(projection)).hexdigest()


def _bootstrap_raw_identity(bootstrap: Mapping[str, Any]) -> dict[str, Any]:
    database = bootstrap.get("database")
    client = bootstrap.get("client")
    copy = bootstrap.get("copy")
    runtime = bootstrap.get("runtime_publication")
    effective_blkio = bootstrap.get("effective_blkio_authority")
    if not all(
        isinstance(row, Mapping)
        for row in (database, client, copy, runtime, effective_blkio)
    ):
        raise ReadinessError("bootstrap raw identity evidence is absent")
    db_inspect = database.get("inspect")
    client_inspect = client.get("inspect")
    if not isinstance(db_inspect, Mapping) or not isinstance(
        client_inspect, Mapping
    ):
        raise ReadinessError("bootstrap raw docker inspect evidence is absent")
    db_pid = int(database.get("pid") or 0)
    client_pid = int(client.get("pid") or 0)
    if (
        db_inspect.get("Id") != database.get("container_id")
        or client_inspect.get("Id") != client.get("container_id")
        or int((db_inspect.get("State") or {}).get("Pid") or 0) != db_pid
        or int((client_inspect.get("State") or {}).get("Pid") or 0)
        != client_pid
        or (db_inspect.get("State") or {}).get("Running") is not True
        or (client_inspect.get("State") or {}).get("Running") is not True
        or effective_blkio.get("database_container_id")
        != database.get("container_id")
        or int(effective_blkio.get("database_pid") or 0) != db_pid
        or effective_blkio.get("schema_version")
        != "mysql_tpch_blkio_v1_throttle_authority_v1"
        or effective_blkio.get("status")
        != "PASS_EFFECTIVE_CGROUP_V1_THROTTLE"
        or effective_blkio.get("read_bps") != 100663296
        or effective_blkio.get("write_bps") != 100663296
        or effective_blkio.get("accepted_round_authorized") is not False
    ):
        raise ReadinessError("bootstrap raw container/PID state differs")
    return {
        "database_container_id": database.get("container_id"),
        "database_pid": db_pid,
        "database_starttime_ticks": int(
            database.get("starttime_ticks") or 0
        ),
        "database_inspect_config_sha256": _inspect_identity(db_inspect),
        "database_datadir": copy.get("destination"),
        "client_container_id": client.get("container_id"),
        "client_pid": client_pid,
        "client_starttime_ticks": int(client.get("starttime_ticks") or 0),
        "client_inspect_config_sha256":
            _client_inspect_identity(client_inspect),
        "client_created": client_inspect.get("Created"),
        "client_image_id": client_inspect.get("Image"),
        "effective_blkio_authority_sha256":
            effective_blkio.get("authority_sha256"),
        "python_runtime_root": runtime.get("target_root"),
        "python_runtime_tree_content_sha256":
            runtime.get("tree_content_sha256"),
        "python_runtime_readback_sha256":
            runtime.get("target_readback_sha256"),
    }


def verify_promotion(
    bundle: Path,
    *,
    bootstrap_path: Path,
    dynamic_gate_path: Path,
    fresh_r0_path: Path,
    debug_round_path: Path,
    handoff_path: Path,
) -> dict[str, Any]:
    static = verify_bundle(bundle)
    manifest = static["manifest"]
    bundle_id = str(manifest["bundle_id"])
    manifest_sha = sha256(bundle / "bundle_manifest.json")
    bootstrap = _bound_document(
        bootstrap_path, "bootstrap", bundle_id, manifest_sha
    )
    dynamic = load_object(dynamic_gate_path, "dynamic gate")
    verify_sidecar(dynamic_gate_path, "dynamic gate")
    fresh = _bound_document(fresh_r0_path, "fresh R0", bundle_id, manifest_sha)
    debug = _bound_document(
        debug_round_path, "debug round", bundle_id, manifest_sha
    )
    handoff = _bound_document(handoff_path, "same-live handoff", bundle_id, manifest_sha)
    identity = bootstrap.get("same_live_identity")
    if not isinstance(identity, Mapping):
        raise ReadinessError("bootstrap same-live identity is absent")
    required_identity = {
        "database_container_id",
        "database_pid",
        "database_starttime_ticks",
        "database_inspect_config_sha256",
        "database_datadir",
        "client_container_id",
        "client_pid",
        "client_starttime_ticks",
        "client_inspect_config_sha256",
        "client_created",
        "client_image_id",
        "python_runtime_root",
        "python_runtime_tree_content_sha256",
        "python_runtime_readback_sha256",
        "effective_blkio_authority_sha256",
    }
    if (
        set(identity) != required_identity
        or any(
            not isinstance(identity[key], str) or not identity[key]
            for key in required_identity
            if key not in {
                "database_pid",
                "database_starttime_ticks",
                "client_pid",
                "client_starttime_ticks",
            }
        )
        or any(
            not isinstance(identity[key], int) or identity[key] <= 0
            for key in (
                "database_pid",
                "database_starttime_ticks",
                "client_pid",
                "client_starttime_ticks",
            )
        )
    ):
        raise ReadinessError("bootstrap same-live identity is incomplete")
    copy = bootstrap.get("copy") or {}
    ownership = bootstrap.get("template_owner_inventory") or {}
    extent_audit = copy.get("extent_audit") or {}
    runtime = bootstrap.get("runtime_publication") or {}
    runtime_readback = runtime.get("target_readback") or {}
    if (
        bootstrap.get("schema_version")
        != "mysql_tpch_distributed_v2_bootstrap_evidence_v1"
        or bootstrap.get("status")
        != "PASS_BOOTSTRAP_LIVE_NOT_R0_OR_FORMAL_AUTHORITY"
        or bootstrap.get("scenario") != "tpch_memory"
        or bootstrap.get("host_id") != "node04"
        or bootstrap.get("qualification_only") is not True
        or bootstrap.get("formal_r0_authorized") is not False
        or bootstrap.get("formal_authorized") is not False
        or bootstrap.get("accepted_round_authorized") is not False
        or bootstrap.get("v40_invalidation_sha256")
        != manifest.get("historical_v40_invalidation_sha256")
        or copy.get("command") != "tar regular-file byte stream"
        or copy.get("reflink") is not False
        or copy.get("clone_api_used") is not False
        or copy.get("full_physical_copy") is not True
        or copy.get("tree_identity_normalization")
        != TREE_IDENTITY_NORMALIZATION
        or copy.get("source_tree_sha256")
        != copy.get("destination_tree_sha256")
        or copy.get("file_count") != EXPECTED_TEMPLATE_REGULAR_FILE_COUNT
        or copy.get("byte_count") != EXPECTED_TEMPLATE_REGULAR_FILE_BYTES
        or not _target_permission_normalization_valid(
            copy.get("target_permission_normalization"),
            copy.get("file_count"),
        )
        or extent_audit.get("method")
        != "linux_fiemap_physical_extent_interval_intersection_v1"
        or extent_audit.get("shared_physical_extent_bytes") != 0
        or extent_audit.get("shared_physical_extents") is not False
        or extent_audit.get("audit_complete") is not True
        or ownership.get("method")
        not in {
            "stdlib_proc_fd_cwd_root_inventory",
            "stdlib_proc_inventory_with_readonly_host_pid_docker_fallback",
        }
        or ownership.get("complete") is not True
        or ownership.get("pass") is not True
        or (ownership.get("local") or {}).get("open_owners") != []
        or (
            ownership.get("docker_fallback_used") is True
            and (
                (ownership.get("docker_fallback") or {}).get("open_owners")
                != []
                or (ownership.get("docker_fallback") or {}).get(
                    "unreadable_pids"
                )
                != []
                or (ownership.get("docker_fallback") or {}).get(
                    "scanner_identity"
                )
                != ownership.get("docker_fallback_requested_identity")
                or not isinstance(
                    ownership.get("docker_fallback_requested_user"), str
                )
                or ownership.get("docker_fallback_requested_identity")
                != {"uid": 0, "gid": 0, "euid": 0, "egid": 0}
                or ownership.get("docker_fallback_requested_user") != "0:0"
                or ownership.get("docker_fallback_capabilities")
                != ["SYS_PTRACE", "DAC_READ_SEARCH"]
                or ownership.get("docker_fallback_network_mode") != "none"
                or ownership.get("docker_fallback_read_only_rootfs") is not True
                or ownership.get("docker_fallback_docker_socket_mount_prohibited")
                is not True
                or ownership.get("docker_fallback_root_scanner_required")
                is not True
            )
        )
        or runtime.get("publication_mode")
        != "tar_regular_file_byte_stream_no_clone_api"
        or runtime.get("source_root") != PYTHON_RUNTIME_SOURCE
        or runtime.get("target_root") != PYTHON_RUNTIME_TARGET
        or runtime.get("source_and_target_different_device") is not True
        or runtime.get("target_device_major_minor") != "259:1"
        or runtime.get("portable_manifest_sha256")
        != PYTHON_RUNTIME_MANIFEST_SHA256
        or runtime.get("tree_content_sha256")
        != PYTHON_RUNTIME_TREE_CONTENT_SHA256
        or runtime.get("target_readback_sha256")
        != hashlib.sha256(canonical(runtime_readback)).hexdigest()
        or runtime_readback.get("status")
        != "PASS_EXACT_PATH_INDEPENDENT_TREE_READBACK"
        or runtime_readback.get("root") != PYTHON_RUNTIME_TARGET
        or runtime_readback.get("tree_content_sha256")
        != PYTHON_RUNTIME_TREE_CONTENT_SHA256
        or runtime.get("atomic_rename_complete") is not True
        or runtime.get("reflink_clone_api_used") is not False
        or identity != _bootstrap_raw_identity(bootstrap)
    ):
        raise ReadinessError("bootstrap raw authority/identity differs")
    attempt = str(bootstrap.get("attempt_token") or "")
    if (
        dynamic.get("schema_version")
        != "mysql_algorithm_c_distributed_dynamic_gate_evidence_v1"
        or dynamic.get("status") != "PASS"
        or dynamic.get("policy_version")
        != "mysql_algorithm_c_same_host_bidirectional_interference_gate_v1"
        or dynamic.get("scenario") != "tpch_memory"
        or dynamic.get("host_id") != "node04"
        or dynamic.get("attempt_token") != attempt
        or dynamic.get("formal_scope") != "mysql_only"
        or dynamic.get("cross_engine_comparison_prohibited") is not True
        or dynamic.get("duration_seconds") != 180
        or dynamic.get("minimum_samples_per_stream") != 181
        or dynamic.get("required_streams") != EXPECTED_DYNAMIC_STREAMS
        or dynamic.get("streams_complete") != EXPECTED_DYNAMIC_STREAMS
        or dynamic.get("directions_passed") != EXPECTED_DYNAMIC_DIRECTIONS
        or set(dynamic.get("directions") or {})
        != set(EXPECTED_DYNAMIC_DIRECTIONS)
        or dynamic.get("both_directions_same_version_verified") is not True
        or dynamic.get("authorization_reuse_verified_absent") is not True
        or dynamic.get("ongoing_rolling_window_required") is not True
        or dynamic.get("collector_sha256")
        != manifest["component_hashes"]["collector"]
        or dynamic.get("database_counter_missing_samples") != 0
        or dynamic.get("database_counter_timeout_count") != 0
        or dynamic.get("all_tolerances_passed") is not True
        or dynamic.get("serial_fallback_required") is not False
        or dynamic.get("accepted_round_authorized") is not False
    ):
        raise ReadinessError("dynamic gate is incomplete")
    utilization = fresh.get("utilization") or {}
    r0_fingerprint = fresh.get("fingerprint") or {}
    r0_l1 = r0_fingerprint.get("l1") or {}
    r0_l2 = r0_fingerprint.get("l2") or {}
    r0_l3 = r0_fingerprint.get("l3") or {}
    r0_queries = r0_fingerprint.get("query_fingerprints")
    if (
        fresh.get("schema_version")
        != "mysql_tpch_distributed_v2_fresh_r0_evidence_v1"
        or fresh.get("status") != "PASS_FRESH_R0"
        or fresh.get("attempt_token") != attempt
        or fresh.get("same_live_identity") != identity
        or fresh.get("dynamic_gate_sha256") != sha256(dynamic_gate_path)
        or fresh.get("v40_reused") is not False
        or fresh.get("r0_profile_gate_passed") is not True
        or fresh.get("database_counter_gate_passed") is not True
        or fresh.get("database_counter_missing_samples") != 0
        or fresh.get("database_counter_timeout_count") != 0
        or int(fresh.get("stable_seconds") or 0) < 180
        or int(fresh.get("sample_count") or 0) < 181
        or not 0.30 <= finite(utilization.get("cpu"), "R0 CPU") <= 0.50
        or not 0.85 <= finite(utilization.get("memory"), "R0 memory") <= 0.95
        or not 0.30 <= finite(utilization.get("io"), "R0 IO") <= 0.50
        or fresh.get("accepted_rounds_credit") != 0
        or fresh.get("accepted_round_authorized") is not False
        or r0_fingerprint.get("schema_version")
        != "mysql_tpch_r0_l1_l2_l3_fingerprint_v1"
        or not isinstance(r0_queries, list)
        or len(r0_queries) != 22
        or [row.get("query") for row in r0_queries]
        != [f"Q{index}.sql" for index in range(1, 23)]
        or not isinstance(r0_l1.get("operator_types"), list)
        or not r0_l1["operator_types"]
        or not isinstance(r0_l1.get("join_order"), list)
        or not r0_l1["join_order"]
        or not isinstance(r0_l1.get("scan_types"), list)
        or not r0_l1["scan_types"]
        or not isinstance(r0_l1.get("parallel_structure"), dict)
        or not isinstance(r0_l2.get("workers"), dict)
        or not isinstance(r0_l2.get("hash_batches"), dict)
        or not isinstance(r0_l2.get("sort_spill"), dict)
        or not isinstance(r0_l2.get("bitmap_lossy_pages"), dict)
        or not isinstance(
            r0_l2.get("intermediate_result_scale"), dict
        )
        or not isinstance(r0_l3.get("memory_peak_bytes"), int)
        or r0_l3["memory_peak_bytes"] <= 0
        or not isinstance(
            r0_l3.get("dynamic_execution_memory_bytes"), int
        )
        or r0_l3["dynamic_execution_memory_bytes"] < 0
        or not isinstance(r0_l3.get("spill"), dict)
        or r0_fingerprint.get("missing_level_policy")
        != "independent_candidate"
    ):
        raise ReadinessError("fresh R0 is incomplete or outside profile")
    verify_debug_round_document(
        debug, bundle_id=bundle_id, attempt_token=attempt, identity=identity
    )
    if (
        handoff.get("schema_version")
        != "mysql_tpch_distributed_v2_same_live_formal10_handoff_v1"
        or handoff.get("status")
        != "READY_FORMAL10_SAME_LIVE_DB_AND_CLIENT"
        or handoff.get("attempt_token") != attempt
        or handoff.get("same_live_identity") != identity
        or handoff.get("new_database_or_client_container_prohibited") is not True
        or handoff.get("new_campaign_planner_prohibited") is not True
        or handoff.get("qualification_containers_adopted") is not True
        or handoff.get("ongoing_rolling_gate_required") is not True
        or handoff.get("v40_r0_reusable") is not False
        or handoff.get("historical_accepted_round_credit") != 0
        or handoff.get("formal10_authorized") is not True
        or handoff.get("target_accepted_rounds") != 10
        or handoff.get("next_accepted_round") != 1
        or handoff.get("accepted_round_authorized") is not False
    ):
        raise ReadinessError("same-live formal10 handoff differs")
    return {
        "schema_version": "mysql_tpch_formal10_readiness_result_v2",
        "status": "READY_TO_START_FORMAL10",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "bundle_id": bundle_id,
        "attempt_token": attempt,
        "same_live_identity": dict(identity),
        "target_accepted_rounds": 10,
        "next_accepted_round": 1,
        "capture_r0_debug_failed_replay_countable_as_accepted": False,
        "formal10_launch_authorized": True,
        "accepted_round_authorized": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--bootstrap", type=Path)
    parser.add_argument("--dynamic-gate", type=Path)
    parser.add_argument("--fresh-r0", type=Path)
    parser.add_argument("--debug-round", type=Path)
    parser.add_argument("--handoff", type=Path)
    args = parser.parse_args()
    supplied = [
        args.bootstrap,
        args.dynamic_gate,
        args.fresh_r0,
        args.debug_round,
        args.handoff,
    ]
    if any(path is not None for path in supplied):
        if any(path is None for path in supplied):
            raise ReadinessError("promotion verification requires all evidence")
        result = verify_promotion(
            args.bundle.resolve(),
            bootstrap_path=args.bootstrap,
            dynamic_gate_path=args.dynamic_gate,
            fresh_r0_path=args.fresh_r0,
            debug_round_path=args.debug_round,
            handoff_path=args.handoff,
        )
    else:
        result = verify_bundle(args.bundle.resolve())
        result = {
            "status": "STATIC_PASS_RUNTIME_EVIDENCE_PENDING",
            "bundle_id": result["manifest"]["bundle_id"],
            "formal10_launch_authorized": False,
            "accepted_round_authorized": False,
        }
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ReadinessError as exc:
        print(f"NOT_READY: {exc}")
        raise SystemExit(2)
