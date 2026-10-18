#!/usr/bin/env python3
"""Freeze v81: bind the formal 2/2/4/4 per-scene worker layout."""

from __future__ import annotations

import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from scripts import build_mysql_algorithm_c_cpu_a_slots_four_worker_release_v3 as base


ROOT = base.ROOT
SOURCE = ROOT / "config/formal/frozen_mysql_four_scene_algorithm_c_cpu_a_slots_20260828_v36"
OUTPUT = ROOT / "config/formal/frozen_mysql_four_scene_algorithm_c_cpu_a_slots_20260828_v37"


def main() -> int:
    if OUTPUT.exists():
        raise RuntimeError(f"fresh release directory required: {OUTPUT}")
    OUTPUT.mkdir(parents=True, mode=0o700)
    optimization = base.load(SOURCE / "outer_runtime_optimization.json")
    optimization.update({"created_at": base.now(), "status": "FROZEN_RUNTIME_VALIDATED"})
    optimization_path = OUTPUT / "outer_runtime_optimization.json"
    base.atomic(optimization_path, optimization)

    updates = {
        "controller": base.binding("scripts/run_mysql_algorithm_c_async_scene_v81.py"),
        "launcher": base.binding("scripts/launch_mysql_algorithm_c_async_v81.py"),
        "mixed_parallel_r0_authority": base.binding(
            "scripts/run_mysql_four_scene_r0_cpu_a_slots_mixed_parallel_v1.py"
        ),
        "release_builder": base.binding(
            "scripts/build_mysql_algorithm_c_cpu_a_slots_four_worker_release_v6.py"
        ),
        "outer_runtime_optimization": base.binding(
            str(optimization_path.relative_to(ROOT))
        ),
    }
    parent = base.load(SOURCE / "algorithm_c_async_contract.json")
    parent.update({"created_at": base.now(), "status": "FROZEN_RUNTIME_VALIDATED"})
    parent_sources = base.refresh(parent.get("source_bindings") or {})
    parent_sources.update(updates)
    parent["source_bindings"] = parent_sources
    parent_path = OUTPUT / "algorithm_c_async_contract.json"
    base.atomic(parent_path, parent)

    contract = base.load(SOURCE / "global_monotonic_incumbent_contract.json")
    parallel = dict(contract.get("parallel_execution") or {})
    parallel.pop("physical_workers_per_scene_initial", None)
    parallel.update({
        "physical_workers_by_scene": {
            "job_memory": 2,
            "tpch_sf4_memory": 2,
            "hybench_xp_cpu": 4,
            "hybench_xp_io": 4,
        },
        "maximum_concurrent_mysql_workers": 12,
        "maximum_concurrent_mysql_workers_initial": 12,
        "four_scene_wave_barrier": False,
        "sibling_scene_wait": False,
        "shared_nvme_lease": False,
    })
    contract.update({
        "release_version": "v81",
        "created_at": base.now(),
        "status": "FROZEN_RUNTIME_VALIDATED",
        "parent_contract": base.binding(str(parent_path.relative_to(ROOT))),
        "parallel_execution": parallel,
        "scene_worker_cardinality_fix": {
            "schema_version": "mysql_algorithm_c_scene_worker_cardinality_v1",
            "memory_scenes_use_two_workers": True,
            "xp_scenes_use_four_workers": True,
            "candidate_batches_by_worker_count": {
                "2": [[1, 2], [3, 4], [5]],
                "4": [[1, 2, 3, 4], [5]],
            },
            "candidate_set_changed": False,
            "outer_policy_changed": False,
            "workload_or_resource_freeze_changed": False,
        },
    })
    sources = base.refresh(contract.get("source_bindings") or {})
    sources.update(updates)
    sources.update({
        "parent_contract": base.binding(str(parent_path.relative_to(ROOT))),
        "parent_optimization_contract": base.binding(
            str(optimization_path.relative_to(ROOT))
        ),
    })
    contract["source_bindings"] = sources
    contract_path = OUTPUT / "global_monotonic_incumbent_contract.json"
    base.atomic(contract_path, contract)
    print(json.dumps({
        "status": "FROZEN_RUNTIME_VALIDATED",
        "release_version": "v81",
        "global_contract": str(contract_path),
        "global_sha256": base.sha(contract_path),
        "parent_sha256": base.sha(parent_path),
        "optimization_sha256": base.sha(optimization_path),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
