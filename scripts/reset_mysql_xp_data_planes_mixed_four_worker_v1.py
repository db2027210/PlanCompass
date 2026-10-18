#!/usr/bin/env python3
"""XP point-reset authority for four workers split across disk1/disk2."""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import sys
import threading
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PREDECESSOR = ROOT / "scripts/reset_mysql_xp_data_planes_v6.py"
_source = PREDECESSOR.read_text(encoding="utf-8")
_old_disk_guard = 'not str(quarantine).startswith("/disk2/")'
_new_disk_guard = 'not str(quarantine).startswith(("/disk1/", "/disk2/"))'
if _source.count(_old_disk_guard) != 1:
    raise RuntimeError("mixed reset predecessor disk guard differs")
_source = _source.replace(_old_disk_guard, _new_disk_guard)
_impl: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._reset_mysql_xp_data_planes_mixed_four_worker_v1_impl",
    "__package__": "scripts",
}
exec(compile(_source, str(Path(__file__).resolve()), "exec"), _impl)

for _name, _value in _impl.items():
    if not _name.startswith("__"):
        globals()[_name] = _value

AUTHORITY_VERSION = "xp_mixed_four_worker_v1"
OUTPUT_SCHEMA = "mysql_mixed_disk_xp_four_worker_reprepared_binding_v1"
XP_IDS = {
    "xp_cpu_w1", "xp_cpu_w2", "xp_cpu_w3", "xp_cpu_w4",
    "xp_io_w1", "xp_io_w2", "xp_io_w3", "xp_io_w4",
}
XP_CONTAINER_ALLOWLIST = {
    "xp_cpu_w1": "ra_mysql_disk2_algc_xp_cpu_w1_20260809",
    "xp_cpu_w2": "ra_mysql_disk2_algc_xp_cpu_w2_20260809",
    "xp_cpu_w3": "ra_pgssd_mysql_baseline_xp_cpu_w1_r2_20260826",
    "xp_cpu_w4": "ra_pgssd_mysql_baseline_xp_cpu_w2_20260826",
    "xp_io_w1": "ra_mysql_disk2_algc_xp_io_w1_20260809",
    "xp_io_w2": "ra_mysql_disk2_algc_xp_io_w2_20260809",
    "xp_io_w3": "ra_pgssd_mysql_baseline_xp_io_w1_20260826",
    "xp_io_w4": "ra_pgssd_mysql_baseline_xp_io_w2_20260826",
}
_base_purge = _impl["purge_completed_discard_quarantine"]
_base_docker_run = _impl["_docker_run"]
_docker_start_lock = Path("/tmp/mysql_xp_mixed_four_worker_docker_start_v1.lock")


def _docker_run(argv: list[str], *, timeout: int = 240):
    if list(argv[:2]) != ["docker", "start"]:
        return _base_docker_run(argv, timeout=timeout)
    descriptor = _docker_start_lock.open("a+", encoding="utf-8")
    try:
        fcntl.flock(descriptor.fileno(), fcntl.LOCK_EX)
        return _base_docker_run(argv, timeout=timeout)
    finally:
        fcntl.flock(descriptor.fileno(), fcntl.LOCK_UN)
        descriptor.close()


def purge_completed_discard_quarantine(
    *, quarantine_root: Path,
    reset_workers: Mapping[str, Mapping[str, Any]],
    binding_path: Path,
    cleanup_path: Path,
) -> dict[str, Any]:
    """Clean each SSD's exact quarantine root and seal an aggregate audit."""
    groups: dict[Path, dict[str, Mapping[str, Any]]] = {}
    for worker_id, row in reset_workers.items():
        target = Path(str(row.get("quarantined_invalid_runtime_root") or ""))
        if not target.is_dir() or target.is_symlink():
            raise RuntimeError(f"{worker_id} mixed quarantine target differs")
        groups.setdefault(target.parent.resolve(), {})[worker_id] = row
    if not groups:
        raise RuntimeError("mixed quarantine cleanup worker set is empty")
    documents: list[dict[str, Any]] = []
    for index, (root, rows) in enumerate(sorted(groups.items(), key=lambda item: str(item[0])), 1):
        group_cleanup = cleanup_path.with_name(
            f"{cleanup_path.stem}.storage_{index}{cleanup_path.suffix}"
        )
        documents.append(_base_purge(
            quarantine_root=root, reset_workers=rows,
            binding_path=binding_path, cleanup_path=group_cleanup,
        ))
    document = {
        "schema_version": "mysql_mixed_disk_completed_reset_quarantine_cleanup_v1",
        "status": "PASS", "created_at": _impl["_now"](),
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "requested_quarantine_root": str(quarantine_root),
        "reset_binding_path": str(binding_path.resolve()),
        "reset_binding_sha256": _impl["_sha"](binding_path),
        "storage_group_count": len(documents), "storage_groups": documents,
        "deleted_worker_count": sum(int(row["deleted_worker_count"]) for row in documents),
        "deleted_bytes": sum(int(row["deleted_bytes"]) for row in documents),
        "all_targets_absent": True, "live_mounts_deleted": False,
        "sealed_templates_deleted": False,
    }
    temporary = cleanup_path.with_name(f".{cleanup_path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, cleanup_path)
    cleanup_path.with_name(cleanup_path.name + ".sha256").write_text(
        f"{_impl['_sha'](cleanup_path)}  {cleanup_path.name}\n", encoding="ascii",
    )
    return document


def configure(*, freeze: Path, prepared: Path) -> None:
    _impl["FREEZE"] = freeze
    _impl["PREPARED"] = prepared
    _impl["RESET_SAME_FREEZE"] = True


def install() -> None:
    _impl.update({
        "AUTHORITY_VERSION": AUTHORITY_VERSION,
        "OUTPUT_SCHEMA": OUTPUT_SCHEMA,
        "XP_IDS": set(XP_IDS),
        "XP_CONTAINER_ALLOWLIST": dict(XP_CONTAINER_ALLOWLIST),
        "HOST_PID_KILL_EXIT_WAIT_SECONDS": 240,
        "DOCKER_CONTROL_CONCURRENCY": 4,
        "_DOCKER_CONTROL_SEMAPHORE": threading.BoundedSemaphore(4),
        "_docker_run": _docker_run,
        "purge_completed_discard_quarantine": purge_completed_discard_quarantine,
    })
    globals().update({
        "_prepare_fresh_xp": _impl["_prepare_fresh_xp"],
        "prep": _impl["prep"],
    })


install()
