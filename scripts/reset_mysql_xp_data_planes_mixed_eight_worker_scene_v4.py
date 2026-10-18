#!/usr/bin/env python3
"""Scene-scoped XP8 reset with unlocked control and long storage bounds."""

from __future__ import annotations

from pathlib import Path
import sys
import threading
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import reset_mysql_xp_data_planes_mixed_eight_worker_v4 as predecessor


def install_for_scene(workers: Mapping[str, Mapping[str, Any]]) -> None:
    worker_ids = set(workers)
    expected = (
        {f"xp_cpu_w{i}" for i in range(1, 9)},
        {f"xp_io_w{i}" for i in range(1, 9)},
    )
    if not any(worker_ids == scene_ids for scene_ids in expected):
        raise RuntimeError("Algorithm A eight-worker scene reset set differs")
    allowlist = {
        worker_id: str(workers[worker_id]["container"])
        for worker_id in sorted(worker_ids)
    }
    base = predecessor.base
    values = {
        "AUTHORITY_VERSION": "xp_mixed_eight_worker_scene_a_v4_long_storage",
        "OUTPUT_SCHEMA": "mysql_xp_mixed_eight_worker_scene_a_long_storage_binding_v4",
        "XP_IDS": set(worker_ids),
        "XP_CONTAINER_ALLOWLIST": dict(allowlist),
    }
    base.install.__globals__.update(values)
    base._impl.update(values)
    base.install()
    base._impl.update({
        "DOCKER_CONTROL_CONCURRENCY": 8,
        "_DOCKER_CONTROL_SEMAPHORE": threading.BoundedSemaphore(8),
        "_docker_run": base._base_docker_run,
    })
    if base._impl["_docker_run"] is base._docker_run:
        raise RuntimeError("scene-local unlocked Docker control was not installed")
    globals().update(values)


def configure(*, freeze: Path, prepared: Path) -> None:
    predecessor.base.configure(freeze=freeze, prepared=prepared)
    globals().update({"FREEZE": freeze, "PREPARED": prepared})


def __getattr__(name: str) -> Any:
    try:
        return getattr(predecessor, name)
    except AttributeError:
        if name in predecessor.base._impl:
            return predecessor.base._impl[name]
        raise
