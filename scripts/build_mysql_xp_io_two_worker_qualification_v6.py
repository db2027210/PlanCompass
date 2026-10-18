#!/usr/bin/env python3
"""Freeze v6 with exact current predecessor container identities."""

from __future__ import annotations

import copy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SOURCE_CONTRACT = ROOT / (
    "config/formal/frozen_mysql_xp_io_two_worker_qualification_20260903_v5/"
    "qualification_contract.json"
)
OLD_PREPARED = ROOT / (
    "history/mysql_four_scene_20260809/"
    "v1062_xp_io_device_fix_point_replay_authority_20260829/prepared_binding.json"
)
CURRENT_RESET = ROOT / (
    "history/mysql_four_scene_20260809/"
    "v1173_xp_io_two_worker_qualification_v4_20260903/xp_resets/"
    "v1173_xp_io_two_worker_qualification_v4_20260903_qualification_wave_01/"
    "reset_binding.json"
)
OUT = ROOT / "config/formal/frozen_mysql_xp_io_two_worker_qualification_20260903_v6"
OUTPUT_PREPARED = OUT / "current_predecessor_prepared_binding.json"
OUTPUT_CONTRACT = OUT / "qualification_contract.json"


def now() -> str:
    return datetime.now().astimezone().isoformat()


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


def inspect(container: str) -> dict:
    completed = subprocess.run(
        ["docker", "inspect", container], cwd=ROOT, capture_output=True,
        text=True, check=True, timeout=60,
    )
    values = json.loads(completed.stdout)
    if len(values) != 1:
        raise RuntimeError(f"Docker inspect cardinality differs: {container}")
    return values[0]


def main() -> int:
    if OUT.exists() or OUT.is_symlink():
        raise RuntimeError(f"fresh contract directory required: {OUT}")
    old = json.loads(OLD_PREPARED.read_text(encoding="utf-8"))
    current = json.loads(CURRENT_RESET.read_text(encoding="utf-8"))
    if current.get("status") != "PASS":
        raise RuntimeError("current predecessor reset binding is not PASS")
    current_rows = {
        row["worker_id"]: copy.deepcopy(row)
        for row in current["workers"]
        if row.get("worker_id") in {"xp_io_w1", "xp_io_w2"}
    }
    if set(current_rows) != {"xp_io_w1", "xp_io_w2"}:
        raise RuntimeError("current predecessor worker set differs")
    live_readback = {}
    for worker_id, row in current_rows.items():
        live = inspect(row["container"])
        labels = (live.get("Config") or {}).get("Labels") or {}
        if (
            live.get("Id") != row.get("container_id")
            or (live.get("State") or {}).get("Running") is not True
            or labels.get("ra.worker_id") != worker_id
            or labels.get("ra.owner") != "mysql_four_scene_v5"
        ):
            raise RuntimeError(f"{worker_id} current predecessor identity differs")
        live_readback[worker_id] = {
            "container_id": live["Id"],
            "running": True,
            "captured_at": now(),
        }
    workers = []
    for row in old["workers"]:
        workers.append(current_rows.get(row["worker_id"], copy.deepcopy(row)))
    prepared = copy.deepcopy(old)
    prepared.update({
        "schema_version": "mysql_xp_io_current_predecessor_prepared_v1",
        "status": "PREPARED",
        "created_at": now(),
        "workers": workers,
        "workload_started": False,
        "formal_outer_started": False,
        "purpose": "exact_identity_source_for_forced_fresh_qualification_reset",
        "source_prepared": bind(OLD_PREPARED),
        "source_current_reset": bind(CURRENT_RESET),
        "live_identity_readback": live_readback,
    })
    atomic(OUTPUT_PREPARED, prepared)

    contract = json.loads(SOURCE_CONTRACT.read_text(encoding="utf-8"))
    contract.update({
        "created_at": now(),
        "status": "FROZEN",
        "release": "exact_current_predecessor_identity_v6",
        "predecessor_contract": bind(SOURCE_CONTRACT),
        "predecessor_identity_refresh": bind(OUTPUT_PREPARED),
    })
    contract["authority"]["prepared_path"] = str(OUTPUT_PREPARED)
    contract["authority"]["prepared_sha256"] = sha(OUTPUT_PREPARED)
    contract["source_bindings"].update({
        "builder": bind(Path(__file__).resolve()),
        "runner": bind(ROOT / "scripts/run_mysql_xp_io_two_worker_qualification_v1.py"),
        "current_predecessor_prepared": bind(OUTPUT_PREPARED),
    })
    atomic(OUTPUT_CONTRACT, contract)
    print(json.dumps({
        "status": "PASS", "contract": str(OUTPUT_CONTRACT),
        "contract_sha256": sha(OUTPUT_CONTRACT),
        "prepared": str(OUTPUT_PREPARED),
        "prepared_sha256": sha(OUTPUT_PREPARED),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
