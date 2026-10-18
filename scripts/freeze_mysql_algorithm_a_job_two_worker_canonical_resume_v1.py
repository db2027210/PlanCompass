#!/usr/bin/env python3
"""Freeze the JOB 4-to-2 recovery and canonicalize duplicated round labels."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/formal/frozen_mysql_algorithm_a_job_two_worker_canonical_resume_20260824_v1"
CONTRACT = CONFIG / "resume_contract.json"
SNAPSHOT = CONFIG / "canonical_predecessor_snapshot.json"
PREDECESSOR = ROOT / "history/mysql_four_scene_20260809/v129_to_a_job_valid_replica_refresh_pg_order_resume_20260824_attempt001_job/algorithm_a_state.json"
FAILED_BATCH = PREDECESSOR.parent / "round_005/wave_001/attempt_01/batch_result.json"
AUTHORITY = ROOT / "history/mysql_four_scene_20260809/v99_to_a_external_diagnostic_resume_20260823_job_dynamic_slots_v1/job_memory.transition_authority.json"
FREEZE = ROOT / "config/formal/frozen_mysql_algorithm_a_job_external_diagnostic_slots_20260823_v1/job_four_worker_resource_successor.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    state = load(PREDECESSOR)
    checkpoint = state.get("round_checkpoint") or {}
    batch = load(FAILED_BATCH)
    if (
        state.get("algorithm") != "A"
        or state.get("async_scene") != "job_memory"
        or int(state.get("accepted") or 0) != 2
        or int(state.get("round_index") or 0) != 5
        or [row.get("round_index") for row in state.get("accepted_history") or []] != [1, 3]
        or [row.get("round_index") for row in state.get("rejected_history") or []] != [2, 3, 4]
        or checkpoint.get("attempted_ranks") != [1, 2, 3, 4]
        or checkpoint.get("valid_ranks") != []
        or checkpoint.get("technical_ranks") != [1, 2, 3, 4]
        or any((row.get("hard_gate_passed") is not False) for row in (batch.get("measurements") or {}).values())
    ):
        raise RuntimeError("JOB four-worker technical predecessor differs")

    snapshot = copy.deepcopy(state)
    snapshot["run_id"] = "v129_job_two_worker_canonical_transition_predecessor"
    snapshot["predecessor_run_id"] = state.get("run_id")
    snapshot["predecessor_snapshot_path"] = str(PREDECESSOR)
    snapshot["predecessor_snapshot_sha256"] = sha(PREDECESSOR)
    snapshot["baseline_by_worker"] = {
        worker_id: copy.deepcopy(state["baseline_by_worker"][worker_id])
        for worker_id in ("job_w1", "job_w2")
    }
    snapshot["physical_replay_parallelism"] = 2
    snapshot["round_index"] = 6
    snapshot["round_checkpoint"]["round_index"] = 6
    snapshot["interrupted_round_index"] = 6
    snapshot["rejected_history"][1]["round_index"] = 4
    snapshot["rejected_history"][2]["round_index"] = 5
    refresh = snapshot.get("last_incumbent_reference_refresh") or {}
    refresh["historical_artifact_round_index"] = refresh.get("round_index")
    refresh["round_index"] = 5
    snapshot["last_incumbent_reference_refresh"] = refresh
    snapshot["round_index_canonicalization"] = {
        "schema_version": "mysql_algorithm_a_job_round_index_canonicalization_v1",
        "cause": "job_2_to_4_transition_restarted_already_accepted_round_3_label",
        "accepted_round_labels_preserved": [1, 3],
        "rejected_round_labels_before": [2, 3, 4],
        "rejected_round_labels_after": [2, 4, 5],
        "interrupted_round_label_before": 5,
        "interrupted_round_label_after": 6,
        "accepted_count_changed": False,
        "accepted_history_changed": False,
        "ranges_changed": False,
        "incumbent_changed": False,
        "four_worker_failed_wave_credited": False,
        "four_worker_failed_wave_valid_candidate_count": 0,
    }
    atomic(SNAPSHOT, snapshot)

    bindings = {
        "controller": ROOT / "scripts/run_mysql_algorithm_a_job_two_worker_canonical_resume_v1.py",
        "same_cohort_resume": ROOT / "scripts/run_mysql_algorithm_a_same_cohort_codefix_resume_v1.py",
        "algorithm_a_controller": ROOT / "scripts/run_mysql_algorithm_a_async_scene_v3.py",
        "job_measurement": ROOT / "scripts/mysql_job_buffer_pool_preconditioned_measurement_v10.py",
        "external_diagnostic_gate": ROOT / "scripts/mysql_external_workload_diagnostic_gate_v1.py",
    }
    atomic(CONTRACT, {
        "schema_version": "mysql_algorithm_a_job_two_worker_canonical_resume_v1",
        "status": "FROZEN_RUNTIME_VALIDATED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "algorithm": "A",
        "scene": "job_memory",
        "physical_replay_parallelism": 2,
        "full_job_113_once_per_measurement": True,
        "suite_deadline_seconds": 900,
        "candidate_count_per_round": 16,
        "accepted_before": 2,
        "accepted_after": 2,
        "accepted_round_labels": [1, 3],
        "rejected_round_labels": [2, 4, 5],
        "next_outer_round": 6,
        "four_worker_failed_wave_valid_candidates": 0,
        "four_worker_failed_wave_replayed_as_valid": False,
        "baseline_replayed": False,
        "incumbent_replayed": False,
        "resource_recalibration_performed": False,
        "candidate_set_changed": False,
        "outer_loop_semantics_changed": False,
        "predecessor_state_path": str(PREDECESSOR),
        "predecessor_state_sha256": sha(PREDECESSOR),
        "failed_batch_path": str(FAILED_BATCH),
        "failed_batch_sha256": sha(FAILED_BATCH),
        "canonical_snapshot_path": str(SNAPSHOT),
        "canonical_snapshot_sha256": sha(SNAPSHOT),
        "authority_path": str(AUTHORITY),
        "authority_sha256": sha(AUTHORITY),
        "resource_freeze_path": str(FREEZE),
        "resource_freeze_sha256": sha(FREEZE),
        "active_workers": ["job_w1", "job_w2"],
        "released_workers": ["job_w3", "job_w4"],
        "source_bindings": {
            name: {"path": str(path.relative_to(ROOT)), "sha256": sha(path)}
            for name, path in bindings.items()
        },
    })
    print(json.dumps({"contract": str(CONTRACT), "snapshot": str(SNAPSHOT), "contract_sha256": sha(CONTRACT)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
