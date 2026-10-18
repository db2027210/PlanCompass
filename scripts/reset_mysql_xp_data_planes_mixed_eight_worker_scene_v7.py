#!/usr/bin/env python3
"""XP reset with container-scoped anonymous swap disabled."""

from __future__ import annotations

from typing import Any, Mapping

from scripts import reset_mysql_xp_data_planes_mixed_eight_worker_scene_v6 as predecessor


_installed_original: Any = None


def _zero_swappiness_docker_run(
    argv: list[str], *, timeout: int = 240,
):
    effective = list(argv)
    if effective[:2] == ["docker", "run"]:
        if "--memory-swappiness" in effective:
            raise RuntimeError("duplicate Docker memory-swappiness option")
        memory_swap_index = effective.index("--memory-swap")
        effective[memory_swap_index + 2:memory_swap_index + 2] = [
            "--memory-swappiness", "0",
        ]
    return _installed_original(effective, timeout=timeout)


def install_for_scene(workers: Mapping[str, Mapping[str, Any]]) -> None:
    global _installed_original
    predecessor.install_for_scene(workers)
    base = predecessor.predecessor.predecessor.predecessor.base
    _installed_original = base._impl["_docker_run"]
    if _installed_original is _zero_swappiness_docker_run:
        raise RuntimeError("zero-swappiness Docker adapter recursively installed")
    base._impl["_docker_run"] = _zero_swappiness_docker_run
    globals().update({
        "AUTHORITY_VERSION": "xp_mixed_eight_worker_scene_v7_zero_swappiness",
        "OUTPUT_SCHEMA": "mysql_xp_mixed_eight_worker_scene_zero_swappiness_v7",
    })


def configure(**kwargs: Any) -> None:
    predecessor.configure(**kwargs)


def __getattr__(name: str) -> Any:
    return getattr(predecessor, name)
