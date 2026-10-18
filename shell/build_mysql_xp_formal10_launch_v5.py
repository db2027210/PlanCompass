#!/usr/bin/env python3
"""Publish the immutable XP private-credential successor to eb2a8."""

import argparse
import errno
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile


ROOT = Path(__file__).resolve().parents[1]
PREDECESSOR_ID = (
    "eb2a8f79447105dd1df5fc1a9061d652abdc5f2db69d0c3f5672876561437e3a"
)
PREDECESSOR_MANIFEST_SHA256 = (
    "bb726cef21802a87777b44f80ab9a709d79ab2e887f836458fc5df9cf4353ad3"
)
PREDECESSOR = (
    ROOT / "artifacts/mysql_xp_formal10_launch_v4" / PREDECESSOR_ID
)
OUTPUT_ROOT = ROOT / "artifacts/mysql_xp_formal10_launch_v5"
COLLECTOR_SOURCE = (
    ROOT / "shell/mysql_xp_distributed_live_readback_qualification_v3.py"
)
COLLECTOR_RELATIVE = (
    "qualification/mysql_distributed_live_readback_qualification_v1.py"
)
SERVICE_SOURCE = ROOT / "shell/mysql_xp_node02_adopted_service_v2.py"
SERVICE_RELATIVE = "launch/mysql_xp_node02_adopted_service_v1.py"
AUTHORITY_SOURCE = ROOT / "shell/mysql_xp_qualification_authority_v2.py"
AUTHORITY_RELATIVE = "qualification/mysql_xp_qualification_authority_v1.py"
TEST_SOURCE = ROOT / "tests/test_mysql_xp_private_credentials_v3.py"
TEST_RELATIVE = "tests/test_mysql_xp_private_credentials_v3.py"
INVALIDATION_SOURCE = (
    ROOT
    / "preflight/mysql_xp_eb2a8_root_auth_invalidation_20260731.json"
)
INVALIDATION_RELATIVE = "invalidated_eb2a8_root_auth_bundle.json"
CONTRACT_RELATIVE = "xp_executable_launch_contract_v3.json"
RUNTIME_CONTRACT_RELATIVE = "distributed_runtime_contract.json"
BUILDER_RELATIVE = "build_mysql_xp_formal10_launch_v5.py"
SHA_RE_LENGTH = 64


class BuildError(RuntimeError):
    pass


def canonical(value):
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


def sha_bytes(payload):
    return hashlib.sha256(payload).hexdigest()


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise BuildError("required regular file absent: %s" % path)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise BuildError("JSON object required: %s" % path)
    return value


def relative_files(root):
    result = {}
    for path in sorted(Path(root).rglob("*")):
        if path.is_symlink():
            raise BuildError("symlink prohibited: %s" % path)
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            if relative not in (
                "bundle_manifest.json",
                "bundle_manifest.json.sha256",
            ):
                result[relative] = sha256(path)
    return result


def verify_bundle(bundle, expected_id=None):
    bundle = Path(bundle)
    manifest_path = bundle / "bundle_manifest.json"
    manifest = load(manifest_path)
    unsigned = dict(manifest)
    bundle_id = unsigned.pop("bundle_id", None)
    sidecar = bundle / "bundle_manifest.json.sha256"
    if (
        bundle_id != (expected_id or bundle.name)
        or len(str(bundle_id or "")) != SHA_RE_LENGTH
        or sha_bytes(canonical(unsigned)) != bundle_id
        or manifest.get("schema_version")
        != "mysql_xp_distributed_formal10_launch_v3"
        or manifest.get("scenario") != "hybench_xp_io"
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
        or not sidecar.is_file()
        or sidecar.read_text(encoding="ascii")
        != "%s  bundle_manifest.json\n" % sha256(manifest_path)
        or relative_files(bundle) != manifest.get("files")
    ):
        raise BuildError("bundle identity or closure differs: %s" % bundle)
    return manifest


