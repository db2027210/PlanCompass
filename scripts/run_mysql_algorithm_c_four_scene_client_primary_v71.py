#!/usr/bin/env python3
"""Resume JOB after outer 2 while retaining the validated startup baseline."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_c_four_scene_client_primary_v68 as predecessor


runtime = predecessor.runtime
CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_full_resume_20260923_v161/"
    "algorithm_c_full_resume_contract.json"
)
EXPECTED_CHECKPOINT = ROOT / (
    "history/mysql_four_scene_20260809/"
    "c_v161_job_outer2_checkpoint_20260923/prepared_binding.json"
)
EXPECTED_SOURCE_ROOT = ROOT / (
    "history/mysql_four_scene_20260809/"
    "c_v158_full_algorithm_resume_20260923_job"
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
        value.get("schema_version")
        != "mysql_algorithm_c_full_resume_contract_v161"
        or value.get("release_version") != "v161-job-outer2-boundary-resume"
        or value.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("resume_only_from_completed_outer_boundary") is not True
        or value.get("resume_completed_outer_attempt") != 2
        or value.get("discarded_incomplete_outer_attempt") != 3
        or value.get("incomplete_outer_credit") != 0
        or value.get("startup_paper_baseline_gate_is_campaign_invariant")
        is not True
        or value.get("paper_baseline_tolerance_percentage_points") != 1.0
        or value.get("full_search_runs_until_runtime_terminal_condition")
        is not True
        or value.get("fixed_outer_round_stop") is not False
        or value.get("parent_contract", {}).get("sha256")
        != sha(predecessor.CONTRACT)
    ):
        raise RuntimeError("Algorithm C v161 resume contract differs")
    for name, raw in dict(value.get("source_bindings") or {}).items():
        path = ROOT / str(raw.get("path") or "")
        if path.is_symlink() or not path.is_file() or sha(path) != raw.get("sha256"):
            raise RuntimeError(f"Algorithm C v161 source differs: {name}: {path}")
    return value


def validate_resume_boundary() -> dict[str, Any]:
    if "--fresh-search" in sys.argv[1:]:
        raise RuntimeError("v161 is a completed-outer resume, not a fresh search")
    scene = predecessor.predecessor.argument_value("--scene")
    checkpoint_path = predecessor.predecessor.argument_path("--checkpoint")
    checkpoint = runtime.scene_checkpoint.verify_or_standard(checkpoint_path)
    boundary = dict(checkpoint.get("independent_scene_boundary") or {})
    source_root = Path(str(checkpoint.get("source_run_root") or "")).resolve()
    state = load(EXPECTED_SOURCE_ROOT / "run_state.json")
    decision = load(EXPECTED_SOURCE_ROOT / "outer_002/job_memory.decision.json")
    incomplete = EXPECTED_SOURCE_ROOT / "outer_003"
    if (
        scene != "job_memory"
        or checkpoint_path != EXPECTED_CHECKPOINT.resolve()
        or checkpoint.get("status") != "PREPARED"
        or checkpoint.get("formal_scope") != "mysql_only"
        or checkpoint.get("cross_engine_comparison_prohibited") is not True
        or int(checkpoint.get("completed_outer_attempts") or 0) != 2
        or boundary.get("scene") != scene
        or int(boundary.get("completed_outer_attempts") or 0) != 2
        or int(boundary.get("discarded_incomplete_outer") or 0) != 3
        or source_root != EXPECTED_SOURCE_ROOT.resolve()
        or checkpoint.get("freeze_sha256")
        != sha(predecessor.predecessor.predecessor.FREEZE)
        or state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or state.get("error")
        != "RuntimeError: dynamic non-interference gate failed closed"
        or int(state.get("outer_attempt") or 0) != 2
        or int(dict(state.get("accepted_rounds") or {}).get(scene) or 0) != 2
        or decision.get("accepted") is not True
        or int(decision.get("outer_attempt") or 0) != 2
        or decision.get("formal_evidence_eligible") is not True
        or not incomplete.is_dir()
        or bool(list(incomplete.glob("*.decision.json")))
    ):
        raise RuntimeError("Algorithm C v161 JOB outer-2 boundary differs")
    return checkpoint


def reuse_startup_paper_gate(
    *, incumbents: Mapping[str, Any], baseline_rows: Mapping[str, Any],
    output: Path,
) -> dict[str, Any]:
    result = predecessor.reuse_startup_paper_gate(
        incumbents=incumbents, baseline_rows=baseline_rows, output=output,
    )
    result.update({
        "schema_version":
        "mysql_algorithm_c_reused_startup_paper_baseline_gate_v2",
        "resume_after_completed_outer": 2,
        "discarded_incomplete_outer": 3,
        "boundary_checkpoint_path": str(EXPECTED_CHECKPOINT),
        "boundary_checkpoint_sha256": sha(EXPECTED_CHECKPOINT),
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
    boundary_owner.validate_resume_boundary = validate_resume_boundary
    predecessor.reuse_startup_paper_gate = reuse_startup_paper_gate
    try:
        return int(predecessor.main())
    finally:
        predecessor.reuse_startup_paper_gate = old_gate
        boundary_owner.validate_resume_boundary = old_boundary


if __name__ == "__main__":
    raise SystemExit(main())
