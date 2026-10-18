#!/usr/bin/env python3
"""Freeze the JOB valid-replica incumbent-refresh recovery."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/formal/frozen_mysql_algorithm_a_job_valid_replica_refresh_resume_20260824_v1"
CONTRACT = CONFIG / "resume_contract.json"
PREDECESSOR = ROOT / "history/mysql_four_scene_20260809/v117_to_a_job_partial_suite_timer_resume_20260824_attempt001_job/algorithm_a_state.json"
ROUND4_DECISION = PREDECESSOR.parent / "round_004/outer_decision.json"
REFRESH_BATCH = PREDECESSOR.parent / "round_004/incumbent_reference_refresh/refresh_01/attempt_01/batch_result.json"
AUTHORITY = ROOT / "history/mysql_four_scene_20260809/v99_to_a_external_diagnostic_resume_20260823_job_dynamic_slots_v1/job_memory.transition_authority.json"


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def relative(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT))


def atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    predecessor = load(PREDECESSOR)
    refresh = load(REFRESH_BATCH).get("measurements") or {}
    if (
        predecessor.get("algorithm") != "A"
        or predecessor.get("async_scene") != "job_memory"
        or int(predecessor.get("accepted") or 0) != 2
        or int(predecessor.get("round_index") or 0) != 4
        or int(predecessor.get("rejected_rounds") or 0) != 3
        or int(predecessor.get("consecutive_rejected_rounds") or 0) != 2
        or sorted(refresh) != ["job_w1", "job_w2", "job_w3", "job_w4"]
        or not all(refresh[name].get("coverage_complete") is True and refresh[name].get("hard_gate_passed") is True for name in ("job_w1", "job_w3", "job_w4"))
        or refresh["job_w2"].get("coverage_complete") is not False
    ):
        raise RuntimeError("JOB valid-replica recovery evidence differs")
    bindings = {
        "controller": ROOT / "scripts/run_mysql_algorithm_a_job_valid_replica_refresh_resume_v1.py",
        "freezer": Path(__file__).resolve(),
        "algorithm_a_controller": ROOT / "scripts/run_mysql_algorithm_a_async_scene_v3.py",
        "pg_outer_policy": ROOT / "scripts/mysql_algorithm_b_pg_outer_policy_v36.py",
        "measurement_v12": ROOT / "scripts/mysql_job_buffer_pool_preconditioned_measurement_v12.py",
        "measurement_v12_contract": ROOT / "config/formal/frozen_mysql_job_cache_state_20260824_v12/cache_state_contract.json",
        "external_diagnostic_gate": ROOT / "scripts/mysql_external_workload_diagnostic_gate_v1.py",
        "predecessor_state": PREDECESSOR,
        "round4_decision": ROUND4_DECISION,
        "refresh_batch": REFRESH_BATCH,
        "transition_authority": AUTHORITY,
    }
    atomic(CONTRACT, {
        "schema_version": "mysql_algorithm_a_job_valid_replica_refresh_resume_v1",
        "status": "FROZEN_RUNTIME_VALIDATED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "algorithm": "A",
        "scene": "job_memory",
        "accepted_target": 10,
        "accepted_before_recovery": 2,
        "accepted_after_recovery": 2,
        "recovered_round_index": 4,
        "physical_replay_parallelism": 4,
        "refresh_replica_policy": "aggregate_complete_valid_replicas_only",
        "minimum_valid_refresh_replicas": 3,
        "valid_refresh_workers": ["job_w1", "job_w3", "job_w4"],
        "invalid_refresh_workers": ["job_w2"],
        "full_job_113_required_per_valid_replica": True,
        "suite_deadline_seconds": 900,
        "candidate_measurements_replayed": False,
        "incumbent_measurements_replayed": False,
        "existing_complete_round_re_evaluated": False,
        "round4_original_rejection_preserved": True,
        "refreshed_reference_first_used_in_round": 5,
        "candidate_set_changed": False,
        "outer_acceptance_policy_changed": False,
        "resource_envelope_changed": False,
        "external_workloads_diagnostic_only": True,
        "external_workload_wait_required": False,
        "external_process_control_or_stop_prohibited": True,
        "predecessor_state_path": relative(PREDECESSOR),
        "predecessor_state_sha256": sha(PREDECESSOR),
        "round4_decision_path": relative(ROUND4_DECISION),
        "round4_decision_sha256": sha(ROUND4_DECISION),
        "refresh_batch_path": relative(REFRESH_BATCH),
        "refresh_batch_sha256": sha(REFRESH_BATCH),
        "transition_authority_path": relative(AUTHORITY),
        "transition_authority_sha256": sha(AUTHORITY),
        "source_bindings": {
            name: {"path": relative(path), "sha256": sha(path)}
            for name, path in bindings.items()
        },
    })
    print(json.dumps({"contract": str(CONTRACT), "sha256": sha(CONTRACT)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
