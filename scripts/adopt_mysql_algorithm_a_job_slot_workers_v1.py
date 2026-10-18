#!/usr/bin/env python3
"""Adopt the sealed, stopped JOB w3/w4 clones for a later slot handoff."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from scripts import mysql_algorithm_a_job_dynamic_slots_v1 as slots
from scripts import reset_mysql_xp_data_planes_v5 as reset


HISTORY = ROOT / "history/mysql_four_scene_20260809"
SEALED_ROOT = (
    HISTORY / "v40_to_a_last_accepted_20260822_attempt002_job_dynamic_slots_v2"
    / "provisioned_workers"
)


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular sealed worker evidence required: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"sealed worker evidence is not an object: {path}")
    return dict(value)


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def adopt_worker(
    freeze: Mapping[str, Any], worker: Mapping[str, Any], output: Path,
) -> dict[str, Any]:
    worker_id = str(worker.get("worker_id") or "")
    if worker_id not in slots.NEW_CONTAINER:
        raise RuntimeError(f"JOB slot adoption is limited to w3/w4: {worker_id}")
    sealed_path = SEALED_ROOT / f"{worker_id}.provisioned.json"
    sealed = load(sealed_path)
    position_fields = (
        "scene", "container", "host_port", *slots.POSITION_FIELDS,
        *slots.RESOURCE_FIELDS,
    )
    if (
        sealed.get("ready") is not True
        or sealed.get("worker_id") != worker_id
        or any(sealed.get(field) != worker.get(field) for field in position_fields)
        or sealed.get("copy_mode") != "cp_a_reflink_never"
        or not str(sealed.get("datadir") or "").startswith("/disk2/")
    ):
        raise RuntimeError(f"sealed JOB slot evidence differs: {worker_id}")
    container = str(worker["container"])
    inspected = reset._docker_json("inspect", container)
    if inspected.get("Id") != sealed.get("container_id"):
        raise RuntimeError(f"sealed JOB slot container identity differs: {worker_id}")
    if (inspected.get("State") or {}).get("Running") is not True:
        reset._docker_run(["docker", "start", container], timeout=120)
    defaults = Path(str(sealed["credential_defaults"]))
    benchmark_defaults = Path(str(sealed["benchmark_defaults"]))
    if (
        not defaults.is_file() or defaults.is_symlink()
        or not benchmark_defaults.is_file() or benchmark_defaults.is_symlink()
        or defaults.stat().st_mode & 0o077
        or benchmark_defaults.stat().st_mode & 0o077
    ):
        raise RuntimeError(f"sealed JOB slot credentials differ: {worker_id}")
    reset._wait_mysql_host(worker, defaults, timeout_seconds=300)
    reset._host_mysql(
        worker, defaults,
        "UPDATE mysql.server_cost SET cost_value=NULL; "
        "UPDATE mysql.engine_cost SET cost_value=NULL; FLUSH OPTIMIZER_COSTS;",
        timeout=60,
    )
    sessions = reset._host_mysql(
        worker, defaults,
        "SELECT COUNT(*) FROM information_schema.processlist "
        "WHERE ID<>CONNECTION_ID() AND COMMAND<>'Daemon' "
        "AND USER<>'event_scheduler'",
        timeout=30,
    ).stdout.strip()
    readback = reset._host_mysql(
        worker, defaults,
        "SELECT @@global.innodb_buffer_pool_size,@@global.innodb_flush_method; "
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='imdb_job';",
        timeout=60,
    ).stdout.splitlines()
    identity = reset._host_mysql(
        worker, benchmark_defaults, "SELECT CURRENT_USER(),DATABASE();", timeout=30,
    ).stdout.strip().split("\t")
    if (
        sessions != "0" or len(readback) != 2
        or int(readback[0].split("\t")[0]) != int(worker["innodb_buffer_pool_bytes"])
        or readback[0].split("\t")[1].upper() != "O_DIRECT"
        or int(readback[1]) <= 0
        or not identity or not identity[0].startswith(f"{reset.BENCHMARK_USER}@")
        or identity[1] != "imdb_job"
    ):
        raise RuntimeError(f"sealed JOB slot live readback differs: {worker_id}")
    inspected = reset._docker_json("inspect", container)
    kernel = reset.prep._kernel_resource_readback(inspected, worker)
    blkio = reset.prep._enforce_blkio(inspected, freeze, worker)
    adopted = {
        **copy.deepcopy(sealed), **copy.deepcopy(dict(worker)),
        "ready": True, "container_id": inspected["Id"],
        "container_pid": int((inspected.get("State") or {}).get("Pid") or 0),
        "kernel_cgroup_resources": kernel, "blkio_authority": blkio,
        "adopted_from_sealed_stopped_clone": True,
        "sealed_predecessor_evidence_path": str(sealed_path),
        "sealed_predecessor_evidence_sha256": sha(sealed_path),
        "adopted_at": now(), "passwords_emitted": False,
    }
    evidence = output / f"{worker_id}.adopted.json"
    atomic(evidence, adopted)
    adopted["worker_evidence_path"] = str(evidence)
    adopted["worker_evidence_sha256"] = sha(evidence)
    return adopted


__all__ = ["SEALED_ROOT", "adopt_worker"]
