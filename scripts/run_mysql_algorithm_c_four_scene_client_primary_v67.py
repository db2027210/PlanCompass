#!/usr/bin/env python3
"""Resume the v156 client-primary search from a sealed outer boundary."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_c_four_scene_client_primary_v30 as fresh_boundary
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v66 as predecessor
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v53 as runtime


CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_full_resume_20260923_v157/"
    "algorithm_c_full_resume_contract.json"
)
SCENE_SOURCE = {
    "job_memory": "c_v156_four_scene_candidate_search_20260923_job",
    "tpch_sf4_memory": "c_v156_four_scene_candidate_search_20260923_tpch",
    "hybench_xp_cpu": "c_v156_four_scene_candidate_search_20260923_xp_cpu",
    "hybench_xp_io": "c_v156_four_scene_candidate_search_20260923_xp_io",
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def argument_path(name: str) -> Path:
    if name not in sys.argv:
        raise RuntimeError(f"required resume argument is absent: {name}")
    index = sys.argv.index(name) + 1
    if index >= len(sys.argv):
        raise RuntimeError(f"resume argument value is absent: {name}")
    return Path(sys.argv[index]).expanduser().resolve()


def argument_value(name: str) -> str:
    if name not in sys.argv:
        raise RuntimeError(f"required resume argument is absent: {name}")
    index = sys.argv.index(name) + 1
    if index >= len(sys.argv):
        raise RuntimeError(f"resume argument value is absent: {name}")
    return str(sys.argv[index])


def validate_contract() -> dict[str, Any]:
    value = load(CONTRACT)
    parent = predecessor.CONTRACT
    if (
        value.get("schema_version") != "mysql_algorithm_c_full_resume_contract_v157"
        or value.get("release_version") != "v157-complete-search-resume"
        or value.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("resume_only_from_completed_outer_boundary") is not True
        or value.get("incomplete_outer_credit") != 0
        or value.get("paper_baseline_tolerance_percentage_points") != 1.0
        or value.get("full_search_runs_until_runtime_terminal_condition") is not True
        or value.get("parent_contract", {}).get("sha256") != sha(parent)
    ):
        raise RuntimeError("Algorithm C v157 full-resume contract differs")
    for name, raw in dict(value.get("source_bindings") or {}).items():
        path = ROOT / str(raw.get("path") or "")
        if path.is_symlink() or not path.is_file() or sha(path) != raw.get("sha256"):
            raise RuntimeError(f"Algorithm C v157 source differs: {name}: {path}")
    return value


def validate_resume_boundary() -> dict[str, Any]:
    if "--fresh-search" in sys.argv[1:]:
        raise RuntimeError("v157 is a completed-outer resume, not a fresh search")
    scene = argument_value("--scene")
    checkpoint_path = argument_path("--checkpoint")
    checkpoint = runtime.scene_checkpoint.verify_or_standard(checkpoint_path)
    boundary = dict(checkpoint.get("independent_scene_boundary") or {})
    source_root = Path(str(checkpoint.get("source_run_root") or "")).resolve()
    if (
        scene not in SCENE_SOURCE
        or checkpoint.get("status") != "PREPARED"
        or checkpoint.get("formal_scope") != "mysql_only"
        or checkpoint.get("cross_engine_comparison_prohibited") is not True
        or int(checkpoint.get("completed_outer_attempts") or 0) != 1
        or boundary.get("scene") != scene
        or int(boundary.get("completed_outer_attempts") or 0) != 1
        or source_root.name != SCENE_SOURCE[scene]
        or checkpoint.get("freeze_sha256") != sha(predecessor.FREEZE)
    ):
        raise RuntimeError("Algorithm C v157 completed-outer resume boundary differs")
    cessation = source_root / "completed_outer_001_cessation.json"
    cessation_value = load(cessation)
    if (
        cessation_value.get("status") != "PASS"
        or cessation_value.get("scene") != scene
        or int(cessation_value.get("completed_outer_attempt") or 0) != 1
        or cessation_value.get("completed_outer_001_unchanged") is not True
        or cessation_value.get("outer_002_formal_evidence_eligible") is not False
    ):
        raise RuntimeError("Algorithm C v157 cessation evidence differs")
    return checkpoint


def resume_interval_main() -> int:
    """Run the v120+ policy without its fresh-search-only migration guard."""
    fresh_boundary.validate_contract()
    validate_resume_boundary()
    old_validate = fresh_boundary.predecessor.validate_contract
    old_contract = fresh_boundary.predecessor.CONTRACT
    fresh_boundary.predecessor.validate_contract = fresh_boundary.validate_contract
    fresh_boundary.predecessor.CONTRACT = fresh_boundary.CONTRACT
    try:
        with fresh_boundary.bind_runtime():
            return int(fresh_boundary.predecessor.main())
    finally:
        fresh_boundary.predecessor.validate_contract = old_validate
        fresh_boundary.predecessor.CONTRACT = old_contract


def main() -> int:
    predecessor.validate_contract()
    validate_contract()
    validate_resume_boundary()
    old_main = fresh_boundary.main
    fresh_boundary.main = resume_interval_main
    try:
        return int(predecessor.main())
    finally:
        fresh_boundary.main = old_main


if __name__ == "__main__":
    raise SystemExit(main())
