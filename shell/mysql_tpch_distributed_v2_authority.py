#!/usr/bin/env python3
"""TPC-H distributed-v2 bootstrap, SQL bridge, and same-live authority.

This host-local tool has no SSH implementation.  Mutating bootstrap execution
requires a short-lived, main-agent-signed specification and an out-of-band
token.  Every later stage adopts the exact database and client containers
created by bootstrap; starting a replacement planner is prohibited.
"""

from __future__ import annotations

import argparse
import bisect
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import socket
import stat
import struct
import subprocess
import sys
import time
from typing import Any, Mapping

sys.dont_write_bytecode = True

try:
    from .mysql_tpch_client_immutable_identity_v1 import (
        client_container_immutable_projection,
    )
except ImportError:
    from mysql_tpch_client_immutable_identity_v1 import (
        client_container_immutable_projection,
    )


CONTRACT_SHA256 = (
    "ea0f3a13d44f1f143e63fcd19003f23e90486cfa297df4b07fea99314af82d7b"
)
BASE_FOUR_SCENE_CONTRACT_SHA256 = (
    "52ba2faa2035dfa9035cb0f6ab75d4461430000097f846c3e94e4a81f99aea58"
)
SCENARIO = "tpch_memory"
HOSTNAME = "node04"
DEVICE = "/dev/nvme0n1"
MAJOR_MINOR = "259:1"
MOUNT = "/mnt/nvme0n1"
TEMPLATE = "/mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch/template"
RUNTIME_ROOT = (
    "/mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch/"
    "runtime_algorithm_c_successor_20260729"
)
PYTHON_RUNTIME_SOURCE = "/home/anonymous/recost_runtime_py311_24_11_1"
PYTHON_RUNTIME_TREE_CONTENT_SHA256 = (
    "3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89"
)
PYTHON_RUNTIME_MANIFEST_SHA256 = (
    "4eeab2e0e5302d20a1fbf4a664fc4938abed84c06f3415e11a462b6e616696c4"
)
PYTHON_RUNTIME_TARGET = (
    RUNTIME_ROOT + "/runtime/python311-" + PYTHON_RUNTIME_TREE_CONTENT_SHA256
)
INVALIDATED_BUNDLE_ID = (
    "948cf38ff8d5c6757143c543fbec6002f1ea638a43d9f935286a256f29620d6c"
)
INVALIDATED_CLOSURE_BUNDLE_ID = (
    "764a5b03627b7a776c59114cc17687ef5bf01cf937c2ca1e971cd9b70a3cb40a"
)
INVALIDATED_LSOF_BUNDLE_ID = (
    "795ee2e905dc680fa593cc56892b42a3d3cb43928d09a5aae7c04a597d3875dc"
)
INVALIDATED_FALLBACK_BUNDLE_ID = (
    "ff1769e482e9e384c038271326e802d515b7aa8408ae895b77cc02b4cbfa04c5"
)
INVALIDATED_NONROOT_FALLBACK_BUNDLE_ID = (
    "2b777b5b66aa03a7fb190a4f1d654ae89f1f277bf16a4cc759acc32d955cdbe1"
)
INVALIDATED_SOCKET_IDENTITY_BUNDLE_ID = (
    "cee512b778291f8cdb335a4dc07bba56646a45d1f5e4b91a82c8e03c57442e0c"
)
INVALIDATED_PERMISSION_BUNDLE_ID = (
    "716544c9dba781b0cea9548225aa61daf4ae04ce141300f76df762b779c061ac"
)
INVALIDATED_PERMISSION_READBACK_BUNDLE_ID = (
    "c659ae9f5d3743c341095c976b03ea6c6b7469c213ede618f7eaf7fa7f597d46"
)
INVALIDATED_TREE_DIGEST_BUNDLE_ID = (
    "6417f98e939aa67ed8042559e716916abb1b798034e4116294190d5e41b12fc4"
)
INVALIDATED_TREE_DIGEST_BYTES_BUNDLE_ID = (
    "703b1382f0833463c42f02566f75bac490034cf6ed915c21bd1b87efaee23728"
)
INVALIDATED_METADATA_SENSITIVE_TREE_IDENTITY_BUNDLE_ID = (
    "9372c125b38343841032592a9b8ce7d0fc8cc7096ced2149ff24de2e1cb98550"
)
INVALIDATED_EFFECTIVE_BLKIO_BUNDLE_ID = (
    "d8ea437cfdc33b197113c5f15ebedb8173032a08b0cd18d19395513787dd4c4b"
)
INVALIDATED_EFFECTIVE_BLKIO_MANIFEST_SHA256 = (
    "93e133c7c777119880cbc597b15cd87998dd23cc2bb56d8be07cce20a2383a7e"
)
INVALIDATED_CLIENT_VOLUME_BUNDLE_ID = (
    "04326c3bca149835bf00f9a4f34e31eac225b4c237421bdda4034987fe8119f0"
)
INVALIDATED_CLIENT_VOLUME_MANIFEST_SHA256 = (
    "deee1481969a6ad303909ee432dff0e78350c0365cb378abedc895a295df44ad"
)
INVALIDATED_QUALIFICATION_CWD_BUNDLE_ID = (
    "de80c37c45106b5aedbcddc7d53de252e5292358002b6d31222e911e74908862"
)
INVALIDATED_QUALIFICATION_CWD_MANIFEST_SHA256 = (
    "754ceab637bd8b1500d02f410d737989ba11e7713f1f664eaa20a6437db1df82"
)
INVALIDATED_CLIENT_IDENTITY_BUNDLE_ID = (
    "7d73a1b8a9b74f1418313117b79c4d6eedfb1e7db9a4eb22876cb16a36c8b1ac"
)
INVALIDATED_CLIENT_IDENTITY_MANIFEST_SHA256 = (
    "8da3cf1513b5edd8f0ac4af79f9b20a22cda8e848bf8868c866c4660eb88f3e4"
)
INVALIDATED_IMPORT_BYTECODE_BUNDLE_ID = (
    "0ddb9a19be2e408fb215a69fa64bfb3d6864ed16de447f22d024eccc4f8a0793"
)
INVALIDATED_IMPORT_BYTECODE_MANIFEST_SHA256 = (
    "fc55d1c5c3878d082dcdbf86f8fc8de6efad03af7cab3de8737d75468cd27c37"
)
INVALIDATED_CLIENT_PERMISSION_BUNDLE_ID = (
    "87fc669e87ae4db5368e129e7cebe2b9d4fccefcbf189dae3387163bdbce84be"
)
INVALIDATED_CLIENT_PERMISSION_MANIFEST_SHA256 = (
    "0060d4cc45f8630e1bc80653adbb434de68907a0c9f3d516e4ee3baeb514335c"
)
INVALIDATED_SHARED_CONTRACT_BUNDLE_ID = (
    "2d4b793d1c68fe1634495629c5207356d96193bdc2fbba3dabe3a201dc4e4aa6"
)
INVALIDATED_SHARED_CONTRACT_MANIFEST_SHA256 = (
    "31d6ddf7e64a8ff0d456cc98dfe9f151a3bdd0580db9b8d320f37a508faacf39"
)
INVALIDATED_HOST_TMPFS_BUNDLE_ID = (
    "2943d88c35c3ceb9e110bc5df7ae3829851e24e75628e2be159ab07debf8bc59"
)
INVALIDATED_HOST_TMPFS_MANIFEST_SHA256 = (
    "5c40fa6a773475d512d9dd8b83e32b4e637a6c4b58ac7154d5b496f37a63b51e"
)
EXPECTED_TEMPLATE_REGULAR_FILE_COUNT = 173
EXPECTED_TEMPLATE_REGULAR_FILE_BYTES = 27_110_986_878
MYSQL_PORT = 65103
HOST_MEASUREMENT_TMPFS_BYTES = 2 * 1024 ** 3
HOST_MEASUREMENT_TMPFS_OPTIONS = {
    "rw", "nosuid", "nodev", "noexec",
}
HOST_MEASUREMENT_ROOT = Path("/dev/shm/ra_mysql_tpch_v2")
DB_ENVELOPE = {
    "cpu_quota_cores": 4,
    "cpu_period_us": 100000,
    "cpu_quota_us": 400000,
    "cpuset_cpus": "18-20,54-56",
    "cpuset_mems": "1",
    "memory_bytes": 5583454208,
    "memory_swap_bytes": 5583454208,
    "pids_max": 512,
    "io_read_bps": 100663296,
    "io_write_bps": 100663296,
    "innodb_buffer_pool_bytes": 4294967296,
}
CLIENT_ENVELOPE = {
    "cpu_quota_cores": 16,
    "cpu_period_us": 100000,
    "cpu_quota_us": 1600000,
    "cpuset_cpus": "0-7,36-43",
    "cpuset_mems": "0",
    "memory_bytes": 4294967296,
    "memory_swap_bytes": 4294967296,
    "pids_max": 512,
    "tmpfs_bytes": 1073741824,
    "user": "1008:1008",
    "measurement_directory_uid": 1008,
    "measurement_directory_gid": 1008,
    "measurement_directory_mode": "0700",
}
CLIENT_MEASUREMENT_SUBDIRECTORY = "client_measurement"
ATTEMPT_RE = re.compile(r"tpch-v2-[a-z0-9][a-z0-9_.-]{24,95}$")
HEX64_RE = re.compile(r"[0-9a-f]{64}$")
ROOT_LOGICAL_SOCKET_NAME = "mysql.sock"
ROOT_LOGICAL_SOCKET_TARGET = "/var/run/mysqld/mysqld.sock"
TREE_IDENTITY_NORMALIZATION = {
    "schema_version": "mysql_tpch_tree_identity_normalization_v1",
    "path_type_size_sha256_content_identity": True,
    "ownership_and_mode_excluded_from_content_identity": True,
    "excluded_root_logical_socket": {
        "path": ROOT_LOGICAL_SOCKET_NAME,
        "target": ROOT_LOGICAL_SOCKET_TARGET,
    },
    "other_transient_entries_rejected": True,
    "other_symlinks_rejected": True,
}
MYSQL_DATADIR_UID = 999
MYSQL_DATADIR_GID = 999
MYSQL_DATADIR_DIRECTORY_MODE = "0700"
MYSQL_DATADIR_FILE_MODE = "0600"
IMAGE_ID_RE = re.compile(r"sha256:[0-9a-f]{64}$")
FS_IOC_FIEMAP = 0xC020660B
FIEMAP_EXTENT_LAST = 0x00000001
FIEMAP_UNTRUSTED_FLAGS = 0x00000002 | 0x00000004 | 0x00000008 | 0x00000200
BLKIO_AUTHORITY_SCHEMA = "mysql_tpch_blkio_v1_throttle_authority_v1"
CLIENT_MYSQL_DATADIR_MASK_RELATIVE = (
    "qualification/client_volume_masks/mysql_datadir"
)
DOCKER_MANAGED_MUTABLE_BIND_TARGETS = {
    "/etc/hosts": "hosts",
    "/etc/hostname": "hostname",
    "/etc/resolv.conf": "resolv.conf",
}


class AuthorityError(RuntimeError):
    pass


class FallbackInventoryError(AuthorityError):
    """Fail-closed fallback error retaining Docker stdout for evidence."""

    def __init__(self, message: str, argv: list[str], stdout: str) -> None:
        super().__init__(message)
        self.argv = list(argv)
        self.stdout = stdout


