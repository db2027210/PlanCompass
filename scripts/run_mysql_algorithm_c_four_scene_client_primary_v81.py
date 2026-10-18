#!/usr/bin/env python3
"""Resume JOB outer 5 after zero-credit external PG interference."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_c_four_scene_client_primary_v79 as predecessor


CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_full_resume_20260923_v171/"
    "algorithm_c_full_resume_contract.json"
)
FAILED_STATE = ROOT / (
    "history/mysql_four_scene_20260809/"
    "c_v167_job_exec_resume_20260923_job/run_state.json"
)
CHECKPOINT = ROOT / (
    "history/mysql_four_scene_20260809/"
    "c_v171_job_outer5_checkpoint_20260923/prepared_binding.json"
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def validate_contract() -> dict[str, Any]:
    value = load(CONTRACT)
    if (
        value.get("schema_version") != "mysql_algorithm_c_full_resume_contract_v171"
        or value.get("release_version") != "v171-job-outer5-interference-resume"
        or value.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("resume_completed_outer_attempt") != 5
        or value.get("failed_outer_credit") != 0
        or value.get("external_process_control_or_stop_performed") is not False
        or value.get("postgresql_process_control_or_stop_performed") is not False
        or value.get("control_sql_timeout_seconds") != 300
        or value.get("plan_capture_timeout_seconds") != 900
        or value.get("formal_measurement_timeout_changed") is not False
        or value.get("candidate_set_or_workload_changed") is not False
        or value.get("resource_envelope_changed") is not False
        or value.get("full_search_runs_until_runtime_terminal_condition") is not True
        or value.get("fixed_outer_round_stop") is not False
    ):
        raise RuntimeError("Algorithm C v171 resume contract differs")
    for name, raw in dict(value.get("source_bindings") or {}).items():
        path = ROOT / str(raw.get("path") or "")
        if path.is_symlink() or not path.is_file() or sha(path) != raw.get("sha256"):
            raise RuntimeError(f"Algorithm C v171 source differs: {name}: {path}")
    return value


def validate_failed_attempt() -> None:
    state = load(FAILED_STATE)
    if (
        state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or state.get("error") != "RuntimeError: dynamic non-interference gate failed closed"
        or int(state.get("outer_attempt") or 0) != 5
        or int(dict(state.get("accepted_rounds") or {}).get("job_memory") or 0) != 5
        or int(dict(state.get("rejected_outer_attempts") or {}).get("job_memory") or 0) != 0
    ):
        raise RuntimeError("Algorithm C v171 failed JOB attempt differs")


def validate_resume_boundary() -> dict[str, Any]:
    if "--fresh-search" in sys.argv[1:]:
        raise RuntimeError("v171 is a completed-outer resume")
    owner = predecessor.predecessor.predecessor.predecessor
    scene = owner.argument_value("--scene")
    checkpoint_path = owner.argument_path("--checkpoint")
    if scene != "job_memory" or checkpoint_path != CHECKPOINT.resolve():
        raise RuntimeError("v171 scene/checkpoint differs")
    checkpoint = predecessor.runtime.scene_checkpoint.verify_or_standard(
        checkpoint_path
    )
    boundary = dict(checkpoint.get("independent_scene_boundary") or {})
    source_root = Path(str(checkpoint.get("source_run_root") or "")).resolve()
    decision = load(source_root / "outer_005/job_memory.decision.json")
    state = load(FAILED_STATE)
    if (
        checkpoint.get("status") != "PREPARED"
        or checkpoint.get("formal_scope") != "mysql_only"
        or checkpoint.get("cross_engine_comparison_prohibited") is not True
        or int(checkpoint.get("completed_outer_attempts") or 0) != 5
        or boundary.get("scene") != scene
        or int(boundary.get("completed_outer_attempts") or 0) != 5
        or state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or state.get("error")
        != "RuntimeError: dynamic non-interference gate failed closed"
        or int(state.get("outer_attempt") or 0) != 5
        or decision.get("formal_evidence_eligible") is not True
        or decision.get("accepted") is not True
        or int(decision.get("outer_attempt") or 0) != 5
        or list((source_root / "outer_006").glob("*.decision.json"))
    ):
        raise RuntimeError("Algorithm C v171 completed boundary differs")
    return checkpoint


def main() -> int:
    validate_contract()
    validate_failed_attempt()
    old = predecessor.BOUNDARIES.get("job_memory")
    predecessor.BOUNDARIES["job_memory"] = {
        "completed": 5,
        "checkpoint": CHECKPOINT,
        "failed_state": FAILED_STATE,
    }
    old_validate = predecessor.validate_resume_boundary
    predecessor.validate_resume_boundary = validate_resume_boundary
    try:
        return int(predecessor.main())
    finally:
        predecessor.validate_resume_boundary = old_validate
        if old is None:
            predecessor.BOUNDARIES.pop("job_memory", None)
        else:
            predecessor.BOUNDARIES["job_memory"] = old


if __name__ == "__main__":
    raise SystemExit(main())
