#!/usr/bin/env python3
"""Freeze the XP/CPU runtime-gate generation-switch recovery contract."""

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
    "config/formal/frozen_mysql_algorithm_c_xp_cpu_runtime_gate_recovery_"
    "20260929_v237/xp_cpu_runtime_gate_recovery_contract.json"
)
CHECKPOINT = HISTORY / (
    "c_v235_xp_cpu_v228_outer19_dynamic_gate_checkpoint_20260929/"
    "prepared_binding.json"
)
QUARANTINE = HISTORY / (
    "c_v235_xp_cpu_v228_outer19_dynamic_gate_quarantine_20260929/"
    "replay_quarantine.json"
)
AUTHORITY = HISTORY / "c_v235_xp_cpu_live_identity_rebind_20260929/authority"
FRESH_R0 = HISTORY / (
    "c_v235_xp_cpu_fresh_r0_identity_successor_20260929/fresh_r0_binding.json"
)
FAILED = HISTORY / "c_v228_xp_cpu_reset_result_recovery_20260929_xp_cpu"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bound(path: Path) -> dict[str, str]:
    path = path.resolve()
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular evidence required: {path}")
    return {"path": str(path), "sha256": sha(path)}


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


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
        raise RuntimeError("fresh v237 contract output required")
    checkpoint = load(CHECKPOINT)
    quarantine = load(QUARANTINE)
    state = load(FAILED / "run_state.json")
    decision = load(FAILED / "outer_019/hybench_xp_cpu.decision.json")
    gate = load(
        FAILED / "outer_020/top5_batch_1_dynamic_noninterference_gate.json"
    )
    prepared = load(AUTHORITY / "prepared_binding.json")
    fresh = load(FRESH_R0)
    if (
        checkpoint.get("schema_version")
        != "mysql_algorithm_c_single_scene_outer_resume_checkpoint_v18"
        or int(checkpoint.get("completed_outer_attempts") or 0) != 19
        or int(checkpoint["reconstructed_state"]["accepted_rounds"]["hybench_xp_cpu"]) != 11
        or int(checkpoint["reconstructed_state"]["rejected_outer_attempts"]["hybench_xp_cpu"]) != 8
        or quarantine.get("status") != "PASS"
        or int(quarantine.get("resume_checkpoint_outer_attempt") or 0) != 19
        or state.get("error") != "RuntimeError: dynamic non-interference gate failed closed"
        or int(state.get("outer_attempt") or 0) != 19
        or decision.get("accepted") is not False
        or gate.get("status") != "BLOCKED_FAIL_CLOSED"
        or "target_mysql_local_resource_isolation_unproven"
        not in set(gate.get("errors") or [])
        or prepared.get("status") != "PREPARED"
        or fresh.get("status") != "PASS"
        or fresh.get("formal_outer_launch_authorized") is not True
    ):
        raise RuntimeError("v237 recovery evidence differs")

    sources = {
        "controller": ROOT / "scripts/run_mysql_algorithm_c_four_scene_client_primary_v132.py",
        "launcher": ROOT / "scripts/launch_mysql_algorithm_c_v237_xp_cpu_runtime_gate_recovery.py",
        "runtime_gate_authority": ROOT / "scripts/mysql_algorithm_c_runtime_gate_authority_v1.py",
        "checkpoint_builder": ROOT / "scripts/build_mysql_algorithm_c_scene_resume_checkpoint_v18.py",
        "identity_builder": ROOT / "scripts/build_mysql_algorithm_c_xp_cpu_w1_identity_rebind_v2.py",
        "quarantine_builder": ROOT / "scripts/build_mysql_algorithm_c_empty_quarantine_for_scene_checkpoint_v2.py",
        "reset_result_swappiness": ROOT / "scripts/mysql_algorithm_c_reset_affinity_v4.py",
        "dynamic_gate": ROOT / "scripts/mysql_cross_project_wave_coordination_v31.py",
        "pg_runtime": ROOT / "scripts/run_mysql_four_scene_algorithm_c_boundary_resume_v59.py",
    }
    evidence = {
        "checkpoint": CHECKPOINT,
        "quarantine": QUARANTINE,
        "authority_preflight": AUTHORITY / "transition_preflight.json",
        "authority_prepared": AUTHORITY / "prepared_binding.json",
        "authority_accounts": AUTHORITY / "benchmark_accounts_binding.json",
        "identity_rebind_audit": AUTHORITY / "container_identity_rebind_audit.json",
        "fresh_r0": FRESH_R0,
        "failed_state": FAILED / "run_state.json",
        "outer19_decision": FAILED / "outer_019/hybench_xp_cpu.decision.json",
        "outer20_failed_gate": FAILED / "outer_020/top5_batch_1_dynamic_noninterference_gate.json",
        "formal_baseline": HISTORY / "c_v149_paper_baseline_calibration_20260923_xp_cpu/formal_incumbent_baseline/batch_result.json",
        "formal_r0_set": HISTORY / "c_v152_formal_r0_set_v36_20260923/formal_r0_set.json",
        "v235_interrupted_state": HISTORY / "c_v235_xp_cpu_runtime_gate_recovery_20260929_xp_cpu/run_state.json",
        "v235_outer20_gate": HISTORY / "c_v235_xp_cpu_runtime_gate_recovery_20260929_xp_cpu/outer_020/top5_batch_1_dynamic_noninterference_gate.json",
        "v235_reset_generation": HISTORY / "c_v235_xp_cpu_runtime_gate_recovery_20260929/runtime_gate_authority/generation_0001/prepared_binding.json",
        "v236_preworkload_failure_log": HISTORY / "c_v236_xp_cpu_runtime_gate_recovery_20260929/xp_cpu.controller.log",
    }
    document = {
        "schema_version": "mysql_algorithm_c_xp_cpu_runtime_gate_recovery_contract_v3",
        "release_version": "v237-xp-cpu-runtime-gate-recovery",
        "status": "FROZEN_RUNTIME_VALIDATED",
        "created_at": datetime.now().astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "scene": "hybench_xp_cpu",
        "completed_outer_attempts_preserved": 19,
        "actual_accepted_decisions_restored": 11,
        "rejected_decisions_preserved": 8,
        "incomplete_outer_attempt": 20,
        "incomplete_outer_credit": 0,
        "fresh_search_lineage_preserved": True,
        "fresh_search_reset_on_resume": False,
        "normal_stop_uses_actual_interval_width": True,
        "terminal_normalized_width": 0.01,
        "accepted_round_target": None,
        "legacy_post_target_cap_retired": True,
        "post_search_stability_confirmation": False,
        "top_k_is_upper_bound": True,
        "candidate_batches": [[1, 2], [3, 4], [5]],
        "dynamic_gate_release": "v210",
        "current_pg_runtime_release": "v59",
        "chained_resume_entry_release": "v118",
        "post_restart_swappiness_reapplied_and_read_back": True,
        "prestart_swappiness_reapplied_and_read_back": True,
        "post_restart_identity_uses_reset_result": True,
        "dynamic_gate_uses_latest_reset_result_identity": True,
        "runtime_gate_authority_is_append_only": True,
        "dynamic_gate_environment_switches_to_each_generation": True,
        "initial_startup_authority_preserved_until_install": True,
        "runtime_gate_authority_generation_is_sha_bound_in_each_gate": True,
        "workload_or_candidate_policy_changed": False,
        "resource_envelope_changed": False,
        "completed_rounds_replayed": False,
        "dynamic_noninterference_gate_relaxed": False,
        "sibling_scene_wait": False,
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
