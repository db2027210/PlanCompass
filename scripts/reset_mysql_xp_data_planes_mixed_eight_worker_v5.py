#!/usr/bin/env python3
"""XP8 unlocked reset with long storage and Docker-control bounds."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import reset_mysql_xp_data_planes_mixed_eight_worker_v4 as predecessor


DOCKER_CONTROL_TIMEOUT_SECONDS = 900


def install_for_workers(workers: Mapping[str, Mapping[str, Any]]) -> None:
    predecessor.install_for_workers(workers)
    base = predecessor.base
    original = base._base_docker_run

    def long_docker_run(argv: list[str], *, timeout: int = 240):
        return original(
            argv,
            timeout=max(int(timeout), DOCKER_CONTROL_TIMEOUT_SECONDS),
        )

    base._impl["_docker_run"] = long_docker_run
    globals().update({
        "XP_IDS": set(predecessor.XP_IDS),
        "XP_CONTAINER_ALLOWLIST": dict(predecessor.XP_CONTAINER_ALLOWLIST),
        "_prepare_fresh_xp": base._impl["_prepare_fresh_xp"],
    })


def configure(*, freeze: Path, prepared: Path) -> None:
    predecessor.configure(freeze=freeze, prepared=prepared)
    globals().update({"FREEZE": freeze, "PREPARED": prepared})


def prepare_fresh_worker(**kwargs: Any):
    return predecessor.prepare_fresh_worker(**kwargs)


def __getattr__(name: str) -> Any:
    try:
        return getattr(predecessor, name)
    except AttributeError:
        if name in predecessor.base._impl:
            return predecessor.base._impl[name]
        raise
