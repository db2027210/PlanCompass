#!/usr/bin/env python3
"""Run Algorithm C with same-lane incumbent revalidation v78."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_c_async_scene_v77 as predecessor
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v3 as paired_runtime


CONTRACT_ROOT = (
    ROOT / "config/formal/frozen_mysql_four_scene_algorithm_c_cpu_a_slots_20260828_v30"
)
PARENT_CONTRACT = CONTRACT_ROOT / "algorithm_c_async_contract.json"
OPTIMIZATION_CONTRACT = CONTRACT_ROOT / "outer_runtime_optimization.json"
CONTRACT = CONTRACT_ROOT / "global_monotonic_incumbent_contract.json"


def mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} object required")
    return dict(value)


def validate_contract() -> dict[str, Any]:
    alg = predecessor.predecessor.predecessor.predecessor.alg
    parent = alg.load(PARENT_CONTRACT)
    optimization = alg.load(OPTIMIZATION_CONTRACT)
    contract = alg.load(CONTRACT)
    paired = mapping(
        contract.get("lane_paired_incumbent_revalidation"),
        "lane-paired incumbent policy",
    )
    if (
        parent.get("release_version") != "v73"
        or parent.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or parent.get("formal_scope") != "mysql_only"
        or parent.get("cross_engine_comparison_prohibited") is not True
        or parent.get("four_scene_wave_barrier") is not False
        or parent.get("sibling_scene_wait") is not False
        or parent.get("lease_mode") != "none"
        or optimization.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("schema_version")
        != "mysql_algorithm_c_global_monotonic_incumbent_contract_v1"
        or contract.get("release_version") != "v78"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("parent_contract", {}).get("sha256")
        != alg.sha(PARENT_CONTRACT)
        or paired.get("schema_version")
        != paired_runtime.LANE_PAIRED_REVALIDATION_SCHEMA
        or paired.get("same_physical_worker_ratio") is not True
        or paired.get("trigger_on_apparent_acceptance") is not True
        or paired.get("trigger_before_second_rejection_shrink") is not True
        or paired.get("raw_qps_preserved") is not True
        or paired.get("control_measurements_grant_no_state_credit") is not True
        or paired.get("workload_or_resource_freeze_changed") is not False
    ):
        raise RuntimeError("Algorithm C v78 lane-paired contract differs")
    for document, label in ((parent, "parent"), (contract, "successor")):
        for name, raw in mapping(
            document.get("source_bindings"), f"{label} bindings"
        ).items():
            row = mapping(raw, f"{label} {name}")
            path = (ROOT / str(row.get("path") or "")).resolve()
            if (
                not path.is_file()
                or path.is_symlink()
                or alg.sha(path) != row.get("sha256")
            ):
                raise RuntimeError(f"Algorithm C v78 source binding differs: {name}")
    return {"parent": parent, "optimization": optimization, "successor": contract}


def main() -> int:
    validate_contract()
    v76 = predecessor.predecessor
    v73 = v76.predecessor.predecessor
    old = {
        "contract_root": predecessor.CONTRACT_ROOT,
        "parent": predecessor.PARENT_CONTRACT,
        "optimization": predecessor.OPTIMIZATION_CONTRACT,
        "contract": predecessor.CONTRACT,
        "validate": predecessor.validate_contract,
        "runtime": v73.runtime,
    }
    predecessor.CONTRACT_ROOT = CONTRACT_ROOT
    predecessor.PARENT_CONTRACT = PARENT_CONTRACT
    predecessor.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    predecessor.CONTRACT = CONTRACT
    predecessor.validate_contract = validate_contract
    v73.runtime = paired_runtime
    try:
        return int(predecessor.main())
    finally:
        predecessor.CONTRACT_ROOT = old["contract_root"]
        predecessor.PARENT_CONTRACT = old["parent"]
        predecessor.OPTIMIZATION_CONTRACT = old["optimization"]
        predecessor.CONTRACT = old["contract"]
        predecessor.validate_contract = old["validate"]
        v73.runtime = old["runtime"]


if __name__ == "__main__":
    raise SystemExit(main())
