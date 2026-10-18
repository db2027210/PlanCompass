#!/usr/bin/env python3
"""Shared prestart authority helpers for independent MySQL scene controllers."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def kernel_lock_held_by(
    pid: int,
    lock_path: Path = Path("/tmp/resource_adaptive_pg_ssd_measurement_v1.lock"),
) -> bool:
    """Accept either a read or write flock held by ``pid`` on ``lock_path``."""

    try:
        inode = int(lock_path.stat().st_ino)
        lines = Path("/proc/locks").read_text(encoding="ascii").splitlines()
    except OSError:
        return False
    for line in lines:
        fields = line.split()
        if len(fields) < 6 or fields[1] != "FLOCK" or fields[3] not in {"READ", "WRITE"}:
            continue
        try:
            holder = int(fields[4])
            locked_inode = int(fields[5].rsplit(":", 1)[-1])
        except (ValueError, IndexError):
            continue
        if holder == int(pid) and locked_inode == inode:
            return True
    return False


def install_shared_lock_verifier(coordination: Any) -> None:
    """Let the point-authority reader verify an intentional shared lease."""

    def verifier(pid: int, lock_path: Path = coordination.LOCK_PATH) -> bool:
        return kernel_lock_held_by(pid, lock_path)

    coordination.lock_held_exclusively_by = verifier


def validate_under_shared_lease(
    *, coordination: Any, r0mod: Any, contract_path: Path,
    timeout_seconds: float, scene: str,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, Any]]:
    """Run the existing point readback while sharing with sibling MySQL scenes."""

    install_shared_lock_verifier(coordination)
    lease = coordination.acquire_shared_blocking(timeout_seconds=timeout_seconds)
    try:
        r0mod.COORDINATED_LEASE_HOLDER_PID = __import__("os").getpid()
        r0mod.COORDINATION_CONTRACT = contract_path
        r0mod.AUTHORITY_SCENE_SCOPE = scene
        freeze, workers, point_gate = r0mod._validate_authority()
        point_gate["prestart_lease_mode"] = "shared_mysql_scene"
        point_gate["sibling_mysql_scene_wait_required"] = False
        return freeze, workers, point_gate
    finally:
        r0mod.COORDINATED_LEASE_HOLDER_PID = None
        r0mod.COORDINATION_CONTRACT = None
        r0mod.AUTHORITY_SCENE_SCOPE = None
        coordination.release(lease)
