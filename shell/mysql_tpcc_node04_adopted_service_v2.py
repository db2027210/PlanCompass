#!/usr/bin/env python3
"""TPC-C v11 service authority: mount the copied inner MySQL datadir only.

The immutable v10 service remains available as a read-only compatibility
dependency.  This successor changes just the copy/root authority: the copied
template envelope is ``<attempt>/datadir`` while MySQL is bound exclusively to
``<attempt>/datadir/datadir``.  The latter is normalized target-only by a
no-network root container to the frozen image identity 999:999, 0700/0600.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
from typing import Any, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mysql_tpcc_node04_adopted_service_v1 as base  # noqa: E402


MYSQL_UID = 999
MYSQL_GID = 999
MYSQL_DIR_MODE = "0700"
MYSQL_FILE_MODE = "0600"
MYSQL_DATADIR_RELATIVE = Path("datadir")
BLKIO_DEVICE_MAJOR_MINOR = "259:2"
BLKIO_BPS = 1_205_862_400
BENCHBASE_PLUGIN_RELATIVE = Path("benchbase_runtime/config")
BENCHBASE_PLUGIN_DESTINATION = "/measurement/config"
CLIENT_UID = 1008
CLIENT_GID = 1008
CLIENT_USER = f"{CLIENT_UID}:{CLIENT_GID}"
CLIENT_READABILITY_PATHS = {
    "jdk": "/opt/mysql-tpcc-jdk/bin/java",
    "bundle": "/opt/mysql-tpcc-bundle/predecessor/runtime/benchbase-formal.jar",
    "plugin": "/measurement/config/plugin.xml",
    "python": "/opt/mysql-tpcc-python/bin/python3.11",
    "tpcc_config": "/opt/mysql-tpcc-config/tpcc.xml",
    "mysql_defaults": "/opt/mysql-tpcc-config/mysql.cnf",
}
BENCHBASE_PLUGIN_SHA256 = (
    "a770865560e0d1044c98a2954bacfd3d0c58ed3e956d3e783b2e830c7951d7e2"
)

ServiceError = base.ServiceError


def _require_host_root() -> None:
    if os.geteuid() != 0 or os.getegid() != 0:
        raise ServiceError(
            "TPC-C adopted-service host phase requires effective uid/gid 0"
        )


def _require_root_plan(plan: Mapping[str, Any]) -> None:
    if plan.get("host_effective_uid_gid") != [0, 0]:
        raise ServiceError("service plan lacks effective uid/gid 0 authority")


def _required_ascii(path: Path, label: str) -> str:
    if not path.is_file() or path.is_symlink():
        raise ServiceError(f"{label} is absent or not a regular authority file")
    try:
        return path.read_text(encoding="ascii")
    except (OSError, UnicodeDecodeError) as exc:
        raise ServiceError(f"{label} cannot be read exactly") from exc


def _container_cgroup(
    client_id: str, pid: int, proc_root: Path,
) -> tuple[int, str]:
    if base.SHA_RE.fullmatch(client_id) is None:
        raise ServiceError("client full container ID is invalid")
    rows = _required_ascii(proc_root / str(pid) / "cgroup", "client cgroup membership")
    unified: list[str] = []
    blkio: list[str] = []
    for line in rows.splitlines():
        pieces = line.split(":", 2)
        if len(pieces) != 3 or not pieces[0].isdigit():
            raise ServiceError("client cgroup membership row is malformed")
        controllers = pieces[1].split(",") if pieces[1] else []
        relative = pieces[2]
        if not relative.startswith("/") or ".." in Path(relative).parts:
            raise ServiceError("client cgroup membership path is unsafe")
        if pieces[0] == "0" and not controllers:
            unified.append(relative)
        if "blkio" in controllers:
            blkio.append(relative)
    if unified:
        if len(unified) != 1 or blkio:
            raise ServiceError("client cgroup version authority is ambiguous")
        version, relative = 2, unified[0]
    else:
        if len(blkio) != 1:
            raise ServiceError("client blkio cgroup membership is missing or duplicated")
        version, relative = 1, blkio[0]
    leaf = Path(relative).name
    if leaf not in {client_id, f"docker-{client_id}.scope"}:
        raise ServiceError("client cgroup membership is not bound to full container ID")
    return version, relative


def _contained_cgroup_path(root: Path, relative: str) -> Path:
    candidate = root.joinpath(*Path(relative).parts[1:])
    try:
        candidate.resolve().relative_to(root.resolve())
    except (OSError, ValueError) as exc:
        raise ServiceError("client cgroup authority escapes controller root") from exc
    if not candidate.is_dir() or candidate.is_symlink():
        raise ServiceError("client cgroup authority directory is absent")
    return candidate


def _v1_device_rows(path: Path, label: str) -> dict[str, dict[str, int]]:
    rows: dict[str, dict[str, int]] = {}
    global_total_seen = False
    for line in _required_ascii(path, label).splitlines():
        pieces = line.split()
        if len(pieces) == 2 and pieces[0] == "Total":
            if global_total_seen or not pieces[1].isdigit():
                raise ServiceError(f"{label} global Total row is malformed or duplicated")
            global_total_seen = True
            continue
        if (
            len(pieces) != 3
            or not re.fullmatch(r"[0-9]+:[0-9]+", pieces[0])
            or pieces[1] not in {"Read", "Write", "Sync", "Async", "Total"}
            or not pieces[2].isdigit()
        ):
            raise ServiceError(f"{label} row is malformed")
        device, operation, raw = pieces
        row = rows.setdefault(device, {})
        if operation in row:
            raise ServiceError(f"{label} device operation is duplicated")
        row[operation] = int(raw)
    return rows


def _strict_v1_target(
    rows: Mapping[str, Mapping[str, int]], label: str, target: str,
) -> dict[str, int]:
    row = rows.get(target)
    required = {"Read", "Write", "Sync", "Async"}
    if row is None or set(row) - (required | {"Total"}) or not required.issubset(row):
        raise ServiceError(f"{label} target-device coverage is incomplete")
    if row["Read"] + row["Write"] != row["Sync"] + row["Async"]:
        raise ServiceError(f"{label} target-device directional totals disagree")
    if "Total" in row and row["Total"] != row["Read"] + row["Write"]:
        raise ServiceError(f"{label} target-device Total disagrees")
    return {key.lower(): row[key] for key in sorted(required)}


def _v2_target_snapshot(path: Path) -> dict[str, Any]:
    target: dict[str, int] | None = None
    devices: set[str] = set()
    for line in _required_ascii(path, "client unified cgroup io.stat").splitlines():
        pieces = line.split()
        if not pieces or not re.fullmatch(r"[0-9]+:[0-9]+", pieces[0]):
            raise ServiceError("client unified cgroup io.stat row is malformed")
        if pieces[0] in devices:
            raise ServiceError("client unified cgroup io.stat device row is duplicated")
        devices.add(pieces[0])
        row: dict[str, int] = {}
        for field in pieces[1:]:
            if field.count("=") != 1:
                raise ServiceError("client unified cgroup io.stat field is malformed")
            key, raw = field.split("=", 1)
            if not key or key in row or not raw.isdigit():
                raise ServiceError("client unified cgroup io.stat field is malformed or duplicated")
            row[key] = int(raw)
        if pieces[0] == BLKIO_DEVICE_MAJOR_MINOR:
            target = row
    required = {"rbytes", "wbytes", "rios", "wios"}
    if target is None or not required.issubset(target):
        raise ServiceError("client unified cgroup io.stat target-device coverage is incomplete")
    counters = {key: target[key] for key in sorted(required)}
    counters.update({
        "dbytes": target.get("dbytes", 0),
        "dios": target.get("dios", 0),
    })
    return {
        "counters": counters,
        "sync_async": None,
        "authority": {
            "cgroup_version": 2,
            "target_device_major_minor": BLKIO_DEVICE_MAJOR_MINOR,
            "counter_files": [str(path)],
        },
    }


def _v1_target_snapshot(root: Path) -> dict[str, Any]:
    families = (
        ("throttle", "blkio.throttle.io_service_bytes", "blkio.throttle.io_serviced"),
        ("controller", "blkio.io_service_bytes", "blkio.io_serviced"),
    )
    selected: tuple[str, Path, Path] | None = None
    for family, byte_name, io_name in families:
        byte_path, io_path = root / byte_name, root / io_name
        byte_exists, io_exists = byte_path.is_file(), io_path.is_file()
        if byte_exists != io_exists:
            raise ServiceError(f"client cgroup v1 {family} counter pair is incomplete")
        if byte_exists:
            selected = family, byte_path, io_path
            break
    if selected is None:
        raise ServiceError("client cgroup v1 authoritative I/O counter files are absent")
    family, byte_path, io_path = selected
    byte_rows = _v1_device_rows(byte_path, "client cgroup v1 service bytes")
    io_rows = _v1_device_rows(io_path, "client cgroup v1 serviced")
    byte_present = BLKIO_DEVICE_MAJOR_MINOR in byte_rows
    io_present = BLKIO_DEVICE_MAJOR_MINOR in io_rows
    if byte_present != io_present:
        raise ServiceError("client cgroup v1 target-device counter-file coverage differs")
    absence_as_zero = not byte_present
    if absence_as_zero:
        byte_row = {"read": 0, "write": 0, "sync": 0, "async": 0}
        io_row = {"read": 0, "write": 0, "sync": 0, "async": 0}
    else:
        byte_row = _strict_v1_target(
            byte_rows, "client cgroup v1 service bytes", BLKIO_DEVICE_MAJOR_MINOR,
        )
        io_row = _strict_v1_target(
            io_rows, "client cgroup v1 serviced", BLKIO_DEVICE_MAJOR_MINOR,
        )
    return {
        "counters": {
            "rbytes": byte_row["read"], "wbytes": byte_row["write"],
            "rios": io_row["read"], "wios": io_row["write"],
            "dbytes": 0, "dios": 0,
        },
        "sync_async": {
            "sync_bytes": byte_row["sync"], "async_bytes": byte_row["async"],
            "sync_ios": io_row["sync"], "async_ios": io_row["async"],
        },
        "absence_as_zero": absence_as_zero,
        "authority": {
            "cgroup_version": 1,
            "counter_family": family,
            "target_device_major_minor": BLKIO_DEVICE_MAJOR_MINOR,
            "counter_files": [str(byte_path), str(io_path)],
        },
    }


def _target_io_snapshot(
    client_id: str, pid: int, *,
    proc_root: Path = Path("/proc"),
    cgroup_root: Path = Path("/sys/fs/cgroup"),
) -> dict[str, Any]:
    version, relative = _container_cgroup(client_id, pid, proc_root)
    if version == 2:
        root = _contained_cgroup_path(cgroup_root, relative)
        return _v2_target_snapshot(root / "io.stat")
    blkio_root = cgroup_root / "blkio"
    derived = _contained_cgroup_path(blkio_root, relative)
    preferred = blkio_root / "docker" / client_id
    if preferred.is_dir() and not preferred.is_symlink():
        if preferred.resolve() != derived.resolve():
            raise ServiceError("client cgroup v1 preferred and membership authorities disagree")
        derived = preferred
    return _v1_target_snapshot(derived)


def _io_delta(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, int]:
    if before.get("authority") != after.get("authority"):
        raise ServiceError("client target-device cgroup I/O authority changed")
    before_counters = before.get("counters")
    after_counters = after.get("counters")
    if not isinstance(before_counters, Mapping) or not isinstance(after_counters, Mapping):
        raise ServiceError("client target-device cgroup I/O snapshot is malformed")
    expected = {"rbytes", "wbytes", "rios", "wios", "dbytes", "dios"}
    if set(before_counters) != expected or set(after_counters) != expected:
        raise ServiceError("client target-device cgroup I/O counter coverage differs")
    result = {
        key: int(after_counters[key]) - int(before_counters[key])
        for key in sorted(expected)
    }
    if any(value < 0 for value in result.values()):
        raise ServiceError("client target-device cgroup I/O counters regressed")
    return result


def _metadata_digest(root: Path) -> str:
    rows: list[dict[str, Any]] = []
    for path in sorted([root, *root.rglob("*")]):
        value = path.lstat()
        if path.is_symlink():
            raise ServiceError("symlink prohibited in MySQL datadir envelope")
        rows.append({"path": path.relative_to(root).as_posix(), "uid": value.st_uid,
                     "gid": value.st_gid, "mode": stat.S_IMODE(value.st_mode),
                     "kind": stat.S_IFMT(value.st_mode)})
    return hashlib.sha256(base.canonical(rows)).hexdigest()


def _normalize_target(image_id: str, target: Path) -> dict[str, Any]:
    script = (
        "test -f /mysql/ibdata1; "
        "test -z \"$(find /mysql -xdev -type l -pCHANGE_ME -quit)\"; "
        "test -z \"$(find /mysql -xdev \\( -type s -o -type b -o -type c -o -type p \\) -pCHANGE_ME -quit)\"; "
        "find /mysql -xdev -type f -exec chown 999:999 {} + -exec chmod 0600 {} +; "
        "find /mysql -xdev -depth -type d -exec chown 999:999 {} + -exec chmod 0700 {} +; "
        "test -z \"$(find /mysql -xdev -type d -pCHANGE_ME '%U:%G:%m\\n' | awk '$0 != \"999:999:700\" {print; exit}')\"; "
        "test -z \"$(find /mysql -xdev -type f -pCHANGE_ME '%U:%G:%m\\n' | awk '$0 != \"999:999:600\" {print; exit}')\"; "
        "d=$(find /mysql -xdev -type d | wc -l | tr -d '[:space:]'); "
        "f=$(find /mysql -xdev -type f | wc -l | tr -d '[:space:]'); "
        "printf '{\"schema_version\":\"mysql_tpcc_target_datadir_permission_normalization_v1\",\"target_only\":true,\"uid\":999,\"gid\":999,\"directory_mode\":\"0700\",\"regular_file_mode\":\"0600\",\"directory_count\":%s,\"regular_file_count\":%s,\"ibdata1_verified\":true}\n' \"$d\" \"$f\""
    )
    completed = subprocess.run([
        "docker", "run", "--rm", "--network", "none", "--read-only",
        "--user", "0:0", "--cap-drop", "ALL", "--cap-add", "CHOWN",
        "--cap-add", "FOWNER", "--cap-add", "DAC_OVERRIDE",
        "--mount", f"type=bind,src={target},dst=/mysql",
        "--entrypoint", "/bin/sh", image_id, "-ceu", script,
    ], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if completed.returncode:
        raise ServiceError("target-only datadir normalization failed: " + completed.stderr[-500:])
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ServiceError("target normalization evidence is invalid") from exc
    if not isinstance(value, dict) or value.get("target_only") is not True or value.get("uid") != MYSQL_UID or value.get("gid") != MYSQL_GID or value.get("directory_mode") != MYSQL_DIR_MODE or value.get("regular_file_mode") != MYSQL_FILE_MODE or value.get("ibdata1_verified") is not True:
        raise ServiceError("target normalization readback differs")
    return value


def _verify_client_private_config(config_dir: Path) -> dict[str, Any]:
    authority_path = config_dir.parent / "config_materialization.json"
    authority = base.load(authority_path)
    if (
        config_dir.is_symlink()
        or not config_dir.is_dir()
        or config_dir.stat().st_uid != CLIENT_UID
        or config_dir.stat().st_gid != CLIENT_GID
        or stat.S_IMODE(config_dir.stat().st_mode) != 0o700
    ):
        raise ServiceError("client private config directory ownership/mode differs")
    files: dict[str, dict[str, Any]] = {}
    for name in ("mysql.cnf", "tpcc.xml"):
        path = config_dir / name
        value = path.stat()
        if (
            path.is_symlink()
            or not path.is_file()
            or value.st_uid != CLIENT_UID
            or value.st_gid != CLIENT_GID
            or stat.S_IMODE(value.st_mode) != 0o600
        ):
            raise ServiceError("client private config file ownership/mode differs: " + name)
        files[name] = {
            "sha256": base.sha256(path),
            "bytes": value.st_size,
            "mode": "0600",
            "uid": CLIENT_UID,
            "gid": CLIENT_GID,
        }
    authority_files = authority.get("files")
    if (
        authority.get("schema_version")
        != "mysql_tpcc_attempt_config_materialization_v1"
        or authority.get("status") != "PASS_CONFIG_MATERIALIZED"
        or authority.get("host_effective_uid_gid") != [0, 0]
        or authority.get("config_dir") != str(config_dir)
        or authority.get("config_dir_uid") != CLIENT_UID
        or authority.get("config_dir_gid") != CLIENT_GID
        or authority.get("config_dir_mode") != "0700"
        or authority.get("ownership_transactional_before_publish") is not True
        or base.SHA_RE.fullmatch(
            str(authority.get("credential_provisioning_authority_sha256") or "")
        ) is None
        or not isinstance(authority_files, Mapping)
        or set(authority_files) != {"mysql.cnf", "tpcc.xml"}
        or any(
            not isinstance(authority_files[name], Mapping)
            or authority_files[name].get("sha256") != files[name]["sha256"]
            or authority_files[name].get("bytes") != files[name]["bytes"]
            or authority_files[name].get("mode") != "0600"
            or authority_files[name].get("uid") != CLIENT_UID
            or authority_files[name].get("gid") != CLIENT_GID
            or base.SHA_RE.fullmatch(
                str(authority_files[name].get("template_sha256") or "")
            ) is None
            for name in ("mysql.cnf", "tpcc.xml")
        )
        or authority_path.stat().st_uid != CLIENT_UID
        or authority_path.stat().st_gid != CLIENT_GID
        or stat.S_IMODE(authority_path.stat().st_mode) != 0o600
    ):
        raise ServiceError("client config materialization authority differs")
    return {
        "configured_user": CLIENT_USER,
        "uid": CLIENT_UID,
        "gid": CLIENT_GID,
        "directory_mode": "0700",
        "ownership_transactional_before_publish": True,
        "host_effective_uid_gid": [0, 0],
        "credential_provisioning_authority_sha256":
            authority["credential_provisioning_authority_sha256"],
        "authority_path": str(authority_path),
        "authority_sha256": base.sha256(authority_path),
        "authority_mode": "0600",
        "files": files,
    }


def _expect_client_inspect(
    inspect: Mapping[str, Any], plan: Mapping[str, Any]
) -> dict[str, Any]:
    identity = base._expect_inspect(inspect, plan, "client")
    config = inspect.get("Config") or {}
    state = inspect.get("State") or {}
    pid = int(state.get("Pid") or 0)
    if (
        config.get("User") != CLIENT_USER
        or identity.get("pid") != pid
        or identity.get("process_start_ticks") != base._proc_start_ticks(pid)
    ):
        raise ServiceError("client configured user/live PID identity differs")
    return {**identity, "configured_user": CLIENT_USER}


def _verify_measurement_bridge(plan: Mapping[str, Any]) -> dict[str, Any]:
    bridge = Path(plan["measurement_bridge"]["host_path"])
    authority_path = bridge.parent / "measurement_bridge_authority.json"
    value = base.load(authority_path)
    live = base._verify_host_bridge(plan)
    stat_value = bridge.stat()
    filesystem = os.statvfs(bridge)
    live_size_bytes = filesystem.f_frsize * filesystem.f_blocks
    options_result = base._run([
        "findmnt", "--noheadings", "--output", "OPTIONS",
        "--target", str(bridge),
    ])
    options = set(options_result.stdout.strip().split(","))
    device = (
        f"{os.major(stat_value.st_dev)}:{os.minor(stat_value.st_dev)}"
    )
    expected = {
        "schema_version": "mysql_tpcc_measurement_bridge_authority_v1",
        "status": "PASS",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "bundle_id": plan["bundle_id"],
        "attempt_id": plan["attempt_id"],
        "host_effective_uid_gid": [0, 0],
        "host_path": str(bridge),
        "container_path": "/measurement",
        "filesystem": "tmpfs",
        "mount_source": value.get("mount_source"),
        "mount_target": str(bridge),
        "device_major_minor": device,
        "size_bytes": 2 * 1024**3,
        "uid": CLIENT_UID,
        "gid": CLIENT_GID,
        "mode": "0700",
        "nosuid": True,
        "nodev": True,
        "noexec": True,
        "rootless_client_writable_by_owner_mode": True,
        "nested_readonly_plugin_mount_required": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    }
    if (
        value != expected
        or live.get("host_path") != str(bridge)
        or live.get("container_path") != "/measurement"
        or live.get("filesystem") != "tmpfs"
        or live.get("mount_target") != str(bridge)
        or live.get("device_major_minor") != device
        or live.get("mode") != "0o700"
        or stat_value.st_uid != CLIENT_UID
        or stat_value.st_gid != CLIENT_GID
        or stat.S_IMODE(stat_value.st_mode) != 0o700
        or live_size_bytes != 2 * 1024**3
        or device == base.DEVICE_MAJOR_MINOR
        or not {"nosuid", "nodev", "noexec"}.issubset(options)
        or authority_path.stat().st_uid != CLIENT_UID
        or authority_path.stat().st_gid != CLIENT_GID
        or stat.S_IMODE(authority_path.stat().st_mode) != 0o600
    ):
        raise ServiceError("measurement bridge root authority/readback differs")
    return {
        **expected,
        "authority_path": str(authority_path),
        "authority_sha256": base.sha256(authority_path),
        "authority_file_uid": CLIENT_UID,
        "authority_file_gid": CLIENT_GID,
        "authority_file_mode": "0600",
        "live_readback_verified": True,
    }


def _client_readability_smoke(
    plan: Mapping[str, Any], client: Mapping[str, Any]
) -> dict[str, Any]:
    code = (
        "import hashlib,json,os\n"
        "paths=json.loads(os.environ['MYSQL_TPCC_READABILITY_PATHS'])\n"
        "rows={}\n"
        "for name,path in sorted(paths.items()):\n"
        "  with open(path,'rb',buffering=0) as stream:\n"
        "    first=stream.read(1)\n"
        "    if not first: raise RuntimeError('empty readability target: '+name)\n"
        "    digest=None\n"
        "    if name in ('plugin','tpcc_config','mysql_defaults'):\n"
        "      h=hashlib.sha256(); h.update(first)\n"
        "      for block in iter(lambda:stream.read(1048576),b''): h.update(block)\n"
        "      digest=h.hexdigest()\n"
        "  rows[name]={'path':path,'bytes_read':1,'sha256':digest}\n"
        "probe='/measurement/.client-write-smoke-%d'%os.getpid()\n"
        "fd=os.open(probe,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)\n"
        "try:\n"
        "  os.write(fd,b'rootless-client-write-smoke'); os.fsync(fd)\n"
        "finally: os.close(fd)\n"
        "probe_stat=os.stat(probe)\n"
        "measurement_write={'directory':'/measurement','bytes':27,"
        "'file_uid':probe_stat.st_uid,'file_gid':probe_stat.st_gid,"
        "'file_mode':format(probe_stat.st_mode & 0o777,'04o')}\n"
        "os.unlink(probe)\n"
        "cap_eff=None\n"
        "with open('/proc/self/status',encoding='ascii') as stream:\n"
        "  for line in stream:\n"
        "    if line.startswith('CapEff:'): cap_eff=line.split()[1]\n"
        "print(json.dumps({'effective_uid':os.geteuid(),'effective_gid':os.getegid(),"
        "'effective_capabilities_hex':cap_eff,'paths':rows,"
        "'measurement_write':measurement_write},sort_keys=True))\n"
    )
    completed = base._run([
        "docker", "exec",
        "--env", "MYSQL_TPCC_READABILITY_PATHS="
        + json.dumps(CLIENT_READABILITY_PATHS, sort_keys=True),
        plan["client"]["name"],
        base.PYTHON_DESTINATION + "/bin/python3.11", "-c", code,
    ], check=False)
    if completed.returncode:
        raise ServiceError(
            "client default-user read-only mount smoke failed: "
            + completed.stderr[-500:]
        )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ServiceError("client readability smoke evidence is invalid") from exc
    paths = value.get("paths") if isinstance(value, Mapping) else None
    measurement_write = (
        value.get("measurement_write") if isinstance(value, Mapping) else None
    )
    private = plan.get("client_private_config") or {}
    private_files = private.get("files") or {}
    if (
        value.get("effective_uid") != CLIENT_UID
        or value.get("effective_gid") != CLIENT_GID
        or value.get("effective_capabilities_hex") != "0000000000000000"
        or not isinstance(paths, Mapping)
        or set(paths) != set(CLIENT_READABILITY_PATHS)
        or any(
            paths[name].get("path") != path
            or paths[name].get("bytes_read") != 1
            for name, path in CLIENT_READABILITY_PATHS.items()
        )
        or paths["plugin"].get("sha256") != BENCHBASE_PLUGIN_SHA256
        or paths["tpcc_config"].get("sha256")
        != (private_files.get("tpcc.xml") or {}).get("sha256")
        or paths["mysql_defaults"].get("sha256")
        != (private_files.get("mysql.cnf") or {}).get("sha256")
        or client.get("configured_user") != CLIENT_USER
        or measurement_write != {
            "directory": "/measurement",
            "bytes": 27,
            "file_uid": CLIENT_UID,
            "file_gid": CLIENT_GID,
            "file_mode": "0600",
        }
    ):
        raise ServiceError("client default-user read-only mount smoke differs")
    return {
        "schema_version": "mysql_tpcc_client_readability_smoke_v1",
        "status": "PASS",
        "configured_user": CLIENT_USER,
        "effective_uid": CLIENT_UID,
        "effective_gid": CLIENT_GID,
        "effective_capabilities_hex": "0000000000000000",
        "client_container_id": client["container_id"],
        "client_pid": client["pid"],
        "client_process_start_ticks": client["process_start_ticks"],
        "measurement_write": dict(measurement_write),
        "paths": dict(paths),
    }


def prepare_physical_copy(attempt_id: str, image_id: str, output: Path) -> dict[str, Any]:
    _require_host_root()
    if not image_id.startswith("sha256:"):
        raise ServiceError("exact image ID required for target normalization")
    if base.socket.gethostname().split(".")[0] != base.HOST or base.TOKEN_RE.fullmatch(attempt_id) is None:
        raise ServiceError("v11 copy host/attempt binding differs")
    source, attempt_root = Path(base.TEMPLATE), Path(base.RUNTIME_ROOT) / attempt_id
    target_root, target = attempt_root / "datadir", attempt_root / "datadir" / MYSQL_DATADIR_RELATIVE
    if output.resolve() != attempt_root / "physical_copy_authority.json" or target_root.exists() or attempt_root.exists():
        raise ServiceError("attempt target already exists or output path differs")
    if not source.is_dir() or source.is_symlink() or not (source / MYSQL_DATADIR_RELATIVE / "ibdata1").is_file():
        raise ServiceError("template inner MySQL datadir/ibdata1 is absent")
    if not Path(base.MOUNT).is_mount() or base._device_major_minor(source) != base.DEVICE_MAJOR_MINOR:
        raise ServiceError("frozen SSD mount differs")
    source_metadata_before = _metadata_digest(source)
    source_tree, source_files, source_bytes = base._tree_identity(source)
    # mkdir(mode=0700) is atomic with respect to visibility; chmod immediately
    # enforces the exact mode even if a restrictive/odd umask is configured.
    # Later credential/config materialization refuses group/world-readable roots.
    attempt_root.mkdir(parents=True, mode=0o700, exist_ok=False)
    os.chmod(attempt_root, 0o700)
    if stat.S_IMODE(attempt_root.stat().st_mode) != 0o700:
        raise ServiceError("attempt root mode must be exactly 0700")
    completed = base._run(["cp", "-a", "--", str(source) + "/.", str(target_root)], check=False)
    if completed.returncode:
        raise ServiceError("physical cp failed: " + completed.stderr[-500:])
    base._run(["sync"])
    target_tree, target_files, target_bytes = base._tree_identity(target_root)
    if (target_tree, target_files, target_bytes) != (source_tree, source_files, source_bytes):
        raise ServiceError("outer copied template identity differs before normalization")
    source_extents = {p.relative_to(source).as_posix(): base._fiemap_extents(p) for p in source.rglob("*") if p.is_file()}
    target_extents = {p.relative_to(target_root).as_posix(): base._fiemap_extents(p) for p in target_root.rglob("*") if p.is_file()}
    extent_audit = base._verify_disjoint_extent_maps(source_extents, target_extents)
    normalization = _normalize_target(image_id, target)
    source_metadata_after = _metadata_digest(source)
    if source_metadata_before != source_metadata_after:
        raise ServiceError("source metadata changed during target-only normalization")
    authority = {
        "schema_version": "mysql_tpcc_physical_copy_authority_v2", "status": "PASS",
        "host_effective_uid_gid": [0, 0],
        "attempt_id": attempt_id, "source_template": str(source), "mount": base.MOUNT,
        "target_copy_root": str(target_root), "target_datadir": str(target),
        "mysql_datadir_relative": "datadir", "device_major_minor": base.DEVICE_MAJOR_MINOR,
        "copy_argv": ["cp", "-a", "--", str(source) + "/.", str(target_root)],
        "full_physical_copy": True, "reflink_used": False, "overlay_used": False,
        "tree_identity_sha256_before_target_normalization": source_tree,
        "regular_file_count_before_target_normalization": source_files,
        "total_regular_file_bytes_before_target_normalization": source_bytes,
        "extent_audit": extent_audit, "target_permission_normalization": normalization,
        "source_metadata_sha256_before": source_metadata_before,
        "source_metadata_sha256_after": source_metadata_after,
        "accepted_round_authorized": False, "formal_launch_authorized": False,
    }
    base.atomic_json(output, authority)
    return authority


def _verify_physical_copy(plan: Mapping[str, Any]) -> dict[str, Any]:
    datadir = Path(plan["database"]["datadir"])
    value = base.load(datadir.parent.parent / "physical_copy_authority.json")
    norm = value.get("target_permission_normalization") or {}
    if (value.get("schema_version") != "mysql_tpcc_physical_copy_authority_v2" or value.get("status") != "PASS" or value.get("host_effective_uid_gid") != [0, 0] or value.get("target_datadir") != str(datadir) or value.get("mysql_datadir_relative") != "datadir" or value.get("full_physical_copy") is not True or value.get("reflink_used") is not False or value.get("overlay_used") is not False or value.get("source_metadata_sha256_before") != value.get("source_metadata_sha256_after") or norm.get("target_only") is not True or norm.get("uid") != MYSQL_UID or norm.get("gid") != MYSQL_GID or norm.get("directory_mode") != MYSQL_DIR_MODE or norm.get("regular_file_mode") != MYSQL_FILE_MODE or norm.get("ibdata1_verified") is not True):
        raise ServiceError("v11 physical inner-datadir authority differs")
    return value


def _verify_blkio_v1(plan: Mapping[str, Any], inspect: Mapping[str, Any]) -> dict[str, Any]:
    datadir = Path(plan["database"]["datadir"])
    path = datadir.parent.parent / "blkio_throttle_authority.json"
    value = base.load(path)
    state = inspect.get("State") or {}
    container_id = str(inspect.get("Id") or "")
    pid = int(state.get("Pid") or 0)
    expected_cgroup = "/sys/fs/cgroup/blkio/docker/" + container_id
    if (
        value.get("schema_version") != "mysql_tpcc_blkio_v1_throttle_authority_v1"
        or value.get("status") != "PASS"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("bundle_id") != plan["bundle_id"]
        or value.get("attempt_id") != plan["attempt_id"]
        or value.get("database_container") != plan["database"]["name"]
        or value.get("database_container_id") != container_id
        or value.get("database_pid") != pid
        or value.get("host_effective_uid_gid") != [0, 0]
        or value.get("cgroup_version") != "v1"
        or value.get("cgroup_path") != expected_cgroup
        or value.get("device_major_minor") != BLKIO_DEVICE_MAJOR_MINOR
        or value.get("read_bps") != BLKIO_BPS
        or value.get("write_bps") != BLKIO_BPS
        or value.get("read_throttle_file")
        != expected_cgroup + "/blkio.throttle.read_bps_device"
        or value.get("write_throttle_file")
        != expected_cgroup + "/blkio.throttle.write_bps_device"
        or value.get("docker_hostconfig_readback_not_authoritative") is not True
        or value.get("database_or_client_started") is not True
        or value.get("workload_started") is not False
        or value.get("formal_launch_authorized") is not False
        or value.get("accepted_round_authorized") is not False
    ):
        raise ServiceError("sealed cgroup-v1 blkio throttle authority differs")
    return value


def build_plan(**kwargs: Any) -> dict[str, Any]:
    _require_host_root()
    attempt_id = str(kwargs["attempt_id"])
    outer = Path(base.RUNTIME_ROOT) / attempt_id / "datadir"
    inner = outer / MYSQL_DATADIR_RELATIVE
    if Path(kwargs["datadir"]).resolve() != inner.resolve():
        raise ServiceError("v11 plan must bind the copied inner MySQL datadir")
    forwarded = dict(kwargs); forwarded["datadir"] = outer
    plan = base.build_plan(**forwarded)
    plan["schema_version"] = "mysql_tpcc_node04_adopted_service_plan_v2"
    plan["host_effective_uid_gid"] = [0, 0]
    plan["database"]["datadir"] = str(inner)
    old = f"type=bind,src={outer},dst=/var/lib/mysql"
    new = f"type=bind,src={inner},dst=/var/lib/mysql"
    plan["database"]["create_argv"] = [new if x == old else x for x in plan["database"]["create_argv"]]
    plan["physical_copy_authority"] = {"schema_version": "mysql_tpcc_physical_copy_authority_v2", "target_datadir": str(inner), "target_only_normalization": True}
    plugin_source = Path(plan["bundle_path"]) / BENCHBASE_PLUGIN_RELATIVE
    plugin = plugin_source / "plugin.xml"
    if (
        not plugin_source.is_dir()
        or plugin_source.is_symlink()
        or plugin.is_symlink()
        or not plugin.is_file()
        or base.sha256(plugin) != BENCHBASE_PLUGIN_SHA256
    ):
        raise ServiceError("manifest-bound production BenchBase plugin is absent")
    create = plan["client"]["create_argv"]
    image_index = create.index(plan["expected_image_id"])
    create[image_index:image_index] = [
        "--user", CLIENT_USER,
        "--mount",
        f"type=bind,src={plugin_source},dst={BENCHBASE_PLUGIN_DESTINATION},readonly",
    ]
    plan["client"]["configured_user"] = CLIENT_USER
    plan["client_private_config"] = _verify_client_private_config(
        Path(kwargs["config_dir"])
    )
    plan["measurement_bridge_authority"] = _verify_measurement_bridge(plan)
    plan["benchbase_plugin_mount"] = {
        "source": str(plugin_source),
        "destination": BENCHBASE_PLUGIN_DESTINATION,
        "plugin_relative": "plugin.xml",
        "plugin_sha256": BENCHBASE_PLUGIN_SHA256,
        "read_only": True,
    }
    return plan


def adopt(plan: Mapping[str, Any]) -> dict[str, Any]:
    _require_host_root()
    _require_root_plan(plan)
    _verify_physical_copy(plan)
    runtimes = base.verify_runtime_trees(Path(plan["bundle_path"]))
    bridge = _verify_measurement_bridge(plan)
    db, client = base._inspect(plan["database"]["name"]), base._inspect(plan["client"]["name"])
    if db is None or client is None:
        raise ServiceError("adoption requires both pre-existing containers")
    blkio = _verify_blkio_v1(plan, db)
    database, client_identity = base._expect_inspect(db, plan, "database", trusted_v1_blkio=True), _expect_client_inspect(client, plan)
    plugin_contract = plan.get("benchbase_plugin_mount") or {}
    plugin_mount = base._mount_map(client).get(BENCHBASE_PLUGIN_DESTINATION) or {}
    plugin = Path(str(plugin_contract.get("source") or "")) / "plugin.xml"
    if (
        plugin_contract.get("destination") != BENCHBASE_PLUGIN_DESTINATION
        or plugin_contract.get("plugin_relative") != "plugin.xml"
        or plugin_contract.get("plugin_sha256") != BENCHBASE_PLUGIN_SHA256
        or plugin_contract.get("read_only") is not True
        or plugin_mount.get("Source") != plugin_contract.get("source")
        or plugin_mount.get("RW") is not False
        or plugin.is_symlink()
        or not plugin.is_file()
        or base.sha256(plugin) != BENCHBASE_PLUGIN_SHA256
    ):
        raise ServiceError("production BenchBase plugin mount differs")
    client_readability = _client_readability_smoke(plan, client_identity)
    result = {"schema_version": "mysql_tpcc_node04_adopted_service_authority_v2", "status": "PASS", "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True, "bundle_id": plan["bundle_id"], "attempt_id": plan["attempt_id"], "handoff_token_sha256": plan["handoff_token_sha256"], "host": base.HOST, "host_effective_uid_gid": [0, 0], "database": {**database, "datadir": plan["database"]["datadir"], "device": base.DEVICE, "device_major_minor": base.DEVICE_MAJOR_MINOR, "mysql_port": base.PORT}, "client": {**client_identity, "root_filesystem_read_only": True, "network_mode": "host", "target_mysql_device_write_prohibited": True}, "client_private_config": dict(plan["client_private_config"]), "client_readability_smoke": client_readability, "blkio_v1_throttle": blkio, "benchbase_plugin_mount": dict(plugin_contract), "measurement_bridge": bridge, "runtime_tree_readback": runtimes, "same_live_database_required": True, "container_recreated": False, "database_restarted": False, "datadir_replaced": False, "service_ready": True, "formal_launch_authorized": False, "accepted_round_authorized": False}
    result["live_readback_sha256"] = base.sha_bytes(base.canonical(result)); return result


def prefault(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Prefault through the v2 same-live adoption and retain the zero-I/O gate."""
    service = adopt(plan)
    client_inspect = base._inspect(plan["client"]["name"])
    if client_inspect is None:
        raise ServiceError("prefault requires the live client container")
    destinations = base._target_mount_destinations(client_inspect)
    python = base.PYTHON_DESTINATION + "/bin/python3.11"
    code = (
        "import os,sys\n"
        "for root in sys.argv[1:]:\n"
        "  for current,dirs,files in os.walk(root,followlinks=False):\n"
        "    dirs.sort(); files.sort()\n"
        "    for name in files:\n"
        "      path=os.path.join(current,name)\n"
        "      if os.path.islink(path): continue\n"
        "      with open(path,'rb',buffering=0) as stream:\n"
        "        while stream.read(1048576): pass\n"
    )
    client_id = service["client"]["container_id"]
    client_pid = service["client"]["pid"]
    before_prefault = _target_io_snapshot(client_id, client_pid)
    if any(before_prefault["counters"].values()) or (
        before_prefault["sync_async"] is not None
        and any(before_prefault["sync_async"].values())
    ):
        raise ServiceError("client target-device I/O baseline is not zero before prefault")
    if destinations:
        base._run([
            "docker", "exec", plan["client"]["name"],
            python, "-c", code, *destinations,
        ])
    after_prefault = _target_io_snapshot(client_id, client_pid)
    if any(after_prefault["counters"].values()) or (
        after_prefault["sync_async"] is not None
        and any(after_prefault["sync_async"].values())
    ):
        raise ServiceError("client target-device I/O baseline is not zero after prefault")
    time.sleep(2)
    quiet = _target_io_snapshot(client_id, client_pid)
    quiet_delta = _io_delta(after_prefault, quiet)
    if any(quiet_delta.values()):
        raise ServiceError("client target-device IO did not quiesce after prefault")
    return {
        "schema_version": "mysql_tpcc_client_target_device_prefault_v1",
        "status": "PASS_PREFAULT_ZERO_IO_BASELINE",
        "host_effective_uid_gid": [0, 0],
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "bundle_id": service["bundle_id"],
        "attempt_id": service["attempt_id"],
        "handoff_token_sha256": service["handoff_token_sha256"],
        "client_container_id": service["client"]["container_id"],
        "client_pid": service["client"]["pid"],
        "client_process_start_ticks":
            service["client"]["process_start_ticks"],
        "target_device_major_minor": base.DEVICE_MAJOR_MINOR,
        "prefaulted_target_mount_destinations": destinations,
        "before_prefault": before_prefault["counters"],
        "after_prefault": after_prefault["counters"],
        "real_window_baseline": quiet["counters"],
        "client_cgroup_io_snapshot_evidence": {
            "before_prefault": before_prefault,
            "after_prefault": after_prefault,
            "real_window_baseline": quiet,
        },
        "quiet_window_seconds": 2,
        "quiet_window_delta": quiet_delta,
        "real_window_all_target_device_io_must_be_zero": True,
        "v2_same_live_adoption_verified": True,
        "accepted_round_authorized": False,
        "formal_launch_authorized": False,
    }


