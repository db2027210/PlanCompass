#!/usr/bin/env python3
"""Root-only TPC-H cgroup-v1 effective blkio throttle authority."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Mapping


DEVICE_MAJOR_MINOR = "259:1"
IO_BPS = 100_663_296
CGROUP_ROOT = Path("/sys/fs/cgroup/blkio")
HEX64 = re.compile(r"[0-9a-f]{64}$")


class BlkioError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, ensure_ascii=True, indent=2) + "\n"
    ).encode("ascii")


def _load(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise BlkioError("regular start authority is required")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise BlkioError("start authority must be a JSON object")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verify_sidecar(path: Path) -> None:
    sidecar = Path(str(path) + ".sha256")
    expected = "%s  %s\n" % (_sha256(path), path.name)
    if (
        sidecar.is_symlink()
        or not sidecar.is_file()
        or sidecar.read_text(encoding="ascii") != expected
    ):
        raise BlkioError("start authority sidecar differs")


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _cgroup_path(pid: int, container_id: str) -> Path:
    matches: list[str] = []
    for line in Path(f"/proc/{pid}/cgroup").read_text(
        encoding="ascii"
    ).splitlines():
        hierarchy, controllers, relative = line.split(":", 2)
        if hierarchy and "blkio" in controllers.split(","):
            matches.append(relative)
    if matches != ["/docker/" + container_id]:
        raise BlkioError("database blkio-v1 membership is not exact Docker v1")
    path = CGROUP_ROOT / "docker" / container_id
    if path.is_symlink() or not path.is_dir():
        raise BlkioError("database blkio-v1 cgroup directory is unsafe")
    return path


def _rate(path: Path) -> int | None:
    if path.is_symlink() or not path.is_file():
        raise BlkioError("blkio throttle pseudo-file is absent or unsafe")
    found: list[int] = []
    for line in path.read_text(encoding="ascii").splitlines():
        pieces = line.split()
        if len(pieces) == 2 and pieces[0] == DEVICE_MAJOR_MINOR:
            found.append(int(pieces[1]))
    if len(found) > 1:
        raise BlkioError("duplicate target-device throttle rows")
    return found[0] if found else None


def _atomic(path: Path, value: Mapping[str, Any]) -> None:
    sidecar = Path(str(path) + ".sha256")
    if path.exists() or sidecar.exists():
        raise BlkioError("refusing to overwrite blkio authority")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(
        str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(canonical(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        sidecar.write_text(
            "%s  %s\n" % (_sha256(path), path.name), encoding="ascii"
        )
        os.chmod(sidecar, 0o600)
    finally:
        if temporary.exists():
            temporary.unlink()


def enforce(start_path: Path, output: Path) -> dict[str, Any]:
    if os.geteuid() != 0 or os.getegid() != 0:
        raise BlkioError("TPC-H effective blkio authority requires host root")
    start = _load(start_path)
    _verify_sidecar(start_path)
    unsigned_start = dict(start)
    recorded_start_sha = unsigned_start.pop("authority_sha256", None)
    attempt_root = start_path.parent
    if (
        start_path.resolve() != attempt_root / "start_authority.json"
        or output.resolve(strict=False)
        != attempt_root / "blkio_throttle_authority.json"
        or output.exists()
        or start.get("schema_version")
        != "mysql_tpch_distributed_v2_start_authority_v1"
        or start.get("status") != "PASS_CONTAINERS_STARTED_BLKIO_REQUIRED"
        or start.get("formal_scope") != "mysql_only"
        or start.get("cross_engine_comparison_prohibited") is not True
        or recorded_start_sha
        != hashlib.sha256(canonical(unsigned_start)).hexdigest()
        or start.get("accepted_round_authorized") is not False
    ):
        raise BlkioError("start/output authority boundary differs")
    database = start.get("database") or {}
    name = database.get("name")
    expected_id = database.get("container_id")
    expected_pid = database.get("pid")
    bundle_id = start.get("bundle_id")
    attempt = start.get("attempt_token")
    if (
        not isinstance(name, str)
        or HEX64.fullmatch(str(expected_id or "")) is None
        or not isinstance(expected_pid, int)
        or expected_pid <= 1
        or HEX64.fullmatch(str(bundle_id or "")) is None
        or not isinstance(attempt, str)
    ):
        raise BlkioError("start database identity differs")
    inspected = _run(["docker", "inspect", name])
    if inspected.returncode:
        raise BlkioError("database inspect failed before blkio enforcement")
    rows = json.loads(inspected.stdout)
    if not isinstance(rows, list) or len(rows) != 1:
        raise BlkioError("database inspect cardinality differs")
    row = rows[0]
    state = row.get("State") or {}
    labels = (row.get("Config") or {}).get("Labels") or {}
    container_id = str(row.get("Id") or "")
    pid = int(state.get("Pid") or 0)
    if (
        state.get("Running") is not True
        or container_id != expected_id
        or pid != expected_pid
        or labels.get("ra.bundle_id") != bundle_id
        or labels.get("ra.attempt") != attempt
        or labels.get("ra.scenario") != "tpch_memory"
    ):
        raise BlkioError("same-live database identity differs")
    cgroup = _cgroup_path(pid, container_id)
    read_file = cgroup / "blkio.throttle.read_bps_device"
    write_file = cgroup / "blkio.throttle.write_bps_device"
    for path in (read_file, write_file):
        with path.open("w", encoding="ascii") as stream:
            stream.write(f"{DEVICE_MAJOR_MINOR} {IO_BPS}\n")
            stream.flush()
    read_rate, write_rate = _rate(read_file), _rate(write_file)
    if read_rate != IO_BPS or write_rate != IO_BPS:
        raise BlkioError("effective cgroup-v1 target-device throttle differs")
    result = {
        "schema_version": "mysql_tpch_blkio_v1_throttle_authority_v1",
        "status": "PASS_EFFECTIVE_CGROUP_V1_THROTTLE",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "bundle_id": bundle_id,
        "attempt_token": attempt,
        "database_container": name,
        "database_container_id": container_id,
        "database_pid": pid,
        "host_effective_uid_gid": [0, 0],
        "cgroup_version": "v1",
        "cgroup_path": str(cgroup),
        "device_major_minor": DEVICE_MAJOR_MINOR,
        "read_bps": read_rate,
        "write_bps": write_rate,
        "read_throttle_file": str(read_file),
        "write_throttle_file": str(write_file),
        "docker_hostconfig_is_declaration_not_effective_authority": True,
        "database_or_client_started": True,
        "workload_started": False,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    }
    result["authority_sha256"] = hashlib.sha256(
        canonical(result)
    ).hexdigest()
    _atomic(output, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("enforce", nargs="?")
    parser.add_argument("--start-authority", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(enforce(args.start_authority, args.output), sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, BlkioError) as exc:
        sys.stderr.write("NOT_READY: " + str(exc) + "\n")
        raise SystemExit(2)
