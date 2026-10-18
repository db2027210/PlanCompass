#!/usr/bin/env python3
"""XP8 reset with eight-way scene-local Docker control and no start lock."""

from __future__ import annotations

from pathlib import Path
import sys
import threading
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import reset_mysql_xp_data_planes_mixed_eight_worker_v1 as predecessor


def _install_unlocked_control() -> None:
    base = predecessor.base
    # mixed_four_worker_v1 installs a historical process-wide flock wrapper.
    # The full-parallel successor explicitly restores the v6 Docker primitive
    # and gives each scene all eight reset-control slots.
    base._impl.update({
        "DOCKER_CONTROL_CONCURRENCY": 8,
        "_DOCKER_CONTROL_SEMAPHORE": threading.BoundedSemaphore(8),
        "_docker_run": base._base_docker_run,
    })
    if base._impl["_docker_run"] is base._docker_run:
        raise RuntimeError("XP8 unlocked Docker control was not installed")


def install_for_workers(workers: Mapping[str, Mapping[str, Any]]) -> None:
    predecessor.install_for_workers(workers)
    _install_unlocked_control()


def configure(*, freeze: Path, prepared: Path) -> None:
    predecessor.configure(freeze=freeze, prepared=prepared)


def prepare_fresh_worker(**kwargs: Any):
    return predecessor.prepare_fresh_worker(**kwargs)


def __getattr__(name: str) -> Any:
    try:
        return getattr(predecessor, name)
    except AttributeError:
        if name in predecessor.base._impl:
            return predecessor.base._impl[name]
        raise

