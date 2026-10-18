#!/usr/bin/env python3
"""Resume TPC-H with both live normalization and bounded control SQL."""

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

from scripts import mysql_algorithm_c_measurement_client_primary_v4 as measurement
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v70 as normalization
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v79 as predecessor


CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_full_resume_20260923_v170/"
    "algorithm_c_full_resume_contract.json"
)
FAILED_STATE = ROOT / (
    "history/mysql_four_scene_20260809/"
    "c_v169_control_sql_resume_20260923_tpch/run_state.json"
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
        value.get("schema_version") != "mysql_algorithm_c_full_resume_contract_v170"
        or value.get("release_version")
        != "v170-tpch-normalization-control-sql-resume"
        or value.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("resume_completed_outer_attempt") != 6
        or value.get("failed_outer_credit") != 0
        or value.get("tpch_live_measurement_normalization_installed") is not True
        or value.get("control_sql_timeout_seconds") != 300
        or value.get("formal_measurement_timeout_changed") is not False
        or value.get("candidate_set_or_workload_changed") is not False
        or value.get("resource_envelope_changed") is not False
        or value.get("full_search_runs_until_runtime_terminal_condition") is not True
        or value.get("fixed_outer_round_stop") is not False
    ):
        raise RuntimeError("Algorithm C v170 resume contract differs")
    for name, raw in dict(value.get("source_bindings") or {}).items():
        path = ROOT / str(raw.get("path") or "")
        if path.is_symlink() or not path.is_file() or sha(path) != raw.get("sha256"):
            raise RuntimeError(f"Algorithm C v170 source differs: {name}: {path}")
    return value


def validate_failed_attempt() -> None:
    state = load(FAILED_STATE)
    if (
        state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or state.get("error")
        != "RuntimeError: TPC-H C fresh baseline normalization is absent"
        or int(state.get("outer_attempt") or 0) != 6
        or int(dict(state.get("accepted_rounds") or {}).get("tpch_sf4_memory") or 0)
        != 5
        or int(dict(state.get("rejected_outer_attempts") or {}).get("tpch_sf4_memory") or 0)
        != 1
    ):
        raise RuntimeError("Algorithm C v170 failed TPC-H attempt differs")


def main() -> int:
    validate_contract()
    validate_failed_attempt()
    normalized = normalization.normalization_contract()
    old = copy.deepcopy(measurement._NORMALIZATION)
    measurement._NORMALIZATION.clear()
    measurement._NORMALIZATION.update(normalized)
    try:
        return int(predecessor.main())
    finally:
        measurement._NORMALIZATION.clear()
        measurement._NORMALIZATION.update(old)


if __name__ == "__main__":
    raise SystemExit(main())
