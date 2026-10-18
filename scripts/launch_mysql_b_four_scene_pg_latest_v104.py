#!/usr/bin/env python3
"""Launch four independent fresh Algorithm-B scenes under v104."""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any, Mapping

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from scripts import launch_mysql_c_four_scene_pg_latest_v103 as base


ROOT = base.ROOT
HISTORY = base.HISTORY
CAMPAIGN_ID = "v1225_mysql_b_four_scene_pg_latest_v104_20260905"
CAMPAIGN = HISTORY / CAMPAIGN_ID
MANIFEST = CAMPAIGN / "launch_manifest.json"
CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_b_pg_latest_outer_20260905_v104/"
    "four_scene_launch_contract.json"
)
CONTROLLER = ROOT / (
    "scripts/run_mysql_algorithm_b_four_scene_client_primary_pg_latest_v1.py"
)
CHECKPOINT = base.CHECKPOINT
QUARANTINE = base.QUARANTINE
BARRIER = Path("/tmp/mysql_async_outer_start") / CAMPAIGN_ID / "algorithm_b"
AUTHORITY = HISTORY / "v1224_to_b_current_identity_authority_20260905/authority"
FRESH_R0 = HISTORY / (
    "v1224_to_b_current_identity_fresh_r0_20260905/fresh_r0_binding.json"
)
SPECS = {
    "job_memory": {
        "run_id": "v1225_to_b_pg_latest_v104_20260905_job",
        "authority": AUTHORITY,
        "fresh_r0": FRESH_R0,
    },
    "tpch_sf4_memory": {
        "run_id": "v1225_to_b_pg_latest_v104_20260905_tpch",
        "authority": AUTHORITY,
        "fresh_r0": FRESH_R0,
    },
    "hybench_xp_cpu": {
        "run_id": "v1225_to_b_pg_latest_v104_20260905_xp_cpu",
        "authority": AUTHORITY,
        "fresh_r0": FRESH_R0,
    },
    "hybench_xp_io": {
        "run_id": "v1225_to_b_pg_latest_v104_20260905_xp_io",
        "authority": AUTHORITY,
        "fresh_r0": FRESH_R0,
    },
}


def validate() -> dict[str, Any]:
    contract = base.load(CONTRACT)
    if (
        contract.get("release_version") != "v104-pg-latest-outer"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("algorithm") != "B"
        or contract.get("fresh_search") is not True
        or contract.get("fresh_formal_baseline_per_scene") is not True
        or contract.get("active_scenes") != list(SPECS)
        or contract.get("all_four_scenes_start_concurrently") is not True
        or contract.get("physical_workers_per_scene") != 2
        or contract.get("maximum_concurrent_mysql_workers") != 8
        or contract.get("per_scene_controller") is not True
        or contract.get("per_scene_outer_state_machine") is not True
        or contract.get("four_scene_wave_barrier") is not False
        or contract.get("sibling_scene_wait") is not False
        or contract.get("lease_mode") != "none"
        or contract.get("algorithm_a_or_previous_b_prior_used") is not False
        or contract.get("previous_b_accepted_or_rejected_credit_inherited") is not False
        or contract.get("previous_b_ranges_inherited") is not False
    ):
        raise RuntimeError("Algorithm B v104 four-scene launch contract differs")
    for name, raw in dict(contract.get("source_bindings") or {}).items():
        row = dict(raw or {})
        path = (ROOT / str(row.get("path") or "")).resolve()
        if (
            not path.is_file()
            or path.is_symlink()
            or base.sha(path) != row.get("sha256")
        ):
            raise RuntimeError(f"Algorithm B v104 launch source differs: {name}")
    required = [CONTROLLER, CHECKPOINT, QUARANTINE, FRESH_R0]
    required.extend([
        AUTHORITY / "transition_preflight.json",
        AUTHORITY / "prepared_binding.json",
        AUTHORITY / "benchmark_accounts_binding.json",
    ])
    for path in required:
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"regular B launch artifact required: {path}")
    return contract


def main() -> int:
    base.CAMPAIGN_ID = CAMPAIGN_ID
    base.CAMPAIGN = CAMPAIGN
    base.MANIFEST = MANIFEST
    base.CONTRACT = CONTRACT
    base.CONTROLLER = CONTROLLER
    base.BARRIER = BARRIER
    base.SPECS = SPECS
    base.validate = validate
    original_atomic = base.atomic

    def algorithm_b_atomic(path: Path, value: Mapping[str, Any]) -> None:
        document = copy.deepcopy(dict(value))
        if path == MANIFEST:
            document.update({
                "schema_version": "mysql_algorithm_b_four_scene_pg_latest_launch_v1",
                "algorithm": "B",
                "algorithm_a_or_previous_b_prior_used": False,
                "previous_b_state_inherited": False,
            })
            document.pop("algorithm_a_or_previous_c_prior_used", None)
            document.pop("previous_c_state_inherited", None)
        original_atomic(path, document)

    base.atomic = algorithm_b_atomic
    try:
        return int(base.main())
    finally:
        base.atomic = original_atomic


if __name__ == "__main__":
    raise SystemExit(main())
