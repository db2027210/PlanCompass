#!/usr/bin/env python3
"""Serialize XP Algorithm A reset+measurement calls on the shared nvme2n1."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import time
from typing import Any, Callable


LOCK_PATH = Path("/tmp/mysql_algorithm_a_nvme2_xp_formal_wave_v1.lock")
XP_SCENES = {"hybench_xp_cpu", "hybench_xp_io"}


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def install(base: Any) -> Callable[..., Any]:
    """Replace only the physical-measure call with a shared-device lease."""

    original = base.measure

    def serialized_measure(**kwargs: Any):
        workers = kwargs.get("workers") or {}
        scenes = {str(row.get("scene") or "") for row in workers.values()}
        if len(scenes) != 1 or not scenes.issubset(XP_SCENES):
            return original(**kwargs)
        scene = next(iter(scenes))
        stage_prefix = str(kwargs.get("stage_prefix") or "FORMAL_POINT")
        token = re.sub(r"[^A-Za-z0-9_.-]+", "_", stage_prefix)[:160]
        state_path = Path(kwargs["state_path"])
        lease_path = state_path.parent / "shared_storage_leases" / f"{token}.json"
        if lease_path.exists() or lease_path.is_symlink():
            raise FileExistsError(f"fresh shared-storage lease evidence required: {lease_path}")
        requested = time.monotonic()
        document = {
            "schema_version": "mysql_algorithm_a_shared_nvme_wave_lease_v1",
            "status": "WAITING",
            "requested_at": now(),
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "device": "/dev/nvme2n1",
            "mount": "/disk2",
            "lock_path": str(LOCK_PATH),
            "run_id": str(kwargs.get("run_id") or ""),
            "scene": scene,
            "stage_prefix": stage_prefix,
            "worker_ids": sorted(workers),
            "lease_covers_reset_and_measurement": True,
        }
        atomic(lease_path, document)
        state = base.alg.load(state_path)
        state["stage"] = f"{stage_prefix}_WAITING_SHARED_NVME_LEASE"
        state["shared_storage_lease"] = {
            "path": str(lease_path), "status": "WAITING",
        }
        base.atomic(state_path, state)
        LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOCK_PATH.open("w", encoding="ascii") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            acquired = time.monotonic()
            document.update({
                "status": "HELD",
                "acquired_at": now(),
                "wait_seconds": acquired - requested,
                "holder_pid": os.getpid(),
            })
            atomic(lease_path, document)
            state = base.alg.load(state_path)
            state["stage"] = f"{stage_prefix}_SHARED_NVME_LEASE_HELD"
            state["shared_storage_lease"] = {
                "path": str(lease_path), "status": "HELD",
            }
            base.atomic(state_path, state)
            try:
                result = original(**{**kwargs, "state": copy.deepcopy(state)})
            except BaseException as error:
                document.update({
                    "status": "RELEASED_AFTER_ERROR",
                    "released_at": now(),
                    "held_seconds": time.monotonic() - acquired,
                    "error": f"{type(error).__name__}: {error}",
                })
                atomic(lease_path, document)
                raise
            document.update({
                "status": "RELEASED",
                "released_at": now(),
                "held_seconds": time.monotonic() - acquired,
            })
            atomic(lease_path, document)
            return result

    base.measure = serialized_measure
    return original


__all__ = ["LOCK_PATH", "XP_SCENES", "install"]
