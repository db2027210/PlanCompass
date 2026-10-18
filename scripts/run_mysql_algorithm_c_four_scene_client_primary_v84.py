#!/usr/bin/env python3
"""Bind actual-width state construction at the effective v32 resume chain."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_c_async_scene_v49 as async_v49
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v82 as actual_width
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v83 as predecessor
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v32 as boundary_v32


CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_full_resume_20260925_v174/"
    "algorithm_c_full_resume_contract.json"
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_contract() -> dict[str, Any]:
    value = json.loads(CONTRACT.read_text(encoding="utf-8"))
    binding = dict(value.get("effective_state_binding") or {})
    if (
        value.get("schema_version") != "mysql_algorithm_c_full_resume_contract_v174"
        or value.get("release_version") != "v174-effective-state-chain-binding"
        or value.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("candidate_set_or_workload_changed") is not False
        or value.get("resource_envelope_changed") is not False
        or value.get("completed_measurements_replayed") is not False
        or binding.get("resume_base") != "async_v49.ORIGINAL_V27_STATE_DOCUMENT"
        or binding.get("effective_static_entry") != "boundary_v32.ORIGINAL_V29_STATE_DOCUMENT"
        or binding.get("v32_and_v34_release_records_preserved") is not True
    ):
        raise RuntimeError("Algorithm C v174 contract differs")
    for name, raw in dict(value.get("source_bindings") or {}).items():
        path = (ROOT / str(raw.get("path") or "")).resolve()
        if path.is_symlink() or not path.is_file() or sha(path) != raw.get("sha256"):
            raise RuntimeError(f"Algorithm C v174 source differs: {name}: {path}")
    return value


def main() -> int:
    predecessor.validate_contract()
    validate_contract()
    old_async_base = async_v49.ORIGINAL_V27_STATE_DOCUMENT
    old_v32_base = boundary_v32.ORIGINAL_V29_STATE_DOCUMENT
    async_v49.ORIGINAL_V27_STATE_DOCUMENT = (
        predecessor.actual_width_v27_state_document
    )
    boundary_v32.ORIGINAL_V29_STATE_DOCUMENT = (
        actual_width.actual_width_state_document
    )
    try:
        return int(predecessor.main())
    finally:
        boundary_v32.ORIGINAL_V29_STATE_DOCUMENT = old_v32_base
        async_v49.ORIGINAL_V27_STATE_DOCUMENT = old_async_base


if __name__ == "__main__":
    raise SystemExit(main())
