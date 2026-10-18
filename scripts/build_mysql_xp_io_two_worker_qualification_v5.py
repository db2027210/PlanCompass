#!/usr/bin/env python3
"""Freeze v5 with verifiable container memory swappiness zero."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_CONTRACT = ROOT / (
    "config/formal/frozen_mysql_xp_io_two_worker_qualification_20260903_v4/"
    "qualification_contract.json"
)
SOURCE_FREEZE = ROOT / (
    "config/formal/frozen_mysql_xp_io_two_worker_qualification_20260903_v4/"
    "xp_effective_resource_successor.json"
)
OUT = ROOT / "config/formal/frozen_mysql_xp_io_two_worker_qualification_20260903_v5"
OUTPUT_FREEZE = OUT / "xp_effective_resource_successor.json"
OUTPUT_CONTRACT = OUT / "qualification_contract.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bind(path: Path) -> dict[str, str]:
    return {"path": str(path.relative_to(ROOT)), "sha256": sha(path)}


def atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def main() -> int:
    if OUT.exists() or OUT.is_symlink():
        raise RuntimeError(f"fresh contract directory required: {OUT}")
    freeze = json.loads(SOURCE_FREEZE.read_text(encoding="utf-8"))
    freeze.update({
        "created_at": datetime.now().astimezone().isoformat(),
        "status": "FROZEN_RUNTIME_READBACK_PENDING",
        "qualification_predecessor": bind(SOURCE_FREEZE),
        "qualification_release": "xp_io_zero_memory_swappiness_v5",
        "swap_isolation": {
            "host_memory_controller": "cgroup_v1_without_memsw_files",
            "docker_memory_swap_request": "memory_equals_memory_swap",
            "docker_memory_swap_observed_on_this_host": -1,
            "container_memory_swappiness": 0,
            "effective_policy": "anonymous_swap_disabled_at_container_cgroup",
            "host_swap_inventory_required": True,
            "global_swapoff_prohibited": True,
        },
    })
    for worker in freeze["workers"]:
        if worker.get("worker_id") in {"xp_io_w1", "xp_io_w2"}:
            worker["memory_swappiness"] = 0
            worker["effective_swap_policy"] = "anonymous_swap_disabled_swappiness_0"
    atomic(OUTPUT_FREEZE, freeze)

    contract = json.loads(SOURCE_CONTRACT.read_text(encoding="utf-8"))
    contract.update({
        "created_at": datetime.now().astimezone().isoformat(),
        "status": "FROZEN",
        "release": "zero_memory_swappiness_live_readback_v5",
        "predecessor_contract": bind(SOURCE_CONTRACT),
        "resource_freeze": bind(OUTPUT_FREEZE),
        "swap_isolation_contract": freeze["swap_isolation"],
    })
    contract["source_bindings"].update({
        "builder": bind(Path(__file__).resolve()),
        "runner": bind(ROOT / "scripts/run_mysql_xp_io_two_worker_qualification_v1.py"),
        "reset": bind(ROOT / "scripts/reset_mysql_xp_data_planes_mixed_eight_worker_scene_v7.py"),
        "reset_predecessor": bind(ROOT / "scripts/reset_mysql_xp_data_planes_mixed_eight_worker_scene_v6.py"),
    })
    atomic(OUTPUT_CONTRACT, contract)
    print(json.dumps({
        "status": "PASS", "contract": str(OUTPUT_CONTRACT),
        "contract_sha256": sha(OUTPUT_CONTRACT),
        "resource_freeze": str(OUTPUT_FREEZE),
        "resource_freeze_sha256": sha(OUTPUT_FREEZE),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
