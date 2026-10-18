#!/usr/bin/env python3
"""Bind one byte-identical qualification core to TPC-H or TPC-C on node04.

The core file is identical for all four A-solo/A-paired/B-solo/B-paired
evidence flows.  This entrypoint changes no data-plane state.  It selects a
dedicated Docker socket only after verifying the target bundle's immutable
content address, mysql_only scope, scenario, device, port, and daemon
contract.  PostgreSQL mutation is not implemented.
"""

import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time


HERE = Path(__file__).resolve().parent
CORE_PATH = HERE / "mysql_distributed_live_readback_qualification_v1.py"
TPCC_TRANSPORT_PATH = HERE / "mysql_shared_tpcc_counter_transport_v1.py"
ROOT_HELPER = "mysql_shared_docker_mount_inventory_v1.py"
ROOT_HELPER_MAX_STDOUT_BYTES = 4 * 1024 * 1024
CURRENT_TARGETS = {
    "tpch_memory": {
        "bundle_id": "ff313374f846ac726244955c2fb27bed8266b93dd37df9624e5c8d4a56653da4",
        "manifest_sha256": "af1d0facca8b33e2638c0a2af6a751bececc407d483055ca064a34308d27bb52",
    },
    "tpcc_cpu": {
        "bundle_id": "7e2e13aa76c0d2e2990ff098dcdcee9b09fdbb98a60abbf4fc08d52ea4fd6c77",
        "manifest_sha256": "e9d5e2213a14d2289c07063c0b24dfb68d6059f068c5c91388862f8a873b4cba",
    },
}
CURRENT_TPCH_ATTEMPT = "tpch-v2-20260803t022500z-afbe6badff313374deadbeef"
CURRENT_TPCH_DB_NAME = "ra_mysql_tpch_v2_tpch_v2_20260803t022500z_afbe6badff313374deadbee_db"
CURRENT_TPCH_CLIENT_NAME = "ra_mysql_tpch_v2_tpch_v2_20260803t022500z_afbe6badff313374deadbee_client"

PROFILES = {
    "tpch_memory": {
        "manifest_schema": "mysql_tpch_v23_recalibrated_successor_v3",
        "socket": "/run/ra-docker-mysql-tpch-v1.sock",
        "socket_uri": "unix:///run/ra-docker-mysql-tpch-v1.sock",
        "unit": "ra-docker-mysql-tpch-v1.service",
        "unit_fragment": "/run/systemd/system/ra-docker-mysql-tpch-v1.service",
        "data_root": "/mnt/nvme0n1/anonymous/resource_adaptive_mysql/docker_ra_tpch_v1",
        "exec_root": "/run/ra-docker-mysql-tpch-v1",
        "pidfile": "/run/ra-docker-mysql-tpch-v1.pid",
        "device": "/dev/nvme0n1",
        "device_major_minor": "259:1",
        "mount": "/mnt/nvme0n1",
        "mysql_port": 65103,
        "declared_daemon_cpuset": "34-35,70-71",
        "daemon_cpuset": "32-33,68-69",
    },
    "tpcc_cpu": {
        "manifest_schema": "mysql_tpcc_prefault_successor_bundle_v16",
        "socket": "/run/ra-docker-mysql-tpcc-v1.sock",
        "socket_uri": "unix:///run/ra-docker-mysql-tpcc-v1.sock",
        "unit": "ra-docker-mysql-tpcc-v1.service",
        "unit_fragment": "/run/systemd/system/ra-docker-mysql-tpcc-v1.service",
        "data_root": "/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/docker_ra_tpcc_v1",
        "exec_root": "/run/ra-docker-mysql-tpcc-v1",
        "pidfile": "/run/ra-docker-mysql-tpcc-v1.pid",
        "device": "/dev/nvme3n1",
        "device_major_minor": "259:2",
        "mount": "/mnt/mysql_tpcc_nvme3n1",
        "mysql_port": 65106,
        "declared_daemon_cpuset": "34-35,70-71",
        "daemon_cpuset": "34-35,70-71",
    },
}