def start(plan: Mapping[str, Any]) -> dict[str, Any]:
    _require_host_root()
    _require_root_plan(plan)
    _verify_physical_copy(plan); _verify_measurement_bridge(plan)
    for role in ("database", "client"):
        current = base._inspect(plan[role]["name"])
        if current is None:
            base._run(plan[role]["create_argv"]); base._run(plan[role]["start_argv"])
        elif (current.get("State") or {}).get("Running") is not True:
            raise ServiceError("existing stopped container is not restartable")
    db = base._inspect(plan["database"]["name"])
    client = base._inspect(plan["client"]["name"])
    if db is None or client is None:
        raise ServiceError("started container disappeared")
    database_identity = base._expect_inspect(
        db, plan, "database", trusted_v1_blkio=True
    )
    client_identity = _expect_client_inspect(client, plan)
    return {"schema_version":"mysql_tpcc_node04_start_authority_v2","status":"PASS_CONTAINERS_STARTED_BLKIO_ADOPT_REQUIRED","bundle_id":plan["bundle_id"],"attempt_id":plan["attempt_id"],"host_effective_uid_gid":[0,0],"database":plan["database"]["name"],"client":plan["client"]["name"],"database_identity":database_identity,"client_identity":client_identity,"formal_launch_authorized":False,"accepted_round_authorized":False}


