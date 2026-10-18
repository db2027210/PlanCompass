#!/usr/bin/env python3
"""Freeze the two-scene XP external-process inventory-only recovery."""

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
    "config/formal/frozen_mysql_algorithm_c_xp_external_inventory_only_20260929_v247/"
    "xp_external_inventory_only_contract.json"
)
CPU_CHECKPOINT = HISTORY / "c_v247_xp_cpu_v237_outer22_external_gate_checkpoint_20260929/prepared_binding.json"
CPU_QUARANTINE = HISTORY / "c_v247_xp_cpu_v237_outer22_external_gate_quarantine_v2_20260929/replay_quarantine.json"
IO_CHECKPOINT = HISTORY / "c_v247_xp_io_v211f_outer18_external_gate_checkpoint_20260929/prepared_binding.json"
IO_QUARANTINE = HISTORY / "c_v247_xp_io_v211f_outer18_external_gate_quarantine_v2_20260929/replay_quarantine.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def bound(path: Path) -> dict[str, str]:
    path = path.resolve()
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"regular evidence required: {path}")
    return {"path": str(path.relative_to(ROOT)), "sha256": sha(path)}


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=False, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii"
    )


def main() -> int:
    if OUTPUT.exists() or OUTPUT.parent.exists():
        raise RuntimeError("fresh v247 contract output required")
    cpu_checkpoint, io_checkpoint = load(CPU_CHECKPOINT), load(IO_CHECKPOINT)
    cpu_quarantine, io_quarantine = load(CPU_QUARANTINE), load(IO_QUARANTINE)
    if (
        cpu_checkpoint.get("scene") != "hybench_xp_cpu"
        or int(cpu_checkpoint.get("completed_outer_attempts") or -1) != 22
        or int(cpu_checkpoint["reconstructed_state"]["accepted_rounds"]["hybench_xp_cpu"]) != 13
        or int(cpu_checkpoint["reconstructed_state"]["rejected_outer_attempts"]["hybench_xp_cpu"]) != 9
        or io_checkpoint.get("scene") != "hybench_xp_io"
        or int(io_checkpoint.get("completed_outer_attempts") or -1) != 18
        or int(io_checkpoint["reconstructed_state"]["accepted_rounds"]["hybench_xp_io"]) != 7
        or int(io_checkpoint["reconstructed_state"]["rejected_outer_attempts"]["hybench_xp_io"]) != 11
        or int(cpu_quarantine["soft_replayed_counts_by_scene"]["hybench_xp_cpu"]) != 2
        or int(io_quarantine["soft_replayed_counts_by_scene"]["hybench_xp_io"]) != 4
    ):
        raise RuntimeError("v247 XP checkpoint or quarantine differs")
    sources = {
        "checkpoint_builder": ROOT / "scripts/build_mysql_algorithm_c_scene_resume_checkpoint_v20.py",
        "quarantine_builder": ROOT / "scripts/build_mysql_algorithm_c_external_gate_replay_quarantine_v1.py",
        "current_pg_runtime": ROOT / "scripts/run_mysql_four_scene_algorithm_c_boundary_resume_v61.py",
        "inventory_only_gate": ROOT / "scripts/mysql_external_process_inventory_only_gate_v1.py",
        "inventory_only_preflight": ROOT / "scripts/preflight_mysql_four_scene_external_inventory_only_v1.py",
        "reset_identity_predecessor": ROOT / "scripts/run_mysql_algorithm_c_four_scene_client_primary_v132.py",
        "resume_controller": ROOT / "scripts/run_mysql_algorithm_c_four_scene_client_primary_v138.py",
        "launcher": ROOT / "scripts/launch_mysql_algorithm_c_v247_xp_external_inventory_only.py",
    }
    evidence = {
        "cpu_checkpoint": CPU_CHECKPOINT, "cpu_quarantine": CPU_QUARANTINE,
        "io_checkpoint": IO_CHECKPOINT, "io_quarantine": IO_QUARANTINE,
        "cpu_failed_state": HISTORY / "c_v237_xp_cpu_runtime_gate_recovery_20260929_xp_cpu/run_state.json",
        "cpu_blocked_gate": HISTORY / "c_v237_xp_cpu_runtime_gate_recovery_20260929_xp_cpu/outer_023/top5_batch_2_dynamic_noninterference_gate.json",
        "io_failed_state": HISTORY / "c_v211f_pg_dynamic_gate_four_scene_20260929_xp_io/run_state.json",
        "io_blocked_gate": HISTORY / "c_v211f_pg_dynamic_gate_four_scene_20260929_xp_io/outer_019/top5_batch_3_dynamic_noninterference_gate.json",
        "cpu_baseline": HISTORY / "c_v149_paper_baseline_calibration_20260923_xp_cpu/formal_incumbent_baseline/batch_result.json",
        "io_baseline": HISTORY / "c_v150_paper_baseline_calibration_20260923_xp_io/formal_incumbent_baseline/batch_result.json",
        "native_xp_manifest": ROOT / "artifacts/mysql_xp_native_iq1_iq7_successor_v2/e3e3a32e6b095e10d5e229be5ec211131f626374bc82764d98ad3f910344e017/manifest.json",
    }
    document = {
        "schema_version": "mysql_algorithm_c_xp_external_inventory_only_contract_v1",
        "release_version": "v247-xp-external-inventory-only",
        "status": "FROZEN_RUNTIME_VALIDATED",
        "created_at": datetime.now().astimezone().isoformat(),
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "scenes": ["hybench_xp_cpu", "hybench_xp_io"],
        "completed_outer_attempts_preserved": {"hybench_xp_cpu": 22, "hybench_xp_io": 18},
        "accepted_decisions_preserved": {"hybench_xp_cpu": 13, "hybench_xp_io": 7},
        "rejected_decisions_preserved": {"hybench_xp_cpu": 9, "hybench_xp_io": 11},
        "soft_aborted_replays": {"hybench_xp_cpu": 2, "hybench_xp_io": 4},
        "aborted_outer_state_credit": 0,
        "normal_stop_uses_actual_interval_width": True,
        "accepted_round_target": None,
        "legacy_post_target_cap_retired": True,
        "post_search_stability_confirmation": False,
        "current_pg_runtime_release": "v61",
        "runtime_gate_authority_follows_reset_identity": True,
        "external_processes_inventory_only": True,
        "external_processes_can_block": False,
        "external_quiet_window_required": False,
        "target_mysql_local_resource_proof_required": True,
        "strict_external_noninterference_evidence_eligible": False,
        "formal_evidence_eligible": False,
        "accepted_state_credit_prohibited": False,
        "native_xp_workload_unchanged": True,
        "native_xp_iq_selection": "uniform_random_IQ1_IQ7",
        "native_xp_at_mix_percent": [35, 25, 15, 15, 7, 3],
        "native_xp_xap_xtp_ratio_unchanged": True,
        "workload_or_candidate_policy_changed": False,
        "resource_envelope_changed": False,
        "strict_external_noninterference_results_must_not_be_merged": True,
        "postgresql_process_control_or_stop_performed": False,
        "source_bindings": {name: bound(path) for name, path in sources.items()},
        "evidence_bindings": {name: bound(path) for name, path in evidence.items()},
    }
    atomic(OUTPUT, document)
    print(json.dumps({"status": document["status"], "output": str(OUTPUT), "sha256": sha(OUTPUT)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
