#!/usr/bin/env python3
"""Retry XP/I-O from its last completed outer after a zero-credit instability."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_c_four_scene_client_primary_v75 as predecessor


runtime = predecessor.runtime
CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_full_resume_20260923_v168/"
    "algorithm_c_full_resume_contract.json"
)
FAILED_STATE = ROOT / (
    "history/mysql_four_scene_20260809/"
    "c_v165_job_xpio_outer2_resume_20260923_xp_io/run_state.json"
)
CHECKPOINT = ROOT / (
    "history/mysql_four_scene_20260809/"
    "c_v165_xp_io_outer2_checkpoint_20260923/prepared_binding.json"
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
    parent = dict(value.get("parent_contract") or {})
    failure = dict(value.get("failed_attempt") or {})
    checkpoint = dict(value.get("resume_boundary") or {})
    if (
        value.get("schema_version")
        != "mysql_algorithm_c_full_resume_contract_v168"
        or value.get("release_version")
        != "v168-xp-io-zero-credit-instability-retry"
        or value.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("full_search_runs_until_runtime_terminal_condition")
        is not True
        or value.get("fixed_outer_round_stop") is not False
        or value.get("candidate_set_or_workload_changed") is not False
        or value.get("resource_envelope_changed") is not False
        or value.get("failed_outer_credit") != 0
        or value.get("resume_completed_outer_attempt") != 2
        or parent.get("path")
        != (
            "config/formal/"
            "frozen_mysql_algorithm_c_full_resume_20260923_v165/"
            "algorithm_c_full_resume_contract.json"
        )
        or parent.get("sha256") != sha(ROOT / str(parent.get("path") or ""))
        or failure.get("path") != str(FAILED_STATE.relative_to(ROOT))
        or failure.get("sha256") != sha(FAILED_STATE)
        or checkpoint.get("path") != str(CHECKPOINT.relative_to(ROOT))
        or checkpoint.get("sha256") != sha(CHECKPOINT)
    ):
        raise RuntimeError("Algorithm C v168 resume contract differs")
    for name, raw in dict(value.get("source_bindings") or {}).items():
        path = ROOT / str(raw.get("path") or "")
        if path.is_symlink() or not path.is_file() or sha(path) != raw.get("sha256"):
            raise RuntimeError(f"Algorithm C v168 source differs: {name}: {path}")
    return value


def validate_failed_attempt() -> None:
    state = load(FAILED_STATE)
    exclusions = list(state.get("technical_batch_exclusions") or [])
    if (
        state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or state.get("error")
        != "RuntimeError: Algorithm C candidate batch incomplete or technically invalid"
        or int(state.get("outer_attempt") or 0) != 2
        or int(dict(state.get("accepted_rounds") or {}).get("hybench_xp_io") or 0)
        != 2
        or len(exclusions) != 1
        or exclusions[0].get("classification")
        != "TECHNICAL_XP_TARGET_RESOURCE_INSTABILITY_EXCLUDED"
        or exclusions[0].get("accepted_state_credit_increment") != 0
        or exclusions[0].get("rejected_outer_attempt_increment") != 0
    ):
        raise RuntimeError("Algorithm C v168 failed XP-I/O attempt differs")


def main() -> int:
    validate_contract()
    validate_failed_attempt()
    old_state = predecessor.LATEST_FAILED_STATES["hybench_xp_io"]
    old_error = predecessor.EXPECTED_FAILURES["hybench_xp_io"]
    predecessor.LATEST_FAILED_STATES["hybench_xp_io"] = FAILED_STATE
    predecessor.EXPECTED_FAILURES["hybench_xp_io"] = (
        "candidate batch incomplete or technically invalid"
    )
    try:
        return int(predecessor.main())
    finally:
        predecessor.LATEST_FAILED_STATES["hybench_xp_io"] = old_state
        predecessor.EXPECTED_FAILURES["hybench_xp_io"] = old_error


if __name__ == "__main__":
    raise SystemExit(main())
