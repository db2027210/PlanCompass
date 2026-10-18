#!/usr/bin/env python3
"""Two-worker-scoped XP reset with verified zero anonymous swapping."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from scripts import reset_mysql_xp_data_planes_v7 as predecessor


_original_docker_run: Any = None


def _zero_swappiness_docker_run(
    argv: list[str], *, timeout: int = 240,
):
    effective = list(argv)
    if effective[:2] == ["docker", "run"] and "--memory-swap" in effective:
        if "--memory-swappiness" in effective:
            raise RuntimeError("duplicate Docker memory-swappiness option")
        index = effective.index("--memory-swap")
        effective[index + 2:index + 2] = ["--memory-swappiness", "0"]
    return _original_docker_run(effective, timeout=timeout)


def configure(
    *, freeze: Path, prepared: Path,
    xp_workers: Mapping[str, Mapping[str, Any]],
) -> None:
    global _original_docker_run
    worker_ids = set(xp_workers)
    expected = {"xp_cpu_w1", "xp_cpu_w2", "xp_io_w1", "xp_io_w2"}
    if worker_ids != expected:
        raise RuntimeError("client-primary XP reset worker cohort differs")
    implementation = predecessor._v6
    implementation.FREEZE = freeze
    implementation.PREPARED = prepared
    implementation.RESET_SAME_FREEZE = True
    implementation.XP_IDS = set(expected)
    implementation.XP_CONTAINER_ALLOWLIST = {
        worker_id: str(row["container"])
        for worker_id, row in xp_workers.items()
    }
    _original_docker_run = implementation._docker_run
    if _original_docker_run is _zero_swappiness_docker_run:
        raise RuntimeError("client-primary XP reset recursively installed")
    implementation._docker_run = _zero_swappiness_docker_run
    implementation.AUTHORITY_VERSION = "client_primary_two_worker_v1"
    implementation.OUTPUT_SCHEMA = "mysql_client_primary_xp_reset_v1"


def __getattr__(name: str) -> Any:
    return getattr(predecessor, name)


for _name in dir(predecessor):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(predecessor, _name))
