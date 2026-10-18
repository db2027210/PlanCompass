#!/usr/bin/env python3
"""Run XP Top-4 Algorithm C with rolling dynamic non-interference."""

from __future__ import annotations

from pathlib import Path
import copy
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v3 as dynamic_gate
from scripts import run_mysql_algorithm_c_async_scene_v55 as v55

runtime = v55.runtime
alg = v55.alg
ASYNC_CONTRACT = (
    ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_c_20260825_v5"
    / "algorithm_c_async_contract.json"
)


def validate_contract() -> dict[str, Any]:
    contract = alg.load(ASYNC_CONTRACT)
    policy = contract.get("dynamic_noninterference_gate") or {}
    empty = contract.get("empty_candidate_wave_policy") or {}
    if (
        contract.get("release_version") != "v56"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or policy.get("required_clean_seconds") != 15
        or policy.get("minimum_host_mem_available_bytes") != 256 * 1024**3
        or policy.get("maximum_window_memory_decline_bytes") != 8 * 1024**3
        or policy.get("external_process_control_or_stop_performed") is not False
        or policy.get("cross_scene_completion_dependency") is not False
        or policy.get("algorithm_or_candidate_flow_changed") is not False
        or empty.get("partial_top_k_empty_wave_skipped") is not True
        or empty.get("physical_workload_started") is not False
        or empty.get("mysql_container_reset_performed") is not False
        or empty.get("quiet_gate_wait_performed") is not False
        or empty.get("semantic_batch_evaluated_from_all_admitted_candidates")
        is not True
    ):
        raise RuntimeError("v56 dynamic non-interference contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        if not isinstance(binding, Mapping):
            raise RuntimeError("v56 source binding is not an object")
        path = ROOT / str(binding["path"])
        if not path.is_file() or path.is_symlink() or alg.sha(path) != binding["sha256"]:
            raise RuntimeError(f"v56 source binding differs: {path}")
    return contract


def install() -> None:
    # Do not revalidate the predecessor v55 contract here: this successor binds
    # every effective changed source independently and retains v55's algorithm,
    # candidate and MySQL interface implementation unchanged.
    v55.install()
    runtime.coordination.rolling_quiet_gate = dynamic_gate.rolling_quiet_gate
    physical_measure = runtime._measure_batch_with_fair_coordination

    def skip_empty_candidate_wave(**kwargs: Any):
        assignments = kwargs.get("assignments") or {}
        if assignments:
            return physical_measure(**kwargs)
        output = Path(kwargs["output"])
        state_path = Path(kwargs["state_path"])
        state = copy.deepcopy(kwargs["state"])
        output.mkdir(parents=True, exist_ok=False, mode=0o700)
        alg.atomic(output / "batch_result.json", {
            "schema_version": "mysql_algorithm_c_empty_candidate_wave_v1",
            "status": "PASS",
            "measurements": {},
            "failures": [],
            "formal_candidate_count": 0,
            "physical_workload_started": False,
            "mysql_container_reset_performed": False,
            "quiet_gate_wait_performed": False,
            "accepted_state_credit_increment": 0,
            "rejected_outer_attempt_increment": 0,
            "candidate_set_changed": False,
            "reason": "partial_top_k_has_no_candidate_in_this_physical_wave",
            "release_version": "v56",
            "release_contract_path": str(ASYNC_CONTRACT),
            "release_contract_sha256": alg.sha(ASYNC_CONTRACT),
            "completed_at": alg.now(),
        })
        state["stage"] = f"{kwargs['stage_prefix']}_SKIPPED_EMPTY"
        state.setdefault("empty_candidate_waves_skipped", []).append({
            "stage_prefix": kwargs["stage_prefix"],
            "output": str(output),
            "physical_workload_started": False,
        })
        alg.atomic(state_path, state)
        return {}, state

    runtime._measure_batch_with_fair_coordination = skip_empty_candidate_wave


def main() -> int:
    validate_contract()
    install()
    return v55.v54.v53.v52.v51.v50.v49.v45.v34.main()


if __name__ == "__main__":
    raise SystemExit(main())
