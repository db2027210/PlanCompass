#!/usr/bin/env python3
"""XP8 reset with two scene-local Docker control calls and eight-way replay."""

from __future__ import annotations

import threading
from typing import Any, Mapping

from scripts import reset_mysql_xp_data_planes_mixed_eight_worker_scene_v5 as predecessor


DOCKER_CONTROL_CONCURRENCY = 2


def install_for_scene(workers: Mapping[str, Mapping[str, Any]]) -> None:
    predecessor.install_for_scene(workers)
    base = predecessor.predecessor.predecessor.base
    values = {
        "AUTHORITY_VERSION": "xp_mixed_eight_worker_scene_a_v6_control2",
        "OUTPUT_SCHEMA": "mysql_xp_mixed_eight_worker_scene_a_control2_binding_v6",
        "DOCKER_CONTROL_CONCURRENCY": DOCKER_CONTROL_CONCURRENCY,
        "_DOCKER_CONTROL_SEMAPHORE": threading.BoundedSemaphore(
            DOCKER_CONTROL_CONCURRENCY
        ),
    }
    base._impl.update(values)
    globals().update(values)


def configure(**kwargs: Any) -> None:
    predecessor.configure(**kwargs)


def __getattr__(name: str) -> Any:
    return getattr(predecessor, name)
