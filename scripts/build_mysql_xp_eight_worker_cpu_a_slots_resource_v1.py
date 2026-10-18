#!/usr/bin/env python3
"""Freeze eight workers per XP scene from the current completed memory slots."""

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
    ROOT / "config/formal/frozen_mysql_four_scene_cpu_a_slots_four_worker_20260828_v3"
    / "four_scene_effective_resource_successor.json"
)
PLACEMENTS = (
    ROOT / "config/formal/frozen_mysql_four_scene_mixed_disk_four_worker_20260827_v4"
    / "four_scene_effective_resource_successor.json"
)
OUTPUT_ROOT = (
    ROOT / "config/formal/frozen_mysql_xp_eight_worker_cpu_a_slots_20260828_v6"
)
OUTPUT = OUTPUT_ROOT / "xp_effective_resource_successor.json"

POSITION_FIELDS = (
    "container", "host_port", "database_cpuset", "database_cpuset_mems",
    "client_cpuset", "client_cpuset_mems",
)
RESOURCE_FIELDS = (
    "cpu_quota_cores", "memory_bytes", "swap_bytes", "pids_max",
    "io_read_bps", "io_write_bps", "innodb_buffer_pool_bytes",
)

# source_kind=current uses a completed memory row in SOURCE. source_kind=mixed
# uses a physical slot that was never assigned by the current v3 successor.
EXPANSION = {
    "xp_cpu_w5": ("hybench_xp_cpu", "current", "job_w1"),
    "xp_cpu_w6": ("hybench_xp_cpu", "current", "job_w2"),
    "xp_cpu_w7": ("hybench_xp_cpu", "mixed", "job_w2"),
    "xp_cpu_w8": ("hybench_xp_cpu", "mixed", "xp_io_w3"),
    "xp_io_w5": ("hybench_xp_io", "current", "tpch_w1"),
    "xp_io_w6": ("hybench_xp_io", "current", "tpch_w2"),
    "xp_io_w7": ("hybench_xp_io", "mixed", "tpch_w2"),
    "xp_io_w8": ("hybench_xp_io", "mixed", "xp_io_w4"),
}


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular JSON required: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


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


def cpus(value: Any) -> set[int]:
    result: set[int] = set()
    for component in str(value).split(","):
        left, separator, right = component.strip().partition("-")
        result.update(
            range(int(left), int(right) + 1) if separator else (int(left),)
        )
    return result


def storage_for(
    document: Mapping[str, Any], worker_id: str,
) -> dict[str, Any]:
    value = (document.get("storage_by_worker") or {}).get(worker_id)
    if not isinstance(value, Mapping):
        raise RuntimeError(f"storage placement absent: {worker_id}")
    return copy.deepcopy(dict(value))


