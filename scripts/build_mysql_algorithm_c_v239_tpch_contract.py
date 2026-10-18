#!/usr/bin/env python3
"""Freeze the fourth accumulated TPC-H timeout recovery contract."""

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
    "config/formal/frozen_mysql_algorithm_c_tpch_fourth_accumulated_"
    "quarantine_recovery_20260929_v239/"
    "tpch_fourth_accumulated_quarantine_recovery_contract.json"
)
CHECKPOINT = HISTORY / (
    "c_v229_tpch_v225_outer10_timeout_checkpoint_20260929/prepared_binding.json"
)
QUARANTINE = HISTORY / (
    "c_v238_tpch_v234_outer10_timeout_quarantine_20260929/replay_quarantine.json"
)
SOURCE = HISTORY / "c_v234_tpch_third_accumulated_quarantine_recovery_20260929_tpch"
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
        raise RuntimeError("fresh v239 contract output required")
    quarantine = load(QUARANTINE)
    state = load(SOURCE / "run_state.json")
    ranking = load(SOURCE / "outer_011/predicted_rankings.json")
    if (
        quarantine.get("status") != "PASS"
        or int(quarantine.get("resume_checkpoint_outer_attempt") or 0) != 10
        or int((quarantine.get("permanent_invalid_counts_by_scene") or {}).get("tpch_sf4_memory", -1)) != 17
        or int((quarantine.get("soft_replayed_counts_by_scene") or {}).get("tpch_sf4_memory", -1)) != 8
        or int((quarantine.get("quarantined_counts_by_scene") or {}).get("tpch_sf4_memory", -1)) != 25
        or quarantine.get("formal_state_mutation") is not False
        or state.get("status") != "FAILED_CLOSED"
        or state.get("error") != "RuntimeError: Algorithm C candidate batch incomplete or technically invalid"
        or int(state.get("outer_attempt") or -1) != 10
        or int(ranking.get("outer_attempt") or 0) != 11
        or len((ranking.get("selected_top_k") or {}).get("tpch_sf4_memory") or []) != 5
    ):
        raise RuntimeError("v239 TPC-H recovery evidence differs")
    sources = {
        "checkpoint_builder": ROOT / "scripts/build_mysql_algorithm_c_scene_resume_checkpoint_v17.py",
        "current_pg_runtime": ROOT / "scripts/run_mysql_four_scene_algorithm_c_boundary_resume_v59.py",
        "dynamic_gate": ROOT / "scripts/mysql_cross_project_wave_coordination_v31.py",
        "permanent_exclusion_helper": ROOT / "scripts/mysql_algorithm_c_interval_control_v3.py",
        "quarantine_builder": ROOT / "scripts/build_mysql_algorithm_c_tpc_timeout_quarantine_v6.py",
        "recovery_launcher": ROOT / "scripts/launch_mysql_algorithm_c_v239_tpch_fourth_accumulated_quarantine_recovery.py",
        "resume_controller": ROOT / "scripts/run_mysql_algorithm_c_four_scene_client_primary_v133.py",
    }
    evidence = {
        "checkpoint": CHECKPOINT,
        "quarantine": QUARANTINE,
        "transition_preflight": AUTHORITY / "transition_preflight.json",
        "prepared_binding": AUTHORITY / "prepared_binding.json",
        "benchmark_accounts": AUTHORITY / "benchmark_accounts_binding.json",
        "fresh_r0": HISTORY / "c_v156_candidate_fresh_r0_v36_20260923/fresh_r0_binding.json",
        "formal_r0_set": HISTORY / "c_v152_formal_r0_set_v36_20260923/formal_r0_set.json",
        "formal_baseline": HISTORY / "c_v148_paper_baseline_calibration_20260923_tpch/formal_incumbent_baseline/batch_result.json",
        "resource_freeze": ROOT / "config/formal/frozen_mysql_four_scene_two_worker_20260923_v36/four_scene_effective_resource_successor.json",
        "source_state": SOURCE / "run_state.json",
        "source_ranking": SOURCE / "outer_011/predicted_rankings.json",
        "source_batch_1": SOURCE / "outer_011/top5_batch_1/batch_result.json",
        "source_batch_2": SOURCE / "outer_011/top5_batch_2/batch_result.json",
        "source_batch_3": SOURCE / "outer_011/top5_batch_3/batch_result.json",
    }
    document = {
        "schema_version": "mysql_algorithm_c_tpch_fourth_accumulated_quarantine_recovery_contract_v1",
        "release_version": "v239-tpch-fourth-accumulated-quarantine-recovery",
        "status": "FROZEN_RUNTIME_VALIDATED",
        "created_at": datetime.now().astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "scene": "tpch_sf4_memory",
        "completed_outer_attempts_preserved": 10,
        "accepted_decisions_preserved": 9,
        "rejected_decisions_preserved": 1,
        "discarded_incomplete_outer": 11,
        "failed_outer_011_state_credit": 0,
        "latest_failed_outer_timeout_candidates": 3,
        "latest_failed_outer_valid_zero_credit_replays": 2,
        "technical_timeout_candidate_count": 17,
        "permanent_invalid_candidate_count": 17,
        "soft_replayed_candidate_count": 8,
        "quarantined_candidate_count": 25,
        "technical_timeout_state_credit": 0,
        "technical_timeout_rejected_round_increment": 0,
        "normal_stop_uses_actual_interval_width": True,
        "accepted_round_target": None,
        "legacy_post_target_cap_retired": True,
        "post_search_stability_confirmation": False,
        "bounded_tpch_cleanup_retry": True,
        "tpch_cleanup_attempt_limit": 3,
        "permanent_invalid_exclusion_all_candidate_paths": True,
        "fresh_search_lineage_preserved": True,
        "fresh_search_reset_on_resume": False,
        "current_pg_runtime_release": "v59",
        "dynamic_gate_release": "v210",
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
