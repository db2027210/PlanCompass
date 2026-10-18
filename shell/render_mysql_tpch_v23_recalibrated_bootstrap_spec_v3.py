#!/usr/bin/env python3
"""Render a short-lived spec bound to the TPC-H v23-recalibrated successor."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import render_mysql_tpch_v23_recalibrated_base_spec_v3 as base


ROOT = HERE.parent
BUNDLE_ID = "b64f5520074fb423e1968cac4fec0f72ec0dee9b1e55d864e5330c5026d0fc1e"
MANIFEST_SHA256 = "282bb61f91f1a86f60bb8f020ede97c45a181466e26a607ba63476adc8639a8b"
DEFAULT_BUNDLE_MANIFEST = (
    ROOT / "artifacts/mysql_tpch_v23_recalibrated_successor_v3"
    / BUNDLE_ID / "bundle_manifest.json"
)
AUTHORITY = "mysql_tpch_distributed_v2_authority.py"
COLLECTOR = "qualification/mysql_distributed_live_readback_qualification_v1.py"
CONTRACT = "contracts/mysql_tpch_dedicated_daemon_contract_v1.json"
UNIT = "systemd/ra-docker-mysql-tpch-v1.service"
INVALIDATION = "invalidated_fb553a_shared_daemon_bundle.json"
INTERMEDIATE_INVALIDATION = (
    "invalidated_intermediate_dedicated_daemon_bundles.json"
)
SYSTEMD_INVALIDATION = "invalidated_00a0_systemd_wait_collect_bundle.json"
SYSTEMD_HELP_FIXTURE = "fixtures/node04_systemd_run_help_fixture_v1.json"
SYSTEMD_MATERIALIZER = "launch/mysql_tpch_systemd_oneshot_materializer_v1.py"
NAMESPACE_INVALIDATION = (
    "invalidated_08f1_missing_cgroup_namespace_bundle.json"
)
NAMESPACE_FIXTURE = (
    "fixtures/node04_kernel_namespace_capability_fixture_v1.json"
)
LIVE_DAEMON_INVALIDATION = "invalidated_9aa_static_fragment_bundle.json"
LIVE_DAEMON_SYSTEMCTL_FIXTURE = (
    "fixtures/node04_tpch_live_daemon_systemctl_fixture_v1.json"
)
LIVE_DAEMON_PROC_FIXTURE = (
    "fixtures/node04_tpch_live_daemon_proc_fixture_v1.json"
)
SOCKET_INVALIDATION = "invalidated_543_socket_inode_bundle.json"
SOCKET_FIXTURE = (
    "fixtures/node04_tpch_daemon_socket_identity_fixture_v1.json"
)
SELF_CONTAINED_INVALIDATION = "invalidated_313_nonselfcontained_bundle.json"
SELF_CONTAINED_VERIFIER = (
    "builder/build_mysql_tpch_socket_identity_successor_v1.py"
)
STALE_ENVELOPE_INVALIDATION = (
    "invalidated_b1ff_stale_resource_envelope_bundle.json"
)
STALE_HASH_INVALIDATION = (
    "invalidated_a0c_stale_four_scene_hash_binding_bundle.json"
)
RECALIBRATION_EVIDENCE = "tpch_v23_fresh_r0_recalibration_evidence.json"
OBSERVED_BUNDLE_ID = (
    "94beaa70a6c2f26c174c835a75a0e758bd42bd0bc776cca07d75785cf08b44ed"
)
OBSERVED_MANIFEST_SHA256 = (
    "57f14b2e0ea2a439236dbf1a4318d8679bce56b21abe7815f5d0192ab46448a2"
)
OBSERVED_BUNDLE_MANIFEST = (
    ROOT / "artifacts/mysql_tpch_observed_recalibration_successor_v1"
    / OBSERVED_BUNDLE_ID / "bundle_manifest.json"
)
AFFINITY_BUNDLE_ID = (
    "1b8ff1920184c113ce08a3a0ffda1f70c2bc3684bc01e7b296f5cb6704fe36e6"
)
AFFINITY_MANIFEST_SHA256 = (
    "64df8de078511046c76bf15778d971c20bb64368255fb5e4f9e1c6c4d73e71ac"
)
AFFINITY_BUNDLE_MANIFEST = (
    ROOT / "artifacts/mysql_tpch_affinity_remediation_successor_v1"
    / AFFINITY_BUNDLE_ID / "bundle_manifest.json"
)
AFFINITY_AUTHORITY = "authority/tpch_daemon_affinity_remediation_v1.json"
AFFINITY_AUTHORITY_SIDECAR = AFFINITY_AUTHORITY + ".sha256"
AFFINITY_INVALIDATION = "invalidated_19ac_affinity_unaware_attempt.json"
MEMSWAP_BUNDLE_ID = (
    "87ed507ed689c103781db5f96332ae7875e4773d04d69b98a7e63e3d4097b257"
)
MEMSWAP_MANIFEST_SHA256 = (
    "fe747c7811a6288a4b624f5bab48ace95727069a534e7314ec6e79938ca47e04"
)
MEMSWAP_BUNDLE_MANIFEST = (
    ROOT / "artifacts/mysql_tpch_memswap_consistency_successor_v1"
    / MEMSWAP_BUNDLE_ID / "bundle_manifest.json"
)
MEMSWAP_INVALIDATION = "invalidated_f6b_memswap_inconsistent_attempt.json"
BINDINGS = {
    BUNDLE_ID: {
        "manifest": DEFAULT_BUNDLE_MANIFEST,
        "manifest_sha256": MANIFEST_SHA256,
        "predecessor_bundle_id":
            "b1ffe6e8dd6396d6da1ee6cb7e2a3a6548a71cac9388b098873b56ed9cb0ff59",
        "envelope": {
            "cpu_quota_cores": 3.0,
            "io_read_bps": 335544320,
            "io_write_bps": 335544320,
            "profile_intervals": {
                "cpu": [0.3, 0.5], "io": [0.3, 0.5],
                "memory": [0.85, 0.95],
            },
        },
        "recalibration_evidence": RECALIBRATION_EVIDENCE,
        "sealed_bundle_pytest_regression_count": 39,
    },
    OBSERVED_BUNDLE_ID: {
        "manifest": OBSERVED_BUNDLE_MANIFEST,
        "manifest_sha256": OBSERVED_MANIFEST_SHA256,
        "predecessor_bundle_id": BUNDLE_ID,
        "envelope": {
            "cpu_quota_cores": 5.0,
            "memory_bytes": 6174015488,
            "memory_swap_bytes": 6174015488,
            "io_read_bps": 293601280,
            "io_write_bps": 293601280,
            "profile_intervals": {
                "cpu": [0.3, 0.5], "io": [0.3, 0.5],
                "memory": [0.85, 0.95],
            },
        },
        "recalibration_evidence":
            "tpch_observed_qualification_recalibration_evidence.json",
        "sealed_bundle_pytest_regression_count": 39,
    },
    AFFINITY_BUNDLE_ID: {
        "manifest": AFFINITY_BUNDLE_MANIFEST,
        "manifest_sha256": AFFINITY_MANIFEST_SHA256,
        "predecessor_bundle_id": OBSERVED_BUNDLE_ID,
        "envelope": {
            "cpu_quota_cores": 5.0,
            "memory_bytes": 6174015488,
            "memory_swap_bytes": 6174015488,
            "io_read_bps": 293601280,
            "io_write_bps": 293601280,
            "profile_intervals": {
                "cpu": [0.3, 0.5], "io": [0.3, 0.5],
                "memory": [0.85, 0.95],
            },
        },
        "recalibration_evidence":
            "tpch_observed_qualification_recalibration_evidence.json",
        "affinity_remediation": True,
        "sealed_bundle_pytest_regression_count": 39,
    },
    MEMSWAP_BUNDLE_ID: {
        "manifest": MEMSWAP_BUNDLE_MANIFEST,
        "manifest_sha256": MEMSWAP_MANIFEST_SHA256,
        "predecessor_bundle_id": AFFINITY_BUNDLE_ID,
        "envelope": {
            "cpu_quota_cores": 5.0,
            "memory_bytes": 6174015488,
            "memory_swap_bytes": 6174015488,
            "io_read_bps": 293601280,
            "io_write_bps": 293601280,
            "profile_intervals": {
                "cpu": [0.3, 0.5], "io": [0.3, 0.5],
                "memory": [0.85, 0.95],
            },
        },
        "recalibration_evidence":
            "tpch_observed_qualification_recalibration_evidence.json",
        "affinity_remediation": True,
        "memswap_consistency": True,
        "sealed_bundle_pytest_regression_count": 39,
    },
}
ATTEMPT_RE = base.ATTEMPT_RE
RenderError = base.RenderError
canonical = base.canonical
atomic_new = base.atomic_new
sha256 = base.sha256


def load(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise RenderError("dedicated successor file is absent or symlinked")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RenderError("dedicated successor JSON object required")
    return value


def verify_successor(manifest_path: Path) -> dict[str, Any]:
    global BUNDLE_ID, MANIFEST_SHA256, DEFAULT_BUNDLE_MANIFEST
    global RECALIBRATION_EVIDENCE
    manifest_path = manifest_path.resolve(strict=True)
    try:
        candidate = load(manifest_path)
        binding = BINDINGS[candidate["bundle_id"]]
    except (KeyError, TypeError):
        raise RenderError("renderer bundle identity is not allow-listed")
    BUNDLE_ID = candidate["bundle_id"]
    MANIFEST_SHA256 = binding["manifest_sha256"]
    DEFAULT_BUNDLE_MANIFEST = binding["manifest"]
    RECALIBRATION_EVIDENCE = binding["recalibration_evidence"]
    if (
        manifest_path.parent.name != BUNDLE_ID
        or sha256(manifest_path) != MANIFEST_SHA256
    ):
        raise RenderError("renderer is bound only to the self-contained successor")
    manifest = load(manifest_path)
    unsigned = dict(manifest)
    identity = unsigned.pop("bundle_id", None)
    files = manifest.get("files") or {}
    expected_contract = {
        "mode": "direct_host_systemd_transient_oneshot",
        "unit_template":
            "ra-mysql-tpch-bootstrap-<attempt_token>.service",
        "service_type": "oneshot",
        "remain_after_exit": True,
        "ppid": 1,
        "bind_main_pid_startticks_namespaces_cgroup": True,
        "docker_ancestry_prohibited": True,
        "token_transport": "token_file_or_in_process_only",
        "token_in_os_argv_prohibited": True,
        "controller_poll_after_nonblocking_start": True,
        "poll_properties": [
            "ActiveState", "SubState", "ExecMainStatus", "Result",
        ],
        "unit_retained_for_audit": True,
        "wait_option_prohibited": True,
        "collect_option_prohibited": True,
    }
    if (
        identity != BUNDLE_ID
        or hashlib.sha256(canonical(unsigned)).hexdigest() != identity
        or manifest.get("schema_version")
        != "mysql_tpch_v23_recalibrated_successor_v3"
        or manifest.get("predecessor_bundle_id")
        != binding["predecessor_bundle_id"]
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("postgresql_mutation_prohibited") is not True
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
        or manifest.get("all_runtime_docker_cli_calls_explicit_host") is not True
        or manifest.get("database_and_client_network_mode") != "host"
        or manifest.get("database_mysqld_port") != 65103
        or manifest.get("bootstrap_execution_contract") != expected_contract
        or manifest.get("static_daemon_unit_removed_from_bundle") is not True
        or manifest.get("static_daemon_unit_deployment_prohibited") is not True
        or manifest.get("live_daemon_mutation_prohibited") is not True
        or manifest.get("self_contained_sealed_verification") is not True
        or manifest.get("sealed_tests_external_repository_dependency") is not False
        or manifest.get("sealed_tests_predecessor_dependency") is not False
        or manifest.get("sealed_bundle_pytest_regression_count")
        != binding["sealed_bundle_pytest_regression_count"]
        or manifest.get("fresh_r0_required") is not True
        or manifest.get("v23_failed_r0_credit") != 0
        or manifest.get("parent_bundle_credit") != 0
        or manifest.get("fresh_r0_envelope_preserved") != binding["envelope"]
        or (
            binding.get("successor_generation") is not None
            and manifest.get("successor_generation")
            != binding["successor_generation"]
        )
        or (
            binding.get("shutdown_successor_generation") is not None
            and manifest.get("shutdown_successor_generation")
            != binding["shutdown_successor_generation"]
        )
        or (
            binding.get("collector_sha256") is not None
            and (manifest.get("component_hashes") or {}).get("collector")
            != binding["collector_sha256"]
        )
    ):
        raise RenderError("systemd-compatible successor authority differs")
    for relative in (
        AUTHORITY, COLLECTOR, CONTRACT, INVALIDATION,
        INTERMEDIATE_INVALIDATION, SYSTEMD_INVALIDATION,
        SYSTEMD_HELP_FIXTURE, SYSTEMD_MATERIALIZER,
        NAMESPACE_INVALIDATION, NAMESPACE_FIXTURE,
        LIVE_DAEMON_INVALIDATION, LIVE_DAEMON_SYSTEMCTL_FIXTURE,
        LIVE_DAEMON_PROC_FIXTURE, SOCKET_INVALIDATION, SOCKET_FIXTURE,
        SELF_CONTAINED_INVALIDATION, SELF_CONTAINED_VERIFIER,
        STALE_ENVELOPE_INVALIDATION, STALE_HASH_INVALIDATION,
        RECALIBRATION_EVIDENCE,
    ):
        path = manifest_path.parent / relative
        if (
            path.is_symlink()
            or not path.is_file()
            or files.get(relative) != sha256(path)
        ):
            raise RenderError("dedicated successor closure differs: " + relative)
    if binding.get("affinity_remediation") is True:
        for relative in (
            AFFINITY_AUTHORITY,
            AFFINITY_AUTHORITY_SIDECAR,
            AFFINITY_INVALIDATION,
            "builder/build_mysql_tpch_affinity_remediation_successor_v1.py",
        ):
            path = manifest_path.parent / relative
            if (
                path.is_symlink()
                or not path.is_file()
                or files.get(relative) != sha256(path)
            ):
                raise RenderError(
                    "affinity-remediation successor closure differs: " + relative
                )
        affinity = load(manifest_path.parent / AFFINITY_AUTHORITY)
        invalid_affinity_attempt = load(
            manifest_path.parent / AFFINITY_INVALIDATION
        )
        if (
            manifest.get("affinity_remediation_required") is not True
            or manifest.get("daemon_declared_affinity_preserved")
            != "34-35,70-71"
            or manifest.get("daemon_effective_affinity_required")
            != "32-33,68-69"
            or manifest.get("affinity_remediation_authority_sha256")
            != "c6906e3580f88b6e21fcaa41250b51cc76952656e7a20fe08efed4370ce0e853"
            or manifest.get("affinity_remediation_internal_sha256")
            != "915d27c99be693866bb31f0260727e2012bf58351bd7d9d5da9a8cdb4433528c"
            or affinity.get("status")
            != "PASS_EFFECTIVE_DISJOINT_DAEMON_AFFINITY"
            or affinity.get("main_pid") != 97636
            or affinity.get("main_pid_startticks") != 233390522
            or affinity.get("declared_exec_affinity") != "34-35,70-71"
            or affinity.get("effective_affinity_after") != "32-33,68-69"
            or affinity.get("peer_effective_affinity") != "34-35,70-71"
            or affinity.get("formal_launch_authorized") is not False
            or affinity.get("accepted_round_authorized") is not False
            or invalid_affinity_attempt.get("status")
            != "INVALIDATED_DO_NOT_RERUN_OR_CREDIT"
            or invalid_affinity_attempt.get("attempt_token")
            != "tpch-v2-20260731t135939z-19acbf59bbf9f4ccab2cb1ac"
            or invalid_affinity_attempt.get("workload_started") is not False
            or invalid_affinity_attempt.get("accepted_rounds_credit") != 0
        ):
            raise RenderError("affinity-remediation authority differs")
    if binding.get("memswap_consistency") is True:
        for relative in (
            MEMSWAP_INVALIDATION,
            "builder/build_mysql_tpch_memswap_consistency_successor_v1.py",
        ):
            path = manifest_path.parent / relative
            if (
                path.is_symlink()
                or not path.is_file()
                or files.get(relative) != sha256(path)
            ):
                raise RenderError(
                    "memswap-consistency successor closure differs: " + relative
                )
        failed = load(manifest_path.parent / MEMSWAP_INVALIDATION)
        if (
            manifest.get("docker_memory_swap_feasibility_verified") is not True
            or manifest.get("memory_bytes") != 6174015488
            or manifest.get("memory_swap_bytes") != 6174015488
            or manifest.get("swap_capacity_bytes") != 0
            or failed.get("status")
            != "INVALIDATED_ZERO_CREDIT_DO_NOT_RERUN"
            or failed.get("attempt_token")
            != "tpch-v2-20260731t142242z-f6b7168fd98f56ffd1ae8c2d"
            or failed.get("database_container_created") is not False
            or failed.get("workload_started") is not False
            or failed.get("accepted_rounds_credit") != 0
        ):
            raise RenderError("memswap-consistency authority differs")
    contract = load(manifest_path.parent / CONTRACT)
    invalid = load(manifest_path.parent / INVALIDATION)
    intermediate = load(manifest_path.parent / INTERMEDIATE_INVALIDATION)
    systemd_invalid = load(manifest_path.parent / SYSTEMD_INVALIDATION)
    help_fixture = load(manifest_path.parent / SYSTEMD_HELP_FIXTURE)
    namespace_invalid = load(manifest_path.parent / NAMESPACE_INVALIDATION)
    namespace_fixture = load(manifest_path.parent / NAMESPACE_FIXTURE)
    live_invalid = load(manifest_path.parent / LIVE_DAEMON_INVALIDATION)
    live_systemctl = load(
        manifest_path.parent / LIVE_DAEMON_SYSTEMCTL_FIXTURE
    )
    live_proc = load(manifest_path.parent / LIVE_DAEMON_PROC_FIXTURE)
    socket_invalid = load(manifest_path.parent / SOCKET_INVALIDATION)
    socket_fixture = load(manifest_path.parent / SOCKET_FIXTURE)
    self_contained_invalid = load(
        manifest_path.parent / SELF_CONTAINED_INVALIDATION
    )
    stale_envelope_invalid = load(
        manifest_path.parent / STALE_ENVELOPE_INVALIDATION
    )
    stale_hash_invalid = load(
        manifest_path.parent / STALE_HASH_INVALIDATION
    )
    recalibration = load(manifest_path.parent / RECALIBRATION_EVIDENCE)
    if (
        contract.get("socket")
        != "/run/ra-docker-mysql-tpch-v1.sock"
        or contract.get("data_root")
        != "/mnt/nvme0n1/anonymous/resource_adaptive_mysql/docker_ra_tpch_v1"
        or contract.get("device_major_minor") != "259:1"
        or contract.get("database_network_mode") != "host"
        or contract.get("client_network_mode") != "host"
        or contract.get("mysqld_port") != 65103
        or invalid.get("status") != "INVALIDATED_DO_NOT_RERUN"
        or invalid.get("accepted_rounds_credit") != 0
        or intermediate.get("status") != "INVALIDATED_DO_NOT_RUN_OR_CREDIT"
        or intermediate.get("accepted_rounds_credit") != 0
        or systemd_invalid.get("status")
        != "INVALIDATED_DO_NOT_RUN_OR_CREDIT"
        or systemd_invalid.get("accepted_rounds_credit") != 0
        or help_fixture.get("stdout_sha256")
        != "b2d1a41d2a3e6402d3927ed76f7e2e053ba425ccd80d4d6c6bf1b5a25a53a665"
        or help_fixture.get("systemd_version") != 219
        or "--wait" in str(help_fixture.get("stdout") or "")
        or namespace_invalid.get("status")
        != "INVALIDATED_DO_NOT_RUN_OR_CREDIT"
        or namespace_invalid.get("accepted_rounds_credit") != 0
        or namespace_fixture.get("stdout_sha256")
        != "544486c6bdaab05ad05efa950b6afa40eb11b98d3dbfa4f7fa7aa795cd63c215"
        or namespace_fixture.get("kernel_release")
        != "3.10.0-1160.71.1.el7"
        or (manifest.get("namespace_capability_contract") or {}).get(
            "mandatory"
        ) != ["ipc", "mnt", "net", "pid", "user", "uts"]
        or (manifest.get("namespace_capability_contract") or {}).get(
            "optional_cgroup"
        ) != {"supported": False, "path_absent": True}
        or live_invalid.get("status")
        != "INVALIDATED_DO_NOT_RUN_OR_CREDIT"
        or live_invalid.get("accepted_rounds_credit") != 0
        or live_systemctl.get("stdout_sha256")
        != "121543dbd5ab15833b5c29f895c177fc9ef9808097108c7329f94e3b541efe8c"
        or live_proc.get("stdout_sha256")
        != "740e9638b4a701dfbff92d029b05801b4c1d7be33ab7efb9765645fba337f513"
        or socket_invalid.get("status")
        != "INVALIDATED_DO_NOT_RUN_OR_CREDIT"
        or socket_invalid.get("accepted_rounds_credit") != 0
        or socket_fixture.get("stdout_sha256")
        != "9ccd4e410ed5fee46ed8a104a55eae0717cf244cc3a6a4fc20ef26c7e3b211f3"
        or (manifest.get("socket_listener_authority") or {}).get(
            "filesystem_validation"
        ) != "S_IFSOCK_only"
        or (manifest.get("socket_listener_authority") or {}).get(
            "listener_cardinality"
        ) != 1
        or self_contained_invalid.get("status")
        != "INVALIDATED_DO_NOT_RUN_OR_CREDIT"
        or self_contained_invalid.get("accepted_rounds_credit") != 0
        or self_contained_invalid.get("bundle_id")
        != "313637860bee5f333c11d8ae92689ef674585d8e7311c82b75c1cb7cc1fd9555"
        or stale_envelope_invalid.get("status")
        != "INVALIDATED_DO_NOT_RUN_OR_CREDIT"
        or stale_envelope_invalid.get("bundle_id")
        != "b1ffe6e8dd6396d6da1ee6cb7e2a3a6548a71cac9388b098873b56ed9cb0ff59"
        or stale_envelope_invalid.get("accepted_rounds_credit") != 0
        or stale_hash_invalid.get("status")
        != "INVALIDATED_DO_NOT_RUN_OR_CREDIT"
        or stale_hash_invalid.get("bundle_id")
        != "a0cebba1482d70728cb0fcfefb474696f11b4896a53007d8db92858b3145cd0a"
        or stale_hash_invalid.get("accepted_rounds_credit") != 0
        or recalibration.get("fresh_r0_required") is not True
        or recalibration.get("formal_launch_authorized") is not False
        or recalibration.get("accepted_round_authorized") is not False
        or (recalibration.get("successor_envelope") or {}).get(
            "cpu_quota_cores"
        ) != binding["envelope"]["cpu_quota_cores"]
        or (recalibration.get("successor_envelope") or {}).get(
            "io_read_bps"
        ) != binding["envelope"]["io_read_bps"]
        or (recalibration.get("successor_envelope") or {}).get(
            "io_write_bps"
        ) != binding["envelope"]["io_write_bps"]
    ):
        raise RenderError("dedicated daemon contract/invalidation differs")
    for path in manifest_path.parent.rglob("*"):
        if path.name == "__pycache__" or path.suffix == ".pyc":
            raise RenderError("bytecode/cache prohibited from sealed bundle")
    return manifest


def render(args: argparse.Namespace) -> dict[str, Any]:
    verify_successor(args.bundle_manifest)
    base.DEFAULT_BUNDLE_ID = BUNDLE_ID
    base.DEFAULT_BUNDLE_MANIFEST_SHA256 = MANIFEST_SHA256
    base.DEFAULT_BUNDLE_MANIFEST = DEFAULT_BUNDLE_MANIFEST
    document = base.render(args)
    attempt = document["attempt_token"]
    document["dedicated_daemon"] = load(
        args.bundle_manifest.parent / CONTRACT
    )
    document["bootstrap_execution"] = {
        "mode": "direct_host_systemd_transient_oneshot",
        "unit": "ra-mysql-tpch-bootstrap-" + attempt + ".service",
        "service_type": "oneshot",
        "remain_after_exit": True,
        "ppid": 1,
        "token_transport": "token_file_or_in_process_only",
        "docker_ancestry_prohibited": True,
        "unit_retained_for_audit": True,
    }
    return document


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bundle-manifest", type=Path, default=DEFAULT_BUNDLE_MANIFEST
    )
    parser.add_argument("--mysql-image-reference", required=True)
    parser.add_argument("--mysql-image-id", required=True)
    parser.add_argument("--client-image-reference", required=True)
    parser.add_argument("--client-image-id", required=True)
    parser.add_argument("--main-agent-token-file", type=Path, required=True)
    parser.add_argument("--attempt-token")
    parser.add_argument("--valid-minutes", type=int, default=25)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    document = render(args)
    payload = canonical(document)
    atomic_new(args.output, payload)
    atomic_new(
        Path(str(args.output) + ".sha256"),
        (
            hashlib.sha256(payload).hexdigest()
            + "  " + args.output.name + "\n"
        ).encode("ascii"),
    )
    print(json.dumps({
        "status": "PASS_RENDERED_DEDICATED_DAEMON_QUALIFICATION_SPEC",
        "bundle_id": BUNDLE_ID,
        "attempt_token": document["attempt_token"],
        "formal_r0_authorized": False,
        "accepted_round_authorized": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RenderError, UnicodeError, ValueError) as exc:
        sys.stderr.write("NOT_READY: %s\n" % exc)
        raise SystemExit(2)
