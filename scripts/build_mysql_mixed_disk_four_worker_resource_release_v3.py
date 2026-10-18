#!/usr/bin/env python3
"""Freeze the mixed-SSD 16-lane envelope with disjoint external-client CPUs."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (
    ROOT / "config/formal/frozen_mysql_four_scene_mixed_disk_four_worker_20260827_v2"
    / "four_scene_effective_resource_successor.json"
)
OUTPUT = (
    ROOT / "config/formal/frozen_mysql_four_scene_mixed_disk_four_worker_20260827_v3"
    / "four_scene_effective_resource_successor.json"
)
CLIENT_REMAP = {"job_w4": ("336-343", "0"), "tpch_w4": ("424-431", "1")}
MANAGEMENT_CPUS = "488-489,498-499"
EXTERNAL_PG_CPUS = (
    "32-103,132-175,184-199,228-255,400-415"
)


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cpus(value: str) -> set[int]:
    result: set[int] = set()
    for part in value.split(","):
        left, separator, right = part.strip().partition("-")
        if not left:
            continue
        result.update(range(int(left), int(right) + 1) if separator else [int(left)])
    return result


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=False)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def main() -> int:
    if OUTPUT.exists() or OUTPUT.parent.exists():
        raise RuntimeError(f"fresh resource release required: {OUTPUT.parent}")
    value = json.loads(SOURCE.read_text(encoding="utf-8"))
    workers = {str(row["worker_id"]): row for row in value.get("workers") or []}
    if len(workers) != 16 or set(CLIENT_REMAP) - set(workers):
        raise RuntimeError("mixed v2 worker set differs")
    for worker_id, (cpu_set, mem_set) in CLIENT_REMAP.items():
        workers[worker_id]["client_cpuset"] = cpu_set
        workers[worker_id]["client_cpuset_mems"] = mem_set
        workers[worker_id]["client_cpuset_remapped_for_actual_external_disjointness"] = True

    occupied: dict[int, str] = {}
    for worker in value["workers"]:
        for field in ("database_cpuset", "client_cpuset"):
            label = f"{worker['worker_id']}:{field}"
            for cpu in cpus(str(worker[field])):
                if cpu in occupied:
                    raise RuntimeError(f"MySQL CPU overlap: {label} and {occupied[cpu]}")
                occupied[cpu] = label
    for cpu in cpus(MANAGEMENT_CPUS):
        if cpu in occupied:
            raise RuntimeError(f"management CPU overlaps {occupied[cpu]}")
    overlap = set(occupied) & cpus(EXTERNAL_PG_CPUS)
    if overlap:
        raise RuntimeError(f"external PostgreSQL CPU overlap remains: {sorted(overlap)}")

    value.update({
        "schema_version": "mysql_four_scene_mixed_disk_four_worker_successor_v3",
        "status": "FROZEN_RUNTIME_READBACK_PENDING",
        "created_at": now(),
        "predecessor_resource_freeze_path": str(SOURCE),
        "predecessor_resource_freeze_sha256": sha(SOURCE),
        "client_cpu_remap": {
            "job_w4": {"from": "400-407", "to": "336-343", "cpuset_mems": "0"},
            "tpch_w4": {"from": "408-415", "to": "424-431", "cpuset_mems": "1"},
            "reason": "actual_external_postgresql_probe_cpu_disjointness",
        },
        "management_cpuset": MANAGEMENT_CPUS,
        "actual_external_postgresql_cpu_exclusion": EXTERNAL_PG_CPUS,
        "all_mysql_database_client_and_management_cpus_pairwise_disjoint": True,
        "all_mysql_cpus_disjoint_from_actual_external_postgresql_cpus": True,
        "database_container_resources_changed_from_v2": False,
        "client_affinity_only_successor": True,
    })
    atomic(OUTPUT, value)
    print(json.dumps({"status": "FROZEN", "path": str(OUTPUT), "sha256": sha(OUTPUT)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
