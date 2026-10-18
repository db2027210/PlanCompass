#!/usr/bin/env python3
"""Build the immutable XP Top-4 Algorithm C v52 release artifacts."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = (
    ROOT / "config/formal/frozen_mysql_operator_physics_experience_tables_20260724_v1"
)
SOURCE_XP = SOURCE_ROOT / "T.hybench_xp_io.operator_physics_v1.json"
SOURCE_JOB = SOURCE_ROOT / "T.job_memory.operator_physics_v1.json"
SOURCE_TPCH = SOURCE_ROOT / "T.tpch_memory.operator_physics_v1.json"
PREDECESSOR_CONTRACT = (
    ROOT / "config/formal/frozen_mysql_async_outer_20260820_v39"
    / "algorithm_c_async_contract.json"
)
OUTPUT = ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_c_20260825_v1"
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_async_scene_v52.py"
LAUNCHER = ROOT / "scripts/launch_mysql_algorithm_c_async_v52.py"
ACTIVE = {
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
TABLE_NAMES = {
    "hybench_xp_cpu": "T.hybench_xp_cpu.top4.operator_physics_v1.json",
    "hybench_xp_io": "T.hybench_xp_io.top4.operator_physics_v1.json",
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def binding(path: Path) -> dict[str, str]:
    return {"path": str(path.relative_to(ROOT)), "sha256": sha(path)}


def main() -> int:
    if OUTPUT.exists() and any(OUTPUT.iterdir()):
        raise RuntimeError(f"refusing to overwrite frozen release: {OUTPUT}")
    OUTPUT.mkdir(parents=True, exist_ok=True)

    parameter_contract_path = OUTPUT / "parameter_contract.json"
    parameter_contract = {
        "schema_version": "mysql_xp_scene_specific_top4_parameter_contract_v1",
        "version": "frozen_mysql_xp_top4_parameters_20260825_v1",
        "status": "FROZEN_RUNTIME_VALIDATED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "active_parameters_by_scene": copy.deepcopy(ACTIVE),
        "selection_basis": {
            "method": "largest_absolute_frozen_target_resource_sensitivity",
            "source_path": str(SOURCE_XP.relative_to(ROOT)),
            "source_sha256": sha(SOURCE_XP),
            "target_resource_by_scene": {
                "hybench_xp_cpu": "cpu",
                "hybench_xp_io": "io",
            },
            "absolute_sensitivity_by_scene": {
                "hybench_xp_cpu": {
                    "row_evaluate_cost": 0.28,
                    "key_compare_cost": 0.16,
                    "disk_temptable_row_cost": 0.08,
                    "io_block_read_cost": 0.08,
                },
                "hybench_xp_io": {
                    "io_block_read_cost": 0.416,
                    "disk_temptable_row_cost": 0.39,
                    "disk_temptable_create_cost": 0.325,
                    "memory_temptable_row_cost": 0.286,
                },
            },
        },
        "candidate_subset_count_by_scene": {
            "hybench_xp_cpu": 15,
            "hybench_xp_io": 15,
        },
        "real_top_k_limit": 5,
        "fresh_search_required": True,
        "predecessor_seven_parameter_evidence_reused": False,
        "algorithm_c_outer_flow_changed": False,
        "workload_or_resource_envelope_changed": False,
    }
    atomic(parameter_contract_path, parameter_contract)

    source = load(SOURCE_XP)
    table_paths: dict[str, Path] = {}
    for scene, active in ACTIVE.items():
        table = copy.deepcopy(source)
        table["scenario"] = scene
        table["version"] = f"{scene}.top4.operator_physics_T_v1"
        table["parameter_space"] = {
            "active_params": copy.deepcopy(active),
            "ranges": {
                name: copy.deepcopy(source["parameter_space"]["ranges"][name])
                for name in sorted(active)
            },
            "sha256": sha(parameter_contract_path),
            "version": parameter_contract["version"],
        }
        table["effect_evidence"] = {
            name: copy.deepcopy(source["effect_evidence"][name])
            for name in sorted(active)
        }
        table["table"] = {
            name: copy.deepcopy(source["table"][name]) for name in sorted(active)
        }
        table["provenance"] = copy.deepcopy(source["provenance"])
        table["provenance"]["top4_parameter_contract_path"] = str(
            parameter_contract_path.relative_to(ROOT)
        )
        table["provenance"]["top4_parameter_contract_sha256"] = sha(
            parameter_contract_path
        )
        path = OUTPUT / TABLE_NAMES[scene]
        atomic(path, table)
        table_paths[scene] = path

    for source_path, name in (
        (SOURCE_JOB, "T.job_memory.operator_physics_v1.json"),
        (SOURCE_TPCH, "T.tpch_memory.operator_physics_v1.json"),
    ):
        atomic(OUTPUT / name, load(source_path))

    contract = load(PREDECESSOR_CONTRACT)
    contract["release_version"] = "v52"
    contract["released_at"] = "2026-08-25T12:00:00+08:00"
    contract["xp_top4_parameter_successor"] = {
        "parameter_contract": binding(parameter_contract_path),
        "active_parameters_by_scene": copy.deepcopy(ACTIVE),
        "candidate_subset_count_by_scene": {
            "hybench_xp_cpu": 15,
            "hybench_xp_io": 15,
        },
        "real_top_k_limit": 5,
        "fresh_search_required": True,
        "predecessor_seven_parameter_evidence_reused": False,
        "algorithm_c_outer_flow_changed": False,
        "candidate_batches_unchanged": [[1, 2], [3, 4], [5]],
        "accepted_target_unchanged": 10,
        "post_search_stability_confirmation": False,
    }
    source_bindings = copy.deepcopy(contract["source_bindings"])
    source_bindings["predecessor_controller_v51"] = binding(
        ROOT / "scripts/run_mysql_algorithm_c_async_scene_v51.py"
    )
    source_bindings["controller"] = binding(CONTROLLER)
    source_bindings["launcher"] = binding(LAUNCHER)
    source_bindings["xp_top4_parameter_contract"] = binding(
        parameter_contract_path
    )
    source_bindings["xp_cpu_top4_table"] = binding(
        table_paths["hybench_xp_cpu"]
    )
    source_bindings["xp_io_top4_table"] = binding(
        table_paths["hybench_xp_io"]
    )
    source_bindings["job_table_unchanged_copy"] = binding(
        OUTPUT / "T.job_memory.operator_physics_v1.json"
    )
    source_bindings["tpch_table_unchanged_copy"] = binding(
        OUTPUT / "T.tpch_memory.operator_physics_v1.json"
    )
    contract["source_bindings"] = source_bindings
    atomic(OUTPUT / "algorithm_c_async_contract.json", contract)
    print(OUTPUT / "algorithm_c_async_contract.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
