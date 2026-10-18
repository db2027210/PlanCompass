#!/usr/bin/env python3
"""Resume JOB after outer 2 with a non-recursive startup-gate wrapper."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


from scripts import run_mysql_algorithm_c_four_scene_client_primary_v71 as predecessor


ROOT = Path(__file__).resolve().parents[1]
runtime = predecessor.runtime
CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_full_resume_20260923_v162/"
    "algorithm_c_full_resume_contract.json"
)
V158_REUSE_STARTUP_PAPER_GATE = (
    predecessor.predecessor.reuse_startup_paper_gate
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
        != "mysql_algorithm_c_full_resume_contract_v162"
        or value.get("release_version")
        != "v162-job-outer2-nonrecursive-resume"
        or value.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("resume_completed_outer_attempt") != 2
        or value.get("discarded_incomplete_outer_attempt") != 3
        or value.get("failed_v161_outer_credit") != 0
        or value.get("startup_gate_wrapper_nonrecursive") is not True
        or value.get("full_search_runs_until_runtime_terminal_condition")
        is not True
        or value.get("fixed_outer_round_stop") is not False
        or value.get("parent_contract", {}).get("sha256")
        != sha(predecessor.CONTRACT)
    ):
        raise RuntimeError("Algorithm C v162 resume contract differs")
    for name, raw in dict(value.get("source_bindings") or {}).items():
        path = ROOT / str(raw.get("path") or "")
        if path.is_symlink() or not path.is_file() or sha(path) != raw.get("sha256"):
            raise RuntimeError(f"Algorithm C v162 source differs: {name}: {path}")
    return value


def reuse_startup_paper_gate(
    *, incumbents: Mapping[str, Any], baseline_rows: Mapping[str, Any],
    output: Path,
) -> dict[str, Any]:
    result = V158_REUSE_STARTUP_PAPER_GATE(
        incumbents=incumbents, baseline_rows=baseline_rows, output=output,
    )
    result.update({
        "schema_version":
        "mysql_algorithm_c_reused_startup_paper_baseline_gate_v3",
        "resume_after_completed_outer": 2,
        "discarded_incomplete_outer": 3,
        "boundary_checkpoint_path": str(predecessor.EXPECTED_CHECKPOINT),
        "boundary_checkpoint_sha256": sha(predecessor.EXPECTED_CHECKPOINT),
        "startup_gate_wrapper_nonrecursive": True,
        "failed_v161_outer_credit": 0,
    })
    runtime.alg.atomic(output, result)
    return result


def main() -> int:
    predecessor.validate_contract()
    validate_contract()
    old_gate = predecessor.reuse_startup_paper_gate
    predecessor.reuse_startup_paper_gate = reuse_startup_paper_gate
    try:
        return int(predecessor.main())
    finally:
        predecessor.reuse_startup_paper_gate = old_gate


if __name__ == "__main__":
    raise SystemExit(main())
