#!/usr/bin/env python3
"""Persist a short full-parallel non-interference gate diagnosis."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v14 as gate


FREEZE = (
    ROOT / "config/formal/frozen_mysql_four_scene_mixed_disk_four_worker_20260827_v3"
    / "four_scene_effective_resource_successor.json"
)


def cpus(value: str) -> set[int]:
    result: set[int] = set()
    for part in value.split(","):
        left, separator, right = part.strip().partition("-")
        if left:
            result.update(range(int(left), int(right) + 1) if separator else [int(left)])
    return result


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(f"{sha(path)}  {path.name}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    freeze = json.loads(FREEZE.read_text())
    selected = [row for row in freeze["workers"] if row["scene"] == args.scene]
    if len(selected) != 4:
        raise RuntimeError("diagnostic scene worker cardinality differs")
    target = {
        cpu for row in selected for field in ("database_cpuset", "client_cpuset")
        for cpu in cpus(str(row[field]))
    }
    value = gate.rolling_quiet_gate(
        run_id=f"diagnostic-{args.scene}-{os.getpid()}",
        required_seconds=15.0, maximum_wait_seconds=60.0,
        maximum_load1=512.0, target_cpus=target,
    )
    value["diagnostic_created_at"] = datetime.now(timezone.utc).astimezone().isoformat()
    value["diagnostic_target_cpus"] = sorted(target)
    atomic(args.output, value)
    print(json.dumps({"status": value.get("status"), "path": str(args.output), "errors": value.get("errors")}))
    return 0 if value.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
