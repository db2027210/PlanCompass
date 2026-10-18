#!/usr/bin/env python3
"""Run the v162 JOB resume with a script-safe repository import path."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_c_four_scene_client_primary_v72 as predecessor


CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_full_resume_20260923_v163/"
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
    if (
        value.get("schema_version")
        != "mysql_algorithm_c_full_resume_contract_v163"
        or value.get("release_version")
        != "v163-job-script-import-path-resume"
        or value.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("resume_completed_outer_attempt") != 2
        or value.get("failed_v162_outer_credit") != 0
        or value.get("script_safe_repository_import_path") is not True
        or value.get("full_search_runs_until_runtime_terminal_condition")
        is not True
        or value.get("fixed_outer_round_stop") is not False
        or value.get("parent_contract", {}).get("sha256")
        != sha(predecessor.CONTRACT)
    ):
        raise RuntimeError("Algorithm C v163 resume contract differs")
    for name, raw in dict(value.get("source_bindings") or {}).items():
        path = ROOT / str(raw.get("path") or "")
        if path.is_symlink() or not path.is_file() or sha(path) != raw.get("sha256"):
            raise RuntimeError(f"Algorithm C v163 source differs: {name}: {path}")
    return value


def main() -> int:
    predecessor.validate_contract()
    validate_contract()
    return int(predecessor.main())


if __name__ == "__main__":
    raise SystemExit(main())
