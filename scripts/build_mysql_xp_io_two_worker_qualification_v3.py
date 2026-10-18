#!/usr/bin/env python3
"""Freeze v3 with container-scoped swap isolation and host readback."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / (
    "config/formal/frozen_mysql_xp_io_two_worker_qualification_20260903_v2/"
    "qualification_contract.json"
)
OUTPUT = ROOT / (
    "config/formal/frozen_mysql_xp_io_two_worker_qualification_20260903_v3/"
    "qualification_contract.json"
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bind(path: Path) -> dict[str, str]:
    return {"path": str(path.relative_to(ROOT)), "sha256": sha(path)}


def main() -> int:
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise RuntimeError(f"fresh contract required: {OUTPUT}")
    document = json.loads(SOURCE.read_text(encoding="utf-8"))
    document.update({
        "created_at": datetime.now().astimezone().isoformat(),
        "status": "FROZEN",
        "release": "container_scoped_zero_swap_host_inventory_v3",
        "predecessor_contract": bind(SOURCE),
        "swap_isolation_contract": {
            "host_swap_devices_may_exist": True,
            "host_swap_inventory_required": True,
            "docker_memory_swap_must_equal_memory": True,
            "container_effective_swap_bytes": 0,
            "kernel_cgroup_readback_required": True,
        },
    })
    document["source_bindings"]["builder"] = bind(Path(__file__).resolve())
    document["source_bindings"]["runner"] = bind(
        ROOT / "scripts/run_mysql_xp_io_two_worker_qualification_v1.py"
    )
    OUTPUT.parent.mkdir(parents=True, mode=0o700)
    temporary = OUTPUT.with_name(f".{OUTPUT.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, OUTPUT)
    print(json.dumps({"status": "PASS", "contract": str(OUTPUT), "sha256": sha(OUTPUT)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
