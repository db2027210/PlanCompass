#!/usr/bin/env python3
"""Resume TPC-H with current PG-C and external-process inventory-only policy."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_c_four_scene_client_primary_v135 as predecessor
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v60 as runtime_v60


CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_tpch_external_inventory_only_20260929_v245/"
    "tpch_external_inventory_only_contract.json"
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
        value.get("release_version") != "v245-tpch-external-inventory-only"
        or value.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("external_processes_inventory_only") is not True
        or value.get("external_processes_can_block") is not False
        or value.get("external_quiet_window_required") is not False
        or value.get("strict_external_noninterference_evidence_eligible") is not False
        or value.get("target_mysql_local_resource_proof_required") is not True
        or value.get("accepted_state_credit_prohibited") is not False
        or value.get("current_pg_runtime_release") != "v60"
        or value.get("workload_or_candidate_policy_changed") is not False
        or value.get("resource_envelope_changed") is not False
    ):
        raise RuntimeError("Algorithm C v245 external-process policy contract differs")
    for name, raw in dict(value.get("source_bindings") or {}).items():
        path = Path(str(raw.get("path") or ""))
        if not path.is_absolute():
            path = ROOT / path
        if path.is_symlink() or not path.is_file() or sha(path) != raw.get("sha256"):
            raise RuntimeError(f"Algorithm C v245 source differs: {name}: {path}")
    return value


BASE_STATE_DOCUMENT = predecessor.actual_width_state_document


def state_document(**kwargs: Any) -> dict[str, Any]:
    document = BASE_STATE_DOCUMENT(**kwargs)
    document.update({
        "execution_class": "MYSQL_ONLY_EXTERNAL_PROCESS_INVENTORY_ONLY",
        "formal_evidence_eligible": False,
        "strict_external_noninterference_evidence_eligible": False,
        "external_processes_inventory_only": True,
        "external_processes_can_block": False,
        "external_quiet_window_required": False,
        "target_mysql_local_resource_proof_required": True,
        "accepted_state_credit_prohibited": False,
        "user_directed_external_process_policy_override": True,
    })
    document["external_process_policy_release"] = {
        "release_version": "v245",
        "source_path": str(Path(__file__).resolve()),
        "source_sha256": sha(Path(__file__).resolve()),
        "strict_external_noninterference_results_must_not_be_merged": True,
        "external_process_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
    }
    return document


def main() -> int:
    validate_contract()
    if "--fresh-search" in sys.argv[1:]:
        raise RuntimeError("v245 recovery must preserve completed fresh-search state")
    old_validate = predecessor.validate_contract
    old_runtime = predecessor.runtime_v59
    old_state = predecessor.actual_width_state_document
    predecessor.validate_contract = validate_contract
    predecessor.runtime_v59 = runtime_v60
    predecessor.actual_width_state_document = state_document
    try:
        return int(predecessor.main())
    finally:
        predecessor.actual_width_state_document = old_state
        predecessor.runtime_v59 = old_runtime
        predecessor.validate_contract = old_validate


if __name__ == "__main__":
    raise SystemExit(main())
