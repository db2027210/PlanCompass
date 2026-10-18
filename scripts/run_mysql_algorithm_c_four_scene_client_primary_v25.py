#!/usr/bin/env python3
"""Algorithm C v115 controller with nested measurement run scoping."""

from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_algorithm_c_measurement_campaign_root_v2 as campaign_root
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v24 as predecessor


CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_pg_latest_outer_20260907_v115/"
    "algorithm_c_pg_latest_outer_contract.json"
)
OPTIMIZATION_CONTRACT = ROOT / (
    "config/formal/frozen_mysql_four_scene_algorithm_c_client_primary_20260907_v115/"
    "outer_runtime_optimization.json"
)
COORDINATION_CONTRACT = ROOT / (
    "config/formal/frozen_mysql_cross_project_wave_coordination_20260907_v115/"
    "coordination_contract.json"
)
runtime = predecessor.runtime


def validate_contract() -> dict[str, Any]:
    contract = runtime.alg.load(CONTRACT)
    flow = dict(contract.get("outer_flow") or {})
    fix = dict(contract.get("nested_measurement_campaign_root") or {})
    if (
        contract.get("release_version") != "v115-nested-measurement-run-scope"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or flow.get("strict_higher_qps_acceptance") is not True
        or flow.get("two_rejections_before_incumbent_shrink") is not True
        or fix.get("nearest_outer_ancestor_parent_is_run_root") is not True
        or fix.get("cross_run_quarantine_collision_prohibited") is not True
        or fix.get("completed_outer_credit_changed") is not False
    ):
        raise RuntimeError("Algorithm C v115 lifecycle contract differs")
    for name, raw in dict(contract.get("source_bindings") or {}).items():
        row = dict(raw or {})
        path = (ROOT / str(row.get("path") or "")).resolve()
        if not path.is_file() or path.is_symlink() or runtime.alg.sha(path) != row.get("sha256"):
            raise RuntimeError(f"Algorithm C v115 source differs: {name}: {path}")
    return contract


def main() -> int:
    old = {
        "contract": predecessor.CONTRACT,
        "optimization": predecessor.OPTIMIZATION_CONTRACT,
        "coordination": predecessor.COORDINATION_CONTRACT,
        "validate": predecessor.validate_contract,
        "campaign_root": runtime.alg.measurement_campaign_root,
    }
    predecessor.CONTRACT = CONTRACT
    predecessor.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    predecessor.COORDINATION_CONTRACT = COORDINATION_CONTRACT
    predecessor.validate_contract = validate_contract
    runtime.alg.measurement_campaign_root = campaign_root.measurement_campaign_root
    try:
        return int(predecessor.main())
    finally:
        runtime.alg.measurement_campaign_root = old["campaign_root"]
        predecessor.validate_contract = old["validate"]
        predecessor.COORDINATION_CONTRACT = old["coordination"]
        predecessor.OPTIMIZATION_CONTRACT = old["optimization"]
        predecessor.CONTRACT = old["contract"]


if __name__ == "__main__":
    raise SystemExit(main())

