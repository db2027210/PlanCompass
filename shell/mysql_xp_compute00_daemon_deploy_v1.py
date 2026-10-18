#!/usr/bin/env python3
"""Deploy or adopt the exact XP-only Docker daemon on compute00.

This entrypoint has one narrowly scoped mutation: when the exact transient
unit does not exist, it may create and start that unit with systemd-run.
It never starts a database/container/workload and has no PostgreSQL mutation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import socket
import stat
import subprocess
import sys
import tempfile
import time


sys.dont_write_bytecode = True
SCHEMA = "mysql_xp_compute00_daemon_deployment_successor_v7"
HOST = "compute00"
UNIT = "ra-docker-mysql-xp-v2.service"
CONTROL_GROUP = "/system.slice/" + UNIT
FRAGMENT_PATH = "/run/systemd/transient/" + UNIT
SOCKET = "/run/ra-docker-mysql-xp-v2.sock"
SOCKET_URI = "unix://" + SOCKET
DATA_ROOT = "/disk1/resource_adaptive_mysql_algorithm_c_local_v2/xp/docker_ra_xp_v29"
EXEC_ROOT = "/run/ra-docker-mysql-xp-v2"
PIDFILE = "/run/ra-docker-mysql-xp-v2.pid"
DATA_MOUNT = "/disk1"
DATA_DEVICE = "/dev/nvme1n1"
DATA_MAJOR_MINOR = "259:6"
DAEMON_CPUSET = "192-193,448-449"
PG_CPUSET = "32-51,288-307"
MEMORY_LIMIT = 2147483648
CPU_SHARES = 256
TASKS_MAX = 512
LIMIT_NOFILE = 1048576
CONFIG_RELATIVE = "deployment/dockerd-empty-config.json"
DOCKERD = "/usr/bin/dockerd"
TASKSET = "/usr/bin/taskset"
SYSTEMD_RUN = "/usr/bin/systemd-run"
SYSTEMCTL = "/usr/bin/systemctl"
FINDMNT = "/usr/bin/findmnt"
DOCKER = "/usr/bin/docker"
ACTIVE_CONTROLLERS = {
    "name=systemd", "cpu", "cpuacct", "devices", "memory", "pids",
    "blkio",
}
ROOT_CONTROLLERS = {
    "cpuset", "rdma", "freezer", "perf_event", "hugetlb", "net_cls",
    "net_prio",
}
SYSTEMD_PROPERTIES = (
    "CPUShares=256",
    "MemoryLimit=2147483648",
    "TasksMax=512",
    "LimitNOFILE=1048576",
    "Delegate=yes",
    "Restart=no",
    "KillMode=process",
)
SHOW_PROPERTIES = (
    "Id", "LoadState", "ActiveState", "SubState", "MainPID",
    "ControlGroup", "FragmentPath", "SourcePath", "UnitFileState",
    "Transient", "ExecStart", "ExecStartPre", "ExecStartPost", "ExecStop",
    "ExecStopPost", "ExecReload", "Requires", "Wants", "BindsTo",
    "PartOf", "Conflicts", "Before", "After", "CPUShares",
    "MemoryLimit", "TasksMax", "LimitNOFILE", "Delegate", "Restart",
    "KillMode",
)
OPTIONAL_EMPTY_EXEC_PROPERTIES_WHEN_NOT_FOUND = frozenset((
    "ExecStart", "ExecStartPre", "ExecStartPost", "ExecStop",
    "ExecStopPost", "ExecReload",
))
OPTIONAL_EMPTY_EXEC_PROPERTIES_WHEN_ACTIVE = frozenset((
    "ExecStartPre", "ExecStartPost", "ExecStop", "ExecStopPost",
    "ExecReload",
))
ADOPT_EXISTING_ONLY = False
ADOPTED_CONFIG_PATH = ""
ADOPTED_BUNDLE_ID = ""
ADOPTED_MANIFEST_SHA256 = ""
ADOPTED_MAIN_PID = 0
ADOPTED_STARTTICKS = 0
ADOPTED_CMDLINE_SHA256 = ""
ADOPTED_ACTIVE_SYSTEMD_EXPECTED = None
SYSTEMD219_NOT_FOUND_EXPECTED = {
    "Restart": "no", "MainPID": "0", "ControlGroup": "",
    "Delegate": "no", "CPUShares": "[not set]",
    "MemoryLimit": "infinity", "TasksMax": "629145",
    "LimitNOFILE": "1048576", "KillMode": "control-group", "Id": UNIT,
    "Requires": "", "Wants": "", "BindsTo": "", "PartOf": "",
    "Conflicts": "", "Before": "", "After": "",
    "LoadState": "not-found", "ActiveState": "inactive",
    "SubState": "dead", "FragmentPath": "", "SourcePath": "",
    "UnitFileState": "", "Transient": "no",
    "ExecStart": "", "ExecStartPre": "", "ExecStartPost": "",
    "ExecStop": "", "ExecStopPost": "", "ExecReload": "",
}


class DeploymentError(RuntimeError):
    pass


def canonical(value):
    # This is the content-addressing canonical form inherited from the v6
    # builder.  The deployer must use byte-for-byte the same representation.
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode("utf-8")


def sha_bytes(payload):
    return hashlib.sha256(payload).hexdigest()


def sha_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def bundle_files(root):
    values = {}
    for path in sorted(Path(root).rglob("*")):
        if path.is_symlink():
            raise DeploymentError("bundle symlink prohibited")
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            if relative not in ("bundle_manifest.json",
                                "bundle_manifest.json.sha256"):
                values[relative] = sha_file(path)
    return values


def verify_bundle(root, authorized_id):
    root = Path(root).resolve()
    manifest = json.loads((root / "bundle_manifest.json").read_text())
    unsigned = dict(manifest)
    observed_id = unsigned.pop("bundle_id", None)
    authority_differs = (
        manifest.get("daemon_adoption_authorized") is not True
        or manifest.get("daemon_deployment_authorized") is not False
    ) if ADOPT_EXISTING_ONLY else (
        manifest.get("daemon_deployment_authorized") is not True
    )
    if (
        root.name != authorized_id
        or observed_id != authorized_id
        or sha_bytes(canonical(unsigned)) != authorized_id
        or bundle_files(root) != manifest.get("files")
        or manifest.get("schema_version") != SCHEMA
        or authority_differs
        or manifest.get("workload_launch_authorized") is not False
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
        or manifest.get("accepted_rounds_credit") != 0
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("postgresql_mutation_prohibited") is not True
    ):
        raise DeploymentError("sealed daemon-deployment bundle differs")
    config = root / CONFIG_RELATIVE
    if config.read_bytes() != b"{}\n":
        raise DeploymentError("sealed empty dockerd config differs")
    return root, manifest, config


def verify_adopted_config():
    if not ADOPT_EXISTING_ONLY:
        raise DeploymentError("adopted config verification is adoption-only")
    config = Path(ADOPTED_CONFIG_PATH)
    bundle = config.parents[1]
    if (
        config.is_symlink() or config.read_bytes() != b"{}\n"
        or bundle.name != ADOPTED_BUNDLE_ID
        or sha_file(bundle / "bundle_manifest.json")
        != ADOPTED_MANIFEST_SHA256
    ):
        raise DeploymentError("adopted v9 config/bundle identity differs")
    manifest = json.loads((bundle / "bundle_manifest.json").read_text())
    unsigned = dict(manifest); observed = unsigned.pop("bundle_id", None)
    if (
        observed != ADOPTED_BUNDLE_ID
        or sha_bytes(canonical(unsigned)) != ADOPTED_BUNDLE_ID
        or bundle_files(bundle) != manifest.get("files")
    ):
        raise DeploymentError("adopted v9 bundle closure differs")
    return config


def dockerd_argv(config):
    return [
        DOCKERD,
        "--config-file=" + str(Path(config).resolve()),
        "--host=" + SOCKET_URI,
        "--data-root=" + DATA_ROOT,
        "--exec-root=" + EXEC_ROOT,
        "--pidfile=" + PIDFILE,
        "--storage-driver=overlay2",
        "--exec-opt=native.cgroupdriver=cgroupfs",
        "--bridge=none",
        "--iptables=false",
        "--ip-forward=false",
        "--ip-masq=false",
        "--userland-proxy=false",
        "--log-driver=none",
        "--group=root",
    ]


def unit_exec_argv(config):
    return [TASKSET, "-c", DAEMON_CPUSET, *dockerd_argv(config)]


def systemd_run_argv(config):
    argv = [
        SYSTEMD_RUN,
        "--unit=" + UNIT,
        "--description=Dedicated Docker daemon for MySQL XP only",
        "--service-type=notify",
    ]
    argv.extend("--property=" + value for value in SYSTEMD_PROPERTIES)
    argv.extend(unit_exec_argv(config))
    return argv


def show_argv():
    argv = [SYSTEMCTL, "show", UNIT]
    argv.extend("--property=" + value for value in SHOW_PROPERTIES)
    return argv


def validate_command(argv, mutation=False):
    argv = list(argv)
    if not argv:
        raise DeploymentError("empty command prohibited")
    if mutation:
        if ADOPT_EXISTING_ONLY:
            raise DeploymentError("systemd-run prohibited by adoption-only successor")
        if argv[0] != SYSTEMD_RUN or argv != systemd_run_argv(
                _config_from_systemd_run(argv)):
            raise DeploymentError("non-exact daemon mutation prohibited")
        return
    if argv[0] == SYSTEMCTL:
        if len(argv) < 3 or argv[1] != "show" or argv[2] != UNIT:
            raise DeploymentError("systemctl lifecycle mutation prohibited")
    elif argv[0] == FINDMNT:
        if "--target" not in argv:
            raise DeploymentError("unscoped findmnt prohibited")
    elif argv[0] == DOCKER:
        if argv[1:3] != ["--host", SOCKET_URI] or argv[3] not in (
                "info", "ps"):
            raise DeploymentError("Docker mutation/default socket prohibited")
    else:
        raise DeploymentError("command is outside daemon readback allowlist")


def _config_from_systemd_run(argv):
    markers = [value for value in argv if value.startswith("--config-file=")]
    if len(markers) != 1:
        raise DeploymentError("dockerd config argv differs")
    return markers[0].split("=", 1)[1]


def run(argv, timeout=30, mutation=False, check=True):
    validate_command(argv, mutation=mutation)
    completed = subprocess.run(
        list(argv), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, timeout=timeout, check=False,
    )
    if check and completed.returncode:
        raise DeploymentError(
            "command failed rc=%d argv=%s stderr=%s" % (
                completed.returncode, json.dumps(list(argv)),
                completed.stderr.strip()[:1000],
            )
        )
    return completed


def parse_show(raw):
    values = {}
    for line in raw.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            if key in values:
                raise DeploymentError("duplicate systemd property")
            values[key] = value
    unexpected = set(values) - set(SHOW_PROPERTIES)
    missing = set(SHOW_PROPERTIES) - set(values)
    if unexpected:
        raise DeploymentError("systemd property inventory differs")
    if values.get("LoadState") == "not-found":
        if not missing <= OPTIONAL_EMPTY_EXEC_PROPERTIES_WHEN_NOT_FOUND:
            raise DeploymentError("systemd property inventory differs")
        for key in missing:
            values[key] = ""
    elif (
        values.get("LoadState") == "loaded"
        and values.get("ActiveState") == "active"
        and values.get("SubState") == "running"
    ):
        if missing != OPTIONAL_EMPTY_EXEC_PROPERTIES_WHEN_ACTIVE:
            raise DeploymentError("systemd property inventory differs")
        for key in missing:
            values[key] = ""
    elif missing:
        raise DeploymentError("systemd property inventory differs")
    return values


def show_unit():
    return parse_show(run(show_argv(), check=False).stdout)


def parse_execstart(raw):
    match = re.search(r"(?:^|[ ;])argv\[\]=(.*?) ; ignore_errors=", raw)
    if not match:
        raise DeploymentError("systemd ExecStart serialization differs")
    try:
        return shlex.split(match.group(1))
    except ValueError as exc:
        raise DeploymentError("systemd ExecStart argv differs") from exc


def expand_cpuset(value):
    result = set()
    for item in value.split(","):
        fields = item.split("-", 1)
        start = int(fields[0]); end = int(fields[-1])
        if end < start:
            raise DeploymentError("CPU set differs")
        result.update(range(start, end + 1))
    return result


def proc_status(pid):
    result = {}
    for line in (Path("/proc") / str(pid) / "status").read_text().splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            result[key] = value.strip()
    return result


def proc_startticks(pid):
    raw = (Path("/proc") / str(pid) / "stat").read_text(encoding="ascii")
    closing = raw.rfind(")")
    fields = raw[closing + 2:].split() if closing > 0 else []
    if len(fields) < 20:
        raise DeploymentError("daemon stat identity differs")
    return int(fields[19])


def host_namespace_identity(pid):
    mandatory = {}
    for name in ("pid", "mnt", "net", "user", "ipc", "uts"):
        own = os.stat("/proc/%d/ns/%s" % (pid, name))
        init = os.stat("/proc/1/ns/" + name)
        row = {"daemon": [own.st_dev, own.st_ino],
               "pid1": [init.st_dev, init.st_ino]}
        if row["daemon"] != row["pid1"]:
            raise DeploymentError("daemon is not in host namespace: " + name)
        mandatory[name] = row
    daemon_cgroup = "/proc/%d/ns/cgroup" % pid
    init_cgroup = "/proc/1/ns/cgroup"
    daemon_supported = os.path.lexists(daemon_cgroup)
    init_supported = os.path.lexists(init_cgroup)
    if daemon_supported != init_supported:
        raise DeploymentError("cgroup namespace capability is asymmetric")
    optional = {
        "supported": daemon_supported, "path_absent": not daemon_supported,
        "daemon": None, "pid1": None,
        "fallback": "/proc/<daemon>/cgroup_plus_systemd_ControlGroup",
    }
    if daemon_supported:
        own = os.stat(daemon_cgroup); init = os.stat(init_cgroup)
        optional["daemon"] = [own.st_dev, own.st_ino]
        optional["pid1"] = [init.st_dev, init.st_ino]
        if optional["daemon"] != optional["pid1"]:
            raise DeploymentError("daemon is not in host cgroup namespace")
    return {"mandatory": mandatory, "optional_cgroup": optional}


def cgroup_projection(raw):
    exact = set(); root = set(); hierarchies = set(); seen = set(); hybrid = 0
    rows = []
    for line in raw.splitlines():
        fields = line.split(":", 2)
        if len(fields) != 3 or not fields[0].isdigit():
            raise DeploymentError("daemon cgroup row differs")
        hierarchy, controllers_raw, path = fields
        if hierarchy in hierarchies:
            raise DeploymentError("duplicate daemon cgroup hierarchy")
        hierarchies.add(hierarchy)
        if hierarchy == "0":
            if controllers_raw or path != CONTROL_GROUP or hybrid:
                raise DeploymentError("daemon hybrid cgroup row differs")
            hybrid += 1; controllers = []; projection = "hybrid_exact_unit"
        else:
            controllers = (controllers_raw.split(",")
                           if controllers_raw != "name=systemd"
                           else ["name=systemd"])
            if (not controllers or any(value in seen for value in controllers)
                    or any(value not in ACTIVE_CONTROLLERS | ROOT_CONTROLLERS
                           for value in controllers)):
                raise DeploymentError("daemon cgroup controllers differ")
            seen.update(controllers)
            values = set(controllers)
            if path == CONTROL_GROUP and values <= ACTIVE_CONTROLLERS:
                exact.update(values); projection = "v1_active_exact_unit"
            elif path == "/" and values <= ROOT_CONTROLLERS:
                root.update(values); projection = "v1_root"
            else:
                raise DeploymentError("daemon cgroup path differs")
        rows.append({"hierarchy": hierarchy, "controllers": controllers,
                     "path": path, "projection": projection})
    if (hybrid != 1 or exact != ACTIVE_CONTROLLERS
            or root != ROOT_CONTROLLERS
            or seen != ACTIVE_CONTROLLERS | ROOT_CONTROLLERS):
        raise DeploymentError("daemon cgroup projection differs")
    return {"rows": rows, "hybrid_exact_unit_rows": hybrid,
            "active_exact_unit_controllers": sorted(exact),
            "root_controllers": sorted(root), "rdma_projection": "root"}


def path_device(path):
    state = Path(path).stat()
    return "%d:%d" % (os.major(state.st_dev), os.minor(state.st_dev))


def mount_readback(path):
    completed = run([
        FINDMNT, "-n", "-o", "SOURCE,MAJ:MIN,FSTYPE,TARGET", "--target",
        str(path),
    ])
    fields = completed.stdout.strip().split()
    if len(fields) != 4:
        raise DeploymentError("data-root findmnt differs")
    return dict(zip(("source", "major_minor", "filesystem", "target"), fields))


def pg_inventory():
    rows = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            raw = (proc / "cmdline").read_bytes()
            text = raw.replace(b"\0", b" ").decode("utf-8", "replace").lower()
            if "postgres" not in text:
                continue
            pid = int(proc.name); status = proc_status(pid)
            # Bind the stable host PostgreSQL service roots.  Ephemeral
            # backends may legitimately come and go under a co-resident
            # workload and are handled by the later dynamic interference
            # gate, not by this daemon-only deployment receipt.
            if int(status.get("PPid", "0")) != 1:
                continue
            rows.append({
                "pid": pid, "startticks": proc_startticks(pid),
                "cpuset": status.get("Cpus_allowed_list", ""),
                "cmdline_sha256": sha_bytes(raw),
            })
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
    return sorted(rows, key=lambda row: (row["pid"], row["startticks"]))


def preflight(config):
    if os.geteuid() != 0:
        raise DeploymentError("daemon deployment requires root")
    if socket.gethostname().split(".", 1)[0] != HOST:
        raise DeploymentError("daemon deployment is host-bound to compute00")
    if expand_cpuset(DAEMON_CPUSET) & expand_cpuset(PG_CPUSET):
        raise DeploymentError("XP daemon/PostgreSQL CPU overlap")
    for executable in (DOCKERD, TASKSET, SYSTEMD_RUN, SYSTEMCTL, FINDMNT,
                       DOCKER):
        state = Path(executable).stat()
        if (not stat.S_ISREG(state.st_mode) or state.st_uid != 0
                or state.st_mode & 0o022):
            raise DeploymentError("root executable identity differs")
    if Path(config).is_symlink() or sha_file(config) != sha_bytes(b"{}\n"):
        raise DeploymentError("dockerd config identity differs")
    target = Path(DATA_ROOT)
    anchor = target if target.exists() else target.parent
    if anchor.is_symlink() or not anchor.is_dir():
        raise DeploymentError("data-root anchor differs")
    mount = mount_readback(anchor)
    if (path_device(anchor) != DATA_MAJOR_MINOR
            or mount["source"] != DATA_DEVICE
            or mount["major_minor"] != DATA_MAJOR_MINOR
            or mount["target"] != DATA_MOUNT):
        raise DeploymentError("XP disk1 identity differs")
    if target.exists() and (target.is_symlink() or not target.is_dir()
                            or path_device(target) != DATA_MAJOR_MINOR):
        raise DeploymentError("existing Docker data-root differs")
    properties = show_unit()
    state = (properties["LoadState"], properties["ActiveState"],
             properties["SubState"], properties["MainPID"])
    if properties["LoadState"] == "not-found":
        if ADOPT_EXISTING_ONLY:
            raise DeploymentError("adoption-only successor requires exact active daemon")
        if properties != SYSTEMD219_NOT_FOUND_EXPECTED:
            raise DeploymentError("not-found unit state differs")
        for path in (Path(SOCKET), Path(PIDFILE), Path(EXEC_ROOT)):
            if path.exists() or path.is_symlink():
                raise DeploymentError("orphan daemon runtime identity exists")
        action = "CREATE_EXACT_TRANSIENT_UNIT"
    elif (properties["LoadState"] == "loaded"
          and properties["ActiveState"] == "active"
          and properties["SubState"] == "running"
          and int(properties["MainPID"] or 0) > 1):
        action = "ADOPT_EXACT_EXISTING_ONLY"
    else:
        raise DeploymentError("existing unit is neither absent nor exact-active")
    return {"action": action, "unit_state": state, "mount": mount,
            "postgresql_before": pg_inventory()}


def check_property_identity(properties):
    if ADOPT_EXISTING_ONLY:
        if (not isinstance(ADOPTED_ACTIVE_SYSTEMD_EXPECTED, dict)
                or properties != ADOPTED_ACTIVE_SYSTEMD_EXPECTED):
            raise DeploymentError("adopted active systemd identity differs")
        return
    expected = {
        "Id": UNIT, "LoadState": "loaded", "ActiveState": "active",
        "SubState": "running", "ControlGroup": CONTROL_GROUP,
        "FragmentPath": FRAGMENT_PATH, "SourcePath": "",
        "UnitFileState": "transient", "Transient": "yes",
        "ExecStartPre": "", "ExecStartPost": "", "ExecStop": "",
        "ExecStopPost": "", "ExecReload": "", "CPUShares": str(CPU_SHARES),
        "MemoryLimit": str(MEMORY_LIMIT), "TasksMax": str(TASKS_MAX),
        "LimitNOFILE": str(LIMIT_NOFILE), "Delegate": "yes",
        "Restart": "no", "KillMode": "process",
    }
    for key, value in expected.items():
        if properties.get(key) != value:
            raise DeploymentError("systemd property differs: " + key)
    dependencies = " ".join(properties[key] for key in (
        "Requires", "Wants", "BindsTo", "PartOf", "Conflicts", "Before",
        "After",
    )).lower()
    if "postgres" in dependencies or "mysqld" in dependencies:
        raise DeploymentError("PostgreSQL/MySQL service dependency prohibited")


def socket_owner(pid):
    state = os.stat(SOCKET, follow_symlinks=False)
    if (not stat.S_ISSOCK(state.st_mode) or state.st_uid != 0
            or state.st_gid != 0 or stat.S_IMODE(state.st_mode) != 0o660):
        raise DeploymentError("dedicated socket metadata differs")
    identity = "socket:[%d]" % state.st_ino
    try:
        targets = {os.readlink(path) for path in
                   (Path("/proc") / str(pid) / "fd").iterdir()}
    except OSError as exc:
        raise DeploymentError("daemon FD inventory differs") from exc
    if identity not in targets:
        raise DeploymentError("dedicated socket is not owned by MainPID")
    return {"path": SOCKET, "inode": state.st_ino, "mode": "0660",
            "uid": 0, "gid": 0, "owned_by_main_pid": True}


def docker_readback():
    info_raw = run([DOCKER, "--host", SOCKET_URI, "info", "--format",
                    "{{json .}}"], timeout=30).stdout
    try:
        info = json.loads(info_raw)
    except json.JSONDecodeError as exc:
        raise DeploymentError("Docker info JSON differs") from exc
    identifiers = run([DOCKER, "--host", SOCKET_URI, "ps", "-aq",
                       "--no-trunc"]).stdout.split()
    root = Path(str(info.get("DockerRootDir") or "")).resolve()
    graph = root / "overlay2"
    if (
        info.get("Driver") != "overlay2"
        or info.get("CgroupDriver") != "cgroupfs"
        or root != Path(DATA_ROOT).resolve()
        or root.is_symlink() or not root.is_dir()
        or path_device(root) != DATA_MAJOR_MINOR
        or not graph.is_dir() or graph.is_symlink()
        or path_device(graph) != DATA_MAJOR_MINOR
        or identifiers
    ):
        raise DeploymentError("Docker root/driver/empty inventory differs")
    mount = mount_readback(root)
    if (mount["source"] != DATA_DEVICE
            or mount["major_minor"] != DATA_MAJOR_MINOR
            or mount["target"] != DATA_MOUNT):
        raise DeploymentError("Docker root mount differs")
    projection = {key: info.get(key) for key in (
        "Architecture", "CgroupDriver", "DockerRootDir", "Driver", "ID",
        "KernelVersion", "Name", "OperatingSystem", "OSType",
        "ServerVersion",
    )}
    return {"docker_info_projection": projection,
            "docker_info_sha256": sha_bytes(canonical(projection)),
            "storage_driver": "overlay2", "cgroup_driver": "cgroupfs",
            "data_root": DATA_ROOT, "data_root_device": DATA_DEVICE,
            "data_root_major_minor": DATA_MAJOR_MINOR,
            "overlay2_major_minor": DATA_MAJOR_MINOR, "mount": mount,
            "container_count": 0, "default_socket_used": False}


def live_readback(config):
    properties = show_unit(); check_property_identity(properties)
    pid = int(properties.get("MainPID") or 0)
    if pid <= 1:
        raise DeploymentError("daemon MainPID differs")
    if ADOPT_EXISTING_ONLY and pid != ADOPTED_MAIN_PID:
        raise DeploymentError("adopted daemon MainPID differs")
    if parse_execstart(properties["ExecStart"]) != unit_exec_argv(config):
        raise DeploymentError("complete systemd ExecStart differs")
    proc = Path("/proc") / str(pid)
    cmdline_raw = (proc / "cmdline").read_bytes()
    cmdline = [part.decode("utf-8") for part in cmdline_raw.split(b"\0")
               if part]
    if cmdline != dockerd_argv(config):
        raise DeploymentError("complete live dockerd argv differs")
    if (ADOPT_EXISTING_ONLY
            and sha_bytes(cmdline_raw) != ADOPTED_CMDLINE_SHA256):
        raise DeploymentError("adopted daemon cmdline identity differs")
    status = proc_status(pid)
    if (int(status.get("PPid", "0")) != 1
            or expand_cpuset(status.get("Cpus_allowed_list", ""))
            != expand_cpuset(DAEMON_CPUSET)):
        raise DeploymentError("daemon PPID/CPU identity differs")
    namespaces = host_namespace_identity(pid)
    cgroup_raw = (proc / "cgroup").read_text(encoding="ascii")
    projection = cgroup_projection(cgroup_raw)
    pidfile = Path(PIDFILE)
    if (pidfile.is_symlink() or not pidfile.is_file()
            or pidfile.read_text().strip() != str(pid)):
        raise DeploymentError("daemon pidfile differs")
    return {
        "unit": {key: properties[key] for key in SHOW_PROPERTIES},
        "unit_exec_argv": unit_exec_argv(config),
        "dockerd_argv": cmdline, "main_pid": pid,
        "process_startticks": proc_startticks(pid), "ppid": 1,
        "cmdline_sha256": sha_bytes(cmdline_raw),
        "cpuset_cpus": DAEMON_CPUSET, "namespaces": namespaces,
        "cgroup": projection, "socket": socket_owner(pid),
        "docker": docker_readback(),
    }


def wait_live(config, timeout=120):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            return live_readback(config)
        except (DeploymentError, OSError, ValueError) as exc:
            last = str(exc)
        time.sleep(1)
    raise DeploymentError("daemon exact-live readback timeout: " + str(last))


def atomic_receipt(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical(value)
    if path.exists():
        if path.is_symlink() or not path.is_file():
            raise DeploymentError("existing deployment receipt differs")
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise DeploymentError("existing deployment receipt differs") from exc
        stable_keys = (
            "schema_version", "status", "bundle_id",
            "bundle_manifest_sha256", "formal_scope",
            "cross_engine_comparison_prohibited", "live_identity",
            "postgresql_process_identity_unchanged", "postgresql_mutation",
            "database_container_started", "client_container_started",
            "workload_started", "formal_launch_authorized",
            "accepted_round_authorized", "accepted_rounds_credit",
        )
        if any(existing.get(key) != value.get(key) for key in stable_keys):
            raise DeploymentError("existing deployment receipt differs")
        return "ADOPTED_IDENTICAL_RECEIPT"
    descriptor, temporary = tempfile.mkstemp(
        prefix="." + path.name + ".", dir=str(path.parent))
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload); handle.flush(); os.fsync(handle.fileno())
        os.rename(temporary, path)
        temporary = ""
    finally:
        if temporary:
            try: os.unlink(temporary)
            except FileNotFoundError: pass
    return "PUBLISHED_NEW_RECEIPT"


def deploy(bundle, authorized_id, output):
    root, manifest, config = verify_bundle(bundle, authorized_id)
    if ADOPT_EXISTING_ONLY:
        config = verify_adopted_config()
    before = preflight(config)
    if ADOPT_EXISTING_ONLY and before["action"] != "ADOPT_EXACT_EXISTING_ONLY":
        raise DeploymentError("adoption-only successor cannot create a unit")
    if before["action"] == "CREATE_EXACT_TRANSIENT_UNIT":
        run(systemd_run_argv(config), timeout=30, mutation=True)
        disposition = "CREATED_AND_STARTED_EXACT_TRANSIENT_UNIT"
    else:
        disposition = "ADOPTED_EXACT_EXISTING_TRANSIENT_UNIT"
    live = live_readback(config) if ADOPT_EXISTING_ONLY else wait_live(config)
    if (ADOPT_EXISTING_ONLY
            and live["process_startticks"] != ADOPTED_STARTTICKS):
        raise DeploymentError("adopted daemon startticks differs")
    postgres_after = pg_inventory()
    if postgres_after != before["postgresql_before"]:
        raise DeploymentError("PostgreSQL process identity changed during deployment")
    receipt = {
        "schema_version": "mysql_xp_compute00_daemon_deployment_receipt_v1",
        "status": "PASS_XP_DEDICATED_DAEMON_ONLY",
        "bundle_id": authorized_id,
        "bundle_manifest_sha256": sha_file(root / "bundle_manifest.json"),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "daemon_deployment_authorized": not ADOPT_EXISTING_ONLY,
        "daemon_adoption_authorized": ADOPT_EXISTING_ONLY,
        "systemd_run_authorized": not ADOPT_EXISTING_ONLY,
        "disposition": disposition,
        "preflight": before,
        "live_identity": live,
        "postgresql_after": postgres_after,
        "postgresql_process_identity_unchanged": True,
        "postgresql_mutation": False,
        "database_container_started": False,
        "client_container_started": False,
        "workload_started": False,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
    }
    mode = atomic_receipt(output, receipt)
    return receipt, mode


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--authorize-exact-bundle-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    receipt, mode = deploy(args.bundle, args.authorize_exact_bundle_id,
                           args.output)
    print(json.dumps({"status": receipt["status"], "receipt_mode": mode,
                      "output": str(args.output),
                      "disposition": receipt["disposition"]},
                     sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (DeploymentError, OSError, ValueError,
            subprocess.SubprocessError) as error:
        raise SystemExit("NOT_READY: " + str(error))
