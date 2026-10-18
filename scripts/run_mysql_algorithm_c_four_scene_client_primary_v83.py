#!/usr/bin/env python3
"""Start the v172 actual-width resume through legacy compatibility layers."""

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
from scripts import build_mysql_algorithm_c_scene_resume_checkpoint_v4 as scene_checkpoint_v4
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v82 as predecessor
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v27 as boundary_v27
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v28 as boundary_v28


CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_full_resume_20260925_v173/"
    "algorithm_c_full_resume_contract.json"
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
    compatibility = dict(value.get("startup_compatibility") or {})
    if (
        value.get("schema_version") != "mysql_algorithm_c_full_resume_contract_v173"
        or value.get("release_version") != "v173-actual-width-startup-compatibility"
        or value.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("candidate_set_or_workload_changed") is not False
        or value.get("resource_envelope_changed") is not False
        or value.get("completed_measurements_replayed") is not False
        or compatibility.get("legacy_confirmation_type_required") is not False
        or compatibility.get("actual_width_checkpoint_schema_dispatched") is not True
        or compatibility.get("legacy_standard_checkpoint_dispatch_preserved") is not True
    ):
        raise RuntimeError("Algorithm C v173 contract differs")
    for name, raw in dict(value.get("source_bindings") or {}).items():
        path = (ROOT / str(raw.get("path") or "")).resolve()
        if path.is_symlink() or not path.is_file() or sha(path) != raw.get("sha256"):
            raise RuntimeError(f"Algorithm C v173 source differs: {name}: {path}")
    return value


def actual_width_v27_state_document(**kwargs: Any) -> dict[str, Any]:
    """Keep v27's liveness record without reintroducing capped counters."""
    document = copy.deepcopy(boundary_v27.ORIGINAL_V22_STATE_DOCUMENT(**kwargs))
    document["post_drain_liveness_refresh_release"] = {
        "release_version": "v27-v173-compatibility",
        "source_path": str(Path(__file__).resolve()),
        "source_sha256": sha(Path(__file__).resolve()),
        "reset_uses_final_drain_liveness": True,
        "dead_mysqld_discard_only": True,
        "legacy_confirmation_counter_rehydration": False,
        "workload_scoring_top5_binary_shrink_and_convergence_changed": False,
    }
    return document


def main() -> int:
    predecessor.validate_contract()
    validate_contract()

    old_v28_base = boundary_v28.ORIGINAL_V27_STATE_DOCUMENT
    old_standard_verify = scene_checkpoint_v4._ORIGINAL_STANDARD_VERIFY

    def schema_aware_standard_verify(path: Path):
        value = actual_checkpoint.load(path)
        if value.get("schema_version") == actual_checkpoint.SCHEMA:
            return actual_checkpoint.verify_checkpoint(path)
        return old_standard_verify(path)

    boundary_v28.ORIGINAL_V27_STATE_DOCUMENT = actual_width_v27_state_document
    scene_checkpoint_v4._ORIGINAL_STANDARD_VERIFY = schema_aware_standard_verify
    try:
        return int(predecessor.main())
    finally:
        scene_checkpoint_v4._ORIGINAL_STANDARD_VERIFY = old_standard_verify
        boundary_v28.ORIGINAL_V27_STATE_DOCUMENT = old_v28_base


if __name__ == "__main__":
    raise SystemExit(main())
