#!/usr/bin/env python3
"""Freeze the completed JOB/TPC-H slots as eight workers per XP scene."""

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
    ROOT / "config/formal/frozen_mysql_four_scene_mixed_disk_four_worker_20260827_v4"
    / "four_scene_effective_resource_successor.json"
)
OUTPUT_ROOT = (
    ROOT / "config/formal/frozen_mysql_xp_eight_worker_20260827_v1"
)
OUTPUT = OUTPUT_ROOT / "xp_effective_resource_successor.json"

# Each completed memory slot keeps its container name, port, CPU/NUMA placement,
# and physical runtime root.  Only its per-database resource envelope and scene
# ownership change.  The resulting placement is four workers per SSD per scene.
REASSIGN = {
    "job_w1": ("xp_cpu_w5", "hybench_xp_cpu", "xp_cpu_w1"),
    "tpch_w1": ("xp_cpu_w6", "hybench_xp_cpu", "xp_cpu_w1"),
    "job_w3": ("xp_cpu_w7", "hybench_xp_cpu", "xp_cpu_w3"),
    "tpch_w3": ("xp_cpu_w8", "hybench_xp_cpu", "xp_cpu_w3"),
    "job_w2": ("xp_io_w5", "hybench_xp_io", "xp_io_w1"),
    "tpch_w2": ("xp_io_w6", "hybench_xp_io", "xp_io_w1"),
    "job_w4": ("xp_io_w7", "hybench_xp_io", "xp_io_w3"),
    "tpch_w4": ("xp_io_w8", "hybench_xp_io", "xp_io_w3"),
}

