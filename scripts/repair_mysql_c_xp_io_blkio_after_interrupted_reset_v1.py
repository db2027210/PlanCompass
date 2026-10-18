#!/usr/bin/env python3
"""Restore and bind frozen blkio limits after the interrupted XP-I/O reset."""

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

from scripts import reset_mysql_xp_data_planes_v5 as reset


FREEZE = ROOT / (
    "config/formal/frozen_mysql_four_scene_two_worker_20260903_v20/"
    "four_scene_effective_resource_successor.json"
)
AUTHORITY = ROOT / (
    "history/mysql_four_scene_20260809/"
    "v1239_c_xp_io_scene_authority_v107_20260905/authority/prepared_binding.json"
)
OUTPUT = ROOT / (
    "history/mysql_four_scene_20260809/"
    "v1241_c_xp_io_blkio_repair_v107_20260905/readback.json"
)
WORKER_IDS = {"xp_io_w1", "xp_io_w2"}


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
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
    if OUTPUT.exists() or OUTPUT.parent.exists():
        raise RuntimeError("fresh blkio repair output required")
    freeze = load(FREEZE)
    authority = load(AUTHORITY)
    frozen = {
        str(row["worker_id"]): dict(row) for row in freeze.get("workers") or []
        if str(row.get("worker_id")) in WORKER_IDS
    }
    bound = {
        str(row["worker_id"]): dict(row) for row in authority.get("workers") or []
        if str(row.get("worker_id")) in WORKER_IDS
    }
    if set(frozen) != WORKER_IDS or set(bound) != WORKER_IDS:
        raise RuntimeError("XP-I/O blkio authority differs")
    rows = []
    for worker_id in sorted(WORKER_IDS):
        worker = frozen[worker_id]
        inspected = reset._docker_json("inspect", str(worker["container"]))
        if (
            inspected.get("Id") != bound[worker_id].get("container_id")
            or (inspected.get("State") or {}).get("Running") is not True
        ):
            raise RuntimeError(f"{worker_id} live identity differs")
        readback = reset.prep._enforce_blkio(inspected, freeze, worker)
        rows.append({
            "worker_id": worker_id, "container": worker["container"],
            "container_id": inspected["Id"],
            "expected_read_bps": int(worker["io_read_bps"]),
            "expected_write_bps": int(worker["io_write_bps"]),
            "blkio_readback": readback,
        })
    atomic(OUTPUT, {
        "schema_version": "mysql_algorithm_c_xp_io_blkio_repair_v1",
        "status": "PASS", "created_at": datetime.now().astimezone().isoformat(),
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "workers": rows, "accepted_or_rejected_round_credit": 0,
        "candidate_measurements_reused": 0,
        "external_process_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
        "freeze_path": str(FREEZE), "freeze_sha256": sha(FREEZE),
        "authority_path": str(AUTHORITY), "authority_sha256": sha(AUTHORITY),
    })
    print(OUTPUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
