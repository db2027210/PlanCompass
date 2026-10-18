#!/usr/bin/env python3
"""Resume all scenes with actual-width credit and sibling D-state fixes."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_c_actual_width_checkpoint_v5 as actual_checkpoint
from scripts import mysql_cross_project_wave_coordination_v28 as coordination_v28
from scripts import mysql_algorithm_c_measurement_client_primary_v4 as measurement
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v70 as normalization
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v79 as predecessor
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v20 as v20
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v21 as v21
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v23 as v23
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v29 as v29


runtime = predecessor.runtime
HISTORY = ROOT / "history/mysql_four_scene_20260809"
CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_full_resume_20260925_v172/"
    "algorithm_c_full_resume_contract.json"
)
BOUNDARIES = {
    "job_memory": {
        "completed": 6,
        "checkpoint": HISTORY / "c_v172_job_outer6_checkpoint_20260925/prepared_binding.json",
        "failed_state": HISTORY / "c_v171_job_outer5_quiet_resume_20260923_job/run_state.json",
        "error": "RuntimeError: dynamic non-interference gate failed closed",
    },
    "tpch_sf4_memory": {
        "completed": 15,
        "checkpoint": HISTORY / "c_v172_tpch_outer15_checkpoint_20260925/prepared_binding.json",
        "failed_state": HISTORY / "c_v170_tpch_normalized_control_sql_resume_20260923_tpch/run_state.json",
        "error": "RuntimeError: dynamic non-interference gate failed closed",
    },
    "hybench_xp_cpu": {
        "completed": 15,
        "checkpoint": HISTORY / "c_v172_xpcpu_outer15_checkpoint_20260925/prepared_binding.json",
        "failed_state": HISTORY / "c_v169_control_sql_resume_20260923_xp_cpu/run_state.json",
        "error": "RuntimeError: dynamic non-interference gate failed closed",
    },
    "hybench_xp_io": {
        "completed": 28,
        "checkpoint": HISTORY / "c_v172_xpio_outer28_checkpoint_20260925/prepared_binding.json",
        "failed_state": HISTORY / "c_v168_xpio_outer2_retry_20260923_xp_io/run_state.json",
        "error": "RuntimeError: post-target confirmation counter exceeds bound",
    },
}


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
        value.get("schema_version") != "mysql_algorithm_c_full_resume_contract_v172"
        or value.get("release_version") != "v172-actual-width-full-continuation"
        or value.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("normal_stop_uses_actual_interval_width") is not True
        or value.get("accepted_round_target") is not None
        or value.get("legacy_post_target_cap_retired") is not True
        or value.get("full_search_runs_until_runtime_terminal_condition") is not True
        or value.get("fixed_outer_round_stop") is not False
        or value.get("failed_incomplete_outer_credit") != 0
        or value.get("candidate_set_or_workload_changed") is not False
        or value.get("resource_envelope_changed") is not False
        or value.get("formal_measurement_timeout_changed") is not False
        or value.get("postgresql_process_control_or_stop_performed") is not False
    ):
        raise RuntimeError("Algorithm C v172 contract differs")
    for name, raw in dict(value.get("source_bindings") or {}).items():
        path = ROOT / str(raw.get("path") or "")
        if path.is_symlink() or not path.is_file() or sha(path) != raw.get("sha256"):
            raise RuntimeError(f"Algorithm C v172 source differs: {name}: {path}")
    return value


def validate_resume_boundary() -> dict[str, Any]:
    owner = predecessor.predecessor.predecessor.predecessor
    scene = owner.argument_value("--scene")
    checkpoint_path = owner.argument_path("--checkpoint")
    if scene not in BOUNDARIES:
        raise RuntimeError("v172 scene differs")
    expected = BOUNDARIES[scene]
    if checkpoint_path != expected["checkpoint"].resolve():
        raise RuntimeError("v172 checkpoint differs")
    checkpoint = actual_checkpoint.verify_or_standard(checkpoint_path)
    completed = int(expected["completed"])
    boundary = dict(checkpoint.get("independent_scene_boundary") or {})
    state = load(expected["failed_state"])
    source = Path(str(checkpoint.get("source_run_root") or "")).resolve()
    decision = load(source / f"outer_{completed:03d}/{scene}.decision.json")
    if (
        checkpoint.get("status") != "PREPARED"
        or checkpoint.get("formal_scope") != "mysql_only"
        or checkpoint.get("cross_engine_comparison_prohibited") is not True
        or int(checkpoint.get("completed_outer_attempts") or 0) != completed
        or boundary.get("scene") != scene
        or int(boundary.get("completed_outer_attempts") or 0) != completed
        or state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or state.get("error") != expected["error"]
        or int(state.get("outer_attempt") or 0) != completed
        or decision.get("formal_evidence_eligible") is not True
        or int(decision.get("outer_attempt") or 0) != completed
        or list((source / f"outer_{completed + 1:03d}").glob("*.decision.json"))
    ):
        raise RuntimeError("Algorithm C v172 completed boundary differs")
    return checkpoint


def actual_width_state_document(**kwargs: Any) -> dict[str, Any]:
    document = v29.ORIGINAL_STATE_DOCUMENT(**kwargs)
    document.pop("post_target_stability_confirmation_release", None)
    document.pop("post_target_stability_confirmation_release_v29", None)
    document.pop("durable_post_target_confirmation_release", None)
    document["post_target_stability_confirmation_counts"] = {}
    document.setdefault("attempt_budget", {}).pop(
        "post_target_stability_confirmation", None
    )
    document["actual_width_credit_release"] = {
        "release_version": "v172",
        "source_path": str(Path(__file__).resolve()),
        "source_sha256": sha(Path(__file__).resolve()),
        "normal_stop_uses_actual_interval_width": True,
        "accepted_round_target": None,
        "accepted_improvements_remain_credited_after_round_10": True,
        "legacy_post_target_confirmation_cap_retired": True,
        "candidate_scoring_workload_and_interval_rules_changed": False,
    }
    return document


def main() -> int:
    validate_contract()
    validate_resume_boundary()
    scene = predecessor.predecessor.predecessor.predecessor.argument_value("--scene")

    old_boundaries = predecessor.BOUNDARIES
    old_validate_contract = predecessor.validate_contract
    old_validate_boundary = predecessor.validate_resume_boundary
    old_checkpoint = runtime.scene_checkpoint
    old_v21_original = v21.ORIGINAL_STATE_DOCUMENT
    old_rehydrate = v23._rehydrate
    old_v29_state = v29.state_document
    old_v29_validate = v29.validate_three_confirmation_release
    old_normalization = copy.deepcopy(measurement._NORMALIZATION)

    predecessor.BOUNDARIES = BOUNDARIES
    predecessor.validate_contract = validate_contract
    predecessor.validate_resume_boundary = validate_resume_boundary
    runtime.scene_checkpoint = actual_checkpoint
    v21.ORIGINAL_STATE_DOCUMENT = v20.ORIGINAL_STATE_DOCUMENT
    v23._rehydrate = lambda document: document
    v29.state_document = actual_width_state_document
    v29.validate_three_confirmation_release = lambda: None
    coordination_v28.install()
    measurement._NORMALIZATION.clear()
    measurement._NORMALIZATION.update(normalization.normalization_contract())
    try:
        return int(predecessor.main())
    finally:
        measurement._NORMALIZATION.clear()
        measurement._NORMALIZATION.update(old_normalization)
        v29.validate_three_confirmation_release = old_v29_validate
        v29.state_document = old_v29_state
        v23._rehydrate = old_rehydrate
        v21.ORIGINAL_STATE_DOCUMENT = old_v21_original
        runtime.scene_checkpoint = old_checkpoint
        predecessor.validate_resume_boundary = old_validate_boundary
        predecessor.validate_contract = old_validate_contract
        predecessor.BOUNDARIES = old_boundaries


if __name__ == "__main__":
    raise SystemExit(main())
