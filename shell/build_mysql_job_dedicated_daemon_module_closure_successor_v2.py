#!/usr/bin/env python3
"""Seal the JOB dedicated-daemon Python module-closure successor offline."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import stat
import sys


ROOT = Path(__file__).resolve().parents[1]
PARENT_ID = (
    "8cc69a3cc762fe26b0ba4c61254c7d9a7b1802f5d7cec5c4cab9b7ea81289678"
)
PARENT_MANIFEST_SHA256 = (
    "26a5330eed6dda012ec438c56a15aad42dc0710420a394e541c6f93d9ff7e78e"
)
PARENT = (
    ROOT / "artifacts/mysql_job_dedicated_daemon_execution_successor_v1"
    / PARENT_ID
)
OUTPUT = (
    ROOT
    / "artifacts/mysql_job_dedicated_daemon_module_closure_successor_v2"
)
AUTHORITY = "qualification/mysql_job_distributed_v2_authority_tmpfs_v2.py"
RENDERER = "controller/render_mysql_job_v32_bootstrap_spec_tmpfs_v2.py"
MODULE = "qualification/mysql_job_dedicated_daemon_contract_v1.py"
FIXTURE = (
    "qualification/fixtures/"
    "mysql_job_dedicated_daemon_readback_fixture_v1.json"
)
MATERIALIZER = (
    "qualification/mysql_job_dedicated_daemon_materializer_v3.py"
)
INVALIDATION = (
    "authority/mysql_job_8cc_bootstrap_attempts_invalidation_20260731.json"
)
TEST = "tests/test_mysql_job_module_closure_successor_v2.py"


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
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def replace_once(source, old, new, label):
    if source.count(old) != 1:
        raise BuildError(
            "%s patch cardinality differs: %d"
            % (label, source.count(old))
        )
    return source.replace(old, new)


def verify_parent():
    manifest_path = PARENT / "bundle_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        PARENT.name != manifest.get("bundle_id")
        or sha256(manifest_path) != PARENT_MANIFEST_SHA256
        or not isinstance(manifest.get("files"), dict)
    ):
        raise BuildError("8cc parent identity differs")
    for relative, expected in manifest["files"].items():
        path = PARENT / relative
        if path.is_symlink() or not path.is_file() or sha256(path) != expected:
            raise BuildError("8cc manifest closure differs: " + relative)
    return manifest


def patch_renderer(source):
    source = replace_once(
        source,
        '    attempt_root = RUNTIME_ROOT + "/attempts/" + attempt\n',
        '    attempt_root = RUNTIME_ROOT + "/attempts/" + attempt\n'
        '    authorization_root = '
        'RUNTIME_ROOT + "/authorizations/" + attempt\n',
        "renderer authorization root",
    )
    source = replace_once(
        source,
        '"python_runtime_root": python_root},',
        '"python_runtime_root": python_root, '
        '"authorization_root": authorization_root, '
        '"main_agent_token_file": '
        'authorization_root + "/main_agent_token"},',
        "renderer authorization paths",
    )
    return source


def patch_authority(source):
    source = replace_once(
        source,
        '            "python_runtime_root",\n',
        '            "python_runtime_root",\n'
        '            "authorization_root",\n'
        '            "main_agent_token_file",\n',
        "authority path keys",
    )
    source = replace_once(
        source,
        '    expected_attempt_root = RUNTIME_ROOT + "/attempts/" + attempt\n',
        '    expected_attempt_root = RUNTIME_ROOT + "/attempts/" + attempt\n'
        '    expected_authorization_root = '
        'RUNTIME_ROOT + "/authorizations/" + attempt\n',
        "authority expected authorization root",
    )
    source = replace_once(
        source,
        '        or paths["credentials_dir"] != expected_attempt_root + "/credentials"\n',
        '        or paths["credentials_dir"] '
        '!= expected_attempt_root + "/credentials"\n'
        '        or paths["authorization_root"] '
        '!= expected_authorization_root\n'
        '        or paths["main_agent_token_file"] '
        '!= expected_authorization_root + "/main_agent_token"\n',
        "authority authorization path gate",
    )
    loader = '''

def _dedicated_contract_validate(info, daemon, containers, mount_devices):
    """Load the manifest-sealed sibling validator and fail as AuthorityError."""
    try:
        from mysql_job_dedicated_daemon_contract_v1 import (
            Error as DedicatedDaemonContractError,
            validate as dedicated_validate,
        )
    except ModuleNotFoundError as exc:
        raise AuthorityError(
            "sealed dedicated daemon contract module is unavailable: %s" % exc
        )
    try:
        return dedicated_validate(info, daemon, containers, mount_devices)
    except DedicatedDaemonContractError as exc:
        raise AuthorityError(
            "dedicated daemon contract readback failed: %s" % exc
        )


def verify_dedicated_daemon_readback_fixture(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise AuthorityError("dedicated daemon readback fixture is absent")
    try:
        fixture = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AuthorityError(
            "dedicated daemon readback fixture is invalid: %s" % exc
        )
    _require_exact_keys(
        fixture,
        {"schema_version", "info", "daemon", "containers", "mount_devices"},
        "dedicated daemon readback fixture",
    )
    if fixture["schema_version"] != (
        "mysql_job_dedicated_daemon_readback_fixture_v1"
    ):
        raise AuthorityError("dedicated daemon fixture schema differs")
    return _dedicated_contract_validate(
        fixture["info"],
        fixture["daemon"],
        fixture["containers"],
        fixture["mount_devices"],
    )
'''
    source = replace_once(
        source,
        "\ndef _dedicated_daemon_readback(database_inspect, client_inspect):\n",
        loader
        + "\n\ndef _dedicated_daemon_readback(database_inspect, client_inspect):\n",
        "sealed module loader",
    )
    source = replace_once(
        source,
        "    from mysql_job_dedicated_daemon_contract_v1 import validate\n",
        "",
        "unsafe runtime import",
    )
    source = replace_once(
        source,
        "    return validate(info, daemon, containers, mount_devices)\n",
        "    return _dedicated_contract_validate(\n"
        "        info, daemon, containers, mount_devices\n"
        "    )\n",
        "dedicated contract call",
    )
    source = replace_once(
        source,
        '    parser.add_argument("--spec", type=Path, required=True)\n',
        '    parser.add_argument("--spec", type=Path)\n',
        "optional spec for readback fixture mode",
    )
    source = replace_once(
        source,
        '    modes.add_argument("--verify-bootstrap", type=Path)\n',
        '    modes.add_argument("--verify-bootstrap", type=Path)\n'
        '    modes.add_argument(\n'
        '        "--verify-dedicated-daemon-readback-fixture", type=Path\n'
        '    )\n',
        "fixture CLI mode",
    )
    source = replace_once(
        source,
        '    parser.add_argument("--main-agent-token-file", type=Path)\n',
        '    parser.add_argument("--main-agent-token-file", type=Path)\n'
        '    parser.add_argument("--failure-output", type=Path)\n',
        "failure output CLI",
    )
    source = replace_once(
        source,
        '    args = parser.parse_args()\n'
        '    spec = load_with_sidecar(args.spec, "bootstrap spec")\n',
        '    args = parser.parse_args()\n'
        '    if args.verify_dedicated_daemon_readback_fixture is not None:\n'
        '        try:\n'
        '            value = verify_dedicated_daemon_readback_fixture(\n'
        '                args.verify_dedicated_daemon_readback_fixture\n'
        '            )\n'
        '        except AuthorityError as exc:\n'
        '            if args.failure_output is not None:\n'
        '                atomic_json(\n'
        '                    args.failure_output,\n'
        '                    {\n'
        '                        "schema_version":\n'
        '                            "mysql_job_readback_fixture_failure_v1",\n'
        '                        "status":\n'
        '                            "FAIL_CLOSED_MODULE_OR_READBACK",\n'
        '                        "stage":\n'
        '                            "dedicated_daemon_readback_fixture",\n'
        '                        "failure_type": type(exc).__name__,\n'
        '                        "failure": str(exc),\n'
        '                        "qualification_credit": 0,\n'
        '                        "r0_credit": 0,\n'
        '                        "accepted_rounds_credit": 0,\n'
        '                        "formal_launch_authorized": False,\n'
        '                        "accepted_round_authorized": False,\n'
        '                    },\n'
        '                )\n'
        '            raise\n'
        '        print(json.dumps(value, indent=2, sort_keys=True))\n'
        '        return 0\n'
        '    if args.spec is None:\n'
        '        raise AuthorityError("--spec is required for this mode")\n'
        '    spec = load_with_sidecar(args.spec, "bootstrap spec")\n',
        "fixture execution before spec",
    )
    old_execute = '''        value = execute_bootstrap(spec, args.output, args.main_agent_token_file)
    elif args.verify_bootstrap is not None:
'''
    new_execute = '''        try:
            value = execute_bootstrap(
                spec, args.output, args.main_agent_token_file
            )
        except AuthorityError as exc:
            if args.failure_output is not None:
                atomic_json(
                    args.failure_output,
                    {
                        "schema_version":
                            "mysql_job_bootstrap_failure_evidence_v2",
                        "status": "FAIL_CLOSED_BOOTSTRAP_AUTHORITY",
                        "stage": "execute_bootstrap",
                        "failure_type": type(exc).__name__,
                        "failure": str(exc),
                        "attempt_token": spec["attempt_token"],
                        "bundle_id": spec["bundle"]["bundle_id"],
                        "spec_sha256": sha_bytes(canonical(spec)),
                        "database_container":
                            spec["containers"]["database"],
                        "client_container": spec["containers"]["client"],
                        "qualification_credit": 0,
                        "r0_credit": 0,
                        "accepted_rounds_credit": 0,
                        "formal_r0_authorized": False,
                        "formal_launch_authorized": False,
                        "accepted_round_authorized": False,
                    },
                )
            raise
    elif args.verify_dedicated_daemon_readback_fixture is not None:
        value = verify_dedicated_daemon_readback_fixture(
            args.verify_dedicated_daemon_readback_fixture
        )
    elif args.verify_bootstrap is not None:
'''
    source = replace_once(
        source, old_execute, new_execute, "fail-closed bootstrap evidence"
    )
    authorization_plan = '''        "authorization": {
            "root": paths["authorization_root"],
            "root_outside_attempt": True,
            "attempt_root_must_be_absent_before_install": True,
            "bootstrap_spec":
                paths["authorization_root"] + "/bootstrap_spec.json",
            "command_plan":
                paths["authorization_root"] + "/command_plan.json",
            "main_agent_token": paths["main_agent_token_file"],
            "directory_mode": "0700",
            "public_file_mode": "0400",
            "private_token_mode": "0600",
            "exclusive_install_required": True,
        },
        "execute_bootstrap": {
            "argv": [
                spec["python_runtime"]["executable_path"],
                bundle
                + "/qualification/"
                + "mysql_job_distributed_v2_authority_tmpfs_v2.py",
                "--spec",
                paths["authorization_root"] + "/bootstrap_spec.json",
                "--execute-bootstrap",
                "--output",
                paths["attempt_root"] + "/bootstrap_evidence.json",
                "--main-agent-token-file",
                paths["main_agent_token_file"],
                "--failure-output",
                paths["attempt_root"] + "/bootstrap_failure_evidence.json",
            ],
        },
'''
    source = replace_once(
        source,
        '        "collect_only": {"argv": collect_argv},\n'
        '        "preflight": {\n',
        '        "collect_only": {"argv": collect_argv},\n'
        + authorization_plan
        + '        "preflight": {\n',
        "authorization command plan",
    )
    source = replace_once(
        source,
        "    token_path = Path(main_agent_token_file)\n",
        "    token_path = Path(main_agent_token_file)\n"
        "    if str(token_path) != spec[\"paths\"][\"main_agent_token_file\"]:\n"
        "        raise AuthorityError(\n"
        "            \"main-agent token is outside sealed authorization path\"\n"
        "        )\n",
        "token path binding",
    )
    return source


def readback_fixture():
    root = (
        "/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/"
        "docker_ra_job_v1"
    )
    paths = {
        "UpperDir": root + "/overlay2/db/diff",
        "WorkDir": root + "/overlay2/db/work",
        "MergedDir": root + "/overlay2/db/merged",
        "LowerDir": root + "/overlay2/db/lower",
    }
    client_paths = {
        key: value.replace("/db/", "/client/")
        for key, value in paths.items()
    }
    row = {
        "network_mode": "host",
        "daemon_socket": "/run/ra-docker-mysql-job-v1.sock",
        "mysql_server_port": 65102,
        "container_id": "a" * 64,
        "pid": 42001,
        "startticks": 100001,
        "graphdriver_data": paths,
    }
    client = dict(row)
    client.update(
        {
            "container_id": "b" * 64,
            "pid": 42002,
            "startticks": 100002,
            "graphdriver_data": client_paths,
        }
    )
    mount_devices = {
        value: "259:3"
        for value in list(paths.values()) + list(client_paths.values())
    }
    return {
        "schema_version":
            "mysql_job_dedicated_daemon_readback_fixture_v1",
        "info": {"DockerRootDir": root, "Driver": "overlay2"},
        "daemon": {
            "unit": "ra-docker-mysql-job-v1.service",
            "socket": "/run/ra-docker-mysql-job-v1.sock",
            "cpuset_cpus": "34-35,70-71",
            "data_root_device": "259:3",
            "pid": 41001,
            "startticks": 90001,
            "cmdline_sha256": "c" * 64,
            "ppid": 1,
            "cgroup":
                "1:name=systemd:/system.slice/"
                "ra-docker-mysql-job-v1.service\n",
            "namespaces": {
                "mnt": "mnt:[1]",
                "pid": "pid:[2]",
                "net": "net:[3]",
                "user": "user:[4]",
            },
            "docker_ancestry": False,
        },
        "containers": {"database": row, "client": client},
        "mount_devices": mount_devices,
    }


def invalidation():
    return {
        "schema_version":
            "mysql_job_8cc_bootstrap_attempts_invalidation_v1",
        "status": "INVALIDATED_DO_NOT_RESUME_RERUN_OR_CREDIT",
        "bundle_id": PARENT_ID,
        "bundle_manifest_sha256": PARENT_MANIFEST_SHA256,
        "attempts": [
            {
                "attempt_token":
                    "job-v2-20260731t061719z-3c69f2da1d4781cafa9e5b39",
                "failure":
                    "materials installed inside attempt; absent-path gate failed",
                "physical_copy_completed": False,
                "database_or_client_started": False,
            },
            {
                "attempt_token":
                    "job-v2-20260731t063314z-6a38fa4a52b89030e7d6e5e4",
                "failure":
                    "sealed authority could not import dedicated daemon contract",
                "physical_copy_completed": True,
                "physical_copy_bytes": "9.8GiB",
                "database_and_client_started": True,
                "containers_stopped_exactly": True,
                "journal_retained": True,
                "deletion_performed": False,
            },
        ],
        "qualification_credit": 0,
        "r0_credit": 0,
        "accepted_rounds_credit": 0,
        "formal_r0_authorized": False,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "rerun_authorized": False,
    }


def freeze(root):
    for path in sorted(Path(root).rglob("*"), reverse=True):
        if path.is_symlink():
            raise BuildError("symlink prohibited in successor")
        os.chmod(path, stat.S_IMODE(path.stat().st_mode) & ~0o222)
    os.chmod(root, stat.S_IMODE(root.stat().st_mode) & ~0o222)


def verify_bundle(path, expected_id=None):
    path = Path(path)
    manifest_path = path / "bundle_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    identity = dict(manifest)
    bundle_id = identity.pop("bundle_id", None)
    actual = {}
    for file_path in sorted(path.rglob("*")):
        if file_path.is_symlink():
            raise BuildError("bundle symlink prohibited")
        if file_path.is_file():
            relative = file_path.relative_to(path).as_posix()
            if relative not in (
                "bundle_manifest.json",
                "bundle_manifest.json.sha256",
            ):
                actual[relative] = sha256(file_path)
    if (
        bundle_id != (expected_id or path.name)
        or sha_bytes(canonical(identity)) != bundle_id
        or actual != manifest.get("files")
        or manifest.get("schema_version")
        != "mysql_job_dedicated_daemon_module_closure_successor_bundle_v2"
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
    ):
        raise BuildError("successor bundle identity differs")
    return manifest


def publish():
    parent_manifest = verify_parent()
    payload = {}
    for relative in parent_manifest["files"]:
        if relative == "closure.json":
            continue
        payload[relative] = (PARENT / relative).read_bytes()
    payload[AUTHORITY] = patch_authority(
        payload[AUTHORITY].decode("utf-8")
    ).encode("utf-8")
    payload[RENDERER] = patch_renderer(
        payload[RENDERER].decode("utf-8")
    ).encode("utf-8")
    module_payload = (
        ROOT / "shell/mysql_job_dedicated_daemon_contract_v1.py"
    ).read_bytes()
    payload[MODULE] = module_payload
    payload[FIXTURE] = canonical(readback_fixture())
    payload[MATERIALIZER] = (
        ROOT / "shell/mysql_job_dedicated_daemon_materializer_v3.py"
    ).read_bytes()
    payload[INVALIDATION] = canonical(invalidation())
    payload[TEST] = (
        ROOT / "tests/test_mysql_job_module_closure_successor_v2.py"
    ).read_bytes()
    closure = json.loads((PARENT / "closure.json").read_text(encoding="utf-8"))
    closure["schema_version"] = (
        "mysql_job_module_closure_v2"
    )
    closure["dedicated_daemon_python_module_closure"] = {
        "runtime_script": AUTHORITY,
        "sibling_module": MODULE,
        "module_sha256": sha_bytes(module_payload),
        "module_manifest_bound": True,
        "module_not_found_converted_to_authority_error": True,
        "failure_evidence_required": True,
        "remote_isomorphic_frozen_python_e2e_required": True,
    }
    closure["authorization_install"] = {
        "root": (
            "/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/job/"
            "runtime_algorithm_c_successor_20260729/authorizations/"
            "<attempt>"
        ),
        "outside_attempt_root": True,
        "attempt_root_absent_gate": True,
    }
    closure["files"] = {
        name: sha_bytes(value)
        for name, value in sorted(payload.items())
    }
    payload["closure.json"] = canonical(closure)
    files = {
        name: sha_bytes(value)
        for name, value in sorted(payload.items())
    }
    identity = {
        key: value
        for key, value in parent_manifest.items()
        if key not in ("bundle_id", "files")
    }
    identity.update(
        {
            "schema_version":
                "mysql_job_dedicated_daemon_module_closure_successor_bundle_v2",
            "predecessor_bundle_id": PARENT_ID,
            "predecessor_manifest_sha256": PARENT_MANIFEST_SHA256,
            "dedicated_daemon_module_relative": MODULE,
            "dedicated_daemon_module_sha256": sha_bytes(module_payload),
            "dedicated_daemon_module_same_directory_as_runtime": True,
            "module_not_found_fail_closed": True,
            "bootstrap_failure_evidence_required": True,
            "authorization_install_outside_attempt_required": True,
            "attempt_root_absent_before_authorization_install_required": True,
            "invalidated_8cc_attempt_count": 2,
            "invalidated_8cc_attempt_credit": 0,
            "invalidated_8cc_attempts_sha256":
                sha_bytes(payload[INVALIDATION]),
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
        verify_bundle(final, bundle_id)
        return final
    stage = OUTPUT / (".stage-" + bundle_id + "-" + secrets.token_hex(6))
    stage.mkdir(mode=0o700)
    try:
        for relative, value in payload.items():
            path = stage / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(value)
            os.chmod(path, 0o755 if relative.endswith(".py") else 0o444)
        manifest_payload = canonical(manifest)
        (stage / "bundle_manifest.json").write_bytes(manifest_payload)
        (stage / "bundle_manifest.json.sha256").write_text(
            sha_bytes(manifest_payload) + "  bundle_manifest.json\n",
            encoding="ascii",
        )
        freeze(stage)
        verify_bundle(stage, bundle_id)
        os.rename(stage, final)
    except BaseException:
        if stage.exists():
            for directory in [stage] + [
                value for value in stage.rglob("*") if value.is_dir()
            ]:
                os.chmod(directory, 0o700)
            shutil.rmtree(stage)
        raise
    return final


def main():
    bundle = publish()
    print(
        json.dumps(
            {
                "status":
                    "PASS_SEALED_JOB_MODULE_CLOSURE_SUCCESSOR",
                "bundle": str(bundle),
                "bundle_id": bundle.name,
                "manifest_sha256": sha256(
                    bundle / "bundle_manifest.json"
                ),
                "formal_launch_authorized": False,
                "accepted_round_authorized": False,
                "remote_actions": 0,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except (BuildError, OSError, ValueError) as exc:
        sys.stderr.write("NOT_READY: %s\n" % exc)
        raise SystemExit(2)
