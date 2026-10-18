#!/usr/bin/env python3
"""Rebind exact idle MySQL containers as fresh XP workers without deleting donors."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
from typing import Any, Mapping


from scripts import provision_mysql_algorithm_a_xp_slot_workers_v1 as physical


def _run(argv: list[str], timeout: float = 120.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv, text=True, capture_output=True, check=True, timeout=timeout,
    )


def _inspect(container: str) -> dict[str, Any]:
    values = json.loads(_run(["docker", "inspect", container], 60).stdout)
    if not isinstance(values, list) or len(values) != 1:
        raise RuntimeError(f"Docker inspect cardinality differs: {container}")
    return dict(values[0])


def release_idle_container(worker: Mapping[str, Any]) -> dict[str, Any]:
    """Remove only the container object; retain its predecessor datadir."""
    container = str(worker["container"])
    inspected = _inspect(container)
    state = inspected.get("State") or {}
    mounts = {str(row.get("Destination")): str(row.get("Source")) for row in inspected.get("Mounts") or []}
    predecessor_datadir = mounts.get("/var/lib/mysql")
    if not predecessor_datadir or predecessor_datadir == str(Path(str(worker["runtime_root"])) / "datadir"):
        raise RuntimeError(f"{container} idle predecessor mount authority differs")
    if state.get("Running") is True:
        sessions = _run([
            "docker", "exec", container, "mysql",
            "--defaults-extra-file=/run/ra-credentials/selected.cnf", "-Nse",
            "SELECT COUNT(*) FROM information_schema.processlist "
            "WHERE ID<>CONNECTION_ID() AND COMMAND<>'Daemon' AND USER<>'event_scheduler';",
        ], 60).stdout.strip()
        if sessions != "0":
            raise RuntimeError(f"{container} is not idle: {sessions} sessions")
        _run(["docker", "stop", "--time", "120", container], 180)
    _run(["docker", "rm", container], 60)
    return {
        "container": container,
        "predecessor_container_id": inspected.get("Id"),
        "predecessor_datadir_retained": predecessor_datadir,
        "container_object_removed": True,
        "recoverable_from_retained_datadir": True,
    }


def _fresh_target(
    freeze: Mapping[str, Any], worker: Mapping[str, Any],
) -> tuple[Path, Path, Path, Path]:
    worker_id = str(worker["worker_id"])
    runtime_root = Path(str(worker["runtime_root"]))
    storage = dict(
        (freeze.get("storage_by_worker") or {}).get(worker_id)
        or freeze["storage"]
    )
    mount = Path(str(storage["mount"])).resolve()
    datadir = runtime_root / "datadir"
    credentials = runtime_root / "credentials"
    template = Path(str(freeze["templates"][worker["scene"]])).resolve(strict=True)
    if (
        mount not in runtime_root.resolve().parents
        or runtime_root.exists() or runtime_root.is_symlink()
        or subprocess.run(
            ["docker", "ps", "-aq", "--filter", f"name=^{worker['container']}$"],
            text=True, capture_output=True, check=True, timeout=30,
        ).stdout.strip()
    ):
        raise RuntimeError(f"unsafe or non-fresh idle XP target: {worker_id}")
    if subprocess.run(
        ["ss", "-ltnH", f"sport = :{int(worker['host_port'])}"],
        text=True, capture_output=True, check=True, timeout=15,
    ).stdout.strip():
        raise RuntimeError(f"new XP host port already listens: {worker_id}")
    runtime_root.mkdir(parents=True, mode=0o700)
    datadir.mkdir(mode=0o700)
    credentials.mkdir(mode=0o700)
    return runtime_root, datadir, credentials, template


def provision_worker(
    freeze: Mapping[str, Any], worker: Mapping[str, Any], output: Path,
) -> dict[str, Any]:
    worker = copy.deepcopy(dict(worker))
    release = release_idle_container(worker)
    worker_id = str(worker["worker_id"])
    storage = dict(
        (freeze.get("storage_by_worker") or {}).get(worker_id)
        or freeze["storage"]
    )
    local_freeze = copy.deepcopy(dict(freeze))
    local_freeze["storage"] = storage
    original_slots = physical.slots
    original_target = physical._ensure_fresh_target
    physical.slots = SimpleNamespace(
        NEW_CONTAINER={worker_id: str(worker["container"])},
    )
    physical._ensure_fresh_target = _fresh_target
    try:
        row = physical.provision_worker(local_freeze, worker, output)
    finally:
        physical.slots = original_slots
        physical._ensure_fresh_target = original_target
    row["idle_slot_release"] = release
    return row
