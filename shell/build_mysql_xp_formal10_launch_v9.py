#!/usr/bin/env python3
"""Seal the XP v9 self-contained-test successor without remote actions."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import stat


ROOT = Path(__file__).resolve().parents[1]
PARENT_ID = (
    "2f7824bf1b7f234ebd754bf6e3283420c6eaa789618a7a38f6d75472338b2087"
)
PARENT_MANIFEST_SHA256 = (
    "8cb77864c55b5b51d846abc736c5ec872cb0cdcf7883550e86652e5e190c6412"
)
PARENT = ROOT / "artifacts/mysql_xp_formal10_launch_v8" / PARENT_ID
OUTPUT = ROOT / "artifacts/mysql_xp_formal10_launch_v9"
TEST_SUPPORT = (
    "test_support/mysql_xp_qualification_bootstrap_materials_v5.py"
)
INVALIDATION = (
    "authority/mysql_xp_v8_bundle_and_ttl_materials_invalidation_20260731.json"
)


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


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def replace_required(source, old, new, label):
    count = source.count(old)
    if count < 1:
        raise BuildError("%s patch target is absent" % label)
    return source.replace(old, new)


def verify_parent():
    path = PARENT / "bundle_manifest.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    unsigned = dict(value)
    bundle_id = unsigned.pop("bundle_id", None)
    if (
        bundle_id != PARENT_ID
        or digest(path) != PARENT_MANIFEST_SHA256
        or sha_bytes(canonical(unsigned)) != PARENT_ID
        or value.get("schema_version")
        != "mysql_xp_distributed_formal10_launch_v3"
        or not isinstance(value.get("files"), dict)
    ):
        raise BuildError("XP v8 parent identity differs")
    actual = {}
    for candidate in sorted(PARENT.rglob("*")):
        if candidate.is_symlink():
            raise BuildError("XP v8 symlink prohibited")
        if candidate.is_file():
            relative = candidate.relative_to(PARENT).as_posix()
            if relative not in (
                "bundle_manifest.json",
                "bundle_manifest.json.sha256",
            ):
                actual[relative] = digest(candidate)
    if actual != value["files"]:
        raise BuildError("XP v8 full manifest closure differs")
    return value


def patch_test(relative, payload):
    source = payload.decode("utf-8")
    replacements = {
        'ROOT / "shell/mysql_xp_node02_adopted_service_v2.py"':
            'ROOT / "launch/mysql_xp_node02_adopted_service_v1.py"',
        'ROOT / "shell/mysql_xp_node02_adopted_service_v3.py"':
            'ROOT / "launch/mysql_xp_node02_adopted_service_v1.py"',
        'ROOT / "shell/mysql_xp_node02_adopted_service_v4.py"':
            'ROOT / "launch/mysql_xp_node02_adopted_service_v1.py"',
        'ROOT / "shell/mysql_xp_distributed_live_readback_qualification_v2.py"':
            'ROOT / "qualification/mysql_distributed_live_readback_qualification_v1.py"',
        'ROOT / "shell/mysql_xp_distributed_live_readback_qualification_v3.py"':
            'ROOT / "qualification/mysql_distributed_live_readback_qualification_v1.py"',
        'ROOT / "shell/mysql_xp_distributed_live_readback_qualification_v4.py"':
            'ROOT / "qualification/mysql_distributed_live_readback_qualification_v1.py"',
        'ROOT / "shell/mysql_xp_qualification_bootstrap_materials_v3.py"':
            'ROOT / "test_support/mysql_xp_qualification_bootstrap_materials_v5.py"',
        'ROOT / "shell/mysql_xp_qualification_bootstrap_materials_v4.py"':
            'ROOT / "test_support/mysql_xp_qualification_bootstrap_materials_v5.py"',
        'ROOT / "shell/mysql_xp_qualification_bootstrap_materials_v5.py"':
            'ROOT / "test_support/mysql_xp_qualification_bootstrap_materials_v5.py"',
        'ROOT / "shell/mysql_xp_raw_evidence_executor_v2.py"':
            'ROOT / "launch/mysql_xp_raw_evidence_executor_v1.py"',
        'ROOT / "artifacts/worker_systemd_run_help_20260731/"\n'
        '    "node02_node04_systemd_run_help.stdout"':
            'ROOT / "authority/node02_systemd_run_help.stdout"',
    }
    changed = False
    for old, new in replacements.items():
        if old in source:
            source = source.replace(old, new)
            changed = True
    if not changed:
        raise BuildError("bundle test contains no recognized source path")
    if 'ROOT / "shell/' in source or 'ROOT / "artifacts/' in source:
        raise BuildError("bundle test retains external project layout")
    graphdriver = (
        '        "GraphDriver": {\n'
        '            "Name": "overlay2",\n'
        '            "Data": {\n'
        '                "LowerDir": "/dedicated/overlay2/lower",\n'
        '                "MergedDir": "/dedicated/overlay2/merged",\n'
        '                "UpperDir": "/dedicated/overlay2/upper",\n'
        '                "WorkDir": "/dedicated/overlay2/work",\n'
        '            },\n'
        '        },\n'
    )
    if relative == "tests/test_mysql_xp_client_tcp_pair_authority_v2.py":
        source = replace_required(
            source,
            '        "HostConfig": {\n'
            '            "NetworkMode": "host",\n'
            '            "CpuPeriod": 100000,\n'
            '            "CpuQuota": 530000 if role == "database" else 1800000,\n'
            '        },\n',
            '        "HostConfig": {\n'
            '            "NetworkMode": "host",\n'
            '            "CpuPeriod": 100000,\n'
            '            "CpuQuota": 530000 if role == "database" else 1800000,\n'
            '            "CpusetCpus": "21-33,57-69",\n'
            '            "CpusetMems": "1",\n'
            '            "Memory": 8589934592,\n'
            '            "MemorySwap": 8589934592,\n'
            '            "PidsLimit": 1024,\n'
            '            "ReadonlyRootfs": False,\n'
            '            "SecurityOpt": [],\n'
            '            "CapDrop": [],\n'
            '            "LogConfig": {"Type": "json-file", "Config": {}},\n'
            '            "RestartPolicy": {"Name": "no"},\n'
            '        },\n',
            "v4 full HostConfig fixture",
        )
        source = replace_required(
            source,
            '        "Mounts": [],\n'
            '    }\n\n\n'
            'def test_dynamic_names_bind_by_labels_not_suffix',
            '        "Mounts": [],\n'
            + graphdriver
            + '    }\n\n\n'
            'def test_dynamic_names_bind_by_labels_not_suffix',
            "v4 pair GraphDriver fixture",
        )
        source = replace_required(
            source,
            '    bundle = tmp_path / BUNDLE_ID\n'
            '    defaults = bundle / "qualification/config/mysql-client.cnf"\n'
            '    defaults.parent.mkdir(parents=True)\n'
            '    defaults.write_text("[client]\\n", encoding="utf-8")\n',
            '    bundle = tmp_path / BUNDLE_ID\n'
            '    (bundle / "qualification/config").mkdir(parents=True)\n'
            '    runtime_root = tmp_path / "runtime"\n'
            '    defaults = (\n'
            '        runtime_root / "attempt-with-dashes/private_credentials/"\n'
            '        "mysql-client.cnf"\n'
            '    )\n'
            '    defaults.parent.mkdir(parents=True, mode=0o700)\n'
            '    defaults.write_text("[client]\\n", encoding="utf-8")\n'
            '    defaults.chmod(0o600)\n',
            "v4 private defaults fixture",
        )
        source = replace_required(
            source,
            '"schema_version": "mysql_algorithm_c_database_counter_transport_v2"',
            '"schema_version": "mysql_algorithm_c_database_counter_transport_v3"',
            "v3 counter transport schema",
        )
        source = replace_required(
            source,
            '            "mysql_defaults_file_relative":\n'
            '                "qualification/config/mysql-client.cnf",\n'
            '            "mysql_defaults_file_in_client":\n'
            '                "/opt/mysql-xp-config/mysql-client.cnf",\n',
            '            "mysql_defaults_file_in_client":\n'
            '                "/run/mysql-xp-private/mysql-client.cnf",\n'
            '            "private_credential_destination":\n'
            '                "/run/mysql-xp-private",\n'
            '            "private_credential_filename": "mysql-client.cnf",\n'
            '            "qualification_account_host": "192.0.2.6",\n',
            "v3 private transport fields",
        )
        source = replace_required(
            source,
            '            "target_datadir_mount_prohibited": True,\n'
            '        },\n'
            '        "toolchain": {"deployment_target": "/runtime/jdk"},\n',
            '            "target_datadir_mount_prohibited": True,\n'
            '            "docker_host":\n'
            '                "unix:///run/ra-docker-mysql-xp-v2.sock",\n'
            '        },\n'
            '        "storage": {"runtime_root": str(runtime_root)},\n'
            '        "toolchain": {"deployment_target": "/runtime/jdk"},\n',
            "v3 dedicated socket and runtime root",
        )
        source = source.replace(
            '"/opt/mysql-xp-config/mysql-client.cnf"',
            '"/run/mysql-xp-private/mysql-client.cnf"',
        )
        source = source.replace(
            '        "pair_identity": pair,\n',
            '        "qualification_user": "ra_user",\n'
            '        "qualification_account_host": "192.0.2.6",\n'
            '        "pair_identity": pair,\n',
        )
        source = source.replace(
            '        "pair_identity": mod._container_pair_snapshot(\n',
            '        "qualification_user": "ra_user",\n'
            '        "qualification_account_host": "192.0.2.6",\n'
            '        "pair_identity": mod._container_pair_snapshot(\n',
        )
        source = source.replace(
            'return "65104\\tra_user\\n"',
            'return "65104\\tra_user@192.0.2.6\\n"',
        )
        source = replace_required(
            source,
            '    assert calls[0][:4] == [\n'
            '        "docker", "exec", client["Id"], "/usr/bin/mysql"\n'
            '    ]\n',
            '    assert calls[0][:6] == [\n'
            '        "docker", "--host", mod.DOCKER_HOST,\n'
            '        "exec", client["Id"], "/usr/bin/mysql",\n'
            '    ]\n',
            "dedicated Docker argv assertion",
        )
    if relative in (
        "tests/test_mysql_xp_private_credentials_v3.py",
        "tests/test_mysql_xp_private_credentials_v4.py",
    ):
        source = replace_required(
            source,
            '            "Config": {\n'
            '                "Image": "sha256:" + "f" * 64,\n'
            '                "Labels": labels,\n'
            '            },\n'
            '            "HostConfig": {"NetworkMode": "host"},\n'
            '            "Mounts": [],\n',
            '            "Config": {\n'
            '                "Image": "sha256:" + "f" * 64,\n'
            '                "Entrypoint": None,\n'
            '                "Cmd": ["sleep", "infinity"],\n'
            '                "User": "",\n'
            '                "Labels": labels,\n'
            '            },\n'
            '            "HostConfig": {\n'
            '                "NetworkMode": "host",\n'
            '                "CpuPeriod": 100000,\n'
            '                "CpuQuota": 530000 if role == "database" else 1800000,\n'
            '                "CpusetCpus": "21-33,57-69",\n'
            '                "CpusetMems": "1",\n'
            '                "Memory": 8589934592,\n'
            '                "MemorySwap": 8589934592,\n'
            '                "PidsLimit": 1024,\n'
            '                "ReadonlyRootfs": False,\n'
            '                "SecurityOpt": [],\n'
            '                "CapDrop": [],\n'
            '                "LogConfig": {"Type": "json-file", "Config": {}},\n'
            '                "RestartPolicy": {"Name": "no"},\n'
            '            },\n'
            '            "Mounts": [],\n'
            + graphdriver.replace("        ", "            ", 1),
            "v4 private collector static inspect fixture",
        )
    return source.encode("utf-8")


def patch_test_support(payload):
    source = payload.decode("utf-8")
    source = replace_required(
        source,
        'DEFAULT_BUNDLE = (\n'
        '    ROOT / "artifacts/mysql_xp_formal10_launch_v8/"\n'
        '    "2f7824bf1b7f234ebd754bf6e3283420c6eaa789618a7a38f6d75472338b2087"\n'
        ')\n',
        "DEFAULT_BUNDLE = ROOT\n",
        "test-support bundle root",
    )
    source = replace_required(
        source,
        'DEFAULT_OUTPUT_ROOT = ROOT / "artifacts/mysql_xp_qualification_bootstrap_v8"\n',
        'DEFAULT_OUTPUT_ROOT = ROOT / "test-output-prohibited"\n',
        "test-support output root",
    )
    source = replace_required(
        source,
        'SYSTEMD_HELP_FIXTURE = (\n'
        '    ROOT / "artifacts/worker_systemd_run_help_20260731/"\n'
        '    "node02_node04_systemd_run_help.stdout"\n'
        ')\n',
        'SYSTEMD_HELP_FIXTURE = (\n'
        '    ROOT / "authority/node02_systemd_run_help.stdout"\n'
        ')\n',
        "test-support fixture",
    )
    source = replace_required(
        source,
        '        bundle.name != EXPECTED_BUNDLE_ID\n'
        '        or sha256(bundle / "bundle_manifest.json")\n'
        '        != EXPECTED_MANIFEST_SHA256\n'
        '        or manifest.get("bundle_id") != bundle.name\n',
        '        manifest.get("bundle_id") != bundle.name\n',
        "test-support dynamic bundle identity",
    )
    return source.encode("utf-8")


def invalidation():
    return {
        "schema_version":
            "mysql_xp_v8_bundle_and_ttl_materials_invalidation_v1",
        "status": "INVALID_ZERO_CREDIT",
        "invalidated_at_utc": "2026-07-31T07:14:13+00:00",
        "bundle_id": PARENT_ID,
        "bundle_manifest_sha256": PARENT_MANIFEST_SHA256,
        "reason":
            "SEALED_BUNDLE_TESTS_REFERENCE_ABSENT_EXTERNAL_SOURCE_LAYOUT",
        "observed_bundle_test_result": {
            "passed": 0,
            "failed": 42,
            "failure_type": "FileNotFoundError",
            "missing_root_prefixes": ["shell/", "artifacts/"],
        },
        "runtime_closure_reused_only_via_new_content_addressed_successor":
            True,
        "invalidated_intermediate_v9_bundle": {
            "bundle_id":
                "147f75a82c627a36a659fc213933e83797e1f49000b60ba4cf664fc8ed4b48f3",
            "bundle_manifest_sha256":
                "9b2842ca93e020fdd48c54e6070eb5cb9514c7f7fe3db201b6c5ea1838a799fe",
            "observed_test_result": {"passed": 34, "failed": 8},
            "credit": 0,
            "reason": "STALE_V2_FIXTURES_REJECTED_BY_HARDENED_V4_AUTHORITY",
        },
        "invalidated_ttl_materials": [
            {
                "attempt_id":
                    "xp-formal-qual-20260731T062327Z-a922d5c60c7d15079880a14d",
                "materials_manifest_sha256":
                    "1f26a7e5ddfa389c457a03aa83cd251669e1892743d8a9f058903978ddf40752",
                "created_at_utc": "2026-07-31T06:23:27+00:00",
                "expires_at_utc": "2026-07-31T06:48:27+00:00",
                "reason": "BOUND_TO_INVALID_V8_BUNDLE",
                "credit": 0,
            },
            {
                "attempt_id":
                    "xp-formal-qual-20260731T070312Z-a77e83266a75e9df583e0869",
                "materials_manifest_sha256":
                    "94382fc8c5c036c100b2384355164124cc37a9f277bad14c6ccffd5a66634df2",
                "created_at_utc": "2026-07-31T07:03:12+00:00",
                "expires_at_utc": "2026-07-31T07:28:12+00:00",
                "reason": "BOUND_TO_INVALID_V8_BUNDLE",
                "credit": 0,
            },
        ],
        "qualification_credit": 0,
        "capture_credit": 0,
        "r0_credit": 0,
        "candidate_credit": 0,
        "replay_credit": 0,
        "accepted_round_credit": 0,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "reuse_or_publish_prohibited": True,
    }


def freeze(root):
    for path in sorted(Path(root).rglob("*"), reverse=True):
        if path.is_symlink():
            raise BuildError("symlink prohibited in XP v9")
        os.chmod(path, stat.S_IMODE(path.stat().st_mode) & ~0o222)
    os.chmod(root, stat.S_IMODE(Path(root).stat().st_mode) & ~0o222)


def verify_bundle(path, expected_id=None):
    path = Path(path)
    manifest_path = path / "bundle_manifest.json"
    sidecar = path / "bundle_manifest.json.sha256"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    unsigned = dict(manifest)
    bundle_id = unsigned.pop("bundle_id", None)
    actual = {}
    bytecode = []
    for candidate in sorted(path.rglob("*")):
        if candidate.is_symlink():
            raise BuildError("XP v9 symlink prohibited")
        if stat.S_IMODE(candidate.stat().st_mode) & 0o222:
            raise BuildError("XP v9 writable entry: " + str(candidate))
        if candidate.name == "__pycache__" or candidate.suffix in (
            ".pyc", ".pyo",
        ):
            bytecode.append(str(candidate))
        if candidate.is_file():
            relative = candidate.relative_to(path).as_posix()
            if relative not in (
                "bundle_manifest.json",
                "bundle_manifest.json.sha256",
            ):
                actual[relative] = digest(candidate)
    if (
        bundle_id != (expected_id or path.name)
        or bundle_id != path.name
        or sha_bytes(canonical(unsigned)) != bundle_id
        or sidecar.read_text(encoding="ascii")
        != "%s  bundle_manifest.json\n" % digest(manifest_path)
        or actual != manifest.get("files")
        or manifest.get("schema_version")
        != "mysql_xp_distributed_formal10_launch_v3"
        or manifest.get("self_contained_bundle_tests") is not True
        or manifest.get("invalidated_v8_bundle_credit") != 0
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
        or bytecode
    ):
        raise BuildError("XP v9 immutable identity differs")
    return manifest


def publish():
    parent = verify_parent()
    payload = {}
    modes = {}
    for relative in parent["files"]:
        source = PARENT / relative
        if relative.startswith("tests/") and relative.endswith(".py"):
            payload[relative] = patch_test(relative, source.read_bytes())
        else:
            payload[relative] = source.read_bytes()
        modes[relative] = stat.S_IMODE(source.stat().st_mode) & ~0o222
    payload[TEST_SUPPORT] = patch_test_support(
        (ROOT / "shell/mysql_xp_qualification_bootstrap_materials_v5.py")
        .read_bytes()
    )
    modes[TEST_SUPPORT] = 0o444
    payload[INVALIDATION] = canonical(invalidation())
    modes[INVALIDATION] = 0o444
    files = {
        relative: sha_bytes(value)
        for relative, value in sorted(payload.items())
    }
    identity = {
        key: item
        for key, item in parent.items()
        if key not in ("bundle_id", "files")
    }
    identity.update(
        {
            "predecessor_v8_bundle_id": PARENT_ID,
            "predecessor_v8_manifest_sha256": PARENT_MANIFEST_SHA256,
            "v8_runtime_payload_files_preserved": len(parent["files"]) - 6,
            "v8_test_files_retargeted": 6,
            "self_contained_bundle_tests": True,
            "bundle_tests_external_repository_dependency_prohibited": True,
            "bundle_tests_publish_or_builder_dependency_prohibited": True,
            "test_support_materializer": TEST_SUPPORT,
            "test_support_not_runtime_entrypoint": True,
            "v8_observed_test_failures": 42,
            "v9_required_bundle_test_passes": 42,
            "regression_14_and_23_sets_preserved": True,
            "invalidated_v8_bundle_id": PARENT_ID,
            "invalidated_v8_bundle_credit": 0,
            "invalidated_v8_ttl_material_count": 2,
            "invalidated_v8_ttl_material_credit": 0,
            "v8_invalidation_relative": INVALIDATION,
            "v8_invalidation_sha256": sha_bytes(payload[INVALIDATION]),
            "formal_launch_authorized": False,
            "accepted_round_authorized": False,
            "offline_build_only": True,
            "remote_actions": 0,
            "files": files,
        }
    )
    bundle_id = sha_bytes(canonical(identity))
    manifest = dict(identity)
    manifest["bundle_id"] = bundle_id
    OUTPUT.mkdir(parents=True, exist_ok=True)
    final = OUTPUT / bundle_id
    if final.exists() or final.is_symlink():
        freeze(final)
        verify_bundle(final, bundle_id)
        return final
    stage = OUTPUT / (".stage-" + bundle_id + "-" + secrets.token_hex(6))
    stage.mkdir(mode=0o700)
    try:
        for relative, value in payload.items():
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(value)
            os.chmod(target, modes[relative])
        manifest_payload = canonical(manifest)
        manifest_path = stage / "bundle_manifest.json"
        manifest_path.write_bytes(manifest_payload)
        os.chmod(manifest_path, 0o444)
        sidecar = stage / "bundle_manifest.json.sha256"
        sidecar.write_text(
            "%s  bundle_manifest.json\n" % sha_bytes(manifest_payload),
            encoding="ascii",
        )
        os.chmod(sidecar, 0o444)
        os.rename(stage, final)
    except BaseException:
        raise
    freeze(final)
    verify_bundle(final, bundle_id)
    return final


if __name__ == "__main__":
    result = publish()
    print(
        json.dumps(
            {
                "status": "SEALED_LOCAL_ONLY_NOT_LAUNCHED",
                "bundle": str(result),
                "bundle_id": result.name,
                "bundle_manifest_sha256":
                    digest(result / "bundle_manifest.json"),
                "formal_launch_authorized": False,
                "accepted_round_authorized": False,
            },
            sort_keys=True,
        )
    )
