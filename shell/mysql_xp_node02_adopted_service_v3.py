#!/usr/bin/env python3
"""Fail-closed node02 bootstrap/adoption service for MySQL XP.

The file is Python 3.6 compatible and never uses SSH. ``plan`` is read-only.
``prepare-copy`` makes one Coreutils-8.22-compatible byte copy and proves,
with per-file SHA-256 plus FIEMAP, that no shared or overlapping physical
extents exist. ``bootstrap`` creates each missing container once and refuses
replacement. ``adopt`` validates an already-live pair.  None of these actions
authorizes R0, an outer replay, or an accepted round.
"""

import sys
sys.dont_write_bytecode = True

import argparse
from datetime import datetime, timezone
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


HOST = "node02"
SCENARIO = "hybench_xp_io"
DATA_PARTITION = "/dev/nvme0n1p1"
DATA_PARTITION_MAJOR_MINOR = "259:4"
THROTTLE_DEVICE = "/dev/nvme0n1"
THROTTLE_DEVICE_MAJOR_MINOR = "259:1"
MYSQL_UID = 999
MYSQL_GID = 999
DATADIR_DIRECTORY_MODE = 0o700
DATADIR_REGULAR_MODE = 0o600
SEALED_BUNDLE_ROOT = Path(__file__).resolve().parents[1]
MOUNT = "/mnt/mysql_nvme0n1p1"
TEMPLATE = MOUNT + "/anonymous/resource_adaptive_mysql/hybench_xp/template_v2"
TEMPLATE_DATADIR = TEMPLATE + "/datadir"
TEMPLATE_MANIFEST = TEMPLATE + "/database_manifest.json"
RUNTIME_SOCKET_RELATIVE = "datadir/mysql.sock"
RUNTIME_SOCKET_TARGET = "/var/run/mysqld/mysqld.sock"
RUNTIME_ROOT = (
    MOUNT
    + "/anonymous/resource_adaptive_mysql/hybench_xp/"
    "runtime_algorithm_c_successor_20260729"
)
JDK_ROOT = (
    MOUNT
    + "/anonymous/resource_adaptive_mysql/runtime/"
    "jdk-23.0.2-oracle-x64-"
    "578395e883d31c692a1c01c37910d22dbdafbcf95e373c7e0f6776a26f5c844c"
)
JDK_TREE_SHA256 = (
    "578395e883d31c692a1c01c37910d22dbdafbcf95e373c7e0f6776a26f5c844c"
)
JAVA_SHA256 = (
    "c8f339ba5d433ef5ecacef4de0640b0a35c99381804413bfbc24375a0410e51c"
)
PORT = 65104
MEMORY = 8589934592
DB_PIDS = 1024
CLIENT_PIDS = 1024
DB_CPU_PERIOD = 100000
DB_CPU_QUOTA = 530000
CLIENT_CPU_PERIOD = 100000
CLIENT_CPU_QUOTA = 1800000
READ_BPS = 29360128
WRITE_BPS = 524288
BUFFER_POOL = 4294967296
DB_CPUS = "21-33,57-69"
DB_MEMS = "1"
CLIENT_CPUS = "3-11,39-47"
CLIENT_MEMS = "0"
TMPFS_BYTES = 2147483648
BUNDLE_DESTINATION = "/opt/mysql-xp-bundle"
RUNTIME_DESTINATION = "/opt/mysql-xp-jdk"
CONFIG_DESTINATION = "/opt/mysql-xp-config"
PRIVATE_CREDENTIAL_DESTINATION = "/run/mysql-xp-private"
PRIVATE_MYSQL_DEFAULTS = "mysql-client.cnf"
PRIVATE_QUALIFICATION_CONFIG = "hybench_xp_qualification.properties"
PRIVATE_AUTHORITY_DIRECTORY = "private_authority"
PRIVATE_MAIN_AGENT_TOKEN = "main_agent_token"
QUALIFICATION_ACCOUNT_HOST = "192.0.2.6"
MEASUREMENT_DESTINATION = "/measurement"
CLIENT_MYSQL_MASK_RELATIVE = "client_masks/var_lib_mysql"
CLIENT_MYSQL_MASK_DESTINATION = "/var/lib/mysql"
CLIENT_MYSQL_MASK_MARKER = ".sealed-read-only-mask"
PG_NVME2_PATH_PREFIX = "/mnt/nvme2n1/"
QUALIFICATION_CONFIG_FILES = {
    "hybench_xp_qualification.properties",
    "mysql-client.cnf",
    "stmt_mysql_formal_case_normalized.toml",
}
FS_IOC_FIEMAP = 0xC020660B
FIEMAP_EXTENT_LAST = 0x00000001
FIEMAP_EXTENT_SHARED = 0x00002000
FIEMAP_UNTRUSTED = 0x00000002 | 0x00000004 | 0x00000008 | 0x00000200
FIEMAP_BATCH = 512
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9_.:-]{16,128}$")
PASSWORD_RE = re.compile(r"^[A-Za-z0-9_-]{43,128}$")
STATIC_INSPECT_IDENTITY_SCHEMA = "mysql_xp_static_inspect_identity_v2"
STATIC_MOUNT_FIELDS = (
    "Type", "Name", "Source", "Destination", "Driver", "Mode", "RW",
    "Propagation",
)


class ServiceError(RuntimeError):
    pass


def canonical(value):
    return (
        json.dumps(
            value, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False
        )
        + "\n"
    ).encode("utf-8")


def sha_bytes(value):
    return hashlib.sha256(value).hexdigest()


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load(path, label="JSON"):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ServiceError("%s must be a regular file: %s" % (label, path))
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ServiceError("%s must be a JSON object" % label)
    return value


def verify_sidecar(path, label):
    path = Path(path)
    sidecar = Path(str(path) + ".sha256")
    expected = "%s  %s\n" % (sha256(path), path.name)
    if (
        sidecar.is_symlink()
        or not sidecar.is_file()
        or sidecar.read_text(encoding="ascii") != expected
    ):
        raise ServiceError("%s sidecar differs" % label)


