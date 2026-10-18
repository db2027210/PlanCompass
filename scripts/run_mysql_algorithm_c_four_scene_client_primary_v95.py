#!/usr/bin/env python3
"""Resume XP/I/O from outer 55 after external PostgreSQL gate interference."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_c_xpio_external_gate_recovery_checkpoint_v1 as recovery_checkpoint
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v82 as actual_width
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v84 as predecessor


CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_full_resume_20260925_v185/"
    "algorithm_c_full_resume_contract.json"
)
CHECKPOINT = ROOT / (
    "history/mysql_four_scene_20260809/"
    "c_v185_xpio_outer55_external_gate_recovery_20260925/prepared_binding.json"
)
QUARANTINE = ROOT / (
    "history/mysql_four_scene_20260809/"
    "c_v185_xpio_outer55_quarantine_20260925/replay_quarantine.json"
)
FAILED_STATE = ROOT / (
    "history/mysql_four_scene_20260809/"
    "c_v180_external_gate_resume_20260925_xp_io/run_state.json"
)
SCENE = "hybench_xp_io"
ERROR = "RuntimeError: dynamic non-interference gate failed closed"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def argument_value(flag: str) -> str:
    try:
        index = sys.argv.index(flag)
        return sys.argv[index + 1]
    except (ValueError, IndexError) as error:
        raise RuntimeError(f"required argument missing: {flag}") from error


def validate_contract() -> dict[str, Any]:
    value = load(CONTRACT)
    recovery = dict(value.get("dynamic_gate_recovery") or {})
    if (
        value.get("schema_version")
        != "mysql_algorithm_c_full_resume_contract_v185"
        or value.get("release_version")
        != "v185-xpio-external-gate-boundary-recovery"
        or value.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("candidate_set_or_workload_changed") is not False
        or value.get("resource_envelope_changed") is not False
        or value.get("dynamic_noninterference_gate_relaxed") is not False
        or value.get("completed_rounds_replayed") is not False
        or recovery.get("resume_from_completed_outer") != 55
        or recovery.get("discarded_incomplete_outer") != 56
        or recovery.get("partial_outer_formal_credit") != 0
        or recovery.get("partial_outer_replayed_from_beginning") is not True
        or recovery.get("completed_range_lineage_preserved") is not True
        or recovery.get("discarded_outer_candidate_pool_exhausted") is not True
        or recovery.get("external_interference_failure_proven") is not True
        or recovery.get("failure_stage")
        != "top5_batch_3_dynamic_noninterference_gate"
        or recovery.get("completed_boundary_replay_signature_count") != 2
    ):
        raise RuntimeError("Algorithm C v185 contract differs")
    for name, raw in dict(value.get("source_bindings") or {}).items():
        path = (ROOT / str(raw.get("path") or "")).resolve()
        if path.is_symlink() or not path.is_file() or sha(path) != raw.get("sha256"):
            raise RuntimeError(f"Algorithm C v185 source differs: {name}: {path}")
    return value


def validate_resume_boundary() -> dict[str, Any]:
    if argument_value("--scene") != SCENE:
        raise RuntimeError("v185 is an XP/I/O-only completed-boundary resume")
    checkpoint_path = Path(argument_value("--checkpoint")).resolve()
    if checkpoint_path != CHECKPOINT.resolve():
        raise RuntimeError("v185 checkpoint differs")
    quarantine_path = Path(argument_value("--replay-quarantine")).resolve()
    if quarantine_path != QUARANTINE.resolve():
        raise RuntimeError("v185 replay quarantine differs")
    checkpoint = recovery_checkpoint.verify_checkpoint(checkpoint_path)
    boundary = dict(checkpoint.get("independent_scene_boundary") or {})
    dynamic = dict(
        checkpoint.get("independent_scene_dynamic_gate_boundary") or {}
    )
    semantics = dict(checkpoint.get("recovery_semantics") or {})
    state = load(FAILED_STATE)
    decision = load(
        Path(str(checkpoint.get("source_run_root") or "")).resolve()
        / "outer_055" / f"{SCENE}.decision.json"
    )
    failed_gate = Path(str((dynamic.get("blocked_dynamic_gate") or {}).get("path") or ""))
    if (
        checkpoint.get("status") != "PREPARED"
        or checkpoint.get("formal_scope") != "mysql_only"
        or checkpoint.get("cross_engine_comparison_prohibited") is not True
        or int(checkpoint.get("completed_outer_attempts") or 0) != 55
        or boundary.get("scene") != SCENE
        or int(boundary.get("completed_outer_attempts") or 0) != 55
        or int(boundary.get("discarded_incomplete_outer") or 0) != 56
        or boundary.get("range_lineage_preserved_from_failed_state") is not True
        or dynamic.get("discarded_entire_undecided_outer") is not True
        or dynamic.get("formal_state_credit_increment") != 0
        or dynamic.get("transient_candidate_replay_state_cleared") is not True
        or dynamic.get("discarded_outer_candidate_pool_exhausted") is not True
        or dynamic.get("external_interference_failure_proven") is not True
        or dynamic.get("completed_boundary_replay_state_preserved") is not True
        or dynamic.get("completed_boundary_replay_signature_count") != 2
        or semantics.get("candidate_set_or_workload_changed") is not False
        or semantics.get("resource_envelope_changed") is not False
        or semantics.get("dynamic_noninterference_gate_relaxed") is not False
        or state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or state.get("error") != ERROR
        or int(state.get("outer_attempt") or 0) != 55
        or decision.get("formal_evidence_eligible") is not True
        or int(decision.get("outer_attempt") or 0) != 55
        or decision.get("accepted") is not False
        or not failed_gate.is_file()
        or load(failed_gate).get("status") != "BLOCKED_FAIL_CLOSED"
        or load(quarantine_path).get("resume_checkpoint_outer_attempt") != 55
        or (load(quarantine_path).get("evidence") or {}).get("checkpoint", {}).get(
            "sha256"
        )
        != sha(checkpoint_path)
    ):
        raise RuntimeError("Algorithm C v185 recovery boundary differs")
    return checkpoint


def main() -> int:
    predecessor.validate_contract()
    validate_contract()
    old_validate_boundary = actual_width.validate_resume_boundary
    actual_width.validate_resume_boundary = validate_resume_boundary
    try:
        return int(predecessor.main())
    finally:
        actual_width.validate_resume_boundary = old_validate_boundary


if __name__ == "__main__":
    raise SystemExit(main())
