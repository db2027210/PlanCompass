#!/usr/bin/env python3
"""XP8 reset v2 with a short global Docker create/start control lock."""

from __future__ import annotations

import fcntl
from pathlib import Path
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


def install_for_workers(workers: Mapping[str, Mapping[str, Any]]) -> None:
    global _installed_original
    predecessor.install_for_workers(workers)
    _installed_original = predecessor.base._impl["_docker_run"]
    if _installed_original is _locked_docker_run:
        raise RuntimeError("XP8 Docker control lock recursively installed")
    predecessor.base._impl["_docker_run"] = _locked_docker_run


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
