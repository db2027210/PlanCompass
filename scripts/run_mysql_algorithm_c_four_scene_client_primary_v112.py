#!/usr/bin/env python3
"""Resume TPC-H after quarantining a zero-credit all-timeout outer."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_c_scene_resume_checkpoint_v14 as checkpoint_v14
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v109 as release_v109


CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_tpch_timeout_recovery_20260929_v216/"
    "tpch_timeout_recovery_contract.json"
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
        value.get("release_version") != "v216-tpch-timeout-quarantine-recovery"
        or value.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("normal_stop_uses_actual_interval_width") is not True
        or value.get("accepted_round_target") is not None
        or value.get("legacy_post_target_cap_retired") is not True
        or value.get("post_search_stability_confirmation") is not False
        or value.get("technical_timeout_state_credit") != 0
        or value.get("technical_timeout_rejected_round_increment") != 0
        or value.get("current_pg_runtime_release") != "v58"
        or value.get("dynamic_gate_release") != "v210"
        or value.get("workload_or_candidate_policy_changed") is not False
        or value.get("resource_envelope_changed") is not False
    ):
        raise RuntimeError("Algorithm C v216 TPC-H recovery contract differs")
    for name, raw in dict(value.get("source_bindings") or {}).items():
        path = Path(str(raw.get("path") or ""))
        if not path.is_absolute():
            path = ROOT / path
        if path.is_symlink() or not path.is_file() or sha(path) != raw.get("sha256"):
            raise RuntimeError(f"Algorithm C v216 source differs: {name}: {path}")
    return value


BASE_ACTUAL_WIDTH_STATE_DOCUMENT = release_v109.actual_width_state_document


def actual_width_state_document(**kwargs: Any) -> dict[str, Any]:
    document = BASE_ACTUAL_WIDTH_STATE_DOCUMENT(**kwargs)
    document["actual_width_credit_release"] = {
        **dict(document["actual_width_credit_release"]),
        "release_version": "v216",
        "source_path": str(Path(__file__).resolve()),
        "source_sha256": sha(Path(__file__).resolve()),
        "technical_timeout_candidates_are_zero_credit_exclusions": True,
    }
    return document


def main() -> int:
    validate_contract()
    if "--fresh-search" in sys.argv[1:]:
        raise RuntimeError("v216 recovery must preserve completed fresh-search state")
    old_validate = release_v109.validate_contract
    old_checkpoint = release_v109.checkpoint_v12
    old_state_document = release_v109.actual_width_state_document
    release_v109.validate_contract = validate_contract
    release_v109.checkpoint_v12 = checkpoint_v14
    release_v109.actual_width_state_document = actual_width_state_document
    try:
        return int(release_v109.main())
    finally:
        release_v109.actual_width_state_document = old_state_document
        release_v109.checkpoint_v12 = old_checkpoint
        release_v109.validate_contract = old_validate


if __name__ == "__main__":
    raise SystemExit(main())