def atomic_json(path, value):
    path = Path(path)
    sidecar = Path(str(path) + ".sha256")
    if (
        path.exists()
        or path.is_symlink()
        or sidecar.exists()
        or sidecar.is_symlink()
    ):
        raise ServiceError("refusing to overwrite evidence: %s" % path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical(value)
    temporary = path.with_name(".%s.%d.tmp" % (path.name, os.getpid()))
    sidecar_temporary = sidecar.with_name(
        ".%s.%d.tmp" % (sidecar.name, os.getpid())
    )
    try:
        descriptor = os.open(
            str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        descriptor = os.open(
            str(sidecar_temporary),
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(
                ("%s  %s\n" % (sha256(temporary), path.name)).encode("ascii")
            )
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(temporary), str(path))
        os.replace(str(sidecar_temporary), str(sidecar))
    finally:
        for candidate in (temporary, sidecar_temporary):
            if candidate.exists():
                candidate.unlink()


def _run(argv, check=True):
    completed = subprocess.run(
        list(argv),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
    )
    if check and completed.returncode != 0:
        raise ServiceError(
            "command failed (%d): %s: %s"
            % (completed.returncode, " ".join(argv), completed.stderr.strip())
        )
    return completed


def _run_private_stdin(argv, payload):
    """Run a secret-bearing SQL stream without putting it in argv or errors."""
    completed = subprocess.run(
        list(argv),
        input=payload,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
    )
    if completed.returncode != 0:
        raise ServiceError(
            "private database credential provisioning failed (exit=%d)"
            % completed.returncode
        )
    return completed


def verify_bundle(bundle):
    bundle = Path(bundle).resolve(strict=True)
    manifest_path = bundle / "bundle_manifest.json"
    manifest = load(manifest_path, "XP launch bundle manifest")
    verify_sidecar(manifest_path, "XP launch bundle manifest")
    unsigned = dict(manifest)
    bundle_id = unsigned.pop("bundle_id", None)
    if (
        bundle_id != bundle.name
        or SHA_RE.fullmatch(bundle.name) is None
        or sha_bytes(canonical(unsigned)) != bundle.name
        or manifest.get("schema_version")
        != "mysql_xp_distributed_formal10_launch_v3"
        or manifest.get("scenario") != SCENARIO
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("formal_launch_authorized") is not False
    ):
        raise ServiceError("XP launch bundle identity/scope differs")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise ServiceError("XP launch bundle file closure is absent")
    actual = {}
    for candidate in sorted(bundle.rglob("*")):
        if candidate.is_symlink():
            raise ServiceError("bundle symlink prohibited: %s" % candidate)
        if candidate.is_file():
            relative = candidate.relative_to(bundle).as_posix()
            if relative not in (
                "bundle_manifest.json",
                "bundle_manifest.json.sha256",
            ):
                actual[relative] = sha256(candidate)
        if stat.S_IMODE(candidate.stat().st_mode) & 0o222:
            raise ServiceError("writable bundle entry: %s" % candidate)
    if actual != files:
        raise ServiceError("XP launch bundle full file closure differs")
    return manifest


def _sealed_bundle_readback(bundle=SEALED_BUNDLE_ROOT):
    if sys.dont_write_bytecode is not True:
        raise ServiceError("sealed entrypoint bytecode writes are not disabled")
    bundle = Path(bundle).resolve(strict=True)
    manifest = verify_bundle(bundle)
    bytecode = sorted(
        candidate.relative_to(bundle).as_posix()
        for candidate in bundle.rglob("*")
        if (
            candidate.name == "__pycache__"
            or candidate.suffix in (".pyc", ".pyo")
        )
    )
    if bytecode:
        raise ServiceError("sealed bundle bytecode artifact exists")
    return {
        "bundle": str(bundle),
        "bundle_id": manifest["bundle_id"],
        "manifest_sha256": sha256(bundle / "bundle_manifest.json"),
        "full_closure_verified": True,
        "bytecode_write_disabled": True,
        "bytecode_artifacts": [],
    }


def verify_runtime_publication(bundle, publication_path):
    publication_path = Path(publication_path)
    verify_sidecar(publication_path, "JDK runtime publication")
    document = load(publication_path, "JDK runtime publication")
    manifest_path = bundle / "runtime/jdk-23.0.2-tree-manifest.json"
    helper_path = bundle / "runtime/runtime_tree_content_identity_v3.py"
    if (
        document.get("schema_version")
        != "mysql_xp_node02_jdk_runtime_publication_v1"
        or document.get("status") != "PUBLISHED_EXACT_MANIFEST_BOUND"
        or document.get("runtime_root") != JDK_ROOT
        or document.get("tree_content_sha256") != JDK_TREE_SHA256
        or document.get("java_binary_sha256") != JAVA_SHA256
        or document.get("tree_manifest_sha256") != sha256(manifest_path)
        or document.get("content_addressed") is not True
        or document.get("atomic_publish") is not True
        or document.get("non_overwriting") is not True
        or document.get("publish_gate_passed") is not True
        or document.get("tree_modes_preserved_as_manifested") is not True
        or document.get("exact_readback_required_before_every_launch")
        is not True
        or document.get("client_mount_read_only_required") is not True
        or document.get("formal_launch_authorized") is not False
    ):
        raise ServiceError("JDK runtime publication gate differs")
    if not helper_path.is_file() or helper_path.is_symlink():
        raise ServiceError("sealed runtime verifier is absent")
    helper_spec = importlib.util.spec_from_file_location(
        "xp_runtime_tree_identity_v3", str(helper_path)
    )
    if helper_spec is None or helper_spec.loader is None:
        raise ServiceError("sealed runtime verifier cannot be loaded")
    helper = importlib.util.module_from_spec(helper_spec)
    helper_spec.loader.exec_module(helper)
    tree_manifest = load(manifest_path, "JDK runtime tree manifest")
    live = helper.verify_tree_manifest(Path(JDK_ROOT), tree_manifest)
    java = Path(JDK_ROOT) / "bin/java"
    if (
        live.get("status") != "PASS_EXACT_PATH_INDEPENDENT_TREE_READBACK"
        or live.get("tree_content_sha256") != JDK_TREE_SHA256
        or java.is_symlink()
        or not java.is_file()
        or sha256(java) != JAVA_SHA256
    ):
        raise ServiceError("live JDK runtime readback differs")
    return {
        "publication_path": str(publication_path.resolve()),
        "publication_sha256": sha256(publication_path),
        "tree_manifest_sha256": sha256(manifest_path),
        "runtime_helper_sha256": sha256(helper_path),
        "live_tree_readback": live,
        "live_java_binary_sha256": sha256(java),
    }


def verify_attempt(attempt_id, handoff_token):
    if TOKEN_RE.fullmatch(attempt_id or "") is None:
        raise ServiceError("attempt ID must be 16..128 safe characters")
    if TOKEN_RE.fullmatch(handoff_token or "") is None:
        raise ServiceError("handoff token must be 16..128 safe characters")
    if attempt_id != handoff_token:
        raise ServiceError("attempt ID and handoff token must be identical")
    return sha_bytes(handoff_token.encode("utf-8"))


def _parse_utc(value, label):
    if not isinstance(value, str):
        raise ServiceError("%s must be UTC ISO-8601" % label)
    if value.endswith("Z"):
        core = value[:-1]
    elif value.endswith("+00:00"):
        core = value[:-6]
    else:
        raise ServiceError("%s must use UTC" % label)
    try:
        parsed = datetime.strptime(
            core,
            "%Y-%m-%dT%H:%M:%S.%f" if "." in core
            else "%Y-%m-%dT%H:%M:%S",
        )
    except ValueError:
        raise ServiceError("%s must be UTC ISO-8601" % label)
    return parsed.replace(tzinfo=timezone.utc)


def verify_short_lived_authority(created_at_utc, expires_at_utc, now=None):
    created = _parse_utc(created_at_utc, "authorization creation")
    expires = _parse_utc(expires_at_utc, "authorization expiry")
    observed = now or datetime.now(timezone.utc)
    duration = (expires - created).total_seconds()
    if duration <= 0 or duration > 1800:
        raise ServiceError("authorization validity exceeds 30 minutes")
    if created > observed or observed >= expires:
        raise ServiceError("short-lived authorization is not currently valid")
    return {
        "created_at_utc": created_at_utc,
        "expires_at_utc": expires_at_utc,
    }


def _device_major_minor(path):
    device = Path(path).stat().st_dev
    return "%d:%d" % (os.major(device), os.minor(device))


def _block_node_major_minor(path, stat_func=os.stat):
    value = stat_func(str(path))
    if not stat.S_ISBLK(value.st_mode):
        raise ServiceError("frozen block device path is not a block node")
    return "%d:%d" % (os.major(value.st_rdev), os.minor(value.st_rdev))


def _current_cpu_affinity():
    for line in Path("/proc/self/status").read_text(
        encoding="ascii"
    ).splitlines():
        if line.startswith("Cpus_allowed_list:"):
            return line.split(":", 1)[1].strip()
    raise ServiceError("current CPU affinity cannot be read")


def _tree_records(
    root, with_extents=False, logical_prefix="",
    allow_runtime_socket=False
):
    records = []
    extent_map = {}
    excluded_runtime_artifacts = []
    total = 0
    files = 0
    root = Path(root)
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        logical = (
            logical_prefix.rstrip("/") + "/" + relative
            if logical_prefix else relative
        )
        if path.is_symlink():
            if (
                allow_runtime_socket
                and logical == RUNTIME_SOCKET_RELATIVE
                and os.readlink(str(path)) == RUNTIME_SOCKET_TARGET
            ):
                excluded_runtime_artifacts.append(
                    {
                        "path": logical,
                        "type": "runtime_socket_symlink",
                        "target": RUNTIME_SOCKET_TARGET,
                        "copied": False,
                        "followed": False,
                    }
                )
                continue
            raise ServiceError("datadir symlink prohibited: %s" % relative)
        mode = stat.S_IMODE(path.stat().st_mode)
        if path.is_dir():
            records.append(
                {"path": logical, "type": "directory", "mode": mode}
            )
        elif path.is_file():
            files += 1
            total += path.stat().st_size
            records.append(
                {
                    "path": logical,
                    "type": "regular",
                    "mode": mode,
                    "size": path.stat().st_size,
                    "sha256": sha256(path),
                }
            )
            if with_extents:
                extent_map[logical] = _fiemap_extents(path)
        else:
            raise ServiceError("unsupported datadir entry: %s" % relative)
    return (
        sha_bytes(canonical(records)), files, total, extent_map,
        excluded_runtime_artifacts,
    )


def _tree_metadata_records(
    root, logical_prefix="datadir", allow_runtime_socket=False
):
    root = Path(root)
    paths = [root] + sorted(root.rglob("*"))
    records = []
    for path in paths:
        logical = (
            logical_prefix
            if path == root
            else logical_prefix.rstrip("/")
            + "/" + path.relative_to(root).as_posix()
        )
        value = path.lstat()
        record = {
            "path": logical,
            "mode": stat.S_IMODE(value.st_mode),
            "uid": value.st_uid,
            "gid": value.st_gid,
        }
        if stat.S_ISLNK(value.st_mode):
            if (
                not allow_runtime_socket
                or logical != RUNTIME_SOCKET_RELATIVE
                or os.readlink(str(path)) != RUNTIME_SOCKET_TARGET
            ):
                raise ServiceError(
                    "datadir metadata symlink prohibited: %s" % logical
                )
            record.update({
                "type": "runtime_socket_symlink",
                "target": RUNTIME_SOCKET_TARGET,
            })
        elif stat.S_ISDIR(value.st_mode):
            record["type"] = "directory"
        elif stat.S_ISREG(value.st_mode):
            record.update({
                "type": "regular",
                "size": value.st_size,
                "mtime_ns": value.st_mtime_ns,
            })
        else:
            raise ServiceError(
                "unsupported datadir metadata entry: %s" % logical
            )
        records.append(record)
    return records


def _metadata_summary(records):
    counts = {}
    owner_mode_inventory = set()
    for record in records:
        kind = record["type"]
        counts[kind] = counts.get(kind, 0) + 1
        owner_mode_inventory.add((
            kind, int(record["uid"]), int(record["gid"]),
            int(record["mode"]),
        ))
    return {
        "metadata_sha256": sha_bytes(canonical(records)),
        "entry_count": len(records),
        "type_counts": dict(sorted(counts.items())),
        "owner_mode_inventory": [
            {
                "type": row[0], "uid": row[1], "gid": row[2],
                "mode": row[3],
            }
            for row in sorted(owner_mode_inventory)
        ],
    }


def _tree_content_identity(
    root, logical_prefix="datadir", allow_runtime_socket=False
):
    root = Path(root)
    records = []
    excluded = []
    files = 0
    total = 0
    for path in sorted(root.rglob("*")):
        logical = (
            logical_prefix.rstrip("/")
            + "/" + path.relative_to(root).as_posix()
        )
        value = path.lstat()
        if stat.S_ISLNK(value.st_mode):
            if (
                allow_runtime_socket
                and logical == RUNTIME_SOCKET_RELATIVE
                and os.readlink(str(path)) == RUNTIME_SOCKET_TARGET
            ):
                excluded.append({
                    "path": logical,
                    "type": "runtime_socket_symlink",
                    "target": RUNTIME_SOCKET_TARGET,
                    "copied": False,
                    "followed": False,
                })
                continue
            raise ServiceError(
                "datadir content symlink prohibited: %s" % logical
            )
        if stat.S_ISDIR(value.st_mode):
            records.append({"path": logical, "type": "directory"})
        elif stat.S_ISREG(value.st_mode):
            files += 1
            total += value.st_size
            records.append({
                "path": logical,
                "type": "regular",
                "size": value.st_size,
                "sha256": sha256(path),
            })
        else:
            raise ServiceError(
                "unsupported datadir content entry: %s" % logical
            )
    return sha_bytes(canonical(records)), files, total, excluded


def _normalize_target_datadir(target, attempt_id):
    target = Path(target)
    expected = Path(RUNTIME_ROOT) / attempt_id / "datadir"
    if target.resolve() != expected.resolve():
        raise ServiceError("permission normalization target is not attempt-owned")
    records = _tree_metadata_records(
        target, logical_prefix="datadir", allow_runtime_socket=False
    )
    for record in records:
        path = (
            target
            if record["path"] == "datadir"
            else target / record["path"].split("/", 1)[1]
        )
        mode = (
            DATADIR_DIRECTORY_MODE
            if record["type"] == "directory"
            else DATADIR_REGULAR_MODE
        )
        os.chown(str(path), MYSQL_UID, MYSQL_GID)
        os.chmod(str(path), mode)
    return _target_metadata_readback(target)


def _target_metadata_readback(target):
    records = _tree_metadata_records(
        target, logical_prefix="datadir", allow_runtime_socket=False
    )
    for record in records:
        expected_mode = (
            DATADIR_DIRECTORY_MODE
            if record["type"] == "directory"
            else DATADIR_REGULAR_MODE
        )
        if (
            record["type"] not in ("directory", "regular")
            or record["uid"] != MYSQL_UID
            or record["gid"] != MYSQL_GID
            or record["mode"] != expected_mode
        ):
            raise ServiceError(
                "attempt datadir owner/mode normalization differs"
            )
    result = _metadata_summary(records)
    result.update({
        "root": str(Path(target).resolve()),
        "required_uid": MYSQL_UID,
        "required_gid": MYSQL_GID,
        "directory_mode": DATADIR_DIRECTORY_MODE,
        "regular_mode": DATADIR_REGULAR_MODE,
        "symlink_count": 0,
        "scope": "attempt_owned_datadir_only",
    })
    return result


def _copy_argv(target):
    source = Path(TEMPLATE_DATADIR)
    entries = []
    for path in sorted(source.iterdir()):
        logical = "datadir/" + path.name
        if logical == RUNTIME_SOCKET_RELATIVE:
            if (
                not path.is_symlink()
                or os.readlink(str(path)) != RUNTIME_SOCKET_TARGET
            ):
                raise ServiceError("runtime socket artifact differs")
            continue
        if path.is_symlink():
            raise ServiceError("datadir symlink prohibited: %s" % logical)
        entries.append(str(path))
    if not entries:
        raise ServiceError("datadir copy inventory is empty")
    return ["cp", "-a", "--"] + entries + [str(target)]


def _verify_template_manifest():
    path = Path(TEMPLATE_MANIFEST)
    verify_sidecar(path, "XP template database manifest")
    document = load(path, "XP template database manifest")
    if (
        document.get("status") != "complete"
        or document.get("engine") != "mysql"
        or document.get("database") != "hybench_sf10x"
        or (document.get("image") or {}).get("image_id")
        != "sha256:b3197d59eb3b270cf21bc1166fa726f9c511438416b421504068301df90c3172"
    ):
        raise ServiceError("XP template database manifest differs")
    return sha256(path)


def _fiemap_extents(path):
    path = Path(path)
    if path.stat().st_size == 0:
        return []
    result = []
    logical = 0
    while True:
        header = struct.pack(
            "<QQIIII", logical, (1 << 64) - 1 - logical, 0, 0,
            FIEMAP_BATCH, 0,
        )
        buffer = bytearray(header + bytes(56 * FIEMAP_BATCH))
        with path.open("rb", buffering=0) as stream:
            fcntl.ioctl(stream.fileno(), FS_IOC_FIEMAP, buffer, True)
        unused_a, unused_b, unused_c, mapped, unused_d, unused_e = (
            struct.unpack_from("<QQIIII", buffer, 0)
        )
        if mapped == 0:
            break
        advanced = logical
        finished = False
        for index in range(mapped):
            values = struct.unpack_from(
                "<QQQQQIIII", buffer, 32 + 56 * index
            )
            extent_logical, physical, length = values[:3]
            flags = values[5]
            if length <= 0 or physical <= 0:
                raise ServiceError("invalid FIEMAP extent: %s" % path)
            if flags & FIEMAP_UNTRUSTED:
                raise ServiceError("untrusted FIEMAP flags: %s" % path)
            result.append((physical, physical + length, flags))
            advanced = max(advanced, extent_logical + length)
            if flags & FIEMAP_EXTENT_LAST:
                finished = True
        if finished:
            break
        if advanced <= logical:
            raise ServiceError("FIEMAP did not advance: %s" % path)
        logical = advanced
    return result


def _verify_disjoint(source, target):
    left = []
    right = []
    for relative, extents in source.items():
        for begin, end, flags in extents:
            if flags & FIEMAP_EXTENT_SHARED:
                raise ServiceError("source shared extent: %s" % relative)
            left.append((begin, end, relative))
    for relative, extents in target.items():
        for begin, end, flags in extents:
            if flags & FIEMAP_EXTENT_SHARED:
                raise ServiceError("target shared extent: %s" % relative)
            right.append((begin, end, relative))
    left.sort()
    right.sort()
    pointer = 0
    for begin, end, relative in right:
        while pointer < len(left) and left[pointer][1] <= begin:
            pointer += 1
        index = pointer
        while index < len(left) and left[index][0] < end:
            if max(begin, left[index][0]) < min(end, left[index][1]):
                raise ServiceError(
                    "source/target physical extent overlap: %s/%s"
                    % (left[index][2], relative)
                )
            index += 1
    return {
        "fiemap_verified": True,
        "source_extent_count": len(left),
        "target_extent_count": len(right),
        "shared_extent_flags_seen": 0,
        "source_target_physical_overlap_count": 0,
    }


def prepare_copy(attempt_id, output):
    if socket.gethostname().split(".")[0] != HOST:
        raise ServiceError("physical copy is host-bound to node02")
    if TOKEN_RE.fullmatch(attempt_id or "") is None:
        raise ServiceError("attempt ID must be 16..128 safe characters")
    sealed_bundle_before = _sealed_bundle_readback()
    source = Path(TEMPLATE_DATADIR)
    attempt_root = Path(RUNTIME_ROOT) / attempt_id
    target = attempt_root / "datadir"
    expected = attempt_root / "physical_copy_authority.json"
    if Path(output).resolve() != expected:
        raise ServiceError("copy authority output must be %s" % expected)
    if not source.is_dir() or source.is_symlink():
        raise ServiceError("sealed source template is absent")
    if _current_cpu_affinity() != DB_CPUS:
        raise ServiceError("physical copy CPU affinity differs")
    if os.geteuid() != 0:
        raise ServiceError(
            "permission normalization requires effective UID 0 before copy"
        )
    if attempt_root.exists() or attempt_root.is_symlink():
        raise ServiceError("attempt root already exists; refusing reuse")
    if (
        _device_major_minor(source) != DATA_PARTITION_MAJOR_MINOR
        or _device_major_minor(Path(RUNTIME_ROOT))
        != DATA_PARTITION_MAJOR_MINOR
    ):
        raise ServiceError("template/runtime root is not on frozen SSD")
    storage_topology = _validate_storage_topology()
    template_manifest_sha = _verify_template_manifest()
    source_metadata_before = _metadata_summary(
        _tree_metadata_records(
            source, logical_prefix="datadir", allow_runtime_socket=True
        )
    )
    (
        source_content_id, source_content_files, source_content_bytes,
        source_content_runtime_artifacts,
    ) = _tree_content_identity(
        source, logical_prefix="datadir", allow_runtime_socket=True
    )
    (
        source_id, source_files, source_bytes, source_extents,
        source_runtime_artifacts,
    ) = _tree_records(
        source, True, logical_prefix="datadir",
        allow_runtime_socket=True,
    )
    if (
        source_runtime_artifacts
        != [{
            "path": RUNTIME_SOCKET_RELATIVE,
            "type": "runtime_socket_symlink",
            "target": RUNTIME_SOCKET_TARGET,
            "copied": False,
            "followed": False,
        }]
    ):
        raise ServiceError("exact runtime socket artifact inventory differs")
    copy_argv = _copy_argv(target)
    attempt_root.mkdir(parents=True, exist_ok=False)
    if _device_major_minor(attempt_root) != DATA_PARTITION_MAJOR_MINOR:
        raise ServiceError("attempt root is not on frozen SSD")
    target.mkdir(mode=0o700, exist_ok=False)
    copied = _run(copy_argv, check=False)
    if copied.returncode != 0:
        raise ServiceError(
            "physical cp failed; target retained for audit: "
            + copied.stderr.strip()
        )
    (
        target_pre_id, target_pre_files, target_pre_bytes,
        unused_target_pre_extents, target_pre_runtime_artifacts,
    ) = _tree_records(
        target, False, logical_prefix="datadir",
        allow_runtime_socket=False,
    )
    if (
        target_pre_runtime_artifacts
        or (target / "mysql.sock").exists()
        or (target / "mysql.sock").is_symlink()
        or target_pre_id != source_id
        or target_pre_files != source_files
        or target_pre_bytes != source_bytes
    ):
        raise ServiceError("copied datadir per-file identity differs")
    target_metadata = _normalize_target_datadir(target, attempt_id)
    _run(["sync"])
    source_metadata_after = _metadata_summary(
        _tree_metadata_records(
            source, logical_prefix="datadir", allow_runtime_socket=True
        )
    )
    (
        source_id_after, source_files_after, source_bytes_after,
        unused_source_extents_after, source_runtime_artifacts_after,
    ) = _tree_records(
        source, False, logical_prefix="datadir",
        allow_runtime_socket=True,
    )
    (
        source_content_id_after, source_content_files_after,
        source_content_bytes_after, source_content_runtime_artifacts_after,
    ) = _tree_content_identity(
        source, logical_prefix="datadir", allow_runtime_socket=True
    )
    (
        target_id, target_files, target_bytes, target_extents,
        target_runtime_artifacts,
    ) = _tree_records(
        target, True, logical_prefix="datadir",
        allow_runtime_socket=False,
    )
    (
        target_content_id, target_content_files, target_content_bytes,
        target_content_runtime_artifacts,
    ) = _tree_content_identity(
        target, logical_prefix="datadir", allow_runtime_socket=False
    )
    if (
        source_metadata_after != source_metadata_before
        or source_id_after != source_id
        or source_files_after != source_files
        or source_bytes_after != source_bytes
        or source_runtime_artifacts_after != source_runtime_artifacts
        or source_content_id_after != source_content_id
        or source_content_files_after != source_content_files
        or source_content_bytes_after != source_content_bytes
        or source_content_runtime_artifacts_after
        != source_content_runtime_artifacts
    ):
        raise ServiceError("source template metadata/content changed")
    if (
        target_runtime_artifacts
        or target_content_runtime_artifacts
        or target_content_id != source_content_id
        or target_content_files != source_content_files
        or target_content_bytes != source_content_bytes
        or target_files != source_files
        or target_bytes != source_bytes
    ):
        raise ServiceError(
            "normalized target datadir content/readback differs"
        )
    sealed_bundle_after = _sealed_bundle_readback()
    if sealed_bundle_after != sealed_bundle_before:
        raise ServiceError("sealed bundle closure changed during physical copy")
    authority = {
        "schema_version": "mysql_xp_node02_physical_copy_authority_v2",
        "status":
            "PASS_BYTE_COPY_FIEMAP_AND_MYSQL_UID_NORMALIZATION",
        "attempt_id": attempt_id,
        "source_template": str(source),
        "target_datadir": str(target),
        "data_partition": DATA_PARTITION,
        "device_major_minor": DATA_PARTITION_MAJOR_MINOR,
        "throttle_device": THROTTLE_DEVICE,
        "throttle_device_major_minor": THROTTLE_DEVICE_MAJOR_MINOR,
        "storage_topology": storage_topology,
        "copy_argv": copy_argv,
        "copy_process_cpu_affinity": DB_CPUS,
        "sealed_bundle_before": sealed_bundle_before,
        "sealed_bundle_after": sealed_bundle_after,
        "sealed_bundle_unchanged": True,
        "sealed_bundle_write_count": 0,
        "source_metadata_before": source_metadata_before,
        "source_metadata_after": source_metadata_after,
        "source_metadata_unchanged": True,
        "source_tree_identity_sha256_before": source_id,
        "source_tree_identity_sha256_after": source_id_after,
        "source_content_identity_sha256_before": source_content_id,
        "source_content_identity_sha256_after": source_content_id_after,
        "target_pre_normalization_tree_identity_sha256": target_pre_id,
        "target_post_normalization_tree_identity_sha256": target_id,
        "target_content_identity_sha256": target_content_id,
        "target_metadata_readback": target_metadata,
        "permission_normalization": {
            "scope": "attempt_owned_datadir_only",
            "uid": MYSQL_UID,
            "gid": MYSQL_GID,
            "directory_mode": DATADIR_DIRECTORY_MODE,
            "regular_mode": DATADIR_REGULAR_MODE,
            "source_mutation_prohibited": True,
        },
        "template_manifest_sha256": template_manifest_sha,
        "excluded_runtime_artifacts": source_runtime_artifacts,
        "runtime_artifacts_followed": False,
        "runtime_artifacts_copied": False,
        "coreutils_8_22_compatible": True,
        "reflink_option_used": False,
        "reflink_clone_api_used": False,
        "full_physical_copy": True,
        "overlay_or_reflink": False,
        "tree_identity_sha256": source_content_id,
        "regular_file_count": source_files,
        "total_regular_file_bytes": source_bytes,
        "extent_audit": _verify_disjoint(source_extents, target_extents),
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    }
    atomic_json(output, authority)
    return authority


def _labels(
    bundle_id, attempt_id, token_sha, main_agent_token_sha,
    authorization_expires_at_utc, role
):
    labels = {
        "ra.formal_scope": "mysql_only",
        "ra.cross_engine_comparison_prohibited": "true",
        "ra.scenario": SCENARIO,
        "ra.bundle_id": bundle_id,
        "ra.attempt_id": attempt_id,
        "ra.handoff_token_sha256": token_sha,
        "ra.main_agent_token_sha256": main_agent_token_sha,
        "ra.authorization_expires_at_utc": authorization_expires_at_utc,
        "ra.role": role,
    }
    result = []
    for key in sorted(labels):
        result += ["--label", "%s=%s" % (key, labels[key])]
    return result


def _safe_image(value, label):
    if IMAGE_ID_RE.fullmatch(value or "") is None:
        raise ServiceError("%s exact local image ID is required" % label)
    return value


def _qualification_user(attempt_id):
    return "ra_xp_qual_" + hashlib.sha256(
        attempt_id.encode("utf-8")
    ).hexdigest()[:20]


def _private_main_agent_token(path, attempt_root, expected_sha256):
    path = Path(path).resolve()
    directory = path.parent
    expected = (
        attempt_root / PRIVATE_AUTHORITY_DIRECTORY / PRIVATE_MAIN_AGENT_TOKEN
    )
    if (
        path != expected
        or directory.is_symlink()
        or not directory.is_dir()
        or stat.S_IMODE(directory.stat().st_mode) != 0o700
        or path.is_symlink()
        or not path.is_file()
        or stat.S_IMODE(path.stat().st_mode) != 0o600
        or path.stat().st_uid != directory.stat().st_uid
        or path.stat().st_gid != directory.stat().st_gid
        or path.stat().st_size > 256
    ):
        raise ServiceError("attempt-owned private authority token differs")
    try:
        payload = path.read_bytes()
        token = payload.decode("ascii")
    except (OSError, UnicodeDecodeError):
        raise ServiceError("private authority token cannot be read")
    if (
        not token.endswith("\n")
        or "\n" in token[:-1]
        or TOKEN_RE.fullmatch(token[:-1]) is None
        or SHA_RE.fullmatch(expected_sha256 or "") is None
        or hashlib.sha256(token[:-1].encode("ascii")).hexdigest()
        != expected_sha256
    ):
        raise ServiceError("private authority token identity differs")
    return token[:-1]


def _private_credentials(directory, attempt_root, attempt_id):
    directory = Path(directory).resolve()
    expected = attempt_root / "private_credentials"
    if (
        directory != expected
        or directory.is_symlink()
        or not directory.is_dir()
        or stat.S_IMODE(directory.stat().st_mode) != 0o700
    ):
        raise ServiceError("attempt-owned private credential directory differs")
    files = {
        PRIVATE_MYSQL_DEFAULTS: directory / PRIVATE_MYSQL_DEFAULTS,
        PRIVATE_QUALIFICATION_CONFIG:
            directory / PRIVATE_QUALIFICATION_CONFIG,
    }
    if set(path.name for path in directory.iterdir()) != set(files):
        raise ServiceError("private credential file set differs")
    for path in files.values():
        if (
            path.is_symlink()
            or not path.is_file()
            or stat.S_IMODE(path.stat().st_mode) != 0o600
            or path.stat().st_uid != directory.stat().st_uid
            or path.stat().st_gid != directory.stat().st_gid
            or path.stat().st_size > 16384
        ):
            raise ServiceError("private credential file metadata differs")
    mysql_lines = [
        line.strip()
        for line in files[PRIVATE_MYSQL_DEFAULTS].read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]
    if len(mysql_lines) != 3 or mysql_lines[0] != "[client]":
        raise ServiceError("private MySQL defaults shape differs")
    mysql_values = {}
    for line in mysql_lines[1:]:
        if "=" not in line:
            raise ServiceError("private MySQL defaults field differs")
        key, value = line.split("=", 1)
        mysql_values[key.strip()] = value.strip()
    properties = {}
    for line in files[PRIVATE_QUALIFICATION_CONFIG].read_text(
        encoding="utf-8"
    ).splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in properties:
            raise ServiceError("duplicate private qualification property")
        properties[key.strip()] = value.strip()
    user = _qualification_user(attempt_id)
    password = mysql_values.get("password")
    if (
        set(mysql_values) != {"user", "password"}
        or mysql_values.get("user") != user
        or PASSWORD_RE.fullmatch(password or "") is None
        or properties.get("username") != user
        or properties.get("username_ap") != user
        or properties.get("password") != password
        or properties.get("password_ap") != password
    ):
        raise ServiceError("private qualification credential binding differs")
    return {
        "directory": str(directory),
        "destination": PRIVATE_CREDENTIAL_DESTINATION,
        "mysql_defaults": PRIVATE_MYSQL_DEFAULTS,
        "qualification_config": PRIVATE_QUALIFICATION_CONFIG,
        "user": user,
        "account_host": QUALIFICATION_ACCOUNT_HOST,
        "directory_mode": "0700",
        "file_mode": "0600",
        "_password": password,
        "_content_sha256": {
            name: sha256(path) for name, path in files.items()
        },
        "_properties": properties,
    }


def _qualification_account_sql(user, account_host, password):
    if (
        re.fullmatch(r"ra_xp_qual_[0-9a-f]{20}", user or "") is None
        or account_host != QUALIFICATION_ACCOUNT_HOST
        or PASSWORD_RE.fullmatch(password or "") is None
    ):
        raise ServiceError("qualification account input differs")
    return (
        "CREATE USER IF NOT EXISTS '%s'@'%s' IDENTIFIED BY 'CHANGE_ME';"
        "ALTER USER '%s'@'%s' IDENTIFIED BY 'CHANGE_ME';"
        "REVOKE ALL PRIVILEGES, GRANT OPTION FROM '%s'@'%s';"
        "GRANT PROCESS ON *.* TO '%s'@'%s';"
        "GRANT SELECT,INSERT,UPDATE,DELETE ON hybench_sf10x.* TO '%s'@'%s';"
        "FLUSH PRIVILEGES;"
        % (
            user, account_host, password,
            user, account_host, password,
            user, account_host,
            user, account_host,
            user, account_host,
        )
    )


def build_plan(
    bundle, database_image, client_image, config_dir, datadir,
    host_output_tmpfs, runtime_publication, credential_dir,
    attempt_id, handoff_token,
    main_agent_token_sha256, authorization_created_at_utc,
    authorization_expires_at_utc
):
    bundle = Path(bundle).resolve()
    manifest = verify_bundle(bundle)
    runtime = verify_runtime_publication(bundle, runtime_publication)
    token_sha = verify_attempt(attempt_id, handoff_token)
    if SHA_RE.fullmatch(main_agent_token_sha256 or "") is None:
        raise ServiceError("main-agent token SHA-256 differs")
    authority = verify_short_lived_authority(
        authorization_created_at_utc, authorization_expires_at_utc
    )
    database_image = _safe_image(database_image, "database")
    client_image = _safe_image(client_image, "client")
    config_dir = Path(config_dir).resolve()
    datadir = Path(datadir).resolve()
    host_output_tmpfs = Path(host_output_tmpfs).resolve()
    attempt_root = Path(RUNTIME_ROOT) / attempt_id
    if datadir != attempt_root / "datadir":
        raise ServiceError("datadir is not attempt-owned")
    if host_output_tmpfs != attempt_root / "host_measurement_tmpfs":
        raise ServiceError("host output tmpfs is not attempt-owned")
    credential = _private_credentials(
        credential_dir, attempt_root, attempt_id
    )
    expected_config_dir = (bundle / "qualification" / "config").resolve()
    if (
        config_dir != expected_config_dir
        or not config_dir.is_dir()
        or config_dir.is_symlink()
        or {
            candidate.name
            for candidate in config_dir.iterdir()
            if candidate.is_file() and not candidate.is_symlink()
        }
        != QUALIFICATION_CONFIG_FILES
        or any(
            candidate.is_symlink() or not candidate.is_file()
            for candidate in config_dir.iterdir()
        )
    ):
        raise ServiceError(
            "manifest-covered sealed qualification configuration required"
        )
    public_properties = {}
    for line in (
        config_dir / "hybench_xp_qualification.properties"
    ).read_text(encoding="utf-8").splitlines():
        if line and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            public_properties[key.strip()] = value.strip()
    credential_keys = {"username", "password", "username_ap", "password_ap"}
    if (
        set(credential["_properties"]) != set(public_properties)
        or any(
            credential["_properties"].get(key) != value
            for key, value in public_properties.items()
            if key not in credential_keys
        )
    ):
        raise ServiceError(
            "private qualification properties differ outside credentials"
        )
    client_mysql_mask = (bundle / CLIENT_MYSQL_MASK_RELATIVE).resolve()
    marker = client_mysql_mask / CLIENT_MYSQL_MASK_MARKER
    if (
        client_mysql_mask.is_symlink()
        or not client_mysql_mask.is_dir()
        or marker.is_symlink()
        or not marker.is_file()
        or list(client_mysql_mask.iterdir()) != [marker]
        or marker.read_bytes()
        != b"sealed read-only mask for image-declared /var/lib/mysql\n"
    ):
        raise ServiceError("sealed client /var/lib/mysql mask differs")
    db_name = "ra_mysql_xp_v3_db_%s" % attempt_id
    client_name = "ra_mysql_xp_v3_client_%s" % attempt_id
    db = [
        "docker", "create", "--name", db_name,
        "--cpuset-cpus", DB_CPUS, "--cpuset-mems", DB_MEMS,
        "--cpu-period", str(DB_CPU_PERIOD), "--cpu-quota", str(DB_CPU_QUOTA),
        "--memory", str(MEMORY), "--memory-swap", str(MEMORY),
        "--pids-limit", str(DB_PIDS),
        "--device-read-bps", "%s:%d" % (THROTTLE_DEVICE, READ_BPS),
        "--device-write-bps", "%s:%d" % (THROTTLE_DEVICE, WRITE_BPS),
        "--publish", "127.0.0.1:%d:3306" % PORT,
        # Docker 20.10.21 rejects the bare ``rw`` field in --mount syntax.
        # A bind mount is writable by default; the post-start inspect gate
        # below still requires Mounts[].RW to be exactly true.
        "--mount", "type=bind,src=%s,dst=/var/lib/mysql" % datadir,
        "--restart", "no",
    ] + _labels(
        manifest["bundle_id"], attempt_id, token_sha,
        main_agent_token_sha256, authorization_expires_at_utc, "database"
    ) + [
        database_image,
        "--datadir=/var/lib/mysql",
        "--innodb-buffer-pool-size=%d" % BUFFER_POOL,
    ]
    client = [
        "docker", "create", "--name", client_name,
        "--cpuset-cpus", CLIENT_CPUS, "--cpuset-mems", CLIENT_MEMS,
        "--cpu-period", str(CLIENT_CPU_PERIOD),
        "--cpu-quota", str(CLIENT_CPU_QUOTA),
        "--memory", str(MEMORY), "--memory-swap", str(MEMORY),
        "--pids-limit", str(CLIENT_PIDS),
        "--read-only", "--network", "host",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
        "--log-driver", "none", "--restart", "no",
        "--tmpfs",
        "%s:rw,nosuid,nodev,noexec,size=%d"
        % (MEASUREMENT_DESTINATION, TMPFS_BYTES),
        "--mount",
        "type=bind,src=%s,dst=%s,readonly"
        % (bundle, BUNDLE_DESTINATION),
        "--mount",
        "type=bind,src=%s,dst=%s,readonly" % (JDK_ROOT, RUNTIME_DESTINATION),
        "--mount",
        "type=bind,src=%s,dst=%s,readonly"
        % (config_dir, CONFIG_DESTINATION),
        "--mount",
        "type=bind,src=%s,dst=%s,readonly"
        % (credential["directory"], PRIVATE_CREDENTIAL_DESTINATION),
        "--mount",
        "type=bind,src=%s,dst=%s,readonly"
        % (client_mysql_mask, CLIENT_MYSQL_MASK_DESTINATION),
    ] + _labels(
        manifest["bundle_id"], attempt_id, token_sha,
        main_agent_token_sha256, authorization_expires_at_utc, "client"
    ) + [
        client_image, "sleep", "infinity"
    ]
    docker_sql_prefix = [
        "docker", "exec", client_name, "mysql",
        "--defaults-extra-file=%s/%s"
        % (PRIVATE_CREDENTIAL_DESTINATION, PRIVATE_MYSQL_DEFAULTS),
        "--protocol=TCP", "--host=127.0.0.1", "--port=%d" % PORT,
        "--batch", "--raw", "--skip-column-names",
    ]
    return {
        "schema_version": "mysql_xp_node02_adopted_service_plan_v1",
        "status": "PLAN_NOT_AUTHORITY",
        "scenario": SCENARIO,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "bundle_id": manifest["bundle_id"],
        "attempt_id": attempt_id,
        "handoff_token_sha256": token_sha,
        "main_agent_token_sha256": main_agent_token_sha256,
        "short_lived_authority": authority,
        "host": HOST,
        "runtime_publication": runtime,
        "database": {
            "name": db_name,
            "image": database_image,
            "create_argv": db,
            "start_argv": ["docker", "start", db_name],
            "datadir": str(datadir),
            "data_partition": DATA_PARTITION,
            "data_partition_major_minor": DATA_PARTITION_MAJOR_MINOR,
            "throttle_device": THROTTLE_DEVICE,
            "throttle_device_major_minor": THROTTLE_DEVICE_MAJOR_MINOR,
        },
        "client": {
            "name": client_name,
            "image": client_image,
            "create_argv": client,
            "start_argv": ["docker", "start", client_name],
            "private_tmpfs_destination": MEASUREMENT_DESTINATION,
            "private_tmpfs_bytes": TMPFS_BYTES,
            "mysql_volume_mask_source": str(client_mysql_mask),
            "mysql_volume_mask_destination": CLIENT_MYSQL_MASK_DESTINATION,
            "anonymous_volume_prohibited": True,
            "private_credentials": {
                key: value for key, value in credential.items()
                if not key.startswith("_")
            },
        },
        "sql_stdout_bridge": {
            "kind": "collector_managed_client_docker_exec_stdout_v1",
            "host_mysql_binary_prohibited": True,
            "argv_prefix": docker_sql_prefix,
            "stdout_only": True,
            "host_output_tmpfs": str(host_output_tmpfs),
            "client_private_tmpfs_not_host_bound": True,
            "mysql_defaults_file":
                PRIVATE_CREDENTIAL_DESTINATION + "/"
                + PRIVATE_MYSQL_DEFAULTS,
            "qualification_config":
                PRIVATE_CREDENTIAL_DESTINATION + "/"
                + PRIVATE_QUALIFICATION_CONFIG,
        },
        "container_replacement_prohibited": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    }


def _inspect(name):
    completed = _run(["docker", "inspect", name], check=False)
    if completed.returncode != 0:
        if "No such" in completed.stderr:
            return None
        raise ServiceError("docker inspect failed: %s" % completed.stderr.strip())
    value = json.loads(completed.stdout)
    if not isinstance(value, list) or len(value) != 1:
        raise ServiceError("docker inspect result differs")
    return value[0]


def _proc_start_ticks(pid):
    raw = Path("/proc/%d/stat" % pid).read_text(encoding="ascii")
    right = raw.rfind(")")
    return int(raw[right + 2:].split()[19])


def _static_mounts(row):
    """Return the explicitly defined static docker-inspect mount projection."""
    mounts = row.get("Mounts")
    if not isinstance(mounts, list):
        raise ServiceError("docker inspect Mounts must be a list")
    result = []
    allowed = set(STATIC_MOUNT_FIELDS)
    for item in mounts:
        if not isinstance(item, dict):
            raise ServiceError("docker inspect Mounts entry differs")
        unknown = set(item) - allowed
        if unknown:
            raise ServiceError(
                "unclassified docker inspect Mounts fields: %s"
                % ",".join(sorted(unknown))
            )
        result.append({
            key: item.get(key) for key in STATIC_MOUNT_FIELDS if key in item
        })
    return sorted(
        result,
        key=lambda item: (
            str(item.get("Destination", "")),
            str(item.get("Source", "")),
            str(item.get("Type", "")),
        ),
    )


def _static_inspect_identity(row):
    """Hash only declared static inspect state.

    Config and HostConfig are defined as whole static Docker configuration
    objects. Mounts is a strict, field-declared projection. NetworkSettings is
    deliberately excluded because Docker mutates it while a container starts;
    the static network mode and published-port declaration are bound again
    explicitly from HostConfig.
    """
    config = row.get("Config")
    host = row.get("HostConfig")
    if not isinstance(config, dict) or not isinstance(host, dict):
        raise ServiceError("docker inspect static Config/HostConfig differs")
    payload = {
        "Config": config,
        "HostConfig": host,
        "Mounts": _static_mounts(row),
        "static_network": {
            "network_mode": host.get("NetworkMode"),
            "published_ports": host.get("PortBindings"),
        },
    }
    return {
        "schema_version": STATIC_INSPECT_IDENTITY_SCHEMA,
        "field_contract": {
            "Config": "entire_object_static",
            "HostConfig": "entire_object_static",
            "Mounts": list(STATIC_MOUNT_FIELDS),
            "static_network": [
                "HostConfig.NetworkMode", "HostConfig.PortBindings",
            ],
            "NetworkSettings": "excluded_dynamic_observation_not_ignored",
        },
        "payload": payload,
        "sha256": sha_bytes(canonical(payload)),
    }


def _dynamic_network_observation(row):
    network = row.get("NetworkSettings")
    if not isinstance(network, dict):
        raise ServiceError("docker inspect NetworkSettings must be an object")
    return {
        "schema_version": "mysql_xp_dynamic_network_observation_v1",
        "classification": "dynamic_observation_not_static_identity",
        "keys": sorted(network),
        "sha256": sha_bytes(canonical(network)),
        "value": network,
    }


def _config_identity(row):
    """Compatibility alias for the static identity hash."""
    return _static_inspect_identity(row)["sha256"]


def _mount_map(row):
    return {
        str(item.get("Destination")): item
        for item in (row.get("Mounts") or [])
        if isinstance(item, dict)
    }


def _validate_container(row, plan, role):
    state = row.get("State") or {}
    host = row.get("HostConfig") or {}
    config = row.get("Config") or {}
    labels = config.get("Labels") or {}
    expected_name = plan[role]["name"]
    expected_image = plan[role]["image"]
    for key, expected in {
        "ra.formal_scope": "mysql_only",
        "ra.cross_engine_comparison_prohibited": "true",
        "ra.scenario": SCENARIO,
        "ra.bundle_id": plan["bundle_id"],
        "ra.attempt_id": plan["attempt_id"],
        "ra.handoff_token_sha256": plan["handoff_token_sha256"],
        "ra.main_agent_token_sha256": plan["main_agent_token_sha256"],
        "ra.authorization_expires_at_utc":
            plan["short_lived_authority"]["expires_at_utc"],
        "ra.role": role,
    }.items():
        if labels.get(key) != expected:
            raise ServiceError("%s label differs: %s" % (role, key))
    pid = int(state.get("Pid") or 0)
    if state.get("Running") is not True or pid <= 1:
        raise ServiceError("%s container has no live PID" % role)
    if config.get("Image") != expected_image:
        raise ServiceError("%s immutable image differs" % role)
    if row.get("Image") != expected_image:
        raise ServiceError("%s resolved image ID differs" % role)
    expected = (
        (DB_CPUS, DB_MEMS, DB_CPU_QUOTA, DB_PIDS)
        if role == "database"
        else (CLIENT_CPUS, CLIENT_MEMS, CLIENT_CPU_QUOTA, CLIENT_PIDS)
    )
    if (
        host.get("CpusetCpus") != expected[0]
        or host.get("CpusetMems") != expected[1]
        or int(host.get("CpuPeriod") or 0) != 100000
        or int(host.get("CpuQuota") or 0) != expected[2]
        or int(host.get("Memory") or 0) != MEMORY
        or int(host.get("MemorySwap") or 0) != MEMORY
        or int(host.get("PidsLimit") or 0) != expected[3]
        or host.get("RestartPolicy", {}).get("Name") != "no"
    ):
        raise ServiceError("%s resource envelope differs" % role)
    mounts = _mount_map(row)
    if role == "database":
        mount = mounts.get("/var/lib/mysql") or {}
        read_bps = host.get("BlkioDeviceReadBps") or []
        write_bps = host.get("BlkioDeviceWriteBps") or []
        port_bindings = host.get("PortBindings") or {}
        mysql_binding = port_bindings.get("3306/tcp") or []
        command = config.get("Cmd") or []
        if (
            mount.get("Source") != plan["database"]["datadir"]
            or mount.get("RW") is not True
            or _device_major_minor(Path(plan["database"]["datadir"]))
            != DATA_PARTITION_MAJOR_MINOR
            or len(read_bps) != 1
            or read_bps[0].get("Path") != THROTTLE_DEVICE
            or int(read_bps[0].get("Rate") or 0) != READ_BPS
            or len(write_bps) != 1
            or write_bps[0].get("Path") != THROTTLE_DEVICE
            or int(write_bps[0].get("Rate") or 0) != WRITE_BPS
            or mysql_binding
            != [{"HostIp": "127.0.0.1", "HostPort": str(PORT)}]
            or "--datadir=/var/lib/mysql" not in command
            or "--innodb-buffer-pool-size=%d" % BUFFER_POOL not in command
        ):
            raise ServiceError("database storage/IO/port/config differs")
    else:
        tmpfs = host.get("Tmpfs")
        options = (tmpfs or {}).get(MEASUREMENT_DESTINATION, "")
        required = {
            "rw", "nosuid", "nodev", "noexec", "size=%d" % TMPFS_BYTES
        }
        expected_mounts = {
            BUNDLE_DESTINATION:
                next(
                    item.split(",src=", 1)[1].split(",dst=", 1)[0]
                    for item in plan["client"]["create_argv"]
                    if isinstance(item, str)
                    and ",dst=%s," % BUNDLE_DESTINATION in item
                ),
            RUNTIME_DESTINATION: JDK_ROOT,
            CONFIG_DESTINATION:
                next(
                    item.split(",src=", 1)[1].split(",dst=", 1)[0]
                    for item in plan["client"]["create_argv"]
                    if isinstance(item, str)
                    and ",dst=%s," % CONFIG_DESTINATION in item
                ),
            PRIVATE_CREDENTIAL_DESTINATION:
                plan["client"]["private_credentials"]["directory"],
            CLIENT_MYSQL_MASK_DESTINATION:
                plan["client"]["mysql_volume_mask_source"],
        }
        mount_rows = row.get("Mounts") or []
        if (
            len(mount_rows) != len(expected_mounts)
            or set(mounts) != set(expected_mounts)
            or any(
                item.get("Type") == "volume"
                or str(item.get("Source") or "").startswith(
                    PG_NVME2_PATH_PREFIX
                )
                for item in mount_rows
                if isinstance(item, dict)
            )
        ):
            raise ServiceError(
                "client anonymous volume or unexpected/PG-device mount"
            )
        if (
            host.get("ReadonlyRootfs") is not True
            or host.get("NetworkMode") != "host"
            or "ALL" not in (host.get("CapDrop") or [])
            or not any(
                str(value).replace("=", ":").lower()
                in ("no-new-privileges", "no-new-privileges:true")
                for value in (host.get("SecurityOpt") or [])
            )
            or (host.get("LogConfig") or {}).get("Type") != "none"
            or not isinstance(tmpfs, dict)
            or set(tmpfs) != {MEASUREMENT_DESTINATION}
            or not required.issubset(set(options.split(",")))
            or any(
                (mounts.get(destination) or {}).get("Type") != "bind"
                or (mounts.get(destination) or {}).get("RW") is not False
                or (mounts.get(destination) or {}).get("Source")
                != expected_source
                for destination, expected_source in expected_mounts.items()
            )
        ):
            raise ServiceError("client read-only/private-tmpfs envelope differs")
        if any(
            (item.get("Source") == plan["sql_stdout_bridge"]["host_output_tmpfs"])
            for item in (row.get("Mounts") or [])
            if isinstance(item, dict)
        ):
            raise ServiceError("host output tmpfs must not be client-mounted")
    static_identity = _static_inspect_identity(row)
    dynamic_network = _dynamic_network_observation(row)
    return {
        "name": expected_name,
        "container_id": row.get("Id"),
        "image_id": row.get("Image"),
        "created": row.get("Created"),
        "pid": pid,
        "pid_starttime_ticks": _proc_start_ticks(pid),
        "inspect_config_sha256": static_identity["sha256"],
        "static_inspect_identity": static_identity,
        "dynamic_network_settings_observation": dynamic_network,
    }


def _decode_mountinfo_path(value):
    replacements = {
        "\\040": " ",
        "\\011": "\t",
        "\\012": "\n",
        "\\134": "\\",
    }
    for escaped, decoded in replacements.items():
        value = value.replace(escaped, decoded)
    return value


def _mountinfo_rows(mountinfo_path=Path("/proc/self/mountinfo")):
    rows = []
    for line in Path(mountinfo_path).read_text(
        encoding="utf-8"
    ).splitlines():
        fields = line.split()
        if "-" not in fields:
            raise ServiceError("malformed /proc/self/mountinfo entry")
        separator = fields.index("-")
        if separator < 6 or len(fields) <= separator + 3:
            raise ServiceError("malformed /proc/self/mountinfo entry")
        rows.append(
            {
                "major_minor": fields[2],
                "root": _decode_mountinfo_path(fields[3]),
                "mount_point": _decode_mountinfo_path(fields[4]),
                "mount_options": set(fields[5].split(",")),
                "filesystem": fields[separator + 1],
                "source": _decode_mountinfo_path(fields[separator + 2]),
                "super_options": set(fields[separator + 3].split(",")),
            }
        )
    return rows


def _exact_mountinfo(path, mountinfo_path=Path("/proc/self/mountinfo")):
    resolved = Path(path).resolve()
    matches = [
        row for row in _mountinfo_rows(mountinfo_path)
        if Path(row["mount_point"]).resolve() == resolved
    ]
    if len(matches) != 1:
        raise ServiceError("host collector output is not an exact mount")
    return matches[0]


def _validate_storage_topology(
    mountinfo_path=Path("/proc/self/mountinfo"),
    sys_dev_block=Path("/sys/dev/block"),
    swaps_path=Path("/proc/swaps"),
    block_stat=os.stat,
):
    if (
        _block_node_major_minor(DATA_PARTITION, block_stat)
        != DATA_PARTITION_MAJOR_MINOR
        or _block_node_major_minor(THROTTLE_DEVICE, block_stat)
        != THROTTLE_DEVICE_MAJOR_MINOR
    ):
        raise ServiceError("frozen block node identity differs")
    partition_link = Path(sys_dev_block) / DATA_PARTITION_MAJOR_MINOR
    parent_link = Path(sys_dev_block) / THROTTLE_DEVICE_MAJOR_MINOR
    if not partition_link.exists() or not parent_link.exists():
        raise ServiceError("frozen block device sysfs identity is absent")
    partition = partition_link.resolve()
    parent = parent_link.resolve()
    if (
        partition.name != Path(DATA_PARTITION).name
        or parent.name != Path(THROTTLE_DEVICE).name
        or partition.parent != parent
        or not (partition / "partition").is_file()
        or (partition / "dev").read_text(encoding="ascii").strip()
        != DATA_PARTITION_MAJOR_MINOR
        or (parent / "dev").read_text(encoding="ascii").strip()
        != THROTTLE_DEVICE_MAJOR_MINOR
    ):
        raise ServiceError("partition to throttle-parent topology differs")
    partitions = sorted(
        child.name for child in parent.iterdir()
        if (child / "partition").is_file()
    )
    if partitions != [Path(DATA_PARTITION).name]:
        raise ServiceError("throttle parent does not have one unique partition")
    for node in (parent, partition):
        holders = node / "holders"
        if not holders.is_dir() or list(holders.iterdir()):
            raise ServiceError("throttle parent or partition has block holders")
    related = [
        row for row in _mountinfo_rows(mountinfo_path)
        if row["major_minor"] in {
            DATA_PARTITION_MAJOR_MINOR, THROTTLE_DEVICE_MAJOR_MINOR
        }
    ]
    if (
        len(related) != 1
        or related[0]["major_minor"] != DATA_PARTITION_MAJOR_MINOR
        or related[0]["root"] != "/"
        or Path(related[0]["mount_point"]).resolve()
        != Path(MOUNT).resolve()
        or related[0]["source"] != DATA_PARTITION
        or "rw" not in (
            related[0]["mount_options"] | related[0]["super_options"]
        )
    ):
        raise ServiceError("parent disk has another or incorrect mount")
    swaps = Path(swaps_path).read_text(encoding="utf-8").splitlines()
    active_swaps = {
        line.split()[0] for line in swaps[1:] if line.split()
    }
    if DATA_PARTITION in active_swaps or THROTTLE_DEVICE in active_swaps:
        raise ServiceError("parent disk is in use as swap")
    return {
        "data_mount": MOUNT,
        "data_partition": DATA_PARTITION,
        "data_partition_major_minor": DATA_PARTITION_MAJOR_MINOR,
        "throttle_parent": THROTTLE_DEVICE,
        "throttle_parent_major_minor": THROTTLE_DEVICE_MAJOR_MINOR,
        "unique_partition_inventory": partitions,
        "related_mount_count": 1,
        "parent_or_partition_holders": [],
        "parent_or_partition_swap_entries": [],
        "parent_throttle_required_for_cgroup_v1": True,
    }


def _validate_host_tmpfs(
    path, mountinfo_path=Path("/proc/self/mountinfo"),
    statvfs=os.statvfs
):
    path = Path(path)
    if path.is_symlink() or not path.is_dir():
        raise ServiceError("host collector output path must be a directory")
    row = _exact_mountinfo(path, mountinfo_path)
    required = {"rw", "nosuid", "nodev", "noexec"}
    observed_options = row["mount_options"] | row["super_options"]
    if row["filesystem"] != "tmpfs":
        raise ServiceError("host collector output is not tmpfs")
    if Path(row["mount_point"]).resolve() != path.resolve():
        raise ServiceError("host collector output is not a private mount")
    if not required.issubset(observed_options):
        raise ServiceError("host collector tmpfs options differ")
    filesystem = statvfs(str(path))
    size_bytes = int(filesystem.f_frsize) * int(filesystem.f_blocks)
    if size_bytes < TMPFS_BYTES:
        raise ServiceError("host collector tmpfs is too small")
    return {
        "path": str(path.resolve()),
        "filesystem": "tmpfs",
        "size_bytes": size_bytes,
        "required_mount_options": sorted(required),
        "observed_mount_options": sorted(observed_options),
        "mount_source": row["source"],
        "legacy_findmnt_dependency": False,
        "not_mounted_into_client": True,
    }


def _validate_port_available():
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", PORT))
    except OSError:
        raise ServiceError("frozen MySQL port is already in use")
    finally:
        probe.close()


def _validate_copy(plan, copy_authority, require_pristine_target=False):
    copy_authority = Path(copy_authority)
    verify_sidecar(copy_authority, "physical copy authority")
    document = load(copy_authority, "physical copy authority")
    if (
        document.get("schema_version")
        != "mysql_xp_node02_physical_copy_authority_v2"
        or document.get("status")
        != "PASS_BYTE_COPY_FIEMAP_AND_MYSQL_UID_NORMALIZATION"
        or document.get("attempt_id") != plan["attempt_id"]
        or document.get("target_datadir") != plan["database"]["datadir"]
        or document.get("data_partition") != DATA_PARTITION
        or document.get("device_major_minor")
        != DATA_PARTITION_MAJOR_MINOR
        or document.get("throttle_device") != THROTTLE_DEVICE
        or document.get("throttle_device_major_minor")
        != THROTTLE_DEVICE_MAJOR_MINOR
        or document.get("storage_topology") != _validate_storage_topology()
        or document.get("source_template") != TEMPLATE_DATADIR
        or document.get("copy_argv")
        != _copy_argv(Path(plan["database"]["datadir"]))
        or document.get("copy_process_cpu_affinity") != DB_CPUS
        or document.get("sealed_bundle_before")
        != document.get("sealed_bundle_after")
        or document.get("sealed_bundle_unchanged") is not True
        or document.get("sealed_bundle_write_count") != 0
        or (document.get("sealed_bundle_before") or {}).get(
            "bundle_id"
        ) != plan["bundle_id"]
        or (document.get("sealed_bundle_before") or {}).get(
            "full_closure_verified"
        ) is not True
        or (document.get("sealed_bundle_before") or {}).get(
            "bytecode_write_disabled"
        ) is not True
        or (document.get("sealed_bundle_before") or {}).get(
            "bytecode_artifacts"
        ) != []
        or document.get("source_metadata_before")
        != document.get("source_metadata_after")
        or document.get("source_metadata_unchanged") is not True
        or document.get("source_tree_identity_sha256_before")
        != document.get("source_tree_identity_sha256_after")
        or document.get("source_content_identity_sha256_before")
        != document.get("source_content_identity_sha256_after")
        or document.get("target_content_identity_sha256")
        != document.get("source_content_identity_sha256_before")
        or document.get("tree_identity_sha256")
        != document.get("source_content_identity_sha256_before")
        or document.get("permission_normalization") != {
            "scope": "attempt_owned_datadir_only",
            "uid": MYSQL_UID,
            "gid": MYSQL_GID,
            "directory_mode": DATADIR_DIRECTORY_MODE,
            "regular_mode": DATADIR_REGULAR_MODE,
            "source_mutation_prohibited": True,
        }
        or (document.get("target_metadata_readback") or {}).get("root")
        != str(Path(plan["database"]["datadir"]).resolve())
        or (document.get("target_metadata_readback") or {}).get(
            "required_uid"
        ) != MYSQL_UID
        or (document.get("target_metadata_readback") or {}).get(
            "required_gid"
        ) != MYSQL_GID
        or (document.get("target_metadata_readback") or {}).get(
            "directory_mode"
        ) != DATADIR_DIRECTORY_MODE
        or (document.get("target_metadata_readback") or {}).get(
            "regular_mode"
        ) != DATADIR_REGULAR_MODE
        or (document.get("target_metadata_readback") or {}).get(
            "symlink_count"
        ) != 0
        or (document.get("target_metadata_readback") or {}).get(
            "owner_mode_inventory"
        ) != [
            {
                "type": "directory",
                "uid": MYSQL_UID,
                "gid": MYSQL_GID,
                "mode": DATADIR_DIRECTORY_MODE,
            },
            {
                "type": "regular",
                "uid": MYSQL_UID,
                "gid": MYSQL_GID,
                "mode": DATADIR_REGULAR_MODE,
            },
        ]
        or document.get("template_manifest_sha256")
        != _verify_template_manifest()
        or document.get("excluded_runtime_artifacts")
        != [{
            "path": RUNTIME_SOCKET_RELATIVE,
            "type": "runtime_socket_symlink",
            "target": RUNTIME_SOCKET_TARGET,
            "copied": False,
            "followed": False,
        }]
        or document.get("runtime_artifacts_followed") is not False
        or document.get("runtime_artifacts_copied") is not False
        or document.get("reflink_option_used") is not False
        or document.get("reflink_clone_api_used") is not False
        or document.get("full_physical_copy") is not True
        or document.get("overlay_or_reflink") is not False
        or (document.get("extent_audit") or {}).get(
            "source_target_physical_overlap_count"
        ) != 0
        or (document.get("extent_audit") or {}).get(
            "shared_extent_flags_seen"
        ) != 0
    ):
        raise ServiceError("physical copy authority differs")
    if require_pristine_target:
        source_metadata = _metadata_summary(
            _tree_metadata_records(
                Path(TEMPLATE_DATADIR), logical_prefix="datadir",
                allow_runtime_socket=True,
            )
        )
        (
            source_content, source_files, source_bytes,
            source_runtime_artifacts,
        ) = _tree_content_identity(
            Path(TEMPLATE_DATADIR), logical_prefix="datadir",
            allow_runtime_socket=True,
        )
        target = Path(plan["database"]["datadir"])
        target_metadata = _target_metadata_readback(target)
        (
            target_content, target_files, target_bytes,
            target_runtime_artifacts,
        ) = _tree_content_identity(
            target, logical_prefix="datadir",
            allow_runtime_socket=False,
        )
        if (
            source_metadata != document.get("source_metadata_before")
            or source_content
            != document.get("source_content_identity_sha256_before")
            or target_metadata != document.get("target_metadata_readback")
            or target_content != document.get(
                "target_content_identity_sha256"
            )
            or target_content != source_content
            or target_files != source_files
            or target_bytes != source_bytes
            or target_runtime_artifacts
            or source_runtime_artifacts
            != document.get("excluded_runtime_artifacts")
        ):
            raise ServiceError(
                "pristine normalized datadir readback differs before create"
            )
    return sha256(copy_authority)


def adopt(plan, copy_authority, output):
    if socket.gethostname().split(".")[0] != HOST:
        raise ServiceError("adoption is host-bound to node02")
    copy_sha = _validate_copy(plan, copy_authority)
    database = _inspect(plan["database"]["name"])
    client = _inspect(plan["client"]["name"])
    if database is None or client is None:
        raise ServiceError("both precreated containers are required")
    db = _validate_container(database, plan, "database")
    cl = _validate_container(client, plan, "client")
    storage_topology = _validate_storage_topology()
    tmpfs = _validate_host_tmpfs(plan["sql_stdout_bridge"]["host_output_tmpfs"])
    result = {
        "schema_version": "mysql_xp_node02_adopted_service_authority_v1",
        "status": "PASS_ADOPTED_SAME_LIVE_SERVICE",
        "scenario": SCENARIO,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "bundle_id": plan["bundle_id"],
        "attempt_id": plan["attempt_id"],
        "handoff_token_sha256": plan["handoff_token_sha256"],
        "main_agent_token_sha256": plan["main_agent_token_sha256"],
        "short_lived_authority": plan["short_lived_authority"],
        "database": db,
        "client": cl,
        "physical_datadir": plan["database"]["datadir"],
        "physical_copy_authority_sha256": copy_sha,
        "runtime_publication": plan["runtime_publication"],
        "storage_topology": storage_topology,
        "host_output_tmpfs": tmpfs,
        "sql_stdout_bridge": plan["sql_stdout_bridge"],
        "private_credentials": plan["client"]["private_credentials"],
        "host_mysql_binary_used": False,
        "new_container_or_datadir_for_later_stage_prohibited": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    }
    atomic_json(output, result)
    return result


def bootstrap(plan, copy_authority, output, main_agent_token):
    if socket.gethostname().split(".")[0] != HOST:
        raise ServiceError("bootstrap is host-bound to node02")
    if os.geteuid() != 0:
        raise ServiceError(
            "bootstrap pristine datadir validation requires effective UID 0"
        )
    if (
        not isinstance(main_agent_token, str)
        or hashlib.sha256(main_agent_token.encode("utf-8")).hexdigest()
        != plan["main_agent_token_sha256"]
    ):
        raise ServiceError("main-agent bootstrap token differs")
    _validate_copy(plan, copy_authority, require_pristine_target=True)
    _validate_storage_topology()
    _validate_host_tmpfs(plan["sql_stdout_bridge"]["host_output_tmpfs"])
    _validate_port_available()
    for role in ("database", "client"):
        name = plan[role]["name"]
        if _inspect(name) is not None:
            raise ServiceError(
                "%s already exists; bootstrap refuses replacement/reuse" % name
            )
    authority = plan["short_lived_authority"]
    verify_short_lived_authority(
        authority["created_at_utc"], authority["expires_at_utc"]
    )
    credential_path = Path(
        plan["client"]["private_credentials"]["directory"]
    )
    credential_before = _private_credentials(
        credential_path, Path(RUNTIME_ROOT) / plan["attempt_id"],
        plan["attempt_id"],
    )
    _run(plan["database"]["create_argv"])
    _run(plan["client"]["create_argv"])
    _run(plan["database"]["start_argv"])
    readiness = None
    for _unused in range(120):
        readiness = _run(
            [
                "docker", "exec", plan["database"]["name"],
                "mysql", "--user=root", "--protocol=socket", "--batch", "--raw",
                "--skip-column-names", "--execute", "SELECT 1",
            ],
            check=False,
        )
        if readiness.returncode == 0 and readiness.stdout.strip() == "1":
            break
        time.sleep(1)
    else:
        raise ServiceError("database socket bootstrap readiness failed")
    user = credential_before["user"]
    account_host = credential_before["account_host"]
    password = credential_before["_password"]
    sql = _qualification_account_sql(
        user, account_host, password
    )
    _run_private_stdin(
        [
            "docker", "exec", "-i", plan["database"]["name"],
            "mysql", "--user=root", "--protocol=socket", "--batch", "--raw",
        ],
        sql,
    )
    _run(plan["client"]["start_argv"])
    verification = _run(
        [
            "docker", "exec", plan["client"]["name"],
            "mysql",
            "--defaults-extra-file=%s/%s"
            % (PRIVATE_CREDENTIAL_DESTINATION, PRIVATE_MYSQL_DEFAULTS),
            "--protocol=TCP", "--host=127.0.0.1",
            "--port=%d" % PORT, "--batch", "--raw",
            "--skip-column-names", "--execute",
            "SELECT @@port, CURRENT_USER()",
        ],
        check=False,
    )
    expected = "%d\t%s@%s" % (PORT, user, account_host)
    if verification.returncode != 0 or verification.stdout.strip() != expected:
        raise ServiceError("private qualification account verification failed")
    credential_after = _private_credentials(
        credential_path, Path(RUNTIME_ROOT) / plan["attempt_id"],
        plan["attempt_id"],
    )
    if (
        credential_before["_content_sha256"]
        != credential_after["_content_sha256"]
    ):
        raise ServiceError("private credential changed during provisioning")
    return adopt(plan, copy_authority, output)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--prepare-copy", action="store_true")
    modes.add_argument("--plan", action="store_true")
    modes.add_argument("--bootstrap", action="store_true")
    modes.add_argument("--adopt", action="store_true")
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--database-image")
    parser.add_argument("--client-image")
    parser.add_argument("--config-dir", type=Path)
    parser.add_argument("--datadir", type=Path)
    parser.add_argument("--host-output-tmpfs", type=Path)
    parser.add_argument("--runtime-publication", type=Path)
    parser.add_argument("--credential-dir", type=Path)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--handoff-token")
    parser.add_argument("--main-agent-token-sha256")
    parser.add_argument("--main-agent-token-file", type=Path)
    parser.add_argument("--authorization-created-at-utc")
    parser.add_argument("--authorization-expires-at-utc")
    parser.add_argument("--physical-copy-authority", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.prepare_copy:
        if args.output is None:
            raise ServiceError("--prepare-copy requires --output")
        result = prepare_copy(args.attempt_id, args.output)
    else:
        required = (
            args.bundle, args.database_image, args.client_image,
            args.config_dir, args.datadir, args.host_output_tmpfs,
            args.runtime_publication, args.credential_dir,
            args.handoff_token,
            args.main_agent_token_sha256,
            args.authorization_created_at_utc,
            args.authorization_expires_at_utc,
        )
        if any(value is None for value in required):
            raise ServiceError("plan/bootstrap/adopt inputs are incomplete")
        plan = build_plan(
            args.bundle, args.database_image, args.client_image,
            args.config_dir, args.datadir, args.host_output_tmpfs,
            args.runtime_publication, args.credential_dir,
            args.attempt_id, args.handoff_token,
            args.main_agent_token_sha256,
            args.authorization_created_at_utc,
            args.authorization_expires_at_utc,
        )
        if args.plan:
            result = plan
        else:
            if args.physical_copy_authority is None or args.output is None:
                raise ServiceError(
                    "bootstrap/adopt requires copy authority and output"
                )
            if args.bootstrap:
                if args.main_agent_token_file is None:
                    raise ServiceError(
                        "bootstrap requires --main-agent-token-file"
                    )
                main_agent_token = _private_main_agent_token(
                    args.main_agent_token_file,
                    Path(RUNTIME_ROOT) / args.attempt_id,
                    args.main_agent_token_sha256,
                )
                result = bootstrap(
                    plan, args.physical_copy_authority, args.output,
                    main_agent_token
                )
            else:
                result = adopt(plan, args.physical_copy_authority, args.output)
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
