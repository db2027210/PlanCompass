#!/usr/bin/env python3
"""Freeze the 2026-09-03 eight-worker client-primary MySQL envelope."""

from __future__ import annotations

import copy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / (
    "config/formal/frozen_mysql_four_scene_two_worker_20260826_v19/"
    "four_scene_effective_resource_successor.json"
)
OUT = ROOT / "config/formal/frozen_mysql_four_scene_two_worker_20260903_v20"
TARGET = OUT / "four_scene_effective_resource_successor.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic(path: Path, value: dict) -> None:
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
    if OUT.exists() or OUT.is_symlink():
        raise RuntimeError(f"fresh resource freeze required: {OUT}")
    document = json.loads(SOURCE.read_text(encoding="utf-8"))
    workers = copy.deepcopy(document["workers"])
    if len(workers) != 8 or {row["scene"] for row in workers} != {
        "job_memory", "tpch_sf4_memory", "hybench_xp_cpu", "hybench_xp_io",
    }:
        raise RuntimeError("v19 predecessor worker cohort differs")
    for row in workers:
        row.update({
            "memory_swappiness": 0,
            "effective_swap_policy": "anonymous_swap_disabled_swappiness_0",
            "kernel_memsw_controller_required": False,
            "host_swap_mutation_performed": False,
        })
    document.update({
        "schema_version": "mysql_four_scene_two_worker_client_primary_successor_v20",
        "status": "FROZEN_RUNTIME_READBACK_PENDING",
        "created_at": datetime.now().astimezone().isoformat(),
        "workers": workers,
        "container_owner_label": "mysql_four_scene_v5",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "predecessor_resource_freeze": {
            "path": str(SOURCE.relative_to(ROOT)), "sha256": sha(SOURCE),
        },
        "swap_isolation_successor": {
            "host_has_active_swap_device": True,
            "host_swap_state_must_not_be_mutated": True,
            "cgroup_v1_memsw_files_available": False,
            "docker_memory_swap_readback_may_be_minus_one": True,
            "every_mysql_container_memory_swappiness": 0,
            "strongest_practical_container_level_anonymous_swap_isolation": True,
        },
        "metric_semantics": {
            "job_memory": "full_113_query_suite_client_throughput",
            "tpch_sf4_memory": "full_q1_q22_five_lane_client_throughput",
            "hybench_xp_cpu": "native_hybench_xp_client_xap_qps",
            "hybench_xp_io": "native_hybench_xp_client_xap_qps",
            "xp_server_questions_qps": "audit_only",
        },
        "full_parallel_successor": {
            "new_a_b_c_formal_launch_starts_all_four_scenes_simultaneously": True,
            "physical_workers_per_scene": 2,
            "maximum_concurrent_mysql_workers": 8,
            "four_scene_barrier": False,
            "sibling_scene_wait": False,
            "cross_scene_completion_dependency": False,
            "lease_mode": "none",
            "candidate_sampling": False,
            "fixed_disjoint_cpu_sets": True,
        },
    })
    atomic(TARGET, document)
    print(json.dumps({"status": "FROZEN", "path": str(TARGET), "sha256": sha(TARGET)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
