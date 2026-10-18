#!/usr/bin/env python3
"""Algorithm C v114 controller with live-authority kernel gate binding."""

from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_c_external_storage_inventory_only_v6 as kernel_preflight
from scripts import mysql_cross_project_wave_coordination_v29 as kernel_gate
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v23 as predecessor


CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_pg_latest_outer_20260907_v114/"
    "algorithm_c_pg_latest_outer_contract.json"
)
OPTIMIZATION_CONTRACT = ROOT / (
    "config/formal/frozen_mysql_four_scene_algorithm_c_client_primary_20260907_v114/"
    "outer_runtime_optimization.json"
)
COORDINATION_CONTRACT = ROOT / (
    "config/formal/frozen_mysql_cross_project_wave_coordination_20260907_v114/"
    "coordination_contract.json"
)
runtime = predecessor.runtime
v21 = predecessor.predecessor.predecessor


def validate_contract() -> dict[str, Any]:
    contract = runtime.alg.load(CONTRACT)
    flow = dict(contract.get("outer_flow") or {})
    gate = dict(contract.get("live_authority_kernel_gate") or {})
    if (
        contract.get("release_version") != "v114-live-authority-kernel-gate"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or flow.get("strict_higher_qps_acceptance") is not True
        or flow.get("two_rejections_before_incumbent_shrink") is not True
        or gate.get("prepared_binding_source")
        != "MYSQL_TWO_WORKER_AUTHORITY_ROOT/prepared_binding.json"
        or gate.get("hardcoded_prepared_binding_prohibited") is not True
        or gate.get("resource_readback_fail_closed") is not True
    ):
        raise RuntimeError("Algorithm C v114 lifecycle contract differs")
    for name, raw in dict(contract.get("source_bindings") or {}).items():
        row = dict(raw or {})
        path = (ROOT / str(row.get("path") or "")).resolve()
        if (
            not path.is_file() or path.is_symlink()
            or runtime.alg.sha(path) != row.get("sha256")
        ):
            raise RuntimeError(f"Algorithm C v114 source differs: {name}: {path}")
    return contract


def main() -> int:
    old = {
        "contract": predecessor.CONTRACT,
        "optimization": predecessor.OPTIMIZATION_CONTRACT,
        "validate": predecessor.validate_contract,
        "preflight": v21.kernel_preflight,
        "gate": v21.kernel_gate,
        "gate_binding": v21.gate_binding.gate_v25,
        "runtime_gate": runtime.coordination,
        "runtime_contract": runtime.WAVE_COORDINATION_CONTRACT,
    }
    predecessor.CONTRACT = CONTRACT
    predecessor.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    predecessor.validate_contract = validate_contract
    v21.kernel_preflight = kernel_preflight
    v21.kernel_gate = kernel_gate
    v21.gate_binding.gate_v25 = kernel_gate
    runtime.coordination = kernel_gate
    runtime.WAVE_COORDINATION_CONTRACT = COORDINATION_CONTRACT
    try:
        return int(predecessor.main())
    finally:
        runtime.WAVE_COORDINATION_CONTRACT = old["runtime_contract"]
        runtime.coordination = old["runtime_gate"]
        v21.gate_binding.gate_v25 = old["gate_binding"]
        v21.kernel_gate = old["gate"]
        v21.kernel_preflight = old["preflight"]
        predecessor.validate_contract = old["validate"]
        predecessor.OPTIMIZATION_CONTRACT = old["optimization"]
        predecessor.CONTRACT = old["contract"]


if __name__ == "__main__":
    raise SystemExit(main())
