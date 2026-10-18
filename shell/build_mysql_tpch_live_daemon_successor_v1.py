#!/usr/bin/env python3
"""Build the immutable TPC-H successor bound to the live transient daemon."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve()
PUBLISHER = ROOT / "shell/build_mysql_tpch_namespace_compat_successor_v1.py"
PUBLISHER_SHA256 = (
    "2edba3b8f24e2bcc49fa366a94ffd2c68d4f37af6280b71deebcb18ac906cf91"
)
PREDECESSOR_ID = (
    "9aa9c6e08d3889d796f56861c3d99e9b25ff1481df76c0ecfbdc64176f80f4fb"
)
PREDECESSOR_MANIFEST_SHA256 = (
    "88632e6cf1294b9c114a3ce4f75ef14f8d39dd53b1f3dba3a63ec758066e9fe5"
)
PREDECESSOR = (
    ROOT / "artifacts/mysql_tpch_namespace_compat_successor_v1"
    / PREDECESSOR_ID
)
BUNDLE_ROOT = ROOT / "artifacts/mysql_tpch_live_daemon_successor_v1"
AUTHORITY = "mysql_tpch_distributed_v2_authority.py"
COLLECTOR = "qualification/mysql_distributed_live_readback_qualification_v1.py"
RUNTIME = "distributed_runtime_contract.json"
CONTRACT_RELATIVE = "contracts/mysql_tpch_dedicated_daemon_contract_v1.json"
STATIC_UNIT_RELATIVE = "systemd/ra-docker-mysql-tpch-v1.service"
INVALIDATION = (
    ROOT / "preflight/mysql_tpch_9aa_static_fragment_invalidation_20260731.json"
)
CONTRACT = ROOT / "preflight/mysql_tpch_live_transient_daemon_contract_v1.json"
SYSTEMCTL_FIXTURE = (
    ROOT / "preflight/node04_tpch_live_daemon_systemctl_fixture_v1.json"
)
PROC_FIXTURE = ROOT / "preflight/node04_tpch_live_daemon_proc_fixture_v1.json"
INVALIDATION_RELATIVE = "invalidated_9aa_static_fragment_bundle.json"
SYSTEMCTL_FIXTURE_RELATIVE = (
    "fixtures/node04_tpch_live_daemon_systemctl_fixture_v1.json"
)
PROC_FIXTURE_RELATIVE = "fixtures/node04_tpch_live_daemon_proc_fixture_v1.json"
BUILDER_RELATIVE = "builder/build_mysql_tpch_live_daemon_successor_v1.py"


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


def publisher():
    if sha256(PUBLISHER) != PUBLISHER_SHA256:
        raise BuildError("sealed publisher identity differs")
    spec = importlib.util.spec_from_file_location("tpch_live_daemon_publisher", PUBLISHER)
    if spec is None or spec.loader is None:
        raise BuildError("publisher is not importable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def atomic_publisher():
    return publisher().atomic_publisher()


def verify_predecessor() -> dict[str, Any]:
    manifest_path = PREDECESSOR / "bundle_manifest.json"
    manifest = load(manifest_path)
    unsigned = {key: value for key, value in manifest.items() if key != "bundle_id"}
    prohibited = [
        path for path in PREDECESSOR.rglob("*")
        if path.name == "__pycache__" or path.suffix == ".pyc"
    ]
    if (
        sha256(manifest_path) != PREDECESSOR_MANIFEST_SHA256
        or manifest.get("bundle_id") != PREDECESSOR_ID
        or sha_bytes(canonical(unsigned)) != PREDECESSOR_ID
        or atomic_publisher().relative_files(PREDECESSOR) != manifest.get("files")
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
        or prohibited
    ):
        raise BuildError("9aa predecessor identity/closure differs")
    return manifest


def parse_properties(stdout: str) -> dict[str, str]:
    result = {}
    for line in stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            result[key] = value
    return result


def validate_fixtures() -> None:
    systemctl = load(SYSTEMCTL_FIXTURE)
    proc = load(PROC_FIXTURE)
    contract = load(CONTRACT)
    system_stdout = systemctl.get("stdout")
    proc_stdout = proc.get("stdout")
    projection = parse_properties(str(system_stdout))
    exact = systemctl.get("exact_projection")
    main_pid_cmdline = (
        b"\0".join(
            item.encode("utf-8") for item in contract.get("main_pid_argv", [])
        )
        + b"\0"
    )
    expected_exec = (
        "argv[]=" + " ".join(contract.get("exec_argv", [])) + " ;"
    )
    if (
        hashlib.sha256(str(system_stdout).encode()).hexdigest()
        != "121543dbd5ab15833b5c29f895c177fc9ef9808097108c7329f94e3b541efe8c"
        or hashlib.sha256(str(proc_stdout).encode()).hexdigest()
        != "740e9638b4a701dfbff92d029b05801b4c1d7be33ab7efb9765645fba337f513"
        or any(projection.get(key) != value for key, value in exact.items())
        or contract.get("unit_fragment")
        != "/run/systemd/system/ra-docker-mysql-tpch-v1.service"
        or contract.get("transient") is not True
        or contract.get("main_pid") != 97636
        or contract.get("main_pid_ppid") != 1
        or contract.get("main_pid_cpus_allowed_list") != "34-35,70-71"
        or contract.get("main_pid_starttime_ticks") != 233390522
        or contract.get("main_pid_cmdline_sha256")
        != "602181ebadf1ab7b20492994b33ef4f6031c29d5afdca4a3154c5e555bd71b29"
        or hashlib.sha256(main_pid_cmdline).hexdigest()
        != contract.get("main_pid_cmdline_sha256")
        or expected_exec not in projection.get("ExecStart", "")
        or contract.get("cpu_shares") != 256
        or contract.get("memory_limit_bytes") != 2147483648
        or contract.get("tasks_max") != 512
        or contract.get("cgroup_v1_lines") != proc.get("cgroup_v1_lines")
        or contract.get("mandatory_namespace_identities")
        != proc.get("mandatory_namespaces")
        or contract.get("optional_cgroup_namespace")
        != proc.get("optional_cgroup_namespace")
    ):
        raise BuildError("live daemon fixture/contract differs")


def replace_once(source: str, old: str, new: str, label: str) -> str:
    if source.count(old) != 1:
        raise BuildError("patch point differs: " + label)
    return source.replace(old, new, 1)


OLD_CONTRACT_BODY = '''{
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
    }'''


AUTHORITY_PROC_HELPER = r'''
def _live_daemon_process_readback(
    pid: int, contract: Mapping[str, Any]
) -> dict[str, Any]:
    try:
        status_lines = (
            Path("/proc") / str(pid) / "status"
        ).read_text(encoding="ascii").splitlines()
        cmdline = (Path("/proc") / str(pid) / "cmdline").read_bytes()
        cgroup_lines = (
            Path("/proc") / str(pid) / "cgroup"
        ).read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeError) as exc:
        raise AuthorityError("live daemon process readback failed") from exc
    status = {}
    for line in status_lines:
        if ":" in line:
            key, value = line.split(":", 1)
            status[key] = value.strip()
    argv = [
        item.decode("utf-8")
        for item in cmdline.rstrip(b"\0").split(b"\0")
        if item
    ]
    namespaces = {}
    try:
        for name in contract["mandatory_namespaces"]:
            namespaces[name] = os.readlink(
                "/proc/%d/ns/%s" % (pid, name)
            )
    except OSError as exc:
        raise AuthorityError(
            "live daemon mandatory namespace readback failed"
        ) from exc
    cgroup_ns_path = "/proc/%d/ns/cgroup" % pid
    cgroup_ns_supported = os.path.lexists(cgroup_ns_path)
    if (
        int(status.get("PPid") or 0) != contract["main_pid_ppid"]
        or status.get("Cpus_allowed_list")
        != contract["main_pid_cpus_allowed_list"]
        or _proc_starttime_ticks(pid)
        != contract["main_pid_starttime_ticks"]
        or hashlib.sha256(cmdline).hexdigest()
        != contract["main_pid_cmdline_sha256"]
        or argv != contract["main_pid_argv"]
        or cgroup_lines != contract["cgroup_v1_lines"]
        or namespaces != contract["mandatory_namespace_identities"]
        or cgroup_ns_supported
        != contract["optional_cgroup_namespace"]["supported"]
        or (not cgroup_ns_supported)
        != contract["optional_cgroup_namespace"]["path_absent"]
    ):
        raise AuthorityError("live daemon process identity differs")
    return {
        "ppid": int(status["PPid"]),
        "cpus_allowed_list": status["Cpus_allowed_list"],
        "starttime_ticks": contract["main_pid_starttime_ticks"],
        "cmdline_sha256": hashlib.sha256(cmdline).hexdigest(),
        "argv": argv,
        "cgroup_v1_lines": cgroup_lines,
        "mandatory_namespaces": namespaces,
        "optional_cgroup_namespace": {
            "supported": cgroup_ns_supported,
            "path_absent": not cgroup_ns_supported,
        },
    }
'''


COLLECTOR_PROC_HELPER = (
    AUTHORITY_PROC_HELPER
    .replace("Mapping[str, Any]", "dict")
    .replace("AuthorityError", "QualificationError")
    .replace("_proc_starttime_ticks", "process_start_ticks")
)


def patch_contracts(source: str, variable: str, error: str) -> str:
    old = f"    if {variable} != " + OLD_CONTRACT_BODY + ":\n"
    new = (
        f"    if {variable} != "
        + repr(load(CONTRACT))
        + ":\n"
    )
    return replace_once(source, old, new, error + " contract")


def authority_payload() -> bytes:
    source = (PREDECESSOR / AUTHORITY).read_text(encoding="utf-8")
    source = replace_once(
        source,
        'DOCKER_UNIT_FRAGMENT = "/etc/systemd/system/'
        'ra-docker-mysql-tpch-v1.service"',
        'DOCKER_UNIT_FRAGMENT = "/run/systemd/system/'
        'ra-docker-mysql-tpch-v1.service"',
        "authority transient fragment",
    )
    source = patch_contracts(source, "dedicated", "spec")
    source = patch_contracts(source, "contract", "readback")
    source = replace_once(
        source,
        "\ndef _dedicated_daemon_readback(\n",
        "\n" + AUTHORITY_PROC_HELPER + "\ndef _dedicated_daemon_readback(\n",
        "authority process helper",
    )
    source = replace_once(
        source,
        '''            "--property=MainPID", "--property=FragmentPath",
            "--property=ExecStart",
''',
        '''            "--property=MainPID", "--property=FragmentPath",
            "--property=ExecStart", "--property=ControlGroup",
            "--property=CPUShares", "--property=MemoryLimit",
            "--property=TasksMax", "--property=Id",
            "--property=Transient",
''',
        "authority systemd properties",
    )
    source = replace_once(
        source,
        '''    required_exec = (
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
''',
        '''    main_pid = int(properties.get("MainPID") or 0)
    expected_exec = "argv[]=" + " ".join(contract["exec_argv"]) + " ;"
''',
        "authority exec contract",
    )
    source = replace_once(
        source,
        '''        properties.get("ActiveState") != "active"
        or properties.get("SubState") != "running"
        or properties.get("FragmentPath") != DOCKER_UNIT_FRAGMENT
        or main_pid <= 1
        or any(fragment not in properties.get("ExecStart", "")
               for fragment in required_exec)
''',
        '''        properties.get("ActiveState") != "active"
        or properties.get("SubState") != "running"
        or properties.get("Id") != contract["unit"]
        or properties.get("FragmentPath") != contract["unit_fragment"]
        or properties.get("Transient") != "yes"
        or main_pid != contract["main_pid"]
        or properties.get("ControlGroup") != contract["control_group"]
        or int(properties.get("CPUShares") or 0) != contract["cpu_shares"]
        or int(properties.get("MemoryLimit") or 0)
        != contract["memory_limit_bytes"]
        or int(properties.get("TasksMax") or 0) != contract["tasks_max"]
        or expected_exec not in properties.get("ExecStart", "")
''',
        "authority exact transient readback",
    )
    source = replace_once(
        source,
        '''    socket_state = os.stat(DOCKER_SOCKET, follow_symlinks=False)
''',
        '''    process_readback = _live_daemon_process_readback(
        main_pid, contract
    )
    socket_state = os.stat(DOCKER_SOCKET, follow_symlinks=False)
''',
        "authority process readback call",
    )
    source = replace_once(
        source,
        '''        "unit_fragment": DOCKER_UNIT_FRAGMENT,
        "main_pid": main_pid,
''',
        '''        "unit_fragment": contract["unit_fragment"],
        "transient": True,
        "control_group": contract["control_group"],
        "cpu_shares": contract["cpu_shares"],
        "memory_limit_bytes": contract["memory_limit_bytes"],
        "tasks_max": contract["tasks_max"],
        "main_pid": main_pid,
        "main_pid_process_readback": process_readback,
''',
        "authority transient evidence",
    )
    return source.encode("utf-8")


def collector_payload() -> bytes:
    source = (PREDECESSOR / COLLECTOR).read_text(encoding="utf-8")
    source = replace_once(
        source,
        'DOCKER_UNIT_FRAGMENT = "/etc/systemd/system/'
        'ra-docker-mysql-tpch-v1.service"',
        'DOCKER_UNIT_FRAGMENT = "/run/systemd/system/'
        'ra-docker-mysql-tpch-v1.service"',
        "collector transient fragment",
    )
    source = replace_once(
        source,
        "\ndef dedicated_daemon_readback():\n",
        "\n" + COLLECTOR_PROC_HELPER
        + "\ndef dedicated_daemon_readback():\n",
        "collector process helper",
    )
    source = replace_once(
        source,
        '''            "--property=MainPID", "--property=FragmentPath",
            "--property=ExecStart",
''',
        '''            "--property=MainPID", "--property=FragmentPath",
            "--property=ExecStart", "--property=ControlGroup",
            "--property=CPUShares", "--property=MemoryLimit",
            "--property=TasksMax", "--property=Id",
            "--property=Transient",
''',
        "collector systemd properties",
    )
    source = replace_once(
        source,
        '''    required = (
        "--host=" + DOCKER_SOCKET_URI,
        "--data-root=" + DOCKER_DATA_ROOT,
        "--storage-driver=overlay2", "--bridge=none", "--iptables=false",
        "--ip-forward=false", "--ip-masq=false", "--userland-proxy=false",
    )
    main_pid = int(properties.get("MainPID") or 0)
''',
        '''    contract = '''
        + repr(load(CONTRACT))
        + '''
    main_pid = int(properties.get("MainPID") or 0)
    expected_exec = "argv[]=" + " ".join(contract["exec_argv"]) + " ;"
''',
        "collector contract",
    )
    source = replace_once(
        source,
        '''        properties.get("ActiveState") != "active"
        or properties.get("SubState") != "running"
        or properties.get("FragmentPath") != DOCKER_UNIT_FRAGMENT
        or main_pid <= 1
        or not stat.S_ISSOCK(socket_state.st_mode)
        or any(item not in properties.get("ExecStart", "") for item in required)
''',
        '''        properties.get("ActiveState") != "active"
        or properties.get("SubState") != "running"
        or properties.get("Id") != contract["unit"]
        or properties.get("FragmentPath") != contract["unit_fragment"]
        or properties.get("Transient") != "yes"
        or main_pid != contract["main_pid"]
        or properties.get("ControlGroup") != contract["control_group"]
        or int(properties.get("CPUShares") or 0) != contract["cpu_shares"]
        or int(properties.get("MemoryLimit") or 0)
        != contract["memory_limit_bytes"]
        or int(properties.get("TasksMax") or 0) != contract["tasks_max"]
        or expected_exec not in properties.get("ExecStart", "")
        or not stat.S_ISSOCK(socket_state.st_mode)
''',
        "collector exact transient readback",
    )
    source = replace_once(
        source,
        '''    raw_info = run(
''',
        '''    process_readback = _live_daemon_process_readback(
        main_pid, contract
    )
    raw_info = run(
''',
        "collector process readback call",
    )
    source = replace_once(
        source,
        '''        "unit": DOCKER_UNIT,
        "main_pid": main_pid,
''',
        '''        "unit": DOCKER_UNIT,
        "unit_fragment": contract["unit_fragment"],
        "transient": True,
        "control_group": contract["control_group"],
        "cpu_shares": contract["cpu_shares"],
        "memory_limit_bytes": contract["memory_limit_bytes"],
        "tasks_max": contract["tasks_max"],
        "main_pid": main_pid,
        "main_pid_process_readback": process_readback,
''',
        "collector transient evidence",
    )
    return source.encode("utf-8")


def runtime_payload() -> bytes:
    value = load(PREDECESSOR / RUNTIME)
    value["dedicated_daemon"] = load(CONTRACT)
    value["dedicated_daemon_deployment_policy"] = {
        "mode": "adopt_exact_live_transient_unit",
        "static_unit_deployment_prohibited": True,
        "live_unit_mutation_prohibited": True,
        "systemctl_fixture_sha256": sha256(SYSTEMCTL_FIXTURE),
        "proc_fixture_sha256": sha256(PROC_FIXTURE),
    }
    return canonical(value)


def verify_inputs() -> None:
    validate_fixtures()
    invalid = load(INVALIDATION)
    if (
        invalid.get("status") != "INVALIDATED_DO_NOT_RUN_OR_CREDIT"
        or invalid.get("bundle_id") != PREDECESSOR_ID
        or invalid.get("bundle_manifest_sha256")
        != PREDECESSOR_MANIFEST_SHA256
        or invalid.get("container_started") is not False
        or invalid.get("database_started") is not False
        or invalid.get("workload_started") is not False
        or invalid.get("accepted_rounds_credit") != 0
        or invalid.get("replay_or_rerun_authorized") is not False
    ):
        raise BuildError("9aa invalidation differs")


def payloads(predecessor: dict[str, Any]) -> dict[str, bytes]:
    result = {
        relative: (PREDECESSOR / relative).read_bytes()
        for relative in sorted(predecessor["files"])
        if relative != STATIC_UNIT_RELATIVE
    }
    result[AUTHORITY] = authority_payload()
    result[COLLECTOR] = collector_payload()
    result[RUNTIME] = runtime_payload()
    result[CONTRACT_RELATIVE] = CONTRACT.read_bytes()
    result[INVALIDATION_RELATIVE] = INVALIDATION.read_bytes()
    result[SYSTEMCTL_FIXTURE_RELATIVE] = SYSTEMCTL_FIXTURE.read_bytes()
    result[PROC_FIXTURE_RELATIVE] = PROC_FIXTURE.read_bytes()
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
    components["bootstrap_authority"] = files[AUTHORITY]
    components["collector"] = files[COLLECTOR]
    identity.update({
        "predecessor_bundle_id": PREDECESSOR_ID,
        "predecessor_manifest_sha256": PREDECESSOR_MANIFEST_SHA256,
        "builder_sha256": sha256(HERE),
        "component_hashes": components,
        "distributed_runtime_contract_sha256": files[RUNTIME],
        "dedicated_daemon_contract_sha256": files[CONTRACT_RELATIVE],
        "dedicated_daemon_contract": load(CONTRACT),
        "invalidated_9aa_static_fragment_bundle_sha256": sha256(INVALIDATION),
        "node04_live_daemon_systemctl_fixture_sha256":
            sha256(SYSTEMCTL_FIXTURE),
        "node04_live_daemon_proc_fixture_sha256": sha256(PROC_FIXTURE),
        "static_daemon_unit_removed_from_bundle": True,
        "static_daemon_unit_deployment_prohibited": True,
        "live_daemon_mutation_prohibited": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "offline_build_only": True,
        "remote_actions": 0,
        "files": files,
    })
    bundle_id = sha_bytes(canonical(identity))
    manifest = dict(identity)
    manifest["bundle_id"] = bundle_id
    atomic = atomic_publisher()
    atomic.BUNDLE_ROOT = BUNDLE_ROOT
    bundle = atomic.publish(content, manifest)
    return {
        "schema_version": "mysql_tpch_live_daemon_successor_build_v1",
        "status": "PASS_IMMUTABLE_SUCCESSOR_PUBLISHED",
        "bundle_id": bundle_id,
        "bundle": str(bundle),
        "bundle_manifest_sha256": sha256(bundle / "bundle_manifest.json"),
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "remote_actions": 0,
    }


def main() -> int:
    print(json.dumps(build(), sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (BuildError, OSError, UnicodeError, ValueError) as exc:
        sys.stderr.write("NOT_READY: %s\n" % exc)
        raise SystemExit(2)