def bootstrap(plan: Mapping[str, Any]) -> dict[str, Any]:
    return start(plan)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("action", choices=("prepare-copy", "plan", "start", "bootstrap", "adopt", "prefault")); parser.add_argument("--bundle", type=Path); parser.add_argument("--image-id"); parser.add_argument("--config-dir", type=Path); parser.add_argument("--datadir", type=Path); parser.add_argument("--measurement-bridge", type=Path); parser.add_argument("--attempt-id"); parser.add_argument("--handoff-token-file", type=Path); parser.add_argument("--plan", type=Path); parser.add_argument("--output", type=Path, required=True); args = parser.parse_args(argv)
    if args.action == "prepare-copy": value = prepare_physical_copy(str(args.attempt_id), str(args.image_id), args.output)
    elif args.action == "plan": value = build_plan(bundle=args.bundle, image_id=args.image_id, config_dir=args.config_dir, datadir=args.datadir, measurement_bridge=args.measurement_bridge, attempt_id=args.attempt_id, handoff_token=args.handoff_token_file.read_text().strip())
    else:
        plan = base.load(args.plan)
        attempt_root = Path(plan["database"]["datadir"]).parent.parent
        expected_output = {
            "start": attempt_root / "start_authority.json",
            "bootstrap": attempt_root / "start_authority.json",
            "adopt": attempt_root / "adopted_service.json",
            "prefault": attempt_root / "prefault_zero_io.json",
        }[args.action]
        if args.output.resolve(strict=False) != expected_output or args.output.exists():
            raise ServiceError("phase output path differs or is already sealed")
        value = (start(plan) if args.action in ("start", "bootstrap") else (adopt(plan) if args.action == "adopt" else prefault(plan)))
    base.atomic_json(args.output, value); print(json.dumps({"status": "PASS", "output": str(args.output)}, sort_keys=True)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
