#!/usr/bin/env python3
"""Freeze v4 with forced fresh Docker creation for both XP/I/O workers."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_CONTRACT = ROOT / (
    "config/formal/frozen_mysql_xp_io_two_worker_qualification_20260903_v3/"
    "qualification_contract.json"
)
SOURCE_FREEZE = ROOT / (
    "config/formal/frozen_mysql_xp_eight_worker_20260827_v1/"
    "xp_effective_resource_successor.json"
)
OUT = ROOT / "config/formal/frozen_mysql_xp_io_two_worker_qualification_20260903_v4"
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
        "qualification_release": "xp_io_w1_w2_forced_fresh_container_v4",
    })
    for worker in freeze["workers"]:
        if worker.get("worker_id") in {"xp_io_w1", "xp_io_w2"}:
            worker["fresh_xp_template_reset_required"] = True
            worker["container_reuse_prohibited"] = True
            worker["effective_swap_bytes_required"] = 0
    atomic(OUTPUT_FREEZE, freeze)

    contract = json.loads(SOURCE_CONTRACT.read_text(encoding="utf-8"))
    contract.update({
        "created_at": datetime.now().astimezone().isoformat(),
        "status": "FROZEN",
        "release": "forced_fresh_container_and_live_resource_readback_v4",
        "predecessor_contract": bind(SOURCE_CONTRACT),
        "resource_freeze": bind(OUTPUT_FREEZE),
        "container_lifecycle_contract": {
            "selected_worker_container_reuse_prohibited": True,
            "fresh_physical_template_copy_required": True,
            "fresh_docker_create_required": True,
            "live_docker_and_kernel_cgroup_readback_required_each_wave": True,
        },
    })
    contract["source_bindings"]["builder"] = bind(Path(__file__).resolve())
    contract["source_bindings"]["runner"] = bind(
        ROOT / "scripts/run_mysql_xp_io_two_worker_qualification_v1.py"
    )
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
