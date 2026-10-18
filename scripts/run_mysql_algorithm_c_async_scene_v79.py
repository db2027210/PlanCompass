#!/usr/bin/env python3
"""Run C v79 and bind every legacy boundary alias to paired runtime v3."""

from __future__ import annotations

from pathlib import Path
import sys
from types import ModuleType
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_c_async_scene_v78 as predecessor
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v3 as paired_runtime
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v4 as boundary_v4


CONTRACT_ROOT = (
    ROOT / "config/formal/frozen_mysql_four_scene_algorithm_c_cpu_a_slots_20260828_v31"
)
PARENT_CONTRACT = CONTRACT_ROOT / "algorithm_c_async_contract.json"
OPTIMIZATION_CONTRACT = CONTRACT_ROOT / "outer_runtime_optimization.json"
CONTRACT = CONTRACT_ROOT / "global_monotonic_incumbent_contract.json"


def mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} object required")
    return dict(value)


def validate_contract() -> dict[str, Any]:
    alg = paired_runtime.alg
    parent = alg.load(PARENT_CONTRACT)
    optimization = alg.load(OPTIMIZATION_CONTRACT)
    contract = alg.load(CONTRACT)
    paired = mapping(
        contract.get("lane_paired_incumbent_revalidation"), "paired policy"
    )
    alias = mapping(contract.get("runtime_alias_binding"), "alias binding")
    if (
        parent.get("release_version") != "v73"
        or parent.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or parent.get("formal_scope") != "mysql_only"
        or parent.get("four_scene_wave_barrier") is not False
        or parent.get("sibling_scene_wait") is not False
        or optimization.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("release_version") != "v79"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("parent_contract", {}).get("sha256")
        != alg.sha(PARENT_CONTRACT)
        or paired.get("schema_version")
        != paired_runtime.LANE_PAIRED_REVALIDATION_SCHEMA
        or paired.get("same_physical_worker_ratio") is not True
        or paired.get("control_measurements_grant_no_state_credit") is not True
        or alias.get("all_loaded_legacy_boundary_runtime_aliases_rebound") is not True
        or alias.get("v4_original_callbacks_rebound") is not True
    ):
        raise RuntimeError("Algorithm C v79 paired runtime contract differs")
    for document, label in ((parent, "parent"), (contract, "successor")):
        for name, raw in mapping(
            document.get("source_bindings"), f"{label} bindings"
        ).items():
            row = mapping(raw, f"{label} {name}")
            path = (ROOT / str(row.get("path") or "")).resolve()
            if (
                not path.is_file()
                or path.is_symlink()
                or alg.sha(path) != row.get("sha256")
            ):
                raise RuntimeError(f"Algorithm C v79 source binding differs: {name}")
    return {"parent": parent, "optimization": optimization, "successor": contract}


def main() -> int:
    validate_contract()
    old_runtime = boundary_v4.runtime
    rebound: list[tuple[ModuleType, Any]] = []
    for module in tuple(sys.modules.values()):
        if (
            isinstance(module, ModuleType)
            and str(getattr(module, "__name__", "")).startswith(
                "scripts.run_mysql_four_scene_algorithm_c_boundary_resume_v"
            )
            and getattr(module, "runtime", None) is old_runtime
        ):
            rebound.append((module, old_runtime))
            module.runtime = paired_runtime
    old = {
        "root": predecessor.CONTRACT_ROOT,
        "parent": predecessor.PARENT_CONTRACT,
        "optimization": predecessor.OPTIMIZATION_CONTRACT,
        "contract": predecessor.CONTRACT,
        "validate": predecessor.validate_contract,
        "v4_state": boundary_v4.ORIGINAL_STATE_DOCUMENT,
        "v4_partial": boundary_v4.ORIGINAL_PARTIAL_VALIDATOR,
        "v4_wave": boundary_v4.ORIGINAL_WAVE_VALIDATOR,
    }
    predecessor.CONTRACT_ROOT = CONTRACT_ROOT
    predecessor.PARENT_CONTRACT = PARENT_CONTRACT
    predecessor.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    predecessor.CONTRACT = CONTRACT
    predecessor.validate_contract = validate_contract
    boundary_v4.ORIGINAL_STATE_DOCUMENT = paired_runtime._state_document
    boundary_v4.ORIGINAL_PARTIAL_VALIDATOR = (
        paired_runtime._validate_formal_partial_continuation
    )
    boundary_v4.ORIGINAL_WAVE_VALIDATOR = (
        paired_runtime._validate_wave_coordination_contract
    )
    try:
        return int(predecessor.main())
    finally:
        predecessor.CONTRACT_ROOT = old["root"]
        predecessor.PARENT_CONTRACT = old["parent"]
        predecessor.OPTIMIZATION_CONTRACT = old["optimization"]
        predecessor.CONTRACT = old["contract"]
        predecessor.validate_contract = old["validate"]
        boundary_v4.ORIGINAL_STATE_DOCUMENT = old["v4_state"]
        boundary_v4.ORIGINAL_PARTIAL_VALIDATOR = old["v4_partial"]
        boundary_v4.ORIGINAL_WAVE_VALIDATOR = old["v4_wave"]
        for module, runtime in rebound:
            module.runtime = runtime


if __name__ == "__main__":
    raise SystemExit(main())
