#!/usr/bin/env python3
"""Container-inventory successor tolerant of unrelated inspect timeouts."""

from __future__ import annotations

import json
import re
import subprocess
from typing import Any

from scripts import preflight_mysql_four_scene_v3 as predecessor


_process_inventory = predecessor._process_inventory
_cpus = predecessor._cpus
_compress = predecessor._compress


def _summary_database_like(summary: dict[str, Any]) -> bool:
    text = " ".join(
        str(summary.get(name) or "")
        for name in ("Names", "Image", "Command")
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
    completed = subprocess.run(
        ["docker", "ps", "--no-trunc", "--format", "{{json .}}"],
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    summaries = [
        json.loads(line) for line in completed.stdout.splitlines() if line.strip()
    ]
    inventory: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    for summary in summaries:
        name = str(summary.get("Names") or "")
        try:
            inspected = subprocess.run(
                ["docker", "inspect", name],
                text=True,
                capture_output=True,
                check=True,
                timeout=15,
            )
            document = json.loads(inspected.stdout)[0]
        except (
            subprocess.TimeoutExpired,
            subprocess.CalledProcessError,
            json.JSONDecodeError,
            IndexError,
        ) as error:
            database_like = _summary_database_like(summary)
            row = {
                "name": name,
                "container_id": summary.get("ID"),
                "pid": None,
                "command": str(summary.get("Command") or ""),
                "image": str(summary.get("Image") or ""),
                "cpuset_cpus": None,
                "cpuset_mems": None,
                "overlap_cpus": "",
                "disk2_bind_sources": [],
                "docker_ps_mount_summary": str(summary.get("Mounts") or ""),
                "database_like": database_like,
                "intended_successor_container": name in intended_names,
                "inspect_status": "TIMEOUT_OR_TRANSIENT_ERROR_SUMMARY_RECORDED",
                "inspect_error_type": type(error).__name__,
                "summary_state": summary.get("State"),
                "summary_status": summary.get("Status"),
            }
            inventory.append(row)
            if name in intended_names or database_like:
                blockers.append({
                    **row,
                    "reason": "database_or_intended_container_inspect_failed_closed",
                })
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
            [str(document.get("Path") or ""), *map(str, document.get("Args") or [])]
        ).strip()
        database_like = bool(
            re.search(
                r"(?:mysqld|postgres|gaussdb|mongod|redis-server)", command, re.I
            )
        )
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
        }
        inventory.append(row)
        if name in intended_names:
            continue
        if database_like and disk2_sources:
            blockers.append({
                **row, "reason": "co_resident_database_uses_target_disk2"
            })
    return inventory, blockers
