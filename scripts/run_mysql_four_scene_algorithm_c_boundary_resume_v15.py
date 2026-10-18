#!/usr/bin/env python3
"""Resume Algorithm C with v58 transient-launcher isolation."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_co_resident_pg_isolation_v6 as isolation_v6
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v14 as v14


v13 = v14.v13
OPTIMIZATION_CONTRACT = (
    ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260817_v56"
    / "optimization_contract.json"
)
PREDECESSOR_CONTRACT = (
    ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260817_v57"
    / "coordination_contract.json"
)
ORIGINAL_STATE_DOCUMENT = v14.state_document


def validate_wave_coordination_contract() -> dict[str, Any]:
    runtime = v13.v11.v10.v4.runtime
    contract = runtime.alg.load(isolation_v6.CONTRACT_PATH)
    predecessor = contract.get("predecessor") or {}
    policy = contract.get("co_resident_isolated_pg_allowance") or {}
    transient = policy.get("transient_launcher_wrapper_allowance") or {}
    profile = next(
        (row for row in policy.get("additional_container_profiles") or []
         if row.get("name") == "bench-pg-row"),
        None,
    )
    evidence = policy.get("additional_container_release_evidence") or {}
    if (
        contract.get("release_version") != "v58"
        or predecessor.get("path") != str(PREDECESSOR_CONTRACT.relative_to(ROOT))
        or predecessor.get("sha256") != runtime.alg.sha(PREDECESSOR_CONTRACT)
        or float(contract.get("per_wave_quiet_seconds") or 0) != 15.0
        or float(contract.get("maximum_quiet_wait_while_held_seconds") or 0) != 60.0
        or int(contract.get("maximum_gate_attempts_per_wave") or 0) != 1
        or int(contract.get("persistent_d_state_required_consecutive_samples") or 0) != 3
        or contract.get("persistent_d_state_action") != "fail_closed"
        or not isinstance(profile, dict)
        or profile.get("required_mount_sources") != ["/disk3/wxr-data/cmp/row"]
        or profile.get("allowed_mount_prefixes") != ["/disk3/wxr-data/cmp/"]
        or evidence.get("bench_pg_row_storage_source") != "/dev/sda1"
        or evidence.get("bench_pg_row_mysql_storage_overlap") is not False
        or evidence.get("external_mutation") is not False
        or transient.get("maximum_rss_bytes") != 8 * 1024 * 1024
        or transient.get("full_host_cpuset_required") is not True
        or transient.get("direct_child_cpu_disjointness_required") is not True
        or transient.get("disk1_lineage_required") is not True
        or transient.get("external_mutation") is not False
    ):
        raise RuntimeError("v58 transient-launcher authority differs")
    return contract


def state_document(**kwargs: Any) -> dict[str, Any]:
    document = ORIGINAL_STATE_DOCUMENT(**kwargs)
    document["transient_launcher_isolation_release"] = {
        "release_version": "v58/v56",
        "coordination_contract_path": str(isolation_v6.CONTRACT_PATH),
        "coordination_contract_sha256": v13.v11.v10.v4.runtime.alg.sha(
            isolation_v6.CONTRACT_PATH
        ),
        "maximum_launcher_rss_bytes": 8 * 1024 * 1024,
        "direct_child_cpu_disjointness_required": True,
        "external_mutation": False,
        "workload_resource_top5_scoring_acceptance_and_convergence_changed": False,
    }
    return document


def main() -> int:
    v14.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    v14.state_document = state_document
    v13.isolation_v5 = isolation_v6
    v13.validate_wave_coordination_contract = validate_wave_coordination_contract
    return v14.main()


if __name__ == "__main__":
    raise SystemExit(main())
