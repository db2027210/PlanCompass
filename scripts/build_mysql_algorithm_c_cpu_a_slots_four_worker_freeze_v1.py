#!/usr/bin/env python3
"""Freeze four-worker XP C using currently idle disk1/disk2 physical slots."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (
    ROOT / "config/formal/frozen_mysql_four_scene_cpu_a_slots_two_worker_20260828_v2"
    / "four_scene_effective_resource_successor.json"
)
DONORS = (
    ROOT / "config/formal/frozen_mysql_four_scene_mixed_disk_four_worker_20260827_v4"
    / "four_scene_effective_resource_successor.json"
)
OUTPUT_ROOT = (
    ROOT / "config/formal/frozen_mysql_four_scene_cpu_a_slots_four_worker_20260828_v3"
)
OUTPUT = OUTPUT_ROOT / "four_scene_effective_resource_successor.json"
POSITION_FIELDS = (
    "container", "host_port", "database_cpuset", "database_cpuset_mems",
    "client_cpuset", "client_cpuset_mems",
)
RESOURCE_FIELDS = (
    "cpu_quota_cores", "memory_bytes", "swap_bytes", "pids_max",
    "io_read_bps", "io_write_bps", "innodb_buffer_pool_bytes",
)
PLAN = {
    "xp_cpu_w3": ("hybench_xp_cpu", "job_w4", "/disk1/resource_adaptive/mysql_algorithm_c_cpu_a_slots_v3"),
    "xp_cpu_w4": ("hybench_xp_cpu", "tpch_w4", "/disk1/resource_adaptive/mysql_algorithm_c_cpu_a_slots_v3"),
    "xp_io_w3": ("hybench_xp_io", "xp_io_w1", "/disk2/resource_adaptive_mysql_algorithm_c_cpu_a_slots_v3"),
    "xp_io_w4": ("hybench_xp_io", "xp_io_w2", "/disk2/resource_adaptive_mysql_algorithm_c_cpu_a_slots_v3"),
}


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def cpus(value: Any) -> set[int]:
    result: set[int] = set()
    for raw in str(value).split(","):
        start, separator, end = raw.strip().partition("-")
        result.update(range(int(start), int(end) + 1) if separator else [int(start)])
    return result


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
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
    if OUTPUT_ROOT.exists():
        raise RuntimeError(f"fresh output required: {OUTPUT_ROOT}")
    source = load(SOURCE)
    donors = load(DONORS)
    rows = {str(row["worker_id"]): copy.deepcopy(dict(row)) for row in source["workers"]}
    donor_rows = {str(row["worker_id"]): dict(row) for row in donors["workers"]}
    storage = copy.deepcopy(source.get("storage_by_worker") or {})
    for worker_id, (scene, donor_id, runtime_base) in PLAN.items():
        prototype = rows["xp_cpu_w1" if scene == "hybench_xp_cpu" else "xp_io_w1"]
        donor = donor_rows[donor_id]
        row = copy.deepcopy(prototype)
        row.update({
            "worker_id": worker_id, "scene": scene,
            "runtime_root": str(Path(runtime_base) / worker_id),
            "slot_origin": donor_id,
            "slot_reassignment_contract": "mysql_cpu_a_slots_idle_2_to_4_v1",
        })
        for field in POSITION_FIELDS:
            row[field] = copy.deepcopy(donor[field])
        for field in RESOURCE_FIELDS:
            row[field] = copy.deepcopy(prototype[field])
        rows[worker_id] = row
        donor_storage = dict((donors.get("storage_by_worker") or {})[donor_id])
        donor_storage["runtime_root"] = runtime_base
        donor_storage["copy_mode"] = "fresh_physical_xp_template_copy_into_idle_slot"
        storage[worker_id] = donor_storage
    containers = [str(row["container"]) for row in rows.values()]
    ports = [int(row["host_port"]) for row in rows.values()]
    if len(rows) != 12 or len(set(containers)) != 12 or len(set(ports)) != 12:
        raise RuntimeError("successor worker/container/port cardinality differs")
    db_seen: dict[int, str] = {}
    client_seen: dict[int, str] = {}
    for worker_id, row in rows.items():
        for cpu in cpus(row["database_cpuset"]):
            if cpu in db_seen:
                raise RuntimeError(f"database CPU overlap {cpu}: {worker_id}/{db_seen[cpu]}")
            db_seen[cpu] = worker_id
        for cpu in cpus(row["client_cpuset"]):
            if cpu in client_seen:
                raise RuntimeError(f"client CPU overlap {cpu}: {worker_id}/{client_seen[cpu]}")
            client_seen[cpu] = worker_id
    value = copy.deepcopy(source)
    value.update({
        "schema_version": "mysql_four_scene_cpu_a_slots_four_worker_successor_v3",
        "release_version": "cpu_a_slots_idle_capacity_2_to_4_v1",
        "created_at": now(), "status": "FROZEN_RUNTIME_READBACK_PENDING",
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "workers": [rows[key] for key in sorted(rows)],
        "storage_by_worker": storage,
        "xp_capacity_successor": {
            "physical_workers_per_xp_scene_before": 2,
            "physical_workers_per_xp_scene_after": 4,
            "candidate_batches_after": [[1, 2, 3, 4], [5]],
            "memory_scene_workers_unchanged": True,
            "idle_slots_used": {key: PLAN[key][1] for key in sorted(PLAN)},
            "round_boundary_transition_required": True,
            "accepted_history_preserved": True,
            "completed_candidate_replay_due_to_transition": 0,
            "recalibration_performed": False,
            "native_xp_workload_unchanged": True,
            "four_scene_barrier": False,
            "sibling_scene_wait": False,
            "lease_mode": "none"
        },
    })
    atomic(OUTPUT, value)
    print(json.dumps({
        "status": "FROZEN_RUNTIME_READBACK_PENDING",
        "path": str(OUTPUT), "sha256": sha(OUTPUT),
        "workers": len(rows), "xp_workers": 8,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
