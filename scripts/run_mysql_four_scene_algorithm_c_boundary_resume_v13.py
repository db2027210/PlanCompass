#!/usr/bin/env python3
"""Resume Algorithm C with the sealed v57 bench-pg-row disk3 profile."""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_co_resident_pg_isolation_v5 as isolation_v5
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v12 as v12


v11 = v12.v11
OPTIMIZATION_CONTRACT = (
    ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260817_v54"
    / "optimization_contract.json"
)
PREDECESSOR_CONTRACT = (
    ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260817_v56"
    / "coordination_contract.json"
)
ORIGINAL_VALIDATE = v11.validate_wave_coordination_contract
ORIGINAL_STATE_DOCUMENT = v12.state_document


def validate_wave_coordination_contract() -> dict[str, Any]:
    runtime = v11.v10.v4.runtime
    original_load = runtime.alg.load

    def compatibility_load(path: Path) -> dict[str, Any]:
        value = original_load(path)
        if Path(path).resolve() == isolation_v5.CONTRACT_PATH.resolve():
            value = copy.deepcopy(value)
            value["release_version"] = "v56"
        return value

    runtime.alg.load = compatibility_load
    try:
        ORIGINAL_VALIDATE()
    finally:
        runtime.alg.load = original_load
    contract = original_load(isolation_v5.CONTRACT_PATH)
    policy = contract.get("co_resident_isolated_pg_allowance") or {}
    profile = next(
        (row for row in policy.get("additional_container_profiles") or []
         if row.get("name") == "bench-pg-row"),
        None,
    )
    evidence = policy.get("additional_container_release_evidence") or {}
    predecessor = contract.get("predecessor") or {}
    if (
        contract.get("release_version") != "v57"
        or predecessor.get("path")
        != str(PREDECESSOR_CONTRACT.relative_to(ROOT))
        or predecessor.get("sha256") != runtime.alg.sha(PREDECESSOR_CONTRACT)
        or not isinstance(profile, dict)
        or profile.get("required_mount_sources")
        != ["/disk3/wxr-data/cmp/row"]
        or profile.get("allowed_mount_prefixes") != ["/disk3/wxr-data/cmp/"]
        or evidence.get("bench_pg_row_storage_mount") != "/disk3"
        or evidence.get("bench_pg_row_storage_source") != "/dev/sda1"
        or evidence.get("bench_pg_row_mysql_storage_overlap") is not False
        or evidence.get("external_mutation") is not False
        or evidence.get("read_only_profile_validation") is not True
    ):
        raise RuntimeError("v57 bench-pg-row disk3 authority differs")
    return contract


def state_document(**kwargs: Any) -> dict[str, Any]:
    document = ORIGINAL_STATE_DOCUMENT(**kwargs)
    document["co_resident_bench_pg_row_disk3_release"] = {
        "release_version": "v57",
        "coordination_contract_path": str(isolation_v5.CONTRACT_PATH),
        "coordination_contract_sha256": v11.v10.v4.runtime.alg.sha(
            isolation_v5.CONTRACT_PATH
        ),
        "isolation_source_path": str(Path(isolation_v5.__file__).resolve()),
        "isolation_source_sha256": v11.v10.v4.runtime.alg.sha(
            Path(isolation_v5.__file__).resolve()
        ),
        "bench_pg_row_storage_source": "/dev/sda1",
        "mysql_storage_source": "/dev/nvme2n1",
        "storage_device_overlap": False,
        "external_mutation": False,
        "workload_resource_top5_scoring_acceptance_and_convergence_changed": False,
    }
    return document


def main() -> int:
    isolation_v5.install()
    v11.isolation_v4 = isolation_v5
    v11.validate_wave_coordination_contract = validate_wave_coordination_contract
    v12.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    v12.state_document = state_document
    return v12.main()


if __name__ == "__main__":
    raise SystemExit(main())
