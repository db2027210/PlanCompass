#!/usr/bin/env python3
"""Algorithm C v112 controller with stale-containerd-task recovery."""

from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_c_scene_resume_checkpoint_v5 as checkpoint_v5
from scripts import reset_mysql_xp_data_planes_client_primary_v5 as xp_reset_v5
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v21 as predecessor


CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_pg_latest_outer_20260906_v112/"
    "algorithm_c_pg_latest_outer_contract.json"
)
OPTIMIZATION_CONTRACT = ROOT / (
    "config/formal/frozen_mysql_four_scene_algorithm_c_client_primary_20260906_v112/"
    "outer_runtime_optimization.json"
)
runtime = predecessor.runtime
base = predecessor.base


def validate_contract() -> dict[str, Any]:
    contract = runtime.alg.load(CONTRACT)
    flow = dict(contract.get("outer_flow") or {})
    lifecycle = dict(contract.get("complete_lifecycle_recovery") or {})
    stale = dict(contract.get("stale_containerd_task_recovery") or {})
    if (
        contract.get("release_version") != "v112-stale-containerd-task-recovery"
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
        or stale.get("exact_error_required") is not True
        or stale.get("stopped_pid_zero_required") is not True
        or stale.get("experiment_ownership_and_mount_verification_required") is not True
        or stale.get("bind_mounted_datadir_deleted") is not False
        or stale.get("all_other_docker_failures_fail_closed") is not True
    ):
        raise RuntimeError("Algorithm C v112 lifecycle contract differs")
    for name, raw in dict(contract.get("source_bindings") or {}).items():
        row = dict(raw or {})
        path = (ROOT / str(row.get("path") or "")).resolve()
        if (
            not path.is_file() or path.is_symlink()
            or runtime.alg.sha(path) != row.get("sha256")
        ):
            raise RuntimeError(f"Algorithm C v112 source differs: {name}: {path}")
    return contract


def main() -> int:
    old = {
        "contract": predecessor.CONTRACT,
        "optimization": predecessor.OPTIMIZATION_CONTRACT,
        "validate": predecessor.validate_contract,
        "checkpoint": predecessor.checkpoint_v4,
        "xp_reset": predecessor.xp_reset,
    }
    predecessor.CONTRACT = CONTRACT
    predecessor.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    predecessor.validate_contract = validate_contract
    predecessor.checkpoint_v4 = checkpoint_v5
    predecessor.xp_reset = xp_reset_v5
    try:
        return int(predecessor.main())
    finally:
        predecessor.xp_reset = old["xp_reset"]
        predecessor.checkpoint_v4 = old["checkpoint"]
        predecessor.validate_contract = old["validate"]
        predecessor.OPTIMIZATION_CONTRACT = old["optimization"]
        predecessor.CONTRACT = old["contract"]


if __name__ == "__main__":
    raise SystemExit(main())
