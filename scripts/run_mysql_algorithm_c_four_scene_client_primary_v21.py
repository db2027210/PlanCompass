#!/usr/bin/env python3
"""Algorithm C v111 controller with complete Docker-path resilience."""

from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_c_external_storage_inventory_only_v5 as kernel_preflight
from scripts import mysql_cross_project_wave_coordination_v28 as kernel_gate
from scripts import mysql_docker_control_resilience_v2 as docker_resilience
from scripts import build_mysql_algorithm_c_scene_resume_checkpoint_v4 as checkpoint_v4
from scripts import reset_mysql_xp_data_planes_client_primary_v4 as xp_reset
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v9 as gate_binding
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v20 as prior


CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_pg_latest_outer_20260906_v111/"
    "algorithm_c_pg_latest_outer_contract.json"
)
COORDINATION_CONTRACT = ROOT / (
    "config/formal/frozen_mysql_cross_project_wave_coordination_20260906_v111/"
    "coordination_contract.json"
)
OPTIMIZATION_CONTRACT = ROOT / (
    "config/formal/frozen_mysql_four_scene_algorithm_c_client_primary_20260906_v111/"
    "outer_runtime_optimization.json"
)
runtime = prior.runtime
base = prior.base


def validate_contract() -> dict[str, Any]:
    contract = runtime.alg.load(CONTRACT)
    flow = dict(contract.get("outer_flow") or {})
    lifecycle = dict(contract.get("complete_lifecycle_recovery") or {})
    if (
        contract.get("release_version") != "v111-full-docker-path-resilience"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or flow.get("local_real_replay_limit") != 10
        or flow.get("global_coverage_real_replay_limit") != 10
        or flow.get("strict_higher_qps_acceptance") is not True
        or flow.get("two_rejections_before_incumbent_shrink") is not True
        or lifecycle.get("stable_definition")
        != "one_complete_outer_per_scene_with_valid_decision"
        or lifecycle.get("maximum_gate_wait_seconds") != 300
        or lifecycle.get("docker_inspect_attempts") != 4
        or lifecycle.get("measurement_prepare_docker_json_covered") is not True
        or lifecycle.get("wave_coordination_contract_bound_at_runtime") is not True
        or lifecycle.get("scene_checkpoint_schema_dispatch_covered") is not True
        or lifecycle.get("run_scoped_xp_quarantine_path") is not True
        or lifecycle.get("optimization_contract_bound_to_xp_reset_v4") is not True
    ):
        raise RuntimeError("Algorithm C v111 lifecycle contract differs")
    for name, raw in dict(contract.get("source_bindings") or {}).items():
        row = dict(raw or {})
        path = (ROOT / str(row.get("path") or "")).resolve()
        if (
            not path.is_file() or path.is_symlink()
            or runtime.alg.sha(path) != row.get("sha256")
        ):
            raise RuntimeError(f"Algorithm C v111 source differs: {name}: {path}")
    return contract


def main() -> int:
    old = {
        "contract": prior.CONTRACT,
        "base_contract": base.CONTRACT,
        "optimization_contract": base.OPTIMIZATION_CONTRACT,
        "validate": base.validate_contract,
        "docker": prior.docker_resilience,
        "preflight": prior.kernel_preflight,
        "gate": prior.kernel_gate,
        "gate_binding": gate_binding.gate_v25,
        "runtime_coordination": runtime.coordination,
        "scene_checkpoint": runtime.scene_checkpoint,
        "xp_reset": base.xp_reset,
        "wave_contract": runtime.WAVE_COORDINATION_CONTRACT,
    }
    prior.CONTRACT = CONTRACT
    base.CONTRACT = CONTRACT
    base.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    base.validate_contract = validate_contract
    prior.docker_resilience = docker_resilience
    prior.kernel_preflight = kernel_preflight
    prior.kernel_gate = kernel_gate
    gate_binding.gate_v25 = kernel_gate
    runtime.coordination = kernel_gate
    runtime.scene_checkpoint = checkpoint_v4.scene_checkpoint
    base.xp_reset = xp_reset
    xp_reset.install()
    runtime.WAVE_COORDINATION_CONTRACT = COORDINATION_CONTRACT
    try:
        return int(prior.main())
    finally:
        runtime.WAVE_COORDINATION_CONTRACT = old["wave_contract"]
        runtime.coordination = old["runtime_coordination"]
        runtime.scene_checkpoint = old["scene_checkpoint"]
        base.xp_reset = old["xp_reset"]
        xp_reset.uninstall()
        gate_binding.gate_v25 = old["gate_binding"]
        prior.kernel_gate = old["gate"]
        prior.kernel_preflight = old["preflight"]
        prior.docker_resilience = old["docker"]
        base.validate_contract = old["validate"]
        base.CONTRACT = old["base_contract"]
        base.OPTIMIZATION_CONTRACT = old["optimization_contract"]
        prior.CONTRACT = old["contract"]


if __name__ == "__main__":
    raise SystemExit(main())
