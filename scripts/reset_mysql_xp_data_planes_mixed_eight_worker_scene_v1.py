#!/usr/bin/env python3
"""Scene-scoped eight-worker reset facade for XP Algorithm A."""

from __future__ import annotations

from pathlib import Path
import fcntl
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import reset_mysql_xp_data_planes_mixed_eight_worker_v1 as predecessor


CONTROL_LOCK = Path("/tmp/mysql_xp8_docker_create_start_v2.lock")
_installed_original: Any = None


def _locked_docker_run(argv: list[str], *, timeout: int = 240):
    if list(argv[:2]) not in (["docker", "run"], ["docker", "start"]):
        return _installed_original(argv, timeout=timeout)
    descriptor = CONTROL_LOCK.open("a+", encoding="utf-8")
    try:
        fcntl.flock(descriptor.fileno(), fcntl.LOCK_EX)
        return _installed_original(argv, timeout=timeout)
    finally:
        fcntl.flock(descriptor.fileno(), fcntl.LOCK_UN)
        descriptor.close()


def install_for_scene(workers: Mapping[str, Mapping[str, Any]]) -> None:
    global _installed_original
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
        "AUTHORITY_VERSION": "xp_mixed_eight_worker_scene_a_v1",
        "OUTPUT_SCHEMA": "mysql_xp_mixed_eight_worker_scene_a_reprepared_binding_v1",
        "XP_IDS": set(worker_ids),
        "XP_CONTAINER_ALLOWLIST": dict(allowlist),
    }
    base.install.__globals__.update(values)
    base._impl.update(values)
    base.install()
    _installed_original = base._impl["_docker_run"]
    base._impl["_docker_run"] = _locked_docker_run
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
