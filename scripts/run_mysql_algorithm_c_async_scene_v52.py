#!/usr/bin/env python3
"""Run asynchronous MySQL Algorithm C with scene-specific XP Top-4 sets."""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_c_async_scene_v51 as v51


runtime = v51.runtime
alg = runtime.alg
FREEZE_ROOT = (
    ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_c_20260825_v1"
)
ASYNC_CONTRACT = FREEZE_ROOT / "algorithm_c_async_contract.json"
TABLE_ROOT = FREEZE_ROOT
TABLES = {
    "job_memory": "T.job_memory.operator_physics_v1.json",
    "tpch_sf4_memory": "T.tpch_memory.operator_physics_v1.json",
    "hybench_xp_cpu": "T.hybench_xp_cpu.top4.operator_physics_v1.json",
    "hybench_xp_io": "T.hybench_xp_io.top4.operator_physics_v1.json",
}
EXPECTED_ACTIVE = {
    "hybench_xp_cpu": [
        "row_evaluate_cost",
        "key_compare_cost",
        "disk_temptable_row_cost",
        "io_block_read_cost",
    ],
    "hybench_xp_io": [
        "io_block_read_cost",
        "disk_temptable_row_cost",
        "disk_temptable_create_cost",
        "memory_temptable_row_cost",
    ],
}


def validate_contract() -> dict[str, Any]:
    if not ASYNC_CONTRACT.is_file() or ASYNC_CONTRACT.is_symlink():
        raise RuntimeError(f"sealed asynchronous C v52 contract absent: {ASYNC_CONTRACT}")
    contract = alg.load(ASYNC_CONTRACT)
    successor = contract.get("xp_top4_parameter_successor") or {}
    if (
        contract.get("release_version") != "v52"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("four_scene_wave_barrier") is not False
        or contract.get("per_scene_outer_state_machine") is not True
        or contract.get("scene_may_advance_immediately") is not True
        or contract.get("candidate_batches") != [[1, 2], [3, 4], [5]]
        or contract.get("post_search_stability_confirmation") is not False
        or successor.get("active_parameters_by_scene") != EXPECTED_ACTIVE
        or successor.get("candidate_subset_count_by_scene")
        != {"hybench_xp_cpu": 15, "hybench_xp_io": 15}
        or successor.get("real_top_k_limit") != 5
        or successor.get("algorithm_c_outer_flow_changed") is not False
        or successor.get("fresh_search_required") is not True
    ):
        raise RuntimeError("v52 XP Top-4 Algorithm C contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        if not isinstance(binding, Mapping):
            raise RuntimeError("v52 source binding is not an object")
        path = ROOT / str(binding["path"])
        if (
            not path.is_file()
            or path.is_symlink()
            or alg.sha(path) != str(binding["sha256"])
        ):
            raise RuntimeError(f"v52 Algorithm C binding differs: {path}")
    for scene, expected in EXPECTED_ACTIVE.items():
        table = alg.load(TABLE_ROOT / TABLES[scene])
        space = table.get("parameter_space") or {}
        if (
            table.get("scenario") != scene
            or space.get("active_params") != expected
            or list(space.get("ranges") or {}) != sorted(expected)
            or set(table.get("table") or {}) != set(expected)
            or set(table.get("effect_evidence") or {}) != set(expected)
        ):
            raise RuntimeError(f"v52 {scene} Top-4 table differs")
    return contract


def install() -> None:
    # Retain every v51 runtime/outer-search fix, then change only the frozen
    # experience-table routing used to construct the parameter candidate set.
    v51.validate_contract()
    v51.v50.v49.validate_v65_wave_coordination_contract()
    v51.install()
    alg.TABLE_ROOT = TABLE_ROOT
    alg.TABLES = copy.deepcopy(TABLES)

    original_state_document = v51.v50.v49.v28.ORIGINAL_V27_STATE_DOCUMENT

    def top4_state_document(**kwargs: Any) -> dict[str, Any]:
        document = copy.deepcopy(original_state_document(**kwargs))
        document["xp_top4_parameter_release"] = {
            "release_version": "v52",
            "contract_path": str(ASYNC_CONTRACT),
            "contract_sha256": alg.sha(ASYNC_CONTRACT),
            "active_parameters_by_scene": copy.deepcopy(EXPECTED_ACTIVE),
            "candidate_subset_count_by_scene": {
                "hybench_xp_cpu": 15,
                "hybench_xp_io": 15,
            },
            "real_top_k_limit": 5,
            "fresh_search_required": True,
            "algorithm_c_outer_flow_changed": False,
        }
        return document

    v51.v50.v49.v28.ORIGINAL_V27_STATE_DOCUMENT = top4_state_document


def main() -> int:
    validate_contract()
    install()
    return v51.v50.v49.v45.v34.main()


if __name__ == "__main__":
    raise SystemExit(main())
