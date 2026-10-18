#!/usr/bin/env python3
"""Freeze a JOB round-10 prefix before the non-enforced wave-7 gate."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v139_to_a_job2_canonical_resume_20260824_attempt001_job"
)
SOURCE_STATE = SOURCE_ROOT / "algorithm_a_state.json"
INVALID_GATE = (
    SOURCE_ROOT / "round_010/wave_007"
    / "attempt_01_cross_project_wave_quiet_gate_attempt_001.json"
)
CHECKPOINT_ROOT = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v140_to_a_job_round10_wave7_interference_checkpoint_20260825"
)
CHECKPOINT = CHECKPOINT_ROOT / "prepared_binding.json"
CONFIG = (
    ROOT / "config/formal"
    / "frozen_mysql_algorithm_a_job_round10_wave7_interference_resume_20260825_v1"
)
CONTRACT = CONFIG / "resume_contract.json"
RUNNER = ROOT / "scripts/run_mysql_algorithm_a_job_round10_wave7_interference_resume_v1.py"
PARTIAL_RUNNER = ROOT / "scripts/run_mysql_algorithm_a_partial_round_codefix_resume_v2.py"
MEASUREMENT = ROOT / "scripts/mysql_job_buffer_pool_preconditioned_measurement_v10.py"
PREFLIGHT = ROOT / "scripts/preflight_mysql_four_scene_v5.py"
GATE = ROOT / "scripts/mysql_cross_project_wave_coordination_v2.py"
AUTHORITY = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v99_to_a_external_diagnostic_resume_20260823_job_dynamic_slots_v1"
    / "job_memory.transition_authority.json"
)
FREEZE = (
    ROOT / "config/formal/frozen_mysql_algorithm_a_job_external_diagnostic_slots_20260823_v1"
    / "job_four_worker_resource_successor.json"
)


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def binding(path: Path) -> dict[str, str]:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"regular source required: {path}")
    return {"path": str(path.relative_to(ROOT)), "sha256": sha(path)}


def main() -> int:
    if CHECKPOINT.exists() or CONTRACT.exists():
        raise RuntimeError("fresh JOB interference recovery outputs required")
    state = load(SOURCE_STATE)
    gate = load(INVALID_GATE)
    checkpoint = copy.deepcopy(state.get("round_checkpoint"))
    if (
        state.get("algorithm") != "A"
        or state.get("async_scene") != "job_memory"
        or state.get("formal_scope") != "mysql_only"
        or state.get("status") != "RUNNING"
        or int(state.get("accepted") or 0) != 5
        or int(state.get("rejected_rounds") or 0) != 4
        or int(state.get("round_index") or 0) != 10
        or not isinstance(checkpoint, dict)
        or checkpoint.get("attempted_ranks") != list(range(1, 15))
        or [row.get("wave_index") for row in checkpoint.get("waves") or []]
        != list(range(1, 8))
        or gate.get("status") != "PASS"
        or gate.get("external_workload_gate_enforced") is not False
        or not gate.get("external_workloads")
    ):
        raise RuntimeError("JOB wave-7 interference boundary differs")
    for wave in range(1, 7):
        batch = SOURCE_ROOT / f"round_010/wave_{wave:03d}/attempt_01/batch_result.json"
        document = load(batch)
        if document.get("status") != "PASS" or document.get("failures"):
            raise RuntimeError(f"JOB cached wave {wave} is not valid")

    trimmed = copy.deepcopy(state)
    trimmed["status"] = "INTERRUPTED_FAIL_CLOSED"
    trimmed["stage"] = "ROUND_010_WAVE_007_EXTERNAL_GATE_NOT_ENFORCED"
    trimmed["round_checkpoint"] = copy.deepcopy(checkpoint)
    trimmed["round_checkpoint"]["attempted_ranks"] = list(range(1, 13))
    trimmed["round_checkpoint"]["valid_ranks"] = list(range(1, 13))
    trimmed["round_checkpoint"]["technical_ranks"] = []
    trimmed["round_checkpoint"]["waves"] = copy.deepcopy(
        checkpoint["waves"][:6]
    )
    trimmed["interference_recovery"] = {
        "schema_version": "mysql_algorithm_a_job_wave7_interference_checkpoint_v1",
        "created_at": now(),
        "source_state_path": str(SOURCE_STATE),
        "source_state_sha256": sha(SOURCE_STATE),
        "source_evidence_root": str(SOURCE_ROOT),
        "invalid_wave": 7,
        "invalid_ranks": [13, 14],
        "unstarted_scored_wave": 8,
        "preserved_wave_prefix": [1, 2, 3, 4, 5, 6],
        "preserved_attempted_ranks": list(range(1, 13)),
        "invalid_gate_path": str(INVALID_GATE),
        "invalid_gate_sha256": sha(INVALID_GATE),
        "invalid_reason": "external_workload_gate_enforced_false",
        "wave7_physical_result_credited": False,
        "wave8_cache_precondition_credited": False,
        "completed_outer_rounds_replayed": False,
    }
    atomic(CHECKPOINT, trimmed)

    sources = {
        "recovery_builder": binding(Path(__file__).resolve()),
        "recovery_runner": binding(RUNNER),
        "partial_round_runtime": binding(PARTIAL_RUNNER),
        "job_measurement": binding(MEASUREMENT),
        "rolling_gate": binding(GATE),
        "inventory_preflight": binding(PREFLIGHT),
        "source_state": binding(SOURCE_STATE),
        "invalid_wave7_gate": binding(INVALID_GATE),
        "runtime_authority": binding(AUTHORITY),
        "resource_freeze": binding(FREEZE),
        "checkpoint": binding(CHECKPOINT),
    }
    contract = {
        "schema_version": "mysql_algorithm_a_job_wave7_interference_resume_contract_v1",
        "release_version": "v1",
        "status": "FROZEN_RUNTIME_VALIDATED",
        "created_at": now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "algorithm": "A",
        "scene": "job_memory",
        "accepted_preserved": 5,
        "rejected_rounds_preserved": 4,
        "interrupted_round": 10,
        "cached_valid_waves": [1, 2, 3, 4, 5, 6],
        "cached_valid_ranks": list(range(1, 13)),
        "physically_replayed_waves": [],
        "first_new_physical_wave": 7,
        "first_new_physical_rank": 13,
        "external_workload_gate_enforced": True,
        "dynamic_non_interference_gate_required": True,
        "runtime_preflight_version": "v5",
        "full_job_113_once_per_measurement": True,
        "physical_replay_parallelism": 2,
        "baseline_replayed": False,
        "incumbent_replayed": False,
        "source_evidence_root": str(SOURCE_ROOT),
        "checkpoint_path": str(CHECKPOINT),
        "checkpoint_sha256": sha(CHECKPOINT),
        "authority_path": str(AUTHORITY),
        "authority_sha256": sha(AUTHORITY),
        "resource_freeze_path": str(FREEZE),
        "resource_freeze_sha256": sha(FREEZE),
        "source_bindings": sources,
    }
    atomic(CONTRACT, contract)
    print(json.dumps({
        "status": "FROZEN",
        "checkpoint": str(CHECKPOINT),
        "contract": str(CONTRACT),
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
