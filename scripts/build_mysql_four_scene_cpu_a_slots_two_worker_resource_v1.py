#!/usr/bin/env python3
"""Freeze eight disjoint two-worker C lanes from the completed CPU-A pool."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
V19 = (
    ROOT / "config/formal/frozen_mysql_four_scene_two_worker_20260826_v19"
    / "four_scene_effective_resource_successor.json"
)
MIXED = (
    ROOT / "config/formal/frozen_mysql_four_scene_mixed_disk_four_worker_20260827_v4"
    / "four_scene_effective_resource_successor.json"
)
CPU_A_STATE = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v843_to_a_cpu_round5_control2_resume_20260827_xp_cpu/algorithm_a_state.json"
)
CPU_A_BINDING = (
    CPU_A_STATE.parent / "round_010/wave_002/xp_resets"
    / "v843_to_a_cpu_round5_control2_resume_20260827_xp_cpu_round_010_wave_002_attempt_01"
    / "reset_binding.json"
)
OUTPUT = (
    ROOT / "config/formal/frozen_mysql_four_scene_cpu_a_slots_two_worker_20260828_v2"
    / "four_scene_effective_resource_successor.json"
)
MANAGEMENT_CPUSET = "484-487"
PLACEMENT = {
    "job_w1": "job_w1",
    "job_w2": "job_w3",
    "tpch_w1": "tpch_w1",
    "tpch_w2": "tpch_w3",
    "xp_cpu_w1": "xp_cpu_w1",
    "xp_cpu_w2": "xp_cpu_w2",
    "xp_io_w1": "xp_cpu_w3",
    "xp_io_w2": "xp_cpu_w4",
}
LIVE_SLOT = {
    "job_w1": "xp_cpu_w5", "job_w3": "xp_cpu_w7",
    "tpch_w1": "xp_cpu_w6", "tpch_w3": "xp_cpu_w8",
    "xp_cpu_w1": "xp_cpu_w1", "xp_cpu_w2": "xp_cpu_w2",
    "xp_cpu_w3": "xp_cpu_w3", "xp_cpu_w4": "xp_cpu_w4",
}
PLACEMENT_FIELDS = (
    "container", "host_port", "database_cpuset", "database_cpuset_mems",
    "client_cpuset", "client_cpuset_mems",
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
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


def cpus(value: str) -> set[int]:
    result: set[int] = set()
    for segment in value.split(","):
        start, separator, end = segment.strip().partition("-")
        result.update(range(int(start), int(end) + 1) if separator else [int(start)])
    return result


def main() -> int:
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise RuntimeError(f"fresh resource successor required: {OUTPUT}")
    base = load(V19)
    mixed = load(MIXED)
    state = load(CPU_A_STATE)
    binding = load(CPU_A_BINDING)
    if (
        base.get("schema_version") != "mysql_four_scene_two_worker_qps_successor_v19"
        or mixed.get("schema_version")
        != "mysql_four_scene_mixed_disk_four_worker_successor_v4"
        or state.get("status") != "COMPLETE"
        or state.get("stage") != "COMPLETE"
        or binding.get("status") != "PASS"
        or set(PLACEMENT) != {str(row["worker_id"]) for row in base.get("workers") or []}
    ):
        raise RuntimeError("CPU-A slot resource predecessors differ")
    base_rows = {str(row["worker_id"]): dict(row) for row in base["workers"]}
    donor_rows = {str(row["worker_id"]): dict(row) for row in mixed["workers"]}
    live_rows = {str(row["worker_id"]): dict(row) for row in binding["workers"]}
    if set(live_rows) != {f"xp_cpu_w{i}" for i in range(1, 9)}:
        raise RuntimeError("completed CPU-A physical pool differs")
    workers: list[dict[str, Any]] = []
    storage_by_worker: dict[str, dict[str, Any]] = {}
    cpu_sets: dict[str, set[int]] = {"management": cpus(MANAGEMENT_CPUSET)}
    for logical_id, donor_id in PLACEMENT.items():
        live_id = LIVE_SLOT[donor_id]
        target = copy.deepcopy(base_rows[logical_id])
        donor = donor_rows[donor_id]
        for field in PLACEMENT_FIELDS:
            target[field] = copy.deepcopy(donor[field])
        runtime_root = Path(str(
            donor.get("runtime_root")
            or Path(str((mixed.get("storage_by_worker") or {}).get(
                donor_id, mixed["storage"],
            )["runtime_root"])) / donor_id
        ))
        storage = copy.deepcopy(dict(
            (mixed.get("storage_by_worker") or {}).get(donor_id)
            or mixed["storage"]
        ))
        if logical_id not in {"xp_cpu_w1", "xp_cpu_w2"}:
            mount = Path(str(storage["mount"]))
            runtime_root = (
                mount / (
                    "resource_adaptive_mysql_algorithm_c_cpu_a_slots_v2"
                    if str(mount) == "/disk2"
                    else "resource_adaptive/mysql_algorithm_c_cpu_a_slots_v2"
                ) / logical_id
            )
        target.update({
            "runtime_root": str(runtime_root),
            "datadir": str(runtime_root / "datadir"),
            "credential_defaults": str(runtime_root / "credentials/selected.cnf"),
            "benchmark_defaults": str(runtime_root / "credentials/benchmark.cnf"),
            "reassigned_from_completed_cpu_a_worker_id": live_id,
            "reassigned_from_completed_cpu_a_container_id": live_rows[live_id]["container_id"],
            "fresh_scene_template_reset_required": donor_id not in {"xp_cpu_w1", "xp_cpu_w2"},
            "algorithm_a_measurement_credit_reused": False,
            "resource_envelope_source_worker_id": logical_id,
            "physical_placement_source_worker_id": donor_id,
        })
        storage["runtime_root"] = str(runtime_root.parent)
        storage_by_worker[logical_id] = storage
        cpu_sets[f"{logical_id}:database"] = cpus(str(target["database_cpuset"]))
        cpu_sets[f"{logical_id}:client"] = cpus(str(target["client_cpuset"]))
        workers.append(target)
    overlaps: list[dict[str, Any]] = []
    names = sorted(cpu_sets)
    for index, left in enumerate(names):
        for right in names[index + 1:]:
            shared = sorted(cpu_sets[left] & cpu_sets[right])
            if shared:
                overlaps.append({"left": left, "right": right, "cpus": shared})
    if overlaps:
        raise RuntimeError(f"CPU-A successor CPU placement overlaps: {overlaps}")
    result = copy.deepcopy(base)
    result.update({
        "schema_version": "mysql_four_scene_cpu_a_slots_two_worker_successor_v2",
        "status": "FROZEN_RUNTIME_READBACK_PENDING",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "formal_launch_authorized": False, "launch_performed": False,
        "management_cpuset": MANAGEMENT_CPUSET,
        "workers": sorted(workers, key=lambda row: str(row["worker_id"])),
        "storage_by_worker": storage_by_worker,
        "cpu_a_slots_successor": {
            "physical_workers_per_scene": 2,
            "maximum_concurrent_mysql_workers": 8,
            "disk1_worker_count": 4,
            "disk2_worker_count": 4,
            "scene_transitions_independent": True,
            "four_scene_barrier": False, "sibling_scene_wait": False,
            "lease_mode": "none", "candidate_sampling": False,
            "algorithm_a_measurement_credit_reused": False,
            "accepted_round_credit_increment": 0,
            "external_process_control_or_stop_performed": False,
            "postgresql_process_control_or_stop_performed": False
        },
        "source_bindings": {
            "v19_resource_freeze": {"path": str(V19.relative_to(ROOT)), "sha256": sha(V19)},
            "mixed_disk_placement_freeze": {"path": str(MIXED.relative_to(ROOT)), "sha256": sha(MIXED)},
            "completed_cpu_a_state": {"path": str(CPU_A_STATE.relative_to(ROOT)), "sha256": sha(CPU_A_STATE)},
            "completed_cpu_a_binding": {"path": str(CPU_A_BINDING.relative_to(ROOT)), "sha256": sha(CPU_A_BINDING)}
        },
    })
    atomic(OUTPUT, result)
    print(json.dumps({
        "status": result["status"], "output": str(OUTPUT),
        "workers": {row["worker_id"]: row["container"] for row in workers},
        "disk1_workers": 4, "disk2_workers": 4,
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
