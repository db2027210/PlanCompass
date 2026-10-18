#!/usr/bin/env python3
"""Fail-closed local bootstrap/adoption contract for the node04 TPC-C lane.

This program is intended to be copied into an immutable TPC-C successor and
executed *on node04*.  It never uses SSH.  ``plan`` is offline. ``bootstrap``
may create the two containers once; it never removes or replaces a container.
``adopt`` only reads and validates an already running pair.

The service authority is deliberately not a launch or accepted-round
authority.  It only proves the identity and frozen resource envelope of the
database, read-only client and private tmpfs measurement bridge.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import stat
import struct
import subprocess
import time
from typing import Any, Mapping, Sequence


HOST = "node04"
DB_CPUS = "21-24,57-60"
DB_MEMS = "1"
CLIENT_CPUS = "8-15,44-51"
CLIENT_MEMS = "0"
DEVICE = "/dev/nvme3n1"
DEVICE_MAJOR_MINOR = "259:2"
MOUNT = "/mnt/mysql_tpcc_nvme3n1"
TEMPLATE = (
    MOUNT + "/anonymous/resource_adaptive_mysql/tpcc/template"
)
RUNTIME_ROOT = (
    MOUNT
    + "/anonymous/resource_adaptive_mysql/tpcc/"
    "runtime_algorithm_c_successor_20260729"
)
JDK_SOURCE = (
    "/home/anonymous/recost_deploy_20260721_v1/runtime/"
    "jdk-23.0.2-oracle-x64"
)
JDK_TREE_SHA256 = (
    "578395e883d31c692a1c01c37910d22dbdafbcf95e373c7e0f6776a26f5c844c"
)
JAVA_SHA256 = (
    "c8f339ba5d433ef5ecacef4de0640b0a35c99381804413bfbc24375a0410e51c"
)
PYTHON_TREE_SHA256 = (
    "3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89"
)
PYTHON_SOURCE = (
    RUNTIME_ROOT + "/runtime/python311-" + PYTHON_TREE_SHA256
)
PORT = 65106
MEMORY = 8 * 1024**3
DB_PIDS = 512
CLIENT_PIDS = 1024
DB_CPU_PERIOD = 100000
DB_CPU_QUOTA = 400000
CLIENT_CPU_PERIOD = 100000
CLIENT_CPU_QUOTA = 1600000
IO_BPS = 1205862400
TMPFS_BYTES = 2 * 1024**3
FS_IOC_FIEMAP = 0xC020660B
FIEMAP_EXTENT_LAST = 0x00000001
FIEMAP_EXTENT_SHARED = 0x00002000
FIEMAP_BATCH = 512
BUNDLE_DESTINATION = "/opt/mysql-tpcc-bundle"
JDK_DESTINATION = "/opt/mysql-tpcc-jdk"
PYTHON_DESTINATION = "/opt/mysql-tpcc-python"
CONFIG_DESTINATION = "/opt/mysql-tpcc-config"
MEASUREMENT_DESTINATION = "/measurement"
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9_.:-]{16,128}$")


class ServiceError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (
        json.dumps(
            value, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False
        )
        + "\n"
    ).encode("utf-8")


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ServiceError(f"regular JSON file required: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ServiceError(f"JSON object required: {path}")
    return value


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    sidecar = path.with_name(path.name + ".sha256")
    sidecar_temporary = sidecar.with_name(
        f".{sidecar.name}.{os.getpid()}.tmp"
    )
    payload = canonical(value)
    try:
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        digest = sha256(temporary)
        descriptor = os.open(
            sidecar_temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(f"{digest}  {path.name}\n".encode("ascii"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.replace(sidecar_temporary, sidecar)
    finally:
        for candidate in (temporary, sidecar_temporary):
            if candidate.exists():
                candidate.unlink()


def verify_bundle(bundle: Path) -> dict[str, Any]:
    bundle = bundle.resolve(strict=True)
    manifest = load(bundle / "bundle_manifest.json")
    unsigned = dict(manifest)
    bundle_id = unsigned.pop("bundle_id", None)
    if (
        bundle_id != bundle.name
        or SHA_RE.fullmatch(bundle.name) is None
        or sha_bytes(canonical(unsigned)) != bundle.name
        or manifest.get("scenario") != "tpcc"
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
    ):
        raise ServiceError("TPC-C successor bundle identity/scope differs")
    for relative, expected in (manifest.get("files") or {}).items():
        candidate = bundle / str(relative)
        if (
            candidate.is_symlink()
            or not candidate.is_file()
            or sha256(candidate) != expected
        ):
            raise ServiceError(f"bundle closure differs: {relative}")
    return manifest


def _runtime_tree_module(bundle: Path):
    helper = bundle / "authority/runtime_tree_content_identity_v3.py"
    spec = importlib.util.spec_from_file_location(
        "sealed_tpcc_runtime_tree_v3", helper
    )
    if spec is None or spec.loader is None:
        raise ServiceError("sealed runtime tree verifier cannot be loaded")
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def verify_runtime_trees(bundle: Path) -> dict[str, Any]:
    module = _runtime_tree_module(bundle)
    result: dict[str, Any] = {}
    specifications = (
        (
            "jdk", Path(JDK_SOURCE),
            bundle / "toolchain/jdk-23.0.2-tree-manifest.json",
            JDK_TREE_SHA256,
        ),
        (
            "python", Path(PYTHON_SOURCE),
            bundle / "toolchain/python-3.11.11-tree-manifest.json",
            PYTHON_TREE_SHA256,
        ),
    )
    for name, root, manifest_path, expected_tree in specifications:
        manifest = load(manifest_path)
        if (
            manifest.get("tree_content_sha256") != expected_tree
            or (manifest.get("source_provenance") or {}).get("source_root")
            != str(root)
        ):
            raise ServiceError(f"{name} portable manifest binding differs")
        try:
            readback = module.verify_tree_manifest(root, manifest)
        except Exception as exc:
            raise ServiceError(f"{name} live tree differs: {exc}") from exc
        if readback.get("tree_content_sha256") != expected_tree:
            raise ServiceError(f"{name} live tree content identity differs")
        result[name] = {
            **readback,
            "manifest_sha256": sha256(manifest_path),
            "mounted_read_only": True,
        }
    if sha256(Path(JDK_SOURCE) / "bin/java") != JAVA_SHA256:
        raise ServiceError("manifest-bound java binary differs")
    python = Path(PYTHON_SOURCE) / "bin/python3.11"
    if not python.is_file() or python.is_symlink():
        raise ServiceError("manifest-bound bin/python3.11 is absent")
    result["java_binary_sha256"] = JAVA_SHA256
    result["python_binary"] = str(python)
    return result


def verify_attempt(attempt_id: str, handoff_token: str) -> str:
    if TOKEN_RE.fullmatch(attempt_id) is None:
        raise ServiceError("attempt ID must be 16..128 safe characters")
    if TOKEN_RE.fullmatch(handoff_token) is None:
        raise ServiceError("handoff token must be 16..128 safe characters")
    if attempt_id != handoff_token:
        raise ServiceError("attempt ID and handoff token must be identical")
    return sha_bytes(handoff_token.encode("utf-8"))


def _device_major_minor(path: Path) -> str:
    device = path.stat().st_dev
    return f"{os.major(device)}:{os.minor(device)}"


def _tree_identity(root: Path) -> tuple[str, int, int]:
    records: list[dict[str, Any]] = []
    total_bytes = 0
    regular_files = 0
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise ServiceError(f"datadir symlink prohibited: {relative}")
        stat_value = path.stat()
        if path.is_dir():
            records.append({
                "path": relative, "type": "directory",
                "mode": stat_value.st_mode & 0o7777,
            })
        elif path.is_file():
            regular_files += 1
            total_bytes += stat_value.st_size
            records.append({
                "path": relative, "type": "regular",
                "mode": stat_value.st_mode & 0o7777,
                "size": stat_value.st_size, "sha256": sha256(path),
            })
        else:
            raise ServiceError(f"unsupported datadir entry: {relative}")
    return sha_bytes(canonical(records)), regular_files, total_bytes


def _fiemap_extents(path: Path) -> list[tuple[int, int, int]]:
    """Return physical [start,end) ranges and flags via Linux FIEMAP."""
    if path.stat().st_size == 0:
        return []
    result: list[tuple[int, int, int]] = []
    logical = 0
    while True:
        header = struct.pack(
            "<QQIIII", logical, (1 << 64) - 1 - logical,
            0, 0, FIEMAP_BATCH, 0,
        )
        buffer = bytearray(header + bytes(56 * FIEMAP_BATCH))
        with path.open("rb", buffering=0) as stream:
            fcntl.ioctl(stream.fileno(), FS_IOC_FIEMAP, buffer, True)
        _, _, _, mapped, _, _ = struct.unpack_from("<QQIIII", buffer, 0)
        if mapped == 0:
            break
        last_logical_end = logical
        finished = False
        for index in range(mapped):
            values = struct.unpack_from("<QQQQQIIII", buffer, 32 + 56 * index)
            extent_logical, physical, length = values[:3]
            flags = values[5]
            if length <= 0:
                raise ServiceError(f"invalid FIEMAP extent: {path}")
            result.append((physical, physical + length, flags))
            last_logical_end = extent_logical + length
            if flags & FIEMAP_EXTENT_LAST:
                finished = True
        if finished:
            break
        if last_logical_end <= logical:
            raise ServiceError(f"FIEMAP did not advance: {path}")
        logical = last_logical_end
    return result


def _verify_disjoint_extent_maps(
    source: Mapping[str, Sequence[tuple[int, int, int]]],
    target: Mapping[str, Sequence[tuple[int, int, int]]],
) -> dict[str, Any]:
    source_ranges: list[tuple[int, int, str]] = []
    target_ranges: list[tuple[int, int, str]] = []
    for relative, extents in source.items():
        for start, end, flags in extents:
            if flags & FIEMAP_EXTENT_SHARED:
                raise ServiceError(f"source has shared extent: {relative}")
            source_ranges.append((start, end, relative))
    for relative, extents in target.items():
        for start, end, flags in extents:
            if flags & FIEMAP_EXTENT_SHARED:
                raise ServiceError(f"target has shared extent: {relative}")
            target_ranges.append((start, end, relative))
    source_ranges.sort()
    target_ranges.sort()
    source_index = 0
    for target_start, target_end, target_name in target_ranges:
        while (
            source_index < len(source_ranges)
            and source_ranges[source_index][1] <= target_start
        ):
            source_index += 1
        index = source_index
        while index < len(source_ranges) and source_ranges[index][0] < target_end:
            source_start, source_end, source_name = source_ranges[index]
            if max(source_start, target_start) < min(source_end, target_end):
                raise ServiceError(
                    "source/target physical extents overlap: "
                    f"{source_name} and {target_name}"
                )
            index += 1
    return {
        "source_extent_count": len(source_ranges),
        "target_extent_count": len(target_ranges),
        "shared_extent_flags_seen": 0,
        "source_target_physical_overlap_count": 0,
        "fiemap_verified": True,
    }


def prepare_physical_copy(attempt_id: str, output: Path) -> dict[str, Any]:
    if socket.gethostname().split(".")[0] != HOST:
        raise ServiceError("physical copy is host-bound to node04")
    if TOKEN_RE.fullmatch(attempt_id) is None:
        raise ServiceError("attempt ID must be 16..128 safe characters")
    source = Path(TEMPLATE)
    attempt_root = Path(RUNTIME_ROOT) / attempt_id
    target = attempt_root / "datadir"
    expected_output = attempt_root / "physical_copy_authority.json"
    if output.resolve() != expected_output:
        raise ServiceError(f"copy authority output must be {expected_output}")
    if not source.is_dir() or source.is_symlink():
        raise ServiceError("sealed source template is absent or symlinked")
    mount_root = Path(MOUNT)
    if (
        not mount_root.is_mount()
        or _device_major_minor(mount_root) != DEVICE_MAJOR_MINOR
    ):
        raise ServiceError("configured TPC-C mount is not the frozen SSD mount")
    if target.exists() or target.is_symlink():
        raise ServiceError("target datadir already exists; refusing overwrite/reuse")
    attempt_root.mkdir(parents=True, exist_ok=False)
    if (
        _device_major_minor(source) != DEVICE_MAJOR_MINOR
        or _device_major_minor(attempt_root) != DEVICE_MAJOR_MINOR
    ):
        raise ServiceError("template/runtime root is not on the frozen SSD")
    source_tree, source_files, source_bytes = _tree_identity(source)
    # Coreutils 8.22 compatible. Its default is a real copy; FIEMAP below is
    # the authority, so no unsupported --reflink option is used or trusted.
    completed = _run(["cp", "-a", "--", str(source) + "/.", str(target)], check=False)
    if completed.returncode != 0:
        raise ServiceError(
            "physical cp failed; incomplete target retained for manual audit: "
            + completed.stderr.strip()
        )
    _run(["sync"])
    target_tree, target_files, target_bytes = _tree_identity(target)
    if (
        target_tree != source_tree
        or target_files != source_files
        or target_bytes != source_bytes
    ):
        raise ServiceError("copied datadir content/tree identity differs")
    source_extents = {
        path.relative_to(source).as_posix(): _fiemap_extents(path)
        for path in sorted(source.rglob("*")) if path.is_file()
    }
    target_extents = {
        path.relative_to(target).as_posix(): _fiemap_extents(path)
        for path in sorted(target.rglob("*")) if path.is_file()
    }
    extent_audit = _verify_disjoint_extent_maps(source_extents, target_extents)
    authority = {
        "schema_version": "mysql_tpcc_physical_copy_authority_v1",
        "status": "PASS",
        "attempt_id": attempt_id,
        "source_template": str(source),
        "mount": MOUNT,
        "target_datadir": str(target),
        "device_major_minor": DEVICE_MAJOR_MINOR,
        "copy_argv": ["cp", "-a", "--", str(source) + "/.", str(target)],
        "coreutils_8_22_compatible": True,
        "reflink_option_used": False,
        "full_physical_copy": True,
        "reflink_used": False,
        "overlay_used": False,
        "tree_identity_sha256": source_tree,
        "regular_file_count": source_files,
        "total_regular_file_bytes": source_bytes,
        "extent_audit": extent_audit,
        "accepted_round_authorized": False,
        "formal_launch_authorized": False,
    }
    atomic_json(output, authority)
    return authority


def _labels(bundle_id: str, attempt_id: str, token_sha: str, role: str) -> list[str]:
    values = {
        "ra.formal_scope": "mysql_only",
        "ra.cross_engine_comparison_prohibited": "true",
        "ra.scenario": "tpcc",
        "ra.bundle_id": bundle_id,
        "ra.attempt_id": attempt_id,
        "ra.handoff_token_sha256": token_sha,
        "ra.role": role,
    }
    result: list[str] = []
    for key in sorted(values):
        result += ["--label", f"{key}={values[key]}"]
    return result


def build_plan(
    *,
    bundle: Path,
    image_id: str,
    config_dir: Path,
    datadir: Path,
    measurement_bridge: Path,
    attempt_id: str,
    handoff_token: str,
) -> dict[str, Any]:
    manifest = verify_bundle(bundle)
    token_sha = verify_attempt(attempt_id, handoff_token)
    bundle = bundle.resolve()
    config_dir = config_dir.resolve()
    datadir = datadir.resolve()
    bridge = measurement_bridge.resolve()
    expected_prefix = Path(RUNTIME_ROOT) / attempt_id
    if datadir != expected_prefix / "datadir":
        raise ServiceError(
            f"datadir must be the attempt-owned path {expected_prefix / 'datadir'}"
        )
    if not config_dir.is_absolute() or not bridge.is_absolute():
        raise ServiceError("config and bridge paths must be absolute")
    config_readback = _verify_attempt_config(
        bundle, attempt_id, config_dir, manifest["bundle_id"]
    )
    if re.fullmatch(r"sha256:[0-9a-f]{64}", image_id or "") is None:
        raise ServiceError("full sha256 Docker image ID is required")
    db_name = f"ra_mysql_tpcc_v2_db_{attempt_id}"
    client_name = f"ra_mysql_tpcc_v2_client_{attempt_id}"
    db = [
        "docker", "create", "--name", db_name,
        "--cpuset-cpus", DB_CPUS, "--cpuset-mems", DB_MEMS,
        "--cpu-period", str(DB_CPU_PERIOD), "--cpu-quota", str(DB_CPU_QUOTA),
        "--memory", str(MEMORY), "--memory-swap", str(MEMORY),
        "--pids-limit", str(DB_PIDS),
        "--device-read-bps", f"{DEVICE}:{IO_BPS}",
        "--device-write-bps", f"{DEVICE}:{IO_BPS}",
        "--publish", f"127.0.0.1:{PORT}:3306",
        "--mount", f"type=bind,src={datadir},dst=/var/lib/mysql",
        "--restart", "no",
        *_labels(manifest["bundle_id"], attempt_id, token_sha, "database"),
        image_id,
        "--datadir=/var/lib/mysql",
        "--innodb-buffer-pool-size=4294967296",
    ]
    client = [
        "docker", "create", "--name", client_name,
        "--cpuset-cpus", CLIENT_CPUS, "--cpuset-mems", CLIENT_MEMS,
        "--cpu-period", str(CLIENT_CPU_PERIOD),
        "--cpu-quota", str(CLIENT_CPU_QUOTA),
        "--memory", str(MEMORY), "--memory-swap", str(MEMORY),
        "--pids-limit", str(CLIENT_PIDS),
        "--read-only", "--network", "host",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--log-driver", "none", "--restart", "no",
        "--mount",
        f"type=bind,src={bundle},dst={BUNDLE_DESTINATION},readonly",
        "--mount",
        f"type=bind,src={JDK_SOURCE},dst={JDK_DESTINATION},readonly",
        "--mount",
        f"type=bind,src={PYTHON_SOURCE},dst={PYTHON_DESTINATION},readonly",
        "--mount",
        f"type=bind,src={config_dir},dst={CONFIG_DESTINATION},readonly",
        "--mount",
        f"type=bind,src={bridge},dst={MEASUREMENT_DESTINATION}",
        "--entrypoint", PYTHON_DESTINATION + "/bin/python3.11",
        *_labels(manifest["bundle_id"], attempt_id, token_sha, "client"),
        image_id, "-c", "import time; time.sleep(315360000)",
    ]
    return {
        "schema_version": "mysql_tpcc_node04_adopted_service_plan_v1",
        "status": "PLAN_NOT_AUTHORITY",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "bundle_id": manifest["bundle_id"],
        "bundle_path": str(bundle),
        "config_path": str(config_dir),
        "config_materialization_sha256": sha_bytes(canonical(config_readback)),
        "attempt_id": attempt_id,
        "handoff_token_sha256": token_sha,
        "host": HOST,
        "expected_image_id": image_id,
        "database": {
            "name": db_name,
            "create_argv": db,
            "start_argv": ["docker", "start", db_name],
            "datadir": str(datadir),
            "device": DEVICE,
            "device_major_minor": DEVICE_MAJOR_MINOR,
        },
        "client": {
            "name": client_name,
            "create_argv": client,
            "start_argv": ["docker", "start", client_name],
        },
        "measurement_bridge": {
            "host_path": str(bridge),
            "container_path": MEASUREMENT_DESTINATION,
            "filesystem": "tmpfs",
            "size_bytes": TMPFS_BYTES,
            "private_attempt_directory": True,
        },
        "container_replacement_prohibited": True,
        "accepted_round_authorized": False,
        "formal_launch_authorized": False,
    }


def _verify_attempt_config(
    bundle: Path, attempt_id: str, config_dir: Path, bundle_id: str
) -> dict[str, Any]:
    if config_dir.is_symlink() or not config_dir.is_dir():
        raise ServiceError("attempt config directory is absent or symlinked")
    if stat.S_IMODE(config_dir.stat().st_mode) != 0o700:
        raise ServiceError("attempt config directory mode must be 0700")
    readback_path = config_dir.parent / "config_materialization.json"
    value = load(readback_path)
    files = value.get("files")
    if (
        value.get("schema_version")
        != "mysql_tpcc_attempt_config_materialization_v1"
        or value.get("status") != "PASS_CONFIG_MATERIALIZED"
        or value.get("bundle_id") != bundle_id
        or value.get("attempt_id") != attempt_id
        or value.get("config_dir") != str(config_dir)
        or value.get("config_dir_mode") != "0700"
        or value.get("config_dir_uid") != config_dir.stat().st_uid
        or value.get("config_dir_gid") != config_dir.stat().st_gid
        or value.get("ownership_transactional_before_publish") is not True
        or value.get("qps_resource_input_prohibited") is not True
        or value.get("password_source")
        != "external_0600_file_not_logged_or_hashed"
        or not isinstance(files, Mapping)
        or set(files) != {"tpcc.xml", "mysql.cnf"}
    ):
        raise ServiceError("attempt config materialization authority differs")
    for name, evidence in files.items():
        candidate = config_dir / name
        if (
            candidate.is_symlink()
            or not candidate.is_file()
            or stat.S_IMODE(candidate.stat().st_mode) != 0o600
            or not isinstance(evidence, Mapping)
            or evidence.get("mode") != "0600"
            or evidence.get("uid") != candidate.stat().st_uid
            or evidence.get("gid") != candidate.stat().st_gid
            or evidence.get("sha256") != sha256(candidate)
            or int(evidence.get("bytes") or -1) != candidate.stat().st_size
        ):
            raise ServiceError("attempt config file/readback differs: " + name)
    return dict(value)


def _run(argv: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv), text=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=check,
    )


def _inspect(name: str) -> dict[str, Any] | None:
    result = _run(["docker", "inspect", name], check=False)
    if result.returncode != 0:
        if "No such object" in result.stderr or "No such container" in result.stderr:
            return None
        raise ServiceError(f"docker inspect failed for {name}: {result.stderr.strip()}")
    value = json.loads(result.stdout)
    if not isinstance(value, list) or len(value) != 1:
        raise ServiceError(f"docker inspect result differs for {name}")
    return value[0]


def _proc_start_ticks(pid: int) -> int:
    raw = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    right = raw.rfind(")")
    fields_after_comm = raw[right + 2 :].split()
    # starttime is field 22; fields_after_comm starts with field 3.
    return int(fields_after_comm[19])


def _config_identity(inspect: Mapping[str, Any]) -> str:
    selected = {
        "Config": inspect.get("Config"),
        "HostConfig": inspect.get("HostConfig"),
        "Mounts": inspect.get("Mounts"),
        "NetworkSettings": inspect.get("NetworkSettings"),
    }
    return sha_bytes(canonical(selected))


def _mount_map(inspect: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    mounts = inspect.get("Mounts")
    if not isinstance(mounts, list):
        raise ServiceError("container inspect lacks Mounts")
    return {
        str(item.get("Destination")): item
        for item in mounts if isinstance(item, Mapping)
    }


def _expect_inspect(
    inspect: Mapping[str, Any], plan: Mapping[str, Any], role: str,
    *, trusted_v1_blkio: bool = False,
) -> dict[str, Any]:
    state = inspect.get("State") or {}
    host = inspect.get("HostConfig") or {}
    config = inspect.get("Config") or {}
    labels = config.get("Labels") or {}
    identity = {
        "bundle_id": plan["bundle_id"],
        "attempt_id": plan["attempt_id"],
        "handoff_token_sha256": plan["handoff_token_sha256"],
        "role": role,
    }
    for key, expected in identity.items():
        if labels.get(f"ra.{key}") != expected:
            raise ServiceError(f"{role} label differs: ra.{key}")
    if state.get("Running") is not True or int(state.get("Pid") or 0) <= 1:
        raise ServiceError(f"{role} container is not live")
    expected = (
        (DB_CPUS, DB_MEMS, DB_CPU_QUOTA, DB_PIDS)
        if role == "database"
        else (CLIENT_CPUS, CLIENT_MEMS, CLIENT_CPU_QUOTA, CLIENT_PIDS)
    )
    if (
        host.get("CpusetCpus") != expected[0]
        or host.get("CpusetMems") != expected[1]
        or int(host.get("CpuQuota") or 0) != expected[2]
        or int(host.get("CpuPeriod") or 0) != 100000
        or int(host.get("Memory") or 0) != MEMORY
        or int(host.get("MemorySwap") or 0) != MEMORY
        or int(host.get("PidsLimit") or 0) != expected[3]
        or int(inspect.get("RestartCount") or 0) != 0
    ):
        raise ServiceError(f"{role} resource envelope differs")
    if role == "client":
        security = host.get("SecurityOpt") or []
        log_config = host.get("LogConfig") or {}
        if (
            host.get("ReadonlyRootfs") is not True
            or host.get("NetworkMode") != "host"
            or host.get("CapDrop") != ["ALL"]
            or not any(
                str(item).startswith("no-new-privileges")
                for item in security
            )
            or log_config.get("Type") != "none"
            or config.get("StopSignal") not in (None, "", "SIGTERM")
        ):
            raise ServiceError("client isolation differs")
        mounts = _mount_map(inspect)
        expected_sources = {
            BUNDLE_DESTINATION: plan["bundle_path"],
            JDK_DESTINATION: JDK_SOURCE,
            PYTHON_DESTINATION: PYTHON_SOURCE,
            CONFIG_DESTINATION: plan["config_path"],
        }
        for destination, source in expected_sources.items():
            if (
                destination not in mounts
                or mounts[destination].get("RW") is not False
                or mounts[destination].get("Source") != source
            ):
                raise ServiceError(f"client read-only mount differs: {destination}")
        if (
            MEASUREMENT_DESTINATION not in mounts
            or mounts[MEASUREMENT_DESTINATION].get("RW") is not True
            or mounts[MEASUREMENT_DESTINATION].get("Source")
            != plan["measurement_bridge"]["host_path"]
        ):
            raise ServiceError("client measurement bridge mount differs")
    else:
        mount = _mount_map(inspect).get("/var/lib/mysql")
        read_limits = host.get("BlkioDeviceReadBps") or []
        write_limits = host.get("BlkioDeviceWriteBps") or []
        ports = (host.get("PortBindings") or {}).get("3306/tcp") or []
        if (
            not isinstance(mount, Mapping)
            or mount.get("RW") is not True
            or mount.get("Source") != plan["database"]["datadir"]
            or (
                not trusted_v1_blkio
                and read_limits != [{"Path": DEVICE, "Rate": IO_BPS}]
            )
            or (
                not trusted_v1_blkio
                and write_limits != [{"Path": DEVICE, "Rate": IO_BPS}]
            )
            or ports
            != [{"HostIp": "127.0.0.1", "HostPort": str(PORT)}]
        ):
            raise ServiceError("database datadir/IO/port envelope differs")
    pid = int(state["Pid"])
    if inspect.get("Image") != plan.get("expected_image_id"):
        raise ServiceError(f"{role} live image ID differs")
    return {
        "container_id": str(inspect.get("Id") or ""),
        "name": str(inspect.get("Name") or "").lstrip("/"),
        "pid": pid,
        "process_start_ticks": _proc_start_ticks(pid),
        "created": inspect.get("Created"),
        "image_id": inspect.get("Image"),
        "inspect_config_sha256": _config_identity(inspect),
        "cpuset_cpus": expected[0],
        "cpuset_mems": expected[1],
        "cpu_quota_cores": expected[2] / 100000,
        "memory_bytes": MEMORY,
        "memory_swap_bytes": MEMORY,
        "pids_max": expected[3],
        "restart_count": 0,
    }


def _verify_host_bridge(plan: Mapping[str, Any]) -> dict[str, Any]:
    bridge = Path(plan["measurement_bridge"]["host_path"])
    bridge.mkdir(parents=True, exist_ok=True, mode=0o700)
    result = _run(
        ["findmnt", "--noheadings", "--output", "FSTYPE,TARGET", "--target", str(bridge)]
    )
    columns = result.stdout.split()
    if len(columns) < 2 or columns[0] != "tmpfs":
        raise ServiceError("measurement bridge is not backed by tmpfs")
    if Path(columns[1]).resolve() != bridge:
        raise ServiceError("measurement bridge must be its own private tmpfs mount")
    return {
        "host_path": str(bridge),
        "container_path": MEASUREMENT_DESTINATION,
        "filesystem": "tmpfs",
        "mount_target": columns[1],
        "device_major_minor": f"{os.major(bridge.stat().st_dev)}:{os.minor(bridge.stat().st_dev)}",
        "mode": oct(bridge.stat().st_mode & 0o777),
    }


def _cgroup_path(pid: int) -> Path:
    for line in Path(f"/proc/{pid}/cgroup").read_text(
        encoding="ascii"
    ).splitlines():
        pieces = line.split(":", 2)
        if len(pieces) == 3 and pieces[0] == "0" and pieces[1] == "":
            relative = pieces[2].lstrip("/")
            path = Path("/sys/fs/cgroup") / relative / "io.stat"
            if path.is_file():
                return path
    raise ServiceError("client unified cgroup io.stat is absent")


def _target_io_snapshot(pid: int) -> dict[str, int]:
    metrics = {
        "rbytes": 0, "wbytes": 0, "rios": 0,
        "wios": 0, "dbytes": 0, "dios": 0,
    }
    for line in _cgroup_path(pid).read_text(encoding="ascii").splitlines():
        pieces = line.split()
        if pieces and pieces[0] == DEVICE_MAJOR_MINOR:
            for item in pieces[1:]:
                key, raw = item.split("=", 1)
                if key in metrics:
                    metrics[key] = int(raw)
            break
    return metrics


def _io_delta(
    before: Mapping[str, int], after: Mapping[str, int]
) -> dict[str, int]:
    result = {key: int(after[key]) - int(before[key]) for key in before}
    if any(value < 0 for value in result.values()):
        raise ServiceError("client target-device cgroup IO counters regressed")
    return result


def _target_mount_destinations(inspect: Mapping[str, Any]) -> list[str]:
    result: list[str] = []
    for destination, mount in _mount_map(inspect).items():
        source = Path(str(mount.get("Source") or ""))
        try:
            major_minor = _device_major_minor(source)
        except OSError:
            continue
        if major_minor == DEVICE_MAJOR_MINOR:
            result.append(destination)
    return sorted(result)


def prefault(plan: Mapping[str, Any]) -> dict[str, Any]:
    service = adopt(plan)
    client_inspect = _inspect(plan["client"]["name"])
    if client_inspect is None:
        raise ServiceError("prefault requires the live client container")
    destinations = _target_mount_destinations(client_inspect)
    python = PYTHON_DESTINATION + "/bin/python3.11"
    code = (
        "import os,sys\n"
        "for root in sys.argv[1:]:\n"
        "  for base,dirs,files in os.walk(root,followlinks=False):\n"
        "    dirs.sort(); files.sort()\n"
        "    for name in files:\n"
        "      path=os.path.join(base,name)\n"
        "      if os.path.islink(path): continue\n"
        "      with open(path,'rb',buffering=0) as f:\n"
        "        while f.read(1048576): pass\n"
    )
    before_prefault = _target_io_snapshot(service["client"]["pid"])
    if destinations:
        _run([
            "docker", "exec", plan["client"]["name"],
            python, "-c", code, *destinations,
        ])
    after_prefault = _target_io_snapshot(service["client"]["pid"])
    time.sleep(2)
    quiet = _target_io_snapshot(service["client"]["pid"])
    quiet_delta = _io_delta(after_prefault, quiet)
    if any(quiet_delta.values()):
        raise ServiceError("client target-device IO did not quiesce after prefault")
    return {
        "schema_version": "mysql_tpcc_client_target_device_prefault_v1",
        "status": "PASS_PREFAULT_ZERO_IO_BASELINE",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "bundle_id": service["bundle_id"],
        "attempt_id": service["attempt_id"],
        "handoff_token_sha256": service["handoff_token_sha256"],
        "client_container_id": service["client"]["container_id"],
        "client_pid": service["client"]["pid"],
        "client_process_start_ticks":
            service["client"]["process_start_ticks"],
        "target_device_major_minor": DEVICE_MAJOR_MINOR,
        "prefaulted_target_mount_destinations": destinations,
        "before_prefault": before_prefault,
        "after_prefault": after_prefault,
        "real_window_baseline": quiet,
        "quiet_window_seconds": 2,
        "quiet_window_delta": quiet_delta,
        "real_window_all_target_device_io_must_be_zero": True,
        "accepted_round_authorized": False,
        "formal_launch_authorized": False,
    }


def _verify_physical_copy(plan: Mapping[str, Any]) -> dict[str, Any]:
    datadir = Path(plan["database"]["datadir"])
    marker = datadir.parent / "physical_copy_authority.json"
    value = load(marker)
    if (
        value.get("schema_version") != "mysql_tpcc_physical_copy_authority_v1"
        or value.get("status") != "PASS"
        or value.get("source_template") != TEMPLATE
        or value.get("target_datadir") != str(datadir)
        or value.get("full_physical_copy") is not True
        or value.get("reflink_used") is not False
        or value.get("overlay_used") is not False
        or value.get("device_major_minor") != DEVICE_MAJOR_MINOR
    ):
        raise ServiceError("physical datadir copy authority differs")
    return value


def adopt(plan: Mapping[str, Any]) -> dict[str, Any]:
    if socket.gethostname().split(".")[0] != HOST:
        raise ServiceError("adoption is host-bound to node04")
    _verify_physical_copy(plan)
    runtimes = verify_runtime_trees(Path(plan["bundle_path"]))
    bridge = _verify_host_bridge(plan)
    db_inspect = _inspect(plan["database"]["name"])
    client_inspect = _inspect(plan["client"]["name"])
    if db_inspect is None or client_inspect is None:
        raise ServiceError("adoption requires both pre-existing containers")
    database = _expect_inspect(db_inspect, plan, "database")
    client = _expect_inspect(client_inspect, plan, "client")
    authority = {
        "schema_version": "mysql_tpcc_node04_adopted_service_authority_v1",
        "status": "PASS",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "bundle_id": plan["bundle_id"],
        "attempt_id": plan["attempt_id"],
        "handoff_token_sha256": plan["handoff_token_sha256"],
        "host": HOST,
        "observed_epoch_ms": time.time_ns() // 1_000_000,
        "database": {
            **database,
            "datadir": plan["database"]["datadir"],
            "device": DEVICE,
            "device_major_minor": DEVICE_MAJOR_MINOR,
            "mysql_port": PORT,
        },
        "client": {
            **client,
            "root_filesystem_read_only": True,
            "network_mode": "host",
            "target_mysql_device_write_prohibited": True,
        },
        "measurement_bridge": bridge,
        "runtime_tree_readback": runtimes,
        "same_live_database_required": True,
        "container_recreated": False,
        "database_restarted": False,
        "datadir_replaced": False,
        "service_ready": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    }
    authority["live_readback_sha256"] = sha_bytes(canonical(authority))
    return authority


def bootstrap(plan: Mapping[str, Any]) -> dict[str, Any]:
    if socket.gethostname().split(".")[0] != HOST:
        raise ServiceError("bootstrap is host-bound to node04")
    _verify_physical_copy(plan)
    _verify_host_bridge(plan)
    for role in ("database", "client"):
        name = plan[role]["name"]
        current = _inspect(name)
        if current is None:
            _run(plan[role]["create_argv"])
            _run(plan[role]["start_argv"])
        else:
            state = current.get("State") or {}
            if state.get("Running") is not True:
                raise ServiceError(
                    f"{role} container exists but is stopped; refusing restart"
                )
            _expect_inspect(current, plan, role)
    return adopt(plan)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=("prepare-copy", "plan", "bootstrap", "adopt", "prefault"),
    )
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--image-id")
    parser.add_argument("--config-dir", type=Path)
    parser.add_argument("--datadir", type=Path)
    parser.add_argument("--measurement-bridge", type=Path)
    parser.add_argument("--attempt-id")
    parser.add_argument("--handoff-token-file", type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.action == "prepare-copy":
        if args.attempt_id is None:
            raise ServiceError("prepare-copy requires --attempt-id")
        value = prepare_physical_copy(args.attempt_id, args.output)
        print(json.dumps({"status": "PASS", "output": str(args.output)}, sort_keys=True))
        return 0
    if args.action == "plan":
        required = (
            args.bundle, args.image_id, args.config_dir, args.datadir,
            args.measurement_bridge, args.attempt_id, args.handoff_token_file,
        )
        if any(value is None for value in required):
            raise ServiceError("plan action lacks required arguments")
        token = args.handoff_token_file.read_text(encoding="utf-8").strip()
        value = build_plan(
            bundle=args.bundle, image_id=args.image_id,
            config_dir=args.config_dir, datadir=args.datadir,
            measurement_bridge=args.measurement_bridge,
            attempt_id=args.attempt_id, handoff_token=token,
        )
    else:
        if args.plan is None:
            raise ServiceError("bootstrap/adopt requires --plan")
        if args.action == "bootstrap":
            value = bootstrap(load(args.plan))
        elif args.action == "prefault":
            value = prefault(load(args.plan))
        else:
            value = adopt(load(args.plan))
    atomic_json(args.output, value)
    print(json.dumps({"status": "PASS", "output": str(args.output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
