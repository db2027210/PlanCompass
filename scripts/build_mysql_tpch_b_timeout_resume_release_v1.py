#!/usr/bin/env python3
"""Freeze the TPC-H B timeout-row successor and Outer-2 recovery evidence."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
HISTORY = ROOT / "history/mysql_four_scene_20260809"
SOURCE = HISTORY / "v1125_to_b_tpch_client_qps_disk2_normalized_20260830_tpch"
EVIDENCE = HISTORY / "v1128_tpch_b_outer2_timeout_recovery_evidence_20260830"
OUTPUT = ROOT / "config/formal/frozen_mysql_tpch_b_timeout_resume_20260830_v2"
RAW_BATCH = SOURCE / "outer_002/top5_batch_1/batch_result.json"
BASELINE = SOURCE / "formal_independent_baseline/batch_result.json"

from scripts import mysql_tpch_client_qps_measurement_v2 as metric
from scripts import mysql_tpch_worker_qps_normalization_v1 as normalization


SOURCES = {
    "builder": Path(__file__).resolve(),
    "controller": ROOT / "scripts/run_mysql_tpch_algorithm_b_timeout_resume_v1.py",
    "metric_adapter": ROOT / "scripts/mysql_tpch_client_qps_measurement_v2.py",
    "normalization": ROOT / "scripts/mysql_tpch_worker_qps_normalization_v1.py",
    "normalized_predecessor": ROOT / "scripts/run_mysql_tpch_algorithm_b_client_qps_normalized_v3.py",
    "client_qps_predecessor": ROOT / "scripts/run_mysql_tpch_algorithm_b_client_qps_fresh_v1.py",
    "algorithm_b_core": ROOT / "scripts/run_mysql_algorithm_b_xp_top4_async_v1.py",
    "two_worker_scheduler": ROOT / "scripts/mysql_algorithm_b_fresh_parallel_221_scheduler_v26.py",
    "strict_progress": ROOT / "scripts/mysql_outer_qps_strict_progress_v1.py",
    "two_reject_shrink": ROOT / "scripts/mysql_outer_rejection_cycle_v2.py",
    "top4_table": ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_c_20260825_v1/T.tpch_memory.operator_physics_v1.json",
    "resource_freeze": ROOT / "config/formal/frozen_mysql_tpch_bc_disk2_two_worker_20260830_v1/four_scene_effective_resource_successor.json",
    "raw_failed_batch": RAW_BATCH,
    "failed_state": SOURCE / "run_state.json",
    "outer1_decision": SOURCE / "outer_001/tpch_sf4_memory.decision.json",
    "outer1_model_update": SOURCE / "outer_001/online_resource_model_update.json",
    "outer2_rankings": SOURCE / "outer_002/predicted_rankings.json",
    "baseline": BASELINE,
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def verified(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular source required: {path}")
    sidecar = path.with_name(path.name + ".sha256")
    if (
        not sidecar.is_file()
        or sidecar.is_symlink()
        or sidecar.read_text(encoding="ascii").split()[0] != sha(path)
    ):
        raise RuntimeError(f"source sidecar differs: {path}")
    return load(path)


def failure_for(worker_id: str, failure: Mapping[str, Any]) -> bool:
    task = str(failure.get("task") or "")
    error = str(failure.get("error") or "")
    pair = f"pair_{worker_id[-1]}"
    return (
        task == worker_id
        or task.startswith(worker_id + "_")
        or task == pair
        or worker_id in error
        or pair in error
    )


def main() -> int:
    if OUTPUT.exists() or OUTPUT.is_symlink() or EVIDENCE.exists() or EVIDENCE.is_symlink():
        raise RuntimeError("fresh timeout-resume release outputs required")
    for path in SOURCES.values():
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"release source absent: {path}")

    state = verified(SOURCE / "run_state.json")
    joint = verified(RAW_BATCH)
    if not (
        state.get("status") == "FAILED_CLOSED"
        and state.get("error")
        == "RuntimeError: full-suite client QPS must be finite and positive"
        and int(state.get("outer_attempt") or 0) == 1
        and joint.get("status") == "FAILED_CLOSED"
        and float(joint.get("elapsed_seconds") or 0.0) >= 900.0
        and set(joint.get("measurements") or {}) == {"tpch_w1", "tpch_w2"}
    ):
        raise RuntimeError("TPC-H B timeout recovery boundary differs")

    rebound = metric.rebind_measurements(joint["measurements"])
    baseline_contract = normalization.baseline_contract(
        verified(BASELINE), scene=metric.SCENE,
    )
    normalization.normalize_candidate_in_place(
        rebound["tpch_w1"], baseline_contract,
    )
    rankings = verified(SOURCE / "outer_002/predicted_rankings.json")
    selected = (rankings.get("selected_top_k") or {}).get(metric.SCENE) or []
    if len(selected) != 5:
        raise RuntimeError("Outer-2 Top-5 rankings differ")

    derived: dict[int, dict[str, Any]] = {}
    for rank, worker_id in ((1, "tpch_w1"), (2, "tpch_w2")):
        row = copy.deepcopy(rebound[worker_id])
        if int(row.get("rank") or 0) != rank:
            raise RuntimeError(f"Outer-2 rank {rank} worker mapping differs")
        failures = [
            copy.deepcopy(dict(failure))
            for failure in (joint.get("failures") or [])
            if isinstance(failure, Mapping) and failure_for(worker_id, failure)
        ]
        hard_pass = (
            row.get("hard_gate_passed") is True
            and row.get("coverage_complete") is True
        )
        document = {
            "schema_version": "mysql_tpch_algorithm_b_timeout_recovery_rank_v1",
            "status": "PASS" if hard_pass and not failures else "FAILED_CLOSED",
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "elapsed_seconds": float(joint["elapsed_seconds"]),
            "measurements": {worker_id: row},
            "companion_measurements": {},
            "failures": failures,
            "candidate_rank": rank,
            "physical_candidate_wave": [1, 2],
            "physical_joint_batch_path": str(RAW_BATCH),
            "physical_joint_batch_sha256": sha(RAW_BATCH),
            "derived_without_additional_workload": True,
            "candidate_measurement_data_cross_rank_mixing": False,
            "eligible_for_outer_acceptance": hard_pass and not failures,
            "invalid_candidate_measurement_credit": False if rank == 2 else None,
        }
        derived[rank] = document
        atomic(EVIDENCE / f"rank_{rank}/batch_result.json", document)

    if (
        derived[1]["status"] != "PASS"
        or derived[2]["status"] != "FAILED_CLOSED"
        or float(derived[1]["measurements"]["tpch_w1"]["db_qps"]) <= 0.0
        or float(derived[2]["measurements"]["tpch_w2"]["db_qps"]) != 0.0
        or not any(
            "timed out after 900 seconds" in str(row.get("error") or "")
            for row in derived[2]["failures"]
        )
    ):
        raise RuntimeError("derived timeout recovery evidence differs")

    atomic(EVIDENCE / "manifest.json", {
        "schema_version": "mysql_tpch_algorithm_b_outer2_timeout_recovery_v1",
        "status": "FROZEN_RUNTIME_VALIDATED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "source_run_root": str(SOURCE),
        "source_state_sha256": sha(SOURCE / "run_state.json"),
        "physical_joint_batch_path": str(RAW_BATCH),
        "physical_joint_batch_sha256": sha(RAW_BATCH),
        "valid_rank_reused": 1,
        "invalid_timeout_rank_preserved_without_measurement_credit": 2,
        "completed_outer_reused_through": 1,
        "candidate_replay_due_to_metric_adapter_failure": False,
        "metric_contract": metric.contract(),
        "normalization_contract": baseline_contract,
    })

    created = datetime.now(timezone.utc).astimezone().isoformat()
    contract = {
        "schema_version": "mysql_tpch_algorithm_b_timeout_resume_v2",
        "status": "FROZEN_RUNTIME_VALIDATED",
        "created_at": created,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "scene": metric.SCENE,
        "active_parameters": [
            "io_block_read_cost", "memory_block_read_cost",
            "row_evaluate_cost", "disk_temptable_row_cost",
        ],
        "qps_metric_contract": metric.contract(),
        "candidate_diversity": (
            "best_model_point_plus_four_maximin_points_from_complete_ranked_pool"
        ),
        "same_worker_qps_normalization": baseline_contract,
        "completed_outer_state_reused_through": 1,
        "partial_outer": 2,
        "valid_partial_ranks_reused": [1],
        "invalid_timeout_ranks_preserved_without_measurement_credit": [2],
        "candidate_batches": [[1, 2], [3, 4], [5]],
        "physical_replay_parallelism": 2,
        "two_rejected_outer_rounds_force_incumbent_centered_shrink": True,
        "convergence_max_normalized_width": 0.01,
        "algorithm_a_parameter_or_measurement_read": False,
        "external_algorithm_evidence_used": False,
        "recovery_evidence": {
            "manifest_path": str((EVIDENCE / "manifest.json").relative_to(ROOT)),
            "manifest_sha256": sha(EVIDENCE / "manifest.json"),
            "valid_rank1_path": str((EVIDENCE / "rank_1/batch_result.json").relative_to(ROOT)),
            "valid_rank1_sha256": sha(EVIDENCE / "rank_1/batch_result.json"),
            "invalid_rank2_path": str((EVIDENCE / "rank_2/batch_result.json").relative_to(ROOT)),
            "invalid_rank2_sha256": sha(EVIDENCE / "rank_2/batch_result.json"),
        },
        "source_bindings": {
            name: {"path": str(path.relative_to(ROOT)), "sha256": sha(path)}
            for name, path in sorted(SOURCES.items())
        },
    }
    atomic(OUTPUT / "algorithm_b_timeout_resume_contract.json", contract)
    print(json.dumps({
        "status": "FROZEN_RUNTIME_VALIDATED",
        "contract": str(OUTPUT / "algorithm_b_timeout_resume_contract.json"),
        "contract_sha256": sha(OUTPUT / "algorithm_b_timeout_resume_contract.json"),
        "evidence": str(EVIDENCE / "manifest.json"),
        "evidence_sha256": sha(EVIDENCE / "manifest.json"),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