def main() -> int:
    if OUTPUT_ROOT.exists() or OUTPUT_ROOT.is_symlink():
        raise RuntimeError(f"fresh output required: {OUTPUT_ROOT}")
    source = load(SOURCE)
    placements = load(PLACEMENTS)
    if (
        source.get("schema_version")
        != "mysql_four_scene_cpu_a_slots_four_worker_successor_v3"
        or len(source.get("workers") or []) != 12
        or placements.get("schema_version")
        != "mysql_four_scene_mixed_disk_four_worker_successor_v4"
        or len(placements.get("workers") or []) != 16
        or source.get("formal_scope") != "mysql_only"
        or source.get("cross_engine_comparison_prohibited") is not True
    ):
        raise RuntimeError("current resource predecessors differ")

    current = {
        str(row["worker_id"]): copy.deepcopy(dict(row))
        for row in source["workers"]
    }
    mixed = {
        str(row["worker_id"]): copy.deepcopy(dict(row))
        for row in placements["workers"]
    }
    workers = {
        worker_id: copy.deepcopy(current[worker_id])
        for worker_id in (
            *(f"xp_cpu_w{i}" for i in range(1, 5)),
            *(f"xp_io_w{i}" for i in range(1, 5)),
        )
    }
    storage = {
        worker_id: storage_for(source, worker_id) for worker_id in workers
    }
    for worker_id, (scene, source_kind, source_id) in EXPANSION.items():
        placement_document = source if source_kind == "current" else placements
        placement_rows = current if source_kind == "current" else mixed
        placement = placement_rows[source_id]
        prototype = current[
            "xp_cpu_w1" if scene == "hybench_xp_cpu" else "xp_io_w1"
        ]
        physical_source_id = str(
            placement.get("physical_placement_source_worker_id") or source_id
        )
        slot_storage = storage_for(placement_document, source_id)
        mount = str(slot_storage["mount"])
        if mount not in {"/disk1", "/disk2"}:
            raise RuntimeError(f"unsupported XP storage mount: {mount}")
        runtime_root = Path(str(
            placement.get("runtime_root")
            or Path(str(slot_storage["runtime_root"])) / source_id
        ))
        if Path(mount).resolve() not in runtime_root.resolve().parents:
            raise RuntimeError(f"{worker_id} physical runtime root differs")
        row = copy.deepcopy(prototype)
        row.update({
            "worker_id": worker_id,
            "scene": scene,
            "runtime_root": str(runtime_root),
            "datadir": str(runtime_root / "datadir"),
            "credential_defaults": str(
                runtime_root / "credentials/selected.cnf"
            ),
            "benchmark_defaults": str(
                runtime_root / "credentials/benchmark.cnf"
            ),
            "reassigned_from_completed_scene_worker_id": physical_source_id,
            "reassigned_from_current_logical_worker_id": (
                source_id if source_kind == "current" else None
            ),
            "physical_placement_source_worker_id": physical_source_id,
            "slot_source_kind": source_kind,
            "fresh_xp_template_reset_required": True,
            "completed_memory_result_credit_reused": False,
            "algorithm_a_candidate_or_qps_evidence_used": False,
        })
        for field in POSITION_FIELDS:
            row[field] = copy.deepcopy(placement[field])
        for field in RESOURCE_FIELDS:
            row[field] = copy.deepcopy(prototype[field])
        workers[worker_id] = row
        slot_storage.update({
            "runtime_root": str(runtime_root.parent),
            "copy_mode": "fresh_physical_xp_template_copy_no_reflink",
        })
        storage[worker_id] = slot_storage

    expected = {
        *(f"xp_cpu_w{i}" for i in range(1, 9)),
        *(f"xp_io_w{i}" for i in range(1, 9)),
    }
    if set(workers) != expected:
        raise RuntimeError("XP eight-worker identities differ")
    containers = [str(row["container"]) for row in workers.values()]
    ports = [int(row["host_port"]) for row in workers.values()]
    if len(set(containers)) != 16 or len(set(ports)) != 16:
        raise RuntimeError("XP container or port placement is not unique")
    seen_db: dict[int, str] = {}
    seen_client: dict[int, str] = {}
    mounts_by_scene: dict[str, dict[str, int]] = {
        scene: {"/disk1": 0, "/disk2": 0}
        for scene in ("hybench_xp_cpu", "hybench_xp_io")
    }
    for worker_id, row in workers.items():
        for cpu in cpus(row["database_cpuset"]):
            if cpu in seen_db:
                raise RuntimeError(
                    f"database CPU overlap {cpu}: {worker_id}/{seen_db[cpu]}"
                )
            seen_db[cpu] = worker_id
        for cpu in cpus(row["client_cpuset"]):
            if cpu in seen_client:
                raise RuntimeError(
                    f"client CPU overlap {cpu}: {worker_id}/{seen_client[cpu]}"
                )
            seen_client[cpu] = worker_id
        mounts_by_scene[str(row["scene"])][str(storage[worker_id]["mount"])] += 1
    if any(counts != {"/disk1": 4, "/disk2": 4} for counts in mounts_by_scene.values()):
        raise RuntimeError(f"XP per-scene SSD balance differs: {mounts_by_scene}")

    document = copy.deepcopy(source)
    document.update({
        "schema_version": "mysql_xp_eight_worker_cpu_a_slots_successor_v6",
        "status": "FROZEN_RUNTIME_READBACK_PENDING",
        "created_at": now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "formal_launch_authorized": False,
        "launch_performed": False,
        "active_scenes": ["hybench_xp_cpu", "hybench_xp_io"],
        "workers": [workers[key] for key in sorted(workers)],
        "storage_by_worker": storage,
        "predecessor_resource_freeze_path": str(SOURCE),
        "predecessor_resource_freeze_sha256": sha(SOURCE),
        "placement_resource_freeze_path": str(PLACEMENTS),
        "placement_resource_freeze_sha256": sha(PLACEMENTS),
        "completed_memory_scene_evidence": {
            "job_memory": "runtime_terminal_state_required_by_launcher",
            "tpch_sf4_memory": "runtime_terminal_state_required_by_launcher",
        },
        "algorithm_evidence_boundary": {
            "algorithm_a_candidate_injection": False,
            "algorithm_a_qps_used_for_generation_or_ranking": False,
            "algorithm_a_parameter_ranges_used": False,
            "resource_placement_only": True,
        },
        "full_parallel_successor": {
            "physical_workers_per_scene": 8,
            "maximum_concurrent_mysql_workers": 16,
            "disk1_workers_per_scene": 4,
            "disk2_workers_per_scene": 4,
            "disk1_total_workers": 8,
            "disk2_total_workers": 8,
            "four_scene_barrier": False,
            "sibling_scene_wait": False,
            "lease_mode": "none",
            "candidate_sampling": False,
            "transition_only_at_scene_boundary": True,
            "completed_history_preserved": True,
            "completed_candidate_replay_due_to_transition": 0,
        },
        "slot_reassignment": {
            worker_id: {
                "scene": scene,
                "source_kind": source_kind,
                "source_worker_id": source_id,
            }
            for worker_id, (scene, source_kind, source_id)
            in sorted(EXPANSION.items())
        },
    })
    # Four-scene v3 capacity descriptions are superseded by this XP-only
    # successor; templates, storage, workload seals, and envelope metadata are
    # intentionally inherited above.
    document.pop("xp_capacity_successor", None)
    document.pop("cpu_a_slots_successor", None)
    atomic(OUTPUT, document)
    print(json.dumps({
        "status": document["status"],
        "path": str(OUTPUT),
        "sha256": sha(OUTPUT),
        "workers": 16,
        "disk1_workers": 8,
        "disk2_workers": 8,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
