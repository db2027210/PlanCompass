#!/usr/bin/env python3
"""Create and seal the private TPC-C measurement tmpfs as root.

This helper is intentionally limited to one attempt-owned measurement path.
It never starts or changes a database/container.
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
from typing import Any


RUNTIME_ROOT = Path(
    "/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/"
    "runtime_algorithm_c_successor_20260729"
)
CLIENT_UID = 1008
CLIENT_GID = 1008
BRIDGE_MODE = 0o700
BRIDGE_BYTES = 2 * 1024**3
TOKEN_RE = re.compile(r"^[A-Za-z0-9_.:-]{16,128}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")


class BridgeError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(canonical(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        check=False,
    )


def _mount_row(path: Path) -> dict[str, str]:
    result = _run([
        "findmnt", "--noheadings", "--output",
        "SOURCE,FSTYPE,TARGET,OPTIONS,MAJ:MIN", "--target", str(path),
    ])
    if result.returncode:
        raise BridgeError("findmnt failed: " + result.stderr[-500:])
    columns = result.stdout.strip().split(None, 4)
    if len(columns) != 5:
        raise BridgeError("measurement bridge findmnt row differs")
    return dict(zip(("source", "filesystem", "target", "options", "device"), columns))


def enforce(
    *, path: Path, attempt_id: str, bundle_id: str, output: Path
) -> dict[str, Any]:
    if os.geteuid() != 0 or os.getegid() != 0:
        raise BridgeError("measurement bridge helper requires host root")
    expected_root = RUNTIME_ROOT / attempt_id
    expected_path = expected_root / "measurement"
    expected_output = expected_root / "measurement_bridge_authority.json"
    if (
        TOKEN_RE.fullmatch(attempt_id) is None
        or SHA_RE.fullmatch(bundle_id) is None
        or path.resolve() != expected_path
        or output.resolve() != expected_output
        or path.is_symlink()
    ):
        raise BridgeError("measurement bridge attempt/path binding differs")
    path.mkdir(mode=BRIDGE_MODE, exist_ok=True)
    row = _mount_row(path)
    if Path(row["target"]).resolve() != path or row["filesystem"] != "tmpfs":
        result = _run([
            "mount", "-t", "tmpfs", "-o",
            "size=2147483648,mode=0700,uid=1008,gid=1008,nosuid,nodev,noexec",
            "tmpfs", str(path),
        ])
        if result.returncode:
            raise BridgeError("measurement tmpfs mount failed: " + result.stderr[-500:])
        row = _mount_row(path)
    if Path(row["target"]).resolve() != path or row["filesystem"] != "tmpfs":
        raise BridgeError("measurement bridge is not its own tmpfs mount")
    os.chown(path, CLIENT_UID, CLIENT_GID)
    os.chmod(path, BRIDGE_MODE)
    value = path.stat()
    filesystem = os.statvfs(path)
    live_size_bytes = filesystem.f_frsize * filesystem.f_blocks
    row = _mount_row(path)
    options = set(row["options"].split(","))
    if (
        value.st_uid != CLIENT_UID
        or value.st_gid != CLIENT_GID
        or stat.S_IMODE(value.st_mode) != BRIDGE_MODE
        or live_size_bytes != BRIDGE_BYTES
        or Path(row["target"]).resolve() != path
        or row["filesystem"] != "tmpfs"
        or not {"nosuid", "nodev", "noexec"}.issubset(options)
    ):
        raise BridgeError("measurement bridge ownership/mode/options differ")
    authority = {
        "schema_version": "mysql_tpcc_measurement_bridge_authority_v1",
        "status": "PASS",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "bundle_id": bundle_id,
        "attempt_id": attempt_id,
        "host_effective_uid_gid": [0, 0],
        "host_path": str(path),
        "container_path": "/measurement",
        "filesystem": "tmpfs",
        "mount_source": row["source"],
        "mount_target": row["target"],
        "device_major_minor": row["device"],
        "size_bytes": BRIDGE_BYTES,
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
    atomic_json(output, authority)
    os.chown(output, CLIENT_UID, CLIENT_GID)
    os.chmod(output, 0o600)
    return authority


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("enforce",))
    parser.add_argument("--path", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--bundle-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    value = enforce(
        path=args.path, attempt_id=args.attempt_id,
        bundle_id=args.bundle_id, output=args.output,
    )
    print(json.dumps({
        "status": value["status"], "bundle_id": value["bundle_id"],
        "attempt_id": value["attempt_id"],
        "device_major_minor": value["device_major_minor"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, BridgeError) as exc:
        sys.stderr.write("NOT_READY: " + str(exc) + "\n")
        raise SystemExit(2)
