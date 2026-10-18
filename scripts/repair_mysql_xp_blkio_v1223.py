#!/usr/bin/env python3
"""Restore the frozen cgroup-v1 I/O limits for the four live XP workers.

This is a non-destructive technical repair: it neither restarts containers nor
changes any search state.  Docker's HostConfig is intentionally not treated as
authoritative because this host enforces per-device limits in the live
``docker-limited`` blkio hierarchy.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import prepare_mysql_four_scene_v4 as prepare
from scripts import reset_mysql_xp_data_planes_v6 as reset


FREEZE = ROOT / (
    "config/formal/frozen_mysql_four_scene_two_worker_20260903_v20/"
    "four_scene_effective_resource_successor.json"
)
AUTHORITY = ROOT / (
    "history/mysql_four_scene_20260809/"
    "v1221_to_c_current_identity_authority_20260905/authority/"
    "prepared_binding.json"
)
OUTPUT = ROOT / (
    "history/mysql_four_scene_20260809/"
    "v1226_repair_xp_live_blkio_20260905/repair_binding.json"
)
XP_IDS = {"xp_cpu_w1", "xp_cpu_w2", "xp_io_w1", "xp_io_w2"}


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=False, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii"
    )


def main() -> int:
    if OUTPUT.parent.exists() or OUTPUT.parent.is_symlink():
        raise RuntimeError(f"fresh repair evidence root required: {OUTPUT.parent}")
    freeze = load(FREEZE)
    authority = load(AUTHORITY)
    frozen = {
        str(row["worker_id"]): dict(row)
        for row in freeze.get("workers") or []
        if row.get("worker_id") in XP_IDS
    }
    sealed = {
        str(row["worker_id"]): dict(row)
        for row in authority.get("workers") or []
        if row.get("worker_id") in XP_IDS
    }
    if set(frozen) != XP_IDS or set(sealed) != XP_IDS:
        raise RuntimeError("frozen or sealed XP worker set differs")

    rows: list[dict[str, Any]] = []
    for worker_id in sorted(XP_IDS):
        worker = frozen[worker_id]
        inspect = reset._docker_json("inspect", str(worker["container"]))
        state = dict(inspect.get("State") or {})
        if (
            inspect.get("Id") != sealed[worker_id].get("container_id")
            or state.get("Running") is not True
            or int(state.get("Pid") or 0) <= 1
        ):
            raise RuntimeError(f"{worker_id} live sealed identity differs")
        kernel = prepare._kernel_resource_readback(inspect, worker)
        blkio = prepare._enforce_blkio(inspect, freeze, worker)
        rows.append({
            "worker_id": worker_id,
            "container": worker["container"],
            "container_id": inspect["Id"],
            "kernel_cgroup_resources": kernel,
            "blkio_authority": blkio,
            "passed": True,
        })

    atomic(OUTPUT, {
        "schema_version": "mysql_xp_live_blkio_repair_v1",
        "status": "PASS",
        "created_at": datetime.now().astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "freeze_path": str(FREEZE),
        "freeze_sha256": sha(FREEZE),
        "authority_path": str(AUTHORITY),
        "authority_sha256": sha(AUTHORITY),
        "workers": rows,
        "container_restart_performed": False,
        "container_identity_changed": False,
        "search_state_changed": False,
        "external_process_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
    })
    print(json.dumps({"status": "PASS", "output": str(OUTPUT)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