def load_module(name, path):
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("sealed module is absent: %s" % path.name)
    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError("sealed module cannot be loaded: %s" % path.name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


core = load_module("mysql_shared_qualification_core_v1", CORE_PATH)
_original_tpch_prepare_transport = core.prepare_database_counter_transport
_original_tpch_db_counter_sample = core.db_counter_sample
_original_tpch_validate_client = core.validate_client_container_readback
_original_docker_container_inventory = core._docker_container_inventory
_original_rolling_process_inventory = core.rolling_process_inventory
_original_validate_command_spec = core.validate_command_spec
_original_validate_main_agent_authority = core.validate_main_agent_authority
_active_profile = None


def validate_tpch_client_container_readback(
    container_row,
    expected,
    binding,
    tmpfs_destination,
    host_tmpfs_directory=None,
    target_major_minor=None,
):
    """Preserve legacy four-argument TPC-H calls after the six-arg hardening.

    The two added arguments are consumed only by TPC-C tmpfs/volume checks.
    TPC-H requires host_tmpfs_bind, whose full outer readback still supplies
    the concrete paths while the counter bootstrap and rolling identity check
    intentionally use the legacy four-argument form.
    """
    if expected.get("measurement_output_medium") != "host_tmpfs_bind":
        raise core.QualificationError(
            "TPC-H client readback wrapper received non-TPC-H envelope"
        )
    return _original_tpch_validate_client(
        container_row,
        expected,
        binding,
        tmpfs_destination,
        host_tmpfs_directory,
        target_major_minor,
    )


def _profile_for_scenario(scenario):
    profile = PROFILES.get(scenario)
    if profile is None:
        raise core.QualificationError("scenario is outside shared collector scope")
    return profile


def reject_static_pid_authority(value, where="authority"):
    """Reject caller-supplied process IDs; live /proc IDs remain observations."""
    if isinstance(value, dict):
        for key, child in value.items():
            low = str(key).lower()
            if low in {"pid", "process_id", "database_pid", "client_pid", "mysqld_pid"} or low.endswith("_pid"):
                raise core.QualificationError("static PID authority differs: " + where)
            reject_static_pid_authority(child, where + "." + str(key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            if isinstance(child, str) and (child == "--pid" or child.endswith("-pid")):
                raise core.QualificationError("static PID option differs: " + where)
            reject_static_pid_authority(child, "%s[%d]" % (where, index))


def validate_command_spec_without_static_pid(*args, **kwargs):
    value = _original_validate_command_spec(*args, **kwargs)
    reject_static_pid_authority(value, "qualification command")
    bundle_id = args[1] if len(args) > 1 else kwargs.get("bundle_id")
    if bundle_id == CURRENT_TARGETS["tpch_memory"]["bundle_id"]:
        verify_current_tpch_command_binding(value)
    return value


def verify_current_tpch_command_binding(value):
    client = value.get("client_container") or {}
    measurement = value.get("measurement_output") or {}
    expected_measurement = Path("/dev/shm/ra_mysql_tpch_v2") / CURRENT_TPCH_ATTEMPT
    if (
        client.get("name") != CURRENT_TPCH_CLIENT_NAME
        or Path(str(measurement.get("host_tmpfs_directory") or "")) != expected_measurement
        or not re.fullmatch(r"[0-9a-f]{64}", str(client.get("container_id") or ""))
    ):
        raise core.QualificationError("current TPC-H command attempt binding differs")


def validate_main_agent_authority_without_static_pid(*args, **kwargs):
    value = _original_validate_main_agent_authority(*args, **kwargs)
    reject_static_pid_authority(value, "main-agent authority")
    return value


def verify_current_target_seal(bundle, expected_scenario=None):
    """Bind exactly one current target without trusting a process ID."""
    bundle = Path(bundle).resolve(strict=True)
    manifest_path = bundle / "bundle_manifest.json"
    manifest = core.load_object(manifest_path, "current target manifest")
    runtime = core.load_object(bundle / "distributed_runtime_contract.json", "current target runtime")
    scenario = runtime.get("scenario")
    target = CURRENT_TARGETS.get(scenario)
    unsigned = dict(manifest)
    bundle_id = unsigned.pop("bundle_id", None)
    if (
        target is None
        or (expected_scenario is not None and scenario != expected_scenario)
        or bundle.name != target["bundle_id"]
        or bundle_id != bundle.name
        or core.sha_bytes(core.canonical(unsigned)) != bundle_id
        or not core.valid_sidecar(manifest_path)
        or core.sha256(manifest_path) != target["manifest_sha256"]
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
        or runtime.get("formal_scope") != "mysql_only"
        or runtime.get("postgresql_mutation_prohibited") is not True
        or (
            scenario == "tpch_memory"
            and (
                manifest.get("schema_version") != "mysql_tpch_v23_recalibrated_successor_v3"
                or manifest.get("successor_generation") != "sql_native_completed_latency_contract_v1"
                or (manifest.get("component_hashes") or {}).get("collector")
                != "89713310afbd157963bd9efb101cc495014e2ae5722004db3e51664b3dd9d2de"
            )
        )
    ):
        raise core.QualificationError("current target seal differs")
    return manifest, runtime


def _activate_profile(profile):
    global _active_profile
    _active_profile = profile
    core.DOCKER_SOCKET = profile["socket"]
    core.DOCKER_SOCKET_URI = profile["socket_uri"]
    core.DOCKER_CLI = ("docker", "--host", profile["socket_uri"])
    core.DOCKER_DATA_ROOT = profile["data_root"]
    core.DOCKER_UNIT = profile["unit"]
    core.DOCKER_UNIT_FRAGMENT = profile["unit_fragment"]
    core.DOCKER_GRAPH_DRIVER = "overlay2"
    core.DOCKER_ROOT_MAJOR_MINOR = profile["device_major_minor"]


def _verify_target_bundle(bundle):
    manifest, sealed_runtime = verify_current_target_seal(bundle)
    identity = dict(manifest)
    identity.pop("bundle_id", None)
    if (
        manifest.get("bundle_id") != bundle.name
        or core.sha_bytes(core.canonical(identity)) != bundle.name
        or not core.valid_sidecar(bundle / "bundle_manifest.json")
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("formal_launch_authorized") is not False
    ):
        raise core.QualificationError("target bundle identity/scope differs")
    for relative, expected in (manifest.get("files") or {}).items():
        path = bundle / relative
        if path.is_symlink() or not path.is_file() or core.sha256(path) != expected:
            raise core.QualificationError("target bundle closure differs: %s" % relative)
    runtime = sealed_runtime
    scenario = runtime.get("scenario")
    profile = _profile_for_scenario(scenario)
    target = CURRENT_TARGETS[scenario]
    storage = runtime.get("storage") or {}
    envelope = runtime.get("resource_envelope") or {}
    if (
        bundle.name != target["bundle_id"]
        or core.sha256(bundle / "bundle_manifest.json") != target["manifest_sha256"]
        or manifest.get("schema_version") != profile["manifest_schema"]
        or runtime.get("data_plane_launch_authority") != "main_agent_only"
        or runtime.get("formal_scope") != "mysql_only"
        or runtime.get("cross_engine_comparison_prohibited") is not True
        or runtime.get("postgresql_mutation_prohibited") is not True
        or runtime.get("formal_launch_authorized") is not False
        or runtime.get("accepted_round_authorized") is not False
        or storage.get("device") != profile["device"]
        or storage.get("expected_live_major_minor") != profile["device_major_minor"]
        or storage.get("mount") != profile["mount"]
        or int(envelope.get("mysql_port") or 0) != profile["mysql_port"]
    ):
        raise core.QualificationError("target runtime/profile binding differs")
    if scenario == "tpch_memory":
        daemon = runtime.get("dedicated_daemon") or {}
        for key in ("socket", "socket_uri", "unit", "data_root"):
            if daemon.get(key) != profile[key]:
                raise core.QualificationError("TPC-H daemon contract differs: %s" % key)
        core.prepare_database_counter_transport = prepare_tpch_database_counter_transport
        core.db_counter_sample = tpch_db_counter_sample
        core.validate_client_container_readback = validate_tpch_client_container_readback
    else:
        collector_manifest_path = HERE.parent / "bundle_manifest.json"
        collector_manifest = core.load_object(
            collector_manifest_path, "shared collector manifest"
        )
        collector_unsigned = dict(collector_manifest)
        collector_id = collector_unsigned.pop("bundle_id", None)
        if (
            collector_id != HERE.parent.name
            or core.sha_bytes(core.canonical(collector_unsigned)) != collector_id
            or not core.valid_sidecar(collector_manifest_path)
        ):
            raise core.QualificationError("shared collector identity differs")
        toolchain = runtime.get("toolchain") or {}
        python_mount = ((runtime.get("client_runtime_mounts") or {}).get("python") or {})
        python_root = Path(str(toolchain.get("python_runtime_root") or ""))
        python_binary = Path(str(toolchain.get("python_runtime") or ""))
        mounted_binary = Path(str(python_mount.get("binary") or ""))
        mounted_destination = Path(str(python_mount.get("destination") or ""))
        if (
            toolchain.get("python_binary_relative") is not None
            or python_mount.get("source") != str(python_root)
            or mounted_destination != Path("/opt/mysql-tpcc-python")
            or mounted_binary != mounted_destination / "bin/python3.11"
            or python_binary != python_root / "bin/python3.11"
            or toolchain.get("python_binary_sha256")
            != "05bd6d6d900bd990b8c00d17df3b3e1425d6c32f0408f07cd29bc495ac4aff39"
        ):
            raise core.QualificationError(
                "TPC-C Python relative-path translation authority differs"
            )
        # The current target freezes both absolute endpoints.  Derive only the
        # unique relative join in memory; no process identity is trusted.
        toolchain["python_binary_relative"] = "bin/python3.11"
        transport = load_module(
            "mysql_shared_tpcc_counter_transport_v1", TPCC_TRANSPORT_PATH
        )
        core.validate_client_container_readback = validate_tpcc_client_container_readback
        transport.install(core)
    _activate_profile(profile)
    return manifest, runtime


def prepare_tpch_database_counter_transport(
    runtime,
    bundle,
    database_container,
    database_row,
    defaults_file,
    mysql_client_container,
    mysql_binary_in_client,
):
    """Bind the legacy SQL bridge to the private client UID, not UID 0.

    The TPC-H client drops every capability.  Consequently its UID 0 cannot
    bypass DAC and cannot read a 0400 file owned by the fixed production UID
    1008.  The workload and counter bridge therefore share that exact UID;
    the credential remains private rather than becoming world-readable.
    """
    defaults_file = Path(defaults_file).resolve(strict=True)
    expected_defaults = (
        Path("/mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch/runtime_algorithm_c_successor_20260729")
        / "attempts" / CURRENT_TPCH_ATTEMPT / "credentials/mysql.cnf"
    )
    if defaults_file != expected_defaults:
        raise core.QualificationError("current TPC-H credential path differs")
    directory = defaults_file.parent
    directory_state = directory.stat(follow_symlinks=False)
    defaults_state = defaults_file.stat(follow_symlinks=False)
    if (
        directory_state.st_uid != 1008
        or directory_state.st_gid != 1008
        or stat.S_IMODE(directory_state.st_mode) != 0o700
        or defaults_state.st_uid != 1008
        or defaults_state.st_gid != 1008
        or stat.S_IMODE(defaults_state.st_mode) != 0o400
    ):
        raise core.QualificationError(
            "TPC-H private credential live permissions differ"
        )
    transport = _original_tpch_prepare_transport(
        runtime,
        bundle,
        database_container,
        database_row,
        defaults_file,
        mysql_client_container,
        mysql_binary_in_client,
    )
    if (
        not isinstance(transport, dict)
        or transport.get("docker_exec_user") != "0"
        or not re.fullmatch(r"[0-9a-f]{64}", str(transport.get("client_container_id") or ""))
        or int(transport.get("client_pid") or 0) <= 0
        or int(transport.get("client_process_start_ticks") or 0) <= 0
    ):
        raise core.QualificationError(
            "TPC-H current counter transport identity differs"
        )
    result = dict(transport)
    result["docker_exec_user"] = "1008:1008"
    result["credential_live_binding_sha256"] = core.sha_bytes(core.canonical({
        "path": str(defaults_file),
        "sha256": core.sha256(defaults_file),
        "uid": defaults_state.st_uid,
        "gid": defaults_state.st_gid,
        "mode": "%04o" % stat.S_IMODE(defaults_state.st_mode),
    }))
    result["credential_file_sha256"] = core.sha256(defaults_file)
    result["mysql_user"] = "root"
    return result


def tpch_db_counter_sample(
    mysql_binary,
    defaults_file,
    port,
    database_counter_transport=None,
):
    """Sample TPC-H counters as client UID 1008 and explicit MySQL root."""
    if database_counter_transport is None:
        return _original_tpch_db_counter_sample(
            mysql_binary, defaults_file, port, database_counter_transport
        )
    transport = database_counter_transport
    if (
        transport.get("docker_exec_user") != "1008:1008"
        or transport.get("mysql_user") != "root"
        or not re.fullmatch(r"[0-9a-f]{64}", str(transport.get("credential_live_binding_sha256") or ""))
    ):
        raise core.QualificationError(
            "TPC-H explicit counter user authority differs"
        )
    current = inspect_container(transport["client_container"])
    current_id = core._full_container_id(current, "database-counter client")
    current_name = core._container_name(current, "database-counter client")
    current_pid = int((current.get("State") or {}).get("Pid") or 0)
    current_start_ticks = core.process_start_ticks(current_pid)
    current_identity_sha = core._client_container_config_identity(current)
    if (
        current_id != transport["client_container_id"]
        or current_name != transport["client_container_name"]
        or current_id[:12] != transport["client_container_short_id"]
        or current_pid != int(transport["client_pid"])
        or current_start_ticks
        != int(transport["client_process_start_ticks"])
        or current_identity_sha != transport["client_inspect_config_sha256"]
    ):
        raise core.QualificationError(
            "TPC-H database-counter client identity changed"
        )
    argv = list(core.DOCKER_CLI) + [
        "exec", "--user", transport["docker_exec_user"],
        transport["client_container_id"],
        transport["mysql_binary_in_client"],
        "--defaults-extra-file=%s"
        % transport.get("mysql_defaults_file_in_client", defaults_file),
        "--user=%s" % transport["mysql_user"],
        "--protocol=TCP",
        "--host=127.0.0.1",
        "--port=%d" % int(port),
        "--batch",
        "--raw",
        "--skip-column-names",
        "--execute",
        "SHOW GLOBAL STATUS",
    ]
    output = core.run(argv, timeout=1, label="database counter sample")
    observed = {}
    required_lower = {name.lower() for name in core.DB_COUNTERS}
    for line in output.splitlines():
        pieces = line.split("\t", 1)
        if len(pieces) != 2:
            continue
        key = pieces[0].strip().lower()
        if key not in required_lower:
            continue
        if key in observed:
            raise core.QualificationError(
                "duplicate database counter: %s" % pieces[0]
            )
        try:
            observed[key] = int(pieces[1].strip())
        except ValueError as exc:
            raise core.QualificationError(
                "database counter value is not an integer: %s" % pieces[0]
            ) from exc
    missing = [
        name for name in core.DB_COUNTERS if name.lower() not in observed
    ]
    if missing:
        raise core.QualificationError(
            "database counter coverage incomplete: %s" % ",".join(missing)
        )
    return {name: observed[name.lower()] for name in core.DB_COUNTERS}


def inspect_container(identifier):
    profiles = []
    if _active_profile is not None:
        profiles.append(_active_profile)
    profiles.extend(profile for profile in PROFILES.values() if profile not in profiles)
    matches = []
    for profile in profiles:
        completed = subprocess.run(
            ["docker", "--host", profile["socket_uri"], "inspect", str(identifier)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=5,
            check=False,
        )
        if completed.returncode != 0:
            continue
        try:
            rows = json.loads(completed.stdout)
        except ValueError as exc:
            raise core.QualificationError("container inspect JSON invalid: %s" % exc)
        if not isinstance(rows, list) or len(rows) != 1:
            raise core.QualificationError("container inspect cardinality differs")
        matches.append(rows[0])
    if len(matches) != 1:
        raise core.QualificationError(
            "container identity is absent or ambiguous across sealed sockets"
        )
    return matches[0]


def shared_docker_container_inventory():
    """Merge exact inventories from both sealed node04 Docker sockets."""
    selected = _active_profile
    if selected is None:
        raise core.QualificationError("shared Docker profile is not bound")
    merged = {}
    evidence_by_unit = {}
    try:
        for profile in PROFILES.values():
            _activate_profile(profile)
            containers, evidence = _original_docker_container_inventory()
            overlap = set(merged).intersection(containers)
            if overlap:
                raise core.QualificationError(
                    "container identity overlaps sealed Docker sockets"
                )
            merged.update(containers)
            evidence_by_unit[profile["unit"]] = evidence
    finally:
        _activate_profile(selected)
    helper = HERE / ROOT_HELPER
    combined = {
        "schema_version":
            "mysql_shared_multi_socket_root_docker_inventory_closure_v1",
        "sockets": evidence_by_unit,
        "container_ids": sorted(merged),
    }
    root_evidence = {
        "helper_relative": ROOT_HELPER,
        "helper_sha256": core.sha256(helper),
        "output_sha256": core.sha_bytes(core.canonical(combined)),
        "effective_uid_gid": [0, 0],
        "mutation_performed": False,
        "container_count": len(merged),
        "multi_socket_closure": combined,
    }
    return merged, root_evidence



def _exact_option(argv, name):
    indexes = [index for index, value in enumerate(argv) if value == name]
    if len(indexes) != 1 or indexes[0] + 1 >= len(argv):
        raise core.QualificationError("collector control option differs: " + name)
    return argv[indexes[0] + 1]


def _proc_argv_and_hash(pid, expected_hash):
    try:
        raw = (Path("/proc") / str(pid) / "cmdline").read_bytes()
    except (OSError, PermissionError) as exc:
        raise core.QualificationError("collector control cmdline unreadable: %s" % exc)
    if core.sha_bytes(raw) != expected_hash:
        raise core.QualificationError("collector control cmdline identity changed")
    argv = [value.decode("utf-8", errors="strict") for value in raw.split(b"\0") if value]
    return argv


def _sealed_collector_command(argv):
    entry = str(Path(__file__).resolve())
    if (len(argv) < 3 or Path(argv[0]).resolve() != Path(sys.executable).resolve()
            or Path(argv[1]).resolve() != Path(entry)
            or argv.count("--qualification-paired") != 1):
        raise core.QualificationError("paired collector executable identity differs")
    command_path = Path(_exact_option(argv, "--command-spec"))
    authority_path = Path(_exact_option(argv, "--main-agent-authority"))
    token_path = Path(_exact_option(argv, "--main-agent-token-file"))
    command = core.load_object(command_path, "paired collector command")
    authority = core.load_object(authority_path, "paired collector authority")
    reject_static_pid_authority(command, "paired command")
    reject_static_pid_authority(authority, "paired authority")
    token_state = token_path.stat(follow_symlinks=False)
    token = token_path.read_text(encoding="ascii").strip()
    manifest_path = HERE.parent / "bundle_manifest.json"
    manifest = core.load_object(manifest_path, "collector manifest")
    now = time.time()
    flags = ("capture_authorized", "formal_r0_authorized", "formal_authorized", "accepted_round_authorized")
    if (not core.valid_sidecar(command_path) or not core.valid_sidecar(authority_path)
            or token_path.is_symlink() or not token_path.is_file()
            or stat.S_IMODE(token_state.st_mode) != 0o600 or token_state.st_uid != 0
            or len(token) < 48 or any(character.isspace() for character in token)
            or command.get("mode") != "qualification_paired" or command.get("debug_only") is not True
            or any(command.get(key) is not False for key in flags)
            or authority.get("authorized_by") != "main_agent" or authority.get("debug_only") is not True
            or any(authority.get(key) is not False for key in flags)
            or authority.get("qualification_attempt_id") != command.get("qualification_attempt_id")
            or authority.get("command_spec_sha256") != core.sha256(command_path)
            or authority.get("main_agent_token_sha256") != core.sha_bytes(token.encode("ascii"))
            or authority.get("collector_bundle_id") != HERE.parent.name
            or authority.get("collector_manifest_sha256") != core.sha256(manifest_path)
            or manifest.get("bundle_id") != HERE.parent.name
            or not float(authority.get("not_before_epoch") or 0) <= now <= float(authority.get("expires_epoch") or 0)):
        raise core.QualificationError("paired collector signed authority differs")
    return command


def _classify_verified_collector_trees(result, mysql_cpuset, workload_cpuset):
    own_pid = os.getpid()
    rows = result.get("processes") or []
    by_pid = {int(row.get("pid") or 0): row for row in rows}
    own_row = by_pid.get(own_pid)
    if own_row is None:
        raise core.QualificationError("own collector control row is absent")
    own_argv = _proc_argv_and_hash(own_pid, own_row.get("cmdline_sha256"))
    own_command = _sealed_collector_command(own_argv)
    own_target = _exact_option(own_argv, "--container")
    own_peer = _exact_option(own_argv, "--peer-container")
    peer_attempt = (own_command.get("paired_peer_workload") or {}).get("qualification_attempt_id")
    candidates = []
    for row in rows:
        pid = int(row.get("pid") or 0)
        if (pid == own_pid or int(row.get("ppid") or 0) != 1
                or row.get("container_identity") is not None
                or row.get("comm") != "python3.11"):
            continue
        try:
            argv = _proc_argv_and_hash(pid, row.get("cmdline_sha256"))
        except (core.QualificationError, UnicodeError):
            continue
        if len(argv) < 2 or Path(argv[1]).resolve() != Path(__file__).resolve() or "--qualification-paired" not in argv:
            continue
        command = _sealed_collector_command(argv)
        if (_exact_option(argv, "--container") != own_peer
                or _exact_option(argv, "--peer-container") != own_target
                or command.get("qualification_attempt_id") != peer_attempt
                or (command.get("paired_peer_workload") or {}).get("qualification_attempt_id") != own_command.get("qualification_attempt_id")):
            raise core.QualificationError("paired collector reciprocal binding differs")
        candidates.append((row, command))
    if len(candidates) != 1:
        raise core.QualificationError("paired collector root cardinality differs")
    peer_row, peer_command = candidates[0]
    own_cpus = core.expand_cpuset((own_command.get("workload_process_isolation") or {}).get("cpuset_cpus", ""))
    peer_cpus = core.expand_cpuset((peer_command.get("workload_process_isolation") or {}).get("cpuset_cpus", ""))
    if (own_cpus != core.expand_cpuset(workload_cpuset) or not peer_cpus
            or not peer_cpus.isdisjoint(core.expand_cpuset(mysql_cpuset) | own_cpus)):
        raise core.QualificationError("paired collector CPU isolation binding differs")
    peer_client = peer_command.get("client_container") or {}
    peer_client_id = peer_client.get("container_id")
    peer_client_rows = [row for row in rows if row.get("container_identity") == peer_client_id]
    if (not isinstance(peer_client_id, str) or re.fullmatch(r"[0-9a-f]{64}", peer_client_id) is None
            or not peer_client_rows):
        raise core.QualificationError("paired peer client identity differs")
    for row in peer_client_rows:
        if (core.expand_cpuset(row.get("cpus_allowed_list", "")) != peer_cpus
                or int(row.get("declared_device_writable_fds") or 0) != 0):
            raise core.QualificationError("paired peer client process isolation differs")
        row["role"] = "qualification_workload"
    result["paired_peer_client_container_id"] = peer_client_id
    result["paired_peer_client_processes_exact_authority_verified"] = True
    def descendants(root_pid):
        closure = {root_pid}; changed = True
        while changed:
            changed = False
            for row in rows:
                pid = int(row.get("pid") or 0)
                if int(row.get("ppid") or 0) in closure and pid not in closure:
                    closure.add(pid); changed = True
        return closure
    for root_pid, expected_cpus, root_role in ((own_pid, own_cpus, "qualification_control_plane"), (int(peer_row["pid"]), peer_cpus, "qualification_peer_control_plane")):
        for pid in descendants(root_pid):
            row = by_pid.get(pid)
            if row is None:
                continue
            if (row.get("container_identity") is not None
                    or core.expand_cpuset(row.get("cpus_allowed_list", "")) != expected_cpus
                    or int(row.get("declared_device_writable_fds") or 0) != 0):
                raise core.QualificationError("collector control descendant isolation differs")
            row["role"] = root_role if pid == root_pid else "qualification_workload"
    result["paired_peer_control_plane_root_pid"] = int(peer_row["pid"])
    result["paired_peer_control_plane_attempt_id"] = peer_attempt
    result["paired_peer_control_tree_exact_authority_verified"] = True

def shared_rolling_process_inventory(*args, **kwargs):
    """Classify the verified active dockerd as target infrastructure."""
    result = _original_rolling_process_inventory(*args, **kwargs)
    snapshot_attempts = 1
    while (
        int(result.get("race_count") or 0) > 0
        and snapshot_attempts < 16
    ):
        # Short-lived SQL adapter children can exit while /proc is being
        # enumerated.  Retry the complete read-only snapshot with bounded
        # backoff; an accepted sample still requires an exact zero-race view.
        time.sleep(0.01 * min(snapshot_attempts, 4))
        result = _original_rolling_process_inventory(*args, **kwargs)
        snapshot_attempts += 1
    result["inventory_snapshot_attempts"] = snapshot_attempts
    result["inventory_snapshot_retry_policy"] = {
        "schema_version": "mysql_shared_proc_snapshot_retry_policy_v1",
        "maximum_complete_snapshot_attempts": 16,
        "retry_backoff_milliseconds": [10, 20, 30, 40],
        "race_rows_ignored": False,
        "accepted_snapshot_requires_zero_races": True,
    }
    if "--qualification-paired" in sys.argv:
        _classify_verified_collector_trees(result, args[4], args[5])
    profile = _active_profile
    if profile is None:
        raise core.QualificationError("shared Docker profile is not bound")
    try:
        daemon_pid = int(Path(profile["pidfile"]).read_text(encoding="ascii").strip())
    except (OSError, ValueError, UnicodeError) as exc:
        raise core.QualificationError("dedicated daemon PID file differs: %s" % exc)
    daemon_rows = [
        row for row in result["processes"] if int(row.get("pid") or 0) == daemon_pid
    ]
    if (
        len(daemon_rows) != 1
        or daemon_rows[0].get("role") != "co_resident"
        or daemon_rows[0].get("comm") != "dockerd"
        or core.expand_cpuset(daemon_rows[0].get("cpus_allowed_list", ""))
        != core.expand_cpuset(profile["daemon_cpuset"])
        or int(daemon_rows[0].get("starttime_ticks") or 0)
        != core.process_start_ticks(daemon_pid)
    ):
        raise core.QualificationError(
            "active dedicated Docker daemon process identity differs"
        )
    daemon_rows[0]["role"] = "target_dedicated_docker_daemon"
    mysql_cpus = core.expand_cpuset(args[4])
    workload_cpus = core.expand_cpuset(args[5])
    dangerous = [
        row
        for row in result["processes"]
        if row.get("role") == "co_resident"
        and (
            not core.expand_cpuset(row.get("cpus_allowed_list", "")).isdisjoint(
                mysql_cpus | workload_cpus
            )
            or int(row.get("declared_device_writable_fds") or 0) > 0
        )
    ]
    conflicts = list(result.get("conflicts") or [])
    if not dangerous:
        conflicts = [
            value for value in conflicts if value != "co_resident_cpu_or_device"
        ]
        result["foreign_writer_count"] = 0
    result["conflicts"] = sorted(set(conflicts))
    result["target_dedicated_docker_daemon_pid"] = daemon_pid
    result["target_dedicated_docker_daemon_verified"] = True
    result["postgresql_static_process_identity_trusted"] = False
    result["postgresql_inventory_policy"] = "dynamic_read_only_interference_gate"
    return result


def _root_docker_mount_inventory():
    if _active_profile is None:
        raise core.QualificationError("shared Docker profile is not bound")
    helper = HERE / ROOT_HELPER
    if helper.is_symlink() or not helper.is_file():
        raise core.QualificationError("sealed root Docker mount helper is absent")
    helper_hash = core.sha256(helper)
    command = [
        "sudo", "-n", sys.executable, "-B", str(helper),
        "--expected-helper-sha256", helper_hash,
        "--docker-socket-uri", _active_profile["socket_uri"],
    ]
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={"PATH": "/usr/bin:/bin", "PYTHONPATH": "", "PYTHONDONTWRITEBYTECODE": "1"},
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise core.QualificationError("root Docker mount helper failed: %s" % exc)
    if completed.returncode != 0 or completed.stderr:
        raise core.QualificationError("root Docker mount helper failed")
    if not completed.stdout or len(completed.stdout) > ROOT_HELPER_MAX_STDOUT_BYTES:
        raise core.QualificationError("root Docker mount helper output differs")
    try:
        value = json.loads(completed.stdout.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise core.QualificationError("root Docker mount helper JSON invalid: %s" % exc)
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != "mysql_shared_host_root_mount_inventory_v1"
        or value.get("status") != "PASS_READ_ONLY_ROOT_DOCKER_MOUNT_INVENTORY"
        or value.get("helper_sha256") != helper_hash
        or value.get("docker_socket_uri") != _active_profile["socket_uri"]
        or value.get("effective_uid_gid") != [0, 0]
        or value.get("mutation_performed") is not False
        or value.get("postgresql_read_only_inventory_allowed") is not True
        or not isinstance(value.get("containers"), list)
    ):
        raise core.QualificationError("root Docker mount helper authority differs")
    return value, {
        "helper_relative": ROOT_HELPER,
        "helper_sha256": helper_hash,
        "docker_socket_uri": _active_profile["socket_uri"],
        "output_sha256": core.sha_bytes(completed.stdout),
        "container_count": len(value["containers"]),
        "container_projection_sha256": core.sha_bytes(core.canonical(value["containers"])),
        "effective_uid_gid": [0, 0],
        "mutation_performed": False,
    }


def _proc_startticks(pid):
    raw = (Path("/proc") / str(pid) / "stat").read_text(encoding="ascii")
    boundary = raw.rfind(") ")
    if boundary < 0:
        raise core.QualificationError("daemon process stat differs")
    fields = raw[boundary + 2 :].split()
    return int(fields[19])


def dedicated_daemon_readback():
    profile = _active_profile
    if profile is None:
        raise core.QualificationError("shared Docker profile is not bound")
    shown = core.run(
        [
            "systemctl", "show", profile["unit"],
            "--property=ActiveState", "--property=SubState",
            "--property=MainPID", "--property=FragmentPath",
            "--property=ExecStart", "--property=ControlGroup",
            "--property=Id", "--property=Transient",
        ],
        timeout=30,
        label="dedicated daemon systemd readback",
    )
    properties = {}
    for line in shown.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            properties[key] = value
    pid = int(properties.get("MainPID") or 0)
    expected_daemon_argv = [
        "/usr/bin/dockerd",
        "--host=" + profile["socket_uri"],
        "--data-root=" + profile["data_root"],
        "--exec-root=" + profile["exec_root"],
        "--pidfile=" + profile["pidfile"],
        "--storage-driver=overlay2", "--bridge=none", "--iptables=false",
        "--ip-masq=false", "--ip-forward=false", "--userland-proxy=false",
        "--log-driver=none",
    ]
    expected_exec = (
        "argv[]=/usr/bin/taskset -c %s %s ;"
        % (profile["declared_daemon_cpuset"], " ".join(expected_daemon_argv))
    )
    proc = Path("/proc") / str(pid)
    try:
        ppid = int((proc / "stat").read_text(encoding="ascii").split()[3])
        cmdline = [
            value.decode("utf-8")
            for value in (proc / "cmdline").read_bytes().split(b"\0")
            if value
        ]
        status = core.process_status(pid)
        cgroup = (proc / "cgroup").read_text(encoding="ascii").splitlines()
        namespaces = {
            name: os.readlink(str(proc / "ns" / name))
            for name in ("ipc", "mnt", "net", "pid", "user", "uts")
        }
    except (OSError, UnicodeError, ValueError, IndexError) as exc:
        raise core.QualificationError("daemon process identity unreadable: %s" % exc)
    socket_state = os.stat(profile["socket"], follow_symlinks=False)
    listener = core._unix_listener_identity(profile["socket"], pid)
    raw_info = core.run(
        ["docker", "--host", profile["socket_uri"], "info", "--format", "{{json .}}"],
        timeout=30,
        label="dedicated daemon info readback",
    )
    try:
        info = json.loads(raw_info)
    except ValueError as exc:
        raise core.QualificationError("dedicated daemon info JSON differs")
    data_state = os.stat(profile["data_root"])
    mounted = core.run(
        ["findmnt", "-n", "-o", "SOURCE,FSTYPE,TARGET", "--target", profile["data_root"]],
        timeout=30,
        label="dedicated daemon root findmnt",
    ).strip().split()
    if (
        properties.get("ActiveState") != "active"
        or properties.get("SubState") != "running"
        or properties.get("Id") != profile["unit"]
        or properties.get("FragmentPath") != profile["unit_fragment"]
        or properties.get("Transient") != "yes"
        or pid <= 0
        or ppid != 1
        or cmdline != expected_daemon_argv
        or expected_exec not in properties.get("ExecStart", "")
        or core.expand_cpuset(status.get("Cpus_allowed_list", ""))
        != core.expand_cpuset(profile["daemon_cpuset"])
        or not stat.S_ISSOCK(socket_state.st_mode)
        or info.get("DockerRootDir") != profile["data_root"]
        or info.get("Driver") != "overlay2"
        or info.get("CgroupDriver") != "cgroupfs"
        or "%d:%d" % (os.major(data_state.st_dev), os.minor(data_state.st_dev))
        != profile["device_major_minor"]
        or len(mounted) != 3
        or mounted[0] != profile["device"]
        or mounted[2] != profile["mount"]
        or any("postgres" in value.lower() for value in cmdline)
    ):
        raise core.QualificationError("dedicated daemon identity differs")
    return {
        "schema_version": "mysql_shared_dedicated_daemon_readback_v1",
        "status": "PASS_DEDICATED_MYSQL_DAEMON_EXACT_NON_PG",
        "scenario": next(key for key, value in PROFILES.items() if value is profile),
        "unit": profile["unit"],
        "unit_fragment": profile["unit_fragment"],
        "control_group": properties.get("ControlGroup"),
        "main_pid": pid,
        "main_pid_starttime_ticks": _proc_startticks(pid),
        "main_pid_cpus_allowed_list": status.get("Cpus_allowed_list"),
        "main_pid_mems_allowed_list": status.get("Mems_allowed_list"),
        "declared_daemon_cpuset": profile["declared_daemon_cpuset"],
        "effective_daemon_cpuset": profile["daemon_cpuset"],
        "static_pid_authority_trusted": False,
        "daemon_identity_source": "same_live_systemd_proc_socket_readback",
        "cgroup_sha256": core.sha_bytes(("\n".join(cgroup) + "\n").encode("utf-8")),
        "namespace_identities": namespaces,
        "socket": profile["socket"],
        "socket_uri": profile["socket_uri"],
        "socket_filesystem_inode": socket_state.st_ino,
        "socket_listener_identity": listener,
        "docker_root_dir": info["DockerRootDir"],
        "docker_root_device_major_minor": profile["device_major_minor"],
        "graph_driver": info["Driver"],
        "cgroup_driver": info["CgroupDriver"],
        "postgresql_mutation_prohibited": True,
        "accepted_round_authorized": False,
    }


def validate_effective_blkio_authority(container_row, runtime, defaults_file, bundle_id):
    defaults_file = Path(defaults_file)
    attempt_root = defaults_file.parent.parent
    scenario = runtime["scenario"]
    profile = _profile_for_scenario(scenario)
    if scenario == "tpch_memory" and attempt_root.name != CURRENT_TPCH_ATTEMPT:
        raise core.QualificationError("current TPC-H attempt differs")
    authority_path = attempt_root / "blkio_throttle_authority.json"
    value = core.load_object(authority_path, "effective blkio authority")
    sidecar = authority_path.with_name(authority_path.name + ".sha256")
    if not sidecar.is_file() or not core.valid_sidecar(authority_path):
        raise core.QualificationError("effective blkio authority sidecar differs")
    schema = {
        "tpch_memory": "mysql_tpch_blkio_v1_throttle_authority_v1",
        "tpcc_cpu": "mysql_tpcc_blkio_v1_throttle_authority_v1",
    }[scenario]
    container_id = container_row.get("Id")
    pid = int((container_row.get("State") or {}).get("Pid") or 0)
    cgroup_path = "/sys/fs/cgroup/blkio/docker/" + str(container_id)
    envelope = runtime["resource_envelope"]
    storage = runtime["storage"]
    attempt_field = "attempt_token" if scenario == "tpch_memory" else "attempt_id"
    status_ok = value.get("status") in (
        "PASS_EFFECTIVE_CGROUP_V1_THROTTLE", "PASS"
    )
    flag_ok = (
        value.get("docker_hostconfig_is_declaration_not_effective_authority") is True
        if scenario == "tpch_memory"
        else value.get("docker_hostconfig_readback_not_authoritative") is True
    )
    if (
        value.get("schema_version") != schema
        or not status_ok
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("database_container_id") != container_id
        or int(value.get("database_pid") or 0) != pid
        or value.get("host_effective_uid_gid") != [0, 0]
        or value.get("cgroup_version") != "v1"
        or value.get("cgroup_path") != cgroup_path
        or value.get("device_major_minor") != storage["expected_live_major_minor"]
        or int(value.get("read_bps") or 0) != int(envelope["io_read_bps"])
        or int(value.get("write_bps") or 0) != int(envelope["io_write_bps"])
        or value.get("read_throttle_file")
        != cgroup_path + "/blkio.throttle.read_bps_device"
        or value.get("write_throttle_file")
        != cgroup_path + "/blkio.throttle.write_bps_device"
        or not flag_ok
        or value.get("database_or_client_started") is not True
        or value.get("workload_started") is not False
        or value.get("formal_launch_authorized") is not False
        or value.get("accepted_round_authorized") is not False
        or value.get("bundle_id") != bundle_id
        or value.get(attempt_field) != attempt_root.name
    ):
        raise core.QualificationError("effective cgroup-v1 blkio authority differs")
    if scenario == "tpch_memory":
        unsigned = dict(value)
        recorded = unsigned.pop("authority_sha256", None)
        if (
            recorded != core.sha_bytes(core.canonical(unsigned))
            or int(value.get("database_pid_starttime_ticks") or 0)
            != core.process_start_ticks(pid)
            or value.get("memory_swappiness") != 0
            or value.get("total_swap_bytes") != 0
            or value.get("database_restarted_by_remediation") is not False
            or value.get("postgresql_mutation_performed") is not False
            or value.get("storage_mutation_performed") is not False
        ):
            raise core.QualificationError(
                "TPC-H restart blkio authority digest/identity differs"
            )
    else:
        recorded = core.sha256(authority_path)
    roots = core._cgroup_roots(pid)
    if roots["version"] != 1:
        raise core.QualificationError("effective blkio authority requires cgroup v1")
    live = core._v1_effective_throttle(
        roots["io"], storage["expected_live_major_minor"],
        envelope["io_read_bps"], envelope["io_write_bps"],
    )
    return {
        "schema_version": "mysql_shared_effective_blkio_authority_v1",
        "source_schema_version": schema,
        "authority_sha256": recorded,
        "bundle_id": bundle_id,
        "attempt_token": attempt_root.name,
        "database_container_id": container_id,
        "database_pid": pid,
        "same_live_identity_verified": True,
        "cgroup_path": cgroup_path,
        "device_major_minor": storage["expected_live_major_minor"],
        "read_bps": int(envelope["io_read_bps"]),
        "write_bps": int(envelope["io_write_bps"]),
        "live_effective_cgroup_v1_throttle": live,
        "docker_hostconfig_is_non_authoritative": True,
    }


def validate_container_readback(
    container_row, runtime, effective_blkio_authority=None,
    bundle_id=None, attempt_token=None,
):
    envelope = runtime["resource_envelope"]
    storage = runtime["storage"]
    host_config = container_row.get("HostConfig") or {}
    state = container_row.get("State") or {}
    pid = int(state.get("Pid") or 0)
    container_id = container_row.get("Id")
    if runtime.get("scenario") == "tpch_memory" and core._container_name(container_row, "TPC-H database") != CURRENT_TPCH_DB_NAME:
        raise core.QualificationError("current TPC-H database attempt binding differs")
    if state.get("Running") is not True or pid <= 0:
        raise core.QualificationError("target container has no running PID")
    period = int(host_config.get("CpuPeriod") or 0)
    quota = int(host_config.get("CpuQuota") or 0)
    if period <= 0 or abs(float(quota) / float(period) - float(envelope["cpu_quota_cores"])) > 1e-9:
        raise core.QualificationError("container CPU quota differs")
    for key, expected in {
        "CpusetCpus": envelope["cpuset_cpus"],
        "CpusetMems": envelope["cpuset_mems"],
        "Memory": envelope["memory_bytes"],
        "MemorySwap": envelope["memory_swap_bytes"],
        "PidsLimit": envelope["pids_max"],
    }.items():
        actual = host_config.get(key)
        if key.startswith("Cpuset"):
            if core.expand_cpuset(actual) != core.expand_cpuset(expected):
                raise core.QualificationError("container %s differs" % key)
        elif int(actual or 0) != int(expected):
            raise core.QualificationError("container %s differs" % key)
    if (
        not isinstance(effective_blkio_authority, dict)
        or effective_blkio_authority.get("schema_version")
        != "mysql_shared_effective_blkio_authority_v1"
        or effective_blkio_authority.get("bundle_id") != bundle_id
        or effective_blkio_authority.get("attempt_token") != attempt_token
        or effective_blkio_authority.get("database_container_id") != container_id
        or effective_blkio_authority.get("database_pid") != pid
        or effective_blkio_authority.get("same_live_identity_verified") is not True
    ):
        raise core.QualificationError("container lacks same-live effective blkio authority")
    read_rows = host_config.get("BlkioDeviceReadBps")
    write_rows = host_config.get("BlkioDeviceWriteBps")
    if read_rows == [] and write_rows == []:
        docker_projection = "empty_arrays_with_exact_effective_cgroup_v1_authority"
    elif (
        core._exact_rate_binding(
            read_rows, storage["device"], storage["expected_live_major_minor"], envelope["io_read_bps"]
        )
        and core._exact_rate_binding(
            write_rows, storage["device"], storage["expected_live_major_minor"], envelope["io_write_bps"]
        )
    ):
        docker_projection = "exact_nonempty_docker_declaration"
    else:
        raise core.QualificationError("container Docker blkio projection differs")
    command = (container_row.get("Config") or {}).get("Cmd") or []
    port_arg = "--port=%d" % int(envelope["mysql_port"])
    if (
        host_config.get("NetworkMode") != "host"
        or (host_config.get("PortBindings") or {}) != {}
        or "--bind-address=127.0.0.1" not in command
        or port_arg not in command
    ):
        raise core.QualificationError("database host-network/port binding differs")
    status = core.process_status(pid)
    if (
        core.expand_cpuset(status.get("Cpus_allowed_list", ""))
        != core.expand_cpuset(envelope["cpuset_cpus"])
        or core.expand_cpuset(status.get("Mems_allowed_list", ""))
        != core.expand_cpuset(envelope["cpuset_mems"])
    ):
        raise core.QualificationError("database PID affinity differs")
    return {
        "container_id": container_id,
        "container_name": container_row.get("Name"),
        "pid": pid,
        "data_partition_device": storage["device"],
        "throttle_parent_device": storage["device"],
        "throttle_parent_major_minor": storage["expected_live_major_minor"],
        "effective_cgroup_v1_throttle": effective_blkio_authority[
            "live_effective_cgroup_v1_throttle"
        ],
        "docker_blkio_projection": docker_projection,
        "effective_blkio_authority": effective_blkio_authority,
        "host_config": {
            key: host_config.get(key)
            for key in (
                "CpuQuota", "CpuPeriod", "CpusetCpus", "CpusetMems",
                "Memory", "MemorySwap", "PidsLimit",
                "BlkioDeviceReadBps", "BlkioDeviceWriteBps",
                "NetworkMode", "PortBindings",
            )
        },
        "pid_cpus_allowed_list": status.get("Cpus_allowed_list"),
        "pid_mems_allowed_list": status.get("Mems_allowed_list"),
    }


def validate_tpcc_client_container_readback(
    container_row,
    expected,
    binding,
    tmpfs_destination,
    host_tmpfs_directory=None,
    target_major_minor=None,
):
    """Validate the exact v13 client, including its inert datadir mask.

    The image declares ``/var/lib/mysql`` as a volume.  Docker therefore
    creates one writable mask volume even though the client is a sleeping,
    read-only-rootfs workload container.  It is accepted only at that exact
    destination, below the dedicated TPC-C daemon root, with no database bind
    source and with the rolling zero-write/device-attribution gate still
    required by the shared core.
    """
    host_config = container_row.get("HostConfig") or {}
    state = container_row.get("State") or {}
    container_id = core._full_container_id(container_row, "TPC-C client")
    container_name = core._container_name(container_row, "TPC-C client")
    pid = int(state.get("Pid") or 0)
    if state.get("Running") is not True or pid <= 0:
        raise core.QualificationError("TPC-C client has no running PID")
    if (
        container_id != binding.get("container_id")
        or binding.get("container_name") not in (None, container_name)
        or container_row.get("Image") != binding.get("image_id")
        or container_row.get("Created") != binding.get("created")
        or core._client_container_config_identity(container_row)
        != binding.get("inspect_config_sha256")
    ):
        raise core.QualificationError("TPC-C client immutable identity differs")
    period = int(host_config.get("CpuPeriod") or 0)
    quota = int(host_config.get("CpuQuota") or 0)
    if period <= 0 or abs(
        float(quota) / float(period) - float(expected["cpu_quota_cores"])
    ) > 1e-9:
        raise core.QualificationError("TPC-C client CPU quota differs")
    for key, wanted in {
        "CpusetCpus": expected["cpuset_cpus"],
        "CpusetMems": expected["cpuset_mems"],
        "Memory": expected["memory_bytes"],
        "MemorySwap": expected["memory_swap_bytes"],
        "PidsLimit": expected["pids_max"],
    }.items():
        actual = host_config.get(key)
        if key.startswith("Cpuset"):
            if core.expand_cpuset(actual) != core.expand_cpuset(wanted):
                raise core.QualificationError("TPC-C client %s differs" % key)
        elif int(actual or 0) != int(wanted):
            raise core.QualificationError("TPC-C client %s differs" % key)
    if (
        host_config.get("NetworkMode") != "host"
        or host_config.get("ReadonlyRootfs") is not True
        or (host_config.get("LogConfig") or {}).get("Type") != "none"
        or "ALL" not in (host_config.get("CapDrop") or [])
        or not any(
            str(value).replace("=", ":").lower()
            in ("no-new-privileges", "no-new-privileges:true")
            for value in (host_config.get("SecurityOpt") or [])
        )
        or host_config.get("Tmpfs") not in (None, {})
    ):
        raise core.QualificationError("TPC-C client hardening differs")
    mounts = container_row.get("Mounts") or []
    observed = {}
    measurement = []
    masks = []
    for mount in mounts:
        if mount.get("Type") in ("bind", "volume"):
            observed[(mount.get("Source"), mount.get("Destination"))] = mount.get("RW")
        if mount.get("Destination") == tmpfs_destination:
            measurement.append(mount)
        if mount.get("Destination") == "/var/lib/mysql":
            masks.append(mount)
        if (
            mount.get("RW") is True
            and mount.get("Destination") not in (tmpfs_destination, "/var/lib/mysql")
            and not core._is_docker_managed_mutable_bind(mount, container_id)
        ):
            raise core.QualificationError("TPC-C client mutable mount differs")
    if (
        len(measurement) != 1
        or measurement[0].get("Type") != "bind"
        or measurement[0].get("RW") is not True
        or len(masks) != 1
        or masks[0].get("Type") != "volume"
        or masks[0].get("RW") is not True
        or not str(masks[0].get("Source") or "").startswith(
            PROFILES["tpcc_cpu"]["data_root"] + "/volumes/"
        )
        or not str(masks[0].get("Source") or "").endswith("/_data")
        or not re.fullmatch(
            r"[0-9a-f]{64}", Path(str(masks[0].get("Source"))).parent.name
        )
    ):
        raise core.QualificationError("TPC-C client measurement/datadir mask differs")
    measurement_source = Path(measurement[0]["Source"])
    if (
        host_tmpfs_directory is not None
        and measurement_source.resolve()
        != Path(host_tmpfs_directory).resolve()
    ):
        raise core.QualificationError(
            "TPC-C measurement command binding differs"
        )
    effective_target_major_minor = (
        target_major_minor
        if target_major_minor is not None
        else PROFILES["tpcc_cpu"]["device_major_minor"]
    )
    if effective_target_major_minor != PROFILES["tpcc_cpu"]["device_major_minor"]:
        raise core.QualificationError("TPC-C target-device binding differs")
    mask_state = Path(str(masks[0]["Source"])).stat()
    mask_major_minor = "%d:%d" % (
        os.major(mask_state.st_dev), os.minor(mask_state.st_dev)
    )
    if mask_major_minor != effective_target_major_minor:
        raise core.QualificationError("TPC-C inert mask device differs")
    raw_mount = core.run(
        [
            "findmnt", "-n", "-o", "FSTYPE,TARGET,OPTIONS",
            "--target", str(measurement_source),
        ],
        timeout=5,
        label="TPC-C measurement tmpfs readback",
    ).strip().split(None, 2)
    sizes = []
    if len(raw_mount) == 3:
        for option in raw_mount[2].split(","):
            if option.startswith("size="):
                sizes.append(core._parse_size_option(option.split("=", 1)[1]))
    if (
        len(raw_mount) != 3
        or raw_mount[0] != "tmpfs"
        or Path(raw_mount[1]).resolve() != measurement_source.resolve()
        or len(sizes) != 1
        or sizes[0] < int(expected["tmpfs_bytes"])
    ):
        raise core.QualificationError("TPC-C measurement tmpfs differs")
    for row in binding.get("required_read_only_mounts") or []:
        if observed.get((row["source"], row["destination"])) is not False:
            raise core.QualificationError("TPC-C required read-only mount differs")
    status = core.process_status(pid)
    if (
        core.expand_cpuset(status.get("Cpus_allowed_list", ""))
        != core.expand_cpuset(expected["cpuset_cpus"])
        or core.expand_cpuset(status.get("Mems_allowed_list", ""))
        != core.expand_cpuset(expected["cpuset_mems"])
    ):
        raise core.QualificationError("TPC-C client PID affinity differs")
    return {
        "container_id": container_id,
        "container_name": container_name,
        "container_short_id": container_id[:12],
        "pid": pid,
        "process_start_ticks": core.process_start_ticks(pid),
        "host_config_sha256": core.sha_bytes(
            core.canonical(
                {
                    key: host_config.get(key)
                    for key in (
                        "CpuQuota", "CpuPeriod", "CpusetCpus", "CpusetMems",
                        "Memory", "MemorySwap", "PidsLimit", "NetworkMode",
                        "ReadonlyRootfs", "LogConfig", "CapDrop", "SecurityOpt",
                        "Tmpfs",
                    )
                }
            )
        ),
        "inspect_config_sha256": core._client_container_config_identity(container_row),
        "measurement_tmpfs": {
            "source": str(measurement_source),
            "size_bytes": sizes[0],
        },
        "inert_datadir_mask_volume": {
            "source": masks[0]["Source"],
            "destination": "/var/lib/mysql",
            "write_authority": False,
        },
    }


core.verify_bundle = _verify_target_bundle
core.validate_command_spec = validate_command_spec_without_static_pid
core.validate_main_agent_authority = validate_main_agent_authority_without_static_pid
core.inspect_container = inspect_container
core._root_docker_mount_inventory = _root_docker_mount_inventory
core._docker_container_inventory = shared_docker_container_inventory
core.rolling_process_inventory = shared_rolling_process_inventory
core.ROOT_DOCKER_MOUNT_HELPER = ROOT_HELPER
core.dedicated_daemon_readback = dedicated_daemon_readback
core.validate_tpch_effective_blkio_authority = validate_effective_blkio_authority
core.validate_container_readback = validate_container_readback


if __name__ == "__main__":
    try:
        raise SystemExit(core.main())
    except core.QualificationError as exc:
        sys.stderr.write("NOT_READY: %s\n" % exc)
        raise SystemExit(2)
