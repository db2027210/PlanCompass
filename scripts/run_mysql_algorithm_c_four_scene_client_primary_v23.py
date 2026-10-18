#!/usr/bin/env python3
"""Algorithm C v113 controller with legacy-freeze stale-task recovery."""

from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import reset_mysql_xp_data_planes_client_primary_v6 as xp_reset_v6
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v22 as predecessor


CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_pg_latest_outer_20260906_v113/"
    "algorithm_c_pg_latest_outer_contract.json"
)
OPTIMIZATION_CONTRACT = ROOT / (
    "config/formal/frozen_mysql_four_scene_algorithm_c_client_primary_20260906_v113/"
    "outer_runtime_optimization.json"
)
runtime = predecessor.runtime


def validate_contract() -> dict[str, Any]:
    contract = runtime.alg.load(CONTRACT)
    flow = dict(contract.get("outer_flow") or {})
    stale = dict(contract.get("stale_containerd_task_recovery") or {})
    if (
        contract.get("release_version")
        != "v113-legacy-freeze-stale-containerd-task-recovery"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or flow.get("strict_higher_qps_acceptance") is not True
        or flow.get("two_rejections_before_incumbent_shrink") is not True
        or stale.get("legacy_freeze_runtime_root_derivation") is not True
        or stale.get("exact_error_required") is not True
        or stale.get("experiment_ownership_and_mount_verification_required") is not True
        or stale.get("bind_mounted_datadir_deleted") is not False
        or stale.get("all_other_docker_failures_fail_closed") is not True
    ):
        raise RuntimeError("Algorithm C v113 lifecycle contract differs")
    for name, raw in dict(contract.get("source_bindings") or {}).items():
        row = dict(raw or {})
        path = (ROOT / str(row.get("path") or "")).resolve()
        if (
            not path.is_file() or path.is_symlink()
            or runtime.alg.sha(path) != row.get("sha256")
        ):
            raise RuntimeError(f"Algorithm C v113 source differs: {name}: {path}")
    return contract


def main() -> int:
    old = {
        "contract": predecessor.CONTRACT,
        "optimization": predecessor.OPTIMIZATION_CONTRACT,
        "validate": predecessor.validate_contract,
        "xp_reset": predecessor.xp_reset_v5,
    }
    predecessor.CONTRACT = CONTRACT
    predecessor.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    predecessor.validate_contract = validate_contract
    predecessor.xp_reset_v5 = xp_reset_v6
    try:
        return int(predecessor.main())
    finally:
        predecessor.xp_reset_v5 = old["xp_reset"]
        predecessor.validate_contract = old["validate"]
        predecessor.OPTIMIZATION_CONTRACT = old["optimization"]
        predecessor.CONTRACT = old["contract"]


if __name__ == "__main__":
    raise SystemExit(main())
