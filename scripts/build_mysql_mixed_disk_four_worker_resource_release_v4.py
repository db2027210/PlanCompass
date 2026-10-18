#!/usr/bin/env python3
"""Remap TPC-H w3 client CPUs away from the inventoried BenchBase client."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (
    ROOT / "config/formal/frozen_mysql_four_scene_mixed_disk_four_worker_20260827_v3"
    / "four_scene_effective_resource_successor.json"
)
OUTPUT = (
    ROOT / "config/formal/frozen_mysql_four_scene_mixed_disk_four_worker_20260827_v4"
    / "four_scene_effective_resource_successor.json"
)
MANAGEMENT_CPUS = "488-489,498-499"
EXTERNAL_CPUS = "32-103,128-175,184-199,228-255,384-387,400-415"


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
    path.parent.mkdir(parents=True, exist_ok=False)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(f"{sha(path)}  {path.name}\n")


def main() -> int:
    if OUTPUT.exists() or OUTPUT.parent.exists():
        raise RuntimeError(f"fresh resource release required: {OUTPUT.parent}")
    value = json.loads(SOURCE.read_text())
    workers = {str(row["worker_id"]): row for row in value["workers"]}
    worker = workers["tpch_w3"]
    if worker.get("client_cpuset") != "128-131,180-183":
        raise RuntimeError("TPC-H w3 predecessor client affinity differs")
    worker["client_cpuset"] = "344-351"
    worker["client_cpuset_mems"] = "0"
    worker["client_cpuset_remapped_for_actual_external_disjointness"] = True

    occupied: dict[int, str] = {}
    for row in value["workers"]:
        for field in ("database_cpuset", "client_cpuset"):
            label = f"{row['worker_id']}:{field}"
            for cpu in cpus(str(row[field])):
                if cpu in occupied:
                    raise RuntimeError(f"MySQL CPU overlap: {label} and {occupied[cpu]}")
                occupied[cpu] = label
    if set(occupied) & cpus(MANAGEMENT_CPUS):
        raise RuntimeError("management CPU overlap remains")
    overlap = set(occupied) & cpus(EXTERNAL_CPUS)
    if overlap:
        raise RuntimeError(f"external CPU overlap remains: {sorted(overlap)}")

    value.update({
        "schema_version": "mysql_four_scene_mixed_disk_four_worker_successor_v4",
        "status": "FROZEN_RUNTIME_READBACK_PENDING",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "predecessor_resource_freeze_path": str(SOURCE),
        "predecessor_resource_freeze_sha256": sha(SOURCE),
        "tpch_w3_client_cpu_remap": {
            "from": "128-131,180-183", "to": "344-351", "cpuset_mems": "0",
            "reason": "actual_inventoried_benchbase_client_cpu_disjointness",
        },
        "actual_external_cpu_exclusion": EXTERNAL_CPUS,
        "all_mysql_database_client_and_management_cpus_pairwise_disjoint": True,
        "all_mysql_cpus_disjoint_from_actual_external_processes": True,
        "database_container_resources_changed_from_v3": False,
        "client_affinity_only_successor": True,
    })
    atomic(OUTPUT, value)
    print(json.dumps({"status": "FROZEN", "path": str(OUTPUT), "sha256": sha(OUTPUT)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
