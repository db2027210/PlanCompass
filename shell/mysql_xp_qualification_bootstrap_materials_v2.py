#!/usr/bin/env python3
"""Create local-only, short-lived bootstrap materials for an XP successor.

This producer never connects to node02, invokes Docker, or starts a workload.
It emits an attempt-unique specification, command plan, and private main-agent
token.  The plan is deliberately qualification-only and requires a separate
main-agent decision before any listed host command is executed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BUNDLE = (
    ROOT / "artifacts/mysql_xp_formal10_launch_v4/"
    "eb2a8f79447105dd1df5fc1a9061d652abdc5f2db69d0c3f5672876561437e3a"
)
EXPECTED_BUNDLE_ID = (
    "eb2a8f79447105dd1df5fc1a9061d652abdc5f2db69d0c3f5672876561437e3a"
)
EXPECTED_MANIFEST_SHA256 = (
    "bb726cef21802a87777b44f80ab9a709d79ab2e887f836458fc5df9cf4353ad3"
)
DEFAULT_OUTPUT_ROOT = ROOT / "artifacts/mysql_xp_qualification_bootstrap_v3"
REMOTE_RUNTIME_ROOT = (
    "/mnt/mysql_nvme0n1p1/anonymous/resource_adaptive_mysql/hybench_xp/"
    "runtime_algorithm_c_successor_20260729"
)
HOST = "node02"
HOST_CONTROL_CPUSET = "21-33,57-69"
TOKEN_RE = re.compile(r"[A-Za-z0-9_.:-]{32,128}$")


class MaterialError(RuntimeError):
    pass


def canonical(value):
    return (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                   allow_nan=False)
        + "\n"
    ).encode("utf-8")


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_sealed_json(path, label):
    path = Path(path)
    sidecar = Path(str(path) + ".sha256")
    if (path.is_symlink() or sidecar.is_symlink() or not path.is_file()
            or not sidecar.is_file()):
        raise MaterialError(label + " is not sealed")
    expected = "%s  %s\n" % (sha256(path), path.name)
    if sidecar.read_text(encoding="ascii") != expected:
        raise MaterialError(label + " sidecar differs")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise MaterialError(label + " is not an object")
    return value


def write_sealed(path, value, mode=0o444):
    path = Path(path)
    sidecar = Path(str(path) + ".sha256")
    if path.exists() or path.is_symlink() or sidecar.exists() or sidecar.is_symlink():
        raise MaterialError("refusing to overwrite " + path.name)
    data = canonical(value)
    temporary = path.with_name(".%s.%d.tmp" % (path.name, os.getpid()))
    with temporary.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(str(temporary), str(path))
    sidecar.write_text(
        "%s  %s\n" % (hashlib.sha256(data).hexdigest(), path.name),
        encoding="ascii",
    )
    os.chmod(path, mode)
    os.chmod(sidecar, mode)


def verified_bundle(bundle):
    bundle = Path(bundle).resolve(strict=True)
    manifest = load_sealed_json(bundle / "bundle_manifest.json", "bundle manifest")
    contract_path = bundle / "xp_executable_launch_contract_v3.json"
    files = manifest.get("files")
    if (
        contract_path.is_symlink() or not contract_path.is_file()
        or not isinstance(files, dict)
        or files.get(contract_path.name) != sha256(contract_path)
    ):
        raise MaterialError("manifest-bound launch contract differs")
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    if not isinstance(contract, dict):
        raise MaterialError("launch contract is not an object")
    if (
        bundle.name != EXPECTED_BUNDLE_ID
        or sha256(bundle / "bundle_manifest.json")
        != EXPECTED_MANIFEST_SHA256
        or manifest.get("bundle_id") != bundle.name
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
        or manifest.get("supersedes_bundle_id")
        != "31b220eaa7feda556f4179a9fa6558a8fd586cc9635555612c16a45acf20fc4a"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("postgresql_mutation_prohibited") is not True
        or contract.get("client_pair_tcp_readiness_successor", {}).get(
            "predecessor_bundle_id"
        )
        != "31b220eaa7feda556f4179a9fa6558a8fd586cc9635555612c16a45acf20fc4a"
    ):
        raise MaterialError("successor bundle scope/identity differs")
    required = {
        "launch/mysql_xp_node02_adopted_service_v1.py",
        "launch/mysql_xp_adoption_reconciliation_v1.py",
        "launch/mysql_xp_capture_authority_v1.py",
        "launch/mysql_xp_capture_transition_v1.py",
        "launch/mysql_xp_raw_evidence_executor_v1.py",
        "runtime/mysql_xp_node02_jdk_publish_v1.py",
        "qualification/mysql_xp_qualification_authority_v1.py",
        "qualification/mysql_distributed_live_readback_qualification_v1.py",
    }
    for relative in required:
        candidate = bundle / relative
        if (
            candidate.is_symlink() or not candidate.is_file()
            or files.get(relative) != sha256(candidate)
        ):
            raise MaterialError("successor entrypoint differs: " + relative)
    db = contract.get("database_resource_envelope")
    client = contract.get("client_resource_envelope")
    storage = contract.get("storage")
    toolchain = contract.get("toolchain")
    host_contract = contract.get("distributed_host_contract")
    if not all(
        isinstance(row, dict)
        for row in (db, client, storage, toolchain, host_contract)
    ):
        raise MaterialError("successor resource closure differs")
    if (
        db.get("cpu_quota_cores") != 5.3
        or db.get("cpuset_cpus") != HOST_CONTROL_CPUSET
        or db.get("cpuset_mems") != "1"
        or db.get("io_read_bps") != 29360128
        or db.get("io_write_bps") != 524288
        or client.get("cpuset_cpus") != "3-11,39-47"
        or client.get("cpuset_mems") != "0"
        or storage.get("full_physical_copy_required") is not True
        or storage.get("data_partition") != "/dev/nvme0n1p1"
        or storage.get("data_partition_major_minor") != "259:4"
        or storage.get("io_throttle_parent_device") != "/dev/nvme0n1"
        or storage.get("io_throttle_parent_major_minor") != "259:1"
        or storage.get("docker_bps_declaration_device") != "/dev/nvme0n1"
        or host_contract.get("host_id") != HOST
        or host_contract.get("database_cpuset_cpus") != HOST_CONTROL_CPUSET
        or host_contract.get("cpuset_change_requires_main_agent_authority")
        is not True
        or host_contract.get("postgresql_mutation_prohibited") is not True
        or storage.get("effective_cgroup_v1_throttle_major_minor") != "259:1"
        or storage.get("data_partition_and_throttle_parent_must_not_be_conflated") is not True
        or toolchain.get("tree_content_sha256")
        != "578395e883d31c692a1c01c37910d22dbdafbcf95e373c7e0f6776a26f5c844c"
    ):
        raise MaterialError("successor formal envelope differs")
    collector = contract.get("collector_throttle_binding")
    invalidated = bundle / "invalidated_parent_throttle_bundle.json"
    if (
        not isinstance(collector, dict)
        or collector.get("data_partition") != "/dev/nvme0n1p1"
        or collector.get("data_partition_major_minor") != "259:4"
        or collector.get("docker_bps_declaration_device") != "/dev/nvme0n1"
        or collector.get("effective_cgroup_v1_throttle_major_minor") != "259:1"
        or collector.get("io_read_bps") != 29360128
        or collector.get("io_write_bps") != 524288
        or collector.get("exact_single_docker_binding_required") is not True
        or collector.get("exact_effective_v1_binding_required") is not True
        or manifest.get("collector_sha256") != sha256(
            bundle / "qualification/mysql_distributed_live_readback_qualification_v1.py"
        )
        or manifest.get("invalidated_parent_throttle_bundle_id")
        != "9bfdd22abba1330fbb3938609f21102bccad68293b1d987b1dd9f7514a652568"
        or not invalidated.is_file()
        or files.get(invalidated.name) != sha256(invalidated)
    ):
        raise MaterialError("successor parent-throttle collector closure differs")
    transition = contract.get("capture_transition")
    executor = contract.get("raw_evidence_executor")
    if (
        not isinstance(transition, dict)
        or transition.get("database_blkio_parent_major_minor") != "259:1"
        or transition.get("capture_io_bps_per_direction") != 1205862400
        or transition.get("formal_io_read_bps") != 29360128
        or transition.get("formal_io_write_bps") != 524288
        or transition.get("transactional_readback_and_restore_required") is not True
        or not isinstance(executor, dict)
        or executor.get("capture_ledger_requires_adopted_client_cgroup_identity") is not True
    ):
        raise MaterialError("successor capture transition closure differs")
    return bundle, manifest, contract


def new_attempt_id(now):
    return "xp-formal-qual-%s-%s" % (
        now.strftime("%Y%m%dT%H%M%SZ"), secrets.token_hex(12)
    )


def create_materials(bundle_path, output_root):
    bundle, manifest, contract = verified_bundle(bundle_path)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    attempt_id = new_attempt_id(now)
    if TOKEN_RE.fullmatch(attempt_id) is None:
        raise MaterialError("generated attempt identity differs")
    output = Path(output_root).resolve() / attempt_id
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() or output.is_symlink():
        raise MaterialError("attempt directory already exists")
    output.mkdir(mode=0o700)
    token = "xp-formal-main-agent-" + secrets.token_urlsafe(36)
    if TOKEN_RE.fullmatch(token) is None:
        raise MaterialError("generated token differs")
    token_path = output / "main_agent_token"
    descriptor = os.open(str(token_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, (token + "\n").encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    remote_bundle = REMOTE_RUNTIME_ROOT + "/bundles/" + bundle.name
    remote_attempt = REMOTE_RUNTIME_ROOT + "/" + attempt_id
    created = now.isoformat()
    expires = (now + timedelta(minutes=25)).isoformat()
    db = contract["database_resource_envelope"]
    client = contract["client_resource_envelope"]
    storage = contract["storage"]
    toolchain = contract["toolchain"]
    spec = {
        "schema_version": "mysql_xp_successor_qualification_bootstrap_spec_v1",
        "status": "QUALIFICATION_ONLY_NOT_LAUNCHED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "host": HOST,
        "bundle": {
            "id": bundle.name,
            "local_manifest_sha256": sha256(bundle / "bundle_manifest.json"),
            "remote_path": remote_bundle,
            "remote_manifest_sha256_required": sha256(bundle / "bundle_manifest.json"),
        },
        "attempt": {
            "id": attempt_id,
            "handoff_token": attempt_id,
            "main_agent_token_path": str(token_path),
            "main_agent_token_sha256": hashlib.sha256(token.encode("utf-8")).hexdigest(),
            "main_agent_token_mode": "0600",
        },
        "created_at_utc": created,
        "expires_at_utc": expires,
        "qualification_only": True,
        "capture_authorized": False,
        "formal_r0_authorized": False,
        "formal_authorized": False,
        "accepted_round_authorized": False,
        "paths": {
            "attempt_root": remote_attempt,
            "datadir": remote_attempt + "/datadir",
            "host_output_tmpfs": remote_attempt + "/host_measurement_tmpfs",
            "copy_authority": remote_attempt + "/physical_copy_authority.json",
            "jdk_publication": remote_attempt + "/jdk_publication.json",
            "adoption": remote_attempt + "/adopted_service.json",
        },
        "physical_copy": {
            "required": True,
            "source": storage["template_path"],
            "target": remote_attempt + "/datadir",
            "argv": ["cp", "-a", "--", "SOURCE/.", "TARGET"],
            "full_copy_required": True,
            "reflink_or_shared_extent_prohibited": True,
            "copy_cpu_affinity": HOST_CONTROL_CPUSET,
            "attempt_datadir_owner": contract["adopted_service"]["attempt_datadir_owner"],
            "attempt_datadir_directory_mode": contract["adopted_service"]["attempt_datadir_directory_mode"],
            "attempt_datadir_regular_mode": contract["adopted_service"]["attempt_datadir_regular_mode"],
        },
        "jdk": {
            "source": toolchain["deployment_source"],
            "target": toolchain["deployment_target"],
            "tree_content_sha256": toolchain["tree_content_sha256"],
            "java_binary_sha256": toolchain["java_binary_sha256"],
            "publication": remote_attempt + "/jdk_publication.json",
            "content_addressed_full_tree_copy_required": True,
        },
        "host_output_tmpfs": {
            "path": remote_attempt + "/host_measurement_tmpfs",
            "minimum_bytes": 2147483648,
            "filesystem": "tmpfs",
            "required_options": ["rw", "nosuid", "nodev", "noexec"],
            "must_exist_before_bootstrap": True,
            "not_client_mounted": True,
        },
        "formal_envelope": {
            "database": db,
            "client": client,
            "storage": {
                "data_partition": storage["data_partition"],
                "data_partition_major_minor": storage["data_partition_major_minor"],
                "io_throttle_parent_device": storage["io_throttle_parent_device"],
                "io_throttle_parent_major_minor": storage["io_throttle_parent_major_minor"],
                "docker_bps_declaration_device": storage["docker_bps_declaration_device"],
                "effective_cgroup_v1_throttle_major_minor": storage["effective_cgroup_v1_throttle_major_minor"],
                "data_partition_and_throttle_parent_must_not_be_conflated": True,
                "unique_partition_required": True,
                "parent_other_mount_holder_or_swap_prohibited": True,
            },
        },
    }
    common = [
        "--bundle", remote_bundle,
        "--database-image", contract["adopted_service"]["required_image_id"],
        "--client-image", contract["adopted_service"]["required_image_id"],
        "--config-dir", remote_bundle + "/qualification/config",
        "--datadir", spec["paths"]["datadir"],
        "--host-output-tmpfs", spec["paths"]["host_output_tmpfs"],
        "--runtime-publication", spec["paths"]["jdk_publication"],
        "--attempt-id", attempt_id,
        "--handoff-token", attempt_id,
        "--main-agent-token-sha256", spec["attempt"]["main_agent_token_sha256"],
        "--authorization-created-at-utc", created,
        "--authorization-expires-at-utc", expires,
    ]
    prefix = ["taskset", "-c", HOST_CONTROL_CPUSET, "env", "PYTHONDONTWRITEBYTECODE=1", "python3", "-B"]
    service = remote_bundle + "/launch/mysql_xp_node02_adopted_service_v1.py"
    plan = {
        "schema_version": "mysql_xp_successor_qualification_command_plan_v1",
        "status": "PLANNED_NOT_LAUNCHED",
        "spec_sha256": hashlib.sha256(canonical(spec)).hexdigest(),
        "host": HOST,
        "ssh_prohibited": True,
        "remote_shell_wrapper_prohibited": True,
        "qualification_only": True,
        "formal_r0_authorized": False,
        "formal_authorized": False,
        "accepted_round_authorized": False,
        "requires_unexpired_spec": True,
        "effective_uid_required_by_command": {
            "physical_copy": 0,
            "bootstrap": 0,
        },
        "commands_are_inner_argv": True,
        "nonroot_direct_execution_of_physical_copy_or_bootstrap_prohibited":
            True,
        "main_agent_host_root_executor_required_for": [
            "physical_copy", "bootstrap",
        ],
        "bootstrap_root_reason":
            "normalized_datadir_is_uid_999_mode_0700_and_pristine_full_tree_readback_is_required",
        "remote_bundle_manifest_sha256_required": spec["bundle"]["remote_manifest_sha256_required"],
        "execution_order": [
            "verify_remote_bundle_manifest",
            "physical_copy",
            "jdk_publish",
            "mount_attempt_owned_host_tmpfs",
            "plan_only",
            "bootstrap_with_private_token",
            "adopt_readback_and_real_pid_check",
        ],
        "commands_to_run_on_node02_after_separate_authorization": {
            "jdk_publish": prefix + [
                remote_bundle + "/runtime/mysql_xp_node02_jdk_publish_v1.py",
                "--bundle", remote_bundle,
                "--output", spec["paths"]["jdk_publication"],
            ],
            "physical_copy": prefix + [
                service, "--prepare-copy", "--attempt-id", attempt_id,
                "--output", spec["paths"]["copy_authority"],
            ],
            "plan_only": prefix + [service, "--plan"] + common,
            "bootstrap": prefix + [service, "--bootstrap"] + common + [
                "--main-agent-token", "READ_PRIVATE_TOKEN_FILE_NOT_LITERAL",
                "--physical-copy-authority", spec["paths"]["copy_authority"],
                "--output", spec["paths"]["adoption"],
            ],
            "adopt_readback": prefix + [service, "--adopt"] + common + [
                "--physical-copy-authority", spec["paths"]["copy_authority"],
                "--output", spec["paths"]["adoption"],
            ],
        },
        "publication_validation": {
            "bundle_manifest_sha256": spec["bundle"]["remote_manifest_sha256_required"],
            "adopted_service_sha256": contract["adopted_service"]["sha256"],
            "jdk_tree_sha256": spec["jdk"]["tree_content_sha256"],
            "java_binary_sha256": spec["jdk"]["java_binary_sha256"],
            "physical_copy_authority_sidecar_required": True,
            "adoption_sidecar_required": True,
            "remote_bundle_is_read_only_exact_closure_required": True,
            "formal_envelope_readback_exact_required": True,
            "host_tmpfs_readback_required": True,
            "fresh_database_and_client_pid_starttime_required": True,
        },
        "publication_validation_commands_to_run_on_node02_after_separate_authorization": {
            "bundle_manifest": [
                "sha256sum", remote_bundle + "/bundle_manifest.json",
            ],
            "bundle_entrypoint": [
                "sha256sum",
                remote_bundle
                + "/launch/mysql_xp_node02_adopted_service_v1.py",
            ],
        },
    }
    try:
        write_sealed(output / "bootstrap_spec.json", spec)
        write_sealed(output / "command_plan.json", plan)
        manifest_value = {
            "schema_version": "mysql_xp_successor_qualification_materials_manifest_v1",
            "status": "SEALED_QUALIFICATION_ONLY_NOT_LAUNCHED",
            "bundle_id": bundle.name,
            "bundle_manifest_sha256": sha256(bundle / "bundle_manifest.json"),
            "attempt_id": attempt_id,
            "created_at_utc": created,
            "expires_at_utc": expires,
            "files": {
                "bootstrap_spec.json": sha256(output / "bootstrap_spec.json"),
                "command_plan.json": sha256(output / "command_plan.json"),
                "main_agent_token": sha256(token_path),
            },
            "main_agent_token_mode": "0600",
            "formal_r0_authorized": False,
            "formal_authorized": False,
            "accepted_round_authorized": False,
        }
        write_sealed(output / "materials_manifest.json", manifest_value)
    except BaseException:
        raise
    return {
        "status": "SEALED_QUALIFICATION_ONLY_NOT_LAUNCHED",
        "attempt_id": attempt_id,
        "bundle_id": bundle.name,
        "bundle_manifest_sha256": sha256(bundle / "bundle_manifest.json"),
        "materials_directory": str(output),
        "bootstrap_spec": str(output / "bootstrap_spec.json"),
        "command_plan": str(output / "command_plan.json"),
        "main_agent_token_file": str(token_path),
        "expires_at_utc": expires,
        "formal_r0_authorized": False,
        "accepted_round_authorized": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args()
    print(json.dumps(create_materials(args.bundle, args.output_root), sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except MaterialError as exc:
        raise SystemExit("NOT_READY: %s" % exc)