def verify_predecessor():
    manifest = verify_bundle(PREDECESSOR, PREDECESSOR_ID)
    if sha256(PREDECESSOR / "bundle_manifest.json") != (
        PREDECESSOR_MANIFEST_SHA256
    ):
        raise BuildError("predecessor manifest SHA-256 differs")
    contract = load(PREDECESSOR / CONTRACT_RELATIVE)
    database = contract.get("database_resource_envelope") or {}
    capture = contract.get("capture_only_resource_envelope") or {}
    storage = contract.get("storage") or {}
    adopted = contract.get("adopted_service") or {}
    client = contract.get("client_resource_envelope") or {}
    if (
        database.get("cpu_quota_cores") != 5.3
        or database.get("io_read_bps") != 29360128
        or database.get("io_write_bps") != 524288
        or capture.get("io_read_bps") != 1205862400
        or capture.get("io_write_bps") != 1205862400
        or capture.get("accepted_rounds_credit") != 0
        or storage.get("data_partition") != "/dev/nvme0n1p1"
        or storage.get("data_partition_major_minor") != "259:4"
        or storage.get("io_throttle_parent_device") != "/dev/nvme0n1"
        or storage.get("io_throttle_parent_major_minor") != "259:1"
        or storage.get("parent_other_mount_holder_or_swap_prohibited")
        is not True
        or adopted.get("client_anonymous_volume_prohibited") is not True
        or adopted.get("client_pg_nvme2_mount_source_prohibited") is not True
        or client.get("target_mysql_device_write_prohibited") is not True
    ):
        raise BuildError("frozen XP envelope/storage isolation differs")
    runtime = load(PREDECESSOR / RUNTIME_CONTRACT_RELATIVE)
    if (
        runtime.get("scenario") != "hybench_xp_io"
        or runtime.get("formal_scope") != "mysql_only"
        or runtime.get("cross_engine_comparison_prohibited") is not True
    ):
        raise BuildError("predecessor distributed runtime contract differs")
    return manifest, contract, runtime


