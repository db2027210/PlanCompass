#!/usr/bin/env python3
"""Install the SHA-bound TPC-H normalization into the live measurement module."""

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
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v69 as predecessor


CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_full_resume_20260923_v160/"
    "algorithm_c_full_resume_contract.json"
)
SOURCE_STATE = predecessor.SOURCE_STATE


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
        value.get("schema_version") != "mysql_algorithm_c_full_resume_contract_v160"
        or value.get("release_version") != "v160-live-tpch-normalization-resume"
        or value.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("tpch_live_measurement_normalization_installed") is not True
        or value.get("failed_v159_tpch_outer_credit") != 0
        or value.get("full_search_runs_until_runtime_terminal_condition") is not True
        or value.get("parent_contract", {}).get("sha256") != sha(predecessor.CONTRACT)
    ):
        raise RuntimeError("Algorithm C v160 resume contract differs")
    for name, raw in dict(value.get("source_bindings") or {}).items():
        path = ROOT / str(raw.get("path") or "")
        if path.is_symlink() or not path.is_file() or sha(path) != raw.get("sha256"):
            raise RuntimeError(f"Algorithm C v160 source differs: {name}: {path}")
    return value


def normalization_contract() -> dict[str, Any]:
    source = load(SOURCE_STATE)
    value = copy.deepcopy(dict(source.get("same_worker_qps_normalization") or {}))
    if (
        value.get("schema_version") != "mysql_tpch_same_worker_client_qps_normalization_v1"
        or value.get("scene") != "tpch_sf4_memory"
        or set(value.get("worker_to_canonical_factors") or {})
        != {"tpch_w1", "tpch_w2"}
        or any(
            float(number) <= 0
            for number in (value.get("worker_to_canonical_factors") or {}).values()
        )
    ):
        raise RuntimeError("live TPC-H normalization contract differs")
    return value


def main() -> int:
    predecessor.validate_contract()
    validate_contract()
    contract = normalization_contract()
    old = copy.deepcopy(measurement._NORMALIZATION)
    measurement._NORMALIZATION.clear()
    measurement._NORMALIZATION.update(contract)
    try:
        return int(predecessor.main())
    finally:
        measurement._NORMALIZATION.clear()
        measurement._NORMALIZATION.update(old)


if __name__ == "__main__":
    raise SystemExit(main())
