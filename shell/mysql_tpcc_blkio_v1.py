#!/usr/bin/env python3
"""Root-only TPC-C cgroup-v1 blkio throttle authority.

Docker 20.10 can accept ``--device-*-bps`` while omitting both the inspect
fields and v1 throttle files.  This helper is deliberately a narrow, sealed
post-start phase: it touches only the database container's blkio controller
for the frozen TPC-C device and refuses every other cgroup layout.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from typing import Any, Mapping


DEVICE_MAJOR_MINOR = "259:2"
IO_BPS = 1_205_862_400
CGROUP_ROOT = Path("/sys/fs/cgroup/blkio")


class BlkioError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, ensure_ascii=True, indent=2) + "\n").encode()


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)


def _load(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise BlkioError("regular plan file required")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise BlkioError("plan must be a JSON object")
    return value


def _cgroup_path(pid: int, container_id: str) -> Path:
    matches: list[str] = []
    for line in Path(f"/proc/{pid}/cgroup").read_text(encoding="ascii").splitlines():
        hierarchy, controllers, relative = line.split(":", 2)
        if hierarchy != "" and "blkio" in controllers.split(","):
            matches.append(relative)
    if len(matches) != 1:
        raise BlkioError("database blkio-v1 cgroup membership differs")
    relative = matches[0].lstrip("/")
    if relative != "docker/" + container_id:
        raise BlkioError("database blkio-v1 cgroup path is not exact Docker v1")
    path = CGROUP_ROOT / relative
    if not path.is_dir() or path.is_symlink():
        raise BlkioError("database blkio-v1 cgroup directory is absent or unsafe")
    return path


def _rate(path: Path) -> int | None:
    if path.is_symlink() or not path.is_file():
        raise BlkioError("blkio throttle file is absent or unsafe")
    found: list[int] = []
    for line in path.read_text(encoding="ascii").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == DEVICE_MAJOR_MINOR:
            found.append(int(parts[1]))
    if len(found) > 1:
        raise BlkioError("duplicate target-device blkio throttle rows")
    return found[0] if found else None


def _atomic_owned(path: Path, value: Mapping[str, Any], uid: int, gid: int) -> None:
    temporary = path.with_name("." + path.name + "." + str(os.getpid()))
    descriptor = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(canonical(value)); stream.flush(); os.fsync(stream.fileno())
        os.chown(temporary, uid, gid); os.chmod(temporary, 0o600); os.replace(temporary, path)
        parent = os.open(str(path.parent), os.O_RDONLY)
        try: os.fsync(parent)
        finally: os.close(parent)
    finally:
        if temporary.exists(): temporary.unlink()


def enforce(plan_path: Path, output: Path) -> dict[str, Any]:
    if os.geteuid() != 0 or os.getegid() != 0:
        raise BlkioError("sealed blkio authority must run as host root")
    plan = _load(plan_path)
    database = plan.get("database") or {}
    attempt_id = plan.get("attempt_id")
    bundle_id = plan.get("bundle_id")
    datadir = Path(str(database.get("datadir") or ""))
    attempt_root = datadir.parent.parent
    expected_output = attempt_root / "blkio_throttle_authority.json"
    if output.resolve(strict=False) != expected_output or output.exists():
        raise BlkioError("blkio authority output path differs or already exists")
    root_stat = attempt_root.stat()
    if attempt_root.is_symlink() or not attempt_root.is_dir() or stat.S_IMODE(root_stat.st_mode) != 0o700:
        raise BlkioError("attempt root ownership/mode authority differs")
    name = database.get("name")
    if not isinstance(name, str) or not isinstance(attempt_id, str) or not isinstance(bundle_id, str):
        raise BlkioError("plan database identity differs")
    inspected = _run(["docker", "inspect", name])
    if inspected.returncode:
        raise BlkioError("database inspect failed before blkio enforcement")
    rows = json.loads(inspected.stdout)
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise BlkioError("database inspect cardinality differs")
    row = rows[0]; state = row.get("State") or {}; config = row.get("Config") or {}
    labels = config.get("Labels") or {}; container_id = str(row.get("Id") or "")
    pid = int(state.get("Pid") or 0)
    if (state.get("Running") is not True or pid <= 1 or len(container_id) != 64
        or labels.get("ra.bundle_id") != bundle_id or labels.get("ra.attempt_id") != attempt_id
        or labels.get("ra.role") != "database"):
        raise BlkioError("live database identity differs before blkio enforcement")
    cgroup = _cgroup_path(pid, container_id)
    read_file = cgroup / "blkio.throttle.read_bps_device"; write_file = cgroup / "blkio.throttle.write_bps_device"
    for file_path in (read_file, write_file):
        with file_path.open("w", encoding="ascii") as stream:
            # cgroup-v1 pseudo-files do not promise fsync(2) support (and
            # commonly return EINVAL).  The authoritative operation is the
            # synchronous write followed by an exact controller readback.
            stream.write(f"{DEVICE_MAJOR_MINOR} {IO_BPS}\n"); stream.flush()
    read_rate, write_rate = _rate(read_file), _rate(write_file)
    if read_rate != IO_BPS or write_rate != IO_BPS:
        raise BlkioError("cgroup-v1 target-device throttle readback differs")
    result = {
        "schema_version": "mysql_tpcc_blkio_v1_throttle_authority_v1", "status": "PASS",
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "bundle_id": bundle_id, "attempt_id": attempt_id, "database_container": name,
        "database_container_id": container_id, "database_pid": pid, "host_effective_uid_gid": [0, 0],
        "cgroup_version": "v1", "cgroup_path": str(cgroup), "device_major_minor": DEVICE_MAJOR_MINOR,
        "read_bps": read_rate, "write_bps": write_rate,
        "read_throttle_file": str(read_file), "write_throttle_file": str(write_file),
        "docker_hostconfig_readback_not_authoritative": True,
        "database_or_client_started": True, "workload_started": False,
        "formal_launch_authorized": False, "accepted_round_authorized": False,
    }
    _atomic_owned(output, result, root_stat.st_uid, root_stat.st_gid)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("enforce", nargs="?")
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(enforce(args.plan, args.output), sort_keys=True))
    return 0


if __name__ == "__main__":
    try: raise SystemExit(main())
    except (OSError, ValueError, BlkioError) as exc:
        sys.stderr.write("NOT_READY: " + str(exc) + "\n"); raise SystemExit(2)
