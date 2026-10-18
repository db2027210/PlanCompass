#!/usr/bin/env python3
"""Preserve frozen XP swappiness before launch and after every restart."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
from typing import Any

from scripts import mysql_algorithm_c_reset_affinity_v2 as predecessor


RELEASE = "v227-xp-prestart-and-reset-swappiness-readback"


def _live_container_id(container: str) -> str:
    document = json.loads(subprocess.run(
        ["docker", "inspect", container], check=True, capture_output=True,
        text=True, timeout=30,
    ).stdout)[0]
    if (document.get("State") or {}).get("Running") is not True:
        raise RuntimeError(f"XP reset container is not running: {container}")
    return str(document.get("Id") or "")


def _enforce_swappiness(worker: dict[str, Any]) -> dict[str, Any]:
    expected = int((worker.get("effective_resources") or {}).get(
        "MemorySwappiness", worker.get("memory_swappiness", 0)
    ))
    if expected != 0:
        raise RuntimeError("formal XP worker must freeze memory swappiness at zero")
    container = str(worker["container"])
    live_id = _live_container_id(container)
    authority_id = str(worker.get("container_id") or "")
    if not authority_id or live_id != authority_id:
        raise RuntimeError("post-reset XP container identity differs from authority")
    path = Path("/sys/fs/cgroup/memory/docker-limited") / live_id / "memory.swappiness"
    before = int(path.read_text(encoding="ascii").strip())
    if before != expected:
        subprocess.run(
            [
                "taskset", "-c", str(worker["database_cpuset"]),
                "sudo", "-n", "tee", str(path),
            ],
            input=f"{expected}\n", text=True, capture_output=True,
            check=True, timeout=15,
        )
    after = int(path.read_text(encoding="ascii").strip())
    if after != expected:
        raise RuntimeError("post-reset XP swappiness readback differs")
    return {
        "release_version": RELEASE,
        "container": container,
        "container_id": live_id,
        "cgroup_path": str(path),
        "before": before,
        "expected": expected,
        "after": after,
        "reapplied": before != expected,
        "readback_passed": True,
    }


def prepare_fresh_xp(*args: Any, **kwargs: Any) -> dict[str, Any]:
    if len(args) < 2 or not isinstance(args[1], dict):
        raise RuntimeError("XP reset worker argument differs")
    worker = args[1]
    result = predecessor.prepare_fresh_xp(*args, **kwargs)
    if not isinstance(result, dict):
        raise RuntimeError("XP reset result differs")
    document = copy.deepcopy(result)
    document["post_restart_swappiness_readback"] = _enforce_swappiness(worker)
    return document


def enforce_prestart_swappiness(
    prepared_binding: Path, scene: str = "hybench_xp_cpu"
) -> dict[str, Any]:
    binding = json.loads(prepared_binding.read_text(encoding="utf-8"))
    workers = [
        worker for worker in binding.get("workers", [])
        if worker.get("scene") == scene
    ]
    if len(workers) != 2:
        raise RuntimeError("formal XP prestart worker authority differs")
    rows = {
        str(worker["worker_id"]): _enforce_swappiness(worker)
        for worker in workers
    }
    if any(row.get("readback_passed") is not True for row in rows.values()):
        raise RuntimeError("formal XP prestart swappiness readback differs")
    return {
        "release_version": RELEASE,
        "scene": scene,
        "worker_count": len(rows),
        "workers": rows,
        "all_readbacks_passed": True,
    }


__all__ = [
    "RELEASE", "enforce_prestart_swappiness", "prepare_fresh_xp"
]
