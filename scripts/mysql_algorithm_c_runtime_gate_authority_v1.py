#!/usr/bin/env python3
"""Write immutable gate authorities from verified XP reset results."""

from __future__ import annotations

import copy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping


RELEASE = "v235-xp-reset-result-runtime-gate-authority"
SCENE = "hybench_xp_cpu"
WORKERS = {"xp_cpu_w1", "xp_cpu_w2"}
ENVELOPE_FIELDS = (
    "scene", "worker_id", "container", "host_port", "database",
    "database_cpuset", "database_cpuset_mems", "client_cpuset",
    "client_cpuset_mems", "cpu_quota_cores", "memory_bytes", "swap_bytes",
    "memory_swappiness", "pids_max", "io_read_bps", "io_write_bps",
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def _rows(document: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    rows = {
        str(row.get("worker_id") or ""): copy.deepcopy(dict(row))
        for row in document.get("workers") or []
        if isinstance(row, Mapping)
    }
    if len(rows) != 8 or not WORKERS.issubset(rows):
        raise RuntimeError("runtime gate authority worker set differs")
    return rows


def _validate_target(previous: Mapping[str, Any], current: Mapping[str, Any]) -> None:
    worker_id = str(current.get("worker_id") or "")
    if worker_id not in WORKERS:
        raise RuntimeError(f"unexpected runtime gate worker: {worker_id}")
    for field in ENVELOPE_FIELDS:
        if current.get(field) != previous.get(field):
            raise RuntimeError(f"{worker_id} runtime gate envelope differs: {field}")
    container_id = str(current.get("container_id") or "")
    swappiness = current.get("post_restart_swappiness_readback") or {}
    blkio = current.get("blkio_authority") or {}
    if (
        re.fullmatch(r"[0-9a-f]{64}", container_id) is None
        or current.get("ready") is not True
        or int(current.get("memory_swappiness", -1)) != 0
        or swappiness.get("container_id") != container_id
        or swappiness.get("readback_passed") is not True
        or container_id not in str(blkio.get("cgroup_path") or "")
    ):
        raise RuntimeError(f"{worker_id} reset-result identity proof differs")


class RuntimeGateAuthority:
    """Append-only identity authority used by the kernel-only gate."""

    def __init__(self, root: Path, source_prepared: Path):
        self.root = root.resolve()
        self.source_prepared = source_prepared.resolve()
        if self.root.exists() or self.root.is_symlink():
            raise RuntimeError("fresh runtime gate authority root required")
        source = load(self.source_prepared)
        if source.get("status") != "PREPARED":
            raise RuntimeError("sealed prepared authority differs")
        rows = _rows(source)
        if {row.get("scene") for key, row in rows.items() if key in WORKERS} != {SCENE}:
            raise RuntimeError("sealed XP/CPU authority scene differs")
        self.root.mkdir(parents=True, mode=0o700)
        self.generation = 0
        self.current_path = self._write_generation(
            source,
            reset_binding=None,
            predecessor=None,
        )

    def _write_generation(
        self,
        document: Mapping[str, Any],
        *,
        reset_binding: Path | None,
        predecessor: Path | None,
    ) -> Path:
        directory = self.root / f"generation_{self.generation:04d}"
        if directory.exists() or directory.is_symlink():
            raise RuntimeError("fresh runtime gate generation required")
        directory.mkdir(mode=0o700)
        value = copy.deepcopy(dict(document))
        value["runtime_gate_identity_release"] = {
            "release_version": RELEASE,
            "generation": self.generation,
            "created_at": datetime.now().astimezone().isoformat(),
            "source_prepared_path": str(self.source_prepared),
            "source_prepared_sha256": sha(self.source_prepared),
            "predecessor_path": str(predecessor) if predecessor else None,
            "predecessor_sha256": sha(predecessor) if predecessor else None,
            "reset_binding_path": str(reset_binding) if reset_binding else None,
            "reset_binding_sha256": sha(reset_binding) if reset_binding else None,
            "immutable_generation": True,
            "resource_envelope_changed": False,
            "workload_or_candidate_policy_changed": False,
        }
        path = directory / "prepared_binding.json"
        atomic(path, value)
        return path

    def refresh(
        self,
        workers: Mapping[str, Mapping[str, Any]],
        reset_binding: Path,
    ) -> Path:
        reset_binding = reset_binding.resolve()
        reset = load(reset_binding)
        reset_rows = {
            str(row.get("worker_id") or ""): dict(row)
            for row in reset.get("workers") or []
            if isinstance(row, Mapping)
        }
        if (
            reset.get("status") != "PASS"
            or not WORKERS.issubset(reset_rows)
            or not set(reset.get("reset_worker_ids") or []).issubset(WORKERS)
        ):
            raise RuntimeError("XP reset binding differs")
        previous_path = self.current_path
        previous = load(previous_path)
        rows = _rows(previous)
        for worker_id in WORKERS:
            if worker_id not in workers:
                raise RuntimeError(f"runtime worker missing: {worker_id}")
            current = copy.deepcopy(dict(workers[worker_id]))
            if current.get("container_id") != reset_rows[worker_id].get("container_id"):
                raise RuntimeError(f"{worker_id} reset binding identity differs")
            _validate_target(rows[worker_id], current)
            rows[worker_id] = current
        successor = copy.deepcopy(previous)
        successor["workers"] = [rows[str(row["worker_id"])] for row in previous["workers"]]
        self.generation += 1
        self.current_path = self._write_generation(
            successor,
            reset_binding=reset_binding,
            predecessor=previous_path,
        )
        return self.current_path


__all__ = ["RELEASE", "RuntimeGateAuthority", "sha"]
