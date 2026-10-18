#!/usr/bin/env python3
"""Run C v81 with scene-aware 2/2/4/4 worker cardinality."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_c_async_scene_v80 as predecessor
from scripts import run_mysql_four_scene_r0_cpu_a_slots_mixed_parallel_v1 as mixed_r0


CONTRACT_ROOT = (
    ROOT / "config/formal/frozen_mysql_four_scene_algorithm_c_cpu_a_slots_20260828_v37"
)
PARENT_CONTRACT = CONTRACT_ROOT / "algorithm_c_async_contract.json"
OPTIMIZATION_CONTRACT = CONTRACT_ROOT / "outer_runtime_optimization.json"
CONTRACT = CONTRACT_ROOT / "global_monotonic_incumbent_contract.json"
SCENE_WORKERS = {
    "job_memory": 2,
    "tpch_sf4_memory": 2,
    "hybench_xp_cpu": 4,
    "hybench_xp_io": 4,
}


def mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} object required")
    return dict(value)


def cli_value(name: str) -> str:
    try:
        value = sys.argv[sys.argv.index(name) + 1]
    except (ValueError, IndexError) as error:
        raise RuntimeError(f"required controller argument absent: {name}") from error
    if not value or value.startswith("--"):
        raise RuntimeError(f"required controller argument differs: {name}")
    return value


def validate_contract() -> dict[str, Any]:
    alg = predecessor.coverage_runtime.alg
    parent = alg.load(PARENT_CONTRACT)
    optimization = alg.load(OPTIMIZATION_CONTRACT)
    contract = alg.load(CONTRACT)
    parallel = mapping(contract.get("parallel_execution"), "parallel execution")
    if (
        parent.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or optimization.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("release_version") != "v81"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("parent_contract", {}).get("sha256") != alg.sha(PARENT_CONTRACT)
        or parallel.get("physical_workers_by_scene") != SCENE_WORKERS
        or parallel.get("maximum_concurrent_mysql_workers") != 12
        or parallel.get("four_scene_wave_barrier") is not False
        or parallel.get("sibling_scene_wait") is not False
        or parallel.get("shared_nvme_lease") is not False
    ):
        raise RuntimeError("Algorithm C v81 mixed cardinality contract differs")
    for document, label in ((parent, "parent"), (contract, "successor")):
        for name, raw in mapping(document.get("source_bindings"), f"{label} bindings").items():
            row = mapping(raw, f"{label} {name}")
            path = (ROOT / str(row.get("path") or "")).resolve()
            if not path.is_file() or path.is_symlink() or alg.sha(path) != row.get("sha256"):
                raise RuntimeError(f"Algorithm C v81 source binding differs: {name}")
    return {"parent": parent, "optimization": optimization, "successor": contract}


def main() -> int:
    validate_contract()
    scene = cli_value("--scene")
    if scene not in SCENE_WORKERS:
        raise RuntimeError(f"unsupported Algorithm C scene: {scene}")
    worker_count = SCENE_WORKERS[scene]
    batches = ((1, 2, 3, 4), (5,)) if worker_count == 4 else ((1, 2), (3, 4), (5,))

    v79 = predecessor.predecessor
    v78 = v79.predecessor
    v77 = v78.predecessor
    v76 = v77.predecessor
    c74 = v76.predecessor
    v73 = c74.predecessor
    original_c74_main = c74.main
    old_r0 = v76.r0_four

    def scene_aware_c74_main() -> int:
        old = {
            "alg_batches": v73.alg.FORMAL_CANDIDATE_BATCHES,
            "runtime_workers": v73.runtime.INDEPENDENT_SCENE_WORKER_COUNT,
            "runtime_batches": v73.runtime.FORMAL_CANDIDATE_BATCHES,
            "resume_workers": v73.runtime.predecessor.FROZEN_WORKERS_PER_SCENE,
        }
        v73.alg.FORMAL_CANDIDATE_BATCHES = batches
        v73.runtime.INDEPENDENT_SCENE_WORKER_COUNT = worker_count
        v73.runtime.FORMAL_CANDIDATE_BATCHES = batches
        v73.runtime.predecessor.FROZEN_WORKERS_PER_SCENE = worker_count
        try:
            return int(original_c74_main())
        finally:
            v73.alg.FORMAL_CANDIDATE_BATCHES = old["alg_batches"]
            v73.runtime.INDEPENDENT_SCENE_WORKER_COUNT = old["runtime_workers"]
            v73.runtime.FORMAL_CANDIDATE_BATCHES = old["runtime_batches"]
            v73.runtime.predecessor.FROZEN_WORKERS_PER_SCENE = old["resume_workers"]

    v76.r0_four = mixed_r0
    c74.main = scene_aware_c74_main
    try:
        return int(predecessor.main())
    finally:
        v76.r0_four = old_r0
        c74.main = original_c74_main


if __name__ == "__main__":
    raise SystemExit(main())
