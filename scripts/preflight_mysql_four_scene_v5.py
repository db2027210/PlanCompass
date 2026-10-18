#!/usr/bin/env python3
"""Docker-Socket inventory with a short management-only snapshot lock."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import http.client
import json
from pathlib import Path
import re
import socket
import time
from typing import Any, Iterator
from urllib.parse import quote

from scripts import preflight_mysql_four_scene_v3 as predecessor


_process_inventory = predecessor._process_inventory
_cpus = predecessor._cpus
_compress = predecessor._compress

DOCKER_SOCKET = Path("/var/run/docker.sock")
SNAPSHOT_LOCK = Path("/tmp/mysql_formal_docker_inventory_snapshot_v1.lock")
SNAPSHOT_LOCK_TIMEOUT_SECONDS = 120.0


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: Path, timeout: float) -> None:
        super().__init__("localhost", timeout=timeout)
        self.socket_path = socket_path

    def connect(self) -> None:
        stream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stream.settimeout(self.timeout)
        stream.connect(str(self.socket_path))
        self.sock = stream


def _docker_get(path: str, timeout: float) -> Any:
    connection = _UnixHTTPConnection(DOCKER_SOCKET, timeout)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        payload = response.read()
        if response.status != 200:
            raise RuntimeError(
                f"Docker API GET {path} returned {response.status}: "
                f"{payload[:200]!r}"
            )
        return json.loads(payload)
    finally:
        connection.close()


@contextmanager
def _snapshot_lock() -> Iterator[dict[str, Any]]:
    started = time.monotonic()
    deadline = started + SNAPSHOT_LOCK_TIMEOUT_SECONDS
    with SNAPSHOT_LOCK.open("a+", encoding="ascii") as stream:
        while True:
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        "Docker inventory management snapshot lock timed out"
                    )
                time.sleep(0.1)
        evidence = {
            "lock_path": str(SNAPSHOT_LOCK),
            "wait_seconds": round(time.monotonic() - started, 6),
            "scope": "docker_list_and_inspect_snapshot_only",
        }
        try:
            yield evidence
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _summary_name(summary: dict[str, Any]) -> str:
    names = summary.get("Names") or []
    if isinstance(names, list) and names:
        return str(names[0]).removeprefix("/")
    return str(names or "").removeprefix("/")


def _summary_database_like(summary: dict[str, Any]) -> bool:
    names = summary.get("Names") or []
    name_text = " ".join(map(str, names)) if isinstance(names, list) else str(names)
    text = " ".join(
        [
            name_text,
            str(summary.get("Image") or ""),
            str(summary.get("Command") or ""),
        ]
    )
    return bool(
        re.search(
            r"(?:mysqld|mysql(?:[:/_-]|$)|postgres|opengauss|gaussdb|"
            r"mongod|mongo(?:[:/_-]|$)|redis-server|redis(?:[:/_-]|$))",
            text,
            re.I,
        )
    )


def _docker_inventory(
    target_cpus: set[int], intended_names: set[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    intended_names = {name.removeprefix("/") for name in intended_names}
    inventory: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    with _snapshot_lock() as lock_evidence:
        summaries = _docker_get("/containers/json?all=0", timeout=30.0)
        if not isinstance(summaries, list):
            raise RuntimeError("Docker API container list is not an array")
        for summary in summaries:
            if not isinstance(summary, dict):
                raise RuntimeError("Docker API container summary is not an object")
            name = _summary_name(summary)
            container_id = str(summary.get("Id") or "")
            try:
                if not container_id:
                    raise RuntimeError("Docker API summary has no container ID")
                document = _docker_get(
                    f"/containers/{quote(container_id, safe='')}/json", timeout=15.0
                )
                if not isinstance(document, dict):
                    raise RuntimeError("Docker API inspect result is not an object")
            except (
                OSError,
                TimeoutError,
                http.client.HTTPException,
                json.JSONDecodeError,
                RuntimeError,
            ) as error:
                database_like = _summary_database_like(summary)
                row = {
                    "name": name,
                    "container_id": container_id or None,
                    "pid": None,
                    "command": str(summary.get("Command") or ""),
                    "image": str(summary.get("Image") or ""),
                    "cpuset_cpus": None,
                    "cpuset_mems": None,
                    "overlap_cpus": "",
                    "disk2_bind_sources": [],
                    "docker_api_mount_summary": summary.get("Mounts") or [],
                    "database_like": database_like,
                    "intended_successor_container": name in intended_names,
                    "inspect_status": "TIMEOUT_OR_TRANSIENT_ERROR_SUMMARY_RECORDED",
                    "inspect_error_type": type(error).__name__,
                    "summary_state": summary.get("State"),
                    "summary_status": summary.get("Status"),
                    "inventory_source": "docker_unix_socket_api",
                    "snapshot_lock": dict(lock_evidence),
                }
                inventory.append(row)
                if name in intended_names or database_like:
                    blockers.append(
                        {
                            **row,
                            "reason": (
                                "database_or_intended_container_inspect_failed_closed"
                            ),
                        }
                    )
                continue

            host = document.get("HostConfig") or {}
            mounts = document.get("Mounts") or []
            cpuset_text = str(host.get("CpusetCpus") or "0-511")
            overlap = target_cpus & _cpus(cpuset_text)
            disk2_sources = sorted(
                str(row.get("Source"))
                for row in mounts
                if str(row.get("Source") or "").startswith("/disk2/")
            )
            command = " ".join(
                [
                    str(document.get("Path") or ""),
                    *map(str, document.get("Args") or []),
                ]
            ).strip()
            database_like = bool(
                re.search(
                    r"(?:mysqld|postgres|gaussdb|mongod|redis-server)",
                    command,
                    re.I,
                )
            ) or _summary_database_like(summary)
            row = {
                "name": name,
                "container_id": document.get("Id"),
                "pid": (document.get("State") or {}).get("Pid"),
                "command": command,
                "cpuset_cpus": cpuset_text,
                "cpuset_mems": str(host.get("CpusetMems") or ""),
                "overlap_cpus": _compress(overlap),
                "disk2_bind_sources": disk2_sources,
                "database_like": database_like,
                "intended_successor_container": name in intended_names,
                "inspect_status": "PASS",
                "inventory_source": "docker_unix_socket_api",
                "snapshot_lock": dict(lock_evidence),
            }
            inventory.append(row)
            if name in intended_names:
                continue
            if database_like and disk2_sources:
                blockers.append(
                    {**row, "reason": "co_resident_database_uses_target_disk2"}
                )
    return inventory, blockers
