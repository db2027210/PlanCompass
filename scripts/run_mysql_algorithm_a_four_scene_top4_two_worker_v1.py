#!/usr/bin/env python3
"""Run any formal scene with two-worker Top4 Algorithm A."""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_a_xp_top4_two_worker_v1 as predecessor


CONTRACT_ROOT = ROOT / "config/formal/frozen_mysql_four_scene_top4_algorithm_a_20260826_v12"
CONTRACT = CONTRACT_ROOT / "algorithm_a_top4_contract.json"
COORDINATION = CONTRACT_ROOT / "coordination_contract.json"
TABLES = {
    "job_memory": "T.job_memory.operator_physics_v1.json",
    "tpch_sf4_memory": "T.tpch_memory.operator_physics_v1.json",
    "hybench_xp_cpu": "T.hybench_xp_cpu.top4.operator_physics_v1.json",
    "hybench_xp_io": "T.hybench_xp_io.top4.operator_physics_v1.json",
}
EXPECTED = {
    "job_memory": {"job_w1", "job_w2"},
    "tpch_sf4_memory": {"tpch_w1", "tpch_w2"},
    "hybench_xp_cpu": {"xp_cpu_w1", "xp_cpu_w2"},
    "hybench_xp_io": {"xp_io_w1", "xp_io_w2"},
}


def validate_contract(scene: str, active: Sequence[str]) -> dict[str, Any]:
    contract = predecessor.predecessor.load(CONTRACT)
    scene_contract = (contract.get("scenes") or {}).get(scene) or {}
    base = predecessor.base
    if (
        contract.get("schema_version")
        != "mysql_algorithm_a_four_scene_top4_two_worker_contract_v1"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("algorithm") != "A"
        or contract.get("method") != base.METHOD
        or contract.get("candidate_search_contract") != base.SEARCH_CONTRACT
        or contract.get("accepted_target") != base.TARGET_ACCEPTED
        or contract.get("normalized_width_threshold") != base.NORMALIZED_WIDTH_THRESHOLD
        or contract.get("physical_replay_parallelism_per_scene") != 2
        or contract.get("candidate_batches")
        != [[1, 2], [3, 4], [5, 6], [7, 8], [9, 10], [11, 12], [13, 14], [15, 16]]
        or contract.get("four_scene_wave_barrier") is not False
        or contract.get("sibling_scene_wait") is not False
        or contract.get("lease_mode") != "none"
        or contract.get("post_convergence_stability_required") is not False
        or scene_contract.get("active_parameters") != list(active)
        or scene_contract.get("candidates_per_round") != 16
        or scene_contract.get("physical_waves_per_round") != 8
    ):
        raise RuntimeError("four-scene Top4-A two-worker contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        path = ROOT / str(binding.get("path") or "")
        if path.is_symlink() or not path.is_file() or predecessor.predecessor.sha(path) != binding.get("sha256"):
            raise RuntimeError(f"four-scene Top4-A binding differs: {path}")
    return contract


def install() -> None:
    predecessor.CONTRACT_ROOT = CONTRACT_ROOT
    predecessor.CONTRACT = CONTRACT
    predecessor.COORDINATION = COORDINATION
    predecessor.EXPECTED = copy.deepcopy(EXPECTED)
    predecessor.validate_contract = validate_contract
    predecessor.predecessor.TOP4_TABLES = copy.deepcopy(TABLES)


def main() -> int:
    install()
    return int(predecessor.main())


if __name__ == "__main__":
    raise SystemExit(main())
