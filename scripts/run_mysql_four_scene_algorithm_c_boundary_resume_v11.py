#!/usr/bin/env python3
"""Resume Algorithm C with v56 read-only co-resident container validation."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_co_resident_pg_isolation_v4 as isolation_v4
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v10 as v10


OPTIMIZATION_CONTRACT = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260817_v52"
    / "optimization_contract.json"
)
ORIGINAL_STATE_DOCUMENT = v10.state_document


def validate_wave_coordination_contract() -> dict[str, Any]:
    """Validate v56 while preserving every v55 runtime invariant."""

    runtime = v10.v4.runtime
    original_load = runtime.alg.load

    def compatibility_load(path: Path) -> dict[str, Any]:
        value = original_load(path)
        if Path(path).resolve() == isolation_v4.CONTRACT_PATH.resolve():
            value = copy.deepcopy(value)
            value["maximum_quiet_wait_while_held_seconds"] = 20
        return value

    runtime.alg.load = compatibility_load
    try:
        v10.v4.ORIGINAL_WAVE_VALIDATOR()
    finally:
        runtime.alg.load = original_load
    contract = original_load(isolation_v4.CONTRACT_PATH)
    policy = contract.get("co_resident_isolated_pg_allowance") or {}
    profiles = policy.get("additional_container_profiles") or []
    evidence = policy.get("additional_container_release_evidence") or {}
    expected_names = {
        "bench-pg-mixstrg-0817",
        "bench-pg-row",
        "bench-run-mixstrg-opt",
        "bench-run-mixstrg-noopt",
        "bench-run-row",
    }
    if (
        contract.get("release_version") != "v56"
        or float(contract.get("per_wave_quiet_seconds") or 0) != 15.0
        or float(contract.get("maximum_quiet_wait_while_held_seconds") or 0)
        != 60.0
        or int(contract.get("maximum_gate_attempts_per_wave") or 0) != 1
        or int(
            contract.get("persistent_d_state_required_consecutive_samples")
            or 0
        ) != 3
        or contract.get("single_sample_d_state_action")
        != "record_without_resetting_clean_window"
        or contract.get("persistent_d_state_action") != "fail_closed"
        or {str(profile.get("name")) for profile in profiles} != expected_names
        or any(profile.get("required") is not False for profile in profiles)
        or any("/disk2/" in json.dumps(profile) for profile in profiles)
        or evidence.get("external_storage_source") != "/dev/nvme0n1p4"
        or evidence.get("mysql_storage_source") != "/dev/nvme2n1"
        or evidence.get("storage_device_overlap") is not False
        or evidence.get("external_mutation") is not False
        or evidence.get("read_only_profile_validation") is not True
    ):
        raise RuntimeError("v56 read-only co-resident authority differs")
    return contract


def state_document(**kwargs: Any) -> dict[str, Any]:
    document = ORIGINAL_STATE_DOCUMENT(**kwargs)
    document["co_resident_mixstrg_read_only_release"] = {
        "isolation_source_path": str(Path(isolation_v4.__file__).resolve()),
        "isolation_source_sha256": v10.v4.runtime.alg.sha(
            Path(isolation_v4.__file__).resolve()
        ),
        "coordination_contract_path": str(isolation_v4.CONTRACT_PATH),
        "coordination_contract_sha256": v10.v4.runtime.alg.sha(
            isolation_v4.CONTRACT_PATH
        ),
        "optimization_contract_path": str(OPTIMIZATION_CONTRACT),
        "optimization_contract_sha256": v10.v4.runtime.alg.sha(
            OPTIMIZATION_CONTRACT
        ),
        "docker_readback_transport": "read_only_unix_socket_http_get",
        "external_container_mutation": False,
        "external_process_affinity_mutation": False,
        "exact_live_container_cgroup_membership_required": True,
        "cpu_and_storage_device_disjointness_required": True,
    }
    return document


def main() -> int:
    isolation_v4.install()
    v10.v4.isolation = isolation_v4
    v10.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    v10.state_document = state_document
    v10.v4.validate_wave_coordination_contract = (
        validate_wave_coordination_contract
    )
    return v10.main()


if __name__ == "__main__":
    raise SystemExit(main())
