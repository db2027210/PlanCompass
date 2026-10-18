#!/usr/bin/env python3
"""Resume Algorithm C with v61 bounded thin-driver isolation."""

from __future__ import annotations
from pathlib import Path
import sys
from typing import Any
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import mysql_co_resident_pg_isolation_v9 as isolation_v9
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v17 as v17

OPTIMIZATION_CONTRACT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260817_v59/optimization_contract.json"
PREDECESSOR_CONTRACT = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260817_v60/coordination_contract.json"
ORIGINAL_STATE_DOCUMENT = v17.state_document

def validate_wave_coordination_contract() -> dict[str, Any]:
    runtime = v17.v16.v15.v13.v11.v10.v4.runtime
    contract = runtime.alg.load(isolation_v9.CONTRACT_PATH)
    predecessor = contract.get("predecessor") or {}
    allowance = (contract.get("co_resident_isolated_pg_allowance") or {}).get("thin_benchbase_tpch_driver_allowance") or {}
    if (
        contract.get("release_version") != "v61"
        or predecessor.get("path") != str(PREDECESSOR_CONTRACT.relative_to(ROOT))
        or predecessor.get("sha256") != runtime.alg.sha(PREDECESSOR_CONTRACT)
        or allowance.get("maximum_processes") != 10
        or allowance.get("maximum_rss_bytes_per_process") != 256 * 1024 * 1024
        or float(allowance.get("maximum_total_lifetime_cpu_cores") or 0) != 0.5
        or allowance.get("exact_command_signature_required") is not True
        or allowance.get("external_mutation") is not False
    ):
        raise RuntimeError("v61 thin-driver authority differs")
    return contract

def state_document(**kwargs: Any) -> dict[str, Any]:
    document = ORIGINAL_STATE_DOCUMENT(**kwargs)
    document["bounded_thin_driver_release"] = {
        "release_version": "v61/v59", "maximum_processes": 10,
        "maximum_total_lifetime_cpu_cores": 0.5,
        "maximum_rss_bytes_per_process": 256 * 1024 * 1024,
        "external_mutation": False,
        "workload_resource_top5_scoring_acceptance_and_convergence_changed": False,
    }
    return document

def main() -> int:
    v17.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    v17.state_document = state_document
    v17.isolation_v8 = isolation_v9
    v17.validate_wave_coordination_contract = validate_wave_coordination_contract
    return v17.main()

if __name__ == "__main__":
    raise SystemExit(main())
