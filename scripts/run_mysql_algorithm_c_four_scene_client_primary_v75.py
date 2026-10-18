#!/usr/bin/env python3
"""Resume outer-2 JOB/XP-I/O with bounded 900s read-only plan capture."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_algorithm_c_plan_capture_timeout_v1 as plan_timeout
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v68 as predecessor
from scripts import run_mysql_four_scene_algorithm_c_v2 as alg


runtime = predecessor.runtime
CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_full_resume_20260923_v165/"
    "algorithm_c_full_resume_contract.json"
)
HISTORY = ROOT / "history/mysql_four_scene_20260809"
CHECKPOINTS = {
    "job_memory": HISTORY / (
        "c_v161_job_outer2_checkpoint_20260923/prepared_binding.json"
    ),
    "hybench_xp_io": HISTORY / (
        "c_v165_xp_io_outer2_checkpoint_20260923/prepared_binding.json"
    ),
}
LATEST_FAILED_STATES = {
    "job_memory": HISTORY / (
        "c_v164_job_outer2_resume_20260923_job/run_state.json"
    ),
    "hybench_xp_io": HISTORY / (
        "c_v158_full_algorithm_resume_20260923_xp_io/run_state.json"
    ),
}
EXPECTED_FAILURES = {
    "job_memory": "TimeoutExpired:",
    "hybench_xp_io": "outer 3 batch 1 failed closed",
}
V158_REUSE_STARTUP_PAPER_GATE = predecessor.reuse_startup_paper_gate
V2_EXPLAIN = alg.explain


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
        value.get("schema_version")
        != "mysql_algorithm_c_full_resume_contract_v165"
        or value.get("release_version")
        != "v165-plan-capture-timeout-boundary-resume"
        or value.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("resume_completed_outer_attempt") != 2
        or value.get("discarded_incomplete_outer_attempt") != 3
        or value.get("incomplete_outer_credit") != 0
        or value.get("plan_capture_timeout_seconds") != 900
        or value.get("plan_capture_outside_formal_measurement_wall_time")
        is not True
        or value.get("plan_capture_read_only") is not True
        or value.get("full_search_runs_until_runtime_terminal_condition")
        is not True
        or value.get("fixed_outer_round_stop") is not False
        or value.get("parent_contract", {}).get("path")
        != (
            "config/formal/"
            "frozen_mysql_algorithm_c_full_resume_20260923_v164/"
            "algorithm_c_full_resume_contract.json"
        )
        or value.get("parent_contract", {}).get("sha256")
        != sha(ROOT / str(value.get("parent_contract", {}).get("path") or ""))
    ):
        raise RuntimeError("Algorithm C v165 resume contract differs")
    for name, raw in dict(value.get("source_bindings") or {}).items():
        path = ROOT / str(raw.get("path") or "")
        if path.is_symlink() or not path.is_file() or sha(path) != raw.get("sha256"):
            raise RuntimeError(f"Algorithm C v165 source differs: {name}: {path}")
    return value


def validate_resume_boundary() -> dict[str, Any]:
    if "--fresh-search" in sys.argv[1:]:
        raise RuntimeError("v165 is a completed-outer resume, not a fresh search")
    owner = predecessor.predecessor
    scene = owner.argument_value("--scene")
    checkpoint_path = owner.argument_path("--checkpoint")
    if scene not in CHECKPOINTS or checkpoint_path != CHECKPOINTS[scene].resolve():
        raise RuntimeError("v165 scene/checkpoint pair differs")
    checkpoint = runtime.scene_checkpoint.verify_or_standard(checkpoint_path)
    boundary = dict(checkpoint.get("independent_scene_boundary") or {})
    source_root = Path(str(checkpoint.get("source_run_root") or "")).resolve()
    state = load(LATEST_FAILED_STATES[scene])
    decision = load(source_root / f"outer_002/{scene}.decision.json")
    latest_root = LATEST_FAILED_STATES[scene].parent
    latest_outer = latest_root / "outer_003"
    if (
        checkpoint.get("status") != "PREPARED"
        or checkpoint.get("formal_scope") != "mysql_only"
        or checkpoint.get("cross_engine_comparison_prohibited") is not True
        or int(checkpoint.get("completed_outer_attempts") or 0) != 2
        or boundary.get("scene") != scene
        or int(boundary.get("completed_outer_attempts") or 0) != 2
        or int(boundary.get("discarded_incomplete_outer") or 0) != 3
        or checkpoint.get("freeze_sha256")
        != sha(predecessor.predecessor.predecessor.FREEZE)
        or state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or EXPECTED_FAILURES[scene] not in str(state.get("error") or "")
        or int(state.get("outer_attempt") or 0) != 2
        or int(dict(state.get("accepted_rounds") or {}).get(scene) or 0) != 2
        or decision.get("accepted") is not True
        or int(decision.get("outer_attempt") or 0) != 2
        or decision.get("formal_evidence_eligible") is not True
        or bool(list(latest_outer.glob("*.decision.json")))
    ):
        raise RuntimeError("Algorithm C v165 outer-2 boundary differs")
    return checkpoint


def reuse_startup_paper_gate(
    *, incumbents: Mapping[str, Any], baseline_rows: Mapping[str, Any],
    output: Path,
) -> dict[str, Any]:
    result = V158_REUSE_STARTUP_PAPER_GATE(
        incumbents=incumbents, baseline_rows=baseline_rows, output=output,
    )
    scene = next(iter(incumbents))
    checkpoint = CHECKPOINTS[scene]
    result.update({
        "schema_version":
        "mysql_algorithm_c_reused_startup_paper_baseline_gate_v4",
        "resume_after_completed_outer": 2,
        "discarded_incomplete_outer": 3,
        "boundary_checkpoint_path": str(checkpoint),
        "boundary_checkpoint_sha256": sha(checkpoint),
        "plan_capture_timeout_seconds": 900,
        "plan_capture_outside_formal_measurement_wall_time": True,
    })
    runtime.alg.atomic(output, result)
    return result


def main() -> int:
    predecessor.validate_contract()
    validate_contract()
    validate_resume_boundary()
    boundary_owner = predecessor.predecessor
    old_boundary = boundary_owner.validate_resume_boundary
    old_gate = predecessor.reuse_startup_paper_gate
    old_explain = alg.explain
    boundary_owner.validate_resume_boundary = validate_resume_boundary
    predecessor.reuse_startup_paper_gate = reuse_startup_paper_gate
    alg.explain = plan_timeout.explain
    try:
        return int(predecessor.main())
    finally:
        alg.explain = old_explain
        predecessor.reuse_startup_paper_gate = old_gate
        boundary_owner.validate_resume_boundary = old_boundary


if __name__ == "__main__":
    raise SystemExit(main())
