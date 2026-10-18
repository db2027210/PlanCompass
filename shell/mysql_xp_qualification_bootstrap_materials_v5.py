#!/usr/bin/env python3
"""Create local-only, short-lived bootstrap materials for the XP v8 successor.

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
    ROOT / "artifacts/mysql_xp_formal10_launch_v8/"
    "2f7824bf1b7f234ebd754bf6e3283420c6eaa789618a7a38f6d75472338b2087"
)
EXPECTED_BUNDLE_ID = (
    "2f7824bf1b7f234ebd754bf6e3283420c6eaa789618a7a38f6d75472338b2087"
)
EXPECTED_MANIFEST_SHA256 = (
    "8cb77864c55b5b51d846abc736c5ec872cb0cdcf7883550e86652e5e190c6412"
)
DEFAULT_OUTPUT_ROOT = ROOT / "artifacts/mysql_xp_qualification_bootstrap_v8"
SYSTEMD_HELP_FIXTURE = (
    ROOT / "artifacts/worker_systemd_run_help_20260731/"
    "node02_node04_systemd_run_help.stdout"
)
SYSTEMD_HELP_FIXTURE_SHA256 = (
    "b2d1a41d2a3e6402d3927ed76f7e2e053ba425ccd80d4d6c6bf1b5a25a53a665"
)
REMOTE_RUNTIME_ROOT = (
    "/mnt/mysql_nvme0n1p1/anonymous/resource_adaptive_mysql/hybench_xp/"
    "runtime_algorithm_c_successor_20260729"
)
REMOTE_AUTHORIZATION_ROOT = REMOTE_RUNTIME_ROOT + "/authorizations"
HOST = "node02"
HOST_CONTROL_CPUSET = "21-33,57-69"
TOKEN_RE = re.compile(r"[A-Za-z0-9_.:-]{32,128}$")
PROHIBITED_SYSTEMD_RUN_OPTIONS = ("--wait", "--collect")
REQUIRED_SYSTEMD_RUN_OPTIONS = (
    "--service-type=oneshot", "--remain-after-exit",
)


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


def parse_systemd_run_help_fixture(path=SYSTEMD_HELP_FIXTURE):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise MaterialError("systemd-run help fixture is absent")
    if sha256(path) != SYSTEMD_HELP_FIXTURE_SHA256:
        raise MaterialError("systemd-run help fixture SHA-256 differs")
    text = path.read_text(encoding="utf-8")
    supported = set()
    for line in text.splitlines():
        for match in re.finditer(r"(?<!\\S)--[a-z][a-z-]*(?:=[A-Z\\[\\]@]+)?", line):
            supported.add(match.group(0).split("=", 1)[0])
    required_supported = {
        "--service-type", "--remain-after-exit", "--collect",
    }
    if (
        not required_supported.issubset(supported)
        or "--wait" in supported
        or "systemd-run [OPTIONS...]" not in text
    ):
        raise MaterialError("node02 systemd-run capability closure differs")
    return {
        "schema_version": "node02_systemd_run_help_fixture_v1",
        "host_id": "node02",
        "command": ["systemd-run", "--help"],
        "exit_code": 0,
        "systemd_version": "219",
        "stdout_path": str(path),
        "stdout_sha256": SYSTEMD_HELP_FIXTURE_SHA256,
        "parsed_supported_options": sorted(supported),
        "parsed_unsupported_options": ["--wait"],
        "launch_policy_prohibited_options":
            list(PROHIBITED_SYSTEMD_RUN_OPTIONS),
    }


def systemd_run_argv(unit, inner):
    if re.fullmatch(
        r"ra-mysql-xp-v8-(copy|plan|bootstrap|adopt)-[0-9a-f]{16}\.service",
        unit or "",
    ) is None:
        raise MaterialError("transient unit identity differs")
    argv = [
        "systemd-run",
        "--quiet",
        "--service-type=oneshot",
        "--remain-after-exit",
        "--unit=" + unit,
        "--property=CPUAffinity=" + HOST_CONTROL_CPUSET.replace(",", " "),
        "--",
    ] + list(inner)
    if (
        any(option in argv for option in PROHIBITED_SYSTEMD_RUN_OPTIONS)
        or any(option not in argv for option in REQUIRED_SYSTEMD_RUN_OPTIONS)
    ):
        raise MaterialError("systemd-run compatibility closure differs")
    return argv


def controller_poll_spec(unit):
    if not unit.startswith("ra-mysql-xp-v8-"):
        raise MaterialError("controller poll unit differs")
    return {
        "argv": [
            "systemctl", "show", unit,
            "--property=ActiveState",
            "--property=SubState",
            "--property=ExecMainStatus",
            "--property=Result",
            "--property=MainPID",
            "--property=ControlGroup",
        ],
        "poll_interval_seconds": 1,
        "deadline_seconds": 1800,
        "running_state": {
            "ActiveState": "activating",
            "SubState": "start",
            "MainPID": "positive",
            "ControlGroup": "/system.slice/" + unit,
        },
        "success_state": {
            "ActiveState": "active",
            "SubState": "exited",
            "ExecMainStatus": "0",
            "Result": "success",
            "MainPID": "0",
            "ControlGroup": "/system.slice/" + unit,
        },
        "all_other_states": "terminal_failure_or_fail_closed",
        "timeout_policy": "fail_closed_and_abandon_attempt",
        "reset_failed_prohibited": True,
        "stop_prohibited": True,
        "collect_prohibited": True,
        "unit_retained_for_audit": True,
    }


def parse_systemctl_show(stdout):
    result = {}
    for line in str(stdout).splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            result[key] = value
    return result


def classify_systemctl_show(stdout, unit):
    row = parse_systemctl_show(stdout)
    cgroup = "/system.slice/" + unit
    if (
        row.get("ActiveState") == "activating"
        and row.get("SubState") == "start"
        and int(row.get("MainPID") or 0) > 1
        and row.get("ControlGroup") == cgroup
    ):
        return "running"
    if (
        row.get("ActiveState") == "active"
        and row.get("SubState") == "exited"
        and row.get("ExecMainStatus") == "0"
        and row.get("Result") == "success"
        and row.get("MainPID") == "0"
        and row.get("ControlGroup") == cgroup
    ):
        return "success"
    return "failure"


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


def write_private(path, payload):
    path = Path(path)
    if path.exists() or path.is_symlink():
        raise MaterialError("refusing to overwrite private credential")
    descriptor = os.open(
        str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
    )
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise MaterialError("private credential mode differs")


def private_properties(public_path, user, password):
    lines = []
    replaced = set()
    replacements = {
        "username": user,
        "password": password,
        "username_ap": user,
        "password_ap": password,
    }
    for raw in Path(public_path).read_text(encoding="utf-8").splitlines():
        if "=" not in raw:
            lines.append(raw)
            continue
        key, unused_value = raw.split("=", 1)
        if key in replacements:
            lines.append("%s=%s" % (key, replacements[key]))
            replaced.add(key)
        else:
            lines.append(raw)
    if replaced != set(replacements):
        raise MaterialError("public qualification credential fields differ")
    return ("\n".join(lines) + "\n").encode("utf-8")


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
    runtime_path = bundle / "distributed_runtime_contract.json"
    if (
        runtime_path.is_symlink() or not runtime_path.is_file()
        or files.get(runtime_path.name) != sha256(runtime_path)
    ):
        raise MaterialError("manifest-bound runtime contract differs")
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    if not isinstance(runtime, dict):
        raise MaterialError("runtime contract is not an object")
    private_contract = contract.get("private_credential_successor") or {}
    token_contract = (
        contract.get("private_authority_token_file_successor") or {}
    )
    readiness = (
        contract.get("client_pair_tcp_readiness_successor", {})
        .get("readiness", {})
    )
    transport = runtime.get("database_counter_transport") or {}
    if (
        bundle.name != EXPECTED_BUNDLE_ID
        or sha256(bundle / "bundle_manifest.json")
        != EXPECTED_MANIFEST_SHA256
        or manifest.get("bundle_id") != bundle.name
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
        or manifest.get("supersedes_bundle_id")
        != "a065b78e8c6151772c692f727d079d5904af82b585f4defb178fc7321de61e6b"
        or manifest.get("invalidated_default_graphdriver_bundle_id")
        != "2c57c211adbc896b905f6858a35df73cf37d049dce44d66b4809129b96cce817"
        or manifest.get("invalidated_systemd_wait_materials_bundle_id")
        != "a065b78e8c6151772c692f727d079d5904af82b585f4defb178fc7321de61e6b"
        or manifest.get("invalidated_root_auth_bundle_id")
        != "eb2a8f79447105dd1df5fc1a9061d652abdc5f2db69d0c3f5672876561437e3a"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("postgresql_mutation_prohibited") is not True
        or contract.get("private_credential_successor", {}).get(
            "predecessor_bundle_id"
        )
        != "a065b78e8c6151772c692f727d079d5904af82b585f4defb178fc7321de61e6b"
        or private_contract.get("secret_material", {}).get(
            "password_in_manifest_command_or_log"
        ) is not False
        or token_contract.get("private_token_file")
        != "private_authority/main_agent_token"
        or token_contract.get(
            "token_literal_in_argv_log_or_manifest_prohibited"
        ) is not True
        or readiness.get("query") != "SELECT @@port, CURRENT_USER()"
        or readiness.get("exact_account_required") is not True
        or transport.get("schema_version")
        != "mysql_algorithm_c_database_counter_transport_v3"
        or transport.get("mysql_defaults_file_in_client")
        != "/run/mysql-xp-private/mysql-client.cnf"
        or transport.get("qualification_account_host") != "192.0.2.6"
        or transport.get("docker_host")
        != "unix:///run/ra-docker-mysql-xp-v2.sock"
        or (contract.get("dedicated_docker_successor") or {}).get(
            "data_root_major_minor"
        ) != "259:4"
        or (runtime.get("dedicated_docker_daemon") or {}).get("socket")
        != "/run/ra-docker-mysql-xp-v2.sock"
        or (
            (contract.get("dedicated_docker_successor") or {})
            .get("root_executor", {}).get("kind")
        ) != "pid1_transient_systemd_oneshot_v1"
        or (
            (contract.get("dedicated_docker_successor") or {})
            .get("root_executor", {}).get("service_type")
        ) != "oneshot"
        or (
            (contract.get("dedicated_docker_successor") or {})
            .get("root_executor", {}).get("remain_after_exit")
        ) is not True
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
        "distributed_runtime_contract.json",
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


def create_materials(bundle_path, output_root, valid_minutes=25):
    bundle, manifest, contract = verified_bundle(bundle_path)
    help_fixture = parse_systemd_run_help_fixture()
    if not isinstance(valid_minutes, int) or not 10 <= valid_minutes <= 30:
        raise MaterialError("valid minutes must be within 10..30")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    attempt_id = new_attempt_id(now)
    if TOKEN_RE.fullmatch(attempt_id) is None:
        raise MaterialError("generated attempt identity differs")
    unit_suffix = hashlib.sha256(
        attempt_id.encode("utf-8")
    ).hexdigest()[:16]
    executor_units = {
        action: "ra-mysql-xp-v8-%s-%s.service" % (action, unit_suffix)
        for action in ("copy", "plan", "bootstrap", "adopt")
    }
    output = Path(output_root).resolve() / attempt_id
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() or output.is_symlink():
        raise MaterialError("attempt directory already exists")
    output.mkdir(mode=0o700)
    token = "xp-formal-main-agent-" + secrets.token_urlsafe(36)
    if TOKEN_RE.fullmatch(token) is None:
        raise MaterialError("generated token differs")
    authority_directory = output / "private_authority"
    authority_directory.mkdir(mode=0o700)
    token_path = authority_directory / "main_agent_token"
    descriptor = os.open(str(token_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, (token + "\n").encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    credential_directory = output / "private_credentials"
    credential_directory.mkdir(mode=0o700)
    qualification_user = "ra_xp_qual_" + hashlib.sha256(
        attempt_id.encode("utf-8")
    ).hexdigest()[:20]
    qualification_password = secrets.token_urlsafe(48)
    if (
        len(qualification_password) < 43
        or any(
            character not in
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
            for character in qualification_password
        )
    ):
        raise MaterialError("generated qualification password differs")
    write_private(
        credential_directory / "mysql-client.cnf",
        (
            "[client]\nuser=%s\npassword=%s\n"
            % (qualification_user, qualification_password)
        ).encode("utf-8"),
    )
    write_private(
        credential_directory / "hybench_xp_qualification.properties",
        private_properties(
            bundle / "qualification/config/hybench_xp_qualification.properties",
            qualification_user,
            qualification_password,
        ),
    )
    remote_bundle = REMOTE_RUNTIME_ROOT + "/bundles/" + bundle.name
    remote_attempt = REMOTE_RUNTIME_ROOT + "/" + attempt_id
    remote_authorization = REMOTE_AUTHORIZATION_ROOT + "/" + attempt_id
    created = now.isoformat()
    expires = (now + timedelta(minutes=valid_minutes)).isoformat()
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
            "executor_units": executor_units,
        },
        "created_at_utc": created,
        "expires_at_utc": expires,
        "qualification_only": True,
        "capture_authorized": False,
        "formal_r0_authorized": False,
        "formal_authorized": False,
        "accepted_round_authorized": False,
        "systemd_run_compatibility": {
            "fixture": help_fixture,
            "service_type": "oneshot",
            "remain_after_exit": True,
            "prohibited_options":
                list(PROHIBITED_SYSTEMD_RUN_OPTIONS),
            "controller_poll_required": True,
            "unit_retained_for_audit": True,
        },
        "paths": {
            "attempt_root": remote_attempt,
            "authorization_root": remote_authorization,
            "authorization_private_credentials":
                remote_authorization + "/private_credentials",
            "authorization_private_authority":
                remote_authorization + "/private_authority",
            "authorization_main_agent_token":
                remote_authorization + "/private_authority/main_agent_token",
            "datadir": remote_attempt + "/datadir",
            "host_output_tmpfs": remote_attempt + "/host_measurement_tmpfs",
            "copy_authority": remote_attempt + "/physical_copy_authority.json",
            "jdk_publication": remote_attempt + "/jdk_publication.json",
            "adoption": remote_attempt + "/adopted_service.json",
            "private_credentials":
                remote_attempt + "/private_credentials",
            "private_authority":
                remote_attempt + "/private_authority",
            "main_agent_token":
                remote_attempt + "/private_authority/main_agent_token",
        },
        "private_credentials": {
            "local_directory": str(credential_directory),
            "remote_directory":
                remote_authorization + "/private_credentials",
            "attempt_runtime_directory":
                remote_attempt + "/private_credentials",
            "directory_mode": "0700",
            "file_mode": "0600",
            "files": [
                "mysql-client.cnf",
                "hybench_xp_qualification.properties",
            ],
            "qualification_user": qualification_user,
            "qualification_account_host": "192.0.2.6",
            "password_in_manifest_or_command": False,
            "copy_requires_private_transport": True,
        },
        "private_authority": {
            "local_directory": str(authority_directory),
            "remote_directory": remote_authorization + "/private_authority",
            "attempt_runtime_directory":
                remote_attempt + "/private_authority",
            "directory_mode": "0700",
            "file_mode": "0600",
            "file": "main_agent_token",
            "token_literal_in_manifest_command_or_log": False,
            "copy_requires_private_transport": True,
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
        "formal_gate_binding": {
            "database_cpu_quota_cores": 5.3,
            "database_io_read_bps": 29360128,
            "database_io_write_bps": 524288,
            "capture_only_io_bps_per_direction": 1205862400,
            "capture_is_formal_r0": False,
            "capture_accepted_round_credit": 0,
            "capture_and_formal_envelopes_must_not_be_mixed": True,
            "active_parameter_count": 7,
            "required_nonempty_subsets": 127,
            "fixed_real_replay_top_k": 5,
            "candidate_order": "global_descending_final_candidate_score",
            "required_trace_transactions": [
                "AT1", "AT2", "AT3", "AT4", "AT5", "AT6",
            ],
            "sealed_real_trace_registry_required_before_fresh_r0": True,
            "trace_registry_evidence_in_these_materials": False,
            "missing_trace_registry_policy": "FAIL_CLOSED",
            "promotion_stage_order": [
                "AT1_AT6_real_trace_registry",
                "fresh_R0",
                "127_candidates_fixed_Top5_debug",
                "same_live_formal10_handoff",
            ],
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
        "--credential-dir", spec["paths"]["private_credentials"],
        "--attempt-id", attempt_id,
        "--handoff-token", attempt_id,
        "--main-agent-token-sha256", spec["attempt"]["main_agent_token_sha256"],
        "--authorization-created-at-utc", created,
        "--authorization-expires-at-utc", expires,
    ]
    prefix = ["taskset", "-c", HOST_CONTROL_CPUSET, "env", "PYTHONDONTWRITEBYTECODE=1", "python3", "-B"]
    service = remote_bundle + "/launch/mysql_xp_node02_adopted_service_v1.py"
    def with_unit(action, values):
        return list(values) + [
            "--executor-unit", executor_units[action],
        ]

    def systemd_run(action, inner):
        return systemd_run_argv(executor_units[action], inner)

    def controller_poll(action):
        return controller_poll_spec(executor_units[action])

    physical_copy = with_unit(
        "copy", prefix + [
            service, "--prepare-copy", "--attempt-id", attempt_id,
            "--output", spec["paths"]["copy_authority"],
        ]
    )
    plan_only = with_unit(
        "plan", prefix + [service, "--plan"] + common
    )
    bootstrap = with_unit(
        "bootstrap", prefix + [service, "--bootstrap"] + common + [
            "--main-agent-token-file", spec["paths"]["main_agent_token"],
            "--physical-copy-authority", spec["paths"]["copy_authority"],
            "--output", spec["paths"]["adoption"],
        ]
    )
    adopt_readback = with_unit(
        "adopt", prefix + [service, "--adopt"] + common + [
            "--physical-copy-authority", spec["paths"]["copy_authority"],
            "--output", spec["paths"]["adoption"],
        ]
    )
    plan = {
        "schema_version": "mysql_xp_successor_qualification_command_plan_v2",
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
        "sealed_process_must_be_pid1_transient_systemd_service": True,
        "sealed_process_service_type": "oneshot",
        "sealed_process_remain_after_exit": True,
        "privileged_helper_may_only_invoke_systemd_run": True,
        "nonroot_direct_execution_of_physical_copy_or_bootstrap_prohibited":
            True,
        "main_agent_host_root_executor_required_for": [
            "physical_copy", "bootstrap",
        ],
        "systemd_run_help_fixture": help_fixture,
        "systemd_run_prohibited_options":
            list(PROHIBITED_SYSTEMD_RUN_OPTIONS),
        "systemd_run_returns_before_completion": True,
        "controller_must_poll_each_unit_before_next_step": True,
        "unit_retained_for_audit": True,
        "bootstrap_root_reason":
            "normalized_datadir_is_uid_999_mode_0700_and_pristine_full_tree_readback_is_required",
        "remote_bundle_manifest_sha256_required": spec["bundle"]["remote_manifest_sha256_required"],
        "execution_order": [
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
        ],
        "fresh_attempt_root_gate": {
            "path": spec["paths"]["attempt_root"],
            "must_be_absent_before_physical_copy": True,
            "reuse_after_any_failed_step_prohibited": True,
            "overwrite_prohibited": True,
            "delete_for_retry_prohibited": True,
            "recovery": "generate_new_attempt_materials",
        },
        "external_authorization_install_gate": {
            "destination": spec["paths"]["authorization_root"],
            "outside_attempt_root": True,
            "attempt_root_must_remain_absent_during_install": True,
            "destination_must_be_absent": True,
            "exclusive_create_required": True,
            "directory_mode": "0700",
            "private_subdirectory_mode": "0700",
            "private_file_mode": "0600",
            "transport_must_not_expose_content_in_argv_or_log": True,
            "partial_install_invalidates_attempt": True,
            "not_executable_by_this_local_only_plan": True,
        },
        "private_credential_install_gate": {
            "source": spec["private_credentials"]["local_directory"],
            "destination": spec["private_credentials"]["remote_directory"],
            "attempt_runtime_destination":
                spec["private_credentials"]["attempt_runtime_directory"],
            "directory_mode": "0700",
            "file_mode": "0600",
            "external_authorization_install_precedes_physical_copy": True,
            "copy_into_attempt_requires_verified_physical_copy_authority":
                True,
            "destination_must_be_absent": True,
            "exclusive_create_required": True,
            "partial_install_makes_attempt_permanently_non_reusable": True,
            "transport_must_not_expose_content_in_argv_or_log": True,
            "must_complete_before_plan_or_bootstrap": True,
            "not_executable_by_this_local_only_plan": True,
        },
        "private_authority_install_gate": {
            "source": spec["private_authority"]["local_directory"],
            "destination": spec["private_authority"]["remote_directory"],
            "attempt_runtime_destination":
                spec["private_authority"]["attempt_runtime_directory"],
            "directory_mode": "0700",
            "file_mode": "0600",
            "external_authorization_install_precedes_physical_copy": True,
            "copy_into_attempt_requires_verified_physical_copy_authority":
                True,
            "destination_must_be_absent": True,
            "exclusive_create_required": True,
            "partial_install_makes_attempt_permanently_non_reusable": True,
            "transport_must_not_expose_content_in_argv_or_log": True,
            "must_complete_before_bootstrap": True,
            "not_executable_by_this_local_only_plan": True,
        },
        "commands_to_run_on_node02_after_separate_authorization": {
            "jdk_publish": prefix + [
                remote_bundle + "/runtime/mysql_xp_node02_jdk_publish_v1.py",
                "--bundle", remote_bundle,
                "--output", spec["paths"]["jdk_publication"],
            ],
            "physical_copy": physical_copy,
            "plan_only": plan_only,
            "bootstrap": bootstrap,
            "adopt_readback": adopt_readback,
        },
        "host_systemd_run_commands_after_separate_authorization": {
            "physical_copy": systemd_run("copy", physical_copy),
            "plan_only": systemd_run("plan", plan_only),
            "bootstrap": systemd_run("bootstrap", bootstrap),
            "adopt_readback": systemd_run("adopt", adopt_readback),
        },
        "controller_poll_after_each_systemd_run": {
            "physical_copy": controller_poll("copy"),
            "plan_only": controller_poll("plan"),
            "bootstrap": controller_poll("bootstrap"),
            "adopt_readback": controller_poll("adopt"),
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
            "formal_gate_binding": spec["formal_gate_binding"],
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
    authorization_install = {
        "schema_version": "mysql_xp_external_authorization_install_plan_v1",
        "status": "PLANNED_NOT_INSTALLED",
        "host": HOST,
        "bundle_id": bundle.name,
        "bundle_manifest_sha256":
            sha256(bundle / "bundle_manifest.json"),
        "attempt_id": attempt_id,
        "created_at_utc": created,
        "expires_at_utc": expires,
        "source_materials_directory": str(output),
        "destination_authorization_root": remote_authorization,
        "destination_outside_attempt_root": True,
        "attempt_root": remote_attempt,
        "formal_r0_authorized": False,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "strict_install_steps": [
            {
                "step": 1,
                "action": "assert_absent",
                "paths": [remote_attempt, remote_authorization],
                "failure_policy": "INVALIDATE_ATTEMPT_ZERO_CREDIT",
            },
            {
                "step": 2,
                "action": "exclusive_create_directory",
                "path": remote_authorization,
                "mode": "0700",
                "attempt_root_must_remain_absent": True,
            },
            {
                "step": 3,
                "action": "exclusive_install_private_directories",
                "directories": {
                    "private_authority": {
                        "source_relative": "private_authority",
                        "destination":
                            remote_authorization + "/private_authority",
                        "mode": "0700",
                    },
                    "private_credentials": {
                        "source_relative": "private_credentials",
                        "destination":
                            remote_authorization + "/private_credentials",
                        "mode": "0700",
                    },
                },
                "all_regular_files_mode": "0600",
                "symlink_prohibited": True,
                "secret_content_in_argv_or_log_prohibited": True,
            },
            {
                "step": 4,
                "action": "verify_external_hashes_modes_and_owner",
                "expected_files": {
                    "private_authority/main_agent_token":
                        sha256(token_path),
                    "private_credentials/mysql-client.cnf": sha256(
                        credential_directory / "mysql-client.cnf"
                    ),
                    "private_credentials/hybench_xp_qualification.properties":
                        sha256(
                            credential_directory
                            / "hybench_xp_qualification.properties"
                        ),
                },
                "directory_mode": "0700",
                "file_mode": "0600",
                "single_owner_uid_gid_required": True,
            },
            {
                "step": 5,
                "action":
                    "run_separately_authorized_full_physical_copy_and_verify_authority",
                "attempt_root_created_only_by_physical_copy_stage": True,
                "no_data_plane_action_in_these_local_materials": True,
            },
            {
                "step": 6,
                "action":
                    "exclusive_copy_external_materials_to_attempt_owned_runtime_paths",
                "sources": {
                    "private_authority":
                        remote_authorization + "/private_authority",
                    "private_credentials":
                        remote_authorization + "/private_credentials",
                },
                "destinations": {
                    "private_authority":
                        remote_attempt + "/private_authority",
                    "private_credentials":
                        remote_attempt + "/private_credentials",
                },
                "destination_must_be_absent": True,
                "directory_mode": "0700",
                "file_mode": "0600",
                "hashes_must_equal_step_4": True,
            },
            {
                "step": 7,
                "action": "run_plan_then_bootstrap_before_ttl_expiry",
                "same_attempt_and_bundle_required": True,
                "all_prior_gates_required": True,
            },
        ],
        "partial_or_expired_install_policy":
            "INVALID_ATTEMPT_ZERO_R0_ZERO_ACCEPTED_CREDIT_GENERATE_NEW_MATERIALS",
        "formal_gate_binding": spec["formal_gate_binding"],
    }
    try:
        write_sealed(output / "bootstrap_spec.json", spec)
        write_sealed(output / "command_plan.json", plan)
        write_sealed(
            output / "authorization_install_plan.json",
            authorization_install,
        )
        manifest_value = {
            "schema_version": "mysql_xp_successor_qualification_materials_manifest_v2",
            "status": "SEALED_QUALIFICATION_ONLY_NOT_LAUNCHED",
            "bundle_id": bundle.name,
            "bundle_manifest_sha256": sha256(bundle / "bundle_manifest.json"),
            "attempt_id": attempt_id,
            "created_at_utc": created,
            "expires_at_utc": expires,
            "files": {
                "bootstrap_spec.json": sha256(output / "bootstrap_spec.json"),
                "command_plan.json": sha256(output / "command_plan.json"),
                "authorization_install_plan.json":
                    sha256(output / "authorization_install_plan.json"),
                "private_authority/main_agent_token": sha256(token_path),
                "private_credentials/mysql-client.cnf": sha256(
                    credential_directory / "mysql-client.cnf"
                ),
                "private_credentials/hybench_xp_qualification.properties":
                    sha256(
                        credential_directory
                        / "hybench_xp_qualification.properties"
                    ),
            },
            "private_credentials": {
                "directory": "private_credentials",
                "directory_mode": "0700",
                "file_mode": "0600",
                "files": [
                    "mysql-client.cnf",
                    "hybench_xp_qualification.properties",
                ],
                "password_in_manifest_or_command": False,
            },
            "main_agent_token_mode": "0600",
            "main_agent_token_literal_in_manifest_command_or_log": False,
            "authorization_root": remote_authorization,
            "authorization_outside_attempt": True,
            "attempt_root_must_be_absent_during_authorization_install": True,
            "formal_gate_binding": spec["formal_gate_binding"],
            "systemd_run_help_fixture_sha256":
                help_fixture["stdout_sha256"],
            "systemd_run_policy": {
                "service_type": "oneshot",
                "remain_after_exit": True,
                "prohibited_options":
                    list(PROHIBITED_SYSTEMD_RUN_OPTIONS),
                "controller_poll_required": True,
                "unit_retained_for_audit": True,
            },
            "invalidated_expired_material_attempts": [
                {
                    "attempt_id":
                        "xp-formal-qual-20260731T062116Z-3cc47550dbda18501b6e9b3b",
                    "bundle_id":
                        "8a6f71c3a10d06d3fcc0dc6a0fc1ee32c57d9661e9c775b0efdc1a5984bf1762",
                    "expires_at_utc": "2026-07-31T06:46:16+00:00",
                    "credit": 0,
                },
                {
                    "attempt_id":
                        "xp-formal-qual-20260731T062327Z-a922d5c60c7d15079880a14d",
                    "bundle_id": EXPECTED_BUNDLE_ID,
                    "expires_at_utc": "2026-07-31T06:48:27+00:00",
                    "credit": 0,
                },
            ],
            "invalidated_predecessor_material_credit": 0,
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
        "authorization_install_plan":
            str(output / "authorization_install_plan.json"),
        "authorization_root": remote_authorization,
        "main_agent_token_file": str(token_path),
        "expires_at_utc": expires,
        "formal_r0_authorized": False,
        "accepted_round_authorized": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--valid-minutes", type=int, default=25)
    args = parser.parse_args()
    print(json.dumps(
        create_materials(args.bundle, args.output_root, args.valid_minutes),
        sort_keys=True,
    ))


if __name__ == "__main__":
    try:
        main()
    except MaterialError as exc:
        raise SystemExit("NOT_READY: %s" % exc)
