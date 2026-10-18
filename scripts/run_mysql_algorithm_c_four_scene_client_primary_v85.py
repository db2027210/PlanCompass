#!/usr/bin/env python3
"""Resume XP/CPU after the outer-17 range-lineage checkpoint repair."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_c_outer_checkpoint_v4 as checkpoint_verifier
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v82 as actual_width
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v84 as predecessor


CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_full_resume_20260925_v175/"
    "algorithm_c_full_resume_contract.json"
)
CHECKPOINT = ROOT / (
    "history/mysql_four_scene_20260809/"
    "c_v175_xpcpu_outer17_range_recovery_v5_20260925/prepared_binding.json"
)
FAILED_STATE = ROOT / (
    "history/mysql_four_scene_20260809/"
    "c_v174_actual_width_resume_20260925_xp_cpu/run_state.json"
)
SCENE = "hybench_xp_cpu"
ERROR = "ValueError: disk_temptable_row_cost incumbent lies outside current range"


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
    recovery = dict(value.get("range_lineage_recovery") or {})
    if (
        value.get("schema_version") != "mysql_algorithm_c_full_resume_contract_v175"
        or value.get("release_version") != "v175-xpcpu-range-lineage-recovery"
        or value.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("candidate_set_or_workload_changed") is not False
        or value.get("resource_envelope_changed") is not False
        or value.get("completed_measurements_replayed") is not False
        or recovery.get("authoritative_live_outer15_range_used") is not True
        or recovery.get("completed_outer17_rejection_preserved") is not True
        or recovery.get("resume_from_completed_outer") != 17
    ):
        raise RuntimeError("Algorithm C v175 contract differs")
    for name, raw in dict(value.get("source_bindings") or {}).items():
        path = (ROOT / str(raw.get("path") or "")).resolve()
        if path.is_symlink() or not path.is_file() or sha(path) != raw.get("sha256"):
            raise RuntimeError(f"Algorithm C v175 source differs: {name}: {path}")
    return value


def validate_resume_boundary() -> dict[str, Any]:
    if argument_value("--scene") != SCENE:
        raise RuntimeError("v175 is an XP/CPU-only completed-boundary resume")
    checkpoint_path = Path(argument_value("--checkpoint")).resolve()
    if checkpoint_path != CHECKPOINT.resolve():
        raise RuntimeError("v175 checkpoint differs")
    checkpoint = checkpoint_verifier.verify_checkpoint(checkpoint_path)
    boundary = dict(checkpoint.get("independent_scene_boundary") or {})
    recovery = dict(checkpoint.get("range_lineage_recovery") or {})
    state = load(FAILED_STATE)
    decision_path = (
        Path(str(checkpoint.get("source_run_root") or "")).resolve()
        / "outer_017" / f"{SCENE}.decision.json"
    )
    decision = load(decision_path)
    if (
        checkpoint.get("status") != "PREPARED"
        or checkpoint.get("formal_scope") != "mysql_only"
        or checkpoint.get("cross_engine_comparison_prohibited") is not True
        or int(checkpoint.get("completed_outer_attempts") or 0) != 17
        or boundary.get("scene") != SCENE
        or int(boundary.get("completed_outer_attempts") or 0) != 17
        or boundary.get("range_lineage_repaired") is not True
        or recovery.get("completed_measurements_replayed") is not False
        or recovery.get("forced_incumbent_shrink_recomputed") is not True
        or state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or state.get("error") != ERROR
        or int(state.get("outer_attempt") or 0) != 16
        or decision.get("status") != "RECOVERED_REJECTED_OUTER"
        or decision.get("accepted") is not False
        or int(decision.get("outer_attempt") or 0) != 17
        or decision.get("candidate_measurements_replayed") is not False
    ):
        raise RuntimeError("Algorithm C v175 recovery boundary differs")
    return checkpoint


def main() -> int:
    validate_contract()
    validate_resume_boundary()
    old_validation = actual_width.validate_resume_boundary
    actual_width.validate_resume_boundary = validate_resume_boundary
    try:
        return int(predecessor.main())
    finally:
        actual_width.validate_resume_boundary = old_validation


if __name__ == "__main__":
    raise SystemExit(main())
