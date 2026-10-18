#!/usr/bin/env python3
"""v144 scene-independent search with SHA-bound formal R0 reuse."""

from __future__ import annotations

from pathlib import Path
import sys
import types
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_c_four_scene_client_primary_v53 as predecessor
from scripts import mysql_algorithm_c_measurement_campaign_root_v3 as old_campaign_root
from scripts import mysql_algorithm_c_measurement_campaign_root_v4 as campaign_root
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v48 as old_runtime
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v49 as execution_controller
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v50 as runtime
from scripts import mysql_algorithm_c_measurement_client_primary_v4 as old_measurement
from scripts import mysql_algorithm_c_measurement_client_primary_v5 as fixed_measurement

FREEZE = predecessor.FREEZE
FREEZE_SCHEMA = predecessor.FREEZE_SCHEMA
OPTIMIZATION_CONTRACT = ROOT / "config/formal/frozen_mysql_four_scene_algorithm_c_client_primary_20260923_v144/outer_runtime_optimization.json"
CONTRACT = ROOT / "config/formal/frozen_mysql_algorithm_c_paper_baseline_20260923_v144/algorithm_c_paper_baseline_contract.json"
PARENT = predecessor.CONTRACT


def validate_contract() -> dict[str, Any]:
    contract = runtime.alg.load(CONTRACT)
    if (
        contract.get("release_version") != "v144-formal-r0-tpch-normalization-v30"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("candidate_search_requires_paper_baseline_pass") is not True
        or contract.get("tolerance_percentage_points") != 1.0
        or contract.get("resource_freeze", {}).get("sha256") != runtime.alg.sha(FREEZE)
        or contract.get("parent_contract", {}).get("sha256") != runtime.alg.sha(PARENT)
    ):
        raise RuntimeError("Algorithm C v144 formal R0 reuse contract differs")
    for name, raw in dict(contract.get("source_bindings") or {}).items():
        path = ROOT / str(raw.get("path") or "")
        if path.is_symlink() or not path.is_file() or runtime.alg.sha(path) != raw.get("sha256"):
            raise RuntimeError(f"Algorithm C v144 source differs: {name}: {path}")
    return contract


def module_graph() -> list[types.ModuleType]:
    pending = [execution_controller]
    seen: set[int] = set()
    modules: list[types.ModuleType] = []
    while pending:
        module = pending.pop()
        if id(module) in seen:
            continue
        seen.add(id(module))
        modules.append(module)
        for name in ("predecessor", "optimization_controller", "prestart_controller", "gate_controller", "campaign_controller"):
            child = getattr(module, name, None)
            if isinstance(child, types.ModuleType):
                pending.append(child)
    return modules


def runtime_owners(modules: list[types.ModuleType]) -> list[types.ModuleType]:
    owners = [module for module in modules if getattr(module, "runtime", None) is old_runtime]
    if not owners:
        raise RuntimeError("Algorithm C v144 runtime owner graph differs")
    return owners


def main() -> int:
    predecessor.predecessor.predecessor.predecessor.validate_contract()
    predecessor.predecessor.predecessor.validate_contract()
    predecessor.predecessor.validate_contract()
    predecessor.validate_contract()
    validate_contract()
    modules = module_graph()
    owners = runtime_owners(modules)
    campaign_owners = [
        module for module in modules
        if getattr(module, "campaign_root", None) is old_campaign_root
    ]
    if not campaign_owners:
        raise RuntimeError("Algorithm C v144 campaign-root owner graph differs")
    previous_optimization = execution_controller.OPTIMIZATION_CONTRACT
    for owner in owners:
        owner.runtime = runtime
    for owner in campaign_owners:
        owner.campaign_root = campaign_root
    execution_controller.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    old_measure_batch = old_measurement.measure_batch
    old_measurement.measure_batch = fixed_measurement.measure_batch
    try:
        return int(execution_controller.main())
    finally:
        old_measurement.measure_batch = old_measure_batch
        execution_controller.OPTIMIZATION_CONTRACT = previous_optimization
        for owner in campaign_owners:
            owner.campaign_root = old_campaign_root
        for owner in owners:
            owner.runtime = old_runtime


if __name__ == "__main__":
    raise SystemExit(main())