PLACEMENT_FIELDS = {
    "container", "host_port", "database_cpuset", "database_cpuset_mems",
    "client_cpuset", "client_cpuset_mems", "client_cpuset_remapped_for_actual_external_disjointness",
    "runtime_root", "datadir", "credential_defaults", "benchmark_defaults",
}
RESOURCE_FIELDS = {
    "cpu_quota_cores", "memory_bytes", "swap_bytes", "pids_max",
    "innodb_buffer_pool_bytes", "io_read_bps", "io_write_bps",
    "memory_alignment_bytes", "memory_request_predecessor_bytes",
    "v5_cpu_quota_cores", "v9_cpu_quota_cores", "v10_cpu_quota_cores",
    "v9_io_write_bps", "v15_io_write_bps", "v16_io_write_bps", "v17_io_write_bps",
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


def runtime_root(row: Mapping[str, Any], storage: Mapping[str, Any]) -> str:
    explicit = str(row.get("runtime_root") or "")
    return explicit or str(Path(str(storage["runtime_root"])) / str(row["worker_id"]))


def main() -> int:
    source = load(SOURCE)
    if (
        source.get("schema_version") != "mysql_four_scene_mixed_disk_four_worker_successor_v4"
        or source.get("formal_scope") != "mysql_only"
        or source.get("cross_engine_comparison_prohibited") is not True
        or len(source.get("workers") or []) != 16
    ):
        raise RuntimeError("mixed four-worker predecessor differs")
    if OUTPUT.exists() or OUTPUT_ROOT.is_symlink():
        raise RuntimeError(f"fresh output required: {OUTPUT_ROOT}")

    old_workers = {str(row["worker_id"]): dict(row) for row in source["workers"]}
    old_storage = copy.deepcopy(dict(source.get("storage_by_worker") or {}))
    result_workers: list[dict[str, Any]] = []
    result_storage: dict[str, dict[str, Any]] = {}

    # Existing XP w1-w4 retain their exact resource and physical data planes.
    for worker_id in (
        *(f"xp_cpu_w{i}" for i in range(1, 5)),
        *(f"xp_io_w{i}" for i in range(1, 5)),
    ):
        result_workers.append(copy.deepcopy(old_workers[worker_id]))
        result_storage[worker_id] = copy.deepcopy(old_storage[worker_id])

    for old_id, (new_id, scene, exemplar_id) in REASSIGN.items():
        slot = old_workers[old_id]
        exemplar = old_workers[exemplar_id]
        storage = copy.deepcopy(old_storage[old_id])
        row: dict[str, Any] = {
            key: copy.deepcopy(value)
            for key, value in slot.items() if key in PLACEMENT_FIELDS
        }
        row.update({
            key: copy.deepcopy(exemplar[key])
            for key in RESOURCE_FIELDS if key in exemplar
        })
        old_root = runtime_root(slot, storage)
        row.update({
            "worker_id": new_id,
            "scene": scene,
            "runtime_root": old_root,
            "datadir": str(Path(old_root) / "datadir"),
            "credential_defaults": str(Path(old_root) / "credentials/selected.cnf"),
            "benchmark_defaults": str(Path(old_root) / "credentials/benchmark.cnf"),
            "reassigned_from_completed_scene_worker_id": old_id,
            "reassigned_from_completed_scene": str(slot["scene"]),
            "completed_scene_result_credit_reused": False,
            "optimizer_state_credit_reused": False,
            "fresh_xp_template_reset_required": True,
            "resource_envelope_equal_to_existing_xp_workers": True,
        })
        result_workers.append(row)
        result_storage[new_id] = storage

    result_workers.sort(key=lambda row: str(row["worker_id"]))
    ids = {str(row["worker_id"]) for row in result_workers}
    expected = {
        *(f"xp_cpu_w{i}" for i in range(1, 9)),
        *(f"xp_io_w{i}" for i in range(1, 9)),
    }
    if ids != expected or set(result_storage) != expected:
        raise RuntimeError("eight-worker XP cardinality differs")

    document = copy.deepcopy(source)
    document.update({
        "schema_version": "mysql_xp_eight_worker_resource_successor_v1",
        "status": "FROZEN_RUNTIME_READBACK_PENDING",
        "created_at": now(),
        "active_scenes": [
            "job_memory", "tpch_sf4_memory",
            "hybench_xp_cpu", "hybench_xp_io",
        ],
        "workers": result_workers,
        "storage_by_worker": result_storage,
        "predecessor_resource_freeze_path": str(SOURCE),
        "predecessor_resource_freeze_sha256": sha(SOURCE),
        "accepted_rounds": {"hybench_xp_cpu": 0, "hybench_xp_io": 0},
        "formal_launch_authorized": False,
        "launch_performed": False,
        "successor_reason": "completed_JOB_and_TPCH_slots_reassigned_equally_to_XP",
        "completed_memory_scene_runtime_datadirs_reusable_as_results": False,
        "completed_memory_scene_result_artifacts_immutable": True,
        "completed_memory_scene_sealed_templates_unchanged": True,
        "runtime_transition": {
            "completed_round_history_preserved": True,
            "completed_candidate_measurements_replayed": False,
            "accepted_round_credit_increment": 0,
            "recalibration_required_due_only_to_worker_expansion": False,
            "transition_only_at_each_scene_completed_round_boundary": True,
            "cpu_and_io_transition_independent": True,
            "fresh_xp_template_reset_for_reassigned_slots": True,
        },
        "full_parallel_successor": {
            "physical_workers_per_scene": 8,
            "maximum_concurrent_mysql_workers": 16,
            "disk1_workers_per_scene": 4,
            "disk2_workers_per_scene": 4,
            "scene_rounds_advance_independently": True,
            "four_scene_barrier": False,
            "sibling_scene_wait": False,
            "lease_mode": "none",
            "candidate_sampling": False,
            "transition_only_at_each_scene_completed_round_boundary": True,
        },
        "replay_policy": {
            "algorithm_b_candidate_batches": [[1, 2, 3, 4, 5]],
            "algorithm_b_effective_replay_parallelism": 5,
            "algorithm_a_candidate_batches": [[1, 2, 3, 4, 5, 6, 7, 8], [9, 10, 11, 12, 13, 14, 15, 16]],
            "algorithm_a_effective_replay_parallelism": 8,
            "independent_mysql_workers_per_scene": 8,
            "same_mysql_instance_parallel_candidate_replay_prohibited": True,
            "candidate_sampling": False,
        },
        "slot_reassignment": {
            "source_scenes": ["job_memory", "tpch_sf4_memory"],
            "source_scenes_complete_before_reassignment": True,
            "mapping": {
                old_id: {"worker_id": value[0], "scene": value[1]}
                for old_id, value in sorted(REASSIGN.items())
            },
            "disk1_active_workers": 8,
            "disk2_active_workers": 8,
            "total_active_workers": 16,
        },
    })
    # The predecessor's four-scene mixed-disk block is no longer authoritative.
    document.pop("mixed_disk_successor", None)
    atomic(OUTPUT, document)
    print(json.dumps({
        "status": "PASS", "output": str(OUTPUT), "sha256": sha(OUTPUT),
        "workers": len(result_workers),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