def replace_exact(value, old, new):
    if isinstance(value, dict):
        return {
            key: replace_exact(item, old, new)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [replace_exact(item, old, new) for item in value]
    return new if value == old else value


def successor_contract(contract, replacements):
    result = contract
    for old_sha, new_sha in replacements:
        result = replace_exact(result, old_sha, new_sha)
    new_collector_sha = replacements[0][1]
    result["collector_sha256"] = new_collector_sha
    client_pair = result.get("client_pair_tcp_readiness_successor")
    if not isinstance(client_pair, dict):
        raise BuildError("client-pair readiness contract absent")
    readiness = client_pair.get("readiness")
    if not isinstance(readiness, dict):
        raise BuildError("client-pair readiness gate absent")
    readiness.update(
        {
            "kind":
                "authenticated_exact_private_account_client_tcp_v2",
            "query": "SELECT @@port, CURRENT_USER()",
            "mysql_defaults_file":
                "/run/mysql-xp-private/mysql-client.cnf",
            "qualification_account_host": "192.0.2.6",
            "exact_account_required": True,
        }
    )
    result["private_credential_successor"] = {
        "predecessor_bundle_id": PREDECESSOR_ID,
        "predecessor_credit": 0,
        "secret_material": {
            "attempt_owned": True,
            "directory_mode": "0700",
            "file_mode": "0600",
            "client_mount_read_only": True,
            "password_in_manifest_command_or_log": False,
        },
        "database_provisioning": {
            "transport": "database_socket_private_stdin_v1",
            "account_host": "192.0.2.6",
            "global_privileges": ["PROCESS"],
            "schema": "hybench_sf10x",
            "schema_privileges": ["SELECT", "INSERT", "UPDATE", "DELETE"],
            "grant_all_prohibited": True,
        },
        "client_verification": {
            "query": "SELECT @@port, CURRENT_USER()",
            "exact_account_required": True,
            "collector_and_workload_share_attempt_private_credentials": True,
        },
        "resource_envelope_unchanged": {
            "cpu_quota_cores": 5.3,
            "io_read_bps": 29360128,
            "io_write_bps": 524288,
            "capture_io_bps_per_direction": 1205862400,
            "capture_accepted_rounds_credit": 0,
        },
        "storage_isolation_unchanged": {
            "data_partition": "/dev/nvme0n1p1",
            "throttle_parent": "/dev/nvme0n1",
            "anonymous_volume_prohibited": True,
            "postgresql_nvme2_mount_prohibited": True,
        },
    }
    return result


def successor_runtime_contract(runtime):
    result = dict(runtime)
    result["database_counter_transport"] = {
        "schema_version":
            "mysql_algorithm_c_database_counter_transport_v3",
        "kind": "exact_client_container_docker_exec_stdout_v1",
        "mysql_binary_in_client": "/usr/bin/mysql",
        "mysql_defaults_file_in_client":
            "/run/mysql-xp-private/mysql-client.cnf",
        "private_credential_destination": "/run/mysql-xp-private",
        "private_credential_filename": "mysql-client.cnf",
        "qualification_account_host": "192.0.2.6",
        "client_bundle_destination": "/opt/mysql-xp-bundle",
        "client_runtime_destination": "/opt/mysql-xp-jdk",
        "client_config_source_relative": "qualification/config",
        "client_config_destination": "/opt/mysql-xp-config",
        "stdout_only": True,
        "host_mysql_cli_required": False,
        "docker_socket_mount_prohibited": True,
        "target_datadir_mount_prohibited": True,
    }
    return result


def freeze(root):
    for path in sorted(Path(root).rglob("*"), reverse=True):
        if path.is_symlink():
            raise BuildError("bundle symlink prohibited")
        os.chmod(path, stat.S_IMODE(path.stat().st_mode) & ~0o222)
    os.chmod(root, stat.S_IMODE(root.stat().st_mode) & ~0o222)


def thaw_directories(root):
    if not Path(root).exists():
        return
    for path in [Path(root)] + sorted(Path(root).rglob("*")):
        if path.is_dir() and not path.is_symlink():
            os.chmod(path, stat.S_IMODE(path.stat().st_mode) | 0o700)


def fsync_tree(root):
    for path in sorted(Path(root).rglob("*")):
        if path.is_file():
            descriptor = os.open(str(path), os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    directories = [Path(root)] + [
        path for path in Path(root).rglob("*") if path.is_dir()
    ]
    for path in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        descriptor = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def publish(output_root=OUTPUT_ROOT):
    (
        predecessor_manifest,
        predecessor_contract,
        predecessor_runtime_contract,
    ) = verify_predecessor()
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".xp-v5.", dir=str(output_root)))
    try:
        for source in sorted(PREDECESSOR.rglob("*")):
            relative = source.relative_to(PREDECESSOR)
            if relative.as_posix() in (
                "bundle_manifest.json",
                "bundle_manifest.json.sha256",
                COLLECTOR_RELATIVE,
                CONTRACT_RELATIVE,
                RUNTIME_CONTRACT_RELATIVE,
                SERVICE_RELATIVE,
                AUTHORITY_RELATIVE,
            ):
                continue
            target = stage / relative
            if source.is_symlink():
                raise BuildError("predecessor symlink prohibited")
            if source.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            elif source.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(str(source), str(target))
                os.chmod(target, stat.S_IMODE(source.stat().st_mode))

        collector_payload = COLLECTOR_SOURCE.read_bytes()
        collector_sha = sha_bytes(collector_payload)
        service_payload = SERVICE_SOURCE.read_bytes()
        service_sha = sha_bytes(service_payload)
        authority_payload = AUTHORITY_SOURCE.read_bytes()
        authority_sha = sha_bytes(authority_payload)
        runtime_payload = canonical(
            successor_runtime_contract(predecessor_runtime_contract)
        )
        old_collector_sha = predecessor_manifest["collector_sha256"]
        old_service_sha = predecessor_manifest["files"][SERVICE_RELATIVE]
        old_authority_sha = predecessor_manifest["files"][AUTHORITY_RELATIVE]
        old_runtime_sha = predecessor_manifest["files"][
            RUNTIME_CONTRACT_RELATIVE
        ]
        contract = successor_contract(
            predecessor_contract,
            [
                (old_collector_sha, collector_sha),
                (old_service_sha, service_sha),
                (old_authority_sha, authority_sha),
                (old_runtime_sha, sha_bytes(runtime_payload)),
            ],
        )
        replacements = {
            COLLECTOR_RELATIVE: collector_payload,
            CONTRACT_RELATIVE: canonical(contract),
            RUNTIME_CONTRACT_RELATIVE: runtime_payload,
            SERVICE_RELATIVE: service_payload,
            AUTHORITY_RELATIVE: authority_payload,
            TEST_RELATIVE: TEST_SOURCE.read_bytes(),
            INVALIDATION_RELATIVE: INVALIDATION_SOURCE.read_bytes(),
            BUILDER_RELATIVE: Path(__file__).read_bytes(),
        }
        for relative, payload in replacements.items():
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
            os.chmod(target, 0o755 if relative.endswith(".py") else 0o644)

        files = relative_files(stage)
        identity = dict(predecessor_manifest)
        identity.pop("bundle_id", None)
        identity.update(
            {
                "schema_version":
                    "mysql_xp_distributed_formal10_launch_v3",
                "supersedes_bundle_id": PREDECESSOR_ID,
                "predecessor_bundle_id": PREDECESSOR_ID,
                "predecessor_manifest_sha256":
                    PREDECESSOR_MANIFEST_SHA256,
                "invalidated_client_bridge_bundle_id": PREDECESSOR_ID,
                "invalidated_root_auth_bundle_id": PREDECESSOR_ID,
                "root_blank_password_qualification_prohibited": True,
                "attempt_owned_private_credentials_required": True,
                "collector_base_sha256": old_collector_sha,
                "collector_sha256": collector_sha,
                "service_base_sha256": old_service_sha,
                "service_sha256": service_sha,
                "qualification_authority_base_sha256":
                    old_authority_sha,
                "qualification_authority_sha256": authority_sha,
                "distributed_runtime_contract_base_sha256":
                    old_runtime_sha,
                "distributed_runtime_contract_sha256":
                    files[RUNTIME_CONTRACT_RELATIVE],
                "launch_contract_sha256": files[CONTRACT_RELATIVE],
                "builder_sha256": files[BUILDER_RELATIVE],
                "formal_launch_authorized": False,
                "accepted_round_authorized": False,
                "offline_build_only": True,
                "remote_actions": 0,
                "fresh_r0_required": True,
                "historical_accepted_round_credit": 0,
                "publication": {
                    "content_addressed": True,
                    "transactional_same_filesystem_atomic_rename": True,
                    "non_overwriting": True,
                    "concurrent_identical_publish_idempotent": True,
                    "all_entries_frozen_before_visibility": True,
                    "fsync_before_and_after_rename": True,
                },
                "files": dict(sorted(files.items())),
            }
        )
        bundle_id = sha_bytes(canonical(identity))
        manifest = dict(identity)
        manifest["bundle_id"] = bundle_id
        payload = canonical(manifest)
        (stage / "bundle_manifest.json").write_bytes(payload)
        (stage / "bundle_manifest.json.sha256").write_text(
            "%s  bundle_manifest.json\n" % sha_bytes(payload),
            encoding="ascii",
        )
        freeze(stage)
        verify_bundle(stage, bundle_id)
        fsync_tree(stage)
        final = output_root / bundle_id
        if final.exists() or final.is_symlink():
            verify_bundle(final, bundle_id)
            thaw_directories(stage)
            shutil.rmtree(stage)
            return final
        try:
            os.rename(str(stage), str(final))
        except OSError as exc:
            if exc.errno not in (errno.EEXIST, errno.ENOTEMPTY):
                raise
            verify_bundle(final, bundle_id)
            thaw_directories(stage)
            shutil.rmtree(stage)
            return final
        descriptor = os.open(str(output_root), os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return verify_bundle(final, bundle_id) and final
    except BaseException:
        if stage.exists():
            thaw_directories(stage)
            shutil.rmtree(stage)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    args = parser.parse_args(argv)
    bundle = publish(args.output_root)
    print(
        json.dumps(
            {
                "schema_version": "mysql_xp_formal10_launch_build_v5",
                "status":
                    "PUBLISHED_IMMUTABLE_PRIVATE_CREDENTIAL_SUCCESSOR",
                "bundle_id": bundle.name,
                "bundle": str(bundle),
                "formal_launch_authorized": False,
                "accepted_round_authorized": False,
                "remote_actions": 0,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
