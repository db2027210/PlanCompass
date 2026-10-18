#!/usr/bin/env python3
"""Bootstrap XP4 handoff from the recovered v14/v95 JOB4 authority."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import bootstrap_mysql_algorithm_a_all_scene_to_xp4_monitor_v7 as previous


predecessor = previous.predecessor
CONFIG = ROOT / "config/formal/frozen_mysql_algorithm_a_all_scene_to_xp4_20260823_v8"
JOB_CONFIG = (
    ROOT / "config/formal/frozen_mysql_algorithm_a_job_buffer_pool_dynamic_slots_20260823_v14"
)
MONITOR = ROOT / "scripts/monitor_mysql_algorithm_a_all_scene_to_xp4_v8.py"
_ORIGINAL_BUILD = previous._ORIGINAL_BUILD


def validate_bootstrap_contract() -> dict[str, Any]:
    contract = predecessor.load(CONFIG / "bootstrap_contract.json")
    if (
        contract.get("schema_version")
        != "mysql_algorithm_a_all_scene_to_xp4_bootstrap_contract_v8"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("memory_scenes_complete_before_handoff") is not True
        or contract.get("xp_parallelism") != {"before": 2, "after": 4}
        or contract.get("cpu_pinning_retained") is not True
        or contract.get("management_controller_taskset_required") is not True
        or contract.get("numa_exclusive_scheduler_lock_required") is not False
        or contract.get("historical_accepted_rounds_preserved") is not True
        or contract.get("partial_round_credited") is not False
        or contract.get("current_recovered_tpch_required") is not True
        or contract.get("inventory_time_wrapper_proof_required") is not True
        or contract.get("job4_v14_authority_required") is not True
    ):
        raise RuntimeError("all-scene XP4 bootstrap v8 contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        path = ROOT / str(binding.get("path") or "")
        if (
            path.is_symlink()
            or not path.is_file()
            or predecessor.sha(path) != binding.get("sha256")
        ):
            raise RuntimeError(f"all-scene XP4 bootstrap v8 source differs: {path}")
    return contract


def build_transition_contract(bootstrap: Mapping[str, Any]) -> dict[str, Any]:
    value = _ORIGINAL_BUILD(bootstrap)
    value["release_version"] = "all_scene_parallel_xp4_v8"
    value["current_recovered_tpch_required"] = True
    value["inventory_time_wrapper_proof_required"] = True
    value["job4_v14_authority_required"] = True
    value["management_controller_cpuset"] = "240-241,496-497"
    value["management_controller_taskset_required"] = True
    return value


def main() -> int:
    predecessor.CONFIG = CONFIG
    predecessor.BOOTSTRAP_CONTRACT = CONFIG / "bootstrap_contract.json"
    predecessor.TRANSITION_CONTRACT = CONFIG / "transition_contract.json"
    predecessor.SOURCE_FREEZE = JOB_CONFIG / "job_four_worker_resource_successor.json"
    predecessor.UPSTREAM_CONTRACT = JOB_CONFIG / "transition_contract.json"
    predecessor.MONITOR = MONITOR
    predecessor.validate_bootstrap_contract = validate_bootstrap_contract
    predecessor.build_transition_contract = build_transition_contract
    return predecessor.main()


if __name__ == "__main__":
    raise SystemExit(main())
