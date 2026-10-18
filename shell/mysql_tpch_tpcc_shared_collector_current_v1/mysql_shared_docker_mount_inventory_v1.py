#!/usr/bin/env python3
"""Read-only root mount inventory for one explicitly sealed Docker socket.

The helper never starts, stops, creates, removes, or execs into a container.
It accepts only the two MySQL-only dedicated daemon sockets used by the
node04 TPC-H/TPC-C dynamic-interference pair.  The caller binds this file by
SHA-256 and the result records the selected socket.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys


HEX64 = re.compile(r"^[0-9a-f]{64}$")
SOCKET_URIS = {
    "unix:///run/ra-docker-mysql-tpch-v1.sock",
    "unix:///run/ra-docker-mysql-tpcc-v1.sock",
}


class InventoryError(RuntimeError):
    pass


def canonical(value):
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run(argv, label):
    completed = subprocess.run(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        check=False,
    )
    if completed.returncode:
        raise InventoryError("%s failed: %s" % (label, completed.stderr[-500:]))
    return completed.stdout


def major_minor(path):
    try:
        info = os.stat(str(path), follow_symlinks=True)
    except (OSError, PermissionError) as exc:
        raise InventoryError("mount source unreadable: %s" % exc)
    device = info.st_rdev if stat.S_ISBLK(info.st_mode) else info.st_dev
    return "%d:%d" % (os.major(device), os.minor(device))


def inventory(socket_uri):
    if os.geteuid() != 0 or os.getegid() != 0:
        raise InventoryError("read-only Docker mount inventory requires host root")
    docker = ["docker", "--host", socket_uri]
    identifiers = [
        value.strip()
        for value in run(
            docker + ["ps", "--no-trunc", "--quiet"],
            "running Docker inventory",
        ).splitlines()
        if value.strip()
    ]
    if not identifiers:
        return []
    raw = run(docker + ["inspect"] + identifiers, "running Docker inspect")
    try:
        rows = json.loads(raw)
    except ValueError as exc:
        raise InventoryError("running Docker inspect JSON invalid: %s" % exc)
    if not isinstance(rows, list) or len(rows) != len(identifiers):
        raise InventoryError("running Docker inspect cardinality differs")
    result = []
    for row in rows:
        container_id = row.get("Id")
        state = row.get("State") or {}
        pid = int(state.get("Pid") or 0)
        if (
            not isinstance(container_id, str)
            or HEX64.fullmatch(container_id) is None
            or state.get("Running") is not True
            or pid <= 0
        ):
            raise InventoryError("running Docker immutable identity differs")
        mounts = []
        for mount in row.get("Mounts") or []:
            source = mount.get("Source")
            destination = mount.get("Destination")
            rw = mount.get("RW")
            if (
                not isinstance(source, str)
                or not source.startswith("/")
                or not isinstance(destination, str)
                or not destination.startswith("/")
                or not isinstance(rw, bool)
            ):
                raise InventoryError("running Docker mount inventory differs")
            resolved = os.path.realpath(source)
            mounts.append(
                {
                    "source": resolved,
                    "destination": destination,
                    "major_minor": major_minor(resolved),
                    "rw": rw,
                }
            )
        mounts.sort(
            key=lambda item: (
                item["source"],
                item["destination"],
                item["major_minor"],
                item["rw"],
            )
        )
        result.append(
            {
                "container_id": container_id,
                "name": str(row.get("Name") or "").lstrip("/"),
                "init_pid": pid,
                "mounts": mounts,
            }
        )
    result.sort(key=lambda item: item["container_id"])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-helper-sha256", required=True)
    parser.add_argument("--docker-socket-uri", required=True)
    args = parser.parse_args()
    if HEX64.fullmatch(args.expected_helper_sha256) is None:
        raise InventoryError("expected helper SHA-256 differs")
    if args.docker_socket_uri not in SOCKET_URIS:
        raise InventoryError("Docker socket is outside sealed MySQL-only allowlist")
    actual = sha256(Path(__file__).resolve())
    if actual != args.expected_helper_sha256:
        raise InventoryError("sealed helper SHA-256 differs")
    value = {
        "schema_version": "mysql_shared_host_root_mount_inventory_v1",
        "status": "PASS_READ_ONLY_ROOT_DOCKER_MOUNT_INVENTORY",
        "helper_sha256": actual,
        "docker_socket_uri": args.docker_socket_uri,
        "effective_uid_gid": [os.geteuid(), os.getegid()],
        "mutation_performed": False,
        "postgresql_read_only_inventory_allowed": True,
        "containers": inventory(args.docker_socket_uri),
    }
    sys.stdout.write(canonical(value).decode("utf-8"))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except InventoryError as exc:
        sys.stderr.write("NOT_READY: %s\n" % exc)
        raise SystemExit(2)
