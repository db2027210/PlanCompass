#!/usr/bin/env python3
"""Fail-closed local preflight for one sealed XP v28 bundle/material attempt."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import stat


EXPECTED_BUNDLE_ID = (
    "a0187979839c8fdb9530da525425a031bca9d955234d754cc495dbe0d5b342e9"
)
EXPECTED_MANIFEST_SHA = (
    "faf226fff3c150b097632fac2dd3b96990fca00e1501cb5b46ac3c390a032fb1"
)
EXPECTED_MATERIALIZER_SHA = (
    "3dd9faf5a0720e075dd1f31660dfb1b2f28446beeb208c744bbb5df4f265700d"
)
PRIVATE_FILES = {
    "mysql-client.cnf",
    "hybench_xp_qualification.properties",
    "mysql-root-bootstrap.cnf",
}


class PreflightError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise PreflightError("JSON root is not an object: %s" % path)
    return value


def require(condition: bool, message: str) -> None:
    if not condition:
        raise PreflightError(message)


def verify_bundle(bundle: Path) -> dict:
    bundle = bundle.resolve(strict=True)
    manifest_path = bundle / "bundle_manifest.json"
    manifest = load(manifest_path)
    require(bundle.name == EXPECTED_BUNDLE_ID, "bundle directory identity differs")
    require(manifest.get("bundle_id") == EXPECTED_BUNDLE_ID, "bundle ID differs")
    require(sha256(manifest_path) == EXPECTED_MANIFEST_SHA, "manifest SHA differs")
    require(manifest.get("formal_scope") == "mysql_only", "scope differs")
    require(manifest.get("cross_engine_comparison_prohibited") is True,
            "cross-engine prohibition differs")
    require(manifest.get("postgresql_mutation_prohibited") is True,
            "PostgreSQL mutation gate differs")
    require(manifest.get("formal_launch_authorized") is False,
            "bundle unexpectedly authorizes formal launch")
    require(manifest.get("materials_generator_sha256") ==
            EXPECTED_MATERIALIZER_SHA, "materializer binding differs")
    materializer = (
        bundle / "test_support/mysql_xp_qualification_bootstrap_materials_v5.py"
    )
    require(sha256(materializer) == EXPECTED_MATERIALIZER_SHA,
            "materializer bytes differ")

    expected = manifest.get("files")
    require(isinstance(expected, dict) and len(expected) == 1040,
            "bundle file map/count differs")
    actual = {}
    for path in bundle.rglob("*"):
        relative = path.relative_to(bundle).as_posix()
        mode = path.lstat().st_mode
        require(not stat.S_ISLNK(mode), "symlink in bundle: " + relative)
        require(mode & 0o222 == 0, "writable bundle entry: " + relative)
        require(path.name != "__pycache__" and not path.name.endswith(".pyc"),
                "Python bytecode pollution: " + relative)
        if path.is_file() and relative not in (
            "bundle_manifest.json", "bundle_manifest.json.sha256"
        ):
            actual[relative] = sha256(path)
    require(actual == expected, "bundle exact file/hash closure differs")

    contract = load(bundle / "xp_executable_launch_contract_v3.json")
    envelope = contract.get("database_resource_envelope") or {}
    require(envelope.get("cpu_quota_cores") == 5.3, "CPU quota differs")
    require(envelope.get("cpuset_cpus") == "21-33,57-69", "CPU set differs")
    require(envelope.get("cpuset_mems") == "1", "NUMA mem set differs")
    require(envelope.get("io_read_bps") == 29360128, "read B/s differs")
    require(envelope.get("io_write_bps") == 524288, "write B/s differs")
    return manifest


def verify_materials(materials: Path, bundle: Path) -> tuple[dict, int]:
    materials = materials.resolve(strict=True)
    manifest = load(materials / "materials_manifest.json")
    spec = load(materials / "bootstrap_spec.json")
    plan = load(materials / "command_plan.json")
    install = load(materials / "authorization_install_plan.json")
    for value, label in ((manifest, "materials"), (spec, "spec"),
                         (plan, "plan"), (install, "install")):
        require(value.get("formal_scope") == "mysql_only", label + " scope differs")
        require(value.get("cross_engine_comparison_prohibited") is True,
                label + " cross-engine gate differs")
        require(value.get("postgresql_mutation_prohibited") is True,
                label + " PostgreSQL gate differs")

    require(manifest.get("bundle_id") == EXPECTED_BUNDLE_ID,
            "materials bundle ID differs")
    require(manifest.get("formal_r0_authorized") is False,
            "materials unexpectedly authorize R0")
    require(manifest.get("accepted_round_authorized") is False,
            "materials unexpectedly authorize accepted round")
    private = manifest.get("private_credentials") or {}
    require(set(private.get("files") or []) == PRIVATE_FILES,
            "nested private credential file closure differs")
    require(private.get("directory_mode") == "0700", "private directory mode differs")
    require(private.get("file_mode") == "0600", "private file mode differs")
    require(private.get("password_in_manifest_or_command") is False,
            "password exposure declaration differs")
    files = manifest.get("files") or {}
    for name in PRIVATE_FILES:
        relative = "private_credentials/" + name
        path = materials / relative
        require(files.get(relative) == sha256(path), relative + " hash differs")
        require(stat.S_IMODE(path.stat().st_mode) == 0o600,
                relative + " mode differs")
    token = materials / "private_authority/main_agent_token"
    require(files.get("private_authority/main_agent_token") == sha256(token),
            "main-agent token hash differs")
    require(stat.S_IMODE(token.stat().st_mode) == 0o600, "token mode differs")
    for directory in (materials, materials / "private_credentials",
                      materials / "private_authority"):
        require(stat.S_IMODE(directory.stat().st_mode) == 0o700,
                "private directory mode differs: " + str(directory))

    required_order = [
        "verify_remote_bundle_manifest",
        "assert_fresh_attempt_root_absent",
        "assert_fresh_external_authorization_root_absent",
        "install_external_authorization_materials_0700_0600",
        "physical_copy",
        "verify_sealed_physical_copy_authority",
        "copy_external_authorization_into_attempt_owned_private_materials_0700_0600",
        "jdk_publish",
        "mount_attempt_owned_host_tmpfs",
        "plan_only",
        "bootstrap_with_private_token",
        "adopt_readback_and_real_pid_check",
    ]
    require(plan.get("execution_order") == required_order, "execution order differs")
    require(plan.get("ssh_prohibited") is True, "remote-local execution gate differs")
    require(plan.get("remote_shell_wrapper_prohibited") is True,
            "remote shell wrapper gate differs")
    require(plan.get("systemd_run_prohibited_options") == ["--wait", "--collect"],
            "systemd 219 prohibited-option gate differs")
    commands = plan.get("host_systemd_run_commands_after_separate_authorization") or {}
    require(set(commands) == {"physical_copy", "plan_only", "bootstrap",
                              "adopt_readback"},
            "remote-local systemd command set differs")
    for name, argv in commands.items():
        require(argv[:4] == ["systemd-run", "--quiet", "--service-type=oneshot",
                             "--remain-after-exit"], name + " systemd prefix differs")
        require("--wait" not in argv and "--collect" not in argv,
                name + " uses a prohibited systemd option")
        require("PYTHONDONTWRITEBYTECODE=1" in argv, name + " bytecode env gate differs")
        python_index = argv.index("python3")
        require(argv[python_index + 1] == "-B", name + " Python lacks -B")
        require(argv[python_index - 4:python_index] ==
                ["-c", "21-33,57-69", "env", "PYTHONDONTWRITEBYTECODE=1"],
                name + " CPU/bytecode prefix differs")
    inner_commands = (
        plan.get("commands_to_run_on_node02_after_separate_authorization") or {}
    )
    require(set(inner_commands) == {"physical_copy", "jdk_publish", "plan_only",
                                    "bootstrap", "adopt_readback"},
            "remote-local inner command set differs")
    for name, argv in inner_commands.items():
        require(argv[:5] == ["taskset", "-c", "21-33,57-69", "env",
                             "PYTHONDONTWRITEBYTECODE=1"],
                name + " inner CPU/bytecode prefix differs")
        python_index = argv.index("python3")
        require(argv[python_index + 1] == "-B", name + " inner Python lacks -B")

    gate = (plan.get("publication_validation") or {}).get("formal_gate_binding") or {}
    require(gate.get("database_cpu_quota_cores") == 5.3,
            "materials CPU gate differs")
    require(gate.get("database_io_read_bps") == 29360128,
            "materials read B/s gate differs")
    require(gate.get("database_io_write_bps") == 524288,
            "materials write B/s gate differs")
    require(plan.get("remote_bundle_manifest_sha256_required") ==
            EXPECTED_MANIFEST_SHA, "remote manifest requirement differs")
    require(spec.get("bundle", {}).get("local_manifest_sha256") ==
            EXPECTED_MANIFEST_SHA, "spec manifest binding differs")

    expires_text = install.get("expires_at_utc")
    require(isinstance(expires_text, str), "expiry is absent")
    expires = dt.datetime.fromisoformat(expires_text)
    remaining = int((expires - dt.datetime.now(dt.timezone.utc)).total_seconds())
    require(remaining > 0, "materials expired; generate a new attempt")
    require(install.get("status") == "PLANNED_NOT_INSTALLED",
            "install status differs")
    return plan, remaining


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--materials", required=True, type=Path)
    args = parser.parse_args()
    bundle = args.bundle.resolve(strict=True)
    verify_bundle(bundle)
    plan, remaining = verify_materials(args.materials, bundle)
    print(json.dumps({
        "status": "PASS_LOCAL_ONLY_NOT_LAUNCHED",
        "bundle_id": EXPECTED_BUNDLE_ID,
        "bundle_manifest_sha256": EXPECTED_MANIFEST_SHA,
        "materials_directory": str(args.materials.resolve()),
        "authority_remaining_seconds": remaining,
        "formal_scope": "mysql_only",
        "database_cpu_quota_cores": 5.3,
        "database_io_read_bps": 29360128,
        "database_io_write_bps": 524288,
        "python_no_bytecode": True,
        "remote_local_execution_required": plan["ssh_prohibited"],
        "formal_r0_authorized": False,
        "accepted_round_authorized": False,
        "remote_actions": 0,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PreflightError as exc:
        raise SystemExit("NOT_READY: %s" % exc)
