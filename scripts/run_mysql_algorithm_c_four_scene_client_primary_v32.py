#!/usr/bin/env python3
"""v122 Algorithm C with bounded slow-Docker-start recovery for XP resets."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_docker_control_resilience_v4 as docker_resilience
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v29 as v29
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v31 as predecessor


CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_paper_baseline_20260920_v122/"
    "algorithm_c_paper_baseline_contract.json"
)
PARENT = predecessor.CONTRACT


def validate_contract() -> dict[str, Any]:
    contract = predecessor.runtime.alg.load(CONTRACT)
    if (
        contract.get("release_version") != "v122-slow-docker-start-recovery"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("candidate_search_requires_paper_baseline_pass") is not True
        or contract.get("tolerance_percentage_points") != 1.0
        or contract.get("docker_start_response_timeout_seconds") != 600
        or contract.get("parent_contract", {}).get("sha256")
        != predecessor.runtime.alg.sha(PARENT)
    ):
        raise RuntimeError("Algorithm C v122 slow-Docker-start contract differs")
    for name, raw in dict(contract.get("source_bindings") or {}).items():
        path = ROOT / str(raw.get("path") or "")
        if path.is_symlink() or not path.is_file() or predecessor.runtime.alg.sha(path) != raw.get("sha256"):
            raise RuntimeError(f"Algorithm C v122 source differs: {name}: {path}")
    return contract


def main() -> int:
    validate_contract()
    old = v29.docker_resilience_v3
    v29.docker_resilience_v3 = docker_resilience
    try:
        return int(predecessor.main())
    finally:
        v29.docker_resilience_v3 = old


if __name__ == "__main__":
    raise SystemExit(main())