def canonical(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def sha_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _target_permission_normalization_valid(
    value: Any, file_count: Any
) -> bool:
    """Accept only the sealed, target-only MySQL datadir normalization."""
    if not isinstance(value, dict) or not isinstance(file_count, int):
        return False
    return (
        value.get("schema_version")
        == "mysql_tpch_target_datadir_permission_normalization_v1"
        and value.get("target_only") is True
        and value.get("frozen_image_uid") == MYSQL_DATADIR_UID
        and value.get("frozen_image_gid") == MYSQL_DATADIR_GID
        and value.get("directory_mode") == MYSQL_DATADIR_DIRECTORY_MODE
        and value.get("regular_file_mode") == MYSQL_DATADIR_FILE_MODE
        and isinstance(value.get("directory_count"), int)
        and value["directory_count"] >= 1
        and value.get("regular_file_count") == file_count
        and value.get("socket_excluded_from_target") is True
        and HEX64_RE.fullmatch(
            str(value.get("source_metadata_sha256_before") or "")
        ) is not None
        and value.get("source_metadata_sha256_before")
        == value.get("source_metadata_sha256_after")
    )


def load_object(path: Path, label: str) -> dict[str, Any]:
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise AuthorityError(f"{label} must be a regular file: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AuthorityError(f"{label} is invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise AuthorityError(f"{label} must be a JSON object")
    return value


def bundle_payload_files(bundle: Path) -> dict[str, str]:
    bundle = Path(bundle)
    excluded = {"bundle_manifest.json", "bundle_manifest.json.sha256"}
    return {
        relative: sha256(path)
        for path in sorted(bundle.rglob("*"))
        if path.is_file()
        and (relative := path.relative_to(bundle).as_posix()) not in excluded
    }


def _path_is_within(target: str, root: str) -> bool:
    normalized = target.removesuffix(" (deleted)")
    if not normalized.startswith("/"):
        return False
    try:
        path = PurePosixPath(normalized)
        base = PurePosixPath(root)
    except (TypeError, ValueError):
        return False
    return path == base or base in path.parents


def _scan_proc_open_owners(
    proc_root: Path, template: str
) -> dict[str, Any]:
    proc_root = Path(proc_root)
    owners: list[dict[str, Any]] = []
    unreadable: set[int] = set()
    scanned_pids = 0
    for process in sorted(
        (path for path in proc_root.iterdir() if path.name.isdigit()),
        key=lambda path: int(path.name),
    ):
        pid = int(process.name)
        scanned_pids += 1
        links = [("cwd", process / "cwd"), ("root", process / "root")]
        fd_root = process / "fd"
        try:
            links.extend(
                (f"fd/{entry.name}", entry)
                for entry in sorted(fd_root.iterdir(), key=lambda path: path.name)
            )
        except FileNotFoundError:
            continue
        except PermissionError:
            unreadable.add(pid)
            continue
        for kind, link in links:
            try:
                target = os.readlink(link)
            except FileNotFoundError:
                continue
            except PermissionError:
                unreadable.add(pid)
                continue
            except OSError:
                continue
            if _path_is_within(target, template):
                owners.append({"pid": pid, "kind": kind, "target": target})
    return {
        "schema_version": "mysql_tpch_proc_open_owner_inventory_v1",
        # This readback is especially important when this function is invoked
        # through the readonly host-PID Docker fallback.  It proves that the
        # scanner ran as the requested host identity rather than the image's
        # default root user, which cannot traverse a root-squashed 0700 bundle.
        "scanner_identity": {
            "uid": os.getuid(),
            "gid": os.getgid(),
            "euid": os.geteuid(),
            "egid": os.getegid(),
        },
        "proc_root": str(proc_root),
        "template": template,
        "scanned_pid_count": scanned_pids,
        "open_owners": owners,
        "unreadable_pids": sorted(unreadable),
        "complete": scanned_pids > 0 and not unreadable,
        "pass": scanned_pids > 0 and not unreadable and not owners,
    }


def _template_owner_inventory(
    bundle: Path, spec: Mapping[str, Any]
) -> dict[str, Any]:
    local = _scan_proc_open_owners(Path("/proc"), TEMPLATE)
    if local["open_owners"]:
        raise AuthorityError("TPC-H template has a live /proc file owner")
    if local["complete"]:
        return {
            "method": "stdlib_proc_fd_cwd_root_inventory",
            "local": local,
            "docker_fallback_used": False,
            "complete": True,
            "pass": True,
        }
    # Verified on node04: a non-root scanner leaves hundreds of host-PID
    # entries unreadable.  This is the minimal successful capability set.
    requested_identity = {"uid": 0, "gid": 0, "euid": 0, "egid": 0}
    requested_user = "0:0"
    requested_capabilities = ["SYS_PTRACE", "DAC_READ_SEARCH"]
    fallback_argv = [
        "docker", "run", "--rm", "--network", "none", "--pid", "host",
        "--read-only", "--cap-drop", "ALL", "--cap-add", "SYS_PTRACE",
        "--cap-add", "DAC_READ_SEARCH",
        "--security-opt", "no-new-privileges:true",
        # The scanner is explicitly root, never image-default implicit root.
        # Its capability closure is locked above and anything unreadable still
        # fails closed before runtime publication or datadir mutation.
        "--user", requested_user,
        "--mount", "type=bind,src=/proc,dst=/host-proc,readonly",
        "--mount",
        (
            "type=bind,src=%s,dst=%s,readonly"
            % (PYTHON_RUNTIME_SOURCE, PYTHON_RUNTIME_SOURCE)
        ),
        "--mount",
        (
            "type=bind,src=%s,dst=%s,readonly"
            % (bundle, bundle)
        ),
        "--entrypoint", PYTHON_RUNTIME_SOURCE + "/bin/python3.11",
        spec["client_image"]["image_id"],
        str(bundle / "mysql_tpch_distributed_v2_authority.py"),
        "--internal-proc-scan", "--proc-root", "/host-proc",
        "--template", TEMPLATE,
    ]
    completed = _run(fallback_argv, timeout=300)
    try:
        fallback = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise FallbackInventoryError(
            "Docker /proc ownership inventory returned invalid JSON",
            fallback_argv,
            completed.stdout,
        ) from exc
    if (
        not isinstance(fallback, dict)
        or fallback.get("complete") is not True
        or fallback.get("pass") is not True
        or fallback.get("open_owners") != []
        or fallback.get("unreadable_pids") != []
        or fallback.get("scanner_identity") != requested_identity
    ):
        raise FallbackInventoryError(
            "Docker /proc ownership inventory is incomplete or has an owner",
            fallback_argv,
            completed.stdout,
        )
    return {
        "method":
            "stdlib_proc_inventory_with_readonly_host_pid_docker_fallback",
        "local": local,
        "docker_fallback_used": True,
        "docker_fallback_argv": fallback_argv,
        "docker_fallback_requested_identity": requested_identity,
        "docker_fallback_requested_user": requested_user,
        "docker_fallback_capabilities": requested_capabilities,
        "docker_fallback_network_mode": "none",
        "docker_fallback_read_only_rootfs": True,
        "docker_fallback_docker_socket_mount_prohibited": True,
        "docker_fallback_root_scanner_required": True,
        "docker_fallback": fallback,
        "complete": True,
        "pass": True,
    }


def verify_sidecar(path: Path, label: str) -> None:
    sidecar = Path(str(path) + ".sha256")
    expected = f"{sha256(path)}  {path.name}\n"
    if (
        sidecar.is_symlink()
        or not sidecar.is_file()
        or sidecar.read_text(encoding="ascii") != expected
    ):
        raise AuthorityError(f"{label} sidecar differs")


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path = Path(path)
    if path.exists() or Path(str(path) + ".sha256").exists():
        raise AuthorityError(f"refusing to overwrite authority: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical(value)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}")
    with temporary.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    sidecar = Path(str(path) + ".sha256")
    sidecar.write_text(
        f"{sha_bytes(payload)}  {path.name}\n", encoding="ascii"
    )


def _exact_keys(value: Any, expected: set[str], label: str) -> None:
    if not isinstance(value, dict) or set(value) != expected:
        actual = set(value) if isinstance(value, dict) else set()
        raise AuthorityError(f"{label} key set differs: {sorted(actual ^ expected)}")


def _absolute_under(raw: Any, parent: str, label: str) -> PurePosixPath:
    if not isinstance(raw, str) or not raw.startswith("/"):
        raise AuthorityError(f"{label} must be absolute")
    value = PurePosixPath(raw)
    base = PurePosixPath(parent)
    if ".." in value.parts or value == base or base not in value.parents:
        raise AuthorityError(f"{label} escapes frozen root")
    return value


def _contract_path(bundle_path: str) -> Path:
    return Path(bundle_path) / "contracts/four_scene_v2.json"


def _tpch_amendment_path(bundle_path: str) -> Path:
    return Path(bundle_path) / "contracts/tpch_client_identity_amendment_v3.json"


def validate_spec(spec: Mapping[str, Any]) -> dict[str, Any]:
    _exact_keys(
        spec,
        {
            "schema_version",
            "formal_scope",
            "cross_engine_comparison_prohibited",
            "postgresql_mutation_prohibited",
            "scenario",
            "host_id",
            "attempt_token",
            "bundle",
            "runtime",
            "contract_sha256",
            "v40_invalidation_sha256",
            "mysql_image",
            "client_image",
            "paths",
            "containers",
            "mysql_binary",
            "mysql_defaults_file",
            "collector",
            "created_at_utc",
            "expires_at_utc",
            "main_agent_token_sha256",
            "qualification_only",
            "formal_r0_authorized",
            "formal_authorized",
            "accepted_round_authorized",
        },
        "bootstrap spec",
    )
    if (
        spec["schema_version"] != "mysql_tpch_distributed_v2_bootstrap_spec_v1"
        or spec["formal_scope"] != "mysql_only"
        or spec["cross_engine_comparison_prohibited"] is not True
        or spec["postgresql_mutation_prohibited"] is not True
        or spec["scenario"] != SCENARIO
        or spec["host_id"] != HOSTNAME
        or spec["contract_sha256"] != CONTRACT_SHA256
        or spec["qualification_only"] is not True
        or spec["formal_r0_authorized"] is not False
        or spec["formal_authorized"] is not False
        or spec["accepted_round_authorized"] is not False
        or HEX64_RE.fullmatch(str(spec["v40_invalidation_sha256"])) is None
    ):
        raise AuthorityError("bootstrap scope or authority boundary differs")
    attempt = spec["attempt_token"]
    if not isinstance(attempt, str) or ATTEMPT_RE.fullmatch(attempt) is None:
        raise AuthorityError("attempt token differs")
    bundle = spec["bundle"]
    _exact_keys(bundle, {"path", "bundle_id", "manifest_sha256"}, "bundle")
    _absolute_under(bundle["path"], RUNTIME_ROOT + "/bundles", "bundle path")
    if (
        PurePosixPath(bundle["path"]).name != bundle["bundle_id"]
        or HEX64_RE.fullmatch(str(bundle["bundle_id"])) is None
        or HEX64_RE.fullmatch(str(bundle["manifest_sha256"])) is None
    ):
        raise AuthorityError("bundle identity differs")
    runtime = spec["runtime"]
    _exact_keys(
        runtime,
        {
            "source_root",
            "target_root",
            "portable_manifest_path",
            "portable_manifest_sha256",
            "tree_content_sha256",
            "publication_mode",
        },
        "runtime",
    )
    if (
        runtime["source_root"] != PYTHON_RUNTIME_SOURCE
        or runtime["target_root"] != PYTHON_RUNTIME_TARGET
        or runtime["portable_manifest_path"]
        != bundle["path"] + "/runtime/python_runtime_tree_manifest_v3.json"
        or runtime["portable_manifest_sha256"]
        != PYTHON_RUNTIME_MANIFEST_SHA256
        or runtime["tree_content_sha256"]
        != PYTHON_RUNTIME_TREE_CONTENT_SHA256
        or runtime["publication_mode"]
        != "cross_device_tar_stream_exact_manifest_atomic_rename"
    ):
        raise AuthorityError("Python runtime publication authority differs")
    for label in ("mysql_image", "client_image"):
        image = spec[label]
        _exact_keys(image, {"reference", "image_id"}, label)
        if (
            not isinstance(image["reference"], str)
            or "@sha256:" not in image["reference"]
            or IMAGE_ID_RE.fullmatch(str(image["image_id"])) is None
        ):
            raise AuthorityError(f"{label} is not digest-bound")
    paths = spec["paths"]
    _exact_keys(
        paths,
        {
            "template",
            "runtime_root",
            "attempt_root",
            "datadir",
            "credentials_dir",
            "host_measurement_tmpfs",
            "python_runtime_root",
        },
        "paths",
    )
    attempt_root = RUNTIME_ROOT + "/attempts/" + attempt
    if (
        paths["template"] != TEMPLATE
        or paths["runtime_root"] != RUNTIME_ROOT
        or paths["attempt_root"] != attempt_root
        or paths["datadir"] != attempt_root + "/datadir"
        or paths["credentials_dir"] != attempt_root + "/credentials"
        or paths["host_measurement_tmpfs"]
        != "/dev/shm/ra_mysql_tpch_v2/" + attempt
        or paths["python_runtime_root"] != PYTHON_RUNTIME_TARGET
    ):
        raise AuthorityError("bootstrap path identity differs")
    containers = spec["containers"]
    _exact_keys(containers, {"database", "client"}, "containers")
    suffix = re.sub(r"[^a-z0-9]", "_", attempt.lower())[:48]
    if containers != {
        "database": f"ra_mysql_tpch_v2_{suffix}_db",
        "client": f"ra_mysql_tpch_v2_{suffix}_client",
    }:
        raise AuthorityError("container names are not attempt-derived")
    if (
        spec["mysql_binary"] != "/usr/bin/mysql"
        or spec["mysql_defaults_file"] != attempt_root + "/credentials/mysql.cnf"
        or spec["collector"]
        != bundle["path"]
        + "/qualification/mysql_distributed_live_readback_qualification_v1.py"
        or HEX64_RE.fullmatch(str(spec["main_agent_token_sha256"])) is None
    ):
        raise AuthorityError("tool binding differs")
    try:
        created = datetime.fromisoformat(
            str(spec["created_at_utc"]).replace("Z", "+00:00")
        )
        expires = datetime.fromisoformat(
            str(spec["expires_at_utc"]).replace("Z", "+00:00")
        )
    except (TypeError, ValueError) as exc:
        raise AuthorityError("bootstrap validity window is invalid") from exc
    now = datetime.now(timezone.utc)
    if (
        created.tzinfo is None
        or expires.tzinfo is None
        or expires <= created
        or (expires - created).total_seconds() > 3600
        or now >= expires.astimezone(timezone.utc)
    ):
        raise AuthorityError("bootstrap validity window is stale or too broad")
    return dict(spec)


def command_plan(spec: Mapping[str, Any]) -> dict[str, Any]:
    spec = validate_spec(spec)
    attempt = spec["attempt_token"]
    paths = spec["paths"]
    measurement_host_path = str(
        Path(paths["host_measurement_tmpfs"])
        / CLIENT_MEASUREMENT_SUBDIRECTORY
    )
    labels = [
        "--label", "ra.owner=mysql_algorithm_c_tpch_v2",
        "--label", f"ra.scenario={SCENARIO}",
        "--label", f"ra.attempt={attempt}",
        "--label", f"ra.bundle_id={spec['bundle']['bundle_id']}",
    ]
    stage = paths["datadir"] + ".stage"
    runtime_stage = (
        spec["runtime"]["target_root"] + ".stage." + spec["attempt_token"]
    )
    runtime_copy_argv = [
        "docker", "run", "--rm", "--network", "none",
        "--cpus", "1", "--cpuset-cpus", "18", "--cpuset-mems", "1",
        "--memory", "1073741824", "--memory-swap", "1073741824",
        "--pids-limit", "128",
        "--mount",
        f"type=bind,src={spec['runtime']['source_root']},dst=/source,readonly",
        "--mount", f"type=bind,src={runtime_stage},dst=/target",
        "--entrypoint", "/bin/sh", spec["client_image"]["image_id"],
        "-ceu",
        "tar -C /source -cpf - . | tar -C /target -xpf -",
    ]
    bootstrap_name = spec["containers"]["database"] + "_bootstrap"
    copy_argv = [
        "docker", "run", "--rm", "--network", "none",
        "--cpus", "0.5", "--cpuset-cpus", "18", "--cpuset-mems", "1",
        "--memory", "536870912", "--memory-swap", "536870912",
        "--pids-limit", "128",
        "--mount", f"type=bind,src={TEMPLATE},dst=/template,readonly",
        "--mount", f"type=bind,src={stage},dst=/copy",
        "--entrypoint", "/bin/sh", spec["client_image"]["image_id"],
        "-ceu",
        (
            "metadata_digest() { "
            "(cd \"$1\"; find . -xdev -printf '%P\\0%y\\0%m\\0%u\\0%g\\0' "
            "| LC_ALL=C sort -z | sha256sum | awk '{print $1}'); "
            "}; "
            "source_before=$(metadata_digest /template); "
            "tar -C /template -cpf - . | tar -C /copy -xpf -; "
            "rm -f /copy/mysql.sock /copy/mysqld.sock /copy/mysqld.pid; "
            "test ! -e /copy/mysql.sock; "
            "test -z \"$(find /copy -xdev \\( -type s -o -type b -o -type c -o -type p -o -type l \\) -print -quit)\"; "
            "find /copy -xdev -type d -exec chown 999:999 {} + -exec chmod 0700 {} +; "
            "find /copy -xdev -type f -exec chown 999:999 {} + -exec chmod 0600 {} +; "
            "test -z \"$(find /copy -xdev -type d -printf '%U:%G:%m\\n' | awk '$0 != \"999:999:700\" {print; exit}')\"; "
            "test -z \"$(find /copy -xdev -type f -printf '%U:%G:%m\\n' | awk '$0 != \"999:999:600\" {print; exit}')\"; "
            "directory_count=$(find /copy -xdev -type d | wc -l | tr -d '[:space:]'); "
            "regular_file_count=$(find /copy -xdev -type f | wc -l | tr -d '[:space:]'); "
            "source_after=$(metadata_digest /template); "
            "test \"$source_before\" = \"$source_after\"; "
            "printf '{\\\"schema_version\\\":\\\"mysql_tpch_target_datadir_permission_normalization_v1\\\",\\\"target_only\\\":true,\\\"frozen_image_uid\\\":999,\\\"frozen_image_gid\\\":999,\\\"directory_mode\\\":\\\"0700\\\",\\\"regular_file_mode\\\":\\\"0600\\\",\\\"directory_count\\\":%s,\\\"regular_file_count\\\":%s,\\\"socket_excluded_from_target\\\":true,\\\"source_metadata_sha256_before\\\":\\\"%s\\\",\\\"source_metadata_sha256_after\\\":\\\"%s\\\"}\\n' \"$directory_count\" \"$regular_file_count\" \"$source_before\" \"$source_after\""
        ),
    ]
    bootstrap_argv = [
        "docker", "run", "-d", "--name", bootstrap_name,
        "--network", "none", "--cpus", "1", "--cpuset-cpus", "18",
        "--cpuset-mems", "1", "--memory", "2147483648",
        "--memory-swap", "2147483648", "--pids-limit", "128",
        "--mount", f"type=bind,src={paths['datadir']},dst=/var/lib/mysql",
        spec["mysql_image"]["image_id"], "mysqld",
        "--skip-grant-tables=ON", "--skip-networking=ON",
        "--innodb-buffer-pool-size=536870912",
        "--innodb-flush-method=O_DIRECT", "--skip-log-bin",
        "--max-connections=32",
    ]
    db_argv = [
        "docker", "run", "-d", "--name", spec["containers"]["database"],
        *labels,
        "--cpu-period", str(DB_ENVELOPE["cpu_period_us"]),
        "--cpu-quota", str(DB_ENVELOPE["cpu_quota_us"]),
        "--cpuset-cpus", DB_ENVELOPE["cpuset_cpus"],
        "--cpuset-mems", DB_ENVELOPE["cpuset_mems"],
        "--memory", str(DB_ENVELOPE["memory_bytes"]),
        "--memory-swap", str(DB_ENVELOPE["memory_swap_bytes"]),
        "--pids-limit", str(DB_ENVELOPE["pids_max"]),
        "--device-read-bps", f"{DEVICE}:{DB_ENVELOPE['io_read_bps']}",
        "--device-write-bps", f"{DEVICE}:{DB_ENVELOPE['io_write_bps']}",
        "--publish", f"127.0.0.1:{MYSQL_PORT}:3306",
        "--mount", f"type=bind,src={paths['datadir']},dst=/var/lib/mysql",
        "--mount",
        f"type=bind,src={paths['credentials_dir']},dst=/run/ra-credentials,readonly",
        spec["mysql_image"]["image_id"], "mysqld",
        "--bind-address=0.0.0.0",
        f"--innodb-buffer-pool-size={DB_ENVELOPE['innodb_buffer_pool_bytes']}",
        "--innodb-flush-method=O_DIRECT",
        "--innodb-flush-log-at-trx-commit=2",
        "--skip-log-bin", "--max-connections=192", "--performance-schema=ON",
    ]
    client_argv = [
        "docker", "run", "-d", "--name", spec["containers"]["client"],
        *labels,
        "--network", "host", "--read-only", "--cap-drop", "ALL",
        "--user", CLIENT_ENVELOPE["user"],
        "--security-opt", "no-new-privileges:true", "--log-driver", "none",
        "--cpu-period", str(CLIENT_ENVELOPE["cpu_period_us"]),
        "--cpu-quota", str(CLIENT_ENVELOPE["cpu_quota_us"]),
        "--cpuset-cpus", CLIENT_ENVELOPE["cpuset_cpus"],
        "--cpuset-mems", CLIENT_ENVELOPE["cpuset_mems"],
        "--memory", str(CLIENT_ENVELOPE["memory_bytes"]),
        "--memory-swap", str(CLIENT_ENVELOPE["memory_swap_bytes"]),
        "--pids-limit", str(CLIENT_ENVELOPE["pids_max"]),
        "--mount",
        (
            "type=bind,src=%s,dst=/measurement"
            % measurement_host_path
        ),
        "--mount",
        f"type=bind,src={spec['bundle']['path']},dst={spec['bundle']['path']},readonly",
        "--mount",
        (
            "type=bind,src=%s,dst=%s,readonly"
            % (paths["python_runtime_root"], paths["python_runtime_root"])
        ),
        "--mount",
        (
            "type=bind,src=%s,dst=%s,readonly"
            % (paths["credentials_dir"], paths["credentials_dir"])
        ),
        "--mount",
        (
            "type=bind,src=%s/%s,dst=/var/lib/mysql,readonly"
            % (
                spec["bundle"]["path"],
                CLIENT_MYSQL_DATADIR_MASK_RELATIVE,
            )
        ),
        "--entrypoint", "/bin/sh", spec["client_image"]["image_id"],
        "-ceu", "exec sleep 2147483647",
    ]
    collect_argv = [
        paths["python_runtime_root"] + "/bin/python3.11",
        spec["collector"], "--collect-only",
        "--bundle", spec["bundle"]["path"],
        "--container", spec["containers"]["database"],
        "--mysql-binary", spec["mysql_binary"],
        "--mysql-client-container", spec["containers"]["client"],
        "--mysql-binary-in-client", "/usr/bin/mysql",
        "--mysql-defaults-file", spec["mysql_defaults_file"],
        "--output", paths["attempt_root"] + "/live_readback.json",
    ]
    start_authority = paths["attempt_root"] + "/start_authority.json"
    blkio_authority = (
        paths["attempt_root"] + "/blkio_throttle_authority.json"
    )
    blkio_argv = [
        paths["python_runtime_root"] + "/bin/python3.11",
        spec["bundle"]["path"] + "/qualification/mysql_tpch_blkio_v1.py",
        "enforce",
        "--start-authority", start_authority,
        "--output", blkio_authority,
    ]
    return {
        "schema_version": "mysql_tpch_distributed_v2_bootstrap_plan_v1",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "attempt_token": attempt,
        "bundle_id": spec["bundle"]["bundle_id"],
        "qualification_only": True,
        "accepted_round_authorized": False,
        "runtime_publication": {
            "argv": runtime_copy_argv,
            "source": spec["runtime"]["source_root"],
            "staging_destination": runtime_stage,
            "final_destination": spec["runtime"]["target_root"],
            "portable_manifest":
                spec["runtime"]["portable_manifest_path"],
            "copy_mode": "tar_regular_file_byte_stream_no_clone_api",
            "source_and_target_different_device_required": True,
            "exact_manifest_readback_required": True,
            "atomic_rename_required": True,
        },
        "copy": {
            "argv": copy_argv,
            "source": TEMPLATE,
            "staging_destination": stage,
            "final_destination": paths["datadir"],
            "copy_mode":
                "tar_regular_file_byte_stream_target_permission_normalization_plus_fiemap_no_shared_extents",
            "atomic_rename_required": True,
            "fiemap_no_shared_extents_required": True,
            "target_permission_normalization_required": True,
            "target_only_source_metadata_stability_required": True,
            "frozen_mysql_image_identity": {
                "uid": MYSQL_DATADIR_UID,
                "gid": MYSQL_DATADIR_GID,
                "directory_mode": MYSQL_DATADIR_DIRECTORY_MODE,
                "regular_file_mode": MYSQL_DATADIR_FILE_MODE,
            },
        },
        "bootstrap_database": {"name": bootstrap_name, "argv": bootstrap_argv},
        "database": {"name": spec["containers"]["database"], "argv": db_argv},
        "client": {
            "name": spec["containers"]["client"],
            "argv": client_argv,
            "execution_user": CLIENT_ENVELOPE["user"],
            "measurement_bind": {
                "source": measurement_host_path,
                "destination": "/measurement",
                "host_tmpfs_backed": True,
                "uid": CLIENT_ENVELOPE["measurement_directory_uid"],
                "gid": CLIENT_ENVELOPE["measurement_directory_gid"],
                "mode": CLIENT_ENVELOPE["measurement_directory_mode"],
            },
        },
        "collect_only": {"argv": collect_argv},
        "start_authority": {"path": start_authority},
        "effective_blkio_enforce": {
            "argv": blkio_argv,
            "output": blkio_authority,
            "host_root_required": True,
            "docker_hostconfig_is_declaration_not_effective_authority": True,
        },
        "phase_order": [
            "start_database_and_client",
            "seal_start_authority",
            "enforce_and_read_back_effective_cgroup_v1_blkio_as_root",
            "adopt_same_live_database_and_client",
            "collect_only",
        ],
        "sql_stdout_bridge": {
            "implementation":
                "collector_managed_docker_exec_stdout_to_host_tmpfs",
            "host_output_root": paths["host_measurement_tmpfs"],
            "container_native_metric_stream": "/measurement/native.jsonl",
            "docker_log_driver_dependency": False,
        },
    }


def _run(argv: list[str], *, timeout: int = 300, check: bool = True):
    completed = subprocess.run(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=False,
    )
    if check and completed.returncode != 0:
        raise AuthorityError(
            f"command failed rc={completed.returncode}: {argv!r}; "
            f"stderr={completed.stderr[-2000:]}"
        )
    return completed


def _tmpfs_option_bytes(raw: str) -> int:
    match = re.fullmatch(r"([0-9]+)([kKmMgG]?)", raw)
    if match is None:
        raise AuthorityError("measurement tmpfs size option differs")
    multiplier = {"": 1, "k": 1024, "m": 1024 ** 2, "g": 1024 ** 3}[match.group(2).lower()]
    return int(match.group(1)) * multiplier


def _prepare_secure_measurement_bind(host_tmpfs: Path) -> dict[str, Any]:
    """Mount and verify the only writable client medium.

    The attempt path itself is an exact-capacity mount point, not merely a
    directory inherited from the shared /dev/shm mount.  The host-root
    authority creates it before any physical copy, container, or workload.
    """
    if os.geteuid() != 0 or os.getegid() != 0:
        raise AuthorityError("measurement tmpfs creation requires host root")
    expected_parent = HOST_MEASUREMENT_ROOT
    if (
        host_tmpfs.parent != expected_parent
        or ATTEMPT_RE.fullmatch(host_tmpfs.name) is None
        or host_tmpfs.is_symlink()
        or not host_tmpfs.is_dir()
        or any(host_tmpfs.iterdir())
    ):
        raise AuthorityError("measurement host mount point is absent or unsafe")
    mounted = False
    try:
        _run(
            [
                "mount", "-t", "tmpfs",
                "-o",
                (
                    f"size={HOST_MEASUREMENT_TMPFS_BYTES},"
                    f"uid={CLIENT_ENVELOPE['measurement_directory_uid']},"
                    f"gid={CLIENT_ENVELOPE['measurement_directory_gid']},"
                    "mode=0700,nosuid,nodev,noexec"
                ),
                "tmpfs", str(host_tmpfs),
            ],
            timeout=30,
        )
        mounted = True
        raw = _run(
            [
                "findmnt", "-n", "-o", "TARGET,SOURCE,FSTYPE,OPTIONS",
                "--target", str(host_tmpfs),
            ],
            timeout=30,
        ).stdout.strip().split(None, 3)
        if (
            len(raw) != 4
            or Path(raw[0]) != host_tmpfs
            or raw[1] != "tmpfs"
            or raw[2] != "tmpfs"
        ):
            raise AuthorityError("measurement path is not the exact private mount")
        options = set(raw[3].split(","))
        sizes = [
            _tmpfs_option_bytes(option.split("=", 1)[1])
            for option in options
            if option.startswith("size=")
        ]
        statvfs = os.statvfs(host_tmpfs)
        statvfs_bytes = statvfs.f_frsize * statvfs.f_blocks
        root_state = host_tmpfs.stat(follow_symlinks=False)
        if (
            not HOST_MEASUREMENT_TMPFS_OPTIONS.issubset(options)
            or len(sizes) != 1
            or sizes[0] != HOST_MEASUREMENT_TMPFS_BYTES
            or statvfs_bytes != HOST_MEASUREMENT_TMPFS_BYTES
            or root_state.st_uid
            != CLIENT_ENVELOPE["measurement_directory_uid"]
            or root_state.st_gid
            != CLIENT_ENVELOPE["measurement_directory_gid"]
            or stat.S_IMODE(root_state.st_mode)
            != int(CLIENT_ENVELOPE["measurement_directory_mode"], 8)
        ):
            raise AuthorityError("measurement private tmpfs readback differs")
        measurement = host_tmpfs / CLIENT_MEASUREMENT_SUBDIRECTORY
        measurement.mkdir(mode=0o700)
        os.chown(
            measurement,
            CLIENT_ENVELOPE["measurement_directory_uid"],
            CLIENT_ENVELOPE["measurement_directory_gid"],
        )
        os.chmod(
            measurement,
            int(CLIENT_ENVELOPE["measurement_directory_mode"], 8),
        )
        current = measurement.stat(follow_symlinks=False)
        if (
            current.st_uid != CLIENT_ENVELOPE["measurement_directory_uid"]
            or current.st_gid != CLIENT_ENVELOPE["measurement_directory_gid"]
            or stat.S_IMODE(current.st_mode)
            != int(CLIENT_ENVELOPE["measurement_directory_mode"], 8)
        ):
            raise AuthorityError("measurement bind ownership or mode differs")
        return {
            "host_tmpfs_root": str(host_tmpfs),
            "host_source": str(measurement),
            "destination": "/measurement",
            "filesystem": "tmpfs",
            "mount_source": "tmpfs",
            "mount_target_exact": True,
            "mount_options_required": sorted(HOST_MEASUREMENT_TMPFS_OPTIONS),
            "size_bytes": sizes[0],
            "statvfs_capacity_bytes": statvfs_bytes,
            "uid": current.st_uid,
            "gid": current.st_gid,
            "mode": format(stat.S_IMODE(current.st_mode), "04o"),
            "container_user": CLIENT_ENVELOPE["user"],
        }
    except BaseException:
        if mounted:
            cleanup = _run(
                ["umount", str(host_tmpfs)], timeout=30, check=False
            )
            if cleanup.returncode != 0:
                raise AuthorityError(
                    "measurement tmpfs verification and cleanup both failed: "
                    + cleanup.stderr[-1000:]
                )
        raise


def _runtime_readback(
    interpreter: Path, bundle: Path, runtime_root: Path
) -> dict[str, Any]:
    helper = bundle / "qualification/runtime_tree_content_identity_v3.py"
    manifest = bundle / "runtime/python_runtime_tree_manifest_v3.json"
    if (
        interpreter.is_symlink()
        or not interpreter.is_file()
        or helper.is_symlink()
        or not helper.is_file()
        or manifest.is_symlink()
        or not manifest.is_file()
        or sha256(manifest) != PYTHON_RUNTIME_MANIFEST_SHA256
    ):
        raise AuthorityError("Python runtime readback closure differs")
    completed = _run(
        [
            str(interpreter),
            str(helper),
            "--authority-manifest", str(manifest),
            "--runtime-root", str(runtime_root),
        ],
        timeout=7200,
    )
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise AuthorityError("Python runtime readback returned invalid JSON") from exc
    if (
        not isinstance(result, dict)
        or result.get("status")
        != "PASS_EXACT_PATH_INDEPENDENT_TREE_READBACK"
        or result.get("tree_content_sha256")
        != PYTHON_RUNTIME_TREE_CONTENT_SHA256
        or result.get("exact_entry_set_verified") is not True
        or result.get("directory_path_and_mode_verified") is not True
        or result.get("file_sha256_size_mode_verified") is not True
        or result.get("symlink_target_verified") is not True
        or result.get("special_entries_rejected") is not True
    ):
        raise AuthorityError("Python runtime exact tree readback differs")
    return result


def _publish_runtime(
    spec: Mapping[str, Any], plan: Mapping[str, Any], bundle: Path
) -> dict[str, Any]:
    publication = plan["runtime_publication"]
    source = Path(publication["source"])
    target = Path(publication["final_destination"])
    stage = Path(publication["staging_destination"])
    if source.is_symlink() or not source.is_dir():
        raise AuthorityError("Python runtime source is absent or symlinked")
    source_interpreter = source / "bin/python3.11"
    source_readback = _runtime_readback(source_interpreter, bundle, source)
    target.parent.mkdir(parents=True, exist_ok=True)
    source_device = os.stat(source).st_dev
    target_device = os.stat(target.parent).st_dev
    if (
        source_device == target_device
        or f"{os.major(target_device)}:{os.minor(target_device)}" != MAJOR_MINOR
    ):
        raise AuthorityError("runtime source/target device separation differs")
    if stage.exists() or stage.is_symlink():
        raise AuthorityError("runtime staging path already exists")
    if target.is_symlink() or (target.exists() and not target.is_dir()):
        raise AuthorityError("runtime target has an unsafe existing entry")
    if target.exists() and any(target.iterdir()):
        target_readback = _runtime_readback(
            target / "bin/python3.11", bundle, target
        )
        return {
            "publication_mode": publication["copy_mode"],
            "source_root": str(source),
            "target_root": str(target),
            "source_device_major_minor":
                f"{os.major(source_device)}:{os.minor(source_device)}",
            "target_device_major_minor":
                f"{os.major(target_device)}:{os.minor(target_device)}",
            "source_and_target_different_device": True,
            "portable_manifest_sha256": PYTHON_RUNTIME_MANIFEST_SHA256,
            "tree_content_sha256": PYTHON_RUNTIME_TREE_CONTENT_SHA256,
            "source_readback_sha256": sha_bytes(canonical(source_readback)),
            "stage_readback_sha256": None,
            "target_readback_sha256": sha_bytes(canonical(target_readback)),
            "target_readback": target_readback,
            "atomic_rename_complete": True,
            "preexisting_content_addressed_target_reused": True,
            "reflink_clone_api_used": False,
            "accepted_round_authorized": False,
        }
    if target.exists():
        try:
            target.rmdir()
        except OSError as exc:
            raise AuthorityError("runtime target is not exactly empty") from exc
    stage.mkdir(mode=0o700)
    _run(publication["argv"], timeout=7200)
    stage_readback = _runtime_readback(source_interpreter, bundle, stage)
    os.rename(stage, target)
    target_interpreter = target / "bin/python3.11"
    target_readback = _runtime_readback(target_interpreter, bundle, target)
    if (
        source_readback["tree_content_sha256"]
        != stage_readback["tree_content_sha256"]
        or source_readback["tree_content_sha256"]
        != target_readback["tree_content_sha256"]
    ):
        raise AuthorityError("runtime source/stage/target identity differs")
    return {
        "publication_mode": publication["copy_mode"],
        "source_root": str(source),
        "target_root": str(target),
        "source_device_major_minor":
            f"{os.major(source_device)}:{os.minor(source_device)}",
        "target_device_major_minor":
            f"{os.major(target_device)}:{os.minor(target_device)}",
        "source_and_target_different_device": True,
        "portable_manifest_sha256": PYTHON_RUNTIME_MANIFEST_SHA256,
        "tree_content_sha256": PYTHON_RUNTIME_TREE_CONTENT_SHA256,
        "source_readback_sha256": sha_bytes(canonical(source_readback)),
        "stage_readback_sha256": sha_bytes(canonical(stage_readback)),
        "target_readback_sha256": sha_bytes(canonical(target_readback)),
        "target_readback": target_readback,
        "atomic_rename_complete": True,
        "preexisting_content_addressed_target_reused": False,
        "reflink_clone_api_used": False,
        "accepted_round_authorized": False,
    }


def _file_extents(path: Path) -> list[tuple[int, int]]:
    extent_count = 256
    start = 0
    result: list[tuple[int, int]] = []
    with path.open("rb", buffering=0) as stream:
        while True:
            request = bytearray(32 + 56 * extent_count)
            struct.pack_into(
                "=QQIIII",
                request,
                0,
                start,
                0xFFFFFFFFFFFFFFFF,
                0,
                0,
                extent_count,
                0,
            )
            try:
                fcntl.ioctl(stream.fileno(), FS_IOC_FIEMAP, request, True)
            except OSError as exc:
                raise AuthorityError(f"FIEMAP failed for {path}: {exc}") from exc
            _, _, _, mapped, _, _ = struct.unpack_from("=QQIIII", request, 0)
            if mapped == 0:
                break
            last = False
            for index in range(mapped):
                logical, physical, length, _, _, flags, _, _, _ = (
                    struct.unpack_from(
                        "=QQQQQIIII", request, 32 + 56 * index
                    )
                )
                if flags & FIEMAP_UNTRUSTED_FLAGS:
                    raise AuthorityError(
                        f"FIEMAP returned untrusted flags for {path}"
                    )
                if physical == 0 or length <= 0:
                    raise AuthorityError(
                        f"FIEMAP returned invalid physical extent for {path}"
                    )
                result.append((physical, physical + length))
                start = logical + length
                last = bool(flags & FIEMAP_EXTENT_LAST)
            if last:
                break
    return result


def _physical_extent_audit(source: Path, destination: Path) -> dict[str, Any]:
    source_device = os.stat(source).st_dev
    destination_device = os.stat(destination).st_dev
    source_extents: list[tuple[int, int]] = []
    source_files = 0
    destination_files = 0
    for path in sorted(source.rglob("*")):
        if path.is_file() and not path.is_symlink():
            source_files += 1
            source_extents.extend(_file_extents(path))
    merged: list[list[int]] = []
    for begin, end in sorted(source_extents):
        if merged and begin <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([begin, end])
    starts = [row[0] for row in merged]
    overlap_bytes = 0
    destination_extent_count = 0
    for path in sorted(destination.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        destination_files += 1
        for begin, end in _file_extents(path):
            destination_extent_count += 1
            if source_device != destination_device:
                continue
            index = bisect.bisect_right(starts, begin) - 1
            candidates = []
            if index >= 0:
                candidates.append(merged[index])
            if index + 1 < len(merged):
                candidates.append(merged[index + 1])
            for source_begin, source_end in candidates:
                overlap_bytes += max(
                    0, min(end, source_end) - max(begin, source_begin)
                )
    if source_files != destination_files or overlap_bytes != 0:
        raise AuthorityError("physical extent non-alias audit failed")
    return {
        "method": "linux_fiemap_physical_extent_interval_intersection_v1",
        "source_file_count": source_files,
        "destination_file_count": destination_files,
        "source_extent_count": len(source_extents),
        "destination_extent_count": destination_extent_count,
        "source_and_destination_same_device":
            source_device == destination_device,
        "shared_physical_extent_bytes": overlap_bytes,
        "shared_physical_extents": False,
        "audit_complete": True,
    }


def _root_extent_audit(
    spec: Mapping[str, Any], bundle: Path, source: Path, destination: Path
) -> dict[str, Any]:
    """Run the sealed FIEMAP routine as a no-network root reader only."""
    runtime = spec["runtime"]["target_root"] + "/bin/python3.11"
    helper = str(bundle / "mysql_tpch_distributed_v2_authority.py")
    argv = [
        "docker", "run", "--rm", "--network", "none", "--read-only",
        "--user", "0:0", "--cap-drop", "ALL", "--cap-add", "DAC_READ_SEARCH",
        "--mount", f"type=bind,src={source},dst=/source,readonly",
        "--mount", f"type=bind,src={destination},dst=/destination,readonly",
        "--mount", f"type=bind,src={bundle},dst=/bundle,readonly",
        "--mount", f"type=bind,src={spec['runtime']['target_root']},dst=/runtime,readonly",
        "--entrypoint", "/runtime/bin/python3.11", spec["client_image"]["image_id"],
        "/bundle/mysql_tpch_distributed_v2_authority.py", "--internal-fiemap-audit",
        "--source", "/source", "--destination", "/destination",
    ]
    completed = _run(argv, timeout=7200)
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise AuthorityError("root FIEMAP audit returned invalid JSON") from exc
    if (
        not isinstance(result, dict)
        or result.get("audit_complete") is not True
        or result.get("shared_physical_extent_bytes") != 0
        or result.get("source_file_count") != result.get("destination_file_count")
    ):
        raise AuthorityError("root FIEMAP audit is incomplete")
    result["execution"] = {
        "kind": "sealed_no_network_root_container_fiemap_v1",
        "user": "0:0", "cap_drop": ["ALL"],
        "cap_add": ["DAC_READ_SEARCH"], "network": "none",
        "read_only_rootfs": True, "docker_socket_mounted": False,
    }
    return result


def _inspect(name: str) -> dict[str, Any]:
    completed = _run(["docker", "inspect", name], timeout=30)
    try:
        rows = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise AuthorityError("docker inspect returned invalid JSON") from exc
    if not isinstance(rows, list) or len(rows) != 1:
        raise AuthorityError("docker inspect cardinality differs")
    return rows[0]


def _inspect_identity(row: Mapping[str, Any]) -> str:
    return sha_bytes(
        canonical(
            {
                "Config": row.get("Config"),
                "HostConfig": row.get("HostConfig"),
                "Mounts": row.get("Mounts"),
                "Image": row.get("Image"),
                "Created": row.get("Created"),
            }
        )
    )


def _client_container_immutable_projection(
    row: Mapping[str, Any],
) -> dict[str, Any]:
    try:
        return client_container_immutable_projection(row)
    except ValueError as exc:
        raise AuthorityError(str(exc)) from exc


def _client_container_config_identity(row: Mapping[str, Any]) -> str:
    return sha_bytes(canonical(_client_container_immutable_projection(row)))


def _pid_starttime(pid: int) -> int:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    except OSError as exc:
        raise AuthorityError(f"cannot read PID {pid} starttime") from exc
    close = raw.rfind(")")
    pieces = raw[close + 2 :].split()
    if close < 0 or len(pieces) < 20:
        raise AuthorityError("process stat is malformed")
    try:
        value = int(pieces[19])
    except ValueError as exc:
        raise AuthorityError("process starttime is invalid") from exc
    if value <= 0:
        raise AuthorityError("process starttime must be positive")
    return value


def _expand_cpuset(value: Any) -> set[int]:
    result: set[int] = set()
    for token in str(value).split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            low, high = (int(item) for item in token.split("-", 1))
            result.update(range(low, high + 1))
        else:
            result.add(int(token))
    return result


def _rate(rows: Any, device: str) -> int | None:
    for row in rows or []:
        if isinstance(row, dict) and row.get("Path") == device:
            return int(row.get("Rate") or 0)
    return None


def _verify_effective_blkio_authority(
    spec: Mapping[str, Any],
    database: Mapping[str, Any],
    embedded: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    path = Path(spec["paths"]["attempt_root"]) / "blkio_throttle_authority.json"
    if embedded is None:
        value = load_object(path, "effective blkio authority")
        verify_sidecar(path, "effective blkio authority")
    elif isinstance(embedded, dict):
        value = dict(embedded)
    else:
        raise AuthorityError("embedded effective blkio authority differs")
    unsigned = dict(value)
    recorded_sha = unsigned.pop("authority_sha256", None)
    expected_cgroup = (
        "/sys/fs/cgroup/blkio/docker/" + str(database["container_id"])
    )
    if (
        recorded_sha != sha_bytes(canonical(unsigned))
        or value.get("schema_version") != BLKIO_AUTHORITY_SCHEMA
        or value.get("status") != "PASS_EFFECTIVE_CGROUP_V1_THROTTLE"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("postgresql_mutation_prohibited") is not True
        or value.get("bundle_id") != spec["bundle"]["bundle_id"]
        or value.get("attempt_token") != spec["attempt_token"]
        or value.get("database_container") != spec["containers"]["database"]
        or value.get("database_container_id") != database.get("container_id")
        or value.get("database_pid") != database.get("pid")
        or value.get("host_effective_uid_gid") != [0, 0]
        or value.get("cgroup_version") != "v1"
        or value.get("cgroup_path") != expected_cgroup
        or value.get("device_major_minor") != MAJOR_MINOR
        or value.get("read_bps") != DB_ENVELOPE["io_read_bps"]
        or value.get("write_bps") != DB_ENVELOPE["io_write_bps"]
        or value.get("read_throttle_file")
        != expected_cgroup + "/blkio.throttle.read_bps_device"
        or value.get("write_throttle_file")
        != expected_cgroup + "/blkio.throttle.write_bps_device"
        or value.get(
            "docker_hostconfig_is_declaration_not_effective_authority"
        )
        is not True
        or value.get("database_or_client_started") is not True
        or value.get("workload_started") is not False
        or value.get("formal_launch_authorized") is not False
        or value.get("accepted_round_authorized") is not False
    ):
        raise AuthorityError("effective cgroup-v1 blkio authority differs")
    return value


def _secure_measurement_bind_exact(
    mount: Mapping[str, Any], spec: Mapping[str, Any]
) -> bool:
    expected_source = str(
        Path(spec["paths"]["host_measurement_tmpfs"])
        / CLIENT_MEASUREMENT_SUBDIRECTORY
    )
    return (
        mount.get("Type") == "bind"
        and mount.get("Source") == expected_source
        and mount.get("Destination") == "/measurement"
        and mount.get("RW") is True
    )


def _docker_managed_mutable_bind_exact(
    mount: Mapping[str, Any], container_id: str
) -> bool:
    if (
        mount.get("Type") != "bind"
        or mount.get("RW") is not True
        or HEX64_RE.fullmatch(str(container_id or "")) is None
    ):
        return False
    destination = mount.get("Destination")
    expected_name = DOCKER_MANAGED_MUTABLE_BIND_TARGETS.get(destination)
    source = mount.get("Source")
    if expected_name is None or not isinstance(source, str):
        return False
    source_path = PurePosixPath(source)
    return (
        source_path.name == expected_name
        and source_path.parent.name == container_id
        and "containers" in source_path.parts
    )


def _verify_container_rows(
    spec: Mapping[str, Any],
    database: Mapping[str, Any],
    client: Mapping[str, Any],
    effective_blkio: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    db_row = database.get("inspect")
    client_row = client.get("inspect")
    if not isinstance(db_row, dict) or not isinstance(client_row, dict):
        raise AuthorityError("container inspect evidence is absent")
    db_host = db_row.get("HostConfig") or {}
    db_state = db_row.get("State") or {}
    db_pid = int(database.get("pid") or 0)
    ports = ((db_host.get("PortBindings") or {}).get("3306/tcp") or [])
    declared_read = _rate(db_host.get("BlkioDeviceReadBps"), DEVICE)
    declared_write = _rate(db_host.get("BlkioDeviceWriteBps"), DEVICE)
    if (
        db_row.get("Id") != database.get("container_id")
        or db_row.get("Image") != spec["mysql_image"]["image_id"]
        or db_state.get("Running") is not True
        or int(db_state.get("Pid") or 0) != db_pid
        or db_pid <= 0
        or int(database.get("starttime_ticks") or 0) <= 0
        or int(db_row.get("RestartCount") or 0) != 0
        or int(db_host.get("CpuPeriod") or 0) != DB_ENVELOPE["cpu_period_us"]
        or int(db_host.get("CpuQuota") or 0) != DB_ENVELOPE["cpu_quota_us"]
        or _expand_cpuset(db_host.get("CpusetCpus"))
        != _expand_cpuset(DB_ENVELOPE["cpuset_cpus"])
        or _expand_cpuset(db_host.get("CpusetMems"))
        != _expand_cpuset(DB_ENVELOPE["cpuset_mems"])
        or int(db_host.get("Memory") or 0) != DB_ENVELOPE["memory_bytes"]
        or int(db_host.get("MemorySwap") or 0)
        != DB_ENVELOPE["memory_swap_bytes"]
        or int(db_host.get("PidsLimit") or 0) != DB_ENVELOPE["pids_max"]
        or declared_read not in (None, DB_ENVELOPE["io_read_bps"])
        or declared_write not in (None, DB_ENVELOPE["io_write_bps"])
        or {
            (row.get("HostIp"), int(row.get("HostPort") or 0))
            for row in ports
        }
        != {("127.0.0.1", MYSQL_PORT)}
        or not any(
            row.get("Source") == spec["paths"]["datadir"]
            and row.get("Destination") == "/var/lib/mysql"
            and row.get("RW") is True
            for row in db_row.get("Mounts") or []
        )
    ):
        raise AuthorityError("database container exact envelope differs")
    blkio = _verify_effective_blkio_authority(
        spec, database, effective_blkio
    )
    cl_host = client_row.get("HostConfig") or {}
    cl_state = client_row.get("State") or {}
    cl_pid = int(client.get("pid") or 0)
    mask_source = (
        spec["bundle"]["path"] + "/" + CLIENT_MYSQL_DATADIR_MASK_RELATIVE
    )
    expected_mounts = {
        ("bind", spec["bundle"]["path"], spec["bundle"]["path"], False),
        (
            "bind",
            spec["paths"]["python_runtime_root"],
            spec["paths"]["python_runtime_root"],
            False,
        ),
        (
            "bind",
            spec["paths"]["credentials_dir"],
            spec["paths"]["credentials_dir"],
            False,
        ),
        ("bind", mask_source, "/var/lib/mysql", False),
        (
            "bind",
            str(
                Path(spec["paths"]["host_measurement_tmpfs"])
                / CLIENT_MEASUREMENT_SUBDIRECTORY
            ),
            "/measurement",
            True,
        ),
    }
    raw_mounts = client_row.get("Mounts") or []
    application_mounts = {
        (
            row.get("Type"),
            row.get("Source"),
            row.get("Destination"),
            row.get("RW"),
        )
        for row in raw_mounts
        if row.get("Destination")
        not in DOCKER_MANAGED_MUTABLE_BIND_TARGETS
    }
    if (
        client_row.get("Id") != client.get("container_id")
        or client_row.get("Image") != spec["client_image"]["image_id"]
        or cl_state.get("Running") is not True
        or int(cl_state.get("Pid") or 0) != cl_pid
        or cl_pid <= 0
        or int(client.get("starttime_ticks") or 0) <= 0
        or int(client_row.get("RestartCount") or 0) != 0
        or int(cl_host.get("CpuPeriod") or 0) != CLIENT_ENVELOPE["cpu_period_us"]
        or int(cl_host.get("CpuQuota") or 0) != CLIENT_ENVELOPE["cpu_quota_us"]
        or _expand_cpuset(cl_host.get("CpusetCpus"))
        != _expand_cpuset(CLIENT_ENVELOPE["cpuset_cpus"])
        or _expand_cpuset(cl_host.get("CpusetMems"))
        != _expand_cpuset(CLIENT_ENVELOPE["cpuset_mems"])
        or int(cl_host.get("Memory") or 0) != CLIENT_ENVELOPE["memory_bytes"]
        or int(cl_host.get("MemorySwap") or 0)
        != CLIENT_ENVELOPE["memory_swap_bytes"]
        or int(cl_host.get("PidsLimit") or 0) != CLIENT_ENVELOPE["pids_max"]
        or cl_host.get("NetworkMode") != "host"
        or cl_host.get("ReadonlyRootfs") is not True
        or (cl_host.get("LogConfig") or {}).get("Type") != "none"
        or (client_row.get("Config") or {}).get("User")
        != CLIENT_ENVELOPE["user"]
        or "ALL" not in (cl_host.get("CapDrop") or [])
        or not any(
            str(value).replace("=", ":").lower()
            in ("no-new-privileges", "no-new-privileges:true")
            for value in (cl_host.get("SecurityOpt") or [])
        )
        or cl_host.get("Tmpfs") not in ({}, None)
        or application_mounts != expected_mounts
        or any(row.get("Type") == "volume" for row in raw_mounts)
        or any(
            row.get("RW") is True
            and not _docker_managed_mutable_bind_exact(
                row, str(client_row.get("Id") or "")
            )
            and not _secure_measurement_bind_exact(row, spec)
            for row in raw_mounts
        )
        or any(
            row.get("Source") == spec["paths"]["datadir"]
            for row in raw_mounts
        )
    ):
        raise AuthorityError("client container exact envelope differs")
    return {
        "database_container_id": database["container_id"],
        "database_pid": db_pid,
        "database_starttime_ticks": int(database["starttime_ticks"]),
        "database_inspect_config_sha256": _inspect_identity(db_row),
        "database_datadir": spec["paths"]["datadir"],
        "client_container_id": client["container_id"],
        "client_pid": cl_pid,
        "client_starttime_ticks": int(client["starttime_ticks"]),
        "client_inspect_config_sha256":
            _client_container_config_identity(client_row),
        "client_created": client_row.get("Created"),
        "client_image_id": client_row.get("Image"),
        "effective_blkio_authority_sha256": blkio["authority_sha256"],
    }


def validate_bootstrap_evidence(
    evidence: Mapping[str, Any], spec: Mapping[str, Any]
) -> dict[str, Any]:
    spec = validate_spec(spec)
    if (
        evidence.get("schema_version")
        != "mysql_tpch_distributed_v2_bootstrap_evidence_v1"
        or evidence.get("status")
        != "PASS_BOOTSTRAP_LIVE_NOT_R0_OR_FORMAL_AUTHORITY"
        or evidence.get("formal_scope") != "mysql_only"
        or evidence.get("cross_engine_comparison_prohibited") is not True
        or evidence.get("postgresql_mutation_prohibited") is not True
        or evidence.get("scenario") != SCENARIO
        or evidence.get("host_id") != HOSTNAME
        or evidence.get("attempt_token") != spec["attempt_token"]
        or evidence.get("bundle_id") != spec["bundle"]["bundle_id"]
        or evidence.get("contract_sha256") != CONTRACT_SHA256
        or evidence.get("v40_invalidation_sha256")
        != spec["v40_invalidation_sha256"]
        or evidence.get("spec_sha256") != sha_bytes(canonical(spec))
        or evidence.get("qualification_only") is not True
        or evidence.get("formal_r0_authorized") is not False
        or evidence.get("formal_authorized") is not False
        or evidence.get("accepted_round_authorized") is not False
    ):
        raise AuthorityError("bootstrap evidence identity/boundary differs")
    copy = evidence.get("copy") or {}
    ownership = evidence.get("template_owner_inventory") or {}
    extent_audit = copy.get("extent_audit") or {}
    if (
        copy.get("command") != "tar regular-file byte stream"
        or copy.get("overlay") is not False
        or copy.get("reflink") is not False
        or copy.get("clone_api_used") is not False
        or copy.get("full_physical_copy") is not True
        or copy.get("atomic_rename_complete") is not True
        or copy.get("source") != TEMPLATE
        or copy.get("destination") != spec["paths"]["datadir"]
        or copy.get("device_major_minor") != MAJOR_MINOR
        or copy.get("tree_identity_normalization")
        != TREE_IDENTITY_NORMALIZATION
        or copy.get("source_tree_sha256")
        != copy.get("destination_tree_sha256")
        or copy.get("file_count") != EXPECTED_TEMPLATE_REGULAR_FILE_COUNT
        or copy.get("byte_count") != EXPECTED_TEMPLATE_REGULAR_FILE_BYTES
        or copy.get("source_root_inode") == copy.get("destination_root_inode")
        or not _target_permission_normalization_valid(
            copy.get("target_permission_normalization"),
            copy.get("file_count"),
        )
        or extent_audit.get("method")
        != "linux_fiemap_physical_extent_interval_intersection_v1"
        or extent_audit.get("source_and_destination_same_device") is not True
        or extent_audit.get("shared_physical_extent_bytes") != 0
        or extent_audit.get("shared_physical_extents") is not False
        or extent_audit.get("audit_complete") is not True
    ):
        raise AuthorityError("full physical no-reflink copy evidence differs")
    if (
        ownership.get("method")
        not in {
            "stdlib_proc_fd_cwd_root_inventory",
            "stdlib_proc_inventory_with_readonly_host_pid_docker_fallback",
        }
        or ownership.get("complete") is not True
        or ownership.get("pass") is not True
        or (ownership.get("local") or {}).get("open_owners") != []
        or (
            ownership.get("docker_fallback_used") is True
            and (
                (ownership.get("docker_fallback") or {}).get("open_owners")
                != []
                or (ownership.get("docker_fallback") or {}).get(
                    "unreadable_pids"
                )
                != []
                or (ownership.get("docker_fallback") or {}).get(
                    "scanner_identity"
                )
                != ownership.get("docker_fallback_requested_identity")
                or ownership.get("docker_fallback_requested_identity")
                != {"uid": 0, "gid": 0, "euid": 0, "egid": 0}
                or ownership.get("docker_fallback_requested_user")
                != "0:0"
                or ownership.get("docker_fallback_capabilities")
                != ["SYS_PTRACE", "DAC_READ_SEARCH"]
                or ownership.get("docker_fallback_network_mode") != "none"
                or ownership.get("docker_fallback_read_only_rootfs") is not True
                or ownership.get("docker_fallback_docker_socket_mount_prohibited")
                is not True
                or ownership.get("docker_fallback_root_scanner_required")
                is not True
            )
        )
    ):
        raise AuthorityError("template /proc ownership inventory differs")
    runtime = evidence.get("runtime_publication") or {}
    target_readback = runtime.get("target_readback") or {}
    if (
        runtime.get("publication_mode")
        != "tar_regular_file_byte_stream_no_clone_api"
        or runtime.get("source_root") != PYTHON_RUNTIME_SOURCE
        or runtime.get("target_root") != PYTHON_RUNTIME_TARGET
        or runtime.get("source_and_target_different_device") is not True
        or runtime.get("target_device_major_minor") != MAJOR_MINOR
        or runtime.get("portable_manifest_sha256")
        != PYTHON_RUNTIME_MANIFEST_SHA256
        or runtime.get("tree_content_sha256")
        != PYTHON_RUNTIME_TREE_CONTENT_SHA256
        or runtime.get("target_readback_sha256")
        != sha_bytes(canonical(target_readback))
        or target_readback.get("status")
        != "PASS_EXACT_PATH_INDEPENDENT_TREE_READBACK"
        or target_readback.get("root") != PYTHON_RUNTIME_TARGET
        or target_readback.get("tree_content_sha256")
        != PYTHON_RUNTIME_TREE_CONTENT_SHA256
        or runtime.get("atomic_rename_complete") is not True
        or runtime.get("reflink_clone_api_used") is not False
        or runtime.get("accepted_round_authorized") is not False
    ):
        raise AuthorityError("Python runtime publish/readback evidence differs")
    measurement_bind = evidence.get("measurement_bind") or {}
    expected_measurement_source = str(
        Path(spec["paths"]["host_measurement_tmpfs"])
        / CLIENT_MEASUREMENT_SUBDIRECTORY
    )
    if (
        set(measurement_bind)
        != {
            "host_tmpfs_root", "host_source", "destination", "filesystem",
            "mount_source", "mount_target_exact", "mount_options_required",
            "size_bytes", "statvfs_capacity_bytes", "uid", "gid", "mode",
            "container_user",
        }
        or measurement_bind.get("host_tmpfs_root")
        != spec["paths"]["host_measurement_tmpfs"]
        or measurement_bind.get("host_source") != expected_measurement_source
        or measurement_bind.get("destination") != "/measurement"
        or measurement_bind.get("filesystem") != "tmpfs"
        or int(measurement_bind.get("size_bytes") or 0)
        != HOST_MEASUREMENT_TMPFS_BYTES
        or int(measurement_bind.get("statvfs_capacity_bytes") or 0)
        != HOST_MEASUREMENT_TMPFS_BYTES
        or measurement_bind.get("mount_source") != "tmpfs"
        or measurement_bind.get("mount_target_exact") is not True
        or measurement_bind.get("mount_options_required")
        != sorted(HOST_MEASUREMENT_TMPFS_OPTIONS)
        or measurement_bind.get("uid")
        != CLIENT_ENVELOPE["measurement_directory_uid"]
        or measurement_bind.get("gid")
        != CLIENT_ENVELOPE["measurement_directory_gid"]
        or measurement_bind.get("mode")
        != CLIENT_ENVELOPE["measurement_directory_mode"]
        or measurement_bind.get("container_user") != CLIENT_ENVELOPE["user"]
    ):
        raise AuthorityError("secure measurement bind evidence differs")
    identity = _verify_container_rows(
        spec,
        evidence.get("database") or {},
        evidence.get("client") or {},
        evidence.get("effective_blkio_authority"),
    )
    identity.update(
        {
            "python_runtime_root": PYTHON_RUNTIME_TARGET,
            "python_runtime_tree_content_sha256":
                PYTHON_RUNTIME_TREE_CONTENT_SHA256,
            "python_runtime_readback_sha256":
                runtime["target_readback_sha256"],
        }
    )
    live = (evidence.get("collect_only") or {}).get("document")
    if (
        not isinstance(live, dict)
        or (evidence.get("collect_only") or {}).get("sha256")
        != sha_bytes(canonical(live))
        or live.get("status") != "PASS_LIVE_READBACK_NOT_LAUNCH_AUTHORITY"
        or live.get("bundle_id") != spec["bundle"]["bundle_id"]
        or live.get("scenario") != SCENARIO
        or (live.get("container") or {}).get("container_id")
        != identity["database_container_id"]
        or int((live.get("container") or {}).get("pid") or 0)
        != identity["database_pid"]
        or live.get("accepted_round_authorized") is not False
    ):
        raise AuthorityError("collect-only evidence differs")
    return identity


def planner_identity(
    evidence: Mapping[str, Any], spec: Mapping[str, Any]
) -> dict[str, Any]:
    return validate_bootstrap_evidence(evidence, spec)


def _tree_digest(
    image: str, path: str, source_tree: bool
) -> dict[str, Any]:
    """Hash the complete copy domain with a sealed privileged read-only scanner."""
    if source_tree:
        socket_checks = (
            "test -L {name}; "
            "test \"$(readlink {name})\" = \"{target}\"; "
            "transient=$(find . -xdev \\( -name mysql.sock -o -name mysqld.sock -o -name mysqld.pid \\) ! -path ./{name} -print -quit); "
            "test -z \"$transient\"; "
            "other_link=$(find . -xdev -type l ! -path ./{name} -print -quit); "
            "test -z \"$other_link\"; "
        ).format(
            name=ROOT_LOGICAL_SOCKET_NAME,
            target=ROOT_LOGICAL_SOCKET_TARGET,
        )
    else:
        socket_checks = (
            "test ! -e {name}; test ! -L {name}; "
            "transient=$(find . -xdev \\( -name mysql.sock -o -name mysqld.sock -o -name mysqld.pid \\) -print -quit); "
            "test -z \"$transient\"; "
            "other_link=$(find . -xdev -type l -print -quit); "
            "test -z \"$other_link\"; "
        ).format(name=ROOT_LOGICAL_SOCKET_NAME)
    script = (
        "set -o pipefail; cd /tree; "
        "test \"$(id -u):$(id -g):$(id -ru):$(id -rg)\" = 0:0:0:0; "
        "special=$(find . -xdev \\( -type s -o -type b -o -type c -o -type p \\) -print -quit); "
        "test -z \"$special\"; "
        + socket_checks
        + "count=$(find . -xdev -type f | wc -l); "
        + "bytes=$(find . -xdev -type f -printf '%s\\n' | awk '{s+=$1} END {print s+0}'); "
        + "digest=$({ printf '%s\\0%s\\0%s\\0%s\\0' . d 0 -; "
        + "find . -xdev -mindepth 1 ! -path ./%s -print0 | sort -z | "
        % ROOT_LOGICAL_SOCKET_NAME
        + "while IFS= read -r -d '' entry; do "
        + "rel=${entry#./}; "
        + "if test -d \"$entry\"; then "
        + "printf '%s\\0%s\\0%s\\0%s\\0' \"$rel\" d 0 -; "
        + "elif test -f \"$entry\"; then "
        + "size=$(stat -c %s -- \"$entry\"); "
        + "content=$(sha256sum -- \"$entry\" | awk '{print $1}'); "
        + "test -n \"$content\"; "
        + "printf '%s\\0f\\0%s\\0%s\\0' \"$rel\" \"$size\" \"$content\"; "
        + "else exit 71; fi; "
        + "done; } | sha256sum | awk '{print $1}'); "
        + "test \"$count\" = %d; test \"$bytes\" = %d; "
        % (
            EXPECTED_TEMPLATE_REGULAR_FILE_COUNT,
            EXPECTED_TEMPLATE_REGULAR_FILE_BYTES,
        )
        + "printf '%s %s %s\\n' \"$digest\" \"$count\" \"$bytes\""
    )
    completed = _run(
        [
            "docker", "run", "--rm", "--network", "none", "--read-only",
            "--user", "0:0", "--cap-drop", "ALL",
            "--cap-add", "DAC_READ_SEARCH",
            "--mount", f"type=bind,src={path},dst=/tree,readonly",
            "--entrypoint", "/bin/bash", image, "-ceu", script,
        ],
        timeout=3600,
    )
    pieces = completed.stdout.strip().split()
    if (
        len(pieces) != 3
        or HEX64_RE.fullmatch(pieces[0]) is None
        or not pieces[1].isdigit()
        or not pieces[2].isdigit()
        or int(pieces[1]) != EXPECTED_TEMPLATE_REGULAR_FILE_COUNT
        or int(pieces[2]) != EXPECTED_TEMPLATE_REGULAR_FILE_BYTES
    ):
        raise AuthorityError("tree digest output differs")
    return {
        "tree_sha256": pieces[0],
        "file_count": int(pieces[1]),
        "byte_count": int(pieces[2]),
        "tree_identity_normalization": TREE_IDENTITY_NORMALIZATION,
    }


def _copy_failure_evidence(
    spec: Mapping[str, Any],
    failure: BaseException,
    source_identity: dict[str, Any] | None,
    destination_identity: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "schema_version": "mysql_tpch_bootstrap_failure_evidence_v1",
        "status": "FAIL_PHYSICAL_COPY_IDENTITY_BEFORE_DB_CLIENT",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "scenario": SCENARIO,
        "host_id": HOSTNAME,
        "attempt_token": spec["attempt_token"],
        "bundle_id": spec["bundle"]["bundle_id"],
        "spec_sha256": sha_bytes(canonical(spec)),
        "failure": str(failure),
        "source_identity": source_identity,
        "destination_identity": destination_identity,
        "database_or_client_started": False,
        "formal_r0_authorized": False,
        "formal_authorized": False,
        "accepted_round_authorized": False,
    }


def _wait_mysql(container: str, defaults_file: str | None = None) -> None:
    for _ in range(180):
        argv = (
            ["docker", "exec", container, "mysqladmin", "-uroot", "ping", "--silent"]
            if defaults_file is None
            else [
                "/usr/bin/mysql", f"--defaults-file={defaults_file}",
                "--protocol=TCP", "--host=127.0.0.1",
                f"--port={MYSQL_PORT}", "--user=root",
                "--connect-timeout=3", "--ssl-mode=DISABLED",
                "--get-server-public-key", "-NBe", "SELECT 1",
            ]
        )
        if _run(argv, timeout=10, check=False).returncode == 0:
            return
        time.sleep(1)
    raise AuthorityError("MySQL did not become ready")


def _execute_bootstrap_inner(
    spec: Mapping[str, Any], output: Path, main_agent_token: str
) -> dict[str, Any]:
    spec = validate_spec(spec)
    if (
        not isinstance(main_agent_token, str)
        or sha_bytes(main_agent_token.encode("utf-8"))
        != spec["main_agent_token_sha256"]
    ):
        raise AuthorityError("main-agent bootstrap token differs")
    if socket.gethostname() != HOSTNAME:
        raise AuthorityError("bootstrap must execute on node04")
    if os.geteuid() != 0 or os.getegid() != 0:
        raise AuthorityError(
            "bootstrap requires host root for sealed cgroup-v1 blkio enforcement"
        )
    bundle = Path(spec["bundle"]["path"])
    manifest = load_object(bundle / "bundle_manifest.json", "bundle manifest")
    invalidated = load_object(
        bundle / "invalidated_bootstrap_bundle.json",
        "invalidated bootstrap bundle",
    )
    invalidated_closure = load_object(
        bundle / "invalidated_closure_bundle.json",
        "invalidated closure bundle",
    )
    invalidated_lsof = load_object(
        bundle / "invalidated_lsof_bundle.json",
        "invalidated lsof-dependent bundle",
    )
    invalidated_fallback = load_object(
        bundle / "invalidated_fallback_bundle.json",
        "invalidated root-squash fallback bundle",
    )
    invalidated_nonroot_fallback = load_object(
        bundle / "invalidated_nonroot_fallback_bundle.json",
        "invalidated non-root fallback bundle",
    )
    invalidated_socket_identity = load_object(
        bundle / "invalidated_socket_identity_bundle.json",
        "invalidated logical-socket identity bundle",
    )
    invalidated_permission = load_object(
        bundle / "invalidated_permission_bundle.json",
        "invalidated datadir-permission bundle",
    )
    invalidated_permission_readback = load_object(
        bundle / "invalidated_permission_readback_bundle.json",
        "invalidated datadir-permission readback bundle",
    )
    invalidated_tree_digest = load_object(
        bundle / "invalidated_tree_digest_bundle.json",
        "invalidated tree-digest bundle",
    )
    invalidated_tree_digest_bytes = load_object(
        bundle / "invalidated_tree_digest_bytes_bundle.json",
        "invalidated tree-digest byte-count bundle",
    )
    invalidated_metadata_sensitive_tree_identity = load_object(
        bundle / "invalidated_metadata_sensitive_tree_identity_bundle.json",
        "invalidated metadata-sensitive tree-identity bundle",
    )
    invalidated_effective_blkio = load_object(
        bundle / "invalidated_effective_blkio_bundle.json",
        "invalidated effective-blkio bundle",
    )
    invalidated_client_volume = load_object(
        bundle / "invalidated_client_volume_bundle.json",
        "invalidated client-volume bundle",
    )
    invalidated_qualification_cwd = load_object(
        bundle / "invalidated_qualification_cwd_bundle.json",
        "invalidated qualification-cwd bundle",
    )
    invalidated_client_identity = load_object(
        bundle / "invalidated_client_identity_projection_bundle.json",
        "invalidated client-identity-projection bundle",
    )
    invalidated_import_bytecode = load_object(
        bundle / "invalidated_import_bytecode_bundle.json",
        "invalidated import-bytecode bundle",
    )
    invalidated_client_permission = load_object(
        bundle / "invalidated_client_permission_bundle.json",
        "invalidated client-permission bundle",
    )
    invalidated_shared_contract = load_object(
        bundle / "invalidated_shared_contract_bundle.json",
        "invalidated shared-contract bundle",
    )
    invalidated_host_tmpfs = load_object(
        bundle / "invalidated_host_tmpfs_bundle.json",
        "invalidated host-tmpfs bundle",
    )
    verify_sidecar(bundle / "bundle_manifest.json", "bundle manifest")
    actual_files = bundle_payload_files(bundle)
    if (
        bundle.name != spec["bundle"]["bundle_id"]
        or sha256(bundle / "bundle_manifest.json")
        != spec["bundle"]["manifest_sha256"]
        or manifest.get("bundle_id") != bundle.name
        or manifest.get("files") != actual_files
        or sha256(_contract_path(str(bundle)))
        != BASE_FOUR_SCENE_CONTRACT_SHA256
        or sha256(_tpch_amendment_path(str(bundle))) != CONTRACT_SHA256
        or sha256(bundle / "historical_v40_invalidation.json")
        != spec["v40_invalidation_sha256"]
        or invalidated.get("status") != "INVALIDATED_DO_NOT_LAUNCH"
        or invalidated.get("bundle_id") != INVALIDATED_BUNDLE_ID
        or invalidated.get("launch_authorized") is not False
        or invalidated_closure.get("status") != "INVALIDATED_DO_NOT_LAUNCH"
        or invalidated_closure.get("bundle_id")
        != INVALIDATED_CLOSURE_BUNDLE_ID
        or invalidated_closure.get("launch_authorized") is not False
        or invalidated_lsof.get("status") != "INVALIDATED_DO_NOT_LAUNCH"
        or invalidated_lsof.get("bundle_id") != INVALIDATED_LSOF_BUNDLE_ID
        or invalidated_lsof.get("launch_authorized") is not False
        or invalidated_fallback.get("status") != "INVALIDATED_DO_NOT_LAUNCH"
        or invalidated_fallback.get("bundle_id")
        != INVALIDATED_FALLBACK_BUNDLE_ID
        or invalidated_fallback.get("reason")
        != "docker_fallback_default_root_cannot_read_root_squashed_0700_bundle"
        or invalidated_fallback.get("launch_authorized") is not False
        or invalidated_nonroot_fallback.get("status")
        != "INVALIDATED_DO_NOT_LAUNCH"
        or invalidated_nonroot_fallback.get("bundle_id")
        != INVALIDATED_NONROOT_FALLBACK_BUNDLE_ID
        or invalidated_nonroot_fallback.get("reason")
        != "nonroot_host_pid_scanner_has_unreadable_pids"
        or invalidated_nonroot_fallback.get("launch_authorized") is not False
        or invalidated_socket_identity.get("status")
        != "INVALIDATED_DO_NOT_LAUNCH"
        or invalidated_socket_identity.get("bundle_id")
        != INVALIDATED_SOCKET_IDENTITY_BUNDLE_ID
        or invalidated_socket_identity.get("database_or_client_started")
        is not False
        or invalidated_socket_identity.get("launch_authorized") is not False
        or invalidated_permission.get("status") != "INVALIDATED_DO_NOT_LAUNCH"
        or invalidated_permission.get("bundle_id")
        != INVALIDATED_PERMISSION_BUNDLE_ID
        or invalidated_permission.get("database_or_client_started") is not False
        or invalidated_permission.get("launch_authorized") is not False
        or invalidated_permission.get("accepted_rounds_credit") != 0
        or invalidated_permission_readback.get("status")
        != "INVALIDATED_DO_NOT_LAUNCH"
        or invalidated_permission_readback.get("bundle_id")
        != INVALIDATED_PERMISSION_READBACK_BUNDLE_ID
        or invalidated_permission_readback.get("failure_stage")
        != "post_copy_pre_database_or_client"
        or invalidated_permission_readback.get("database_or_client_started")
        is not False
        or invalidated_permission_readback.get("database_pid") != 0
        or invalidated_permission_readback.get("client_pid") != 0
        or invalidated_permission_readback.get("launch_authorized") is not False
        or invalidated_permission_readback.get("accepted_rounds_credit") != 0
        or invalidated_tree_digest.get("status")
        != "INVALIDATED_DO_NOT_LAUNCH"
        or invalidated_tree_digest.get("bundle_id")
        != INVALIDATED_TREE_DIGEST_BUNDLE_ID
        or invalidated_tree_digest.get("bundle_manifest_sha256")
        != "b545b4e7ae47987a54082bf1a2e5b8ccbec6320f3bd6733f9bcd5d9edb11bb56"
        or invalidated_tree_digest.get("failure_stage")
        != "source_tree_digest_pre_copy_pre_database_or_client"
        or invalidated_tree_digest.get("database_or_client_started") is not False
        or invalidated_tree_digest.get("launch_authorized") is not False
        or invalidated_tree_digest.get("accepted_rounds_credit") != 0
        or invalidated_tree_digest_bytes.get("status")
        != "INVALIDATED_DO_NOT_LAUNCH"
        or invalidated_tree_digest_bytes.get("bundle_id")
        != INVALIDATED_TREE_DIGEST_BYTES_BUNDLE_ID
        or invalidated_tree_digest_bytes.get("stale_expected_regular_file_bytes")
        != 27_111_280_640
        or invalidated_tree_digest_bytes.get("read_only_scanner_regular_file_bytes")
        != EXPECTED_TEMPLATE_REGULAR_FILE_BYTES
        or invalidated_tree_digest_bytes.get("prior_authority_summary_regular_file_bytes")
        != EXPECTED_TEMPLATE_REGULAR_FILE_BYTES
        or invalidated_tree_digest_bytes.get("root_dac_read_search_scanner_preserved")
        is not True
        or invalidated_tree_digest_bytes.get("traversal_or_hash_error_policy")
        != "FAIL_CLOSED"
        or invalidated_tree_digest_bytes.get("failure_stage")
        != "source_tree_digest_pre_attempt_pre_database_or_client"
        or invalidated_tree_digest_bytes.get("attempt_created") is not False
        or invalidated_tree_digest_bytes.get("database_or_client_started")
        is not False
        or invalidated_tree_digest_bytes.get("database_pid") != 0
        or invalidated_tree_digest_bytes.get("client_pid") != 0
        or invalidated_tree_digest_bytes.get("accepted_rounds_credit") != 0
        or invalidated_tree_digest_bytes.get("launch_authorized") is not False
        or invalidated_metadata_sensitive_tree_identity.get("status")
        != "INVALIDATED_DO_NOT_LAUNCH"
        or invalidated_metadata_sensitive_tree_identity.get("bundle_id")
        != INVALIDATED_METADATA_SENSITIVE_TREE_IDENTITY_BUNDLE_ID
        or invalidated_metadata_sensitive_tree_identity.get("reason")
        != "tar_tree_identity_included_owner_and_mode_after_target_permission_normalization"
        or invalidated_metadata_sensitive_tree_identity.get(
            "ownership_and_mode_excluded_from_content_identity"
        )
        is not True
        or invalidated_metadata_sensitive_tree_identity.get(
            "target_permission_readback_remains_separate_and_strict"
        )
        is not True
        or invalidated_metadata_sensitive_tree_identity.get(
            "root_dac_read_search_scanner_preserved"
        )
        is not True
        or invalidated_metadata_sensitive_tree_identity.get(
            "traversal_or_hash_error_policy"
        )
        != "FAIL_CLOSED"
        or invalidated_metadata_sensitive_tree_identity.get("failure_stage")
        != "post_copy_pre_database_or_client"
        or invalidated_metadata_sensitive_tree_identity.get(
            "database_or_client_started"
        )
        is not False
        or invalidated_metadata_sensitive_tree_identity.get("database_pid") != 0
        or invalidated_metadata_sensitive_tree_identity.get("client_pid") != 0
        or invalidated_metadata_sensitive_tree_identity.get(
            "accepted_rounds_credit"
        )
        != 0
        or invalidated_metadata_sensitive_tree_identity.get(
            "launch_authorized"
        )
        is not False
        or invalidated_effective_blkio.get("status")
        != "INVALIDATED_DO_NOT_LAUNCH"
        or invalidated_effective_blkio.get("bundle_id")
        != INVALIDATED_EFFECTIVE_BLKIO_BUNDLE_ID
        or invalidated_effective_blkio.get("bundle_manifest_sha256")
        != INVALIDATED_EFFECTIVE_BLKIO_MANIFEST_SHA256
        or invalidated_effective_blkio.get("failure_stage")
        != "collect_only_before_fresh_r0"
        or invalidated_effective_blkio.get("reusable") is not False
        or invalidated_effective_blkio.get("accepted_rounds_credit") != 0
        or invalidated_effective_blkio.get("launch_authorized") is not False
        or invalidated_client_volume.get("status")
        != "INVALIDATED_DO_NOT_REUSE"
        or invalidated_client_volume.get("bundle_id")
        != INVALIDATED_CLIENT_VOLUME_BUNDLE_ID
        or invalidated_client_volume.get("bundle_manifest_sha256")
        != INVALIDATED_CLIENT_VOLUME_MANIFEST_SHA256
        or invalidated_client_volume.get("database_pid") != 87352
        or invalidated_client_volume.get("client_pid") != 87558
        or invalidated_client_volume.get("reason")
        != "mysql_image_implicit_var_lib_mysql_anonymous_writable_volume"
        or invalidated_client_volume.get("reusable") is not False
        or invalidated_client_volume.get("accepted_rounds_credit") != 0
        or invalidated_client_volume.get("r0_credit") != 0
        or invalidated_client_volume.get("launch_authorized") is not False
        or invalidated_qualification_cwd.get("status")
        != "INVALIDATED_DO_NOT_LAUNCH"
        or invalidated_qualification_cwd.get("bundle_id")
        != INVALIDATED_QUALIFICATION_CWD_BUNDLE_ID
        or invalidated_qualification_cwd.get("bundle_manifest_sha256")
        != INVALIDATED_QUALIFICATION_CWD_MANIFEST_SHA256
        or invalidated_qualification_cwd.get("reason")
        != "sealed_command_spec_cwd_outside_client_measurement_tmpfs"
        or invalidated_qualification_cwd.get("workload_started") is not False
        or invalidated_qualification_cwd.get("accepted_rounds_credit") != 0
        or invalidated_qualification_cwd.get("r0_credit") != 0
        or invalidated_qualification_cwd.get("reusable") is not False
        or invalidated_qualification_cwd.get("launch_authorized") is not False
        or invalidated_client_identity.get("status")
        != "INVALIDATED_DO_NOT_REUSE"
        or invalidated_client_identity.get("bundle_id")
        != INVALIDATED_CLIENT_IDENTITY_BUNDLE_ID
        or invalidated_client_identity.get("bundle_manifest_sha256")
        != INVALIDATED_CLIENT_IDENTITY_MANIFEST_SHA256
        or invalidated_client_identity.get("reason")
        != (
            "bootstrap_and_command_materializer_used_full_inspect_projection_"
            "while_database_counter_transport_and_qualification_used_"
            "canonical_immutable_projection"
        )
        or invalidated_client_identity.get(
            "same_live_client_inspect_config_sha256"
        )
        != "fbc8a5b5f9b025fe807793160d4645652a5ce9cb3f874bbce4e988b440a57fb5"
        or invalidated_client_identity.get(
            "collect_only_client_inspect_config_sha256"
        )
        != "440a1093e2584491583a8c0eda5d36055f056c75b0cba9ce3456a8eaeafb688b"
        or invalidated_client_identity.get("accepted_rounds_credit") != 0
        or invalidated_client_identity.get("r0_credit") != 0
        or invalidated_client_identity.get("reusable") is not False
        or invalidated_client_identity.get("formal_launch_authorized")
        is not False
        or invalidated_import_bytecode.get("status")
        != "INVALIDATED_DO_NOT_REUSE"
        or invalidated_import_bytecode.get("bundle_id")
        != INVALIDATED_IMPORT_BYTECODE_BUNDLE_ID
        or invalidated_import_bytecode.get("bundle_manifest_sha256")
        != INVALIDATED_IMPORT_BYTECODE_MANIFEST_SHA256
        or invalidated_import_bytecode.get("reason")
        != (
            "root_authority_import_created_unmanifested_projection_helper_"
            "bytecode_before_live_bundle_closure_scan"
        )
        or invalidated_import_bytecode.get(
            "bootstrap_reached_container_or_workload_start"
        )
        is not False
        or invalidated_import_bytecode.get("accepted_rounds_credit") != 0
        or invalidated_import_bytecode.get("r0_credit") != 0
        or invalidated_import_bytecode.get("reusable") is not False
        or invalidated_import_bytecode.get("formal_launch_authorized")
        is not False
        or invalidated_client_permission.get("status")
        != "INVALIDATED_DO_NOT_REUSE"
        or invalidated_client_permission.get("bundle_id")
        != INVALIDATED_CLIENT_PERMISSION_BUNDLE_ID
        or invalidated_client_permission.get("bundle_manifest_sha256")
        != INVALIDATED_CLIENT_PERMISSION_MANIFEST_SHA256
        or invalidated_client_permission.get("attempt_token")
        != "tpch-v2-20260731t020337z-c02b2656a8868d38b0dcab8a"
        or invalidated_client_permission.get("workload_started") is not False
        or invalidated_client_permission.get("r0_credit") != 0
        or invalidated_client_permission.get("accepted_rounds_credit") != 0
        or invalidated_client_permission.get("reusable") is not False
        or invalidated_client_permission.get("formal_r0_authorized") is not False
        or invalidated_client_permission.get("formal_authorized") is not False
        or invalidated_client_permission.get("accepted_round_authorized")
        is not False
        or invalidated_shared_contract.get("status")
        != "INVALIDATED_DO_NOT_LAUNCH"
        or invalidated_shared_contract.get("bundle_id")
        != INVALIDATED_SHARED_CONTRACT_BUNDLE_ID
        or invalidated_shared_contract.get("bundle_manifest_sha256")
        != INVALIDATED_SHARED_CONTRACT_MANIFEST_SHA256
        or invalidated_shared_contract.get("workload_started") is not False
        or invalidated_shared_contract.get("r0_credit") != 0
        or invalidated_shared_contract.get("accepted_rounds_credit") != 0
        or invalidated_shared_contract.get("reusable") is not False
        or invalidated_shared_contract.get("formal_r0_authorized") is not False
        or invalidated_shared_contract.get("formal_authorized") is not False
        or invalidated_shared_contract.get("accepted_round_authorized")
        is not False
        or invalidated_host_tmpfs.get("status")
        != "INVALIDATED_DO_NOT_LAUNCH"
        or invalidated_host_tmpfs.get("bundle_id")
        != INVALIDATED_HOST_TMPFS_BUNDLE_ID
        or invalidated_host_tmpfs.get("bundle_manifest_sha256")
        != INVALIDATED_HOST_TMPFS_MANIFEST_SHA256
        or invalidated_host_tmpfs.get("bootstrap_reached_container_or_workload_start")
        is not False
        or invalidated_host_tmpfs.get("workload_started") is not False
        or invalidated_host_tmpfs.get("r0_credit") != 0
        or invalidated_host_tmpfs.get("accepted_rounds_credit") != 0
        or invalidated_host_tmpfs.get("reusable") is not False
        or invalidated_host_tmpfs.get("formal_r0_authorized") is not False
        or invalidated_host_tmpfs.get("formal_launch_authorized") is not False
    ):
        raise AuthorityError("live bundle closure differs")
    for key in ("mysql_image", "client_image"):
        rows = json.loads(
            _run(["docker", "image", "inspect", spec[key]["image_id"]]).stdout
        )
        if (
            not isinstance(rows, list)
            or len(rows) != 1
            or rows[0].get("Id") != spec[key]["image_id"]
        ):
            raise AuthorityError(f"{key} image identity differs")
    device = os.stat(DEVICE)
    mount = os.stat(MOUNT)
    if (
        f"{os.major(device.st_rdev)}:{os.minor(device.st_rdev)}" != MAJOR_MINOR
        or f"{os.major(mount.st_dev)}:{os.minor(mount.st_dev)}" != MAJOR_MINOR
    ):
        raise AuthorityError("live device or mount major:minor differs")
    findmnt = _run(
        ["findmnt", "-n", "-o", "SOURCE,FSTYPE,TARGET", "--target", MOUNT],
        timeout=30,
    ).stdout.strip().split()
    if len(findmnt) != 3 or findmnt[0] != DEVICE or findmnt[2] != MOUNT:
        raise AuthorityError("live mount source/target differs")
    if not Path(TEMPLATE).is_dir():
        raise AuthorityError("TPC-H template is absent")
    try:
        template_owner_inventory = _template_owner_inventory(bundle, spec)
    except FallbackInventoryError as exc:
        atomic_json(
            output,
            {
                "schema_version": "mysql_tpch_bootstrap_failure_evidence_v1",
                "status": "FAIL_TEMPLATE_OWNER_INVENTORY_BEFORE_MUTATION",
                "formal_scope": "mysql_only",
                "cross_engine_comparison_prohibited": True,
                "postgresql_mutation_prohibited": True,
                "scenario": SCENARIO,
                "host_id": HOSTNAME,
                "attempt_token": spec["attempt_token"],
                "bundle_id": spec["bundle"]["bundle_id"],
                "failure": str(exc),
                "docker_fallback_argv": exc.argv,
                "docker_fallback_stdout": exc.stdout,
                "runtime_or_datadir_mutation_started": False,
                "formal_r0_authorized": False,
                "formal_authorized": False,
                "accepted_round_authorized": False,
            },
        )
        raise
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", MYSQL_PORT))
        except OSError as exc:
            raise AuthorityError("TPC-H port is occupied") from exc
    for name in spec["containers"].values():
        if _run(["docker", "inspect", name], check=False).returncode == 0:
            raise AuthorityError(f"attempt container already exists: {name}")
    plan = command_plan(spec)
    paths = spec["paths"]
    attempt_root = Path(paths["attempt_root"])
    datadir = Path(paths["datadir"])
    stage = Path(plan["copy"]["staging_destination"])
    host_tmpfs = Path(paths["host_measurement_tmpfs"])
    if any(path.exists() for path in (attempt_root, datadir, stage, host_tmpfs)):
        raise AuthorityError("attempt path already exists")
    runtime_publication = _publish_runtime(spec, plan, bundle)
    attempt_root.mkdir(parents=True, mode=0o700, exist_ok=False)
    if stat.S_IMODE(attempt_root.stat().st_mode) != 0o700:
        raise AuthorityError("attempt root mode differs")
    credentials = Path(paths["credentials_dir"])
    credentials.mkdir(mode=0o700)
    host_tmpfs.mkdir(parents=True, mode=0o700)
    measurement_bind = _prepare_secure_measurement_bind(host_tmpfs)
    stage.mkdir(mode=0o700)
    source_identity = None
    destination_identity = None
    target_permission_normalization = None
    try:
        source_identity = _tree_digest(
            spec["client_image"]["image_id"], TEMPLATE, source_tree=True
        )
        copy_completed = _run(plan["copy"]["argv"], timeout=7200)
        try:
            target_permission_normalization = json.loads(copy_completed.stdout)
        except json.JSONDecodeError as exc:
            raise AuthorityError(
                "target permission normalization evidence is invalid JSON"
            ) from exc
        if not _target_permission_normalization_valid(
            target_permission_normalization, source_identity["file_count"]
        ):
            raise AuthorityError("target permission normalization differs")
        destination_identity = _tree_digest(
            spec["client_image"]["image_id"], str(stage), source_tree=False
        )
        source_identity_after = _tree_digest(
            spec["client_image"]["image_id"], TEMPLATE, source_tree=True
        )
        if (
            source_identity != source_identity_after
            or source_identity != destination_identity
        ):
            raise AuthorityError("physical copy tree identity differs")
    except (AuthorityError, subprocess.CalledProcessError) as exc:
        atomic_json(
            output,
            _copy_failure_evidence(
                spec, exc, source_identity, destination_identity
            ),
        )
        raise
    source_inode = os.stat(TEMPLATE).st_ino
    destination_inode = os.stat(stage).st_ino
    if source_inode == destination_inode:
        raise AuthorityError("copy aliases template root inode")
    os.sync()
    try:
        extent_audit = _root_extent_audit(spec, bundle, Path(TEMPLATE), stage)
    except AuthorityError as exc:
        atomic_json(
            output,
            _copy_failure_evidence(
                spec, exc, source_identity, destination_identity
            ),
        )
        raise
    os.rename(stage, datadir)
    if (
        f"{os.major(os.stat(datadir).st_dev)}:{os.minor(os.stat(datadir).st_dev)}"
        != MAJOR_MINOR
    ):
        raise AuthorityError("datadir is outside frozen target device")
    datadir_stat = os.stat(datadir, follow_symlinks=False)
    if (
        datadir_stat.st_uid != MYSQL_DATADIR_UID
        or datadir_stat.st_gid != MYSQL_DATADIR_GID
        or stat.S_IMODE(datadir_stat.st_mode)
        != int(MYSQL_DATADIR_DIRECTORY_MODE, 8)
    ):
        raise AuthorityError("fresh TPC-H datadir target permission normalization differs")
    password = secrets.token_urlsafe(36)
    defaults = Path(spec["mysql_defaults_file"])
    defaults.write_text(
        "[client]\n"
        f"password={password}\nprotocol=TCP\nssl-mode=DISABLED\n"
        "get-server-public-key\n",
        encoding="utf-8",
    )
    defaults.chmod(0o600)
    bootstrap_name = plan["bootstrap_database"]["name"]
    _run(plan["bootstrap_database"]["argv"])
    _wait_mysql(bootstrap_name)
    sql = (
        "FLUSH PRIVILEGES; "
        "CREATE USER IF NOT EXISTS 'root'@'%' IDENTIFIED WITH "
        f"caching_sha2_password BY '{password}'; "
        f"ALTER USER 'root'@'%' IDENTIFIED WITH caching_sha2_password BY '{password}'; "
        "ALTER USER 'root'@'localhost' IDENTIFIED WITH "
        f"caching_sha2_password BY '{password}';"
    )
    _run(["docker", "exec", bootstrap_name, "mysql", "-uroot", "-NBe", sql])
    _run(["docker", "stop", "--time", "120", bootstrap_name], timeout=150)
    _run(["docker", "rm", bootstrap_name])
    _run(plan["database"]["argv"])
    _wait_mysql(spec["containers"]["database"], str(defaults))
    _run(
        [
            "/usr/bin/mysql", f"--defaults-file={defaults}",
            "--protocol=TCP", "--host=127.0.0.1", f"--port={MYSQL_PORT}",
            "--user=root", "-NBe",
            (
                "UPDATE mysql.server_cost SET cost_value=NULL; "
                "UPDATE mysql.engine_cost SET cost_value=NULL; "
                "FLUSH OPTIMIZER_COSTS;"
            ),
        ]
    )
    _run(plan["client"]["argv"])
    db_row = _inspect(spec["containers"]["database"])
    client_row = _inspect(spec["containers"]["client"])
    database_start = {
        "name": spec["containers"]["database"],
        "container_id": db_row.get("Id"),
        "pid": int((db_row.get("State") or {}).get("Pid") or 0),
        "starttime_ticks": _pid_starttime(
            int((db_row.get("State") or {}).get("Pid") or 0)
        ),
    }
    client_start = {
        "name": spec["containers"]["client"],
        "container_id": client_row.get("Id"),
        "pid": int((client_row.get("State") or {}).get("Pid") or 0),
        "starttime_ticks": _pid_starttime(
            int((client_row.get("State") or {}).get("Pid") or 0)
        ),
    }
    start_authority = {
        "schema_version": "mysql_tpch_distributed_v2_start_authority_v1",
        "status": "PASS_CONTAINERS_STARTED_BLKIO_REQUIRED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "scenario": SCENARIO,
        "host_id": HOSTNAME,
        "bundle_id": spec["bundle"]["bundle_id"],
        "attempt_token": spec["attempt_token"],
        "database": database_start,
        "client": client_start,
        "phase": "containers_started_before_effective_blkio",
        "database_or_client_started": True,
        "workload_started": False,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    }
    start_authority["authority_sha256"] = sha_bytes(
        canonical(start_authority)
    )
    start_path = Path(plan["start_authority"]["path"])
    atomic_json(start_path, start_authority)
    _run(plan["effective_blkio_enforce"]["argv"], timeout=60)
    blkio_path = Path(plan["effective_blkio_enforce"]["output"])
    verify_sidecar(start_path, "start authority")
    database_after_blkio = _inspect(spec["containers"]["database"])
    client_after_blkio = _inspect(spec["containers"]["client"])
    database_adopted = {
        "container_id": database_after_blkio.get("Id"),
        "pid": int((database_after_blkio.get("State") or {}).get("Pid") or 0),
        "starttime_ticks": _pid_starttime(
            int((database_after_blkio.get("State") or {}).get("Pid") or 0)
        ),
        "inspect": database_after_blkio,
    }
    client_adopted = {
        "container_id": client_after_blkio.get("Id"),
        "pid": int((client_after_blkio.get("State") or {}).get("Pid") or 0),
        "starttime_ticks": _pid_starttime(
            int((client_after_blkio.get("State") or {}).get("Pid") or 0)
        ),
        "inspect": client_after_blkio,
    }
    if (
        database_start["container_id"] != database_adopted["container_id"]
        or database_start["pid"] != database_adopted["pid"]
        or database_start["starttime_ticks"]
        != database_adopted["starttime_ticks"]
        or client_start["container_id"] != client_adopted["container_id"]
        or client_start["pid"] != client_adopted["pid"]
        or client_start["starttime_ticks"] != client_adopted["starttime_ticks"]
    ):
        raise AuthorityError("start-to-blkio adopt same-live identity differs")
    blkio_authority = _verify_effective_blkio_authority(
        spec, database_adopted
    )
    live_path = attempt_root / "live_readback.json"
    _run(plan["collect_only"]["argv"], timeout=900)
    live = load_object(live_path, "collect-only readback")
    evidence = {
        "schema_version": "mysql_tpch_distributed_v2_bootstrap_evidence_v1",
        "status": "PASS_BOOTSTRAP_LIVE_NOT_R0_OR_FORMAL_AUTHORITY",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "scenario": SCENARIO,
        "host_id": HOSTNAME,
        "attempt_token": spec["attempt_token"],
        "bundle_id": spec["bundle"]["bundle_id"],
        "bundle_manifest_sha256": spec["bundle"]["manifest_sha256"],
        "contract_sha256": CONTRACT_SHA256,
        "v40_invalidation_sha256": spec["v40_invalidation_sha256"],
        "spec_sha256": sha_bytes(canonical(spec)),
        "copy": {
            "command": "tar regular-file byte stream",
            "overlay": False,
            "reflink": False,
            "clone_api_used": False,
            "full_physical_copy": True,
            "atomic_rename_complete": True,
            "source": TEMPLATE,
            "destination": str(datadir),
            "device_major_minor": MAJOR_MINOR,
            "source_tree_sha256": source_identity["tree_sha256"],
            "destination_tree_sha256": destination_identity["tree_sha256"],
            "file_count": source_identity["file_count"],
            "byte_count": source_identity["byte_count"],
            "tree_identity_normalization": source_identity[
                "tree_identity_normalization"
            ],
            "source_root_inode": source_inode,
            "destination_root_inode": destination_inode,
            "extent_audit": extent_audit,
            "target_permission_normalization": target_permission_normalization,
        },
        "runtime_publication": runtime_publication,
        "template_owner_inventory": template_owner_inventory,
        "start_authority": start_authority,
        "effective_blkio_authority": blkio_authority,
        "measurement_bind": measurement_bind,
        "phase_order": plan["phase_order"],
        "database": database_adopted,
        "client": client_adopted,
        "collect_only": {
            "sha256": sha_bytes(canonical(live)),
            "document": live,
        },
        "qualification_only": True,
        "formal_r0_authorized": False,
        "formal_authorized": False,
        "accepted_round_authorized": False,
    }
    evidence["same_live_identity"] = validate_bootstrap_evidence(evidence, spec)
    validate_bootstrap_evidence(evidence, spec)
    atomic_json(output, evidence)
    return evidence


def _cleanup_failed_bootstrap(
    spec: Mapping[str, Any], failure: BaseException
) -> dict[str, Any]:
    """Clean only exact attempt-owned resources and read back the result."""
    attempt_root = Path(spec["paths"]["attempt_root"])
    datadir = Path(spec["paths"]["datadir"])
    stage = Path(str(datadir) + ".stage")
    host_tmpfs = Path(spec["paths"]["host_measurement_tmpfs"])
    expected_attempt_root = (
        Path(RUNTIME_ROOT) / "attempts" / spec["attempt_token"]
    )
    expected_tmpfs = (
        HOST_MEASUREMENT_ROOT / spec["attempt_token"]
    )
    if attempt_root != expected_attempt_root or host_tmpfs != expected_tmpfs:
        raise AuthorityError("refusing cleanup outside exact attempt paths")
    rows: list[dict[str, Any]] = []
    bootstrap = spec["containers"]["database"] + "_bootstrap"
    for name in (
        spec["containers"]["client"],
        spec["containers"]["database"],
        bootstrap,
    ):
        inspect = _run(["docker", "inspect", name], timeout=30, check=False)
        existed = inspect.returncode == 0
        stop_rc = None
        remove_rc = None
        if existed:
            stopped = _run(
                ["docker", "stop", "--time", "120", name],
                timeout=150,
                check=False,
            )
            stop_rc = stopped.returncode
            removed = _run(
                ["docker", "rm", name], timeout=30, check=False
            )
            remove_rc = removed.returncode
        absent = (
            _run(["docker", "inspect", name], timeout=30, check=False)
            .returncode
            != 0
        )
        rows.append(
            {
                "container": name,
                "existed": existed,
                "stop_returncode": stop_rc,
                "remove_returncode": remove_rc,
                "absent_after": absent,
            }
        )
    mount_before = _run(
        [
            "findmnt", "-n", "-o", "TARGET", "--target",
            str(host_tmpfs),
        ],
        timeout=30,
        check=False,
    )
    exact_mounted_before = (
        mount_before.returncode == 0
        and mount_before.stdout.strip() == str(host_tmpfs)
    )
    umount_returncode = None
    if exact_mounted_before:
        unmounted = _run(
            ["umount", str(host_tmpfs)], timeout=30, check=False
        )
        umount_returncode = unmounted.returncode
    mount_after = _run(
        [
            "findmnt", "-n", "-o", "TARGET", "--target",
            str(host_tmpfs),
        ],
        timeout=30,
        check=False,
    )
    exact_mounted_after = (
        mount_after.returncode == 0
        and mount_after.stdout.strip() == str(host_tmpfs)
    )
    removed_paths: list[str] = []
    if not exact_mounted_after:
        for path in (stage, datadir, host_tmpfs):
            if path.exists() or path.is_symlink():
                if path.is_symlink():
                    raise AuthorityError(
                        f"refusing failed-bootstrap symlink cleanup: {path}"
                    )
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
                removed_paths.append(str(path))
    cleanup_pass = (
        all(row["absent_after"] for row in rows)
        and not exact_mounted_after
        and not any(
            path.exists() or path.is_symlink()
            for path in (stage, datadir, host_tmpfs)
        )
    )
    return {
        "schema_version": "mysql_tpch_bootstrap_failure_cleanup_v1",
        "status": (
            "PASS_EXACT_ATTEMPT_FAILURE_CLEANUP"
            if cleanup_pass
            else "FAIL_EXACT_ATTEMPT_FAILURE_CLEANUP"
        ),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "scenario": SCENARIO,
        "attempt_token": spec["attempt_token"],
        "bundle_id": spec["bundle"]["bundle_id"],
        "failure": str(failure),
        "containers": rows,
        "host_tmpfs": {
            "path": str(host_tmpfs),
            "exact_mounted_before": exact_mounted_before,
            "umount_returncode": umount_returncode,
            "exact_mounted_after": exact_mounted_after,
        },
        "removed_paths": removed_paths,
        "attempt_root_retained_for_failure_evidence": True,
        "cleanup_passed": cleanup_pass,
        "workload_started_credit": 0,
        "r0_credit": 0,
        "accepted_rounds_credit": 0,
        "formal_r0_authorized": False,
        "formal_authorized": False,
        "accepted_round_authorized": False,
    }


def execute_bootstrap(
    spec: Mapping[str, Any], output: Path, main_agent_token: str
) -> dict[str, Any]:
    validated = validate_spec(spec)
    try:
        return _execute_bootstrap_inner(
            validated, output, main_agent_token
        )
    except BaseException as exc:
        cleanup = _cleanup_failed_bootstrap(validated, exc)
        evidence_path = (
            output
            if not output.exists()
            else output.with_name(output.name + ".failure_cleanup.json")
        )
        atomic_json(evidence_path, cleanup)
        if cleanup["cleanup_passed"] is not True:
            raise AuthorityError(
                f"{exc}; exact failed-bootstrap cleanup did not pass"
            ) from exc
        raise


def build_command_spec(
    spec: Mapping[str, Any],
    bootstrap: Mapping[str, Any],
    mode: str,
    peer_binding: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    spec = validate_spec(spec)
    identity = validate_bootstrap_evidence(bootstrap, spec)
    if mode not in ("qualification_solo", "qualification_paired"):
        raise AuthorityError("qualification mode differs")
    if (mode == "qualification_solo") != (peer_binding is None):
        raise AuthorityError("peer binding/mode differs")
    bundle = Path(spec["bundle"]["path"])
    python = Path(spec["paths"]["python_runtime_root"]) / "bin/python3.11"
    adapter = bundle / "qualification/mysql_sql_debug_metric_adapter_v1.py"
    query_dir = bundle / "qualification/sql/tpch"
    defaults = Path(spec["mysql_defaults_file"])
    sql_paths = sorted(query_dir.glob("*.sql"))
    expected_names = {f"Q{index}.sql" for index in range(1, 23)}
    if (
        python.is_symlink()
        or not python.is_file()
        or adapter.is_symlink()
        or not adapter.is_file()
        or defaults.is_symlink()
        or not defaults.is_file()
        or {path.name for path in sql_paths} != expected_names
    ):
        raise AuthorityError("TPC-H SQL command closure differs")
    code_files = {
        str(path): sha256(path)
        for path in [python, adapter, defaults, *sql_paths]
    }
    weights = ",".join(
        "16" if path.name == "Q18.sql" else "4" for path in sql_paths
    )
    host_metric = Path(spec["paths"]["host_measurement_tmpfs"]) / (
        f"{mode}.host_metrics.jsonl"
    )
    argv = [
        str(python),
        str(adapter),
        f"--attempt-id={spec['attempt_token']}-{mode}",
        "--metric-stream",
        "/measurement/native.jsonl",
        "--mysql-binary=/usr/bin/mysql",
        f"--defaults-extra-file={defaults}",
        "--host=127.0.0.1",
        f"--port={MYSQL_PORT}",
        "--user=root",
        "--database=tpch",
        f"--query-dir={query_dir}",
        "--concurrency=8",
        "--duration-seconds=240",
        f"--weights={weights}",
        "--seed=20260731",
    ]
    required_mounts = [
        {"source": str(bundle), "destination": str(bundle)},
        {
            "source": spec["paths"]["python_runtime_root"],
            "destination": spec["paths"]["python_runtime_root"],
        },
        {
            "source": spec["paths"]["credentials_dir"],
            "destination": spec["paths"]["credentials_dir"],
        },
    ]
    result = {
        "schema_version": "mysql_algorithm_c_debug_qualification_command_v1",
        "bundle_id": spec["bundle"]["bundle_id"],
        "bundle_manifest_sha256": spec["bundle"]["manifest_sha256"],
        "mode": mode,
        "qualification_attempt_id": f"{spec['attempt_token']}-{mode}",
        "debug_only": True,
        "capture_authorized": False,
        "formal_r0_authorized": False,
        "formal_authorized": False,
        "accepted_round_authorized": False,
        "argv": argv,
        "executable_sha256": sha256(python),
        "code_files": code_files,
        "environment": {},
        "cwd": "/measurement",
        "workload_metric_stream": {
            "schema_version":
                "mysql_algorithm_c_debug_workload_metric_stream_v1",
            "path": str(host_metric),
        },
        "workload_metric_adapter": {
            "schema_version":
                "mysql_algorithm_c_debug_workload_metric_adapter_v1",
            "kind": "sql_query_native",
        },
        "workload_process_isolation": {
            "cpuset_cpus": CLIENT_ENVELOPE["cpuset_cpus"],
            "cpuset_mems": CLIENT_ENVELOPE["cpuset_mems"],
            "root_and_descendants_exact": True,
        },
        "client_container": {
            "name": spec["containers"]["client"],
            "container_id": identity["client_container_id"],
            "image_id": identity["client_image_id"],
            "created": identity["client_created"],
            "inspect_config_sha256":
                identity["client_inspect_config_sha256"],
            "execution_user": CLIENT_ENVELOPE["user"],
            "required_read_only_mounts": required_mounts,
        },
        "measurement_output": {
            "host_tmpfs_directory": spec["paths"]["host_measurement_tmpfs"],
            "host_bind_source": str(
                Path(spec["paths"]["host_measurement_tmpfs"])
                / CLIENT_MEASUREMENT_SUBDIRECTORY
            ),
            "container_tmpfs_destination": "/measurement",
        },
        "paired_peer_workload": (
            None if peer_binding is None else dict(peer_binding)
        ),
        "stdout_host_bridge": {
            "producer": "docker_exec_stdout",
            "consumer": "mysql_distributed_workload_metric_adapter_v1",
            "host_tmpfs": spec["paths"]["host_measurement_tmpfs"],
            "docker_log_driver_dependency": False,
            "same_attempt_only": True,
        },
    }
    return result


def build_handoff(
    spec: Mapping[str, Any],
    bootstrap: Mapping[str, Any],
    gate: Mapping[str, Any],
    fresh_r0: Mapping[str, Any],
    debug_round: Mapping[str, Any] | None,
    formal10: bool,
) -> dict[str, Any]:
    identity = validate_bootstrap_evidence(bootstrap, spec)
    expected_identity = fresh_r0.get("same_live_identity")
    if expected_identity != identity:
        raise AuthorityError("fresh R0 changed same-live identity")
    if (
        gate.get("schema_version")
        != "mysql_algorithm_c_distributed_dynamic_gate_evidence_v1"
        or gate.get("status") != "PASS"
        or gate.get("scenario") != SCENARIO
        or gate.get("host_id") != HOSTNAME
        or gate.get("attempt_token") != spec["attempt_token"]
        or gate.get("duration_seconds") != 180
        or gate.get("minimum_samples_per_stream") != 181
        or gate.get("database_counter_missing_samples") != 0
        or gate.get("database_counter_timeout_count") != 0
        or gate.get("all_tolerances_passed") is not True
        or gate.get("ongoing_monitor_required") is not True
        or gate.get("serial_fallback_required") is not False
        or gate.get("accepted_round_authorized") is not False
    ):
        raise AuthorityError("dynamic gate is incomplete")
    fingerprint = fresh_r0.get("fingerprint") or {}
    l1 = fingerprint.get("l1") or {}
    l2 = fingerprint.get("l2") or {}
    l3 = fingerprint.get("l3") or {}
    query_fingerprints = fingerprint.get("query_fingerprints")
    fingerprint_complete = (
        fingerprint.get("schema_version")
        == "mysql_tpch_r0_l1_l2_l3_fingerprint_v1"
        and isinstance(query_fingerprints, list)
        and len(query_fingerprints) == 22
        and [row.get("query") for row in query_fingerprints]
        == [f"Q{index}.sql" for index in range(1, 23)]
        and isinstance(l1.get("operator_types"), list)
        and bool(l1["operator_types"])
        and isinstance(l1.get("join_order"), list)
        and bool(l1["join_order"])
        and isinstance(l1.get("scan_types"), list)
        and bool(l1["scan_types"])
        and isinstance(l1.get("parallel_structure"), dict)
        and isinstance(l2.get("workers"), dict)
        and isinstance(l2.get("hash_batches"), dict)
        and isinstance(l2.get("sort_spill"), dict)
        and isinstance(l2.get("bitmap_lossy_pages"), dict)
        and isinstance(l2.get("intermediate_result_scale"), dict)
        and isinstance(l3.get("memory_peak_bytes"), int)
        and l3["memory_peak_bytes"] > 0
        and isinstance(l3.get("dynamic_execution_memory_bytes"), int)
        and l3["dynamic_execution_memory_bytes"] >= 0
        and isinstance(l3.get("spill"), dict)
        and fingerprint.get("missing_level_policy")
        == "independent_candidate"
    )
    if (
        fresh_r0.get("schema_version")
        != "mysql_tpch_distributed_v2_fresh_r0_evidence_v1"
        or fresh_r0.get("status") != "PASS_FRESH_R0"
        or fresh_r0.get("attempt_token") != spec["attempt_token"]
        or fresh_r0.get("bundle_id") != spec["bundle"]["bundle_id"]
        or fresh_r0.get("dynamic_gate_sha256")
        != sha_bytes(canonical(gate))
        or fresh_r0.get("v40_reused") is not False
        or fresh_r0.get("r0_profile_gate_passed") is not True
        or fresh_r0.get("database_counter_gate_passed") is not True
        or fresh_r0.get("database_counter_missing_samples") != 0
        or fresh_r0.get("database_counter_timeout_count") != 0
        or int(fresh_r0.get("stable_seconds") or 0) < 180
        or int(fresh_r0.get("sample_count") or 0) < 181
        or fresh_r0.get("accepted_rounds_credit") != 0
        or fresh_r0.get("accepted_round_authorized") is not False
        or not fingerprint_complete
    ):
        raise AuthorityError("fresh R0 evidence is incomplete")
    if formal10:
        if not isinstance(debug_round, dict):
            raise AuthorityError("formal10 requires debug-round evidence")
        if debug_round.get("same_live_identity") != identity:
            raise AuthorityError("debug round changed same-live identity")
        bundle = Path(spec["bundle"]["path"])
        readiness_path = bundle / "tpch_formal10_readiness_v2.py"
        import importlib.util
        module_spec = importlib.util.spec_from_file_location(
            "tpch_v2_readiness", readiness_path
        )
        if module_spec is None or module_spec.loader is None:
            raise AuthorityError("readiness verifier cannot be loaded")
        module = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(module)
        module.verify_debug_round_document(
            debug_round,
            bundle_id=spec["bundle"]["bundle_id"],
            attempt_token=spec["attempt_token"],
            identity=identity,
        )
    return {
        "schema_version":
            "mysql_tpch_distributed_v2_same_live_formal10_handoff_v1",
        "status": (
            "READY_FORMAL10_SAME_LIVE_DB_AND_CLIENT"
            if formal10
            else "READY_DEBUG_SAME_LIVE_DB_AND_CLIENT"
        ),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "scenario": SCENARIO,
        "host_id": HOSTNAME,
        "attempt_token": spec["attempt_token"],
        "bundle_id": spec["bundle"]["bundle_id"],
        "bundle_manifest_sha256": spec["bundle"]["manifest_sha256"],
        "same_live_identity": identity,
        "new_database_or_client_container_prohibited": True,
        "new_campaign_planner_prohibited": True,
        "qualification_containers_adopted": True,
        "ongoing_rolling_gate_required": True,
        "dynamic_gate_sha256": sha_bytes(canonical(gate)),
        "fresh_r0_sha256": sha_bytes(canonical(fresh_r0)),
        "debug_round_sha256": (
            sha_bytes(canonical(debug_round)) if debug_round is not None else None
        ),
        "v40_invalidation_sha256": spec["v40_invalidation_sha256"],
        "v40_r0_reusable": False,
        "historical_accepted_round_credit": 0,
        "formal10_authorized": formal10,
        "target_accepted_rounds": 10 if formal10 else 0,
        "next_accepted_round": 1 if formal10 else None,
        "capture_r0_debug_or_failed_replay_countable_as_accepted": False,
        "accepted_round_authorized": False,
    }


def main() -> int:
    if "--internal-fiemap-audit" in sys.argv[1:]:
        internal = argparse.ArgumentParser()
        internal.add_argument("--internal-fiemap-audit", action="store_true")
        internal.add_argument("--source", type=Path, required=True)
        internal.add_argument("--destination", type=Path, required=True)
        args = internal.parse_args()
        print(json.dumps(
            _physical_extent_audit(args.source, args.destination),
            sort_keys=True,
        ))
        return 0
    if "--internal-proc-scan" in sys.argv[1:]:
        internal = argparse.ArgumentParser()
        internal.add_argument("--internal-proc-scan", action="store_true")
        internal.add_argument("--proc-root", type=Path, required=True)
        internal.add_argument("--template", required=True)
        args = internal.parse_args()
        value = _scan_proc_open_owners(args.proc_root, args.template)
        print(json.dumps(value, sort_keys=True, indent=2))
        return 0 if value["pass"] else 2
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--emit-plan", action="store_true")
    modes.add_argument("--execute-bootstrap", action="store_true")
    modes.add_argument("--verify-bootstrap", type=Path)
    modes.add_argument("--emit-command-spec", choices=("qualification_solo", "qualification_paired"))
    modes.add_argument("--build-handoff", action="store_true")
    parser.add_argument("--bootstrap", type=Path)
    parser.add_argument("--peer-binding", type=Path)
    parser.add_argument("--dynamic-gate", type=Path)
    parser.add_argument("--fresh-r0", type=Path)
    parser.add_argument("--debug-round", type=Path)
    parser.add_argument("--formal10", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--main-agent-token")
    args = parser.parse_args()
    verify_sidecar(args.spec, "bootstrap spec")
    spec = load_object(args.spec, "bootstrap spec")
    if args.emit_plan:
        value = command_plan(spec)
    elif args.execute_bootstrap:
        if args.output is None or args.main_agent_token is None:
            raise AuthorityError("execute-bootstrap requires output and token")
        value = execute_bootstrap(spec, args.output, args.main_agent_token)
    else:
        bootstrap_path = (
            args.verify_bootstrap
            if args.verify_bootstrap is not None
            else args.bootstrap
        )
        if bootstrap_path is None:
            raise AuthorityError("operation requires bootstrap evidence")
        verify_sidecar(bootstrap_path, "bootstrap evidence")
        bootstrap = load_object(bootstrap_path, "bootstrap evidence")
        if args.verify_bootstrap is not None:
            value = validate_bootstrap_evidence(bootstrap, spec)
        elif args.emit_command_spec:
            peer = (
                load_object(args.peer_binding, "peer binding")
                if args.peer_binding is not None
                else None
            )
            value = build_command_spec(
                spec, bootstrap, args.emit_command_spec, peer
            )
            if args.output is None:
                raise AuthorityError("emit-command-spec requires output")
            atomic_json(args.output, value)
        else:
            if args.output is None or args.dynamic_gate is None or args.fresh_r0 is None:
                raise AuthorityError("build-handoff requires output/gate/R0")
            value = build_handoff(
                spec,
                bootstrap,
                load_object(args.dynamic_gate, "dynamic gate"),
                load_object(args.fresh_r0, "fresh R0"),
                (
                    load_object(args.debug_round, "debug round")
                    if args.debug_round is not None
                    else None
                ),
                args.formal10,
            )
            atomic_json(args.output, value)
    print(json.dumps(value, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AuthorityError as exc:
        sys.stderr.write(f"NOT_READY: {exc}\n")
        raise SystemExit(2)
