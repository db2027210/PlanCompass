#!/usr/bin/env python3
"""Serialize full JOB and TPC-H Algorithm A waves on shared nvme2n1."""

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


ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = Path("/tmp/mysql_algorithm_a_nvme2_memory_formal_wave_v1.lock")
MEMORY_SCENES = {"job_memory", "tpch_sf4_memory"}
CONTRACT = (
    ROOT / "config/formal/frozen_mysql_algorithm_a_memory_nvme_wave_20260823_v1"
    / "memory_wave_contract.json"
)


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


def validate_contract() -> dict[str, Any]:
    if CONTRACT.is_symlink() or not CONTRACT.is_file():
        raise RuntimeError("memory shared-NVMe contract is absent")
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    if (
        not isinstance(contract, dict)
        or contract.get("schema_version")
        != "mysql_algorithm_a_memory_shared_nvme_wave_contract_v1"
        or contract.get("status") != "FROZEN"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("algorithm") != "A"
        or contract.get("scenes") != sorted(MEMORY_SCENES)
        or contract.get("shared_device") != "/dev/nvme2n1"
        or contract.get("physical_wave_mutual_exclusion") is not True
        or contract.get("per_scene_outer_state_machine_unchanged") is not True
        or contract.get("candidate_and_acceptance_policy_unchanged") is not True
        or contract.get("cached_valid_prefix_reused") is not True
        or contract.get("wall_time_limit_seconds") != 900
    ):
        raise RuntimeError("memory shared-NVMe contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        source = ROOT / str(binding.get("path") or "")
        if (
            source.is_symlink() or not source.is_file()
            or sha(source) != binding.get("sha256")
        ):
            raise RuntimeError(f"memory shared-NVMe source differs: {source}")
    return contract


def install(base: Any) -> Callable[..., Any]:
    """Wrap only reset+physical measurement; outer loops stay independent."""

    validate_contract()
    original = base.measure

    def serialized_measure(**kwargs: Any):
        workers = kwargs.get("workers") or {}
        scenes = {str(row.get("scene") or "") for row in workers.values()}
        if len(scenes) != 1 or not scenes.issubset(MEMORY_SCENES):
            return original(**kwargs)
        scene = next(iter(scenes))
        stage_prefix = str(kwargs.get("stage_prefix") or "FORMAL_POINT")
        token = re.sub(r"[^A-Za-z0-9_.-]+", "_", stage_prefix)[:160]
        state_path = Path(kwargs["state_path"])
        lease_path = state_path.parent / "shared_storage_leases" / f"{token}.json"
        if lease_path.exists() or lease_path.is_symlink():
            raise FileExistsError(
                f"fresh shared-storage lease evidence required: {lease_path}"
            )
        requested = time.monotonic()
        document = {
            "schema_version": "mysql_algorithm_a_shared_nvme_memory_wave_lease_v1",
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
            "lease_covers_reset_cache_precondition_and_measurement": True,
            "outer_loop_or_candidate_policy_changed": False,
        }
        atomic(lease_path, document)
        state = base.alg.load(state_path)
        state["stage"] = f"{stage_prefix}_WAITING_SHARED_NVME_MEMORY_LEASE"
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
            state["stage"] = f"{stage_prefix}_SHARED_NVME_MEMORY_LEASE_HELD"
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


__all__ = ["LOCK_PATH", "MEMORY_SCENES", "install", "validate_contract"]
