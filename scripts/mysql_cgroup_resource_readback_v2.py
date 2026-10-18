#!/usr/bin/env python3
"""Page-aware kernel cgroup resource read-back for frozen MySQL workers."""

from __future__ import annotations

import os
from typing import Any

from scripts import prepare_mysql_four_scene_v4 as predecessor


def _page_aligned_memory_limit(requested_bytes: int, page_size: int) -> int:
    if requested_bytes <= 0 or page_size <= 0:
        raise ValueError("memory limit and page size must be positive")
    return requested_bytes - requested_bytes % page_size


def _memory_limit_matches(
    actual_bytes: int,
    requested_bytes: int,
    *,
    page_size: int | None = None,
) -> bool:
    size = int(page_size or os.sysconf("SC_PAGE_SIZE"))
    return actual_bytes == _page_aligned_memory_limit(requested_bytes, size)


def kernel_resource_readback(
    inspect: dict[str, Any], worker: dict[str, Any]
) -> dict[str, Any]:
    """Validate effective cgroups, accepting only kernel page rounding for memory."""
    container_id = str(inspect["Id"])
    pid = int((inspect.get("State") or {}).get("Pid") or 0)
    cpuset = predecessor._controller_path(pid, container_id, "cpuset", "cpuset")
    cpu = predecessor._controller_path(pid, container_id, "cpu", "cpu,cpuacct")
    memory = predecessor._controller_path(pid, container_id, "memory", "memory")
    pids = predecessor._controller_path(pid, container_id, "pids", "pids")
    values: dict[str, Any] = {
        "cpuset_cpus": (cpuset / "cpuset.cpus").read_text().strip(),
        "cpuset_mems": (cpuset / "cpuset.mems").read_text().strip(),
        "cpu_quota_us": int((cpu / "cpu.cfs_quota_us").read_text()),
        "cpu_period_us": int((cpu / "cpu.cfs_period_us").read_text()),
        "memory_limit_bytes": int((memory / "memory.limit_in_bytes").read_text()),
        "pids_max": int((pids / "pids.max").read_text()),
    }
    expected_quota = int(
        round(float(worker["cpu_quota_cores"]) * values["cpu_period_us"])
    )
    requested_memory = int(worker["memory_bytes"])
    expected = {
        "cpuset_cpus": str(worker["database_cpuset"]),
        "cpuset_mems": str(worker["database_cpuset_mems"]),
        "cpu_quota_us": expected_quota,
        "pids_max": int(worker["pids_max"]),
    }
    differences = {
        key: {"actual": values[key], "expected": value}
        for key, value in expected.items()
        if values[key] != value
    }
    if not _memory_limit_matches(values["memory_limit_bytes"], requested_memory):
        differences["memory_limit_bytes"] = {
            "actual": values["memory_limit_bytes"],
            "requested": requested_memory,
            "expected_page_aligned": _page_aligned_memory_limit(
                requested_memory, int(os.sysconf("SC_PAGE_SIZE"))
            ),
        }
    if differences:
        raise RuntimeError(
            f"{worker['worker_id']} kernel cgroup resource differs: {differences}"
        )
    return values

