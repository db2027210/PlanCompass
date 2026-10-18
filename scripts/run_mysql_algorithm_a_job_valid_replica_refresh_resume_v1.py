#!/usr/bin/env python3
"""Resume JOB A after salvaging a complete-valid incumbent refresh cohort.

Only refresh replicas that completed all 113 JOB queries within the frozen
900-second suite deadline are aggregated.  The already rejected round 4 stays
rejected, exactly matching the PG outer-loop ordering; the refreshed reference
is used beginning with round 5.  No workload is replayed by this recovery.
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_external_workload_diagnostic_gate_v1 as diagnostic_gate
from scripts import mysql_job_buffer_pool_preconditioned_measurement_v12 as job_v12
from scripts import run_mysql_algorithm_a_async_scene_v3 as base
from scripts import run_mysql_algorithm_a_job_buffer_pool_dynamic_external_diagnostic_resume_v1 as dynamic_external


CONFIG = ROOT / "config/formal/frozen_mysql_algorithm_a_job_valid_replica_refresh_resume_20260824_v1"
CONTRACT_PATH = CONFIG / "resume_contract.json"
PREDECESSOR_PATH = ROOT / "history/mysql_four_scene_20260809/v117_to_a_job_partial_suite_timer_resume_20260824_attempt001_job/algorithm_a_state.json"
ROUND4_DECISION = PREDECESSOR_PATH.parent / "round_004/outer_decision.json"
REFRESH_BATCH = PREDECESSOR_PATH.parent / "round_004/incumbent_reference_refresh/refresh_01/attempt_01/batch_result.json"
ORIGINAL_BOUNDARY = ROOT / "history/mysql_four_scene_20260809/v99_to_a_external_diagnostic_resume_20260823_job_dynamic_slots_v1/job_memory.predecessor_boundary_snapshot.json"
AUTHORITY_PATH = ROOT / "history/mysql_four_scene_20260809/v99_to_a_external_diagnostic_resume_20260823_job_dynamic_slots_v1/job_memory.transition_authority.json"
CONTRACT_NAME = "mysql_algorithm_a_job_valid_replica_refresh_resume_v1"


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def regular_file(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if path.is_symlink() or not path.is_file():
        raise argparse.ArgumentTypeError(f"regular file required: {path}")
    return path


def validate_contract(predecessor_path: Path) -> dict[str, Any]:
    contract = load(CONTRACT_PATH)
    if (
        CONTRACT_PATH.is_symlink()
        or contract.get("schema_version") != CONTRACT_NAME
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("algorithm") != "A"
        or contract.get("scene") != "job_memory"
        or contract.get("accepted_before_recovery") != 2
        or contract.get("accepted_after_recovery") != 2
        or contract.get("recovered_round_index") != 4
        or contract.get("valid_refresh_workers") != ["job_w1", "job_w3", "job_w4"]
        or contract.get("invalid_refresh_workers") != ["job_w2"]
        or contract.get("minimum_valid_refresh_replicas") != 3
        or contract.get("candidate_measurements_replayed") is not False
        or contract.get("incumbent_measurements_replayed") is not False
        or contract.get("candidate_set_changed") is not False
        or contract.get("outer_acceptance_policy_changed") is not False
        or predecessor_path.resolve() != PREDECESSOR_PATH.resolve()
        or contract.get("predecessor_state_sha256") != base.alg.sha(PREDECESSOR_PATH)
        or contract.get("round4_decision_sha256") != base.alg.sha(ROUND4_DECISION)
        or contract.get("refresh_batch_sha256") != base.alg.sha(REFRESH_BATCH)
        or contract.get("transition_authority_sha256") != base.alg.sha(AUTHORITY_PATH)
    ):
        raise RuntimeError("JOB valid-replica refresh resume contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        source = ROOT / str(binding.get("path") or "")
        if source.is_symlink() or not source.is_file() or base.alg.sha(source) != binding.get("sha256"):
            raise RuntimeError(f"JOB recovery source differs: {source}")
    job_v12._validate_contract()
    return contract


def recover(predecessor: Mapping[str, Any], output: Path) -> dict[str, Any]:
    if (
        predecessor.get("algorithm") != "A"
        or predecessor.get("async_scene") != "job_memory"
        or predecessor.get("formal_scope") != "mysql_only"
        or int(predecessor.get("accepted") or 0) != 2
        or int(predecessor.get("round_index") or 0) != 4
        or int(predecessor.get("rejected_rounds") or 0) != 3
        or int(predecessor.get("consecutive_rejected_rounds") or 0) != 2
    ):
        raise RuntimeError("JOB predecessor semantic state differs")
    baseline_rows = predecessor.get("baseline_by_worker") or {}
    canonical = predecessor.get("canonical_baseline") or {}
    refresh_measurements = load(REFRESH_BATCH).get("measurements") or {}
    valid_workers = [
        worker_id for worker_id in sorted(refresh_measurements)
        if base.pg_outer.physical_valid(refresh_measurements[worker_id])
    ]
    if valid_workers != ["job_w1", "job_w3", "job_w4"]:
        raise RuntimeError("frozen JOB valid refresh replica set differs")
    normalized_rows = [
        base.normalize_to_worker_baseline(
            refresh_measurements[worker_id], baseline_rows[worker_id], canonical,
        )
        for worker_id in valid_workers
    ]
    for worker_id, row in zip(valid_workers, normalized_rows, strict=True):
        row["worker_id"] = worker_id
    refreshed_reference = base.aggregate_incumbent_reference(normalized_rows)

    recovery_path = output / "round_004/outer_decision_valid_replica_recovery.json"
    base.atomic(recovery_path, {
        "schema_version": "mysql_algorithm_a_job_valid_replica_round_recovery_v1",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "scene": "job_memory",
        "round_index": 4,
        "refresh_replica_policy": "aggregate_complete_valid_replicas_only",
        "minimum_valid_refresh_replicas": 3,
        "valid_refresh_workers": valid_workers,
        "invalid_refresh_workers": ["job_w2"],
        "full_job_113_required_per_valid_replica": True,
        "suite_deadline_seconds": 900,
        "previous_reference": copy.deepcopy(predecessor["incumbent_validation"]),
        "refreshed_reference": refreshed_reference,
        "raw_refresh_rows": {worker_id: refresh_measurements[worker_id] for worker_id in valid_workers},
        "normalized_refresh_rows": normalized_rows,
        "round4_original_rejection_preserved": True,
        "round4_re_evaluated": False,
        "refreshed_reference_first_used_in_round": 5,
        "candidate_measurements_replayed": False,
        "incumbent_measurements_replayed": False,
        "source_round4_decision": str(ROUND4_DECISION),
        "source_round4_decision_sha256": base.alg.sha(ROUND4_DECISION),
    })

    state = copy.deepcopy(dict(predecessor))
    state["accepted"] = 2
    state["rejected_rounds"] = 3
    state["consecutive_rejected_rounds"] = 0
    state["incumbent_validation"] = copy.deepcopy(refreshed_reference)
    state["incumbent_reference_source"] = "replayed_after_consecutive_rejections"
    state["incumbent_reference_refresh_count"] = int(predecessor.get("incumbent_reference_refresh_count") or 0) + 1
    state["last_incumbent_reference_refresh"] = {
        "round_index": 4,
        "refresh_index": 1,
        "valid_replica_count": 3,
        "valid_workers": valid_workers,
        "previous_db_qps": predecessor["incumbent_validation"]["db_qps"],
        "refreshed_db_qps": refreshed_reference["db_qps"],
        "recovery_path": str(recovery_path),
        "recovery_sha256": base.alg.sha(recovery_path),
    }
    state["ranges"] = copy.deepcopy(predecessor["ranges"])
    state["normalized_width"] = 0.5 ** 2
    state["rejected_history"] = copy.deepcopy(predecessor.get("rejected_history") or [])
    if not state["rejected_history"] or int(state["rejected_history"][-1].get("round_index") or 0) != 4:
        raise RuntimeError("JOB round-4 rejection history differs")
    state["rejected_history"][-1]["incumbent_reference_refresh_triggered"] = True
    state["accepted_history"] = copy.deepcopy(predecessor.get("accepted_history") or [])
    state["round_checkpoint"] = None
    state["status"] = "RUNNING"
    state["stage"] = "ROUND_004_INCUMBENT_REFERENCE_REFRESHED_CONTINUING"
    state["converged"] = False
    state["run_id"] = output.name
    state["predecessor_run_id"] = predecessor.get("run_id")
    state["predecessor_snapshot_path"] = str(PREDECESSOR_PATH)
    state["predecessor_snapshot_sha256"] = base.alg.sha(PREDECESSOR_PATH)
    state["execution_evidence_epoch"] = int(predecessor.get("execution_evidence_epoch") or 1) + 1
    state["valid_replica_refresh_resume"] = {
        "contract": CONTRACT_NAME,
        "contract_path": str(CONTRACT_PATH),
        "contract_sha256": base.alg.sha(CONTRACT_PATH),
        "candidate_measurements_replayed": False,
        "incumbent_measurements_replayed": False,
        "recovered_round_index": 4,
        "round4_original_rejection_preserved": True,
        "refreshed_reference_first_used_in_round": 5,
    }
    return state


def run(args: argparse.Namespace) -> int:
    if str(args.scene) != "job_memory":
        raise RuntimeError("valid-replica refresh continuation is JOB-only")
    contract = validate_contract(args.predecessor_state)
    predecessor = load(args.predecessor_state)
    active, _ranges = base.scene_parameter_space("job_memory")
    algorithm_contract = base.validate_a_contract("job_memory", active)
    freeze, workers, authority = dynamic_external.validate_authority(
        AUTHORITY_PATH, ORIGINAL_BOUNDARY, "job_memory",
    )
    dynamic_external.configure_runtime(
        freeze_path=Path(str(authority["resource_freeze_path"])), workers=workers,
        scene="job_memory",
    )
    if set(workers) != set(predecessor.get("baseline_by_worker") or {}):
        raise RuntimeError("JOB worker cohort changed during recovery")
    base.REPLAY_PARALLELISM = 4
    base.alg.WORKER_IDS = tuple(sorted(workers))
    base.runtime.alg.measure_batch = job_v12.measure_batch
    base.runtime.coordination.rolling_quiet_gate = diagnostic_gate.rolling_quiet_gate

    original_fresh_state = base.fresh_state
    original_measure = base.measure
    original_aggregate = base.aggregate_baseline
    original_atomic = base.atomic
    state_path = args.output_dir.resolve() / "algorithm_a_state.json"
    baseline_skipped = False
    recovered_state: dict[str, Any] | None = None
    state_recovery_injected = False

    def recovered_fresh_state(**kwargs: Any) -> dict[str, Any]:
        nonlocal recovered_state
        original_fresh_state(**kwargs)
        recovered_state = recover(predecessor, args.output_dir.resolve())
        recovered_state["authority"] = copy.deepcopy(authority)
        recovered_state["physical_replay_parallelism"] = 4
        return copy.deepcopy(recovered_state)

    def preserved_aggregate(rows: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
        if rows != predecessor["baseline_by_worker"]:
            raise RuntimeError("preserved JOB baseline differs")
        return copy.deepcopy(predecessor["canonical_baseline"])

    def recovery_measure(**kwargs: Any):
        nonlocal baseline_skipped
        if not baseline_skipped and Path(kwargs["output"]).name == "formal_baseline":
            baseline_skipped = True
            ranking_log_root = args.output_dir.resolve() / "plan_ranking_client_processes"
            ranking_log_root.mkdir(parents=True, mode=0o700)
            for worker_id, worker in kwargs["workers"].items():
                worker["formal_client_log"] = str(ranking_log_root / f"{worker_id}.jsonl")
            return copy.deepcopy(predecessor["baseline_by_worker"]), kwargs["state"]
        return original_measure(**kwargs)

    def recovery_atomic(path: Path, value: Mapping[str, Any]) -> None:
        nonlocal state_recovery_injected
        document = job_v12.reconcile_document(value) if path.name == "batch_result.json" else value
        if (
            path == state_path
            and document.get("formal_started") is True
            and recovered_state is not None
            and baseline_skipped
            and not state_recovery_injected
        ):
            merged = copy.deepcopy(dict(document))
            for key in (
                "accepted", "accepted_history", "rejected_rounds", "rejected_history",
                "consecutive_rejected_rounds", "round_index", "ranges", "normalized_width",
                "incumbent_params", "incumbent_validation", "incumbent_reference_source",
                "incumbent_reference_refresh_count", "last_incumbent_reference_refresh",
                "valid_replica_refresh_resume", "execution_evidence_epoch", "predecessor_run_id",
                "predecessor_snapshot_path", "predecessor_snapshot_sha256",
            ):
                merged[key] = copy.deepcopy(recovered_state[key])
            merged["round_checkpoint"] = None
            document = merged
            state_recovery_injected = True
        original_atomic(path, document)

    base.fresh_state = recovered_fresh_state
    base.measure = recovery_measure
    base.aggregate_baseline = preserved_aggregate
    base.atomic = recovery_atomic
    base.validate_a_contract = lambda _scene, _active: algorithm_contract
    base.validate_fresh_authority = lambda **_kwargs: (
        freeze, copy.deepcopy(workers), copy.deepcopy(authority)
    )
    args.runtime_preflight = AUTHORITY_PATH
    args.prepared_binding = AUTHORITY_PATH
    args.benchmark_accounts = AUTHORITY_PATH
    args.fresh_r0 = AUTHORITY_PATH
    try:
        return base.run(args)
    finally:
        base.fresh_state = original_fresh_state
        base.measure = original_measure
        base.aggregate_baseline = original_aggregate
        base.atomic = original_atomic


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--scene", choices=("job_memory",), required=True)
    parser.add_argument("--predecessor-state", type=regular_file, required=True)
    parser.add_argument("--runtime-preflight", type=regular_file, required=True)
    parser.add_argument("--prepared-binding", type=regular_file, required=True)
    parser.add_argument("--benchmark-accounts", type=regular_file, required=True)
    parser.add_argument("--fresh-r0", type=regular_file, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
