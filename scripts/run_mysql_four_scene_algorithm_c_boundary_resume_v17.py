#!/usr/bin/env python3
"""Resume Algorithm C with v60 raw-cmdline clone recognition."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_co_resident_pg_isolation_v8 as isolation_v8
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v16 as v16

OPTIMIZATION_CONTRACT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260817_v58/optimization_contract.json"
PREDECESSOR_CONTRACT = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260817_v59/coordination_contract.json"
ORIGINAL_STATE_DOCUMENT = v16.state_document


def validate_wave_coordination_contract() -> dict[str, Any]:
    runtime = v16.v15.v13.v11.v10.v4.runtime
    contract = runtime.alg.load(isolation_v8.CONTRACT_PATH)
    predecessor = contract.get("predecessor") or {}
    policy = contract.get("co_resident_isolated_pg_allowance") or {}
    clone = policy.get("template_clone_control_client_allowance") or {}
    if (
        contract.get("release_version") != "v60"
        or predecessor.get("path") != str(PREDECESSOR_CONTRACT.relative_to(ROOT))
        or predecessor.get("sha256") != runtime.alg.sha(PREDECESSOR_CONTRACT)
        or clone.get("raw_cmdline_matching") is not True
        or clone.get("exact_sql_required") is not True
        or clone.get("actual_server_backend_remains_workload") is not True
        or clone.get("external_mutation") is not False
        or float(contract.get("per_wave_quiet_seconds") or 0) != 15.0
        or float(contract.get("maximum_quiet_wait_while_held_seconds") or 0) != 60.0
    ):
        raise RuntimeError("v60 raw-cmdline authority differs")
    return contract


def state_document(**kwargs: Any) -> dict[str, Any]:
    document = ORIGINAL_STATE_DOCUMENT(**kwargs)
    document["raw_cmdline_clone_control_release"] = {
        "release_version": "v60/v58",
        "coordination_contract_path": str(isolation_v8.CONTRACT_PATH),
        "coordination_contract_sha256": v16.v15.v13.v11.v10.v4.runtime.alg.sha(isolation_v8.CONTRACT_PATH),
        "external_mutation": False,
        "workload_resource_top5_scoring_acceptance_and_convergence_changed": False,
    }
    return document


def main() -> int:
    v16.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    v16.state_document = state_document
    v16.isolation_v7 = isolation_v8
    v16.validate_wave_coordination_contract = validate_wave_coordination_contract
    return v16.main()


if __name__ == "__main__":
    raise SystemExit(main())
