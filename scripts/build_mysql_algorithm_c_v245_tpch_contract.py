#!/usr/bin/env python3
"""Freeze the TPC-H recovery with external processes as inventory only."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history/mysql_four_scene_20260809"
OUTPUT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_tpch_external_inventory_only_20260929_v245/"
    "tpch_external_inventory_only_contract.json"
)
CHECKPOINT = HISTORY / (
    "c_v244_tpch_v243_outer10_timeout_checkpoint_20260929/prepared_binding.json"
)
QUARANTINE = HISTORY / (
    "c_v244_tpch_v243_outer10_timeout_quarantine_20260929/replay_quarantine.json"
)
FAILED_START = HISTORY / "c_v244_tpch_fifth_timeout_recovery_20260929_tpch"
FAILED_CAMPAIGN = HISTORY / "c_v244_tpch_fifth_timeout_recovery_20260929"
AUTHORITY = HISTORY / "c_v156_candidate_authority_v36_20260923/authority"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def bound(path: Path) -> dict[str, str]:
    path = path.resolve()
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular evidence required: {path}")
    return {"path": str(path.relative_to(ROOT)), "sha256": sha(path)}


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=False, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def main() -> int:
    if OUTPUT.exists() or OUTPUT.parent.exists():
        raise RuntimeError("fresh v245 contract output required")
    checkpoint = load(CHECKPOINT)
    quarantine = load(QUARANTINE)
    failed = load(FAILED_START / "run_state.json")
    if (
        checkpoint.get("schema_version")
        != "mysql_algorithm_c_single_scene_outer_resume_checkpoint_v19"
        or int(checkpoint.get("completed_outer_attempts") or -1) != 10
        or quarantine.get("status") != "PASS"
        or int((quarantine.get("permanent_invalid_counts_by_scene") or {}).get("tpch_sf4_memory", -1)) != 18
        or int((quarantine.get("soft_replayed_counts_by_scene") or {}).get("tpch_sf4_memory", -1)) != 9
        or failed.get("status") != "FAILED_CLOSED"
        or int(failed.get("outer_attempt") or -1) != 10
        or int((failed.get("accepted_rounds") or {}).get("tpch_sf4_memory", -1)) != 9
        or int((failed.get("rejected_outer_attempts") or {}).get("tpch_sf4_memory", -1)) != 1
        or not str(failed.get("error") or "").startswith(
            "RuntimeError: fresh R0 point non-interference gate blocked:"
        )
        or (FAILED_START / "outer_011").exists()
    ):
        raise RuntimeError("v245 recovery predecessor evidence differs")

    sources = {
        "checkpoint_builder": ROOT / "scripts/build_mysql_algorithm_c_scene_resume_checkpoint_v19.py",
        "current_pg_runtime": ROOT / "scripts/run_mysql_four_scene_algorithm_c_boundary_resume_v60.py",
        "inventory_only_gate": ROOT / "scripts/mysql_external_process_inventory_only_gate_v1.py",
        "inventory_only_preflight": ROOT / "scripts/preflight_mysql_four_scene_external_inventory_only_v1.py",
        "permanent_exclusion_helper": ROOT / "scripts/mysql_algorithm_c_interval_control_v3.py",
        "cleanup_predecessor_controller": ROOT / "scripts/run_mysql_algorithm_c_four_scene_client_primary_v135.py",
        "resume_controller": ROOT / "scripts/run_mysql_algorithm_c_four_scene_client_primary_v136.py",
        "recovery_launcher": ROOT / "scripts/launch_mysql_algorithm_c_v245_tpch_external_inventory_only.py",
    }
    evidence = {
        "checkpoint": CHECKPOINT,
        "quarantine": QUARANTINE,
        "failed_v244_state": FAILED_START / "run_state.json",
        "failed_v244_launch": FAILED_CAMPAIGN / "launch_manifest.json",
        "transition_preflight": AUTHORITY / "transition_preflight.json",
        "prepared_binding": AUTHORITY / "prepared_binding.json",
        "benchmark_accounts": AUTHORITY / "benchmark_accounts_binding.json",
        "fresh_r0": HISTORY / "c_v156_candidate_fresh_r0_v36_20260923/fresh_r0_binding.json",
        "formal_r0_set": HISTORY / "c_v152_formal_r0_set_v36_20260923/formal_r0_set.json",
        "formal_baseline": HISTORY / "c_v148_paper_baseline_calibration_20260923_tpch/formal_incumbent_baseline/batch_result.json",
        "resource_freeze": ROOT / "config/formal/frozen_mysql_four_scene_two_worker_20260923_v36/four_scene_effective_resource_successor.json",
    }
    document = {
        "schema_version": "mysql_algorithm_c_tpch_external_inventory_only_contract_v1",
        "release_version": "v245-tpch-external-inventory-only",
        "status": "FROZEN_RUNTIME_VALIDATED",
        "created_at": datetime.now().astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "scene": "tpch_sf4_memory",
        "completed_outer_attempts_preserved": 10,
        "accepted_decisions_preserved": 9,
        "rejected_decisions_preserved": 1,
        "failed_v244_workload_started": False,
        "failed_v244_state_credit": 0,
        "technical_timeout_candidate_count": 18,
        "permanent_invalid_candidate_count": 18,
        "soft_replayed_candidate_count": 9,
        "quarantined_candidate_count": 27,
        "technical_timeout_state_credit": 0,
        "technical_timeout_rejected_round_increment": 0,
        "normal_stop_uses_actual_interval_width": True,
        "accepted_round_target": None,
        "legacy_post_target_cap_retired": True,
        "post_search_stability_confirmation": False,
        "bounded_tpch_cleanup_retry": True,
        "tpch_cleanup_attempt_limit": 3,
        "tpch_cleanup_readback_after_error": True,
        "tpch_cleanup_lock_wait_timeout_seconds": 5,
        "permanent_invalid_exclusion_all_candidate_paths": True,
        "fresh_search_lineage_preserved": True,
        "fresh_search_reset_on_resume": False,
        "current_pg_runtime_release": "v60",
        "external_process_policy_release": "v245-external-process-inventory-only",
        "external_processes_inventory_only": True,
        "external_processes_can_block": False,
        "external_quiet_window_required": False,
        "target_mysql_local_resource_proof_required": True,
        "strict_external_noninterference_evidence_eligible": False,
        "formal_evidence_eligible": False,
        "accepted_state_credit_prohibited": False,
        "strict_external_noninterference_results_must_not_be_merged": True,
        "user_directed_policy_override": True,
        "workload_or_candidate_policy_changed": False,
        "resource_envelope_changed": False,
        "other_scene_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
        "source_bindings": {name: bound(path) for name, path in sources.items()},
        "evidence_bindings": {name: bound(path) for name, path in evidence.items()},
    }
    atomic(OUTPUT, document)
    print(json.dumps({
        "status": document["status"], "output": str(OUTPUT),
        "sha256": sha(OUTPUT),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
