#!/usr/bin/env python3
"""Build the immutable TPC-H dedicated-Docker-daemon successor offline."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve()
BASE_BUILDER = ROOT / "shell/build_mysql_tpch_readiness_evidence_successor_v1.py"
BASE_BUILDER_SHA256 = (
    "5a5827896398de8bed93be63d5359b3a8cef6d4c6e247416869fda0f8a8ae263"
)
PREDECESSOR_ID = (
    "fb553aa090db2f8841d7c99aa9e89a6bd705138eca5057cdd720b9b058f67575"
)
PREDECESSOR_MANIFEST_SHA256 = (
    "d680d9a3fc6b0793f76e0fb0eb49dfb684a2e5f19794372430f500ca459e2760"
)
PREDECESSOR = (
    ROOT / "artifacts/mysql_tpch_readiness_evidence_successor_v1"
    / PREDECESSOR_ID
)
BUNDLE_ROOT = ROOT / "artifacts/mysql_tpch_dedicated_daemon_successor_v1"
INVALIDATION = (
    ROOT / "preflight/mysql_tpch_fb553a_shared_daemon_invalidation_20260731.json"
)
INTERMEDIATE_INVALIDATION = (
    ROOT
    / "preflight/mysql_tpch_intermediate_dedicated_bundles_invalidation_20260731.json"
)
CONTRACT = (
    ROOT / "preflight/mysql_tpch_dedicated_daemon_contract_v1.json"
)
UNIT = ROOT / "preflight/ra-docker-mysql-tpch-v1.service"
AUTHORITY = "mysql_tpch_distributed_v2_authority.py"
COLLECTOR = "qualification/mysql_distributed_live_readback_qualification_v1.py"
BLKIO = "qualification/mysql_tpch_blkio_v1.py"
FRESH_R0 = "qualification/mysql_tpch_v2_fresh_r0_runner_v1.py"
RUNTIME = "distributed_runtime_contract.json"
INVALIDATION_RELATIVE = "invalidated_fb553a_shared_daemon_bundle.json"
INTERMEDIATE_INVALIDATION_RELATIVE = (
    "invalidated_intermediate_dedicated_daemon_bundles.json"
)
CONTRACT_RELATIVE = "contracts/mysql_tpch_dedicated_daemon_contract_v1.json"
UNIT_RELATIVE = "systemd/ra-docker-mysql-tpch-v1.service"
BUILDER_RELATIVE = "builder/build_mysql_tpch_dedicated_daemon_successor_v1.py"
SOCKET = "/run/ra-docker-mysql-tpch-v1.sock"
SOCKET_URI = "unix://" + SOCKET
DATA_ROOT = "/mnt/nvme0n1/anonymous/resource_adaptive_mysql/docker_ra_tpch_v1"
UNIT_NAME = "ra-docker-mysql-tpch-v1.service"
BOOTSTRAP_UNIT_PREFIX = "ra-mysql-tpch-bootstrap-"


class BuildError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2,
                   allow_nan=False) + "\n"
    ).encode("utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def load(path: Path) -> dict[str, Any]:
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise BuildError("absent or symlinked: " + str(path))
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise BuildError("JSON object required: " + str(path))
    return value


def base_module():
    if sha256(BASE_BUILDER) != BASE_BUILDER_SHA256:
        raise BuildError("sealed local base publisher identity differs")
    spec = importlib.util.spec_from_file_location("tpch_base_publisher", BASE_BUILDER)
    if spec is None or spec.loader is None:
        raise BuildError("base publisher is not importable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_predecessor() -> dict[str, Any]:
    manifest_path = PREDECESSOR / "bundle_manifest.json"
    manifest = load(manifest_path)
    unsigned = {key: value for key, value in manifest.items() if key != "bundle_id"}
    base = base_module()
    if (
        PREDECESSOR.name != PREDECESSOR_ID
        or sha256(manifest_path) != PREDECESSOR_MANIFEST_SHA256
        or manifest.get("bundle_id") != PREDECESSOR_ID
        or sha_bytes(canonical(unsigned)) != PREDECESSOR_ID
        or base.relative_files(PREDECESSOR) != manifest.get("files")
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("postgresql_mutation_prohibited") is not True
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
        or (manifest.get("readiness_evidence_contract") or {}).get(
            "formal_database_failure_evidence_before_cleanup"
        ) is not True
    ):
        raise BuildError("fb553a predecessor identity/authority differs")
    return manifest


def verify_inputs() -> None:
    invalid = load(INVALIDATION)
    intermediate = load(INTERMEDIATE_INVALIDATION)
    contract = load(CONTRACT)
    if (
        invalid.get("status") != "INVALIDATED_DO_NOT_RERUN"
        or invalid.get("bundle_id") != PREDECESSOR_ID
        or invalid.get("bundle_manifest_sha256") != PREDECESSOR_MANIFEST_SHA256
        or invalid.get("workload_started") is not False
        or any(invalid.get(key) != 0 for key in (
            "capture_credit", "qualification_credit", "r0_credit",
            "accepted_rounds_credit",
        ))
        or invalid.get("replay_or_rerun_authorized") is not False
        or intermediate.get("status") != "INVALIDATED_DO_NOT_RUN_OR_CREDIT"
        or [row.get("bundle_id") for row in intermediate.get("bundles", [])]
        != [
            "11ca6544d426985da5ff8e30d1c7ec5809d8fc8bb8c688f046b933262c201ad0",
            "7869775f278d038daad3c6b5c0f915626f9e682d0b5027da22ec319b9e29d167",
        ]
        or any(intermediate.get(key) != 0 for key in (
            "capture_credit", "qualification_credit", "r0_credit",
            "accepted_rounds_credit",
        ))
        or intermediate.get("replay_or_rerun_authorized") is not False
        or contract.get("daemon_socket") != SOCKET
        or contract.get("daemon_data_root") != DATA_ROOT
        or contract.get("daemon_unit") != UNIT_NAME
        or contract.get("docker_root_device_major_minor") != "259:1"
        or contract.get("graph_driver") != "overlay2"
        or contract.get("bridge") != "none"
        or contract.get("iptables") is not False
        or contract.get("database_network_mode") != "host"
        or contract.get("client_network_mode") != "host"
        or contract.get("mysqld_port") != 65103
        or contract.get("postgresql_mutation_prohibited") is not True
    ):
        raise BuildError("dedicated daemon input contract differs")
    unit = UNIT.read_text(encoding="utf-8")
    for fragment in (
        "--host=" + SOCKET_URI,
        "--data-root=" + DATA_ROOT,
        "--storage-driver=overlay2",
        "--bridge=none",
        "--iptables=false",
        "--ip-forward=false",
        "--ip-masq=false",
        "--userland-proxy=false",
    ):
        if unit.count(fragment) != 1:
            raise BuildError("systemd unit differs: " + fragment)


def replace_once(source: str, old: str, new: str, label: str) -> str:
    if source.count(old) != 1:
        raise BuildError("patch point differs: " + label)
    return source.replace(old, new, 1)


DOCKER_CONSTANTS = '''DOCKER_SOCKET = "/run/ra-docker-mysql-tpch-v1.sock"
DOCKER_SOCKET_URI = "unix:///run/ra-docker-mysql-tpch-v1.sock"
DOCKER_CLI = ("docker", "--host", DOCKER_SOCKET_URI)
DOCKER_DATA_ROOT = "/mnt/nvme0n1/anonymous/resource_adaptive_mysql/docker_ra_tpch_v1"
DOCKER_UNIT = "ra-docker-mysql-tpch-v1.service"
DOCKER_UNIT_FRAGMENT = "/etc/systemd/system/ra-docker-mysql-tpch-v1.service"
DOCKER_GRAPH_DRIVER = "overlay2"
DOCKER_ROOT_MAJOR_MINOR = "259:1"
'''


AUTHORITY_DAEMON_HELPER = r'''
def _dedicated_daemon_readback(
    spec: Mapping[str, Any],
    *,
    require_empty: bool = False,
    expected_containers: set[str] | None = None,
) -> dict[str, Any]:
    contract = spec.get("dedicated_daemon") or {}
    if contract != {
        "socket": DOCKER_SOCKET,
        "socket_uri": DOCKER_SOCKET_URI,
        "data_root": DOCKER_DATA_ROOT,
        "unit": DOCKER_UNIT,
        "unit_fragment": DOCKER_UNIT_FRAGMENT,
        "device": DEVICE,
        "device_major_minor": DOCKER_ROOT_MAJOR_MINOR,
        "graph_driver": DOCKER_GRAPH_DRIVER,
        "cgroup_driver": "cgroupfs",
        "bridge": "none",
        "iptables": False,
        "database_network_mode": "host",
        "client_network_mode": "host",
        "mysqld_port": MYSQL_PORT,
        "postgresql_prohibited": True,
    }:
        raise AuthorityError("dedicated daemon spec differs")
    shown = _run(
        [
            "systemctl", "show", DOCKER_UNIT,
            "--property=ActiveState", "--property=SubState",
            "--property=MainPID", "--property=FragmentPath",
            "--property=ExecStart",
        ],
        timeout=30,
    ).stdout
    properties = {}
    for line in shown.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            properties[key] = value
    required_exec = (
        "--host=" + DOCKER_SOCKET_URI,
        "--data-root=" + DOCKER_DATA_ROOT,
        "--storage-driver=overlay2",
        "--bridge=none",
        "--iptables=false",
        "--ip-forward=false",
        "--ip-masq=false",
        "--userland-proxy=false",
    )
    main_pid = int(properties.get("MainPID") or 0)
    if (
        properties.get("ActiveState") != "active"
        or properties.get("SubState") != "running"
        or properties.get("FragmentPath") != DOCKER_UNIT_FRAGMENT
        or main_pid <= 1
        or any(fragment not in properties.get("ExecStart", "")
               for fragment in required_exec)
    ):
        raise AuthorityError("dedicated daemon systemd binding differs")
    socket_state = os.stat(DOCKER_SOCKET, follow_symlinks=False)
    if not stat.S_ISSOCK(socket_state.st_mode):
        raise AuthorityError("dedicated daemon socket differs")
    socket_identity = "socket:[%d]" % socket_state.st_ino
    try:
        fd_targets = {
            os.readlink(path)
            for path in (Path("/proc") / str(main_pid) / "fd").iterdir()
        }
    except (OSError, PermissionError) as exc:
        raise AuthorityError("dedicated daemon socket owner is unreadable") from exc
    if socket_identity not in fd_targets:
        raise AuthorityError("dedicated daemon MainPID does not own socket")
    info_completed = _run(
        [*DOCKER_CLI, "info", "--format", "{{json .}}"], timeout=30
    )
    try:
        info = json.loads(info_completed.stdout)
    except json.JSONDecodeError as exc:
        raise AuthorityError("dedicated daemon info JSON differs") from exc
    data_state = os.stat(DOCKER_DATA_ROOT)
    data_major_minor = "%d:%d" % (
        os.major(data_state.st_dev), os.minor(data_state.st_dev)
    )
    mounted = _run(
        ["findmnt", "-n", "-o", "SOURCE,FSTYPE,TARGET", "--target",
         DOCKER_DATA_ROOT],
        timeout=30,
    ).stdout.strip().split()
    if (
        not isinstance(info, dict)
        or info.get("DockerRootDir") != DOCKER_DATA_ROOT
        or info.get("Driver") != DOCKER_GRAPH_DRIVER
        or info.get("CgroupDriver") != "cgroupfs"
        or data_major_minor != DOCKER_ROOT_MAJOR_MINOR
        or len(mounted) != 3
        or mounted[0] != DEVICE
        or mounted[2] != MOUNT
    ):
        raise AuthorityError("dedicated daemon root/driver/findmnt differs")
    identifiers = _run(
        [*DOCKER_CLI, "ps", "-a", "--no-trunc", "--quiet"],
        timeout=30,
        check=False,
    ).stdout.split()
    rows = []
    if identifiers:
        completed = _run(
            [*DOCKER_CLI, "inspect", *identifiers], timeout=30
        )
        try:
            rows = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise AuthorityError("dedicated daemon inventory differs") from exc
    if not isinstance(rows, list):
        raise AuthorityError("dedicated daemon inventory differs")
    names = {str(row.get("Name") or "").lstrip("/") for row in rows}
    if require_empty and rows:
        raise AuthorityError("dedicated daemon is not empty before attempt")
    if expected_containers is not None and names != expected_containers:
        raise AuthorityError("dedicated daemon attempt container set differs")
    for row in rows:
        config = row.get("Config") or {}
        labels = config.get("Labels") or {}
        searchable = " ".join((
            str(row.get("Name") or ""), str(config.get("Image") or ""),
            json.dumps(labels, sort_keys=True),
        )).lower()
        if (
            re.search(r"postgres(?:ql)?|(?:^|[^a-z])ra_pg(?:[^a-z]|$)",
                      searchable)
            or labels.get("ra.owner") != "mysql_algorithm_c_tpch_v2"
        ):
            raise AuthorityError("dedicated daemon contains non-TPC-H/PG state")
    return {
        "schema_version": "mysql_tpch_dedicated_daemon_readback_v1",
        "status": "PASS_DEDICATED_DAEMON_EXACT_NON_PG",
        "unit": DOCKER_UNIT,
        "unit_fragment": DOCKER_UNIT_FRAGMENT,
        "main_pid": main_pid,
        "socket": DOCKER_SOCKET,
        "socket_uri": DOCKER_SOCKET_URI,
        "socket_inode": socket_state.st_ino,
        "socket_owned_by_main_pid": True,
        "docker_root_dir": info["DockerRootDir"],
        "docker_root_device": DEVICE,
        "docker_root_device_major_minor": data_major_minor,
        "findmnt": {"source": mounted[0], "fstype": mounted[1],
                    "target": mounted[2]},
        "graph_driver": info["Driver"],
        "cgroup_driver": info["CgroupDriver"],
        "bridge": "none",
        "iptables": False,
        "container_names": sorted(names),
        "container_count": len(rows),
        "postgresql_container_or_label_absent": True,
        "all_docker_cli_explicit_host": True,
        "formal_scope": "mysql_only",
        "postgresql_mutation_prohibited": True,
        "accepted_round_authorized": False,
    }
'''


COLLECTOR_DAEMON_HELPER = r'''
def dedicated_daemon_readback():
    shown = run(
        [
            "systemctl", "show", DOCKER_UNIT,
            "--property=ActiveState", "--property=SubState",
            "--property=MainPID", "--property=FragmentPath",
            "--property=ExecStart",
        ],
        timeout=30,
        label="dedicated daemon systemd readback",
    )
    properties = {}
    for line in shown.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            properties[key] = value
    required = (
        "--host=" + DOCKER_SOCKET_URI,
        "--data-root=" + DOCKER_DATA_ROOT,
        "--storage-driver=overlay2", "--bridge=none", "--iptables=false",
        "--ip-forward=false", "--ip-masq=false", "--userland-proxy=false",
    )
    main_pid = int(properties.get("MainPID") or 0)
    socket_state = os.stat(DOCKER_SOCKET, follow_symlinks=False)
    if (
        properties.get("ActiveState") != "active"
        or properties.get("SubState") != "running"
        or properties.get("FragmentPath") != DOCKER_UNIT_FRAGMENT
        or main_pid <= 1
        or not stat.S_ISSOCK(socket_state.st_mode)
        or any(item not in properties.get("ExecStart", "") for item in required)
    ):
        raise QualificationError("dedicated daemon systemd/socket differs")
    raw_info = run(
            [*DOCKER_CLI, "info", "--format", "{{json .}}"],
            timeout=30,
            label="dedicated daemon info readback",
    )
    try:
        info = json.loads(raw_info)
    except ValueError as exc:
        raise QualificationError("dedicated daemon info JSON differs")
    data_state = os.stat(DOCKER_DATA_ROOT)
    mounted = run(
        ["findmnt", "-n", "-o", "SOURCE,FSTYPE,TARGET", "--target",
         DOCKER_DATA_ROOT],
        timeout=30,
        label="dedicated daemon root findmnt",
    ).strip().split()
    if (
        info.get("DockerRootDir") != DOCKER_DATA_ROOT
        or info.get("Driver") != DOCKER_GRAPH_DRIVER
        or info.get("CgroupDriver") != "cgroupfs"
        or "%d:%d" % (os.major(data_state.st_dev), os.minor(data_state.st_dev))
        != DOCKER_ROOT_MAJOR_MINOR
        or len(mounted) != 3
        or mounted[0] != "/dev/nvme0n1"
        or mounted[2] != "/mnt/nvme0n1"
    ):
        raise QualificationError("dedicated daemon root/driver/findmnt differs")
    return {
        "schema_version": "mysql_tpch_dedicated_daemon_readback_v1",
        "status": "PASS_DEDICATED_DAEMON_EXACT_NON_PG",
        "unit": DOCKER_UNIT,
        "main_pid": main_pid,
        "socket": DOCKER_SOCKET,
        "socket_uri": DOCKER_SOCKET_URI,
        "docker_root_dir": info["DockerRootDir"],
        "docker_root_device_major_minor": DOCKER_ROOT_MAJOR_MINOR,
        "graph_driver": info["Driver"],
        "cgroup_driver": info["CgroupDriver"],
        "bridge": "none",
        "iptables": False,
        "postgresql_mutation_prohibited": True,
        "accepted_round_authorized": False,
    }
'''


AUTHORITY_DIRECT_HOST_HELPER = r'''
def _read_main_agent_token_file(path: Path) -> str:
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise AuthorityError("main-agent token file must be regular")
    state = path.stat()
    if stat.S_IMODE(state.st_mode) != 0o600 or state.st_uid != os.geteuid():
        raise AuthorityError("main-agent token file ownership/mode differs")
    token = path.read_text(encoding="ascii").strip()
    if len(token) < 32 or any(character.isspace() for character in token):
        raise AuthorityError("main-agent token file content differs")
    return token


def _proc_starttime_ticks(pid: int) -> int:
    raw = (Path("/proc") / str(pid) / "stat").read_text(encoding="ascii")
    closing = raw.rfind(")")
    fields_after_comm = raw[closing + 2:].split() if closing > 0 else []
    if len(fields_after_comm) < 20:
        raise AuthorityError("bootstrap /proc stat is incomplete")
    value = int(fields_after_comm[19])
    if value <= 0:
        raise AuthorityError("bootstrap process startticks differ")
    return value


def _direct_host_bootstrap_readback(spec: Mapping[str, Any]) -> dict[str, Any]:
    expected_unit = (
        "ra-mysql-tpch-bootstrap-" + spec["attempt_token"] + ".service"
    )
    contract = spec.get("bootstrap_execution") or {}
    if contract != {
        "mode": "direct_host_systemd_transient_unit",
        "unit": expected_unit,
        "ppid": 1,
        "token_transport": "token_file_or_in_process_only",
        "docker_ancestry_prohibited": True,
    }:
        raise AuthorityError("direct-host bootstrap execution spec differs")
    pid = os.getpid()
    if os.getppid() != 1:
        raise AuthorityError("bootstrap must be direct-host PPID=1")
    shown = _run(
        [
            "systemctl", "show", expected_unit,
            "--property=Id", "--property=LoadState",
            "--property=ActiveState", "--property=SubState",
            "--property=MainPID", "--property=ControlGroup",
            "--property=FragmentPath", "--property=Transient",
        ],
        timeout=30,
    ).stdout
    properties = {}
    for line in shown.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            properties[key] = value
    control_group = properties.get("ControlGroup") or ""
    if (
        properties.get("Id") != expected_unit
        or properties.get("LoadState") != "loaded"
        or properties.get("ActiveState") != "active"
        or properties.get("SubState") not in ("running", "start")
        or int(properties.get("MainPID") or 0) != pid
        or properties.get("Transient") != "yes"
        or not control_group.startswith("/")
    ):
        raise AuthorityError("transient-unit/MainPID binding differs")
    cgroup_lines = Path("/proc/self/cgroup").read_text(
        encoding="ascii"
    ).splitlines()
    cgroup_paths = []
    for line in cgroup_lines:
        pieces = line.split(":", 2)
        if len(pieces) != 3 or not pieces[2].startswith("/"):
            raise AuthorityError("bootstrap cgroup readback differs")
        cgroup_paths.append(pieces[2])
    namespaces = {
        name: os.readlink("/proc/self/ns/" + name)
        for name in ("pid", "mnt", "net", "user", "ipc", "uts", "cgroup")
    }
    init_namespaces = {
        name: os.readlink("/proc/1/ns/" + name)
        for name in namespaces
    }
    ancestry_text = "\n".join(
        cgroup_lines
        + Path("/proc/1/cgroup").read_text(encoding="ascii").splitlines()
    ).lower()
    prohibited = ("docker", "kubepods", "containerd", "libpod", "podman")
    if (
        control_group not in cgroup_paths
        or namespaces != init_namespaces
        or any(marker in ancestry_text for marker in prohibited)
        or os.environ.get("container")
        or Path("/.dockerenv").exists()
    ):
        raise AuthorityError("Docker/container ancestry or namespace differs")
    return {
        "schema_version": "mysql_tpch_direct_host_bootstrap_identity_v1",
        "status": "PASS_DIRECT_HOST_TRANSIENT_UNIT_NON_DOCKER",
        "unit": expected_unit,
        "main_pid": pid,
        "ppid": 1,
        "process_starttime_ticks": _proc_starttime_ticks(pid),
        "control_group": control_group,
        "cgroup_paths": cgroup_paths,
        "namespaces": namespaces,
        "init_namespaces": init_namespaces,
        "transient": True,
        "fragment_path": properties.get("FragmentPath") or "",
        "token_transport": "token_file_or_in_process_only",
        "docker_ancestry": False,
    }
'''


COLLECTOR_TOKEN_HELPER = r'''
def read_main_agent_token_file(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise QualificationError("main-agent token file must be regular")
    state = path.stat()
    if stat.S_IMODE(state.st_mode) != 0o600 or state.st_uid != os.geteuid():
        raise QualificationError("main-agent token file ownership/mode differs")
    token = path.read_text(encoding="ascii").strip()
    if len(token) < 32 or any(character.isspace() for character in token):
        raise QualificationError("main-agent token file content differs")
    return token
'''


def dockerize(source: str, marker: str) -> str:
    count = source.count('"docker",')
    if count < 1:
        raise BuildError("no Docker argv found")
    source = source.replace('"docker",', "*DOCKER_CLI,")
    if '"docker",' in source:
        raise BuildError("implicit Docker argv remains")
    source = replace_once(
        source, marker, marker + "\n" + DOCKER_CONSTANTS,
        "Docker constants",
    )
    return source


def authority_payload() -> bytes:
    source = (PREDECESSOR / AUTHORITY).read_text(encoding="utf-8")
    source = dockerize(source, "MYSQL_PORT = 65103")
    source = replace_once(
        source, "\ndef _tmpfs_option_bytes(raw: str) -> int:\n",
        "\n" + AUTHORITY_DAEMON_HELPER + "\n" + AUTHORITY_DIRECT_HOST_HELPER
        + "\ndef _tmpfs_option_bytes(raw: str) -> int:\n",
        "daemon helper",
    )
    source = replace_once(
        source, '            "containers",\n            "mysql_binary",',
        '            "containers",\n            "dedicated_daemon",\n'
        '            "bootstrap_execution",\n            "mysql_binary",',
        "spec key",
    )
    source = replace_once(
        source,
        '''    if (
        spec["mysql_binary"] != "/usr/bin/mysql"
''',
        '''    dedicated = spec["dedicated_daemon"]
    if dedicated != {
        "socket": DOCKER_SOCKET,
        "socket_uri": DOCKER_SOCKET_URI,
        "data_root": DOCKER_DATA_ROOT,
        "unit": DOCKER_UNIT,
        "unit_fragment": DOCKER_UNIT_FRAGMENT,
        "device": DEVICE,
        "device_major_minor": DOCKER_ROOT_MAJOR_MINOR,
        "graph_driver": DOCKER_GRAPH_DRIVER,
        "cgroup_driver": "cgroupfs",
        "bridge": "none",
        "iptables": False,
        "database_network_mode": "host",
        "client_network_mode": "host",
        "mysqld_port": MYSQL_PORT,
        "postgresql_prohibited": True,
    }:
        raise AuthorityError("dedicated daemon spec differs")
    execution = spec["bootstrap_execution"]
    expected_bootstrap_unit = (
        "ra-mysql-tpch-bootstrap-" + attempt + ".service"
    )
    _exact_keys(
        execution,
        {
            "mode", "unit", "ppid", "token_transport",
            "docker_ancestry_prohibited",
        },
        "bootstrap execution",
    )
    if execution != {
        "mode": "direct_host_systemd_transient_unit",
        "unit": expected_bootstrap_unit,
        "ppid": 1,
        "token_transport": "token_file_or_in_process_only",
        "docker_ancestry_prohibited": True,
    }:
        raise AuthorityError("bootstrap direct-host execution binding differs")
    if (
        spec["mysql_binary"] != "/usr/bin/mysql"
''',
        "spec daemon contract",
    )
    source = replace_once(
        source,
        '''    db_argv = [
        *DOCKER_CLI, "run", "-d", "--name", spec["containers"]["database"],
        *labels,
''',
        '''    db_argv = [
        *DOCKER_CLI, "run", "-d", "--name", spec["containers"]["database"],
        *labels,
        "--network", "host",
''',
        "database host network",
    )
    source = replace_once(
        source,
        '''        "--device-write-bps", f"{DEVICE}:{DB_ENVELOPE['io_write_bps']}",
        "--publish", f"127.0.0.1:{MYSQL_PORT}:3306",
        "--mount",''',
        '''        "--device-write-bps", f"{DEVICE}:{DB_ENVELOPE['io_write_bps']}",
        "--mount",''',
        "remove port publish",
    )
    source = replace_once(
        source,
        '''        spec["mysql_image"]["image_id"], "mysqld",
        "--bind-address=0.0.0.0",
''',
        '''        spec["mysql_image"]["image_id"], "mysqld",
        "--bind-address=127.0.0.1", f"--port={MYSQL_PORT}",
''',
        "mysqld host port",
    )
    source = replace_once(
        source,
        '''    ports = ((db_host.get("PortBindings") or {}).get("3306/tcp") or [])
    declared_read =''',
        '''    port_bindings = db_host.get("PortBindings") or {}
    db_command = (db_row.get("Config") or {}).get("Cmd") or []
    declared_read =''',
        "database network readback variables",
    )
    source = replace_once(
        source,
        '''        or {
            (row.get("HostIp"), int(row.get("HostPort") or 0))
            for row in ports
        }
        != {("127.0.0.1", MYSQL_PORT)}
        or not any(
''',
        '''        or db_host.get("NetworkMode") != "host"
        or port_bindings != {}
        or "--bind-address=127.0.0.1" not in db_command
        or f"--port={MYSQL_PORT}" not in db_command
        or not any(
''',
        "database network readback",
    )
    source = replace_once(
        source,
        '''    bundle = Path(spec["bundle"]["path"])
    manifest = load_object(bundle / "bundle_manifest.json", "bundle manifest")
''',
        '''    bootstrap_execution_start = _direct_host_bootstrap_readback(spec)
    daemon_preflight = _dedicated_daemon_readback(
        spec, require_empty=True
    )
    bundle = Path(spec["bundle"]["path"])
    manifest = load_object(bundle / "bundle_manifest.json", "bundle manifest")
''',
        "pre-mutation daemon gate",
    )
    source = replace_once(
        source,
        '''    live_path = attempt_root / "live_readback.json"
    _run(plan["collect_only"]["argv"], timeout=900)
''',
        '''    daemon_live = _dedicated_daemon_readback(
        spec,
        expected_containers={
            spec["containers"]["database"],
            spec["containers"]["client"],
        },
    )
    live_path = attempt_root / "live_readback.json"
    _run(plan["collect_only"]["argv"], timeout=900)
''',
        "live daemon gate",
    )
    source = replace_once(
        source,
        '''        "template_owner_inventory": template_owner_inventory,
        "start_authority": start_authority,
''',
        '''        "template_owner_inventory": template_owner_inventory,
        "bootstrap_execution": {
            "start": bootstrap_execution_start,
            "end": bootstrap_execution_end,
        },
        "dedicated_daemon_preflight": daemon_preflight,
        "dedicated_daemon": daemon_live,
        "start_authority": start_authority,
''',
        "bootstrap daemon evidence",
    )
    source = replace_once(
        source,
        '''    live = load_object(live_path, "collect-only readback")
    evidence = {
''',
        '''    live = load_object(live_path, "collect-only readback")
    bootstrap_execution_end = _direct_host_bootstrap_readback(spec)
    if bootstrap_execution_end != bootstrap_execution_start:
        raise AuthorityError(
            "bootstrap transient-unit/process identity changed in-flight"
        )
    evidence = {
''',
        "direct-host bootstrap end gate",
    )
    source = replace_once(
        source,
        '''        or evidence.get("spec_sha256") != sha_bytes(canonical(spec))
        or evidence.get("qualification_only") is not True
''',
        '''        or evidence.get("spec_sha256") != sha_bytes(canonical(spec))
        or (evidence.get("bootstrap_execution") or {}).get("start")
        != (evidence.get("bootstrap_execution") or {}).get("end")
        or (
            (evidence.get("bootstrap_execution") or {}).get("start") or {}
        ).get("status")
        != "PASS_DIRECT_HOST_TRANSIENT_UNIT_NON_DOCKER"
        or (
            (evidence.get("bootstrap_execution") or {}).get("start") or {}
        ).get("unit")
        != spec["bootstrap_execution"]["unit"]
        or (
            (evidence.get("bootstrap_execution") or {}).get("start") or {}
        ).get("ppid") != 1
        or (
            (evidence.get("bootstrap_execution") or {}).get("start") or {}
        ).get("docker_ancestry") is not False
        or (evidence.get("dedicated_daemon") or {}).get("status")
        != "PASS_DEDICATED_DAEMON_EXACT_NON_PG"
        or (evidence.get("dedicated_daemon") or {}).get("socket")
        != DOCKER_SOCKET
        or (evidence.get("dedicated_daemon") or {}).get("docker_root_dir")
        != DOCKER_DATA_ROOT
        or (evidence.get("dedicated_daemon") or {}).get("graph_driver")
        != DOCKER_GRAPH_DRIVER
        or evidence.get("qualification_only") is not True
''',
        "bootstrap daemon validation",
    )
    source = replace_once(
        source,
        '''    parser.add_argument("--main-agent-token")
''',
        '''    parser.add_argument("--main-agent-token-file", type=Path)
''',
        "authority token-file parser",
    )
    source = replace_once(
        source,
        '''    elif args.execute_bootstrap:
        if args.output is None or args.main_agent_token is None:
            raise AuthorityError("execute-bootstrap requires output and token")
        value = execute_bootstrap(spec, args.output, args.main_agent_token)
''',
        '''    elif args.execute_bootstrap:
        if args.output is None or args.main_agent_token_file is None:
            raise AuthorityError(
                "execute-bootstrap requires output and token file"
            )
        value = execute_bootstrap(
            spec,
            args.output,
            _read_main_agent_token_file(args.main_agent_token_file),
        )
''',
        "authority token-file execution",
    )
    return source.encode("utf-8")


def collector_payload() -> bytes:
    source = (PREDECESSOR / COLLECTOR).read_text(encoding="utf-8")
    source = dockerize(source, "QUALIFICATION_SECONDS = 180")
    source = replace_once(
        source, "\ndef port_probe(port):\n",
        "\n" + COLLECTOR_DAEMON_HELPER + "\ndef port_probe(port):\n",
        "collector daemon helper",
    )
    source = replace_once(
        source, "\ndef bundle_from_script():\n",
        "\n" + COLLECTOR_TOKEN_HELPER + "\ndef bundle_from_script():\n",
        "collector token-file helper",
    )
    source = replace_once(
        source,
        '    parser.add_argument("--main-agent-token")\n',
        '    parser.add_argument("--main-agent-token-file", type=Path)\n',
        "collector token-file parser",
    )
    source = source.replace(
        "args.main_agent_token", "args.main_agent_token_file"
    )
    source = replace_once(
        source,
        '''        mode = (
            "qualification_solo"
''',
        '''        main_agent_token = read_main_agent_token_file(
            args.main_agent_token_file
        )
        mode = (
            "qualification_solo"
''',
        "collector token-file read",
    )
    source = replace_once(
        source,
        '''            args.main_agent_authority,
            args.main_agent_token_file,
            mysql_client_container=args.mysql_client_container,
''',
        '''            args.main_agent_authority,
            main_agent_token,
            mysql_client_container=args.mysql_client_container,
''',
        "collector in-process token",
    )
    source = replace_once(
        source,
        '''    database_row = inspect_container(container)
    effective_blkio_authority =''',
        '''    daemon_readback = dedicated_daemon_readback()
    database_row = inspect_container(container)
    effective_blkio_authority =''',
        "collector daemon call",
    )
    source = replace_once(
        source,
        '''    status = process_status(pid)
    if expand_cpuset(status.get("Cpus_allowed_list", "")) != expand_cpuset(
''',
        '''    command = (container_row.get("Config") or {}).get("Cmd") or []
    if (
        host_config.get("NetworkMode") != "host"
        or (host_config.get("PortBindings") or {}) != {}
        or "--bind-address=127.0.0.1" not in command
        or "--port=65103" not in command
    ):
        raise QualificationError("database host-network/port binding differs")
    status = process_status(pid)
    if expand_cpuset(status.get("Cpus_allowed_list", "")) != expand_cpuset(
''',
        "collector DB network gate",
    )
    source = replace_once(
        source,
        '''        "toolchain": toolchain,
        "capture_authorized": False,
''',
        '''        "toolchain": toolchain,
        "dedicated_daemon": daemon_readback,
        "capture_authorized": False,
''',
        "collector daemon evidence",
    )
    return source.encode("utf-8")


def simple_docker_payload(relative: str, marker: str) -> bytes:
    source = (PREDECESSOR / relative).read_text(encoding="utf-8")
    return dockerize(source, marker).encode("utf-8")


def fresh_r0_payload() -> bytes:
    source = simple_docker_payload(FRESH_R0, 'HEX64 = re.compile(r"[0-9a-f]{64}$")').decode()
    source = replace_once(
        source,
        '''        or bootstrap.get("bundle_id") != bundle.name
        or not isinstance(identity, dict)
''',
        '''        or bootstrap.get("bundle_id") != bundle.name
        or (bootstrap.get("dedicated_daemon") or {}).get("status")
        != "PASS_DEDICATED_DAEMON_EXACT_NON_PG"
        or (bootstrap.get("dedicated_daemon") or {}).get("socket")
        != DOCKER_SOCKET
        or (bootstrap.get("dedicated_daemon") or {}).get("docker_root_dir")
        != DOCKER_DATA_ROOT
        or not isinstance(identity, dict)
''',
        "fresh R0 daemon binding",
    )
    source = replace_once(
        source,
        '''    token = read_token(args.main_agent_token_file)
    if (
''',
        '''    read_token(args.main_agent_token_file)
    if (
''',
        "fresh R0 token-file verification",
    )
    source = replace_once(
        source,
        '''            "--main-agent-token",
            token,
''',
        '''            "--main-agent-token-file",
            str(args.main_agent_token_file),
''',
        "fresh R0 token transport",
    )
    return source.encode("utf-8")


def runtime_payload() -> bytes:
    value = load(PREDECESSOR / RUNTIME)
    value["dedicated_daemon"] = load(CONTRACT)
    value["database_network_mode"] = "host"
    value["database_mysqld_port"] = 65103
    value["bootstrap_execution"] = {
        "mode": "direct_host_systemd_transient_unit",
        "unit_template": BOOTSTRAP_UNIT_PREFIX + "<attempt_token>.service",
        "ppid": 1,
        "bind_main_pid_startticks_namespaces_cgroup": True,
        "docker_ancestry_prohibited": True,
        "token_transport": "token_file_or_in_process_only",
        "token_in_os_argv_prohibited": True,
    }
    return canonical(value)


def payloads(predecessor: dict[str, Any]) -> dict[str, bytes]:
    result = {
        relative: (PREDECESSOR / relative).read_bytes()
        for relative in sorted(predecessor["files"])
    }
    result[AUTHORITY] = authority_payload()
    result[COLLECTOR] = collector_payload()
    result[BLKIO] = simple_docker_payload(BLKIO, 'IO_BPS = 100_663_296')
    result[FRESH_R0] = fresh_r0_payload()
    result[RUNTIME] = runtime_payload()
    result[INVALIDATION_RELATIVE] = INVALIDATION.read_bytes()
    result[INTERMEDIATE_INVALIDATION_RELATIVE] = (
        INTERMEDIATE_INVALIDATION.read_bytes()
    )
    result[CONTRACT_RELATIVE] = CONTRACT.read_bytes()
    result[UNIT_RELATIVE] = UNIT.read_bytes()
    result[BUILDER_RELATIVE] = HERE.read_bytes()
    return result


def build() -> dict[str, Any]:
    predecessor = verify_predecessor()
    verify_inputs()
    content = payloads(predecessor)
    files = {name: sha_bytes(data) for name, data in sorted(content.items())}
    identity = {
        key: value for key, value in predecessor.items()
        if key not in ("bundle_id", "files")
    }
    components = dict(identity.get("component_hashes") or {})
    components.update({
        "bootstrap_authority": files[AUTHORITY],
        "collector": files[COLLECTOR],
        "blkio_v1_helper": files[BLKIO],
        "fresh_r0_runner": files[FRESH_R0],
    })
    identity.update({
        "predecessor_bundle_id": PREDECESSOR_ID,
        "predecessor_manifest_sha256": PREDECESSOR_MANIFEST_SHA256,
        "builder_sha256": sha256(HERE),
        "component_hashes": components,
        "distributed_runtime_contract_sha256": files[RUNTIME],
        "invalidated_fb553a_shared_daemon_bundle_sha256": sha256(INVALIDATION),
        "invalidated_intermediate_dedicated_daemon_bundles_sha256": sha256(
            INTERMEDIATE_INVALIDATION
        ),
        "dedicated_daemon_contract_sha256": sha256(CONTRACT),
        "dedicated_daemon_systemd_unit_sha256": sha256(UNIT),
        "dedicated_daemon_contract": load(CONTRACT),
        "all_runtime_docker_cli_calls_explicit_host": True,
        "database_and_client_network_mode": "host",
        "database_mysqld_port": 65103,
        "bootstrap_execution_contract": {
            "mode": "direct_host_systemd_transient_unit",
            "unit_template": BOOTSTRAP_UNIT_PREFIX + "<attempt_token>.service",
            "ppid": 1,
            "bind_main_pid_startticks_namespaces_cgroup": True,
            "docker_ancestry_prohibited": True,
            "token_transport": "token_file_or_in_process_only",
            "token_in_os_argv_prohibited": True,
        },
        "fresh_r0_envelope_preserved": {
            "cpu_quota_cores": 4,
            "io_read_bps": 100663296,
            "io_write_bps": 100663296,
            "profile_intervals": {"cpu": [0.30, 0.50],
                                  "memory": [0.85, 0.95],
                                  "io": [0.30, 0.50]},
        },
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "offline_build_only": True,
        "remote_actions": 0,
        "files": files,
    })
    bundle_id = sha_bytes(canonical(identity))
    manifest = dict(identity)
    manifest["bundle_id"] = bundle_id
    base = base_module()
    base.BUNDLE_ROOT = BUNDLE_ROOT
    bundle = base.publish(content, manifest)
    return {
        "schema_version": "mysql_tpch_dedicated_daemon_successor_build_result_v1",
        "status": "PASS_IMMUTABLE_SUCCESSOR_PUBLISHED",
        "bundle_id": bundle_id,
        "bundle": str(bundle),
        "bundle_manifest_sha256": sha256(bundle / "bundle_manifest.json"),
        "component_hashes": components,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "remote_actions": 0,
    }


if __name__ == "__main__":
    try:
        print(json.dumps(build(), indent=2, sort_keys=True))
    except (BuildError, OSError, ValueError) as exc:
        sys.stderr.write("NOT_READY: %s\n" % exc)
        raise SystemExit(2)
