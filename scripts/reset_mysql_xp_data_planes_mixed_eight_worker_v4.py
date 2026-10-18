#!/usr/bin/env python3
"""XP8 mixed-SSD reset with unlocked control and long storage bounds."""

from __future__ import annotations

from pathlib import Path
import sys
import threading
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import reset_mysql_xp_data_planes_mixed_four_worker_v3 as base
from scripts.reset_mysql_xp_data_planes_mixed_four_worker_v3 import *  # noqa: F401,F403


AUTHORITY_VERSION = "xp_mixed_eight_worker_v4_long_storage_unlocked"
OUTPUT_SCHEMA = "mysql_xp_mixed_eight_worker_long_storage_binding_v4"
XP_IDS = {
    *(f"xp_cpu_w{i}" for i in range(1, 9)),
    *(f"xp_io_w{i}" for i in range(1, 9)),
}


def install_for_workers(workers: Mapping[str, Mapping[str, Any]]) -> None:
    worker_ids = set(workers)
    if worker_ids != XP_IDS:
        raise RuntimeError("mixed eight-worker reset worker set differs")
    allowlist = {
        worker_id: str(workers[worker_id]["container"])
        for worker_id in sorted(worker_ids)
    }
    base.AUTHORITY_VERSION = AUTHORITY_VERSION
    base.OUTPUT_SCHEMA = OUTPUT_SCHEMA
    base.XP_IDS = set(worker_ids)
    base.XP_CONTAINER_ALLOWLIST = dict(allowlist)
    base.install.__globals__.update({
        "AUTHORITY_VERSION": AUTHORITY_VERSION,
        "OUTPUT_SCHEMA": OUTPUT_SCHEMA,
        "XP_IDS": set(worker_ids),
        "XP_CONTAINER_ALLOWLIST": dict(allowlist),
    })
    base._impl.update({
        "AUTHORITY_VERSION": AUTHORITY_VERSION,
        "OUTPUT_SCHEMA": OUTPUT_SCHEMA,
        "XP_IDS": set(worker_ids),
        "XP_CONTAINER_ALLOWLIST": dict(allowlist),
    })
    base.install()
    base._impl.update({
        "DOCKER_CONTROL_CONCURRENCY": 8,
        "_DOCKER_CONTROL_SEMAPHORE": threading.BoundedSemaphore(8),
        "_docker_run": base._base_docker_run,
    })
    if base._impl["_docker_run"] is base._docker_run:
        raise RuntimeError("XP8 v4 unlocked Docker control was not installed")
    globals().update({
        "XP_IDS": set(worker_ids),
        "XP_CONTAINER_ALLOWLIST": dict(allowlist),
        "_prepare_fresh_xp": base._impl["_prepare_fresh_xp"],
    })


def configure(*, freeze: Path, prepared: Path) -> None:
    base.configure(freeze=freeze, prepared=prepared)
    globals().update({"FREEZE": freeze, "PREPARED": prepared})


def prepare_fresh_worker(
    *, freeze: dict[str, Any], worker: dict[str, Any], prior: dict[str, Any],
    quarantine_root: Path, output_root: Path, reset_context: str,
    resume_stalled_copy: bool = False,
) -> dict[str, Any]:
    return base._impl["_prepare_fresh_xp"](
        freeze, worker, prior, quarantine_root, output_root, resume_stalled_copy,
        reset_context=reset_context,
    )


def __getattr__(name: str) -> Any:
    """Delegate private reset primitives required by the sealed runtime."""
    try:
        return getattr(base, name)
    except AttributeError:
        if name in base._impl:
            return base._impl[name]
        raise
