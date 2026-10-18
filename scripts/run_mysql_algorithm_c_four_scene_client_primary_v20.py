#!/usr/bin/env python3
"""Algorithm C v110 controller with full-lifecycle recovery."""

from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_c_external_storage_inventory_only_v4 as kernel_preflight
from scripts import mysql_cross_project_wave_coordination_v27 as kernel_gate
from scripts import mysql_docker_control_resilience_v1 as docker_resilience
from scripts import mysql_resource_sampler_resilience_v1 as sampler_resilience
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v9 as gate_binding
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v16 as predecessor
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v45 as runtime


CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_pg_latest_outer_20260906_v110/"
    "algorithm_c_pg_latest_outer_contract.json"
)
base = predecessor.base
base.latest_runtime = runtime
base.CONTRACT = CONTRACT


def validate_contract() -> dict[str, Any]:
    contract = runtime.alg.load(CONTRACT)
    flow = dict(contract.get("outer_flow") or {})
    lifecycle = dict(contract.get("complete_lifecycle_recovery") or {})
    if (
        contract.get("release_version") != "v110-complete-lifecycle-recovery"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or flow.get("local_real_replay_limit") != 10
        or flow.get("global_coverage_real_replay_limit") != 10
        or flow.get("strict_higher_qps_acceptance") is not True
        or flow.get("two_rejections_before_incumbent_shrink") is not True
        or lifecycle.get("stable_definition")
        != "one_complete_outer_per_scene_with_valid_decision"
        or lifecycle.get("coverage_helper_bound_to_current_runtime") is not True
        or lifecycle.get("prestart_rolling_clean_seconds") != 15
        or lifecycle.get("maximum_gate_wait_seconds") != 300
        or lifecycle.get("resource_sampler_extra_join_seconds") != 120
        or lifecycle.get("docker_inspect_attempts") != 4
    ):
        raise RuntimeError("Algorithm C v110 lifecycle contract differs")
    for name, raw in dict(contract.get("source_bindings") or {}).items():
        row = dict(raw or {})
        path = (ROOT / str(row.get("path") or "")).resolve()
        if (
            not path.is_file() or path.is_symlink()
            or runtime.alg.sha(path) != row.get("sha256")
        ):
            raise RuntimeError(f"Algorithm C v110 source differs: {name}: {path}")
    return contract


base.validate_contract = validate_contract


def main() -> int:
    old_gate = gate_binding.gate_v25
    old_preflight = base.external_inventory_v2
    old_no_lease = runtime.NO_LEASE_RUNTIME
    old_independent = runtime.INDEPENDENT_SCENE_EXECUTION
    gate_binding.gate_v25 = kernel_gate
    base.external_inventory_v2 = kernel_preflight
    runtime.NO_LEASE_RUNTIME = True
    runtime.INDEPENDENT_SCENE_EXECUTION = True
    sampler_resilience.install()
    docker_resilience.install()
    try:
        return int(base.main())
    finally:
        docker_resilience.uninstall()
        sampler_resilience.uninstall()
        runtime.INDEPENDENT_SCENE_EXECUTION = old_independent
        runtime.NO_LEASE_RUNTIME = old_no_lease
        base.external_inventory_v2 = old_preflight
        gate_binding.gate_v25 = old_gate


if __name__ == "__main__":
    raise SystemExit(main())
